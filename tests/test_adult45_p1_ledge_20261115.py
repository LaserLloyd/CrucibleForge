"""adult45 N4 probe P1 ``fact-ledge`` (FIXNOW 16, lead 2026-11-15): the history moves the scene to "the heated stone
ledge in the alcove"; ref and qwenheretic stayed there but wrote "the hot stone", never "ledge", and failed the recall.
The pool's stone is "cool" and its floor "wet tiles", so the wider key does not admit the pool."""
from __future__ import annotations

import pytest

from crucibleforge import session_checks as sc
from crucibleforge.gen import adult45

pytestmark = pytest.mark.live_ladder  # reads the live probes bank (conftest guard)


def _ledge():
    case = adult45.materialize({"type": "adult45", "variant": "probe", "level": 1, "pin": "P1-ssevra"},
                               release_seed="t", case_id="t")
    return {c["id"]: c for c in case["checks"]}


def _ok(chk, t1, t2):
    fn = sc.check_require_regex if chk["type"] == "require_regex" else sc.check_forbid_regex
    return fn([(1, t1), (2, t2)], chk)[0]


@pytest.mark.parametrize("t1,t2", [
    # the stored replies' own words (ref, qwenheretic)
    ("She coils, settling around your hips on the hot stone.", "Her body spread across the hot stone."),
    ("You are on all fours on the hot stone.", "Steam drawing in from the hot stone."),
    ("She keeps you on the ledge.", "Still there."),
    ("Back in the alcove, she settles.", "Quiet."),
])
def test_stayed_on_the_ledge_passes(t1, t2):
    assert _ok(_ledge()["fact-ledge"], t1, t2)


@pytest.mark.parametrize("t1,t2", [
    ("She carries you down onto the wet tiles.", "The cool stone of the pool floor."),
    ("On all fours on the stone.", "She moves."),
])
def test_no_ledge_word_fails(t1, t2):
    assert not _ok(_ledge()["fact-ledge"], t1, t2)


def test_ledge_not_unchanged():
    c = _ledge()["fact-ledge-not"]
    assert c["pattern"] == r"\bpool'?s? edge\b|\bedge of the pool\b|\bpoolside\b|\bby the pool now\b"
    assert not _ok(c, "Back at the pool edge now.", "x")
