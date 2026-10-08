"""Deterministic offline reasoners — run in CI/demos without an API key, and act as the regression
baseline the Claude configuration must match on the evaluation gates."""

from __future__ import annotations

import re

from meridian.agents.schemas import AgentFinding, CFOBriefing, Plan


def usd(x: float) -> str:
    a = abs(x)
    s = "-" if x < 0 else ""
    if a >= 1e9:
        return f"{s}${a / 1e9:.2f}B"
    if a >= 1e7:
        return f"{s}${a / 1e6:.1f}M"
    if a >= 1e6:
        return f"{s}${a / 1e6:.2f}M"
    if a >= 1e3:
        return f"{s}${a / 1e3:.0f}K"
    return f"{s}${a:.0f}"


# ---------------------------------------------------------------------------------------- planner
_EU = ["DE", "FR", "NL", "PL"]


def plan(ev: dict) -> Plan:
    q = (ev.get("question") or "").lower()
    if not q:
        return Plan(intent="DAILY_RUN")
    p = Plan(intent="SCENARIO", scenario_name=ev["question"][:80])
    numeric_stress = re.search(r"\d+\s*(%|bps|basis|days?)", q)
    if re.search(r"\b(allowed|permitted|who (can )?approves?|policy|covenant say|rules?)\b", q) and not numeric_stress:
        return Plan(intent="POLICY_QUESTION", policy_query=ev["question"])
    if not numeric_stress and not re.search(r"cyclone|flood|hazard|disaster|earthquake|typhoon|storm", q):
        from meridian.analytics.insights import classify

        if topic := classify(q):
            return Plan(intent="INSIGHT", insight_topic=topic, scenario_name=ev["question"][:80])
    if m := re.search(r"(\d+(?:\.\d+)?)\s*%\s*(slower|later|longer|delay)", q):
        if re.search(r"\b(eu|europe|european)\b", q):
            p.country_delay_days = {c: round(float(m.group(1)) / 100 * 66, 1) for c in _EU}  # ≈ % of avg days-to-pay
        else:
            p.collection_delay_pct = float(m.group(1)) / 100
    if m := re.search(r"(\d+)\s*days?\s*(later|slower|delay)", q):
        p.country_delay_days = {c: float(m.group(1)) for c in (_EU if re.search(r"\b(eu|europe)", q) else [])} \
            or p.country_delay_days
        if not p.country_delay_days:
            p.collection_delay_pct = float(m.group(1)) / 66
    if m := re.search(r"(\d+)\s*(bps|basis points?)", q):
        p.rate_shock_bps = float(m.group(1))
    if m := re.search(r"(revenue|sales|billing|demand)[^.%]*?(drop|fall|decline|down|lower)s?\D*(\d+)\s*%", q):
        p.billing_change_pct_all = -float(m.group(3)) / 100
    for ccy, names in {"EUR": "eur|euro", "GBP": "gbp|sterling|pound", "CNY": "cny|yuan|rmb", "INR": "inr|rupee",
                       "BRL": "brl|real", "MXN": "mxn|peso"}.items():
        if m := re.search(rf"\b({names})\b[^.%]*?(fall|drop|weaken|depreciat|down)\w*\D*(\d+)\s*%", q):
            p.fx_shock_pct[ccy] = -float(m.group(3)) / 100
    if re.search(r"cyclone|flood|hazard|disaster|earthquake|typhoon|storm", q):
        p.include_hazard_events = True
    return p


# ---------------------------------------------------------------------------------------- specialists
def _conf(base: float, *penalties: tuple[bool, float]) -> float:
    c = base
    for cond, p in penalties:
        if cond:
            c -= p
    return round(max(0.05, min(1.0, c)), 2)


