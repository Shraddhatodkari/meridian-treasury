"""Scenario / stress-test specification and hazard-event translation."""

from __future__ import annotations

from pydantic import BaseModel, Field

from meridian.domain.models import RiskEvent

# How a hazard alert level translates into operating shocks for counterparties in that country.
# Calibrate with the client's own experience (e.g. past cyclone seasons); these are planning defaults.
SEVERITY_IMPACT = {
    "GREEN": {"collection_delay_days": 2, "billing_change_pct": -0.02},
    "ORANGE": {"collection_delay_days": 10, "billing_change_pct": -0.15},
    "RED": {"collection_delay_days": 20, "billing_change_pct": -0.35},
}


class Scenario(BaseModel):
    name: str = "Base case"
    collection_delay_pct: float = Field(default=0.0, description="Global % increase in days-to-pay (0.3 = +30%)")
    country_delay_days: dict[str, float] = Field(default_factory=dict, description="Extra days late by country")
    billing_change_pct: dict[str, float] = Field(default_factory=dict, description="Change in new billing; key '*' = all")
    rate_shock_bps: float = 0.0
    fx_shock_pct: dict[str, float] = Field(default_factory=dict, description="Currency move vs USD (-0.1 = -10%)")
    event_ids: list[str] = Field(default_factory=list)

    def is_base(self) -> bool:
        return not (self.collection_delay_pct or self.country_delay_days or self.billing_change_pct
                    or self.rate_shock_bps or self.fx_shock_pct or self.event_ids)


def apply_events(s: Scenario, events: list[RiskEvent]) -> Scenario:
    """Fold selected hazard events into country-level shocks (disaster-risk overlay)."""
    s = s.model_copy(deep=True)
    for ev in events:
        if ev.event_id not in s.event_ids:
            continue
        imp = SEVERITY_IMPACT.get(ev.severity, SEVERITY_IMPACT["GREEN"])
        s.country_delay_days[ev.country] = s.country_delay_days.get(ev.country, 0) + imp["collection_delay_days"]
        s.billing_change_pct[ev.country] = s.billing_change_pct.get(ev.country, 0) + imp["billing_change_pct"]
    return s


PRESET_SCENARIOS = {
    "base": Scenario(),
    "eu_slowdown": Scenario(name="EU customers pay 15 days later",
                            country_delay_days={"DE": 15, "FR": 15, "NL": 15, "PL": 15}),
    "global_stress": Scenario(name="Global stress: +30% days-to-pay, -10% billing, +50bps, EUR -8%",
                              collection_delay_pct=0.30, billing_change_pct={"*": -0.10}, rate_shock_bps=50,
                              fx_shock_pct={"EUR": -0.08}),
    "asia_hazards": Scenario(name="Active hazard events (CN cyclone, IN floods)",
                             event_ids=["EV-CN-TC-2026-31", "EV-IN-FL-2026-12"]),
}
