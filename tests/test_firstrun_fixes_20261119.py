"""Fixes from the first fresh reference run of 4.5 (2026-11-19), one block per finding."""
from __future__ import annotations

from crucibleforge import report


# F7: a multi-step tool row keeps its conversation in ``tool_conversation``; its final text and ``tool_calls``
# fields are empty, and it was counted as an "empty reply (scored 0)" although its steps were graded
def test_a_graded_tool_conversation_is_not_an_empty_reply():
    row = {"case_id": "TZ20", "grade": "pass", "response": "", "tool_calls": [], "finish_reason": "tool_calls",
           "tool_conversation": '[{"role": "system", "content": "x"}]'}
    assert report._row_empty_reply(row) is False
    plain = {"response": "", "tool_calls": [], "finish_reason": "stop"}
    assert report._row_empty_reply(plain) is True


# F6: the adult page is dropped for a model that was not asked for it; a plain run without those units is still
# a whole run (the early-stop check was skipped as "targeted rerun: 54 of 167")
def test_a_run_without_the_unplanned_adult_units_is_a_whole_run():
    from crucibleforge import earlystop
    cfg = {"_profile_case_ids": ["a", "b", "c", "adult1", "adult2"]}
    assert earlystop.targeted_reason(cfg, ["a", "b", "c"]) is not None            # nothing said: looks targeted
    assert earlystop.targeted_reason(cfg, ["a", "b", "c"], ["adult1", "adult2"]) is None
    assert earlystop.targeted_reason(cfg, ["a", "b"], ["adult1", "adult2"]) == "targeted rerun: 2 of 3 profile cases"


# F3: a page lists only what it covers (13 empty adult rows under a standard page)
def _prof(monkeypatch):
    from crucibleforge import profiles
    monkeypatch.setenv("CF_COMPOSITION", "standard")
    profiles.set_active("bench45")
    p = profiles.load_profile("bench45")
    profiles.activate_cases_root(p)
    return p


def _part(**kw):
    base = {"ended": 0, "units": 16, "elements": 0, "turns": 0, "tokens": {"prompt": 0, "thinking": 0, "completion": 0}}
    return {**base, **kw}


def test_empty_adult_parts_are_not_listed_on_the_standard_page(monkeypatch):
    prof = _prof(monkeypatch)
    cs = {"work/c1_implied": _part(ended=6, units=6, elements=147, turns=6),
          "nsfw/n1_scene": _part(), "nsfw/u3_know": _part()}
    assert set(report.page_chain_stats45(cs, prof)) == {"work/c1_implied"}


def test_an_adult_part_with_any_unit_stays_listed(monkeypatch):
    prof = _prof(monkeypatch)
    cs = {"nsfw/n1_scene": _part(turns=3), "nsfw/n2_peak": _part(ended=2), "nsfw/n3_way": _part()}
    assert set(report.page_chain_stats45(cs, prof)) == {"nsfw/n1_scene", "nsfw/n2_peak"}


# F2: one `all` invocation is one run for timing: the ladder-pack rounds run under their own bench_run_id inside
# the invocation's window; the board prints the standard minutes and the pack minutes
def _meta():
    return {"bench_run_id": "r1", "started": "2026-11-18T15:28:37+00:00", "timing": [
        {"phase": "generation", "s": 6000.0}, {"phase": "judge", "s": 600.0, "at": "2026-11-18T17:03:18+00:00"},
        {"phase": "ladder round 1 generation", "s": 1200.0, "at": "2026-11-18T17:31:53+00:00"},
        {"phase": "ladder round 1 judge", "s": 0.0, "at": "2026-11-18T17:31:53+00:00"},
        {"phase": "report", "s": 60.0, "at": "2026-11-18T17:33:39+00:00"}]}


def test_pack_rounds_of_the_same_invocation_are_not_a_second_run():
    rows = [{"bench_run_id": "r1", "ts": "2026-11-18T16:00:00+00:00"},
            {"bench_run_id": "p2", "ts": "2026-11-18T17:20:00+00:00"}]
    assert report.timing_minutes45(_meta(), rows) == 111.0          # (6000 + 600 + 60) s: the standard run only
    assert report.timing_pack_minutes45(_meta()) == 20.0


def test_a_row_of_another_invocation_still_blanks_the_minutes():
    rows = [{"bench_run_id": "r1", "ts": "2026-11-18T16:00:00+00:00"},
            {"bench_run_id": "old", "ts": "2026-11-17T16:00:00+00:00"}]
    assert report.timing_minutes45(_meta(), rows) is None


def test_the_minutes_cell_says_both_numbers():
    assert report.minutes45_cell({"run_minutes45": 110.0, "run_minutes_packs45": 20.0, "run_ids45": 2}) == \
        "110.0 (+ 20.0 ladder packs)"
    assert report.minutes45_cell({"run_minutes45": 110.0, "run_ids45": 1}) == "110.0"
    assert report.minutes45_cell({"run_minutes45": None, "run_ids45": 2}) == "—"
