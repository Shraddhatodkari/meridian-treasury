"""Working-capital analytics: DSO/DPO, aging, collections prioritisation, early-pay discounts, trapped cash."""

from __future__ import annotations

from datetime import timedelta

import numpy as np

from meridian.analytics.forecast import ForecastResult
from meridian.data.generator import GroupData
from meridian.ml.ar_model import ARDelayModel


def kpis(g: GroupData) -> dict:
    fx, as_of = g.fx, g.as_of
    open_ar = [r for r in g.receivables if r.settled_date is None]
    ar = sum(r.amount * fx[r.currency] for r in open_ar)
    rev90 = sum(r.amount * fx[r.currency] for r in g.receivables if r.issue_date >= as_of - timedelta(days=90))
    open_ap = g.open_payables()
    ap = sum(p.amount * fx[p.currency] for p in open_ap)
    # DPO = open AP / average daily purchases (all bills issued in the last 90 days, paid or open). Falls back to
    # the open book's last 42 days when no payment history has been loaded.
    hist = [p for p in g.payables if p.issue_date >= as_of - timedelta(days=90)]
    has_hist = any(p.paid_date for p in hist)
    ap_issued_42 = (sum(p.amount * fx[p.currency] for p in hist) / 90 * 42 if has_hist else
                    sum(p.amount * fx[p.currency] for p in open_ap if p.issue_date >= as_of - timedelta(days=42)))
    buckets = {"current": 0.0, "1-30": 0.0, "31-60": 0.0, "61-90": 0.0, "90+": 0.0}
    for r in open_ar:
        od = (as_of - r.due_date).days
        k = "current" if od <= 0 else "1-30" if od <= 30 else "31-60" if od <= 60 else "61-90" if od <= 90 else "90+"
        buckets[k] += r.amount * fx[r.currency]
    cash = sum(a.balance * fx[a.currency] for a in g.accounts)
    ents = {e.entity_id: e for e in g.entities}
    by_ent: dict[str, float] = {}
    for a in g.accounts:
        by_ent[a.entity_id] = by_ent.get(a.entity_id, 0.0) + a.balance * fx[a.currency]
    trapped = sum(max(0.0, v - ents[k].min_buffer_local * fx[ents[k].currency] - ents[k].repatriation_allowance_usd)
                  for k, v in by_ent.items() if ents[k].capital_controls)
    dso = ar / rev90 * 90 if rev90 else None
    dpo = ap / (ap_issued_42 / 42) if ap_issued_42 else None
    f = g.facility
    return {
        "as_of": str(as_of), "cash_usd": round(cash), "bank_accounts": len(g.accounts),
        "currencies": len({a.currency for a in g.accounts}), "entities": len(g.entities),
        "open_ar_usd": round(ar), "overdue_ar_usd": round(ar - buckets["current"]),
        "ar_aging_usd": {k: round(v) for k, v in buckets.items()},
        "open_ap_usd": round(ap), "dso_days": round(dso, 1) if dso else None,
        "dpo_days": round(dpo, 1) if dpo else None,
        "trapped_cash_usd": round(trapped),
        "revolver": {"limit_usd": f.limit_usd, "drawn_usd": f.drawn_usd, "rate": f.rate},
        "liquidity_usd": round(cash + f.limit_usd - f.drawn_usd),
        "min_liquidity_covenant_usd": f.min_liquidity_covenant_usd,
        "net_leverage_x": round((f.gross_debt_usd - cash) / f.ltm_ebitda_usd, 2),
        "max_net_leverage_x": f.max_net_leverage,
    }


def collections_priorities(g: GroupData, model: ARDelayModel, top_n: int = 15) -> list[dict]:
    """Rank open invoices by cash-at-risk: value × predicted lateness, with a recommended action."""
    open_ar = [r for r in g.receivables if r.settled_date is None]
    pred = model.predict(open_ar, g.as_of)
    custs = {c.customer_id: c for c in g.customers}
    rows = []
    for r, p50, p90 in zip(open_ar, pred.p50, pred.p90, strict=True):
        usd = r.amount * g.fx[r.currency]
        exp_pay = r.due_date + timedelta(days=round(float(p50)))
        days_cash_late = max(0.0, float(p50))
        score = usd * days_cash_late / 365  # USD-years of delay ≈ financing cost driver
        if score <= 0:
            continue
        action = ("Resolve dispute (assign owner, credit-note review)" if r.disputed else
                  "Executive escalation + hold on new shipments review" if p90 > 45 else
                  "Personalised reminder from AR + confirm remittance date" if p50 > 10 else "Standard dunning")
        rows.append({"invoice_id": r.invoice_id, "entity_id": r.entity_id, "customer_id": r.customer_id,
                     "customer": custs[r.customer_id].name, "rating": custs[r.customer_id].credit_rating,
                     "amount_usd": round(usd), "due_date": str(r.due_date),
                     "predicted_days_late_p50": round(float(p50), 1), "predicted_days_late_p90": round(float(p90), 1),
                     "expected_pay_date": str(exp_pay), "disputed": r.disputed,
                     "financing_cost_of_delay_usd": round(score * g.facility.rate), "recommended_action": action})
    return sorted(rows, key=lambda x: -x["financing_cost_of_delay_usd"])[:top_n]


def early_pay_opportunities(g: GroupData, fc: ForecastResult, cost_of_funds: float | None = None) -> dict:
    """Capture supplier discounts (e.g. 2/10 net 30) where the implied return beats the cost of funds
    AND the paying entity stays above its buffer on the P10 path."""
    cof = cost_of_funds if cost_of_funds is not None else g.facility.rate
    headroom = {eid: float(np.percentile(p.min(axis=1), 10)) - fc.entity_buffers_usd[eid]
                for eid, p in fc.entity_paths_usd.items()}
    cands = []
    for p in g.open_payables():
        if not p.discount_pct:
            continue
        pay_by = p.issue_date + timedelta(days=p.discount_days)
        if pay_by < g.as_of:
            continue  # discount window already closed
        days_gained = (p.due_date - pay_by).days
        apr = p.discount_pct / (1 - p.discount_pct) * 365 / max(days_gained, 1)
        if apr > cof:
            usd = p.amount * g.fx[p.currency]
            cands.append({"bill_id": p.bill_id, "entity_id": p.entity_id, "supplier_id": p.supplier_id,
                          "amount_usd": round(usd), "discount_pct": p.discount_pct, "pay_by": str(pay_by),
                          "implied_annual_return": round(apr, 4), "saving_usd": round(usd * p.discount_pct)})
    cands.sort(key=lambda c: -c["implied_annual_return"])
    plan, skipped = [], []
    for c in cands:
        if headroom.get(c["entity_id"], 0) >= c["amount_usd"]:
            headroom[c["entity_id"]] -= c["amount_usd"]
            plan.append(c)
        else:
            skipped.append({**c, "reason": "insufficient P10 headroom at paying entity"})
    return {"cost_of_funds": cof, "selected": plan, "skipped_for_liquidity": skipped,
            "total_saving_usd": sum(c["saving_usd"] for c in plan),
            "cash_accelerated_usd": sum(c["amount_usd"] for c in plan)}
