"""Financial relationship graph built from APPLICATION DATA — no decorative nodes.

    Organization → Entity → BankAccount → Bank / Currency
    Organization → Customer → Invoice → Payment            (collections)
    Organization → Supplier → Bill → Payment               (disbursements)
    Organization → DebtFacility → Covenant ; DebtFacility → RateIndex
    Dataset → Dataset (lineage) ; Metric → Dataset ; Risk → Metric / Dataset
    Recommendation → Metric / Dataset (evidence) → PolicyClause → Action → Approver / AuditRecord

Queries answer CFO questions by traversal and return the path they used, so every answer is explainable.
"""

from __future__ import annotations

from collections import defaultdict, deque
from datetime import timedelta

from meridian.data.enterprise import BANK_ID_BY_NAME, bu_of_segment


class FinancialGraph:
    def __init__(self):
        self.nodes: dict[str, dict] = {}
        self.out: dict[str, list[tuple[str, str]]] = defaultdict(list)
        self.inc: dict[str, list[tuple[str, str]]] = defaultdict(list)

    def node(self, nid: str, kind: str, label: str, **props) -> str:
        if nid not in self.nodes:
            self.nodes[nid] = {"id": nid, "kind": kind, "label": label, **props}
        else:
            self.nodes[nid].update(props)
        return nid

    def edge(self, a: str, rel: str, b: str) -> None:
        if a in self.nodes and b in self.nodes:
            self.out[a].append((rel, b))
            self.inc[b].append((rel, a))

    def kids(self, nid: str, rel: str | None = None, kind: str | None = None) -> list[str]:
        return [b for r, b in self.out.get(nid, []) if (rel is None or r == rel) and
                (kind is None or self.nodes[b]["kind"] == kind)]

    def parents(self, nid: str, rel: str | None = None) -> list[str]:
        return [a for r, a in self.inc.get(nid, []) if rel is None or r == rel]

    def downstream(self, start: str, max_depth: int = 6) -> list[dict]:
        seen, q, out = {start}, deque([(start, 0)]), []
        while q:
            n, d = q.popleft()
            if d >= max_depth:
                continue
            for rel, a in self.inc.get(n, []):     # reverse of "depends on" edges = impact direction
                if a not in seen and rel in DEPENDS:
                    seen.add(a)
                    out.append({**self.nodes[a], "depth": d + 1, "via": rel, "from": n})
                    q.append((a, d + 1))
        return out

    def stats(self) -> dict:
        k: dict[str, int] = defaultdict(int)
        for n in self.nodes.values():
            k[n["kind"]] += 1
        return {"nodes": len(self.nodes), "edges": sum(len(v) for v in self.out.values()), "by_kind": dict(k)}


# Edges that express "A depends on B" — used for impact analysis (what is affected if B changes)
DEPENDS = {"DERIVED_FROM", "COMPUTED_FROM", "SUPPORTED_BY", "PROPOSES", "EVIDENCED_BY", "PRICED_OFF", "TESTED_ON",
           "HAS_COVENANT"}


