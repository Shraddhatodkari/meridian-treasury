// AI Decision Center · Recommendations · Approval Center
import {
  $, $$, C, answerView, api, badge, basis, card, chart, classBadge, confBadge, confirmModal, drawer, dt, esc,
  evidenceChain, fanChart, full, html, impactTable, invalidate, loading, metricDrawer, num, pct, prio, state, statusBadge,
  stepper, table, title, toast, usd,
} from './core.js';
import { can, head, set, weeksOf } from './p_common.js';
import { params, refreshApprovalCount } from './app.js';

// ================================================================= AI DECISION CENTER
const SUGGEST = [
  'Why did liquidity deteriorate?', 'Which entities have excess cash?', 'What is causing the DSO increase?',
  'What is our 13-week liquidity risk?', 'What happens if collections slow by 20%?', 'Can we repay the revolver?',
  'Which covenants are closest to breach?', 'Which debt facilities create refinancing risk?', 'What is our interest-rate and FX exposure?',
  'Which actions require CFO approval?', 'Who approves a revolver prepayment?', 'Is the AI allowed to execute payments?',
];
export async function copilot(main) {
  const runs = await api('/v1/runs');
  set(main, html`
    ${head('AI Decision Center', 'Questions are planned, routed to bounded agents over read-only MCP tools, grounded in application data and checked against policy. Every answer shows its evidence.')}
    <div class="card"><div class="card-b" style="padding-top:16px">
      <div class="row"><input class="input" id="q" maxlength="500" style="flex:1;min-width:280px" placeholder="Ask about liquidity, working capital, funding, risk or policy — or leave empty for the full analysis">
        <button class="btn primary" id="ask" ${can('run_agents') ? '' : 'disabled title="Your role cannot run agents"'}>Ask</button></div>
      <div class="chips" style="margin-top:12px">${SUGGEST.map((s) => html`<button class="chip" data-q="${s}">${s}</button>`)}
        <button class="chip" id="ev-chip">◆ Show me the evidence behind a recommendation</button></div>
    </div></div>
    <div class="grid g-3" style="margin-top:16px"><div class="span-2" id="ans"><div class="card"><div class="empty">Ask a question, or open a previous run.</div></div></div>
      ${card('Recent questions & runs', table([{ key: 'created_at', label: 'When', fmt: (v) => html`<span class="nowrap">${dt(v).slice(5)}</span>` },
        { key: 'question', label: 'Request', fmt: (v) => v || 'Full liquidity analysis' }, { key: 'requested_by', label: 'By' },
        { key: 'status', label: 'Status', fmt: (v) => statusBadge(v) }], runs, { rowAttr: (r) => `data-run="${esc(r.run_id)}" style="cursor:pointer"`, empty: 'No runs yet' }))}</div>`);
  const ask = async (question) => {
    $('#ask').disabled = true;
    set($('#ans'), html`<div class="card">${loading()}<div class="mini" style="text-align:center">planner → data intelligence → … → governance → briefing → guardrail</div></div>`);
    try {
      const r = await api('/v1/runs', { method: 'POST', body: { question: question || null } });
      invalidate('');
      await showRun(r.run_id);
    } catch (e) { toast(e.message, true); } finally { $('#ask').disabled = !can('run_agents'); }
  };
  $$('.chip[data-q]', main).forEach((c) => c.onclick = () => { $('#q').value = c.dataset.q; if (can('run_agents')) ask(c.dataset.q); });
  $('#q').onkeydown = (e) => { if (e.key === 'Enter') ask($('#q').value.trim()); };
  $('#ask').onclick = () => ask($('#q').value.trim());
  $('#ev-chip').onclick = () => { location.hash = '#/recommendations?lineage=1'; };
  $$('tr[data-run]', main).forEach((tr) => tr.onclick = () => showRun(tr.dataset.run));
  if (runs.length) showRun(runs[0].run_id);
}

