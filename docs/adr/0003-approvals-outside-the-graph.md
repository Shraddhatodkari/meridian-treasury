# ADR-0003: Approval workflow outside the agent graph

**Status:** Accepted

**Context.** A run proposes many actions that need different approvers (treasurer, CFO), on different days,
with segregation of duties.

**Decision.** The LangGraph run ends at AWAITING_APPROVALS. Proposed actions persist in MongoDB. Each approval
is a separate, authenticated API call that checks role hierarchy and SoD, writes to the audit ledger, and
generates the draft payment file.

**Consequences.** No long-lived paused graph threads. Approvals survive restarts and scale across replicas.
