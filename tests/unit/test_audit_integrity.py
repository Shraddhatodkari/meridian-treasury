"""Audit ledger integrity (v3.2): every form of tampering is detected; anchors detect a full rewrite."""

import pytest

from meridian.governance.audit import AuditLedger, canonical
from meridian.store.base import MemoryStore


@pytest.fixture
def led():
    led = AuditLedger(MemoryStore())
    for i in range(5):
        led.record("tom", "action_approve", {"amount_usd": 1000 * i, "action_id": f"A{i}"}, "RUN-1")
    assert led.verify()["valid"]
    return led


def test_canonical_serialisation_is_order_independent():
    assert canonical({"b": 1, "a": "é"}) == canonical({"a": "é", "b": 1}) == '{"a":"é","b":1}'


def test_edit_of_payload_actor_or_timestamp_is_detected(led):
    rows = led.store._audit
    for field, value in (("payload", '{"amount_usd":9999999}'), ("actor", "mallory"), ("ts", "2020-01-01T00:00:00")):
        orig = rows[2][field]
        rows[2][field] = value
        v = led.verify()
        assert not v["valid"] and v["broken_at_seq"] == 3 and v["reason"] == "record content altered"
        rows[2][field] = orig
    assert led.verify()["valid"]


def test_deleting_a_record_is_detected(led):
    del led.store._audit[1]
    v = led.verify()
    assert not v["valid"] and v["reason"] == "sequence gap or reorder"


def test_reordering_records_is_detected(led):
    a = led.store._audit
    a[1], a[2] = a[2], a[1]
    assert not led.verify()["valid"]


def test_renumbering_after_deletion_is_still_detected(led):
    a = led.store._audit
    del a[1]
    for i, r in enumerate(a, start=1):
        r["seq"] = i                                   # attacker also fixes the sequence numbers
    v = led.verify()
    assert not v["valid"] and v["reason"] in {"link to previous record broken", "record content altered"}


def test_external_anchor_detects_a_full_rewrite(led):
    anchor = led.anchor()
    assert anchor["valid"] and led.verify_anchor(anchor["head"], anchor["seq"])
    forged = AuditLedger(MemoryStore())                # a perfectly consistent but rewritten history
    for i in range(5):
        forged.record("tom", "action_approve", {"amount_usd": 1, "action_id": f"A{i}"}, "RUN-1")
    assert forged.verify()["valid"] and not forged.verify_anchor(anchor["head"], anchor["seq"])


def test_v1_records_without_seq_in_digest_still_verify():
    import hashlib
    st = MemoryStore()
    body = canonical({"x": 1})
    h = hashlib.sha256("|".join(["0" * 64, "t", "", "a", "act", body]).encode()).hexdigest()
    st.append_audit({"ts": "t", "run_id": None, "actor": "a", "action": "act", "payload": body,
                     "prev_hash": "0" * 64, "hash": h}, expected_seq=1)
    led = AuditLedger(st)
    led.record("b", "act2", {"y": 2})
    assert led.verify()["valid"]


def test_store_exposes_no_update_or_delete_for_audit():
    from meridian.store.base import TreasuryStore
    names = {n for n in dir(TreasuryStore) if "audit" in n}
    assert names == {"append_audit", "last_audit", "audit_records", "tamper_audit_for_test"}