def forecast(ev: dict) -> AgentFinding:
    k, f = ev["kpis"], ev["forecast"]
    short = [e for e in f["entities"] if e["weeks_p50_below_buffer"] > 0]
    pts = [f"Group cash today {usd(k['cash_usd'])}; P50 week-13 group cash {usd(f['group_cash_usd']['p50'][-1])} "
           f"(P10 {usd(f['group_cash_usd']['p10'][-1])}).",
           f"Liquidity {usd(k['liquidity_usd'])} vs {usd(f['covenant_min_liquidity_usd'])} minimum (RCF-22.1); "
           f"lowest P10 headroom {usd(f['min_covenant_headroom_p10_usd'])}.",
           f"Net leverage {k['net_leverage_x']}x vs {k['max_net_leverage_x']}x maximum (RCF-22.2)."]
    risks = [f"{e['entity_id']} falls below its operating buffer in {e['weeks_p50_below_buffer']} week(s) "
             f"(P10 trough {usd(e['p10_trough_usd'])} vs buffer {usd(e['buffer_usd'])})" for e in short[:6]]
    if f["covenant_breach_week"] is not None:
        risks.insert(0, f"P10 liquidity breaches the covenant in week {f['covenant_breach_week']}")
    return AgentFinding(agent="forecast_agent", key_points=pts, risks=risks, policy_refs=["RCF-22.1", "TP-3.1"],
                        headline=f"{f['scenario']}: no covenant breach on the P10 path" if f["covenant_breach_week"]
                        is None else f"{f['scenario']}: covenant breach risk in week {f['covenant_breach_week']}",
                        recommendations=["Fund subsidiaries below buffer from Group Treasury"] if short else [],
                        confidence=_conf(0.85, (k.get("classification") == "SYNTHETIC", 0.0)),
                        limitations=["Excludes unplanned capex/M&A flows", "Weekly granularity"])


def optimization(ev: dict) -> AgentFinding:
    o, ents = ev["optimization"], {e["entity_id"]: e for e in ev["entities"]}
    sweeps = [t for t in o["transfers"] if t["kind"].startswith("sweep")]
    funds = [t for t in o["transfers"] if t["kind"].startswith("fund")]
    trapped = [e for e in ents.values() if e["capital_controls"]]
    pts = [f"Sweep {usd(o['sweep_total_usd'])} from {len(sweeps)} surplus subsidiaries to Group Treasury; fund "
           f"{usd(o['funding_total_usd'])} to {len(funds)} subsidiaries below buffer.",
           (f"Repay {usd(o['revolver_repay_usd'])} of the revolver ({o['revolver_repay_note']}), saving "
            f"{usd(o['annualized_interest_saving_from_repay_usd'])} a year at {o['revolver_rate']:.2%} "
            f"({o['revolver_rate_source']}).") if o["revolver_repay_usd"] > 0 else
           (f"No revolver repayment: the surplus is needed to keep entities above buffer on the P10 path "
            f"({o['revolver_repay_note']}); borrowing cost {o['revolver_rate']:.2%} ({o['revolver_rate_source']})."),
           f"Optimiser annualised benefit {usd(o['annualized_benefit_usd'])} versus leaving cash where it is."]
    risks = [f"{e['name']} holds {usd(e['balance_usd'])} under capital controls; only allowance-based upstreaming"
             for e in trapped if e["balance_usd"] > 5_000_000]
    headline = ("Concentrate surplus cash and pay down the revolver" if o["revolver_repay_usd"] > 0
                else "Rebalance cash to protect entity buffers; preserve revolver capacity")
    binding = [c for c in o.get("constraints", []) if c.get("binding") and c.get("shadow_price_per_usd_13w")]
    if binding:
        top = max(binding, key=lambda c: c["shadow_price_per_usd_13w"])
        pts.append(f"{len(binding)} policy constraints bind; the most expensive is '{top['constraint']}'.")
    return AgentFinding(agent="optimization_agent", headline=headline,
                        key_points=pts, risks=risks, policy_refs=["TP-4.1", "TP-3.2", "RCF-8.1", "CR-1", "CR-2"],
                        recommendations=[f"{t['kind'].capitalize()}: {t['from_entity']} → {t['to_entity']} "
                                         f"{usd(t['amount_usd'])}" for t in o["transfers"][:6]],
                        confidence=_conf(0.9, (o["status"] != "optimal", 0.6), (bool(o["residual_shortfalls"]), 0.2)),
                        limitations=["FX spread / transfer fee are standard assumptions", "Tax leakage needs Tax review"])


def liquidity(ev: dict) -> AgentFinding:
    ov = ev["overview"]
    w, b, c = ov["waterfall"], ov["bridge"], ov["concentration"]
    up = [d for d in b["drivers"] if d["value_usd"] > 0]
    down = [d for d in b["drivers"] if d["value_usd"] < 0]
    pts = [f"Group cash {usd(w['steps'][0]['value_usd'])}; deployable liquidity {usd(w['deployable_liquidity_usd'])} "
           f"after {usd(w['trapped_usd'])} trapped and {usd(w['buffers_usd'])} of operating buffers.",
           f"Cash moved from {usd(b['opening_usd'])} to {usd(b['closing_usd'])} over 8 weeks: largest inflow "
           f"{up[-1]['driver'] if up else 'n/a'} {usd(up[-1]['value_usd']) if up else ''}, largest outflow "
           f"{down[0]['driver'] if down else 'n/a'} {usd(down[0]['value_usd']) if down else ''}.",
           f"Covenant liquidity {usd(w['covenant_liquidity_usd'])} vs {usd(w['covenant_minimum_usd'])} minimum "
           f"(RCF-22.1) and {usd(w['management_minimum_usd'])} management floor (TP-3.3)."]
    risks = [f"{x['bank']} holds {x['share']:.0%} of group cash (limit {x['limit_share']:.0%}, TP-5.2)"
             for x in c["banks"] if x["breach"]]
    return AgentFinding(agent="liquidity_agent", headline="Where the cash is, what moved it, and what is usable",
                        key_points=pts, risks=risks, policy_refs=["TP-3.1", "TP-3.3", "TP-5.2", "RCF-22.1"],
                        recommendations=["Rebalance bank counterparty exposure"] if risks else [],
                        confidence=0.9, limitations=["Historical bridge covers bank-statement entries of the last 8 weeks"])


