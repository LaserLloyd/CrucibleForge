"""CQ1-CQ3 as the second pack of the Writes part (packs lane, 2026-11-20), and the kill test that made it safe:
adding a pack set file changes no scorer stamp, no grader stamp of an existing case, no suite hash and no standard
set of the release composition. (The same was shown on rendered boards of four stored folders: Code, Chat, Overall
and report.json standard points identical; only bootstrap margins moved, see PACKS-LANE-45.md.)"""
from __future__ import annotations

import copy
import json
import shutil
from pathlib import Path

import pytest

from crucibleforge import ladder, profiles, segments45, version

pytestmark = pytest.mark.live_ladder
ROOT = Path(__file__).resolve().parent.parent
CQ_IDS = ["CQ1-format-spec-from-scratch", "CQ2-regex-engine-from-scratch", "CQ3-spreadsheet-engine"]
SET = ROOT / "cases/ladder/work/c1_implied/set-2.json"


@pytest.fixture
def standard(monkeypatch):
    monkeypatch.setenv(profiles.COMPOSITION_ENV, "standard")
    return profiles.load_profile("bench45")


def test_cq_is_discovered_under_writes_for_the_release_composition(standard):
    found = ladder.discover(standard, staged=True)
    seq = found["work/c1_implied"]
    assert [s["n"] for s in seq] == [1, 2]
    cq = seq[1]
    assert cq["cases"] == CQ_IDS and cq["level"] == 1 and cq["points"] == 1.0 and cq["budget"] == 65536
    assert cq["item"] == "CQ-FROMSCRATCH"
    # nothing else moved: the other live packs and the staged one are as before. Integration 2026-11-20 added the
    # three Chat pack parts (packs45) and made chat/h6_know set-3 (everygap) live; KNOWGAP set-1 stays staged; the six
    # NSFW+ packs were wired 2026-11-21 (owner ruling); the Fixes pack (work/c3_fix set-3) went live 2026-11-21
    chat_pk = ["chat/pk_character", "chat/pk_cowrites", "chat/pk_remembers"]
    nsfw_pk = ["nsfw/n1_scene", "nsfw/n6a_craft", "nsfw/n7_taboo", "nsfw/n8_bodies", "nsfw/u1_will", "nsfw/u3_know"]
    assert sorted(found) == sorted(["chat/h6_know", *chat_pk, *nsfw_pk, "work/c1_implied", "work/c2_predict",
                                    "work/c2_trace", "work/c3_fix", "work/c5_tools"])
    live = ladder.discover(standard, staged=False)
    assert sorted(live) == sorted(["chat/h6_know", *chat_pk, *nsfw_pk, "work/c1_implied", "work/c2_predict",
                                   "work/c2_trace", "work/c3_fix", "work/c5_tools"])
    assert [s["n"] for s in live["chat/h6_know"]] == [3]


def test_the_items_are_the_4_1_text_byte_for_byte():
    old = json.loads((ROOT / "archive/4.1.0/cases/ladder/work/code/set-1.json").read_text(encoding="utf-8"))
    old = {c["id"]: c for c in old["cases"] if isinstance(c, dict)}
    new = json.loads((ROOT / "cases/ladder/work/code/classic-code.json").read_text(encoding="utf-8"))
    new = {c["id"]: c for c in new["cases"] if isinstance(c, dict)}
    for cid in CQ_IDS:
        assert new[cid] == old[cid], cid


def test_no_case_is_in_two_discovered_packs(standard):
    seen = {}
    for key, seq in ladder.discover(standard, staged=True).items():
        for s in seq:
            for cid in s["cases"]:
                seen.setdefault(cid, []).append(f"{key}#{s['n']}")
    assert {c: v for c, v in seen.items() if len(v) > 1} == {}


def test_cq_is_locked_below_the_gate_and_opens_at_it_with_the_implied_pack(standard):
    seq = ladder.discover(standard)["work/c1_implied"]
    unlock = ladder.unlock_share(standard)
    assert unlock == 0.9
    below = ladder.states(0.8999, {}, seq, unlock)
    assert below == {1: "locked", 2: "locked"}
    at = ladder.states(0.90, {}, seq, unlock)
    assert at == {1: "pending", 2: "pending"}          # one level: both open together, both unrun until generated


