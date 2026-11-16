"""4.5 time budget check (E7): `crucibleforge budget --home` and its committed inputs.

The home budget is literal (PLAN-45-v4 D1): a non-thinking model at budget.home.tps
on budget.home.slots, plus judge_s_per_call per judge call and the load, <= limit_min.
Pure over committed data: no results/ directory, no models.yaml, no network."""
import copy
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from crucibleforge import budget, config, profiles, version


@pytest.fixture(autouse=True, scope="module")
def _legacy_profile_dir():
    """These tests predate the 4.5 v2 bench45 (hard sets, 29 cases): they run on the
    pre-WP-4 profile kept in tests/fixtures/legacy_profiles/."""
    from crucibleforge import profiles
    mp = pytest.MonkeyPatch()
    legacy = __import__("pathlib").Path(__file__).parent / "fixtures" / "legacy_profiles"
    live = profiles.PROFILES_DIR
    mp.setattr(profiles, "_dirs", lambda cfg: [legacy, live])
    mp.setattr(profiles, "activate_cases_root", lambda prof: None)   # root stays cases/
    yield
    mp.undo()


ROOT = Path(__file__).resolve().parent.parent
HOME = budget.HOME_DEFAULTS


@pytest.fixture(scope="module")
def prof():
    return profiles.load_profile("bench45", config.load_config(config.EXAMPLE_CONFIG_PATH))


@pytest.fixture(scope="module")
def targets():
    return budget.load_targets(ROOT / "tests" / "fixtures" / "legacy_profiles" / "targets-45-legacy.json")


@pytest.fixture(scope="module")
def reference():
    return budget.load_reference(ROOT / "budget" / "reference.json")


def _run(prof, targets, reference, **kw):
    kw.setdefault("packs", False)
    return budget.compute(prof, targets=copy.deepcopy(targets), reference=copy.deepcopy(reference), **kw)


def _excused(targets):
    """Targets where every case carries a retarget_note: only the home limit can fail."""
    t = copy.deepcopy(targets)
    for e in t["cases"].values():
        e["retarget_note"] = "test"
    return t


@pytest.fixture(scope="module")
def cases_copy(tmp_path_factory):
    d = tmp_path_factory.mktemp("cases") / "cases"
    shutil.copytree(ROOT / "cases", d)
    return d


# ---------------------------------------------------------------- limits

def test_home_defaults_apply_without_a_home_block(prof):
    assert budget.settings(prof)["home"] == HOME
    p = copy.deepcopy(prof)
    p["budget"]["home"] = {"tps": 30, "limit_min": 20}
    h = budget.settings(p, slots=2)["home"]
    assert (h["tps"], h["limit_min"], h["slots"], h["judge_s_per_call"]) == (30, 20, 2, 25.0)


def test_bad_home_blocks_are_refused(prof):
    for bad in ({"tps": 0}, {"tps": "fast"}, {"bogus": 1}):
        p = copy.deepcopy(prof)
        p["budget"]["home"] = bad
        with pytest.raises(budget.BudgetError):
            budget.settings(p)
        assert any("budget.home" in e for e in profiles.profile_problems(p))


def test_core_is_gated_at_the_home_limit(prof, targets, reference):
    t = _excused(targets)
    over = _run(prof, t, reference)
    assert over["core_home_min"] > HOME["limit_min"] and not over["ok"]
    assert any(p.startswith("Core ") for p in over["problems"])
    fast = _run(prof, t, reference, tps=5000, judge_s=0.01)
    assert fast["core_home_min"] < HOME["limit_min"]
    assert not any(p.startswith("Core ") for p in fast["problems"])
    assert fast["ok"], fast["problems"]


def test_the_limit_is_the_profiles_home_limit(prof, targets, reference):
    p = copy.deepcopy(prof)
    base = _run(p, _excused(targets), reference)
    p["budget"]["rig"] = {"limit_min": base["core_rig_min"] + 1}
    p["budget"]["home"] = {"limit_min": base["core_home_min"] + 1}
    assert _run(p, _excused(targets), reference)["ok"]
    p["budget"]["home"] = {"limit_min": base["core_home_min"] - 1}
    assert not _run(p, _excused(targets), reference)["ok"]


# ---------------------------------------------------------------- sources

def test_every_case_has_a_source_and_a_target(prof, targets, reference):
    rep = _run(prof, targets, reference)
    assert len(rep["cases"]) == 29 and not any(r["hard"] for r in rep["cases"])
    for r in rep["cases"]:
        assert r["source"] in ("NOMINAL", "ESTIMATE"), r["id"]
        assert r["tokens"] and r["minutes"] > 0


