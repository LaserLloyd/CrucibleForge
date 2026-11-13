"""MakoBench sizing round (2026-11-06): row facts quant_reason / served /
served_precision (K8), bench-all --quant-reason, the openrouter-q5plus
provider, and judge-only planning for a hosted model whose rows are complete."""
from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

import httpx
import pytest
import yaml

from crucibleforge import api, bench_all, cli, providers, report
from crucibleforge import config as cfgmod

ROOT = Path(__file__).resolve().parent.parent
CASES = ["C1", "C2", "C3"]
JUDGE = "judge/org/judge-q8"
Q5PLUS = ["fp6", "int8", "fp8", "fp16", "bf16", "fp32"]


@pytest.fixture(autouse=True)
def _settings_gate_passes(monkeypatch):
    """These fixtures carry no recommended sampler; the settings gate (2026-11-13) has its own
    tests (test_settings_gate_20261113.py), so here every model passes it."""
    from crucibleforge import settings_gate
    monkeypatch.setattr(settings_gate, "check", lambda cfg_p, entry, *a, **k: settings_gate.Verdict(
        ok=True, label=str(entry.get("name") or entry.get("model_id") or ""), basis="test"))


def _cfg(models):
    return {"providers": {"sf": {"type": "studioforge"}, "ds": {"type": "openai"}}, "models": models}


# ------------------------------------------------------------ K8 row facts

def test_row_facts_carry_quant_reason_served_and_precision():
    cfg = _cfg([
        {"name": "big", "model_id": "org/Big-Q6_K.gguf", "provider": "sf", "quant": "Q6_K",
         "quant_reason": "q8_does_not_fit"},
        {"name": "rent", "model_id": "x/y", "provider": "ds", "served": "rented",
         "served_precision": "Q6_K"},
        {"name": "api", "model_id": "z/w", "provider": "ds", "served_precision": "fp8"},
    ])
    big = report.row_facts("big", {}, cfg)
    assert big["quant"] == "Q6_K" and big["quant_standard"] is False
    assert big["quant_reason"] == "q8_does_not_fit" and big["served"] == "rig"
    assert big["served_precision"] is None
    rent = report.row_facts("rent", {}, cfg)
    assert rent["served"] == "rented" and rent["served_precision"] == "Q6_K"
    assert rent["quant_reason"] is None
    api_ = report.row_facts("api", {}, cfg)
    assert api_["served"] == "hosted" and api_["served_precision"] == "fp8"
    none = report.row_facts("ghost", {}, cfg)
    assert none["quant_reason"] is None and none["served_precision"] is None


# ------------------------------------------------------ --quant-reason

class _Rig:
    def __init__(self, ids):
        self.ids = ids

    def list_models(self, refresh=False):
        return set(self.ids)


def _run(monkeypatch, tmp_path, rig, **kw):
    monkeypatch.setattr(cfgmod, "RESULTS_DIR", tmp_path)
    models = tmp_path / "models.yaml"
    models.write_text("providers:\n  sf: {type: studioforge, base_url: http://127.0.0.1:9/v1}\nmodels:\n"
                      "- name: a\n  model_id: org/a\n  provider: sf\n"
                      "judge:\n  temperature: 0.0\n  max_tokens: 4096\n"
                      "defaults:\n  context_length: 32768\n", encoding="utf-8")
    cfg = {"providers": {"sf": {"type": "studioforge"}},
           "models": [{"name": "a", "model_id": "org/a", "provider": "sf"}], "_path": str(models)}
    monkeypatch.setattr(providers, "get_provider", lambda c, n: _Rig(rig))
    monkeypatch.setattr(bench_all, "rig_arches", lambda prov: {})
    monkeypatch.setattr(cli, "cmd_run", lambda a, c: 0)
    monkeypatch.setattr(cli, "cmd_judge", lambda a, c: 0)
    monkeypatch.setattr(cli, "cmd_escalate", lambda a, c: 0)
    monkeypatch.setattr(cli, "_ladder_phases", lambda a, c, l: 0)
    monkeypatch.setattr(cli, "cmd_report", lambda a, c, full_board=False: 0)
    base = dict(go=True, include_api=False, no_sync=False, no_prune=True, first=None, until=None,
                judge_reserve_min=45, escalate=False, no_escalate=False, no_ladder=True,
                skip_conformance=True, only=None, lease_mode=None, no_judge=False,
                plan_json=False, plan_start=None, quant_reason=None)
    base.update(kw)
    rc = bench_all.execute(argparse.Namespace(**base), cfg)
    return rc, yaml.safe_load(models.read_text(encoding="utf-8"))


