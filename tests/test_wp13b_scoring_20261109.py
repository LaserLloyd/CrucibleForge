"""WP-13b (4.5 scoring/report side): missing != 0, hard part, tie/separation,
variance, bench_min, rung-2 fact groups, OOC gate fix, element lint."""
from __future__ import annotations

import copy
import json

import pytest

from crucibleforge import element_lint, profiles, report, rpdeep, scoring45

import test_points_board_39 as pb


# ------------------------------------------------------------- missing != 0


@pytest.fixture(autouse=True, scope="module")
def _legacy_profile_dir():
    """These tests predate the 4.5 v2 bench45 (hard sets, 29 cases): they run on the
    pre-WP-4 profile kept in tests/fixtures/legacy_profiles/."""
    from crucibleforge import profiles
    mp = pytest.MonkeyPatch()
    legacy = __import__("pathlib").Path(__file__).parent / "fixtures" / "legacy_profiles"
    live = profiles.PROFILES_DIR
    mp.setattr(profiles, "_dirs", lambda cfg: [legacy, live])
    mp.setattr(profiles, "activate_cases_root", lambda prof: None)   # root stays cases/
    yield
    mp.undo()


def _row(**kw):
    r = {"case_id": "C1", "repeat": 1, "turn": None, "category": "rp", "grade": "pass",
         "response": "hello", "finish_reason": "stop"}
    r.update(kw)
    return r


@pytest.mark.parametrize("rows,why", [
    ([_row(grade="error", error="x", error_kind="transport")], "error"),
    ([_row(grade="error", error_kind="ctx_short")], "ctx_short"),
    ([_row(response="")], "empty"),
    ([_row(response="", tool_calls=[{"n": 1}])], None),
    ([_row(response="", finish_reason="length")], None),     # ran out = the model's own
    ([_row(category="codebank", response="")], None),
    ([_row(needs_judge=True)], "unjudged"),
    ([_row(needs_judge=True, judge={"judge_failed": True})], "judge_failed"),
    ([_row(needs_judge=True, judge={"judge_failed": True, "empty_generation": True})], None),
    ([_row(needs_judge=True, judge={"elements": {"unjudged": ["a"]}})], "unjudged"),
    ([_row(needs_judge=True, judge={"scores": {}})], None),
])
def test_job_missing_reason(rows, why):
    assert scoring45.job_missing_reason(rows) == why


def test_split_missing_drops_whole_session_and_lists_it():
    by_t = {1: [_row(case_id="S", turn=1), _row(case_id="S", turn=2, response=""),
                _row(case_id="K", repeat=1), _row(case_id="K", repeat=2, grade="error")],
            2: [], 3: []}
    out, miss = scoring45.split_missing(by_t)
    assert {(m["case_id"], m["repeat"], m["reason"]) for m in miss} == {
        ("S", 1, "empty"), ("K", 2, "error")}
    assert [(r["case_id"], r["repeat"]) for r in out[1]] == [("K", 1)]


def test_pending_policy_flag():
    assert scoring45.pending_policy({"missing_rows": "pending"})
    assert not scoring45.pending_policy({"errored_rows": "fail"})


# ----------------------------------------------------- separation / ranks

def test_simultaneous_separation_and_rank_intervals():
    import random
    rnd = random.Random(1)
    n = 400
    base = {"a": 9.0, "b": 8.9, "c": 6.0}
    # a and b share their case noise (paired), c is far below
    noise = [rnd.gauss(0, 0.5) for _ in range(n)]
    s = {"a": [9.0 + x + rnd.gauss(0, .05) for x in noise],
         "b": [8.9 + x + rnd.gauss(0, .3) for x in noise],
         "c": [6.0 + x for x in noise]}
    sep = scoring45.simultaneous_separation(s, base, ["a", "b", "c"], 10, 0.9)
    by = {(p["a"], p["b"]): p for p in sep["pairs"]}
    assert not by[("a", "b")]["separated"]
    assert by[("a", "c")]["separated"] and by[("b", "c")]["separated"]
    assert sep["critical"] >= 1.2     # wider than a single 90% two-sided z
    ri = scoring45.rank_intervals(s, ["a", "b", "c"], 0.9)
    assert ri["c"] == [3, 3] and ri["a"][0] == 1 and ri["b"][1] <= 2


def test_identical_points_are_not_separated():
    s = {"a": [1.0] * 20, "b": [1.0] * 20}
    sep = scoring45.simultaneous_separation(s, {"a": 1.0, "b": 1.0}, ["a", "b"])
    assert sep["pairs"][0]["separated"] is False


