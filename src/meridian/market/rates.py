"""Interest-rate, credit-spread and FX-history connectors (authoritative public sources).

| source_id              | Publisher                                   | Used for                                             |
|------------------------|---------------------------------------------|------------------------------------------------------|
| market.ust_curve       | U.S. Department of the Treasury             | Par yield curve: refinancing cost, rate outlook      |
| market.sofr_history    | Federal Reserve Bank of New York            | Floating-debt cost trend, rate-sensitivity base      |
| market.effr            | Federal Reserve Bank of New York            | Policy-rate corridor context (FOMC target range)     |
| market.estr            | European Central Bank                       | EUR overnight rate: euro cash yield benchmark        |
| market.credit_spread   | ICE BofA BBB US Corporate OAS, via FRED     | Indicative new-issue spread for refinancing          |
| market.fx_history      | European Central Bank (90-day reference)    | FX volatility → currency-exposure value-at-risk      |

All parsers are pure functions over the raw payload so they are unit-tested without the network.
"""

from __future__ import annotations

import csv
import io
import math
from datetime import date, datetime
from typing import Any, cast

import httpx
from defusedxml.ElementTree import fromstring as _safe_fromstring  # rejects entity expansion / XXE / DTDs

from meridian.provenance import Classification, SourceRecord, ValidationStatus, checksum, now_utc

UST_URL = ("https://home.treasury.gov/resource-center/data-chart-center/interest-rates/daily-treasury-rates.csv/"
           "{year}/all?type=daily_treasury_yield_curve&field_tdr_date_value={year}&page&_format=csv")
SOFR_HIST_URL = "https://markets.newyorkfed.org/api/rates/secured/sofr/last/{n}.json"
EFFR_URL = "https://markets.newyorkfed.org/api/rates/unsecured/effr/last/1.json"
ESTR_URL = "https://data-api.ecb.europa.eu/service/data/EST/B.EU000A2X2A25.WT?lastNObservations=60&format=csvdata"
FRED_CSV = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={series}"
ECB_HIST_URL = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-hist-90d.xml"
UA = "Meridian-Treasury-Platform/3.0"
TENORS = {"1 Mo": 1 / 12, "3 Mo": 0.25, "6 Mo": 0.5, "1 Yr": 1, "2 Yr": 2, "3 Yr": 3, "5 Yr": 5, "7 Yr": 7,
          "10 Yr": 10, "20 Yr": 20, "30 Yr": 30}


def _get(url: str, timeout: float = 25) -> httpx.Response:
    from meridian.market import http

    return http.get(url, timeout=timeout, headers={"User-Agent": UA})


def _src(source_id: str, name: str, system: str, uri: str, as_of: date | None, n: int, raw: bytes,
         sla_h: float, issues: list[str] | None = None, notes: str | None = None) -> SourceRecord:
    return SourceRecord(source_id=source_id, name=name, system=system, classification=Classification.PUBLIC_MARKET_DATA,
                        uri=uri, retrieved_at=now_utc(), as_of=as_of, record_count=n, checksum_sha256=checksum(raw),
                        freshness_sla_hours=sla_h, issues=issues or [], notes=notes,
                        validation=ValidationStatus.PASSED_WITH_WARNINGS if issues else ValidationStatus.PASSED)


# ------------------------------------------------------------------------------------------ U.S. Treasury curve
def parse_ust_curve(csv_text: str, history_tenors: tuple[str, ...] = ("3 Mo", "2 Yr", "5 Yr", "10 Yr")) -> dict:
    rows = list(csv.DictReader(io.StringIO(csv_text)))
    pts = []
    for r in rows:
        d = datetime.strptime(r["Date"], "%m/%d/%Y").date()
        pts.append((d, {k: float(r[k]) for k in TENORS if r.get(k) not in (None, "", "N/A")}))
    pts.sort(key=lambda x: x[0])
    latest_d, latest = pts[-1]
    return {"curve_date": str(latest_d),
            "curve": [{"tenor": k, "years": TENORS[k], "yield_pct": latest[k]} for k in TENORS if k in latest],
            "history": [{"date": str(d), **{t: v.get(t) for t in history_tenors}} for d, v in pts]}


def fetch_ust_curve(year: int | None = None) -> tuple[dict, SourceRecord]:
    year = year or now_utc().year
    url = UST_URL.format(year=year)
    r = _get(url)
    data = parse_ust_curve(r.text)
    return data, _src("market.ust_curve", "U.S. Treasury par yield curve (daily)",
                      "U.S. Department of the Treasury", url, date.fromisoformat(data["curve_date"]),
                      len(data["history"]), r.content, 96)


# ------------------------------------------------------------------------------------------ NY Fed SOFR / EFFR
def parse_nyfed_history(payload: dict) -> list[dict]:
    rows = [{"date": x["effectiveDate"], "rate_pct": float(x["percentRate"]),
             "p1": x.get("percentPercentile1"), "p99": x.get("percentPercentile99"),
             "volume_bn": x.get("volumeInBillions")} for x in payload["refRates"]]
    return sorted(rows, key=lambda x: x["date"])


