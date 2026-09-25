(function () {
'use strict';
// The board data is baked into <script type="application/json" id="board-data">
// by render_html.py — no fetch, no sidecar file.
const DATA = JSON.parse(document.getElementById('board-data').textContent);
const rows = DATA.rows;
const COMPS = DATA.components;            // [{label, side}] in column order
const THRESHOLDS = DATA.thresholds;       // [10, 30, 50, 70, 90, 100]
const TEXT_COLS = new Set(['label', 'date', 'notes']);
let sortKey = 'rank', sortDir = 1, filter = '', benchedOnly = false;
const expanded = new Set();
const minScore = {};                      // component label -> threshold (number)

function esc(s) {
  return String(s).replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
}
function isNum(v) { return v !== null && v !== undefined && v !== '' && !isNaN(v); }
function num(v, d) { return isNum(v) ? Number(v).toFixed(d) : '<span class="muted">–</span>'; }
function band(v) { return !isNum(v) ? '' : (v < 50 ? ' lo' : (v < 75 ? ' mid' : '')); }
function value(r, k) {
  if (k.startsWith('c:')) { const v = (r.components || {})[k.slice(2)]; return isNum(v) ? Number(v) : null; }
  const v = r[k];
  if (TEXT_COLS.has(k)) return v === null || v === undefined ? '' : String(v).toLowerCase();
  return isNum(v) ? Number(v) : null;
}
function compare(a, b) {
  const ka = value(a, sortKey), kb = value(b, sortKey);
  const ea = ka === null || ka === '', eb = kb === null || kb === '';
  if (ea && eb) return a.rank - b.rank;
  if (ea) return 1;                       // missing values always sort last
  if (eb) return -1;
  if (ka < kb) return -sortDir;
  if (ka > kb) return sortDir;
  return a.rank - b.rank;
}
function passes(r) {
  if (benchedOnly && !isNum(r.overall)) return false;
  if (filter) {
    const f = filter.toLowerCase();
    if (!(r.label.toLowerCase().includes(f) || String(r.model_id || '').toLowerCase().includes(f)
          || String(r.provider || '').toLowerCase().includes(f))) return false;
  }
  for (const lbl in minScore) {
    const v = (r.components || {})[lbl];
    if (!isNum(v) || Number(v) < minScore[lbl]) return false;
  }
  return true;
}
function detail(r) {
  const side = s => COMPS.filter(c => c.side === s).map(c => {
    const v = (r.components || {})[c.label];
    return '<button class="comp" data-sort="c:' + esc(c.label) + '" title="Sort all rows by ' + esc(c.label) + '">' +
      '<span class="lbl">' + esc(c.label) + '</span><span class="val' + band(v) + '">' + num(v, 0) + '</span></button>';
  }).join('');
  const groups = ['chat', 'coding'].map(s => {
    const inner = side(s);
    return inner ? '<div class="group wide"><h4>' + (s === 'chat' ? 'Chat' : 'Coding') +
      ' components — click one to sort every row by it</h4><div class="comp-grid">' + inner + '</div></div>' : '';
  }).join('');
  return '<div class="detail">' + groups +
    '<div class="group"><h4>Notes</h4><div class="meta-line">' + esc(r.notes || 'none') + '</div></div>' +
    '<div class="group"><h4>Judge</h4><div class="meta-line">' + esc(r.judge || '–') + '</div></div>' +
    '<div class="group"><h4>Coverage</h4><div class="meta-line">' + esc(r.coverage || '–') + '</div></div>' +
    '<div class="group"><h4>Model id</h4><div class="meta-line">' + esc(r.model_id || '–') + '</div></div>' +
    '<div class="group"><h4>Provider</h4><div class="meta-line">' + esc(r.provider || '–') + '</div></div>' +
    '</div>';
}
function render() {
  const data = rows.filter(passes).sort(compare);
  const ncol = 9 + COMPS.length;
  document.getElementById('tbody').innerHTML = data.map(r => {
    const open = expanded.has(r.label);
    const cls = r.tier >= 2 ? ' fail-row' : (r.tier >= 1 ? ' warn-row' : '');
    const comps = COMPS.map(c => {
      const v = (r.components || {})[c.label];
      return '<td class="num comp-col side-' + c.side + band(v) + '">' + num(v, 0) + '</td>';
    }).join('');
    return '<tr class="data-row' + cls + '" data-label="' + esc(r.label) + '">' +
      '<td><button class="toggle" aria-expanded="' + open + '" aria-label="components" data-toggle="' + esc(r.label) + '">' + (open ? '▾' : '▸') + '</button></td>' +
      '<td class="num">' + r.rank + '</td><td class="label">' + esc(r.label) + '</td>' +
      '<td class="num strong">' + num(r.chat, 1) + '</td><td class="num strong">' + num(r.coding, 1) + '</td>' +
      '<td class="num">' + num(r.overall, 1) + '</td><td class="num">' + num(r.tok_s, 1) + '</td>' +
      '<td class="num">' + esc(r.date || '–') + '</td><td class="notes">' + esc(r.notes || '') + '</td>' + comps + '</tr>' +
      (open ? '<tr class="detail-row"><td colspan="' + ncol + '">' + detail(r) + '</td></tr>' : '');
  }).join('');
  document.querySelectorAll('th[data-k]').forEach(th => {
    const a = th.querySelector('.arrow');
    const on = th.dataset.k === sortKey;
    a.textContent = on ? (sortDir === 1 ? '▲' : '▼') : '⇅';
    th.setAttribute('aria-sort', on ? (sortDir === 1 ? 'ascending' : 'descending') : 'none');
  });
  document.querySelectorAll('.comp-filter').forEach(el => el.classList.toggle('is-active', el.dataset.c in minScore));
  document.getElementById('count').textContent = data.length + ' of ' + rows.length +
    ' models · click any column to sort · ▸ shows components, notes and judge';
}
function sortBy(k) {
  if (sortKey === k) sortDir = -sortDir;
  else { sortKey = k; sortDir = (k === 'rank' || TEXT_COLS.has(k)) ? 1 : -1; }
  render();
}
function init() {
  document.getElementById('filter').addEventListener('input', e => { filter = e.target.value; render(); });
  document.getElementById('benched').addEventListener('change', e => { benchedOnly = e.target.checked; render(); });
  document.getElementById('expandAll').addEventListener('click', () => { rows.forEach(r => expanded.add(r.label)); render(); });
  document.getElementById('collapseAll').addEventListener('click', () => { expanded.clear(); render(); });
  document.getElementById('clearFilters').addEventListener('click', () => {
    for (const k in minScore) delete minScore[k];
    document.querySelectorAll('.comp-filter select').forEach(s => { s.value = ''; });
    render();
  });
  document.querySelectorAll('.comp-filter select').forEach(sel => sel.addEventListener('change', () => {
    const c = sel.closest('.comp-filter').dataset.c;
    if (sel.value === '') delete minScore[c]; else minScore[c] = Number(sel.value);
    render();
  }));
  document.querySelectorAll('th[data-k]').forEach(th => th.addEventListener('click', () => sortBy(th.dataset.k)));
  document.getElementById('tbody').addEventListener('click', e => {
    const t = e.target.closest('[data-toggle]');
    if (t) {
      const l = t.dataset.toggle;
      if (expanded.has(l)) expanded.delete(l); else expanded.add(l);
      render();
      return;
    }
    const c = e.target.closest('[data-sort]');
    if (c) sortBy(c.dataset.sort);
  });
  render();
}
if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init); else init();
})();
