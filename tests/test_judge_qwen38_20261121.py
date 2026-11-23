"""The 4.5 judge is the plain Qwen 3.8 27B, 8-bit (owner 2026-11-21, DECISIONS amendment r; INTENT-45 "The judge").

It is also the reference model on the board, so in a 4.5 profile a judge under test grades its own rows (Chat and
the adult page) and the board says so in plain words; older profiles keep the refusal. No hosted grader anywhere.
Fake judges and synthetic rows only: no model call, no rig."""
from __future__ import annotations

import json
from argparse import Namespace
from pathlib import Path

import pytest
import yaml

from crucibleforge import cli, config, judge, profiles, report

pytestmark = [pytest.mark.shipping_default, pytest.mark.live_ladder]

ROOT = Path(__file__).resolve().parents[1]
QWEN = "unsloth/Qwen3.8-27B-GGUF/Qwen3.8-27B-Q8_0"
GEMMA = "llmfan46/gemma-4-31B-it-uncensored-heretic-GGUF/gemma-4-31B-it-uncensored-heretic-Q8_0"


# ------------------------------------------------------------------ the judge id
def test_the_45_profile_judge_is_the_plain_qwen():
    j = profiles.profile_judge(profiles.load_profile("bench45"))
    assert j["model_id"] == QWEN and j["provider"] == "studioforge"
    assert j["thinking"] is True and j["context_length"] == 16384
    from crucibleforge import judge45
    assert judge45.judge_id(profiles.load_profile("bench45")) == QWEN
    # the 4.1 archive keeps its judge
    assert profiles.profile_judge(profiles.load_profile("bench"))["model_id"] == GEMMA


def test_the_example_config_names_qwen_first_and_keeps_gemma_by_name():
    cands = yaml.safe_load((ROOT / "models.example.yaml").read_text(encoding="utf-8"))["judge"]["candidates"]
    assert cands[0]["model_id"] == QWEN and cands[0]["provider"] == "studioforge"
    assert cands[0].get("thinking") is True and cands[0].get("context_length") == 16384
    named = {c.get("name"): c for c in cands if c.get("name")}
    assert any(c["model_id"] == GEMMA for c in named.values())
    assert not any(c["model_id"] == GEMMA for c in cands if not c.get("name"))


def test_a_45_judge_command_keeps_no_hosted_candidate():
    cfg = config.load_config(config.EXAMPLE_CONFIG_PATH)
    args = Namespace(profile="bench45", judge=None, smoke=False)
    cfg2, _cases, prof = cli._apply_profile_arg(args, cfg)
    cfg3 = cli._guard_45_judge(args, cfg2, prof)
    provs = {k: (v or {}).get("type") for k, v in (cfg3.get("providers") or {}).items()}
    cands = (cfg3.get("judge") or {}).get("candidates") or []
    assert cands and all(provs.get(c["provider"], c["provider"]) in ("studioforge", "lmstudio") for c in cands)
    assert args.judge["model_id"] == QWEN


# ------------------------------------------------------------------ Chat: a judge under test grades itself
def _locked_run(monkeypatch, tmp_path, prof_name):
    seen = {}
    monkeypatch.setattr(config, "RESULTS_DIR", tmp_path)

    class Guard:
        provs = {}

        def __init__(self, *a, **k):
            pass

        def restore(self):
            pass

    def fake_run_judge(cfg, labels, **kw):
        seen.update(kw)
        return {"judged": 0, "failed": 0}
    monkeypatch.setattr(judge, "run_judge", fake_run_judge)
    monkeypatch.setattr(judge, "acquire_judge_lease", lambda *a, **k: None)
    monkeypatch.setattr(cli, "_ProviderGuard", Guard)
    monkeypatch.setattr(cli, "_adult_judge_phase", lambda *a, **k: {})
    args = Namespace(force_evict=False, judge=None, instance=None, force=False, cmd="judge", judge_fallback=None,
                     allow_self_judge=False, judge_stop=None, judge_deadline=None,
                     _judge_prof=profiles.load_profile(prof_name))
    cfg = {"models": [{"name": "ref", "model_id": QWEN, "provider": "fake"}], "judge": {"samples": 1},
           "providers": {}, "defaults": {}}
    cli._cmd_judge_locked(args, cfg, ["ref"], None, False)
    return seen


def test_a_45_judge_run_lets_the_judge_grade_its_own_chat_rows(monkeypatch, tmp_path):
    assert _locked_run(monkeypatch, tmp_path, "bench45")["allow_self_judge"] is True


def test_an_older_profile_keeps_the_refusal(monkeypatch, tmp_path):
    assert _locked_run(monkeypatch, tmp_path, "bench")["allow_self_judge"] is False


def test_a_self_judged_chat_row_carries_the_plain_note():
    s = {"coverage": {"self_judged": ["RP-1", "RP-2"]}}
    notes = report._model_notes("ref", s, {})
    assert any("graded by itself" in n and "2" in n for n in notes), notes