async function showRun(runId) {
  const el = $('#ans'); if (!el) return;
  const r = await api(`/v1/runs/${encodeURIComponent(runId)}`);
  const rep = r.reproducibility || {};
  const trace = html`<div class="timeline">${(r.trace || []).map((t) => html`<div class="tl-item ${['guardrail', 'governance_agent'].includes(t.actor) ? 'guard' : ''}">
    <b>${t.actor}</b> · ${t.action} <span class="muted small">${num(t.latency_ms, 0)} ms</span></div>`)}</div>`;
  let body;
  if (r.intent === 'INSIGHT' && r.insight) body = answerView(r.insight);
  else if (r.intent === 'POLICY_QUESTION' && r.answer) {
    body = html`<div class="answer"><div class="concl">${r.question}</div>
      <ul>${r.answer.key_points.map((p) => html`<li>${p}</li>`)}</ul>
      <div class="sec"><h5>Citations (retrieved, non-quarantined)</h5>${table([{ key: 'clause_id', label: 'Clause' }, { key: 'document', label: 'Document' },
        { key: 'rank', label: 'Rank', num: 1 }], r.answer.citations || [], { maxH: false })}</div>
      <div class="mini">Retrieval is hybrid (vector + BM25, RRF). Legal interpretation remains with Treasury / Legal.</div></div>`;
  } else {
    const b = r.briefing || {};
    body = html`<div class="answer"><div class="row">${r.grounding?.grounded ? badge(`All ${r.grounding.claims} $ figures grounded in tool output`, 'good', '✓ ') :
      badge('Fallback briefing used', 'warn')} ${badge(`AI: ${b.llm_provider || r.llm_provider}`, 'muted')} ${r.failed_agents?.length ? badge(`DEGRADED: ${r.failed_agents.join(', ')}`, 'crit') : ''}</div>
      <div class="concl">${b.executive_summary}</div>
      <div class="grid g-2"><div class="sec"><h5>Decisions required</h5><ul>${(b.decisions_required || []).map((d) => html`<li><b>${d}</b></li>`)}</ul>
        <h5>Key numbers</h5><ul>${(b.key_numbers || []).map((d) => html`<li>${d}</li>`)}</ul></div>
        <div class="sec"><h5>Risks</h5><ul>${(b.risks || []).map((d) => html`<li>${d}</li>`)}</ul><h5>Policy</h5>${(b.citations || []).map((c) => badge(c, 'info'))}</div></div>
      ${r.forecast ? html`<div class="chart" id="run-fan"></div>` : ''}
      ${r.recommendations?.length ? html`<div class="sec"><h5>${r.recommendations.length} recommendations generated</h5>${r.recommendations.map((x) =>
        html`<div class="row small" style="margin-bottom:4px">${prio(x.priority)} <a href="#/recommendations?r=${encodeURIComponent(x.rec_id)}">${x.title}</a> ${statusBadge(x.status)}</div>`)}</div>` : ''}</div>`;
  }
  const fnd = (r.findings || []).map((f) => html`<tr><td><b>${f.agent}</b></td><td>${f.headline}</td><td>${confBadge(f.confidence)}</td>
    <td>${f.validation?.numeric_grounding ? badge(`${f.validation.figures_checked} grounded`, 'good', '✓ ') : f.status === 'FAILED' ? badge('FAILED', 'crit') : badge('check', 'warn')}</td>
    <td class="mini">${Object.entries((f.provenance?.tool_calls || []).reduce((a, c) => ({ ...a, [c.tool]: (a[c.tool] || 0) + 1 }), {})).map(([t, n]) => (n > 1 ? `${t} ×${n}` : t)).join(', ')}</td></tr>`);
  set(el, html`${card(r.question || 'Full liquidity analysis', body, { hint: `${r.run_id} · ${r.intent} · by ${r.requested_by}`, right: statusBadge(r.status) })}
    <div style="margin-top:16px">${card('Agent findings (structured outputs, validated)', html`<div class="table-wrap"><table class="t"><thead><tr><th>Agent</th><th>Finding</th><th>Confidence</th><th>Validation</th><th>MCP tools</th></tr></thead><tbody>${fnd}</tbody></table></div>`)}</div>
    <div class="grid g-2" style="margin-top:16px">${card('Agent trace', trace)}${card('Reproducibility', html`<dl class="kv">
      <dt>Mode / data</dt><dd>${rep.mode} · ${classBadge(rep.classification)}</dd><dt>Policy</dt><dd>${rep.policy_version} <span class="mono">${rep.policy_sha256}</span></dd>
      <dt>Model</dt><dd class="mono">${rep.model_id}</dd><dt>LLM</dt><dd>${rep.llm?.provider} · ${rep.llm?.model}</dd>
      <dt>Seed / paths</dt><dd>${rep.seed} / ${rep.monte_carlo_paths}</dd><dt>Market data</dt><dd>${rep.market_data}</dd>
      <dt>Run audit</dt><dd class="mono">${r.completion_audit_hash || ''}</dd></dl>`)}</div>`);
  if (r.forecast) fanChart($('#run-fan'), weeksOf(r.forecast), r.forecast.liquidity_usd, { covenant: r.forecast.covenant_min_liquidity_usd, name: r.forecast.scenario.slice(0, 40) });
}

