# Meridian 3.4.0 — Production Readiness Report

**Date:** 2026-10-12 · **Baseline:** 3.3.0 (tag `v3.3.0`, 300 passed / 11 skipped before any change) · **Added cost:
₹0** · no Docker, no WSL · **Previous report:** [`docs/PRODUCTION_READINESS_REPORT_v3.3.md`](docs/PRODUCTION_READINESS_REPORT_v3.3.md)

**Bottom line.** Meridian is a working, tested treasury-intelligence application on real SEC and market data, with
governed approvals and a tamper-evident audit trail. It is **not ready for a live enterprise deployment**: the
blockers in section 1 (enterprise SSO, tenant isolation, a real client data connection, deployment and independent
security testing) need client systems, credentials or third parties that were not available here. No security
certification or penetration test is claimed.

## 1. Production blockers, in priority order

| # | Blocker | Why it blocks | What closes it |
|---|---|---|---|
| 1 | **No enterprise SSO (OIDC/SAML); local accounts only** | Joiner/mover/leaver, MFA and password policy are not under the client's identity provider | Integrate the client IdP; map IdP groups to Meridian roles; disable local sign-in except break-glass |
| 2 | **No tenant isolation inside one database** | Two client organisations must never share a database today | Keep one deployment + one database per client (current rule), or build tenant-scoped queries with negative tests |
| 3 | **No real client ERP / bank data connected** | 13-week forecast and working-capital models are validated only on synthetic data | Connect one authorised ERP export + one bank statement feed; re-run forecast validation on that history |
| 4 | **Not deployed; no independent security assessment** | TLS termination, secret storage, network rules, backups off-host and monitoring exist only as documentation | Deploy to the target environment, run a restore drill, commission a penetration test |
| 5 | **Single-node SQLite by default** | One API host only; the database file must be backed up off the machine | Use MongoDB (supported, tested) for several replicas; schedule and test off-host backups |
| 6 | **Audit storage is tamper-evident, not immutable** | A hash chain detects edits, deletions and reordering; an administrator with file or database access could still replace the whole store. External anchors (`meridian audit anchor`) catch a full rewrite only if the anchor is stored somewhere they cannot reach | Ship anchors to WORM/object-lock storage owned by a different team |
| 7 | **Per-entity / per-currency approval limits not modelled** | Thresholds are by amount and action type only | Model them in `config/treasury_policy.yaml` if the client's policy requires it |
| 8 | **Static typing not clean** | `mypy` reports 260 issues (262 before this release); mostly missing annotations and optional-value handling. Not a test failure, but a maintainability risk | Burn down by module; add `mypy` to CI once clean |

## 2. What 3.4 changed (P0 → P2)

### P0 — Financial data integrity

