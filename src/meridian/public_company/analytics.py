"""Public-company treasury & working-capital analytics — computed ONLY from as-filed SEC XBRL facts and public
market data, with SOURCE → FILING → PERIOD → TRANSFORMATION → CALCULATION → RESULT lineage on every metric.

Basis labels:
  REPORTED    the as-filed XBRL value (10-K / 10-Q), unchanged
  CALCULATED  arithmetic on REPORTED facts (TTM sums, ratios, quarter = YTD differences)
  ESTIMATED   CALCULATED + a stated market/policy assumption (e.g. new-issue cost = UST 5Y + A OAS)
  SCENARIO    a what-if overlay on the above
Anything the public record does not support is returned as NOT_AVAILABLE with the reason — never imputed.
"""

from __future__ import annotations

import functools
import json
import statistics
from datetime import date, timedelta
from functools import cached_property
from pathlib import Path
from typing import Any

from meridian.config import PACKAGE_ROOT
from meridian.public_company.sec import CONCEPT_NOTES, SEC_FACTS_URL, load_snapshot

CLASS = "PUBLIC_REAL"
NA = "NOT AVAILABLE FROM PUBLIC DATA"


def _days(f: dict) -> int:
    return (date.fromisoformat(f["end"]) - date.fromisoformat(f["start"])).days


def _ref(f: dict) -> dict:
    return {"concept": f"us-gaap:{f['concept']}", "value": f["val"], "period": (f"{f['start']} → " if f.get("start") else "")
            + f["end"], "form": f["form"], "fiscal_period": f"{f.get('fy')} {f.get('fp')}", "accession": f["accn"],
            "filed": f["filed"]}


@functools.lru_cache(maxsize=1)
def _shared_addenda() -> dict:
    """Market series that are not company-specific (e.g. the Single-A OAS) are refreshed with the primary snapshot and
    shared by every company, so an added issuer never loses a public market input."""
    p = Path(__file__).resolve().parents[1] / "data" / "public_company" / "caterpillar_snapshot.json"
    try:
        return json.loads(p.read_text(encoding="utf-8")).get("market_addenda") or {}
    except (OSError, ValueError):
        return {}


def _usd(x: float | None) -> str:
    if x is None:
        return "n/a"
    a, s = abs(x), "-" if x < 0 else ""
    return f"{s}${a / 1e9:.2f}B" if a >= 1e9 else f"{s}${a / 1e6:.1f}M" if a >= 1e6 else f"{s}${a:,.0f}"


