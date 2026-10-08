"""Market-data service: live fetch with persisted fallback, bundled real snapshot, or none (static)."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

from meridian.config import PACKAGE_ROOT, Settings
from meridian.data.generator import GroupData
from meridian.domain.models import RiskEvent
from meridian.market import connectors as c
from meridian.market import rates as rt
from meridian.provenance import SourceRecord

log = logging.getLogger(__name__)
SNAPSHOT = PACKAGE_ROOT / "data" / "market_snapshots" / "market_snapshot.json"


@dataclass
class MarketData:
    mode: str
    fx: dict | None = None
    sofr: dict | None = None
    hazards: list[RiskEvent] = field(default_factory=list)
    peers: list[dict] = field(default_factory=list)
    ust_curve: dict | None = None
    sofr_history: list[dict] = field(default_factory=list)
    effr: dict | None = None
    estr: list[dict] = field(default_factory=list)
    credit_spread: dict | None = None
    fx_history: dict | None = None
    sources: list[SourceRecord] = field(default_factory=list)

    def get(self, name: str):
        return getattr(self, name, None)


EXTENDED = ("ust_curve", "sofr_history", "effr", "estr", "credit_spread", "fx_history")


def _from_snapshot() -> MarketData:
    s = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    srcs = []
    for k in ("fx", "sofr", "hazards", "peers", *EXTENDED):
        if k not in s:
            continue
        rec = SourceRecord(**s[k]["source"])
        rec.notes = ((rec.notes or "") + " | bundled snapshot of real public data (reproducible demo/CI)").strip(" |")
        srcs.append(rec)
    md = MarketData(mode="snapshot", fx=s["fx"]["data"], sofr=s["sofr"]["data"],
                    hazards=[RiskEvent(**e) for e in s["hazards"]["data"]], peers=s["peers"]["data"], sources=srcs)
    for k in EXTENDED:
        if k in s:
            setattr(md, k, s[k]["data"])
    return md


def _live(settings: Settings, store, currencies: set[str], countries: set[str]) -> MarketData:
    md = MarketData(mode="live")
    jobs = [("fx", lambda: c.fetch_ecb_fx(currencies)), ("sofr", c.fetch_sofr),
            ("hazards", lambda: c.fetch_gdacs(countries)), ("ust_curve", rt.fetch_ust_curve),
            ("sofr_history", rt.fetch_sofr_history), ("effr", rt.fetch_effr), ("estr", rt.fetch_estr),
            ("credit_spread", rt.fetch_credit_spread), ("fx_history", lambda: rt.fetch_fx_history(currencies))]
    if settings.sec_user_agent:
        jobs.append(("peers", lambda: c.fetch_sec_peers(settings.sec_user_agent)))
    for name, fn in jobs:
        try:
            data, src = fn()
            payload = [e.model_dump(mode="json") for e in data] if name == "hazards" else data
            store.put_blob(f"market:{name}", json.dumps({"data": payload, "source": src.model_dump(mode="json")}).encode())
        except Exception as e:  # noqa: BLE001 - network/source outage => use last persisted copy, flagged
            log.warning("market source %s unavailable: %s", name, e)
            blob = store.get_blob(f"market:{name}")
            if not blob:
                continue
            saved = json.loads(blob)
            payload, src = saved["data"], SourceRecord(**saved["source"])
            src.issues = [*src.issues, f"LIVE FETCH FAILED ({type(e).__name__}); using last persisted copy"]
        if name == "hazards":
            md.hazards = [RiskEvent(**x) if isinstance(x, dict) else x for x in payload]
        else:
            setattr(md, name, payload)
        md.sources.append(src)
    return md


def load_market(settings: Settings, store, g: GroupData) -> MarketData:
    if settings.market_data == "static":
        return MarketData(mode="static")
    if settings.market_data == "snapshot":
        return _from_snapshot()
    return _live(settings, store, {a.currency for a in g.accounts} | {"USD", "EUR"},
                 {e.country for e in g.entities})


def apply_market(g: GroupData, md: MarketData, settings: Settings) -> list[str]:
    """Overlay official FX and SOFR onto the company data. Returns notes for governance metadata."""
    notes = []
    if md.fx:
        rates = md.fx["usd_per_unit"]
        missing = sorted({a.currency for a in g.accounts} - set(rates))
        g.fx = {**g.fx, **{k: v for k, v in rates.items()}}
        notes.append(f"FX: ECB reference rates of {md.fx['rate_date']}" + (f"; missing {missing}" if missing else ""))
    if md.sofr and g.facility:
        margin = g.facility.margin_bps if g.facility.margin_bps is not None else settings.revolver_margin_bps
        g.facility.rate = round(md.sofr["sofr_pct"] / 100 + margin / 10_000, 6)
        g.facility.rate_source = (f"SOFR {md.sofr['sofr_pct']:.2f}% ({md.sofr['effective_date']}, NY Fed) "
                                  f"+ {margin:.0f} bps margin")
        notes.append(f"Revolver rate: {g.facility.rate_source} = {g.facility.rate:.2%}")
        for fac in g.debt_facilities:
            if fac.rate_type == "FLOATING" and fac.margin_bps is not None:
                fac.rate = round(md.sofr["sofr_pct"] / 100 + fac.margin_bps / 10_000, 6)
                fac.rate_source = (f"SOFR {md.sofr['sofr_pct']:.2f}% ({md.sofr['effective_date']}, NY Fed) "
                                   f"+ {fac.margin_bps:.0f} bps margin")
    if md.hazards:
        known = {e.event_id for e in g.risk_events}
        g.risk_events = g.risk_events + [e for e in md.hazards if e.event_id not in known]
        notes.append(f"Hazards: {len(md.hazards)} live GDACS alerts in operating countries")
    return notes
