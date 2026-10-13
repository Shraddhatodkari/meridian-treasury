"""LangGraph workflow for the REAL public-company case (SEC filings + public market data).

  planner ─┬─(question)──► public_insight ─► END
           └─(review)────► data_intelligence ─► forecast ─► liquidity ─► risk ─► working_capital ─► scenario
                              guardrail ◄─ briefing ◄─ governance ◄─ policy ◄─ decision ◄──────────────┘

Same governance as the evaluation workflow: per-agent MCP tool allowlists (read-only, no payment tool), structured
outputs, numeric grounding, policy citation verification, deterministic governance gate, hash-chained audit,
degraded-mode failure handling. Claude is used when configured; otherwise deterministic reasoners run and the
run says so.
"""

from __future__ import annotations

import operator
import time
from datetime import UTC, datetime
from typing import Annotated, Any, TypedDict, cast

from langgraph.graph import END, START, StateGraph

from meridian.agents.schemas import AgentFinding, CFOBriefing
from meridian.governance.audit import AuditLedger
from meridian.governance.guardrails import mask, numeric_grounding
from meridian.governance.policy import load_policy
from meridian.llm.claude import PROMPT_VERSION, ClaudeClient
from meridian.observability.metrics import GUARDRAIL_EVENTS
from meridian.orchestration.gateway import MCPGateway
from meridian.public_company import reasoning as R

SOURCES = ["public.sec.companyfacts", "public.sec.peers", "market.sofr", "market.ust_curve", "market.credit_spread_a",
           "market.credit_spread"]
INSTR = ("You analyse a REAL listed company from its SEC filings. Use ONLY numbers in the evidence; keep each figure "
         "exactly as given; label estimates as estimates; never invent private data (bank accounts, invoices, "
         "covenants). If something is not in the evidence, say it is not available from public data.")


def _merge(a: dict, b: dict) -> dict:
    return {**(a or {}), **(b or {})}


class PublicState(TypedDict, total=False):
    run_id: str
    cik: int
    question: str | None
    requested_by: str
    evidence: Annotated[dict[str, Any], _merge]
    findings: Annotated[list[dict], operator.add]
    tool_calls: Annotated[list[dict], operator.add]
    failed_agents: Annotated[list[str], operator.add]
    trace: Annotated[list[dict], operator.add]
    actions: list[dict]
    recommendations: list[dict]
    rules: list[dict]
    briefing: dict
    grounding: dict
    briefing_attempts: int
    answer: dict


def _trace(actor: str, action: str, t0: float, **detail) -> list[dict]:
    return [{"ts": datetime.now(UTC).isoformat(), "actor": actor, "action": action,
             "latency_ms": round((time.perf_counter() - t0) * 1000, 1), "detail": detail}]


