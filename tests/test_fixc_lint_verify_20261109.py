"""Review F11/F19: element lint by default on 4.5 profiles, one crafted bad
element per rule fails plain `cases verify --profile bench45`; unknown profile."""
import copy
import json
import shutil
import os
import subprocess
import sys
from pathlib import Path

import pytest

from crucibleforge import config, element_lint

ROOT = Path(__file__).resolve().parent.parent

GOOD = {"id": "A1", "class": "fact", "evidence": "single", "turns": [1],
        "q": "Does Mina name Kea as her dog? Quote ONE contiguous sentence. NO if she names another dog.",
        "gold_quote": "Kea's the boss of this hut now."}
CASE = {"id": "ZZ-new", "rubric": "rp_deep", "category": "rpdeep", "element_standard": 45,
        "turns": ["hello there Mina", "tell me about the dog"], "judge_elements": [GOOD]}


def _f(case, **kw):
    return {(f["rule"], f["severity"]) for f in element_lint.lint_case(case, **kw)}


def _with(**patch):
    return {**CASE, "judge_elements": [{**GOOD, **patch}]}


def test_clean_case_is_clean():
    assert element_lint.lint_case(CASE) == []


def test_el9_template_in_a_scored_case_is_an_error_even_without_the_standard():
    c = {**_with(template=True), "element_standard": None}
    assert ("EL-9", "error") not in _f(c)
    assert ("EL-9", "error") in _f(c, scored=True)


def test_el12_ghost_target():
    assert ("EL-12", "error") in _f(_with(replaces="ghost"))
    assert ("EL-12", "error") not in _f(_with(replaces="A0"), prev_ids={"A0"})


def test_el12_migration_drop_needs_a_retire_record_or_replaces():
    assert ("EL-12", "error") in _f(CASE, prev_ids={"A1", "OLD"})
    kept = {**CASE, "judge_elements": [GOOD, {"id": "OLD", "status": "retire", "note": "covered by A1"}]}
    assert ("EL-12", "error") not in _f(kept, prev_ids={"A1", "OLD"})
    nonote = {**CASE, "judge_elements": [GOOD, {"id": "OLD", "status": "retire"}]}
    assert ("EL-12", "error") in _f(nonote, prev_ids={"A1", "OLD"})
    repl = _with(replaces="OLD")
    assert ("EL-12", "error") not in _f(repl, prev_ids={"A1", "OLD"})


def test_el13_evidence_prefix_must_be_in_an_earlier_user_turn():
    ev = [{"src": "turn", "text": "tell me about the dog"}]
    # turn 2 text is later than the element's last turn (1) -> error
    assert ("EL-13", "error") in _f(_with(evidence=ev, evidence_type="single"))
    assert ("EL-13", "error") not in _f(_with(evidence=ev, evidence_type="single", turns=[2]))
    assert ("EL-13", "error") in _f(_with(evidence=[{"src": "turn", "text": "never said anything like it"}],
                                          evidence_type="single", turns=[2]))


def test_el15_structural_fixtures_required(tmp_path):
    (tmp_path / "rp-deep" / "ZZ-new").mkdir(parents=True)
    assert ("EL-15", "error") in _f(CASE, cases_dir=tmp_path)
    fx = tmp_path / "rp-deep" / "ZZ-new" / "fixtures"
    fx.mkdir()
    (fx / "gold.md").write_text("Kea's the boss of this hut now.", encoding="utf-8")
    assert ("EL-15", "error") not in _f(CASE, cases_dir=tmp_path)
    assert ("EL-15", "error") in _f(_with(foil_fails=True), cases_dir=tmp_path)


def test_el16_row_list_overlap():
    p = {"judge_policy": {"samples3_rows": ["A", "B"], "samples1_rows": ["B"]}}
    assert any("EL-16" in x and "B" in x for x in element_lint.lint_profile(p))
    assert element_lint.lint_profile({"judge_policy": {"samples3_rows": ["A"]}}) == []


def test_standard_required_on_scored_cases_unless_pending():
    prof = {"budget": {"anchor": "x"}, "cases": {"rpdeep": ["P", "Q", "R"]}}
    cases = [{"id": "P"}, {"id": "Q", "element_standard": 45}, {"id": "R"}, {"id": "H", "hard_score": "code"}]
    out = element_lint.standard_problems(prof, cases, pending={"R"})
    assert [x.split(":")[0] for x in out] == ["H", "P"]
    assert element_lint.standard_problems({"cases": {"rpdeep": ["P"]}}, cases) == [
        "H: element_standard: 45 is required on a scored 4.5 case"]


# ------------------------------------------------ plain `cases verify --profile bench45`

def _cli(*args, cwd=ROOT):
    # the suite's own fixtures run where bwrap does not exist (conftest): the opt-in is passed explicitly, since an
    # xdist worker on Windows does not hand the parent's setting on; UTF-8 both ways (a Windows console is cp437/1252)
    env = {**os.environ, "CRUCIBLEFORGE_ALLOW_UNSANDBOXED": "1", "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
    return subprocess.run([sys.executable, "-m", "crucibleforge.cli", *args], cwd=cwd, text=True, encoding="utf-8",
                          errors="replace", capture_output=True, timeout=900, env=env)


def test_unknown_profile_is_reported_not_a_traceback():
    r = _cli("cases", "verify", "--profile", "no-such-profile", "--categories", "overrefusal")
    assert "UnboundLocalError" not in r.stderr and "Traceback" not in r.stderr
    assert r.returncode == 1 and "FAIL ladder: profile/ladder" in r.stdout


def test_plain_verify_of_bench45_fails_on_a_bad_hard_element(tmp_path):
    """A crafted hard case with a template element fails without any flag."""
    work = tmp_path / "wt"
    shutil.copytree(ROOT, work, ignore=shutil.ignore_patterns(".git", ".venv", "__pycache__", "results", "node_modules"))
    bad = {"id": "HC9-bad", "category": "rpdeep", "difficulty": "hard", "max_tokens": 2048,
           "rubric": "rp_deep", "turns": ["hi"], "element_standard": 45,
           "meta": {"authorship": "deepseek", "budget": {"anchor_tokens": 8000}, "pack": "chat-a", "certified": True},
           "judge_elements": [
               {**GOOD, "id": "e9", "template": True, "q": "Does {NAME} appear? Quote it. NO if not."},
               {**GOOD, "id": "e12", "replaces": "ghost"},
               {**GOOD, "id": "e13", "evidence": [{"src": "turn", "text": "a sentence nobody ever said"}],
                "evidence_type": "single"},
               {**GOOD, "id": "e2", "q": "Does Mina name Kea as her dog? Quote ONE contiguous sentence."},
               {**GOOD, "id": "e16", "samples": 3}]}
    (work / "cases" / "rp-deep" / "HC9-bad").mkdir(parents=True)
    d = work / "cases" / "hard" / "chat"
    (d / "4.5.json").write_text(json.dumps({"schema": 1, "score": "chat", "rev": "4.5", "cases": [bad]}), encoding="utf-8")
    r = _cli("cases", "verify", "--profile", "bench45", "--categories", "rpdeep", "--budget", "off", cwd=work)
    assert r.returncode == 1
    for rule in ("EL-2", "EL-9", "EL-12", "EL-13", "EL-15", "EL-16"):
        assert f"FAIL HC9-bad" in r.stdout and rule in r.stdout, (rule, r.stdout[-2500:])
