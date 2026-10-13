# Meridian 3.3.0 — Production Readiness Report

**Date:** 2026-10-12 · **Baseline:** 3.2.0 · **Added cost: ₹0** (no paid services, no Docker, no WSL) · **Build
environment:** Linux, Python 3.12 clean virtual environment, MongoDB 8.0.15 (optional) · **Target:** native Windows/Linux.

**Bottom line.** 3.3.0 closes the biggest gap left in 3.2: the evaluation deployment kept everything in memory and lost
it on restart. Meridian now persists users, runs, approvals, decisions, added companies and the audit chain in SQLite
(built into Python, free), proven by restarting a real server. It is still **not a live enterprise deployment**: no client
ERP/bank system is connected, enterprise SSO and multi-tenant isolation are not implemented, and sign-ins are
evaluation accounts. Section B lists the remaining gaps.

## A. 3.3.0 changes and evidence

### A.1 Gap analysis (from the code, before changes)

| # | Gap found | Severity | Status in 3.3 |
|---|---|---|---|
| 1 | Default store was in-memory: users, runs, approvals, audit lost on every restart | Critical | **Fixed** — SQLite store, default |
| 2 | Only persistent option needed a MongoDB install; no free local option | High | **Fixed** — SQLite needs nothing |
| 3 | Production mode required a paid Claude endpoint (conflicts with ₹0) | High | **Fixed** — deterministic mode allowed, labelled WARN |
| 4 | Audit append-only only enforceable with a MongoDB role | High | **Fixed** — SQLite triggers reject UPDATE/DELETE |
| 5 | Backup/restore documented but never executed | High | **Fixed** — `meridian db backup/restore`, executed |
| 6 | No proof that every API route needs authentication | Medium | **Fixed** — route-by-route test (74 protected, 2 public) |
| 7 | Bank statements not reconciled; duplicate statements/transactions not detected | Medium | **Fixed** |
| 8 | Synthetic-case model/forecast tiles in System Operations not labelled synthetic | Medium | **Fixed** |
| 9 | A restart test could silently import an older installed copy of Meridian | Medium | **Fixed** (test now pins this source tree) |

### A.2 Files changed and why

| File | Why |
|---|---|
| `src/meridian/store/sqlite.py` (new) | Persistent SQLite store: same interface as Memory/Mongo; versioned migrations; append-only audit triggers; WAL, busy timeout, locking; online backup; verified restore |
| `src/meridian/store/factory.py` | Store selection `MERIDIAN_STORE=auto|sqlite|mongodb|memory`; auto = MongoDB if a URI is set, else SQLite; memory refused in production; `build_store()` for testing |
| `src/meridian/config.py` | `store`, `sqlite_path` settings and `store_kind` |
| `src/meridian/readiness.py` | Production accepts SQLite (WARN: single node) or MongoDB, refuses memory; AI mode is a labelled WARN, not a paid requirement |
| `src/meridian/cli.py` | `meridian db status | migrate | backup | restore` |
| `src/meridian/ingest/quality.py` (new), `camt053.py`, `pipeline.py` | Opening + entries = closing reconciliation, duplicate statement/transaction detection, counts in the ingestion report |
| `src/meridian/api/main.py`, `console/js/app.js`, `p_gov.js` | Store and persistence shown in the header and System Operations; synthetic tiles labelled |
| `tests/integration/test_sqlite_store.py` (new) | Contract, append-only at DB level, concurrency, migrations, backup/restore, 3-process restart |
| `tests/integration/test_access_matrix.py` (new) | Every `/v1` route refuses anonymous calls; role matrix on direct API calls |
| `tests/unit/test_ingest_quality.py` (new) | Reconciliation, break detection, duplicates |
| `tests/unit/test_platform.py` | Readiness test updated to the ₹0 policy (see disclosure below) + new SQLite/memory tests |
| `tests/conftest.py` | Tests use `MERIDIAN_STORE=memory` explicitly (isolation); persistence has its own tests |
| `tests/integration/test_mongodb_production_path.py` | Child processes pinned to this source tree |
| `README.md`, `docs/production-runbook.md`, `.gitignore` | Persistence, Windows commands, SQLite backup/restore; `var/` and backups never committed |

