"""Connectors to authoritative public market & reference data. Each returns (payload, SourceRecord).

| Source                              | Used for                                    | Licence / access                     |
|-------------------------------------|---------------------------------------------|--------------------------------------|
| ECB euro foreign exchange reference | FX translation to USD                       | Public, free (ECB reference rates)   |
| Federal Reserve Bank of New York    | SOFR → revolver all-in rate (SOFR + margin) | Public API, free                     |
| GDACS (UN / EC JRC)                 | Natural-hazard alerts → counterparty stress | Public RSS, free                     |
| SEC EDGAR XBRL company facts        | Peer working-capital benchmarks (10-K)      | Public API; User-Agent with contact  |

ECB rates are published for information purposes; production treasury accounting should use the
rate source mandated by the client's accounting policy (e.g. WM/Refinitiv 4pm) via the same interface.
"""

from __future__ import annotations

from datetime import date, datetime
from email.utils import parsedate_to_datetime
from xml.etree import ElementTree

import httpx

from meridian.domain.models import RiskEvent
from meridian.provenance import Classification, SourceRecord, ValidationStatus, checksum, now_utc

ECB_URL = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml"
SOFR_URL = "https://markets.newyorkfed.org/api/rates/secured/sofr/last/1.json"
GDACS_URL = "https://www.gdacs.org/xml/rss.xml"
SEC_FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"
AED_PER_USD_PEG = 3.6725   # Central Bank of the UAE fixed peg (not published by the ECB)
DEFAULT_UA = "Meridian-Treasury-Platform/3.0"


# ------------------------------------------------------------------------------------------------ ECB FX
def parse_ecb(xml_text: str, currencies: set[str]) -> tuple[dict, list[str]]:
    ns = {"e": "http://www.ecb.int/vocabulary/2002-08-01/eurofxref"}
    root = ElementTree.fromstring(xml_text)
    day = root.find(".//e:Cube[@time]", ns)
    per_eur = {c.get("currency"): float(c.get("rate")) for c in day.findall("e:Cube", ns)}
    usd_per_eur = per_eur["USD"]
    out, issues = {"USD": 1.0, "EUR": round(usd_per_eur, 6)}, []
    for ccy in sorted(currencies - {"USD", "EUR"}):
        if ccy in per_eur:
            out[ccy] = round(usd_per_eur / per_eur[ccy], 8)
        elif ccy == "AED":
            out["AED"] = round(1 / AED_PER_USD_PEG, 8)
            issues.append("AED not published by ECB; CBUAE USD peg 3.6725 applied")
        else:
            issues.append(f"{ccy} not published by ECB")
    return {"rate_date": day.get("time"), "usd_per_unit": out, "ecb_per_eur": per_eur}, issues


def fetch_ecb_fx(currencies: set[str], timeout: float = 15) -> tuple[dict, SourceRecord]:
    r = httpx.get(ECB_URL, timeout=timeout, headers={"User-Agent": DEFAULT_UA})
    r.raise_for_status()
    data, issues = parse_ecb(r.text, currencies)
    missing = [i for i in issues if "not published" in i and "peg" not in i]
    return data, SourceRecord(
        source_id="market.fx", name="FX reference rates (USD per unit)", system="European Central Bank",
        classification=Classification.PUBLIC_MARKET_DATA, uri=ECB_URL, retrieved_at=now_utc(),
        as_of=date.fromisoformat(data["rate_date"]), record_count=len(data["usd_per_unit"]),
        checksum_sha256=checksum(r.content), freshness_sla_hours=96,
        validation=ValidationStatus.FAILED if missing else (
            ValidationStatus.PASSED_WITH_WARNINGS if issues else ValidationStatus.PASSED), issues=issues)


# ------------------------------------------------------------------------------------------------ SOFR
def parse_sofr(payload: dict) -> dict:
    ref = payload["refRates"][0]
    return {"effective_date": ref["effectiveDate"], "sofr_pct": float(ref["percentRate"]),
            "volume_bn": ref.get("volumeInBillions")}


def fetch_sofr(timeout: float = 15) -> tuple[dict, SourceRecord]:
    r = httpx.get(SOFR_URL, timeout=timeout, headers={"User-Agent": DEFAULT_UA})
    r.raise_for_status()
    data = parse_sofr(r.json())
    ok = 0 < data["sofr_pct"] < 20
    return data, SourceRecord(
        source_id="market.sofr", name="Secured Overnight Financing Rate (SOFR)",
        system="Federal Reserve Bank of New York", classification=Classification.PUBLIC_MARKET_DATA, uri=SOFR_URL,
        retrieved_at=now_utc(), as_of=date.fromisoformat(data["effective_date"]), record_count=1,
        checksum_sha256=checksum(r.content), freshness_sla_hours=96,
        validation=ValidationStatus.PASSED if ok else ValidationStatus.FAILED,
        issues=[] if ok else ["SOFR outside plausible range"],
        notes="Overnight SOFR used as base-rate proxy; Term SOFR (CME) requires a licence")


