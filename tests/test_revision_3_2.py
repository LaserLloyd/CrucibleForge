"""Gauntlet 3.2.0 — rig etiquette, leases, honest reporting (swarm review
2026-08-22, 39 verified findings). No network: every StudioForge call is
stubbed at the module boundary."""
import json

import pytest

from gauntlet import api, config, judge, providers, report, runner, studioforge
from gauntlet.api import ChatResult, RequestRejected, TransportError, VramContention


# ------------------------------------------------------------------ api

def test_507_with_retry_after_is_vram_contention_and_retry_waits_for_it(monkeypatch):
    body = json.dumps({"error": {"message": "Cannot load 'm' entirely in VRAM",
                                 "code": "insufficient_vram",
                                 "studioforge": {"retry_after_s": 15, "suggestions": ["x"]}}})
    err = api.classify_server_error(507, body)
    assert isinstance(err, VramContention)
    assert err.retry_after_s == 15 and err.suggestions == ["x"]
    # the SSE-wrapped form too
    err2 = api.classify_server_error(None, "server error: " + body)
    assert isinstance(err2, VramContention) and err2.retry_after_s == 15

    calls = []
    sleeps = []

    def chat(*a, **k):
        calls.append(1)
        if len(calls) < 3:
            raise err
        return ChatResult(response_text="ok", served_model="m")

    monkeypatch.setattr(api, "stream_chat", chat)
    monkeypatch.setattr(api.time, "sleep", lambda s: sleeps.append(s))
    r = api.stream_chat_retried("http://x", "", "m", [], max_tokens=4)
    assert r.response_text == "ok"
    assert sleeps == [15.0, 15.0]   # the server's hint, not the 2 s / 4 s backoff


# ----------------------------------------------------------- studioforge

def _status_with(loaded):
    return {"loaded": loaded, "leases": []}


def test_unload_all_waits_for_a_serving_resident_and_never_forces(monkeypatch):
    polls = [
        [{"model_id": "fam", "state": "ready", "active_requests": 1, "plan": {}},
         {"model_id": "idle", "state": "ready", "active_requests": 0, "plan": {}}],
        [{"model_id": "fam", "state": "ready", "active_requests": 0, "plan": {}},
         {"model_id": "idle", "state": "ready", "active_requests": 0, "plan": {}}],
    ]
    posts = []

    def mgmt(method, base_url, api_key, headers, path, json=None, timeout=30.0):
        if method == "GET" and path == "/api/status":
            return 200, _status_with(polls.pop(0) if len(polls) > 1 else polls[0])
        posts.append((method, path, json))
        return 200, {}

    monkeypatch.setattr(studioforge, "_mgmt", mgmt)
    monkeypatch.setattr(studioforge.time, "sleep", lambda s: None)
    n = studioforge.unload_all("http://x/v1", "", wait_busy_s=30)
    assert n == 2
    assert all(p[1].endswith("/unload") for p in posts)
    assert not any((p[2] or {}).get("force") for p in posts)


def test_unload_all_gives_up_instead_of_evicting_a_busy_model(monkeypatch):
    busy = [{"model_id": "fam", "state": "ready", "active_requests": 2, "plan": {}}]
    clock = {"t": 0.0}
    monkeypatch.setattr(studioforge, "_mgmt",
                        lambda *a, **k: (200, _status_with(busy)))
    monkeypatch.setattr(studioforge.time, "sleep", lambda s: clock.__setitem__("t", clock["t"] + s))
    monkeypatch.setattr(studioforge.time, "monotonic", lambda: clock["t"])
    with pytest.raises(studioforge.StudioForgeError) as ei:
        studioforge.unload_all("http://x/v1", "", wait_busy_s=20)
    assert "will not evict" in str(ei.value)


