"""Tamper-evident audit ledger (SHA-256 hash chain) persisted in MongoDB.

Multi-replica safe: each append claims sequence number head+1 under a unique index; a concurrent
writer gets AuditConflict, re-reads the new head and retries, so the chain never forks.

Record format v2 (3.2): the digest covers the sequence number and a canonical JSON payload
(sorted keys, compact separators, UTF-8), so editing, deleting, inserting or reordering any record
breaks verification. v1 records (without seq in the digest) still verify.

A hash chain makes tampering DETECTABLE, not impossible: the database account used by the API must
only be able to insert and read the audit collection (see docs/production-runbook.md), and the
chain head should be anchored outside the database (``meridian audit anchor``) so a full rewrite
of the collection is also detectable.
"""

from __future__ import annotations

import hashlib
import json
import threading
from datetime import UTC, datetime
from typing import Any

from meridian.store.base import AuditConflict

GENESIS = "0" * 64
_lock = threading.Lock()


def canonical(payload: dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def _digest(prev: str, ts: str, run_id: str | None, actor: str, action: str, payload: str, seq: int | None = None) -> str:
    parts = [prev, ts, run_id or "", actor, action, payload] + ([str(seq)] if seq is not None else [])
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()


class AuditLedger:
    def __init__(self, store):
        self.store = store

    def record(self, actor: str, action: str, payload: dict[str, Any], run_id: str | None = None) -> str:
        body = canonical(payload)
        with _lock:
            for _ in range(20):
                last = self.store.last_audit()
                prev, seq = (last["hash"], last["seq"] + 1) if last else (GENESIS, 1)
                ts = datetime.now(UTC).isoformat()
                h = _digest(prev, ts, run_id, actor, action, body, seq)
                try:
                    self.store.append_audit({"ts": ts, "run_id": run_id, "actor": actor, "action": action,
                                             "payload": body, "prev_hash": prev, "hash": h, "v": 2}, expected_seq=seq)
                    return h
                except AuditConflict:
                    continue
        raise RuntimeError("audit ledger contention: could not append after 20 attempts")

    def verify(self) -> dict[str, Any]:
        prev, rows = GENESIS, self.store.audit_records()
        for i, r in enumerate(rows, start=1):
            seq = r["seq"] if r.get("v", 1) >= 2 else None
            expect = _digest(prev, r["ts"], r["run_id"], r["actor"], r["action"], r["payload"], seq)
            if r["seq"] != i or r["prev_hash"] != prev or r["hash"] != expect:
                return {"valid": False, "records": len(rows), "broken_at_seq": r["seq"],
                        "reason": "sequence gap or reorder" if r["seq"] != i else
                                  "link to previous record broken" if r["prev_hash"] != prev else "record content altered"}
            prev = r["hash"]
        return {"valid": True, "records": len(rows), "head": prev, "head_seq": len(rows)}

    def anchor(self) -> dict[str, Any]:
        """Chain head to publish OUTSIDE the database (ticket, WORM bucket, signed email). Re-verify later with
        ``verify_anchor``: if someone rewrites the whole collection, the published head no longer appears."""
        v = self.verify()
        return {"head": v.get("head"), "seq": v.get("head_seq"), "valid": v["valid"],
                "anchored_at": datetime.now(UTC).isoformat()}

    def verify_anchor(self, head: str, seq: int) -> bool:
        rows = self.store.audit_records()
        return self.verify()["valid"] and len(rows) >= seq and rows[seq - 1]["hash"] == head

    def records(self, run_id: str | None = None, limit: int = 500) -> list[dict[str, Any]]:
        rows = self.store.audit_records(run_id)[-limit:]
        return [{"seq": r["seq"], "ts": r["ts"], "run_id": r["run_id"], "actor": r["actor"], "action": r["action"],
                 "payload": json.loads(r["payload"]), "hash": r["hash"], "prev_hash": r["prev_hash"]} for r in rows]

    def for_run(self, run_id: str) -> list[dict[str, Any]]:
        return [{**r, "hash": r["hash"][:16]} for r in self.records(run_id)]
