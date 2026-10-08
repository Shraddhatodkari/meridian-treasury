"""Store factory + bootstrap.

demo        : in-memory store (or MongoDB if a URI is given), seeded with the SYNTHETIC company.
production  : MongoDB only; company data must come from `meridian ingest` (never auto-seeded).
"""

from __future__ import annotations

from functools import lru_cache

from meridian.config import get_settings
from meridian.store.base import MemoryStore, MongoStore, TreasuryStore


@lru_cache
def get_store() -> TreasuryStore:
    s = get_settings()
    if s.mongodb_uri:
        store: TreasuryStore = MongoStore(s.mongodb_uri, s.mongodb_db, atlas="mongodb.net" in s.mongodb_uri,
                                          vector_index=s.atlas_vector_index)
    elif s.is_demo:
        store = MemoryStore()
    else:
        raise RuntimeError("Production mode requires MERIDIAN_MONGODB_URI (MongoDB Atlas). In-memory state is "
                           "not permitted in production.")
    bootstrap(store)
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
