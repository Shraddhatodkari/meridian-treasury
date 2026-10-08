// Core utilities: API client, session, safe HTML templating, formatting, shared components, charts.

// ---------------------------------------------------------------- session (sessionStorage, fails safe)
const mem = {};
export const session = {
  get(k) { try { return sessionStorage.getItem(k) ?? mem[k] ?? null; } catch { return mem[k] ?? null; } },
  set(k, v) { mem[k] = v; try { sessionStorage.setItem(k, v); } catch { /* storage unavailable */ } },
  clear() { for (const k of Object.keys(mem)) delete mem[k]; try { sessionStorage.clear(); } catch { /* ignore */ } },
};
export const state = { user: null, env: null, ready: null, cache: {} };

// ---------------------------------------------------------------- API
export class ApiError extends Error { constructor(status, msg) { super(msg); this.status = status; } }
export async function api(path, { method = 'GET', body } = {}) {
  const headers = { 'Content-Type': 'application/json' };
  const tok = session.get('token');
  if (tok) headers.Authorization = `Bearer ${tok}`;
  const r = await fetch(path, { method, headers, body: body ? JSON.stringify(body) : undefined });
  if (r.status === 401 && !path.includes('/auth/login')) {
    session.clear(); location.hash = '#/login'; location.reload();
    throw new ApiError(401, 'Session expired');
  }
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new ApiError(r.status, data.detail || r.statusText);
  return data;
}
export async function cached(key, fn) {
  if (!state.cache[key]) state.cache[key] = fn();
  try { return await state.cache[key]; } catch (e) { delete state.cache[key]; throw e; }
}
export function invalidate(prefix = '') { for (const k of Object.keys(state.cache)) if (k.startsWith(prefix)) delete state.cache[k]; }