class PublicCompany:
    """Analytics over one issuer's companyfacts snapshot + Meridian's public market-data snapshot."""

    def __init__(self, snapshot: dict | None = None, market: dict | None = None):
        self.snap = snapshot or load_snapshot()
        if market is None:
            p = PACKAGE_ROOT / "data" / "market_snapshots" / "market_snapshot.json"
            market = json.loads(p.read_text(encoding="utf-8"))
        self.market = market
        self.facts: dict[str, list[dict]] = self.snap["facts"]
        self.subject = self.snap["subject"]
        self.cik = int(self.subject["cik"])
        self.captive = bool(self.subject.get("captive_finance"))

    # ------------------------------------------------------------------------------------------- fact access
    def instants(self, key: str) -> dict[str, dict]:
        return {f["end"]: f for f in self.facts.get(key, []) if not f.get("start")}

    def at(self, key: str, end: str) -> dict | None:
        return self.instants(key).get(end)

    def latest(self, key: str) -> dict | None:
        inst = self.instants(key)
        return inst[max(inst)] if inst else None

    @cached_property
    def quarter_ends(self) -> list[str]:
        ends = sorted({f["end"] for f in self.facts.get("cash", [])})
        return ends

    @cached_property
    def latest_quarter(self) -> str:
        return self.quarter_ends[-1]

    @cached_property
    def latest_fy(self) -> str:
        """Latest fiscal year end with a 10-K balance sheet (debt detail is annual-only in companyfacts)."""
        fy = [f["end"] for k in ("revenue", "ocf") for f in self.facts.get(k, [])
              if f.get("start") and f["form"] == "10-K" and _days(f) > 330]
        return max(fy) if fy else self.latest_quarter

    @cached_property
    def debt_date(self) -> str:
        """Latest balance-sheet date carrying both long-term debt and cash (10-Q when the issuer tags it, else 10-K)."""
        cash = set(self.instants("cash"))
        floor = (date.fromisoformat(self.latest_fy) - timedelta(days=31)).isoformat()
        ends = [e for e in self.instants("ltd_noncurrent") if e in cash and e >= floor]
        # A debt fact older than the latest 10-K is stale (issuer changed tagging): report NOT AVAILABLE, never reuse it.
        return max(ends) if ends else "STALE"

    def quarterly(self, key: str) -> list[dict]:
        """Discrete quarterly values. 3-month facts are used as filed; otherwise quarter = YTD(n) − YTD(n−1), and
        Q4 = FY − 9-month YTD. Each derived value carries both source facts."""
        fs = [f for f in self.facts.get(key, []) if f.get("start")]
        q3 = {f["end"]: f for f in fs if 80 <= _days(f) <= 100}
        ytd = [f for f in fs if _days(f) > 100]
        by_start: dict[str, list[dict]] = {}
        for f in ytd:
            by_start.setdefault(f["start"], []).append(f)
        out: dict[str, dict] = {}
        for e, f in q3.items():
            out[e] = {"end": e, "value": f["val"], "derivation": "3-month value as filed", "facts": [_ref(f)],
                      "basis": "REPORTED"}
        for s, lst in by_start.items():
            chain = sorted(lst, key=lambda x: x["end"])
            q1 = next((f for f in q3.values() if f["start"] == s), None)
            prev = q1
            for f in chain:
                if f["end"] not in out and prev is not None:
                    label = "FY − 9-month YTD" if _days(f) > 330 else "YTD − prior YTD"
                    out[f["end"]] = {"end": f["end"], "value": f["val"] - prev["val"],
                                     "derivation": label, "facts": [_ref(f), _ref(prev)], "basis": "CALCULATED"}
                prev = f
        return [out[k] for k in sorted(out)]

    def short_term_debt(self, d: str) -> dict | None:
        """Debt due within 12 months at date d, from as-filed facts only (precedence stated in `how`)."""
        stb, cur, dc = self.at("short_term_borrowings", d), self.at("ltd_current", d), self.at("debt_current", d)
        mat = self.at("maturity_y1", d)
        if dc and not (stb or cur):
            return {"value": dc["val"], "facts": [dc], "how": "As filed", "formula": f"us-gaap:{dc['concept']}",
                    "note": "Issuer reports debt due within one year as a single line (short-term + current LTD)."}
        if stb or cur or mat:
            parts = [x for x in (stb, cur or mat) if x]
            note = ("" if cur else "Current portion of LTD from the ASC 470 maturity schedule (principal): the balance-sheet "
                                   "line is not published in companyfacts. " if mat else "")
            if not stb:
                note += "No short-term borrowings / commercial paper fact for this date: treated as nil. "
            return {"value": sum(x["val"] for x in parts), "facts": parts, "how": "Sum of as-filed facts",
                    "formula": " + ".join(f"us-gaap:{x['concept']}" for x in parts), "note": note.strip() or None}
        return None

    def debt_at(self, d: str) -> dict | None:
        st, ltd = self.short_term_debt(d), self.at("ltd_noncurrent", d)
        if not ltd:
            return None
        return {"value": (st["value"] if st else 0) + ltd["val"], "facts": (st["facts"] if st else []) + [ltd]}

    def ttm(self, key: str, end: str | None = None) -> dict | None:
        qs = self.quarterly(key)
        end = end or (qs[-1]["end"] if qs else None)
        idx = next((i for i, q in enumerate(qs) if q["end"] == end), None)
        if idx is None or idx < 3:
            return None
        win = qs[idx - 3: idx + 1]
        span = (date.fromisoformat(win[-1]["end"]) - date.fromisoformat(win[0]["end"])).days
        if not 250 <= span <= 290:     # four consecutive quarters only
            return None
        qsum = sum(q["value"] for q in win)
        out = {"end": end, "value": qsum, "quarters": [q["end"] for q in win],
               "facts": [r for q in win for r in q["facts"]], "basis": "CALCULATED",
               "method": "sum of 4 quarters", "quarter_sum": qsum, "note": None}
        br = self._bridge(key, str(end))           # end is set: idx was found above
        if br is not None:
            # Preferred: FY + YTD − prior-year YTD. It uses the LATEST filed comparatives, so it stays right when an
            # issuer recasts prior periods (e.g. discontinued operations) and a derived Q4 (FY − original 9-month) does not.
            val, facts, how = br
            out.update(value=val, facts=facts, method=how)
            if qsum and abs(qsum - val) > 0.005 * abs(val):
                out["note"] = (f"Quarter-by-quarter sum ({_usd(qsum)}) differs from the annual bridge ({_usd(val)}): "
                               "prior periods were recast in a later filing; the bridge uses the recast figures.")
        return out

    def _bridge(self, key: str, end: str) -> tuple[float, list[dict], str] | None:
        d = date.fromisoformat
        fs = [f for f in self.facts.get(key, []) if f.get("start")]
        at_end = [f for f in fs if f["end"] == end]
        annual = next((f for f in at_end if _days(f) > 330), None)
        if annual is not None:                                  # TTM ends at a fiscal year end: the year as filed
            return annual["val"], [_ref(annual)], "fiscal year as filed"
        ytd = max((f for f in at_end if 80 <= _days(f) <= 330), key=_days, default=None)
        if ytd is None:
            return None
        fy = next((f for f in fs if _days(f) > 330 and abs((d(ytd["start"]) - d(f["end"])).days - 1) <= 7), None)
        if fy is None:
            return None
        prior = next((f for f in fs if abs((d(f["start"]) - d(fy["start"])).days) <= 7
                      and abs((d(ytd["end"]) - d(f["end"])).days - 364) <= 7 and f["end"] < fy["end"]), None)
        if prior is None:
            return None
        return (fy["val"] + ytd["val"] - prior["val"], [_ref(fy), _ref(ytd), _ref(prior)],
                "FY + YTD − prior-year YTD")

    def fy(self, key: str, end: str | None = None) -> dict | None:
        end = end or self.latest_fy
        return next((f for f in self.facts.get(key, []) if f.get("start") and f["end"] == end and _days(f) > 330), None)

    # ------------------------------------------------------------------------------------------- metric builder
    def _metric(self, mid: str, label: str, value, unit: str, basis: str, period: str, transformation: str,
                calculation: str, facts: list[dict], note: str | None = None, assumptions: list[str] | None = None,
                market_inputs: list[dict] | None = None) -> dict:
        filings = sorted({(r["form"], r["accession"], r["filed"]) for r in facts})
        return {"metric_id": mid, "label": label, "value": value, "unit": unit, "basis": basis,
                "classification": CLASS if value is not None else "NOT_AVAILABLE", "period": period,
                "formula": calculation, "note": note, "assumptions": assumptions or [],
                "lineage": {
                    "source": f"SEC EDGAR XBRL company facts — {self.subject['name']} (CIK {self.cik:010d})",
                    "source_url": SEC_FACTS_URL.format(cik=self.cik),
                    "filings": [{"form": f, "accession": a, "filed": d,
                                 "url": f"https://www.sec.gov/Archives/edgar/data/{self.cik}/{a.replace('-', '')}/"}
                                for f, a, d in filings],
                    "period": period, "transformation": transformation, "calculation": calculation,
                    "result": value, "facts": facts, "market_inputs": market_inputs or []}}

    def _na(self, mid: str, label: str, reason: str) -> dict:
        return {"metric_id": mid, "label": label, "value": None, "unit": "", "basis": "NOT_AVAILABLE",
                "classification": "NOT_AVAILABLE", "period": None, "formula": None, "note": f"{NA}: {reason}",
                "assumptions": [], "lineage": None}

    # ------------------------------------------------------------------------------------------- market inputs
    @cached_property
    def rates(self) -> dict:
        m = self.market
        curve = {c["tenor"]: c["yield_pct"] for c in m["ust_curve"]["data"]["curve"]}
        a = (self.snap.get("market_addenda") or {}).get("credit_spread_a") or _shared_addenda().get("credit_spread_a") or {}
        return {"sofr_pct": m["sofr"]["data"]["sofr_pct"], "sofr_date": m["sofr"]["data"]["effective_date"],
                "ust5_pct": curve.get("5 Yr"), "ust_date": m["ust_curve"]["data"]["curve_date"],
                "bbb_oas_pct": m["credit_spread"]["data"]["latest"]["value"],
                "bbb_oas_date": m["credit_spread"]["data"]["latest"]["date"],
                "a_oas_pct": (a.get("latest") or {}).get("value"), "a_oas_date": (a.get("latest") or {}).get("date"),
                "effr_pct": m["effr"]["data"]["effr_pct"], "curve": m["ust_curve"]["data"]["curve"]}

    def _mkt(self, *keys) -> list[dict]:
        r = self.rates
        lab = {"sofr_pct": ("NY Fed SOFR", r["sofr_date"]), "ust5_pct": ("U.S. Treasury 5Y par yield", r["ust_date"]),
               "a_oas_pct": ("ICE BofA Single-A OAS (FRED BAMLC0A3CA)", r["a_oas_date"]),
               "bbb_oas_pct": ("ICE BofA BBB OAS (FRED BAMLC0A4CBBB)", r["bbb_oas_date"])}
        return [{"input": lab[k][0], "value_pct": r[k], "as_of": lab[k][1], "classification": "PUBLIC_REAL"} for k in keys]

    # ------------------------------------------------------------------------------------------- core metrics
    @cached_property
    def metrics(self) -> dict[str, dict]:
        q, fy, dd = self.latest_quarter, self.latest_fy, self.debt_date
        M: dict[str, dict] = {}

        def rep(mid, key, label, end=None):
            f = self.at(key, end) if end else self.latest(key)
            if not f:
                M[mid] = self._na(mid, label, f"us-gaap concept for '{label}' not published for this issuer")
                return
            M[mid] = self._metric(mid, label, f["val"], "USD", "REPORTED", f"as of {f['end']}",
                                  "None — as-filed XBRL value", f"us-gaap:{f['concept']}", [_ref(f)],
                                  CONCEPT_NOTES.get(f["concept"]))

        rep("cash", "cash", "Cash and cash equivalents")
        rep("receivables_trade", "receivables_trade", "Receivables – trade and other")
        rep("receivables_finance", "receivables_finance", "Receivables – finance (current)")
        rep("inventory", "inventory", "Inventories")
        rep("payables", "payables", "Accounts payable")
        rep("rpo", "rpo", "Remaining performance obligations (backlog-type disclosure)")
        rep("derivative_notional", "derivative_notional", "Derivatives – total notional")
        rep("short_term_borrowings", "short_term_borrowings", "Short-term borrowings", dd)
        rep("ltd_noncurrent", "ltd_noncurrent", "Long-term debt due after one year", dd)
        rep("maturity_y1", "maturity_y1", "Long-term debt maturing in the next 12 months", fy)
        rep("debt_current", "debt_current", "Debt due within one year (as one line)", dd)
        rep("credit_capacity", "credit_capacity", "Committed credit facilities – capacity", fy)
        rep("credit_available", "credit_available", "Committed credit facilities – remaining capacity", fy)

        for key, mid, lab in [("revenue", "revenue_ttm", "Revenue (TTM)"), ("cogs", "cogs_ttm", "Cost of goods sold (TTM)"),
                              ("ocf", "ocf_ttm", "Operating cash flow (TTM)"), ("capex", "capex_ttm", "Capital expenditure (TTM)"),
                              ("operating_income", "operating_income_ttm", "Operating profit (TTM)"),
                              ("dna", "dna_ttm", "Depreciation & amortization (TTM)"),
                              ("dividends", "dividends_ttm", "Dividends paid (TTM)"),
                              ("buybacks", "buybacks_ttm", "Share repurchases (TTM)"),
                              ("fx_cash_effect", "fx_cash_effect_ttm", "FX effect on cash (TTM)")]:
            t = self.ttm(key)
            if t is None:
                M[mid] = self._na(mid, lab, "four consecutive quarters not derivable from filed facts")
                continue
            concept = f"us-gaap:{self.facts[key][0]['concept']}"
            M[mid] = self._metric(mid, lab, t["value"], "USD", "CALCULATED",
                                  f"4 quarters ending {t['end']} ({', '.join(t['quarters'])})",
                                  {"sum of 4 quarters": "Quarter = 3-month fact, or YTD − prior YTD, or FY − 9-month YTD",
                                   "fiscal year as filed": "Fiscal-year value as filed (TTM ends at fiscal year end)"}.get(
                                      t["method"], "Latest fiscal year + current YTD − prior-year YTD (latest filed, "
                                      "recast comparatives)"),
                                  {"sum of 4 quarters": f"Σ last 4 quarters of {concept}",
                                   "fiscal year as filed": f"{concept}, fiscal year"}.get(
                                      t["method"], f"FY + YTD − prior-year YTD of {concept}"),
                                  t["facts"], t.get("note"))

        def calc(mid, label, value, unit, period, calcs, parts, note=None, basis="CALCULATED", assumptions=None, mkt=None):
            facts = [r for p in parts if p and p.get("lineage") for r in p["lineage"]["facts"]]
            if value is None or any(p is None or p.get("value") is None for p in parts):
                M[mid] = self._na(mid, label, "an input is not available in the public filing data")
                return
            M[mid] = self._metric(mid, label, value, unit, basis, period, "Arithmetic on REPORTED / CALCULATED inputs",
                                  calcs, facts, note, assumptions, mkt)

        g = M.get

        def v(k: str) -> Any:
            return (g(k) or {}).get("value")

        def need(mid, label, keys, fn, unit, period, calcs, note=None):
            """Calculate only when every input exists; otherwise NOT AVAILABLE (never a partial or imputed value)."""
            if any(v(k) is None for k in keys) or any(v(k) == 0 for k in keys if k in denominators.get(mid, ())):
                M[mid] = self._na(mid, label, f"input not available in the public filing data: "
                                  f"{', '.join(k for k in keys if v(k) is None) or 'zero denominator'}")
                return
            calc(mid, label, fn(), unit, period, calcs, [g(k) for k in keys], note)

        denominators = {"dso": ("revenue_ttm",), "dio": ("cogs_ttm",), "dpo": ("cogs_ttm",)}
        cap = self.captive
        need("fcf_ttm", "Free cash flow proxy (TTM)", ["ocf_ttm", "capex_ttm"], lambda: v("ocf_ttm") - v("capex_ttm"), "USD",
             (g("ocf_ttm") or {}).get("period"), "operating cash flow − capital expenditure",
             "Proxy: excludes captive-finance receivable investment, reported in investing activities" if cap else
             "Proxy: operating cash flow less purchases of property, plant and equipment")
        need("ebitda_ttm", "EBITDA proxy (TTM)", ["operating_income_ttm", "dna_ttm"],
             lambda: v("operating_income_ttm") + v("dna_ttm"), "USD", (g("operating_income_ttm") or {}).get("period"),
             "operating profit + depreciation & amortization", "Consolidated" + (", including captive finance" if cap else ""))
        need("shareholder_returns_ttm", "Shareholder returns (TTM)", ["dividends_ttm", "buybacks_ttm"],
             lambda: v("dividends_ttm") + v("buybacks_ttm"), "USD", (g("dividends_ttm") or {}).get("period"),
             "dividends paid + share repurchases")
        need("dso", "DSO (trade receivables)", ["receivables_trade", "revenue_ttm"],
             lambda: round(v("receivables_trade") / v("revenue_ttm") * 365, 1), "days", f"receivables at {q}; revenue TTM",
             "trade receivables / revenue TTM × 365",
             "Revenue includes financial-services revenues; trade receivables include dealer receivables" if cap else None)
        need("dio", "DIO", ["inventory", "cogs_ttm"], lambda: round(v("inventory") / v("cogs_ttm") * 365, 1), "days",
             f"inventory at {q}; COGS TTM", "inventories / cost of goods sold TTM × 365")
        need("dpo", "DPO", ["payables", "cogs_ttm"], lambda: round(v("payables") / v("cogs_ttm") * 365, 1), "days",
             f"payables at {q}; COGS TTM", "accounts payable / cost of goods sold TTM × 365")
        need("ccc", "Cash conversion cycle", ["dso", "dio", "dpo"], lambda: round(v("dso") + v("dio") - v("dpo"), 1), "days",
             f"at {q}", "DSO + DIO − DPO")
        need("trade_working_capital", "Trade working capital", ["receivables_trade", "inventory", "payables"],
             lambda: v("receivables_trade") + v("inventory") - v("payables"), "USD", f"as of {q}",
             "trade receivables + inventories − accounts payable")

        # ---- debt & liquidity at the latest balance-sheet date that carries debt (10-Q if tagged, else 10-K) ----
        lab = "FY end" if dd == fy else "quarter end"
        if dd == "STALE":
            lab, dd = "no current debt facts", "n/a"
        cash_d = self.at("cash", dd)
        M["cash_fy"] = self._metric("cash_fy", f"Cash and cash equivalents ({lab} {dd})", cash_d["val"], "USD", "REPORTED",
                                    f"as of {dd}", "None — as-filed XBRL value", f"us-gaap:{cash_d['concept']}",
                                    [_ref(cash_d)], CONCEPT_NOTES.get(cash_d["concept"])) if cash_d else \
            self._na("cash_fy", "Cash at the debt balance-sheet date", "not reported")
        st = self.short_term_debt(dd)
        if st:
            M["st_obligations"] = self._metric(
                "st_obligations", "Debt due within 12 months", st["value"], "USD", "CALCULATED" if len(st["facts"]) > 1
                else "REPORTED", f"as of {dd}", st["how"], st["formula"], [_ref(f) for f in st["facts"]], st["note"])
            for f in st["facts"]:
                if f["concept"] in ("LongTermDebtCurrent", "LongTermDebtAndCapitalLeaseObligationsCurrent"):
                    M["ltd_current"] = self._metric("ltd_current", "Long-term debt due within one year", f["val"], "USD",
                                                    "REPORTED", f"as of {dd}", "None — as filed", f"us-gaap:{f['concept']}",
                                                    [_ref(f)], CONCEPT_NOTES.get(f["concept"]))
        else:
            M["st_obligations"] = self._na("st_obligations", "Debt due within 12 months",
                                           "no short-term or current-debt facts at the debt balance-sheet date")
        need("total_debt", f"Total debt ({lab} {dd})", ["st_obligations", "ltd_noncurrent"],
             lambda: v("st_obligations") + v("ltd_noncurrent"), "USD", f"as of {dd}",
             "debt due within 12 months + long-term debt due after one year",
             ((st or {}).get("note") or "") + (" Consolidated: includes captive-finance debt that funds finance receivables."
                                           if cap else ""))
        need("net_debt", f"Net debt ({lab} {dd})", ["total_debt", "cash_fy"], lambda: v("total_debt") - v("cash_fy"), "USD",
             f"as of {dd}", "total debt − cash and cash equivalents")
        e_fy, d_fy = self.fy("operating_income"), self.fy("dna")
        if e_fy and d_fy:
            M["ebitda_fy"] = self._metric("ebitda_fy", f"EBITDA proxy (FY ending {fy})", e_fy["val"] + d_fy["val"], "USD",
                                          "CALCULATED", f"FY ending {fy}", "FY facts as filed", "operating profit + D&A",
                                          [_ref(e_fy), _ref(d_fy)])
        lev_base = "ebitda_fy" if dd == fy else "ebitda_ttm"
        need("gross_leverage", "Gross leverage (total debt / EBITDA proxy)", ["total_debt", lev_base],
             lambda: round(v("total_debt") / v(lev_base), 2) if v(lev_base) else None, "x", f"debt {dd}; {lev_base}",
             f"total debt / {'FY' if dd == fy else 'TTM'} EBITDA proxy",
             "Consolidated incl. captive-finance debt — not comparable to industrial-only leverage" if cap else None)
        need("net_leverage", "Net leverage (net debt / EBITDA proxy)", ["net_debt", lev_base],
             lambda: round(v("net_debt") / v(lev_base), 2) if v(lev_base) else None, "x", f"debt {dd}; {lev_base}",
             f"net debt / {'FY' if dd == fy else 'TTM'} EBITDA proxy")
        ip = self.fy("interest_paid")
        if ip:
            M["interest_paid_fy"] = self._metric("interest_paid_fy", f"Cash interest paid (FY ending {fy})", ip["val"],
                                                 "USD", "REPORTED", f"FY ending {fy}", "None — as filed",
                                                 f"us-gaap:{ip['concept']}", [_ref(ip)])
        need("interest_cover", "Interest cover proxy (EBITDA / cash interest)", ["ebitda_fy", "interest_paid_fy"],
             lambda: round(v("ebitda_fy") / v("interest_paid_fy"), 2) if v("interest_paid_fy") else None, "x",
             f"FY ending {fy}", "FY EBITDA proxy / FY cash interest paid")
        prev_fy = f"{int(fy[:4]) - 1}{fy[4:]}"
        d0, d1 = self.debt_at(prev_fy), self.debt_at(fy)
        if ip and d0 and d1:
            avg = (d0["value"] + d1["value"]) / 2
            M["avg_cost_of_debt"] = self._metric(
                "avg_cost_of_debt", "Implied average cash cost of debt", round(ip["val"] / avg, 4), "ratio", "CALCULATED",
                f"FY ending {fy}", "Average of opening and closing total debt (10-K balance sheets)",
                "cash interest paid / average(total debt at prior FY end, total debt at FY end)",
                [_ref(ip), *[_ref(f) for f in d0["facts"] + d1["facts"]]],
                "Cash interest includes captive-finance funding cost" if cap else None)
            M["avg_cost_of_debt"]["mixed_dates_reason"] = "average of two year-end debt balances by definition"
        else:
            M["avg_cost_of_debt"] = self._na("avg_cost_of_debt", "Implied average cash cost of debt",
                                             "cash interest paid or year-end debt for two consecutive 10-Ks not available")
        # Liquidity and its coverage are measured at ONE balance-sheet date: the latest date at which cash, undrawn
        # committed facilities and debt due within 12 months are all filed. Different dates are never combined.
        liq = None
        for d in dict.fromkeys([dd, fy]):
            ca_d, cash_l, st_l = self.at("credit_available", d), self.at("cash", d), self.short_term_debt(d)
            if ca_d and cash_l:
                liq = (d, ca_d, cash_l, st_l)
                break
        if liq:
            ld, ca, cash_l, st_l = liq
            tail = (f"debt metrics elsewhere are as of {dd}." if dd != "STALE" else
                    "later debt tagging by this issuer is not usable, so other debt metrics are NOT AVAILABLE.")
            moved = (f"Measured at {ld}, the latest date at which both cash and undrawn committed facilities are filed; "
                     + tail) if ld != dd else None
            M["liquidity_fy"] = self._metric(
                "liquidity_fy", f"Liquidity (cash + undrawn committed facilities, {ld})", cash_l["val"] + ca["val"], "USD",
                "CALCULATED", f"as of {ld} (cash and facilities same date)", "Arithmetic on REPORTED inputs",
                "cash and cash equivalents + remaining capacity of committed credit facilities",
                [_ref(cash_l), _ref(ca)], moved)
            M["liquidity_fy"]["as_of"] = ld
            if st_l and st_l["value"]:
                M["liquidity_coverage"] = self._metric(
                    "liquidity_coverage", "Liquidity coverage of 12-month debt",
                    round((cash_l["val"] + ca["val"]) / st_l["value"], 2), "x", "CALCULATED", f"as of {ld}",
                    "Arithmetic on REPORTED inputs, single balance-sheet date", "liquidity / debt due within 12 months",
                    [_ref(cash_l), _ref(ca), *[_ref(f) for f in st_l["facts"]]], moved)
                M["liquidity_coverage"]["as_of"] = ld
                M["liquidity_coverage"]["debt_due_usd"] = st_l["value"]   # unrounded, same date: for rule arithmetic
            else:
                M["liquidity_coverage"] = self._na("liquidity_coverage", "Liquidity coverage of 12-month debt",
                                                   f"debt due within 12 months not filed at {ld}")
        else:
            M["liquidity_fy"] = self._na("liquidity_fy", "Liquidity (cash + undrawn committed facilities)",
                                         "undrawn committed-facility capacity is not tagged by this issuer")
            M["liquidity_coverage"] = self._na("liquidity_coverage", "Liquidity coverage of 12-month debt",
                                               "input not available in the public filing data: liquidity_fy")
        cur_key = "ltd_current" if "ltd_current" in M else "maturity_y1"
        r = self.rates
        if v("st_obligations"):
            calc("repricing_100bps", "Annual cost of +100 bps on debt repricing within 12 months",
                 round(v("st_obligations") * 0.01), "USD", f"debt as of {dd}", "debt due within 12 months × 1.00%",
                 [g("st_obligations")], basis="ESTIMATED",
                 assumptions=["All debt due within 12 months is refinanced at prevailing rates (no prepayment)",
                              "Fixed/floating split and hedges are NOT disclosed in companyfacts and are ignored"])
        if v(cur_key) and v("avg_cost_of_debt") and r["ust5_pct"] and r["a_oas_pct"]:
            new_rate = (r["ust5_pct"] + r["a_oas_pct"]) / 100
            delta = new_rate - v("avg_cost_of_debt")
            calc("refi_cost_delta", "Annual cost change refinancing 12-month maturities at today's market",
                 round(v(cur_key) * delta), "USD",
                 f"maturities {(g(cur_key) or {}).get('period')}; cost of debt FY {fy}; market {r['ust_date']}",
                 "LTD due within one year × ((UST 5Y + Single-A OAS) − implied average cost of debt)",
                 [g(cur_key), g("avg_cost_of_debt")], basis="ESTIMATED",
                 assumptions=[f"New 5-year issue at UST 5Y {r['ust5_pct']:.2f}% + Single-A OAS {r['a_oas_pct']:.2f}% = "
                              f"{new_rate:.2%} (no new-issue premium, no fees)",
                              "Single-A spread is a market-segment proxy, not the issuer's own curve"],
                 mkt=self._mkt("ust5_pct", "a_oas_pct"))
            if "refi_cost_delta" in M and M["refi_cost_delta"]["value"] is not None:
                M["refi_cost_delta"]["mixed_dates_reason"] = ("a rate (FY implied cost of debt, market yield) applied to the "
                                                              "latest maturing balance; each input is dated separately")
        return M

    # ------------------------------------------------------------------------------------------- views
    def trend(self) -> list[dict]:
        """Quarter-by-quarter: balances (REPORTED) and TTM-based working-capital days (CALCULATED)."""
        rows = []
        for e in self.quarter_ends[-12:]:
            rv, cg = self.ttm("revenue", e), self.ttm("cogs", e)
            ar, inv, ap, cash = (self.at(k, e) for k in ("receivables_trade", "inventory", "payables", "cash"))
            rq = next((x for x in self.quarterly("revenue") if x["end"] == e), None)
            ocf = next((x for x in self.quarterly("ocf") if x["end"] == e), None)
            cpx = next((x for x in self.quarterly("capex") if x["end"] == e), None)
            row: dict[str, Any] = {"end": e, "cash_usd": cash and cash["val"], "receivables_usd": ar and ar["val"],
                                   "inventory_usd": inv and inv["val"], "payables_usd": ap and ap["val"],
                                   "revenue_q_usd": rq and rq["value"], "ocf_q_usd": ocf and ocf["value"],
                                   "fcf_q_usd": (ocf["value"] - cpx["value"]) if ocf and cpx else None,
                                   "revenue_ttm_usd": rv and rv["value"]}
            if rv and ar:
                row["dso"] = round(ar["val"] / rv["value"] * 365, 1)
            if cg and inv:
                row["dio"] = round(inv["val"] / cg["value"] * 365, 1)
            if cg and ap:
                row["dpo"] = round(ap["val"] / cg["value"] * 365, 1)
            if all(row.get(k) is not None for k in ("dso", "dio", "dpo")):
                row["ccc"] = round(row["dso"] + row["dio"] - row["dpo"], 1)
            if ar and inv and ap:
                row["trade_wc_usd"] = ar["val"] + inv["val"] - ap["val"]
            rows.append(row)
        return rows

    def wc_bridge(self) -> dict:
        """Change in trade working capital and CCC over the last 4 quarters, decomposed by component."""
        t = [r for r in self.trend() if r.get("ccc") is not None]
        if len(t) < 5:
            return {"available": False, "reason": NA}
        a, b = t[-5], t[-1]
        comps = [{"component": "Receivables (DSO)", "days_change": round(b["dso"] - a["dso"], 1),
                  "usd_change": b["receivables_usd"] - a["receivables_usd"]},
                 {"component": "Inventory (DIO)", "days_change": round(b["dio"] - a["dio"], 1),
                  "usd_change": b["inventory_usd"] - a["inventory_usd"]},
                 {"component": "Payables (DPO, inverse)", "days_change": round(-(b["dpo"] - a["dpo"]), 1),
                  "usd_change": -(b["payables_usd"] - a["payables_usd"])}]
        return {"available": True, "from": a["end"], "to": b["end"], "ccc_from": a["ccc"], "ccc_to": b["ccc"],
                "ccc_change": round(b["ccc"] - a["ccc"], 1), "trade_wc_change_usd": b["trade_wc_usd"] - a["trade_wc_usd"],
                "components": comps, "basis": "CALCULATED",
                "formula": "Δ trade WC = ΔAR + ΔInventory − ΔAP; ΔCCC = ΔDSO + ΔDIO − ΔDPO (TTM denominators)"}

    def maturities(self) -> dict:
        fy = self.latest_fy
        rows: list[dict[str, Any]] = []
        for i, k in enumerate(["maturity_y1", "maturity_y2", "maturity_y3", "maturity_y4", "maturity_y5"], 1):
            f = self.at(k, fy)
            rows.append({"bucket": f"{int(fy[:4]) + i}", "amount_usd": f and f["val"], "basis": "REPORTED" if f else
                         "NOT_AVAILABLE", "fact": f and _ref(f)})
        cur = self.metrics.get("ltd_current", {}).get("value") or self.metrics.get("maturity_y1", {}).get("value") or 0
        ltd_total = cur + (self.metrics.get("ltd_noncurrent", {}).get("value") or 0)
        known = sum(r["amount_usd"] or 0 for r in rows)
        rows.append({"bucket": f"after {int(fy[:4]) + 5}", "amount_usd": ltd_total - known if ltd_total > known else None,
                     "basis": "CALCULATED", "fact": None,
                     "note": "Residual: (LTD due in 12 months + LTD due after one year) − years 1–5 schedule; the "
                             "'thereafter' line is not published in companyfacts. Includes unamortised discounts/hedge "
                             "adjustments, so it is an approximation."})
        return {"as_of": fy, "rows": rows, "short_term_borrowings_usd": self.metrics.get("short_term_borrowings", {}).get("value")}

    def peers(self) -> dict:
        subj = self.snap.get("subject_fy_working_capital") or {}
        ps = self.snap.get("peers", [])

        def med(k):
            vals = [p[k] for p in ps if p.get(k) is not None]
            return round(statistics.median(vals), 1) if vals else None

        return {"subject": {k: subj.get(k) for k in ("company", "fiscal_year_end", "dso_days", "dio_days", "dpo_days",
                                                        "ccc_days", "revenue_usd", "accession", "filed", "lineage")},
                "peers": ps, "median": {k: med(k) for k in ("dso_days", "dio_days", "dpo_days", "ccc_days")},
                "issues": self.snap.get("peer_issues", []), "basis": "CALCULATED (FY 10-K facts, same method for all)",
                "peer_set": self.snap.get("peer_set", "Meridian default industrial peer set"),
                "caveat": (f"Receivable definitions differ: {self.subject['name']}'s trade receivables include dealer "
                           "receivables; peers without captive finance are not like-for-like. " if self.captive else
                           f"Peer set: {self.snap.get('peer_set', 'Meridian default industrial peer set')} — business "
                           "models and receivable definitions differ. ") + "Use as directional context only."}

    def forecast(self) -> dict:
        """Seasonal run-rate cash outlook with an empirical range, and a point-in-time forecast-vs-actual back-test
        on reported quarters. Method (stated, reproducible):
          P50 change for a quarter = the change reported in the same quarter one year earlier (seasonal naive),
          range = P50 ± 1.2816 × σ(back-test errors) × √k for the k-th quarter ahead (≈ P10 / P90).
        No private data is used; announced-but-unreported events are not modelled."""
        cash = [(e, f["val"]) for e in self.quarter_ends if (f := self.at("cash", e))]
        if len(cash) < 7:
            return {"available": False, "start": {"date": cash[-1][0] if cash else None, "cash_usd": cash[-1][1] if cash else None},
                    "history": [{"end": e, "cash_usd": c} for e, c in cash], "outlook": [], "backtest": [], "mape": None,
                    "mape_naive_no_change": None, "error_sd_usd": None, "basis": "NOT_AVAILABLE",
                    "method": f"{NA}: fewer than seven reported quarterly cash balances", "limitations": []}
        ch = [(cash[i][0], cash[i][1] - cash[i - 1][1]) for i in range(1, len(cash))]

        def backtest(seasonal: bool) -> list[dict]:
            rows = []
            for j in range(5, len(cash)):      # point-in-time: only quarters reported before quarter j are used
                fc = cash[j - 1][1] + (cash[j - 4][1] - cash[j - 5][1] if seasonal else 0)
                rows.append({"quarter_end": cash[j][0], "forecast_cash_usd": max(fc, 0), "actual_cash_usd": cash[j][1],
                             "error_usd": cash[j][1] - max(fc, 0)})
            return rows

        def mape_of(rows: list[dict]):
            return round(statistics.mean(abs(b["error_usd"]) / b["actual_cash_usd"] for b in rows), 4) if rows else None

        b_seas, b_flat = backtest(True), backtest(False)
        m_seas, m_flat = mape_of(b_seas), mape_of(b_flat)
        # keep the method with the better point-in-time track record (both errors are reported)
        seasonal = m_seas is not None and (m_flat is None or m_seas <= m_flat)
        back = b_seas if seasonal else b_flat
        mape = m_seas if seasonal else m_flat
        sd = statistics.pstdev([b["error_usd"] for b in back]) if len(back) > 1 else 0.0
        start = cash[-1]
        y, m = int(start[0][:4]), int(start[0][5:7])
        out, lvl = [], start[1]
        recent = ch[-4:]
        floored = False
        for k in range(1, 5):
            m += 3
            if m > 12:
                m, y = m - 12, y + 1
            if seasonal:
                lvl += recent[k - 1][1]
            band = 1.2816 * sd * (k ** 0.5)
            p50, p10 = max(lvl, 0), max(lvl - band, 0)
            floored = floored or p10 == 0 or p50 == 0
            out.append({"quarter": f"{y}-Q{(m - 1) // 3 + 1}", "p50_usd": round(p50), "p10_usd": round(p10),
                        "p90_usd": round(max(lvl + band, 0)), "seasonal_reference": recent[k - 1][0] if seasonal else None})
        method = ("Seasonal naive: each quarter's cash change = the change reported in the same quarter a year earlier"
                  if seasonal else
                  "No-change (persistence): cash stays at the last reported balance — chosen because it back-tests better "
                  f"than the seasonal method ({m_flat:.1%} vs {m_seas:.1%} MAPE) for this issuer")
        method += "; band = ±1.28 σ of point-in-time back-test errors × √quarters ahead (≈ P10/P90)"
        method += "; cash floored at zero." if floored else "."
        lim = ["Ignores announced but unreported events (buybacks, M&A, debt issuance, dividends changes)",
               "Cash only — liquidity facilities and refinancing are not projected",
               "A 13-week, transaction-level forecast needs bank and ERP data (CLIENT · NOT CONFIGURED)"]
        if mape is not None and mape > 0.4:
            lim.insert(0, f"Low reliability: back-test error {mape:.0%} — this issuer's cash is driven by discrete "
                          "events (acquisitions, debt, buybacks) that a run-rate cannot anticipate")
        return {"available": True, "start": {"date": start[0], "cash_usd": start[1]},
                "history": [{"end": e, "cash_usd": c} for e, c in cash],
                "outlook": out, "backtest": back, "mape": mape, "mape_naive_no_change": m_flat, "mape_seasonal": m_seas,
                "method_selected": "seasonal" if seasonal else "no_change", "error_sd_usd": round(sd),
                "basis": "ESTIMATED", "method": method, "limitations": lim}

    def scenario(self, revenue_change_pct: float = 0.0, dso_change_days: float = 0.0, dio_change_days: float = 0.0,
                 rate_shock_bps: float = 0.0, spread_shock_bps: float = 0.0) -> dict:
        M = self.metrics

        def val(k: str) -> Any:
            return (M.get(k) or {}).get("value")
        miss = [k for k in ("revenue_ttm", "ebitda_ttm", "fcf_ttm") if val(k) is None]
        if miss:
            return {"inputs": {}, "impacts": [], "available": False, "basis": "NOT_AVAILABLE",
                    "fcf_base_usd": None, "fcf_scenario_usd": None, "liquidity_coverage_base": None,
                    "liquidity_coverage_scenario": None, "gross_leverage_base": None, "gross_leverage_scenario": None,
                    "assumptions": [f"{NA}: {', '.join(miss)}"]}
        rev, cogs = val("revenue_ttm"), val("cogs_ttm") or 0
        ebitda, fcf = val("ebitda_ttm"), val("fcf_ttm")
        margin = ebitda / rev
        d_rev = rev * revenue_change_pct / 100
        d_ebitda = d_rev * margin                               # constant-margin assumption, stated
        d_ar = (rev * (1 + revenue_change_pct / 100) / 365 * dso_change_days
                + (val("receivables_trade") or 0) * revenue_change_pct / 100)
        d_inv = cogs * (1 + revenue_change_pct / 100) / 365 * dio_change_days + (val("inventory") or 0) * revenue_change_pct / 100
        d_int = (val("st_obligations") or 0) * (rate_shock_bps + spread_shock_bps) / 10_000
        d_fcf = d_ebitda - d_ar - d_inv - d_int
        cov = (val("liquidity_fy") + d_fcf) / val("st_obligations") if val("liquidity_fy") and val("st_obligations") else None
        # same EBITDA basis as the reported gross-leverage metric, so the base case reproduces it exactly
        e_key = "ebitda_fy" if self.debt_date == self.latest_fy else "ebitda_ttm"
        lev = val("total_debt") / (val(e_key) + d_ebitda) if val("total_debt") and val(e_key) else None
        return {"inputs": {"revenue_change_pct": revenue_change_pct, "dso_change_days": dso_change_days,
                           "dio_change_days": dio_change_days, "rate_shock_bps": rate_shock_bps,
                           "spread_shock_bps": spread_shock_bps},
                "impacts": [{"label": "Change in EBITDA (constant TTM margin)", "value_usd": round(d_ebitda)},
                            # FCF effects: positive = cash released, negative = cash absorbed (more tied up)
                            {"label": "Receivables: cash released (+) / absorbed (−)", "value_usd": round(-d_ar)},
                            {"label": "Inventory: cash released (+) / absorbed (−)", "value_usd": round(-d_inv)},
                            {"label": "Interest on debt repricing within 12 months", "value_usd": round(-d_int)},
                            {"label": "Change in free cash flow (first year)", "value_usd": round(d_fcf)}],
                "fcf_base_usd": fcf, "fcf_scenario_usd": round(fcf + d_fcf),
                "available": True, "liquidity_coverage_base": M.get("liquidity_coverage", {}).get("value"),
                "liquidity_coverage_scenario": round(cov, 2) if cov else None,
                "gross_leverage_base": M.get("gross_leverage", {}).get("value"),
                "gross_leverage_scenario": round(lev, 2) if lev else None, "basis": "SCENARIO",
                "assumptions": [f"EBITDA moves with revenue at the TTM margin ({margin:.1%})",
                                "Receivables and inventory scale with revenue, plus the stated days change",
                                "Rate and spread shocks apply to debt due within 12 months only (refinanced at market)",
                                "No management action; tax effects ignored"]}

    PRESETS = [("Base case", {}), ("Demand downturn: revenue −15%", {"revenue_change_pct": -15}),
               ("Collections slow: DSO +10 days", {"dso_change_days": 10}),
               ("Inventory build: DIO +15 days", {"dio_change_days": 15}),
               ("Rates +100 bps", {"rate_shock_bps": 100}), ("Credit stress: spread +150 bps", {"spread_shock_bps": 150}),
               ("Combined downturn", {"revenue_change_pct": -15, "dso_change_days": 10, "dio_change_days": 15,
                                      "rate_shock_bps": 100, "spread_shock_bps": 150})]

    def scenarios(self) -> list[dict]:
        return [{"name": n, **self.scenario(**kw)} for n, kw in self.PRESETS]

    def data_gaps(self) -> list[dict]:
        return [
            {"capability": "Bank balances, bank accounts, cash by legal entity / currency", "status": "CLIENT · NOT CONFIGURED",
             "reason": "Private. Requires bank statements (camt.053 / BAI2) or a bank/TMS connector."},
            {"capability": "Invoices, payments, customer & supplier ledgers (collections, discount capture)",
             "status": "CLIENT · NOT CONFIGURED", "reason": "Private. Requires an ERP extract or API (SAP / Oracle / NetSuite)."},
            {"capability": "13-week transaction-level cash forecast and intercompany optimisation",
             "status": "CLIENT · NOT CONFIGURED",
             "reason": "Needs bank and ERP data. Demonstrated on the SYNTHETIC · EVALUATION tenant."},
            {"capability": "Segment / business-unit financials", "status": NA,
             "reason": "Disclosed in the 10-K segment note, but tagged with XBRL dimensions that the companyfacts API does "
                       "not publish."},
            {"capability": "Quarterly debt balances (10-Q), interest expense line", "status": NA,
             "reason": "Tagged with company-specific XBRL elements not published in companyfacts; annual 10-K debt facts are used."},
            {"capability": "Fixed/floating debt mix, hedge positions, currency exposure by currency", "status": NA,
             "reason": "Narrative/tabular disclosure only; not available as structured facts. Derivative notional is shown."},
            {"capability": "Covenant definitions and headroom", "status": NA,
             "reason": "Credit agreement terms are not in structured filing data; Meridian does not infer covenants."},
        ] + ([{"capability": "Industrial vs captive-finance (Financial Products) split", "status": NA,
                "reason": "Supplemental consolidating data is dimensional; consolidated figures are shown and labelled."}]
             if self.captive else [])

    STALE_AFTER_DAYS = 135      # one quarter + the 45-day 10-Q filing deadline: a newer filing should exist by then

    def freshness(self, as_of: date | None = None) -> dict:
        """Is the snapshot behind the issuer's filing cadence? Never hidden: surfaced in provenance and the UI."""
        from meridian.config import get_settings

        ref = as_of or get_settings().effective_as_of()
        if not self.latest_quarter:
            return {"latest_period": None, "age_days": None, "stale": True,
                    "warning": "STALE: no quarterly balance sheet in the snapshot"}
        age = (ref - date.fromisoformat(self.latest_quarter)).days
        stale = age > self.STALE_AFTER_DAYS
        return {"latest_period": self.latest_quarter, "as_of": ref.isoformat(), "age_days": age, "stale": stale,
                "retrieved_at": self.snap.get("retrieved_at"),
                "warning": (f"STALE: latest filed period {self.latest_quarter} is {age} days old (> {self.STALE_AFTER_DAYS}); "
                            "older than one quarter plus the 45-day 10-Q deadline. Refresh the company from SEC EDGAR; if SEC "
                            "still has nothing newer, treat these figures as out of date") if stale else None}

    def provenance(self) -> list[dict]:
        r = self.rates
        snap = self.snap
        filings = sorted({(f["form"], f["accn"], f["filed"], f["end"]) for fs in self.facts.values() for f in fs},
                         key=lambda x: x[2])
        latest_filing = filings[-1] if filings else None
        return [
            {"source_id": "public.sec.companyfacts", "name": f"{self.subject['name']} — SEC XBRL company facts",
             "classification": "PUBLIC_REAL", "system": "SEC EDGAR (data.sec.gov)", "uri": self.subject["source_url"],
             "retrieved_at": snap["retrieved_at"], "as_of": self.latest_quarter,
             "latest_filing": latest_filing and {"form": latest_filing[0], "accession": latest_filing[1],
                                                 "filed": latest_filing[2], "period": latest_filing[3]},
             "record_count": sum(len(v) for v in self.facts.values()), "checksum_sha256": snap["checksum_sha256"],
             "validation": "PASSED" if not (snap.get("missing_concepts") or self.freshness()["stale"])
             else "PASSED_WITH_WARNINGS",
             "issues": ([f"Concept not published by issuer: {', '.join(snap.get('missing_concepts', []))}"]
                        if snap.get("missing_concepts") else []) + ([self.freshness()["warning"]]
                                                                      if self.freshness()["stale"] else [])},
            {"source_id": "public.sec.peers", "name": "Peer 10-K working-capital facts", "classification": "PUBLIC_REAL",
             "system": "SEC EDGAR (data.sec.gov)", "uri": "https://data.sec.gov/api/xbrl/companyfacts/",
             "retrieved_at": snap["retrieved_at"], "as_of": max((p["filed"] for p in snap.get("peers", [])), default=None),
             "record_count": len(snap.get("peers", [])), "checksum_sha256": snap["checksum_sha256"],
             "validation": "PASSED_WITH_WARNINGS" if snap.get("peer_issues") else "PASSED", "issues": snap.get("peer_issues", [])},
            {"source_id": "market.sofr", "name": "NY Fed SOFR", "classification": "PUBLIC_REAL", "system": "NY Fed Markets API",
             "as_of": r["sofr_date"], "validation": "PASSED", "issues": []},
            {"source_id": "market.ust_curve", "name": "U.S. Treasury par yield curve", "classification": "PUBLIC_REAL",
             "system": "U.S. Treasury", "as_of": r["ust_date"], "validation": "PASSED", "issues": []},
            {"source_id": "market.credit_spread_a", "name": "ICE BofA Single-A OAS", "classification": "PUBLIC_REAL",
             "system": "FRED (BAMLC0A3CA)", "as_of": r["a_oas_date"], "validation": "PASSED" if r["a_oas_pct"] else "FAILED",
             "issues": []},
            {"source_id": "market.credit_spread", "name": "ICE BofA BBB OAS", "classification": "PUBLIC_REAL",
             "system": "FRED (BAMLC0A4CBBB)", "as_of": r["bbb_oas_date"], "validation": "PASSED", "issues": []},
        ]

    def overview(self) -> dict:
        M = self.metrics
        return {"company": self.subject, "classification": CLASS, "latest_quarter": self.latest_quarter,
                "latest_fiscal_year_end": self.latest_fy, "debt_date": self.debt_date, "retrieved_at": self.snap["retrieved_at"],
                "metrics": M, "trend": self.trend(), "wc_bridge": self.wc_bridge(), "maturities": self.maturities(),
                "rates": {k: v for k, v in self.rates.items() if k != "curve"}, "curve": self.rates["curve"],
                "provenance": self.provenance(), "data_gaps": self.data_gaps(), "freshness": self.freshness()}