def build_public_graph(store):
    ledger = AuditLedger(store)
    policy = load_policy()
    llms: dict[str, ClaudeClient] = {}

    def llm(run_id: str) -> ClaudeClient:
        if run_id not in llms:
            llms[run_id] = ClaudeClient(policy.llm.max_calls_per_run)
        return llms[run_id]

    def finalize(state, f: AgentFinding, ev: dict, gw: MCPGateway, client: ClaudeClient) -> dict:
        text = " ".join([f.headline, *f.key_points, *f.risks, *f.recommendations])
        g = numeric_grounding(text, ev)
        d = f.model_dump()
        if not g["grounded"]:
            GUARDRAIL_EVENTS.labels(type="ungrounded_number_specialist").inc(len(g["ungrounded"]))
            d["confidence"] = round(d["confidence"] * 0.6, 2)
            d["limitations"].append(f"{len(g['ungrounded'])} figure(s) could not be traced to tool output")
        d["validation"] = {"numeric_grounding": g["grounded"], "figures_checked": g["claims"],
                           "ungrounded": g["ungrounded"], "schema": "valid"}
        d["provenance"] = {"tool_calls": gw.calls, "data_sources": SOURCES, "prompt_version": PROMPT_VERSION,
                           "llm_provider": client.mode, "data_classification": "PUBLIC_REAL",
                           "llm_model": client.settings.llm_model if client.mode != "mock" else "deterministic offline reasoner",
                           "llm_source": client.last_source.get(f.agent, "deterministic")}
        d["audit_hash"] = ledger.record(f.agent, "agent_output", {k: d[k] for k in (
            "headline", "confidence", "validation", "provenance", "policy_refs")}, state["run_id"])[:16]
        return d

    def guarded(agent: str):
        def wrap(fn):
            async def run(state: PublicState) -> dict:
                t0 = time.perf_counter()
                try:
                    return await fn(state)
                except Exception as e:  # noqa: BLE001 - degrade, never crash or invent
                    ledger.record(agent, "agent_failed", {"error": f"{type(e).__name__}: {e}"[:300]}, state["run_id"])
                    d = AgentFinding(agent=agent, headline=f"{agent} FAILED — its evidence is excluded", confidence=0.0,
                                     limitations=[f"{type(e).__name__}: {str(e)[:160]}"]).model_dump()
                    d["status"] = "FAILED"
                    d["validation"] = {"numeric_grounding": False, "figures_checked": 0, "ungrounded": [], "schema": "n/a"}
                    return {"findings": [d], "failed_agents": [agent], "trace": _trace(agent, "FAILED", t0)}
            return run
        return wrap

    async def specialist(state, agent: str, tool: str, key: str, offline) -> dict:
        gw = MCPGateway(state["run_id"], ledger)
        ev = await gw.call(agent, tool, cik=state["cik"])
        t0 = time.perf_counter()
        client = llm(state["run_id"])
        f = await client.structured(agent, INSTR, mask(ev), AgentFinding, offline)
        return {"evidence": {key: ev}, "findings": [finalize(state, f, ev, gw, client)], "tool_calls": gw.calls,
                "trace": _trace(agent, "finding", t0)}

    async def planner(state: PublicState) -> dict:
        t0 = time.perf_counter()
        ledger.record("orchestrator", "run_started", {"case": "public", "cik": state["cik"], "question": state.get("question"),
                                                      "requested_by": state.get("requested_by"),
                                                      "policy_version": policy.version}, state["run_id"])
        intent = "PUBLIC_INSIGHT" if state.get("question") else "PUBLIC_COMPANY_REVIEW"
        ledger.record("planner_agent", "plan", {"intent": intent, "topic": R.classify(state.get("question") or "")},
                      state["run_id"])
        return {"evidence": {"plan": {"intent": intent}}, "trace": _trace("planner_agent", intent, t0)}

    async def public_insight(state: PublicState) -> dict:
        from meridian.public_company.analytics import get_public_company

        t0 = time.perf_counter()
        pc = get_public_company(state["cik"])
        ans = R.answer(pc, cast(str, state["question"]), policy.version)   # routed here only when a question is set
        g = numeric_grounding(ans["conclusion"], {"answer": ans, "metrics": pc.metrics, "maturities": pc.maturities(),
                                                  "scenarios": pc.scenarios(), "peers": pc.peers(), "rates": pc.rates,
                                                  "wc_bridge": pc.wc_bridge(), "forecast": pc.forecast(),
                                                  "policy": policy.public_company.model_dump()})
        ans["grounding"] = {"grounded": g["grounded"], "figures_checked": g["claims"]}
        ledger.record("insight_agent", "public_insight", {"topic": ans["topic"], "grounded": g["grounded"]}, state["run_id"])
        return {"answer": ans, "trace": _trace("insight_agent", ans["topic"], t0)}

    @guarded("data_intelligence_agent")
    async def data_intelligence_agent(state):
        return await specialist(state, "data_intelligence_agent", "get_public_data_quality", "data_quality",
                                R.data_intelligence)

    @guarded("forecast_agent")
    async def forecast_agent(state):
        return await specialist(state, "forecast_agent", "get_public_cash_flow", "cash_flow", R.forecast)

    @guarded("liquidity_agent")
    async def liquidity_agent(state):
        return await specialist(state, "liquidity_agent", "get_public_liquidity", "liquidity", R.liquidity)

    @guarded("risk_agent")
    async def risk_agent(state):
        return await specialist(state, "risk_agent", "get_public_debt_and_rates", "rates", R.risk)

    @guarded("working_capital_agent")
    async def working_capital_agent(state):
        return await specialist(state, "working_capital_agent", "get_public_working_capital", "working_capital",
                                R.working_capital)

    @guarded("scenario_agent")
    async def scenario_agent(state):
        gw = MCPGateway(state["run_id"], ledger)
        sc = await gw.call("scenario_agent", "get_public_scenarios", cik=state["cik"])
        t0 = time.perf_counter()
        client = llm(state["run_id"])
        ev = {"scenarios": sc}
        f = await client.structured("scenario_agent", INSTR, ev, AgentFinding, R.scenario)
        return {"evidence": ev, "findings": [finalize(state, f, ev, gw, client)], "tool_calls": gw.calls,
                "trace": _trace("scenario_agent", "finding", t0)}

    async def decision_agent(state: PublicState) -> dict:
        """Evidence-gated rules (PC-*). Recommendations depending on a failed agent's evidence are excluded."""
        from meridian.public_company.analytics import get_public_company
        from meridian.public_company.recommendations import evaluate

        t0 = time.perf_counter()
        failed = set(state.get("failed_agents", []))
        out = evaluate(get_public_company(state["cik"]), state["run_id"], policy)
        dep = {"REFINANCING": "liquidity_agent", "LIQUIDITY": "liquidity_agent", "WORKING_CAPITAL": "working_capital_agent",
               "CAPITAL_ALLOCATION": "forecast_agent", "RATE_RISK": "risk_agent"}
        excluded = [r for r in out["recommendations"] if dep[r["category"]] in failed]
        recs = [r for r in out["recommendations"] if r not in excluded]
        keep = {a for r in recs for a in r["action_ids"]}
        acts = [a for a in out["actions"] if a["action_id"] in keep]
        findings = {f["agent"]: f for f in state.get("findings", [])}
        calls = {c["tool"]: c for c in state.get("tool_calls", [])}
        tool_of = {"REFINANCING": "get_public_liquidity", "LIQUIDITY": "get_public_liquidity",
                   "WORKING_CAPITAL": "get_public_working_capital", "CAPITAL_ALLOCATION": "get_public_cash_flow",
                   "RATE_RISK": "get_public_debt_and_rates"}
        for r in recs:
            agent = dep[r["category"]]
            for a in acts:
                if a["action_id"] in r["action_ids"]:
                    c = calls.get(tool_of[r["category"]], {})
                    a["evidence"] = {
                        "data": [{"source_id": s, "classification": "PUBLIC_REAL"} for s in SOURCES[:2]]
                        + [{"metric": e["id"], "value": e["value"], "basis": e["basis"], "period": e["period"]}
                           for e in r["evidence"]],
                        "analysis": {"tool": tool_of[r["category"]], "result_sha256": c.get("result_sha256"),
                                     "tool_call_audit_hash": c.get("audit_hash"),
                                     "method": "Deterministic rule on SEC-reported facts (Meridian reference policy PC-*)"},
                        "agent": {"agent": agent, "headline": findings.get(agent, {}).get("headline"),
                                  "confidence": findings.get(agent, {}).get("confidence"),
                                  "grounded": findings.get(agent, {}).get("validation", {}).get("numeric_grounding"),
                                  "audit_hash": findings.get(agent, {}).get("audit_hash")},
                        "policy": {"refs": a["policy_refs"], "policy_version": policy.version,
                                   "required_approver": a["required_approver"]},
                        "recommendation": a["rationale"], "nature": r["nature"]}
        f = R.decision({"recommendations": recs, "rules": out["rules"]})
        d = f.model_dump() | {"validation": {"numeric_grounding": True, "figures_checked": 0, "ungrounded": [],
                                             "schema": "valid"},
                              "provenance": {"tool_calls": [], "data_sources": SOURCES, "llm_provider": "deterministic"},
                              "rules": out["rules"]}
        h = ledger.record("decision_agent", "recommendations_built", {
            "case": "public", "rules": out["rules"],
            "recommendations": [{k: r[k] for k in ("rec_id", "category", "priority", "approval_required")} for r in recs],
            "excluded": [r["rec_id"] for r in excluded]}, state["run_id"])
        d["audit_hash"] = h[:16]
        for a in acts:
            a.setdefault("evidence", {})["audit"] = {"proposed_record_hash": h[:16]}
        return {"actions": acts, "recommendations": recs, "rules": out["rules"], "findings": [d],
                "trace": _trace("decision_agent", "recommendations", t0, n=len(recs))}

    @guarded("policy_agent")
    async def policy_agent(state: PublicState) -> dict:
        from meridian.nlp.rag import verify_citations

        gw = MCPGateway(state["run_id"], ledger)
        qs = {"liquidity": "liquidity coverage of short-term debt undrawn committed facilities",
              "refinancing": "refinancing readiness debt due within twelve months plan",
              "working_capital": "working-capital deterioration DSO DIO year on year review",
              "rates": "repricing exposure 100 basis points EBITDA rate-risk review",
              "capital": "shareholder returns free cash flow capital allocation",
              "governance": "public-data recommendations advisory decision mandate never move money dual control",
              "peers": "peer benchmarks public filings context only business models differ",
              "limitations": "disclosure of data limitations sources filing periods not available from public data"}
        clauses = {k: await gw.call("policy_agent", "search_treasury_policy", query=q, k=3) for k, q in qs.items()}
        retrieved = [c for hits in clauses.values() for c in hits if c.get("relevant", True)]
        recs = state.get("recommendations", [])
        for r in recs:
            r["policy_verification"] = verify_citations(" ".join(r["policy_refs"]), retrieved)
            r["policy_citations"] = [{"clause_id": c["clause_id"], "document": c["document"],
                                      "doc_version": c.get("doc_version"), "relevance": c.get("relevance"),
                                      "retrieved_at": c.get("retrieved_at"), "citation": c.get("citation")}
                                     for c in {c["clause_id"]: c for c in retrieved}.values()
                                     if c["clause_id"] in r["policy_refs"]]
        t0 = time.perf_counter()
        cited = sorted({c["clause_id"] for c in retrieved if c["clause_id"].startswith("PC-")})
        f = AgentFinding(agent="policy_agent", headline=f"{len(cited)} reference-policy clauses retrieved and verified "
                         f"for {len(recs)} recommendation{'' if len(recs) == 1 else 's'}", key_points=[f"{c}" for c in cited],
                         policy_refs=cited, confidence=0.9,
                         limitations=["Meridian reference policy (illustrative) — not the company's internal policy"])
        return {"evidence": {"policy_clauses": clauses}, "recommendations": recs,
                "findings": [finalize(state, f, {"clauses": clauses}, gw, llm(state["run_id"]))],
                "tool_calls": gw.calls, "trace": _trace("policy_agent", "finding", t0)}

    async def governance_agent(state: PublicState) -> dict:
        t0 = time.perf_counter()
        acts, recs = state.get("actions", []), state.get("recommendations", [])
        from meridian.domain.models import PAYMENT_ACTIONS

        checks: list[dict[str, Any]] = [
            {"check": "Company data is PUBLIC · REAL with filing-level lineage (no synthetic inputs)", "passed": all(
                r.get("data_classification") == "PUBLIC_REAL" for r in recs), "detail": "SEC EDGAR XBRL companyfacts"},
            {"check": "Every action routed through the approval matrix (TP-2.1 / PC-*)",
             "passed": all(a.get("required_approver") for a in acts), "detail": f"{len(acts)} actions routed"},
            {"check": "No public-data action is a payment (PC-6.1, TP-7.1)",
             "passed": not any(a["type"] in PAYMENT_ACTIONS for a in acts), "detail": "decision mandates only"},
            {"check": "Every recommendation cites verified policy clauses",
             "passed": all(r.get("policy_verification", {}).get("verified") and r.get("policy_citations") for r in recs),
             "detail": ", ".join(f"{r['short_id']}: {r.get('policy_verification', {}).get('unsupported')}" for r in recs
                                 if not r.get("policy_verification", {}).get("verified")) or "all verified"},
            {"check": "Data limitations disclosed (PC-6.2)", "passed": all("data_limitations" in r for r in recs),
             "detail": "NOT AVAILABLE items listed per recommendation"},
            {"check": "Requester recorded for segregation of duties (TP-2.2)", "passed": bool(state.get("requested_by")),
             "detail": f"requested_by={state.get('requested_by')}"},
            {"check": "No agent failed", "passed": not state.get("failed_agents"),
             "detail": ", ".join(state.get("failed_agents", [])) or "all agents completed"},
        ]
        for r in recs:
            r["governance"] = {"checks_passed": all(c["passed"] for c in checks), "data_classification": "PUBLIC_REAL"}
        d = AgentFinding(agent="governance_agent", headline=f"{sum(c['passed'] for c in checks)}/{len(checks)} governance "
                         "checks passed", key_points=[f"{'PASS' if c['passed'] else 'FAIL'} — {c['check']}" for c in checks],
                         confidence=1.0).model_dump() | {
            "validation": {"numeric_grounding": True, "figures_checked": 0, "ungrounded": [], "schema": "valid"},
            "checks": checks, "provenance": {"tool_calls": [], "data_sources": [], "llm_provider": "deterministic"}}
        d["audit_hash"] = ledger.record("governance_agent", "governance_checks", {"checks": checks}, state["run_id"])[:16]
        return {"findings": [d], "recommendations": recs,
                "trace": _trace("governance_agent", "checks", t0, failed=sum(not c["passed"] for c in checks))}

    def brief_ev(state: PublicState) -> dict:
        from meridian.public_company.analytics import get_public_company

        pc = get_public_company(state["cik"])
        return {"company": pc.subject["name"], "latest_quarter": pc.latest_quarter, "latest_fiscal_year_end": pc.latest_fy,
                "debt_date": pc.debt_date, "captive_finance": pc.captive,
                "metrics": {k: {"label": m["label"], "value": m["value"], "basis": m["basis"]} for k, m in pc.metrics.items()},
                "recommendations": [{k: r[k] for k in ("short_id", "title", "approval_required", "policy_refs", "impact")}
                                    for r in state.get("recommendations", [])],
                "feedback": state.get("grounding", {}).get("ungrounded")}

    async def briefing_agent(state: PublicState) -> dict:
        t0 = time.perf_counter()
        ev = brief_ev(state)
        client = llm(state["run_id"])
        b = await client.structured("briefing_agent", INSTR + " Write the CFO briefing.", ev, CFOBriefing, R.briefing)
        d = b.model_dump()
        d["confidence"] = round(min((f.get("confidence", 0.8) for f in state.get("findings", [])
                                     if f.get("status") != "FAILED"), default=0.8), 2)
        d["llm_provider"] = client.mode
        d["llm_source"] = client.last_source.get("briefing_agent", "deterministic")
        d["llm_usage"] = client.summary()
        return {"briefing": d, "briefing_attempts": state.get("briefing_attempts", 0) + 1,
                "trace": _trace("briefing_agent", "draft", t0)}

    async def guardrail(state: PublicState) -> dict:
        t0 = time.perf_counter()
        b = state["briefing"]
        text = " ".join([b["executive_summary"], *b["key_numbers"], *b["decisions_required"], *b["risks"]])
        ev = brief_ev(state) | {"evidence": state.get("evidence", {})}
        g = numeric_grounding(text, ev)
        if not g["grounded"]:
            ledger.record("guardrail", "ungrounded_numbers", g, state["run_id"])
            if state.get("briefing_attempts", 0) >= 2:
                fb = R.briefing(brief_ev(state)).model_dump()
                fb["confidence"], fb["llm_provider"] = b.get("confidence"), "deterministic fallback"
                g = {**numeric_grounding(" ".join([fb["executive_summary"], *fb["key_numbers"]]), ev), "fallback": True}
                ledger.record("guardrail", "fallback_briefing", g, state["run_id"])
                return {"briefing": fb, "grounding": g, "trace": _trace("guardrail", "fallback_briefing", t0)}
        else:
            ledger.record("guardrail", "briefing_grounded", {"figures_checked": g["claims"]}, state["run_id"])
        return {"grounding": g, "trace": _trace("guardrail", "grounded" if g["grounded"] else "regenerate", t0,
                                                claims=g["claims"])}

    g = StateGraph(PublicState)
    chain = ["data_intelligence_agent", "forecast_agent", "liquidity_agent", "risk_agent", "working_capital_agent",
             "scenario_agent", "decision_agent", "policy_agent", "governance_agent", "briefing_agent", "guardrail"]
    nodes = {"planner": planner, "public_insight": public_insight, "data_intelligence_agent": data_intelligence_agent,
             "forecast_agent": forecast_agent, "liquidity_agent": liquidity_agent, "risk_agent": risk_agent,
             "working_capital_agent": working_capital_agent, "scenario_agent": scenario_agent,
             "decision_agent": decision_agent, "policy_agent": policy_agent, "governance_agent": governance_agent,
             "briefing_agent": briefing_agent, "guardrail": guardrail}
    for name, fn in nodes.items():
        g.add_node(name, fn)
    g.add_edge(START, "planner")
    g.add_conditional_edges("planner", lambda s: "public_insight" if s.get("question") else "data_intelligence_agent",
                            ["public_insight", "data_intelligence_agent"])
    g.add_edge("public_insight", END)
    for x, y in zip(chain[:-2], chain[1:-1], strict=True):
        g.add_edge(x, y)
    g.add_edge("briefing_agent", "guardrail")
    g.add_conditional_edges("guardrail", lambda s: END if s["grounding"]["grounded"] or s["grounding"].get("fallback")
                            else "briefing_agent", [END, "briefing_agent"])
    return g.compile(), ledger
