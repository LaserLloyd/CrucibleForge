"""Harness lane 2026-10-31: stop-path unload, empty session turns, per-case
budget on the NSFW writing rows."""
from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path

import pytest

from crucibleforge import estimate, runner, stopflag
from crucibleforge.api import ChatResult

ROOT = Path(__file__).resolve().parent.parent
NSFW_LONG = ["WL2-lighthouse", "NX2-juniper", "XP3-named-peak", "NW1-salt-and-cedar"]


@pytest.fixture(autouse=True)
def _clear_stop_flags():
    stopflag.ABORT.clear()
    runner.STOP.clear()
    yield
    stopflag.ABORT.clear()
    runner.STOP.clear()


# ------------------------------------------------ 1. SIGTERM in lease-retry

def _provider(monkeypatch):
    from crucibleforge import providers, studioforge
    calls = []

    def mgmt(method, base, key, hdrs, path, json=None, timeout=30.0):
        calls.append((method, path))
        if path == "/api/leases":
            return 503, {"error": {"code": "model_busy"}, "_retry_after_s": 30.0}
        return 200, {}

    monkeypatch.setattr(studioforge, "_mgmt", mgmt)
    monkeypatch.setattr(studioforge, "release_stale_leases", lambda *a, **k: 0)
    monkeypatch.setattr(studioforge, "release_lease", lambda *a, **k: True)
    monkeypatch.setattr(studioforge, "unload_all",
                        lambda *a, **k: calls.append(("UNLOAD_ALL", "")))
    p = providers.Provider(name="sf", type="studioforge", base_url="http://x/v1")
    p.claim_devices = lambda model_id, exclude=(): [0]
    return p, calls


def test_sigterm_in_lease_retry_unloads_what_this_run_loaded(monkeypatch):
    """bench-all had model A loaded, then waits (503 retry) for B's lease and
    gets SIGTERM: the wait ends at once AND A is unloaded on the way out (it
    used to stay resident and the judge's lease was refused 409)."""
    p, calls = _provider(monkeypatch)
    p._plans = {"A": {"ctx_size": 4096}}
    p._touched = True
    threading.Timer(0.3, stopflag.ABORT.set).start()
    from crucibleforge import studioforge
    t0 = time.time()
    with pytest.raises(studioforge.StudioForgeError, match="stopping"):
        p._lease_load("B", 4096)
    assert time.time() - t0 < 5
    p.restore([{"model_id": "chat", "loaded_by": "pin"}])
    assert ("POST", "/api/models/A/unload") in calls
    # residents are NOT reloaded on a stop, and a model that was resident
    # before the run is never unloaded
    assert not [c for c in calls if c[1].endswith("/load")]
    assert ("POST", "/api/models/chat/unload") not in calls


def test_stop_unload_skips_models_that_were_resident_before(monkeypatch):
    p, calls = _provider(monkeypatch)
    p._plans = {"chat": {}}
    p._touched = True
    stopflag.ABORT.set()
    p.restore([{"model_id": "chat", "loaded_by": "pin"}])
    assert not [c for c in calls if c[1].endswith("/unload")]


def test_unload_models_never_raises_or_waits(monkeypatch):
    from crucibleforge import studioforge

    def boom(*a, **k):
        raise studioforge.StatusUnavailable("rig down")
    monkeypatch.setattr(studioforge, "_mgmt", boom)
    assert studioforge.unload_models("http://x/v1", "", None, ["A", "B"]) == 0


# ------------------------------------------------ 2. empty session turns

class _P:
    name, type = "fake", "openai"

    def __init__(self, replies):
        self.replies = list(replies)
        self.sent = []

    def chat(self, model_id, messages, **kw):
        self.sent.append([dict(m) for m in messages])
        text = self.replies.pop(0)
        return ChatResult(response_text=text, finish_reason="stop", served_model="m")

    def switch_model(self, *a, **k):
        return 0.0


def _ctx(p):
    return runner._Ctx({"defaults": {}}, {"name": "m", "model_id": "m", "thinking": True}, p, 8192)


def _case(cat="rpdeep"):
    return {"id": "S", "category": cat, "turns": ["one", "two", "three"], "max_tokens": 64}


@pytest.mark.parametrize("cat", ["rpdeep", "rp", "nsfw"])
def test_empty_session_turn_is_retried_once_on_every_session_category(cat):
    p = _P(["", "first reply", "second", "third"])
    rows = runner._run_multiturn(_case(cat), {}, _ctx(p), 64, 0.0, 1.0, 1)
    assert len(p.sent) == 4                       # turn 1 was asked twice
    assert p.sent[0] == p.sent[1]                 # the same request
    assert rows[0]["response"] == "first reply"
    # the retry's reply is what the next turn is sent
    assert p.sent[2][1] == {"role": "assistant", "content": "first reply"}


def test_empty_turn_that_still_stands_is_not_sent_as_an_empty_assistant_message():
    p = _P(["", "", "second", "third"])          # turn 1: empty twice -> stands
    rows = runner._run_multiturn(_case(), {}, _ctx(p), 64, 0.0, 1.0, 1)
    turn2 = p.sent[2]
    assert all(m["content"].strip() for m in turn2 if m["role"] == "assistant")
    assert [m["role"] for m in turn2] == ["user"]          # roles stay alternating
    assert turn2[0]["content"] == "one\n\ntwo"
    # the record (and the judged conversation) keeps the empty turn
    conv = rows[-1]["conversation"]
    assert {"role": "assistant", "content": ""} in conv
    assert rows[0]["response"] == ""


