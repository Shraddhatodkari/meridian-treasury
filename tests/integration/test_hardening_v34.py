"""v3.4 P0 hardening — negative tests: privilege escalation, four-eyes role grants, login enumeration, decision
replay, superseded runs, policy-version drift, concurrent approvals (compare-and-swap) and decision audit content."""

import threading

import pytest
from fastapi.testclient import TestClient

from meridian.api.main import app
from meridian.orchestration.service import TreasuryService
from meridian.security.auth import DEMO_PASSWORD
from meridian.store.factory import get_store


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def login(client, user, pw=DEMO_PASSWORD):
    r = client.post("/v1/auth/login", json={"username": user, "password": pw})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


# ------------------------------------------------------------------------------------------------ privilege escalation
def test_admin_cannot_change_own_role(client):
    ada = login(client, "ada")
    r = client.patch("/v1/admin/users/ada", json={"role": "cfo"}, headers=ada)
    assert r.status_code == 403
    assert get_store().get("users", "ada")["role"] == "admin"


def test_privileged_grant_needs_a_second_admin(client):
    ada = login(client, "ada")
    # creating a CFO account through the API: created INACTIVE as analyst with a pending grant
    r = client.post("/v1/admin/users", json={"username": "puppet.cfo", "display_name": "Puppet", "role": "cfo",
                                             "password": "Puppet-Password-2026"}, headers=ada)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["role"] == "analyst" and body["active"] is False and body["pending_role_grant"]["status"] == "PENDING"
    gid = body["pending_role_grant"]["grant_id"]
    # the puppet cannot sign in, and the requesting admin cannot confirm or re-activate it
    assert client.post("/v1/auth/login", json={"username": "puppet.cfo", "password": "Puppet-Password-2026"}).status_code == 401
    assert client.post(f"/v1/admin/role-grants/{gid}/confirm", headers=ada).status_code == 403
    assert client.patch("/v1/admin/users/puppet.cfo", json={"active": True}, headers=ada).status_code == 409
    # elevating an existing analyst to treasurer is also only a request
    r = client.patch("/v1/admin/users/ana", json={"role": "treasurer"}, headers=ada)
    assert r.status_code == 200 and r.json()["role"] == "analyst" and r.json()["pending_role_grant"]["role"] == "treasurer"
    # non-admins cannot confirm at all
    assert client.post(f"/v1/admin/role-grants/{gid}/confirm", headers=login(client, "carla")).status_code == 403
    audit = [a["action"] for a in client.get("/v1/governance/audit", headers=login(client, "iris")).json()]
    assert "role_grant_requested" in audit and "role_grant_sod_denied" in audit and "self_privilege_change_denied" in audit
    # bootstrap a second admin out-of-band (CLI path = direct store write), who CAN confirm
    from meridian.security.auth import create_user
    create_user(get_store(), "ada2", "Second Admin", "admin", "Second-Admin-Pass-2026")
    ada2 = login(client, "ada2", "Second-Admin-Pass-2026")
    r = client.post(f"/v1/admin/role-grants/{gid}/confirm", headers=ada2)
    assert r.status_code == 200 and r.json()["status"] == "CONFIRMED" and r.json()["decided_by"] == "ada2"
    u = get_store().get("users", "puppet.cfo")
    assert u["role"] == "cfo" and u["active"] is True
    assert client.post(f"/v1/admin/role-grants/{gid}/confirm", headers=ada2).status_code == 409    # no replay
    # reject the pending ana→treasurer grant so later tests keep ana as analyst
    pend = [g for g in client.get("/v1/admin/role-grants", headers=ada2).json() if g["status"] == "PENDING"]
    for g in pend:
        assert client.post(f"/v1/admin/role-grants/{g['grant_id']}/reject", headers=ada2).status_code == 200
    assert get_store().get("users", "ana")["role"] == "analyst"


def test_non_privileged_role_change_applies_directly(client):
    ada = login(client, "ada")
    r = client.post("/v1/admin/users", json={"username": "temp.viewer", "display_name": "Temp", "role": "auditor",
                                             "password": "Temp-Viewer-Pass-2026"}, headers=ada)
    assert r.status_code == 200 and r.json()["role"] == "auditor" and r.json().get("active", True)
    assert client.patch("/v1/admin/users/temp.viewer", json={"role": "wc_manager"}, headers=ada).json()["role"] == "wc_manager"


