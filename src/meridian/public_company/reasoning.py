"""Deterministic offline reasoners and executive Q&A for the public-company case.

Used when no LLM is configured (AI OFFLINE / DETERMINISTIC) and as the grounded fallback when Claude is configured.
Every figure is formatted directly from tool evidence, so the numeric-grounding guardrail can verify it.
"""

from __future__ import annotations

import re

from meridian.agents.schemas import AgentFinding, CFOBriefing
from meridian.public_company.analytics import NA, PublicCompany, _usd


def _p(x, d: int = 2) -> str:
    return f"{x:.{d}%}" if x is not None else "n/a"


def _x(x) -> str:
    return f"{x}x" if x is not None else "n/a"


def _d(x) -> str:
    return f"{x}" if x is not None else "n/a"


def _dd(ev: dict) -> str:
    d = ev.get("debt_date")
    return d if d and d != "STALE" else "n/a (debt facts stale)"


def _liq(liq, st, cov, cash) -> str:
    """One honest sentence on liquidity vs 12-month debt; never prints 'n/a covers'."""
    if st is None:
        return "Debt due within 12 months is not available from public data"
    if liq is not None and cov is not None:
        return f"Liquidity of {_usd(liq)} covers {_usd(st)} of debt due within 12 months {cov}x"
    return (f"Debt due within 12 months is {_usd(st)} against cash of {_usd(cash)}; undrawn committed facilities are not "
            f"tagged by this issuer, so liquidity coverage is not available from public data")


def _m(ev: dict, k: str):
    return (ev.get("metrics") or {}).get(k, {}).get("value")


def data_intelligence(ev: dict) -> AgentFinding:
    prov = ev["provenance"]
    warn = [p for p in prov if p.get("validation") != "PASSED"]
    return AgentFinding(
        agent="data_intelligence_agent",
        headline=f"{ev['company']}: {len(prov)} public sources validated for decision use; latest filing period "
                 f"{ev['latest_quarter']}, debt detail as of {_dd(ev)}",
        key_points=[f"{p['source_id']} — {p['system']} — as of {p.get('as_of')} — {p.get('validation')}" for p in prov],
        risks=[f"{p['source_id']}: {'; '.join(p.get('issues') or [])}" for p in warn if p.get("issues")],
        recommendations=[], confidence=0.9 if not warn else 0.8,
        limitations=[f"{g['capability']}: {g['status']}" for g in ev["data_gaps"]][:5])


def forecast(ev: dict) -> AgentFinding:
    o = ev["outlook"]
    how = "seasonal run-rate" if ev.get("method_selected", "seasonal") == "seasonal" else \
        "no-change outlook (better back-test than seasonal)"
    out = (f"; {how} puts cash at {_usd(o[-1]['p50_usd'])} by {o[-1]['quarter']} (range "
           f"{_usd(o[-1]['p10_usd'])}–{_usd(o[-1]['p90_usd'])})") if o else "; cash outlook not available from public data"
    return AgentFinding(
        agent="forecast_agent",
        headline=f"FCF proxy {_usd(_m(ev, 'fcf_ttm'))} TTM on operating cash flow {_usd(_m(ev, 'ocf_ttm'))}{out}",
        key_points=[f"Shareholder returns TTM {_usd(_m(ev, 'shareholder_returns_ttm'))} vs FCF TTM {_usd(_m(ev, 'fcf_ttm'))}",
                    f"Back-test MAPE of the seasonal method on reported quarters: {_p(ev['backtest_mape'], 1)}"],
        risks=["Run-rate outlook ignores unreported buybacks, M&A and debt issuance"],
        confidence=0.7, limitations=[ev["method"]])


