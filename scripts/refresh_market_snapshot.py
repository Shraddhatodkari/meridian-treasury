"""Fetch REAL public market/reference data and write a dated, checksummed snapshot used by demo & CI.

    python scripts/refresh_market_snapshot.py --sec-user-agent "Your Org ops@your-org.com"
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from meridian.config import PACKAGE_ROOT  # noqa: E402
from meridian.market import connectors as c  # noqa: E402
from meridian.market import rates as rt  # noqa: E402

CCY = {"USD", "EUR", "GBP", "CAD", "MXN", "BRL", "INR", "CNY", "SGD", "JPY", "AUD", "AED", "ZAR", "PLN"}
COUNTRIES = set(c.ISO3_TO_2.values())

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--sec-user-agent", required=True, help='SEC policy: "Org Name contact@org.com"')
    args = ap.parse_args()
    out = PACKAGE_ROOT / "data" / "market_snapshots"
    out.mkdir(parents=True, exist_ok=True)
    fx, fx_src = c.fetch_ecb_fx(CCY)
    sofr, sofr_src = c.fetch_sofr()
    haz, haz_src = c.fetch_gdacs(COUNTRIES)
    peers, peer_src = c.fetch_sec_peers(args.sec_user_agent)
    extended = {"ust_curve": rt.fetch_ust_curve(), "sofr_history": rt.fetch_sofr_history(), "effr": rt.fetch_effr(),
                "estr": rt.fetch_estr(), "credit_spread": rt.fetch_credit_spread(), "fx_history": rt.fetch_fx_history(CCY)}
    snap = {
        "fx": {"data": fx, "source": fx_src.model_dump(mode="json")},
        "sofr": {"data": sofr, "source": sofr_src.model_dump(mode="json")},
        "hazards": {"data": [e.model_dump(mode="json") for e in haz], "source": haz_src.model_dump(mode="json")},
        "peers": {"data": peers, "source": peer_src.model_dump(mode="json")},
        **{k: {"data": d, "source": src.model_dump(mode="json")} for k, (d, src) in extended.items()},
    }
    (out / "market_snapshot.json").write_text(json.dumps(snap, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"FX {fx['rate_date']} EURUSD={fx['usd_per_unit']['EUR']} | SOFR {sofr['effective_date']} {sofr['sofr_pct']}% "
          f"| hazards {len(haz)} | peers {len(peers)} {peer_src.issues} | "
          + " | ".join(f"{k} {src.as_of}" for k, (_, src) in extended.items()))
