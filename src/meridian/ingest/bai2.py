"""BAI2 (BAI Cash Management Balance Reporting Specification v2) — the North-American bank statement format.

Supported records: 01 file header, 02 group header (as-of date), 03 account identifier with summary amounts,
16 transaction detail, 88 continuation, 49/98/99 trailers. Amounts are in minor units (cents) per the spec.
Type codes used: 015 closing ledger, 010 opening ledger; detail type codes 100–399 credits, 400–699 debits.
"""

from __future__ import annotations

from datetime import date, datetime

CREDIT_CATEGORIES = {"195": "CUSTOMER_RECEIPT", "301": "CUSTOMER_RECEIPT", "275": "INTERCOMPANY", "165": "CUSTOMER_RECEIPT"}
DEBIT_CATEGORIES = {"475": "SUPPLIER_PAYMENT", "495": "SUPPLIER_PAYMENT", "575": "INTERCOMPANY", "455": "PAYROLL",
                    "577": "TAX", "698": "OPEX", "399": "OTHER"}


def _records(text: str) -> list[list[str]]:
    out: list[list[str]] = []
    for raw in text.replace("\r", "").split("\n"):
        line = raw.strip()
        if not line:
            continue
        line = line.rstrip("/")
        fields = line.split(",")
        if fields[0] == "88" and out:
            out[-1].extend(fields[1:])
        else:
            out.append(fields)
    return out


def parse_bai2(text: str) -> list[dict]:
    """Return one record per account: account number, currency, opening/closing ledger, as-of date, transactions."""
    accounts, as_of, cur = [], None, None
    for f in _records(text):
        code = f[0]
        if code == "02":
            as_of = datetime.strptime(f[4], "%y%m%d").date() if len(f) > 4 and f[4] else None
        elif code == "03":
            cur = {"account": f[1], "currency": f[2] or "USD", "opening_balance": None, "closing_balance": None,
                   "balance_date": as_of, "transactions": []}
            accounts.append(cur)
            rest = f[3:]
            for i in range(0, len(rest) - 1, 4):
                tcode, amt = rest[i], rest[i + 1]
                if not tcode or amt in ("", None):
                    continue
                val = int(amt) / 100
                if tcode == "015":
                    cur["closing_balance"] = val
                elif tcode == "010":
                    cur["opening_balance"] = val
        elif code == "16" and cur is not None:
            tcode, amt = f[1], int(f[2] or 0) / 100
            credit = 100 <= int(tcode) < 400
            cat = (CREDIT_CATEGORIES if credit else DEBIT_CATEGORIES).get(tcode, "UNCLASSIFIED")
            cur["transactions"].append({"type_code": tcode, "amount": amt if credit else -amt,
                                        "booking_date": as_of, "category": cat,
                                        "reference": f[4] if len(f) > 4 else None,
                                        "counterparty_id": f[6] if len(f) > 6 and f[6] else None})
    return accounts


def build_bai2(sender: str, receiver: str, as_of: date, accounts: list[dict]) -> str:
    """Minimal BAI2 writer used for tests and sample packages."""
    lines = [f"01,{sender},{receiver},{as_of:%y%m%d},0000,1,,,2/",
             f"02,{receiver},{sender},1,{as_of:%y%m%d},0000,,2/"]
    for a in accounts:
        lines.append(f"03,{a['account']},{a.get('currency', 'USD')},010,{round(a['opening'] * 100)},,,015,"
                     f"{round(a['closing'] * 100)},,/")
        for t in a.get("transactions", []):
            code = t.get("type_code") or ("195" if t["amount"] >= 0 else "475")
            lines.append(f"16,{code},{round(abs(t['amount']) * 100)},0,{t.get('reference', '')},,{t.get('counterparty_id', '')}/")
        lines.append(f"49,{round(a['closing'] * 100)},{2 + len(a.get('transactions', []))}/")
    lines += ["98,0,1,0/", "99,0,1,0/"]
    return "\n".join(lines) + "\n"