def build(engine, runs: list[dict] | None = None) -> FinancialGraph:
    g, G = engine.g, FinancialGraph()
    fx = g.fx
    org = g.organization
    o = G.node(f"ORG:{org.org_id if org else 'ORG'}", "Organization", org.name if org else g.tenant,
               classification=g.classification)
    cash: dict[str, float] = defaultdict(float)
    for a in g.accounts:
        cash[a.entity_id] += a.balance * fx[a.currency]
    fc = engine.forecast_summary()
    fce = {e["entity_id"]: e for e in fc["entities"]}
    for e in g.entities:
        G.node(e.entity_id, "Entity", e.name, country=e.country, currency=e.currency, capital_controls=e.capital_controls,
               cash_usd=round(cash[e.entity_id]), buffer_usd=fce.get(e.entity_id, {}).get("buffer_usd"),
               p10_trough_usd=fce.get(e.entity_id, {}).get("p10_trough_usd"))
        G.edge(o, "HAS_ENTITY", e.entity_id)
        G.node(f"CTRY:{e.country}", "Country", e.country)
        G.edge(e.entity_id, "LOCATED_IN", f"CTRY:{e.country}")
    for b in g.banks:
        G.node(b.bank_id, "Bank", b.name, rating=b.credit_rating, country=b.country)
    for a in g.accounts:
        G.node(f"ACC:{a.account_id}", "BankAccount", a.account_id, bank=a.bank, currency=a.currency,
               balance_usd=round(a.balance * fx[a.currency]))
        G.edge(a.entity_id, "HOLDS_ACCOUNT", f"ACC:{a.account_id}")
        G.node(f"CCY:{a.currency}", "Currency", a.currency, usd_rate=fx[a.currency])
        G.edge(f"ACC:{a.account_id}", "DENOMINATED_IN", f"CCY:{a.currency}")
        bid = BANK_ID_BY_NAME.get(a.bank)
        if bid:
            G.edge(f"ACC:{a.account_id}", "AT_BANK", bid)
    for bu in g.business_units:
        G.node(bu.bu_id, "BusinessUnit", bu.name)
        G.edge(o, "HAS_BU", bu.bu_id)
    for c in g.customers:
        G.node(f"CUS:{c.customer_id}", "Customer", c.name, rating=c.credit_rating, segment=c.segment, country=c.country)
        G.edge(o, "HAS_CUSTOMER", f"CUS:{c.customer_id}")
        G.edge(f"CUS:{c.customer_id}", "IN_BU", bu_of_segment(c.segment))
    horizon = g.as_of - timedelta(days=180)
    for r in g.receivables:
        if r.settled_date is not None and r.settled_date < horizon:
            continue
        iid = f"INV:{r.invoice_id}"
        G.node(iid, "Invoice", r.invoice_id, amount_usd=round(r.amount * fx[r.currency]), due=str(r.due_date),
               open=r.settled_date is None, overdue_days=max(0, (g.as_of - r.due_date).days) if r.settled_date is None
               else 0, disputed=r.disputed)
        G.edge(f"CUS:{r.customer_id}", "BILLED", iid)
        G.edge(iid, "ISSUED_BY", r.entity_id)
        if r.settled_date:
            pid = f"PAY:{r.invoice_id}"
            G.node(pid, "Payment", f"Receipt {r.invoice_id}", date=str(r.settled_date),
                   days_late=(r.settled_date - r.due_date).days, amount_usd=round(r.amount * fx[r.currency]))
            G.edge(iid, "SETTLED_BY", pid)
    for s in g.suppliers:
        G.node(f"SUP:{s.supplier_id}", "Supplier", s.name, critical=s.critical, country=s.country)
        G.edge(o, "HAS_SUPPLIER", f"SUP:{s.supplier_id}")
    for p in g.payables:
        bid = f"BILL:{p.bill_id}"
        took = bool(p.discount_pct and p.paid_date and p.paid_date <= p.issue_date + timedelta(days=p.discount_days))
        G.node(bid, "Bill", p.bill_id, amount_usd=round(p.amount * fx[p.currency]), due=str(p.due_date),
               open=p.paid_date is None, discount_pct=p.discount_pct, discount_taken=took,
               days_early=(p.due_date - p.paid_date).days if p.paid_date else None)
        G.edge(f"SUP:{p.supplier_id}", "INVOICED", bid)
        G.edge(bid, "BILLED_TO", p.entity_id)
        if p.paid_date:
            G.node(f"DIS:{p.bill_id}", "Payment", f"Disbursement {p.bill_id}", date=str(p.paid_date))
            G.edge(bid, "PAID_BY", f"DIS:{p.bill_id}")
    debt = engine.debt_profile()
    for f in debt["facilities"]:
        G.node(f["facility_id"], "DebtFacility", f["name"], type=f["type"], drawn_usd=f["drawn_usd"], rate=f["rate"],
               maturity=f["maturity_date"], months_to_maturity=f["months_to_maturity"],
               in_refinancing_window=f["in_refinancing_window"])
        G.edge(o, "HAS_FACILITY", f["facility_id"])
        G.edge(f["facility_id"], "BORROWED_BY", f["borrower_entity"])
    for c in engine.covenants()["covenants"]:
        G.node(c["covenant_id"], "Covenant", c["name"], status=c["status"], headroom=min(c["headroom_today_pct"],
                                                                                        c["headroom_forward_pct"]),
               clause=c["clause_ref"])
        G.edge(c["facility_id"], "HAS_COVENANT", c["covenant_id"])
    # ---- data lineage, metrics, risks
    for d in engine.catalog():
        G.node(f"DS:{d['dataset_id']}", "Dataset", d["dataset_id"], layer=d["layer"], classification=d["classification"],
               as_of=d.get("as_of"))
    for d in engine.catalog():
        for p in d.get("derived_from", []):
            G.edge(f"DS:{d['dataset_id']}", "DERIVED_FROM", f"DS:{p}")
    for f in debt["facilities"]:
        if f["rate_type"] == "FLOATING":
            G.edge(f["facility_id"], "PRICED_OFF", "DS:market.sofr")
    for c in engine.covenants()["covenants"]:
        G.edge(c["covenant_id"], "TESTED_ON", "DS:analytical.covenant_tests")
    for m in engine.metrics().values():
        G.node(f"MET:{m['metric_id']}", "Metric", m["label"], value=m["value"], unit=m["unit"], basis=m["basis"])
        for i in m["inputs"]:
            G.edge(f"MET:{m['metric_id']}", "COMPUTED_FROM", f"DS:{i}")
        G.edge(f"MET:{m['metric_id']}", "COMPUTED_FROM", f"DS:{m['data_product']}")
    for r in engine.risks():
        G.node(f"RSK:{r['risk_id']}", "Risk", r["title"], category=r["category"], score=r["score"])
        for ev in r["evidence"]:
            kind, _, ref = ev.partition(":")
            G.edge(f"RSK:{r['risk_id']}", "EVIDENCED_BY", f"MET:{ref}" if kind == "metric" else f"DS:{ref}")
    # ---- decisions
    for run in runs or []:
        for rec in run.get("recommendations") or []:
            rid = f"REC:{rec['rec_id']}"
            G.node(rid, "Recommendation", f"{rec['short_id']} {rec['title']}", status=rec["status"],
                   priority=rec["priority"], run_id=run["run_id"], category=rec["category"])
            for ev in rec["evidence"]:
                tgt = {"metric": f"MET:{ev['id']}", "dataset": f"DS:{ev['id']}"}.get(ev["type"])
                if tgt:
                    G.edge(rid, "SUPPORTED_BY", tgt)
            for ref in rec["policy_refs"]:
                G.node(f"POL:{ref}", "PolicyClause", ref)
                G.edge(rid, "GOVERNED_BY", f"POL:{ref}")
        for a in run.get("actions", []):
            aid = f"ACT:{run['run_id']}:{a['action_id']}"
            G.node(aid, "Action", f"{a['action_id']} {a['type'].replace('_', ' ').title()}", status=a["status"],
                   amount_usd=a["amount_usd"], required_approver=a["required_approver"])
            if a.get("rec_id"):
                G.edge(f"REC:{a['rec_id']}", "PROPOSES", aid)
            for who in ("submitted_by", "approved_by", "rejected_by", "prepared_by"):
                if a.get(who):
                    u = a[who]["user"]
                    G.node(f"USR:{u}", "User", f"{u} ({a[who]['role']})")
                    G.edge(aid, who.upper(), f"USR:{u}")
    return G