def test_role_change_invalidates_existing_token(client):
    ada = login(client, "ada")
    client.post("/v1/admin/users", json={"username": "temp.role", "display_name": "Temp", "role": "auditor",
                                         "password": "Temp-Role-Pass-2026"}, headers=ada)
    t = login(client, "temp.role", "Temp-Role-Pass-2026")
    assert client.get("/v1/cases", headers=t).status_code == 200
    client.patch("/v1/admin/users/temp.role", json={"role": "analyst"}, headers=ada)
    assert client.get("/v1/cases", headers=t).status_code == 401


# ------------------------------------------------------------------------------------------------ authentication
def test_lockout_does_not_reveal_account_existence(client):
    ada = login(client, "ada")
    client.post("/v1/admin/users", json={"username": "lock.me", "display_name": "Lock", "role": "auditor",
                                         "password": "Lock-Me-Password-2026"}, headers=ada)
    for _ in range(6):
        client.post("/v1/auth/login", json={"username": "lock.me", "password": "wrong-password-x"})
    locked = client.post("/v1/auth/login", json={"username": "lock.me", "password": "Lock-Me-Password-2026"})
    unknown = client.post("/v1/auth/login", json={"username": "nobody.here", "password": "whatever-12345"})
    wrong = client.post("/v1/auth/login", json={"username": "ana", "password": "wrong-password-x"})
    assert locked.status_code == unknown.status_code == wrong.status_code == 401
    assert locked.json() == unknown.json() == wrong.json()
    client.patch("/v1/admin/users/lock.me", json={"unlock": True}, headers=ada)
    assert client.post("/v1/auth/login", json={"username": "lock.me", "password": "Lock-Me-Password-2026"}).status_code == 200
    client.patch("/v1/admin/users/ana", json={"unlock": True}, headers=ada)


def test_logout_revokes_token(client):
    h = login(client, "iris")
    assert client.post("/v1/auth/logout", headers=h).status_code in (200, 204)
    assert client.get("/v1/cases", headers=h).status_code == 401              # replay of a logged-out token


# ------------------------------------------------------------------------------------------------ approvals
def _fresh_run(client, ana, cik=773840):
    r = client.post("/v1/public/runs", json={"cik": cik}, headers=ana)
    assert r.status_code == 200, r.text
    return r.json()


def test_replay_and_superseded_runs_are_refused(client):
    ana, tom, carla = login(client, "ana"), login(client, "tom"), login(client, "carla")
    old = _fresh_run(client, ana)
    acts = [a for a in old["actions"] if a["status"] in ("PROPOSED", "SUBMITTED")]
    if not acts:
        pytest.skip("this company currently triggers no approvable action")
    a = acts[0]
    approver = carla if a["required_approver"] == "cfo" else tom
    url = f"/v1/runs/{old['run_id']}/actions/{a['action_id']}/decision"
    r = client.post(url, json={"decision": "APPROVE", "comment": "ok"}, headers=approver)
    assert r.status_code == 200, r.text
    dec = r.json()["actions"][[x["action_id"] for x in r.json()["actions"]].index(a["action_id"])]
    assert dec["evidence"]["approval"]["policy_version"] and dec["evidence"]["audit"]["decision_record_hash"]
    # replay of the same decision
    assert client.post(url, json={"decision": "APPROVE", "comment": "again"}, headers=approver).status_code == 409
    # a newer base review supersedes the old one: remaining pending actions there are blocked
    rest = [x for x in old["actions"] if x["action_id"] != a["action_id"] and x["status"] in ("PROPOSED", "SUBMITTED")]
    new = _fresh_run(client, ana)
    q = client.get("/v1/approvals/queue", headers=carla).json()
    olds = [x for x in q if x["run_id"] == old["run_id"]]
    assert all(not x["can_approve"] and "Superseded" in x["blocked_reason"] for x in olds)
    assert all(x["qualified_action_id"] == f"{x['run_id']}/{x['action_id']}" for x in q)
    if rest:
        r = client.post(f"/v1/runs/{old['run_id']}/actions/{rest[0]['action_id']}/decision",
                        json={"decision": "REJECT", "comment": "late"}, headers=carla)
        assert r.status_code == 409 and "superseded" in r.json()["detail"]
    assert new["run_id"] != old["run_id"]