def test_quant_reason_is_written_on_a_single_new_registration(monkeypatch, tmp_path):
    rc, doc = _run(monkeypatch, tmp_path, ["org/a", "org/new-70b"], only="new-70b",
                   quant_reason="q8_does_not_fit")
    assert rc == 0
    new = next(m for m in doc["models"] if m["name"] == "new-70b")
    assert new["quant_reason"] == "q8_does_not_fit"


@pytest.mark.parametrize("kw", [
    dict(only=None),                                 # no --only
    dict(only="new-70b,new-8b"),                     # two labels
    dict(only="a"),                                  # not a new registration
    dict(only="new-70b", quant_reason="whatever"),   # unknown reason
])
def test_quant_reason_is_refused_outside_a_single_new_registration(monkeypatch, tmp_path, kw):
    kw.setdefault("quant_reason", "q8_does_not_fit")
    rc, doc = _run(monkeypatch, tmp_path, ["org/a", "org/new-70b", "org/new-8b"], **kw)
    assert rc == bench_all.EXIT_USAGE
    assert [m["name"] for m in doc["models"]] == ["a"]       # nothing registered


def test_quant_reason_flag_parses(monkeypatch):
    seen = {}
    monkeypatch.setattr(cli, "load_config", lambda *a, **k: {"models": [], "providers": {}})
    monkeypatch.setattr(cli, "cmd_bench_all", lambda args, cfg: seen.setdefault("a", args) and 0)
    monkeypatch.setattr(cli, "acquire_rig_lock", lambda: None)
    with pytest.raises(SystemExit) as e:
        cli.main(["bench-all", "--only", "x", "--quant-reason", "q8_does_not_fit"])
    assert e.value.code == 0 and seen["a"].quant_reason == "q8_does_not_fit"


# ------------------------------------------------- openrouter-q5plus

def test_models_yaml_has_the_q5plus_provider_as_an_openrouter_copy():
    for name in ("models.yaml", "models.example.yaml"):
        if not (ROOT / name).exists():          # models.yaml is operator-local (untracked)
            continue
        provs = yaml.safe_load((ROOT / name).read_text(encoding="utf-8"))["providers"]
        base, q5 = provs["openrouter"], provs["openrouter-q5plus"]
        for k in ("type", "base_url", "api_key_env"):
            assert q5[k] == base[k]
        assert q5["extra_body"] == {"provider": {"quantizations": Q5PLUS, "allow_fallbacks": False}}


def test_provider_extra_body_reaches_the_request_body(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-not-a-key")
    cfg = yaml.safe_load((ROOT / "models.example.yaml").read_text(encoding="utf-8"))
    cfg = {"providers": {"openrouter-q5plus": cfg["providers"]["openrouter-q5plus"]}}
    prov = providers.get_provider(cfg, "openrouter-q5plus")
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        sse = ('data: {"model":"m","choices":[{"delta":{"content":"hi"}}]}\n\n'
               'data: {"usage":{"prompt_tokens":1,"completion_tokens":1}}\n\ndata: [DONE]\n\n')
        return httpx.Response(200, content=sse.encode(),
                              headers={"content-type": "text/event-stream"})
    real = httpx.Client
    monkeypatch.setattr(api.httpx, "Client",
                        lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    prov.chat("org/model", [{"role": "user", "content": "x"}], max_tokens=8)
    assert seen["body"]["provider"] == {"quantizations": Q5PLUS, "allow_fallbacks": False}


# ------------------------------------- hosted model, rows complete, unjudged

def test_include_api_plans_judge_only_for_a_complete_unjudged_hosted_model():
    cfg = _cfg([{"name": "x", "model_id": "z/w", "provider": "ds"}])
    rows = {"x": [{"case_id": i, "grade": "pass"} for i in CASES]}
    p = bench_all.plan(cfg, set(), board_labels=["x"], include_api=True, only=["x"],
                       rows_for=lambda l: rows.get(l, []), pending_for=lambda l: 2,
                       attempts_for=lambda l: {}, judge_id=JUDGE, case_ids=CASES,
                       ladder_for=lambda l: None)
    assert p["groups"] == [] and p["judge"] == ["x"] and p["candidates"] == ["x"]
    obj = bench_all.plan_json(p, cfg, until=None, start=datetime(2026, 11, 6, 1, 0),
                              estimate_for=lambda g: (0.0, 0.0),
                              pending_for=lambda labels: 0.0, pending_label=lambda l: 1.0)
    assert obj["models"]["x"]["verdict"] == "judge_only"
    assert obj["models"]["x"]["missing_cases"] == 0
