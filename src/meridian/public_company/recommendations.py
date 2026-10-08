"""Evidence-gated recommendations for the public-company case (Meridian reference policy PC-*).

Each rule is evaluated deterministically on PUBLIC · REAL metrics. A recommendation is produced ONLY when the
policy trigger is met by the evidence; every rule that was evaluated but not triggered is reported too, so the
screen is never padded and never silently empty. All outputs are decision mandates — none creates a payment.
"""

from __future__ import annotations

from meridian.domain.models import ActionType, ProposedAction
from meridian.governance.policy import load_policy, route_approval
from meridian.public_company.analytics import PublicCompany, _usd

OWNER = {"REFINANCING": "CFO / Group Treasurer", "LIQUIDITY": "CFO", "WORKING_CAPITAL": "Working Capital Manager",
         "CAPITAL_ALLOCATION": "CFO", "RATE_RISK": "Group Treasurer"}


def evaluate(pc: PublicCompany, run_id: str, policy=None) -> dict:
    policy = policy or load_policy()
    P = policy.public_company
    M = pc.metrics
    v = lambda k: (M.get(k) or {}).get("value")  # noqa: E731
    recs, acts, rules = [], [], []
    trend = pc.trend()
    yoy = trend[-5] if len(trend) >= 5 else None
    now = trend[-1]

    def action(t: ActionType, amount: float, rationale: str) -> dict:
        a = ProposedAction(action_id=f"A{len(acts) + 1:03d}", type=t, amount_usd=round(amount), rationale=rationale,
                           to_entity=pc.subject["ticker"])
        role, ref = route_approval(a, policy=policy)
        a.required_approver, a.policy_refs = role, [ref]
        d = {**a.model_dump(mode="json"), "source_tool": "public_company", "case": "public"}
        acts.append(d)
        return d

    def rec(cat, title, decision, objective, constraints, impacts, timing, risks, confidence, evidence, policy_refs,
            act, priority, data_limits):
        n = len(recs) + 1
        recs.append({
            "rec_id": f"{run_id}:R{n:02d}", "short_id": f"R{n:02d}", "run_id": run_id, "case": "public",
            "scenario": "Base case (reported data)", "category": cat, "title": title, "decision": decision,
            "objective": objective, "constraints": constraints, "impact": impacts, "timing": timing, "risks": risks,
            "confidence": confidence, "confidence_label": "HIGH" if confidence >= 0.8 else "MEDIUM" if confidence >= 0.6
            else "LOW", "owner": OWNER[cat], "evidence": evidence, "policy_refs": policy_refs,
            "policy_version": policy.version, "approval_required": act["required_approver"] if act else None,
            "action_ids": [act["action_id"]] if act else [], "priority": priority, "payment_related": False,
            "status": "PROPOSED" if act else "OPEN", "data_classification": "PUBLIC_REAL",
            "data_limitations": data_limits,
            "nature": "DECISION MANDATE from public-filing evidence — not a transaction; never moves money"})
        if act:
            act["rec_id"] = recs[-1]["rec_id"]

    def imp(label, value, basis, formula):
        return {"label": label, "value_usd": round(value), "basis": basis, "formula": formula}

    def ev(*ids):
        return [{"type": "metric", "id": i, "label": M[i]["label"], "value": M[i]["value"], "basis": M[i]["basis"],
                 "period": M[i]["period"]} for i in ids if i in M]

    # ---------------------------------------------------------------- PC-2.1 refinancing readiness
    st, liq = v("st_obligations"), v("liquidity_fy")
    share = st / liq if st and liq else None
    triggered = share is not None and share > P.refinancing_trigger_share_of_liquidity
    rules.append({"rule": "PC-2.1 Refinancing readiness", "test": f"debt due ≤12m / liquidity = {share:.0%} vs trigger "
                  f"{P.refinancing_trigger_share_of_liquidity:.0%}" if share is not None else "inputs not available",
                  "triggered": triggered})
    if triggered:
        mat = pc.maturities()["rows"]
        a = action(ActionType.REFINANCING_READINESS_PLAN, st,
                   f"Refinancing readiness plan: {_usd(st)} of debt due within 12 months of {pc.debt_date} "
                   f"({M['st_obligations']['formula']})"
                   + (f"; following year {_usd(mat[1]['amount_usd'])}" if mat[1].get("amount_usd") else ""))
        rec("REFINANCING", f"Approve a refinancing-readiness plan for {_usd(st)} due within 12 months",
            f"Mandate Treasury to prepare issuance windows, funding mix (term debt / commercial paper / committed "
            f"facilities) and pre-funding options for {_usd(st)} maturing within 12 months of {pc.debt_date}",
            "Keep refinancing risk and funding-cost drift within policy while preserving liquidity coverage",
            [f"Liquidity coverage of 12-month debt stays ≥ {P.min_liquidity_coverage}x (PC-1.1); today "
             f"{v('liquidity_coverage')}x", f"Debt due ≤12m is {share:.0%} of liquidity (trigger "
             f"{P.refinancing_trigger_share_of_liquidity:.0%}, PC-2.1)",
             "Decision mandate only — no issuance or payment is executed by Meridian (PC-6.1)"],
            [imp("Debt due within 12 months", st, M["st_obligations"]["basis"], M["st_obligations"]["formula"]),
             *([imp("Annual cost of refinancing 12-month LTD maturities at today's market", v("refi_cost_delta"),
                    "ESTIMATED", M["refi_cost_delta"]["formula"])] if v("refi_cost_delta") is not None else []),
             imp("Annual cost of +100 bps on debt due within 12 months", v("repricing_100bps") or 0, "ESTIMATED",
                 M["repricing_100bps"]["formula"])],
            "Plan within 30 days; execution windows over the next 2–3 quarters",
            ["Market access and spreads can widen (credit-stress scenario: +150 bps)",
             *(["Consolidated debt includes captive-finance funding that rolls with the finance-receivable book"]
               if pc.captive else [])],
            0.85, ev("st_obligations", "short_term_borrowings", "maturity_y1", "liquidity_fy", "liquidity_coverage",
                     "avg_cost_of_debt", "refi_cost_delta", "repricing_100bps"),
            ["PC-2.1", "PC-1.1", "PC-6.1"], a, "P1",
            ["Quarterly 10-Q debt balances use company-specific XBRL elements (not in companyfacts): debt is as of the "
             "latest 10-K", "Issuer's own credit curve not used — Single-A index spread is a proxy"])

    # ---------------------------------------------------------------- PC-1.1 liquidity coverage
    cov = v("liquidity_coverage")
    triggered = cov is not None and cov < P.min_liquidity_coverage
    rules.append({"rule": "PC-1.1 Liquidity coverage", "test": f"coverage {cov}x vs minimum {P.min_liquidity_coverage}x"
                  if cov is not None else "inputs not available", "triggered": triggered})
    if triggered:
        a = action(ActionType.LIQUIDITY_POLICY_REVIEW, max(0.0, P.min_liquidity_coverage * st - liq),
                   f"Liquidity coverage {cov}x below {P.min_liquidity_coverage}x")
        rec("LIQUIDITY", f"Review liquidity: coverage {cov}x is below {P.min_liquidity_coverage}x",
            "Mandate a liquidity policy review (facility size, cash floor, CP back-up)", "Restore coverage to policy",
            [f"Coverage ≥ {P.min_liquidity_coverage}x (PC-1.1)"],
            [imp("Liquidity shortfall to policy coverage", P.min_liquidity_coverage * st - liq, "CALCULATED",
                 "min coverage × debt due ≤12m − liquidity")], "Within 30 days", ["Facility pricing"], 0.85,
            ev("liquidity_fy", "st_obligations", "liquidity_coverage"), ["PC-1.1", "PC-6.1"], a, "P1", [])

    # ---------------------------------------------------------------- PC-3.1 working capital (YoY, own history)
    def delta(k):
        return round(now[k] - yoy[k], 1) if yoy and now.get(k) is not None and yoy.get(k) is not None else None

    d_dso, d_dio = delta("dso"), delta("dio")
    if d_dso is not None or d_dio is not None:
        triggered = (d_dso or 0) >= P.wc_days_trigger or (d_dio or 0) >= P.wc_days_trigger
        parts_txt = [f"DSO {yoy['dso']}→{now['dso']} ({d_dso:+} d)" if d_dso is not None else "DSO n/a",
                     f"DIO {yoy['dio']}→{now['dio']} ({d_dio:+} d)" if d_dio is not None else "DIO n/a"]
        rules.append({"rule": "PC-3.1 Working-capital deterioration",
                      "test": f"{', '.join(parts_txt)} vs trigger +{P.wc_days_trigger} d", "triggered": triggered})
        if triggered:
            rev = v("revenue_ttm")
            cogs = v("cogs_ttm")
            parts = []
            cash_tied = 0.0
            if (d_dso or 0) >= P.wc_days_trigger:
                x = d_dso * rev / 365
                cash_tied += x
                parts.append(imp(f"Cash tied up by DSO increase ({d_dso:+} days YoY)", x, "ESTIMATED",
                                 "ΔDSO × revenue TTM / 365"))
            if (d_dio or 0) >= P.wc_days_trigger:
                x = d_dio * cogs / 365
                cash_tied += x
                parts.append(imp(f"Cash tied up by DIO increase ({d_dio:+} days YoY)", x, "ESTIMATED",
                                 "ΔDIO × COGS TTM / 365"))
            kd = v("avg_cost_of_debt")
            if kd:
                parts.append(imp("Annual financing cost of that cash at the implied cost of debt", cash_tied * kd,
                                 "ESTIMATED", "cash tied up × implied average cost of debt"))
            dso_hit = (d_dso or 0) >= P.wc_days_trigger
            what = "receivables" if dso_hit else "inventory"
            drv, dd_ = ("DSO", d_dso) if dso_hit else ("DIO", d_dio)
            a = action(ActionType.WORKING_CAPITAL_PROGRAM, cash_tied,
                       f"Working-capital review: {drv} {yoy[drv.lower()]}→{now[drv.lower()]} days ({yoy['end']}→{now['end']})")
            pe = pc.peers()
            cap = pc.captive
            art = "an" if what == "inventory" else "a"
            rec("WORKING_CAPITAL", f"Launch {art} {what} review: {drv} up {dd_} days year on year",
                f"Mandate {art} {what} review to explain and, where possible, reverse the {dd_}-day {drv} increase "
                f"({yoy['end']} → {now['end']}); target the {_usd(cash_tied)} tied up",
                "Release cash from working capital without harming customer and supplier relationships",
                [f"Measured against the company's own history first (PC-3.2); peer median {drv} "
                 f"{pe['median'][drv.lower() + '_days']} days is context only",
                 "Days use trailing-twelve-month denominators, so sales growth alone does not raise them"],
                parts, f"Diagnose in 30 days; measure {drv} quarterly",
                ["Customer and supplier terms are commercial decisions; tighter terms can affect sales"]
                + (["Trade receivables include dealer receivables, which differ from peers' trade receivables"] if cap else []),
                0.7, ev("dso" if dso_hit else "dio", "receivables_trade" if dso_hit else "inventory",
                        "revenue_ttm" if dso_hit else "cogs_ttm", "avg_cost_of_debt"),
                ["PC-3.1", "PC-3.2", "PC-6.1"], a, "P2",
                ["Ageing, customer/SKU concentration and terms are private (CLIENT · NOT CONFIGURED)"]
                + (["Revenue denominator includes financial-services revenues"] if cap else []))
    else:
        rules.append({"rule": "PC-3.1 Working-capital deterioration", "test": "insufficient quarterly history",
                      "triggered": False})

    # ---------------------------------------------------------------- PC-4.1 capital allocation
    sr, fcf = v("shareholder_returns_ttm"), v("fcf_ttm")
    triggered = sr is not None and fcf is not None and sr > fcf
    rules.append({"rule": "PC-4.1 Shareholder returns vs FCF", "test": f"returns TTM {_usd(sr)} vs FCF TTM {_usd(fcf)}"
                  if sr is not None and fcf is not None else "inputs not available", "triggered": triggered})
    if triggered:
        a = action(ActionType.CAPITAL_ALLOCATION_REVIEW, sr - fcf, f"Shareholder returns exceed FCF by {_usd(sr - fcf)}")
        rec("CAPITAL_ALLOCATION", f"Review capital allocation: returns exceed FCF by {_usd(sr - fcf)} (TTM)",
            "Mandate a capital-allocation review of repurchase pacing against FCF and liquidity",
            "Keep shareholder returns funded by free cash flow", ["PC-4.1"],
            [imp("Shareholder returns in excess of FCF (TTM)", sr - fcf, "CALCULATED", "returns TTM − FCF TTM")],
            "Next board cycle", ["Board authorisations are outside Treasury's mandate"], 0.8,
            ev("shareholder_returns_ttm", "fcf_ttm"), ["PC-4.1", "PC-6.1"], a, "P2", [])

    # ---------------------------------------------------------------- PC-5.1 rate exposure
    rp, eb = v("repricing_100bps"), v("ebitda_ttm")
    triggered = rp is not None and eb and rp / eb > P.repricing_trigger_share_of_ebitda
    rules.append({"rule": "PC-5.1 Repricing exposure", "test": f"+100 bps cost {_usd(rp)} = {rp / eb:.2%} of EBITDA TTM vs "
                  f"trigger {P.repricing_trigger_share_of_ebitda:.2%}" if rp is not None and eb else "inputs not available",
                  "triggered": bool(triggered)})
    if triggered:
        a = action(ActionType.RATE_RISK_REVIEW, rp, f"+100 bps on debt due ≤12m costs {_usd(rp)} a year")
        rec("RATE_RISK", f"Review rate-risk hedging: +100 bps costs {_usd(rp)} a year on debt due within 12 months",
            "Mandate a rate-risk review of the repricing profile and hedge ratios using internal debt and hedge data",
            "Bound the earnings impact of refinancing at higher rates",
            [f"Exposure {rp / eb:.2%} of EBITDA TTM exceeds the {P.repricing_trigger_share_of_ebitda:.2%} trigger (PC-5.1)",
             f"Disclosed derivative notional {_usd(v('derivative_notional'))} — hedge designation and direction are not "
             "available as structured data"],
            [imp("Annual cost of +100 bps on debt due within 12 months", rp, "ESTIMATED", M["repricing_100bps"]["formula"])],
            "Within one quarter", ["Hedge positions unknown from public data: the true exposure may be lower"],
            0.6, ev("repricing_100bps", "st_obligations", "ebitda_ttm", "derivative_notional"),
            ["PC-5.1", "PC-6.2", "PC-6.1"], a, "P3",
            ["Fixed/floating mix and hedge positions are NOT AVAILABLE FROM PUBLIC DATA (structured)"])

    return {"recommendations": recs, "actions": acts, "rules": rules}
