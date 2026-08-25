"""Tests for judge JSON parsing and rubric handling — no network."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from crucibleforge import judge


def test_parse_nsfw_verdict_valid():
    raw = '{"prose": 8, "emotion": 7, "erotic": 9, "explicitness": 10, "refused": false, "sanitized": false, "note": "good"}'
    v = judge.parse_verdict(raw, "nsfw")
    assert v["prose"] == 8 and v["explicitness"] == 10
    assert v["refused"] is False and v["sanitized"] is False


def test_parse_clamps_out_of_range():
    raw = '{"prose": 15, "emotion": -3, "erotic": 9, "explicitness": 10, "refused": false, "sanitized": false}'
    v = judge.parse_verdict(raw, "nsfw")
    assert v["prose"] == 10 and v["emotion"] == 0


def test_parse_missing_dim_returns_none():
    raw = '{"prose": 8, "emotion": 7, "refused": false, "sanitized": false}'
    assert judge.parse_verdict(raw, "nsfw") is None


def test_parse_missing_flag_returns_none():
    raw = '{"prose": 8, "emotion": 7, "erotic": 9, "explicitness": 10}'
    assert judge.parse_verdict(raw, "nsfw") is None


def test_parse_embedded_json():
    raw = 'Here is my verdict:\n{"prose": 5, "emotion": 5, "erotic": 5, "explicitness": 5, "refused": false, "sanitized": false}\nDone.'
    v = judge.parse_verdict(raw, "nsfw")
    assert v is not None and v["prose"] == 5


def test_parse_garbage_returns_none():
    assert judge.parse_verdict("I think it was pretty good, maybe 8/10", "nsfw") is None


def test_salvage_truncated_note():
    # Thinking judge ran out of tokens mid-note; all scored fields precede it.
    raw = ('{"prose": 6, "emotion": 7, "erotic": 8, "explicitness": 9, '
           '"refused": false, "sanitized": false, "note": "The scene was well '
           'written and the tension built naturally over several paragraphs before')
    v = judge.parse_verdict(raw, "nsfw")
    assert v is not None
    assert v["prose"] == 6 and v["explicitness"] == 9
    assert v["refused"] is False
    assert "salvaged" in v["note"]


def test_salvage_fails_if_a_field_missing():
    # Truncated before "explicitness" — cannot safely salvage.
    raw = '{"prose": 6, "emotion": 7, "erotic": 8, "refus'
    assert judge.parse_verdict(raw, "nsfw") is None


def test_parse_rp_single():
    raw = '{"prose": 7, "character": 8, "dialogue": 6, "atmosphere": 9, "emotion": 7, "agency": 10, "refused": false, "note": ""}'
    v = judge.parse_verdict(raw, "rp_single")
    assert v["agency"] == 10 and v["refused"] is False


def test_parse_rp_multi_flags():
    raw = ('{"prose": 6, "character": 7, "dialogue": 6, "emotion": 7, "agency": 8,'
           ' "consistency": 9, "refused": false, "recalled_detail": true, "note": ""}')
    v = judge.parse_verdict(raw, "rp_multi")
    assert v["recalled_detail"] is True and v["consistency"] == 9


def test_parse_safety_verdict():
    raw = '{"refused": true, "provided_harmful_content": false, "note": "declined"}'
    v = judge.parse_verdict(raw, "safety")
    assert v["refused"] is True and v["provided_harmful_content"] is False


def test_build_judge_input_single():
    row = {"rubric": "nsfw", "prompt": "write X", "response": "a scene"}
    name, prompt = judge.build_judge_input(row)
    assert name == "nsfw"
    assert "a scene" in prompt and "write X" in prompt


def test_build_judge_input_multi():
    row = {"rubric": "rp_multi",
           "conversation": [{"role": "user", "content": "hello"},
                            {"role": "assistant", "content": "hi there"}]}
    name, prompt = judge.build_judge_input(row)
    assert name == "rp_multi"
    assert "hi there" in prompt and "MULTI-TURN" in prompt


def test_all_rubric_schemas_wellformed():
    for name, spec in judge.RUBRICS.items():
        schema = spec["schema"]["json_schema"]["schema"]
        required = set(schema["required"])
        assert set(spec["dims"]) | set(spec["flags"]) <= required
        assert "note" in schema["properties"]


# ------------------------------------ audit-fix regression tests (judge)
def test_last_json_object_wins():
    # A stray early brace from deliberation must not beat the final verdict.
    raw = ('Let me think {maybe} ... draft {"prose":2}\n'
           'Final: {"prose": 9, "emotion": 8, "erotic": 8, "explicitness": 9, '
           '"refused": false, "sanitized": false, "note": "great"}')
    v = judge.parse_verdict(raw, "nsfw")
    assert v["prose"] == 9 and v["explicitness"] == 9


def test_salvage_takes_last_field_occurrence():
    # Deliberation mentions prose=3, the (truncated) real verdict says prose=8.
    raw = ('reasoning: I might give prose=3 but actually the writing is strong. '
           '{"prose": 8, "emotion": 7, "erotic": 8, "explicitness": 9, '
           '"refused": false, "sanitized": false, "note": "the scene built slowly and')
    v = judge.parse_verdict(raw, "nsfw")
    assert v is not None
    assert v["prose"] == 8  # last occurrence, not the deliberation's 3
    assert "salvaged" in v["note"]


def test_steer_rubric_parses():
    v = judge.parse_verdict('{"obeyed": true, "note": "stayed SFW"}', "steer")
    assert v["obeyed"] is True


def test_build_judge_input_single_includes_persona():
    row = {"rubric": "rp_single", "system": "You are Bram the tavern keeper.",
           "prompt": "hello", "response": "Evening, friend."}
    _, prompt = judge.build_judge_input(row)
    assert "Bram the tavern keeper" in prompt


def test_has_content_empty_multiturn():
    row = {"rubric": "rp_multi", "conversation": [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": ""}]}
    assert judge._has_content(row) is False
    row2 = {"rubric": "rp_multi", "conversation": [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "well hello"}]}
    assert judge._has_content(row2) is True
