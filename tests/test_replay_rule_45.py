"""4.5 reasoning-replay rule (EXP-20261114-11).

Replay follows what the model's own chat template does at default settings
(a no-GPU render); a documented card/API rule overrides it; the record
carries {value, source, quote/url, template_sha}; a changed template fails
closed; ``--inherit-from`` re-renders instead of copying; conformance c3 is
a diagnostic warning; rows stamp the source and what was replayed."""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
import yaml

from crucibleforge import bench_all, conformance, replay_rule, runner
from crucibleforge.api import ChatResult

# --- minimal templates in the shapes the rig serves -------------------------
#: Qwen3.8 / Dark-Scarlett shape: keeps every prior turn's reasoning unless preserve_thinking=false
KEEP = """{%- set ns = namespace(multi_step=true, last_query_index=messages|length - 1) %}
{%- for message in messages[::-1] %}{%- if ns.multi_step and message.role == 'user' %}{%- set ns.multi_step = false %}{%- set ns.last_query_index = (messages|length - 1) - loop.index0 %}{%- endif %}{%- endfor %}
{%- for message in messages %}
{%- if message.role == 'assistant' %}
{%- set rc = message.reasoning_content if message.reasoning_content is string else '' %}
{%- if preserve_thinking is undefined or preserve_thinking is true or loop.index0 > ns.last_query_index %}
{{- '<|im_start|>assistant\\n<think>\\n' + rc + '\\n</think>\\n\\n' + message.content + '<|im_end|>\\n' }}
{%- else %}
{{- '<|im_start|>assistant\\n' + message.content + '<|im_end|>\\n' }}
{%- endif %}
{%- else %}
{{- '<|im_start|>' + message.role + '\\n' + message.content + '<|im_end|>\\n' }}
{%- endif %}
{%- endfor %}
{%- if add_generation_prompt %}{{- '<|im_start|>assistant\\n<think>\\n' }}{%- endif %}"""
#: Qwen3 / Qwen3.5 shape: reasoning only after the last user message
DROP = KEEP.replace("preserve_thinking is undefined or preserve_thinking is true or ", "")
#: gemma-2 shape: refuses a system role, never reads reasoning
NOSYS = """{%- for message in messages %}{%- if message.role == 'system' %}{{ raise_exception('System role not supported') }}{%- endif %}
{{- '<start_of_turn>' + message.role + '\\n' + message.content + '<end_of_turn>\\n' }}{%- endfor %}
{%- if add_generation_prompt %}{{ '<start_of_turn>model\\n' }}{%- endif %}"""

NB_RULE = {"id": "nanbeige4-chat", "match": "(?i)nanbeige4", "value": False, "source": "card",
           "url": "https://huggingface.co/Nanbeige/Nanbeige4.3-33B", "quote": "We recommend `False` for general chat"}


def _cfg_p(rules=()):
    return {"defaults": {"reasoning_replay_rules": list(rules)}}


def _sha(t):
    return hashlib.sha256(t.encode()).hexdigest()[:16]


# --- 1. the render ------------------------------------------------------------

def test_render_probe_reads_the_template_default():
    assert replay_rule.render_probe(KEEP) == {"keeps": True, "error": ""}
    assert replay_rule.render_probe(DROP) == {"keeps": False, "error": ""}
    # a template that refuses the system role still decides (probe without it)
    assert replay_rule.render_probe(NOSYS)["keeps"] is False
    assert replay_rule.render_probe("{{ raise_exception('boom') }}")["keeps"] is None
    assert replay_rule.render_probe(None)["keeps"] is None


