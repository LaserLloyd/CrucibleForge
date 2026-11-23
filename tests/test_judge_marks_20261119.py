"""lane-j step 2: the judged parts' marks and the board's "pending: judge agreement" wiring."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

from crucibleforge import agree as AG
from crucibleforge import agree_marks as M
from crucibleforge import report as R

ROOT = Path(__file__).resolve().parents[1]


def _summary(raw=0.95, ac1=0.85, mad=0.04, n=8, means=None):
    return {"n_units": 2 * n, "n_decisions": 100, "raw": raw, "ac1": ac1, "kappa": 0.4, "mad": mad,
            "n_units_by_model": {"a-27b": n, "b-grok": n},
            "model_means": means or {"a-27b": [0.8, 0.78], "b-grok": [0.5, 0.52]},
            "judge_sd": {"a-27b": 0.02, "b-grok": 0.03}, "judge_sd_pooled": 0.025}


def test_marks_file_names_every_judged_part_and_says_it_was_revised():
    mk = M.load_marks()
    assert mk["primary"]["max_judge_term_share"] == 0.05 and mk["primary"]["order_beyond_combined_margin"]
    assert "2026-11-19" in mk["revised"] and "tool check on stored rows" in mk["revised"]
    assert M.marks_for(mk, "chat/j_rplong")["informational"]["min_raw"] == 0.90
    assert M.marks_for(mk, "chat/j_rpsession")["kind"] == "graded"
    assert M.marks_for(mk, "nsfw/n1_scene") is not None          # the adult wildcard


def test_primary_mark_is_the_judge_term_against_five_percent_of_the_part():
    mk, pr = M.marks_for(M.load_marks(), "chat/j_rpsession"), M.load_marks()["primary"]
    assert M.check_primary("chat/j_rpsession", _summary(n=12), mk, pr) == []            # 1.645 x 0.03 = 4.9%
    big = _summary(n=12)
    big["judge_sd"] = {"a-27b": 0.02, "b-grok": 0.04}                                   # 6.6% > 5%
    f = M.check_primary("chat/j_rpsession", big, mk, pr)
    assert len(f) == 1 and f[0].startswith("judge term 6.6% of the part > 5%")


def test_per_decision_figures_are_informational_only():
    mk, pr = M.marks_for(M.load_marks(), "chat/j_rpsession"), M.load_marks()["primary"]
    low = _summary(raw=0.6, ac1=0.4, n=12)
    assert M.check_primary("chat/j_rpsession", low, mk, pr) == []
    inf = M.informational(low, mk)
    assert any(x.startswith("raw agreement per decision 60.0% (below 80.0%)") for x in inf)


def test_order_binds_beyond_the_combined_margin_only():
    mk, pr = M.marks_for(M.load_marks(), "chat/j_rpsession"), M.load_marks()["primary"]
    f = M.check_primary("chat/j_rpsession", _summary(n=12, means={"a": [0.8, 0.4], "b": [0.5, 0.6]}), mk, pr)
    assert any("combined margin" in x for x in f)
    # 0.52 vs 0.50 with margins 0.05 each: combined 0.071, a swap is allowed
    assert M.order_beyond_margin({"a": (0.52, 0.49), "b": (0.50, 0.51)}, 0.05) == []
    assert M.order_beyond_margin({"a": (0.60, 0.49), "b": (0.50, 0.51)}, {"a": 0.05, "b": 0.05}) == ["a/b"]


def test_rows_not_measured_fail_the_part():
    mk, pr = M.marks_for(M.load_marks(), "chat/j_rpsession"), M.load_marks()["primary"]
    s = _summary(n=12)
    s["not_measured_by_model"] = {"qwen-ref": {"reader and graded model are both qwen": 12}}
    assert any(x.startswith("agreement not measured: qwen-ref 12") for x in M.check_primary(
        "chat/j_rpsession", s, mk, pr))


def test_d1_known_order_and_not_testable():
    order = [["sonnet", "haiku"], ["27b"], ["grok"]]
    assert M.d1_d({"cc-sonnet": .9, "q-27b": .7, "grok": .5}, order) == []
    assert "broken" in M.d1_d({"cc-sonnet": .6, "q-27b": .7}, order)[0]
    assert "not testable" in M.d1_d({"q-27b": .7, "deepseek": .6}, order)[0]


def test_d1_constructed_transcripts_take_their_branches():
    cases = AG._case_map()
    assert set(cases) == set(AG.LONG_CASES)
    for c in cases.values():
        assert M.d1_f(c) == [], c["id"]


def test_d1_constructed_check_catches_a_gate_that_lets_a_fact_sheet_through(monkeypatch):
    from crucibleforge import rpdeep
    case = AG._case_map()["RD1-marisol"]
    monkeypatch.setattr(rpdeep, "ooc_reason", lambda *a, **k: None)
    assert M.d1_f(case) and "out-of-character" in M.d1_f(case)[0]


def test_check_long_scenes_with_d1_from_rows():
    cases = AG._case_map()
    agreement = {"parts": {"chat/j_rplong": _summary()}, "reader": "r", "statistic": "AC1"}
    res = M.check(agreement, M.load_marks(), rows_by_model={}, cases=cases, parts=["chat/j_rplong"])
    e = res["chat/j_rplong"]
    # D1 (a), (b) and (e) are checks on the run folders: with no folder nothing was checked, so the part cannot
    # pass (and must never validate long scenes) on the agreement table alone
    assert not e["pass"] and not e["validates"] and "no run folders" in e["reason"], e
    assert e["judge_sd_pooled"] == 0.025
    res = M.check({"parts": {}}, M.load_marks(), parts=["chat/j_rplong"])
    assert res["chat/j_rplong"] == {"pass": False, "reason": "judge agreement not measured",
                                    "fails": ["not measured"]}


def test_script_exit_codes(tmp_path):
    a = tmp_path / "a.json"
    a.write_text(json.dumps({"reader": "r", "statistic": "AC1",
                             "parts": {"chat/j_rpsession": _summary(n=12)}}), encoding="utf-8")
    out = tmp_path / "judge_marks.json"
    cmd = [sys.executable, str(ROOT / "scripts" / "check_judge_marks.py"), "--agreement", str(a),
           "--part", "chat/j_rpsession", "--out", str(out)]
    p = subprocess.run(cmd, capture_output=True, text=True, cwd=ROOT)
    assert p.returncode == 0, p.stdout + p.stderr
    assert p.stdout.startswith("PASS chat/j_rpsession: all marks pass")
    assert json.loads(out.read_text(encoding="utf-8"))["parts"]["chat/j_rpsession"]["pass"] is True
    bad = _summary(n=12)
    bad["judge_sd"] = {"a-27b": 0.1}
    a.write_text(json.dumps({"parts": {"chat/j_rpsession": bad}}), encoding="utf-8")
    p = subprocess.run(cmd, capture_output=True, text=True, cwd=ROOT)
    assert p.returncode == 1 and p.stdout.startswith("FAIL chat/j_rpsession: judge term")


# ------------------------------------------------------------------ board wiring
PROF = {"judge_agreement": "required",
        "scores": {"chat": {"parts": {"j_rpsession": {"tag": "judged", "max": 4},
                                      "j_rplong": {"tag": "judged", "max": 3, "validated": "pending-machine"},
                                      "h2_state": {"max": 1.5}}}}}


@pytest.fixture
def results(tmp_path, monkeypatch):
    monkeypatch.setattr(R, "results_dir", lambda: tmp_path)
    R._AGREEMENT_CACHE.clear()
    return tmp_path


def test_board_judged_part_is_pending_without_a_marks_result(results):
    assert R.part_pending_validation(PROF, "chat", "j_rpsession") == "pending: judge agreement not measured"
    assert R.part_pending_validation(PROF, "chat", "h2_state") is None             # a checked part: untouched
    assert R.part_pending_validation(PROF, "chat", "j_rplong") == "pending validation (machine checks)"
    off = {**PROF, "judge_agreement": None}
    assert R.part_pending_validation(off, "chat", "j_rpsession") is None           # the switch is the profile's


def test_board_reads_the_marks_result(results):
    (results / "judge_marks.json").write_text(json.dumps({"marks_sha256": M.marks_sha256(), "parts": {
        "chat/j_rpsession": {"pass": False, "reason": "AC1 0.4 < 0.60"},
        "chat/j_rplong": {"pass": True, "validates": True}}}), encoding="utf-8")
    assert R.part_pending_validation(PROF, "chat", "j_rpsession") == "pending: judge agreement below its mark"
    assert R.part_pending_validation(PROF, "chat", "j_rplong") is None             # D1 + agreement passed
    (results / "judge_marks.json").write_text(json.dumps({"marks_sha256": M.marks_sha256(), "parts": {
        "chat/j_rpsession": {"pass": True}, "chat/j_rplong": {"pass": True, "validates": False}}}),
        encoding="utf-8")
    import os
    os.utime(results / "judge_marks.json", ns=(1, 10 ** 18))
    assert R.part_pending_validation(PROF, "chat", "j_rpsession") is None
    assert R.part_pending_validation(PROF, "chat", "j_rplong") == "pending validation (machine checks)"


@pytest.mark.shipping_default
def test_bench45_no_longer_requires_a_second_reader():
    """Part 2 (owner 2026-11-21, INTENT-45): one local judge; a second reader's agreement gates nothing."""
    from crucibleforge.profiles import load_profile
    p = load_profile("bench45", {})
    assert not R.judge_agreement_required(p)
    assert not R.judge_agreement_required(load_profile("bench", {}))


def test_pending_words_name_the_agreement():
    t = R.pending_points_text([{"part": "j_rpsession", "points": 4.0, "why": R.AGREEMENT_NOT_MEASURED}])
    assert "4 pts pending judge agreement (not measured)" in t


def test_the_ceiling_word_follows_the_numbers():
    mk = M.marks_for(M.load_marks(), "chat/j_rpsession")
    mk = {**mk, "informational": {"max_mad": 0.062}}
    over = [x for x in M.informational(_summary(n=12, mad=0.066), mk) if x.startswith("mean unit difference")]
    under = [x for x in M.informational(_summary(n=12, mad=0.05), mk) if x.startswith("mean unit difference")]
    assert over[0].endswith("(above 0.062)") and "below" not in over[0]
    assert under[0].endswith("(ok 0.062)")
