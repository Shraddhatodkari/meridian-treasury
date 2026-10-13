"""Agent registry — the contract for every agent: responsibility, bounded tools, I/O schemas, policy
constraints, validation and confidence method. Served to the governance console and checked in tests
against the policy file (tools) and the graph (nodes), so documentation cannot drift from behaviour.
"""

from __future__ import annotations

from pydantic import BaseModel

from meridian.governance.policy import load_policy


class AgentSpec(BaseModel):
    name: str
    order: int
    responsibility: str
    input_schema: str
    output_schema: str
    tools: list[str]
    policy_constraints: list[str]
    validation: list[str]
    confidence_method: str
    can_move_money: bool = False


def _a(name, order, resp, inp, out, constraints, validation, conf):
    return AgentSpec(name=name, order=order, responsibility=resp, input_schema=inp, output_schema=out, tools=[],
                     policy_constraints=constraints, validation=validation, confidence_method=conf)


# Order = position in the LangGraph chain (orchestration/graph.py). Failure handling for every agent: an exception is
# caught by `guarded()`, audited as `agent_failed`, and the agent's evidence is excluded from decisions (degraded run).
_SPECS = [
    _a("planner_agent", 1, "Classify the request (daily run, scenario, CFO question, policy question) and translate "
       "stresses into a typed Scenario.", "question: str", "Plan", ["No data access beyond policy search"],
       ["Schema validation (Pydantic)", "Labelled planner set in CI (accuracy gate ≥ 0.9)"],
       "Deterministic parse coverage; LLM output schema-enforced"),
    _a("insight_agent", 2, "Answer a specific executive question (liquidity change, DSO drivers, excess cash, covenants, "
       "refinancing, rates/FX, trapped cash, shocks) from the deterministic insight library.", "topic",
       "Insight", ["Answer must carry conclusion, impact, sources, policy, assumptions, confidence and limitations"],
       ["Numeric grounding", "Citation verification"], "Source validation + freshness"),
    _a("data_intelligence_agent", 2, "Check that the data is fit for decision: classification, freshness, validation "
       "and the data-quality gate; block decisions on failed sources.", "provenance + ingestion checks",
       "AgentFinding", ["Production refuses SYNTHETIC data", "Stale/failed sources lower confidence"],
       ["Every source has as-of, checksum, validation", "Numeric grounding"], "Share of validated, fresh sources"),
    _a("forecast_agent", 3, "Interpret the 13-week probabilistic forecast, minimum liquidity, covenant headroom and "
       "funding requirement; flag entities below buffer.", "kpis + forecast summary", "AgentFinding",
       ["TP-3.1 buffers", "RCF-22.1 covenant", "TP-3.3 management floor"],
       ["Numeric grounding vs tool output", "Back-test WAPE gate (≤ 15%)"],
       "Back-test interval coverage × source validation status"),
    _a("liquidity_agent", 4, "Explain where cash is, what moved it (bridge), what is deployable and where it is trapped.",
       "entity positions + liquidity overview", "AgentFinding", ["CR-* repatriation limits", "TP-5.2 bank limits"],
       ["Waterfall reconciles to balances (tested)", "Numeric grounding"], "Source validation of bank data"),
    _a("risk_agent", 5, "Funding, covenant, rate, FX, counterparty and hazard risk: debt profile, covenant tests and the "
       "likelihood × impact register.", "risk events + debt profile + register", "AgentFinding",
       ["RCF-22.x covenants", "DP-2.1 refinancing window", "DP-3.1 fixed/floating mix"],
       ["Numeric grounding", "ESTIMATED figures labelled"], "Lowest of source freshness/validation"),
    _a("working_capital_agent", 6, "DSO/DPO drivers, collections priorities and supplier discount capture.",
       "WC drivers + collections ranking + early-pay plan", "AgentFinding", ["TP-6.1 escalation", "TP-2.3", "PP-1.x"],
       ["DSO contributions sum to the change (tested)", "Discount return > cost of funds", "Numeric grounding"],
       "Model card coverage × source validation"),
    _a("scenario_agent", 7, "Compare the requested scenario with global-stress and hazard scenarios on the same seed "
       "and paths; report headroom loss and covenant breach weeks.", "scenario comparison", "AgentFinding",
       ["RCF-22.1"], ["Monotonic stress checks (tested)", "Numeric grounding"], "Reproducibility (same seed/paths)"),
    _a("optimization_agent", 8, "Explain the LP allocation (sweeps, fundings, revolver repayment) with objective, "
       "binding constraints and shadow prices; never alter amounts.", "optimisation result", "AgentFinding",
       ["TP-3.1", "TP-3.2 no yield arbitrage", "TP-4.1", "CR-*", "RCF-8.1 prepayment rounding"],
       ["Optimiser constraints verified in tests", "Numeric grounding"], "LP optimality status × source validation"),
    _a("decision_agent", 9, "Turn verified evidence into governed recommendations (R01–R07) and proposed actions with "
       "impact basis, constraints, risks, owner and approval route. Excludes evidence from failed agents.",
       "evidence from all upstream agents", "Recommendation[] + ProposedAction[]",
       ["TP-2.1 approval matrix", "Impact never exceeds its evidence (no overclaim)"],
       ["Recommendation schema", "Overclaim cap (tested)"], "Minimum confidence of contributing evidence"),
    _a("policy_agent", 10, "Retrieve and cite the clauses that govern each recommendation; verify every cited clause.",
       "recommendations + retrieved clauses", "AgentFinding", ["Cites only non-quarantined, relevant clauses"],
       ["Retrieval hit@3 gate (≥ 0.8)", "Relevance floor", "Citation verification", "Quarantine filter"],
       "Retrieval relevance of cited clauses"),
    _a("governance_agent", 11, "Deterministic governance checks before anything reaches a human: data fitness, no "
       "payment tools, approval routing, SoD readiness, grounding, citations, failed agents.", "run state",
       "GovernanceChecks", ["TP-2.2 SoD", "TP-7.1 recommend only"], ["7 deterministic checks"], "N/A (pass/fail)"),
    _a("briefing_agent", 12, "Write the executive briefing: decisions, numbers, risks, recommendation impacts, "
       "citations.", "all findings + recommendations (IBANs masked)", "CFOBriefing", ["TP-7.1 recommend only",
       "DLP masking"], ["Numeric grounding (blocking): regenerate once, then deterministic fallback"],
       "Minimum confidence of contributing findings"),
    AgentSpec(name="guardrail", order=13, responsibility="Deterministic gate: numeric grounding, DLP, injection "
              "quarantine, LLM budget. Not an LLM.", input_schema="briefing + evidence", output_schema="GroundingResult",
              tools=[], policy_constraints=["TP-7.1"], validation=["Unit-tested"], confidence_method="N/A (pass/fail)"),
]


def registry() -> list[AgentSpec]:
    perms = load_policy().tool_permissions
    return [s.model_copy(update={"tools": perms.get(s.name, [])}) for s in sorted(_SPECS, key=lambda s: s.order)]
