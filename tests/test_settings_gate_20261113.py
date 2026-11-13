"""Recommended-settings gate + card-check (2026-11-13).

No rig model is benchmarked before its maker's recommended sampler and chat
template were checked: a profile rule / registry ``sampler:``, or an explicit
``settings_checked`` block for a model that publishes none. Hosted API models
are exempt. card-check proposes the entry from the HF card(s)."""
from __future__ import annotations

import json

from crucibleforge import bench_all, cardcheck, settings_gate

DEPLOYED = {"defaults": {"regime": "deployed", "recommended_samplers": [
    {"id": "qwen3-thinking", "match": "Qwen3\\.[68]", "sampler": {"temperature": 1.0, "top_p": 0.95}},
    {"id": "api-vendor-fixed", "match": "^(deepseek):", "sampler": {}}]}}
CHECKED = {"sampler": "none_published", "source": "https://huggingface.co/org/m",
           "template": "enable_thinking switch, default on"}


def _e(name, mid, **kw):
    return {"name": name, "model_id": mid, "provider": "sf", **kw}


def test_rule_registry_and_checked_pass_default_is_refused():
    ok = settings_gate.check(DEPLOYED, _e("q", "org/Qwen3.8-27B-GGUF/x-Q8_0"), "studioforge")
    assert ok.ok and ok.basis == "rule:qwen3-thinking"
    assert settings_gate.check(DEPLOYED, _e("r", "org/m", sampler={"temperature": 0.7}),
                               "studioforge").basis == "registry"
    no = settings_gate.check(DEPLOYED, _e("gpt-oss", "unsloth/gpt-oss-20b-GGUF/x"), "studioforge")
    assert not no.ok and no.basis == "default"
    assert "crucibleforge card-check unsloth/gpt-oss-20b-GGUF/x" in no.fix and "settings_checked:" in no.fix
    assert "sampler: none_published" in no.fix
    yes = settings_gate.check(DEPLOYED, _e("gpt-oss", "unsloth/gpt-oss-20b-GGUF/x",
                                           settings_checked=CHECKED), "studioforge")
    assert yes.ok and yes.basis == "checked:none_published"


def test_api_models_and_the_fixed_regime_are_exempt():
    assert settings_gate.check(DEPLOYED, _e("c", "claude-x"), "openai").ok
    assert settings_gate.check(DEPLOYED, _e("d", "deepseek-chat"), "anthropic").basis == "api"
    assert settings_gate.check({"defaults": {}}, _e("g", "org/gpt"), "studioforge").basis == "fixed-regime"


def test_an_incomplete_checked_block_is_refused_with_the_reason():
    for bad, why in (({**CHECKED, "sampler": "TODO"}, "must be one of"),
                     ({**CHECKED, "source": "the card"}, "URL"),
                     ({**CHECKED, "template": ""}, "template")):
        v = settings_gate.check(DEPLOYED, _e("m", "org/m", settings_checked=bad), "studioforge")
        assert not v.ok and why in v.reason


def test_a_requant_registration_inherits_the_check():
    e = {"name": "m-q8", "model_id": "org/m-Q8_0", "inherit": {"settings_checked": CHECKED}}
    assert settings_gate.check(DEPLOYED, e, "studioforge").ok
    assert "settings_checked" in bench_all.INHERIT_KEYS


QWEN_TPL = ("{%- if add_generation_prompt %}\n{{- '<|im_start|>assistant\\n' }}\n"
            "{%- if enable_thinking is defined and enable_thinking is false %}\n"
            "{{- '<think>\\n\\n</think>\\n\\n' }}\n{%- endif %}\n{%- endif %}")
FORCED_TPL = ("{% for m in messages %}{{ m.content }}{% endfor %}"
              "{%- if add_generation_prompt %}{{- '<|assistant|>\\n<think>\\n' }}{%- endif %}"
              "{% if preserve_thinking %}{{ reasoning_content }}{% endif %}")


