// PUBLIC · REAL company workspaces — every figure from SEC EDGAR XBRL filings or public market data, with lineage.
import {
  $, $$, C, answerView, api, axisMoney, badge, basis, cached, card, chart, classBadge, confBadge, dt, esc, html, impactTable,
  kpi, kpiM, loading, metricValue, num, pct, prio, publicMetricDrawer, state, statusBadge, table, toast, usd, waterfall, xx,
} from './core.js';
import { can, head, q, set } from './p_common.js';

const ck = () => `cik=${state.cik}`;
const pub = (path) => api(`${path}${path.includes('?') ? '&' : '?'}${ck()}`);
const val = (m) => (m && m.value !== null && m.value !== undefined ? m.value : null);
const ov = () => cached(`pub:ov:${state.cik}`, () => pub('/v1/public/overview'));
const qlab = (e) => `${e.slice(0, 4)}-Q${Math.floor((+e.slice(5, 7) - 1) / 3) + 1}`;
const pubHead = (o, t, sub, actions = '') => head(html`${t}`, html`${classBadge('PUBLIC_REAL')} <b>${o.company.name}</b> (ticker ${o.company.ticker || '—'},
  CIK ${String(o.company.cik).padStart(10, '0')}) · ${sub}`, actions);
const naTile = (m) => html`<div class="card kpi"><div class="label">${m.label} ${basis('NOT_AVAILABLE')}</div>
  <div class="value na-row" style="font-size:14px">Not available</div><div class="foot">${m.note || ''}</div></div>`;
const tile = (m, lab, foot) => (m && m.value !== null ? kpiM(m, lab, foot) : naTile(m || { label: lab }));

function gapsCard(o) {
  return card('Data boundary — what public data cannot provide', html`<ul class="gap-list small" style="margin:0;padding-left:18px">
    ${o.data_gaps.map((g) => html`<li><b>${g.capability}</b> — ${g.status.startsWith('CLIENT') ? classBadge('NOT_CONFIGURED') : classBadge('NOT_AVAILABLE')}
      <div class="mini">${g.reason}</div></li>`)}</ul>`, { hint: 'never imputed, never shown as real' });
}

async function latestPublicRun() {
  const runs = (await api('/v1/runs')).filter((r) => r.case === 'public' && r.cik === state.cik && r.intent === 'PUBLIC_COMPANY_REVIEW');
  return runs.length ? api(`/v1/runs/${encodeURIComponent(runs[0].run_id)}`) : null;
}
export async function runPublicAnalysis(btn) {
  if (btn) btn.disabled = true;
  toast('Running the governed agent chain on SEC filings…');
  try {
    const r = await api('/v1/public/runs', { method: 'POST', body: { cik: state.cik } });
    for (const k of Object.keys(state.cache)) delete state.cache[k];
    toast(`${r.run_id}: ${r.recommendations.length} recommendation(s); ${r.rules.filter((x) => !x.triggered).length} rule(s) not triggered`);
    return r;
  } finally { if (btn) btn.disabled = false; }
}

function recsList(recs) {
  return recs.map((r) => {
    const m = r.impact.find((i) => i.value_usd) || r.impact[0];
    return html`<div class="rec" style="cursor:pointer" data-rec="${r.rec_id}"><div class="rh">${prio(r.priority)}<span class="rt">${r.title}</span>
      ${statusBadge(r.status)}<span style="margin-left:auto" class="tabular strong">${m ? usd(m.value_usd) : ''} ${m ? basis(m.basis) : ''}</span></div>
      <div class="meta"><span>Owner <b>${r.owner}</b></span><span>Approval <b>${(r.approval_required || 'none').toUpperCase()}</b></span>
      <span>Confidence ${confBadge(r.confidence_label)}</span><span>Timing <b>${r.timing}</b></span><span>Policy ${r.policy_refs.join(' · ')}</span></div></div>`;
  });
}

