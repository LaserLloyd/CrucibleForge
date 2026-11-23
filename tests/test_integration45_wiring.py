"""Integration of the 4.5 realignment lanes: the score tree computes from the real scorers, no stubs.

Chat scenes: stored rows + reader store -> rplong_mean -> segments45. Fixes: fixcheck-stamped rows -> fixrows45 ->
segments45. NSFW+: stored rows -> classing45.units_from_rows -> reader store (offline) -> gate_result ->
nsfwplus45.numbers. A missing input is "pending: <what>", never 0."""
from __future__ import annotations

import json

import pytest

from crucibleforge import classing45 as C
from crucibleforge import fixrows45 as FR
from crucibleforge import nsfwplus45 as NP
from crucibleforge import rplong_mean as RM
from crucibleforge import segments45 as S
from crucibleforge.gen import adult45

pytestmark = pytest.mark.live_ladder      # reads the real adult45 bank
LABEL = "deepseek-v4-flash"          # graded family deepseek: readers rq (qwen) + rm (mistral) fit
PROF = {"nsfw_plus": {"default": "on", "classing": {"readers": {"local": ["R-Q", "R-M"], "hosted_fallback": []}}}}
NAMED = {"R-Q": {"provider": "p", "model_id": "some-qwen-tune", "family": "qwen"},
         "R-M": {"provider": "p", "model_id": "some-mistral-tune", "family": "mistral"}}


@pytest.fixture(autouse=True)
def _readers(monkeypatch):
    monkeypatch.setattr(C, "named_readers", lambda cal_dir=None: NAMED)


def _admit(res):
    sha = C.load_prompt({})[1]
    (res / "marks45.json").write_text(json.dumps({"classing": {"C1_constructed": {"per_reader": {
        r: {"model": d["model_id"], "family": d["family"], "prompt_sha": sha, "status": "pass"}
        for r, d in NAMED.items()}}}}), encoding="utf-8")


def _card(variant, cid):
    return next(it for it in adult45.items(variant) if it["id"] == cid)


def _row(variant, cid, text, rep=1, finish="stop"):
    return {"case_id": f"{variant}-{cid}-x", "repeat": rep, "turn": 1, "bench_run_id": "run1", "response": text,
            "finish_reason": finish, "ts": "2026-11-19T00:00:00",
            "judge_card": {"variant": variant, "card": cid, "sexual": True}}


# ---------------------------------------------------------------- NSFW+
def test_nsfw_plus_units_come_from_one_path_and_cover_every_variant():
    ref = _card("write", "W1-ashby")["ref_reply"]
    rows = [_row("write", "W1-ashby", ref), _row("bodies", "B1-x", "a reply")]
    cl = C.units_from_rows(rows[:1])
    assert [u["variant"] for u in cl] == ["write"]
    units = NP.units_from_rows(rows[:1])
    assert units[0]["segment"] == "erotica_written" and units[0]["classing"]["ask"] == cl[0]["ask"]
    assert NP.SEGMENT_OF.keys() == set(adult45.VARIANTS)


def test_no_nsfw_rows_is_not_run_never_zero(tmp_path):
    b = NP.report_block([{"case_id": "x", "response": "hi"}], LABEL, tmp_path, PROF)
    assert b["state"] == "not run" and b["reason"] == "pending: NSFW+ not run in this folder" and b["numbers"] is None


def test_stored_votes_class_a_unit_offline_and_a_missing_vote_is_pending(tmp_path):
    # Part 1 (2026-11-21): the run's judge store holds ONE vote per unit; no reader admission, no pair
    from crucibleforge import judge45 as J
    prof = {**PROF, "judge": {"provider": "studioforge", "model_id": "local-judge"}}
    ref = _card("write", "W1-ashby")["ref_reply"]
    rows = [_row("write", "W1-ashby", ref), _row("write", "W1-ashby", ref + " More.", rep=2)]
    units = NP.units_from_rows(rows)
    prompt = C.load_prompt(prof)
    store = J.JudgeStore(J.store_path(tmp_path, LABEL))
    u = units[0]["classing"]
    import re as _re
    quote = next(x for x in _re.split(r"(?<=[.!?])\s+", u["reply"]) if C.crude_rx().search(x) and len(x.split()) >= 3)
    store.put("class", "local-judge", prompt[1], C.unit_sha(u["ask"], u["reply"]),
              {"cls": "EXPLICIT", "quote": quote, "quote_ok": True})
    b = NP.report_block(rows, LABEL, tmp_path, prof)
    assert b["counts45"]["nsfw"]["explicit"] == 1 and b["counts45"]["nsfw"]["asks"] == 2
    ero = b["numbers"]["nsfw"]["segments"]["erotica"]
    assert ero["value"] is None and "pending" in ero["pending"]               # repeat 2 unread, 4 cards not run
    assert "not judged yet" in b["judge_pending"]
    assert b["numbers"]["nsfw"]["value"] is None and b["numbers"]["nsfw"]["partial"] == 0.0


