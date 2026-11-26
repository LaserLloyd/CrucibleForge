"""Liveness without os.kill(pid, 0) on Windows, where signal 0 is CTRL_C_EVENT: a real Ctrl-C to the console
(CI on Windows died with KeyboardInterrupt mid-run, 2026-11-26)."""
from __future__ import annotations

import os
import subprocess
import sys

import pytest

from crucibleforge import pidcheck, spend, writelock


def test_own_process_is_alive_and_a_finished_one_is_not():
    assert pidcheck.pid_alive(os.getpid())
    p = subprocess.Popen([sys.executable, "-c", "pass"])
    p.wait()
    assert not pidcheck.pid_alive(p.pid) or sys.platform != "linux"   # a reused id is possible, not on a fresh exit
    for bad in (None, 0, -5, "x"):
        assert not pidcheck.pid_alive(bad)


@pytest.mark.parametrize("probe", [pidcheck.pid_alive, writelock._alive, spend._pid_alive])
def test_windows_never_sends_signal_zero(monkeypatch, probe):
    def no_kill(*a):
        raise AssertionError("os.kill(pid, 0) on Windows is CTRL_C_EVENT")
    monkeypatch.setattr(os, "kill", no_kill)
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(pidcheck, "_win_alive", lambda pid: pid == 4242)
    assert probe(4242) is True
    assert probe(4343) is False