# ================================================================================================ queries
def q_excess_cash(G: FinancialGraph) -> dict:
    rows = []
    for n in G.nodes.values():
        if n["kind"] == "Entity" and n.get("buffer_usd") is not None:
            excess = n["cash_usd"] - n["buffer_usd"]
            rows.append({"entity_id": n["id"], "entity": n["label"], "cash_usd": n["cash_usd"], "buffer_usd": n["buffer_usd"],
                         "excess_usd": round(excess), "p10_trough_usd": n.get("p10_trough_usd"),
                         "capital_controls": n["capital_controls"],
                         "accounts": len(G.kids(n["id"], "HOLDS_ACCOUNT"))})
    rows.sort(key=lambda r: -r["excess_usd"])
    return {"path": "Organization → Entity → BankAccount → Currency", "rows": [r for r in rows if r["excess_usd"] > 0]}


def q_concentration(G: FinancialGraph) -> dict:
    by_bank: dict[str, float] = defaultdict(float)
    for n in G.nodes.values():
        if n["kind"] == "BankAccount":
            for b in G.kids(n["id"], "AT_BANK"):
                by_bank[b] += n["balance_usd"]
    total = sum(by_bank.values()) or 1
    rows = [{"bank_id": b, "bank": G.nodes[b]["label"], "rating": G.nodes[b]["rating"], "cash_usd": round(v),
             "share": round(v / total, 4)} for b, v in sorted(by_bank.items(), key=lambda kv: -kv[1])]
    ents = sorted((n for n in G.nodes.values() if n["kind"] == "Entity"), key=lambda n: -n["cash_usd"])
    return {"path": "Entity → BankAccount → Bank", "rows": rows,
            "entities": [{"entity_id": e["id"], "entity": e["label"], "share": round(e["cash_usd"] / total, 4)}
                         for e in ents[:5]]}


