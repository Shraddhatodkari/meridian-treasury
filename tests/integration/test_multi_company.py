"""Multi-company support: any SEC-reporting company can be added by ticker or CIK and analysed with the same governed
pipeline. Network access is mocked; the bundled snapshots are as-filed SEC data."""

import copy
import json

import pytest
from fastapi.testclient import TestClient

from meridian.api.main import app
from meridian.public_company import analytics as A
from meridian.public_company import sec
from meridian.public_company.reasoning import answer
from meridian.public_company.recommendations import evaluate
from meridian.security.auth import DEMO_PASSWORD

BUNDLED = sorted(A._bundled())


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="module")
def H(client):
    out = {}
    for u in ("ana", "carla", "iris"):
        r = client.post("/v1/auth/login", json={"username": u, "password": DEMO_PASSWORD})
        out[u] = {"Authorization": f"Bearer {r.json()['access_token']}"}
    return out


def test_several_real_companies_are_bundled():
    assert len(BUNDLED) >= 5 and A.PRIMARY_CIK in BUNDLED


@pytest.mark.parametrize("cik", BUNDLED)
def test_every_bundled_company_is_traceable_and_does_not_crash(cik):
    pc = A.get_public_company(cik)
    assert pc.cik == cik and pc.subject["name"]
    for key, facts in pc.facts.items():
        for f in facts:
            assert f["form"] in {"10-K", "10-Q"} and f["accn"] and f["filed"] and f["concept"], key
    for mid, m in pc.metrics.items():
        if m["value"] is None:
            assert m["classification"] == "NOT_AVAILABLE" and m["note"], mid
            continue
        L = m["lineage"]
        assert m["classification"] == "PUBLIC_REAL" and m["basis"] in {"REPORTED", "CALCULATED", "ESTIMATED"}, mid
        assert L["source"].startswith("SEC EDGAR XBRL") and L["filings"] and L["facts"] and L["result"] == m["value"], mid
    pc.scenarios()
    pc.forecast()
    pc.peers()
    pc.data_gaps()
    r = evaluate(pc, "T")
    assert len(r["rules"]) == 5 and all(x.get("rec_id") for x in r["recommendations"])
    for q in ("What is our liquidity position?", "How is working capital trending?", "What about debt and rates?"):
        a = answer(pc, q, "test")
        assert a and "None" not in str(a.get("answer", ""))


def test_add_company_validation_and_unknown(client, H):
    assert client.post("/v1/companies", json={"query": "honeywell!"}, headers=H["ana"]).status_code == 422
    assert client.post("/v1/companies", json={"query": "KO"}, headers=H["iris"]).status_code in (401, 403)


def test_add_unknown_ticker_404(client, H, monkeypatch):
    def nope(q, ua=None, **k):
        raise LookupError(f"{q} is not an SEC-registered ticker or CIK")
    monkeypatch.setattr(sec, "resolve", nope)
    assert client.post("/v1/companies", json={"query": "ZZZZZZ"}, headers=H["ana"]).status_code == 404


def test_add_new_company_without_network_and_analyse(client, H, monkeypatch):
    src = json.loads(A._bundled()[315189].read_text(encoding="utf-8"))
    new_cik = 999001
    monkeypatch.setattr(sec, "resolve", lambda q, ua=None, **k: {"cik": new_cik, "ticker": "TSTX", "name": "Test Co"})

    def fake_build(ua, subject=None, peers=None, peer_snapshot=None):
        s = copy.deepcopy(src)
        s["subject"] = {**s["subject"], **subject, "captive_finance": False}
        s.setdefault("missing_concepts", [])
        return s
    monkeypatch.setattr(sec, "build_snapshot", fake_build)
    r = client.post("/v1/companies", json={"query": "TSTX"}, headers=H["ana"])
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "ADDED" and r.json()["cik"] == new_cik
    assert client.post("/v1/companies", json={"query": "TSTX"}, headers=H["ana"]).json()["status"] == "EXISTS"
    ciks = [c["cik"] for c in client.get("/v1/companies", headers=H["ana"]).json()]
    assert new_cik in ciks
    cases = client.get("/v1/cases", headers=H["iris"]).json()
    assert any(c.get("cik") == new_cik for c in cases)
    run = client.post("/v1/public/runs", json={"cik": new_cik}, headers=H["ana"])
    assert run.status_code == 200, run.text
    assert run.json()["case"] == "public" and not run.json()["failed_agents"]
    recs = client.get(f"/v1/recommendations?case=public&cik={new_cik}", headers=H["carla"])
    assert recs.status_code == 200
    body = recs.json()
    items = body if isinstance(body, list) else body.get("items", body.get("recommendations", []))
    for x in items:
        assert x.get("cik", new_cik) == new_cik