def test_resolve_template_card_and_api_default():
    e = {"name": "q", "model_id": "unsloth/Qwen3.8-27B-GGUF/Qwen3.8-27B-Q8_0"}
    rec = replay_rule.resolve(_cfg_p(), e, KEEP, "studioforge")
    assert rec["value"] is True and rec["source"] == "template" and rec["template_sha"] == _sha(KEEP)
    assert "preserve_thinking" in rec["quote"]
    # a documented card recommendation overrides the render (Nanbeige: keeps by template, False for chat)
    nb = {"name": "nb", "model_id": "bartowski/Nanbeige_Nanbeige4.3-33B-GGUF/x-Q8_0"}
    rec = replay_rule.resolve(_cfg_p([NB_RULE]), nb, KEEP, "studioforge")
    assert rec["value"] is False and rec["source"] == "card" and rec["rule"] == "nanbeige4-chat"
    assert rec["template_value"] is True and rec["url"].startswith("https://")
    # a rig model with no readable template: nothing decided
    assert replay_rule.resolve(_cfg_p(), e, None, "studioforge") is None
    # a hosted API with no template and no rule: the provider default
    api = {"name": "a", "model_id": "some-api-model"}
    assert replay_rule.resolve(_cfg_p(), api, None, "openai", provider_default=True) == \
        {"value": True, "source": "default"}


# --- 2. the preflight -------------------------------------------------------

def test_check_new_ok_stale_mismatch_unreadable():
    e = {"name": "q", "model_id": "m/q-Q8_0", "reasoning_replay": True}     # legacy bool
    v = replay_rule.check(_cfg_p(), e, "studioforge", KEEP)
    assert v.ok and v.status == "new" and v.was is True and v.record["value"] is True
    e2 = {**e, "reasoning_replay": v.record}
    assert replay_rule.check(_cfg_p(), e2, "studioforge", KEEP).status == "ok"
    # the served template changed: fails closed, names the fix
    v = replay_rule.check(_cfg_p(), e2, "studioforge", KEEP + "\n{# new #}")
    assert not v.ok and v.status == "stale" and "replay-rule q --write" in v.fix
    # a rule added since the record was written: refused until re-rendered
    v = replay_rule.check(_cfg_p([{**NB_RULE, "match": "q-Q8"}]), e2, "studioforge", KEEP)
    assert not v.ok and v.status == "mismatch"
    # no record and no readable template: refused; a record stands when the rig listing is unreadable
    v = replay_rule.check(_cfg_p(), {"name": "x", "model_id": "m/x"}, "studioforge", None)
    assert not v.ok and v.status == "unreadable"
    assert replay_rule.check(_cfg_p(), e2, "studioforge", None).ok


def test_gate_plan_records_new_and_plans_out_a_changed_template():
    rec_old = {"value": True, "source": "template", "template_sha": "0000000000000000"}
    cfg = {"providers": {"rig": {"type": "studioforge"}},
           "models": [{"name": "a", "model_id": "m/a", "provider": "rig"},
                      {"name": "b", "model_id": "m/b", "provider": "rig", "reasoning_replay": rec_old}]}
    p = {"candidates": ["a", "b"], "groups": [{"models": ["a", "b"]}], "add": []}
    replay_rule.gate_plan(p, cfg, _cfg_p(), {"m/a": DROP, "m/b": KEEP})
    assert p["replay_new"] == {"a": {"value": False, "source": "template", "template_sha": _sha(DROP),
                                     "quote": p["replay_new"]["a"]["quote"]}}
    assert p["replay_blocked"] == ["b"] and p["groups"] == [{"models": ["a"]}]
    lines = "\n".join(replay_rule.plan_lines(p))
    assert "+ replay a: False (template)" in lines and "NOT RUN b" in lines


def test_inherit_from_does_not_copy_replay():
    assert "reasoning_replay" not in bench_all.INHERIT_KEYS
    cfg = {"models": [{"name": "old", "model_id": "m/old-Q5", "reasoning_replay": True, "thinking": True}]}
    assert "reasoning_replay" not in bench_all.inherited_settings(cfg, "old")


# --- 3. the registry write ----------------------------------------------------

