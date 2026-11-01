import os
import subprocess
import time
from pathlib import Path

SCRIPT = str(Path("lockrun.sh").resolve())


def env(d):
    return {"PATH": "/usr/bin:/bin", "LC_ALL": "C", "LOCKDIR": str(d)}


def run(d, *args, **kw):
    return subprocess.run(["bash", SCRIPT, *map(str, args)], capture_output=True, text=True, env=env(d),
                          timeout=20, **kw)


def start(d, *args):
    return subprocess.Popen(["bash", SCRIPT, *map(str, args)], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, env=env(d))


def wait_for(path, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        if Path(path).exists() and Path(path).read_text().strip():
            return True
        time.sleep(0.05)
    return False
