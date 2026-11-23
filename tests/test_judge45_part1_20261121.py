"""Part 1 of the one build pass (owner 2026-11-21, INTENT-45 "One local judge does all the grading"): the run's judge
classes every NSFW+ unit and reads the Uncensored opinion flags, one vote each, into its own store; no second reader,
no hosted grader or checker; NSFW+ /20 = NSFW + Uncensored.

Synthetic rows and a fake judge only: no model call, no rig. The offline proof on copies of graded folders is the
last test (skipped unless ``MB45_PART1_FOLDERS`` names scratch copies)."""
from __future__ import annotations

import json
import os
import re
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from crucibleforge import classing45 as C  # noqa: E402
from crucibleforge import flags45 as F  # noqa: E402
from crucibleforge import judge45 as J  # noqa: E402
from crucibleforge import nsfwplus45 as N  # noqa: E402
from crucibleforge import profiles  # noqa: E402
from crucibleforge.gen import adult45 as A  # noqa: E402

pytestmark = [pytest.mark.shipping_default, pytest.mark.live_ladder]

LABEL = "deepseek-flash"
# the profile's judge (amendment r, 2026-11-21: the plain Qwen 3.8 27B; Gemma 4 31B heretic before)
JUDGE = "unsloth/Qwen3.8-27B-GGUF/Qwen3.8-27B-Q8_0"
GEMMA = "llmfan46/gemma-4-31B-it-uncensored-heretic-GGUF/gemma-4-31B-it-uncensored-heretic-Q8_0"


def _prof():
    return profiles.load_profile("bench45")


# ------------------------------------------------------------------ synthetic rows + a fake judge
def std_rows(run="s1", variants=("write", "taboo", "uask", "open"), n=2) -> list[dict]:
    rows = []
    for v in variants:
        for i, it in enumerate(A.items(v)[:n]):
            m = A.materialize({"variant": v, "pin": it["id"]}, release_seed="s", case_id=f"STD-{v}-{i}")
            ref = A.reference({"variant": v, "pin": it["id"]}, release_seed="s", case_id=f"STD-{v}-{i}")
            rows.append({"case_id": f"STD-{v}-{i}", "repeat": 1, "bench_run_id": run, "turn": 1, "response": ref,
                         "finish_reason": "stop", "category": "nsfw", "grader_family": "adult45",
                         "judge_card": m["judge_card"], "model_label": LABEL, "ts": "2026-11-21T00:00:00+00:00"})
    return rows


def _reply_of(messages) -> str:
    u = messages[-1]["content"]
    m = re.search(re.escape(C.REPLY_OPEN) + r"\n(.*)\n" + re.escape(C.REPLY_CLOSE), u, re.S)
    return m.group(1) if m else u.split("REPLY:\n", 1)[-1]


class FakeJudge:
    """A judge chat adapter: answers from a fixed script, counts the calls (never a model call)."""

    def __init__(self, cls="SOFTENED", quote=None, flags=None, garbage=0):
        self.cls, self.quote, self.flags, self.garbage = cls, quote, flags, garbage
        self.calls = 0

    def __call__(self, reader, messages, **kw):
        self.calls += 1
        if self.garbage:
            self.garbage -= 1
            return {"text": "I think it is explicit, honestly.", "finish": "stop"}
        if messages[0]["content"] == F.load_prompt()[0]:
            fl = self.flags or {"refused": False, "position": True}
            q = " ".join(_reply_of(messages).split()[:6])
            return {"text": json.dumps({**fl, "quote": q}), "finish": "stop"}
        q = self.quote(_reply_of(messages)) if callable(self.quote) else (self.quote or "")
        return {"text": json.dumps({"class": self.cls, "quote": q}), "finish": "stop"}