def test_sendable_history_leaves_a_normal_history_alone():
    h = [{"role": "user", "content": "a"}, {"role": "assistant", "content": "b"},
         {"role": "user", "content": "c"}]
    assert runner._sendable_history(h) == h


def test_every_ctx_call_site_passes_the_recovery_switch():
    src = (ROOT / "crucibleforge" / "runner.py").read_text(encoding="utf-8")
    calls = [m.start() for m in re.finditer(r"ctx\.call\(", src)]
    assert len(calls) >= 4
    for at in calls:
        stmt = src[at:at + 400]
        assert "recover=" in stmt or "Hi" in stmt, stmt[:120]
    # and the single-turn path really retries
    p = _P(["", "ok"])
    row = runner._run_single({"id": "X", "category": "nsfw", "prompt": "hi", "max_tokens": 64},
                             {}, _ctx(p), 64, 0.0, 1.0, 1)
    assert len(p.sent) == 2 and row["response"] == "ok"


# ------------------------------------------------ 3. nsfw per-case budget

def _nsfw_cases():
    cases = json.loads((ROOT / "cases" / "nsfw.json").read_text(encoding="utf-8"))
    by = {c["id"]: dict(c, category="nsfw") for c in cases}
    return [by[i] for i in NSFW_LONG], by


def test_nsfw_writing_rows_carry_65536_and_the_rest_do_not():
    long, by = _nsfw_cases()
    assert all(c["budget"] == 65536 for c in long)
    assert not [i for i, c in by.items() if i not in NSFW_LONG and c.get("budget")]


def test_nsfw_long_rows_get_the_full_budget_and_a_wide_context():
    long, _ = _nsfw_cases()
    cfg = {"defaults": {"bank_budget": 65536}}

    class Ctx:
        ctx_len = 73728
        rung = None
    for c in long:
        assert runner.fixed_budget(cfg, c) == 65536
        assert runner.wide_context(cfg, [c]) == 65536 + runner.ESCALATION_CTX_PAD
        assert runner.row_budget(Ctx(), c, 800) == min(65536, 73728 - 800 - runner.BANK_WINDOW_SLACK)
    # a small window clamps instead of overflowing
    Ctx.ctx_len = 40960
    assert runner.row_budget(Ctx(), long[0], 800) < 40960
    # a rung round decides its own budget
    Ctx.rung = 65536
    assert runner.row_budget(Ctx(), long[0], 800) is None


def test_nsfw_long_rows_are_sent_65536_through_the_single_turn_path():
    long, _ = _nsfw_cases()
    seen = {}

    class P(_P):
        def chat(self, model_id, messages, **kw):
            seen["max_tokens"] = kw.get("max_tokens")
            return super().chat(model_id, messages, **kw)
    ctx = _ctx(P(["text"]))
    ctx.ctx_len = 98304
    c = dict(long[0], prompt=long[0].get("prompt") or "write")
    if c.get("turns"):
        runner._run_multiturn(c, {}, ctx, 2400, 0.0, 1.0, 1)
    else:
        runner._run_single(c, {}, ctx, 2400, 0.0, 1.0, 1)
    assert seen["max_tokens"] == 65536


def test_plan_rounds_puts_the_long_nsfw_rows_in_their_own_wide_round(monkeypatch):
    long, by = _nsfw_cases()
    cfg = {"defaults": {"context_length": 32768, "context_floor": 40960,
                        "bank_budget": 65536, "run_plan": {}}}
    entry = {"name": "m", "model_id": "m", "provider": "sf", "context_length": 32768}
    monkeypatch.setattr(runner, "provider_for", lambda c, e: type("T", (), {"type": "studioforge"})())
    cases = [c for c in by.values()]
    rounds = runner.plan_rounds(cfg, entry, cases)
    wide = [r for r in rounds if r["name"] == "B"]
    assert len(wide) == 1 and {c["id"] for c in wide[0]["cases"]} == set(NSFW_LONG)
    assert wide[0]["ctx"] >= 65536 + runner.ESCALATION_CTX_PAD
    narrow = [r for r in rounds if r["name"] == "A"][0]
    assert not set(NSFW_LONG) & {c["id"] for c in narrow["cases"]}


def test_estimator_and_scheduler_treat_long_nsfw_rows_as_long_jobs():
    long, by = _nsfw_cases()
    plain = dict(long[0])
    plain.pop("budget")
    assert estimate.job_seconds(long[0], True) == pytest.approx(
        estimate.job_seconds(plain, True) * 65536 / estimate.BOARD_BUDGET_REF)
    assert estimate.job_seconds(long[0], True) > estimate.job_seconds(plain, True) * 2
    # LPT: the long rows start before every ordinary nsfw job
    jobs = [(c, 1) for c in by.values()]
    order = runner.schedule_jobs(jobs, seconds=lambda c: estimate.job_seconds(c, True))
    first = [j[0]["id"] for j in order[:len(NSFW_LONG)]]
    assert set(first) == set(NSFW_LONG)


def test_a_full_budget_nsfw_row_is_never_escalated():
    row = {"case_id": "WL2-lighthouse", "repeat": 1, "category": "nsfw",
           "finish_reason": "length", "thinking": True, "max_tokens_sent": 65536}
    assert runner.escalation_jobs([row], 65536) == set()
