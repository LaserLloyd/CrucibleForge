"""Chat packs (realign45-packs-chat, wired at integration 2026-11-20): set files, byte-identical 4.1 cases, mean-of-two
scoring, segment gate, wiring."""
import json
from pathlib import Path

import pytest

from crucibleforge import ladder, packs45, profiles
from crucibleforge import rplong_mean as RM

ROOT = Path(__file__).resolve().parents[1]
C41 = Path.home() / "Projects" / "crucibleforge" / "cases" / "ladder" / "chat"
SETS = {"pk_character": 2, "pk_remembers": 2, "pk_cowrites": 2}


def _set(part, n):
    return json.loads((ROOT / "cases/ladder/chat" / part / f"set-{n}.json").read_text(encoding="utf-8"))


def test_every_pack_set_is_live_and_sized():
    # wired at integration 2026-11-20 (the lane built them staged)
    for part in SETS:
        for n in (1, 2):
            s = ladder._read_set(ROOT / "cases/ladder/chat" / part / f"set-{n}.json")
            assert not ladder.is_staged(s), (part, n)
            assert ladder._size_problems(s) == []
            assert s.get("points", 1.0) == 1.0


@pytest.mark.live_ladder
def test_packs_are_wired_into_the_release_profile_and_the_scorer_stamp(monkeypatch):
    from crucibleforge import segments45
    monkeypatch.setenv(profiles.COMPOSITION_ENV, "standard")
    prof = profiles.load_profile("bench45")
    found = ladder.discover(prof)
    assert {k for k in found if "/pk_" in k} == {f"chat/{p}" for p in SETS}
    assert all([x["n"] for x in found[f"chat/{p}"]] == [1, 2] for p in SETS)
    assert set(packs45.CODE_LEVELS) == set(packs45.SEGMENTS)
    parts = prof["scores"]["chat"]["parts"]
    for seg, part in packs45.PART_OF.items():
        assert float(parts[part]["max"]) == 0 and parts[part]["pack_segment"] == seg
    assert "packs45.py" in segments45.SCORER_FILES
    assert "_chat_packs45" in (ROOT / "crucibleforge/report.py").read_text(encoding="utf-8")


def test_board_packs_lock_on_a_held_or_low_segment_and_stay_pending_without_the_marks():
    tb = {"segments": {"character": {"points": 3.8, "max": 4, "complete": True},
                       "remembers": {"points": None, "max": 3.5, "complete": False},
                       "cowrites": {"points": 1.2, "max": 1.5, "complete": True}}}
    out = packs45.board_packs(tb, {}, {})
    assert out["character"]["state"] == "pending" and out["character"]["points"] == 0.0
    assert out["remembers"]["state"] == "locked" and out["cowrites"]["state"] == "locked"
    assert all(v["points"] == 0.0 for v in out.values())


@pytest.mark.skipif(not C41.is_dir(), reason="4.1 checkout not present")
def test_judged_cases_are_byte_identical_to_4_1():
    src = {}
    for sub in ("character", "conversation", "writing"):
        for c in json.loads((C41 / sub / "set-1.json").read_text(encoding="utf-8"))["cases"]:
            src[c["id"]] = c
    got = {}
    for part in SETS:
        for c in _set(part, 1)["cases"]:
            got[c["id"]] = c
    assert set(got) == set(packs45.PACK_SCENES)
    for cid, c in got.items():
        assert c == src[cid], cid


def test_generated_levels_materialise_and_key_audit():
    from crucibleforge import gen
    for part in SETS:
        spec = ladder._read_set(ROOT / "cases/ladder/chat" / part / "set-2.json")
        assert len(spec["cases"]) == 4
        for c in spec["cases"]:
            assert gen.verify_case(c) == []


def _row(case, rate_ok, run="r1"):
    return {"case_id": case, "rubric": "rp_deep", "bench_run_id": run, "repeat": 1,
            "judge": {"elements": {"results": [{"id": "a", "pass": rate_ok}]},
                      "instrument": {"rubric_sha": "sha1"}}}


