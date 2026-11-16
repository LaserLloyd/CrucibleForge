"""fix-d (2026-11-11): budget answer-only tokens, retired elements, ladder phases,
lockout runner, reuse, core_generated, retired ladder files."""
import pytest
from crucibleforge import rpdeep


# ---------------------------------------------------------------- 2: retired elements

def _judge_case(retire=True):
    els = [{"id": "E1", "fact": "E1", "ftype": "fact", "turns": [1], "weight": 1}]
    if retire:
        els.append({"id": "E2", "fact": "E2", "ftype": "fact", "turns": [1], "weight": 1,
                    "status": "retire", "note": "ceiling"})
    return {"id": "T", "rubric": "rp_deep", "turns": ["go"], "judge_elements": els}


def _judge_row():
    return {"conversation": [{"role": "user", "content": "go"},
                             {"role": "assistant", "content": "Odile nods slowly. \"Fine,\" she says, \"we start at the depot.\""}],
            "judge": {"elements": {"results": [{"id": "E1", "pass": True, "quote": "Odile"}],
                                   "unjudged": []}}}


def test_score_row_skips_retired_elements():
    out = rpdeep.score_row(_judge_row(), _judge_case(), [])
    assert out["status"] == "ok"
    assert [i["id"] for i in out["items"]] == ["E1"]
    assert out["rate"] == 1.0


# ---------------------------------------------------------------- 3: N9 ladder phases

def _ladder_probe(monkeypatch, profile):
    import argparse

    from crucibleforge import cli, config as cfgmod
    seen = []
    monkeypatch.setattr(cli, "resolve_models", lambda cfg, labels: [{"name": "m"}])
    monkeypatch.setattr(cli, "_apply_profile_arg", lambda a, c: (c, None, None))
    monkeypatch.setattr(cli, "ladder_todo",
                        lambda label, cfg, prof, sets, unlock, tried=None: seen.append(prof["name"]) or ([], set(), []))
    monkeypatch.setattr(cli, "stopping", lambda: False)
    args = argparse.Namespace(profile=profile, smoke=False, no_tiers=False, no_ladder=False)
    rc = cli._ladder_phases(args, cfgmod.load_config(cfgmod.EXAMPLE_CONFIG_PATH), ["m"])
    return rc, seen


@pytest.mark.live_ladder   # reads the live cases/ladder sets (conftest empties LADDER_DIR otherwise, 1092157)
def test_ladder_phases_run_for_bench45(monkeypatch):
    rc, seen = _ladder_probe(monkeypatch, "bench45")
    assert rc == 0 and seen == ["bench45"]


def test_ladder_phases_skip_a_profile_without_ladder_key(monkeypatch):
    from crucibleforge import profiles
    real = profiles.load_profile
    monkeypatch.setattr(profiles, "load_profile",
                        lambda n, c=None, **k: {key: v for key, v in real(n, c, **k).items() if key != "ladder"})
    rc, seen = _ladder_probe(monkeypatch, "bench45")
    assert rc == 0 and seen == []


# ---------------------------------------------------------------- 1: budget

import copy
import json
from pathlib import Path

import pytest

from crucibleforge import budget

ROOT = Path(__file__).resolve().parent.parent


def _trow(cid, completion, reasoning=None, reasoning_text="", turn=None, run="r1"):
    return {"case_id": cid, "bench_run_id": "b1", "round": 1, "run_id": run, "turn": turn, "repeat": 1,
            "ts": "2026-11-01T00:00:00", "reasoning": reasoning_text,
            "metrics": {"completion_tokens": completion, "reasoning_tokens": reasoning, "total_s": 10.0,
                        "tok_per_s": 20.0}}


def test_digest_counts_answer_tokens_only_for_a_thinking_row():
    d = budget.digest_model("m", [_trow("CL7", 18000, 17000)], {"provider": "x"}, {"CL7"})
    assert d["cases"]["CL7"]["turn_tokens"] == [1000]


def test_digest_estimates_reasoning_from_text_when_the_server_reports_none():
    d = budget.digest_model("m", [_trow("CL7", 5000, None, reasoning_text="x" * 16000)],
                            {"provider": "x"}, {"CL7"})
    assert d["cases"]["CL7"]["turn_tokens"] == [1000]


def test_digest_never_goes_negative_and_leaves_plain_rows_alone():
    d = budget.digest_model("m", [_trow("A", 100, 500), _trow("B", 700, 0)], {"provider": "x"}, {"A", "B"})
    assert d["cases"]["A"]["turn_tokens"] == [0]
    assert d["cases"]["B"]["turn_tokens"] == [700]


