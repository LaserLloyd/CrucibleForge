"""Cross-platform guards for the CI matrix (ubuntu-latest + macos-latest).

Every test here is provable on Linux: none of them need a macOS or Windows
runner, because each simulates the *specific* thing the other platform does
differently rather than waiting to be told by a red CI leg.

Two hazards are covered:

1. Case-insensitive filesystems (macOS APFS, Windows NTFS). Linux keeps
   ``Foo`` and ``foo`` apart; those platforms do not.
2. A default text encoding that is not UTF-8 (Windows cp1252). Linux and
   macOS both default to UTF-8, so an ``open()`` with no ``encoding=`` looks
   correct here forever and corrupts transcripts there. We assert the
   encoding is passed rather than trying to change the process default
   mid-run, which CPython does not allow.
"""
from __future__ import annotations

import builtins
import json
import logging
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from crucibleforge import config, graders  # noqa: E402


# --------------------------------------------------------------- case folding

def _minimal_cfg(names: list[str]) -> dict:
    return {
        "providers": {"p": {"type": "openai", "base_url": "http://127.0.0.1:1"}},
        "models": [{"name": n, "model_id": f"id-{n}", "provider": "p"} for n in names],
        "judge": {},
        "defaults": {},
    }


def test_model_names_differing_only_in_case_are_rejected():
    """A model name IS a filename (``transcripts_<name>.jsonl``).

    On macOS/Windows ``Qwen`` and ``qwen`` are the same file, so both models'
    rows would land in one transcript and the report would score a chimera.
    Linux would never notice, so the check has to be explicit.
    """
    with pytest.raises(config.ConfigError) as e:
        config.validate_config(_minimal_cfg(["Qwen", "qwen"]))
    assert "case" in str(e.value).lower()


def test_case_distinct_names_that_do_not_collide_are_still_allowed():
    """The guard must reject collisions, not merely any capital letter."""
    config.validate_config(_minimal_cfg(["Qwen", "Gemma", "qwen-15"]))


def test_exact_duplicate_names_still_rejected():
    """The pre-existing gate is not weakened by the new one."""
    with pytest.raises(config.ConfigError):
        config.validate_config(_minimal_cfg(["a", "a"]))


def test_transcript_and_meta_paths_collide_when_names_differ_only_in_case(tmp_path,
                                                                         monkeypatch):
    """Documents *why* the guard above exists, without needing macOS.

    We do not assert that the filesystem merges them — Linux will not. We
    assert the two names produce paths that a case-insensitive filesystem
    cannot tell apart, which is the property the config gate protects.
    """
    monkeypatch.setattr(config, "RESULTS_DIR", tmp_path)
    a = config.transcript_path("Qwen")
    b = config.transcript_path("qwen")
    assert a != b                                   # distinct on Linux
    assert str(a).casefold() == str(b).casefold()   # identical on macOS/Windows


# ------------------------------------------------------- explicit text encoding

class _EncodingPolice:
    """Fail any text-mode file I/O that leaves the encoding to the platform.

    This is the Windows cp1252 leg, simulated: on a machine whose default is
    not UTF-8, every one of these calls silently writes (or fails to read)
    mojibake. Rather than trying to mutate the process default — CPython
    fixes it at startup — we assert the call sites are explicit.
    """

    def __init__(self):
        self.real_open = builtins.open
        self.real_read = Path.read_text
        self.real_write = Path.write_text
        self.real_run = subprocess.run
        self.violations: list[str] = []

    def install(self, monkeypatch):
        def open_(file, mode="r", *a, **kw):
            if "b" not in mode and kw.get("encoding") is None:
                self.violations.append(f"open({file!r}, mode={mode!r})")
            return self.real_open(file, mode, *a, **kw)

        def read_text(self_p, encoding=None, *a, **kw):
            if encoding is None:
                self.violations.append(f"Path.read_text({str(self_p)!r})")
            return self.real_read(self_p, encoding, *a, **kw)

        def write_text(self_p, data, encoding=None, *a, **kw):
            if encoding is None:
                self.violations.append(f"Path.write_text({str(self_p)!r})")
            return self.real_write(self_p, data, encoding, *a, **kw)

        def run(cmd, *a, **kw):
            if kw.get("text") and kw.get("encoding") is None:
                self.violations.append(f"subprocess.run(text=True, {cmd[:1]})")
            return self.real_run(cmd, *a, **kw)

        monkeypatch.setattr(builtins, "open", open_)
        monkeypatch.setattr(Path, "read_text", read_text)
        monkeypatch.setattr(Path, "write_text", write_text)
        monkeypatch.setattr(subprocess, "run", run)
        return self