# ------------------------------------------------------------------------------------------------ multi-company registry
_CACHE: dict[int, PublicCompany] = {}
PRIMARY_CIK = 18230


def _bundled() -> dict[int, Path]:
    from meridian.public_company.sec import SNAP_DIR, SNAPSHOT

    out = {PRIMARY_CIK: SNAPSHOT}
    for p in SNAP_DIR.glob("CIK*.json"):
        out[int(p.stem[3:])] = p
    return out


def _stored_snapshot(cik: int) -> dict | None:
    from meridian.store.factory import get_store

    blob = get_store().get_blob(f"public_company/CIK{cik:010d}.json")
    return json.loads(blob.decode("utf-8")) if blob else None


def list_companies() -> list[dict]:
    """Bundled snapshots + companies added by users (persisted in the store: MongoDB/GridFS in production)."""
    from meridian.store.factory import get_store

    rows = {}
    for cik, path in _bundled().items():
        snap = json.loads(path.read_text(encoding="utf-8"))
        s = snap["subject"]
        rows[cik] = {"cik": cik, "ticker": s.get("ticker"), "name": s.get("name") or s.get("entity_name"),
                     "retrieved_at": snap["retrieved_at"], "source": "bundled snapshot", "primary": cik == PRIMARY_CIK}
    for d in get_store().find("public_companies", limit=500):
        rows.setdefault(int(d["cik"]), {k: d.get(k) for k in ("cik", "ticker", "name", "retrieved_at", "added_by", "added_at")}
                        | {"source": "added from SEC EDGAR", "primary": False})
    return sorted(rows.values(), key=lambda r: (not r["primary"], r["name"] or ""))


