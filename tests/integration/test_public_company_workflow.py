"""End-to-end through the HTTP API on the REAL public company: governed agent run → recommendations with verified
policy citations → submit → authority / SoD / scope denials → CFO approval → mandate preparation → dual-control issue →
audit chain → filing-level lineage. Payments remain impossible."""

import pytest
from fastapi.testclient import TestClient

from meridian.api.main import app
from meridian.security.auth import DEMO_PASSWORD


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def login(client, user):
    r = client.post("/v1/auth/login", json={"username": user, "password": DEMO_PASSWORD})
    assert r.status_code == 200
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest.fixture(scope="module")
def H(client):
    return {u: login(client, u) for u in ("ana", "wendy", "tom", "carla", "iris", "ada")}


@pytest.fixture(scope="module")
def run(client, H):
    r = client.post("/v1/public/runs", json={}, headers=H["ana"])
    assert r.status_code == 200, r.text
    return r.json()


def test_cases_put_real_company_first(client, H):
    c = client.get("/v1/cases", headers=H["iris"]).json()
    assert c[0]["case"] == "public" and c[0]["classification"] == "PUBLIC_REAL" and c[0]["primary"]
    assert c[0]["cik"] == 18230                                   # Caterpillar is the primary real company
    pub = [x for x in c if x["case"] == "public"]
    assert all(x["classification"] == "PUBLIC_REAL" for x in pub) and len(pub) >= 5   # every bundled company listed
    syn = [x for x in c if x["case"] == "synthetic"]
    assert len(syn) == 1 and syn[0]["label"] == "SYNTHETIC · EVALUATION" and not syn[0]["primary"]
    assert c.index(syn[0]) > max(c.index(x) for x in pub)          # real companies before the evaluation case


def test_environment_reports_ai_honestly(client):
    env = client.get("/v1/system/environment").json()
    assert env["ai_status"]["label"] == "AI OFFLINE / DETERMINISTIC" and env["primary_case"] == "public"
    assert env["version"].startswith("3.")


def test_public_run_is_fully_governed(run):
    assert run["case"] == "public" and run["classification"] == "PUBLIC_REAL" and not run["failed_agents"]
    agents = [f["agent"] for f in run["findings"]]
    for a in ("data_intelligence_agent", "forecast_agent", "liquidity_agent", "risk_agent", "working_capital_agent",
              "scenario_agent", "decision_agent", "policy_agent", "governance_agent"):
        assert a in agents
    assert all(f["validation"]["numeric_grounding"] for f in run["findings"])
    assert run["grounding"]["grounded"] and run["briefing"]["llm_provider"] == "mock"
    gov = next(f for f in run["findings"] if f["agent"] == "governance_agent")
    assert all(c["passed"] for c in gov["checks"])
    tools = {c["tool"] for c in run["tool_calls"]}
    assert {"get_public_liquidity", "get_public_working_capital", "get_public_debt_and_rates"} <= tools


def test_recommendations_cite_verified_policy(run):
    assert run["recommendations"]
    for r in run["recommendations"]:
        assert r["policy_verification"]["verified"] and r["policy_citations"]
        assert all(c["document"].startswith("Meridian Reference Policy") for c in r["policy_citations"])
        assert r["governance"]["checks_passed"]


def test_rules_report_untriggered_rules(run):
    assert len(run["rules"]) == 5 and any(not r["triggered"] for r in run["rules"])


def test_public_lineage_reaches_sec_filings(client, H, run):
    rid = run["recommendations"][0]["rec_id"]
    g = client.get(f"/v1/lineage/recommendations/{rid}", headers=H["iris"]).json()
    kinds = {n["kind"] for n in g["nodes"]}
    assert {"Source", "Filing", "Fact", "Metric", "PolicyClause", "Recommendation", "Action"} <= kinds
    assert all(n["accession"].startswith("0000018230-") for n in g["nodes"] if n["kind"] == "Fact")


def test_public_metric_endpoint_lineage(client, H):
    m = client.get("/v1/public/metrics/dso", headers=H["ana"]).json()
    assert m["classification"] == "PUBLIC_REAL" and m["lineage"]["filings"] and m["lineage"]["facts"]
    assert client.get("/v1/public/metrics/nope", headers=H["ana"]).status_code == 404


def test_scenario_input_validation(client, H):
    assert client.get("/v1/public/scenario?revenue_change_pct=-500", headers=H["ana"]).status_code == 422
    assert client.get("/v1/public/scenario?rate_shock_bps=100", headers=H["ana"]).json()["basis"] == "SCENARIO"


