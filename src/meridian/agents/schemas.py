"""Structured outputs for every agent (flat schemas => clean Claude tool `input_schema`s)."""

from __future__ import annotations

from pydantic import BaseModel, Field


class Plan(BaseModel):
    intent: str = Field(description="DAILY_RUN | SCENARIO | INSIGHT | POLICY_QUESTION")
    insight_topic: str | None = Field(default=None, description="for INSIGHT: trapped_cash | liquidity_risk | collections | "
                                      "releasable_liquidity | repay_vs_preserve | shocks | wc_actions | cfo_approvals | "
                                      "liquidity_change | dso_drivers | excess_cash | covenants | refinancing | "
                                      "rate_exposure | liquidity_outlook")
    scenario_name: str = "Base case"
    collection_delay_pct: float = 0.0
    country_delay_days: dict[str, float] = Field(default_factory=dict)
    billing_change_pct_all: float = 0.0
    rate_shock_bps: float = 0.0
    fx_shock_pct: dict[str, float] = Field(default_factory=dict)
    include_hazard_events: bool = False
    policy_query: str | None = None


class AgentFinding(BaseModel):
    agent: str
    headline: str
    key_points: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    recommendations: list[str] = Field(default_factory=list)
    policy_refs: list[str] = Field(default_factory=list)
    confidence: float = Field(0.8, ge=0, le=1, description="0-1, how strongly the evidence supports the finding")
    limitations: list[str] = Field(default_factory=list)


class CFOBriefing(BaseModel):
    title: str
    executive_summary: str
    key_numbers: list[str]
    decisions_required: list[str]
    risks: list[str]
    citations: list[str] = Field(default_factory=list)
