"""Bank-statement data quality: balance reconciliation and duplicate detection (v3.3).

* Reconciliation: for a statement whose entries all fall on its balance date (a normal end-of-day camt.053 / BAI2),
  opening balance + Σ entries must equal the closing balance to the cent; a difference is a BREAK (ERROR).
  Statements whose entries span several days (history extracts) cannot be reconciled from one opening/closing pair;
  they are reported as NOT_CHECKABLE with the reason — never silently passed.
* Duplicates: an entry repeating the same (reference, amount, booking date) within a statement is flagged and
  dropped once, so cash is never double-counted; a statement id delivered twice is ignored after the first file.
"""

from __future__ import annotations


def reconcile(stmt: dict) -> dict:
    opening, closing = stmt.get("opening_balance"), stmt.get("closing_balance")
    txns = stmt.get("transactions") or []
    if opening is None or closing is None:
        return {"status": "NOT_CHECKABLE", "reason": "opening or closing balance missing"}
    days = {t.get("booking_date") for t in txns}
    if txns and days != {stmt.get("balance_date")}:
        return {"status": "NOT_CHECKABLE", "reason": f"entries span {len(days)} booking dates (history extract)"}
    total = round(sum(t["amount"] for t in txns), 2)
    diff = round(opening + total - closing, 2)
    return {"status": "RECONCILED" if abs(diff) < 0.005 else "BREAK", "opening": opening, "entries_total": total,
            "closing": closing, "difference": diff}


def dedupe_transactions(stmt: dict) -> tuple[list[dict], list[str]]:
    seen, keep, dups = set(), [], []
    for t in stmt.get("transactions") or []:
        key = (t.get("reference"), round(t["amount"], 2), t.get("booking_date"))
        if t.get("reference") and key in seen:
            dups.append(f"{t.get('reference')} {t['amount']:.2f} {t.get('booking_date')}")
            continue
        seen.add(key)
        keep.append(t)
    return keep, dups
