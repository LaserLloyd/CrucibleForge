"""`crucibleforge bench-all` (2026-09-29): the plan is computed from the rig
listing + registry + board rows, and does only what is missing."""
from __future__ import annotations

import argparse
import json

import pytest
import yaml

from crucibleforge import bench_all, cli
from crucibleforge import config as cfgmod

CASES = ["C1", "C2", "C3"]
JUDGE = "judge/org/judge-q8"


def _cfg(models):
    return {"providers": {"sf": {"type": "studioforge"}, "ds": {"type": "openai"},
                          "lm": {"type": "lmstudio"}},
            "models": models}


def _m(name, mid, prov="sf", **kw):
    return {"name": name, "model_id": mid, "provider": prov, **kw}


def _plan(cfg, rig, rows=None, pending=None, board=None, **kw):
    rows = rows or {}
    pending = pending or {}
    return bench_all.plan(cfg, set(rig), board_labels=board or [],
                          rows_for=lambda l: rows.get(l, []),
                          pending_for=lambda l: pending.get(l, 0),
                          attempts_for=lambda l: {},
                          judge_id=JUDGE, case_ids=CASES, **kw)


def _done(*ids, **extra):
    return [{"case_id": i, "grade": "pass", **extra} for i in ids]


def test_everything_current_is_nothing_to_do():
    cfg = _cfg([_m("a", "org/a"), _m("api", "x", prov="ds")])
    p = _plan(cfg, ["org/a", JUDGE], rows={"a": _done(*CASES)}, board=["a", "api"])
    assert bench_all.nothing_to_do(p), p
    assert "nothing to do" in bench_all.format_plan(p)


def test_only_missing_cases_are_generated_and_grouped():
    cfg = _cfg([_m("a", "org/a"), _m("b", "org/b"), _m("c", "org/c")])
    rows = {"a": _done("C1"), "b": _done("C1"), "c": _done(*CASES)}
    p = _plan(cfg, ["org/a", "org/b", "org/c"], rows=rows)
    assert p["groups"] == [{"models": ["a", "b"], "cases": ["C2", "C3"]}]
    assert p["judge"] == ["a", "b"]


def test_retryable_errors_are_missing_but_permanent_ones_are_not():
    rows = [{"case_id": "C1", "grade": "error", "error_kind": "transport"},
            {"case_id": "C2", "grade": "error", "error_kind": "template"},
            {"case_id": "C3", "grade": "skipped"}]
    assert bench_all.missing_cases(rows, CASES) == ["C1"]


def test_unregistered_rig_models_are_added_except_embeddings_and_the_judge():
    cfg = _cfg([_m("a", "org/a")])
    p = _plan(cfg, ["org/a", "org/New-Model-7B-Q8_0", "org/Qwen3-Embedding-8B", JUDGE],
              rows={"a": _done(*CASES)})
    assert [a["model_id"] for a in p["add"]] == ["org/New-Model-7B-Q8_0"]
    assert p["add"][0]["name"] == "new-model-7b-q8-0"
    assert {m for m, _ in p["skipped_rig"]} == {"org/Qwen3-Embedding-8B", JUDGE}
    # a newly added model needs the whole benchmark
    assert p["groups"] == [{"models": ["new-model-7b-q8-0"], "cases": CASES}]


def test_prune_gone_disabled_and_unregistered_but_keep_api_rows():
    cfg = _cfg([_m("gone", "org/gone"), _m("off", "org/off", enabled=False),
                _m("api", "x", prov="ds"), _m("here", "org/here")])
    p = _plan(cfg, ["org/here", "org/off"], rows={"here": _done(*CASES)},
              board=["gone", "off", "api", "here", "ghost"])
    assert dict(p["prune"]) == {"gone": "no longer on the rig",
                                "off": "disabled in models.yaml",
                                "ghost": "no registry entry"}
    assert p["not_on_rig"] == ["gone"]


def test_api_models_only_with_include_api():
    cfg = _cfg([_m("api", "x", prov="ds"), _m("lm", "y", prov="lm")])
    assert _plan(cfg, [])["candidates"] == []
    assert _plan(cfg, [], include_api=True)["candidates"] == ["api"]   # never lmstudio


def test_pending_judge_rows_alone_trigger_a_judge_phase():
    cfg = _cfg([_m("a", "org/a")])
    p = _plan(cfg, ["org/a"], rows={"a": _done(*CASES)}, pending={"a": 2})
    assert p["groups"] == [] and p["judge"] == ["a"]


def test_append_registry_lands_inside_models_and_keeps_comments(tmp_path):
    f = tmp_path / "models.yaml"
    f.write_text("# head comment\ndefaults: {}\nmodels:\n- name: a\n  model_id: org/a\n"
                 "  provider: sf\n# providers comment\nproviders:\n  sf: {type: studioforge}\n")
    bench_all.append_registry(f, [{"name": "b", "model_id": "org/b"}], "sf")
    text = f.read_text()
    d = yaml.safe_load(text)
    assert [m["name"] for m in d["models"]] == ["a", "b"]
    assert d["providers"] == {"sf": {"type": "studioforge"}}
    assert "# head comment" in text and "# providers comment" in text
    assert text.index("name: b") < text.index("# providers comment")


