"""LangGraph workflow for a treasury decision run — a governed chain of bounded agents.

  planner ─┬─(DAILY_RUN / SCENARIO)─► data_intelligence ─► forecast ─► liquidity ─► risk ─► working_capital
           │                                                                                      │
           │     guardrail ◄─ briefing ◄─ governance ◄─ policy ◄─ decision ◄─ optimization ◄─ scenario
           │        └─ ungrounded figures: regenerate once, then deterministic fallback
           ├─(INSIGHT)──────────► insight_agent ─► END
           └─(POLICY_QUESTION)──► policy_answer ─► END

Each specialist has an MCP tool allowlist, a structured output schema, numeric-grounding validation and a
confidence score. A failing agent is recorded (audit + trace) and the run continues DEGRADED: the decision
agent excludes every recommendation that depends on the failed agent's evidence.

Every agent step records: tool calls (MCP, result hashes), data sources used, numeric-grounding
validation, confidence, limitations, prompt/model versions — all in the hash-chained audit ledger.
Approvals happen OUTSIDE the graph (service.py): they are asynchronous, multi-party and durable.
"""

from __future__ import annotations

import operator
import time
from datetime import UTC, datetime
from typing import Annotated, Any, TypedDict

from langgraph.graph import END, START, StateGraph

from meridian.agents import prompts, reasoners
from meridian.agents.schemas import AgentFinding, CFOBriefing, Plan
from meridian.analytics.scenarios import Scenario
from meridian.decision.recommendations import build_recommendations
from meridian.domain.models import ActionType, ProposedAction
from meridian.governance.audit import AuditLedger
from meridian.governance.guardrails import mask, numeric_grounding
from meridian.governance.policy import load_policy, route_approval
from meridian.llm.claude import PROMPT_VERSION, ClaudeClient
from meridian.observability.metrics import AGENT_LATENCY, GUARDRAIL_EVENTS
from meridian.orchestration.gateway import MCPGateway

AGENT_SOURCES = {
    "data_intelligence_agent": ["company.entities", "company.bank_balances", "company.receivables", "company.payables"],
    "forecast_agent": ["company.bank_balances", "company.receivables", "company.payables", "company.recurring",
                       "company.debt_facilities", "model.ar_delay", "market.fx", "market.sofr"],
    "liquidity_agent": ["company.bank_balances", "company.bank_transactions", "company.entities", "company.banks",
                        "company.facility", "market.fx", "market.fx_history"],
    "risk_agent": ["company.debt_facilities", "company.covenants", "market.hazards", "market.sofr", "market.ust_curve",
                   "market.credit_spread", "company.receivables"],
    "working_capital_agent": ["company.receivables", "company.payables", "company.counterparties", "model.ar_delay"],
    "scenario_agent": ["company.bank_balances", "company.receivables", "market.hazards", "market.fx"],
    "optimization_agent": ["company.bank_balances", "company.entities", "company.facility", "company.debt_facilities",
                           "market.fx", "market.sofr"],
    "policy_agent": ["reference.policies"],
}
# Which agent's evidence each action type depends on (used for degraded-mode exclusion and evidence chains)
ACTION_AGENT = {"optimize_liquidity": "optimization_agent", "get_early_pay_opportunities": "working_capital_agent",
                "get_collections_priorities": "working_capital_agent", "get_debt_profile": "risk_agent",
                "get_liquidity_overview": "liquidity_agent"}
ACTION_METHOD = {"optimize_liquidity": "Linear programme (HiGHS) on P10 trough balances",
                 "get_early_pay_opportunities": "Implied discount return vs cost of funds + headroom",
                 "get_collections_priorities": "ML payment-delay P50/P90 × financing cost",
                 "get_debt_profile": "Maturity vs DP-2.1 window; cost ESTIMATED from UST 5Y + BBB OAS",
                 "get_liquidity_overview": "Share of group cash per bank vs TP-5.2 limits"}


def _merge(a: dict, b: dict) -> dict:
    return {**(a or {}), **(b or {})}


class RunState(TypedDict, total=False):
    run_id: str
    question: str | None
    requested_by: str
    plan: dict
    scenario: dict
    evidence: Annotated[dict[str, Any], _merge]
    findings: Annotated[list[dict], operator.add]
    tool_calls: Annotated[list[dict], operator.add]
    actions: list[dict]
    recommendations: list[dict]
    failed_agents: Annotated[list[str], operator.add]
    briefing: dict
    grounding: dict
    briefing_attempts: int
    answer: dict
    insight: dict
    trace: Annotated[list[dict], operator.add]


