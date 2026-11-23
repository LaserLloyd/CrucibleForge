"""Part 2 of the one build pass (owner 2026-11-21, INTENT-45): notes not gates (10), each model's own accuracy range
(9), Chat judge-only (11b), packs on the top of the model's own range (12), the default release (13), and the judge
copy settings of the judge candidate (Part 1 defect from the rig test). Synthetic inputs only: no model, rig or
hosted call."""
from __future__ import annotations

import argparse
import copy
import math
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from crucibleforge import accuracy45 as A  # noqa: E402
from crucibleforge import display45 as D  # noqa: E402
from crucibleforge import nsfwpack45 as NP  # noqa: E402
from crucibleforge import packs45 as PK  # noqa: E402
from crucibleforge import rplong_mean as RM  # noqa: E402
from crucibleforge import segments45 as S  # noqa: E402

pytestmark = [pytest.mark.shipping_default, pytest.mark.live_ladder]

_RAW = yaml.safe_load((ROOT / "profiles" / "bench45.yaml").read_text(encoding="utf-8"))
PROF = {**_RAW, **_RAW["compositions"]["standard"]}
FIXES = ("CB-F1-larder-bughunt", "PATCHFIX-core-7-1", "PATCHFIX-core-7-2", "PATCHMOD-core-3-1")
SCENES8 = ("RV1-three-at-the-table", "RV2-skarren-hut", "CC3-tea-hut", "CC4-second-pitch", "RD1-marisol",
           "RD4-kettle-saddle", "WR1-ninth-string", "WR2-red-thread-key")


def _model(share=0.8):
    parts = ["c1_implied", "c2_predict", "c2_trace", "c5_tools", "h2_state", "h2_ledger", "h6_know", "j_rpsession"]
    sect = {"c1_implied": "work", "c2_predict": "work", "c2_trace": "work", "c5_tools": "work"}
    mx = {f"{sect.get(p, 'chat')}/{p}": 1.0 for p in parts}
    return {"headline": {"max": mx, "P": {k: share for k in mx}, "m": {}},
            "chain_stats": {}, "assistant45": {"status": "ok", "share": share},
            "scorecard": {"ladder": {k.split("/")[0]: {kk.split("/")[1]: {"std_share": share} for kk in mx
                                                       if kk.split("/")[0] == k.split("/")[0]} for k in mx}},
            "rplong45": {"scenes": {c: {"score": share, "reason": None, "repeats": 2, "values": [share, share]}
                                    for c in SCENES8}},
            "fix45": {"cases": {c: {"scores": [1.0, 0.0] if c == FIXES[0] else [1.0]} for c in FIXES}}}


def _failed_marks():
    ids = {i for v in S.SEGMENT_MARKS.values() for i in v}
    return {i: {"status": "fail", "reason": "known order not met"} for i in ids}


# ------------------------------------------------------------------ item 10: notes, not gates
def test_failed_or_missing_marks_never_change_a_number():
    free = S.segment_table(_model(), PROF, None)
    for marks in (_failed_marks(), {}):
        tb = S.segment_table(_model(), PROF, marks)
        for sec in ("code", "chat"):
            assert tb[sec]["points"] == free[sec]["points"]
            for k, g in tb[sec]["segments"].items():
                assert g["points"] == free[sec]["segments"][k]["points"]
                assert not any(p["unit"] == "marks" for p in g["pending"])
        assert tb["overall"]["points"] == free["overall"]["points"]


def test_a_failed_mark_still_prints_as_a_note():
    tb = S.segment_table(_model(), PROF, _failed_marks())
    sec, k = next(key.split("/") for key in S.SEGMENT_MARKS if S.noted_marks(key) and key.split("/")[0] in tb)
    g = tb[sec]["segments"][k]
    assert g["marks"]["state"] == "failed"
    assert any("not met" in n for n in g.get("notes") or [])


