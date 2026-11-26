"""S5 merge: cache45 (spend controls, cache split) and cc-provider (claude_code) together."""
from __future__ import annotations

import pytest

from crucibleforge import api, claudecode, providers, runner, spend
from crucibleforge.api import ChatResult
from crucibleforge.config import PROVIDER_TYPES, validate_config
from crucibleforge.providers import clear_cache, get_provider


def test_both_provider_types_exist_and_claude_code_stays_single_threaded():
    assert {"anthropic", "claude_code", "openai"} <= set(PROVIDER_TYPES)
    assert "claude_code" not in providers.HOSTED_TYPES and "anthropic" in providers.HOSTED_TYPES
    cfg = {"providers": {"cc": {"type": "claude_code"},
                         "an": {"type": "anthropic", "base_url": "https://api.anthropic.com/v1"},
                         # 4.5.1: the same API shape on the user's own machine gets one request at a time
                         "loc": {"type": "anthropic", "base_url": "http://127.0.0.1:1"}},
           "models": [{"name": "a", "provider": "cc", "model_id": "x"}],
           "judge": {"candidates": [{"provider": "cc", "model_id": "x"}]}, "defaults": {}}
    validate_config(cfg)
    clear_cache()
    assert get_provider(cfg, "cc").concurrency == 1
    assert get_provider(cfg, "an").concurrency == providers.API_DEFAULT_CONCURRENCY
    assert get_provider(cfg, "loc").concurrency == 1


def test_claude_code_never_reaches_the_api_or_the_spend_path(monkeypatch):
    """Even with a price declared on its model, a claude_code chat goes to the CLI only."""
    seen = []

    def boom(*a, **k):
        raise AssertionError("claude_code reached the metered path")
    monkeypatch.setattr(api, "stream_chat_retried", boom)
    monkeypatch.setattr(spend, "check", boom)
    monkeypatch.setattr(spend, "record", boom)
    monkeypatch.setattr(claudecode, "run_chat",
                        lambda *a, **k: seen.append(1) or ChatResult(response_text="ok", finish_reason="stop"))
    cfg = {"providers": {"cc": {"type": "claude_code"}},
           "models": [{"name": "m", "provider": "cc", "model_id": "claude-x", "price": {"input": 1.0, "output": 5.0}}],
           "judge": {"candidates": [{"provider": "cc", "model_id": "x"}]}, "defaults": {}}
    validate_config(cfg)
    clear_cache()
    r = get_provider(cfg, "cc").chat("claude-x", [{"role": "user", "content": "hi"}], max_tokens=5)
    assert r.response_text == "ok" and seen == [1]


def test_a_metered_provider_still_goes_through_the_spend_cap(monkeypatch):
    calls = []
    monkeypatch.setattr(spend, "check", lambda *a, **k: calls.append("check"))
    monkeypatch.setattr(spend, "record", lambda *a, **k: calls.append("record"))
    monkeypatch.setattr(api, "stream_chat_retried",
                        lambda *a, **k: ChatResult(response_text="ok", prompt_tokens=10, completion_tokens=5))
    cfg = {"providers": {"p": {"type": "openai", "base_url": "http://127.0.0.1:1", "api_key_env": "K"}},
           "models": [{"name": "m", "provider": "p", "model_id": "mm", "price": {"input": 1.0, "output": 4.0}}]}
    clear_cache()
    get_provider(cfg, "p").chat("mm", [{"role": "user", "content": "hi"}], max_tokens=5)
    assert calls == ["check", "record"]


def test_row_metrics_carry_cache45_split_and_the_claude_code_input_split_without_empty_keys():
    plain = runner._metrics(ChatResult(response_text="x", prompt_tokens=10, completion_tokens=2))
    assert not {"input_tokens", "cache_read_tokens", "cache_write_tokens"} & set(plain)
    native = runner._metrics(ChatResult(prompt_tokens=100, completion_tokens=2, cache_read_tokens=80,
                                        cache_write_tokens=5))
    assert (native["cache_read_tokens"], native["cache_write_tokens"]) == (80, 5)
    cc = ChatResult(prompt_tokens=123, completion_tokens=7, cache_read_tokens=100, cache_write_tokens=20)
    cc.input_tokens = 3
    m = runner._metrics(cc)
    assert (m["input_tokens"], m["cache_read_tokens"], m["cache_write_tokens"]) == (3, 100, 20)


def test_notes_are_on_under_an_active_bench45_and_off_under_bench(monkeypatch):
    """notes45 reads the ACTIVE profile (fix-d active_name), not the default one."""
    from crucibleforge import config, notes45, profiles
    cfg = config.load_config(config.EXAMPLE_CONFIG_PATH)
    monkeypatch.setattr(profiles, "_ACTIVE", [None])
    monkeypatch.setattr(profiles, "DEFAULT_PROFILE", "bench")
    assert not notes45.enabled(notes45._active_profile(cfg))
    monkeypatch.setattr(profiles, "_ACTIVE", ["bench45"])
    prof = notes45._active_profile(cfg)
    assert prof.get("name") == "bench45" and notes45.enabled(prof)
    assert notes45.enabled(notes45._active_profile({**cfg, "_profile": "bench45"}))
