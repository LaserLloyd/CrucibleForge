"""claude_code provider: a fake ``claude`` executable on PATH (no real calls)."""
from __future__ import annotations

import json
import os
import stat
import sys

import pytest

from crucibleforge import claudecode
from crucibleforge.api import RequestRejected, TransportError
from crucibleforge.config import validate_config
from crucibleforge.providers import clear_cache, get_provider

FAKE = r'''#!{py}
import json, os, sys
log = os.environ["FAKE_CLAUDE_LOG"]
prompt = sys.stdin.read()
mode = os.environ.get("FAKE_CLAUDE_MODE", "ok")
argv = sys.argv[1:]
rec = {{"argv": argv, "prompt": prompt, "cwd": os.getcwd(), "empty_cwd": os.listdir("."),
        "env_anthropic": sorted(k for k in os.environ if k.startswith("ANTHROPIC_"))}}
open(log, "a").write(json.dumps(rec) + "\n")
if mode == "limit":
    print(json.dumps({{"type": "result", "is_error": True,
                       "result": "Claude AI usage limit reached|1700000000"}}))
    sys.exit(1)
if mode == "rate":
    print(json.dumps({{"type": "result", "is_error": True, "api_error_status": 429,
                       "result": "slow down"}}))
    sys.exit(1)
if mode == "crash":
    sys.stderr.write("boom"); sys.exit(2)
n = sum(1 for _ in open(log))
sid = argv[argv.index("--resume") + 1] if "--resume" in argv else "sess-%d" % n
print(json.dumps({{"type": "result", "subtype": "success", "is_error": False,
    "result": "PONG" if n == 1 else "turn %d" % n, "session_id": sid,
    "stop_reason": "end_turn", "duration_ms": 1500, "duration_api_ms": 1200,
    "total_cost_usd": 0.5,
    "usage": {{"input_tokens": 3, "output_tokens": 7,
               "cache_read_input_tokens": 100, "cache_creation_input_tokens": 20}}}}))
'''


@pytest.fixture
def fake(tmp_path, monkeypatch):
    if sys.platform == "win32":
        pytest.skip("the stand-in executable is a shebang script, which Windows cannot run")
    d = tmp_path / "bin"
    d.mkdir()
    exe = d / "claude"
    exe.write_text(FAKE.format(py=sys.executable), encoding="utf-8")
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    log = tmp_path / "calls.jsonl"
    monkeypatch.setenv("PATH", f"{d}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_CLAUDE_LOG", str(log))
    monkeypatch.setenv("CRUCIBLEFORGE_CC_TMP", str(tmp_path / "cc-tmp"))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-must-not-leak")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://nowhere")
    monkeypatch.setenv("CF_CC_COUNTER", str(tmp_path / "cc_counter_default.json"))
    for k in ("CF_CC_MAX_CALLS", "CF_CC_MAX_TOKENS", "CF_CC_WINDOW"):
        monkeypatch.delenv(k, raising=False)
    claudecode.reset_guard()
    claudecode._SESSIONS.clear()
    clear_cache()

    def calls():
        return [json.loads(x) for x in log.read_text(encoding="utf-8").splitlines()]
    return calls


def _prov():
    cfg = {"providers": {"cc": {"type": "claude_code"}},
           "models": [{"name": "m-cc", "provider": "cc", "model_id": "claude-haiku-5-5"}],
           "judge": {"candidates": [{"provider": "cc", "model_id": "x"}]},
           "defaults": {}}
    validate_config(cfg)
    return get_provider(cfg, "cc")


def test_config_needs_no_base_url_and_concurrency_is_one(fake):
    p = _prov()
    assert p.type == "claude_code" and p.concurrency == 1
    assert p.alive() and p.is_available("claude-haiku-5-5")


