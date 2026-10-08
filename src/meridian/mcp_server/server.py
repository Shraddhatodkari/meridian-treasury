"""Meridian MCP server — the treasury tool surface for agents, Claude Desktop and other MCP clients.

Tools are analytical and READ-ONLY. There is deliberately no "execute payment" tool: money only
moves through the approval workflow (TP-2.1, TP-7.1), never through an agent tool call.

    python -m meridian.mcp_server                 # stdio (Claude Desktop)
    python -m meridian.mcp_server --http 8200     # streamable HTTP
"""

from __future__ import annotations

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

from meridian import __version__
from meridian.analytics.engine import get_engine, scenario_from_any
from meridian.governance.guardrails import mask
from meridian.governance.policy import load_policy

RO = ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=False)

mcp = MCPServer(
    name="meridian-treasury-tools",
    instructions=("Read-only treasury analytics. Two clearly separated cases: (1) a REAL listed company analysed from "
                  "SEC EDGAR XBRL filings and public market data (get_public_* tools), and (2) a SYNTHETIC evaluation "
                  "multinational used to demonstrate transaction-level capabilities. No tool moves money."),
    version=__version__,
)


@mcp.tool(annotations=RO)
def get_treasury_kpis() -> dict:
    """Group cash, liquidity, DSO/DPO, AR aging, trapped cash, revolver usage and covenant metrics."""
    return get_engine().kpis()


@mcp.tool(annotations=RO)
def get_entity_cash_positions() -> list[dict]:
    """Legal entities with country, currency, capital-control status, buffer and current cash (USD)."""
    return get_engine().entities()


@mcp.tool(annotations=RO)
def get_bank_accounts() -> list[dict]:
    """Bank accounts by entity (IBANs masked per TP-7.1)."""
    return mask([a.model_dump() for a in get_engine().g.accounts])


@mcp.tool(annotations=RO)
def run_cash_forecast(scenario: str = "base") -> dict:
    """13-week Monte-Carlo cash forecast (P10/P50/P90) by entity and group, with covenant headroom.
    `scenario` is a preset name (base, eu_slowdown, global_stress, asia_hazards) or a Scenario JSON."""
    return get_engine().forecast_summary(scenario_from_any(scenario))


@mcp.tool(annotations=RO)
def compare_scenarios(scenarios: list[str]) -> list[dict]:
    """Stress-test several scenarios side by side (covenant headroom, entities below buffer, week-13 cash)."""
    return get_engine().scenario_compare([scenario_from_any(s) for s in scenarios])


@mcp.tool(annotations=RO)
def optimize_liquidity(scenario: str = "base") -> dict:
    """Linear-programme recommendation: intercompany sweeps/fundings and revolver repayment, with costs and
    annualised benefit, using P10 trough balances."""
    return get_engine().optimize(scenario_from_any(scenario))


@mcp.tool(annotations=RO)
def get_collections_priorities(top_n: int = 10) -> list[dict]:
    """Open invoices ranked by financing cost of predicted delay (ML P50/P90 days late) with actions."""
    return get_engine().collections(top_n)


@mcp.tool(annotations=RO)
def get_early_pay_opportunities(scenario: str = "base") -> dict:
    """Supplier discounts whose implied return beats the cost of funds, filtered by entity liquidity."""
    return get_engine().early_pay(scenario_from_any(scenario))


@mcp.tool(annotations=RO)
def get_risk_events() -> list[dict]:
    """Active natural-hazard events (GDACS-style) with open-AR exposure in the affected country."""
    return get_engine().risk_events()


@mcp.tool(annotations=RO)
def search_treasury_policy(query: str, k: int = 4) -> list[dict]:
    """Hybrid (vector + keyword) search over the treasury policy, RCF agreement and repatriation rules.
    Returns citable clause IDs. Chunks quarantined for prompt injection are never returned."""
    return get_engine().policy_search(query, k)


@mcp.tool(annotations=RO)
def get_ar_model_card() -> dict:
    """Model card of the receivables payment-delay model (validation metrics vs baselines)."""
    return get_engine().model.card


