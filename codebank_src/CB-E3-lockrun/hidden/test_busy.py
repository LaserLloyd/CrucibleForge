import time

from _lr import run, start, wait_for


def test_busy_without_wait_exits_75_fast(tmp_path):
    holder = start(tmp_path, "nightly", "--", "sleep", "3")
    assert wait_for(tmp_path / "nightly.pid")
    t = time.time()
    p = run(tmp_path, "nightly", "--", "echo", "second")
    assert p.returncode == 75 and p.stdout == "" and "lockrun: nightly is busy" in p.stderr, p
    assert time.time() - t < 1.5
    holder.wait(timeout=10)
    assert holder.returncode == 0


def test_wait_then_run_and_wait_timeout(tmp_path):
    holder = start(tmp_path, "nightly", "--", "sleep", "2")
    assert wait_for(tmp_path / "nightly.pid")
    p = run(tmp_path, "-w", "1", "nightly", "--", "echo", "late")
    assert p.returncode == 75, p
    p = run(tmp_path, "-w", "6", "nightly", "--", "echo", "after")
    assert p.returncode == 0 and p.stdout == "after\n", p
    holder.wait(timeout=10)


def test_other_names_do_not_block(tmp_path):
    holder = start(tmp_path, "a", "--", "sleep", "2")
    assert wait_for(tmp_path / "a.pid")
    p = run(tmp_path, "b", "--", "echo", "free")
    assert p.returncode == 0 and p.stdout == "free\n"
    holder.wait(timeout=10)
