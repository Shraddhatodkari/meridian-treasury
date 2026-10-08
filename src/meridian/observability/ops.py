"""In-process operational telemetry for the System Operations workspace (complements Prometheus /metrics).

Per-route request counts, error counts and latency percentiles over a rolling window, plus a job log
(agent runs, data refreshes). In a multi-replica deployment Prometheus is the source of truth; this view
shows the replica that served the request and says so.
"""

from __future__ import annotations

import socket
import threading
import time
from collections import defaultdict, deque
from datetime import UTC, datetime

_LOCK = threading.Lock()
_WINDOW = 2000
_requests: deque = deque(maxlen=_WINDOW)
_jobs: deque = deque(maxlen=200)
STARTED = datetime.now(UTC)


def record_request(route: str, method: str, status: int, ms: float) -> None:
    with _LOCK:
        _requests.append((time.time(), route, method, status, ms))


def record_job(kind: str, status: str, duration_ms: float, detail: str = "", ref: str | None = None) -> None:
    with _LOCK:
        _jobs.appendleft({"at": datetime.now(UTC).isoformat(), "kind": kind, "status": status,
                          "duration_ms": round(duration_ms, 1), "detail": detail, "ref": ref})


def _pct(xs: list[float], q: float) -> float | None:
    if not xs:
        return None
    xs = sorted(xs)
    return round(xs[min(len(xs) - 1, int(q * (len(xs) - 1)))], 1)


def snapshot() -> dict:
    with _LOCK:
        reqs = list(_requests)
        jobs = list(_jobs)
    by: dict[tuple[str, str], list] = defaultdict(list)
    for _, route, method, status, ms in reqs:
        by[(method, route)].append((status, ms))
    routes = []
    for (method, route), rows in by.items():
        lat = [m for _, m in rows]
        routes.append({"route": route, "method": method, "requests": len(rows),
                       "errors_5xx": sum(1 for s, _ in rows if s >= 500), "client_4xx": sum(1 for s, _ in rows if 400 <= s < 500),
                       "p50_ms": _pct(lat, 0.5), "p95_ms": _pct(lat, 0.95)})
    routes.sort(key=lambda r: -r["requests"])
    lat_all = [r[4] for r in reqs]
    return {"replica": socket.gethostname(), "started_at": STARTED.isoformat(),
            "uptime_s": round((datetime.now(UTC) - STARTED).total_seconds()), "window_requests": len(reqs),
            "errors_5xx": sum(1 for r in reqs if r[3] >= 500), "denied_403": sum(1 for r in reqs if r[3] == 403),
            "unauthenticated_401": sum(1 for r in reqs if r[3] == 401),
            "p50_ms": _pct(lat_all, 0.5), "p95_ms": _pct(lat_all, 0.95), "routes": routes[:25], "jobs": jobs[:50]}
