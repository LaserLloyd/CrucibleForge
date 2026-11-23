"""R7 (fix round 2026-11-18, REVIEW finding 12, D11): every row prints how it was run: thinking on or off, the
reasoning effort as sent, the median reasoning tokens per answer, and its cut and empty counts. The run path (lane-p)
writes ``thinking``, ``effort``, ``reasoning_tokens``, ``finish_reason`` and ``cut`` on each row; a row from before
that prints "not recorded" for what it lacks. Toy board: no model, no rig."""
from __future__ import annotations

from crucibleforge import report

from _lane_r_toy import PASS, board, cell, cut, head, html_data, toys  # noqa: F401


def test_disclosure_unit_old_rows():
    rows = [{"response": "x", "metrics": {}}, {"response": "y"}]
    d = report.disclosure45(rows)
    assert d == {"thinking": None, "effort": None, "reasoning_tokens_median": None}
    s = {"disclosure45": d, "scorecard": {}}
    assert report.disclosure45_cell(s) == ("thinking not recorded · effort not recorded · "
                                           "reasoning tokens not recorded · 0 cut · 0 empty")


def test_disclosure_unit_new_rows():
    rows = [{"thinking": True, "effort": "high", "reasoning_tokens": n} for n in (1000, 3000, 9000)]
    rows.append({"thinking": True, "effort": "high", "metrics": {"reasoning_tokens": 2000}})
    d = report.disclosure45(rows)
    assert d == {"thinking": "on", "effort": "high", "reasoning_tokens_median": 2500}
    card = {"ladder": {"work": {"a": {"cut": {"cut": 2, "of": 8, "empty": 1}}, "b": {"cut": {"cut": 1, "of": 4,
                                                                                            "empty": 0}}}}}
    assert report.disclosure45_cell({"disclosure45": d, "scorecard": card}) == \
        "thinking on · effort high · median 2.5k reasoning tokens · 3 cut · 1 empty"
    assert report.disclosure45([{"thinking": False}, {"thinking": True}])["thinking"] == "mixed (1 on, 1 off)"
    assert report.disclosure45([{"effort": None, "thinking": True}])["effort"] is None
    assert report.disclosure45([{"effort": "provider default"}])["effort"] == "provider default"


def test_the_board_prints_it_per_row(tmp_path, monkeypatch):
    def pol(lv, cid):
        return cut("PAS") if cid.startswith("FLAT-xa-001") else "PASS"
    md, stats, html = board(tmp_path, monkeypatch, {"m": pol})
    assert "Run settings" in head(md)
    c = cell(md, "m", "Run settings")
    assert "1 cut" in c and "0 empty" in c, c
    r = html_data(html)["rows"][0]
    assert r["run_settings"] == c and r["disclosure"]["cut"] == 1


def test_a_did_not_finish_reply_is_not_hidden_behind_0_cut():
    # D10a: a short-keyed reply cut at the full budget is graded, not left out, so it is not a "cut"; the same row
    # must still say it hit its budget, or "0 cut" sits beside "2 ran out of budget" in the same row
    d = {"thinking": "on", "effort": "provider default", "reasoning_tokens_median": 2000}
    card = {"ladder": {"work": {"p": {"cut": {"cut": 0, "of": 5, "empty": 0},
                                      "did_not_finish": {"n": 2, "of": 5, "ceiling": 65536}}}}}
    assert report.disclosure45_cell({"disclosure45": d, "scorecard": card}) == \
        "thinking on · effort provider default · median 2.0k reasoning tokens · 0 cut · 2 did not finish · 0 empty"
