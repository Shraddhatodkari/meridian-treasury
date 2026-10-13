"""Offline, isolated test session: in-memory store (explicit), mock LLM, hashing embedder."""

import os

os.environ["MERIDIAN_LLM_PROVIDER"] = "mock"
os.environ.pop("MERIDIAN_MONGODB_URI", None)
os.environ["MERIDIAN_STORE"] = "memory"          # tests are isolated; persistence has its own tests (test_sqlite_store)
os.environ["MERIDIAN_EMBEDDER"] = "hashing"
os.environ["MERIDIAN_WARMUP"] = "0"

import pytest  # noqa: E402

from meridian.analytics.engine import get_engine  # noqa: E402
from meridian.store.factory import get_store  # noqa: E402


@pytest.fixture(scope="session")
def engine():
    return get_engine()


@pytest.fixture(scope="session")
def store():
    return get_store()
