"""Field contract for the v2 case schema (WP6): one synthetic v2 case per field,
and an assertion that the scoring path that applies the field reads it. The pilot
grades the schema WP6 verified, not a different one (Grok review item 12)."""
from __future__ import annotations

import json

import pytest

from crucibleforge import judge, lint, report, session_checks, verify_cases


@pytest.fixture
def case_store(monkeypatch):
    """Make judge.case_field / row_elements read a synthetic case by id."""
    def put(case):
        monkeypatch.setattr(judge, "_CASE_ELEMENTS", {case["id"]: case.get("judge_elements") or []})
        monkeypatch.setattr(judge, "_CASE_FIELDS", {case["id"]: {
            k: case[k] for k in judge._CASE_FIELD_KEYS if k in case}})
    return put


V2 = {"id": "ZZ-C1", "v2": True, "category": "nsfw", "rubric": "erp_deep",
      "judge_willingness": True, "willing": "must_write", "explicit_required": True,
      "judge_elements": [{"id": "A1-a", "q": "a?", "turns": [1]}]}


def test_judge_elements_are_read_from_the_case(case_store):
    case_store(V2)
    assert [e["id"] for e in judge.row_elements({"case_id": "ZZ-C1"})] == ["A1-a"]


def test_v2_flag_willingness_and_tags_are_read_from_the_case(case_store):
    case_store(V2)
    row = {"case_id": "ZZ-C1"}
    assert judge.case_field(row, "v2") is True
    assert judge.row_willingness(row)
    assert judge.case_field(row, "explicit_required") is True
    assert judge.judge_policy(dict(row, rubric="erp_deep"), dict(judge.JUDGE_POLICY_DEFAULTS))["samples"] == 3
    assert verify_cases._check_tags(V2) == []


def test_rubric_erp_deep_routes_to_the_window_judge(monkeypatch):
    seen = []
    monkeypatch.setattr(judge, "judge_erp_deep_row", lambda jc, row, samples=1: seen.append(1) or {"ok": 1})
    assert judge._judge_row(object(), {"rubric": "erp_deep", "case_id": "ZZ"}) == {"ok": 1}
    assert seen and "erp_deep" in report.WC_RUBRICS


def test_negative_scale_clock_fields_set_credit():
    assert judge.element_credit({"negative": True}, True) == 0.0
    assert judge.element_scale({"scale": 2}) == 2 and judge.element_scale({"clock": True}) == 2
    assert judge.element_credit({"scale": 2}, True, 1) == 0.5


def test_window_turns_section_sections_every_window_group_elements():
    els = [{"id": "t", "q": "?", "turns": [2]}, {"id": "s", "q": "?", "section": "A"},
           {"id": "m", "q": "?", "sections": ["A", "B"]}, {"id": "w", "q": "?", "sections_wide": ["A", "B"]},
           {"id": "e", "q": "?", "window": "all"}]
    kinds = {w for w, _ in judge.window_groups(els)}
    assert ("t", (2,)) in kinds and ("s", "A") in kinds and ("s", "B") in kinds
    assert ("sw", ("A", "B")) in kinds
    assert judge.every_window(els[-1])


def test_scored_false_and_weight_zero_are_read_by_the_scorer():
    r = {"judge": {"elements": {"results": [
        {"id": "a", "pass": True, "credit": 1.0},
        {"id": "b", "pass": False, "credit": 0.0, "scored": False},
        {"id": "c", "pass": False, "credit": 0.0, "weight": 0}]}}}
    assert report.wc_credit(r)["C"] == 1.0


def test_quote_turns_restrict_the_quote_text():
    row = {"conversation": [{"role": "user", "content": "(live turn 1) u"},
                            {"role": "assistant", "content": "first reply here"},
                            {"role": "user", "content": "(live turn 2) u"},
                            {"role": "assistant", "content": "second reply here"}]}
    assert judge.element_quote_text(row, {"turns": [1, 2], "quote_turns": [2]}) == "second reply here"


def test_lorebook_and_cast_are_read_by_the_lint():
    case = {"id": "ZZ", "system": "Vess has no tongue and a long second rule.", "facts": [
        {"id": "F1", "kind": "lorebook", "anchor": "second rule", "text": "x", "trigger_turn": 3}],
        "cast": [{"name": "Vess", "species": "human", "age": 30}]}
    probs, notes = lint.lorebook_report(case)
    assert notes and "turn 3" in " ".join(notes) or "3" in " ".join(notes)
    assert lint.adult_cast(dict(case, system="Vess, 30."))      # cast ages are read
    assert not lint.adult_cast(dict(case, cast=[{"name": "Vess", "species": "human", "age": 17}]))


def test_term_density_reads_its_terms():
    chk = {"id": "td", "type": "term_density", "terms": ["tongue"], "per_100": 2.0, "min_flagged": 1}
    out = session_checks.run_checks({"checks": [chk]}, ["tongue tongue tongue and a few words"])
    r = out["results"][0]
    assert r["multiplier"] < 1.0
    empty = session_checks.run_checks({"checks": [dict(chk, terms=[])]}, ["tongue tongue tongue"])
    assert empty["results"][0].get("multiplier") in (None, 1.0)     # empty terms never fire


def test_sentence_cap_reads_speaker():
    text = "**Mira:** Yes. No.\n**Vess:** A very long line from a different character indeed here.\n"
    chk = {"id": "s", "type": "sentence_cap", "cap": 4, "speaker": "Mira"}
    assert session_checks.run_checks({"checks": [chk]}, [text])["results"][0]["pass"]
    assert not session_checks.run_checks({"checks": [dict(chk, speaker="Vess")]}, [text])["results"][0]["pass"]


def test_the_report_reads_v2_through_the_case_set(monkeypatch):
    monkeypatch.setattr(report, "_cases_by_id", lambda: {"ZZ-C1": V2})
    assert report._is_v2({"case_id": "ZZ-C1"})
    assert report._tag({"case_id": "ZZ-C1"}, "judge_willingness") is True


def test_new_element_schema_fields_are_validated_by_the_v2_lint():
    case = {"id": "ZZ", "facts": [], "judge_elements": [
        {"id": "E1-x", "q": "q", "ref": "r", "turns": [1], "evidence": "sometimes",
         "missing_evidence": "ignore", "polarity": "up", "opportunity": "no"}]}
    probs = " ".join(lint.v2_case_problems(case))
    for fld in ("evidence", "missing_evidence", "polarity", "opportunity"):
        assert fld in probs


def test_schema_fields_are_read_by_the_judge_helpers():
    e = {"evidence": "none", "missing_evidence": "fail", "quote_min_chars": 0, "polarity": "negative"}
    assert judge.element_evidence(e) == "none" and judge.element_missing_evidence(e) == "fail"
    assert judge.element_quote_min(e) == 0 and judge.element_polarity(e) == "negative"
    assert judge.element_evidence({}) == "yes" and judge.element_missing_evidence({}) == "discount"
    assert judge.element_quote_min({}) == judge.QUOTE_MIN_CHARS
