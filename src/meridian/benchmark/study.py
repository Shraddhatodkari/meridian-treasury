"""Working-capital value-at-stake study across large US-listed companies (fiscal year 2025, SEC filings).

Method (the standard outside-in working-capital diagnostic):
  DSO = trade receivables / revenue × 365 · DIO = inventory / cost of revenue × 365 · DPO = payables / cost of revenue
  × 365 · CCC = DSO + DIO − DPO, all from ONE fiscal-year-end balance sheet and the fiscal year it closes.
  Peers = the finest SEC SIC level (4-digit industry, then 3-digit group, then broad sector) that has at least
  MIN_PEERS companies with plausible values — so a department store is compared with retailers like it first.
  Cash opportunity = the cash released if a company moved to its peer MEDIAN (conservative case) or peer TOP QUARTILE
  (stretch case) on each lever; a company already better than the benchmark contributes zero, never a negative.

Every figure is labelled: REPORTED (from a filing), CALCULATED (arithmetic on reported values) or ESTIMATED (the
opportunity, which rests on the assumption that peer performance is achievable). Limitations are listed in the output.
"""

from __future__ import annotations

import functools
import statistics

from meridian.benchmark.sources import DATASET, load

MIN_PEERS = 8
METHOD = ("Standard outside-in working-capital diagnostic. DSO = trade receivables / revenue × 365; DIO = inventory / "
          "cost of revenue × 365; DPO = payables / cost of revenue × 365; CCC = DSO + DIO − DPO — all from one "
          "fiscal-year-end balance sheet and the fiscal year it closes. Peers: the finest SEC SIC level (4-digit "
          "industry, then 3-digit group, then broad sector) with at least 8 companies with plausible values. Cash "
          "opportunity: cash released if a company moved to its peer median (conservative) or top quartile (stretch) "
          "on each lever; a company already better than the benchmark contributes zero, never a negative.")
PLAUSIBLE = {"dso": (0.0, 200.0), "dio": (0.0, 500.0), "dpo": (0.0, 300.0)}

# SIC → industry group. Specific ranges first: the first match wins.
SECTORS: list[tuple[int, int, str]] = [
    (1300, 1399, "Oil & gas production"), (1000, 1499, "Mining"), (1500, 1799, "Construction & engineering"),
    (2000, 2199, "Food, beverage & tobacco"), (2200, 2399, "Textiles & apparel"),
    (2400, 2799, "Wood, paper, furniture & printing"), (2830, 2836, "Pharmaceuticals & biotech"),
    (2840, 2844, "Household & personal products"), (2800, 2899, "Chemicals"), (2900, 2999, "Petroleum refining"),
    (3000, 3099, "Rubber & plastics"), (3100, 3199, "Textiles & apparel"), (3200, 3299, "Building materials"),
    (3300, 3399, "Metals"), (3400, 3499, "Fabricated metal products"), (3570, 3579, "Computer hardware"),
    (3500, 3599, "Industrial machinery"), (3670, 3679, "Semiconductors & components"),
    (3600, 3699, "Electrical equipment & electronics"), (3710, 3716, "Automotive"),
    (3720, 3729, "Aerospace & defense"), (3760, 3769, "Aerospace & defense"), (3700, 3799, "Transportation equipment"),
    (3840, 3851, "Medical devices"), (3800, 3899, "Instruments"), (3900, 3999, "Other manufacturing"),
    (4000, 4799, "Transportation & logistics"), (4800, 4899, "Telecom & media"), (4900, 4999, "Utilities"),
    (5000, 5199, "Wholesale distribution"), (5200, 5999, "Retail"), (6000, 6799, "Financials"),
    (7370, 7379, "Software & IT services"), (7000, 8999, "Services"),
]
EXCLUDED_SECTORS = {"Financials", "Utilities"}      # balance sheets not comparable on these levers
LIMITATIONS = [
    "Balances are fiscal-year-end snapshots; seasonal businesses can look better or worse than their average year.",
    "Receivables are total current trade receivables as tagged (AccountsReceivableNetCurrent); some issuers include "
    "non-trade items. Companies that tag only other concepts are not in the sample.",
    "Industry groups come from SEC SIC codes, which are broad: a group can mix different business models.",
    "Opportunities assume peer-median or top-quartile performance is achievable for this company — a hypothesis to "
    "test with management, not a forecast.",
    "Extending supplier payment terms moves cash from suppliers to the buyer. It should only be pursued with supplier "
    "protection (e.g. supply-chain finance or commercial-card programmes) and is shown separately.",
    "Where a company sits beyond its peers' quartile ± 3 × interquartile range on a lever, the gap is treated as a likely "
    "business-mix difference (e.g. a cloud or financing business inside a retailer): that cash is excluded from the "
    "headline and reported as 'requires segment-level review'.",
    "A company with more than half of its peer group's revenue (e.g. a giant among small peers) is not benchmarked "
    "against that group: its whole gap is reported as 'requires segment-level review'.",
    "Companies outside plausible ranges (DSO>200, DIO>500, DPO>300 days) are excluded from benchmarks and listed.",
]


