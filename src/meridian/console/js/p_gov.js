// Policy & Evidence · Data Lineage · Audit & Governance · System Operations · Administration
import {
  $, $$, C, api, badge, basis, card, chart, classBadge, confirmModal, drawer, dt, esc, html, kpi, loading, metricDrawer,
  num, pct, state, statusBadge, table, title, toast,
} from './core.js';
import { can, head, set } from './p_common.js';
import { roleName } from './app.js';

// ================================================================= POLICY & EVIDENCE
export async function policy(main) {
  set(main, loading());
  const [pol, docs, quar] = await Promise.all([api('/v1/governance/policy'), api('/v1/policy/documents'), api('/v1/governance/quarantine')]);
  set(main, html`
    ${head('Policy & Evidence', html`Governed retrieval over versioned policy documents (hybrid vector + BM25, reciprocal-rank fusion) and
      policy-as-code <b>${pol.version}</b>. Injected or malicious content is quarantined at ingestion and never reaches an agent.`)}
    <div class="grid g-3"><div class="span-2">${card('Search the rulebook', html`<div class="row"><input class="input" id="pq" style="flex:1" maxlength="300" value="${state.case === 'public' ? 'refinancing readiness debt due within twelve months' : 'Who approves a revolver prepayment?'}">
        <button class="btn primary" id="ps">Search</button></div>
        <div class="chips" style="margin-top:8px">${[...(state.case === 'public' ? ['peer benchmarks context only', 'disclosure of data limitations', 'repricing exposure EBITDA'] : []), 'refinancing horizon maturity', 'bank counterparty limit', 'payment release dual control', 'pay suppliers on terms',
          'what is the weather tomorrow'].map((x) => html`<button class="chip" data-q="${x}">${x}</button>`)}</div>
        <div id="pr" style="margin-top:12px"></div>`, { hint: 'relevance floor rejects off-topic queries' })}</div>
      ${card('Quarantined content', quar.length ? html`<ul class="list-plain">${quar.map((c) => html`<li>${badge('QUARANTINED', 'crit', '⛔ ')}
        <b>${c.clause_id}</b> · ${c.source}<div class="small muted">Prompt-injection pattern detected at ingestion; stored for audit, never retrieved.</div>
        <details><summary class="small">show content (untrusted)</summary><pre class="mono small" style="white-space:pre-wrap">${c.text}</pre></details></li>`)}</ul>` : html`<div class="muted">None</div>`)}</div>
    <div style="margin-top:16px">${card('Policy documents (versioned)', table([{ key: 'title', label: 'Document' }, { key: 'version', label: 'Version' },
      { key: 'effective', label: 'Effective' }, { key: 'owner', label: 'Owner' }, { key: 'clauses', label: 'Clauses', fmt: (v) => v.join(' · ') },
      { key: 'sha256', label: 'SHA-256', fmt: (v) => html`<span class="mono">${v}</span>` },
      { key: 'quarantined_clauses', label: 'Quarantined', fmt: (v) => (v.length ? badge(v.join(', '), 'crit') : '') }], docs, { maxH: false }),
      { hint: html`${classBadge('REFERENCE')} fictional evaluation documents — replace with the client's executed agreements` })}</div>
    <div class="grid g-2" style="margin-top:16px">
      ${card(`Approval matrix — policy-as-code ${pol.version}`, table([{ key: 'action', label: 'Action', fmt: (v) => title(v) },
        { key: 'when', label: 'Condition', fmt: (v, r) => (v ? title(v) : r.max_amount_usd ? `≤ $${(r.max_amount_usd / 1e6).toFixed(0)}M` : 'otherwise') },
        { key: 'approver_role', label: 'Approver', fmt: (v) => badge(roleName(v), 'info') }, { key: 'policy_ref', label: 'Clause' }], pol.approval_matrix, { maxH: false }))}
      ${card('Controls enforced in code', html`<dl class="kv"><dt>Segregation of duties</dt><dd>${pol.segregation_of_duties ? badge('enforced', 'good', '✓ ') : badge('off', 'crit')} requester and submitter can never approve (TP-2.2)</dd>
        <dt>Autonomous execution</dt><dd>${pol.autonomous_execution ? badge('ON', 'crit') : badge('disabled', 'good', '✓ ')} agents have no payment tool (TP-7.1)</dd>
        <dt>Payment release</dt><dd>roles ${pol.execution.release_roles.join(', ')} · dual control · channel <b>${pol.execution.transmission_channel}</b> (TP-7.2)</dd>
        <dt>Role scopes</dt><dd>${Object.entries(pol.role_scopes || {}).map(([r, s]) => html`<div>${roleName(r)}: ${s.join(', ')}</div>`)}</dd>
        <dt>Liquidity floors</dt><dd>management minimum $${(pol.liquidity.management_min_liquidity_usd / 1e6).toFixed(0)}M (TP-3.3) · bank ≤ ${pct(pol.liquidity.bank_max_share, 0)} (TP-5.2)</dd>
        <dt>Debt policy</dt><dd>refinance ≥ ${pol.debt.refinancing_window_months} months before maturity (DP-2.1) · fixed share ${pct(pol.debt.fixed_share_min, 0)}–${pct(pol.debt.fixed_share_max, 0)} (DP-3.1)</dd>
        <dt>Numeric grounding</dt><dd>every $ figure an AI writes must match tool output within ${pct(pol.guardrails.numeric_grounding_tolerance, 0)}</dd>
        <dt>LLM budget</dt><dd>${pol.llm.max_calls_per_run} calls per run</dd></dl>`)}
    </div>`);
  const search = async () => {
    const r = await api(`/v1/policy/search?q=${encodeURIComponent($('#pq').value)}&k=4`);
    const anyRel = r.some((c) => c.relevant);
    set($('#pr'), html`${anyRel ? '' : html`<div class="callout warn small">No sufficiently relevant policy found — Meridian will not cite a clause for this question.</div>`}
      <ul class="list-plain">${r.map((c) => html`<li style="${c.relevant ? '' : 'opacity:.55'}"><div class="row">${badge(c.clause_id, 'info')} <b>${c.clause_title}</b>
        ${statusBadge(c.relevant ? c.confidence : 'LOW')}
        ${/FICTIONAL/.test(c.document) ? badge('evaluation-case document (fictional)', 'synth') : /Reference Policy/.test(c.document) ? badge('Meridian reference policy', 'info') : ''}
        <span class="muted small">${c.document} · version ${c.doc_version} · relevance ${num(c.relevance, 2)} · rank ${c.rrf_rank} · ${dt(c.retrieved_at)}</span></div>
        <div style="margin-top:6px">${c.text}</div><div class="mini">Citation: ${c.citation}</div></li>`)}</ul>`);
  };
  $('#ps').onclick = search; $('#pq').onkeydown = (e) => { if (e.key === 'Enter') search(); };
  $$('.chip[data-q]', main).forEach((c) => c.onclick = () => { $('#pq').value = c.dataset.q; search(); });
  search();
}

// ================================================================= DATA LINEAGE
const LAYERS = ['RAW', 'VALIDATED', 'NORMALIZED', 'ANALYTICAL', 'DECISION'];
export async function lineage(main) {
  set(main, loading());
  const [cons, cat, prov, ings, mets] = await Promise.all([api('/v1/connectors'), api('/v1/catalog'), api('/v1/governance/provenance'),
    api('/v1/governance/ingestions'), api('/v1/metrics')]);
  const ing = ings[0];
  set(main, html`
    ${head('Data Lineage', 'Raw → Validated → Normalized → Analytical → Decision. Every metric answers: source → dataset → date → transformation → calculation → recommendation.')}
    <div class="grid g-4" style="grid-template-columns:repeat(5,minmax(0,1fr))">${LAYERS.map((l) => kpi(l, String(cat.filter((d) => d.layer === l).length), 'datasets'))}</div>
    <div style="margin-top:16px">${card('Connectors — what feeds Meridian in this deployment', table([{ key: 'category', label: 'Category' }, { key: 'name', label: 'Connector' },
      { key: 'standard', label: 'Standard' }, { key: 'status', label: 'Status', fmt: (v) => statusBadge(v) },
      { key: 'datasets', label: 'Datasets · as of', fmt: (v) => v.map((d) => html`<div class="small"><span class="mono">${d.source_id}</span> ${classBadge(d.classification)} ${d.as_of || ''}</div>`) },
      { key: 'note', label: 'Note', fmt: (v) => html`<span class="mini">${v}</span>` }], cons, { maxH: false }),
      { hint: 'statuses are computed — NOT_CONFIGURED means no client exists for that system in this build' })}</div>
    <div class="grid g-3" style="margin-top:16px"><div class="span-2">${card('Catalog lineage graph', html`<div class="chart xl" id="c-g"></div>`, { hint: 'columns = layers; hover a dataset' })}</div>
      ${card('Metric lineage explorer', html`<select class="input" id="m-sel">${mets.map((m) => html`<option value="${m.metric_id}">${m.label}</option>`)}</select>
        <div id="m-out" style="margin-top:10px"></div>`)}</div>
    <div class="grid g-2" style="margin-top:16px">
      ${card(`Data-quality gate — ${ing ? ing.ingestion_id : 'not run'}`, ing ? html`<div class="row small">${classBadge(ing.classification)} ${ing.loaded ? badge('loaded', 'good', '✓ ') : badge(ing.mode || 'not loaded', 'muted')}
        ${badge(`${ing.errors} errors`, ing.errors ? 'crit' : 'good')} ${badge(`${ing.warnings} warnings`, ing.warnings ? 'warn' : 'muted')}</div>
        <dl class="kv" style="margin-top:8px">${Object.entries(ing.counts || {}).map(([k, v]) => html`<dt>${title(k)}</dt><dd class="tabular">${num(v, 0)}</dd>`)}</dl>
        ${table([{ key: 'file', label: 'File' }, { key: 'level', label: 'Level', fmt: (v) => badge(v, v === 'ERROR' ? 'crit' : 'warn') }, { key: 'rule', label: 'Rule' }, { key: 'detail', label: 'Detail' }], ing.checks || [], { empty: 'No findings — all checks passed' })}`
        : html`<div class="muted">No ingestion recorded.</div>`, { hint: 'schema · IBAN mod-97 · referential integrity · business rules · camt.053 balances' })}
      ${card('Source register (normalized layer)', table([{ key: 'source_id', label: 'Source', fmt: (v, r) => html`<b>${v}</b><div class="mini">${r.system}</div>` },
        { key: 'classification', label: 'Class', fmt: (v) => classBadge(v) }, { key: 'as_of', label: 'As of' },
        { key: 'freshness', label: 'Fresh', fmt: (v) => statusBadge(v) }, { key: 'validation', label: 'Valid', fmt: (v) => statusBadge(v) },
        { key: 'record_count', label: 'Records', num: 1 }], prov, { maxH: true }))}
    </div>`);
  const col = Object.fromEntries(LAYERS.map((l, i) => [l, i]));
  const cnt = {};
  const nodes = cat.map((d) => { const c = col[d.layer]; cnt[c] = (cnt[c] || 0) + 1;
    return { id: d.dataset_id, name: d.dataset_id, ...d, x: c * 230, y: cnt[c] * 30, symbolSize: 10,
      itemStyle: { color: { RAW: '#98a2b3', VALIDATED: C.s4, NORMALIZED: C.s1, ANALYTICAL: C.s3, DECISION: C.s2 }[d.layer] } }; });
  const mx = Math.max(...Object.values(cnt));
  nodes.forEach((n) => { const c = col[n.layer]; n.y += ((mx - cnt[c]) * 30) / 2; });
  chart($('#c-g'), { tooltip: { formatter: (p) => (p.dataType === 'edge' ? '' : `<b>${esc(p.data.dataset_id)}</b><br>${esc(p.data.layer)} · ${esc(p.data.classification)}` +
      (p.data.as_of ? ` · as of ${esc(p.data.as_of)}` : '') + `<br>${esc(String(p.data.transformation || '').slice(0, 140))}`) },
  series: [{ type: 'graph', layout: 'none', roam: true, edgeSymbol: ['none', 'arrow'], edgeSymbolSize: 5,
    label: { show: true, position: 'right', fontSize: 9, formatter: (p) => p.data.dataset_id.replace(/^(raw|analytical|decision|company|market)\./, '') },
    lineStyle: { color: '#c3cad6', width: 0.8, curveness: 0.1 }, emphasis: { focus: 'adjacency' }, data: nodes,
    links: cat.flatMap((d) => (d.derived_from || []).filter((p) => cat.some((x) => x.dataset_id === p)).map((p) => ({ source: p, target: d.dataset_id }))) }] });
  const showM = async () => {
    const L = await api(`/v1/metrics/${$('#m-sel').value}/lineage`);
    const m = L.metric;
    set($('#m-out'), html`<div class="row" style="justify-content:space-between"><b style="font-size:18px">${m.unit === 'USD' ? `$${num(m.value / 1e6, 1)}M` : m.value}</b>${basis(m.basis)}</div>
      <div class="lin-chain" style="margin-top:8px">${L.inputs.map((i) => html`<div class="lin-step"><div class="lk">Source · date</div><div>${i.dataset.system || i.input}
        ${classBadge(i.dataset.classification)} <b>${i.dataset.as_of || ''}</b><div class="mini mono">${i.input}</div></div></div>`)}
      <div class="lin-step"><div class="lk">Transform</div><div class="small">${(m.steps || []).join(' → ') || (L.data_product || {}).transformation}</div></div>
      <div class="lin-step"><div class="lk">Calculation</div><div class="mono small">${m.formula}</div></div>
      <div class="lin-step"><div class="lk">Used by</div><div class="small">${L.used_by.map((r) => html`<div><a href="#/recommendations?r=${encodeURIComponent(r.rec_id)}">${r.short_id} ${r.title}</a></div>`)}${L.used_by.length ? '' : '—'}</div></div></div>
      <button class="btn" id="m-open" style="margin-top:8px">Open full lineage</button>`);
    $('#m-open').onclick = () => metricDrawer(m.metric_id);
  };
  $('#m-sel').onchange = showM; showM();
}

// ================================================================= AUDIT & GOVERNANCE
export async function audit(main) {
  if (!can('verify_audit')) {
    set(main, html`${head('Audit & Governance', '')}<div class="callout warn">The full audit ledger is available to Treasury, CFO, Internal Audit and Administrator roles. Run-level trails are visible inside each run.</div>`);
    return;
  }
  set(main, loading());
  const [rows, v, reg, mc] = await Promise.all([api('/v1/governance/audit?limit=600'), api('/v1/governance/audit/verify'), api('/v1/governance/agents'), api('/v1/governance/model-card')]);
  const denied = rows.filter((r) => /denied|blocked|failed/.test(r.action));
  set(main, html`
    ${head('Audit & Governance', 'Append-only SHA-256 hash-chained ledger: sign-ins, data loads, tool calls, agent outputs, guardrails, submissions, approvals, denials, executions', html`<button class="btn" id="au-verify">Verify chain</button>`)}
    <div class="grid g-4">${kpi('Chain integrity', v.valid ? '✓ Valid' : '✕ Broken', v.valid ? html`head <span class="mono">${(v.head || '').slice(0, 16)}</span>` : `broken at #${v.broken_at_seq}`)}
      ${kpi('Records', String(v.records))}${kpi('Decisions & executions', String(rows.filter((r) => /approve|reject|submitted|prepared/.test(r.action)).length))}
      ${kpi('Denials & blocked attempts', String(denied.length), 'authorization, SoD, release')}</div>
    <div class="tabs" id="gt" style="margin-top:16px">${['Ledger', 'Agent registry', 'Model governance'].map((t, i) => html`<div class="tab ${i ? '' : 'on'}" data-t="${i}">${t}</div>`)}</div>
    <div id="gt-0">${card('Ledger', html`<div class="row"><input class="input" id="au-f" placeholder="Filter by actor, action or run…" style="flex:1">
      <label class="chip"><input type="checkbox" id="au-den"> denials only</label></div><div style="margin-top:10px">
      ${table([{ key: 'seq', label: '#', num: 1 }, { key: 'ts', label: 'Time (UTC)', fmt: (x) => dt(x) }, { key: 'actor', label: 'Actor' },
        { key: 'action', label: 'Action', fmt: (x) => (/denied|failed|blocked|ungrounded/.test(x) ? badge(x, 'crit') : /approve|reject|submitted|prepared/.test(x) ? badge(x, 'warn') : x) },
        { key: 'run_id', label: 'Run' }, { key: 'hash', label: 'Hash', fmt: (x) => html`<span class="mono">${x.slice(0, 14)}</span>` },
        { key: 'prev_hash', label: 'Prev', fmt: (x) => html`<span class="mono muted">${x.slice(0, 10)}</span>` }], rows, { rowAttr: (r, i) => `data-i="${i}" style="cursor:pointer"` })}</div>`)}</div>
    <div id="gt-1" hidden>${card('Agent registry — bounded responsibilities and tools', table([{ key: 'order', label: '#', num: 1 }, { key: 'name', label: 'Agent', fmt: (x) => html`<b>${x}</b>` },
      { key: 'responsibility', label: 'Responsibility' }, { key: 'tools', label: 'MCP tools (allowlist)', fmt: (x) => (x.length ? x.join(', ') : html`<span class="muted">none — evidence only</span>`) },
      { key: 'output_schema', label: 'Output' }, { key: 'validation', label: 'Validation', fmt: (x) => x.join('; ') },
      { key: 'can_move_money', label: 'Moves money', fmt: (x) => (x ? badge('YES', 'crit') : badge('Never', 'good', '✓ ')) }], reg, { maxH: false }))}</div>
    <div id="gt-2" hidden>${card(`Model card — ${mc.model_version}`, html`<dl class="kv"><dt>Algorithm</dt><dd>${mc.algorithm}</dd><dt>Model id</dt><dd class="mono">${mc.model_id}</dd>
      <dt>Training rows</dt><dd>${mc.train_rows}</dd><dt>Validation</dt><dd>${mc.validation_window}</dd>
      <dt>MAE (days late)</dt><dd><b>${mc.mae_days_model_p50}</b> model · ${mc.mae_days_customer_average} customer avg · ${mc.mae_days_erp_due_date_assumption} ERP due date</dd>
      <dt>P10–P90 coverage</dt><dd>${pct(mc.p10_p90_coverage_conformal)} conformal (raw ${pct(mc.p10_p90_coverage_raw)})</dd><dt>Intended use</dt><dd>${mc.intended_use}</dd>
      <dt>Training data</dt><dd>${classBadge(state.ready?.classification)} receivables ledger — the model must be re-trained and re-validated on client history before use</dd></dl>`)}</div>`);
  $$('#gt .tab').forEach((t) => t.onclick = () => { $$('#gt .tab').forEach((x) => x.classList.toggle('on', x === t)); [0, 1, 2].forEach((k) => { $(`#gt-${k}`).hidden = k !== +t.dataset.t; }); });
  $('#au-verify').onclick = async () => { const r = await api('/v1/governance/audit/verify'); toast(r.valid ? `Hash chain valid — ${r.records} records` : `Chain broken at record ${r.broken_at_seq}`, !r.valid); };
  const filt = () => { const t = $('#au-f').value.toLowerCase(), d = $('#au-den').checked;
    $$('#gt-0 tbody tr', main).forEach((tr) => { const s = tr.textContent.toLowerCase(); tr.style.display = s.includes(t) && (!d || /denied|blocked|failed/.test(s)) ? '' : 'none'; }); };
  $('#au-f').oninput = filt; $('#au-den').onchange = filt;
  $$('tr[data-i]', main).forEach((tr) => tr.onclick = () => { const r = rows[+tr.dataset.i];
    const b = drawer(`Audit record #${r.seq}`, ''); b.innerHTML = String(html`<dl class="kv"><dt>Actor</dt><dd>${r.actor}</dd><dt>Action</dt><dd>${r.action}</dd>
      <dt>Run</dt><dd>${r.run_id || '—'}</dd><dt>Time</dt><dd>${r.ts}</dd><dt>Hash</dt><dd class="mono">${r.hash}</dd><dt>Previous</dt><dd class="mono">${r.prev_hash}</dd></dl>
      <h4>Payload</h4><pre class="json">${JSON.stringify(r.payload, null, 2)}</pre>`); });
}

// ================================================================= SYSTEM OPERATIONS
export async function operations(main) {
  if (!can('view_operations')) { set(main, html`${head('System Operations', '')}<div class="callout warn">Operations are visible to Treasury, CFO, Internal Audit and Administrator roles.</div>`); return; }
  set(main, loading());
  const o = await api('/v1/ops/status');
  const sg = (k, v, d, st) => html`<div class="sg"><div class="k">${k}</div><div class="v">${v} ${st ? statusBadge(st) : ''}</div><div class="d">${d}</div></div>`;
  const rq = o.recommendation_quality, fq = o.forecast_quality;
  set(main, html`
    ${head('System Operations', html`Operational control center · replica <span class="mono">${o.platform.replica}</span> · up ${num(o.platform.uptime_s / 60, 0)} min · v${o.platform.version} · ${o.platform.mode} mode`,
      html`<button class="btn" id="op-ref">Refresh</button>`)}
    <div class="statusgrid">
      ${sg('API', `${o.api.window_requests} req`, `p50 ${o.api.p50_ms ?? '—'} ms · p95 ${o.api.p95_ms ?? '—'} ms · ${o.api.errors_5xx} errors · ${o.api.denied_403} denied`, o.api.errors_5xx ? 'DEGRADED' : 'OK')}
      ${sg('Database', o.database.kind, o.database.error || (o.database.kind === 'memory' ? 'in-memory (evaluation only — production requires MongoDB)' : 'ping ok'), o.database.status)}
      ${sg('Data', `${o.data.sources} sources`, `${o.data.stale.length} stale · ${o.data.failed.length} failed · market ${o.data.market_mode}`, o.data.failed.length ? 'DEGRADED' : 'OK')}
      ${sg('Audit chain', `${o.audit.records} records`, `head ${(o.audit.head || '').slice(0, 12)}`, o.audit.valid ? 'OK' : 'DEGRADED')}
      ${sg('Forecast quality', pct(fq.wape_model), `WAPE vs ${pct(fq.wape_erp)} ERP · coverage ${pct(fq.coverage, 0)} · t0 ${fq.t0}`, fq.wape_model <= 0.15 ? 'OK' : 'DEGRADED')}
      ${sg('Recommendation quality', `${rq.approved}/${rq.decided_actions} approved`, `${rq.rejected} rejected · briefings grounded ${rq.briefings_grounded}/${rq.briefings}`)}
      ${sg('Model', o.model.model_id ? o.model.model_id.split(':').slice(-1)[0] : '—', `MAE ${o.model.mae_days_model_p50} d vs ${o.model.mae_days_customer_average} baseline · coverage ${pct(o.model.p10_p90_coverage_conformal, 0)}`, 'OK')}
      ${sg('RAG', `${o.rag.chunks} clauses`, `${o.rag.quarantined} quarantined · ${o.rag.embedder} · ${o.rag.vector_backend}`, 'OK')}
    </div>
    <div class="callout ${o.llm.provider === 'mock' ? 'warn' : ''} small" style="margin-top:12px"><b>AI provider:</b> ${o.llm.provider} · ${o.llm.model}.
      ${o.llm.provider === 'mock' ? 'No LLM is configured in this deployment; agents use deterministic reasoners. A live Claude call has NOT been verified here — run `meridian llm-check` after configuring credentials.' : ''}</div>
    <div class="grid g-2" style="margin-top:16px">
      ${card('Agents (from recorded runs)', table([{ key: 'agent', label: 'Agent' }, { key: 'runs', label: 'Steps', num: 1 }, { key: 'failed', label: 'Failed', num: 1 },
        { key: 'p50_ms', label: 'p50 ms', num: 1, fmt: (v) => num(v, 1) }, { key: 'max_ms', label: 'max ms', num: 1, fmt: (v) => num(v, 1) },
        { key: 'grounded_rate', label: 'Grounded', num: 1, fmt: (v) => (v === null ? '—' : pct(v, 0)) }, { key: 'status', label: 'Status', fmt: (v) => statusBadge(v) }], o.agents, { maxH: false, empty: 'No runs yet' }))}
      ${card('Jobs', table([{ key: 'at', label: 'When', fmt: (v) => dt(v) }, { key: 'kind', label: 'Job' }, { key: 'status', label: 'Status', fmt: (v) => statusBadge(v) },
        { key: 'duration_ms', label: 'ms', num: 1, fmt: (v) => num(v, 0) }, { key: 'detail', label: 'Detail', fmt: (v) => html`<span class="mini">${v}</span>` }], o.jobs, { empty: 'No jobs yet' }))}
    </div>
    <div class="grid g-2" style="margin-top:16px">
      ${card('API latency by route (rolling window)', html`<div class="chart" id="op-lat"></div>${table([{ key: 'method', label: '' }, { key: 'route', label: 'Route' },
        { key: 'requests', label: 'Req', num: 1 }, { key: 'p50_ms', label: 'p50', num: 1 }, { key: 'p95_ms', label: 'p95', num: 1 },
        { key: 'client_4xx', label: '4xx', num: 1 }, { key: 'errors_5xx', label: '5xx', num: 1 }], o.routes.filter((x) => !x.route.startsWith('/console')))}`)}
      ${card(`Production readiness — ${o.readiness.ready ? 'checks passing for this mode' : 'NOT READY'}`, html`
        ${o.platform.mode === 'demo' ? html`<div class="callout warn small" style="margin-bottom:8px">Evaluation mode runs evaluation checks only. The production gate
          (MongoDB, real Claude endpoint, signing key, CLIENT_DATA, validated sources) is enforced at start-up when <span class="mono">MERIDIAN_MODE=production</span>; preview with <span class="mono">meridian doctor</span>.</div>` : ''}
        ${table([{ key: 'status', label: '', fmt: (v) => statusBadge(v) }, { key: 'check', label: 'Check' }, { key: 'detail', label: 'Detail' }], o.readiness.checks, { maxH: false })}`)}
    </div>`);
  $('#op-ref').onclick = () => operations(main);
  const r = o.routes.filter((x) => x.route.startsWith('/v1')).slice(0, 10).reverse();
  chart($('#op-lat'), { grid: { left: 210, right: 30, top: 30, bottom: 24 }, legend: { top: 0 }, tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
    xAxis: { type: 'value', name: 'ms' }, yAxis: { type: 'category', data: r.map((x) => `${x.method} ${x.route}`), axisLabel: { width: 200, overflow: 'truncate', fontSize: 10 } },
    series: [{ name: 'p50', type: 'bar', data: r.map((x) => x.p50_ms), itemStyle: { color: C.s1 } }, { name: 'p95', type: 'bar', data: r.map((x) => x.p95_ms), itemStyle: { color: C.s4 } }] });
}

// ================================================================= ADMINISTRATION
export async function admin(main) {
  set(main, loading());
  const [roles, cons] = await Promise.all([api('/v1/admin/roles'), api('/v1/connectors')]);
  const users = can('admin') ? await api('/v1/admin/users') : null;
  set(main, html`
    ${head('Administration', 'Users, roles and permissions, approval authority, connectors and environment. Administrators manage operations but can never approve or release money.')}
    ${users ? card('Users', html`${table([{ key: 'username', label: 'User' }, { key: 'display_name', label: 'Name' }, { key: 'role', label: 'Role', fmt: (v) => roleName(v) },
      { key: 'active', label: 'Status', fmt: (v, r) => (v === false ? badge('disabled', 'crit') : (r.failed_logins || 0) >= 5 ? badge('locked', 'crit') : badge('active', 'good', '✓ ')) },
      { key: 'last_login', label: 'Last sign-in', fmt: (v) => dt(v) }, { key: 'demo_account', label: '', fmt: (v) => (v ? badge('evaluation account', 'synth') : '') },
      { key: 'username', label: 'Actions', fmt: (v, r) => html`<button class="btn" data-act="toggle" data-u="${v}">${r.active === false ? 'Enable' : 'Disable'}</button>
        <button class="btn" data-act="unlock" data-u="${v}">Unlock</button>` }], users, { maxH: false })}
      <h4>Create user</h4><div class="formrow"><input class="input" id="nu-u" placeholder="username"><input class="input" id="nu-n" placeholder="Full name">
        <select class="input" id="nu-r">${roles.roles.map((r) => html`<option value="${r.role}">${roleName(r.role)}</option>`)}</select>
        <input class="input" id="nu-p" type="password" placeholder="password (12+ chars)"><button class="btn primary" id="nu-go">Create</button></div>
      <div class="mini">Passwords: PBKDF2-SHA256 600k iterations · lockout after 5 failures · every change audited. Production adds SSO (OIDC) — not configured.</div>`)
    : html`<div class="callout warn small">User management requires the Finance Administrator role.</div>`}
    <div class="grid g-2" style="margin-top:16px">
      ${card('Roles and permissions (enforced server-side)', table([{ key: 'role', label: 'Role', fmt: (v) => html`<b>${roleName(v)}</b>` }, { key: 'description', label: 'Description' },
        { key: 'permissions', label: 'Permissions', fmt: (v) => v.join(', ') }, { key: 'approval_rank', label: 'Approval rank', num: 1 },
        { key: 'scope', label: 'Scope', fmt: (v) => (v ? v.join(', ') : '') }], roles.roles, { maxH: false }))}
      ${card('Connector configuration', table([{ key: 'name', label: 'Connector' }, { key: 'status', label: 'Status', fmt: (v) => statusBadge(v) },
        { key: 'implementation', label: 'Implementation', fmt: (v) => html`<span class="mono small">${v || '—'}</span>` },
        { key: 'datasets', label: 'Data carried', fmt: (v) => ((v || []).length ? [...new Set(v.map((d) => d.classification))].map((c) => classBadge(c))
          : html`<span class="muted">none</span>`) }], cons, { maxH: false }),
        { hint: html`environment ${state.ready?.mode} · store ${state.ready?.store} · policy ${state.ready?.policy_version}` })}
    </div>`);
  $$('[data-act]', main).forEach((b) => b.onclick = async () => {
    const u = users.find((x) => x.username === b.dataset.u);
    const body = b.dataset.act === 'unlock' ? { unlock: true } : { active: u.active === false };
    try { await api(`/v1/admin/users/${u.username}`, { method: 'PATCH', body }); toast('Saved — audited'); admin(main); } catch (e) { toast(e.message, true); }
  });
  $('#nu-go')?.addEventListener('click', async () => {
    const ok = await confirmModal('Create user?', `<p>${esc($('#nu-u').value)} as ${esc(roleName($('#nu-r').value))}</p>`, { okText: 'Create' });
    if (!ok) return;
    try { await api('/v1/admin/users', { method: 'POST', body: { username: $('#nu-u').value, display_name: $('#nu-n').value, role: $('#nu-r').value, password: $('#nu-p').value } });
      toast('User created — audited'); admin(main); } catch (e) { toast(e.message, true); }
  });
}