# ---------------------------------------------------------------- variance

def test_variance_components_labels():
    j = scoring45.judge_sample_spread([
        {"judge": {"elements": {"results": [
            {"id": "x", "pass": True, "verified_samples": [0, 1], "detail": "yes (2/3 samples)"},
            {"id": "y", "pass": True, "detail": "judge: yes"}]}}}])
    assert j["multi_sample_elements"] == 1 and j["split_elements"] == 1
    v = scoring45.variance_components(0.3, None, False, j, None)
    assert v["generation"]["sd"] is None and "absent" in v["generation"]["basis"]
    v = scoring45.variance_components(0.3, None, False, j, 0.2)
    assert v["generation"]["basis"] == "model-based"
    v = scoring45.variance_components(0.3, 0.1, True, j, 0.2)
    assert v["generation"]["basis"] == "observed repeats"


def test_bench_min_splits_main_and_hard_and_cuts_idle_gaps():
    rows = [{"case_id": "M", "category": "rp", "ts": f"2026-11-01T00:{m:02d}:00+00:00"} for m in (0, 10, 20)]
    rows += [{"case_id": "M", "category": "rp", "ts": "2026-11-02T10:00:00+00:00"},
             {"case_id": "M", "category": "rp", "ts": "2026-11-02T10:05:00+00:00"}]
    rows += [{"case_id": "H", "category": "rp", "ts": "2026-11-03T00:00:00+00:00",
              "judged_ts": "2026-11-03T00:30:00+00:00"},
             {"case_id": "H", "category": "rp", "ts": "2026-11-03T00:10:00+00:00",
              "judged_ts": "2026-11-03T00:40:00+00:00"}]
    b = scoring45.bench_min(rows, {"H"})
    assert b["main"]["gen_min"] == 25.0          # 20 + 5, the overnight gap cut
    assert b["main"]["from"] == "2026-11-01" and b["main"]["to"] == "2026-11-02"
    assert b["hard_min"] == 20.0 and b["hard"]["judge_min"] == 10.0
    assert scoring45.bench_min(rows[:1], set())["main_min"] is None


# --------------------------------------------------------------- rpdeep

def test_fact_groups_default_and_turn_window():
    els = [{"id": "a", "turns": [1]}, {"id": "b", "turns": [2]}, {"id": "c", "turns": [2]},
           {"id": "d", "turns": [3, 4]}, {"id": "e", "turns": [9]}]
    base = rpdeep.fact_groups(els)
    assert [g[0] for g in base] == [(1,), (2,), (3, 4), (9,)]
    win = rpdeep.fact_groups(els, "turn_window", 4)
    assert [g[0] for g in win] == [(1, 2, 3, 4), (9,)]
    assert sorted(e["id"] for e in win[0][1]) == ["a", "b", "c", "d"]
    assert [g[0] for g in rpdeep.fact_groups(els, "turn_window", 2)] == [(1, 2), (3, 4), (9,)]


GOOD = '"The tea is on," she said, and set the kettle down.'


def _ooc_el(detail="judge: yes, quote verified (1/1 samples)"):
    return {"E": {"id": "E", "pass": False, "judge_pass": True, "quote": "the tea is on",
                  "detail": rpdeep.OOC_PREFIX + detail}}


#: the judge-side group_meta keys the 4.5 judge stores (fix45-a contract)
JS = {"refused": False, "ooc_quote": "", "in_character_votes": [False]}


def test_single_judge_ooc_flag_does_not_zero_a_passed_item():
    j = {"groups": [{"turns": [1], "elements": ["E"], "out_of_character": True, **JS}]}
    out = rpdeep.rescue_ooc_zeroing(_ooc_el(), [GOOD], j, "chat")
    assert out["E"]["pass"] is True and "flag ignored" in out["E"]["detail"]


def test_ooc_flag_counts_on_format_gate_refusal_or_two_sample_quote():
    j = {"groups": [{"turns": [1], "elements": ["E"], **JS}]}
    bullets = "- one\n- two\n- three"
    assert not rpdeep.rescue_ooc_zeroing(_ooc_el(), [bullets], j, "chat")["E"]["pass"]
    g = {"turns": [1], "elements": ["E"], **JS, "refused": True}
    assert not rpdeep.rescue_ooc_zeroing(_ooc_el(), [GOOD], {"groups": [g]}, "chat")["E"]["pass"]
    g = {"turns": [1], "elements": ["E"], "refused": False,
         "ooc_quote": "she said, and set the kettle down",
         "in_character_votes": [False, False, True]}
    assert not rpdeep.rescue_ooc_zeroing(_ooc_el(), [GOOD], {"groups": [g]}, "chat")["E"]["pass"]
    g["in_character_votes"] = [False, True, True]
    assert rpdeep.rescue_ooc_zeroing(_ooc_el(), [GOOD], {"groups": [g]}, "chat")["E"]["pass"]