| Finding | Fix | Evidence |
|---|---|---|
| Liquidity added cash and facilities from **different dates** for some issuers (Coca-Cola: cash at one date, facilities at another). Coverage showed 3.75x | Liquidity and liquidity coverage now use cash, undrawn facilities and debt due ≤12 months from **one** balance-sheet date; the label states the date and why it may differ from other debt metrics. Coca-Cola coverage is now **5.27x** (2025-12-31) | `test_no_silent_mixing_of_balance_sheet_dates` (every bundled company) |
| Two metrics combine dates by definition (average cost of debt; refinancing cost uses today's market) | They now carry an explicit `mixed_dates_reason` and the period label names every date | same test: any multi-date metric without a reason fails |
| PC-2.1 (refinancing readiness) compared debt at one date with liquidity at another | Rule test and action amount use the same-date figures. Effect: Coca-Cola no longer triggers PC-2.1 (19 % vs 25 % trigger); Deere triggers PC-1.1 and PC-2.1 | `test_recommendation_shortfall_and_share_use_same_date_figures` |
| **Crash**: for Deere, whose later debt tagging is unusable, the refinancing rule raised `TypeError` once same-date figures were used | Fixed; Deere now shows liquidity $15.58B and coverage **1.13x** at 2025-11-02, below the 1.25x reference floor | evaluation of all bundled companies in the test suite |
| No warning when a snapshot is behind the filing cadence | `freshness()`: latest filed quarter older than 135 days (one quarter + 45-day 10-Q deadline) → **STALE** warning on every company page and `PASSED_WITH_WARNINGS` in Data Lineage. Coca-Cola is flagged today (latest quarter 2026-04-03; live SEC also has nothing newer as of 2026-10-12) | `test_staleness_is_flagged_and_propagates_to_provenance` |
| Formulas were tested indirectly | Every bundled company: FCF = OCF − capex (capex positive), EBITDA, net debt, liquidity, coverage, +100 bps repricing, DSO/DIO/DPO/CCC, trade working capital recomputed independently; TTM checked as four consecutive quarters **and** against FY + current YTD − prior YTD from raw facts | `tests/unit/test_financial_integrity.py` (30 tests) |
| Units / restatements | Non-USD units and non-10-K/10-Q forms are ignored; the latest filing wins for restated periods | `test_extract_ignores_non_usd_units_non_periodic_forms_and_keeps_latest_restatement` |
| Invalid inputs | Out-of-range, `nan`, `inf` and non-numeric scenario inputs → 422; unknown company → not fabricated | parametrised tests |

**Caterpillar traced to SEC (live, 2026-10-12):** all 14 REPORTED metrics equal the latest 10-Q/10-K fact for the same
concept and period — `docs/evidence/cat_sec_reconciliation.txt`. Every CALCULATED/ESTIMATED figure is derived from
those facts by the formulas tested above.

### P0 — Security

| Finding | Fix | Evidence |
|---|---|---|
| An administrator could give **themselves** an approver role, or create a CFO account they control and use it to approve | Nobody can change their own role (403, audited). Granting Treasurer, CFO or Administrator through the API creates a **pending grant** (new accounts are created inactive) that a **different** administrator must confirm; requester and grantee cannot confirm; replays → 409. Bootstrap is the server-side CLI only | `test_admin_cannot_change_own_role`, `test_privileged_grant_needs_a_second_admin` |
| The lockout message revealed that an account exists | Locked, unknown and wrong-password sign-ins now return the same 401 body with the same hashing work; administrators still see "locked" | `test_lockout_does_not_reveal_account_existence`, updated `test_account_lockout_after_repeated_failures` (see disclosure) |
| CSRF | Not applicable: sessions are bearer tokens in the `Authorization` header, no cookies are set — tested. If cookies are introduced, CSRF protection becomes mandatory | `test_no_cookies_so_csrf_does_not_apply` |
| Token replay | Logged-out tokens are refused; a role change or deactivation invalidates existing tokens | `test_logout_revokes_token`, `test_role_change_invalidates_existing_token` |
| Authorization coverage | Unchanged from 3.3 and re-run: every `/v1` route refuses anonymous callers except sign-in and the environment banner; role matrix tested on direct API calls | `tests/integration/test_access_matrix.py` |
| Tenant isolation | **Not implemented** — blocker #2 | — |
| Dependencies | `pip-audit`: no known vulnerabilities | `docs/evidence/pip_audit.txt` |

### P0 — Approval workflow and audit

**The "duplicate" A001/A002/A003 entries.** Action IDs are numbered per run, so every review has an A001. The queue
listed actions from older reviews next to the current ones, which looked like duplicates. Now:

* each queue item shows `RUN-ID / A001` (`qualified_action_id` in the API);
* actions on a **superseded** run (a newer review exists for the same company) are shown as blocked, and deciding or
  submitting them is refused (409, audited `decision_denied_superseded`);
* a decision is refused if the **policy version** changed since the run (409, audited);
* a **replayed** decision on an already-decided action is refused (409, audited `decision_replay_denied`);
* **concurrent** decisions are serialised by compare-and-swap on a run revision: SQLite (inside one `BEGIN IMMEDIATE`
  transaction, safe across processes), MongoDB (conditional `replace_one`) and memory. A losing writer gets 409 and no
  decision audit record is written for it;
* the decision audit record now carries actor, role, timestamp, decision, comment, **policy version**, policy
  references, required approver, recommendation id, run revision and the evidence values the recommendation rests on.

Execution stays disabled: no payment channel exists (`transmission_channel: none`, tested).

Evidence: `tests/integration/test_hardening_v34.py` — replay/superseded, policy drift, concurrency (two threads,
exactly one succeeds), store-level CAS for memory and SQLite, decision audit content.

### P1 / P2

* **Outage handling:** adding a company while SEC EDGAR is unreachable → 503 "no data was fabricated", nothing stored (tested).
* **Forecast:** Forecast Studio states the quarterly outlook is back-tested but **not validated for operational cash
  management**, and that the band reflects historical back-test error, not a guaranteed range. Back-tests remain point-in-time (no future data).
* **Rate shock:** +100 bps repricing applies to debt due within 12 months only, labelled ESTIMATED with the formula.
* **Labels:** PC-* rules are shown as "Meridian reference policy — not the issuer's own policy" on recommendation
  cards; the offline AI label is unchanged ("AI OFFLINE / DETERMINISTIC"); success toasts appear only after the API
  returns success.
* **Operations:** runbook section 10 covers role grants, approval refusals, staleness and lockout.

### Disclosed test changes (no assertion about security or correctness was removed)

1. `test_account_lockout_after_repeated_failures` asserted the word "locked" in the sign-in response. That message is
   now deliberately identical to a wrong password (enumeration fix). The test still proves the lockout: the correct
   password is refused, and the account's failed-login counter is at the limit.
2. The MongoDB restart test created a review for Coca-Cola and submitted its first recommendation. With same-date
   liquidity, Coca-Cola's debt due ≤12 months is 19 % of liquidity (was 27 % with mixed dates), below the 25 % PC-2.1
   trigger, so it correctly has no recommendation. The test now uses Honeywell; what it checks (runs, decisions and
   audit survive a restart on MongoDB) is unchanged.
3. The `run` fixture in `test_security_and_approvals.py` is now created per test instead of once per module, because
   a newer review now (correctly) blocks decisions on older ones. Each test still uses its own action.

## 3. Verification

| Check | Result |
|---|---|
| Full suite, clean install, MongoDB 8.0.15 and live network enabled | **355 passed, 0 failed, 0 skipped** (`docs/evidence/pytest_clean_install.txt`) |
| Deterministic suite (`pytest -q`) | **344 passed, 11 skipped** (skips = 6 MongoDB + 5 live-network, opt-in) |
| Tests added in 3.4 | 30 financial-integrity + 14 hardening = 44 |
| Ruff (incl. security rules) | clean |
| `mypy src/meridian` | 260 errors (262 at 3.3.0) — reported, not a gate (blocker #8) |
| `pip-audit` | no known vulnerabilities |
| Secret / unintended-file scan of the ZIP | clean (`scripts/scan_release.py`) |
| Real server restart (default SQLite config) | run, CFO approval, treasurer denial (403), added MSFT and 6 users survive; audit chain valid 35 → 37 records; backup and restore verified (`docs/evidence/restart_evidence.txt`) |
| Browser (headless Chromium): login, add MSFT live, 28 page loads | 0 problems, 0 JavaScript errors; STALE banner on Coca-Cola only; role-grant panel present |

## 4. Readiness by category

**Verified locally (executed here):** SEC reconciliation for Caterpillar; formula, period and date-consistency tests on
all bundled companies; staleness warnings; RBAC on every route; four-eyes role grants; lockout without enumeration;
token revocation; replay, superseded-run, policy-drift and concurrency guards; persistence across a real restart;
backup/restore; audit tamper detection; MongoDB path including append-only role; SEC outage handling; UI end to end.

**Implemented but not independently validated:** password hashing and session design; audit anchoring procedure;
DLP before optional Claude calls; security headers (CSP, HSTS on HTTPS); the deterministic reasoners' wording.

**Requires real client data or credentials:** ERP and bank connections (CSV, BAI2, camt.053 parsers are tested on
synthetic files only); 13-week forecast accuracy; live Claude (`ANTHROPIC_API_KEY`, billed separately and off by
default); enterprise SSO.

**Requires deployment or independent security validation:** TLS and network configuration; secret management;
off-host backups and restore drill; monitoring and alerting; WORM storage for audit anchors; penetration test.