// ================================================================= EXECUTIVE COMMAND CENTER
export async function command(main) {
  set(main, loading());
  const [o, fc, recs, run] = await Promise.all([ov(), cached(`pub:fc:${state.cik}`, () => pub('/v1/public/forecast')),
    api(`/v1/recommendations?case=public&${ck()}`), latestPublicRun()]);
  const M = o.metrics;
  const t = o.trend;
  set(main, html`
    ${pubHead(o, 'Executive Command Center', html`filings to <b>${o.latest_quarter}</b>, debt as of <b>${o.debt_date}</b>, latest 10-K <b>${o.latest_fiscal_year_end}</b>,
      market data ${o.rates.ust_date} · click any tile for SOURCE → FILING → PERIOD → CALCULATION`,
      html`${can('run_agents') ? html`<button class="btn primary" id="run">▶ Run governed analysis</button>` : ''}
        <a class="btn" href="#/recommendations">Recommendations →</a>`)}
    <div class="strip">
      ${tile(M.cash, 'Cash & equivalents', `as of ${o.latest_quarter}`)}
      ${tile(M.liquidity_fy, 'Liquidity (cash + undrawn facilities)', `as of ${o.debt_date}`)}
      ${tile(M.st_obligations, 'Debt due within 12 months', (val(M.liquidity_coverage) !== null ? `${xx(val(M.liquidity_coverage))} covered` : 'coverage n/a'))}
      ${tile(M.net_debt, 'Net debt', `gross leverage ${xx(M.gross_leverage?.value)}${o.company.captive_finance ? ' (incl. captive finance)' : ''}`)}
      ${tile(M.fcf_ttm, 'Free cash flow proxy (TTM)', `returns ${usd(M.shareholder_returns_ttm?.value)}`)}
      ${tile(M.dso, 'DSO', `CCC ${val(M.ccc) === null ? 'n/a' : `${val(M.ccc)} d`} · trade WC ${usd(val(M.trade_working_capital))}`)}
    </div>
    <div class="grid g-2" style="margin-top:16px">
      ${card(html`${q('How much liquidity is available against debt coming due?')}Liquidity vs 12-month debt (${o.debt_date && o.debt_date !== 'STALE' ? o.debt_date : o.latest_fiscal_year_end})`,
        html`<div class="chart tall" id="x-wf"></div>`, { hint: 'REPORTED → CALCULATED' })}
      ${card(html`${q('What is the cash outlook?')}Cash: reported quarters and ${fc.method_selected === 'no_change' ? 'no-change' : 'seasonal'} outlook (P10–P90)`, html`<div class="chart tall" id="x-fc"></div>
        <div class="mini">${fc.method} Back-test MAPE: seasonal ${pct(fc.mape_seasonal)}, no-change ${pct(fc.mape_naive_no_change)}.</div>`, { hint: 'REPORTED + ESTIMATED' })}
    </div>
    <div class="grid g-2" style="margin-top:16px">
      ${card(html`${q('When does debt mature?')}Long-term debt maturity ladder`, html`<div class="chart" id="x-mat"></div>
        <div class="mini">Years 1–5: ASC 470 schedule in the ${o.latest_fiscal_year_end.slice(0, 4)} 10-K (REPORTED). "After" bucket is a CALCULATED residual.</div>`)}
      ${card(html`${q('What is driving working capital?')}DSO · DIO · DPO (TTM denominators)`, html`<div class="chart" id="x-wc"></div>
        <div class="mini">${o.wc_bridge.available ? html`Year on year: CCC ${o.wc_bridge.ccc_from} → ${o.wc_bridge.ccc_to} days; trade working capital ${usd(o.wc_bridge.trade_wc_change_usd)}.` : ''}</div>`)}
    </div>
    <div class="grid g-2" style="margin-top:16px">
      ${card(html`${q('Where does the cash go?')}Cash generation and use (TTM to ${o.latest_quarter})`, html`<div class="chart" id="x-cf"></div>`, { hint: 'CALCULATED from 10-Q/10-K cash-flow statements' })}
      ${card(html`${q('What are the risks?')}Scenario impact on first-year free cash flow`, html`<div class="chart" id="x-sc"></div>
        <div class="mini">SCENARIO overlays on reported data. A revenue fall releases receivables and inventory in year one, which can more than offset lower EBITDA (counter-cyclical cash) — assumptions in Scenario Lab.</div>`)}
    </div>
    <div class="grid g-3" style="margin-top:16px"><div class="span-2">
      ${card(html`${q('What should management decide?')}Governed recommendations from public evidence`,
        recs.length ? html`${recsList(recs)}${run ? html`<h4>Policy rules evaluated</h4>${table([{ key: 'rule', label: 'Rule' }, { key: 'test', label: 'Evidence test' },
          { key: 'triggered', label: 'Result', fmt: (v) => (v ? badge('triggered', 'warn') : badge('not triggered', 'muted')) }], run.rules || [], { maxH: false })}` : ''}`
        : html`<div class="empty">No analysis has been run on the public filings yet.
          ${can('run_agents') ? 'Run the governed analysis to evaluate the policy rules.' : 'Ask a Finance Analyst, Treasurer or CFO to run it.'}</div>`,
        { hint: 'decision mandates — never payments' })}</div>
      ${gapsCard(o)}</div>`);

  const liq = val(M.liquidity_fy), st = val(M.st_obligations), cashD = val(M.cash_fy);
  const curPart = val(M.ltd_current) ?? val(M.maturity_y1);
  const wf = [];
  if (cashD !== null) wf.push({ label: 'Cash', value: cashD, kind: 'total', basis: 'REPORTED' });
  if (liq !== null) {
    wf.push({ label: 'Undrawn committed facilities', value: liq - cashD, kind: 'delta', basis: 'REPORTED' });
    wf.push({ label: 'Liquidity', value: liq, kind: 'subtotal', basis: 'CALCULATED' });
  }
  if (st !== null) {
    const stb = val(M.short_term_borrowings);
    if (stb && curPart !== null && Math.abs(stb + curPart - st) < 1) {
      wf.push({ label: 'Short-term borrowings', value: -stb, kind: 'delta', basis: 'REPORTED' });
      wf.push({ label: 'LTD due in 12 months', value: -curPart, kind: 'delta', basis: 'REPORTED' });
    } else wf.push({ label: 'Debt due within 12 months', value: -st, kind: 'delta', basis: M.st_obligations.basis });
    if (cashD !== null) wf.push({ label: 'Headroom after 12-month debt', value: (liq ?? cashD) - st, kind: 'total', basis: 'CALCULATED' });
  }
  if (wf.length > 1) waterfall($('#x-wf'), wf);
  else $('#x-wf').innerHTML = '<div class="empty">Liquidity and 12-month debt are not available from this filer\'s structured data.</div>';
  const hist = fc.history.slice(-8);
  if (!fc.available) $('#x-fc').innerHTML = `<div class="empty">${esc(fc.method)}</div>`;
  else {
  const xs = [...hist.map((h) => qlab(h.end)), ...fc.outlook.map((x) => x.quarter)];
  const pad = (arr) => [...hist.map(() => null), ...arr];
  chart($('#x-fc'), { grid: { left: 64, right: 16, top: 20, bottom: 48 }, legend: { bottom: 0 }, tooltip: { trigger: 'axis', valueFormatter: (v) => usd(v) },
    xAxis: { type: 'category', data: xs }, yAxis: { type: 'value', ...axisMoney },
    series: [{ name: 'Reported cash', type: 'bar', data: [...hist.map((h) => h.cash_usd), ...fc.outlook.map(() => null)], itemStyle: { color: C.s1 }, barMaxWidth: 26 },
      { name: 'P10', type: 'line', stack: 'b', data: pad(fc.outlook.map((x) => x.p10_usd)), lineStyle: { opacity: 0 }, symbol: 'none' },
      { name: 'P10–P90', type: 'line', stack: 'b', data: pad(fc.outlook.map((x) => x.p90_usd - x.p10_usd)), lineStyle: { opacity: 0 }, areaStyle: { color: C.band }, symbol: 'none' },
      { name: 'Outlook P50', type: 'line', data: pad(fc.outlook.map((x) => x.p50_usd)), lineStyle: { type: 'dashed', color: C.s2 }, itemStyle: { color: C.s2 } }] });
  }
  const mat = o.maturities.rows;
  chart($('#x-mat'), { grid: { left: 64, right: 16, top: 16, bottom: 30 }, tooltip: { trigger: 'axis', valueFormatter: (v) => usd(v) },
    xAxis: { type: 'category', data: mat.map((r) => r.bucket) }, yAxis: { type: 'value', ...axisMoney },
    series: [{ type: 'bar', barMaxWidth: 40, data: mat.map((r) => ({ value: r.amount_usd, itemStyle: { color: r.basis === 'REPORTED' ? C.s1 : '#98a2b3' } })),
      label: { show: true, position: 'top', formatter: (p) => usd(p.value), fontSize: 10 } }] });
  const tt = t.filter((r) => r.dso);
  chart($('#x-wc'), { grid: { left: 44, right: 16, top: 16, bottom: 48 }, legend: { bottom: 0 }, tooltip: { trigger: 'axis' },
    xAxis: { type: 'category', data: tt.map((r) => qlab(r.end)) }, yAxis: { type: 'value', name: 'days', splitLine: { lineStyle: { color: C.grid } } },
    series: [['DSO', 'dso', C.s1], ['DIO', 'dio', C.s4], ['DPO', 'dpo', C.s3], ['CCC', 'ccc', C.s2]].map(([n, k, c]) => ({
      name: n, type: 'line', data: tt.map((r) => r[k]), itemStyle: { color: c }, lineStyle: { width: k === 'ccc' ? 2.5 : 1.8 } })) });
  const cf = [];
  if (val(M.ocf_ttm) !== null) cf.push({ label: 'Operating cash flow', value: val(M.ocf_ttm), kind: 'total' });
  if (val(M.capex_ttm) !== null) cf.push({ label: 'Capex', value: -val(M.capex_ttm), kind: 'delta' });
  if (val(M.fcf_ttm) !== null) cf.push({ label: 'FCF proxy', value: val(M.fcf_ttm), kind: 'subtotal' });
  if (val(M.dividends_ttm) !== null) cf.push({ label: 'Dividends', value: -val(M.dividends_ttm), kind: 'delta' });
  if (val(M.buybacks_ttm) !== null) cf.push({ label: 'Repurchases', value: -val(M.buybacks_ttm), kind: 'delta' });
  if (val(M.shareholder_returns_ttm) !== null && val(M.fcf_ttm) !== null) {
    cf.push({ label: 'FCF after returns', value: val(M.fcf_ttm) - val(M.shareholder_returns_ttm), kind: 'total' });
  }
  if (cf.length > 1) waterfall($('#x-cf'), cf);
  else $('#x-cf').innerHTML = '<div class="empty">Cash-flow lines not available from this filer\'s structured data.</div>';
  const sc = (await cached(`pub:sc:${state.cik}`, () => pub('/v1/public/scenarios'))).slice(1).filter((x) => x.available !== false);
  chart($('#x-sc'), { grid: { left: 210, right: 40, top: 10, bottom: 24 }, tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' }, valueFormatter: (v) => usd(v) },
    xAxis: { type: 'value', ...axisMoney }, yAxis: { type: 'category', data: sc.map((s) => s.name), axisLabel: { width: 200, overflow: 'truncate' } },
    series: [{ type: 'bar', barMaxWidth: 18, data: sc.map((s) => ({ value: s.fcf_scenario_usd - s.fcf_base_usd, itemStyle: { color: s.fcf_scenario_usd >= s.fcf_base_usd ? C.s3 : C.s2 } })),
      label: { show: true, position: 'right', formatter: (p) => usd(p.value), fontSize: 10 } }] });
  $$('[data-rec]', main).forEach((el) => el.onclick = () => { location.hash = `#/recommendations?r=${encodeURIComponent(el.dataset.rec)}`; });
  $('#run')?.addEventListener('click', async (e) => { await runPublicAnalysis(e.target); command(main); });
}

