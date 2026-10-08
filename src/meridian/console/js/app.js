// Meridian Console — enterprise shell, authentication, navigation, command palette, alerts.
import { $, $$, api, closeDrawer, disposeCharts, esc, html, session, state, toast } from './core.js';
import * as D from './p_decisions.js';
import * as E from './p_exec.js';
import * as G from './p_gov.js';
import * as L from './p_liquidity.js';
import * as P from './p_public.js';
import * as W from './p_wc.js';

export const NAV = [
  ['Executive', [['command', '◎', 'Executive Command Center', E.command]]],
  ['Liquidity & Treasury', [['liquidity', '≋', 'Liquidity Intelligence', L.liquidity], ['network', '⋈', 'Cash & Entity Network', L.network],
    ['forecast', '↗', 'Forecast Studio', L.forecast], ['scenarios', '⚡', 'Scenario Lab', L.scenarios]]],
  ['Working Capital', [['working-capital', '⇄', 'Working Capital', W.workingCapital]]],
  ['Decisions', [['copilot', '✦', 'AI Decision Center', D.copilot], ['recommendations', '◆', 'Recommendations', D.recommendations],
    ['approvals', '✔', 'Approval Center', D.approvals]]],
  ['Governance', [['policy', '§', 'Policy & Evidence', G.policy], ['lineage', '⛁', 'Data Lineage', G.lineage],
    ['audit', '⛓', 'Audit & Governance', G.audit]]],
  ['Platform', [['operations', '◉', 'System Operations', G.operations], ['admin', '⚙', 'Administration', G.admin]]],
];
const PAGES = Object.fromEntries(NAV.flatMap(([, items]) => items.map(([id, , , fn]) => [id, fn])));
// PUBLIC · REAL company case (primary). Workspaces whose evaluation version needs private transaction data are replaced
// by public-filing implementations; governance workspaces are shared and filtered by case.
const PUBLIC_PAGES = { command: P.command, liquidity: P.liquidity, network: P.network, 'working-capital': P.workingCapital,
  forecast: P.forecast, scenarios: P.scenarios, copilot: P.copilot, lineage: P.lineage };
const LABEL = Object.fromEntries(NAV.flatMap(([, items]) => items.map(([id, , l]) => [id, l])));
const ROLE_NAMES = { analyst: 'Finance Analyst', wc_manager: 'Working Capital Manager', treasurer: 'Group Treasurer', cfo: 'CFO',
  auditor: 'Internal Audit', admin: 'Finance Administrator' };
export const roleName = (r) => ROLE_NAMES[r] || r;
const initials = (n) => (n || '?').split(' ').map((p) => p[0]).join('').slice(0, 2).toUpperCase();

async function boot() {
  try { state.env = await api('/v1/system/environment'); } catch { state.env = { mode: 'unknown' }; }
  if (!session.get('token')) return renderLogin();
  try {
    [state.user, state.ready] = await Promise.all([api('/v1/auth/me'), api('/ready')]);
    [state.prov, state.cases] = await Promise.all([api('/v1/governance/provenance'), api('/v1/cases')]);
  } catch { return renderLogin(); }
  state.case = session.get('case') || 'public';
  const pubs = state.cases.filter((c) => c.case === 'public');
  state.cik = +(session.get('cik') || 0) || (pubs.find((c) => c.primary) || pubs[0] || {}).cik;
  if (!pubs.some((c) => c.cik === state.cik)) state.cik = (pubs.find((c) => c.primary) || pubs[0] || {}).cik;
  renderShell();
  window.addEventListener('hashchange', route);
  route();
}