def q_overdue_drivers(G: FinancialGraph) -> dict:
    rows = []
    for n in G.nodes.values():
        if n["kind"] != "Customer":
            continue
        inv = [G.nodes[i] for i in G.kids(n["id"], "BILLED")]
        od = [i for i in inv if i["open"] and i["overdue_days"] > 0]
        if not od:
            continue
        paid = [G.nodes[p] for i in inv for p in G.kids(i["id"], "SETTLED_BY")]
        rows.append({"customer_id": n["id"][4:], "customer": n["label"], "rating": n["rating"],
                     "overdue_usd": sum(i["amount_usd"] for i in od), "overdue_invoices": len(od),
                     "max_days_overdue": max(i["overdue_days"] for i in od),
                     "disputed": sum(1 for i in od if i["disputed"]),
                     "avg_days_late_recent": round(sum(p["days_late"] for p in paid) / len(paid), 1) if paid else None})
    rows.sort(key=lambda r: -r["overdue_usd"])
    total = sum(r["overdue_usd"] for r in rows) or 1
    for r in rows:
        r["share_of_overdue"] = round(r["overdue_usd"] / total, 4)
    return {"path": "Organization → Customer → Invoice → Payment", "rows": rows[:20]}


def q_supplier_opportunities(G: FinancialGraph, cost_of_funds: float) -> dict:
    rows = []
    for n in G.nodes.values():
        if n["kind"] != "Supplier":
            continue
        bills = [G.nodes[b] for b in G.kids(n["id"], "INVOICED")]
        missed = sum(b["amount_usd"] * b["discount_pct"] for b in bills if not b["open"] and b["discount_pct"]
                     and not b["discount_taken"])
        early = sum(b["amount_usd"] * (b["days_early"] or 0) / 365 * cost_of_funds for b in bills
                    if not b["open"] and not b["discount_pct"] and (b["days_early"] or 0) >= 5)
        open_disc = sum(b["amount_usd"] * b["discount_pct"] for b in bills if b["open"] and b["discount_pct"])
        if missed + early + open_disc > 0:
            rows.append({"supplier_id": n["id"][4:], "supplier": n["label"], "critical": n["critical"],
                         "missed_discounts_usd": round(missed), "early_payment_cost_usd": round(early),
                         "open_discount_value_usd": round(open_disc),
                         "opportunity_usd": round(missed + early + open_disc)})
    rows.sort(key=lambda r: -r["opportunity_usd"])
    return {"path": "Organization → Supplier → Bill → Payment", "rows": rows[:20]}


def q_refinancing(G: FinancialGraph) -> dict:
    rows = [{"facility_id": n["id"], "name": n["label"], "type": n["type"], "drawn_usd": n["drawn_usd"],
             "maturity": n["maturity"], "months_to_maturity": n["months_to_maturity"],
             "in_window": n["in_refinancing_window"],
             "covenants": [G.nodes[c]["label"] for c in G.kids(n["id"], "HAS_COVENANT")]}
            for n in G.nodes.values() if n["kind"] == "DebtFacility"]
    rows.sort(key=lambda r: r["months_to_maturity"])
    return {"path": "Organization → DebtFacility → Covenant", "rows": rows}