// ================================================================= LIQUIDITY INTELLIGENCE
export async function liquidity(main) {
  set(main, loading());
  const o = await ov();
  const M = o.metrics, r = o.rates;
  set(main, html`
    ${pubHead(o, 'Liquidity Intelligence', `liquidity, debt, cost of funding and market rates — consolidated${o.company.captive_finance ? ' (includes the captive finance arm)' : ''} · debt as of ${o.debt_date}`)}
    <div class="strip">${tile(M.total_debt, 'Total debt')}${tile(M.net_debt, 'Net debt')}${tile(M.liquidity_coverage, 'Liquidity coverage of 12-month debt')}
      ${tile(M.avg_cost_of_debt, 'Implied average cash cost of debt')}${tile(M.interest_cover, 'Interest cover proxy')}${tile(M.credit_capacity, 'Committed facilities')}</div>
    <div class="grid g-2" style="margin-top:16px">
      ${card('Debt composition and 12-month obligations', html`<div class="chart" id="l-debt"></div>
        <div class="mini">${M.total_debt.note || ''}</div>`, { hint: `as of ${o.debt_date}` })}
      ${card('Interest-rate & refinancing exposure', html`<div class="grid g-2">${tile(M.repricing_100bps, '+100 bps on debt due ≤12m (annual)')}
        ${tile(M.refi_cost_delta, 'Refinance 12-month maturities at market (annual)')}</div>
        <ul class="small" style="padding-left:18px">${[...(M.refi_cost_delta?.assumptions || []), ...(M.repricing_100bps?.assumptions || [])].map((a) => html`<li>${a}</li>`)}</ul>
        <div class="callout small">Fixed/floating mix, hedge designations and covenant terms are ${classBadge('NOT_AVAILABLE')} as structured public data.
          Disclosed derivative notional: <b>${usd(M.derivative_notional?.value)}</b> (${M.derivative_notional?.period || 'not tagged'}).</div>`)}
    </div>
    <div class="grid g-2" style="margin-top:16px">
      ${card(html`Market rates ${classBadge('PUBLIC_REAL')}`, html`<div class="chart" id="l-curve"></div>
        <dl class="kv"><dt>SOFR</dt><dd><b>${r.sofr_pct}%</b> (${r.sofr_date}, NY Fed)</dd><dt>EFFR</dt><dd>${r.effr_pct}%</dd>
        <dt>UST 5Y</dt><dd><b>${r.ust5_pct}%</b> (${r.ust_date}, U.S. Treasury)</dd>
        <dt>Single-A OAS</dt><dd>${r.a_oas_pct == null ? html`${classBadge('NOT_AVAILABLE')} not in market snapshot` : html`<b>${r.a_oas_pct}%</b> (${r.a_oas_date}, FRED BAMLC0A3CA)`}</dd><dt>BBB OAS</dt><dd>${r.bbb_oas_pct == null ? 'n/a' : `${r.bbb_oas_pct}% (${r.bbb_oas_date}, FRED)`}</dd>
        <dt>Indicative new 5Y issue</dt><dd>${r.ust5_pct == null || r.a_oas_pct == null ? 'n/a (needs UST 5Y and Single-A OAS)' : html`<b>${num(r.ust5_pct + r.a_oas_pct, 2)}%</b> ${basis('ESTIMATED')} vs implied cost ${M.avg_cost_of_debt?.value == null ? 'n/a' : pct(M.avg_cost_of_debt.value, 2)}`}</dd></dl>`)}
      ${card('Cash and liquidity by quarter', html`<div class="chart" id="l-cash"></div><div class="mini">Cash by legal entity, bank or currency is
        ${classBadge('NOT_CONFIGURED')} — it requires bank statements or a bank/TMS connector.</div>`)}
    </div>
    <div style="margin-top:16px">${card('Liquidity & debt facts (as filed)', table([{ key: 'label', label: 'Metric' },
      { key: 'value', label: 'Value', num: 1, fmt: (v, m) => metricValue(m) }, { key: 'basis', label: 'Basis', fmt: (v) => basis(v) },
      { key: 'period', label: 'Period' }, { key: 'formula', label: 'Concept / formula', fmt: (v) => html`<span class="mono small">${v || ''}</span>` },
      { key: 'metric_id', label: '', fmt: (v) => html`<a class="chip" data-metric="${v}">lineage</a>` }],
    ['cash', 'cash_fy', 'credit_capacity', 'credit_available', 'liquidity_fy', 'short_term_borrowings', 'maturity_y1', 'ltd_noncurrent', 'total_debt',
      'net_debt', 'st_obligations', 'liquidity_coverage', 'ebitda_fy', 'gross_leverage', 'net_leverage', 'interest_paid_fy', 'interest_cover', 'avg_cost_of_debt']
      .map((k) => M[k]).filter(Boolean), { maxH: false }))}</div>`);
  chart($('#l-debt'), { grid: { left: 64, right: 16, top: 30, bottom: 30 }, legend: { top: 0 }, tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' }, valueFormatter: (v) => usd(v) },
    xAxis: { type: 'category', data: ['Debt', 'Liquidity'] }, yAxis: { type: 'value', ...axisMoney },
    series: [['Debt due ≤12m', [val(M.st_obligations), null], C.s2],
      ['LTD due after 1 year', [val(M.ltd_noncurrent), null], C.s1], ['Cash', [null, val(M.cash_fy)], C.s3],
      ['Undrawn facilities', [null, val(M.liquidity_fy) !== null && val(M.cash_fy) !== null ? val(M.liquidity_fy) - val(M.cash_fy) : null], '#7fd3b0']].map(([n, d, c]) => ({ name: n, type: 'bar', stack: 'x', data: d, itemStyle: { color: c }, barMaxWidth: 70 })) });
  chart($('#l-curve'), { grid: { left: 40, right: 16, top: 16, bottom: 30 }, tooltip: { trigger: 'axis' },
    xAxis: { type: 'category', data: o.curve.map((c) => c.tenor) }, yAxis: { type: 'value', scale: true, axisLabel: { formatter: '{value}%' } },
    series: [{ type: 'line', data: o.curve.map((c) => c.yield_pct), itemStyle: { color: C.s1 }, name: `UST par curve ${r.ust_date}` }] });
  chart($('#l-cash'), { grid: { left: 64, right: 16, top: 16, bottom: 30 }, tooltip: { trigger: 'axis', valueFormatter: (v) => usd(v) },
    xAxis: { type: 'category', data: o.trend.map((x) => qlab(x.end)) }, yAxis: { type: 'value', ...axisMoney },
    series: [{ type: 'bar', name: 'Cash (REPORTED)', data: o.trend.map((x) => x.cash_usd), itemStyle: { color: C.s1 }, barMaxWidth: 26 }] });
}

