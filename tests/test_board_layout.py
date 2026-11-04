"""The board's customizable layout (2026-09-26): fixed main columns
``# · Model · Chat · Coding · Overall · tok/s``, a ⚙ Columns chooser for the
right-hand columns (components + run info), per-column ≥ filters for the
chosen numeric columns, a phone Table | Cards toggle, and the whole layout
saved per viewer — through DisPatch's tool-state bridge when framed
(``{type:'dispatch:tool-state', op:'get'|'set'}``), else localStorage.

``run_board`` executes the page's real ``script.js`` under node against a
tiny fake DOM (ids, innerHTML, delegated events) and a fake parent window;
the other board tests reuse it."""
import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from crucibleforge.templates.board import CSS, render_html

COMPS = [{"label": "RP", "side": "chat"}, {"label": "NSFW", "side": "chat"},
         {"label": "Programs", "side": "coding"}, {"label": "Tools", "side": "coding"}]
MAIN = ["rank", "label", "chat", "coding", "overall", "tok_s"]
DEFAULT_EXTRA = ["c:RP", "c:NSFW", "c:Programs", "c:Tools"]

needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")


def rows():
    return [{"rank": i + 1, "label": f"m{i}", "model_id": "x", "provider": f"p{i}",
             "chat": ch, "coding": c, "overall": c, "tok_s": t, "date": f"2026-09-2{i}",
             "notes": n, "tier": tier, "judge": "j", "coverage": "full",
             "components": {"RP": rp, "NSFW": ns, "Programs": c, "Tools": tl}}
            for i, (ch, c, t, rp, ns, tl, n, tier) in enumerate([
                (90.0, 50.0, 9.0, 95, 80, 60, "", 0),
                (None, 80.0, 100.0, None, 20, 90, "FAILED: http://192.0.2.10:1234/v1 down", 2),
                (70.0, 20.0, 30.0, 40, 75, 10, "partial", 1)])]


def board(**kw):
    return render_html(kw.pop("rows", None) or rows(), "sub", components=kw.pop("components", COMPS),
                       footer=["f"], **kw)


