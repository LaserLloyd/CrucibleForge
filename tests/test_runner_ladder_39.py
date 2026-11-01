"""Suite 3.9 runner: ladder phases (crucibleforge/ladder.py), the errored-row
retry rule (EVAL-FABLE M4, run side) and per-phase timing.

Ladder sets are written to a tmp dir (no set files exist in the repo yet);
``report.ladder_progress`` (the board side) is stubbed with the signature
{"cat/part": {"std": share|None, "sets": {n: share|None}}}.
"""
from __future__ import annotations

import argparse
import copy
import json

import pytest

from crucibleforge import bench_all, cli, config, ladder, report, runner, stopflag
from crucibleforge.profiles import load_profile


@pytest.fixture(autouse=True)
def _flags():
    stopflag.ABORT.clear()
    runner.STOP.clear()
    yield


@pytest.fixture
def sets(tmp_path, monkeypatch):
    root = tmp_path / "ladder"
    for key, n, level, ids, extra in [
        ("work/reasoning", 1, 1, ["MH16-digit-sum-power", "MH22-dual-base-palindrome",
                                  "CZ11-lex-triple-substring"], {"budget": 65536}),
        ("work/reasoning", 2, 2, ["CZ02-segment-crossings", "CZ01-prime-census",
                                  "CZ03-lisp-machine"], {}),
        ("chat/deep_rp", 1, 1, ["RD2-trieste", "RD3-granary", "CB-E3-lockrun"], {}),
    ]:
        d = root / key
        d.mkdir(parents=True, exist_ok=True)
        (d / f"set-{n}.json").write_text(json.dumps({"cases": ids, "level": level, **extra}),
                                         encoding="utf-8")
    monkeypatch.setattr(ladder, "roots", lambda: [root])
    return root


def _prof():
    return load_profile("bench", {})


# ------------------------------------------------- errored rows (M4)

def test_done_jobs_never_counts_an_errored_row():
    rows = [{"case_id": "A", "repeat": 1, "grade": "pass"},
            {"case_id": "B", "repeat": 1, "grade": "error"},
            {"case_id": "C", "repeat": 1, "grade": "fail"}]
    assert cli.done_jobs(rows) == {("A", 1), ("C", 1)}


def test_tier_todo_retries_errored_rows(monkeypatch):
    """tier_todo used every board row as done — an errored tier row (rig
    down, rejected request) was never retried by a resumed run."""
    monkeypatch.setattr(cli, "tier_progress", lambda *a, **k: {})
    import crucibleforge.tiers as tiers
    monkeypatch.setattr(tiers, "unlocked", lambda prof, tier, prev: ["code"])
    cases = [{"id": "CZ02", "category": "coding"}, {"id": "CZ03", "category": "coding"}]
    import crucibleforge.profiles as profiles
    monkeypatch.setattr(profiles, "tier_cases", lambda *a, **k: cases)
    monkeypatch.setattr(report, "board_filter", lambda label, cfg, **k: [
        {"case_id": "CZ02", "repeat": 1, "grade": "error"},
        {"case_id": "CZ03", "repeat": 1, "grade": "pass"}])
    _, _, jobs = cli.tier_todo("m", {"defaults": {}}, {}, 2)
    assert jobs == {(None, "CZ02", 1)}


def test_bench_all_retries_a_transport_errored_case():
    rows = [{"case_id": "A", "repeat": 1, "grade": "error", "error_kind": "transport"},
            {"case_id": "B", "repeat": 1, "grade": "pass"},
            {"case_id": "C", "repeat": 1, "grade": "error", "error_kind": "template"}]
    assert bench_all.missing_cases(rows, ["A", "B", "C"]) == ["A"]
    # bounded: a case that errored MAX_CASE_ATTEMPTS times is left errored
    assert bench_all.missing_cases(rows, ["A"], attempts={"A": 2}) == []


# ----------------------------------------------------------- ladder_todo

def test_due_sets_are_the_lowest_open_level_and_errored_rows_rerun(sets, monkeypatch):
    prof = _prof()
    found = ladder.discover(prof)
    assert set(found) == {"work/reasoning", "chat/deep_rp"}
    progress = {"work/reasoning": {"std": 0.95, "sets": {}},     # unlocked: set 1 due
                "chat/deep_rp": {"std": 0.5, "sets": {}}}         # locked
    monkeypatch.setattr(report, "board_filter", lambda label, cfg, **k: [
        {"case_id": "MH16-digit-sum-power", "repeat": 1, "grade": "pass"},
        {"case_id": "MH22-dual-base-palindrome", "repeat": 1, "grade": "error"}])
    cfg_p, _, _ = cli._apply_profile_arg(argparse.Namespace(profile=None, judge=None), {})
    cases, jobs, names = cli.ladder_todo("m", cfg_p, prof, found, 0.9, progress=progress)
    assert names == ["work/reasoning#1"]
    assert {c["id"] for c in cases} == {"MH16-digit-sum-power", "MH22-dual-base-palindrome",
                                        "CZ11-lex-triple-substring"}
    assert all(c["ladder_budget"] == 65536 and c["ladder"] == "work/reasoning#1" for c in cases)
    ids = {j[1] for j in jobs}
    assert "MH16-digit-sum-power" not in ids                     # done
    assert "MH22-dual-base-palindrome" in ids                    # errored: retried
    # level 2 only after set 1 scored >= unlock
    progress["work/reasoning"]["sets"] = {1: 0.95}
    _, _, names = cli.ladder_todo("m", cfg_p, prof, found, 0.9, progress=progress)
    assert names == ["work/reasoning#2"]
    progress["work/reasoning"]["sets"] = {1: 0.5}
    assert cli.ladder_todo("m", cfg_p, prof, found, 0.9, progress=progress)[2] == []