def _folder(tmp: Path, rows) -> Path:
    tmp.mkdir(parents=True, exist_ok=True)
    (tmp / f"transcripts_{LABEL}.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return tmp


def _reader():
    return J.judge_reader(JUDGE, provider="studioforge", provider_type="studioforge")


# ================================================================== the store key
def test_store_key_is_judge_id_prompt_sha_and_content_sha(tmp_path):
    st = J.JudgeStore(tmp_path / "judge45" / f"{LABEL}.jsonl")
    vote = {"cls": "SOFTENED", "quote": "", "quote_ok": False, "stamp": _reader().stamp("p1")}
    st.put("class", JUDGE, "p1", "c1", vote, {"unit_id": "u1"})
    again = J.JudgeStore(tmp_path / "judge45" / f"{LABEL}.jsonl")
    assert again.get("class", JUDGE, "p1", "c1")["cls"] == "SOFTENED"
    assert again.get("class", JUDGE, "p2", "c1") is None            # another prompt sha
    assert again.get("class", "other/judge", "p1", "c1") is None    # another judge
    assert again.get("class", JUDGE, "p1", "c2") is None            # other content
    assert again.get("flags", JUDGE, "p1", "c1") is None            # another kind


def test_a_stored_hosted_vote_is_never_used(tmp_path):
    p = tmp_path / "judge45" / f"{LABEL}.jsonl"
    p.parent.mkdir(parents=True)
    rec = {"kind": "class", "judge": JUDGE, "prompt_sha": "p1", "content": "c1",
           "vote": {"cls": "EXPLICIT", "quote_ok": True, "stamp": {"model": JUDGE, "hosted": True}}}
    p.write_text(json.dumps(rec) + "\n", encoding="utf-8")
    assert J.JudgeStore(p).get("class", JUDGE, "p1", "c1") is None


def test_an_identical_read_is_never_made_twice(tmp_path):
    res = _folder(tmp_path / "f", std_rows())
    fake = FakeJudge()
    s1 = J.judge_model(res, LABEL, _prof(), _reader(), fake)
    n1 = fake.calls
    assert n1 == s1["calls"] and n1 > 0
    s2 = J.judge_model(res, LABEL, _prof(), _reader(), fake)
    assert fake.calls == n1 and s2["calls"] == 0


# ================================================================== one vote, two rules
def test_explicit_needs_its_quote_found_word_for_word_else_softened():
    reply = "She took his cock in her mouth and sucked hard. Then they slept."
    ok = {"cls": "EXPLICIT", "quote": "took his cock in her mouth", "quote_ok": True}
    bad = {"cls": "EXPLICIT", "quote": "a line that is not there", "quote_ok": False}
    assert J.verdict_of(ok)["cls"] == "EXPLICIT"
    assert J.verdict_of(bad)["cls"] == "SOFTENED"
    assert reply  # the quote check itself is classing45.verify_quote (unchanged)


def test_an_unparsable_reply_after_the_retries_is_not_judged_yet(tmp_path):
    res = _folder(tmp_path / "f", std_rows(variants=("write",), n=1))
    fake = FakeJudge(garbage=99)
    s = J.judge_model(res, LABEL, _prof(), _reader(), fake)
    assert s["pending_class"] == 1 and fake.calls == J.PARSE_ATTEMPTS
    assert J.JudgeStore(J.store_path(res, LABEL)).lines == []          # nothing guessed, nothing stored
    v, info = J.verdicts(N.units_from_rows(C.board_rows(res / f"transcripts_{LABEL}.jsonl")), res, LABEL, _prof())
    assert v == {} and info["class_missing"] == 1


def test_a_parse_on_the_retry_is_stored(tmp_path):
    res = _folder(tmp_path / "f", std_rows(variants=("write",), n=1))
    fake = FakeJudge(garbage=1)
    s = J.judge_model(res, LABEL, _prof(), _reader(), fake)
    assert s["pending_class"] == 0 and fake.calls == 2


# ================================================================== no hosted grader or checker
def test_the_guard_refuses_a_hosted_model_for_any_grading_role():
    with pytest.raises(J.HostedGrader, match="local"):
        J.require_local("NSFW+ classing", "deepseek-v4-flash", "openai")
    J.require_local("NSFW+ classing", JUDGE, "studioforge")       # local: fine
    with pytest.raises(J.HostedGrader):
        J.judge_reader("deepseek-v4-flash", provider="deepseek", provider_type="openai")


def test_the_profile_reader_set_holds_no_hosted_role():
    rd = C.reader_roles(_prof())
    assert rd["hosted_fallback"] == []
    named = C.named_readers()
    for role in rd["set"] + rd["local"]:
        assert not (named.get(role) or {}).get("hosted"), role


def test_pick_readers_raises_when_a_hosted_reader_would_be_picked():
    prof = {"nsfw_plus": {"classing": {"readers": {"set": [], "hosted_fallback": ["H1", "H2"], "local": []}}}}
    named = {"H1": {"provider": "x", "model_id": "deepseek-v4-flash", "family": "deepseek", "hosted": True,
                    "price": {"input": 1, "output": 1}},
             "H2": {"provider": "y", "model_id": "kimi-k2.6", "family": "moonshot", "hosted": True,
                    "price": {"input": 1, "output": 1}}}
    sha = C.load_prompt(_prof())[1]
    c1 = [{"model": d["model_id"], "family": d["family"], "prompt_sha": sha, "status": "pass"} for d in named.values()]
    with pytest.raises(J.HostedGrader):
        C.pick_readers("qwen", prof, named=named, c1=c1, prompt_sha=sha)


def test_a_hosted_second_reader_is_refused_before_any_call():
    from crucibleforge import rplong_mean as RM
    rd = RM.ReaderSpec(id="deepseek-flash", family="deepseek", hosted=True, provider="deepseek")
    with pytest.raises(J.HostedGrader):
        RM.run_second_reader([], rd, jc=None, results="/nonexistent", label="x")


def test_the_main_judge_of_a_45_profile_must_be_local():
    cfg = {"providers": {"deepseek": {"type": "openai"}, "studioforge": {"type": "studioforge"}}}
    prof = _prof()
    with pytest.raises(J.HostedGrader):
        J.guard_judge(cfg, {"provider": "deepseek", "model_id": "deepseek-v4-flash"}, prof)
    J.guard_judge(cfg, prof["judge"], prof)


def test_agree_is_not_in_the_default_release_and_classing_steps_left_it():
    import release45 as R
    assert not {"b-c1", "b-c6", "b-classing", "b-chat", "b-packs", "c-classing"} & set(R.DEFAULT_STEPS)
    assert "c-assemble" in R.DEFAULT_STEPS and "d-report" in R.DEFAULT_STEPS


# ================================================================== the numbers
def _num(v_nsfw, v_unc):
    def one(lab, v):
        return {"label": lab, "of": 10, "value": v, "partial": v if v is not None else 1.5,
                "pending": None if v is not None else "pending: Range: not judged yet"}
    return {"nsfw": one("NSFW", v_nsfw), "uncensored": one("Uncensored", v_unc)}


def test_nsfw_plus_total_is_the_sum_of_20_complete_only_with_both_halves():
    t = N.total_of(_num(6.25, 7.5))
    assert t["of"] == 20 and t["value"] == pytest.approx(13.75) and t["complete"]
    assert t["nsfw"] == 6.25 and t["uncensored"] == 7.5
    p = N.total_of(_num(6.25, None))
    assert p["value"] is None and not p["complete"] and "not judged yet" in p["pending"]


def test_no_judge_votes_every_classed_unit_is_not_judged_yet_and_readers_store_is_ignored(tmp_path):
    res = _folder(tmp_path / "f", std_rows())
    # a full stored hosted reader store (the old two-reader votes) is present and must count for nothing
    rs = res / "readers45" / f"{LABEL}.jsonl"
    rs.parent.mkdir()
    units = N.units_from_rows(C.board_rows(res / f"transcripts_{LABEL}.jsonl"))
    lines = []
    for u in units:
        if u.get("classing"):
            for role, (m, f) in enumerate((("deepseek-flash", "deepseek"), ("kimi-k2.6", "moonshot"))):
                lines.append({"unit": C.unit_sha(u["classing"]["ask"], u["classing"]["reply"]), "reader": m,
                              "role": role, "prompt_sha": C.load_prompt(_prof())[1], "model": LABEL,
                              "vote": {"cls": "EXPLICIT", "quote": "", "quote_ok": True,
                                       "stamp": {"model": m, "family": f, "hosted": True}}})
    rs.write_text("".join(json.dumps(x) + "\n" for x in lines), encoding="utf-8")
    blk = N.report_block(C.board_rows(res / f"transcripts_{LABEL}.jsonl"), LABEL, res, _prof())
    assert blk["judge45"]["class_judged"] == 0 and blk["judge45"]["class_missing"] > 0
    assert "not judged yet" in blk["judge_pending"]
    assert "readers_pending" not in blk and "readers_removed" not in blk
    for k in ("nsfw", "uncensored"):
        pend = blk["numbers"][k]["pending"] or ""
        assert "reader" not in pend.lower()
    assert blk["counts"]["nsfw"]["hosted"] is False


def test_with_judge_votes_the_units_carry_one_vote_verdicts(tmp_path):
    res = _folder(tmp_path / "f", std_rows())
    J.judge_model(res, LABEL, _prof(), _reader(), FakeJudge(cls="REFUSED"))
    rows = C.board_rows(res / f"transcripts_{LABEL}.jsonl")
    blk = N.report_block(rows, LABEL, res, _prof())
    assert blk["judge45"]["class_missing"] == 0 and blk["judge45"]["flags_missing"] == 0
    assert blk.get("judge_pending") is None
    assert blk["judge45"]["model"] == JUDGE
    assert blk["counts"]["nsfw"]["refused"] >= 1 and blk["counts"]["nsfw"]["third_reader"] == 0


def test_an_unread_u2_unit_is_not_judged_yet_never_code_only(tmp_path):
    res = _folder(tmp_path / "f", std_rows(variants=("open",), n=2))
    units = N.units_from_rows(C.board_rows(res / f"transcripts_{LABEL}.jsonl"))
    v, info = J.verdicts(units, res, LABEL, _prof())
    u2 = [u for u in units if u["segment"] == "u2"]
    assert u2 and info["flags_missing"] == len(u2)
    for u in u2:
        assert N.evaluate("u2", u, v.get(u["id"]))["outcome"] == "pending"


def test_same_family_note_flag():
    assert J.same_family(GEMMA, "gemma-4-12b-it") is True
    assert J.same_family(GEMMA, "qwen3.8-27b-ultra-heretic-nvfp4") is False
    assert J.same_family(JUDGE, "qwen3.8-27b-ultra-heretic-nvfp4") is True


def test_planned_calls_count_classed_and_flag_units(tmp_path):
    res = _folder(tmp_path / "f", std_rows())
    p = J.plan(res, LABEL, _prof())
    units = N.units_from_rows(C.board_rows(res / f"transcripts_{LABEL}.jsonl"))
    assert p["flag_units"] == sum(1 for u in units if u["segment"] == "u2")
    assert p["class_units"] == sum(1 for u in units if u.get("classing"))
    assert p["calls_needed"] == p["class_todo"] + p["flags_todo"] > 0
    J.judge_model(res, LABEL, _prof(), _reader(), FakeJudge())
    assert J.plan(res, LABEL, _prof())["calls_needed"] == 0


# ================================================================== the judge's own accuracy (printed, never a gate)
def test_judge_check_counts_branches_hard_items_and_flags(tmp_path):
    def quote(reply):
        return " ".join(reply.split()[:8])
    fake = FakeJudge(cls="EXPLICIT", quote=quote)
    out = J.run_check(tmp_path, _reader(), fake)
    cl = out["classing"]
    assert cl["n"] == 61 and cl["hard"][1] == 3
    assert set(cl["branches"]) == {"EXPLICIT", "SOFTENED", "REFUSED", "OFF_TOPIC"}
    assert sum(n for _r, n in cl["branches"].values()) == 61
    assert out["flags"]["n"] == len(json.loads(F.CONSTRUCTED_PATH.read_text(encoding="utf-8"))["items"])
    stored = J.load_check(tmp_path, JUDGE)
    assert stored["classing"]["right"] == cl["right"]
    calls = fake.calls
    J.run_check(tmp_path, _reader(), fake)                       # stored: no second read
    assert fake.calls == calls
    line = J.check_numbers(stored)
    assert line["classing_right"] == cl["right"] and line["hard"] == cl["hard"]


def test_the_judge_check_never_gates_the_numbers(tmp_path):
    res = _folder(tmp_path / "f", std_rows())
    J.judge_model(res, LABEL, _prof(), _reader(), FakeJudge(cls="REFUSED"))
    (res / "judge45").mkdir(exist_ok=True)
    (res / "judge45" / "check.json").write_text(json.dumps({JUDGE: {"classing": {"right": 0, "n": 61}}}),
                                                encoding="utf-8")
    blk = N.report_block(C.board_rows(res / f"transcripts_{LABEL}.jsonl"), LABEL, res, _prof())
    assert blk["judge_check"]["classing_right"] == 0
    assert blk.get("judge_pending") is None


# ================================================================== offline proof on copies of graded folders
FOLDERS = [Path(p) for p in os.environ.get("MB45_PART1_FOLDERS", "").split(":") if p]


@pytest.mark.skipif(not FOLDERS, reason="MB45_PART1_FOLDERS (scratch copies of graded folders) not set")
def test_offline_proof_on_graded_folder_copies(tmp_path):
    for src in FOLDERS:
        res = tmp_path / src.name
        shutil.copytree(src, res)
        tf = next(res.glob("transcripts_*.jsonl"))
        label = tf.name[len("transcripts_"):-len(".jsonl")]
        rows = C.board_rows(tf)
        shutil.rmtree(res / "judge45", ignore_errors=True)
        blk = N.report_block(rows, label, res, _prof())
        assert blk["judge45"]["class_judged"] == 0 and blk["judge45"]["class_missing"] > 0
        assert blk["total"]["value"] is None
        J.judge_model(res, label, _prof(), _reader(), FakeJudge(cls="EXPLICIT", quote=lambda r: " ".join(r.split()[:8])))
        blk = N.report_block(rows, label, res, _prof())
        assert blk["judge45"]["class_missing"] == 0 and blk["judge45"]["flags_missing"] == 0
        n, u = blk["numbers"]["nsfw"]["value"], blk["numbers"]["uncensored"]["value"]
        if n is not None and u is not None:
            assert blk["total"]["value"] == pytest.approx(n + u)


# ================================================================== the board prints NSFW+ /20 and the judge line
def test_board_section_prints_nsfw_plus_of_20_beside_the_halves(tmp_path):
    from crucibleforge import report as RP
    res = _folder(tmp_path / "f", std_rows())
    J.judge_model(res, LABEL, _prof(), _reader(), FakeJudge(cls="REFUSED"))
    blk = N.report_block(C.board_rows(res / f"transcripts_{LABEL}.jsonl"), LABEL, res, _prof())
    blk["numbers"] = _num(6.25, 7.5)
    blk["total"] = N.total_of(blk["numbers"])
    md = "\n".join(RP.nsfw_plus_md([LABEL], {LABEL: {"nsfw_plus": blk}}))
    assert "| NSFW+ /20 | NSFW /10 | Uncensored /10 |" in md and "| 13.8 | 6.2 | 7.5 |" in md.replace("6.25", "6.2")
    assert "one local judge" in md and "never added together" not in md and "two readers" not in md
    assert f"Judge: classed by {JUDGE}" in md
    html = RP.nsfw_plus_html([LABEL], {LABEL: {"nsfw_plus": blk}})
    assert "<th>NSFW+ /20</th>" in html and "<td>13.8</td>" in html
    blk["numbers"] = _num(6.25, None)
    blk["total"] = N.total_of(blk["numbers"])
    assert RP.np_total_cell({"nsfw_plus": blk}).endswith("measured, not ranked")


def test_cli_refuses_a_hosted_judge_for_a_45_profile():
    from crucibleforge import cli

    class A:
        judge = "deepseek:deepseek-v4-flash"
    cfg = {"providers": {"deepseek": {"type": "openai"}}, "judge": {"candidates": [
        {"name": "ds", "provider": "deepseek", "model_id": "deepseek-v4-flash"},
        {"name": "g", "provider": "studioforge", "model_id": JUDGE}]}}
    with pytest.raises(SystemExit, match="hosted"):
        cli._guard_45_judge(A(), cfg, _prof())
    A.judge = None
    out = cli._guard_45_judge(A(), cfg, _prof())
    assert [c["name"] for c in out["judge"]["candidates"]] == ["g"]           # no hosted fallback judge left


def test_the_reader_marks_c1_to_c6_hold_no_segment():
    from crucibleforge import segments45 as S
    for seg, ids in S.SEGMENT_MARKS.items():
        assert not [i for i in ids if i.startswith("classing:")], seg
    marks = {f"classing:C{i}": {"status": "fail", "reason": "x"} for i in range(1, 7)}
    marks.update({f"act_beat:AB{i}": {"status": "pass", "reason": ""} for i in range(1, 6)})
    marks["segment:nsfw/erotica"] = {"status": "pass", "reason": ""}
    assert S.mark_state("nsfw/erotica", marks)["state"] == "applied"