def test_ooc_rescue_is_a_no_op_without_the_judge_side_keys():
    """review F1/F2: a 4.1 verdict (no refused / in_character_votes in its
    group meta) keeps the judge's zeroing; the judge's refusal is never lost."""
    j = {"groups": [{"turns": [1], "elements": ["E"], "out_of_character": True}]}
    out = rpdeep.rescue_ooc_zeroing(_ooc_el(), [GOOD], j, "chat")
    assert out["E"]["pass"] is False and out["E"]["detail"].startswith(rpdeep.OOC_PREFIX)
    j = {"groups": [{"turns": [1], "elements": ["E"], "refused": True, "ooc_quote": "",
                     "in_character_votes": [True, True, True]}]}
    assert rpdeep.rescue_ooc_zeroing(_ooc_el(), [GOOD], j, "chat")["E"]["pass"] is False


def _ooc_session(profile):
    case = {"id": "T-ooc", "rubric": "rp_deep", "category": "rpdeep", "turns": [{}],
            "format_gate": "chat",
            "judge_elements": [{"id": "E", "fact": "E", "ftype": "lore", "weight": 1}]}
    row = {"case_id": "T-ooc", "profile": profile, "category": "rpdeep",
           "conversation": [{"role": "user", "content": "hi"},
                            {"role": "assistant", "content": GOOD}],
           "judge": {"groups": [{"turns": [1], "elements": ["E"], **JS}],
                     "elements": {"results": [{**_ooc_el()["E"]}]}}}
    return row, case


@pytest.mark.archive_default
def test_ooc_rescue_only_on_a_profile_that_switches_it_on():
    """review F1 (BLOCKER): the 4.1 `bench` profile scores stored verdicts as
    4.1 did; only bench45 (`ooc_gate: votes`) applies the rescue."""
    row, case = _ooc_session("bench")
    assert not rpdeep.ooc_votes_gate(row, case)
    assert rpdeep.score_row(row, case, [])["rate"] == 0.0
    row, case = _ooc_session("bench45")
    assert rpdeep.ooc_votes_gate(row, case)
    assert rpdeep.score_row(row, case, [])["rate"] == 1.0
    row, case = _ooc_session(None)
    assert rpdeep.score_row(row, case, [])["rate"] == 0.0
    assert rpdeep.ooc_votes_gate(row, {**case, "ooc_gate": "votes"})


def test_a_judge_no_stays_no_under_the_ooc_flag():
    j = {"groups": [{"turns": [1], "elements": ["E"], **JS}]}
    out = rpdeep.rescue_ooc_zeroing(_ooc_el("judge: no"), [GOOD], j, "chat")
    assert out["E"]["pass"] is False and out["E"]["detail"].startswith("judge: no")


# ---------------------------------------------------------- element lint

GOODEL = {"id": "A1", "class": "fact", "evidence": "single", "turns": [1],
          "q": "Does Mina name Kea as her dog? Quote ONE contiguous sentence. NO if she names another dog.",
          "gold_quote": "Kea's the boss of this hut now."}
CASE = {"id": "T", "rubric": "rp_deep", "category": "rpdeep", "turns": [{}], "judge_elements": []}


def _rules(el, **kw):
    return {(r, s) for r, s, _ in element_lint.lint_element({**GOODEL, **el}, CASE, **kw)}


def test_clean_element_has_no_findings():
    assert element_lint.lint_element(GOODEL, CASE, None, 3) == []


@pytest.mark.parametrize("patch,rule", [
    ({"class": "maybe"}, "EL-1"),
    ({"q": "Does she name Kea? Quote ONE sentence."}, "EL-2"),
    ({"q": "Does she? Is it? Was it? Quote ONE. NO if not."}, "EL-3"),
    ({"dim": "continuity", "q": "Does she stage the room, AND does she come? Quote it. NO if not."}, "EL-4"),
    ({"evidence": "negative"}, "EL-5"),
    ({"q": "Does she name Kea? NO if she names another dog."}, "EL-6"),
    ({"gold_quote": None}, "EL-7"),
    ({"q": "Does she say it, inside quotation marks? Quote one sentence. NO if not."}, "EL-8"),
    ({"q": "At turn {T0} does she repeat it? Quote one sentence. NO if not."}, "EL-9"),
    ({"dim": "explicitness", "cap": 5}, "EL-10"),
    ({"gate": "heat", "class": "craft"}, "EL-11"),
    ({"samples": 3}, "EL-16"),
])
def test_each_rule_fires(patch, rule):
    assert rule in {r for r, _ in _rules(patch)}