def fetch_sofr_history(n: int = 260) -> tuple[list[dict], SourceRecord]:
    url = SOFR_HIST_URL.format(n=n)
    r = _get(url)
    rows = parse_nyfed_history(r.json())
    return rows, _src("market.sofr_history", "SOFR daily history (≈1 year)", "Federal Reserve Bank of New York", url,
                      date.fromisoformat(rows[-1]["date"]), len(rows), r.content, 96)


def parse_effr(payload: dict) -> dict:
    x = payload["refRates"][0]
    return {"effective_date": x["effectiveDate"], "effr_pct": float(x["percentRate"]),
            "target_from_pct": x.get("targetRateFrom"), "target_to_pct": x.get("targetRateTo")}


def fetch_effr() -> tuple[dict, SourceRecord]:
    r = _get(EFFR_URL)
    d = parse_effr(r.json())
    return d, _src("market.effr", "Effective Federal Funds Rate and FOMC target range",
                   "Federal Reserve Bank of New York", EFFR_URL, date.fromisoformat(d["effective_date"]), 1, r.content, 96)


# ------------------------------------------------------------------------------------------ ECB €STR
def parse_estr(csv_text: str) -> list[dict]:
    rows = [{"date": r["TIME_PERIOD"], "rate_pct": float(r["OBS_VALUE"])}
            for r in csv.DictReader(io.StringIO(csv_text)) if r.get("OBS_VALUE")]
    return sorted(rows, key=lambda x: x["date"])


def fetch_estr() -> tuple[list[dict], SourceRecord]:
    r = _get(ESTR_URL)
    rows = parse_estr(r.text)
    return rows, _src("market.estr", "Euro short-term rate (€STR)", "European Central Bank", ESTR_URL,
                      date.fromisoformat(rows[-1]["date"]), len(rows), r.content, 120)


# ------------------------------------------------------------------------------------------ FRED credit spread
def parse_fred_csv(csv_text: str) -> list[dict]:
    rdr = csv.reader(io.StringIO(csv_text))
    next(rdr)
    out = []
    for d, v in rdr:
        if v not in ("", "."):
            out.append({"date": d, "value": float(v)})
    return out


def fetch_credit_spread(series: str = "BAMLC0A4CBBB", keep: int = 520) -> tuple[dict, SourceRecord]:
    url = FRED_CSV.format(series=series)
    r = _get(url)
    rows = parse_fred_csv(r.text)[-keep:]
    names = {"BAMLC0A4CBBB": "ICE BofA BBB US Corporate Index Option-Adjusted Spread",
             "BAMLC0A3CA": "ICE BofA Single-A US Corporate Index Option-Adjusted Spread"}
    data = {"series": series, "name": names.get(series, f"ICE BofA index {series} option-adjusted spread"), "unit": "percent",
            "latest": rows[-1], "history": rows}
    return data, _src("market.credit_spread", "BBB US corporate option-adjusted spread", "FRED (St. Louis Fed) — "
                      "ICE BofA index data", url, date.fromisoformat(rows[-1]["date"]), len(rows), r.content, 120,
                      notes="ICE Data Indices via FRED; indicative only — used to ESTIMATE new-issue refinancing cost")


# ------------------------------------------------------------------------------------------ ECB FX history → vol
def parse_ecb_history(xml_text: str, currencies: set[str]) -> dict:
    ns = {"e": "http://www.ecb.int/vocabulary/2002-08-01/eurofxref"}
    root = _safe_fromstring(xml_text)
    days: list[dict[str, Any]] = []
    for day in root.findall(".//e:Cube[@time]", ns):
        per_eur = {c.get("currency"): float(cast(str, c.get("rate"))) for c in day.findall("e:Cube", ns)}
        if "USD" not in per_eur:
            continue
        usd = per_eur["USD"]
        row: dict[str, Any] = {"date": day.get("time"), "EUR": usd}
        for ccy in currencies - {"USD", "EUR"}:
            if ccy in per_eur:
                row[ccy] = usd / per_eur[ccy]
        days.append(row)
    days.sort(key=lambda r: r["date"])
    vols = {}
    for ccy in sorted({k for r in days for k in r} - {"date"}):
        series = [r[ccy] for r in days if ccy in r]
        rets = [math.log(b / a) for a, b in zip(series, series[1:], strict=False) if a and b]
        if len(rets) > 10:
            mu = sum(rets) / len(rets)
            sd = math.sqrt(sum((x - mu) ** 2 for x in rets) / (len(rets) - 1))
            vols[ccy] = {"daily_vol": round(sd, 6), "annualised_vol": round(sd * math.sqrt(252), 4),
                         "observations": len(rets), "change_90d_pct": round((series[-1] / series[0] - 1) * 100, 2)}
    return {"from": days[0]["date"], "to": days[-1]["date"], "usd_per_unit": days, "volatility": vols}


def fetch_fx_history(currencies: set[str]) -> tuple[dict, SourceRecord]:
    r = _get(ECB_HIST_URL)
    d = parse_ecb_history(r.text, currencies)
    missing = sorted(currencies - set(d["volatility"]) - {"USD"})
    return d, _src("market.fx_history", "FX reference rates — last 90 days (USD per unit)", "European Central Bank",
                   ECB_HIST_URL, date.fromisoformat(d["to"]), len(d["usd_per_unit"]), r.content, 120,
                   issues=[f"No ECB history for {m} (pegged or not published); volatility treated as 0" for m in missing])