// ================================================================= RECOMMENDATIONS
export async function recommendations(main) {
  const p = params();
  set(main, loading());
  const scope = p.get('scope') || 'latest';
  const recs = await api(`/v1/recommendations?scope=${scope}&case=${state.case || 'all'}${state.case === 'public' ? `&cik=${state.cik}` : ''}`);
  const cats = [...new Set(recs.map((r) => r.category))];
  set(main, html`
    ${head('Recommendations', html`Decision workspace — each recommendation states decision, objective, constraints, quantified impact (with basis),
      timing, risk, confidence, owner, evidence, policy and the approval it needs. Recommendations are not transactions.`,
      html`<select class="input" id="r-scope" style="width:200px"><option value="latest" ${scope === 'latest' ? 'selected' : ''}>Latest analysis</option>
        <option value="all" ${scope === 'all' ? 'selected' : ''}>All runs (incl. scenarios)</option></select>`)}
    ${recs.length ? '' : html`<div class="callout warn">No governed analysis has been run for this case yet — run it from the Executive Command Center or the AI Decision Center. Meridian does not create recommendations without evidence.</div>`}
    <div class="row small" style="margin-bottom:10px">${cats.map((c) => html`<label class="chip"><input type="checkbox" class="r-cat" value="${c}" checked> ${title(c)}</label>`)}</div>
    <div class="grid g-3"><div id="r-list">${recs.map((r) => {
      const m = r.impact.find((i) => i.value_usd) || r.impact[0];
      return html`<div class="rec" data-rec="${r.rec_id}" data-cat="${r.category}" style="cursor:pointer"><div class="rh">${prio(r.priority)}<span class="rt">${r.title}</span></div>
        <div class="meta"><span>${statusBadge(r.status)}</span><span><b>${m ? usd(m.value_usd) : ''}</b> ${m ? basis(m.basis) : ''}</span>
          <span>Approval <b>${r.approval_required ? r.approval_required.toUpperCase() : 'none'}</b></span>${r.stale ? badge('older run', 'muted') : ''}</div></div>`;
    })}</div><div class="span-2" id="r-detail">${card('Select a recommendation', html`<div class="empty">Choose a recommendation to see its evidence, data, policy and approval path.</div>`)}</div></div>`);
  $('#r-scope').onchange = (e) => { location.hash = `#/recommendations?scope=${e.target.value}`; };
  $$('.r-cat', main).forEach((c) => c.onchange = () => { const on = new Set($$('.r-cat:checked', main).map((x) => x.value));
    $$('#r-list .rec', main).forEach((el) => { el.style.display = on.has(el.dataset.cat) ? '' : 'none'; }); });
  $$('#r-list .rec', main).forEach((el) => el.onclick = () => { $$('#r-list .rec').forEach((x) => x.classList.remove('sel')); el.classList.add('sel'); showRec(recs.find((r) => r.rec_id === el.dataset.rec)); });
  const want = p.get('r') ? recs.find((r) => r.rec_id === p.get('r')) : recs[0];
  if (want) { $(`#r-list .rec[data-rec="${CSS.escape(want.rec_id)}"]`)?.classList.add('sel'); showRec(want, p.get('lineage') === '1'); }
}

