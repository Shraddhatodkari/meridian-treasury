// Liquidity Intelligence · Cash & Entity Network · Forecast Studio · Scenario Lab
import {
  $, $$, C, api, axisMoney, badge, basis, cached, card, chart, classBadge, drawer, esc, fanChart, full, html, kpi, kpiM,
  loading, num, pct, statusBadge, table, title, toast, usd, waterfall,
} from './core.js';
import { head, q, set, weeksOf } from './p_common.js';
import { params } from './app.js';

const M_ = () => cached('metrics', () => api('/v1/metrics')).then((a) => Object.fromEntries(a.map((m) => [m.metric_id, m])));
const srcBadge = (sys, date) => html`<span class="row small" style="gap:6px">${classBadge('PUBLIC_MARKET_DATA')}<span class="muted">${sys} · ${date}</span></span>`;

// ================================================================= LIQUIDITY INTELLIGENCE
export async function liquidity(main) {
  set(main, loading());
  const [M, ov, debt, mk] = await Promise.all([M_(), cached('liqov', () => api('/v1/liquidity/overview')),
    cached('debt', () => api('/v1/debt')), cached('market', () => api('/v1/market'))]);
  const b = ov.bridge, f = ov.forward, conc = ov.concentration, fx = ov.fx;
  set(main, html`
    ${head('Liquidity Intelligence', html`What moved cash, where it will go, where it sits, and what it costs to fund — ${b.transactions} bank-statement entries, real market rates`)}
    <div class="strip">${kpiM(M.group_cash)}${kpiM(M.deployable_liquidity)}${kpiM(M.bank_concentration_max, 'Largest bank share')}
      ${kpiM(M.fx_var_1m)}${kpiM(M.revolver_rate)}${kpiM(M.rate_sensitivity_100bps)}</div>
    <div class="grid g-2" style="margin-top:16px">
      ${card(html`${q('Why did liquidity change?')}Cash bridge — last 8 weeks`, html`<div class="chart tall" id="l-hist"></div>
        <div class="mini">${b.basis}. FX: ${b.fx_basis}.</div>`, { hint: `${b.window_start} → ${b.window_end}` })}
      ${card(html`${q('Where will cash go?')}Forward bridge — next 13 weeks (expected)`, html`<div class="chart tall" id="l-fwd"></div>
        <div class="mini">${f.basis}. Week-13 P10 / P50 / P90: ${usd(f.p10_close_usd)} / ${usd(f.p50_close_usd)} / ${usd(f.p90_close_usd)}.</div>`)}
    </div>
    <div class="grid g-2" style="margin-top:16px">
      ${card(html`${q('Where is cash concentrated?')}Bank counterparty exposure vs TP-5.2 limits`, html`<div class="chart" id="l-bank"></div>
        ${table([{ key: 'bank', label: 'Bank' }, { key: 'rating', label: 'Rating' }, { key: 'accounts', label: 'Accts', num: 1 },
          { key: 'cash_usd', label: 'Cash', num: 1, fmt: (v) => usd(v) }, { key: 'share', label: 'Share', num: 1, fmt: (v) => pct(v) },
          { key: 'limit_share', label: 'Limit', num: 1, fmt: (v) => pct(v, 0) },
          { key: 'breach', label: '', fmt: (v, r) => (v ? badge(`over by ${usd(r.excess_usd)}`, 'crit', '✕ ') : badge('within', 'good', '✓ ')) }], conc.banks, { maxH: false })}
        <div class="mini">Entity concentration HHI ${num(conc.entity_hhi, 3)} (≈ ${conc.effective_entities} equally-sized entities).</div>`)}
      ${card(html`${q('What is our currency exposure?')}Net exposure and 1-month 95% VaR`, html`<div class="chart" id="l-fx"></div>
        ${table([{ key: 'currency', label: 'Ccy' }, { key: 'cash_usd', label: 'Cash', num: 1, fmt: (v) => usd(v) },
          { key: 'open_ar_usd', label: '+ AR', num: 1, fmt: (v) => usd(v) }, { key: 'open_ap_usd', label: '− AP', num: 1, fmt: (v) => usd(v) },
          { key: 'net_exposure_usd', label: 'Net', num: 1, fmt: (v) => html`<b>${usd(v)}</b>` },
          { key: 'annualised_vol', label: 'Vol (ECB 90d)', num: 1, fmt: (v) => pct(v) }, { key: 'var95_1m_usd', label: 'VaR 95% 1m', num: 1, fmt: (v) => usd(v) }],
          fx.currencies.filter((c) => c.currency !== 'USD'))}<div class="mini">${fx.method}</div>`, { hint: html`${basis('ESTIMATED')}` })}
    </div>
    <h3 style="margin:22px 0 10px">Funding, debt and market rates</h3>
    <div class="grid g-3">
      <div class="span-2">${card(html`${q('What does our funding cost, and when does it mature?')}Debt stack`, html`
        ${table([{ key: 'name', label: 'Facility' }, { key: 'type', label: 'Type' },
          { key: 'drawn_usd', label: 'Drawn', num: 1, fmt: (v) => usd(v) }, { key: 'rate', label: 'Rate', num: 1, fmt: (v) => pct(v, 2) },
          { key: 'rate_source', label: 'Rate source', fmt: (v) => html`<span class="mini">${v}</span>` },
          { key: 'annual_interest_usd', label: 'Interest / yr', num: 1, fmt: (v) => usd(v) }, { key: 'maturity_date', label: 'Maturity' },
          { key: 'in_refinancing_window', label: 'DP-2.1', fmt: (v, r) => (v ? badge(`${r.months_to_maturity} mo — refinance`, 'crit') : badge(`${r.months_to_maturity} mo`, 'muted')) }],
          debt.facilities, { maxH: false })}
        <div class="grid g-2" style="margin-top:10px"><div class="chart" id="l-ladder"></div><div>
          ${debt.refinancing.map((r) => html`<div class="callout warn small"><b>${r.name}</b> matures ${r.maturity_date} (${r.months_to_maturity} months).
            Estimated 5-year fixed refinancing rate <b>${pct(r.estimated_new_5y_fixed_rate, 2)}</b> vs ${pct(r.current_rate, 2)} today
            → <b>${usd(r.estimated_annual_cost_change_usd)}</b> a year ${basis('ESTIMATED')}<div class="mini" style="margin-top:4px">${r.assumption}</div></div>`)}
          <div class="small" style="margin-top:8px">Fixed share <b>${pct(debt.fixed_share, 0)}</b> (DP-3.1 band ${pct(debt.fixed_share_policy[0], 0)}–${pct(debt.fixed_share_policy[1], 0)})
            ${debt.fixed_share_compliant ? badge('within band', 'good', '✓ ') : badge('outside band', 'crit')} · +100 bps on floating debt:
            <b>${usd(debt.rate_sensitivity_100bps_annual_usd)}</b>/yr ${basis('CALCULATED')}</div></div></div>`)}</div>
      ${card('Market rates (real public data)', html`
        <div class="chart" id="l-sofr"></div>${srcBadge('NY Fed SOFR & EFFR', mk.sofr?.effective_date)}
        <div class="chart" id="l-ust" style="margin-top:8px"></div>${srcBadge('U.S. Treasury par curve', mk.ust_curve?.curve_date)}
        <div class="small" style="margin-top:8px">BBB OAS <b>${mk.credit_spread?.latest?.value}%</b> (${mk.credit_spread?.latest?.date}, FRED / ICE BofA) ·
          €STR <b>${(mk.estr || []).slice(-1)[0]?.rate_pct}%</b> (ECB) · FOMC target ${mk.effr?.target_from_pct}–${mk.effr?.target_to_pct}%</div>`)}
    </div>`);
  waterfall($('#l-hist'), [{ label: 'Opening cash', value: b.opening_usd, kind: 'total', basis: 'CALCULATED' },
    ...b.drivers.map((d) => ({ label: d.driver, value: d.value_usd, kind: 'delta', basis: d.category === 'FX' ? 'CALCULATED' : 'OBSERVED' })),
    { label: 'Closing cash', value: b.closing_usd, kind: 'total', basis: 'OBSERVED' }]);
  waterfall($('#l-fwd'), [{ label: 'Cash today', value: f.opening_usd, kind: 'total' },
    ...f.drivers.map((d) => ({ label: d.driver.replace('Collections — ', 'Coll. '), value: d.value_usd, kind: 'delta', basis: 'FORECAST' })),
    { label: 'Week 13 (expected)', value: f.expected_close_usd, kind: 'total', basis: 'FORECAST' }]);
  chart($('#l-bank'), { grid: { left: 110, right: 30, top: 8, bottom: 26 }, tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' }, valueFormatter: (v) => pct(v) },
    xAxis: { type: 'value', max: 1, axisLabel: { formatter: (v) => `${v * 100}%` }, splitLine: { lineStyle: { color: C.grid } } },
    yAxis: { type: 'category', data: conc.banks.map((x) => x.bank).reverse() },
    series: [{ type: 'bar', data: conc.banks.map((x) => ({ value: x.share, itemStyle: { color: x.breach ? C.crit : C.s1 } })).reverse(), barMaxWidth: 20,
      markLine: { symbol: 'none', data: [{ xAxis: 0.4, label: { formatter: '40% limit' } }, { xAxis: 0.15, label: { formatter: '15% (<A-)' } }],
        lineStyle: { type: 'dashed', color: C.ink } } }] });
  const fxs = fx.currencies.filter((c) => c.currency !== 'USD').slice(0, 10);
  chart($('#l-fx'), { grid: { left: 50, right: 16, top: 10, bottom: 40 }, legend: { bottom: 0 }, tooltip: { trigger: 'axis', valueFormatter: (v) => usd(v) },
    xAxis: { type: 'category', data: fxs.map((c) => c.currency) }, yAxis: { type: 'value', ...axisMoney },
    series: [{ name: 'Net exposure', type: 'bar', data: fxs.map((c) => c.net_exposure_usd), itemStyle: { color: C.s1 }, barMaxWidth: 26 },
      { name: 'VaR 95% 1m', type: 'bar', data: fxs.map((c) => c.var95_1m_usd), itemStyle: { color: C.s2 }, barMaxWidth: 26 }] });
  chart($('#l-ladder'), { grid: { left: 50, right: 10, top: 10, bottom: 26 }, tooltip: { trigger: 'axis', valueFormatter: (v) => usd(v) },
    xAxis: { type: 'category', data: debt.maturity_ladder.map((x) => x.year) }, yAxis: { type: 'value', ...axisMoney },
    series: [{ type: 'bar', data: debt.maturity_ladder.map((x) => x.amount_usd), itemStyle: { color: C.s4, borderRadius: [3, 3, 0, 0] }, barMaxWidth: 34 }] });
  const sh = mk.sofr_history || [];
  chart($('#l-sofr'), { grid: { left: 40, right: 10, top: 10, bottom: 30 }, tooltip: { trigger: 'axis', valueFormatter: (v) => `${v}%` },
    xAxis: { type: 'category', data: sh.map((x) => x.date), axisLabel: { formatter: (v) => v.slice(2, 7) } }, yAxis: { type: 'value', scale: true },
    series: [{ name: 'SOFR %', type: 'line', data: sh.map((x) => x.rate_pct), symbol: 'none', lineStyle: { color: C.s1, width: 2 } }] });
  const cv = mk.ust_curve?.curve || [];
  chart($('#l-ust'), { grid: { left: 40, right: 10, top: 10, bottom: 30 }, tooltip: { trigger: 'axis', valueFormatter: (v) => `${v}%` },
    xAxis: { type: 'category', data: cv.map((x) => x.tenor) }, yAxis: { type: 'value', scale: true },
    series: [{ name: 'UST yield %', type: 'line', data: cv.map((x) => x.yield_pct), lineStyle: { color: C.s3, width: 2 }, itemStyle: { color: C.s3 } }] });
}

