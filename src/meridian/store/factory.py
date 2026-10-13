"""Store factory + bootstrap.

Store selection (MERIDIAN_STORE, default auto):
  auto     MongoDB if MERIDIAN_MONGODB_URI is set, otherwise SQLite at MERIDIAN_SQLITE_PATH (persistent, free)
  sqlite   single-node persistent store (built into Python — no install, no Docker)
  mongodb  multi-replica deployments
  memory   automated tests only — REFUSED in production mode, never chosen silently

demo        : seeded with the SYNTHETIC evaluation company and demo sign-ins on first start.
production  : company data must come from `meridian ingest` (never auto-seeded).
"""

from __future__ import annotations

from functools import lru_cache

from meridian.config import get_settings
from meridian.store.base import MemoryStore, MongoStore, TreasuryStore


@lru_cache
def get_store() -> TreasuryStore:
    store = build_store(get_settings())
    bootstrap(store)
    return store


def build_store(s) -> TreasuryStore:
    """Create the configured store (no seeding). Never falls back to memory silently."""
    kind = s.store_kind
    if kind == "mongodb":
        if not s.mongodb_uri:
            raise RuntimeError("MERIDIAN_STORE=mongodb requires MERIDIAN_MONGODB_URI")
        store: TreasuryStore = MongoStore(s.mongodb_uri, s.mongodb_db, atlas="mongodb.net" in s.mongodb_uri,
                                          vector_index=s.atlas_vector_index)
    elif kind == "sqlite":
        from meridian.store.sqlite import SQLiteStore

        store = SQLiteStore(s.sqlite_path)
    elif kind == "memory":
        if not s.is_demo:
            raise RuntimeError("MERIDIAN_STORE=memory is not permitted in production: state would be lost on restart. "
                               "Use sqlite (single node) or mongodb.")
        store = MemoryStore()
    else:  # pragma: no cover - guarded by the settings Literal
        raise RuntimeError(f"unknown store {kind}")
    return store


def bootstrap(store: TreasuryStore) -> None:
    s = get_settings()
    if not store.is_seeded() and s.is_demo:
        from meridian.data.generator import generate

        store.seed(generate(seed=s.seed, as_of=s.effective_as_of()))
    if store.chunk_count() == 0:
        from meridian.nlp.rag import index_documents

        index_documents(store)
    if s.is_demo:
        from meridian.security.auth import seed_demo_users

        seed_demo_users(store)
