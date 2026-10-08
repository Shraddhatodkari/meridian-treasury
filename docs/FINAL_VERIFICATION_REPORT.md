# Meridian 3.1.0: final verification report

Run on 2026-10-07 in a clean Linux build environment (Python 3.13). The project targets native Windows/Linux
Python 3.11+, with no Docker and no WSL. Raw logs are in [`evidence/`](evidence).

## 1. Real company used
**Caterpillar Inc.** (NYSE: CAT, SEC CIK 0000018230). Latest data: 10-Q for the quarter ended 2026-06-30. Debt detail
comes from the 10-K for FY2025 (filed 2026-02-13, accession 0000018230-26-000008). Rationale and limits are in
[`PUBLIC_COMPANY_CASE.md`](PUBLIC_COMPANY_CASE.md).

### Multi-company (3.1)
Users are not limited to Caterpillar. Any SEC-reporting company can be added by ticker or CIK from the header menu
(or `POST /v1/companies`). Its facts are fetched live from SEC EDGAR and analysed by the same governed pipeline.
Five companies are bundled (CAT, KO, HON, AAPL, DE). Verified live in this build: 15 tickers (CAT, DE, HON, MMM, AAPL,
MSFT, WMT, PG, GE, F, KO, NVDA, JNJ, XOM, AMZN) ran through metrics, recommendations, scenarios, forecast, Q&A and
the MCP tools with no errors. A Coca-Cola governed run produced 1 recommendation, all findings grounded and 7/7
governance checks passed. Invalid input returns 422, unknown tickers 404, and filers without us-gaap data are refused
with the reason.

## 2. Authoritative sources
* SEC EDGAR XBRL company facts: Caterpillar (562 as-filed 10-K/10-Q facts) and six peers' latest 10-Ks.
* NY Fed: SOFR 3.89% and EFFR (2026-10-05).
* U.S. Treasury par yield curve (2026-10-06).
* FRED ICE BofA Single-A OAS 0.72% and BBB OAS 1.04% (2026-10-05).
* ECB euro reference rates.

Each source is dated and checksummed. Refresh with `scripts/refresh_public_company.py` and `scripts/refresh_market_snapshot.py`.

## 3. Dashboard metrics that are PUBLIC · REAL (Caterpillar case)
* **REPORTED (as filed):**
  * cash
  * trade receivables
  * finance receivables
  * inventories
  * accounts payable
  * remaining performance obligations
  * derivative notional
  * short-term borrowings
  * long-term debt and the years 1–5 maturity schedule
  * committed facility capacity and remaining capacity
  * cash interest paid
  * cash at fiscal year end
* **CALCULATED from those facts:**
  * TTM revenue, COGS, operating cash flow, capex, operating profit, D&A, dividends and repurchases
  * FCF proxy and EBITDA proxy
  * DSO, DIO, DPO, CCC and trade working capital, with their trend and year-on-year bridge
  * total debt, net debt, liquidity, debt due within 12 months and coverage
  * gross and net leverage, interest cover and implied cost of debt
  * peer DSO, DIO and DPO
* **ESTIMATED** (stated assumptions, real inputs): +100 bp repricing cost; refinancing cost at UST 5Y + Single-A OAS; the seasonal cash outlook.
* **SCENARIO:** Scenario Lab outputs.

The automated tests check that every PUBLIC · REAL metric traces to issuer-filed 10-K/10-Q facts (accession, period,
filing date). They also check that the public-case code never imports the synthetic generator or engine.

## 4. Capabilities that remain SYNTHETIC · EVALUATION
These need private transaction data:
* 13-week invoice-level Monte-Carlo forecast and the ML payment-timing model
* intercompany cash optimisation (LP)
* entity, bank and account cash positioning
* collections and supplier-discount analytics
* covenant tests on facility terms
* ISO 20022 pain.001 payment-file lifecycle

They run on the "Orion Industrial Group" case, which is labelled SYNTHETIC · EVALUATION on every screen and in every API payload.

## 5. Enterprise integrations that remain CLIENT · NOT CONFIGURED
* ERP APIs: SAP S/4HANA, Oracle Fusion, NetSuite
* bank host-to-host / SWIFT
* TMS
* enterprise SSO (OIDC)