async function showRec(r, openLineage = false) {
  const el = $('#r-detail');
  const canSubmit = can('submit_recommendations') && r.actions.some((a) => a.status === 'PROPOSED');
  set(el, card(html`${prio(r.priority)} ${r.title}`, html`
    <div class="row">${statusBadge(r.status)} ${badge(title(r.category), 'info')} ${classBadge(r.governance?.data_classification || state.ready?.classification)}
      ${confBadge(r.confidence_label)} <span class="mini">confidence ${num(r.confidence, 2)} · scenario: ${r.scenario}</span></div>
    <div class="callout small" style="margin-top:10px"><b>Decision:</b> ${r.decision}<br><b>Objective:</b> ${r.objective}</div>
    <div class="tabs" id="rt">${['Impact', 'Evidence', 'Data lineage', 'Policy', 'Approval'].map((t, i) => html`<div class="tab ${i ? '' : 'on'}" data-t="${i}">${t}</div>`)}</div>
    <div id="rt-0">${impactTable(r.impact)}
      ${(r.data_limitations || []).length ? html`<div class="callout small" style="margin-top:10px"><b>Data limitations (PC-6.2):</b><ul style="margin:4px 0 0;padding-left:18px">
        ${r.data_limitations.map((x) => html`<li>${x}</li>`)}</ul></div>` : ''}
      <div class="grid g-2" style="margin-top:12px"><div><h4>Constraints</h4><ul>${r.constraints.map((c) => html`<li>${c}</li>`)}</ul></div>
        <div><h4>Risks</h4><ul>${r.risks.map((c) => html`<li>${c}</li>`)}</ul><h4>Timing</h4><div>${r.timing}</div>
        <h4>Owner</h4><div>${r.owner}</div></div></div></div>
    <div id="rt-1" hidden>${table([{ key: 'type', label: 'Type' }, { key: 'id', label: 'Reference', fmt: (v, x) => (x.type === 'metric' ? html`<a class="chip" data-m="${v}">${v} ›</a>` : html`<span class="mono">${v}</span>`) }], r.evidence, { maxH: false })}
      <div class="mini">Metrics open their Source → Dataset → Date → Transformation → Calculation lineage. Tool results are SHA-256 hashed in the audit ledger.</div></div>
    <div id="rt-2" hidden><div class="chart xl" id="lin-g"></div><div class="mini">Left → right: RAW → VALIDATED → NORMALIZED → ANALYTICAL datasets → metrics, tool calls and agent findings → policy → recommendation → actions → decisions → audit records. Hover for details.</div></div>
    <div id="rt-3" hidden>${table([{ key: 'clause_id', label: 'Clause' }, { key: 'document', label: 'Document' }, { key: 'doc_version', label: 'Version' },
      { key: 'relevance', label: 'Relevance', num: 1, fmt: (v) => num(v, 2) }, { key: 'retrieved_at', label: 'Retrieved', fmt: (v) => dt(v) }], r.policy_citations || [], { maxH: false, empty: 'Cited from policy-as-code' })}
      <div class="small" style="margin-top:6px">Policy refs: ${r.policy_refs.map((x) => badge(x, 'info'))} · policy-as-code ${r.policy_version} ·
        citation check ${r.policy_verification?.verified ? badge('verified', 'good', '✓ ') : badge(`unsupported ${r.policy_verification?.unsupported}`, 'crit')}</div></div>
    <div id="rt-4" hidden><div class="small">Required approver: <b>${r.approval_required ? r.approval_required.toUpperCase() : 'none — advisory'}</b> ·
      ${r.payment_related ? 'payment-related: DRAFT → IN APPROVAL → APPROVED → READY; transmission needs dual control and a bank channel' : 'non-payment decision record'}</div>
      ${table([{ key: 'action_id', label: 'Action' }, { key: 'type', label: 'Type', fmt: (v) => title(v) }, { key: 'amount_usd', label: 'Amount', num: 1, fmt: (v) => usd(v) },
        { key: 'required_approver', label: 'Needs' }, { key: 'status', label: 'Status', fmt: (v) => statusBadge(v) },
        { key: 'payment_state', label: 'Payment file', fmt: (v) => (v ? statusBadge(v) : '') }], r.actions, { maxH: false, empty: 'Advisory — no actions' })}
      ${(r.history || []).map((h) => html`<div class="mini">${h.event} by ${h.user} (${h.role}) ${dt(h.at)} · audit ${h.audit_hash}</div>`)}</div>
    <div class="row" style="margin-top:14px">
      ${canSubmit ? html`<button class="btn primary" id="r-submit">Submit for approval</button>` : ''}
      ${r.actions.length ? html`<a class="btn" href="#/approvals?rec=${encodeURIComponent(r.rec_id)}">Open in Approval Center →</a>` : ''}
      <span class="mini">${r.nature} · run ${r.run_id} by ${r.requested_by}</span></div>`,
  { hint: r.short_id }));
  const tabs = (i) => { $$('#rt .tab').forEach((t) => t.classList.toggle('on', +t.dataset.t === i)); [0, 1, 2, 3, 4].forEach((k) => { $(`#rt-${k}`).hidden = k !== i; });
    if (i === 2) drawLineage(r.rec_id); };
  $$('#rt .tab').forEach((t) => t.onclick = () => tabs(+t.dataset.t));
  $$('[data-m]', el).forEach((a) => a.onclick = () => metricDrawer(a.dataset.m));
  $('#r-submit')?.addEventListener('click', async () => {
    const res = await confirmModal(`Submit ${r.short_id} for approval?`, String(html`<p>${r.actions.filter((a) => a.status === 'PROPOSED').length} action(s) will be
      routed to <b>${r.approval_required}</b>. As submitter you cannot approve them (TP-2.2).</p>`), { okText: 'Submit' });
    if (!res) return;
    try { await api(`/v1/recommendations/${encodeURIComponent(r.rec_id)}/submit`, { method: 'POST', body: { comment: res.comment } });
      toast(`${r.short_id} submitted — recorded in the audit trail`); refreshApprovalCount(); recommendations($('#main'));
    } catch (e) { toast(e.message, true); }
  });
  if (openLineage) tabs(2);
}