def test_decision_audit_records_policy_version_and_evidence(client):
    iris = login(client, "iris")
    rows = [r for r in client.get("/v1/governance/audit", headers=iris).json() if r["action"] == "action_approve"]
    assert rows
    payload = rows[0]
    text = str(payload)
    assert "policy_version" in text and "evidence" in text


def test_decision_refused_when_policy_version_changed(client):
    ana, carla = login(client, "ana"), login(client, "carla")
    run = _fresh_run(client, ana, cik=18230)
    acts = [a for a in run["actions"] if a["status"] in ("PROPOSED", "SUBMITTED")]
    if not acts:
        pytest.skip("no approvable action")
    st = get_store()
    doc = st.get_run(run["run_id"])
    doc["policy_version"] = "0.0-old"
    st.save_run(doc)
    r = client.post(f"/v1/runs/{run['run_id']}/actions/{acts[0]['action_id']}/decision",
                    json={"decision": "REJECT", "comment": "x"}, headers=carla)
    assert r.status_code == 409 and "Policy changed" in r.json()["detail"]


def test_concurrent_decisions_only_one_wins():
    """Two threads decide the same action at the same time: compare-and-swap lets exactly one succeed."""
    st = get_store()
    svc = TreasuryService()
    run = st.list_runs(1)[0]
    rid = f"{run['run_id']}-CAS"
    from meridian.governance.policy import load_policy
    clone = {**run, "run_id": rid, "is_base_case": False, "rev": 0, "policy_version": load_policy().version,
             "actions": [{**run["actions"][0], "status": "SUBMITTED", "required_approver": "treasurer",
                          "submitted_by": {"user": "ana", "role": "analyst"}, "type": "REFINANCING_READINESS_PLAN",
                          "amount_usd": 1_000_000}]
             if run["actions"] else []}
    if not clone["actions"]:
        pytest.skip("no action to clone")
    st.save_run(clone)
    aid = clone["actions"][0]["action_id"]
    barrier, results = threading.Barrier(2), []

    def go(user, role):
        barrier.wait()
        try:
            svc.decide(rid, aid, "REJECT", user, role, "concurrent")
            results.append("ok")
        except ValueError as e:
            results.append(str(e))
        except Exception as e:   # noqa: BLE001 — record any other refusal
            results.append(type(e).__name__)

    ts = [threading.Thread(target=go, args=("carla", "cfo")), threading.Thread(target=go, args=("tom", "treasurer"))]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert results.count("ok") == 1, results
    final = st.get_run(rid)["actions"][0]
    assert final["status"] == "REJECTED"


@pytest.mark.parametrize("kind", ["memory", "sqlite"])
def test_store_compare_and_swap(tmp_path, kind):
    if kind == "memory":
        from meridian.store.base import MemoryStore
        s = MemoryStore()
    else:
        from meridian.store.sqlite import SQLiteStore
        s = SQLiteStore(tmp_path / "cas.db")
    s.save_run({"run_id": "R1", "created_at": "x", "rev": 0})
    assert s.save_run_cas({"run_id": "R1", "created_at": "x", "rev": 1, "v": "a"}, 0)
    assert not s.save_run_cas({"run_id": "R1", "created_at": "x", "rev": 1, "v": "b"}, 0)     # stale writer loses
    assert s.get_run("R1")["v"] == "a"


# ------------------------------------------------------------------------------------------------ outage handling
def test_sec_outage_returns_503_and_stores_nothing(client, monkeypatch):
    import httpx

    from meridian.public_company import sec

    before = len(get_store().find("public_companies"))

    def down(*a, **k):
        raise httpx.ConnectError("simulated SEC outage")

    monkeypatch.setattr(sec, "resolve", down)
    monkeypatch.setattr(sec, "build_snapshot", down)
    r = client.post("/v1/companies", json={"query": "NVDA"}, headers=login(client, "ana"))
    assert r.status_code == 503 and "no data was fabricated" in r.json()["detail"]
    assert len(get_store().find("public_companies")) == before


def test_no_cookies_so_csrf_does_not_apply(client):
    """Sessions are bearer tokens sent in the Authorization header, never cookies: a cross-site request carries no
    credential, so CSRF does not apply. If cookies are ever introduced, CSRF protection becomes mandatory."""
    r = client.post("/v1/auth/login", json={"username": "iris", "password": DEMO_PASSWORD})
    assert "set-cookie" not in {k.lower() for k in r.headers}
    assert client.get("/v1/cases").status_code == 401                       # no ambient credential
