"""v3.5 Value at Stake: the cross-company working-capital study (real SEC filings) and the forecast validation on real
invoices. Formulas are recomputed here from the filing values carried in each company's lineage."""

import json

import pytest
from fastapi.testclient import TestClient

from meridian.api.main import app
from meridian.benchmark import forecast_validation as FV
from meridian.benchmark import sources
from meridian.benchmark.report import render
from meridian.benchmark.study import DOMINANCE, MIN_PEERS, run_study, sector_of
from meridian.security.auth import DEMO_PASSWORD


@pytest.fixture(scope="module")
def study():
    return run_study()


def test_dataset_is_checksummed_and_tamper_evident(tmp_path):
    body = sources.load()
    assert body["companies"] and body["source"].startswith("SEC EDGAR")
    body["companies"][0]["revenue"]["val"] += 1
    p = tmp_path / "x.json"
    p.write_text(json.dumps(body), encoding="utf-8")
    with pytest.raises(ValueError, match="checksum"):
        sources.load(p)


def test_no_mixed_dates_and_full_fiscal_years():
    from datetime import date
    for c in sources.load()["companies"]:
        end = c["fiscal_year_end"]
        assert c["revenue"]["end"] == c["cogs"]["end"] == end
        assert c["receivables"]["end"] == c["inventory"]["end"] == c["payables"]["end"] == end
        for k in ("revenue", "cogs"):
            span = (date.fromisoformat(c[k]["end"]) - date.fromisoformat(c[k]["start"])).days
            assert 350 <= span <= 380
        assert c["revenue"]["val"] >= sources.MIN_REVENUE_USD


def test_formulas_recompute_from_filing_values(study):
    for c in study["companies"]:
        L = c["lineage"]
        rev, cogs = L["revenue"]["value"], L["cogs"]["value"]
        ar, inv, ap = L["receivables"]["value"], L["inventory"]["value"], L["payables"]["value"]
        assert c["dso"] == pytest.approx(ar / rev * 365, abs=0.051)
        assert c["dio"] == pytest.approx(inv / cogs * 365, abs=0.051)
        assert c["dpo"] == pytest.approx(ap / cogs * 365, abs=0.051)
        assert c["trade_working_capital_usd"] == ar + inv - ap
        assert all(L[k]["accession"] and L[k]["url"].startswith("https://www.sec.gov/Archives/") for k in L)


def test_opportunity_is_non_negative_and_follows_the_formula(study):
    for c in study["companies"]:
        b, op = c["benchmark"], c["opportunity"]["median"]
        exp_ar = 0 if "dso" in c["review_levers"] else max(0, c["dso"] - b["dso"]["median"]) * c["revenue_usd"] / 365
        exp_inv = 0 if "dio" in c["review_levers"] else max(0, c["dio"] - b["dio"]["median"]) * c["cogs_usd"] / 365
        assert op["receivables_usd"] == pytest.approx(exp_ar, abs=1)
        assert op["inventory_usd"] == pytest.approx(exp_inv, abs=1)
        for case in ("median", "top_quartile"):
            assert all(v >= 0 for v in c["opportunity"][case].values())
        # stretch case is never smaller than the conservative case
        assert c["opportunity"]["top_quartile"]["total_usd"] + 1 >= c["opportunity"]["median"]["total_usd"]


def test_totals_add_up_and_review_cash_is_kept_out_of_the_headline(study):
    m = study["summary"]["opportunity"]["median"]
    assert m["total_excl_payables_usd"] == sum(c["opportunity"]["median"]["total_excl_payables_usd"]
                                               for c in study["companies"])
    assert m["requires_segment_review_usd"] == sum(c["opportunity"]["median"]["requires_segment_review_usd"]
                                                   for c in study["companies"])
    tol = len(study["companies"])                                   # per-company rounding to whole dollars
    assert m["total_usd"] == pytest.approx(m["total_excl_payables_usd"] + m["payables_usd"], abs=tol)