def data_intelligence(ev: dict) -> AgentFinding:
    prov, q = ev["provenance"], ev["quality"]
    failed = [p["source_id"] for p in prov if p.get("validation") == "FAILED"]
    stale = [p["source_id"] for p in prov if p.get("freshness") == "STALE"]
    cls = sorted({p["classification"] for p in prov})
    pts = [f"{len(prov)} sources registered ({', '.join(cls)}); {len(failed)} failed validation, {len(stale)} stale.",
           f"Data-quality gate: {q.get('errors', 0)} errors, {q.get('warnings', 0)} warnings on the latest company load "
           f"({q.get('mode') or ('loaded' if q.get('loaded') else 'not loaded')})."]
    if any(p["classification"] == "SYNTHETIC" for p in prov):
        pts.append("Company data is SYNTHETIC evaluation data — figures demonstrate the method, not a real company.")
    return AgentFinding(agent="data_intelligence_agent",
                        headline="Data fit for decision" if not failed else "Data quality issues — decisions degraded",
                        key_points=pts, risks=[f"{s} failed validation" for s in failed] + [f"{s} is stale" for s in stale],
                        policy_refs=["TP-7.1"], confidence=0.95 if not failed else 0.5,
                        limitations=["Validation covers schema, referential and business rules — not accounting accuracy"])


def scenario(ev: dict) -> AgentFinding:
    sc = ev["scenarios"]
    worst = min(sc, key=lambda s: s["min_covenant_headroom_p10_usd"])
    pts = [f"'{s['scenario']}': P10 headroom {usd(s['min_covenant_headroom_p10_usd'])}, week-13 cash change "
           f"{usd(s['week13_cash_change_vs_base_usd'])}." for s in sc[:4]]
    risks = [f"'{s['scenario']}' breaches the liquidity covenant in week {s['covenant_breach_week']}"
             for s in sc if s["covenant_breach_week"] is not None]
    return AgentFinding(agent="scenario_agent", headline=f"Worst scenario tested: {worst['scenario']}", key_points=pts,
                        risks=risks, policy_refs=["RCF-22.1", "TP-3.3"], confidence=0.8,
                        limitations=["Scenarios are independent unless combined explicitly"])


def risk(ev: dict) -> AgentFinding:
    evs, debt, cov = ev["events"], ev["debt"], ev["covenants"]
    material = [e for e in evs if e["severity"] in {"ORANGE", "RED"} or e.get("classification") == "SYNTHETIC"]
    c0 = cov["covenants"][0]
    pts = [f"Closest covenant: {c0['name']} at {c0['value_today']} vs {c0['threshold']} "
           f"({min(c0['headroom_today_pct'], c0['headroom_forward_pct']):.0%} headroom, {c0['clause_ref']}).",
           f"Floating debt {usd(debt['floating_debt_usd'])}: +100 bps costs {usd(debt['rate_sensitivity_100bps_annual_usd'])} "
           f"a year; fixed share {debt['fixed_share']:.0%} (DP-3.1 band 40–75%)."]
    pts += [f"{r['name']} ({usd(r['amount_usd'])}) matures in {r['months_to_maturity']} months — inside the DP-2.1 window."
            for r in debt["refinancing"]]
    pts += [f"{e['hazard'].replace('_', ' ').title()} in {e['country']} ({e['severity']}): open AR exposure "
            f"{usd(e['open_ar_exposure_usd'])}." for e in material[:3]]
    risks = [f"{r['risk_id']}: {r['title']}" for r in ev.get("register", [])[:5]]
    return AgentFinding(agent="risk_agent", headline="Funding, covenant, rate and event risks", key_points=pts,
                        risks=risks, policy_refs=["RCF-22.1", "RCF-22.2", "DP-2.1", "DP-3.1"],
                        recommendations=[f"Mandate refinancing of {r['name']}" for r in debt["refinancing"]],
                        confidence=0.8, limitations=["Hazard-to-cash translation uses planning defaults by alert level",
                                                     "Refinancing cost is an ESTIMATE from public curves"])


