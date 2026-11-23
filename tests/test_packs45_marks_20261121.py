"""Stamp-2 item A: the Chat-pack marks P1/P2/P3 are evaluated and the board reads them (packs45.board_packs,
scripts/check_pack_marks45.py). Since Part 2 (owner ruling 2026-11-21) the marks print as notes only, never a gate."""
from __future__ import annotations

import itertools
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from crucibleforge import packs45  # noqa: E402

CP = pytest.importorskip("check_pack_marks45") if (ROOT / "scripts/check_pack_marks45.py").is_file() else None
need_script = pytest.mark.skipif(CP is None, reason="scripts/check_pack_marks45.py not written yet")

SEGS = packs45.SEGMENTS
MARKS_FILE = ROOT / "calibration/pack-marks-chat-45-addendum-20261120.json"


def _res(status, reason=""):
    return {"status": status, "reason": reason}


def marks(p1="pass", p2="pass", p3="pass", p4="pass"):
    return {"P1_reader_agreement": _res(p1, "" if p1 == "pass" else f"P1 {p1}"),
            "P4_key_audit_code_levels": _res(p4),
            "segments": {s: {"P2_known_order": _res(p2, "" if p2 == "pass" else f"P2 {p2}"),
                             "P3_not_maxed": _res(p3, "" if p3 == "pass" else f"P3 {p3}")} for s in SEGS}}


def tb(share=1.0):
    mx = {"character": 4.0, "remembers": 3.5, "cowrites": 1.5}
    return {"segments": {s: {"points": mx[s] * share, "max": mx[s], "complete": True} for s in SEGS}}


def scores_for(seg, mean=0.8, n_repeats=1):
    """Both-reader scene scores for every judged scene of ``seg`` (n_repeats units per scene)."""
    out = {}
    for case, (s, _part, _lvl) in packs45.PACK_SCENES.items():
        if s != seg:
            continue
        for r in range(n_repeats):
            out[f"r|{case}|{r + 1}"] = {"case": case, "segment": seg, "judge": mean, "reader": mean, "mean": mean,
                                        "status": "ok", "reason": None}
    return out


def all_scores(mean=0.8):
    out = {}
    for s in SEGS:
        out.update(scores_for(s, mean))
    return out


# ---- Part 2 (owner ruling 2026-11-21, INTENT-45 item 12): the pack marks are notes, never a gate. A pack needs only
# its unlock (the top of the model's own range at the threshold), a judged result, and a score. The cases below
# replaced the fail-closed gate tests of stamp 2 item A.
def test_marks_pass_gives_scored_points_equal_to_the_judged_share():
    out = packs45.board_packs(tb(), all_scores(0.8), {}, marks=marks())
    for s in SEGS:
        assert out[s]["state"] == "scored", out[s]
        assert out[s]["points"] == pytest.approx(0.8)


def test_without_marks_an_unlocked_pack_still_scores():
    out = packs45.board_packs(tb(), all_scores(0.8), {})
    assert out["character"]["state"] == "scored" and out["character"]["points"] == pytest.approx(0.8)


@pytest.mark.parametrize("p1,p2,p3", list(itertools.product(("pass", "fail", "not_evaluable", None), repeat=3)))
def test_no_mark_state_changes_the_pack_number(p1, p2, p3):
    m = marks()
    if p1 is None:
        del m["P1_reader_agreement"]
    else:
        m["P1_reader_agreement"] = _res(p1, "x")
    seg = m["segments"]["character"]
    for key, st in (("P2_known_order", p2), ("P3_not_maxed", p3)):
        if st is None:
            del seg[key]
        else:
            seg[key] = _res(st, "x")
    out = packs45.board_packs(tb(), all_scores(0.8), {}, marks=m)["character"]
    assert out["state"] == "scored" and out["points"] == pytest.approx(0.8)
    # P1 (second reader) and P2 (known order) are gone from the board; a failed P3 prints as a note only
    assert not any("P1" in n or "P2" in n or "known-order" in n for n in out["notes"])
    if p3 in ("fail", "not_evaluable"):
        assert out["notes"]


def test_a_locked_segment_stays_locked_whatever_the_marks_say():
    out = packs45.board_packs(tb(0.5), all_scores(0.8), {}, marks=marks())
    assert all(v["state"] == "locked" and v["points"] == 0.0 for v in out.values())


def test_a_maxed_pack_prints_at_least_whatever_p3_says():
    out = packs45.board_packs(tb(), all_scores(1.0), {}, marks=marks(p3="fail"))
    assert out["character"]["state"] == "scored" and out["character"]["at_least"] is True