def sector_of(sic: int | None) -> str | None:
    if sic is None:
        return None
    return next((name for lo, hi, name in SECTORS if lo <= sic <= hi), "Other")


def _q(values: list[float]) -> dict:
    v = sorted(values)
    q1, q2, q3 = statistics.quantiles(v, n=4, method="inclusive")
    return {"p25": round(q1, 1), "median": round(q2, 1), "p75": round(q3, 1), "n": len(v)}


def _fact(f: dict, cik: int) -> dict:
    return {"concept": f"us-gaap:{f['concept']}", "value": f["val"], "end": f["end"], "start": f.get("start"),
            "accession": f["accn"],
            "url": f"https://www.sec.gov/Archives/edgar/data/{cik}/{f['accn'].replace('-', '')}/"}


def company_metrics(row: dict) -> dict:
    rev, cogs = row["revenue"]["val"], row["cogs"]["val"]
    ar, inv, ap = row["receivables"]["val"], row["inventory"]["val"], row["payables"]["val"]
    if rev <= 0 or cogs <= 0:               # cannot compute days ratios: excluded downstream as implausible
        m = {"dso": float("nan"), "dio": float("nan"), "dpo": float("nan")}
    else:
        m = {"dso": ar / rev * 365, "dio": inv / cogs * 365, "dpo": ap / cogs * 365}
    m["ccc"] = m["dso"] + m["dio"] - m["dpo"]
    plausible = cogs > 0 and rev > 0 and cogs <= rev * 1.5 and all(m[k] == m[k] and
        PLAUSIBLE[k][0] <= m[k] <= PLAUSIBLE[k][1] for k in PLAUSIBLE)
    return {"cik": row["cik"], "name": row["name"], "tickers": row.get("tickers", []), "sic": row.get("sic"),
            "sic_description": row.get("sic_description"), "sector": sector_of(row.get("sic")),
            "fiscal_year_end": row["fiscal_year_end"],
            "revenue_usd": rev, "cogs_usd": cogs, "receivables_usd": ar, "inventory_usd": inv, "payables_usd": ap,
            "trade_working_capital_usd": ar + inv - ap,
            **{k: (round(v, 1) if v == v else None) for k, v in m.items()}, "plausible": plausible,
            "lineage": {k: _fact(row[k], row["cik"]) for k in ("revenue", "cogs", "receivables", "inventory", "payables")}}


DOMINANCE = 0.5  # a company above half of its peer group's revenue is not benchmarked against that group
FAR_OUT = 3.0     # Tukey "far out" fence: beyond quartile ± 3 × IQR the gap is more likely business mix than performance


