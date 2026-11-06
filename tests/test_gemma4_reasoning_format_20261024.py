"""A gemma4 model must be registered WITH ``extra_body.reasoning_format``.

2026-10-24: three ReadyArt gemma4 models (omega-arsenal, swish, wicked-tutu)
were auto-registered by `bench-all` without the flag. Benched that way the
rig leaks the thinking channel into the answer as literal
`<|channel>thought ... <channel|>` text: every row truncates, the
deterministic graders score 0 and the model is never classified as a thinking
model (no x4 token budget). The rig reports the architecture of every model
it serves, so the auto-register path can set the flag itself.
"""
from __future__ import annotations

import types

import yaml

from crucibleforge import bench_all

CASES = ["C1", "C2"]
JUDGE = "judge/org/judge-q8"


def _cfg(models=()):
    return {"providers": {"sf": {"type": "studioforge"}},
            "models": list(models)}


def _plan(rig, rig_arch=None):
    return bench_all.plan(_cfg(), set(rig), board_labels=[], rows_for=lambda l: [],
                          pending_for=lambda l: 0, attempts_for=lambda l: {},
                          judge_id=JUDGE, case_ids=CASES, rig_arch=rig_arch, register_new=True)


def test_a_gemma4_rig_model_is_registered_with_the_reasoning_flag():
    p = _plan(["org/Ready-Gemma4-Q8_0"], rig_arch={"org/Ready-Gemma4-Q8_0": "gemma4"})
    assert len(p["add"]) == 1
    assert p["add"][0]["extra_body"] == {"reasoning_format": "deepseek"}


def test_other_architectures_are_registered_without_the_flag():
    p = _plan(["org/qwen-q8", "org/llama-q8"],
              rig_arch={"org/qwen-q8": "qwen35", "org/llama-q8": "llama"})
    assert all("extra_body" not in a for a in p["add"])


def test_no_architecture_means_no_flag_never_a_guess():
    p = _plan(["org/Ready-Gemma4-Q8_0"])
    assert p["add"] and "extra_body" not in p["add"][0]


def test_the_flag_reaches_models_yaml(tmp_path):
    f = tmp_path / "models.yaml"
    f.write_text("defaults: {}\nmodels:\n- name: a\n  model_id: org/a\n"
                 "  provider: sf\nproviders:\n  sf: {type: studioforge}\n", encoding="utf-8")
    p = _plan(["org/Ready-Gemma4-Q8_0"], rig_arch={"org/Ready-Gemma4-Q8_0": "gemma4"})
    bench_all.append_registry(f, p["add"], "sf")
    d = yaml.safe_load(f.read_text(encoding="utf-8"))
    entry = next(m for m in d["models"] if m["model_id"] == "org/Ready-Gemma4-Q8_0")
    assert entry["extra_body"] == {"reasoning_format": "deepseek"}
    assert entry["tags"] == ["rig", "auto-added"]


def test_the_architecture_comes_from_the_rig_listing(monkeypatch):
    from crucibleforge import studioforge
    records = [{"id": "org/a", "arch": "gemma4"}, {"id": "org/b", "arch": "Qwen3"}]
    monkeypatch.setattr(studioforge, "list_models_full", lambda *a, **k: records)
    prov = types.SimpleNamespace(base_url="http://rig/v1", api_key="")
    assert bench_all.rig_arches(prov) == {"org/a": "gemma4", "org/b": "qwen3"}


def test_an_unreadable_rig_listing_registers_the_model_as_before(monkeypatch):
    from crucibleforge import studioforge

    def _boom(*a, **k):
        raise RuntimeError("rig unreachable")

    monkeypatch.setattr(studioforge, "list_models_full", _boom)
    prov = types.SimpleNamespace(base_url="http://rig/v1", api_key="")
    assert bench_all.rig_arches(prov) == {}
    p = _plan(["org/Ready-Gemma4-Q8_0"], rig_arch=bench_all.rig_arches(prov))
    assert p["add"] and "extra_body" not in p["add"][0]