def liquidity(ev: dict) -> AgentFinding:
    return AgentFinding(
        agent="liquidity_agent",
        headline=f"{_liq(_m(ev, 'liquidity_fy'), _m(ev, 'st_obligations'), _m(ev, 'liquidity_coverage'), _m(ev, 'cash'))}; "
                 f"total debt {_usd(_m(ev, 'total_debt'))}, net debt {_usd(_m(ev, 'net_debt'))} (as of {_dd(ev)})",
        key_points=[f"Cash {_usd(_m(ev, 'cash'))} at the latest quarter; undrawn committed facilities "
                    f"{_usd(_m(ev, 'credit_available'))}",
                    f"Gross leverage {_x(_m(ev, 'gross_leverage'))}, net leverage {_x(_m(ev, 'net_leverage'))} on EBITDA proxy "
                    f"{_usd(_m(ev, 'ebitda_fy'))}", f"Interest cover proxy {_x(_m(ev, 'interest_cover'))}"],
        risks=[f"{_usd(_m(ev, 'maturity_y1'))} of long-term debt matures within 12 months plus "
               f"{_usd(_m(ev, 'short_term_borrowings'))} of short-term borrowings"],
        policy_refs=["PC-1.1", "PC-2.1"], confidence=0.85,
        limitations=[*(["Consolidated debt includes captive-finance funding of finance receivables"]
                       if ev.get("captive_finance") else []),
                     f"Debt as of {_dd(ev)}: the latest date with as-filed debt facts in the companyfacts API"])


def risk(ev: dict) -> AgentFinding:
    r = ev["rates"]
    return AgentFinding(
        agent="risk_agent",
        headline=f"+100 bps on debt due within 12 months costs {_usd(_m(ev, 'repricing_100bps'))} a year"
                 + (f"; refinancing 12-month maturities at today's market adds {_usd(_m(ev, 'refi_cost_delta'))} a year"
                    if _m(ev, 'refi_cost_delta') is not None else
                    "; refinancing cost at market is not available (needs Single-A OAS and implied cost of debt)"),
        key_points=[f"Implied average cash cost of debt {_p(_m(ev, 'avg_cost_of_debt'))}",
                    f"Market: SOFR {_d(r['sofr_pct'])}% ({r['sofr_date']}), UST 5Y {_d(r['ust5_pct'])}%, Single-A OAS "
                    f"{_d(r['a_oas_pct'])}%, BBB OAS {_d(r['bbb_oas_pct'])}%",
                    f"Derivative notional disclosed {_usd(_m(ev, 'derivative_notional'))}"],
        risks=["Fixed/floating mix and hedge designations are not available as structured public data"],
        policy_refs=["PC-5.1"], confidence=0.7,
        limitations=["Single-A index spread is a proxy for the issuer's own curve"])


def working_capital(ev: dict) -> AgentFinding:
    b = ev["bridge"]
    p = ev["peers"]
    return AgentFinding(
        agent="working_capital_agent",
        headline=f"Trade working capital {_usd(_m(ev, 'trade_working_capital'))}; CCC {_d(_m(ev, 'ccc'))} days "
                 f"(DSO {_d(_m(ev, 'dso'))}, DIO {_d(_m(ev, 'dio'))}, DPO {_d(_m(ev, 'dpo'))})",
        key_points=[f"Year on year ({b['from']} → {b['to']}): trade working capital {_usd(b['trade_wc_change_usd'])}, "
                    f"CCC {b['ccc_change']:+} days" if b.get("available") else "YoY bridge not available",
                    *[f"{c['component']}: {c['days_change']:+} days, {_usd(c['usd_change'])}" for c in b.get("components", [])],
                    f"Peer medians (FY 10-K): DSO {p['median']['dso_days']}, DIO {p['median']['dio_days']}, DPO "
                    f"{p['median']['dpo_days']} days — context only"],
        risks=(["Receivables include dealer/finance receivables; revenue includes the captive-finance business"]
               if ev.get("captive_finance") else
               ["Customer terms, ageing and concentration are private and not visible in the filings"]),
        policy_refs=["PC-3.1", "PC-3.2"], confidence=0.75, limitations=[p["caveat"]])


def scenario(ev: dict) -> AgentFinding:
    sc = [x for x in ev["scenarios"] if x.get("fcf_scenario_usd") is not None]
    if not sc:
        return AgentFinding(agent="scenario_agent", headline="Scenarios not available: required public inputs are missing",
                            confidence=0.5, limitations=["Revenue, EBITDA or FCF not derivable from the filings"])
    worst = min(sc, key=lambda s: s["fcf_scenario_usd"] - s["fcf_base_usd"])
    return AgentFinding(
        agent="scenario_agent",
        headline=f"Worst single scenario for free cash flow: {worst['name']} "
                 f"({_usd(worst['fcf_scenario_usd'] - worst['fcf_base_usd'])} in year one); liquidity coverage "
                 f"{_x(worst['liquidity_coverage_scenario'])}",
        key_points=[f"{s['name']}: FCF {_usd(s['fcf_scenario_usd'])}, coverage {_x(s['liquidity_coverage_scenario'])}, "
                    f"gross leverage {_x(s['gross_leverage_scenario'])}" for s in sc],
        confidence=0.7, limitations=["Constant-margin and pro-rata working-capital assumptions; no management action"])


