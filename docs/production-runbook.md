# Production runbook

Meridian runs in one of two modes, chosen by `MERIDIAN_MODE`. The mode appears on every screen, in every API
response that carries numbers, and in each run's reproducibility metadata.

| | `demo` (default) | `production` |
|---|---|---|
| Company data | Built-in **SYNTHETIC** company (Orion Industrial Group), labelled on every screen | **CLIENT_DATA** loaded through the governed ingestion pipeline. SYNTHETIC data is refused |
| Market data | Bundled, dated snapshot of **real public data** (ECB, NY Fed, GDACS, SEC EDGAR) | `MERIDIAN_MARKET_DATA=live`: fetched from the same public sources and stored with provenance |
| AI | Deterministic offline reasoner (no key needed; used in tests and CI) | Claude via Anthropic API, Vertex AI or Bedrock. The mock reasoner is refused |
| Persistence | In-memory, or MongoDB if a URI is given | MongoDB Atlas required. Runs, approvals, audit, users, sources and model artefacts are stored there |
| Sign-in | Published demo accounts, demo service keys | Provisioned users only. Demo accounts cannot authenticate outside demo mode |

Production **refuses to start** if any readiness check fails. Run the same checks before you deploy:

```bash
meridian doctor          # prints PASS / WARN / FAIL with remediation; exit code 1 when NOT READY
```

## 1. Configure (environment or secret manager, never code)

| Variable | Purpose |
|---|---|
| `MERIDIAN_MODE=production` | Turns on the production gate |
| `MERIDIAN_MONGODB_URI` | Atlas connection string (secret) |
| `MERIDIAN_JWT_SECRET` | 32+ byte random secret for session signing (secret) |
| `MERIDIAN_LLM_PROVIDER` | `anthropic` · `vertex` · `bedrock` |
| `ANTHROPIC_API_KEY` / `MERIDIAN_GCP_PROJECT` + ADC / AWS credential chain | Provider credentials |
| `MERIDIAN_LLM_MODEL` | Claude model ID for your provider (Vertex and Bedrock use their own model IDs) |
| `MERIDIAN_EMBEDDER=transformer` | Semantic embeddings (`pip install -e ".[nlp]"`) |
| `MERIDIAN_MARKET_DATA=live` | Fetch ECB FX, SOFR, GDACS and SEC EDGAR at start-up |
| `MERIDIAN_SEC_USER_AGENT` | `"Org Name contact@org.com"`, required by the SEC fair-access policy |
| `MERIDIAN_GIT_SHA` | Release identifier recorded in reproducibility metadata |
| `MERIDIAN_API_KEYS` | Optional service keys `key:role:user,...` for system integrations (no demo keys) |

Check the AI path end to end before go-live:

```bash
meridian llm-check       # one structured call through the configured provider; prints provider / model
```

## 2. Create the vector index and users

- Atlas Vector Search: create `deploy/atlas/vector_index.json` on the `policy_chunks` collection.
- Users: `meridian users create --username jsmith --name "Jane Smith" --role cfo` (roles: `analyst`, `wc_manager`, `treasurer`, `cfo`, `auditor`, `admin`; you are prompted for the password;
  minimum 12 characters; PBKDF2-SHA256, 600k iterations; lockout after 5 failures).
  Roles: `analyst`, `treasurer`, `cfo`, `auditor` (read-only), `admin` (users and data loads; cannot approve money).

## 3. Load company data (governed ingestion)

```bash
meridian ingest --dir /secure/extracts/2026-10-06
```

The package is validated as a whole and loaded **atomically**. Any ERROR rejects the whole load, and the rejection
is recorded in the ingestion history and the audit ledger.

