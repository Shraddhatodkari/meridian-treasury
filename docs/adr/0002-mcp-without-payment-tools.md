# ADR-0002: MCP tool layer with no money-movement tools

**Status:** Accepted

**Context.** Agent frameworks and external assistants (e.g. Claude Desktop) need treasury data. A tool that
moves money would make prompt injection or a bad plan financially dangerous.

**Decision.** Expose read-only, analytical tools through one MCP server. Enforce per-agent allowlists in a
gateway that audits every call. Money moves only through the human approval workflow, which outputs a
draft payment file.

**Consequences.** One security review covers all agent clients. The "excessive agency" risk is removed by design.
