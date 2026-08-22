"""Root-cause fixes from the 2026-08-22 campaign postmortem.

1. Reasoning overflow: a thinking model that spends the whole completion
   budget in its reasoning channel returns finish=length with EMPTY content.
   The runner now recovers the answer (thinking disabled, then a continuation
   nudge) instead of scoring an empty string.
2. A llama-server 500 caused by the model's own output ("Failed to parse tool
   call arguments") is a failed CASE, not a transport failure — it must not be
   retried verbatim and must never abort the whole model run.
3. A forced --judge is strict: transient VRAM contention is retried, and the
   phase fails loudly rather than silently scoring with a different judge.
4. The judge's "parse-failures" counter no longer conflates empty generations
   (a model failure) with unparsable judge output.
5. The scorecard shows coverage so a 13-case smoke run can't be read as a
   full-suite rank.
6. StudioForge: wait for the loaded engine to report "ready" before warm-up.
"""
import json

import pytest

from gauntlet import api, config, judge, report, runner, studioforge
from gauntlet.api import ChatResult, GenerationRejected, TransportError


# ------------------------------------------------------------ 2. api errors

def test_classify_tool_call_parse_500_is_generation_rejected():
    body = ('{"message": "llama-server for \'x\' returned HTTP 500: Failed to '
            'parse tool call arguments as JSON: [json.exception.parse_error.101]"}')
    err = api.classify_server_error(500, body)
    assert isinstance(err, GenerationRejected)
    assert isinstance(err, TransportError)


def test_classify_plain_500_is_transport():
    err = api.classify_server_error(500, "internal error")
    assert isinstance(err, TransportError)
    assert not isinstance(err, GenerationRejected)


def test_generation_rejected_not_retried(monkeypatch):
    calls = []

    def boom(*a, **k):
        calls.append(1)
        raise GenerationRejected("Failed to parse tool call arguments as JSON")

    monkeypatch.setattr(api, "stream_chat", boom)
    with pytest.raises(GenerationRejected):
        api.stream_chat_retried("http://x", "", "m", [], max_tokens=8)
    assert len(calls) == 1


# -------------------------------------------------- 1. reasoning overflow

class _FakeProvider:
    """Scripted provider: .chat pops the next ChatResult and records kwargs."""
    name = "fake"
    type = "openai"

    def __init__(self, results):
        self.results = list(results)
        self.calls = []

    def chat(self, model_id, messages, **kw):
        self.calls.append({"messages": messages, **kw})
        return self.results.pop(0)

    def switch_model(self, *a, **k):
        return 0.0


def _overflow():
    return ChatResult(response_text="", reasoning_text="thinking " * 50,
                      finish_reason="length", completion_tokens=4096,
                      reasoning_tokens=4096, served_model="m")


def _answer(text="42"):
    return ChatResult(response_text=text, finish_reason="stop",
                      completion_tokens=3, served_model="m")


def _ctx(provider, thinking=True, **defaults):
    cfg = {"defaults": {"thinking_max_tokens_factor": 8,
                        "thinking_max_tokens_cap": 32768, **defaults}}
    entry = {"name": "m", "model_id": "m", "provider": "fake", "thinking": thinking}
    return runner._Ctx(cfg, entry, provider, 32768)


def test_overflow_recovered_with_thinking_disabled():
    prov = _FakeProvider([_overflow(), _answer("42")])
    ctx = _ctx(prov)
    r = ctx.call([{"role": "user", "content": "q"}], max_tokens=512)
    assert r.response_text == "42"
    assert r.recovery["mode"] == "no_think"
    assert r.recovery["first"]["finish_reason"] == "length"
    assert r.recovery["first"]["reasoning_tokens"] == 4096
    # first attempt ran on the boosted budget, recovery on the answer budget
    assert prov.calls[0]["max_tokens"] == 512 * 8
    assert prov.calls[1]["max_tokens"] == 512
    assert prov.calls[1]["extra_body"]["chat_template_kwargs"] == {"enable_thinking": False}
    # the original conversation is sent unchanged to the no-think attempt
    assert prov.calls[1]["messages"] == [{"role": "user", "content": "q"}]


