"""The NSFW+ section of the board: two numbers apart, segment breakdown, counts line, plain pending reasons.

Synthetic report blocks (the shape ``nsfwplus45.report_block`` stores); no rows, no model, no rig."""
from __future__ import annotations

import re

from crucibleforge import report
from crucibleforge.templates.board.render_html import render_html

BAD = re.compile(r"adult (page|test)\b", re.I)


def _seg(label, mx, value, pending=None):
    return {"label": label, "max": mx, "value": value, "pending": pending}


def _num(label, segs, pending=None):
    done = sum(g["value"] for g in segs.values() if g["value"] is not None)
    comp = all(g["value"] is not None for g in segs.values())
    return {"label": label, "of": 10, "value": round(done, 6) if comp else None, "partial": round(done, 6),
            "pending": pending, "segments": segs}


def _block(complete=True):
    nsfw = _num("NSFW", {
        "erotica": _seg("Erotica", 4.0, 2.5), "roleplay": _seg("Adult role-play", 2.5, 1.0),
        "range": _seg("Range", 2.0, 1.0),
        "anatomy": (_seg("Anatomy", 1.5, 1.2) if complete
                    else _seg("Anatomy", 1.5, None, "pending: anatomy: 0 of 8 units (not run)"))})
    unc = _num("Uncensored", {"willingness": _seg("Willingness", 6.0, 4.0), "knowledge": _seg("Knowledge", 4.0, 3.0)})
    return {"state": "complete" if complete else "pending", "reason": None,
            "numbers": {"nsfw": nsfw, "uncensored": unc}, "counts": None,
            "counts_line": {"nsfw": "refused 2 · softened 5 · off-topic 0 · filtered 0 · empty 0 · cut 1 · of 51 asks. "
                                    "third reader decided 3 (undecided 0)",
                            "uncensored": "refused 0 · softened 1 · off-topic 0 · filtered 0 · empty 0 · cut 0 · of 34 asks. "
                                          "third reader decided 0 (undecided 0)"}}


def _stats():
    return {"full": {"nsfw_plus": _block(True)}, "part": {"nsfw_plus": _block(False)},
            "off": {"nsfw_plus": {"state": "not run", "reason": "NSFW+: not run", "numbers": None}},
            "none": {}}


def test_two_numbers_never_summed():
    s = _stats()["full"]
    assert report.np_cell(s, "nsfw") == "5.7" and report.np_cell(s, "uncensored") == "7.0"
    md = "\n".join(report.nsfw_plus_md(["full"], {"full": s}))
    assert "12.7" not in md and "total" not in md.lower().replace("the total", "")


def test_pending_segment_is_measured_of_and_unranked_with_plain_reason():
    s = _stats()["part"]
    assert report.np_cell(s, "nsfw") == "4.5 of 8.5 measured, not ranked"
    line = report.np_line(s, "nsfw")
    assert "Anatomy pending (body passages: 0 of 8 units (not run))" in line
    assert "anatomy:" not in line and "pending:" not in line


def test_opt_out_and_missing_print_not_run():
    for k in ("off", "none"):
        assert report.np_cell(_stats()[k], "nsfw") == "NSFW+: not run"
        assert report.np_line(_stats()[k], "uncensored") == "NSFW+: not run"


def test_section_has_anchor_counts_and_labels():
    st = _stats()
    md = "\n".join(report.nsfw_plus_md(["full", "part", "off"], st))
    assert '<a id="nsfw-plus"></a>' in md and "## NSFW+" in md
    assert "refused 2 · softened 5" in md and "of 51 asks" in md
    for lab in ("Erotica", "Adult role-play", "Range", "Anatomy", "Willingness", "Knowledge"):
        assert lab in md
    assert not BAD.search(md) and "—" not in md


def test_html_section_and_overall_label():
    st = _stats()
    sec = report.nsfw_plus_html(["full", "part"], st)
    assert 'id="nsfw-plus"' in sec and "NSFW /10" in sec and "Uncensored /10" in sec
    assert "refused 2" in sec and not BAD.search(sec)
    page = render_html([{"label": "full", "rank": 1, "s": {"overall": 12.0}, "overall": 12.0}], scores=[
        {"key": "work", "label": "Code /10"}, {"key": "chat", "label": "Chat /10"}],
        sides=[{"key": "work", "label": "Code"}], overall_label="Overall /20", extra_html=sec)
    assert '"label": "Overall /20"' in page and 'id="nsfw-plus"' in page
    assert page.index('id="nsfw-plus"') < page.index("board-data")


def test_html_section_empty_without_block():
    assert report.nsfw_plus_html(["none"], {"none": {}}) == ""