// ---------------------------------------------------------------- login
function renderLogin() {
  const env = state.env || {};
  const demo = env.mode === 'demo';
  $('#root').innerHTML = String(html`
  <div class="login-wrap">
    <div class="login-art">
      <div class="brand" style="padding:0"><div class="brand-mark">M</div><div><div class="brand-name">MERIDIAN</div>
        <div class="brand-sub">Liquidity, Treasury & Working-Capital Intelligence</div></div></div>
      <h1>From data to an approved, audited treasury decision.</h1>
      <p>Group liquidity, 13-week probabilistic forecasting, working-capital drivers, funding and covenant risk —
        with governed AI agents, policy-as-code approvals and end-to-end lineage behind every number.</p>
      <div class="login-feats">
        <div>◎ Data → forecast → optimisation → recommendation</div><div>✔ Approval matrix, SoD, dual-control release</div>
        <div>⛁ Source → dataset → date → calculation lineage</div><div>⛓ Hash-chained audit trail</div>
      </div>
    </div>
    <div class="login-panel"><div class="login-box">
      <h2>Sign in</h2>
      <div class="muted" style="margin-bottom:18px">${env.ai_status?.label || ''}</div>
      <div class="callout small" style="margin-bottom:16px"><b>Primary case: Caterpillar Inc. — PUBLIC · REAL.</b> Company figures come from
        SEC EDGAR XBRL filings; market data from the NY Fed, U.S. Treasury, ECB and FRED. Transaction-level capabilities are demonstrated on a
        separately labelled SYNTHETIC · EVALUATION company. No ERP, bank or treasury system is connected (CLIENT · NOT CONFIGURED).</div>
      <form id="login-form" class="stack">
        <div><label class="lbl" for="u">Username</label><input class="input" id="u" autocomplete="username" required></div>
        <div><label class="lbl" for="p">Password</label><input class="input" id="p" type="password" autocomplete="current-password" required></div>
        <div id="login-err" class="small" style="color:#b42318;min-height:18px"></div>
        <button class="btn primary" style="width:100%;justify-content:center" type="submit">Sign in</button>
      </form>
      ${demo && env.demo_accounts?.length ? html`<div class="demo-accounts small"><b>Evaluation accounts</b> (password
        <span class="mono">${env.demo_password}</span> — disabled outside evaluation mode)<br>${env.demo_accounts.map((a) =>
        html`<button class="btn" data-u="${a.username}" type="button">${a.name} · ${roleName(a.role)}</button>`)}</div>` : ''}
      <div class="muted small" style="margin-top:18px">Meridian v${env.version || ''} · ${String(env.mode || '').toUpperCase()} mode</div>
    </div></div>
  </div>`);
  $$('.demo-accounts button').forEach((b) => b.onclick = () => { $('#u').value = b.dataset.u; $('#p').value = env.demo_password; $('#p').focus(); });
  $('#login-form').onsubmit = async (ev) => {
    ev.preventDefault();
    try {
      const r = await api('/v1/auth/login', { method: 'POST', body: { username: $('#u').value.trim(), password: $('#p').value } });
      session.set('token', r.access_token);
      location.hash = '#/command'; location.reload();
    } catch (e) { $('#login-err').textContent = e.message; }
  };
}

// ---------------------------------------------------------------- shell
function freshness() {
  const p = state.prov || [];
  const stale = p.filter((x) => x.freshness === 'STALE' || x.validation === 'FAILED');
  const mkt = p.filter((x) => x.classification === 'PUBLIC_MARKET_DATA').map((x) => x.as_of).filter(Boolean).sort();
  return { stale, latest: mkt[mkt.length - 1], oldest: mkt[0] };
}

