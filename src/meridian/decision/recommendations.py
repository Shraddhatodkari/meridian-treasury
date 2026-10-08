"""Decision layer: turn verified analytical evidence into governed recommendations.

A Recommendation is the unit a CFO / Treasurer reviews. It groups one or more proposed actions and states:
  decision · objective · constraints (incl. binding LP constraints) · quantified impact (each figure labelled
  OBSERVED / CALCULATED / FORECAST / ESTIMATED / SCENARIO) · timing · risks · confidence · owner ·
  evidence (metrics, datasets, tool-result hashes, agent findings) · policy clauses · approval requirement.

Recommendations are NOT transactions. Payment-type actions only become payment files after approval, and
files stop at READY — transmission requires a configured bank channel and a second authorised signatory.
"""

from __future__ import annotations

from meridian.domain.models import PAYMENT_ACTIONS

ROLE_RANK = {"analyst": 1, "wc_manager": 2, "treasurer": 2, "cfo": 3}
OWNER = {"CASH_CONCENTRATION": "Group Treasurer", "DEBT_REPAYMENT": "CFO", "SUPPLIER_DISCOUNTS": "Working Capital Manager",
         "COLLECTIONS": "Working Capital Manager", "REFINANCING": "CFO", "BANK_COUNTERPARTY": "Group Treasurer",
         "PAYMENT_DISCIPLINE": "Working Capital Manager"}


def _imp(label: str, value: float, basis: str, formula: str = "") -> dict:
    return {"label": label, "value_usd": round(value), "basis": basis, "formula": formula}


def _approver(actions: list[dict]) -> str | None:
    roles = [a["required_approver"] for a in actions if a.get("required_approver")]
    return max(roles, key=lambda r: ROLE_RANK.get(r, 0)) if roles else None


def derive_status(actions: list[dict]) -> str:
    if not actions:
        return "OPEN"
    st = {a["status"] for a in actions}
    if st <= {"EXECUTED"}:
        return "EXECUTED"
    if st <= {"PROPOSED"}:
        return "PROPOSED"
    if st <= {"REJECTED"}:
        return "REJECTED"
    if st <= {"READY", "TRANSMITTED"} or (st <= {"READY", "APPROVED", "TRANSMITTED"} and "READY" in st and all(
            a["status"] != "APPROVED" or a["type"] not in PAYMENT_ACTIONS for a in actions)):
        return "READY_FOR_EXECUTION"
    if st <= {"APPROVED", "READY", "TRANSMITTED", "REJECTED"} and st & {"APPROVED", "READY"}:
        return "APPROVED" if "REJECTED" not in st else "PARTIALLY_APPROVED"
    if "SUBMITTED" in st:
        return "IN_REVIEW"
    return "PARTIALLY_APPROVED"