// ================================================================= CASH & ENTITY NETWORK
const KIND = { Organization: C.ink, Entity: C.s1, Bank: C.s6, BankAccount: '#7fa8e0', DebtFacility: C.s2, Covenant: '#b5502b',
  Customer: C.s5, BusinessUnit: C.s4, Risk: C.crit, Recommendation: C.s3, Supplier: '#98a2b3', Country: '#c3cad6', Currency: '#c3cad6' };
const QUERIES = [['excess_cash', 'Which entities have excess cash?'], ['concentration', 'Where is liquidity concentrated?'],
  ['overdue_drivers', 'Which customers drive overdue receivables?'], ['supplier_opportunities', 'Which suppliers create the largest cash opportunity?'],
  ['refinancing', 'Which facilities create refinancing risk?'], ['covenants', 'Which covenants are closest to breach?'],
  ['source_dependencies', 'Which decisions depend on a data source?'], ['impact', 'What is affected by a financial event?']];

export async function network(main) {
  set(main, loading());
  const kinds = ['Organization', 'Entity', 'Bank', 'BankAccount', 'DebtFacility', 'Covenant', 'Customer', 'BusinessUnit', 'Risk', 'Recommendation'];
  const on = new Set(['Organization', 'Entity', 'Bank', 'DebtFacility', 'Covenant', 'Risk', 'Recommendation', 'BusinessUnit']);
  const stats = (await api('/v1/graph/v3?kinds=Organization')).stats;
  set(main, html`
    ${head('Cash & Entity Network', html`One relationship graph built from application data: ${num(stats.nodes, 0)} nodes, ${num(stats.edges, 0)} edges —
      ${Object.entries(stats.by_kind).map(([k, v]) => `${v} ${k}`).join(' · ')}`)}
    <div class="grid g-3"><div class="span-2">${card('Financial relationship graph', html`
      <div class="row small" id="kinds">${kinds.map((k) => html`<label class="chip"><input type="checkbox" value="${k}" ${on.has(k) ? 'checked' : ''}>
        <span style="display:inline-block;width:9px;height:9px;border-radius:50%;background:${KIND[k]}"></span> ${k}</label>`)}</div>
      <div class="chart xl" id="n-g"></div>`, { hint: 'Click a node: properties, relationships and downstream impact' })}</div>
      ${card('Graph questions', html`<div class="tabs" id="qtabs" style="flex-wrap:wrap">${QUERIES.map(([k], i) => html`<div class="tab ${i ? '' : 'on'}" data-q="${k}">${title(k)}</div>`)}</div>
        <div id="q-arg"></div><div id="q-out"></div>`)}</div>`);
  const draw = async (node) => {
    const ks = $$('#kinds input:checked').map((i) => i.value).join(',');
    const v = await api(`/v1/graph/v3?kinds=${ks}${node ? `&node=${encodeURIComponent(node)}&depth=1` : ''}&limit=260`);
    window.echarts.getInstanceByDom($('#n-g'))?.dispose();
    const deg = {};
    v.edges.forEach((e) => { deg[e.source] = (deg[e.source] || 0) + 1; deg[e.target] = (deg[e.target] || 0) + 1; });
    const ch = chart($('#n-g'), { tooltip: { formatter: (p) => (p.dataType === 'edge' ? esc(p.data.rel) : `<b>${esc(p.data.label)}</b><br>${esc(p.data.kind)}`) },
      series: [{ type: 'graph', layout: 'force', roam: true, draggable: true, force: { repulsion: 160, edgeLength: [40, 120], gravity: 0.07 },
        label: { show: true, fontSize: 10, formatter: (p) => (['Entity', 'Bank', 'DebtFacility', 'Organization', 'BusinessUnit'].includes(p.data.kind) ? (p.data.kind === 'Entity' ? p.data.id : p.data.label.slice(0, 22)) : '') },
        lineStyle: { color: '#c3cad6', width: 1, curveness: 0.06 }, emphasis: { focus: 'adjacency' },
        data: v.nodes.map((n) => ({ ...n, name: n.id, symbolSize: n.kind === 'Organization' ? 26 : n.kind === 'Entity' ? 10 + Math.min(18, (n.cash_usd || 0) / 4e6)
          : n.kind === 'DebtFacility' ? 16 : 6 + Math.min(10, deg[n.id] || 0), itemStyle: { color: KIND[n.kind] || '#98a2b3', borderColor: '#fff', borderWidth: 1 } })),
        links: v.edges.map((e) => ({ source: e.source, target: e.target, rel: e.rel })) }] });
    ch?.on('click', async (p) => { if (p.dataType !== 'node') return; nodeDrawer(p.data); });
  };
  const nodeDrawer = async (n) => {
    const [nb, imp] = await Promise.all([api(`/v1/graph/v3?node=${encodeURIComponent(n.id)}&depth=1&limit=80`),
      api(`/v1/graph/query/impact?arg=${encodeURIComponent(n.id)}`)]);
    const props = Object.entries(n).filter(([k]) => !['name', 'symbolSize', 'itemStyle', 'id', 'label', 'kind'].includes(k));
    const b = drawer(`${n.kind}: ${n.label}`, '');
    b.innerHTML = String(html`<dl class="kv"><dt>Node</dt><dd class="mono">${n.id}</dd>${props.map(([k, v]) => html`<dt>${title(k)}</dt>
      <dd>${typeof v === 'number' && /usd/.test(k) ? full(v) : String(v)}</dd>`)}</dl>
      <h4>Relationships (${nb.edges.length})</h4>${table([{ key: 'source', label: 'From' }, { key: 'rel', label: 'Relationship' }, { key: 'target', label: 'To' }], nb.edges.slice(0, 40), { maxH: false })}
      <h4>Downstream impact if this changes (${imp.rows.length})</h4>${table([{ key: 'kind', label: 'Kind' }, { key: 'label', label: 'Affected' }, { key: 'depth', label: 'Hops', num: 1 }], imp.rows.slice(0, 30), { maxH: false })}`);
  };
  $$('#kinds input').forEach((i) => i.onchange = () => draw());
  draw(params().get('n'));
  const runQ = async (k) => {
    $$('#qtabs .tab').forEach((t) => t.classList.toggle('on', t.dataset.q === k));
    const needs = { source_dependencies: ['market.sofr', 'market.fx', 'company.receivables', 'company.bank_balances', 'market.ust_curve', 'model.ar_delay'],
      impact: ['market.sofr', 'company.receivables', 'CTRY:CN', 'CUS:C0089', 'TLA-2027', 'E12'] }[k];
    $('#q-arg').innerHTML = needs ? String(html`<select class="input" id="q-sel" style="margin-bottom:8px">${needs.map((x) => html`<option>${x}</option>`)}</select>`) : '';
    const go = async () => {
      const r = await api(`/v1/graph/query/${k}${needs ? `?arg=${encodeURIComponent($('#q-sel').value)}` : ''}`);
      const rows = r.rows || [];
      const cols = rows.length ? Object.keys(rows[0]).filter((c) => !['id'].includes(c)).slice(0, 6) : [];
      set($('#q-out'), html`<div class="mini" style="margin-bottom:6px">${QUERIES.find((x) => x[0] === k)[1]} · traversal: <b>${r.path || ''}</b></div>
        ${table(cols.map((c) => ({ key: c, label: title(c), num: typeof rows[0][c] === 'number',
          fmt: (v) => (typeof v === 'number' && /usd/.test(c) ? usd(v) : typeof v === 'number' && /share|headroom/.test(c) ? pct(v) : Array.isArray(v) ? v.join(', ') : typeof v === 'boolean' ? (v ? 'yes' : '') : v) })), rows.slice(0, 25), { maxH: false, empty: 'No matches' })}`);
    };
    if (needs) $('#q-sel').onchange = go;
    go();
  };
  $$('#qtabs .tab').forEach((t) => t.onclick = () => runQ(t.dataset.q));
  runQ('excess_cash');
}

