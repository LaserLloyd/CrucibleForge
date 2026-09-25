"""The board on phones (2026-09-25): <= 720 px renders one card per model
instead of the 18-column table, with a Sort select + direction toggle and the
element filters behind one "Filters (n)" disclosure. One JSON block and one JS
state drive both layouts."""
import json
import re
import shutil
import subprocess

import pytest

from crucibleforge.templates.board import CSS, render_html

COMPS = [{"label": "RP", "side": "chat"}, {"label": "Programs", "side": "coding"}]


def _rows():
    return [{"rank": i + 1, "label": f"m{i}", "model_id": "x", "provider": "p",
             "chat": ch, "coding": c, "overall": c, "tok_s": t, "date": "2026-09-23",
             "notes": n, "tier": tier, "judge": "j", "coverage": "full",
             "components": {"Programs": c, "RP": rp}}
            for i, (ch, c, t, rp, n, tier) in enumerate([
                (90.0, 50.0, 9.0, 95, "", 0),
                (None, 80.0, 100.0, None, "FAILED: http://192.0.2.10:1234/v1 down", 2),
                (70.0, 20.0, 30.0, 40, "partial", 1)])]


def _mobile_block(css):
    start = css.index("@media(max-width:720px){")
    return css[start:]


def test_page_carries_card_layout_and_phone_controls():
    html = render_html(_rows(), "sub", components=COMPS, footer=["f"])
    assert 'id="cards"' in html and "@media(max-width:720px)" in html
    # the table is still there, unchanged, for desktop
    assert '<tbody id="tbody"></tbody>' in html and 'data-k="c:RP"' in html
    # phone controls: one Sort select over every sortable key, a direction toggle,
    # and the Filters disclosure pointing at the (same) element-filter row
    sel = re.search(r'<select id="sortSel"[^>]*>(.*?)</select>', html).group(1)
    values = re.findall(r'<option value="([^"]*)"', sel)
    assert values == ["rank", "label", "chat", "coding", "overall", "tok_s", "date",
                      "c:RP", "c:Programs"]
    assert "Chat components" in sel and "Coding components" in sel
    assert 'id="sortDir"' in html
    assert 'id="filtersBtn"' in html and 'aria-controls="filtersRow"' in html
    assert 'class="filters-row" id="filtersRow"' in html


def test_css_hides_phone_bits_on_desktop_and_table_on_phones():
    desktop = CSS[:CSS.index("@media(max-width:720px){")]
    assert ".m-only,.cards{display:none;}" in desktop
    phone = _mobile_block(CSS)
    assert re.search(r"\.wrap[^{]*\{display:none;\}", phone)
    assert ".cards{display:flex;" in phone
    # collapsed filters, stacked full width when open
    assert ".filters-row{display:none;" in phone and ".filters-row.open{display:flex;flex-direction:column;" in phone
    # tap targets and readable numbers
    assert "min-height:44px" in phone and "font-size:24px" in phone
    assert "overflow-x:hidden" in phone
    # both colour schemes still come from the same tokens
    assert "prefers-color-scheme:light" in CSS


