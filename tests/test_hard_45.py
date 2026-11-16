"""4.5 hard sets (WP-05): schema, profile loading, verify rules, case_hash."""
import copy
import json
import shutil
from pathlib import Path

import pytest
import yaml

from crucibleforge import config, hard, profiles, version


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
GOOD_META = {"authorship": "deepseek", "budget": {"anchor_tokens": 8000},
             "pack": "code-a", "certified": True}


def _case(cid="HC1-x", category="coding", **kw):
    c = {"id": cid, "category": category, "difficulty": "hard", "max_tokens": 2048,
         "prompt": "Write f.", "grader": "exact", "grader_config": {"answer": "1"},
         "meta": copy.deepcopy(GOOD_META), "element_standard": 45}
    c.update(kw)
    return c


def _write(cases_dir, score, cases, rev="4.5"):
    d = Path(cases_dir) / "hard" / score
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{rev}.json").write_text(json.dumps(
        {"schema": 1, "score": score, "rev": rev, "cases": cases}), encoding="utf-8")


@pytest.fixture
def prof():
    cfg = config.load_config(config.EXAMPLE_CONFIG_PATH)
    return profiles.load_profile("bench45", cfg), cfg


# ---------------------------------------------------------------- the profile

def test_bench45_loads_and_selects_the_29_main_cases(prof):
    p, cfg = prof
    _cfg2, cases = profiles.apply_profile(p, cfg)
    ids = [c["id"] for c in cases if c["category"] != "perf"]
    assert len(ids) == 29
    bench = profiles.load_profile("bench", cfg)
    assert {c for ids_ in bench["cases"].values() for c in ids_} == \
           {c for ids_ in p["cases"].values() for c in ids_}
    assert "ladder" not in p


def test_parts_are_scaled_to_8_plus_hard_2(prof):
    p, _ = prof
    for key, spec in p["scores"].items():
        std = sum(x["max"] for x in spec["parts"].values())
        assert abs(std - 8) < 1e-9, key
        assert spec["hard"]["max"] == 2
    w = {k: v["max"] for k, v in p["scores"]["work"]["parts"].items()}
    assert w == {"code": 4, "reasoning": 2, "agentic": 2}
    c = {k: v["max"] for k, v in p["scores"]["chat"]["parts"].items()}
    assert c == {"deep_rp": 2.4, "conversation": 2.4, "character": 1.6, "writing": 1.6}
    n = {k: v["max"] for k, v in p["scores"]["nsfw"]["parts"].items()}
    assert n == {"uncensored": 1.6, "nsfw_rp": 3.2, "nsfw_writing": 3.2}
    assert p["scores"]["nsfw"]["in_total"] is False


def test_hard_declared_points_at_4_5_files(prof):
    p, _ = prof
    d = profiles.hard_declared(p)
    assert set(d) == {"work", "chat", "nsfw"}
    assert {v["hard_score"] for v in d.values()} == {"code", "chat", "nsfw"}
    for v in d.values():
        assert v["file"].is_file() and v["file"].name == "4.5.json"
        spec = json.loads(v["file"].read_text(encoding="utf-8"))
        assert spec["schema"] == 1 and spec["cases"] == []
    assert profiles.hard_cases(p) == []


def test_budget_block_and_judge_policy_accepted(prof):
    p, cfg = prof
    assert p["budget"]["anchor"] == "trohrbaugh-122b-heretic"
    assert p["budget"]["anchor_limit_min"] == 49.5 and len(p["budget"]["reference_models"]) == 4
    assert p["judge"]["row_timeout_s"] == 1200
    assert p["judge_policy"]["element_class"] == {"fact": 1, "craft": 3}
    assert p["judge_policy"]["sequential_majority"] is True
    from crucibleforge.judge import judge_policy_config
    # WP-13a: the key exists for every profile; the default (4.1) profile leaves it unset
    assert judge_policy_config(cfg).get("element_class") is None
    assert profiles.profile_problems(p) == []


