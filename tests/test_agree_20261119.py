"""lane-j step 1: the second reader (crucibleforge.agree) on synthetic folders; no model calls."""
import json
import math

import pytest

from crucibleforge import agree as A
from crucibleforge import rpladder as RL

GEMMA = "someone/gemma-4-31B-it-uncensored-heretic-GGUF/gemma-4-31B-it-uncensored-heretic-Q8_0"
QWEN = "someone/Qwen3.8-27B-Uncensored-Heretic-GGUF/Qwen3.8-27B-Q8_0"


# ------------------------------------------------------------------ statistics
def test_ac1_stays_high_where_kappa_collapses_on_a_skewed_base_rate():
    pairs = [(True, True)] * 90 + [(True, False)] * 5 + [(False, True)] * 5
    assert A.raw_agreement(pairs) == pytest.approx(0.90)
    assert A.cohen_kappa(pairs) == pytest.approx((0.90 - 0.905) / 0.095)        # about -0.05
    assert A.gwet_ac1(pairs) == pytest.approx((0.90 - 0.095) / 0.905)          # about 0.89


def test_ac1_equals_kappa_on_balanced_classes():
    pairs = [(1, 1)] * 40 + [(0, 0)] * 40 + [(1, 0)] * 10 + [(0, 1)] * 10
    assert A.gwet_ac1(pairs) == pytest.approx(A.cohen_kappa(pairs))
    assert A.gwet_ac1([(1, 1)] * 5) == 1.0


def test_judge_sd_recovers_a_known_reader_error():
    import random
    rnd = random.Random(7)
    sigma, n = 0.10, 4000
    # two independent readers, each truth + N(0, sigma): one reader's error on the mean of n units is sigma/sqrt(n)
    diffs = [rnd.gauss(0, sigma) - rnd.gauss(0, sigma) for _ in range(n)]
    assert A.judge_sd(diffs) == pytest.approx(sigma / math.sqrt(n), rel=0.08)
    # a systematic leniency gap g between the readers adds g/sqrt(2) that never shrinks with n
    assert A.judge_sd([d + 0.06 for d in diffs]) == pytest.approx(math.hypot(sigma / math.sqrt(n), 0.06 / math.sqrt(2)),
                                                                  rel=0.05)


# ------------------------------------------------------------------ rules
def test_reader_of_the_judges_family_is_refused():
    with pytest.raises(A.ReaderRefused, match="judge's family"):
        A.check_reader("other/gemma-3-27b-it-Q8_0", GEMMA)
    A.check_reader(QWEN, GEMMA)
    with pytest.raises(A.ReaderRefused, match="no known family"):
        A.check_reader("acme/mystery-7b", GEMMA)
    with pytest.raises(A.ReaderRefused, match="cap"):
        A.check_reader("deepseek-v4-flash", GEMMA, hosted=True, cap_usd=None)
    A.check_reader("deepseek-v4-flash", GEMMA, hosted=True, cap_usd=2.0)


# ------------------------------------------------------------------ long scenes
def _verdict(passes: dict[str, bool]) -> dict:
    return {"judge_failed": False, "refused": False, "scores": None,
            "policy": {"kind": "elements", "mode": "elements", "samples": 1, "thinking": False, "batch": 8},
            "elements": {"results": [{"id": k, "type": "judge", "pass": v, "judge_pass": v, "weight": 1.0}
                                     for k, v in passes.items()], "unjudged": []}}


def _long_row(case_id: str, passes: dict[str, bool]) -> dict:
    return {"case_id": case_id, "rubric": "rp_deep", "repeat": 1, "bench_run_id": "r1",
            "conversation": [{"role": "system", "content": "s"}, {"role": "user", "content": "u"},
                             {"role": "assistant", "content": f'"{case_id}," she says.'}],
            "judge": _verdict(passes), "judge_model": GEMMA}


def _folder(tmp_path, label, rows):
    d = tmp_path / label
    d.mkdir()
    p = d / f"transcripts_{label}.jsonl"
    p.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return d, p


