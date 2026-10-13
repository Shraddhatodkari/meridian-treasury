"""Treasury domain model."""

from __future__ import annotations

from datetime import date
from enum import Enum

from pydantic import BaseModel, Field


class Organization(BaseModel):
    org_id: str
    name: str
    classification: str                # SYNTHETIC | CLIENT_DATA
    reporting_currency: str = "USD"
    fiscal_year_end: str = "12-31"
    industry: str = ""


class BusinessUnit(BaseModel):
    bu_id: str
    name: str
    segments: list[str] = Field(default_factory=list)   # customer segments served


class Bank(BaseModel):
    bank_id: str
    name: str
    country: str
    credit_rating: str                 # counterparty rating used for TP-5.1 exposure limits


class Entity(BaseModel):
    entity_id: str
    name: str
    country: str
    currency: str
    role: str  # HOLDCO | TREASURY_CENTER | OPCO
    capital_controls: bool = False
    repatriation_allowance_usd: float = 0.0   # max cash that may leave in the horizon (if controlled)
    upstream_cost_bps: float = 0.0            # withholding tax / IOF-style friction on upstreaming cash
    min_buffer_local: float                   # operating cash buffer required by policy
    deposit_yield: float                      # annual yield on idle local cash
    overdraft_rate: float                     # annual cost of local overdraft if short


class BankAccount(BaseModel):
    account_id: str
    entity_id: str
    bank: str
    currency: str
    iban: str
    balance: float


class Customer(BaseModel):
    customer_id: str
    name: str
    country: str
    segment: str
    credit_rating: str


class Supplier(BaseModel):
    supplier_id: str
    name: str
    country: str
    critical: bool = False


class Receivable(BaseModel):
    invoice_id: str
    entity_id: str
    customer_id: str
    currency: str
    amount: float
    issue_date: date
    due_date: date
    terms_days: int
    disputed: bool = False
    paperless: bool = True
    settled_date: date | None = None
    # Ground truth for open invoices (simulation only) — never exposed to agents or models.
    actual_settle_date: date | None = Field(default=None, exclude=True)


class Payable(BaseModel):
    bill_id: str
    entity_id: str
    supplier_id: str
    currency: str
    amount: float
    issue_date: date
    due_date: date
    discount_pct: float = 0.0
    discount_days: int = 0
    paid_date: date | None = None


class RecurringFlow(BaseModel):
    entity_id: str
    category: str      # PAYROLL | RENT | TAX | OPEX
    currency: str
    amount: float      # negative = outflow
    cadence: str       # WEEKLY | BIWEEKLY | MONTHLY | QUARTERLY
    anchor: date


class BankTransaction(BaseModel):
    """A booked bank-statement entry (camt.053 Ntry / BAI2 16-record)."""
    txn_id: str
    account_id: str
    entity_id: str
    booking_date: date
    amount: float                      # signed, account currency (+ credit / - debit)
    currency: str
    category: str                      # CUSTOMER_RECEIPT | SUPPLIER_PAYMENT | PAYROLL | TAX | RENT | OPEX | INTEREST | INTERCOMPANY
    counterparty_id: str | None = None
    reference: str | None = None       # invoice / bill id or remittance reference


class DebtFacility(BaseModel):
    facility_id: str
    name: str
    type: str                          # RCF | TERM_LOAN | SENIOR_NOTES
    borrower_entity: str
    currency: str = "USD"
    limit_usd: float
    drawn_usd: float
    rate_type: str                     # FLOATING | FIXED
    margin_bps: float | None = None    # FLOATING: all-in = SOFR + margin
    fixed_rate: float | None = None    # FIXED coupon
    rate: float = 0.0                  # all-in annual rate actually applied (set from market data for FLOATING)
    rate_source: str = "facility terms"
    maturity_date: date
    prepayable: bool = False
    prepay_min_usd: float = 0.0
    prepay_multiple_usd: float = 1.0
    amortization_pct_per_year: float = 0.0
    clause_ref: str = ""


class Covenant(BaseModel):
    covenant_id: str
    facility_id: str
    name: str
    metric: str                        # min_liquidity | max_net_leverage | min_interest_cover
    threshold: float
    direction: str                     # ">=" | "<="
    clause_ref: str
    test_frequency: str = "QUARTERLY"


class Facility(BaseModel):
    facility_id: str
    name: str
    limit_usd: float
    drawn_usd: float
    rate: float
    min_liquidity_covenant_usd: float
    max_net_leverage: float
    ltm_ebitda_usd: float
    gross_debt_usd: float
    margin_bps: float | None = None      # Applicable Margin over SOFR; all-in rate = SOFR + margin when market data is on
    rate_source: str = "facility terms"


class RiskEvent(BaseModel):
    event_id: str
    hazard: str        # CYCLONE | FLOOD | EARTHQUAKE | PORT_CLOSURE | ...
    country: str
    severity: str      # GREEN | ORANGE | RED (GDACS-style alert levels)
    start: date
    expected_days: int
    description: str
    source: str = "Scenario library"
    classification: str = "SYNTHETIC"     # SYNTHETIC scenario event vs PUBLIC_MARKET_DATA live alert
    url: str | None = None


class ActionType(str, Enum):
    INTERCOMPANY_TRANSFER = "INTERCOMPANY_TRANSFER"
    REVOLVER_REPAY = "REVOLVER_REPAY"
    REVOLVER_DRAW = "REVOLVER_DRAW"
    EARLY_PAY_DISCOUNT = "EARLY_PAY_DISCOUNT"
    COLLECTIONS_ESCALATION = "COLLECTIONS_ESCALATION"
    REFINANCING_PLAN = "REFINANCING_PLAN"          # mandate to launch a refinancing — not a payment
    BANK_REBALANCING_PLAN = "BANK_REBALANCING_PLAN"  # mandate to rebalance bank counterparty exposure (TP-5.1)
    # Listed-company (public-data) decision mandates — Meridian reference policy PC-*; never payments
    REFINANCING_READINESS_PLAN = "REFINANCING_READINESS_PLAN"
    LIQUIDITY_POLICY_REVIEW = "LIQUIDITY_POLICY_REVIEW"
    WORKING_CAPITAL_PROGRAM = "WORKING_CAPITAL_PROGRAM"
    CAPITAL_ALLOCATION_REVIEW = "CAPITAL_ALLOCATION_REVIEW"
    RATE_RISK_REVIEW = "RATE_RISK_REVIEW"


PAYMENT_ACTIONS = {"INTERCOMPANY_TRANSFER", "REVOLVER_REPAY", "REVOLVER_DRAW", "EARLY_PAY_DISCOUNT"}
MANDATE_ACTIONS = {"REFINANCING_PLAN", "BANK_REBALANCING_PLAN", "REFINANCING_READINESS_PLAN", "LIQUIDITY_POLICY_REVIEW",
                   "WORKING_CAPITAL_PROGRAM", "CAPITAL_ALLOCATION_REVIEW", "RATE_RISK_REVIEW"}


class ProposedAction(BaseModel):
    action_id: str
    type: ActionType
    from_entity: str | None = None
    to_entity: str | None = None
    amount_usd: float
    currency: str = "USD"
    rationale: str
    annualized_benefit_usd: float = 0.0
    required_approver: str | None = None
    policy_refs: list[str] = Field(default_factory=list)
    status: str = "PROPOSED"   # PROPOSED | SUBMITTED | APPROVED | REJECTED | READY | TRANSMITTED