const COL = { Source: 0, Filing: 1, Fact: 2, MarketData: 2, RAW: 0, VALIDATED: 1, NORMALIZED: 2, ANALYTICAL: 3, DECISION: 3, Metric: 4, ToolCall: 4, AgentFinding: 4, PolicyClause: 5,
  Recommendation: 6, Action: 7, Decision: 8, AuditRecord: 9 };
const KC = { Source: '#101828', Filing: '#98a2b3', Fact: C.s1, MarketData: C.s3, Dataset: C.s1, Metric: C.s3, ToolCall: '#7a8699', AgentFinding: C.s5, PolicyClause: C.s4, Recommendation: C.s2, Action: C.ink, Decision: C.s6, AuditRecord: '#475467' };
async function drawLineage(recId) {
  const L = await api(`/v1/lineage/recommendations/${encodeURIComponent(recId)}`);
  const cols = {};
  const nodes = L.nodes.map((n) => {
    const c = n.kind === 'Dataset' ? (COL[n.layer] ?? 3) : (COL[n.kind] ?? 4);
    cols[c] = (cols[c] || 0) + 1;
    return { ...n, name: n.id, x: c * 140, y: cols[c] * 46, symbolSize: n.kind === 'Recommendation' ? 26 : 13,
      itemStyle: { color: KC[n.kind] || '#98a2b3' } };
  });
  const maxRows = Math.max(...Object.values(cols));
  nodes.forEach((n) => { const c = Math.round(n.x / 140); n.y += ((maxRows - cols[c]) * 46) / 2; });
  window.echarts.getInstanceByDom($('#lin-g'))?.dispose();
  chart($('#lin-g'), { tooltip: { formatter: (p) => (p.dataType === 'edge' ? esc(p.data.rel) : `<b>${esc(p.data.label)}</b><br>${esc(p.data.kind)}${p.data.layer ? ` · ${esc(p.data.layer)}` : ''}` +
      (p.data.classification ? `<br>${esc(p.data.classification)}` : '') + (p.data.as_of ? ` · as of ${esc(p.data.as_of)}` : '') +
      (p.data.formula ? `<br><code>${esc(p.data.formula)}</code>` : '') + (p.data.transformation ? `<br>${esc(String(p.data.transformation).slice(0, 120))}` : '') +
      (p.data.accession ? `<br>accession ${esc(p.data.accession)} filed ${esc(p.data.filed)}` : '') + (p.data.value !== undefined && p.data.kind === 'Fact' ? `<br>value ${esc(usd(p.data.value))}` : '') + (p.data.version ? `<br>version ${esc(p.data.version)}` : '') + (p.data.hash ? `<br>hash ${esc(p.data.hash)}` : '')) },
  series: [{ type: 'graph', layout: 'none', roam: true, edgeSymbol: ['none', 'arrow'], edgeSymbolSize: 6,
    label: { show: true, position: 'bottom', fontSize: 9, formatter: (p) => String(p.data.label).slice(0, 26) },
    lineStyle: { color: '#c3cad6', width: 1, curveness: 0.12 }, emphasis: { focus: 'adjacency' },
    data: nodes, links: L.edges.map((e) => ({ source: e.from, target: e.to, rel: e.rel })) }] });
}

// ================================================================= APPROVAL CENTER
const PAY_STATES = ['DRAFT', 'IN_APPROVAL', 'APPROVED', 'READY', 'TRANSMITTED'];
const MANDATES = new Set(['REFINANCING_PLAN', 'BANK_REBALANCING_PLAN', 'REFINANCING_READINESS_PLAN', 'LIQUIDITY_POLICY_REVIEW',
  'WORKING_CAPITAL_PROGRAM', 'CAPITAL_ALLOCATION_REVIEW', 'RATE_RISK_REVIEW']);