def test_ladder_phases_climb_until_nothing_is_due(sets, monkeypatch):
    shares = {"work/reasoning": {"std": 0.95, "sets": {}},
              "chat/deep_rp": {"std": 0.95, "sets": {}}}
    calls = []
    monkeypatch.setattr(report, "ladder_progress",
                        lambda label, cfg: copy.deepcopy(shares), raising=False)
    monkeypatch.setattr(report, "board_filter", lambda label, cfg, **k: [])

    def fake_run(cfg, entries, cases, jobs_by_label=None, record_key=None, **k):
        ids = sorted({j[1] for j in jobs_by_label["m"]})
        calls.append(("run", ids, record_key))
        # the run "scores" what it ran: every set run this round passes
        for c in cases:
            key, n = c["ladder"].split("#")
            if c["id"] in ids:
                shares[key]["sets"][int(n)] = 1.0
        return {"m": {"failed": False, "rows": len(ids), "load_s": 0, "device": "p"}}

    class Guard:
        def __init__(self, *a, **k):
            pass

        def restore(self):
            pass

    monkeypatch.setattr(runner, "run_models", fake_run)
    monkeypatch.setattr(cli, "_ProviderGuard", Guard)
    monkeypatch.setattr(cli, "cmd_judge", lambda a, c: calls.append(("judge", a.models)) or 0)
    cfg = {"models": [{"name": "m", "model_id": "x/y", "provider": "studioforge"}],
           "providers": {"studioforge": {"type": "studioforge", "base_url": "http://s.invalid/v1"}},
           "defaults": {}, "judge": {"candidates": []}}
    args = argparse.Namespace(models="m", profile=None, judge=None, smoke=False, escalate=False)
    assert cli._ladder_phases(args, cfg, ["m"]) == 0
    runs = [c for c in calls if c[0] == "run"]
    # level 1 of both sub-scores together, then work/reasoning set 2, then nothing
    assert runs[0][1] == sorted(["MH16-digit-sum-power", "MH22-dual-base-palindrome",
                                 "CZ11-lex-triple-substring", "RD2-trieste", "RD3-granary",
                                 "CB-E3-lockrun"])
    assert runs[1][1] == ["CZ01-prime-census", "CZ02-segment-crossings", "CZ03-lisp-machine"]
    assert len(runs) == 2 and all(r[2] == "ladder" for r in runs)
    assert [c[0] for c in calls] == ["run", "judge", "run", "judge"]


def test_no_ladder_and_no_tiers_skip_the_ladder(sets, monkeypatch):
    monkeypatch.setattr(report, "ladder_progress", lambda *a: pytest.fail("looked"),
                        raising=False)
    for flag in ("no_ladder", "no_tiers"):
        args = argparse.Namespace(models="m", profile=None, judge=None, smoke=False,
                                  **{flag: True})
        assert cli._ladder_phases(args, {}, ["m"]) == 0


def test_the_bench_profile_takes_the_ladder_path():
    args = argparse.Namespace(profile=None)
    assert cli.uses_ladder(args, {}) is True


# ------------------------------------------------------------- timing

def test_all_records_every_phase_and_prints_the_table(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(config, "RESULTS_DIR", tmp_path)
    (tmp_path / "meta_m.json").write_text(json.dumps({"timing": [
        {"phase": "generation round A", "s": 600, "ctx": 40960, "workers": 8, "jobs": 30}]}),
        encoding="utf-8")

    def run(args, cfg):
        args.ran_labels = ["m"]
        return 0

    monkeypatch.setattr(cli, "cmd_run", run)
    monkeypatch.setattr(cli, "cmd_judge", lambda a, c: 0)
    monkeypatch.setattr(cli, "cmd_report", lambda a, c, full_board=False: 0)
    monkeypatch.setattr(cli, "_ladder_phases", lambda a, c, labels: 0)
    args = argparse.Namespace(cmd="all", models="m", profile=None, smoke=False, plan=False,
                              judge=None, escalate=False, timing=True)
    assert cli.cmd_all(args, {"models": []}) == 0
    meta = json.loads((tmp_path / "meta_m.json").read_text(encoding="utf-8"))
    phases = [t["phase"] for t in meta["timing"]]
    assert phases == ["generation round A", "judge", "report"]
    out = capsys.readouterr().out
    assert "timing: generation" in out and "judge" in out and "total" in out
    assert "generation round A 10.0 min (ctx 40960, 8 wide, 30 jobs)" in out