def test_long_scenes_regraded_into_a_separate_file(tmp_path, monkeypatch):
    facts = {f"F{i}": True for i in range(10)}
    rows = [_long_row(c, facts) for c in A.LONG_CASES[:4]]
    d, p = _folder(tmp_path, "deepseek-m1", rows)
    before = p.read_bytes()
    calls = []

    def fake_regrade(jc, row, thinking=False):
        calls.append(row["case_id"])
        assert thinking is True                                       # the reader thinks by default
        assert "judge" not in row or row["judge"]["policy"]          # the stored policy travels
        v = dict(facts)
        if row["case_id"] == A.LONG_CASES[0]:
            v["F0"] = v["F1"] = False                                 # the reader fails two facts on one case
        return _verdict(v)
    monkeypatch.setattr(A, "regrade_long", fake_regrade)
    recs = A.run([d], object(), QWEN, parts=(A.LONG_PART,), cases={})
    assert p.read_bytes() == before                                   # production verdicts untouched
    assert A.second_path(d, A.reader_key(QWEN, True)).exists()
    s = A.summarize(recs)[A.LONG_PART]
    assert (s["n_units"], s["n_decisions"]) == (4, 40)
    assert s["raw"] == pytest.approx(38 / 40)
    assert s["mad"] == pytest.approx(0.2 / 4)
    # a resumed run reads its records and asks nothing again
    calls.clear()
    recs2 = A.run([d], object(), QWEN, parts=(A.LONG_PART,), cases={})
    assert calls == [] and len(recs2) == 4
    assert "chat/j_rplong" in A.render_table(A.summarize(recs2), QWEN, GEMMA)


def test_run_refuses_a_reader_of_the_folders_judge_family(tmp_path):
    d, _ = _folder(tmp_path, "m1", [_long_row(A.LONG_CASES[0], {"F": True})])
    with pytest.raises(A.ReaderRefused):
        A.run([d], object(), "x/gemma-3-12b-Q8_0", parts=(A.LONG_PART,), cases={})


def test_same_order_and_correlation_per_model():
    def rec(m, sj, sr, u):
        return {"part": "p", "model": m, "unit": u, "row_sha": u, "decisions": [], "score_judge": sj,
                "score_reader": sr, "weight": 1.0}
    a, b = "deepseek-a", "grok-b"
    recs = [rec(a, .9, .8, "1"), rec(a, .7, .6, "2"), rec(a, .5, .45, "3"),
            rec(b, .4, .5, "4"), rec(b, .3, .35, "5"), rec(b, .2, .1, "6")]
    for r in recs:
        r["reader"] = QWEN
    s = A.summarize(recs)["p"]
    assert s["same_order"] and s["order_judge"] == [a, b]
    assert s["corr"][a] > 0.95
    more = [dict(rec(b, .2, .99, u), reader=QWEN) for u in "789"]
    flip = A.summarize(recs + more)["p"]
    assert not flip["same_order"]


# ------------------------------------------------------------------ sessions (real ladder file, fake readers)
def _session_setup():
    cards = RL.load()
    cid = sorted(cards)[0]
    card = cards[cid]
    lad = card["candidates"][card["order"][0]]
    check = {"cells": {f"{fk}|{ax}": {"usable": True} for fk in RL.flat(cards) for ax in RL.AXES},
             "checked": list(RL.flat(cards))}
    rows = {}
    for seed in (1, 2):
        replies = [r + f" (seed {seed})" for r in lad["rungs"]["3"]["replies"]]
        conv = [{"role": "system", "content": card["system"]}]
        for u, a in zip(card["turns"], replies):
            conv += [{"role": "user", "content": u}, {"role": "assistant", "content": a}]
        rows[f"U{seed}"] = {"case_id": f"U{seed}", "model_id": "deepseek-m", "conversation": conv, "rubric": "rp_long",
                            "chain": {"part": A.SESSION_PART, "id": f"U{seed}", "gen_seed": seed}}
    return cards, check, rows


def _judge_fn(answer):
    def fn(reqs):
        return {r["key"]: {"better": answer(r), "reason": "x"} for r in reqs}
    return fn


def _subject_is(r):
    return r["subj_is"]          # the subject always wins: placed at the top


