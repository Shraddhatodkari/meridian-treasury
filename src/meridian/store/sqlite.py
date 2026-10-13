"""SQLiteStore — free, zero-install, persistent single-node store (Python's built-in sqlite3).

Why: the evaluation deployment used to keep everything in process memory, so users, runs, approvals, decisions and
the audit chain vanished on restart. SQLite needs no server, no Docker and no licence, and survives restarts.

* Same `TreasuryStore` interface as MemoryStore / MongoStore, so nothing else changes.
* Versioned schema migrations (`schema_version` table); applied automatically and idempotently at start-up and by
  `meridian db migrate`.
* Audit records are append-only AT THE DATABASE LEVEL: triggers abort any UPDATE or DELETE on `audit`.
  (Someone with direct file access can still rewrite the file — the external chain anchor detects that.)
* WAL journal, busy timeout and a process-local lock: safe for the threaded API server; for several API replicas on
  different machines use MongoDB.
* Online backup (`sqlite3.Connection.backup`) and verified restore: see `backup()` / `restore()`.
"""

from __future__ import annotations

import copy
import json
import shutil
import sqlite3
import threading
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import numpy as np

from meridian.data.generator import GroupData
from meridian.domain.models import Facility, Organization
from meridian.store.base import _COLLECTIONS, DOC_KEYS, AuditConflict, TreasuryStore, _cosine_top_k

SCHEMA_VERSION = 1
MIGRATIONS: dict[int, list[str]] = {
    1: [
        "CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)",
        "CREATE TABLE IF NOT EXISTS ledger (collection TEXT NOT NULL, pos INTEGER NOT NULL, doc TEXT NOT NULL, "
        "PRIMARY KEY (collection, pos))",
        "CREATE TABLE IF NOT EXISTS docs (collection TEXT NOT NULL, key TEXT NOT NULL, doc TEXT NOT NULL, "
        "updated_at TEXT NOT NULL, PRIMARY KEY (collection, key))",
        "CREATE TABLE IF NOT EXISTS blobs (name TEXT PRIMARY KEY, data BLOB NOT NULL, updated_at TEXT NOT NULL)",
        "CREATE TABLE IF NOT EXISTS chunks (chunk_id TEXT PRIMARY KEY, doc TEXT NOT NULL)",
        "CREATE TABLE IF NOT EXISTS audit (seq INTEGER PRIMARY KEY, ts TEXT NOT NULL, run_id TEXT, actor TEXT NOT NULL, "
        "action TEXT NOT NULL, payload TEXT NOT NULL, prev_hash TEXT NOT NULL, hash TEXT NOT NULL, v INTEGER NOT NULL DEFAULT 1)",
        "CREATE INDEX IF NOT EXISTS audit_run ON audit(run_id)",
        "CREATE TRIGGER IF NOT EXISTS audit_no_update BEFORE UPDATE ON audit "
        "BEGIN SELECT RAISE(ABORT, 'audit ledger is append-only'); END",
        "CREATE TRIGGER IF NOT EXISTS audit_no_delete BEFORE DELETE ON audit "
        "BEGIN SELECT RAISE(ABORT, 'audit ledger is append-only'); END",
    ],
}


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _connect(path: str | Path) -> sqlite3.Connection:
    con = sqlite3.connect(str(path), check_same_thread=False, timeout=10, isolation_level=None)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    con.execute("PRAGMA foreign_keys=ON")
    con.execute("PRAGMA busy_timeout=10000")
    return con


def migrate(con: sqlite3.Connection) -> dict:
    """Apply pending migrations in order, each in its own transaction. Idempotent."""
    con.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)")
    have = con.execute("SELECT COALESCE(MAX(version), 0) FROM schema_version").fetchone()[0]
    if have > SCHEMA_VERSION:
        raise RuntimeError(f"database schema v{have} is newer than this release (v{SCHEMA_VERSION}); "
                           "upgrade Meridian or restore a matching backup")
    applied = []
    for v in sorted(k for k in MIGRATIONS if k > have):
        con.execute("BEGIN IMMEDIATE")
        try:
            for stmt in MIGRATIONS[v]:
                con.execute(stmt)
            con.execute("INSERT INTO schema_version VALUES (?, ?)", (v, _now()))
            con.execute("COMMIT")
        except Exception:
            con.execute("ROLLBACK")
            raise
        applied.append(v)
    return {"from": have, "to": max([have, *applied]), "applied": applied}


