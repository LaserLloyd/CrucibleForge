"""Knowledge answer sheets numbered with the template's literal "N." (ship plan C8, lead 2026-11-15): Grok answered
all 20 questions of FLAT-unc_know-001 in order, one line each, but wrote "N. ANSWER: ..." on every line (the
prompt's own template) and the parser graded the whole sheet as missing. When a reply has NO numbered answer
line and exactly as many literal-N answer lines as questions, the lines are the answers in order; any other
count is ambiguous and stays missing. The same rule for knowgap (its template is the same "N. ANSWER:")."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pytest

from crucibleforge import chains, graders, profiles

FIX = json.loads((Path(__file__).with_name("fixtures") / "grok_unc_know_literal_n.json").read_text(encoding="utf-8"))


@pytest.fixture
def case(monkeypatch):
    monkeypatch.setenv(profiles.COMPOSITION_ENV, "rc3-main")
    prof = profiles.load_profile("bench45")
    profiles.apply_profile(prof, {"defaults": {}, "models": [], "providers": {}})
    spec = {c["id"]: c for c in chains.chain_cases(prof)}["FLAT-unc_know-001"]
    out = chains.materialize(chains.instance(spec["chain_spec"], 1, 1))
    # the same instance Grok was sent (its prompt is not stored here: only its hash)
    assert hashlib.sha256(out["prompt"].encode()).hexdigest()[:12] == FIX["prompt_sha256_12"]
    return out


class _R:
    def __init__(self, text):
        self.text = text
        self.finish_reason = "stop"

    def scoreable_text(self):
        return self.text


def _grade(text, case):
    assert case["grader"] == "unc_know"
    return graders.GRADERS["unc_know"](_R(text), case["grader_config"])


def _numbered(text):
    lines = text.splitlines()
    return "\n".join(re.sub(r"^N\.", f"{i}.", ln) for i, ln in enumerate(lines, 1))


@pytest.mark.live_ladder
def test_grok_s_literal_n_sheet_is_graded_like_the_same_sheet_numbered(case):
    got = _grade(FIX["response"], case)
    want = _grade(_numbered(FIX["response"]), case)
    assert want["score"] > 0.5
    assert got["score"] == want["score"] and got["results"] == want["results"]
    assert not any(r["detail"] == "missing" for r in got["results"])


@pytest.mark.live_ladder
def test_a_short_literal_n_sheet_is_ambiguous_and_stays_missing(case):
    short = "\n".join(FIX["response"].splitlines()[:19])
    assert _grade(short, case)["score"] == 0.0


@pytest.mark.live_ladder
def test_numbered_lines_win_over_literal_n_lines(case):
    lines = FIX["response"].splitlines()
    mixed = "\n".join(["1. " + lines[0][3:]] + lines[1:])          # one numbered line: the sheet is numbered
    r = _grade(mixed, case)["results"]
    assert r[0]["detail"] != "missing" and r[2]["detail"] == "missing"


def test_the_rule_on_its_own_and_for_knowgap():
    f = graders.literal_n_numbered
    assert f("N. ANSWER: 790\n**N. ANSWER:** NOT STATED", 2) == "1. ANSWER: 790\n**2. ANSWER:** NOT STATED"
    assert f("N. ANSWER: 790\nN. ANSWER: NOT STATED", 3) == "N. ANSWER: 790\nN. ANSWER: NOT STATED"
    assert f("1. ANSWER: 790\nN. ANSWER: NOT STATED", 2) == "1. ANSWER: 790\nN. ANSWER: NOT STATED"
    from crucibleforge.gen import knowgap
    assert knowgap._lines(f("N. ANSWER: 790\nN. ANSWER: NOT STATED", 2)) == {1: "790", 2: "NOT STATED"}



@pytest.mark.live_ladder
def test_stored_rows_take_it_by_regrade_switch_answer_sheet(monkeypatch):
    """The rule is in graders.py, so no chain revision moves: a stored attempt stays CURRENT and a plain
    regrade writes nothing. ``--switch answer_sheet`` re-checks current attempts and supersedes only those
    whose verdict changes (Grok's sheet: 0 -> its numbered grade); an already-numbered sheet stays current."""
    from crucibleforge import regrade
    monkeypatch.setenv(profiles.COMPOSITION_ENV, "rc3-main")
    prof = profiles.load_profile("bench45")
    profiles.apply_profile(prof, {"defaults": {}, "models": [], "providers": {}})
    specs = {c["id"]: c for c in chains.chain_cases(prof)}
    inst = chains.instance(specs["FLAT-unc_know-001"], 1, 1)
    case = chains.materialize(inst)

    def row(run, text):
        return {"bench_run_id": run, "case_id": inst["id"], "repeat": 1, "turn": None, "category": case["category"],
                "system": case.get("system"), "prompt": case["prompt"], "response": text, "reasoning": "",
                "finish_reason": "stop", "tool_calls": [], "metrics": {"completion_tokens": 900},
                "chain": {**inst["chain"], "score": 0.0, "passed": False, "outcome": "fail"},
                "chain_revision": chains.revision(inst["chain"])}

    good = _numbered(FIX["response"])
    rows = [row("grok", FIX["response"]), row("other", good)]
    assert [r["status"] for r in regrade.plan(rows, specs, "t")] == ["current", "current"]
    first = regrade.plan(rows, specs, "t", switch="answer_sheet")
    assert first[0]["status"] == "regrade"
    assert first[0]["score_after"] == pytest.approx(_grade(good, case)["score"]) and first[0]["passed_after"] is True
    assert first[0]["rows"][-1]["regraded_by"]["switch"] == "answer_sheet"
    # a sheet whose stored verdict already is what the grader gives is not rewritten; a second pass is a no-op
    stored_other = first[1]["rows"]
    again = regrade.plan([rows[0], *stored_other], specs, "t", switch="answer_sheet")
    assert [r["status"] for r in again] == ["regrade", "current"]
    done = regrade.plan([*first[0]["rows"], *stored_other], specs, "t", switch="answer_sheet")
    assert [r["status"] for r in done] == ["current", "current"]