@mcp.tool(annotations=RO)
def get_cfo_insight(topic: str) -> dict:
    """Evidence-backed answer to a CFO question. topic: trapped_cash | liquidity_risk | collections |
    releasable_liquidity | repay_vs_preserve | shocks | wc_actions | cfo_approvals."""
    from meridian.analytics.insights import TOPICS, answer

    if topic not in TOPICS:
        raise ValueError(f"unknown topic {topic}; valid: {sorted(TOPICS)}")
    return answer(get_engine(), topic)


@mcp.tool(annotations=RO)
def get_data_provenance() -> list[dict]:
    """Every data source feeding the analytics: classification, system, as-of, validation, freshness, checksum."""
    return get_engine().provenance()


@mcp.tool(annotations=RO)
def get_peer_benchmarks() -> dict:
    """Working-capital benchmarks (DSO/DPO/DIO) of real public peers from SEC EDGAR 10-K XBRL facts."""
    return get_engine().peers()


@mcp.tool(annotations=RO)
def get_relationships(node_id: str, depth: int = 2) -> dict:
    """Neighbourhood of a node in the treasury relationship graph (e.g. an entity id like 'E12')."""
    from meridian.analytics.relationships import neighborhood

    return neighborhood(get_engine().graph, node_id, depth)


@mcp.tool(annotations=RO)
def get_liquidity_overview() -> dict:
    """Liquidity waterfall (cash → trapped → buffers → deployable + undrawn RCF), 8-week historical bridge from
    bank-statement entries with FX translation, and bank-counterparty concentration vs TP-5.2."""
    e = get_engine()
    return {"waterfall": e.liquidity_waterfall(), "bridge": e.liquidity_bridge(), "concentration": e.concentration()}


@mcp.tool(annotations=RO)
def get_debt_profile() -> dict:
    """Debt stack with SOFR-based rates, maturity ladder, refinancing window (DP-2.1), fixed/floating mix,
    rate sensitivity, and covenant tests today and on the P10 path."""
    e = get_engine()
    return {"debt": e.debt_profile(), "covenants": e.covenants()}


@mcp.tool(annotations=RO)
def get_working_capital_drivers() -> dict:
    """DSO bridge (contributions by customer, entity, business unit, aging) and supplier-payment behaviour."""
    e = get_engine()
    return {"dso": e.dso_drivers(), "ap": e.ap_analysis()}


@mcp.tool(annotations=RO)
def get_risk_register() -> list[dict]:
    """Scored risk register (likelihood × impact) with drivers, evidence and owners."""
    return get_engine().risks()


@mcp.tool(annotations=RO)
def get_data_quality() -> dict:
    """Latest data-quality gate result for company data, plus stale or failed sources."""
    e = get_engine()
    ing = next(iter(e.store.find("ingestions", 5, sort_desc="created_at")), {}) or {}
    return {k: ing.get(k) for k in ("ingestion_id", "classification", "loaded", "errors", "warnings", "mode",
                                     "created_at", "counts")}


@mcp.tool(annotations=RO)
def get_metric_lineage(metric_id: str) -> dict:
    """Source → dataset → date → transformation → calculation for a headline metric."""
    e = get_engine()
    m = e.metrics().get(metric_id)
    if m is None:
        raise ValueError(f"unknown metric {metric_id}")
    return m


@mcp.resource("policy://treasury")
def treasury_policy() -> str:
    """Active policy-as-code (approval matrix, SoD, DLP, guardrails)."""
    return load_policy().model_dump_json(indent=2)


# ------------------------------------------------------------------------------ PUBLIC · REAL company (SEC filings)
def _pc(cik: int | None = None):
    from meridian.public_company.analytics import get_public_company

    return get_public_company(cik)


def _slim(metrics: dict, keys: list[str]) -> dict:
    return {k: {f: metrics[k][f] for f in ("label", "value", "unit", "basis", "period", "formula", "note")}
            for k in keys if k in metrics}


