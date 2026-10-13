# Business case: working capital and liquidity intelligence for the CFO

*Structured the way a strategy-consulting team would present it to a CFO and Group Treasurer.*

## Executive summary

- **Situation.** Multinationals run treasury across dozens of legal entities, banks and currencies.
  ERPs report what *happened*. The 13-week cash forecast that drives funding decisions is usually a
  spreadsheet consolidated by hand each week.
- **Complication.** Cash sits idle in some subsidiaries while others run short or borrow locally. The
  group pays revolver interest while holding surplus elsewhere. Receivables are forecast as if customers
  pay on the due date, which they rarely do. Capital controls trap cash. Every move needs approvals that
  live in email.
- **Resolution.** An agentic copilot that (1) predicts when each customer will actually pay, (2) builds a
  probabilistic 13-week forecast for every entity, (3) solves for where cash should sit within policy,
  (4) answers "what if" questions in plain English, and (5) routes every recommended money movement
  through the approval matrix with a full audit trail. It never moves money itself.

## Measured results on the simulated group (Orion Industrial Group, SYNTHETIC, 17 entities)

These are measurements on synthetic data with hidden ground truth. They are evidence that the method works, not a forecast
for any real company.

| Lever | Measured | How |
|---|---|---|
| Forecast accuracy, open AR collections (13 wks) | **9.1 % WAPE vs 69.7 %** for the ERP "pays on due date" assumption | Point-in-time back-test: ledger rewound, model re-trained on data available then, compared with simulated actual settlements |
| Payment-delay prediction | **MAE 3.42 days** vs 5.47 (customer average) vs 21.38 (due date) | Time-based hold-out |
| Cash concentration + revolver paydown | **$60M** revolver repayment, **~$3.40M/yr** benefit from the optimiser | LP over P10 troughs, within policy; revolver priced at real SOFR (NY Fed) + 250 bps |
| Supplier discounts | ~$129K captured on the current open AP book (TP-2.3 test) | Implied return > cost of funds and liquidity headroom |
| Subsidiaries below operating buffer | 6 → **0** after recommended fundings | Optimiser constraint |

Reproduce these with `make eval` (seed 7). Out-of-sample seed 11 gives comparable results.

## Sizing the value for a real client (assumption-driven, replace with client data)

| Driver | Formula | Illustrative |
|---|---|---|
| Interest saved by repaying revolver with idle cash | repayable surplus × (revolver rate − deposit yield) | $60M × (6.39 % − 4.30 %) ≈ **$1.25M/yr** |
| Cash released by DSO improvement | gap to real peer median (SEC 10-K) × daily revenue | 8.5 days × ~$8.9M ≈ **$75M** one-off; × 6.39 % ≈ **$4.8M/yr** |
| Lower precautionary buffers from better forecasts | buffer reduction × cost of funds | depends on the client's risk appetite |
| Treasury analyst time | hours of weekly manual forecast consolidation | typically 1–3 FTE in a group this size |

## Implementation roadmap (12 weeks)

| Weeks | Workstream | Exit criteria |
|---|---|---|
| 1–3 | Connect ERP AR/AP and bank statement feeds (read-only), load the policy and facility documents | Data in MongoDB Atlas, MCP tools live in non-prod |
| 4–6 | Re-train the delay model on client history; back-test the forecast on last year's actuals | Forecast error clearly below the current process |
| 7–9 | Parallel run: copilot recommends, treasury decides as today; compare | Recommendation acceptance rate, time saved |
| 10–12 | Approval workflow in production for intercompany sweeps below treasurer limit | Internal Control sign-off on the control catalogue |

## Risks and mitigations

| Risk | Mitigation |
|---|---|
| AI moves money | No payment tool exists; humans approve; output is a *draft* pain.001 file |
| Wrong numbers in an executive briefing | Numbers come from code; numeric grounding check; deterministic fallback |
| Policy breach in a recommendation | Policy-as-code constraints inside the optimiser (buffers, capital controls, RCF-8.1) |
| Prompt injection via supplier documents | Chunks scanned at ingestion and quarantined |
| Data leakage to an LLM vendor | IBAN masking; option to run Claude inside the client's cloud (Vertex AI / Bedrock) |
| Model drift | Weekly back-test, interval-coverage monitoring, CI release gates |
