"""ISO 20022 camt.053 (BankToCustomerStatement) — read closing balances, write sample statements.

Banks deliver end-of-day statements in camt.053 (successor to MT940). Meridian uses the closing booked
balance (CLBD) per account as the authoritative opening cash for the forecast.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, cast
from xml.etree.ElementTree import Element, SubElement, tostring

from defusedxml.ElementTree import fromstring as _safe_fromstring  # rejects entity expansion / XXE / DTDs

NS = "urn:iso:std:iso:20022:tech:xsd:camt.053.001.08"


def _strip(tag: str) -> str:
    return tag.split("}", 1)[-1]


def parse_camt053(xml_bytes: bytes) -> list[dict]:
    """Return one record per statement: iban, currency, closing booked balance, statement date, entry count."""
    root = _safe_fromstring(xml_bytes)
    out: list[dict[str, Any]] = []
    for stmt in root.iter():
        if _strip(stmt.tag) != "Stmt":
            continue
        rec: dict[str, Any] = {"statement_id": None, "iban": None, "currency": None, "closing_balance": None,
                               "opening_balance": None, "balance_date": None, "entries": 0, "created": None}
        for el in stmt:
            t = _strip(el.tag)
            if t == "Id":
                rec["statement_id"] = el.text
            elif t == "CreDtTm":
                rec["created"] = el.text
            elif t == "Acct":
                for a in el.iter():
                    if _strip(a.tag) == "IBAN":
                        rec["iban"] = a.text
                    elif _strip(a.tag) == "Ccy":
                        rec["currency"] = a.text
            elif t == "Bal":
                code = next((c.text for c in el.iter() if _strip(c.tag) == "Cd"), None)
                if code not in ("CLBD", "OPBD"):
                    continue
                amt = next(c for c in el.iter() if _strip(c.tag) == "Amt")
                sign = next((c.text for c in el.iter() if _strip(c.tag) == "CdtDbtInd"), "CRDT")
                if code == "OPBD":
                    rec["opening_balance"] = float(cast(str, amt.text)) * (-1 if sign == "DBIT" else 1)
                    continue
                rec["closing_balance"] = float(cast(str, amt.text)) * (-1 if sign == "DBIT" else 1)
                rec["currency"] = rec["currency"] or amt.get("Ccy")
                d = next((c.text for c in el.iter() if _strip(c.tag) == "Dt" and c.text), None)
                rec["balance_date"] = date.fromisoformat(d[:10]) if d else None
            elif t == "Ntry":
                rec["entries"] += 1
                amt_el = next((c for c in el if _strip(c.tag) == "Amt"), None)
                sign = next((c.text for c in el if _strip(c.tag) == "CdtDbtInd"), "CRDT")
                bdt = next((c.text for c in el.iter() if _strip(c.tag) in {"Dt"} and c.text), None)
                info = next((c.text for c in el if _strip(c.tag) == "AddtlNtryInf"), None)
                ref = next((c.text for c in el if _strip(c.tag) == "NtryRef"), None)
                if amt_el is not None and amt_el.text:
                    rec.setdefault("transactions", []).append({
                        "amount": float(amt_el.text) * (-1 if sign == "DBIT" else 1),
                        "booking_date": date.fromisoformat(bdt[:10]) if bdt else None,
                        "category": (info or "UNCLASSIFIED").split("|")[0], "reference": ref,
                        "counterparty_id": (info or "|").split("|")[1] if info and "|" in info else None})
        out.append(rec)
    return out


def build_camt053(statement_id: str, iban: str, currency: str, closing: float, opening: float,
                  balance_date: date, owner: str, entries: list[dict] | None = None) -> bytes:
    doc = Element("Document", xmlns=NS)
    s = SubElement(SubElement(doc, "BkToCstmrStmt"), "Stmt")
    SubElement(s, "Id").text = statement_id
    SubElement(s, "CreDtTm").text = datetime(balance_date.year, balance_date.month, balance_date.day, 22).isoformat()
    acct = SubElement(s, "Acct")
    SubElement(SubElement(acct, "Id"), "IBAN").text = iban
    SubElement(acct, "Ccy").text = currency
    SubElement(SubElement(acct, "Ownr"), "Nm").text = owner
    for code, val in (("OPBD", opening), ("CLBD", closing)):
        bal = SubElement(s, "Bal")
        SubElement(SubElement(SubElement(bal, "Tp"), "CdOrPrtry"), "Cd").text = code
        a = SubElement(bal, "Amt", Ccy=currency)
        a.text = f"{abs(val):.2f}"
        SubElement(bal, "CdtDbtInd").text = "CRDT" if val >= 0 else "DBIT"
        SubElement(SubElement(bal, "Dt"), "Dt").text = balance_date.isoformat()
    for e in entries or []:
        n = SubElement(s, "Ntry")
        if e.get("reference"):
            SubElement(n, "NtryRef").text = e["reference"]
        a = SubElement(n, "Amt", Ccy=currency)
        a.text = f"{abs(e['amount']):.2f}"
        SubElement(n, "CdtDbtInd").text = "CRDT" if e["amount"] >= 0 else "DBIT"
        SubElement(n, "Sts").text = "BOOK"
        SubElement(SubElement(n, "BookgDt"), "Dt").text = e["booking_date"].isoformat()
        SubElement(n, "AddtlNtryInf").text = f"{e.get('category', 'UNCLASSIFIED')}|{e.get('counterparty_id') or ''}"
    return b'<?xml version="1.0" encoding="UTF-8"?>\n' + tostring(doc)
