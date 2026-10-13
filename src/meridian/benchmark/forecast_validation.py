"""Forecast validation on REAL transactions: 13-week weekly cash-in forecasting, back-tested on the UCI Online Retail II
invoices (a UK online retailer, Dec 2009 – Dec 2011, ~1.07M invoice lines; weekly aggregates bundled).

Rolling-origin back-test, no leakage: at each origin week t a method sees weeks ≤ t only and forecasts t+1 … t+13.
Methods compared:
  naive_last          — next weeks = last week
  moving_average_4    — mean of the last 4 weeks
  seasonal_naive      — same week last year
  seasonal_level      — same week last year × (last 8 weeks / same 8 weeks a year earlier)   ← Meridian's method
Accuracy = WAPE (Σ|error| / Σ actual) over all origins and horizons. The 80 % band for the selected method comes from
the empirical 10th/90th percentiles of its relative errors at the same horizon on EARLIER origins only, and its
coverage is measured on later origins.

What this proves and what it does not: it validates the forecasting method on real weekly invoiced sales of a real
business. It is not a test of customer payment timing (this retailer is paid largely at order) and two years of data
is a small sample. Labelled accordingly in the UI and report.
"""

from __future__ import annotations

import functools
import hashlib
import json
from pathlib import Path

DATA = Path(__file__).resolve().parents[1] / "data" / "validation" / "online_retail_weekly.json"
H = 13
SEASON = 52
LEVEL_WEEKS = 8
MIN_ERR_HISTORY = 8


def load(path: Path = DATA) -> dict:
    body = json.loads(path.read_text(encoding="utf-8"))
    if hashlib.sha256(json.dumps(body["series"], sort_keys=True).encode()).hexdigest() != body["checksum_sha256"]:
        raise ValueError("validation series checksum mismatch")
    return body


def _forecast(y: list[float], t: int, method: str) -> list[float]:
    """Forecast weeks t+1..t+H using y[0..t] only."""
    hist = y[: t + 1]
    if method == "naive_last":
        return [hist[-1]] * H
    if method == "moving_average_4":
        return [sum(hist[-4:]) / 4] * H
    if method == "seasonal_naive":
        return [hist[t + h - SEASON] for h in range(1, H + 1)]
    if method == "seasonal_level":
        recent = sum(hist[t - LEVEL_WEEKS + 1: t + 1])
        year_ago = sum(hist[t - SEASON - LEVEL_WEEKS + 1: t - SEASON + 1])
        k = recent / year_ago if year_ago > 0 else 1.0
        return [hist[t + h - SEASON] * k for h in range(1, H + 1)]
    raise ValueError(method)


METHODS = ["naive_last", "moving_average_4", "seasonal_naive", "seasonal_level"]


@functools.lru_cache(maxsize=1)
def run_validation(series_key: str = "net_gbp") -> dict:
    body = load()
    weeks = [r["week"] for r in body["series"]]
    y = [r[series_key] for r in body["series"]]
    first = SEASON + LEVEL_WEEKS - 1                      # every method has the history it needs
    origins = list(range(first, len(y) - H))
    res: dict[str, dict] = {}
    for m in METHODS:
        abs_err = act = 0.0
        by_h = [[0.0, 0.0] for _ in range(H)]
        for t in origins:
            f = _forecast(y, t, m)
            for h in range(H):
                a = y[t + h + 1]
                abs_err += abs(f[h] - a)
                act += a
                by_h[h][0] += abs(f[h] - a)
                by_h[h][1] += a
        res[m] = {"wape": round(abs_err / act, 4), "wape_by_horizon": [round(e / a, 4) for e, a in by_h]}
    best = min(METHODS, key=lambda m: res[m]["wape"])
    # point-in-time 80% band for the Meridian method, coverage measured out of sample
    hits = n = 0
    rel: list[list[float]] = [[] for _ in range(H)]
    for t in origins:
        f = _forecast(y, t, "seasonal_level")
        for h in range(H):
            if t + h + 1 >= len(y):
                continue
            a = y[t + h + 1]
            past = rel[h]
            if len(past) >= MIN_ERR_HISTORY:
                s = sorted(past)
                lo, hi = s[int(0.1 * (len(s) - 1))], s[int(0.9 * (len(s) - 1))]
                n += 1
                hits += f[h] * (1 + lo) <= a <= f[h] * (1 + hi)
        # errors become known only once the actual is observed: add after the whole origin is scored
        for h in range(H):
            if t + h + 1 < len(y) and f[h] > 0:
                rel[h].append(y[t + h + 1] / f[h] - 1)
    naive = res["naive_last"]["wape"]
    return {
        "title": "Forecast validation on real transactions — UCI Online Retail II",
        "source": body["source"], "source_url": body["source_url"], "raw_rows": body["raw_rows"],
        "classification": body["classification"], "period": body["period"], "weeks": len(y),
        "series": series_key, "horizon_weeks": H, "origins": len(origins),
        "first_origin_week": weeks[origins[0]], "last_origin_week": weeks[origins[-1]],
        "methods": res, "selected": "seasonal_level", "best_method": best,
        "improvement_vs_naive_pct": round(100 * (naive - res["seasonal_level"]["wape"]) / naive, 1),
        "interval": {"nominal": 0.8, "observed_coverage": round(hits / n, 3) if n else None, "evaluated_points": n,
                     "method": "empirical 10th–90th percentile of earlier relative errors at the same horizon"},
        "limitations": [
            "Weekly invoiced sales (net of cancellations), used as a cash-in proxy: this retailer is largely paid at "
            "order. Customer payment-timing models still need a client's receivables ledger.",
            f"{len(y)} weeks of history give {len(origins)} rolling origins — a small sample; treat accuracy as indicative.",
            "Holiday shutdown weeks (late December) are real and included; they dominate the largest errors.",
        ],
        "weekly_actuals": [{"week": w, "value": v} for w, v in zip(weeks, y, strict=True)],
    }