export async function approvals(main) {
  set(main, loading());
  const cs = state.case || 'all';
  const [q00, runs0] = await Promise.all([api('/v1/approvals/queue'), api('/v1/runs')]);
  const mine = (x) => cs === 'all' || (x.case === cs && (cs !== 'public' || x.cik === state.cik));
  const q0 = q00.filter(mine);
  const runs = runs0.filter(mine);
  const rank = { cfo: 0, treasurer: 1, wc_manager: 2, analyst: 3 };
  const queue = [...q0].sort((a, b) => (b.status === 'SUBMITTED') - (a.status === 'SUBMITTED') || (rank[a.required_approver] ?? 4) - (rank[b.required_approver] ?? 4) || b.amount_usd - a.amount_usd);
  const decided = [];
  for (const r of runs.slice(0, 8)) {
    if (!r.actions) continue;
    const full2 = await api(`/v1/runs/${encodeURIComponent(r.run_id)}`);
    for (const a of full2.actions) if (!['PROPOSED', 'SUBMITTED'].includes(a.status)) decided.push({ ...a, run_id: r.run_id });
  }
  set(main, html`
    ${head('Approval Center', html`Detect → Analyze → Recommend → <b>Review → Approve → Prepare execution → Execute</b>. Authority per approval matrix (TP-2.1),
      segregation of duties (TP-2.2), dual-control release (TP-7.2). Every decision and denial is hash-chained.`)}
    <div class="grid g-3">
      <div>${card(`Awaiting decision (${queue.length})`, queue.length ? html`<div class="queue scroll-y">${queue.map((x) => html`
        <div class="q-item" data-k="${x.run_id}|${x.action_id}"><div class="row" style="justify-content:space-between"><b>${x.action_id} · ${title(x.type)}</b>
          <span class="tabular strong">${usd(x.amount_usd)}</span></div>
          <div class="row small" style="margin-top:4px">${statusBadge(x.status)} ${badge(`needs ${x.required_approver}`, x.required_approver === 'cfo' ? 'serious' : 'info')}
            ${x.can_approve ? badge('you can decide', 'good', '✓ ') : badge('view only', 'muted')}${x.stale ? badge('older run', 'muted') : ''}</div>
          <div class="mini" style="margin-top:3px">${x.rec_short_id ? `${x.rec_short_id} ${x.rec_title}` : ''}</div></div>`)}</div>`
        : html`<div class="empty">Nothing awaiting decision</div>`, { hint: 'submitted first, then CFO-level' })}</div>
      <div class="span-2" id="ap-detail">${card('Select an action', html`<div class="empty">Choose an action to review its evidence chain.</div>`)}</div>
    </div>
    <div style="margin-top:16px">${card('Decided and in execution', table([
      { key: 'action_id', label: 'ID' }, { key: 'type', label: 'Action', fmt: (v) => title(v) }, { key: 'amount_usd', label: 'Amount', num: 1, fmt: (v) => usd(v) },
      { key: 'status', label: 'Status', fmt: (v) => statusBadge(v) }, { key: 'payment_state', label: 'Payment file', fmt: (v) => (v ? statusBadge(v) : '') },
      { key: 'approved_by', label: 'Decided by', fmt: (v, r) => { const s = v || r.rejected_by; return s ? `${s.user} (${s.role}) · ${dt(s.at)}` : ''; } },
      { key: 'prepared_by', label: 'Prepared by', fmt: (v) => (v ? `${v.user} · ${dt(v.at)}` : '') },
      { key: 'executed_by', label: 'Issued by (dual control)', fmt: (v) => (v ? `${v.user} · ${dt(v.at)}` : '') }],
      decided, { rowAttr: (r) => `data-k="${esc(r.run_id)}|${esc(r.action_id)}" style="cursor:pointer"`, empty: 'No decisions yet' }))}</div>`);
  $$('[data-k]', main).forEach((tr) => tr.onclick = () => { $$('.sel', main).forEach((x) => x.classList.remove('sel')); tr.classList.add('sel');
    const [rid, aid] = tr.dataset.k.split('|'); showAction(rid, aid, queue); });
  const want = params().get('rec');
  const first = want ? $$('.q-item', main).find((el) => queue.find((x) => `${x.run_id}|${x.action_id}` === el.dataset.k && x.rec_id === want)) : $('.q-item', main);
  first?.click();
}

