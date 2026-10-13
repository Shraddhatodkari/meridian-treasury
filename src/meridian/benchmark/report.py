"""Board-style 'Value at Stake' report: self-contained HTML (no external assets), printable to PDF from a browser.
Every number is computed from the study and the validation at render time — the text contains no hard-coded figures."""

from __future__ import annotations

from datetime import UTC, datetime
from html import escape

from meridian import __version__
from meridian.benchmark.forecast_validation import run_validation
from meridian.benchmark.study import run_study


def _b(x: float) -> str:
    if abs(x) >= 1e12:
        return f"${x / 1e12:,.2f}T"
    return f"${x / 1e9:,.1f}B" if abs(x) >= 1e9 else f"${x / 1e6:,.0f}M"


def _bars(rows: list[tuple[str, float, float]], width: int = 640) -> str:
    """Horizontal bar chart (inline SVG): headline opportunity and segment-review amount per sector."""
    top = max((a + b for _, a, b in rows), default=1) or 1
    h, lab, bw = 22, 230, width - 230 - 90
    out = [f'<svg viewBox="0 0 {width} {len(rows) * h + 10}" width="100%" role="img" '
           'aria-label="Cash opportunity by sector">']
    for i, (name, a, b) in enumerate(rows):
        y = i * h + 4
        wa, wb = bw * a / top, bw * b / top
        out.append(f'<text x="{lab - 8}" y="{y + 13}" text-anchor="end" font-size="11" fill="#333">{escape(name)}</text>'
                   f'<rect x="{lab}" y="{y}" width="{wa:.1f}" height="15" fill="#1f5fa8"/>'
                   f'<rect x="{lab + wa:.1f}" y="{y}" width="{wb:.1f}" height="15" fill="#b7c7dc"/>'
                   f'<text x="{lab + wa + wb + 6:.1f}" y="{y + 12}" font-size="11" fill="#333">{_b(a)}</text>')
    out.append("</svg>")
    return "".join(out)


