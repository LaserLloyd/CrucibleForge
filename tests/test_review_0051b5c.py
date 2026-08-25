"""Adversarial review of commit 0051b5c (reasoning-overflow recovery,
per-case isolation, readiness wait). Each test pins a defect found by
reading the code; they FAIL on 0051b5c/0e33cc4 until the fix lands."""
import json

import pytest

from crucibleforge import config, runner, studioforge
from crucibleforge.api import ChatResult, TransportError

from tests.test_root_causes_20260822 import _FakeProvider, _answer, _ctx, _overflow


def test_recovery_keeps_a_tool_call_answer():
    """runner.py:250 — a no-think recovery that answers with a TOOL CALL has
    empty content, so ``not r.response_text.strip()`` throws the recovered
    result away and the case is graded on the empty overflow (fail)."""
    tool_reply = ChatResult(response_text="", finish_reason="tool_calls",
                            tool_calls=[{"id": "c1", "name": "get_weather",
                                         "arguments": '{"city": "Tokyo"}'}],
                            completion_tokens=20, served_model="m")
    prov = _FakeProvider([_overflow(), tool_reply])
    ctx = _ctx(prov)
    r = ctx.call([{"role": "user", "content": "weather in Tokyo?"}], max_tokens=256,
                 tools=[{"type": "function", "function": {"name": "get_weather"}}])
    assert r.tool_calls and r.tool_calls[0]["name"] == "get_weather"
    assert r.recovery["mode"] == "no_think"


def test_recovered_row_bills_the_overflow_pass_too():
    """runner.py:259-261 + persist() — cost_usd/completion_tokens of a
    recovered row come from the recovery pass only; the 4096-token overflow
    pass that was actually billed vanishes from metrics and cost."""
    prov = _FakeProvider([_overflow(), _answer("42")])
    ctx = _ctx(prov)
    case = {"id": "M1", "category": "math", "prompt": "q", "max_tokens": 512,
            "grader": "contains", "grader_config": {"needles": ["42"]}}
    row = runner._run_single(case, {"case_id": "M1"}, ctx, 512, 0.0, 1.0, 42)
    assert row["grade"] == "pass"
    assert row["metrics"]["completion_tokens"] >= 4096 + 3


def test_wait_ready_survives_one_transient_status_failure(monkeypatch):
    """studioforge.py:56-60 — loaded_plan() returns None for BOTH "model not
    in the list" and "GET /api/status failed" (lines 134-140), so a single
    transient status error mid-load raises 'vanished' and the model FAILS."""
    polls = iter([{"state": "loading"}, studioforge.StatusUnavailable("timeout"),
                  {"state": "ready", "parallel": 2}])

    def poll(*a, **k):
        x = next(polls)
        if isinstance(x, Exception):
            raise x
        return x
    monkeypatch.setattr(studioforge, "loaded_plan", poll)
    monkeypatch.setattr(studioforge.time, "sleep", lambda s: None)
    live = studioforge.wait_ready("m", "http://x/v1", "", timeout_s=60)
    assert live["state"] == "ready"


def test_wait_ready_does_not_wait_the_full_deadline_for_a_model_that_never_appears(monkeypatch):
    """studioforge.py:56-66 — when the model never shows up in /api/status the
    loop only exits at the 900 s deadline (the 'vanished' check needs a
    previous sighting). Brief item 6: review."""
    clock = {"t": 0.0, "polls": 0}

    def never(*a, **k):
        clock["polls"] += 1
        return None

    def sleep(s):
        clock["t"] += s
    monkeypatch.setattr(studioforge, "loaded_plan", never)
    monkeypatch.setattr(studioforge.time, "sleep", sleep)
    monkeypatch.setattr(studioforge.time, "monotonic", lambda: clock["t"])
    with pytest.raises(studioforge.StudioForgeError):
        studioforge.wait_ready("m", "http://x/v1", "", timeout_s=900, poll_s=2)
    assert clock["t"] < 120, f"waited {clock['t']:.0f} s (the whole deadline) for a model that never appeared"


def test_failed_recover_does_not_relabel_a_complete_run_as_failed(tmp_path, monkeypatch):
    """runner.py:302 + 362 — `crucibleforge recover` reuses _run_one_model, which
    rewrites meta_<label>.json (new bench_run_id, failed/error). A recover
    that aborts (3 transport failures) stamps failed=True onto the meta of a
    COMPLETE run, and report._coverage then ranks that model last as FAILED."""
    monkeypatch.setattr(config, "RESULTS_DIR", tmp_path)
    cfg = {"defaults": {"context_length": 4096, "repeats": {},
                        "thinking_max_tokens_factor": 8, "thinking_max_tokens_cap": 32768},
           "providers": {"fake": {"type": "openai", "base_url": "http://127.0.0.1:9/v1"}},
           "judge": {"candidates": []}, "models": []}
    entry = {"name": "m", "model_id": "m", "provider": "fake", "thinking": True}
    cases = [{"id": f"M{i}", "category": "math", "prompt": "q", "max_tokens": 64,
              "grader": "contains", "grader_config": {"needles": ["2"]}} for i in range(3)]
    old = [{"bench_run_id": "orig", "case_id": f"M{i}", "repeat": 1, "turn": None,
            "category": "math", "finish_reason": "length", "response": "",
            "reasoning": "thinking...", "grade": "fail", "metrics": {}} for i in range(3)]
    (tmp_path / "transcripts_m.jsonl").write_text("\n".join(json.dumps(r) for r in old) + "\n")
    (tmp_path / "meta_m.json").write_text(json.dumps(
        {"model_label": "m", "bench_run_id": "orig", "failed": False, "finished": "x"}))

    from crucibleforge import providers

    def chat(self, model_id, messages, **kw):
        raise TransportError("boom")
    monkeypatch.setattr(providers.Provider, "chat", chat)
    monkeypatch.setattr(providers.Provider, "list_models", lambda self, refresh=False: {"m"})
    monkeypatch.setattr(providers.Provider, "alive", lambda self: True)
    summary = runner.recover_models(cfg, [entry], cases)
    assert summary["m"]["failed"]
    meta = json.loads((tmp_path / "meta_m.json").read_text())
    assert meta["failed"] is False, meta
    assert meta["bench_run_id"] == "orig"
