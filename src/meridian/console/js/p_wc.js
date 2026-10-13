// Working Capital — driver-based DSO / DPO analytics with drill-down, collections, supplier payments, opportunities, peers.
import {
  $, $$, C, api, axisMoney, badge, basis, cached, card, chart, classBadge, drawer, esc, full, html, kpiM, loading, num, pct,
  table, title, usd,
} from './core.js';
import { head, q, set } from './p_common.js';

const M_ = () => cached('metrics', () => api('/v1/metrics')).then((a) => Object.fromEntries(a.map((m) => [m.metric_id, m])));

export async function workingCapital(main) {
  set(main, loading());
  const [M, wc, col, ep, wca, peers, kp] = await Promise.all([M_(), cached('wcd', () => api('/v1/working-capital/drivers')),
    api('/v1/working-capital/collections?top_n=20'), cached('earlypay', () => api('/v1/working-capital/early-pay')),
    cached('ins:wc_actions', () => api('/v1/insights/wc_actions')), cached('peers', () => api('/v1/benchmarks/peers')),
    cached('kpis', () => api('/v1/kpis'))]);
  const d = wc.dso, ap = wc.ap;
  set(main, html`
    ${head('Working Capital', html`Driver-based receivables and payables intelligence — DSO ${num(d.dso_t0)} → ${num(d.dso_t1)} days since ${d.t0}`)}
    <div class="strip">${kpiM(M.dso)}${kpiM(M.dso_change_90d)}${kpiM(M.dso_cash_impact)}${kpiM(M.overdue_ar)}${kpiM(M.dpo)}${kpiM(M.ap_leakage_annual)}</div>
    <div class="grid g-3" style="margin-top:16px">
      <div class="span-2">${card(html`${q('What is causing the DSO increase?')}DSO bridge — contribution to the change`, html`
        <div class="tabs" id="dtabs">${[['by_customer', 'Customers'], ['by_entity', 'Entities'], ['by_business_unit', 'Business units'], ['by_aging_bucket', 'Aging buckets']].map(([k, l], i) =>
          html`<div class="tab ${i ? '' : 'on'}" data-k="${k}">${l}</div>`)}</div>
        <div class="chart tall" id="w-bridge"></div>
        <div class="mini">${d.formula}. Contributions sum exactly to ${num(d.change_days)} days = ${usd(d.cash_impact_usd)} of cash
          (daily sales ${usd(d.daily_sales_usd)}) ${basis('CALCULATED')}. Click a customer to drill into invoices and payments.</div>`)}</div>
      ${card('DSO trend (point-in-time, monthly)', html`<div class="chart tall" id="w-trend"></div>`)}
    </div>
    <div class="grid g-3" style="margin-top:16px"><div class="span-2">
      ${card(html`${q('Which receivables should we prioritise?')}Collections priorities (ML-predicted delay × financing cost)`, table([
        { key: 'invoice_id', label: 'Invoice' }, { key: 'customer', label: 'Customer' }, { key: 'amount_usd', label: 'Amount', num: 1, fmt: (v) => usd(v) },
        { key: 'due_date', label: 'Due' }, { key: 'predicted_days_late_p50', label: 'P50 late', num: 1, fmt: (v) => `${num(v)} d` },
        { key: 'predicted_days_late_p90', label: 'P90', num: 1, fmt: (v) => `${num(v)} d` },
        { key: 'disputed', label: '', fmt: (v) => (v ? badge('Disputed', 'serious') : '') },
        { key: 'financing_cost_of_delay_usd', label: 'Cost of delay', num: 1, fmt: (v) => usd(v) },
        { key: 'recommended_action', label: 'Action', fmt: (v) => html`<span class="mini">${v}</span>` }], col), { hint: html`${basis('FORECAST')} model ${kp.as_of}` })}</div>
      ${card('Receivables aging', html`<div class="chart" id="w-age"></div>
        <div>${card('Business units', table([{ key: 'label', label: 'BU' }, { key: 'dso_contribution_days', label: 'Δ DSO days', num: 1, fmt: (v) => num(v, 2) },
          { key: 'cash_impact_usd', label: 'Cash', num: 1, fmt: (v) => usd(v) }], d.by_business_unit, { maxH: false }))}</div>
          <div>${card('Open AR by issue month (cohorts)', html`<div class="chart" id="w-coh" style="height:180px"></div>`)}</div>`)}
    </div>
    <div class="grid g-2" style="margin-top:16px">
      ${card(html`${q('Which suppliers create the largest cash opportunity?')}Supplier-payment behaviour — last ${ap.history_days} days`, html`
        <div class="grid g-3">${[['Discounts captured', ap.discounts_captured_usd, 'good'], ['Discounts missed', ap.discounts_missed_usd, 'crit'],
          ['Early payments w/o discount', ap.early_paid_usd, 'warn']].map(([l, v]) => html`<div class="sg"><div class="k">${l}</div><div class="v">${usd(v)}</div></div>`)}</div>
        <div class="small" style="margin:8px 0">Capture rate <b>${pct(ap.capture_rate, 0)}</b> · ${ap.early_payments_without_discount} bills paid ≥5 days early with no discount
          (financing cost ${usd(ap.early_payment_financing_cost_usd)}) · ${ap.late_payments} late payments ${basis('CALCULATED')}</div>
        ${table([{ key: 'supplier', label: 'Supplier' }, { key: 'critical', label: '', fmt: (v) => (v ? badge('critical', 'warn') : '') },
          { key: 'missed_discount_usd', label: 'Missed disc.', num: 1, fmt: (v) => usd(v) }, { key: 'early_no_discount_usd', label: 'Paid early', num: 1, fmt: (v) => usd(v) },
          { key: 'early_financing_cost_usd', label: 'Cost', num: 1, fmt: (v) => usd(v) }], ap.by_supplier, { maxH: false })}`)}
      ${card(html`${q('Which working-capital actions create the most value?')}Opportunity sizing`, html`<div class="chart" id="w-opp"></div>
        ${table([{ key: 'action', label: 'Lever' }, { key: 'annual_value_usd', label: 'Annual value', num: 1, fmt: (v) => usd(v) },
          { key: 'basis', label: 'Basis', fmt: (v) => html`<span class="mini">${v}</span>` }, { key: 'approver', label: 'Approver' }], wca.detail, { maxH: false })}
        <div class="small" style="margin-top:6px">Open-book supplier discounts worth capturing now: <b>${usd(ep.total_saving_usd)}</b> on ${ep.selected.length} bills
          (${ep.skipped_for_liquidity.length} skipped to protect liquidity) ${basis('CALCULATED')}</div>`)}
    </div>
    <div style="margin-top:16px">${card(html`Peer benchmarks — real public filers ${classBadge('PUBLIC_MARKET_DATA')}`, html`
      <div class="grid g-2"><div class="chart tall" id="w-peer"></div><div class="chart tall" id="w-peerhist"></div></div>
      ${table([{ key: 'company', label: 'Company' }, { key: 'fiscal_year_end', label: 'FY end' }, { key: 'revenue_usd', label: 'Revenue', num: 1, fmt: (v) => usd(v) },
        { key: 'dso_days', label: 'DSO', num: 1 }, { key: 'dpo_days', label: 'DPO', num: 1 }, { key: 'dio_days', label: 'DIO', num: 1 },
        { key: 'accession', label: 'Source → filing → concept', fmt: (v, r) => html`<a class="mono" data-peer="${r.cik}" href="javascript:void 0">${v}</a>` }], peers.peers, { maxH: false })}
      <div class="mini">Group (SYNTHETIC) DSO ${num(peers.group_dso_days)} vs peer median ${num(peers.peer_median_dso_days)} → ${usd(peers.cash_release_at_peer_median_usd)}
        if closed ${basis('ESTIMATED')}. ${peers.note}</div>`, { hint: 'SEC EDGAR XBRL company facts (10-K)' })}</div>`);

  let tab = 'by_customer';
  const drawBridge = () => {
    const rows = (tab === 'by_customer' ? [...d.by_customer.filter((r) => !r.is_remainder).slice(0, 12), ...d.by_customer.filter((r) => r.is_remainder)] : d[tab]).slice(0, 13).reverse();
    const lab = (r) => r.customer || r.label;
    window.echarts.getInstanceByDom($('#w-bridge'))?.dispose();
    const ch = chart($('#w-bridge'), { grid: { left: 200, right: 50, top: 10, bottom: 30 },
      tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' }, formatter: (ps) => { const r = rows[ps[0].dataIndex];
        return `<b>${esc(lab(r))}</b><br>${r.dso_contribution_days >= 0 ? '+' : ''}${r.dso_contribution_days} DSO days · ${usd(r.cash_impact_usd)}` +
          (r.avg_days_late_last90 !== undefined ? `<br>days late ${r.avg_days_late_prior90 ?? '—'} → ${r.avg_days_late_last90 ?? '—'} · predicted ${r.predicted_days_late_p50 ?? '—'}` : ''); } },
      xAxis: { type: 'value', name: 'DSO days', nameLocation: 'middle', nameGap: 22, splitLine: { lineStyle: { color: C.grid } } },
      yAxis: { type: 'category', data: rows.map(lab), axisLabel: { width: 190, overflow: 'truncate' } },
      series: [{ type: 'bar', barMaxWidth: 18, data: rows.map((r) => ({ value: r.dso_contribution_days, itemStyle: { color: r.dso_contribution_days >= 0 ? C.s2 : C.s3 } })),
        label: { show: true, position: 'right', formatter: (p) => `${p.value >= 0 ? '+' : ''}${p.value.toFixed(2)}`, fontSize: 10 } }] });
    if (tab === 'by_customer') ch?.on('click', (p) => { if (!rows[p.dataIndex].is_remainder) custDrawer(rows[p.dataIndex]); });
  };
  $$('#dtabs .tab').forEach((t) => t.onclick = () => { tab = t.dataset.k; $$('#dtabs .tab').forEach((x) => x.classList.toggle('on', x === t)); drawBridge(); });
  drawBridge();
  chart($('#w-trend'), { grid: { left: 40, right: 12, top: 10, bottom: 30 }, tooltip: { trigger: 'axis', valueFormatter: (v) => `${v} d` },
    xAxis: { type: 'category', data: d.trend.map((x) => x.date.slice(2, 7)) }, yAxis: { type: 'value', scale: true },
    series: [{ type: 'line', data: d.trend.map((x) => x.dso_days), lineStyle: { color: C.s1, width: 2 }, itemStyle: { color: C.s1 },
      markLine: peers.peer_median_dso_days ? { symbol: 'none', data: [{ yAxis: peers.peer_median_dso_days, label: { formatter: 'peer median' } }], lineStyle: { type: 'dashed', color: C.ink } } : undefined }] });
  const ag = Object.entries(kp.ar_aging_usd);
  chart($('#w-age'), { grid: { left: 50, right: 12, top: 10, bottom: 26 }, tooltip: { trigger: 'axis', valueFormatter: (v) => usd(v) },
    xAxis: { type: 'category', data: ag.map((a) => a[0]) }, yAxis: { type: 'value', ...axisMoney },
    series: [{ type: 'bar', data: ag.map((a, i) => ({ value: a[1], itemStyle: { color: i ? [C.s4, C.s2, C.crit, '#7a1f1f'][i - 1] : C.s1 } })), barMaxWidth: 40 }] });
  chart($('#w-coh'), { grid: { left: 46, right: 8, top: 8, bottom: 22 }, tooltip: { trigger: 'axis', valueFormatter: (v) => usd(v) },
    xAxis: { type: 'category', data: d.open_ar_by_issue_month.map((x) => x.month.slice(2)) }, yAxis: { type: 'value', ...axisMoney },
    series: [{ type: 'bar', data: d.open_ar_by_issue_month.map((x) => x.open_ar_usd), itemStyle: { color: C.s1 }, barMaxWidth: 22 }] });
  const lv = [...wca.detail].sort((a, b) => a.annual_value_usd - b.annual_value_usd);
  chart($('#w-opp'), { grid: { left: 220, right: 50, top: 8, bottom: 26 }, tooltip: { trigger: 'axis', valueFormatter: (v) => usd(v) },
    xAxis: { type: 'value', ...axisMoney }, yAxis: { type: 'category', data: lv.map((x) => x.action), axisLabel: { width: 210, overflow: 'truncate' } },
    series: [{ type: 'bar', data: lv.map((x) => x.annual_value_usd), itemStyle: { color: C.s3, borderRadius: [0, 3, 3, 0] }, barMaxWidth: 20,
      label: { show: true, position: 'right', formatter: (p) => usd(p.value), fontSize: 10 } }] });
  const pd = [...peers.peers].sort((a, b) => a.dso_days - b.dso_days);
  chart($('#w-peer'), { grid: { left: 110, right: 40, top: 20, bottom: 26 }, tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
    xAxis: { type: 'value', axisLabel: { formatter: '{value} d' }, splitLine: { lineStyle: { color: C.grid } } },
    yAxis: { type: 'category', data: [...pd.map((p) => p.company.split(' ')[0].replace(/^(.)(.*)$/, (m, a, b) => a + b.toLowerCase())), 'Our group'] },
    series: [{ type: 'bar', barMaxWidth: 18, data: [...pd.map((p) => ({ value: p.dso_days, itemStyle: { color: C.s1 } })), { value: peers.group_dso_days, itemStyle: { color: C.s2 } }],
      label: { show: true, position: 'right', fontSize: 10 } }] });
  const yrs = [...new Set(peers.peers.flatMap((p) => (p.history || []).map((h) => h.fiscal_year_end.slice(0, 4))))].sort();
  chart($('#w-peerhist'), { grid: { left: 40, right: 10, top: 20, bottom: 50 }, legend: { bottom: 0, type: 'scroll' }, tooltip: { trigger: 'axis' },
    xAxis: { type: 'category', data: yrs }, yAxis: { type: 'value', name: 'DSO days', scale: true },
    series: peers.peers.map((p, i) => ({ name: p.company.split(' ')[0], type: 'line', data: yrs.map((y) => (p.history || []).find((h) => h.fiscal_year_end.startsWith(y))?.dso_days ?? null),
      lineStyle: { width: 1.5 }, itemStyle: { color: [C.s1, C.s2, C.s3, C.s4, C.s5, C.s6, '#7a8699'][i % 7] } })) });
  $$('[data-peer]', main).forEach((a) => a.onclick = () => peerDrawer(peers.peers.find((p) => String(p.cik) === a.dataset.peer)));
}

