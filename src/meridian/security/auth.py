"""Authentication & RBAC.

* Users are persisted in the store (MongoDB in production) with PBKDF2-SHA256 password hashes
  (600k iterations, per-user salt) and lockout after repeated failures.
* Sessions are stateless signed JWTs (HS256, MERIDIAN_JWT_SECRET) — no server-side session state,
  so any replica can validate any request.
* Production deployments typically front this with the corporate IdP (Entra ID / Okta via OIDC);
  the role model and checks below stay the same.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import uuid
from datetime import UTC, datetime, timedelta

import jwt
from pydantic import BaseModel

from meridian.config import get_settings

ROLES = {
    "analyst": "Finance analyst — runs analyses, submits recommendations, approves collections escalations",
    "wc_manager": "Working Capital Manager — collections and supplier-payment decisions (scoped authority)",
    "treasurer": "Group Treasurer — approves transfers up to policy limits (TP-2.1)",
    "cfo": "Chief Financial Officer — approves large transfers, debt actions, controlled-cash moves",
    "auditor": "Internal audit — read-only access to evidence, audit trail and governance",
    "admin": "Platform administrator — manages users and data loads; cannot approve money movements",
}
# Feature permissions (approval authority is governed separately by the policy approval matrix)
PERMISSIONS = {
    "view": set(ROLES),
    "run_agents": {"analyst", "wc_manager", "treasurer", "cfo"},
    "submit_recommendations": {"analyst", "wc_manager", "treasurer", "cfo"},
    "decide_actions": {"analyst", "wc_manager", "treasurer", "cfo"},
    "prepare_execution": {"treasurer", "cfo"},
    "release_payments": {"treasurer", "cfo"},
    "view_bank_details": {"treasurer", "cfo"},
    "verify_audit": {"treasurer", "cfo", "auditor", "admin"},
    "view_operations": {"treasurer", "cfo", "auditor", "admin"},
    "admin": {"admin"},
}
PBKDF2_ITERATIONS = 600_000
MAX_FAILED = 5
# Published demo credential for the SYNTHETIC demo tenant only. Demo accounts are seeded only in demo mode, are
# flagged `demo_account`, and authenticate() refuses them in any other mode — so this value can never open production.
DEMO_PASSWORD = "Meridian-Demo-2026"  # noqa: S105
DEMO_USERS = [("ana", "Ana Lopez", "analyst"), ("wendy", "Wendy Osei", "wc_manager"), ("tom", "Tom Becker", "treasurer"),
              ("carla", "Carla Mendes", "cfo"),
              ("iris", "Iris Novak", "auditor"), ("ada", "Ada Kim", "admin")]


class Principal(BaseModel):
    username: str
    display_name: str
    role: str
    auth: str = "jwt"   # jwt | service_key
    jti: str | None = None
    expires: int | None = None

    def can(self, permission: str) -> bool:
        return self.role in PERMISSIONS.get(permission, set())


class AuthError(PermissionError):
    pass


def hash_password(password: str, salt: bytes | None = None) -> str:
    salt = salt or os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PBKDF2_ITERATIONS)
    return f"pbkdf2_sha256${PBKDF2_ITERATIONS}${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, it, salt, dk = stored.split("$")
    except ValueError:
        return False
    calc = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), int(it))
    return hmac.compare_digest(calc.hex(), dk)


def create_user(store, username: str, display_name: str, role: str, password: str) -> dict:
    if role not in ROLES:
        raise ValueError(f"unknown role {role}; valid: {sorted(ROLES)}")
    if len(password) < 12:
        raise ValueError("password must be at least 12 characters")
    user = {"username": username, "display_name": display_name, "role": role, "password_hash": hash_password(password),
            "active": True, "failed_logins": 0, "created_at": datetime.now(UTC).isoformat()}
    store.put("users", user)
    return {k: v for k, v in user.items() if k != "password_hash"}


def seed_demo_users(store) -> None:
    for username, name, role in DEMO_USERS:
        if store.get("users", username) is None:
            create_user(store, username, name, role, DEMO_PASSWORD)
            store.put("users", {**store.get("users", username), "demo_account": True})


def authenticate(store, username: str, password: str) -> dict:
    user = store.get("users", username)
    if user and user.get("demo_account") and not get_settings().is_demo:
        user = None  # demo accounts never authenticate outside demo mode
    if not user or not user.get("active", True):
        hash_password(password)  # equalise timing for unknown users
        raise AuthError("Invalid username or password")
    if user.get("failed_logins", 0) >= MAX_FAILED:
        raise AuthError("Account locked after repeated failed sign-ins — contact the platform administrator")
    if not verify_password(password, user["password_hash"]):
        user["failed_logins"] = user.get("failed_logins", 0) + 1
        store.put("users", user)
        raise AuthError("Invalid username or password")
    user["failed_logins"] = 0
    user["last_login"] = datetime.now(UTC).isoformat()
    store.put("users", user)
    return user


def issue_token(user: dict) -> tuple[str, datetime]:
    s = get_settings()
    exp = datetime.now(UTC) + timedelta(minutes=s.jwt_ttl_minutes)
    claims = {"sub": user["username"], "name": user["display_name"], "role": user["role"], "exp": exp,
              "iat": datetime.now(UTC), "jti": uuid.uuid4().hex, "iss": "meridian", "mode": s.mode}
    return jwt.encode(claims, s.signing_key(), algorithm="HS256"), exp


def decode_token(token: str, store=None) -> Principal:
    try:
        c = jwt.decode(token, get_settings().signing_key(), algorithms=["HS256"], issuer="meridian")
    except jwt.PyJWTError as e:
        raise AuthError(f"Invalid or expired session ({type(e).__name__})") from e
    if store is not None and c.get("jti") and store.get("revoked_tokens", c["jti"]):
        raise AuthError("Session has been signed out")
    if store is not None:
        u = store.get("users", c["sub"])
        if u is not None and (not u.get("active", True) or u.get("role") != c["role"]):
            raise AuthError("Account disabled or role changed — sign in again")
    return Principal(username=c["sub"], display_name=c.get("name", c["sub"]), role=c["role"], jti=c.get("jti"),
                     expires=c.get("exp"))


def revoke(store, principal) -> None:
    if principal.jti:
        store.put("revoked_tokens", {"jti": principal.jti, "user": principal.username, "exp": principal.expires})


def service_principal(api_key: str) -> Principal | None:
    for key, (role, user) in get_settings().principals().items():
        if secrets.compare_digest(key, api_key):
            return Principal(username=user, display_name=f"{user} (service key)", role=role, auth="service_key")
    return None
