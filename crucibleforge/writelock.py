"""One writer per results folder (Docker assessment 2026-11-25, item 3).

Two runs of the same label on one results folder both generated, so a hosted model was paid twice. Every WRITING
command (``run``, ``all``, ``judge``, ``recover``, ``escalate``, ``bench-all --go``, the re-grade tools with
``--go``) takes an exclusive, non-blocking lock on ``<results>/.write.lock``; a second one exits at once naming the
holder (pid, start time, command) and how to go on. The lock (filelock.py: flock on POSIX, msvcrt on Windows) is
dropped by the system when its process dies, so a lock left by a dead process is never in the way; its note is
reported as taken over. A child of the holder (bench-all's per-model runs, ``all`` calling ``judge``) runs under
its parent's lock (found through /proc on Linux, through the environment everywhere). Read-only commands
(``status``, ``report``, ``all --plan``) never take it."""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from pathlib import Path

#: exit code of a command refused because another process writes the same results folder
BUSY_EXIT = 5
NAME = ".write.lock"


class ResultsBusy(SystemExit):
    """Another process holds the results folder's write lock."""


def lock_path(results: Path) -> Path:
    return Path(results) / NAME


def ancestor_holds(path: Path) -> bool:
    """True when this process (an inherited fd) or one of its ancestors has ``path`` open (Linux /proc), or a
    parent marked it held in the environment (filelock.mark_held: every platform)."""
    from .filelock import held_by_parent
    if held_by_parent(path):
        return True
    try:
        target = os.path.realpath(path)
        pid = os.getpid()
        for _ in range(64):
            if pid <= 1:
                return False
            try:
                for fd in Path(f"/proc/{pid}/fd").iterdir():
                    try:
                        if os.path.realpath(os.readlink(fd)) == target:
                            return True
                    except OSError:
                        continue
            except OSError:
                pass
            stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
            pid = int(stat.rsplit(")", 1)[1].split()[1])
    except (OSError, ValueError, IndexError):
        return False
    return False


def _pid_of(note: str) -> int | None:
    parts = note.split()
    try:
        return int(parts[1]) if len(parts) > 1 and parts[0] == "pid" else None
    except ValueError:
        return None


def _alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def acquire(results: Path, command: str, say=None):
    """Take the write lock of ``results``; returns the open file (keep it for the process lifetime) or None (no
    lock, or an ancestor holds it). Raises :class:`ResultsBusy` (exit :data:`BUSY_EXIT`) when
    another live process holds it."""
    from .filelock import lock, mark_held
    path = lock_path(results)
    path.parent.mkdir(parents=True, exist_ok=True)
    if ancestor_holds(path):
        return None
    f = open(path, "a+", encoding="utf-8")
    try:
        lock(f, blocking=False)
    except BlockingIOError:
        f.seek(0)
        holder = f.read().strip()[:300] or "holder unknown"
        f.close()
        print(f"crucibleforge: another command is writing this results folder ({path.parent}):\n"
              f"  {holder}\n"
              "  Wait for it to finish (`crucibleforge status` shows its progress) or stop it, then run this again.\n"
              "  Read-only commands (status, report, all --plan) work meanwhile. Two writers on one folder would\n"
              "  generate the same rows twice (a hosted model would be paid twice).", file=sys.stderr)
        raise ResultsBusy(BUSY_EXIT) from None
    f.seek(0)
    prev = f.read().strip()
    if prev:
        pid = _pid_of(prev)
        if pid != os.getpid():
            (say or (lambda m: print(m, file=sys.stderr)))(
                f"crucibleforge: took over the write lock of {path.parent} left by "
                f"{'a process that is no longer running' if not _alive(pid) else 'a process that no longer holds it'}"
                f" ({prev[:160]})")
    f.seek(0)
    f.truncate()
    f.write(f"pid {os.getpid()} since {datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}: "
            f"crucibleforge {command[:160]}\n")
    f.flush()
    mark_held(path)
    return f


def release(f) -> None:
    """Drop the lock and clear the note (a clean exit leaves nothing to take over)."""
    if f is None:
        return
    from .filelock import unlock, unmark_held
    try:
        unmark_held(f.name)
    except (AttributeError, OSError):
        pass
    try:
        f.seek(0)
        f.truncate()
        f.flush()
        unlock(f)               # before the close: Windows asks for locked regions to be unlocked first
    except (OSError, ValueError):
        pass
    try:
        f.close()
    except (OSError, ValueError):
        pass