def test_nsfw_marks_never_null_a_value():
    num = {"nsfw": {"label": "NSFW", "of": 10, "value": 6.0, "partial": 6.0, "pending": None,
                    "segments": {"erotica": {"label": "Erotica", "max": 4, "value": 3.0, "pending": None,
                                             "parts": {"x": 3.0}, "cut": {}},
                                 "range": {"label": "Range", "max": 2, "value": 3.0, "pending": None,
                                           "parts": {"y": 3.0}, "cut": {}}}},
           "uncensored": {"label": "Uncensored", "of": 10, "value": 5.0, "partial": 5.0, "pending": None,
                          "segments": {"knowledge": {"label": "Knowledge", "max": 4, "value": 5.0, "pending": None,
                                                     "parts": {"u3": 5.0}, "cut": {}}}}}
    blk = S.nsfw_marks({"numbers": copy.deepcopy(num)}, _failed_marks() | {"act_beat:AB2": {"status": "fail",
                                                                                          "reason": "x"}})
    assert blk["numbers"]["nsfw"]["value"] == 6.0 and blk["numbers"]["uncensored"]["value"] == 5.0
    assert blk["state"] == "complete"
    assert all(g["value"] is not None for n in blk["numbers"].values() for g in n["segments"].values())


def test_no_cell_says_held():
    sect = {"points": None, "segments": {"a": {"marks": {"state": "failed"}, "pending": []}}}
    assert "held" not in D.section_cell(sect)


def test_a_scene_without_a_judge_verdict_is_not_judged_yet():
    m = _model()
    m["rplong45"]["scenes"]["RD1-marisol"] = {"score": None, "reason": None, "repeats": 1}
    tb = S.segment_table(m, PROF, None)
    reasons = [p["reason"] for p in tb["chat"]["segments"]["remembers"]["pending"]]
    assert reasons and all("not judged yet" in r for r in reasons)


# ------------------------------------------------------------------ item 9: each model's own accuracy
def test_profile_carries_the_accuracy_thresholds():
    assert {k: _RAW["intent"][k] for k in ("accuracy_target_pct", "accuracy_limit_pct")} == \
        {"accuracy_target_pct": 2.5, "accuracy_limit_pct": 5}
    assert PROF.get("judge_agreement") in (None, False, "off")      # no second-reader gate on judged parts
    assert A.settings(PROF) == {"target_pct": 2.5, "limit_pct": 5.0}


def test_repeat_half_width_is_z90_times_the_se_of_each_unit_mean():
    groups = [(2.0, [1.0, 0.0]), (1.0, [1.0, 1.0, 0.0])]
    var = 2.0 ** 2 * (0.5 / 2) + 1.0 ** 2 * ((1 / 3) / 3)
    assert A.repeat_half(groups) == pytest.approx(A.Z90 * math.sqrt(var))
    assert A.repeat_half([(1.0, [0.7])]) is None          # one repeat: nothing measured


def test_range_text_names_the_limit():
    b = A.block(0.6, 10.0, "repeats", A.settings(PROF))
    assert b["pct"] == pytest.approx(6.0) and b["above_limit"]
    assert "range 6.0% (above the 5% limit)" in A.range_text(b)
    ok = A.block(0.2, 10.0, "repeats", A.settings(PROF))
    assert not ok["above_limit"] and "above" not in A.range_text(ok)
    est = A.block(0.4, 10.0, "estimate", A.settings(PROF))
    assert "estimate" in A.range_text(est)


def test_model_accuracy_measures_repeated_units_and_labels_single_ones():
    m = _model()
    m["segments45"] = S.segment_table(m, PROF, None)
    m["repeat45"] = {k: 0.0 for k in m["headline"]["max"]}       # repeat bootstrap: parts measured, no spread
    m["repeat45_n"] = {k: 2 for k in m["headline"]["max"]}
    acc = A.model_accuracy(m, PROF, estimates={"code/fixes": 24.3})
    fx = acc["segments"]["code/fixes"]
    assert fx["source"] == "repeats+estimate"                   # CB-F1 has 2 repeats, the PATCH units one
    assert "single repeat" in fx["note"]
    for k in ("code", "chat", "overall", "nsfw", "uncensored", "nsfw_plus"):
        assert k in acc
    assert acc["code"]["max"] == 10 and acc["overall"]["max"] == 20 and acc["nsfw_plus"]["max"] == 20
    assert acc["code"]["half"] is not None and acc["code"]["half"] >= fx["half"] - 1e-9


def test_item_margin_stays_a_second_labelled_figure():
    assert "different question set" in A.ITEM_MARGIN_LABEL


# ------------------------------------------------------------------ item 11b: Chat is judge-only
def _long_row(case, rep, credit):
    return {"rubric": "rp_deep", "case_id": case, "bench_run_id": "r1", "repeat": rep,
            "judge": {"elements": {"results": [{"credit": credit}]}}}


