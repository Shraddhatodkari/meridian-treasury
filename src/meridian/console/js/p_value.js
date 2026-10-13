// Value at Stake — cross-company working-capital benchmark from SEC filings + forecast validation on real invoices.
import { $, $$, C, api, session, axisMoney, badge, basis, cached, card, chart, classBadge, esc, html, kpi, loading, num, pct, table, toast, usd } from './core.js';
import { head, set } from './p_common.js';

const study = () => cached('bm:study', () => api('/v1/benchmark/study?top=40'));
const fv = () => cached('bm:fv', () => api('/v1/benchmark/forecast-validation'));
const days = (v) => (v === null || v === undefined ? '—' : num(v, 1));

export async function valueAtStake(main) {
  set(main, loading());
  const [s, v] = await Promise.all([study(), fv()]);
  const o = s.summary.opportunity, m = o.median, t = o.top_quartile, u = s.universe;
  set(main, html`
    ${head('Value at Stake', html`${classBadge('PUBLIC_REAL')} Working capital across <b>${u.benchmarked}</b> large US-listed companies ·
      SEC 10-K filings, fiscal years ending mid-2025 to early 2026 · metrics ${basis('CALCULATED')} · opportunity ${basis('ESTIMATED')}`,
      html`<button class="btn primary" id="vs-report">Download board report</button>`)}
    <div class="grid g-4">
      ${kpi('Cash to peer median', usd(m.total_excl_payables_usd), html`receivables ${usd(m.receivables_usd)} · inventory ${usd(m.inventory_usd)}`,
        'Conservative case: each company moves to its peer median on receivables and inventory; companies already better contribute zero')}
      ${kpi('Stretch case (top quartile)', usd(t.total_excl_payables_usd), 'receivables + inventory')}
      ${kpi('Supplier terms (separate)', usd(m.payables_usd), 'only with supplier protection', 'Extending payment terms moves cash from suppliers; pair with supply-chain finance or card programmes')}
      ${kpi('Needs segment review', usd(m.requires_segment_review_usd), `${s.segment_review.length} companies — not in headline`)}
    </div>
    <div class="grid g-2" style="margin-top:16px">
      ${card('Where the cash is — by sector', html`<div id="vs-sec" style="height:420px"></div>`, { hint: 'conservative case, receivables + inventory' })}
      ${card('Forecast validation on real transactions', html`
        <div class="small">${esc(v.source)} · ${v.raw_rows.toLocaleString()} invoice lines · ${v.weeks} weeks · ${v.origins} rolling origins · ${v.horizon_weeks}-week horizon · no future data used</div>
        ${table([{ key: 'm', label: 'Method' }, { key: 'w', label: 'WAPE (lower is better)', num: 1, fmt: (x) => pct(x, 1) }],
          Object.entries(v.methods).map(([k, x]) => ({ m: k.replace(/_/g, ' ') + (k === v.selected ? ' (Meridian)' : ''), w: x.wape })), { maxH: false })}
        <div class="callout ${v.methods[v.selected].wape <= v.methods[v.best_method].wape ? 'good' : 'warn'} small" style="margin-top:8px">
          Meridian's method is ${v.improvement_vs_naive_pct}% more accurate than a naive forecast.
          ${v.best_method !== v.selected ? html`Plain ${v.best_method.replace(/_/g, ' ')} is marginally better (${pct(v.methods[v.best_method].wape, 1)}), so the level adjustment adds no measurable value on this data.` : ''}
          80% band covered ${pct(v.interval.observed_coverage, 0)} of outcomes${v.interval.observed_coverage < 0.75 ? ' — too narrow' : ''}.</div>
        <div id="vs-fv" style="height:200px;margin-top:8px"></div>`, { hint: classBadge('PUBLIC_REAL') })}
    </div>
    ${card('Largest company opportunities', html`<div class="formrow" style="margin-bottom:8px;grid-template-columns:minmax(0,1fr) auto">
        <input class="input" id="vs-q" placeholder="Find a company or ticker (e.g. CAT, Home Depot)"><button class="btn" id="vs-go">Look up</button></div>
      <div id="vs-co"></div>
      ${table([{ key: 'rank_excl_payables', label: '#', num: 1 }, { key: 'name', label: 'Company', fmt: (x, r) => html`<a href="#" data-co="${r.cik}">${x}</a>` },
        { key: 'tickers', label: 'Ticker', fmt: (x) => (x || [])[0] || '' }, { key: 'peer_group', label: 'Peer group', fmt: (x) => `${x.label} (${x.companies})` },
        { key: 'dso', label: 'DSO', num: 1, fmt: days }, { key: 'dio', label: 'DIO', num: 1, fmt: days }, { key: 'dpo', label: 'DPO', num: 1, fmt: days },
        { key: 'opportunity', label: 'To median', num: 1, fmt: (x) => html`<b>${usd(x.median.total_excl_payables_usd)}</b>` },
        { key: 'opportunity', label: 'To top quartile', num: 1, fmt: (x) => usd(x.top_quartile.total_excl_payables_usd) }], s.top_opportunities, { maxH: true })}`,
      { hint: 'hypotheses to test with management, not forecasts', cls: '' })}
    <div class="grid g-2" style="margin-top:16px">
      ${card('Requires segment-level review (excluded from headline)', table([{ key: 'name', label: 'Company' },
        { key: 'review_reason', label: 'Why' }, { key: 'opportunity', label: 'Gap to median', num: 1, fmt: (x) => usd(x.median.requires_segment_review_usd) }],
        s.segment_review, { maxH: true }))}
      ${card('Method & limitations', html`<p class="small">${esc(s.method)}</p><ul class="small" style="padding-left:18px">${s.limitations.map((x) => html`<li>${x}</li>`)}</ul>
        <div class="mini">${u.excluded} companies excluded with reasons · dataset retrieved ${esc(s.retrieved_at)} · checksum ${esc(s.checksum_sha256.slice(0, 16))}…</div>`)}
    </div>`);
  const sec = s.sectors.slice(0, 14).reverse();
  chart($('#vs-sec'), { grid: { left: 190, right: 60, top: 10, bottom: 24 }, tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' }, valueFormatter: (x) => usd(x) },
    xAxis: { type: 'value', ...axisMoney }, yAxis: { type: 'category', data: sec.map((x) => x.sector), axisLabel: { width: 180, overflow: 'truncate' } },
    series: [{ name: 'Cash to median', type: 'bar', data: sec.map((x) => x.opportunity.median.total_excl_payables_usd), itemStyle: { color: C.s1 },
      label: { show: true, position: 'right', formatter: (p) => usd(p.value), fontSize: 10 } }] });
  chart($('#vs-fv'), { grid: { left: 60, right: 10, top: 10, bottom: 24 }, tooltip: { trigger: 'axis' },
    xAxis: { type: 'category', data: v.weekly_actuals.map((x) => x.week) }, yAxis: { type: 'value', axisLabel: { formatter: (x) => `£${Math.round(x / 1000)}k` } },
    series: [{ name: 'Weekly net invoiced (£)', type: 'line', symbol: 'none', data: v.weekly_actuals.map((x) => x.value), lineStyle: { color: C.s3 } }] });
  const show = async (q) => {
    const el = $('#vs-co');
    try {
      const r = await api(`/v1/benchmark/company/${encodeURIComponent(q)}`);
      const c = r.company;
      if (!r.benchmarked) { set(el, html`<div class="callout warn small">${c.name}: not benchmarked — ${c.reason}</div>`); return; }
      const b = c.benchmark, op = c.opportunity.median;
      set(el, card(html`${c.name} ${(c.tickers || [])[0] ? `(${c.tickers[0]})` : ''}`, html`
        <div class="small">Peer group: <b>${c.peer_group.label}</b> (${c.peer_group.companies} companies, ${c.peer_group.level}) · fiscal year end ${c.fiscal_year_end}
          · cash conversion better than ${c.percentile_ccc}% of peers${c.review_reason ? html` · ${badge('segment review', 'warn')} ${c.review_reason}` : ''}</div>
        ${table([{ key: 'k', label: 'Lever' }, { key: 'c', label: 'Company (days)', num: 1 }, { key: 'p25', label: 'Peer top quartile', num: 1 },
          { key: 'med', label: 'Peer median', num: 1 }, { key: 'cash', label: 'Cash to median', num: 1 }],
          [{ k: 'Receivables (DSO)', c: days(c.dso), p25: days(b.dso.p25), med: days(b.dso.median), cash: usd(op.receivables_usd) },
           { k: 'Inventory (DIO)', c: days(c.dio), p25: days(b.dio.p25), med: days(b.dio.median), cash: usd(op.inventory_usd) },
           { k: 'Payables (DPO) — supplier terms', c: days(c.dpo), p25: days(b.dpo.p75), med: days(b.dpo.median), cash: usd(op.payables_usd) }], { maxH: false })}
        <div class="mini" style="margin-top:6px">Sources (${basis('REPORTED')}): ${Object.entries(c.lineage).map(([k, f]) => html`<a href="${f.url}" target="_blank" rel="noopener">${k} ${f.accession}</a> `)}</div>`,
        { hint: html`${usd(op.total_excl_payables_usd)} to median` }));
    } catch (e) { set(el, html`<div class="callout warn small">${e.message}</div>`); }
  };
  $('#vs-go').onclick = () => show($('#vs-q').value.trim());
  $('#vs-q').onkeydown = (e) => { if (e.key === 'Enter') show($('#vs-q').value.trim()); };
  $$('[data-co]', main).forEach((a) => a.onclick = (e) => { e.preventDefault(); show(a.dataset.co); $('#vs-co').scrollIntoView({ behavior: 'smooth' }); });
  $('#vs-report').onclick = async () => {
    try {
      const tok = session.get('token');
      const r = await fetch('/v1/benchmark/report', { headers: tok ? { Authorization: `Bearer ${tok}` } : {} });
      if (!r.ok) throw new Error(`Report failed (${r.status})`);
      const u2 = URL.createObjectURL(await r.blob()); const l = document.createElement('a');
      l.href = u2; l.download = 'Meridian_Value_at_Stake_FY2025.html'; l.click(); URL.revokeObjectURL(u2);
      toast('Board report downloaded — open it and print to PDF if needed');
    } catch (e) { toast(e.message, true); }
  };
}
