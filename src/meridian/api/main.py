"""Meridian REST API (FastAPI) + the Treasury Console (served from /).

Authentication: `POST /v1/auth/login` → signed JWT (Bearer). Optional service-account API keys
(`X-API-Key`) for machine clients. Every endpoint is permission-checked (RBAC); money-movement
authority is enforced separately by the policy approval matrix and segregation of duties.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any, Literal, cast

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi import Path as Path_
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from pydantic import BaseModel, Field
from starlette.responses import Response

from meridian import __version__
from meridian.analytics.engine import get_engine, scenario_from_any
from meridian.analytics.scenarios import PRESET_SCENARIOS, Scenario
from meridian.config import get_settings
from meridian.governance.audit import AuditLedger
from meridian.governance.guardrails import mask
from meridian.governance.policy import ApprovalDenied, load_policy
from meridian.nlp.rag import quarantined_chunks
from meridian.observability import ops
from meridian.orchestration.service import ExecutionBlocked, TreasuryService
from meridian.readiness import runtime_checks, static_checks, summarize
from meridian.security.auth import (
    DEMO_PASSWORD,
    DEMO_USERS,
    PERMISSIONS,
    PRIVILEGED_ROLES,
    ROLES,
    AuthError,
    Principal,
    authenticate,
    create_user,
    decode_token,
    issue_token,
    revoke,
    service_principal,
)
from meridian.store.factory import get_store

CONSOLE_DIR = Path(__file__).resolve().parents[1] / "console"
_svc: TreasuryService | None = None


def svc() -> TreasuryService:
    global _svc
    if _svc is None:
        _svc = TreasuryService()
    return _svc


@asynccontextmanager
async def lifespan(_: FastAPI):
    from meridian.observability.logs import configure as _configure_logs

    _configure_logs()
    s = get_settings()
    checks = static_checks(s)
    if s.mode == "production":
        checks += runtime_checks(s, get_store())
        rep = summarize(checks)
        if not rep["ready"]:
            failed = "; ".join(f"{c['check']}: {c['detail']}" for c in rep["checks"] if c["status"] == "FAIL")
            raise RuntimeError(f"Production readiness failed — refusing to start. {failed}")
    svc()
    import os
    import threading
    import time as _t

    def _warm():
        t0 = _t.perf_counter()
        try:
            e = get_engine()
            e.metrics()
            e.risks()
            ops.record_job("cache_warmup", "SUCCEEDED", (_t.perf_counter() - t0) * 1000, "metrics, back-test, risk register")
        except Exception as ex:  # noqa: BLE001
            ops.record_job("cache_warmup", "FAILED", (_t.perf_counter() - t0) * 1000, str(ex)[:200])
        # Scheduled base-case review of the PUBLIC · REAL company, so the platform never opens on an empty decision
        # queue. Runs only if no public base-case run exists; requester is the scheduler service account (audited).
        if os.getenv("MERIDIAN_STARTUP_REVIEW", "1") != "0":
            import asyncio

            try:
                if not any(r.get("case") == "public" and r.get("is_base_case") for r in get_store().list_runs(50)):
                    asyncio.run(svc().run_public(None, requested_by="meridian-scheduler"))
            except Exception as ex:  # noqa: BLE001
                ops.record_job("public_company_run", "FAILED", 0, f"startup review: {ex}"[:200])

    if os.getenv("MERIDIAN_WARMUP", "1") != "0":
        threading.Thread(target=_warm, daemon=True).start()
    yield


app = FastAPI(title="Meridian — Enterprise Liquidity & Working-Capital Intelligence", version=__version__,
              lifespan=lifespan, description="Treasury analytics, governed AI agents, approvals and audit.")
app.add_middleware(CORSMiddleware, allow_origins=[], allow_methods=["GET", "POST", "PATCH"], allow_headers=["*"])


CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
       "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")


@app.middleware("http")
async def security_headers(request: Request, call_next):
    import re as _re
    import time as _t
    import uuid as _uuid

    from meridian.observability.logs import access

    rid = request.headers.get("X-Request-ID", "")
    rid = rid if _re.fullmatch(r"[A-Za-z0-9._\-]{8,64}", rid) else _uuid.uuid4().hex
    t0 = _t.perf_counter()
    try:
        resp = await call_next(request)
    except Exception:
        ops.record_request(request.url.path, request.method, 500, (_t.perf_counter() - t0) * 1000)
        access.exception("request failed", extra={"request_id": rid, "method": request.method,
                                                  "route": request.url.path, "status": 500})
        raise
    route = request.scope.get("route")
    ms = round((_t.perf_counter() - t0) * 1000, 1)
    ops.record_request(getattr(route, "path", request.url.path), request.method, resp.status_code, ms)
    access.info("request", extra={"request_id": rid, "method": request.method,
                                  "route": getattr(route, "path", request.url.path), "status": resp.status_code, "ms": ms})
    resp.headers["X-Request-ID"] = rid
    resp.headers.update({"X-Content-Type-Options": "nosniff", "X-Frame-Options": "DENY",
                         "Referrer-Policy": "no-referrer", "Cache-Control": "no-store",
                         "Permissions-Policy": "camera=(), microphone=(), geolocation=()"})
    if request.url.path == "/" or request.url.path.startswith("/console"):
        resp.headers["Content-Security-Policy"] = CSP
        # Always declare UTF-8 for text assets. Windows' mimetypes registry can map .js/.css to types without a charset,
        # which makes browsers decode UTF-8 symbols (Σ, →, ≥, —) as Windows-1252 mojibake (Î£, â†’, â‰¥, â€”).
        ext = request.url.path.rsplit(".", 1)[-1].lower() if "." in request.url.path else "html"
        ctype = {"js": "text/javascript", "mjs": "text/javascript", "css": "text/css", "html": "text/html",
                 "svg": "image/svg+xml", "json": "application/json"}.get(ext)
        if ctype and resp.status_code == 200:
            resp.headers["Content-Type"] = f"{ctype}; charset=utf-8"
    if request.url.scheme == "https":
        resp.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return resp


# ------------------------------------------------------------------------------------------- auth & RBAC
def principal(authorization: Annotated[str | None, Header()] = None,
              x_api_key: Annotated[str | None, Header()] = None) -> Principal:
    if authorization and authorization.lower().startswith("bearer "):
        try:
            return decode_token(authorization[7:], get_store())
        except AuthError as e:
            raise HTTPException(401, str(e)) from None
    if x_api_key and (p := service_principal(x_api_key)):
        return p
    raise HTTPException(401, "Authentication required")


def require(permission: str):
    def dep(request: Request, p: Principal = Depends(principal)) -> Principal:
        if not p.can(permission):
            AuditLedger(get_store()).record(p.username, "authorization_denied", {
                "role": p.role, "permission": permission, "method": request.method, "path": request.url.path})
            raise HTTPException(403, f"Role '{p.role}' lacks permission '{permission}'")
        return p
    return dep


class LoginRequest(BaseModel):
    username: str
    password: str


class RunRequest(BaseModel):
    question: str | None = Field(default=None, max_length=500)


class PublicRunRequest(BaseModel):
    question: str | None = Field(default=None, max_length=500)
    cik: int | None = Field(default=None, ge=1, le=9_999_999_999)


class CompanyAdd(BaseModel):
    query: str = Field(min_length=1, max_length=12, pattern=r"^[A-Za-z0-9.\-]+$", description="Ticker (e.g. DE) or CIK")
    peers: list[Annotated[str, Field(max_length=12, pattern=r"^[A-Za-z0-9.\-]+$")]] | None = Field(default=None, max_length=10)


class DecisionRequest(BaseModel):
    decision: Literal["APPROVE", "REJECT"]
    comment: str = Field(default="", max_length=1000)


class CommentRequest(BaseModel):
    comment: str = Field(default="", max_length=1000)


class UserCreate(BaseModel):
    username: str = Field(pattern=r"^[a-z][a-z0-9._-]{2,31}$")
    display_name: str = Field(min_length=2, max_length=80)
    role: str
    password: str = Field(min_length=12, max_length=128)


class UserUpdate(BaseModel):
    active: bool | None = None
    role: str | None = None
    unlock: bool = False


@app.post("/v1/auth/login", tags=["auth"])
def login(req: LoginRequest):
    store = get_store()
    try:
        user = authenticate(store, req.username, req.password)
    except AuthError as e:
        AuditLedger(store).record(req.username, "login_failed", {"reason": str(e)})
        raise HTTPException(401, str(e)) from None
    token, exp = issue_token(user)
    AuditLedger(store).record(user["username"], "login", {"role": user["role"]})
    return {"access_token": token, "token_type": "bearer", "expires_at": exp.isoformat(),
            "user": {"username": user["username"], "display_name": user["display_name"], "role": user["role"],
                     "permissions": sorted(k for k, v in PERMISSIONS.items() if user["role"] in v)}}


@app.post("/v1/auth/logout", tags=["auth"])
def logout(p: Principal = Depends(principal)):
    """Server-side sign-out: the token's id is revoked, so the token is useless even before it expires."""
    store = get_store()
    revoke(store, p)
    AuditLedger(store).record(p.username, "logout", {"role": p.role})
    return {"status": "signed_out"}


