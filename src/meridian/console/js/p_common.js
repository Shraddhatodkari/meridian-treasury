// Helpers shared by workspace modules.
import { html, sortable, state, wireMetrics } from './core.js';

export const can = (perm) => (state.user?.permissions || []).includes(perm);
export const set = (el, h) => { el.innerHTML = String(h); sortable(el); wireMetrics(el); };
export const head = (t, sub, actions = '') => html`<div class="page-head"><div><h1>${t}</h1><div class="sub">${sub}</div></div>
  <div class="actions">${actions}</div></div>`;
export const weeksOf = (fc) => ['Today', ...fc.week_starts.map((d) => `W/c ${d.slice(5)}`)];
export const q = (label) => html`<div class="q-label">${label}</div>`;
