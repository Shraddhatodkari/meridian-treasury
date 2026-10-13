"""Receivables payment-delay model: predicts *days late* per open invoice as P10 / P50 / P90.

* Point-in-time features: every customer statistic uses only invoices SETTLED BEFORE the
  invoice's issue date — no look-ahead leakage.
* Time-based validation: train on older invoices, validate on later ones.
* Quantile gradient boosting gives an uncertainty band that feeds the Monte-Carlo cash forecast.
* Benchmarked against what ERPs implicitly assume (customer pays on due date) and a naive
  customer-average heuristic. The model must beat both to ship (see model card).
"""

from __future__ import annotations

import hashlib
import io
from bisect import bisect_left
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import cast

import joblib
import numpy as np
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.metrics import mean_absolute_error

from meridian.config import PROJECT_ROOT
from meridian.data.generator import COUNTRY_DELAY, FX_TO_USD, RATINGS, SEGMENTS
from meridian.domain.models import Customer, Receivable

MODEL_VERSION = "ar-delay-hgb-q-1.0.0"
MODEL_DIR = PROJECT_ROOT / "artifacts"
FEATURES = ["hist_mean_late", "recent_mean_late", "hist_count", "hist_dispute_rate", "late_trend",
            "log_amount_usd", "terms_days", "disputed", "paperless", "quarter_end", "rating", "segment", "country"]
CATEGORICAL = ["rating", "segment", "country"]
_RATING_IDX = {r: i for i, r in enumerate(RATINGS)}
_SEGMENT_IDX = {s: i for i, s in enumerate(SEGMENTS)}
_COUNTRY_IDX = {c: i for i, c in enumerate(COUNTRY_DELAY)}


def _settled(r: Receivable) -> date:
    """Settlement date of an invoice already filtered to settled ones (typing narrowing only)."""
    return cast(date, r.settled_date)


class CustomerHistory:
    """Point-in-time customer payment statistics."""

    def __init__(self, receivables: list[Receivable]):
        self._by_cust: dict[str, tuple[list[date], list[float], list[bool]]] = {}
        settled = sorted((r for r in receivables if r.settled_date), key=_settled)
        for r in settled:
            d, late, disp = self._by_cust.setdefault(r.customer_id, ([], [], []))
            d.append(_settled(r))
            late.append((_settled(r) - r.due_date).days)
            disp.append(r.disputed)

    def stats(self, customer_id: str, as_of: date) -> dict[str, float]:
        d, late, disp = self._by_cust.get(customer_id, ([], [], []))
        k = bisect_left(d, as_of)
        if k == 0:
            return {"hist_mean_late": np.nan, "recent_mean_late": np.nan, "hist_count": 0.0,
                    "hist_dispute_rate": np.nan, "late_trend": np.nan}
        hist = np.array(late[:k])
        j = bisect_left(d, date.fromordinal(as_of.toordinal() - 90))
        recent = np.array(late[j:k]) if k > j else hist[-5:]
        return {"hist_mean_late": float(hist.mean()), "recent_mean_late": float(recent.mean()),
                "hist_count": float(k), "hist_dispute_rate": float(np.mean(disp[:k])),
                "late_trend": float(recent.mean() - hist.mean())}


def featurize(invs: list[Receivable], customers: dict[str, Customer], hist: CustomerHistory,
              as_of: date | None = None, fx: dict[str, float] | None = None) -> np.ndarray:
    fx = fx or FX_TO_USD
    rows = []
    for r in invs:
        c = customers[r.customer_id]
        s = hist.stats(r.customer_id, as_of or r.issue_date)
        rows.append([s["hist_mean_late"], s["recent_mean_late"], s["hist_count"], s["hist_dispute_rate"],
                     s["late_trend"], np.log1p(r.amount * fx.get(r.currency, 1.0)), r.terms_days, float(r.disputed),
                     float(r.paperless), float(r.issue_date.month in (3, 6, 9, 12) and r.issue_date.day > 15),
                     # unseen categories map to a dedicated "other" code
                     _RATING_IDX.get(c.credit_rating, len(_RATING_IDX)), _SEGMENT_IDX.get(c.segment, len(_SEGMENT_IDX)),
                     _COUNTRY_IDX.get(c.country, len(_COUNTRY_IDX))])
    return np.array(rows, dtype=float)


@dataclass
class DelayPrediction:
    p10: np.ndarray
    p50: np.ndarray
    p90: np.ndarray


def _model(q: float) -> HistGradientBoostingRegressor:
    cat = [FEATURES.index(c) for c in CATEGORICAL]
    return HistGradientBoostingRegressor(loss="quantile", quantile=q, max_iter=300, learning_rate=0.05,
                                         max_leaf_nodes=31, min_samples_leaf=40, categorical_features=cat,
                                         random_state=0)


