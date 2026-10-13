"""Generate docs/images/architecture.svg (the README architecture diagram). Pure Python, no dependencies.

    python scripts/build_architecture_diagram.py

Layout: channels → API → (intelligence over data) with the governance column on the right. Every arrow runs through
empty space, never across a box.
"""

from html import escape
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "docs" / "images" / "architecture.svg"
W, H = 1480, 1010
INK, MUTED, LINE = "#101828", "#475467", "#7a869a"
out: list[str] = []


def text(x, y, s, size=13, weight=400, color=INK, anchor="start"):
    out.append(f'<text x="{x}" y="{y}" font-size="{size}" font-weight="{weight}" fill="{color}" '
               f'text-anchor="{anchor}">{escape(s)}</text>')


def band(x, y, w, h, title, fill, stroke):
    out.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="14" fill="{fill}" stroke="{stroke}"/>')
    text(x + 16, y + 20, title, 11.5, 800, MUTED)


def box(x, y, w, h, title, lines, badge=None, badge_color=None, dashed=False, fill="#ffffff", stroke="#d0d5dd"):
    dash = ' stroke-dasharray="6 4"' if dashed else ""
    out.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="10" fill="{fill}" stroke="{stroke}" '
               f'stroke-width="1.4"{dash}/>')
    text(x + 14, y + 24, title, 14, 700)
    if badge:
        bw = 12 + 6.7 * len(badge)
        out.append(f'<rect x="{x + w - bw - 10}" y="{y + 9}" width="{bw}" height="20" rx="10" fill="{badge_color}"/>')
        text(x + w - bw / 2 - 10, y + 23, badge, 10.5, 700, "#ffffff", "middle")
    for i, ln in enumerate(lines):
        text(x + 14, y + 46 + i * 18, ln, 12.2, 400, MUTED)


def arrow(pts, label=None, dashed=False, lx=None, ly=None, anchor="start"):
    dash = ' stroke-dasharray="5 4"' if dashed else ""
    d = " ".join(f"{'M' if i == 0 else 'L'}{x},{y}" for i, (x, y) in enumerate(pts))
    out.append(f'<path d="{d}" fill="none" stroke="{LINE}" stroke-width="1.7"{dash} marker-end="url(#ah)"/>')
    if label:
        text(lx, ly, label, 11, 600, MUTED, anchor)


# ------------------------------------------------------------------------------------------------ header + legend
out.append(f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" '
           'font-family="Inter, Segoe UI, Helvetica, Arial, sans-serif">')
out.append('<defs><marker id="ah" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" '
           f'orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="{LINE}"/></marker></defs>')
out.append(f'<rect width="{W}" height="{H}" fill="#ffffff"/>')
text(30, 42, "Meridian — architecture", 24, 800)
text(30, 66, "Treasury & working-capital intelligence on real public data · governed AI agents · human approval · "
     "tamper-evident audit · no money ever moves", 13.5, 400, MUTED)
for k, (lab, col) in enumerate([("PUBLIC · REAL", "#067647"), ("SYNTHETIC", "#7a5af8"), ("NOT CONFIGURED", "#98a2b3")]):
    x = W - 480 + k * 155
    out.append(f'<rect x="{x}" y="30" width="14" height="14" rx="3" fill="{col}"/>')
    text(x + 20, 42, lab, 11.5, 700, MUTED)

# ------------------------------------------------------------------------------------------------ users & channels
band(20, 86, W - 40, 150, "USERS & CHANNELS", "#f2f4f7", "#d0d5dd")
uw = (W - 80 - 3 * 20) / 4
U = [(40 + i * (uw + 20), 116) for i in range(4)]
for (x, y), (t, ls) in zip(U, [
        ("Six finance roles", ["Analyst · WC Manager · Treasurer", "CFO · Internal Audit · Administrator",
                               "permissions enforced server-side"]),
        ("Web console", ["15 workspaces · ECharts", "lineage drawer on every number", "Value at Stake · board report"]),
        ("REST / OpenAPI clients", ["83 /v1 endpoints · OpenAPI docs", "bearer tokens (no cookies → no CSRF)",
                                    "every route requires a role"]),
        ("Scheduler", ["governed review at start-up", "runs as meridian-scheduler", "audited like any user"])],
        strict=True):
    box(x, y, uw, 104, t, ls)

# ------------------------------------------------------------------------------------------------ API & security
band(20, 262, W - 40, 150, "API & SECURITY", "#eef4ff", "#b2ccff")
aw = (W - 80 - 2 * 20) / 3
A = [(40 + i * (aw + 20), 292) for i in range(3)]
box(*A[0], aw, 104, "Identity & access", ["PBKDF2-SHA256 (600k) · lockout without enumeration",
                                          "four-eyes privileged role grants",
                                          "enterprise SSO (OIDC): not configured"], "LOCAL ACCOUNTS", "#667085")
box(*A[1], aw, 104, "Operations", ["/health · /ready · meridian doctor", "db status · backup · restore (verified)",
                                   "audit verify · anchor · JSON logs · /metrics"])
box(*A[2], aw, 104, "FastAPI gateway", ["JWT (HS256) · logout revocation · RBAC per route",
                                        "CSP · HSTS · nosniff · request ids",
                                        "input validation (422) · rate-safe SEC client"])

# ------------------------------------------------------------------------------------------------ intelligence
IX, IW = 20, 1060
band(IX, 438, IW, 262, "INTELLIGENCE", "#f4f3ff", "#c7c2f9")
iw = (IW - 40 - 2 * 24) / 3
IN = [(IX + 20 + i * (iw + 24), 468) for i in range(3)]
box(*IN[0], iw, 210, "Analytics engines", ["Public-company metrics + lineage", "TTM: FY + YTD − prior YTD (recast-safe)",
                                          "Value at Stake: 390 cos, SIC peers", "forecasts · scenarios · rate shocks",
                                          "synthetic: Monte Carlo · HiGHS LP ·", "gradient-boosted payment delay",
                                          "policy RAG: vector + BM25"])