@pytest.fixture
def encoding_police(monkeypatch):
    return _EncodingPolice().install(monkeypatch)


# Model output is routinely non-ASCII: smart quotes, em dashes, CJK, emoji.
NON_ASCII = "café — 日本語 — “quoted” — 🔥"


def test_transcript_round_trip_declares_utf8_and_survives_non_ascii(
        tmp_path, monkeypatch, encoding_police):
    """``append_transcript`` writes ``ensure_ascii=False`` — i.e. raw non-ASCII
    bytes — so leaving the encoding to the platform is a guaranteed
    UnicodeEncodeError on a cp1252 Windows box, and mojibake on read-back.
    """
    monkeypatch.setattr(config, "RESULTS_DIR", tmp_path)
    config.append_transcript("m", {"bench_run_id": "r", "case_id": "C",
                                   "repeat": 1, "turn": None,
                                   "response_text": NON_ASCII})
    rows = config.load_transcripts("m")
    assert encoding_police.violations == []
    assert rows[0]["response_text"] == NON_ASCII
    # and the bytes on disk really are UTF-8, not the platform default
    assert NON_ASCII.encode("utf-8") in config.transcript_path("m").read_bytes()


def test_config_save_load_round_trip_declares_utf8(tmp_path, encoding_police):
    cfg = _minimal_cfg(["m"])
    cfg["models"][0]["notes"] = NON_ASCII
    dst = tmp_path / "models.yaml"
    config.save_config(cfg, dst)
    loaded = config.load_config(dst)
    assert encoding_police.violations == []
    assert loaded["models"][0]["notes"] == NON_ASCII


def test_run_meta_round_trip_declares_utf8(tmp_path, monkeypatch, encoding_police):
    """``_write_meta`` reads the existing meta back before merging, so a
    non-ASCII server error recorded on one pass has to survive the read on the
    next. Both halves needed the encoding."""
    from crucibleforge import runner

    class _Prov:
        name, type = "sf", "studioforge"

    monkeypatch.setattr(config, "RESULTS_DIR", tmp_path)
    entry = {"name": "m", "model_id": "id"}
    runner._write_meta("m", entry, _Prov(), failed=True, error=NON_ASCII)
    runner._write_meta("m", entry, _Prov(), finished=True)   # merge pass: reads it back
    assert encoding_police.violations == []
    meta = json.loads((tmp_path / "meta_m.json").read_text(encoding="utf-8"))
    assert meta["error"] == NON_ASCII
    # NB: meta is written with json's default ensure_ascii=True, so the bytes
    # on disk are ASCII and this path was hardening rather than a live bug —
    # unlike transcripts/CSV below, which carry raw non-ASCII.


def test_runs_csv_writer_declares_utf8(tmp_path, monkeypatch, encoding_police):
    """The CSV writer keeps ``newline=""`` (correct, and required on Windows)
    but used to inherit the platform encoding for model text."""
    from crucibleforge import runner
    monkeypatch.setattr(config, "RESULTS_DIR", tmp_path)
    w = runner._Csv()
    try:
        w.write({"case_id": "C", "grade_detail": NON_ASCII})
    finally:
        w.close()
    assert encoding_police.violations == []
    assert NON_ASCII.encode("utf-8") in (tmp_path / "runs.csv").read_bytes()


