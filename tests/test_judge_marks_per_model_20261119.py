"""Lead 2026-11-19 (b): judge agreement is gated per model (the agreement run must have covered that model's own
units with a fitting reader, on the rows the board shows), and the marks result carries the hash of the frozen
marks file it was checked against (compared at render)."""
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from crucibleforge import agree_marks as M
from crucibleforge import report as R

ROOT = Path(__file__).resolve().parents[1]
PROF = {"judge_agreement": "required",
        "scores": {"chat": {"parts": {"j_rpsession": {"tag": "judged", "max": 4},
                                      "j_rplong": {"tag": "judged", "max": 3, "validated": "pending-machine"}}}}}
SHA = hashlib.sha256(M.MARKS.read_bytes()).hexdigest()


@pytest.fixture
def results(tmp_path, monkeypatch):
    monkeypatch.setattr(R, "results_dir", lambda: tmp_path)
    R._AGREEMENT_CACHE.clear()
    return tmp_path


def _write(results, parts, covered=None, sha=SHA):
    body = {"marks": M.MARKS.name, "marks_sha256": sha, "parts": parts,
            "covered": covered if covered is not None else {"a-27b": {"folders": ["f1"], "run_ids": ["r1"]}}}
    (results / M.RESULT_NAME).write_text(json.dumps(body), encoding="utf-8")
    R._AGREEMENT_CACHE.clear()


def test_a_model_the_agreement_run_did_not_cover_is_pending(results):
    _write(results, {"chat/j_rpsession": {"pass": True, "n_units_by_model": {"a-27b": 8}}})
    assert R.part_pending_validation(PROF, "chat", "j_rpsession", label="a-27b", run_ids={"r1"}) is None
    assert R.part_pending_validation(PROF, "chat", "j_rpsession", label="c-new", run_ids={"r9"}) \
        == "pending: judge agreement not measured for this model"


def test_rows_from_a_run_the_check_never_saw_are_pending(results):
    _write(results, {"chat/j_rpsession": {"pass": True, "n_units_by_model": {"a-27b": 8}}})
    assert R.part_pending_validation(PROF, "chat", "j_rpsession", label="a-27b", run_ids={"r1", "r2"}) \
        == R.AGREEMENT_NOT_MEASURED_MODEL


def test_a_failed_mark_stays_board_level(results):
    _write(results, {"chat/j_rpsession": {"pass": False, "n_units_by_model": {"a-27b": 8}}})
    assert R.part_pending_validation(PROF, "chat", "j_rpsession", label="a-27b", run_ids={"r1"}) \
        == R.AGREEMENT_FAILED


def test_long_scenes_validate_only_for_a_covered_model(results):
    _write(results, {"chat/j_rplong": {"pass": True, "validates": True, "n_units_by_model": {"a-27b": 5}}})
    assert R.part_pending_validation(PROF, "chat", "j_rplong", label="a-27b", run_ids={"r1"}) is None
    assert R.part_pending_validation(PROF, "chat", "j_rplong", label="c-new", run_ids={"r9"}) \
        == R.AGREEMENT_NOT_MEASURED_MODEL


def test_a_result_checked_against_other_marks_is_not_used(results):
    _write(results, {"chat/j_rpsession": {"pass": True, "n_units_by_model": {"a-27b": 8}}}, sha="0" * 64)
    assert R.part_pending_validation(PROF, "chat", "j_rpsession", label="a-27b", run_ids={"r1"}) \
        == R.AGREEMENT_OTHER_MARKS
    _write(results, {"chat/j_rpsession": {"pass": True, "n_units_by_model": {"a-27b": 8}}}, sha=None)
    assert R.part_pending_validation(PROF, "chat", "j_rpsession", label="a-27b", run_ids={"r1"}) \
        == R.AGREEMENT_OTHER_MARKS


def test_the_pending_words_say_for_this_model():
    t = R.pending_points_text([{"part": "j_rpsession", "points": 4.0, "why": R.AGREEMENT_NOT_MEASURED_MODEL}])
    assert "judge agreement (not measured for this model)" in t


def test_check_carries_the_units_per_model():
    s = {"n_units": 16, "n_decisions": 100, "raw": 0.95, "ac1": 0.85, "kappa": 0.4, "mad": 0.04,
         "n_units_by_model": {"a-27b": 8, "b-grok": 0}, "model_means": {"a-27b": [0.8, 0.78]},
         "judge_sd": {"a-27b": 0.02}, "judge_sd_pooled": 0.02}
    res = M.check({"parts": {"chat/j_rpsession": s}}, M.load_marks(), parts=["chat/j_rpsession"])
    assert res["chat/j_rpsession"]["n_units_by_model"] == {"a-27b": 8, "b-grok": 0}


def test_the_script_stamps_the_marks_hash_and_the_folders_it_covered(tmp_path):
    f = tmp_path / "results-a"
    f.mkdir()
    (f / "transcripts_a-27b.jsonl").write_text(json.dumps({"case_id": "x", "bench_run_id": "r1"}) + "\n",
                                               encoding="utf-8")
    a = tmp_path / "a.json"
    a.write_text(json.dumps({"reader": "r", "statistic": "AC1", "parts": {}}), encoding="utf-8")
    out = tmp_path / "judge_marks.json"
    subprocess.run([sys.executable, str(ROOT / "scripts" / "check_judge_marks.py"), "--agreement", str(a),
                    "--folders", str(f), "--part", "chat/j_rpsession", "--out", str(out)],
                   capture_output=True, text=True, cwd=ROOT)
    body = json.loads(out.read_text(encoding="utf-8"))
    assert body["marks_sha256"] == SHA
    assert body["covered"] == {"a-27b": {"folders": ["results-a"], "run_ids": ["r1"]}}
