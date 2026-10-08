"""Production readiness checks — run at API start-up (production refuses to start on any FAIL) and by
`meridian doctor`. Each check: name, status (PASS | WARN | FAIL), detail, remediation."""

from __future__ import annotations

import os

from meridian.config import Settings


def _c(name: str, ok: bool | None, detail: str, fix: str = "", warn: bool = False) -> dict:
    status = "PASS" if ok else ("WARN" if warn else "FAIL")
    return {"check": name, "status": status, "detail": detail, "remediation": "" if ok else fix}


def static_checks(s: Settings) -> list[dict]:
    """Configuration-only checks (no network)."""
    prod = s.mode == "production"
    out = [_c("Operating mode", True, f"{s.mode.upper()}" + (" — synthetic company, offline reasoner allowed"
                                                                 if not prod else ""))]
    if not prod:
        return out
    out += [
        _c("Persistence: MongoDB configured", bool(s.mongodb_uri), "MERIDIAN_MONGODB_URI set" if s.mongodb_uri else
           "no MongoDB URI", "Set MERIDIAN_MONGODB_URI to the Atlas connection string"),
        _c("LLM provider is a real Claude endpoint", s.llm_provider != "mock", f"provider={s.llm_provider}",
           "Set MERIDIAN_LLM_PROVIDER to anthropic | vertex | bedrock"),
        _c("LLM credentials", (s.llm_provider != "anthropic" or bool(s.anthropic_api_key))
           and (s.llm_provider != "vertex" or bool(s.gcp_project)) and s.llm_provider != "mock",
           {"anthropic": "ANTHROPIC_API_KEY", "vertex": "MERIDIAN_GCP_PROJECT + ADC",
            "bedrock": "AWS credential chain"}.get(s.llm_provider, "n/a"),
           "Provide credentials via environment / secret manager (never in code)"),
        _c("Semantic embeddings", s.embedder == "transformer", f"embedder={s.embedder}",
           "Set MERIDIAN_EMBEDDER=transformer and install the [nlp] extra", warn=True),
        _c("Session signing key", bool(s.jwt_secret) and len(s.jwt_secret or "") >= 32,
           "MERIDIAN_JWT_SECRET length >= 32" if s.jwt_secret else "missing", "Generate a 32+ byte secret"),
        _c("No demo service keys", "analyst-key" not in (s.api_keys or ""), "service keys reviewed",
           "Remove demo keys from MERIDIAN_API_KEYS"),
        _c("Live market data", s.market_data == "live", f"market_data={s.market_data}",
           "Set MERIDIAN_MARKET_DATA=live", warn=True),
        _c("SEC EDGAR user agent", bool(s.sec_user_agent), "set" if s.sec_user_agent else "missing — peer benchmarks off",
           'Set MERIDIAN_SEC_USER_AGENT="Org Name contact@org.com"', warn=True),
        _c("Fixed as-of date not pinned", s.as_of is None, f"as_of={s.as_of}", "Unset MERIDIAN_AS_OF in production",
           warn=True),
        _c("Release identifier", bool(os.getenv("MERIDIAN_GIT_SHA")), os.getenv("MERIDIAN_GIT_SHA", "not set"),
           "Set MERIDIAN_GIT_SHA at deploy time for reproducibility metadata", warn=True),
    ]
    return out


def runtime_checks(s: Settings, store) -> list[dict]:
    """Checks that need the store (connectivity, data, indexes)."""
    out = []
    prod = s.mode == "production"
    if hasattr(store, "ping"):
        try:
            store.ping()
            out.append(_c("MongoDB reachable", True, "ping ok"))
        except Exception as e:  # noqa: BLE001
            out.append(_c("MongoDB reachable", False, str(e)[:200], "Check network access list / credentials"))
            return out
        if getattr(store, "atlas", False):
            try:
                names = [i["name"] for i in store.db["policy_chunks"].list_search_indexes()]
                out.append(_c("Atlas Vector Search index", s.atlas_vector_index in names, f"indexes={names}",
                              "Create deploy/atlas/vector_index.json in Atlas"))
            except Exception as e:  # noqa: BLE001
                out.append(_c("Atlas Vector Search index", False, str(e)[:200], "Create the vector index", warn=True))
    elif prod:
        out.append(_c("Persistent store", False, "in-memory store", "Configure MongoDB"))
    try:
        from meridian.public_company.analytics import get_public_company

        pc = get_public_company()
        out.append(_c("Public-company case (PUBLIC · REAL)", True, f"{pc.subject['name']} — SEC XBRL to {pc.latest_quarter}, "
                      f"snapshot {pc.snap['retrieved_at']}", ""))
    except Exception as e:  # noqa: BLE001
        out.append(_c("Public-company case (PUBLIC · REAL)", False, str(e)[:200],
                      "Run scripts/refresh_public_company.py", warn=True))
    g = store.load_group() if store.is_seeded() else None
    out.append(_c("Evaluation / client company data loaded", g is not None, f"{g.tenant} as of {g.as_of}" if g else "none",
                  "Run `meridian ingest --dir <package>`"))
    if g is not None and prod:
        out.append(_c("Company data classification", g.classification == "CLIENT_DATA",
                      g.classification, "Production must run on ingested CLIENT_DATA, never SYNTHETIC"))
    if prod:
        failed = [d["source_id"] for d in store.find("data_sources") if d.get("validation") == "FAILED"]
        out.append(_c("Data sources validated", not failed, f"failed={failed}" if failed else "all passed",
                      "Fix data-quality errors and re-ingest"))
    users = store.find("users", limit=1000)
    out.append(_c("Users provisioned", bool(users), f"{len(users)} users", "Create users with `meridian users create`"))
    if prod:
        out.append(_c("CFO approver exists", any(u["role"] == "cfo" for u in users), "role=cfo",
                      "Provision at least one CFO approver"))
    return out


def summarize(checks: list[dict]) -> dict:
    fails = [c for c in checks if c["status"] == "FAIL"]
    return {"ready": not fails, "fail": len(fails), "warn": sum(c["status"] == "WARN" for c in checks), "checks": checks}