// ================================================================= FINANCIAL NETWORK
export async function network(main) {
  set(main, loading());
  const [o, g] = await Promise.all([ov(), pub('/v1/public/graph')]);
  const kinds = [...new Set(g.nodes.map((n) => n.kind))];
  const col = { Company: '#101828', Domain: '#475467', Metric: C.s1, Filing: '#98a2b3', Peer: C.s5, MarketData: C.s3, Recommendation: C.s2, PolicyClause: C.s4 };
  set(main, html`${pubHead(o, 'Financial Network', `${g.nodes.length} nodes, ${g.edges.length} edges built from filings, market data, recommendations and policy — no private nodes`)}
    <div class="grid g-3"><div class="span-2">${card('Company → domain → metric → filing · peers · market data · recommendations → policy',
      html`<div class="chart xl" id="n-g" style="height:620px"></div>`, { hint: 'click a node' })}</div>
      ${card('Node', html`<div id="n-d" class="small muted">Select a node to see its properties, sources and what depends on it.</div>
        <h4>Legend</h4>${kinds.map((k) => html`<div class="row small"><span style="display:inline-block;width:10px;height:10px;border-radius:50%;background:${col[k] || '#999'}"></span>${k}</div>`)}
        <div class="callout small" style="margin-top:12px">Entity, bank-account and counterparty networks need private data
          (${classBadge('NOT_CONFIGURED')}). They are demonstrated on the SYNTHETIC · EVALUATION case.</div>`)}</div>`);
  const ch = chart($('#n-g'), { tooltip: { formatter: (p) => (p.dataType === 'edge' ? esc(p.data.rel) : `<b>${esc(p.data.label)}</b><br>${esc(p.data.kind)}`) },
    series: [{ type: 'graph', layout: 'force', roam: true, draggable: true, force: { repulsion: 140, edgeLength: [40, 110], gravity: 0.08 },
      label: { show: true, fontSize: 9, formatter: (p) => (['Company', 'Domain', 'Recommendation', 'Peer', 'PolicyClause', 'MarketData'].includes(p.data.kind) ? p.data.label.slice(0, 34) : '') },
      edgeSymbol: ['none', 'arrow'], edgeSymbolSize: 4, lineStyle: { color: '#c3cad6', width: 0.8 }, emphasis: { focus: 'adjacency' },
      data: g.nodes.map((n) => ({ ...n, name: n.id, symbolSize: { Company: 34, Domain: 20, Recommendation: 18, Metric: 11, Peer: 12, PolicyClause: 12, MarketData: 12, Filing: 7 }[n.kind] || 8,
        itemStyle: { color: col[n.kind] || '#999' } })),
      links: g.edges.map((e) => ({ source: e.from, target: e.to, rel: e.rel })) }] });
  ch?.on('click', (p) => {
    if (p.dataType !== 'node') return;
    const n = p.data;
    const down = g.edges.filter((e) => e.from === n.id).map((e) => g.nodes.find((x) => x.id === e.to)).filter(Boolean);
    const up = g.edges.filter((e) => e.to === n.id).map((e) => g.nodes.find((x) => x.id === e.from)).filter(Boolean);
    set($('#n-d'), html`<b style="font-size:15px">${n.label}</b> <span class="mini">${n.kind}</span>
      ${n.value !== undefined ? html`<div>Value <b>${n.unit === 'USD' ? usd(n.value) : n.value}</b> ${n.basis ? basis(n.basis) : ''}</div>` : ''}
      ${n.accession ? html`<div class="mono mini">${n.accession}</div>` : ''}
      <h4>Depends on (${up.length})</h4>${up.slice(0, 12).map((x) => html`<div>${x.label} <span class="mini">${x.kind}</span></div>`)}
      <h4>Downstream impact (${down.length})</h4>${down.slice(0, 14).map((x) => html`<div>${x.label} <span class="mini">${x.kind}</span></div>`)}
      ${n.kind === 'Metric' ? html`<a class="chip" data-metric="${n.id.slice(2)}" style="margin-top:8px">open lineage</a>` : ''}`);
  });
}