def test_profile_problems_flag_bad_budget_and_sums(prof):
    p, _ = prof
    bad = copy.deepcopy(p)
    bad["budget"]["occupancy"] = "high"
    bad["scores"]["chat"]["parts"]["writing"]["max"] = 2
    bad["judge_policy"]["element_class"] = {"fact": 0}
    errs = "\n".join(profiles.profile_problems(bad))
    assert "budget.occupancy" in errs and "add up to 10.4" in errs and "element_class" in errs


def test_bench_41_unchanged_and_has_no_hard():
    cfg = config.load_config(config.EXAMPLE_CONFIG_PATH)
    b = profiles.load_profile("bench", cfg)
    assert profiles.hard_declared(b) == {} and profiles.profile_problems(b) == []


# ------------------------------------------------------------- verify rules

def test_empty_shipped_hard_files_verify_clean():
    assert hard.problems(config.CASES_DIR) == []
    for s in hard.HARD_SCORES:
        assert (config.CASES_DIR / "hard" / s / "README.md").is_file()
        assert (config.CASES_DIR / "hard" / s / "shelf").is_dir()
    assert (config.CASES_DIR / "hard" / "SCHEMA.md").is_file()


def test_good_hard_case_verifies_and_loads(tmp_path):
    _write(tmp_path, "code", [_case()])
    assert hard.problems(tmp_path) == []
    (c,) = hard.load(tmp_path)
    assert c["hard_score"] == "code" and c["hard_rev"] == "4.5" and c["category"] == "coding"


@pytest.mark.parametrize("mutate,needle", [
    (lambda m: m.pop("authorship"), "authorship"),
    (lambda m: m.pop("budget"), "anchor_tokens"),
    (lambda m: m["budget"].update(anchor_tokens=0), "anchor_tokens"),
    (lambda m: m.pop("pack"), "pack"),
    (lambda m: m.pop("certified"), "certified"),
    (lambda m: m.update(certified=False), "shelf"),
])
def test_missing_meta_fails(tmp_path, mutate, needle):
    c = _case()
    mutate(c["meta"])
    _write(tmp_path, "code", [c])
    assert any(needle in e for e in hard.problems(tmp_path))


def test_meta_absent_and_wrong_category_fail(tmp_path):
    c = _case()
    del c["meta"]
    _write(tmp_path, "code", [c, _case("HC2", category="nsfw")])
    errs = "\n".join(hard.problems(tmp_path))
    assert "missing meta" in errs and "category 'nsfw'" in errs


def test_ids_must_be_disjoint_from_standard_and_each_other(tmp_path):
    _write(tmp_path, "code", [_case("A1"), _case("A1")])
    _write(tmp_path, "chat", [_case("CC3-tea-hut", category="rpdeep")])
    errs = hard.problems(tmp_path, standard_ids={"CC3-tea-hut": "profile rpdeep"})
    assert sum("not unique" in e for e in errs) == 2


def test_header_must_match_directory_and_rev(tmp_path):
    d = tmp_path / "hard" / "code"
    d.mkdir(parents=True)
    (d / "4.5.json").write_text(json.dumps({"schema": 1, "score": "chat", "rev": "9", "cases": []}), encoding="utf-8")
    errs = "\n".join(hard.problems(tmp_path))
    assert "directory says 'code'" in errs and "file name" in errs


def test_shelf_is_never_loaded(tmp_path):
    _write(tmp_path, "code", [])
    shelf = tmp_path / "hard" / "code" / "shelf"
    shelf.mkdir()
    (shelf / "cand.json").write_text(json.dumps({"schema": 1, "score": "code", "rev": "cand",
                                                 "cases": [_case("SHELF1", **{"meta": {}})]}), encoding="utf-8")
    assert hard.load(tmp_path) == [] and hard.problems(tmp_path) == []
    assert "SHELF1" not in version.case_table(tmp_path)