def test_template_facts():
    f = {x["id"]: x for x in settings_gate.template_facts(QWEN_TPL)}
    assert "think_switch_in_prompt" in f and "switch:enable_thinking" in f
    assert f["switch:enable_thinking"]["level"] == "WARN"
    g = {x["id"]: x for x in settings_gate.template_facts(FORCED_TPL, acknowledged=True)}
    assert "think_forced_open" in g and g["think_forced_open"]["level"] == "INFO"
    assert "switch:preserve_thinking" in g and "history_reasoning" in g
    assert settings_gate.template_facts("{{ messages }}") == []


def test_facts_ride_on_the_verdict_but_never_decide():
    v = settings_gate.check(DEPLOYED, _e("q", "org/Qwen3.8-x"), "studioforge", FORCED_TPL)
    assert v.ok and any(f["id"] == "think_forced_open" and f["level"] == "WARN" for f in v.facts)


def _plan():
    return {"candidates": ["q", "gpt", "api"], "add": [{"name": "new", "model_id": "org/new-Q8_0"}],
            "groups": [{"models": ["q", "gpt", "new"], "cases": ["C1"]}],
            "judge": ["q", "gpt", "new"], "escalate": [{"model": "gpt", "jobs": 2}],
            "ladder": [{"model": "gpt", "jobs": 1, "sets": []}]}


def _cfg():
    return {"providers": {"sf": {"type": "studioforge", "base_url": "http://rig"},
                          "ds": {"type": "openai", "base_url": "http://api"}},
            "models": [_e("q", "org/Qwen3.8-27B-Q8_0"), _e("gpt", "org/gpt-oss-20b-Q8_0"),
                       {"name": "api", "model_id": "deepseek-chat", "provider": "ds"}]}


def test_gate_plan_takes_blocked_models_out_of_every_phase():
    p = settings_gate.gate_plan(_plan(), _cfg(), cfg_p={**_cfg(), **DEPLOYED})
    assert p["settings_blocked"] == ["gpt", "new"]
    assert p["candidates"] == ["q", "gpt", "api"]
    assert p["groups"] == [{"models": ["q"], "cases": ["C1"]}]
    assert p["judge"] == ["q", "gpt", "new"] and p["escalate"] == [] and p["ladder"] == []
    assert p["add"] == [{"name": "new", "model_id": "org/new-Q8_0"}]     # still registered
    assert p["settings"]["api"]["basis"] == "api"
    text = "\n".join(settings_gate.plan_lines(p))
    assert "NOT RUN gpt" in text and "card-check org/gpt-oss-20b-Q8_0" in text


def test_plan_json_says_settings_unchecked():
    from datetime import datetime
    p = settings_gate.gate_plan({**_plan(), "rig_models": 3, "case_count": 1, "prune": [],
                                 "not_on_rig": [], "per_model": {}},
                                _cfg(), cfg_p={**_cfg(), **DEPLOYED})
    out = bench_all.plan_json(p, _cfg(), until=None, start=datetime(2026, 11, 13, 21, 0),
                              estimate_for=lambda *a, **k: (1.0, 1.0),
                              pending_for=lambda labels: 0.0)
    assert out["models"]["gpt"]["verdict"] == "settings_unchecked"
    assert out["models"]["new"]["verdict"] == "settings_unchecked" and out["models"]["new"]["register"]
    assert out["models"]["gpt"]["settings"]["ok"] is False
    assert out["models"]["q"]["verdict"] != "settings_unchecked"
    json.dumps(out)


# ------------------------------------------------------------- card-check

README = """---
base_model: Org/Model-33B
---
# Model GGUF
"""
BASE = """# Model
We recommend:

| Scenario | Temperature | Top_p | Max New Tokens |
|---|---:|---:|---:|
| Agentic | 1.0 | 0.95 | 65,536 |
| Reasoning and chat | 0.6 | 0.95 | 262,144 |

Or use temperature=0.7, top_k = 20 for creative work.
"""


def _fetch(pages):
    return lambda url: pages.get(url)


