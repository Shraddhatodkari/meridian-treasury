"""SEC EDGAR XBRL company-facts extraction with fact-level lineage.

Source: https://data.sec.gov/api/xbrl/companyfacts/CIK##########.json (SEC fair-access policy: declared
User-Agent, <10 requests/s). Every value kept here is an as-filed XBRL fact from a 10-K or 10-Q with its
concept, period, form, fiscal period, accession number and filing date.

The companyfacts API publishes us-gaap / dei facts WITHOUT dimensions and WITHOUT company-specific (extension)
elements. Anything an issuer tags only with an extension element or a dimension (segment tables, some debt
lines, interest expense for many issuers) is therefore reported as NOT AVAILABLE in this source, never guessed.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import httpx

from meridian.config import PACKAGE_ROOT

SEC_FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"
DEFAULT_UA = "Meridian-Treasury-Platform/3.0 (ops@meridian-treasury.invalid)"
SNAPSHOT = PACKAGE_ROOT / "data" / "public_company" / "caterpillar_snapshot.json"

SUBJECT: dict[str, Any] = {"cik": 18230, "ticker": "CAT", "name": "Caterpillar Inc.", "sector": "Construction & mining equipment, "
           "energy & transportation; captive finance (Cat Financial)", "captive_finance": True}
PEERS = [(315189, "Deere & Company"), (75362, "PACCAR Inc"), (26172, "Cummins Inc."), (773840, "Honeywell"),
         (66740, "3M"), (32604, "Emerson Electric"), (76334, "Parker-Hannifin"), (49826, "Illinois Tool Works"),
         (29905, "Dover")]

# key -> (candidate us-gaap concepts in priority order, "I"nstant | "D"uration, label)
# Concepts that are broader than the metric they stand in for — the difference is disclosed on the metric, never hidden.
CONCEPT_NOTES = {
    "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents": "Issuer does not tag cash excl. restricted cash: "
    "this figure INCLUDES restricted cash (cash-flow statement total)",
    "AccountsPayableAndAccruedLiabilitiesCurrent": "Issuer tags payables together with accrued liabilities: DPO is overstated",
    "AccountsReceivableNet": "Receivables tagged without a current/non-current split",
    "LongTermDebtAndCapitalLeaseObligations": "Includes finance-lease obligations",
    "LongTermDebtAndCapitalLeaseObligationsCurrent": "Includes finance-lease obligations",
}

CONCEPTS: dict[str, tuple[list[str], str, str]] = {
    "cash": (["CashAndCashEquivalentsAtCarryingValue", "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents"], "I", "Cash and cash equivalents"),
    "receivables_trade": (["AccountsReceivableNetCurrent", "ReceivablesNetCurrent", "AccountsNotesAndLoansReceivableNetCurrent",
                           "AccountsReceivableNet"], "I", "Receivables – trade and other (current)"),
    "receivables_finance": (["NotesAndLoansReceivableNetCurrent"], "I", "Receivables – finance (current)"),
    "inventory": (["InventoryNet"], "I", "Inventories"),
    "payables": (["AccountsPayableCurrent", "AccountsPayableTradeCurrent", "AccountsPayableAndAccruedLiabilitiesCurrent"], "I", "Accounts payable"),
    "assets_current": (["AssetsCurrent"], "I", "Total current assets"),
    "liabilities_current": (["LiabilitiesCurrent"], "I", "Total current liabilities"),
    "assets": (["Assets"], "I", "Total assets"),
    "liabilities": (["Liabilities"], "I", "Total liabilities"),
    "short_term_borrowings": (["ShortTermBorrowings", "CommercialPaper", "OtherShortTermBorrowings"], "I", "Short-term borrowings"),
    "debt_current": (["DebtCurrent"], "I", "Debt due within one year (short-term + current LTD)"),
    "ltd_current": (["LongTermDebtCurrent", "LongTermDebtAndCapitalLeaseObligationsCurrent"], "I", "Long-term debt due within one year"),
    "ltd_noncurrent": (["LongTermDebtNoncurrent", "LongTermDebtAndCapitalLeaseObligations"], "I", "Long-term debt due after one year"),
    "maturity_y1": (["LongTermDebtMaturitiesRepaymentsOfPrincipalInNextTwelveMonths"], "I", "LTD maturities – next 12 months"),
    "maturity_y2": (["LongTermDebtMaturitiesRepaymentsOfPrincipalInYearTwo"], "I", "LTD maturities – year 2"),
    "maturity_y3": (["LongTermDebtMaturitiesRepaymentsOfPrincipalInYearThree"], "I", "LTD maturities – year 3"),
    "maturity_y4": (["LongTermDebtMaturitiesRepaymentsOfPrincipalInYearFour"], "I", "LTD maturities – year 4"),
    "maturity_y5": (["LongTermDebtMaturitiesRepaymentsOfPrincipalInYearFive"], "I", "LTD maturities – year 5"),
    "maturity_after5": (["LongTermDebtMaturitiesRepaymentsOfPrincipalAfterYearFive"], "I", "LTD maturities – after year 5"),
    "credit_capacity": (["LineOfCreditFacilityMaximumBorrowingCapacity"], "I", "Credit facilities – maximum capacity"),
    "credit_available": (["LineOfCreditFacilityRemainingBorrowingCapacity"], "I", "Credit facilities – remaining capacity"),
    "derivative_notional": (["DerivativeNotionalAmount"], "I", "Derivatives – total notional"),
    "rpo": (["RevenueRemainingPerformanceObligation"], "I", "Remaining performance obligations"),
    "revenue": (["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax", "SalesRevenueNet"], "D", "Total sales and revenues"),
    "cogs": (["CostOfRevenue", "CostOfGoodsAndServicesSold", "CostOfGoodsSold"], "D", "Cost of goods sold"),
    "operating_income": (["OperatingIncomeLoss"], "D", "Operating profit"),
    "dna": (["DepreciationDepletionAndAmortization", "DepreciationAmortizationAndAccretionNet", "DepreciationAndAmortization"], "D", "Depreciation and amortization"),
    "net_income": (["NetIncomeLoss", "ProfitLoss"], "D", "Profit (consolidated)"),
    "ocf": (["NetCashProvidedByUsedInOperatingActivities"], "D", "Net cash provided by operating activities"),
    "capex": (["PaymentsToAcquirePropertyPlantAndEquipment", "PaymentsToAcquireProductiveAssets"], "D", "Capital expenditures"),
    "dividends": (["PaymentsOfDividendsCommonStock", "PaymentsOfDividends"], "D", "Dividends paid"),
    "buybacks": (["PaymentsForRepurchaseOfCommonStock"], "D", "Common shares repurchased"),
    "interest_paid": (["InterestPaidNet", "InterestPaid"], "D", "Cash interest paid"),
    "fx_cash_effect": (["EffectOfExchangeRateOnCashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents"], "D",
                       "Effect of exchange rates on cash"),
}


def _days(f: dict) -> int | None:
    return (date.fromisoformat(f["end"]) - date.fromisoformat(f["start"])).days if f.get("start") else None


def extract(companyfacts: dict, since: str = "2021-01-01") -> dict[str, list[dict]]:
    """Keep only 10-K/10-Q USD facts for the concepts above; one fact per (start, end) — the latest filing wins
    (restatements), and the original form/accession/filing date stay attached."""
    gaap = companyfacts.get("facts", {}).get("us-gaap", {})
    out: dict[str, list[dict]] = {}
    for key, (names, kind, _label) in CONCEPTS.items():
        cands = []
        for i, n in enumerate(names):
            facts = [u for u in gaap.get(n, {}).get("units", {}).get("USD", [])
                     if u.get("form") in ("10-K", "10-Q") and u["end"] >= since and (("start" in u) == (kind == "D"))
                     and not (kind == "D" and (nd := _days(u)) is not None and nd < 80)]
            if facts:
                cands.append((max(u["end"] for u in facts), -i, n, facts))
        if not cands:
            continue
        _, _, n, facts = max(cands)     # issuers switch concepts over time: use the one carrying the latest period
        best: dict[tuple, dict] = {}
        for u in facts:
            k = (u.get("start"), u["end"])
            if k not in best or u["filed"] > best[k]["filed"]:
                best[k] = u
        out[key] = [{"concept": n, "start": u.get("start"), "end": u["end"], "val": u["val"], "form": u["form"],
                     "fy": u.get("fy"), "fp": u.get("fp"), "accn": u["accn"], "filed": u["filed"]}
                    for u in sorted(best.values(), key=lambda x: (x["end"], x.get("start") or ""))]
    return out


def checksum(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()


def fetch_companyfacts(cik: int, user_agent: str = DEFAULT_UA, timeout: float = 40) -> dict:
    from meridian.market import http

    return http.get(SEC_FACTS_URL.format(cik=cik), headers={"User-Agent": user_agent}, timeout=timeout).json()


TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SNAP_DIR = PACKAGE_ROOT / "data" / "public_company"


def resolve(query: str, user_agent: str = DEFAULT_UA, timeout: float = 30) -> dict:
    """Ticker or CIK → {cik, ticker, name} using SEC's official ticker file. A numeric query is taken as a CIK."""
    q = query.strip().upper()
    from meridian.market import http

    r = http.get(TICKERS_URL, headers={"User-Agent": user_agent}, timeout=timeout, cache_ttl_s=6 * 3600)
    rows = list(r.json().values())
    if q.isdigit():
        hit = next((x for x in rows if int(x["cik_str"]) == int(q)), None)
        return {"cik": int(q), "ticker": hit["ticker"] if hit else None, "name": hit["title"] if hit else None}
    hit = next((x for x in rows if x["ticker"].upper() == q), None)
    if hit is None:
        raise LookupError(f"No SEC registrant with ticker '{query}'")
    return {"cik": int(hit["cik_str"]), "ticker": hit["ticker"], "name": hit["title"]}


