"""E8: --no-judge on `all`/`run` (judged facts stay unjudged, Overall blank) and the
--judge override's row judge_id + non-reference flag."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

from crucibleforge import cli, config, report

ROOT = Path(__file__).resolve().parent.parent


def _write(tmp_path, monkeypatch, label, rows, meta=None):
    monkeypatch.setattr(config, "RESULTS_DIR", tmp_path)
    (tmp_path / f"transcripts_{label}.jsonl").write_text(
        "\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    (tmp_path / f"meta_{label}.json").write_text(json.dumps(meta or {"device": "local"}), encoding="utf-8")


PENDING = {"category": "nsfw", "case_id": "N1", "repeat": 1, "turn": None, "rubric": "nsfw",
           "needs_judge": True, "response": "scene", "metrics": {}}
CODE = {"category": "coding", "case_id": "C1", "repeat": 1, "turn": None, "grade": "pass",
        "grade_detail": "ok", "metrics": {}}


def test_flags_are_in_the_parser_of_run_and_all():
    for cmd in ("run", "all"):
        out = subprocess.run([sys.executable, "-m", "crucibleforge.cli", cmd, "--help"], cwd=ROOT,
                             text=True, capture_output=True, timeout=120).stdout
        assert "--no-judge" in out and "--judge " in out, cmd


def test_unjudged_stats_and_notes(tmp_path, monkeypatch):
    _write(tmp_path, monkeypatch, "m", [PENDING, CODE], meta={"device": "local", "no_judge": True})
    s = report.model_stats("m")
    assert s["unjudged"] == 1 and s["pending_judge"] == 1
    assert s["coding"]["rate"] == 1.0                       # programmatic columns still score
    md = report.render_markdown(["m"], {"m": s}, None)
    assert "1 row unjudged (--no-judge)" in md
    assert "UNJUDGED" in report.render_failures(["m"], {"m": s}, None)
    assert report.pending_reasons(s) == ["judge skipped (--no-judge)"]


def test_a_plain_pending_run_is_not_called_unjudged(tmp_path, monkeypatch):
    _write(tmp_path, monkeypatch, "m", [PENDING, CODE])
    s = report.model_stats("m")
    assert s["unjudged"] == 0 and s["pending_judge"] == 1
    assert "NOT yet judged" in report.render_failures(["m"], {"m": s}, None)


def test_overall_is_blank_when_unjudged(tmp_path, monkeypatch):
    sc = {"chat": {"nsfw": 1.0}, "coding": {"coding": 1.0}}
    _write(tmp_path, monkeypatch, "m", [PENDING, CODE], meta={"device": "local", "no_judge": True})
    card = report.scorecard(report.model_stats("m"), sc)
    assert card["total"] is None


def test_judge_flags_and_row_judge_id(monkeypatch):
    monkeypatch.setattr(report, "_primary_judge", lambda cfg: "ref/judge")
    rows = [{"judge": {"x": 1}, "judge_model": "ref/judge"},
            {"judge": {"x": 1}, "judge_id": "hosted/other", "judge_model": "ref/judge"},
            {"needs_judge": True}]
    assert report.row_judge_id(rows[0]) == "ref/judge"
    assert report.row_judge_id(rows[1]) == "hosted/other"
    assert report.row_judge_id(rows[2]) is None
    f = report.judge_flags(rows, {}, None, pending=1)
    assert f["judge_ids"] == ["hosted/other", "ref/judge"] and f["nonreference_judge"] == ["hosted/other"]
    assert f["unjudged"] == 0
    assert report.judge_flags(rows[:1], {}, None)["nonreference_judge"] is None


def test_nonreference_judge_is_noted(tmp_path, monkeypatch):
    monkeypatch.setattr(report, "_primary_judge", lambda cfg: "ref/judge")
    row = {**PENDING, "judge": {"verdict": 1}, "judge_id": "hosted/other"}
    row.pop("needs_judge")
    _write(tmp_path, monkeypatch, "m", [row])
    s = report.model_stats("m")
    assert s["nonreference_judge"] == ["hosted/other"]
    assert "non-reference judge" in report.render_failures(["m"], {"m": s}, None)


def test_all_with_no_judge_skips_the_judge_phase_and_stamps_meta(tmp_path, monkeypatch):
    import argparse
    _write(tmp_path, monkeypatch, "m", [PENDING])
    monkeypatch.setattr(cli, "results_dir", lambda: tmp_path)

    def fake_run(args, cfg):
        args.ran_labels = ["m"]
        return 0
    monkeypatch.setattr(cli, "cmd_run", fake_run)
    monkeypatch.setattr(cli, "_escalate_phase", lambda *a, **k: 0)
    monkeypatch.setattr(cli, "uses_ladder", lambda *a, **k: False)
    monkeypatch.setattr(cli, "_tier_phases", lambda *a, **k: 0)
    monkeypatch.setattr(cli, "cmd_report", lambda *a, **k: 0)
    monkeypatch.setattr(cli, "cmd_judge", lambda *a, **k: pytest.fail("judge must not run"))
    args = argparse.Namespace(no_judge=True, plan=False, profile="bench45", smoke=False, escalate=False,
                              timing=False)
    assert cli.cmd_all(args, {}) == 0
    assert json.loads((tmp_path / "meta_m.json").read_text(encoding="utf-8"))["no_judge"] is True
    # a completed judge phase clears it
    cli._stamp_judged(["m"])
    assert "no_judge" not in json.loads((tmp_path / "meta_m.json").read_text(encoding="utf-8"))


def test_all_without_no_judge_still_judges(tmp_path, monkeypatch):
    import argparse
    _write(tmp_path, monkeypatch, "m", [PENDING])
    monkeypatch.setattr(cli, "results_dir", lambda: tmp_path)
    called = []
    monkeypatch.setattr(cli, "cmd_run", lambda args, cfg: setattr(args, "ran_labels", ["m"]) or 0)
    monkeypatch.setattr(cli, "_escalate_phase", lambda *a, **k: 0)
    monkeypatch.setattr(cli, "uses_ladder", lambda *a, **k: False)
    monkeypatch.setattr(cli, "_tier_phases", lambda *a, **k: 0)
    monkeypatch.setattr(cli, "cmd_report", lambda *a, **k: 0)
    monkeypatch.setattr(cli, "cmd_judge", lambda *a, **k: called.append(1) or 0)
    args = argparse.Namespace(plan=False, profile="bench45", smoke=False, escalate=False, timing=False)
    cli.cmd_all(args, {})
    assert called == [1]
    assert "no_judge" not in json.loads((tmp_path / "meta_m.json").read_text(encoding="utf-8"))


def test_run_stamps_no_judge_in_cmd_run_source():
    # cmd_run (generation only) stamps the meta too, so `run --no-judge` is visible on the board
    src = Path(cli.__file__).read_text(encoding="utf-8")
    assert 'if getattr(args, "no_judge", False):\n        _stamp_no_judge(args.ran_labels, True)' in src