class SQLiteStore(TreasuryStore):
    kind = "sqlite"

    def __init__(self, path: str | Path):
        self.path = Path(path)
        if str(path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.con = _connect(path)
        self.migration = migrate(self.con)
        self._cache: GroupData | None = None

    # ---------------------------------------------------------------- helpers
    def _q(self, sql: str, args: tuple = ()) -> list[tuple]:
        with self._lock:
            return self.con.execute(sql, args).fetchall()

    def _tx(self, fn):
        with self._lock:
            self.con.execute("BEGIN IMMEDIATE")
            try:
                out = fn(self.con)
                self.con.execute("COMMIT")
                return out
            except Exception:
                self.con.execute("ROLLBACK")
                raise

    def ping(self) -> dict:
        ok = self._q("PRAGMA quick_check")[0][0]
        return {"ok": 1 if ok == "ok" else 0, "schema_version": self.schema_version(), "path": str(self.path)}

    def schema_version(self) -> int:
        return self._q("SELECT COALESCE(MAX(version), 0) FROM schema_version")[0][0]

    # ---------------------------------------------------------------- company ledgers
    def is_seeded(self) -> bool:
        return bool(self._q("SELECT 1 FROM meta WHERE key='group'"))

    def seed(self, g: GroupData) -> None:
        g = copy.deepcopy(g)
        for r in g.receivables:
            r.actual_settle_date = None              # ground-truth simulation fields are never stored

        def write(con):
            con.execute("DELETE FROM ledger")
            for name in _COLLECTIONS:
                con.executemany("INSERT INTO ledger VALUES (?, ?, ?)",
                                [(name, i, json.dumps(o.model_dump(mode="json")))
                                 for i, o in enumerate(getattr(g, name))])
            meta = {"as_of": str(g.as_of), "fx": g.fx, "facility": g.facility.model_dump(mode="json"),
                    "classification": g.classification, "tenant": g.tenant,
                    "organization": g.organization.model_dump(mode="json") if g.organization else None}
            con.execute("INSERT OR REPLACE INTO meta VALUES ('group', ?)", (json.dumps(meta),))
        self._tx(write)
        self._cache = None

    def load_group(self) -> GroupData | None:
        if self._cache is None:
            row = self._q("SELECT value FROM meta WHERE key='group'")
            if not row:
                return None
            meta = json.loads(row[0][0])
            g = GroupData(as_of=date.fromisoformat(meta["as_of"]), fx=meta["fx"], facility=Facility(**meta["facility"]),
                          classification=meta.get("classification", "CLIENT_DATA"), tenant=meta.get("tenant", ""),
                          organization=Organization(**meta["organization"]) if meta.get("organization") else None)
            for name, model in _COLLECTIONS.items():
                rows = self._q("SELECT doc FROM ledger WHERE collection=? ORDER BY pos", (name,))
                setattr(g, name, [model(**json.loads(r[0])) for r in rows])
            self._cache = g
        return self._cache

    # ---------------------------------------------------------------- RAG chunks
    def upsert_chunks(self, chunks: list[dict]) -> None:
        self._tx(lambda con: con.executemany("INSERT OR REPLACE INTO chunks VALUES (?, ?)",
                                             [(c["chunk_id"], json.dumps(c, default=str)) for c in chunks]))

    def vector_search(self, query_vec: list[float], k: int = 4) -> list[dict]:
        docs = [json.loads(r[0]) for r in self._q("SELECT doc FROM chunks")]
        return _cosine_top_k(np.array(query_vec), docs, k)

    def chunk_count(self) -> int:
        return self._q("SELECT COUNT(*) FROM chunks")[0][0]

    def all_chunks(self) -> list[dict]:
        return [{k: v for k, v in json.loads(r[0]).items() if k != "embedding"} for r in self._q("SELECT doc FROM chunks")]

    # ---------------------------------------------------------------- documents
    def put(self, collection: str, doc: dict) -> None:
        key = str(doc[DOC_KEYS[collection]])
        self._tx(lambda con: con.execute("INSERT OR REPLACE INTO docs VALUES (?, ?, ?, ?)",
                                         (collection, key, json.dumps(doc, default=str), _now())))

    def save_run_cas(self, run: dict, expected_rev: int) -> bool:
        """Atomic across processes: read-compare-write inside one BEGIN IMMEDIATE transaction (SQLite write lock)."""
        def fn(con):
            r = con.execute("SELECT doc FROM docs WHERE collection='runs' AND key=?", (str(run["run_id"]),)).fetchall()
            if r and int(json.loads(r[0][0]).get("rev", 0)) != expected_rev:
                return False
            con.execute("INSERT OR REPLACE INTO docs VALUES (?, ?, ?, ?)",
                        ("runs", str(run["run_id"]), json.dumps(run, default=str), _now()))
            return True
        return self._tx(fn)

    def get(self, collection: str, key: str) -> dict | None:
        DOC_KEYS[collection]                                  # unknown collection -> KeyError, like the other stores
        r = self._q("SELECT doc FROM docs WHERE collection=? AND key=?", (collection, str(key)))
        return json.loads(r[0][0]) if r else None

    def find(self, collection: str, limit: int = 500, sort_desc: str | None = None) -> list[dict]:
        docs = [json.loads(r[0]) for r in self._q("SELECT doc FROM docs WHERE collection=?", (collection,))]
        if sort_desc:
            docs.sort(key=lambda d: d.get(sort_desc) or "", reverse=True)
        return docs[:limit]

    # ---------------------------------------------------------------- blobs
    def put_blob(self, name: str, data: bytes) -> None:
        self._tx(lambda con: con.execute("INSERT OR REPLACE INTO blobs VALUES (?, ?, ?)", (name, data, _now())))

    def get_blob(self, name: str) -> bytes | None:
        r = self._q("SELECT data FROM blobs WHERE name=?", (name,))
        return bytes(r[0][0]) if r else None

    # ---------------------------------------------------------------- audit (append-only)
    def append_audit(self, rec: dict, expected_seq: int) -> None:
        def ins(con):
            last = con.execute("SELECT COALESCE(MAX(seq), 0) FROM audit").fetchone()[0]
            if last + 1 != expected_seq:
                raise AuditConflict(expected_seq)
            con.execute("INSERT INTO audit (seq, ts, run_id, actor, action, payload, prev_hash, hash, v) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (expected_seq, rec["ts"], rec.get("run_id"), rec["actor"], rec["action"], rec["payload"],
                         rec["prev_hash"], rec["hash"], rec.get("v", 1)))
        try:
            self._tx(ins)
        except sqlite3.IntegrityError as e:
            raise AuditConflict(expected_seq) from e

    _COLS = ("seq", "ts", "run_id", "actor", "action", "payload", "prev_hash", "hash", "v")

    def _rows(self, sql: str, args: tuple = ()) -> list[dict]:
        return [dict(zip(self._COLS, r, strict=True)) for r in self._q(sql, args)]

    _SELECT = "SELECT seq, ts, run_id, actor, action, payload, prev_hash, hash, v FROM audit"   # literal: no injection

    def last_audit(self) -> dict | None:
        r = self._rows(self._SELECT + " ORDER BY seq DESC LIMIT 1")
        return r[0] if r else None

    def audit_records(self, run_id: str | None = None) -> list[dict]:
        if run_id is None:
            return self._rows(self._SELECT + " ORDER BY seq")
        return self._rows(self._SELECT + " WHERE run_id=? ORDER BY seq", (run_id,))

    def tamper_audit_for_test(self, seq: int, payload: str) -> None:
        """TEST ONLY: simulates an attacker with direct file access (drops the triggers, edits, restores them)."""
        def tamper(con):
            con.execute("DROP TRIGGER audit_no_update")
            con.execute("UPDATE audit SET payload=? WHERE seq=?", (payload, seq))
            con.execute(MIGRATIONS[1][7])
        self._tx(tamper)

    # ---------------------------------------------------------------- operations
    def counts(self) -> dict:
        return {"users": self._q("SELECT COUNT(*) FROM docs WHERE collection='users'")[0][0],
                "runs": self._q("SELECT COUNT(*) FROM docs WHERE collection='runs'")[0][0],
                "recommendations": self._q("SELECT COUNT(*) FROM docs WHERE collection='recommendations'")[0][0],
                "audit_records": self._q("SELECT COUNT(*) FROM audit")[0][0],
                "public_companies": self._q("SELECT COUNT(*) FROM docs WHERE collection='public_companies'")[0][0]}

    def backup(self, dest: str | Path) -> dict:
        """Consistent online backup (safe while the API is running)."""
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        out = sqlite3.connect(str(dest))
        with self._lock:
            self.con.backup(out)
        out.close()
        return {"backup": str(dest), "bytes": dest.stat().st_size, **verify_file(dest)}

    def close(self) -> None:
        with self._lock:
            self.con.close()


