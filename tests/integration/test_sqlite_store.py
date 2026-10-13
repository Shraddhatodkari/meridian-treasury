"""SQLite persistent store (v3.3): contract, append-only audit at the database level, migrations, backup/restore,
concurrent writers and survival across application restarts. Needs no server — always runs."""

import json
import os
import sqlite3
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from meridian.data.generator import generate
from meridian.governance.audit import AuditLedger
from meridian.nlp.rag import index_documents, retrieve
from meridian.store.sqlite import MIGRATIONS, SCHEMA_VERSION, SQLiteStore, migrate, restore, verify_file


@pytest.fixture
def db(tmp_path):
    st = SQLiteStore(tmp_path / "meridian.db")
    yield st
    st.close()


def test_store_contract_round_trip(db):
    g = generate(seed=11)
    db.seed(g)
    back = SQLiteStore(db.path).load_group()                    # a second connection reads what the first wrote
    assert len(back.receivables) == len(g.receivables) and back.facility.limit_usd == g.facility.limit_usd
    assert all(r.actual_settle_date is None for r in back.receivables)   # ground truth never persisted
    db.put("users", {"username": "u1", "role": "cfo"})
    db.put("users", {"username": "u1", "role": "treasurer"})    # upsert
    assert db.get("users", "u1")["role"] == "treasurer" and db.get("users", "nobody") is None
    db.save_run({"run_id": "R1", "created_at": "2026-01-01"})
    db.save_run({"run_id": "R2", "created_at": "2026-02-01"})
    assert [r["run_id"] for r in db.list_runs()] == ["R2", "R1"]
    db.put_blob("model/x.bin", b"\x00\x01binary")
    assert db.get_blob("model/x.bin") == b"\x00\x01binary" and db.get_blob("missing") is None
    index_documents(db)
    assert retrieve(db, "minimum liquidity covenant", 1)[0]["clause_id"] == "RCF-22.1"
    with pytest.raises(KeyError):
        db.get("no_such_collection", "k")


def test_audit_is_append_only_in_the_database(db):
    led = AuditLedger(db)
    for i in range(3):
        led.record("tom", "action_approve", {"i": i}, "RUN-1")
    raw = sqlite3.connect(db.path)
    for sql in ("UPDATE audit SET payload='{}' WHERE seq=2", "DELETE FROM audit WHERE seq=2", "DELETE FROM audit"):
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            raw.execute(sql)
    raw.close()
    assert led.verify() == {**led.verify(), "valid": True, "records": 3}
    db.tamper_audit_for_test(2, '{"i": 99}')                   # attacker with file access: detected by the chain
    assert led.verify()["broken_at_seq"] == 2


