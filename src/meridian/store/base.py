"""Persistence layer.

* MongoStore  — production. MongoDB Atlas holds the ledgers, runs, approvals, audit chain, users, model
                registry (GridFS), data-source provenance, relationship graph, document chunks and their
                embeddings (Atlas Vector Search `$vectorSearch`). Nothing required for correctness lives in
                process memory, so any number of API replicas can serve the same tenant.
* MemoryStore — same interface for tests and the offline demo.

Ground-truth simulation fields (e.g. actual settlement dates of open invoices) are never stored.
"""

from __future__ import annotations

import copy
from abc import ABC, abstractmethod
from datetime import date
from typing import Any

import numpy as np

from meridian.data.generator import GroupData
from meridian.domain.models import (
    Bank,
    BankAccount,
    BankTransaction,
    BusinessUnit,
    Covenant,
    Customer,
    DebtFacility,
    Entity,
    Facility,
    Organization,
    Payable,
    Receivable,
    RecurringFlow,
    RiskEvent,
    Supplier,
)

_COLLECTIONS = {"entities": Entity, "accounts": BankAccount, "customers": Customer, "suppliers": Supplier,
                "receivables": Receivable, "payables": Payable, "recurring": RecurringFlow,
                "risk_events": RiskEvent, "business_units": BusinessUnit, "banks": Bank,
                "debt_facilities": DebtFacility, "covenants": Covenant, "bank_transactions": BankTransaction}
# Generic document collections and their primary keys
DOC_KEYS = {"runs": "run_id", "data_sources": "source_id", "users": "username", "graph": "graph_id",
            "model_registry": "model_id", "ingestions": "ingestion_id", "datasets": "dataset_id",
            "recommendations": "rec_id", "revoked_tokens": "jti", "connectors": "connector_id",
            "jobs": "job_id", "settings": "key", "public_companies": "cik_key"}


def _cosine_top_k(query: np.ndarray, docs: list[dict], k: int) -> list[dict]:
    if not docs:
        return []
    M = np.array([d["embedding"] for d in docs], dtype=float)
    q = query / (np.linalg.norm(query) or 1.0)
    sims = M @ q / np.maximum(np.linalg.norm(M, axis=1), 1e-12)
    order = np.argsort(-sims)[:k]
    return [{**{kk: vv for kk, vv in docs[i].items() if kk != "embedding"}, "score": round(float(sims[i]), 4)}
            for i in order]


class AuditConflict(RuntimeError):
    """Another writer appended to the audit chain concurrently; caller re-reads the head and retries."""


class TreasuryStore(ABC):
    kind = "abstract"

    # ---- company data
    @abstractmethod
    def is_seeded(self) -> bool: ...
    @abstractmethod
    def seed(self, g: GroupData) -> None: ...
    @abstractmethod
    def load_group(self) -> GroupData: ...

    # ---- document chunks + vectors
    @abstractmethod
    def upsert_chunks(self, chunks: list[dict]) -> None: ...
    @abstractmethod
    def vector_search(self, query_vec: list[float], k: int = 4) -> list[dict]: ...
    @abstractmethod
    def chunk_count(self) -> int: ...
    @abstractmethod
    def all_chunks(self) -> list[dict]: ...

    # ---- generic documents (runs, data_sources, users, graph, model_registry, ingestions)
    @abstractmethod
    def put(self, collection: str, doc: dict) -> None: ...
    @abstractmethod
    def get(self, collection: str, key: str) -> dict | None: ...
    @abstractmethod
    def find(self, collection: str, limit: int = 500, sort_desc: str | None = None) -> list[dict]: ...

    # ---- binary model artefacts
    @abstractmethod
    def put_blob(self, name: str, data: bytes) -> None: ...
    @abstractmethod
    def get_blob(self, name: str) -> bytes | None: ...

    # ---- audit (append-only, optimistic concurrency on sequence number)
    @abstractmethod
    def append_audit(self, rec: dict, expected_seq: int) -> None: ...
    @abstractmethod
    def last_audit(self) -> dict | None: ...
    @abstractmethod
    def audit_records(self, run_id: str | None = None) -> list[dict]: ...
    @abstractmethod
    def tamper_audit_for_test(self, seq: int, payload: str) -> None: ...

    # ---- convenience wrappers kept for backwards compatibility
    def save_run(self, run: dict) -> None:
        self.put("runs", run)

    def get_run(self, run_id: str) -> dict | None:
        return self.get("runs", run_id)

    def list_runs(self, limit: int = 50) -> list[dict]:
        return self.find("runs", limit, sort_desc="created_at")


