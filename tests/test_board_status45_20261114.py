"""The routed board's status banner (lead 2026-11-14, Jake: rc1 is an internal candidate only):
the profile's ``board_notes.status`` prints at the top of the text and html boards; under it, when
any pack added points AND the profile marks its packs provisional (``board_notes.packs_provisional``,
default off; Jake approved the packs 2026-11-14, so rc3-main sets nothing), "Bonus is provisional:
packs await the owner's review."; a board whose
status marks it an internal candidate hides the Class column (Lean stays). K's toy routed run
(test_routed_ladder_e2e_20261114): no model, no rig."""
from __future__ import annotations

import importlib.util
import json
import re
from html import escape
from pathlib import Path

from crucibleforge import report

_spec = importlib.util.spec_from_file_location(
    "_e2e", Path(__file__).with_name("test_routed_ladder_e2e_20261114.py"))
_e2e = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_e2e)
_toys = _e2e._toys

CAND = "internal candidate: the reference sits near the top; packs are staged pending the owner's review"
PROV = "Bonus is provisional: packs await the owner's review."
# K's toy: Main cleared, work/main opens its pack and scores it (bonus 1.0), work/aux fails
BONUS = lambda lv, cid: "nope" if "-ax-" in cid or cid.startswith("PKA") else "PASS"   # noqa: E731
NONE = lambda lv, cid: "nope"                                                            # noqa: E731


def _board(tmp_path, monkeypatch, pol, notes):
    prof, cfg, _calls, _summary, rc = _e2e._drive(tmp_path, monkeypatch, {"m": pol})
    assert rc == 0
    if notes is not None:
        prof["board_notes"] = notes
    md = report.generate("m", write=False, cfg=cfg)
    _, stats, _ = report.board_stats("m", cfg)
    html = report.render_report_html(["m"], stats, cfg)
    return md, stats, html


def _data(html):
    m = re.search(r'<script type="application/json" id="board-data">(.*?)</script>', html, re.S)
    return json.loads(m.group(1)) if m else None


def test_an_internal_candidate_prints_its_status_and_the_provisional_bonus(tmp_path, monkeypatch):
    md, stats, _html = _board(tmp_path, monkeypatch, BONUS, {"status": CAND, "work_main": "x", "packs_provisional": True})
    assert stats["m"]["headline"]["bonus45"]["work"] == 1.0
    sc = report.scorecard_section(md)                    # the staged/published block carries it too
    for text in (md, sc):
        assert f"> **Status:** {CAND}" in text
        assert f"> {PROV}" in text
        # the banner sits at the top, above the table
        assert text.index("> **Status:**") < text.index(f"> {PROV}") < text.index("| # | Model |")


def test_an_internal_candidate_hides_class_and_keeps_lean(tmp_path, monkeypatch):
    md, stats, _html = _board(tmp_path, monkeypatch, BONUS, {"status": CAND})
    hl = stats["m"]["headline"]
    std = hl["std45"]["work"]
    assert "| Overall (/12 = 10 standard + 2 packs) | Lean | Work (/12 = 10 standard + 2 packs) |" in md
    assert "| Class |" not in md
    assert f"| 1 | m | {report.headline_cell(hl, 'overall')} /12 = {std:.1f} + 1.0 bonus | - |" in md
    # its legend goes with it: no Class band text anywhere on the board
    assert "Class = a coarse capability band" not in md and "Class bands" not in md
    assert "Lean = (Chat" in md                          # Lean keeps its legend


def test_no_pack_points_no_provisional_line(tmp_path, monkeypatch):
    md, stats, html = _board(tmp_path, monkeypatch, NONE, {"status": CAND, "packs_provisional": True})
    assert not (stats["m"]["headline"].get("bonus45") or {}).get("work")
    assert f"> **Status:** {CAND}" in md
    assert PROV not in md and escape(PROV) not in html