def test_sessions_reader_agrees_with_itself_and_splits_when_it_sees_no_difference(tmp_path):
    cards, check, rows = _session_setup()
    prod = RL.place_units([dict(RL.unit_from_row(r, cards), unit=u) for u, r in rows.items()], cards,
                          _judge_fn(_subject_is), RL.VerdictCache(None), judge_model=GEMMA, check=check,
                          windows_used=(1, 3))
    for u, r in rows.items():
        r["judge_ladder"] = prod[u]
        assert prod[u]["complete"] and prod[u]["position"] == 4.0
    same = A.session_records("deepseek-m", rows, cards, _judge_fn(_subject_is), RL.VerdictCache(None), QWEN, check)
    s = A.summarize(same)[A.SESSION_PART]
    assert s["raw"] == 1.0 and s["mad"] == 0.0 and s["n_decisions"] > 0
    none = A.session_records("deepseek-m", rows, cards, _judge_fn(lambda r: "none"), RL.VerdictCache(None), QWEN, check)
    s2 = A.summarize(none)[A.SESSION_PART]
    assert s2["raw"] == 0.0                          # every comparison 1 (judge) vs .5 (reader)
    assert all(r["score_reader"] == 1.5 / 4 for r in none)      # R2 split -> 1.5
    # the judged half of a session piece is half its points: the judge term carries that weight
    assert s2["judge_sd"]["deepseek-m"] == pytest.approx(A.judge_sd([0.5 * (1.0 - 1.5 / 4)] * 2))


def test_adult_bands():
    assert A.adult_bands({"explicit": 7, "craft": 3.4, "flag": True, "x": "a"}) == {"explicit": "high", "craft": "low"}


def test_capped_client_stops_at_the_cap():
    class R:
        usage = {"prompt_tokens": 1_000_000, "completion_tokens": 0}

    class J:
        def chat(self, m, **kw):
            return R()
    c = A.CappedClient(J(), {"input": 1.0, "output": 2.0}, 1.5)
    c.chat([])
    c.chat([])
    with pytest.raises(A.SpendCapReached):
        c.chat([])


def test_cli_agree_plan_counts_units_and_refuses_the_judges_family(tmp_path, capsys):
    from crucibleforge import cli
    d, _ = _folder(tmp_path, "deepseek-m1", [_long_row(c, {"F": True}) for c in A.LONG_CASES])
    cfg = {"providers": {"studioforge": {"type": "studioforge", "base_url": "http://x"}}, "models": []}
    import argparse
    ns = argparse.Namespace(folders=[str(d)], reader=[f"studioforge:{QWEN}"], parts=None, adult=False,
                            cap_usd=None, plan=True, out=None, judge_devices="2,3", judge_parallel=None,
                            judge_allow_devices="3", judge_instance_name=None, reader_thinking="on")
    assert cli.cmd_agree(ns, cfg) == 0
    assert "8 judged units" in capsys.readouterr().out
    ns.reader = ["studioforge:x/gemma-3-27b-Q8_0"]
    assert cli.cmd_agree(ns, cfg) == 2


# ------------------------------------------------------------------ follow-up 2: the reader thinks by default
class _RecJC:
    thinking = True
    extra_body = None
    concurrency = 2

    def __init__(self):
        self.kw = []

    def chat(self, messages, **kw):
        self.kw.append(kw)

        class R:
            response_text = '{"better": "A", "reason": "r"}'
        return R()


def test_regrade_long_turns_the_readers_thinking_on(monkeypatch):
    from crucibleforge import judge as J
    seen = {}
    monkeypatch.setattr(J, "judge_row", lambda jc, row, samples=1: seen.update(row["_judge_policy"]) or {})
    row = _long_row(A.LONG_CASES[0], {"F": True})
    A.regrade_long(object(), row, thinking=True)
    assert seen["thinking"] is True and seen["max_tokens"] >= A.READER_THINK_MAX_TOKENS
    seen.clear()
    A.regrade_long(object(), row, thinking=False)
    assert seen["thinking"] is False                                   # off = the production policy as stored


def test_reader_pair_calls_keep_thinking_on_and_off_matches_production():
    from crucibleforge.judge import _NO_THINK
    jc = _RecJC()
    out = A.reader_judge_fn(jc, {}, True)([{"key": "k", "messages": [{"role": "user", "content": "x"}]}])
    assert out == {"k": {"better": "A", "reason": "r"}}
    assert "extra_body" not in jc.kw[0] and jc.kw[0]["max_tokens"] >= A.READER_THINK_MAX_TOKENS
    jc = _RecJC()
    A.reader_judge_fn(jc, {}, False)([{"key": "k", "messages": [{"role": "user", "content": "x"}]}])
    assert (jc.kw[0].get("extra_body") or {}).get("chat_template_kwargs") == _NO_THINK["chat_template_kwargs"]


