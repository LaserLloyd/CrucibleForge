"""Guard: tracked files must not mention the private project name."""
import re
import subprocess
from pathlib import Path

import pytest

_PAT = re.compile("cl" + r"aw[-_ ]?forge", re.I)


def test_no_private_project_names_in_tracked_files():
    here = Path(__file__).resolve().parent
    try:
        root = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=here, check=True,
                              capture_output=True, text=True).stdout.strip()
        names = subprocess.run(["git", "ls-files", "-z"], cwd=root, check=True,
                               capture_output=True).stdout.split(b"\0")
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("not a git checkout")
    hits = []
    for raw in filter(None, names):
        path = Path(root) / raw.decode("utf-8", "replace")
        try:
            text = path.read_bytes().decode("utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for n, line in enumerate(text.splitlines(), 1):
            if _PAT.search(line):
                hits.append(f"{path.relative_to(root)}:{n}")
    assert not hits, "private project name in tracked files: " + ", ".join(hits)
