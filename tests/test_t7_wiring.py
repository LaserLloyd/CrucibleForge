"""T7 (first half): the Main + Frontier composition wired into profiles/bench45.yaml, the unlock, the F4 grader pin,
the F5 COMPACTOR family and the measured Main budget (budget/measured-main-45.json). The composition is
PROVISIONAL (gates re-opened 2026-11-13 16:15); weights and counts live in the profile, and these tests read them
from there, so a reweight edits the profile and the EXPECTED table below in one commit."""
import json
import re
import sys
from pathlib import Path

import pytest

from crucibleforge import budget, config, graders, ladder, profiles, report

ROOT = Path(__file__).resolve().parent.parent
CFG = config.load_config(config.EXAMPLE_CONFIG_PATH)

#: part -> (points, instance count) of the Main tier, as frozen/provisional (T1 F1, T3, T45)
# rc1 (2026-11-13, lead R18): TRACEVM L8 x8 left Main for the bonus pack work/trace; MC1/3/4 reweighted 3/3/4
MAIN = {"work": {"mc1_functions": (3, 2), "mc3_fix": (3, 2), "mc4_solve": (4, 3)},
        "chat": {"mh1_conversation": (3, 1), "mh2_character": (4, 1), "mh3_state": (3, 2)}}
FRONTIER = {"work": [4, 4, 1, 1], "chat": [6, 2, 2]}


@pytest.fixture(scope="module")
def prof():
    return profiles.load_profile("bench45", CFG)


def test_main_tier_points_and_instance_counts(prof):
    for score, parts in MAIN.items():
        decl = prof["scores"][score]["parts"]
        for pk, (pts, n) in parts.items():
            assert decl[pk]["max"] == pts, (score, pk)
            assert len(decl[pk]["cases"]) == n, (score, pk)
        assert sum(p[0] for p in parts.values()) == 10


@pytest.mark.live_ladder   # reads the committed packs (conftest empty-ladder guard, 4e1cfeb)
def test_frontier_sets_sum_to_ten_per_section(prof):
    sets = ladder.discover(prof)
    for score, pts in FRONTIER.items():
        seq = sets[f"{score}/frontier"]
        assert [x["points"] for x in seq] == pts
        assert sum(pts) == 10
        assert all(x["level"] == 1 for x in seq)          # one level: they open together


def test_no_frontier_nsfw_and_main_nsfw_is_on(prof):
    # rc1 (lead 2026-11-13): Main NSFW is a full section of the default run (SYSTEM-45 sections 4, 9)
    assert "nsfw/frontier" not in ladder.discover(prof)
    assert "frontier" not in prof["scores"]["nsfw"]["parts"]
    assert prof["main_nsfw"]["enabled"] is True
    want = {"mn1": "NC3-palm-house", "mn2": "NX2-juniper", "mn3": "UX1-tide-cell", "mn4": "NC2-after-hours",
            "mn5": "NC1-tideglass"}
    assert {k: prof["scores"]["nsfw"]["parts"][k]["cases"] for k in want} == {k: [v] for k, v in want.items()}
    assert sum(prof["scores"]["nsfw"]["parts"][k]["max"] for k in want) == 10
    text = (ROOT / "profiles" / "bench45.yaml").read_text(encoding="utf-8")
    assert "J7 hold" in text and "pending owner decision (J11)" in text


def test_main_nsfw_flag_wires_the_five_stored_cases_with_their_points(prof):
    import copy
    p = copy.deepcopy(prof)
    p["main_nsfw"]["enabled"] = True
    profiles.apply_main_nsfw(p)
    ids = list(p["main_nsfw"]["cases"].values())
    assert p["cases"]["nsfw"] == ids and len(ids) == 5
    for pk, cid in p["main_nsfw"]["cases"].items():
        assert p["scores"]["nsfw"]["parts"][pk]["cases"] == [cid]
        assert p["scores"]["nsfw"]["parts"][pk]["max"] == p["main_nsfw"]["points"][pk]
    assert sum(p["scores"]["nsfw"]["parts"][pk]["max"] for pk in p["main_nsfw"]["cases"]) == 10
    assert set(ids) <= set(p["pack_exclude"])
    for seq in ladder.discover(p).values():
        for s in seq:
            assert not set(ids) & set(s["cases"])


def test_main_items_sit_in_exactly_one_place(prof):
    seen = {}
    for key, seq in ladder.discover(prof).items():
        for s in seq:
            for c in s["cases"]:
                seen.setdefault(c, []).append(key)
    main_ids = {c for cat in ("tooluse", "codebank", "rpdeep", "coding") for c in prof["cases"].get(cat, [])}
    assert not main_ids & set(seen)


