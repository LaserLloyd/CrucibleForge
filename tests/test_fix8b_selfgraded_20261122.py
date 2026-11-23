"""The judge's own row (the board's reference model) says "graded by itself" in the board's Notes cell and in the
NSFW+ section, not only in the per-model failure notes."""
from __future__ import annotations

from crucibleforge import report

JID = "vendor/Judge-27B-GGUF/Judge-27B-Q8_0"


def _row(chat=True, adult=True):
    return {"meta": {"model_id": JID},
            "coverage": {"notes": [], "self_judged": ["CC3-tea-hut", "RD1-marisol"] if chat else []},
            "nsfw_plus": {"self_graded": adult, "judge45": {"model": JID, "class_missing": 0, "flags_missing": 0}}}


def test_notes_cell_says_graded_by_itself():
    cell = report.notes_cell(_row())
    assert "graded by itself" in cell
    assert "Chat" in cell and "NSFW+" in cell


def test_notes_cell_chat_only_and_adult_only():
    c = report.notes_cell(_row(adult=False))
    assert "graded by itself" in c and "NSFW+" not in c
    a = report.notes_cell(_row(chat=False))
    assert "graded by itself" in a and "Chat" not in a


def test_other_models_carry_no_note():
    s = {"meta": {"model_id": "other/model"}, "coverage": {"notes": [], "self_judged": []},
         "nsfw_plus": {"self_graded": False, "judge45": {"model": JID}}}
    assert "itself" not in report.notes_cell(s)
    assert "itself" not in (report.np_judge_line(s) or "")


def test_nsfw_plus_judge_line_says_graded_by_itself():
    assert "graded by itself" in report.np_judge_line(_row())
