"""Stamp-2 batch G, agent B: G3 (a model's value over the runs that carry the segment), G4 (the addendum's
fallback margin for long scenes, fix tasks and Everyday; a computed 0 is a stored margin), and anchors never
counting toward a launch ceiling or a launch pair.

Rule text: calibration/marks-evaluation-45-addendum-20261120.json, margins.fallback_without_spread and
margins.no_margin_zero; lead rulings R2, R3 (PLAN-STAMP2G-45-v2). Real inputs: the stored ``marks_input45``
segments of ref-r1 (has nsfw keys) and ref-r2 (none), numbers only, in tests/fixtures/."""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import check_marks45 as CM  # noqa: E402
from crucibleforge import segments45 as S  # noqa: E402

FIX = json.loads((ROOT / "tests" / "fixtures" / "stamp2g_b_ref_segments_20261121.json").read_text(encoding="utf-8"))
_RAW = yaml.safe_load((ROOT / "profiles" / "bench45.yaml").read_text(encoding="utf-8"))
PROF = {**_RAW, **_RAW["compositions"]["standard"]}
REF = FIX["ref-r1"]["label"]


def _runs(r1=True, r2=True, r3=True):
    out = []
    for name, use in (("ref-r1", r1), ("ref-r2", r2), ("ref-r3", r2 and r3)):
        if use:
            src = FIX["ref-r1" if name == "ref-r1" else "ref-r2"]
            out.append(CM.Run(Path(name), REF, {"segments": src["segments"]}))
    return out


# ------------------------------------------------------------------ G3
def test_g3_fixture_is_the_real_shape():
    assert any(k.startswith("nsfw/") for k in FIX["ref-r1"]["segments"])
    assert not any(k.startswith(("nsfw/", "unc/")) for k in FIX["ref-r2"]["segments"])


def test_g3_value_uses_the_runs_that_carry_the_segment():
    M = CM.Models(_runs(), {"reference": REF})            # r1 carries nsfw/range; r2 and r3 do not
    v = M.value(REF, "nsfw/range")
    assert v is not None
    want = FIX["ref-r1"]["segments"]["nsfw/range"]
    assert v["points"] == pytest.approx(want["points"]) and v["margin"] == pytest.approx(want["margin"])
    assert v["n"] == 1 and v["runs_used"] == 1 and v["runs_total"] == 3
    assert "1 of 3 runs carry this segment" in v["runs_note"]


def test_g3_a_segment_every_run_carries_uses_all_runs():
    M = CM.Models(_runs(), {"reference": REF})
    v = M.value(REF, "code/fixes")
    assert v["runs_used"] == 3 and v["runs_total"] == 3 and v["n"] == 3
    assert "3 of 3" in v["runs_note"]


def test_g3_a_run_with_the_key_but_no_value_still_gives_none():
    runs = _runs()
    runs[0].mi = {"segments": {**runs[0].mi["segments"],
                               "nsfw/range": {"points": None, "max": 2.0, "margin": None, "reason": "pending: x"}}}
    M = CM.Models(runs, {"reference": REF})
    assert M.value(REF, "nsfw/range") is None
    assert "pending" in M.why_missing(REF, "nsfw/range")


def test_g3_no_run_carries_it_is_none_with_a_reason():
    M = CM.Models(_runs(r1=False), {"reference": REF})
    assert M.value(REF, "nsfw/range") is None
    why = M.why_missing(REF, "nsfw/range")
    assert "no run carries" in why and "nsfw/range" in why


def test_g3_the_mark_line_says_how_many_runs_were_used():
    segs = FIX["ref-r1"]["segments"]
    other = [CM.Run(Path("d"), "other-model", {"segments": {k: {**v} for k, v in segs.items()}})]
    M = CM.Models(_runs() + other, {"reference": REF, "damaged": "other-model"})
    p = CM.pair_part(M, "nsfw/range", ["reference", ">", "damaged"], "primary", True)
    assert p["measured"]["a_runs"] == "1 of 3 runs carry this segment"
    assert p["measured"]["b_runs"] == "1 of 1 runs carry this segment"