function renderShell() {
  const u = state.user, r = state.ready, demo = r.mode === 'demo';
  const f = freshness();
  const pub = state.case === 'public';
  const cs = state.cases.find((c) => c.case === state.case && (c.case !== 'public' || c.cik === state.cik)) || state.cases[0];
  const ai = r.ai_status || {};
  $('#root').innerHTML = String(html`
  <div class="shell3">
    <aside class="sidebar">
      <div class="brand"><div class="brand-mark">M</div><div><div class="brand-name">MERIDIAN</div>
        <div class="brand-sub">Treasury Intelligence</div></div></div>
      ${NAV.map(([g, items]) => html`<div class="nav-group"><div class="nav-group-title">${g}</div>
        ${items.map(([id, ic, label]) => html`<div class="nav-item" data-page="${id}"><span class="nav-icon">${ic}</span>${label}
          ${id === 'approvals' ? html`<span class="count" id="appr-count" hidden></span>` : ''}</div>`)}</div>`)}
      <div class="sidebar-foot">v${r.version} · policy ${r.policy_version}<br>${pub ? `SEC snapshot · ${cs.ticker} to ${cs.as_of}` : String(r.model_id || '').slice(0, 30)}</div>
    </aside>
    <header class="cmdbar">
      <div class="org" id="org-btn" title="Company / case">${cs.name}
        <small>${pub ? `PUBLIC · REAL · ${cs.ticker || 'CIK ' + cs.cik} · SEC filings to ${cs.as_of} · switch / add company` : 'SYNTHETIC · EVALUATION dataset'} ▾</small></div>
      <div class="search" id="cmd-btn"><span class="st">⌕ Search workspaces, metrics, recommendations…</span><kbd>Ctrl K</kbd></div>
      <div style="flex:1"></div>
      <span class="chip3 ${demo ? 'demo' : 'prod'}" title="Environment">${demo ? '◆ EVALUATION DEPLOYMENT' : '● PRODUCTION'}</span>
      <span class="chip3 ${f.stale.length ? 'stale' : 'fresh'}" id="fresh-chip" title="Data freshness">
        ${f.stale.length ? `! ${f.stale.length} stale source(s)` : `◷ Market data ${f.latest || 'n/a'}`}</span>
      <button class="iconbtn" id="appr-btn" title="Approvals awaiting you">✔<span class="dot" id="appr-dot" hidden></span></button>
      <button class="iconbtn" id="alert-btn" title="Alerts">⚑<span class="dot" id="alert-dot" hidden></span></button>
      <div class="me" id="me-btn"><div class="avatar">${initials(u.display_name)}</div>
        <div><div style="color:#fff;font-weight:600;font-size:12.5px">${u.display_name}</div><small>${roleName(u.role)}</small></div></div>
    </header>
    <div class="ctxbar ${pub ? 'pub' : 'syn'}">
      <span>Company data ${pub ? html`<span class="badge b-real">● PUBLIC · REAL</span> <b>${cs.name}</b> — SEC EDGAR XBRL, latest period ${cs.as_of}`
        : html`<span class="badge b-synth">◆ SYNTHETIC · EVALUATION</span> <b>${cs.name}</b> — simulator, never real`}</span><span class="sep"></span>
      <span>Market data <span class="badge b-real">● PUBLIC · REAL</span> <b>${r.market_data === 'snapshot' ? 'dated snapshot' : r.market_data === 'live' ? 'live feeds' : 'none'}</b></span><span class="sep"></span>
      <span>AI <b class="${ai.configured ? '' : 'ai-off'}">${ai.label || r.llm_provider}</b></span><span class="sep"></span>
      <span>ERP / bank / TMS <span class="badge b-muted">— CLIENT · NOT CONFIGURED</span></span><span class="sep"></span>
      <span>Store <b>${r.store}</b> · v${r.version}</span>
    </div>
    <main class="main" id="main"></main>
  </div>`);
  $$('.nav-item').forEach((n) => n.onclick = () => { location.hash = `#/${n.dataset.page}`; });
  $('#me-btn').onclick = (e) => userMenu(e.currentTarget);
  $('#alert-btn').onclick = (e) => alertMenu(e.currentTarget);
  $('#appr-btn').onclick = () => { location.hash = '#/approvals'; };
  $('#org-btn').onclick = (e) => orgMenu(e.currentTarget);
  $('#cmd-btn').onclick = palette;
  $('#fresh-chip').onclick = () => { location.hash = '#/lineage'; };
  document.addEventListener('keydown', (e) => { if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') { e.preventDefault(); palette(); } });
  refreshApprovalCount();
  refreshAlerts();
}

function popMenu(anchor, inner) {
  closeMenus();
  const m = document.createElement('div');
  m.className = 'menu'; m.id = 'menu';
  m.innerHTML = String(inner);
  const r = anchor.getBoundingClientRect();
  m.style.top = `${r.bottom + 6}px`;
  m.style.left = `${Math.max(8, Math.min(window.innerWidth - 430, r.right - 320))}px`;
  document.body.appendChild(m);
  setTimeout(() => document.addEventListener('click', closeMenus, { once: true }), 0);
  return m;
}
function closeMenus() { $('#menu')?.remove(); }

function userMenu(anchor) {
  const u = state.user;
  const m = popMenu(anchor, html`<div class="mh">Signed in</div><div class="mi"><div><b>${u.display_name}</b><div class="mini">${u.username} · ${roleName(u.role)}</div>
    <div class="mini">${(u.permissions || []).join(' · ')}</div></div></div>
    <div class="mi" id="mi-out">⏻ Sign out (revokes this session server-side)</div>`);
  m.querySelector('#mi-out').onclick = async () => {
    try { await api('/v1/auth/logout', { method: 'POST' }); } catch { /* ignore */ }
    session.clear(); location.hash = ''; location.reload();
  };
}
function orgMenu(anchor) {
  const m = popMenu(anchor, html`<div class="mh">Companies — PUBLIC · REAL (SEC EDGAR)</div>
    ${state.cases.filter((c) => c.case === 'public').map((c) => html`<div class="mi" data-case="public" data-cik="${c.cik}">
      <div><b>${c.name}</b> ${c.ticker ? html`<span class="mono">${c.ticker}</span>` : ''} ${state.case === 'public' && c.cik === state.cik ? '✓' : ''}
      <div class="mini">CIK ${String(c.cik).padStart(10, '0')} · filings to ${c.as_of} · ${c.origin}</div></div></div>`)}
    ${state.user.permissions.includes('run_agents') ? html`<div class="mi" style="display:block" id="add-co">
      <div class="mini" style="margin-bottom:6px">Add any SEC-reporting company (ticker or CIK). Facts are fetched live from SEC EDGAR —
        nothing is estimated or invented; filers without us-gaap 10-K/10-Q data are refused with the reason.</div>
      <div class="row"><input class="input" id="add-q" maxlength="12" placeholder="e.g. DE, HON, KO or 0000021344" style="flex:1">
        <button class="btn primary" id="add-go">Add</button></div><div class="mini" id="add-msg"></div></div>` : ''}
    <div class="mh">Evaluation</div>${state.cases.filter((c) => c.case === 'synthetic').map((c) => html`<div class="mi" data-case="synthetic">
      <div><b>${c.name}</b> ${state.case === 'synthetic' ? '✓' : ''}<div class="mini">${c.label} · ${c.source}</div></div></div>`)}
    <div class="mh">Client organisations</div><div class="mi mini">CLIENT · NOT CONFIGURED. A client is added by a Finance Administrator
    through governed ingestion (ERP CSV package + ISO 20022 camt.053 / BAI2) or an ERP/bank connector.</div>`);
  m.onclick = (e) => e.stopPropagation();
  $$('.mi[data-case]', m).forEach((el) => el.onclick = () => {
    session.set('case', el.dataset.case);
    if (el.dataset.cik) session.set('cik', el.dataset.cik);
    for (const k of Object.keys(state.cache)) delete state.cache[k];
    location.hash = '#/command'; location.reload();
  });
  const go = async () => {
    const q = $('#add-q').value.trim();
    if (!q) return;
    $('#add-go').disabled = true;
    $('#add-msg').textContent = 'Fetching XBRL company facts from SEC EDGAR…';
    try {
      const r = await api('/v1/companies', { method: 'POST', body: { query: q } });
      $('#add-msg').textContent = `${r.name} ${r.status === 'EXISTS' ? 'already added' : 'added'} — opening…`;
      session.set('case', 'public'); session.set('cik', String(r.cik));
      for (const k of Object.keys(state.cache)) delete state.cache[k];
      location.hash = '#/command'; location.reload();
    } catch (e) { $('#add-msg').textContent = e.message; $('#add-go').disabled = false; }
  };
  $('#add-go')?.addEventListener('click', go);
  $('#add-q')?.addEventListener('keydown', (e) => { if (e.key === 'Enter') go(); });
}
async function alertMenu(anchor) {
  const a = await api(`/v1/alerts?case=${state.case === 'public' ? `public&cik=${state.cik}` : 'synthetic'}`).catch(() => []);
  const m = popMenu(anchor, html`<div class="mh">Alerts (${a.length})</div>${a.length ? a.map((x, i) => html`<div class="mi" data-i="${i}">
    <span class="sev ${x.severity}"></span><div><div>${x.title}</div><div class="mini">${x.category}</div></div></div>`) : html`<div class="mi mini">No alerts</div>`}`);
  $$('.mi[data-i]', m).forEach((el) => el.onclick = () => { location.hash = a[+el.dataset.i].link; });
}
async function refreshAlerts() {
  const a = await api(`/v1/alerts?case=${state.case === 'public' ? `public&cik=${state.cik}` : 'synthetic'}`).catch(() => []);
  const n = a.filter((x) => x.severity !== 'info').length;
  const d = $('#alert-dot'); if (d) { d.hidden = !n; d.textContent = n; }
}
export async function refreshApprovalCount() {
  try {
    const q = await api('/v1/approvals/queue');
    const n = q.filter((x) => x.can_approve && (!state.case || x.case === state.case) && (state.case !== 'public' || x.cik === state.cik)).length;
    for (const id of ['#appr-count', '#appr-dot']) { const el = $(id); if (el) { el.hidden = !n; el.textContent = n; } }
  } catch { /* ignore */ }
}

// ---------------------------------------------------------------- command palette
async function palette() {
  closeMenus();
  if ($('.palette')) return;
  const back = document.createElement('div'); back.className = 'backdrop';
  const p = document.createElement('div'); p.className = 'palette';
  p.innerHTML = '<input placeholder="Jump to a workspace, entity, metric or recommendation…" autofocus><div class="res"></div>';
  document.body.append(back, p);
  const close = () => { back.remove(); p.remove(); };
  back.onclick = close;
  const items = NAV.flatMap(([g, its]) => its.map(([id, , l]) => ({ label: l, sub: g, go: `#/${id}` })));
  try {
    const pub = state.case === 'public';
    const [ents, mets, recs] = await Promise.all([pub ? [] : api('/v1/entities'), api(pub ? `/v1/public/metrics?cik=${state.cik}` : '/v1/metrics'),
      api(`/v1/recommendations?case=${state.case}${pub ? `&cik=${state.cik}` : ''}`)]);
    ents.forEach((e) => items.push({ label: `${e.entity_id} ${e.name}`, sub: 'Entity · Cash & Entity Network', go: `#/network?n=${e.entity_id}` }));
    mets.filter((m) => m.value !== null).forEach((m) => items.push({ label: m.label, sub: `Metric lineage · ${m.basis}`, metric: m.metric_id }));
    recs.forEach((r) => items.push({ label: `${r.short_id} ${r.title}`, sub: `Recommendation · ${r.status}`, go: `#/recommendations?r=${encodeURIComponent(r.rec_id)}` }));
  } catch { /* palette still works with workspaces */ }
  let sel = 0, shown = items.slice(0, 12);
  const inp = p.querySelector('input'), res = p.querySelector('.res');
  const draw = () => {
    res.innerHTML = shown.map((it, i) => `<div class="ri ${i === sel ? 'on' : ''}" data-i="${i}"><span>${esc(it.label)}</span><small>${esc(it.sub)}</small></div>`).join('');
    $$('.ri', res).forEach((el) => el.onclick = () => go(shown[+el.dataset.i]));
  };
  const go = async (it) => { close(); if (it.metric) { const { metricDrawer } = await import('./core.js'); metricDrawer(it.metric); } else location.hash = it.go; };
  inp.oninput = () => { const t = inp.value.toLowerCase(); shown = items.filter((x) => `${x.label} ${x.sub}`.toLowerCase().includes(t)).slice(0, 14); sel = 0; draw(); };
  inp.onkeydown = (e) => {
    if (e.key === 'ArrowDown') { sel = Math.min(sel + 1, shown.length - 1); draw(); e.preventDefault(); }
    if (e.key === 'ArrowUp') { sel = Math.max(sel - 1, 0); draw(); e.preventDefault(); }
    if (e.key === 'Enter' && shown[sel]) go(shown[sel]);
    if (e.key === 'Escape') close();
  };
  draw(); inp.focus();
}

// ---------------------------------------------------------------- routing
export function params() { return new URLSearchParams((location.hash.split('?')[1]) || ''); }
async function route() {
  const page = (location.hash.replace('#/', '').split('?')[0]) || 'command';
  if (page === 'login') return;
  const fn = (state.case === 'public' && PUBLIC_PAGES[page]) || PAGES[page] || PAGES.command;
  $$('.nav-item').forEach((n) => n.classList.toggle('active', n.dataset.page === page));
  document.title = `${LABEL[page] || 'Meridian'} · Meridian`;
  closeDrawer(); closeMenus(); disposeCharts();
  const main = $('#main');
  main.scrollTop = 0;
  try { await fn(main); } catch (e) {
    main.innerHTML = String(html`<div class="callout crit"><b>Could not load this workspace.</b> ${e.message}</div>`);
    if (e.status !== 403) toast(e.message, true);
  }
}

window.addEventListener('error', (e) => console.error(esc(e.message)));
boot();