@app.get("/v1/auth/me", tags=["auth"])
def me(p: Principal = Depends(principal)):
    return {**p.model_dump(), "permissions": sorted(k for k, v in PERMISSIONS.items() if p.role in v),
            "role_description": ROLES.get(p.role)}


# ------------------------------------------------------------------------------------------- system
@app.get("/health", tags=["system"])
def health():
    return {"status": "ok", "version": __version__}


@app.get("/ready", tags=["system"])
def ready():
    s = get_settings()
    e = get_engine()
    return {"status": "ready", "mode": s.mode, "primary_case": "public:CAT (PUBLIC_REAL)",
            "classification": e.g.classification, "tenant": e.g.tenant,
            "llm_provider": s.llm_provider, "llm_model": s.llm_model if s.llm_provider != "mock" else "offline reasoner",
            "ai_status": s.ai_status(),
            "store": get_store().kind, "embedder": s.embedder, "market_data": e.market.mode,
            "policy_version": load_policy().version, "model_id": e.model.card.get("model_id"), "as_of": str(e.g.as_of),
            "version": __version__}


@app.get("/v1/system/environment", tags=["system"])
def environment():
    """Public, unauthenticated banner info for the login screen."""
    s = get_settings()
    e = get_engine()
    demo = s.is_demo
    return {"mode": s.mode, "classification": e.g.classification, "tenant": e.g.tenant, "version": __version__,
            "ai_status": s.ai_status(), "primary_case": "public",
            "demo_accounts": [{"username": u, "name": n, "role": r} for u, n, r in DEMO_USERS] if demo else [],
            "demo_password": DEMO_PASSWORD if demo else None}


@app.get("/v1/system/readiness", tags=["system"])
def readiness(_: Principal = Depends(require("verify_audit"))):
    s = get_settings()
    return summarize(static_checks(s) + runtime_checks(s, get_store()))


@app.get("/metrics", include_in_schema=False)
def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


# ------------------------------------------------------------------------------------------- analytics
@app.get("/v1/kpis", tags=["analytics"])
def kpis(_: Principal = Depends(principal)):
    return get_engine().kpis()


@app.get("/v1/entities", tags=["analytics"])
def entities(_: Principal = Depends(principal)):
    return get_engine().entities()


@app.get("/v1/accounts", tags=["analytics"])
def accounts(p: Principal = Depends(principal)):
    rows = [a.model_dump() for a in get_engine().g.accounts]
    return rows if p.can("view_bank_details") else mask(rows)


@app.get("/v1/forecast", tags=["analytics"])
def forecast(scenario: str = "base", _: Principal = Depends(principal)):
    return get_engine().forecast_summary(scenario_from_any(scenario))


@app.get("/v1/forecast/entities/{entity_id}", tags=["analytics"])
def forecast_entity(entity_id: str, scenario: str = "base", _: Principal = Depends(principal)):
    fc = get_engine().forecast(scenario_from_any(scenario))
    if entity_id not in fc.entity_paths_usd:
        raise HTTPException(404, "Unknown entity")
    return {"entity_id": entity_id, "week_starts": [str(d) for d in fc.week_starts],
            "cash_usd": fc.band(fc.entity_paths_usd[entity_id]), "buffer_usd": round(fc.entity_buffers_usd[entity_id])}


@app.post("/v1/forecast", tags=["analytics"])
def forecast_custom(sc: Scenario, _: Principal = Depends(principal)):
    return get_engine().forecast_summary(sc)


@app.get("/v1/scenarios", tags=["analytics"])
def scenarios(_: Principal = Depends(principal)):
    e = get_engine()
    return {"presets": {k: v.model_dump() for k, v in PRESET_SCENARIOS.items()},
            "comparison": e.scenario_compare(list(PRESET_SCENARIOS.values()))}


@app.get("/v1/optimize", tags=["analytics"])
def optimize(scenario: str = "base", _: Principal = Depends(principal)):
    return get_engine().optimize(scenario_from_any(scenario))


@app.get("/v1/working-capital/collections", tags=["analytics"])
def collections(top_n: int = 20, _: Principal = Depends(principal)):
    return get_engine().collections(top_n)


@app.get("/v1/working-capital/early-pay", tags=["analytics"])
def early_pay(_: Principal = Depends(principal)):
    return get_engine().early_pay()


@app.get("/v1/risk/events", tags=["analytics"])
def risk_events(_: Principal = Depends(principal)):
    return get_engine().risk_events()


@app.get("/v1/backtest", tags=["analytics"])
def backtest(_: Principal = Depends(principal)):
    return get_engine().backtest()


@app.get("/v1/benchmarks/peers", tags=["analytics"])
def peers(_: Principal = Depends(principal)):
    return get_engine().peers()


@app.get("/v1/insights", tags=["insights"])
def insights_list(_: Principal = Depends(principal)):
    from meridian.analytics.insights import TOPICS

    return TOPICS


@app.get("/v1/insights/{topic}", tags=["insights"])
def insight(topic: str, _: Principal = Depends(principal)):
    from meridian.analytics.insights import TOPICS, answer

    if topic not in TOPICS:
        raise HTTPException(404, f"Unknown topic; valid: {sorted(TOPICS)}")
    return answer(get_engine(), topic)


@app.get("/v1/graph", tags=["relationships"])
def graph(node: str | None = None, depth: int = 2, _: Principal = Depends(principal)):
    from meridian.analytics.relationships import hazard_exposure_paths, neighborhood

    g = get_engine().refresh_graph()  # include the latest proposed / approved actions
    if node:
        return neighborhood(g, node, depth)
    return {**g, "hazard_paths": hazard_exposure_paths(g)}


@app.get("/v1/policy/search", tags=["policy"])
def policy_search(q: Annotated[str, Query(min_length=2, max_length=300)], k: Annotated[int, Query(ge=1, le=10)] = 4,
                  _: Principal = Depends(principal)):
    from meridian.governance.guardrails import scan_text

    hits = get_engine().policy_search(q, k)
    return hits if not scan_text(q) else [{**h, "query_flagged": "prompt-injection pattern in query"} for h in hits]


# ------------------------------------------------------------------------------------------- agents & approvals
@app.post("/v1/runs", tags=["agents"])
async def run(req: RunRequest, p: Principal = Depends(require("run_agents"))):
    return await svc().run(req.question, requested_by=p.username)


@app.get("/v1/runs", tags=["agents"])
def runs(_: Principal = Depends(principal)):
    return [{k: r.get(k) for k in ("run_id", "created_at", "question", "requested_by", "status", "intent",
                                    "classification")} | {"case": r.get("case", "synthetic"), "cik": _ckey(r)[1] or None} |
            {"actions": len(r.get("actions", [])),
             "pending": sum(a["status"] == "PROPOSED" for a in r.get("actions", []))} for r in get_store().list_runs()]


@app.get("/v1/runs/{run_id}", tags=["agents"])
def get_run(run_id: str, _: Principal = Depends(principal)):
    r = get_store().get_run(run_id)
    if not r:
        raise HTTPException(404, "Run not found")
    return r


