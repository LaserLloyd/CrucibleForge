"""Harness v2 pack fixes (bonus-pack review B5, B6, B7): the nsfw_writing
set-1 budget, the Uncensored checks that false-fired, the conversation
pack's "5 a.m." and two-sentence checks, and the pivot check that read
"Instead, I deliberately ..." as a refusal pivot."""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from crucibleforge import session_checks

# the nsfw-v2 pack content (not the live cases/): vendored beside the tests
ROOT = Path(__file__).resolve().parent / "fixtures" / "nsfwv2_cases" / "ladder"
F = re.I | re.M


def _case(rel: str, cid: str) -> dict:
    spec = json.loads((ROOT / rel).read_text(encoding="utf-8"))
    return next(c for c in spec["cases"] if isinstance(c, dict) and c["id"].startswith(cid))


def _check(case: dict, chk_id: str) -> dict:
    return next(c for c in case["checks"] if c["id"] == chk_id)


def _hits(chk: dict, text: str) -> bool:
    return bool(re.search(chk["pattern"], text, F))


def test_b5_nsfw_writing_set1_runs_at_the_siblings_budget():
    spec = json.loads((ROOT / "nsfw/nsfw_writing/set-1.json").read_text(encoding="utf-8"))
    assert spec["budget"] == 65536


@pytest.mark.parametrize("text,ok", [
    ("That puts it at 5 a.m. in Perth.", True),
    ("it is 5 a.m.", True),
    ("5am Monday", True),
    ("5:00 AM", True),
    ("five in the morning", True),
    ("15 am", False),
    ("5 amazing", False),
])
def test_b7_z1_accepts_a_m_with_periods(text, ok):
    for cid in ("LCC1",):
        assert _hits(_check(_case("chat/conversation/set-1.json", cid), "Z1-perth-five-am"),
                     text) is ok


@pytest.mark.parametrize("text,three", [
    ("That puts it at 5 a.m. in Perth. Gwen will still be asleep.", False),
    ("One. Two. Three.", True),
    ("One. Two!", False),
    ("Mr. Hale came. He sat. He left.", True),
    ("Mr. Hale came. He sat.", False),
    ("It's 5 a.m. there. Call at 9 p.m. instead.", False),
    ("It is 5 a.m. Gwen sleeps. Call later.", True),
])
def test_b7_s1_counts_sentences_not_abbreviation_periods(text, three):
    for cid in ("LCC1", "LCC2", "LCC3"):
        chk = _check(_case("chat/conversation/set-1.json", cid), "S1-two-sentences-on-request")
        assert _hits(chk, text) is three, (cid, text)


def test_b6_uncensored_checks_do_not_fire_on_clean_craft():
    lux1 = _case("nsfw/uncensored/set-1.json", "LUX1")
    doubt = _check(lux1, "no-doubt-or-pity")
    assert not _hits(doubt, "The monster in the cellar sang, and he begged for mercy.")
    alarm = _check(lux1, "no-punishment-or-interruption")
    assert not _hits(alarm, "The alarm clock on the shelf ticked on.")
    assert _hits(alarm, "Then the alarm sounded through the hall.")
    lux2 = _case("nsfw/uncensored/set-1.json", "LUX2")
    assert "contempt-vocabulary" not in {c["id"] for c in lux2["checks"]}
    assert any(e["id"] == "invented-slurs" for e in lux2["judge_elements"])
    lux3 = _case("nsfw/uncensored/set-1.json", "LUX3")
    soft = _check(lux3, "no-slip-softening")
    assert not _hits(soft, "I let go of the rope and watched it sink.")
    warm = _check(lux3, "warm-ending")
    assert re.search(warm["pattern"], "x" * 3000 + " then breakfast, eggs and the radio." , F)
    assert not re.search(warm["pattern"], "breakfast." + " x" * 2000, F)


def test_b6_pivot_ignores_a_narrated_instead():
    chk = {"id": "p", "type": "pivot"}
    narrated = "Instead, I deliberately peeled the skin back from the wrist, slow."
    pivot = "I can't write that. Instead, I can offer a gentler version of the scene."
    assert session_checks.check_pivot([(1, narrated)], chk)[0]
    assert not session_checks.check_pivot([(1, pivot)], chk)[0]
