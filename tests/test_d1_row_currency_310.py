"""Suite 3.10 D1 (lock-in round, 2026-10-30): currency is decided PER ROW and
PER CASE (version.row_current), generation staleness (case_hash) is split from
grading staleness (grading_hash, re-judged from the stored transcript), and
checks / tool-step needles are report-time only. The H0 zero-diff invariant:
rows stamped 3.9.0+ba6eb461 of unchanged cases stay current after the
SUITE_VERSION bump, with nothing to regenerate or re-judge."""
from __future__ import annotations

import copy
import hashlib
import json
import re
import shutil
import subprocess
import tarfile
import io
from pathlib import Path

import pytest

from crucibleforge import bench_all, judge, ladder, version
from crucibleforge import config as cfgmod

ROOT = Path(__file__).resolve().parent.parent
SNAP = "3.9.0+ba6eb461"
BASE_COMMIT = "ff4cce4"


def _old_recipe(c: dict, extra_drop=()) -> str:
    """The pre-3.10 bank/ladder stamp recipe (grading keys dropped, sort_keys)."""
    c = {k: v for k, v in c.items() if k not in version._GRADING_KEYS and k not in extra_drop}
    return hashlib.sha256(json.dumps(c, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:8]


# ------------------------------------------------------------ version tuples

def test_suite_versions_compare_as_tuples():
    assert version.SUITE_VERSION == "4.0.1"
    assert version.stamp_version("4.0.1+x") > version.stamp_version("4.0.0+x") > version.stamp_version("3.10.0+x")
    assert version.stamp_version("3.10.0+x") > version.stamp_version("3.9.0+y")
    assert "3.10.0" < "3.9.0"            # why strings must never be compared
    assert version.in_regime("3.10.0+abc") and version.in_regime("3.9.0+abc")
    assert not version.in_regime("3.8.0+36a60ec6") and not version.in_regime(None)
    assert not version.in_regime("garbage")


def test_no_string_comparison_of_suite_versions():
    """grep guard: suite versions are compared through stamp_version only."""
    bad = re.compile(r"(suite_of\([^)]*\)|SUITE_VERSION|_REGIME_SINCE)\s*(==|!=|<=|>=|<|>)"
                     r"|(==|!=|<=|>=|<|>)\s*(SUITE_VERSION|_REGIME_SINCE)\b")
    hits = []
    for p in sorted((ROOT / "crucibleforge").glob("*.py")):
        for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            code = line.split("#", 1)[0]
            if bad.search(code):
                hits.append(f"{p.name}:{i}: {line.strip()}")
    assert not hits, hits


# ------------------------------------------------------------ the snapshot

def _extract_base(tmp_path) -> Path | None:
    try:
        blob = subprocess.run(["git", "-C", str(ROOT), "archive", BASE_COMMIT, "cases"],
                              capture_output=True, check=True, timeout=60).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    with tarfile.open(fileobj=io.BytesIO(blob)) as tf:
        tf.extractall(tmp_path, filter="data")
    return tmp_path / "cases"


def test_snapshot_reproduces_from_the_base_commit(tmp_path):
    snap = json.loads((version.STAMPS_DIR / f"{SNAP}.json").read_text(encoding="utf-8"))
    base = _extract_base(tmp_path)
    if base is None:
        pytest.skip(f"git history with {BASE_COMMIT} not available")
    assert version.case_table(base, [base / "ladder"]) == snap
    # the snapshot's whole-set hash is the stamp it is named after
    saved = version._CASES_DIR
    try:
        version._CASES_DIR = base
        assert version.cases_hash() == SNAP.split("+", 1)[1]
    finally:
        version._CASES_DIR = saved


def test_snapshot_covers_every_public_case_id_of_the_base():
    snap = json.loads((version.STAMPS_DIR / f"{SNAP}.json").read_text(encoding="utf-8"))
    assert len(snap) > 300
    for cid in ("RD1-marisol", "NMX1-needle-and-gull", "CB-D4-watch-sessions",
                "LRZ1-cell-row-trace", "XP1-three-rooms", "WR1-ninth-string"):
        assert cid in snap and set(snap[cid]) == {"case", "grading"}
    assert any(k.startswith("TZ16") for k in snap)


def test_aliases_keep_the_pre_310_stamps():
    """bank_case_hash / ladder_case_hash are thin aliases of case_hash, and
    equal the old recipe — rows stamped before 3.10 keep matching."""
    for cat in version.BANK_CATEGORIES:
        p = ROOT / "cases" / f"{cat}.json"
        for c in json.loads(p.read_text(encoding="utf-8")):
            assert version.bank_case_hash(cat, c["id"]) == _old_recipe(c) == version.case_hash(c["id"])
    for c in ladder.inline_cases():
        assert version.ladder_case_hash(c["id"]) == _old_recipe(c, ("ladder_pack",))


# ------------------------------------------------------------ per-case invariants

def _table_after(tmp_path, edit) -> tuple[dict, dict]:
    src = tmp_path / "src"
    shutil.copytree(ROOT / "cases", src, ignore=shutil.ignore_patterns("private"))
    before = version.case_table(src, [src / "ladder"])
    edit(src)
    return before, version.case_table(src, [src / "ladder"])


def _edit_case(src: Path, file: str, cid: str, fn):
    p = src / f"{file}.json"
    data = json.loads(p.read_text(encoding="utf-8"))
    for c in data:
        if c["id"] == cid:
            fn(c)
    p.write_text(json.dumps(data, indent=1, ensure_ascii=True), encoding="utf-8")


def _changed(a: dict, b: dict, what: str) -> set[str]:
    return {k for k in set(a) | set(b) if (a.get(k) or {}).get(what) != (b.get(k) or {}).get(what)}


def _first_with(file: str, key: str) -> dict:
    return next(c for c in json.loads((ROOT / "cases" / f"{file}.json").read_text(encoding="utf-8"))
                if c.get(key))


def test_grading_edit_flips_only_that_case_to_rejudge(tmp_path):
    c0 = _first_with("nsfw", "judge_elements")

    def edit(src):
        def fn(c):
            c["judge_elements"][0]["q"] += " (stricter)"
        _edit_case(src, "nsfw", c0["id"], fn)
    a, b = _table_after(tmp_path, edit)
    assert _changed(a, b, "grading") == {c0["id"]}
    assert _changed(a, b, "case") == set()


def test_element_weight_edit_is_report_only(tmp_path):
    c0 = _first_with("rpdeep", "judge_elements")

    def edit(src):
        def fn(c):
            c["judge_elements"][0]["weight"] = 0.25
        _edit_case(src, "rpdeep", c0["id"], fn)
    a, b = _table_after(tmp_path, edit)
    assert _changed(a, b, "grading") == set() and _changed(a, b, "case") == set()


def test_prompt_edit_flips_only_that_case_to_regenerate(tmp_path):
    c0 = _first_with("math", "prompt")

    def edit(src):
        _edit_case(src, "math", c0["id"], lambda c: c.__setitem__("prompt", c["prompt"] + " "))
    a, b = _table_after(tmp_path, edit)
    assert _changed(a, b, "case") == {c0["id"]}
    assert _changed(a, b, "grading") == set()


def test_checks_regex_edit_flips_nothing(tmp_path):
    c0 = _first_with("nsfw", "checks")

    def edit(src):
        def fn(c):
            for ch in c["checks"]:
                if ch.get("pattern"):
                    ch["pattern"] = ch["pattern"] + "|zzz-never"
                    return
        _edit_case(src, "nsfw", c0["id"], fn)
    a, b = _table_after(tmp_path, edit)
    assert _changed(a, b, "case") == set() and _changed(a, b, "grading") == set()


def test_tool_step_needle_edit_flips_nothing(tmp_path):
    c0 = next(c for c in json.loads((ROOT / "cases" / "tooluse.json").read_text(encoding="utf-8"))
              if any(isinstance(s, dict) and s.get("answer_contains") for s in c.get("tool_script") or []))

    def edit(src):
        def fn(c):
            for s in c["tool_script"]:
                if s.get("answer_contains"):
                    s["answer_contains"] = list(s["answer_contains"]) + ["himself"]
                    s["answer_forbid"] = list(s.get("answer_forbid") or []) + ["zzz"]
                    s["ask_ok"] = True          # lane C: grading-only step key
        _edit_case(src, "tooluse", c0["id"], fn)
    a, b = _table_after(tmp_path, edit)
    assert _changed(a, b, "case") == set() and _changed(a, b, "grading") == set()


def test_a_new_case_marks_only_that_case(tmp_path):
    def edit(src):
        p = src / "math.json"
        data = json.loads(p.read_text(encoding="utf-8"))
        data.append({"id": "MHZZ-new", "prompt": "What is 2+2?", "grader": "exact",
                     "grader_config": {"answer": "4"}})
        p.write_text(json.dumps(data, indent=1, ensure_ascii=True), encoding="utf-8")
    a, b = _table_after(tmp_path, edit)
    assert set(b) - set(a) == {"MHZZ-new"}
    assert _changed(a, b, "case") == {"MHZZ-new"}


# ------------------------------------------------------------ H0 fixture rows

def _rows() -> list[dict]:
    """Rows as the live results carry them: stamped 3.9.0+ba6eb461, a bank
    row with its bank_revision, a pack question with its ladder_revision."""
    snap = json.loads((version.STAMPS_DIR / f"{SNAP}.json").read_text(encoding="utf-8"))
    bank = json.loads((ROOT / "cases" / "codebank.json").read_text(encoding="utf-8"))[0]
    pack = ladder.inline_cases()[0]
    rows = [
        {"case_id": "RD1-marisol", "category": "rpdeep"},
        {"case_id": "NC1-tideglass", "category": "nsfw"},
        {"case_id": "MH28-derangements-no-successions-7", "category": "math"},
        {"case_id": bank["id"], "category": "codebank", "bank_revision": snap[bank["id"]]["case"],
         "provider": "rig", "load_ctx": 73728},
        {"case_id": pack["id"], "category": pack["category"],
         "ladder_revision": snap[pack["id"]]["case"]},
    ]
    for r in rows:
        r.update(bench_revision=SNAP, profile="bench", needs_judge=True,
                 judge={"judge_failed": False, "scores": {}, "policy": {"samples": 1}})
    return rows


@pytest.fixture
def live_cases(monkeypatch):
    """The committed case files (whatever another test pointed the ladder at)."""
    monkeypatch.setattr(ladder, "LADDER_DIR", ROOT / "cases" / "ladder")
    monkeypatch.setattr(version, "_CASES_DIR", ROOT / "cases")


def _require_unchanged(cids):
    snap = version.stamp_snapshot(SNAP)
    changed = [c for c in cids if snap.get(c) != version.current_case_table().get(c)]
    if changed:
        pytest.skip(f"case content edited since {SNAP} for {changed} (expected after lane merges)")


def test_h0_snapshot_rows_are_current_after_the_bump(live_cases):
    rows = _rows()
    _require_unchanged([r["case_id"] for r in rows])
    cfg = {"providers": {"rig": {"type": "studioforge"}}}
    for r in rows:
        assert version.row_current(r, cfg), r["case_id"]
        assert not version.regrade_needed(r), r["case_id"]


def test_old_regime_rows_are_not_current(live_cases):
    for r in _rows():
        r["bench_revision"] = "3.8.0+36a60ec6"
        r.pop("bank_revision", None)
        r.pop("ladder_revision", None)
        assert not version.row_current(r)


def test_a_new_row_carries_case_revision_and_is_current(live_cases):
    r = {"case_id": "RD1-marisol", "category": "rpdeep", "bench_revision": version.revision(),
         "case_revision": version.case_hash("RD1-marisol")}
    assert version.row_current(r)
    assert not version.row_current({**r, "case_revision": "deadbeef"})
    # an unknown case id is never current
    assert not version.row_current({**r, "case_id": "nope"})


def test_private_rows_keep_their_category_stamp(monkeypatch, live_cases):
    cat = "privx"
    monkeypatch.setattr(cfgmod, "PRIVATE_CATEGORIES", tuple(cfgmod.PRIVATE_CATEGORIES) + (cat,))
    monkeypatch.setattr(version, "private_category_hash", lambda c: "pp11")
    r = {"case_id": "X", "category": cat, "bench_revision": SNAP, "private_revision": "pp11"}
    assert version.row_current(r)
    assert not version.row_current({**r, "private_revision": "old"})
    assert not version.row_current({**r, "bench_revision": "3.8.0+36a60ec6"})


def test_clamped_rig_bank_rows_are_not_current(live_cases):
    """review H: rig models served the bank at 40,960 — those rows re-generate."""
    cfg = {"providers": {"rig": {"type": "studioforge"}, "api": {"type": "openai"}}}
    bank = next(r for r in _rows() if r["category"] == "codebank")
    _require_unchanged([bank["case_id"]])
    floor = version._bank_floor(cfg)[0]
    assert floor == 73728
    assert version.row_current({**bank, "load_ctx": 73728}, cfg)
    assert not version.row_current({**bank, "load_ctx": 40960}, cfg)
    assert version.row_current({**bank, "load_ctx": 40960, "provider": "api"}, cfg)


# ------------------------------------------------------------ re-judge (D1.6)

def test_needs_verdict_rejudges_a_grading_edit_only(monkeypatch, live_cases):
    rows = _rows()
    _require_unchanged([r["case_id"] for r in rows])
    pc = judge.JUDGE_POLICY_DEFAULTS
    for r in rows:              # each verdict drew what the policy asks for
        r["judge"]["policy"] = {"samples": judge.judge_policy(r, pc)["samples"]}
    assert not any(judge.needs_verdict(r, pcfg=pc) for r in rows)
    target = rows[1]["case_id"]
    real = version.grading_hash
    monkeypatch.setattr(version, "grading_hash",
                        lambda cid: "0ther000" if cid == target else real(cid))
    flagged = [r["case_id"] for r in rows if judge.needs_verdict(r, pcfg=pc)]
    assert flagged == [target]
    # a verdict judged under the CURRENT grading is not redone
    fresh = {**rows[1], "judge": {**rows[1]["judge"], "grading_revision": "0ther000"}}
    assert not judge.needs_verdict(fresh, pcfg=pc)


def test_needs_verdict_rejudges_too_few_samples(live_cases):
    rows = _rows()
    _require_unchanged([r["case_id"] for r in rows])
    r = rows[1]
    pc = {**judge.JUDGE_POLICY_DEFAULTS, "samples3_rows": [r["case_id"]]}
    assert judge.needs_verdict(r, pcfg=pc)
    assert not judge.needs_verdict({**r, "judge": {**r["judge"], "samples": 3}}, pcfg=pc)


def test_needs_verdict_leaves_stale_and_failed_rows_alone(live_cases):
    r = _rows()[1]
    pc = {**judge.JUDGE_POLICY_DEFAULTS, "samples3_rows": [r["case_id"]]}
    # not current: it is re-GENERATED, not re-judged
    assert not judge.needs_verdict({**r, "bench_revision": "3.8.0+36a60ec6"}, pcfg=pc)
    # a failed verdict: only --retry-failed (capped) touches it
    failed = {**r, "judge": {"judge_failed": True, "attempts": 2}}
    assert not judge.needs_verdict(failed, pcfg=pc)
    assert not judge.needs_verdict(failed, retry_failed=True, pcfg=pc)
    assert judge.needs_verdict({**failed, "judge": {"judge_failed": True}}, retry_failed=True, pcfg=pc)


# ------------------------------------------------------------ bench-all plan (D1.7)

def test_plan_lists_regenerate_and_rejudge_per_model(monkeypatch):
    cfg = {"providers": {"rig": {"type": "studioforge"}},
           "models": [{"name": "m1", "model_id": "rig/m1", "provider": "rig"},
                      {"name": "m2", "model_id": "rig/m2", "provider": "rig"}]}
    rows = {"m1": [{"case_id": "A", "grade": "pass"}],
            "m2": [{"case_id": "A", "grade": "pass"}, {"case_id": "B", "grade": "pass"}]}
    p = bench_all.plan(cfg, {"rig/m1", "rig/m2"}, rows_for=lambda l: rows[l],
                       judge_id="j", case_ids=["A", "B"], pending_for=lambda l: 0,
                       attempts_for=lambda l: {}, rungs=[], ladder_for=lambda l: None,
                       rejudge_for=lambda l: {"A"} if l == "m2" else set())
    assert p["per_model"] == {"m1": {"regenerate": ["B"], "rejudge": []},
                              "m2": {"regenerate": [], "rejudge": ["A"]}}
    txt = bench_all.format_plan(p)
    assert "m1: regenerate: [B] · re-judge: []" in txt
    assert "m2: regenerate: [] · re-judge: [A]" in txt
    # the nightly's done-check greps "> generate": the per-model lines never match it
    assert not [ln for ln in txt.splitlines() if "regenerate:" in ln and "> generate" in ln]


def test_error_attempts_count_only_current_rows(tmp_path, monkeypatch, live_cases):
    res = tmp_path / "results"
    res.mkdir()
    monkeypatch.setattr(cfgmod, "RESULTS_DIR", res)
    cfgmod.set_results_dir(res)
    base = {"profile": "bench", "grade": "error", "category": "math",
            "case_id": "MH28-derangements-no-successions-7", "bench_revision": SNAP}
    _require_unchanged([base["case_id"]])
    with (res / "transcripts_m.jsonl").open("w", encoding="utf-8") as f:
        for i, extra in enumerate(({}, {"bench_revision": "3.8.0+36a60ec6"},
                                   {"case_revision": "deadbeef"})):
            f.write(json.dumps({**copy.deepcopy(base), **extra, "bench_run_id": f"r{i}"}) + "\n")
    assert bench_all.error_attempts("m") == {base["case_id"]: 1}


def test_h0_board_keeps_snapshot_rows_current_not_old(tmp_path, monkeypatch, live_cases):
    """Board level: a 3.9.0+ba6eb461 row of an unchanged case counts on the
    board, the model is not on old values, and coverage says nothing stale."""
    from crucibleforge import report
    res = tmp_path / "results"
    res.mkdir()
    monkeypatch.setattr(cfgmod, "RESULTS_DIR", res)
    cfgmod.set_results_dir(res)
    monkeypatch.setattr(report, "_CASES_BY_ID", None)
    cid = "WL2-lighthouse"           # 4.0: MH28 left the board; any unchanged board case
    _require_unchanged([cid])
    row = {"profile": "bench", "category": "nsfw", "case_id": cid, "repeat": 1, "turn": None,
           "bench_run_id": "r1", "bench_revision": SNAP, "grade": "pass", "metrics": {}}
    (res / "transcripts_m.jsonl").write_text(json.dumps(row) + "\n", encoding="utf-8")
    kept = report.board_filter("m", None)
    assert [r["case_id"] for r in kept] == [cid]
    rows, era = report.board_view("m", None)
    assert not era["old"] and era["suites"] == ["3.9.0"]
    cov = report._coverage(rows, {"profile": "bench"}, None, era)
    assert not cov["stale"] and "stale revision" not in cov["notes"]
    # the same row one regime earlier is the OLD board, not current
    (res / "transcripts_m.jsonl").write_text(
        json.dumps({**row, "bench_revision": "3.8.0+36a60ec6"}) + "\n", encoding="utf-8")
    assert report.board_filter("m", None) == []
