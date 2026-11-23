"""R4 (fix round 2026-11-18, D2 as amended): in 4.5.0 the adult page prints no NSFW number, rank or sort key: the NSFW
cell (and the NSFW + Uncensored total) reads "not measured in 4.5.0"; Uncensored prints its measured points as
"x of y measured", never ranked; the adult page is never added to Overall. Toy board (release_seed 4.5.0): no model,
no rig."""
from __future__ import annotations

import re

from crucibleforge import report

from _lane_r_toy import PASS, board, cell, head, html_data, prof45, row, toys  # noqa: F401

W = report.ADULT_WITHHELD45


def test_the_nsfw_cell_is_withheld_and_uncensored_reads_measured(tmp_path, monkeypatch):
    md, stats, _ = board(tmp_path, monkeypatch, {"m": PASS})
    assert cell(md, "m", "NSFW (/10)") == W
    u = cell(md, "m", "Uncensored (/10)")
    assert u == "10.0 of 10 measured, not ranked", u


def test_no_nsfw_number_anywhere_in_the_markdown(tmp_path, monkeypatch):
    md, stats, _ = board(tmp_path, monkeypatch, {"m": PASS})
    pts = next(x for x in md.splitlines() if x.startswith("| m |") and "NSFW" in x)
    assert f"NSFW {W}" in pts and "N1" not in pts and "N2" not in pts, pts
    for x in md.splitlines():
        if x.startswith("| m | nsfw/n"):
            cells = [c.strip() for c in x.split("|")]
            assert cells[3].startswith("- /") and cells[9] == "-" and "4.00" not in x and "6.00" not in x, x


def test_the_html_board_carries_no_nsfw_number(tmp_path, monkeypatch):
    _md, stats, html = board(tmp_path, monkeypatch, {"m": PASS})
    r = html_data(html)["rows"][0]
    assert r["s"].get("adult_nsfw") is None and r.get("adult_nsfw") is None and r.get("nsfw") is None
    assert r["cell_text"]["adult_nsfw"] == W
    assert r["s"]["adult_uncensored"] == 10.0 and "not ranked" in r["sub_tag"]["adult_uncensored"]
    assert "N1 " not in (r.get("points_cell") or "") and f"NSFW {W}" in r["points_cell"]
    assert (r.get("measured") or {}).get("nsfw", {}).get("points") is None


def test_the_adult_page_is_never_added_to_overall(tmp_path, monkeypatch):
    _md, stats, _ = board(tmp_path, monkeypatch, {"m": PASS})
    hl = stats["m"]["headline"]
    assert hl["S"]["overall"] == hl["S"]["work"] + hl["S"]["chat"] == 20.0


def test_nsfw_is_never_a_sort_key():
    """Two models equal on Overall, Coding and Chat: the adult score never orders them (the label does)."""
    def st(nsfw):
        defs = [{"key": "work", "group": "coding", "in_total": True}, {"key": "chat", "group": "chat", "in_total": True},
                {"key": "nsfw", "group": "nsfw", "in_total": False}]
        return {"coverage": {}, "scorecard": {"score_defs": defs, "coding": 5.0, "chat": 5.0, "nsfw": nsfw},
                "headline": {"sort": 10.0}}
    assert report.rank_labels(["a", "b"], {"a": st(1.0), "b": st(9.0)}) == ["a", "b"]
    assert report.rank_labels(["b", "a"], {"a": st(9.0), "b": st(1.0)}) == ["a", "b"]


def test_a_4_1_shaped_adult_page_still_prints_its_numbers():
    """The withholding is the 4.5.0 rule only (release_seed 4.5.x with chains); an older board keeps its numbers."""
    assert report.adult_withheld45(prof45())
    assert not report.adult_withheld45({**prof45(), "release_seed": "4.6.0"})
    assert not report.adult_withheld45({"scores": {}})
