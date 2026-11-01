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
    assert out["filters"] == ["chat", "coding", "overall"] + DEFAULT_EXTRA
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
    assert out["filters1"] == ["chat", "coding", "overall", "c:RP", "c:Programs", "c:Tools"]
    assert out["order1"] == "m0,m1,m2"               # NSFW >= 70 filter dropped with the column
    assert out["heads"] == ["c:Tools", "c:RP", "notes", "judge", "c:Programs", "date"]
    assert out["filters2"] == ["chat", "coding", "overall", "c:Tools", "c:RP", "c:Programs"]
    assert out["chooser"][:6] == ["+c:Tools", "+c:RP", "+notes", "+judge", "+c:Programs", "+date"]
    assert out["chooser"][6:] == ["-c:NSFW", "-suite", "-coverage", "-provider", "-room_cell", "-reason", "-tiers_cell", "-raw_cell", "-vs_above", "-points_cell"]


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
    assert out["filters"] == ["chat", "coding", "overall", "c:RP"]  # Programs not shown -> its filter dropped
    assert out["order"] == "m0"                                  # benched + chat>=10 + RP>=50
    assert out["view"] == "cards" and out["expanded"] and out["benched"] and out["filtersOpen"]
    assert out["stillHeads"] == ["notes", "c:RP"]
    assert out["beforeDebounce"] == 0 and out["sets"] == ["*"]    # one debounced set
    assert out["state"] == {"version": 2, "columns": ["notes", "c:RP"], "sortKey": "tok_s", "sortDir": -1,
                            "filters": {"c:RP": 50, "chat": 10}, "text": "", "benched": True,
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
      out.html = el('filtersRow').innerHTML;
    """)
    over = out["html"].split('data-f="overall"', 1)[1].split("</select>", 1)[0]
    assert '<option value="18"' in over and '<option value="90"' not in over
    comp = out["html"].split('data-f="c:RP"', 1)[1].split("</select>", 1)[0]
    assert '<option value="90"' in comp                          # components stay on 0-100
