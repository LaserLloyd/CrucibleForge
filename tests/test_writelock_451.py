"""Docker assessment 2026-11-25, item 3: one writer per results folder. A second writing command exits at once
naming the holder; a lock left by a dead process is taken over; read-only commands never take it."""
from __future__ import annotations

import subprocess
import sys
import types

import pytest

from crucibleforge import cli, writelock


def test_a_second_writer_exits_at_once_naming_the_holder(tmp_path):
    hold = ("import sys; from pathlib import Path; from crucibleforge import writelock as w\n"
            f"f = w.acquire(Path({str(tmp_path)!r}), 'all --models x'); print('held', flush=True)\n"
            "sys.stdin.read(); w.release(f)\n")
    holder = subprocess.Popen([sys.executable, "-c", hold], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    try:
        assert holder.stdout.readline().strip() == "held"
        code = ("from pathlib import Path; from crucibleforge import writelock as w\n"
                f"w.acquire(Path({str(tmp_path)!r}), 'run --models x')\n")
        p = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60)
        assert p.returncode == writelock.BUSY_EXIT
        assert f"pid {holder.pid} since " in p.stderr and "all --models x" in p.stderr
        assert "Read-only commands" in p.stderr
    finally:
        holder.communicate("", timeout=60)
    p = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60)
    assert p.returncode == 0                                       # free again after a clean release


def test_a_lock_left_by_a_dead_process_is_taken_over(tmp_path):
    code = ("import os; from pathlib import Path; from crucibleforge import writelock as w\n"
            f"w.acquire(Path({str(tmp_path)!r}), 'all --models x'); os._exit(9)\n")   # dies holding it
    assert subprocess.run([sys.executable, "-c", code], timeout=60).returncode == 9
    said = []
    f = writelock.acquire(tmp_path, "all --models x", say=said.append)
    assert f is not None and said and "no longer running" in said[0]
    writelock.release(f)


def test_a_child_of_the_holder_runs_under_its_lock(tmp_path):
    held = writelock.acquire(tmp_path, "bench-all --go")
    try:
        assert writelock.acquire(tmp_path, "all --models x") is None          # same process: already held
    finally:
        writelock.release(held)


@pytest.mark.parametrize("ns,want", [
    (dict(cmd="run"), True), (dict(cmd="all"), True), (dict(cmd="judge"), True), (dict(cmd="recover"), True),
    (dict(cmd="all", plan=True), False), (dict(cmd="status"), False), (dict(cmd="report"), False),
    (dict(cmd="bench-all"), False), (dict(cmd="bench-all", go=True), True),
    (dict(cmd="judge", shard="1/2"), False),
])
def test_which_commands_take_the_lock(ns, want):
    assert cli.writes_results(types.SimpleNamespace(**ns)) is want
