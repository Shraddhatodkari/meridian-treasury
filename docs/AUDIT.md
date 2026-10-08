# Upgrade audit: Meridian v1 → v2

The upgrade started with an audit of the v1 code. The architecture was kept: LangGraph orchestration, the read-only
MCP tool surface, the deterministic treasury engine (ML → Monte Carlo → LP), hybrid RAG, policy-as-code, the
hash-chained audit ledger and the approval → pain.001 draft flow. Each gap below was then closed inside that
architecture. No module was replaced with a toy, no working feature was removed, and no new infrastructure
(Docker, WSL, graph database, message queue) was added.

## Findings and changes

| # | Area | v1 finding | v2 change | Evidence |
|---|---|---|---|---|
| 1 | Real data path | Only a simulated company. Market inputs (FX, rates, hazards) were simulator constants. Nothing separated demo numbers from real ones. | Two modes (`demo` / `production`). Governed ingestion of ERP CSV + ISO 20022 camt.053 with a data-quality gate and atomic load. Real public market data (ECB FX, NY Fed SOFR, GDACS, SEC EDGAR XBRL) via connectors, stored as a dated snapshot for reproducible demos and fetched live in production. A provenance register records classification, as-of, retrieval time, freshness, validation and checksum for every input. | `ingest/`, `market/`, `provenance.py`, `tests/unit/test_platform.py` |
| 2 | Honesty of numbers | Synthetic company figures sat in the README with nothing to distinguish them from real figures. | Every synthetic source is classified `SYNTHETIC`. A mode banner on every screen and the tenant name say "synthetic demo company". Production refuses SYNTHETIC data. Exported samples are marked `SYNTHETIC_SAMPLE` and can never load as client data. | readiness check "Company data classification"; `test_exported_sample_ingests_cleanly_and_stays_labelled_synthetic` |
| 3 | Real AI path | Claude path existed (API / Vertex / Bedrock) but had never been exercised against a provider, and provider/model were missing from governance metadata. | Provider and model recorded on every finding, briefing and run (reproducibility block). `meridian llm-check` added for end-to-end verification. Production refuses the mock reasoner. Keys come only from the environment or a secret manager. | `orchestration/graph.py`, `cli.py`, readiness |
| 4 | Persistence | MongoDB adapter existed, but the default was in-memory and the model registry lived in process memory. Concurrent writers could fork the audit chain. | Production requires MongoDB. Runs, approvals, audit, users, data sources, ingestions and model artefacts (GridFS) are persisted. The audit sequence has a unique index with retry, so multiple replicas cannot fork the chain. | `store/`, `governance/audit.py`, `ml/ar_model.py`; `tests/integration/test_mongodb_production_path.py` (real `mongod` 8.0) |
| 5 | Identity & access | Static API keys only. No sign-in, no lockout, no auditor role. | Username/password (PBKDF2-SHA256, 600k iterations), JWT sessions, lockout after 5 failures, identical error for unknown users. Roles: analyst, treasurer, cfo, auditor (read-only), admin (no money authority). Logins and failures are audited. Demo accounts cannot authenticate outside demo mode. | `security/auth.py`; `tests/integration/test_security_and_approvals.py` |
| 6 | Dashboard | Streamlit cockpit: demo-style, no sign-in, no provenance. | Enterprise Treasury Console served by the API: 14 views across executive overview, CFO questions, cash positioning, 13-week forecast, stress testing, collections, supplier payments, peer benchmarks, approvals, relationship graph, policy intelligence, data & provenance, agents & models, and audit. Sign-in, role-aware actions, mode banner, source badges, freshness, confidence and limitations on every answer. (v3: the legacy Streamlit explorer was removed; the enterprise console has 14 workspaces.) | `console/`, `docs/images/v3/` |
| 7 | CFO questions | Briefing only. No direct answers to "where is cash trapped", "repay or preserve" and similar questions. | Insight library with eight CFO questions. Each answer carries figures, detail rows, data sources, method, model, policy clauses, confidence with reasons, and limitations. Reachable from the overview, Ask Meridian and MCP (`get_cfo_insight`). | `analytics/insights.py` |
| 8 | Relationships | Relationships were implicit in code. | Relationship graph built from the existing data: entities, accounts, banks, currencies, countries, facility, customers, suppliers, contracts, policy clauses, risk events, proposed/approved actions and approvers. Includes hazard-exposure paths. No graph database was added. | `analytics/relationships.py`, `/v1/graph` |
| 9 | Agent governance | Specialists ran in parallel. Responsibilities, schemas and validation were implicit. | Sequential governed chain Planner → Forecast → Liquidity → Risk → Working capital → Policy → Briefing → Guardrail (plus an Insight path). A registry declares each agent's responsibility, I/O schema, MCP allowlist, policy constraints, validation and confidence method. Every finding carries tool-call hashes, data sources, numeric-grounding results and limitations. | `agents/registry.py`, `orchestration/graph.py` |
| 10 | Approvals | Matrix + SoD existed. Rejection did not need a rationale, rejection authority was not checked, and there was no queue. | Approval queue that explains for each action whether *you* may decide and why not. Rejection requires a written rationale and the same authority as approval. Every approval, rejection and denied attempt is audited. Evidence chain DATA → ANALYSIS → AGENT → POLICY → RECOMMENDATION → APPROVAL → AUDIT is attached to every action. | `orchestration/service.py`, `/v1/approvals/queue` |
| 11 | Back-test | Used information not available at the forecast date. | Point-in-time back-test: rewind the ledger, retrain on data available at t0, re-forecast, compare with actual settlements. | `analytics/backtest.py` |
| 12 | Readiness | None. | `meridian doctor` and `/v1/system/readiness`. Production start-up fails on any FAIL. | `readiness.py` |