def test_overflow_falls_through_to_continuation_when_template_ignores_kwarg():
    prov = _FakeProvider([_overflow(), _overflow(), _answer("final")])
    ctx = _ctx(prov)
    r = ctx.call([{"role": "user", "content": "q"}], max_tokens=256)
    assert r.response_text == "final"
    assert r.recovery["mode"] == "continue"
    msgs = prov.calls[2]["messages"]
    assert msgs[0] == {"role": "user", "content": "q"}
    assert msgs[1]["role"] == "assistant" and "thinking" in msgs[1]["content"]
    assert msgs[2]["role"] == "user" and "final answer" in msgs[2]["content"].lower()


def test_overflow_unrecoverable_keeps_first_result_and_marks_overflow():
    prov = _FakeProvider([_overflow(), _overflow(), _overflow()])
    ctx = _ctx(prov)
    r = ctx.call([{"role": "user", "content": "q"}], max_tokens=256)
    assert r.response_text == ""
    assert r.finish_reason == "length"
    assert r.recovery["mode"] is None
    assert r.recovery["attempts"] == 2


def test_no_recovery_when_answer_present_or_not_thinking():
    prov = _FakeProvider([_answer("ok")])
    ctx = _ctx(prov)
    r = ctx.call([{"role": "user", "content": "q"}], max_tokens=64)
    assert r.recovery is None and len(prov.calls) == 1
    # a non-thinking model that truncates is simply truncated (no reasoning channel)
    prov2 = _FakeProvider([ChatResult(response_text="", finish_reason="length",
                                      served_model="m")])
    r2 = _ctx(prov2, thinking=False).call([{"role": "user", "content": "q"}], max_tokens=64)
    assert r2.recovery is None and len(prov2.calls) == 1


def test_recovery_can_be_disabled_in_config():
    prov = _FakeProvider([_overflow()])
    ctx = _ctx(prov, reasoning_overflow_recovery=False)
    r = ctx.call([{"role": "user", "content": "q"}], max_tokens=64)
    assert r.response_text == "" and r.recovery is None and len(prov.calls) == 1


def test_recovery_survives_provider_rejecting_template_kwargs():
    from gauntlet.api import RequestRejected

    class Rejecting(_FakeProvider):
        def chat(self, model_id, messages, **kw):
            self.calls.append({"messages": messages, **kw})
            if "chat_template_kwargs" in (kw.get("extra_body") or {}):
                raise RequestRejected(400, "unknown parameter chat_template_kwargs")
            return self.results.pop(0)

    prov = Rejecting([_overflow(), _answer("final")])
    ctx = _ctx(prov)
    r = ctx.call([{"role": "user", "content": "q"}], max_tokens=128)
    assert r.response_text == "final" and r.recovery["mode"] == "continue"
    assert ctx.no_think_supported is False
    assert "chat_template_kwargs" not in (prov.calls[-1]["extra_body"] or {})
    # next overflow skips the rejected rung entirely
    prov.results = [_overflow(), _answer("again")]
    n = len(prov.calls)
    r2 = ctx.call([{"role": "user", "content": "q2"}], max_tokens=128)
    assert r2.response_text == "again" and len(prov.calls) == n + 2


def test_overflow_rows_detected_for_recover_command():
    rows = [
        {"case_id": "A", "repeat": 1, "turn": None, "category": "coding",
         "bench_run_id": "r1", "finish_reason": "length", "response": "",
         "reasoning": "lots of thinking"},
        {"case_id": "B", "repeat": 1, "turn": None, "category": "coding",
         "bench_run_id": "r1", "finish_reason": "stop", "response": "done",
         "reasoning": ""},
        # multi-turn: turn 2 overflowed -> the whole case (A2, repeat 1) re-runs
        {"case_id": "A2", "repeat": 1, "turn": 2, "category": "rp",
         "bench_run_id": "r1", "finish_reason": "length", "response": "",
         "reasoning": "x"},
        # perf rows are timing rows — never recovered
        {"case_id": "P1", "repeat": 1, "turn": None, "category": "perf",
         "bench_run_id": "r1", "finish_reason": "length", "response": "",
         "reasoning": "x"},
        # already recovered once and still empty: not retried forever
        {"case_id": "C", "repeat": 1, "turn": None, "category": "math",
         "bench_run_id": "r1", "finish_reason": "length", "response": "",
         "reasoning": "x", "recovery": {"mode": None, "attempts": 2}},
    ]
    found = runner.overflow_jobs(rows)
    assert found == {("r1", "A", 1), ("r1", "A2", 1)}


