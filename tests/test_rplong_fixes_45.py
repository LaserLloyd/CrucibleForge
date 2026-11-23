"""The two recorded false fails on the co-write scenes (single line break as a paragraph break), as bench45 report-time
overrides; the CC3 two-sentence clash (W2) is already unscored in the same table. Both ways: a correct reply passes,
a wrong one still fails; and no stamp moves."""
import pytest
from crucibleforge import profiles, report, rpdeep, session_checks as SC, version


@pytest.fixture
def b45(monkeypatch):
    monkeypatch.setenv("CF_COMPOSITION", "rc4-main")
    profiles.set_active("bench45")
    p = profiles.load_profile("bench45")
    profiles.activate_cases_root(p)
    return p


def _check(case_id, check_id):
    case = report._cases_by_id()[case_id]
    ov = profiles.load_profile("bench45")["rpdeep_overrides"]
    laid = rpdeep.apply_overrides(case, ov[case_id])
    old = next(c for c in case["checks"] if c["id"] == check_id)
    new = next(c for c in laid["checks"] if c["id"] == check_id)
    return old, new


def _ok(chk, text, turn):
    return SC.check_require_regex([(turn, text)], chk)[0]


W20 = "one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen twenty."


def test_z5_single_newline_last_paragraph(b45):
    old, new = _check("WR1-ninth-string", "Z5-last-paragraph-20-words")
    reply = "First paragraph here.\n" + W20
    assert not _ok(old, reply, 3) and _ok(new, reply, 3)
    assert _ok(old, "First.\n\n" + W20, 3) and _ok(new, "First.\n\n" + W20, 3)       # old pass stays a pass
    short = "First.\nonly five words in all."
    assert not _ok(new, short, 3)
    assert not _ok(new, "First.\n" + W20.replace("twenty.", "twenty twenty-one."), 3)


def test_p2_three_paragraphs_single_newline(b45):
    old, new = _check("WR2-red-thread-key", "P2-three-paragraphs")
    three = "One.\nTwo.\nThree."
    assert not _ok(old, three, 2) and _ok(new, three, 2)
    blank = "One.\n\nTwo.\n\nThree."
    assert _ok(old, blank, 2) and _ok(new, blank, 2)
    assert not _ok(new, "One.\nTwo.", 2) and not _ok(new, "One.\nTwo.\nThree.\nFour.", 2)
    assert not _ok(new, "One.\n\nTwo.", 2)


def test_cc3_two_sentence_cap_is_unscored_on_45(b45):
    old, new = _check("CC3-tea-hut", "W2-short-on-request")
    assert new.get("scored") is False


def test_no_stamp_moves(b45):
    """The fixes live in the profile's override table: no case file that existed at the fork point, no grading
    module, so no hash changes. Pack lanes merged after the fork may ADD case files (a new set carries its own
    stamp); modifying or deleting a case file that existed at the fork, or moving an existing family's grader
    stamp, would move stored rows and fails here."""
    import json
    import subprocess
    from pathlib import Path
    from crucibleforge import gen, grader_stamp as G
    root = Path(__file__).resolve().parents[1]
    base = "realign45-w0"
    if subprocess.run(["git", "rev-parse", "--verify", "-q", base], cwd=root, capture_output=True).returncode:
        pytest.skip(f"fork point {base} not in this clone")
    diff = subprocess.run(["git", "diff", "--no-renames", "--name-status", base, "HEAD"], cwd=root,
                          capture_output=True, text=True, check=True).stdout.splitlines()
    rows = [ln.split("\t", 1) for ln in diff if ln.strip()]
    assert not [f for st, f in rows if f.startswith("cases/") and st != "A"]
    assert not [f for _st, f in rows if f in {"crucibleforge/graders.py", "crucibleforge/codebank.py",
                "crucibleforge/adult45.py", "crucibleforge/rpsession.py", "crucibleforge/session_checks.py",
                "crucibleforge/rpladder.py", "crucibleforge/judge.py", "crucibleforge/rpdeep.py"}]
    pinned = json.loads((root / "tests/fixtures/grader_stamps_8f946f9.json").read_text(encoding="utf-8"))
    gen.discover()
    G.clear()
    assert {fam: G.stamp(fam) for fam in pinned} == pinned