async function showAction(runId, actionId, queue) {
  const el = $('#ap-detail');
  set(el, html`<div class="card">${loading()}</div>`);
  const run = await api(`/v1/runs/${encodeURIComponent(runId)}`);
  const a = run.actions.find((x) => x.action_id === actionId);
  const qq = queue.find((x) => x.run_id === runId && x.action_id === actionId);
  const art = a.execution_artifact;
  const pending = ['PROPOSED', 'SUBMITTED'].includes(a.status);
  const blocked = pending && qq && !qq.can_approve ? qq.blocked_reason : null;
  const payment = ['INTERCOMPANY_TRANSFER', 'REVOLVER_REPAY', 'REVOLVER_DRAW', 'EARLY_PAY_DISCOUNT'].includes(a.type);
  const pstate = a.payment_state || (a.status === 'REJECTED' ? 'DRAFT' : pending ? (a.status === 'SUBMITTED' ? 'IN_APPROVAL' : 'DRAFT') : 'APPROVED');
  set(el, card(`${a.action_id} · ${title(a.type)}`, html`
    <div class="row"><span class="strong" style="font-size:20px">${full(a.amount_usd)}</span>${statusBadge(a.status)}
      ${a.from_entity ? badge(`${a.from_entity}${a.to_entity ? ` → ${a.to_entity}` : ''}`, 'info') : ''} ${badge(`requires ${a.required_approver}`, 'muted')}</div>
    <p>${a.rationale}</p>
    ${blocked ? html`<div class="callout crit small"><b>You cannot decide this action:</b> ${blocked}. The server enforces this check and audits every denied attempt.</div>` : ''}
    <h4>Decision workflow</h4>${stepper(['DETECT', 'ANALYZE', 'RECOMMEND', 'REVIEW', 'APPROVE', 'PREPARE_EXECUTION', 'EXECUTE'],
      a.status === 'PROPOSED' ? 'REVIEW' : a.status === 'SUBMITTED' ? 'APPROVE' : a.status === 'APPROVED' ? 'PREPARE_EXECUTION' : a.status === 'READY' ? 'EXECUTE' : a.status === 'REJECTED' ? 'APPROVE' : 'EXECUTE',
      a.status === 'REJECTED' || (a.status === 'READY' && payment))}
    <div class="mini">${a.status === 'REJECTED' ? 'Rejected at approval — stops here.' : a.status === 'READY' && payment ? 'Execution blocked by design: dual-control release and a configured bank channel are required (TP-7.2).'
      : a.status === 'READY' ? 'Mandate validated. Issue it to the owner: requires a second authorised person (dual control). No money moves.'
      : a.status === 'EXECUTED' ? `Decision mandate issued under dual control by ${a.executed_by?.user} — no payment, issuance or trade executed by Meridian.` : ''}</div>
    ${payment ? html`<h4>Payment file lifecycle</h4>${stepper(PAY_STATES, a.status === 'REJECTED' ? 'DRAFT' : pstate, a.status === 'REJECTED')}
      <div class="mini">${a.status === 'REJECTED' ? 'Rejected — no payment file will be produced.' : 'READY is the last state Meridian can reach on its own: transmission requires a second authorised signatory AND a configured bank channel (none in this deployment).'}</div>` : ''}
    <div class="callout small" style="margin-top:10px">${(a.evidence || {}).nature || 'Recommendation — not a transaction.'}</div>
    <h4>Evidence chain</h4>${evidenceChain(a)}
    <h4>History</h4><div class="small">requested by <b>${run.requested_by}</b> ${dt(run.created_at)}
      ${a.submitted_by ? html` · submitted by <b>${a.submitted_by.user}</b> (${a.submitted_by.role}) ${dt(a.submitted_by.at)}` : ''}
      ${a.approved_by ? html` · approved by <b>${a.approved_by.user}</b> (${a.approved_by.role}) ${dt(a.approved_by.at)}` : ''}
      ${a.rejected_by ? html` · rejected by <b>${a.rejected_by.user}</b>: “${a.rejected_by.comment}”` : ''}
      ${a.prepared_by ? html` · prepared by <b>${a.prepared_by.user}</b> ${dt(a.prepared_by.at)}` : ''}</div>
    <div class="row" style="margin-top:14px" id="ap-btns">
      ${pending ? html`<button class="btn good" id="ap-approve" ${blocked ? 'disabled' : ''}>Approve</button>
        <button class="btn danger" id="ap-reject" ${blocked ? 'disabled' : ''}>Reject…</button>
        ${blocked ? html`<span class="badge b-crit">✕ ${blocked}</span>` : ''}` : ''}
      ${a.status === 'APPROVED' && (payment || MANDATES.has(a.type)) ? html`<button class="btn primary" id="ap-prepare" ${can('prepare_execution') ? '' : 'disabled title="Treasurer or CFO"'}>${payment ? 'Prepare execution (validate → READY)' : 'Prepare mandate (validate → READY)'}</button>` : ''}
      ${a.status === 'READY' ? html`<button class="btn primary" id="ap-release" ${can('release_payments') ? '' : 'disabled'}>${payment ? 'Release to bank…' : 'Issue mandate (dual control)…'}</button>` : ''}
    </div><div id="ap-msg"></div>
    ${art && art.validation ? html`<h4>Pre-release validation</h4>${art.validation.map((c) => html`<div class="small">${c.passed ? '✓' : '✕'} ${c.check}</div>`)}` : ''}
    ${art ? html`<h4>${art.kind === 'iso20022_pain001_draft' ? `ISO 20022 pain.001 payment file — ${art.state || 'DRAFT'}` : art.kind === 'decision_mandate' ? 'Decision record' : 'Escalation draft'}</h4>
      ${art.kind === 'iso20022_pain001_draft' ? html`<div class="small muted">${art.status} · sha256 ${art.sha256 || ''}</div>
        <div class="draft-doc"><pre class="mono small" style="margin:0;white-space:pre-wrap;max-height:260px;overflow:auto">${art.xml.replace(/></g, '>\n<')}</pre></div>
        <button class="btn" id="ap-dl" style="margin-top:8px">Download ${art.state || 'DRAFT'} XML</button>` : html`<pre class="mono small" style="white-space:pre-wrap">${art.text}</pre>`}` : ''}`,
  { hint: `${run.run_id} · ${a.rec_id || ''}`, right: classBadge(run.classification) }));
  const after = async (msg) => { toast(msg); invalidate(''); refreshApprovalCount(); await approvals($('#main'));
    $$('[data-k]').find((x) => x.dataset.k === `${runId}|${actionId}`)?.click(); };
  const decide = async (decision) => {
    const res = await confirmModal(decision === 'APPROVE' ? `Approve ${a.action_id}?` : `Reject ${a.action_id}?`,
      String(html`<p>${title(a.type)} of <b>${full(a.amount_usd)}</b>. ${decision === 'APPROVE' ? (payment ? 'A DRAFT pain.001 payment file will be generated; nothing is transmitted.' : 'A decision record will be generated.') : 'A written rationale is required.'}</p>`),
      { okText: decision === 'APPROVE' ? 'Approve' : 'Reject', okCls: decision === 'APPROVE' ? 'good' : 'danger', requireText: decision === 'REJECT' });
    if (!res) return;
    try { await api(`/v1/runs/${encodeURIComponent(runId)}/actions/${actionId}/decision`, { method: 'POST', body: { decision, comment: res.comment } });
      after(`${a.action_id} ${decision === 'APPROVE' ? 'approved' : 'rejected'} — recorded in the audit trail`);
    } catch (e) { toast(e.message, true); }
  };
  $('#ap-approve')?.addEventListener('click', () => decide('APPROVE'));
  $('#ap-reject')?.addEventListener('click', () => decide('REJECT'));
  $('#ap-prepare')?.addEventListener('click', async () => {
    try { await api(`/v1/runs/${encodeURIComponent(runId)}/actions/${actionId}/prepare`, { method: 'POST' }); after(payment ? `${a.action_id} validated — payment file READY (not transmitted)` : `${a.action_id} mandate validated — READY for dual-control issue`); }
    catch (e) { toast(e.message, true); }
  });
  $('#ap-release')?.addEventListener('click', async () => {
    const ok = await confirmModal(payment ? `Release ${a.action_id} to the bank?` : `Issue mandate ${a.action_id}?`, String(payment ? html`<p>Explicit authorisation to transmit <b>${full(a.amount_usd)}</b>. Requires a second signatory
      who is not the requester, submitter or approver, and a configured bank channel.</p>` : html`<p>Issue the approved decision mandate to its owner. You must not be the requester, submitter or approver (dual control).
      No money moves.</p>`), { okText: payment ? 'Authorise release' : 'Issue mandate', okCls: payment ? 'danger' : 'primary' });
    if (!ok) return;
    try { await api(`/v1/runs/${encodeURIComponent(runId)}/actions/${actionId}/release`, { method: 'POST' }); if (!payment) after(`${a.action_id} mandate issued under dual control — audited`); }
    catch (e) { set($('#ap-msg'), html`<div class="callout crit small" style="margin-top:10px"><b>Release refused (audited):</b> ${e.message}</div>`); }
  });
  $('#ap-dl')?.addEventListener('click', () => { const bl = new Blob([art.xml], { type: 'application/xml' });
    const u = URL.createObjectURL(bl); const l = document.createElement('a'); l.href = u; l.download = `${a.action_id}_pain001_${art.state || 'DRAFT'}.xml`; l.click(); URL.revokeObjectURL(u); });
}
void pct;
