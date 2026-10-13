"""Numeric grounding at displayed precision (v3.2): sign-, currency- and precision-aware, no blanket tolerance."""

from meridian.governance.guardrails import extract_claims, numeric_grounding


def g(text, ev):
    return numeric_grounding(text, ev)


def test_exact_at_displayed_precision():
    ev = {"debt": 12_634_000_000, "cost": 126_340_000}
    assert g("Debt due $12.63B; +100 bps costs $126.3M.", ev)["grounded"]
    assert g("Debt due $12.6B.", ev)["grounded"]                     # coarser rounding of the same fact is valid


def test_rejects_what_a_blanket_two_percent_rule_accepted():
    ev = {"wc_change": 3_700_000_000}
    r = g("Working capital rose by $3.77B.", ev)                       # 1.9% off: old 2% rule passed it
    assert not r["grounded"] and r["ungrounded"] == [3_770_000_000]


def test_sign_must_match_when_written():
    ev = {"twc": -22_040_000_000, "cash": 39_540_000_000}
    assert g("Trade working capital is -$22.04B.", ev)["grounded"]
    assert g("Trade working capital is −$22.04B.", ev)["grounded"]     # unicode minus
    assert not g("Cash is -$39.54B.", ev)["grounded"]                 # explicit wrong sign
    assert g("A $22.04B release of working capital.", ev)["grounded"]  # unsigned = magnitude statement


def test_large_and_small_amounts_and_thousands_separators():
    ev = {"rpo": 684_000_000_000, "fee": 5_000_000, "x": 250_000}
    assert g("RPO $684.00B, fee $5,000,000, small $250K.", ev)["grounded"]
    assert not g("Fee $5,000,001.", ev)["grounded"]                   # whole-dollar display must be exact


def test_missing_evidence_and_unsupported_currency():
    assert not g("Cash is $1.00B.", {})["grounded"]
    ev = {"cash": 1_000_000_000, "currency": "USD"}
    assert not g("Cash is €1.00B.", ev)["grounded"]                   # EUR claim, evidence only in USD
    assert g("Cash is €1.00B.", {"cash": 1_000_000_000, "currency": "EUR"})["grounded"]


def test_multiples_are_checked():
    ev = {"coverage": 1.5547, "policy": {"min": 1.25}}
    assert g("Covered 1.55x, above the 1.25x minimum.", ev)["grounded"]
    assert not g("Covered 1.75x.", ev)["grounded"]


def test_percent_extracted_with_precision_and_optional_check():
    c = [x for x in extract_claims("cost of debt 4.5%") if x["kind"] == "percent"][0]
    assert c["value"] == 4.5 and c["tolerance"] == 0.05
    ev = {"kd": 0.0449}
    assert numeric_grounding("cost of debt 4.5%", ev, kinds=("percent",))["grounded"]   # ratio 0.0449 = 4.49%
    assert not numeric_grounding("cost of debt 5.5%", ev, kinds=("percent",))["grounded"]


def test_result_explains_every_check():
    r = g("Cash $6.71B and debt $12.63B.", {"a": 6_713_000_000})
    assert r["claims"] == 2 and [d["grounded"] for d in r["detail"]] == [True, False]
    assert "display-precision" in r["method"]
