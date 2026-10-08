"""Authentication, RBAC, approval workflow, segregation of duties and audit evidence — through the real HTTP API."""

import jwt
import pytest
from fastapi.testclient import TestClient

from meridian.api.main import app
from meridian.config import get_settings
from meridian.security.auth import DEMO_PASSWORD, MAX_FAILED, create_user
from meridian.store.factory import get_store


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def login(client, user, password=DEMO_PASSWORD):
    r = client.post("/v1/auth/login", json={"username": user, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest.fixture(scope="module")
def H(client):
    return {u: login(client, u) for u in ("ana", "tom", "carla", "iris", "ada")}


# ------------------------------------------------------------------------------------------------ authentication
def test_login_issues_signed_short_lived_jwt(client):
    r = client.post("/v1/auth/login", json={"username": "carla", "password": DEMO_PASSWORD}).json()
    claims = jwt.decode(r["access_token"], get_settings().signing_key(), algorithms=["HS256"], issuer="meridian")
    assert claims["sub"] == "carla" and claims["role"] == "cfo" and claims["exp"] > claims["iat"]
    assert "decide_actions" in r["user"]["permissions"]


def test_bad_password_and_unknown_user_rejected_identically(client):
    a = client.post("/v1/auth/login", json={"username": "carla", "password": "wrong"})
    b = client.post("/v1/auth/login", json={"username": "nobody", "password": "wrong"})
    assert a.status_code == b.status_code == 401 and a.json() == b.json()


def test_account_lockout_after_repeated_failures(client):
    create_user(get_store(), "lock_me", "Lock Test", "analyst", "Correct-Horse-Battery-9")
    for _ in range(MAX_FAILED):
        assert client.post("/v1/auth/login", json={"username": "lock_me", "password": "nope"}).status_code == 401
    r = client.post("/v1/auth/login", json={"username": "lock_me", "password": "Correct-Horse-Battery-9"})
    assert r.status_code == 401 and "locked" in r.json()["detail"].lower()


def test_tampered_or_foreign_token_rejected(client, H):
    tok = H["ana"]["Authorization"].split()[1]
    forged = jwt.encode({**jwt.decode(tok, options={"verify_signature": False}), "role": "cfo"}, "x" * 40, algorithm="HS256")
    assert client.get("/v1/auth/me", headers={"Authorization": f"Bearer {forged}"}).status_code == 401
    assert client.get("/v1/kpis").status_code == 401
    assert client.get("/v1/kpis", headers={"X-API-Key": "not-a-key"}).status_code == 401


def test_login_and_failures_are_audited(client, H):
    client.post("/v1/auth/login", json={"username": "tom", "password": "not-his-password"})
    rows = client.get("/v1/governance/audit?limit=2000", headers=H["iris"]).json()
    actions = {r["action"] for r in rows}
    assert {"login", "login_failed"} <= actions


# ------------------------------------------------------------------------------------------------ RBAC
@pytest.mark.parametrize("user,method,path,expected", [
    ("iris", "post", "/v1/runs", 403),                 # auditor is read-only
    ("ada", "post", "/v1/runs", 403),                  # admin cannot run agents
    ("ana", "get", "/v1/governance/audit/verify", 403),  # analyst cannot read the full ledger
    ("iris", "get", "/v1/governance/audit/verify", 200),
    ("ana", "get", "/v1/admin/users", 403),
    ("carla", "get", "/v1/admin/users", 403),          # even the CFO is not a platform admin
    ("ada", "get", "/v1/admin/users", 200),
    ("ana", "get", "/v1/system/readiness", 403),
    ("iris", "get", "/v1/kpis", 200),
])
def test_role_permissions(client, H, user, method, path, expected):
    r = getattr(client, method)(path, headers=H[user], **({"json": {}} if method == "post" else {}))
    assert r.status_code == expected, (user, path, r.text[:200])


def test_admin_user_list_never_exposes_password_hashes(client, H):
    users = client.get("/v1/admin/users", headers=H["ada"]).json()
    assert users and all("password_hash" not in u for u in users)


def test_bank_details_masked_for_non_treasury_roles(client, H):
    masked = client.get("/v1/accounts", headers=H["ana"]).json()[0]["iban"]
    clear = client.get("/v1/accounts", headers=H["tom"]).json()[0]["iban"]
    assert "•" in masked and "•" not in clear


# ------------------------------------------------------------------------------------------------ approvals & SoD
@pytest.fixture(scope="module")
def run(client, H):
    r = client.post("/v1/runs", json={}, headers=H["tom"])     # the TREASURER requests the run
    assert r.status_code == 200
    run = r.json()
    assert run["status"] == "AWAITING_APPROVALS" and run["requested_by"] == "tom"
    return run


def _first(run, approver, i=0):
    """i-th action needing `approver`; each test uses its own action so tests stay order-independent."""
    return [a for a in run["actions"] if a["required_approver"] == approver][i]


def decide(client, headers, run, action, decision="APPROVE", comment=""):
    return client.post(f"/v1/runs/{run['run_id']}/actions/{action['action_id']}/decision",
                       json={"decision": decision, "comment": comment}, headers=headers)


def test_recommendations_are_not_transactions(run):
    for a in run["actions"]:
        assert a["status"] == "PROPOSED" and a.get("execution_artifact") is None
        assert "not a transaction" in a["evidence"]["nature"].lower()


def test_queue_explains_who_can_decide(client, H, run):
    q = [x for x in client.get("/v1/approvals/queue", headers=H["tom"]).json() if x["run_id"] == run["run_id"]]
    tre = next(x for x in q if x["required_approver"] == "treasurer")
    cfo = next(x for x in q if x["required_approver"] == "cfo")
    assert not tre["can_approve"] and "Segregation of duties" in tre["blocked_reason"]
    assert not cfo["can_approve"] and "Requires cfo authority" in cfo["blocked_reason"]
    q_cfo = {x["action_id"]: x for x in client.get("/v1/approvals/queue", headers=H["carla"]).json()
             if x["run_id"] == run["run_id"]}
    assert q_cfo[cfo["action_id"]]["can_approve"]


def test_requester_cannot_approve_own_run(client, H, run):
    r = decide(client, H["tom"], run, _first(run, "treasurer", -1))
    assert r.status_code == 403 and "segregation" in r.json()["detail"].lower()


def test_treasurer_blocked_from_cfo_level_action(client, H, run):
    ana_run = client.post("/v1/runs", json={}, headers=H["ana"]).json()   # tom is not the requester here
    r = decide(client, H["tom"], ana_run, _first(ana_run, "cfo"))
    assert r.status_code == 403 and "cfo" in r.json()["detail"].lower()


@pytest.mark.parametrize("user", ["iris", "ada"])
def test_auditor_and_admin_can_never_decide(client, H, run, user):
    assert decide(client, H[user], run, _first(run, "treasurer", -2)).status_code == 403


def test_analyst_cannot_reject_above_authority(client, H, run):
    r = decide(client, H["ana"], run, _first(run, "cfo", -1), "REJECT", "too large")
    assert r.status_code == 403


def test_rejection_requires_written_rationale(client, H, run):
    act = _first(run, "treasurer")
    assert decide(client, H["carla"], run, act, "REJECT", "   ").status_code == 409
    r = decide(client, H["carla"], run, act, "REJECT", "Hold: subsidiary audit in progress")
    assert r.status_code == 200
    rejected = next(a for a in r.json()["actions"] if a["action_id"] == act["action_id"])
    assert rejected["status"] == "REJECTED" and rejected["rejected_by"]["comment"].startswith("Hold")
    assert rejected.get("execution_artifact") is None   # nothing is drafted for a rejected action


def test_cfo_approval_produces_pain001_draft_only(client, H, run):
    act = _first(run, "cfo")
    r = decide(client, H["carla"], run, act, "APPROVE", "Approved per liquidity committee")
    assert r.status_code == 200
    a = next(x for x in r.json()["actions"] if x["action_id"] == act["action_id"])
    art = a["execution_artifact"]
    assert a["status"] == "APPROVED" and a["approved_by"]["user"] == "carla"
    assert art["kind"] == "iso20022_pain001_draft" and art["status"].startswith("DRAFT — NOT TRANSMITTED")
    assert "pain.001" in art["xml"] and len(art["sha256"]) == 16
    assert a["evidence"]["audit"]["decision_record_hash"]
    # a decided action cannot be decided again
    assert decide(client, H["carla"], run, act).status_code == 409


def test_every_decision_and_denial_is_in_the_hash_chain(client, H):
    run = client.post("/v1/runs", json={}, headers=H["tom"]).json()
    tre = [a for a in run["actions"] if a["required_approver"] == "treasurer"]
    assert decide(client, H["tom"], run, tre[0]).status_code == 403                       # SoD denial
    assert decide(client, H["carla"], run, tre[0]).status_code == 200                     # approval
    assert decide(client, H["carla"], run, tre[1], "REJECT", "Not this week").status_code == 200
    trail = client.get(f"/v1/governance/audit/{run['run_id']}", headers=H["iris"]).json()
    acts = [t["action"] for t in trail]
    assert {"action_approve", "action_reject", "approval_denied"} <= set(acts)
    denied = next(t for t in trail if t["action"] == "approval_denied")
    assert denied["actor"] == "tom" and "segregation" in denied["payload"]["reason"].lower()
    v = client.get("/v1/governance/audit/verify", headers=H["iris"]).json()
    assert v["valid"] and v["records"] > 0


def test_no_endpoint_executes_payments(client):
    """v3: the only execution-related endpoint is the controlled release gate (TP-7.2), and it cannot transmit:
    no bank channel is configured, so release is always refused (and audited) in this deployment."""
    paths = [r.path for r in app.routes]
    exec_paths = [p for p in paths if any(w in p.lower() for w in ("execute", "transmit", "release", "/pay"))]
    assert exec_paths == ["/v1/runs/{run_id}/actions/{action_id}/release"]
    from meridian.governance.policy import load_policy
    assert load_policy().execution.transmission_channel == "none"


# ------------------------------------------------------------------------------------------------ console & environment
def test_console_served_and_environment_declares_mode(client):
    assert "Meridian Treasury Console" in client.get("/").text
    assert all(client.get(f"/console/js/{m}.js").status_code == 200 for m in ("app", "core", "p_exec", "p_gov"))
    env = client.get("/v1/system/environment").json()
    assert env["mode"] == "demo" and env["classification"] == "SYNTHETIC" and "synthetic" in env["tenant"].lower()


def test_demo_accounts_cannot_sign_in_outside_demo_mode(monkeypatch):
    from meridian.security import auth
    from meridian.security.auth import AuthError, authenticate
    store = get_store()
    assert store.get("users", "carla")["demo_account"]
    monkeypatch.setattr(auth, "get_settings", lambda: get_settings().model_copy(update={"mode": "production"}))
    with pytest.raises(AuthError):
        authenticate(store, "carla", DEMO_PASSWORD)
