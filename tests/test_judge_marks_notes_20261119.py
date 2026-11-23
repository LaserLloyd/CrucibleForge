"""H1/H2: sessions count under marks v2 with the v1 failure printed beside it (CHANGELOG, SCORING, board note); a
part that fails its marks says why in numbers from judge_marks.json; the 4.5.1 draft marks exist and 4.5.0 never loads them."""
import hashlib
import json
from pathlib import Path

from crucibleforge import report as R

ROOT = Path(__file__).resolve().parent.parent
PROF = {"judge_agreement": "required",
        "scores": {"chat": {"parts": {"j_rpsession": {"label": "Role-play: sessions", "tag": "judged"},
                                      "j_rplong": {"label": "Role-play: long scenes", "tag": "judged"}}}}}


def _marks(monkeypatch, res):
    monkeypatch.setattr(R, "judge_marks_result", lambda: res)


def test_changelog_scoring_and_the_board_note_state_v2_and_the_v1_failure():
    for txt in ((ROOT / "CHANGELOG.md").read_text(encoding="utf-8"), (ROOT / "docs" / "SCORING.md").read_text(encoding="utf-8"),
                R.SESSIONS_MARKS_NOTE):
        assert "0.43" in txt and "0.60" in txt and "judge_marks.json" in txt
        assert "ladder placements" in txt and "pairwise" in txt


def test_the_board_note_gives_the_sessions_figures_and_the_long_scene_reason_in_numbers(monkeypatch):
    _marks(monkeypatch, {"parts": {"chat/j_rpsession": {"pass": True, "judge_term_share": 0.019},
                                   "chat/j_rplong": {"pass": False, "judge_term_share": 0.058,
                                                     "reason": "judge term 5.8% of the part > 5%"}}})
    notes = R.judged_part_notes(PROF)
    assert any("0.43 against a 0.60 mark" in n and "judge term 1.9% of the part, limit 5%" in n for n in notes)
    assert "Role-play: long scenes: pending: judge disagreement adds 5.8% of the part, limit 5%." in notes


def test_no_note_when_the_profile_does_not_require_agreement(monkeypatch):
    _marks(monkeypatch, {"parts": {}})
    assert R.judged_part_notes({"scores": PROF["scores"]}) == []


def test_the_draft_marks_are_hashed_in_the_notes_and_not_for_450():
    draft = ROOT / "calibration" / "judge-marks-45.1-draft.json"
    d = json.loads(draft.read_text(encoding="utf-8"))
    frozen = json.loads((ROOT / "calibration" / "judge-marks-45.json").read_text(encoding="utf-8"))
    assert "NOT used by 4.5.0" in d["_DRAFT"]
    assert d["primary"] == frozen["primary"]                                    # limits unchanged
    assert set(d["parts"]) == set(frozen["parts"])
    assert d["parts"]["chat/j_rplong"]["score"]["definition"] == "mean_of_two_readers"
    sha = hashlib.sha256(draft.read_bytes()).hexdigest()
    notes = (ROOT / "calibration" / "judge-marks-45-notes.md").read_text(encoding="utf-8")
    assert sha in notes and "written 2026-11-19, before any reader run it will be tested on" in notes.lower()


def test_only_the_hashed_loader_reads_the_draft():
    """4.5 (realigned): the long chat scenes load the promoted marks, by their indexed hash, in one module only;
    the frozen marks path of the 4.5.0 code is unchanged."""
    from crucibleforge import agree_marks
    assert "draft" not in str(agree_marks.MARKS)
    users = [f.name for f in list((ROOT / "crucibleforge").rglob("*.py")) + list((ROOT / "scripts").rglob("*.py"))
             if "judge-marks-45.1-draft" in f.read_text(encoding="utf-8", errors="replace")]
    assert users == ["rplong_marks.py"]
