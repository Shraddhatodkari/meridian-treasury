"""PUBLIC · REAL company case (Caterpillar Inc., SEC EDGAR XBRL): traceability, calculation integrity, data honesty,
evidence-gated recommendations, forecast back-test discipline, scenarios, AI configuration and encoding safety."""

import ast
import json
import subprocess
import sys
from pathlib import Path

import pytest

from meridian import __version__
from meridian.config import PACKAGE_ROOT, PROJECT_ROOT, Settings
from meridian.governance.policy import load_policy
from meridian.public_company.analytics import NA, PublicCompany, get_public_company
from meridian.public_company.reasoning import answer, classify
from meridian.public_company.recommendations import evaluate
from meridian.public_company.sec import SNAPSHOT, extract, load_snapshot

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")


@pytest.fixture(scope="module")
def pc():
    return get_public_company()


# ------------------------------------------------------------------------------------------------ source & lineage
def test_subject_is_real_sec_registrant(pc):
    s = pc.snap["subject"]
    assert s["cik"] == 18230 and s["ticker"] == "CAT" and "CATERPILLAR" in s["entity_name"].upper()
    assert s["source_url"] == "https://data.sec.gov/api/xbrl/companyfacts/CIK0000018230.json"
    assert len(pc.snap["checksum_sha256"]) == 64 and pc.snap["retrieved_at"]


def test_every_fact_carries_filing_lineage(pc):
    for key, facts in pc.facts.items():
        for f in facts:
            assert f["form"] in {"10-K", "10-Q"}, key
            assert f["accn"].startswith("0000018230-"), (key, f["accn"])   # filed by the issuer itself
            assert f["filed"] and f["end"] and f["concept"]


def test_reported_metrics_equal_the_as_filed_fact(pc):
    raw = load_snapshot()["facts"]
    for mid, m in pc.metrics.items():
        if m["basis"] != "REPORTED":
            continue
        (f,) = m["lineage"]["facts"]
        src = [x for x in raw[next(k for k, v in raw.items() if any(y["accn"] == f["accession"] and
                                                                     f"us-gaap:{y['concept']}" == f["concept"] for y in v))]
               if f"us-gaap:{x['concept']}" == f["concept"] and x["end"] == f["period"].split(" → ")[-1]]
        assert src and src[0]["val"] == m["value"], mid


def test_every_public_metric_has_full_lineage_chain(pc):
    for mid, m in pc.metrics.items():
        if m["value"] is None:
            assert m["classification"] == "NOT_AVAILABLE" and NA in m["note"]
            continue
        L = m["lineage"]
        assert m["classification"] == "PUBLIC_REAL"
        assert L["source"].startswith("SEC EDGAR XBRL") and L["filings"] and L["period"] and L["calculation"]
        assert L["transformation"] and L["facts"] and L["result"] == m["value"]
        assert m["basis"] in {"REPORTED", "CALCULATED", "ESTIMATED"}
        if m["basis"] == "ESTIMATED":
            assert m["assumptions"], f"{mid}: ESTIMATED without stated assumptions"


def test_public_case_never_loads_synthetic_generator():
    code = ("import sys; from meridian.public_company.analytics import PublicCompany; "
            "from meridian.public_company.recommendations import evaluate; p = PublicCompany(); p.metrics; "
            "evaluate(p, 'X'); p.forecast(); p.scenarios(); "
            "assert 'meridian.data.generator' not in sys.modules and 'meridian.analytics.engine' not in sys.modules")
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120)  # noqa: S603 - fixed code
    assert r.returncode == 0, r.stderr[-2000:]


def test_extract_keeps_only_issuer_10k_10q_usd_facts():
    raw = {"facts": {"us-gaap": {"InventoryNet": {"units": {"USD": [
        {"end": "2025-12-31", "val": 1, "form": "10-K", "fy": 2025, "fp": "FY", "accn": "a", "filed": "2026-02-01"},
        {"end": "2025-12-31", "val": 2, "form": "10-K/A", "fy": 2025, "fp": "FY", "accn": "b", "filed": "2026-03-01"},
        {"end": "2025-12-31", "val": 3, "form": "8-K", "fy": 2025, "fp": "FY", "accn": "c", "filed": "2026-03-01"},
        {"end": "2025-12-31", "val": 4, "form": "10-Q", "fy": 2026, "fp": "Q1", "accn": "d", "filed": "2026-05-01"}]}}}}}
    f = extract(raw)["inventory"]
    assert len(f) == 1 and f[0]["val"] == 4 and f[0]["accn"] == "d"     # latest 10-K/10-Q filing wins (restatement)


