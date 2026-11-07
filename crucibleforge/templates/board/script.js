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
// every column has its own filter menu (the ▾ in its header); the operators
// depend on the column's type: a score is a number, a model name is text
const NUM_OPS = ['lt', 'le', 'gt', 'ge', 'eq', 'ne', 'between', 'empty'];
const TEXT_OPS = ['contains', 'eq', 'not', 'starts', 'empty'];
const OP_LABEL = {lt: 'Less than', le: 'Less than or equal', gt: 'Greater than', ge: 'Greater than or equal',
                  eq: 'Equal to', ne: 'Not equal', between: 'Between', empty: 'Is empty',
                  contains: 'Contains', not: 'Does not contain', starts: 'Starts with'};
const OP_LABEL_TEXT = {eq: 'Equals'};               // a text column says "equals", not "equal to"
const OP_SYM = {lt: '<', le: '≤', gt: '>', ge: '≥', eq: '=', ne: '≠'};
// component groups ("sides"), in board order: [{key, label}]
const SIDES = DATA.sides || [{key: 'chat', label: 'Chat'}, {key: 'coding', label: 'Coding'}];
const SIDE_LABEL = {}; SIDES.forEach(x => { SIDE_LABEL[x.key] = x.label; });
const SCORE_COLS = MAIN.filter(c => c.kind === 'num' && c.key !== 'rank' && c.key !== 'tok_s');
const TEXT_COLS = new Set(['label', 'base', 'class', 'lean', 'suite', 'date', 'notes', 'judge', 'coverage', 'provider', 'tiers_cell', 'raw_cell', 'vs_above', 'points_cell']);
const LABELS = new Set(rows.map(r => r.label));
// quick-filter chips are built from the rows: only values that exist are offered
const CLASS_ORDER = ['S', 'A', 'B', 'C'];
const CLASSES = CLASS_ORDER.filter(c => rows.some(r => r.class === c))
  .concat([...new Set(rows.map(r => r.class).filter(c => c && !CLASS_ORDER.includes(c)))]);
const LEANS = [...new Set(rows.map(r => r.lean).filter(Boolean))];
const HAS_OLD = rows.some(r => r.old);
const HAS_OLDER = rows.some(r => r.superseded_by);   // a newer version of some official model is on the board
const PART_LABELS = DATA.part_labels || {};
const HAS_WHERE = rows.some(r => r.hosted === true) && rows.some(r => r.hosted === false);
const STATE_VERSION = 2;                  // bump = every saved layout resets (2: Suite column)
const STORE_KEY = 'crucibleforge-board-layout';
const BRIDGE = 'dispatch:tool-state';
const MAX_STATE = 16 * 1024;

function defaults() {
  return {columns: COMPS.map(c => 'c:' + c.label).concat(['suite']), sortKey: 'rank', sortDir: 1,
          filters: {}, text: '', benched: false, latest: false, expanded: [], view: 'table',
          classes: [], leans: [], current: false, where: ''};
}
let S = defaults();
let expanded = new Set();
let filtersOpen = false, colsOpen = false, dragKey = null;
let openKey = null;                       // the column whose filter menu is open
const menuState = {};                     // per column: {op, value, to} — the menu's own state

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
// every visible column is filterable: the fixed ones (rank, model, scores,
// tok/s) plus the chosen right-hand ones
function filterKeys(cols) {
  return MAIN.map(c => c.key).concat(cols.filter(k => EXTRA_BY[k]));
}

// ------------------------------------------------------------ saved state
/** A saved per-column filter → a valid one. The old build wrote a plain
 *  number (the minimum score): it reads back as "greater than or equal", so
 *  a layout saved before the menus existed still means the same thing.
 *  An unknown operator, an unknown column or a broken value is dropped. */