# ------------------------------------------- 2. per-case failure isolation

def _fake_cases():
    return [
        {"id": "T1", "category": "tooluse", "prompt": "call it", "max_tokens": 64,
         "grader": "tool_call", "grader_config": {"expect_tool": "f"}},
        {"id": "I1", "category": "instruct", "prompt": "say ok", "max_tokens": 64,
         "grader": "contains", "grader_config": {"needles": ["ok"]}},
    ]


def test_generation_rejected_scores_case_fail_and_run_continues(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "RESULTS_DIR", tmp_path)
    cfg = {"defaults": {"context_length": 4096, "repeats": {}},
           "providers": {"fake": {"type": "openai", "base_url": "http://127.0.0.1:9/v1"}},
           "judge": {"candidates": []}, "models": []}
    entry = {"name": "m", "model_id": "m", "provider": "fake", "thinking": False}

    from gauntlet import providers

    def chat(self, model_id, messages, **kw):
        if "call it" in messages[-1]["content"]:
            raise GenerationRejected("HTTP 500: Failed to parse tool call arguments as JSON")
        return _answer("ok")

    monkeypatch.setattr(providers.Provider, "chat", chat)
    monkeypatch.setattr(providers.Provider, "list_models", lambda self, refresh=False: {"m"})
    monkeypatch.setattr(providers.Provider, "alive", lambda self: True)
    summary = runner.run_models(cfg, [entry], _fake_cases())
    assert summary["m"]["failed"] is False
    rows = {r["case_id"]: r for r in config.load_transcripts("m")}
    assert rows["T1"]["grade"] == "fail"
    assert "rejected" in rows["T1"]["grade_detail"].lower()
    assert rows["T1"]["error"]
    assert rows["I1"]["grade"] == "pass"
    meta = json.loads((tmp_path / "meta_m.json").read_text())
    assert meta["failed"] is False and meta.get("finished")


def test_consecutive_transport_errors_abort_model(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "RESULTS_DIR", tmp_path)
    cfg = {"defaults": {"context_length": 4096, "repeats": {}},
           "providers": {"fake": {"type": "openai", "base_url": "http://127.0.0.1:9/v1"}},
           "judge": {"candidates": []}, "models": []}
    entry = {"name": "m", "model_id": "m", "provider": "fake", "thinking": False}
    from gauntlet import providers

    def chat(self, model_id, messages, **kw):
        raise TransportError("connection refused")

    monkeypatch.setattr(providers.Provider, "chat", chat)
    monkeypatch.setattr(providers.Provider, "list_models", lambda self, refresh=False: {"m"})
    monkeypatch.setattr(providers.Provider, "alive", lambda self: True)
    cases = [{"id": f"I{i}", "category": "instruct", "prompt": "x", "max_tokens": 8,
              "grader": "contains", "grader_config": {"needles": ["ok"]}} for i in range(6)]
    summary = runner.run_models(cfg, [entry], cases)
    assert summary["m"]["failed"] is True
    assert "consecutive" in summary["m"]["error"]
    # every attempted case left an error row (nothing silently vanished)
    rows = config.load_transcripts("m")
    assert rows and all(r.get("error") for r in rows)
    assert len(rows) == runner.MAX_CONSECUTIVE_TRANSPORT_ERRORS


# ------------------------------------------------------ 3. strict judge

def _judge_cfg():
    return {"providers": {"sf": {"type": "studioforge", "base_url": "http://127.0.0.1:9/v1"}},
            "judge": {"candidates": [
                {"provider": "sf", "model_id": "big", "thinking": True},
                {"provider": "sf", "model_id": "small"}],
                "load_retry_s": [1, 2]},
            "models": [], "defaults": {}}


def test_forced_judge_retries_transient_load_then_succeeds(monkeypatch):
    attempts = []
    sleeps = []

    def load(self):
        attempts.append(self.model_id)
        if len(attempts) < 3:
            raise studioforge.StudioForgeError("Cannot load 'big' entirely in VRAM")
        return 1.0

    monkeypatch.setattr(judge.JudgeClient, "load", load)
    monkeypatch.setattr(judge, "_eligible_judge_candidates", lambda cfg, b: [])
    monkeypatch.setattr(judge.time, "sleep", lambda s: sleeps.append(s))
    jc = judge._load_judge(_judge_cfg(), set(), rows=1, samples=1, override="sf:big")
    assert jc.model_id == "big"
    assert attempts == ["big", "big", "big"]
    assert sleeps == [1, 2]


