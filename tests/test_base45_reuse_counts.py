"""A5: the reusable stored rows per Base case are exactly what A0b measured, after the grading edits.

Reads the live results store read-only (`~/Projects/crucibleforge/results`, never written); skipped
when it is absent or its stored-row totals have moved since A0b's count (then the numbers below are
stale, not wrong). CC4 / NC2 / WL2 rows are reusable AND marked "re-judge required"; CL3 / TZ16 are
reusable with nothing to re-judge.
"""
from pathlib import Path

import pytest

RES = Path.home() / "Projects" / "crucibleforge" / "results"
BASE = ["CL3-paragraph-wrap", "TZ16-pickup-dst-delay", "CC4-second-pitch", "WL2-lighthouse", "NC2-after-hours"]
STORED = {"CL3-paragraph-wrap": 136, "TZ16-pickup-dst-delay": 82, "CC4-second-pitch": 488,
          "WL2-lighthouse": 91, "NC2-after-hours": 633}
REUSABLE = {"CL3-paragraph-wrap": 63, "TZ16-pickup-dst-delay": 82, "CC4-second-pitch": 412,
            "WL2-lighthouse": 80, "NC2-after-hours": 628}
REJUDGE = {"CL3-paragraph-wrap": 0, "TZ16-pickup-dst-delay": 0, "CC4-second-pitch": 412,
           "WL2-lighthouse": 80, "NC2-after-hours": 628}


@pytest.fixture(scope="module")
def counts():
    if not RES.is_dir():
        pytest.skip("no results store")
    from crucibleforge import config, profiles, report
    mp = pytest.MonkeyPatch()
    mp.setattr(profiles, "DEFAULT_PROFILE", "bench45")
    mp.setattr(config, "RESULTS_DIR", RES)
    try:
        cfg = config.load_config(config.EXAMPLE_CONFIG_PATH)
        prof = profiles.load_profile("bench45", cfg)
        prof = {**prof, "reuse": {"from": ["bench"], "ids": BASE}}
        mp.setattr(config, "RESULTS_DIR", RES)
        tot = dict.fromkeys(BASE, 0)
        ok = dict.fromkeys(BASE, 0)
        rj = dict.fromkeys(BASE, 0)
        for f in sorted(RES.glob("transcripts_*.jsonl")):
            for r in config.load_transcripts(f.stem[len("transcripts_"):]):
                c = r.get("case_id")
                if c in tot and r.get("profile") == "bench":
                    tot[c] += 1
                    good, rejudge = report.reuse_status(r, prof)
                    ok[c] += bool(good)
                    rj[c] += bool(good and rejudge)
        yield tot, ok, rj
    finally:
        mp.undo()


def test_reusable_row_counts_unchanged(counts):
    tot, ok, _rj = counts
    if tot != STORED:
        pytest.skip(f"the store moved since A0b: stored rows {tot}")
    assert ok == REUSABLE


def test_rejudge_flags_follow_the_grading_edits(counts):
    tot, _ok, rj = counts
    if tot != STORED:
        pytest.skip(f"the store moved since A0b: stored rows {tot}")
    assert rj == REJUDGE