def test_single_turn_argv_usage_and_isolation(fake):
    p = _prov()
    r = p.chat("claude-haiku-5-5", [{"role": "system", "content": "SYS"},
                                    {"role": "user", "content": "Reply with PONG."}],
               max_tokens=50, temperature=0.7, seed=1)
    c = fake()[0]
    a = c["argv"]
    assert a[:1] == ["-p"] and a[a.index("--model") + 1] == "claude-haiku-5-5"
    assert a[a.index("--output-format") + 1] == "json"
    assert a[a.index("--system-prompt") + 1] == "SYS"
    assert a[a.index("--tools") + 1] == ""
    assert "--strict-mcp-config" in a and "--bare" not in a
    assert a[a.index("--setting-sources") + 1] == ""
    assert c["prompt"] == "Reply with PONG."
    assert c["env_anthropic"] == []                      # no ANTHROPIC_* in the child
    assert c["empty_cwd"] == [] and "cc-tmp" in c["cwd"]  # empty temp cwd
    assert not os.path.exists(c["cwd"])                   # and removed afterwards
    assert r.response_text == "PONG" and r.finish_reason == "stop"
    assert (r.prompt_tokens, r.completion_tokens) == (123, 7)
    assert (r.input_tokens, r.cache_read_tokens, r.cache_write_tokens) == (3, 100, 20)
    assert r.cost_basis == "subscription" and r.total_s == 1.5
    assert os.environ["ANTHROPIC_API_KEY"] == "sk-must-not-leak"  # parent untouched


def test_default_system_prompt(fake):
    _prov().chat("m", [{"role": "user", "content": "hi"}], max_tokens=5)
    a = fake()[0]["argv"]
    assert a[a.index("--system-prompt") + 1] == claudecode.DEFAULT_SYSTEM


def test_multi_turn_resumes_session(fake):
    p = _prov()
    m = [{"role": "user", "content": "one"}]
    r1 = p.chat("m", m, max_tokens=5)
    m += [{"role": "assistant", "content": r1.response_text}, {"role": "user", "content": "two"}]
    r2 = p.chat("m", m, max_tokens=5)
    c1, c2 = fake()
    assert "--resume" not in c1["argv"]
    assert c2["argv"][c2["argv"].index("--resume") + 1] == "sess-1"
    assert c2["prompt"] == "two"            # only the new user message
    assert r2.response_text == "turn 2"


def test_unmatched_history_renders_transcript(fake):
    _prov().chat("m", [{"role": "user", "content": "a"}, {"role": "assistant", "content": "b"},
                       {"role": "user", "content": "c"}], max_tokens=5)
    c = fake()[0]
    assert "--resume" not in c["argv"] and "[assistant]\nb" in c["prompt"] and "[user]\nc" in c["prompt"]


@pytest.mark.parametrize("mode,exc", [("limit", RequestRejected), ("rate", RequestRejected),
                                      ("crash", TransportError)])
def test_errors_are_infra(fake, monkeypatch, mode, exc):
    monkeypatch.setenv("FAKE_CLAUDE_MODE", mode)
    with pytest.raises(exc) as ei:
        _prov().chat("m", [{"role": "user", "content": "x"}], max_tokens=5)
    if exc is RequestRejected:
        from crucibleforge.preflight import billing_rejection
        from crucibleforge.runner import _rejection_kind
        assert billing_rejection(ei.value)
        assert _rejection_kind({"category": "coding"}, ei.value) == "transport"


def test_tools_refused(fake):
    with pytest.raises(RequestRejected):
        _prov().chat("m", [{"role": "user", "content": "x"}], max_tokens=5, tools=[{"x": 1}])


def test_meta_records_harness_sampling(tmp_path, monkeypatch, fake):
    from crucibleforge import runner
    monkeypatch.setattr(runner, "results_dir", lambda: tmp_path)
    runner._write_meta("m-cc", {"model_id": "claude-haiku-5-5"}, _prov(), reset=True)
    meta = json.loads((tmp_path / "meta_m-cc.json").read_text(encoding="utf-8"))
    assert meta["sampling"] == "harness-default" and meta["cost_basis"] == "subscription"
    assert "harness sampling" in meta["sampling_note"]


# ---- subscription protection: caps, window, usage-limit stop (20261111) ----
def _chat():
    return _prov().chat("m", [{"role": "user", "content": "x"}], max_tokens=5)


