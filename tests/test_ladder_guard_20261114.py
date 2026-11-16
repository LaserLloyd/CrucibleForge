"""The suite's ladder guard (2026-11-14): tests see an empty ladder unless marked
``live_ladder``, and gen.set_release_seed refuses a test seed while the live
ladder is in reach -- only inside a running pytest test."""
from __future__ import annotations

from pathlib import Path

import pytest

from crucibleforge import gen, ladder

LIVE = (Path(ladder.ROOT_DIR) / "cases" / "ladder").resolve()


def test_unmarked_tests_see_an_empty_ladder():
    assert Path(ladder.LADDER_DIR).resolve() != LIVE
    assert list(ladder._set_files()) == []
    gen.set_release_seed("T9")            # no live ladder in reach: allowed
    gen.set_release_seed(None)


@pytest.mark.live_ladder
def test_marked_tests_see_the_live_ladder():
    assert Path(ladder.LADDER_DIR).resolve() == LIVE
    assert any(True for _ in ladder._set_files())


@pytest.mark.live_ladder
def test_a_test_seed_on_the_live_ladder_fails_at_once():
    with pytest.raises(RuntimeError, match="live"):
        gen.set_release_seed("T9")
    assert gen._RELEASE_SEED[0] is None   # the refused seed is not left active


@pytest.mark.live_ladder
def test_the_production_seed_is_allowed_on_the_live_ladder():
    try:
        gen.set_release_seed(gen.DEFAULT_RELEASE_SEED)
        assert gen.release_seed() == gen.DEFAULT_RELEASE_SEED
    finally:
        gen.set_release_seed(None)


@pytest.mark.live_ladder
def test_the_check_is_inert_outside_a_running_test(monkeypatch):
    monkeypatch.delenv("PYTEST_CURRENT_TEST")
    try:
        gen.set_release_seed("T9")        # what a CLI process does: no check
        assert gen._RELEASE_SEED[0] == "T9"
    finally:
        gen.set_release_seed(None)
