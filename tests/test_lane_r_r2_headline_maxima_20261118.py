"""R2 (fix round 2026-11-18, AUDIT-HARNESS F3): the Scorecard's bold Coding / Chat cells never mix maxima. A section
with a part missing (not run, not measured, pending) prints its measured points OF the measured maximum ("5.0 of 5"),
as the Points table already does; a section with nothing measured prints "pending". A complete section stays a
plain number out of 10. Toy board: no model, no rig."""
from __future__ import annotations

from crucibleforge import report

from _lane_r_toy import PASS, board, cell, cut, toys  # noqa: F401


def _cut(prefixes):
    return lambda lv, cid: cut("PAS") if cid.startswith(prefixes) else "PASS"


def test_a_complete_section_is_a_plain_number(tmp_path, monkeypatch):
    md, _s, _h = board(tmp_path, monkeypatch, {"m": PASS})
    c = cell(md, "m", "Coding (small tasks)")
    assert c.startswith("**10**.0 ± 0.1") and " of " not in c, c


def test_a_section_with_a_part_missing_says_of_what(tmp_path, monkeypatch):
    # part a: every unit cut -> not measured; Coding = b alone, 5 of 5
    md, stats, _h = board(tmp_path, monkeypatch, {"m": _cut(("FLAT-xa-",))})
    c = cell(md, "m", "Coding (small tasks)")
    assert c.startswith("**5**.0 of 5"), c
    assert stats["m"]["headline"]["unscaled"]["work"]["of"] == 5


def test_a_section_with_nothing_measured_reads_pending(tmp_path, monkeypatch):
    md, _s, _h = board(tmp_path, monkeypatch, {"m": _cut(("FLAT-xc-",))})
    assert cell(md, "m", "Role-play and memory") == "pending"


def test_section_cell_unit():
    hl = {"S": {"work": 6.4}, "m": {"work": 0.5}, "unscaled": {"work": {"of": 7.5, "pending": []}}}
    d = {"key": "work", "parts": [{"key": "a", "weight": 5}, {"key": "b", "weight": 2.5}, {"key": "c", "weight": 2.5}]}
    assert report.section_cell45(hl, d) == "**6**.4 of 7.5 ± 0.5"
    hl["unscaled"]["work"]["of"] = 10
    assert report.section_cell45(hl, d) == "**6**.4 ± 0.5"
    assert report.section_cell45({"S": {"work": None}, "m": {}, "unscaled": {"work": {"of": 0}}}, d) == "pending"


def test_the_html_row_says_of_what_too(tmp_path, monkeypatch):
    from _lane_r_toy import html_data
    _md, _s, html = board(tmp_path, monkeypatch, {"m": _cut(("FLAT-xa-", "FLAT-xc-"))})
    r = html_data(html)["rows"][0]
    assert r["sub_tag"]["work"] == "of 5"
    assert r["cell_text"]["chat"] == "pending"
