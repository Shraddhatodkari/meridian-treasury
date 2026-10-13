"""Governed ingestion of the organisation's own treasury data.

Input package (one directory per load, produced by the ERP / TMS / bank-connectivity team):

    manifest.json            tenant, extract system, as-of date, data classification
    entities.csv             legal entities, currency, buffers, capital-control status
    bank_accounts.csv        account master (IBAN, bank, currency, owning entity)
    bank_statements/*.xml    ISO 20022 camt.053 end-of-day statements (closing booked balances)
    customers.csv, suppliers.csv
    ar_invoices.csv          open AND settled receivables (settled history trains the payment model)
    ap_invoices.csv          open payables incl. discount terms
    recurring_flows.csv      payroll / rent / tax / opex schedules
    facility.json            committed facilities: limit, drawn, margin over SOFR, covenants

Every file is validated (schema, types, referential integrity, business rules, IBAN mod-97, statement
freshness) BEFORE anything is written. ERRORs block the load; WARNINGs are recorded in provenance.
"""

from __future__ import annotations

import csv
import io
import json
import uuid
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any, cast

from meridian.data.generator import GroupData, iban_check_digits
from meridian.domain.models import (
    Bank,
    BankAccount,
    BankTransaction,
    Covenant,
    Customer,
    DebtFacility,
    Entity,
    Facility,
    Payable,
    Receivable,
    RecurringFlow,
    Supplier,
)
from meridian.ingest.bai2 import parse_bai2
from meridian.ingest.camt053 import build_camt053, parse_camt053
from meridian.provenance import Classification, SourceRecord, ValidationStatus, checksum, now_utc

SPEC: dict[str, list[str]] = {
    "entities.csv": ["entity_id", "name", "country", "currency", "role", "capital_controls",
                     "repatriation_allowance_usd", "upstream_cost_bps", "min_buffer_local", "deposit_yield",
                     "overdraft_rate"],
    "bank_accounts.csv": ["account_id", "entity_id", "bank", "currency", "iban"],
    "customers.csv": ["customer_id", "name", "country", "segment", "credit_rating"],
    "suppliers.csv": ["supplier_id", "name", "country", "critical"],
    "ar_invoices.csv": ["invoice_id", "entity_id", "customer_id", "currency", "amount", "issue_date", "due_date",
                        "terms_days", "disputed", "paperless", "settled_date"],
    "ap_invoices.csv": ["bill_id", "entity_id", "supplier_id", "currency", "amount", "issue_date", "due_date",
                        "discount_pct", "discount_days", "paid_date"],
    "recurring_flows.csv": ["entity_id", "category", "currency", "amount", "cadence", "anchor"],
    # optional files (loaded when present)
    "debt_facilities.csv": ["facility_id", "name", "type", "borrower_entity", "currency", "limit_usd", "drawn_usd",
                            "rate_type", "margin_bps", "fixed_rate", "rate", "maturity_date", "prepayable",
                            "prepay_min_usd", "prepay_multiple_usd", "amortization_pct_per_year", "clause_ref"],
    "covenants.csv": ["covenant_id", "facility_id", "name", "metric", "threshold", "direction", "clause_ref",
                      "test_frequency"],
    "banks.csv": ["bank_id", "name", "country", "credit_rating"],
}
ISO_CCY = {"USD", "EUR", "GBP", "CAD", "MXN", "BRL", "INR", "CNY", "SGD", "JPY", "AUD", "AED", "ZAR", "PLN", "CHF",
           "SEK", "NOK", "DKK", "CZK", "HUF", "KRW", "HKD", "NZD", "THB", "IDR", "MYR", "PHP", "ILS", "TRY", "RON"}


@dataclass
class Check:
    file: str
    level: str      # ERROR | WARN
    rule: str
    detail: str


@dataclass
class IngestionReport:
    ingestion_id: str
    source_dir: str
    classification: str
    tenant: str
    as_of: str | None
    checks: list[Check] = field(default_factory=list)
    sources: list[SourceRecord] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)
    loaded: bool = False

    @property
    def errors(self) -> list[Check]:
        return [c for c in self.checks if c.level == "ERROR"]

    def to_dict(self) -> dict:
        return {"ingestion_id": self.ingestion_id, "source_dir": self.source_dir, "classification": self.classification,
                "tenant": self.tenant, "as_of": self.as_of, "loaded": self.loaded, "counts": self.counts,
                "errors": len(self.errors), "warnings": len(self.checks) - len(self.errors),
                "checks": [c.__dict__ for c in self.checks[:500]],
                "sources": [s.model_dump(mode="json") for s in self.sources], "created_at": now_utc().isoformat()}


