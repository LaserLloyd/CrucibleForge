(function () {
'use strict';
// The board data is baked into <script type="application/json" id="board-data">
// by render_html.py — no fetch, no sidecar file.
//
// ONE state drives every layout: the table (desktop, and the phone's Table
// view) and the phone cards. State = the viewer's chosen right-hand columns
// (ordered), sort key/dir, per-column ≥ filters, text filter, benched-only,
// expanded rows and the phone view. It is saved on every change (debounced):
//   - inside DisPatch (a sandboxed frame: no localStorage of its own) through
//     the shell's tool-state bridge — {type:'dispatch:tool-state', op:'get'|'set'};
//   - opened directly, in localStorage.
// Both are best-effort; a page that can save nothing still works.
const DATA = JSON.parse(document.getElementById('board-data').textContent);
const rows = DATA.rows;
const COMPS = DATA.components;            // [{label, side}] in column order
const THRESHOLDS = DATA.thresholds;       // [10, 30, 50, 70, 90, 100]
const MAIN = DATA.main;                   // fixed left columns [{key, label, kind}]
const EXTRA = DATA.extra;                 // choosable right-hand columns [{key, label, kind, side?, group}]
const MAIN_BY = {}; MAIN.forEach(c => { MAIN_BY[c.key] = c; });
const EXTRA_BY = {}; EXTRA.forEach(c => { EXTRA_BY[c.key] = c; });
const HEAD_FILTERS = ['chat', 'coding', 'overall'];   // always filterable
const TEXT_COLS = new Set(['label', 'date', 'notes', 'judge', 'coverage', 'provider']);
const LABELS = new Set(rows.map(r => r.label));
const STATE_VERSION = 1;                  // bump = every saved layout resets
const STORE_KEY = 'crucibleforge-board-layout';
const BRIDGE = 'dispatch:tool-state';
const MAX_STATE = 16 * 1024;

function defaults() {
  return {columns: COMPS.map(c => 'c:' + c.label), sortKey: 'rank', sortDir: 1,
          filters: {}, text: '', benched: false, expanded: [], view: 'table'};
}
let S = defaults();
let expanded = new Set();
let filtersOpen = false, colsOpen = false, dragKey = null;

const $ = id => document.getElementById(id);
function esc(s) {
  return String(s).replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
}
function isNum(v) { return v !== null && v !== undefined && v !== '' && !isNaN(v); }
function num(v, d) { return isNum(v) ? Number(v).toFixed(d) : '<span class="muted">–</span>'; }
function band(v) { return !isNum(v) ? '' : (v < 50 ? ' lo' : (v < 75 ? ' mid' : '')); }
function colOf(k) { return MAIN_BY[k] || EXTRA_BY[k]; }
function value(r, k) {
  if (k.startsWith('c:')) { const v = (r.components || {})[k.slice(2)]; return isNum(v) ? Number(v) : null; }
  const v = r[k];
  if (TEXT_COLS.has(k)) return v === null || v === undefined ? '' : String(v).toLowerCase();
  return isNum(v) ? Number(v) : null;
}
function filterKeys(cols) {
  return HEAD_FILTERS.concat(cols.filter(k => EXTRA_BY[k] && EXTRA_BY[k].kind === 'num'));
}

// ------------------------------------------------------------ saved state
/** Whatever came back from storage → a valid state. Unknown columns, filters
 *  on columns that are not shown, and rows that no longer exist are dropped
 *  silently; another `version` means defaults. */
function sanitize(raw) {
  const d = defaults();
  if (!raw || typeof raw !== 'object' || raw.version !== STATE_VERSION) return d;
  if (Array.isArray(raw.columns)) {
    const seen = new Set();
    d.columns = raw.columns.filter(k => typeof k === 'string' && EXTRA_BY[k] && !seen.has(k) && seen.add(k));
  }
  if (typeof raw.sortKey === 'string' && colOf(raw.sortKey)) {      // an unknown key: rank, top first
    d.sortKey = raw.sortKey;
    if (raw.sortDir === 1 || raw.sortDir === -1) d.sortDir = raw.sortDir;
  }
  const fk = filterKeys(d.columns);
  if (raw.filters && typeof raw.filters === 'object') {
    for (const k of Object.keys(raw.filters)) {
      const v = raw.filters[k];
      if (fk.includes(k) && typeof v === 'number' && isFinite(v) && v >= 0) d.filters[k] = v;
    }
  }
  if (typeof raw.text === 'string') d.text = raw.text.slice(0, 200);
  d.benched = raw.benched === true;
  if (Array.isArray(raw.expanded)) d.expanded = raw.expanded.filter(l => typeof l === 'string' && LABELS.has(l));
  if (raw.view === 'table' || raw.view === 'cards') d.view = raw.view;
  return d;
}
function serialize() {
  return {version: STATE_VERSION, columns: S.columns.slice(), sortKey: S.sortKey, sortDir: S.sortDir,
          filters: Object.assign({}, S.filters), text: S.text, benched: S.benched,
          expanded: [...expanded], view: S.view};
}
const framed = (() => { try { return window.parent !== window; } catch (e) { return true; } })();
let saveTimer = null, touched = false, bridgeHeard = false;
function writeNow() {
  saveTimer = null;
  const st = serialize();
  let json = JSON.stringify(st);
  if (json.length > MAX_STATE) { st.expanded = []; json = JSON.stringify(st); }   // the only unbounded part
  if (framed) { try { window.parent.postMessage({type: BRIDGE, op: 'set', state: st}, '*'); } catch (e) { /* no bridge */ } }
  try { window.localStorage.setItem(STORE_KEY, json); } catch (e) { /* sandboxed / disabled: fine */ }
}
function save() {
  touched = true;
  if (saveTimer) clearTimeout(saveTimer);
  saveTimer = setTimeout(writeNow, 300);
}
function loadLocal() {
  try { const s = window.localStorage.getItem(STORE_KEY); return s ? JSON.parse(s) : null; } catch (e) { return null; }
}
// The shell answers `get` and also pushes once after load: the FIRST answer is
// the saved layout; every later one is an ack of our own `set` and ignored.
function onBridge(ev) {
  if (ev.source !== window.parent) return;
  const d = ev.data;
  if (!d || typeof d !== 'object' || d.type !== BRIDGE || bridgeHeard) return;
  bridgeHeard = true;
  if (d.state && !touched) applyState(sanitize(d.state));
}
function applyState(st) {
  expanded = new Set(st.expanded);
  S = st;
  filtersOpen = Object.keys(S.filters).length > 0;
  $('filter').value = S.text;
  $('benched').checked = S.benched;
  renderFilters();
  renderChooser();
  render();
}

// ------------------------------------------------------------ rows
function compare(a, b) {
  const ka = value(a, S.sortKey), kb = value(b, S.sortKey);
  const ea = ka === null || ka === '', eb = kb === null || kb === '';
  if (ea && eb) return a.rank - b.rank;
  if (ea) return 1;                       // missing values always sort last
  if (eb) return -1;
  if (ka < kb) return -S.sortDir;
  if (ka > kb) return S.sortDir;
  return a.rank - b.rank;
}
function passes(r) {
  if (S.benched && !isNum(r.overall)) return false;
  if (S.text) {
    const f = S.text.toLowerCase();
    if (!(r.label.toLowerCase().includes(f) || String(r.model_id || '').toLowerCase().includes(f)
          || String(r.provider || '').toLowerCase().includes(f))) return false;
  }
  for (const k in S.filters) {
    const v = value(r, k);
    if (v === null || v < S.filters[k]) return false;
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
    '<div class="group wide"><h4>Notes</h4><div class="meta-line">' + esc(r.notes || 'none') + '</div></div>' +
    '<div class="group"><h4>Run date</h4><div class="meta-line">' + esc(r.date || '–') + '</div></div>' +
    metaGroups(r) + '</div>';
}
// Phone card: headline numbers big, every component in two short columns; the
// bookkeeping sits behind the per-card Details toggle (shares the ▸ set).
function card(r) {
  const open = expanded.has(r.label);
  const cls = r.tier >= 2 ? ' fail-card' : (r.tier >= 1 ? ' warn-card' : '');
  const head = [['chat', 'Chat'], ['coding', 'Coding'], ['overall', 'Overall']].map(([k, t]) =>
    '<div class="hl' + (S.sortKey === k ? ' sorted' : '') + '"><span class="hl-lbl">' + t + '</span>' +
    '<span class="hl-val">' + num(r[k], 1) + '</span></div>').join('');
  const col = s => {
    const items = COMPS.filter(c => c.side === s).map(c => {
      const v = (r.components || {})[c.label];
      return '<div class="chip side-' + s + (S.sortKey === 'c:' + c.label ? ' sorted' : '') + '">' +
        '<span class="lbl">' + esc(c.label) + '</span><span class="val' + band(v) + '">' + num(v, 0) + '</span></div>';
    }).join('');
    return items ? '<div class="ccol"><h4>' + (s === 'chat' ? 'Chat' : 'Coding') + '</h4>' + items + '</div>' : '';
  };
  return '<article class="card' + cls + '" data-label="' + esc(r.label) + '">' +
    '<div class="card-top"><span class="rank">#' + r.rank + '</span><h2 class="name">' + esc(r.label) + '</h2></div>' +
    '<div class="hls">' + head + '</div>' +
    '<div class="sub"><span' + (S.sortKey === 'tok_s' ? ' class="sorted"' : '') + '>' + num(r.tok_s, 1) + ' tok/s</span>' +
    '<span' + (S.sortKey === 'date' ? ' class="sorted"' : '') + '>' + esc(r.date || '–') + '</span></div>' +
    (r.notes ? '<div class="card-notes">' + esc(r.notes) + '</div>' : '') +
    '<div class="ccols">' + col('chat') + col('coding') + '</div>' +
    '<button class="card-toggle" type="button" aria-expanded="' + open + '" data-toggle="' + esc(r.label) + '">' +
    (open ? '▾ Hide details' : '▸ Details: judge, coverage, id') + '</button>' +
    (open ? '<div class="card-detail">' + metaGroups(r) + '</div>' : '') +
    '</article>';
}
function th(k) {
  const c = colOf(k);
  const on = S.sortKey === k;
  const cls = c.side ? 'side-' + c.side : (MAIN_BY[k] ? 'main-col' : 'extra-col');
  return '<th data-k="' + esc(k) + '" scope="col" class="' + cls + (k === 'label' ? ' label' : '') + '" aria-sort="' +
    (on ? (S.sortDir === 1 ? 'ascending' : 'descending') : 'none') + '"' +
    (c.side ? ' title="' + (c.side === 'chat' ? 'Chat' : 'Coding') + ' component"' : '') + '>' + esc(c.label) +
    ' <span class="arrow">' + (on ? (S.sortDir === 1 ? '▲' : '▼') : '⇅') + '</span></th>';
}
function cell(r, k) {
  const c = EXTRA_BY[k];
  if (k.startsWith('c:')) {
    const v = (r.components || {})[c.label];
    return '<td class="num comp-col side-' + c.side + band(v) + '">' + num(v, 0) + '</td>';
  }
  if (k === 'notes') return '<td class="notes">' + esc(r.notes || '') + '</td>';
  if (k === 'date') return '<td class="num">' + esc(r.date || '–') + '</td>';
  return '<td class="meta-col">' + esc(r[k] || '–') + '</td>';
}
function dirText() {
  if (S.sortKey === 'rank') return S.sortDir === 1 ? '▲ Top first' : '▼ Bottom first';
  if (TEXT_COLS.has(S.sortKey)) return S.sortDir === 1 ? '▲ A → Z' : '▼ Z → A';
  return S.sortDir === -1 ? '▼ High first' : '▲ Low first';
}
function render() {
  const data = rows.filter(passes).sort(compare);
  const ncol = 1 + MAIN.length + S.columns.length;
  $('thead').innerHTML = '<tr><th scope="col" class="xcol" aria-label="details"></th>' +
    MAIN.map(c => th(c.key)).join('') + S.columns.map(th).join('') + '</tr>';
  $('tbody').innerHTML = data.map(r => {
    const open = expanded.has(r.label);
    const cls = r.tier >= 2 ? ' fail-row' : (r.tier >= 1 ? ' warn-row' : '');
    return '<tr class="data-row' + cls + '" data-label="' + esc(r.label) + '">' +
      '<td class="xcol"><button class="toggle" aria-expanded="' + open + '" aria-label="details" data-toggle="' + esc(r.label) + '">' + (open ? '▾' : '▸') + '</button></td>' +
      '<td class="num">' + r.rank + '</td><td class="label">' + esc(r.label) + '</td>' +
      '<td class="num strong">' + num(r.chat, 1) + '</td><td class="num strong">' + num(r.coding, 1) + '</td>' +
      '<td class="num strong">' + num(r.overall, 1) + '</td><td class="num">' + num(r.tok_s, 1) + '</td>' +
      S.columns.map(k => cell(r, k)).join('') + '</tr>' +
      (open ? '<tr class="detail-row"><td colspan="' + ncol + '">' + detail(r) + '</td></tr>' : '');
  }).join('') || '<tr><td colspan="' + ncol + '" class="empty">No model matches these filters.</td></tr>';
  $('cards').innerHTML = data.length ? data.map(card).join('')
    : '<p class="empty">No model matches these filters.</p>';
  const nf = Object.keys(S.filters).length;
  const fb = $('filtersBtn');
  fb.textContent = (filtersOpen ? '▾ ' : '▸ ') + 'Filters' + (nf ? ' (' + nf + ')' : '');
  fb.setAttribute('aria-expanded', String(filtersOpen));
  fb.classList.toggle('is-active', nf > 0);
  $('filtersRow').classList.toggle('open', filtersOpen);
  $('sortSel').value = S.sortKey;
  $('sortDir').textContent = dirText();
  document.body.setAttribute('data-view', S.view);
  $('viewTable').setAttribute('aria-pressed', String(S.view === 'table'));
  $('viewCards').setAttribute('aria-pressed', String(S.view === 'cards'));
  $('colsBtn').setAttribute('aria-expanded', String(colsOpen));
  $('colsPanel').classList.toggle('open', colsOpen);
  $('count').innerHTML = data.length + ' of ' + rows.length + ' models' +
    '<span class="desk-only"> · click any column to sort · ▸ shows every component, notes, run date and judge</span>';
}

// ------------------------------------------------------------ filters row
// Rebuilt only when the column set changes (never while typing): Chat, Coding
// and Overall always, then one per chosen numeric column, in column order.
function filterChip(k) {
  const c = colOf(k);
  const v = S.filters[k];
  const custom = isNum(v) && !THRESHOLDS.includes(v);
  const opts = '<option value="">any</option>' + THRESHOLDS.map(t =>
    '<option value="' + t + '"' + (v === t ? ' selected' : '') + '>' + (t < 100 ? '≥' : '') + t + '</option>').join('') +
    '<option value="custom" disabled' + (custom ? ' selected' : '') + '>custom</option>';
  return '<div class="comp-filter ' + (c.side ? 'side-' + c.side : 'main') + (k in S.filters ? ' is-active' : '') +
    '" data-f="' + esc(k) + '" role="group" aria-label="minimum ' + esc(c.label) + '">' +
    '<span class="lbl">' + esc(c.label) + '</span>' +
    '<select aria-label="minimum ' + esc(c.label) + '">' + opts + '</select>' +
    '<span class="ge" aria-hidden="true">≥</span><input type="number" inputmode="decimal" min="0" max="100" step="any" ' +
    'placeholder="__" value="' + (isNum(v) ? v : '') + '" aria-label="minimum ' + esc(c.label) + ', any number"></div>';
}
function renderFilters() {
  $('filtersRow').innerHTML = '<span class="flabel">Filter by element →</span>' +
    filterKeys(S.columns).map(filterChip).join('') +
    '<button type="button" data-act="clear" title="Clear every element filter">Clear</button>';
}
function setFilter(chip, k, n) {
  if (n === null) delete S.filters[k]; else S.filters[k] = n;
  if (chip && chip.classList) chip.classList.toggle('is-active', k in S.filters);
  render(); save();
}
function onFilterChange(e) {
  const t = e.target;
  if (!t || t.tagName !== 'SELECT') return;
  const chip = t.closest('[data-f]');
  if (!chip) return;
  if (t.value === 'custom') return;
  const n = t.value === '' ? null : Number(t.value);
  const inp = chip.querySelector('input');
  if (inp) inp.value = n === null ? '' : String(n);
  setFilter(chip, chip.dataset.f, n);
}
function onFilterInput(e) {
  const t = e.target;
  if (!t || t.tagName !== 'INPUT') return;
  const chip = t.closest('[data-f]');
  if (!chip) return;
  const raw = String(t.value).trim();
  const n = raw === '' ? null : Number(raw);
  if (n !== null && (!isFinite(n) || n < 0)) return;
  const sel = chip.querySelector('select');
  if (sel) sel.value = n === null ? '' : (THRESHOLDS.includes(n) ? String(n) : 'custom');
  setFilter(chip, chip.dataset.f, n);
}
function onFilterClick(e) {
  if (!e.target.closest('[data-act="clear"]')) return;
  S.filters = {};
  renderFilters(); render(); save();
}

// ------------------------------------------------------------ column chooser
// Every choosable column: the chosen ones first in their order (checked,
// ↑/↓ and drag to reorder), then the rest in default order (unchecked).
function renderChooser() {
  const rest = EXTRA.map(c => c.key).filter(k => !S.columns.includes(k));
  const n = S.columns.length;
  $('colsList').innerHTML = S.columns.concat(rest).map(k => {
    const c = EXTRA_BY[k];
    const i = S.columns.indexOf(k), on = i >= 0;
    return '<li class="col-item' + (on ? ' on' : '') + '" draggable="true" data-col="' + esc(k) + '">' +
      '<span class="grip" aria-hidden="true">⋮⋮</span>' +
      '<label><input type="checkbox" data-col="' + esc(k) + '"' + (on ? ' checked' : '') + '> ' +
      '<span class="cl">' + esc(c.label) + '</span> <span class="cg">' + esc(c.group) + '</span></label>' +
      '<button type="button" data-move="-1" data-col="' + esc(k) + '" aria-label="Move ' + esc(c.label) + ' left"' +
      (!on || i === 0 ? ' disabled' : '') + '>↑</button>' +
      '<button type="button" data-move="1" data-col="' + esc(k) + '" aria-label="Move ' + esc(c.label) + ' right"' +
      (!on || i === n - 1 ? ' disabled' : '') + '>↓</button></li>';
  }).join('');
}
function columnsChanged() { renderChooser(); renderFilters(); render(); save(); }
function setColumn(k, on) {
  if (!EXTRA_BY[k]) return;
  const has = S.columns.includes(k);
  if (on && !has) S.columns.push(k);
  if (!on && has) {
    S.columns = S.columns.filter(x => x !== k);
    delete S.filters[k];                   // a hidden filter would hide rows silently
    if (S.sortKey === k) { S.sortKey = 'rank'; S.sortDir = 1; }
  }
  columnsChanged();
}
function moveColumn(k, delta) {
  const i = S.columns.indexOf(k), j = i + delta;
  if (i < 0 || j < 0 || j >= S.columns.length) return;
  S.columns.splice(i, 1); S.columns.splice(j, 0, k);
  columnsChanged();
}
/** Drag-and-drop: put `k` just before `target` (dropping on an unchosen row
 *  = append). Dragging an unchosen column in also chooses it. */
function moveBefore(k, target) {
  if (!EXTRA_BY[k] || k === target) return;
  S.columns = S.columns.filter(x => x !== k);
  const j = S.columns.indexOf(target);
  if (j < 0) S.columns.push(k); else S.columns.splice(j, 0, k);
  columnsChanged();
}
function onColsChange(e) {
  const t = e.target;
  if (t && t.type === 'checkbox' && t.dataset.col) setColumn(t.dataset.col, t.checked);
}
function onColsClick(e) {
  const b = e.target.closest('[data-move]');
  if (b && !b.disabled) moveColumn(b.dataset.col, Number(b.dataset.move));
}
function itemOf(e) { const it = e.target && e.target.closest ? e.target.closest('li[data-col]') : null; return it; }
function onDragStart(e) {
  const it = itemOf(e);
  if (!it) return;
  dragKey = it.dataset.col;
  if (e.dataTransfer) { e.dataTransfer.effectAllowed = 'move'; try { e.dataTransfer.setData('text/plain', dragKey); } catch (x) { /* old engines */ } }
  it.classList.add('dragging');
}
function onDragOver(e) { if (dragKey && itemOf(e)) { e.preventDefault(); if (e.dataTransfer) e.dataTransfer.dropEffect = 'move'; } }
function onDrop(e) {
  const it = itemOf(e);
  if (!dragKey || !it) return;
  e.preventDefault();
  const k = dragKey; dragKey = null;
  moveBefore(k, it.dataset.col);
}
function onDragEnd() { dragKey = null; renderChooser(); }

// ------------------------------------------------------------ wiring
function defaultDir(k) { return (k === 'rank' || TEXT_COLS.has(k)) ? 1 : -1; }
function sortBy(k) {
  if (!colOf(k)) return;
  if (S.sortKey === k) S.sortDir = -S.sortDir;
  else { S.sortKey = k; S.sortDir = defaultDir(k); }
  render(); save();
}
function onListClick(e) {
  const t = e.target.closest('[data-toggle]');
  if (t) {
    const l = t.dataset.toggle;
    if (expanded.has(l)) expanded.delete(l); else expanded.add(l);
    render(); save();
    return;
  }
  const c = e.target.closest('[data-sort]');
  if (c) sortBy(c.dataset.sort);
}
function onHeadClick(e) { const h = e.target.closest('th[data-k]'); if (h) sortBy(h.dataset.k); }
function setView(v) { S.view = v; render(); save(); }
function resetLayout() {
  applyState(defaults());
  touched = true;
  if (saveTimer) clearTimeout(saveTimer);
  writeNow();
}
function init() {
  $('filter').addEventListener('input', e => { S.text = e.target.value; render(); save(); });
  $('benched').addEventListener('change', e => { S.benched = !!e.target.checked; render(); save(); });
  $('expandAll').addEventListener('click', () => { rows.forEach(r => expanded.add(r.label)); render(); save(); });
  $('collapseAll').addEventListener('click', () => { expanded.clear(); render(); save(); });
  $('sortSel').addEventListener('change', e => { S.sortKey = e.target.value; S.sortDir = defaultDir(S.sortKey); render(); save(); });
  $('sortDir').addEventListener('click', () => { S.sortDir = -S.sortDir; render(); save(); });
  $('filtersBtn').addEventListener('click', () => { filtersOpen = !filtersOpen; render(); });
  $('viewTable').addEventListener('click', () => setView('table'));
  $('viewCards').addEventListener('click', () => setView('cards'));
  $('colsBtn').addEventListener('click', () => { colsOpen = !colsOpen; render(); });
  $('colsDone').addEventListener('click', () => { colsOpen = false; render(); });
  $('resetLayout').addEventListener('click', resetLayout);
  const fr = $('filtersRow');
  fr.addEventListener('change', onFilterChange);
  fr.addEventListener('input', onFilterInput);
  fr.addEventListener('click', onFilterClick);
  const cl = $('colsList');
  cl.addEventListener('change', onColsChange);
  cl.addEventListener('click', onColsClick);
  cl.addEventListener('dragstart', onDragStart);
  cl.addEventListener('dragover', onDragOver);
  cl.addEventListener('drop', onDrop);
  cl.addEventListener('dragend', onDragEnd);
  $('thead').addEventListener('click', onHeadClick);
  $('tbody').addEventListener('click', onListClick);
  $('cards').addEventListener('click', onListClick);
  applyState(sanitize(loadLocal()));
  if (framed) {
    window.addEventListener('message', onBridge);
    try { window.parent.postMessage({type: BRIDGE, op: 'get'}, '*'); } catch (e) { /* no bridge */ }
  }
}
if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init); else init();
})();
