"""Structured access logs (v3.2): request id round-trip, no tokens or query strings in logs."""

import json
import logging

from fastapi.testclient import TestClient

from meridian.api.main import app
from meridian.observability.logs import JsonFormatter
from meridian.security.auth import DEMO_PASSWORD


class _Collect(logging.Handler):
    def __init__(self):
        super().__init__()
        self.records = []

    def emit(self, record):
        self.records.append(record)


def test_request_id_and_log_hygiene():
    h = _Collect()
    logging.getLogger("meridian.access").addHandler(h)
    with TestClient(app) as c:
        tok = c.post("/v1/auth/login", json={"username": "ana", "password": DEMO_PASSWORD}).json()["access_token"]
        r = c.get("/v1/cases?secret=hunter2", headers={"Authorization": f"Bearer {tok}", "X-Request-ID": "req-12345678"})
        assert r.status_code == 200 and r.headers["X-Request-ID"] == "req-12345678"
        bad = c.get("/health", headers={"X-Request-ID": "<script>"})
        assert bad.headers["X-Request-ID"] != "<script>"               # untrusted ids are replaced
    logging.getLogger("meridian.access").removeHandler(h)
    lines = [JsonFormatter().format(rec) for rec in h.records]
    assert lines, "access log emitted"
    rec = next(json.loads(x) for x in lines if '"/v1/cases"' in x)
    assert rec["request_id"] == "req-12345678" and rec["route"] == "/v1/cases" and rec["status"] == 200
    blob = "\n".join(lines)
    assert tok not in blob and "hunter2" not in blob and "Bearer" not in blob


def test_formatter_redacts_messages():
    r = logging.LogRecord("meridian", logging.INFO, __file__, 1, "pay DE89370400440532013000 now", None, None)
    assert "[REDACTED:IBAN]" in json.loads(JsonFormatter().format(r))["msg"]