def iban_valid(iban: str) -> bool:
    iban = iban.replace(" ", "").upper()
    return len(iban) >= 15 and iban[:2].isalpha() and iban_check_digits(iban[:2], iban[4:]) == iban[2:4]


def _b(v: str) -> bool:
    return str(v).strip().lower() in {"1", "true", "yes", "y"}


def _d(v: str) -> date | None:
    v = (v or "").strip()
    return date.fromisoformat(v) if v else None


def _req_d(v: str) -> date:
    """A required date column: an empty value reaches the model as None and is rejected by its validation."""
    return cast(date, _d(v))


def _read(path: Path, rep: IngestionReport) -> list[dict]:
    if not path.exists():
        rep.checks.append(Check(path.name, "ERROR", "file_present", "required file missing"))
        return []
    rows = list(csv.DictReader(io.StringIO(path.read_text(encoding="utf-8-sig"))))
    cols = set(rows[0].keys()) if rows else set()
    missing = [c for c in SPEC[path.name] if c not in cols]
    if missing:
        rep.checks.append(Check(path.name, "ERROR", "schema", f"missing columns {missing}"))
        return []
    return rows


def _parse(rows: list[dict], fn, fname: str, key: str | None, rep: IngestionReport) -> list:
    out, seen = [], set()
    for i, r in enumerate(rows, start=2):
        try:
            obj = fn(r)
        except (ValueError, TypeError, KeyError) as e:
            rep.checks.append(Check(fname, "ERROR", "type", f"row {i}: {e}"))
            continue
        k = getattr(obj, key, None) if key else None
        if key and k in seen:
            rep.checks.append(Check(fname, "ERROR", "unique_key", f"duplicate {key} {k}"))
            continue
        if key:
            seen.add(k)
        out.append(obj)
    return out


def _source(sid: str, name: str, path: Path, rep: IngestionReport, n: int, system: str, as_of: date | None,
            sla: float | None = None) -> SourceRecord:
    issues = [f"{c.level}: {c.rule} — {c.detail}" for c in rep.checks if c.file == path.name][:50]
    lvl = {c.level for c in rep.checks if c.file == path.name}
    status = ValidationStatus.FAILED if "ERROR" in lvl else (
        ValidationStatus.PASSED_WITH_WARNINGS if lvl else ValidationStatus.PASSED)
    raw = path.read_bytes() if path.is_file() else b"".join(p.read_bytes() for p in sorted(path.glob("*.xml")))
    return SourceRecord(source_id=sid, name=name, system=system, uri=f"file://{path.name}",
                        classification=Classification(rep.classification), retrieved_at=now_utc(), as_of=as_of,
                        record_count=n, checksum_sha256=checksum(raw), validation=status, issues=issues,
                        freshness_sla_hours=sla)


