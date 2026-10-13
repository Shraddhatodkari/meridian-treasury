"""Answers to the questions a CFO actually asks — each with figures, evidence, confidence and limitations.

Every answer is computed deterministically from the engine and cites:
  data sources (provenance ids) → analysis (method / model) → policy clauses.
Agents may rephrase these answers; they may not change the numbers (numeric grounding guardrail).
"""

from __future__ import annotations

from meridian.analytics.scenarios import Scenario
from meridian.governance.policy import load_policy

TOPICS = {
    "trapped_cash": "Where is cash trapped?",
    "liquidity_risk": "Which entities are approaching liquidity risk?",
    "collections": "Which receivables should Treasury prioritise?",
    "releasable_liquidity": "How much liquidity can be released?",
    "repay_vs_preserve": "Should we repay debt or preserve liquidity?",
    "shocks": "What happens under rate, FX and collections shocks?",
    "wc_actions": "Which working-capital actions create the most value?",
    "cfo_approvals": "What requires CFO approval?",
    "liquidity_change": "Why did liquidity change?",
    "dso_drivers": "What is causing the DSO increase?",
    "excess_cash": "Which entities have excess cash?",
    "covenants": "Which covenants are closest to breach?",
    "refinancing": "Which debt facilities create refinancing risk?",
    "rate_exposure": "What is our interest-rate and FX exposure?",
    "liquidity_outlook": "What is our 13-week liquidity risk?",
}
KEYWORDS = {
    "trapped_cash": ["trapped", "capital control", "repatriat", "stuck"],
    "liquidity_risk": ["approaching", "liquidity risk", "below buffer", "short", "shortfall", "at risk"],
    "collections": ["receivable", "collect", "prioriti", "overdue", "debtor"],
    "releasable_liquidity": ["release", "free up", "unlock", "excess cash", "idle"],
    "repay_vs_preserve": ["repay", "preserve", "pay down", "paydown", "deleverag"],
    "shocks": ["shock", "sensitivity", "stress", "tornado"],
    "wc_actions": ["working-capital action", "working capital action", "most value", "highest value", "levers"],
    "cfo_approvals": ["cfo approval", "requires cfo", "need cfo", "approve"],
    "liquidity_change": ["deteriorat", "why did liquidity", "why did cash", "cash change", "liquidity change",
                         "what moved", "bridge", "decline in cash"],
    "dso_drivers": ["dso", "days sales", "causing the dso", "dso increase", "receivable days"],
    "excess_cash": ["excess cash", "surplus cash", "which entities have", "cash rich"],
    "covenants": ["covenant", "closest to breach", "leverage", "interest cover"],
    "refinancing": ["refinanc", "maturit", "term loan", "debt facilit"],
    "rate_exposure": ["interest-rate", "interest rate", "fx exposure", "currency exposure", "rate exposure", "hedg"],
    "liquidity_outlook": ["13-week", "13 week", "liquidity risk", "forward liquidity", "liquidity outlook"],
}


def classify(question: str) -> str | None:
    q = question.lower()
    best, score = None, 0
    for topic, kws in KEYWORDS.items():
        s = sum(1 for k in kws if k in q)
        if s > score:
            best, score = topic, s
    return best


def _usd(x: float) -> str:
    a = abs(x)
    s = "-" if x < 0 else ""
    return f"{s}${a / 1e9:.2f}B" if a >= 1e9 else f"{s}${a / 1e6:.1f}M" if a >= 1e7 else (
        f"{s}${a / 1e6:.2f}M" if a >= 1e6 else f"{s}${a / 1e3:.0f}K")


