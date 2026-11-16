"""G-Reuse report check (scripts/reuse_gate.py) on synthetic rows; one admission path."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import reuse_gate  # noqa: E402
from crucibleforge import report, version  # noqa: E402

IDS = ["CL3-x", "CC4-y"]
PROF = {"name": "bench45", "regime": "deployed", "reuse": {"from": ["bench"], "ids": IDS}}


@pytest.fixture(autouse=True)
def world(monkeypatch):
    # A0b: the stamp is compared with the SOURCE profile's tree; the live and source
    # generation views must be equal (both trees hold the same synthetic cases here)
    raw = {i: {"id": i, "system": "S"} for i in IDS}
    monkeypatch.setattr(report, "_source_tree",
                        lambda rp: ({i: {"case": "H-" + i} for i in IDS}, raw))
    monkeypatch.setattr(version, "live_views", lambda: ({i: {"case": "H-" + i} for i in IDS}, raw))
    monkeypatch.setattr(report, "_cases_by_id", lambda: {i: {"id": i, "system": "S"} for i in IDS})
    monkeypatch.setattr(report, "_measure_view", lambda name: {"k": 1})
    monkeypatch.setattr(report, "_MEASURE_KEYS", ("k",))
    PROF["k"] = 1
    yield


def row(cid="CL3-x", **kw):
    r = {"case_id": cid, "profile": "bench", "regime": "deployed", "case_revision": "H-" + cid,
         "finish_reason": "stop", "ts": "2026-11-01T00:00:00", "judge_fingerprint": "FP"}
    r.update(kw)
    return r


def ev(rows, **kw):
    return reuse_gate.evaluate(rows, PROF, IDS, **kw)


def test_reused_and_every_refusal_has_a_reason():
    rows = {"m1": [row(), row("CC4-y")],
            "m2": [row(finish_reason="length"), row("CC4-y", case_revision="old")],
            "m3": [row(regime="thinking")],
            "m4": [row(system="other")],
            "m5": [row(profile="other-profile")],
            "m6": [row(error="boom")]}
    r = ev(rows)
    m = r["models"]
    assert m["m1"]["CL3-x"]["status"] == m["m1"]["CC4-y"]["status"] == "reused"
    reasons = {(k, c): e.get("reason") for k, v in m.items() for c, e in v.items()
               if e["status"] == "rerun_required"}
    assert reasons[("m2", "CL3-x")] == "truncated" and reasons[("m2", "CC4-y")] == "stamp"
    assert reasons[("m3", "CL3-x")] == "regime" and reasons[("m4", "CL3-x")] == "system"
    assert reasons[("m5", "CL3-x")] == "profile" and reasons[("m6", "CL3-x")] == "errored"
    assert reasons[("m3", "CC4-y")] == "missing"
    assert all(reasons.values())                                   # never a silent pending
    assert r["reused"] == 2 and r["rerun_required"] == 10 and r["ok"]


def test_expected_count_is_enforced():
    rows = {"m1": [row(), row("CC4-y")]}
    assert ev(rows, expected_reused=2)["ok"]
    r = ev(rows, expected_reused=3)
    assert not r["ok"] and "expected 3" in r["problems"][0]


def test_judged_rows_need_the_reference_judge():
    rows = {"m1": [row("CC4-y", judge_fingerprint="OLD")]}
    r = ev(rows, judged_ids={"CC4-y"}, judge_fingerprint="FP")
    assert r["regrade_needed"] == 1 and not r["ok"]
    assert r["models"]["m1"]["CC4-y"]["regrade_needed"] is True
    assert ev({"m1": [row("CC4-y")]}, judged_ids={"CC4-y"}, judge_fingerprint="FP")["ok"]


def test_the_only_admission_path_is_reusable_row(monkeypatch):
    """G-Reuse 6: the gate decides with report.reusable_row and nothing else."""
    calls = []
    monkeypatch.setattr(report, "reusable_row", lambda r, p: calls.append(r["case_id"]) or False)
    r = ev({"m1": [row()]})
    assert calls == ["CL3-x"] and r["reused"] == 0
    monkeypatch.setattr(report, "reusable_row", lambda r, p: True)
    assert ev({"m1": [row(stamp_garbage=1)]})["reused"] == 1       # admission is not re-derived here


def test_why_not_labels_only_what_reusable_row_refused():
    for kw in ({}, {"finish_reason": "length"}, {"regime": "x"}, {"case_revision": "z"},
               {"system": "q"}, {"profile": "zz"}):
        r = row(**kw)
        if report.reusable_row(r, PROF):
            continue
        assert reuse_gate.why_not(r, PROF) in {"truncated", "regime", "stamp", "system", "profile", "measure",
                                              "generation"}


def test_a_changed_generation_view_is_labelled_generation(monkeypatch):
    """A0b: stamp equal to the source tree but the live case moved for the model."""
    src = {i: {"id": i, "system": "S"} for i in IDS}
    live = {**src, "CL3-x": {"id": "CL3-x", "system": "S", "prompt": "new"}}
    monkeypatch.setattr(version, "live_views", lambda: ({}, live))
    monkeypatch.setattr(report, "_source_tree", lambda rp: ({i: {"case": "H-" + i} for i in IDS}, src))
    r = ev({"m1": [row()]})
    assert r["models"]["m1"]["CL3-x"] == {"status": "rerun_required", "reason": "generation"}


def test_own_profile_rows_are_not_reuse_candidates():
    """S10 review 5a: a bench45 row of a Base id is the profile's own row, not a reused 4.1
    row (reusable_row admits own rows unconditionally, truncated ones too)."""
    r = ev({"m1": [row(profile="bench45", finish_reason="length")]})
    assert r["models"]["m1"]["CL3-x"] == {"status": "rerun_required", "reason": "profile"}
    assert r["reused"] == 0


def test_reused_rows_that_need_a_rejudge_are_counted(monkeypatch):
    """S10 review 5b: a grading-only change leaves the row reusable but not on the board until
    it is re-judged; the gate says how many."""
    src = {i: {"id": i, "system": "S", "judge_elements": [{"id": "a"}]} for i in IDS}
    live = {**src, "CC4-y": {"id": "CC4-y", "system": "S", "judge_elements": [{"id": "b"}]}}
    monkeypatch.setattr(version, "live_views", lambda: ({}, live))
    monkeypatch.setattr(report, "_source_tree", lambda rp: ({i: {"case": "H-" + i} for i in IDS}, src))
    r = ev({"m1": [row(), row("CC4-y")]})
    assert r["reused"] == 2 and r["rejudge_required"] == 1
    assert r["models"]["m1"]["CC4-y"].get("rejudge_required") is True
    assert "rejudge_required" not in r["models"]["m1"]["CL3-x"]