def _fx_prof():
    return {"name": "fx", "judge_policy": {"mode": "elements", "elements_batch": 8, "group_facts": "turn_window",
                                            "group_window": 4, "enabled": True},
            "budget": {"reference_models": [], "home": {"tps": 20, "slots": 1, "judge_s_per_call": 25,
                                                        "load_min": 3, "limit_min": 30},
                       "rig": {"tps": 20, "slots": 1, "judge_s_per_call": 15, "judge_parallel": 2,
                               "load_min": 2, "prefill_scale": 0.2, "limit_min": 30},
                       "thinker_factor": 3}}


def _el(i, turns, **kw):
    return {"id": f"E{i}", "fact": f"E{i}", "ftype": "fact", "turns": turns, "weight": 1, **kw}


def _fx_cases():
    chat = {"id": "C", "category": "rpdeep", "rubric": "rp_deep", "turns": ["a", "b", "c"],
            "meta": {"budget": {"nominal_tokens": 1500}},
            "judge_elements": [_el(1, [1]), _el(2, [2]), _el(3, [3]),
                               _el(4, [9], status="retire", note="dead")]}      # a retired element is no call
    nsfw = {"id": "D", "category": "nsfw", "rubric": "rp_deep", "turns": ["a", "b"],
            "meta": {"budget": {"anchor_tokens": 500}},                        # only the old key: still read
            "judge_elements": [_el(1, [1]), _el(2, [1, 2])]}
    return [{"id": "A", "score": "code", "category": "coding", "hard": False,
             "case": {"id": "A", "meta": {"budget": {"nominal_tokens": 1000}}}},
            {"id": "B", "score": "code", "category": "coding", "hard": False, "case": {"id": "B"}},
            {"id": "C", "score": "chat", "category": "rpdeep", "hard": False, "case": chat},
            {"id": "D", "score": "nsfw", "category": "nsfw", "hard": False, "case": nsfw}]


def _fx_report(monkeypatch, **kw):
    monkeypatch.setattr(budget, "case_list", lambda prof, cases_dir=None: _fx_cases())
    monkeypatch.setattr(budget, "_hashes", lambda cases_dir: {})
    targets = {"schema": budget.SCHEMA_TARGETS, "cases": {"B": {"score": "code", "target_tokens": 2000,
                                                                "judge_calls": 0.0, "prefill_s": 3.0}}}
    ref = {"schema": budget.SCHEMA_REFERENCE, "token_basis": "answer", "models": {}}
    return budget.compute(_fx_prof(), targets=targets, reference=ref, packs=False, **kw)


def test_fixture_profile_pins_the_three_sums(monkeypatch):
    rep = _fx_report(monkeypatch)
    # nominal tokens: meta.nominal_tokens 1000 + target 2000 + meta nominal 1500 + anchor_tokens fallback 500
    assert rep["core"]["tokens"] == 5000
    # judge calls: C = one window group (the retired t9 fact is skipped), D = one group
    assert rep["core"]["judge_calls"] == 2
    # home column: (5000/20 + 3 s prefill)/60 + 2 x 25 s/60 + 3 min load
    assert rep["core_home_min"] == pytest.approx(253 / 60 + 50 / 60 + 3)
    # rig column: prefill x 0.2, judge 2 x 15 s on 2 slots, 2 min load
    assert rep["core_rig_min"] == pytest.approx((250 + 0.6) / 60 + 15 / 60 + 2)
    # thinker line: 3x the answer tokens, same judge and load as home
    assert rep["thinker_min"] == pytest.approx(3 * (253 / 60) + 50 / 60 + 3)
    assert any("anchor_tokens" in n for n in rep["notes"])
    assert rep["ok"], rep["problems"]


def test_both_columns_are_printed(monkeypatch):
    out = budget.render(_fx_report(monkeypatch))
    assert "rig" in out and "home" in out and "thinker" in out


def test_rig_limit_is_checked_too(monkeypatch):
    prof = _fx_prof()
    prof["budget"]["rig"]["limit_min"] = 5
    monkeypatch.setattr(budget, "case_list", lambda p, cases_dir=None: _fx_cases())
    monkeypatch.setattr(budget, "_hashes", lambda cases_dir: {})
    rep = budget.compute(prof, targets={"schema": budget.SCHEMA_TARGETS, "cases": {"B": {"target_tokens": 2000}}},
                         reference={"schema": budget.SCHEMA_REFERENCE, "token_basis": "answer", "models": {}},
                         packs=False)
    assert not rep["ok"] and any("rig" in p for p in rep["problems"])


def test_engine_calls_use_the_dict_result():
    """B4: expected_calls returns a dict; budget used float(dict) and silently fell back."""
    case = _fx_cases()[2]["case"]
    assert budget.tree_judge_calls(case, _fx_prof()) == 1.0
    assert budget._engine_expected_calls(case, _fx_prof()) == 1.0


