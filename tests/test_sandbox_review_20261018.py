"""Second review of the bubblewrap self-test (2026-10-18).

1. The self-test must exercise the sandbox a graded run really gets. A bwrap
   that can create namespaces but cannot mount /proc (a container with masked
   /proc paths) passed ``--ro-bind / / true`` and then failed every coding
   case -- recorded as the MODEL failing.
2. Graded code must not inherit the operator's secrets from the environment
   (provider API keys, the GUI token). The unsandboxed path has the network.
3. When the opt-in is in force and there is no working sandbox, the CLI says
   so before any work starts, not once at the first coding case.
4. Rows stamped 3.5.1 stay on the board under 3.6.0 (same case hash).

Fake ``bwrap`` executables only; nothing here creates a namespace.
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

import pytest

from crucibleforge import cli, graders, version

PROC_ERROR = "bwrap: Can't mount proc on /newroot/proc: Operation not permitted"

# A bwrap stand-in that behaves like the container case: it starts (exit 0)
# unless it is asked to mount /proc.
_FAKE_BODY = f'''import sys
if "--proc" in sys.argv[1:]:
    sys.stderr.write({PROC_ERROR!r} + "\\n")
    sys.exit(1)
sys.exit(0)
'''


def _fake_bwrap_without_proc(tmp_path, monkeypatch):
    script = tmp_path / "fake_bwrap.py"
    script.write_text(_FAKE_BODY, encoding="utf-8")
    if sys.platform == "win32":
        exe = tmp_path / "bwrap.cmd"
        exe.write_text(f'@"{sys.executable}" "{script}" %*\r\n', encoding="ascii")
    else:
        exe = tmp_path / "bwrap"
        exe.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{script}" "$@"\n', encoding="ascii")
        exe.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path) + os.pathsep + os.environ.get("PATH", ""))
    found = shutil.which("bwrap")
    assert found and Path(found).parent == tmp_path
    monkeypatch.setattr(graders, "_BWRAP", found)
    monkeypatch.setattr(graders, "_BWRAP_PROBES", {})
    monkeypatch.setattr(graders, "_warned_no_sandbox", False)
    return found


# ------------------------------------------------- 1. probe == graded sandbox

def test_a_bwrap_that_cannot_mount_proc_fails_the_self_test(tmp_path, monkeypatch):
    _fake_bwrap_without_proc(tmp_path, monkeypatch)
    ok, err = graders.probe_bwrap(graders._BWRAP)
    assert ok is False
    assert "mount proc" in err
    assert graders.sandbox_available() is False


def test_the_graded_command_is_the_probed_command_plus_the_harness(monkeypatch):
    bw = "/usr/bin/bwrap"
    monkeypatch.setattr(graders, "_BWRAP", bw)
    monkeypatch.setattr(graders, "_BWRAP_PROBES", {bw: (True, "")})
    cmd = graders._sandbox_cmd("/x/harness.py", "/x")
    base = graders._bwrap_args(bw)
    assert cmd[:len(base)] == base
    assert cmd[len(base):] == ["--ro-bind", "/x/harness.py", "/tmp/harness.py",
                               graders._REAL_PY, "-I", "/tmp/harness.py"]
    for flag in ("--unshare-all", "--die-with-parent", "--new-session", "--proc", "--dev"):
        assert flag in base


# ------------------------------------------------- 2. no secrets in the child

def test_graded_code_does_not_see_api_keys_or_the_gui_token(monkeypatch):
    monkeypatch.setattr(graders, "_BWRAP", None)
    monkeypatch.setattr(graders, "ALLOW_UNSANDBOXED", True)
    monkeypatch.setattr(graders, "_warned_no_sandbox", True)
    for name in ("STUDIOFORGE_API_KEY", "DEEPSEEK_API_KEY", "OPENROUTER_API_KEY",
                 "CRUCIBLEFORGE_GUI_TOKEN", "STUDIOFORGE_MCP_PIN"):
        monkeypatch.setenv(name, "sekrit-" + name.lower())
    leaked = ("STUDIOFORGE_API_KEY", "DEEPSEEK_API_KEY", "OPENROUTER_API_KEY",
              "CRUCIBLEFORGE_GUI_TOKEN", "STUDIOFORGE_MCP_PIN")
    resp = "```python\nimport os\ndef env_names():\n    return set(os.environ)\n```"
    out = graders.grade_python_exec(
        resp, {"tests": f"assert not (env_names() & set({leaked!r})), env_names()\n"
                        "assert 'PATH' in env_names()"})
    assert out["grade"] == "pass", out


def test_child_env_keeps_what_an_interpreter_needs(monkeypatch):
    monkeypatch.setenv("SYSTEMROOT", r"C:\Windows")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    env = graders._child_env()
    assert "PATH" in env and "OPENAI_API_KEY" not in env
    assert any(k.upper() == "SYSTEMROOT" for k in env)


# ------------------------------------------- 3. announced before any work

def _quiet_cases(monkeypatch):
    # {} = no registry: no log file is opened, and `cases` tolerates that
    monkeypatch.setattr(cli, "load_config", lambda *a, **kw: {})
    monkeypatch.setattr(cli, "cmd_cases", lambda args, cfg: 0)


def _run_verify(capsys) -> str:
    """`cases verify` with the verifier stubbed out; returns stderr. (stderr, not
    caplog: setup_logging() reconfigures the root logger with force=True.)"""
    with pytest.raises(SystemExit) as e:
        cli.main(["cases", "verify"])
    assert e.value.code == 0
    return capsys.readouterr().err


def test_cli_warns_up_front_when_the_opt_in_meets_a_broken_bwrap(tmp_path, monkeypatch, capsys):
    _fake_bwrap_without_proc(tmp_path, monkeypatch)
    monkeypatch.setattr(graders, "ALLOW_UNSANDBOXED", True)
    _quiet_cases(monkeypatch)
    err = _run_verify(capsys)
    assert "NO sandbox" in err and "self-test failed" in err and "mount proc" in err, err
    assert graders.ALLOW_FLAG in err


def test_cli_warns_up_front_when_there_is_no_bwrap_at_all(monkeypatch, capsys):
    monkeypatch.setattr(graders, "_BWRAP", None)
    monkeypatch.setattr(graders, "ALLOW_UNSANDBOXED", True)
    _quiet_cases(monkeypatch)
    err = _run_verify(capsys)
    assert "NO sandbox" in err and "not on PATH" in err, err


def test_cli_is_silent_when_the_sandbox_works(monkeypatch, capsys):
    bw = "/usr/bin/bwrap"
    monkeypatch.setattr(graders, "_BWRAP", bw)
    monkeypatch.setattr(graders, "_BWRAP_PROBES", {bw: (True, "")})
    monkeypatch.setattr(graders, "ALLOW_UNSANDBOXED", True)
    _quiet_cases(monkeypatch)
    assert "NO sandbox" not in _run_verify(capsys)


# ------------------------------------------- 4. 3.6.0 keeps the 3.5.1 rows

def test_rows_stamped_3_5_1_are_current_under_3_6_0():
    if version.cases_hash() != "36a60ec6":
        pytest.skip("case set moved past 36a60ec6; the equivalence key must move with it")
    revs = version.current_revisions({})
    assert version.SUITE_VERSION == "3.6.0"
    assert {"3.6.0+36a60ec6", "3.5.1+36a60ec6", "3.5.0+d2d09bda"} <= revs
