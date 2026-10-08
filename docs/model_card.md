# Model card: `ar-delay-hgb-q-1.0.0`

| Field | Value |
|---|---|
| Task | Predict days late (settlement − due date) per open receivable, as P10/P50/P90 |
| Algorithm | scikit-learn HistGradientBoostingRegressor, quantile loss; conformalised quantile regression for calibrated intervals |
| Features (point-in-time) | Customer historical and 90-day mean lateness, invoice count, dispute rate, trend, log amount (USD), terms, disputed, paperless, quarter-end issuance, rating, segment, country |
| Leakage control | Customer statistics use only invoices settled before the invoice's issue date |
| Validation | Train on invoices issued > 300 days before as-of; calibrate on the first half of the next window; test on the second half |
| Results (seed 7) | MAE 3.65 days vs 5.64 (customer average) vs 21.93 (ERP due-date); P10–P90 coverage 0.81 after conformal (0.67 raw) |
| Intended use | Collections timing in the cash forecast; collections prioritisation |
| Not for | Credit limit decisions or any customer-facing decision |
| Limitations | Synthetic behaviour; must be re-trained and validated on client history; drift monitored by weekly back-test |