// ================================================================= WORKING CAPITAL
export async function workingCapital(main) {
  set(main, loading());
  const [o, p] = await Promise.all([ov(), cached(`pub:peers:${state.cik}`, () => pub('/v1/public/peers'))]);
  const M = o.metrics, b = o.wc_bridge, t = o.trend.filter((r) => r.dso);
  set(main, html`
    ${pubHead(o, 'Working Capital', `receivables, inventory and payables from 10-Q/10-K balance sheets · days on TTM revenue/COGS`)}
    <div class="strip">${tile(M.dso, 'DSO')}${tile(M.dio, 'DIO')}${tile(M.dpo, 'DPO')}${tile(M.ccc, 'Cash conversion cycle')}
      ${tile(M.trade_working_capital, 'Trade working capital')}${o.company.captive_finance ? tile(M.receivables_finance, 'Finance receivables (current)', 'captive finance — not trade WC') : ''}</div>
    <div class="grid g-2" style="margin-top:16px">
      ${card(html`${q('What changed year on year?')}Working-capital bridge ${b.available ? `${b.from} → ${b.to}` : ''}`, b.available ? html`<div class="chart" id="w-br"></div>
        ${table([{ key: 'component', label: 'Component' }, { key: 'days_change', label: 'Δ days (CCC effect)', num: 1, fmt: (v) => `${v > 0 ? '+' : ''}${num(v)}` },
          { key: 'usd_change', label: 'Δ cash tied up', num: 1, fmt: (v) => usd(v) }], b.components, { maxH: false })}<div class="mini">${b.formula}</div>` : html`<div class="empty">${b.reason}</div>`,
        { hint: 'CALCULATED' })}
      ${card('Days trend (TTM denominators)', html`<div class="chart" id="w-days"></div>`)}
    </div>
    <div class="grid g-2" style="margin-top:16px">
      ${card('Balances by quarter (REPORTED)', html`<div class="chart" id="w-bal"></div>`)}
      ${card(html`Peer context — FY 10-K, same formula ${classBadge('PUBLIC_REAL')}`, html`<div class="chart" id="w-peer"></div>
        <div class="callout warn small">${p.caveat}</div>${p.issues.length ? html`<div class="mini">Excluded: ${p.issues.join('; ')}</div>` : ''}`)}
    </div>
    <div style="margin-top:16px">${card('Peers (latest 10-K)', table([{ key: 'company', label: 'Company' }, { key: 'fiscal_year_end', label: 'FY end' },
      { key: 'revenue_usd', label: 'Revenue', num: 1, fmt: (v) => usd(v) }, { key: 'dso_days', label: 'DSO', num: 1 }, { key: 'dio_days', label: 'DIO', num: 1 },
      { key: 'dpo_days', label: 'DPO', num: 1 }, { key: 'ccc_days', label: 'CCC', num: 1 },
      { key: 'accession', label: 'Filing', fmt: (v) => (v ? html`<span class="mono">${v}</span>` : '—') }],
    [{ ...p.subject, company: `${p.subject.company || o.company.name} (subject)${p.subject.dso_days == null ? ' — not computable: required concepts missing' : ''}` }, ...p.peers], { maxH: false }))}</div>
    <div class="grid g-2" style="margin-top:16px">${card('Customer-, invoice- and supplier-level analytics', html`<div class="small">Collections priorities, AR ageing,
      dispute tracking, supplier discount capture and payment behaviour need the ERP sub-ledgers: ${classBadge('NOT_CONFIGURED')}.
      These capabilities are implemented and tested on the ${classBadge('SYNTHETIC')} case (switch case in the header).</div>`)}${gapsCard(o)}</div>`);
  if (b.available) {
    chart($('#w-br'), { grid: { left: 170, right: 40, top: 10, bottom: 24 }, tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
      xAxis: { type: 'value', name: 'days', splitLine: { lineStyle: { color: C.grid } } }, yAxis: { type: 'category', data: b.components.map((c) => c.component) },
      series: [{ type: 'bar', barMaxWidth: 22, data: b.components.map((c) => ({ value: c.days_change, itemStyle: { color: c.days_change > 0 ? C.s2 : C.s3 } })),
        label: { show: true, position: 'right', formatter: (x) => `${x.value > 0 ? '+' : ''}${x.value} d`, fontSize: 10 } }] });
  }
  chart($('#w-days'), { grid: { left: 44, right: 16, top: 16, bottom: 48 }, legend: { bottom: 0 }, tooltip: { trigger: 'axis' },
    xAxis: { type: 'category', data: t.map((r) => qlab(r.end)) }, yAxis: { type: 'value', name: 'days', splitLine: { lineStyle: { color: C.grid } } },
    series: [['DSO', 'dso', C.s1], ['DIO', 'dio', C.s4], ['DPO', 'dpo', C.s3], ['CCC', 'ccc', C.s2]].map(([n, k, c]) => ({ name: n, type: 'line', data: t.map((r) => r[k]), itemStyle: { color: c } })) });
  chart($('#w-bal'), { grid: { left: 64, right: 16, top: 16, bottom: 48 }, legend: { bottom: 0 }, tooltip: { trigger: 'axis', valueFormatter: (v) => usd(v) },
    xAxis: { type: 'category', data: t.map((r) => qlab(r.end)) }, yAxis: { type: 'value', ...axisMoney },
    series: [['Trade receivables', 'receivables_usd', C.s1], ['Inventories', 'inventory_usd', C.s4], ['Accounts payable', 'payables_usd', C.s3]].map(([n, k, c]) => ({
      name: n, type: 'line', data: t.map((r) => r[k]), itemStyle: { color: c } })) });
  const rows = [{ name: `${p.subject.company || o.company.ticker || o.company.name} (subject)`, ...p.subject, me: true }, ...p.peers.map((x) => ({ name: x.label || x.company, ...x }))];
  chart($('#w-peer'), { grid: { left: 150, right: 30, top: 30, bottom: 24 }, legend: { top: 0 }, tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
    xAxis: { type: 'value', name: 'days' }, yAxis: { type: 'category', data: rows.map((r) => r.name), axisLabel: { width: 140, overflow: 'truncate' } },
    series: [['DSO', 'dso_days', C.s1], ['DIO', 'dio_days', C.s4], ['DPO', 'dpo_days', C.s3]].map(([n, k, c]) => ({ name: n, type: 'bar', data: rows.map((r) => r[k]), itemStyle: { color: c }, barMaxWidth: 8 })) });
}

