"""Data platform catalog: every dataset in the five layers, with lineage edges.

    RAW ──► VALIDATED ──► NORMALIZED ──► ANALYTICAL ──► DECISION

RAW         landed exactly as received (ERP extract files, bank statements, API payloads) with checksum
VALIDATED   passed the data-quality gate (schema, IBAN mod-97, referential integrity, business rules)
NORMALIZED  the financial domain model (entities, accounts, invoices, facilities, rates, …)
ANALYTICAL  derived data products (cash positions, forecast, DSO bridge, covenant tests, optimisation, …)
DECISION    recommendations, approvals and execution files

Each dataset carries its classification (SYNTHETIC / CLIENT_DATA / PUBLIC_MARKET_DATA / REFERENCE), so a
synthetic evaluation dataset can never be mistaken for client or public data anywhere downstream.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class Layer(str, Enum):
    RAW = "RAW"
    VALIDATED = "VALIDATED"
    NORMALIZED = "NORMALIZED"
    ANALYTICAL = "ANALYTICAL"
    DECISION = "DECISION"


class Dataset(BaseModel):
    dataset_id: str
    layer: Layer
    name: str
    classification: str
    system: str
    derived_from: list[str] = Field(default_factory=list)
    transformation: str = ""
    as_of: str | None = None
    record_count: int | None = None
    checksum: str | None = None
    validation: str | None = None
    owner: str = "Group Treasury"


# Analytical data products: id -> (name, inputs, transformation)
ANALYTICAL = {
    "analytical.cash_positions": ("Cash positions by entity, bank and currency",
                                  ["company.bank_balances", "company.entities", "market.fx"],
                                  "Sum closing balances per account → translate at ECB reference rates → aggregate"),
    "analytical.liquidity_bridge": ("Historical liquidity bridge (8 weeks)",
                                    ["company.bank_transactions", "company.bank_balances", "market.fx_history"],
                                    "Classify bank-statement entries by driver; FX translation at ECB rates of window start"),
    "analytical.liquidity_forecast": ("13-week probabilistic liquidity forecast",
                                      ["company.bank_balances", "company.receivables", "company.payables",
                                       "company.recurring", "company.debt_facilities", "model.ar_delay", "market.fx",
                                       "market.sofr"],
                                      "Direct-method Monte Carlo (400 paths) over ML-predicted payment dates"),
    "analytical.forecast_accuracy": ("Forecast vs actual (point-in-time back-test)", ["company.receivables",
                                                                                      "model.ar_delay"],
                                     "Rewind ledger 13 weeks, retrain without look-ahead, compare with settlements"),
    "analytical.dso_bridge": ("DSO driver decomposition", ["company.receivables", "company.counterparties",
                                                           "model.ar_delay"],
                              "Point-in-time DSO at t0/t1; contributions by customer, entity, BU, aging"),
    "analytical.ap_analysis": ("Supplier-payment behaviour", ["company.payables"],
                               "Paid-bill history: discounts captured/missed, early and late payments"),
    "analytical.covenant_tests": ("Covenant compliance and headroom", ["company.debt_facilities", "company.covenants",
                                                                       "analytical.liquidity_forecast",
                                                                       "analytical.cash_positions"],
                                  "Liquidity, net leverage and interest cover today and on the P10 path"),
    "analytical.debt_profile": ("Debt stack, maturities, refinancing and rate sensitivity",
                                ["company.debt_facilities", "market.sofr", "market.ust_curve", "market.credit_spread"],
                                "Floating rates = SOFR + margin; refinancing cost ESTIMATED from UST 5Y + BBB OAS"),
    "analytical.fx_exposure": ("Currency exposure and 1-month VaR", ["company.bank_balances", "company.receivables",
                                                                     "company.payables", "market.fx_history"],
                               "Net exposure × 1.645 × realised 90-day vol × √(21/252)"),
    "analytical.concentration": ("Bank-counterparty and entity concentration", ["company.bank_balances",
                                                                                 "company.banks"],
                                 "Share of group cash per bank vs TP-5.2 limits; entity HHI"),
    "analytical.optimization": ("Liquidity optimisation (LP)", ["analytical.liquidity_forecast", "company.entities",
                                                               "company.debt_facilities", "reference.policies"],
                                "HiGHS linear programme with policy constraints; duals report binding constraints"),
    "analytical.scenarios": ("Stress scenarios", ["analytical.liquidity_forecast", "market.hazards"],
                             "Re-forecast with collections, billing, rate, FX and hazard shocks"),
    "analytical.risk_register": ("Risk register", ["analytical.liquidity_forecast", "analytical.covenant_tests",
                                                   "analytical.debt_profile", "analytical.concentration",
                                                   "analytical.fx_exposure", "analytical.dso_bridge"],
                                 "Rule-based likelihood × impact scoring (documented thresholds)"),
}
DECISION = {
    "decision.recommendations": ("Recommendations", ["analytical.optimization", "analytical.dso_bridge",
                                                     "analytical.debt_profile", "analytical.concentration",
                                                     "analytical.ap_analysis", "reference.policies"],
                                 "Decision agent: verified findings → recommendations with impact, evidence, policy"),
    "decision.approvals": ("Approvals and execution files", ["decision.recommendations"],
                           "Approval matrix TP-2.1, SoD TP-2.2; pain.001 DRAFT → READY; no autonomous transmission"),
}


def build_catalog(provenance: list[dict], classification: str, ingestion: dict | None) -> list[dict]:
    """Assemble the five-layer catalog from the provenance register (normalized layer) and the static
    definitions of analytical and decision products."""
    out: list[Dataset] = []
    company = [p for p in provenance if p["source_id"].startswith("company.")]
    validated_id = "validated.company_package"
    for p in provenance:
        sid = p["source_id"]
        raw_id = f"raw.{sid}"
        out.append(Dataset(dataset_id=raw_id, layer=Layer.RAW, name=f"{p['name']} — as received",
                           classification=p["classification"], system=p.get("system", ""), as_of=p.get("as_of"),
                           record_count=p.get("record_count"), checksum=(p.get("checksum_sha256") or "")[:16],
                           transformation="Landed unchanged (file / API payload); checksum recorded"))
        parent = validated_id if sid.startswith("company.") else raw_id
        out.append(Dataset(dataset_id=sid, layer=Layer.NORMALIZED, name=p["name"], classification=p["classification"],
                           system=p.get("system", ""), derived_from=[parent], as_of=p.get("as_of"),
                           record_count=p.get("record_count"), checksum=(p.get("checksum_sha256") or "")[:16],
                           validation=p.get("validation"),
                           transformation="Parsed into the financial domain model (typed, referentially linked)"))
    if company:
        checks = (ingestion or {}).get("checks", [])
        out.append(Dataset(
            dataset_id=validated_id, layer=Layer.VALIDATED, name="Company data package — data-quality gate",
            classification=classification, system="Meridian ingestion pipeline",
            derived_from=[f"raw.{p['source_id']}" for p in company], as_of=company[0].get("as_of"),
            validation="PASSED" if ingestion and not ingestion.get("errors") else (
                "FAILED" if ingestion else "NOT_RUN"),
            record_count=sum(p.get("record_count") or 0 for p in company),
            transformation=f"Schema, IBAN mod-97, referential integrity, business rules; {len(checks)} findings "
                           f"({(ingestion or {}).get('errors', 0)} errors) — atomic load or reject"))
    known = {d.dataset_id for d in out}
    for did, (name, inputs, how) in {**ANALYTICAL, **DECISION}.items():
        layer = Layer.DECISION if did.startswith("decision.") else Layer.ANALYTICAL
        ins = [i for i in inputs if i in known or i.startswith(("analytical.", "decision."))]
        cls = {next((d.classification for d in out if d.dataset_id == i), None) for i in ins} - {None}
        out.append(Dataset(dataset_id=did, layer=layer, name=name, derived_from=ins, transformation=how,
                           classification=classification if "SYNTHETIC" in cls or classification in cls else
                           (sorted(cls)[0] if cls else classification), system="Meridian analytics"))
    return [d.model_dump(mode="json") for d in out]


def upstream(catalog: list[dict], dataset_id: str) -> list[dict]:
    """All ancestors of a dataset (lineage walk), nearest first."""
    by_id = {d["dataset_id"]: d for d in catalog}
    seen, order, frontier = set(), [], [dataset_id]
    while frontier:
        nxt = []
        for did in frontier:
            for p in by_id.get(did, {}).get("derived_from", []):
                if p not in seen:
                    seen.add(p)
                    order.append(by_id.get(p, {"dataset_id": p, "layer": "UNKNOWN", "name": p}))
                    nxt.append(p)
        frontier = nxt
    return order


def downstream(catalog: list[dict], dataset_id: str) -> list[dict]:
    seen, order, frontier = set(), [], [dataset_id]
    while frontier:
        nxt = []
        for did in frontier:
            for d in catalog:
                if did in d.get("derived_from", []) and d["dataset_id"] not in seen:
                    seen.add(d["dataset_id"])
                    order.append(d)
                    nxt.append(d["dataset_id"])
        frontier = nxt
    return order