# ------------------------------------------------------------------ G4
def test_g4_binomial_margin_numbers_from_the_plan():
    f = S.binomial_margin
    assert f(3.0, 1.0, 5) == 0.0                                     # Fixes shares all 1 -> 0.0, not None
    assert f(3.0, 0.8, 5) == pytest.approx(0.883, abs=5e-4)          # [1,1,1,1,0]
    assert f(3.0, 1 / 3, 5) == pytest.approx(1.040, abs=5e-4)        # ref-r1: 1.0 of 3 points
    assert f(3.0, 0.5, 0) is None


def _model(fix_scores, scenes=None, basics=None):
    sys.path.insert(0, str(ROOT / "tests"))
    from test_segments45_sum import _model as full, FIXES, SCENES8
    m = full(0.5)
    ids = ["CB-F1-larder-bughunt", "CB-F1-larder-bughunt"] + list(_G4_IDS[1:])
    cases: dict = {}
    for c, s in zip(ids, fix_scores):
        cases.setdefault(c, {"scores": [], "reason": None})["scores"].append(s)
    m["fix45"] = {"cases": cases}
    m["rplong45"] = {"scenes": {c: {"score": (scenes or {}).get(c, 0.6), "repeats": 1, "reason": None}
                                for c in SCENES8}}
    if basics is not None:
        m["assistant45"] = basics
    return m


#: the pooled-margin arithmetic on five equal units (no slices): the formula these tests pin. Step 2 (2026-11-21)
#: gave the shipping Fixes segment slices; its margin is the same bound with each unit's own weight
#: (test_g4_fixes_slices_weigh_the_margin below)
_G4_IDS = ("CB-F1-larder-bughunt", "PATCHFIX-core-4-1", "PATCHMINI-core-1-1", "PATCHMOD-core-3-1")
_G4_PROF = {**PROF, "segments45": {**PROF["segments45"], "code": {**PROF["segments45"]["code"], "fixes": {
    **{k: v for k, v in PROF["segments45"]["code"]["fixes"].items() if k != "slices"},
    "cases": list(_G4_IDS), "case_repeats": {_G4_IDS[0]: 2}}}}}


def _fixes(scores, prof=None):
    return S.segment_table(_model(scores), prof or _G4_PROF)["code"]["segments"]["fixes"]


def test_g4_fixes_slices_weigh_the_margin():
    """Unequal unit weights (slices): 1.645 x sqrt(p (1 - p) x sum of w^2), p = the weighted mean share; with equal
    weights this is the bound above."""
    slices = {"bughunt": {"points": 2.0, "match": ["CB-F1-"]}, "patch": {"points": 0.6, "match": ["PATCHFIX-", "PATCHMINI-"]},
              "patchmod": {"points": 0.4, "match": ["PATCHMOD-"]}}
    sl = {**_G4_PROF["segments45"]["code"]["fixes"], "slices": slices}
    prof = {**_G4_PROF, "segments45": {**_G4_PROF["segments45"], "code": {**_G4_PROF["segments45"]["code"], "fixes": sl}}}
    w = [1.0, 1.0, 0.3, 0.3, 0.4]                  # bug-hunt 2.0 / 2, patch 0.6 / 2, patchmod 0.4 / 1
    sh = [1, 0, 1, 0, 1]
    g = _fixes(sh, prof)
    p = sum(a * b for a, b in zip(w, sh)) / sum(w)
    assert g["points"] == pytest.approx(1.7)
    assert g["margin"] == pytest.approx(1.645 * math.sqrt(p * (1 - p) * sum(x * x for x in w)), abs=0.005)


def test_g4_fixes_all_ones_is_a_stored_margin_of_zero():
    assert _fixes([1, 1, 1, 1, 1])["margin"] == 0.0


