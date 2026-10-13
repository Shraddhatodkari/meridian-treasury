"""Create the least-privilege MongoDB role and application user for Meridian.

The API connects as the application user. It may read and write the business collections, but on the `audit`
collection it can only INSERT and FIND: it can never update or delete an audit record. Tampering would need a
separate administrator credential, and the external chain anchor (`meridian audit anchor`) still detects a rewrite.

    MERIDIAN_APP_DB_PASSWORD=... python scripts/mongo_least_privilege.py --admin-uri "mongodb://admin:...@host/admin"
    # then run the API with MERIDIAN_MONGODB_URI=mongodb://meridian_app:...@host/meridian?authSource=meridian

MongoDB privileges are additive, so the role lists every business collection explicitly (no database-wide grant,
which would also cover `audit`). Re-run this script after adding a collection.
"""

from __future__ import annotations

import argparse
import os
import sys

from pymongo import MongoClient


def collections() -> list[str]:
    from meridian.store.base import _COLLECTIONS, DOC_KEYS
    return sorted(set(DOC_KEYS) | set(_COLLECTIONS) | {"meta", "policy_chunks", "artifacts.files", "artifacts.chunks"})


def privileges(db: str) -> list[dict]:
    rw = ["find", "insert", "update", "remove", "createIndex"]
    p = [{"resource": {"db": db, "collection": c}, "actions": rw} for c in collections()]
    p.append({"resource": {"db": db, "collection": "audit"}, "actions": ["find", "insert", "createIndex"]})
    return p


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--admin-uri", required=True)
    ap.add_argument("--db", default="meridian")
    ap.add_argument("--user", default="meridian_app")
    a = ap.parse_args(argv)
    pw = os.environ.get("MERIDIAN_APP_DB_PASSWORD")
    if not pw or len(pw) < 16:
        print("Set MERIDIAN_APP_DB_PASSWORD (16+ characters); passwords are never taken on the command line.")
        return 1
    d = MongoClient(a.admin_uri)[a.db]
    if d.command("rolesInfo", "meridianApp")["roles"]:
        d.command("updateRole", "meridianApp", privileges=privileges(a.db), roles=[])
    else:
        d.command("createRole", "meridianApp", privileges=privileges(a.db), roles=[])
    if d.command("usersInfo", a.user)["users"]:
        d.command("updateUser", a.user, pwd=pw, roles=[{"role": "meridianApp", "db": a.db}])
    else:
        d.command("createUser", a.user, pwd=pw, roles=[{"role": "meridianApp", "db": a.db}])
    print(f"role meridianApp on {a.db}: {len(collections())} business collections read/write, audit append-only; "
          f"user {a.user} updated")
    return 0


if __name__ == "__main__":
    sys.exit(main())
