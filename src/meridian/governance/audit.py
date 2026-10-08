"""Tamper-evident audit ledger (SHA-256 hash chain) persisted in MongoDB.

Multi-replica safe: each append claims sequence number head+1 under a unique index; a concurrent
writer gets AuditConflict, re-reads the new head and retries, so the chain never forks.
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


def _digest(prev: str, ts: str, run_id: str | None, actor: str, action: str, payload: str) -> str:
    return hashlib.sha256("|".join([prev, ts, run_id or "", actor, action, payload]).encode()).hexdigest()


class AuditLedger:
    def __init__(self, store):
        self.store = store

    def record(self, actor: str, action: str, payload: dict[str, Any], run_id: str | None = None) -> str:
        body = json.dumps(payload, sort_keys=True, default=str)
        with _lock:
            for _ in range(20):
                last = self.store.last_audit()
                prev, seq = (last["hash"], last["seq"] + 1) if last else (GENESIS, 1)
                ts = datetime.now(UTC).isoformat()
                h = _digest(prev, ts, run_id, actor, action, body)
                try:
                    self.store.append_audit({"ts": ts, "run_id": run_id, "actor": actor, "action": action,
                                             "payload": body, "prev_hash": prev, "hash": h}, expected_seq=seq)
                    return h
                except AuditConflict:
                    continue
        raise RuntimeError("audit ledger contention: could not append after 20 attempts")

    def verify(self) -> dict[str, Any]:
        prev, rows = GENESIS, self.store.audit_records()
        for r in rows:
            if r["prev_hash"] != prev or r["hash"] != _digest(prev, r["ts"], r["run_id"], r["actor"], r["action"],
                                                                r["payload"]):
                return {"valid": False, "records": len(rows), "broken_at_seq": r["seq"]}
            prev = r["hash"]
        return {"valid": True, "records": len(rows), "head": prev}

    def records(self, run_id: str | None = None, limit: int = 500) -> list[dict[str, Any]]:
        rows = self.store.audit_records(run_id)[-limit:]
        return [{"seq": r["seq"], "ts": r["ts"], "run_id": r["run_id"], "actor": r["actor"], "action": r["action"],
                 "payload": json.loads(r["payload"]), "hash": r["hash"], "prev_hash": r["prev_hash"]} for r in rows]

    def for_run(self, run_id: str) -> list[dict[str, Any]]:
        return [{**r, "hash": r["hash"][:16]} for r in self.records(run_id)]
