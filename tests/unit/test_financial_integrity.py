"""P0 financial-data integrity (v3.4): every bundled company's metrics are recomputed here independently of the
metric builder, from the raw SEC facts in the snapshot — formulas, signs, periods, date consistency, TTM, units,
staleness and invalid inputs. Nothing is mocked: the assertions run against the shipped SEC snapshots."""

from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from meridian.api.main import app
from meridian.public_company import analytics as A
from meridian.public_company import sec
from meridian.security.auth import DEMO_PASSWORD

CIKS = sorted(A._bundled())


def _co(cik):
    return A.get_public_company(cik)


def _v(M, k):
    m = M.get(k)
    return m["value"] if m else None


def _instant_dates(m) -> set[str]:
    facts = (m.get("lineage") or {}).get("facts") or []
    return {f["period"] for f in facts if "→" not in f["period"]}


@pytest.mark.parametrize("cik", CIKS)
def test_every_metric_is_labelled_and_numeric(cik):
    for k, m in _co(cik).metrics.items():
        assert m["basis"] in {"REPORTED", "CALCULATED", "ESTIMATED", "NOT_AVAILABLE"}, k
        if m["value"] is None:
            assert m["basis"] == "NOT_AVAILABLE" and m["note"], k      # missing data is explicit, never zero
        else:
            assert isinstance(m["value"], (int, float)) and m["value"] == m["value"], k     # not NaN
            assert m["lineage"]["facts"] or m["lineage"]["market_inputs"], k               # every number has a source


@pytest.mark.parametrize("cik", CIKS)
def test_no_silent_mixing_of_balance_sheet_dates(cik):
    """A CALCULATED/ESTIMATED figure built from instant facts at more than one date must say why (by definition,
    e.g. an average of two year-ends). Liquidity and its coverage must always be single-date."""
    M = _co(cik).metrics
    for k, m in M.items():
        if m["basis"] in ("CALCULATED", "ESTIMATED") and len(_instant_dates(m)) > 1:
            assert m.get("mixed_dates_reason"), f"{cik} {k} mixes {_instant_dates(m)} without a declared reason"
    for k in ("liquidity_fy", "liquidity_coverage"):
        if M[k]["value"] is not None:
            assert len(_instant_dates(M[k])) == 1, (cik, k, _instant_dates(M[k]))
            assert M[k]["as_of"] in M[k]["period"]


@pytest.mark.parametrize("cik", CIKS)
def test_formulas_reconcile(cik):
    M = _co(cik).metrics
    v = lambda k: _v(M, k)   # noqa: E731
    if v("ocf_ttm") is not None and v("capex_ttm") is not None:
        assert v("fcf_ttm") == v("ocf_ttm") - v("capex_ttm")
        assert v("capex_ttm") >= 0              # capex is a payment (positive); FCF subtracts it
    if v("operating_income_ttm") is not None and v("dna_ttm") is not None:
        assert v("ebitda_ttm") == v("operating_income_ttm") + v("dna_ttm")
    if v("total_debt") is not None and v("cash_fy") is not None:
        assert v("net_debt") == v("total_debt") - v("cash_fy")
    if v("st_obligations") is not None and v("repricing_100bps") is not None:
        assert v("repricing_100bps") == pytest.approx(v("st_obligations") * 0.01, rel=1e-9)
    if M["liquidity_fy"]["value"] is not None:
        refs = M["liquidity_fy"]["lineage"]["facts"]
        assert v("liquidity_fy") == sum(r["value"] for r in refs)          # cash + undrawn facilities, same date
        if v("liquidity_coverage") is not None:
            st = sum(r["value"] for r in M["liquidity_coverage"]["lineage"]["facts"][2:])
            assert v("liquidity_coverage") == pytest.approx(v("liquidity_fy") / st, abs=0.005)
    if v("revenue_ttm") and v("receivables_trade") is not None and v("dso") is not None:
        assert v("dso") == pytest.approx(v("receivables_trade") / v("revenue_ttm") * 365, abs=0.05)
    if v("cogs_ttm") and v("inventory") is not None and v("dio") is not None:
        assert v("dio") == pytest.approx(v("inventory") / v("cogs_ttm") * 365, abs=0.05)
    if v("cogs_ttm") and v("payables") is not None and v("dpo") is not None:
        assert v("dpo") == pytest.approx(v("payables") / v("cogs_ttm") * 365, abs=0.05)
    if None not in (v("dso"), v("dio"), v("dpo"), v("ccc")):
        assert v("ccc") == pytest.approx(v("dso") + v("dio") - v("dpo"), abs=0.15)
    if None not in (v("receivables_trade"), v("inventory"), v("payables"), v("trade_working_capital")):
        assert v("trade_working_capital") == v("receivables_trade") + v("inventory") - v("payables")


