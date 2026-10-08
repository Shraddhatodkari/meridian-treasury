"""ISO 20022 pain.001 (Customer Credit Transfer Initiation) DRAFT generation.

Approved actions are rendered as a pain.001.001.09 XML draft for upload to the bank portal /
TMS by an authorised human. Nothing is transmitted to any bank (TP-7.1: AI never executes payments).
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from xml.etree.ElementTree import Element, SubElement, tostring

NS = "urn:iso:std:iso:20022:tech:xsd:pain.001.001.09"


def build_pain001(msg_id: str, debtor_name: str, transfers: list[dict], execution_date: date) -> str:
    root = Element("Document", xmlns=NS)
    cct = SubElement(root, "CstmrCdtTrfInitn")
    hdr = SubElement(cct, "GrpHdr")
    SubElement(hdr, "MsgId").text = msg_id
    SubElement(hdr, "CreDtTm").text = datetime.now(UTC).replace(microsecond=0).isoformat()
    SubElement(hdr, "NbOfTxs").text = str(len(transfers))
    SubElement(hdr, "CtrlSum").text = f"{sum(t['amount'] for t in transfers):.2f}"
    SubElement(SubElement(hdr, "InitgPty"), "Nm").text = debtor_name
    for i, t in enumerate(transfers, 1):
        pmt = SubElement(cct, "PmtInf")
        SubElement(pmt, "PmtInfId").text = f"{msg_id}-{i}"
        SubElement(pmt, "PmtMtd").text = "TRF"
        SubElement(SubElement(pmt, "ReqdExctnDt"), "Dt").text = execution_date.isoformat()
        SubElement(SubElement(pmt, "Dbtr"), "Nm").text = t["debtor"]
        SubElement(SubElement(SubElement(pmt, "DbtrAcct"), "Id"), "IBAN").text = t["debtor_iban"]
        tx = SubElement(pmt, "CdtTrfTxInf")
        SubElement(SubElement(tx, "PmtId"), "EndToEndId").text = t["end_to_end_id"]
        amt = SubElement(SubElement(tx, "Amt"), "InstdAmt", Ccy=t["currency"])
        amt.text = f"{t['amount']:.2f}"
        SubElement(SubElement(tx, "Cdtr"), "Nm").text = t["creditor"]
        SubElement(SubElement(SubElement(tx, "CdtrAcct"), "Id"), "IBAN").text = t["creditor_iban"]
        SubElement(SubElement(tx, "RmtInf"), "Ustrd").text = t["remittance"][:140]
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + tostring(root, encoding="unicode")