| File | Columns |
|---|---|
| `manifest.json` | `tenant`, `data_classification` (`CLIENT_DATA`), `extract_system`, `as_of`, `extracted_at` |
| `entities.csv` | entity_id, name, country, currency, role (`HOLDCO`/`OPCO`), capital_controls, repatriation_allowance_usd, upstream_cost_bps, min_buffer_local, deposit_yield, overdraft_rate |
| `bank_accounts.csv` | account_id, entity_id, bank, currency, iban |
| `bank_statements/*.xml` | ISO 20022 **camt.053** statements; the closing booked balance (`CLBD`) sets each account balance; `Ntry` entries become bank transactions (cash bridge) |
| `bank_statements/*.bai` | Optional **BAI2** statements (North America): 03 opening/closing ledger, 16 transaction detail |
| `customers.csv` | customer_id, name, country, segment, credit_rating |
| `suppliers.csv` | supplier_id, name, country, critical |
| `ar_invoices.csv` | invoice_id, entity_id, customer_id, currency, amount, issue_date, due_date, terms_days, disputed, paperless, settled_date |
| `ap_invoices.csv` | bill_id, entity_id, supplier_id, currency, amount, issue_date, due_date, discount_pct, discount_days, paid_date |
| `recurring_flows.csv` | entity_id, category, currency, amount, cadence, anchor |
| `facility.json` | revolving facility terms: limit, drawn, rate, covenants, margin, rate source |
| `debt_facilities.csv` (optional) | facility_id, name, type, borrower_entity, currency, limit_usd, drawn_usd, rate_type (`FLOATING`/`FIXED`), margin_bps, fixed_rate, rate, maturity_date, prepayable, prepay_min_usd, prepay_multiple_usd, amortization_pct_per_year, clause_ref |
| `covenants.csv` (optional) | covenant_id, facility_id, name, metric (`min_liquidity`/`max_net_leverage`/`min_interest_cover`), threshold, direction (`>=`/`<=`), clause_ref, test_frequency |
| `banks.csv` (optional) | bank_id, name, country, credit_rating (counterparty limits TP-5.2) |

`ap_invoices.csv` should include **paid history** (rows with `paid_date`) so DPO and AP-behaviour analytics work.

Validation rules include: required files and columns; ISO currency codes; exactly one HOLDCO; **IBAN mod-97**;
referential integrity (accounts → entities, invoices → entities / customers / suppliers); business rules
(due ≥ issue, amount > 0, no settlements after the as-of date); one camt.053 closing balance per account with a
matching currency; statement freshness; enough settled history to train the payment-delay model.

To see the format, `meridian export-sample --out ./sample` writes the demo company in this format. Its manifest
says `SYNTHETIC_SAMPLE`, so it always loads as **SYNTHETIC** and production refuses to run on it.

## 4. Go-live checklist

1. `meridian doctor` reports **READY** (no FAIL).
2. `meridian llm-check` succeeds against the production provider.
3. `make test` and `make eval` pass. `MERIDIAN_TEST_MONGODB_URI=<non-prod cluster> make test-mongo` passes.
4. The payment-delay model is re-trained on the client's history. Review the model card (Agents & models) and
   the back-test (13-week forecast) before anyone uses the forecast for decisions.
5. Replace the fictional policy documents in `src/meridian/data/documents/` with the executed treasury policy,
   facility agreement and repatriation guidance. Then review `config/treasury_policy.yaml` (approval matrix,
   buffers, tool allowlists) with Treasury and Internal Audit.
6. Put the service behind SSO / an internal load balancer. Treasury tools should not be public.

## What production still needs from you

- Bank connectivity is file-based (camt.053 in, pain.001 **drafts** out). Releasing a payment remains with the
  bank portal or TMS under your bank mandate. Meridian never transmits.
- Identity is local username/password plus JWT. Enterprise SSO (OIDC with Entra ID or Okta) is on the roadmap.

## 5. Database least privilege and audit anchoring (v3.2)

1. Create the application role and user once, as a database administrator:
   ```bash
   export MERIDIAN_APP_DB_PASSWORD='<16+ chars from your secret manager>'
   python scripts/mongo_least_privilege.py --admin-uri "$ADMIN_URI" --db meridian
   ```
   The API then connects as `meridian_app`. That account can insert and read `audit` but cannot update, delete or
   drop it (verified against MongoDB 8.0 with authentication: error 13 *Unauthorized*, see
   `docs/evidence/pytest_mongodb.txt`). Re-run the script after adding a collection.
2. Anchor the audit chain outside the database every day (or after each approval batch):
   ```bash
   meridian audit anchor            # prints {"head": ..., "seq": ...} — store it in a ticket, WORM bucket or signed email
   meridian audit check-anchor --head <head> --seq <seq>   # exit 2 if the ledger was altered or rewritten
   meridian audit verify            # full chain verification, exit 2 on any break
   ```
   A hash chain makes tampering detectable, not impossible; the external anchor is what detects a complete rewrite.

## 6. Backup and restore

**SQLite (default single-node store, ₹0):**
```powershell
meridian db status                        # schema version, record counts, integrity, audit chain
meridian db backup --out backups\meridian-2026-10-12.db   # online, consistent, verified (integrity + audit chain)
# restore: STOP the API first
meridian db restore --from backups\meridian-2026-10-12.db  # refuses a backup that fails verification;
                                                           # keeps the current file as *.pre-restore-<timestamp>
```
Schedule `meridian db backup` daily with Windows Task Scheduler (or cron) and copy the file off the machine.

**MongoDB:**

