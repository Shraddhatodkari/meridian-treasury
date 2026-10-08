"""Enterprise extensions of the deterministic EVALUATION company (synthetic): organisation, business units, bank
counterparties, the debt stack and covenants, accounts-payable payment history and bank-statement transactions.

Everything here uses its OWN random stream (seed + 10_007), so the original ledgers — and therefore every
back-test, model metric and release gate — are byte-identical to the v2 baseline.
"""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np

from meridian.domain.models import (
    Bank,
    BankAccount,
    BankTransaction,
    BusinessUnit,
    Covenant,
    DebtFacility,
    Organization,
    Payable,
)

# Customer segment → business unit (invoices inherit the BU of their customer)
BUSINESS_UNITS = [
    ("BU-IA", "Industrial Automation", ["Automotive", "Aerospace"]),
    ("BU-EI", "Energy & Infrastructure", ["Energy", "Construction", "Public sector"]),
    ("BU-AS", "Aftermarket & Services", ["Retail"]),
]
BANKS = [("BNK-ATLAS", "Atlas Bank", "US", "A+"), ("BNK-NORTH", "Northbridge", "GB", "A"),
         ("BNK-CROWN", "Crown Commercial", "US", "BBB+"), ("BNK-HARBR", "Harbor Trust", "SG", "A-")]
BANK_ID_BY_NAME = {n: b for b, n, _, _ in BANKS}


def bu_of_segment(segment: str) -> str:
    return next((b for b, _, segs in BUSINESS_UNITS if segment in segs), "BU-AS")


def _monthly_dates(anchor: date, months: int, start: date, end: date) -> list[date]:
    out, y, m = [], anchor.year, anchor.month
    for _ in range(40):
        d = date(y, m, min(anchor.day, 28))
        if start <= d < end:
            out.append(d)
        m -= months
        while m <= 0:
            m += 12
            y -= 1
    return out


def recurring_dates(cadence: str, anchor: date, start: date, end: date) -> list[date]:
    """Occurrences of a recurring flow inside [start, end) — works backwards as well as forwards."""
    step = {"WEEKLY": 7, "BIWEEKLY": 14}.get(cadence)
    if step:
        d = anchor
        while d >= start:
            d -= timedelta(days=step)
        while d < start:
            d += timedelta(days=step)
        out = []
        while d < end:
            out.append(d)
            d += timedelta(days=step)
        return out
    months = {"MONTHLY": 1, "QUARTERLY": 3}[cadence]
    fwd = anchor
    while fwd < end:   # move the anchor beyond the window so the backward walk covers it
        y, m = fwd.year + (fwd.month - 1 + months) // 12, (fwd.month - 1 + months) % 12 + 1
        fwd = date(y, m, min(anchor.day, 28))
    return sorted(_monthly_dates(fwd, months, start, end))