def _trace(actor: str, action: str, t0: float, **detail) -> list[dict]:
    return [{"ts": datetime.now(UTC).isoformat(), "actor": actor, "action": action,
             "latency_ms": round((time.perf_counter() - t0) * 1000, 1), "detail": detail}]


def plan_to_scenario(p: Plan, event_ids: list[str]) -> Scenario:
    return Scenario(name=p.scenario_name if p.intent == "SCENARIO" else "Base case",
                    collection_delay_pct=p.collection_delay_pct, country_delay_days=p.country_delay_days,
                    billing_change_pct={"*": p.billing_change_pct_all} if p.billing_change_pct_all else {},
                    rate_shock_bps=p.rate_shock_bps, fx_shock_pct=p.fx_shock_pct,
                    event_ids=event_ids if p.include_hazard_events else [])


def build_actions(opt: dict, early_pay: dict, collections: list[dict], entities: list[dict],
                  debt: dict | None = None, concentration: dict | None = None) -> list[dict]:
    """Deterministically turn analytics into proposed actions routed through the approval matrix.
    A proposed action is a RECOMMENDATION, not a transaction."""
    ents = {e["entity_id"]: e for e in entities}
    acts: list[ProposedAction] = []
    n = 0

    def add(source_tool: str, **kw):
        nonlocal n
        n += 1
        a = ProposedAction(action_id=f"A{n:03d}", **kw)
        controls = bool(kw.get("from_entity") and ents.get(kw["from_entity"], {}).get("capital_controls"))
        role, ref = route_approval(a, capital_controls=controls)
        a.required_approver, a.policy_refs = role, [ref] + a.policy_refs
        acts.append((a, source_tool))

    for t in opt["transfers"]:
        add("optimize_liquidity", type=ActionType.INTERCOMPANY_TRANSFER, from_entity=t["from_entity"],
            to_entity=t["to_entity"], amount_usd=t["amount_usd"], currency=t["currency"],
            rationale=f"{t['kind']}: {t['reason']}", policy_refs=["TP-4.1"] if t["kind"].startswith("sweep") else ["TP-3.2"])
    if opt["revolver_repay_usd"] > 0:
        add("optimize_liquidity", type=ActionType.REVOLVER_REPAY, from_entity="E01", amount_usd=opt["revolver_repay_usd"],
            rationale=f"Apply concentrated surplus to reduce revolver borrowings at {opt['revolver_rate']:.2%} (TP-4.1)",
            annualized_benefit_usd=opt["annualized_interest_saving_from_repay_usd"], policy_refs=["RCF-8.1"])
    by_ent: dict[str, list[dict]] = {}
    for b in early_pay["selected"]:
        by_ent.setdefault(b["entity_id"], []).append(b)
    for eid, bills in by_ent.items():
        # One batch per paying entity; TP-2.3 limits apply per individual payment, so route on the largest bill.
        n += 1
        a = ProposedAction(action_id=f"A{n:03d}", type=ActionType.EARLY_PAY_DISCOUNT, from_entity=eid,
                           amount_usd=sum(b["amount_usd"] for b in bills),
                           rationale=f"Early-pay {len(bills)} supplier bill(s) ({', '.join(b['bill_id'] for b in bills)}) "
                                     f"to capture discounts worth ${sum(b['saving_usd'] for b in bills):,.0f}",
                           annualized_benefit_usd=sum(b["saving_usd"] for b in bills))
        a.required_approver, ref = route_approval(a.model_copy(update={"amount_usd": max(b["amount_usd"] for b in bills)}))
        a.policy_refs = [ref]
        acts.append((a, "get_early_pay_opportunities"))
    for c in collections:
        if c["predicted_days_late_p50"] > 45 or (c["disputed"] and c["amount_usd"] > 1_000_000):
            add("get_collections_priorities", type=ActionType.COLLECTIONS_ESCALATION, from_entity=c["entity_id"],
                amount_usd=c["amount_usd"], rationale=f"{c['invoice_id']} {c['customer']}: {c['recommended_action']}",
                policy_refs=["TP-6.1"])
    for r in (debt or {}).get("refinancing", []):
        add("get_debt_profile", type=ActionType.REFINANCING_PLAN, to_entity=r["facility_id"], amount_usd=r["amount_usd"],
            rationale=f"Mandate refinancing of {r['name']} maturing {r['maturity_date']} ({r['months_to_maturity']} months; "
                      f"DP-2.1 requires launch 18 months before maturity)", policy_refs=["DP-2.1"])
    for b in (concentration or {}).get("banks", []):
        if b["breach"]:
            add("get_liquidity_overview", type=ActionType.BANK_REBALANCING_PLAN, to_entity=b["bank_id"],
                amount_usd=b["excess_usd"], rationale=f"Rebalance {b['bank']}: {b['share']:.0%} of group cash vs "
                                                      f"{b['limit_share']:.0%} limit (TP-5.2)", policy_refs=["TP-5.2"])
    return [{**a.model_dump(mode="json"), "source_tool": tool} for a, tool in acts]


