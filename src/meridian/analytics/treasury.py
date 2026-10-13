"""Treasury & working-capital analytics on top of the normalized data layer.

Every function is deterministic, works identically on evaluation (synthetic) and client data, and labels each
output figure with its BASIS so nobody confuses an observation with an estimate:

  OBSERVED    read directly from a source record (bank balance, invoice amount, published rate)
  CALCULATED  arithmetic on observed data (DSO, net leverage, interest at current rates)
  FORECAST    model / Monte-Carlo output (13-week cash, P10 headroom)
  ESTIMATED   depends on an explicit assumption that is stated next to the figure
  SCENARIO    output under a user-defined stress
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from typing import Any, cast

import numpy as np

from meridian.data.enterprise import BANK_ID_BY_NAME, bu_of_segment
from meridian.data.generator import GroupData

RATING_ORDER = ["AAA", "AA+", "AA", "AA-", "A+", "A", "A-", "BBB+", "BBB", "BBB-", "BB+", "BB", "B"]


def _r(x: float) -> float:
    return float(round(x))


def _cash_by_entity(g: GroupData) -> dict[str, float]:
    out: dict[str, float] = defaultdict(float)
    for a in g.accounts:
        out[a.entity_id] += a.balance * g.fx.get(a.currency, 1.0)
    return dict(out)


def _local_by_entity(g: GroupData) -> dict[str, float]:
    out: dict[str, float] = defaultdict(float)
    for a in g.accounts:
        out[a.entity_id] += a.balance
    return dict(out)


# ================================================================================================ liquidity
def liquidity_waterfall(g: GroupData, fc_summary: dict, policy) -> dict:
    """From group cash to deployable liquidity, and the covenant / management floors it must stay above."""
    cash = _cash_by_entity(g)
    ents = {e.entity_id: e for e in g.entities}
    trapped = buffers = 0.0
    for eid, usd in cash.items():
        e = ents[eid]
        buf = min(usd, e.min_buffer_local * g.fx[e.currency])
        buffers += max(buf, 0.0)
        if e.capital_controls:
            trapped += max(0.0, usd - max(buf, 0.0) - e.repatriation_allowance_usd)
    total = sum(cash.values())
    f = g.rcf()
    undrawn = f.limit_usd - f.drawn_usd
    deployable_cash = total - trapped - buffers
    steps = [
        {"label": "Group cash", "value_usd": _r(total), "kind": "total", "basis": "OBSERVED",
         "note": "Closing bank balances translated at ECB reference rates"},
        {"label": "Trapped (capital controls)", "value_usd": -_r(trapped), "kind": "delta", "basis": "CALCULATED",
         "note": "Cash above buffer in controlled entities, beyond this quarter's repatriation allowance (CR-*)"},
        {"label": "Operating buffers", "value_usd": -_r(buffers), "kind": "delta", "basis": "CALCULATED",
         "note": "Minimum cash every entity must hold (TP-3.1)"},
        {"label": "Deployable cash", "value_usd": _r(deployable_cash), "kind": "subtotal", "basis": "CALCULATED"},
        {"label": "Undrawn RCF", "value_usd": _r(undrawn), "kind": "delta", "basis": "OBSERVED",
         "note": f"{f.facility_id}: limit {f.limit_usd:,.0f} − drawn {f.drawn_usd:,.0f}"},
        {"label": "Deployable liquidity", "value_usd": _r(deployable_cash + undrawn), "kind": "total",
         "basis": "CALCULATED"},
    ]
    covenant_liq = total + undrawn
    return {"steps": steps, "covenant_liquidity_usd": _r(covenant_liq),
            "covenant_minimum_usd": f.min_liquidity_covenant_usd,
            "management_minimum_usd": policy.liquidity.management_min_liquidity_usd,
            "headroom_today_usd": _r(covenant_liq - f.min_liquidity_covenant_usd),
            "min_forward_headroom_p10_usd": fc_summary["min_covenant_headroom_p10_usd"],
            "trapped_usd": _r(trapped), "buffers_usd": _r(buffers), "deployable_liquidity_usd": _r(deployable_cash + undrawn)}


def liquidity_bridge(g: GroupData, fx_history: dict | None, weeks: int = 8) -> dict:
    """Why did cash change? Bank-statement movements over the last `weeks`, by driver, plus FX translation
    computed from real ECB rates at the start of the window."""
    start = g.as_of - timedelta(days=7 * weeks)
    bal = _local_by_entity(g)
    ccy = {e.entity_id: e.currency for e in g.entities}
    net_local: dict[str, float] = defaultdict(float)
    cat_usd: dict[str, float] = defaultdict(float)
    weekly_net = np.zeros(weeks)
    for t in g.bank_transactions:
        if t.booking_date < start or t.booking_date >= g.as_of:
            continue
        net_local[t.entity_id] += t.amount
        usd = t.amount * g.fx.get(t.currency, 1.0)
        cat_usd[t.category] += usd
        weekly_net[min(weeks - 1, (t.booking_date - start).days // 7)] += usd
    fx_then = dict(g.fx)
    fx_source = "no FX history loaded — translation effect not separated"
    if fx_history and fx_history.get("usd_per_unit"):
        rows = [r for r in fx_history["usd_per_unit"] if r["date"] <= str(start)] or fx_history["usd_per_unit"][:1]
        then = rows[-1]
        fx_then = {c: then.get(c, g.fx.get(c, 1.0)) for c in g.fx}
        fx_then["USD"] = 1.0
        fx_source = f"ECB reference rates of {then['date']} vs {fx_history['usd_per_unit'][-1]['date']}"
    opening_local = {eid: bal.get(eid, 0.0) - net_local.get(eid, 0.0) for eid in ccy}
    opening_usd = sum(v * fx_then.get(ccy[eid], 1.0) for eid, v in opening_local.items())
    fx_effect = sum(v * (g.fx.get(ccy[eid], 1.0) - fx_then.get(ccy[eid], 1.0)) for eid, v in opening_local.items())
    closing = sum(v * g.fx.get(ccy[eid], 1.0) for eid, v in bal.items())
    labels = {"CUSTOMER_RECEIPT": "Customer receipts", "SUPPLIER_PAYMENT": "Supplier payments", "PAYROLL": "Payroll",
              "TAX": "Tax", "RENT": "Rent", "OPEX": "Operating expenses", "INTEREST": "Interest",
              "INTERCOMPANY": "Intercompany (net)"}
    drivers: list[dict[str, Any]] = [{"driver": labels.get(k, k), "category": k, "value_usd": _r(v)} for k, v in cat_usd.items()
               if abs(v) >= 1 and k != "INTERCOMPANY"]
    ic = cat_usd.get("INTERCOMPANY", 0.0)
    if abs(ic) >= 1:
        drivers.append({"driver": labels["INTERCOMPANY"], "category": "INTERCOMPANY", "value_usd": _r(ic)})
    drivers.append({"driver": "FX translation", "category": "FX", "value_usd": _r(fx_effect)})
    drivers.sort(key=lambda d: d["value_usd"])
    series, run = [], closing
    for w in range(weeks - 1, -1, -1):
        series.append({"week_ending": str(start + timedelta(days=7 * (w + 1))), "cash_usd": _r(run)})
        run -= weekly_net[w]
    series.reverse()
    return {"window_start": str(start), "window_end": str(g.as_of), "opening_usd": _r(opening_usd),
            "closing_usd": _r(closing), "change_usd": _r(closing - opening_usd), "drivers": drivers,
            "weekly_closing": series, "fx_basis": fx_source, "transactions": sum(1 for t in g.bank_transactions
                                                                                  if start <= t.booking_date < g.as_of),
            "basis": "OBSERVED (bank-statement entries) + CALCULATED (FX translation)"}


def forward_bridge(fc_summary: dict) -> dict:
    flows = fc_summary["flows_p50_usd"]
    opening = fc_summary["group_cash_usd"]["p50"][0]
    names = {"AR_open": "Collections — open invoices (ML timing)", "AR_new": "Collections — new billing",
             "AP": "Supplier payments", "Payroll/Rent/Opex/Tax": "Payroll, rent, opex, tax", "Interest": "Interest"}
    drivers = [{"driver": names.get(k, k), "value_usd": _r(sum(v))} for k, v in flows.items()]
    expected = opening + sum(d["value_usd"] for d in drivers)
    return {"opening_usd": _r(opening), "drivers": drivers, "expected_close_usd": _r(expected),
            "p10_close_usd": fc_summary["group_cash_usd"]["p10"][-1], "p50_close_usd": fc_summary["group_cash_usd"]["p50"][-1],
            "p90_close_usd": fc_summary["group_cash_usd"]["p90"][-1], "basis": "FORECAST (Monte-Carlo, expected flows)"}


def concentration(g: GroupData, policy) -> dict:
    cash = sum(a.balance * g.fx[a.currency] for a in g.accounts)
    banks = {b.name: b for b in g.banks}
    by_bank: dict[str, float] = defaultdict(float)
    accts: dict[str, int] = defaultdict(int)
    for a in g.accounts:
        by_bank[a.bank] += a.balance * g.fx[a.currency]
        accts[a.bank] += 1
    floor = RATING_ORDER.index(policy.liquidity.bank_min_rating)
    rows = []
    for name, usd in sorted(by_bank.items(), key=lambda kv: -kv[1]):
        b = banks.get(name)
        rating = b.credit_rating if b else "NR"
        low = rating not in RATING_ORDER or RATING_ORDER.index(rating) > floor
        limit = policy.liquidity.bank_low_rating_max_share if low else policy.liquidity.bank_max_share
        share = usd / cash if cash else 0
        rows.append({"bank_id": BANK_ID_BY_NAME.get(name, name), "bank": name, "rating": rating, "accounts": accts[name],
                     "cash_usd": _r(usd), "share": round(share, 4), "limit_share": limit,
                     "excess_usd": _r(max(0.0, usd - limit * cash)), "breach": share > limit})
    ent = _cash_by_entity(g)
    shares = [v / cash for v in ent.values() if cash]
    hhi = sum(s * s for s in shares)
    return {"banks": rows, "entity_hhi": round(hhi, 4), "effective_entities": round(1 / hhi, 1) if hhi else None,
            "largest_entity_share": round(max(shares), 4) if shares else None, "policy_ref": "TP-5.2"}


def currency_exposure(g: GroupData, fx_history: dict | None) -> dict:
    vols = (fx_history or {}).get("volatility", {})
    rows: defaultdict[str, dict[str, float]] = defaultdict(lambda: {"cash": 0.0, "ar": 0.0, "ap": 0.0})
    for a in g.accounts:
        rows[a.currency]["cash"] += a.balance * g.fx[a.currency]
    for r in g.receivables:
        if r.settled_date is None:
            rows[r.currency]["ar"] += r.amount * g.fx[r.currency]
    for p in g.open_payables():
        rows[p.currency]["ap"] += p.amount * g.fx[p.currency]
    out, total_var = [], 0.0
    z, horizon = 1.645, np.sqrt(21 / 252)
    for c, v in sorted(rows.items(), key=lambda kv: -(kv[1]["cash"] + kv[1]["ar"])):
        net = v["cash"] + v["ar"] - v["ap"]
        vol = vols.get(c, {}).get("annualised_vol", 0.0) if c != "USD" else 0.0
        var = z * vol * horizon * abs(net) if c != "USD" else 0.0
        total_var += var
        out.append({"currency": c, "cash_usd": _r(v["cash"]), "open_ar_usd": _r(v["ar"]), "open_ap_usd": _r(v["ap"]),
                    "net_exposure_usd": _r(net), "annualised_vol": vol,
                    "change_90d_pct": vols.get(c, {}).get("change_90d_pct"), "var95_1m_usd": _r(var)})
    return {"currencies": out, "var95_1m_undiversified_usd": _r(total_var),
            "method": "Net exposure (cash + open AR − open AP) × 1.645 × ECB 90-day realised vol × √(21/252); "
                      "undiversified (sum), so it overstates diversified risk",
            "vol_source": (fx_history or {}).get("from") and f"ECB {cast(dict, fx_history)['from']}..{cast(dict, fx_history)['to']}"}


# ================================================================================================ debt & covenants
def debt_profile(g: GroupData, market, policy) -> dict:
    sofr = (market.sofr or {}).get("sofr_pct") if market else None
    as_of = g.as_of
    rows: list[dict[str, Any]] = []
    ladder: defaultdict[int, float] = defaultdict(float)
    total = floating = interest = 0.0
    for f in g.debt_facilities:
        rate = f.rate
        src = f.rate_source
        if f.rate_type == "FLOATING" and sofr is not None and f.margin_bps is not None:
            rate = sofr / 100 + f.margin_bps / 10_000
            src = f"SOFR {sofr:.2f}% ({market.sofr['effective_date']}, NY Fed) + {f.margin_bps:.0f} bps"
        months = (f.maturity_date.year - as_of.year) * 12 + f.maturity_date.month - as_of.month
        annual = f.drawn_usd * rate
        total += f.drawn_usd
        floating += f.drawn_usd if f.rate_type == "FLOATING" else 0.0
        interest += annual
        ladder[f.maturity_date.year] += f.drawn_usd
        rows.append({**f.model_dump(mode="json"), "rate": round(rate, 6), "rate_source": src,
                     "annual_interest_usd": _r(annual), "months_to_maturity": months,
                     "undrawn_usd": _r(f.limit_usd - f.drawn_usd) if f.type == "RCF" else 0,
                     "in_refinancing_window": months <= policy.debt.refinancing_window_months})
    curve = (getattr(market, "ust_curve", None) or {}).get("curve", []) if market else []
    spread = ((getattr(market, "credit_spread", None) or {}).get("latest") or {}).get("value") if market else None
    refis = []
    for row in rows:
        if not row["in_refinancing_window"] or row["type"] == "RCF":
            continue
        ust5 = next((c["yield_pct"] for c in curve if c["tenor"] == "5 Yr"), None)
        est = None
        if ust5 is not None and spread is not None:
            est = (ust5 + spread) / 100 + policy.debt.new_issue_premium_bps / 10_000
        refis.append({"facility_id": row["facility_id"], "name": row["name"], "amount_usd": row["drawn_usd"],
                      "maturity_date": row["maturity_date"], "months_to_maturity": row["months_to_maturity"],
                      "current_rate": row["rate"], "estimated_new_5y_fixed_rate": round(est, 5) if est else None,
                      "estimated_annual_cost_change_usd": _r(row["drawn_usd"] * (est - row["rate"])) if est else None,
                      "basis": "ESTIMATED",
                      "assumption": (f"New 5-year fixed issue priced at U.S. Treasury 5Y ({ust5}% on "
                                     f"{market.ust_curve['curve_date']}) + ICE BofA BBB OAS ({spread}%) + "
                                     f"{policy.debt.new_issue_premium_bps:.0f} bps new-issue premium (policy assumption)")
                      if est else "No market curve loaded", "policy_ref": "DP-2.1"})
    fixed_share = 1 - floating / total if total else None
    return {"facilities": rows, "gross_debt_usd": _r(total), "floating_debt_usd": _r(floating),
            "fixed_share": round(fixed_share, 4) if fixed_share is not None else None,
            "fixed_share_policy": [policy.debt.fixed_share_min, policy.debt.fixed_share_max],
            "fixed_share_compliant": fixed_share is not None and policy.debt.fixed_share_min <= fixed_share
            <= policy.debt.fixed_share_max,
            "annual_interest_usd": _r(interest), "weighted_avg_rate": round(interest / total, 5) if total else None,
            "maturity_ladder": [{"year": y, "amount_usd": _r(v)} for y, v in sorted(ladder.items())],
            "refinancing": refis,
            "rate_sensitivity_100bps_annual_usd": _r(floating * 0.01),
            "rate_sensitivity_100bps_13w_usd": _r(floating * 0.01 * 13 / 52),
            "ust_curve": curve, "ust_curve_date": (getattr(market, "ust_curve", None) or {}).get("curve_date"),
            "credit_spread_bbb_pct": spread}


def covenant_tests(g: GroupData, kpis: dict, fc_summary: dict, debt: dict, policy, ebitda_change_pct: float = 0.0) -> dict:
    ebitda = g.rcf().ltm_ebitda_usd * (1 + ebitda_change_pct)
    cash = kpis["cash_usd"]
    undrawn = g.rcf().limit_usd - g.rcf().drawn_usd
    liq_today = cash + undrawn
    liq_min_fwd = min(fc_summary["liquidity_usd"]["p10"])
    cash_min_fwd = min(fc_summary["group_cash_usd"]["p10"])
    gross = debt["gross_debt_usd"]
    values = {"min_liquidity": (liq_today, liq_min_fwd),
              "max_net_leverage": ((gross - cash) / ebitda, (gross - cash_min_fwd) / ebitda),
              "min_interest_cover": (ebitda / debt["annual_interest_usd"], ebitda / debt["annual_interest_usd"])}
    out = []
    for c in g.covenants:
        now, fwd = values[c.metric]

        def head(v: float, c=c) -> float:
            return (v - c.threshold) / c.threshold if c.direction == ">=" else (c.threshold - v) / c.threshold

        worst = min(head(now), head(fwd))
        status = "BREACH" if worst < 0 else ("WATCH" if worst < policy.liquidity.covenant_watch_headroom else "COMPLIANT")
        unit = "USD" if c.metric == "min_liquidity" else "x"
        out.append({**c.model_dump(), "value_today": round(now, 2 if unit == "x" else 0),
                    "value_forward_worst": round(fwd, 2 if unit == "x" else 0), "unit": unit,
                    "headroom_today_pct": round(head(now), 4), "headroom_forward_pct": round(head(fwd), 4),
                    "status": status,
                    "formula": {"min_liquidity": "cash + undrawn RCF (forward: P10 liquidity path minimum)",
                                "max_net_leverage": "(gross debt − cash) / LTM EBITDA (forward: P10 cash trough)",
                                "min_interest_cover": "LTM EBITDA / annual interest at current rates"}[c.metric],
                    "basis": "CALCULATED today; FORECAST forward (P10)"})
    out.sort(key=lambda r: min(r["headroom_today_pct"], r["headroom_forward_pct"]))
    return {"covenants": out, "closest_to_breach": out[0]["covenant_id"] if out else None, "ebitda_usd": _r(ebitda),
            "ebitda_basis": "OBSERVED (facility compliance certificate input; LTM)" if not ebitda_change_pct else "SCENARIO"}


# ================================================================================================ working capital
def _dso_at(g: GroupData, d: date, days: int = 90):
    ar_by: dict[str, float] = defaultdict(float)
    rev = 0.0
    for r in g.receivables:
        usd = r.amount * g.fx[r.currency]
        if d - timedelta(days=days) < r.issue_date <= d:
            rev += usd
        if r.issue_date <= d and (r.settled_date is None or r.settled_date > d):
            ar_by[r.invoice_id] = usd
    return ar_by, rev


def dso_drivers(g: GroupData, model=None, prior_days: int = 90, days: int = 90) -> dict:
    """Driver-based DSO bridge between t0 = as-of − prior_days and today. Contributions sum exactly to the change:
    ΔDSO = Σ_i (AR_i,t / S_t − AR_i,t0 / S_t0) × days, grouped by customer, entity, business unit, aging bucket."""
    t1, t0 = g.as_of, g.as_of - timedelta(days=prior_days)
    inv = {r.invoice_id: r for r in g.receivables}
    custs = {c.customer_id: c for c in g.customers}
    ents = {e.entity_id: e for e in g.entities}
    ar1, s1 = _dso_at(g, t1, days)
    ar0, s0 = _dso_at(g, t0, days)

    def bucket(r, d):
        od = (d - r.due_date).days
        return "current" if od <= 0 else "1-30" if od <= 30 else "31-60" if od <= 60 else "61-90" if od <= 90 else "90+"

    groups: dict[str, defaultdict[str, float]] = {k: defaultdict(float) for k in ("customer", "entity", "business_unit", "aging")}
    for ar, s, sign, d in ((ar1, s1, 1, t1), (ar0, s0, -1, t0)):
        for iid, usd in ar.items():
            r = inv[iid]
            c = custs[r.customer_id]
            contrib = sign * usd / s * days
            groups["customer"][r.customer_id] += contrib
            groups["entity"][r.entity_id] += contrib
            groups["business_unit"][bu_of_segment(c.segment)] += contrib
            groups["aging"][bucket(r, d)] += contrib
    dso1, dso0 = sum(ar1.values()) / s1 * days, sum(ar0.values()) / s0 * days
    daily_sales = s1 / days

    # payment behaviour: average days late of invoices settled in the last 90 days vs the 90 days before
    late_now, late_prev = defaultdict(list), defaultdict(list)
    for r in g.receivables:
        if r.settled_date and t1 - timedelta(days=90) <= r.settled_date < t1:
            late_now[r.customer_id].append((r.settled_date - r.due_date).days)
        elif r.settled_date and t0 - timedelta(days=90) <= r.settled_date < t0:
            late_prev[r.customer_id].append((r.settled_date - r.due_date).days)
    pred: dict[str, list[float]] = defaultdict(list)
    if model is not None:
        open_ar = [r for r in g.receivables if r.settled_date is None]
        p = model.predict(open_ar, g.as_of)
        for r, p50 in zip(open_ar, p.p50, strict=True):
            pred[r.customer_id].append(float(p50))
    bu_names = {b.bu_id: b.name for b in g.business_units}
    top_c = sorted(groups["customer"].items(), key=lambda kv: -kv[1])[:15]
    customers = []
    for cid, contrib in top_c:
        c = custs[cid]
        open_usd = sum(r.amount * g.fx[r.currency] for r in g.receivables if r.customer_id == cid and r.settled_date is None)
        overdue = sum(r.amount * g.fx[r.currency] for r in g.receivables if r.customer_id == cid and r.settled_date is None
                      and r.due_date < g.as_of)
        ln, lp = late_now.get(cid, []), late_prev.get(cid, [])
        customers.append({
            "customer_id": cid, "customer": c.name, "rating": c.credit_rating, "segment": c.segment,
            "business_unit": bu_names.get(bu_of_segment(c.segment)), "dso_contribution_days": round(contrib, 2),
            "cash_impact_usd": _r(contrib * daily_sales), "open_ar_usd": _r(open_usd), "overdue_usd": _r(overdue),
            "avg_days_late_last90": round(float(np.mean(ln)), 1) if ln else None,
            "avg_days_late_prior90": round(float(np.mean(lp)), 1) if lp else None,
            "predicted_days_late_p50": round(float(np.mean(pred[cid])), 1) if pred.get(cid) else None})
    rest = sum(groups["customer"].values()) - sum(v for _, v in top_c)
    n_rest = len(groups["customer"]) - len(top_c)
    if n_rest > 0:
        customers.append({"customer_id": None, "customer": f"All other customers ({n_rest}, net)", "rating": "", "segment": "",
                          "business_unit": None, "dso_contribution_days": round(rest, 2), "cash_impact_usd": _r(rest * daily_sales),
                          "open_ar_usd": None, "overdue_usd": None, "avg_days_late_last90": None, "avg_days_late_prior90": None,
                          "predicted_days_late_p50": None, "is_remainder": True})

    def rows(name: str, label=lambda k: k) -> list[dict]:
        return [{"key": k, "label": label(k), "dso_contribution_days": round(v, 2), "cash_impact_usd": _r(v * daily_sales)}
                for k, v in sorted(groups[name].items(), key=lambda kv: -kv[1])]

    cohorts: dict[str, float] = defaultdict(float)
    for iid, usd in ar1.items():
        cohorts[inv[iid].issue_date.strftime("%Y-%m")] += usd
    trend = []
    for m in range(12, -1, -1):
        d = g.as_of - timedelta(days=30 * m)
        a, s = _dso_at(g, d, days)
        trend.append({"date": str(d), "dso_days": round(sum(a.values()) / s * days, 1) if s else None})
    return {"t0": str(t0), "t1": str(t1), "dso_t0": round(dso0, 1), "dso_t1": round(dso1, 1),
            "change_days": round(dso1 - dso0, 1), "daily_sales_usd": _r(daily_sales),
            "cash_impact_usd": _r((dso1 - dso0) * daily_sales),
            "by_customer": customers,
            "by_entity": rows("entity", lambda k: f"{k} {ents[k].name}"),
            "by_business_unit": rows("business_unit", lambda k: bu_names.get(k, k)),
            "by_aging_bucket": rows("aging"),
            "open_ar_by_issue_month": [{"month": k, "open_ar_usd": _r(v)} for k, v in sorted(cohorts.items())][-8:],
            "trend": trend,
            "formula": "DSO = open AR / revenue of the last 90 days × 90; ΔDSO = Σ_i (AR_i,t/S_t − AR_i,t0/S_t0) × 90",
            "basis": "CALCULATED from the receivables ledger (point-in-time); cash impact = ΔDSO × daily sales"}


def ap_analysis(g: GroupData, cost_of_funds: float) -> dict:
    paid = g.paid_payables()
    disc_avail = disc_taken = disc_missed = 0.0
    early_amt = early_cost = 0.0
    early_n = late_n = 0
    late_amt = 0.0
    by_sup: dict[str, dict] = defaultdict(lambda: {"missed_discount_usd": 0.0, "early_no_discount_usd": 0.0,
                                                   "early_financing_cost_usd": 0.0})
    for p in paid:
        usd = p.amount * g.fx[p.currency]
        paid_date = cast(date, p.paid_date)          # paid_payables() only returns settled bills
        window = p.issue_date + timedelta(days=p.discount_days)
        if p.discount_pct:
            disc_avail += usd * p.discount_pct
            if paid_date <= window:
                disc_taken += usd * p.discount_pct
                continue
            disc_missed += usd * p.discount_pct
            by_sup[p.supplier_id]["missed_discount_usd"] += usd * p.discount_pct
        days_early = (p.due_date - paid_date).days
        if days_early >= 5:
            early_n += 1
            early_amt += usd
            cost = usd * days_early / 365 * cost_of_funds
            early_cost += cost
            by_sup[p.supplier_id]["early_no_discount_usd"] += usd
            by_sup[p.supplier_id]["early_financing_cost_usd"] += cost
        elif days_early < -2:
            late_n += 1
            late_amt += usd
    span = max(1, (g.as_of - min((p.issue_date for p in paid), default=g.as_of)).days)
    ann = 365 / span
    sups = {s.supplier_id: s for s in g.suppliers}
    top = sorted(by_sup.items(), key=lambda kv: -(kv[1]["missed_discount_usd"] + kv[1]["early_financing_cost_usd"]))[:12]
    return {"history_days": span, "bills_paid": len(paid),
            "discounts_available_usd": _r(disc_avail), "discounts_captured_usd": _r(disc_taken),
            "discounts_missed_usd": _r(disc_missed),
            "capture_rate": round(disc_taken / disc_avail, 4) if disc_avail else None,
            "early_payments_without_discount": early_n, "early_paid_usd": _r(early_amt),
            "early_payment_financing_cost_usd": _r(early_cost), "late_payments": late_n, "late_paid_usd": _r(late_amt),
            "annualised_missed_discounts_usd": _r(disc_missed * ann),
            "annualised_early_payment_cost_usd": _r(early_cost * ann),
            "by_supplier": [{"supplier_id": k, "supplier": sups[k].name if k in sups else k, "critical": sups[k].critical
                             if k in sups else False, **{kk: _r(vv) for kk, vv in v.items()}} for k, v in top],
            "basis": "CALCULATED from paid AP history; annualised figures are ESTIMATED (history × 365/days)",
            "cost_of_funds": cost_of_funds}


# ================================================================================================ risk register
def _score(usd: float) -> int:
    return 5 if usd >= 50e6 else 4 if usd >= 20e6 else 3 if usd >= 5e6 else 2 if usd >= 1e6 else 1


def risk_register(g: GroupData, fc: dict, cov: dict, debt: dict, conc: dict, fxe: dict, dso: dict, ap: dict,
                  provenance: list[dict], pending: int) -> list[dict]:
    risks = []

    def add(rid, cat, title, likelihood, impact_usd, drivers, evidence, mitigation, owner, impact=None):
        risks.append({"risk_id": rid, "category": cat, "title": title, "likelihood": likelihood,
                      "impact": impact or _score(impact_usd), "exposure_usd": _r(impact_usd), "drivers": drivers,
                      "evidence": evidence, "mitigation": mitigation, "owner": owner,
                      "score": likelihood * (impact or _score(impact_usd))})

    short = [e for e in fc["entities"] if e["shortfall_vs_buffer_usd"] > 0]
    if short:
        tot = sum(e["shortfall_vs_buffer_usd"] for e in short)
        add("R-LIQ-1", "LIQUIDITY", f"{len(short)} entities fall below operating buffer on the P10 path",
            4 if len(short) > 3 else 3, tot, [f"{e['entity_id']} shortfall ${e['shortfall_vs_buffer_usd']:,.0f}" for e in short[:5]],
            ["metric:entities_below_buffer", "dataset:analytical.liquidity_forecast"], "Intercompany funding (cash concentration)",
            "Group Treasurer")
    c0 = cov["covenants"][0]
    head = min(c0["headroom_today_pct"], c0["headroom_forward_pct"])
    add("R-COV-1", "COVENANT", f"{c0['name']} is the covenant closest to breach ({head:.0%} headroom)",
        5 if head < 0 else 4 if head < 0.1 else 3 if head < 0.2 else 2 if head < 0.35 else 1,
        g.rcf().ltm_ebitda_usd * max(0.0, 0.35 - head), [c0["formula"], f"clause {c0['clause_ref']}"],
        ["metric:covenant_headroom_min"], "Hold cash buffer; avoid leverage-increasing actions", "CFO", impact=4)
    for r in debt["refinancing"]:
        add(f"R-FUN-{r['facility_id']}", "FUNDING", f"{r['name']} matures in {r['months_to_maturity']} months",
            4 if r["months_to_maturity"] <= 12 else 3, r["amount_usd"],
            [f"Inside the {r['months_to_maturity']}-month window vs DP-2.1", r["assumption"]],
            ["metric:refinancing_due_18m", "dataset:normalized.debt_facilities", "dataset:market.ust_curve"],
            "Mandate refinancing (CFO approval)", "CFO")
    for b in conc["banks"]:
        if b["breach"]:
            add(f"R-CPT-{b['bank_id']}", "COUNTERPARTY", f"{b['bank']} holds {b['share']:.0%} of group cash (limit "
                f"{b['limit_share']:.0%})", 3, b["excess_usd"], [f"rating {b['rating']}"], ["metric:bank_concentration_max"],
                "Rebalance balances across relationship banks (TP-5.2)", "Group Treasurer")
    add("R-FX-1", "FX", "Foreign-currency exposure (1-month 95% VaR, undiversified)", 3, fxe["var95_1m_undiversified_usd"],
        [f"{c['currency']} net {c['net_exposure_usd']:,.0f}" for c in fxe["currencies"][:3] if c["currency"] != "USD"],
        ["metric:fx_var_1m", "dataset:market.fx_history"], "Review hedging for largest net exposures", "Group Treasurer")
    add("R-IR-1", "INTEREST_RATE", "Floating-rate debt: +100 bps raises annual interest", 3,
        debt["rate_sensitivity_100bps_annual_usd"], [f"floating debt ${debt['floating_debt_usd']:,.0f}",
                                                     f"fixed share {debt['fixed_share']:.0%}"],
        ["metric:rate_sensitivity_100bps", "dataset:market.sofr"], "Fixed/floating mix within DP-3.1 band", "CFO")
    if dso["change_days"] > 0:
        add("R-WC-1", "WORKING_CAPITAL", f"DSO up {dso['change_days']} days in 90 days", 4, dso["cash_impact_usd"],
            [f"{c['customer']}: +{c['dso_contribution_days']} d" for c in dso["by_customer"][:3]],
            ["metric:dso_change_90d", "dataset:analytical.dso_bridge"], "Collections acceleration on top contributors",
            "Working Capital Manager")
    add("R-WC-2", "WORKING_CAPITAL", "Supplier-payment leakage (missed discounts + early payments)", 3,
        ap["annualised_missed_discounts_usd"] + ap["annualised_early_payment_cost_usd"],
        [f"discount capture {ap['capture_rate']:.0%}" if ap["capture_rate"] is not None else "no discount history"],
        ["metric:ap_leakage_annual"], "Payment-run discipline: pay on terms, capture discounts", "Working Capital Manager")
    stale = [p["source_id"] for p in provenance if p.get("freshness") == "STALE" or p.get("validation") == "FAILED"]
    if stale:
        add("R-DATA-1", "DATA_QUALITY", f"{len(stale)} data source(s) stale or failed validation", 3, 0, stale, ["provenance"],
            "Refresh / re-ingest", "Finance Administrator", impact=3)
    if pending:
        add("R-GOV-1", "GOVERNANCE", f"{pending} proposed actions awaiting decision", 2, 0, [], ["approvals"],
            "Review in Approval Center", "CFO / Treasurer", impact=2)
    return sorted(risks, key=lambda r: -r["score"])