def test_unknown_cik_is_404(client, H):
    assert client.get("/v1/public/overview?cik=123", headers=H["ana"]).status_code == 404
    assert client.post("/v1/public/runs", json={"cik": 123}, headers=H["ana"]).status_code in (404, 422)


@pytest.mark.parametrize("cik", BUNDLED)
def test_market_inputs_shared_and_scenario_base_consistent(cik):
    pc = A.get_public_company(cik)
    assert pc.rates["a_oas_pct"] is not None and pc.rates["bbb_oas_pct"] is not None   # every company gets them
    base = next(s for s in pc.scenarios() if s["name"].startswith("Base"))
    if base.get("available", True) and pc.metrics["gross_leverage"]["value"] is not None:
        assert base["gross_leverage_scenario"] == pc.metrics["gross_leverage"]["value"]   # base case reproduces metric


@pytest.mark.parametrize("cik", BUNDLED)
def test_briefing_text_is_honest_for_every_company(cik):
    from meridian.public_company import reasoning as R
    pc = A.get_public_company(cik)
    r = evaluate(pc, "T")
    ev = {"company": pc.subject["name"], "latest_quarter": pc.latest_quarter, "latest_fiscal_year_end": pc.latest_fy,
          "debt_date": pc.debt_date, "captive_finance": pc.captive,
          "metrics": {k: {"label": m["label"], "value": m["value"], "basis": m["basis"]} for k, m in pc.metrics.items()},
          "recommendations": [{k: x[k] for k in ("short_id", "title", "approval_required", "policy_refs", "impact")}
                              for x in r["recommendations"]]}
    b = R.briefing(ev)
    text = " ".join([b.executive_summary, *b.key_numbers, *b.risks])
    assert "n/a covers" not in text and "None" not in text and "nullx" not in text
    assert any("captive" in x for x in b.risks) == pc.captive          # captive caveat only for captive issuers
    if pc.debt_date != "STALE":
        assert f"({pc.debt_date})" in b.key_numbers[1]                 # debt figures carry the debt date
    for x in r["recommendations"]:
        assert "a inventory" not in x["title"]


def test_console_never_renders_null_multiples():
    from pathlib import Path
    js = Path(A.__file__).resolve().parents[1] / "console" / "js" / "p_public.js"
    s = js.read_text(encoding="utf-8")
    assert "?? 'n/a'}x" not in s and "_scenario}x" not in s and "_base}x" not in s
    assert "r.ust5_pct + r.a_oas_pct" in s and "r.a_oas_pct == null" in s   # no sum with a missing spread


@pytest.mark.parametrize("cik", BUNDLED)
def test_forecast_never_negative_and_uses_better_backtested_method(cik):
    f = A.get_public_company(cik).forecast()
    if not f["available"]:
        return
    assert all(o["p10_usd"] >= 0 and o["p50_usd"] >= 0 for o in f["outlook"])   # cash cannot be negative
    assert f["mape"] == min(f["mape_seasonal"], f["mape_naive_no_change"])       # the better track record is used
    assert f["method_selected"] in f["method"].lower().replace("-", "_").replace(" ", "_") or f["method_selected"] == "seasonal"


def test_briefing_states_no_decision_when_no_rule_triggers():
    from meridian.public_company import reasoning as R
    pc = A.get_public_company(18230)
    ev = {"company": "X", "latest_quarter": pc.latest_quarter, "latest_fiscal_year_end": pc.latest_fy,
          "debt_date": pc.debt_date, "captive_finance": False, "recommendations": [],
          "metrics": {k: {"label": m["label"], "value": m["value"], "basis": m["basis"]} for k, m in pc.metrics.items()}}
    assert R.briefing(ev).decisions_required[0].startswith("None — no policy rule was triggered")


def test_forecast_finding_names_the_method_actually_used():
    from meridian.mcp_server.server import get_public_cash_flow
    from meridian.public_company import reasoning as R
    for cik in BUNDLED:
        ev = get_public_cash_flow(cik)
        head = R.forecast(ev).headline
        if ev.get("method_selected") == "no_change":
            assert "no-change" in head and "seasonal run-rate" not in head
        elif ev.get("outlook"):
            assert "seasonal run-rate" in head
