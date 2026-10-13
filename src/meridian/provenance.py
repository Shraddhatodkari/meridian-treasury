"""Data provenance: every dataset that feeds an analysis carries where it came from, when, and whether it passed
validation. Recommendations reference these records, so any number can be traced back to its source.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, date, datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class Classification(str, Enum):
    CLIENT_DATA = "CLIENT_DATA"              # the organisation's own records (ERP, bank statements)
    PUBLIC_MARKET_DATA = "PUBLIC_MARKET_DATA"  # official public sources (ECB, NY Fed, SEC, GDACS)
    SYNTHETIC = "SYNTHETIC"                  # simulated demo data — never presented as real
    REFERENCE = "REFERENCE"                  # policies, agreements, configuration


class ValidationStatus(str, Enum):
    PASSED = "PASSED"
    PASSED_WITH_WARNINGS = "PASSED_WITH_WARNINGS"
    FAILED = "FAILED"
    NOT_VALIDATED = "NOT_VALIDATED"


class SourceRecord(BaseModel):
    source_id: str                     # stable key, e.g. "fx.ecb", "erp.receivables"
    name: str
    classification: Classification
    system: str                        # "European Central Bank", "SAP S/4HANA export", "Meridian simulator"
    uri: str | None = None
    retrieved_at: datetime
    as_of: date | None = None          # business date the data describes
    record_count: int = 0
    checksum_sha256: str | None = None
    validation: ValidationStatus = ValidationStatus.NOT_VALIDATED
    issues: list[str] = Field(default_factory=list)
    freshness_sla_hours: float | None = None
    notes: str | None = None

    def age_hours(self, now: datetime | None = None) -> float | None:
        if self.as_of is None:
            return None
        now = now or datetime.now(UTC)
        ref = datetime(self.as_of.year, self.as_of.month, self.as_of.day, tzinfo=UTC)
        return round((now - ref).total_seconds() / 3600, 1)

    def freshness(self, now: datetime | None = None, reference_date: date | None = None) -> str:
        """FRESH / STALE / N/A. In demo mode freshness is evaluated against the simulation date."""
        if self.freshness_sla_hours is None or self.as_of is None:
            return "N/A"
        if reference_date is not None:
            now = datetime(reference_date.year, reference_date.month, reference_date.day, 23, 59, tzinfo=UTC)
        age = self.age_hours(now)
        if age is not None and age < -48:
            return "FUTURE_DATED"
        return "FRESH" if age is not None and age <= self.freshness_sla_hours else "STALE"


def checksum(obj: Any) -> str:
    raw = obj if isinstance(obj, (bytes, bytearray)) else json.dumps(obj, sort_keys=True, default=str).encode()
    return hashlib.sha256(raw).hexdigest()


def now_utc() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)
