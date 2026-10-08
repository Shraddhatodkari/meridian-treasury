"""Enterprise platform behaviour: governed ingestion, provenance, readiness, CFO insights, relationship graph,
agent registry, public-data parsers, numeric grounding and stress scenarios."""

import csv
import json
import shutil
from datetime import UTC, date, datetime

import pytest
from mcp import Client

from meridian.agents.registry import registry
from meridian.analytics.insights import ANSWERS, TOPICS, answer, classify
from meridian.analytics.relationships import build_graph, hazard_exposure_paths, neighborhood
from meridian.analytics.scenarios import Scenario
from meridian.config import Settings
from meridian.governance.guardrails import extract_money, numeric_grounding
from meridian.ingest.camt053 import build_camt053, parse_camt053
from meridian.ingest.pipeline import export_package, iban_valid, ingest_directory, validate_and_build
from meridian.market.connectors import parse_ecb, parse_gdacs, parse_sofr
from meridian.mcp_server.server import mcp
from meridian.provenance import Classification, SourceRecord
from meridian.readiness import runtime_checks, static_checks, summarize
from meridian.store.base import MemoryStore


# ------------------------------------------------------------------------------------------------ ingestion
@pytest.fixture(scope="module")
def package(engine, tmp_path_factory):
    return export_package(engine.g, tmp_path_factory.mktemp("pkg"))


def _copy(package, tmp_path):
    dst = tmp_path / "pkg"
    shutil.copytree(package, dst)
    return dst


def _rewrite(path, fn):
    rows = list(csv.DictReader(path.open()))
    fields = list(rows[0].keys())
    rows = fn(rows)
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)


def test_exported_sample_ingests_cleanly_and_stays_labelled_synthetic(package):
    store = MemoryStore()
    rep = ingest_directory(package, store)
    assert rep.loaded and not rep.errors, [c.__dict__ for c in rep.errors][:5]
    g = store.load_group()
    assert g.classification == "SYNTHETIC"          # a sample can never pose as client data
    assert json.loads((package / "manifest.json").read_text())["data_classification"] == "SYNTHETIC_SAMPLE"
    assert {s.source_id for s in rep.sources} >= {"company.bank_balances", "company.receivables"}
    assert all(s.checksum_sha256 for s in rep.sources)


def test_bad_iban_blocks_the_whole_load(package, tmp_path):
    pkg = _copy(package, tmp_path)
    _rewrite(pkg / "bank_accounts.csv", lambda rows: [{**rows[0], "iban": rows[0]["iban"][:-1] + "0"
                                                       if rows[0]["iban"][-1] != "0" else rows[0]["iban"][:-1] + "1"}]
             + rows[1:])
    store = MemoryStore()
    rep = ingest_directory(pkg, store)
    assert not rep.loaded and any(c.rule == "iban_mod97" for c in rep.errors)
    assert not store.is_seeded()                      # atomic: nothing partially loaded
    assert store.find("ingestions")[0]["loaded"] is False


def test_missing_bank_statement_and_referential_errors_are_reported(package, tmp_path):
    pkg = _copy(package, tmp_path)
    next((pkg / "bank_statements").glob("*.xml")).unlink()
    _rewrite(pkg / "ar_invoices.csv", lambda rows: [{**rows[0], "customer_id": "C-DOES-NOT-EXIST"}] + rows[1:])
    _, rep = validate_and_build(pkg)
    rules = {c.rule for c in rep.errors}
    assert {"statement_missing", "referential_integrity"} <= rules


def test_missing_manifest_is_an_error(package, tmp_path):
    pkg = _copy(package, tmp_path)
    (pkg / "manifest.json").unlink()
    g, rep = validate_and_build(pkg)
    assert g is None and any(c.rule == "file_present" for c in rep.errors)


def test_iban_mod97():
    assert iban_valid("GB82WEST12345698765432") and iban_valid("DE89370400440532013000")
    assert not iban_valid("GB82WEST12345698765433")


