# Meridian 3.5.4 — Production Readiness Report

**Date:** 2026-10-12 · **Baseline:** 3.4.0 (tag `v3.4.0`) · **Added cost: ₹0** · no Docker, no WSL ·
**Previous reports:** [`v3.4`](docs/PRODUCTION_READINESS_REPORT_v3.4.md) (financial-integrity, security and approval
hardening, in full) · [`v3.3`](docs/PRODUCTION_READINESS_REPORT_v3.3.md)

**Bottom line.** Meridian is a working, tested treasury and working-capital intelligence platform on **real public
data**: SEC filings for 390 large US companies plus five deep-dive companies, public market rates, and 1.07 million
real retail invoice lines for forecast validation. Recommendations go through governed approvals with a tamper-evident
audit trail. It is **not a live enterprise deployment**: the blockers below need a client's systems and credentials, a
deployment environment or an independent security firm. No certification or penetration test is claimed.

## 1. Production blockers, in priority order

| # | Blocker | Why it blocks | What closes it |
|---|---|---|---|
| 1 | **No enterprise SSO (OIDC/SAML); local accounts only** | Joiner/mover/leaver, MFA and password policy are not under the client's identity provider | Integrate the client IdP; map IdP groups to roles; keep local sign-in for break-glass only |
| 2 | **No tenant isolation inside one database** | Two client organisations must never share a database today | One deployment + one database per client (current rule), or tenant-scoped queries with negative tests |
| 3 | **No client ERP / bank ledger connected** | Customer payment-timing models are validated on synthetic data; the cash-in forecast method is validated on real retail sales (a proxy), not on a client's receivables | Pilot with one company's ERP and bank exports (importers exist); re-validate on that history |
| 4 | **Not deployed; no independent security assessment** | TLS, secrets, network rules, off-host backups and monitoring are documented, not operated | Deploy to the target environment, run a restore drill, commission a penetration test |
| 5 | **Single-node SQLite by default** | One API host; the database file must be backed up off the machine | MongoDB (supported, tested) for several replicas; scheduled off-host backups |
| 6 | **Audit is tamper-evident, not immutable** | The hash chain detects edits, deletions and reordering; someone with full storage access could still replace the store. Anchors catch that only if stored out of their reach | Ship anchors to WORM/object-lock storage owned by another team |
| 7 | **Per-entity / per-currency approval limits not modelled** | Thresholds are by amount and action type | Model them in `config/treasury_policy.yaml` if the client policy requires |
| 8 | **Value-at-stake study is outside-in** | Peer benchmarks from public filings; diversified companies need segment-level data, and opportunities are hypotheses until management validates them | Segment-level benchmarks; validation with a company's own data |

