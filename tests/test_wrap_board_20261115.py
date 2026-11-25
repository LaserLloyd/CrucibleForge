"""Wrap the board (lead 2026-11-15, D's renders on dff8ac3):

(a) a 4.1 render (``report --profile bench``) reads, word for word, what it read before the release flip
    (9596a19): the whole report.md and report.html, minus only the generated/rendered time. The flip
    moved SUITE_VERSION to 4.5.0 and the 4.1 header read "suite 4.5.0+..."; the suite label of a render
    follows the profile being rendered (the archive profile is suite 4.1.0). The site's version switch
    reads that stamp. The pre-flip text is tests/fixtures/board41_render/ (made on 9596a19 by
    render_board41 below; no golden was touched).
(b) on the shipping board a case part whose rows exist but whose verdicts all wait for the judge (an
    unstamped verdict under ``judge_stamp_required``) reads "waiting for the judge" / "pending the judge",
    never "not run": its generation rows are there.
(c) the shipping composition's status line is one plain line with no rc names."""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from crucibleforge import config, profiles, report

FIX = Path(__file__).parent / "fixtures" / "board41"
PRE = Path(__file__).parent / "fixtures" / "board41_render"
_TIME = re.compile(r"\b(Generated|rendered) \d{4}-\d\d-\d\d \d\d:\d\d\b")


def _untimed(text: str) -> str:
    return _TIME.sub(r"\1 <time>", text)


def render_board41(results: Path) -> tuple[str, str]:
    """(report.md, report.html) of the board41 rows rendered as ``report --profile bench`` does, untimed."""
    rows = json.loads((FIX / "rows.json").read_text(encoding="utf-8"))
    results.mkdir(parents=True, exist_ok=True)
    for label, rs in rows.items():
        (results / f"transcripts_{label}.jsonl").write_text(
            "".join(json.dumps(r) + "\n" for r in rs), encoding="utf-8")
    old_dir, old_active = config.RESULTS_DIR, profiles._ACTIVE[0]
    config.RESULTS_DIR = results
    profiles.set_active("bench")             # what `report --profile bench` sets at the command entry
    try:
        report.generate(None, write=True, cfg=None)
    finally:
        config.RESULTS_DIR = old_dir
        profiles.set_active(old_active)
    md = (results / "report.md").read_text(encoding="utf-8")
    html = (results / "report.html").read_text(encoding="utf-8")
    return _untimed(md), _untimed(html)


def test_a_4_1_render_reads_word_for_word_what_it_read_before_the_flip(tmp_path):
    md, html = render_board41(tmp_path / "results")
    assert "suite 4.1.0+" in md and "suite 4.5.0" not in md
    assert md == (PRE / "pre_flip.md").read_text(encoding="utf-8"), "4.1 report.md drifted"
    assert html == (PRE / "pre_flip.html").read_text(encoding="utf-8"), "4.1 report.html drifted"


def test_the_suite_label_follows_the_rendered_profile():
    from crucibleforge.version import SUITE_VERSION, suite_version
    assert suite_version(profiles.ARCHIVE_PROFILE) == "4.1.0"
    assert suite_version("bench45") == SUITE_VERSION == "4.5.1"
    assert report._current_revision({"_profile": "bench"}).startswith("4.1.0+")
    assert report._current_revision({"_profile": "bench45"}).startswith("4.5.1+")


def test_a_part_whose_rows_all_wait_for_the_judge_is_pending_the_judge_not_not_run():
    ids = {"RPL1", "RPL2"}
    jobs = {"RPL1": [{"case_id": "RPL1", "repeat": 1, "reason": "unjudged"}],
            "RPL2": [{"case_id": "RPL2", "repeat": 1, "reason": "unjudged"},
                     {"case_id": "RPL2", "repeat": 2, "reason": "judge_failed"}]}
    assert report._held_part_why(ids, set(ids), jobs) == "the judge"
    pend = [{"part": "rplong", "points": 3.0, "why": report._held_part_why(ids, set(ids), jobs)}]
    assert report.pending_points_text(pend) == " (3 pts pending the judge)"
    card = {"pending_points": {"chat": pend}}
    assert report.unranked_why(card, [{"key": "chat", "parts": [{"key": "rplong", "short": "RPL"}]}]) == \
        "not ranked: waiting for the judge (RPL)"
    # unchanged: no row at all is "not run"; errored jobs are a re-run
    assert report._held_part_why(ids, set(ids), {}) == "not run"
    err = {"RPL1": [{"case_id": "RPL1", "repeat": 1, "reason": "error"}]}
    # rows stored for some cases, none for others (D's 8B: 5 of 8 long scenes ran, all wait for the judge): the
    # rows that are missing hold the part, never "not run"; a part whose every job errored has rows too
    assert report._held_part_why(ids | {"RPL3"}, ids | {"RPL3"}, jobs) == "its missing rows"
    assert report._held_part_why({"RPL1"}, {"RPL1"}, err) == "a re-run of 1 errored job"
    assert report._held_part_why(ids, {"RPL1"}, err) == "a re-run of 1 errored job"


@pytest.mark.shipping_default
def test_the_shipping_status_is_one_plain_line_without_rc_names():
    prof = profiles.load_profile("bench45")
    status = report.board_status45(prof)
    assert status and "\n" not in status
    assert not re.search(r"\brc\d", status), status
    # the Class column stays hidden until REL words the release (class_hidden45 reads the prefix)
    assert report.class_hidden45(prof)