async function custDrawer(c) {
  const b = drawer(`${c.customer} — drill-down`, '<div class="empty"><span class="spinner"></span></div>');
  const v = await api(`/v1/graph/v3?node=${encodeURIComponent(`CUS:${c.customer_id}`)}&depth=2&limit=400`);
  const inv = v.nodes.filter((n) => n.kind === 'Invoice');
  const pay = Object.fromEntries(v.edges.filter((e) => e.rel === 'SETTLED_BY').map((e) => [e.source, v.nodes.find((n) => n.id === e.target)]));
  b.innerHTML = String(html`<dl class="kv"><dt>Rating · segment</dt><dd>${c.rating} · ${c.segment} (${c.business_unit})</dd>
    <dt>DSO contribution</dt><dd><b>+${c.dso_contribution_days} days</b> = ${full(c.cash_impact_usd)} ${basis('CALCULATED')}</dd>
    <dt>Open / overdue AR</dt><dd>${full(c.open_ar_usd)} / ${full(c.overdue_usd)}</dd>
    <dt>Payment behaviour</dt><dd>${c.avg_days_late_prior90 ?? '—'} → <b>${c.avg_days_late_last90 ?? '—'}</b> days late (settled invoices, prior vs last 90 days)</dd>
    <dt>Model prediction</dt><dd>P50 ${c.predicted_days_late_p50 ?? '—'} days late on open invoices ${basis('FORECAST')}</dd></dl>
    <h4>Invoices (open + settled last 180 days): ${inv.length}</h4>
    ${table([{ key: 'label', label: 'Invoice' }, { key: 'amount_usd', label: 'Amount', num: 1, fmt: (x) => usd(x) }, { key: 'due', label: 'Due' },
      { key: 'open', label: 'Status', fmt: (x, r) => (x ? (r.overdue_days > 0 ? badge(`${r.overdue_days} d overdue`, 'crit') : badge('open', 'info')) : badge(`paid ${pay[r.id]?.days_late ?? ''} d late`, 'muted')) },
      { key: 'disputed', label: '', fmt: (x) => (x ? badge('disputed', 'serious') : '') }], inv.sort((a, z) => (z.open - a.open) || z.amount_usd - a.amount_usd).slice(0, 40), { maxH: false })}
    <div class="mini">Traversal: Customer → Invoice → Payment (graph layer, application data).</div>`);
}