def decision(ev: dict) -> AgentFinding:
    recs = ev["recommendations"]
    pts = [f"{r['short_id']} [{r['priority']}] {r['title']} — approval: {r['approval_required'] or 'none (advisory)'}"
           for r in recs]
    excluded = ev.get("excluded", [])
    return AgentFinding(agent="decision_agent", headline=f"{len(recs)} recommendations from verified evidence",
                        key_points=pts, policy_refs=sorted({p for r in recs for p in r["policy_refs"]})[:10],
                        risks=[f"Excluded: {x}" for x in excluded],
                        confidence=round(min([r["confidence"] for r in recs], default=0.8), 2),
                        limitations=["Recommendations are not transactions; humans approve under TP-2.1"])


def governance(ev: dict) -> AgentFinding:
    checks = ev["checks"]
    failed = [c for c in checks if not c["passed"]]
    return AgentFinding(agent="governance_agent",
                        headline="All governance checks passed" if not failed else f"{len(failed)} governance check(s) failed",
                        key_points=[f"{'PASS' if c['passed'] else 'FAIL'} — {c['check']}: {c['detail']}" for c in checks],
                        risks=[c["detail"] for c in failed], policy_refs=["TP-2.1", "TP-2.2", "TP-7.1", "TP-7.2"],
                        confidence=1.0 if not failed else 0.4,
                        limitations=["Segregation of duties is enforced again at decision time, per user"])


def risk_legacy(ev: dict) -> AgentFinding:
    evs, sc = ev["events"], ev["scenarios"]
    material = [e for e in evs if e["severity"] in {"ORANGE", "RED"} or e.get("classification") == "SYNTHETIC"]
    pts = [f"{e['hazard'].replace('_', ' ').title()} in {e['country']} ({e['severity']}, "
           f"{'live GDACS' if e.get('classification') != 'SYNTHETIC' else 'scenario library'}): open AR exposure "
           f"{usd(e['open_ar_exposure_usd'])}." for e in material[:6]]
    green_live = sum(1 for e in evs if e not in material)
    if green_live:
        pts.append(f"{green_live} further GREEN (low-impact) live alerts monitored.")
    worst = min(sc, key=lambda s: s["min_covenant_headroom_p10_usd"])
    pts.append(f"Worst stress tested: '{worst['scenario']}' — P10 covenant headroom "
               f"{usd(worst['min_covenant_headroom_p10_usd'])}.")
    risks = [f"'{s['scenario']}' breaches the liquidity covenant in week {s['covenant_breach_week']}"
             for s in sc if s["covenant_breach_week"] is not None]
    thin = [s for s in sc if s["min_covenant_headroom_p10_usd"] < 100_000_000 and s["covenant_breach_week"] is None]
    risks += [f"'{s['scenario']}' leaves under $100M of covenant headroom" for s in thin]
    return AgentFinding(agent="risk_agent", headline="Hazard exposure and stress-test results", key_points=pts,
                        risks=risks, policy_refs=["RCF-22.1"],
                        recommendations=["Pre-agree temporary buffer increases for affected entities"] if material else [],
                        confidence=0.75, limitations=["Hazard-to-cash translation uses planning defaults by alert level"])


def working_capital(ev: dict) -> AgentFinding:
    col, ep = ev["collections"], ev["early_pay"]
    drv = ev.get("drivers") or {}
    esc = [c for c in col if c["predicted_days_late_p50"] > 45 or (c["disputed"] and c["amount_usd"] > 1_000_000)]
    pts = [f"Largest predicted delay: {col[0]['invoice_id']} ({col[0]['customer']}, {usd(col[0]['amount_usd'])}, "
           f"P50 {col[0]['predicted_days_late_p50']} days late)." if col else "No material collection risk.",
           f"{len(ep['selected'])} early-payment discounts beat the {ep['cost_of_funds']:.2%} cost of funds: "
           f"saving {usd(ep['total_saving_usd'])} on {usd(ep['cash_accelerated_usd'])} paid early (TP-2.3)."]
    if ep["skipped_for_liquidity"]:
        pts.append(f"{len(ep['skipped_for_liquidity'])} further discounts skipped: paying entity lacks P10 headroom.")
    if drv.get("dso"):
        d = drv["dso"]
        top = d["by_customer"][0] if d["by_customer"] else None
        pts.insert(0, f"DSO moved {d['dso_t0']} → {d['dso_t1']} days in 90 days ({usd(d['cash_impact_usd'])} of cash)"
                   + (f"; largest driver {top['customer']} (+{top['dso_contribution_days']} days)." if top else "."))
    if drv.get("ap"):
        a = drv["ap"]
        pts.append(f"Supplier-payment leakage: {usd(a['discounts_missed_usd'])} of discounts missed and "
                   f"{a['early_payments_without_discount']} early payments without discount (PP-1.1).")
    recs = [f"Escalate {c['invoice_id']} ({c['customer']}, {usd(c['amount_usd'])}): {c['recommended_action']}"
            for c in col[:5]]
    return AgentFinding(agent="working_capital_agent", headline="Collections focus and supplier discount capture",
                        key_points=pts, recommendations=recs, policy_refs=["TP-6.1", "TP-2.3", "PP-1.1"],
                        risks=[f"{len(esc)} invoice(s) meet the TP-6.1 escalation threshold"] if esc else [],
                        confidence=0.8, limitations=["Predicted delays are probabilistic (see P90)"])


