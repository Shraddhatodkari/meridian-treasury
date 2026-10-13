"""Structured JSON logs. Access lines carry a request id, route template, status and latency — never the query string,
request/response bodies, Authorization header or tokens. Any message is passed through the DLP redactor first."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import cast

from meridian.governance.dlp import redact_text


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        out = {"ts": datetime.now(UTC).isoformat(), "level": record.levelname, "logger": record.name,
               "msg": redact_text(record.getMessage())}
        for k in ("request_id", "method", "route", "status", "ms", "actor"):
            if hasattr(record, k):
                out[k] = getattr(record, k)
        if record.exc_info:
            out["error"] = redact_text(f"{cast(type, record.exc_info[0]).__name__}: {record.exc_info[1]}")[:300]
        return json.dumps(out, ensure_ascii=False)


def configure(level: str = "INFO") -> None:
    root = logging.getLogger("meridian")
    if any(isinstance(h.formatter, JsonFormatter) for h in root.handlers):
        return
    h = logging.StreamHandler()
    h.setFormatter(JsonFormatter())
    root.addHandler(h)
    root.setLevel(level)
    root.propagate = False


access = logging.getLogger("meridian.access")