def test_concurrent_writers_keep_one_chain(db):
    led = AuditLedger(db)

    def work(n):
        for i in range(25):
            led.record(f"w{n}", "tool_call", {"i": i})
    ts = [threading.Thread(target=work, args=(n,)) for n in range(4)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    v = led.verify()
    assert v["valid"] and v["records"] == 100


def test_migrations_are_versioned_idempotent_and_refuse_newer_schemas(tmp_path):
    con = sqlite3.connect(tmp_path / "m.db", isolation_level=None)
    first = migrate(con)
    assert first == {"from": 0, "to": SCHEMA_VERSION, "applied": sorted(MIGRATIONS)}
    assert migrate(con)["applied"] == []                       # re-running is a no-op
    con.execute("INSERT INTO schema_version VALUES (?, 'future')", (SCHEMA_VERSION + 1,))
    with pytest.raises(RuntimeError, match="newer than this release"):
        migrate(con)


def test_backup_restore_round_trip_and_refuses_a_tampered_backup(db, tmp_path):
    led = AuditLedger(db)
    db.put("users", {"username": "carla", "role": "cfo"})
    led.record("carla", "action_approve", {"amount_usd": 12_634_000_000}, "RUN-9")
    rep = db.backup(tmp_path / "backups" / "b1.db")
    assert rep["integrity"] == "ok" and rep["audit_valid"] and rep["audit_records"] == 1
    db.put("users", {"username": "mallory", "role": "admin"})  # change after the backup
    led.record("mallory", "action_approve", {"x": 1})
    db.close()
    out = restore(tmp_path / "backups" / "b1.db", db.path)
    assert out["audit_valid"] and out["audit_records"] == 1 and out["previous_copy"]
    back = SQLiteStore(db.path)
    assert back.get("users", "carla") and back.get("users", "mallory") is None
    back.close()
    bad = SQLiteStore(tmp_path / "bad.db")
    AuditLedger(bad).record("a", "b", {"c": 1})
    AuditLedger(bad).record("a", "b", {"c": 2})
    bad.tamper_audit_for_test(1, '{"c": 999}')
    bad.close()
    with pytest.raises(RuntimeError, match="failed verification"):
        restore(tmp_path / "bad.db", tmp_path / "target.db")
    assert verify_file(tmp_path / "bad.db")["audit_valid"] is False


_STEP = r'''
import json, sys
from fastapi.testclient import TestClient
from meridian.api.main import app
from meridian.security.auth import DEMO_PASSWORD
step, state = sys.argv[1], json.loads(sys.argv[2])
with TestClient(app) as c:
    def h(u):
        t = c.post("/v1/auth/login", json={"username": u, "password": DEMO_PASSWORD}).json()["access_token"]
        return {"Authorization": "Bearer " + t}
    if step == "1":
        run = c.post("/v1/public/runs", json={"cik": 18230}, headers=h("ana")).json()
        rec = next(r for r in run["recommendations"] if r["approval_required"] == "cfo")
        assert c.post(f"/v1/recommendations/{rec['rec_id']}/submit", json={"comment": "restart test"},
                      headers=h("ana")).status_code == 200
        state = {"run_id": run["run_id"], "rec": rec["rec_id"], "action": rec["action_ids"][0]}
    elif step == "2":
        r = c.post(f"/v1/runs/{state['run_id']}/actions/{state['action']}/decision",
                   json={"decision": "APPROVE", "comment": "approved after restart"}, headers=h("carla"))
        assert r.status_code == 200, r.text
    else:
        hi = h("iris")
        runs = {x["run_id"] for x in c.get("/v1/runs", headers=hi).json()}
        run = c.get(f"/v1/runs/{state['run_id']}", headers=hi).json()
        act = next(a for a in run["actions"] if a["action_id"] == state["action"])
        users = c.get("/v1/admin/users", headers=h("ada")).json()
        state = {"run_found": state["run_id"] in runs, "action_status": act["status"],
                 "approved_by": (act.get("decision") or {}).get("by") or act.get("approved_by"),
                 "audit": c.get("/v1/governance/audit/verify", headers=hi).json(), "users": len(users)}
    print(json.dumps(state))
'''


def test_users_runs_approvals_and_audit_survive_three_restarts(tmp_path):
    src = str(Path(__file__).resolve().parents[2] / "src")         # child processes must import THIS source tree
    env = {**os.environ, "MERIDIAN_STORE": "sqlite", "MERIDIAN_SQLITE_PATH": str(tmp_path / "meridian.db"),
           "MERIDIAN_MODE": "demo", "MERIDIAN_WARMUP": "0", "MERIDIAN_STARTUP_REVIEW": "0",
           "PYTHONPATH": src + os.pathsep + os.environ.get("PYTHONPATH", "")}
    state = "{}"
    for step in ("1", "2", "3"):                                # three separate processes = two restarts
        p = subprocess.run([sys.executable, "-c", _STEP, step, state], env=env, capture_output=True, text=True,  # noqa: S603
                           timeout=300)
        assert p.returncode == 0, p.stderr[-3000:]
        state = p.stdout.strip().splitlines()[-1]
    out = json.loads(state)
    assert out["run_found"] and out["action_status"] == "APPROVED"
    assert out["audit"]["valid"] and out["audit"]["records"] > 20 and out["users"] >= 6
