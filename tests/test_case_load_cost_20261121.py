"""Case-load cost (stamp-2 item D): the staged PATCHTOUGH set renders for ~26 s in every process that loads
cases, so it lives outside discovery (cases/staged/...). A fresh `load_cases()` stays under 25 s and no staged
generated set in cases/ladder costs more than 8 s to render (generous bounds; the structural test below is exact)."""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


# The two wall-clock tests carry a generous bound (the base cost was ~32 s alone; ~5 s alone today) so parallel load
# cannot flake them; run them alone for the tight reading:  pytest -m timing -n 0
import pytest  # noqa: E402


@pytest.mark.timing
def test_fresh_process_load_cases_is_fast():
    t = time.time()
    r = subprocess.run([sys.executable, "-c", "from crucibleforge import config; config.load_cases()"],
                       cwd=ROOT, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr[-500:]
    assert time.time() - t < 25, "load_cases() fresh process took >= 25 s"


@pytest.mark.timing
def test_no_staged_generated_set_in_ladder_is_slow_to_render():
    from crucibleforge import config
    from crucibleforge.ladder import inline_cases
    per_set: dict[str, float] = {}
    for c in inline_cases():
        if c.get("ladder_staged") and c.get("generator"):
            t = time.time()
            config._materialize_generated(dict(c))
            per_set[c["ladder_pack"]] = per_set.get(c["ladder_pack"], 0.0) + time.time() - t
    slow = {k: round(v, 1) for k, v in per_set.items() if v > 8}
    assert not slow, f"staged generated sets too slow to render on every load: {slow}"


def test_patchtough_set_sits_outside_discovery():
    assert (ROOT / "cases/staged/work/c3_fix/set-2.json").is_file()
    assert not (ROOT / "cases/ladder/work/c3_fix/set-2.json").exists()
