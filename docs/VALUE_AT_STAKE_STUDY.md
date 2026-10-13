# Working-capital value at stake — large US-listed companies, fiscal year 2025

*Meridian 3.5 · data: SEC EDGAR XBRL (frames + submissions APIs), retrieved 2026-10-12 · all inputs PUBLIC · REAL ·
metrics CALCULATED · opportunity ESTIMATED. Figures below are reproduced by `GET /v1/benchmark/study`; the in-app
"Value at Stake" workspace and the downloadable board report are generated from the same computation.*

## Headline

**$152.1B of cash is tied up in receivables and inventory beyond peer medians** across **390** large US-listed
companies (revenue ≥ $1B, $6.95T combined revenue, $719B trade working capital).

| Case | Receivables | Inventory | Total (headline) | Supplier terms (separate) | Needs segment review |
|---|---|---|---|---|---|
| Conservative — move to peer **median** | $72.4B | $79.8B | **$152.1B** | $53.1B | $105.3B |
| Stretch — move to peer **top quartile** | $165.2B | $189.8B | **$355.1B** | $143.6B | $206.6B |

## Findings

1. **Receivables and inventory contribute almost equally** to the conservative opportunity (48 % / 52 %).
2. **Pharmaceuticals & biotech ($27.0B), retail ($22.2B) and wholesale distribution ($20.6B)** hold the largest pools.
3. **Largest single opportunities** (conservative case): Eli Lilly (inventory 454 days vs a peer median of 179 — this may
   reflect a deliberate build for new products, so it is a question for management, not a finding of inefficiency),
   Home Depot, Caterpillar (inventory 148 days vs 98 for industrial-machinery peers: $6.1B), TD SYNNEX and Ingram Micro
   (receivables).
4. **Supplier terms are kept out of the headline.** Matching peer-median payment terms would add $53.1B, but that cash
   comes from suppliers; it should only be pursued alongside supplier protection (supply-chain finance or commercial
   card programmes) — which is where payment networks and banks create value for both sides.
5. **12 companies need segment-level work ($105.3B, excluded)**: Amazon, Microsoft, Apple and Alphabet each make up more
   than half of their SEC industry group's revenue, so their small "peers" are not comparable (e.g. Amazon's
   receivables reflect cloud and advertising, not retail). Others sit beyond quartile ± 3 × IQR on a lever.

## Method

* DSO = trade receivables / revenue × 365 · DIO = inventory / cost of revenue × 365 · DPO = payables / cost of
  revenue × 365 · CCC = DSO + DIO − DPO.
* Flows: each company's fiscal year that SEC assigns to calendar 2025 (350–380 days; years ending June 2025 to February
  2026). Balances: **the same fiscal-year-end date** — dates are never mixed.
* One concept per line item in a fixed priority order (e.g. `Revenues`, then `RevenueFromContractWithCustomer…`).
* Peers: the finest SEC SIC level with ≥ 8 companies (4-digit industry → 3-digit group → broad sector).
* Opportunity = max(0, company days − peer benchmark days) × daily revenue (receivables) or daily cost of revenue
  (inventory, payables). Companies already better than the benchmark contribute zero.
* Exclusions (64 of 454, each with a reason): financials and utilities (not comparable), implausible values
  (DSO > 200, DIO > 500, DPO > 300), and companies without 8 peers even at sector level.

## Limitations

Year-end snapshots (seasonality); SIC groups are broad; receivables may include some non-trade items; opportunities
assume peer performance is achievable for each company — they are hypotheses for management discussion, not
forecasts. No company named here has reviewed these estimates. Not investment advice.

## Reproduce

```powershell
python scripts/build_wc_study.py          # re-fetch from SEC (free, no key), ~5 minutes at SEC fair-access limits
pytest tests/unit/test_value_at_stake.py  # formulas, dates, totals, exclusions, API
```

## Forecast validation on real transactions

Dataset: UCI Online Retail II (D. Chen, CC BY 4.0) — 1,067,371 real invoice lines of a UK online retailer,
Dec 2009 – Dec 2011, aggregated to 102 weeks (no customer data stored). 30 rolling origins, 13-week horizon, each
forecast uses only data available at its origin.

| Method | WAPE |
|---|---|
| Naive (last week) | 27.5 % |
| 4-week moving average | 24.4 % |
| Seasonal naive (same week last year) | **20.4 %** |
| Seasonal × level (Meridian) | 20.7 % |

Meridian's method is 24.7 % more accurate than naive, but **not better than plain seasonal-naive** on this data, and its
80 % band covered only 70 % of outcomes (too narrow). Both results are shown in the product as they are. Limitation:
these are weekly invoiced sales used as a cash-in proxy; customer payment-timing models still need a real receivables
ledger.
