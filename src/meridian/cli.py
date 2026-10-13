"""Meridian operations CLI.

    meridian doctor                         production-readiness report (config, MongoDB, Atlas index, data, users)
    meridian ingest --dir ./load_2026_10_06 validate + load an ERP / camt.053 package (atomic, with provenance)
    meridian export-sample --out ./sample   write the SYNTHETIC demo company in the production ingestion format
    meridian users create --username carla --name "Carla Mendes" --role cfo     (password prompted)
    meridian llm-check                      one real round-trip to the configured Claude provider
"""

from __future__ import annotations

import argparse
import getpass
import json
import sys
from pathlib import Path


def _print(obj) -> None:
    print(json.dumps(obj, indent=2, default=str))


def cmd_doctor(_: argparse.Namespace) -> int:
    from meridian.config import get_settings
    from meridian.readiness import runtime_checks, static_checks, summarize

    s = get_settings()
    checks = static_checks(s)
    try:
        from meridian.store.factory import get_store

        checks += runtime_checks(s, get_store())
    except Exception as e:  # noqa: BLE001
        checks.append({"check": "Store initialisation", "status": "FAIL", "detail": str(e)[:300],
                       "remediation": "See detail"})
    rep = summarize(checks)
    for c in rep["checks"]:
        print(f"[{c['status']:4}] {c['check']}: {c['detail']}" + (f"  → {c['remediation']}" if c["remediation"] else ""))
    print(f"\n{'READY' if rep['ready'] else 'NOT READY'} — {rep['fail']} fail, {rep['warn']} warn ({s.mode} mode)")
    return 0 if rep["ready"] else 1


def cmd_ingest(a: argparse.Namespace) -> int:
    from meridian.governance.audit import AuditLedger
    from meridian.ingest.pipeline import ingest_directory
    from meridian.store.factory import get_store

    store = get_store()
    rep = ingest_directory(Path(a.dir), store)
    AuditLedger(store).record("ingestion", "data_load", {"ingestion_id": rep.ingestion_id, "loaded": rep.loaded,
                                                         "classification": rep.classification, "counts": rep.counts,
                                                         "errors": len(rep.errors)})
    for c in rep.checks:
        print(f"[{c.level}] {c.file}: {c.rule} — {c.detail}")
    print(f"\n{'LOADED' if rep.loaded else 'REJECTED'} {rep.ingestion_id} ({rep.classification}) counts={rep.counts}")
    return 0 if rep.loaded else 2


def cmd_export(a: argparse.Namespace) -> int:
    from meridian.config import get_settings
    from meridian.data.generator import generate
    from meridian.ingest.pipeline import export_package

    s = get_settings()
    out = export_package(generate(seed=s.seed, as_of=s.effective_as_of()), Path(a.out))
    print(f"Wrote SYNTHETIC_SAMPLE package to {out}")
    return 0


def cmd_users(a: argparse.Namespace) -> int:
    from meridian.security.auth import create_user
    from meridian.store.factory import get_store

    pw = a.password or getpass.getpass("Password (min 12 chars): ")
    _print(create_user(get_store(), a.username, a.name, a.role, pw))
    return 0


def cmd_llm_check(_: argparse.Namespace) -> int:
    import asyncio

    from meridian.agents.schemas import AgentFinding
    from meridian.llm.claude import ClaudeClient

    c = ClaudeClient(2)
    if c.mode == "mock":
        print("LLM provider is 'mock' — set MERIDIAN_LLM_PROVIDER to anthropic | vertex | bedrock")
        return 1
    from meridian.llm.claude import SOURCE_LIVE

    def no_fallback(_payload: dict) -> AgentFinding | None:     # connectivity check: no offline answer
        return None

    out = asyncio.run(c.structured("connectivity_check", "Return a finding with headline 'ok'.", {"ping": True},
                                   AgentFinding, no_fallback))
    ev = c.events[-1] if c.events else {}
    if c.last_source.get("connectivity_check") != SOURCE_LIVE or out is None:
        print(f"FAILED: {c.mode} / {c.settings.llm_model} did not return a live structured answer "
              f"({ev.get('source')}: {ev.get('error', 'no detail')}). Claude is NOT active.")
        return 1
    print(f"{c.mode} / {c.settings.llm_model}: live structured output OK → {out.headline!r}; tokens {ev.get('tokens')}")
    return 0


