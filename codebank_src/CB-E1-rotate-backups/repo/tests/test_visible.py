import subprocess
from pathlib import Path


def run(*args):
    return subprocess.run(["bash", "rotate.sh", *map(str, args)], capture_output=True, text=True)


def test_keeps_newest_three(tmp_path):
    for d in ("20261021", "20261022", "20261023", "20261024"):
        (tmp_path / f"db-{d}-020000.tar.zst").write_text("x")
    p = run(tmp_path)
    assert p.returncode == 0
    assert p.stdout == "deleted db-20261021-020000.tar.zst\n"