def test_units_only_for_pack_scenes():
    rows = [_row("LRV1-pilots-mess", True), _row("RV1-three-at-the-table", True)]
    assert list(packs45.pack_units(rows)) == ["r1|LRV1-pilots-mess|1"]


def test_scene_scores_need_the_pinned_reader(monkeypatch):
    rows = [_row("LCC1-quiet-hours", True)]
    monkeypatch.setattr(packs45.AG, "long_score", lambda row, v, c: 1.0)
    monkeypatch.setattr(packs45.AG, "row_sha", lambda r: "rs")
    rd = RM.ReaderSpec(id="qwen-reader", family="alibaba", thinking=True)
    monkeypatch.setattr(RM, "check_fit", lambda *a, **k: None)
    s = packs45.scene_scores(rows, [], label="m", cases={}, graded_family="x", reader_for=rd)
    (u,) = s.values()
    assert u["status"] == "pending" and u["mean"] is None
    rec = {"unit": "r1|LCC1-quiet-hours|1", "model": "m", "row_sha": "rs", "score_reader": 0.5,
           "reader_stamp": {"model": "qwen-reader", "family": "alibaba", "prompt_sha": "sha1"}}
    s = packs45.scene_scores(rows, [rec], label="m", cases={}, graded_family="x", reader_for=rd)
    (u,) = s.values()
    assert u["status"] == "ok" and u["mean"] == pytest.approx(0.75)   # mean of judge 1.0 and reader 0.5
    # a record from another reader never counts
    rec2 = {**rec, "reader_stamp": {**rec["reader_stamp"], "model": "other"}}
    (u,) = packs45.scene_scores(rows, [rec2], label="m", cases={}, graded_family="x", reader_for=rd).values()
    assert u["status"] == "pending"


def test_judged_share_never_prints_partial():
    ok = lambda c, m: {"case": c, "segment": "remembers", "status": "ok", "mean": m, "reason": None}
    sc = {"a": ok("LCC1-quiet-hours", 0.8), "b": ok("LCC2-last-boxes", 0.6)}
    sh, why = packs45.judged_share(sc, "remembers")
    assert sh is None and "LCC3" in why
    sc["c"] = ok("LCC3-left-luggage", 1.0)
    assert packs45.judged_share(sc, "remembers")[0] == pytest.approx(0.8)


def test_gate_and_points():
    assert not packs45.segment_unlock(3.0, 3.5)       # .857 < .9
    assert packs45.segment_unlock(3.2, 3.5)
    p = packs45.pack_points("remembers", 2.0, 3.5, 0.8, None, marks_pass=True)
    assert p["state"] == "locked" and p["points"] == 0
    p = packs45.pack_points("remembers", 3.3, 3.5, 0.8, 1.0, marks_pass=True)
    assert p["points"] == pytest.approx(0.8)           # level 2 stays closed under .9
    p = packs45.pack_points("remembers", 3.3, 3.5, 0.95, 0.5, marks_pass=True)
    assert p["points"] == pytest.approx(1.45)
    # Part 2 (owner ruling 2026-11-21): marks never gate; the pack scores the same without them
    p = packs45.pack_points("remembers", 3.3, 3.5, 0.95, 0.5)
    assert p["state"] == "scored" and p["points"] == pytest.approx(1.45)


def test_marks_addendum_is_indexed_and_precedes_data():
    idx = (ROOT / "calibration/MARKS-INDEX-45.md").read_text(encoding="utf-8")
    assert "pack-marks-chat-45-addendum-20261120.json" in idx
    row = [l for l in idx.splitlines() if "pack-marks-chat-45-addendum" in l][0]
    assert "before any pack row existed and before any launch row was read" in row
    d = json.loads((ROOT / "calibration/pack-marks-chat-45-addendum-20261120.json").read_text(encoding="utf-8"))
    assert d["written_before_any_pack_row"] is True and {"P1_reader_agreement", "P2_known_order", "P3_not_maxed"} <= set(d["marks"])