class MemoryStore(TreasuryStore):
    kind = "memory"

    def __init__(self):
        self._group: GroupData | None = None
        self._chunks: dict[str, dict] = {}
        self._docs: dict[str, dict[str, dict]] = {c: {} for c in DOC_KEYS}
        self._blobs: dict[str, bytes] = {}
        self._audit: list[dict] = []

    def is_seeded(self) -> bool:
        return self._group is not None

    def seed(self, g: GroupData) -> None:
        g = copy.deepcopy(g)
        for r in g.receivables:
            r.actual_settle_date = None
        self._group = g

    def load_group(self) -> GroupData:
        return self._group

    def upsert_chunks(self, chunks: list[dict]) -> None:
        for c in chunks:
            self._chunks[c["chunk_id"]] = c

    def vector_search(self, query_vec: list[float], k: int = 4) -> list[dict]:
        return _cosine_top_k(np.array(query_vec), list(self._chunks.values()), k)

    def chunk_count(self) -> int:
        return len(self._chunks)

    def all_chunks(self) -> list[dict]:
        return [{k: v for k, v in c.items() if k != "embedding"} for c in self._chunks.values()]

    def put(self, collection: str, doc: dict) -> None:
        self._docs[collection][doc[DOC_KEYS[collection]]] = copy.deepcopy(doc)

    def get(self, collection: str, key: str) -> dict | None:
        d = self._docs[collection].get(key)
        return copy.deepcopy(d) if d else None

    def find(self, collection: str, limit: int = 500, sort_desc: str | None = None) -> list[dict]:
        docs = [copy.deepcopy(d) for d in self._docs[collection].values()]
        if sort_desc:
            docs.sort(key=lambda d: d.get(sort_desc) or "", reverse=True)
        return docs[:limit]

    def put_blob(self, name: str, data: bytes) -> None:
        self._blobs[name] = data

    def get_blob(self, name: str) -> bytes | None:
        return self._blobs.get(name)

    def append_audit(self, rec: dict, expected_seq: int) -> None:
        if expected_seq != len(self._audit) + 1:
            raise AuditConflict(expected_seq)
        self._audit.append({**rec, "seq": expected_seq})

    def last_audit(self) -> dict | None:
        return self._audit[-1] if self._audit else None

    def audit_records(self, run_id: str | None = None) -> list[dict]:
        return [r for r in self._audit if run_id is None or r.get("run_id") == run_id]

    def tamper_audit_for_test(self, seq: int, payload: str) -> None:
        self._audit[seq - 1]["payload"] = payload