def test_a_case_without_any_source_fails(prof, targets, reference):
    t = copy.deepcopy(targets)
    del t["cases"]["CL8-machine-plan"]
    ref = copy.deepcopy(reference)                                  # a brand-new case: nobody has run it
    for m in ref["models"].values():
        m["cases"].pop("CL8-machine-plan", None)
    rep = _run(prof, t, ref)
    assert any("CL8-machine-plan: no budget source" in p for p in rep["problems"])


def test_pending_cases_are_estimates(prof, targets, reference):
    rows = {r["id"]: r for r in _run(prof, targets, reference)["cases"]}
    assert rows["CL3-paragraph-wrap"]["source"] == "ESTIMATE" and rows["CL3-paragraph-wrap"]["pending"]


# fix-d: CB-F1 instead of CL7 (CL7 is element_standard 45, so E11a moved its hash off the digest)
def test_nominal_is_the_median_over_the_reference_models(prof, targets, reference):
    h = version.case_hash("CB-F1-larder-bughunt")
    refs = [m for m in prof["budget"]["reference_models"]
            if budget.measured_tokens(reference["models"].get(m, {}).get("cases", {}).get("CB-F1-larder-bughunt"), h)]
    assert len(refs) >= 2
    vals = sorted(budget.measured_tokens(reference["models"][m]["cases"]["CB-F1-larder-bughunt"], h) for m in refs)
    want = vals[len(vals) // 2] if len(vals) % 2 else sum(vals[len(vals) // 2 - 1:len(vals) // 2 + 1]) / 2
    r = next(x for x in _run(prof, targets, reference)["cases"] if x["id"] == "CB-F1-larder-bughunt")
    assert r["source"] == "NOMINAL" and r["tokens"] == pytest.approx(want)
    # a model outside reference_models never moves it
    ref = copy.deepcopy(reference)
    outsider = next(m for m in ref["models"] if m not in prof["budget"]["reference_models"])
    ref["models"][outsider]["cases"]["CB-F1-larder-bughunt"] = {"hash": h, "turn_tokens": [1], "seconds": 1}
    r2 = next(x for x in _run(prof, targets, ref)["cases"] if x["id"] == "CB-F1-larder-bughunt")
    assert r2["tokens"] == pytest.approx(want)


def test_no_reference_measurement_falls_back_to_the_target(prof, targets, reference):
    ref = copy.deepcopy(reference)
    for m in prof["budget"]["reference_models"]:
        if m in ref["models"]:
            ref["models"][m]["cases"].pop("CL7-span-by-example", None)
    t = copy.deepcopy(targets)
    t["cases"]["CL7-span-by-example"]["target_tokens"] = 6000
    r = next(x for x in _run(prof, t, ref)["cases"] if x["id"] == "CL7-span-by-example")
    assert r["source"] == "ESTIMATE" and r["tokens"] == 6000


def test_an_old_hash_is_not_a_measurement(prof, targets, reference):
    ref = copy.deepcopy(reference)
    for m in prof["budget"]["reference_models"]:
        if m in ref["models"] and "CL7-span-by-example" in ref["models"][m]["cases"]:
            ref["models"][m]["cases"]["CL7-span-by-example"]["hash"] = "deadbeef"
    r = next(x for x in _run(prof, targets, ref)["cases"] if x["id"] == "CL7-span-by-example")
    assert r["source"] == "ESTIMATE"


def test_a_nominal_case_far_over_target_fails_unless_retargeted(prof, targets, reference):
    t = copy.deepcopy(targets)
    t["cases"]["CB-F1-larder-bughunt"]["target_tokens"] = 100       # the median is far above
    rep = _run(prof, t, reference)
    assert any("CB-F1-larder-bughunt" in p and "over its" in p for p in rep["problems"])
    t["cases"]["CB-F1-larder-bughunt"]["retarget_note"] = "harder spec, accepted by the maintainer"
    assert not any("CB-F1-larder-bughunt" in p for p in _run(prof, t, reference)["problems"])


# ---------------------------------------------------------------- the math

def test_minutes_follow_the_formula(prof, targets, reference):
    rep = _run(prof, targets, reference)
    r = next(x for x in rep["cases"] if x["id"] == "RD1-marisol")
    assert r["minutes"] == pytest.approx((r["tokens"] / HOME["tps"] + r["prefill_s"]) / HOME["slots"] / 60)
    assert r["judge_min"] == pytest.approx(r["calls"] * HOME["judge_s_per_call"] / 60)
    c = rep["core"]
    assert c["generation_min"] == pytest.approx(sum(x["minutes"] for x in rep["cases"]))
    assert c["total_min"] == pytest.approx(HOME["load_min"] + c["generation_min"] + c["judge_calls"] * 25 / 60)
    assert rep["core_home_min"] == c["total_min"]
    assert rep["home"] == HOME


def test_tps_slots_and_judge_seconds_move_only_their_part(prof, targets, reference):
    a = _run(prof, targets, reference)["core"]
    b = _run(prof, targets, reference, tps=10)["core"]
    assert b["generation_min"] > 1.9 * a["generation_min"] and b["judge_min"] == a["judge_min"]
    c = _run(prof, targets, reference, slots=2)["core"]
    assert c["generation_min"] == pytest.approx(a["generation_min"] / 2)
    d = _run(prof, targets, reference, judge_s=50)["core"]
    assert d["judge_min"] == pytest.approx(2 * a["judge_min"]) and d["generation_min"] == a["generation_min"]


@pytest.mark.live_ladder   # reads the live bench45 pack sets (conftest otherwise empties the ladder dir)
def test_packs_are_reported_and_never_gated(prof, targets, reference):
    with_p = budget.compute(prof, targets=copy.deepcopy(targets), reference=copy.deepcopy(reference), packs=True)
    without = _run(prof, targets, reference)
    assert with_p["packs"] and with_p["packs_min"] > 0
    assert all("error" not in p for p in with_p["packs"])   # real sets, not the discovery-error row
    assert with_p["ok"] == without["ok"] and with_p["problems"] == without["problems"]
    assert with_p["core_home_min"] == without["core_home_min"]


def test_a_generated_pack_is_sized_from_its_family_levels(prof, targets, reference, tmp_path, monkeypatch):
    import json
    import types
    from crucibleforge import gen, ladder
    toy = types.SimpleNamespace(FAMILY="toy", LEVELS={1: {"answer_tokens": 1000}, 2: {"answer_tokens": 3000}},
                                materialize=lambda g, **k: {"prompt": "x"}, reference=lambda g, **k: "",
                                mutants=lambda g, **k: [], grade=lambda a, m: 0.0)
    gen.register(toy)
    root = tmp_path / "ladder" / "work" / "code"
    root.mkdir(parents=True)
    (root / "set-1.json").write_text(json.dumps(
        {"category": "coding", "generator": {"family": "toy", "rungs": [1, 2], "per_rung": 1}}), encoding="utf-8")
    monkeypatch.setattr(ladder, "roots", lambda public=False: [tmp_path / "ladder"])
    try:
        rep = budget.compute(prof, targets=copy.deepcopy(targets), reference=copy.deepcopy(reference))
    finally:
        gen.unregister("toy")
    p = next(x for x in rep["packs"] if x["set"] == "work/code#1")
    assert p["tokens"] == 4000 and p["unsized"] == []
    assert p["minutes"] == pytest.approx(4000 / 20 / 60)


# ---------------------------------------------------------------- tree calls

@pytest.fixture
def derived(monkeypatch):
    """The derivation path (used when the engine count is unavailable)."""
    monkeypatch.setattr(budget, "_engine_expected_calls", lambda case, prof: None)


def test_tree_judge_calls_from_elements(derived):
    two_groups = {"id": "X", "rubric": "rp_deep", "judge_elements": [
        {"id": "a", "turns": [1]}, {"id": "b", "turns": [1]}, {"id": "c", "turns": [2, 3]}]}
    assert budget.tree_judge_calls({"id": "C", "grader": "exact"}) == 0.0
    assert budget.tree_judge_calls(two_groups) == pytest.approx(2 + budget.CRAFT_CALLS)
    p = {"judge_policy": {"element_class": {"fact": 2, "craft": 3}}}
    assert budget.tree_judge_calls(two_groups, p) == pytest.approx(4 + budget.CRAFT_CALLS)


# ---------------------------------------------------------------- CLI, CI, verify

def _cli(*args, env=None):
    return subprocess.run([sys.executable, "-m", "crucibleforge.cli", *args], cwd=ROOT, text=True,
                          capture_output=True, timeout=300, env=env)


def test_cli_home_json_and_exit_codes(tmp_path):
    r = _cli("budget", "--home", "--tps", "20", "--json")
    rep = json.loads(r.stdout)
    assert rep["home"]["tps"] == 20 and "core_home_min" in rep and "packs_min" in rep
    assert (r.returncode == 0) == rep["ok"]
    fast = _cli("budget", "--home", "--tps", "50000", "--judge-s", "0.01", "--json")
    assert json.loads(fast.stdout)["home"]["tps"] == 50000
    one = _cli("budget", "--home", "--case", "CL3-paragraph-wrap", "--tps", "20")
    assert one.returncode == 0 and "CL3-paragraph-wrap" in one.stdout and "partial" in one.stdout
    assert "CL7-span" not in one.stdout
    bad = _cli("budget", "--tps", "0")
    assert bad.returncode == 2


def test_budget_needs_no_results_dir_or_models_yaml(tmp_path):
    import os
    env = dict(os.environ, CRUCIBLEFORGE_RESULTS=str(tmp_path / "nothing-here"))
    r = _cli("budget", "--home", "--tps", "20", env=env)
    assert "core_home_min" in r.stdout, r.stderr + r.stdout
    assert not (tmp_path / "nothing-here").exists()


def test_cases_verify_calls_the_budget_check_only_on_profiles_that_have_one(prof):
    old = _cli("cases", "verify", "--profile", "bench", "--categories", "overrefusal")
    assert "budget" not in (old.stdout + old.stderr).lower()
    # A6: the 4.5 Core fits the profile's run ceiling (default_run.ceiling_minutes), so `warn` stays quiet ...
    new = _cli("cases", "verify", "--profile", "bench45", "--categories", "overrefusal", "--budget", "warn")
    assert "WARN budget" not in new.stdout
    # ... and the check itself still fails a Core that does not fit (`error` turns that into rc 1)
    tight = copy.deepcopy(profiles.load_profile("bench45", config.load_config(config.EXAMPLE_CONFIG_PATH)))
    tight["budget"]["home"] = {**(tight["budget"].get("home") or {}), "limit_min": 5}
    assert any("Core" in p for p in budget.check_problems(tight))
    off = _cli("cases", "verify", "--profile", "bench45", "--categories", "overrefusal", "--budget", "off")
    assert "budget" not in off.stdout.lower()


def test_reference_digest_is_public(reference):
    assert len(reference["models"]) >= 40
    text = (ROOT / "budget" / "reference.json").read_text(encoding="utf-8")
    for banned in ('"prompt"', '"response"', "cases/private", "bench.private"):
        assert banned not in text


def test_digest_builder_roundtrip(tmp_path, prof):
    """build_reference on a tiny synthetic results dir: per-turn medians and the case stamp."""
    h = version.case_hash("RD1-marisol")
    def row(turn, tok, run, ts):
        return {"case_id": "RD1-marisol", "bench_run_id": run, "round": None, "run_id": f"{run}{turn}", "turn": turn,
                "repeat": 1, "ts": ts, "case_revision": h, "metrics": {"completion_tokens": tok, "total_s": 10, "tok_per_s": 12.0}}
    rows = [row(1, 100, "r1", "2026-11-01T00:00:00"), row(2, 300, "r1", "2026-11-01T00:00:00"),
            row(1, 200, "r2", "2026-11-02T00:00:00"), row(2, 500, "r2", "2026-11-02T00:00:00"),
            row(1, 900, "r3", "2026-11-03T00:00:00"), row(2, 100, "r3", "2026-11-03T00:00:00")]
    (tmp_path / "transcripts_m1.jsonl").write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    (tmp_path / "meta_m1.json").write_text(json.dumps({"provider": "deepseek", "rounds": [{"workers": 4}]}), encoding="utf-8")
    ref = budget.build_reference(tmp_path, prof, min_cases=1)
    e = ref["models"]["m1"]["cases"]["RD1-marisol"]
    assert e["hash"] == h and e["turn_tokens"] == [200, 300] and e["episodes"] == 3
    assert ref["models"]["m1"]["hosted"] and ref["models"]["m1"]["slots"] == 4


def test_require_measured_lists_every_estimate_case(prof, targets, reference):
    rep = _run(prof, targets, reference, require_measured=True)
    est = [r["id"] for r in rep["cases"] if r["source"] == "ESTIMATE"]
    assert len(est) >= 20 and not rep["ok"]
    listed = [p.split(":")[0] for p in rep["problems"] if "ESTIMATE only" in p]
    assert sorted(listed) == sorted(est)
    assert not any("ESTIMATE only" in p for p in _run(prof, targets, reference)["problems"])   # off by default
    frozen = copy.deepcopy(prof)
    frozen["budget"]["freeze"] = True
    assert not budget.compute(frozen, targets=copy.deepcopy(targets), reference=copy.deepcopy(reference),
                              packs=False)["ok"]


def test_a_rewritten_case_takes_its_judge_calls_from_the_tree(prof, targets, reference, monkeypatch):
    """Once a case's hash differs from pending_from_hash the table no longer speaks for it."""
    tg = copy.deepcopy(targets)
    cid = next(i for i, e in tg["cases"].items() if e.get("pending_from_hash") and e.get("judge_calls"))
    tg["cases"][cid]["pending_from_hash"] = version.case_hash(cid)           # still at its pending content
    base = _run(prof, tg, reference)
    r0 = next(r for r in base["cases"] if r["id"] == cid)
    assert r0["calls_src"] == "table"
    tg["cases"][cid]["pending_from_hash"] = "00000000"                       # the case has moved on
    monkeypatch.setattr(budget, "tree_judge_calls", lambda c, p=None: r0["calls"] * 2)
    rep = _run(prof, tg, reference)
    r1 = next(r for r in rep["cases"] if r["id"] == cid)
    assert r1["calls_src"] == "tree" and r1["calls"] == r0["calls"] * 2
    assert any(p.startswith(cid) and "judge calls" in p for p in rep["problems"])


def test_unset_class_with_a_rubric_dimension_is_craft_like_the_engine(derived):
    els = [{"id": "a", "turns": [1]}, {"id": "p", "dim": "prose"}]
    c = {"id": "X", "rubric": "rp_single", "judge_elements": els}
    assert budget.tree_judge_calls(c) == pytest.approx(1 + budget.CRAFT_CALLS)


def test_tree_calls_prefer_the_engine_when_it_exposes_expected_calls(monkeypatch):
    from crucibleforge import judge
    monkeypatch.setattr(judge, "expected_calls", lambda case, pol: {"calls": 7.0}, raising=False)
    assert budget.tree_judge_calls({"id": "X", "rubric": "rp_deep", "judge_elements": [{"id": "a"}]}) == 7.0


def test_reference_builder_drops_private_scope_ids(tmp_path, prof, monkeypatch):
    h = version.case_hash("RD1-marisol")
    row = {"case_id": "RD1-marisol", "bench_run_id": "r", "round": None, "run_id": "a", "turn": 1, "repeat": 1,
           "ts": "2026-11-01", "case_revision": h, "metrics": {"completion_tokens": 10, "total_s": 1, "tok_per_s": 9.0}}
    (tmp_path / "transcripts_m1.jsonl").write_text(json.dumps(row), encoding="utf-8")
    (tmp_path / "meta_m1.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(profiles, "private_scope", lambda *a, **k: {"case_ids": {"RD1-marisol"}})
    assert budget.build_reference(tmp_path, prof, min_cases=1)["models"] == {}


def test_every_digest_case_id_is_in_the_public_case_table(reference):
    table = version.case_table()
    for label, m in reference["models"].items():
        assert set(m["cases"]) <= set(table), (label, set(m["cases"]) - set(table))


def test_cli_require_measured_fails_on_the_committed_tree():
    r = _cli("budget", "--home", "--require-measured")
    assert r.returncode == 1 and "ESTIMATE only" in r.stdout


def test_core_limit_is_the_profiles_run_ceiling_and_the_home_figure_is_reported(monkeypatch):
    """R21 (2026-11-14): the run length is derived, the ceiling is the profile's `default_run.ceiling_minutes`
    (180), not a fixed 30; the 20 tok/s x 1 slot home figure is reported, never enforced on its own."""
    monkeypatch.setattr(profiles, "_dirs", lambda cfg: [profiles.PROFILES_DIR])    # the LIVE bench45, not the legacy one
    monkeypatch.setattr(profiles, "activate_cases_root", lambda prof: None)
    prof = copy.deepcopy(profiles.load_profile("bench45", config.load_config(config.EXAMPLE_CONFIG_PATH)))
    ceiling = prof["default_run"]["ceiling_minutes"]
    s = budget.settings(prof)
    assert s["home"]["limit_min"] == s["rig"]["limit_min"] == ceiling
    assert s["home"]["tps"] == 20.0
    assert not any(p.startswith("Core") for p in budget.check_problems(prof))
    rep = budget.compute(prof, targets=budget.load_targets(ROOT / budget.DEFAULT_TARGETS),
                         reference=budget.load_reference(ROOT / budget.DEFAULT_REFERENCE))
    assert any("tok/s" in n and "home figure, reported" in n and f"limit {ceiling:g}" in n for n in rep["notes"])
    tight = copy.deepcopy(prof)
    tight["default_run"]["ceiling_minutes"] = 5
    assert any(p.startswith("Core") and "> limit 5" in p for p in budget.check_problems(tight))
    # an explicit budget.home limit_min still wins over the run ceiling
    tight["default_run"]["ceiling_minutes"] = ceiling
    tight["budget"]["home"] = {"limit_min": 5}
    assert budget.settings(tight)["home"]["limit_min"] == 5
