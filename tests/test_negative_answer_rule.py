"""L5 2026-11-08: negative elements must carry an explicit answer rule in the
judge prompt (both spellings), or judges answer them in the good polarity."""
from crucibleforge import judge


def test_negative_flag_gets_answer_rule():
    line = judge._element_line({"id": "E1", "q": "Does the narrator break character?", "negative": True})
    assert "NEGATIVE test" in line and '"pass": true' in line


def test_polarity_negative_gets_answer_rule():
    line = judge._element_line({"id": "E1", "q": "Is there a disclaimer?", "polarity": "negative"})
    assert "NEGATIVE test" in line


def test_scale2_negative_explains_score():
    line = judge._element_line({"id": "E1", "q": "Drift?", "negative": True, "scale": 2})
    assert "how strongly the failure is present" in line


def test_positive_unchanged():
    line = judge._element_line({"id": "E1", "q": "Is the lamp named?"})
    assert "NEGATIVE" not in line
