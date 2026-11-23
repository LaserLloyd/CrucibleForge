"""The bug-fix checker lives outside every grading family's stamp closure: adding it moves no stored row's
``grader_stamp``. The fixture holds every family's stamp computed on the frozen generation tip 8f946f9."""
from __future__ import annotations

import json
from pathlib import Path

from crucibleforge import fixcheck, fixspecs  # noqa: F401  (imported first on purpose)
from crucibleforge import gen
from crucibleforge import grader_stamp as G

FIXTURE = Path(__file__).with_name("fixtures") / "grader_stamps_8f946f9.json"


def test_every_stamp_is_unchanged_against_the_generation_tip():
    gen.discover()
    G.clear()
    want = json.loads(FIXTURE.read_text(encoding="utf-8"))
    # families added after the tip (staged GAP packs, 2026-11-20) are not in the fixture; every family that WAS in
    # it must keep its stamp
    assert set(gen.families()) - set(want) <= {"patchgap", "toolgap", "everygap", "patchtough"}
    now = {fam: G.stamp(fam) for fam in want}
    assert now == want


def test_no_family_reaches_the_new_modules():
    gen.discover()
    for fam in gen.families():
        names = {m.__name__ for m in G._helper_modules(gen.get(fam))}
        assert not any(n.endswith((".fixcheck", ".fixspecs")) for n in names), fam
    for mods in G.STATIC_MODULES.values():
        assert not {"fixcheck.py", "fixspecs.py"} & set(mods)
