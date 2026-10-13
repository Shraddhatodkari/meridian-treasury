# Changelog

All releases are built with `git archive` from a tagged commit; verification evidence is in `docs/evidence/` and the
production-readiness report.

## 3.5.4 — final review fixes

* Scenario Lab working-capital lines labelled as **cash released (+) / absorbed (−)** (the arithmetic was right, the
  old "cash tied up" label meant the opposite); a test checks the lines add up to the free-cash-flow change.
* Multiples always shown with two decimals; "No recommendation needs a decision" when no rule triggers.
* All 54 reported figures of the five bundled companies re-checked against live SEC: 54 match.

## 3.5.3

* Interest cover stored at two decimals (Caterpillar 7.28x, previously displayed 7.30x); singular/plural in briefings.

## 3.5.2

* Policy arithmetic on unrounded same-date figures (Caterpillar PC-2.1: 64 %, previously 65 % from a rounded ratio).
* Working-capital change shown with direction; approval queue separates superseded runs; Value at Stake method text.

## 3.5.1

* TTM = latest fiscal year + current YTD − prior-year YTD from the latest filings, so restated prior periods are
  handled (Honeywell TTM cost of revenue corrected from $22.92B to $24.16B after its spin-off recast).
* Approval evidence shows the real status; evidence lists only values actually used.

## 3.5.0 — Value at Stake (real data, ₹0)

* **Cross-company working-capital study on real SEC filings:** 390 large US-listed companies, fiscal year 2025.
  **$152.1B** of cash is tied up in receivables and inventory beyond peer medians ($355.1B to top quartile); supplier
  terms ($53.1B) are reported separately, and 12 companies whose peer comparison is unfair (e.g. Amazon, Microsoft) are
  routed to segment review instead of inflating the headline. Every input links to its SEC accession number.
  See [`docs/VALUE_AT_STAKE_STUDY.md`](docs/VALUE_AT_STAKE_STUDY.md).
* **Forecast validation on real transactions:** 1,067,371 real invoice lines (UCI Online Retail II, D. Chen, CC BY 4.0), 30 rolling origins,
  13-week horizon, no leakage — results reported as they are, including where the method does not beat a baseline.
* **Board report:** one click from the Value at Stake workspace; every figure computed at render time.
* **Demo and interview guide:** [`docs/DEMO_AND_INTERVIEW_GUIDE.md`](docs/DEMO_AND_INTERVIEW_GUIDE.md).

## 3.4.0 — Integrity and control hardening

* **No silently mixed dates:** liquidity and its coverage now use cash, facilities and debt from one balance-sheet
  date (this corrected Coca-Cola's coverage from 3.75x to 5.27x). Any metric that combines dates by definition says so.
* **Stale-data warnings:** a snapshot more than 135 days behind its latest filed quarter is flagged on every page and
  in Data Lineage.
* **Approval integrity:** decisions on superseded runs or under a changed policy version are refused; replays are
  refused; two simultaneous decisions cannot both succeed (compare-and-swap on the run revision in SQLite, MongoDB and
  memory); the decision audit record carries the policy version and evidence.
* **Four-eyes on privileged roles:** granting Treasurer, CFO or Administrator through the API needs a second
  administrator; nobody can change their own role; lockout no longer reveals whether an account exists.
* **New tests:** formula reconciliation for every bundled company, TTM vs annual + YTD, units, staleness, invalid
  inputs, escalation, replay, concurrency, SEC outage.

## 3.3.0 — Persistence by default

* **Persistent by default:** users, runs, approvals, decisions, added companies and the audit chain are stored in
  SQLite (`var/meridian.db`, part of Python — no server, no Docker, no cost) and survive restarts (tested across three
  separate processes). Versioned migrations, verified online backup and restore (`meridian db ...`). The audit table is
  append-only inside the database (triggers reject UPDATE/DELETE). MongoDB remains supported for multi-replica setups.
* **No silent memory fallback:** in-memory storage is for automated tests only and is refused in production mode.
* **Access control proven on every route:** all 70+ `/v1` endpoints refuse anonymous calls; each role's permissions
  are tested on direct API calls.
* **Bank statement quality:** opening + entries = closing reconciliation to the cent, duplicate statement and
  transaction detection.
* **AI at ₹0:** deterministic, evidence-grounded reasoning needs no API key and makes no external AI calls; Claude is
  optional and off unless you set a key.

## 3.2.0 — Production hardening

* **Precision-aware grounding:** every $ figure and multiple an AI writes must equal a tool-computed value at the
  precision shown (`$12.63B` ±$5M), with sign and currency checks, instead of a blanket 2% tolerance.
* **Claude client:** outbound DLP redaction, timeouts and bounded retries, token budget per run, and honest labels:
  live Claude, offline deterministic, or a labelled fallback after an error or exhausted budget.
* **Resilient public-data layer:** retries with backoff, `Retry-After`, SEC fair-access throttling, caching; opt-in
  live tests against the real sources.
* **Audit you can defend:** sequence-bound hashes, tamper tests, external chain anchors, and a MongoDB role that can
  append to the audit log but never change or delete it.
* **Operations:** JSON access logs with request ids, release secret scanner, dependency audit, runbook for backup,
  rollback and incidents. See [`PRODUCTION_READINESS_REPORT.md`](PRODUCTION_READINESS_REPORT.md) for what is verified
  and what still needs client systems or credentials.
