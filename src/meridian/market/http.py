"""One resilient HTTP GET for every public data source (SEC EDGAR, NY Fed, U.S. Treasury, FRED, ECB).

* bounded timeout on every request;
* bounded retries with exponential backoff for transient failures (connection errors, timeouts, HTTP 429/500/502/
  503/504), honouring ``Retry-After`` (capped) when the server sends it;
* per-host throttling — SEC EDGAR's fair-access policy allows at most 10 requests per second per client;
* a small in-process TTL cache for slow-changing reference files (e.g. SEC's ticker list);
* errors are raised with the source, URL and attempt count so the caller can show a useful message.

Responses are never invented: when every attempt fails the error propagates and the caller keeps the dated
snapshot, which the UI labels as a snapshot — not as a live response.
"""

from __future__ import annotations

import threading
import time
from urllib.parse import urlparse

import httpx

RETRY_STATUS = {429, 500, 502, 503, 504}
MIN_INTERVAL_S = {"data.sec.gov": 0.12, "www.sec.gov": 0.12}   # ≤ 10 req/s per SEC fair-access policy
MAX_RETRY_AFTER_S = 30.0

_TRANSPORT: httpx.BaseTransport | None = None      # tests inject httpx.MockTransport here
_sleep = time.sleep                                 # tests replace to avoid real waiting
_last_call: dict[str, float] = {}
_cache: dict[str, tuple[float, httpx.Response]] = {}
_lock = threading.Lock()


class SourceUnavailable(httpx.HTTPError):
    """All attempts to reach a public source failed."""


def _throttle(host: str) -> None:
    gap = MIN_INTERVAL_S.get(host)
    if not gap:
        return
    with _lock:
        wait = _last_call.get(host, 0.0) + gap - time.monotonic()
        if wait > 0:
            _sleep(wait)
        _last_call[host] = time.monotonic()


def _retry_after(r: httpx.Response, default: float) -> float:
    try:
        return min(float(r.headers.get("Retry-After", default)), MAX_RETRY_AFTER_S)
    except ValueError:
        return default


def get(url: str, *, headers: dict | None = None, timeout: float = 30.0, retries: int = 3, backoff: float = 0.5,
        cache_ttl_s: float = 0.0, follow_redirects: bool = True) -> httpx.Response:
    """GET with timeout, bounded retries/backoff, Retry-After, per-host throttle and optional TTL cache.
    Raises httpx.HTTPStatusError for non-retryable statuses, SourceUnavailable after the last failed attempt."""
    if cache_ttl_s:
        hit = _cache.get(url)
        if hit and time.monotonic() - hit[0] < cache_ttl_s:
            return hit[1]
    host = urlparse(url).hostname or ""
    last: Exception | None = None
    for attempt in range(retries + 1):
        _throttle(host)
        try:
            with httpx.Client(timeout=timeout, headers=headers or {}, transport=_TRANSPORT,
                              follow_redirects=follow_redirects) as c:
                r = c.get(url)
        except (httpx.TimeoutException, httpx.TransportError) as e:
            last = e
            if attempt < retries:
                _sleep(backoff * 2 ** attempt)
            continue
        if r.status_code in RETRY_STATUS and attempt < retries:
            last = httpx.HTTPStatusError(f"HTTP {r.status_code}", request=r.request, response=r)
            _sleep(_retry_after(r, backoff * 2 ** attempt))
            continue
        r.raise_for_status()
        if cache_ttl_s:
            _cache[url] = (time.monotonic(), r)
        return r
    raise SourceUnavailable(f"{host}: unavailable after {retries + 1} attempts ({type(last).__name__}: {last})")


def clear_cache() -> None:
    _cache.clear()
    _last_call.clear()