def test_camt053_round_trip():
    xml = build_camt053("STMT-1", "DE89370400440532013000", "EUR", -1250.5, 1000.0, date(2026, 10, 5), "Orion GmbH")
    [s] = parse_camt053(xml)
    assert s["iban"] == "DE89370400440532013000" and s["currency"] == "EUR"
    assert s["closing_balance"] == -1250.5 and s["balance_date"] == date(2026, 10, 5)


# ------------------------------------------------------------------------------------------------ provenance
def test_freshness_states():
    rec = SourceRecord(source_id="market.fx", name="FX", classification=Classification.PUBLIC_MARKET_DATA, system="ECB",
                       retrieved_at=datetime.now(UTC), as_of=date(2026, 10, 6), freshness_sla_hours=96)
    assert rec.freshness(reference_date=date(2026, 10, 7)) == "FRESH"
    assert rec.freshness(reference_date=date(2026, 10, 20)) == "STALE"
    assert rec.freshness(reference_date=date(2026, 9, 1)) == "FUTURE_DATED"
    assert rec.model_copy(update={"freshness_sla_hours": None}).freshness() == "N/A"


def test_every_input_has_provenance_and_synthetic_is_labelled(engine):
    prov = {p["source_id"]: p for p in engine.provenance()}
    assert {"company.bank_balances", "company.receivables", "market.fx", "market.sofr", "model.ar_delay"} <= set(prov)
    assert all(p["classification"] == "SYNTHETIC" for k, p in prov.items() if k.startswith("company."))
    assert prov["market.fx"]["classification"] == "PUBLIC_MARKET_DATA"


# ------------------------------------------------------------------------------------------------ readiness
def test_production_readiness_refuses_demo_shortcuts():
    s = Settings(mode="production", llm_provider="mock", mongodb_uri=None, jwt_secret=None, api_keys=None)
    checks = static_checks(s) + runtime_checks(s, _seeded_synthetic_store())
    res = summarize(checks)
    failed = {c["check"] for c in checks if c["status"] == "FAIL"}
    assert not res["ready"]
    assert {"Persistence: MongoDB configured", "LLM provider is a real Claude endpoint", "Session signing key",
            "Company data classification", "Persistent store"} <= failed


def test_demo_readiness_is_explicitly_demo():
    checks = static_checks(Settings(mode="demo"))
    assert checks[0]["check"] == "Operating mode" and "DEMO" in checks[0]["detail"]


def _seeded_synthetic_store():
    from meridian.data.generator import generate
    st = MemoryStore()
    st.seed(generate(seed=7))
    return st


# ------------------------------------------------------------------------------------------------ CFO insights
@pytest.mark.parametrize("q,topic", [
    ("Where is cash trapped?", "trapped_cash"),
    ("Which entities are approaching liquidity risk?", "liquidity_risk"),
    ("Which receivables should Treasury prioritise?", "collections"),
    ("How much liquidity can we release?", "releasable_liquidity"),
    ("Should we repay debt or preserve liquidity?", "repay_vs_preserve"),
    ("What requires CFO approval?", "cfo_approvals"),
])
def test_cfo_question_classification(q, topic):
    assert classify(q) == topic


@pytest.mark.parametrize("topic", sorted(TOPICS))
def test_every_cfo_answer_carries_evidence_confidence_and_limitations(engine, topic):
    a = answer(engine, topic)
    assert a["headline"] and a["confidence"] in {"HIGH", "MEDIUM", "LOW"}
    assert a["evidence"]["data_sources"] and a["evidence"]["policy_refs"] and a["evidence"]["policy_version"]
    assert a["limitations"]
    assert set(ANSWERS) == set(TOPICS)


def test_trapped_cash_reconciles_to_entity_balances(engine):
    a = answer(engine, "trapped_cash")
    controlled = sum(e["balance_usd"] for e in engine.entities() if e["capital_controls"])
    assert 0 < a["figures"]["trapped_total_usd"] <= controlled + 1