@app.get("/v1/approvals/queue", tags=["approvals"])
def approval_queue(p: Principal = Depends(principal)):
    """Pending decisions with a server-side answer to "can I decide this, and if not, why not?"."""
    from meridian.governance.policy import check_approval

    out = []
    runs = get_store().list_runs(200)
    latest: dict[tuple, str] = {}
    for r in runs:
        if r.get("is_base_case"):
            latest.setdefault(_ckey(r), r["run_id"])
    for r in runs:
        latest_base = latest.get(_ckey(r))
        recs = {x["rec_id"]: x for x in r.get("recommendations") or []}
        for a in r.get("actions", []):
            if a["status"] not in {"PROPOSED", "SUBMITTED"}:
                continue
            proposers = [r["requested_by"]] + ([a["submitted_by"]["user"]] if a.get("submitted_by") else [])
            reason = None
            if not p.can("decide_actions"):
                reason = f"Role {p.role} has no decision authority"
            else:
                try:
                    check_approval(a, p.role, p.username, proposers)
                except ApprovalDenied as e:
                    reason = str(e)
            stale = bool(latest_base and r.get("is_base_case") and r["run_id"] != latest_base)
            if stale:
                reason = f"Superseded by the newer review {latest_base} — decisions are blocked on older runs"
            rec = recs.get(a.get("rec_id"), {})
            out.append({"run_id": r["run_id"], "created_at": r["created_at"], "requested_by": r["requested_by"],
                        "qualified_action_id": f"{r['run_id']}/{a['action_id']}",
                        **{k: a.get(k) for k in ("action_id", "type", "from_entity", "to_entity", "amount_usd",
                                                 "required_approver", "policy_refs", "rationale", "status", "rec_id",
                                                 "submitted_by", "payment_state")},
                        "rec_title": rec.get("title"), "rec_short_id": rec.get("short_id"),
                        "case": r.get("case", "synthetic"), "cik": _ckey(r)[1] or None,
                        "company": (r.get("company") or {}).get("name"), "classification": r.get("classification"),
                        "scenario": r.get("scenario", {}).get("name") if r.get("scenario") else None,
                        "stale": stale, "can_approve": reason is None, "blocked_reason": reason})
    return out


@app.post("/v1/runs/{run_id}/actions/{action_id}/decision", tags=["approvals"])
def decide(run_id: str, action_id: str, req: DecisionRequest, p: Principal = Depends(require("decide_actions"))):
    try:
        return svc().decide(run_id, action_id, req.decision, p.username, p.role, req.comment)
    except KeyError as e:
        raise HTTPException(404, f"Not found: {e}") from None
    except ApprovalDenied as e:
        raise HTTPException(403, str(e)) from None
    except ValueError as e:
        raise HTTPException(409, str(e)) from None


@app.post("/v1/runs/{run_id}/actions/{action_id}/prepare", tags=["execution"])
def prepare(run_id: str, action_id: str, p: Principal = Depends(require("prepare_execution"))):
    """Prepare controlled execution: validate the approved payment file → READY. Nothing is transmitted."""
    try:
        return svc().prepare_execution(run_id, action_id, p.username, p.role)
    except KeyError as e:
        raise HTTPException(404, f"Not found: {e}") from None
    except ValueError as e:
        raise HTTPException(409, str(e)) from None


@app.post("/v1/runs/{run_id}/actions/{action_id}/release", tags=["execution"])
def release(run_id: str, action_id: str, p: Principal = Depends(require("release_payments"))):
    """Execute = transmit to the bank. Refused unless READY, dual control holds and a bank channel is configured."""
    try:
        return svc().release(run_id, action_id, p.username, p.role)
    except KeyError as e:
        raise HTTPException(404, f"Not found: {e}") from None
    except ExecutionBlocked as e:
        raise HTTPException(409, str(e)) from None


# ------------------------------------------------------------------------------------------- governance
@app.get("/v1/governance/policy", tags=["governance"])
def policy(_: Principal = Depends(principal)):
    return load_policy().model_dump()


@app.get("/v1/governance/provenance", tags=["governance"])
def provenance(_: Principal = Depends(principal)):
    return get_engine().provenance()


@app.get("/v1/governance/reproducibility", tags=["governance"])
def reproducibility(_: Principal = Depends(principal)):
    return get_engine().reproducibility()


@app.get("/v1/governance/agents", tags=["governance"])
def agents(_: Principal = Depends(principal)):
    from meridian.agents.registry import registry

    return [a.model_dump() for a in registry()]


@app.get("/v1/governance/audit/verify", tags=["governance"])
def audit_verify(_: Principal = Depends(require("verify_audit"))):
    return AuditLedger(get_store()).verify()


@app.get("/v1/governance/audit", tags=["governance"])
def audit_list(limit: int = 300, _: Principal = Depends(require("verify_audit"))):
    return list(reversed(AuditLedger(get_store()).records(limit=limit)))


@app.get("/v1/governance/audit/{run_id}", tags=["governance"])
def audit_run(run_id: str, _: Principal = Depends(principal)):
    return AuditLedger(get_store()).for_run(run_id)


@app.get("/v1/governance/model-card", tags=["governance"])
def model_card(_: Principal = Depends(principal)):
    return get_engine().model.card


@app.get("/v1/governance/models", tags=["governance"])
def models(_: Principal = Depends(principal)):
    return get_store().find("model_registry")


@app.get("/v1/governance/quarantine", tags=["governance"])
def quarantine(_: Principal = Depends(principal)):
    return [{k: v for k, v in c.items() if k != "embedding"} for c in quarantined_chunks(get_store())]


@app.get("/v1/governance/ingestions", tags=["governance"])
def ingestions(_: Principal = Depends(principal)):
    return get_store().find("ingestions", sort_desc="created_at")


@app.get("/v1/admin/users", tags=["admin"])
def users(_: Principal = Depends(require("admin"))):
    return [{k: v for k, v in u.items() if k != "password_hash"} for u in get_store().find("users")]


# ------------------------------------------------------------------------------------------- recommendations
def _ckey(r: dict) -> tuple:
    return (r.get("case", "synthetic"), int((r.get("company") or {}).get("cik") or 0))


def _all_recs(scope: str = "latest", case: str = "all", cik: int | None = None) -> list[dict]:
    runs = get_store().list_runs(200)
    latest: dict[tuple, str] = {}
    for r in runs:
        if r.get("is_base_case"):
            latest.setdefault(_ckey(r), r["run_id"])
    out = []
    for r in runs:
        rc = r.get("case", "synthetic")
        if case != "all" and rc != case:
            continue
        if cik is not None and _ckey(r)[1] != int(cik):
            continue
        latest_base = latest.get(_ckey(r))
        if scope == "latest" and r["run_id"] != latest_base:
            continue
        acts = {a["action_id"]: a for a in r.get("actions", [])}
        for rec in r.get("recommendations") or []:
            out.append({**rec, "case": rc, "cik": _ckey(r)[1] or None, "run_created_at": r["created_at"],
                        "requested_by": r["requested_by"],
                        "stale": bool(latest_base and r["run_id"] != latest_base and r.get("is_base_case")),
                        "actions": [acts[i] for i in rec["action_ids"] if i in acts]})
    return out


@app.get("/v1/recommendations", tags=["decisions"])
def recommendations(scope: Literal["latest", "all"] = "latest", case: Literal["public", "synthetic", "all"] = "all",
                    cik: int | None = None, _: Principal = Depends(principal)):
    return _all_recs(scope, case, cik)


@app.get("/v1/recommendations/{rec_id}", tags=["decisions"])
def recommendation(rec_id: str, _: Principal = Depends(principal)):
    r = next((x for x in _all_recs("all") if x["rec_id"] == rec_id), None)
    if not r:
        raise HTTPException(404, "Unknown recommendation")
    return r


@app.post("/v1/recommendations/{rec_id}/submit", tags=["decisions"])
def submit(rec_id: str, req: CommentRequest, p: Principal = Depends(require("submit_recommendations"))):
    run_id, _, short = rec_id.rpartition(":")
    try:
        return svc().submit(run_id, short, p.username, p.role, req.comment)
    except KeyError as e:
        raise HTTPException(404, f"Not found: {e}") from None
    except ValueError as e:
        raise HTTPException(409, str(e)) from None