def verify_file(path: str | Path) -> dict[str, Any]:
    """Integrity check of a database file: SQLite structure, schema version and the audit hash chain."""
    from meridian.governance.audit import AuditLedger

    st = SQLiteStore.__new__(SQLiteStore)       # open without running migrations on a file we are only checking
    st.path, st._lock, st._cache = Path(path), threading.RLock(), None
    st.con = sqlite3.connect(str(path), check_same_thread=False)
    try:
        integrity = st.con.execute("PRAGMA integrity_check").fetchone()[0]
        version = st.con.execute("SELECT COALESCE(MAX(version), 0) FROM schema_version").fetchone()[0]
        chain = AuditLedger(st).verify()
        return {"integrity": integrity, "schema_version": version, "audit_valid": chain["valid"],
                "audit_records": chain["records"]}
    finally:
        st.con.close()


def restore(backup: str | Path, target: str | Path) -> dict:
    """Restore a backup over `target` (the API must be stopped). The backup is verified first; the current file is
    kept as `<target>.pre-restore-<timestamp>` so the restore itself can be rolled back."""
    backup, target = Path(backup), Path(target)
    v = verify_file(backup)
    if v["integrity"] != "ok" or not v["audit_valid"]:
        raise RuntimeError(f"backup failed verification, not restored: {v}")
    kept = None
    if target.exists():
        kept = target.with_name(f"{target.name}.pre-restore-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}")
        shutil.copy2(target, kept)
        for side in (target.with_name(target.name + "-wal"), target.with_name(target.name + "-shm")):
            if side.exists():
                side.unlink()
    shutil.copy2(backup, target)
    return {"restored": str(target), "previous_copy": str(kept) if kept else None, **verify_file(target)}
