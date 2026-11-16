"""A check of weight 0 (IMPLIED's contested checks, lead 2026-11-14: kept and reported, out of the
numerator and the denominator) stays weight 0 on its chain element and in the scale's unit
proportion; only a missing weight counts as 1."""
from __future__ import annotations

from crucibleforge import chains, graders, scale
from crucibleforge import runner  # noqa: F401 - imported BEFORE any graders.grade patch: runner binds
#                                   graders.grade by name at import, and a patched one would outlive the test


def test_a_zero_weight_result_keeps_weight_zero(monkeypatch):
    out = {"grade": "fail", "results": [{"id": "a", "pass": True, "weight": 2.0},
                                        {"id": "contested", "pass": False, "weight": 0.0},
                                        {"id": "plain", "pass": True}]}
    monkeypatch.setattr(graders, "grade", lambda result, case: out)
    res = chains.check_results({"id": "u", "grader": "x"}, "reply")
    assert [(i, w) for i, _s, w, _g in res] == [("a", 2.0), ("contested", 0.0), ("plain", 1.0)]
    els = chains._check_els("u", res)
    assert [e["weight"] for e in els] == [2.0, 0.0, 1.0]


def test_a_zero_weight_element_is_out_of_the_unit_proportion():
    blk = {"score": 0.5, "elements": [{"score": 1.0, "weight": 2.0}, {"score": 0.0, "weight": 0.0},
                                      {"score": 0.0}]}
    x, n, ro = scale.unit_score(blk)
    assert x == 2.0 / 3.0 and n == 3 and ro == 0
    # a unit whose every element weighs 0 gives no proportion (never a division by zero)
    assert scale.unit_score({"score": 0.0, "elements": [{"score": 0.0, "weight": 0.0}]})[0] is None


def _case_and_row(monkeypatch, results):
    out = {"grade": "fail", "results": results}
    monkeypatch.setattr(graders, "grade", lambda result, case: out)
    case = {"id": "FLAT-t_L1-001-r1-a1", "grader": "x",
            "chain": {"id": "FLAT-t_L1-001", "kind": "flat", "element": "check", "pass_at": 0.5,
                      "family": "t", "level": 1, "part": "work/main", "ladder": "t_L1", "c": 1, "rung": 1,
                      "attempt": 1}}
    row = {"case_id": case["id"], "response": "r", "finish_reason": "stop", "chain": dict(case["chain"])}
    return case, row


def test_a_unit_whose_only_failures_weigh_zero_scores_one(monkeypatch):
    from crucibleforge import elemstats
    case, row = _case_and_row(monkeypatch, [{"id": "a", "pass": True, "weight": 2.0},
                                            {"id": "b", "pass": True},
                                            {"id": "contested", "pass": False, "weight": 0.0}])
    chains.stamp_verdict([row], case)
    blk = row["chain"]
    assert blk["score"] == 1.0 and blk["passed"]                         # the chain score
    assert [e["weight"] for e in blk["elements"]] == [2.0, 1.0, 0.0]     # kept on record
    assert scale.unit_score(blk)[0] == 1.0                               # the scale unit
    (u,) = scale.units([row])
    assert u["x"] == 1.0
    es = elemstats.elements([row])                                       # the element statistics
    assert sorted(e.rsplit("-c", 1)[1] for e in es) == ["a", "b"]


def test_a_missing_weight_still_counts_as_one(monkeypatch):
    case, row = _case_and_row(monkeypatch, [{"id": "a", "pass": True}, {"id": "b", "pass": False}])
    chains.stamp_verdict([row], case)
    assert row["chain"]["score"] == 0.5 and [e["weight"] for e in row["chain"]["elements"]] == [1.0, 1.0]
    assert scale.unit_score(row["chain"])[0] == 0.5


def test_regrade_element_weight_rechecks_current_rows_from_stored_replies(monkeypatch):
    # the fix is in the shared chain grading, so the chain revision does not move: the stored rows
    # read current and only the element_weight switch regrades them (no model call), writing only
    # the attempts whose verdict changes
    import types
    from crucibleforge import gen, regrade
    fam = types.SimpleNamespace(
        FAMILY="toyw0", LEVELS={1: {}},
        materialize=lambda g, *, release_seed, case_id: {"prompt": f"W0 {g['key']} {case_id}", "max_tokens": 64,
                                                         "temperature": 0.0, "grader": "contains",
                                                         "grader_config": {"needles": ["PASS"]}},
        reference=lambda g, **kw: "PASS", mutants=lambda g, **kw: ["nope"], grade=lambda a, m: 1.0)
    gen.register(fam)
    try:
        monkeypatch.setattr(graders, "grade", lambda result, case: {"grade": "fail", "detail": "d", "results": [
            {"id": "a", "pass": True}, {"id": "b", "pass": True}, {"id": "contested", "pass": False, "weight": 0.0}]})
        prof = {"name": "t", "cases": {"coding": []},
                "scores": {"work": {"parts": {"main": {"max": 10, "scoring": ["coding"], "cases": []}}}},
                "chains": {"work/main": {"flat": [{"family": "toyw0", "level": 1, "count": 1, "element": "check"}]}}}
        specs = {c["id"]: c for c in chains.chain_cases(prof)}
        inst = chains.instance(next(iter(specs.values())), 1, 1)
        case = chains.materialize(inst)
        row = {"case_id": inst["id"], "category": "coding", "prompt": case["prompt"], "system": "",
               "response": "PASS", "finish_reason": "stop", "repeat": 1, "chain": dict(inst["chain"])}
        chains.stamp_verdict([row], case)
        row["chain_revision"] = chains.revision(row["chain"])
        # as stored before the fix: the contested check weighed 1 and cost a third of the unit
        for e in row["chain"]["elements"]:
            e["weight"] = 1.0
        row["chain"].update(score=round(2 / 3, 4), passed=True)
        assert [r["status"] for r in regrade.plan([row], specs, "t")] == ["current"]
        (rec,) = regrade.plan([row], specs, "weight 0 stays 0", switch="element_weight")
        assert rec["status"] == "regrade" and rec["score_before"] == round(2 / 3, 4) and rec["score_after"] == 1.0
        new = rec["rows"][-1]
        assert [e["weight"] for e in new["chain"]["elements"]] == [1.0, 1.0, 0.0]
        assert new["chain_revision"] == row["chain_revision"] and new["regraded_by"]["switch"] == "element_weight"
        # a second pass finds the superseding rows current: idempotent
        assert [r["status"] for r in regrade.plan([new], specs, "t", switch="element_weight")] == ["current"]
    finally:
        gen.unregister("toyw0")