def extend_enterprise(g, seed: int) -> None:
    from meridian.data.generator import FX_TO_USD

    rng = np.random.default_rng(seed + 10_007)
    as_of = g.as_of
    g.organization = Organization(org_id="ORG-ORION", name="Orion Industrial Group", classification=g.classification,
                                  industry="Diversified industrial manufacturing (synthetic evaluation company)")
    g.business_units = [BusinessUnit(bu_id=b, name=n, segments=segs) for b, n, segs in BUSINESS_UNITS]
    g.banks = [Bank(bank_id=b, name=n, country=c, credit_rating=r) for b, n, c, r in BANKS]

    # ---- debt stack: RCF (existing facility) + amortising term loan + fixed-rate senior notes ----------------
    f = g.facility
    g.debt_facilities = [
        DebtFacility(facility_id=f.facility_id, name=f.name, type="RCF", borrower_entity="E01", limit_usd=f.limit_usd,
                     drawn_usd=f.drawn_usd, rate_type="FLOATING", margin_bps=f.margin_bps, rate=f.rate,
                     rate_source=f.rate_source, maturity_date=date(2029, 6, 30), prepayable=True,
                     prepay_min_usd=5_000_000, prepay_multiple_usd=1_000_000, clause_ref="RCF-8.1"),
        DebtFacility(facility_id="TLA-2027", name="USD 430M Term Loan A", type="TERM_LOAN", borrower_entity="E01",
                     limit_usd=430_000_000, drawn_usd=430_000_000, rate_type="FLOATING", margin_bps=175,
                     rate=0.0525, rate_source="simulation constant (5.25%)", maturity_date=date(2027, 9, 30),
                     prepayable=True, prepay_min_usd=10_000_000, prepay_multiple_usd=5_000_000,
                     amortization_pct_per_year=0.05, clause_ref="TLA-6.1"),
        DebtFacility(facility_id="SN-2030", name="USD 500M 4.25% Senior Notes due 2030", type="SENIOR_NOTES",
                     borrower_entity="E01", limit_usd=500_000_000, drawn_usd=500_000_000, rate_type="FIXED",
                     fixed_rate=0.0425, rate=0.0425, rate_source="fixed coupon 4.25%", maturity_date=date(2030, 3, 15),
                     clause_ref="SN-4.1"),
    ]
    g.covenants = [
        Covenant(covenant_id="COV-LIQ", facility_id=f.facility_id, name="Minimum liquidity", metric="min_liquidity",
                 threshold=f.min_liquidity_covenant_usd, direction=">=", clause_ref="RCF-22.1",
                 test_frequency="CONTINUOUS"),
        Covenant(covenant_id="COV-LEV", facility_id=f.facility_id, name="Maximum net leverage",
                 metric="max_net_leverage", threshold=f.max_net_leverage, direction="<=", clause_ref="RCF-22.2"),
        Covenant(covenant_id="COV-ICR", facility_id=f.facility_id, name="Minimum interest cover",
                 metric="min_interest_cover", threshold=4.0, direction=">=", clause_ref="RCF-22.3"),
    ]

    # ---- AP payment history (last 270 days): captured / missed discounts, early and late payments -----------
    opcos = [e for e in g.entities if e.role == "OPCO"]
    sup_by_country: dict[str, list[str]] = {}
    for s in g.suppliers:
        sup_by_country.setdefault(s.country, []).append(s.supplier_id)
    weekly_rev = {e.entity_id: sum(r.amount * FX_TO_USD[r.currency] for r in g.receivables
                                   if r.entity_id == e.entity_id and r.issue_date >= as_of - timedelta(days=364)) / 52
                  for e in opcos}
    seq = 0
    for e in opcos:
        weekly_ap = weekly_rev[e.entity_id] * 0.62
        for day in range(-270, 1):
            d = as_of + timedelta(days=day)
            if d.weekday() >= 5:
                continue
            for _ in range(rng.poisson(weekly_ap / 5 / 300_000)):
                terms = int(rng.choice([30, 45, 60], p=[0.4, 0.35, 0.25]))
                due = d + timedelta(days=terms)
                disc = rng.random() < 0.3
                behaviour = rng.random()
                amount = round(float(rng.lognormal(np.log(300_000) - 0.2, 0.6)) / FX_TO_USD[e.currency], 2)
                supplier = str(rng.choice(sup_by_country[e.country]))
                pay_shift = int(rng.integers(5, 21))
                if due >= as_of:
                    continue  # the open book was generated by the core simulator
                dpct = 0.02 if disc and terms == 30 else (0.01 if disc else 0.0)
                if disc and behaviour < 0.55:
                    paid = d + timedelta(days=min(pay_shift // 2 + 4, 10))       # discount captured
                elif behaviour < 0.18:
                    paid = due - timedelta(days=pay_shift)                       # paid early, no discount
                elif behaviour > 0.9:
                    paid = due + timedelta(days=pay_shift)                       # paid late
                else:
                    paid = due + timedelta(days=int(behaviour * 10) % 3 - 1)
                paid = min(max(paid, d + timedelta(days=1)), as_of - timedelta(days=1))
                seq += 1
                g.payables.append(Payable(
                    bill_id=f"BILL-H{seq:06d}", entity_id=e.entity_id, supplier_id=supplier, currency=e.currency,
                    amount=amount, issue_date=d, due_date=due, discount_pct=dpct, discount_days=10 if disc else 0,
                    paid_date=paid))

    # ---- bank-statement transactions for the last 8 weeks, derived from the same ledgers ---------------------
    g.bank_transactions = derive_bank_transactions(g, weeks=8)


def derive_bank_transactions(g, weeks: int = 8) -> list[BankTransaction]:
    """Booked cash movements consistent with the ledgers: customer receipts, supplier payments (net of any
    discount taken), recurring operating flows and facility interest. Production loads camt.053 entries instead."""
    as_of = g.as_of
    main_acct: dict[str, BankAccount] = {}
    for a in g.accounts:
        if a.entity_id not in main_acct or a.balance > main_acct[a.entity_id].balance:
            main_acct[a.entity_id] = a
    start = as_of - timedelta(days=7 * weeks)
    txns: list[BankTransaction] = []

    def add(eid: str, d: date, amt: float, ccy: str, cat: str, cp: str | None, ref: str | None) -> None:
        txns.append(BankTransaction(txn_id=f"TX-{len(txns) + 1:07d}", account_id=main_acct[eid].account_id,
                                    entity_id=eid, booking_date=d, amount=round(amt, 2), currency=ccy, category=cat,
                                    counterparty_id=cp, reference=ref))

    for r in g.receivables:
        if r.settled_date and start <= r.settled_date < as_of:
            add(r.entity_id, r.settled_date, r.amount, r.currency, "CUSTOMER_RECEIPT", r.customer_id, r.invoice_id)
    for p in g.payables:
        if p.paid_date and start <= p.paid_date < as_of:
            took = p.discount_pct and p.paid_date <= p.issue_date + timedelta(days=p.discount_days)
            add(p.entity_id, p.paid_date, -p.amount * (1 - p.discount_pct) if took else -p.amount, p.currency,
                "SUPPLIER_PAYMENT", p.supplier_id, p.bill_id)
    for rf in g.recurring:
        cat = rf.category if rf.category in {"PAYROLL", "RENT", "TAX"} else "OPEX"
        for d in recurring_dates(rf.cadence, rf.anchor, start, as_of):
            add(rf.entity_id, d, rf.amount, rf.currency, cat, None, f"{rf.category} {d:%Y-%m-%d}")
    for fac in g.debt_facilities:
        if fac.type == "SENIOR_NOTES":
            continue
        for d in recurring_dates("MONTHLY", date(as_of.year, as_of.month, 1), start, as_of):
            add("E01", d, -fac.drawn_usd * fac.rate / 12, "USD", "INTEREST", fac.facility_id, f"{fac.facility_id} interest")

    # Zero-balancing cash pool: subsidiaries that generated more cash than they held were swept to the HoldCo
    # during the window, so no entity shows an implausible negative opening balance.
    hub = next(e for e in g.entities if e.role == "HOLDCO")
    ents = {e.entity_id: e for e in g.entities}
    bal: dict[str, float] = {}
    for a in g.accounts:
        bal[a.entity_id] = bal.get(a.entity_id, 0.0) + a.balance
    net: dict[str, float] = {}
    for t in txns:
        net[t.entity_id] = net.get(t.entity_id, 0.0) + t.amount
    sweep_day = start + timedelta(days=7 * weeks // 2)
    for eid, e in ents.items():
        if eid == hub.entity_id:
            continue
        floor = 0.5 * e.min_buffer_local
        implied_open = bal.get(eid, 0.0) - net.get(eid, 0.0)
        if implied_open < floor:
            d_local = floor - implied_open
            usd = d_local * g.fx.get(e.currency, 1.0)
            add(eid, sweep_day, -d_local, e.currency, "INTERCOMPANY", hub.entity_id, f"Cash-pool sweep {eid}->{hub.entity_id}")
            add(hub.entity_id, sweep_day, usd, hub.currency, "INTERCOMPANY", eid, f"Cash-pool sweep {eid}->{hub.entity_id}")
    return sorted(txns, key=lambda t: (t.booking_date, t.txn_id))