def decision(ev: dict) -> AgentFinding:
    recs = ev["recommendations"]
    return AgentFinding(
        agent="decision_agent",
        headline=f"{len(recs)} governed recommendation{'' if len(recs) == 1 else 's'} from public evidence; "
                 f"{sum(r['triggered'] for r in ev['rules'])} of {len(ev['rules'])} policy rules triggered",
        key_points=[f"{r['short_id']} {r['title']} — approval {r['approval_required']}" for r in recs]
        + [f"Not triggered: {r['rule']} ({r['test']})" for r in ev["rules"] if not r["triggered"]],
        confidence=0.85, limitations=["Recommendations are decision mandates; none moves money (PC-6.1)"])


def briefing(ev: dict) -> CFOBriefing:
    M = ev["metrics"]

    def v(k):
        return (M.get(k) or {}).get("value")

    recs = ev["recommendations"]
    return CFOBriefing(
        title=f"{ev['company']} — liquidity & working-capital review from public filings (data to {ev['latest_quarter']})",
        executive_summary=(f"{_liq(v('liquidity_fy'), v('st_obligations'), v('liquidity_coverage'), v('cash'))}. "
                           f"Free cash flow proxy is {_usd(v('fcf_ttm'))} TTM against "
                           f"{_usd(v('shareholder_returns_ttm'))} of shareholder returns. Trade working capital is "
                           f"{_usd(v('trade_working_capital'))} with DSO at {_d(v('dso'))} days. "
                           + ("No recommendation needs a decision." if not recs else
                              f"{len(recs)} recommendation needs a decision." if len(recs) == 1
                              else f"{len(recs)} recommendations need a decision.")),
        key_numbers=[f"Cash {_usd(v('cash'))} ({ev['latest_quarter']})", f"Total debt {_usd(v('total_debt'))}, net debt "
                     f"{_usd(v('net_debt'))} ({_dd(ev)})",
                     f"Debt due within 12 months {_usd(v('st_obligations'))}",
                     f"+100 bps repricing cost {_usd(v('repricing_100bps'))} a year (ESTIMATED)",
                     f"Revenue TTM {_usd(v('revenue_ttm'))}; EBITDA proxy TTM {_usd(v('ebitda_ttm'))}"],
        decisions_required=[f"{r['short_id']}: {r['title']} — {r['approval_required']} approval" for r in recs]
        or ["None — no policy rule was triggered by the evidence, so no decision is required"],
        risks=[*(["Consolidated figures include captive-finance debt and receivables"] if ev.get("captive_finance") else []),
               "Hedge positions and fixed/floating mix are not available from public data"],
        citations=sorted({p for r in recs for p in r["policy_refs"]}))


# ------------------------------------------------------------------------------------------------ executive Q&A
TOPICS = {
    "liquidity": "How much liquidity is available and is it enough for debt coming due?",
    "debt": "What is the debt and refinancing profile?",
    "working_capital": "What is driving working capital?",
    "rates": "What is our interest-rate and credit-spread exposure?",
    "cash_flow": "How much free cash flow is there and how is it used?",
    "peers": "How does working capital compare with peers?",
    "scenarios": "What happens under a downturn or rate shock?",
    "data": "What data is this based on and what is not available?",
}
_KW = [("data", r"source|data|lineage|real|synthetic|filing"), ("peers", r"peer|benchmark|compare|competitor"),
       ("scenarios", r"what if|scenario|downturn|shock|stress|recession"),
       ("rates", r"rate|sofr|spread|hedg|interest"),
       ("cash_flow", r"free cash|fcf|cash flow|buyback|repurchase|dividend|capital allocation"),
       ("liquidity", r"liquidity|coverage|enough"), ("debt", r"debt|refinanc|maturit|leverage|borrow"),
       ("working_capital", r"dso|dio|dpo|receivable|inventor|payable|working capital|ccc"),
       ("liquidity", r"cash")]


def classify(q: str) -> str:
    q = (q or "").lower()
    return next((t for t, rx in _KW if re.search(rx, q)), "liquidity")