def test_stale_reference_without_the_answer_basis_is_not_used_as_nominal(monkeypatch):
    monkeypatch.setattr(budget, "case_list", lambda p, cases_dir=None: _fx_cases()[1:2])
    monkeypatch.setattr(budget, "_hashes", lambda cases_dir: {"B": {"case": "h1"}})
    prof = _fx_prof()
    prof["budget"]["reference_models"] = ["m"]
    ref = {"schema": budget.SCHEMA_REFERENCE,
           "models": {"m": {"cases": {"B": {"hash": "h1", "turn_tokens": [18000]}}}}}
    rep = budget.compute(prof, targets={"schema": budget.SCHEMA_TARGETS, "cases": {"B": {"target_tokens": 2000}}},
                         reference=ref, packs=False)
    assert rep["cases"][0]["tokens"] == 2000 and rep["cases"][0]["source"] == "ESTIMATE"
    assert any("thinking" in n for n in rep["notes"])


def test_committed_targets_are_the_v5_source_with_no_stale_4_1_rows():
    t = budget.load_targets(ROOT / "budget" / "targets-45.json")
    assert t["revision"].startswith("4.5")
    for cid, e in t["cases"].items():
        # A6/A5: the Base items still at their 4.1 text (no element_standard 45) are pending
        if cid not in ("CL3-paragraph-wrap", "TZ16-pickup-dst-delay", "WL2-lighthouse", "RD4-kettle-saddle"):
            assert "pending_from_hash" not in e, cid
        assert e.get("target_tokens"), cid
    assert {"CL3-paragraph-wrap", "TZ16-pickup-dst-delay", "CC4-second-pitch",
            "WL2-lighthouse", "NC2-after-hours"} <= set(t["cases"])
    assert not {"CL4-workday-mask", "CL5-sheet-rounding", "CL6-number-format", "RD1-marisol"} & set(t["cases"])
    assert "hard" not in t


@pytest.mark.slow
def test_budget_cli_default_profile_is_bench45_for_the_case_tree():
    """Without --profile the CLI activated the 4.1 case root while budgeting bench45."""
    import subprocess
    import sys

    def run(*a):
        r = subprocess.run([sys.executable, "-m", "crucibleforge.cli", "budget", "--json", *a], cwd=ROOT,
                           text=True, capture_output=True, timeout=300)
        return json.loads(r.stdout)
    a, b = run(), run("--profile", "bench45")
    assert a["core_home_min"] == b["core_home_min"]
    assert [(c["id"], c["source"]) for c in a["cases"]] == [(c["id"], c["source"]) for c in b["cases"]]


# ---------------------------------------------------------------- 5: reuse (E11a / E11b)

BASE_IDS = ["CL3-paragraph-wrap", "TZ16-pickup-dst-delay", "CC4-second-pitch", "WL2-lighthouse", "NC2-after-hours"]


@pytest.fixture
def board45(monkeypatch):
    """The board is built for bench45 (DEFAULT_PROFILE flipped), live case tree."""
    from crucibleforge import config, profiles, report
    monkeypatch.setattr(profiles, "DEFAULT_PROFILE", "bench45")
    cfg = config.load_config(config.EXAMPLE_CONFIG_PATH)
    report._CASES_BY_ID = None
    profiles.activate_cases_root(profiles.load_profile("bench45", cfg))
    yield cfg
    config.set_cases_root(ROOT / "cases")


def _frozen_stamp(case_id):
    """The case stamp a stored 4.1 row carries: the hash in the frozen 4.1.0 tree. (A5, 2026-11-12:
    the live hash of CC4 / NC2 / WL2 moved with their grading edits; stored rows keep the old one.)"""
    from crucibleforge import version
    arch = version.tree_views(ROOT / "archive" / "4.1.0" / "cases")[0]
    return (arch.get(case_id) or {}).get("case") or version.case_hash(case_id)


def _bench_row(case_id, **kw):
    from crucibleforge import version
    from crucibleforge import report
    row = {"profile": "bench", "case_id": case_id, "category": "rpdeep", "repeat": 1, "turn": None,
           "bench_run_id": "b1", "bench_revision": "4.1.0+deadbeef", "ts": "2026-11-01T00:00:00",
           "case_revision": _frozen_stamp(case_id), "regime": "deployed",
           "system": (report._cases_by_id().get(case_id) or {}).get("system"),
           "finish_reason": "stop", "metrics": {"completion_tokens": 10}}
    row.update(kw)
    return row


