# Roadmap to a live enterprise deployment

Status as of Meridian **3.5.4** (2026-10-12). Each phase lists what already exists in this repository (tested), what
needs a client or a third party, and the exit criterion. Nothing below is claimed as done unless marked **Built**.

| Phase | Weeks* | Built in Meridian today | Needs a client, credentials or a third party | Exit criterion |
|---|---|---|---|---|
| **1. Security & infrastructure** | 1–3 | RBAC on every route (tested); four-eyes privileged role grants; lockout without enumeration; token revocation; compare-and-swap approvals; tamper-evident audit with anchors; SQLite (default) and MongoDB stores with migrations, backup/restore; Bandit/ruff/mypy/pip-audit clean | Enterprise SSO (OIDC/SAML with Entra ID or Okta: the client's tenant); tenant isolation if several clients share one database (today: one deployment per client); hosting, TLS, secrets manager; **independent penetration test** | SSO live with group→role mapping; pen-test findings closed |
| **2. ERP & bank data** | 4–7 | Governed ingestion with validation, lineage and quarantine: ERP CSV package, ISO 20022 **camt.053** and **BAI2** parsers (reconciliation, duplicate detection, hostile-XML protection) — tested on files | ERP API access (SAP S/4HANA OData, Oracle Fusion REST, NetSuite SuiteQL) and bank delivery (SFTP / SWIFT FileAct / H2H) — all require the client's systems and credentials | Daily AR/AP and bank statements loaded and reconciled for 20 consecutive business days |
| **3. Predictive engine** | 8–11 | **13-week Monte Carlo cash forecast** (paths set by `MERIDIAN_MONTE_CARLO_PATHS`) driven by a **gradient-boosted quantile model** (P10/P50/P90) of invoice payment delay — validated on the synthetic case; forecast method back-tested on 1.07M **real** retail invoices (results reported honestly) | Training and validation on the client's own receivables history; back-test vs the treasury team's current forecast | Beats the client's current forecast on a 13-week rolling back-test |
| **4. Execution & pilot** | 12–16 | ISO 20022 **pain.001 DRAFT** generated only for approved payment actions; validation → READY; dual-control release (approver ≠ releaser ≠ requester); transmission channel `none` — Meridian cannot send money | Bank H2H gateway, signing keys in an HSM or bank token, bank certification; 4-week parallel run with the treasury team | Parallel run with zero unexplained differences; bank sign-off |

\* Indicative, assuming a client sponsor, system access in week 1 and an available security firm.

## What should never be skipped
* Payment transmission stays disabled until phase 4 exit criteria are met — approval in Meridian is a decision
  mandate, not a payment.
* Public-filing figures (SEC) remain labelled PUBLIC · REAL; client data enters only through governed ingestion and is
  labelled CLIENT; synthetic data stays SYNTHETIC. They are never mixed.
* No certification or "production" claim before the independent security test and the parallel run.

## Notes on proposals that change the cost model
* Managed databases such as AWS Aurora or CockroachDB Cloud are paid services. Meridian already supports MongoDB
  (self-hosted free) for several replicas; PostgreSQL with row-level security is an option if a client standardises on it.
* 10,000 Monte Carlo paths per run is possible (`MERIDIAN_MONTE_CARLO_PATHS`) but multiplies run time accordingly; more
  paths reduce simulation noise, not model error — validate before raising it.
