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
    setAttribute(k, v) { this.attrs[k] = String(v); }, getAttribute(k) { return this.attrs[k]; }};
  e.classList = {toggle(c, on) { on ? e.cls.add(c) : e.cls.delete(c); }, add(c) { e.cls.add(c); },
    remove(c) { e.cls.delete(c); }, contains(c) { return e.cls.has(c); }};
  return e;
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
global.document = {readyState: 'complete', getElementById: el, body, addEventListener() {}};
function deliver(data, source) {
  (listeners.message || []).forEach(f => f({source: source === undefined ? window.parent : source, data}));
}
const wait = ms => new Promise(r => setTimeout(r, ms));
const keys = (id, attr) => [...el(id).innerHTML.matchAll(new RegExp(attr + '="([^"]+)"', 'g'))].map(m => m[1]);
const heads = () => keys('thead', 'data-k');
const filterRow = () => keys('filtersRow', 'data-f');
const chooser = () => [...el('colsList').innerHTML.matchAll(/<li class="col-item( on)?"[^>]*data-col="([^"]+)"/g)].map(m => (m[1] ? '+' : '-') + m[2]);
const order = () => [...el('tbody').innerHTML.matchAll(/<tr class="data-row[^"]*" data-label="([^"]+)"/g)].map(m => m[1]).join(',');
const cardOrder = () => [...el('cards').innerHTML.matchAll(/<article class="card[^"]*" data-label="([^"]+)"/g)].map(m => m[1]).join(',');
const lastSet = () => { const s = parentMsgs.filter(([m]) => m.op === 'set'); return s.length ? s[s.length - 1][0].state : null; };
const saved = () => (store['crucibleforge-board-layout'] ? JSON.parse(store['crucibleforge-board-layout']) : null);
// delegated-event targets
const th = k => ({closest: s => (s === 'th[data-k]' ? {dataset: {k}} : null)});
function chipFor(k) {
  const chip = {dataset: {f: k}, cls: new Set()};
  chip.classList = {toggle(c, on) { on ? chip.cls.add(c) : chip.cls.delete(c); }};
  chip.sel = {tagName: 'SELECT', value: '', closest: s => (s === '[data-f]' ? chip : null)};
  chip.inp = {tagName: 'INPUT', value: '', closest: s => (s === '[data-f]' ? chip : null)};
  chip.querySelector = s => (s === 'select' ? chip.sel : chip.inp);
  return chip;
}
function pickFilter(k, v) { const c = chipFor(k); c.sel.value = String(v); el('filtersRow').fire('change', {target: c.sel}); return c; }
function typeFilter(k, v) { const c = chipFor(k); c.inp.value = String(v); el('filtersRow').fire('input', {target: c.inp}); return c; }
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
    res = subprocess.run(["node", "-e", js], capture_output=True, text=True, timeout=30)
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
    assert extra == DEFAULT_EXTRA + ["date", "notes", "judge", "coverage", "provider"]
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
    assert out["heads"] == MAIN + DEFAULT_EXTRA
    assert out["filters"] == ["chat", "coding", "overall"] + DEFAULT_EXTRA
    assert out["chooser"] == ["+" + k for k in DEFAULT_EXTRA] + ["-date", "-notes", "-judge", "-coverage", "-provider"]
    assert out["order"] == "m0,m1,m2" and out["view"] == "table" and out["pressed"] == "true"


@needs_node
def test_chooser_add_remove_reorder_drag_and_filters_follow_the_columns():
    out = run_board(board(), r"""
      pickFilter('c:NSFW', 70); el('thead').fire('click', {target: th('c:NSFW')});
      out.sortedBy = el('thead').innerHTML.includes('data-k="c:NSFW" scope="col" class="side-chat" aria-sort="descending"');
      tick('c:NSFW', false);                         // remove NSFW: its filter and its sort go too
      out.afterRemove = heads().slice(6); out.filters1 = filterRow(); out.order1 = order();
      tick('notes', true); tick('date', true);        // add Notes, Run date (text: no ≥ filter)
      move('notes', -1); move('notes', -1);            // Notes two places left
      drag('c:Tools', 'c:RP');                         // Tools before RP
      drag('judge', 'c:Programs');                     // dragging an unchosen column in chooses it
      out.heads = heads().slice(6); out.filters2 = filterRow(); out.chooser = chooser();
    """)
    assert out["sortedBy"] is True
    assert out["afterRemove"] == ["c:RP", "c:Programs", "c:Tools"]
    assert out["filters1"] == ["chat", "coding", "overall", "c:RP", "c:Programs", "c:Tools"]
    assert out["order1"] == "m0,m1,m2"               # NSFW >= 70 filter dropped with the column
    assert out["heads"] == ["c:Tools", "c:RP", "notes", "judge", "c:Programs", "date"]
    assert out["filters2"] == ["chat", "coding", "overall", "c:Tools", "c:RP", "c:Programs"]
    assert out["chooser"][:6] == ["+c:Tools", "+c:RP", "+notes", "+judge", "+c:Programs", "+date"]
    assert out["chooser"][6:] == ["-c:NSFW", "-coverage", "-provider"]


@needs_node
def test_threshold_and_free_number_filters():
    out = run_board(board(), r"""
      pickFilter('c:Tools', 50); out.t50 = order();
      const c = typeFilter('coding', 65); out.c65 = order(); out.sel = c.sel.value;
      typeFilter('coding', 50); out.c50 = order();
      typeFilter('coding', ''); pickFilter('c:Tools', ''); out.none = order();
      pickFilter('chat', 30); out.chat = order();                  // a missing Chat fails the filter
      el('filtersRow').fire('click', {target: {closest: s => (s === '[data-act="clear"]' ? {} : null)}});
      out.cleared = order(); out.btn = el('filtersBtn').textContent;
    """)
    assert out["t50"] == "m0,m1"
    assert out["c65"] == "m1" and out["sel"] == "custom"
    assert out["c50"] == "m0,m1" and out["none"] == "m0,m1,m2"
    assert out["chat"] == "m0,m2"
    assert out["cleared"] == "m0,m1,m2" and out["btn"].endswith("Filters")


@needs_node
def test_state_round_trip_through_a_fake_dispatch_parent():
    """Framed + sandboxed (localStorage throws): `get` on load, the first
    answer applied, later answers (acks) ignored, `set` after each change."""
    saved_state = {"version": 1, "columns": ["notes", "c:RP", "bogus", "c:RP"], "sortKey": "c:RP", "sortDir": -1,
                   "filters": {"c:RP": 50, "c:Programs": 70, "chat": 10}, "text": "", "benched": True,
                   "expanded": ["m0", "ghost"], "view": "cards"}
    out = run_board(board(), r"""
      out.first = parentMsgs.map(([m, t]) => [m.type, m.op, t]);
      deliver({type: 'dispatch:tool-state', state: null}, {});          // a foreign window: ignored
      deliver({type: 'dispatch:tool-state', state: """ + json.dumps(saved_state) + r"""});
      out.heads = heads().slice(6); out.filters = filterRow(); out.order = order(); out.view = body.getAttribute('data-view');
      out.expanded = /detail-row/.test(el('tbody').innerHTML); out.benched = el('benched').checked;
      out.filtersOpen = el('filtersRow').cls.has('open');
      deliver({type: 'dispatch:tool-state', state: {version: 1, columns: []}});   // an ack: ignored
      out.stillHeads = heads().slice(6);
      el('viewTable').fire('click'); el('thead').fire('click', {target: th('tok_s')});
      out.beforeDebounce = parentMsgs.filter(([m]) => m.op === 'set').length;
      await wait(400);
      out.sets = parentMsgs.filter(([m]) => m.op === 'set').map(([, t]) => t);
      out.state = lastSet();
    """, framed=True, storage_throws=True)
    assert out["first"] == [["dispatch:tool-state", "get", "*"]]
    assert out["heads"] == ["notes", "c:RP"]                     # unknown + duplicate dropped
    assert out["filters"] == ["chat", "coding", "overall", "c:RP"]  # Programs not shown -> its filter dropped
    assert out["order"] == "m0"                                  # benched + chat>=10 + RP>=50
    assert out["view"] == "cards" and out["expanded"] and out["benched"] and out["filtersOpen"]
    assert out["stillHeads"] == ["notes", "c:RP"]
    assert out["beforeDebounce"] == 0 and out["sets"] == ["*"]    # one debounced set
    assert out["state"] == {"version": 1, "columns": ["notes", "c:RP"], "sortKey": "tok_s", "sortDir": -1,
                            "filters": {"c:RP": 50, "chat": 10}, "text": "", "benched": True,
                            "expanded": ["m0"], "view": "table"}


@needs_node
def test_localstorage_when_opened_directly_and_version_bump_resets():
    st = {"version": 1, "columns": ["c:Tools", "date"], "sortKey": "c:Tools", "sortDir": -1,
          "filters": {"c:Tools": 50}, "text": "m", "benched": False, "expanded": [], "view": "table"}
    out = run_board(board(), r"""
      out.msgs = parentMsgs.length; out.heads = heads().slice(6); out.order = order(); out.text = el('filter').value;
      el('filter').fire('input', {target: {value: 'm1'}}); await wait(400); out.saved = saved();
      el('resetLayout').fire('click'); out.reset = saved(); out.resetHeads = heads().slice(6);
    """, store={"crucibleforge-board-layout": json.dumps(st)})
    assert out["msgs"] == 0                                       # not framed: no bridge traffic
    assert out["heads"] == ["c:Tools", "date"] and out["order"] == "m1,m0" and out["text"] == "m"
    assert out["saved"]["text"] == "m1" and out["saved"]["columns"] == ["c:Tools", "date"]
    assert out["reset"]["columns"] == DEFAULT_EXTRA and out["reset"]["filters"] == {}
    assert out["resetHeads"] == DEFAULT_EXTRA
    old = dict(st, version=99)
    out = run_board(board(), "out.heads = heads().slice(6); out.order = order();",
                    store={"crucibleforge-board-layout": json.dumps(old)})
    assert out["heads"] == DEFAULT_EXTRA and out["order"] == "m0,m1,m2"
    out = run_board(board(), "out.heads = heads().slice(6);", store={"crucibleforge-board-layout": "{not json"})
    assert out["heads"] == DEFAULT_EXTRA


@needs_node
def test_a_renamed_component_is_dropped_from_a_saved_layout():
    st = {"version": 1, "columns": ["c:Gone", "c:Tools"], "sortKey": "c:Gone", "sortDir": -1,
          "filters": {"c:Gone": 90}, "text": "", "benched": False, "expanded": [], "view": "table"}
    out = run_board(board(), "out.heads = heads().slice(6); out.filters = filterRow(); out.order = order();",
                    store={"crucibleforge-board-layout": json.dumps(st)})
    assert out["heads"] == ["c:Tools"] and out["filters"] == ["chat", "coding", "overall", "c:Tools"]
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