function sanitizeFilter(k, raw) {
  const c = colOf(k);
  if (!c) return null;
  if (typeof raw === 'number') return (isFinite(raw) && raw >= 0) ? {op: 'ge', value: raw} : null;
  if (!raw || typeof raw !== 'object' || typeof raw.op !== 'string') return null;
  const op = raw.op;
  if (!opList(c).includes(op)) return null;
  if (op === 'empty') return {op: 'empty'};
  if (op === 'between') {
    const a = Number(raw.from), b = Number(raw.to);
    if (!isFinite(a) || !isFinite(b)) return null;
    return {op: 'between', from: a, to: b};
  }
  if (c.kind === 'num') return (typeof raw.value === 'number' && isFinite(raw.value)) ? {op: op, value: raw.value} : null;
  return (typeof raw.value === 'string') ? {op: op, value: raw.value.slice(0, 200)} : null;
}
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
      if (!fk.includes(k)) continue;                  // a column that is not shown: dropped
      const f = sanitizeFilter(k, raw.filters[k]);
      if (f) d.filters[k] = f;
    }
  }
  if (typeof raw.text === 'string') d.text = raw.text.slice(0, 200);
  d.benched = raw.benched === true;
  d.latest = raw.latest === true && HAS_OLDER;
  if (Array.isArray(raw.expanded)) d.expanded = raw.expanded.filter(l => typeof l === 'string' && LABELS.has(l));
  if (raw.view === 'table' || raw.view === 'cards') d.view = raw.view;
  // quick chips (optional keys: a layout saved before they existed still loads)
  const pick = (a, ok) => Array.isArray(a) ? a.filter(x => typeof x === 'string' && ok.includes(x)) : [];
  d.classes = pick(raw.classes, CLASSES);
  d.leans = pick(raw.leans, LEANS);
  d.current = raw.current === true && HAS_OLD;
  if ((raw.where === 'hosted' || raw.where === 'local') && HAS_WHERE) d.where = raw.where;
  return d;
}
function serialize() {
  return {version: STATE_VERSION, columns: S.columns.slice(), sortKey: S.sortKey, sortDir: S.sortDir,
          filters: Object.assign({}, S.filters), text: S.text, benched: S.benched,
          expanded: [...expanded], view: S.view, classes: S.classes.slice(), leans: S.leans.slice(),
          current: S.current, where: S.where, ...(S.latest ? {latest: true} : {})};
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
  if (openKey) closeMenu(false);
  $('filter').value = S.text;
  $('benched').checked = S.benched;
  if ($('latestOnly')) $('latestOnly').checked = S.latest;
  renderChips();
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
  if (S.latest && r.superseded_by) return false;
  if (S.classes.length && !S.classes.includes(r.class)) return false;
  if (S.leans.length && !S.leans.includes(r.lean)) return false;
  if (S.current && r.old) return false;
  if (S.where && r.hosted !== (S.where === 'hosted')) return false;
  if (S.text) {
    const f = S.text.toLowerCase();
    if (!(r.label.toLowerCase().includes(f) || String(r.model_id || '').toLowerCase().includes(f)
          || String(r.base || '').toLowerCase().includes(f)
          || String(r.provider || '').toLowerCase().includes(f))) return false;
  }
  for (const k in S.filters) {                     // AND across the columns
    if (!passesOp(r, k, S.filters[k])) return false;
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
  const groups = SIDES.map(x => x.key).map(s => {
    const inner = side(s);
    return inner ? '<div class="group wide"><h4>' + esc(SIDE_LABEL[s] || s) +
      ' components — click one to sort every row by it</h4><div class="comp-grid">' + inner + '</div></div>' : '';
  }).join('');
  const pts = r.points_cell ? '<div class="group wide"><h4>Points breakdown</h4><ul class="pts">' +
    r.points_cell.split('; ').map(x => '<li>' + esc(x) + '</li>').join('') + '</ul></div>' : '';
  return '<div class="detail">' + pts + groups +
    '<div class="group wide"><h4>Notes</h4><div class="meta-line">' + esc(r.notes || 'none') + '</div></div>' +
    '<div class="group"><h4>Run date</h4><div class="meta-line">' + esc(r.date || '–') + '</div></div>' +
    metaGroups(r) + '</div>';
}
// Phone card: headline numbers big, every component in two short columns; the
// bookkeeping sits behind the per-card Details toggle (shares the ▸ set).
// The cards have no headers, so every number carries its own ▾: the same
// filter menu as the table header, opened right there.
function card(r) {
  const open = expanded.has(r.label);
  const cls = (r.tier >= 2 ? ' fail-card' : (r.tier >= 1 ? ' warn-card' : '')) + (r.old ? ' old-card' : '');
  const head = SCORE_COLS.map(c => [c.key, c.label]).map(([k, t]) =>
    '<div class="hl' + (S.sortKey === k ? ' sorted' : '') + '"><span class="hl-lbl">' + t + '</span>' +
    '<span class="hl-val">' + sval(r, k) + (k === 'overall' ? '' : tierTag(r, k)) + '</span>' + fbtn(k, t) + '</div>').join('');
  const col = s => {
    const items = COMPS.filter(c => c.side === s).map(c => {
      const v = (r.components || {})[c.label];
      return '<div class="chip side-' + s + (S.sortKey === 'c:' + c.label ? ' sorted' : '') + '">' +
        '<span class="lbl">' + esc(c.label) + '</span><span class="val' + band(v) + '">' + num(v, 0) + '</span>' +
        fbtn('c:' + c.label, c.label) + '</div>';
    }).join('');
    return items ? '<div class="ccol"><h4>' + esc(SIDE_LABEL[s] || s) + '</h4>' + items + '</div>' : '';
  };
  return '<article class="card' + cls + '" data-label="' + esc(r.label) + '">' +
    '<div class="card-top"><span class="rank">#' + rankOf(r) + '</span><h2 class="name">' + esc(r.label) + oldTag(r) + '</h2></div>' +
    '<div class="hls">' + head + '</div>' +
    '<div class="sub">' + (r.class ? '<span>class ' + esc(r.class) + '</span>' : '') +
    (r.lean ? '<span>' + esc(r.lean) + '</span>' : '') +
    '<span' + (S.sortKey === 'tok_s' ? ' class="sorted"' : '') + '>' + num(r.tok_s, 1) + ' tok/s</span>' +
    (costText(r) ? '<span' + (S.sortKey === 'cost_usd' ? ' class="sorted"' : '') + ' title="Run cost of the Code and Chat tests">' + esc(costText(r)) + ' run cost</span>' : '') +
    '<span' + (S.sortKey === 'suite' ? ' class="sorted"' : '') + '>suite ' + esc(r.suite || '–') + '</span>' +
    '<span' + (S.sortKey === 'date' ? ' class="sorted"' : '') + '>' + esc(r.date || '–') + '</span></div>' +
    (r.notes ? '<div class="card-notes">' + esc(r.notes) + '</div>' : '') +
    '<div class="ccols">' + SIDES.map(x => col(x.key)).join('') + '</div>' +
    '<button class="card-toggle" type="button" aria-expanded="' + open + '" data-toggle="' + esc(r.label) + '">' +
    (open ? '▾ Hide details' : '▸ Details: judge, coverage, id') + '</button>' +
    (open ? '<div class="card-detail">' + metaGroups(r) + '</div>' : '') +
    '</article>';
}
// the ▾ in a header: opens that column's filter menu (the header label keeps
// its click-to-sort; the two never fight over the same click)
function fbtn(k, label) {
  return '<button type="button" class="fbtn' + (k in S.filters ? ' on' : '') + '" data-fbtn="' + esc(k) +
    '" aria-haspopup="true" aria-expanded="' + (openKey === k) + '" aria-label="Filter ' + esc(label) +
    '" title="Filter ' + esc(label) + '">▾</button>';
}
function th(k) {
  const c = colOf(k);
  const on = S.sortKey === k;
  const cls = c.side ? 'side-' + c.side : (MAIN_BY[k] ? 'main-col' : 'extra-col');
  return '<th data-k="' + esc(k) + '" scope="col" tabindex="0" class="' + cls + (k === 'label' ? ' label' : '') + '" aria-sort="' +
    (on ? (S.sortDir === 1 ? 'ascending' : 'descending') : 'none') + '"' +
    (c.side ? ' title="' + esc(SIDE_LABEL[c.side] || c.side) + ' component"' : '') + '>' + esc(c.label) +
    ' <span class="arrow">' + (on ? (S.sortDir === 1 ? '▲' : '▼') : '⇅') + '</span>' + fbtn(k, c.label) + '</th>';
}
// Run cost: $11.77 · ~$0.42 (estimated from tokens at list price) · $0.66* (some
// requests returned no usage data) · <$0.01 · $0.00 (exactly zero) · "–" for a model run locally
function costText(r) {
  if (!isNum(r.cost_usd)) return r.cost_partial ? '?*' : '';
  const v = Number(r.cost_usd);
  let t = v === 0 ? '$0.00' : v < 0.005 ? '<$0.01' : '$' + v.toLocaleString('en-US', {minimumFractionDigits: 2, maximumFractionDigits: 2});
  if (r.cost_basis === 'list' || r.cost_basis === 'mixed') t = '~' + t;
  if (r.cost_partial) t += '*';
  return t;
}
function cell(r, k) {
  const c = EXTRA_BY[k];
  if (k.startsWith('c:')) {
    const v = (r.components || {})[c.label];
    return '<td class="num comp-col side-' + c.side + band(v) + '">' + num(v, 0) + '</td>';
  }
  if (k === 'notes') return '<td class="notes">' + esc(r.notes || '') + '</td>';
  if (k === 'reason') return '<td class="num">' + sval(r, 'reason') + tierTag(r, 'reason') + '</td>';
  if (k === 'date') return '<td class="num">' + esc(r.date || '–') + '</td>';
  if (k === 'cost_usd') return '<td class="num">' + (costText(r) ? esc(costText(r)) : '<span class="muted">–</span>') + '</td>';
  return '<td class="meta-col">' + esc(r[k] || '–') + '</td>';
}
// suite 3.9: the tier a score reached (Chat/Code up to 200 = T1 + half of T2 and T3)
function tierTag(r, k) {
  const t = r.reached && (r.reached[k] || r.reached[k === 'coding' ? 'code' : k]);
  return t ? ' <span class="tier-tag" title="highest tier scored">T' + t + '</span>' : '';
}
// suite 3.9 headline: S on the rough scale, integer part as the main number,
// "± m" after it; the raw % stays in the tooltip (and the Raw % column)
function sval(r, k) {
  const s = r.s && r.s[k];
  if (!isNum(s)) return num(r[k], 1);
  const m = r.m && r.m[k];
  const t = s.toFixed(1).split('.');
  return '<span class="sv" title="raw ' + num(r[k], 1) + ' %"><b class="s-main">' + t[0] + '</b>.' + t[1] +
    (isNum(m) ? '<span class="pm"> ± ' + m.toFixed(1) + '</span>' : '') + '</span>';
}
function rankOf(r) { return esc(r.rank_label || String(r.rank)); }
function oldTag(r) {
  return (r.old ? ' <span class="old-tag" title="values from an older suite — re-run pending">old</span>' : '') +
    (r.superseded_by ? ' <span class="old-tag" title="A newer version is on the board: ' + esc(r.superseded_by) + '">older version</span>' : '') +
    maxTag(r);
}
// MAX: the model reached the top of every set in these parts, so the tests cannot tell it from the next model
// until a new pack exists (the name of each part comes from the board's part_labels)
function maxTag(r) {
  const ps = Array.isArray(r.maxed_parts) ? r.maxed_parts : [];
  if (!ps.length) return '';
  const names = ps.map(k => PART_LABELS[k] || k).join(', ');
  return ' <span class="max-tag" title="Reached the top of every set in: ' + esc(names) + '. A new pack is needed.">MAX</span>';
}
function dirText() {
  if (S.sortKey === 'rank') return S.sortDir === 1 ? '▲ Top first' : '▼ Bottom first';
  if (TEXT_COLS.has(S.sortKey)) return S.sortDir === 1 ? '▲ A → Z' : '▼ Z → A';
  return S.sortDir === -1 ? '▼ High first' : '▲ Low first';
}
function render() {
  const data = rows.filter(passes).sort(compare);
  const ncol = 1 + MAIN.length + S.columns.length;
  if (openKey && filterKeys(S.columns).indexOf(openKey) < 0) closeMenu(false);   // its column is gone
  renderFilters();                                   // the column launcher (which column is filtered)
  $('thead').innerHTML = '<tr><th scope="col" class="xcol" aria-label="details"></th>' +
    MAIN.map(c => th(c.key)).join('') + S.columns.map(th).join('') + '</tr>';
  $('tbody').innerHTML = data.map(r => {
    const open = expanded.has(r.label);
    const cls = (r.tier >= 2 ? ' fail-row' : (r.tier >= 1 ? ' warn-row' : '')) + (r.old ? ' old-row' : '');
    return '<tr class="data-row' + cls + '" data-label="' + esc(r.label) + '">' +
      '<td class="xcol"><button class="toggle" aria-expanded="' + open + '" aria-label="details" data-toggle="' + esc(r.label) + '">' + (open ? '▾' : '▸') + '</button></td>' +
      '<td class="num">' + rankOf(r) + '</td><td class="label">' + esc(r.label) + oldTag(r) + '</td>' +
      MAIN.slice(2).map(c => c.key === 'tok_s' ? '<td class="num">' + num(r.tok_s, 1) + '</td>'
        : c.kind === 'text' ? '<td class="meta-col">' + esc(r[c.key] || '–') + '</td>'
        : '<td class="num strong">' + sval(r, c.key) + (c.key === 'overall' ? '' : tierTag(r, c.key)) + '</td>').join('') +
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
  const ranked = data.filter(r => isNum(r.overall)).length;
  const hiddenOld = S.latest ? rows.filter(r => r.superseded_by).length : 0;
  $('count').innerHTML = data.length + ' of ' + rows.length + ' models · ' + ranked + ' ranked' +
    (hiddenOld ? ' · ' + hiddenOld + (hiddenOld === 1 ? ' older version hidden' : ' older versions hidden') : '') +
    '<span class="desk-only"> · click a column head (or Enter on it) to sort · the ▾ in a head filters that column · ▸ opens points, components, notes and judge</span>';
  syncChips();
  $('clearAll').disabled = !anyFilter();
}

// ------------------------------------------------------------ quick chips
// Class / Lean (several may be on: any of them), Current suite, Hosted | Local.
// None on = no restriction. Built once per layout; render() only syncs the flags.
function chipOn(id) {
  if (id.startsWith('class:')) return S.classes.includes(id.slice(6));
  if (id.startsWith('lean:')) return S.leans.includes(id.slice(5));
  if (id === 'current') return S.current;
  return S.where === id.slice(6);
}
function chipBtn(id, label, title) {
  return '<button type="button" class="chip-btn" data-chip="' + esc(id) + '" aria-pressed="' + chipOn(id) + '"' +
    (title ? ' title="' + esc(title) + '"' : '') + '>' + esc(label) + '</button>';
}
function renderChips() {
  const grp = (name, items) => items ? '<span class="chip-group"><span class="chip-lbl">' + name + '</span>' + items + '</span>' : '';
  $('chips').innerHTML =
    grp('Class', CLASSES.map(c => chipBtn('class:' + c, c)).join('')) +
    grp('Lean', LEANS.map(l => chipBtn('lean:' + l, l)).join('')) +
    grp('Show', (HAS_OLD ? chipBtn('current', 'Current suite', 'hide rows from an older suite') : '') +
      (HAS_WHERE ? chipBtn('where:hosted', 'Hosted API', 'models run through a paid API') +
        chipBtn('where:local', 'Local rig', 'models run on the rig') : ''));
}
function syncChips() {
  const box = $('chips');
  if (!box.querySelectorAll) return;
  box.querySelectorAll('[data-chip]').forEach(b => b.setAttribute('aria-pressed', String(chipOn(b.dataset.chip))));
}
function toggleIn(a, v) { const i = a.indexOf(v); if (i < 0) a.push(v); else a.splice(i, 1); }
function onChipClick(e) {
  const b = e.target && e.target.closest ? e.target.closest('[data-chip]') : null;
  if (!b) return;
  const id = b.dataset.chip;
  if (id.startsWith('class:')) toggleIn(S.classes, id.slice(6));
  else if (id.startsWith('lean:')) toggleIn(S.leans, id.slice(5));
  else if (id === 'current') S.current = !S.current;
  else S.where = S.where === id.slice(6) ? '' : id.slice(6);
  render(); save();
}
function anyFilter() {
  return !!(S.text || S.benched || S.latest || S.classes.length || S.leans.length || S.current || S.where ||
            Object.keys(S.filters).length);
}
function clearAll() {
  S.text = ''; S.benched = false; S.latest = false; S.classes = []; S.leans = []; S.current = false; S.where = ''; S.filters = {};
  $('filter').value = ''; $('benched').checked = false; if ($('latestOnly')) $('latestOnly').checked = false;
  render(); save();
  if (openKey) updateActive();
}

// ------------------------------------------------------------ column filters
// Every column carries its own filter menu (the ▾ in its header): sort up /
// down, the operators for that column's type, the minimum-score presets, a
// free number and a clear. One filter per column at a time; the filters of
// different columns compose with AND. The menu is one shared popup, placed
// next to the trigger that opened it.
const POINTS = rows.some(r => r.s);
function stepsFor(k) {
  if (!POINTS || !MAIN_BY[k] || MAIN_BY[k].kind !== 'num') return THRESHOLDS;
  return k === 'overall' ? [8, 10, 12, 14, 16, 18] : [4, 5, 6, 7, 8, 9];
}
// the presets are score steps: a rank or a token rate has no meaningful ones
function presetsFor(k) { return (k === 'rank' || k === 'tok_s') ? [] : stepsFor(k); }
function opList(c) { return c.kind === 'num' ? NUM_OPS : TEXT_OPS; }
function opName(op, num) { return (!num && OP_LABEL_TEXT[op]) ? OP_LABEL_TEXT[op] : (OP_LABEL[op] || op); }
function opText(op, num) { return OP_SYM[op] ? OP_SYM[op] + ' ' + opName(op, num) : opName(op, num); }
/** The value of a row for a column, as the filters see it (text is
 *  lower-cased, a missing value is null / ''). */
function filterValue(r, k) {
  if (k.startsWith('c:')) { const v = (r.components || {})[k.slice(2)]; return isNum(v) ? Number(v) : null; }
  const c = colOf(k);
  if (c && c.kind === 'text') { const v = r[k]; return v === null || v === undefined ? '' : String(v).toLowerCase(); }
  const v = r[k];
  return isNum(v) ? Number(v) : null;
}
function passesOp(r, k, f) {
  if (!f) return true;
  const v = filterValue(r, k);
  if (f.op === 'empty') return v === null || v === '';
  if (v === null || v === '') return false;      // a missing value fails every other operator
  if (f.op === 'between') return v >= f.from && v <= f.to;
  if (typeof v === 'number') {
    switch (f.op) {
      case 'lt': return v < f.value;
      case 'le': return v <= f.value;
      case 'gt': return v > f.value;
      case 'ge': return v >= f.value;
      case 'eq': return v === f.value;
      case 'ne': return v !== f.value;
    }
    return false;
  }
  const s = String(v), t = String(f.value).toLowerCase();
  switch (f.op) {
    case 'contains': return s.includes(t);
    case 'eq': return s === t;
    case 'not': return !s.includes(t);
    case 'starts': return s.startsWith(t);
  }
  return true;
}
// --- the menu ------------------------------------------------------------
function menuInit(k) {
  const c = colOf(k);
  const f = S.filters[k];
  const st = {op: (f && f.op) || (c.kind === 'num' ? 'ge' : 'contains'), value: '', to: ''};
  if (f) {
    if (f.op === 'between') { st.value = String(f.from); st.to = String(f.to); }
    else if (f.op !== 'empty') st.value = String(f.value);
  }
  menuState[k] = st;
  return st;
}
function activeText(k) {
  const f = S.filters[k];
  if (!f) return '';
  const c = colOf(k);
  if (f.op === 'empty') return c.label + ' is empty';
  if (f.op === 'between') return c.label + ' between ' + f.from + ' and ' + f.to;
  const desc = c.kind === 'num' ? (OP_SYM[f.op] || opName(f.op, true).toLowerCase()) : opName(f.op, false).toLowerCase();
  return c.label + ' ' + desc + ' ' + (c.kind === 'num' ? f.value : '"' + f.value + '"');
}
function menuHTML(k) {
  const c = colOf(k);
  const num = c.kind === 'num';
  const st = menuState[k] || menuInit(k);
  const dir = S.sortKey === k ? S.sortDir : 0;
  const item = (act, label, on) => '<button type="button" class="fm-item' + (on ? ' on' : '') +
    '" role="menuitem" data-act="' + act + '" data-k="' + esc(k) + '">' + esc(label) + '</button>';
  const opBtn = o => '<button type="button" class="fm-op' + (st.op === o ? ' on' : '') + '" role="menuitemradio"' +
    ' aria-checked="' + (st.op === o) + '" data-op="' + o + '" data-k="' + esc(k) + '" title="' +
    esc(opName(o, num)) + '">' + esc(opText(o, num)) + '</button>';
  const val = (st.value === undefined || st.value === null) ? '' : st.value;
  const fields = num
    ? '<label class="fm-field"><span>' + (st.op === 'between' ? 'From' : 'Value') + '</span>' +
      '<input type="text" inputmode="decimal" data-role="value" data-k="' + esc(k) + '" value="' + esc(val) +
      '" aria-label="' + esc(opName(st.op, num)) + ' for ' + esc(c.label) + '"></label>' +
      (st.op === 'between' ? '<label class="fm-field"><span>To</span><input type="text" inputmode="decimal" ' +
        'data-role="to" data-k="' + esc(k) + '" value="' + esc(st.to) + '" aria-label="upper bound for ' +
        esc(c.label) + '"></label>' : '')
    : '<label class="fm-field"><span>Text</span><input type="search" data-role="value" data-k="' + esc(k) +
      '" value="' + esc(val) + '" aria-label="value for ' + esc(c.label) + '"></label>';
  const steps = presetsFor(k);
  const presets = steps.length ? '<div class="fm-presets">' + steps.map(t =>
      '<button type="button" class="fm-preset" data-preset="' + t + '" data-k="' + esc(k) + '" title="minimum ' +
      t + '">≥ ' + t + '</button>').join('') + '</div>' : '';
  return '<div class="fm-head">' + esc(c.label) + '</div>' +
    item('asc', 'Sort ascending', dir === 1) + item('desc', 'Sort descending', dir === -1) +
    '<hr class="fm-sep"><div class="fm-ops" role="group" aria-label="' + esc(c.label) + ' operators">' +
    opList(c).map(opBtn).join('') + '</div>' + fields + presets +
    '<hr class="fm-sep">' + item('clear', 'Clear filter for this column');
}
function updateActive() {
  const a = $('fmenuActive');
  const t = openKey ? activeText(openKey) : '';
  a.textContent = t;
  a.classList.toggle('has', !!t);
}
function renderMenu() {
  if (!openKey) return;
  const k = openKey;
  const act = document.activeElement;
  let sel = null;
  if (act && act.getAttribute) {
    for (const a of ['data-op', 'data-preset', 'data-act']) {
      const v = act.getAttribute(a);
      if (v) { sel = '[' + a + '="' + v + '"]'; break; }
    }
  }
  const body = $('fmenuBody');
  body.innerHTML = menuHTML(k);
  updateActive();
  if (sel && body.querySelector) {
    const b = body.querySelector(sel);
    if (b && b.focus) b.focus();
  }
}
function menuItems() {
  const b = $('fmenuBody');
  return b && b.querySelectorAll ? Array.prototype.slice.call(b.querySelectorAll('button,input')) : [];
}
function focusTrigger(k) {
  if (!document.querySelector) return;
  const b = document.querySelector('[data-fbtn="' + String(k).replace(/"/g, '') + '"]');
  if (b && b.focus) b.focus();
}
// the trigger says whether its menu is open (the head is re-rendered on every
// change, so this only matters between two renders)
function setTrigger(k, expanded, trigger) {
  const v = String(!!expanded);
  if (trigger && trigger.setAttribute) trigger.setAttribute('aria-expanded', v);
  if (!document.querySelectorAll) return;
  const list = document.querySelectorAll('[data-fbtn="' + String(k).replace(/"/g, '') + '"]');
  for (let i = 0; i < list.length; i++) {
    if (list[i] && list[i].setAttribute) list[i].setAttribute('aria-expanded', v);
  }
}
function positionMenu(trigger) {
  const m = $('fmenu');
  if (!m.style) return;
  const r = trigger && trigger.getBoundingClientRect ? trigger.getBoundingClientRect() : null;
  if (!r) return;
  const vw = window.innerWidth || 0;
  const w = m.offsetWidth || 280;
  const left = vw ? Math.max(6, Math.min(r.left, vw - w - 6)) : Math.max(6, r.left);
  m.style.left = left + 'px';
  m.style.top = (r.bottom + 4) + 'px';
}
function openMenu(k, trigger) {
  if (!colOf(k)) return;
  closeMenu(false);
  openKey = k;
  menuInit(k);
  $('fmenu').classList.add('open');
  setTrigger(k, true, trigger);
  renderMenu();
  positionMenu(trigger);
  const items = menuItems();
  if (items.length && items[0].focus) items[0].focus();
}
function closeMenu(refocus) {
  const k = openKey;
  openKey = null;
  $('fmenu').classList.remove('open');
  $('fmenuBody').innerHTML = '';
  $('fmenuActive').textContent = '';
  $('fmenuActive').classList.remove('has');
  if (k) setTrigger(k, false);
  if (refocus && k) focusTrigger(k);
}
function toggleMenu(k, trigger) {
  if (openKey === k) { closeMenu(true); return; }
  openMenu(k, trigger);
}
// --- applying a filter ---------------------------------------------------
function makeFilter(k, st) {
  const c = colOf(k);
  const raw = String(st.value === undefined || st.value === null ? '' : st.value).trim();
  if (c.kind === 'num') {
    const n = Number(raw);
    if (raw === '' || !isFinite(n)) return null;
    return {op: st.op, value: n};
  }
  if (raw === '') return null;
  return {op: st.op, value: raw};
}
function setFilter(k, f) {
  if (f) S.filters[k] = f; else delete S.filters[k];
  render(); save();
  if (openKey === k) updateActive();
}
function clearFilter(k) {
  if (!k) return;
  delete S.filters[k];
  menuInit(k);
  render(); save();
}
function selectOp(k, op) {
  if (!k) return;
  const c = colOf(k);
  if (!c || !opList(c).includes(op)) return;
  const st = menuState[k];
  st.op = op;
  if (op === 'empty') { setFilter(k, {op: 'empty'}); renderMenu(); return; }
  if (op === 'between') {
    const a = Number(st.value), b = Number(st.to);
    if (String(st.value).trim() !== '' && String(st.to).trim() !== '' && isFinite(a) && isFinite(b))
      setFilter(k, {op: 'between', from: a, to: b});
    renderMenu();
    return;
  }
  const f = makeFilter(k, st);
  if (f) setFilter(k, f);
  renderMenu();
}
function applyPreset(k, t) {
  if (!k || !isFinite(t)) return;
  const st = menuState[k];
  st.op = 'ge'; st.value = String(t); st.to = '';
  setFilter(k, {op: 'ge', value: t});
  renderMenu();
}
function onMenuClick(e) {
  const t = e.target;
  if (!t || !t.closest) return;
  const op = t.closest('[data-op]');
  if (op) { selectOp(openKey, op.dataset.op); return; }
  const pre = t.closest('[data-preset]');
  if (pre) { applyPreset(openKey, Number(pre.dataset.preset)); return; }
  const act = t.closest('[data-act]');
  if (!act) return;
  const k = act.dataset.k || openKey;
  if (act.dataset.act === 'asc') { sortBy(k, 1); closeMenu(true); }
  else if (act.dataset.act === 'desc') { sortBy(k, -1); closeMenu(true); }
  else if (act.dataset.act === 'clear') { clearFilter(k); closeMenu(true); }
}
function onMenuInput(e) {
  const t = e.target;
  if (!t || !t.dataset || !t.dataset.role) return;
  const k = openKey;
  if (!k) return;
  const st = menuState[k];
  if (t.dataset.role === 'to') st.to = t.value; else st.value = t.value;
  if (st.op === 'between') {
    const a = Number(st.value), b = Number(st.to);
    if (String(st.value).trim() !== '' && String(st.to).trim() !== '' && isFinite(a) && isFinite(b))
      setFilter(k, {op: 'between', from: a, to: b});
    return;
  }
  if (st.op === 'empty') return;
  if (String(t.value).trim() === '') { setFilter(k, null); return; }
  const f = makeFilter(k, st);
  if (f) setFilter(k, f);
}
function onMenuKey(e) {
  if (!e || !e.key) return;
  if (e.key === 'Escape') { closeMenu(true); if (e.preventDefault) e.preventDefault(); return; }
  if (e.key !== 'ArrowDown' && e.key !== 'ArrowUp' && e.key !== 'Home' && e.key !== 'End') return;
  const items = menuItems();
  if (!items.length) return;
  const cur = items.indexOf(document.activeElement);
  let i = 0;
  if (e.key === 'ArrowDown') i = (cur + 1) % items.length;
  else if (e.key === 'ArrowUp') i = cur <= 0 ? items.length - 1 : cur - 1;
  else if (e.key === 'Home') i = 0;
  else i = items.length - 1;
  if (items[i] && items[i].focus) items[i].focus();
  if (e.preventDefault) e.preventDefault();
}
function onDocClick(e) {
  if (!openKey) return;
  const t = e.target;
  if (!t || !t.closest) return;
  // A click inside the menu re-renders it (renderMenu) before this bubbles up,
  // so the clicked button is already detached and closest('#fmenu') misses:
  // without this, picking an operator or a preset closed the menu.
  if (t.isConnected === false) return;
  if (t.closest('#fmenu') || t.closest('[data-fbtn]')) return;
  closeMenu(false);
}
// --- the filters row ------------------------------------------------------
// The old row of per-column minimums now lives in the header menus. The row
// stays as the launcher: one button per column, each opening its own menu
// (on the phone the cards have no headers, so this is the way in).
function renderFilters() {
  $('filtersRow').innerHTML = '<span class="flabel">Filter a column →</span>' +
    filterKeys(S.columns).map(k => {
      const c = colOf(k);
      return '<button type="button" class="col-fbtn' + (k in S.filters ? ' on' : '') + '" data-fbtn="' + esc(k) +
        '" aria-haspopup="true" aria-expanded="' + (openKey === k) + '" aria-label="Filter ' + esc(c.label) + '">' +
        esc(c.label) + ' <span class="sym" aria-hidden="true">▾</span></button>';
    }).join('') +
    '<button type="button" data-act="clear" title="Clear every column filter">Clear</button>';
}
function onFiltersRowClick(e) {
  const t = e.target;
  if (!t || !t.closest) return;
  const b = t.closest('[data-fbtn]');
  if (b) { toggleMenu(b.dataset.fbtn, b); return; }
  if (t.closest('[data-act="clear"]')) {
    S.filters = {};
    render(); save();
  }
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
function columnsChanged() { renderChooser(); render(); save(); }
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
/** Sort by a column. Without `dir` it toggles (the header label); the menu's
 *  "Sort ascending" / "Sort descending" pass the direction explicitly. */
function sortBy(k, dir) {
  if (!colOf(k)) return;
  if (dir === undefined) {
    if (S.sortKey === k) S.sortDir = -S.sortDir;
    else { S.sortKey = k; S.sortDir = defaultDir(k); }
  } else { S.sortKey = k; S.sortDir = dir === 1 ? 1 : -1; }
  render(); save();
}
function onListClick(e) {
  const b = e.target && e.target.closest ? e.target.closest('[data-fbtn]') : null;
  if (b) { toggleMenu(b.dataset.fbtn, b); return; }
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
function onHeadClick(e) {
  const b = e.target && e.target.closest ? e.target.closest('[data-fbtn]') : null;
  if (b) { toggleMenu(b.dataset.fbtn, b); return; }     // the ▾ opens the menu, it does not sort
  const h = e.target.closest('th[data-k]');
  if (h) sortBy(h.dataset.k);
}
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
  if ($('latestOnly')) $('latestOnly').addEventListener('change', e => { S.latest = !!e.target.checked; render(); save(); });
  $('chips').addEventListener('click', onChipClick);
  $('clearAll').addEventListener('click', clearAll);
  $('sortSel').addEventListener('change', e => { S.sortKey = e.target.value; S.sortDir = defaultDir(S.sortKey); render(); save(); });
  $('sortDir').addEventListener('click', () => { S.sortDir = -S.sortDir; render(); save(); });
  $('filtersBtn').addEventListener('click', () => { filtersOpen = !filtersOpen; render(); });
  $('viewTable').addEventListener('click', () => setView('table'));
  $('viewCards').addEventListener('click', () => setView('cards'));
  $('colsBtn').addEventListener('click', () => { colsOpen = !colsOpen; render(); });
  $('colsDone').addEventListener('click', () => { colsOpen = false; render(); });
  $('resetLayout').addEventListener('click', resetLayout);
  const fr = $('filtersRow');
  fr.addEventListener('click', onFiltersRowClick);
  const menu = $('fmenu');
  menu.addEventListener('click', onMenuClick);
  menu.addEventListener('input', onMenuInput);
  menu.addEventListener('keydown', onMenuKey);
  document.addEventListener('click', onDocClick);      // click outside the menu: it closes
  const cl = $('colsList');
  cl.addEventListener('change', onColsChange);
  cl.addEventListener('click', onColsClick);
  cl.addEventListener('dragstart', onDragStart);
  cl.addEventListener('dragover', onDragOver);
  cl.addEventListener('drop', onDrop);
  cl.addEventListener('dragend', onDragEnd);
  $('thead').addEventListener('click', onHeadClick);
  $('thead').addEventListener('keydown', e => {                 // sortable by keyboard: Enter / Space on a head
    const t = e.target;
    if (t && t.closest && t.closest('[data-fbtn]')) return;     // the ▾ handles its own keys
    if (e.key !== 'Enter' && e.key !== ' ') return;
    const h = e.target && e.target.closest ? e.target.closest('th[data-k]') : null;
    if (!h) return;
    e.preventDefault();
    const k = h.dataset.k;
    sortBy(k);
    const again = document.querySelector ? document.querySelector('th[data-k="' + k.replace(/"/g, '') + '"]') : null;
    if (again && again.focus) again.focus();                    // render() rebuilt the head: keep the keyboard place
  });
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
