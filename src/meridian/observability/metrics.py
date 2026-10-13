"""Prometheus metrics."""

from prometheus_client import Counter, Histogram

RUNS = Counter("meridian_runs_total", "Treasury agent runs", ["status"])
AGENT_LATENCY = Histogram("meridian_agent_latency_seconds", "Agent step latency", ["agent"])
TOOL_CALLS = Counter("meridian_tool_calls_total", "MCP tool calls", ["agent", "tool", "status"])
LLM_CALLS = Counter("meridian_llm_calls_total", "LLM calls", ["agent", "provider"])
LLM_LATENCY = Histogram("meridian_llm_latency_seconds", "LLM latency", ["agent"])
GUARDRAIL_EVENTS = Counter("meridian_guardrail_events_total", "Guardrail interventions", ["type"])
APPROVALS = Counter("meridian_approvals_total", "Approval decisions", ["decision", "role"])