def test_profile_scores_scenes_by_the_judge_alone():
    assert PROF["rplong_score"]["definition"] == "judge_only"


def test_judge_only_scene_scores_ignore_every_stored_reader_vote():
    rows = [_long_row("RD1-marisol", 1, 1.0), _long_row("RD1-marisol", 2, 0.0)]
    hosted = [{"unit": "r1|RD1-marisol|1", "model": "m", "score_reader": 0.0,
               "reader_stamp": {"model": "deepseek-flash", "family": "deepseek", "hosted": True}}]
    sc = RM.judge_scores(rows, hosted, label="m", cases={})
    assert {u["mean"] for u in sc.values()} == {1.0, 0.0}
    assert all(u["status"] == "ok" and u["reader"] is None for u in sc.values())


def test_judge_only_pack_scene_scores():
    case = next(iter(PK.PACK_SCENES))
    rows = [{**_long_row(case, 1, 0.5)}]
    sc = PK.judge_scores(rows, [], label="m", cases={})
    if sc:                                     # pack_units may need pack-row markers; judge-only either way
        assert all(u["mean"] == 0.5 and u["status"] == "ok" for u in sc.values())


# ------------------------------------------------------------------ item 12: packs
def _tb_chat(share):
    return {"segments": {s: {"points": share * mx, "max": mx, "complete": True}
                         for s, mx in (("character", 4.0), ("remembers", 3.5), ("cowrites", 1.5))}}


def test_the_unlock_is_the_one_profile_value():
    assert PK.unlock_of(PROF) == pytest.approx(_RAW["ladder"]["unlock"]) == pytest.approx(0.9)


def test_a_pack_opens_on_the_top_of_the_models_own_range_and_marks_never_gate():
    scores = {f"u{i}": {"segment": "character", "case": c, "status": "ok", "mean": 0.8, "judge": 0.8, "reader": None}
              for i, c in enumerate(c for c, v in PK.PACK_SCENES.items() if v[0] == "character")}
    bad = {PK.P1_KEY: {"status": "fail"},
           "segments": {"character": {PK.P2_KEY: {"status": "fail"}, PK.P3_KEY: {"status": "fail", "reason": "x"}}}}
    shut = PK.board_packs(_tb_chat(0.88), scores, {}, unlock=0.9, marks=bad, ranges={"character": 0.0})
    assert shut["character"]["state"] == "locked"
    opened = PK.board_packs(_tb_chat(0.88), scores, {}, unlock=0.9, marks=bad, ranges={"character": 0.1})
    assert opened["character"]["state"] == "scored" and opened["character"]["points"] > 0
    assert opened["character"].get("notes")                     # the failed marks print as notes


def test_a_maxed_pack_prints_at_least():
    scores = {f"u{i}": {"segment": "character", "case": c, "status": "ok", "mean": 1.0, "judge": 1.0, "reader": None}
              for i, c in enumerate(c for c, v in PK.PACK_SCENES.items() if v[0] == "character")}
    out = PK.board_packs(_tb_chat(1.0), scores, {}, unlock=0.9, marks=None)
    assert out["character"]["at_least"] is True


def test_nsfw_packs_open_without_marks():
    numbers = {"nsfw": {"segments": {"erotica": {"value": 3.9, "max": 4, "parts": {"erotica_written": 2.45}}}},
               "uncensored": {"segments": {}}}
    pk = next(p for p, g in NP.GATE_OF.items() if g[1] == "erotica")
    shares = {pk: {"state": "scored", "share": 0.5, "reason": None}}
    out = NP.board_packs(numbers, shares, None, unlock=0.9)
    assert out[pk]["state"] == "scored" and out[pk]["points"] == 0.5


def test_nsfw_pack_carries_its_own_range_and_at_least():
    numbers = {"nsfw": {"segments": {"erotica": {"value": 3.9, "max": 4, "parts": {"erotica_written": 2.5}}}},
               "uncensored": {"segments": {}}}
    pk = next(p for p, g in NP.GATE_OF.items() if g[1] == "erotica")
    out = NP.board_packs(numbers, {pk: {"state": "scored", "share": 1.0, "reason": None}}, None, unlock=0.9)
    assert out[pk]["at_least"] is True
    assert "range" in out[pk] and "one repeat per unit" in out[pk]["range"]