def test_g4_fixes_one_miss_and_one_third():
    assert _fixes([1, 1, 1, 1, 0])["margin"] == pytest.approx(0.88, abs=0.005)
    # ref-r1: 1.0 of 3 points = a mean share of 1/3 over the 5 units (the old per-unit sum gave 0.66)
    assert _fixes([1, 0, 0, 0, 0])["margin"] == pytest.approx(1.645 * 3 * math.sqrt(0.2 * 0.8 / 5), abs=0.005)
    assert _fixes([1, 1, 0, 0, 0])["margin"] == pytest.approx(1.645 * 3 * math.sqrt(0.4 * 0.6 / 5), abs=0.005)


def test_g4_fixes_none_only_when_no_unit_is_measured():
    m = _model([1, 1, 1, 1, 1])
    m["fix45"] = {"cases": {}}
    assert S.segment_table(m, PROF)["code"]["segments"]["fixes"]["margin"] is None


def test_g4_everyday_uses_the_binomial_bound_over_the_items():
    # the stored 10-point margin of the basics block no longer decides (R2); n = its 15 items
    b = {"status": "ok", "share": 0.5, "margin": 10.0, "n_items": 15}
    ev = S.segment_table(_model([1] * 5, basics=b), PROF)["chat"]["segments"]["everyday"]
    assert ev["margin"] == pytest.approx(round(1.645 * 0.5 * math.sqrt(0.25 / 15), 2), abs=1e-9)
    b1 = {"status": "ok", "share": 1.0, "margin": 7.6, "n_items": 15}
    assert S.segment_table(_model([1] * 5, basics=b1), PROF)["chat"]["segments"]["everyday"]["margin"] == 0.0


def test_g4_long_scenes_keep_the_binomial_bound_and_a_certain_scene_is_a_stored_zero():
    m = _model([1] * 5, scenes={"WR1-ninth-string": 1.0, "WR2-red-thread-key": 1.0})
    assert S.segment_table(m, PROF)["chat"]["segments"]["cowrites"]["margin"] == 0.0
    m = _model([1] * 5, scenes={"WR1-ninth-string": 0.5, "WR2-red-thread-key": 1.0})
    m["rplong45"]["scenes"]["WR1-ninth-string"]["repeats"] = 4
    assert S.segment_table(m, PROF)["chat"]["segments"]["cowrites"]["margin"] == pytest.approx(
        round(1.645 * 0.75 * math.sqrt(0.25 / 4), 2), abs=1e-9)


# ------------------------------------------------------------------ anchors
def _seg(points, margin, mx):
    return {"points": points, "partial": points, "max": mx, "margin": margin, "complete": True, "reason": None}


def test_anchor_never_counts_toward_a_launch_ceiling():
    launch = CM.Run(Path("l"), "launch-model", {"segments": {"chat/everyday": _seg(1.0, 0.05, 1.0)}})
    anchor = CM.Run(Path("a"), "anchor-model", {"segments": {"chat/everyday": _seg(0.3, 0.05, 1.0)}}, anchor=True)
    M = CM.Models([launch, anchor], {"reference": "launch-model", "damaged": "anchor-model"})
    p = CM.ceiling_part(M, "chat/everyday", 1.0)
    assert p["status"] == CM.FAIL and p["measured"]["models"] == 1       # the anchor's 0.3 does not pass it
    assert M.labels == ["launch-model"] and M.anchor_labels == ["anchor-model"]


def test_anchor_is_not_a_launch_pair_member_but_everyday_checks_may_use_it():
    launch = CM.Run(Path("l"), "launch-model", {"segments": {"chat/everyday": _seg(1.0, 0.05, 1.0)}})
    anchor = CM.Run(Path("a"), "anchor-model", {"segments": {"chat/everyday": _seg(0.3, 0.05, 1.0)}}, anchor=True)
    M = CM.Models([launch, anchor], {"reference": "launch-model", "damaged": "anchor-model"})
    p = CM.pair_part(M, "chat/everyday", ["reference", ">", "damaged"], "primary", True)
    assert p["status"] == CM.NE and "damaged" in p["reason"]            # a launch pair names launch labels only
    ev1, ev2 = CM.share_marks(M, 0.9, 0.2)
    assert ev2["status"] == CM.PASS and ev2["measured"]["gap"] == pytest.approx(0.7)