# ------------------------------------------------------------------------------------------------ relationship graph
def test_graph_connects_treasury_objects(engine):
    g = build_graph(engine.g)
    kinds = set(g["counts"])
    assert {"Entity", "BankAccount", "Currency", "Facility", "Customer", "Supplier", "PolicyClause", "RiskEvent",
            "Country", "Contract"} <= kinds
    ids = {n["id"] for n in g["nodes"]}
    assert all(e["source"] in ids and e["target"] in ids for e in g["edges"])   # no dangling edges


def test_graph_includes_actions_and_approvers():
    from meridian.data.generator import generate
    g = generate(seed=7)
    run = {"run_id": "RUN-T", "actions": [{"action_id": "A001", "type": "INTERCOMPANY_TRANSFER", "status": "APPROVED",
                                           "amount_usd": 1e6, "required_approver": "treasurer", "from_entity": "E02",
                                           "to_entity": "E01", "policy_refs": ["TP-2.1"],
                                           "approved_by": {"user": "tom", "role": "treasurer"}}]}
    graph = build_graph(g, [run])
    hood = neighborhood(graph, "ACT:RUN-T:A001", depth=1)
    rels = {e["rel"] for e in hood["edges"]}
    assert {"DEBITS", "CREDITS", "CITES", "APPROVED_BY"} <= rels


def test_hazard_paths_link_events_to_exposed_entities(engine):
    paths = hazard_exposure_paths(build_graph(engine.g))
    assert paths and all(p["entities"] or p["ar_exposed_top_customers_usd"] >= 0 for p in paths)


# ------------------------------------------------------------------------------------------------ agents
async def test_every_allowlisted_tool_exists_and_is_read_only():
    async with Client(mcp) as c:
        tools = {t.name: t for t in (await c.list_tools()).tools}
    for spec in registry():
        assert not spec.can_move_money
        for t in spec.tools:
            assert t in tools, f"{spec.name} allowlists unknown tool {t}"
            assert tools[t].annotations.read_only_hint


def test_agent_pipeline_order_matches_governance_design():
    names = [s.name for s in registry()]
    core = ["planner_agent", "forecast_agent", "liquidity_agent", "risk_agent", "working_capital_agent",
            "policy_agent", "briefing_agent", "guardrail"]
    assert [n for n in names if n in core] == core


# ------------------------------------------------------------------------------------------------ public data parsers
ECB = """<?xml version="1.0" encoding="UTF-8"?><gesmes:Envelope xmlns:gesmes="http://www.gesmes.org/xml/2002-08-01"
xmlns="http://www.ecb.int/vocabulary/2002-08-01/eurofxref"><Cube><Cube time="2026-10-06">
<Cube currency="USD" rate="1.1269"/><Cube currency="GBP" rate="0.8488"/><Cube currency="JPY" rate="178.15"/></Cube></Cube>
</gesmes:Envelope>"""


def test_ecb_parser_cross_rates_and_aed_peg():
    d, issues = parse_ecb(ECB, {"USD", "EUR", "GBP", "JPY", "AED", "XYZ"})
    assert d["rate_date"] == "2026-10-06" and d["usd_per_unit"]["EUR"] == 1.1269
    assert d["usd_per_unit"]["GBP"] == pytest.approx(1.1269 / 0.8488, rel=1e-6)
    assert d["usd_per_unit"]["AED"] == pytest.approx(1 / 3.6725, rel=1e-6)
    assert any("AED" in i for i in issues) and any("XYZ" in i for i in issues)


def test_sofr_parser():
    r = parse_sofr({"refRates": [{"effectiveDate": "2026-10-05", "percentRate": 3.89, "volumeInBillions": 2900}]})
    assert r == {"effective_date": "2026-10-05", "sofr_pct": 3.89, "volume_bn": 2900}


