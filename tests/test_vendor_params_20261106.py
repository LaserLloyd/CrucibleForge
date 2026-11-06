"""Provider rename_params / drop_params (2026-11-06): OpenAI's reasoning models refuse max_tokens and
non-default sampling, so the openai provider renames/drops those body keys."""
from crucibleforge import api, providers
from tests.test_api import _patch_capture, _sse

LINES = _sse({"model": "m", "choices": [{"delta": {"content": "ok"}, "finish_reason": "stop"}]},
             {"model": "m", "choices": [], "usage": {"prompt_tokens": 1, "completion_tokens": 1}})


def test_rename_and_drop_applied(monkeypatch):
    cap = {}
    _patch_capture(monkeypatch, cap, LINES)
    api.stream_chat("http://x/v1", "k", "m", [{"role": "user", "content": "q"}], max_tokens=8,
                    temperature=0.0, top_p=1.0,
                    rename_params={"max_tokens": "max_completion_tokens"}, drop_params=["top_p", "temperature"])
    body = cap["json"]
    assert body["max_completion_tokens"] == 8 and "max_tokens" not in body
    assert "top_p" not in body and "temperature" not in body


def test_defaults_unchanged(monkeypatch):
    cap = {}
    _patch_capture(monkeypatch, cap, LINES)
    api.stream_chat("http://x/v1", "k", "m", [{"role": "user", "content": "q"}], max_tokens=8)
    assert cap["json"]["max_tokens"] == 8 and "max_completion_tokens" not in cap["json"]


def test_provider_reads_config(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "x")
    cfg = {"providers": {"openai": {"type": "openai", "base_url": "https://api.openai.com/v1",
                                    "api_key_env": "OPENAI_API_KEY",
                                    "rename_params": {"max_tokens": "max_completion_tokens"},
                                    "drop_params": ["top_p"]}}, "models": []}
    p = providers.get_provider(cfg, "openai")
    assert p.rename_params == {"max_tokens": "max_completion_tokens"} and p.drop_params == ["top_p"]
    cap = {}
    _patch_capture(monkeypatch, cap, LINES)
    p.chat("gpt-x", [{"role": "user", "content": "q"}], max_tokens=8, top_p=1.0)
    assert "max_completion_tokens" in cap["json"] and "top_p" not in cap["json"]