box(*IN[1], iw, 210, "MCP tool server", ["27 read-only tools", "agents cannot write, pay or approve",
                                        "every call traced", "", "outbound DLP before any LLM call",
                                        "token & call budget per run"], "MCP", "#6941c6")
box(*IN[2], iw, 210, "Governed agent pipeline", ["LangGraph: planner → 6 analysis agents", "→ decision → policy → governance",
                                                "→ briefing → grounding guardrail", "",
                                                "LLM: Claude when a key is set,", "else deterministic — always labelled"],
    "LANGGRAPH", "#6941c6")

# ------------------------------------------------------------------------------------------------ data
band(IX, 726, IW, 262, "DATA", "#ecfdf3", "#a6e9c5")
dw = (IW - 40 - 3 * 20) / 4
D = [(IX + 20 + i * (dw + 20), 756) for i in range(4)]
box(*D[0], dw, 210, "Public data", ["SEC EDGAR XBRL", "(companyfacts + frames)", "NY Fed SOFR · UST curve",
                                    "FRED spreads · ECB FX", "UCI retail invoices", "dated, checksummed"],
    "REAL", "#067647")
box(*D[1], dw, 210, "Governed ingestion", ["ERP CSV · camt.053 · BAI2", "validation · reconciliation",
                                           "dedupe · quarantine", "hostile XML refused", "lineage per loaded row"],
    "SYNTHETIC", "#7a5af8")
box(*D[2], dw, 210, "Client systems", ["SAP · Oracle · NetSuite", "bank H2H / SWIFT FileAct", "enterprise SSO",
                                       "interfaces defined,", "never faked"],
    "NOT CONFIG.", "#98a2b3", dashed=True, fill="#fcfcfd")
box(*D[3], dw, 210, "Persistence", ["SQLite (default, WAL,", "migrations)", "MongoDB (multi-replica)",
                                    "users · runs · decisions", "verified backup/restore"])

# ------------------------------------------------------------------------------------------------ governance column
GX, GW = 1100, W - 20 - 1100
band(GX, 438, GW, 550, "GOVERNANCE & DECISIONS", "#fef6ee", "#f9c599")
gw = GW - 40
G = [(GX + 20, 468, 168), (GX + 20, 664, 132), (GX + 20, 824, 142)]
box(G[0][0], G[0][1], gw, G[0][2], "Policy & approvals", ["← recommendations from the agents",
                                                          "approval matrix (policy-as-code, versioned)",
                                                          "authority + segregation of duties",
                                                          "superseded-run · policy-drift · replay",
                                                          "  guards · compare-and-swap",
                                                          ])
box(G[1][0], G[1][1], gw, G[1][2], "Execution boundary", ["pain.001 DRAFT only after approval",
                                                          "dual control: approver ≠ releaser",
                                                          "transmission channel: none"], "NO PAYMENTS", "#b42318")
box(G[2][0], G[2][1], gw, G[2][2], "Audit ledger", ["SHA-256 hash chain, sequence-bound",
                                                    "append-only (DB triggers / Mongo role)",
                                                    "every decision and denial recorded",
                                                    "external anchors · stored in persistence ←"])

# ------------------------------------------------------------------------------------------------ flows (empty space only)
gx = A[2][0] + 60                                                   # gateway / pipeline overlap
arrow([(U[1][0] + uw / 2, 220), (A[2][0] + 30, 292)])               # console → gateway
arrow([(U[2][0] + uw / 2, 220), (A[2][0] + 90, 292)])               # REST → gateway
arrow([(U[3][0] + 60, 220), (A[2][0] + aw - 60, 292)])              # scheduler → gateway
arrow([(gx, 396), (gx, 468)], "runs, questions", lx=gx + 8, ly=430)
arrow([(IN[2][0], 600), (IN[1][0] + iw, 600)], "tools", lx=IN[2][0] - 12, ly=592, anchor="end")
arrow([(IN[1][0], 600), (IN[0][0] + iw, 600)], "read", lx=IN[1][0] - 12, ly=592, anchor="end")
arrow([(IN[2][0] + iw, 540), (GX + 20, 540)])                       # recommendations → approvals
arrow([(GX + 20 + gw / 2, 636), (GX + 20 + gw / 2, 664)])           # approvals → execution
arrow([(GX + 20 + gw / 2, 796), (GX + 20 + gw / 2, 824)])           # execution → audit
arrow([(D[0][0] + dw / 2, 756), (D[0][0] + dw / 2, 678)], "snapshots", lx=D[0][0] + dw / 2 + 8, ly=720)
arrow([(D[1][0] + 40, 756), (D[1][0] + 40, 678)], "loads", dashed=True, lx=D[1][0] + 48, ly=720)
arrow([(D[2][0], 860), (D[1][0] + dw, 860)], None, dashed=True)     # client systems → ingestion (when configured)
arrow([(IN[2][0] + iw / 2 + 60, 678), (IN[2][0] + iw / 2 + 60, 756)], "runs stored", lx=IN[2][0] + iw / 2 + 68, ly=720)
arrow([(GX + 20, 900), (D[3][0] + dw, 900)])                       # audit records → persistence
out.append("</svg>")
OUT.parent.mkdir(parents=True, exist_ok=True)
OUT.write_text("\n".join(out), encoding="utf-8")
print(OUT)