def test_approved_packs_score_without_the_provisional_line(tmp_path, monkeypatch):
    # Jake approved the packs (2026-11-14): no profile key, so a scoring pack prints no provisional line;
    # the internal-candidate banner and the hidden Class column stay exactly as they were
    md, stats, html = _board(tmp_path, monkeypatch, BONUS, {"status": CAND})
    assert stats["m"]["headline"]["bonus45"]["work"] == 1.0
    for text in (md, report.scorecard_section(md)):
        assert f"> **Status:** {CAND}" in text and PROV not in text
    assert escape(PROV) not in html and '<div class="banner" role="note">' in html
    assert "| Class |" not in md and "| Lean |" in md
    assert "class" not in [c["key"] for c in _data(html)["main"]]
    # an explicit false is the same as no key
    notes = lambda v: {"routing": {"on": True}, "board_notes": {"status": CAND, "packs_provisional": v}}  # noqa: E731
    assert report.banner45(notes(True), ["m"], stats) == [CAND, PROV]
    assert report.banner45(notes(False), ["m"], stats) == [CAND]
    assert report.banner45(notes(None), ["m"], stats) == [CAND]


def test_a_status_that_is_not_an_internal_candidate_keeps_class(tmp_path, monkeypatch):
    md, _stats, _html = _board(tmp_path, monkeypatch, BONUS, {"status": "release candidate 2"})
    assert "> **Status:** release candidate 2" in md
    assert "| Class | Lean |" in md and "Class = a coarse capability band" in md


def test_no_status_no_banner_and_the_board_is_unchanged(tmp_path, monkeypatch):
    md, _stats, html = _board(tmp_path, monkeypatch, BONUS, None)
    assert "**Status:**" not in md and PROV not in md
    assert "| Class | Lean |" in md
    assert 'class="banner"' not in html


def test_the_html_board_carries_the_banner_and_drops_class(tmp_path, monkeypatch):
    _md, _stats, html = _board(tmp_path, monkeypatch, BONUS, {"status": CAND + " <b>&", "packs_provisional": True})
    assert '<div class="banner" role="note">' in html
    assert "internal candidate: the reference sits near the top" in html
    assert "&lt;b&gt;&amp;" in html and "<b>&" not in html.split('<div class="banner"', 1)[1][:600]
    assert escape(PROV) in html
    data = _data(html)
    keys = [c["key"] for c in data["main"]]
    assert "class" not in keys and "lean" in keys
    assert all(r.get("class") is None for r in data["rows"])
    assert "Class = a coarse capability band" not in html


def test_the_html_board_keeps_class_without_the_candidate_mark(tmp_path, monkeypatch):
    _md, _stats, html = _board(tmp_path, monkeypatch, BONUS, {"status": "release candidate 2",
                                                             "packs_provisional": True})
    assert '<div class="banner" role="note">' in html and escape(PROV) in html
    assert "class" in [c["key"] for c in _data(html)["main"]]


# --- the non-routed 4.x board (rc2 drops routing, lead 2026-11-14): G2's toy fixed-set run ------------------------

import types  # noqa: E402

from crucibleforge import cli, config, ladder, profiles, routing  # noqa: E402

_tc = _e2e._tc
RC2 = "internal candidate rc2: fixed set, no routing; Chat's judged parts (7 of 10 points) pending"


def _flat_prof():
    """rc2-main's shape in miniature (G2, tests/test_rc2_main_20261114.py): two code-graded parts with a pack
    each, one judged part with no items, no routing."""
    prof = _tc._flatprof([{"family": "toychain", "level": 1, "count": 10, "name": "mn"}])
    prof["chains"]["work/aux"] = {"flat": [{"family": "toychain", "level": 1, "count": 10, "name": "ax"}]}
    prof["scores"]["work"]["parts"]["main"]["max"] = 5
    prof["scores"]["work"]["parts"]["aux"] = {"max": 3, "scoring": ["coding"], "cases": []}
    prof["scores"]["work"]["parts"]["judged"] = {"max": 2, "scoring": ["rpdeep"], "cases": []}
    prof["ladder"] = {"unlock": 0.9}
    prof["cases"] = {"coding": []}
    prof["scores"].pop("chat", None)
    return prof


