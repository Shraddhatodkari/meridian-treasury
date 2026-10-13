"""Public-source HTTP layer (v3.2): bounded retries, Retry-After, per-host throttle, TTL cache — deterministic, no network."""

import httpx
import pytest

from meridian.market import http


@pytest.fixture(autouse=True)
def fake(monkeypatch):
    slept = []
    monkeypatch.setattr(http, "_sleep", lambda s: slept.append(s))
    http.clear_cache()
    yield slept
    monkeypatch.setattr(http, "_TRANSPORT", None)
    http.clear_cache()


def transport(monkeypatch, responses):
    calls = []

    def handler(req):
        calls.append(req)
        r = responses[min(len(calls) - 1, len(responses) - 1)]
        if isinstance(r, Exception):
            raise r
        return r
    monkeypatch.setattr(http, "_TRANSPORT", httpx.MockTransport(handler))
    return calls


def test_retries_transient_errors_then_succeeds(monkeypatch, fake):
    calls = transport(monkeypatch, [httpx.Response(503), httpx.ConnectError("reset"), httpx.Response(200, json={"ok": 1})])
    assert http.get("https://example.org/x", retries=3).json() == {"ok": 1}
    assert len(calls) == 3 and len(fake) == 2                         # two backoffs, no real sleeping


def test_honours_retry_after_with_a_cap(monkeypatch, fake):
    transport(monkeypatch, [httpx.Response(429, headers={"Retry-After": "7"}), httpx.Response(429, headers={"Retry-After": "999"}),
                            httpx.Response(200, text="ok")])
    http.get("https://example.org/x", retries=3)
    assert fake[:2] == [7.0, http.MAX_RETRY_AFTER_S]


def test_gives_up_after_bounded_attempts(monkeypatch):
    calls = transport(monkeypatch, [httpx.ConnectError("down")])
    with pytest.raises(http.SourceUnavailable, match="after 3 attempts"):
        http.get("https://example.org/x", retries=2)
    assert len(calls) == 3


def test_non_retryable_status_fails_fast(monkeypatch):
    calls = transport(monkeypatch, [httpx.Response(404)])
    with pytest.raises(httpx.HTTPStatusError):
        http.get("https://example.org/missing", retries=3)
    assert len(calls) == 1


def test_sec_requests_are_throttled_and_carry_the_user_agent(monkeypatch, fake):
    calls = transport(monkeypatch, [httpx.Response(200, json={})])
    for _ in range(3):
        http.get("https://data.sec.gov/api/xbrl/companyfacts/CIK0000018230.json", headers={"User-Agent": "Org ops@org.invalid"})
    assert all(c.headers["User-Agent"] == "Org ops@org.invalid" for c in calls)
    assert len(fake) >= 2 and all(0 < s <= http.MIN_INTERVAL_S["data.sec.gov"] for s in fake)


def test_ttl_cache_avoids_refetching_reference_files(monkeypatch):
    calls = transport(monkeypatch, [httpx.Response(200, json={"0": {"cik_str": 1, "ticker": "X", "title": "X"}})])
    for _ in range(3):
        http.get("https://www.sec.gov/files/company_tickers.json", cache_ttl_s=60)
    assert len(calls) == 1


def test_sec_resolve_uses_the_resilient_layer(monkeypatch):
    from meridian.public_company import sec
    transport(monkeypatch, [httpx.Response(200, json={"0": {"cik_str": 18230, "ticker": "CAT", "title": "CATERPILLAR INC"}})])
    assert sec.resolve("cat")["cik"] == 18230
    with pytest.raises(LookupError):
        sec.resolve("ZZZZ")