def _confidence(engine, sources: list[str]) -> tuple[str, list[str]]:
    prov = {p["source_id"]: p for p in engine.provenance()}
    reasons = []
    level = "HIGH"
    for sid in sources:
        p = prov.get(sid)
        if not p:
            continue
        if p["validation"] == "FAILED":
            level = "LOW"
            reasons.append(f"{sid} failed validation")
        elif p["validation"] == "PASSED_WITH_WARNINGS" and level == "HIGH":
            level = "MEDIUM"
            reasons.append(f"{sid} has validation warnings")
        if p.get("freshness") == "STALE":
            level = "LOW" if level != "LOW" else level
            reasons.append(f"{sid} is stale")
        if p["classification"] == "SYNTHETIC":
            reasons.append(f"{sid} is SYNTHETIC demo data")
    return level, reasons


def _answer(engine, topic: str, headline: str, figures: dict, detail: list, sources: list[str], method: str,
            policy: list[str], limitations: list[str], reasoning: list[str] | None = None,
            assumptions: list[str] | None = None, impact: list[dict] | None = None, metrics: list[str] | None = None,
            chart: dict | None = None) -> dict:
    """Standard copilot answer: conclusion · financial impact (with basis) · reasoning · evidence · sources (system,
    as-of, classification) · policy · assumptions · confidence · limitations."""
    level, reasons = _confidence(engine, sources)
    prov = {p["source_id"]: p for p in engine.provenance()}
    srcs = [{"source_id": s, "system": prov.get(s, {}).get("system"), "as_of": prov.get(s, {}).get("as_of"),
             "classification": prov.get(s, {}).get("classification"), "freshness": prov.get(s, {}).get("freshness")}
            for s in sources]
    if impact is None:
        impact = [{"label": k.replace("_usd", "").replace("_", " ").capitalize(), "value_usd": round(v),
                   "basis": "CALCULATED"} for k, v in figures.items() if k.endswith("_usd") and isinstance(v, (int, float))][:4]
    return {"topic": topic, "question": TOPICS[topic], "headline": headline, "conclusion": headline,
            "financial_impact": impact, "reasoning": reasoning or [method], "assumptions": assumptions or [],
            "sources": srcs, "metrics": metrics or [], "chart": chart, "figures": figures, "detail": detail,
            "evidence": {"data_sources": sources, "analysis": method, "model_id": engine.model.card.get("model_id"),
                         "policy_refs": policy, "policy_version": load_policy().version},
            "policy_refs": policy, "confidence": level, "confidence_reasons": reasons, "limitations": limitations}


def trapped_cash(engine) -> dict:
    fx = engine.g.fx
    rows = []
    for e in engine.entities():
        if not e["capital_controls"]:
            continue
        buf = e["min_buffer_local"] * fx[e["currency"]]
        above = max(0.0, e["balance_usd"] - buf)
        trapped = max(0.0, above - e["repatriation_allowance_usd"])
        rows.append({"entity_id": e["entity_id"], "entity": e["name"], "country": e["country"],
                     "cash_usd": e["balance_usd"], "buffer_usd": round(buf),
                     "repatriable_now_usd": round(min(above, e["repatriation_allowance_usd"])),
                     "trapped_usd": round(trapped), "upstream_cost_bps": e["upstream_cost_bps"]})
    total = sum(r["trapped_usd"] for r in rows)
    top = max(rows, key=lambda r: r["trapped_usd"]) if rows else None
    head = (f"{_usd(total)} is trapped above operating buffers in {sum(r['trapped_usd'] > 0 for r in rows)} "
            f"capital-controlled entities; the largest pool is {top['entity']} ({_usd(top['trapped_usd'])})."
            if top else "No cash is held in capital-controlled entities.")
    return _answer(engine, "trapped_cash", head, {"trapped_total_usd": round(total),
                   "repatriable_now_usd": sum(r["repatriable_now_usd"] for r in rows)}, rows,
                   ["company.bank_balances", "company.entities", "market.fx"],
                   "Cash above TP-3.1 buffer minus quarterly repatriation allowance per CR-* rules",
                   ["TP-3.1", "CR-1", "CR-2", "CR-3", "CR-4", "RCF-23.4"],
                   ["Allowances are policy inputs — confirm with Tax each quarter",
                    "Dividend capacity (distributable reserves) not modelled"])