@app.get("/v1/lineage/recommendations/{rec_id}", tags=["lineage"])
def rec_lineage(rec_id: str, _: Principal = Depends(principal)):
    """Recommendation → Evidence → Data → Policy → Approval → Audit, as a DAG."""
    from meridian.platform.catalog import upstream

    r = next((x for x in _all_recs("all") if x["rec_id"] == rec_id), None)
    if not r:
        raise HTTPException(404, "Unknown recommendation")
    if r.get("case") == "public":
        return _public_rec_lineage(r)
    e = get_engine()
    run = cast(dict, get_store().get_run(r["run_id"]))     # the recommendation came from this stored run
    cat = e.catalog()
    by = {d["dataset_id"]: d for d in cat}
    mets = e.metrics()
    nodes: dict[str, dict] = {}
    edges: list[dict] = []

    def n(nid, kind, label, **kw):
        nodes.setdefault(nid, {"id": nid, "kind": kind, "label": label, **kw})

    n(rec_id, "Recommendation", f"{r['short_id']} {r['title']}", status=r["status"])
    ds_ids = set()
    for ev in r["evidence"]:
        if ev["type"] == "metric" and ev["id"] in mets:
            m = mets[ev["id"]]
            n(f"metric:{m['metric_id']}", "Metric", m["label"], value=m["value"], unit=m["unit"], basis=m["basis"],
              formula=m["formula"])
            edges.append({"from": f"metric:{m['metric_id']}", "to": rec_id, "rel": "SUPPORTS"})
            for i in m["inputs"] + [m["data_product"]]:
                ds_ids.add(i)
                edges.append({"from": f"ds:{i}", "to": f"metric:{m['metric_id']}", "rel": "INPUT"})
        elif ev["type"] == "dataset":
            ds_ids.add(ev["id"])
            edges.append({"from": f"ds:{ev['id']}", "to": rec_id, "rel": "EVIDENCE"})
        elif ev["type"] == "tool":
            call: dict = next((c for c in run.get("tool_calls", []) if c["tool"] == ev["id"]), {})
            n(f"tool:{ev['id']}", "ToolCall", ev["id"], result_sha256=call.get("result_sha256"),
              audit_hash=call.get("audit_hash"), agent=call.get("agent"))
            edges.append({"from": f"tool:{ev['id']}", "to": rec_id, "rel": "COMPUTED_BY"})
        elif ev["type"] == "finding":
            f: dict = next((x for x in run.get("findings", []) if x["agent"] == ev["id"]), {})
            n(f"agent:{ev['id']}", "AgentFinding", ev["id"], confidence=f.get("confidence"),
              grounded=(f.get("validation") or {}).get("numeric_grounding"), audit_hash=f.get("audit_hash"))
            edges.append({"from": f"agent:{ev['id']}", "to": rec_id, "rel": "REASONED_BY"})
    frontier = set(ds_ids)
    for d in list(ds_ids):
        for u in upstream(cat, d):
            frontier.add(u["dataset_id"])
    for d in frontier:
        x = by.get(d, {"dataset_id": d, "layer": "?", "name": d})
        n(f"ds:{d}", "Dataset", x.get("name", d), layer=x.get("layer"), classification=x.get("classification"),
          as_of=x.get("as_of"), system=x.get("system"), transformation=x.get("transformation"), dataset_id=d)
        for p_ in x.get("derived_from", []):
            edges.append({"from": f"ds:{p_}", "to": f"ds:{d}", "rel": "DERIVED_FROM"})
    for c in r.get("policy_citations") or [{"clause_id": c} for c in r["policy_refs"]]:
        n(f"policy:{c['clause_id']}", "PolicyClause", c["clause_id"], document=c.get("document"),
          version=c.get("doc_version"), relevance=c.get("relevance"))
        edges.append({"from": f"policy:{c['clause_id']}", "to": rec_id, "rel": "GOVERNS"})
    audit = {a["hash"][:16]: a for a in get_store().audit_records(r["run_id"])}
    for a in r["actions"]:
        aid = f"action:{a['action_id']}"
        n(aid, "Action", f"{a['action_id']} {a['type'].replace('_', ' ').title()}", status=a["status"],
          amount_usd=a["amount_usd"], required_approver=a["required_approver"], payment_state=a.get("payment_state"))
        edges.append({"from": rec_id, "to": aid, "rel": "PROPOSES"})
        for who in ("submitted_by", "approved_by", "rejected_by", "prepared_by"):
            if a.get(who):
                uid = f"{who}:{a['action_id']}"
                n(uid, "Decision", f"{who.split('_')[0].upper()} by {a[who]['user']} ({a[who]['role']})", at=a[who]["at"])
                edges.append({"from": aid, "to": uid, "rel": who.upper()})
        for k, h in ((a.get("evidence") or {}).get("audit") or {}).items():
            rec_ = audit.get(h)
            n(f"audit:{h}", "AuditRecord", f"#{rec_['seq'] if rec_ else '?'} {rec_['action'] if rec_ else k}", hash=h)
            edges.append({"from": aid, "to": f"audit:{h}", "rel": "RECORDED_IN"})
    seen = set()
    uniq = []
    for ed in edges:
        k = (ed["from"], ed["to"], ed["rel"])
        if k not in seen and ed["from"] in nodes and ed["to"] in nodes:
            seen.add(k)
            uniq.append(ed)
    return {"recommendation": {k: r[k] for k in ("rec_id", "short_id", "title", "status", "category")},
            "nodes": list(nodes.values()), "edges": uniq,
            "layers": ["RAW", "VALIDATED", "NORMALIZED", "ANALYTICAL", "Metric", "AgentFinding", "PolicyClause",
                       "Recommendation", "Action", "Decision", "AuditRecord"]}


def _public_rec_lineage(r: dict) -> dict:
    """SOURCE → FILING → FACT → METRIC → RECOMMENDATION → POLICY → ACTION → DECISION → AUDIT for public-data recs."""
    from meridian.public_company.analytics import get_public_company

    run = cast(dict, get_store().get_run(r["run_id"]))     # the recommendation came from this stored run
    pc = get_public_company((run.get("company") or {}).get("cik"))
    nodes: dict[str, dict] = {}
    edges: list[dict] = []

    def n(nid, kind, label, **kw):
        nodes.setdefault(nid, {"id": nid, "kind": kind, "label": label, **kw})

    rid = r["rec_id"]
    n(rid, "Recommendation", f"{r['short_id']} {r['title']}", status=r["status"])
    n("src:sec", "Source", "SEC EDGAR XBRL company facts", classification="PUBLIC_REAL", url=pc.subject["source_url"])
    for ev in r["evidence"]:
        m = pc.metrics.get(ev["id"])
        if not m or not m.get("lineage"):
            continue
        mid = f"metric:{ev['id']}"
        n(mid, "Metric", m["label"], value=m["value"], unit=m["unit"], basis=m["basis"], formula=m["formula"],
          period=m["period"])
        edges.append({"from": mid, "to": rid, "rel": "SUPPORTS"})
        for f in m["lineage"]["facts"]:
            fid = f"fact:{f['concept']}:{f['period']}"
            n(fid, "Fact", f"{f['concept'].split(':')[-1]} {f['period']}", value=f["value"], accession=f["accession"],
              form=f["form"], filed=f["filed"])
            fl = f"filing:{f['accession']}"
            n(fl, "Filing", f"{f['form']} {f['accession']} (filed {f['filed']})", classification="PUBLIC_REAL")
            edges += [{"from": "src:sec", "to": fl, "rel": "PUBLISHED"}, {"from": fl, "to": fid, "rel": "CONTAINS"},
                      {"from": fid, "to": mid, "rel": "INPUT"}]
        for mi in m["lineage"].get("market_inputs") or []:
            k = f"mkt:{mi['input']}"
            n(k, "MarketData", f"{mi['input']} {mi['value_pct']}% ({mi['as_of']})", classification="PUBLIC_REAL")
            edges.append({"from": k, "to": mid, "rel": "INPUT"})
    for c in r.get("policy_citations") or [{"clause_id": c} for c in r["policy_refs"]]:
        n(f"policy:{c['clause_id']}", "PolicyClause", c["clause_id"], document=c.get("document"),
          version=c.get("doc_version"), relevance=c.get("relevance"))
        edges.append({"from": f"policy:{c['clause_id']}", "to": rid, "rel": "GOVERNS"})
    audit = {a["hash"][:16]: a for a in get_store().audit_records(r["run_id"])}
    for a in r["actions"]:
        aid = f"action:{a['action_id']}"
        n(aid, "Action", f"{a['action_id']} {a['type'].replace('_', ' ').title()}", status=a["status"],
          amount_usd=a["amount_usd"], required_approver=a["required_approver"])
        edges.append({"from": rid, "to": aid, "rel": "PROPOSES"})
        for who in ("submitted_by", "approved_by", "rejected_by", "prepared_by", "executed_by"):
            if a.get(who):
                uid = f"{who}:{a['action_id']}"
                n(uid, "Decision", f"{who.split('_')[0].upper()} by {a[who]['user']} ({a[who]['role']})", at=a[who]["at"])
                edges.append({"from": aid, "to": uid, "rel": who.upper()})
        for k, h in ((a.get("evidence") or {}).get("audit") or {}).items():
            rec_ = audit.get(h)
            n(f"audit:{h}", "AuditRecord", f"#{rec_['seq'] if rec_ else '?'} {rec_['action'] if rec_ else k}", hash=h)
            edges.append({"from": aid, "to": f"audit:{h}", "rel": "RECORDED_IN"})
    _ = run
    seen, uniq = set(), []
    for ed in edges:
        ek = (ed["from"], ed["to"], ed["rel"])
        if ek not in seen and ed["from"] in nodes and ed["to"] in nodes:
            seen.add(ek)
            uniq.append(ed)
    return {"recommendation": {k: r[k] for k in ("rec_id", "short_id", "title", "status", "category")},
            "nodes": list(nodes.values()), "edges": uniq,
            "layers": ["Source", "Filing", "Fact", "MarketData", "Metric", "PolicyClause", "Recommendation", "Action",
                       "Decision", "AuditRecord"]}


