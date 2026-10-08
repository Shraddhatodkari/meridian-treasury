"""Metric registry with lineage.

Every headline number shown in the console or used by an agent is a Metric:
  value + unit + basis (OBSERVED / CALCULATED / FORECAST / ESTIMATED / SCENARIO)
  + formula + the input datasets (with system, classification and as-of date) + the transformation steps
  + the analytical data product it belongs to.
"Where did this number come from?" is answered by GET /v1/metrics/{id}/lineage.
"""

from __future__ import annotations


def _m(mid, label, value, unit, basis, formula, inputs, steps, product, fmt=None) -> dict:
    return {"metric_id": mid, "label": label, "value": value, "unit": unit, "basis": basis, "formula": formula,
            "inputs": inputs, "steps": steps, "data_product": product, "display": fmt}


def build_metrics(e) -> dict[str, dict]:
    k = e.kpis()
    fc = e.forecast_summary()
    lw = e.liquidity_waterfall()
    debt = e.debt_profile()
    cov = e.covenants()
    dso = e.dso_drivers()
    ap = e.ap_analysis()
    fxe = e.fx_exposure()
    conc = e.concentration()
    opt = e.optimize()
    bt = e.backtest()
    fx_date = (e.market.fx or {}).get("rate_date", "simulation constants")
    sofr = e.market.sofr or {}
    worst_cov = cov["covenants"][0]
    funding_req = max(0.0, e.policy.liquidity.management_min_liquidity_usd - min(fc["liquidity_usd"]["p10"]))
    ms = [
        _m("group_cash", "Group cash", k["cash_usd"], "USD", "OBSERVED", "Σ closing booked balance × FX(USD per unit)",
           ["company.bank_balances", "market.fx"],
           [f"{k['bank_accounts']} closing balances (camt.053 CLBD)",
            f"Translate at ECB reference rates of {fx_date}", "Sum across accounts"], "analytical.cash_positions"),
        _m("available_liquidity", "Liquidity (cash + undrawn RCF)", k["liquidity_usd"], "USD", "CALCULATED",
           "group cash + (RCF limit − RCF drawn)", ["company.bank_balances", "market.fx", "company.facility"],
           ["Group cash (metric group_cash)", f"+ undrawn {e.g.facility.facility_id}"], "analytical.cash_positions"),
        _m("deployable_liquidity", "Deployable liquidity (incl. undrawn RCF)", lw["deployable_liquidity_usd"], "USD", "CALCULATED",
           "cash − trapped − operating buffers + undrawn RCF",
           ["company.bank_balances", "company.entities", "market.fx", "company.facility"],
           ["Liquidity waterfall (TP-3.1 buffers, CR-* allowances)"], "analytical.cash_positions"),
        _m("trapped_cash", "Trapped cash", lw["trapped_usd"], "USD", "CALCULATED",
           "Σ_controlled max(0, cash − buffer − repatriation allowance)", ["company.bank_balances", "company.entities"],
           ["Capital-controlled entities only", "Allowance per CR-* guidelines"], "analytical.cash_positions"),
        _m("min_covenant_headroom_p10", "Min. covenant headroom (P10, 13 wks)", fc["min_covenant_headroom_p10_usd"],
           "USD", "FORECAST", "min_w P10(liquidity_w) − covenant minimum (RCF-22.1)",
           ["company.bank_balances", "company.receivables", "company.payables", "company.recurring",
            "company.debt_facilities", "model.ar_delay", "market.fx", "market.sofr"],
           ["400-path Monte Carlo over ML payment dates", "P10 per week", "minus $250M covenant"],
           "analytical.liquidity_forecast"),
        _m("funding_requirement", "Funding requirement vs management minimum", round(funding_req), "USD", "FORECAST",
           "max(0, management minimum liquidity − min P10 liquidity)", ["analytical.liquidity_forecast",
                                                                        "reference.policies"],
           [f"Management minimum ${e.policy.liquidity.management_min_liquidity_usd:,.0f} (TP-3.3)"],
           "analytical.liquidity_forecast"),
        _m("week13_cash_p50", "Week-13 group cash (P50)", fc["group_cash_usd"]["p50"][-1], "USD", "FORECAST",
           "P50 of simulated week-13 closing cash", ["analytical.liquidity_forecast"], [], "analytical.liquidity_forecast"),
        _m("dso", "DSO", k["dso_days"], "days", "CALCULATED", "open AR / revenue of last 90 days × 90",
           ["company.receivables", "market.fx"], ["Open invoices at as-of", "Invoices issued in last 90 days"],
           "analytical.dso_bridge"),
        _m("dso_change_90d", "DSO change (90 days)", dso["change_days"], "days", "CALCULATED",
           "DSO(t1) − DSO(t0); contributions Σ_i (AR_i,t/S_t − AR_i,t0/S_t0) × 90",
           ["company.receivables", "company.counterparties"], [f"t0 {dso['t0']}  DSO {dso['dso_t0']}",
                                                               f"t1 {dso['t1']}  DSO {dso['dso_t1']}"],
           "analytical.dso_bridge"),
        _m("dso_cash_impact", "Cash tied up by DSO increase", dso["cash_impact_usd"], "USD", "CALCULATED",
           "ΔDSO × daily sales (last 90 days)", ["company.receivables"], [], "analytical.dso_bridge"),
        _m("dpo", "DPO", k["dpo_days"], "days", "CALCULATED", "open AP / purchases of last 90 days × 90",
           ["company.payables"], [], "analytical.ap_analysis"),
        _m("overdue_ar", "Overdue receivables", k["overdue_ar_usd"], "USD", "OBSERVED", "Σ open AR past due date",
           ["company.receivables", "market.fx"], [], "analytical.dso_bridge"),
        _m("ap_leakage_annual", "Supplier-payment leakage (annualised)",
           ap["annualised_missed_discounts_usd"] + ap["annualised_early_payment_cost_usd"], "USD", "ESTIMATED",
           "(missed discounts + financing cost of early payments) × 365 / history days",
           ["company.payables", "market.sofr"], [f"{ap['bills_paid']} paid bills over {ap['history_days']} days",
                                                 f"cost of funds {ap['cost_of_funds']:.2%} (revolver all-in)"],
           "analytical.ap_analysis"),
        _m("net_leverage", "Net leverage", next(c["value_today"] for c in cov["covenants"] if c["metric"] ==
                                                "max_net_leverage"), "x", "CALCULATED",
           "(gross debt − cash) / LTM EBITDA", ["company.debt_facilities", "company.facility", "company.bank_balances"],
           [], "analytical.covenant_tests"),
        _m("covenant_headroom_min", f"Closest covenant: {worst_cov['name']}",
           min(worst_cov["headroom_today_pct"], worst_cov["headroom_forward_pct"]), "ratio", "CALCULATED",
           worst_cov["formula"], ["company.covenants", "analytical.liquidity_forecast"], [worst_cov["clause_ref"]],
           "analytical.covenant_tests"),
        _m("revolver_rate", "Revolver all-in rate", e.g.facility.rate, "rate", "CALCULATED", "SOFR + margin",
           ["market.sofr", "company.facility"], [e.g.facility.rate_source], "analytical.debt_profile"),
        _m("rate_sensitivity_100bps", "Floating-rate sensitivity (+100 bps, annual)",
           debt["rate_sensitivity_100bps_annual_usd"], "USD", "CALCULATED", "floating drawn debt × 1%",
           ["company.debt_facilities"], [], "analytical.debt_profile"),
        _m("refinancing_due_18m", "Debt maturing inside refinancing window",
           sum(r["amount_usd"] for r in debt["refinancing"]), "USD", "OBSERVED",
           f"Σ drawn of facilities maturing within {e.policy.debt.refinancing_window_months} months (DP-2.1)",
           ["company.debt_facilities"], [], "analytical.debt_profile"),
        _m("fx_var_1m", "FX exposure — 1-month 95% VaR", fxe["var95_1m_undiversified_usd"], "USD", "ESTIMATED",
           "Σ |net exposure| × 1.645 × σ_annual × √(21/252)", ["company.bank_balances", "company.receivables",
                                                               "company.payables", "market.fx_history"],
           [fxe["method"]], "analytical.fx_exposure"),
        _m("bank_concentration_max", "Largest bank share of group cash", conc["banks"][0]["share"] if conc["banks"] else None,
           "ratio", "CALCULATED", "max_bank cash_bank / group cash", ["company.bank_balances", "company.banks"],
           [f"limit {conc['banks'][0]['limit_share']:.0%} (TP-5.2)" if conc["banks"] else ""], "analytical.concentration"),
        _m("optimizer_benefit", "Optimisation benefit (annualised)", opt["annualized_benefit_usd"], "USD", "CALCULATED",
           "(cost without transfers − optimal cost) × 52/13", ["analytical.liquidity_forecast", "company.entities"],
           [opt.get("objective", "")], "analytical.optimization"),
        _m("forecast_wape", "Collections forecast error (WAPE)", bt["wape_model"], "ratio", "CALCULATED",
           "Σ|forecast − actual| / Σ actual (13 weekly buckets)", ["company.receivables", "model.ar_delay"],
           [bt["method"], f"t0 {bt['t0']}"], "analytical.forecast_accuracy"),
        _m("sofr", "SOFR", sofr.get("sofr_pct"), "pct", "OBSERVED", "published rate", ["market.sofr"],
           [f"effective {sofr.get('effective_date')}"], "analytical.debt_profile"),
    ]
    return {m["metric_id"]: m for m in ms}
