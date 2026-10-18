"""Small cross-platform helpers for the test-suite."""
from __future__ import annotations

import shutil
import sys
from pathlib import Path


def posix_bash() -> str | None:
    """A real POSIX ``bash``, or ``None`` when there is not one.

    On Windows ``bash`` on PATH is usually the WSL launcher in System32: it
    ignores the Windows path and prints an RPC error unless a distro is
    installed, so a test that merely checks ``shutil.which("bash")`` fails there
    for the wrong reason. Prefer the bash that ships with Git for Windows (found
    via ``git``), and refuse the launcher.
    """
    def is_wsl_launcher(p: str) -> bool:
        low = p.replace("/", "\\").lower()
        return "\\windows\\system32\\" in low or "\\windowsapps\\" in low

    candidates: list[str] = []
    found = shutil.which("bash")
    if found:
        candidates.append(found)
    if sys.platform == "win32":
        git = shutil.which("git")
        if git:
            root = Path(git).resolve().parent.parent      # <...>/Git/cmd/git.exe -> <...>/Git
            candidates += [str(root / "bin" / "bash.exe"), str(root.parent / "bin" / "bash.exe")]
    for c in candidates:
        if Path(c).is_file() and not is_wsl_launcher(c):
            return c
    return None
