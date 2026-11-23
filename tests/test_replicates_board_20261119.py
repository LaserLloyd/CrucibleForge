"""H4: a model with several complete replicate runs is shown at the MEAN of them per part, section and Overall, with
its run-to-run term in the margin and "n runs, Overall a-b" in its notes; one run says "single run"."""
import pytest

from crucibleforge import headline as H
from crucibleforge import report as R
from crucibleforge import resolution45 as RS

PROF = {"headline": {"overall": "sum"},
        "scores": {"work": {"parts": {"c1": {"max": 5}}}, "chat": {"parts": {"h2_state": {"max": 1.5}}}}}
CARD = {"parts": {"chat": {"h2_state": 100.0}, "work": {"c1": 80.0}}, "chat": 1.5, "work": 4.0, "total": 5.5,
        "units": "points"}


def _runs():
    return {"ref": [{"work": 4.0, "chat": 1.5, "overall": 9.0, "work/c1": 4.0},
                    {"work": 4.4, "chat": 1.5, "overall": 9.6, "work/c1": 4.4},
                    {"work": 4.2, "chat": 1.5, "overall": 9.3, "work/c1": 4.2}],
            "one": [{"work": 3.0, "chat": 1.0, "overall": 4.0, "work/c1": 3.0}]}


def test_the_precision_block_carries_means_ranges_and_run_counts():
    r = RS.run_to_run(_runs())
    assert r["runs"] == {"ref": 3, "one": 1}
    assert r["means"]["ref"]["overall"] == pytest.approx(9.3)
    assert r["ranges"]["ref"]["overall"] == [9.0, 9.6]


def _s():
    return {"segments45": {"code": {"points": 4.0, "pack_points": 0.0, "segments": {}},
                           "chat": {"points": 1.5, "pack_points": 0.0, "segments": {}},
                           "overall": {"points": 5.5, "pending": []}},
            "headline": {"S": {}, "P": {}, "T1": {}}}


def test_three_runs_show_the_mean_and_the_range():
    # step 8: the mean of runs is applied on the board's own scale (report._replicate_means45), not by summarize
    r = RS.run_to_run({"ref": [{"code": 4.0, "chat": 1.5, "overall": 9.0}, {"code": 4.4, "chat": 1.5, "overall": 9.6},
                               {"code": 4.2, "chat": 1.5, "overall": 9.3}]})
    s = _s()
    R._replicate_means45("ref", s, r)
    hl = s["headline"]
    assert hl["S"]["overall"] == 9.3 and s["segments45"]["code"]["points"] == pytest.approx(4.2)
    assert hl["sort"] == pytest.approx(9.3)                       # the board sorts on the mean too
    assert R.replicate_note(hl).startswith("3 runs") and "Overall 9.0–9.6" in R.replicate_note(hl)
    assert "Overall 9.0–9.6" in R.notes_cell({"headline": hl})


def test_one_run_says_single_run_and_keeps_its_own_row():
    r = RS.run_to_run({"one": [{"code": 3.0, "chat": 1.0, "overall": 4.0}]})
    s = _s()
    R._replicate_means45("one", s, r)
    assert R.replicate_note(s["headline"]) == "single run"
    assert s["segments45"]["code"]["points"] == 4.0                 # not replaced by a mean


def test_no_precision_file_changes_nothing(monkeypatch):
    monkeypatch.setattr(R, "run_to_run_result", lambda: None)
    assert R.replicate_info("ref") is None
    hl = H.summarize(CARD, None, PROF)
    assert "replicates" not in hl and R.replicate_note(hl) is None


def test_the_margin_of_a_replicated_model_includes_its_run_term():
    r = RS.run_to_run({"ref": [{"overall": 9.0, "work": 4.0}, {"overall": 9.6, "work": 4.4}]})
    pr = H.model_precision("ref", PROF, None, r)
    assert pr["run_sd"]["overall"] == pytest.approx(0.4243, abs=1e-3) and "own (2 runs)" in pr["run_source"]["overall"]
