"""Spec 1.4 / review N7, N8: S8's texts-* items are Pace items; a segment is complete only with its distinct cards
(each with its seeds), not by unit count alone."""
from __future__ import annotations

import pytest

from crucibleforge import gen
from crucibleforge import nsfwplus45 as N
from crucibleforge.gen import adult45

pytestmark = pytest.mark.live_ladder


@pytest.fixture(autouse=True, scope="module")
def _discover():
    gen.discover()


def test_s8_texts_items_are_pace_items():
    c = next(x for x in adult45.items("session") if x["id"].startswith("S8"))
    pace = N.pace_items(c["id"], c["ref_replies"])
    texts = {k for k in N.check_table(c["id"], c["ref_replies"]) if k.startswith("texts-")}
    assert texts and texts <= set(pace)


def _range(units_cards):
    return [{"id": f"range-{i}", "segment": "range", "card": cid, "replies": ["x"]} for i, cid in enumerate(units_cards)]


def test_twenty_cards_with_one_twice_is_short():
    ids = [it["id"] for it in adult45.items("taboo")]
    assert len(ids) == 21
    assert N._short(_range(ids), "range") is None
    short = N._short(_range(ids[:20] + ids[:1]), "range")
    assert short and "20 of 21 cards" in short


def test_a_card_with_one_seed_where_two_are_asked_is_short():
    dw = [it["id"] for it in adult45.items("dwell")]
    units = [{"id": f"d{i}", "segment": "erotica_dwell", "card": c, "replies": ["x"]} for i, c in enumerate(dw * 2)]
    assert N._short(units, "erotica_dwell") is None
    lop = units[:-1] + [{**units[0], "id": "dup"}]                 # 8 units, one card three times, one once
    assert N._short(lop, "erotica_dwell")
