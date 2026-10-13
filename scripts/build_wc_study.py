"""Refresh the cross-company working-capital dataset from SEC EDGAR (free, no key). ~20 frame requests plus one
submissions request per company, throttled to SEC fair-access limits.

    python scripts/build_wc_study.py
"""

from meridian.benchmark.sources import collect, save
from meridian.config import get_settings
from meridian.public_company.sec import DEFAULT_UA

if __name__ == "__main__":
    body = collect(get_settings().sec_user_agent or DEFAULT_UA)
    print("saved", save(body), len(body["companies"]), "companies")