def liquidity_risk(engine, scenario: Scenario | None = None) -> dict:
    s = engine.forecast_summary(scenario)
    names = {e["entity_id"]: e["name"] for e in engine.entities()}
    rows = sorted([{**e, "entity": names[e["entity_id"]]} for e in s["entities"] if e["shortfall_vs_buffer_usd"] > 0],
                  key=lambda e: -e["shortfall_vs_buffer_usd"])
    head = (f"{len(rows)} entities fall below their operating buffer on the P10 path; combined shortfall "
            f"{_usd(sum(r['shortfall_vs_buffer_usd'] for r in rows))}. Group liquidity headroom stays at "
            f"{_usd(s['min_covenant_headroom_p10_usd'])} above the covenant." if rows else
            "No entity falls below its operating buffer on the P10 path.")
    return _answer(engine, "liquidity_risk", head, {"entities_at_risk": len(rows),
                   "combined_shortfall_usd": sum(r["shortfall_vs_buffer_usd"] for r in rows),
                   "min_covenant_headroom_p10_usd": s["min_covenant_headroom_p10_usd"]}, rows,
                   ["company.bank_balances", "company.receivables", "company.payables", "company.recurring", "model.ar_delay"],
                   f"13-week Monte Carlo ({engine.settings.monte_carlo_paths} paths), P10 trough vs TP-3.1 buffer",
                   ["TP-3.1", "RCF-22.1"], ["Forecast excludes unplanned capex and M&A flows",
                                            "Intra-week timing within a week is not modelled"])


def collections(engine) -> dict:
    rows = engine.collections(15)
    total = sum(r["amount_usd"] for r in rows)
    cost = sum(r["financing_cost_of_delay_usd"] for r in rows)
    disputed = [r for r in rows if r["disputed"]]
    head = (f"Prioritise {len(rows)} invoices worth {_usd(total)}: the model predicts the largest delays; "
            f"{len(disputed)} are disputed and need resolution before cash moves.")
    return _answer(engine, "collections", head, {"invoices": len(rows), "amount_usd": total,
                   "financing_cost_of_delay_usd": cost}, rows,
                   ["company.receivables", "company.counterparties", "model.ar_delay"],
                   "Ranked by financing cost of ML-predicted delay (P50 days late × amount × revolver rate)",
                   ["TP-6.1"], ["Predictions are probabilistic — see P90 column",
                                "Customer relationship context is not in the data"])


def releasable_liquidity(engine) -> dict:
    opt = engine.optimize()
    tr = trapped_cash(engine)["figures"]
    head = (f"{_usd(opt['sweep_total_usd'])} can be swept safely to Group Treasury on the P10 path; after funding "
            f"{_usd(opt['funding_total_usd'])} of subsidiary shortfalls, {_usd(opt['revolver_repay_usd'])} can repay the "
            f"revolver. A further {_usd(tr['trapped_total_usd'])} remains trapped by capital controls.")
    return _answer(engine, "releasable_liquidity", head, {"sweepable_usd": opt["sweep_total_usd"],
                   "funding_needs_usd": opt["funding_total_usd"], "revolver_repay_usd": opt["revolver_repay_usd"],
                   "trapped_usd": tr["trapped_total_usd"], "annualized_benefit_usd": opt["annualized_benefit_usd"]},
                   opt["transfers"], ["company.bank_balances", "company.entities", "market.fx", "market.sofr", "model.ar_delay"],
                   "Linear programme (HiGHS) over P10 trough balances with policy constraints",
                   ["TP-3.1", "TP-3.2", "TP-4.1", "RCF-8.1", "CR-1", "CR-2", "CR-3", "CR-4"],
                   ["FX spreads and transfer fees use standard assumptions (8 bps / 1 bps)",
                    "Tax leakage beyond upstream_cost_bps requires Tax review"])


