"""Real MongoDB integration (not mongomock). Runs when MERIDIAN_TEST_MONGODB_URI points at a mongod / Atlas
cluster; skipped otherwise. Exercises the production data path end-to-end:

export (ERP CSV + camt.053) → validated ingestion → persisted ledgers / provenance → model registry in GridFS
→ agent run → approval → hash-chained audit with concurrent writers (two "replicas")."""

import os
import threading
import uuid

import pytest

URI = os.getenv("MERIDIAN_TEST_MONGODB_URI")
pytestmark = pytest.mark.skipif(not URI, reason="set MERIDIAN_TEST_MONGODB_URI to run against a real MongoDB")


@pytest.fixture(scope="module")
def mongo_store(tmp_path_factory):
    from meridian.data.generator import generate
    from meridian.ingest.pipeline import export_package, ingest_directory
    from meridian.store.base import MongoStore

    db = f"meridian_test_{uuid.uuid4().hex[:8]}"
    store = MongoStore(URI, db)
    pkg = export_package(generate(seed=7), tmp_path_factory.mktemp("pkg"))
    rep = ingest_directory(pkg, store)
    assert rep.loaded and not rep.errors
    yield store
    store.client.drop_database(db)


def test_ingested_ledgers_round_trip(mongo_store):
    g = mongo_store.load_group()
    assert g.classification == "SYNTHETIC" and len(g.accounts) == 37 and g.facility.margin_bps == 250
    ids = {d["source_id"] for d in mongo_store.find("data_sources")}
    assert {"company.bank_balances", "company.receivables", "company.payables"} <= ids


def test_model_registry_persists_artifact_in_gridfs(mongo_store):
    from meridian.ml.ar_model import _CACHE, fingerprint, load_or_train

    g = mongo_store.load_group()
    bundle = load_or_train(g.receivables, g.customers, g.as_of, fx=g.fx, store=mongo_store)
    _CACHE.clear()                                   # simulate a fresh replica: must load from MongoDB, not retrain
    again = load_or_train(g.receivables, g.customers, g.as_of, fx=g.fx, store=mongo_store)
    assert again["card"]["model_id"] == bundle["card"]["model_id"] == f"ar-delay:{fingerprint(g.receivables, g.as_of)}"
    assert mongo_store.get("model_registry", bundle["card"]["model_id"])["status"] == "ACTIVE"