def test_write_records_replaces_inserts_and_keeps_comments(tmp_path: Path):
    path = tmp_path / "models.yaml"
    path.write_text("""providers:
  rig: {type: studioforge}
models:
# a comment that must survive
- name: a
  model_id: m/a
  reasoning_replay: true  # 2026-11-06: conformance c3 FAIL
  thinking: true
- name: b
  model_id: m/b
  enabled: true
""", encoding="utf-8")
    recs = {"a": {"value": True, "source": "template", "template_sha": "abc", "quote": "{%- if x: y %}"},
            "b": {"value": False, "source": "card", "rule": "r", "url": "https://x", "quote": "q"}}
    assert replay_rule.write_records(path, recs) == ["a", "b"]
    text = path.read_text(encoding="utf-8")
    assert "# a comment that must survive" in text and "c3 FAIL" not in text
    d = yaml.safe_load(text)
    a, b = d["models"]
    assert a["reasoning_replay"]["value"] is True and a["reasoning_replay"]["template_sha"] == "abc"
    assert a["thinking"] is True and b["enabled"] is True
    assert b["reasoning_replay"]["source"] == "card" and b["reasoning_replay"]["checked"]
    with pytest.raises(ValueError):
        replay_rule.write_records(path, {"nope": recs["a"]})


# --- 4. the run side: value, stamp, what was replayed -------------------------

class _P:
    name, type = "fake", "studioforge"
    extra_body, reasoning_replay = {}, False

    def __init__(self, replies):
        self.replies, self.msgs = list(replies), []

    def chat(self, model_id, messages, **kw):
        self.msgs.append([dict(m) for m in messages])
        c, r = self.replies.pop(0)
        return ChatResult(response_text=c, reasoning_text=r, finish_reason="stop",
                          completion_tokens=10, prompt_tokens=100, served_model="m")

    def switch_model(self, *a, **k):
        return 0.0


def _ctx(p, replay):
    entry = {"name": "m", "model_id": "m", "thinking": True}
    if replay is not None:
        entry["reasoning_replay"] = replay
    ctx = runner._Ctx({"defaults": {}}, entry, p, 32768)
    ctx.recovery_enabled = False
    return ctx


def test_ctx_reads_the_record_and_the_protocol_stamps_its_source():
    p = _P([])
    ctx = _ctx(p, {"value": True, "source": "template", "template_sha": "abc"})
    assert ctx.reasoning_replay is True and ctx.reasoning_replay_source == "template"
    assert _ctx(p, {"value": False, "source": "card"}).reasoning_replay is False
    assert _ctx(p, True).reasoning_replay_source == "legacy"
    assert _ctx(p, None).reasoning_replay_source == "default"
    proto = conformance.protocol_fingerprint({"defaults": {}}, ctx.entry, p, ctx)
    assert proto["reasoning_replay"] is True and proto["reasoning_replay_source"] == "template"
    # provenance only: a record that only gained its source is the same protocol
    old = {k: v for k, v in proto.items() if k != "reasoning_replay_source"}
    assert conformance.comparable_protocol(proto) == conformance.comparable_protocol(old)


@pytest.mark.parametrize("replay", [True, False])
def test_rows_store_what_was_replayed(replay):
    p = _P([("Turn one.", "secret chain one"), ("Turn two.", "chain two")])
    rows = runner._run_multiturn({"id": "S", "category": "rpdeep", "turns": ["a", "b"], "max_tokens": 64,
                                  "rubric": "rp_multi"}, {}, _ctx(p, {"value": replay, "source": "template"}),
                                 64, 0.6, 0.95, 1)
    assert "replayed_reasoning" not in rows[0]
    if replay:
        sent = rows[1]["replayed_reasoning"]
        assert sent == [{"msg": 1, "chars": len("secret chain one"),
                         "sha256": hashlib.sha256(b"secret chain one").hexdigest()[:16]}]
        assert p.msgs[1][1]["reasoning_content"] == "secret chain one"
    else:
        assert "replayed_reasoning" not in rows[1]
        assert "reasoning_content" not in p.msgs[1][1]
    # the judged conversation stays content-only either way
    assert all("reasoning_content" not in m for m in rows[1]["conversation"])