# ------------------------------------------------------------------------------------------- PUBLIC · REAL company
def _pcx(cik: int | None = None):
    from meridian.public_company.analytics import get_public_company

    try:
        return get_public_company(cik)
    except KeyError as e:
        raise HTTPException(404, f"Company not added: {e}. Add it with POST /v1/companies.") from None


@app.get("/v1/companies", tags=["public-company"])
def companies(_: Principal = Depends(principal)):
    from meridian.public_company.analytics import list_companies

    return list_companies()


@app.post("/v1/companies", tags=["public-company"])
def add_company_ep(req: CompanyAdd, p: Principal = Depends(require("run_agents"))):
    """Add any SEC-reporting company by ticker or CIK. Facts are fetched live from SEC EDGAR, validated, snapshotted
    (dated, checksummed) and persisted. Companies without us-gaap 10-K/10-Q facts are refused with the reason."""
    import httpx

    from meridian.public_company.analytics import add_company

    try:
        out = add_company(req.query, p.username, req.peers)
    except LookupError as e:
        raise HTTPException(404, str(e)) from None
    except ValueError as e:
        raise HTTPException(422, str(e)) from None
    except httpx.HTTPError as e:
        raise HTTPException(503, f"SEC EDGAR unreachable ({type(e).__name__}); no data was fabricated") from None
    AuditLedger(get_store()).record(p.username, "company_added", {k: out.get(k) for k in ("cik", "ticker", "name", "status")})
    return out


@app.get("/v1/cases", tags=["public-company"])
def cases(_: Principal = Depends(principal)):
    from meridian.public_company.analytics import get_public_company, list_companies

    e = get_engine()
    out = []
    for c in list_companies():
        pc = get_public_company(c["cik"])
        out.append({"case": "public", "cik": c["cik"], "name": pc.subject["name"], "ticker": pc.subject.get("ticker"),
                    "classification": "PUBLIC_REAL", "label": "PUBLIC · REAL", "as_of": pc.latest_quarter,
                    "source": "SEC EDGAR XBRL + public market data", "origin": c["source"], "primary": c["primary"]})
    return out + [
            {"case": "synthetic", "name": e.g.tenant, "classification": e.g.classification, "label": "SYNTHETIC · EVALUATION",
             "as_of": str(e.g.as_of), "source": "Meridian simulator — transaction-level capabilities only", "primary": False}]


# ------------------------------------------------------------------------------------------- value at stake (v3.5)
def _slim(c: dict) -> dict:
    keep = ("cik", "name", "tickers", "sector", "sic", "sic_description", "fiscal_year_end", "revenue_usd", "cogs_usd",
            "receivables_usd", "inventory_usd", "payables_usd", "trade_working_capital_usd", "dso", "dio", "dpo", "ccc",
            "peer_group", "benchmark", "opportunity", "percentile_ccc", "rank_excl_payables", "review_levers",
            "review_reason")
    return {k: c.get(k) for k in keep}


@app.get("/v1/benchmark/study", tags=["value-at-stake"])
def benchmark_study(top: Annotated[int, Query(ge=1, le=500)] = 25, _: Principal = Depends(principal)):
    """Cross-company working-capital value at stake from SEC filings (fiscal year 2025). PUBLIC_REAL inputs,
    CALCULATED metrics, ESTIMATED opportunity."""
    from meridian.benchmark.study import run_study

    s = run_study()
    return {**{k: v for k, v in s.items() if k not in ("companies", "excluded")},
            "top_opportunities": [_slim(c) for c in s["companies"][:top]],
            "segment_review": [_slim(c) for c in s["companies"] if c.get("review_levers")],
            "excluded": s["excluded"]}


@app.get("/v1/benchmark/companies", tags=["value-at-stake"])
def benchmark_companies(sector: str | None = None, q: Annotated[str | None, Query(max_length=60)] = None,
                        _: Principal = Depends(principal)):
    from meridian.benchmark.study import run_study

    out = run_study()["companies"]
    if sector:
        out = [c for c in out if c["sector"] == sector]
    if q:
        qq = q.upper()
        out = [c for c in out if qq in c["name"].upper() or qq in [t.upper() for t in c.get("tickers", [])]]
    return [_slim(c) for c in out]


@app.get("/v1/benchmark/company/{query}", tags=["value-at-stake"])
def benchmark_company(query: Annotated[str, Path_(max_length=40)], _: Principal = Depends(principal)):
    """One company's position against its peers, with filing-level lineage for every input."""
    from meridian.benchmark.study import find_company, run_study

    c = find_company(query)
    if c is None:
        raise HTTPException(404, "Not in the FY2025 study (needs revenue ≥ $1B and all five items tagged at the "
                                 "same fiscal-year end)")
    if "peer_group" not in c:
        return {"company": c, "benchmarked": False}
    code = c["peer_group"]["code"]
    peers = [_slim(x) for x in run_study()["companies"]
             if x.get("peer_group", {}).get("code") == code or (c["peer_group"]["level"] == "sector"
                                                               and x["sector"] == c["sector"])]
    full = next(x for x in run_study()["companies"] if x["cik"] == c["cik"])
    return {"company": {**_slim(full), "lineage": full["lineage"]}, "benchmarked": True,
            "peers": sorted(peers, key=lambda x: x["ccc"])[:60]}


@app.get("/v1/benchmark/forecast-validation", tags=["value-at-stake"])
def benchmark_forecast_validation(_: Principal = Depends(principal)):
    from meridian.benchmark.forecast_validation import run_validation

    return run_validation()


@app.get("/v1/benchmark/report", tags=["value-at-stake"], response_class=HTMLResponse)
def benchmark_report(p: Principal = Depends(principal)):
    """Board-style report (self-contained HTML, print to PDF from the browser)."""
    from meridian.benchmark.report import render

    AuditLedger(get_store()).record(p.username, "report_generated", {"report": "value_at_stake_fy2025"})
    return HTMLResponse(render(), headers={"Content-Disposition": 'attachment; filename="Meridian_Value_at_Stake_FY2025.html"'})