def test_peer_groups_are_large_enough_and_dominant_companies_are_flagged(study):
    for c in study["companies"]:
        assert c["peer_group"]["companies"] >= MIN_PEERS
        if c["peer_group"]["revenue_share"] > DOMINANCE:
            assert set(c["review_levers"]) == {"dso", "dio", "dpo"}
            assert c["opportunity"]["median"]["total_usd"] == 0


def test_exclusions_are_explicit(study):
    assert study["universe"]["benchmarked"] + study["universe"]["excluded"] == study["universe"]["filers_with_all_items"]
    assert all(e["reason"] for e in study["excluded"])
    assert not any(c["sector"] in ("Financials", "Utilities") for c in study["companies"])


@pytest.mark.parametrize(("sic", "sector"), [(3531, "Industrial machinery"), (2834, "Pharmaceuticals & biotech"),
                                             (5331, "Retail"), (3674, "Semiconductors & components"),
                                             (6021, "Financials"), (None, None), (3721, "Aerospace & defense")])
def test_sector_mapping(sic, sector):
    assert sector_of(sic) == sector


def test_report_numbers_come_from_the_study(study):
    h = render()
    from meridian.benchmark.report import _b
    assert _b(study["summary"]["opportunity"]["median"]["total_excl_payables_usd"]) in h
    assert "nan" not in h.lower().replace("financ", "") and "None" not in h
    assert "<script" not in h                       # self-contained, static
    assert "Not investment advice" in h


# ------------------------------------------------------------------------------------------------ forecast validation
def test_validation_series_is_real_and_checksummed():
    body = FV.load()
    assert body["raw_rows"] > 1_000_000 and len(body["series"]) >= 100 and "CC BY 4.0" in body["source"]
    assert "Customer ID" not in json.dumps(body)


def test_forecasts_use_no_future_data():
    y = [r["net_gbp"] for r in FV.load()["series"]]
    t = 70
    for m in FV.METHODS:
        base = FV._forecast(y, t, m)
        poisoned = y[: t + 1] + [1e12] * (len(y) - t - 1)
        assert FV._forecast(poisoned, t, m) == base, m


def test_validation_reports_honest_comparison():
    r = FV.run_validation()
    w = {m: x["wape"] for m, x in r["methods"].items()}
    assert r["best_method"] == min(w, key=w.get)
    assert r["improvement_vs_naive_pct"] == pytest.approx(100 * (w["naive_last"] - w["seasonal_level"]) / w["naive_last"], abs=0.1)
    assert 0 < r["interval"]["observed_coverage"] <= 1 and r["interval"]["evaluated_points"] > 100
    assert r["origins"] >= 20


# ------------------------------------------------------------------------------------------------ API
@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        tok = c.post("/v1/auth/login", json={"username": "iris", "password": DEMO_PASSWORD}).json()["access_token"]
        c.headers["Authorization"] = f"Bearer {tok}"
        yield c


def test_api(client):
    s = client.get("/v1/benchmark/study?top=5").json()
    assert len(s["top_opportunities"]) == 5 and s["classification"] == "PUBLIC_REAL"
    r = client.get("/v1/benchmark/company/CAT").json()
    assert r["benchmarked"] and r["company"]["cik"] == 18230 and r["company"]["lineage"]["revenue"]["accession"]
    assert client.get("/v1/benchmark/company/18230").json()["company"]["cik"] == 18230
    assert client.get("/v1/benchmark/company/NOPE-XYZ").status_code == 404
    assert client.get("/v1/benchmark/study?top=0").status_code == 422
    rep = client.get("/v1/benchmark/report")
    assert rep.status_code == 200 and "attachment" in rep.headers["content-disposition"]
    assert client.get("/v1/benchmark/forecast-validation").json()["horizon_weeks"] == 13


def test_benchmark_endpoints_require_authentication():
    with TestClient(app) as c:
        for path in ("/v1/benchmark/study", "/v1/benchmark/company/CAT", "/v1/benchmark/report",
                     "/v1/benchmark/forecast-validation", "/v1/benchmark/companies"):
            assert c.get(path).status_code == 401, path
