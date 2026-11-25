"""4.5.1: fix rows stored while no grading route existed are graded on the next run, from the stored reply.

Fourth Docker run (2026-11-25): the resume was right (0 jobs) but the model stayed unranked. The fix rows of the
earlier runs carry ``fixcheck: {"unavailable": true}`` (no sandbox then), fixrows45 counts them as "no sandbox", and a
resume never graded them again, so the unsandboxed route (fixopen) never got a chance. The same happens to anyone who
runs without bubblewrap, then installs it or sets the switch, and runs again. Now `run`/`all` grades such rows from
the stored reply with the live run's code (no model call) whenever a route exists, and writes the verdict a first
grading would have stored, as a superseding row."""
from __future__ import annotations

from pathlib import Path

import pytest

from crucibleforge import cli, config, fixcheck, fixopen, fixrows45, graders, profiles, runner
from crucibleforge.api import ChatResult

ROOT = Path(__file__).resolve().parent.parent
needs_bwrap = pytest.mark.skipif(not graders.sandbox_available(), reason="needs a working bwrap")
FIX = "PATCHFIX-core-4-1"
LABEL = "m"
OWN_RUNNER = fixcheck.run_unit
OWN_BWRAP = graders._BWRAP


@pytest.fixture(scope="module")
def std():
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("CF_COMPOSITION", "standard")
        cfg = config.load_config(ROOT / "models.example.yaml")
        prof = profiles.load_profile("bench45", cfg)
        _cfg2, cases = profiles.apply_profile(prof, cfg)
    return {c["id"]: c for c in cases}


@pytest.fixture
def folder(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "RESULTS_DIR", tmp_path)
    monkeypatch.setattr("crucibleforge.version.row_current", lambda r, cfg=None: True)
    monkeypatch.setattr(fixcheck, "run_unit", OWN_RUNNER)
    monkeypatch.setattr(graders, "_warned_no_sandbox", False)
    return tmp_path


def _reply(case):
    return "\n".join(f"{p}\n````python\n{t}````\n" for p, t in sorted(case["reference_files"].items()))


def _no_route(monkeypatch):
    monkeypatch.setattr(graders, "_BWRAP", None)
    monkeypatch.setattr(graders, "ALLOW_UNSANDBOXED", False)


def _store_without_a_route(case, monkeypatch, repeat=1):
    """A row as the earlier Docker runs stored it (runner.grade_single_row, the live code): no bwrap, the switch set so
    the code bank ran, but fixcheck (before fixopen) refused the fix units. Leaves this process with no route."""
    monkeypatch.setattr(graders, "_BWRAP", None)
    monkeypatch.setattr(graders, "ALLOW_UNSANDBOXED", True)
    monkeypatch.setattr(fixcheck, "run_unit", OWN_RUNNER)
    reply = _reply(case)
    res = ChatResult(response_text=reply, finish_reason="stop", completion_tokens=100)
    row = {"bench_run_id": "r1", "case_id": case["id"], "category": case["category"], "repeat": repeat,
           "turn": None, "model_label": LABEL, "response": reply, "finish_reason": "stop",
           "metrics": {"completion_tokens": 100}, "max_tokens_sent": 65536, "ts": "2026-11-24T17:30:00"}
    runner.grade_single_row(row, res, case, False, 65536)
    assert row["fixcheck"] == {"unavailable": True}
    config.append_transcript(LABEL, row)
    _no_route(monkeypatch)
    return row


def _fresh(case):
    """What a first grading with a route stores for the same reply."""
    return fixrows45.reply_verdict(case, _reply(case), finish_reason="stop", completion_tokens=100, sent=65536)


def _stored(case_id=FIX):
    return [r for r in config.load_transcripts(LABEL) if r["case_id"] == case_id]


@needs_bwrap
def test_the_sandbox_route_grades_a_stored_unavailable_row(std, folder, monkeypatch):
    case = std[FIX]
    _store_without_a_route(case, monkeypatch)
    monkeypatch.setattr(graders, "_BWRAP", OWN_BWRAP)                       # bwrap is back (it was installed)
    assert fixopen.regrade_unavailable(LABEL, list(std.values())) == 1
    [row] = _stored()
    assert row["fixcheck"] == _fresh(case) and row["fixcheck"]["score"] == 1.0
    assert row["regraded_by"]["tool"] == "crucibleforge.fixopen" and row["regraded_by"]["regraded"] == ["fixcheck"]
    assert fixopen.regrade_unavailable(LABEL, list(std.values())) == 0      # graded once, never twice