def test_scored_private_or_local_paths_fail_loudly(tmp_path):
    root = tmp_path / "cases" / "private"
    _write(root, "code", [_case()])
    errs = hard.problems(root)
    assert any("private" in e for e in errs)
    loc = tmp_path / "x.local.d"
    _write(loc, "code", [_case()])
    assert any("*.local.*" in e for e in hard.problems(loc))
    assert any("under private" in e for e in hard.problems(
        tmp_path, declared={"work": tmp_path / "cases" / "private" / "hard" / "code" / "4.5.json"}))


def test_profile_problems_catch_overlap_with_standard(prof, tmp_path):
    p, _ = prof
    f = ROOT / "cases" / "hard" / "code" / "4.5.json"
    _write(tmp_path, "code", [_case("CL3-paragraph-wrap")])
    errs = "\n".join(profiles.profile_problems(p, tmp_path))
    assert "not unique" in errs and "standard AND hard" in errs
    assert f.is_file()      # the repo file was not touched


def test_hard_cases_pass_the_standard_case_checks(tmp_path):
    from crucibleforge import verify_cases
    _write(tmp_path, "code", [_case()])
    (c,) = hard.load(tmp_path)
    assert verify_cases._check(c) == []
    bad = dict(c, grader="nope")
    assert verify_cases._check(bad)


def test_cli_cases_verify_sees_hard_via_load_cases():
    cases = config.load_cases(include_hard=True)
    assert all("hard_score" not in c for c in cases)      # empty shipped sets
    assert len(cases) == len(config.load_cases())


# ----------------------------------------------------------------- case_hash

def test_case_hash_does_not_depend_on_location_or_part(tmp_path):
    base = {"id": "MV1-move", "difficulty": "medium", "max_tokens": 1024,
            "prompt": "Say 1.", "grader": "exact", "grader_config": {"answer": "1"}}
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir(); b.mkdir()
    # as a HARD case: category + meta present
    _write(a, "code", [dict(base, category="instruct", meta=GOOD_META)])
    # (instruct is not a code kind; the hash path does not validate kinds)
    (b / "instruct.json").write_text(json.dumps([base]), encoding="utf-8")     # as a STANDARD case
    ha = version.case_table(a)["MV1-move"]
    hb = version.case_table(b)["MV1-move"]
    assert ha == hb
    assert version.case_table(a) is not None
    # content still matters
    _write(a, "code", [dict(base, prompt="Say 2.", category="instruct", meta=GOOD_META)])
    assert version.case_table(a)["MV1-move"]["case"] != hb["case"]
    # editing meta alone does not change it
    _write(a, "code", [dict(base, category="instruct",
                            meta=dict(GOOD_META, pack="other", authorship="grok"))])
    assert version.case_table(a)["MV1-move"] == hb


def test_hard_case_not_in_suite_hash(tmp_path, monkeypatch):
    """A hard case written into the hashed tree must not move cases_hash()."""
    shutil.copytree(ROOT / "cases", tmp_path / "cases")
    cd = tmp_path / "cases"
    monkeypatch.setattr(version, "_CASES_DIR", cd)
    before = version.cases_hash()
    assert "HC1-x" not in version._case_table_kinds(cd)[1]
    _write(cd, "code", [_case()])
    assert version._case_table_kinds(cd)[1]["HC1-x"] == "hard"
    assert version.cases_hash() == before


def test_hard_case_needs_element_standard_45(tmp_path):
    c = _case()
    c.pop("element_standard")
    _write(tmp_path, "code", [c])
    assert any("element_standard" in e for e in hard.problems(tmp_path))


def test_nsfw_authorship_claude_is_rejected_but_code_and_chat_allow_it(tmp_path):
    nsfw = _case(category="nsfw", meta=dict(GOOD_META, authorship="Claude"))
    _write(tmp_path, "nsfw", [nsfw])
    assert any("authorship" in e and "NSFW" in e for e in hard.problems(tmp_path))
    ok = tmp_path / "ok"
    _write(ok, "code", [_case(meta=dict(GOOD_META, authorship="claude"))])
    assert hard.problems(ok) == []
    _write(ok, "chat", [_case("HC2-c", category="rp", meta=dict(GOOD_META, authorship="claude"))])
    assert hard.problems(ok) == []