def test_forced_judge_never_silently_falls_back(monkeypatch):
    attempts = []

    def load(self):
        attempts.append(self.model_id)
        raise studioforge.StudioForgeError("Cannot load 'big' entirely in VRAM")

    monkeypatch.setattr(judge.JudgeClient, "load", load)
    monkeypatch.setattr(judge, "_eligible_judge_candidates",
                        lambda cfg, b: [{"provider": "sf", "model_id": "small"}])
    monkeypatch.setattr(judge.time, "sleep", lambda s: None)
    with pytest.raises(judge.JudgeError) as ei:
        judge._load_judge(_judge_cfg(), set(), rows=1, samples=1, override="sf:big")
    assert "small" not in attempts
    assert "--judge-fallback" in str(ei.value)


def test_forced_judge_fallback_is_opt_in(monkeypatch):
    attempts = []

    def load(self):
        attempts.append(self.model_id)
        if self.model_id == "big":
            raise studioforge.StudioForgeError("Cannot load 'big' entirely in VRAM")
        return 0.5

    monkeypatch.setattr(judge.JudgeClient, "load", load)
    monkeypatch.setattr(judge, "_eligible_judge_candidates",
                        lambda cfg, b: [{"provider": "sf", "model_id": "small"}])
    monkeypatch.setattr(judge.time, "sleep", lambda s: None)
    jc = judge._load_judge(_judge_cfg(), set(), rows=1, samples=1, override="sf:big",
                           allow_fallback=True)
    assert jc.model_id == "small"


# ------------------------------------------------------- 4. judge counts

def test_run_judge_separates_empty_generations_from_parse_failures(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "RESULTS_DIR", tmp_path)
    rows = [
        {"bench_run_id": "r", "case_id": "E", "repeat": 1, "turn": None, "rubric": "nsfw",
         "needs_judge": True, "response": "", "reasoning": "thinking", "prompt": "p"},
        {"bench_run_id": "r", "case_id": "G", "repeat": 1, "turn": None, "rubric": "nsfw",
         "needs_judge": True, "response": "a scene", "prompt": "p"},
        {"bench_run_id": "r", "case_id": "B", "repeat": 1, "turn": None, "rubric": "nsfw",
         "needs_judge": True, "response": "another scene", "prompt": "p"},
    ]
    (tmp_path / "transcripts_m.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n")

    class FakeJC:
        model_id = "j"
        concurrency = 1
        thinking = False
        label = "j @ sf"

    def fake_judge_row(jc, row, samples=1):
        if not judge._has_content(row):
            return {"judge_failed": True, "empty_generation": True, "judge_raw": "",
                    "refused": False, "scores": None}
        if row["case_id"] == "B":
            return {"judge_failed": True, "judge_raw": "garbage", "refused": False,
                    "scores": None}
        return {"judge_failed": False, "judge_raw": "{}", "refused": False,
                "scores": {"prose": 8, "emotion": 8, "erotic": 8, "explicitness": 5,
                           "refused": False, "sanitized": False, "note": ""}}

    monkeypatch.setattr(judge, "_load_judge", lambda *a, **k: FakeJC())
    monkeypatch.setattr(judge, "run_canary", lambda jc: None)
    monkeypatch.setattr(judge, "judge_row", fake_judge_row)
    cfg = {"models": [{"name": "m", "model_id": "m", "provider": "sf"}],
           "judge": {"samples": 1}, "providers": {}, "defaults": {}}
    res = judge.run_judge(cfg, ["m"])
    assert res["judged"] == 3
    assert res["failed"] == 1      # only the genuinely unparsable verdict
    assert res["empty"] == 1       # the model produced no content


# ------------------------------------------------------- 5. coverage

def _row(cid, cat="instruct", grade="pass", rev="3.0.0+abc"):
    return {"bench_run_id": "r", "bench_revision": rev, "case_id": cid, "repeat": 1,
            "turn": None, "category": cat, "grade": grade, "metrics": {}}