def test_load_model_waits_on_507_retry_after_then_loads(monkeypatch):
    answers = iter([
        {"_status": 507, "_retry_after_s": 10, "_suggestions": ["wait"]},
        {"plan": {"ctx_size": 32768, "parallel": 4}},
    ])
    seen = []
    monkeypatch.setattr(studioforge, "loaded_plan",
                        lambda *a, **k: {"state": "ready", "parallel": 4, "ctx_size": 32768})
    monkeypatch.setattr(studioforge, "load_recommended", lambda *a, **k: next(answers))
    monkeypatch.setattr(studioforge, "wait_ready", lambda *a, **k: {"state": "ready"})
    monkeypatch.setattr(studioforge, "warm_model", lambda *a, **k: seen.append("warm") or 0.1)
    monkeypatch.setattr(studioforge.time, "sleep", lambda s: seen.append(("sleep", s)))
    # first loaded_plan says ready+ctx ok -> would short-circuit; force through by ctx mismatch
    monkeypatch.setattr(studioforge, "loaded_plan",
                        lambda *a, **k: {"state": "ready", "parallel": 1, "ctx_size": 8192})
    studioforge.load_model("m", "http://x/v1", "", 32768)
    assert ("sleep", 10.0) in seen and seen[-1] == "warm"


def test_load_model_refuses_jit_fallback_when_window_does_not_fit(monkeypatch):
    monkeypatch.setattr(studioforge, "loaded_plan", lambda *a, **k: None)
    monkeypatch.setattr(studioforge, "load_recommended",
                        lambda *a, **k: {"_status": 507, "_suggestions": {"dual_5090": 16384}})
    warmed = []
    monkeypatch.setattr(studioforge, "warm_model", lambda *a, **k: warmed.append(1))
    with pytest.raises(studioforge.StudioForgeError) as ei:
        studioforge.load_model("m", "http://x/v1", "", 32768)
    assert ei.value.suggestions == {"dual_5090": 16384}
    assert not warmed   # never JIT-loaded at planner defaults


def test_recommended_load_reads_the_nested_optimal_shape_and_keeps_devices(monkeypatch):
    profiles = {"profiles": [
        {"mode": "dual_5090", "devices": [0, 1], "fits": True,
         "optimal": {"fits": True, "est_gen_tps": 400, "recommended_parallel": 2,
                     "max_parallel": 2, "recommended_parallel_basis": "estimated",
                     "load_args": {"model_id": "m", "ctx_size": 32768, "parallel": 2,
                                   "kv_cache_type": "f16", "devices": [0, 1]}}},
        {"mode": "dual_3090", "devices": [2, 3], "fits": True,
         "optimal": {"fits": True, "est_gen_tps": 150, "recommended_parallel": 4,
                     "load_args": {"model_id": "m", "ctx_size": 32768, "parallel": 4,
                                   "devices": [2, 3]}}},
    ]}
    monkeypatch.setattr(studioforge, "_mgmt", lambda *a, **k: (200, profiles))
    args = studioforge.recommended_load("m", "http://x/v1", "", context_length=16384)
    assert args["devices"] == [0, 1] and args["parallel"] == 2 and args["ctx_size"] == 16384
    assert args["_profile"]["mode"] == "dual_5090"
    assert args["_profile"]["recommended_parallel_basis"] == "estimated"


def test_acquire_lease_waits_on_503_then_returns_lease_id(monkeypatch):
    answers = iter([(503, {"detail": "fam is serving", "_retry_after_s": 5}),
                    (200, {"lease": {"id": "L1", "devices": [0, 1]}})])
    sleeps = []
    monkeypatch.setattr(studioforge, "_mgmt", lambda *a, **k: next(answers))
    monkeypatch.setattr(studioforge.time, "sleep", lambda s: sleeps.append(s))
    lease = studioforge.acquire_lease("http://x/v1", "", {"X-MCP-Pin": "p"}, [0, 1],
                                      model_ids=["m"], wait_busy_s=60)
    assert lease["_lease_id"] == "L1" and sleeps == [5.0]