def repay_vs_preserve(engine) -> dict:
    from meridian.analytics.scenarios import PRESET_SCENARIOS

    opt = engine.optimize()
    base = engine.forecast_summary()
    stress = engine.forecast_summary(PRESET_SCENARIOS["global_stress"])
    f = engine.g.facility
    hub_yield = engine._hub().deposit_yield
    carry = f.rate - hub_yield
    rep = opt["revolver_repay_usd"]
    threshold = 100_000_000
    ok = stress["min_covenant_headroom_p10_usd"] >= threshold and stress["covenant_breach_week"] is None
    decision = "REPAY" if ok and rep > 0 else "PRESERVE"
    head = (f"{decision}: repaying {_usd(rep)} of the revolver saves {_usd(rep * carry)} a year "
            f"({f.rate:.2%} − {hub_yield:.2%} deposit yield). Because the facility is revolving, repayment restores "
            f"undrawn commitment, so covenant liquidity is unchanged; under global stress the P10 headroom is still "
            f"{_usd(stress['min_covenant_headroom_p10_usd'])}." if decision == "REPAY" else
            f"PRESERVE: stressed P10 covenant headroom of {_usd(stress['min_covenant_headroom_p10_usd'])} is below the "
            f"{_usd(threshold)} comfort level, so keep cash rather than repay.")
    return _answer(engine, "repay_vs_preserve", head, {
        "recommended_repay_usd": rep if decision == "REPAY" else 0, "annual_interest_saving_usd": round(rep * carry),
        "revolver_rate": f.rate, "deposit_yield": hub_yield, "base_min_headroom_usd": base["min_covenant_headroom_p10_usd"],
        "stressed_min_headroom_usd": stress["min_covenant_headroom_p10_usd"], "comfort_threshold_usd": threshold},
        [{"rate_source": f.rate_source, "decision_rule": "Repay if stressed P10 covenant headroom ≥ $100M and no breach"}],
        ["company.bank_balances", "company.facility", "market.sofr", "model.ar_delay"],
        "Carry economics + stressed covenant headroom (global stress scenario)",
        ["RCF-8.1", "RCF-22.1", "TP-4.1"],
        ["Redraw is subject to the facility's conditions precedent (no default, repeated representations)",
         "Comfort threshold of $100M is a board-set parameter — confirm with the CFO"])


def shocks(engine) -> dict:
    sc = [Scenario(name="Rates +100 bps", rate_shock_bps=100),
          Scenario(name="EUR −10% vs USD", fx_shock_pct={"EUR": -0.10}),
          Scenario(name="Customers pay 30% slower", collection_delay_pct=0.30),
          Scenario(name="Billing −10%", billing_change_pct={"*": -0.10}),
          Scenario(name="Combined severe stress", rate_shock_bps=100, fx_shock_pct={"EUR": -0.10},
                   collection_delay_pct=0.30, billing_change_pct={"*": -0.10})]
    rows = engine.scenario_compare(sc)
    worst = min(rows, key=lambda r: r["min_covenant_headroom_p10_usd"])
    singles = sorted(rows[:-1], key=lambda r: r["week13_cash_change_vs_base_usd"])
    head = (f"The most damaging single shock is '{singles[0]['scenario']}' ({_usd(singles[0]['week13_cash_change_vs_base_usd'])} "
            f"of week-13 group cash, {_usd(singles[0]['headroom_change_vs_base_usd'])} of minimum P10 headroom)."
            f" Under the combined stress the P10 headroom is {_usd(worst['min_covenant_headroom_p10_usd'])}"
            + (f" with a covenant breach in week {worst['covenant_breach_week']}." if worst["covenant_breach_week"] is not None
               else " and no covenant breach."))
    return _answer(engine, "shocks", head, {"worst_headroom_usd": worst["min_covenant_headroom_p10_usd"]}, rows,
                   ["company.bank_balances", "company.receivables", "market.fx", "market.sofr", "model.ar_delay"],
                   "Single-factor and combined scenario re-forecasts (same seed, same paths)", ["RCF-22.1", "TP-3.1"],
                   ["FX shock applies to translation of balances and flows, not to pricing pass-through",
                    "Scenarios are independent of each other unless combined explicitly",
                    "Minimum headroom can sit at today's opening position, which forward shocks cannot change — "
                    "week-13 cash shows their effect"])


