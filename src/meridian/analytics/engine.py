"""TreasuryEngine — one facade over data, provenance, ML, forecasting, optimisation, graph and retrieval.

All numbers in the platform come from here (deterministic and reproducible). Agents interpret them.
"""

from __future__ import annotations

import hashlib
import json
from functools import lru_cache

from meridian import __version__
from meridian.analytics import treasury as tr
from meridian.analytics import working_capital as wc
from meridian.analytics.backtest import backtest_point_in_time
from meridian.analytics.forecast import ForecastResult, run_forecast
from meridian.analytics.optimizer import optimize
from meridian.analytics.relationships import build_graph
from meridian.analytics.scenarios import PRESET_SCENARIOS, Scenario
from meridian.config import PACKAGE_ROOT, get_settings
from meridian.governance.policy import load_policy
from meridian.market.service import apply_market, load_market
from meridian.ml.ar_model import MODEL_VERSION, ARDelayModel, load_or_train
from meridian.nlp.rag import retrieve
from meridian.provenance import Classification, SourceRecord, ValidationStatus, checksum, now_utc
from meridian.store.factory import get_store


class TreasuryEngine:
    def __init__(self, store=None):
        self.settings = get_settings()
        self.store = store or get_store()
        self.g = self.store.load_group()
        if self.g is None:
            raise RuntimeError("No company data loaded. Run `meridian ingest --dir <package>` (production) "
                               "or start in demo mode.")
        self.policy = load_policy()
        self._ensure_debt_stack()
        self.market = load_market(self.settings, self.store, self.g)
        self.market_notes = apply_market(self.g, self.market, self.settings)
        self._cache: dict[str, object] = {}
        bundle = load_or_train(self.g.receivables, self.g.customers, self.g.as_of, fx=self.g.fx, store=self.store)
        self.model = ARDelayModel(bundle, self.g.receivables, self.g.customers, fx=self.g.fx)
        self._fc: dict[str, ForecastResult] = {}
        self._backtest: dict | None = None
        self._register_sources()
        self._quality_check()
        self.graph = build_graph(self.g, self.store.list_runs(20))
        self.store.put("graph", self.graph)

    # ------------------------------------------------------------------ provenance
    def _register_sources(self) -> None:
        for s in self.market.sources:
            self.store.put("data_sources", s.model_dump(mode="json"))
        if self.g.classification == "SYNTHETIC":
            # Same source ids as a production ingestion, so lineage and confidence logic are identical.
            g = self.g
            for sid, name, n in [
                ("company.entities", "Legal entity master", len(g.entities)),
                ("company.bank_balances", "Bank closing balances", len(g.accounts)),
                ("company.receivables", "Accounts receivable (open + settled history)", len(g.receivables)),
                ("company.payables", "Accounts payable (open)", len(g.payables)),
                ("company.counterparties", "Customer master", len(g.customers)),
                ("company.recurring", "Recurring flow schedules", len(g.recurring)),
                ("company.facility", "Committed facilities & covenants", 1),
                ("company.bank_transactions", "Bank-statement entries (8 weeks)", len(g.bank_transactions)),
                ("company.debt_facilities", "Debt facilities", len(g.debt_facilities)),
                ("company.covenants", "Financial covenants", len(g.covenants)),
                ("company.banks", "Bank counterparties", len(g.banks))]:
                self.store.put("data_sources", SourceRecord(
                    source_id=sid, name=name, system=f"Meridian simulator (deterministic, seed {self.settings.seed})",
                    classification=Classification.SYNTHETIC, retrieved_at=now_utc(), as_of=g.as_of, record_count=n,
                    checksum_sha256=checksum([sid, n, str(g.as_of), self.settings.seed]),
                    validation=ValidationStatus.PASSED, freshness_sla_hours=48 if sid != "company.entities" else None,
                    notes="SYNTHETIC demo company — not real financial data.").model_dump(mode="json"))
        docs = sorted((PACKAGE_ROOT / "data" / "documents").glob("*.md"))
        self.store.put("data_sources", SourceRecord(
            source_id="reference.policies", name="Treasury policy, facility agreement, repatriation rules, contracts",
            system="Document repository", classification=Classification.REFERENCE, retrieved_at=now_utc(),
            record_count=len(docs), checksum_sha256=checksum(b"".join(p.read_bytes() for p in docs)),
            validation=ValidationStatus.PASSED_WITH_WARNINGS,
            issues=["1 clause quarantined for prompt injection (see governance)"],
            notes="Fictional documents in demo; replace with the client's executed agreements").model_dump(mode="json"))
        c = self.model.card
        self.store.put("data_sources", SourceRecord(
            source_id="model.ar_delay", name=f"Payment-delay model {c['model_version']}", system="Meridian model registry",
            classification=Classification.REFERENCE, retrieved_at=now_utc(), as_of=self.g.as_of,
            record_count=c["train_rows"], checksum_sha256=(c.get("data_fingerprint") or "").split(":")[-1] or None,
            validation=ValidationStatus.PASSED if c["mae_days_model_p50"] < c["mae_days_customer_average"]
            else ValidationStatus.FAILED,
            notes=f"MAE {c['mae_days_model_p50']}d vs {c['mae_days_customer_average']}d baseline; "
                  f"coverage {c['p10_p90_coverage_conformal']}").model_dump(mode="json"))

    def _ensure_debt_stack(self) -> None:
        """Client packages without debt_facilities.csv still get a debt stack derived from facility.json."""
        from meridian.domain.models import Covenant, DebtFacility

        g, f = self.g, self.g.facility
        if f and not g.debt_facilities:
            from datetime import date as _d

            g.debt_facilities = [DebtFacility(facility_id=f.facility_id, name=f.name, type="RCF", borrower_entity=next(
                e.entity_id for e in g.entities if e.role == "HOLDCO"), limit_usd=f.limit_usd, drawn_usd=f.drawn_usd,
                rate_type="FLOATING" if f.margin_bps is not None else "FIXED", margin_bps=f.margin_bps, rate=f.rate,
                maturity_date=_d(g.as_of.year + 3, 6, 30), prepayable=True, prepay_min_usd=5e6, prepay_multiple_usd=1e6,
                clause_ref="RCF-8.1")]
        if f and not g.covenants:
            g.covenants = [Covenant(covenant_id="COV-LIQ", facility_id=f.facility_id, name="Minimum liquidity",
                                    metric="min_liquidity", threshold=f.min_liquidity_covenant_usd, direction=">=",
                                    clause_ref="RCF-22.1"),
                           Covenant(covenant_id="COV-LEV", facility_id=f.facility_id, name="Maximum net leverage",
                                    metric="max_net_leverage", threshold=f.max_net_leverage, direction="<=",
                                    clause_ref="RCF-22.2")]

    def _quality_check(self) -> None:
        """Run the production data-quality gate over the loaded company data. For the synthetic evaluation company
        this is a dry run (the data is exported to the ingestion format and validated, never re-loaded)."""
        if self.store.find("ingestions", 1):
            return
        if self.g.classification != "SYNTHETIC":
            return
        import tempfile
        from pathlib import Path

        from meridian.ingest.pipeline import export_package, validate_and_build

        with tempfile.TemporaryDirectory() as d:
            export_package(self.g, Path(d))
            _, rep = validate_and_build(Path(d))
        doc = rep.to_dict()
        doc.update({"loaded": False, "mode": "validation dry-run of the SYNTHETIC evaluation dataset",
                    "ingestion_id": f"DRYRUN-{doc['ingestion_id']}"})
        self.store.put("ingestions", doc)

    # ------------------------------------------------------------------ treasury analytics (cached)
    def _c(self, key: str, fn):
        if key not in self._cache:
            self._cache[key] = fn()
        return self._cache[key]

    def liquidity_waterfall(self) -> dict:
        return self._c("lw", lambda: tr.liquidity_waterfall(self.g, self.forecast_summary(), self.policy))

    def liquidity_bridge(self) -> dict:
        return self._c("lb", lambda: tr.liquidity_bridge(self.g, self.market.fx_history))

    def forward_bridge(self, scenario: Scenario | None = None) -> dict:
        return tr.forward_bridge(self.forecast_summary(scenario))

    def debt_profile(self) -> dict:
        return self._c("debt", lambda: tr.debt_profile(self.g, self.market, self.policy))

    def covenants(self, scenario: Scenario | None = None, ebitda_change_pct: float = 0.0) -> dict:
        if scenario is None and not ebitda_change_pct:
            return self._c("cov", lambda: tr.covenant_tests(self.g, self.kpis(), self.forecast_summary(),
                                                            self.debt_profile(), self.policy))
        return tr.covenant_tests(self.g, self.kpis(), self.forecast_summary(scenario), self.debt_profile(), self.policy,
                                 ebitda_change_pct)

    def dso_drivers(self) -> dict:
        return self._c("dso", lambda: tr.dso_drivers(self.g, self.model))

    def ap_analysis(self) -> dict:
        return self._c("ap", lambda: tr.ap_analysis(self.g, self.g.facility.rate))

    def fx_exposure(self) -> dict:
        return self._c("fx", lambda: tr.currency_exposure(self.g, self.market.fx_history))

    def concentration(self) -> dict:
        return self._c("conc", lambda: tr.concentration(self.g, self.policy))

    def risks(self) -> list[dict]:
        pending = sum(1 for r in self.store.list_runs(50) for a in r.get("actions", []) if a["status"] in
                      {"PROPOSED", "SUBMITTED"})
        return tr.risk_register(self.g, self.forecast_summary(), self.covenants(), self.debt_profile(),
                                self.concentration(), self.fx_exposure(), self.dso_drivers(), self.ap_analysis(),
                                self.provenance(), pending)

    def metrics(self) -> dict:
        from meridian.analytics.metrics import build_metrics

        return self._c("metrics", lambda: build_metrics(self))

    def catalog(self) -> list[dict]:
        from meridian.platform.catalog import build_catalog

        ing = next(iter(self.store.find("ingestions", 5, sort_desc="created_at")), None)
        return build_catalog(self.provenance(), self.g.classification, ing)

    def connectors(self) -> list[dict]:
        from meridian.platform.connectors import connector_status

        return connector_status(self.provenance(), self.market.mode, self.policy)

    def provenance(self) -> list[dict]:
        ref_date = self.g.as_of if self.settings.is_demo else None
        out = []
        for d in self.store.find("data_sources"):
            rec = SourceRecord(**d)
            out.append({**d, "freshness": rec.freshness(reference_date=ref_date), "age_hours": rec.age_hours()})
        return sorted(out, key=lambda d: d["source_id"])

    def reproducibility(self, scenario: Scenario | None = None) -> dict:
        pol = load_policy()
        pol_raw = self.settings.policy_path.read_bytes()
        return {"platform_version": __version__, "mode": self.settings.mode, "classification": self.g.classification,
                "tenant": self.g.tenant, "as_of": str(self.g.as_of), "seed": self.settings.seed,
                "monte_carlo_paths": self.settings.monte_carlo_paths, "forecast_weeks": self.settings.forecast_weeks,
                "policy_version": pol.version, "policy_sha256": hashlib.sha256(pol_raw).hexdigest()[:16],
                "model_id": self.model.card.get("model_id"), "model_version": MODEL_VERSION,
                "llm": {"provider": self.settings.llm_provider,
                        "model": self.settings.llm_model if self.settings.llm_provider != "mock" else
                        "deterministic offline reasoner"},
                "embedder": self.settings.embedder, "market_data": self.market.mode,
                "scenario": (scenario or Scenario()).model_dump(),
                "sources": [{"source_id": d["source_id"], "checksum": (d.get("checksum_sha256") or "")[:16],
                             "as_of": d.get("as_of"), "validation": d.get("validation")} for d in self.provenance()]}

    # ------------------------------------------------------------------ core analytics
    def forecast(self, scenario: Scenario | None = None) -> ForecastResult:
        scenario = scenario or Scenario()
        key = scenario.model_dump_json()
        if key not in self._fc:
            self._fc[key] = run_forecast(self.g, self.model, scenario, weeks=self.settings.forecast_weeks,
                                         paths=self.settings.monte_carlo_paths, seed=self.settings.seed)
        return self._fc[key]

    def kpis(self) -> dict:
        k = wc.kpis(self.g)
        k["revolver"]["rate_source"] = self.g.facility.rate_source
        k["classification"] = self.g.classification
        return k

    def forecast_summary(self, scenario: Scenario | None = None) -> dict:
        return self.forecast(scenario).summary()

    def optimize(self, scenario: Scenario | None = None) -> dict:
        res = optimize(self.g, self.forecast(scenario)).to_dict()
        rv = load_policy().revolver          # RCF-8.1: prepay >= USD 5M in multiples of USD 1M
        rep = (res["revolver_repay_usd"] // rv.prepay_multiple_usd) * rv.prepay_multiple_usd
        res["revolver_repay_usd"] = rep if rep >= rv.prepay_min_usd else 0
        res["revolver_repay_note"] = (f"Rounded per {rv.policy_ref} (min ${rv.prepay_min_usd:,.0f}, multiples of "
                                      f"${rv.prepay_multiple_usd:,.0f})")
        res["revolver_prepay_terms"] = {"policy_ref": rv.policy_ref, "min_usd": rv.prepay_min_usd,
                                        "multiple_usd": rv.prepay_multiple_usd}
        res["annualized_interest_saving_from_repay_usd"] = round(
            res["revolver_repay_usd"] * (self.g.facility.rate - self._hub().deposit_yield))
        res["sweep_total_usd"] = round(sum(t["amount_usd"] for t in res["transfers"] if t["to_entity"] == self._hub().entity_id))
        res["funding_total_usd"] = round(sum(t["amount_usd"] for t in res["transfers"] if t["from_entity"] == self._hub().entity_id))
        res["revolver_rate"] = self.g.facility.rate
        res["revolver_rate_source"] = self.g.facility.rate_source
        return res

    def collections(self, top_n: int = 15) -> list[dict]:
        return wc.collections_priorities(self.g, self.model, top_n)

    def early_pay(self, scenario: Scenario | None = None) -> dict:
        return wc.early_pay_opportunities(self.g, self.forecast(scenario))

    def scenario_compare(self, scenarios: list[Scenario]) -> list[dict]:
        base = self.forecast().summary()
        out = []
        for sc in scenarios:
            s = self.forecast(sc).summary()
            out.append({"scenario": s["scenario"],
                        "min_covenant_headroom_p10_usd": s["min_covenant_headroom_p10_usd"],
                        "headroom_change_vs_base_usd": s["min_covenant_headroom_p10_usd"] - base["min_covenant_headroom_p10_usd"],
                        "covenant_breach_week": s["covenant_breach_week"],
                        "week13_group_cash_p50_usd": s["group_cash_usd"]["p50"][-1],
                        "week13_cash_change_vs_base_usd": round(s["group_cash_usd"]["p50"][-1]
                                                                - base["group_cash_usd"]["p50"][-1]),
                        "entities_below_buffer": [e["entity_id"] for e in s["entities"] if e["weeks_p50_below_buffer"]]})
        return out

    def policy_search(self, query: str, k: int = 4) -> list[dict]:
        return [{"clause_id": h["clause_id"], "clause_title": h["clause_title"], "document": h["document"],
                 "source": h.get("source"), "text": h["text"], "rrf_rank": h.get("rrf_rank"),
                 "relevance": h.get("relevance"), "relevant": h.get("relevant"), "confidence": h.get("confidence"),
                 "doc_version": h.get("doc_version"), "effective": h.get("effective"), "retrieved_at": h.get("retrieved_at"),
                 "citation": h.get("citation"), "keyword_score": h.get("keyword_score")}
                for h in retrieve(self.store, query, k)]

    def risk_events(self) -> list[dict]:
        country_of = {e.entity_id: e.country for e in self.g.entities}
        exposure: dict[str, float] = {}
        for r in self.g.receivables:
            if r.settled_date is None:
                c = country_of[r.entity_id]
                exposure[c] = exposure.get(c, 0.0) + r.amount * self.g.fx.get(r.currency, 1.0)
        return [{**ev.model_dump(mode="json"), "open_ar_exposure_usd": round(exposure.get(ev.country, 0.0))}
                for ev in self.g.risk_events]

    def backtest(self) -> dict:
        if self._backtest is None:
            self._backtest = backtest_point_in_time(self.g, self.store, weeks=self.settings.forecast_weeks)
        return self._backtest

    def peers(self) -> dict:
        k = self.kpis()
        peers = self.market.peers or []
        dsos = sorted(p["dso_days"] for p in peers)
        med = dsos[len(dsos) // 2] if dsos else None
        daily_rev = sum(r.amount * self.g.fx.get(r.currency, 1.0) for r in self.g.receivables
                        if (self.g.as_of - r.issue_date).days <= 90) / 90
        gap = (k["dso_days"] - med) if med is not None else None
        return {"peers": peers, "peer_median_dso_days": med, "group_dso_days": k["dso_days"],
                "dso_gap_days": round(gap, 1) if gap is not None else None,
                "cash_release_at_peer_median_usd": round(max(gap, 0) * daily_rev) if gap is not None else None,
                "group_classification": self.g.classification,
                "peer_source": "SEC EDGAR XBRL (10-K)", "note": "Peers are real public filers; compare like-for-like "
                "receivable definitions before using in a client recommendation."}

    def entities(self) -> list[dict]:
        bal: dict[str, float] = {}
        for a in self.g.accounts:
            bal[a.entity_id] = bal.get(a.entity_id, 0.0) + a.balance
        return [{**e.model_dump(), "balance_local": round(bal.get(e.entity_id, 0.0)),
                 "balance_usd": round(bal.get(e.entity_id, 0.0) * self.g.fx.get(e.currency, 1.0))}
                for e in self.g.entities]

    def market_overview(self) -> dict:
        m = self.market
        return {"mode": m.mode, "fx": m.fx, "sofr": m.sofr, "effr": m.effr, "ust_curve": m.ust_curve,
                "sofr_history": m.sofr_history, "estr": (m.estr or [])[-30:], "credit_spread": m.credit_spread and {
                    "latest": m.credit_spread["latest"], "name": m.credit_spread["name"],
                    "history": m.credit_spread["history"][-260:]},
                "fx_volatility": (m.fx_history or {}).get("volatility")}

    def refresh_graph(self) -> dict:
        self.graph = build_graph(self.g, self.store.list_runs(20))
        self.store.put("graph", self.graph)
        return self.graph

    def _hub(self):
        return next(e for e in self.g.entities if e.role == "HOLDCO")


@lru_cache
def get_engine() -> TreasuryEngine:
    return TreasuryEngine()


def scenario_from_any(spec: str | dict | None) -> Scenario:
    if spec is None or spec == "":
        return Scenario()
    if isinstance(spec, str):
        if spec in PRESET_SCENARIOS:
            return PRESET_SCENARIOS[spec]
        spec = json.loads(spec)
    return Scenario(**spec)
