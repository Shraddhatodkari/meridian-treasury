"""Liquidity optimiser: where should the group's cash sit for the next 13 weeks?

Linear programme (HiGHS via SciPy) over a hub-and-spoke in-house-bank structure:

  decision variables (USD)
    up_i    cash swept from subsidiary i to Group Treasury (HoldCo)
    down_i  cash funded from Group Treasury to subsidiary i
    repay   revolver repayment            draw   revolver drawdown
    short_i residual local shortfall (overdraft rate + a policy-breach penalty: used only if funding is infeasible)

  minimise   transfer frictions (FX spread + withholding/upstream costs)
           + revolver interest (drawn + draw - repay) over the horizon
           - deposit yield earned where the cash ends up
           + local overdraft cost of any residual shortfall (+ TP-3.1 breach penalty, excluded from reported costs)

  subject to  P10 trough balance_i + down_i - up_i + short_i >= policy buffer_i      (every entity)
              up_i <= cash available today above buffer;   up_i <= repatriation allowance (capital controls)
              HoldCo trough + sum(up) - sum(down) + draw - repay >= HoldCo buffer
              down_i <= operating need (buffer - trough): no borrowing to chase local deposit yields
              0 <= repay <= drawn;  0 <= draw <= undrawn commitment

Using the P10 trough (not the average) means every recommendation survives a 1-in-10 bad path.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.optimize import linprog

from meridian.analytics.forecast import ForecastResult
from meridian.data.generator import GroupData

FX_SPREAD_BPS = 8.0      # bank FX spread on cross-currency sweeps
TRANSFER_FEE_BPS = 1.0
MIN_TICKET_USD = 250_000
# TP-3.1 operating buffers are a policy minimum, not a price: a residual shortfall is allowed only when funding is
# infeasible. A large per-dollar penalty enforces that; it is removed again from every reported cost figure.
BUFFER_BREACH_PENALTY = 1.0


@dataclass
class OptimizationResult:
    transfers: list[dict]
    revolver_repay_usd: float
    revolver_draw_usd: float
    residual_shortfalls: list[dict]
    baseline_cost_usd: float
    optimized_cost_usd: float
    horizon_weeks: int
    status: str
    constraints: list[dict] | None = None
    objective: str = ("Minimise transfer frictions + revolver interest − deposit yield + overdraft cost over the "
                      "13-week horizon, on P10 trough balances")

    @property
    def annualized_benefit_usd(self) -> float:
        return round((self.baseline_cost_usd - self.optimized_cost_usd) * 52 / self.horizon_weeks)

    def to_dict(self) -> dict:
        return {"status": self.status, "transfers": self.transfers,
                "revolver_repay_usd": round(self.revolver_repay_usd), "revolver_draw_usd": round(self.revolver_draw_usd),
                "residual_shortfalls": self.residual_shortfalls,
                "objective": self.objective, "constraints": self.constraints or [],
                "horizon_cost_baseline_usd": round(self.baseline_cost_usd),
                "horizon_cost_optimized_usd": round(self.optimized_cost_usd),
                "annualized_benefit_usd": self.annualized_benefit_usd}


def optimize(g: GroupData, fc: ForecastResult, allow_transfers: bool = True) -> OptimizationResult:
    H = fc.weeks / 52
    ents = {e.entity_id: e for e in g.entities}
    hub = next(e.entity_id for e in g.entities if e.role == "HOLDCO")
    subs = [eid for eid in ents if eid != hub]
    n = len(subs)
    f = g.facility
    trough = {eid: float(np.percentile(p.min(axis=1), 10)) for eid, p in fc.entity_paths_usd.items()}
    opening = {eid: float(p[0, 0]) for eid, p in fc.entity_paths_usd.items()}
    buf = fc.entity_buffers_usd

    # variable layout: up[0:n], down[n:2n], short[2n:3n], repay, draw
    nv = 3 * n + 2
    R, D = 3 * n, 3 * n + 1
    c = np.zeros(nv)
    hub_y = ents[hub].deposit_yield
    for k, eid in enumerate(subs):
        e = ents[eid]
        fx_bps = FX_SPREAD_BPS if e.currency != "USD" else 0.0
        c[k] = (fx_bps + TRANSFER_FEE_BPS + e.upstream_cost_bps) / 1e4 + (e.deposit_yield - hub_y) * H
        c[n + k] = (fx_bps + TRANSFER_FEE_BPS) / 1e4 + (hub_y - e.deposit_yield) * H
        c[2 * n + k] = e.overdraft_rate * H + BUFFER_BREACH_PENALTY
    c[R] = -(f.rate - hub_y) * H      # repaying saves revolver interest but forgoes hub deposit yield
    c[D] = (f.rate - hub_y) * H

    A, b = [], []
    for k, eid in enumerate(subs):     # -(trough + down - up + short) <= -buffer
        row = np.zeros(nv)
        row[k], row[n + k], row[2 * n + k] = 1, -1, -1
        A.append(row)
        b.append(trough[eid] - buf[eid])
    row = np.zeros(nv)                  # HoldCo buffer
    row[:n], row[n:2 * n], row[R], row[D] = -1, 1, 1, -1
    A.append(row)
    b.append(trough[hub] - buf[hub])

    bounds = []
    for eid in subs:
        e = ents[eid]
        cap = max(0.0, min(opening[eid], trough[eid]) - buf[eid]) if allow_transfers else 0.0
        if e.capital_controls:
            cap = min(cap, e.repatriation_allowance_usd)
        bounds.append((0, cap))
    # Treasury policy: intercompany funding only covers operating needs (no yield arbitrage).
    # (+$400 tolerance keeps the LP non-degenerate so buffer duals price funding, not the breach penalty)
    bounds += [(0, max(0.0, buf[eid] - trough[eid]) + (400 if buf[eid] > trough[eid] else 0) if allow_transfers else 0)
               for eid in subs]
    bounds += [(0, None)] * n
    bounds += [(0, f.drawn_usd if allow_transfers else 0), (0, f.limit_usd - f.drawn_usd)]

    res = linprog(c, A_ub=np.array(A), b_ub=np.array(b), bounds=bounds, method="highs")
    if res.status != 0:
        return OptimizationResult([], 0, 0, [], 0, 0, fc.weeks, f"infeasible: {res.message}")
    x = res.x
    base_interest = f.drawn_usd * f.rate * H
    optimized_cost = float(res.fun) - BUFFER_BREACH_PENALTY * float(x[2 * n:3 * n].sum()) + base_interest
    baseline_cost = (base_interest if not allow_transfers else
                     optimize(g, fc, allow_transfers=False).optimized_cost_usd)

    transfers = []
    for k, eid in enumerate(subs):
        e = ents[eid]
        for amt, frm, to, kind in ((x[k], eid, hub, "sweep to Group Treasury"),
                                   (x[n + k], hub, eid, "fund from Group Treasury")):
            if amt >= MIN_TICKET_USD:
                cost = amt * c[k if frm == eid else n + k] - amt * (
                    (e.deposit_yield - hub_y) * H if frm == eid else (hub_y - e.deposit_yield) * H)
                transfers.append({"from_entity": frm, "to_entity": to, "amount_usd": round(amt, -3),
                                  "currency": e.currency, "kind": kind,
                                  "friction_cost_usd": round(cost), "capital_controls": e.capital_controls,
                                  "reason": (f"{ents[eid].name} P10 trough ${trough[eid]/1e6:,.1f}M vs buffer "
                                             f"${buf[eid]/1e6:,.1f}M")})
    # ---- constraint report: which policy constraints bind, and what relaxing them is worth (LP duals) -------
    report = []
    marg = getattr(getattr(res, "ineqlin", None), "marginals", None)
    slack = getattr(getattr(res, "ineqlin", None), "residual", None)
    names = [(f"buffer:{eid}", f"{ents[eid].name} P10 cash ≥ operating buffer (TP-3.1)") for eid in subs] + [
        (f"buffer:{hub}", f"{ents[hub].name} P10 cash ≥ buffer after sweeps, fundings and debt actions (TP-3.1)")]
    if marg is not None:
        for (cid, label), m, s in zip(names, marg, slack, strict=False):
            report.append({"constraint_id": cid, "constraint": label, "type": "policy",
                           "binding": abs(float(s)) < 1_000, "slack_usd": round(float(s)),
                           "shadow_price_per_usd_13w": round(-float(m), 6)})
    for k, eid in enumerate(subs):
        e = ents[eid]
        lo, hi = bounds[k]
        if e.capital_controls and hi is not None:
            report.append({"constraint_id": f"repatriation:{eid}", "constraint": f"{e.name} upstream ≤ repatriation "
                           f"allowance ${e.repatriation_allowance_usd:,.0f} (CR-*)", "type": "regulatory",
                           "binding": hi > 0 and x[k] >= hi - 1_000, "slack_usd": round(hi - x[k]),
                           "shadow_price_per_usd_13w": None})
    report.append({"constraint_id": "rcf:repay<=drawn", "constraint": f"Repayment ≤ drawn revolver "
                   f"${f.drawn_usd:,.0f}", "type": "contractual", "binding": x[R] >= f.drawn_usd - 1_000,
                   "slack_usd": round(f.drawn_usd - x[R]), "shadow_price_per_usd_13w": None})
    shortfalls = [{"entity_id": eid, "residual_shortfall_usd": round(x[2 * n + k])}
                  for k, eid in enumerate(subs) if x[2 * n + k] > 1_000]
    return OptimizationResult(transfers=sorted(transfers, key=lambda t: -t["amount_usd"]),
                              revolver_repay_usd=float(x[R]), revolver_draw_usd=float(x[D]),
                              residual_shortfalls=shortfalls, baseline_cost_usd=baseline_cost,
                              optimized_cost_usd=optimized_cost, horizon_weeks=fc.weeks, status="optimal",
                              constraints=report)