@app.get("/v1/public/overview", tags=["public-company"])
def public_overview(cik: int | None = None, _: Principal = Depends(principal)):
    return _pcx(cik).overview()


@app.get("/v1/public/metrics", tags=["public-company"])
def public_metrics(cik: int | None = None, _: Principal = Depends(principal)):
    return list(_pcx(cik).metrics.values())


@app.get("/v1/public/metrics/{metric_id}", tags=["public-company"])
def public_metric(metric_id: str, cik: int | None = None, _: Principal = Depends(principal)):
    pc = _pcx(cik)
    m = pc.metrics.get(metric_id)
    if m is None:
        raise HTTPException(404, "Unknown metric")
    used = [{"rec_id": x["rec_id"], "short_id": x["short_id"], "title": x["title"]} for x in _all_recs("latest", "public", pc.cik)
            if any(ev["id"] == metric_id for ev in x["evidence"])]
    return {**m, "used_by": used}


@app.get("/v1/public/peers", tags=["public-company"])
def public_peers(cik: int | None = None, _: Principal = Depends(principal)):
    return _pcx(cik).peers()


@app.get("/v1/public/forecast", tags=["public-company"])
def public_forecast(cik: int | None = None, _: Principal = Depends(principal)):
    return _pcx(cik).forecast()


@app.get("/v1/public/scenarios", tags=["public-company"])
def public_scenarios(cik: int | None = None, _: Principal = Depends(principal)):
    return _pcx(cik).scenarios()


@app.get("/v1/public/scenario", tags=["public-company"])
def public_scenario(revenue_change_pct: Annotated[float, Query(ge=-60, le=60)] = 0.0,
                    dso_change_days: Annotated[float, Query(ge=-60, le=60)] = 0.0,
                    dio_change_days: Annotated[float, Query(ge=-90, le=90)] = 0.0,
                    rate_shock_bps: Annotated[float, Query(ge=-300, le=500)] = 0.0,
                    spread_shock_bps: Annotated[float, Query(ge=-200, le=600)] = 0.0, cik: int | None = None,
                    _: Principal = Depends(principal)):
    return _pcx(cik).scenario(revenue_change_pct, dso_change_days, dio_change_days, rate_shock_bps, spread_shock_bps)


@app.get("/v1/public/graph", tags=["public-company"])
def public_graph(cik: int | None = None, _: Principal = Depends(principal)):
    """Financial network for the public company: company → statements → facts → metrics → recommendations →
    policy, plus peers and market data. Built from application data, no private nodes."""
    pc = _pcx(cik)
    nodes, edges = [], []

    def n(i, kind, label, **kw):
        nodes.append({"id": i, "kind": kind, "label": label, **kw})

    n("co", "Company", pc.subject["name"], classification="PUBLIC_REAL")
    groups = {"Liquidity": ["cash", "credit_available", "liquidity_fy", "liquidity_coverage"],
              "Debt": ["short_term_borrowings", "maturity_y1", "ltd_noncurrent", "total_debt", "net_debt", "gross_leverage",
                       "avg_cost_of_debt"],
              "Working capital": ["receivables_trade", "inventory", "payables", "dso", "dio", "dpo", "ccc"],
              "Cash flow": ["ocf_ttm", "capex_ttm", "fcf_ttm", "shareholder_returns_ttm", "ebitda_ttm"],
              "Rates": ["repricing_100bps", "refi_cost_delta"]}
    for gname, keys in groups.items():
        n(f"g:{gname}", "Domain", gname)
        edges.append({"from": "co", "to": f"g:{gname}", "rel": "HAS"})
        for k in keys:
            m = pc.metrics.get(k)
            if not m:
                continue
            n(f"m:{k}", "Metric", m["label"], value=m["value"], unit=m["unit"], basis=m["basis"])
            edges.append({"from": f"g:{gname}", "to": f"m:{k}", "rel": "MEASURES"})
            for f in (m.get("lineage") or {}).get("facts", [])[:6]:
                fl = f"f:{f['accession']}"
                if not any(x["id"] == fl for x in nodes):
                    n(fl, "Filing", f"{f['form']} {f['filed']}", accession=f["accession"])
                edges.append({"from": fl, "to": f"m:{k}", "rel": "SOURCE_OF"})
    for p_ in pc.snap.get("peers", []):
        pid = f"peer:{p_['cik']}"
        n(pid, "Peer", p_.get("label") or p_["company"], dso=p_["dso_days"], dio=p_.get("dio_days"),
          classification="PUBLIC_REAL")
        edges.append({"from": "co", "to": pid, "rel": "BENCHMARKED_AGAINST"})
    for lab, key in (("SOFR", "sofr_pct"), ("UST 5Y", "ust5_pct"), ("Single-A OAS", "a_oas_pct"), ("BBB OAS", "bbb_oas_pct")):
        n(f"mkt:{key}", "MarketData", f"{lab} {pc.rates[key]}%", classification="PUBLIC_REAL")
    edges += [{"from": "mkt:ust5_pct", "to": "m:refi_cost_delta", "rel": "INPUT"},
              {"from": "mkt:a_oas_pct", "to": "m:refi_cost_delta", "rel": "INPUT"}]
    for r in _all_recs("latest", "public", pc.cik):
        n(f"r:{r['short_id']}", "Recommendation", f"{r['short_id']} {r['title']}", status=r["status"])
        for ev in r["evidence"]:
            edges.append({"from": f"m:{ev['id']}", "to": f"r:{r['short_id']}", "rel": "SUPPORTS"})
        for c in r["policy_refs"]:
            if not any(x["id"] == f"p:{c}" for x in nodes):
                n(f"p:{c}", "PolicyClause", c)
            edges.append({"from": f"p:{c}", "to": f"r:{r['short_id']}", "rel": "GOVERNS"})
    ids = {x["id"] for x in nodes}
    return {"nodes": nodes, "edges": [e for e in edges if e["from"] in ids and e["to"] in ids]}


@app.post("/v1/public/runs", tags=["public-company"])
async def public_run(req: PublicRunRequest, p: Principal = Depends(require("run_agents"))):
    """Run the governed agent chain on a REAL public company (or answer a question about it)."""
    _pcx(req.cik)
    return await svc().run_public(req.question, requested_by=p.username, cik=req.cik)


# ------------------------------------------------------------------------------------------- analytics v3
def _sc(scenario: str | None) -> Scenario | None:
    return scenario_from_any(scenario) if scenario else None


@app.get("/v1/liquidity/overview", tags=["liquidity"])
def liquidity_overview(_: Principal = Depends(principal)):
    e = get_engine()
    return {"waterfall": e.liquidity_waterfall(), "bridge": e.liquidity_bridge(), "forward": e.forward_bridge(),
            "concentration": e.concentration(), "fx": e.fx_exposure()}


@app.get("/v1/liquidity/forward-bridge", tags=["liquidity"])
def forward_bridge(scenario: str | None = None, _: Principal = Depends(principal)):
    return get_engine().forward_bridge(_sc(scenario))


@app.get("/v1/debt", tags=["debt"])
def debt(_: Principal = Depends(principal)):
    return get_engine().debt_profile()


@app.get("/v1/covenants", tags=["debt"])
def covenants_ep(scenario: str | None = None, ebitda_change_pct: float = 0.0, _: Principal = Depends(principal)):
    if not -0.9 <= ebitda_change_pct <= 1.0:
        raise HTTPException(422, "ebitda_change_pct must be between -0.9 and 1.0")
    return get_engine().covenants(_sc(scenario), ebitda_change_pct)


@app.get("/v1/working-capital/drivers", tags=["working capital"])
def wc_drivers(_: Principal = Depends(principal)):
    e = get_engine()
    return {"dso": e.dso_drivers(), "ap": e.ap_analysis()}


@app.get("/v1/risks", tags=["risk"])
def risks(_: Principal = Depends(principal)):
    return get_engine().risks()


@app.get("/v1/market", tags=["market data"])
def market(_: Principal = Depends(principal)):
    return get_engine().market_overview()


@app.get("/v1/metrics", tags=["lineage"])
def metrics_all(_: Principal = Depends(principal)):
    return list(get_engine().metrics().values())