@pytest.fixture
def guard(fake, tmp_path, monkeypatch):
    monkeypatch.setenv("CF_CC_COUNTER", str(tmp_path / "cc_counter.json"))
    for k in ("CF_CC_MAX_CALLS", "CF_CC_MAX_TOKENS", "CF_CC_WINDOW"):
        monkeypatch.delenv(k, raising=False)
    claudecode.reset_guard()
    return tmp_path / "cc_counter.json"


def _ncalls(tmp_path):
    p = tmp_path / "calls.jsonl"
    return len(p.read_text(encoding="utf-8").splitlines()) if p.exists() else 0


def test_call_cap_refuses_before_calling(guard, tmp_path, monkeypatch, caplog):
    from crucibleforge.preflight import billing_rejection
    monkeypatch.setenv("CF_CC_MAX_CALLS", "2")
    _chat(); _chat()
    with pytest.raises(RequestRejected) as ei:
        _chat()
    assert billing_rejection(ei.value) and "cap reached (calls 2/2)" in str(ei.value)
    assert _ncalls(tmp_path) == 2
    assert json.loads(guard.read_text(encoding="utf-8"))["calls"] == 2


def test_counter_shared_across_processes_via_file(guard, tmp_path, monkeypatch):
    monkeypatch.setenv("CF_CC_MAX_CALLS", "3")
    guard.write_text(json.dumps({"calls": 3, "tokens": 0, "started": __import__("time").time()}), encoding="utf-8")
    with pytest.raises(RequestRejected):
        _chat()
    assert _ncalls(tmp_path) == 0


def test_token_cap(guard, tmp_path, monkeypatch):
    monkeypatch.setenv("CF_CC_MAX_TOKENS", "200")   # fake call reports 3+7+100+20 = 130
    _chat()
    _chat()                                          # 130 < 200: allowed, now 260
    with pytest.raises(RequestRejected) as ei:
        _chat()
    assert "tokens 260/200" in str(ei.value) and _ncalls(tmp_path) == 2


def test_stale_counter_resets(guard, tmp_path, monkeypatch):
    monkeypatch.setenv("CF_CC_MAX_CALLS", "1")
    guard.write_text(json.dumps({"calls": 9, "tokens": 9, "started": 1.0}), encoding="utf-8")
    _chat()
    assert json.loads(guard.read_text(encoding="utf-8"))["calls"] == 1


def test_window(guard, tmp_path, monkeypatch):
    import datetime as dt
    now = dt.datetime.now()
    inside = f"{(now - dt.timedelta(hours=1)):%H:%M}-{(now + dt.timedelta(hours=1)):%H:%M}"
    outside = f"{(now + dt.timedelta(hours=2)):%H:%M}-{(now + dt.timedelta(hours=3)):%H:%M}"
    monkeypatch.setenv("CF_CC_WINDOW", inside)
    _chat()
    monkeypatch.setenv("CF_CC_WINDOW", outside)
    with pytest.raises(RequestRejected) as ei:
        _chat()
    assert "outside CF_CC_WINDOW" in str(ei.value) and _ncalls(tmp_path) == 1


def test_window_wraps_midnight():
    import datetime as dt
    assert claudecode.in_window("22:00-06:00", dt.time(23, 30))
    assert claudecode.in_window("22:00-06:00", dt.time(5, 0))
    assert not claudecode.in_window("22:00-06:00", dt.time(12, 0))
    assert claudecode.in_window(None, dt.time(12, 0))


def test_two_limit_responses_set_stop_flag(guard, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "limit")
    for _ in range(2):
        with pytest.raises(RequestRejected):
            _chat()
    assert _ncalls(tmp_path) == 2
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "ok")
    with pytest.raises(RequestRejected) as ei:
        _chat()
    assert "stopped" in str(ei.value) and _ncalls(tmp_path) == 2


def test_success_resets_limit_streak(guard, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "limit")
    with pytest.raises(RequestRejected):
        _chat()
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "ok")
    _chat()
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "limit")
    with pytest.raises(RequestRejected):
        _chat()
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "ok")
    _chat()   # streak was 1, not 2: not stopped
