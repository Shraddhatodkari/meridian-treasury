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
