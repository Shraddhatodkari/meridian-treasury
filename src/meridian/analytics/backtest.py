"""Point-in-time back-test of the collections forecast — works identically on demo and client data.

Rewind the ledger to T0 = as-of − 13 weeks, using ONLY information available at T0:
* invoices issued before T0; anything settled on/after T0 is treated as still open
* a payment-delay model re-trained on that rewound history (no look-ahead)
Then forecast the next 13 weeks of collections on the T0 open book and compare with what actually
settled. Benchmark: the ERP convention that customers pay on the due date.
"""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np

from meridian.analytics.forecast import run_forecast
from meridian.data.generator import GroupData
from meridian.ml.ar_model import ARDelayModel, load_or_train


def backtest_point_in_time(g: GroupData, store=None, weeks: int = 13, paths: int = 300, seed: int = 0) -> dict:
    t0 = g.as_of - timedelta(days=7 * weeks)
    recv: list = []
    actual: dict[str, date | None] = {}
    for r in g.receivables:
        if r.issue_date >= t0:
            continue
        rr = r.model_copy()
        if rr.settled_date and rr.settled_date >= t0:
            actual[rr.invoice_id] = rr.settled_date
            rr.settled_date = None
        elif rr.settled_date is None:
            actual[rr.invoice_id] = None          # still unpaid at as-of
        recv.append(rr)
    g0 = GroupData(as_of=t0, entities=g.entities, accounts=g.accounts, customers=g.customers, suppliers=g.suppliers,
                   receivables=recv, payables=[], recurring=[], facility=g.facility, risk_events=[], fx=g.fx,
                   classification=g.classification, tenant=g.tenant)
    bundle = load_or_train(g0.receivables, g0.customers, t0, fx=g.fx, store=store)
    model = ARDelayModel(bundle, g0.receivables, g0.customers, fx=g.fx)
    fc = run_forecast(g0, model, weeks=weeks, paths=paths, seed=seed)

    act, erp = np.zeros(weeks), np.zeros(weeks)
    for r in recv:
        if r.settled_date is not None:
            continue
        usd = r.amount * g.fx.get(r.currency, 1.0)
        a = actual.get(r.invoice_id)
        if a is not None and 0 <= (a - t0).days // 7 < weeks:
            act[(a - t0).days // 7] += usd
        e = max(0, (r.due_date - t0).days // 7)
        if e < weeks:
            erp[e] += usd
    model_mean = fc.open_ar_collections_usd.mean(axis=0)
    p10, p90 = np.percentile(fc.open_ar_collections_usd, [10, 90], axis=0)

    def wape(pred):
        return float(np.abs(pred - act).sum() / max(act.sum(), 1.0))

    return {"method": "point-in-time rewind (no look-ahead)", "t0": str(t0), "as_of": str(g.as_of), "weeks": weeks,
            "open_invoices_at_t0": sum(1 for r in recv if r.settled_date is None),
            "actual_usd": act.round(0).tolist(), "model_mean_usd": model_mean.round(0).tolist(),
            "model_p10_usd": p10.round(0).tolist(), "model_p90_usd": p90.round(0).tolist(),
            "erp_due_date_usd": erp.round(0).tolist(), "wape_model": round(wape(model_mean), 4),
            "wape_erp_due_date": round(wape(erp), 4),
            "p10_p90_weekly_coverage": round(float(np.mean((act >= p10) & (act <= p90))), 3),
            "model_id": bundle["card"].get("model_id")}
