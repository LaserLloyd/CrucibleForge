"""A cut reply is not counted on an ``ran_out: exclude`` cell (the owner's ruling, lead 2026-11-14 after D's
DeepSeek-R1-0528-8B rc2 run): two engine paths scored the cut reply as a 0.

1. Element-less chain units (TRACEVM, SESSION_STATE: one score per reply or per session) took the
   ``vals = [blk.score]`` fallback in chains.section_stats: a cut reply scored 0, nothing excluded.
2. Code-bank case parts (CB-C5 / CB-C6 / CB-C3, the Predict part) had no ran-out rule: a cut reply was
   'TRUNCATED -> fail'. Now a 4.5 code-bank part takes ``exclude`` (or the part's own ``ran_out``), and
   above scale.RAN_OUT_NOT_MEASURED (25%) of the part's calls cut the part is "not measured": its
   section is scaled from the measured points and the board says so.

A degenerate cut reply (a loop) still scores 0 on both paths. The fixture is the 8B's coding rows from
its rc2-main folder (C1 implied, C2 trace, CB-C5), with the reasoning dropped and long replies trimmed
(the trimmed rows render the same board as the full folder: checked when the fixture was cut)."""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from crucibleforge import chains, config, profiles, report

FIX = Path(__file__).with_name("fixtures") / "ds8b_rc2_coding_rows.json"
LABEL = "deepseek-r1-0528-8b"


def _rows():
    return json.loads(FIX.read_text(encoding="utf-8"))


def _omit(monkeypatch):
    """These tests pin the plain D10 rule (a cut reply is left out); the shipped profile says
    ``cut_at_full_budget: zero`` for the short-keyed parts since 2026-11-19 (tests/test_firstrun_f5_20261119.py)."""
    real = profiles.load_profile
    monkeypatch.setattr(profiles, "load_profile", lambda *a, **k: {**real(*a, **k), "cut_at_full_budget": "omit"})


def _prof(monkeypatch):
    monkeypatch.setenv(profiles.COMPOSITION_ENV, "rc2-main")
    return {**profiles.load_profile("bench45"), "cut_at_full_budget": "omit"}


# ------------------------------------------------------------------ 1. element-less chain units

def test_a_cut_trace_reply_is_excluded_not_scored_zero(monkeypatch):
    """FLAT-rc2_trace8_L8-003 was cut at its 69,632 ceiling (not degenerate): left out, so Trace is
    0.26 of 3 on 8 units (D's hand count), not 0.23 on 9."""
    st = chains.section_stats(_rows(), _prof(monkeypatch))["work/c2_trace"]
    assert st["units"] == 9 and st["ended"] == 9 and st["complete"]
    assert st["ran_out_excluded_units"] == 1 and st["elements"] == 8
    assert st["points"] == pytest.approx(0.264, abs=0.001)
    s8 = st["sets"]["rc2_trace8_L8"]
    assert s8 == {**s8, "elements": 3, "ran_out": 1, "rule": "exclude", "excluded_units": 1}
    assert s8["ceiling_too_low"]                     # 1 of 3 cut is still reported
    assert st["outcomes"]["ran out"] == 1


def test_a_cut_degenerate_trace_reply_still_scores_zero(monkeypatch):
    rows = _rows()
    for r in rows:
        if (r.get("chain") or {}).get("id") == "FLAT-rc2_trace8_L8-003":
            r["degenerate"] = True
    st = chains.section_stats(rows, _prof(monkeypatch))["work/c2_trace"]
    assert st["ran_out_excluded_units"] == 0 and st["elements"] == 9
    assert st["points"] == pytest.approx(0.7038 / 9 * 3, abs=0.001)


def test_a_zero_cell_keeps_scoring_the_cut_reply(monkeypatch):
    prof = _prof(monkeypatch)
    for fs in prof["chains"]["work/c2_trace"]["flat"]:
        fs["ran_out"] = "zero"
    st = chains.section_stats(_rows(), prof)["work/c2_trace"]
    assert st["ran_out_excluded_units"] == 0 and st["elements"] == 9
    assert st["points"] == pytest.approx(0.7038 / 9 * 3, abs=0.001)


# ------------------------------------------------------------------ 2. code-bank case parts

def _bank(n, cut, degenerate=0):
    out = []
    for i in range(n):
        r = {"case_id": "CB-X", "repeat": i + 1, "score": 1.0, "finish_reason": "stop", "response": "ok"}
        if i < cut:
            r.update(finish_reason="length", score=0.0, response="a real answer cut short")
            if i < degenerate:
                r["degenerate"] = True
        out.append(r)
    return out


def test_bank_part_rule():
    part = {"key": "c2_predict", "scoring": ["codebank"]}
    assert report.case_part_ran_out({"chains": {"x": {}}}, part) == "exclude"
    assert report.case_part_ran_out({"chains": {"x": {}}}, {**part, "ran_out": "zero"}) == "zero"
    assert report.case_part_ran_out({}, part) is None                       # 4.1: untouched
    assert report.case_part_ran_out({"chains": {"x": {}}}, {"key": "k", "scoring": ["coding"]}) is None
    with pytest.raises(report.ConfigError):
        report.case_part_ran_out({"chains": {"x": {}}}, {**part, "ran_out": "maybe"})


def test_bank_cut_reply_is_left_out_under_the_quarter():
    rows, rec = report.case_part_rows(_bank(5, 1), "exclude")
    assert len(rows) == 4 and all(r["finish_reason"] == "stop" for r in rows)
    assert rec == {"rule": "exclude", "calls": 5, "ran_out": 1, "share": 0.2, "not_measured": False}


