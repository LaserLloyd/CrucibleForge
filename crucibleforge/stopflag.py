"""Process-wide HARD stop (SIGTERM / systemd stop), shared by every layer.

``runner.STOP`` is the cooperative stop the GUI and ``bench-all --until``
use: the rows in flight finish, nothing new starts. ``ABORT`` is the hard
one a SIGTERM sets (cli.main's handler): nothing new starts, retry/backoff
loops and polling sleeps give up at once, executors are shut down without
waiting, the run's meta is marked ``stopped`` and the GPU lease released.

Before suite 3.9 a SIGTERM raised SystemExit inside whatever the main thread
was doing; ``with ThreadPoolExecutor`` then joined worker threads stuck in a
socket connect to an unreachable rig, and systemd SIGABRTed the process
46-90 s later with the meta unfinished and the lease held (STEP4 defect 1,
2026-10-28: the rig's Tailscale key expired mid-run).

This module imports nothing from the package so api.py, studioforge.py and
runner.py can all use it.
"""
from __future__ import annotations

import threading
import time

ABORT = threading.Event()
_REAL_SLEEP = time.sleep


def aborted() -> bool:
    return ABORT.is_set()


def sleep(seconds: float, sleeper=None) -> bool:
    """``time.sleep`` that ends early on ABORT. True = aborted.

    ``sleeper`` = the caller module's ``time.sleep`` as looked up at call
    time: when a test has monkeypatched it (to record or skip the pauses),
    that function is called instead, so those tests keep their meaning."""
    if ABORT.is_set():
        return True
    if sleeper is not None and sleeper is not _REAL_SLEEP:
        sleeper(seconds)
        return ABORT.is_set()
    if seconds and seconds > 0:
        return ABORT.wait(seconds)
    return ABORT.is_set()
