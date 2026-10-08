"""Guardrails: injection scanning, DLP masking, and numeric grounding of AI-written briefings."""

from __future__ import annotations

import re
from typing import Any

from meridian.governance.policy import load_policy

# Amounts like $61.4M, $1.25 million, $5,000,000 or $250bn. Thousands separators must be well-formed so a trailing
# comma ("$5,000,000, multiples of ...") is not swallowed, and a unit must be a whole word ("multiples" is not "M").
_MONEY = re.compile(r"\$\s?(-?\d{1,3}(?:,\d{3})+(?:\.\d+)?|-?\d+(?:\.\d+)?)(?:\s?(billion|million|bn|M|B|K)\b)?", re.I)
_MULT = {"m": 1e6, "million": 1e6, "b": 1e9, "bn": 1e9, "billion": 1e9, "k": 1e3}


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
            out = re.sub(pat, lambda m, n=name: f"[{n.upper()}-MASKED]", out)
        return out
    return obj


def extract_money(text: str) -> list[float]:
    vals = []
    for m in _MONEY.finditer(text):
        try:
            v = float(m.group(1).replace(",", ""))
        except ValueError:
            continue
        vals.append(v * _MULT.get((m.group(2) or "").lower(), 1.0))
    return vals


def _numbers(obj: Any) -> list[float]:
    if isinstance(obj, dict):
        return [x for v in obj.values() for x in _numbers(v)]
    if isinstance(obj, list):
        return [x for v in obj for x in _numbers(v)]
    if isinstance(obj, (int, float)) and not isinstance(obj, bool):
        return [float(obj)]
    return []


def numeric_grounding(text: str, evidence: Any) -> dict:
    """Every $ amount in an AI briefing must match a number produced by the tools (within tolerance).
    LLMs are good at narrative and bad at arithmetic; this catches invented or mis-copied figures."""
    tol = load_policy().guardrails.numeric_grounding_tolerance
    facts = [abs(x) for x in _numbers(evidence) if abs(x) >= 1]
    claims = extract_money(text)
    ungrounded = []
    for c in claims:
        if abs(c) < 1:
            continue
        if not any(abs(abs(c) - f) <= tol * max(f, 1.0) for f in facts):
            ungrounded.append(c)
    return {"claims": len(claims), "ungrounded": ungrounded, "grounded": not ungrounded}