def render() -> str:
    s, v = run_study(), run_validation()
    o = s["summary"]["opportunity"]
    med, top = o["median"], o["top_quartile"]
    u = s["universe"]
    secs = s["sectors"]
    lead = secs[0] if secs else None
    rows_top = s["companies"][:15]
    review = [c for c in s["companies"] if c.get("review_levers")]
    mv = v["methods"]
    best = min(mv, key=lambda m: mv[m]["wape"])
    ar_share = med["receivables_usd"] / med["total_excl_payables_usd"] if med["total_excl_payables_usd"] else 0
    tr = "".join(
        f"<tr><td>{c['rank_excl_payables']}</td><td>{escape(c['name'])}</td><td>{escape(', '.join(c['tickers'][:1]))}</td>"
        f"<td>{escape(c['peer_group']['label'] or '')} ({c['peer_group']['companies']})</td>"
        f"<td class=n>{c['dso']}</td><td class=n>{c['dio']}</td><td class=n>{c['dpo']}</td>"
        f"<td class=n><b>{_b(c['opportunity']['median']['total_excl_payables_usd'])}</b></td>"
        f"<td class=n>{_b(c['opportunity']['top_quartile']['total_excl_payables_usd'])}</td></tr>" for c in rows_top)
    rv = "".join(f"<tr><td>{escape(c['name'])}</td><td>{escape(c.get('review_reason', ''))}</td>"
                 f"<td class=n>{_b(c['opportunity']['median']['requires_segment_review_usd'])}</td></tr>" for c in review)
    sec_rows = "".join(
        f"<tr><td>{escape(x['sector'])}</td><td class=n>{x['companies']}</td>"
        f"<td class=n>{_b(x['revenue_usd'])}</td>"
        f"<td class=n>{(x['benchmarks'] or {}).get('ccc', {}).get('median', '—')}</td>"
        f"<td class=n><b>{_b(x['opportunity']['median']['total_excl_payables_usd'])}</b></td>"
        f"<td class=n>{_b(x['opportunity']['median']['payables_usd'])}</td></tr>" for x in secs)
    meth = "".join(f"<tr><td>{m.replace('_', ' ')}{' (Meridian)' if m == v['selected'] else ''}</td>"
                   f"<td class=n>{mv[m]['wape']:.1%}</td></tr>" for m in mv)
    lim = "".join(f"<li>{escape(x)}</li>" for x in s["limitations"] + v["limitations"])
    cov = v["interval"]["observed_coverage"]
    beat = mv["seasonal_level"]["wape"] <= mv[best]["wape"]
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Working-capital value at stake — FY2025</title>
<style>
body{{font:14px/1.5 -apple-system,Segoe UI,Roboto,Arial,sans-serif;color:#1d2433;max-width:960px;margin:32px auto;padding:0 20px;background:#fff}}
h1{{font-size:26px;margin:0 0 4px}} h2{{font-size:18px;margin:28px 0 8px;border-bottom:2px solid #1f5fa8;padding-bottom:4px}}
.sub{{color:#5b6475}} .kpis{{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin:18px 0}}
.k{{border:1px solid #d9dee8;border-radius:8px;padding:12px}} .k b{{display:block;font-size:22px;color:#1f5fa8}}
table{{border-collapse:collapse;width:100%;font-size:12.5px}} th,td{{border-bottom:1px solid #e3e7ef;padding:5px 6px;text-align:left}}
th{{background:#f4f6fa}} td.n,th.n{{text-align:right;font-variant-numeric:tabular-nums}}
.note{{background:#f4f6fa;border-left:3px solid #1f5fa8;padding:8px 12px;font-size:13px}} .lab{{font-size:11px;color:#5b6475}}
@media print{{body{{margin:0}} h2{{page-break-after:avoid}} table{{page-break-inside:auto}}}}
</style></head><body>
<div class="lab">MERIDIAN {escape(__version__)} · PUBLIC · REAL data · generated {datetime.now(UTC):%Y-%m-%d %H:%M} UTC</div>
<h1>{_b(med['total_excl_payables_usd'])} of cash is tied up in receivables and inventory beyond peer medians</h1>
<div class="sub">{u['benchmarked']} large US-listed companies (revenue ≥ $1B), fiscal years ending June 2025 – February 2026 (SEC calendar-year {s['fiscal_year']} frame), SEC 10-K filings.
Stretch case (peer top quartile): {_b(top['total_excl_payables_usd'])}.</div>
<div class="kpis">
<div class="k"><span class="lab">Companies benchmarked</span><b>{u['benchmarked']}</b><span class="lab">{u['sectors']} sectors · {u['excluded']} excluded with reasons</span></div>
<div class="k"><span class="lab">Revenue covered</span><b>{_b(s['summary']['revenue_usd'])}</b><span class="lab">trade working capital {_b(s['summary']['trade_working_capital_usd'])}</span></div>
<div class="k"><span class="lab">Cash to peer median</span><b>{_b(med['total_excl_payables_usd'])}</b><span class="lab">receivables {_b(med['receivables_usd'])} · inventory {_b(med['inventory_usd'])}</span></div>
<div class="k"><span class="lab">Needs segment review</span><b>{_b(med['requires_segment_review_usd'])}</b><span class="lab">{len(review)} companies, not in headline</span></div>
</div>
<h2>Key findings</h2>
<ol>
<li><b>Receivables are {ar_share:.0%} of the conservative opportunity.</b> Collecting closer to peer medians releases {_b(med['receivables_usd'])}; inventory adds {_b(med['inventory_usd'])}.</li>
{f"<li><b>{escape(lead['sector'])} carries the largest pool</b> ({_b(lead['opportunity']['median']['total_excl_payables_usd'])} across {lead['companies']} companies).</li>" if lead else ""}
<li><b>Supplier terms are reported separately:</b> matching peer-median payment terms would add {_b(med['payables_usd'])}, but that cash comes from suppliers. Pursue it only with supplier protection such as supply-chain finance or commercial-card programmes.</li>
<li><b>{len(review)} companies need segment-level work</b> ({_b(med['requires_segment_review_usd'])}): they dominate their peer group or sit far outside it, so the gap is more likely business mix than performance.</li>
<li><b>Forecasting on real transactions:</b> on {v['raw_rows']:,} real invoice lines, Meridian's 13-week method is {v['improvement_vs_naive_pct']}% more accurate than a naive forecast (WAPE {mv['seasonal_level']['wape']:.1%} vs {mv['naive_last']['wape']:.1%}){'' if beat else f"; plain seasonal-naive is marginally better ({mv[best]['wape']:.1%}), so it is not a material improvement on that baseline"}. The 80% band covered {cov:.0%} of outcomes — it is too narrow and is shown as such.</li>
</ol>
<h2>Where the cash is — by sector (conservative case)</h2>
{_bars([(x['sector'], x['opportunity']['median']['total_excl_payables_usd'], 0) for x in secs[:12]])}
<table><tr><th>Sector</th><th class=n>Companies</th><th class=n>Revenue</th><th class=n>Median CCC (days)</th><th class=n>Cash to median (AR+inv.)</th><th class=n>Supplier terms (separate)</th></tr>{sec_rows}</table>
<h2>Largest company opportunities</h2>
<table><tr><th>#</th><th>Company</th><th>Ticker</th><th>Peer group (n)</th><th class=n>DSO</th><th class=n>DIO</th><th class=n>DPO</th><th class=n>To median</th><th class=n>To top quartile</th></tr>{tr}</table>
<p class="note">Opportunities are hypotheses to test with management, not forecasts. Every input traces to an SEC accession number in the application (Value at Stake → company).</p>
<h2>Requires segment-level review (excluded from the headline)</h2>
<table><tr><th>Company</th><th>Why</th><th class=n>Gap to median</th></tr>{rv}</table>
<h2>Forecast validation on real transactions</h2>
<p>{escape(v['source'])} · {v['weeks']} weeks · {v['origins']} rolling origins, {v['horizon_weeks']}-week horizon, no future data used.</p>
<table><tr><th>Method</th><th class=n>WAPE (lower is better)</th></tr>{meth}</table>
<h2>Method</h2>
<p>DSO = receivables / revenue × 365; DIO = inventory / cost of revenue × 365; DPO = payables / cost of revenue × 365; balances
at the same fiscal-year end as the flows. Peers: finest SEC SIC level with at least {u['min_peers']} companies. Cash to median =
max(0, company days − peer median days) × daily revenue or cost of revenue; top quartile likewise.</p>
<h2>Limitations</h2><ul>{lim}</ul>
<p class="lab">Sources: SEC EDGAR XBRL frames and submissions APIs (retrieved {escape(s['retrieved_at'])}, dataset checksum {s['checksum_sha256'][:16]}…);
UCI Machine Learning Repository, Online Retail II (CC BY 4.0). Not investment advice. No company named here has reviewed these estimates.</p>
</body></html>"""