def validate_and_build(src: Path) -> tuple[GroupData | None, IngestionReport]:
    man: dict[str, Any] = json.loads((src / "manifest.json").read_text(encoding="utf-8")) if (src / "manifest.json").exists() else {}
    cls = {"SYNTHETIC_SAMPLE": "SYNTHETIC", "SYNTHETIC": "SYNTHETIC"}.get(man.get("data_classification", ""), "CLIENT_DATA")
    rep = IngestionReport(ingestion_id=f"ING-{uuid.uuid4().hex[:10]}", source_dir=str(src), classification=cls,
                          tenant=man.get("tenant", "Unnamed tenant"), as_of=man.get("as_of"))
    if not man:
        rep.checks.append(Check("manifest.json", "ERROR", "file_present", "manifest.json missing"))
    as_of = _d(man.get("as_of", "")) if man else None
    system = man.get("extract_system", "ERP extract")

    ents = _parse(_read(src / "entities.csv", rep), lambda r: Entity(
        entity_id=r["entity_id"], name=r["name"], country=r["country"], currency=r["currency"], role=r["role"],
        capital_controls=_b(r["capital_controls"]), repatriation_allowance_usd=float(r["repatriation_allowance_usd"] or 0),
        upstream_cost_bps=float(r["upstream_cost_bps"] or 0), min_buffer_local=float(r["min_buffer_local"]),
        deposit_yield=float(r["deposit_yield"]), overdraft_rate=float(r["overdraft_rate"])),
        "entities.csv", "entity_id", rep)
    ent_ids = {e.entity_id for e in ents}
    if ents and sum(e.role == "HOLDCO" for e in ents) != 1:
        rep.checks.append(Check("entities.csv", "ERROR", "holdco", "exactly one HOLDCO (in-house bank) required"))
    for e in ents:
        if e.currency not in ISO_CCY:
            rep.checks.append(Check("entities.csv", "ERROR", "currency", f"{e.entity_id}: unknown currency {e.currency}"))

    accts_raw = _parse(_read(src / "bank_accounts.csv", rep), lambda r: dict(
        account_id=r["account_id"], entity_id=r["entity_id"], bank=r["bank"], currency=r["currency"],
        iban=r["iban"].replace(" ", "")), "bank_accounts.csv", None, rep)
    for a in accts_raw:
        if a["entity_id"] not in ent_ids:
            rep.checks.append(Check("bank_accounts.csv", "ERROR", "ref_entity", f"{a['account_id']}: unknown entity"))
        if not iban_valid(a["iban"]):
            rep.checks.append(Check("bank_accounts.csv", "ERROR", "iban_mod97", f"{a['account_id']}: invalid IBAN checksum"))

    # ---- bank statements (camt.053)
    stmts: dict[str, dict] = {}
    seen_ids: set[str] = set()
    sdir = src / "bank_statements"
    for p in sorted(sdir.glob("*.xml")) if sdir.exists() else []:
        try:
            for s in parse_camt053(p.read_bytes()):
                if s.get("statement_id") and s["statement_id"] in seen_ids:
                    rep.checks.append(Check("bank_statements", "WARN", "duplicate_statement",
                                            f"{p.name}: statement {s['statement_id']} already loaded — ignored"))
                    continue
                seen_ids.add(s.get("statement_id") or p.name)
                prev = stmts.get(s["iban"])
                if prev is None or (s.get("balance_date") or "") >= (prev.get("balance_date") or ""):
                    stmts[s["iban"]] = s
        except Exception as e:  # noqa: BLE001 - any malformed XML is a data-quality error
            rep.checks.append(Check("bank_statements", "ERROR", "camt053_parse", f"{p.name}: {e}"))
    for p in sorted([*sdir.glob("*.bai"), *sdir.glob("*.bai2")]) if sdir.exists() else []:
        try:
            for s in parse_bai2(p.read_text(encoding="utf-8")):   # BAI2 identifies accounts by account number (matched to IBAN field)
                stmts[s["account"]] = {**s, "iban": s["account"]}
        except Exception as e:  # noqa: BLE001
            rep.checks.append(Check("bank_statements", "ERROR", "bai2_parse", f"{p.name}: {e}"))
    from meridian.ingest.quality import dedupe_transactions, reconcile

    txns: list[BankTransaction] = []
    accounts = []
    latest_stmt = None
    recon = {"RECONCILED": 0, "BREAK": 0, "NOT_CHECKABLE": 0}
    for a in accts_raw:
        stm = stmts.get(a["iban"])
        if not stm or stm["closing_balance"] is None:
            rep.checks.append(Check("bank_statements", "ERROR", "statement_missing", f"{a['account_id']}: no camt.053 CLBD"))
            continue
        if stm["currency"] != a["currency"]:
            rep.checks.append(Check("bank_statements", "ERROR", "currency_match", f"{a['account_id']}: statement currency"))
        if as_of and stm["balance_date"] and (as_of - stm["balance_date"]).days > 3:
            rep.checks.append(Check("bank_statements", "WARN", "freshness", f"{a['account_id']}: balance dated {stm['balance_date']}"))
        latest_stmt = max(filter(None, [latest_stmt, stm["balance_date"]]), default=None)
        accounts.append(BankAccount(**a, balance=stm["closing_balance"]))
        kept, dups = dedupe_transactions(stm)
        for d in dups:
            rep.checks.append(Check("bank_statements", "WARN", "duplicate_transaction", f"{a['account_id']}: {d} dropped"))
        rc = reconcile({**stm, "transactions": kept})
        recon[rc["status"]] += 1
        if rc["status"] == "BREAK":
            rep.checks.append(Check("bank_statements", "ERROR", "balance_reconciliation",
                                    f"{a['account_id']}: opening {rc['opening']:.2f} + entries {rc['entries_total']:.2f} "
                                    f"!= closing {rc['closing']:.2f} (difference {rc['difference']:.2f})"))
        for k, t in enumerate(kept):
            if t.get("booking_date") is None:
                continue
            txns.append(BankTransaction(txn_id=f"{a['account_id']}-{k + 1:06d}", account_id=a["account_id"],
                                        entity_id=a["entity_id"], booking_date=t["booking_date"], amount=t["amount"],
                                        currency=a["currency"], category=t.get("category") or "UNCLASSIFIED",
                                        counterparty_id=t.get("counterparty_id") or None, reference=t.get("reference")))

    rep.counts.update({f"statements_{k.lower()}": v for k, v in recon.items()})

    custs = _parse(_read(src / "customers.csv", rep), lambda r: Customer(**{k: r[k] for k in SPEC["customers.csv"]}),
                   "customers.csv", "customer_id", rep)
    cust_ids = {c.customer_id for c in custs}
    sups = _parse(_read(src / "suppliers.csv", rep), lambda r: Supplier(
        supplier_id=r["supplier_id"], name=r["name"], country=r["country"], critical=_b(r["critical"])),
        "suppliers.csv", "supplier_id", rep)
    sup_ids = {s.supplier_id for s in sups}

    ar = _parse(_read(src / "ar_invoices.csv", rep), lambda r: Receivable(
        invoice_id=r["invoice_id"], entity_id=r["entity_id"], customer_id=r["customer_id"], currency=r["currency"],
        amount=float(r["amount"]), issue_date=_req_d(r["issue_date"]), due_date=_req_d(r["due_date"]),
        terms_days=int(r["terms_days"]), disputed=_b(r["disputed"]), paperless=_b(r["paperless"]),
        settled_date=_d(r["settled_date"])), "ar_invoices.csv", "invoice_id", rep)
    bad_ref = sum(1 for r in ar if r.entity_id not in ent_ids or r.customer_id not in cust_ids)
    if bad_ref:
        rep.checks.append(Check("ar_invoices.csv", "ERROR", "referential_integrity",
                                f"{bad_ref} rows reference unknown entity/customer"))
    if n := sum(1 for r in ar if r.due_date < r.issue_date or r.amount <= 0):
        rep.checks.append(Check("ar_invoices.csv", "ERROR", "business_rules", f"{n} rows with due<issue or amount<=0"))
    if as_of and (n := sum(1 for r in ar if r.settled_date and r.settled_date > as_of)):
        rep.checks.append(Check("ar_invoices.csv", "ERROR", "future_settlement", f"{n} rows settled after as-of"))
    if (n := sum(1 for r in ar if r.settled_date)) < 500:
        rep.checks.append(Check("ar_invoices.csv", "WARN", "history_depth",
                                f"only {n} settled invoices — payment-delay model needs >= 500 for reliable training"))

    ap = _parse(_read(src / "ap_invoices.csv", rep), lambda r: Payable(
        bill_id=r["bill_id"], entity_id=r["entity_id"], supplier_id=r["supplier_id"], currency=r["currency"],
        amount=float(r["amount"]), issue_date=_req_d(r["issue_date"]), due_date=_req_d(r["due_date"]),
        discount_pct=float(r["discount_pct"] or 0), discount_days=int(r["discount_days"] or 0),
        paid_date=_d(r["paid_date"])), "ap_invoices.csv", "bill_id", rep)
    if n := sum(1 for p in ap if p.entity_id not in ent_ids or p.supplier_id not in sup_ids):
        rep.checks.append(Check("ap_invoices.csv", "ERROR", "referential_integrity", f"{n} rows reference unknown entity/supplier"))
    ap_open = [p for p in ap if p.paid_date is None]

    rec = _parse(_read(src / "recurring_flows.csv", rep), lambda r: RecurringFlow(
        entity_id=r["entity_id"], category=r["category"], currency=r["currency"], amount=float(r["amount"]),
        cadence=r["cadence"], anchor=_req_d(r["anchor"])), "recurring_flows.csv", None, rep)
    if n := sum(1 for f in rec if f.cadence not in {"WEEKLY", "BIWEEKLY", "MONTHLY", "QUARTERLY"}):
        rep.checks.append(Check("recurring_flows.csv", "ERROR", "cadence", f"{n} rows with unknown cadence"))

    fac = None
    fpath = src / "facility.json"
    if fpath.exists():
        try:
            fd = json.loads(fpath.read_text(encoding="utf-8"))
            fac = Facility(**{k: v for k, v in fd.items() if k in Facility.model_fields})
        except Exception as e:  # noqa: BLE001
            rep.checks.append(Check("facility.json", "ERROR", "schema", str(e)))
    else:
        rep.checks.append(Check("facility.json", "ERROR", "file_present", "facility.json missing"))

    # ---- optional: debt stack, covenants, bank counterparties
    debts = _parse(_read(src / "debt_facilities.csv", rep), lambda r: DebtFacility(
        **cast(dict[str, Any], {k: (v if v != "" else None) for k, v in r.items() if k in DebtFacility.model_fields and v != ""})),
        "debt_facilities.csv", "facility_id", rep) if (src / "debt_facilities.csv").exists() else []
    covs = _parse(_read(src / "covenants.csv", rep), lambda r: Covenant(**{k: v for k, v in r.items() if v != ""}),
                  "covenants.csv", "covenant_id", rep) if (src / "covenants.csv").exists() else []
    banks = _parse(_read(src / "banks.csv", rep), lambda r: Bank(**r), "banks.csv", "bank_id", rep) \
        if (src / "banks.csv").exists() else []
    fac_ids = {d.facility_id for d in debts} | ({fac.facility_id} if fac else set())
    if n := sum(1 for c in covs if c.facility_id not in fac_ids):
        rep.checks.append(Check("covenants.csv", "ERROR", "referential_integrity", f"{n} covenants reference unknown facility"))
    if n := sum(1 for c in covs if c.direction not in {">=", "<="} or c.metric not in
                {"min_liquidity", "max_net_leverage", "min_interest_cover"}):
        rep.checks.append(Check("covenants.csv", "ERROR", "business_rules", f"{n} covenants with unknown metric/direction"))
    if n := sum(1 for d in debts if d.drawn_usd > d.limit_usd + 1):
        rep.checks.append(Check("debt_facilities.csv", "ERROR", "business_rules", f"{n} facilities drawn above limit"))

    rep.counts = {**rep.counts, "entities": len(ents), "bank_accounts": len(accounts), "customers": len(custs),
                  "suppliers": len(sups), "ar_invoices": len(ar), "ar_open": sum(r.settled_date is None for r in ar),
                  "ap_open": len(ap_open), "ap_paid_history": len(ap) - len(ap_open), "recurring_flows": len(rec),
                  "statements": len(stmts), "bank_transactions": len(txns), "debt_facilities": len(debts),
                  "covenants": len(covs), "banks": len(banks)}
    rep.sources = [
        _source("company.entities", "Legal entity master", src / "entities.csv", rep, len(ents), system, as_of),
        _source("company.bank_balances", "Bank closing balances (camt.053)", sdir, rep, len(accounts),
                "Bank statements via SWIFT / EBICS / host-to-host", latest_stmt, sla=48),
        _source("company.receivables", "Accounts receivable (open + settled history)", src / "ar_invoices.csv", rep,
                len(ar), system, as_of, sla=48),
        _source("company.payables", "Accounts payable (open book + payment history)", src / "ap_invoices.csv", rep,
                len(ap), system, as_of, sla=48),
        _source("company.counterparties", "Customer master", src / "customers.csv", rep, len(custs), system, as_of),
        _source("company.recurring", "Recurring flow schedules", src / "recurring_flows.csv", rep, len(rec), system, as_of),
    ]
    if txns:
        rep.sources.append(_source("company.bank_transactions", "Bank-statement entries (camt.053 Ntry / BAI2 16)", sdir,
                                   rep, len(txns), "Bank statements via SWIFT / EBICS / host-to-host", latest_stmt, sla=48))
    if debts:
        rep.sources.append(_source("company.debt_facilities", "Debt facilities", src / "debt_facilities.csv", rep,
                                   len(debts), "Treasury management system / facility agreements", as_of))
    if covs:
        rep.sources.append(_source("company.covenants", "Financial covenants", src / "covenants.csv", rep, len(covs),
                                   "Facility agreements (compliance certificate)", as_of))
    if fpath.exists():
        rep.sources.append(_source("company.facility", "Committed facilities & covenants", fpath, rep, 1,
                                   "Facility agreement (treasury)", as_of))
    if rep.errors or not as_of or fac is None:
        return None, rep
    g = GroupData(as_of=as_of, entities=ents, accounts=accounts, customers=custs, suppliers=sups, receivables=ar,
                  payables=ap, recurring=rec, facility=fac, risk_events=[], classification=rep.classification,
                  tenant=rep.tenant, debt_facilities=debts, covenants=covs, banks=banks,
                  bank_transactions=sorted(txns, key=lambda t: (t.booking_date, t.txn_id)))
    from meridian.domain.models import Organization

    g.organization = Organization(org_id=man.get("org_id", "ORG-CLIENT"), name=man.get("organization", rep.tenant),
                                  classification=rep.classification)
    return g, rep


