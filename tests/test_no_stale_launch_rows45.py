"""Guard rule (spec 4.1), one canonical fixture: every family's ``grader_stamp`` equals its value on the frozen
generation tip 8f946f9 (``fixtures/grader_stamps_8f946f9.json``, computed on that tip: every generator family and
every static grader), so no stored row goes stale; and the shakedown result folders read 0 stale rows.

The shakedown check reads copies of the folders named by ``CF_SHAKEDOWN_COPIES`` (a directory holding one result
folder per run); it is skipped when the variable is unset."""
import json
import os
from pathlib import Path

import pytest

from crucibleforge import gen
from crucibleforge import grader_stamp as g

GOLD = json.loads((Path(__file__).parent / "fixtures" / "grader_stamps_8f946f9.json").read_text(encoding="utf-8"))


def test_every_family_stamp_is_unchanged():
    gen.discover()
    g.clear()
    bad = {f: (want, g.stamp(f)) for f, want in GOLD.items() if g.stamp(f) != want}
    assert not bad, f"a grader closure changed (rows would go stale): {sorted(bad)}"


def test_the_fixture_covers_every_family_and_static_grader():
    gen.discover()
    assert set(gen.families()) | {f"static:{k}" for k in g.STATIC_MODULES} <= set(GOLD)
    assert {"implied", "adult45", "unc_know", "rpsession", "static:codebank", "static:tooluse"} <= set(GOLD)


def test_one_canonical_stamp_fixture():
    fx = sorted(p.name for p in (Path(__file__).parent / "fixtures").glob("grader_stamps_*.json"))
    assert fx == ["grader_stamps_8f946f9.json"], fx


@pytest.mark.skipif(not os.environ.get("CF_SHAKEDOWN_COPIES"), reason="CF_SHAKEDOWN_COPIES not set")
def test_shakedown_copies_read_zero_stale_rows():
    gen.discover()
    g.clear()
    folders = sorted(p for p in Path(os.environ["CF_SHAKEDOWN_COPIES"]).iterdir() if list(p.glob("transcripts_*.jsonl")))
    assert folders
    for f in folders:
        for t in f.glob("transcripts_*.jsonl"):
            rows = [json.loads(x) for x in t.read_text(encoding="utf-8").splitlines() if x.strip()]
            stale = g.stale_rows(rows)
            assert not stale, f"{f.name}: {len(stale)} stale row(s), e.g. {stale[0][1]}"