HARNESS = r"""
const els = {};
function mk(id) {
  const e = {id, innerHTML: '', textContent: '', value: '', checked: false, handlers: {}, attrs: {}, cls: new Set(),
    addEventListener(ev, fn) { (this.handlers[ev] = this.handlers[ev] || []).push(fn); },
    fire(ev, x) { (this.handlers[ev] || []).forEach(f => f(x || {})); },
    setAttribute(k, v) { this.attrs[k] = String(v); }, getAttribute(k) { return this.attrs[k]; },
    querySelectorAll() { return this.id === 'fmenuBody' ? menuItems(this.innerHTML) : []; },
    querySelector(sel) { const l = this.querySelectorAll(sel); return l.length ? l[0] : null; }};
  e.classList = {toggle(c, on) { on ? e.cls.add(c) : e.cls.delete(c); }, add(c) { e.cls.add(c); },
    remove(c) { e.cls.delete(c); }, contains(c) { return e.cls.has(c); }};
  return e;
}
// the menu body is a string in this fake DOM: make its buttons focusable
function menuItems(html) {
  const out = [];
  const re = /<(?:button|input)([^>]*)>/g;
  let m;
  while ((m = re.exec(html))) {
    const ds = {};
    const dre = /data-(op|act|preset|role)="([^"]*)"/g;
    let a;
    while ((a = dre.exec(m[1]))) ds[a[1]] = a[2];
    out.push({dataset: ds, getAttribute(n) { return ds[n.replace('data-', '')]; },
      focus() { focused = 'menu:' + (ds.op || ds.act || ds.preset || ds.role || 'item'); }});
  }
  return out;
}
function el(id) { return els[id] || (els[id] = mk(id)); }
el('board-data').textContent = __BLOB__;
const body = mk('body');
const OPT = __OPT__;
const store = Object.assign({}, OPT.store || {});
const parentMsgs = [];
const listeners = {};
const parentWin = {postMessage(m, t) { parentMsgs.push([JSON.parse(JSON.stringify(m)), t]); }};
global.window = {
  addEventListener(ev, fn) { (listeners[ev] = listeners[ev] || []).push(fn); },
  get localStorage() {
    if (OPT.storageThrows) throw new Error('SecurityError: sandboxed');
    return {getItem: k => (k in store ? store[k] : null), setItem: (k, v) => { store[k] = String(v); }};
  },
};
window.parent = OPT.framed ? parentWin : window;
const docListeners = {};
const document = {readyState: 'complete', getElementById: el, body,
  addEventListener(t, f) { (docListeners[t] = docListeners[t] || []).push(f); },
  querySelectorAll: sel => { const m = /\[data-fbtn="([^"]+)"\]/.exec(sel); return (m && trigs[m[1]]) ? [trigs[m[1]]] : []; },
  querySelector: sel => { const l = document.querySelectorAll(sel); return l.length ? l[0] : null; }};
global.document = document;
function deliver(data, source) {
  (listeners.message || []).forEach(f => f({source: source === undefined ? window.parent : source, data}));
}
const wait = ms => new Promise(r => setTimeout(r, ms));
const keys = (id, attr) => [...el(id).innerHTML.matchAll(new RegExp(attr + '="([^"]+)"', 'g'))].map(m => m[1]);
const heads = () => keys('thead', 'data-k');
const filterRow = () => keys('filtersRow', 'data-fbtn');
const chooser = () => [...el('colsList').innerHTML.matchAll(/<li class="col-item( on)?"[^>]*data-col="([^"]+)"/g)].map(m => (m[1] ? '+' : '-') + m[2]);
const order = () => [...el('tbody').innerHTML.matchAll(/<tr class="data-row[^"]*" data-label="([^"]+)"/g)].map(m => m[1]).join(',');
const cardOrder = () => [...el('cards').innerHTML.matchAll(/<article class="card[^"]*" data-label="([^"]+)"/g)].map(m => m[1]).join(',');
const lastSet = () => { const s = parentMsgs.filter(([m]) => m.op === 'set'); return s.length ? s[s.length - 1][0].state : null; };
const saved = () => (store['crucibleforge-board-layout'] ? JSON.parse(store['crucibleforge-board-layout']) : null);
// delegated-event targets
const th = k => ({closest: s => (s === 'th[data-k]' ? {dataset: {k}} : null)});
// --- the per-column filter menu (the ▾ in a header) ---
const trigs = {};
let focused = null;
const trig = k => { const o = {dataset: {fbtn: k}, attrs: {},
  setAttribute(n, v) { o.attrs[n] = String(v); }, getAttribute(n) { return o.attrs[n]; },
  closest: s => (s === '[data-fbtn]' ? o : null),
  getBoundingClientRect: () => ({left: 0, top: 0, right: 0, bottom: 0, width: 0, height: 0}),
  focus() { focused = k; }};
  trigs[k] = o; return o; };
// open the menu of a column (clicking the same ▾ twice closes it: undo that)
function openMenu(k) {
  const cur = (menuHtml().match(/data-k="([^"]+)"/) || [])[1];
  if (cur === k) el('thead').fire('click', {target: trig(k)});
  const t = trig(k);
  el('thead').fire('click', {target: t});
  return t;
}
function menuClick(sel, ds) {
  // the browser order: the menu's own handler runs (and re-renders the menu,
  // detaching the clicked button), then the click bubbles to the document
  const target = {dataset: ds || {}, isConnected: false,
    closest: s => (s === sel ? {dataset: ds || {}} : null)};
  el('fmenu').fire('click', {target});
  (docListeners.click || []).forEach(f => f({target}));
}
function menuInput(role, val) {
  el('fmenu').fire('input', {target: {dataset: {role}, value: String(val)}});
}
const menuHtml = () => el('fmenuBody').innerHTML;
const menuOpen = () => el('fmenu').cls.has('open');
const activeLine = () => el('fmenuActive').textContent;
function pickFilter(k, v) { openMenu(k); menuClick('[data-op]', {op: 'ge'}); menuInput('value', v); }
function typeFilter(k, v) { openMenu(k); menuInput('value', v); }
function tick(k, on) { el('colsList').fire('change', {target: {type: 'checkbox', checked: on, dataset: {col: k}}}); }
function move(k, d) { el('colsList').fire('click', {target: {closest: s => (s === '[data-move]' ? {dataset: {col: k, move: String(d)}, disabled: false} : null)}}); }
function drag(k, before) {
  const li = key => ({closest: s => (s === 'li[data-col]' ? {dataset: {col: key}, classList: {add() {}}} : null)});
  const dt = {setData() {}, effectAllowed: '', dropEffect: ''};
  el('colsList').fire('dragstart', {target: li(k), dataTransfer: dt});
  el('colsList').fire('dragover', {target: li(before), dataTransfer: dt, preventDefault() {}});
  el('colsList').fire('drop', {target: li(before), dataTransfer: dt, preventDefault() {}});
}
"""


