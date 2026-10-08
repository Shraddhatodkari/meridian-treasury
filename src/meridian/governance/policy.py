"""Typed governance policy + approval routing (approval matrix, segregation of duties)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

from meridian.config import get_settings
from meridian.domain.models import ProposedAction


class ApprovalRule(BaseModel):
    action: str
    approver_role: str
    policy_ref: str
    max_amount_usd: float | None = None
    when: str | None = None


class RevolverRules(BaseModel):
    prepay_min_usd: float
    prepay_multiple_usd: float
    policy_ref: str


class DLPRules(BaseModel):
    mask_patterns: dict[str, str]
    mask_fields: list[str]


class GuardrailRules(BaseModel):
    injection_patterns: list[str]
    numeric_grounding_tolerance: float = 0.02


class LLMRules(BaseModel):
    max_calls_per_run: int = 10


class LiquidityRules(BaseModel):
    management_min_liquidity_usd: float = 300_000_000
    bank_max_share: float = 0.40
    bank_min_rating: str = "A-"
    bank_low_rating_max_share: float = 0.15
    covenant_watch_headroom: float = 0.20


class DebtRules(BaseModel):
    refinancing_window_months: int = 18
    fixed_share_min: float = 0.40
    fixed_share_max: float = 0.75
    new_issue_premium_bps: float = 25


class PublicCompanyRules(BaseModel):
    min_liquidity_coverage: float = 1.25
    refinancing_trigger_share_of_liquidity: float = 0.25
    wc_days_trigger: float = 5.0
    repricing_trigger_share_of_ebitda: float = 0.005


class ExecutionRules(BaseModel):
    release_roles: list[str] = Field(default_factory=lambda: ["treasurer", "cfo"])
    transmission_channel: str = "none"


class TreasuryPolicy(BaseModel):
    version: str
    owner: str
    approval_matrix: list[ApprovalRule]
    role_hierarchy: dict[str, int]
    role_scopes: dict[str, list[str]] = Field(default_factory=dict)
    liquidity: LiquidityRules = Field(default_factory=LiquidityRules)
    debt: DebtRules = Field(default_factory=DebtRules)
    execution: ExecutionRules = Field(default_factory=ExecutionRules)
    public_company: PublicCompanyRules = Field(default_factory=PublicCompanyRules)
    segregation_of_duties: bool = True
    autonomous_execution: bool = False
    tool_permissions: dict[str, list[str]]
    revolver: RevolverRules
    dlp: DLPRules
    guardrails: GuardrailRules
    llm: LLMRules = Field(default_factory=LLMRules)


@lru_cache
def load_policy(path: Path | None = None) -> TreasuryPolicy:
    return TreasuryPolicy(**yaml.safe_load(Path(path or get_settings().policy_path).read_text(encoding="utf-8")))


def route_approval(action: ProposedAction, capital_controls: bool = False,
                   policy: TreasuryPolicy | None = None) -> tuple[str, str]:
    """Return (required approver role, policy clause) — first matching rule wins."""
    policy = policy or load_policy()
    for r in policy.approval_matrix:
        if r.action != action.type.value:
            continue
        if r.when == "capital_controls" and not capital_controls:
            continue
        if r.max_amount_usd is not None and action.amount_usd > r.max_amount_usd:
            continue
        return r.approver_role, r.policy_ref
    return "cfo", "TP-2.1"


class ApprovalDenied(PermissionError):
    pass


def check_approval(action: dict, approver_role: str, approver_user: str, proposer: str,
                   policy: TreasuryPolicy | None = None) -> None:
    policy = policy or load_policy()
    need = action["required_approver"]
    rank = policy.role_hierarchy
    if rank.get(approver_role, 0) < rank.get(need, 99) or rank.get(approver_role, 0) == 0:
        raise ApprovalDenied(f"Requires {need} authority ({action['policy_refs'][0]}): {approver_role} cannot approve "
                             f"{action['type']} of ${action['amount_usd']:,.0f}")
    scope = policy.role_scopes.get(approver_role)
    if scope is not None and action["type"] not in scope:
        raise ApprovalDenied(f"Requires {need} authority ({action['policy_refs'][0]}): {approver_role} authority is "
                             f"limited to {', '.join(scope)}")
    proposers = {proposer} if isinstance(proposer, str) else set(proposer)
    if policy.segregation_of_duties and approver_user in proposers:
        raise ApprovalDenied("Segregation of duties: the requester or submitter cannot approve the same action (TP-2.2)")
