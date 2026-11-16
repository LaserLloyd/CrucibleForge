"""The Lockout label on the 4.x board (lead 2026-11-14, N2's flags.lockout_label, 814250e): the Uncensored
parts line and the Lockout component cell print what the ladder did ("Willingness 3.6 of 6: wrote 6 of 10,
first refusal LK01"), so a model that refuses the first rung but writes six of ten no longer reads like a
contradiction; a model whose adult page did not run reads "not run". Toy run as test_adult_page_board."""
from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

from crucibleforge import lockout, report

_spec = importlib.util.spec_from_file_location("_ad", Path(__file__).with_name("test_adult_page_board_20261114.py"))
_ad = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_ad)
_toys = _ad._toys

LOCKED = "locked at LK01 · wrote 6 of 10"


def test_the_label_reads_as_a_sentence():
    assert report.lockout_text(LOCKED) == "wrote 6 of 10, first refusal LK01"
    assert report.lockout_text("open · wrote 10 of 10") == "wrote 10 of 10, no refusal"
    assert report.lockout_text("locked at LK03 · wrote 4 of 10 (2 not run)") == \
        "wrote 4 of 10 (2 not run), first refusal LK03"
    # the older stop-at-first-refusal wording (rung_label without results)
    assert report.lockout_text("locked at LK01 (0/10)") == "first refusal LK01 (0 of 10 written)"
    assert report.lockout_text("open (10/10)") == "no refusal (10 of 10 written)"
    assert report.lockout_text("something else") == "something else"


def _board(tmp_path, monkeypatch, pol, label, adult=True):
    """The adult toy with its willingness part keyed "lockout" and a Lockout flag column whose
    ladder answers ``label`` (None = not run)."""
    real = _ad._prof

    def prof_(a):
        p = real(a)
        nsfw = p["scores"]["nsfw"]["parts"]
        nsfw["lockout"] = nsfw.pop("w")
        if a:
            p["chains"]["nsfw/lockout"] = p["chains"].pop("nsfw/w")
        p["score_subtotals"]["uncensored"]["parts"] = ["lockout", "k"]
        p["flags"] = {"lockout": {"label": "Lockout", "file": "pyproject.toml"}}
        return p
    monkeypatch.setattr(_ad, "_prof", prof_)
    monkeypatch.setattr(lockout, "board_value", lambda prof, l, rows=None: None if label is None else 60)
    monkeypatch.setattr(lockout, "board_label", lambda prof, l, rows=None: label)
    return _ad._board(tmp_path, monkeypatch, pol, adult=adult)


POL = lambda lv, cid: "nope" if cid.startswith(("FLAT-xa-004", "FLAT-xw-003", "FLAT-xw-004")) else "PASS"   # noqa: E731


def test_the_uncensored_line_says_what_the_ladder_did(tmp_path, monkeypatch):
    md, stats, html = _board(tmp_path, monkeypatch, POL, LOCKED)
    assert stats["m"]["flags"]["lockout_label"] == LOCKED
    v = stats["m"]["scorecard"]["subtotals"]["uncensored"]["parts_points"]["lockout"]
    want = f"Willingness {v:.1f} of 6: wrote 6 of 10, first refusal LK01"
    line = next(x for x in md.splitlines() if x.startswith("| m |") and "Uncensored " in x and " = " in x)
    assert want in line and "Lockout " not in line.split("Uncensored", 1)[1]
    d = json.loads(re.search(r'<script type="application/json" id="board-data">(.*?)</script>', html, re.S).group(1))
    r = d["rows"][0]
    assert want in r["points_cell"]
    assert r["comp_text"]["Lockout"] == "wrote 6 of 10, first refusal LK01"


def test_the_lockout_component_cell_carries_the_label():
    st = {"m": {"scorecard": {"components": {"lockout": 60.0}},
                "flags": {"lockout": 60, "lockout_label": LOCKED}},
          "n": {"scorecard": {"components": {"lockout": None}},
                "flags": {"lockout": None, "lockout_label": None}}}
    md = report._components_md([], ["m", "n"], st, {"nsfw": {"lockout": 1.0}})
    assert "| m | 60 (wrote 6 of 10, first refusal LK01) |" in md
    assert "| n | not run |" in md


def test_a_model_without_the_adult_page_reads_not_run(tmp_path, monkeypatch):
    md, stats, html = _board(tmp_path, monkeypatch, lambda lv, cid: "PASS", None, adult=False)
    assert stats["m"]["flags"]["lockout_label"] is None
    assert "Uncensored not run" in md and "Willingness" not in md
    d = json.loads(re.search(r'<script type="application/json" id="board-data">(.*?)</script>', html, re.S).group(1))
    assert d["rows"][0]["comp_text"]["Lockout"] == "not run"
