"""Build the cross-company working-capital dataset from SEC EDGAR XBRL *frames* (one request returns one concept for
every filer and period) plus each company's SEC submissions record (SIC industry code).

Every value kept carries its SEC accession number, period end and concept, so each benchmark figure traces back to a
filing. Rules that keep the comparison honest:
  * flows (revenue, cost of revenue) are each company's fiscal year that SEC assigns to calendar year 2025 (frame
    CY2025: the 350–380 day fiscal year that best overlaps 2025 — in practice years ending mid-2025 to early 2026);
  * balances (receivables, inventory, payables) must be dated EXACTLY at that same fiscal-year end — never mixed;
  * one concept per line item, chosen in a fixed priority order and recorded per company;
  * USD only, us-gaap only; nothing is estimated or filled in at this stage.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, date, datetime
from pathlib import Path

from meridian.public_company.sec import DEFAULT_UA

FRAMES_URL = "https://data.sec.gov/api/xbrl/frames/us-gaap/{concept}/USD/{period}.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
DATASET = Path(__file__).resolve().parents[1] / "data" / "benchmark" / "wc_dataset_FY2025.json"

FISCAL_YEAR = 2025
# line item -> concepts in priority order (the first one a company reports is used)
FLOW_CONCEPTS = {
    "revenue": ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax", "SalesRevenueNet",
                "RevenueFromContractWithCustomerIncludingAssessedTax"],
    "cogs": ["CostOfRevenue", "CostOfGoodsAndServicesSold", "CostOfGoodsSold"],
}
BALANCE_CONCEPTS = {
    "receivables": ["AccountsReceivableNetCurrent"],
    "inventory": ["InventoryNet"],
    "payables": ["AccountsPayableCurrent"],
}
MIN_REVENUE_USD = 1_000_000_000          # large enterprises only


def _get(url: str, ua: str) -> dict:
    import httpx

    from meridian.market import http

    try:
        return http.get(url, headers={"User-Agent": ua}, timeout=60).json()
    except httpx.HTTPStatusError as e:
        if e.response is not None and e.response.status_code == 404 and "/frames/" in url:
            return {"data": []}           # no filer used this (deprecated) concept for the period
        raise


def _days(start: str, end: str) -> int:
    return (date.fromisoformat(end) - date.fromisoformat(start)).days


def collect(ua: str = DEFAULT_UA, log=print) -> dict:
    flows: dict[str, dict[int, dict]] = {k: {} for k in FLOW_CONCEPTS}
    for item, concepts in FLOW_CONCEPTS.items():
        for c in concepts:
            d = _get(FRAMES_URL.format(concept=c, period=f"CY{FISCAL_YEAR}"), ua)
            n = 0
            for r in d["data"]:
                if r["cik"] in flows[item] or not r.get("start") or not 350 <= _days(r["start"], r["end"]) <= 380:
                    continue
                flows[item][r["cik"]] = {**r, "concept": c}
                n += 1
            log(f"{item:12s} {c:55s} +{n}")
    balances: dict[str, dict[tuple[int, str], dict]] = {k: {} for k in BALANCE_CONCEPTS}
    for item, concepts in BALANCE_CONCEPTS.items():
        for c in concepts:
            for q in (1, 2, 3, 4):
                d = _get(FRAMES_URL.format(concept=c, period=f"CY{FISCAL_YEAR}Q{q}I"), ua)
                for r in d["data"]:
                    balances[item].setdefault((r["cik"], r["end"]), {**r, "concept": c})
            log(f"{item:12s} {c:55s} instants loaded")

    rows = []
    for cik, rev in flows["revenue"].items():
        cogs = flows["cogs"].get(cik)
        if not cogs or rev["val"] < MIN_REVENUE_USD or cogs["end"] != rev["end"]:
            continue
        bal = {k: balances[k].get((cik, rev["end"])) for k in BALANCE_CONCEPTS}
        if not all(bal.values()):
            continue
        rows.append({"cik": cik, "name": rev["entityName"], "fiscal_year_end": rev["end"],
                     "revenue": rev, "cogs": cogs, **bal})
    log(f"companies with revenue ≥ $1B and all five items at the same fiscal-year end: {len(rows)}")

    for i, row in enumerate(rows):
        try:
            s = _get(SUBMISSIONS_URL.format(cik=row["cik"]), ua)
        except Exception as e:  # noqa: BLE001 — record and continue: a missing SIC excludes the company
            row["sic"] = None
            row["sic_error"] = type(e).__name__
            continue
        row["sic"] = int(s["sic"]) if str(s.get("sic") or "").isdigit() else None
        row["sic_description"] = s.get("sicDescription")
        row["tickers"] = s.get("tickers") or []
        row["exchanges"] = s.get("exchanges") or []
        if i % 50 == 0:
            log(f"  SIC {i + 1}/{len(rows)}")
    body = {"source": "SEC EDGAR XBRL frames API + submissions API (data.sec.gov)", "fiscal_year": FISCAL_YEAR,
            "retrieved_at": datetime.now(UTC).isoformat(timespec="seconds"), "user_agent": ua,
            "concept_priority": {**FLOW_CONCEPTS, **BALANCE_CONCEPTS}, "min_revenue_usd": MIN_REVENUE_USD,
            "companies": sorted(rows, key=lambda r: r["cik"])}
    body["checksum_sha256"] = hashlib.sha256(json.dumps(body["companies"], sort_keys=True).encode()).hexdigest()
    return body


def save(body: dict, path: Path = DATASET) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(body, separators=(",", ":")), encoding="utf-8")
    return path


def load(path: Path = DATASET) -> dict:
    body = json.loads(path.read_text(encoding="utf-8"))
    if hashlib.sha256(json.dumps(body["companies"], sort_keys=True).encode()).hexdigest() != body["checksum_sha256"]:
        raise ValueError(f"{path.name}: checksum mismatch — dataset was modified after retrieval")
    return body
