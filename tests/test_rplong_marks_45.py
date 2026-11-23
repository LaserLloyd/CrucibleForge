import hashlib
import json
import math
import pytest
from crucibleforge import rplong_marks as K


def _rec(case, run, j, r, model="m", reader="deepseek-flash+think"):
    return {"part": "chat/j_rplong", "model": model, "unit": f"{run}|{case}|1", "row_sha": "x", "reader": reader,
            "score_judge": j, "score_reader": r, "decisions": [[True, True]], "decision_ids": ["a"], "weight": 1.0,
            "graded_family": "qwen"}


def test_draft_marks_are_loaded_by_their_indexed_hash(tmp_path):
    m = K.load_marks()
    assert m["parts"]["chat/j_rplong"]["score"]["definition"] == "mean_of_two_readers"
    assert K.indexed_sha() == hashlib.sha256(K.DRAFT.read_bytes()).hexdigest().__str__()
    assert m["primary"]["max_judge_term_share"] == 0.05


def test_mean_of_two_term_is_single_term_over_root_two():
    recs = [_rec("CC3-tea-hut", "a", 0.8, 0.7), _rec("CC4-second-pitch", "a", 0.6, 0.65),
            _rec("RV1-three-at-the-table", "a", 0.9, 0.7), _rec("WR1-ninth-string", "a", 0.7, 0.75)]
    t = K.terms(recs)
    assert t["all"]["n"] == 4
    assert t["all"]["term"] == pytest.approx(t["all"]["single"] / math.sqrt(2))
    assert t["remembers"]["n"] == 2 and t["character"]["n"] == 1 and t["cowrites"]["n"] == 1


def test_check_passes_under_the_limit_and_fails_over_it():
    mk = K.load_marks()
    tight = [_rec(c, r, 0.8, 0.79) for c in ("CC3-tea-hut", "RD1-marisol") for r in "abcdefgh"]
    res = K.check(tight, mk)
    assert res["judge_term_share"] < 0.05 and not any("judge term" in f for f in res["fails"])
    loose = [_rec(c, r, 0.9, 0.6) for c in ("CC3-tea-hut", "RD1-marisol") for r in "abcdefgh"]
    res = K.check(loose, mk)
    assert not res["pass"] and "judge term" in res["reason"] and "> 5%" in res["reason"]


def test_not_measured_without_records_and_exit_codes():
    res = K.check([], K.load_marks())
    assert res["measured"] is False and K.exit_code(res) == 2
    assert K.exit_code({"pass": True}) == 0 and K.exit_code({"pass": False}) == 1


def test_stored_reference_shakedown_numbers():
    """The kill test of the lane: stored ref-r1/ref-r2 decisions, deepseek flash reader: 4.09% (limit 5%)."""
    import os
    from pathlib import Path
    recs = []
    root = os.environ.get("CF_SHAKEDOWN_RUNS")
    if not root:
        pytest.skip("set CF_SHAKEDOWN_RUNS to the folder holding the stored shakedown runs")
    for r in ("ref-r1", "ref-r2"):
        p = Path(root) / r / "agree" / "second-deepseek-flash-think.jsonl"
        if not p.exists():
            pytest.skip("stored shakedown folders absent")
        recs += [json.loads(x) for x in p.open(encoding="utf-8") if x.strip() and '"chat/j_rplong"' in x]
    t = K.terms(recs)
    assert t["all"]["n"] == 16 and round(100 * t["all"]["term"], 2) == 4.09
    assert round(100 * t["all"]["single"], 2) == 5.79


def test_write_result_merges(tmp_path):
    (tmp_path / "marks45.json").write_text('{"other": 1}', encoding="utf-8")
    K.write_result(tmp_path, {"pass": True})
    assert json.loads((tmp_path / "marks45.json").read_text(encoding="utf-8")) == {"other": 1, "chat_rplong": {"pass": True}}