# ------------------------------------------------------------------------------------------------ calculations
def test_q4_is_fy_minus_nine_month_ytd(pc):
    q = {x["end"]: x for x in pc.quarterly("revenue")}
    fy = pc.fy("revenue", "2025-12-31")
    nine = next(f for f in pc.facts["revenue"] if f.get("start") == "2025-01-01" and f["end"] == "2025-09-30")
    assert q["2025-12-31"]["value"] == fy["val"] - nine["val"]
    assert q["2025-12-31"]["basis"] == "CALCULATED" and len(q["2025-12-31"]["facts"]) == 2


def test_ttm_is_four_consecutive_quarters(pc):
    t = pc.ttm("revenue")
    qs = {x["end"]: x["value"] for x in pc.quarterly("revenue")}
    assert t["value"] == sum(qs[e] for e in t["quarters"]) and len(t["quarters"]) == 4
    assert pc.metrics["revenue_ttm"]["value"] == t["value"]


def test_working_capital_days_recompute(pc):
    M = pc.metrics
    assert M["dso"]["value"] == round(M["receivables_trade"]["value"] / M["revenue_ttm"]["value"] * 365, 1)
    assert M["dio"]["value"] == round(M["inventory"]["value"] / M["cogs_ttm"]["value"] * 365, 1)
    assert M["ccc"]["value"] == round(M["dso"]["value"] + M["dio"]["value"] - M["dpo"]["value"], 1)
    assert M["trade_working_capital"]["value"] == M["receivables_trade"]["value"] + M["inventory"]["value"] - M["payables"]["value"]


def test_debt_and_liquidity_reconcile(pc):
    M = pc.metrics
    assert M["total_debt"]["value"] == M["short_term_borrowings"]["value"] + M["maturity_y1"]["value"] + M["ltd_noncurrent"]["value"]
    assert M["net_debt"]["value"] == M["total_debt"]["value"] - M["cash_fy"]["value"]
    assert M["liquidity_fy"]["value"] == M["cash_fy"]["value"] + M["credit_available"]["value"]
    assert M["fcf_ttm"]["value"] == M["ocf_ttm"]["value"] - M["capex_ttm"]["value"]
    assert "maturity schedule" in M["total_debt"]["note"]          # proxy disclosed, not hidden


def test_missing_concepts_are_not_imputed(pc):
    assert "maturity_after5" in pc.snap["missing_concepts"]
    after = pc.maturities()["rows"][-1]
    assert after["basis"] == "CALCULATED" and "Residual" in after["note"]
    caps = {g["capability"] for g in pc.data_gaps()}
    assert any("Bank balances" in c for c in caps) and any("Covenant" in c for c in caps)


def test_forecast_backtest_is_point_in_time(pc):
    f = pc.forecast()
    cash = {h["end"]: h["cash_usd"] for h in f["history"]}
    ends = sorted(cash)
    for b in f["backtest"]:
        j = ends.index(b["quarter_end"])
        expected = cash[ends[j - 1]] + (cash[ends[j - 4]] - cash[ends[j - 5]])
        assert b["forecast_cash_usd"] == expected and b["actual_cash_usd"] == cash[ends[j]]
    assert f["basis"] == "ESTIMATED" and f["mape"] < f["mape_naive_no_change"]
    assert all(o["p10_usd"] <= o["p50_usd"] <= o["p90_usd"] for o in f["outlook"])


def test_scenarios_are_consistent(pc):
    base = pc.scenario()
    assert all(i["value_usd"] == 0 for i in base["impacts"]) and base["fcf_scenario_usd"] == base["fcf_base_usd"]
    worse = pc.scenario(dso_change_days=10, rate_shock_bps=100)
    assert worse["fcf_scenario_usd"] < base["fcf_base_usd"]
    assert worse["liquidity_coverage_scenario"] < base["liquidity_coverage_base"]


# ------------------------------------------------------------------------------------------------ recommendations
def test_recommendations_are_evidence_gated_and_complete(pc):
    out = evaluate(pc, "RUN-T")
    triggered = [r for r in out["rules"] if r["triggered"]]
    assert len(out["recommendations"]) == len(triggered) and len(out["rules"]) == 5
    for r in out["recommendations"]:
        for k in ("decision", "objective", "impact", "timing", "constraints", "risks", "confidence", "owner", "evidence",
                  "policy_refs", "approval_required", "data_limitations"):
            assert r.get(k) not in (None, ""), (r["short_id"], k)
        assert r["data_classification"] == "PUBLIC_REAL" and not r["payment_related"]
        assert all(i["basis"] in {"CALCULATED", "ESTIMATED"} for i in r["impact"])
        assert all(e["value"] is not None for e in r["evidence"])
    from meridian.domain.models import PAYMENT_ACTIONS
    assert not any(a["type"] in PAYMENT_ACTIONS for a in out["actions"])


