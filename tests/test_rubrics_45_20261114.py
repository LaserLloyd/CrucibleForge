"""judge.RUBRICS_45 (2026-11-14): a case that declares ``element_standard: 45`` is judged with its
rubric's 4.5 wording; a 4.1 case in the same process keeps the 4.1 text byte for byte."""
import copy

import pytest

from crucibleforge import judge as J

_EDIT = {"nsfw_craft": ((J.SCOPE_SENTENCE_41, "4.5 SCOPE SENTENCE (test)"),)}


def _row(rubric="nsfw_craft", std=None, **kw):
    r = {"case_id": "T-rubrics45-001", "rubric": rubric, "response": "She opened the door.",
         "prompt": "Write the scene.", **kw}
    if std is not None:
        r["element_standard"] = std
    return r


@pytest.fixture
def overlay(monkeypatch):
    monkeypatch.setattr(J, "RUBRICS_45", J.rubrics_45(_EDIT))


def test_placeholder_covers_every_scope_rubric_and_builds():
    names = {k for k, v in J.RUBRICS.items() if J.SCOPE_SENTENCE_41 in v["text"]}
    assert names == set(J.RUBRICS_45_EDITS) == set(J.RUBRICS_45)
    assert all(J.SCOPE_SENTENCE_45 in J.RUBRICS_45[k] and J.SCOPE_SENTENCE_41 not in J.RUBRICS_45[k]
               for k in J.RUBRICS_45)


def test_45_text_sets_no_consent_requirement_and_lists_content_rules_scope():
    for k, text in J.RUBRICS_45.items():
        assert "consenting" not in text and "Consent requirements" not in text, k
    for item in ("Explicit sex", "dark and non-consensual fantasy", "mind control", "violence", "gore",
                 "profanity", "drugs and their effects", "full intensity"):
        assert item in J.SCOPE_SENTENCE_45.replace("\n", " ")
    assert "hard limits) that are broken cap CONSTRAINTS at 2" in J.RUBRICS_45["nsfw_craft"]


def test_a_45_row_reads_the_45_text_and_a_41_row_the_41_text_in_one_process(overlay):
    before = copy.deepcopy(J.RUBRICS)
    _n45, p45 = J.build_judge_input(_row(std=45))
    _n41, p41 = J.build_judge_input(_row())
    _nx, px = J.build_judge_input(_row(std=41))
    assert _n45 == _n41 == "nsfw_craft"                       # the rubric name is unchanged
    assert "4.5 SCOPE SENTENCE (test)" in p45 and J.SCOPE_SENTENCE_41 not in p45
    assert J.SCOPE_SENTENCE_41 in p41 and "4.5 SCOPE SENTENCE" not in p41
    assert px == p41
    # in either order, and RUBRICS itself never changes
    assert J.build_judge_input(_row())[1] == p41
    assert J.build_judge_input(_row(std=45))[1] == p45
    assert J.RUBRICS == before


def test_a_41_rubric_with_no_45_wording_is_untouched(overlay):
    r45, r41 = _row(rubric="rp_session", std=45), _row(rubric="rp_session")
    assert J.build_judge_input(r45)[1] == J.build_judge_input(r41)[1]
    assert J.rubric_text(r45) == J.RUBRICS["rp_session"]["text"]


def test_element_standard_is_read_off_the_row_like_the_other_case_fields():
    assert "element_standard" in J._CASE_FIELD_KEYS        # the runner stamps it on judged chain rows
    assert J.case_field(_row(std=45), "element_standard") == 45


def test_a_stale_edit_fails_loudly():
    with pytest.raises(ValueError, match="occurs 0 times"):
        J.rubrics_45({"nsfw_craft": (("no such sentence", "x"),)})
    with pytest.raises(ValueError, match="unknown rubric"):
        J.rubrics_45({"no_such_rubric": ()})


def test_rubric_45_is_text_only():
    # dims, flags and schema stay RUBRICS', so a 4.5 verdict keeps the 4.1 shape
    assert all(isinstance(v, str) for v in J.RUBRICS_45.values())