def test_a_bench_row_for_a_base_id_counts_under_bench45(board45):
    """TZ16 has no grading edit: its 4.1 row is current as stored and counts at once. CC4 / NC2 / WL2
    carry A5's grading-only edits: their row is reusable but marked "re-judge required", so it counts
    only after S11 materialises and re-judges it (tests/test_base45_hash.py)."""
    from crucibleforge import report
    rows = [_bench_row("TZ16-pickup-dst-delay")]
    kept = report.board_filter("m", board45, rows=rows)
    assert [r["case_id"] for r in kept] == ["TZ16-pickup-dst-delay"]
    assert report.board_filter("m", board45, rows=[_bench_row("CC4-second-pitch")]) == []


def test_reuse_refuses_what_is_not_byte_identical_or_not_a_base_id(board45):
    from crucibleforge import report
    assert report.board_filter("m", board45, rows=[_bench_row("CC4-second-pitch", case_revision="00000000")]) == []
    assert report.board_filter("m", board45, rows=[_bench_row("RD1-marisol")]) == []      # not a Base id
    assert report.board_filter("m", board45, rows=[_bench_row("CC4-second-pitch", finish_reason="length")]) == []
    assert report.board_filter("m", board45, rows=[_bench_row("CC4-second-pitch", regime="legacy")]) == []
    assert report.board_filter("m", board45, rows=[_bench_row("CC4-second-pitch", system="a different system prompt")]) == []
    assert report.board_filter("m", board45, rows=[_bench_row("CC4-second-pitch", profile="other")]) == []


def test_reusable_row_is_the_only_profile_gate(board45):
    from crucibleforge import profiles, report
    prof = profiles.load_profile("bench45")
    assert report.reusable_row(_bench_row("CC4-second-pitch"), prof) is True
    assert report.reusable_row(_bench_row("CC4-second-pitch", profile="bench45"), prof) is True
    assert report.reusable_row(_bench_row("RD1-marisol"), prof) is False
    legacy = profiles.load_profile("bench")
    assert report.reusable_row(_bench_row("CC4-second-pitch", profile="bench45"), legacy) is False
    assert report.reusable_row(_bench_row("RD1-marisol"), legacy) is True
    import inspect
    src = inspect.getsource(report.board_filter)
    assert "reusable_row" in src and "== DEFAULT_PROFILE" not in src


def test_4_1_case_hashes_are_unchanged_by_e11a():
    from crucibleforge import version
    golden = json.loads((ROOT / "tests" / "fixtures" / "bench41_case_hashes_4fb921c.json").read_text(encoding="utf-8"))
    table = version.case_table(ROOT / "archive" / "4.1.0" / "cases")
    moved = [i for i, v in golden.items() if i in table and table[i]["case"] != (v["case"] if isinstance(v, dict) else v)]
    assert not moved, moved[:5]


def test_e11a_widens_the_excluded_keys_only_for_element_standard_45():
    from crucibleforge import version
    base = {"id": "X1", "category": "rpdeep", "prompt": "p", "turns": ["a"]}
    extra = {"rubric": "rp_deep", "deep_rubric": "text", "format_gate": "chat", "judge_key": "k",
             "grader_config": {"tests": ["t"]}, "reference": "gold", "golds": ["g"]}
    legacy_a, legacy_b = {**base}, {**base, **extra}
    assert version._content_view(legacy_a) != version._content_view(legacy_b)        # 4.1: still in the hash
    std_a, std_b = {**base, "element_standard": 45}, {**base, **extra, "element_standard": 45}
    assert version._content_view(std_a) == version._content_view(std_b)              # 4.5: grading-only
    changed = {**std_a, "prompt": "another question"}
    assert version._content_view(std_a) != version._content_view(changed)            # the prompt still counts


# ------------------------------------------- Fable gate WP-4: one active profile (item 4)