def test_mandate_lifecycle_with_sod_and_dual_control(client, H, run):
    r = next(x for x in run["recommendations"] if x["category"] == "REFINANCING")
    aid = r["action_ids"][0]
    url = f"/v1/runs/{run['run_id']}/actions/{aid}"
    assert client.post(f"/v1/recommendations/{r['rec_id']}/submit", json={"comment": "go"}, headers=H["ana"]).status_code == 200
    d = client.post(f"{url}/decision", json={"decision": "APPROVE", "comment": "x"}, headers=H["tom"])
    assert d.status_code == 403 and "cfo" in d.json()["detail"].lower()                     # authority (PC-2.1 → CFO)
    assert client.post(f"{url}/decision", json={"decision": "APPROVE", "comment": "x"}, headers=H["ada"]).status_code == 403
    assert client.post(f"{url}/prepare", headers=H["tom"]).status_code == 409                # not approved yet
    assert client.post(f"{url}/decision", json={"decision": "APPROVE", "comment": "plan approved"},
                       headers=H["carla"]).status_code == 200
    assert client.post(f"{url}/prepare", headers=H["ana"]).status_code == 403                # analyst cannot prepare
    p = client.post(f"{url}/prepare", headers=H["tom"])
    assert p.status_code == 200
    act = next(a for a in p.json()["actions"] if a["action_id"] == aid)
    assert act["status"] == "READY" and all(c["passed"] for c in act["execution_artifact"]["validation"])
    blocked = client.post(f"{url}/release", headers=H["carla"])                              # approver cannot issue
    assert blocked.status_code == 409 and "dual control" in blocked.json()["detail"].lower()
    ok = client.post(f"{url}/release", headers=H["tom"])
    assert ok.status_code == 200
    act = next(a for a in ok.json()["actions"] if a["action_id"] == aid)
    assert act["status"] == "EXECUTED" and act["execution_artifact"]["kind"] == "decision_mandate"
    assert "no payment" in act["execution_artifact"]["status"].lower()
    rec = client.get(f"/v1/recommendations/{r['rec_id']}", headers=H["iris"]).json()
    assert rec["status"] == "EXECUTED"
    trail = [a["action"] for a in client.get(f"/v1/governance/audit/{run['run_id']}", headers=H["iris"]).json()]
    for needed in ("recommendation_submitted", "approval_denied", "action_approve", "mandate_prepared",
                   "release_blocked", "mandate_issued"):
        assert needed in trail, needed
    assert client.get("/v1/governance/audit/verify", headers=H["iris"]).json()["valid"]


def test_requester_cannot_approve_and_scope_applies(client, H, run):
    r = next(x for x in run["recommendations"] if x["category"] == "WORKING_CAPITAL")
    url = f"/v1/runs/{run['run_id']}/actions/{r['action_ids'][0]}/decision"
    d = client.post(url, json={"decision": "APPROVE", "comment": "x"}, headers=H["ana"])     # requester + no authority
    assert d.status_code == 403
    ok = client.post(url, json={"decision": "APPROVE", "comment": "review approved"}, headers=H["wendy"])
    assert ok.status_code == 200                                                             # WC manager scope includes it


def test_public_question_answer(client, H):
    r = client.post("/v1/public/runs", json={"question": "Is liquidity enough for debt coming due?"}, headers=H["ana"]).json()
    assert r["status"] == "ANSWERED" and r["insight"]["topic"] == "liquidity" and r["insight"]["grounding"]["grounded"]
    assert all(s["classification"] == "PUBLIC_REAL" for s in r["insight"]["sources"])


def test_public_case_filters(client, H, run):
    recs = client.get("/v1/recommendations?case=public", headers=H["iris"]).json()
    assert recs and all(r["case"] == "public" for r in recs)
    q = client.get("/v1/approvals/queue", headers=H["carla"]).json()
    assert all("case" in x for x in q)
    alerts = client.get("/v1/alerts?case=public", headers=H["carla"]).json()
    assert all(a["severity"] in {"info", "warning", "critical"} for a in alerts)


def test_console_assets_declare_utf8(client):
    for path in ("/console/js/core.js", "/console/js/p_public.js", "/console/app.css", "/"):
        r = client.get(path)
        assert r.status_code == 200 and "charset=utf-8" in r.headers["content-type"].lower(), path
    assert "PUBLIC · REAL" in client.get("/console/js/p_public.js").content.decode("utf-8")