## Defects found and fixed during the upgrade

| Defect | Impact | Fix | Test |
|---|---|---|---|
| Money regex swallowed a trailing comma and read the "m" of "multiples" as *million*: "$5,000,000, multiples" became $5 trillion | The liquidity agent's finding was wrongly marked ungrounded, and its confidence fell to 0.54 | Well-formed thousands groups only; units must be whole words | `test_money_extraction_does_not_misread_thousands_or_words` |
| RCF-8.1 prepayment minimum and multiple existed only inside a text note | The real figures could not be traced by grounding | Numeric `revolver_prepay_terms` added to optimiser output | grounding 5/5 agents |
| The LP treated the TP-3.1 buffer as a soft cost. Where the overdraft rate was low (JPY 3%), it chose to overdraft instead of funding | Seed 11 left Orion Japan $2.8M below its policy buffer (release gate FAIL) | Buffer breach carries a policy penalty and is used only when funding is infeasible. The penalty is removed from reported costs | eval seed 11: 0 residual shortfalls |
| Under stress the revolver repayment rounds to $0, but text still said "repay $0 … saving $0" | Misleading CFO briefing | Wording and headline change when no repayment is recommended. Zero-value levers are dropped from the WC ranking | scenario run |
| Stress sensitivity used minimum headroom, which sits at today's opening position for most shocks | Rate and billing shocks showed exactly $0 impact | Tornado now shows the change in week-13 cash. Minimum headroom stays in the table with an explanatory limitation | `/v1/insights/shocks` |
| Graph built once at start-up | New actions and approvers never appeared in the graph | Graph rebuilt on request and after each decision | `test_graph_includes_actions_and_approvers` |
| Model checksum showed the as-of date | Misleading provenance | Checksum shows the data-fingerprint hash only | Data & provenance view |
| SEC concept fallback chose stale concepts (e.g. FY2011) | Wrong peer metrics | Latest fiscal year chosen across all candidate concepts, with balances aligned to the same fiscal year-end | snapshot notes |

## Not verified in this environment (stated plainly)

- **A live Claude call.** No API key was available in the build sandbox. The provider adapters are covered with a
  fake client, and `meridian llm-check` is the one-command check to run with real credentials.
- **Atlas `$vectorSearch`.** The persistence path was tested against a real MongoDB 8.0 server, but Atlas Search
  indexes exist only on Atlas. Retrieval falls back to the in-process hybrid ranker when the index is absent.
  Readiness checks for the index.
- **Live market fetch at deploy time.** The connectors were used to build the bundled snapshot (ECB 2026-10-06,
  SOFR 2026-10-05, GDACS 2026-10-07, SEC 10-K filings). In production they run at start-up.


---

# Upgrade audit: v2 → v3 (Enterprise Liquidity, Treasury & Working-Capital Intelligence)

| # | Area | v2 gap | v3 change | Evidence |
|---|---|---|---|---|
| 1 | Domain | Single revolver; no organization, BU, bank, debt stack, covenants or bank transactions | Enterprise domain model: Organization, BU, Bank, BankTransaction, DebtFacility (RCF, term loan, notes), Covenant; AP paid history | `domain/models.py`, `data/enterprise.py` |
| 2 | Real data | ECB, SOFR, GDACS, SEC only | + ECB 90-day history, EFFR, UST par curve, €STR, FRED BBB OAS; SEC fact-level lineage and 4-year history | `market/rates.py`, `market/connectors.py` |
| 3 | Data architecture | Provenance register only | Five-layer catalog (62 datasets), connector registry (14, with honest statuses), metric registry (23 metrics with basis and formula) | `platform/`, `analytics/metrics.py` |
| 4 | Ingestion | camt.053 balances only | camt.053 entries, BAI2, debt/covenant/bank CSVs, AP history, referential and business rules | `ingest/` |
| 5 | Treasury analytics | KPIs + forecast | Waterfall, 8-week bridge, forward bridge, concentration, FX VaR, debt profile, refinancing, covenant tests with EBITDA stress, DSO driver decomposition, AP behaviour, risk register | `analytics/treasury.py` |
| 6 | Optimisation | Transfers only | Objective, binding constraints, slack and shadow prices reported | `analytics/optimizer.py` |
| 7 | Recommendations | Actions only | Recommendations as governed objects (R01–R07) with decision, objective, constraints, impact basis, risks, owner, evidence, policy, approval, status; no overclaim | `decision/recommendations.py` |
| 8 | Agents | 7 agents | 11 agents + guardrail; failure handling (`guarded`), governance checks, citation verification | `orchestration/graph.py`, `agents/` |
| 9 | Graph | Relationship view | Financial graph (~18k nodes) with 8 decision queries and downstream impact | `analytics/graph_intel.py` |
| 10 | RAG | Clause retrieval | Document versions and metadata, relevance floor, citation verification, query injection flag, 3 new policy documents | `nlp/rag.py`, `data/documents/` |
| 11 | Workflow | Approve → pain.001 DRAFT | Submit → approve/reject → prepare (6 checks, READY) → release (dual control + channel; blocked) with full history | `orchestration/service.py` |
| 12 | Security | JWT + RBAC | Permission model, WC-manager role and scopes, token revocation, audited denials, CSP/HSTS, input limits, admin user management | `security/auth.py`, `api/main.py` |
| 13 | Observability | `/metrics` | Operations control center: API latency/errors, DB, freshness, agents, jobs, model, RAG, audit, forecast and recommendation quality | `observability/ops.py` |
| 14 | UI | 14 v2 views | 14 enterprise workspaces answering the 8 executive questions; required visualisations; lineage drawers | `console/` |
| 15 | Tests | 98 | 166 (+4 MongoDB); 3 eval seeds | `docs/evidence/` |
