"""MCP tool gateway: least-privilege, audited, observable access from agents to treasury tools."""

from __future__ import annotations

import hashlib
import json
import time
from typing import Any

from mcp import Client

from meridian.config import get_settings
from meridian.governance.audit import AuditLedger
from meridian.governance.policy import load_policy
from meridian.observability.metrics import TOOL_CALLS


class ToolPermissionDenied(PermissionError):
    pass


def _target():
    import os

    url = os.getenv("MERIDIAN_MCP_SERVER_URL")
    if url:
        return url
    from meridian.mcp_server.server import mcp

    return mcp


class MCPGateway:
    def __init__(self, run_id: str, ledger: AuditLedger):
        self.run_id, self.ledger = run_id, ledger
        self.policy = load_policy()
        self.settings = get_settings()
        self.calls: list[dict] = []   # provenance of every tool result this gateway returned

    async def call(self, agent: str, tool: str, **args: Any) -> Any:
        if tool not in self.policy.tool_permissions.get(agent, []):
            TOOL_CALLS.labels(agent=agent, tool=tool, status="denied").inc()
            self.ledger.record(agent, "tool_denied", {"tool": tool, "args": args}, self.run_id)
            raise ToolPermissionDenied(f"{agent} may not call {tool}")
        t0 = time.perf_counter()
        async with Client(_target()) as c:
            res = await c.call_tool(tool, args)
        content: list[Any] = res.content    # text blocks; a non-text block fails on .text exactly as before
        if res.is_error:
            TOOL_CALLS.labels(agent=agent, tool=tool, status="error").inc()
            self.ledger.record(agent, "tool_error", {"tool": tool, "args": args}, self.run_id)
            raise RuntimeError(f"{tool} failed: {content[0].text if content else ''}")
        data = getattr(res, "structured_content", None)
        if data is None:
            data = json.loads(content[0].text) if len(content) == 1 else \
                [json.loads(b.text) for b in content]
        if isinstance(data, dict) and set(data) == {"result"}:
            data = data["result"]
        TOOL_CALLS.labels(agent=agent, tool=tool, status="ok").inc()
        digest = hashlib.sha256(json.dumps(data, sort_keys=True, default=str).encode()).hexdigest()[:16]
        audit_hash = self.ledger.record(agent, "tool_call", {
            "tool": tool, "args": args, "transport": "mcp", "result_sha256": digest,
            "latency_ms": round((time.perf_counter() - t0) * 1000, 1)}, self.run_id)
        self.calls.append({"agent": agent, "tool": tool, "args": args, "result_sha256": digest,
                           "audit_hash": audit_hash[:16]})
        return data
