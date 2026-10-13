# Demo script and interview guide (Meridian 3.5)

## One-line pitch
"Meridian is a treasury and working-capital intelligence platform. I used it to analyse 390 large US companies' SEC
filings: $152B of cash is tied up in receivables and inventory beyond peer medians. Every number traces to a filing,
recommendations go through governed approvals, and the forecasting method is validated on a million real invoices."

## 3-minute demo (in this order)

| Time | Screen | What to say |
|---|---|---|
| 0:00 | **Value at Stake** | "The business problem: cash trapped in working capital. Across 390 companies, $152B is tied up beyond peer medians — $355B to top quartile. Supplier terms are shown separately because that cash comes from suppliers." |
| 0:40 | Type **CAT** in the lookup | "Caterpillar: inventory 148 days vs 98 for industrial-machinery peers — about $6B. Each input links to the SEC filing (click an accession number)." |
| 1:05 | **Segment review** table | "Amazon and Microsoft are excluded from the headline: they are most of their SEC peer group, so the comparison isn't fair. A consultant would analyse them at segment level — the tool says so instead of inflating the number." |
| 1:25 | **Working Capital** (Caterpillar) | "Same company, quarter by quarter: the year-on-year bridge and the peer gap side by side." |
| 1:45 | **AI Decision Center → Run governed review** | "Eleven agents work only through read-only tools; every figure in the briefing is checked against computed values." |
| 2:10 | **Recommendations → Approval Center** | "A recommendation is a decision mandate. The treasurer cannot approve a CFO-level action, the requester cannot approve their own, and nothing is ever transmitted to a bank." |
| 2:35 | **Audit & Governance** | "Every decision and denial is hash-chained with the policy version and evidence." |
| 2:50 | **Download board report** | "And the output a CFO or partner actually reads." |

## How to frame it for each firm

* **McKinsey / BCG / Bain** — "An outside-in working-capital diagnostic, the kind used before a cash-release engagement: peer
  benchmarks, value at stake in conservative and stretch cases, and the hypotheses to test with management."
* **Deloitte (Finance / Treasury / Risk advisory)** — "Treasury transformation with controls built in: approval matrix,
  segregation of duties, four-eyes on privileged roles, tamper-evident audit, data lineage to source filings."
* **Mastercard (B2B payments, commercial solutions)** — "$53B of supplier-terms opportunity can only be captured fairly
  with supplier protection — supply-chain finance or commercial-card programmes. Meridian sizes that pool by sector and
  keeps it separate from the headline."

## Questions you will get — honest answers

| Question | Answer |
|---|---|
| Is it used by a real company? | "Not yet. It runs on real public data (SEC filings, market rates) and real retail transactions. A pilot needs a company's ERP and bank exports; the importers exist and are tested on files." |
| How accurate is the forecast? | "On real weekly data, 24.7 % better than naive, but not better than a seasonal-naive baseline, and its 80 % band was too narrow (70 % coverage). I report that rather than hide it." |
| Why trust the $152B? | "Every input is a filed value with an accession number; formulas are recomputed in tests; balances and flows share the same fiscal-year end; outliers and giants are excluded with reasons." |
| What would you do next? | "Segment-level benchmarks for diversified companies, a pilot on one company's ledgers, enterprise SSO, and an independent security review." |
| Is it secure? | "Role-based access on every endpoint (tested), four-eyes on privileged roles, lockout without account enumeration, compare-and-swap on approvals, tamper-evident audit. It has not had an independent penetration test." |

## Never say
"Production deployment at a client", "certified", "pen-tested", "real-time bank integration", or that a named company
has an inefficiency — they are hypotheses from public filings.
