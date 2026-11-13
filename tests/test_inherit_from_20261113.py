"""bench-all --inherit-from: a re-test of the same model at another quant is registered with the original's
model-behaviour settings (conformance c3 failed two Q8_0 retests that had none)."""
import pytest
import yaml

from crucibleforge import bench_all

CFG = {"models": [
    {"name": "orig", "model_id": "org/o/o-Q6_K", "provider": "sf", "context_length": 8192, "censored": False,
     "thinking": True, "reasoning_replay": True, "extra_body": {"reasoning_format": "deepseek"},
     "sampler": {"temperature": 0.6}, "max_output_tokens": 4096, "tags": ["rig", "auto-added", "heretic"],
     "price": {"input": 1}, "enabled": True}]}


def test_inherited_settings_copies_behaviour_only():
    s = bench_all.inherited_settings(CFG, "orig")
    assert s["reasoning_replay"] is True and s["thinking"] is True and s["censored"] is False
    assert s["extra_body"] == {"reasoning_format": "deepseek"} and s["sampler"] == {"temperature": 0.6}
    assert s["tags"] == ["rig", "heretic"]
    assert not {"name", "model_id", "context_length", "provider", "enabled", "price"} & set(s)
    with pytest.raises(ValueError):
        bench_all.inherited_settings(CFG, "nope")


def test_append_registry_writes_inherited_keys_and_leaves_others(tmp_path):
    f = tmp_path / "models.yaml"
    f.write_text("models:\n- name: orig\n  model_id: org/o/o-Q6_K\n  provider: sf\n  thinking: true\n"
                 "providers:\n  sf: {type: studioforge}\n", encoding="utf-8")
    before = yaml.safe_load(f.read_text())["models"][0]
    bench_all.append_registry(f, [{"name": "q8", "model_id": "org/o/o-Q8_0", "extra_body": {"a": 1},
                                   "inherit": bench_all.inherited_settings(CFG, "orig")}], "sf")
    m = {x["name"]: x for x in yaml.safe_load(f.read_text())["models"]}
    n = m["q8"]
    assert n["reasoning_replay"] is True and n["thinking"] is True and n["censored"] is False
    assert n["extra_body"] == {"a": 1, "reasoning_format": "deepseek"} and n["sampler"] == {"temperature": 0.6}
    assert n["max_output_tokens"] == 4096 and n["tags"] == ["rig", "auto-added", "heretic"]
    assert n["model_id"] == "org/o/o-Q8_0" and n["context_length"] == 32768 and "price" not in n
    assert m["orig"] == before
