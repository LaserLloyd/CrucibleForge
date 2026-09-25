(function () {
'use strict';
// The board data is baked into <script type="application/json" id="board-data">
// by render_html.py — no fetch, no sidecar file.
// ONE state (sort key/dir, text filter, benched toggle, element filters,
// expanded set) drives BOTH layouts: the wide table (> 720 px) and the phone
// cards (<= 720 px). Both are rendered on every change and CSS shows one, so
// rotating a phone keeps the exact view.
const DATA = JSON.parse(document.getElementById('board-data').textContent);
const rows = DATA.rows;
const COMPS = DATA.components;            // [{label, side}] in column order
const THRESHOLDS = DATA.thresholds;       // [10, 30, 50, 70, 90, 100]
const TEXT_COLS = new Set(['label', 'date', 'notes']);
let sortKey = 'rank', sortDir = 1, filter = '', benchedOnly = false, filtersOpen = false;
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
function metaGroups(r) {
  return '<div class="group"><h4>Judge</h4><div class="meta-line">' + esc(r.judge || '–') + '</div></div>' +
    '<div class="group"><h4>Coverage</h4><div class="meta-line">' + esc(r.coverage || '–') + '</div></div>' +
    '<div class="group"><h4>Model id</h4><div class="meta-line">' + esc(r.model_id || '–') + '</div></div>' +
    '<div class="group"><h4>Provider</h4><div class="meta-line">' + esc(r.provider || '–') + '</div></div>';
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
    metaGroups(r) + '</div>';
}
// Phone card: headline numbers big, components always visible (they are the
// point of the board and fit in two short columns); only the bookkeeping
// (judge, coverage, model id, provider) sits behind the per-card Details toggle,
// which shares the table's ▸ expanded set.
function card(r) {
  const open = expanded.has(r.label);
  const cls = r.tier >= 2 ? ' fail-card' : (r.tier >= 1 ? ' warn-card' : '');
  const head = [['chat', 'Chat'], ['coding', 'Coding'], ['overall', 'Overall']].map(([k, t]) =>
    '<div class="hl' + (sortKey === k ? ' sorted' : '') + '"><span class="hl-lbl">' + t + '</span>' +
    '<span class="hl-val">' + num(r[k], 1) + '</span></div>').join('');
  const col = s => {
    const items = COMPS.filter(c => c.side === s).map(c => {
      const v = (r.components || {})[c.label];
      return '<div class="chip side-' + s + (sortKey === 'c:' + c.label ? ' sorted' : '') + '">' +
        '<span class="lbl">' + esc(c.label) + '</span><span class="val' + band(v) + '">' + num(v, 0) + '</span></div>';
    }).join('');
    return items ? '<div class="ccol"><h4>' + (s === 'chat' ? 'Chat' : 'Coding') + '</h4>' + items + '</div>' : '';
  };
  return '<article class="card' + cls + '" data-label="' + esc(r.label) + '">' +
    '<div class="card-top"><span class="rank">#' + r.rank + '</span><h2 class="name">' + esc(r.label) + '</h2></div>' +
    '<div class="hls">' + head + '</div>' +
    '<div class="sub"><span' + (sortKey === 'tok_s' ? ' class="sorted"' : '') + '>' + num(r.tok_s, 1) + ' tok/s</span>' +
    '<span' + (sortKey === 'date' ? ' class="sorted"' : '') + '>' + esc(r.date || '–') + '</span></div>' +
    (r.notes ? '<div class="card-notes">' + esc(r.notes) + '</div>' : '') +
    '<div class="ccols">' + col('chat') + col('coding') + '</div>' +
    '<button class="card-toggle" type="button" aria-expanded="' + open + '" data-toggle="' + esc(r.label) + '">' +
    (open ? '▾ Hide details' : '▸ Details: judge, coverage, id') + '</button>' +
    (open ? '<div class="card-detail">' + metaGroups(r) + '</div>' : '') +
    '</article>';
}
function dirText() {
  if (sortKey === 'rank') return sortDir === 1 ? '▲ Top first' : '▼ Bottom first';
  if (TEXT_COLS.has(sortKey)) return sortDir === 1 ? '▲ A → Z' : '▼ Z → A';
  return sortDir === -1 ? '▼ High first' : '▲ Low first';
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
  document.getElementById('cards').innerHTML = data.length ? data.map(card).join('')
    : '<p class="empty">No model matches these filters.</p>';
  document.querySelectorAll('th[data-k]').forEach(th => {
    const a = th.querySelector('.arrow');
    const on = th.dataset.k === sortKey;
    a.textContent = on ? (sortDir === 1 ? '▲' : '▼') : '⇅';
    th.setAttribute('aria-sort', on ? (sortDir === 1 ? 'ascending' : 'descending') : 'none');
  });
  document.querySelectorAll('.comp-filter').forEach(el => el.classList.toggle('is-active', el.dataset.c in minScore));
  const nf = Object.keys(minScore).length;
  const fb = document.getElementById('filtersBtn');
  fb.textContent = (filtersOpen ? '▾ ' : '▸ ') + 'Filters' + (nf ? ' (' + nf + ')' : '');
  fb.setAttribute('aria-expanded', String(filtersOpen));
  fb.classList.toggle('is-active', nf > 0);
  document.getElementById('filtersRow').classList.toggle('open', filtersOpen);
  document.getElementById('sortSel').value = sortKey;
  document.getElementById('sortDir').textContent = dirText();
  document.getElementById('count').innerHTML = data.length + ' of ' + rows.length + ' models' +
    '<span class="desk-only"> · click any column to sort · ▸ shows components, notes and judge</span>';
}
function defaultDir(k) { return (k === 'rank' || TEXT_COLS.has(k)) ? 1 : -1; }
function sortBy(k) {
  if (sortKey === k) sortDir = -sortDir;
  else { sortKey = k; sortDir = defaultDir(k); }
  render();
}
function onListClick(e) {
  const t = e.target.closest('[data-toggle]');
  if (t) {
    const l = t.dataset.toggle;
    if (expanded.has(l)) expanded.delete(l); else expanded.add(l);
    render();
    return;
  }
  const c = e.target.closest('[data-sort]');
  if (c) sortBy(c.dataset.sort);
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
  document.getElementById('sortSel').addEventListener('change', e => {
    sortKey = e.target.value; sortDir = defaultDir(sortKey); render();
  });
  document.getElementById('sortDir').addEventListener('click', () => { sortDir = -sortDir; render(); });
  document.getElementById('filtersBtn').addEventListener('click', () => { filtersOpen = !filtersOpen; render(); });
  document.querySelectorAll('th[data-k]').forEach(th => th.addEventListener('click', () => sortBy(th.dataset.k)));
  document.getElementById('tbody').addEventListener('click', onListClick);
  document.getElementById('cards').addEventListener('click', onListClick);
  render();
}
if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init); else init();
})();