def wc_actions(engine) -> dict:
    opt, ep = engine.optimize(), engine.early_pay()
    col = engine.collections(15)
    peers = engine.peers()
    acts = [
        {"action": "Concentrate surplus cash and fund shortfalls (in-house bank)",
         "annual_value_usd": opt["annualized_benefit_usd"],
         "basis": "LP optimiser vs do-nothing", "approver": "Treasurer / CFO by amount"},
        {"action": f"Repay revolver ({_usd(opt['revolver_repay_usd'])})",
         "annual_value_usd": opt["annualized_interest_saving_from_repay_usd"],
         "basis": "Revolver rate minus hub deposit yield", "approver": "CFO"},
        {"action": "Capture supplier early-payment discounts",
         "annual_value_usd": round(ep["total_saving_usd"] * 52 / engine.settings.forecast_weeks),
         "basis": f"{_usd(ep['total_saving_usd'])} on the current open book, annualised", "approver": "Treasurer"},
        {"action": "Accelerate top-15 predicted-late receivables",
         "annual_value_usd": sum(c["financing_cost_of_delay_usd"] for c in col),
         "basis": "Financing cost of predicted delay", "approver": "AR analyst"},
    ]
    if peers.get("cash_release_at_peer_median_usd"):
        acts.append({"action": f"Close DSO gap to peer median ({peers['dso_gap_days']} days)",
                     "annual_value_usd": round(peers["cash_release_at_peer_median_usd"] * engine.g.facility.rate),
                     "basis": f"{_usd(peers['cash_release_at_peer_median_usd'])} one-off cash release × revolver rate; "
                              "peers from SEC 10-K filings", "approver": "CFO programme"})
    acts = [a for a in acts if a["annual_value_usd"] > 0]   # e.g. no revolver repayment under stress
    acts.sort(key=lambda a: -a["annual_value_usd"])
    head = (f"Highest-value lever: {acts[0]['action']} (~{_usd(acts[0]['annual_value_usd'])}/yr). "
            f"Total identified value ~{_usd(sum(a['annual_value_usd'] for a in acts))}/yr across {len(acts)} levers.")
    return _answer(engine, "wc_actions", head, {"total_annual_value_usd": sum(a["annual_value_usd"] for a in acts)}, acts,
                   ["company.receivables", "company.payables", "company.bank_balances", "market.sofr",
                    "market.peer_benchmarks", "model.ar_delay"],
                   "Sum of optimiser, carry, discount and financing-cost models; peer gap from SEC EDGAR",
                   ["TP-2.3", "TP-4.1", "TP-6.1", "RCF-8.1"],
                   ["Values overlap partly (cash released by collections also reduces revolver need)",
                    "Peer receivable definitions vary (some include financing receivables)"])


def cfo_approvals(engine) -> dict:
    pending = []
    for r in engine.store.list_runs(20):
        for a in r.get("actions", []):
            if a["status"] in {"PROPOSED", "SUBMITTED"} and a["required_approver"] == "cfo":
                pending.append({"run_id": r["run_id"], "action_id": a["action_id"], "type": a["type"],
                                "amount_usd": a["amount_usd"], "rationale": a["rationale"], "policy_refs": a["policy_refs"]})
    head = (f"{len(pending)} proposed actions totalling {_usd(sum(p['amount_usd'] for p in pending))} require CFO approval "
            "under TP-2.1 (transfers > $10M, any revolver draw/repay, any cash leaving a controlled entity)."
            if pending else "No proposed actions are waiting for CFO approval.")
    return _answer(engine, "cfo_approvals", head, {"pending": len(pending),
                   "amount_usd": sum(p["amount_usd"] for p in pending)}, pending, ["company.bank_balances"],
                   "Approval matrix routing of proposed actions", ["TP-2.1", "TP-2.2", "TP-7.1"],
                   ["Only actions proposed by Meridian runs are listed; manual treasury requests live in the TMS"])