def test_audit_chain_is_safe_with_concurrent_replicas(mongo_store):
    from meridian.governance.audit import AuditLedger
    from meridian.store.base import MongoStore

    replica_b = MongoStore(URI, mongo_store.db.name)          # second client = second API replica
    errors = []

    def write(store, n):
        try:
            for i in range(n):
                AuditLedger(store).record("replica", "concurrency_test", {"i": i})
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    ts = [threading.Thread(target=write, args=(s, 25)) for s in (mongo_store, replica_b, mongo_store, replica_b)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert not errors
    v = AuditLedger(mongo_store).verify()
    assert v["valid"] and v["records"] >= 100


def test_users_and_runs_persist(mongo_store):
    from meridian.security.auth import authenticate, create_user

    create_user(mongo_store, "carla.cfo", "Carla Mendes", "cfo", "a-long-test-password-123")
    assert authenticate(mongo_store, "carla.cfo", "a-long-test-password-123")["role"] == "cfo"
    mongo_store.save_run({"run_id": "RUN-X", "created_at": "2026-10-06T00:00:00", "actions": []})
    assert mongo_store.get_run("RUN-X")["run_id"] == "RUN-X"


# ------------------------------------------------------------------------------------------------ v3.2: restarts
_PHASE1 = r'''
import json, sys
from fastapi.testclient import TestClient
from meridian.api.main import app
from meridian.security.auth import DEMO_PASSWORD
with TestClient(app) as c:
    tok = c.post("/v1/auth/login", json={"username": "ana", "password": DEMO_PASSWORD}).json()["access_token"]
    h = {"Authorization": "Bearer " + tok}
    run = c.post("/v1/public/runs", json={"cik": 773840}, headers=h).json()   # v3.4: KO no longer triggers a rule
    rec = run["recommendations"][0]["rec_id"]
    assert c.post(f"/v1/recommendations/{rec}/submit", json={"comment": "persist"}, headers=h).status_code == 200
    print(json.dumps({"run_id": run["run_id"], "rec": rec}))
'''
_PHASE2 = r'''
import json, sys
from fastapi.testclient import TestClient
from meridian.api.main import app
from meridian.security.auth import DEMO_PASSWORD
want = json.loads(sys.argv[1])
with TestClient(app) as c:
    tok = c.post("/v1/auth/login", json={"username": "iris", "password": DEMO_PASSWORD}).json()["access_token"]
    h = {"Authorization": "Bearer " + tok}
    runs = {r["run_id"] for r in c.get("/v1/runs", headers=h).json()}
    rec = c.get(f"/v1/recommendations/{want['rec']}", headers=h).json()
    v = c.get("/v1/governance/audit/verify", headers=h).json()
    print(json.dumps({"run_found": want["run_id"] in runs, "rec_status": rec.get("status"), "audit": v}))
'''


def test_runs_decisions_and_audit_survive_an_application_restart(tmp_path):
    """Two separate processes = an application restart. Everything written by the first must be read by the second,
    and the audit chain must still verify."""
    import json as _json
    import subprocess
    import sys
    from pathlib import Path

    src = str(Path(__file__).resolve().parents[2] / "src")         # child processes must import THIS source tree
    env = {**os.environ, "MERIDIAN_MONGODB_URI": URI, "MERIDIAN_MONGODB_DB": f"meridian_restart_{uuid.uuid4().hex[:8]}",
           "MERIDIAN_STORE": "mongodb", "MERIDIAN_MODE": "demo", "MERIDIAN_WARMUP": "0", "MERIDIAN_STARTUP_REVIEW": "0",
           "PYTHONPATH": src + os.pathsep + os.environ.get("PYTHONPATH", "")}
    try:
        p1 = subprocess.run([sys.executable, "-c", _PHASE1], env=env, capture_output=True, text=True, timeout=300)  # noqa: S603
        assert p1.returncode == 0, p1.stderr[-2000:]
        first = p1.stdout.strip().splitlines()[-1]
        p2 = subprocess.run([sys.executable, "-c", _PHASE2, first], env=env, capture_output=True, text=True, timeout=300)  # noqa: S603
        assert p2.returncode == 0, p2.stderr[-2000:]
        out = _json.loads(p2.stdout.strip().splitlines()[-1])
        assert out["run_found"] and out["rec_status"] == "IN_REVIEW" and out["audit"]["valid"] and out["audit"]["records"] > 10
    finally:
        from pymongo import MongoClient
        MongoClient(URI).drop_database(env["MERIDIAN_MONGODB_DB"])


ADMIN_URI = os.getenv("MERIDIAN_TEST_MONGODB_ADMIN_URI")   # an auth-enabled mongod, root credentials


@pytest.mark.skipif(not ADMIN_URI, reason="set MERIDIAN_TEST_MONGODB_ADMIN_URI (auth-enabled mongod) to run")
def test_app_role_can_append_but_never_alter_audit():
    import subprocess
    import sys
    from urllib.parse import urlparse

    from pymongo import MongoClient
    from pymongo.errors import OperationFailure

    from meridian.governance.audit import AuditLedger
    from meridian.store.base import MongoStore

    db = f"meridian_role_{uuid.uuid4().hex[:8]}"
    pw = "App-Password-" + uuid.uuid4().hex[:12]
    AuditLedger(MongoStore(ADMIN_URI, db)).record("setup", "seed", {"ok": True})
    r = subprocess.run([sys.executable, "scripts/mongo_least_privilege.py", "--admin-uri", ADMIN_URI, "--db", db],  # noqa: S603
                       env={**os.environ, "MERIDIAN_APP_DB_PASSWORD": pw}, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stdout + r.stderr
    host = urlparse(ADMIN_URI).netloc.split("@")[-1]
    app_uri = f"mongodb://meridian_app:{pw}@{host}/{db}?authSource={db}"
    led = AuditLedger(MongoStore(app_uri, db))
    led.record("tom", "action_approve", {"x": 1})
    assert led.verify()["valid"]
    coll = MongoClient(app_uri)[db]["audit"]
    for attempt in (lambda: coll.update_one({"seq": 1}, {"$set": {"payload": "{}"}}),
                    lambda: coll.delete_one({"seq": 1}), lambda: coll.drop()):
        with pytest.raises(OperationFailure) as e:
            attempt()
        assert e.value.code == 13                                  # Unauthorized
    MongoClient(ADMIN_URI).drop_database(db)
