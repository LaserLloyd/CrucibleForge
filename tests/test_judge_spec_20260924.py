"""--judge spec resolution, per-judge sampling, the self-judge switch and the
rig-lock skip for hosted judges (2026-09-24 judge comparison)."""
import types

import pytest

from crucibleforge import cli, judge, providers


def _cfg():
    return {
        "providers": {
            "sf": {"type": "studioforge", "base_url": "http://x/v1"},
            "mm": {"type": "openai", "base_url": "https://api.x/v1", "api_key": "k"},
        },
        "defaults": {},
        "judge": {"temperature": 0.0, "candidates": [
            {"provider": "sf", "model_id": "big/judge-122b", "context_length": 16384,
             "thinking": True},
            {"name": "kimi-judge", "provider": "mm", "model_id": "kimi-k3",
             "thinking": True, "temperature": 1.0, "top_p": 0.95},
        ]},
        "models": [
            {"name": "minimax-m3", "provider": "mm", "model_id": "MiniMax-M3",
             "context_length": 1000000, "judge": {"thinking": True}},
            {"name": "small-27b", "provider": "sf", "model_id": "q/small-27b-Q8_0",
             "context_length": 32768, "thinking": True,
             "extra_body": {"reasoning_format": "deepseek"}},
        ],
    }


def test_spec_by_candidate_name_keeps_its_sampling():
    c = judge.resolve_judge_spec(_cfg(), "kimi-judge")
    assert c["model_id"] == "kimi-k3" and c["top_p"] == 0.95 and c["temperature"] == 1.0


def test_spec_by_provider_model_of_a_candidate():
    c = judge.resolve_judge_spec(_cfg(), "sf:big/judge-122b")
    assert c["thinking"] is True and c["context_length"] == 16384


def test_spec_by_registry_label_uses_judge_block_and_judge_context():
    c = judge.resolve_judge_spec(_cfg(), "minimax-m3")
    assert (c["provider"], c["model_id"], c["thinking"]) == ("mm", "MiniMax-M3", True)
    # the run context (1M) must not leak in: every judge sees the same clamped input
    assert c["context_length"] == judge.JUDGE_DEFAULT_CONTEXT


def test_spec_by_provider_model_of_a_registry_entry_inherits_it():
    c = judge.resolve_judge_spec(_cfg(), "sf:q/small-27b-Q8_0")
    assert c["thinking"] is True and c["extra_body"] == {"reasoning_format": "deepseek"}
    assert c["context_length"] == 16384


def test_spec_unknown_model_on_known_provider_is_plain():
    assert judge.resolve_judge_spec(_cfg(), "mm:other") == {"provider": "mm", "model_id": "other"}


def test_spec_unknown_bare_name_is_an_error():
    with pytest.raises(judge.JudgeError):
        judge.resolve_judge_spec(_cfg(), "nope")


def test_select_judge_refuses_a_contestant_unless_allowed():
    with pytest.raises(judge.JudgeError, match="allow-self-judge"):
        judge.select_judge(_cfg(), {"MiniMax-M3"}, override="minimax-m3")


def test_judge_client_sends_candidate_top_p(monkeypatch):
    seen = {}
    monkeypatch.setattr(providers.Provider, "chat",
                        lambda self, mid, msgs, **kw: seen.update(kw) or "ok")
    jc = judge.JudgeClient(_cfg(), judge.resolve_judge_spec(_cfg(), "kimi-judge"))
    jc.chat([{"role": "user", "content": "x"}])
    assert seen["top_p"] == 0.95 and seen["temperature"] == 1.0
    seen.clear()
    jc = judge.JudgeClient(_cfg(), judge.resolve_judge_spec(_cfg(), "minimax-m3"))
    jc.chat([{"role": "user", "content": "x"}])
    assert "top_p" not in seen and seen["temperature"] == 0.0


def test_run_judge_allow_self_judge_lifts_the_exclusion(monkeypatch):
    rows = [{"needs_judge": True, "rubric": "rp_scene", "case_id": "RPX1"}]
    monkeypatch.setattr(judge, "load_transcripts", lambda label: rows)
    seen = {}

    class Stop(Exception):
        pass

    def fake_load(cfg, benched, **kw):
        seen["benched"] = set(benched)
        raise Stop
    monkeypatch.setattr(judge, "_load_judge", fake_load)
    with pytest.raises(Stop):
        judge.run_judge(_cfg(), ["minimax-m3"], judge_override="minimax-m3")
    assert seen["benched"] == {"MiniMax-M3"}
    with pytest.raises(Stop):
        judge.run_judge(_cfg(), ["minimax-m3"], judge_override="minimax-m3",
                        allow_self_judge=True)
    assert seen["benched"] == set()


def test_remote_judge_skips_the_rig_lock_rig_judge_takes_it():
    cfg = _cfg()
    remote = types.SimpleNamespace(cmd="judge", judge="minimax-m3", profile=None)
    rig = types.SimpleNamespace(cmd="judge", judge="sf:big/judge-122b", profile=None)
    run = types.SimpleNamespace(cmd="run", judge="minimax-m3", profile=None)
    assert cli._remote_judge_only(remote, cfg) is True
    assert cli._remote_judge_only(rig, cfg) is False
    assert cli._remote_judge_only(run, cfg) is False


def test_guard_for_a_hosted_judge_does_not_touch_the_rig(monkeypatch):
    monkeypatch.setattr(providers.Provider, "snapshot", lambda self: [])
    g = cli._ProviderGuard(_cfg(), [], judge="minimax-m3")
    assert g.provs == {}
    g = cli._ProviderGuard(_cfg(), [], judge="sf:big/judge-122b")
    assert list(g.provs) == ["sf"]