@mcp.tool(annotations=RO)
def get_public_data_quality(cik: int | None = None) -> dict:
    """PUBLIC · REAL: source register (SEC filings, market data), validation status and the data NOT available
    from public sources."""
    pc = _pc(cik)
    return {"company": pc.subject["name"], "provenance": pc.provenance(), "data_gaps": pc.data_gaps(),
            "latest_quarter": pc.latest_quarter, "latest_fiscal_year_end": pc.latest_fy, "debt_date": pc.debt_date}


@mcp.tool(annotations=RO)
def get_public_liquidity(cik: int | None = None) -> dict:
    """PUBLIC · REAL: cash, liquidity, debt due within 12 months, coverage, leverage and the maturity ladder."""
    pc = _pc(cik)
    return {"company": pc.subject["name"], "debt_date": pc.debt_date, "captive_finance": pc.captive,
            "metrics": _slim(pc.metrics, [
        "cash", "cash_fy", "credit_capacity", "credit_available", "liquidity_fy", "short_term_borrowings", "maturity_y1",
        "ltd_current",
        "ltd_noncurrent", "total_debt", "net_debt", "st_obligations", "liquidity_coverage", "ebitda_fy", "gross_leverage",
        "net_leverage", "interest_paid_fy", "interest_cover", "avg_cost_of_debt"]), "maturities": pc.maturities()}


@mcp.tool(annotations=RO)
def get_public_working_capital(cik: int | None = None) -> dict:
    """PUBLIC · REAL: receivables, inventory, payables, DSO/DIO/DPO/CCC trend, YoY bridge and peer context."""
    pc = _pc(cik)
    p = pc.peers()
    return {"company": pc.subject["name"], "captive_finance": pc.captive, "metrics": _slim(pc.metrics, [
        "receivables_trade", "inventory", "payables", "revenue_ttm", "cogs_ttm", "dso", "dio", "dpo", "ccc",
        "trade_working_capital"]), "trend": pc.trend()[-8:], "bridge": pc.wc_bridge(),
        "peers": {"median": p["median"], "subject": {k: p["subject"][k] for k in ("dso_days", "dio_days", "dpo_days")},
                  "caveat": p["caveat"]}}


@mcp.tool(annotations=RO)
def get_public_debt_and_rates(cik: int | None = None) -> dict:
    """PUBLIC · REAL: debt cost, repricing exposure, refinancing estimate and market rates (SOFR, UST, credit spreads)."""
    pc = _pc(cik)
    return {"company": pc.subject["name"], "metrics": _slim(pc.metrics, [
        "avg_cost_of_debt", "repricing_100bps", "refi_cost_delta", "derivative_notional", "st_obligations", "ebitda_ttm"]),
        "rates": {k: v for k, v in pc.rates.items() if k != "curve"}}


@mcp.tool(annotations=RO)
def get_public_cash_flow(cik: int | None = None) -> dict:
    """PUBLIC · REAL: operating cash flow, capex, FCF proxy, shareholder returns (TTM) and the seasonal cash outlook."""
    pc = _pc(cik)
    f = pc.forecast()
    return {"company": pc.subject["name"], "metrics": _slim(pc.metrics, [
        "ocf_ttm", "capex_ttm", "fcf_ttm", "dividends_ttm", "buybacks_ttm", "shareholder_returns_ttm", "ebitda_ttm",
        "revenue_ttm"]), "outlook": f["outlook"], "backtest_mape": f["mape"], "method": f["method"]}


@mcp.tool(annotations=RO)
def get_public_scenarios(cik: int | None = None) -> list[dict]:
    """PUBLIC · REAL inputs, SCENARIO outputs: demand, collections, inventory, rate and credit-spread shocks."""
    return [{k: s[k] for k in ("name", "impacts", "fcf_base_usd", "fcf_scenario_usd", "liquidity_coverage_base",
                               "liquidity_coverage_scenario", "gross_leverage_base", "gross_leverage_scenario")}
            for s in _pc(cik).scenarios()]