// ================================================================= FORECAST STUDIO
export async function forecast(main) {
  set(main, loading());
  const [fc, ents, bt, sc, M] = await Promise.all([cached('fc:base', () => api('/v1/forecast')), cached('entities', () => api('/v1/entities')),
    cached('backtest', () => api('/v1/backtest')), cached('scenarios', () => api('/v1/scenarios')), M_()]);
  const mgmt = 300_000_000;
  set(main, html`
    ${head('Forecast Studio', 'Direct-method 13-week forecast · Monte Carlo over ML-predicted customer payment dates · P10 / P50 / P90 · scenario overlays · forecast vs actual')}
    <div class="strip">${kpiM(M.min_covenant_headroom_p10)}${kpiM(M.funding_requirement)}${kpiM(M.week13_cash_p50)}
      ${kpiM(M.forecast_wape, 'Forecast error (back-test)')}${kpi('ERP due-date error', pct(bt.wape_erp_due_date), 'same back-test')}${kpi('P10–P90 weekly coverage', pct(bt.p10_p90_weekly_coverage, 0), 'back-test')}</div>
    <div class="row" style="margin:14px 0 10px"><label class="lbl" style="margin:0">View</label>
      <select class="input" id="fc-ent" style="width:320px"><option value="">Group liquidity (cash + undrawn RCF)</option>
      ${ents.map((e) => html`<option value="${e.entity_id}">${e.entity_id} · ${e.name}</option>`)}</select>
      <label class="row small"><input type="checkbox" id="fc-ov" checked> Overlay preset scenarios</label></div>
    <div class="grid g-3"><div class="span-2">${card(html`${q('What is our 13-week liquidity risk?')}Liquidity forecast`, html`<div class="chart tall" id="fc-fan"></div>`, { hint: 'P10–P90 band = Monte-Carlo customer payment timing only; dashed overlays = preset macro scenarios' })}</div>
      ${card(html`${q('Do we need funding?')}Funding requirement`, html`<div class="chart" id="fc-fund"></div>
        <div class="mini">Weekly P10 liquidity minus the TP-3.3 management floor (${usd(mgmt)}). Negative = funding needed ${basis('FORECAST')}.</div>`)}</div>
    <div class="grid g-2" style="margin-top:16px">
      ${card(html`${q('How accurate is the forecast?')}Forecast vs actual — point-in-time back-test`, html`<div class="chart tall" id="fc-bt"></div>
        <div class="mini">${bt.method}: ledger rewound to ${bt.t0}; ${bt.open_invoices_at_t0} open invoices re-forecast with a model re-trained only on data
          available then; compared with actual settlements. WAPE ${pct(bt.wape_model)} vs ${pct(bt.wape_erp_due_date)} for the ERP due-date convention.</div>`)}
      ${card('Weekly flows by driver (group, expected)', html`<div class="chart tall" id="fc-flows"></div>`)}
    </div>`);
  const drawFan = async () => {
    const id = $('#fc-ent').value;
    window.echarts.getInstanceByDom($('#fc-fan'))?.dispose();
    if (id) {
      const e = await api(`/v1/forecast/entities/${id}`);
      fanChart($('#fc-fan'), ['Today', ...e.week_starts.map((d) => `W/c ${d.slice(5)}`)], e.cash_usd, { covenant: e.buffer_usd, name: `${id} cash P50` });
      return;
    }
    const ch = fanChart($('#fc-fan'), weeksOf(fc), fc.liquidity_usd, { covenant: fc.covenant_min_liquidity_usd, name: 'Base P50' });
    if (!ch || !$('#fc-ov').checked) return;
    const extra = await Promise.all(['global_stress', 'eu_slowdown'].map((s) => cached(`fc:${s}`, () => api(`/v1/forecast?scenario=${s}`))));
    const cols = [C.s2, C.s4];
    ch.setOption({ series: [...ch.getOption().series, ...extra.map((x, i) => ({ name: `${x.scenario.slice(0, 28)} P10`, type: 'line', data: x.liquidity_usd.p10,
      symbol: 'none', lineStyle: { color: cols[i], width: 1.6, type: 'dashed' }, itemStyle: { color: cols[i] } })),
      { name: 'Management floor', type: 'line', data: weeksOf(fc).map(() => mgmt), symbol: 'none', lineStyle: { type: 'dotted', color: '#e08a1e' }, itemStyle: { color: '#e08a1e' } }],
    legend: { bottom: 0, type: 'scroll' } });
  };
  $('#fc-ent').onchange = drawFan; $('#fc-ov').onchange = drawFan; drawFan();
  const gap = fc.liquidity_usd.p10.map((v) => v - mgmt);
  chart($('#fc-fund'), { grid: { left: 56, right: 10, top: 10, bottom: 30 }, tooltip: { trigger: 'axis', valueFormatter: (v) => usd(v) },
    xAxis: { type: 'category', data: weeksOf(fc).map((x) => x.replace('W/c ', '')) }, yAxis: { type: 'value', ...axisMoney },
    series: [{ type: 'bar', data: gap.map((v) => ({ value: v, itemStyle: { color: v < 0 ? C.crit : C.s3 } })), barMaxWidth: 20 }] });
  const wk = bt.actual_usd.map((_, i) => `W${i + 1}`);
  const yMax = Math.ceil((1.3 * Math.max(...bt.actual_usd, ...bt.model_p90_usd)) / 1e7) * 1e7;
  chart($('#fc-bt'), { grid: { left: 56, right: 12, top: 16, bottom: 50 }, legend: { bottom: 0, data: ['Actual', 'Meridian forecast', 'P10–P90', 'ERP due-date assumption'] },
    tooltip: { trigger: 'axis', valueFormatter: (v) => usd(v) }, xAxis: { type: 'category', data: wk, boundaryGap: false },
    yAxis: { type: 'value', max: yMax, ...axisMoney }, series: [
      { name: 'P10', type: 'line', data: bt.model_p10_usd, stack: 'b', lineStyle: { opacity: 0 }, symbol: 'none', silent: true },
      { name: 'P10–P90', type: 'line', data: bt.model_p90_usd.map((v, i) => v - bt.model_p10_usd[i]), stack: 'b', lineStyle: { opacity: 0 }, areaStyle: { color: C.band }, itemStyle: { color: C.band }, symbol: 'none', silent: true },
      { name: 'Actual', type: 'line', data: bt.actual_usd, lineStyle: { color: C.ink, width: 2 }, itemStyle: { color: C.ink }, symbolSize: 7 },
      { name: 'Meridian forecast', type: 'line', data: bt.model_mean_usd, lineStyle: { color: C.s1, width: 2 }, itemStyle: { color: C.s1 } },
      { name: 'ERP due-date assumption', type: 'line', data: bt.erp_due_date_usd, lineStyle: { color: C.s2, width: 2, type: 'dashed' }, itemStyle: { color: C.s2 } }] });
  const cats = Object.keys(fc.flows_p50_usd), colors = [C.s1, C.s3, C.s2, C.s4, C.s5, C.s6];
  chart($('#fc-flows'), { grid: { left: 56, right: 12, top: 16, bottom: 50 }, legend: { bottom: 0, type: 'scroll' },
    tooltip: { trigger: 'axis', valueFormatter: (v) => usd(v) }, xAxis: { type: 'category', data: fc.week_starts.map((d) => d.slice(5)) },
    yAxis: { type: 'value', ...axisMoney }, series: cats.map((c, i) => ({ name: c.replace('AR_open', 'AR open book').replace('AR_new', 'AR new billing'), type: 'bar', stack: 'f',
      data: fc.flows_p50_usd[c], itemStyle: { color: colors[i % colors.length], borderColor: '#fff', borderWidth: 1 } })) });
  void sc;
}