The file interfaces are implemented and tested (ERP CSV package, ISO 20022 camt.053, BAI2, debt/covenant CSV), but they carry no Caterpillar data. Payment transmission channel: `none`.

## 6. AI provider / model configuration
* `MERIDIAN_LLM_PROVIDER=auto` (default) selects Anthropic Claude when `ANTHROPIC_API_KEY` is set (model `MERIDIAN_LLM_MODEL`, default `claude-sonnet-4-5`). Vertex AI and Bedrock are also supported.
* No API key was configured in this build. The system therefore ran **AI OFFLINE / DETERMINISTIC**, and the UI, `/ready`, `/v1/system/environment` and every run record say so.
* The Claude configuration path is tested: with a key set, the provider resolves to `anthropic` and the SDK client is constructed. **No live Claude call was made or claimed.** Verify one with `meridian llm-check`.

## 7. Test results
| Suite | Result |
|---|---|
| `pytest -v -rs tests` | **230 passed, 4 skipped** (the 4 skips need MongoDB) → [`evidence/pytest.txt`](evidence/pytest.txt) |
| MongoDB 8.0.15 production path | **4 passed** → [`evidence/pytest_mongodb.txt`](evidence/pytest_mongodb.txt) |
| Evaluation gates, seeds 7 / 11 / 23 | **11/11 PASS on each** → `evaluation-results-seed{7,11,23}.md` |
| API contract smoke (live server) | **61/61 OK** → [`evidence/api_contract.txt`](evidence/api_contract.txt) |
| Production readiness on a misconfigured env | NOT READY, exit 1 (as designed) → [`evidence/doctor_production_misconfigured.txt`](evidence/doctor_production_misconfigured.txt) |
| Ruff | clean |
| Console (headless Chromium): 14 workspaces + 24-step journey | no application JavaScript errors |

New in 3.1:
* `tests/integration/test_multi_company.py` (10 tests): every bundled company passes the lineage and basis invariants,
  scenarios, forecast, peers, the 5 policy rules and grounded Q&A; add-company flow with SEC mocked (ADDED → EXISTS →
  listed → governed run → recommendations filtered by `cik`); 422 validation, 403 for roles without `run_agents`,
  404 for unknown tickers and CIKs.
* `tests/unit/test_public_company.py` (25 tests):
  * lineage and traceability
  * no synthetic import
  * restatement handling
  * Q4/TTM derivation
  * WC and debt reconciliation
  * missing concepts not imputed
  * point-in-time back-test
  * scenarios
  * evidence-gated recommendations, including a threshold change and no manufactured capital-allocation recommendation
  * grounded copilot answers
  * AI offline and Claude configuration
  * no keys in source
  * UTF-8 I/O
  * no mojibake
  * version 3.x
* `tests/integration/test_public_company_workflow.py` (13 tests):
  * case ordering and honest AI status
  * fully governed agent run
  * verified policy citations
  * lineage to SEC accessions
  * input validation
  * mandate lifecycle: submit → authority denial → approve → prepare → dual-control refusal → issue
  * requester SoD and WC-manager scope
  * grounded Q&A
  * UTF-8 content types

No existing test was weakened. `test_cases_put_real_company_first` was updated in 3.1 because the case list now holds every real company before the evaluation case; it asserts that ordering and the PUBLIC_REAL classification of each. Two assertions changed in v3.0 and remain disclosed in `PLATFORM_V3.md`:
* the release endpoint is the only execution path;
* the console module check.

## 8. Recommendation and approval workflow result (Caterpillar, run in the browser)
At start-up the API runs a scheduled base-case review of the public company (requester `meridian-scheduler`,
audited). It runs only if no public base-case run is stored, so the platform never opens on an empty decision queue.
Disable it with `MERIDIAN_STARTUP_REVIEW=0`.
1. The analyst ran the governed review. 9 agents completed with no failures and every finding was numerically grounded. The briefing grounded 14 of 14 figures. Governance checks passed 7 of 7.
2. 5 policy rules were evaluated; 3 were triggered:
   * R01: refinancing-readiness plan for $12.63B, routed to the CFO
   * R02: receivables review (DSO +8.3 days YoY, $1.70B ESTIMATED), routed to the Treasurer
   * R03: rate-risk review ($126.3M per year ESTIMATED), routed to the Treasurer

   The 2 rules not triggered (liquidity coverage, returns vs FCF) were reported as not triggered. Every recommendation cites verified PC-* clauses.
