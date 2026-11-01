import os
import time

from nightwatch.check import check_target, newest


def test_newest_and_fresh(tmp_path):
    for i, name in enumerate(["a.tar.zst", "b.tar.zst", "c.txt"]):
        p = tmp_path / name
        p.write_text("x")
        os.utime(p, (time.time() - 3600 * (3 - i), time.time() - 3600 * (3 - i)))
    path, _ = newest(tmp_path, "*.tar.zst")
    assert path.name == "b.tar.zst"
    r = check_target({"name": "t", "dir": str(tmp_path), "pattern": "*.tar.zst"}, 26)
    assert r["status"] == "FRESH"