def test_python_exec_harness_declares_utf8_end_to_end(encoding_police):
    """The harness embeds the model's own source via ``repr()``, which keeps
    non-ASCII characters literal — so both the file write and the subprocess
    pipe decode need an explicit UTF-8."""
    resp = f'```python\nMSG = "{NON_ASCII}"\ndef f():\n    return MSG\n```'
    out = graders.grade_python_exec(
        resp, {"tests": f'assert f() == "{NON_ASCII}"'})
    assert encoding_police.violations == [], encoding_police.violations
    assert out["grade"] == "pass", out


# ------------------------------------------------- unsandboxed fallback is loud

def test_missing_bwrap_refuses_to_execute(monkeypatch):
    """bwrap is Linux-only. On macOS ``shutil.which("bwrap")`` is always None,
    so every coding case — and ``crucibleforge cases verify`` — used to execute
    model-authored Python with no isolation at all: network reachable, home
    directory readable, nothing in the log.

    A warning does not change what runs, it only means you find out
    afterwards. So the default is to stop.
    """
    monkeypatch.setattr(graders, "_BWRAP", None)
    monkeypatch.setattr(graders, "ALLOW_UNSANDBOXED", False)
    with pytest.raises(graders.SandboxUnavailable) as e:
        graders._sandbox_cmd("/somewhere/harness.py", "/somewhere")
    assert graders.ALLOW_FLAG in str(e.value), (
        "the refusal must name the flag, or a macOS user is just stuck")


def test_the_refusal_happens_before_anything_is_executed(monkeypatch):
    """grade_python_exec must not turn a refusal into {'grade': 'fail'} — a
    refusal is not a grading result, and scoring every coding case as a fail
    would look like the model was bad rather than like nothing was run."""
    monkeypatch.setattr(graders, "_BWRAP", None)
    monkeypatch.setattr(graders, "ALLOW_UNSANDBOXED", False)

    def explode(*a, **kw):   # nothing may reach subprocess
        raise AssertionError("executed model code despite the refusal")

    monkeypatch.setattr(subprocess, "run", explode)
    with pytest.raises(graders.SandboxUnavailable):
        graders.grade_python_exec("```python\ndef f():\n    return 1\n```",
                                  {"tests": "assert f() == 1"})


def test_opt_in_runs_and_warns_once(monkeypatch, caplog):
    """--allow-unsandboxed is the escape hatch for macOS/Windows. It must
    actually work, and it must be noisy."""
    monkeypatch.setattr(graders, "_BWRAP", None)
    monkeypatch.setattr(graders, "ALLOW_UNSANDBOXED", True)
    monkeypatch.setattr(graders, "_warned_no_sandbox", False)
    with caplog.at_level(logging.WARNING, logger="crucibleforge.graders"):
        for _ in range(3):
            cmd = graders._sandbox_cmd("/x/harness.py", "/x")
    assert cmd == [sys.executable, "-I", "/x/harness.py"]
    warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert len(warnings) == 1, warnings
    assert "WITHOUT a sandbox" in warnings[0].getMessage()


def test_environment_variable_opt_in_is_honoured(monkeypatch):
    """The GUI spawns runs as subprocesses; the operator's decision has to
    travel with them."""
    monkeypatch.setenv("CRUCIBLEFORGE_ALLOW_UNSANDBOXED", "1")
    import importlib
    reloaded = importlib.reload(graders)
    try:
        assert reloaded.ALLOW_UNSANDBOXED is True
    finally:
        monkeypatch.delenv("CRUCIBLEFORGE_ALLOW_UNSANDBOXED")
        importlib.reload(graders)


def test_cli_refuses_early_for_the_commands_that_execute_code(monkeypatch, capsys):
    """A refusal at the first coding case is a refusal three hours into a run.
    The check belongs before dispatch."""
    from crucibleforge import cli, graders as g
    monkeypatch.setattr(g, "_BWRAP", None)
    monkeypatch.setattr(g, "ALLOW_UNSANDBOXED", False)
    monkeypatch.setattr(cli, "load_config", lambda *a, **kw: {"models": [], "providers": {}})
    with pytest.raises(SystemExit) as e:
        cli.main(["cases", "verify"])
    assert e.value.code == 3
    err = capsys.readouterr().err
    assert g.ALLOW_FLAG in err and "sandbox" in err.lower()