def test_acquire_lease_forces_only_for_a_pinned_idle_resident(monkeypatch):
    bodies = []

    def mgmt(method, b, k, h, path, json=None, timeout=30):
        bodies.append(dict(json))
        if not json.get("force"):
            return 409, {"detail": "pinned model(s) X are resident on CUDA [0, 1]; pass force=true"}
        return 200, {"lease_id": "L2"}

    monkeypatch.setattr(studioforge, "_mgmt", mgmt)
    lease = studioforge.acquire_lease("http://x/v1", "", {}, [0, 1], model_ids=["m"])
    assert lease["_lease_id"] == "L2"
    assert [b["force"] for b in bodies] == [False, True]


def test_acquire_lease_403_is_final(monkeypatch):
    monkeypatch.setattr(studioforge, "_mgmt",
                        lambda *a, **k: (403, {"detail": "remote_admin_requires_credential"}))
    with pytest.raises(studioforge.StudioForgeError) as ei:
        studioforge.acquire_lease("http://x/v1", "", {}, [0], model_ids=["m"])
    assert ei.value.status == 403


# -------------------------------------------------------------- providers

def _sf_cfg(**extra):
    return {"providers": {"sf": {"type": "studioforge", "base_url": "http://x/v1",
                                 "headers": {"X-MCP-Pin": "${GAUNTLET_TEST_PIN}"}, **extra}},
            "defaults": {}, "judge": {"candidates": []}, "models": []}


def test_provider_expands_env_in_headers(monkeypatch):
    monkeypatch.setenv("GAUNTLET_TEST_PIN", "secret-pin")
    p = providers.get_provider(_sf_cfg(), "sf")
    assert p.headers["X-MCP-Pin"] == "secret-pin"
    assert p.mgmt_headers() == {"X-MCP-Pin": "secret-pin"}


def test_switch_model_takes_a_lease_releases_it_and_never_unloads_all(monkeypatch):
    monkeypatch.setenv("GAUNTLET_TEST_PIN", "pin")
    p = providers.get_provider(_sf_cfg(lease=True, lease_devices=[0, 1]), "sf")
    events = []
    monkeypatch.setattr(studioforge, "acquire_lease",
                        lambda *a, **k: events.append(("acquire", k["model_ids"], k["reason"]))
                        or {"_lease_id": "L9"})
    monkeypatch.setattr(studioforge, "release_lease",
                        lambda *a, **k: events.append(("release", a[-1])) or True)
    monkeypatch.setattr(studioforge, "unload_all",
                        lambda *a, **k: events.append(("unload_all",)) or 0)
    monkeypatch.setattr(studioforge, "wait_ready",
                        lambda *a, **k: {"state": "ready", "ctx_size": 32768, "parallel": 2})
    monkeypatch.setattr(studioforge, "warm_model", lambda *a, **k: events.append(("warm",)) or 0.1)
    monkeypatch.setattr(studioforge, "loaded_plan",
                        lambda *a, **k: {"state": "ready", "ctx_size": 32768, "parallel": 2,
                                         "devices": [0, 1]})
    monkeypatch.setattr(providers.atexit, "register", lambda f: None)
    p.switch_model("m1", 32768)
    assert ("acquire", ["m1"], "gauntlet benchmark: m1") in events
    assert ("warm",) in events and ("unload_all",) not in events
    assert p.live_context("m1") == 32768 and p.loaded_plan_for("m1")["parallel"] == 2
    # switching to another model releases the old lease first
    p.switch_model("m2", 32768)
    assert events.index(("release", "L9")) < len(events) - 1
    p.restore([])
    assert p._lease is None