**Disclosed test change.** `test_production_readiness_refuses_demo_shortcuts` previously required a real Claude endpoint
and MongoDB. Under the ₹0 requirement, production now refuses *memory* storage (still FAIL), missing signing key (FAIL)
and non-client data (FAIL), while deterministic AI is a visible WARN. The test asserts exactly that; no assertion about
security was removed.

### A.3 Test results

| Suite | Result |
|---|---|
| Clean install, all suites (`pytest`, with MongoDB 8.0.15 and live network enabled) | **311 passed, 0 failed, 0 skipped** |
| Deterministic suite only (`pytest -q`, default) | 300 passed, 11 skipped (6 MongoDB + 5 live-network, opt-in) |
| Ruff | clean |
| Browser (headless Chromium, clean install): login, add MSFT live, 28 page loads | 0 problems |

### A.4 Persistence evidence (real server, default configuration, `docs/evidence/restart_evidence.txt`)

Fresh start → `/ready` reports `store: sqlite`. As analyst: governed run on Caterpillar, recommendation submitted.
Treasurer tries to approve the CFO-level action → **403**. CFO approves → **200**. Microsoft added live from SEC.
Audit chain valid, 35 records. **Server stopped.** Restarted → run found, action **APPROVED**, Microsoft still
registered, 6 users, audit chain valid (37 records incl. the restart's sign-ins) and contains `approval_denied`,
`action_approve`, `recommendation_submitted`, `company_added`. Offline: `meridian db status` (schema v1, integrity ok),
`meridian db backup` (verified), `meridian db restore` (verified; previous file kept).

### A.5 Role permissions and audit access

* 74 of 76 `/v1` endpoints return 401 without a token; the 2 public ones are sign-in and the environment banner.
* Garbage, unsigned (`alg: none`) and empty tokens → 401.
* Full audit ledger and verification: Treasurer, CFO, Internal Audit, Administrator only (Analyst and WC Manager → 403);
  user administration: Administrator only (even CFO → 403); operations: Treasurer, CFO, Audit, Admin.
* Approval authority, scopes, requester SoD and dual control: existing workflow tests, plus the live denial above.

### A.6 Public data — tested live on 2026-10-12

| Source | Latest observation returned live | Bundled snapshot used by the app |
|---|---|---|
| NY Fed SOFR / EFFR | 3.87 % / 3.88 % (2026-10-08) | 2026-10-05 |
| U.S. Treasury par curve | 5Y 5.02 % (2026-10-09) | 2026-10-06 |
| FRED ICE BofA BBB / Single-A OAS | 1.02 % / 0.69 % (2026-10-08) | 2026-10-05 |
| ECB euro reference rates | 2026-10-09 | dated snapshot |
| SEC EDGAR (Caterpillar) | latest cash fact 2026-06-30, 10-Q filed 2026-08-05 | same filing |

The app uses dated, checksummed snapshots so results are reproducible; every screen shows the snapshot date. Refresh
explicitly with `python scripts/refresh_market_snapshot.py` and `python scripts/refresh_public_company.py`
(`MERIDIAN_MARKET_DATA=live` fetches at runtime instead).

### A.7 Integrations, AI mode and cost

* **Connected for real:** SEC EDGAR, NY Fed, U.S. Treasury, FRED, ECB (public, free, no keys).
* **Implemented, tested on files, NOT CONFIGURED for a client:** ERP CSV package, ISO 20022 camt.053, BAI2, debt/covenant CSV.
* **NOT CONFIGURED:** ERP APIs (SAP/Oracle/NetSuite), bank host-to-host/SWIFT, TMS, enterprise SSO. No credentials were invented; no payment can be transmitted (channel `none`).
* **AI mode:** deterministic, evidence-grounded reasoners. **No external AI calls are made** unless `ANTHROPIC_API_KEY` is set (Claude is billed separately by Anthropic and is off by default). No local LLM was added: none is needed for the grounded reasoners, and adding one would raise hardware requirements.
* **Added cost:** ₹0. SQLite is part of Python; MongoDB is optional; all data sources are free.

## B. Remaining gaps before a live enterprise deployment

1. Enterprise SSO (OIDC) is not implemented; sign-ins are local accounts (PBKDF2 + JWT) and evaluation accounts in demo mode.
2. Multi-tenant isolation in one database is not implemented (one deployment per organisation).
3. No client ERP/bank system is connected; the 13-week forecast is validated only on synthetic data.
4. SQLite serves one API host; use MongoDB for several replicas. Backups must be copied off the machine.
5. Per-entity / per-currency approval limits are not modelled.
6. No independent security assessment or penetration test has been performed; no certification is claimed.

---

# History: 3.2.0 report

**Date:** 2026-10-11 · **Baseline:** Meridian 3.1.0 · **Build environment:** Linux, Python 3.12 (clean virtual environment),
MongoDB 8.0.15 · **Target:** native Windows/Linux, Python 3.11+, no Docker, no WSL.

**Bottom line.** Meridian 3.2.0 is a production-*oriented* release: its financial calculations, governance, audit,
grounding, data connectors and persistence were verified with real data and real infrastructure in this build. It is
**not yet a live enterprise deployment**. No client ERP or bank data is connected, no live Claude call was made (no API key
was provided), enterprise SSO and multi-tenant isolation are not implemented, and the evaluation deployment uses demo
sign-ins. Section 7 lists exactly what remains.

Classification used throughout: **VERIFIED WORKING** (executed and passed here) · **PARTIALLY IMPLEMENTED** ·
**BLOCKED** (needs credentials or infrastructure not available here) · **NOT VERIFIED** (not executed here).

---

## 1. What changed in 3.2.0 (all implemented and tested)

| Area | Change | Files |
|---|---|---|
| Defects from the 3.1 run | Forecast agent no longer says "seasonal" when the no-change method is used; Command Center and Recommendations say "rules evaluated, none triggered" instead of "no analysis has been run" when a run produced no recommendation; briefing states "no decision required" instead of an empty list | `public_company/reasoning.py`, `mcp_server/server.py`, `console/js/p_public.js`, `p_decisions.js` |
| Numeric grounding | The blanket 2 % tolerance is replaced by **display-precision matching** (half of the last digit shown: `$12.63B` ±$5M), **explicit-sign** and **currency** checks, and checking of multiples (`1.55x`). This exposed two real evidence gaps that the 2 % rule had hidden (a policy threshold and a working-capital change were matched to unrelated numbers); both sources are now passed as evidence, and the 1.25x policy floor is read from policy-as-code instead of being hard-coded | `governance/guardrails.py`, `orchestration/public_graph.py`, `public_company/reasoning.py` |
| Claude client | Outbound **DLP redaction** before any model call; SDK **timeout** and **bounded retries**; **token accounting** and a **per-run token cap** in addition to the call cap; provider errors, malformed responses and exhausted budgets fall back to the deterministic reasoner and are **labelled** (`claude`, `deterministic`, `fallback_after_error`, `fallback_budget_exhausted`) on every finding and briefing; `meridian llm-check` now fails unless a live structured answer is returned | `llm/claude.py`, `governance/dlp.py`, `config.py`, `cli.py`, `console/js/core.js` |
| Public data connectors | One resilient HTTP layer for SEC, NY Fed, U.S. Treasury, FRED and ECB: timeouts, bounded exponential backoff, `Retry-After` (capped), SEC fair-access throttle (≤10 req/s), TTL cache for the ticker file, explicit errors | `market/http.py`, `public_company/sec.py`, `market/rates.py`, `market/connectors.py` |
| Audit integrity | Record format v2: digest covers the **sequence number** and **canonical JSON**; verification reports deletion, reordering, renumbering and edits; external **chain anchors** (`meridian audit anchor / check-anchor / verify`) detect a full rewrite; v1 records still verify | `governance/audit.py`, `cli.py` |
| Storage protection | `scripts/mongo_least_privilege.py` creates an application role that can **insert and read but never update, delete or drop** the audit collection — verified on an authentication-enabled MongoDB 8.0 | `scripts/mongo_least_privilege.py` |
| Observability | JSON access logs with `X-Request-ID` (validated), route template, status, latency; no tokens, query strings or bodies; messages pass through DLP | `observability/logs.py`, `api/main.py` |
| Release hygiene | Secret and unintended-file scanner for trees and ZIPs; dependency audit; opt-in live network tests; runbook sections for least privilege, anchoring, backup/restore, upgrade/rollback, incident response, troubleshooting | `scripts/scan_release.py`, `tests/network/`, `docs/production-runbook.md` |

Disclosed test changes (no test was weakened):
1. `test_claude_path_with_fake_client` previously expected an exception when the call budget was exhausted. The
   required behaviour is now a labelled deterministic fallback; the test asserts the provider is **not** called again
   and the answer is labelled `fallback_budget_exhausted`.
2. The MongoDB restart test initially expected status `SUBMITTED`; the application's actual status after submission is
   `IN_REVIEW`, which the test now asserts.

## 2. Requirement classification

| Requirement | Status | Evidence |
|---|---|---|
| Executive dashboard, 14 workspaces, multi-company (add any SEC filer) | **VERIFIED WORKING** | Clean-install browser check: login, add MSFT live via UI, 28 page loads (MSFT + CAT), 0 JS errors, 0 "null/NaN/undefined" — `docs/evidence/browser_check_clean_install.txt` |
| Every figure sourced or calculated, never fabricated | **VERIFIED WORKING** | Lineage/basis invariants for all bundled companies; Apple and Microsoft balance-sheet values cross-checked to the cent against SEC; missing concepts shown as NOT AVAILABLE |
| SEC EDGAR (tickers + companyfacts) | **VERIFIED WORKING** (live) | `docs/evidence/network_live_sources.txt` — live CAT cash equals the bundled REPORTED value |
| NY Fed SOFR/EFFR, U.S. Treasury curve, FRED credit spread, ECB FX | **VERIFIED WORKING** (live) | same file; 5/5 live tests passed |
| Connector resilience (retry, backoff, Retry-After, throttle, cache) | **VERIFIED WORKING** | `tests/unit/test_http_resilience.py` (7) |
| Live vs snapshot distinction | **VERIFIED WORKING** | Sources labelled SNAPSHOT with dates in Data Lineage; failed sources shown FAILED |
| FRED API key | Not required | Meridian uses FRED's public CSV endpoint (`fredgraph.csv`), no key |
| Private ERP/bank file ingestion (ERP CSV, camt.053, BAI2, covenants CSV) | **VERIFIED WORKING** on synthetic files | Existing round-trip and validation tests; MongoDB ingestion test |
| Live ERP API / bank host-to-host | **BLOCKED** | Needs a client system and credentials; interfaces documented, labelled NOT CONFIGURED |
| Live Claude API call | **BLOCKED / NOT VERIFIED** | No `ANTHROPIC_API_KEY` provided. SDK path, retries, fallbacks, budget, DLP verified with fake transports. Run `meridian llm-check` with a key |
| Offline deterministic mode, honest AI labels | **VERIFIED WORKING** | `test_offline_mode_never_claims_claude`; UI badges per briefing |
| Numerical grounding (precision, sign, currency, multiples) | **VERIFIED WORKING** | `tests/unit/test_grounding_precision.py` (8) + all agent runs grounded; evaluation gates 3/3 briefings grounded on 3 seeds |
| Grounding of period mismatches | **PARTIALLY IMPLEMENTED** | Periods are carried in lineage and printed beside figures; the checker does not yet parse dates in prose |
| Authentication (PBKDF2, JWT expiry, lockout, logout revocation) | **VERIFIED WORKING** | `tests/integration/test_security_and_approvals.py` |
| RBAC enforced server-side; SoD; scopes; dual control | **VERIFIED WORKING** | same + `test_public_company_workflow.py` |
| Configurable approval thresholds | **VERIFIED WORKING** | Approval matrix in `config/treasury_policy.yaml`; threshold-change test |
| Legal-entity / currency-scoped approval limits | **PARTIALLY IMPLEMENTED** | Capital-control and amount conditions exist; per-entity and per-currency limits are not modelled |
| Tenant isolation | **NOT IMPLEMENTED** | One deployment and one database per client organisation; shared-database multi-tenancy is not built |
| Outbound DLP before model calls | **VERIFIED WORKING** (pattern-based) | `tests/unit/test_llm_and_dlp.py` (IBAN mod-97, Luhn cards, SSN, EIN, keys, tokens, emails; false-positive tests on accessions/CIKs). Limitation: no NER for names/addresses |
| Audit chain integrity and tamper detection | **VERIFIED WORKING** | `tests/unit/test_audit_integrity.py` (8): edit, delete, reorder, renumber, full rewrite via anchor |
| Append-only audit storage | **VERIFIED WORKING** (MongoDB role) | `test_app_role_can_append_but_never_alter_audit` on auth-enabled MongoDB 8.0 |
| Persistence across restart (runs, decisions, audit) | **VERIFIED WORKING** | `test_runs_decisions_and_audit_survive_an_application_restart` |
| Concurrent audit writers | **VERIFIED WORKING** | `test_audit_chain_is_safe_with_concurrent_replicas` |
| Backup and restore | **VERIFIED (SQLite, 3.3)** / NOT VERIFIED (MongoDB) | 3.3: `meridian db backup/restore` executed with verification; MongoDB procedure documented, `mongodump` not installed |
| Migrations | **PARTIALLY IMPLEMENTED** | Schema-less store; indexes created at start-up; audit v1→v2 handled in code. No formal migration framework |
| Background jobs (Celery/Redis) | **Not added — not justified** | Measured on the clean install: a full governed run (11 agents, audit, grounding) takes 0.30–0.37 s end to end over HTTP (CAT, KO, AAPL); adding a company live from SEC took 0.77 s. A queue would add infrastructure without a measured need |
| OpenTelemetry | **Not added** | Prometheus `/metrics`, JSON access logs with request ids and agent traces cover current needs; OTel remains optional |
| Health checks, structured logs | **VERIFIED WORKING** | `/health`, `/ready`, `test_observability_logs.py` |
| Forecast validation (public companies) | **VERIFIED WORKING** | Point-in-time back-tests; best method selected per issuer (table below) |
| 13-week forecast validation on real company data | **BLOCKED** | Needs client bank/ERP history; validated on synthetic data only (gates below) |
| Dependency vulnerabilities | **VERIFIED** | `pip-audit`: no known vulnerabilities — `docs/evidence/pip_audit.txt` |
| Secret scan of the release | **VERIFIED** | `scripts/scan_release.py` on the final ZIP: clean (see manifest) |
| Certifications / regulatory compliance | **None claimed** | No independent security assessment has been performed |

## 3. Test results (clean install, Python 3.12)

| Suite | Result |
|---|---|
| Deterministic suite (`pytest -q`) | **279 passed**, 11 skipped (skips = MongoDB and live-network tests, run separately) |
| MongoDB 8.0.15 (incl. restart persistence and append-only role) | **6 passed** |
| Live public sources (`MERIDIAN_NETWORK_TESTS=1`) | **5 passed** |
| **Total** | **290 passed, 0 failed, 0 skipped when all environments are available** |
| Ruff | clean |
| Evaluation release gates, seeds 7 / 11 / 23 | **11/11 PASS** on each |
| Browser (headless Chromium, clean install) | 28 page loads, 0 problems |
| `pip-audit` | no known vulnerabilities |

56 tests were added in this release (234 → 290).

## 4. Financial validation

| Check | Result |
|---|---|
| Apple balance sheet vs SEC (cash, receivables, inventory, payables, CP, current and non-current LTD) | exact; total debt $84.34B, net debt $44.80B, trade WC −$22.04B recomputed |
| Microsoft balance sheet vs SEC | exact; total debt $40.29B, net debt $19.36B, trade WC $39.86B recomputed |
| Caterpillar cash vs live SEC request | equal |
| Liquidity waterfall (CAT): $9.98B + $9.66B − $5.51B − $7.12B | $7.00B headroom (rounding) |

Public-company cash outlook — point-in-time back-test (MAPE, lower is better); the better method is used:

| Issuer | Seasonal | No-change | Selected |
|---|---|---|---|
| Caterpillar | 25.3 % | 33.4 % | seasonal |
| Apple | 26.0 % | 13.6 % | no-change |
| Coca-Cola | 16.8 % | 15.4 % | no-change |
| Deere | 15.5 % | 15.3 % | no-change |
| Honeywell | 21.7 % | 15.6 % | no-change |
| Microsoft | 63.1 % | 45.8 % | no-change, flagged **low reliability** |

Synthetic evaluation case (13-week, invoice-level): collections WAPE 9.1 % / 12.4 % / 8.8 % vs 69.7 % for the ERP
due-date assumption (seeds 7/11/23); AR model MAE 3.42 / 3.86 / 3.49 days vs customer-average baseline. These are
**synthetic** results and say nothing about accuracy on a real company's ledgers.

## 5. Security notes

- Secrets come only from the environment / secret manager; `.env.example` contains no values. The demo password
  constant exists only for evaluation sign-ins, which are refused outside demo mode (tested).
- Sessions: HS256 JWT with expiry and issuer; logout revokes the token id. Production requires `MERIDIAN_JWT_SECRET`.
- Browser: CSP, X-Frame-Options DENY, nosniff, no-referrer, no-store; HSTS when served over HTTPS.
- The AI cannot move money: no payment tool exists; release requires a different human than approver and requester,
  and the transmission channel is `none`.

## 6. Credentials, setup and costs

| Item | Needed for | Cost |
|---|---|---|
| None | Evaluation deployment, all public data, deterministic AI | Free |
| `ANTHROPIC_API_KEY` | Live Claude reasoning | Billed per token by Anthropic, separately from any Claude subscription; capped per run by `MERIDIAN_LLM_MAX_TOKENS_PER_RUN` (default 200,000) and 24 calls |
| MongoDB (self-hosted or Atlas) | Persistence in production | Self-hosted free; Atlas from the free tier up, backups need paid tiers |
| `MERIDIAN_SEC_USER_AGENT` | SEC fair-access identification | Free |
| SSO (Entra ID / Okta) | Enterprise sign-in | Not implemented yet |

## 7. Remaining work before a live enterprise deployment

1. Connect one authorised ERP export and one bank statement feed for the client, and re-run forecast validation on
   that history (the 13-week forecast is validated only on synthetic data).
2. Provide an Anthropic key, run `meridian llm-check`, and review a sample of live briefings and fallback rates.
3. Implement enterprise SSO (OIDC) and remove demo sign-ins from the deployment.
4. Decide on tenancy: keep one database per client, or implement tenant-scoped queries and tests.
5. Add per-entity and per-currency approval limits if the client's policy requires them.
6. Install MongoDB Database Tools, schedule backups, and execute a restore drill.
7. Commission an independent security review and penetration test.

## 8. Release

`Meridian_Enterprise_Final.zip` is built with `git archive` from the release commit recorded in
`RELEASE_MANIFEST.json` (outside the ZIP), so the packaged files are exactly the committed source. The manifest records
the version, commit, file count, test results of the independent extraction and the ZIP's SHA-256 checksum.