@pytest.mark.live_ladder   # reads the committed packs (conftest empty-ladder guard, 4e1cfeb)
def test_unlock_is_point_nine_of_the_section(prof):
    assert prof["ladder"]["unlock"] == 0.9   # Jake 2026-11-14: "The unlock is .9."

    def card(share):
        def fake(src, w, partial=frozenset()):
            out = {"chat": None, "coding": None, "total": None, "tok_per_s": None, "components": {},
                   "weights": dict(w)}
            out.update({g: share for g in w})
            return out
        mp = pytest.MonkeyPatch()
        mp.setattr(report, "scorecard", fake)
        try:
            sc = report._declared_weights(prof, None, None)[0]
            return report.points_scorecard({}, sc, prof)
        finally:
            mp.undo()
    hit, miss = card(90.0), card(89.0)
    for score in ("work", "chat"):
        assert hit["columns"][score]["unlocked"] is True
        assert miss["columns"][score]["unlocked"] is False
        assert miss["columns"][score]["frontier"] is None            # "not unlocked", never 0
        assert hit["columns"][score]["frontier_max"] == 10


def test_set_points_weight_the_pack_value():
    seq = [{"n": 1, "level": 1, "points": 4.0}, {"n": 2, "level": 1}]
    p = ladder.points(1.0, 0, {1: 0.5, 2: 1.0}, seq, 0.8)
    assert p == pytest.approx(0.5 * 4 + 1.0)


def test_board_notes_are_in_the_profile_and_sets():
    fc2 = json.loads((ROOT / "cases/ladder/work/frontier/set-2.json").read_text(encoding="utf-8"))
    assert "65,536 tokens" in fc2["board_note"] and "score 0" in fc2["board_note"]       # F1
    y = (ROOT / "profiles" / "bench45.yaml").read_text(encoding="utf-8")
    assert "board_notes:" in y and "one run" in y                                       # N4


# ---------------------------------------------------------------- F4: FC4's interpreter pin

def test_fc4_is_pinned_to_python_313(prof):
    pin = prof["grader_pins"]
    assert pin["python_minor"] == "3.13"
    assert set(pin["cases"]) == {"CQ1-format-spec-from-scratch", "CQ2-regex-engine-from-scratch",
                                 "CQ3-spreadsheet-engine"}


class _R:
    finish_reason = "stop"

    def __init__(self, t):
        self.t = t

    def scoreable_text(self):
        return self.t


def _case(cid):
    return {"id": cid, "grader": "python_exec",
            "grader_config": {"tests": "import ast as _ast\n_tree = _ast.parse('x')\nfor _n in _ast.walk(_tree):\n    pass\n", "test_groups": {
                "minor": "import sys\nassert sys.version_info[:2] == (3, 13), sys.version\n"}}}


def test_fc4_is_graded_under_313_and_fails_under_another_minor(prof, monkeypatch):
    profiles.set_active("bench45")
    try:
        res = graders.grade(_R("```python\nx = 1\n```"), _case("CQ1-format-spec-from-scratch"))
        assert res["rate"] == 1.0, res
        # the same harness under a different minor must FAIL (the pin is what makes it pass above)
        monkeypatch.setattr(graders, "interpreter_for",
                            lambda minor: (graders._REAL_PY, graders._PY_ROOT) if sys.version_info[:2] != (3, 13)
                            else (_other_python(), None))
        if sys.version_info[:2] != (3, 13):
            res2 = graders.grade(_R("x = 1"), _case("CQ1-format-spec-from-scratch"))
            assert res2["rate"] == 0.0
    finally:
        profiles.set_active(None)


def _other_python():
    pytest.skip("this interpreter is 3.13; the negative leg needs a default of another minor")


def test_a_missing_pinned_interpreter_is_a_refusal_not_a_fallback(monkeypatch):
    monkeypatch.setattr(graders.shutil, "which", lambda *_: None)
    monkeypatch.setattr(graders.Path, "is_file", lambda self: False)
    with pytest.raises(graders.GraderInterpreterMissing):
        graders.interpreter_for("3.99")


def test_unpinned_cases_and_the_41_profile_are_untouched(monkeypatch):
    profiles.set_active("bench")
    try:
        assert graders.grader_pins({"id": "CQ1-format-spec-from-scratch"}) is None
    finally:
        profiles.set_active(None)