def answer(pc: PublicCompany, question: str, policy_version: str) -> dict:
    t = classify(question)
    M = pc.metrics
    v = lambda k: (M.get(k) or {}).get("value")  # noqa: E731
    srcs = [{"source_id": p["source_id"], "system": p["system"], "as_of": p.get("as_of"), "classification": "PUBLIC_REAL"}
            for p in pc.provenance()]

    def imp(label, k, basis=None):
        return {"label": label, "value_usd": round(v(k)) if v(k) is not None else None,
                "basis": basis or M.get(k, {}).get("basis", "NOT_AVAILABLE"), "formula": M.get(k, {}).get("formula") or NA}

    from meridian.governance.policy import load_policy

    pcy = load_policy().public_company
    floor = pcy.min_liquidity_coverage
    if t == "liquidity":
        cov = v("liquidity_coverage")
        vs = ("— no coverage ratio can be computed from public data" if cov is None else
              f"— {'above' if cov >= floor else 'BELOW'} the {floor}x reference minimum (PC-1.1)")
        concl = (f"Liquidity is {_usd(v('liquidity_fy'))} (cash plus undrawn committed facilities, {pc.debt_date}), covering "
                 f"{_usd(v('st_obligations'))} of debt due within 12 months {_x(cov)} {vs}. Cash at {pc.latest_quarter} "
                 f"is {_usd(v('cash'))}.")
        impact = [imp("Liquidity", "liquidity_fy"), imp("Debt due within 12 months", "st_obligations"),
                  imp("Cash (latest quarter)", "cash")]
        reasoning = ["Liquidity = cash + remaining capacity of committed credit facilities (10-K)",
                     "Debt due ≤12m = short-term borrowings + long-term maturities in the next 12 months",
                     "Coverage compared with the PC-1.1 reference threshold"]
        mets, pol = ["liquidity_fy", "st_obligations", "liquidity_coverage", "cash"], ["PC-1.1"]
    elif t == "debt":
        mat = pc.maturities()["rows"]
        concl = (f"Total debt is {_usd(v('total_debt'))} (net {_usd(v('net_debt'))}) at {pc.debt_date}; "
                 f"{_usd(mat[0]['amount_usd'])} of long-term debt matures in {mat[0]['bucket']} and "
                 f"{_usd(mat[1]['amount_usd'])} in {mat[1]['bucket']}. Gross leverage {_x(v('gross_leverage'))} on the EBITDA proxy.")
        impact = [imp("Total debt", "total_debt"), imp("Net debt", "net_debt"), imp("Debt due within 12 months", "st_obligations")]
        reasoning = ["Maturities from the ASC 470 five-year schedule in the 10-K", "Leverage on consolidated EBITDA proxy"]
        mets, pol = ["total_debt", "net_debt", "gross_leverage", "maturity_y1"], ["PC-2.1"]
    elif t == "working_capital":
        b = pc.wc_bridge()
        yoy = (f" Year on year the CCC moved {b['ccc_change']:+} days and trade working capital changed by "
               f"{_usd(b['trade_wc_change_usd'])}; receivables moved {b['components'][0]['days_change']:+} DSO days."
               if b.get("available") else " A year-on-year bridge is not available from the filed facts.")
        concl = (f"Trade working capital is {_usd(v('trade_working_capital'))}: DSO {_d(v('dso'))}, DIO {_d(v('dio'))}, "
                 f"DPO {_d(v('dpo'))} days (CCC {_d(v('ccc'))}).{yoy}")
        impact = [imp("Trade working capital", "trade_working_capital"), imp("Trade receivables", "receivables_trade"),
                  imp("Inventories", "inventory")]
        reasoning = ["Days use trailing-twelve-month revenue and COGS",
                     "Bridge compares the latest quarter with the same quarter a year earlier"]
        mets, pol = ["dso", "dio", "dpo", "ccc", "trade_working_capital"], ["PC-3.1", "PC-3.2"]
    elif t == "rates":
        r = pc.rates
        concl = (f"A 100 bp rise on the {_usd(v('st_obligations'))} of debt due within 12 months costs about "
                 f"{_usd(v('repricing_100bps'))} a year; refinancing 12-month maturities at UST 5Y {r['ust5_pct']}% + "
                 f"Single-A OAS {r['a_oas_pct']}% would add about {_usd(v('refi_cost_delta'))} a year versus the implied "
                 f"{_p(v('avg_cost_of_debt'))} average cost.")
        impact = [imp("+100 bps repricing cost (annual)", "repricing_100bps"),
                  imp("Refinancing cost change (annual)", "refi_cost_delta")]
        reasoning = ["Implied cost = cash interest paid / average total debt",
                     "New-issue rate = UST 5Y + Single-A OAS (stated assumption)"]
        mets, pol = ["repricing_100bps", "refi_cost_delta", "avg_cost_of_debt"], ["PC-5.1"]
    elif t == "cash_flow":
        concl = (f"Free cash flow proxy is {_usd(v('fcf_ttm'))} TTM (operating cash flow {_usd(v('ocf_ttm'))} less capex "
                 f"{_usd(v('capex_ttm'))}); shareholder returns were {_usd(v('shareholder_returns_ttm'))} "
                 f"(dividends {_usd(v('dividends_ttm'))}, repurchases {_usd(v('buybacks_ttm'))}).")
        impact = [imp("FCF proxy (TTM)", "fcf_ttm"), imp("Shareholder returns (TTM)", "shareholder_returns_ttm")]
        reasoning = ["TTM = last four quarters; cash-flow quarters derived as YTD differences"]
        mets, pol = ["fcf_ttm", "ocf_ttm", "capex_ttm", "shareholder_returns_ttm"], ["PC-4.1"]
    elif t == "peers":
        p = pc.peers()
        concl = (f"On FY 10-K data, {pc.subject['name']} runs DSO {p['subject']['dso_days']}, DIO {p['subject']['dio_days']}, "
                 f"DPO {p['subject']['dpo_days']} days versus peer medians of {p['median']['dso_days']}, "
                 f"{p['median']['dio_days']} and {p['median']['dpo_days']} days. {p['caveat']}")
        impact, reasoning = [], ["Same formula applied to every filer's latest 10-K"]
        mets, pol = ["dso", "dio", "dpo"], ["PC-3.2"]
    elif t == "scenarios":
        sc = {s["name"]: s for s in pc.scenarios()}
        c = sc["Combined downturn"]
        if not c.get("available", True):
            c = {**c, "fcf_scenario_usd": None, "fcf_base_usd": None, "impacts": []}
        concl = (f"In the combined downturn (revenue −15%, DSO +10, DIO +15 days, +100 bps, +150 bps spread) first-year FCF "
                 f"falls to {_usd(c['fcf_scenario_usd'])} from {_usd(c['fcf_base_usd'])}; liquidity coverage moves to "
                 f"{_x(c['liquidity_coverage_scenario'])} and gross leverage to {_x(c['gross_leverage_scenario'])}.")
        impact = [{"label": i["label"], "value_usd": i["value_usd"], "basis": "SCENARIO", "formula": ""} for i in c["impacts"]]
        reasoning = c["assumptions"]
        mets, pol = ["fcf_ttm", "liquidity_coverage", "gross_leverage"], ["PC-1.1"]
    else:
        concl = (f"All company figures come from {pc.subject['name']}'s SEC XBRL filings (latest period {pc.latest_quarter}) "
                 "and market data from the NY Fed, U.S. Treasury and FRED. Bank accounts, invoices, payments and entity cash "
                 f"are {NA} and require client connectors.")
        impact, reasoning, mets, pol = [], [f"{g['capability']}: {g['status']}" for g in pc.data_gaps()], [], ["PC-6.2"]
    return {"topic": t, "question": question or TOPICS[t], "headline": concl, "conclusion": concl,
            "financial_impact": impact, "reasoning": reasoning, "sources": srcs, "metrics": mets, "policy_refs": pol,
            "evidence": {"policy_refs": pol, "policy_version": policy_version, "data_classification": "PUBLIC_REAL",
                         "policy_inputs": pcy.model_dump(), "wc_bridge": pc.wc_bridge() if t == "working_capital" else None},
            "confidence": "HIGH" if t in {"liquidity", "debt", "working_capital", "cash_flow", "data"} else "MEDIUM",
            "confidence_reasons": ["as-filed SEC facts", "deterministic calculation"],
            "assumptions": [a for k in mets for a in (M.get(k) or {}).get("assumptions", [])],
            "limitations": [n for k in mets if (n := (M.get(k) or {}).get("note"))][:4]}