def policy(ev: dict) -> AgentFinding:
    clauses = {c["clause_id"]: c for hits in ev["clauses"].values() for c in hits}
    pts = [f"{cid} ({c['clause_title']}): {c['text'][:160]}" for cid, c in list(clauses.items())[:6]]
    acts = ev["actions"]
    by_role: dict[str, int] = {}
    for a in acts:
        by_role[a["required_approver"]] = by_role.get(a["required_approver"], 0) + 1
    recs = [f"{n} action(s) require {role} approval" for role, n in sorted(by_role.items())]
    return AgentFinding(agent="policy_agent", headline="Approvals and constraints that apply", key_points=pts,
                        recommendations=recs, policy_refs=list(clauses)[:8],
                        risks=["AI may not approve or execute payments (TP-7.1)"], confidence=0.9,
                        limitations=["Clauses are retrieved; legal interpretation remains with Treasury/Legal"])


def briefing(ev: dict) -> CFOBriefing:
    k, f, o, ep = ev["kpis"], ev["forecast"], ev["optimization"], ev["early_pay"]
    acts = ev["actions"]
    cfo = [a for a in acts if a["required_approver"] == "cfo"]
    tre = [a for a in acts if a["required_approver"] == "treasurer"]
    summary = (f"Liquidity is {usd(k['liquidity_usd'])} against a {usd(f['covenant_min_liquidity_usd'])} covenant, "
               f"with P10 headroom never below {usd(f['min_covenant_headroom_p10_usd'])} over 13 weeks "
               f"({f['scenario']}). Cash is fragmented across {k['entities']} entities and {k['bank_accounts']} "
               + (f"accounts while {usd(o['revolver_repay_usd'])} of revolver debt can be repaid. "
                  if o["revolver_repay_usd"] > 0 else "accounts; no revolver repayment is recommended in this scenario. ") +
               f"The recommended plan yields {usd(o['annualized_benefit_usd'])} a year from cash concentration plus "
               f"{usd(ep['total_saving_usd'])} of supplier discounts.")
    nums = [f"Group cash {usd(k['cash_usd'])}; DSO {k['dso_days']} days; overdue AR {usd(k['overdue_ar_usd'])}",
            f"Trapped cash (capital controls) {usd(k['trapped_cash_usd'])}",
            f"P50 week-13 group cash {usd(f['group_cash_usd']['p50'][-1])}"]
    repay = (f" incl. revolver repayment of {usd(o['revolver_repay_usd'])} (TP-2.1, RCF-8.1)"
             if any(a["type"] == "REVOLVER_REPAY" for a in cfo) else " (TP-2.1)")
    decisions = [f"CFO: approve {len(cfo)} action(s){repay}"] if cfo else []
    if tre:
        decisions.append(f"Treasurer: approve {len(tre)} action(s) up to the TP-2.1 limits")
    for r in ev.get("recommendations", [])[:4]:
        main = next((i for i in r["impact"] if i["value_usd"]), None)
        if main:
            nums.append(f"{r['short_id']} {r['title']}: {main['label'].lower()} {usd(main['value_usd'])} ({main['basis']})")
    risks = [r for fd in ev["findings"] for r in fd["risks"]][:6]
    cites = sorted({c for fd in ev["findings"] for c in fd["policy_refs"]})
    return CFOBriefing(title=f"Treasury briefing — {f['scenario']} — as of {f['as_of']}", executive_summary=summary,
                       key_numbers=nums, decisions_required=decisions, risks=risks, citations=cites)
