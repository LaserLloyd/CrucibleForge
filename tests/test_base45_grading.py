"""A5: the grading-only edits of the Base cases (no prompt, system or turn text moves).

CC4: W2 and J1/J3 unscored (J1/J3 retired with notes), elements at standard 45, <= 2 judge calls.
NC2: every `limit-*` check unscored, elements at standard 45, <= 2 judge calls.
WL2: willingness W is programmatic (no judge W rung).
"""
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def cases():
    from crucibleforge import config
    return {c["id"]: c for c in config.load_cases()}


@pytest.fixture(scope="module")
def prof():
    from crucibleforge import config, profiles
    return profiles.load_profile("bench45", config.load_config(config.EXAMPLE_CONFIG_PATH))


def _checks(c, prefix=None, cid=None):
    return [k for k in c["checks"] if (prefix is None or k["id"].startswith(prefix))
            and (cid is None or k["id"] == cid)]


def test_cc4_w2_is_unscored_and_still_runs(cases):
    w2 = _checks(cases["CC4-second-pitch"], cid="W2-short-on-request")
    assert len(w2) == 1 and w2[0].get("scored") is False
    # the other format checks keep scoring
    assert all(k.get("scored", True) for k in cases["CC4-second-pitch"]["checks"]
               if k["id"] != "W2-short-on-request")


def test_nc2_limit_checks_are_unscored(cases):
    lim = _checks(cases["NC2-after-hours"], prefix="limit-")
    assert lim, "NC2 has no limit-* check"
    assert all(k.get("scored") is False for k in lim)
    assert all(k.get("scored", True) for k in cases["NC2-after-hours"]["checks"]
               if not k["id"].startswith("limit-"))


def test_wl2_willingness_is_programmatic(cases):
    c = cases["WL2-lighthouse"]
    assert c.get("willing") == "must_write"
    assert not c.get("judge_willingness")
    from crucibleforge import judge
    assert judge.row_willingness({"case_id": "WL2-lighthouse", "rubric": c["rubric"], "judge_willingness": False,
                                  "willing": "must_write"}) is False


def test_cc4_j1_j3_are_retired_with_notes(cases):
    els = {e["id"]: e for e in cases["CC4-second-pitch"]["judge_elements"]}
    for eid in ("J1-gertie-promise", "J3-photo-promise"):
        e = els[eid]
        assert e["status"] == "retire" and e["scored"] is False and e["note"].strip(), eid
        assert "weight" not in e, eid
    live = [e for e in els.values() if e.get("status") != "retire"]
    assert {e["id"] for e in live} >= {"C1-rope-advice-misremembered", "M3-belay-from-above",
                                       "C2-nain-in-porthmadog", "P1-priya-pushback",
                                       "I2-colour-blind-ropes", "J2a-jet-lag"}


@pytest.mark.parametrize("cid", ["CC4-second-pitch", "NC2-after-hours"])
def test_standard_45_and_lint_clean(cases, cid):
    from crucibleforge import element_lint
    c = cases[cid]
    assert c.get("element_standard") == 45
    errs = [f for f in element_lint.lint_case(c, strict=True) if f["severity"] == "error"]
    assert not errs, errs
    assert len([e for e in c["judge_elements"] if e.get("status") != "retire"]) <= 8
    for e in c["judge_elements"]:
        if e.get("status") != "retire":
            assert e["class"] in ("fact", "craft") and e["evidence_type"] in ("single", "multi", "negative")


@pytest.mark.parametrize("cid", ["CC4-second-pitch", "NC2-after-hours"])
def test_at_most_two_judge_calls(cases, prof, cid):
    from crucibleforge import budget
    assert budget.tree_judge_calls(cases[cid], prof) <= 2.0
    assert budget._engine_expected_calls(cases[cid], prof) <= 2.0


def test_cc4_gold_quotes_sit_in_the_gold_reference(cases):
    from crucibleforge import element_lint
    gold = (ROOT / "cases/rp-deep/CC4-second-pitch/fixtures/gold.md").read_text(encoding="utf-8")
    norm = element_lint._norm
    for e in cases["CC4-second-pitch"]["judge_elements"]:
        if e.get("status") != "retire":
            assert norm(e["gold_quote"]).strip(" \"'.,!?") in norm(gold), e["id"]


@pytest.mark.parametrize("cid", ["CC4-second-pitch", "NC2-after-hours", "WL2-lighthouse"])
def test_prompt_system_and_turns_equal_the_frozen_4_1_text(cases, cid):
    """Belt and braces next to the generation-view test: the literal text fields."""
    import json
    arch = {c["id"]: c for p in ("rpdeep", "nsfw") for c in json.loads(
        (ROOT / "archive/4.1.0/cases" / f"{p}.json").read_text(encoding="utf-8"))}
    for k in ("prompt", "system", "turns", "history", "max_tokens", "temperature", "top_p"):
        assert cases[cid].get(k) == arch[cid].get(k), (cid, k)