Closed in 3.5: **static typing** (was blocker #8 in 3.4) — `mypy src/meridian` now reports 0 errors (261 before) and runs
as a test.

## 2. What 3.5 changed

### Business problem: cash trapped in working capital (real data)

| Item | What it does | Evidence |
|---|---|---|
| Cross-company study | 454 SEC filers with revenue ≥ $1B and all five line items at the same fiscal-year end → 390 benchmarked in 19 sectors; 64 excluded with reasons. **$152.1B** cash to peer median (receivables + inventory), **$355.1B** to top quartile, supplier terms **$53.1B** reported separately, **$105.3B** routed to segment review (12 companies, e.g. Amazon, Microsoft, Apple, Alphabet, which are most of their SEC peer group) | `docs/VALUE_AT_STAKE_STUDY.md`, `tests/unit/test_value_at_stake.py` |
| Data integrity rules | Flows and balances share the same fiscal-year-end date; one concept per line item in fixed priority; USD/us-gaap only; dataset checksummed (tampering detected); every input carries its SEC accession and a link | same tests: dates, formulas recomputed from filing values, totals, exclusions |
| Peer groups | Finest SEC SIC level with ≥ 8 companies (4-digit → 3-digit → sector); dominance (> 50 % of group revenue) and far-out (quartile ± 3 × IQR) rules keep business-mix gaps out of the headline | tested |
| Forecast validation on real transactions | UCI Online Retail II (1,067,371 invoice lines, CC BY 4.0), 102 weeks, 30 rolling origins, 13-week horizon, no leakage (tested by poisoning future data). Meridian's seasonal × level method: WAPE 20.7 % vs naive 27.5 % (−24.7 %), but **not better than seasonal-naive (20.4 %)**; 80 % band covered **70 %** (too narrow). Reported as is | `tests/unit/test_value_at_stake.py` |
| Product | **Value at Stake** workspace (study, company lookup with filing links, segment-review list, forecast validation, method and limitations); peer card on each company's Working Capital page; one-click **board report** (self-contained HTML, every number computed at render time) | browser check; report test |
| Interview material | 3-minute demo script and honest Q&A for consulting, advisory and payments interviews | `docs/DEMO_AND_INTERVIEW_GUIDE.md` |

### Engineering

* **Type checking:** 261 → **0** mypy errors with no `# type: ignore`; `[tool.mypy]` in `pyproject.toml`;
  `tests/unit/test_typecheck.py` fails the suite on any new error. Wrong annotations corrected (e.g. `load_group()`
  can return `None`); the MongoDB store now returns `None` when not seeded, like the other stores, instead of raising.
* **Untrusted XML:** bank statements (camt.053) and public XML feeds are parsed with `defusedxml`; entity-expansion
  ("billion laughs") and external-entity (XXE) payloads are refused — tested.
* **Static security scan (Bandit):** 0 high, 0 medium after the XML fix; 5 low, all reviewed: two "hardcoded password"
  hits are the literal `"bearer"` token type and the published demo password (refused outside demo mode, tested);
  three are imports of `xml.etree` used only to *build* XML or as a type. Report: `docs/evidence/bandit.txt`.
* An `assert` used for type narrowing in a policy rule was replaced by an explicit error (asserts are removed under `python -O`).

### 3.5.1 fixes (found while reviewing a real Apple run)

| Finding | Fix | Evidence |
|---|---|---|
| **TTM wrong after a recast.** Honeywell recast its 2025 periods after a spin-off (H1-2025 cost of revenue $12.366B as first filed, $11.121B as recast — verified against live SEC). Deriving Q4 as FY − original 9-month and summing quarters mixed the two bases: TTM cost of revenue was **$22.92B instead of $24.16B** (−5.2 %), overstating DIO/DPO | TTM = latest FY + current YTD − prior-year YTD from the latest filings (recast-safe, 52/53-week years handled); the quarter sum is kept and any gap is disclosed on the metric | `test_ttm_survives_recast_prior_periods`; the TTM bridge test now checks every bundled company including Apple |
| Approval Center evidence chain showed **PROPOSED** for a **SUBMITTED** action | Shows the action's real status and who submitted it | browser check |
| Evidence listed a metric that was NOT AVAILABLE (cost of debt for Apple) | Evidence lists only values the recommendation actually used | `test_recommendation_evidence_lists_only_available_metrics` |
| Apple's inventory review could read as a peer gap | PC-3.1 now states when the company is still below the peer median (Apple DIO 16.9 vs 92.1): a change versus its own history | rule output |

### 3.5.2 fixes (found while reviewing a real Caterpillar run)

| Finding | Fix |
|---|---|
| PC-2.1 showed debt due as **65 %** of liquidity: it divided by the *rounded* coverage (1/1.55). Actual $12.634B / $19.638B = **64 %** | Rule arithmetic (PC-2.1 share, PC-1.1 shortfall) uses the unrounded same-date debt; regression test added |
| "Trade working capital $3.77B" read as a level, not a change | "trade working capital up $3.77B — more cash tied up" |
| Approval queue mixed blocked actions from superseded runs with current ones (6 items, 3 actionable) | Current actions first; header shows "3 · 6 on superseded runs, blocked" |
| Value at Stake method text was cut from a docstring and started mid-sentence | Fixed method text |

### 3.5.3 fixes (final review of the Caterpillar run)

| Finding | Fix |
|---|---|
| Interest cover shown as **7.30x**, but it was rounded to one decimal first: $13.413B / $1.842B = **7.28x** (false precision) | Stored at two decimals like the other ratios |
| Briefing text "3 recommendation(s) need a decision" | Proper singular/plural |

### 3.5.4 fixes (final review of the Apple run)

| Finding | Fix |
|---|---|
| All 54 REPORTED figures of the 5 bundled companies re-checked against live SEC (2026-10-13): **54 match, 0 differ** — `docs/evidence/sec_reconciliation_all_companies.txt` | — |
| Briefing said "0 recommendations need a decision" | "No recommendation needs a decision" |
| Scenario Lab labelled free-cash-flow effects as "Cash tied up": Caterpillar showed "Cash tied up in receivables $237.9M" when that cash is actually **released** (−15 % revenue shrinks receivables more than +10 days adds); Apple showed −$6.16B for cash that is **absorbed**. The arithmetic was right, the label had the opposite meaning | Lines now read "Receivables: cash released (+) / absorbed (−)"; a test checks the lines add up to the FCF change for every company |
| Ratios lost their trailing zero on some tiles ("0.5x" next to "0.50x") | All multiples shown with two decimals |

Roadmap with what is built vs what needs a client: [`docs/ROADMAP_TO_LIVE_DEPLOYMENT.md`](docs/ROADMAP_TO_LIVE_DEPLOYMENT.md).

## 3. Verification

| Check | Result |
|---|---|
| Full suite, clean install, MongoDB 8.0.15 and live network enabled | **381 passed, 0 failed, 0 skipped** (v3.5.4) |
| Deterministic suite (`pytest -q`) | 369 + 11 opt-in (6 MongoDB, 5 live-network) skipped when those are not configured |
| Tests added in 3.5 / 3.5.1 | 20 value-at-stake + 1 type check + 1 hostile-XML + 2 integrity = 24 |
| Ruff (incl. security rules) | clean |
| mypy | 0 errors (90 source files) |
| Bandit | 0 high / 0 medium / 5 low (reviewed, above) |
| pip-audit | no known vulnerabilities |
| Secret / unintended-file scan of the ZIP | clean |
| Real server restart (default SQLite) | run, approval, denial (403), added company and users survive; audit valid 35 → 37 records; backup/restore verified |
| Browser (headless Chromium) | 30 page loads incl. Value at Stake, company lookup and board-report download: 0 problems, 0 JavaScript errors |

## 4. Readiness by category

**Verified locally (executed here):** value-at-stake computation and its data rules on real SEC filings; forecast
back-test on real transactions with no leakage; Caterpillar figures equal to live SEC; formula, period and
date-consistency tests on all deep-dive companies; staleness warnings; RBAC on every route; four-eyes role grants;
lockout without enumeration; token revocation; replay, superseded-run, policy-drift and concurrency guards; hostile
XML refused; persistence across a real restart; backup/restore; audit tamper detection; MongoDB path; SEC outage
handling; UI end to end; type checking.

**Implemented but not independently validated:** password hashing and session design; audit anchoring procedure; DLP
before optional Claude calls; security headers; peer-group methodology (a reasonable, documented choice — other
groupings would give different totals).

**Requires real client data or credentials:** ERP and bank connections (parsers tested on files); payment-timing and
13-week forecast accuracy on a real receivables ledger; segment-level benchmarks; live Claude (optional, billed
separately); enterprise SSO.

**Requires deployment or independent security validation:** TLS and network configuration; secret management; off-host
backups and restore drill; monitoring and alerting; WORM storage for audit anchors; penetration test.
