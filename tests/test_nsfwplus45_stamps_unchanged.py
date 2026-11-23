"""Guard rule (4.5 realignment W5): the W5 modules are new files only. Every family's grader stamp equals the value
it had at 8f946f9, so no stored row is made stale by this lane (the U3 key fixes are report-time)."""
from __future__ import annotations

import json
from pathlib import Path

from crucibleforge import gen
from crucibleforge import grader_stamp as G

FIX = Path(__file__).resolve().parent / "fixtures" / "grader_stamps_8f946f9.json"


def test_every_grader_stamp_equals_its_8f946f9_value():
    gen.discover()
    G.clear()
    want = json.loads(FIX.read_text(encoding="utf-8"))
    got = {k: G.stamp(k) for k in want}
    assert got == want
    assert {"adult45", "unc_know", "rpsession"} <= set(want)