@app.get("/v1/metrics/{metric_id}/lineage", tags=["lineage"])
def metric_lineage(metric_id: str, _: Principal = Depends(principal)):
    """Source → Dataset → Date → Transformation → Calculation → Agent/Model → Recommendation."""
    from meridian.platform.catalog import upstream

    e = get_engine()
    m = e.metrics().get(metric_id)
    if not m:
        raise HTTPException(404, "Unknown metric")
    cat = e.catalog()
    by = {d["dataset_id"]: d for d in cat}
    chain = []
    for i in m["inputs"]:
        d = by.get(i, {"dataset_id": i})
        chain.append({"input": i, "dataset": d, "upstream": upstream(cat, i)})
    used_by = [{"rec_id": r["rec_id"], "short_id": r["short_id"], "title": r["title"], "status": r["status"]}
               for r in _all_recs("latest") if any(ev["type"] == "metric" and ev["id"] == metric_id for ev in r["evidence"])]
    return {"metric": m, "inputs": chain, "data_product": by.get(m["data_product"]), "used_by": used_by,
            "model": e.model.card if "model.ar_delay" in m["inputs"] or m["data_product"] in
            {"analytical.liquidity_forecast", "analytical.dso_bridge", "analytical.forecast_accuracy"} else None}


@app.get("/v1/catalog", tags=["lineage"])
def catalog(_: Principal = Depends(principal)):
    return get_engine().catalog()


@app.get("/v1/connectors", tags=["data"])
def connectors(_: Principal = Depends(principal)):
    return get_engine().connectors()


_GRAPH_CACHE: dict = {}


def _kg():
    from meridian.analytics import graph_intel as gi

    runs = get_store().list_runs(10)
    key = tuple((r["run_id"], len(r.get("actions", [])), sum(a["status"] != "PROPOSED" for a in r.get("actions", [])))
                for r in runs)
    if _GRAPH_CACHE.get("key") != key:
        _GRAPH_CACHE.update(key=key, g=gi.build(get_engine(), runs))
    return _GRAPH_CACHE["g"]


@app.get("/v1/graph/v3", tags=["relationships"])
def graph_v3(kinds: str = "Organization,Entity,Bank,BankAccount,DebtFacility,Covenant,Customer,BusinessUnit,Risk,"
                          "Recommendation", node: str | None = None, depth: int = 1, limit: int = 300,
             _: Principal = Depends(principal)):
    from meridian.analytics import graph_intel as gi

    G = _kg()
    if node and node not in G.nodes:
        raise HTTPException(404, "Unknown node")
    v = gi.view(G, set(kinds.split(",")), node, min(max(depth, 1), 3), min(limit, 800))
    return {**v, "stats": G.stats()}


@app.get("/v1/graph/query/{name}", tags=["relationships"])
def graph_query(name: str, arg: str | None = None, _: Principal = Depends(principal)):
    from meridian.analytics import graph_intel as gi

    G = _kg()
    e = get_engine()
    q = {"excess_cash": lambda: gi.q_excess_cash(G), "concentration": lambda: gi.q_concentration(G),
         "overdue_drivers": lambda: gi.q_overdue_drivers(G),
         "supplier_opportunities": lambda: gi.q_supplier_opportunities(G, e.g.facility.rate),
         "refinancing": lambda: gi.q_refinancing(G), "covenants": lambda: gi.q_covenants(G),
         "source_dependencies": lambda: gi.q_source_dependencies(G, arg or "market.sofr"),
         "impact": lambda: gi.q_impact(G, arg or "company.receivables")}
    if name not in q:
        raise HTTPException(404, f"Unknown query; valid: {sorted(q)}")
    return {"query": name, **q[name]()}


@app.get("/v1/policy/documents", tags=["policy"])
def policy_documents(_: Principal = Depends(principal)):
    from meridian.nlp.rag import DOC_DIR, chunk_document, doc_meta

    store = get_store()
    quarantined = {c["chunk_id"] for c in store.all_chunks() if c.get("quarantined")}
    out = []
    for p in sorted(DOC_DIR.glob("*.md")):
        text = p.read_text(encoding="utf-8")
        chunks = chunk_document(p)
        out.append({"source": p.name, "title": text.splitlines()[0].lstrip("# "), **doc_meta(text),
                    "clauses": [c["clause_id"] for c in chunks], "sha256": chunks[0]["doc_sha256"] if chunks else None,
                    "quarantined_clauses": [c["clause_id"] for c in chunks if c["chunk_id"] in quarantined]})
    return out


@app.get("/v1/alerts", tags=["system"])
def alerts(p: Principal = Depends(principal), case: Literal["public", "synthetic"] = "synthetic", cik: int | None = None):
    if case == "public":
        pc = _pcx(cik)
        out = [{"severity": "warning", "title": f"{r['short_id']} {r['title']}", "category": r["category"],
                "link": f"#/recommendations?r={r['rec_id']}"} for r in _all_recs("latest", "public", pc.cik)
               if r["status"] in {"PROPOSED", "IN_REVIEW"}]
        out += [{"severity": "warning", "title": f"{d['source_id']}: {'; '.join(d['issues'])}", "category": "DATA",
                 "link": "#/lineage"} for d in pc.provenance() if d.get("issues")]
        mine = [x for x in approval_queue(p) if x["can_approve"] and x["case"] == "public" and x["cik"] == pc.cik]
        if mine:
            out.insert(0, {"severity": "info", "title": f"{len(mine)} actions awaiting your decision", "category": "APPROVAL",
                           "link": "#/approvals"})
        return out
    e = get_engine()
    out = [{"severity": "critical" if r["score"] >= 16 else "warning", "title": r["title"], "category": r["category"],
            "link": "#/risk"} for r in e.risks() if r["score"] >= 12]
    for d in e.provenance():
        if d.get("freshness") == "STALE" or d.get("validation") == "FAILED":
            out.append({"severity": "warning", "title": f"{d['source_id']}: {d.get('freshness')} / {d.get('validation')}",
                        "category": "DATA", "link": "#/lineage"})
    q = approval_queue(p)
    mine = [x for x in q if x["can_approve"]]
    if mine:
        out.insert(0, {"severity": "info", "title": f"{len(mine)} actions awaiting your decision", "category": "APPROVAL",
                       "link": "#/approvals"})
    return out