# --- 5. c3 is a diagnostic ------------------------------------------------------

def test_c3_never_fails_and_old_c3_fails_do_not_block(monkeypatch):
    arms = iter([{"recalled": 0, "conclusive": 3, "no_reasoning": 0, "wrote_down": 0, "errors": 0, "error": ""},
                 {"recalled": 3, "conclusive": 3, "no_reasoning": 0, "wrote_down": 0, "errors": 0, "error": ""}])
    monkeypatch.setattr(conformance, "_chain_arm", lambda *a, **k: next(arms))
    monkeypatch.setattr(conformance, "_make_ctx", lambda *a, **k: type("C", (), {})())
    p = _P([])
    ctx = _ctx(p, {"value": False, "source": "card"})
    chk = conformance._c3_chain({"defaults": {}}, ctx.entry, p, ctx)
    assert chk.status == conformance.WARN and "diagnostic" in chk.detail and "source card" in chk.detail
    old = {"verdict": "FAIL", "checks": [{"id": "c1", "status": "PASS"},
                                         {"id": "c3", "status": "FAIL", "detail": "replay setting is wrong"}]}
    assert conformance.effective_verdict(old) == conformance.WARN
    assert conformance.blocking_fails(old["checks"]) == []
    real = {"verdict": "FAIL", "checks": [{"id": "c4", "status": "FAIL", "detail": "malformed history"}]}
    assert conformance.effective_verdict(real) == conformance.FAIL


def test_bench45_profile_rules():
    from crucibleforge import profiles
    prof = profiles.load_profile("bench45")
    cfg_p, _ = profiles.apply_profile(prof, {"defaults": {}})
    rules = {r["id"]: r for r in cfg_p["defaults"]["reasoning_replay_rules"]}
    assert set(rules) >= {"nanbeige4-chat", "flint-card", "minimax-api", "kimi-k3-api", "deepseek-api-chat"}
    for r in rules.values():
        assert r["source"] in ("card", "api-doc") and r["url"].startswith("https://") and r["quote"]
    nb = {"model_id": "bartowski/Nanbeige_Nanbeige4.3-33B-GGUF/Nanbeige_Nanbeige4.3-33B-Q8_0"}
    assert replay_rule.match_rule(cfg_p, nb)["value"] is False
    q = {"model_id": "unsloth/Qwen3.8-27B-GGUF/Qwen3.8-27B-Q8_0"}
    assert replay_rule.match_rule(cfg_p, q) is None          # Qwen3.8 follows its template


def test_an_ab_rule_overrides_the_template_and_carries_its_evidence():
    import pytest
    from crucibleforge import replay_rule
    tpl = "{% for m in messages %}{{ m.reasoning_content or '' }}{{ m.content }}{% endfor %}"
    rule = {"id": "qwen38-ab", "match": "Qwen3.8", "value": False, "source": "ab",
            "evidence": "EXP-2026xxxx: replay off, same 12 sessions, cut turns 20 -> n"}
    cfg = {"defaults": {"reasoning_replay_rules": [rule]}}
    rec = replay_rule.resolve(cfg, {"model_id": "unsloth/Qwen3.8-27B-GGUF/x"}, tpl, "studioforge")
    assert rec["value"] is False and rec["source"] == "ab" and rec["evidence"].startswith("EXP-")
    assert "evidence:" in replay_rule.flow(rec)
    bad = {"defaults": {"reasoning_replay_rules": [{**rule, "evidence": None}]}}
    with pytest.raises(ValueError):
        replay_rule.resolve(bad, {"model_id": "unsloth/Qwen3.8-27B-GGUF/x"}, tpl, "studioforge")
