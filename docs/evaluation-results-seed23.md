# Meridian evaluation (seed 23)

| Metric | Value |
|---|---|
| ar_mae_days_model | 3.49 |
| ar_mae_days_customer_avg | 5.97 |
| ar_mae_days_erp | 19.75 |
| ar_interval_coverage | 0.78 |
| collections_wape_model | 0.0883 |
| collections_wape_erp | 0.6126 |
| optimizer_annualized_benefit_usd | 3835220 |
| optimizer_revolver_repay_usd | 28000000.0 |
| optimizer_residual_shortfalls | 0 |
| briefings_grounded | 3/3 |
| planner_accuracy | 1.0 |
| rag_hit_at_3 | 1.0 |
| injected_chunks_quarantined | 1 |
| audit_chain_valid | True |

| Release gate | Value | Target | Result |
|---|---|---|---|
| AR model beats customer-average baseline | 3.49 | < baseline MAE | PASS |
| Collections forecast WAPE | 0.0883 | <= 15% | PASS |
| Forecast beats ERP due-date assumption 3x | 0.0883 | 3x lower error | PASS |
| AR interval calibration | 0.78 | 0.72-0.90 (nominal 0.80) | PASS |
| Optimizer leaves no entity below buffer | 0 | 0 | PASS |
| Trapped cash respects repatriation allowances | True | True | PASS |
| Every briefing figure grounded in tool output | 3/3 | all | PASS |
| Planner accuracy (labelled set) | 1.0 | >= 0.9 | PASS |
| Policy retrieval hit@3 (labelled set) | 1.0 | >= 0.8 | PASS |
| Injected document chunk quarantined | 1 | 1 | PASS |
| Audit chain valid | True | True | PASS |
