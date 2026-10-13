"""Guardrails: injection scanning, DLP masking, and numeric grounding of AI-written briefings."""

from __future__ import annotations

import re
from typing import Any

from meridian.governance.policy import load_policy

# Amounts like $61.4M, $1.25 million, $5,000,000 or $250bn. Thousands separators must be well-formed so a trailing
# comma ("$5,000,000, multiples of ...") is not swallowed, and a unit must be a whole word ("multiples" is not "M").


def scan_text(text: str) -> list[str]:
    pats = load_policy().guardrails.injection_patterns
    return [p for p in pats if re.search(p, text, re.IGNORECASE)]


def mask(obj: Any, key: str | None = None) -> Any:
    """DLP: mask bank identifiers / emails before anything leaves the trust boundary (TP-7.1)."""
    pol = load_policy().dlp
    if isinstance(obj, dict):
        return {k: mask(v, k) for k, v in obj.items()}
    if isinstance(obj, list):
        return [mask(v, key) for v in obj]
    if isinstance(obj, str):
        if key in pol.mask_fields:
            return obj[:4] + "•" * 6 + obj[-4:]
        out = obj
        for name, pat in pol.mask_patterns.items():
            def _masked(_m: re.Match[str], n: str = name) -> str:
                return f"[{n.upper()}-MASKED]"

            out = re.sub(pat, _masked, out)
        return out
    return obj


_SIGNED_MONEY = re.compile(
    r"(?P<sign>[-\u2212])?\s?(?P<cur>[$\u20ac\u00a3])\s?(?P<sign2>-)?(?P<num>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)"
    r"(?:\s?(?P<unit>billion|million|thousand|bn|M|B|K)\b)?", re.I)
_MULTIPLE = re.compile(r"(?<![\w.])(?P<sign>[-\u2212])?(?P<num>\d+(?:\.\d+)?)x\b")
_PERCENT = re.compile(r"(?<![\w.])(?P<sign>[-\u2212+])?(?P<num>\d+(?:\.\d+)?)\s?%")
_UNIT = {"": 1.0, "k": 1e3, "thousand": 1e3, "m": 1e6, "million": 1e6, "b": 1e9, "bn": 1e9, "billion": 1e9}
_CURRENCY = {"$": "USD", "\u20ac": "EUR", "\u00a3": "GBP"}


def _half_unit(num: str, scale: float) -> float:
    """Half of the last displayed digit: '$12.63B' is exact to ±$5M, '$126.3M' to ±$50K, '$5,000,000' to ±$0.5."""
    dec = len(num.split(".")[1]) if "." in num else 0
    return 0.5 * (10 ** -dec) * scale


def extract_claims(text: str) -> list[dict]:
    """Every material figure in a text, with its displayed precision. Money carries currency and an explicit sign when
    written ('-$22.04B', '\u2212$1.02B'); multiples ('1.55x') and percentages ('4.5%') are extracted too."""
    out = []
    for m in _SIGNED_MONEY.finditer(text):
        num, unit = m.group("num"), (m.group("unit") or "").lower()
        scale = _UNIT.get(unit, 1.0)
        try:
            v = float(num.replace(",", "")) * scale
        except ValueError:
            continue
        out.append({"kind": "money", "text": m.group(0).strip(), "value": -v if (m.group("sign") or m.group("sign2")) else v,
                    "signed": bool(m.group("sign") or m.group("sign2")), "currency": _CURRENCY.get(m.group("cur"), "USD"),
                    "tolerance": _half_unit(num.replace(",", ""), scale)})
    for kind, rx in (("multiple", _MULTIPLE), ("percent", _PERCENT)):
        for m in rx.finditer(text):
            v = float(m.group("num"))
            neg = m.group("sign") in ("-", "\u2212")
            out.append({"kind": kind, "text": m.group(0).strip(), "value": -v if neg else v, "signed": neg,
                        "currency": None, "tolerance": _half_unit(m.group("num"), 1.0)})
    return out


def extract_money(text: str) -> list[float]:
    """Backward-compatible: magnitudes of money amounts in display order."""
    return [abs(c["value"]) for c in extract_claims(text) if c["kind"] == "money"]


def _numbers(obj: Any) -> list[float]:
    if isinstance(obj, dict):
        return [x for v in obj.values() for x in _numbers(v)]
    if isinstance(obj, list):
        return [x for v in obj for x in _numbers(v)]
    if isinstance(obj, (int, float)) and not isinstance(obj, bool):
        return [float(obj)]
    return []


def _currencies(obj: Any) -> set[str]:
    found: set[str] = set()
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in ("currency", "unit", "ccy") and isinstance(v, str) and len(v) == 3 and v.isupper():
                found.add(v)
            found |= _currencies(v)
    elif isinstance(obj, list):
        for v in obj:
            found |= _currencies(v)
    return found


def _match(c: dict, facts: list[float]) -> bool:
    eps = 1e-9 * max(1.0, abs(c["value"]))
    for f in facts:
        cands = [f]
        if c["kind"] == "percent":                      # 4.5% may be stored as 0.045 (ratio) or 4.5 (pct)
            cands = [f, f * 100]
        for x in cands:
            if c["signed"] or c["kind"] != "money":
                ok = abs(c["value"] - x) <= c["tolerance"] + eps
                if not ok and not c["signed"] and c["kind"] != "money":
                    ok = abs(c["value"] - abs(x)) <= c["tolerance"] + eps
            else:                                       # unsigned money is a magnitude statement
                ok = abs(abs(c["value"]) - abs(x)) <= c["tolerance"] + eps
            if ok:
                return True
    return False


def numeric_grounding(text: str, evidence: Any, kinds: tuple[str, ...] = ("money", "multiple")) -> dict:
    """Every material figure an agent writes must equal a tool-produced number AT THE PRECISION DISPLAYED.

    * '$12.63B' must match a fact within ±$5M (half of the last digit shown), not within a blanket percentage;
    * an explicit sign must agree ('-$22.04B' never matches +22.04B); an unsigned amount is checked as a magnitude;
    * a non-USD currency is grounded only if the evidence carries that currency;
    * multiples ('1.55x') are checked the same way; percentages are reported but checked only when requested,
      because many are derived ratios.
    Arithmetic is never delegated to the model: the deterministic tools compute, the model may only quote."""
    facts = _numbers(evidence)
    ccy = _currencies(evidence) | {"USD"}
    claims = [c for c in extract_claims(text) if c["kind"] in kinds and not (c["kind"] == "money" and abs(c["value"]) < 1)]
    ungrounded, detail = [], []
    for c in claims:
        ok = (c["kind"] != "money" or c["currency"] in ccy) and _match(c, facts)
        detail.append({"text": c["text"], "kind": c["kind"], "grounded": ok, "tolerance": c["tolerance"]})
        if not ok:
            ungrounded.append(abs(c["value"]) if c["kind"] == "money" and not c["signed"] else c["value"])
    return {"claims": len(claims), "ungrounded": ungrounded, "grounded": not ungrounded, "detail": detail,
            "method": "display-precision match (half of the last displayed digit), sign- and currency-aware"}