def _u(x):
    return _usd(x)


def liquidity_change(engine) -> dict:
    b = engine.liquidity_bridge()
    drv = sorted(b["drivers"], key=lambda d: d["value_usd"])
    worst, best = drv[0], drv[-1]
    verb = "rose" if b["change_usd"] >= 0 else "fell"
    head = (f"Group cash {verb} {_u(abs(b['change_usd']))} over 8 weeks ({_u(b['opening_usd'])} → {_u(b['closing_usd'])}). "
            f"Largest inflow: {best['driver']} {_u(best['value_usd'])}; largest outflow: {worst['driver']} "
            f"{_u(worst['value_usd'])}; FX translation {_u(next((d['value_usd'] for d in drv if d['category'] == 'FX'), 0))}.")
    return _answer(engine, "liquidity_change", head, {"opening_usd": b["opening_usd"], "closing_usd": b["closing_usd"],
                                                      "change_usd": b["change_usd"]}, drv,
                   ["company.bank_transactions", "company.bank_balances", "market.fx_history"],
                   f"{b['transactions']} bank-statement entries classified by driver; FX at {b['fx_basis']}",
                   ["TP-3.1"], ["Covers booked bank entries only (no in-transit items)"],
                   reasoning=["Opening cash = closing balance − net bank movements in the window, at window-start FX",
                              "Movements grouped by transaction category (receipts, supplier payments, payroll, …)",
                              "FX translation = opening local balances × change in ECB USD rate"],
                   impact=[{"label": d["driver"], "value_usd": d["value_usd"], "basis": "OBSERVED" if d["category"] != "FX"
                            else "CALCULATED"} for d in drv[:3] + drv[-2:]], metrics=["group_cash"],
                   chart={"type": "waterfall", "start": b["opening_usd"], "end": b["closing_usd"], "steps": drv})


def dso_drivers(engine) -> dict:
    d = engine.dso_drivers()
    top = d["by_customer"][:5]
    head = (f"DSO {'rose' if d['change_days'] >= 0 else 'fell'} from {d['dso_t0']} to {d['dso_t1']} days in 90 days, "
            f"tying up {_u(d['cash_impact_usd'])}. Top driver: {top[0]['customer']} (+{top[0]['dso_contribution_days']} "
            f"days, paying {top[0]['avg_days_late_last90']} days late vs {top[0]['avg_days_late_prior90']} before)."
            if top else f"DSO {d['dso_t1']} days")
    return _answer(engine, "dso_drivers", head, {"dso_t0": d["dso_t0"], "dso_t1": d["dso_t1"],
                                                 "change_days": d["change_days"], "cash_impact_usd": d["cash_impact_usd"]},
                   top, ["company.receivables", "company.counterparties", "model.ar_delay"], d["formula"], ["TP-6.1"],
                   ["Point-in-time DSO uses the ledger as known today (late postings may revise history)"],
                   reasoning=[f"DSO at {d['t0']} and {d['t1']} from the receivables ledger (point-in-time)",
                              "Change decomposed exactly into customer contributions (they sum to ΔDSO)",
                              "Payment behaviour compared: last 90 days vs prior 90 days; ML predicts open-invoice delays"],
                   impact=[{"label": "Cash tied up by the DSO increase", "value_usd": d["cash_impact_usd"],
                            "basis": "CALCULATED"}] + [{"label": f"{c['customer']}", "value_usd": c["cash_impact_usd"],
                                                        "basis": "CALCULATED"} for c in top[:3]],
                   metrics=["dso", "dso_change_90d", "dso_cash_impact"])