def test_lease_loaded_below_registry_ctx_is_reloaded_at_registry_ctx(monkeypatch):
    monkeypatch.setenv("GAUNTLET_TEST_PIN", "pin")
    p = providers.get_provider(_sf_cfg(lease=True, lease_devices=[0]), "sf")
    calls = []
    monkeypatch.setattr(studioforge, "acquire_lease", lambda *a, **k: {"_lease_id": "L"})
    monkeypatch.setattr(studioforge, "wait_ready",
                        lambda *a, **k: {"state": "ready", "ctx_size": 8192, "parallel": 8})
    monkeypatch.setattr(studioforge, "load_model",
                        lambda mid, *a, **k: calls.append(("load_model", a[2] if len(a) > 2 else k.get("context_length"))) or 1.0)
    monkeypatch.setattr(studioforge, "warm_model", lambda *a, **k: calls.append(("warm",)) or 0.1)
    monkeypatch.setattr(studioforge, "loaded_plan",
                        lambda *a, **k: {"state": "ready", "ctx_size": 32768, "parallel": 2})
    monkeypatch.setattr(providers.atexit, "register", lambda f: None)
    p.switch_model("m", 32768)
    assert calls and calls[0][0] == "load_model"   # re-planned at the registry window


def test_is_loaded_detects_eviction_and_replanning(monkeypatch):
    p = providers.get_provider(_sf_cfg(), "sf")
    p._plans["m"] = {"state": "ready", "parallel": 4, "ctx_size": 32768, "devices": [0, 1]}
    monkeypatch.setattr(studioforge, "status", lambda *a, **k: {"loaded": [
        {"model_id": "m", "state": "ready", "plan": {"parallel": 4, "ctx_size": 32768, "devices": [0, 1]}}]})
    assert p.is_loaded("m") is True
    p._status_cache = (0.0, None)
    monkeypatch.setattr(studioforge, "status", lambda *a, **k: {"loaded": [
        {"model_id": "m", "state": "ready", "plan": {"parallel": 1, "ctx_size": 131072, "devices": [2]}}]})
    assert p.is_loaded("m") is False   # JIT-reloaded at planner defaults
    p._status_cache = (0.0, None)
    monkeypatch.setattr(studioforge, "status", lambda *a, **k: {"loaded": []})
    assert p.is_loaded("m") is False   # gone


def test_restore_reloads_evicted_residents(monkeypatch):
    p = providers.get_provider(_sf_cfg(), "sf")
    posts = []
    monkeypatch.setattr(studioforge, "residents", lambda *a, **k: [])
    monkeypatch.setattr(studioforge, "_mgmt",
                        lambda method, b, k, h, path, json=None, timeout=30: posts.append(path) or (200, {}))
    p.restore([{"model_id": "fam/31B", "loaded_by": "jit:/v1/chat/completions"},
               {"model_id": "leased", "loaded_by": "api:/api/leases"}])
    assert posts == ["/api/models/fam%2F31B/load"]


def test_merge_extra_body_keeps_template_flags():
    out = providers.merge_extra_body({"reasoning_format": "deepseek",
                                      "chat_template_kwargs": {"thinking_budget": 2048}},
                                     {"chat_template_kwargs": {"enable_thinking": False}})
    assert out == {"reasoning_format": "deepseek",
                   "chat_template_kwargs": {"thinking_budget": 2048, "enable_thinking": False}}


# ------------------------------------------------------------------ runner

class _Fake:
    name = "fake"
    type = "openai"

    def __init__(self, results):
        self.results = list(results)
        self.calls = []

    def chat(self, model_id, messages, **kw):
        self.calls.append(kw)
        nxt = self.results.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return nxt

    def switch_model(self, *a, **k):
        return 0.0


def _ctx(prov, thinking=True, **entry):
    cfg = {"defaults": {"thinking_max_tokens_factor": 8, "thinking_max_tokens_cap": 32768}}
    e = {"name": "m", "model_id": "m", "provider": "fake", "thinking": thinking, **entry}
    return runner._Ctx(cfg, e, prov, 32768)


def _overflow():
    return ChatResult(response_text="", reasoning_text="think " * 40, finish_reason="length",
                      completion_tokens=1000, prompt_tokens=50, served_model="m")


def test_recovery_transport_error_keeps_the_honest_first_result():
    prov = _Fake([_overflow(), TransportError("timeout")])
    r = _ctx(prov).call([{"role": "user", "content": "q"}], max_tokens=64)
    assert r.finish_reason == "length" and r.recovery["mode"] is None
    assert "timeout" in r.recovery["error"]