def cmd_db(a: argparse.Namespace) -> int:
    """Database operations for the SQLite store (single node, free). MongoDB uses its own tooling (runbook §6)."""
    from datetime import UTC, datetime

    from meridian.config import get_settings
    from meridian.store import sqlite as sq

    s = get_settings()
    if s.store_kind != "sqlite":
        print(f"store is {s.store_kind}; `meridian db` manages the SQLite store (see docs/production-runbook.md for MongoDB)")
        return 1
    if a.dcmd == "restore":
        _print(sq.restore(a.src, s.sqlite_path))       # the API must be stopped
        return 0
    st = sq.SQLiteStore(s.sqlite_path)                  # opening applies pending migrations
    try:
        if a.dcmd == "status":
            _print({"path": str(st.path), "schema_version": st.schema_version(), "latest_schema": sq.SCHEMA_VERSION,
                    "migration": st.migration, "counts": st.counts(), **sq.verify_file(st.path)})
        elif a.dcmd == "migrate":
            _print({"schema_version": st.schema_version(), "applied_now": st.migration["applied"]})
        elif a.dcmd == "backup":
            out = a.out or f"backups/meridian-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}.db"
            _print(st.backup(out))
        return 0
    finally:
        st.close()


def cmd_audit(a: argparse.Namespace) -> int:
    from meridian.governance.audit import AuditLedger
    from meridian.store.factory import get_store

    led = AuditLedger(get_store())
    if a.acmd == "verify":
        v = led.verify()
        _print(v)
        return 0 if v["valid"] else 2
    if a.acmd == "anchor":
        _print(led.anchor())
        return 0
    ok = led.verify_anchor(a.head, a.seq)
    print("anchor MATCHES the ledger" if ok else "anchor DOES NOT MATCH — the ledger was altered or rewritten")
    return 0 if ok else 2


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="meridian")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("doctor").set_defaults(fn=cmd_doctor)
    p = sub.add_parser("ingest")
    p.add_argument("--dir", required=True)
    p.set_defaults(fn=cmd_ingest)
    p = sub.add_parser("export-sample")
    p.add_argument("--out", required=True)
    p.set_defaults(fn=cmd_export)
    p = sub.add_parser("users")
    usub = p.add_subparsers(dest="ucmd", required=True)
    u = usub.add_parser("create")
    u.add_argument("--username", required=True)
    u.add_argument("--name", required=True)
    u.add_argument("--role", required=True)
    u.add_argument("--password", help="omit to be prompted (recommended)")
    u.set_defaults(fn=cmd_users)
    sub.add_parser("llm-check").set_defaults(fn=cmd_llm_check)
    p = sub.add_parser("db", help="SQLite store: status, migrate, backup, restore")
    dsub = p.add_subparsers(dest="dcmd", required=True)
    dsub.add_parser("status").set_defaults(fn=cmd_db)
    dsub.add_parser("migrate").set_defaults(fn=cmd_db)
    b = dsub.add_parser("backup")
    b.add_argument("--out", help="destination file (default backups/meridian-<UTC timestamp>.db)")
    b.set_defaults(fn=cmd_db)
    r = dsub.add_parser("restore", help="stop the API first; the backup is verified and the current file kept")
    r.add_argument("--from", dest="src", required=True)
    r.set_defaults(fn=cmd_db)
    p = sub.add_parser("audit", help="verify the hash chain, print an external anchor, or check an anchor")
    asub = p.add_subparsers(dest="acmd", required=True)
    asub.add_parser("verify").set_defaults(fn=cmd_audit)
    asub.add_parser("anchor").set_defaults(fn=cmd_audit)
    c = asub.add_parser("check-anchor")
    c.add_argument("--head", required=True)
    c.add_argument("--seq", required=True, type=int)
    c.set_defaults(fn=cmd_audit)
    a = ap.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