def excess_cash(engine) -> dict:
    fc = engine.forecast_summary()
    ents = {e["entity_id"]: e for e in engine.entities()}
    rows = sorted([{"entity_id": e["entity_id"], "entity": ents[e["entity_id"]]["name"],
                    "cash_usd": e["opening_usd"], "buffer_usd": e["buffer_usd"],
                    "excess_today_usd": e["opening_usd"] - e["buffer_usd"],
                    "excess_on_p10_path_usd": e["p10_trough_usd"] - e["buffer_usd"],
                    "capital_controls": ents[e["entity_id"]]["capital_controls"]} for e in fc["entities"]],
                  key=lambda r: -r["excess_on_p10_path_usd"])
    rows = [r for r in rows if r["excess_on_p10_path_usd"] > 0]
    free = sum(r["excess_on_p10_path_usd"] for r in rows if not r["capital_controls"])
    head = (f"{len(rows)} entities hold cash above their buffer even on the P10 path; {_u(free)} of it is in "
            f"unrestricted entities and can be concentrated. Largest: {rows[0]['entity']} ({_u(rows[0]['excess_on_p10_path_usd'])}).")
    return _answer(engine, "excess_cash", head, {"unrestricted_excess_usd": free}, rows,
                   ["company.bank_balances", "company.entities", "model.ar_delay", "market.fx"],
                   "Entity P10 trough over 13 weeks minus TP-3.1 buffer", ["TP-3.1", "TP-4.1", "CR-1"],
                   ["Controlled-entity cash is only movable within repatriation allowances"], metrics=["trapped_cash"])


def covenants(engine) -> dict:
    c = engine.covenants()
    first = c["covenants"][0]
    head = (f"{first['name']} ({first['clause_ref']}) is closest to breach: {first['value_today']} vs "
            f"{first['direction']} {first['threshold']} — {min(first['headroom_today_pct'], first['headroom_forward_pct']):.0%} "
            f"headroom today and on the P10 path. All {len(c['covenants'])} covenants are "
            f"{'compliant' if all(x['status'] == 'COMPLIANT' for x in c['covenants']) else 'NOT all compliant'}.")
    return _answer(engine, "covenants", head, {"ebitda_usd": c["ebitda_usd"]}, c["covenants"],
                   ["company.covenants", "company.debt_facilities", "company.facility", "company.bank_balances"],
                   "Covenant definitions applied today and to the P10 forecast path", ["RCF-22.1", "RCF-22.2", "RCF-22.3"],
                   ["LTM EBITDA is an input from the last compliance certificate, not recomputed"],
                   impact=[], metrics=["net_leverage", "covenant_headroom_min", "min_covenant_headroom_p10"])


def refinancing(engine) -> dict:
    d = engine.debt_profile()
    if not d["refinancing"]:
        head = "No facility matures inside the DP-2.1 refinancing window."
    else:
        r = d["refinancing"][0]
        head = (f"{r['name']} ({_u(r['amount_usd'])}) matures in {r['months_to_maturity']} months — inside the 18-month "
                f"DP-2.1 window. Refinancing as 5-year fixed is ESTIMATED at {r['estimated_new_5y_fixed_rate']:.2%} vs "
                f"{r['current_rate']:.2%} today ({_u(r['estimated_annual_cost_change_usd'])} a year).")
    return _answer(engine, "refinancing", head, {"gross_debt_usd": d["gross_debt_usd"]}, d["facilities"],
                   ["company.debt_facilities", "market.ust_curve", "market.credit_spread", "market.sofr"],
                   "Maturity ladder vs DP-2.1; new-issue cost = UST 5Y + BBB OAS + premium", ["DP-2.1", "DP-3.1", "DP-4.1"],
                   ["Indicative pricing only; actual spread depends on the issuer's rating and market access"],
                   assumptions=[r["assumption"] for r in d["refinancing"]],
                   impact=[{"label": f"Annual cost change if {r['facility_id']} is refinanced 5Y fixed",
                            "value_usd": r["estimated_annual_cost_change_usd"] or 0, "basis": "ESTIMATED"}
                           for r in d["refinancing"]], metrics=["refinancing_due_18m"])


