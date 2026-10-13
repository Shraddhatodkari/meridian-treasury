"""Natural-hazard feed connector (GDACS — Global Disaster Alert and Coordination System).

The demo ships an offline sample (see data/generator.py). `fetch_gdacs_rss()` maps the public
GDACS RSS feed to RiskEvent objects so the same exposure/scenario pipeline runs on live alerts.
Severity follows GDACS alert levels (GREEN / ORANGE / RED). Verify feed terms before production use.
"""

from __future__ import annotations

from datetime import date, datetime
from email.utils import parsedate_to_datetime

from defusedxml.ElementTree import fromstring as _safe_fromstring  # rejects entity expansion / XXE / DTDs

from meridian.domain.models import RiskEvent

GDACS_RSS = "https://www.gdacs.org/xml/rss.xml"
_GDACS_NS = {"gdacs": "http://www.gdacs.org"}
_HAZARD = {"TC": "TROPICAL_CYCLONE", "FL": "FLOOD", "EQ": "EARTHQUAKE", "VO": "VOLCANO", "DR": "DROUGHT",
           "WF": "WILDFIRE"}
# ISO3 -> ISO2 for the countries where the group operates
_ISO3 = {"USA": "US", "CAN": "CA", "MEX": "MX", "BRA": "BR", "GBR": "GB", "DEU": "DE", "FRA": "FR", "NLD": "NL",
         "POL": "PL", "IND": "IN", "CHN": "CN", "SGP": "SG", "JPN": "JP", "AUS": "AU", "ARE": "AE", "ZAF": "ZA"}


def parse_gdacs_rss(xml_text: str) -> list[RiskEvent]:
    root = _safe_fromstring(xml_text)
    events = []
    for item in root.iter("item"):
        iso3 = (item.findtext("gdacs:iso3", default="", namespaces=_GDACS_NS) or "").upper()
        if iso3 not in _ISO3:
            continue
        etype = item.findtext("gdacs:eventtype", default="", namespaces=_GDACS_NS)
        level = (item.findtext("gdacs:alertlevel", default="Green", namespaces=_GDACS_NS) or "Green").upper()
        pub = item.findtext("pubDate")
        try:
            start = parsedate_to_datetime(pub).date() if pub else date.today()
        except (TypeError, ValueError):
            start = datetime.now().date()
        events.append(RiskEvent(
            event_id=f"GDACS-{etype}-{item.findtext('gdacs:eventid', default='0', namespaces=_GDACS_NS)}",
            hazard=_HAZARD.get(etype, etype or "OTHER"), country=_ISO3[iso3],
            severity=level if level in {"GREEN", "ORANGE", "RED"} else "GREEN", start=start,
            expected_days={"RED": 21, "ORANGE": 10}.get(level, 3),
            description=(item.findtext("title") or "")[:200]))
    return events


def fetch_gdacs_rss(timeout: float = 10.0) -> list[RiskEvent]:  # pragma: no cover - network
    import httpx

    return parse_gdacs_rss(httpx.get(GDACS_RSS, timeout=timeout).text)