def test_turn_bound_and_gold_in_fixture():
    assert ("EL-13", "error") in _rules({"turns": [4]}, n_turns=3)
    assert ("EL-7", "error") in _rules({}, fixture="nothing like it")
    assert ("EL-7", "error") not in _rules({}, fixture="x. Kea's the boss of this hut now. y")


def test_strict_only_for_element_standard_45():
    bad = {**CASE, "judge_elements": [{**GOODEL, "q": "no clause here, quote it"}]}
    assert {f["severity"] for f in element_lint.lint_case(bad)} == {"warn"}
    bad45 = {**bad, "element_standard": 45}
    assert "error" in {f["severity"] for f in element_lint.lint_case(bad45)}
    dup = {**CASE, "element_standard": 45, "judge_elements": [GOODEL, GOODEL]}
    assert any(f["rule"] == "EL-12" and f["severity"] == "error"
               for f in element_lint.lint_case(dup))


def test_live_41_cases_lint_without_errors():
    from crucibleforge.config import load_cases
    errs, warns, _ = element_lint.lint_cases(load_cases(["rpdeep", "nsfw"]))
    assert errs == 0 and warns > 0


def test_named_term_and_lexicon():
    ok, n = element_lint.named_term_ok("The ladle hung. A ladle, then the Ladle again.", "ladle", 3)
    assert ok and n == 3
    assert not element_lint.named_term_ok("ladle ladle", "ladle", 3)[0]
    assert element_lint.named_term_ok("x", "", 1) == (False, 0)
    assert element_lint.lexicon_hit("a quote", None) is None
    assert element_lint.lexicon_hit("she said Foo!", ["foo"]) is True
    assert element_lint.lexicon_hit("she said bar", ["foo"]) is False


# ------------------------------------------------- board: hard + pending

HARD = [{"id": "HC1-x", "category": "coding", "hard_score": "code", "hard_rev": "4.5"},
        {"id": "HC2-y", "category": "coding", "hard_score": "code", "hard_rev": "4.5"}]


def _env(tmp_path, monkeypatch, pending=True, hard=True):
    def edit(p):
        pb._work_only(p)
        p["scores"]["work"]["parts"] = {
            "agentic": {"label": "Agentic", "short": "Ag", "max": 3, "scoring": ["codeagent", "tooluse"]},
            "code": {"label": "Code", "short": "Co", "max": 2.5, "scoring": ["codebank", "coding"]},
            "reasoning": {"label": "Reasoning", "short": "Re", "max": 2.5, "scoring": ["reasoning"]}}
        if hard:
            p["scores"]["work"]["hard"] = {"label": "Hard", "short": "Hard", "max": 2,
                                           "file": "cases/hard/code/4.5.json"}
        p.setdefault("headline", {}).update(variance_components=True, variance_resamples=60)
        if pending:
            p["missing_rows"] = "pending"
    env = pb._setup(tmp_path, monkeypatch, edit)
    monkeypatch.setattr(profiles, "hard_cases", lambda prof, cases_dir=None: copy.deepcopy(HARD))
    monkeypatch.setattr(profiles, "_HARD_CACHE", {})
    from crucibleforge import version
    orig = version.row_current
    monkeypatch.setattr(version, "row_current",
                        lambda r, *a, **k: True if str(r.get("case_id", "")).startswith("HC")
                        else orig(r, *a, **k))
    return env


def _hard_rows(bad=()):
    return [pb._row(c["id"], "coding", 1, c["id"] in bad) for c in HARD]


def _scored(env, rows_by_label):
    pb._write(env, rows_by_label)
    return report.board_stats(None, env.cfg)[1]