def rate_exposure(engine) -> dict:
    d, fx = engine.debt_profile(), engine.fx_exposure()
    big = [c for c in fx["currencies"] if c["currency"] != "USD"][:3]
    head = (f"+100 bps on SOFR raises annual interest by {_u(d['rate_sensitivity_100bps_annual_usd'])} "
            f"({_u(d['floating_debt_usd'])} floating; fixed share {d['fixed_share']:.0%}, policy 40–75%). FX: 1-month 95% VaR "
            f"{_u(fx['var95_1m_undiversified_usd'])}, largest net exposure {big[0]['currency']} "
            f"{_u(big[0]['net_exposure_usd'])}.")
    return _answer(engine, "rate_exposure", head, {"rate_sensitivity_100bps_usd": d["rate_sensitivity_100bps_annual_usd"],
                                                   "fx_var95_1m_usd": fx["var95_1m_undiversified_usd"]}, fx["currencies"],
                   ["company.debt_facilities", "market.sofr", "market.fx_history", "company.bank_balances"],
                   fx["method"], ["DP-3.1"], ["VaR is undiversified and uses 90 days of realised volatility"],
                   impact=[{"label": "Annual interest +100 bps", "value_usd": d["rate_sensitivity_100bps_annual_usd"],
                            "basis": "CALCULATED"}, {"label": "FX 1-month 95% VaR",
                                                     "value_usd": fx["var95_1m_undiversified_usd"], "basis": "ESTIMATED"}],
                   metrics=["rate_sensitivity_100bps", "fx_var_1m"])


def liquidity_outlook(engine) -> dict:
    fc = engine.forecast_summary()
    pol = load_policy()
    mn = min(fc["liquidity_usd"]["p10"])
    wk = fc["liquidity_usd"]["p10"].index(mn)
    short = [e for e in fc["entities"] if e["shortfall_vs_buffer_usd"] > 0]
    head = (f"Lowest P10 liquidity over 13 weeks is {_u(mn)} (week {wk}), {_u(mn - fc['covenant_min_liquidity_usd'])} above "
            f"the RCF-22.1 covenant and {_u(mn - pol.liquidity.management_min_liquidity_usd)} above the TP-3.3 management "
            f"floor. {len(short)} entities fall below their buffer on the P10 path.")
    return _answer(engine, "liquidity_outlook", head, {"min_p10_liquidity_usd": mn,
                                                       "covenant_headroom_usd": mn - fc["covenant_min_liquidity_usd"]},
                   fc["entities"], ["company.bank_balances", "company.receivables", "company.payables", "company.recurring",
                                    "model.ar_delay", "market.sofr"],
                   "400-path Monte Carlo over ML-predicted payment dates", ["RCF-22.1", "TP-3.1", "TP-3.3"],
                   ["Excludes unplanned capex / M&A"], impact=[{"label": "Min P10 covenant headroom",
                                                                "value_usd": mn - fc["covenant_min_liquidity_usd"],
                                                                "basis": "FORECAST"}],
                   metrics=["min_covenant_headroom_p10", "funding_requirement"])


ANSWERS = {"liquidity_change": liquidity_change, "dso_drivers": dso_drivers, "excess_cash": excess_cash,
           "covenants": covenants, "refinancing": refinancing, "rate_exposure": rate_exposure,
           "liquidity_outlook": liquidity_outlook,
           "trapped_cash": trapped_cash, "liquidity_risk": liquidity_risk, "collections": collections,
           "releasable_liquidity": releasable_liquidity, "repay_vs_preserve": repay_vs_preserve, "shocks": shocks,
           "wc_actions": wc_actions, "cfo_approvals": cfo_approvals}


def answer(engine, topic: str) -> dict:
    return ANSWERS[topic](engine)
