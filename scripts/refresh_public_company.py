"""Refresh the real public-company snapshot (Caterpillar Inc. + SEC peers) from SEC EDGAR.

    python scripts/refresh_public_company.py            # uses MERIDIAN_SEC_USER_AGENT if set
"""

import os

from meridian.public_company.sec import DEFAULT_UA, build_snapshot, save_snapshot

if __name__ == "__main__":
    snap = build_snapshot(os.getenv("MERIDIAN_SEC_USER_AGENT") or DEFAULT_UA)
    p = save_snapshot(snap)
    print(f"saved {p} · {snap['subject']['entity_name']} · {sum(len(v) for v in snap['facts'].values())} facts · "
          f"{len(snap['peers'])} peers · missing {snap['missing_concepts']} · issues {snap['peer_issues']}")
