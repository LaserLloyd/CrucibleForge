"""PLAN-451-LIMITS step 3 (2026-11-25): an errored row stamps the budget that was sent, the keep-alive ceiling is
capped at 2,700 s (and covers a 65,536-token request), and the empty-start sanity abort neither fires in a
recover/targeted/rung round nor reads a reply with reported reasoning tokens as empty."""
from __future__ import annotations

import pytest

from crucibleforge import api, config, providers, runner
from crucibleforge.api import TransportError


def test_the_keepalive_ceiling_covers_a_65536_request_and_is_capped():
    assert api.keepalive_ceiling(300, 65536) == api.KEEPALIVE_MAX_S == 2700.0
    assert api.keepalive_ceiling(300, 65536) >= 1800            # PATCHFIX-core-4-1 sat silent past 600 s
    assert api.keepalive_ceiling(300, 6144) == 600.0            # small requests keep the 2 x stall floor
    assert api.keepalive_ceiling(300, 10 ** 6) == 2700.0
    assert api.keepalive_ceiling(300, 65536, max_wall_s=1200) == 1200
    assert api.keepalive_ceiling(None, 65536) is None


def _run(tmp_path, monkeypatch, chat, cases):
    monkeypatch.setattr(config, "RESULTS_DIR", tmp_path)
    cfg = {"defaults": {"context_length": 100000, "repeats": {}},
           "providers": {"fake": {"type": "openai", "base_url": "http://127.0.0.1:9/v1", "concurrency": 1}},
           "judge": {"candidates": []}, "models": []}
    entry = {"name": "m", "model_id": "m", "provider": "fake", "thinking": False}
    monkeypatch.setattr(providers.Provider, "chat", chat)
    monkeypatch.setattr(providers.Provider, "list_models", lambda self, refresh=False: {"m"})
    monkeypatch.setattr(providers.Provider, "alive", lambda self: True)
    summary = runner.run_models(cfg, [entry], cases)
    return summary, {r["case_id"]: r for r in config.load_transcripts("m")}


def test_an_errored_row_stamps_the_budget_that_was_sent(tmp_path, monkeypatch):
    sent = []

    def chat(self, model_id, messages, **kw):
        sent.append(kw.get("max_tokens"))
        raise TransportError("connection refused")

    cases = [{"id": f"F{i}", "category": "coding", "prompt": "x", "max_tokens": 64, "budget": 20000,
              "grader": "contains", "grader_config": {"needles": ["ok"]}} for i in range(3)]
    _summary, rows = _run(tmp_path, monkeypatch, chat, cases)
    assert rows and all(r["error_kind"] == "transport" for r in rows.values())
    assert {r["max_tokens_sent"] for r in rows.values()} == {20000} == set(sent)   # not the raw 64


def test_planned_budget_is_the_fixed_budget_or_the_formula():
    class C:
        ctx_len = None
        rung = None

        def budget(self, mt):
            return mt * 4
    assert runner.planned_budget(C(), {"id": "a", "category": "coding", "prompt": "x", "budget": 65536}, 6144) == 65536
    assert runner.planned_budget(C(), {"id": "b", "category": "instruct", "prompt": "x"}, 768) == 3072


def test_the_sanity_abort_is_off_in_a_recover_round_and_reads_reasoning_tokens():
    empty = {"turn": 1, "response": "", "finish_reason": "stop"}
    st = {"sanity_total": 0, "sanity_empty": 0, "sanity_off": True}
    runner._sanity_check(st, [empty] * 5, "m")                       # no abort, nothing counted
    assert st["sanity_total"] == 0
    st = {"sanity_total": 0, "sanity_empty": 0}
    hidden = {**empty, "reasoning_tokens": 4000}                       # xAI: reasoning hidden, but it thought
    runner._sanity_check(st, [hidden] * 5, "m")
    assert st["sanity_total"] == 5 and st["sanity_empty"] == 0
    with pytest.raises(runner.ModelRunError):
        runner._sanity_check({"sanity_total": 0, "sanity_empty": 0}, [empty] * 5, "m")
