"""13-week direct cash-flow forecast (the treasury standard), probabilistic via Monte Carlo.

Per entity, per week: opening cash
  + collections of the OPEN AR book       (timing from the ML delay model's P10/P50/P90)
  + collections of FUTURE billing          (run-rate billing × entity's empirical collection lag)
  - supplier payments of the OPEN AP book  (due date, or discount date if early-pay is planned)
  - future AP run-rate
  - payroll / rent / opex / tax schedules
  - revolver interest (HoldCo)
Every path is converted to USD (with FX shocks if any) to give group P10 / P50 / P90 cash,
liquidity (cash + undrawn revolver) and covenant headroom.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import cast

import numpy as np

from meridian.analytics.scenarios import Scenario, apply_events
from meridian.data.generator import GroupData
from meridian.ml.ar_model import ARDelayModel


@dataclass
class ForecastResult:
    as_of: date
    weeks: int
    week_starts: list[date]
    scenario: str
    entity_paths_usd: dict[str, np.ndarray]          # (paths, weeks+1) closing balances incl. opening
    group_cash_usd: np.ndarray                        # (paths, weeks+1)
    liquidity_usd: np.ndarray                         # cash + undrawn revolver
    covenant_min_liquidity_usd: float
    entity_buffers_usd: dict[str, float]
    open_ar_collections_usd: np.ndarray               # (paths, weeks) — for back-testing
    flows_p50_usd: dict[str, list[float]] = field(default_factory=dict)  # group weekly flows by category

    def band(self, arr: np.ndarray) -> dict[str, list[float]]:
        return {f"p{q}": [round(float(x), 0) for x in np.percentile(arr, q, axis=0)] for q in (10, 50, 90)}

    def summary(self) -> dict:
        liq10 = np.percentile(self.liquidity_usd, 10, axis=0)
        head = liq10 - self.covenant_min_liquidity_usd
        ents = []
        for eid, p in self.entity_paths_usd.items():
            trough10 = float(np.percentile(p.min(axis=1), 10))
            weeks_below = int(np.sum(np.percentile(p, 50, axis=0)[1:] < self.entity_buffers_usd[eid]))
            ents.append({"entity_id": eid, "opening_usd": round(float(p[0, 0])),
                         "p50_week13_usd": round(float(np.percentile(p[:, -1], 50))),
                         "p10_trough_usd": round(trough10), "buffer_usd": round(self.entity_buffers_usd[eid]),
                         "weeks_p50_below_buffer": weeks_below,
                         "shortfall_vs_buffer_usd": round(max(0.0, self.entity_buffers_usd[eid] - trough10))})
        return {
            "as_of": str(self.as_of), "scenario": self.scenario, "weeks": self.weeks,
            "week_starts": [str(d) for d in self.week_starts],
            "group_cash_usd": self.band(self.group_cash_usd),
            "liquidity_usd": self.band(self.liquidity_usd),
            "covenant_min_liquidity_usd": self.covenant_min_liquidity_usd,
            "min_covenant_headroom_p10_usd": round(float(head.min())),
            "covenant_breach_week": next((i for i, h in enumerate(head) if h < 0), None),
            "entities": ents,
            "flows_p50_usd": self.flows_p50_usd,
        }


def _inverse_cdf(u: np.ndarray, p10: np.ndarray, p50: np.ndarray, p90: np.ndarray) -> np.ndarray:
    """Piecewise-linear quantile function through (0.1,p10),(0.5,p50),(0.9,p90) with linear tails."""
    lo = p50 - (p50 - p10) * (0.5 - u) / 0.4
    hi = p50 + (p90 - p50) * (u - 0.5) / 0.4
    return np.where(u < 0.5, lo, hi)


def _schedule(anchor: date, cadence: str, start: date, end: date) -> list[date]:
    out, d = [], anchor
    step = {"WEEKLY": 7, "BIWEEKLY": 14}.get(cadence)
    if step:
        while d < start:
            d += timedelta(days=step)
        while d < end:
            out.append(d)
            d += timedelta(days=step)
        return out
    months = {"MONTHLY": 1, "QUARTERLY": 3}[cadence]
    y, m = anchor.year, anchor.month
    while True:
        try:
            d = date(y, m, anchor.day)
        except ValueError:
            d = date(y, m, 28)
        if d >= end:
            break
        if d >= start:
            out.append(d)
        m += months
        y, m = y + (m - 1) // 12, (m - 1) % 12 + 1
    return out


def run_forecast(g: GroupData, model: ARDelayModel, scenario: Scenario | None = None, weeks: int = 13,
                 paths: int = 400, seed: int = 0, early_pay_bills: set[str] | None = None) -> ForecastResult:
    scenario = apply_events(scenario or Scenario(), g.risk_events)
    rng = np.random.default_rng(seed)
    as_of, end = g.as_of, g.as_of + timedelta(days=7 * weeks)
    fx = {c: r * (1 + scenario.fx_shock_pct.get(c, 0.0)) for c, r in g.fx.items()}
    ents = {e.entity_id: e for e in g.entities}
    country = {e.entity_id: e.country for e in g.entities}
    early_pay_bills = early_pay_bills or set()

    def wk(d: date) -> int:
        return (d - as_of).days // 7

    flows = {eid: np.zeros((paths, weeks)) for eid in ents}       # local currency
    cat = {k: np.zeros(weeks) for k in ("AR_open", "AR_new", "AP", "Payroll/Rent/Opex/Tax", "Interest")}

    # ---- open AR book (ML timing) ------------------------------------------------------------
    open_ar = [r for r in g.receivables if r.settled_date is None]
    pred = model.predict(open_ar, as_of)
    open_ar_usd = np.zeros((paths, weeks))
    if open_ar:
        u = rng.random((paths, len(open_ar)))
        late = _inverse_cdf(u, pred.p10, pred.p50, pred.p90)
        due_off = np.array([(r.due_date - as_of).days for r in open_ar], dtype=float)
        dtp = np.array([(r.due_date - r.issue_date).days for r in open_ar], dtype=float) + late
        extra = scenario.collection_delay_pct * np.maximum(dtp, 0)
        extra += np.array([scenario.country_delay_days.get(country[r.entity_id], 0.0) for r in open_ar])
        settle_off = due_off + late + extra
        # Already-overdue invoices that the model says "should have paid" arrive over the next 2 weeks.
        settle_off = np.where(settle_off < 0, rng.uniform(0, 14, settle_off.shape), settle_off)
        widx = (settle_off // 7).astype(int)
        amounts = np.array([r.amount for r in open_ar])
        ent_idx = np.array([r.entity_id for r in open_ar])
        usd = np.array([fx[r.currency] for r in open_ar])
        for eid in ents:
            mask = ent_idx == eid
            if not mask.any():
                continue
            for w in range(weeks):
                hit = (widx[:, mask] == w) * amounts[mask]
                flows[eid][:, w] += hit.sum(axis=1)
                open_ar_usd[:, w] += (hit * usd[mask]).sum(axis=1)

    # ---- future billing (run-rate × empirical collection lag) --------------------------------
    recent_from = as_of - timedelta(days=84)
    lag_from = as_of - timedelta(days=270)
    for eid, e in ents.items():
        inv = [r for r in g.receivables if r.entity_id == eid]
        if not inv:
            continue
        weekly_bill = sum(r.amount for r in inv if r.issue_date >= recent_from) / 12
        chg = scenario.billing_change_pct.get(e.country, 0.0) + scenario.billing_change_pct.get("*", 0.0)
        weekly_bill *= (1 + chg)
        lags = np.array([(r.settled_date - r.issue_date).days for r in inv
                         if r.settled_date and r.issue_date >= lag_from], dtype=float)
        if lags.size == 0:
            continue
        lags = lags * (1 + scenario.collection_delay_pct) + scenario.country_delay_days.get(e.country, 0.0)
        pmf = np.bincount((lags // 7).astype(int), minlength=weeks + 30) / lags.size
        vol = rng.lognormal(0, 0.08, (paths, weeks))  # billing volume uncertainty
        for issue_w in range(weeks):
            for w in range(issue_w, weeks):
                share = pmf[w - issue_w]
                if share:
                    flows[eid][:, w] += weekly_bill * vol[:, issue_w] * share
                    cat["AR_new"][w] += weekly_bill * share * fx[e.currency]

    # ---- AP: open book + run-rate ------------------------------------------------------------
    open_ap = [p for p in g.payables if p.paid_date is None]
    for p in open_ap:
        pay = p.issue_date + timedelta(days=p.discount_days) if p.bill_id in early_pay_bills else p.due_date
        amt = p.amount * (1 - p.discount_pct) if p.bill_id in early_pay_bills else p.amount
        w = max(0, wk(pay))
        if w < weeks:
            flows[p.entity_id][:, w] -= amt
            cat["AP"][w] -= amt * fx[p.currency]
    for eid, e in ents.items():
        recent = [p for p in open_ap if p.entity_id == eid and p.issue_date >= as_of - timedelta(days=42)]
        if not recent:
            continue
        weekly_ap = sum(p.amount for p in recent) / 6
        avg_terms = int(np.mean([(p.due_date - p.issue_date).days for p in recent]))
        for issue_w in range(weeks):
            w = issue_w + avg_terms // 7
            if w < weeks:
                flows[eid][:, w] -= weekly_ap
                cat["AP"][w] -= weekly_ap * fx[e.currency]

    # ---- recurring operating flows -------------------------------------------------------------
    for rf in g.recurring:
        for d in _schedule(rf.anchor, rf.cadence, as_of, end):
            flows[rf.entity_id][:, wk(d)] += rf.amount
            cat["Payroll/Rent/Opex/Tax"][wk(d)] += rf.amount * fx[rf.currency]

    # ---- debt service (HoldCo): floating interest monthly, note coupons semi-annually, term-loan amortisation -----
    f = g.rcf()
    holdco = next(e.entity_id for e in g.entities if e.role == "HOLDCO")
    shock = scenario.rate_shock_bps / 10_000
    facilities = g.debt_facilities or []
    if not facilities:   # legacy single-facility data
        for d in _schedule(date(as_of.year, as_of.month, 1), "MONTHLY", as_of, end):
            flows[holdco][:, wk(d)] -= f.drawn_usd * (f.rate + shock) / 12
            cat["Interest"][wk(d)] -= f.drawn_usd * (f.rate + shock) / 12
    for fac in facilities:
        if fac.rate_type == "FLOATING":
            for d in _schedule(date(as_of.year, as_of.month, 1), "MONTHLY", as_of, end):
                amt = fac.drawn_usd * (fac.rate + shock) / 12
                flows[holdco][:, wk(d)] -= amt
                cat["Interest"][wk(d)] -= amt
        else:
            m = fac.maturity_date
            for d in _schedule(date(as_of.year - 1, m.month, min(m.day, 28)), "QUARTERLY", as_of, end):
                if (d.month - m.month) % 6 == 0:
                    amt = fac.drawn_usd * fac.rate / 2
                    flows[holdco][:, wk(d)] -= amt
                    cat["Interest"][wk(d)] -= amt
        if fac.amortization_pct_per_year:
            for d in _schedule(date(as_of.year, 3, 31), "QUARTERLY", as_of, end):
                amt = fac.limit_usd * fac.amortization_pct_per_year / 4
                flows[holdco][:, wk(d)] -= amt
                cat.setdefault("Debt amortisation", np.zeros(weeks))[wk(d)] -= amt

    # ---- balances --------------------------------------------------------------------------------
    opening = {eid: 0.0 for eid in ents}
    for a in g.accounts:
        opening[a.entity_id] += a.balance
    entity_usd, buffers = {}, {}
    for eid, e in ents.items():
        local = np.concatenate([np.full((paths, 1), opening[eid]), opening[eid] + np.cumsum(flows[eid], axis=1)],
                               axis=1)
        entity_usd[eid] = local * fx[e.currency]
        buffers[eid] = e.min_buffer_local * fx[e.currency]
    group = cast(np.ndarray, sum(entity_usd.values()))
    liquidity = group + (f.limit_usd - f.drawn_usd)
    cat["AR_open"] = open_ar_usd.mean(axis=0)
    return ForecastResult(
        as_of=as_of, weeks=weeks, week_starts=[as_of + timedelta(days=7 * i) for i in range(weeks)],
        scenario=scenario.name, entity_paths_usd=entity_usd, group_cash_usd=group, liquidity_usd=liquidity,
        covenant_min_liquidity_usd=f.min_liquidity_covenant_usd, entity_buffers_usd=buffers,
        open_ar_collections_usd=open_ar_usd,
        flows_p50_usd={k: [round(float(x)) for x in v] for k, v in cat.items()})


def backtest_open_ar(g: GroupData, fc: ForecastResult) -> dict:
    """Compare weekly collections of the open AR book: ML forecast vs ERP due-date assumption vs actual."""
    weeks, as_of = fc.weeks, g.as_of
    actual, erp = np.zeros(weeks), np.zeros(weeks)
    for r in g.receivables:
        if r.settled_date is not None:
            continue
        usd = r.amount * g.fx[r.currency]
        # ground truth exists only on generated (synthetic) data; client loads never carry it
        a = (cast(date, r.actual_settle_date) - as_of).days // 7
        if 0 <= a < weeks:
            actual[a] += usd
        e = max(0, (r.due_date - as_of).days // 7)
        if e < weeks:
            erp[e] += usd
    model = fc.open_ar_collections_usd.mean(axis=0)
    p10, p90 = np.percentile(fc.open_ar_collections_usd, [10, 90], axis=0)

    def wape(pred):
        return float(np.abs(pred - actual).sum() / actual.sum())

    return {"weeks": weeks, "actual_usd": actual.round(0).tolist(), "model_mean_usd": model.round(0).tolist(),
            "erp_due_date_usd": erp.round(0).tolist(),
            "wape_model": round(wape(model), 4), "wape_erp_due_date": round(wape(erp), 4),
            "p10_p90_weekly_coverage": round(float(np.mean((actual >= p10) & (actual <= p90))), 3)}