def test_bank_part_over_the_quarter_is_not_measured():
    rows, rec = report.case_part_rows(_bank(5, 3), "exclude")
    assert rows == [] and rec["not_measured"] and rec["share"] == pytest.approx(0.6)
    rows, rec = report.case_part_rows(_bank(4, 1), "exclude")              # exactly 25%: measured
    assert len(rows) == 3 and not rec["not_measured"]


def test_bank_degenerate_cut_reply_stays_a_zero_and_never_counts_to_the_quarter():
    rows, rec = report.case_part_rows(_bank(5, 3, degenerate=3), "exclude")
    assert len(rows) == 5 and rec["ran_out"] == 0 and not rec["not_measured"]


def test_bank_zero_rule_and_no_rule_change_nothing():
    rows = _bank(5, 3)
    assert report.case_part_rows(rows, "zero") == (rows, None)
    assert report.case_part_rows(rows, None) == (rows, None)


# ------------------------------------------------------------------ the 8B's board (D's hand calculation)

def _current_rows(monkeypatch):
    """The fixture rows with the Implied attempts regraded under today's grader (replies untouched): the Implied
    key fix of the 4.5 item audit moved its chain revision, so the stored attempts are no longer current and the
    board would read Implied as pending. One unit moves (0.7391 -> 0.6957)."""
    from crucibleforge import regrade
    from crucibleforge.gen import _det
    p = _prof(monkeypatch)
    profiles.activate_cases_root(p)
    rows = _rows()
    specs = {c["id"]: c for c in chains.chain_cases(p)}
    recs = regrade.plan(rows, specs, "test: today's grader", None, {}, "work/c1_implied",
                        now="2026-11-18T00:00:00+00:00", commit="test", pin=_det.pinned_python(p))
    return rows + [r for rec in recs if rec["status"] == "regrade" for r in rec["rows"]]


@pytest.mark.live_ladder
def test_the_8b_board_line(tmp_path, monkeypatch):
    """Trace 0.26 of 3 on 8 units; Predict not measured (3 of 5 CB-C5 repeats cut at 65,536 = 60%);
    Coding 2.54 on 8 of 10 points, shown scaled to 3.2 with the scaling stated (post-T rows)."""
    _omit(monkeypatch)
    res = tmp_path / "results"
    res.mkdir()
    (res / f"transcripts_{LABEL}.jsonl").write_text("".join(json.dumps(r) + "\n" for r in _current_rows(monkeypatch)),
                                                    encoding="utf-8")
    cfgf = tmp_path / "models.yaml"
    cfgf.write_text(
        "providers:\n  studioforge:\n    type: openai\n    base_url: http://127.0.0.1:9/v1\n"
        f"models:\n- name: {LABEL}\n  model_id: m\n  provider: studioforge\n  context_length: 73728\n"
        "  thinking: true\njudge:\n  candidates:\n  - provider: studioforge\n    model_id: j\n"
        "    context_length: 16384\ndefaults:\n  context_length: 131072\n", encoding="utf-8")
    monkeypatch.setenv("CRUCIBLEFORGE_RESULTS", str(res))
    monkeypatch.setattr(config, "RESULTS_DIR", res)
    monkeypatch.setenv("CRUCIBLEFORGE_CONFIG", str(cfgf))
    monkeypatch.setenv(profiles.COMPOSITION_ENV, "rc2-main")
    from crucibleforge import cli
    try:
        rc = cli.main(["--results", str(res), "report", "--profile", "bench45"])
    except SystemExit as e:
        rc = e.code
    assert rc in (0, None)
    md = (res / "report.md").read_text(encoding="utf-8")
    # Implied 2.2 -> 2.3 (and 3.1 / 2.5 -> 3.2 / 2.6): the fixture's IMPLIED rows are T's regraded copies
    # (a47174a scrub + halves-up, regrade 060085f); the rule this pins is unchanged
    # fix round 2026-11-18: the 4.5.0 labels (R5: Implied = "Functions", Trace = "Steps", Predict = "Traps") and the
    # one truncation rule's wording (R1: "3 of 5 replies cut"); the numbers are unchanged
    assert re.search(rf"\| {LABEL} \| 3\.2 \(scaled from 2\.6 of 8 measured\) = Functions 2\.3±\d\.\d "
                     r"\+ Steps 0\.3±\d\.\d \+ Traps not measured \(3 of 5 replies cut\) \|", md), md
    assert re.search(rf"\| {LABEL} \| work/c2_trace \| 0\.26 / 3 \| 9/9 \| 8 \|", md), md

    cfg = config.load_config()
    cfg["_profile"] = "bench45"
    prof = report._tier_profile(cfg)
    st = report.tier_model_stats(LABEL, cfg, report.board_filter(LABEL, cfg), report.load_meta(LABEL),
                                 {"old": False})
    card = report.tier_scorecard(st, report.scoring_config(cfg), prof)
    parts = card["parts"]["work"]
    assert parts["c2_predict"] is None
    # 2.46 before T's regrade, 2.54 before the item-audit Implied key fix (one unit 0.7391 -> 0.6957)
    assert parts["c1_implied"] + parts["c2_trace"] == pytest.approx(2.52, abs=0.005)
    assert card["ladder"]["work"]["c2_predict"]["not_measured"]
    assert card["labels"]["work"].startswith("3.2") and "[2 pts not measured]" in card["labels"]["work"]