# ------------------------------------------------------------------------------------------------ GDACS
_GDACS_NS = {"gdacs": "http://www.gdacs.org"}
_HAZARD = {"TC": "TROPICAL_CYCLONE", "FL": "FLOOD", "EQ": "EARTHQUAKE", "VO": "VOLCANO", "DR": "DROUGHT",
           "WF": "WILDFIRE"}
ISO3_TO_2 = {"USA": "US", "CAN": "CA", "MEX": "MX", "BRA": "BR", "GBR": "GB", "DEU": "DE", "FRA": "FR", "NLD": "NL",
             "POL": "PL", "IND": "IN", "CHN": "CN", "SGP": "SG", "JPN": "JP", "AUS": "AU", "ARE": "AE", "ZAF": "ZA"}


def _rfc822(s: str | None) -> date | None:
    try:
        return parsedate_to_datetime(s).date() if s else None
    except (TypeError, ValueError):
        return None


def parse_gdacs(xml_text: str, countries: set[str]) -> list[RiskEvent]:
    root = ElementTree.fromstring(xml_text)
    seen: dict[str, RiskEvent] = {}
    for item in root.iter("item"):
        if (item.findtext("gdacs:iscurrent", default="true", namespaces=_GDACS_NS) or "").lower() != "true":
            continue
        ctry = ISO3_TO_2.get((item.findtext("gdacs:iso3", default="", namespaces=_GDACS_NS) or "").upper())
        if not ctry or ctry not in countries:
            continue
        etype = item.findtext("gdacs:eventtype", default="", namespaces=_GDACS_NS)
        eid = f"GDACS-{etype}-{item.findtext('gdacs:eventid', default='0', namespaces=_GDACS_NS)}"
        level = (item.findtext("gdacs:alertlevel", default="Green", namespaces=_GDACS_NS) or "Green").upper()
        start = _rfc822(item.findtext("gdacs:fromdate", namespaces=_GDACS_NS)) or _rfc822(item.findtext("pubDate"))
        end = _rfc822(item.findtext("gdacs:todate", namespaces=_GDACS_NS))
        seen[eid] = RiskEvent(
            event_id=eid, hazard=_HAZARD.get(etype, etype or "OTHER"), country=ctry,
            severity=level if level in {"GREEN", "ORANGE", "RED"} else "GREEN", start=start or date.today(),
            expected_days=max(1, (end - start).days + 1) if start and end else 3,
            description=(item.findtext("title") or "")[:220], source="GDACS (live public feed)",
            classification="PUBLIC_MARKET_DATA", url=item.findtext("link"))
    return sorted(seen.values(), key=lambda e: ({"RED": 0, "ORANGE": 1, "GREEN": 2}[e.severity], e.country))


def fetch_gdacs(countries: set[str], timeout: float = 20) -> tuple[list[RiskEvent], SourceRecord]:
    r = httpx.get(GDACS_URL, timeout=timeout, headers={"User-Agent": DEFAULT_UA})
    r.raise_for_status()
    events = parse_gdacs(r.text, countries)
    return events, SourceRecord(
        source_id="market.hazards", name="Natural-hazard alerts in operating countries",
        system="GDACS — Global Disaster Alert and Coordination System", classification=Classification.PUBLIC_MARKET_DATA,
        uri=GDACS_URL, retrieved_at=now_utc(), as_of=now_utc().date(), record_count=len(events),
        checksum_sha256=checksum(r.content), freshness_sla_hours=24, validation=ValidationStatus.PASSED)


# ------------------------------------------------------------------------------------------------ SEC EDGAR peers
PEERS = [(18230, "Caterpillar"), (773840, "Honeywell"), (66740, "3M"), (32604, "Emerson Electric"),
         (76334, "Parker-Hannifin"), (49826, "Illinois Tool Works"), (26172, "Cummins"), (29905, "Dover")]
_CONCEPTS = {
    "receivables": ["AccountsReceivableNetCurrent", "ReceivablesNetCurrent", "AccountsNotesAndLoansReceivableNetCurrent"],
    "revenue": ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax", "SalesRevenueNet"],
    "payables": ["AccountsPayableCurrent"],
    "cogs": ["CostOfRevenue", "CostOfGoodsAndServicesSold", "CostOfGoodsSold",
             "CostOfGoodsAndServiceExcludingDepreciationDepletionAndAmortization"],
    "inventory": ["InventoryNet"],
    "cash": ["CashAndCashEquivalentsAtCarryingValue"],
}


def _facts(facts: dict, names: list[str], duration: bool) -> list[dict]:
    gaap = facts.get("facts", {}).get("us-gaap", {})
    out = []
    for n in names:
        for u in gaap.get(n, {}).get("units", {}).get("USD", []):
            if u.get("form") != "10-K" or u.get("fp") != "FY":
                continue
            if duration and not ("start" in u and 330 <= (date.fromisoformat(u["end"]) -
                                                          date.fromisoformat(u["start"])).days <= 380):
                continue
            out.append({**u, "concept": n})
    return out