// ================================================================= FORECAST STUDIO
export async function forecast(main) {
  set(main, loading());
  const [o, fc] = await Promise.all([ov(), cached(`pub:fc:${state.cik}`, () => pub('/v1/public/forecast'))]);
  const t = o.trend;
  set(main, html`
    ${pubHead(o, 'Forecast Studio', 'quarterly cash outlook from reported history · forecast vs actual on reported quarters')}
    <div class="strip">${kpi('Cash today (REPORTED)', usd(fc.start.cash_usd), fc.start.date)}
      ${fc.outlook.map((x) => kpi(html`${x.quarter} P50 ${basis('ESTIMATED')}`, usd(x.p50_usd), `P10 ${usd(x.p10_usd)} · P90 ${usd(x.p90_usd)}`))}
      ${kpi('Back-test MAPE', pct(fc.mape), `${fc.method_selected === 'seasonal' ? 'seasonal (selected)' : 'no-change (selected)'} · seasonal ${pct(fc.mape_seasonal)} · no-change ${pct(fc.mape_naive_no_change)}`)}</div>
    <div class="grid g-3" style="margin-top:16px"><div class="span-2">${card('Cash: reported vs point-in-time forecast, and outlook', html`<div class="chart tall" id="f-main"></div>
      <div class="mini">${fc.method}</div>`)}</div>
      ${card('Method & limitations', html`<ul class="small" style="padding-left:18px">${fc.limitations.map((x) => html`<li>${x}</li>`)}</ul>
        <div class="callout small">The 13-week, transaction-level Monte-Carlo forecast (ML payment timing per invoice) needs bank and ERP data —
        ${classBadge('NOT_CONFIGURED')} for this company. It is demonstrated on the ${classBadge('SYNTHETIC')} case.</div>`)}</div>
    <div class="grid g-2" style="margin-top:16px">
      ${card('Forecast vs actual (point-in-time back-test)', table([{ key: 'quarter_end', label: 'Quarter' },
        { key: 'forecast_cash_usd', label: 'Forecast', num: 1, fmt: (v) => usd(v) }, { key: 'actual_cash_usd', label: 'Actual (REPORTED)', num: 1, fmt: (v) => usd(v) },
        { key: 'error_usd', label: 'Error', num: 1, fmt: (v) => usd(v) }], fc.backtest.slice().reverse(), { maxH: true }))}
      ${card('Operating cash flow and FCF by quarter (CALCULATED)', html`<div class="chart" id="f-cf"></div>`)}</div>`);
  const xs = [...fc.backtest.map((b) => b.quarter_end), ...fc.outlook.map((x) => x.quarter)];
  const n0 = fc.backtest.length;
  chart($('#f-main'), { grid: { left: 64, right: 16, top: 20, bottom: 48 }, legend: { bottom: 0 }, tooltip: { trigger: 'axis', valueFormatter: (v) => usd(v) },
    xAxis: { type: 'category', data: xs.map((x) => (x.includes('-Q') ? x : qlab(x))) }, yAxis: { type: 'value', ...axisMoney },
    series: [{ name: 'Actual (REPORTED)', type: 'line', data: [...fc.backtest.map((b) => b.actual_cash_usd), ...fc.outlook.map(() => null)], itemStyle: { color: C.ink }, lineStyle: { width: 2 } },
      { name: 'Point-in-time forecast', type: 'line', data: [...fc.backtest.map((b) => b.forecast_cash_usd), ...fc.outlook.map(() => null)], itemStyle: { color: C.s1 }, lineStyle: { type: 'dotted' } },
      { name: 'P10', type: 'line', stack: 'o', data: [...Array(n0).fill(null), ...fc.outlook.map((x) => x.p10_usd)], lineStyle: { opacity: 0 }, symbol: 'none' },
      { name: 'P10–P90', type: 'line', stack: 'o', data: [...Array(n0).fill(null), ...fc.outlook.map((x) => x.p90_usd - x.p10_usd)], lineStyle: { opacity: 0 }, areaStyle: { color: C.band }, symbol: 'none' },
      { name: 'Outlook P50', type: 'line', data: [...Array(n0).fill(null), ...fc.outlook.map((x) => x.p50_usd)], itemStyle: { color: C.s2 }, lineStyle: { type: 'dashed' } }] });
  const tq = t.filter((r) => r.ocf_q_usd !== null && r.ocf_q_usd !== undefined);
  chart($('#f-cf'), { grid: { left: 64, right: 16, top: 16, bottom: 48 }, legend: { bottom: 0 }, tooltip: { trigger: 'axis', valueFormatter: (v) => usd(v) },
    xAxis: { type: 'category', data: tq.map((r) => qlab(r.end)) }, yAxis: { type: 'value', ...axisMoney },
    series: [{ name: 'Operating cash flow', type: 'bar', data: tq.map((r) => r.ocf_q_usd), itemStyle: { color: C.s1 }, barMaxWidth: 18 },
      { name: 'FCF proxy', type: 'bar', data: tq.map((r) => r.fcf_q_usd), itemStyle: { color: C.s3 }, barMaxWidth: 18 }] });
}

