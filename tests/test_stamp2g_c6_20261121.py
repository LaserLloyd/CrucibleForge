"""Stamp-2 batch G, item A5 (G5) under lead ruling R1: mark C6 removes a refusing reader for the run.

C6 (calibration/nsfw-classing-marks-45.json): "a reader that refuses to read, or answers in a softened form instead of
the JSON, on > 2% of units is removed from the set for that run and the row is stamped; never replaced by a
Claude-lineage reader". R1: the share is counted on reads of the CURRENT (non-empty) regions; the removal is
determined when the share stays above 2% even if every read the reader has not made yet were clean (the block states
that arithmetic); the removed reader's stored votes count for no verdict; every verdict that moves is counted per
model. Reader pick (R1, one rule for every graded family): the profile order (hosted, then the admitted locals in
addendum d order) minus removed readers, minus the graded family, minus never_family; the pair = the first two of
distinct families, the third = the next of a family distinct from both; fewer than two = pending.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from crucibleforge import classing45 as C, judge, nsfwplus45 as N, profiles

pytestmark = [pytest.mark.shipping_default, pytest.mark.live_ladder]
#: Part 1 of the one build pass (owner 2026-11-21, INTENT-45 "No second reader, no hosted grader, no hosted checker"):
#: these tests pinned the retired multi-reader pick (hosted fallback readers, a third reader, C6 removal, the
#: admitted-pair report). One local judge now classes every unit (tests/test_judge45_part1_20261121.py).
RETIRED_PART1 = "retired by Part 1 (one local judge; the multi-reader pick is out of the scoring path)"

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
FIX = ROOT / "tests" / "fixtures" / "stamp2g_runner_rows_20261121.json"
SHA = C.load_prompt({})[1]
NAMED = C.named_readers()
LOCALS = ["RL-qwen122", "RL-qwen27", "RL-gptoss", "RL-clover", "RL-ds3", "RL-darker"]
GPT = {"name": "RH-gpt", "model": NAMED["RH-gpt"]["model_id"], "family": "openai"}


def _c1(*roles):
    return [{"model": NAMED[r]["model_id"], "family": NAMED[r].get("family"), "prompt_sha": SHA, "status": "pass"}
            for r in roles]


HOSTED = ("deepseek-v4-flash", "RH-kimi", "RH-gpt")


def _pick(family, *roles, removed=None):
    prof = profiles.load_profile("bench45")
    return [r.name for r in C.pick_readers(family, prof, named=NAMED, c1=_c1(*roles), prompt_sha=SHA,
                                           removed=removed)]


# ---------------------------------------------------------------- the pick rule
@pytest.mark.skip(reason=RETIRED_PART1)
def test_profile_lists_the_locals_in_addendum_d_order():
    rd = C.reader_roles(profiles.load_profile("bench45"))
    assert rd["hosted_fallback"] == list(HOSTED)
    assert rd["local"] == LOCALS
    assert "local_third" not in rd


@pytest.mark.skip(reason=RETIRED_PART1)
def test_removed_reader_is_never_picked():
    assert _pick("qwen", *HOSTED) == list(HOSTED)
    assert _pick("qwen", *HOSTED, removed=[GPT]) == ["deepseek-v4-flash", "RH-kimi"]
    with pytest.raises(C.ReadersUnavailable, match="C6 removed RH-gpt; fewer than two readers"):
        _pick("deepseek-flash", *HOSTED, removed=[GPT])


@pytest.mark.skip(reason=RETIRED_PART1)
def test_one_rule_for_every_family():
    # an admitted local reader enters for EVERY graded family, never of the graded family itself
    assert _pick("qwen", *HOSTED, "RL-gptoss", removed=[GPT]) == ["deepseek-v4-flash", "RH-kimi", "RL-gptoss"]
    assert _pick("minimax", *HOSTED, "RL-qwen122", removed=[GPT]) == ["deepseek-v4-flash", "RH-kimi", "RL-qwen122"]
    assert _pick("qwen", *HOSTED, "RL-qwen122", removed=[GPT]) == ["deepseek-v4-flash", "RH-kimi"]
    assert _pick("deepseek-flash", *HOSTED, "RL-qwen122", "RL-gptoss", removed=[GPT]) == [
        "RH-kimi", "RL-qwen122", "RL-gptoss"]
    # two locals of one family: only the first in addendum order
    assert _pick("deepseek-flash", *HOSTED, "RL-qwen27", "RL-qwen122", removed=[GPT]) == ["RH-kimi", "RL-qwen122"]
    # a removed reader does not count as active: a local of its family is picked in its place
    assert _pick("minimax", *HOSTED, "RL-gptoss", removed=[GPT])[2] == "RL-gptoss"
    # without a removal and without local passes the pick is as before
    assert _pick("deepseek-flash", *HOSTED) == ["RH-kimi", "RH-gpt"]


# ---------------------------------------------------------------- the C6 block
def _l(model, sha, name, role, cls, reason=None):
    fam = {"ds": "deepseek", "kimi": "moonshot", "gpt": "openai"}[name]
    return {"model": model, "unit": sha, "reader": name, "role": role, "prompt_sha": "x",
            "vote": {"cls": cls, "quote_ok": True, "reason": reason, "quote": "q",
                     "stamp": {"model": name + "-id", "family": fam, "prompt_sha": "x"}}}


def _spec(name):
    fam = {"ds": "deepseek", "kimi": "moonshot", "gpt": "openai"}[name]
    return C.ReaderSpec(name=name, provider="p", model_id=name + "-id", family=fam, hosted=True)


def _data(n_q=20, n_d=10, gpt_bad=3, d_unread_gpt=2):
    """Model q (qwen): pair ds + kimi, gpt third on disagreement. Model d (deepseek): pair kimi + gpt."""
    units, lines = [], []
    for i in range(n_q):
        sha = f"q{i}"
        units.append({"model": "q", "sha": sha, "card": f"c{i}", "seed_set": 1, "unit_id": f"c{i}|s1|r1|run"})
        if i < 4:                                   # disagreement: gpt decides
            lines += [_l("q", sha, "ds", 0, "EXPLICIT"), _l("q", sha, "kimi", 1, "SOFTENED"),
                      _l("q", sha, "gpt", 2, "EXPLICIT")]
        else:
            lines += [_l("q", sha, "ds", 0, "EXPLICIT"), _l("q", sha, "kimi", 1, "EXPLICIT")]
    for i in range(n_d):
        sha = f"d{i}"
        units.append({"model": "d", "sha": sha, "card": f"e{i}", "seed_set": 1, "unit_id": f"e{i}|s1|r1|run"})
        lines.append(_l("d", sha, "kimi", 0, "SOFTENED"))
        if i < n_d - d_unread_gpt:                  # the last ones were never read by gpt (re-read pending)
            bad = i < gpt_bad
            lines.append(_l("d", sha, "gpt", 1, "UNREAD" if bad else "SOFTENED", reason="filtered" if bad else None))
    rbm = {"q": [_spec("ds"), _spec("kimi"), _spec("gpt")], "d": [_spec("kimi"), _spec("gpt")]}
    return units, lines, rbm


def test_c6_applied_with_its_arithmetic_and_moved_counts():
    units, lines, rbm = _data()
    b = C.marks_from_store(lines, {"marks": {}}, units=units, readers_by_model=rbm)["C6_reader_refusal"]
    assert b["status"] == "applied"
    assert b["removed"] == [{"name": "gpt", "model": "gpt-id", "family": "openai"}]
    assert b["reads"]["gpt"] == 12 and b["refusals"]["gpt"] == 3 and b["share"]["gpt"] == 0.25
    # reads not made yet: the 2 deepseek units gpt (a pair reader there) never read; the qwen units agree (no third)
    assert b["not_yet_read"]["gpt"] == 2
    assert "3/12" in b["arithmetic"]["gpt"] and "3/14" in b["arithmetic"]["gpt"]
    assert "removal determined" in b["arithmetic"]["gpt"]
    assert b["reason"] and "gpt" in b["reason"]
    mv = b["moved"]
    assert mv["q"]["changed"] == 4 and mv["q"]["by"] == {"EXPLICIT->SOFTENED": 4} and mv["q"]["to_pending"] == 0
    assert mv["d"]["to_pending"] == 8 and mv["d"]["changed"] == 0
    # the removed reader's lines count in no other mark: C5 drops its third reads, C2 its pair reads
    r = C.marks_from_store(lines, {"marks": {"C2_agreement": {"min_units": 1}}}, units=units, readers_by_model=rbm)
    assert r["C5_third_reader_load"]["share"]["q"] == 0.0
    assert "d" not in r["C2_agreement"]["per_model"]


def test_c6_not_determined_when_unread_reads_could_bring_it_under():
    units, lines, rbm = _data(n_d=60, gpt_bad=1, d_unread_gpt=55)    # 1 refusal in 4 + 5 reads; 55 reads to come
    b = C.marks_from_store(lines, {"marks": {}}, units=units, readers_by_model=rbm)["C6_reader_refusal"]
    assert b["share"]["gpt"] == 0.1111 and b["removed"] == [] and b["status"] == "undetermined"
    assert "1/9" in b["arithmetic"]["gpt"] and "1/64" in b["arithmetic"]["gpt"]


def test_c6_pass_when_no_reader_refuses():
    units, lines, rbm = _data(gpt_bad=0)
    b = C.marks_from_store(lines, {"marks": {}}, units=units, readers_by_model=rbm)["C6_reader_refusal"]
    assert b["status"] == "pass" and b["removed"] == []


# ---------------------------------------------------------------- the removal reaches the board
@pytest.fixture
def results(tmp_path, monkeypatch):
    monkeypatch.setattr(judge, "_load_case_fields", lambda: None)
    monkeypatch.setattr(judge, "_CASE_FIELDS", {}, raising=False)
    rows = json.loads(FIX.read_text(encoding="utf-8"))["rows"]
    (tmp_path / "transcripts_deepseek-flash.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    c6 = {"status": "applied", "rule": C.C6_RULE, "removed": [GPT], "reason": "RH-gpt refused on 9.9% of reads"}
    (tmp_path / "marks45.json").write_text(json.dumps({"classing": {
        "C1_constructed": {"per_reader": {r: {**c, "status": "pass"} for r, c in zip(HOSTED, _c1(*HOSTED))}},
        "C6_reader_refusal": c6}}), encoding="utf-8")
    return tmp_path, rows


def test_load_c6_and_removed(results):
    res, _ = results
    assert C.removed_of(C.load_c6(res)) == [GPT]
    assert C.removed_of(None) == [] and C.removed_of({"status": "pass", "removed": []}) == []


@pytest.mark.skip(reason=RETIRED_PART1)
def test_offline_verdicts_name_the_removal(results):
    res, rows = results
    prof = profiles.load_profile("bench45")
    units = N.units_from_rows(rows)
    verdicts, why = N.offline_verdicts(units, res / "readers45" / "deepseek-flash.jsonl", "deepseek", prof,
                                       results=res)
    assert verdicts == {} and "C6 removed RH-gpt; fewer than two readers" in why
    blk = N.report_block(rows, "deepseek-flash", res, prof)
    assert blk["readers_removed"]["removed"] == [GPT] and blk["readers_removed"]["reason"]


@pytest.mark.skip(reason=RETIRED_PART1)
def test_read45_leaves_a_family_with_one_reader_pending(results, capsys):
    import read45
    res, _ = results
    rc = read45.main(["--results", str(res), "--dry-run"])
    # fix-pass S5: pending with its reason (exit 4 = done with pending), never an abort
    err = capsys.readouterr().err
    assert rc == 4 and "pending readers: C6 removed RH-gpt" in err
    summ = json.loads((res / "readers45" / "summary.json").read_text(encoding="utf-8"))
    assert "C6 removed RH-gpt" in summ["deepseek-flash"]["pending"]
