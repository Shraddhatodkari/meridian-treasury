# ADR-0006: One orchestration framework (LangGraph), no CrewAI/AutoGen in this service

**Status:** Accepted

**Context.** Multi-framework stacks add dependency conflicts and governance surface area. This workflow
is a fixed, auditable sequence of bounded agents (v2: Planner → Forecast → Liquidity → Risk → Working capital →
Policy → Briefing → Guardrail), which LangGraph models directly.

**Decision.** Use LangGraph alone, with the native Anthropic SDK for structured outputs and prompt caching.
(Multi-framework agent runtimes were considered and rejected: one deterministic orchestrator is easier to audit.)

**Consequences.** A smaller dependency tree and simpler validation. New specialist agents are added as graph nodes.