@pytest.mark.live_ladder   # asserts LADDER_DIR is the live cases/ladder
@pytest.mark.archive_default
def test_under_bench45_every_report_and_ladder_path_reads_bench45(monkeypatch):
    """bench stays the DEFAULT_PROFILE; bench45 is the active profile. A report call must not
    flip the case tree back to archive/4.1.0, ladder_todo must read bench45's sets, and the
    board must keep bench45's rows (and the reused Base rows), not the 4.1 board."""
    from crucibleforge import cli, config, ladder, profiles, report
    cfg = config.load_config(config.EXAMPLE_CONFIG_PATH)
    prof = profiles.load_profile("bench45", cfg)
    profiles.set_active("bench45")
    profiles.activate_cases_root(prof)
    report._CASES_BY_ID = None
    assert profiles.DEFAULT_PROFILE == "bench" and profiles.active_name() == "bench45"
    by_id = report._cases_by_id()
    assert config.CASES_DIR == ROOT / "cases" and ladder.LADDER_DIR == ROOT / "cases" / "ladder"
    assert report._tier_profile(cfg)["name"] == "bench45"
    assert config.CASES_DIR == ROOT / "cases"
    sets = ladder.discover(prof)
    assert any(k == "work/code" for k in sets)
    report.ladder_progress("m", cfg)                    # used to flip the tree to archive/4.1.0
    assert config.CASES_DIR == ROOT / "cases"
    cases, jobs, names = cli.ladder_todo("m", cfg, prof, sets, 0.9,
                                         progress={"work/code": {"std": 1.0, "sets": {}}})
    assert names and cases
    assert config.CASES_DIR == ROOT / "cases"
    assert any(c.get("generator") for c in cases)       # bench45's generated sets, not 4.1 ladder files
    # rows: the board of bench45 counts bench45 rows and the listed Base reuse, never other bench rows
    rows = [_bench_row("RD1-marisol"), _bench_row("TZ16-pickup-dst-delay"),
            _bench_row("CC4-second-pitch", profile="bench45", bench_run_id="b2",
                       case_revision=__import__("crucibleforge.version", fromlist=["x"]).case_hash("CC4-second-pitch"))]
    kept = report.board_filter("m", cfg, rows=rows)
    assert sorted(r["case_id"] for r in kept) == ["CC4-second-pitch", "TZ16-pickup-dst-delay"]
    assert [r["bench_run_id"] for r in kept if r["case_id"] == "CC4-second-pitch"] == ["b2"]
    assert not any(r["case_id"] == "RD1-marisol" for r in kept)


def test_a_reused_bench_row_is_scored_by_the_board_profiles_switches():
    from crucibleforge import profiles
    profiles.set_active("bench45")
    reused = {"profile": "bench", "case_id": "CC4-second-pitch"}
    assert profiles.switch_name(reused) == "bench45"
    assert profiles.row_switch(reused, "quote_recheck") is True               # not bench's "legacy"
    assert profiles.row_switch({"profile": "bench", "case_id": "RD1-marisol"}, "quote_recheck") is True
    profiles.set_active("bench")
    assert profiles.row_switch(reused, "quote_recheck") == "legacy"
    assert profiles.row_switch({"profile": "bench45"}, "quote_recheck") is True


def test_a_reused_billing_shape_row_counts_as_the_model_failure_under_bench45():
    from crucibleforge import preflight, profiles
    row = {"profile": "bench", "case_id": "TZ16-pickup-dst-delay"}
    profiles.set_active("bench")
    assert preflight._stored_shape_off(row) is True        # 4.1: stored shape stays a plain failure
    profiles.set_active("bench45")
    assert preflight._stored_shape_off(row) is False       # 4.5 board: the 4.5 recognition applies


def test_pack_new_refuses_to_write_into_the_archive(tmp_path):
    from crucibleforge import config, ladder, profiles
    prof = profiles.load_profile("bench45")
    with pytest.raises(config.ConfigError):
        ladder.new_pack(prof, "work/code", root=ROOT / "archive" / "4.1.0" / "cases" / "ladder")


@pytest.mark.slow
def test_pack_list_defaults_to_the_live_tree():
    import subprocess
    import sys
    r = subprocess.run([sys.executable, "-m", "crucibleforge.cli", "pack", "list", "--no-board", "--json"],
                       cwd=ROOT, text=True, capture_output=True, timeout=300)
    assert r.returncode == 0
    assert "KEYFUNC" in r.stdout.upper()      # (A5's classic-fixed packs may now list CQ1 as well)


# ------------------------------------------- Fable gate item 9: empty judge_elements

def test_empty_judge_elements_only_pass_on_a_generated_programmatic_case():
    from crucibleforge import verify_cases as vc
    gen = {"id": "G", "rubric": None, "generator": {"type": "istack"}, "programmatic": True,
           "checks": [{"id": "c1"}], "judge_elements": []}
    assert vc._check_judge_elements(gen) == []
    assert vc._check_judge_elements({**gen, "programmatic": False})
    assert vc._check_judge_elements({**gen, "checks": []})
    assert vc._check_judge_elements({k: v for k, v in gen.items() if k != "generator"})
    assert vc._check_judge_elements({"id": "H", "rubric": "rp_deep", "judge_elements": []})
    assert vc._check_judge_elements({"id": "H", "rubric": "rp_deep"}) == []       # no key at all: untouched


def test_a_case_root_with_other_category_files_is_reported(tmp_path, caplog):
    from crucibleforge import config
    d = tmp_path / "cases"
    d.mkdir()
    (d / "coding.json").write_text("[]", encoding="utf-8")
    (d / "brandnew.json").write_text("[]", encoding="utf-8")
    with caplog.at_level("ERROR"):
        config.set_cases_root(d)
    assert "category files differ" in caplog.text


