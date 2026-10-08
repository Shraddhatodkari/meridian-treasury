"""Deterministic simulation of a fictional multinational: "Orion Industrial Group".

~$3B revenue, 17 legal entities in 14 currencies, ~150 B2B customers, ~80 suppliers,
two years of invoice history, an open AR/AP book, recurring payroll/rent/tax flows, a
syndicated revolver with covenants, and bank accounts with realistic fragmentation
(surplus cash trapped in capital-controlled countries while other subsidiaries run short).

Customer payment behaviour is driven by HIDDEN parameters (rating, country, segment,
disputes, quarter-end effects and, for some customers, a deteriorating trend). Open
invoices carry a hidden actual settlement date so forecasts can be back-tested honestly.
All names are fictional.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

import numpy as np

from meridian.domain.models import (
    Bank,
    BankAccount,
    BankTransaction,
    BusinessUnit,
    Covenant,
    Customer,
    DebtFacility,
    Entity,
    Facility,
    Organization,
    Payable,
    Receivable,
    RecurringFlow,
    RiskEvent,
    Supplier,
)

FX_TO_USD = {"USD": 1.0, "EUR": 1.08, "GBP": 1.27, "CAD": 0.73, "MXN": 0.055, "BRL": 0.18, "INR": 0.012,
             "CNY": 0.14, "SGD": 0.74, "JPY": 0.0067, "AUD": 0.66, "AED": 0.272, "ZAR": 0.055, "PLN": 0.25}

# entity_id, name, country, ccy, role, controls, repatriation allowance USD, upstream bps, revenue share,
# cash multiple of weekly net outflow (drives surplus/shortfall), deposit yield, overdraft rate
_ENTITIES = [
    ("E01", "Orion Group Treasury (US HoldCo)", "US", "USD", "HOLDCO", False, 0, 0, 0.00, 0.0, 0.043, 0.090),
    ("E02", "Orion Industrial USA LLC", "US", "USD", "OPCO", False, 0, 0, 0.24, 1.2, 0.040, 0.090),
    ("E03", "Orion Canada Ltd", "CA", "CAD", "OPCO", False, 0, 5, 0.05, 2.5, 0.032, 0.085),
    ("E04", "Orion Mexico S.A. de C.V.", "MX", "MXN", "OPCO", False, 0, 25, 0.05, 0.3, 0.070, 0.160),
    ("E05", "Orion Brasil Ltda", "BR", "BRL", "OPCO", True, 6_000_000, 38, 0.05, 3.5, 0.095, 0.180),
    ("E06", "Orion UK Ltd", "GB", "GBP", "OPCO", False, 0, 0, 0.08, 4.0, 0.038, 0.080),
    ("E07", "Orion Deutschland GmbH", "DE", "EUR", "OPCO", False, 0, 0, 0.11, 5.0, 0.025, 0.070),
    ("E08", "Orion France SAS", "FR", "EUR", "OPCO", False, 0, 0, 0.06, 0.4, 0.025, 0.070),
    ("E09", "Orion Nederland BV", "NL", "EUR", "OPCO", False, 0, 0, 0.04, 2.0, 0.025, 0.070),
    ("E10", "Orion Polska Sp. z o.o.", "PL", "PLN", "OPCO", False, 0, 10, 0.03, 0.2, 0.045, 0.110),
    ("E11", "Orion India Pvt Ltd", "IN", "INR", "OPCO", True, 3_000_000, 50, 0.07, 6.0, 0.065, 0.120),
    ("E12", "Orion (Shanghai) Co Ltd", "CN", "CNY", "OPCO", True, 0, 100, 0.08, 7.0, 0.015, 0.060),
    ("E13", "Orion Singapore Pte Ltd", "SG", "SGD", "OPCO", False, 0, 0, 0.04, 3.0, 0.030, 0.070),
    ("E14", "Orion Japan KK", "JP", "JPY", "OPCO", False, 0, 15, 0.04, 1.5, 0.002, 0.030),
    ("E15", "Orion Australia Pty Ltd", "AU", "AUD", "OPCO", False, 0, 0, 0.03, 0.5, 0.040, 0.090),
    ("E16", "Orion Middle East FZE", "AE", "AED", "OPCO", False, 0, 0, 0.02, 2.0, 0.040, 0.090),
    ("E17", "Orion South Africa (Pty) Ltd", "ZA", "ZAR", "OPCO", True, 2_000_000, 20, 0.02, 1.0, 0.075, 0.140),
]
GROUP_REVENUE_USD = 3_000_000_000
RATINGS = {"AA": -3.0, "A": 1.0, "BBB": 5.0, "BB": 13.0, "B": 26.0}
COUNTRY_DELAY = {"US": 0, "CA": 1, "MX": 9, "BR": 12, "GB": 2, "DE": 0, "FR": 6, "NL": 1, "PL": 5, "IN": 14,
                 "CN": 8, "SG": 1, "JP": -1, "AU": 3, "AE": 7, "ZA": 10}
SEGMENTS = {"Automotive": 4.0, "Aerospace": 2.0, "Energy": 6.0, "Construction": 9.0, "Retail": 3.0, "Public sector": 15.0}
_NAME_A = ["Apex", "Borealis", "Cobalt", "Delta", "Everest", "Falcon", "Granite", "Helix", "Ion", "Juniper",
           "Keystone", "Lumen", "Mosaic", "Nimbus", "Onyx", "Polaris", "Quartz", "Redwood", "Summit", "Titan",
           "Umbra", "Vertex", "Willow", "Xenon", "Yarrow", "Zenith"]
_NAME_B = ["Motors", "Aero", "Energy", "Builders", "Retail Group", "Systems", "Logistics", "Utilities",
           "Rail", "Marine", "Mining", "Components"]


@dataclass
class GroupData:
    as_of: date
    entities: list[Entity] = field(default_factory=list)
    accounts: list[BankAccount] = field(default_factory=list)
    customers: list[Customer] = field(default_factory=list)
    suppliers: list[Supplier] = field(default_factory=list)
    receivables: list[Receivable] = field(default_factory=list)
    payables: list[Payable] = field(default_factory=list)
    recurring: list[RecurringFlow] = field(default_factory=list)
    facility: Facility | None = None
    risk_events: list[RiskEvent] = field(default_factory=list)
    fx: dict[str, float] = field(default_factory=lambda: dict(FX_TO_USD))
    classification: str = "SYNTHETIC"          # SYNTHETIC | CLIENT_DATA — shown on every screen and report
    tenant: str = "Orion Industrial Group (synthetic demo company)"
    organization: Organization | None = None
    business_units: list[BusinessUnit] = field(default_factory=list)
    banks: list[Bank] = field(default_factory=list)
    debt_facilities: list[DebtFacility] = field(default_factory=list)
    covenants: list[Covenant] = field(default_factory=list)
    bank_transactions: list[BankTransaction] = field(default_factory=list)

    # ---- convenience views used across analytics -------------------------------------------------
    def open_payables(self) -> list[Payable]:
        return [p for p in self.payables if p.paid_date is None]

    def paid_payables(self) -> list[Payable]:
        return [p for p in self.payables if p.paid_date is not None]


def iban_check_digits(country: str, bban: str) -> str:
    """ISO 13616 mod-97 check digits."""
    digits = "".join(str(int(ch, 36)) for ch in (bban + country + "00").upper())
    return f"{98 - int(digits) % 97:02d}"


def _iban(rng: np.random.Generator, country: str) -> str:
    bban = "ORIN" + "".join(str(d) for d in rng.integers(0, 10, 14))
    return f"{country}{iban_check_digits(country, bban)}{bban}"


def generate(seed: int = 7, as_of: date = date(2026, 9, 28), history_days: int = 730) -> GroupData:
    rng = np.random.default_rng(seed)
    g = GroupData(as_of=as_of)
    start = as_of - timedelta(days=history_days)

    # ---------------------------------------------------------------- entities
    weekly_rev = {}
    for (eid, name, ctry, ccy, role, ctrl, allow, bps, share, cash_mult, yld, od) in _ENTITIES:
        rev_usd_week = GROUP_REVENUE_USD * share / 52
        weekly_rev[eid] = rev_usd_week
        buffer_usd = max(1_500_000, 0.6 * rev_usd_week) if role == "OPCO" else 25_000_000
        g.entities.append(Entity(
            entity_id=eid, name=name, country=ctry, currency=ccy, role=role, capital_controls=ctrl,
            repatriation_allowance_usd=allow, upstream_cost_bps=bps,
            min_buffer_local=round(buffer_usd / FX_TO_USD[ccy], -3), deposit_yield=yld, overdraft_rate=od))
        # Cash: fragmented on purpose — several accounts per entity
        total_usd = 60_000_000 if role == "HOLDCO" else rev_usd_week * cash_mult
        n_acc = 1 if role == "HOLDCO" else int(rng.integers(1, 4))
        splits = rng.dirichlet(np.ones(n_acc))
        for k, s in enumerate(splits):
            g.accounts.append(BankAccount(
                account_id=f"{eid}-A{k + 1}", entity_id=eid, currency=ccy,
                bank=["Atlas Bank", "Northbridge", "Crown Commercial", "Harbor Trust"][k % 4],
                iban=_iban(rng, ctry), balance=round(total_usd * s / FX_TO_USD[ccy], 2)))

    opcos = [e for e in g.entities if e.role == "OPCO"]

    # ---------------------------------------------------------------- customers (hidden behaviour)
    behaviour = {}
    for i in range(150):
        e = opcos[int(rng.choice(len(opcos), p=np.array([weekly_rev[x.entity_id] for x in opcos]) /
                                  sum(weekly_rev[x.entity_id] for x in opcos)))]
        rating = str(rng.choice(list(RATINGS), p=[0.08, 0.27, 0.35, 0.22, 0.08]))
        seg = str(rng.choice(list(SEGMENTS)))
        cid = f"C{i + 1:04d}"
        g.customers.append(Customer(customer_id=cid, name=f"{rng.choice(_NAME_A)} {rng.choice(_NAME_B)} "
                                                           f"{e.country}-{i + 1}",
                                    country=e.country, segment=seg, credit_rating=rating))
        behaviour[cid] = {
            "entity": e.entity_id,
            "base": RATINGS[rating] + COUNTRY_DELAY[e.country] + SEGMENTS[seg] * 0.6 + rng.normal(0, 3),
            "noise": float(rng.uniform(2, 9)),
            "dispute_p": float(rng.uniform(0.01, 0.08)),
            "trend": float(rng.choice([0.0, 0.0, 0.0, 0.0, rng.uniform(0.02, 0.06)])),  # days/day worsening
            "paperless": bool(rng.random() < 0.7),
            "weight": float(rng.lognormal(0, 0.8)),
        }

    # ---------------------------------------------------------------- receivables
    seq = 0
    cust_by_entity: dict[str, list[str]] = {}
    for cid, b in behaviour.items():
        cust_by_entity.setdefault(b["entity"], []).append(cid)
    for e in opcos:
        cids = cust_by_entity.get(e.entity_id, [])
        if not cids:
            continue
        w = np.array([behaviour[c]["weight"] for c in cids])
        w = w / w.sum()
        avg_inv_usd = 400_000
        for day in range(history_days + 1):
            d = start + timedelta(days=day)
            if d.weekday() >= 5:
                continue
            n = rng.poisson(weekly_rev[e.entity_id] / 5 / avg_inv_usd)
            for _ in range(n):
                cid = cids[int(rng.choice(len(cids), p=w))]
                b = behaviour[cid]
                amt_usd = float(rng.lognormal(np.log(avg_inv_usd) - 0.3, 0.75))
                terms = int(rng.choice([30, 45, 60, 90], p=[0.35, 0.3, 0.25, 0.1]))
                disputed = bool(rng.random() < b["dispute_p"])
                qe = d.month in (3, 6, 9, 12) and d.day > 15
                late = (b["base"] + b["trend"] * day + (25 if disputed else 0) + (4 if qe else 0)
                        - (2 if b["paperless"] else 0) + 1.5 * np.log(amt_usd / avg_inv_usd + 1)
                        + rng.gamma(2.0, b["noise"] / 2) - b["noise"])
                due = d + timedelta(days=terms)
                settle = due + timedelta(days=int(round(max(late, -terms + 3))))
                seq += 1
                g.receivables.append(Receivable(
                    invoice_id=f"INV-{seq:06d}", entity_id=e.entity_id, customer_id=cid, currency=e.currency,
                    amount=round(amt_usd / FX_TO_USD[e.currency], 2), issue_date=d, due_date=due, terms_days=terms,
                    disputed=disputed, paperless=b["paperless"],
                    settled_date=settle if settle < as_of else None,
                    actual_settle_date=None if settle < as_of else settle))

    # ---------------------------------------------------------------- suppliers & payables (open book)
    for i in range(80):
        e = opcos[i % len(opcos)]
        g.suppliers.append(Supplier(supplier_id=f"S{i + 1:04d}", name=f"{rng.choice(_NAME_A)} Supply {i + 1}",
                                    country=e.country, critical=bool(rng.random() < 0.2)))
    sup_by_country: dict[str, list[str]] = {}
    for s in g.suppliers:
        sup_by_country.setdefault(s.country, []).append(s.supplier_id)
    bseq = 0
    for e in opcos:
        weekly_ap = weekly_rev[e.entity_id] * 0.62
        for day in range(-75, 1):
            d = as_of + timedelta(days=day)
            if d.weekday() >= 5:
                continue
            for _ in range(rng.poisson(weekly_ap / 5 / 300_000)):
                terms = int(rng.choice([30, 45, 60], p=[0.4, 0.35, 0.25]))
                due = d + timedelta(days=terms)
                if due < as_of:
                    continue  # already paid
                disc = rng.random() < 0.3
                bseq += 1
                g.payables.append(Payable(
                    bill_id=f"BILL-{bseq:06d}", entity_id=e.entity_id,
                    supplier_id=str(rng.choice(sup_by_country[e.country])), currency=e.currency,
                    amount=round(float(rng.lognormal(np.log(300_000) - 0.2, 0.6)) / FX_TO_USD[e.currency], 2),
                    issue_date=d, due_date=due, discount_pct=0.02 if disc and terms == 30 else (0.01 if disc else 0.0),
                    discount_days=10 if disc else 0))

    # ---------------------------------------------------------------- recurring flows
    for e in opcos:
        r = weekly_rev[e.entity_id] / FX_TO_USD[e.currency]
        g.recurring += [
            RecurringFlow(entity_id=e.entity_id, category="PAYROLL", currency=e.currency, amount=-r * 0.30,
                          cadence="BIWEEKLY", anchor=as_of + timedelta(days=4)),
            RecurringFlow(entity_id=e.entity_id, category="RENT", currency=e.currency, amount=-r * 0.12,
                          cadence="MONTHLY", anchor=date(as_of.year, as_of.month, 1)),
            RecurringFlow(entity_id=e.entity_id, category="OPEX", currency=e.currency, amount=-r * 0.08,
                          cadence="WEEKLY", anchor=as_of + timedelta(days=2)),
            RecurringFlow(entity_id=e.entity_id, category="TAX", currency=e.currency, amount=-r * 0.35,
                          cadence="QUARTERLY", anchor=date(2026, 10, 15)),
        ]
    g.recurring.append(RecurringFlow(entity_id="E01", category="OPEX", currency="USD", amount=-1_200_000,
                                     cadence="WEEKLY", anchor=as_of + timedelta(days=4)))

    g.facility = Facility(facility_id="RCF-2029", name="USD 600M Syndicated Revolving Credit Facility",
                          limit_usd=600_000_000, drawn_usd=220_000_000, rate=0.0685,
                          min_liquidity_covenant_usd=250_000_000, max_net_leverage=3.0,
                          ltm_ebitda_usd=430_000_000, gross_debt_usd=1_150_000_000, margin_bps=250,
                          rate_source="simulation constant (6.85%)")

    # Illustrative hazard feed in GDACS-like format (see risk/events.py for the live connector).
    g.risk_events = [
        RiskEvent(event_id="EV-CN-TC-2026-31", hazard="TROPICAL_CYCLONE", country="CN", severity="ORANGE",
                  start=as_of - timedelta(days=2), expected_days=10,
                  description="Tropical cyclone landfall near Pearl River Delta; port and road closures expected."),
        RiskEvent(event_id="EV-IN-FL-2026-12", hazard="FLOOD", country="IN", severity="ORANGE",
                  start=as_of - timedelta(days=6), expected_days=14,
                  description="Monsoon flooding across western India affecting logistics corridors."),
        RiskEvent(event_id="EV-MX-EQ-2026-04", hazard="EARTHQUAKE", country="MX", severity="GREEN",
                  start=as_of - timedelta(days=1), expected_days=3,
                  description="Moderate earthquake in central Mexico; limited infrastructure impact reported."),
    ]
    from meridian.data.enterprise import extend_enterprise

    extend_enterprise(g, seed)
    return g
