"""S3 (fix round 2026-11-18, REVIEW-HOLISTIC finding 3): a model that wrote a rung met that rung as its OWN other
writer seed's session, so its two seeds were compared with each other and their placements mirrored (the reference,
DeepSeek flash and Grok were scored against themselves). Every session is now compared with the frozen rung text; the
other-seed substitute is used only for the one unit whose replies ARE the rung (identical sha), where comparing a
text with itself would measure nothing. A stored placement made against a substitute for any other unit is stale."""
from __future__ import annotations

import copy

from crucibleforge import rpladder as RL

from test_rpladder_20261115 import LengthJudge, _all_usable, _cards, _unit


def _place(cards, units, other):
    return RL.place_units(units, cards, LengthJudge(), RL.VerdictCache(None), judge_model="j",
                          check=_all_usable(cards), own_other=lambda u: other)


def test_a_rung_model_whose_seeds_both_beat_r3_is_placed_above_its_rung():
    cards = _cards()
    me = _unit(cards, "C1", 45, model="rung-model-2", seed=1)
    other = _unit(cards, "C1", 45, model="rung-model-2", seed=2)
    other["replies"] = [r + "y" for r in other["replies"]]
    p = _place(cards, [me], other)[me["unit"]]
    assert p["complete"] and p["position"] > 1.5          # R2 sits at 1.5; mirrored it read 1.5 ("level")
    frozen = {str(k): cards["C1"]["candidates"]["standard"]["rungs"][str(k)]["sha"] for k in RL.RUNGS}
    assert all(pr["anchor_sha"] == frozen[str(pr["rung"])] for pr in p["pairs"])


def test_a_rung_model_never_waits_for_its_other_seed():
    cards = _cards()
    me = _unit(cards, "C1", 25, model="rung-model-2", seed=1)
    p = _place(cards, [me], None)[me["unit"]]
    assert p["complete"] is True


def test_the_unit_that_is_the_rung_meets_its_other_seed_and_waits_without_it():
    cards = _cards()
    lad = cards["C1"]["candidates"]["standard"]
    me = _unit(cards, "C1", 25, model="rung-model-2", seed=1)
    me["replies"] = list(lad["rungs"]["2"]["replies"])            # identical sha: this session IS rung 2
    other = _unit(cards, "C1", 25, model="rung-model-2", seed=2)
    p = _place(cards, [me], other)[me["unit"]]
    r2 = [pr for pr in p["pairs"] if pr["rung"] == 2]
    assert r2 and all(pr["anchor_sha"] == RL.anchor_sha(other["replies"]) for pr in r2)
    assert _place(cards, [me], None)[me["unit"]]["complete"] is False


def _rec(cards, unit, other):
    return _place(cards, [unit], other)[unit["unit"]]


def test_a_stored_placement_against_a_substitute_is_stale_unless_the_unit_is_the_rung():
    cards = _cards()
    lad = cards["C1"]["candidates"]["standard"]
    me = _unit(cards, "C1", 25, model="rung-model-2", seed=1)
    other = _unit(cards, "C1", 25, model="rung-model-2", seed=2)
    good = _rec(cards, me, other)
    assert RL.stale_reason(good, cards, None, "j", unit_sha=RL.anchor_sha(me["replies"])) is None
    # the old rule's record: rung 2 met as the other seed
    old = copy.deepcopy(good)
    for pr in old["pairs"]:
        if pr["rung"] == 2:
            pr["anchor_sha"] = RL.anchor_sha(other["replies"])
    assert RL.stale_reason(old, cards, None, "j", unit_sha=RL.anchor_sha(me["replies"])) == "substitute"
    # ... but not for the unit whose replies ARE rung 2
    assert RL.stale_reason(old, cards, None, "j", unit_sha=lad["rungs"]["2"]["sha"]) is None
    # without the unit's sha (a caller that cannot see the row) the old answer stands
    assert RL.stale_reason(old, cards, None, "j") is None