@app.get("/v1/ops/status", tags=["operations"])
def ops_status(_: Principal = Depends(require("view_operations"))):
    """Operational control center: API, store, data, agents, models, RAG, audit, jobs, latency, quality."""
    from meridian.governance.audit import AuditLedger as _AL

    s, e, store = get_settings(), get_engine(), get_store()
    t = ops.snapshot()
    db: dict[str, Any] = {"kind": store.kind, "status": "UP"}
    if hasattr(store, "ping"):
        try:
            info = store.ping()
            if store.kind == "sqlite":
                db |= {"path": info.get("path"), "schema_version": info.get("schema_version"), "persistent": True}
        except Exception as ex:  # noqa: BLE001
            db = {"kind": store.kind, "status": "DOWN", "error": str(ex)[:200]}
    runs = store.list_runs(50)
    agent_stats: dict[str, dict] = {}
    for r in runs:
        for tr in r.get("trace", []):
            a = agent_stats.setdefault(tr["actor"], {"agent": tr["actor"], "runs": 0, "failed": 0, "latency": []})
            a["runs"] += 1
            a["failed"] += tr["action"] == "FAILED"
            a["latency"].append(tr["latency_ms"])
        for f in r.get("findings", []):
            if f["agent"] in agent_stats:
                agent_stats[f["agent"]].setdefault("grounded", []).append(bool((f.get("validation") or {})
                                                                               .get("numeric_grounding")))
    agents = []
    for a in agent_stats.values():
        lat = sorted(a.pop("latency"))
        g = a.pop("grounded", [])
        agents.append({**a, "p50_ms": lat[len(lat) // 2] if lat else None, "max_ms": lat[-1] if lat else None,
                       "grounded_rate": round(sum(g) / len(g), 3) if g else None,
                       "status": "DEGRADED" if a["failed"] else "OK"})
    decided = [a for r in runs for a in r.get("actions", []) if a["status"] not in {"PROPOSED", "SUBMITTED"}]
    prov = e.provenance()
    audit = _AL(store).verify()
    bt = e.backtest()
    return {
        "platform": {"version": __version__, "mode": s.mode, "replica": t["replica"], "uptime_s": t["uptime_s"],
                     "started_at": t["started_at"]},
        "api": {k: t[k] for k in ("window_requests", "errors_5xx", "denied_403", "unauthenticated_401", "p50_ms", "p95_ms")},
        "routes": t["routes"], "jobs": t["jobs"], "database": db,
        "data": {"sources": len(prov), "stale": [d["source_id"] for d in prov if d.get("freshness") == "STALE"],
                 "failed": [d["source_id"] for d in prov if d.get("validation") == "FAILED"],
                 "last_ingestion": next(iter(store.find("ingestions", 5, sort_desc="created_at")), None),
                 "market_mode": e.market.mode},
        "agents": sorted(agents, key=lambda a: a["agent"]),
        "model": {k: e.model.card.get(k) for k in ("model_id", "model_version", "mae_days_model_p50",
                                                   "mae_days_customer_average", "p10_p90_coverage_conformal", "train_rows")},
        "rag": {"chunks": store.chunk_count(), "quarantined": len(quarantined_chunks(store)), "embedder": s.embedder,
                "vector_backend": "Atlas $vectorSearch" if getattr(store, "atlas", False) else "in-process cosine + BM25"},
        "llm": {"provider": s.llm_provider, "ai_status": s.ai_status(),
                "model": s.llm_model if s.llm_provider != "mock" else "deterministic offline reasoner",
                "live_verified": False if s.llm_provider == "mock" else None},
        "audit": audit,
        "forecast_quality": {"wape_model": bt["wape_model"], "wape_erp": bt["wape_erp_due_date"],
                             "coverage": bt["p10_p90_weekly_coverage"], "t0": bt["t0"]},
        "recommendation_quality": {"runs": len(runs), "decided_actions": len(decided),
                                   "approved": sum(a["status"] in {"APPROVED", "READY"} for a in decided),
                                   "rejected": sum(a["status"] == "REJECTED" for a in decided),
                                   "briefings_grounded": sum(bool((r.get("grounding") or {}).get("grounded")) for r in runs
                                                             if r.get("grounding")),
                                   "briefings": sum(1 for r in runs if r.get("grounding"))},
        "readiness": summarize(static_checks(s) + runtime_checks(s, store)),
    }


@app.post("/v1/admin/users", tags=["admin"])
def create_user_ep(req: UserCreate, p: Principal = Depends(require("admin"))):
    store = get_store()
    if store.get("users", req.username):
        raise HTTPException(409, "User exists")
    if req.role not in ROLES:
        raise HTTPException(422, "Unknown role")
    privileged = req.role in PRIVILEGED_ROLES
    try:
        # A privileged account is created INACTIVE with no role authority until a second administrator confirms it.
        u = create_user(store, req.username, req.display_name, "analyst" if privileged else req.role, req.password)
    except ValueError as e:
        raise HTTPException(422, str(e)) from None
    AuditLedger(store).record(p.username, "user_created", {"user": req.username, "role": u["role"]})
    if privileged:
        u["active"] = False
        store.put("users", {**(store.get("users", req.username) or {}), "active": False})
        u["pending_role_grant"] = _request_role_grant(store, p, req.username, req.role, activate=True)
    return {k: v for k, v in u.items() if k != "password_hash"}


def _request_role_grant(store, p: Principal, username: str, role: str, activate: bool = False) -> dict:
    g = {"grant_id": f"RG-{uuid.uuid4().hex[:10]}", "user": username, "role": role, "activate": activate,
         "requested_by": p.username, "requested_at": datetime.now(UTC).isoformat(), "status": "PENDING"}
    store.put("role_grants", g)
    AuditLedger(store).record(p.username, "role_grant_requested", {k: g[k] for k in ("grant_id", "user", "role")})
    return g


@app.patch("/v1/admin/users/{username}", tags=["admin"])
def update_user(username: str, req: UserUpdate, p: Principal = Depends(require("admin"))):
    store = get_store()
    u = store.get("users", username)
    if not u:
        raise HTTPException(404, "Unknown user")
    if username == p.username and req.active is False:
        raise HTTPException(409, "Administrators cannot deactivate themselves")
    if username == p.username and req.role is not None and req.role != u["role"]:
        AuditLedger(store).record(p.username, "self_privilege_change_denied", {"user": username, "role": req.role})
        raise HTTPException(403, "Administrators cannot change their own role")
    if req.role is not None and req.role not in ROLES:
        raise HTTPException(422, "Unknown role")
    changes: dict = {}
    if req.active is not None:
        if req.active and not u.get("active", True) and any(
                g["user"] == username and g["status"] == "PENDING" and g.get("activate")
                for g in store.find("role_grants")):
            raise HTTPException(409, "Account is awaiting a second administrator's confirmation of its role grant")
        u["active"] = req.active
        changes["active"] = req.active
    pending = None
    if req.role is not None and req.role != u["role"]:
        if req.role in PRIVILEGED_ROLES:
            pending = _request_role_grant(store, p, username, req.role)        # four-eyes: not applied yet
        else:
            u["role"] = req.role
            changes["role"] = req.role
    if req.unlock:
        u["failed_logins"] = 0
        changes["unlocked"] = True
    store.put("users", u)
    AuditLedger(store).record(p.username, "user_updated", {"user": username, **changes})
    return {**{k: v for k, v in u.items() if k != "password_hash"}, **({"pending_role_grant": pending} if pending else {})}


@app.get("/v1/admin/role-grants", tags=["admin"])
def role_grants(_: Principal = Depends(require("admin"))):
    return sorted(get_store().find("role_grants"), key=lambda g: g["requested_at"], reverse=True)


@app.post("/v1/admin/role-grants/{grant_id}/{verdict}", tags=["admin"])
def decide_role_grant(grant_id: str, verdict: Literal["confirm", "reject"], p: Principal = Depends(require("admin"))):
    """Second-administrator confirmation of a privileged role grant. The requester and the grantee cannot confirm."""
    store = get_store()
    g = store.get("role_grants", grant_id)
    if not g:
        raise HTTPException(404, "Unknown role grant")
    if g["status"] != "PENDING":
        raise HTTPException(409, f"Role grant already {g['status']}")
    if p.username in (g["requested_by"], g["user"]):
        AuditLedger(store).record(p.username, "role_grant_sod_denied", {"grant_id": grant_id})
        raise HTTPException(403, "Four-eyes: the requesting administrator or the grantee cannot confirm this grant")
    g.update(status="CONFIRMED" if verdict == "confirm" else "REJECTED", decided_by=p.username,
             decided_at=datetime.now(UTC).isoformat())
    if verdict == "confirm":
        u = store.get("users", g["user"])
        if not u:
            raise HTTPException(409, "Grantee no longer exists")
        u["role"] = g["role"]
        if g.get("activate"):
            u["active"] = True
        store.put("users", u)
    store.put("role_grants", g)
    AuditLedger(store).record(p.username, f"role_grant_{g['status'].lower()}",
                              {k: g[k] for k in ("grant_id", "user", "role", "requested_by")})
    return g


@app.get("/v1/admin/roles", tags=["admin"])
def roles(_: Principal = Depends(principal)):
    pol = load_policy()
    return {"roles": [{"role": r, "description": d, "permissions": sorted(k for k, v in PERMISSIONS.items() if r in v),
                       "approval_rank": pol.role_hierarchy.get(r, 0), "scope": pol.role_scopes.get(r)}
                      for r, d in ROLES.items()],
            "approval_matrix": [r.model_dump() for r in pol.approval_matrix],
            "execution": pol.execution.model_dump()}


# ------------------------------------------------------------------------------------------- console
if CONSOLE_DIR.exists():
    app.mount("/console", StaticFiles(directory=CONSOLE_DIR), name="console")

    @app.get("/", include_in_schema=False)
    def console_index():
        return FileResponse(CONSOLE_DIR / "index.html")


@app.exception_handler(RuntimeError)
async def runtime_error(_: Request, exc: RuntimeError):
    return JSONResponse(status_code=503, content={"detail": str(exc)})