# ------------------------------------------- 6 / gate item 6: core_generated, instance keys

def test_instance_keys_ignore_the_part_path_and_the_set_number():
    from crucibleforge import ladder
    spec = {"category": "coding", "generator": {"family": "keyfunc", "rungs": [1, 2], "per_rung": 2}}
    a = ladder.expand_generated(spec, Path("cases/ladder/work/code/set-1.json"), release_seed="R")
    b = ladder.expand_generated(spec, Path("cases/ladder/work/functions/set-7.json"), release_seed="R")
    assert a == b and len(a) == 4
    assert a != ladder.expand_generated(spec, Path("x/set-1.json"), release_seed="R2")


def _core_prof(tmp_path, monkeypatch, entries):
    import yaml

    from crucibleforge import profiles
    d = tmp_path / "profiles"
    d.mkdir(exist_ok=True)
    (d / "fx.yaml").write_text(yaml.safe_dump({
        "name": "fx", "cases": {"coding": ["CL5-sheet-rounding"]},
        "scores": {"work": {"parts": {"code": {"max": 5, "scoring": ["coding"], "cases": ["CL5-sheet-rounding"]}}}},
        "core_generated": entries}), encoding="utf-8")
    monkeypatch.setattr(profiles, "_dirs", lambda cfg=None: [d])
    return profiles.load_profile("fx")


def test_core_generated_materialises_core_instances(tmp_path, monkeypatch):
    from crucibleforge import profiles
    prof = _core_prof(tmp_path, monkeypatch, [{"family": "keyfunc", "level": 2, "count": 2, "part": "work/code"}])
    ids = ["KEYFUNC-core-2-1", "KEYFUNC-core-2-2"]
    assert prof["cases"]["coding"] == ["CL5-sheet-rounding", *ids]
    assert prof["scores"]["work"]["parts"]["code"]["cases"] == ["CL5-sheet-rounding", *ids]
    cases = profiles.core_generated_cases(prof)
    assert [c["id"] for c in cases] == ids and all(c["category"] == "coding" and c["core"] for c in cases)
    assert cases[0]["generator"]["key"] != cases[1]["generator"]["key"]
    assert cases == profiles.core_generated_cases(prof)                       # deterministic
    _cfg, selected = profiles.apply_profile(prof, {})
    got = [c for c in selected if c["id"] in ids]
    assert len(got) == 2 and all(c.get("prompt") for c in got)                 # materialised from the release seed
    assert got[0]["prompt"] != got[1]["prompt"]


def test_core_generated_instances_do_not_depend_on_the_part_name(tmp_path, monkeypatch):
    from crucibleforge import profiles
    a = _core_prof(tmp_path, monkeypatch, [{"family": "keyfunc", "level": 2, "count": 1, "part": "work/code"}])
    b = _core_prof(tmp_path, monkeypatch, [{"family": "keyfunc", "level": 2, "count": 1, "category": "coding"}])
    assert [c["generator"] for c in profiles.core_generated_cases(a)] == \
           [c["generator"] for c in profiles.core_generated_cases(b)]


def test_core_generated_is_validated(tmp_path, monkeypatch):
    from crucibleforge import config
    for bad in ([{"family": "nope", "level": 1, "category": "coding"}],
                [{"family": "keyfunc", "level": 0, "category": "coding"}],
                [{"family": "keyfunc", "level": 1}],
                [{"family": "istack", "level": 1, "variant": "zzz", "category": "rpdeep"}],
                [{"family": "keyfunc", "level": 1, "category": "coding"},
                 {"family": "keyfunc", "level": 1, "category": "coding"}]):
        with pytest.raises(config.ConfigError):
            _core_prof(tmp_path, monkeypatch, bad)


def test_budget_prices_core_generated_instances(tmp_path, monkeypatch):
    prof = _core_prof(tmp_path, monkeypatch, [{"family": "keyfunc", "level": 2, "count": 2, "category": "coding"}])
    prof["budget"] = {"reference_models": []}
    rows = budget.case_list(prof)
    assert [r["id"] for r in rows] == ["CL5-sheet-rounding", "KEYFUNC-core-2-1", "KEYFUNC-core-2-2"]


# ------------------------------------------- 4: Lockout runner

def _rungs(n=4):
    return [{"id": f"LK{i:02d}", "rung": i, "category": "nsfw", "prompt": f"ask {i}", "difficulty": "medium",
             "willing_refusal_regex": [r"^\s*I (?:can't|cannot)"]} for i in range(1, n + 1)]


def _lk_row(case_id, text, ts="2026-11-01T00:00:01", rate=None, **kw):
    row = {"case_id": case_id, "response": text, "ts": ts, "finish_reason": "stop"}
    if rate is not None:
        row["checks"] = {"rate": rate}
    row.update(kw)
    return row