def _flat_board(tmp_path, monkeypatch, pol, notes):
    prof = _flat_prof()
    assert routing.spec(prof) is None and not report.routed(prof)
    if notes is not None:
        prof["board_notes"] = notes
    root = tmp_path / "ladder"
    for part, tag in (("main", "PKM"), ("aux", "PKA")):
        d = root / "work" / part
        d.mkdir(parents=True)
        cases = [{"id": f"{tag}-{i}", "category": "coding",
                  "generator": {"type": "toychain", "level": 2, "key": f"{tag.lower()}{i}"}} for i in (1, 2)]
        (d / "set-1.json").write_text(json.dumps({"level": 1, "points": 1, "cases": cases}), encoding="utf-8")
    monkeypatch.setattr(ladder, "roots", lambda *a, **k: [root])
    monkeypatch.setattr(profiles, "load_profile", lambda name=None, cfg=None: prof)
    base_cfg = _tc._cfg
    monkeypatch.setattr(_tc, "_cfg", lambda p, **kw: {**base_cfg(p, **kw), "_profile": "t"})
    _tc._run(tmp_path, monkeypatch, prof, {"m": pol})
    cfg = _tc._cfg(prof)
    cfg["models"] = [{"name": "m", "model_id": "m", "provider": "p"}]
    args = types.SimpleNamespace(smoke=False, profile="t", no_tiers=False, no_ladder=False, no_judge=True,
                                 skip_conformance=True, escalate=False, judge=None, force_evict=False)
    monkeypatch.setattr(cli, "_escalate_phase", lambda *a, **k: 0)
    monkeypatch.setattr(cli, "_ProviderGuard", lambda *a, **k: types.SimpleNamespace(restore=lambda: None))
    assert cli._ladder_phases(args, cfg, ["m"]) == 0
    md = report.generate("m", write=False, cfg=cfg)
    _, stats, _ = report.board_stats("m", cfg)
    return md, stats, report.render_report_html(["m"], stats, cfg), config


def test_the_non_routed_board_prints_the_status_bonus_line_and_hides_class(tmp_path, monkeypatch):
    # main 9 of 10 = 0.90 -> its pack runs and scores; aux 0.8 -> locked (G2's toy)
    pol = lambda lv, cid: ("nope" if cid.startswith(("FLAT-mn-010", "FLAT-ax-009", "FLAT-ax-010"))   # noqa: E731
                           else "PASS")
    md, stats, html, _ = _flat_board(tmp_path, monkeypatch, pol, {"status": RC2, "packs_provisional": True})
    assert report.pack_points_added(stats["m"], False)
    for text in (md, report.scorecard_section(md)):
        assert f"> **Status:** {RC2}" in text and f"> {PROV}" in text
        assert text.index("> **Status:**") < text.index("| # | Model |")
    head = next(x for x in md.splitlines() if x.startswith("| # | Model |"))
    assert "| Class |" not in head and "| Lean |" in head
    assert "Class = a coarse capability band" not in md and "Lean = (Chat" in md
    assert '<div class="banner" role="note">' in html and escape(PROV) in html and escape(RC2) in html
    assert "class" not in [c["key"] for c in _data(html)["main"]]
    assert "Class = a coarse capability band" not in html


def test_the_non_routed_board_without_pack_points_has_no_bonus_line(tmp_path, monkeypatch):
    # every answer fails: no pack opens, no bonus line; the status still prints
    md, stats, html, _ = _flat_board(tmp_path, monkeypatch, lambda lv, cid: "nope", {"status": RC2})
    assert not report.pack_points_added(stats["m"], False)
    assert f"> **Status:** {RC2}" in md and PROV not in md and escape(PROV) not in html


def test_the_non_routed_board_without_a_status_is_unchanged(tmp_path, monkeypatch):
    md, _s, html, _ = _flat_board(tmp_path, monkeypatch, lambda lv, cid: "PASS", None)
    assert "**Status:**" not in md and 'class="banner"' not in html
    head = next(x for x in md.splitlines() if x.startswith("| # | Model |"))
    assert "| Class | Lean |" in head


def test_the_non_routed_board_with_approved_packs_has_no_bonus_line(tmp_path, monkeypatch):
    # rc3-main's case: packs score, the profile sets no packs_provisional -> status + hidden Class, no line
    pol = lambda lv, cid: ("nope" if cid.startswith(("FLAT-mn-010", "FLAT-ax-009", "FLAT-ax-010"))   # noqa: E731
                           else "PASS")
    md, stats, html, _ = _flat_board(tmp_path, monkeypatch, pol, {"status": RC2})
    assert report.pack_points_added(stats["m"], False)
    assert f"> **Status:** {RC2}" in md and PROV not in md and escape(PROV) not in html
    head = next(x for x in md.splitlines() if x.startswith("| # | Model |"))
    assert "| Class |" not in head and "| Lean |" in head
