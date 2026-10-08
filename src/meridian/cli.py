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
    out = asyncio.run(c.structured("connectivity_check", "Return a finding with headline 'ok'.", {"ping": True},
                                   AgentFinding, lambda e: None))
    print(f"{c.mode} / {c.settings.llm_model}: structured output OK → {out.headline!r}")
    return 0


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
    a = ap.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