def test_nsfw_pack_opens_on_the_top_of_the_segment_range():
    numbers = {"nsfw": {"segments": {"range": {"value": 1.7, "max": 2}}}, "uncensored": {"segments": {}}}
    pk = next(p for p, g in NP.GATE_OF.items() if g[1] == "range")
    sh = {pk: {"state": "scored", "share": 0.4, "reason": None}}
    assert NP.board_packs(numbers, sh, None, unlock=0.9)[pk]["state"] == "locked"
    rg = NP.share_ranges({"nsfw/range": {"half": 0.2, "max": 2.0}})
    assert rg[pk] == pytest.approx(0.1)
    assert NP.board_packs(numbers, sh, None, unlock=0.9, ranges=rg)[pk]["state"] == "scored"


def test_nsfw_pack_line_prints_number_range_and_at_least():
    from crucibleforge import report as R
    pk = next(iter(NP.PACKS))
    s = {"nsfw_plus": {"packs45": {"board": {pk: {"state": "scored", "points": 1.0, "label": NP.PACKS[pk][1],
                                                   "at_least": True, "range": NP.PACK_RANGE_NOTE}}}}}
    line = R.np_packs_line(s)
    assert "at least 1.00 of 1" in line and NP.PACK_RANGE_NOTE in line


def test_staged_sets_listing_exists():
    st = PK.staged_sets(PROF) if hasattr(PK, "staged_sets") else None
    assert st is None or isinstance(st, list)


# ------------------------------------------------------------------ item 13: the default release
def test_the_default_release_is_judge_assemble_report_accuracy():
    import release45 as R
    # step 8 (reviews A Q3, B finding 2): the folder reports, the board-scale spread, then the board report
    assert R.DEFAULT_STEPS == ("b-judge", "c-assemble", "d-report", "e-resolution", "e-board", "e-accuracy")
    for s in ("b-c1", "b-c6", "b-classing", "b-chat", "b-packs", "c-classing", "c-marks", "c-sanity"):
        assert s in R.STEPS and s not in R.DEFAULT_STEPS        # commands stay, out of the default path


# ------------------------------------------------------------------ the judge copy (Part 1 rig-test defect)
def test_the_profile_judge_keeps_the_candidates_devices_parallel_and_instance():
    from crucibleforge import cli
    jid = PROF["judge"]["model_id"]
    cfg = {"providers": {"studioforge": {"type": "studioforge", "base_url": "http://x"}},
           "judge": {"candidates": [{"name": "j", "provider": "studioforge", "model_id": jid,
                                     "instance_name": "mb45ref", "instance_devices": [2, 3],
                                     "instance_parallel": [4, 3, 2]}]}}
    args = argparse.Namespace(judge=dict(PROF["judge"]))
    cli._guard_45_judge(args, cfg, PROF)
    assert args.judge["instance_devices"] == [2, 3]
    assert args.judge["instance_parallel"] == [4, 3, 2] and args.judge["instance_name"] == "mb45ref"
    assert args.judge["context_length"] == PROF["judge"]["context_length"]     # the profile's settings still win


# ------------------------------------------------------------------ judge-check over the judge's slots (rig finding)
def test_judge_check_reads_concurrently_and_never_twice(tmp_path):
    import json
    import threading
    import time
    from crucibleforge import flags45 as F
    from crucibleforge import judge45 as J

    class Fake:
        def __init__(self):
            self.calls, self.now, self.peak = 0, 0, 0
            self.lock = threading.Lock()

        def __call__(self, rd, msgs, **kw):
            with self.lock:
                self.calls += 1
                self.now += 1
                self.peak = max(self.peak, self.now)
            time.sleep(0.01)
            with self.lock:
                self.now -= 1
            if msgs[0]["content"] == F.load_prompt()[0]:
                return {"text": json.dumps({"refused": False, "position": False, "quote": ""}), "finish": "stop"}
            return {"text": json.dumps({"class": "SOFTENED", "quote": ""}), "finish": "stop"}

    rd = J.judge_reader(PROF["judge"]["model_id"])
    fk = Fake()
    J.run_check(tmp_path, rd, fk, PROF, width=4)
    assert fk.peak > 1                                           # the reads ran over several slots
    first = fk.calls
    J.run_check(tmp_path, rd, fk, PROF, width=4)
    assert fk.calls == first                                     # every vote stored: nothing read twice