function peerDrawer(p) {
  const b = drawer(`${p.company} — source lineage`, '');
  b.innerHTML = String(html`<div class="callout small">${classBadge('PUBLIC_MARKET_DATA')} SEC EDGAR XBRL company facts · CIK ${p.cik} ·
    filing ${p.accession} filed ${p.filed}</div>
    <h4>Source → dataset → date → transformation → calculation</h4>
    ${table([{ key: 'k', label: 'Input' }, { key: 'concept', label: 'XBRL concept', fmt: (v) => html`<span class="mono">${v}</span>` },
      { key: 'value_usd', label: 'Value', num: 1, fmt: (v) => usd(v) }, { key: 'period_end', label: 'Period end' }, { key: 'form', label: 'Form' },
      { key: 'accession', label: 'Accession', fmt: (v) => html`<span class="mono">${v}</span>` }],
      Object.entries(p.lineage || {}).filter(([, v]) => v).map(([k, v]) => ({ k, ...v })), { maxH: false })}
    <dl class="kv">${Object.entries(p.formulas || {}).map(([k, f]) => html`<dt>${title(k)}</dt><dd><span class="mono">${f}</span> = <b>${p[k] ?? '—'}</b></dd>`)}</dl>
    <h4>Trend</h4>${table([{ key: 'fiscal_year_end', label: 'FY end' }, { key: 'dso_days', label: 'DSO', num: 1 }, { key: 'dpo_days', label: 'DPO', num: 1 },
      { key: 'dio_days', label: 'DIO', num: 1 }, { key: 'revenue_usd', label: 'Revenue', num: 1, fmt: (v) => usd(v) }], p.history || [], { maxH: false })}
    <div class="mini">URL: <span class="mono">${p.source_url}</span></div>`);
}
