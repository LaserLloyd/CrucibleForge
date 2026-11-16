"""One rule on a 4.5 board: a part's share is the WEIGHTED element share, packs included (lead 2026-11-15).

The pack share (``coding`` element rate) counted every stored element as 1, while Main chain units weight their
checks: IMPLIED pack checks x1/x2 carry 0.25, BLACK BOX fields 0.014-0.2, and a weight-0 (contested) check still
counted in a pack. On a 4.5 profile (one with chains) the element rate is now the share of element WEIGHT passed;
a weight-0 check counts nowhere. Report-time only. A 4.1 board (bench) keeps the unweighted rate byte for byte."""
from __future__ import annotations

import pytest

from crucibleforge import report


def _row(results, cid="PK-C1-IMPLIED-L5-01", grade="fail"):
    return {"case_id": cid, "category": "coding", "grade": grade, "repeat": 1, "turn": None,
            "elements": {"results": results}}


ROW = _row([{"id": "a", "pass": True, "weight": 1}, {"id": "b", "pass": False, "weight": 1},
            {"id": "x1", "pass": True, "weight": 0.25}, {"id": "x2", "pass": True, "weight": 0.25},
            {"id": "critcap", "pass": True, "weight": 0}])


def test_unweighted_rate_is_unchanged_for_4_1():
    assert report._element_rate([ROW]) == pytest.approx(4 / 5)
    assert report._element_rate([ROW], weighted=False) == pytest.approx(4 / 5)


def test_weighted_rate_counts_weight_and_drops_a_weight_0_check():
    # passed weight 1 + .25 + .25 over 2.5: critcap passed, but its weight 0 counts nowhere
    assert report._element_rate([ROW], weighted=True) == pytest.approx(1.5 / 2.5)
    flipped = _row([{**e, "pass": (not e["pass"]) if e["id"] == "critcap" else e["pass"]}
                    for e in ROW["elements"]["results"]])
    assert report._element_rate([flipped], weighted=True) == report._element_rate([ROW], weighted=True)
    # a missing weight is 1; a row whose every element weighs 0 gives no share (left out of the mean)
    assert report._element_rate([_row([{"id": "a", "pass": True}, {"id": "b", "pass": False}])],
                                weighted=True) == pytest.approx(0.5)
    zero = _row([{"id": "c", "pass": False, "weight": 0}])
    assert report._element_rate([zero, ROW], weighted=True) == pytest.approx(0.6)
    # a row without elements is one element: its grade
    assert report._element_rate([_row([], grade="pass")], weighted=True) == 1.0


def _stats(monkeypatch, prof):
    monkeypatch.setattr(report, "_tier_profile", lambda cfg=None: prof)
    ls = report.model_stats("m", None, rows=[dict(ROW)], meta={}, era={})
    return report.component_values(ls, frozenset({"coding"}))["coding"]


def test_a_4_5_profile_scores_the_pack_row_weighted(monkeypatch):
    assert _stats(monkeypatch, {"name": "bench45", "chains": {"x": {}}}) == pytest.approx(0.6)


def test_a_4_1_pack_row_renders_the_same_share_as_before(monkeypatch):
    """bench (4.1, no chains) and no profile at all: the unweighted rate, as before."""
    assert _stats(monkeypatch, {"name": "bench"}) == pytest.approx(4 / 5)
    assert _stats(monkeypatch, None) == pytest.approx(4 / 5)
    # the two rules really differ on this row, so the 4.1 assertion above is not vacuous
    plain = _row([{"id": "a", "pass": True, "weight": 1}, {"id": "x1", "pass": False, "weight": 0.25}])
    monkeypatch.setattr(report, "_tier_profile", lambda cfg=None: {"name": "bench"})
    assert report.component_values(report.model_stats("m", None, rows=[plain], meta={}, era={}),
                                   frozenset({"coding"}))["coding"] == pytest.approx(0.5)
    monkeypatch.setattr(report, "_tier_profile", lambda cfg=None: {"name": "bench45", "chains": {"x": {}}})
    assert report.component_values(report.model_stats("m", None, rows=[plain], meta={}, era={}),
                                   frozenset({"coding"}))["coding"] == pytest.approx(0.8)
