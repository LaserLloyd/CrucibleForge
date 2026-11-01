import os
import subprocess
from pathlib import Path


def run(*args):
    return subprocess.run(["bash", str(Path("rotate.sh").resolve()), *map(str, args)],
                          capture_output=True, text=True, env={"PATH": "/usr/bin:/bin", "LC_ALL": "C"})


def make(d, names, mtime_order=None):
    """Create files; mtime_order (list of names) gets increasing mtimes."""
    for n in names:
        (d / n).write_text(n)
    for i, n in enumerate(mtime_order or []):
        os.utime(d / n, (1_700_000_000 + i * 3600, 1_700_000_000 + i * 3600))


def left(d):
    return sorted(p.name for p in d.iterdir())