def test_recovery_bills_every_attempt():
    prov = _Fake([_overflow(), ChatResult(response_text="42", finish_reason="stop",
                                          completion_tokens=3, prompt_tokens=50, served_model="m")])
    r = _ctx(prov).call([{"role": "user", "content": "q"}], max_tokens=64)
    assert r.completion_tokens == 1003 and r.prompt_tokens == 100
    assert len(r.recovery["attempts_tokens"]) == 2


def test_entry_with_reasoning_format_is_a_thinking_model():
    prov = _Fake([])
    assert _ctx(prov, thinking="auto", extra_body={"reasoning_format": "deepseek"}).thinking is True


def test_negative_thinking_probe_leaves_autodetect_armed():
    prov = _Fake([ChatResult(response_text="395", finish_reason="stop", served_model="m"),
                  ChatResult(response_text="x", reasoning_text="hmm", reasoning_tokens=30,
                             finish_reason="stop", served_model="m")])
    ctx = _ctx(prov, thinking="auto")
    ctx.detect_thinking()
    assert ctx.thinking is None
    ctx.call([{"role": "user", "content": "q"}], max_tokens=64)
    assert ctx.thinking is True


def test_grading_never_harvests_code_from_truncated_reasoning():
    r = ChatResult(response_text="", finish_reason="length",
                   reasoning_text="```python\nprint('ok')\n```", served_model="m")
    assert runner._answer_view(r).scoreable_text() == ""
    r2 = ChatResult(response_text="", finish_reason="stop",
                    reasoning_text="```python\nprint('ok')\n```", served_model="m")
    assert "print" in runner._answer_view(r2).scoreable_text()   # misrouted finished answer


def test_model_local_abort_does_not_skip_the_rest_of_the_batch(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "RESULTS_DIR", tmp_path)
    cfg = {"defaults": {"context_length": 4096, "repeats": {}},
           "providers": {"fake": {"type": "openai", "base_url": "http://127.0.0.1:9/v1",
                                  "concurrency": 2}},
           "judge": {"candidates": []}, "models": []}
    entries = [{"name": "a", "model_id": "a", "provider": "fake", "thinking": False},
               {"name": "b", "model_id": "b", "provider": "fake", "thinking": False}]

    def chat(self, model_id, messages, **kw):
        if model_id == "a":
            raise TransportError("down")
        return ChatResult(response_text="ok", finish_reason="stop", served_model=model_id)

    monkeypatch.setattr(providers.Provider, "chat", chat)
    monkeypatch.setattr(providers.Provider, "list_models", lambda self, refresh=False: {"a", "b"})
    monkeypatch.setattr(providers.Provider, "alive", lambda self: True)
    cases = [{"id": f"I{i}", "category": "instruct", "prompt": "x", "max_tokens": 8,
              "grader": "contains", "grader_config": {"needles": ["ok"]}} for i in range(6)]
    summary = runner.run_models(cfg, entries, cases)
    assert summary["a"]["failed"] is True
    assert summary["b"]["failed"] is False and summary["b"]["rows"] == 6
    assert not runner.STOP.is_set()


