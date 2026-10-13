"""Environment-driven configuration (12-factor).

Two operating modes, never mixed silently:

* ``demo``        Synthetic company ("Orion Industrial Group", clearly labelled SYNTHETIC), in-memory
                  store and offline reasoner allowed. Deterministic — used for tests, CI and walkthroughs.
* ``production``  Client data ingested through governed connectors (ERP extracts, ISO 20022 camt.053),
                  MongoDB Atlas persistence, a real Claude provider, live market data. The service
                  refuses to start if any production requirement is unmet (see ``meridian.readiness``).
"""

from __future__ import annotations

from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PACKAGE_ROOT = Path(__file__).resolve().parent

DEMO_API_KEYS = "analyst-key:analyst:ana,treasurer-key:treasurer:tom,cfo-key:cfo:carla,admin-key:admin:ada"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="MERIDIAN_", env_file=".env", extra="ignore")

    mode: Literal["demo", "production"] = "demo"
    tenant_name: str = "Orion Industrial Group (synthetic demo company)"

    # --- Data store --------------------------------------------------------
    # auto (default): MongoDB when MERIDIAN_MONGODB_URI is set, otherwise SQLite at MERIDIAN_SQLITE_PATH (persistent,
    # free, no install). "memory" is for automated tests only and is refused in production mode.
    store: Literal["auto", "sqlite", "mongodb", "memory"] = "auto"
    sqlite_path: str = "var/meridian.db"
    mongodb_uri: str | None = None          # optional: multi-replica deployments (MongoDB / Atlas)
    mongodb_db: str = "meridian"
    atlas_vector_index: str = "policy_vector_index"

    # --- Market & reference data -------------------------------------------
    # live     : ECB / NY Fed / GDACS / SEC EDGAR fetched at runtime, persisted with provenance
    # snapshot : bundled REAL snapshot (dated, checksummed) — reproducible, used by demo/CI
    # static   : no market overlay (simulation constants) — unit tests only
    market_data: Literal["live", "snapshot", "static"] = "snapshot"
    sec_user_agent: str | None = None       # SEC requires "Org Name contact@org.com" for EDGAR API access
    revolver_margin_bps: float = 250.0      # Applicable Margin over SOFR (from the facility agreement)

    # --- LLM (Anthropic Claude via the native SDK) ---------------------------
    # auto (default): Anthropic Claude when ANTHROPIC_API_KEY is set, otherwise the deterministic offline reasoner.
    # Never claims Claude is active without credentials; the UI shows "AI OFFLINE / DETERMINISTIC" in that case.
    llm_provider: Literal["auto", "mock", "anthropic", "vertex", "bedrock"] = "auto"
    llm_model: str = "claude-sonnet-4-5"
    llm_max_tokens: int = 2048
    llm_timeout_s: float = 60.0               # per request; the SDK retries 429/5xx with backoff
    llm_max_retries: int = 2
    llm_max_tokens_per_run: int = 200_000     # spending control: once reached, agents fall back (labelled)
    anthropic_api_key: str | None = Field(default=None, validation_alias="ANTHROPIC_API_KEY")
    gcp_project: str | None = None          # vertex
    gcp_region: str = "us-east5"            # vertex
    aws_region: str = "us-east-1"           # bedrock

    # --- NLP ---------------------------------------------------------------
    embedder: Literal["hashing", "transformer"] = "hashing"
    transformer_model: str = "sentence-transformers/all-MiniLM-L6-v2"

    # --- Analytics -----------------------------------------------------------
    seed: int = 7
    as_of: date | None = None               # demo: fixed simulation date; production: latest statement date
    demo_as_of: date = date(2026, 10, 6)     # aligned to the bundled market snapshot business date
    forecast_weeks: int = 13
    monte_carlo_paths: int = 400

    # --- Governance --------------------------------------------------------
    policy_path: Path = PROJECT_ROOT / "config" / "treasury_policy.yaml"

    # --- Security ------------------------------------------------------------
    jwt_secret: str | None = None           # required in production (HS256 signing key, >= 32 chars)
    jwt_ttl_minutes: int = 480
    api_keys: str | None = None             # optional service accounts "key:role:user,..."; demo keys in demo mode

    @model_validator(mode="after")
    def _resolve_llm(self):
        if self.llm_provider == "auto":
            self.llm_provider = "anthropic" if self.anthropic_api_key else "mock"
        return self

    def ai_status(self) -> dict:
        """What the UI may claim about AI. 'configured' means credentials/provider are set — a live call is only
        claimed after `meridian llm-check` succeeds (recorded separately)."""
        if self.llm_provider == "mock":
            return {"label": "AI OFFLINE / DETERMINISTIC", "provider": "none", "model": "deterministic offline reasoner",
                    "configured": False}
        if self.llm_provider == "anthropic" and not self.anthropic_api_key:
            return {"label": "AI MISCONFIGURED — no ANTHROPIC_API_KEY", "provider": "anthropic", "model": self.llm_model,
                    "configured": False}
        return {"label": f"AI: {self.llm_provider.upper()} · {self.llm_model}", "provider": self.llm_provider,
                "model": self.llm_model, "configured": True}

    @property
    def store_kind(self) -> str:
        if self.store != "auto":
            return self.store
        return "mongodb" if self.mongodb_uri else "sqlite"

    @property
    def is_demo(self) -> bool:
        return self.mode == "demo"

    def effective_as_of(self) -> date:
        return self.as_of or (self.demo_as_of if self.is_demo else date.today())

    def principals(self) -> dict[str, tuple[str, str]]:
        """Service-account API keys -> (role, user). Demo keys exist only in demo mode."""
        raw = self.api_keys if self.api_keys is not None else (DEMO_API_KEYS if self.is_demo else "")
        out = {}
        for item in raw.split(","):
            parts = item.strip().split(":")
            if len(parts) == 3:
                out[parts[0]] = (parts[1], parts[2])
        return out

    def signing_key(self) -> str:
        if self.jwt_secret:
            return self.jwt_secret
        if self.is_demo:
            return "meridian-demo-signing-key-not-for-production-use"
        raise RuntimeError("MERIDIAN_JWT_SECRET is required in production mode")


@lru_cache
def get_settings() -> Settings:
    return Settings()
