"""Treasury relationship graph — entities, accounts, currencies, facilities, counterparties, contracts,
policies, hazards and approvals as typed nodes and edges.

Why a graph and not just tables: CFO questions are path questions. "Which cash is trapped, and by which
rule?" is Entity → SUBJECT_TO → policy clause. "What does the cyclone in China put at risk?" is
RiskEvent → AFFECTS → Country ← LOCATED_IN ← Customer ← SELLS_TO ← Entity. The graph is persisted in
MongoDB as one versioned document (nodes + edges) and rebuilt on every data load, so it never drifts
from the ledgers it describes.
"""

from __future__ import annotations

from collections import defaultdict, deque

from meridian.data.generator import GroupData
from meridian.provenance import now_utc

CONTROL_CLAUSE = {"CN": "CR-1", "IN": "CR-2", "BR": "CR-3", "ZA": "CR-4"}


def build_graph(g: GroupData, runs: list[dict] | None = None, top_counterparties: int = 40) -> dict:
    nodes: dict[str, dict] = {}
    edges: list[dict] = []

    def node(nid: str, kind: str, label: str, **props) -> str:
        nodes.setdefault(nid, {"id": nid, "kind": kind, "label": label, **props})
        return nid

    def edge(src: str, rel: str, dst: str, **props) -> None:
        edges.append({"source": src, "rel": rel, "target": dst, **props})

    fx = g.fx
    bal: dict[str, float] = defaultdict(float)
    for a in g.accounts:
        bal[a.entity_id] += a.balance * fx.get(a.currency, 1.0)
    hub = next((e for e in g.entities if e.role == "HOLDCO"), None)

    for e in g.entities:
        node(e.entity_id, "Entity", e.name, country=e.country, currency=e.currency, role=e.role,
             capital_controls=e.capital_controls, cash_usd=round(bal[e.entity_id]))
        node(f"CCY:{e.currency}", "Currency", e.currency)
        node(f"CTRY:{e.country}", "Country", e.country)
        edge(e.entity_id, "FUNCTIONAL_CURRENCY", f"CCY:{e.currency}")
        edge(e.entity_id, "LOCATED_IN", f"CTRY:{e.country}")
        edge(e.entity_id, "SUBJECT_TO", "POL:TP-3.1")
        if e.capital_controls and e.country in CONTROL_CLAUSE:
            edge(e.entity_id, "SUBJECT_TO", f"POL:{CONTROL_CLAUSE[e.country]}",
                 allowance_usd=e.repatriation_allowance_usd)
        if hub and e.entity_id != hub.entity_id:
            edge(hub.entity_id, "IN_HOUSE_BANK_FOR", e.entity_id)
    for a in g.accounts:
        node(f"ACC:{a.account_id}", "BankAccount", f"{a.bank} {a.currency}", bank=a.bank,
             balance_usd=round(a.balance * fx.get(a.currency, 1.0)))
        edge(a.entity_id, "HOLDS", f"ACC:{a.account_id}")
        edge(f"ACC:{a.account_id}", "DENOMINATED_IN", f"CCY:{a.currency}")
        node(f"BANK:{a.bank}", "Bank", a.bank)
        edge(f"ACC:{a.account_id}", "HELD_AT", f"BANK:{a.bank}")

    for pid, label in [("TP-2.1", "Approval matrix"), ("TP-2.2", "Segregation of duties"), ("TP-3.1", "Operating buffers"),
                       ("TP-3.2", "Intercompany funding"), ("TP-4.1", "Cash concentration"), ("TP-7.1", "Use of AI"),
                       ("RCF-8.1", "Voluntary prepayment"), ("RCF-22.1", "Minimum liquidity covenant"),
                       ("RCF-22.2", "Net leverage covenant"), ("CR-1", "China repatriation"), ("CR-2", "India repatriation"),
                       ("CR-3", "Brazil repatriation"), ("CR-4", "South Africa repatriation")]:
        node(f"POL:{pid}", "PolicyClause", f"{pid} {label}")
    if g.facility:
        f = g.facility
        node(f"FAC:{f.facility_id}", "Facility", f.name, limit_usd=f.limit_usd, drawn_usd=f.drawn_usd, rate=f.rate)
        if hub:
            edge(f"FAC:{f.facility_id}", "BORROWER", hub.entity_id)
        for c in ("RCF-8.1", "RCF-22.1", "RCF-22.2"):
            edge(f"FAC:{f.facility_id}", "GOVERNED_BY", f"POL:{c}")

    # counterparties: largest open exposures only (keeps the graph readable; full lists live in the ledgers)
    custs = {c.customer_id: c for c in g.customers}
    ar: dict[tuple[str, str], float] = defaultdict(float)
    for r in g.receivables:
        if r.settled_date is None:
            ar[(r.entity_id, r.customer_id)] += r.amount * fx.get(r.currency, 1.0)
    for (eid, cid), usd in sorted(ar.items(), key=lambda kv: -kv[1])[:top_counterparties]:
        c = custs[cid]
        node(f"CUS:{cid}", "Customer", c.name, country=c.country, rating=c.credit_rating)
        node(f"CTRY:{c.country}", "Country", c.country)
        edge(eid, "SELLS_TO", f"CUS:{cid}", open_ar_usd=round(usd))
        edge(f"CUS:{cid}", "LOCATED_IN", f"CTRY:{c.country}")
    sups = {s.supplier_id: s for s in g.suppliers}
    ap: dict[tuple[str, str], float] = defaultdict(float)
    for p in g.open_payables():
        ap[(p.entity_id, p.supplier_id)] += p.amount * fx.get(p.currency, 1.0)
    for (eid, sid), usd in sorted(ap.items(), key=lambda kv: -kv[1])[:top_counterparties // 2]:
        s = sups[sid]
        node(f"SUP:{sid}", "Supplier", s.name, country=s.country, critical=s.critical)
        edge(eid, "BUYS_FROM", f"SUP:{sid}", open_ap_usd=round(usd))
    if "S0007" in sups:
        node("SUP:S0007", "Supplier", sups["S0007"].name, country=sups["S0007"].country)
        node("DOC:SC7", "Contract", "Supply agreement S0007 (contains quarantined clause SC7-2)", quarantined=True)
        edge("SUP:S0007", "CONTRACT", "DOC:SC7")

    for ev in g.risk_events:
        if ev.severity == "GREEN" and ev.classification != "SYNTHETIC":
            continue  # keep live green alerts out of the visual graph; they remain in the risk register
        node(f"EVT:{ev.event_id}", "RiskEvent", f"{ev.hazard.replace('_', ' ').title()} ({ev.severity})",
             severity=ev.severity, classification=ev.classification)
        node(f"CTRY:{ev.country}", "Country", ev.country)
        edge(f"EVT:{ev.event_id}", "AFFECTS", f"CTRY:{ev.country}")

    for run in runs or []:
        for a in run.get("actions", [])[:60]:
            aid = f"ACT:{run['run_id']}:{a['action_id']}"
            node(aid, "Action", f"{a['action_id']} {a['type'].replace('_', ' ').title()}", status=a["status"],
                 amount_usd=a["amount_usd"], required_approver=a["required_approver"])
            if a.get("from_entity"):
                edge(aid, "DEBITS", a["from_entity"])
            if a.get("to_entity"):
                edge(aid, "CREDITS", a["to_entity"])
            for ref in a.get("policy_refs", []):
                node(f"POL:{ref}", "PolicyClause", ref)
                edge(aid, "CITES", f"POL:{ref}")
            if a.get("approved_by"):
                u = a["approved_by"]
                node(f"USR:{u['user']}", "User", f"{u['user']} ({u['role']})")
                edge(aid, "APPROVED_BY", f"USR:{u['user']}")

    counts: dict[str, int] = defaultdict(int)
    for n in nodes.values():
        counts[n["kind"]] += 1
    return {"graph_id": "current", "built_at": now_utc().isoformat(), "nodes": list(nodes.values()), "edges": edges,
            "counts": dict(counts), "classification": g.classification}


def neighborhood(graph: dict, start: str, depth: int = 2, limit: int = 120) -> dict:
    adj: dict[str, list[dict]] = defaultdict(list)
    for e in graph["edges"]:
        adj[e["source"]].append(e)
        adj[e["target"]].append(e)
    seen, q, keep = {start}, deque([(start, 0)]), []
    while q and len(seen) < limit:
        nid, d = q.popleft()
        if d >= depth:
            continue
        for e in adj[nid]:
            keep.append(e)
            other = e["target"] if e["source"] == nid else e["source"]
            if other not in seen:
                seen.add(other)
                q.append((other, d + 1))
    idx = {n["id"]: n for n in graph["nodes"]}
    return {"nodes": [idx[i] for i in seen if i in idx], "edges": [e for e in keep if e["source"] in seen and e["target"] in seen]}


def hazard_exposure_paths(graph: dict) -> list[dict]:
    """RiskEvent → Country ← (Entity | Customer) paths with the AR exposed through each customer."""
    by_target = defaultdict(list)
    for e in graph["edges"]:
        by_target[e["target"]].append(e)
    idx = {n["id"]: n for n in graph["nodes"]}
    out = []
    for e in graph["edges"]:
        if e["rel"] != "AFFECTS":
            continue
        ctry = e["target"]
        located = [x["source"] for x in by_target[ctry] if x["rel"] == "LOCATED_IN"]
        entities = [n for n in located if idx.get(n, {}).get("kind") == "Entity"]
        customers = [n for n in located if idx.get(n, {}).get("kind") == "Customer"]
        exposed = sum(x.get("open_ar_usd", 0) for c in customers for x in by_target[c] if x["rel"] == "SELLS_TO")
        out.append({"event": idx[e["source"]]["label"], "event_id": e["source"][4:], "country": ctry[5:],
                    "classification": idx[e["source"]].get("classification"), "entities": entities,
                    "top_customers_exposed": len(customers), "ar_exposed_top_customers_usd": exposed})
    return out
