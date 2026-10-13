import numpy as np

from meridian.analytics.forecast import _inverse_cdf, _schedule
from meridian.analytics.scenarios import PRESET_SCENARIOS, Scenario, apply_events
from meridian.data.generator import generate


def test_generator_is_deterministic():
    a, b = generate(seed=3), generate(seed=3)
    assert [r.amount for r in a.receivables[:200]] == [r.amount for r in b.receivables[:200]]


def test_ground_truth_never_serialised():
    r = next(r for r in generate(seed=3).receivables if r.settled_date is None)
    assert r.actual_settle_date is not None and "actual_settle_date" not in r.model_dump()


def test_model_beats_baselines(engine):
    card = engine.model.card
    assert card["mae_days_model_p50"] < card["mae_days_customer_average"] < card["mae_days_erp_due_date_assumption"]
    assert 0.72 <= card["p10_p90_coverage_conformal"] <= 0.9  # calibrated near the nominal 80%


def test_inverse_cdf_hits_quantiles():
    p10, p50, p90 = np.array([0.0]), np.array([10.0]), np.array([30.0])
    assert _inverse_cdf(np.array([0.1]), p10, p50, p90)[0] == 0.0
    assert _inverse_cdf(np.array([0.5]), p10, p50, p90)[0] == 10.0
    assert _inverse_cdf(np.array([0.9]), p10, p50, p90)[0] == 30.0


def test_schedule_cadences():
    from datetime import date

    assert len(_schedule(date(2026, 10, 2), "BIWEEKLY", date(2026, 9, 28), date(2026, 12, 28))) == 7
    assert len(_schedule(date(2026, 10, 15), "QUARTERLY", date(2026, 9, 28), date(2026, 12, 28))) == 1


def test_forecast_backtest_beats_erp(engine):
    bt = engine.backtest()
    assert bt["wape_model"] < 0.15 and bt["wape_model"] < bt["wape_erp_due_date"] / 3


def test_stress_reduces_headroom(engine):
    base = engine.forecast_summary()["min_covenant_headroom_p10_usd"]
    stress = engine.forecast_summary(PRESET_SCENARIOS["global_stress"])["min_covenant_headroom_p10_usd"]
    assert stress < base


def test_hazard_events_translate_to_country_shocks(engine):
    s = apply_events(Scenario(event_ids=["EV-CN-TC-2026-31"]), engine.g.risk_events)
    assert s.country_delay_days["CN"] == 10 and s.billing_change_pct["CN"] == -0.15