def test_append_registry_refuses_rather_than_corrupting(tmp_path):
    f = tmp_path / "models.yaml"
    f.write_text("defaults: {}\n")
    with pytest.raises(ValueError):
        bench_all.append_registry(f, [{"name": "b", "model_id": "org/b"}], "sf")
    assert f.read_text() == "defaults: {}\n"


def test_prune_moves_files_to_a_dated_archive(tmp_path):
    (tmp_path / "transcripts_x.jsonl").write_text("{}\n")
    (tmp_path / "meta_x.json").write_text("{}")
    dest = bench_all.prune_results(tmp_path, ["x"])
    assert dest.name.endswith("-pruned")
    assert (dest / "transcripts_x.jsonl").exists() and not (tmp_path / "meta_x.json").exists()


def test_plan_only_without_go_touches_nothing(monkeypatch, tmp_path):
    """No --go: print the plan, run nothing, write nothing."""
    monkeypatch.setattr(cfgmod, "RESULTS_DIR", tmp_path)
    cfg = _cfg([_m("a", "org/a")])
    cfg["_path"] = str(tmp_path / "models.yaml")

    class _P:
        def list_models(self, refresh=False):
            return {"org/a", "org/new-7b"}
    monkeypatch.setattr("crucibleforge.providers.get_provider", lambda cfg, name: _P())
    monkeypatch.setattr(bench_all, "plan", lambda cfg, rig, **kw: {
        "rig_models": 2, "add": [{"name": "new-7b", "model_id": "org/new-7b"}],
        "skipped_rig": [], "candidates": ["a"], "not_on_rig": [], "prune": [],
        "groups": [{"models": ["a"], "cases": CASES}], "judge": ["a"], "case_count": 3})
    monkeypatch.setattr(cli, "cmd_run", lambda *a, **k: pytest.fail("ran without --go"))
    args = argparse.Namespace(go=False, include_api=False, no_sync=False, no_prune=False)
    assert bench_all.execute(args, cfg) == 0
    assert not (tmp_path / "models.yaml").exists()


def test_rig_down_is_its_own_exit_code(monkeypatch):
    class _P:
        def list_models(self, refresh=False):
            return set()
    monkeypatch.setattr("crucibleforge.providers.get_provider", lambda cfg, name: _P())
    args = argparse.Namespace(go=True, include_api=False, no_sync=False, no_prune=False)
    assert bench_all.execute(args, _cfg([])) == bench_all.EXIT_RIG_DOWN


def test_retry_failed_is_capped_per_row():
    from crucibleforge.judge import needs_verdict
    once = {"needs_judge": True, "judge": {"judge_failed": True}}
    twice = {"needs_judge": True, "judge": {"judge_failed": True, "attempts": 2}}
    assert needs_verdict(once, retry_failed=True)
    assert not needs_verdict(twice, retry_failed=True)
    assert needs_verdict(twice, force=True)


def test_a_case_that_keeps_erroring_is_not_retried_forever():
    rows = [{"case_id": "C1", "grade": "error", "error_kind": "transport"}]
    assert bench_all.missing_cases(rows, CASES[:1], {"C1": 1}) == ["C1"]
    assert bench_all.missing_cases(rows, CASES[:1], {"C1": 2}) == []


def test_a_prompt_longer_than_the_context_is_permanent():
    rows = [{"case_id": "C1", "grade": "error", "error_kind": "transport",
             "error": 'server error: {"message": "The prompt does not fit the context \'m\'"}'}]
    assert bench_all.missing_cases(rows, CASES[:1]) == []


def test_first_puts_the_named_models_at_the_front():
    cfg = _cfg([_m("a", "org/a"), _m("b", "org/b"), _m("q8", "org/q8")])
    rows = {"a": _done("C1"), "b": _done("C1", "C2")}
    p = _plan(cfg, ["org/a", "org/b", "org/q8"], rows=rows, first=["q8"])
    assert p["groups"][0] == {"models": ["q8"], "cases": CASES}
    assert [g["models"] for g in p["groups"][1:]] == [["a"], ["b"]]
    # a --first model sharing a group with others is split out ahead of them
    p = _plan(cfg, ["org/a", "org/b", "org/q8"], rows={"a": _done("C1"), "b": _done("C1"),
                                                         "q8": _done("C1")}, first=["b"])
    assert p["groups"] == [{"models": ["b"], "cases": ["C2", "C3"]},
                           {"models": ["a", "q8"], "cases": ["C2", "C3"]}]


def test_plan_only_never_waits_for_the_rig_lock(monkeypatch):
    """A plan must answer at once while a bench holds results/.rig.lock."""
    called = {}
    monkeypatch.setattr(cli, "acquire_rig_lock", lambda: called.setdefault("lock", True))
    monkeypatch.setattr(cli, "load_config", lambda *a, **k: {"models": [], "providers": {}})
    monkeypatch.setattr(cli, "cmd_bench_all", lambda args, cfg: 0)
    with pytest.raises(SystemExit) as e:
        cli.main(["bench-all"])
    assert e.value.code == 0 and "lock" not in called
