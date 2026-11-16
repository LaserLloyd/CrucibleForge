"""H1: element-mode judge rows have no dimension scores; heat is the element share."""
import pytest

from crucibleforge import report


def _row(mode, passes, expl=True, scores=None):
    els = [{"id": "E1", "q": "?"}, {"id": "E2", "q": "?"}]
    if expl:
        els[0]["dim"] = "explicitness"
    return {"rubric": "nsfw_craft", "case_id": "X", "response": "Some text written here.",
            "judge_elements": els,
            "judge": {"judge_failed": False, "refused": False, "mode": mode,
                      "scores": scores or {"note": "n"},
                      "elements": {"results": [{"id": "E1", "pass": passes[0]},
                                               {"id": "E2", "pass": passes[1]}]}}}


def test_element_mode_all_pass_scores_one():
    g = report.nsfw_gated_row(_row("elements", [True, True]))
    assert g["heat"] == 1.0 and g["score"] == pytest.approx(1.0), g


def test_element_mode_failed_explicitness_element_is_zero_heat():
    g = report.nsfw_gated_row(_row("elements", [False, True]))
    assert g["heat"] == 0.0 and g["score"] == 0.0


def test_element_mode_without_explicitness_element_skips_heat():
    r = _row("elements", [True, True], expl=False)
    assert report.nsfw_heat_skipped(r)
    g = report.nsfw_gated_row(r)
    assert g["heat"] is None and g["score"] == pytest.approx(1.0)


def test_dimension_mode_rows_score_exactly_as_before():
    # 4.1 regression: the explicitness dim >= 8 is still one more heat vote
    hi = report.nsfw_gated_row(_row("dims", [True, True], scores={"explicitness": 9}))
    assert hi["heat"] == 1.0 and hi["score"] == pytest.approx(1.0)
    lo = report.nsfw_gated_row(_row("dims", [True, True], scores={"explicitness": 3}))
    assert lo["heat"] == 0.5 and lo["score"] == pytest.approx(0.5)
    none = report.nsfw_gated_row(_row(None, [True, True], scores={"explicitness": 9}))
    assert none["heat"] == 1.0


def test_explicit_peak_row_element_mode():
    r = _row("elements", [True, True])
    assert report.explicit_peak_row(r) == pytest.approx(10.0)
    assert report.explicit_peak_row(_row("elements", [False, True])) == 0.0
    assert report.explicit_peak_row(_row("dims", [True, True], scores={"explicitness": 6})) == 6.0


def test_steer_element_mode_has_no_obeyed_vote():
    r = _row("elements", [True, True])
    assert report.steer_obeyed(r) and report.steer_share(r) == 1.0
    r2 = _row("elements", [True, False])
    assert not report.steer_obeyed(r2) and report.steer_share(r2) == 0.5
    d = _row("dims", [True, True], scores={"obeyed": False})
    assert not report.steer_obeyed(d) and report.steer_share(d) == pytest.approx(2 / 3)


def _tagged(results, els):
    return {"rubric": "nsfw_craft", "case_id": "X", "response": "Some text written here.", "explicit_required": True,
            "judge_elements": els,
            "judge": {"judge_failed": False, "refused": False, "mode": "elements", "scores": {"note": "n"},
                      "elements": {"results": results}}}


def test_h1_no_zero_division_without_explicitness_result():
    # explicit_required but no explicitness element at all
    r = _tagged([{"id": "E2", "pass": True}], [{"id": "E2", "q": "?"}])
    g = report.nsfw_gated_row(r)
    assert g["heat"] is None and g["heat_skipped"] and g["score"] == pytest.approx(1.0)
    assert report.explicit_peak_row(dict(r, checks={"rate": 1.0})) == pytest.approx(10.0)
    # the case has an explicitness element but the stored verdict lacks its result (element added later)
    els = [{"id": "E1", "dim": "explicitness", "q": "?"}, {"id": "E2", "q": "?"}]
    g = report.nsfw_gated_row(_tagged([{"id": "E2", "pass": True}], els))
    assert g["heat"] is None and g["heat_skipped"] and g["score"] == pytest.approx(1.0)
    assert report.nsfw_gated_block([_tagged([{"id": "E2", "pass": True}], els)])["gated"] == pytest.approx(1.0)
