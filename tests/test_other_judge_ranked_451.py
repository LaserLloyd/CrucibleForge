"""Owner 2026-11-24 ("allow any user to use any judge they want, just note our scores on MakoBench are from a local
Q8 model"): a row judged by another judge is scored, ranked and marked on the user's own board; a board mixing
judges says so above the table; ``report --public`` still refuses it unless ``--allow-other-judge``; a board judged
by the profile's judge alone renders as before."""
from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

from crucibleforge import report

_spec = importlib.util.spec_from_file_location(
    "_e2e_oj", Path(__file__).with_name("test_routed_ladder_e2e_20261114.py"))
_e2e = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_e2e)
_toys = _e2e._toys          # the toy families (autouse fixture of test_chains_20261113)

PASS = lambda lv, cid: "nope" if "-ax-" in cid or cid.startswith("PKA") else "PASS"   # noqa: E731
OTHER = "vendor/some-judge"
NOTE = f"judged by {OTHER}; MakoBench's published scores use"


def _render(tmp_path, monkeypatch, other_of):
    _prof, cfg, _calls, _summary, rc = _e2e._drive(tmp_path, monkeypatch, {"m": PASS, "n": PASS})
    assert rc == 0
    real = report.other_judge_ids
    monkeypatch.setattr(report, "other_judge_ids",
                        lambda label, c, rows=None: list(other_of.get(label, [])) or real(label, c, rows))
    md = report.generate("m,n", write=False, cfg=cfg)
    _, stats, _ = report.board_stats("m,n", cfg)
    html = report.render_report_html(["m", "n"], stats, cfg)
    return md, stats, html


def _data(html):
    m = re.search(r'<script type="application/json" id="board-data">(.*?)</script>', html, re.S)
    return json.loads(m.group(1))


def _row(data, label):
    rows = data["rows"] if isinstance(data, dict) else data
    return next(r for r in rows if r["label"] == label)


def test_a_row_judged_by_another_judge_is_ranked_marked_and_the_board_says_judges_differ(tmp_path, monkeypatch):
    md, stats, html = _render(tmp_path, monkeypatch, {"n": [OTHER]})
    n, m = stats["n"], stats["m"]
    assert n["other_judge45"]["ids"] == [OTHER] and "other_judge45" not in m
    assert not n.get("provisional") and not n.get("held45")
    assert {n["headline"]["rank"], m["headline"]["rank"]} <= {"1", "1="}       # same rows, same score: tied, both ranked
    assert n["headline"]["S"]["overall"] == m["headline"]["S"]["overall"]
    # md: the mark in the Overall cell, the note in the row's notes and details, the mixed line above the table
    assert f"> {report.MIXED_JUDGES_LINE}" in md
    line = next(x for x in md.splitlines() if x.startswith("|") and "| n |" in x)
    assert f" {report.OTHER_JUDGE_MARK}" in line and NOTE in line and "not ranked" not in line
    assert f"{report.OTHER_JUDGE_MARK} {NOTE}" in md
    # html: the banner line and the row's fields
    assert report.MIXED_JUDGES_LINE in html
    d = _data(html)
    rn, rm = _row(d, "n"), _row(d, "m")
    assert rn["overall"] is not None and "not_ranked" not in rn and NOTE in rn["other_judge"] and NOTE in rn["notes"]
    assert "other_judge" not in rm


def test_one_other_judge_on_every_row_marks_rows_without_the_mixed_line(tmp_path, monkeypatch):
    md, stats, html = _render(tmp_path, monkeypatch, {"m": [OTHER], "n": [OTHER]})
    assert report.MIXED_JUDGES_LINE not in md and report.MIXED_JUDGES_LINE not in html
    assert all(stats[l]["headline"]["rank"] != "-" for l in "mn")


def test_a_board_of_the_profiles_judge_alone_is_unchanged(tmp_path, monkeypatch):
    md, stats, html = _render(tmp_path, monkeypatch, {})
    assert report.MIXED_JUDGES_LINE not in md and report.MIXED_JUDGES_LINE not in html
    assert report.OTHER_JUDGE_MARK not in md and report.OTHER_JUDGE_MARK not in html
    assert all("other_judge45" not in stats[l] for l in "mn")
    assert all("other_judge" not in r for r in (_data(html)["rows"] if isinstance(_data(html), dict) else _data(html)))


def test_the_mixed_rule():
    st = {"a": {}, "b": {"other_judge45": {"ids": ["x"]}}, "c": {"other_judge45": {"ids": ["y"]}}}
    assert report.mixed_judges45(["a", "b"], st) and report.mixed_judges45(["b", "c"], st)
    assert not report.mixed_judges45(["a"], st) and not report.mixed_judges45(["b"], st)


def test_a_public_render_still_holds_other_judge_rows_unless_allowed(monkeypatch):
    monkeypatch.setattr(report, "_tier_profile", lambda cfg: {"judge": {"model_id": "board-judge"}})
    monkeypatch.setattr(report, "active_name", lambda cfg=None: "bench45")
    row = {"judge_ladder": {"complete": True, "instrument": {"judge_model": OTHER}}}
    pub = {"_audience": report.PUBLIC}
    assert any("judged by" in h for h in report.held45([row], {}, pub))
    assert not any("judged by" in h for h in report.held45([row], {}, {**pub, "_allow_other_judge": True}))
    assert not any("judged by" in h for h in report.held45([row], {}, {}))
