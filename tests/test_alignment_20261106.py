"""MakoBench alignment round (2026-11-06, ALIGNMENT-REVIEW.md B1/C1, D2).

* a model with a conformance FAIL record is PROVISIONAL: its scores stay on
  the board, it is never ranked, and the public row says why;
* rows carry the public facts the site needs (quant, scope, family);
* bench-all never registers a rig model on its own (the operator registers
  with --only, or --register-new), never registers an out-of-scope kind, and
  never benches a ``bench: false`` row.
"""
from __future__ import annotations

import json

import pytest

from crucibleforge import bench_all, config, report
from crucibleforge import headline as _h

CASES = ["C1", "C2", "C3"]
JUDGE = "judge/org/judge-q8"


@pytest.fixture()
def results(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "RESULTS_DIR", tmp_path)
    yield tmp_path


def _record(root, label, status="FAIL", check="c3"):
    d = root / "conformance"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{label}.json").write_text(json.dumps({
        "ts": "2026-11-03T09:00:00+00:00",
        "checks": [{"id": "c1", "status": "PASS", "detail": ""},
                   {"id": check, "status": status, "detail": "replay setting wrong"}],
        "verdict": status}), encoding="utf-8")


# ------------------------------------------------------------- provisional

def test_a_conformance_fail_makes_the_row_provisional(results):
    _record(results, "m")
    cov = report._coverage([], {"model_label": "m"}, None, label="m")
    assert cov["provisional"] is True
    assert cov["provisional_reason"] == "conformance FAIL: c3"
    assert "conformance FAIL: c3" in cov["status"]          # the note is kept


def test_a_warn_or_no_record_is_not_provisional(results):
    _record(results, "w", status="WARN", check="c4")
    for label in ("w", "none"):
        cov = report._coverage([], {"model_label": label}, None, label=label)
        assert cov["provisional"] is False and cov["provisional_reason"] is None


def _stats(label, total, provisional=False, old=False):
    return {"scorecard": {"total": total, "chat": total / 2, "coding": total / 2,
                          "score_defs": []},
            "coverage": {"provisional": provisional,
                         "provisional_reason": "conformance FAIL: c3" if provisional else None},
            "era": {"old": old}, "meta": {}}


def test_rank_order_puts_provisional_rows_after_the_ranked_rows():
    stats = {"a": _stats("a", 10), "p": _stats("p", 20, provisional=True),
             "b": _stats("b", 5), "o": _stats("o", 30, old=True)}
    assert report.rank_labels(list(stats), stats) == ["a", "b", "p", "o"]


def test_provisional_rows_get_no_rank_and_the_ranks_close_up(monkeypatch):
    stats = {"a": _stats("a", 10), "p": _stats("p", 20, provisional=True),
             "b": _stats("b", 5)}
    seen = {}

    def fake_rank_groups(part, boots, s_raw, level):
        if part:
            seen["part"] = list(part)
        return {l: str(i) for i, l in enumerate(part, 1)}, []
    monkeypatch.setattr(report, "_tier_profile", lambda cfg: {"x": 1})
    monkeypatch.setattr(report, "model_boots", lambda *a, **k: {})
    monkeypatch.setattr(_h, "summarize", lambda card, boot, prof, level: {
        "P": {"overall": card["total"]}, "S": {"overall": card["total"],
                                                "chat": card["chat"], "code": card["coding"]},
        "sort": card["total"]})
    monkeypatch.setattr(_h, "display", lambda p, prof: {"overall": p.get("overall")})
    monkeypatch.setattr(_h, "rank_groups", fake_rank_groups)
    medians = {}
    monkeypatch.setattr(_h, "board_medians", lambda pairs: medians.setdefault("n", len(pairs)))
    monkeypatch.setattr(_h, "capability_class", lambda s, prof: "A")
    monkeypatch.setattr(_h, "lean", lambda chat, code, prof, med: "even")
    report._headline(list(stats), stats, {l: ([], {}, {}) for l in stats}, None)
    assert seen["part"] == ["a", "b"]
    assert stats["a"]["headline"]["rank"] == "1"
    assert stats["b"]["headline"]["rank"] == "2"
    assert stats["p"]["headline"]["rank"] == "-"
    assert medians["n"] == 2                                  # p left out of the medians
    assert stats["p"]["headline"]["class"] == "A"             # its numbers stay


# ------------------------------------------------------------ row facts

