# Public-company case: Caterpillar Inc. (PUBLIC · REAL)

## Why Caterpillar
Caterpillar is an SEC registrant (CIK 0000018230) with complete us-gaap XBRL for cash, working capital, cash flow and
the debt maturity schedule. It is treasury-relevant: a large balance sheet, a captive finance arm, long-cycle inventory
and an active capital-return programme. It was already in Meridian's peer set. No technical or legal reason favoured a
different industrial: SEC data is public domain and accessed under the SEC fair-access policy (declared User-Agent,
fewer than 10 requests per second).

## Sources
| Source | Endpoint | Snapshot |
|---|---|---|
| SEC EDGAR XBRL company facts (Caterpillar) | `https://data.sec.gov/api/xbrl/companyfacts/CIK0000018230.json` | `src/meridian/data/public_company/caterpillar_snapshot.json` (dated, SHA-256 checksum) |
| SEC EDGAR peers (latest 10-K) | same API: Honeywell, 3M, Emerson, Parker-Hannifin, Illinois Tool Works, Dover | same file. Deere, PACCAR and Cummins were excluded because the required concepts are missing; the exclusion is shown in the UI |
| NY Fed SOFR / EFFR | NY Fed Markets API | `market_snapshot.json` |
| U.S. Treasury par yield curve | treasury.gov CSV | `market_snapshot.json` |
| FRED ICE BofA Single-A OAS (BAMLC0A3CA), BBB OAS (BAMLC0A4CBBB) | fred.stlouisfed.org CSV | snapshot `market_addenda` / `market_snapshot.json` |
| ECB euro reference rates | ECB eurofxref | `market_snapshot.json` |

Refresh: `python scripts/refresh_public_company.py`.

## Rules applied to the data
* Only `10-K` and `10-Q` USD facts filed by the issuer are used. When a period is restated, the latest filing wins and keeps its accession number.
* Discrete quarters: the 3-month value as filed, otherwise YTD − prior YTD, and Q4 = FY − 9-month YTD. Each derived quarter carries both source facts.
* TTM figures are the sum of four consecutive quarters; non-consecutive windows are refused.
* Debt lines in Caterpillar's 10-Qs use company-specific XBRL elements, which the companyfacts API does not publish. Debt is therefore taken from the latest 10-K. The current portion of long-term debt comes from the ASC 470 maturity schedule (principal), and this is disclosed on the metric.
* Figures are consolidated: they include Cat Financial debt and finance receivables. The Machinery/Energy & Transportation vs Financial Products split is dimensional XBRL and is NOT AVAILABLE in this source.