// ================================================================= SCENARIO LAB
export async function scenarios(main) {
  set(main, loading());
  const [o, pre0] = await Promise.all([ov(), cached(`pub:sc:${state.cik}`, () => pub('/v1/public/scenarios'))]);
  const pre = pre0.filter((s) => s.available !== false && s.fcf_scenario_usd != null);
  if (!pre.length) {
    const why = (pre0[0] && pre0[0].assumptions && pre0[0].assumptions.join('; ')) || 'required public inputs are missing';
    set(main, html`${pubHead(o, 'Scenario Lab', 'what-if overlays on reported data — outputs are SCENARIO, never forecasts of reported results')}
      ${card(html`Scenarios ${classBadge('NOT_AVAILABLE')}`, html`<div class="empty">Scenarios need TTM revenue, an EBITDA proxy and free cash flow
        derived from the issuer's own us-gaap facts. For ${o.company.name} they cannot be derived: ${why}. Nothing is estimated to fill the gap.</div>`)}`);
    return;
  }
  const sl = [['revenue_change_pct', 'Revenue change (%)', -40, 20, 1, -15], ['dso_change_days', 'DSO change (days)', -20, 30, 1, 10],
    ['dio_change_days', 'DIO change (days)', -30, 45, 1, 15], ['rate_shock_bps', 'Rate shock (bps)', -200, 400, 25, 100], ['spread_shock_bps', 'Credit-spread shock (bps)', 0, 400, 25, 150]];
  set(main, html`
    ${pubHead(o, 'Scenario Lab', 'what-if overlays on reported data — outputs are SCENARIO, never forecasts of reported results')}
    <div class="grid g-3"><div>${card('Scenario builder', html`${sl.map(([k, l, a, b, st, v]) => html`<label class="lbl">${l}</label>
      <div class="row"><input type="range" id="s-${k}" min="${a}" max="${b}" step="${st}" value="${v}" style="flex:1"><span class="mono" id="v-${k}">${v}</span></div>`)}
      <button class="btn primary" id="s-run" style="margin-top:10px">Run scenario</button>`)}</div>
      <div class="span-2" id="s-out">${card('Result', loading())}</div></div>
    <div style="margin-top:16px">${card('Preset scenarios', html`<div class="chart" id="s-cmp"></div>${table([{ key: 'name', label: 'Scenario' },
      { key: 'fcf_scenario_usd', label: 'FCF year 1', num: 1, fmt: (v) => usd(v) }, { key: 'd', label: 'Δ FCF', num: 1, fmt: (v, r) => usd(r.fcf_scenario_usd - r.fcf_base_usd) },
      { key: 'liquidity_coverage_scenario', label: 'Liquidity coverage', num: 1, fmt: (v) => xx(v) },
      { key: 'gross_leverage_scenario', label: 'Gross leverage', num: 1, fmt: (v) => xx(v) }], pre, { maxH: false })}`, { hint: 'SCENARIO' })}</div>`);
  sl.forEach(([k]) => { $(`#s-${k}`).oninput = (e) => { $(`#v-${k}`).textContent = e.target.value; }; });
  const runIt = async () => {
    const qs = sl.map(([k]) => `${k}=${$(`#s-${k}`).value}`).join('&');
    const r = await pub(`/v1/public/scenario?${qs}`);
    set($('#s-out'), card(html`Scenario result ${basis('SCENARIO')}`, html`<div class="grid g-3">
      ${kpi('FCF year 1', usd(r.fcf_scenario_usd), `base ${usd(r.fcf_base_usd)}`)}${kpi('Liquidity coverage', xx(r.liquidity_coverage_scenario), `base ${xx(r.liquidity_coverage_base)} · policy ≥ 1.25x`)}
      ${kpi('Gross leverage', xx(r.gross_leverage_scenario), `base ${xx(r.gross_leverage_base)}`)}</div>
      <div class="chart" id="s-imp"></div>${impactTable(r.impacts.map((i) => ({ ...i, basis: 'SCENARIO' })))}
      <h4>Assumptions</h4><ul class="small" style="padding-left:18px">${r.assumptions.map((a) => html`<li>${a}</li>`)}</ul>`));
    waterfall($('#s-imp'), [{ label: 'Base FCF', value: r.fcf_base_usd, kind: 'total' }, ...r.impacts.slice(0, 4).map((i) => ({ label: i.label, value: i.value_usd, kind: 'delta' })),
      { label: 'Scenario FCF', value: r.fcf_scenario_usd, kind: 'total' }]);
  };
  $('#s-run').onclick = runIt; runIt();
  chart($('#s-cmp'), { grid: { left: 60, right: 60, top: 30, bottom: 70 }, legend: { top: 0 }, tooltip: { trigger: 'axis' },
    xAxis: { type: 'category', data: pre.map((s) => s.name), axisLabel: { interval: 0, width: 110, overflow: 'break', fontSize: 10 } },
    yAxis: [{ type: 'value', ...axisMoney }, { type: 'value', name: 'x', splitLine: { show: false } }],
    series: [{ name: 'FCF year 1', type: 'bar', data: pre.map((s) => s.fcf_scenario_usd), itemStyle: { color: C.s1 }, barMaxWidth: 30 },
      { name: 'Liquidity coverage (x)', type: 'line', yAxisIndex: 1, data: pre.map((s) => s.liquidity_coverage_scenario), itemStyle: { color: C.s2 } },
      { name: 'Gross leverage (x)', type: 'line', yAxisIndex: 1, data: pre.map((s) => s.gross_leverage_scenario), itemStyle: { color: C.s4 } }] });
}

// ================================================================= AI DECISION CENTER
const SUGGEST = ['Is liquidity enough for debt coming due?', 'What is the debt and refinancing profile?', 'What is driving working capital?',
  'What is our interest-rate and credit-spread exposure?', 'How much free cash flow is there and how is it used?', 'How does working capital compare with peers?',
  'What happens in a downturn?', 'What data is this based on?'];
export async function copilot(main) {
  set(main, loading());
  const [o, run] = await Promise.all([ov(), latestPublicRun()]);
  const ai = state.ready.ai_status || {};
  set(main, html`
    ${pubHead(o, 'AI Decision Center', html`bounded agents over read-only MCP tools · AI: <b>${ai.label}</b>`,
      can('run_agents') ? html`<button class="btn primary" id="c-run">▶ Run full governed review</button>` : '')}
    ${card('Ask about the company', html`<div class="row"><input class="input" id="c-q" maxlength="500" style="flex:1" placeholder="Ask about liquidity, debt, working capital, rates, cash flow, peers or scenarios">
      <button class="btn primary" id="c-ask" ${can('run_agents') ? '' : 'disabled'}>Ask</button></div>
      <div class="chips" style="margin-top:8px">${SUGGEST.map((s) => html`<button class="chip" data-s="${s}">${s}</button>`)}</div>
      ${ai.configured ? '' : html`<div class="callout warn small" style="margin-top:10px"><b>AI OFFLINE / DETERMINISTIC.</b> No LLM credentials are configured.
        Answers come from deterministic reasoners over the same tool evidence and are numerically grounded. Set ANTHROPIC_API_KEY to enable Claude.</div>`}
      <div id="c-ans" style="margin-top:12px"></div>`)}
    <div id="c-run-out" style="margin-top:16px"></div>`);
  const showRun = (r) => {
    if (!r) { set($('#c-run-out'), card('Latest governed review', html`<div class="empty">No review has been run yet.</div>`)); return; }
    const b = r.briefing || {};
    set($('#c-run-out'), html`<div class="grid g-3"><div class="span-2">${card(html`Executive briefing — ${r.run_id}`, html`
      <div class="row">${badge(r.status, 'warn')} ${r.grounding?.grounded ? badge(`all ${r.grounding.claims} $ figures grounded`, 'good', '✓ ') : badge('grounding fallback', 'warn')}
        ${badge(`AI: ${b.llm_provider === 'mock' ? 'offline deterministic' : b.llm_provider}`, 'muted')} ${classBadge('PUBLIC_REAL')}</div>
      <p class="strong" style="font-size:15px">${b.executive_summary}</p>
      <div class="grid g-2"><div><h5>Key numbers</h5><ul>${(b.key_numbers || []).map((x) => html`<li>${x}</li>`)}</ul></div>
        <div><h5>Decisions required</h5><ul>${(b.decisions_required || []).map((x) => html`<li>${x}</li>`)}</ul><h5>Risks</h5><ul>${(b.risks || []).map((x) => html`<li>${x}</li>`)}</ul></div></div>
      <h4>Agent findings (structured, validated)</h4>${table([{ key: 'agent', label: 'Agent', fmt: (v) => html`<b>${v}</b>` }, { key: 'headline', label: 'Finding' },
        { key: 'confidence', label: 'Conf.', num: 1, fmt: (v) => num(v, 2) },
        { key: 'validation', label: 'Grounding', fmt: (v) => (v?.numeric_grounding ? badge(`✓ ${v.figures_checked}`, 'good') : badge('✕', 'crit')) },
        { key: 'provenance', label: 'MCP tools', fmt: (v) => html`<span class="mini">${[...new Set((v?.tool_calls || []).map((c) => c.tool))].join(', ')}</span>` }], r.findings, { maxH: false })}`)}</div>
      ${card('Trace', html`${r.trace.map((x) => html`<div class="small"><b>${x.actor}</b> · ${x.action} <span class="mini">${x.latency_ms} ms</span></div>`)}
        <h4>Reproducibility</h4><dl class="kv small">${Object.entries(r.reproducibility || {}).map(([k, v]) => html`<dt>${k}</dt><dd class="mono">${String(v).slice(0, 40)}</dd>`)}</dl>`)}</div>`);
  };
  showRun(run);
  const ask = async (qq) => {
    set($('#c-ans'), loading());
    try {
      const r = await api('/v1/public/runs', { method: 'POST', body: { question: qq, cik: state.cik } });
      set($('#c-ans'), html`<div class="mini">${r.run_id} · ${r.insight.grounding.grounded ? `✓ ${r.insight.grounding.figures_checked} $ figures grounded` : 'ungrounded'}</div>${answerView(r.insight)}`);
    } catch (e) { set($('#c-ans'), html`<div class="callout crit">${e.message}</div>`); }
  };
  $('#c-ask').onclick = () => ask($('#c-q').value || SUGGEST[0]);
  $('#c-q').onkeydown = (e) => { if (e.key === 'Enter') ask($('#c-q').value); };
  $$('.chip[data-s]', main).forEach((c) => c.onclick = () => { $('#c-q').value = c.dataset.s; ask(c.dataset.s); });
  $('#c-run')?.addEventListener('click', async (e) => { const r = await runPublicAnalysis(e.target); showRun(await api(`/v1/runs/${encodeURIComponent(r.run_id)}`)); });
}

// ================================================================= DATA LINEAGE (public)
export async function lineage(main) {
  set(main, loading());
  const [o, cons0] = await Promise.all([ov(), api('/v1/connectors')]);
  // Company-data connectors are implemented but only fed with SYNTHETIC evaluation data; for this company they are NOT CONFIGURED.
  const cons = cons0.map((c) => ((c.datasets || []).some((d) => d.classification === 'SYNTHETIC') || /^(erp|bank|tms|idp)/.test(c.connector_id)
    ? { ...c, status: 'NOT_CONFIGURED', note: `Not configured for ${o.company.name.replace(/\.$/, '')}. ${c.status === 'NOT_CONFIGURED' ? c.note : 'Interface implemented and tested; in this deployment it only carries the SYNTHETIC · EVALUATION dataset.'}` } : c));
  const M = Object.values(o.metrics);
  set(main, html`
    ${pubHead(o, 'Data Lineage', 'SOURCE → FILING → PERIOD → TRANSFORMATION → CALCULATION → RESULT for every metric')}
    <div class="strip">${kpi('PUBLIC · REAL metrics', String(M.filter((m) => m.value !== null).length), 'with filing-level lineage')}
      ${kpi('REPORTED (as filed)', String(M.filter((m) => m.basis === 'REPORTED').length))}${kpi('CALCULATED', String(M.filter((m) => m.basis === 'CALCULATED').length))}
      ${kpi('ESTIMATED', String(M.filter((m) => m.basis === 'ESTIMATED').length), 'stated assumptions')}
      ${kpi('Not available from public data', String(o.data_gaps.length), 'capabilities')}${kpi('SEC facts in snapshot', String(o.provenance[0].record_count), `checksum ${o.provenance[0].checksum_sha256.slice(0, 10)}`)}</div>
    <div style="margin-top:16px">${card('Source register', table([{ key: 'source_id', label: 'Source', fmt: (v, r) => html`<b>${v}</b><div class="mini">${r.name}</div>` },
      { key: 'classification', label: 'Class', fmt: (v) => classBadge(v) }, { key: 'system', label: 'System' }, { key: 'as_of', label: 'As of' },
      { key: 'latest_filing', label: 'Latest filing', fmt: (v) => (v ? html`<span class="mono small">${v.form} ${v.accession} · ${v.filed}</span>` : '') },
      { key: 'validation', label: 'Validation', fmt: (v) => statusBadge(v) }, { key: 'issues', label: 'Warnings', fmt: (v) => html`<span class="mini">${(v || []).join('; ')}</span>` }],
    o.provenance, { maxH: false }), { hint: `retrieved ${dt(o.retrieved_at)}` })}</div>
    <div class="grid g-3" style="margin-top:16px"><div class="span-2">${card('Metric register (click for full lineage)', table([{ key: 'label', label: 'Metric' },
      { key: 'value', label: 'Value', num: 1, fmt: (v, m) => (v === null ? html`<span class="muted">n/a</span>` : metricValue(m)) },
      { key: 'basis', label: 'Basis', fmt: (v) => basis(v) }, { key: 'period', label: 'Period' },
      { key: 'formula', label: 'Calculation', fmt: (v) => html`<span class="mono small">${v || ''}</span>` }], M,
    { maxH: true, rowAttr: (m) => (m.value !== null ? `data-metric="${m.metric_id}" style="cursor:pointer"` : '') }))}</div>
      ${gapsCard(o)}</div>
    <div style="margin-top:16px">${card('Connectors', table([{ key: 'category', label: 'Category' }, { key: 'name', label: 'Connector' },
      { key: 'status', label: 'Status', fmt: (v) => statusBadge(v) }, { key: 'note', label: 'Note', fmt: (v) => html`<span class="mini">${v}</span>` }], cons, { maxH: false }),
      { hint: 'company ERP/bank/TMS connectors are not configured; SEC/market connectors are public' })}</div>`);
  $$('tr[data-metric]', main).forEach((tr) => tr.onclick = () => publicMetricDrawer(tr.dataset.metric));
}