@pytest.mark.parametrize("mid,quant,std", [
    ("ReadyArt/Dark-Scarlett-27B-v3.0-GGUF/Dark-Scarlett-27B-v3.0-Q8_0_hb16", "Q8_0", True),
    ("llmfan46/X-GGUF/Qwen3.8-27B-Ultra-Uncensored-Heretic-NVFP4-Q8_0", "NVFP4", False),
    ("mradermacher/X-i1-GGUF/Model.i1-Q4_K_S", "Q4_K_S", False),
    ("org/X-GGUF/Model-IQ4_XS", "IQ4_XS", False),
    ("24_10_Mistrial_Celeste-12B-V1.6.Q8_0NSFW", "Q8_0", True),
    ("unsloth/gemma-4-26B-A4B-it-qat-GGUF/gemma-4-26B-A4B-it-qat-UD-Q4_K_XL", "Q4_K_XL", False),
    ("24_10_Pygmalion or Mistral_cydonia-22b-v1-q6_k", "Q6_K", False),
])
def test_quant_is_parsed_from_the_rig_id(mid, quant, std):
    cfg = {"providers": {"sf": {"type": "studioforge"}},
           "models": [{"name": "m", "model_id": mid, "provider": "sf"}]}
    f = report.row_facts("m", {}, cfg)
    assert f["quant"] == quant and f["quant_standard"] is std
    assert f["scope"] == {"modalities": "text->text", "text_suite_only": False}
    assert f["family"] is None


def test_hosted_rows_have_no_quant_and_registry_fields_win():
    cfg = {"providers": {"sf": {"type": "studioforge"}, "ds": {"type": "openai"}},
           "models": [{"name": "api", "model_id": "deepseek-flash", "provider": "ds"},
                      {"name": "vl", "model_id": "org/Qwen2.5-VL-7B-Instruct-Q6_K",
                       "provider": "sf", "modalities": "text+image->text",
                       "family": "Qwen/Qwen2.5-VL-7B-Instruct", "quant": "Q8_0"}]}
    api = report.row_facts("api", {}, cfg)
    assert api["quant"] is None and api["quant_standard"] is None
    vl = report.row_facts("vl", {}, cfg)
    assert vl["scope"] == {"modalities": "text+image->text", "text_suite_only": True}
    assert vl["family"] == "Qwen/Qwen2.5-VL-7B-Instruct" and vl["quant"] == "Q8_0"


# ------------------------------------------------------------- bench-all

def _cfg(models):
    return {"providers": {"sf": {"type": "studioforge"}}, "models": models}


def _m(name, mid, **kw):
    return {"name": name, "model_id": mid, "provider": "sf", **kw}


def _plan(cfg, rig, board=None, **kw):
    kw.setdefault("ladder_for", lambda l: None)
    return bench_all.plan(cfg, set(rig), board_labels=board or [],
                          rows_for=lambda l: [], pending_for=lambda l: 0,
                          attempts_for=lambda l: {}, judge_id=JUDGE, case_ids=CASES,
                          rejudge_for=lambda l: set(), rungs=[], **kw)


RIG_TOOLS = ["mradermacher/Hy-MT2-30B-A3B-i1-GGUF/Hy-MT2-30B-A3B.i1-Q4_K_S",
             "mradermacher/Qwen2.5-VL-7B-Abliterated-Caption-it-GGUF/X-Caption-it.Q4_K_S",
             "prithivMLmods/Qwen3.5-4B-GLB-3D-Coder-GGUF/Qwen3.5-4B-GLB-3D-Coder.Q8_0",
             "Qwen/Qwen3-VL-Embedding-8B", "org/Kokoro-82M-TTS", "org/Llama-Guard-4-12B",
             "org/Skywork-Reward-8B", "org/Wan2.1-video-14B"]


@pytest.mark.parametrize("mid", RIG_TOOLS)
def test_out_of_scope_kinds_are_recognised(mid):
    assert bench_all.out_of_scope(mid)


@pytest.mark.parametrize("mid", [
    "unsloth/Qwen3.8-27B-GGUF/Qwen3.8-27B-Q5_K_S",
    "HauhauCS/Gemma4-31B-QAT-Uncensored-HauhauCS-Balanced-MTP/Gemma4-Balanced-Q4_K_M",
    "org/Qwen2.5-VL-7B-Instruct-GGUF/Qwen2.5-VL-7B-Instruct-Q8_0",   # VL chat: operator's call
    "ggml-org/SmolVLM-256M-Instruct-GGUF/SmolVLM-256M-Instruct-Q8_0",
    "deepseek-flash", "24_10_Mistrial_Celeste-12B-V1.6.Q8_0NSFW"])
def test_chat_models_are_not_out_of_scope(mid):
    assert not bench_all.out_of_scope(mid)


def test_a_plain_run_registers_nothing():
    cfg = _cfg([])
    p = _plan(cfg, ["org/New-Chat-7B-Q8_0"] + RIG_TOOLS + [JUDGE])
    assert p["add"] == [] and p["candidates"] == []
    why = dict(p["skipped_rig"])
    assert "--only" in why["org/New-Chat-7B-Q8_0"]
    for mid in RIG_TOOLS:
        assert why[mid].startswith("out of scope")


