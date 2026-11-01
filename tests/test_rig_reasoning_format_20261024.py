"""The bench asks the rig to separate reasoning for every thinking model.

Root cause of the 2026-10-24 gemma4 zeros: StudioForge launches every model
with ``--reasoning-format none`` (its D12, on purpose: plain OpenAI clients
must never see an empty ``content``), so a thinking model's thoughts arrive
INLINE in ``content``. The bench only coped when (a) a registry entry happened
to carry ``extra_body.reasoning_format`` or (b) the inline tags were a dialect
``api._THINK_TAGS`` knew. gemma4's ``<|channel>`` dialect was neither: tags
leaked, the model was never classified as thinking, budgets stayed un-boosted,
every row truncated. The rig advertises ``capabilities: ["thinking"]`` per
model, so the runner now asks for ``reasoning_format`` itself — and a tripwire
flags any special-token marker that still reaches a graded answer.
"""
from __future__ import annotations

from types import SimpleNamespace

from crucibleforge import providers, runner, studioforge
from crucibleforge.api import ChatResult


def _prov(caps=frozenset(), ptype="studioforge"):
    return SimpleNamespace(name="sf", type=ptype, model_capabilities=lambda mid: set(caps))


def _ctx(entry=None, prov=None, defaults=None):
    e = {"name": "m", "model_id": "m", **(entry or {})}
    return runner._Ctx({"defaults": defaults or {}}, e, prov or _prov({"thinking"}), 32768)


def test_a_rig_thinking_model_gets_reasoning_format_and_the_thinking_budget():
    ctx = _ctx()
    assert ctx.thinking is None
    assert ctx.adopt_rig_reasoning() == "deepseek"
    assert ctx.extra_body["reasoning_format"] == "deepseek"
    assert ctx.thinking is True and ctx.budget(80) == 320


def test_an_explicit_registry_value_wins_even_none():
    ctx = _ctx({"extra_body": {"reasoning_format": "none"}})
    assert ctx.adopt_rig_reasoning() is None
    assert ctx.extra_body["reasoning_format"] == "none"


def test_a_model_without_the_thinking_capability_is_untouched():
    ctx = _ctx(prov=_prov({"tools"}))
    assert ctx.adopt_rig_reasoning() is None
    assert "reasoning_format" not in ctx.extra_body and ctx.thinking is None


def test_a_remote_api_is_untouched():
    ctx = _ctx(prov=_prov({"thinking"}, ptype="openai"))
    assert ctx.adopt_rig_reasoning() is None
    assert "reasoning_format" not in ctx.extra_body


def test_the_profile_can_switch_it_off():
    ctx = _ctx(defaults={"rig_reasoning_format": None})
    assert ctx.adopt_rig_reasoning() is None
    assert "reasoning_format" not in ctx.extra_body


def test_thinking_false_in_the_registry_keeps_the_raw_budget():
    ctx = _ctx({"thinking": False})
    assert ctx.adopt_rig_reasoning() == "deepseek"
    assert ctx.thinking is False and ctx.budget(80) == 80


def test_unreadable_capabilities_change_nothing():
    def boom(mid):
        raise RuntimeError("rig down")
    ctx = _ctx(prov=SimpleNamespace(name="sf", type="studioforge", model_capabilities=boom))
    assert ctx.adopt_rig_reasoning() is None


def test_provider_reads_capabilities_from_the_rig_listing(monkeypatch):
    recs = [{"id": "a", "capabilities": ["tools", "thinking"]}, {"id": "b"}]
    monkeypatch.setattr(studioforge, "list_models_full", lambda *a, **k: recs)
    p = SimpleNamespace(type="studioforge", base_url="http://rig/v1", api_key="")
    assert providers.Provider.model_capabilities(p, "a") == {"tools", "thinking"}
    assert providers.Provider.model_capabilities(p, "b") == set()
    assert providers.Provider.model_capabilities(p, "missing") == set()
    p.type = "openai"
    assert providers.Provider.model_capabilities(p, "a") == set()


# ------------------------------------------------ tripwire: unknown dialects

def test_an_unparsed_special_token_in_an_answer_is_counted():
    ctx = _ctx(prov=_prov())
    ctx._observe(ChatResult(response_text="<|im_start|>assistant\n395"))
    ctx._observe(ChatResult(response_text="17 * 23 = 391, so 395"))
    ctx._observe(ChatResult(response_text="x <turn|> y"))
    assert ctx.unparsed_markers == 2
    w = ctx.marker_warning(3)
    assert w and "2 of 3" in w and "<|im_start|>" in w


def test_ordinary_text_and_code_do_not_trip_it():
    ctx = _ctx(prov=_prov())
    for t in ("if a < b || c > d: pass", "x = y <= z", "<div>|</div>", "<b>bold</b>"):
        ctx._observe(ChatResult(response_text=t))
    assert ctx.unparsed_markers == 0 and ctx.marker_warning(4) is None