def q_covenants(G: FinancialGraph) -> dict:
    rows = []
    for n in G.nodes.values():
        if n["kind"] == "Covenant":
            fac = G.parents(n["id"], "HAS_COVENANT")
            rows.append({"covenant_id": n["id"], "covenant": n["label"], "status": n["status"], "headroom": n["headroom"],
                         "clause": n["clause"], "facility": G.nodes[fac[0]]["label"] if fac else None})
    rows.sort(key=lambda r: r["headroom"])
    return {"path": "DebtFacility → Covenant", "rows": rows}


def q_source_dependencies(G: FinancialGraph, dataset_id: str) -> dict:
    nid = f"DS:{dataset_id}"
    if nid not in G.nodes:
        return {"path": "Dataset → … → Recommendation", "rows": [], "error": f"unknown dataset {dataset_id}"}
    hits = G.downstream(nid)
    return {"path": "Dataset → Dataset → Metric → Recommendation → Action", "dataset": dataset_id,
            "rows": [{"id": h["id"], "kind": h["kind"], "label": h["label"], "depth": h["depth"], "via": h["via"]}
                     for h in hits if h["kind"] in {"Dataset", "Metric", "Recommendation", "Action", "Risk", "Covenant",
                                                    "DebtFacility"}]}


def q_impact(G: FinancialGraph, node_id: str) -> dict:
    """What is affected if this node changes (a rate print, a dataset reload, a hazard, a customer default)?"""
    if node_id not in G.nodes:
        alt = f"DS:{node_id}"
        node_id = alt if alt in G.nodes else node_id
    if node_id not in G.nodes:
        return {"rows": [], "error": f"unknown node {node_id}"}
    n = G.nodes[node_id]
    extra = []
    if n["kind"] in {"Country"}:
        for e in G.parents(node_id, "LOCATED_IN"):
            extra.append({**G.nodes[e], "depth": 1, "via": "LOCATED_IN"})
    if n["kind"] == "Customer":
        inv = [G.nodes[i] for i in G.kids(node_id, "BILLED") if G.nodes[i]["open"]]
        extra.append({"id": "open_ar", "kind": "Exposure", "label": f"Open AR ${sum(i['amount_usd'] for i in inv):,.0f}",
                      "depth": 1, "via": "BILLED"})
    hits = G.downstream(node_id)
    return {"node": n, "path": "impact = reverse of DERIVED_FROM / COMPUTED_FROM / SUPPORTED_BY / PROPOSES edges",
            "rows": [{"id": h["id"], "kind": h["kind"], "label": h["label"], "depth": h["depth"], "via": h["via"]}
                     for h in extra + hits]}


def view(G: FinancialGraph, kinds: set[str], node: str | None = None, depth: int = 1, limit: int = 400) -> dict:
    if node:
        order, seen, frontier = [node], {node}, [node]
        for _ in range(depth):
            nxt = []
            for n in frontier:
                # follow outgoing edges first (owned objects), then incoming; hubs (Organization) are not expanded
                if G.nodes[n]["kind"] == "Organization" and n != node:
                    continue
                for b in [b for _, b in G.out.get(n, [])] + [a for _, a in G.inc.get(n, [])]:
                    if b not in seen:
                        seen.add(b)
                        order.append(b)
                        nxt.append(b)
            frontier = nxt
        keep = set(order[:limit])
    else:
        keep = {n for n, d in G.nodes.items() if d["kind"] in kinds}
        if len(keep) > limit:
            keep = set(sorted(keep, key=lambda n: -len(G.out.get(n, [])) - len(G.inc.get(n, [])))[:limit])
    nodes = [G.nodes[n] for n in keep]
    edges = [{"source": a, "rel": r, "target": b} for a in keep for r, b in G.out.get(a, []) if b in keep]
    return {"nodes": nodes, "edges": edges}