@pytest.mark.parametrize("cik", CIKS)
def test_ttm_is_four_consecutive_quarters_and_ties_to_annual_plus_ytd(cik):
    """Independent check from raw facts: TTM = latest FY + current YTD − prior-year YTD. Prior-year periods are matched
    within ±7 days so 52/53-week fiscal years (e.g. Apple) are covered too. Every company with a mid-year TTM is checked."""
    d = date.fromisoformat
    c = _co(cik)
    checked = 0
    for key in ("revenue", "ocf", "capex", "cogs"):
        t = c.ttm(key)
        if not t:
            continue
        q = [d(x) for x in t["quarters"]]
        assert len(q) == 4 and all(80 <= (b - a).days <= 100 for a, b in zip(q, q[1:], strict=False))
        facts = [f for f in c.facts.get(key, []) if f.get("start")]
        span = lambda f: (d(f["end"]) - d(f["start"])).days  # noqa: E731
        cur = [f for f in facts if f["end"] == t["end"]]
        if not cur or any(span(f) > 330 for f in cur):
            continue                                                    # TTM ends at FY end: nothing to bridge
        ytd = max(cur, key=span)
        fy = max((f for f in facts if span(f) > 330 and d(f["end"]) <= d(ytd["start"]) + timedelta(days=7)),
                 key=lambda f: f["end"])
        prior = [f for f in facts if abs((d(f["start"]) - d(fy["start"])).days) <= 7
                 and abs((d(f["end"]) - d(ytd["end"])).days + 364) <= 7]
        assert prior, (cik, key, "prior-year YTD missing")
        assert t["value"] == fy["val"] + ytd["val"] - prior[0]["val"], (cik, key)
        checked += 1
    assert checked or all(not c.ttm(k) for k in ("revenue", "ocf"))


def test_extract_ignores_non_usd_units_non_periodic_forms_and_keeps_latest_restatement():
    fact = lambda val, filed, form="10-Q", end="2026-06-30": {  # noqa: E731
        "end": end, "val": val, "form": form, "accn": f"a-{filed}", "filed": filed, "fy": 2026, "fp": "Q2"}
    cf = {"facts": {"us-gaap": {"CashAndCashEquivalentsAtCarryingValue": {"units": {
        "USD": [fact(100, "2026-08-01"), fact(110, "2026-11-01"), fact(999, "2026-08-02", form="8-K")],
        "EUR": [fact(5, "2026-08-01")]}}}}}
    out = sec.extract(cf)
    cash = out["cash"]
    assert [f["val"] for f in cash] == [110]                # restatement wins, EUR and 8-K ignored
    assert cash[0]["accn"] == "a-2026-11-01"


def test_staleness_is_flagged_and_propagates_to_provenance():
    c = _co(18230)
    fresh = c.freshness(as_of=date.fromisoformat(c.latest_quarter))
    assert fresh["stale"] is False and fresh["age_days"] == 0 and fresh["warning"] is None
    old = c.freshness(as_of=date.fromisoformat(c.latest_quarter).replace(year=date.fromisoformat(c.latest_quarter).year + 1))
    assert old["stale"] is True and old["warning"].startswith("STALE")
    ov = c.overview()
    assert "freshness" in ov and ov["freshness"]["latest_period"] == c.latest_quarter
    cf = next(p for p in c.provenance() if p.get("issues") is not None and "validation" in p)
    if ov["freshness"]["stale"]:
        assert cf["validation"] == "PASSED_WITH_WARNINGS" and any(i.startswith("STALE") for i in cf["issues"])