def test_phone_page_still_carries_no_urls_or_hosts():
    html = render_html(_rows(), "sub http://x.example/y", components=COMPS,
                       footer=["see https://h/i on box.tail0.ts.net:8700"])
    for leak in ("192.0.2.10", "ts.net", "http://", "https://", "src=\"http", "@import"):
        assert leak not in html, leak
    assert "<link" not in html and "fetch(" not in html
    assert len(html.encode()) < 100_000


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_one_state_drives_cards_and_table(tmp_path):
    html = render_html(_rows(), "sub", components=COMPS)
    blob = html.split('<script type="application/json" id="board-data">', 1)[1].split("</script>", 1)[0]
    script = html.rsplit("<script>", 1)[1].rsplit("</script>", 1)[0]
    harness = r"""
const els = {};
function mk(o){ return Object.assign({innerHTML:'', textContent:'', handlers:{}, value:'', checked:false,
  cls: new Set(), attrs: {},
  addEventListener(ev, fn){ this.handlers[ev] = fn; }, setAttribute(k, v){ this.attrs[k] = v; },
  classList:null}, o); }
function el(id){
  if (!els[id]) { const e = mk({id}); e.classList = {toggle(c, on){ on ? e.cls.add(c) : e.cls.delete(c); }}; els[id] = e; }
  return els[id];
}
els['board-data'] = mk({textContent: """ + json.dumps(blob) + r"""});
const progFilter = mk({dataset:{c:'Programs'}, classList:{toggle(){}}});
const progSelect = mk({closest(){ return progFilter; }});
global.document = { readyState: 'complete', getElementById: el,
  querySelectorAll: (sel) => sel === '.comp-filter select' ? [progSelect] : sel === '.comp-filter' ? [progFilter] : [],
  addEventListener(){} };
""" + script + r"""
const cards = () => [...el('cards').innerHTML.matchAll(/<article class="card[^"]*" data-label="(m\d)"/g)].map(m => m[1]).join(',');
const table = () => [...el('tbody').innerHTML.matchAll(/data-label="(m\d)"/g)].map(m => m[1]).join(',');
const out = {initial: cards(), initialTable: table(), dir0: el('sortDir').textContent};
el('sortSel').value = 'tok_s'; el('sortSel').handlers.change({target: el('sortSel')});
out.tok = cards(); out.tokTable = table(); out.dir1 = el('sortDir').textContent;
el('sortDir').handlers.click(); out.tokAsc = cards(); out.dir2 = el('sortDir').textContent;
out.btn0 = el('filtersBtn').textContent;
el('filtersBtn').handlers.click(); out.open = el('filtersRow').cls.has('open');
progSelect.value = '50'; progSelect.handlers.change();
out.prog = cards(); out.btn1 = el('filtersBtn').textContent; out.sel = el('sortSel').value;
el('filter').handlers.input({target: {value: 'm1'}}); out.text = cards();
el('filter').handlers.input({target: {value: 'zzz'}}); out.empty = el('cards').innerHTML;
el('filter').handlers.input({target: {value: ''}});
el('cards').handlers.click({target: {closest: s => s === '[data-toggle]' ? {dataset: {toggle: 'm1'}} : null}});
out.expandedCard = /card-detail/.test(el('cards').innerHTML);
out.expandedRow = /detail-row/.test(el('tbody').innerHTML);
out.cardsHtml = el('cards').innerHTML;
console.log(JSON.stringify(out));
"""
    p = tmp_path / "board.js"
    p.write_text(harness)
    res = subprocess.run(["node", str(p)], capture_output=True, text=True, timeout=30)
    assert res.returncode == 0, res.stderr
    out = json.loads(res.stdout)
    assert out["initial"] == out["initialTable"] == "m0,m1,m2"
    assert "Top first" in out["dir0"]
    # the Sort select re-orders cards AND table the same way; numbers default high-first
    assert out["tok"] == out["tokTable"] == "m1,m2,m0" and "High first" in out["dir1"]
    assert out["tokAsc"] == "m0,m2,m1" and "Low first" in out["dir2"]
    # Filters (n) counts active element filters; the row opens
    assert out["btn0"].endswith("Filters") and out["open"] is True
    assert out["prog"] == "m0,m1" and out["btn1"].endswith("Filters (1)")   # asc tok/s kept
    assert out["sel"] == "tok_s"
    assert out["text"] == "m1"
    assert "No model matches" in out["empty"]
    # a card's Details toggle shares the table's expanded set
    assert out["expandedCard"] and out["expandedRow"]
    html_cards = out["cardsHtml"]
    assert 'class="card fail-card"' in html_cards               # tier colour on the card
    assert 'class="val mid"' in html_cards                      # same bands as the table (Programs 50)
    assert "hl-val" in html_cards and "chip side-coding" in html_cards
