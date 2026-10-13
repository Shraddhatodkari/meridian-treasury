"""Smoke test: every endpoint and field the Treasury Console relies on, against a running demo-mode API.

MERIDIAN_URL=http://127.0.0.1:8000 python scripts/smoke_api_contract.py
"""

import os

import httpx

B = os.getenv("MERIDIAN_URL", "http://127.0.0.1:8000")
c = httpx.Client(base_url=B, timeout=300)
tok = c.post("/v1/auth/login", json={"username": "carla", "password": "Meridian-Demo-2026"}).json()["access_token"]
c.headers["Authorization"] = f"Bearer {tok}"


def need(path, obj, keys):
    o = obj[0] if isinstance(obj, list) and obj else obj
    miss = [k for k in keys if not (isinstance(o, dict) and k in o)]
    print(("OK  " if not miss else "MISS"), path, miss if miss else "")




def g(p):
    return c.get(p).json()


k = g("/v1/kpis")
need(
    "kpis",
    k,
    [
        "as_of",
        "entities",
        "bank_accounts",
        "currencies",
        "cash_usd",
        "liquidity_usd",
        "min_liquidity_covenant_usd",
        "trapped_cash_usd",
        "dso_days",
        "open_ar_usd",
        "overdue_ar_usd",
        "ar_aging_usd",
        "open_ap_usd",
        "dpo_days",
    ],
)
fc = g("/v1/forecast")
need(
    "forecast",
    fc,
    [
        "week_starts",
        "liquidity_usd",
        "group_cash_usd",
        "covenant_min_liquidity_usd",
        "min_covenant_headroom_p10_usd",
        "covenant_breach_week",
        "entities",
        "flows_p50_usd",
        "scenario",
    ],
)
need(
    "forecast.entities",
    fc["entities"],
    ["entity_id", "p10_trough_usd", "buffer_usd", "shortfall_vs_buffer_usd", "weeks_p50_below_buffer"],
)
e = g("/v1/entities")
need(
    "entities",
    e,
    [
        "entity_id",
        "name",
        "country",
        "currency",
        "balance_local",
        "balance_usd",
        "capital_controls",
        "repatriation_allowance_usd",
    ],
)
need("accounts", g("/v1/accounts"), ["account_id", "entity_id", "bank", "currency", "iban", "balance"])
need("fc entity", g(f"/v1/forecast/entities/{e[0]['entity_id']}"), ["week_starts", "cash_usd", "buffer_usd"])
need("scenarios", g("/v1/scenarios"), ["comparison"])
need(
    "scen.comp",
    g("/v1/scenarios")["comparison"],
    [
        "scenario",
        "min_covenant_headroom_p10_usd",
        "headroom_change_vs_base_usd",
        "covenant_breach_week",
        "entities_below_buffer",
    ],
)
need(
    "collections",
    g("/v1/working-capital/collections?top_n=5"),
    [
        "invoice_id",
        "customer",
        "rating",
        "amount_usd",
        "due_date",
        "predicted_days_late_p50",
        "predicted_days_late_p90",
        "disputed",
        "financing_cost_of_delay_usd",
        "recommended_action",
    ],
)
ep = g("/v1/working-capital/early-pay")
need("earlypay", ep, ["cost_of_funds", "total_saving_usd", "selected", "cash_accelerated_usd", "skipped_for_liquidity"])
if ep["selected"]:
    need(
        "ep.sel",
        ep["selected"],
        [
            "bill_id",
            "entity_id",
            "supplier_id",
            "amount_usd",
            "discount_pct",
            "pay_by",
            "implied_annual_return",
            "saving_usd",
        ],
    )
if ep["skipped_for_liquidity"]:
    need("ep.skip", ep["skipped_for_liquidity"], ["reason"])
bt = g("/v1/backtest")
need(
    "backtest",
    bt,
    [
        "wape_model",
        "wape_erp_due_date",
        "method",
        "t0",
        "open_invoices_at_t0",
        "p10_p90_weekly_coverage",
        "actual_usd",
        "model_p10_usd",
        "model_p90_usd",
        "model_mean_usd",
        "erp_due_date_usd",
    ],
)
p = g("/v1/benchmarks/peers")
need(
    "peers",
    p,
    [
        "peers",
        "group_classification",
        "group_dso_days",
        "peer_median_dso_days",
        "dso_gap_days",
        "cash_release_at_peer_median_usd",
        "note",
    ],
)
need(
    "peers.p",
    p["peers"],
    [
        "company",
        "fiscal_year_end",
        "revenue_usd",
        "dso_days",
        "dpo_days",
        "dio_days",
        "ccc_days",
        "accession",
        "source_url",
    ],
)
for t in [
    "trapped_cash",
    "liquidity_risk",
    "releasable_liquidity",
    "repay_vs_preserve",
    "shocks",
    "wc_actions",
    "collections",
    "cfo_approvals",
]:
    r = c.get(f"/v1/insights/{t}")
    if r.status_code != 200:
        print("FAIL insight", t, r.status_code, r.text[:200])
        continue
    a = r.json()
    need(
        "insight " + t,
        a,
        ["question", "headline", "confidence", "confidence_reasons", "figures", "detail", "evidence", "limitations"],
    )
    need(" ev " + t, a["evidence"], ["data_sources", "analysis", "model_id", "policy_refs", "policy_version"])
