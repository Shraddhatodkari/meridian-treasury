"""Connector registry — what can feed Meridian, what is configured in THIS deployment, and what is not.

Status values are computed, never asserted:
  ACTIVE           data from this connector is loaded right now (with as-of date)
  AVAILABLE        implemented and tested; not used by the current load
  SNAPSHOT         real public data served from a dated, checksummed snapshot (demo / CI reproducibility)
  NOT_CONFIGURED   interface contract only — no client for that system ships with Meridian
"""

from __future__ import annotations

CONNECTORS = [
    # id, category, name, standard, implementation, feeds
    ("erp.file_drop", "ERP / AR / AP / GL masters", "ERP extract — CSV package", "Meridian package spec v1",
     "meridian.ingest.pipeline", ["company.entities", "company.receivables", "company.payables",
                                  "company.counterparties", "company.recurring"]),
    ("bank.camt053", "Bank balances & transactions", "Bank statements — ISO 20022 camt.053", "ISO 20022 camt.053.001.08",
     "meridian.ingest.camt053", ["company.bank_balances", "company.bank_transactions"]),
    ("bank.bai2", "Bank balances & transactions", "Bank statements — BAI2 (North America)", "BAI2 v2",
     "meridian.ingest.bai2", []),
    ("tms.debt", "Debt facilities & covenants", "Facilities and covenants — CSV", "Meridian package spec v1",
     "meridian.ingest.pipeline", ["company.debt_facilities", "company.covenants", "company.facility"]),
    ("mkt.ecb_fx", "FX", "ECB euro reference rates (daily + 90-day history)", "ECB eurofxref XML",
     "meridian.market.connectors / rates", ["market.fx", "market.fx_history"]),
    ("mkt.nyfed", "Interest rates", "NY Fed SOFR and EFFR", "NY Fed Markets API", "meridian.market.rates",
     ["market.sofr", "market.sofr_history", "market.effr"]),
    ("mkt.ust", "Interest rates", "U.S. Treasury daily par yield curve", "Treasury CSV", "meridian.market.rates",
     ["market.ust_curve"]),
    ("mkt.estr", "Interest rates", "ECB euro short-term rate (€STR)", "ECB Data Portal SDMX", "meridian.market.rates",
     ["market.estr"]),
    ("mkt.fred", "Credit spreads", "FRED — ICE BofA BBB OAS", "FRED CSV", "meridian.market.rates",
     ["market.credit_spread"]),
    ("mkt.sec", "Peer benchmarks", "SEC EDGAR XBRL company facts", "SEC XBRL JSON", "meridian.market.connectors",
     ["market.peer_benchmarks"]),
    ("mkt.gdacs", "Risk events", "GDACS natural-hazard alerts", "GDACS RSS", "meridian.market.connectors",
     ["market.hazards"]),
    ("erp.api", "ERP / GL", "ERP API (SAP S/4HANA OData, Oracle Fusion REST, NetSuite SuiteQL)", "vendor APIs", None, []),
    ("bank.h2h", "Payment transmission", "Bank host-to-host / SWIFT FileAct for pain.001", "ISO 20022 pain.001", None, []),
    ("idp.oidc", "Identity", "Enterprise SSO (OIDC: Entra ID, Okta)", "OpenID Connect", None, []),
]


def connector_status(provenance: list[dict], market_mode: str, policy) -> list[dict]:
    prov = {p["source_id"]: p for p in provenance}
    out = []
    for cid, cat, name, std, impl, feeds in CONNECTORS:
        note = ""
        loaded = [prov[f] for f in feeds if f in prov]
        if impl is None:
            status = "NOT_CONFIGURED"
            note = "Interface contract only; no client ships with Meridian. Use the file-drop connectors."
            if cid == "bank.h2h":
                note = (f"Transmission channel: {policy.execution.transmission_channel}. Payment files stop at READY; "
                        "release happens in the bank portal / TMS under bank mandate.")
            if cid == "idp.oidc":
                note = "Local accounts with PBKDF2 + JWT are active; OIDC is on the roadmap."
        elif not loaded:
            status = "AVAILABLE"
            if cid == "bank.bai2":
                note = "Implemented and tested (ingest/bai2.py); the current load used camt.053 statements"
            note = note if cid == "bank.bai2" else "Implemented and tested; not used by the current data load"
        elif cid.startswith("mkt."):
            status = "SNAPSHOT" if market_mode == "snapshot" else "ACTIVE"
            note = "Dated, checksummed snapshot of real public data" if status == "SNAPSHOT" else "Live fetch at start-up"
        else:
            status = "ACTIVE"
            cls = {p["classification"] for p in loaded}
            note = "Evaluation data (SYNTHETIC) loaded through this connector's format" if "SYNTHETIC" in cls \
                else "Client data loaded"
        out.append({"connector_id": cid, "category": cat, "name": name, "standard": std, "implementation": impl,
                    "status": status, "feeds": feeds, "note": note,
                    "datasets": [{"source_id": p["source_id"], "as_of": p.get("as_of"), "freshness": p.get("freshness"),
                                  "validation": p.get("validation"), "classification": p.get("classification"),
                                  "records": p.get("record_count")} for p in loaded]})
    return out