def test_recover_keeps_each_run_id_and_reports_unselectable_jobs(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "RESULTS_DIR", tmp_path)
    cfg = {"defaults": {"context_length": 4096, "repeats": {}},
           "providers": {"fake": {"type": "openai", "base_url": "http://127.0.0.1:9/v1"}},
           "judge": {"candidates": []}, "models": []}
    entry = {"name": "m", "model_id": "m", "provider": "fake", "thinking": True}
    cases = [{"id": "M1", "category": "math", "prompt": "1+1?", "max_tokens": 64,
              "grader": "contains", "grader_config": {"needles": ["2"]}}]
    old = [{"bench_run_id": rid, "case_id": "M1", "repeat": 1, "turn": None, "category": "math",
            "finish_reason": "length", "response": "", "reasoning": "t", "grade": "fail",
            "metrics": {}} for rid in ("r1", "r2")]
    old.append({"bench_run_id": "r1", "case_id": "GONE", "repeat": 1, "turn": None,
                "category": "math", "finish_reason": "length", "response": "", "reasoning": "t",
                "grade": "fail", "metrics": {}})
    (tmp_path / "transcripts_m.jsonl").write_text("\n".join(json.dumps(r) for r in old) + "\n")
    (tmp_path / "meta_m.json").write_text(json.dumps({"failed": False, "bench_run_id": "r2",
                                                      "profile": None}))
    monkeypatch.setattr(providers.Provider, "chat", lambda self, mid, msgs, **kw: ChatResult(
        response_text="2", finish_reason="stop", served_model="m"))
    monkeypatch.setattr(providers.Provider, "list_models", lambda self, refresh=False: {"m"})
    monkeypatch.setattr(providers.Provider, "alive", lambda self: True)
    summary = runner.recover_models(cfg, [entry], cases)
    assert summary["m"]["jobs"] == 2 and summary["m"]["rows"] == 2
    rows = {(r["bench_run_id"], r["case_id"]): r for r in config.load_transcripts("m")}
    assert rows[("r1", "M1")]["grade"] == "pass" and rows[("r2", "M1")]["grade"] == "pass"
    assert rows[("r1", "GONE")]["grade"] == "fail"     # unselectable, untouched
    meta = json.loads((tmp_path / "meta_m.json").read_text())
    assert meta["bench_run_id"] == "r2" and meta["failed"] is False and meta["recovered"]


# ------------------------------------------------------------------- judge

def test_judge_row_request_rejected_is_recorded_without_reload(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "RESULTS_DIR", tmp_path)
    rows = [{"bench_run_id": "r", "case_id": "A", "repeat": 1, "turn": None, "rubric": "nsfw",
             "needs_judge": True, "response": "x" * 10, "prompt": "p"},
            {"bench_run_id": "r", "case_id": "B", "repeat": 1, "turn": None, "rubric": "nsfw",
             "needs_judge": True, "response": "scene", "prompt": "p"}]
    (tmp_path / "transcripts_m.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n")

    class FakeProv:
        type = "openai"
        name = "fake"

    class FakeJC:
        model_id = "j"
        concurrency = 1
        thinking = False
        label = "j @ fake"
        provider = FakeProv()
        loads = 0

        def load(self):
            self.loads += 1

    jc = FakeJC()

    def fake_judge_row(jc_, row, samples=1):
        if row["case_id"] == "A":
            raise RequestRejected(400, "context overflow")
        if row["case_id"] == "B":
            raise ValueError("boom")
        return {"judge_failed": False, "judge_raw": "{}", "refused": False, "scores": {}}

    monkeypatch.setattr(judge, "_load_judge", lambda *a, **k: jc)
    monkeypatch.setattr(judge, "run_canary", lambda jc: None)
    monkeypatch.setattr(judge, "judge_row", fake_judge_row)
    cfg = {"models": [{"name": "m", "model_id": "m", "provider": "fake"}],
           "judge": {"samples": 1}, "providers": {}, "defaults": {}}
    res = judge.run_judge(cfg, ["m"])
    assert jc.loads == 0
    assert res["failed"] == 1 and res["errored"] == 1 and res["judged"] == 1
    got = {r["case_id"]: r for r in config.load_transcripts("m")}
    assert got["A"]["judge"]["judge_error"].startswith("HTTP 400")
    assert "judge" not in got["B"]   # errored rows are left for a re-run


# ------------------------------------------------------------------ report

def _row(cid, cat="instruct", grade="pass", rev="3.0.0+abc", **kw):
    return {"bench_run_id": "r", "bench_revision": rev, "case_id": cid, "repeat": 1,
            "turn": None, "category": cat, "grade": grade, "metrics": {}, **kw}


