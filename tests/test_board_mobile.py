"""The board on phones (2026-09-25, revised 2026-09-26): <= 720 px offers a
persisted Table | Cards toggle (Table by default: the same table scrolling
inside its box), a Sort select + direction toggle, and the element filters
behind one "Filters (n)" disclosure that starts OPEN when a filter is active.
One JSON block and one JS state drive both layouts."""
import json
import re
import shutil

import pytest

from crucibleforge.templates.board import CSS, render_html

from test_board_layout import run_board

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
    # the table is still there for desktop (its head is built by script.js)
    assert '<tbody id="tbody"></tbody>' in html and '<thead id="thead"></thead>' in html
    data = json.loads(html.split('id="board-data">', 1)[1].split("</script>", 1)[0])
    assert [c["key"] for c in data["extra"]][:2] == ["c:RP", "c:Programs"]
    # phone controls: Table | Cards, one Sort select over every sortable key,
    # a direction toggle, and the Filters disclosure pointing at the filter row
    assert 'id="viewTable"' in html and 'id="viewCards"' in html
    sel = re.search(r'<select id="sortSel"[^>]*>(.*?)</select>', html).group(1)
    values = re.findall(r'<option value="([^"]*)"', sel)
    assert values == ["rank", "label", "chat", "coding", "overall", "tok_s", "c:RP", "c:Programs",
                      "date", "notes", "judge", "coverage", "provider"]
    assert "Chat components" in sel and "Coding components" in sel and "Run info" in sel
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
    assert len(html.encode()) < 120_000


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_one_state_drives_cards_and_table():
    out = run_board(render_html(_rows(), "sub", components=COMPS), r"""
      el('viewCards').fire('click');
      out.initial = cardOrder(); out.initialTable = order(); out.dir0 = el('sortDir').textContent;
      el('sortSel').value = 'tok_s'; el('sortSel').fire('change', {target: el('sortSel')});
      out.tok = cardOrder(); out.tokTable = order(); out.dir1 = el('sortDir').textContent;
      el('sortDir').fire('click'); out.tokAsc = cardOrder(); out.dir2 = el('sortDir').textContent;
      out.btn0 = el('filtersBtn').textContent; out.open0 = el('filtersRow').cls.has('open');
      el('filtersBtn').fire('click'); out.open = el('filtersRow').cls.has('open');
      pickFilter('c:Programs', 50);
      out.prog = cardOrder(); out.btn1 = el('filtersBtn').textContent; out.sel = el('sortSel').value;
      el('filter').fire('input', {target: {value: 'm1'}}); out.text = cardOrder();
      el('filter').fire('input', {target: {value: 'zzz'}}); out.empty = el('cards').innerHTML;
      el('filter').fire('input', {target: {value: ''}});
      el('cards').fire('click', {target: {closest: s => s === '[data-toggle]' ? {dataset: {toggle: 'm1'}} : null}});
      out.expandedCard = /card-detail/.test(el('cards').innerHTML);
      out.expandedRow = /detail-row/.test(el('tbody').innerHTML);
      out.cardsHtml = el('cards').innerHTML;
      el('viewTable').fire('click'); out.view = body.getAttribute('data-view');
      await wait(400); out.saved = saved();
    """)
    assert out["initial"] == out["initialTable"] == "m0,m1,m2"
    assert "Top first" in out["dir0"]
    # the Sort select re-orders cards AND table the same way; numbers default high-first
    assert out["tok"] == out["tokTable"] == "m1,m2,m0" and "High first" in out["dir1"]
    assert out["tokAsc"] == "m0,m2,m1" and "Low first" in out["dir2"]
    # Filters (n) counts active element filters; closed with none, the row opens
    assert out["btn0"].endswith("Filters") and out["open0"] is False and out["open"] is True
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
    # back to Table; the whole view is saved
    assert out["view"] == "table"
    assert out["saved"]["view"] == "table" and out["saved"]["filters"] == {"c:Programs": 50}
    assert out["saved"]["expanded"] == ["m1"] and out["saved"]["sortKey"] == "tok_s"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_filters_disclosure_starts_open_when_a_saved_filter_is_active():
    st = {"version": 1, "columns": ["c:RP", "c:Programs"], "sortKey": "rank", "sortDir": 1,
          "filters": {"c:RP": 50}, "text": "", "benched": False, "expanded": [], "view": "table"}
    out = run_board(render_html(_rows(), "sub", components=COMPS), r"""
      out.open = el('filtersRow').cls.has('open'); out.btn = el('filtersBtn').textContent;
    """, store={"crucibleforge-board-layout": json.dumps(st)})
    assert out["open"] is True and out["btn"] == "▾ Filters (1)"