// ================================================================= SCENARIO LAB
export async function scenarios(main) {
  set(main, loading());
  const [shocks, presets] = await Promise.all([cached('ins:shocks', () => api('/v1/insights/shocks')), cached('scenarios', () => api('/v1/scenarios'))]);
  set(main, html`
    ${head('Scenario Lab', 'Re-forecast liquidity and re-test covenants under collections, billing, rate, FX, EBITDA and hazard shocks — same seed and paths as the base case')}
    <div class="grid g-3">
      ${card('Scenario builder', html`<div class="stack">
        ${[['dly', 'Customers pay slower (%)', 0, 60, 20], ['bill', 'New billing change (%)', -30, 10, 0], ['bps', 'Rate shock (bps)', 0, 300, 100],
          ['eur', 'EUR vs USD (%)', -20, 10, -5], ['gbp', 'GBP vs USD (%)', -20, 10, 0], ['ebitda', 'LTM EBITDA change (%) — covenants', -50, 10, -15]].map(([id, l, mn, mx, v]) => html`<div>
          <label class="lbl">${l}</label><div class="range-row"><input type="range" id="s-${id}" min="${mn}" max="${mx}" value="${v}" step="1">
          <span class="mono" id="s-${id}-v">${v}</span></div></div>`)}
        <label class="row small"><input type="checkbox" id="s-haz"> Include material hazard events (ORANGE/RED + scenario library)</label>
        <button class="btn primary" id="s-run">Run scenario</button></div>`)}
      <div class="span-2">${card(html`${q('What happens if collections slow?')}Liquidity under the scenario`, html`<div class="chart tall" id="s-fan"></div><div id="s-res"></div>`)}</div>
    </div>
    <div class="grid g-2" style="margin-top:16px">
      ${card('Covenants under the scenario', html`<div id="s-cov"></div>`, { hint: html`${basis('SCENARIO')}` })}
      ${card('Sensitivity: change in week-13 group cash vs base', html`<div class="chart tall" id="s-tornado"></div><div class="small muted">${shocks.headline}</div>`, { hint: 'P50 · single-factor shocks' })}
    </div>
    <div style="margin-top:16px">${card('Preset scenario comparison', table([{ key: 'scenario', label: 'Scenario' },
      { key: 'min_covenant_headroom_p10_usd', label: 'Min headroom P10', num: 1, fmt: (v) => usd(v) },
      { key: 'headroom_change_vs_base_usd', label: 'vs base', num: 1, fmt: (v) => usd(v) },
      { key: 'week13_cash_change_vs_base_usd', label: 'Wk-13 cash vs base', num: 1, fmt: (v) => usd(v) },
      { key: 'covenant_breach_week', label: 'Covenant breach', fmt: (v) => (v === null ? badge('None', 'good', '✓ ') : badge(`Week ${v}`, 'crit')) },
      { key: 'entities_below_buffer', label: 'Entities < buffer', num: 1, fmt: (v) => v.length }], presets.comparison, { maxH: false }))}</div>`);
  $$('input[type=range]', main).forEach((r) => r.oninput = () => { $(`#${r.id}-v`).textContent = r.value; });
  const run = async () => {
    const v = (id) => Number($(`#s-${id}`).value);
    const sc = { name: 'Custom scenario', collection_delay_pct: v('dly') / 100, billing_change_pct: { '*': v('bill') / 100 }, rate_shock_bps: v('bps'),
      fx_shock_pct: { EUR: v('eur') / 100, GBP: v('gbp') / 100 }, event_ids: $('#s-haz').checked ? ['EV-CN-TC-2026-31', 'EV-IN-FL-2026-12', 'EV-MX-EQ-2026-04'] : [] };
    const [r, cov] = await Promise.all([api('/v1/forecast', { method: 'POST', body: sc }),
      api(`/v1/covenants?scenario=${encodeURIComponent(JSON.stringify(sc))}&ebitda_change_pct=${v('ebitda') / 100}`)]);
    window.echarts.getInstanceByDom($('#s-fan'))?.dispose();
    fanChart($('#s-fan'), weeksOf(r), r.liquidity_usd, { covenant: r.covenant_min_liquidity_usd, name: 'Scenario liquidity P50' });
    set($('#s-res'), html`<div class="row" style="margin-top:8px">Minimum P10 headroom <b>${usd(r.min_covenant_headroom_p10_usd)}</b> ${basis('SCENARIO')}
      ${r.covenant_breach_week === null ? badge('No liquidity-covenant breach', 'good', '✓ ') : badge(`Breach in week ${r.covenant_breach_week}`, 'crit', '✕ ')}
      · ${r.entities.filter((e) => e.weeks_p50_below_buffer).length} entities below buffer</div>`);
    set($('#s-cov'), table([{ key: 'name', label: 'Covenant' }, { key: 'clause_ref', label: 'Clause' },
      { key: 'value_forward_worst', label: 'Worst (P10 path)', num: 1, fmt: (x, rr) => (rr.unit === 'USD' ? usd(x) : `${x}x`) },
      { key: 'threshold', label: 'Threshold', num: 1, fmt: (x, rr) => `${rr.direction} ${rr.unit === 'USD' ? usd(x) : `${x}x`}` },
      { key: 'headroom_forward_pct', label: 'Headroom', num: 1, fmt: (x) => pct(x) }, { key: 'status', label: 'Status', fmt: (x) => statusBadge(x) }],
      cov.covenants, { maxH: false }));
  };
  $('#s-run').onclick = () => run().catch((e) => toast(e.message, true));
  run();
  const rows = shocks.detail.slice(0, -1).sort((a, b) => b.week13_cash_change_vs_base_usd - a.week13_cash_change_vs_base_usd);
  const lo = Math.min(...rows.map((r) => r.week13_cash_change_vs_base_usd));
  chart($('#s-tornado'), { grid: { left: 170, right: 24, top: 10, bottom: 30 },
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' }, valueFormatter: (v) => usd(v) },
    xAxis: { type: 'value', ...axisMoney }, yAxis: { type: 'category', data: rows.map((r) => r.scenario), axisLabel: { width: 150, overflow: 'truncate' } },
    series: [{ type: 'bar', barMaxWidth: 34, itemStyle: { color: C.s2, borderRadius: 4 },
      data: rows.map((r) => ({ value: r.week13_cash_change_vs_base_usd, label: { show: true, formatter: usd(r.week13_cash_change_vs_base_usd),
        ...(r.week13_cash_change_vs_base_usd < lo * 0.35 ? { position: 'insideLeft', color: '#fff', fontWeight: 600 } : { position: 'left', color: C.ink2 }) } })) }] });
}