def test_report_coverage_marks_partial_runs(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "RESULTS_DIR", tmp_path)
    monkeypatch.setattr(report, "_expected_case_count", lambda cfg, profile: 200)
    monkeypatch.setattr(report, "_current_revision", lambda cfg: "3.0.0+abc")
    for label, n in (("full", 200), ("smoke", 13)):
        (tmp_path / f"transcripts_{label}.jsonl").write_text(
            "\n".join(json.dumps(_row(f"C{i}")) for i in range(n)) + "\n")
        (tmp_path / f"meta_{label}.json").write_text(json.dumps({"device": "sf", "failed": False}))
    (tmp_path / "transcripts_dead.jsonl").write_text(json.dumps(_row("C0")) + "\n")
    (tmp_path / "meta_dead.json").write_text(json.dumps({"device": "sf", "failed": True,
                                                         "error": "HTTP 500"}))
    stats = {l: report.model_stats(l) for l in ("full", "smoke", "dead")}
    assert stats["full"]["coverage"]["complete"] is True
    assert stats["smoke"]["coverage"]["complete"] is False
    assert stats["smoke"]["coverage"]["cases"] == 13
    assert stats["dead"]["coverage"]["status"].startswith("FAILED")
    md = report.render_markdown(["full", "smoke", "dead"], stats, None)
    assert "Coverage" in md
    scorecard = md.split("## Scorecard")[1].split("## Summary")[0]
    # complete runs rank above partial/failed ones regardless of score
    assert scorecard.index("| full |") < scorecard.index("| smoke |") < scorecard.index("| dead |")
    assert "13/200" in scorecard and "FAILED" in scorecard


def test_report_coverage_flags_stale_revision(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "RESULTS_DIR", tmp_path)
    monkeypatch.setattr(report, "_expected_case_count", lambda cfg, profile: 1)
    monkeypatch.setattr(report, "_current_revision", lambda cfg: "3.0.0+new")
    (tmp_path / "transcripts_old.jsonl").write_text(json.dumps(_row("C0", rev="3.0.0+old")) + "\n")
    stats = report.model_stats("old")
    assert stats["coverage"]["stale"] is True
    md = report.render_markdown(["old"], {"old": stats}, None)
    assert "stale" in md


def test_report_notes_reasoning_overflow_recoveries(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "RESULTS_DIR", tmp_path)
    rows = [
        {**_row("C1", cat="coding"), "reasoning_overflow": True,
         "recovery": {"mode": "no_think", "attempts": 1}},
        {**_row("C2", cat="coding", grade="fail"), "reasoning_overflow": True,
         "recovery": {"mode": None, "attempts": 2}, "truncated": True},
    ]
    (tmp_path / "transcripts_m.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    stats = report.model_stats("m")
    assert stats["reasoning_overflow"] == {"n": 2, "recovered": 1}
    md = report.render_markdown(["m"], {"m": stats}, None)
    assert "reasoning overflow" in md.lower()


# ------------------------------------------------ 6. studioforge readiness

def test_load_model_waits_for_ready_before_warmup(monkeypatch):
    # load_model first asks "already loaded?" (consumes one poll), then
    # wait_ready polls: loading, loading, ready
    states = iter([{"state": "loading"}, {"state": "loading"}, {"state": "loading"},
                   {"state": "ready", "parallel": 2, "ctx_size": 32768, "devices": [0]}])
    seen = []
    monkeypatch.setattr(studioforge, "load_recommended",
                        lambda *a, **k: {"plan": {"ctx_size": 32768, "parallel": 2}})
    monkeypatch.setattr(studioforge, "loaded_plan",
                        lambda *a, **k: next(states, {"state": "ready", "parallel": 2}))
    monkeypatch.setattr(studioforge, "warm_model", lambda *a, **k: seen.append("warm") or 0.1)
    monkeypatch.setattr(studioforge.time, "sleep", lambda s: seen.append("sleep"))
    studioforge.load_model("m", "http://x/v1", "", 32768)
    # two "loading" polls slept, then warm-up ran exactly once
    assert seen.count("sleep") == 2 and seen.count("warm") == 1
    assert seen.index("warm") > seen.index("sleep")


def test_wait_ready_gives_up_when_model_vanishes(monkeypatch):
    monkeypatch.setattr(studioforge, "loaded_plan", lambda *a, **k: None)
    monkeypatch.setattr(studioforge.time, "sleep", lambda s: None)
    with pytest.raises(studioforge.StudioForgeError):
        studioforge.wait_ready("m", "http://x/v1", "", timeout_s=5)