def build_snapshot(user_agent: str = DEFAULT_UA, subject: dict | None = None,
                   peers: list[tuple[int, str]] | None = None, peer_snapshot: dict | None = None) -> dict:
    """Fetch the subject (and peers) live from SEC EDGAR and build a dated, checksummed snapshot.
    If `peer_snapshot` is given and no explicit peers, its already-fetched peer facts are reused (no extra calls)."""
    from meridian.market.connectors import peer_metrics

    subject = subject or SUBJECT
    raw = fetch_companyfacts(subject["cik"], user_agent)
    facts = extract(raw)
    if not facts.get("cash") or not facts.get("revenue"):
        raise ValueError(f"{raw.get('entityName') or subject['cik']}: no us-gaap cash/revenue facts in 10-K/10-Q filings "
                         "(foreign filers on IFRS/20-F and funds are not supported)")
    out_peers, issues = [], []
    if peers is None and peer_snapshot is not None:
        out_peers = [p for p in peer_snapshot.get("peers", []) if int(p["cik"]) != int(subject["cik"])]
        issues = list(peer_snapshot.get("peer_issues", []))
    else:
        for cik, label in (peers if peers is not None else PEERS):
            if int(cik) == int(subject["cik"]):
                continue
            try:
                m = peer_metrics(fetch_companyfacts(cik, user_agent))
            except httpx.HTTPError as e:
                issues.append(f"{label}: {type(e).__name__}")
                continue
            if m:
                out_peers.append({**m, "label": label, "source_url": SEC_FACTS_URL.format(cik=cik)})
            else:
                issues.append(f"{label}: required XBRL concepts missing")
    subject_fy = peer_metrics(raw)
    snap = {"subject": {**subject, "name": subject.get("name") or raw.get("entityName"), "entity_name": raw.get("entityName"),
                        "source_url": SEC_FACTS_URL.format(cik=subject["cik"])},
            "retrieved_at": datetime.now(UTC).isoformat(timespec="seconds"), "facts": facts,
            "subject_fy_working_capital": subject_fy, "peers": out_peers, "peer_issues": issues,
            "peer_set": "explicit" if peers is not None else "Meridian default industrial peer set",
            "missing_concepts": sorted(k for k in CONCEPTS if k not in facts)}
    snap["checksum_sha256"] = checksum({k: snap[k] for k in ("facts", "peers")})
    return snap


def snapshot_path(cik: int) -> Path:
    return SNAPSHOT if int(cik) == SUBJECT["cik"] else SNAP_DIR / f"CIK{int(cik):010d}.json"


def save_snapshot(snap: dict, path: Path = SNAPSHOT) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(snap, indent=1, ensure_ascii=False), encoding="utf-8")
    return path


def load_snapshot(path: Path = SNAPSHOT) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))