// ---------------------------------------------------------------- safe HTML
const ESC = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
export const esc = (v) => String(v ?? '').replace(/[&<>"']/g, (c) => ESC[c]);
class Raw { constructor(s) { this.s = s; } toString() { return this.s; } }
export const raw = (s) => new Raw(s);
export function html(strings, ...vals) {
  let out = '';
  strings.forEach((s, i) => {
    out += s;
    if (i < vals.length) {
      const v = vals[i];
      if (v instanceof Raw) out += v.s;
      else if (Array.isArray(v)) out += v.map((x) => (x instanceof Raw ? x.s : esc(x))).join('');
      else out += esc(v);
    }
  });
  return raw(out);
}
export const $ = (sel, el = document) => el.querySelector(sel);
export const $$ = (sel, el = document) => [...el.querySelectorAll(sel)];

// ---------------------------------------------------------------- formatting
export function usd(x, d) {
  if (x === null || x === undefined || Number.isNaN(x)) return '—';
  const a = Math.abs(x), s = x < 0 ? '−' : '';
  if (a >= 1e9) return `${s}$${(a / 1e9).toFixed(d ?? 2)}B`;
  if (a >= 1e6) return `${s}$${(a / 1e6).toFixed(d ?? (a >= 1e7 ? 1 : 2))}M`;
  if (a >= 1e3) return `${s}$${(a / 1e3).toFixed(d ?? 0)}K`;
  return `${s}$${a.toFixed(0)}`;
}
export const full = (x) => (x === null || x === undefined ? '—' : `$${Math.round(x).toLocaleString('en-US')}`);
export const pct = (x, d = 1) => (x === null || x === undefined ? '—' : `${(x * 100).toFixed(d)}%`);
export const num = (x, d = 1) => (x === null || x === undefined ? '—' : Number(x).toFixed(d));
export const dt = (s) => (s ? String(s).replace('T', ' ').slice(0, 16) : '—');
export const title = (s) => String(s || '').replace(/_/g, ' ').toLowerCase().replace(/(^|\s)\S/g, (c) => c.toUpperCase());

// ---------------------------------------------------------------- components
export function badge(text, kind = 'muted', icon = '') { return html`<span class="badge b-${kind}">${raw(icon)}${text}</span>`; }
export function classBadge(c) {
  if (c === 'SYNTHETIC') return badge('SYNTHETIC · EVALUATION', 'synth', '◆ ');
  if (c === 'PUBLIC_MARKET_DATA' || c === 'PUBLIC_REAL') return badge('PUBLIC · REAL', 'real', '● ');
  if (c === 'NOT_AVAILABLE') return badge('NOT AVAILABLE FROM PUBLIC DATA', 'muted', '○ ');
  if (c === 'NOT_CONFIGURED') return badge('CLIENT · NOT CONFIGURED', 'muted', '— ');
  if (c === 'CLIENT_DATA') return badge('CLIENT DATA', 'good', '● ');
  return badge(c || 'REFERENCE', 'muted');
}
export function statusBadge(s) {
  const m = { PASSED: ['good', '✓ '], PASSED_WITH_WARNINGS: ['warn', '! '], FAILED: ['crit', '✕ '],
    FRESH: ['good', '✓ '], STALE: ['crit', '✕ '], FUTURE_DATED: ['warn', '! '], 'N/A': ['muted', ''],
    PROPOSED: ['warn', '● '], APPROVED: ['good', '✓ '], REJECTED: ['crit', '✕ '], AWAITING_APPROVALS: ['warn', '● '],
    COMPLETED: ['good', '✓ '], ANSWERED: ['info', '✓ '], HIGH: ['good', '▲ '], MEDIUM: ['warn', '■ '], LOW: ['crit', '▼ '],
    PASS: ['good', '✓ '], WARN: ['warn', '! '], FAIL: ['crit', '✕ '], SUBMITTED: ['info', '● '], READY: ['good', '◉ '],
    IN_REVIEW: ['info', '● '], PARTIALLY_APPROVED: ['warn', '◐ '], READY_FOR_EXECUTION: ['good', '◉ '], OPEN: ['muted', '○ '],
    COMPLIANT: ['good', '✓ '], WATCH: ['warn', '! '], BREACH: ['crit', '✕ '], ACTIVE: ['good', '● '], AVAILABLE: ['info', '○ '],
    SNAPSHOT: ['info', '◷ '], NOT_CONFIGURED: ['muted', '— '], UP: ['good', '● '], DOWN: ['crit', '● '], OK: ['good', '✓ '],
    DEGRADED: ['crit', '! '], SUCCEEDED: ['good', '✓ '], IN_APPROVAL: ['info', '● '], TRANSMITTED: ['good', '✓ '],
    NOT_RUN: ['muted', '○ '], EXECUTED: ['good', '✓ '], ISSUED: ['good', '✓ '], PUBLIC_COMPANY_REVIEW: ['info', '● '] };
  const [k, i] = m[s] || ['muted', ''];
  return badge(String(s).replace(/_/g, ' '), k, i);
}
export function confBadge(c) {
  if (typeof c === 'number') return statusBadge(c >= 0.8 ? 'HIGH' : c >= 0.6 ? 'MEDIUM' : 'LOW');
  return statusBadge(c);
}
export function kpi(label, value, foot = '', help = '') {
  return html`<div class="card kpi"><div class="label">${label}${help ? html` <span title="${help}" class="muted">ⓘ</span>` : ''}</div>
    <div class="value">${value}</div><div class="foot">${foot}</div></div>`;
}
export function card(titleText, body, { hint = '', right = '', cls = '' } = {}) {
  return html`<div class="card ${cls}"><div class="card-h"><h3>${titleText}</h3><span class="hint">${hint}</span>
    <div class="right">${right}</div></div><div class="card-b">${body}</div></div>`;
}
/** cols: [{key, label, num?, fmt?(v,row) -> string|raw}] */
export function table(cols, rows, { id = '', maxH = true, empty = 'No rows', rowAttr } = {}) {
  if (!rows || !rows.length) return html`<div class="empty">${empty}</div>`;
  const head = cols.map((c) => html`<th class="${c.num ? 'num' : ''}" data-k="${c.key}">${c.label}</th>`);
  const body = rows.map((r, i) => html`<tr ${raw(rowAttr ? rowAttr(r, i) : '')}>${cols.map((c) => {
    const v = c.fmt ? c.fmt(r[c.key], r) : r[c.key];
    return html`<td class="${c.num ? 'num' : ''}">${v ?? '—'}</td>`;
  })}</tr>`);
  return html`<div class="table-wrap ${maxH ? 'scroll-y' : ''}"><table class="t" ${raw(id ? `id="${esc(id)}"` : '')}>
    <thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div>`;
}
export function sortable(root) {
  $$('table.t', root).forEach((t) => {
    $$('th', t).forEach((th, idx) => th.addEventListener('click', () => {
      const rows = $$('tbody tr', t);
      const asc = th.dataset.dir !== 'asc';
      th.dataset.dir = asc ? 'asc' : 'desc';
      const val = (tr) => {
        const s = tr.children[idx]?.textContent.trim() || '';
        const n = parseFloat(s.replace(/[$,%−BMKx ]/g, '').replace('−', '-'));
        const mult = /B$/.test(s) ? 1e9 : /M$/.test(s) ? 1e6 : /K$/.test(s) ? 1e3 : 1;
        return Number.isNaN(n) ? s : (s.startsWith('−') ? -Math.abs(n) : n) * mult;
      };
      rows.sort((a, b) => { const x = val(a), y = val(b); return (x > y ? 1 : x < y ? -1 : 0) * (asc ? 1 : -1); });
      rows.forEach((r) => t.tBodies[0].appendChild(r));
    }));
  });
}
export function toast(msg, err = false) {
  const t = document.createElement('div');
  t.className = `toast ${err ? 'err' : ''}`; t.textContent = msg;
  document.body.appendChild(t); setTimeout(() => t.remove(), 4200);
}
export function drawer(titleText, bodyHtml) {
  closeDrawer();
  const back = document.createElement('div'); back.className = 'drawer-back'; back.id = 'drawer-back';
  const d = document.createElement('div'); d.className = 'drawer'; d.id = 'drawer';
  d.innerHTML = `<div class="drawer-h"><h2>${esc(titleText)}</h2><button class="x" aria-label="Close">×</button></div>
    <div class="drawer-b">${bodyHtml}</div>`;
  back.onclick = closeDrawer; d.querySelector('.x').onclick = closeDrawer;
  document.body.append(back, d);
  return d.querySelector('.drawer-b');
}
export function closeDrawer() { $('#drawer')?.remove(); $('#drawer-back')?.remove(); }
export function confirmModal(titleText, bodyHtml, { okText = 'Confirm', okCls = 'primary', requireText = false } = {}) {
  return new Promise((resolve) => {
    const back = document.createElement('div'); back.className = 'modal-back';
    const m = document.createElement('div'); m.className = 'modal';
    m.innerHTML = `<div class="drawer-h"><h2>${esc(titleText)}</h2></div><div class="drawer-b">${bodyHtml}
      <label class="lbl" style="margin-top:12px">Comment${requireText ? ' (required)' : ' (optional)'}</label>
      <textarea class="input" rows="3" id="m-comment"></textarea>
      <div class="row" style="justify-content:flex-end;margin-top:14px"><button class="btn" id="m-cancel">Cancel</button>
      <button class="btn ${okCls}" id="m-ok">${esc(okText)}</button></div></div>`;
    const done = (v) => { back.remove(); m.remove(); resolve(v); };
    back.onclick = () => done(null);
    document.body.append(back, m);
    m.querySelector('#m-cancel').onclick = () => done(null);
    m.querySelector('#m-ok').onclick = () => {
      const c = m.querySelector('#m-comment').value.trim();
      if (requireText && !c) { m.querySelector('#m-comment').focus(); return; }
      done({ comment: c });
    };
  });
}
export const loading = () => html`<div class="empty"><span class="spinner"></span> Loading…</div>`;

// ---------------------------------------------------------------- charts (ECharts, validated palette)
export const C = { s1: '#2a78d6', s2: '#eb6834', s3: '#1baf7a', s4: '#eda100', s5: '#e87ba4', s6: '#008300',
  ink: '#101828', ink2: '#475467', grid: '#eef0f3', band: 'rgba(42,120,214,0.26)', good: '#0ca30c', crit: '#d03b3b' };
const charts = new Set();
export function chart(el, option) {
  if (!el || !window.echarts) return null;
  const c = window.echarts.init(el, null, { renderer: 'svg' });
  c.setOption({ textStyle: { fontFamily: 'Inter, Segoe UI, system-ui, sans-serif', color: C.ink2 }, animationDuration: 400,
    tooltip: { backgroundColor: '#fff', borderColor: '#e3e6eb', textStyle: { color: C.ink, fontSize: 12 },
      extraCssText: 'box-shadow:0 4px 14px rgba(16,24,40,.12);border-radius:8px;' }, ...option });
  charts.add(c);
  return c;
}
export function disposeCharts() { charts.forEach((c) => c.dispose()); charts.clear(); }
window.addEventListener('resize', () => charts.forEach((c) => c.resize()));
export const axisMoney = { axisLabel: { formatter: (v) => usd(v, 0) }, splitLine: { lineStyle: { color: C.grid } } };

export function fanChart(el, weeks, band, { covenant = null, name = 'P50' } = {}) {
  const x = weeks;
  const series = [
    { name: 'P10', type: 'line', data: band.p10, lineStyle: { opacity: 0 }, stack: 'band', symbol: 'none', silent: true },
    { name: 'P10–P90 range', type: 'line', data: band.p90.map((v, i) => v - band.p10[i]), lineStyle: { opacity: 0 },
      areaStyle: { color: C.band }, itemStyle: { color: C.band }, stack: 'band', symbol: 'none', silent: true },
    { name, type: 'line', data: band.p50, lineStyle: { width: 2, color: C.s1 }, itemStyle: { color: C.s1 },
      symbolSize: 7, z: 5 },
  ];
  if (covenant) series.push({ name: 'Covenant minimum', type: 'line', data: x.map(() => covenant), symbol: 'none',
    lineStyle: { type: 'dashed', color: C.crit, width: 1.5 }, itemStyle: { color: C.crit } });
  return chart(el, {
    grid: { left: 64, right: 18, top: 30, bottom: 48 },
    legend: { bottom: 0, data: [name, 'P10–P90 range', ...(covenant ? ['Covenant minimum'] : [])], icon: 'roundRect' },
    tooltip: { trigger: 'axis', formatter: (ps) => {
      const i = ps[0].dataIndex;
      return `<b>${esc(x[i])}</b><br>P90 ${usd(band.p90[i])}<br>P50 <b>${usd(band.p50[i])}</b><br>P10 ${usd(band.p10[i])}` +
        (covenant ? `<br><span style="color:${C.crit}">Covenant ${usd(covenant)}</span>` : '');
    } },
    xAxis: { type: 'category', data: x, boundaryGap: false, axisLine: { lineStyle: { color: '#cfd4dc' } } },
    yAxis: { type: 'value', scale: true, ...axisMoney }, series,
  });
}

// ---------------------------------------------------------------- evidence chain (DATA → … → AUDIT)
export function evidenceChain(a) {
  const ev = a.evidence || {};
  const step = (name, cls, body) => html`<div class="step ${cls}"><h5>${name}</h5>${body}</div>`;
  const data = (ev.data || []).map((d) => (d.source_id ? html`<div>${d.source_id} ${classBadge(d.classification)}</div>`
    : html`<div class="small"><span class="mono">${d.metric}</span> ${basis(d.basis)} <span class="muted">${d.period || ''}</span></div>`));
  const appr = a.approved_by || a.rejected_by;
  return html`<div class="chain">
    ${step('1 · Data', 'done', html`${data}<div class="muted small">${(ev.data || [])[0]?.as_of ? `as of ${ev.data[0].as_of}` : ''}</div>`)}
    ${step('2 · Analysis', 'done', html`<div><b>${ev.analysis?.tool || ''}</b></div><div class="small">${ev.analysis?.method || ''}</div>
      <div class="mono small muted">result ${ev.analysis?.result_sha256 || '—'}</div>`)}
    ${step('3 · Agent', 'done', html`<div><b>${ev.agent?.agent || ''}</b></div><div class="small">${ev.agent?.headline || ''}</div>
      <div class="row small">${confBadge(ev.agent?.confidence)} ${ev.agent?.grounded ? badge('grounded', 'good', '✓ ') : badge('check', 'warn')}</div>`)}
    ${step('4 · Policy', 'done', html`<div>${(ev.policy?.refs || a.policy_refs || []).join(' · ')}</div>
      <div class="small muted">policy ${ev.policy?.policy_version || ''}</div><div class="small">approver: <b>${a.required_approver}</b></div>`)}
    ${step('5 · Recommendation', 'done', html`<div class="small">${a.rationale}</div><div class="strong">${full(a.amount_usd)}</div>`)}
    ${step('6 · Approval', appr ? (a.approved_by ? 'ok' : '') : 'pending', appr
      ? html`<div>${statusBadge(a.status)}</div><div class="small">${appr.user} (${appr.role})</div><div class="small muted">${dt(appr.at)}</div>`
      : html`<div>${statusBadge('PROPOSED')}</div><div class="small">awaiting ${a.required_approver}</div>`)}
    ${step('7 · Audit', 'done', html`<div class="mono small">proposed ${ev.audit?.proposed_record_hash || '—'}</div>
      <div class="mono small">decision ${ev.audit?.decision_record_hash || '—'}</div><div class="small muted">SHA-256 hash chain</div>`)}
  </div>`;
}


// ======================================================================== v3 helpers
export const basis = (b) => html`<span class="basis ${b}" title="${({ REPORTED: 'As-filed value from an SEC filing (XBRL)',
  NOT_AVAILABLE: 'Not available from public data', OBSERVED: 'Read directly from a source record',
  CALCULATED: 'Arithmetic on observed data', FORECAST: 'Model / Monte-Carlo output', ESTIMATED: 'Depends on a stated assumption',
  SCENARIO: 'Output under a user-defined stress' })[b] || ''}">${b}</span>`;
export const prio = (p) => html`<span class="prio ${p}">${p}</span>`;
/** Multiple ("1.55x") or "n/a" when the value is not available — never "nullx". */
export const xx = (v, d = 2) => (v === null || v === undefined || Number.isNaN(Number(v)) ? 'n/a' : `${Number(Number(v).toFixed(d))}x`);
export function metricValue(m) {
  if (!m) return '—';
  if (m.value === null || m.value === undefined) return 'n/a';
  if (m.unit === 'USD') return usd(m.value);
  if (m.unit === 'days') return `${num(m.value)} d`;
  if (m.unit === 'ratio') return pct(m.value, 1);
  if (m.unit === 'rate') return pct(m.value, 2);
  if (m.unit === 'pct') return `${num(m.value, 2)}%`;
  if (m.unit === 'x') return `${num(m.value, 2)}x`;
  return String(m.value);
}
/** KPI tile bound to a lineage-tracked metric: click → Source → Dataset → Date → Transformation → Calculation. */
export function kpiM(m, labelOverride, foot = '') {
  if (!m) return kpi(labelOverride || '—', '—', foot);
  return html`<div class="card kpi click" data-metric="${m.metric_id}" title="Click for lineage">
    <div class="label">${labelOverride || m.label} ${basis(m.basis)}</div><div class="value">${metricValue(m)}</div>
    <div class="foot">${foot}</div><div class="lin">lineage ›</div></div>`;
}
export function wireMetrics(root) {
  $$('[data-metric]', root).forEach((el) => el.onclick = () => metricDrawer(el.dataset.metric));
}
export async function publicMetricDrawer(id) {
  const b = drawer('Where does this number come from?', '<div class="empty"><span class="spinner"></span></div>');
  const m = await api(`/v1/public/metrics/${encodeURIComponent(id)}?cik=${state.cik}`);
  const L = m.lineage;
  if (!L) { b.innerHTML = String(html`<h3>${m.label}</h3><div class="callout warn">${m.note}</div>`); return; }
  b.innerHTML = String(html`
    <div class="row" style="justify-content:space-between"><div><div class="mini">${m.metric_id}</div>
      <div style="font-size:22px;font-weight:650">${metricValue(m)}</div><div>${m.label}</div></div><div>${classBadge('PUBLIC_REAL')} ${basis(m.basis)}</div></div>
    <h4>Lineage</h4><div class="lin-chain">
    <div class="lin-step"><div class="lk">Source</div><div>${L.source}<div class="mini mono">${L.source_url}</div></div></div>
    <div class="lin-step"><div class="lk">Filing</div><div>${L.filings.map((f) => html`<div><b>${f.form}</b> <span class="mono">${f.accession}</span> filed ${f.filed}
      · <a href="${f.url}" target="_blank" rel="noopener noreferrer">EDGAR</a></div>`)}</div></div>
    <div class="lin-step"><div class="lk">Period</div><div><b>${L.period}</b></div></div>
    <div class="lin-step"><div class="lk">Facts</div><div>${L.facts.map((f) => html`<div class="small"><span class="mono">${f.concept}</span> ${f.period}
      = <b>${usd(f.value)}</b> <span class="mini">${f.form} ${f.fiscal_period}</span></div>`)}
      ${(L.market_inputs || []).map((x) => html`<div class="small">${x.input} = <b>${x.value_pct}%</b> as of ${x.as_of} ${classBadge('PUBLIC_REAL')}</div>`)}</div></div>
    <div class="lin-step"><div class="lk">Transformation</div><div>${L.transformation}</div></div>
    <div class="lin-step"><div class="lk">Calculation</div><div class="mono">${L.calculation}</div></div>
    <div class="lin-step"><div class="lk">Result</div><div><b>${metricValue(m)}</b> ${basis(m.basis)}</div></div>
    ${m.assumptions.length ? html`<div class="lin-step"><div class="lk">Assumptions</div><div><ul style="margin:0;padding-left:18px">${m.assumptions.map((a) => html`<li>${a}</li>`)}</ul></div></div>` : ''}
    ${m.note ? html`<div class="lin-step"><div class="lk">Note</div><div class="small">${m.note}</div></div>` : ''}
    <div class="lin-step"><div class="lk">Used by</div><div>${m.used_by.length ? m.used_by.map((r) => html`<div><a href="#/recommendations?r=${encodeURIComponent(r.rec_id)}">${r.short_id} ${r.title}</a></div>`) : html`<span class="muted">No current recommendation cites this metric</span>`}</div></div>
    </div>`);
}
export async function metricDrawer(id) {
  if (state.case === 'public') return publicMetricDrawer(id);
  const b = drawer('Where does this number come from?', '<div class="empty"><span class="spinner"></span></div>');
  const L = await api(`/v1/metrics/${encodeURIComponent(id)}/lineage`);
  const m = L.metric;
  b.innerHTML = String(html`
    <div class="row" style="justify-content:space-between"><div><div class="mini">${m.metric_id}</div>
      <div style="font-size:22px;font-weight:650">${metricValue(m)}</div><div>${m.label}</div></div>${basis(m.basis)}</div>
    <h4>Lineage</h4><div class="lin-chain">
    ${L.inputs.map((i) => html`<div class="lin-step"><div class="lk">Source</div><div>${i.dataset.system || '—'}
      ${classBadge(i.dataset.classification)}<div class="mini">${(i.upstream || []).filter((u) => u.layer === 'RAW').map((u) => u.dataset_id).join(', ')}</div></div></div>
      <div class="lin-step"><div class="lk">Dataset · date</div><div><span class="mono">${i.input}</span> · as of <b>${i.dataset.as_of || '—'}</b>
      · ${i.dataset.layer || ''} ${i.dataset.validation ? statusBadge(i.dataset.validation) : ''}
      <div class="mini">${i.dataset.transformation || ''}</div></div></div>`)}
    <div class="lin-step"><div class="lk">Transformation</div><div>${(m.steps || []).length ? html`<ol style="margin:0;padding-left:18px">${m.steps.map((x) => html`<li>${x}</li>`)}</ol>` : '—'}
      <div class="mini">${(L.data_product || {}).name || ''} — ${(L.data_product || {}).transformation || ''}</div></div></div>
    <div class="lin-step"><div class="lk">Calculation</div><div class="mono">${m.formula}</div></div>
    ${L.model ? html`<div class="lin-step"><div class="lk">Model</div><div class="mono">${L.model.model_id}</div></div>` : ''}
    <div class="lin-step"><div class="lk">Used by</div><div>${L.used_by.length ? L.used_by.map((r) => html`<div><a href="#/recommendations?r=${encodeURIComponent(r.rec_id)}">${r.short_id} ${r.title}</a> ${statusBadge(r.status)}</div>`) : html`<span class="muted">No current recommendation cites this metric</span>`}</div></div>
    </div>`);
}
export function waterfall(el, steps) {
  // steps: [{label, value, kind: total|subtotal|delta}] — floating bars drawn from [lo, hi], so the running total may cross zero.
  let run = 0;
  const rows = steps.map((s, i) => {
    let lo, hi, k;
    if (s.kind === 'total' || s.kind === 'subtotal') { lo = Math.min(0, s.value); hi = Math.max(0, s.value); run = s.value; k = 't'; }
    else { const a0 = run; run += s.value; lo = Math.min(a0, run); hi = Math.max(a0, run); k = s.value >= 0 ? 'u' : 'd'; }
    return { value: [i, lo, hi, s.value], k };
  });
  const col = { t: C.s1, u: C.s3, d: C.s2 };
  return chart(el, {
    grid: { left: 64, right: 16, top: 22, bottom: 60 },
    tooltip: { trigger: 'item', formatter: (p) => { const s = steps[p.value[0]];
      return `<b>${esc(s.label)}</b><br>${usd(s.value)}${s.basis ? `<br><span style="color:#667085">${esc(s.basis)}</span>` : ''}`; } },
    xAxis: { type: 'category', data: steps.map((s) => s.label), axisLabel: { interval: 0, width: 90, overflow: 'break', fontSize: 11 } },
    yAxis: { type: 'value', scale: false, ...axisMoney },
    series: [{ type: 'custom', data: rows.map((r) => ({ value: r.value, itemStyle: { color: col[r.k] }, k: r.k })),
      encode: { x: 0, y: [1, 2] },
      renderItem: (params, api) => {
        const i = api.value(0), lo = api.coord([i, api.value(1)]), hi = api.coord([i, api.value(2)]);
        const w = Math.min(44, api.size([1, 0])[0] * 0.6);
        const h = Math.max(1.5, lo[1] - hi[1]);
        const v = api.value(3);
        return { type: 'group', children: [{ type: 'rect', shape: { x: lo[0] - w / 2, y: hi[1], width: w, height: h, r: 3 }, style: { fill: api.visual('color') } },
          { type: 'text', style: { text: usd(v), x: lo[0], y: hi[1] - 4, textAlign: 'center', textVerticalAlign: 'bottom', fontSize: 10, fill: C.ink2 } }] };
      },
      label: { show: true, position: 'top', fontSize: 10, formatter: (p) => (rows[p.dataIndex].k === 't' ? usd(p.value[3]) : '') } }],
  });
}
export function heatmap5(el, risks, onClick) {
  const pts = risks.map((r) => [r.impact - 1, r.likelihood - 1, r.score, r]);
  const c = chart(el, {
    grid: { left: 70, right: 16, top: 10, bottom: 46 },
    tooltip: { formatter: (p) => (p.data[3] ? `<b>${esc(p.data[3].title)}</b><br>${esc(p.data[3].category)} · score ${p.data[2]}` : '') },
    xAxis: { type: 'category', data: ['1', '2', '3', '4', '5'], name: 'Impact →', nameLocation: 'middle', nameGap: 28, splitArea: { show: false } },
    yAxis: { type: 'category', data: ['1', '2', '3', '4', '5'], name: 'Likelihood', nameLocation: 'middle', nameGap: 40 },
    visualMap: { show: false, min: 1, max: 25, inRange: { color: ['#e9f7e9', '#fff6e0', '#fdeee8', '#f6caca'] } },
    series: [
      { type: 'heatmap', data: [0, 1, 2, 3, 4].flatMap((x) => [0, 1, 2, 3, 4].map((y) => [x, y, (x + 1) * (y + 1)])), silent: true,
        itemStyle: { borderColor: '#fff', borderWidth: 2 } },
      { type: 'scatter', data: pts, symbolSize: 18, itemStyle: { color: C.ink, borderColor: '#fff', borderWidth: 2 },
        label: { show: true, formatter: (p) => p.data[3].risk_id.split('-')[1], position: 'right', fontSize: 10, color: C.ink } },
    ],
  });
  if (c && onClick) c.on('click', (p) => p.data && p.data[3] && onClick(p.data[3]));
  return c;
}
export function stepper(states, current, blocked) {
  const i = states.indexOf(current);
  return html`<div class="stepper">${states.map((s, k) => html`<div class="st ${k < i ? 'done' : k === i ? (blocked ? 'blocked' : 'now') : ''}">${s.replace(/_/g, ' ')}</div>`)}</div>`;
}
export function impactTable(impacts) {
  return html`<div class="imp">${impacts.map((i) => html`<div>${i.label}${i.formula ? html`<div class="mini">${i.formula}</div>` : ''}</div>
    <div class="v">${usd(i.value_usd)}</div><div>${basis(i.basis)}</div>`)}</div>`;
}
export function answerView(a) {
  return html`<div class="answer">
    <div class="row">${confBadge(a.confidence)} ${(a.confidence_reasons || []).slice(0, 3).map((r) => badge(r, 'muted'))}</div>
    <div class="concl">${a.conclusion || a.headline}</div>
    ${a.financial_impact && a.financial_impact.length ? html`<div class="sec"><h5>Financial impact</h5>${impactTable(a.financial_impact)}</div>` : ''}
    <div class="grid g-2">
      <div class="sec"><h5>Reasoning</h5><ol style="margin:0;padding-left:18px">${(a.reasoning || []).map((x) => html`<li>${x}</li>`)}</ol></div>
      <div class="sec"><h5>Sources</h5>${(a.sources || []).map((s) => html`<div class="row small">${classBadge(s.classification)}
        <span class="mono">${s.source_id}</span><span class="muted">${s.system || ''} · as of ${s.as_of || 'n/a'}</span></div>`)}</div>
    </div>
    <div class="grid g-2">
      <div class="sec"><h5>Policy</h5>${(a.policy_refs || []).map((p) => badge(p, 'info'))} <span class="mini">policy ${a.evidence?.policy_version || ''}</span>
        ${a.metrics && a.metrics.length ? html`<h5 style="margin-top:10px">Metrics (click for lineage)</h5>${a.metrics.map((m) => html`<a class="chip" data-metric="${m}">${m}</a> `)}` : ''}</div>
      <div class="sec"><h5>Assumptions & limitations</h5><ul style="margin:0;padding-left:18px">${[...(a.assumptions || []), ...(a.limitations || [])].map((x) => html`<li>${x}</li>`)}</ul></div>
    </div></div>`;
}
