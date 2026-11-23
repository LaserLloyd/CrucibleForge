"""Guard rule (spec 4.1): the classing lane edits nothing inside any grader_stamp closure."""
import hashlib
import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GUARDED = ["crucibleforge/graders.py", "crucibleforge/codebank.py", "crucibleforge/adult45.py",
           "crucibleforge/gen", "crucibleforge/rpsession.py", "crucibleforge/session_checks.py",
           "crucibleforge/explicit_gate.py", "crucibleforge/rpladder.py", "crucibleforge/judge.py",
           "crucibleforge/tooltask.py", "crucibleforge/grader_stamp.py",
           "calibration/nsfw-classing-marks-45.json",
           "calibration/nsfw-classing-prompt-45.txt", "calibration/nsfw-classing-constructed-45.json"]


PINNED = ROOT / "tests" / "fixtures" / "marks_index_45_pinned.json"


def _pinned():
    # no skip: a clone without the fixture fails here, it never passes by default
    return json.loads(PINNED.read_text(encoding="utf-8"))


def test_guarded_files_unchanged_since_w0():
    pin = _pinned()["guarded"]
    files = sorted(p for g in GUARDED for p in pin if p == g or p.startswith(g + "/"))
    unpinned = {g for g in GUARDED if (ROOT / g).exists() and not any(p == g or p.startswith(g + "/") for p in pin)}
    assert unpinned == set(), f"a guarded path has no pin: {unpinned}"
    # integration 2026-11-20: two reviewed speed changes of the pilot-2 lane (f3aed5e list-verdict memo, fee61a2 opt-in
    # render memo; report.json byte-identical, every grader stamp unchanged against 8f946f9). Pinned to their NEW bytes
    # here: any further edit still fails this test.
    reviewed = {"crucibleforge/explicit_gate.py": "b9a34033788a3899ac7e64ca943128fc32d5f232cc729197abbde5640d85bb2d",
                "crucibleforge/gen/__init__.py": "5a5bcdd068ccf79cf98e833c87172f580a1395e3e5f6afd66f3495252b3560ae"}
    changed = [p for p in files if not (ROOT / p).is_file()
               or hashlib.sha256((ROOT / p).read_bytes()).hexdigest() != reviewed.get(p, pin[p])]
    assert changed == [], f"guarded files changed since w0: {changed}"
    tracked = subprocess.run(["git", "-C", str(ROOT), "ls-files", "--", "crucibleforge/gen"], capture_output=True,
                             text=True).stdout.split()
    # the GAP / Fixes families and the NSFW+ pack family (2026-11-20) are new, named, and touch no classing code
    gap = {f"crucibleforge/gen/{n}.py" for n in ("patchgap", "toolgap", "everygap", "patchtough",
                                                 "nsfwpack45", "nsfwpack45_pins")}
    assert not tracked or set(tracked) <= set(pin) | gap, f"new files under a guarded dir: {sorted(set(tracked) - set(pin) - gap)}"


def _index_rows():
    rx = re.compile(r"^\| `(calibration/[^`]+)` \| `([0-9a-f]{64})` \|(.*)$")
    return [m.groups() for ln in (ROOT / "calibration" / "MARKS-INDEX-45.md").read_text(encoding="utf-8").splitlines()
            if (m := rx.match(ln))]


def test_marks_index_only_grows_by_addendum_rows():
    """The index is append-only: every pinned row (path + sha) is still there in order; a later row is a dated
    addendum written before any launch row was read."""
    pin = [tuple(r) for r in _pinned()["index_rows"]]
    now = _index_rows()
    assert [(p, s) for p, s, _ in now[:len(pin)]] == pin, "a pinned row of the marks index was edited or removed"
    for p, s, purpose in now[len(pin):]:
        assert "before any launch row was read" in purpose, (p, purpose)


def test_every_addendum_file_on_disk_is_indexed():
    from crucibleforge import marksindex45 as MI
    for p in sorted((ROOT / "calibration").glob("*addendum*")):
        MI.require(p)


def test_classing_module_outside_every_stamp_closure():
    from crucibleforge import grader_stamp
    src = (ROOT / "crucibleforge" / "classing45.py").read_text(encoding="utf-8")
    assert "grader_stamp" not in src
    for f in ("adult45", "rpsession", "session_checks", "graders", "codebank"):
        text = (ROOT / "crucibleforge" / f"{f}.py").read_text(encoding="utf-8") if (ROOT / "crucibleforge" / f"{f}.py").exists() else ""
        assert "classing45" not in text
    assert grader_stamp.stamp("adult45")     # still computes
