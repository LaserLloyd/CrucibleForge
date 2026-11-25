"""File locks on POSIX and Windows: the one place the package locks a file.

POSIX: ``fcntl.flock`` on the whole file. Windows: ``msvcrt.locking`` on one lock byte far past any content
(:data:`LOCK_OFFSET`), so the lock never stops another process from reading the holder note at the start of the
file. Both are released by :func:`unlock`, by closing the file and by the process's death.

Windows has no shared lock: ``exclusive=False`` takes an exclusive one there (judge shards then take turns
instead of sharing the rig lock; nothing runs unlocked). ``blocking=False`` raises :class:`BlockingIOError` when
another process holds the lock, on both platforms."""
from __future__ import annotations

import os
import sys
import time

#: the byte msvcrt locks (beyond the end of every lock, ledger or counter file this package writes)
LOCK_OFFSET = 0x7FFF_FFF0
#: seconds between attempts of a blocking lock on Windows (msvcrt's own blocking mode gives up after 10 s)
WIN_POLL_S = 0.05


def _fd(f) -> int:
    if isinstance(f, int):
        return f
    try:
        f.flush()
    except (AttributeError, OSError, ValueError):
        pass
    return f.fileno()


def _win() -> bool:
    return sys.platform == "win32"


def lock(f, *, exclusive: bool = True, blocking: bool = True) -> None:
    """Lock the open file (or fd) ``f``."""
    fd = _fd(f)
    if not _win():
        import fcntl
        mode = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
        fcntl.flock(fd, mode if blocking else mode | fcntl.LOCK_NB)
        return
    import msvcrt
    while True:
        here = os.lseek(fd, 0, os.SEEK_CUR)
        try:
            os.lseek(fd, LOCK_OFFSET, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            return
        except OSError:
            if not blocking:
                raise BlockingIOError("the file is locked by another process") from None
        finally:
            os.lseek(fd, here, os.SEEK_SET)
        time.sleep(WIN_POLL_S)


def unlock(f) -> None:
    """Release a lock taken by :func:`lock` (closing the file releases it too)."""
    fd = _fd(f)
    if not _win():
        import fcntl
        fcntl.flock(fd, fcntl.LOCK_UN)
        return
    import msvcrt
    here = os.lseek(fd, 0, os.SEEK_CUR)
    try:
        os.lseek(fd, LOCK_OFFSET, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
    finally:
        os.lseek(fd, here, os.SEEK_SET)


#: the locks this process holds, for its child processes (Windows has no /proc to find a parent's open files)
HELD_ENV = "CRUCIBLEFORGE_HELD_LOCKS"


def _key(path) -> str:
    return os.path.normcase(os.path.realpath(path))


def mark_held(path) -> None:
    """Say in the environment (inherited by child processes) that this process holds the lock file ``path``."""
    held = [p for p in os.environ.get(HELD_ENV, "").split(os.pathsep) if p]
    if _key(path) not in held:
        os.environ[HELD_ENV] = os.pathsep.join(held + [_key(path)])


def unmark_held(path) -> None:
    held = [p for p in os.environ.get(HELD_ENV, "").split(os.pathsep) if p and p != _key(path)]
    if held:
        os.environ[HELD_ENV] = os.pathsep.join(held)
    else:
        os.environ.pop(HELD_ENV, None)


def held_by_parent(path) -> bool:
    """True when a parent process (or this one) marked ``path`` as held (:func:`mark_held`)."""
    return _key(path) in os.environ.get(HELD_ENV, "").split(os.pathsep)
