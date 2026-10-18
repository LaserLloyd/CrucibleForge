"""The bwrap self-test must prove the network is isolated, not just that bwrap starts.

CI finding (2026-10-19): on a GitHub runner bwrap is on PATH but user
namespaces are restricted, so a "sandbox" test that only checked
``shutil.which("bwrap")`` ran graded code with no isolation and watched a
socket connect succeed. ``--unshare-all`` on the command line is a request, not
a fact, so ``probe_bwrap`` now connects to a loopback listener from INSIDE the
sandbox and treats a successful connect as "no sandbox".

Fake ``bwrap`` executables only; nothing here creates a namespace.
"""
from __future__ import annotations

import logging
import os
import shutil
import socket
import subprocess
import sys
from pathlib import Path

import pytest

from crucibleforge import graders

# A bwrap stand-in that starts fine (so a start-only self-test would pass) but
# does NOT isolate anything: it drops the bwrap flags and runs the command that
# follows them as an ordinary child of the caller.
_PASSTHROUGH_BODY = f'''import subprocess, sys
argv = sys.argv[1:]
i = argv.index({graders._REAL_PY!r})
sys.exit(subprocess.run(argv[i:]).returncode)
'''


def _fake_passthrough_bwrap(tmp_path, monkeypatch):
    script = tmp_path / "fake_bwrap.py"
    script.write_text(_PASSTHROUGH_BODY, encoding="utf-8")
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


def _run_snippet(port: int) -> int:
    return subprocess.run(
        [sys.executable, "-I", "-c", graders._NET_PROBE_SNIPPET, str(port)],
        capture_output=True, timeout=30).returncode


def test_probe_snippet_exit_codes():
    """0 = the connect failed (isolated), NET_REACHABLE_RC = it connected."""
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]
    try:
        assert _run_snippet(port) == graders.NET_REACHABLE_RC
    finally:
        srv.close()
    assert _run_snippet(port) == 0   # nothing listening any more


def test_a_bwrap_that_starts_but_does_not_isolate_the_network_is_not_a_sandbox(tmp_path, monkeypatch):
    _fake_passthrough_bwrap(tmp_path, monkeypatch)
    ok, err = graders.probe_bwrap(graders._BWRAP)
    assert ok is False
    assert "network isolation is not in effect" in err
    assert graders.sandbox_available() is False
    assert "network isolation" in graders.unsandboxed_reason()


def test_unisolated_bwrap_is_refused_without_the_flag(tmp_path, monkeypatch):
    _fake_passthrough_bwrap(tmp_path, monkeypatch)
    monkeypatch.setattr(graders, "ALLOW_UNSANDBOXED", False)
    with pytest.raises(graders.SandboxUnavailable) as e:
        graders.require_sandbox()
    assert "network isolation is not in effect" in str(e.value)
    assert graders.ALLOW_FLAG in str(e.value)
    # grading refuses rather than scoring a fail, and never runs the code
    with pytest.raises(graders.SandboxUnavailable):
        graders.grade_python_exec("```python\ndef f():\n    return 1\n```",
                                  {"tests": "assert f() == 1"})


def test_unisolated_bwrap_with_the_flag_runs_unsandboxed_and_warns(tmp_path, monkeypatch, caplog):
    _fake_passthrough_bwrap(tmp_path, monkeypatch)
    monkeypatch.setattr(graders, "ALLOW_UNSANDBOXED", True)
    graders.require_sandbox()   # must not raise
    with caplog.at_level(logging.WARNING, logger="crucibleforge.graders"):
        cmd = graders._sandbox_cmd("/x/harness.py", "/x")
    assert cmd == [sys.executable, "-I", "/x/harness.py"]   # never the bwrap command
    warnings = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
    assert len(warnings) == 1 and "WITHOUT a sandbox" in warnings[0]
    assert "network isolation is not in effect" in warnings[0]


@pytest.mark.skipif(not shutil.which("bwrap"), reason="no bwrap on PATH")
def test_real_bwrap_probe_is_consistent_with_the_graded_run():
    """Whatever this machine's bwrap can do, the probe's verdict and the graded
    run agree: a verdict of 'available' means the loopback connect is blocked;
    otherwise the run is refused. Never 'available' and reachable."""
    graders._BWRAP_PROBES.pop(graders._BWRAP, None)
    ok, err = graders.probe_bwrap(graders._BWRAP)
    assert ok or err   # a failure always says why
    if not ok:
        assert graders.sandbox_available() is False