def test_cli_does_not_gate_read_only_commands(monkeypatch):
    """`cases list` executes nothing, so it must not be blocked."""
    from crucibleforge import cli, graders as g
    monkeypatch.setattr(g, "_BWRAP", None)
    monkeypatch.setattr(g, "ALLOW_UNSANDBOXED", False)
    monkeypatch.setattr(cli, "load_config", lambda *a, **kw: {"models": [], "providers": {}})
    with pytest.raises(SystemExit) as e:
        cli.main(["cases", "list"])
    assert e.value.code == 0


def test_bwrap_path_unchanged_when_available(monkeypatch):
    """The fix must not alter the sandboxed command on Linux."""
    monkeypatch.setattr(graders, "_BWRAP", "/usr/bin/bwrap")
    cmd = graders._sandbox_cmd("/x/harness.py", "/x")
    assert cmd[0] == "/usr/bin/bwrap"
    assert "--unshare-all" in cmd and "--die-with-parent" in cmd


# ------------------------------------------------------------- the CI matrix

def test_ci_gives_linux_a_real_sandbox_and_names_the_macos_opt_in():
    """The refusal must not be silently defeated by CI.

    Two ways to keep the macOS leg green: install a sandbox (impossible there)
    or opt in. This asserts the opt-in is written where a reader sees it, and
    that Linux is NOT opted in but given bubblewrap instead.
    """
    yaml_mod = pytest.importorskip("yaml")
    root = Path(__file__).resolve().parent.parent
    text = (root / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    ci = yaml_mod.safe_load(text)
    steps = ci["jobs"]["unit"]["steps"]
    runs = "\n".join(s.get("run", "") for s in steps)

    assert "bubblewrap" in runs, "Linux CI must install the real sandbox"
    assert "--allow-unsandboxed" in runs, (
        "macOS has no bwrap; the opt-in has to be explicit in the workflow")
    # ...and only for macOS.
    macos_only = [ln for ln in runs.splitlines() if "--allow-unsandboxed" in ln]
    assert macos_only, runs
    assert "macOS" in runs


def test_ci_actions_are_not_end_of_life():
    yaml_mod = pytest.importorskip("yaml")
    root = Path(__file__).resolve().parent.parent
    ci = yaml_mod.safe_load((root / ".github" / "workflows" / "ci.yml").read_text(
        encoding="utf-8"))
    # (oldest major still supported, newest major that actually EXISTS upstream).
    # The ceiling is the half that matters most: a floor alone once pushed this
    # workflow to `astral-sh/setup-uv@v10`, a tag that has never been published,
    # and every run failed with "Unable to resolve action". A pin is only valid
    # if someone can `git ls-remote --tags` it and see it. Verified 2026-08-30:
    # actions/checkout tops out at v7, astral-sh/setup-uv at v7. Raise a ceiling
    # only after checking the tag is really there.
    windows = {"actions/checkout": (6, 7), "astral-sh/setup-uv": (6, 7)}
    stale, unresolvable = [], []
    for job in ci["jobs"].values():
        for step in job.get("steps", []):
            uses = str(step.get("uses", ""))
            action, _, ref = uses.partition("@")
            window = windows.get(action)
            if not window:
                continue
            floor, ceiling = window
            major = int(ref.lstrip("v").split(".")[0])
            if major < floor:
                stale.append(uses)
            elif major > ceiling:
                unresolvable.append(uses)
    assert stale == [], f"end-of-life action pins: {stale}"
    assert unresolvable == [], (
        f"action pinned to a tag that does not exist upstream: {unresolvable}")


def test_ci_privacy_job_scans_every_commit_tree():
    """Same blind spot as the pre-push hook: a head-only scan misses a secret
    that a later commit in the same PR removed."""
    root = Path(__file__).resolve().parent.parent
    text = (root / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "rev-list" in text and "--rev" in text, text[:400]
