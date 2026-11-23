"""The verifiers' staged list (tests/_staged45.py) is exactly the staged sets in the tree: nothing is left out
silently, and a wired set can never be on it."""
from __future__ import annotations

import json

from crucibleforge import ladder

import _staged45 as S


def _sets():
    return {S.rel(f): json.loads(f.read_text(encoding="utf-8")) for f in sorted(S.LADDER.rglob("set-*.json"))}


def test_every_staged_set_is_on_the_list():
    staged = {k for k, spec in _sets().items() if ladder.is_staged(spec)}
    assert staged - S.STAGED_SETS == set(), "staged set missing from tests/_staged45.py"


def test_no_wired_set_is_on_the_list_and_no_entry_is_stale():
    sets = _sets()
    assert S.STAGED_SETS - set(sets) == set(), "listed set no longer exists"
    wired = {k for k in S.STAGED_SETS if not ladder.is_staged(sets[k])}
    assert wired == set(), "a wired set is on the staged list: take it off so the verifiers check it"


def test_listed_is_by_exact_path():
    some = next(iter(sorted(S.STAGED_SETS)))
    assert S.listed(S.LADDER / some)
    assert not S.listed(S.LADDER / some.replace("set-", "set-9"))
    assert not S.listed("/var/tmp/elsewhere/cases/ladder/" + some)