## Metric register (generated from the running code)
| Metric | Value | Basis | Period | Concept / calculation |
|---|---|---|---|---|
| Cash and cash equivalents | 6713000000 | REPORTED | as of 2026-06-30 | us-gaap:CashAndCashEquivalentsAtCarryingValue |
| Receivables – trade and other | 13188000000 | REPORTED | as of 2026-06-30 | us-gaap:AccountsReceivableNetCurrent |
| Receivables – finance (Cat Financial, current) | 10844000000 | REPORTED | as of 2026-06-30 | us-gaap:NotesAndLoansReceivableNetCurrent |
| Inventories | 20627000000 | REPORTED | as of 2026-06-30 | us-gaap:InventoryNet |
| Accounts payable | 10313000000 | REPORTED | as of 2026-06-30 | us-gaap:AccountsPayableCurrent |
| Remaining performance obligations (backlog-type disclosure) | 44100000000 | REPORTED | as of 2026-06-30 | us-gaap:RevenueRemainingPerformanceObligation |
| Derivatives – total notional | 29400000000 | REPORTED | as of 2026-06-30 | us-gaap:DerivativeNotionalAmount |
| Short-term borrowings | 5514000000 | REPORTED | as of 2025-12-31 | us-gaap:ShortTermBorrowings |
| Long-term debt due after one year | 30696000000 | REPORTED | as of 2025-12-31 | us-gaap:LongTermDebtNoncurrent |
| Long-term debt maturing in the next 12 months | 7120000000 | REPORTED | as of 2025-12-31 | us-gaap:LongTermDebtMaturitiesRepaymentsOfPrincipalInNextTwelveMonths |
| Committed credit facilities – capacity | 15837000000 | REPORTED | as of 2025-12-31 | us-gaap:LineOfCreditFacilityMaximumBorrowingCapacity |
| Committed credit facilities – remaining capacity | 9658000000 | REPORTED | as of 2025-12-31 | us-gaap:LineOfCreditFacilityRemainingBorrowingCapacity |
| Revenue (TTM) | 74729000000 | CALCULATED | 4 quarters ending 2026-06-30 (2025-09-30, 2025-12-31, 2026-03-31, 2026-06-30) | Σ last 4 quarters of us-gaap:Revenues |
| Cost of goods sold (TTM) | 49067000000 | CALCULATED | 4 quarters ending 2026-06-30 (2025-09-30, 2025-12-31, 2026-03-31, 2026-06-30) | Σ last 4 quarters of us-gaap:CostOfRevenue |
| Operating cash flow (TTM) | 13569000000 | CALCULATED | 4 quarters ending 2026-06-30 (2025-09-30, 2025-12-31, 2026-03-31, 2026-06-30) | Σ last 4 quarters of us-gaap:NetCashProvidedByUsedInOperatingActivities |
| Capital expenditure (TTM) | 2871000000 | CALCULATED | 4 quarters ending 2026-06-30 (2025-09-30, 2025-12-31, 2026-03-31, 2026-06-30) | Σ last 4 quarters of us-gaap:PaymentsToAcquirePropertyPlantAndEquipment |
| Operating profit (TTM) | 13092000000 | CALCULATED | 4 quarters ending 2026-06-30 (2025-09-30, 2025-12-31, 2026-03-31, 2026-06-30) | Σ last 4 quarters of us-gaap:OperatingIncomeLoss |
| Depreciation & amortization (TTM) | 2379000000 | CALCULATED | 4 quarters ending 2026-06-30 (2025-09-30, 2025-12-31, 2026-03-31, 2026-06-30) | Σ last 4 quarters of us-gaap:DepreciationDepletionAndAmortization |
| Dividends paid (TTM) | 2812000000 | CALCULATED | 4 quarters ending 2026-06-30 (2025-09-30, 2025-12-31, 2026-03-31, 2026-06-30) | Σ last 4 quarters of us-gaap:PaymentsOfDividendsCommonStock |
| Share repurchases (TTM) | 7224000000 | CALCULATED | 4 quarters ending 2026-06-30 (2025-09-30, 2025-12-31, 2026-03-31, 2026-06-30) | Σ last 4 quarters of us-gaap:PaymentsForRepurchaseOfCommonStock |
| FX effect on cash (TTM) | -71000000 | CALCULATED | 4 quarters ending 2026-06-30 (2025-09-30, 2025-12-31, 2026-03-31, 2026-06-30) | Σ last 4 quarters of us-gaap:EffectOfExchangeRateOnCashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents |
| Free cash flow proxy (TTM) | 10698000000 | CALCULATED | 4 quarters ending 2026-06-30 (2025-09-30, 2025-12-31, 2026-03-31, 2026-06-30) | operating cash flow − capital expenditure |
| EBITDA proxy (TTM) | 15471000000 | CALCULATED | 4 quarters ending 2026-06-30 (2025-09-30, 2025-12-31, 2026-03-31, 2026-06-30) | operating profit + depreciation & amortization |
| Shareholder returns (TTM) | 10036000000 | CALCULATED | 4 quarters ending 2026-06-30 (2025-09-30, 2025-12-31, 2026-03-31, 2026-06-30) | dividends paid + share repurchases |
| DSO (trade receivables) | 64.4 | CALCULATED | receivables at 2026-06-30; revenue TTM | trade receivables / revenue TTM × 365 |
| DIO | 153.4 | CALCULATED | inventory at 2026-06-30; COGS TTM | inventories / cost of goods sold TTM × 365 |
| DPO | 76.7 | CALCULATED | payables at 2026-06-30; COGS TTM | accounts payable / cost of goods sold TTM × 365 |
| Cash conversion cycle | 141.1 | CALCULATED | at 2026-06-30 | DSO + DIO − DPO |
| Trade working capital | 23502000000 | CALCULATED | as of 2026-06-30 | trade receivables + inventories − accounts payable |
| Cash and cash equivalents (FY end 2025-12-31) | 9980000000 | REPORTED | as of 2025-12-31 | us-gaap:CashAndCashEquivalentsAtCarryingValue |
| Total debt (FY end 2025-12-31) | 43330000000 | CALCULATED | as of 2025-12-31 | short-term borrowings + LTD maturing in 12 months + LTD due after one year |
| Net debt (FY end 2025-12-31) | 33350000000 | CALCULATED | as of 2025-12-31 | total debt − cash and cash equivalents |
| EBITDA proxy (FY 2025) | 13413000000 | CALCULATED | FY ending 2025-12-31 | operating profit + D&A |
| Gross leverage (total debt / EBITDA proxy) | 3.23 | CALCULATED | FY 2025 | total debt / EBITDA proxy |
| Net leverage (net debt / EBITDA proxy) | 2.49 | CALCULATED | FY 2025 | net debt / EBITDA proxy |
| Cash interest paid (FY 2025) | 1842000000 | REPORTED | FY ending 2025-12-31 | us-gaap:InterestPaidNet |
| Interest cover proxy (EBITDA / cash interest) | 7.3 | CALCULATED | FY 2025 | EBITDA proxy / cash interest paid |
| Implied average cash cost of debt | 0.0451 | CALCULATED | FY 2025 | cash interest paid / average(total debt at prior FY end, total debt at FY end) |
| Liquidity (cash + undrawn committed facilities, FY end 2025-12-31) | 19638000000 | CALCULATED | as of 2025-12-31 | cash and cash equivalents + remaining capacity of committed credit facilities |
| Debt due within 12 months | 12634000000 | CALCULATED | as of 2025-12-31 | short-term borrowings + LTD maturing in the next 12 months |
| Liquidity coverage of 12-month debt | 1.55 | CALCULATED | as of 2025-12-31 | liquidity / debt due within 12 months |
| Annual cost of +100 bps on debt repricing within 12 months | 126340000 | ESTIMATED | debt as of 2025-12-31 | debt due within 12 months × 1.00% |
| Annual cost change refinancing 12-month maturities at today's market | 88288000 | ESTIMATED | maturities as of 2025-12-31; market 2026-10-06 | LTD maturing in 12 months × ((UST 5Y + Single-A OAS) − implied average cost of debt) |


