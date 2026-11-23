"""The two live PACKS2 packs follow the one 4.5 ran-out rule (G2 g2-optc 59b914d): a cut reply is
left out, and more than a quarter of a set's calls cut reads "not measured".

G2's tests cover a 12-case pack at one sample each. These cover the two live packs as they ship:
work/c2_predict set-1 (three items, one sample) and work/c5_tools set-2 (two items x "repeats": 2,
so four calls; each sample is a call of its own)."""
from __future__ import annotations

import pytest

from crucibleforge import config, profiles, report, version


def _rc4(monkeypatch):
    # see test_rc3_option_c_20261115._active45: the board asks profiles.active_name()
    monkeypatch.setattr(profiles, "_ACTIVE", ["bench45"])
    monkeypatch.setattr(report, "_CASES_BY_ID", None)
    monkeypatch.setenv(profiles.COMPOSITION_ENV, "rc4-main")
    return profiles.load_profile("bench45")


def _rows(set_cases, repeats, cut):
    """One row per (case, sample); ``cut`` = the (case, sample) pairs that ran out."""
    cby = {c["id"]: c for c in config.load_cases()}
    out = []
    for cid in set_cases:
        for rep in range(1, repeats + 1):
            is_cut = (cid, rep) in cut
            out.append({"model_label": "m", "profile": "bench45", "category": cby[cid]["category"],
                        "case_id": cid, "repeat": rep, "case_revision": version.case_hash(cid),
                        "turn": None, "difficulty": cby[cid].get("difficulty", "hard"),
                        "finish_reason": "length" if is_cut else "stop", "truncated": is_cut,
                        "response": "partial" if is_cut else "ok", "grade": "fail" if is_cut else "pass",
                        "score": 0.0 if is_cut else 1.0})
    return out


def _set(prof, key, n):
    return next(x for x in report._ladder_sets(prof)[key] if x["n"] == n)


def _stats(prof, rows, k):
    st = report.stats_from_tier_rows("m", None, prof, {1: [], 2: rows, 3: []}, None, None)
    return st["ladder_stats"][k]


def _scored_n(ls):
    return sum(v.get("n") or 0 for v in ls.values() if isinstance(v, dict) and "n" in v)


@pytest.mark.live_ladder
@pytest.mark.parametrize("cut_reps,measured", [((), True), ((1,), True), ((1, 2), False)])
def test_tool_use_pack_counts_each_sample_as_a_call(monkeypatch, cut_reps, measured):
    """c5_tools set-2: LAG1 + LAG2 x 2 samples = 4 calls. One cut sample is 1/4 = 25% (not MORE than
    a quarter): left out, the other three score. Both samples of LAG1 cut = 2/4: not measured."""
    prof = _rc4(monkeypatch)
    s = _set(prof, "work/c5_tools", 2)
    assert s["repeats"] == 2 and s["cases"] == ["LAG1-potkeeper-yearend", "LAG2-seedbank-units"]
    cut = {("LAG1-potkeeper-yearend", r) for r in cut_reps}
    ls = _stats(prof, _rows(s["cases"], 2, cut), "work/c5_tools#2")
    ro = ls.get("ran_out_case")
    assert ro is not None and ro["rule"] == "exclude"
    assert ro["calls"] == 4 and ro["ran_out"] == len(cut)
    assert ls["not_measured"] is (not measured)
    assert _scored_n(ls) == (4 - len(cut) if measured else 0)


@pytest.mark.live_ladder
@pytest.mark.parametrize("cut_n,measured", [(0, True), (1, True), (2, False)])
def test_predict_pack_one_cut_reply_of_three_is_not_measured(monkeypatch, cut_n, measured):
    """c2_predict set-1: three items, one sample each. D10 as amended (fix round 2026-11-18): one cut reply of
    three is left out and the other two score; two cut replies (over a quarter, and at least two) read "not
    measured" (scores nothing) rather than counting the cut replies as fails."""
    prof = _rc4(monkeypatch)
    s = _set(prof, "work/c2_predict", 1)
    assert not s.get("repeats") and len(s["cases"]) == 3
    cut = {(cid, 1) for cid in s["cases"][:cut_n]}
    ls = _stats(prof, _rows(s["cases"], 1, cut), "work/c2_predict#1")
    ro = ls.get("ran_out_case")
    assert ro is not None and ro["rule"] == "exclude" and ro["calls"] == 3 and ro["ran_out"] == cut_n
    assert ls["not_measured"] is (not measured)
    assert _scored_n(ls) == (3 - cut_n if measured else 0)
