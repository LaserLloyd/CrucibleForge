const rows = /*ROWS*/[];
let sortKey = 'rank', sortDir = 1, filter = '';
const expanded = new Set();
const NUMERIC = new Set(['rank', 'chat', 'coding', 'overall', 'tok_s']);
function esc(s) {
  return String(s).replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
}
function num(v, d) {
  return (v === null || v === undefined || isNaN(v)) ? '<span class="muted">–</span>' : Number(v).toFixed(d);
}
function key(r, k) {
  const v = r[k];
  if (NUMERIC.has(k)) return (v === null || v === undefined) ? null : Number(v);
  return v === null || v === undefined ? '' : String(v).toLowerCase();
}
function compare(a, b) {
  const ka = key(a, sortKey), kb = key(b, sortKey);
  if (ka === null && kb === null) return a.rank - b.rank;
  if (ka === null) return 1;           // missing values always sort last
  if (kb === null) return -1;
  if (ka < kb) return -sortDir;
  if (ka > kb) return sortDir;
  return a.rank - b.rank;
}
function detail(r) {
  const comps = Object.entries(r.components || {}).map(([k, v]) =>
    '<div class="comp"><div class="lbl">' + esc(k) + '</div><div class="val">' + num(v, 0) + '</div></div>').join('');
  return '<div class="detail"><div class="group"><h4>Model id</h4><div class="meta-line">' + esc(r.model_id || '–') +
    '</div></div><div class="group"><h4>Provider</h4><div class="meta-line">' + esc(r.provider || '–') +
    '</div></div><div class="group wide"><h4>Components (Chat: RP · NSFW · Story · Explicit peak · Willing · Steer — ' +
    'Coding: Programs · Tools · Instruct · Reason)</h4><div class="comp-grid">' + comps + '</div></div></div>';
}
function render() {
  const f = filter.toLowerCase();
  const data = rows.filter(r => !f || r.label.toLowerCase().includes(f) || (r.model_id || '').toLowerCase().includes(f));
  data.sort(compare);
  document.getElementById('tbody').innerHTML = data.map(r => {
    const open = expanded.has(r.label);
    const cls = r.tier >= 2 ? 'fail' : (r.tier >= 1 ? 'warn-row' : '');
    return '<tr class="data-row ' + cls + '" data-label="' + esc(r.label) + '">' +
      '<td><button class="toggle" aria-expanded="' + open + '" data-toggle="' + esc(r.label) + '">' + (open ? '▾' : '▸') + '</button></td>' +
      '<td class="num">' + r.rank + '</td><td class="label">' + esc(r.label) + '</td>' +
      '<td class="num strong">' + num(r.chat, 1) + '</td><td class="num strong">' + num(r.coding, 1) + '</td>' +
      '<td class="num">' + num(r.overall, 1) + '</td><td class="num">' + num(r.tok_s, 1) + '</td>' +
      '<td>' + esc(r.date || '–') + '</td><td class="notes">' + esc(r.notes || '') + '</td></tr>' +
      (open ? '<tr class="detail-row"><td colspan="9">' + detail(r) + '</td></tr>' : '');
  }).join('');
  document.querySelectorAll('th[data-k]').forEach(th => {
    const a = th.querySelector('.arrow');
    a.textContent = th.dataset.k === sortKey ? (sortDir === 1 ? '▲' : '▼') : '⇅';
  });
  document.getElementById('count').textContent = data.length + ' of ' + rows.length +
    ' models · click a column to sort · click ▸ for components';
}
function init() {
  document.getElementById('filter').addEventListener('input', e => { filter = e.target.value; render(); });
  document.getElementById('expandAll').addEventListener('click', () => { rows.forEach(r => expanded.add(r.label)); render(); });
  document.getElementById('collapseAll').addEventListener('click', () => { expanded.clear(); render(); });
  document.querySelectorAll('th[data-k]').forEach(th => th.addEventListener('click', () => {
    const k = th.dataset.k;
    if (sortKey === k) sortDir = -sortDir;
    else { sortKey = k; sortDir = (k === 'rank' || k === 'label' || k === 'notes') ? 1 : -1; }
    render();
  }));
  document.getElementById('tbody').addEventListener('click', e => {
    const t = e.target.closest('[data-toggle]');
    if (!t) return;
    const l = t.dataset.toggle;
    if (expanded.has(l)) expanded.delete(l); else expanded.add(l);
    render();
  });
  render();
}
if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init); else init();