def test_the_switch_route_grades_a_stored_unavailable_row(std, folder, monkeypatch):
    case = std[FIX]
    stored = _store_without_a_route(case, monkeypatch)
    monkeypatch.setattr(graders, "ALLOW_UNSANDBOXED", True)                 # the switch is set now (no bwrap)
    assert fixopen.regrade_unavailable(LABEL, list(std.values())) == 1
    assert fixcheck.run_unit is fixopen.run_unit_unsandboxed
    [row] = _stored()
    assert row["fixcheck"] == _fresh(case) and row["fixcheck"]["score"] == 1.0
    # only the check and its record changed: the reply, the run and everything else are the stored row's own
    assert {k: v for k, v in row.items() if k not in ("fixcheck", "regraded_by")} == \
        {k: v for k, v in stored.items() if k != "fixcheck"}
    assert fixopen.regrade_unavailable(LABEL, list(std.values())) == 0


def test_without_a_route_nothing_changes(std, folder, monkeypatch):
    case = std[FIX]
    _store_without_a_route(case, monkeypatch)
    before = (folder / f"transcripts_{LABEL}.jsonl").read_bytes() if (folder / f"transcripts_{LABEL}.jsonl").exists() \
        else None
    assert fixopen.regrade_unavailable(LABEL, list(std.values())) == 0
    assert _stored()[0]["fixcheck"] == {"unavailable": True}
    if before is not None:
        assert (folder / f"transcripts_{LABEL}.jsonl").read_bytes() == before


def test_a_regraded_row_queues_no_generation_job(std, folder, monkeypatch):
    case = std[FIX]
    _store_without_a_route(case, monkeypatch)
    monkeypatch.setattr(graders, "ALLOW_UNSANDBOXED", True)
    monkeypatch.setattr("crucibleforge.config.repeats_for", lambda cfg, cat, smoke, c=None: 1)
    fixopen.regrade_unavailable(LABEL, list(std.values()))
    rows = config.load_transcripts(LABEL)
    assert cli._first_reply_jobs(LABEL, {}, [case], rows=rows) == set()


def test_a_row_that_cannot_be_graded_from_the_row_is_owed_once_a_route_exists(std, monkeypatch):
    # an agent row (CB-F1) keeps no final tree: with a route it is owed again; without one it waits as before
    monkeypatch.setattr("crucibleforge.config.repeats_for", lambda cfg, cat, smoke, c=None: 1)
    c = std["CB-F1-larder-bughunt"]
    rows = [{"case_id": c["id"], "repeat": 1, "grade": "pass", "response": "done", "fixcheck": {"unavailable": True}}]
    monkeypatch.setattr(fixopen, "route_available", lambda: False)
    assert cli._first_reply_jobs(LABEL, {}, [c], rows=rows) == set()
    monkeypatch.setattr(fixopen, "route_available", lambda: True)
    assert cli._first_reply_jobs(LABEL, {}, [c], rows=rows) == {(None, c["id"], 1)}


def test_a_row_of_changed_case_content_is_left(std, folder, monkeypatch):
    case = std[FIX]
    _store_without_a_route(case, monkeypatch)
    monkeypatch.setattr(graders, "ALLOW_UNSANDBOXED", True)
    monkeypatch.setattr("crucibleforge.version.row_current", lambda r, cfg=None: False)
    assert fixopen.regrade_unavailable(LABEL, list(std.values())) == 0


def test_run_grades_them_before_it_plans_any_job():
    src = Path(cli.__file__).read_text(encoding="utf-8")
    hook = src.index("n_fix = _fixopen.regrade_unavailable(")
    assert hook < src.index("summary, entries = _run_selected(cfg, entries, cases, prof, args, only, run_models)")
    assert hook < src.index("summary = _run_routed(cfg, entries, cases, prof, args)")
