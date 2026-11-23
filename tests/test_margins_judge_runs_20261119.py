"""lane-j step 3: the printed margins carry a judge term and a run-to-run term; the resolution script recovers
known variance components from synthetic replicate runs."""
import math
import random

import pytest

from crucibleforge import headline as H
from crucibleforge import resolution45 as RS

PROF = {"headline": {"overall": "sum"},
        "scores": {"work": {"parts": {"c1": {"max": 5}}}, "chat": {"parts": {"j_rplong": {"max": 3, "tag": "judged"},
                                                                          "h2_state": {"max": 1.5}}}}}


def _gauss(n, mu, s, seed):
    r = random.Random(seed)
    return [mu + r.gauss(0, s) for _ in range(n)]


# ------------------------------------------------------------------ run-to-run term
def test_run_to_run_recovers_the_true_spread_and_pools():
    rnd = random.Random(1)
    true = {"chat": 0.30, "overall": 0.50}
    pts = {f"m{i}": [{k: 5 + i + rnd.gauss(0, s) for k, s in true.items()} for _ in range(8)] for i in range(6)}
    pts["single"] = [{"chat": 4.0, "overall": 9.0}]
    r = RS.run_to_run(pts)
    assert r["pooled"]["chat"] == pytest.approx(0.30, rel=0.15)
    assert r["pooled"]["overall"] == pytest.approx(0.50, rel=0.15)
    assert r["runs"]["single"] == 1 and r["by_model"]["single"]["chat"] is None
    assert r["pooled_models"] == 6


def test_a_model_with_one_run_gets_the_pooled_term_flagged():
    runs = {"by_model": {"a": {"chat": 0.4}, "b": {"chat": None}}, "runs": {"a": 3, "b": 1},
            "pooled": {"chat": 0.25}, "pooled_models": 4}
    pa = H.model_precision("a", PROF, None, runs)
    pb = H.model_precision("b", PROF, None, runs)
    assert pa["run_sd"]["chat"] == 0.4 and pa["run_source"]["chat"].startswith("own (3 runs)")
    assert pb["run_sd"]["chat"] == 0.25 and "pooled" in pb["run_source"]["chat"]


def test_run_excess_makes_the_spread_the_larger_of_the_two_never_their_sum():
    base = {"chat": _gauss(4000, 5.0, 0.20, 3), "overall": _gauss(4000, 9.0, 0.60, 4)}
    samples = {k: list(v) for k, v in base.items()}
    comp = H._add_run_excess(samples, base, {"run_sd": {"chat": 0.50, "overall": 0.30}}, "m")
    assert H._sd(samples["chat"]) == pytest.approx(0.50, rel=0.05)        # run spread larger: it wins
    assert H._sd(samples["overall"]) == pytest.approx(0.60, rel=0.05)     # bootstrap larger: unchanged
    assert samples["overall"] == base["overall"]
    assert comp["chat"]["run_sd"] == 0.50


# ------------------------------------------------------------------ judge term
def test_judge_term_from_the_marks_result_per_model_else_pooled():
    marks = {"parts": {"chat/j_rplong": {"judge_sd": {"a": 0.05}, "judge_sd_pooled": 0.04, "reader": "r"}}}
    pa = H.model_precision("a", PROF, marks, None)
    pb = H.model_precision("b", PROF, marks, None)
    assert pa["judge_sd"]["chat/j_rplong"] == 0.05 and pa["judge_pts"]["chat/j_rplong"] == pytest.approx(0.15)
    assert pb["judge_sd"]["chat/j_rplong"] == 0.04 and "pooled" in pb["judge_source"]["chat/j_rplong"]
    assert pa["judge_pts"]["chat"] == pytest.approx(0.15) and pa["judge_pts"]["overall"] == pytest.approx(0.15)
    assert H.model_precision("a", PROF, None, None) is None


def _boot(samples, comp):
    return {"samples": samples, "samples_items": {k: list(v) for k, v in samples.items()}, "gate_pass": {},
            "n": len(next(iter(samples.values()))), "precision": comp}


def test_no_judged_part_prints_a_margin_smaller_than_its_judge_term():
    card = {"parts": {"chat": {"j_rplong": 70.0, "h2_state": 100.0}, "work": {"c1": 80.0}},
            "chat": None, "work": None, "total": None, "units": "points"}
    tight = _gauss(1000, 2.1, 0.01, 5)
    comp = {k: {"items_repeats_sd": 0.01, "run_sd": None, "judge_sd": None} for k in
            ("work", "chat", "overall", "work/c1", "chat/j_rplong", "chat/h2_state")}
    comp["chat/j_rplong"]["judge_sd"] = 0.20                  # points: judge term 1.645 x 0.20 = 0.33
    samples = {k: list(tight) for k in comp}
    hl = H.summarize(card, _boot(samples, comp), PROF)
    m = hl["m"]["chat/j_rplong"]
    assert m >= round(H.Z90 * 0.20, 1)
    p = hl["precision"]["chat/j_rplong"]
    assert p["judge"] == pytest.approx(H.Z90 * 0.20, abs=1e-3) and p["printed"] == m
    assert set(p) >= {"items_repeats", "judge", "run_to_run", "combined", "rule"}
    assert hl["m"]["chat/h2_state"] == 0.1                    # an unjudged part keeps its bootstrap margin


