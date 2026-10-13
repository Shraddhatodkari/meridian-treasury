"""Outbound data-loss prevention: redact sensitive values BEFORE anything is sent to an external model provider.

Three layers, applied to every string in the payload (numbers are left untouched, so financial evidence survives):

1. Structured rules: a value under a sensitive key (``iban``, ``account_number``, ``tax_id``, ``password``, ...) is
   redacted whatever it looks like.
2. Validated patterns: IBANs must pass the ISO 13616 mod-97 check and card numbers the Luhn check, which keeps false
   positives (SEC accession numbers, CIKs, invoice ids) out.
3. Credential patterns: API keys, bearer tokens, JWTs, private-key blocks, ``password=...`` assignments.

Limitations (stated, not hidden): free-text personal names and addresses are not detected (no NER model is shipped);
national identifiers other than US SSN/EIN are not recognised; detection is pattern-based, so novel formats can pass.
The model is also never sent raw bank details by design — agents only receive tool outputs that are masked upstream.
"""

from __future__ import annotations

import re
from typing import Any

SENSITIVE_KEYS = {
    "iban", "account_number", "account_no", "acct", "bank_account", "routing_number", "aba", "sort_code",
    "tax_id", "tin", "ssn", "ein", "vat_id", "national_id", "passport",
    "password", "passwd", "secret", "api_key", "apikey", "token", "access_token", "refresh_token", "private_key",
    "client_secret", "authorization",
}

_IBAN = re.compile(r"\b[A-Z]{2}\d{2}(?:[ ]?[A-Z0-9]){11,30}\b")
_CARD = re.compile(r"\b(?:\d{4}[ -]){3}\d{1,7}\b|\b\d{13,19}\b")   # 4-4-4-n groups or one run (not 10-2-6 accessions)
_SSN = re.compile(r"\b(?!000|666|9\d\d)\d{3}-(?!00)\d{2}-(?!0000)\d{4}\b")
_EIN = re.compile(r"\b\d{2}-\d{7}\b")
_EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")
_SECRETS = [
    ("anthropic_key", re.compile(r"\bsk-an[t]-[A-Za-z0-9_\-]{8,}")),
    ("api_key", re.compile(r"\bsk-[A-Za-z0-9]{20,}")),
    ("aws_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("github_token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}")),
    ("bearer", re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]{16,}")),
    ("private_key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----")),
    ("credential", re.compile(r"(?i)\b(password|passwd|secret|api[_-]?key)\s*[:=]\s*\S+")),
]


def _iban_ok(s: str) -> bool:
    s = s.replace(" ", "")
    if not 15 <= len(s) <= 34:
        return False
    r = s[4:] + s[:4]
    try:
        return int("".join(str(int(c, 36)) for c in r)) % 97 == 1
    except ValueError:
        return False


def _luhn_ok(s: str) -> bool:
    d = [int(c) for c in s if c.isdigit()]
    if not 13 <= len(d) <= 19 or len(set(d)) == 1:
        return False
    total = 0
    for i, x in enumerate(reversed(d)):
        if i % 2:
            x *= 2
            x -= 9 if x > 9 else 0
        total += x
    return total % 10 == 0


def redact_text(text: str, counts: dict[str, int] | None = None) -> str:
    counts = counts if counts is not None else {}

    def sub(kind: str, rx: re.Pattern, s: str, check=None) -> str:
        def rep(m: re.Match) -> str:
            if check and not check(m.group(0)):
                return m.group(0)
            counts[kind] = counts.get(kind, 0) + 1
            return f"[REDACTED:{kind.upper()}]"
        return rx.sub(rep, s)

    for kind, rx in _SECRETS:
        text = sub(kind, rx, text)
    text = sub("iban", _IBAN, text, _iban_ok)
    text = sub("card", _CARD, text, _luhn_ok)
    text = sub("ssn", _SSN, text)
    text = sub("ein", _EIN, text)
    text = sub("email", _EMAIL, text)
    return text


def redact(obj: Any, key: str | None = None, counts: dict[str, int] | None = None) -> Any:
    """Return a redacted deep copy. Strings under sensitive keys are fully redacted; other strings are scanned."""
    counts = counts if counts is not None else {}
    if isinstance(obj, dict):
        return {k: redact(v, str(k).lower(), counts) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [redact(v, key, counts) for v in obj]
    if isinstance(obj, str):
        if key and key in SENSITIVE_KEYS:
            counts["field:" + key] = counts.get("field:" + key, 0) + 1
            return f"[REDACTED:{key.upper()}]"
        return redact_text(obj, counts)
    return obj


def redact_with_report(obj: Any) -> tuple[Any, dict[str, int]]:
    counts: dict[str, int] = {}
    return redact(obj, None, counts), counts