def test_reader_key_keeps_a_thinking_reading_apart():
    assert A.reader_key(QWEN, True) != A.reader_key(QWEN, False) == QWEN
    assert A.slug(A.reader_key(QWEN, True)).endswith("-think")


# ------------------------------------------------------------------ follow-up 3e: reader of another family than BOTH
def test_a_row_of_the_readers_own_family_is_not_measured_and_a_second_reader_takes_it(tmp_path, monkeypatch):
    rows = [dict(_long_row(c, {"F": True}), model_id="unsloth/Qwen3.8-27B-GGUF/Qwen3.8-27B-Q8_0")
            for c in A.LONG_CASES[:2]]
    d, _ = _folder(tmp_path, "qwen-ref", rows)
    asked = []
    monkeypatch.setattr(A, "regrade_long", lambda jc, row, thinking=False: asked.append(1) or _verdict({"F": True}))
    recs = A.run([d], object(), QWEN, parts=(A.LONG_PART,), cases={})
    assert asked == [] and all(r["not_measured"] == "reader and graded model are both qwen" for r in recs)
    s = A.summarize(recs)[A.LONG_PART]
    assert s["n_units"] == 0 and s["not_measured_by_model"] == {"qwen-ref": {"reader and graded model are both qwen": 2}}
    recs += A.run([d], object(), "someone/Mistral-Small-3.2-24B-Q8_0", parts=(A.LONG_PART,), cases={})
    s = A.summarize(recs)[A.LONG_PART]
    assert len(asked) == 2 and s["n_units"] == 2 and not s["not_measured_by_model"]


def test_an_old_record_of_a_clashing_reader_is_left_out_on_summary():
    rec = {"part": A.LONG_PART, "model": "qwen3.8-27b-q8-ref", "unit": "u", "row_sha": "s", "reader": QWEN,
           "decisions": [[True, True]], "score_judge": 1.0, "score_reader": 1.0, "weight": 1.0}
    s = A.summarize([rec])[A.LONG_PART]
    assert s["n_units"] == 0 and "qwen3.8-27b-q8-ref" in s["not_measured_by_model"]


def test_session_placement_agreement_is_distance_weighted():
    recs = [{"part": A.SESSION_PART, "model": "deepseek-a", "unit": str(i), "row_sha": str(i), "reader": QWEN,
             "decisions": [[1.0, 0.5]], "score_judge": sj / 4, "score_reader": sr / 4, "weight": 0.5}
            for i, (sj, sr) in enumerate([(2, 2), (2, 3), (3, 2.5), (1, 1)])]
    pb = A.summarize(recs)[A.SESSION_PART]["placement"]
    assert pb["weighted"] == pytest.approx(1 - (0 + 1 + 0.5 + 0) / 4 / 4)
    assert pb["within_half_rung"] == pytest.approx(0.75) and pb["bias_rungs"] == pytest.approx(0.125)


def test_a_long_scene_the_reader_left_unscorable_is_asked_again_on_resume(tmp_path, monkeypatch):
    facts = {f"F{i}": True for i in range(4)}
    d, _ = _folder(tmp_path, "deepseek-m9", [_long_row(c, facts) for c in A.LONG_CASES[:2]])
    calls = []
    bad = {"on": True}
    real = A.long_score

    def fake_score(row, verdict, case):
        if bad["on"] and row["case_id"] == A.LONG_CASES[0] and verdict is not row.get("judge"):
            return None                                               # the reader's verdict on case 0 is unscorable
        return real(row, verdict, case)
    monkeypatch.setattr(A, "long_score", fake_score)
    monkeypatch.setattr(A, "regrade_long", lambda jc, row, thinking=False: calls.append(row["case_id"]) or _verdict(facts))
    A.run([d], object(), QWEN, parts=(A.LONG_PART,), cases={})
    assert sorted(calls) == sorted(A.LONG_CASES[:2])
    calls.clear()
    bad["on"] = False
    A.run([d], object(), QWEN, parts=(A.LONG_PART,), cases={})
    assert calls == [A.LONG_CASES[0]]                                 # only the unscored scene is read again
