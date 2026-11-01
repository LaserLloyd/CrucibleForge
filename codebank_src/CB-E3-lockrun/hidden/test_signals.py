import os
import signal
import time

from _lr import start, wait_for


def _alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    try:
        with open(f"/proc/{pid}/stat") as f:
            return f.read().split()[2] != "Z"
    except FileNotFoundError:
        return False


def test_sigterm_forwarded_cleaned_up(tmp_path):
    p = start(tmp_path, "job", "--", "sleep", "30")
    assert wait_for(tmp_path / "job.pid")
    child = int((tmp_path / "job.pid").read_text())
    assert child != p.pid
    p.send_signal(signal.SIGTERM)
    p.wait(timeout=8)
    assert p.returncode == 143, p.returncode
    time.sleep(0.2)
    assert not _alive(child)
    assert not (tmp_path / "job.pid").exists()


def test_sigint_and_pidfile_removed_after_normal_exit(tmp_path):
    p = start(tmp_path, "job2", "--", "sleep", "30")
    assert wait_for(tmp_path / "job2.pid")
    p.send_signal(signal.SIGINT)
    p.wait(timeout=8)
    assert p.returncode == 130
    from _lr import run
    q = run(tmp_path, "job3", "--", "true")
    assert q.returncode == 0 and not (tmp_path / "job3.pid").exists()
