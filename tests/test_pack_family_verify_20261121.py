"""Pack-family NSFW+ sets are verified by the pack's own rules (owner ruling 2026-11-21, option c).

One rule set for a case of the nsfwpack45 family, in ``verify_cases``: the bank item's ``adult45.item_problems``, the
reference reply graded against the case's own checks must score 1.0 (stored cards: ``nsfw_rp/refs/<id>.json`` good
replies; other variants: the bank's ``ref_reply``), and the item carries both attestations (``attest_check``). The
4.1 lint (check groups, rubric tags) does not apply to them. The six pack sets are wired: none is on the staged list.
"""
from __future__ import annotations

import json

import pytest

from crucibleforge import gen as _g, ladder, verify_cases as V

import _staged45 as S

pytestmark = [pytest.mark.shipping_default, pytest.mark.live_ladder]

PACK_SETS = ("nsfw/n1_scene/set-1.json", "nsfw/n6a_craft/set-1.json", "nsfw/n7_taboo/set-1.json",
             "nsfw/n8_bodies/set-1.json", "nsfw/u1_will/set-1.json", "nsfw/u3_know/set-1.json")


def _cases(rel):
    f = ladder.LADDER_DIR / rel
    return [_g.materialize_case(dict(c)) for c in ladder.expand_generated(json.loads(f.read_text(encoding="utf-8")), f)]


def test_the_six_pack_sets_are_wired():
    for rel in PACK_SETS:
        spec = json.loads((ladder.LADDER_DIR / rel).read_text(encoding="utf-8"))
        assert not ladder.is_staged(spec), rel
        assert rel not in S.STAGED_SETS, rel


@pytest.mark.parametrize("rel", PACK_SETS)
def test_every_pack_case_verifies_clean(rel):
    bad = {c["id"]: V._check(c) for c in _cases(rel)}
    assert {k: v for k, v in bad.items() if v} == {}


def _stored():
    return next(c for c in _cases("nsfw/n1_scene/set-1.json") if (c.get("meta") or {}).get("variant") == "stored")


def test_a_pack_case_is_recognised():
    c = _stored()
    assert V.is_pack_family(c)
    assert not V.is_pack_family({"id": "X", "meta": {"family": "adult45"}})


def test_a_wrong_reference_fails(monkeypatch):
    c = _stored()
    monkeypatch.setattr(V, "_pack_reference", lambda case: "nothing")
    assert any("reference" in e for e in V._check(c))


def test_a_missing_attestation_fails(monkeypatch):
    c = _stored()
    monkeypatch.setattr(V, "_pack_item", lambda case: {"id": c["meta"]["card"]})
    errs = V._check(c)
    assert any("attest" in e for e in errs)


def test_a_minor_in_the_cast_fails(monkeypatch):
    c = next(c for c in _cases("nsfw/n7_taboo/set-1.json"))
    it = dict(V._pack_item(c))
    it["cast"] = {"model": [{"name": "A", "age": 16}]}
    monkeypatch.setattr(V, "_pack_item", lambda case: it)
    assert any("adult age" in e for e in V._check(c))


def test_the_41_lint_does_not_apply_to_pack_cases():
    c = _stored()
    errs = V._check(c)
    assert not any("unknown group" in e or "non-erotic rubric" in e for e in errs)