Basis labels: **REPORTED** = as filed; **CALCULATED** = arithmetic on reported facts; **ESTIMATED** = calculation plus a
stated market assumption; **SCENARIO** = what-if overlay.

## Not available from public data (shown in the UI, never imputed)
Bank balances and accounts; cash by entity or currency; invoices, payments and customer/supplier ledgers; the 13-week
transaction-level forecast; intercompany optimisation; segment financials (dimensional XBRL); quarterly debt lines;
fixed/floating mix and hedge positions; covenant terms; the ME&T vs Financial Products split.

## Forecast method (public case)
Seasonal naive: each quarter's cash change equals the change reported in the same quarter one year earlier. The band is
±1.28σ of the point-in-time back-test errors × √(quarters ahead). The back-test uses only data available before each
quarter. MAPE is 25.3%, versus 33.4% for a no-change forecast.

## Recommendation rules (Meridian reference policy PC-*, illustrative, NOT Caterpillar policy)
| Rule | Test on the evidence | Result |
|---|---|---|
| PC-2.1 Refinancing readiness | debt due ≤12m / liquidity = 64% vs 25% | triggered → R01 (CFO) |
| PC-1.1 Liquidity coverage | 1.55x vs 1.25x | not triggered |
| PC-3.1 Working-capital deterioration | DSO 56.1 → 64.4 (+8.3 d) YoY | triggered → R02 (Treasurer / WC Manager) |
| PC-4.1 Shareholder returns vs FCF | $10.04B vs $10.70B TTM | not triggered |
| PC-5.1 Repricing exposure | $126.3M = 0.82% of EBITDA vs 0.50% | triggered → R03 (Treasurer) |


## Other companies (multi-company)

The same analytics apply to any SEC-reporting company added by ticker or CIK (header menu → **switch / add company**,
or `POST /v1/companies`). Rules applied to every issuer:

* Only us-gaap facts from the issuer's own 10-K/10-Q filings are used; no dimensions or company extensions.
* Broader concepts are used as fallbacks only when the narrower one is not tagged, and each carries a disclosure note
  (for example cash including restricted cash, or payables including accrued liabilities).
* Debt due within 12 months is `DebtCurrent` when filed as one line, otherwise short-term borrowings plus the current
  portion of long-term debt (or the maturity-schedule year-1 proxy).
* Debt facts are used only if dated within 31 days of the latest 10-K; otherwise the debt metrics are NOT AVAILABLE (stale).
* Captive-finance caveats appear only for issuers flagged as such (CAT, DE).
* An issuer with no us-gaap cash or revenue facts (for example an IFRS 20-F filer) is refused with the reason.
