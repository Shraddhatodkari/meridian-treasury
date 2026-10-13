# Governance and control catalogue

Principle: **the AI analyses and recommends; authorised humans approve; no system executes payments.**

| # | Control | Type | Implementation | Test |
|---|---|---|---|---|
| C1 | Approval matrix by action type, amount and capital-control status (TP-2.1) | Preventive | `governance/policy.py::route_approval`, `config/treasury_policy.yaml` | `test_approval_matrix` |
| C2 | Role hierarchy; admins cannot approve money | Preventive | `check_approval` | `test_role_hierarchy_and_segregation_of_duties` |
| C3 | Segregation of duties: requester ≠ approver (TP-2.2) | Preventive | `check_approval` | same |
| C4 | No autonomous execution: approvals produce a *draft* ISO 20022 pain.001 only (TP-7.1) | Preventive | `execution/payments.py`; no payment tool on the MCP server | `test_mcp_tools_are_read_only_and_no_payment_tool` |
| C5 | Least-privilege tool access per agent | Preventive | `orchestration/gateway.py` + `tool_permissions` | `test_gateway_least_privilege` |
| C6 | DLP: IBANs masked before any LLM call or for non-treasury roles | Preventive | `guardrails.mask` | `test_dlp_masks_bank_identifiers`, `test_accounts_masked_for_analyst` |
| C7 | Prompt-injection quarantine at document ingestion | Preventive | `nlp/rag.py::index_documents` | `test_injected_supplier_note_is_quarantined…` |
| C8 | Numeric grounding of every $ figure in the CFO briefing (2 % tolerance) | Detective/corrective | `guardrails.numeric_grounding` + graph retry/fallback | `test_numeric_grounding_flags_invented_figures` |
| C9 | Policy constraints inside the optimiser (buffers TP-3.1, no yield arbitrage TP-3.2, repatriation CR-*, RCF-8.1 rounding) | Preventive | `analytics/optimizer.py`, `engine.optimize` | `test_optimizer_respects_policy` |
| C10 | Hash-chained, tamper-evident audit log of tool calls, agent outputs, approvals and denials | Detective | `governance/audit.py` | `test_audit_chain_detects_tampering` |
| C11 | Model validation: time-based hold-out, baselines, conformal interval calibration | Detective | `ml/ar_model.py` model card | `test_model_beats_baselines` |
| C12 | Release gates in CI (accuracy, grounding, policy compliance, retrieval, audit) | Preventive | `evaluation/benchmark.py` | CI |
| C13 | LLM call budget per run | Preventive | `llm/claude.py` | `test_claude_path_with_fake_client` |
| C14 | Change control on policy and documents | Preventive | `.github/CODEOWNERS` | — |
| C15 | Authentication: PBKDF2 passwords, signed JWT sessions, lockout after 5 failures, uniform failure message | Preventive | `security/auth.py` | `test_login_issues_signed…`, `test_account_lockout…`, `test_tampered_or_foreign_token_rejected` |
| C16 | Role-based permissions (analyst, treasurer, cfo, auditor read-only, admin without money authority) | Preventive | `security/auth.py::PERMISSIONS`, `api/main.py::require` | `test_role_permissions` (9 cases) |
| C17 | Rejections need written rationale and the same authority as approvals; decided actions are immutable | Preventive | `orchestration/service.py::decide` | `test_rejection_requires_written_rationale`, `test_analyst_cannot_reject_above_authority` |
| C18 | Demo accounts can never authenticate outside demo mode | Preventive | `authenticate` | `test_demo_accounts_cannot_sign_in_outside_demo_mode` |
| C19 | Governed ingestion: schema, IBAN mod-97, referential integrity, business rules, camt.053 balance match; atomic load or reject; every load audited | Preventive | `ingest/pipeline.py`, `cli.py ingest` | `test_bad_iban_blocks_the_whole_load`, `test_missing_bank_statement_and_referential_errors…` |
| C20 | Data classification and provenance for every input (SYNTHETIC / CLIENT_DATA / PUBLIC_MARKET_DATA / REFERENCE, as-of, freshness, validation, checksum) | Detective | `provenance.py`, `engine.provenance` | `test_every_input_has_provenance…`, `test_freshness_states` |
| C21 | Production readiness gate: refuses SYNTHETIC data, mock LLM, missing MongoDB, missing signing key, demo keys | Preventive | `readiness.py`, `store/factory.py` | `test_production_readiness_refuses_demo_shortcuts` |
| C22 | Agent registry: declared responsibility, schemas, allowlisted read-only tools, policy constraints, validation, confidence method; no agent moves money | Preventive | `agents/registry.py` | `test_every_allowlisted_tool_exists_and_is_read_only` |
| C23 | Evidence chain on every proposed action (data → analysis → agent → policy → recommendation → approval → audit) | Detective | `orchestration/graph.py` | `test_recommendations_are_not_transactions` |
| C24 | No payment-execution endpoint exists | Preventive | API surface | `test_no_endpoint_executes_payments` |
| C25 | Every CFO answer carries confidence with reasons, evidence and limitations | Detective | `analytics/insights.py` | `test_every_cfo_answer_carries_evidence…` |

## Framework alignment (indicative, not legal advice)

- **SOX / ITGC-style controls.** C1–C4, C10 and C14 mirror segregation-of-duties and change-management expectations
  for financial processes.
- **Model risk management (e.g. SR 11-7 principles).** Model card, conceptual soundness (facts from code), back-testing and
  ongoing monitoring (C11, C12).
- **NIST AI RMF.** Govern (policy-as-code ownership) · Map (documented intended use) · Measure (eval harness) · Manage (gates, fallback).
- **OWASP Top 10 for LLM applications.** Prompt injection (C7), sensitive information disclosure (C6), excessive agency (C4, C5).


## v3 controls

| Control | Where enforced | Test |
|---|---|---|
| Submitter and requester can never approve (TP-2.2) | `orchestration/service.py::decide` | `test_submitter_and_requester_cannot_approve` |
| Role scopes (WC Manager: collections + early-pay; Analyst: collections) | `governance/policy.py::check_approval` | `test_wc_manager_scope_limits_authority` |
| Prepare execution: 6 validation checks before READY | `service.prepare_execution` | `test_full_lifecycle_…` |
| Release: READY + release role + dual control + configured channel (none → refused, audited) | `service.release` | `test_full_lifecycle_…`, `test_no_endpoint_executes_payments` |
| Every authorization denial audited | `api/main.py::require` | `test_authorization_denials_are_audited` |
| Token revocation on logout | `security/auth.py::revoke` | `test_logout_revokes_token` |
| Agent failure → degraded run, evidence excluded | `orchestration/graph.py::guarded` | `test_agent_failure_degrades_run_without_inventing_evidence` |
| Recommendation impact never exceeds evidence | `decision/recommendations.py` | `test_collections_impact_is_not_overclaimed` |
| Citation verification + relevance floor | `nlp/rag.py` | `test_citation_verifier_flags_unsupported_clauses`, `test_off_topic_query_is_flagged_irrelevant` |
