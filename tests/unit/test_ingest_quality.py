"""Bank-statement reconciliation and duplicate detection (v3.3)."""

import shutil
from datetime import date

from meridian.data.generator import generate
from meridian.ingest.camt053 import build_camt053, parse_camt053
from meridian.ingest.pipeline import export_package, ingest_directory
from meridian.ingest.quality import dedupe_transactions, reconcile
from meridian.store.base import MemoryStore

D = date(2026, 3, 31)


def _stmt(opening, closing, entries):
    return parse_camt053(build_camt053("S1", "DE89370400440532013000", "EUR", closing, opening, D, "Owner", entries))[0]


def test_end_of_day_statement_reconciles_to_the_cent():
    s = _stmt(1000.00, 1250.50, [{"amount": 300.50, "booking_date": D, "reference": "R1"},
                                 {"amount": -50.00, "booking_date": D, "reference": "R2"}])
    assert s["opening_balance"] == 1000.00
    assert reconcile(s) == {"status": "RECONCILED", "opening": 1000.0, "entries_total": 250.5, "closing": 1250.5,
                            "difference": 0.0}


def test_break_is_detected_with_the_exact_difference():
    s = _stmt(1000.00, 1250.00, [{"amount": 300.50, "booking_date": D, "reference": "R1"}])
    r = reconcile(s)
    assert r["status"] == "BREAK" and r["difference"] == 50.5


def test_history_extract_is_not_checkable_not_passed():
    s = _stmt(500.0, 500.0, [{"amount": 10.0, "booking_date": D, "reference": "A"},
                             {"amount": -10.0, "booking_date": date(2026, 3, 1), "reference": "B"}])
    assert reconcile(s)["status"] == "NOT_CHECKABLE"


def test_duplicate_entries_are_dropped_once():
    s = _stmt(0, 0, [{"amount": 10.0, "booking_date": D, "reference": "R1"},
                     {"amount": 10.0, "booking_date": D, "reference": "R1"},
                     {"amount": 10.0, "booking_date": D, "reference": "R2"}])
    keep, dups = dedupe_transactions(s)
    assert len(keep) == 2 and len(dups) == 1


def test_pipeline_flags_duplicate_statement_files_and_reports_reconciliation(tmp_path):
    pkg = export_package(generate(seed=7), tmp_path / "pkg")
    sd = pkg / "bank_statements"
    first = sorted(sd.glob("*.xml"))[0]
    shutil.copy(first, sd / ("zz_copy_of_" + first.name))            # same statement delivered twice
    rep = ingest_directory(pkg, MemoryStore())
    rules = [c.rule for c in rep.checks]
    assert "duplicate_statement" in rules and rep.loaded
    c = rep.counts
    assert c["statements_reconciled"] + c["statements_break"] + c["statements_not_checkable"] == c["bank_accounts"]


def test_hostile_xml_is_refused():
    """v3.5: bank files are untrusted input. Entity-expansion ('billion laughs') and external-entity (XXE) payloads
    are refused by the parser instead of being expanded or fetched."""
    import pytest
    bomb = (b'<?xml version="1.0"?><!DOCTYPE l [<!ENTITY a "aaaaaaaaaa"><!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;">]>'
            b"<Document>&b;</Document>")
    xxe = (b'<?xml version="1.0"?><!DOCTYPE d [<!ENTITY x SYSTEM "file:///etc/passwd">]><Document>&x;</Document>')
    for payload in (bomb, xxe):
        with pytest.raises(ValueError):           # defusedxml errors subclass ValueError
            parse_camt053(payload)
