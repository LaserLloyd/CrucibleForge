"""Is a process alive? One answer that is safe on every platform.

``os.kill(pid, 0)`` is the POSIX liveness probe. On Windows signal 0 is ``CTRL_C_EVENT``: ``os.kill(pid, 0)`` calls
``GenerateConsoleCtrlEvent(CTRL_C_EVENT, pid)``, which can deliver a real Ctrl-C to every process on the console,
the caller included (CI on Windows died with ``KeyboardInterrupt`` mid-run, 2026-11-26). On Windows this asks the
kernel instead: open the process for a limited query and read its exit code."""
from __future__ import annotations

import os
import sys

_STILL_ACTIVE = 259
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_ERROR_ACCESS_DENIED = 5


def pid_alive(pid) -> bool:
    """True when a process with this id exists (a process we may not signal counts as alive); False for a
    missing, zero, negative or unreadable id."""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    if sys.platform == "win32":
        return _win_alive(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return True                     # cannot tell: treat as alive (a lock or reservation is kept, never stolen)
    return True


def _win_alive(pid: int) -> bool:
    import ctypes
    from ctypes import wintypes
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.OpenProcess.restype = wintypes.HANDLE
    k32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    h = k32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return ctypes.get_last_error() == _ERROR_ACCESS_DENIED     # exists, owned by someone else
    try:
        code = wintypes.DWORD()
        if not k32.GetExitCodeProcess(h, ctypes.byref(code)):
            return True
        return code.value == _STILL_ACTIVE
    finally:
        k32.CloseHandle(h)
