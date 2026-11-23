"""The Fixes segment runs in every standard run and counts only stamped, host-compared verdicts (W3b).

Offline: no model, no rig. Replies come from a fake provider; grading is the sandboxed fixcheck."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
import yaml

from crucibleforge import codeenv, config, fixcheck, fixrows45, graders, profiles, providers, runner, stopflag
from crucibleforge import segments45 as S
from crucibleforge.api import ChatResult
from crucibleforge.gen import patchhard

ROOT = Path(__file__).resolve().parent.parent
needs_bwrap = pytest.mark.skipif(not graders.sandbox_available(), reason="needs a working bwrap")
# step 2 (2026-11-21): one unit of each generated family now scored (patchmini L1-3, patchfix L1-4, patchmod L3)
UNITS = ("CB-F1-larder-bughunt", "PATCHFIX-core-4-1", "PATCHMINI-core-1-1", "PATCHMOD-core-3-1")


@pytest.fixture(scope="module")
def std():
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("CF_COMPOSITION", "standard")          # the suite pins `default` unless a test asks
        cfg = config.load_config(ROOT / "models.example.yaml")
        prof = profiles.load_profile("bench45", cfg)
        cfg2, cases = profiles.apply_profile(prof, cfg)
    return {"cfg": cfg2, "prof": prof, "cases": {c["id"]: c for c in cases}}


def _whole(files: dict) -> str:
    return "\n".join(f"{p}\n````python\n{t}````\n" for p, t in sorted(files.items()))


def _forged(case: dict) -> str:
    g, ref = case["grader_config"], case["reference_files"]
    path = sorted(ref)[0]
    trick = ("import sys\nprint('PASS 100%')\nopen('/out/obs.json', 'w').write('[]')\nsys.exit(0)\n")
    return _whole({path: g["files"][path] + "\n" + trick})


# ------------------------------------------------------------------ composition
def test_the_standard_composition_runs_the_four_fix_units(std):
    for u in UNITS:
        assert u in std["cases"], u
    assert std["cases"]["CB-F1-larder-bughunt"]["category"] == "codeagent"
    assert std["prof"]["case_repeats"]["CB-F1-larder-bughunt"] == 24
    for u in UNITS[1:]:
        c = std["cases"][u]
        assert c["category"] == "coding" and c["grader"] == "repo_tests" and c["budget"] == 65536
    # the declared segment names exactly these units
    fx = S.tree({**std["prof"]})["code"]["fixes"]
    assert set(UNITS) <= set(fx["cases"]) and fx["cases"][0] == UNITS[0]
    # the other repeat counts the composition merged are intact
    assert std["prof"]["case_repeats"]["TZ16-pickup-dst-delay"] == 10


def test_is_fix_case(std):
    assert all(fixrows45.is_fix_case(std["cases"][u]) for u in UNITS)
    assert not fixrows45.is_fix_case(std["cases"]["CB-C6-predict-node"])
    assert not fixrows45.is_fix_case({"id": "X", "generator": {"type": "patchfix"}, "grader_config": {}, "chain": {"id": "c"}})


# ------------------------------------------------------------------ the kill table
@needs_bwrap
@pytest.mark.parametrize("unit", UNITS[1:])
def test_kill_table_patch_units(std, unit):
    c = std["cases"][unit]
    g, ref = c["grader_config"], c["reference_files"]
    assert fixcheck.grade_answer(_whole(ref), g)["score"] == 1.0
    assert fixcheck.grade_answer(_whole({p: g["files"][p] for p in ref}), g)["score"] == 0.0
    assert fixcheck.grade_answer(_forged(c), g)["score"] == 0.0


@needs_bwrap
def test_kill_table_patchhard_l8_pack():
    m = patchhard.materialize({"type": "patchhard", "level": 8}, release_seed="4.5.0", case_id="PATCHHARD-8")
    g, ref = m["grader_config"], m["reference_files"]
    assert fixcheck.grade_answer(_whole(ref), g)["score"] == 1.0
    assert fixcheck.grade_answer(_whole({p: g["files"][p] for p in ref}), g)["score"] == 0.0


def _env(case, over):
    env = codeenv.RepoEnv(case["env"])
    env.tree.update(over)
    env.writes.extend(sorted(over))
    return env


@needs_bwrap
def test_kill_table_cbf1(std):
    c = std["cases"]["CB-F1-larder-bughunt"]
    ref = c["reference_files"]
    assert fixrows45.agent_verdict(_env(c, ref), any_cut=False)["score"] == 1.0
    assert fixrows45.agent_verdict(_env(c, {}), any_cut=False)["score"] == 0.0
    forged = {"larder/units.py": c["env"]["files"]["larder/units.py"] + "\nprint('PASS')\nimport sys; sys.exit(0)\n"}
    assert fixrows45.agent_verdict(_env(c, forged), any_cut=False)["score"] == 0.0


# ------------------------------------------------------------------ the stamped row (live path = regrade path)
def _row(case, reply, **kw):
    res = ChatResult(response_text=reply, finish_reason=kw.pop("finish", "stop"), completion_tokens=kw.pop("tokens", 100))
    row = {"case_id": case["id"], "category": case["category"], "response": reply}
    runner.grade_single_row(row, res, case, kw.pop("blank", False), kw.pop("sent", 65536))
    return row


@needs_bwrap
def test_a_reply_row_carries_the_stamped_verdict(std):
    c = std["cases"]["PATCHFIX-core-4-1"]
    good = _row(c, _whole(c["reference_files"]))
    assert good["fixcheck"]["score"] == 1.0 and good["fixcheck"]["stamp"] == fixrows45.checker_stamp()
    bad = _row(c, _forged(c))
    assert bad["fixcheck"]["score"] == 0.0 and not bad["fixcheck"]["cut"]


@needs_bwrap
def test_cut_at_the_full_budget_scores_zero_even_with_a_correct_prefix(std):
    c = std["cases"]["PATCHFIX-core-4-1"]
    cut = _row(c, _whole(c["reference_files"]), finish="length", tokens=65536 - 10)
    assert cut["fixcheck"]["score"] == 0.0 and cut["fixcheck"]["cut"] is True
    # a length stop far below the budget is a provider cap: graded as delivered, flagged
    short = _row(c, _whole(c["reference_files"]), finish="length", tokens=900)
    assert short["fixcheck"]["short_cut"] is True and short["fixcheck"]["cut"] is False


@needs_bwrap
def test_an_empty_or_blank_reply_scores_zero_and_a_missing_sandbox_stamps_nothing(std, monkeypatch):
    c = std["cases"]["PATCHFIX-core-4-1"]
    assert _row(c, "", blank=True)["fixcheck"]["score"] == 0.0
    assert _row(c, "   ")["fixcheck"]["empty"] is True

    def boom(*a, **k):
        raise graders.SandboxUnavailable("no bwrap")
    monkeypatch.setattr(fixcheck, "grade_answer", boom)
    assert _row(c, _whole(c["reference_files"]))["fixcheck"] == {"unavailable": True}     # never counts


def test_a_non_fix_case_row_is_untouched(std):
    c = std["cases"]["CB-C6-predict-node"]
    res = ChatResult(response_text="x", finish_reason="stop")
    row = {"case_id": c["id"], "category": c["category"]}
    runner.grade_single_row(row, res, c, False, 4096)
    assert "fixcheck" not in row


# ------------------------------------------------------------------ end to end through the runner and the segment
@pytest.fixture(autouse=True)
def _flags():
    stopflag.ABORT.clear()
    runner.STOP.clear()
    providers.clear_cache()
    yield
    providers.clear_cache()


def _fake_rig(monkeypatch, replies: dict):
    """A fake StudioForge provider: the reply for a prompt comes from ``replies[prompt]``."""
    P = providers.Provider
    cur = {"ctx": None}

    def switch(self, model_id, ctx=None):
        cur["ctx"] = ctx
        return 1.0

    def chat(self, model_id, messages, **kw):
        text = replies[messages[-1]["content"]]
        return ChatResult(response_text=text, finish_reason="stop", served_model="m", completion_tokens=50, total_s=0.1)
    monkeypatch.setattr(P, "alive", lambda self: True)
    monkeypatch.setattr(P, "is_available", lambda self, mid: True)
    monkeypatch.setattr(P, "switch_model", switch)
    monkeypatch.setattr(P, "chat", chat)
    monkeypatch.setattr(P, "trained_context", lambda self, mid: 131072)
    monkeypatch.setattr(P, "live_context", lambda self, mid: cur["ctx"])
    monkeypatch.setattr(P, "loaded_plan_for", lambda self, mid: {"ctx_size": cur["ctx"], "parallel": 2})
    monkeypatch.setattr(P, "workers", lambda self, mid: 3)
    monkeypatch.setattr(P, "is_loaded", lambda self, mid: True)
    monkeypatch.setattr(P, "model_capabilities", lambda self, mid: set())


RUNCFG = {"defaults": {"context_length": 32768, "context_floor": 40960, "bank_budget": 65536, "repeats": {"perf": 1}},
          "providers": {"p": {"type": "studioforge", "base_url": "http://p.invalid/v1"}},
          "judge": {"candidates": []}, "models": []}
ENTRY = {"name": "m", "model_id": "m", "provider": "p", "thinking": False}


def _agent_rows(c, score_files: dict | None, n: int):
    """CB-F1 rows as the agent loop stores them (stamped); None = the untouched buggy tree."""
    env = _env(c, score_files or {})
    return [{"case_id": c["id"], "repeat": i + 1, "profile": "bench45", "ts": f"2026-11-19T10:0{i}:00+00:00",
             "run_id": f"a{i}", "bench_run_id": "r1", "fixcheck": fixrows45.agent_verdict(env, any_cut=False)}
            for i in range(n)]


_RAW = yaml.safe_load((ROOT / "profiles" / "bench45.yaml").read_text(encoding="utf-8"))
_STD_PROF = {**_RAW, **_RAW["compositions"]["standard"]}


def _fixes_points(rows):
    model = {"fix45": fixrows45.case_scores(rows, list(UNITS))}
    # the declared segment cut down to the four units (one per slice member family; slices and points as declared)
    seg = {**S.tree(_STD_PROF)["code"]["fixes"], "cases": list(UNITS), "case_repeats": {UNITS[0]: 2}}
    return S._segment(model, "fixes", seg, {})


@needs_bwrap
def test_end_to_end_reference_forged_old_grader_and_mixed(std, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "RESULTS_DIR", tmp_path)
    cases = [std["cases"][u] for u in UNITS[1:]]
    ref = {c["prompt"]: _whole(c["reference_files"]) for c in cases}
    forged = {c["prompt"]: _forged(c) for c in cases}
    f1 = std["cases"]["CB-F1-larder-bughunt"]

    _fake_rig(monkeypatch, ref)
    runner.run_models(RUNCFG, [ENTRY], cases)
    rows = config.load_transcripts("m")
    assert len(rows) == 3 and all(r["fixcheck"]["score"] == 1.0 for r in rows)
    # the budget is the case's 65,536, clamped to the served window; rows record it, the finish reason and the metrics
    assert all(60000 < r["max_tokens_sent"] <= 65536 and r["finish_reason"] == "stop" and "metrics" in r for r in rows)
    seg = _fixes_points(rows + _agent_rows(f1, f1["reference_files"], 2))
    assert seg["points"] == pytest.approx(3.0) and seg["complete"]

    # forged replies and a forged agent session: nothing earned
    for r in rows:
        r["fixcheck"] = _row(std["cases"][r["case_id"]], forged[std["cases"][r["case_id"]]["prompt"]])["fixcheck"]
    seg = _fixes_points(rows + _agent_rows(f1, None, 2))
    assert seg["points"] == 0.0 and seg["complete"]

    # a row graded only by the old code-bank grader never counts (it is reported, not scored)
    old = [{k: v for k, v in r.items() if k != "fixcheck"} for r in rows]
    seg = _fixes_points(old)
    assert seg["points"] is None and seg["pending_points"] == 3
    assert fixrows45.case_scores(old, list(UNITS))["cases"]["PATCHFIX-core-4-1"]["old_grader"] == 1

    # mixed: both CB-F1 right (0.6 each of the bug-hunt's 1.2), PATCHFIX right (half the 1.2 patch slice), PATCHMINI
    # and PATCHMOD wrong
    good = {r["case_id"]: _row(std["cases"][r["case_id"]], _whole(std["cases"][r["case_id"]]["reference_files"]))
            for r in rows}
    mixed = [{**r, "fixcheck": good[r["case_id"]]["fixcheck"] if r["case_id"] == "PATCHFIX-core-4-1" else r["fixcheck"]}
             for r in rows]
    seg = _fixes_points(mixed + _agent_rows(f1, f1["reference_files"], 2))
    assert seg["points"] == pytest.approx(0.6 * 3)


@needs_bwrap
def test_appending_fix_rows_to_an_existing_folder_moves_no_stored_row(std, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "RESULTS_DIR", tmp_path)
    old = [{"case_id": "P1-tiny", "category": "perf", "repeat": 1, "ts": "2026-11-18T10:00:00+00:00", "run_id": "o1",
            "bench_run_id": "b0", "profile": "bench45", "grade": "pass", "response": "ok", "grader_stamp": "abc123",
            "elements": {"results": [1]}, "judge": {"scores": {"x": 3}}}]
    path = tmp_path / "transcripts_m.jsonl"
    path.write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in old), encoding="utf-8")
    before = path.read_bytes()
    cases = [std["cases"]["PATCHFIX-core-4-1"]]
    _fake_rig(monkeypatch, {cases[0]["prompt"]: _whole(cases[0]["reference_files"])})
    runner.run_models(RUNCFG, [ENTRY], cases)
    after = path.read_bytes()
    assert after.startswith(before)                                   # byte-for-byte: only appended
    assert hashlib.sha256(after[:len(before)]).hexdigest() == hashlib.sha256(before).hexdigest()
    assert after[len(before):].count(b"\n") == 1


def test_the_stamp_fixture_is_untouched():
    from crucibleforge import gen
    from crucibleforge import grader_stamp as G
    gen.discover()
    G.clear()
    want = json.loads((ROOT / "tests" / "fixtures" / "grader_stamps_8f946f9.json").read_text(encoding="utf-8"))
    assert {fam: G.stamp(fam) for fam in want} == want