def test_card_check_follows_base_model_and_reads_tables():
    H = cardcheck.HF
    pages = {f"{H}/bart/Model-33B-GGUF/raw/main/README.md": README,
             f"{H}/Org/Model-33B/raw/main/README.md": BASE,
             f"{H}/Org/Model-33B/raw/main/tokenizer_config.json": json.dumps({"chat_template": QWEN_TPL}),
             f"{H}/Org/Model-33B/raw/main/generation_config.json": json.dumps({"temperature": 0.6,
                                                                              "do_sample": True})}
    cfg = {"models": [{"name": "model-33b-q8-0", "model_id": "bart/Model-33B-GGUF/Model-33B-Q8_0"}]}
    res = cardcheck.check(cfg, "model-33b-q8-0", fetch=_fetch(pages))
    assert [c["repo"] for c in res["cards"]] == ["bart/Model-33B-GGUF", "Org/Model-33B"]
    base = res["cards"][1]
    params = [s["params"] for s in base["sampler_lines"]]
    assert {"temperature": 0.6, "top_p": 0.95} in params and {"temperature": 1.0, "top_p": 0.95} in params
    assert {"temperature": 0.7, "top_k": 20} in params
    assert base["generation_config"] == {"temperature": 0.6, "do_sample": True}
    assert any(f["id"] == "switch:enable_thinking" for f in base["template_facts"])
    text = cardcheck.format_check(res)
    assert "recommended_samplers" in text and "https://huggingface.co/Org/Model-33B" in text


def test_card_check_without_a_published_sampler_proposes_settings_checked():
    H = cardcheck.HF
    pages = {f"{H}/a/b/raw/main/README.md": "# just a model\n",
             f"{H}/a/b/raw/main/generation_config.json": json.dumps({"do_sample": True})}
    res = cardcheck.check(None, "a/b", fetch=_fetch(pages))
    text = cardcheck.format_check(res)
    assert "settings_checked:" in text and "none_published" in text
    assert "https://huggingface.co/a/b" in text


def test_run_refuses_an_unchecked_rig_model_and_writes_nothing(tmp_path, monkeypatch):
    from crucibleforge import cli
    from crucibleforge import config as cfgmod
    monkeypatch.setattr(cfgmod, "RESULTS_DIR", tmp_path)
    cfgmod.set_results_dir(tmp_path)
    cfg = {**_cfg(), **DEPLOYED}
    ok, bad = cli._settings_gate(cfg, cfg["models"])
    assert [e["name"] for e in ok] == ["q", "api"]
    assert list(bad) == ["gpt"] and "needs recommended settings" in bad["gpt"]
    assert list(tmp_path.iterdir()) == []          # no FAILED meta on the board


def test_card_check_proposes_nothing_when_no_card_was_read():
    res = cardcheck.check(None, "some-label", fetch=lambda url: None)
    text = cardcheck.format_check(res)
    assert "NOTHING proposed" in text and "none_published" not in text


def test_template_facts_edge_cases():
    elif_tpl = "{% if x %}a{% elif add_generation_prompt %}{{ '<seed:think>' }}{% endif %}"
    assert any(f["id"] == "think_forced_open" for f in settings_gate.template_facts(elif_tpl))
    cond = "{% if add_generation_prompt %}{% if thinking %}{{ '<think>\\n' }}{% endif %}{% endif %}"
    assert [f["id"] for f in settings_gate.template_facts(cond)] == ["think_switch_in_prompt"]
    assert settings_gate.template_facts("{# enable_thinking is documented here #}{{ x }}") == []


def test_complete_unchecked_models_are_left_alone_and_judge_is_kept():
    p = _plan()
    p["groups"] = [{"models": ["q"], "cases": ["C1"]}]
    p["escalate"], p["ladder"], p["add"] = [], [], []
    p = settings_gate.gate_plan(p, _cfg(), cfg_p={**_cfg(), **DEPLOYED})
    assert p["settings_blocked"] == []                 # gpt has nothing to generate
    assert "gpt" in p["judge"] and p["settings"]["gpt"]["ok"] is False


def test_unregistered_fix_says_how_to_register():
    p = settings_gate.gate_plan(_plan(), _cfg(), cfg_p={**_cfg(), **DEPLOYED})
    fix = p["settings"]["new"]["fix"]
    assert "not registered yet" in fix and "card-check org/new-Q8_0" in fix