def test_gdacs_parser_keeps_current_events_in_footprint():
    rss = """<rss xmlns:gdacs="http://www.gdacs.org"><channel>
    <item><title>Red flood in India</title><gdacs:iscurrent>true</gdacs:iscurrent><gdacs:iso3>IND</gdacs:iso3>
      <gdacs:eventtype>FL</gdacs:eventtype><gdacs:eventid>1</gdacs:eventid><gdacs:alertlevel>Red</gdacs:alertlevel>
      <gdacs:fromdate>Mon, 05 Oct 2026 00:00:00 GMT</gdacs:fromdate><gdacs:todate>Wed, 07 Oct 2026 00:00:00 GMT</gdacs:todate></item>
    <item><title>Old quake</title><gdacs:iscurrent>false</gdacs:iscurrent><gdacs:iso3>IND</gdacs:iso3>
      <gdacs:eventtype>EQ</gdacs:eventtype><gdacs:eventid>2</gdacs:eventid></item>
    <item><title>Elsewhere</title><gdacs:iscurrent>true</gdacs:iscurrent><gdacs:iso3>ISL</gdacs:iso3>
      <gdacs:eventtype>VO</gdacs:eventtype><gdacs:eventid>3</gdacs:eventid></item></channel></rss>"""
    [ev] = parse_gdacs(rss, {"IN", "CN"})
    assert ev.country == "IN" and ev.severity == "RED" and ev.hazard == "FLOOD" and ev.expected_days == 3
    assert ev.classification == "PUBLIC_MARKET_DATA"


def test_market_snapshot_is_dated_and_complete():
    from meridian.market.service import SNAPSHOT
    snap = json.loads(SNAPSHOT.read_text())
    assert {"fx", "sofr", "hazards", "peers"} <= set(snap)
    assert snap["fx"]["data"]["rate_date"] and snap["sofr"]["data"]["effective_date"]


# ------------------------------------------------------------------------------------------------ grounding
def test_money_extraction_does_not_misread_thousands_or_words():
    # regression: "$5,000,000, multiples" was read as $5 trillion (trailing comma + "m" of "multiples")
    assert extract_money("min $5,000,000, multiples of $1,000,000") == [5e6, 1e6]
    assert extract_money("$61.4M and $1.25 million and $3bn and $250K") == [61.4e6, 1.25e6, 3e9, 250e3]


def test_grounding_accepts_traced_and_flags_invented_figures():
    ev = {"cash": 61_400_000, "terms": {"min_usd": 5_000_000}}
    assert numeric_grounding("Sweep $61.4M (min $5,000,000).", ev)["grounded"]
    assert not numeric_grounding("Sweep $72.0M.", ev)["grounded"]


# ------------------------------------------------------------------------------------------------ stress
def test_stress_scenarios_reduce_headroom_monotonically(engine):
    base = engine.forecast_summary(Scenario(name="base"))["min_covenant_headroom_p10_usd"]
    mild = engine.forecast_summary(Scenario(name="m", collection_delay_pct=0.15))["min_covenant_headroom_p10_usd"]
    severe = engine.forecast_summary(Scenario(name="s", collection_delay_pct=0.45, rate_shock_bps=200,
                                              fx_shock_pct={"EUR": -0.15}))["min_covenant_headroom_p10_usd"]
    assert base > mild > severe


def test_stress_is_reproducible(engine):
    s = Scenario(name="r", collection_delay_pct=0.3, rate_shock_bps=50)
    a, b = engine.forecast_summary(s), engine.forecast_summary(s)
    assert a["min_covenant_headroom_p10_usd"] == b["min_covenant_headroom_p10_usd"]
    assert a["liquidity_usd"]["p10"] == b["liquidity_usd"]["p10"]


# ------------------------------------------------------------------------------------------------ RAG citations
async def test_policy_question_cites_correct_clauses_and_never_quarantined(store):
    from meridian.orchestration.service import TreasuryService
    run = await TreasuryService(store).run("Who approves a revolver prepayment?", "ana")
    assert run["intent"] == "POLICY_QUESTION"
    cited = {c["clause_id"] for c in run["answer"]["citations"]}
    assert {"TP-2.1", "RCF-8.1"} & cited
    assert "SC7-2" not in cited
    assert all(c["document"] for c in run["answer"]["citations"])