sh = g("/v1/insights/shocks")
need("shocks.detail", sh["detail"], ["scenario", "headroom_change_vs_base_usd"])
tr = g("/v1/insights/trapped_cash")
need("trapped.fig", tr["figures"], ["trapped_total_usd", "repatriable_now_usd"])
co = g("/v1/insights/collections")
need("coll.fig", co["figures"], ["amount_usd", "invoices"])
gr = g("/v1/graph")
need("graph", gr, ["nodes", "edges", "counts", "hazard_paths"])
need("graph.n", gr["nodes"], ["id", "kind", "label"])
need("graph.e", gr["edges"], ["source", "target", "rel"])
if gr["hazard_paths"]:
    need(
        "hazard", gr["hazard_paths"], ["event", "country", "classification", "entities", "ar_exposed_top_customers_usd"]
    )
print("graph counts", gr["counts"])
nid = gr["nodes"][0]["id"]
need("graph node", g(f"/v1/graph?node={nid}&depth=1"), ["edges"])
need(
    "policy search",
    g("/v1/policy/search?q=revolver&k=2"),
    ["clause_id", "clause_title", "document", "rrf_rank", "text"],
)
pol = g("/v1/governance/policy")
need("gov.policy", pol, ["version", "approval_matrix", "segregation_of_duties", "autonomous_execution"])
need("matrix", pol["approval_matrix"], ["action", "approver_role", "policy_ref"])
q = g("/v1/governance/quarantine")
print("quarantine", len(q), q[0].keys() if q else "")
need(
    "prov",
    g("/v1/governance/provenance"),
    [
        "source_id",
        "name",
        "classification",
        "system",
        "as_of",
        "retrieved_at",
        "freshness",
        "validation",
        "record_count",
        "checksum_sha256",
        "issues",
        "notes",
    ],
)
print("ingestions", g("/v1/governance/ingestions")[:1])
rd = g("/v1/system/readiness")
need("readiness", rd, ["ready", "checks"])
need("checks", rd["checks"], ["status", "check", "detail", "remediation"])
need(
    "agents",
    g("/v1/governance/agents"),
    ["order", "name", "responsibility", "tools", "output_schema", "policy_constraints", "validation", "can_move_money"],
)
need(
    "modelcard",
    g("/v1/governance/model-card"),
    [
        "model_version",
        "algorithm",
        "model_id",
        "train_rows",
        "validation_window",
        "mae_days_model_p50",
        "mae_days_customer_average",
        "mae_days_erp_due_date_assumption",
        "p10_p90_coverage_conformal",
        "p10_p90_coverage_raw",
        "intended_use",
    ],
)
print("repro keys", list(g("/v1/governance/reproducibility").keys()))
need(
    "ready",
    g("/ready"),
    [
        "mode",
        "classification",
        "market_data",
        "llm_provider",
        "llm_model",
        "store",
        "as_of",
        "version",
        "policy_version",
        "model_id",
        "tenant",
    ],
)
need("me", g("/v1/auth/me"), ["display_name", "role", "auth"])
r = c.post("/v1/runs", json={"question": None})
print("run", r.status_code)
run = r.json()
need(
    "run",
    run,
    [
        "run_id",
        "intent",
        "status",
        "trace",
        "findings",
        "briefing",
        "forecast",
        "actions",
        "grounding",
        "reproducibility",
        "requested_by",
        "classification",
        "completion_audit_hash",
    ],
)
need(
    "finding",
    run["findings"],
    [
        "agent",
        "confidence",
        "validation",
        "headline",
        "key_points",
        "risks",
        "provenance",
        "policy_refs",
        "limitations",
    ],
)
need(
    "briefing",
    run["briefing"],
    ["executive_summary", "decisions_required", "key_numbers", "risks", "citations", "confidence", "llm_provider"],
)
need(
    "action",
    run["actions"],
    ["action_id", "type", "amount_usd", "status", "required_approver", "rationale", "evidence", "from_entity"],
)
need(
    "repro",
    run["reproducibility"],
    [
        "mode",
        "classification",
        "policy_version",
        "policy_sha256",
        "model_id",
        "llm",
        "seed",
        "monte_carlo_paths",
        "market_data",
    ],
)
need("trace", run["trace"], ["actor", "action", "latency_ms"])
need("runs list", g("/v1/runs"), ["run_id", "created_at", "question", "status", "actions"])
qq = g("/v1/approvals/queue")
need("queue", qq, ["run_id", "action_id", "type", "amount_usd", "required_approver", "can_approve", "blocked_reason"])
for qn in [
    "Where is cash trapped?",
    "Who approves a revolver prepayment?",
    "What if customers pay 30% slower and rates rise 50 bps?",
]:
    r = c.post("/v1/runs", json={"question": qn}).json()
    print(qn, "->", r["intent"], r["status"], "insight" in r and bool(r.get("insight")), bool(r.get("answer")))
    if r.get("answer"):
        need(" answer", r["answer"], ["key_points", "citations"])
        need(" cit", r["answer"]["citations"], ["clause_id", "document", "rank"])
au = g("/v1/governance/audit?limit=5")
need("audit", au, ["seq", "ts", "actor", "action", "run_id", "hash", "prev_hash", "payload"])
need("verify", g("/v1/governance/audit/verify"), ["valid", "records", "head"])
