"""4.5 packs (PACKS2, 2026-11-14): the hard Predict programs' answers are their real output, the pack set files
name only cases that exist, and a draft pack never opens on rc3-main."""
import json
import pathlib
import sys

import pytest

from crucibleforge import ladder, profiles

ROOT = pathlib.Path(__file__).parents[1]
HARD = ["CB-X1-predict-pyhard", "CB-X2-predict-nodehard", "CB-X3-predict-bashhard"]


def _bank():
    return {c["id"]: c for c in json.loads((ROOT / "cases/codebank.json").read_text(encoding="utf-8"))}


def test_hard_predict_cases_exist_with_one_check_per_snippet():
    bank = _bank()
    for cid in HARD:
        c = bank[cid]
        assert c["family"] == "predict" and c["grader"] == "checks"
        assert len(c["grader_config"]["checks"]) == len(c["snippets"]) == len(c["golds"])


def test_hard_predict_golds_are_the_real_run():
    sys.path.insert(0, str(ROOT / "scripts"))
    import build_codebank as b
    bank = _bank()
    for cid in HARD:
        c = bank[cid]
        for i, sn in enumerate(c["snippets"], 1):
            assert b.run_snippet(sn["lang"], sn["code"]) == c["golds"][f"Q{i}"], (cid, i)


def test_hard_predict_answers_are_not_in_the_prompt():
    bank = _bank()
    for cid in HARD:
        c = bank[cid]
        for q, gold in c["golds"].items():
            assert len(gold) < 12 or gold not in c["prompt"], (cid, q)


def test_predict_pack_is_the_three_hard_programs_only():
    spec = json.loads((ROOT / "cases/ladder/work/c2_predict/set-1.json").read_text(encoding="utf-8"))
    assert spec["cases"] == HARD and spec["points"] == 1.0 and not spec.get("draft")
    assert spec["budget"] + 4096 <= spec["context"]


def test_rpsession_chat_packs_are_drafts_until_the_family_lands():
    for rel in ("chat/h2_state/set-1.json", "chat/h2_ledger/set-1.json", "chat/h6_know/set-2.json"):
        spec = json.loads((ROOT / "cases/ladder" / rel).read_text(encoding="utf-8"))
        assert spec["draft"] is True
        assert all(c["generator"]["type"] == "rpsession" for c in spec["cases"])
        assert all(c["generator"]["card"].startswith("RPS") for c in spec["cases"])


@pytest.mark.live_ladder      # reads the live cases/ladder packs (conftest guard)
def test_rc3_main_discovers_no_draft_pack(monkeypatch):
    monkeypatch.setenv(profiles.COMPOSITION_ENV, "rc3-main")
    prof = profiles.load_profile("bench45")
    sets = ladder.discover(prof, staged=False)
    for key in ("chat/h2_state", "chat/h2_ledger"):
        assert key not in sets
    assert all(s["n"] != 2 for s in sets.get("chat/h6_know", []))
    # not vacuous: the live packs are there
    assert [s["n"] for s in sets["work/c2_predict"]] == [1] and [s["n"] for s in sets["work/c5_tools"]] == [2]


def _answers(golds, swap=None, wrong="WRONG"):
    return "\n".join(f"Q{i}: {wrong if q == swap else g}"
                     for i, (q, g) in enumerate(sorted(golds.items(), key=lambda kv: int(kv[0][1:])), 1))


def test_hard_predict_fixtures_both_ways():
    # QC Q1: the key passes every check; one wrong line fails exactly its own check
    from crucibleforge.graders import grade_checks
    bank = _bank()
    for cid in HARD:
        c = bank[cid]
        cfg = c["grader_config"]
        good = grade_checks(_answers(c["golds"]), cfg)
        assert good["grade"] == "pass" and good["rate"] == 1.0, cid
        for q in c["golds"]:
            bad = grade_checks(_answers(c["golds"], swap=q), cfg)
            assert [r["id"] for r in bad["results"] if not r["pass"]] == [q], (cid, q)


def test_hard_predict_cannot_be_gamed():
    # QC Q4/Q6: listing candidates (key first, a guess last) earns nothing; echoing the code
    # earns nothing; the key with a near-miss (extra space inside, case change) fails; empty = 0
    from crucibleforge.graders import grade_checks
    bank = _bank()
    for cid in HARD:
        c = bank[cid]
        cfg = c["grader_config"]
        listed = _answers(c["golds"]) + "\n" + "\n".join(f"Q{i}: guess" for i in range(1, len(c["golds"]) + 1))
        assert grade_checks(listed, cfg)["rate"] == 0.0, cid
        assert grade_checks(c["prompt"], cfg)["rate"] == 0.0, cid
        assert grade_checks("", cfg)["rate"] == 0.0 and grade_checks("", cfg)["grade"] == "fail"
        for q, g in c["golds"].items():
            if any(ch.isalpha() for ch in g) and g.swapcase() != g:
                near = grade_checks(_answers(c["golds"], swap=q, wrong=g.swapcase()), cfg)
                assert q in [r["id"] for r in near["results"] if not r["pass"]], (cid, q)


def test_tool_use_pack_is_the_three_agent_tasks_without_standard_cases():
    root = ROOT / "cases/ladder/work/c5_tools"
    lag = json.loads((root / "set-2.json").read_text(encoding="utf-8"))
    assert lag["cases"] == ["LAG1-potkeeper-yearend", "LAG2-seedbank-units"] and lag["allow_small"]   # LAG3: timing, ruled out
    assert not lag.get("draft") and lag["points"] == 1.0
    assert not any(c.startswith("TZ") for c in lag["cases"])        # TZ16/19/20 are the standard part now
    # the PROBE draft (set-1) is deleted, not kept closed: a committed pack that cannot pass verify does
    # not stay in cases/ladder (lead 2026-11-15); the tool-use pack is set-2 alone
    assert not (root / "set-1.json").exists() and sorted(f.name for f in root.glob("set-*.json")) == ["set-2.json"]
    bank = json.loads((ROOT / "cases/codeagent_long.json").read_text(encoding="utf-8"))
    ids = {c["id"] for c in (bank["cases"] if isinstance(bank, dict) else bank)}
    assert set(lag["cases"]) <= ids


@pytest.mark.live_ladder
def test_a_set_level_repeats_reaches_every_row_of_the_set(monkeypatch):
    # the tool-use pack runs each of its two agent tasks twice (lead 2026-11-15: 2 items x 2 samples)
    from crucibleforge import config
    monkeypatch.setenv(profiles.COMPOSITION_ENV, "rc3-main")
    prof = profiles.load_profile("bench45")
    sets = ladder.discover(prof)
    spec = next(s for s in sets["work/c5_tools"] if s["n"] == 2)
    assert spec["repeats"] == 2
    cs = ladder.set_cases(prof, "work/c5_tools", 2, sets=sets)
    cfg = {"defaults": {"repeats": {"codeagent_long": 1}}}
    assert [(c["id"], config.repeats_for(cfg, c["category"], False, c)) for c in cs] == [
        ("LAG1-potkeeper-yearend", 2), ("LAG2-seedbank-units", 2)]
    assert config.repeats_for(cfg, cs[0]["category"], True, cs[0]) == 1      # smoke stays one