def run_board(html, body, *, framed=False, storage_throws=False, store=None):
    """Run the page's script.js under node with the fake DOM, then ``body``
    (inside an async function; it must fill ``out``). Returns ``out``."""
    blob = html.split('<script type="application/json" id="board-data">', 1)[1].split("</script>", 1)[0]
    script = html.rsplit("<script>", 1)[1].rsplit("</script>", 1)[0]
    opt = {"framed": framed, "storageThrows": storage_throws, "store": store or {}}
    js = (HARNESS.replace("__BLOB__", json.dumps(blob)).replace("__OPT__", json.dumps(opt))
          + script + "\n(async () => { const out = {};\n" + body
          + "\nconsole.log(JSON.stringify(out)); })().catch(e => { console.error(e.stack || e); process.exit(3); });\n")
    # a file, not `node -e`: the page is ~100 KB and Windows caps a command line at 32 KB
    with tempfile.TemporaryDirectory() as td:
        f = Path(td) / "board_run.js"
        f.write_text(js, encoding="utf-8")
        res = subprocess.run(["node", str(f)], capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert res.returncode == 0, res.stderr
    return json.loads(res.stdout.strip().splitlines()[-1])


# ------------------------------------------------------------ static page

def test_page_has_chooser_reset_view_toggle_and_column_specs():
    html = board()
    for needle in ('id="colsBtn"', 'aria-controls="colsPanel"', 'id="colsPanel"', 'id="colsList"',
                   'id="colsDone"', 'id="resetLayout"', 'id="viewTable"', 'id="viewCards"',
                   '<thead id="thead"></thead>', '<tbody id="tbody"></tbody>', 'id="filtersRow"',
                   '<body data-view="table">'):
        assert needle in html, needle
    data = json.loads(html.split('id="board-data">', 1)[1].split("</script>", 1)[0])
    assert [c["key"] for c in data["main"]] == MAIN
    extra = [c["key"] for c in data["extra"]]
    assert extra == DEFAULT_EXTRA + ["suite", "date", "notes", "judge", "coverage", "provider", "room_cell", "reason", "tiers_cell",
                             "raw_cell", "vs_above", "points_cell"]
    assert {c["kind"] for c in data["extra"] if c["key"].startswith("c:")} == {"num"}
    assert data["thresholds"] == [10, 30, 50, 70, 90, 100]


def test_page_stays_self_contained_small_and_unbreakable():
    evil = [{"label": "RP</script><script>alert(1)</script>", "side": "chat"}]
    r = rows()
    for x in r:
        x["components"] = {evil[0]["label"]: 50}
    html = render_html(r, "sub http://x.example/y", components=evil,
                       footer=["see https://h/i on box.tail0.ts.net:8700"])
    assert html.count("</script>") == 2                  # the data block and the script, nothing else
    for leak in ("192.0.2.10", "ts.net", "http://", "https://", "@import", "<link", "fetch("):
        assert leak not in html, leak
    assert len(board().encode()) < 120_000


# ------------------------------------------------------------ behaviour (node)

@needs_node
def test_default_layout_main_then_components_and_filters():
    out = run_board(board(), r"""
      out.heads = heads(); out.filters = filterRow(); out.chooser = chooser(); out.order = order();
      out.view = body.getAttribute('data-view'); out.pressed = el('viewTable').getAttribute('aria-pressed');
    """)
    assert out["heads"] == MAIN + DEFAULT_EXTRA + ["suite"]     # Suite shown by default (3.8.0)
    assert out["filters"] == MAIN + DEFAULT_EXTRA + ["suite"]   # every column is filterable
    assert out["chooser"] == ["+" + k for k in DEFAULT_EXTRA] + ["+suite", "-date", "-notes", "-judge", "-coverage", "-provider", "-room_cell", "-reason", "-tiers_cell", "-raw_cell", "-vs_above", "-points_cell"]
    assert out["order"] == "m0,m1,m2" and out["view"] == "table" and out["pressed"] == "true"


@needs_node
def test_chooser_add_remove_reorder_drag_and_filters_follow_the_columns():
    out = run_board(board(), r"""
      pickFilter('c:NSFW', 70); el('thead').fire('click', {target: th('c:NSFW')});
      out.sortedBy = el('thead').innerHTML.includes('data-k="c:NSFW" scope="col" tabindex="0" class="side-chat" aria-sort="descending"');
      tick('c:NSFW', false);                         // remove NSFW: its filter and its sort go too
      tick('suite', false);                          // (3.8.0 default column) out of the way
      out.afterRemove = heads().slice(6); out.filters1 = filterRow(); out.order1 = order();
      tick('notes', true); tick('date', true);        // add Notes, Run date (text: no ≥ filter)
      move('notes', -1); move('notes', -1);            // Notes two places left
      drag('c:Tools', 'c:RP');                         // Tools before RP
      drag('judge', 'c:Programs');                     // dragging an unchosen column in chooses it
      out.heads = heads().slice(6); out.filters2 = filterRow(); out.chooser = chooser();
    """)
    assert out["sortedBy"] is True
    assert out["afterRemove"] == ["c:RP", "c:Programs", "c:Tools"]
    assert out["filters1"] == MAIN + ["c:RP", "c:Programs", "c:Tools"]
    assert out["order1"] == "m0,m1,m2"               # NSFW >= 70 filter dropped with the column
    assert out["heads"] == ["c:Tools", "c:RP", "notes", "judge", "c:Programs", "date"]
    assert out["filters2"] == MAIN + ["c:Tools", "c:RP", "notes", "judge", "c:Programs", "date"]
    assert out["chooser"][:6] == ["+c:Tools", "+c:RP", "+notes", "+judge", "+c:Programs", "+date"]
    assert out["chooser"][6:] == ["-c:NSFW", "-suite", "-coverage", "-provider", "-room_cell", "-reason", "-tiers_cell", "-raw_cell", "-vs_above", "-points_cell"]


@needs_node
def test_threshold_and_free_number_filters():
    out = run_board(board(), r"""
      pickFilter('c:Tools', 50); out.t50 = order();
      typeFilter('coding', 65); out.c65 = order(); out.active = activeLine();
      typeFilter('coding', 50); out.c50 = order();
      typeFilter('coding', ''); pickFilter('c:Tools', ''); out.none = order();
      pickFilter('chat', 30); out.chat = order();                  // a missing Chat fails the filter
      el('filtersRow').fire('click', {target: {closest: s => (s === '[data-act="clear"]' ? {} : null)}});
      out.cleared = order(); out.btn = el('filtersBtn').textContent;
    """)
    assert out["t50"] == "m0,m1"
    assert out["c65"] == "m1" and out["active"] == "Coding ≥ 65"
    assert out["c50"] == "m0,m1" and out["none"] == "m0,m1,m2"
    assert out["chat"] == "m0,m2"
    assert out["cleared"] == "m0,m1,m2" and out["btn"].endswith("Filters")


@needs_node
def test_state_round_trip_through_a_fake_dispatch_parent():
    """Framed + sandboxed (localStorage throws): `get` on load, the first
    answer applied, later answers (acks) ignored, `set` after each change."""
    saved_state = {"version": 2, "columns": ["notes", "c:RP", "bogus", "c:RP"], "sortKey": "c:RP", "sortDir": -1,
                   "filters": {"c:RP": 50, "c:Programs": 70, "chat": 10}, "text": "", "benched": True,
                   "expanded": ["m0", "ghost"], "view": "cards"}
    out = run_board(board(), r"""
      out.first = parentMsgs.map(([m, t]) => [m.type, m.op, t]);
      deliver({type: 'dispatch:tool-state', state: null}, {});          // a foreign window: ignored
      deliver({type: 'dispatch:tool-state', state: """ + json.dumps(saved_state) + r"""});
      out.heads = heads().slice(6); out.filters = filterRow(); out.order = order(); out.view = body.getAttribute('data-view');
      out.expanded = /detail-row/.test(el('tbody').innerHTML); out.benched = el('benched').checked;
      out.filtersOpen = el('filtersRow').cls.has('open');
      deliver({type: 'dispatch:tool-state', state: {version: 2, columns: []}});   // an ack: ignored
      out.stillHeads = heads().slice(6);
      el('viewTable').fire('click'); el('thead').fire('click', {target: th('tok_s')});
      out.beforeDebounce = parentMsgs.filter(([m]) => m.op === 'set').length;
      await wait(400);
      out.sets = parentMsgs.filter(([m]) => m.op === 'set').map(([, t]) => t);
      out.state = lastSet();
    """, framed=True, storage_throws=True)
    assert out["first"] == [["dispatch:tool-state", "get", "*"]]
    assert out["heads"] == ["notes", "c:RP"]                     # unknown + duplicate dropped
    assert out["filters"] == MAIN + ["notes", "c:RP"]  # Programs not shown -> its filter dropped
    assert out["order"] == "m0"                                  # benched + chat>=10 + RP>=50
    assert out["view"] == "cards" and out["expanded"] and out["benched"] and out["filtersOpen"]
    assert out["stillHeads"] == ["notes", "c:RP"]
    assert out["beforeDebounce"] == 0 and out["sets"] == ["*"]    # one debounced set
    assert out["state"] == {"version": 2, "columns": ["notes", "c:RP"], "sortKey": "tok_s", "sortDir": -1,
                            "filters": {"c:RP": {"op": "ge", "value": 50},
                                        "chat": {"op": "ge", "value": 10}},
                            "text": "", "benched": True,
                            "expanded": ["m0"], "view": "table",
                            "classes": [], "leans": [], "current": False, "where": ""}


@needs_node
def test_localstorage_when_opened_directly_and_version_bump_resets():
    st = {"version": 2, "columns": ["c:Tools", "date"], "sortKey": "c:Tools", "sortDir": -1,
          "filters": {"c:Tools": 50}, "text": "m", "benched": False, "expanded": [], "view": "table"}
    out = run_board(board(), r"""
      out.msgs = parentMsgs.length; out.heads = heads().slice(6); out.order = order(); out.text = el('filter').value;
      el('filter').fire('input', {target: {value: 'm1'}}); await wait(400); out.saved = saved();
      el('resetLayout').fire('click'); out.reset = saved(); out.resetHeads = heads().slice(6);
    """, store={"crucibleforge-board-layout": json.dumps(st)})
    assert out["msgs"] == 0                                       # not framed: no bridge traffic
    assert out["heads"] == ["c:Tools", "date"] and out["order"] == "m1,m0" and out["text"] == "m"
    assert out["saved"]["text"] == "m1" and out["saved"]["columns"] == ["c:Tools", "date"]
    assert out["reset"]["columns"] == DEFAULT_EXTRA + ["suite"] and out["reset"]["filters"] == {}
    assert out["resetHeads"] == DEFAULT_EXTRA + ["suite"]
    old = dict(st, version=99)
    out = run_board(board(), "out.heads = heads().slice(6); out.order = order();",
                    store={"crucibleforge-board-layout": json.dumps(old)})
    assert out["heads"] == DEFAULT_EXTRA + ["suite"] and out["order"] == "m0,m1,m2"
    out = run_board(board(), "out.heads = heads().slice(6);", store={"crucibleforge-board-layout": "{not json"})
    assert out["heads"] == DEFAULT_EXTRA + ["suite"]


@needs_node
def test_a_renamed_component_is_dropped_from_a_saved_layout():
    st = {"version": 2, "columns": ["c:Gone", "c:Tools"], "sortKey": "c:Gone", "sortDir": -1,
          "filters": {"c:Gone": 90}, "text": "", "benched": False, "expanded": [], "view": "table"}
    out = run_board(board(), "out.heads = heads().slice(6); out.filters = filterRow(); out.order = order();",
                    store={"crucibleforge-board-layout": json.dumps(st)})
    assert out["heads"] == ["c:Tools"] and out["filters"] == MAIN + ["c:Tools"]
    assert out["order"] == "m0,m1,m2"                             # sort fell back to rank


@needs_node
def test_phone_table_cards_toggle_is_persisted():
    out = run_board(board(), r"""
      el('viewCards').fire('click'); out.v1 = body.getAttribute('data-view');
      out.p = [el('viewTable').getAttribute('aria-pressed'), el('viewCards').getAttribute('aria-pressed')];
      await wait(400); out.saved = saved().view;
      out.cards = cardOrder(); out.table = order();
    """)
    assert out["v1"] == "cards" and out["p"] == ["false", "true"] and out["saved"] == "cards"
    assert out["cards"] == out["table"] == "m0,m1,m2"             # one state, both views
    phone = CSS[CSS.index("@media(max-width:720px){"):]
    assert "body[data-view=cards] .wrap{display:none;}" in phone
    assert "body[data-view=cards] .cards{display:flex;}" in phone
    # the phone table scrolls inside its box, never the page
    assert re.search(r"\.wrap\{[^}]*overflow-x:auto", phone) and "overflow-x:hidden" in phone
    assert ".viewtog" in phone and "min-height:44px" in phone


# ------------------------------------------------------------ 2026-11 board redesign: chips, sections, keyboard sort

def chip_rows():
    base = rows()
    extra = [dict(class_="S", lean="balanced", old=False, hosted=True),
             dict(class_="A", lean="chat-leaning", old=False, hosted=False),
             dict(class_=None, lean=None, old=True, hosted=False)]
    for r, e in zip(base, extra):
        r["class"], r["lean"], r["old"], r["hosted"] = e["class_"], e["lean"], e["old"], e["hosted"]
        r["points_cell"] = "Coding 5.0 = Func 3.0 (set 1) + Tools 2.0; Chat 4.0 = Deep RP 4.0"
    return base


@needs_node
def test_quick_chips_filter_rows_and_are_saved():
    out = run_board(board(rows=chip_rows()), r"""
      const chip = id => el('chips').fire('click', {target: {closest: s => (s === '[data-chip]' ? {dataset: {chip: id}} : null)}});
      out.chips = keys('chips', 'data-chip');
      chip('class:S'); out.s = order(); chip('class:A'); out.sa = order(); chip('class:S'); chip('class:A');
      chip('lean:chat-leaning'); out.lean = order(); chip('lean:chat-leaning');
      chip('current'); out.current = order(); chip('current');
      chip('where:hosted'); out.hosted = order(); chip('where:local'); out.local = order(); chip('where:local'); out.nowhere = order();
      el('benched').checked = true; el('benched').fire('change', {target: el('benched')}); out.ranked = order();
      chip('class:S'); await wait(400); out.saved = saved();
      el('clearAll').fire('click'); out.cleared = order(); out.count = el('count').innerHTML;
    """)
    assert out["chips"] == ["class:S", "class:A", "lean:balanced", "lean:chat-leaning", "current",
                            "where:hosted", "where:local"]
    assert out["s"] == "m0" and out["sa"] == "m0,m1"
    assert out["lean"] == "m1" and out["current"] == "m0,m1"
    assert out["hosted"] == "m0" and out["local"] == "m1,m2" and out["nowhere"] == "m0,m1,m2"
    assert out["ranked"] == "m0,m1,m2"
    assert out["saved"]["classes"] == ["S"] and out["saved"]["benched"] is True
    assert out["cleared"] == "m0,m1,m2" and out["count"].startswith("3 of 3 models")


@needs_node
def test_chips_are_restored_and_unknown_chip_values_dropped():
    st = {"version": 2, "columns": [], "sortKey": "rank", "sortDir": 1, "filters": {}, "text": "",
          "benched": False, "expanded": [], "view": "table", "classes": ["A", "Z"], "leans": ["bogus"],
          "current": True, "where": "local"}
    out = run_board(board(rows=chip_rows()), "out.order = order();",
                    store={"crucibleforge-board-layout": json.dumps(st)})
    assert out["order"] == "m1"                                  # A + current suite + local
    out = run_board(board(), "out.chips = keys('chips', 'data-chip');")
    assert out["chips"] == []                                    # rows without class/lean/old/hosted: no chips


@needs_node
def test_head_sorts_from_the_keyboard_and_points_show_in_the_expander():
    out = run_board(board(rows=chip_rows()), r"""
      const key = (k, name) => el('thead').fire('keydown', {key: name, preventDefault() {}, target: th(k)});
      key('tok_s', 'Enter'); out.tok = order(); key('tok_s', 'Tab'); out.same = order();
      key('tok_s', ' '); out.tok2 = order();
      el('tbody').fire('click', {target: {closest: s => (s === '[data-toggle]' ? {dataset: {toggle: 'm0'}} : null)}});
      out.pts = el('tbody').innerHTML.includes('Points breakdown') && el('tbody').innerHTML.includes('<li>Chat 4.0 = Deep RP 4.0</li>');
      out.tabindex = el('thead').innerHTML.includes('tabindex="0"');
    """)
    assert out["tok"] == "m1,m2,m0" and out["same"] == out["tok"] and out["tok2"] == "m0,m2,m1"
    assert out["pts"] and out["tabindex"]


def test_sections_under_the_table_and_one_closed_more_detail():
    html = render_html(chip_rows(), "sub", components=COMPS, footer=["long footnote"],
                       legend=["how"], versus=["a beats b"], ladder=["a: Coding set 1"])
    tail = html.split("<footer>", 1)[1]
    assert tail.index("Who beats whom") < tail.index("Ladder packs") < tail.index("How to read this") < tail.index("More detail")
    assert tail.count("<details") == 1 and "<details class=\"more\">" in tail and " open" not in tail.split("<summary>")[0]
    assert "long footnote" in tail.split("<summary>", 1)[1]       # the long text only lives inside More detail
    bare = render_html(chip_rows(), "sub", components=COMPS, footer=["x"])
    assert "Who beats whom" not in bare and "Ladder packs" not in bare
    assert "expandAll" not in html and "Expand all" not in html


def test_html_ladder_lines_from_the_points_breakdown():
    from crucibleforge import report
    got = report.html_ladder([
        {"label": "a", "points_cell": "Coding 11.2 = Func 5.1±0.2 (set 1) + Tools 3.3 (set 2); Chat 8.0 = X 2.0; NSFW 9.8 = Unc 2.8 (set 1) MAXED"},
        {"label": "b", "points_cell": "Coding - = Func -"}, {"label": "c", "points_cell": None}])
    assert got == ["a: Coding set 2, NSFW set 1 (maxed)"]
    assert report.HTML_LEGEND and all("private" not in x.lower() for x in report.HTML_LEGEND)


@needs_node
def test_point_scores_get_point_sized_minimum_steps():
    rs = chip_rows()
    for r in rs:
        r["s"] = {"overall": r["overall"]}
    out = run_board(board(rows=rs), r"""
      openMenu('overall'); out.html = menuHtml();
      openMenu('c:RP'); out.comp = menuHtml();
    """)
    over = out["html"]
    assert 'data-preset="18"' in over and 'data-preset="90"' not in over
    assert 'data-preset="8"' in over
    comp = out["comp"]
    assert 'data-preset="90"' in comp                              # components stay on 0-100
    assert 'data-preset="18"' not in comp


# --------------------------------------------- 2026-11-03: per-header menus
# Every header carries a ▾ that opens that column's filter menu: sort up /
# down, the operators for the column's type, a clear and the active filter.

@needs_node
def test_the_header_menu_opens_from_its_trigger_and_the_label_still_sorts():
    out = run_board(board(), r"""
      out.closed = menuOpen();
      const t = openMenu('chat');
      out.opened = menuOpen(); out.expanded = t.getAttribute('aria-expanded');
      out.html = menuHtml();
      out.hasAsc = menuHtml().includes('Sort ascending') && menuHtml().includes('Sort descending');
      out.hasClear = menuHtml().includes('Clear filter for this column');
      out.seps = (menuHtml().match(/fm-sep/g) || []).length;
      out.triggers = (el('thead').innerHTML.match(/data-fbtn=/g) || []).length;
      el('thead').fire('click', {target: th('coding')});      // the label still sorts
      out.sorted = el('thead').innerHTML.includes('data-k="coding" scope="col" tabindex="0" class="main-col" aria-sort="descending"');
      out.arrow = el('thead').innerHTML.includes('aria-haspopup="true"');
    """)
    assert out["closed"] is False and out["opened"] is True
    assert out["expanded"] == "true"                             # aria-expanded on the trigger
    assert out["hasAsc"] and out["hasClear"] and out["seps"] == 2
    assert out["triggers"] == len(MAIN) + len(DEFAULT_EXTRA) + 1  # one ▾ per header
    assert out["sorted"] and out["arrow"]                        # the ▾ did not steal the sort


@needs_node
def test_the_menu_is_keyboard_reachable_and_escape_returns_the_focus():
    out = run_board(board(), r"""
      const t = openMenu('chat');
      out.expanded = t.getAttribute('aria-expanded');
      el('fmenu').fire('keydown', {key: 'Escape', preventDefault() {}});
      out.closed = menuOpen(); out.focus = focused;
      out.after = t.getAttribute('aria-expanded');
      const t2 = openMenu('coding');
      el('fmenu').fire('keydown', {key: 'ArrowDown', preventDefault() {}});
      out.walk = focused;                       // the arrow key walks the menu
    """)
    assert out["expanded"] == "true"
    assert out["closed"] is False and out["focus"] == "chat"     # focus went back to the ▾
    assert out["after"] == "false"
    assert out["walk"] == "menu:asc"                           # the arrow key walks the menu


@needs_node
def test_the_menu_sorts_the_column_ascending_and_descending():
    out = run_board(board(), r"""
      openMenu('coding'); menuClick('[data-act]', {act: 'asc', k: 'coding'});
      out.asc = order();
      out.ascSort = el('thead').innerHTML.includes('data-k="coding" scope="col" tabindex="0" class="main-col" aria-sort="ascending"');
      openMenu('coding'); menuClick('[data-act]', {act: 'desc', k: 'coding'});
      out.desc = order(); out.closed = menuOpen();
    """)
    assert out["asc"] == "m2,m0,m1" and out["ascSort"]      # coding 20, 50, 80
    assert out["desc"] == "m1,m0,m2"
    assert out["closed"] is False                             # the menu closed after the sort


@needs_node
def test_the_menu_offers_the_operators_of_the_column_type():
    out = run_board(board(), r"""
      openMenu('chat'); out.num = menuHtml();
      openMenu('label'); out.text = menuHtml();
      openMenu('date'); out.text2 = menuHtml();
    """)
    num = out["num"]
    for op in ("Less than", "Less than or equal", "Greater than", "Greater than or equal",
               "Equal to", "Not equal", "Between", "Is empty"):
        assert op in num, op
    assert 'data-op="between"' in num
    txt = out["text"]
    for op in ("Contains", "Equals", "Does not contain", "Starts with", "Is empty"):
        assert op in txt, op
    assert "Less than" not in txt and "Between" not in txt
    assert "Contains" in out["text2"]                            # Run date is text too


@needs_node
def test_picking_an_operator_or_preset_keeps_the_menu_open():
    # 2026-11-04: the re-render detached the clicked button, the document's
    # click-outside handler missed #fmenu and closed the menu, so an operator
    # click was lost (reopening fell back to >=).
    out = run_board(board(), r"""
      openMenu('coding'); menuClick('[data-op]', {op: 'lt'});
      out.afterOp = menuOpen(); out.opOn = menuHtml().includes('data-op="lt"') && /fm-op on[^>]*data-op="lt"/.test(menuHtml());
      menuInput('value', 60); out.rows = order(); out.active = activeLine();
      menuClick('[data-preset]', {preset: '5'}); out.afterPreset = menuOpen();
      (docListeners.click || []).forEach(f => f({target: {isConnected: true, closest: () => null}}));
      out.outside = menuOpen();
    """)
    assert out["afterOp"] is True and out["opOn"] is True
    assert out["rows"] == "m0,m2" and out["active"].startswith("Coding <")
    assert out["afterPreset"] is True
    assert out["outside"] is False    # a real click outside still closes it


@needs_node
def test_every_numeric_operator_filters_the_rows():
    out = run_board(board(), r"""
      const op = (o, v) => { openMenu('coding'); menuClick('[data-op]', {op: o}); menuInput('value', v); };
      op('lt', 60); out.lt = order();
      op('le', 50); out.le = order();
      op('gt', 50); out.gt = order();
      op('ge', 50); out.ge = order();
      op('eq', 80); out.eq = order();
      op('ne', 80); out.ne = order();
      op('between', 20); menuInput('to', 50); out.between = order();
      menuClick('[data-act]', {act: 'clear', k: 'coding'}); out.cleared = order();
      openMenu('chat'); menuClick('[data-op]', {op: 'empty'}); out.empty = order();
      out.active = activeLine();
    """)
    assert out["lt"] == "m0,m2"       # coding 50 and 20 are below 60
    assert out["le"] == "m0,m2"       # 50 and 20
    assert out["gt"] == "m1"          # 80
    assert out["ge"] == "m0,m1"
    assert out["eq"] == "m1"
    assert out["ne"] == "m0,m2"
    assert out["between"] == "m0,m2"  # 20..50: 50 and 20
    assert out["cleared"] == "m0,m1,m2"   # "Clear filter for this column"
    assert out["empty"] == "m1"       # only m1 has no Chat score
    assert out["active"] == "Chat is empty"


@needs_node
def test_the_text_operators_filter_the_rows():
    out = run_board(board(), r"""
      const op = (o, v) => { openMenu('label'); menuClick('[data-op]', {op: o}); menuInput('value', v); };
      op('contains', '1'); out.contains = order();
      op('eq', 'm2'); out.eq = order();
      op('not', '1'); out.not = order();
      op('starts', 'm'); out.starts = order();
      tick('notes', true);
      openMenu('notes'); menuClick('[data-op]', {op: 'empty'}); out.empty = order();
      out.active = activeLine();
    """)
    assert out["contains"] == "m1"
    assert out["eq"] == "m2"
    assert out["not"] == "m0,m2"
    assert out["starts"] == "m0,m1,m2"
    assert out["empty"] == "m0"       # only m0 has no notes
    assert out["active"] == "Notes is empty"


@needs_node
def test_filters_compose_with_and_across_columns():
    out = run_board(board(), r"""
      openMenu('coding'); menuClick('[data-op]', {op: 'ge'}); menuInput('value', 50);
      out.one = order();
      openMenu('c:Tools'); menuClick('[data-op]', {op: 'ge'}); menuInput('value', 70);
      out.two = order();
      openMenu('c:Tools'); menuClick('[data-op]', {op: 'lt'}); menuInput('value', 90);
      out.replaced = order();                        // one filter per column: the new op replaces
      out.active = activeLine();
    """)
    assert out["one"] == "m0,m1"                     # coding >= 50
    assert out["two"] == "m1"                        # AND: coding >= 50 AND Tools >= 70
    assert out["replaced"] == "m0"                  # Tools < 90 now (60 passes, 90 does not)
    assert out["active"] == "Tools < 90"


@needs_node
def test_per_column_filters_round_trip_through_save_and_reload():
    out = run_board(board(), r"""
      openMenu('coding'); menuClick('[data-op]', {op: 'gt'}); menuInput('value', 20);
      openMenu('c:RP'); menuClick('[data-op]', {op: 'between'}); menuInput('value', 50); menuInput('to', 99);
      await wait(400); out.saved = saved(); out.order = order();
    """)
    assert out["order"] == "m0"                      # coding > 20 AND RP 50..99
    assert out["saved"]["filters"] == {"coding": {"op": "gt", "value": 20},
                                       "c:RP": {"op": "between", "from": 50, "to": 99}}
    again = run_board(board(), "out.order = order(); out.filters = filterRow();",
                      store={"crucibleforge-board-layout": json.dumps(out["saved"])})
    assert again["order"] == "m0"                    # the filters came back with the layout
    assert again["filters"] == MAIN + DEFAULT_EXTRA + ["suite"]


@needs_node
def test_an_unknown_operator_or_column_is_dropped_not_fatal():
    st = {"version": 2, "columns": ["c:RP"], "sortKey": "rank", "sortDir": 1,
          "filters": {"c:RP": {"op": "sideways", "value": 50},          # unknown operator
                      "c:Tools": {"op": "ge", "value": 50},             # a column that is not shown
                      "chat": {"op": "gt", "value": 70}},               # fine
          "text": "", "benched": False, "expanded": [], "view": "table"}
    out = run_board(board(), "out.order = order(); out.saved = null;", store={"crucibleforge-board-layout": json.dumps(st)})
    assert out["order"] == "m0"                      # only the chat > 70 filter survived
    # a broken value is dropped as well, and an old number still means ">="
    st2 = {"version": 2, "columns": ["c:RP", "c:Tools"], "sortKey": "rank", "sortDir": 1,
           "filters": {"c:RP": {"op": "between", "from": "x", "to": 2}, "c:Tools": 50},
           "text": "", "benched": False, "expanded": [], "view": "table"}
    out = run_board(board(), "out.order = order();", store={"crucibleforge-board-layout": json.dumps(st2)})
    assert out["order"] == "m0,m1"                   # Tools >= 50 (the old number format)


@needs_node
def test_the_old_saved_layout_without_filters_loads_clean():
    st = {"version": 2, "columns": ["c:Tools"], "sortKey": "rank", "sortDir": 1,
          "filters": {}, "text": "", "benched": False, "expanded": [], "view": "table"}
    out = run_board(board(), "out.order = order(); out.filters = filterRow(); out.count = el('count').innerHTML;",
                    store={"crucibleforge-board-layout": json.dumps(st)})
    assert out["order"] == "m0,m1,m2"
    assert out["count"].startswith("3 of 3 models")


def test_the_page_carries_the_menu_and_no_external_host():
    html = board()
    assert 'id="fmenu"' in html and 'id="fmenuBody"' in html and 'id="fmenuActive"' in html
    for leak in ("http://", "https://", "//cdn", "<link", "@import", "fetch("):
        assert leak not in html, leak
    assert len(html.encode()) < 120_000