@pytest.mark.archive_default
def test_hard_part_adds_two_points_and_exports_subscores(tmp_path, monkeypatch):
    env = _env(tmp_path, monkeypatch)
    st = _scored(env, {"a": pb._rows() + _hard_rows(), "b": pb._rows() + _hard_rows(bad={"HC1-x"})})
    sa, sb = (st[x]["scorecard"]["subscores"]["work"] for x in "ab")
    assert sa["standard_max"] == pytest.approx(8) and sa["hard_max"] == 2 and sa["max"] == pytest.approx(10)
    assert sa["hard"] == pytest.approx(2) and sb["hard"] == pytest.approx(1)
    assert sa["standard"] == pytest.approx(8) and sa["score"] == pytest.approx(10)
    assert sb["score"] == pytest.approx(9)
    assert st["a"]["scorecard"]["parts"]["work"]["hard"] == pytest.approx(2)
    assert st["a"]["ranked"] and st["a"]["status"] == "ranked" and st["a"]["tie_group"] in ("1", "1=")


@pytest.mark.archive_default
def test_hard_rows_never_leak_into_standard_parts(tmp_path, monkeypatch):
    env = _env(tmp_path, monkeypatch)
    st = _scored(env, {"a": pb._rows() + _hard_rows(bad={"HC1-x", "HC2-y"})})
    sc = st["a"]["scorecard"]["subscores"]["work"]
    assert sc["standard"] == pytest.approx(8) and sc["hard"] == pytest.approx(0)


@pytest.mark.archive_default
def test_missing_hard_rows_leave_the_model_pending(tmp_path, monkeypatch):
    env = _env(tmp_path, monkeypatch)
    st = _scored(env, {"a": pb._rows() + _hard_rows(), "b": pb._rows()})
    assert st["b"]["ranked"] is False and st["b"]["status"] == "pending"
    assert st["b"]["tie_group"] is None and st["b"]["scorecard"]["total"] is None
    assert any("HC1-x" in r for r in st["b"]["pending_reasons"])
    assert st["a"]["ranked"]


@pytest.mark.archive_default
def test_an_errored_row_is_missing_not_zero_under_bench45_but_zero_under_41(tmp_path, monkeypatch):
    rows = [{**r, "grade": "error", "error": "boom", "error_kind": "transport"}
            if r["category"] == "tooluse" and r["repeat"] == 1 else r
            for r in pb._rows() + _hard_rows()]
    env = _env(tmp_path, monkeypatch)
    st = _scored(env, {"e": rows})["e"]
    assert st["status"] == "pending" and not st["ranked"]
    assert st["missing_jobs"] and all(m["reason"] == "error" for m in st["missing_jobs"])
    assert st["errored_rows"] == {"n": 0, "total": 0}
    # a 4.1-style profile (no missing_rows) keeps counting it failed
    env2 = _env(tmp_path / "b", monkeypatch, pending=False)
    st2 = _scored(env2, {"e": rows})["e"]
    assert st2["errored_rows"]["n"] > 0 and st2["missing_jobs"] == []


@pytest.mark.archive_default
def test_board_exports_tie_rank_interval_separation_and_bench_min(tmp_path, monkeypatch):
    env = _env(tmp_path, monkeypatch)
    weak = pb.ALL_WORK - {"CB-F1-larder-bughunt", "MH06-fermat-mod-prime"}
    st = _scored(env, {"a": pb._rows() + _hard_rows(),
                       "b": pb._rows(fail_cases={"TZ06-three-parallel-one-turn"}) + _hard_rows(),
                       "c": pb._rows(fail_cases=weak) + _hard_rows(bad={"HC1-x", "HC2-y"})})
    for l in "abc":
        assert st[l]["rank_lo"] <= st[l]["rank_hi"]
        assert set(st[l]) >= {"ranked", "status", "tie_group", "bench_min", "date_first", "date_last"}
        assert set(st[l]["bench_min"]) >= {"main", "hard"}
        assert st[l]["date_first"] == "2026-10-28"
    assert st["c"]["rank_lo"] == 3
    assert "a" in st["c"]["headline"]["separated_from"]
    assert set(report.LAST_SEPARATION["labels"]) == {"a", "b", "c"}
    assert all("separated" in p for p in report.LAST_SEPARATION["pairs"])
    v = st["a"]["variance"]
    assert set(v) >= {"case_sampling", "generation", "judge_sample"}
    assert v["generation"]["basis"] == "observed repeats"   # the fixture has 2 repeats


@pytest.mark.archive_default
def test_report_json_carries_separation(tmp_path, monkeypatch):
    env = _env(tmp_path, monkeypatch)
    pb._write(env, {"a": pb._rows() + _hard_rows(), "b": pb._rows() + _hard_rows(bad={"HC1-x"})})
    report.generate(None, write=True, cfg=env.cfg)
    d = json.loads((env.results / "report.json").read_text(encoding="utf-8"))
    assert d["separation"]["pairs"] and d["models"]["a"]["status"] == "ranked"