MongoDB Atlas: enable continuous cloud backup with point-in-time restore (M10+ tiers; cost depends on the tier).
Self-managed MongoDB: install the MongoDB Database Tools and run nightly:
```bash
mongodump  --uri "$BACKUP_URI" --db meridian --gzip --archive=meridian-$(date +%F).gz
mongorestore --uri "$RESTORE_URI" --gzip --archive=meridian-YYYY-MM-DD.gz --nsFrom 'meridian.*' --nsTo 'meridian_restore.*'
```
After a restore: run `meridian audit verify` and `meridian audit check-anchor` against the last published anchor
before the restored database is used. Test a restore at least quarterly. (The release build did not have
`mongodump` installed, so this procedure is documented but was not executed in verification.)

## 7. Upgrade and rollback

1. Take a backup (section 6) and publish an audit anchor.
2. Install the new release into a NEW virtual environment and a NEW folder (never on top of the old one):
   `pip install -e .` binds Python to the folder it was run in, so an unzipped copy elsewhere is ignored.
3. Run `meridian doctor`, `pytest -q`, then start the API and check `/ready` and `/health` (version shown).
4. Rollback = stop the new service, start the previous folder/virtual environment. Data written by v3.2 stays
   readable by v3.1 except audit records with `"v": 2`, which v3.1 cannot verify (v3.2 verifies both formats).

## 8. Incident response

| Signal | First action |
|---|---|
| `meridian audit verify` fails | Freeze approvals (remove `decide_actions` from roles), snapshot the database, compare with the last anchor, escalate to Internal Audit |
| AI labelled "Claude error → deterministic fallback" repeatedly | Check provider status and `meridian llm-check`; the platform keeps working on deterministic reasoners |
| "budget exhausted" fallbacks | Raise `MERIDIAN_LLM_MAX_TOKENS_PER_RUN` only after reviewing spend; each run records its token usage |
| Public source FAILED / stale in Data Lineage | Check the source's status page; the dated snapshot stays in use and is labelled SNAPSHOT |
| Suspected credential leak | Rotate `MERIDIAN_JWT_SECRET` (invalidates all sessions), rotate the DB password and API keys, review the audit ledger |

Every request carries an `X-Request-ID` header that also appears in the JSON access log line, so a user-reported
problem can be traced end to end. Logs never contain tokens, query strings or request bodies.

## 9. Troubleshooting

- **The browser still shows old behaviour after an upgrade.** Python is still importing the old folder (see 7.2) or
  the browser cached old scripts: reinstall with `pip install -e .` inside the new folder and reload with Ctrl+Shift+R.
- **Audit, Operations or Administration pages look empty.** The signed-in role is not allowed to see them (e.g. Finance
  Analyst). Sign in as Treasurer, CFO, Internal Audit or Administrator.
- **Adding a company returns 503.** SEC EDGAR was unreachable after the bounded retries; try again later. A 404 means
  the ticker/CIK is not an SEC registrant; a 422 means the input was malformed.
- **Live source tests.** `MERIDIAN_NETWORK_TESTS=1 pytest tests/network` exercises SEC, NY Fed, U.S. Treasury, FRED
  and ECB for real; they are kept out of deterministic CI.

## 10. Access, approvals and data freshness (v3.4)

**Privileged role grants (four-eyes).** Treasurer, CFO and Administrator roles can approve money movements or manage
users. Through the API or the Administration page, granting one creates a *pending grant* (a new account is created
inactive); a **different** administrator must confirm it under Administration → Privileged role grants. The requester
and the grantee cannot confirm, and no one can change their own role. Every request, confirmation, rejection and
refused attempt is in the audit trail.

Bootstrap (first administrator, or a second administrator when only one exists) is an out-of-band, server-side step:

```powershell
meridian users create --username second.admin --name "Second Admin" --role admin   # password prompted
```

This requires shell access to the server and is the break-glass path: record who ran it and why.

**Approval integrity.** A decision is refused (HTTP 409, audited) when:
- the action was already decided (replay);
- a newer review for the same company exists (the older run is *superseded*; decide on the current one);
- the policy version changed since the run (re-run the review);
- another decision changed the same run at the same moment (compare-and-swap conflict: reload and retry).

Action IDs (A001, A002, ...) are numbered **per run**; the approval queue shows `RUN-ID / A001` to keep them unambiguous.

**Data freshness.** If a company's latest filed quarter is more than 135 days before the as-of date, every page shows
a STALE warning and Data Lineage shows `PASSED_WITH_WARNINGS`. Refresh the company (Add company → same ticker) when
SEC EDGAR is reachable; if it is not, the dated snapshot stays in use and stays labelled.

**Lockout.** After 5 failed sign-ins an account is locked. The sign-in screen shows the same message as a wrong password
(so it cannot be used to find valid usernames); administrators see "locked" in Administration and can unlock.
