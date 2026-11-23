"""Stamp-2 item C: a Chat pack part (``pack_segment``, max 0; packs45 scores it) adds no weight to the old-tree
scorecard. Before this, each ``pk_*`` part with ``cases: []`` counted all 8 rpdeep cases: chat.rpdeep 32 -> 56."""
from __future__ import annotations

import copy

import pytest

from crucibleforge import profiles, report


@pytest.fixture
def prof(monkeypatch):
    monkeypatch.setenv("CF_COMPOSITION", "rc4-main")
    profiles.set_active("bench45")
    p = profiles.load_profile("bench45")
    profiles.activate_cases_root(p)
    return p


def _packs(p):
    return [(sk, pk) for sk, s in p["scores"].items() for pk, part in (s.get("parts") or {}).items()
            if part.get("pack_segment")]


def test_chat_rpdeep_weight_is_32(prof):
    cfg = {"_profile": "bench45", "models": []}
    assert report.scoring_config(cfg)["chat"]["rpdeep"] == 32


def test_every_pack_segment_part_has_empty_weights(prof):
    packs = _packs(prof)
    assert packs, "no pack_segment part in the profile"
    cfg = {"_profile": "bench45", "models": []}
    _, part_w = report._declared_weights(prof, None, cfg)
    for sk, pk in packs:
        assert part_w[sk][pk] == {}, (sk, pk, part_w[sk][pk])


def test_pack_parts_cannot_move_an_old_tree_scorecard_value(prof):
    cfg = {"_profile": "bench45", "models": []}
    bare = copy.deepcopy(prof)
    for sk, pk in _packs(prof):
        del bare["scores"][sk]["parts"][pk]
    a, _ = report._declared_weights(prof, None, cfg)
    b, _ = report._declared_weights(bare, None, cfg)
    assert a == b
