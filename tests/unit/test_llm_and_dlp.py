"""Claude client hardening (v3.2): outbound DLP, labelled fallbacks on provider errors and budgets, token accounting.
The real SDK code path is exercised with fake transports; no network and no API key are needed."""

import asyncio
from types import SimpleNamespace

import pytest

from meridian.agents.schemas import AgentFinding
from meridian.config import Settings
from meridian.governance.dlp import redact_text, redact_with_report
from meridian.llm.claude import SOURCE_FALLBACK_BUDGET, SOURCE_FALLBACK_ERROR, SOURCE_LIVE, SOURCE_OFFLINE, ClaudeClient

FB = AgentFinding(agent="risk_agent", headline="deterministic answer")


def _client(create, **settings):
    return ClaudeClient(10, Settings(llm_provider="anthropic", **settings), client=SimpleNamespace(
        messages=SimpleNamespace(create=create)))


def _ok(tokens=(1200, 300)):
    async def create(**kw):
        create.kw = kw
        return SimpleNamespace(usage=SimpleNamespace(input_tokens=tokens[0], output_tokens=tokens[1],
                                                     cache_read_input_tokens=0, cache_creation_input_tokens=0),
                               content=[SimpleNamespace(type="tool_use", input={"agent": "risk_agent", "headline": "live"})])
    return create


# ------------------------------------------------------------------------------------------------ DLP
@pytest.mark.parametrize("text,kind", [
    ("pay to DE89370400440532013000 today", "IBAN"),
    ("card 4111 1111 1111 1111 on file", "CARD"),
    ("employee SSN 123-45-6789", "SSN"),
    ("EIN 12-3456789", "EIN"),
    ("key sk-ant-api03-abcdefghijklmnop", "ANTHROPIC_KEY"),
    ("Authorization: Bearer abcdefghijklmnopqrstuvwxyz123", "BEARER"),
    ("password=hunter2!", "CREDENTIAL"),
    ("contact cfo@example.com", "EMAIL"),
])
def test_dlp_redacts_sensitive_values(text, kind):
    assert f"[REDACTED:{kind}]" in redact_text(text)


@pytest.mark.parametrize("text", [
    "accession 0000018230-26-000008 filed 2026-02-13",          # SEC accession: not a card, not an EIN
    "CIK 0000018230 and CAT debt $12.63B",
    "invalid IBAN DE00370400440532013000",                       # fails mod-97
    "card-like 4111 1111 1111 1112",                             # fails Luhn
    "fiscal year 2026-06-30, ratio 1.55x, DSO 64.4 days",
])
def test_dlp_leaves_financial_evidence_alone(text):
    assert redact_text(text) == text


def test_dlp_structured_fields_and_numbers():
    out, rep = redact_with_report({"iban": "anything at all", "bank": {"account_number": "123"},
                                   "cash_usd": 6_713_000_000, "note": "ok"})
    assert out["iban"] == "[REDACTED:IBAN]" and out["bank"]["account_number"] == "[REDACTED:ACCOUNT_NUMBER]"
    assert out["cash_usd"] == 6_713_000_000 and out["note"] == "ok" and rep["field:iban"] == 1


# ------------------------------------------------------------------------------------------------ client
def test_live_call_is_labelled_redacted_and_metered():
    create = _ok()
    cc = _client(create)
    out = asyncio.run(cc.structured("risk_agent", "x", {"iban": "DE89370400440532013000", "cash": 5}, AgentFinding,
                                    lambda e: FB))
    assert out.headline == "live" and cc.last_source["risk_agent"] == SOURCE_LIVE
    sent = create.kw["messages"][0]["content"]
    assert "DE89370400440532013000" not in sent and "[REDACTED:IBAN]" in sent       # DLP before leaving the process
    assert cc.usage["input_tokens"] == 1200 and cc.summary()["redactions"] == 1


@pytest.mark.parametrize("exc", [TimeoutError("timed out"), ConnectionError("down"), RuntimeError("HTTP 429")])
def test_provider_errors_fall_back_and_are_labelled(exc):
    async def create(**kw):
        raise exc
    cc = _client(create)
    out = asyncio.run(cc.structured("risk_agent", "x", {}, AgentFinding, lambda e: FB))
    assert out.headline == "deterministic answer" and cc.last_source["risk_agent"] == SOURCE_FALLBACK_ERROR
    assert type(exc).__name__ in cc.events[-1]["error"]


def test_malformed_response_falls_back():
    async def create(**kw):
        return SimpleNamespace(usage=None, content=[SimpleNamespace(type="text", text="no tool call")])
    cc = _client(create)
    asyncio.run(cc.structured("risk_agent", "x", {}, AgentFinding, lambda e: FB))
    assert cc.last_source["risk_agent"] == SOURCE_FALLBACK_ERROR


def test_token_budget_stops_spending():
    create = _ok(tokens=(900, 200))
    cc = _client(create, llm_max_tokens_per_run=1000)
    asyncio.run(cc.structured("risk_agent", "x", {}, AgentFinding, lambda e: FB))   # uses 1,100 tokens
    asyncio.run(cc.structured("risk_agent", "x", {}, AgentFinding, lambda e: FB))   # over budget: no call
    assert [e["source"] for e in cc.events] == [SOURCE_LIVE, SOURCE_FALLBACK_BUDGET]


def test_offline_mode_never_claims_claude():
    cc = ClaudeClient(10, Settings(llm_provider="mock"))
    asyncio.run(cc.structured("risk_agent", "x", {}, AgentFinding, lambda e: FB))
    s = cc.summary()
    assert cc.mode == "mock" and s["sources"] == {SOURCE_OFFLINE: 1} and s["model"] == "deterministic offline reasoner"


def test_sdk_client_gets_timeout_and_retries():
    from meridian.llm.claude import _make_client
    c = _make_client(Settings(llm_provider="anthropic", ANTHROPIC_API_KEY="sk-ant-test-not-real", llm_timeout_s=12,
                              llm_max_retries=3))
    assert c.max_retries == 3 and float(c.timeout) == 12.0