3. The analyst submitted R01.
   * The Treasurer was **denied** (CFO authority required).
   * The analyst was **denied** on R02 (requester SoD and role scope).
   * The administrator was **denied** (no decision authority).
4. The CFO approved R01.
5. The Treasurer prepared it: 6 validation checks → READY.
6. The CFO tried to issue it and was **refused** (dual control: the approver cannot issue).
7. The Treasurer issued it → **EXECUTED as a decision mandate. No payment, issuance or trade was executed.**
8. The audit ledger records `recommendation_submitted`, `approval_denied`, `action_approve`, `mandate_prepared`, `release_blocked` and `mandate_issued`. The hash chain is valid.
9. The lineage DAG for R01 runs SEC source → filings (accessions) → facts → metrics → recommendation → policy → action → decisions → audit records.
10. Payment-file release on the evaluation case remains blocked (no bank channel; dual control).

Screenshots: [`images/v3/journey/`](images/v3/journey) (24 steps) and [`images/v3/`](images/v3) (14 workspaces).

## 9. Final project version
**3.1.0** (`pyproject.toml`, `meridian.__version__`). The `/health`, `/ready` and `/v1/system/environment` endpoints report it, and the console shows the backend's value (no hard-coded version). Policy-as-code version: `2026.10-t3`.

## 10. ZIP
`/mnt/user-data/outputs/Meridian_v3.1_Final.zip`. One top-level `meridian/` folder. No `.venv`, caches, logs or nested duplicates.

## Data-correctness review (multi-company)
Apple and Microsoft balance-sheet figures were re-fetched independently from SEC EDGAR and matched to the cent
(cash, receivables, inventory, payables, commercial paper, current and non-current long-term debt; total debt, net
debt and trade working capital recomputed by hand). Fixes made in this review:
* The Single-A credit spread was stored only with Caterpillar, so other companies showed it as FAILED and the console
  added a missing spread as 0% to the "indicative new 5Y issue" rate. Market series are now shared by all companies,
  and the console never sums a missing input.
* Briefings and agent findings no longer print "Liquidity of n/a covers…"; when undrawn facilities are not tagged, the
  text says so. Debt figures carry the debt date (not the 10-K date), and captive-finance caveats appear only for
  captive issuers (CAT, DE).
* Scenario base-case leverage now uses the same EBITDA basis as the reported leverage metric (Apple: 0.50x both).
* Console: no "nullx", "n/ax" or "null" anywhere; Scenario Lab explains when scenarios cannot be derived (e.g.
  Microsoft tags D&A with a company-specific element, so its EBITDA proxy is NOT AVAILABLE rather than estimated).
* Cash outlook: the method with the better point-in-time back-test is used (seasonal for Caterpillar at 25.3% vs
  33.4%; no-change for Apple, Microsoft, Coca-Cola, Deere, Honeywell). Both errors are shown, forecasts are floored
  at zero (Microsoft previously showed a negative P10), and a low-reliability warning appears above 40% error.
* Grammar ("an inventory review"); `.gitignore` no longer excludes the bundled market snapshot.

## Known limitations
* Added companies use a default industrial peer set unless peers are given. Segment, dimensional and company-extension XBRL facts are not used.
* No live Claude call verified. Atlas `$vectorSearch` was not executed; MongoDB was tested against a local 8.0 server.
* Caterpillar figures are consolidated, including Cat Financial. The ME&T-only split, segments, quarterly debt lines, interest-expense line, hedge mix and covenants are NOT AVAILABLE in the companyfacts API.
* The current portion of long-term debt comes from the maturity schedule (a disclosed proxy). The "after year 5" maturity bucket is a calculated residual.
* Peer comparisons are directional: business models differ (captive finance, dealer receivables). Three candidate peers were excluded for missing concepts.
* The PC-* thresholds are Meridian's illustrative reference policy, not Caterpillar's.
* The public-case forecast is a quarterly seasonal run-rate (MAPE 25%). A 13-week forecast needs client bank and ERP data.
* The evaluation deployment uses an in-memory store and evaluation sign-ins. Production mode enforces MongoDB, a signing key, a real LLM and client data.