def test_raising_a_threshold_removes_the_recommendation(pc):
    pol = load_policy()
    strict = pol.model_copy(update={"public_company": pol.public_company.model_copy(update={"wc_days_trigger": 99.0})})
    cats = {r["category"] for r in evaluate(pc, "RUN-T", strict)["recommendations"]}
    assert "WORKING_CAPITAL" not in cats


def test_capital_allocation_not_manufactured(pc):
    M = pc.metrics
    out = evaluate(pc, "RUN-T")
    has = any(r["category"] == "CAPITAL_ALLOCATION" for r in out["recommendations"])
    assert has == (M["shareholder_returns_ttm"]["value"] > M["fcf_ttm"]["value"])


def test_copilot_routes_and_grounds(pc):
    from meridian.governance.guardrails import numeric_grounding

    assert classify("when does debt mature?") == "debt" and classify("what is driving DSO") == "working_capital"
    for q in ("Is liquidity enough?", "What is the refinancing profile?", "What happens in a downturn?",
              "How much free cash flow?", "What is our rate exposure?"):
        a = answer(pc, q, "v")
        ev = {"a": a, "m": pc.metrics, "mat": pc.maturities(), "s": pc.scenarios(), "r": pc.rates}
        assert numeric_grounding(a["conclusion"], ev)["grounded"], q
        assert a["sources"] and all(s["classification"] == "PUBLIC_REAL" for s in a["sources"])


# ------------------------------------------------------------------------------------------------ AI provider config
def test_ai_offline_without_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("MERIDIAN_LLM_PROVIDER", raising=False)
    s = Settings(_env_file=None)
    assert s.llm_provider == "mock" and s.ai_status()["label"] == "AI OFFLINE / DETERMINISTIC"
    assert not s.ai_status()["configured"]


def test_claude_configured_from_environment(monkeypatch):
    from meridian.llm.claude import ClaudeClient

    monkeypatch.delenv("MERIDIAN_LLM_PROVIDER", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-not-a-real-key")
    monkeypatch.setenv("MERIDIAN_LLM_MODEL", "claude-sonnet-4-5")
    s = Settings(_env_file=None)
    assert s.llm_provider == "anthropic" and s.ai_status() == {"label": "AI: ANTHROPIC · claude-sonnet-4-5",
                                                              "provider": "anthropic", "model": "claude-sonnet-4-5",
                                                              "configured": True}
    c = ClaudeClient(4, settings=s)          # constructs the SDK client; no network call is made
    assert c.mode == "anthropic"


def test_explicit_mock_overrides_key(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.setenv("MERIDIAN_LLM_PROVIDER", "mock")
    assert Settings(_env_file=None).llm_provider == "mock"


def test_no_api_key_in_source():
    for p in (PROJECT_ROOT / "src").rglob("*.py"):
        assert "sk-ant-" not in p.read_text(encoding="utf-8"), p


# ------------------------------------------------------------------------------------------------ encoding & version
def test_all_text_io_declares_utf8():
    """Windows defaults to cp1252: every text read/write must pass encoding= or symbols become mojibake (Î£, â†’)."""
    bad = []
    for p in list((PROJECT_ROOT / "src").rglob("*.py")) + list((PROJECT_ROOT / "scripts").rglob("*.py")):
        for n in ast.walk(ast.parse(p.read_text(encoding="utf-8"))):
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr in {"read_text", "write_text"}:
                if not any(k.arg == "encoding" for k in n.keywords):
                    bad.append(f"{p.name}:{n.lineno}")
    assert not bad, bad


def test_console_assets_have_no_mojibake():
    for p in (PACKAGE_ROOT / "console").rglob("*"):
        if p.suffix in {".js", ".css", ".html"} and "vendor" not in p.parts:
            t = p.read_text(encoding="utf-8")
            for seq in ("Î£", "Ã©", "â€", "â†", "â‰"):
                assert seq not in t, (p.name, seq)


def test_version_is_3x_everywhere():
    assert __version__.startswith("3.")
    assert f'version = "{__version__}"' in (PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    js = (PACKAGE_ROOT / "console" / "js" / "app.js").read_text(encoding="utf-8")
    assert "r.version" in js and "2.0.0" not in js     # the console shows the backend's version, never a constant


def test_snapshot_file_is_utf8_json():
    json.loads(Path(SNAPSHOT).read_text(encoding="utf-8"))
    PublicCompany(load_snapshot())