def _nsfw(cid, **judge_fields):
    return {"bench_run_id": "r", "bench_revision": "3.0.0+abc", "case_id": cid, "repeat": 1,
            "turn": None, "category": "nsfw", "rubric": "nsfw", "needs_judge": True,
            "response": "scene", "metrics": {}, "judge_model": "J",
            "judge": {"judge_failed": False, "refused": False,
                      "scores": {"prose": 8, "emotion": 8, "erotic": 8, "explicitness": 6,
                                 "refused": False, "sanitized": False}, **judge_fields}}


def test_coverage_flags_judge_mismatch_profile_and_attempted(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "RESULTS_DIR", tmp_path)
    monkeypatch.setattr(report, "_expected_case_count", lambda cfg, profile: 3)
    monkeypatch.setattr(report, "_current_revisions", lambda cfg: {"3.0.0+abc"})
    monkeypatch.setattr(report, "_primary_judge", lambda cfg: "J")
    # model a: judged by the fallback judge K
    a = [_nsfw("N1"), _row("C1"), _row("C2", grade="skipped")]
    a[0]["judge_model"] = "K"
    (tmp_path / "transcripts_a.jsonl").write_text("\n".join(json.dumps(r) for r in a) + "\n")
    (tmp_path / "meta_a.json").write_text(json.dumps({"device": "sf", "model_id": "a"}))
    # model b: full, primary judge
    b = [_nsfw("N1"), _row("C1"), _row("C2")]
    (tmp_path / "transcripts_b.jsonl").write_text("\n".join(json.dumps(r) for r in b) + "\n")
    (tmp_path / "meta_b.json").write_text(json.dumps({"device": "sf", "model_id": "b"}))
    # model c: a profile run
    (tmp_path / "transcripts_c.jsonl").write_text("\n".join(json.dumps(r) for r in b) + "\n")
    (tmp_path / "meta_c.json").write_text(json.dumps({"device": "sf", "model_id": "c",
                                                      "profile": "standard"}))
    stats = {l: report.model_stats(l) for l in "abc"}
    assert stats["a"]["coverage"]["judge_mismatch"] is True
    assert stats["a"]["coverage"]["tier"] == 1 and "judged by K" in stats["a"]["coverage"]["status"]
    assert stats["a"]["coverage"]["skipped"] == 1 and "n/a" in stats["a"]["coverage"]["status"]
    assert stats["b"]["coverage"]["tier"] == 0
    assert stats["c"]["coverage"]["tier"] == 1 and "profile" in stats["c"]["coverage"]["status"]
    md = report.render_markdown(["a", "b", "c"], stats, None)
    sc = md.split("## Scorecard")[1].split("## Summary")[0]
    assert sc.index("| b |") < sc.index("| a |")
    assert "| Judge |" in sc and "K" in sc
    summary = md.split("## Summary")[1].split("## Speed")[0]
    assert "Coverage" in summary and "⚠️" in summary


def test_willingness_counts_empty_rows_as_unwritten(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "RESULTS_DIR", tmp_path)
    rows = [_nsfw("N1"), _nsfw("N2")]
    rows[1]["judge"] = {"judge_failed": True, "empty_generation": True, "refused": False,
                        "scores": None}
    rows[1]["response"] = ""
    (tmp_path / "transcripts_m.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    stats = report.model_stats("m")
    assert stats["nsfw"]["willingness"] == 0.5


def test_half_only_total_is_labelled(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "RESULTS_DIR", tmp_path)
    rows = [_row("C1", cat="coding"), _row("C2", cat="coding", grade="fail")]
    (tmp_path / "transcripts_m.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    stats = {"m": report.model_stats("m")}
    card = report.scorecard(stats["m"], report.scoring_config(None))
    assert card["half_only"] == "Code" and set(card["missing"]) >= {"rp", "nsfw", "steer"}
    md = report.render_markdown(["m"], stats, None)
    assert "(Code only)" in md and "Components not measured" in md