def ingest_directory(src: Path, store) -> IngestionReport:
    """Validate the whole package; load atomically only if there are no ERRORs."""
    g, rep = validate_and_build(Path(src))
    if g is not None:
        store.seed(g)
        for s in rep.sources:
            store.put("data_sources", s.model_dump(mode="json"))
        rep.loaded = True
    store.put("ingestions", rep.to_dict())
    return rep


# ------------------------------------------------------------------------------------------- sample export
def export_package(g: GroupData, out: Path, classification: str = "SYNTHETIC_SAMPLE",
                   extract_system: str = "Meridian simulator (synthetic sample)") -> Path:
    """Write a GroupData into the production ingestion format (used to exercise the real ingestion path
    end-to-end without client data — the manifest marks it SYNTHETIC_SAMPLE so it can never pose as real)."""
    out = Path(out)
    (out / "bank_statements").mkdir(parents=True, exist_ok=True)

    def w(name: str, rows: list[dict]) -> None:
        with (out / name).open("w", newline="", encoding="utf-8") as f:
            wr = csv.DictWriter(f, fieldnames=SPEC[name])
            wr.writeheader()
            for r in rows:
                wr.writerow({k: ("" if r.get(k) is None else r.get(k)) for k in SPEC[name]})

    (out / "manifest.json").write_text(json.dumps({
        "tenant": g.tenant, "data_classification": classification, "extract_system": extract_system,
        "as_of": str(g.as_of), "extracted_at": now_utc().isoformat()}, indent=2), encoding="utf-8")
    w("entities.csv", [e.model_dump() for e in g.entities])
    w("bank_accounts.csv", [a.model_dump() for a in g.accounts])
    w("customers.csv", [c.model_dump() for c in g.customers])
    w("suppliers.csv", [s.model_dump() for s in g.suppliers])
    w("ar_invoices.csv", [r.model_dump(mode="json") for r in g.receivables])
    w("ap_invoices.csv", [p.model_dump(mode="json") for p in g.payables])
    w("recurring_flows.csv", [f.model_dump(mode="json") for f in g.recurring])
    if g.debt_facilities:
        w("debt_facilities.csv", [d.model_dump(mode="json") for d in g.debt_facilities])
    if g.covenants:
        w("covenants.csv", [c.model_dump(mode="json") for c in g.covenants])
    if g.banks:
        w("banks.csv", [b.model_dump(mode="json") for b in g.banks])
    by_acct: dict[str, list[dict]] = {}
    for t in g.bank_transactions:
        by_acct.setdefault(t.account_id, []).append({"amount": t.amount, "booking_date": t.booking_date,
                                                     "category": t.category, "reference": t.reference,
                                                     "counterparty_id": t.counterparty_id})
    (out / "facility.json").write_text(json.dumps(g.rcf().model_dump(), indent=2), encoding="utf-8")
    owners = {e.entity_id: e.name for e in g.entities}
    for a in g.accounts:
        (out / "bank_statements" / f"{a.account_id}.xml").write_bytes(build_camt053(
            f"STMT-{a.account_id}-{g.as_of:%Y%m%d}", a.iban, a.currency, a.balance, a.balance,
            g.as_of - timedelta(days=1), owners[a.entity_id], entries=by_acct.get(a.account_id)))
    return out