def test_only_registers_the_named_chat_model_but_never_a_tool_model():
    cfg = _cfg([])
    p = _plan(cfg, ["org/New-Chat-7B-Q8_0", RIG_TOOLS[0]],
              only=["new-chat-7b-q8-0", RIG_TOOLS[0]])
    assert [a["model_id"] for a in p["add"]] == ["org/New-Chat-7B-Q8_0"]
    assert dict(p["skipped_rig"])[RIG_TOOLS[0]].startswith("out of scope")


def test_register_new_registers_every_in_scope_rig_model():
    p = _plan(_cfg([]), ["org/New-Chat-7B-Q8_0"] + RIG_TOOLS, register_new=True)
    assert [a["model_id"] for a in p["add"]] == ["org/New-Chat-7B-Q8_0"]


def test_bench_false_rows_and_registered_tool_models_are_never_benched_and_pruned():
    cfg = _cfg([_m("chat", "org/chat-Q8_0"), _m("off", "org/other-Q8_0", bench=False),
                _m("hy-mt2", RIG_TOOLS[0]), _m("kept", "org/kept-Q8_0", bench=False,
                                                keep_on_board=True)])
    p = _plan(cfg, ["org/chat-Q8_0", "org/other-Q8_0", RIG_TOOLS[0], "org/kept-Q8_0"],
              board=["chat", "off", "hy-mt2", "kept"])
    assert p["candidates"] == ["chat"]
    assert set(p["not_benched"]) == {"off", "hy-mt2", "kept"}
    assert dict(p["prune"]) == {"off": "out of scope (bench: false)",
                                "hy-mt2": "out of scope (bench: false)"}
    assert "never benched" in bench_all.format_plan(p)


# ------------------------------------------------- per-model reasoning replay

def test_a_registry_row_replay_override_reaches_the_ctx_and_the_fingerprint():
    from crucibleforge import conformance, runner
    from crucibleforge.providers import Provider
    prov = Provider(name="sf", type="studioforge", base_url="http://x")
    cfg = {"providers": {"sf": {"type": "studioforge", "base_url": "http://x"}}, "models": []}
    plain = runner._Ctx(cfg, {"name": "m", "model_id": "m", "provider": "sf"}, prov, 8192)
    on = runner._Ctx(cfg, {"name": "m", "model_id": "m", "provider": "sf",
                           "reasoning_replay": True}, prov, 8192)
    assert plain.reasoning_replay is False and on.reasoning_replay is True
    assert on.provider is prov and prov.reasoning_replay is False   # provider untouched
    fp = conformance.protocol_fingerprint(cfg, on.entry, prov, on)
    assert fp["reasoning_replay"] is True and "reasoning_split" not in fp["extra_body_keys"]


def test_rows_under_the_other_replay_setting_are_regenerated():
    cfg = _cfg([_m("q", "org/q-Q8_0", reasoning_replay=True), _m("p", "org/p-Q8_0")])
    old = [{"case_id": c, "grade": "pass", "protocol": {"reasoning_replay": False}} for c in CASES]
    p = bench_all.plan(cfg, {"org/q-Q8_0", "org/p-Q8_0"}, board_labels=["q", "p"],
                       rows_for=lambda l: old, pending_for=lambda l: 0,
                       attempts_for=lambda l: {}, judge_id=JUDGE, case_ids=CASES,
                       rejudge_for=lambda l: set(), rungs=[], ladder_for=lambda l: None)
    assert p["groups"] == [{"models": ["q"], "cases": CASES}]       # p's rows still count
    new = [dict(r, protocol={"reasoning_replay": True}) for r in old]
    assert bench_all.replay_rows(cfg["models"][0], new) == new
    assert bench_all.replay_rows(cfg["models"][0], [{"case_id": "C1"}]) == []


def test_a_vl_chat_model_is_registered_with_its_modalities(tmp_path):
    mid = "org/Qwen2.5-VL-7B-Instruct-GGUF/Qwen2.5-VL-7B-Instruct-Q8_0"
    p = _plan(_cfg([]), [mid], only=[mid])
    assert p["add"][0]["modalities"] == "text+image->text"
    reg = tmp_path / "models.yaml"
    reg.write_text("models:\n- name: a\n  model_id: org/a\n", encoding="utf-8")
    bench_all.append_registry(reg, p["add"], "sf")
    import yaml
    row = yaml.safe_load(reg.read_text(encoding="utf-8"))["models"][-1]
    assert row["modalities"] == "text+image->text"
    cfg = {"providers": {"sf": {"type": "studioforge"}}, "models": [dict(row, provider="sf")]}
    assert report.row_facts(row["name"], {}, cfg)["scope"]["text_suite_only"] is True
    # inferred for an older row without the key
    old = {"providers": cfg["providers"],
           "models": [{"name": "s", "provider": "sf",
                       "model_id": "ggml-org/SmolVLM-256M-Instruct-GGUF/SmolVLM-256M-Instruct-Q8_0"}]}
    assert report.row_facts("s", {}, old)["scope"]["modalities"] == "text+image->text"