def review_levers(c: dict, b: dict) -> list[str]:
    """Levers where the company is so far from its peers that the gap probably reflects a different business model
    (e.g. a cloud or financing arm inside a retailer). Their cash is NOT counted in the headline — it is reported
    separately as 'requires segment-level review'."""
    out = []
    for k, side in (("dso", "high"), ("dio", "high"), ("dpo", "low")):
        iqr = b[k]["p75"] - b[k]["p25"]
        if side == "high" and c[k] > b[k]["p75"] + FAR_OUT * iqr:
            out.append(k)
        if side == "low" and c[k] < b[k]["p25"] - FAR_OUT * iqr:
            out.append(k)
    return out


def _opportunity(c: dict, b: dict, case: str, review: list[str] | None = None) -> dict:
    lo = "median" if case == "median" else "p25"
    hi = "median" if case == "median" else "p75"
    gaps = {"dso": max(0.0, c["dso"] - b["dso"][lo]) * c["revenue_usd"] / 365,
            "dio": max(0.0, c["dio"] - b["dio"][lo]) * c["cogs_usd"] / 365,
            "dpo": max(0.0, b["dpo"][hi] - c["dpo"]) * c["cogs_usd"] / 365}
    held = {k: v for k, v in gaps.items() if k in (review or [])}
    ar, inv, ap = (0.0 if k in held else gaps[k] for k in ("dso", "dio", "dpo"))
    return {"receivables_usd": round(ar), "inventory_usd": round(inv), "payables_usd": round(ap),
            "total_usd": round(ar + inv + ap), "total_excl_payables_usd": round(ar + inv),
            "requires_segment_review_usd": round(sum(held.values()))}


