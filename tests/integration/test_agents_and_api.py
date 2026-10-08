import asyncio
import json

import pytest
from fastapi.testclient import TestClient
from mcp import Client

from meridian.agents.reasoners import plan
from meridian.api.main import app
from meridian.mcp_server.server import mcp
from meridian.orchestration.gateway import MCPGateway, ToolPermissionDenied

H = {"analyst": {"X-API-Key": "analyst-key"}, "treasurer": {"X-API-Key": "treasurer-key"},
     "cfo": {"X-API-Key": "cfo-key"}}


def test_planner_parses_cfo_questions():
    p = plan({"question": "What if customers pay 30% slower and rates rise 50 bps?"})
    assert p.collection_delay_pct == 0.3 and p.rate_shock_bps == 50
    assert plan({"question": "What if the euro falls 8% and revenue drops 10%?"}).fx_shock_pct == {"EUR": -0.08}
    assert plan({"question": "Who approves a revolver prepayment?"}).intent == "POLICY_QUESTION"
    assert plan({"question": "Impact of the cyclone in China?"}).include_hazard_events


async def test_mcp_tools_are_read_only_and_no_payment_tool():
    async with Client(mcp) as c:
        tools = (await c.list_tools()).tools
    names = {t.name for t in tools}
    assert {"run_cash_forecast", "optimize_liquidity", "search_treasury_policy"} <= names
    assert not any("pay" in n and "early" not in n for n in names)
    assert all(t.annotations.read_only_hint for t in tools)


async def test_mcp_masks_ibans():
    async with Client(mcp) as c:
        r = await c.call_tool("get_bank_accounts", {})
    assert "•" in r.content[0].text


async def test_gateway_least_privilege(store):
    from meridian.governance.audit import AuditLedger

    with pytest.raises(ToolPermissionDenied):
        await MCPGateway("RUN-X", AuditLedger(store)).call("briefing_agent", "optimize_liquidity")


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def test_auth(client):
    assert client.get("/v1/kpis").status_code == 401


def test_daily_run_to_approved_payment_draft(client):
    run = client.post("/v1/runs", json={}, headers=H["analyst"]).json()
    assert run["status"] == "AWAITING_APPROVALS" and run["grounding"]["grounded"]
    agents = {t["actor"] for t in run["trace"]}
    assert {"forecast_agent", "working_capital_agent", "risk_agent", "liquidity_agent", "policy_agent",
            "briefing_agent"} <= agents
    big = next(a for a in run["actions"] if a["type"] == "INTERCOMPANY_TRANSFER" and a["required_approver"] == "cfo")
    url = f"/v1/runs/{run['run_id']}/actions/{big['action_id']}/decision"
    assert client.post(url, json={"decision": "APPROVE"}, headers=H["treasurer"]).status_code == 403
    ok = client.post(url, json={"decision": "APPROVE", "comment": "go"}, headers=H["cfo"]).json()
    act = next(a for a in ok["actions"] if a["action_id"] == big["action_id"])
    assert act["status"] == "APPROVED" and "pain.001" in act["execution_artifact"]["xml"]
    assert "NOT TRANSMITTED" in act["execution_artifact"]["status"]
    assert client.post(url, json={"decision": "APPROVE"}, headers=H["cfo"]).status_code == 409
    trail = client.get(f"/v1/governance/audit/{run['run_id']}", headers=H["cfo"]).json()
    assert {"tool_call", "agent_output", "action_approve", "approval_denied"} <= {e["action"] for e in trail}
    assert client.get("/v1/governance/audit/verify", headers=H["cfo"]).json()["valid"]


def test_scenario_question_and_policy_question(client):
    r = client.post("/v1/runs", json={"question": "What if customers pay 30% slower and rates rise 50 bps?"},
                    headers=H["treasurer"]).json()
    assert r["scenario"]["collection_delay_pct"] == 0.3 and r["forecast"]["scenario"].startswith("What if")
    p = client.post("/v1/runs", json={"question": "Who approves a revolver prepayment?"}, headers=H["cfo"]).json()
    assert p["status"] == "ANSWERED" and "RCF-8.1" in p["answer"]["policy_refs"]


def test_accounts_masked_for_analyst(client):
    assert "•" in json.dumps(client.get("/v1/accounts", headers=H["analyst"]).json(), ensure_ascii=False)
    assert "•" not in json.dumps(client.get("/v1/accounts", headers=H["cfo"]).json(), ensure_ascii=False)


def test_claude_path_with_fake_client():
    """Exercise the real SDK code path (forced tool use, prompt caching) with a fake Anthropic client."""
    from types import SimpleNamespace

    from meridian.agents.schemas import AgentFinding
    from meridian.config import Settings
    from meridian.llm.claude import BudgetExceeded, ClaudeClient

    seen = {}

    class FakeMessages:
        async def create(self, **kw):
            seen.update(kw)
            return SimpleNamespace(content=[SimpleNamespace(type="tool_use", input={
                "agent": "forecast_agent", "headline": "ok", "key_points": ["$1.0M"]})])

    cc = ClaudeClient(1, Settings(llm_provider="anthropic"), client=SimpleNamespace(messages=FakeMessages()))
    out = asyncio.run(cc.structured("forecast_agent", "x", {"a": 1}, AgentFinding, lambda e: None))
    assert out.headline == "ok"
    assert seen["tool_choice"]["type"] == "tool" and seen["system"][0]["cache_control"] == {"type": "ephemeral"}
    with pytest.raises(BudgetExceeded):
        asyncio.run(cc.structured("forecast_agent", "x", {}, AgentFinding, lambda e: None))
