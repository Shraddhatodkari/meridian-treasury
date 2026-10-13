"""Systematic access control (v3.3): EVERY /v1 route refuses anonymous callers, except an explicit public allowlist;
every role's server-side permissions match the documented matrix on direct API calls."""

import re

import pytest
from fastapi.testclient import TestClient
from starlette.routing import Route

from meridian.api.main import app
from meridian.security.auth import DEMO_PASSWORD, PERMISSIONS

PUBLIC = {("POST", "/v1/auth/login"), ("GET", "/v1/system/environment")}


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def _routes():
    for r in app.routes:
        if isinstance(r, Route) and r.path.startswith("/v1"):
            for m in sorted(r.methods - {"HEAD", "OPTIONS"}):
                yield m, r.path


def test_every_v1_route_requires_authentication(client):
    checked, open_ = 0, []
    for method, path in _routes():
        if (method, path) in PUBLIC:
            continue
        url = re.sub(r"\{[^}]+\}", "x", path)
        resp = client.request(method, url, json={})
        checked += 1
        if resp.status_code != 401:
            open_.append((method, path, resp.status_code))
    assert checked > 60 and not open_, open_


def test_garbage_and_expired_tokens_are_rejected(client):
    for tok in ("garbage", "eyJhbGciOiJub25lIn0.eyJzdWIiOiJjYXJsYSJ9.", ""):
        assert client.get("/v1/cases", headers={"Authorization": f"Bearer {tok}"}).status_code == 401


ROLES = {"ana": "analyst", "wendy": "wc_manager", "tom": "treasurer", "carla": "cfo", "iris": "auditor", "ada": "admin"}
PROBES = {   # permission -> a read-only endpoint guarded by exactly that permission
    "verify_audit": ("GET", "/v1/governance/audit"),
    "admin": ("GET", "/v1/admin/users"),
    "view_operations": ("GET", "/v1/ops/status"),
}


@pytest.mark.parametrize("user", ROLES)
def test_role_matrix_matches_documented_permissions(client, user):
    tok = client.post("/v1/auth/login", json={"username": user, "password": DEMO_PASSWORD}).json()["access_token"]
    h = {"Authorization": f"Bearer {tok}"}
    for perm, (method, path) in PROBES.items():
        allowed = ROLES[user] in PERMISSIONS[perm]
        code = client.request(method, path, headers=h).status_code
        assert (code == 200) == allowed and (code == 403) == (not allowed), (user, perm, code)