# ---------------------------------------------------------------- F5: COMPACTOR is an engine family

def test_compactor_is_a_registered_family_with_its_frozen_baselines():
    from crucibleforge import gen
    from crucibleforge.gen import compactor
    assert "compactor" in gen.families()
    base = []
    for k in ("s1", "s2", "s3"):
        g = {"type": "compactor", "key": k}
        kw = dict(release_seed="4.5.0", case_id="x")
        mat = compactor.materialize(g, **kw)
        assert mat["console"] == {"family": "compactor", "key": k}
        assert compactor.grade(compactor.reference(g, **kw), mat) == pytest.approx(0.85)
        base.append(compactor.grade(compactor.mutants(g, **kw)[0], mat) * 100)      # read-everything
        assert compactor.grade(compactor.mutants(g, **kw)[1], mat) == pytest.approx(0.05)
    assert base == pytest.approx([15.0, 61.0, 40.0])
    assert sum(base) / 3 == pytest.approx(38.7, abs=0.05)           # re-measured on the frozen seeds; limit 40


def test_the_runner_drives_a_console_case_through_the_simulator(monkeypatch):
    from crucibleforge import runner
    from crucibleforge.api import ChatResult
    from crucibleforge.gen import compactor
    g = {"type": "compactor", "key": "s1"}
    case = {"id": "FC3-compactor-s1", "category": "coding", "system": compactor.SYSTEM,
            **compactor.materialize(g, release_seed="4.5.0", case_id="x")}
    replies = iter(compactor.reference(g, release_seed="4.5.0", case_id="x").split("\n<<<TURN>>>\n"))

    class Ctx:
        def budget(self, k):
            return k

        def recover_for(self, c):
            return None

        def call(self, messages, **kw):
            t = next(replies)
            return ChatResult(response_text=t, reasoning_text="", finish_reason="stop", tool_calls=[],
                              prompt_tokens=10, completion_tokens=5, reasoning_tokens=0)
    monkeypatch.setattr(runner, "row_budget", lambda *a, **k: None)
    monkeypatch.setattr(runner, "_prompt_tokens", lambda m: 10)
    row = runner._run_console_loop(case, {"case_id": case["id"]}, Ctx(), 512, None, None)
    assert row["score"] == pytest.approx(0.85) and row["grade"] == "pass"
    assert row["console"]["state"] == "stopped" and row["elements"]["rate"] == pytest.approx(0.85)


# ---------------------------------------------------------------- measured budget (item 4)

M = json.loads((ROOT / "budget" / "measured-main-45.json").read_text(encoding="utf-8"))
LIMIT_TOKENS = M["limit_min"] * 60 * M["tps"]          # 36,000


def _j10(reason):
    return pytest.mark.xfail(reason=f"J10: {reason}", strict=True)


# Jake R21 (SYSTEM-45 section 16): the run length is derived from the margin-of-error bar and reported,
# never asserted. These tests keep the measured figures honest (present, numeric) without a time gate.
@pytest.mark.parametrize("cls", ["Haiku", "Sonnet", "Fable", "DSoff"])
def test_measured_main_minutes_are_reported_not_gated(cls):
    for variant, t in M["totals"][cls].items():
        assert isinstance(t["minutes_total"], (int, float)) and t["minutes_total"] > 0, (cls, variant)


def test_run_length_is_a_profile_value_not_an_assert(prof):
    assert prof["default_run"]["ceiling_minutes"] == 180
    assert prof["default_run"]["design_margin_points"] == 2.5
    assert prof["run_settings"]["kv_cache_type"] == "q8_0"


@pytest.mark.live_ladder   # reads the committed packs (conftest empty-ladder guard, 4e1cfeb)
def test_tracevm_is_a_bonus_pack_with_the_frozen_instances(prof):
    seq = ladder.discover(prof)["work/trace"]
    assert len(seq) == 1 and seq[0]["points"] == 1 and seq[0].get("item") == "TRACEVM"
    ids = list(seq[0]["cases"])
    assert ids == [f"TRACEVM-core-8-{i}" for i in range(1, 9)]
    assert prof["scores"]["work"]["parts"]["trace"]["unlock_part"] == "work"
    assert not any(e.get("family") == "tracevm" for e in prof["core_generated"])


def test_measured_numbers_cover_every_main_item():
    assert set(M["table"]) == {"MC1a", "MC1b", "MC2", "MC3", "MC4", "MH1", "MH2", "MH3"}
