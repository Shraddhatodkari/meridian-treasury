// Executive Command Center — answers the eight questions a CFO asks first, each with lineage-backed numbers.
import {
  $, $$, C, api, axisMoney, badge, basis, cached, card, chart, confBadge, esc, fanChart, heatmap5, html, kpiM, loading,
  num, pct, prio, state, statusBadge, toast, usd, waterfall,
} from './core.js';
import { can, head, q, set, weeksOf } from './p_common.js';

export async function runAnalysis(btn) {
  if (btn) btn.disabled = true;
  toast('Running the governed agent chain (13 steps)…');
  try {
    const r = await api('/v1/runs', { method: 'POST', body: {} });
    for (const k of Object.keys(state.cache)) delete state.cache[k];
    toast(`Analysis ${r.run_id}: ${r.recommendations?.length || 0} recommendations`);
    return r;
  } finally { if (btn) btn.disabled = false; }
}

export async function command(main) {
  set(main, loading());
  const [M, ov, fc, cov, wc, risks, recs, ents] = await Promise.all([
    cached('metrics', () => api('/v1/metrics')).then((a) => Object.fromEntries(a.map((m) => [m.metric_id, m]))),
    cached('liqov', () => api('/v1/liquidity/overview')), cached('fc:base', () => api('/v1/forecast')),
    cached('cov', () => api('/v1/covenants')), cached('wcd', () => api('/v1/working-capital/drivers')),
    api('/v1/risks'), api('/v1/recommendations'), cached('entities', () => api('/v1/entities'))]);
  const w = ov.waterfall, dso = wc.dso;
  const controlled = ents.filter((e) => e.capital_controls);
  set(main, html`
    ${head('Executive Command Center', html`As of ${fc.as_of} · ${ents.length} legal entities · USD at ECB reference rates ·
      every figure carries its basis and lineage (click any tile)`,
      html`${can('run_agents') ? html`<button class="btn primary" id="run">▶ Run liquidity analysis</button>` : ''}
        <a class="btn" href="#/recommendations">Recommendations →</a>`)}
    <div class="strip">
      ${kpiM(M.group_cash, 'Cash today', html`${usd(ov.bridge.change_usd)} over 8 weeks`)}
      ${kpiM(M.available_liquidity, 'Liquidity (cash + undrawn RCF)', html`covenant min ${usd(w.covenant_minimum_usd)}`)}
      ${kpiM(M.deployable_liquidity, 'Deployable liquidity', 'after trapped cash and buffers, incl. undrawn RCF')}
      ${kpiM(M.min_covenant_headroom_p10, 'Forward covenant headroom (P10)', fc.covenant_breach_week === null ? '✓ no breach in 13 weeks' : `breach week ${fc.covenant_breach_week}`)}
      ${kpiM(M.trapped_cash, 'Trapped cash', `${controlled.length} capital-controlled entities`)}
      ${kpiM(M.dso, 'DSO', html`${dso.change_days >= 0 ? '▲' : '▼'} ${num(Math.abs(dso.change_days))} d (90-day bridge ${num(dso.dso_t0)}→${num(dso.dso_t1)}, ${usd(dso.cash_impact_usd)})`)}
    </div>
    <div class="grid g-2" style="margin-top:16px">
      ${card(html`${q('How much liquidity is available?')}Liquidity waterfall`, html`<div class="chart tall" id="x-wf"></div>`,
        { hint: 'OBSERVED balances → CALCULATED deductions' })}
      ${card(html`${q('What is our forward liquidity?')}13-week liquidity, P10–P90`, html`<div class="chart tall" id="x-fan"></div>`,
        { hint: 'FORECAST · band = payment-timing uncertainty only (macro shocks in Scenario Lab) · RCF-22.1 / TP-3.3' })}
    </div>
    <div class="grid g-2" style="margin-top:16px">
      ${card(html`${q('What is minimum covenant headroom?')}Covenant headroom — today and on the P10 path`, html`
        <div class="bullet">${cov.covenants.map((c) => {
          const h = Math.min(c.headroom_today_pct, c.headroom_forward_pct);
          const scale = Math.max(1.6, h + 0.2);
          return html`<div class="bl"><div><b>${c.name}</b><div class="mini">${c.clause_ref} · ${c.direction} ${c.unit === 'USD' ? usd(c.threshold) : `${c.threshold}x`}</div></div>
            <div class="track" title="headroom ${pct(h)}"><div class="watch" style="left:0;width:${(0.2 / scale) * 100}%"></div>
              <div class="fill" style="width:${Math.max(0, Math.min(100, (h / scale) * 100))}%;background:${h < 0 ? C.crit : h < 0.2 ? '#e08a1e' : C.s1}"></div>
              <div class="thr" style="left:0"></div></div>
            <div style="text-align:right"><b>${pct(h, 0)}</b> headroom ${statusBadge(c.status)}<div class="mini">now ${c.unit === 'USD' ? usd(c.value_today) : `${c.value_today}x`}</div></div></div>`;
        })}</div>
        <div class="mini" style="margin-top:10px">Shaded zone = WATCH (&lt;20% headroom). Closest to breach: <b>${cov.covenants[0].name}</b>.
          EBITDA ${usd(cov.ebitda_usd)} (${cov.ebitda_basis}).</div>`)}
      ${card(html`${q('Where is cash trapped?')}Capital-controlled entities`, html`<div class="chart" id="x-trap"></div>
        <div class="mini">${usd(w.trapped_usd)} cannot leave this quarter under CR-* repatriation rules; allowances shown in green.</div>`)}
    </div>
    <div class="grid g-2" style="margin-top:16px">
      ${card(html`${q('What is driving working-capital deterioration?')}DSO ${num(dso.dso_t0)} → ${num(dso.dso_t1)} days: contribution by customer`,
        html`<div class="chart tall" id="x-dso"></div><div class="row small" style="justify-content:space-between">
          <span class="muted">${dso.formula}</span><a href="#/working-capital">Drill into drivers →</a></div>`,
        { hint: 'CALCULATED · contributions sum to the change' })}
      ${card(html`${q('What are the top risks?')}Risk heatmap (likelihood × impact)`, html`<div class="grid g-2">
        <div class="chart" id="x-heat"></div>
        <div>${risks.slice(0, 6).map((r) => html`<div class="row small" style="margin-bottom:8px;align-items:flex-start">
          ${badge(r.score, r.score >= 16 ? 'crit' : r.score >= 9 ? 'warn' : 'muted')}<div><b>${r.title}</b><div class="mini">${r.category} · owner ${r.owner}</div></div></div>`)}</div></div>`)}
    </div>
    <div style="margin-top:16px">${card(html`${q('What actions should management take?')}Recommendations from the latest analysis`,
      recs.length ? html`${recs.map((r) => { const main_ = r.impact.find((i) => i.value_usd) || r.impact[0];
        return html`<div class="rec" style="cursor:pointer" data-rec="${r.rec_id}"><div class="rh">${prio(r.priority)}
          <span class="rt">${r.title}</span>${statusBadge(r.status)}${r.stale ? badge('superseded run', 'muted') : ''}
          <span style="margin-left:auto" class="tabular strong">${main_ ? usd(main_.value_usd) : ''} ${main_ ? basis(main_.basis) : ''}</span></div>
          <div class="meta"><span>Owner <b>${r.owner}</b></span><span>Approval <b>${r.approval_required ? r.approval_required.toUpperCase() : 'none (advisory)'}</b></span>
          <span>Confidence ${confBadge(r.confidence_label)}</span><span>Timing <b>${r.timing}</b></span><span>Policy ${r.policy_refs.slice(0, 3).join(' · ')}</span></div></div>`; })}`
        : html`<div class="empty">No analysis has been run yet. ${can('run_agents') ? 'Run the liquidity analysis to generate governed recommendations.' : 'Ask a Finance Analyst, Treasurer or CFO to run the analysis.'}</div>`,
      { hint: 'Recommendations are not transactions — every payment needs approval, preparation and dual-control release' })}</div>`);

  waterfall($('#x-wf'), w.steps.map((s) => ({ label: s.label, value: s.value_usd, kind: s.kind, basis: s.basis })));
  const fan = fanChart($('#x-fan'), weeksOf(fc), fc.liquidity_usd, { covenant: fc.covenant_min_liquidity_usd, name: 'Liquidity P50' });
  fan?.setOption({ series: [...fan.getOption().series, { name: 'Management floor', type: 'line', data: weeksOf(fc).map(() => w.management_minimum_usd),
    symbol: 'none', lineStyle: { type: 'dotted', color: '#e08a1e', width: 1.5 }, itemStyle: { color: '#e08a1e' } }],
  legend: { bottom: 0, data: ['Liquidity P50', 'P10–P90 range', 'Covenant minimum', 'Management floor'] } });
  const ctl = controlled.map((e) => {
    const buf = (fc.entities.find((x) => x.entity_id === e.entity_id) || {}).buffer_usd || 0;
    const above = Math.max(0, e.balance_usd - buf);
    const allow = Math.min(above, e.repatriation_allowance_usd);
    return { id: e.entity_id, name: e.name, buf: Math.min(buf, e.balance_usd), allow, trapped: above - allow };
  });
  chart($('#x-trap'), { grid: { left: 60, right: 10, top: 10, bottom: 40 }, legend: { bottom: 0 },
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' }, valueFormatter: (v) => usd(v) },
    xAxis: { type: 'value', ...axisMoney }, yAxis: { type: 'category', data: ctl.map((c) => c.id) },
    series: [['Operating buffer', 'buf', '#98a2b3'], ['Repatriable this quarter', 'allow', C.s3], ['Trapped', 'trapped', C.s2]].map(([n, k, col]) => ({
      name: n, type: 'bar', stack: 't', data: ctl.map((c) => c[k]), itemStyle: { color: col }, barMaxWidth: 26 })) });
  const top = [...dso.by_customer.filter((c) => !c.is_remainder).slice(0, 9), ...dso.by_customer.filter((c) => c.is_remainder)].reverse();
  chart($('#x-dso'), { grid: { left: 190, right: 40, top: 10, bottom: 30 },
    tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' }, formatter: (ps) => { const c = top[ps[0].dataIndex];
      return `<b>${esc(c.customer)}</b> (${c.rating})<br>+${c.dso_contribution_days} DSO days · ${usd(c.cash_impact_usd)}<br>days late: ${c.avg_days_late_prior90 ?? '—'} → ${c.avg_days_late_last90 ?? '—'}<br>predicted P50: ${c.predicted_days_late_p50 ?? '—'} d`; } },
    xAxis: { type: 'value', name: 'DSO days', nameLocation: 'middle', nameGap: 22, splitLine: { lineStyle: { color: C.grid } } },
    yAxis: { type: 'category', data: top.map((c) => c.customer), axisLabel: { width: 180, overflow: 'truncate' } },
    series: [{ type: 'bar', data: top.map((c) => c.dso_contribution_days), itemStyle: { color: C.s2, borderRadius: [0, 3, 3, 0] }, barMaxWidth: 18,
      label: { show: true, position: 'right', formatter: (p) => `+${p.value.toFixed(2)}`, fontSize: 10 } }] });
  heatmap5($('#x-heat'), risks, () => { location.hash = '#/scenarios'; });
  $$('[data-rec]', main).forEach((el) => el.onclick = () => { location.hash = `#/recommendations?r=${encodeURIComponent(el.dataset.rec)}`; });
  $('#run')?.addEventListener('click', async (e) => { await runAnalysis(e.target); command(main); });
}