class MongoStore(TreasuryStore):
    kind = "mongodb"

    def __init__(self, uri: str | None = None, db: str = "meridian", client: Any = None, atlas: bool = False,
                 vector_index: str = "policy_vector_index"):
        if client is None:
            from pymongo import MongoClient

            client = MongoClient(uri, appname="meridian-treasury", tz_aware=False, serverSelectionTimeoutMS=8000)
        self.client = client
        self.db = client[db]
        self.atlas = atlas
        self.vector_index = vector_index
        self._cache: GroupData | None = None
        self._ensure_indexes()

    def _ensure_indexes(self) -> None:
        from pymongo import ASCENDING

        self.db["audit"].create_index([("seq", ASCENDING)], unique=True)
        self.db["audit"].create_index([("run_id", ASCENDING)])
        for coll, key in DOC_KEYS.items():
            self.db[coll].create_index([(key, ASCENDING)], unique=True)
        self.db["policy_chunks"].create_index([("chunk_id", ASCENDING)], unique=True)

    def ping(self) -> dict:
        return self.client.admin.command("ping")

    # ---- company data
    def is_seeded(self) -> bool:
        return self.db["meta"].find_one({"_id": "group"}) is not None

    def seed(self, g: GroupData) -> None:
        for name in _COLLECTIONS:
            self.db[name].delete_many({})
            docs = [o.model_dump(mode="json") for o in getattr(g, name)]
            if docs:
                self.db[name].insert_many(docs)
        self.db["meta"].replace_one({"_id": "group"}, {
            "_id": "group", "as_of": str(g.as_of), "fx": g.fx, "facility": g.facility.model_dump(mode="json"),
            "classification": g.classification, "tenant": g.tenant,
            "organization": g.organization.model_dump(mode="json") if g.organization else None}, upsert=True)
        for name, key in (("receivables", "invoice_id"), ("payables", "bill_id"), ("accounts", "account_id"),
                          ("entities", "entity_id"), ("customers", "customer_id")):
            self.db[name].create_index(key, unique=True)
        self._cache = None

    def load_group(self) -> GroupData:
        if self._cache is None:
            meta = self.db["meta"].find_one({"_id": "group"})
            g = GroupData(as_of=date.fromisoformat(meta["as_of"]), fx=meta["fx"],
                          facility=Facility(**meta["facility"]),
                          classification=meta.get("classification", "CLIENT_DATA"), tenant=meta.get("tenant", ""),
                          organization=Organization(**meta["organization"]) if meta.get("organization") else None)
            for name, model in _COLLECTIONS.items():
                setattr(g, name, [model(**{k: v for k, v in d.items() if k != "_id"})
                                  for d in self.db[name].find({})])
            self._cache = g
        return self._cache

    # ---- chunks + vectors
    def upsert_chunks(self, chunks: list[dict]) -> None:
        for c in chunks:
            self.db["policy_chunks"].replace_one({"chunk_id": c["chunk_id"]}, c, upsert=True)

    def vector_search(self, query_vec: list[float], k: int = 4) -> list[dict]:
        if self.atlas:
            pipeline = [
                {"$vectorSearch": {"index": self.vector_index, "path": "embedding", "queryVector": list(query_vec),
                                   "numCandidates": max(50, 10 * k), "limit": k}},
                {"$project": {"_id": 0, "embedding": 0, "score": {"$meta": "vectorSearchScore"}}},
            ]
            return list(self.db["policy_chunks"].aggregate(pipeline))
        docs = [{kk: vv for kk, vv in d.items() if kk != "_id"} for d in self.db["policy_chunks"].find({})]
        return _cosine_top_k(np.array(query_vec), docs, k)

    def chunk_count(self) -> int:
        return self.db["policy_chunks"].count_documents({})

    def all_chunks(self) -> list[dict]:
        return list(self.db["policy_chunks"].find({}, {"_id": 0, "embedding": 0}))

    # ---- generic documents
    def put(self, collection: str, doc: dict) -> None:
        key = DOC_KEYS[collection]
        self.db[collection].replace_one({key: doc[key]}, copy.deepcopy(doc), upsert=True)

    def get(self, collection: str, key: str) -> dict | None:
        return self.db[collection].find_one({DOC_KEYS[collection]: key}, {"_id": 0})

    def find(self, collection: str, limit: int = 500, sort_desc: str | None = None) -> list[dict]:
        cur = self.db[collection].find({}, {"_id": 0})
        if sort_desc:
            cur = cur.sort(sort_desc, -1)
        return list(cur.limit(limit))

    # ---- blobs (GridFS — model artefacts can exceed the 16 MB document limit)
    def put_blob(self, name: str, data: bytes) -> None:
        import gridfs

        fs = gridfs.GridFS(self.db, collection="artifacts")
        for old in fs.find({"filename": name}):
            fs.delete(old._id)
        fs.put(data, filename=name)

    def get_blob(self, name: str) -> bytes | None:
        import gridfs

        f = gridfs.GridFS(self.db, collection="artifacts").find_one({"filename": name})
        return f.read() if f else None

    # ---- audit
    def append_audit(self, rec: dict, expected_seq: int) -> None:
        from pymongo.errors import DuplicateKeyError

        try:
            self.db["audit"].insert_one({**rec, "seq": expected_seq})
        except DuplicateKeyError as e:   # unique index on seq => no forks in the hash chain across replicas
            raise AuditConflict(expected_seq) from e

    def last_audit(self) -> dict | None:
        return self.db["audit"].find_one({}, {"_id": 0}, sort=[("seq", -1)])

    def audit_records(self, run_id: str | None = None) -> list[dict]:
        q = {"run_id": run_id} if run_id else {}
        return list(self.db["audit"].find(q, {"_id": 0}).sort("seq", 1))

    def tamper_audit_for_test(self, seq: int, payload: str) -> None:
        self.db["audit"].update_one({"seq": seq}, {"$set": {"payload": payload}})
