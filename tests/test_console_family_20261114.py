"""Console families beyond COMPACTOR (2026-11-14, C5 TOOLWORLD).

``runner._run_console_loop`` takes the family from the case's ``console.family``: its
``new_session`` and ``console_result`` (score, per-check elements, detail). COMPACTOR rows are
byte-identical to the hard-wired loop (digest pinned before the change), and a console family's
per-check results become the unit's elements on the board the way a flat set's checks do.
"""
from __future__ import annotations

import hashlib
import json
import types

import pytest

from crucibleforge import chains, gen, graders, runner
from crucibleforge.api import ChatResult
from crucibleforge.gen import compactor

#: the three compactor rows (reference + two mutants) of the hard-wired loop, run_id removed
COMPACTOR_ROWS_DIGEST = "3ca78105917e9581eaaac7adbb4bd7f70b59d3caca23d13c29b31c5b95e2269b"


def _ctx(replies):
    it = iter(replies)

    class Ctx:
        sent: list = []

        def budget(self, k):
            return k

        def recover_for(self, c):
            return None

        def call(self, messages, **kw):
            Ctx.sent.append(messages[-1]["content"])
            return ChatResult(response_text=next(it, ""), reasoning_text="", finish_reason="stop", tool_calls=[],
                              prompt_tokens=10, completion_tokens=5, reasoning_tokens=0)
    return Ctx()


@pytest.fixture
def _quiet(monkeypatch):
    monkeypatch.setattr(runner, "row_budget", lambda *a, **k: None)
    monkeypatch.setattr(runner, "_prompt_tokens", lambda m: 10)


def test_compactor_rows_are_byte_identical(_quiet):
    out = []
    for key, pick in (("s1", "ref"), ("s2", "m0"), ("s3", "m1")):
        g = {"type": "compactor", "key": key}
        case = {"id": f"FC3-compactor-{key}", "category": "coding", "system": compactor.SYSTEM,
                **compactor.materialize(g, release_seed="4.5.0", case_id="x")}
        txt = (compactor.reference(g, release_seed="4.5.0", case_id="x") if pick == "ref"
               else compactor.mutants(g, release_seed="4.5.0", case_id="x")[int(pick[1])])
        row = runner._run_console_loop(case, {"case_id": case["id"]}, _ctx(txt.split("\n<<<TURN>>>\n")),
                                       512, None, None)
        row.pop("run_id")
        out.append(json.dumps(row, sort_keys=True, default=str))
    assert hashlib.sha256("\n".join(out).encode()).hexdigest() == COMPACTOR_ROWS_DIGEST


# ------------------------------------------------------------ a toy console family


class _Sess:
    """Ask for three words; DONE ends. One check per word, plus 'done'."""

    def __init__(self, key):
        self.key, self.state, self.turn, self.end, self.got = key, "running", 0, None, []

    def step_reply(self, reply):
        self.turn += 1
        if reply.strip() == "DONE":
            self.state, self.end = "stopped", "done"
            return "bye"
        self.got.append(reply.strip())
        if self.turn >= 6:
            self.state, self.end = "out of turns", "out of turns"
        return f"RESULT {self.turn}"


def _toy():
    def console_result(s):
        els = [{"id": w, "pass": w in s.got, "detail": "seen" if w in s.got else "missing"} for w in ("a", "b", "c")]
        els.append({"id": "done", "pass": s.end == "done"})
        sc = sum(e["pass"] for e in els) / len(els)
        return {"score": sc, "elements": els, "detail": f"{sum(e['pass'] for e in els)}/4"}

    def score_replies(key, replies):
        s = _Sess(key)
        for r in replies:
            if s.state != "running":
                break
            s.step_reply(r)
        return console_result(s)

    m = types.SimpleNamespace(FAMILY="toyconsole", LEVELS={1: {}}, new_session=_Sess,
                              console_result=console_result, score_replies=score_replies,
                              materialize=lambda g, **k: {}, reference=lambda g, **k: "", mutants=lambda g, **k: [],
                              grade=lambda a, m: 0.0)
    return m


@pytest.fixture
def toy():
    m = _toy()
    gen.register(m)
    yield m
    gen.unregister("toyconsole")


def _toy_case(cid="TOY-1", chain=None):
    c = {"id": cid, "category": "tools", "system": "sys", "prompt": "go",
         "console": {"family": "toyconsole", "key": "k1"},
         "grader": "console", "grader_config": {"family": "toyconsole", "key": "k1"}}
    if chain:
        c["chain"] = chain
    return c


def test_another_family_is_driven_by_its_own_session_and_result(_quiet, toy):
    ctx = _ctx(["a", "c", "DONE"])
    row = runner._run_console_loop(_toy_case(), {"case_id": "TOY-1"}, ctx, 512, None, None)
    assert ctx.sent == ["go", "RESULT 1", "RESULT 2"]                 # the family's replies are the next turns
    assert row["score"] == 0.75 and row["grade"] == "fail" and row["grade_detail"] == "3/4"
    assert [(e["id"], e["pass"]) for e in row["elements"]["results"]] == [
        ("a", True), ("b", False), ("c", True), ("done", True)]
    assert row["console"]["family"] == "toyconsole" and row["console"]["end"] == "done"
    assert row["console"]["faults"] is None and "ladders" not in row["console"]
    full = runner._run_console_loop(_toy_case(), {"case_id": "TOY-1"}, _ctx(["a", "b", "c", "DONE"]), 512, None, None)
    assert full["score"] == 1.0 and full["grade"] == "pass"


def test_a_family_without_console_result_is_refused(_quiet):
    m = types.SimpleNamespace(FAMILY="noresult", LEVELS={1: {}}, new_session=_Sess, materialize=None,
                              reference=None, mutants=None, grade=None)
    gen.register(m)
    try:
        with pytest.raises(ValueError, match="console_result"):
            runner._run_console_loop({**_toy_case(), "console": {"family": "noresult", "key": "k"}},
                                     {"case_id": "X"}, _ctx(["DONE"]), 512, None, None)
    finally:
        gen.unregister("noresult")


def test_the_grader_replays_through_the_named_family(toy):
    res = graders._grade_console("a\n<<<TURN>>>\nb\n<<<TURN>>>\nc\n<<<TURN>>>\nDONE", {"family": "toyconsole", "key": "k"})
    assert res["grade"] == "pass" and res["score"] == 1.0 and len(res["results"]) == 4
    # no family = COMPACTOR, as before
    g = {"type": "compactor", "key": "s1"}
    mat = compactor.materialize(g, release_seed="4.5.0", case_id="x")
    ref = compactor.reference(g, release_seed="4.5.0", case_id="x")
    assert graders._grade_console(ref, mat["grader_config"])["score"] == pytest.approx(0.85)


def test_console_checks_are_the_units_elements_on_the_board(_quiet, toy):
    chain = {"id": "toy-L1-001", "ladder": "toy", "element": "check", "pass_at": 1.0}
    case = _toy_case("TOY-L1-001", chain=chain)
    row = runner._run_console_loop(case, {"case_id": case["id"]}, _ctx(["a", "c", "DONE"]), 512, None, None)
    chains.stamp_verdict([row], case)
    els = row["chain"]["elements"]
    assert [(e["id"], e["score"]) for e in els] == [
        ("toy-L1-001-ca", 1.0), ("toy-L1-001-cb", 0.0), ("toy-L1-001-cc", 1.0), ("toy-L1-001-cdone", 1.0)]
    assert row["chain"]["score"] == 0.75 and row["chain"]["passed"] is False
    assert {e["outcome"] for e in els} == {"pass", "wrong"}