def build_recommendations(run_id: str, scenario: str, ev: dict, actions: list[dict], findings: dict[str, dict],
                          policy_version: str, binding: list[dict]) -> list[dict]:
    by_type: dict[str, list[dict]] = {}
    for a in actions:
        by_type.setdefault(a["type"], []).append(a)
    opt, debt, cov = ev["optimization"], ev.get("debt") or {}, ev.get("covenants") or {}
    dso, ap, conc = ev.get("dso") or {}, ev.get("ap") or {}, ev.get("concentration") or {}
    recs: list[dict] = []

    def conf(agent: str) -> float:
        return round(float(findings.get(agent, {}).get("confidence", 0.7)), 2)

    def add(cat, title, decision, objective, constraints, impacts, timing, risks, confidence, evidence, policy, acts,
            priority):
        recs.append({
            "rec_id": f"{run_id}:R{len(recs) + 1:02d}", "short_id": f"R{len(recs) + 1:02d}", "run_id": run_id,
            "scenario": scenario, "category": cat, "title": title, "decision": decision, "objective": objective,
            "constraints": constraints, "impact": impacts, "timing": timing, "risks": risks,
            "confidence": confidence, "confidence_label": "HIGH" if confidence >= 0.8 else "MEDIUM" if confidence >= 0.6
            else "LOW", "owner": OWNER[cat], "evidence": evidence, "policy_refs": policy,
            "policy_version": policy_version, "approval_required": _approver(acts),
            "action_ids": [a["action_id"] for a in acts], "priority": priority,
            "payment_related": any(a["type"] in PAYMENT_ACTIONS for a in acts), "status": derive_status(acts),
            "nature": "RECOMMENDATION — not a transaction"})

    # ---- 1. cash concentration (intercompany sweeps / fundings) -------------------------------------------------
    tr = by_type.get("INTERCOMPANY_TRANSFER", [])
    if tr:
        short = [e for e in ev["forecast"]["entities"] if e["shortfall_vs_buffer_usd"] > 0]
        repay_saving = opt.get("annualized_interest_saving_from_repay_usd", 0)
        add("CASH_CONCENTRATION", f"Concentrate {len([a for a in tr if a['to_entity'] == 'E01'])} surpluses and fund "
            f"{len([a for a in tr if a['from_entity'] == 'E01'])} shortfalls through the in-house bank",
            f"Execute {len(tr)} intercompany transfers: sweep ${opt['sweep_total_usd']:,.0f} to Group Treasury and fund "
            f"${opt['funding_total_usd']:,.0f} to entities below buffer",
            opt.get("objective", ""),
            ["Every entity ≥ operating buffer on the P10 path (TP-3.1)", "No yield-arbitrage funding (TP-3.2)",
             "Upstreaming within repatriation allowances (CR-*)",
             *[f"BINDING: {c['constraint']}" + (f" — shadow price {c['shadow_price_per_usd_13w']:.2%} per $ over 13 wks"
                                                if c.get("shadow_price_per_usd_13w") else "") for c in binding[:4]]],
            [_imp("Annual benefit of optimal allocation (excl. revolver repayment)",
                  max(0, opt["annualized_benefit_usd"] - repay_saving), "CALCULATED",
                  "(cost without transfers − optimal cost) × 52/13 − revolver saving"),
             _imp("Entity shortfalls closed (P10)", sum(e["shortfall_vs_buffer_usd"] for e in short), "FORECAST",
                  "Σ max(0, buffer − P10 trough)")],
            "Value date T+1; review weekly", ["FX spread and transfer fees are standard assumptions (8 bps / 1 bps)",
                                              "Tax leakage on upstreaming requires Tax review"],
            conf("liquidity_agent"),
            [{"type": "metric", "id": "optimizer_benefit"}, {"type": "metric", "id": "trapped_cash"},
             {"type": "dataset", "id": "analytical.optimization"}, {"type": "dataset", "id": "analytical.liquidity_forecast"},
             {"type": "tool", "id": "optimize_liquidity"}, {"type": "finding", "id": "liquidity_agent"}],
            ["TP-3.1", "TP-3.2", "TP-4.1", "TP-2.1"], tr, "P1" if short else "P2")

    # ---- 2. debt repayment --------------------------------------------------------------------------------------
    rp = by_type.get("REVOLVER_REPAY", [])
    if rp:
        amt = rp[0]["amount_usd"]
        lev = next((c for c in cov.get("covenants", []) if c["metric"] == "max_net_leverage"), None)
        add("DEBT_REPAYMENT", f"Repay ${amt / 1e6:,.0f}M of the revolver with concentrated surplus cash",
            f"Prepay ${amt:,.0f} of {opt.get('revolver_rate_source', 'RCF')}-priced borrowings",
            "Reduce net interest cost without reducing covenant liquidity (repaid RCF is re-drawable)",
            [opt.get("revolver_repay_note", "RCF-8.1 rounding"), "Group Treasury keeps its $25M buffer on the P10 path",
             f"Liquidity unchanged: cash ↓ ${amt:,.0f}, undrawn RCF ↑ ${amt:,.0f} (RCF-22.1)",
             f"Net leverage unchanged at {lev['value_today']}x (cash and debt fall together)" if lev else
             "Net leverage unchanged"],
            [_imp("Annual interest saving", opt["annualized_interest_saving_from_repay_usd"], "CALCULATED",
                  "repayment × (revolver all-in rate − HoldCo deposit yield)"),
             _imp("Change in covenant liquidity", 0, "CALCULATED", "−cash + undrawn commitment")],
            "3 business days' notice (RCF-8.1)", ["If forecast deteriorates, re-draw adds back cost (no penalty)",
                                                  "Rate saving moves with SOFR"],
            conf("optimization_agent") if "optimization_agent" in findings else conf("liquidity_agent"),
            [{"type": "metric", "id": "revolver_rate"}, {"type": "metric", "id": "sofr"},
             {"type": "dataset", "id": "analytical.optimization"}, {"type": "dataset", "id": "market.sofr"},
             {"type": "tool", "id": "optimize_liquidity"}],
            ["RCF-8.1", "TP-4.1", "TP-2.1", "RCF-22.1"], rp, "P1")

    # ---- 3. supplier discounts --------------------------------------------------------------------------------
    ep = by_type.get("EARLY_PAY_DISCOUNT", [])
    if ep:
        e = ev["early_pay"]
        add("SUPPLIER_DISCOUNTS", f"Capture {len(e['selected'])} supplier early-payment discounts",
            f"Pay {len(e['selected'])} bills early (${e['cash_accelerated_usd']:,.0f}) to capture discounts",
            "Earn a return above the cost of funds without breaching entity buffers",
            [f"Implied annual return > cost of funds {e['cost_of_funds']:.2%} (TP-2.3)",
             "Paying entity stays above P10 buffer", f"{len(e['skipped_for_liquidity'])} attractive discounts skipped "
                                                     "for liquidity"],
            [_imp("Discount savings (this book)", e["total_saving_usd"], "CALCULATED", "Σ bill × discount %"),
             _imp("Cash paid earlier", -e["cash_accelerated_usd"], "CALCULATED", "Σ bills paid in discount window")],
            "Before each discount date", ["Supplier must accept early remittance"], conf("working_capital_agent"),
            [{"type": "dataset", "id": "company.payables"}, {"type": "tool", "id": "get_early_pay_opportunities"},
             {"type": "finding", "id": "working_capital_agent"}], ["TP-2.3", "PP-1.2"], ep, "P2")

    # ---- 4. collections acceleration (DSO drivers) ---------------------------------------------------------------
    co = by_type.get("COLLECTIONS_ESCALATION", [])
    if co or dso.get("change_days", 0) > 0:
        top = dso.get("by_customer", [])[:5]
        add("COLLECTIONS", f"Accelerate collections: DSO {dso.get('dso_t0')}→{dso.get('dso_t1')} days",
            f"Escalate {len(co)} high-risk invoices (TP-6.1) and run a collections sprint on the "
            f"{len(top)} customers driving the DSO increase",
            "Return DSO to its level of 90 days ago and reduce predicted payment delays",
            ["Escalations within 5 business days (TP-6.1)", "Credit holds require Sales & Credit sign-off"],
            [             _imp("Cash released if DSO returns to its level 90 days ago", dso.get("cash_impact_usd", 0), "ESTIMATED",
                  "ΔDSO × daily sales — assumes full reversal of the increase"),
             _imp("…of which attributable to the top-5 customers", min(dso.get("cash_impact_usd", 0),
                                                                       sum(max(0, c["cash_impact_usd"]) for c in top)),
                  "CALCULATED", "Σ top-5 customer DSO contributions × daily sales, capped at the total"),
             _imp("Escalated invoices (face value)", sum(a["amount_usd"] for a in co), "OBSERVED", "Σ invoice amounts")],
            "Start this week; measure DSO weekly", ["Disputed invoices need operational resolution, not dunning",
                                                    "Reversal assumption may be optimistic for distressed customers"],
            conf("working_capital_agent"),
            [{"type": "metric", "id": "dso_change_90d"}, {"type": "metric", "id": "dso_cash_impact"},
             {"type": "dataset", "id": "analytical.dso_bridge"}, {"type": "dataset", "id": "model.ar_delay"},
             {"type": "tool", "id": "get_collections_priorities"}], ["TP-6.1"], co,
            "P1" if dso.get("change_days", 0) >= 2 else "P2")

    # ---- 5. refinancing ---------------------------------------------------------------------------------------
    rf = by_type.get("REFINANCING_PLAN", [])
    for r_act in rf:
        r = next((x for x in debt.get("refinancing", []) if x["facility_id"] == r_act.get("to_entity")), None) or {}
        add("REFINANCING", f"Mandate refinancing of {r.get('name', r_act['to_entity'])}",
            f"Approve a refinancing plan for ${r_act['amount_usd']:,.0f} maturing {r.get('maturity_date')} "
            f"({r.get('months_to_maturity')} months)", "Remove the maturity cliff before it erodes liquidity headroom",
            [f"Launch ≥ 18 months before maturity (DP-2.1) — currently {r.get('months_to_maturity')} months",
             "Fixed share within 40–75% (DP-3.1)", "No more than 40% of debt maturing in 12 months (DP-4.1)"],
            [_imp("Debt maturing inside the window", r_act["amount_usd"], "OBSERVED", "facility drawn amount"),
             *([_imp("Annual cost change if refinanced as 5-year fixed", r["estimated_annual_cost_change_usd"],
                     "ESTIMATED", r["assumption"])] if r.get("estimated_annual_cost_change_usd") is not None else [])],
            "Mandate banks this quarter", ["Market access and spreads can move before launch",
                                           "Board approval needed above $250M (DP-1.1)"],
            conf("risk_agent"),
            [{"type": "metric", "id": "refinancing_due_18m"}, {"type": "dataset", "id": "analytical.debt_profile"},
             {"type": "dataset", "id": "market.ust_curve"}, {"type": "dataset", "id": "market.credit_spread"},
             {"type": "tool", "id": "get_debt_profile"}], ["DP-2.1", "DP-3.1", "TLA-9.1"], [r_act], "P1")

    # ---- 6. bank counterparty rebalancing ---------------------------------------------------------------------
    bk = by_type.get("BANK_REBALANCING_PLAN", [])
    if bk:
        b = next((x for x in conc.get("banks", []) if x["breach"]), {})
        add("BANK_COUNTERPARTY", f"Rebalance bank exposure: {b.get('bank')} holds {b.get('share', 0):.0%} of cash",
            f"Move ${b.get('excess_usd', 0):,.0f} from {b.get('bank')} to other relationship banks within 30 days",
            "Bring every bank within its TP-5.2 limit", [f"Single bank ≤ {b.get('limit_share', 0.4):.0%} of group cash",
                                                        "Banks rated below A- ≤ 15%"],
            [_imp("Excess exposure above limit", b.get("excess_usd", 0), "CALCULATED", "cash at bank − limit × group cash")],
            "Within 30 days (TP-5.2)", ["Account opening lead times at receiving banks"], 0.85,
            [{"type": "metric", "id": "bank_concentration_max"}, {"type": "dataset", "id": "analytical.concentration"}],
            ["TP-5.2"], bk, "P2")

    # ---- 7. payment discipline (advisory, no approval) --------------------------------------------------------
    if ap.get("annualised_missed_discounts_usd", 0) + ap.get("annualised_early_payment_cost_usd", 0) > 250_000:
        add("PAYMENT_DISCIPLINE", "Stop supplier-payment leakage (pay on terms, capture discounts)",
            "Enforce pay-on-terms in payment runs and add discount dates to the run calendar",
            "Recover missed discounts and stop paying early without a discount",
            ["PP-1.1 pay on terms", "PP-1.2 capture discounts above cost of funds"],
            [_imp("Missed discounts (annualised)", ap["annualised_missed_discounts_usd"], "ESTIMATED",
                  "missed discounts in history × 365 / history days"),
             _imp("Financing cost of early payments (annualised)", ap["annualised_early_payment_cost_usd"], "ESTIMATED",
                  "Σ amount × days early / 365 × cost of funds, annualised")],
            "Next payment run", ["Some early payments may reflect supplier-relationship decisions"],
            0.75, [{"type": "metric", "id": "ap_leakage_annual"}, {"type": "dataset", "id": "analytical.ap_analysis"}],
            ["PP-1.1", "PP-1.2"], [], "P3")
    return recs