def train(receivables: list[Receivable], customers: list[Customer], as_of: date,
          fx: dict[str, float] | None = None) -> dict:
    custs = {c.customer_id: c for c in customers}
    hist = CustomerHistory(receivables)
    split = date.fromordinal(as_of.toordinal() - 300)
    cutoff = date.fromordinal(as_of.toordinal() - 150)  # validation invoices are (almost) all settled
    train_set = [r for r in receivables if r.settled_date and r.issue_date < split]
    valid_set = [r for r in receivables if r.settled_date and split <= r.issue_date < cutoff]
    y_tr = np.array([(_settled(r) - r.due_date).days for r in train_set], dtype=float)
    y_va = np.array([(_settled(r) - r.due_date).days for r in valid_set], dtype=float)
    if len(train_set) < 200 or len(valid_set) < 50:
        raise ValueError(f"insufficient settled history to train (train={len(train_set)}, validation={len(valid_set)})")
    X_tr, X_va = featurize(train_set, custs, hist, fx=fx), featurize(valid_set, custs, hist, fx=fx)
    models = {q: _model(q).fit(X_tr, y_tr) for q in (0.1, 0.5, 0.9)}
    # Conformalised quantile regression (CQR): calibrate the band on the first half of the
    # validation window, report honest coverage on the second half.
    half = len(valid_set) // 2
    p10, p50, p90 = (models[q].predict(X_va) for q in (0.1, 0.5, 0.9))
    scores = np.maximum(p10[:half] - y_va[:half], y_va[:half] - p90[:half])
    conformal_q = float(np.quantile(scores, 0.8 * (1 + 1 / max(half, 1))))
    t = slice(half, None)
    p10c, p90c = p10[t] - conformal_q, p90[t] + conformal_q
    naive = np.nan_to_num(X_va[t, FEATURES.index("hist_mean_late")], nan=float(np.mean(y_tr)))
    y_te = y_va[t]
    card = {
        "model_version": MODEL_VERSION,
        "trained_at": datetime.now(UTC).isoformat(),
        "algorithm": "HistGradientBoostingRegressor, quantile loss (P10/P50/P90)",
        "train_rows": len(train_set), "validation_rows": len(valid_set),
        "validation_window": f"invoices issued {split} .. {cutoff}",
        "test_rows": int(len(y_te)),
        "mae_days_model_p50": round(float(mean_absolute_error(y_te, p50[t])), 2),
        "mae_days_erp_due_date_assumption": round(float(mean_absolute_error(y_te, np.zeros_like(y_te))), 2),
        "mae_days_customer_average": round(float(mean_absolute_error(y_te, naive)), 2),
        "p10_p90_coverage_raw": round(float(np.mean((y_te >= p10[t]) & (y_te <= p90[t]))), 3),
        "p10_p90_coverage_conformal": round(float(np.mean((y_te >= p10c) & (y_te <= p90c))), 3),
        "conformal_adjustment_days": round(conformal_q, 2),
        "features": FEATURES,
        "intended_use": "Cash-forecast collections timing and collections prioritisation. Not a credit decision.",
    }
    return {"models": models, "card": card, "conformal_q": conformal_q}


class ARDelayModel:
    def __init__(self, bundle: dict, receivables: list[Receivable], customers: list[Customer],
                 fx: dict[str, float] | None = None):
        self.models, self.card = bundle["models"], bundle["card"]
        self.fx = fx
        self.cq = float(bundle.get("conformal_q", 0.0))
        self.custs = {c.customer_id: c for c in customers}
        self.hist = CustomerHistory(receivables)

    def predict(self, invs: list[Receivable], as_of: date) -> DelayPrediction:
        if not invs:
            z = np.zeros(0)
            return DelayPrediction(z, z, z)
        X = featurize(invs, self.custs, self.hist, as_of=as_of, fx=self.fx)
        p10, p50, p90 = (self.models[q].predict(X) for q in (0.1, 0.5, 0.9))
        p10, p90 = np.minimum(p10 - self.cq, p50), np.maximum(p90 + self.cq, p50)  # calibrated, monotone
        return DelayPrediction(p10, p50, p90)


_CACHE: dict[str, dict] = {}


def fingerprint(receivables: list[Receivable], as_of: date) -> str:
    """Deterministic identity of the training data: same book => same model, different book => retrain."""
    h = hashlib.sha256()
    for r in sorted(receivables, key=lambda r: r.invoice_id):
        h.update(f"{r.invoice_id}|{r.amount}|{r.issue_date}|{r.due_date}|{r.settled_date}".encode())
    return f"{as_of}:{h.hexdigest()[:16]}"


def load_or_train(receivables: list[Receivable], customers: list[Customer], as_of: date,
                  fx: dict[str, float] | None = None, store=None) -> dict:
    """Model registry. Artefacts live in the store (MongoDB GridFS in production) so every API replica
    serves the same validated model; local process memory is only a cache."""
    fp = fingerprint(receivables, as_of)
    if fp in _CACHE:
        return _CACHE[fp]
    model_id = f"ar-delay:{fp}"
    if store is not None:
        blob = store.get_blob(model_id)
        if blob:
            bundle = joblib.load(io.BytesIO(blob))
            _CACHE[fp] = bundle
            return bundle
    bundle = train(receivables, customers, as_of, fx=fx)
    bundle["card"].update({"data_fingerprint": fp, "model_id": model_id})
    if store is not None:
        buf = io.BytesIO()
        joblib.dump(bundle, buf)
        store.put_blob(model_id, buf.getvalue())
        store.put("model_registry", {"model_id": model_id, "model_version": MODEL_VERSION, "status": "ACTIVE",
                                     "registered_at": datetime.now(UTC).isoformat(), "card": bundle["card"],
                                     "artifact_bytes": len(buf.getvalue())})
    _CACHE[fp] = bundle
    return bundle