# ------------------------------------------------------------------ the adult page
def _adult(monkeypatch, tmp_path, prof_name):
    from crucibleforge import judge45
    monkeypatch.setattr(config, "RESULTS_DIR", tmp_path)
    prof = profiles.load_profile(prof_name)
    monkeypatch.setattr(cli, "_adult_plan_of", lambda *a, **k: {"ref": {"calls_needed": 3}})

    class Prov:
        type, name = "studioforge", "studioforge"

    class JC:
        model_id = QWEN
        concurrency, provider = 1, Prov()

    def fake_load(cfg, benched, rows, samples, override=None, **k):
        judge.select_judge(cfg, benched, override=override)         # the real self-judge refusal
        return JC()
    monkeypatch.setattr(judge, "_load_judge", fake_load)
    got = []
    monkeypatch.setattr(judge45, "judge_model", lambda res, label, p, reader, chat, width=1: got.append(label) or {
        "classed": 3, "flagged": 0, "pending_class": 0, "pending_flags": 0, "calls": 3})
    cfg = {"models": [{"name": "ref", "model_id": QWEN, "provider": "studioforge"}],
           "providers": {"studioforge": {"type": "studioforge"}},
           "judge": {"candidates": [{"provider": "studioforge", "model_id": QWEN}]}}
    args = Namespace(judge={"provider": "studioforge", "model_id": QWEN})
    return cli._adult_judge_phase(args, cfg, prof, ["ref"]), got


def test_the_adult_phase_lets_a_45_judge_grade_its_own_page(monkeypatch, tmp_path):
    out, got = _adult(monkeypatch, tmp_path, "bench45")
    assert got == ["ref"] and out["ref"]["classed"] == 3


def test_the_adult_phase_of_an_older_profile_still_refuses(monkeypatch, tmp_path):
    with pytest.raises(judge.JudgeError, match="under test"):
        _adult(monkeypatch, tmp_path, "bench")


def test_a_self_graded_adult_page_carries_the_plain_note(tmp_path):
    import test_judge45_part1_20261121 as P
    from crucibleforge import judge45 as J
    from crucibleforge import nsfwplus45 as N
    prof = profiles.load_profile("bench45")
    rows = P.std_rows()
    (tmp_path / f"transcripts_{P.LABEL}.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows),
                                                           encoding="utf-8")
    J.judge_model(tmp_path, P.LABEL, prof, J.judge_reader(QWEN), P.FakeJudge(cls="SOFTENED"))
    blk = N.report_block(rows, P.LABEL, tmp_path, prof, model_id=QWEN)
    assert blk["judge45"]["model"] == QWEN and blk["self_graded"] is True
    assert N.report_block(rows, P.LABEL, tmp_path, prof, model_id="someone/other-model")["self_graded"] is False
    notes = report._model_notes(P.LABEL, {"nsfw_plus": blk}, {})
    assert any("graded by itself" in n and "adult page" in n for n in notes), notes


# ------------------------------------------------------------------ the ladder judge (Chat packs)
def _ladder(monkeypatch, tmp_path, prof_name, jid):
    from crucibleforge import rpladder as RL
    from crucibleforge import rpladder_run as LR
    monkeypatch.setattr(config, "RESULTS_DIR", tmp_path)
    monkeypatch.setattr(RL, "load", lambda: {"card": {}})
    monkeypatch.setattr(RL, "flat", lambda cards: ["x"])
    monkeypatch.setattr(RL, "load_check", lambda *a, **k: {"checked": ["x"], "cells": {}})
    monkeypatch.setattr(LR, "current_session_rows", lambda label, cfg: {"u1": {"case_id": "C1", "repeat": 1}})
    monkeypatch.setattr(LR, "_units", lambda rows, cards: ([{"unit": "u1", "replies": []}], None))
    monkeypatch.setattr(RL, "place_units", lambda units, cards, judge, cache, **k: (
        judge([{"key": "k"}]) and {"u1": {"complete": True, "position": 3}}))

    class JC:
        model_id = jid
        deadline = None

    def fake_load(cfg, benched, rows, samples, override=None, **k):
        judge.select_judge(cfg, benched, override=override)         # the real self-judge refusal
        return JC()
    monkeypatch.setattr(judge, "_load_judge", fake_load)
    monkeypatch.setattr(LR, "make_judge_fn", lambda jc, stats: (lambda reqs: [{} for _ in reqs]))
    cfg = {"models": [{"name": "ref", "model_id": jid, "provider": "studioforge"}],
           "providers": {"studioforge": {"type": "studioforge"}},
           "judge": {"candidates": [{"provider": "studioforge", "model_id": jid}]}}
    return LR.run(cfg, profiles.load_profile(prof_name), ["ref"], windows=[1, 3],
                  judge_override={"provider": "studioforge", "model_id": jid})     # the profile's judge, forced


def test_the_ladder_judge_of_45_places_its_own_sessions_with_the_note(monkeypatch, tmp_path):
    out = _ladder(monkeypatch, tmp_path, "bench45", QWEN)
    assert out["labels"]["ref"]["placed"] == 1
    rows = [json.loads(x) for x in (tmp_path / "transcripts_ref.jsonl").read_text(encoding="utf-8").splitlines()]
    assert rows[-1]["judge_ladder"]["self_graded"] is True
    cov = report._coverage(rows, {"model_id": QWEN}, None)
    assert cov["self_judged"] == ["C1"]
    notes = report._model_notes("ref", {"coverage": cov}, {})
    assert any("graded by itself" in n for n in notes), notes


def test_the_ladder_judge_of_41_still_refuses_itself(monkeypatch, tmp_path):
    with pytest.raises(judge.JudgeError, match="under test"):
        _ladder(monkeypatch, tmp_path, "bench", GEMMA)
