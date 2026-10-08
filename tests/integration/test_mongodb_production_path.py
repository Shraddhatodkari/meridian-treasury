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