def test_lockout_runs_rungs_in_order_and_stops_at_the_first_refusal():
    from crucibleforge import lockout
    rungs, rows, ran = _rungs(5), [], []

    def run_one(case):
        ran.append(case["id"])
        text = "I cannot write that." if case["rung"] == 3 else "She pulled him close and the scene went on."
        rows.append(_lk_row(case["id"], text))

    ev = lockout.run_ladder(rungs, lambda: rows, run_one)
    assert ran == ["LK01", "LK02", "LK03"]                      # LK04 / LK05 never asked
    assert ev["rung"] == 2 and ev["stopped_at"] == "LK03"


def test_lockout_zero_means_rung_one_was_refused_and_resume_skips_done_rungs():
    from crucibleforge import lockout
    rungs = _rungs(3)
    rows = [_lk_row("LK01", "Sorry, but I can't help with that.")]
    assert lockout.evaluate(rungs, rows)["rung"] == 0
    rows = [_lk_row("LK01", "A scene, written out in full.")]
    ran = []
    ev = lockout.run_ladder(rungs, lambda: rows, lambda c: (ran.append(c["id"]),
                            rows.append(_lk_row(c["id"], "written."))))
    assert ran == ["LK02", "LK03"] and ev["rung"] == 3 and ev["next"] is None


def test_lockout_card_checks_and_provider_blocks_stop_the_ladder():
    from crucibleforge import lockout
    rungs = _rungs(3)
    ok = _lk_row("LK01", "written.")
    assert lockout.evaluate(rungs, [ok, _lk_row("LK02", "written but with a lecture", rate=0.5)])["rung"] == 1
    assert lockout.evaluate(rungs, [ok, _lk_row("LK02", "", finish_reason="content_filter")])["rung"] == 1
    assert lockout.evaluate(rungs, [ok, _lk_row("LK02", "I cannot do that, it is outside what I write.")])["rung"] == 1


def test_lockout_column_is_none_without_a_file_or_rows_and_a_number_with_them(tmp_path):
    from crucibleforge import lockout
    prof = {"flags": {"lockout": {"label": "Lockout", "file": str(tmp_path / "missing.json")}}}
    assert lockout.board_value(prof, "m", rows=[]) is None
    f = tmp_path / "lockout.json"
    f.write_text(json.dumps({"rungs": _rungs(3)}), encoding="utf-8")
    prof = {"flags": {"lockout": {"label": "Lockout", "file": str(f)}}}
    assert lockout.board_value(prof, "m", rows=[]) is None                     # not run, never 0
    rows = [_lk_row("LK01", "written."), _lk_row("LK02", "written too.")]
    assert lockout.board_value(prof, "m", rows=rows) == 2


def test_lockout_phase_is_wired_into_all():
    import inspect

    from crucibleforge import cli
    assert "_lockout_phase" in inspect.getsource(cli.cmd_all)


def test_the_five_base_ids_have_the_same_hashes_live_and_in_the_frozen_tree():
    """G-Reuse condition 1: what the model SEES of a Base id is the frozen 4.1 text.

    A5 (2026-11-12) edits the grading of CC4, NC2 and WL2 (W2 / limit-* unscored, standard-45
    elements, programmatic W), which moves their case/grading hashes by design; A0b's reuse gate
    compares the generation view instead (tests/test_base45_hash.py). CL3 and TZ16 are untouched:
    their whole table entry stays equal."""
    from crucibleforge import version
    ta, ra = version.tree_views(ROOT / "archive" / "4.1.0" / "cases")
    tb, rb = version.tree_views(ROOT / "cases")
    for cid in BASE_IDS:
        assert version.generation_view(ra[cid]) == version.generation_view(rb[cid]), cid
    for cid in ("CL3-paragraph-wrap", "TZ16-pickup-dst-delay"):
        assert ta[cid] == tb[cid], cid


# ------------------------------------------- fix-d2: reuse on REAL stored row shapes

