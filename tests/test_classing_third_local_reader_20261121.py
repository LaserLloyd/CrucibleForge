"""Stamp-2 item F, re-ruled by lead ruling R1 (2026-11-21): the local rig readers of addendum d are no longer scoped to
DeepSeek-family rows as a third reader. One mechanical rule for every graded family: the profile order (hosted, then
the admitted locals in addendum d order) minus removed readers, minus the graded family, minus never_family; the pair
is the first two of distinct families, the third the next of a family distinct from both. A local reader stays inert
until a stored C1 pass exists for its exact model id. (Batch G tests: tests/test_stamp2g_c6_20261121.py.)"""
from __future__ import annotations

import pytest

from crucibleforge import classing45 as C
from crucibleforge import profiles

pytestmark = pytest.mark.shipping_default
#: Part 1 of the one build pass (owner 2026-11-21, INTENT-45 "No second reader, no hosted grader, no hosted checker"):
#: these tests pinned the retired multi-reader pick (hosted fallback readers, a third reader, C6 removal, the
#: admitted-pair report). One local judge now classes every unit (tests/test_judge45_part1_20261121.py).
RETIRED_PART1 = "retired by Part 1 (one local judge; the multi-reader pick is out of the scoring path)"

SHA = C.load_prompt({})[1]
NAMED = C.named_readers()


def _c1(*roles):
    out = []
    for r in roles:
        d = NAMED[r]
        out.append({"model": d["model_id"], "family": d.get("family"), "prompt_sha": SHA, "status": "pass"})
    return out


BASE = ("deepseek-v4-flash", "RH-kimi", "RH-gpt")


def _pick(family, *roles):
    prof = profiles.load_profile("bench45")
    return [r.name for r in C.pick_readers(family, prof, named=NAMED, c1=_c1(*roles), prompt_sha=SHA)]


@pytest.mark.skip(reason=RETIRED_PART1)
def test_profile_lists_the_locals_after_the_hosted_readers():
    rd = C.reader_roles(profiles.load_profile("bench45"))
    assert rd["hosted_fallback"] == ["deepseek-v4-flash", "RH-kimi", "RH-gpt"]
    assert rd["local"][:2] == ["RL-qwen122", "RL-qwen27"]


@pytest.mark.skip(reason=RETIRED_PART1)
def test_deepseek_row_gets_the_local_reader_third():
    assert _pick("deepseek-flash", *BASE[1:], "RL-qwen122") == ["RH-kimi", "RH-gpt", "RL-qwen122"]
    assert _pick("deepseek-flash", *BASE[1:], "RL-qwen27") == ["RH-kimi", "RH-gpt", "RL-qwen27"]
    # both admitted: same family, so only the first (qwen122) is picked
    assert _pick("deepseek-flash", *BASE[1:], "RL-qwen122", "RL-qwen27") == ["RH-kimi", "RH-gpt", "RL-qwen122"]


@pytest.mark.skip(reason=RETIRED_PART1)
def test_no_local_pass_keeps_todays_pick():
    assert _pick("deepseek-flash", *BASE[1:]) == ["RH-kimi", "RH-gpt"]


@pytest.mark.skip(reason=RETIRED_PART1)
def test_three_hosted_readers_fill_the_set_before_any_local():
    for fam in ("minimax", "moonshot"):
        assert _pick(fam, *BASE, "RL-qwen122", "RL-qwen27") == [r for r in BASE if not
                                                                 (fam == "moonshot" and r == "RH-kimi")] + (
            ["RL-qwen122"] if fam == "moonshot" else [])


@pytest.mark.skip(reason=RETIRED_PART1)
def test_a_local_of_the_graded_family_is_never_picked():
    assert "RL-qwen122" not in _pick("qwen", "deepseek-v4-flash", "RH-kimi", "RL-qwen122")


def test_candidates_list_every_local_for_every_family():
    prof = profiles.load_profile("bench45")
    names = lambda fam: [s.name for s in C.candidates(prof, NAMED, graded_family=fam)]
    for fam in ("deepseek-flash", "openai", "moonshot", None):
        assert "RL-qwen122" in names(fam) and "RL-darker" in names(fam)