def _metrics_at(facts: dict, fye: str) -> dict | None:
    def at(kind: str, duration: bool = False) -> dict | None:
        c = [x for x in _facts(facts, _CONCEPTS[kind], duration) if x["end"] == fye]
        return max(c, key=lambda x: x["filed"]) if c else None

    v = {"revenue": at("revenue", True), "cogs": at("cogs", True), "receivables": at("receivables"),
         "payables": at("payables"), "inventory": at("inventory"), "cash": at("cash")}
    if not (v["receivables"] and v["revenue"]):
        return None
    rev, cogs = v["revenue"]["val"], (v["cogs"] or {}).get("val")
    dso = v["receivables"]["val"] / rev * 365
    dpo = v["payables"]["val"] / cogs * 365 if v["payables"] and cogs else None
    dio = v["inventory"]["val"] / cogs * 365 if v["inventory"] and cogs else None
    return {"v": v, "dso": dso, "dpo": dpo, "dio": dio}


def peer_metrics(facts: dict, years: int = 4) -> dict | None:
    """Latest fiscal year where revenue is reported; every balance is taken at that same fiscal year end.
    Also returns the last `years` fiscal years (trend) and fact-level lineage for every input."""
    revs = _facts(facts, _CONCEPTS["revenue"], duration=True)
    if not revs:
        return None
    fyes = sorted({r["end"] for r in revs}, reverse=True)
    fye = fyes[0]
    m = _metrics_at(facts, fye)
    if m is None:
        return None
    v, dso, dpo, dio = m["v"], m["dso"], m["dpo"], m["dio"]
    history = []
    for e in fyes[:years]:
        h = _metrics_at(facts, e)
        if h:
            history.append({"fiscal_year_end": e, "dso_days": round(h["dso"], 1),
                            "dpo_days": round(h["dpo"], 1) if h["dpo"] else None,
                            "dio_days": round(h["dio"], 1) if h["dio"] else None,
                            "revenue_usd": h["v"]["revenue"]["val"]})
    lineage = {k: ({"concept": x["concept"], "value_usd": x["val"], "period_start": x.get("start"),
                    "period_end": x["end"], "form": x.get("form"), "fiscal_year": x.get("fy"),
                    "accession": x.get("accn"), "filed": x.get("filed")} if x else None) for k, x in v.items()}
    return {"company": facts.get("entityName"), "cik": facts.get("cik"), "fiscal_year_end": fye,
            "filed": v["revenue"]["filed"], "accession": v["revenue"]["accn"], "revenue_usd": v["revenue"]["val"],
            "dso_days": round(dso, 1), "dpo_days": round(dpo, 1) if dpo else None,
            "dio_days": round(dio, 1) if dio else None,
            "ccc_days": round(dso + (dio or 0) - (dpo or 0), 1) if dpo else None,
            "concepts": {k: (x or {}).get("concept") for k, x in v.items()},
            "lineage": lineage,
            "formulas": {"dso_days": "receivables / revenue × 365", "dpo_days": "payables / cost of revenue × 365",
                         "dio_days": "inventory / cost of revenue × 365", "ccc_days": "DSO + DIO − DPO"},
            "history": sorted(history, key=lambda h: h["fiscal_year_end"])}


def fetch_sec_peers(user_agent: str, peers: list[tuple[int, str]] = PEERS, timeout: float = 30) -> tuple[list, SourceRecord]:
    out, issues = [], []
    with httpx.Client(timeout=timeout, headers={"User-Agent": user_agent}) as c:
        for cik, label in peers:
            r = c.get(SEC_FACTS_URL.format(cik=cik))
            if r.status_code != 200:
                issues.append(f"{label}: HTTP {r.status_code}")
                continue
            m = peer_metrics(r.json())
            if m:
                out.append({**m, "source_url": SEC_FACTS_URL.format(cik=cik)})
            else:
                issues.append(f"{label}: required XBRL concepts missing")
    latest = max((date.fromisoformat(p["filed"]) for p in out), default=None)
    return out, SourceRecord(
        source_id="market.peer_benchmarks", name="Peer working-capital benchmarks (latest 10-K)",
        system="SEC EDGAR XBRL company facts", classification=Classification.PUBLIC_MARKET_DATA,
        uri="https://data.sec.gov/api/xbrl/companyfacts/", retrieved_at=now_utc(), as_of=latest,
        record_count=len(out), checksum_sha256=checksum(out), freshness_sla_hours=24 * 400,
        validation=ValidationStatus.PASSED_WITH_WARNINGS if issues else ValidationStatus.PASSED, issues=issues,
        notes="Receivables per XBRL AccountsReceivableNetCurrent; some issuers include financing receivables")


def iso(dt: datetime | date | None) -> str | None:
    return dt.isoformat() if dt else None