@functools.lru_cache(maxsize=2)
def run_study(path: str = str(DATASET)) -> dict:
    from pathlib import Path

    data = load(Path(path))
    companies = [company_metrics(r) for r in data["companies"]]
    excluded: list[dict] = []
    ranked: list[dict] = []
    eligible: list[dict] = []
    for c in companies:
        reason = (None if c["sector"] and c["sector"] not in EXCLUDED_SECTORS else
                  f"sector {c['sector']} not comparable" if c["sector"] else "no SIC code")
        if reason is None and not c["plausible"]:
            reason = "values outside plausible range — review manually"
        if reason:
            excluded.append({"cik": c["cik"], "name": c["name"], "sector": c["sector"], "reason": reason,
                             **{k: c[k] for k in ("dso", "dio", "dpo")}})
            continue
        eligible.append(c)
    # Peer group = the finest SIC level with at least MIN_PEERS companies: 4-digit, then 3-digit, then sector.
    levels = [("SIC4", lambda c: f"{c['sic']:04d}"), ("SIC3", lambda c: f"{c['sic'] // 10:03d}x"),
              ("sector", lambda c: c["sector"])]
    members: dict[str, dict[str, list[dict]]] = {lv: {} for lv, _ in levels}
    for c in eligible:
        for lv, key in levels:
            members[lv].setdefault(key(c), []).append(c)
    bench_cache: dict[tuple, dict] = {}
    sectors_acc: dict[str, list[dict]] = {}
    for c in eligible:
        chosen = next(((lv, key(c)) for lv, key in levels if len(members[lv][key(c)]) >= MIN_PEERS), None)
        if chosen is None:
            excluded.append({"cik": c["cik"], "name": c["name"], "sector": c["sector"],
                             "reason": f"fewer than {MIN_PEERS} peers even at sector level",
                             **{k: c[k] for k in ("dso", "dio", "dpo")}})
            continue
        peers = members[chosen[0]][chosen[1]]
        if chosen not in bench_cache:
            bench_cache[chosen] = {k: _q([x[k] for x in peers]) for k in ("dso", "dio", "dpo", "ccc")}
        b = bench_cache[chosen]
        c["peer_group"] = {"level": chosen[0], "code": chosen[1], "companies": len(peers),
                           "label": c["sic_description"] if chosen[0] == "SIC4" else
                           (f"SIC {chosen[1]} group" if chosen[0] == "SIC3" else c["sector"])}
        c["benchmark"] = b
        c["review_levers"] = review_levers(c, b)
        share = c["revenue_usd"] / sum(x["revenue_usd"] for x in peers)
        c["peer_group"]["revenue_share"] = round(share, 3)
        if share > DOMINANCE:                # most of the peer group IS this company: peers are not representative
            c["review_levers"] = ["dso", "dio", "dpo"]
            c["review_reason"] = (f"{share:.0%} of its peer group's revenue — peers are much smaller and likely a "
                                  "different business mix; benchmark at segment level")
        elif c["review_levers"]:
            c["review_reason"] = "far outside peers (quartile ± 3 × IQR) on " + ", ".join(
                x.upper() for x in c["review_levers"])
        c["opportunity"] = {"median": _opportunity(c, b, "median", c["review_levers"]),
                            "top_quartile": _opportunity(c, b, "top", c["review_levers"])}
        c["percentile_ccc"] = round(100 * sum(1 for x in peers if x["ccc"] >= c["ccc"]) / len(peers))  # higher = better
        sectors_acc.setdefault(c["sector"], []).append(c)
        ranked.append(c)
    sectors = []
    for name, cs in sectors_acc.items():
        tot: dict[str, dict] = {"median": {}, "top_quartile": {}}
        for c in cs:
            for case in tot:
                for k, v in c["opportunity"][case].items():
                    tot[case][k] = tot[case].get(k, 0) + v
        sectors.append({"sector": name, "companies": len(cs), "revenue_usd": sum(c["revenue_usd"] for c in cs),
                        "trade_working_capital_usd": sum(c["trade_working_capital_usd"] for c in cs),
                        "benchmarks": {k: _q([c[k] for c in cs]) for k in ("dso", "dio", "dpo", "ccc")}
                        if len(cs) >= 4 else None,
                        "opportunity": tot})
    ranked.sort(key=lambda c: c["opportunity"]["median"]["total_excl_payables_usd"], reverse=True)
    for i, c in enumerate(ranked, 1):
        c["rank_excl_payables"] = i
    total = {case: {k: sum(s["opportunity"][case][k] for s in sectors) for k in sectors[0]["opportunity"][case]}
             for case in ("median", "top_quartile")} if sectors else {}
    return {
        "title": "Working-capital value at stake — large US-listed companies, fiscal year 2025",
        "source": data["source"], "retrieved_at": data["retrieved_at"], "checksum_sha256": data["checksum_sha256"],
        "fiscal_year": data["fiscal_year"], "classification": "PUBLIC_REAL",
        "basis": {"metrics": "CALCULATED from REPORTED filing values", "opportunity": "ESTIMATED (peer-based)"},
        "method": METHOD,
        "universe": {"filers_with_all_items": len(companies), "benchmarked": len(ranked), "sectors": len(sectors),
                     "excluded": len(excluded), "min_revenue_usd": data["min_revenue_usd"], "min_peers": MIN_PEERS},
        "summary": {"revenue_usd": sum(c["revenue_usd"] for c in ranked),
                    "trade_working_capital_usd": sum(c["trade_working_capital_usd"] for c in ranked),
                    "opportunity": total},
        "sectors": sorted(sectors, key=lambda s: s["opportunity"]["median"]["total_excl_payables_usd"], reverse=True),
        "companies": ranked, "excluded": excluded, "limitations": LIMITATIONS,
    }


def find_company(query: str) -> dict | None:
    q = query.strip().upper()
    s = run_study()
    for c in s["companies"] + s["excluded"]:
        if str(c["cik"]) == q.lstrip("0") or q in [t.upper() for t in c.get("tickers", [])] or c["name"].upper() == q:
            return c
    return None
