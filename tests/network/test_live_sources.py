"""LIVE network tests against the real public sources. Opt-in: set MERIDIAN_NETWORK_TESTS=1.
They are kept out of deterministic CI because they depend on third-party availability."""

import os

import pytest

pytestmark = pytest.mark.skipif(os.environ.get("MERIDIAN_NETWORK_TESTS") != "1",
                                reason="live network tests are opt-in (MERIDIAN_NETWORK_TESTS=1)")
UA = os.environ.get("MERIDIAN_SEC_USER_AGENT", "Meridian-Treasury-Platform/3.2 (ops@meridian-treasury.invalid)")


def test_sec_ticker_resolution_and_companyfacts_match_bundled_snapshot():
    from meridian.public_company import analytics as A
    from meridian.public_company import sec
    assert sec.resolve("CAT", UA)["cik"] == 18230
    facts = sec.fetch_companyfacts(18230, UA)
    usd = facts["facts"]["us-gaap"]["CashAndCashEquivalentsAtCarryingValue"]["units"]["USD"]
    pc = A.get_public_company(18230)
    end = pc.metrics["cash"]["period"].split("as of ")[-1]
    live = [u["val"] for u in usd if u["end"] == end and u["form"] in ("10-Q", "10-K")]
    assert live and pc.metrics["cash"]["value"] in live              # bundled REPORTED value equals the live filing


def test_nyfed_sofr_and_effr():
    from meridian.market import rates
    from meridian.market.connectors import fetch_sofr
    d, src = fetch_sofr()
    assert 0 < d["sofr_pct"] < 20 and src.as_of
    e, _ = rates.fetch_effr()
    assert 0 < e["effr_pct"] < 20


def test_us_treasury_curve():
    from meridian.market import rates
    c, src = rates.fetch_ust_curve()
    assert any(x["tenor"] == "5 Yr" for x in c["curve"]) and src.as_of


def test_fred_credit_spread_csv():
    from meridian.market import rates
    s, src = rates.fetch_credit_spread("BAMLC0A4CBBB")
    assert 0 < s["latest"]["value"] < 10 and src.as_of


def test_ecb_fx():
    from meridian.market.connectors import fetch_ecb_fx
    fx, src = fetch_ecb_fx({"USD", "GBP"})
    assert fx and src.as_of