def test_a_code_level_counts_whatever_p4_says():
    sc = all_scores(0.95)
    a = packs45.board_packs(tb(), sc, {"character": 0.5}, marks=marks())["character"]["points"]
    b = packs45.board_packs(tb(), sc, {"character": 0.5}, marks=marks(p4="fail"))["character"]["points"]
    assert a == pytest.approx(b)


# ---- the evaluator (scripts/check_pack_marks45.py)
@need_script
def test_p3_boundary_and_none():
    assert CP.p3_for_segment("character", {"a": 0.96})["status"] == "pass"
    r = CP.p3_for_segment("character", {"a": 0.97, "b": 0.5})
    assert r["status"] == "fail" and "MAXED" in r["reason"]
    assert CP.p3_for_segment("character", {})["status"] == "not_evaluable"


@need_script
def test_p1_is_the_largest_term_over_the_models_measured():
    ok = {"state": "measured", "n": 9, "term": 0.02}
    bad = {"state": "measured", "n": 9, "term": 0.06}
    assert CP.p1_mark({"a": ok, "b": bad})["status"] == "fail"
    assert CP.p1_mark({"a": ok})["status"] == "pass"
    few = {"state": "not_evaluable", "n": 3, "term": None}
    assert CP.p1_mark({"a": few})["status"] == "not_evaluable"
    assert CP.p1_mark({"a": few, "b": bad})["status"] == "fail"       # an unmeasured model never hides a measured one
    # and the board (Part 2): a failed P1 holds nothing; the pack still scores
    m = marks()
    m["P1_reader_agreement"] = CP.p1_mark({"a": ok, "b": bad})
    out = packs45.board_packs(tb(), all_scores(0.8), {}, marks=m)
    assert all(v["state"] == "scored" and v["points"] == pytest.approx(0.8) for v in out.values())


@need_script
def test_p2_not_evaluable_without_anchor_runs_names_the_missing_roles():
    r = CP.p2_for_segment("character", ["model-a", "model-b"], {"model-a": {"LRV1-pilots-mess": 0.9}})
    assert r["status"] == "not_evaluable"
    assert r["reason"].startswith("P2 known-order pair: order not tested (no run of sonnet-class, haiku-class")
    r = CP.p2_for_segment("character", ["claude-sonnet-5", "model-b"], {})
    assert "no run of haiku-class" in r["reason"]


@need_script
def test_p2_role_identity_is_a_substring_of_the_label_case_insensitive():
    assert CP.role_labels(["Claude-Sonnet-5-5", "claude-haiku-4", "gpt-6"], "sonnet") == ["Claude-Sonnet-5-5"]
    assert CP.role_labels(["claude-haiku-4"], "sonnet") == []


@need_script
def test_p2_passes_only_when_the_pair_is_separated_beyond_the_combined_margin():
    """The pre-written mark: the stronger must score above the weaker BY MORE THAN the combined margin."""
    cases = ["LRV1-pilots-mess", "LRV3-hollis-van"]
    lab = ["claude-sonnet-5", "claude-haiku-4"]
    rev = {"claude-sonnet-5": {c: 0.2 for c in cases}, "claude-haiku-4": {c: 0.9 for c in cases}}
    r = CP.p2_for_segment("character", lab, rev)
    assert r["status"] == "fail" and r["reason"].startswith("P2 known-order pair: order reversed")
    near = {"claude-sonnet-5": {c: 0.50 for c in cases}, "claude-haiku-4": {c: 0.55 for c in cases}}
    r = CP.p2_for_segment("character", lab, near)                      # inside the margin, reversed side
    assert r["status"] == "fail" and r["reason"].startswith("P2 known-order pair: not separated (diff ")
    near2 = {"claude-sonnet-5": {c: 0.55 for c in cases}, "claude-haiku-4": {c: 0.50 for c in cases}}
    r = CP.p2_for_segment("character", lab, near2)                     # inside the margin, right side
    assert r["status"] == "fail" and "not separated (diff" in r["reason"] and "combined margin" in r["reason"]
    tie = {"claude-sonnet-5": {c: 0.5 for c in cases}, "claude-haiku-4": {c: 0.5 for c in cases}}
    assert CP.p2_for_segment("character", lab, tie)["status"] == "fail"
    good = {"claude-sonnet-5": {c: 0.9 for c in cases}, "claude-haiku-4": {c: 0.3 for c in cases}}
    r = CP.p2_for_segment("character", lab, good)
    assert r["status"] == "pass" and r["measured"]["state"] == "separated"