def test_boot_model_without_precision_is_unchanged(monkeypatch):
    """No measurement file = the exact samples of before (the 4.1 board stays byte-identical)."""
    import inspect
    sig = inspect.signature(H.boot_model)
    assert sig.parameters["precision"].default is None


# ------------------------------------------------------------------ resolution
def _replicates(mu, sb, sr, runs, n, seed):
    rnd = random.Random(seed)
    out = []
    for _ in range(runs):
        off = rnd.gauss(0, sr)
        out.append({"chat": [mu + off + rnd.gauss(0, sb) for _ in range(n)]})
    return out


def test_resolution_recovers_the_gap_from_known_components():
    sb, sr = 0.20, 0.30
    sm = {"a": _replicates(6.0, sb, sr, 40, 2000, 1), "b": _replicates(5.0, sb, sr, 40, 2000, 2)}
    res = RS.resolve(sm, ["chat"])["chat"]
    sd_d = math.sqrt(2 * sb * sb + 2 * sr * sr)
    want = (1.6449 + 1.2816) * sd_d
    assert res["median"] == pytest.approx(want, rel=0.12)
    one_run = {"a": _replicates(6.0, sb, sr, 1, 2000, 1), "b": _replicates(5.0, sb, sr, 1, 2000, 2)}
    # one run each: the bootstrap cannot see the run term -> a much smaller (over-confident) gap
    assert RS.resolve(one_run, ["chat"])["chat"]["median"] < 0.8 * want


def test_reference_share_and_render():
    pts = {"ref-27b": [{"chat": 6.0}, {"chat": 7.0}], "x": [{"chat": 3.0}]}
    assert RS.reference_share(pts, "ref-27b", {"chat": 10.0})["chat"] == pytest.approx(0.65)
    txt = RS.render({"chat": {"median": 1.2, "max": 1.5, "pairs": {}}}, {"chat": 0.65},
                    RS.run_to_run(pts), "ref-27b")
    assert "65%" in txt and "1.20" in txt


def test_margin_note_says_what_the_margin_includes():
    from crucibleforge import report as R
    none = {"a": {"headline": {}}}
    assert "not measured yet" in R.margin_note45(["a"], none)
    both = {"a": {"headline": {"precision": {"chat": {"judge": 0.2, "run_to_run": 0.3,
                                                      "run_source": "pooled (2 models)"}}}}}
    t = R.margin_note45(["a"], both)
    assert "second reader" in t and "pooled" in t and "replicate" in t


# ------------------------------------------------------------------ follow-up: resolution reads the board as gated
def test_marks_file_named_by_env_is_the_one_the_board_gate_reads(tmp_path, monkeypatch):
    """resolution45 --marks must gate the parts with the same file it takes the judge term from (it read the
    folder's judge_marks.json for the gate, so judged parts were pending and chat resolved on 3 of 10 points)."""
    import json as _j
    from crucibleforge import report as R
    monkeypatch.setattr(R, "results_dir", lambda: tmp_path / "none")
    R._AGREEMENT_CACHE.clear()
    m = tmp_path / "marks.json"
    from crucibleforge.agree_marks import marks_sha256
    m.write_text(_j.dumps({"marks_sha256": marks_sha256(), "parts": {"chat/j_rpsession": {"pass": True}}}),
                 encoding="utf-8")
    monkeypatch.setenv(R.MARKS_ENV, str(m))
    prof = {"judge_agreement": "required", "scores": {"chat": {"parts": {"j_rpsession": {"tag": "judged"}}}}}
    assert R.part_pending_validation(prof, "chat", "j_rpsession") is None
    monkeypatch.delenv(R.MARKS_ENV)
    R._AGREEMENT_CACHE.clear()
    assert R.part_pending_validation(prof, "chat", "j_rpsession") == R.AGREEMENT_NOT_MEASURED


def test_render_names_a_section_measured_on_part_of_its_maximum():
    res = {"chat": {"median": 0.13, "max": 0.13, "pairs": {}}}
    txt = RS.render(res, {"chat": 1.0}, {"pooled": {}, "runs": {"a": 1}}, "a", measured={"chat": 3.0},
                    maxima={"chat": 10.0})
    assert "3 of 10" in txt and "pending" in txt
    # the reference share is of what was measured
    pts = {"ref": [{"chat": 3.0}]}
    assert RS.reference_share(pts, "ref", {"chat": 10.0}, measured={"chat": 3.0})["chat"] == pytest.approx(1.0)


# step 8 (2026-11-23): the old-tree maxima of the separation table (scored_pack_points) left with the old
# folder bootstrap; the release reads board-scale points from each folder's report (test_step8_scoring_20261123).
