# Verification report

**Current release: 3.1.0. See [`../FINAL_VERIFICATION_REPORT.md`](../FINAL_VERIFICATION_REPORT.md).** It covers:
* 230 passed, 4 skipped (includes multi-company tests)
* MongoDB 4 passed
* evaluation gates 11/11 on seeds 7, 11 and 23
* API contract 61/61
* Caterpillar (PUBLIC · REAL) end-to-end workflow verified in the browser

The 3.0.0 report below is kept for history.

# Verification report: Meridian v3.0.0

Run on 2026-10-07 in a clean Linux build environment (Python 3.13). Raw logs are in this folder. The project
targets native Windows/Linux Python 3.11+ and needs no Docker and no WSL.

| Check | Command | Result |
|---|---|---|
| Full test suite | `pytest -v -rs tests` | **166 passed, 4 skipped**. The 4 skips are the real-MongoDB tests, run separately below. The v2 baseline (98) is fully included → [`pytest.txt`](pytest.txt) |
| Production persistence on a real MongoDB 8.0.15 server | `MERIDIAN_TEST_MONGODB_URI=… pytest tests/integration/test_mongodb_production_path.py` | **4 passed** → [`pytest_mongodb.txt`](pytest_mongodb.txt) |
| Evaluation, seed 7 / 11 / 23 | `python -m meridian.evaluation.benchmark --seed N` | **11/11 release gates PASS on all three seeds** → `../evaluation-results-seed{7,11,23}.md` |
| API health | `/health`, `/ready`, unauthenticated `/v1/kpis`, console `/` | 200 · ready (v3.0.0, mode, classification, provider, store) · **401** · 200 → [`api_health.txt`](api_health.txt) |
| Console API contract (live server) | `scripts/smoke_api_contract.py` | all contracts OK → [`api_contract.txt`](api_contract.txt) |
| v3 lifecycle (HTTP) | `pytest tests/integration/test_v3_workflow_and_api.py` | 36 passed: submit → Treasurer blocked (authority) → requester SoD → CFO approve → prepare (6 checks, READY) → release refused (channel/dual control) → audit chain valid; WC-manager scope; rejection needs rationale; admin cannot approve; logout revokes token; CSP; 422 validation; agent failure → degraded run |
| v3 platform (unit) | `pytest tests/unit/test_v3_enterprise.py` | 25 passed: domain, waterfall/bridge reconciliation, covenants under EBITDA stress, DSO contributions sum to change, metric lineage, 5-layer catalog, data-class separation, NOT_CONFIGURED connectors, graph queries, RAG version/relevance/quarantine/citations, LP shadow prices, BAI2 and camt.053 round trips |
| Production configuration | `MERIDIAN_MODE=production meridian doctor` | **NOT READY, 5 FAIL / 4 WARN, exit code 1** on a deliberately misconfigured environment → [`doctor_production_misconfigured.txt`](doctor_production_misconfigured.txt) |
| End-to-end narrated run | `python scripts/run_demo.py` | Completes; audit chain valid → [`run_demo.txt`](run_demo.txt) |
| Console UI | Headless Chromium over all 14 workspaces (CFO and Administrator), plus a 25-step journey across Analyst, Treasurer and CFO | No application JavaScript errors; no "undefined", "NaN" or "[object Object]" rendered → `docs/images/v3/` |
| Lint | `ruff check src tests scripts` | clean |

## Intentional test changes (disclosed)

1. `test_no_endpoint_executes_payments` now asserts that the only execution path is the dual-control
   `/v1/runs/{run_id}/actions/{action_id}/release` endpoint, and that `transmission_channel == "none"`.
   Previously it asserted that no execution endpoint existed at all.
2. `test_console_served_and_environment_declares_mode` checks the v3 console modules instead of the removed
   `pages.js`.

## Not verified here

- **Live Claude call.** There was no API key in the build environment. Run `meridian llm-check` with credentials.
- **Atlas `$vectorSearch`.** This needs an Atlas cluster; the readiness check covers the index.
- **ERP / bank / SSO connectivity.** These are interface contracts only (`NOT_CONFIGURED`).
- **Live market fetch at deploy time.** The bundled snapshot (2026-10-05/06) was produced by the same connectors.