@need_script
def test_p2_a_difference_exactly_equal_to_the_combined_margin_fails(monkeypatch):
    monkeypatch.setattr(CP, "segment_margin", lambda seg, label, means: 0.3 if "sonnet" in label else 0.4)
    lab = ["claude-sonnet-5", "claude-haiku-4"]
    exact = {"claude-sonnet-5": {"LRV1-pilots-mess": 0.75}, "claude-haiku-4": {"LRV1-pilots-mess": 0.25}}
    r = CP.p2_for_segment("character", lab, exact)                     # diff 0.5 == sqrt(0.3^2 + 0.4^2)
    assert r["status"] == "fail" and "not separated" in r["reason"]
    over = {"claude-sonnet-5": {"LRV1-pilots-mess": 0.76}, "claude-haiku-4": {"LRV1-pilots-mess": 0.25}}
    assert CP.p2_for_segment("character", lab, over)["status"] == "pass"


@need_script
def test_p2_margin_is_the_headline_margin_of_a_seeded_scene_bootstrap_combined_as_in_the_pair_rule():
    from crucibleforge import headline
    cases = ["LCC1-quiet-hours", "LCC2-last-boxes", "LCC3-left-luggage"]
    means = {"LCC1-quiet-hours": 0.2, "LCC2-last-boxes": 0.9, "LCC3-left-luggage": 0.6}
    m1 = CP.segment_margin("remembers", "claude-sonnet-5", means)
    assert m1 == CP.segment_margin("remembers", "claude-sonnet-5", means)          # seeded: repeatable
    assert isinstance(m1, float) and m1 >= 0.1                                       # headline.margin's floor
    # own stream per (segment, label): the pack draws never share a stream with another label's
    assert CP.stream_seed("remembers", "a") != CP.stream_seed("remembers", "b")
    assert CP.stream_seed("remembers", "a") != CP.stream_seed("cowrites", "a")
    assert CP.combined_margin(0.3, 0.4) == pytest.approx(0.5)                        # sqrt(ma^2 + mb^2)
    assert headline.margin([0.0, 1.0]) is not None                                   # the function reused


# ---- hardening: every mark id in the marks file has an evaluator
@need_script
def test_every_mark_id_in_the_pack_marks_file_has_an_evaluator():
    ids = set(json.loads(MARKS_FILE.read_text(encoding="utf-8"))["marks"])
    assert ids == set(CP.EVALUATORS), "a mark with no evaluator (or an evaluator for no mark)"
    assert all(callable(f) for f in CP.EVALUATORS.values())


@need_script
def test_evaluate_stores_every_mark_id_and_a_pass_needs_no_invention(tmp_path):
    scores = {"a": all_scores(0.8)}
    blk = CP.evaluate_block(scores, ["a"], code_shares={}, p4=_res("pass"), p5=_res("pass"))
    assert "P1_reader_agreement" in blk and "P4_key_audit_code_levels" in blk and "P5_unlock" in blk
    for s in SEGS:
        assert {"P2_known_order", "P3_not_maxed"} <= set(blk["segments"][s])
    # today's data: no anchors -> P2 not evaluable for every segment; since Part 2 that holds no pack
    assert all(blk["segments"][s]["P2_known_order"]["status"] == "not_evaluable" for s in SEGS)
    out = packs45.board_packs(tb(), scores["a"], {}, marks=blk)
    assert all(v["state"] == "scored" and v["points"] == pytest.approx(0.8) for v in out.values())


# ---- role identity addendum (identity only, no threshold)
def test_role_identity_addendum_is_indexed_dated_and_states_when_it_was_written():
    idx = (ROOT / "calibration/MARKS-INDEX-45.md").read_text(encoding="utf-8")
    name = "pack-marks-chat-45-addendum-20261120b.json"
    assert f"calibration/{name}" in idx
    d = json.loads((ROOT / "calibration" / name).read_text(encoding="utf-8"))
    text = json.dumps(d)
    assert d["written"] == "2026-11-20"
    assert "after launch rows of non-anchor models had been graded in part" in text
    assert "before any Sonnet- or Haiku-class pack row existed" in text
    assert "changes no threshold" in text
    assert d["role_identity"]["sonnet-class"] == "sonnet" and d["role_identity"]["haiku-class"] == "haiku"
    assert d["clarifies"] == ["pack-marks-chat-45-addendum-20261120.json"]
    assert set(d) <= {"about", "written", "written_before_launch_rows", "written_after_launch_rows_graded_in_part",
                      "written_before_any_sonnet_or_haiku_pack_row", "clarifies", "role_identity", "note"}