def test_pack_points_are_separate_from_standard_points(standard):
    seq = ladder.discover(standard)["work/c1_implied"]
    unlock = ladder.unlock_share(standard)
    # standard 10-point share .92 of a 4-point part; IMPLIED L5 .8, CQ .5: the sets add on top, never inside
    pts = ladder.points(0.92, 4.0, {1: 0.8, 2: 0.5}, seq, unlock)
    assert pts == pytest.approx(0.92 * 4.0 + 0.8 + 0.5)
    # locked: standard only
    assert ladder.points(0.89, 4.0, {1: None, 2: None}, seq, unlock) == pytest.approx(0.89 * 4.0)
    # the Writes segment is built from the part's standard share alone (segments45 has no pack input)
    wr = standard["segments45"]["code"]["writes"]
    assert wr["parts"] == {"c1_implied": 1.0} and wr["max"] == 4


def test_the_cut_rule_is_the_existing_one(standard):
    raw = json.loads(SET.read_text(encoding="utf-8"))
    assert "ran_out" not in raw and "cut_rule" not in raw        # no rule of its own
    assert standard["cut_at_full_budget"] == "zero"                # the release's one rule for short-keyed replies
    assert raw["budget"] == standard["bank_budget"] == 65536       # CQ's 4.1 budget = the release bank budget


def test_cq_is_graded_under_python_3_13(standard):
    """The profile's pin names all three ids (graders.grader_pins reads exactly this block; the live lookup goes through
    a per-process profile cache that other tests in a worker can leave on another composition, so it is not used here)."""
    pin = standard["grader_pins"]
    assert pin["python_minor"] == "3.13" and pin["cfg_transform"] == "cq_rule_group"
    assert set(CQ_IDS) <= set(pin["cases"])


# ---- the kill test: a new pack set file moves nothing that is a stamp or a standard set -------------------------

def _dummy_tree(tmp: Path) -> Path:
    dst = tmp / "ladder"
    shutil.copytree(ROOT / "cases/ladder", dst)
    classic = json.loads((ROOT / "cases/ladder/work/code/classic-code.json").read_text(encoding="utf-8"))
    cq = [c for c in classic["cases"] if isinstance(c, dict)][0]
    cases = []
    for i in (1, 2, 3):
        c = copy.deepcopy(cq)
        c["id"] = f"ZZ-DUMMY-{i}"
        cases.append(c)
    (dst / "work/c1_implied/set-9.json").write_text(json.dumps(
        {"level": 1, "points": 1.0, "item": "CQ-FROMSCRATCH", "budget": 65536, "cases": cases}), encoding="utf-8")
    return dst


def test_a_new_pack_file_moves_no_stamp_and_no_standard_set(standard, tmp_path):
    base = tmp_path / "base"
    shutil.copytree(ROOT / "cases/ladder", base)
    dummy = _dummy_tree(tmp_path)
    t0 = version.case_table(ladder_dirs=[base])
    t1 = version.case_table(ladder_dirs=[dummy])
    assert set(t1) - set(t0) == {"ZZ-DUMMY-1", "ZZ-DUMMY-2", "ZZ-DUMMY-3"}
    assert {k: v for k, v in t1.items() if k in t0} == t0            # every existing case stamp identical
    d0 = ladder.discover(standard, dirs=[base], staged=True)
    d1 = ladder.discover(standard, dirs=[dummy], staged=True)
    assert [s["n"] for s in d1["work/c1_implied"]] == [1, 2, 9]
    def bare(seq):                                                    # a set without its path (the trees differ)
        return [{k: v for k, v in s.items() if k != "path"} for s in seq if s["n"] != 9]
    for key in d0:                                                    # the standard sets of every part are as before
        assert bare(d1[key]) == bare(d0[key])


def test_the_report_time_scorer_stamp_does_not_read_the_ladder_folder():
    files = list(segments45.SCORER_FILES) + list(segments45.SCORER_DATA)
    assert not [f for f in files if "ladder" in f or f.startswith("cases")]
    assert "profiles/bench45.yaml" in files and "calibration/MARKS-INDEX-45.md" in files
