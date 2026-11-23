"""Assistant basics (checked), step 5: the validation marks are data written before any run, and the script that
reads them exits 0 or 1. Tested on synthetic result folders (no rows from any model)."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("check_assistant_marks", ROOT / "scripts" / "check_assistant_marks.py")
cam = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cam)

IDS = ["IZ02-json-single-line-typed", "IZ04-lowercase-except-http", "IZ05-five-dash-lines-exact-26",
       "IZ06-allcaps-recall-notice", "IZ07-json-city-record", "IZ08-two-questions-only",
       "RX02-knights-knaves", "RX08-water-jugs", "RX13-recurrence-term", "RX20-five-houses-four-attributes",
       "RX21-flawed-cost-chain", "LX2-multihop-16k", "LX3-aggregate-12k", "LX4-count-6k", "LX6-absent-12k"]
CAT = {"IZ": "instruct", "RX": "reasoning", "LX": "longctx"}


def _rows(passing, cut=()):
    """Two repeats of every item; ``passing`` = set of items answered right; ``cut`` = (item, repeat) cut."""
    out = []
    for cid in IDS:
        for rep in (1, 2):
            c = (cid, rep) in cut
            out.append({"case_id": cid, "category": CAT[cid[:2]], "repeat": rep, "bench_run_id": "r",
                        "grade": "pass" if cid in passing and not c else "fail",
                        "response": "partial" if c else "answer", "finish_reason": "length" if c else "stop",
                        "truncated": c})
    return out


def _write(root, label, passing, cut=()):
    root.mkdir(parents=True, exist_ok=True)
    (root / f"transcripts_{label}.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in _rows(passing, cut)), encoding="utf-8")


@pytest.fixture(autouse=True)
def _standard_composition(monkeypatch):
    monkeypatch.delenv("CF_COMPOSITION", raising=False)


ALL = set(IDS)
SOUND_SET = ALL - {IDS[14]}                      # 14 of 15
DAMAGED = set(IDS[:9])                           # 9 of 15 = 0.60
SMALL = set(IDS[:3])                             # 3 of 15 = 0.20


def _good(tmp_path, **over):
    a, b = tmp_path / "run_a", tmp_path / "run_b"
    for d in (a, b):
        _write(d, "reference", ALL)
    _write(a, "deepseek-flash", SOUND_SET)
    _write(a, "darker-scarlett-27b", ALL)
    _write(a, "split-untied-31b", DAMAGED)
    _write(a, "deepseek-r1-0528-8b", SMALL)
    return a, b


def test_the_marks_are_a_data_file_written_before_any_run():
    m = json.loads((ROOT / "calibration" / "assistant_marks_20261119.json").read_text(encoding="utf-8"))
    assert m["written_before_any_run"] is True
    assert m["marks"]["a_reference_min"] == 0.90 and m["marks"]["a_replicate_max_difference"] == 0.10
    assert m["marks"]["d_max_cut_share"] == 0.05
    assert set(m["set"]) == {"reference", "sound", "damaged", "small"}


def test_a_run_that_meets_every_mark_exits_0(tmp_path, capsys):
    a, b = _good(tmp_path)
    assert cam.main([str(a), str(b)]) == 0
    out = capsys.readouterr().out
    assert "ALL MARKS HOLD" in out and "FAIL" not in out
    assert "dead weight" in out


def test_the_reference_below_090_fails_mark_a(tmp_path, capsys):
    a, b = _good(tmp_path)
    _write(b, "reference", set(IDS[:12]))                       # 0.80 on the replicate
    assert cam.main([str(a), str(b)]) == 1
    assert "FAIL  (a) reference lowest run 0.800" in capsys.readouterr().out


def test_replicates_that_differ_by_more_than_010_fail_mark_a(tmp_path, capsys):
    a, b = _good(tmp_path)
    _write(a, "reference", ALL)
    _write(b, "reference", set(IDS[:13]) | {IDS[14]})           # 14 of 15 = 0.933 vs 1.0: 0.067 apart, fine
    assert cam.main([str(a), str(b)]) == 0
    capsys.readouterr()
    _write(a, "reference", set(IDS[:3]) | set(IDS[3:]))
    _write(b, "reference", set(IDS[:13]))                       # 0.867 vs 1.0 is 0.133 apart (and below 0.90)
    assert cam.main([str(a), str(b)]) == 1
    assert "replicate runs differ by 0.133" in capsys.readouterr().out


def test_one_run_of_the_reference_is_not_a_replicate(tmp_path, capsys):
    a, _b = _good(tmp_path)
    assert cam.main([str(a)]) == 1
    assert "needs 2 measured runs" in capsys.readouterr().out


def test_a_damaged_model_above_a_sound_one_fails_mark_b(tmp_path, capsys):
    a, b = _good(tmp_path)
    _write(a, "split-untied-31b", set(IDS[:14]))                 # 0.93, above the sound 14/15 ... equal is not above
    assert cam.main([str(a), str(b)]) == 1
    assert "FAIL  (b) lowest sound" in capsys.readouterr().out


def test_a_small_model_above_a_damaged_one_fails_mark_b(tmp_path, capsys):
    a, b = _good(tmp_path)
    _write(a, "deepseek-r1-0528-8b", set(IDS[:10]))
    assert cam.main([str(a), str(b)]) == 1
    assert "FAIL  (b) lowest damaged" in capsys.readouterr().out


def test_an_item_every_sound_model_fails_is_a_broken_key(tmp_path, capsys):
    a, b = _good(tmp_path)
    for d, label in ((a, "deepseek-flash"), (a, "darker-scarlett-27b"), (a, "reference"), (b, "reference")):
        _write(d, label, ALL - {IDS[5]})
    assert cam.main([str(a), str(b)]) == 1
    out = capsys.readouterr().out
    assert "FAIL  (c) no item failed by every sound model" in out and IDS[5] in out


def test_an_item_every_model_passes_is_reported_not_failed(tmp_path, capsys):
    a, b = _good(tmp_path)
    assert cam.main([str(a), str(b)]) == 0
    # IZ02 is in the small model's 3 passes and in everyone else's: dead weight, reported
    assert IDS[0] in [l for l in capsys.readouterr().out.splitlines() if "dead weight" in l][0]


def test_more_than_5_percent_cut_on_a_sound_model_fails_mark_d(tmp_path, capsys):
    a, b = _good(tmp_path)
    # step 2 (2026-11-21): one repeat per item (was two), so a sound model's 15 runs allow no cut under the 5% bar
    _write(a, "deepseek-flash", SOUND_SET, cut={(IDS[0], 1), (IDS[1], 1)})      # 2 of 15 = 0.133
    assert cam.main([str(a), str(b)]) == 1
    assert "FAIL  (d) deepseek-flash: cut share 0.133" in capsys.readouterr().out
    _write(a, "deepseek-flash", SOUND_SET, cut={(IDS[0], 1)})                    # 1 of 15 = 0.067
    assert cam.main([str(a), str(b)]) == 1
    assert "FAIL  (d) deepseek-flash: cut share 0.067" in capsys.readouterr().out
    _write(a, "deepseek-flash", SOUND_SET, cut=set())                            # 0 of 15
    assert cam.main([str(a), str(b)]) == 0


def test_the_model_set_is_a_parameter_and_a_missing_role_is_a_failure(tmp_path, capsys):
    a, b = _good(tmp_path)
    assert cam.main([str(a), str(b), "--set", "small=nothing-like-this"]) == 1
    assert "no model found for role 'small'" in capsys.readouterr().out
    _write(a, "my-weak-one", SMALL)
    assert cam.main([str(a), str(b), "--set", "small=my-weak-*"]) == 0


def test_usage_problems_exit_2(tmp_path, capsys):
    assert cam.main([str(tmp_path / "missing")]) == 2
    assert cam.main([str(tmp_path), "--set", "bogus=x"]) == 2


def test_the_default_patterns_match_suffixed_run_folder_ids_and_values_are_unchanged(tmp_path):
    marks = json.loads(cam.DEFAULT_MARKS.read_text(encoding="utf-8"))
    assert marks["marks"] == {
        "a_reference_min": 0.90, "a_replicate_max_difference": 0.10, "a_min_runs": 2,
        "b_order": marks["marks"]["b_order"], "c_item_passed_at": 0.5, "d_max_cut_share": 0.05}
    got = cam._match(["reference-qwen3.8-27b-r2", "deepseek-flash-a", "darker-scarlett-27b-q8",
                      "split-untied-31b-x", "deepseek-r1-0528-8b-q4"], marks["set"]["reference"])
    assert got == ["reference-qwen3.8-27b-r2"]
    assert cam._match(["m-split-untied-v2"], marks["set"]["damaged"]) == ["m-split-untied-v2"]
    assert cam._match(["r1-8b-q4"], marks["set"]["small"]) == ["r1-8b-q4"]


def test_a_set_with_no_folder_is_a_failed_mark_not_a_crash(tmp_path, capsys):
    a, b = _good(tmp_path)
    assert cam.main([str(a), str(b), "--set", "damaged=no-such-model*"]) == 1
    out = capsys.readouterr().out
    assert "not run: no folder for damaged" in out and "MARKS NOT MET" in out
    empty = tmp_path / "empty"
    empty.mkdir()
    assert cam.main([str(empty)]) == 1
    assert "not run: no folder for reference" in capsys.readouterr().out


def test_assistant_stays_unvalidated():
    from crucibleforge import assistant45, profiles
    assert assistant45.validated(profiles.load_profile("bench45")) is False


def test_the_marks_output_names_what_failed_or_was_not_run(tmp_path):
    from crucibleforge import assistant45
    a, b = _good(tmp_path)
    _write(a, "deepseek-flash", SOUND_SET, cut={(IDS[i], 1) for i in range(6)})            # cut share too high
    out = tmp_path / "assistant_marks.json"
    assert cam.main([str(a), str(b), "--set", "damaged=no-such*", "--out", str(out)]) == 1
    res = json.loads(out.read_text(encoding="utf-8"))
    assert "damaged-model set not run" in res["reasons"] and res["ok"] is False
    note = assistant45.pending_note(tmp_path)
    assert "pending validation" in note and "damaged-model set not run" in note
    assert "not counted in any total" in note
    assert "have not been run" in assistant45.pending_note(tmp_path / "elsewhere")


def test_cut_share_failure_reads_as_a_phrase():
    assert cam.reasons(["PASS  (a) ok", "FAIL  (d) x: cut share 0.100 <= 0.05"]) == ["cut-share mark failed"]


def test_pending_validation_adds_no_score_to_any_total():
    from crucibleforge import assistant45, profiles
    prof = profiles.load_profile("bench45")
    assert not assistant45.validated(prof)
    ok = {"status": "ok", "share": 0.97, "margin": 0.03, "band": "pass", "cut": 0}
    cell = assistant45.cell(ok, validated=False, ceiling=False)
    assert cell == "pending validation" and not any(ch.isdigit() for ch in cell)
    keys = {k for s in prof["scores"].values() for k in (s.get("parts") or {})}
    assert not any("assistant" in k for k in prof["scores"]) and not any("assistant" in k for k in keys)