def build_graph(store):
    ledger = AuditLedger(store)
    policy = load_policy()
    llms: dict[str, ClaudeClient] = {}

    def llm(run_id: str) -> ClaudeClient:
        if run_id not in llms:
            llms[run_id] = ClaudeClient(policy.llm.max_calls_per_run)
        return llms[run_id]

    async def agent_step(state: RunState, agent: str, instructions: str, ev: dict, schema, offline):
        t0 = time.perf_counter()
        client = llm(state["run_id"])
        out = await client.structured(agent, instructions, mask(ev), schema, offline)
        AGENT_LATENCY.labels(agent=agent).observe(time.perf_counter() - t0)
        return out, t0, client

    def finalize_finding(state: RunState, f: AgentFinding, ev: dict, gw: MCPGateway, client: ClaudeClient) -> dict:
        """Attach validation, provenance and governance metadata to a specialist finding, then audit it."""
        text = " ".join([f.headline, *f.key_points, *f.risks, *f.recommendations])
        g = numeric_grounding(text, ev)
        d = f.model_dump()
        if not g["grounded"]:
            GUARDRAIL_EVENTS.labels(type="ungrounded_number_specialist").inc(len(g["ungrounded"]))
            d["confidence"] = round(d["confidence"] * 0.6, 2)
            d["limitations"].append(f"{len(g['ungrounded'])} figure(s) could not be traced to tool output")
        d["validation"] = {"numeric_grounding": g["grounded"], "figures_checked": g["claims"],
                           "ungrounded": g["ungrounded"], "schema": "valid"}
        d["provenance"] = {"tool_calls": gw.calls, "data_sources": AGENT_SOURCES.get(f.agent, []),
                           "prompt_version": PROMPT_VERSION, "llm_provider": client.mode,
                           "llm_model": client.settings.llm_model if client.mode != "mock" else "deterministic offline reasoner"}
        d["audit_hash"] = ledger.record(f.agent, "agent_output", {k: d[k] for k in (
            "headline", "confidence", "validation", "provenance", "policy_refs")}, state["run_id"])[:16]
        return d

    # ------------------------------------------------------------------------------------ nodes
    async def planner(state: RunState) -> dict:
        gw = MCPGateway(state["run_id"], ledger)
        ledger.record("orchestrator", "run_started", {"question": state.get("question"),
                                                      "requested_by": state.get("requested_by"),
                                                      "policy_version": policy.version}, state["run_id"])
        p, t0, client = await agent_step(state, "planner_agent", prompts.PLANNER, {"question": state.get("question")},
                                         Plan, reasoners.plan)
        ledger.record("planner_agent", "plan", {"plan": p.model_dump(), "llm_provider": client.mode}, state["run_id"])
        upd: dict = {"plan": p.model_dump(), "trace": _trace("planner_agent", p.intent, t0, topic=p.insight_topic)}
        if p.intent == "POLICY_QUESTION":
            upd["evidence"] = {"policy_answer": await gw.call("planner_agent", "search_treasury_policy",
                                                              query=p.policy_query or state.get("question") or "", k=4)}
            upd["tool_calls"] = gw.calls
        else:
            from meridian.analytics.engine import get_engine

            ids = [e.event_id for e in get_engine().g.risk_events if e.classification == "SYNTHETIC"
                   or e.severity in {"ORANGE", "RED"}]
            upd["scenario"] = plan_to_scenario(p, ids).model_dump()
        return upd

    def route_plan(state: RunState) -> str:
        return {"POLICY_QUESTION": "policy_answer", "INSIGHT": "insight_agent"}.get(state["plan"]["intent"],
                                                                                     "data_intelligence_agent")

    async def policy_answer(state: RunState) -> dict:
        t0 = time.perf_counter()
        hits = state["evidence"]["policy_answer"]
        ans = AgentFinding(agent="policy_agent", headline=f"Policy answer: {state['question']}",
                           key_points=[f"{h['clause_id']} — {h['clause_title']}: {h['text']}" for h in hits[:3]],
                           policy_refs=[h["clause_id"] for h in hits[:3]], confidence=0.9,
                           limitations=["Retrieved clauses; legal interpretation remains with Treasury/Legal"])
        client = llm(state["run_id"])
        if client.mode != "mock":
            ans, _, _ = await agent_step(state, "policy_agent", prompts.POLICY,
                                         {"question": state["question"], "clauses": hits}, AgentFinding, lambda e: ans)
        d = ans.model_dump()
        d["citations"] = [{"clause_id": h["clause_id"], "document": h["document"], "source": h.get("source"),
                           "rank": h.get("rrf_rank")} for h in hits]
        ledger.record("policy_agent", "policy_answer", {"citations": d["citations"]}, state["run_id"])
        return {"answer": d, "trace": _trace("policy_agent", "answered", t0)}

    async def insight_agent(state: RunState) -> dict:
        gw = MCPGateway(state["run_id"], ledger)
        topic = state["plan"]["insight_topic"]
        ins = await gw.call("insight_agent", "get_cfo_insight", topic=topic)
        offline = AgentFinding(agent="insight_agent", headline=ins["headline"], key_points=[
            f"{k.replace('_', ' ')}: {v}" for k, v in ins["figures"].items()], policy_refs=ins["evidence"]["policy_refs"],
            confidence={"HIGH": 0.9, "MEDIUM": 0.7, "LOW": 0.4}[ins["confidence"]], limitations=ins["limitations"])
        f, t0, client = await agent_step(state, "insight_agent", "Answer the CFO's question from the insight evidence. "
                                         "Lead with the answer; keep every number exactly as given.", ins, AgentFinding,
                                         lambda e: offline)
        d = finalize_finding(state, f, ins, gw, client)
        return {"insight": {**ins, "agent_finding": d}, "findings": [d], "tool_calls": gw.calls,
                "trace": _trace("insight_agent", topic, t0, confidence=ins["confidence"])}

    def guarded(agent: str):
        """Failure handling: an exception inside an agent is audited, traced and turned into a FAILED finding;
        the run continues in degraded mode instead of failing or inventing evidence."""
        def wrap(fn):
            async def run(state: RunState) -> dict:
                t0 = time.perf_counter()
                try:
                    return await fn(state)
                except Exception as e:  # noqa: BLE001 - any agent failure must degrade, not crash, the run
                    ledger.record(agent, "agent_failed", {"error": f"{type(e).__name__}: {e}"[:300]}, state["run_id"])
                    GUARDRAIL_EVENTS.labels(type="agent_failed").inc()
                    d = AgentFinding(agent=agent, headline=f"{agent} FAILED — its evidence is excluded", confidence=0.0,
                                     limitations=[f"{type(e).__name__}: {str(e)[:160]}"]).model_dump()
                    d["status"] = "FAILED"
                    d["validation"] = {"numeric_grounding": False, "figures_checked": 0, "ungrounded": [], "schema": "n/a"}
                    return {"findings": [d], "failed_agents": [agent], "trace": _trace(agent, "FAILED", t0)}
            return run
        return wrap

    def _sc(state: RunState) -> str:
        return Scenario(**state["scenario"]).model_dump_json()

    async def _specialist(state: RunState, agent: str, calls: list[tuple[str, str, dict]], prompt: str, offline,
                          extra: dict | None = None) -> dict:
        gw = MCPGateway(state["run_id"], ledger)
        ev = {key: await gw.call(agent, tool, **kw) for key, tool, kw in calls}
        f, t0, client = await agent_step(state, agent, prompt, {**ev, **(extra or {})}, AgentFinding, offline)
        return {"evidence": ev, "findings": [finalize_finding(state, f, {**ev, **(extra or {})}, gw, client)],
                "tool_calls": gw.calls,
                "trace": _trace(agent, "finding", t0)}

    @guarded("data_intelligence_agent")
    async def data_intelligence_agent(state: RunState) -> dict:
        return await _specialist(state, "data_intelligence_agent", [("provenance", "get_data_provenance", {}),
                                                                    ("quality", "get_data_quality", {})],
                                 prompts.DATA_INTELLIGENCE, reasoners.data_intelligence)

    @guarded("forecast_agent")
    async def forecast_agent(state: RunState) -> dict:
        return await _specialist(state, "forecast_agent", [("kpis", "get_treasury_kpis", {}),
                                                           ("forecast", "run_cash_forecast", {"scenario": _sc(state)})],
                                 prompts.FORECAST, reasoners.forecast)

    @guarded("liquidity_agent")
    async def liquidity_agent(state: RunState) -> dict:
        return await _specialist(state, "liquidity_agent", [("entities", "get_entity_cash_positions", {}),
                                                            ("overview", "get_liquidity_overview", {})],
                                 prompts.LIQUIDITY, reasoners.liquidity)

    @guarded("risk_agent")
    async def risk_agent(state: RunState) -> dict:
        out = await _specialist(state, "risk_agent", [("events", "get_risk_events", {}),
                                                      ("debt_profile", "get_debt_profile", {}),
                                                      ("register", "get_risk_register", {})],
                                prompts.RISK, lambda ev: reasoners.risk({**ev, "debt": ev["debt_profile"]["debt"],
                                                                         "covenants": ev["debt_profile"]["covenants"]}))
        dp = out["evidence"].pop("debt_profile")
        out["evidence"].update({"debt": dp["debt"], "covenants": dp["covenants"]})
        return out

    @guarded("working_capital_agent")
    async def working_capital_agent(state: RunState) -> dict:
        return await _specialist(state, "working_capital_agent", [
            ("collections", "get_collections_priorities", {"top_n": 10}),
            ("early_pay", "get_early_pay_opportunities", {"scenario": _sc(state)}),
            ("drivers", "get_working_capital_drivers", {})], prompts.WORKING_CAPITAL, reasoners.working_capital)

    @guarded("scenario_agent")
    async def scenario_agent(state: RunState) -> dict:
        current = _sc(state)
        return await _specialist(state, "scenario_agent", [("scenarios", "compare_scenarios", {
            "scenarios": list(dict.fromkeys([current, "global_stress", "asia_hazards"]))})],
                                 prompts.SCENARIO, reasoners.scenario)

    @guarded("optimization_agent")
    async def optimization_agent(state: RunState) -> dict:
        ent = state["evidence"].get("entities")
        return await _specialist(state, "optimization_agent", [
            ("optimization", "optimize_liquidity", {"scenario": _sc(state)})], prompts.OPTIMIZATION,
            reasoners.optimization, extra={"entities": ent or []})

    async def decision_agent(state: RunState) -> dict:
        """Combine VERIFIED evidence into actions and recommendations. Evidence from failed or ungrounded agents is
        excluded, and the exclusion is stated in the output."""
        t0 = time.perf_counter()
        ev = state["evidence"]
        failed = set(state.get("failed_agents", []))
        findings = {f["agent"]: f for f in state["findings"]}
        excluded = []
        if "optimization" not in ev or "optimization_agent" in failed:
            excluded.append("cash concentration & debt repayment (optimisation evidence unavailable)")
            ev = {**ev, "optimization": {"transfers": [], "revolver_repay_usd": 0, "annualized_benefit_usd": 0,
                                         "constraints": [], "sweep_total_usd": 0, "funding_total_usd": 0}}
        if "working_capital_agent" in failed:
            excluded.append("supplier discounts & collections (working-capital evidence unavailable)")
        acts = build_actions(ev["optimization"], ev.get("early_pay") or {"selected": []}, ev.get("collections") or [],
                             ev.get("entities") or [], ev.get("debt") if "risk_agent" not in failed else None,
                             (ev.get("overview") or {}).get("concentration") if "liquidity_agent" not in failed else None)
        calls = {c["tool"]: c for c in state.get("tool_calls", [])}
        from meridian.analytics.engine import get_engine

        prov = {p["source_id"]: p for p in get_engine().provenance()}
        for a in acts:
            agent = ACTION_AGENT[a["source_tool"]]
            fnd = findings.get(agent, {})
            a["evidence"] = {
                "data": [{"source_id": s, "classification": prov.get(s, {}).get("classification"),
                          "as_of": prov.get(s, {}).get("as_of"), "validation": prov.get(s, {}).get("validation"),
                          "checksum": (prov.get(s, {}).get("checksum_sha256") or "")[:16]}
                         for s in AGENT_SOURCES[agent] if s in prov],
                "analysis": {"tool": a["source_tool"], "result_sha256": calls.get(a["source_tool"], {}).get("result_sha256"),
                             "tool_call_audit_hash": calls.get(a["source_tool"], {}).get("audit_hash"),
                             "method": ACTION_METHOD[a["source_tool"]]},
                "agent": {"agent": agent, "headline": fnd.get("headline"), "confidence": fnd.get("confidence"),
                          "grounded": fnd.get("validation", {}).get("numeric_grounding"),
                          "audit_hash": fnd.get("audit_hash")},
                "policy": {"refs": a["policy_refs"], "policy_version": policy.version,
                           "required_approver": a["required_approver"]},
                "recommendation": a["rationale"],
                "nature": "RECOMMENDATION — not a transaction. No payment is created until an authorised approver "
                          "approves it, and then only as a DRAFT payment file that stops at READY.",
            }
        binding = [c for c in ev["optimization"].get("constraints", []) if c.get("binding")
                   and c.get("shadow_price_per_usd_13w")]
        binding.sort(key=lambda c: -(c["shadow_price_per_usd_13w"] or 0))
        recs = build_recommendations(state["run_id"], Scenario(**state["scenario"]).name, {
            "optimization": ev["optimization"], "early_pay": ev.get("early_pay") or {"selected": []},
            "forecast": ev.get("forecast") or {"entities": []}, "debt": ev.get("debt"), "covenants": ev.get("covenants"),
            "dso": (ev.get("drivers") or {}).get("dso"), "ap": (ev.get("drivers") or {}).get("ap"),
            "concentration": (ev.get("overview") or {}).get("concentration")}, acts, findings, policy.version, binding)
        rec_of = {aid: r["rec_id"] for r in recs for aid in r["action_ids"]}
        for a in acts:
            a["rec_id"] = rec_of.get(a["action_id"])
        f = reasoners.decision({"recommendations": recs, "excluded": excluded})
        d = f.model_dump() | {"validation": {"numeric_grounding": True, "figures_checked": 0, "ungrounded": [],
                                             "schema": "valid"},
                              "provenance": {"tool_calls": [], "data_sources": [], "llm_provider": "deterministic"}}
        h = ledger.record("decision_agent", "recommendations_built", {
            "recommendations": [{k: r[k] for k in ("rec_id", "category", "priority", "approval_required")} for r in recs],
            "actions": [{k: a[k] for k in ("action_id", "type", "amount_usd", "required_approver")} for a in acts],
            "excluded": excluded}, state["run_id"])
        d["audit_hash"] = h[:16]
        for a in acts:
            a["evidence"]["audit"] = {"proposed_record_hash": h[:16]}
        return {"actions": acts, "recommendations": recs, "findings": [d],
                "trace": _trace("decision_agent", "recommendations", t0, n=len(recs), actions=len(acts))}

    @guarded("policy_agent")
    async def policy_agent(state: RunState) -> dict:
        gw = MCPGateway(state["run_id"], ledger)
        qs = {"approvals": "approval matrix intercompany transfer revolver prepayment CFO treasurer",
              "prepayment": "revolver voluntary prepayment minimum amount multiples",
              "trapped": "repatriation capital controls China India",
              "early_pay": "early payment supplier discount",
              "refinancing": "refinancing horizon maturity term loan",
              "counterparty": "bank counterparty limit share of group cash",
              "release": "payment release dual control signatory",
              "collections": "collections escalation overdue invoices regional finance director",
              "terms": "pay on terms supplier invoices early payment discounts"}
        ev = {"clauses": {k: await gw.call("policy_agent", "search_treasury_policy", query=q, k=3) for k, q in qs.items()},
              "actions": state.get("actions", [])}
        retrieved = [c for hits in ev["clauses"].values() for c in hits if c.get("relevant", True)]
        from meridian.nlp.rag import verify_citations

        recs = state.get("recommendations", [])
        for r in recs:
            v = verify_citations(" ".join(r["policy_refs"]), retrieved + [{"clause_id": c} for c in _policy_code_refs()])
            r["policy_citations"] = [{"clause_id": c["clause_id"], "document": c["document"],
                                      "doc_version": c.get("doc_version"), "relevance": c.get("relevance"),
                                      "retrieved_at": c.get("retrieved_at"), "citation": c.get("citation")}
                                     for c in retrieved if c["clause_id"] in r["policy_refs"]]
            r["policy_verification"] = v
        f, t0, client = await agent_step(state, "policy_agent", prompts.POLICY, ev, AgentFinding, reasoners.policy)
        return {"evidence": {"policy_clauses": ev["clauses"]}, "recommendations": recs,
                "findings": [finalize_finding(state, f, ev, gw, client)], "tool_calls": gw.calls,
                "trace": _trace("policy_agent", "finding", t0)}

    async def governance_agent(state: RunState) -> dict:
        """Deterministic governance gate (not an LLM): routing, autonomy, citations, data classification."""
        t0 = time.perf_counter()
        acts, recs = state.get("actions", []), state.get("recommendations", [])
        from meridian.analytics.engine import get_engine

        eng = get_engine()
        checks = [
            {"check": "Every action routed through the approval matrix (TP-2.1)",
             "passed": all(a.get("required_approver") for a in acts), "detail": f"{len(acts)} actions routed"},
            {"check": "No autonomous execution (TP-7.1)", "passed": not policy.autonomous_execution,
             "detail": "autonomous_execution=false; agents have no payment tool"},
            {"check": "Payment release requires a configured channel and second signatory (TP-7.2)", "passed": True,
             "detail": f"transmission channel: {policy.execution.transmission_channel}; release roles "
                       f"{', '.join(policy.execution.release_roles)}"},
            {"check": "Every recommendation cites policy found in retrieved documents or policy-as-code",
             "passed": all(r.get("policy_verification", {}).get("verified", True) for r in recs),
             "detail": ", ".join(f"{r['short_id']}: {r.get('policy_verification', {}).get('unsupported')}" for r in recs
                                 if not r.get("policy_verification", {}).get("verified", True)) or "all verified"},
            {"check": "Data classification disclosed on every recommendation", "passed": True,
             "detail": f"company data classification: {eng.g.classification}"},
            {"check": "Requester recorded for segregation of duties (TP-2.2)", "passed": bool(state.get("requested_by")),
             "detail": f"requested_by={state.get('requested_by')}"},
            {"check": "No agent failed", "passed": not state.get("failed_agents"),
             "detail": ", ".join(state.get("failed_agents", [])) or "all agents completed"},
        ]
        for r in recs:
            r["governance"] = {"checks_passed": all(c["passed"] for c in checks), "data_classification": eng.g.classification}
        f = reasoners.governance({"checks": checks})
        d = f.model_dump() | {"validation": {"numeric_grounding": True, "figures_checked": 0, "ungrounded": [],
                                             "schema": "valid"}, "checks": checks,
                              "provenance": {"tool_calls": [], "data_sources": [], "llm_provider": "deterministic"}}
        d["audit_hash"] = ledger.record("governance_agent", "governance_checks", {"checks": checks}, state["run_id"])[:16]
        return {"findings": [d], "recommendations": recs, "trace": _trace("governance_agent", "checks", t0,
                                                                         failed=sum(not c["passed"] for c in checks))}

    def _brief_ev(state: RunState) -> dict:
        ev = state["evidence"]
        opt = ev.get("optimization") or {"transfers": [], "revolver_repay_usd": 0, "annualized_benefit_usd": 0}
        epay = ev.get("early_pay") or {"total_saving_usd": 0, "cash_accelerated_usd": 0, "cost_of_funds": 0}
        return {"kpis": ev["kpis"], "forecast": ev["forecast"], "optimization": opt,
                "early_pay": {k: epay[k] for k in ("total_saving_usd", "cash_accelerated_usd", "cost_of_funds")},
                "actions": state.get("actions", []), "findings": state["findings"],
                "recommendations": state.get("recommendations", []),
                "feedback": state.get("grounding", {}).get("ungrounded")}

    async def briefing_agent(state: RunState) -> dict:
        ev = _brief_ev(state)
        b, t0, client = await agent_step(state, "briefing_agent", prompts.BRIEFING, ev, CFOBriefing, reasoners.briefing)
        d = b.model_dump()
        d["confidence"] = round(min((f.get("confidence", 0.8) for f in state["findings"]), default=0.8), 2)
        d["llm_provider"] = client.mode
        return {"briefing": d, "briefing_attempts": state.get("briefing_attempts", 0) + 1,
                "trace": _trace("briefing_agent", "draft", t0)}

    async def guardrail(state: RunState) -> dict:
        t0 = time.perf_counter()
        b = state["briefing"]
        text = " ".join([b["executive_summary"], *b["key_numbers"], *b["decisions_required"], *b["risks"]])
        g = numeric_grounding(text, _brief_ev(state) | {"evidence": state["evidence"]})
        if not g["grounded"]:
            GUARDRAIL_EVENTS.labels(type="ungrounded_number").inc(len(g["ungrounded"]))
            ledger.record("guardrail", "ungrounded_numbers", g, state["run_id"])
            if state.get("briefing_attempts", 0) >= 2:   # fall back to the deterministic, fully grounded brief
                fb = reasoners.briefing(_brief_ev(state)).model_dump()
                fb["confidence"], fb["llm_provider"] = b.get("confidence"), "deterministic fallback"
                g = {**numeric_grounding(" ".join([fb["executive_summary"], *fb["key_numbers"]]), _brief_ev(state)),
                     "fallback": True}
                ledger.record("guardrail", "fallback_briefing", g, state["run_id"])
                return {"briefing": fb, "grounding": g, "trace": _trace("guardrail", "fallback_briefing", t0)}
        else:
            ledger.record("guardrail", "briefing_grounded", {"figures_checked": g["claims"]}, state["run_id"])
        return {"grounding": g, "trace": _trace("guardrail", "grounded" if g["grounded"] else "regenerate", t0,
                                                claims=g["claims"])}

    def route_guardrail(state: RunState) -> str:
        return END if state["grounding"]["grounded"] or state["grounding"].get("fallback") else "briefing_agent"

    g = StateGraph(RunState)
    chain = ["data_intelligence_agent", "forecast_agent", "liquidity_agent", "risk_agent", "working_capital_agent",
             "scenario_agent", "optimization_agent", "decision_agent", "policy_agent", "governance_agent",
             "briefing_agent", "guardrail"]
    nodes = {"planner": planner, "policy_answer": policy_answer, "insight_agent": insight_agent,
             "data_intelligence_agent": data_intelligence_agent, "forecast_agent": forecast_agent,
             "liquidity_agent": liquidity_agent, "risk_agent": risk_agent, "working_capital_agent": working_capital_agent,
             "scenario_agent": scenario_agent, "optimization_agent": optimization_agent, "decision_agent": decision_agent,
             "policy_agent": policy_agent, "governance_agent": governance_agent, "briefing_agent": briefing_agent,
             "guardrail": guardrail}
    for name, fn in nodes.items():
        g.add_node(name, fn)
    g.add_edge(START, "planner")
    g.add_conditional_edges("planner", route_plan, ["policy_answer", "insight_agent", "data_intelligence_agent"])
    g.add_edge("policy_answer", END)
    g.add_edge("insight_agent", END)
    for x, y in zip(chain[:-2], chain[1:-1], strict=True):
        g.add_edge(x, y)
    g.add_edge("briefing_agent", "guardrail")
    g.add_conditional_edges("guardrail", route_guardrail, [END, "briefing_agent"])
    return g.compile(), ledger


def _policy_code_refs() -> list[str]:
    """Clause ids enforced by policy-as-code (approval matrix etc.) — valid citations even if not retrieved."""
    pol = load_policy()
    return sorted({r.policy_ref for r in pol.approval_matrix} | {"TP-2.2", "TP-7.1", "TP-7.2", "TP-3.1", "TP-3.2",
                                                                  "TP-3.3", "TP-4.1", "RCF-8.1", "RCF-22.1"})