def test_readers_not_configured_name_the_reason(tmp_path):
    # Part 1 (2026-11-21): the reader set picks nothing; a profile without a judge says so
    ref = _card("write", "W1-ashby")["ref_reply"]
    b = NP.report_block([_row("write", "W1-ashby", ref)], LABEL, tmp_path, PROF)
    assert b["numbers"]["nsfw"]["pending"].startswith("no judge named in the profile")
    b = NP.report_block([_row("write", "W1-ashby", ref)], LABEL, tmp_path,
                        {**PROF, "judge": {"provider": "studioforge", "model_id": "local-judge"}})
    assert "not judged yet" in b["judge_pending"] and "reader" not in b["numbers"]["nsfw"]["pending"]


def test_provider_filter_on_nsfw_plus_scores_zero_and_is_counted(tmp_path):
    rows = [_row("write", "W1-ashby", "", finish="content_filter")]
    b = NP.report_block(rows, LABEL, tmp_path, PROF)
    assert b["counts45"]["nsfw"]["filtered"] == 1
    ev = NP.evaluate("erotica_written", NP.units_from_rows(rows)[0], None)
    assert ev["score"] == 0.0 and ev["outcome"] == "filtered"


# ---------------------------------------------------------------- one reader store per model, two line kinds
def test_reader_store_file_holds_votes_and_scene_records(tmp_path):
    p = tmp_path / "readers45" / f"{LABEL}.jsonl"
    st = C.ReaderStore(p)
    rq = C.spec_from(NAMED["R-Q"], name="R-Q")
    st.put("u1", rq, "sha", {"cls": "SOFTENED", "quote": "", "quote_ok": False, "reason": "ok"})
    RM.append_store(tmp_path, LABEL, [{"part": "chat/j_rplong", "unit": "r|CC3-tea-hut|1", "reader": "x+think",
                                        "row_sha": "a", "score_reader": 0.5, "reader_stamp": {"prompt_sha": "s"}}])
    assert RM.store_path(tmp_path, LABEL) == p
    assert list(RM.load_store(tmp_path, LABEL)) == [("r|CC3-tea-hut|1", "x+think", "s", "a")]
    st2 = C.ReaderStore(p)
    assert len(st2.lines) == 1 and st2.get("u1", rq, "sha")["cls"] == "SOFTENED"


# ---------------------------------------------------------------- fixes
def test_fix_rows_count_only_with_the_current_checker_stamp():
    ok = FR.stamp_verdict({"score": 0.75, "grade": "partial", "checker": "fixcheck-host-compared-1"})
    rows = [{"case_id": "PATCHFIX-core-7-1", "repeat": 1, "fixcheck": ok},
            {"case_id": "PATCHFIX-core-7-2", "repeat": 1, "fixcheck": {**ok, "stamp": "000000000000"}},
            {"case_id": "PATCHMOD-core-3-1", "repeat": 1, "score": 1.0}]               # old code-bank grade only
    cs = FR.case_scores(rows, ["PATCHFIX-core-7-1", "PATCHFIX-core-7-2", "PATCHMOD-core-3-1", "CB-F1-larder-bughunt"])
    c = cs["cases"]
    assert c["PATCHFIX-core-7-1"] == {"scores": [0.75], "reason": None, "stale": 0, "old_grader": 0}
    assert "older checker" in c["PATCHFIX-core-7-2"]["reason"]
    assert c["PATCHMOD-core-3-1"]["reason"] == "pending: no fix rows yet" and c["PATCHMOD-core-3-1"]["old_grader"] == 1
    assert c["CB-F1-larder-bughunt"]["scores"] == []
    assert len(FR.checker_stamp()) == 12


def test_segment_reads_fix_rows_and_scenes_from_the_report_inputs():
    m = {"headline": {"max": {}, "P": {}, "m": {}}, "fix45": FR.case_scores([], ["CB-F1-larder-bughunt"]),
         "rplong45": {"scenes": {"WR1-ninth-string": {"score": 0.8, "reason": None}}}}
    assert S._fix_case(m, "CB-F1-larder-bughunt") == (None, "pending: no fix rows yet")
    assert S._scene(m, "WR1-ninth-string") == (0.8, None)
    assert S._scene(m, "WR2-red-thread-key")[1] == "pending: not run or not judged yet"   # Part 2 item 10 wording
    assert json.dumps(m)                                                      # plain data, report JSON safe