def test_recommendation_shortfall_and_share_use_same_date_figures():
    from meridian.public_company.recommendations import evaluate
    for cik in CIKS:
        c = _co(cik)
        M = c.metrics
        out = evaluate(c, "RUN-TEST")
        ids = [a["action_id"] if isinstance(a, dict) else a.action_id for a in out["actions"]]
        assert len(ids) == len(set(ids))                       # action ids are unique within a run
        if M["liquidity_coverage"]["value"] is not None:
            implied_st = M["liquidity_fy"]["value"] / M["liquidity_coverage"]["value"]
            st_ref = sum(x["value"] for x in M["liquidity_coverage"]["lineage"]["facts"][2:])
            assert implied_st == pytest.approx(st_ref, rel=0.005)


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as cl:
        tok = cl.post("/v1/auth/login", json={"username": "ana", "password": DEMO_PASSWORD}).json()["access_token"]
        cl.headers["Authorization"] = f"Bearer {tok}"
        yield cl


@pytest.mark.parametrize("q", ["revenue_change_pct=500", "dso_change_days=-1000", "rate_shock_bps=99999",
                               "spread_shock_bps=nan", "revenue_change_pct=abc", "dio_change_days=inf"])
def test_invalid_scenario_inputs_are_rejected(client, q):
    assert client.get(f"/v1/public/scenario?{q}&cik=18230").status_code == 422


def test_unknown_company_is_not_fabricated(client):
    r = client.get("/v1/public/overview?cik=999999999")
    assert r.status_code in (404, 422)


def test_ttm_survives_recast_prior_periods():
    """Honeywell recast 2025 periods after a spin-off (H1-2025 cost of revenue: $12.366B as first filed, $11.121B
    as recast in the 2026 10-Q). Summing derived quarters mixed the two bases and understated TTM cost of revenue by
    $1.25B; the annual bridge uses the recast comparatives and the gap is disclosed on the metric."""
    m = _co(773840).metrics["cogs_ttm"]
    assert m["value"] == 23_613_000_000 + 11_670_000_000 - 11_121_000_000
    assert "recast" in (m["note"] or "")


def test_recommendation_evidence_lists_only_available_metrics():
    from meridian.public_company.recommendations import evaluate
    for cik in CIKS:
        out = evaluate(_co(cik), "RUN-EV")
        for r in out["recommendations"]:
            for e in r.get("evidence") or []:
                if isinstance(e, dict) and e.get("type") == "metric":
                    assert e.get("value") is not None, (cik, r["title"], e["id"])


def test_policy_arithmetic_uses_unrounded_figures():
    """The displayed coverage is rounded (1.55x); the PC-2.1 share must come from the unrounded same-date debt
    (Caterpillar: $12.634B / $19.638B = 64 %, not 1 / 1.55 = 65 %)."""
    from meridian.public_company.recommendations import evaluate
    c = _co(18230)
    M = c.metrics
    share = M["liquidity_coverage"]["debt_due_usd"] / M["liquidity_fy"]["value"]
    rule = next(r for r in evaluate(c, "RUN-R")["rules"] if r["rule"].startswith("PC-2.1"))
    assert f"{share:.0%}" in rule["test"] and round(share, 2) == 0.64


def test_scenario_impacts_add_up_and_signs_are_labelled():
    """Every scenario line is a free-cash-flow effect (positive = cash released). The lines must add up to the FCF
    change, and working-capital lines must say which direction they mean."""
    for cik in CIKS:
        s = _co(cik).scenario(-15, 10, 15, 100, 150)
        if not s["available"]:
            continue
        lines = {i["label"]: i["value_usd"] for i in s["impacts"]}
        total = lines.pop("Change in free cash flow (first year)")
        assert abs(sum(lines.values()) - total) <= 3, cik
        assert all("released (+)" in k for k in lines if k.startswith(("Receivables", "Inventory")))
    # Caterpillar: −15% revenue shrinks receivables by more than +10 days adds → cash released (positive)
    cat = {i["label"]: i["value_usd"] for i in _co(18230).scenario(-15, 10, 15, 100, 150)["impacts"]}
    assert cat["Receivables: cash released (+) / absorbed (−)"] > 0
