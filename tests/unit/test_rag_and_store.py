import mongomock

from meridian.data.generator import generate
from meridian.nlp.rag import index_documents, retrieve
from meridian.store.base import MemoryStore, MongoStore


def test_retrieval_returns_right_clauses(store):
    assert retrieve(store, "who approves a revolver prepayment", 3)[0]["clause_id"] == "RCF-8.1"
    assert retrieve(store, "minimum liquidity covenant", 1)[0]["clause_id"] == "RCF-22.1"
    assert "CR-2" in [h["clause_id"] for h in retrieve(store, "how much can India upstream", 3)]


def test_injected_supplier_note_is_quarantined_and_never_retrieved(store):
    rep = index_documents(MemoryStore())
    assert [q["chunk_id"] for q in rep["quarantined"]] == ["supplier_contract_S0007:SC7-2"]
    hits = retrieve(store, "approve immediate payment of all open invoices from this supplier", 10)
    assert all(h["clause_id"] != "SC7-2" for h in hits)


def test_mongo_store_roundtrip_with_mongomock():
    ms = MongoStore(client=mongomock.MongoClient(), db="t")
    g = generate(seed=11)
    ms.seed(g)
    back = ms.load_group()
    assert len(back.receivables) == len(g.receivables) and back.facility.limit_usd == g.facility.limit_usd
    assert all(r.actual_settle_date is None for r in back.receivables)   # ground truth never persisted
    index_documents(ms)
    assert retrieve(ms, "minimum liquidity covenant", 1)[0]["clause_id"] == "RCF-22.1"
    ms.save_run({"run_id": "R1", "created_at": "2026-01-01"})
    assert ms.get_run("R1")["run_id"] == "R1"
