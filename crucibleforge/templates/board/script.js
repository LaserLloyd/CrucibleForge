
const rows = ROWS_PLACEHOLDER;
let sortKey='total', sortDir=-1, filter='', onRigOnly=false, benchedOnly=false, expanded=new Set();
const COMP_LABELS = COMP_LABELS_PLACEHOLDER;
const COMP_KEYS = COMP_KEYS_PLACEHOLDER;
const componentFilters = {};
COMP_KEYS.forEach(k => componentFilters[k] = '');
function esc(s){return String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}
function cellHtml(s){
  s=String(s);
  if(s.indexOf('\u26a0\ufe0f')>=0)return '<span class="warn">'+esc(s)+'</span>';
  if(s.indexOf('\u274c')>=0)return '<span class="fail">'+esc(s)+'</span>';
  if(s==='\u2013'||s==='-')return '<span class="muted">\u2013</span>';
  return esc(s);
}
function getKey(r,k){
  if(k==='total') return r._t===null?-Infinity:r._t;
  if(k==='chat')  return r._c===null?-Infinity:r._c;
  if(k==='code')  return r._k===null?-Infinity:r._k;
  if(k==='date') return r.date||'';
  if(k==='ctx')  return r.ctx||0;
  if(COMP_LABELS[k]) return (r._comps[k]===null||r._comps[k]===undefined)?-Infinity:r._comps[k];
  return (r[k]||'').toLowerCase();
}
function passesFilters(r){
  for(const k of COMP_KEYS){
    const v = componentFilters[k];
    if(!v || v==='any' || v==='') continue;
    const threshold = parseInt(String(v).replace(/[^\d]/g,''),10);
    const score = r._comps[k];
    if(score===null||score===undefined||isNaN(score)) return false;
    if(v.indexOf('\u2265')>=0){ if(score<threshold) return false; }
    else if(v==='100'){ if(score<100) return false; }
  }
  return true;
}
function buildDetail(r){
  const compsHtml = Object.keys(COMP_LABELS).map(k=>{
    const v = r.comps[k]||'\u2013';
    const cls = (v==='\u2013'||v==='-')?'muted':'';
    return '<div class="comp" data-sort="'+k+'" title="Click to sort all rows by '+COMP_LABELS[k]+'"><div class="lbl">'+COMP_LABELS[k]+'</div><div class="val '+cls+'">'+esc(v)+'</div></div>';
  }).join('');
  return '<div class="detail">'
    + '<div class="group"><h4>Model ID</h4><div class="meta-line">'+esc(r.model_id||'\u2013')+'</div></div>'
    + '<div class="group"><h4>Judge</h4><div class="meta-line">'+cellHtml(r.judge||'\u2013')+'</div></div>'
    + '<div class="group"><h4>Coverage</h4><div class="meta-line">'+cellHtml(r.coverage||'\u2013')+'</div></div>'
    + '<div class="group"><h4>Speed</h4><div class="val">'+cellHtml(r.tok_s||'\u2013')+'</div><div class="meta-line">tok/s</div></div>'
    + '<div class="group" style="grid-column:1/-1"><h4>Components \u2014 click to sort all rows by element</h4><div class="comp-grid">'+compsHtml+'</div><div class="sort-hint">Chat (5): RP, NSFW, Explicit peak, Willing, Steer. Code (4): Code, Tools, Instruct, Reason.</div></div>'
    + '</div>';
}
function apply(){
  let data = rows.slice();
  if(filter){const f=filter.toLowerCase();data=data.filter(r=>r.label.toLowerCase().indexOf(f)>=0||(r.provider||'').toLowerCase().indexOf(f)>=0);}
  if(onRigOnly) data = data.filter(r=>r.on_rig);
  if(benchedOnly) data = data.filter(r=>r._t!==null);
  data = data.filter(passesFilters);
  data.sort((a,b)=>{const ka=getKey(a,sortKey),kb=getKey(b,sortKey);if(ka<kb)return -1*sortDir;if(ka>kb)return 1*sortDir;return 0;});
  document.getElementById('tbody').innerHTML = data.map(r=>{
    const cls = !r.enabled ? 'fail' : (r._t===null ? 'muted' : '');
    const isOpen = expanded.has(r.label);
    const arrow = isOpen ? '\u25be' : '\u25b8';
    return '<tr class="data-row '+cls+'" data-label="'+esc(r.label)+'"><td><button class="toggle" data-toggle="'+esc(r.label)+'">'+arrow+'</button></td><td>'+esc(r.label)+'</td><td>'+esc(r.provider)+'</td><td>'+(r.date||'<span class=muted>\u2013</span>')+'</td><td class="num">'+(r.ctx||'')+'</td><td class="num">'+cellHtml(r.total)+'</td><td class="num">'+cellHtml(r.chat)+'</td><td class="num">'+cellHtml(r.code)+'</td><td>'+cellHtml(r.coverage)+'</td><td class="num">'+cellHtml(r.tok_s)+'</td><td>'+cellHtml(r.judge)+'</td></tr>'
      +(isOpen?'<tr class="detail-row"><td colspan="10">'+buildDetail(r)+'</td></tr>':'');
  }).join('');
  document.querySelectorAll('th').forEach(th=>{
    const k=th.dataset.k;const a=th.querySelector('.arrow');
    if(k===sortKey)a.textContent=sortDir===1?'\u25b2':'\u25bc';
    else a.textContent='\u21c5';
  });
  // Update filter chip styling
  document.querySelectorAll('.comp-filter').forEach(el=>{
    const k = el.dataset.k;
    el.classList.toggle('is-active', !!componentFilters[k]);
  });
}
document.addEventListener('DOMContentLoaded',()=>{
  document.getElementById('filter').addEventListener('input',e=>{filter=e.target.value;apply();});
  document.getElementById('onRig').addEventListener('change',e=>{onRigOnly=e.target.checked;apply();});
  document.getElementById('benched').addEventListener('change',e=>{benchedOnly=e.target.checked;apply();});
  document.getElementById('expandAll').addEventListener('click',()=>{rows.forEach(r=>expanded.add(r.label));apply();});
  document.getElementById('collapseAll').addEventListener('click',()=>{expanded.clear();apply();});
  document.getElementById('clearFilters').addEventListener('click',()=>{
    COMP_KEYS.forEach(k=>componentFilters[k]='');
    document.querySelectorAll('.comp-filter select').forEach(sel=>sel.value='');
    apply();
  });
  document.querySelectorAll('.comp-filter select').forEach(sel=>{
    sel.addEventListener('change',e=>{
      const k = sel.closest('.comp-filter').dataset.k;
      componentFilters[k] = sel.value;
      apply();
    });
  });
  document.querySelectorAll('th[data-k]').forEach(th=>th.addEventListener('click',()=>{
    const k=th.dataset.k;if(sortKey===k)sortDir=-sortDir;else{sortKey=k;sortDir=(k==='label'||k==='provider')?1:-1;}
    apply();
  }));
  document.getElementById('tbody').addEventListener('click',e=>{
    const t = e.target.closest('[data-toggle]');
    if(t){
      const lbl = t.dataset.toggle;
      if(expanded.has(lbl)) expanded.delete(lbl); else expanded.add(lbl);
      apply();
      return;
    }
    const c = e.target.closest('[data-sort]');
    if(c){
      const k = c.dataset.sort;
      if(sortKey===k) sortDir=-sortDir; else {sortKey=k;sortDir=-1;}
      apply();
      window.scrollTo({top:0,behavior:'smooth'});
    }
  });
  apply();
});
