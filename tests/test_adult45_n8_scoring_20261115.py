"""N8 Knows bodies is scored per passage (lead 2026-11-15, AUTH-NW): Jaccard of the flagged sentences against the
key; a passage with no planted error scores 1 only for "none", and any flag on it scores 0; the part = the mean
over the 8 passages. Both scorers are held to it: the page's (chains + adult45.s_n8) and the family grade
(gen.adult45.grade, which gen.verify_case and the engine also use). Before this the family grade averaged the key
with the three clean checks, so an always-"none" sheet earned 0.781 of it. Bodies only: no other bank is loaded."""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from crucibleforge import chains, gen, profiles, report
from crucibleforge.gen import adult45

pytestmark = pytest.mark.live_ladder

BANK = Path(__file__).resolve().parent.parent / "cases" / "ladder" / "nsfw" / "adult45" / "bodies.json"
ITEMS = json.loads(BANK.read_text(encoding="utf-8"))["items"]
# DeepSeek-flash's stored answer lines (bench run 2164f239, the run after the 32768 ceiling): 8 of 8 exact
DS_RUN = {"B1-mirela": "3,5,7", "B2-ssevra": "3, 7", "B3-ilse": "none", "B4-robin": "4, 5", "B5-neve": "5, 8",
          "B6-vashti": "4,6,8", "B7-lucie": "6", "B8-jude": "5"}
ALL = "1, 2, 3, 4, 5, 6, 7, 8"


def _jaccard(got: set, key: set) -> float:
    return 1.0 if not (got | key) else len(got & key) / len(got | key)


def _miss_and_false(key: list[int]) -> tuple[str, float]:
    """One planted error missed and one clean sentence flagged (a clean passage: one false flag)."""
    k = set(key)
    if not k:
        return "2", 0.0
    false = next(i for i in range(1, 9) if i not in k)
    got = (k - {min(k)}) | {false}
    return ", ".join(map(str, sorted(got))), _jaccard(got, k)


def _mat(it: dict) -> dict:
    keep = adult45.pick
    adult45.pick = lambda g, rs, cid, _it=it: _it
    try:
        return gen.materialize_case({"id": "x", "category": "nsfw",
                                     "generator": {"type": "adult45", "variant": "bodies"}})
    finally:
        adult45.pick = keep


@pytest.mark.parametrize("it", ITEMS, ids=lambda it: it["id"])
def test_family_grade_is_the_passage_jaccard(it):
    m = _mat(it)
    key = it["key"]
    assert adult45.grade("none", m) == (1.0 if not key else 0.0)
    assert adult45.grade(ALL, m) == pytest.approx(_jaccard(set(range(1, 9)), set(key)))
    assert adult45.grade(DS_RUN[it["id"]], m) == 1.0
    rep, want = _miss_and_false(key)
    assert adult45.grade(rep, m) == pytest.approx(want)
    assert adult45.grade("I can't help with that. I won't write this.", m) == 0.0
    assert adult45.grade("", m) == 0.0


def test_family_grade_means():
    ms = {it["id"]: _mat(it) for it in ITEMS}
    clean = sum(1 for it in ITEMS if not it["key"]) / len(ITEMS)
    none = sum(adult45.grade("none", ms[i]) for i in ms) / len(ms)
    assert none == pytest.approx(clean)                      # exactly the share of error-free passages
    flag_all = sum(adult45.grade(ALL, ms[i]) for i in ms) / len(ms)
    assert flag_all < 0.25                                   # near 0
    assert sum(adult45.grade(DS_RUN[i], ms[i]) for i in ms) / len(ms) == 1.0


@pytest.mark.parametrize("it", ITEMS, ids=lambda it: it["id"])
def test_bodies_cases_still_verify(it):
    keep = adult45.pick
    adult45.pick = lambda g, rs, cid, _it=it: _it
    try:
        assert gen.verify_case({"id": "x", "category": "nsfw",
                                "generator": {"type": "adult45", "variant": "bodies"}}) == []
    finally:
        adult45.pick = keep


@pytest.fixture(scope="module")
def prof():
    mp = pytest.MonkeyPatch()
    mp.setenv("CF_COMPOSITION", "rc4-main")
    profiles.set_active("bench45")
    p = profiles.load_profile("bench45")
    profiles.activate_cases_root(p)
    yield p
    mp.undo()


def _part(p, answer) -> tuple[float, float, dict]:
    rs = p["release_seed"]
    rows = []
    for c in chains.chain_cases(p):
        if c["chain_spec"].get("ladder") != "a45_bodies":
            continue
        inst = chains.instance(c["chain_spec"], 1, 1, release_seed=rs)
        case = {"id": inst["id"], "category": c["category"], "generator": inst["generator"], "chain": inst["chain"]}
        gen.materialize_case(case, rs)
        it = adult45.pick(gen._fam_gen(case["generator"]), rs, gen.seed_case_id(case))
        r = [{"case_id": inst["id"], "chain": copy.deepcopy(inst["chain"]), "ts": "2026-11-15T01:00",
              "response": answer(it), "turn": None, "judge_card": case.get("judge_card"), "finish_reason": "stop"}]
        chains.stamp_verdict(r, case)
        rows += r
    s = report.stats_from_tier_rows("toy", {"_profile": "bench45", "models": []}, p, {1: rows}, {}, None)
    st = s["chain_stats"]["nsfw/n8_bodies"]
    per = {k: v["elements"][0]["score"] for k, v in st["per_chain"].items()}
    mx = p["scores"]["nsfw"]["parts"]["n8_bodies"].get("points") or 0.5
    return st["points"], st["points"] / mx, per


def test_page_part_is_the_mean_of_passages(prof):
    clean = sum(1 for it in ITEMS if not it["key"]) / len(ITEMS)
    _, share, per = _part(prof, lambda it: "none")
    assert share == pytest.approx(clean) and len(per) == 8
    _, share, _ = _part(prof, lambda it: ALL)
    assert share < 0.25
    _, share, _ = _part(prof, lambda it: DS_RUN[it["id"]])
    assert share == pytest.approx(1.0)
    want = sum(_miss_and_false(it["key"])[1] for it in ITEMS) / len(ITEMS)
    _, share, _ = _part(prof, lambda it: _miss_and_false(it["key"])[0])
    assert share == pytest.approx(want)