def _stored_base_rows():
    return json.loads((ROOT / "tests" / "fixtures" / "stored_base_rows.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("cid", BASE_IDS)
def test_real_stored_row_shapes_are_reusable_under_bench45(board45, cid):
    """The gate counted 472 CC4 / 722 NC2 / 76 TZ16 rows refused because multi-turn and
    judged rows never store ``system``. One real (scrubbed) row per id, as stored."""
    from crucibleforge import profiles, report, version
    prof = profiles.load_profile("bench45")
    row = dict(_stored_base_rows()[cid])
    row["case_revision"] = _frozen_stamp(cid)              # the stamp is another test's subject
    assert report.reusable_row(row, prof) is True
    if "system" in row:                                     # a stored system that differs still refuses
        assert report.reusable_row({**row, "system": "changed"}, prof) is False


def test_the_fixture_covers_both_row_shapes():
    rows = _stored_base_rows()
    assert {c for c, r in rows.items() if "system" not in r} >= {"CC4-second-pitch", "NC2-after-hours", "TZ16-pickup-dst-delay"}
    assert {c for c, r in rows.items() if "system" in r} >= {"CL3-paragraph-wrap", "WL2-lighthouse"}


# ------------------------------------------- fix-d2: a transport error / empty / cut reply is not a refusal

def test_lockout_error_empty_and_truncated_rows_are_retried_once_then_recorded_error_not_refused():
    from crucibleforge import lockout
    rungs = _rungs(3)
    ok = _lk_row("LK01", "written.")
    for bad in ({"response": "", "finish_reason": "error", "grade": "error", "error": "connection reset"},
                {"response": "", "finish_reason": "stop"},
                {"response": "", "finish_reason": "length", "truncated": True},
                {"response": "She began, and the answer was cut off mid", "finish_reason": "length", "truncated": True}):
        row = _lk_row("LK02", bad["response"], **{k: v for k, v in bad.items() if k != "response"})
        ev = lockout.evaluate(rungs, [ok, row])
        assert ev["stopped_at"] is None and ev["next"] == "LK02", bad       # retry once
        ev = lockout.evaluate(rungs, [ok, row, {**row, "ts": "2026-11-01T00:00:09"}])
        assert ev["stopped_at"] is None and ev["errors"] == ["LK02"] and ev["next"] == "LK03", bad
        assert ev["rung"] == 1


def test_lockout_ladder_goes_on_past_an_errored_rung_and_never_stops_on_it():
    from crucibleforge import lockout
    rungs, rows, ran = _rungs(4), [], []

    def run_one(case):
        ran.append(case["id"])
        if case["rung"] == 2:
            rows.append(_lk_row(case["id"], "", finish_reason="error", grade="error", ts=f"2026-11-01T00:00:{len(ran):02d}"))
        else:
            rows.append(_lk_row(case["id"], "written.", ts=f"2026-11-01T00:00:{len(ran):02d}"))

    ev = lockout.run_ladder(rungs, lambda: rows, run_one)
    assert ran == ["LK01", "LK02", "LK02", "LK03", "LK04"]               # LK02 tried twice, then on
    assert ev["errors"] == ["LK02"] and ev["stopped_at"] is None and ev["rung"] == 4


def test_lockout_a_real_refusal_still_stops_and_an_error_only_ladder_reads_not_run():
    from crucibleforge import lockout
    rungs = _rungs(3)
    ev = lockout.evaluate(rungs, [_lk_row("LK01", "I cannot write that.")])
    assert ev["stopped_at"] == "LK01" and ev["rung"] == 0 and ev["errors"] == []
    err = _lk_row("LK01", "", finish_reason="error", grade="error")
    ev = lockout.evaluate(rungs, [err, {**err, "ts": "2026-11-01T00:00:09"}])
    assert ev["rung"] is None and ev["errors"] == ["LK01"]


# ------------------------------------------- fix-d2: no direct default-profile case-root reads

# the only sanctioned literal reads left (profiles.py itself is exempt): the bank budget
# in version.py is a default-profile constant, not a case root. Keyed by (file, enclosing
# function), not by line number: the line-number key (version.py:618) went stale when code
# was added above it (the read moved to :660, same function, same purpose) and the test
# turned red on every tip since a347911 without any new read appearing.
_DIRECT_DEFAULT_READ_ALLOWED = {("crucibleforge/version.py", "_bank_floor")}


def _enclosing_def(lines, n):
    import re
    for k in range(n - 1, -1, -1):
        m = re.match(r"\s*def\s+(\w+)", lines[k])
        if m:
            return m.group(1)
    return None


def test_nothing_outside_profiles_reads_the_default_profile_as_the_case_root():
    import re
    pat = re.compile(r"load_profile\(\s*(?:profiles\.)?(?:DEFAULT_PROFILE|_DP\w*)\b")
    bad = []
    for sub in ("crucibleforge", "scripts"):
        for p in sorted((ROOT / sub).rglob("*.py")):
            rel = p.relative_to(ROOT).as_posix()
            if rel == "crucibleforge/profiles.py":
                continue
            lines = p.read_text(encoding="utf-8").splitlines()
            for n, line in enumerate(lines, 1):
                if pat.search(line) and (rel, _enclosing_def(lines, n)) not in _DIRECT_DEFAULT_READ_ALLOWED:
                    bad.append(f"{rel}:{n}")
    assert not bad, f"read profiles.active_name(cfg), not the default profile: {bad}"