def get_public_company(cik: int | None = None) -> PublicCompany:
    cik = int(cik or PRIMARY_CIK)
    if cik not in _CACHE:
        path = _bundled().get(cik)
        snap = json.loads(path.read_text(encoding="utf-8")) if path else _stored_snapshot(cik)
        if snap is None:
            raise KeyError(f"Company CIK {cik} has not been added")
        _CACHE[cik] = PublicCompany(snap)
    return _CACHE[cik]


def add_company(query: str, user: str, peers: list[str] | None = None, refresh: bool = False) -> dict:
    """Add any SEC-reporting company by ticker or CIK: fetch its XBRL company facts live from SEC EDGAR, validate,
    snapshot (dated, checksummed), persist and register it. Never fabricates: if the filer lacks the required us-gaap
    facts, the add is refused with the reason."""
    from datetime import UTC, datetime

    from meridian.config import get_settings
    from meridian.public_company.sec import DEFAULT_UA, build_snapshot, resolve
    from meridian.store.factory import get_store

    ua = get_settings().sec_user_agent or DEFAULT_UA
    who = resolve(query, ua)
    cik = who["cik"]
    if not refresh and (cik in _bundled() or get_store().get("public_companies", f"{cik:010d}")):
        pc = get_public_company(cik)
        return {"cik": cik, "ticker": pc.subject.get("ticker"), "name": pc.subject.get("name"), "status": "EXISTS"}
    peer_list = None
    if peers:
        peer_list = [(resolve(t, ua)["cik"], t.upper()) for t in peers[:10]]
    base = json.loads(_bundled()[PRIMARY_CIK].read_text(encoding="utf-8"))
    snap = build_snapshot(ua, subject={"cik": cik, "ticker": who["ticker"], "name": who["name"]}, peers=peer_list,
                          peer_snapshot=None if peer_list else base)
    store = get_store()
    store.put_blob(f"public_company/CIK{cik:010d}.json", json.dumps(snap, ensure_ascii=False).encode("utf-8"))
    store.put("public_companies", {"cik_key": f"{cik:010d}", "cik": cik, "ticker": who["ticker"],
                                   "name": snap["subject"]["name"], "retrieved_at": snap["retrieved_at"], "added_by": user,
                                   "added_at": datetime.now(UTC).isoformat(), "checksum_sha256": snap["checksum_sha256"]})
    _CACHE.pop(cik, None)
    pc = get_public_company(cik)
    return {"cik": cik, "ticker": who["ticker"], "name": pc.subject["name"], "status": "ADDED",
            "latest_quarter": pc.latest_quarter, "facts": sum(len(v) for v in pc.facts.values()),
            "missing_concepts": snap["missing_concepts"]}
