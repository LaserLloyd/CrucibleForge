"""4.5.1: the fix rows honour the documented unsandboxed switch every other code test honours.

Third Docker run (2026-11-25): the image has no bubblewrap and runs with CRUCIBLEFORGE_ALLOW_UNSANDBOXED=1 (compose
file), so the code bank ran, but fixcheck refused every fix unit ("fix rows not graded: no sandbox") and a run in the
image could never be ranked. Now the same switch, with the same warning line, grades the fix units without bwrap
(fixopen.install, at CLI start). Where bwrap works nothing changes: fixcheck's own runner, the same checker stamp,
the same verdict; fixrows45 and the checker's files are byte-identical (test_patchtough_20261120 pins them)."""
from __future__ import annotations

import logging
from pathlib import Path

import pytest

from crucibleforge import cli, codeenv, config, fixcheck, fixopen, fixrows45, graders, profiles

ROOT = Path(__file__).resolve().parent.parent
needs_bwrap = pytest.mark.skipif(not graders.sandbox_available(), reason="needs a working bwrap")
FIX = "PATCHFIX-core-4-1"
OWN_RUNNER = fixcheck.run_unit


@pytest.fixture(scope="module")
def std():
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("CF_COMPOSITION", "standard")
        cfg = config.load_config(ROOT / "models.example.yaml")
        prof = profiles.load_profile("bench45", cfg)
        _cfg2, cases = profiles.apply_profile(prof, cfg)
    return {c["id"]: c for c in cases}


def _whole(files: dict) -> str:
    return "\n".join(f"{p}\n````python\n{t}````\n" for p, t in sorted(files.items()))


def _replies(case):
    g, ref = case["grader_config"], case["reference_files"]
    path = sorted(ref)[0]
    return {"reference": _whole(ref),
            "unfixed": _whole({path: g["files"][path]}),                 # the hidden checks fail
            "forged": _whole({path: g["files"][path] + "\nimport sys\nprint('PASS 100%')\nsys.exit(0)\n"})}


def _verdict(case, reply):
    return fixrows45.reply_verdict(case, reply, finish_reason="stop", completion_tokens=100, sent=65536)


def _no_bwrap(monkeypatch, allow=True):
    """This box as the Docker image: no bwrap, the switch in force (or not); then the CLI's start-up hook."""
    monkeypatch.setattr(fixcheck, "run_unit", OWN_RUNNER)               # restored after the test
    monkeypatch.setattr(graders, "_BWRAP", None)
    monkeypatch.setattr(graders, "_warned_no_sandbox", False)
    monkeypatch.setattr(graders, "ALLOW_UNSANDBOXED", allow)
    return fixopen.install()


@needs_bwrap
def test_a_known_fix_row_grades_the_same_with_and_without_the_sandbox(std, monkeypatch):
    case = std[FIX]
    stamp = fixrows45.checker_stamp()
    boxed = {k: _verdict(case, r) for k, r in _replies(case).items()}        # the sandbox, exactly as before
    assert fixcheck.run_unit is OWN_RUNNER
    assert boxed["reference"]["score"] == 1.0 and boxed["unfixed"]["score"] == 0.0 and boxed["forged"]["score"] == 0.0
    assert _no_bwrap(monkeypatch) is True and fixcheck.run_unit is fixopen.run_unit_unsandboxed
    open_ = {k: _verdict(case, r) for k, r in _replies(case).items()}
    assert open_ == boxed                                  # the whole stored block: score, grade, checker, stamp
    assert fixrows45.checker_stamp() == stamp


@needs_bwrap
def test_the_agent_case_grades_the_same_with_and_without_the_sandbox(std, monkeypatch):
    c = std["CB-F1-larder-bughunt"]

    def env(files):
        e = codeenv.RepoEnv(c["env"])
        e.tree.update(files)
        e.writes.extend(sorted(files))
        return e
    boxed = [fixrows45.agent_verdict(env(f), any_cut=False) for f in (c["reference_files"], {})]
    _no_bwrap(monkeypatch)
    open_ = [fixrows45.agent_verdict(env(f), any_cut=False) for f in (c["reference_files"], {})]
    assert [v["score"] for v in boxed] == [1.0, 0.0] and open_ == boxed


def test_without_the_switch_a_fix_row_still_waits_for_a_sandbox(std, monkeypatch):
    assert _no_bwrap(monkeypatch, allow=False) is False
    assert fixcheck.run_unit is OWN_RUNNER
    case = std[FIX]
    assert _verdict(case, _replies(case)["reference"]) == {"unavailable": True}


def test_the_switch_prints_the_same_warning_line_as_every_code_test(std, monkeypatch, caplog):
    _no_bwrap(monkeypatch)
    case = std[FIX]
    with caplog.at_level(logging.WARNING, logger="crucibleforge.graders"):
        v = _verdict(case, _replies(case)["reference"])
    assert v["score"] == 1.0
    lines = [r.getMessage() for r in caplog.records if "WITHOUT a sandbox" in r.getMessage()]
    assert len(lines) == 1 and lines[0].startswith(f"{graders.ALLOW_FLAG} is in force: executing model-authored code")


def test_with_a_working_sandbox_the_switch_changes_nothing(monkeypatch):
    monkeypatch.setattr(fixcheck, "run_unit", OWN_RUNNER)
    monkeypatch.setattr(graders, "sandbox_available", lambda: True)
    monkeypatch.setattr(graders, "ALLOW_UNSANDBOXED", True)
    assert fixopen.install() is False and fixcheck.run_unit is OWN_RUNNER


def test_the_cli_installs_it_at_start_whatever_the_command(monkeypatch):
    seen = []
    monkeypatch.setattr(fixopen, "install", lambda: seen.append(1) or False)
    monkeypatch.setattr(cli, "cmd_spend", lambda args: 0)
    with pytest.raises(SystemExit):
        cli.main(["spend", "status"])
    assert seen == [1]


def test_the_driver_rewrite_fails_closed_on_a_changed_driver(monkeypatch, tmp_path):
    from crucibleforge import codebank
    monkeypatch.setattr(fixcheck, "DRIVER_PY", fixcheck.DRIVER_PY.replace('"/out/obs.json"', '"/out/o.json"'))
    with pytest.raises(codebank.Unsupported):
        fixopen._open_driver(tmp_path / "h", tmp_path / "w", tmp_path / "out")
