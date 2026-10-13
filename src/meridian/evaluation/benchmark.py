"""Evaluation harness + release gates (run in CI).   python -m meridian.evaluation.benchmark [--seed 11]

Measures every layer against ground truth or a labelled set, and fails the build if a gate is missed.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path

from meridian.config import PROJECT_ROOT

PLANNER_SET = [
    ("What if customers pay 30% slower?", {"collection_delay_pct": 0.3}),
    ("What if customers pay 20% later and rates rise 100 bps?", {"collection_delay_pct": 0.2, "rate_shock_bps": 100}),
    ("Stress: revenue drops 15% next quarter", {"billing_change_pct_all": -0.15}),
    ("What if the euro falls 10%?", {"fx_shock_pct": {"EUR": -0.1}}),
    ("What happens if the cyclone in China hits collections?", {"include_hazard_events": True}),
    ("Rates up 75 basis points — impact on liquidity?", {"rate_shock_bps": 75}),
    ("Who approves a revolver prepayment?", {"intent": "POLICY_QUESTION"}),
    ("Is the AI allowed to execute payments?", {"intent": "POLICY_QUESTION"}),
    ("What if sterling weakens 5% and sales decline 10%?", {"fx_shock_pct": {"GBP": -0.05}, "billing_change_pct_all": -0.1}),
    ("Run the daily liquidity position", {"intent": "SCENARIO"}),
]
RAG_SET = [
    ("who approves a revolver prepayment", {"RCF-8.1", "TP-2.1"}),
    ("minimum prepayment amount on the revolver", {"RCF-8.1"}),
    ("can we move cash out of China", {"CR-1"}),
    ("minimum liquidity covenant", {"RCF-22.1"}),
    ("how much can India upstream", {"CR-2"}),
    ("where can we invest surplus cash", {"TP-5.1"}),
    ("is the AI allowed to execute payments", {"TP-7.1"}),
    ("approval for intercompany transfer above 10 million", {"TP-2.1"}),
    ("operating cash buffer requirement for subsidiaries", {"TP-3.1"}),
    ("early payment supplier discount rule", {"TP-2.3"}),
]


def _gate(name, value, ok, target):
    return {"gate": name, "value": value, "target": target, "pass": bool(ok)}


async def run(seed: int) -> dict:
    os.environ["MERIDIAN_SEED"] = str(seed)
    os.environ["MERIDIAN_LLM_PROVIDER"] = os.getenv("MERIDIAN_LLM_PROVIDER", "mock")
    os.environ.pop("MERIDIAN_MONGODB_URI", None)
    from meridian.agents.reasoners import plan
    from meridian.analytics.engine import TreasuryEngine
    from meridian.config import get_settings
    from meridian.governance.audit import AuditLedger
    from meridian.nlp.rag import index_documents, retrieve
    from meridian.store.base import MemoryStore
    from meridian.store.factory import bootstrap, get_store

    get_settings.cache_clear()
    get_store.cache_clear()
    from meridian.analytics import engine as eng_mod

    eng_mod.get_engine.cache_clear()
    store = get_store()
    engine = eng_mod.get_engine()
    card, bt, opt = engine.model.card, engine.backtest(), engine.optimize()

    from meridian.orchestration.service import TreasuryService

    svc = TreasuryService(store)
    runs = [await svc.run(q, "eval") for q in (None, "What if customers pay 30% slower and rates rise 50 bps?",
                                                 "What happens if the cyclone in China and floods in India hit collections?")]
    grounded = sum(bool(r["grounding"] and r["grounding"]["grounded"] and not r["grounding"].get("fallback")) for r in runs)

    def plan_ok(q, exp):
        p = plan({"question": q}).model_dump()
        return all(p.get(k) == v for k, v in exp.items())
    planner_acc = sum(plan_ok(q, e) for q, e in PLANNER_SET) / len(PLANNER_SET)
    rag_hit = sum(bool({h["clause_id"] for h in retrieve(store, q, 3)} & exp) for q, exp in RAG_SET) / len(RAG_SET)
    quarantine = index_documents(MemoryStore())["quarantined"]
    ents = {e.entity_id: e for e in engine.g.entities}
    trapped_ok = all(t["amount_usd"] <= ents[t["from_entity"]].repatriation_allowance_usd + 1_000
                     for t in opt["transfers"] if t["to_entity"] == "E01" and ents[t["from_entity"]].capital_controls)
    _ = TreasuryEngine, bootstrap

    metrics = {
        "ar_mae_days_model": card["mae_days_model_p50"], "ar_mae_days_customer_avg": card["mae_days_customer_average"],
        "ar_mae_days_erp": card["mae_days_erp_due_date_assumption"],
        "ar_interval_coverage": card["p10_p90_coverage_conformal"],
        "collections_wape_model": bt["wape_model"], "collections_wape_erp": bt["wape_erp_due_date"],
        "optimizer_annualized_benefit_usd": opt["annualized_benefit_usd"],
        "optimizer_revolver_repay_usd": opt["revolver_repay_usd"],
        "optimizer_residual_shortfalls": len(opt["residual_shortfalls"]),
        "briefings_grounded": f"{grounded}/{len(runs)}", "planner_accuracy": planner_acc, "rag_hit_at_3": rag_hit,
        "injected_chunks_quarantined": len(quarantine),
        "audit_chain_valid": AuditLedger(store).verify()["valid"],
    }
    gates = [
        _gate("AR model beats customer-average baseline", card["mae_days_model_p50"],
              card["mae_days_model_p50"] < card["mae_days_customer_average"], "< baseline MAE"),
        _gate("Collections forecast WAPE", bt["wape_model"], bt["wape_model"] <= 0.15, "<= 15%"),
        _gate("Forecast beats ERP due-date assumption 3x", bt["wape_model"], bt["wape_model"] * 3 <= bt["wape_erp_due_date"],
              "3x lower error"),
        _gate("AR interval calibration", card["p10_p90_coverage_conformal"],
              0.72 <= card["p10_p90_coverage_conformal"] <= 0.90, "0.72-0.90 (nominal 0.80)"),
        _gate("Optimizer leaves no entity below buffer", len(opt["residual_shortfalls"]),
              not opt["residual_shortfalls"], "0"),
        _gate("Trapped cash respects repatriation allowances", trapped_ok, trapped_ok, "True"),
        _gate("Every briefing figure grounded in tool output", f"{grounded}/{len(runs)}", grounded == len(runs), "all"),
        _gate("Planner accuracy (labelled set)", planner_acc, planner_acc >= 0.9, ">= 0.9"),
        _gate("Policy retrieval hit@3 (labelled set)", rag_hit, rag_hit >= 0.8, ">= 0.8"),
        _gate("Injected document chunk quarantined", len(quarantine), len(quarantine) == 1, "1"),
        _gate("Audit chain valid", metrics["audit_chain_valid"], metrics["audit_chain_valid"], "True"),
    ]
    return {"seed": seed, "metrics": metrics, "gates": gates, "all_pass": all(g["pass"] for g in gates)}


def to_md(r: dict) -> str:
    lines = [f"# Meridian evaluation (seed {r['seed']})", "", "| Metric | Value |", "|---|---|"]
    lines += [f"| {k} | {v} |" for k, v in r["metrics"].items()]
    lines += ["", "| Release gate | Value | Target | Result |", "|---|---|---|---|"]
    lines += [f"| {g['gate']} | {g['value']} | {g['target']} | {'PASS' if g['pass'] else 'FAIL'} |" for g in r["gates"]]
    return "\n".join(lines) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()
    r = asyncio.run(run(args.seed))
    out = PROJECT_ROOT / "reports"
    out.mkdir(exist_ok=True)
    (out / f"eval_seed{args.seed}.json").write_text(json.dumps(r, indent=2, default=str), encoding="utf-8")
    (out / f"eval_seed{args.seed}.md").write_text(to_md(r), encoding="utf-8")
    print(to_md(r))
    raise SystemExit(0 if r["all_pass"] else 1)


if __name__ == "__main__":
    Path  # noqa: B018
    main()
