"""PLAN-451-LIMITS step 1 (2026-11-25): thinking and answer no longer share one cap (profile block budgets_451,
crucibleforge/budgets451.py). The 4.5.0 keys, case files and chain blocks are untouched; the runner applies the
new numbers; a stored reply cut below its unit's new budget is owed again by a plain resume and still scores until
then."""
from __future__ import annotations

import copy

import pytest

from crucibleforge import attempts45, budgets451 as B, cli, profiles, runner

pytestmark = pytest.mark.live_ladder

SENT = {"instruct": 33536, "steer": 33536, "rp": 34768, "rpdeep": 34768, "nsfw": 34768, "story": 35768,
        "tooluse": 33792, "coding": 63488, "reasoning": 63488, "math": 63488}


@pytest.fixture
def prof(monkeypatch):
    monkeypatch.setattr(profiles, "_ACTIVE", [profiles._ACTIVE[0]])   # restored after the test
    profiles.set_active("bench45")
    return profiles.load_profile("bench45")


def test_the_formula_per_category(prof):
    b = prof[B.KEY]
    assert {c: B.formula(b, m) for c, m in prof["max_tokens"].items()} == SENT
    # the 4.5.0 keys stay as they were (they describe 4.5.0 rows)
    assert (prof["thinking_max_tokens_factor"], prof["thinking_max_tokens_floor"], prof["thinking_max_tokens_cap"],
            prof["bank_budget"], prof["thinker_context"]) == (4, 16384, 24576, 65536, 73728)


def test_ctx_budget_uses_the_formula_outside_rung_rounds(prof):
    class C:
        thinking, one_budget, think_factor, think_cap, think_floor = True, True, 4.0, 24576, 16384
        b451, rung = prof[B.KEY], None
    assert runner._Ctx.budget(C(), 768) == 33536 and runner._Ctx.budget(C(), 6144) == 63488
    C.rung = 65536
    C.think_cap = C.think_floor = 65536
    assert runner._Ctx.budget(C(), 768) == 65536                       # a rung keeps its own budget
    C.b451, C.rung, C.think_cap, C.think_floor = None, None, 24576, 16384
    assert runner._Ctx.budget(C(), 768) == 16384                       # no block: the 4.5.0 formula


def test_the_cq_override_leaves_the_case_untouched(prof):
    import json
    from pathlib import Path
    cfg, _cases = profiles.apply_profile(prof, {"defaults": {}, "providers": {}, "models": []})
    pack = json.loads((Path(__file__).resolve().parents[1] / "cases/ladder/work/code/classic-code.json")
                      .read_text(encoding="utf-8"))
    assert pack["budget"] == 65536                                       # the frozen pack file still says 65,536
    cq = [{"id": c["id"], "category": "coding", "prompt": "x", "ladder_budget": pack["budget"]}
          for c in pack["cases"] if isinstance(c, dict) and c["id"].startswith(("CQ1-", "CQ2-", "CQ3-"))]
    assert len(cq) == 3
    before = copy.deepcopy(cq)
    assert {runner.fixed_budget(cfg, c) for c in cq} == {131072}
    assert runner.wide_context(cfg, cq) == 131072 + runner.ESCALATION_CTX_PAD == 139264
    assert cq == before                                                  # the case dicts (and hashes) unchanged
    cl = {"id": "CL7-span-by-example", "category": "coding", "prompt": "x", "ladder_budget": 65536}
    assert runner.fixed_budget(cfg, cl) == 65536                         # CL keeps its own budget


def test_chain_ceilings_map_by_value(prof):
    b = prof[B.KEY]
    assert (B.ceiling(b, 32768), B.ceiling(b, 16384), B.ceiling(b, 21845), B.ceiling(b, None)) == (49152, 32768,
                                                                                                    21845, None)


def test_rows_carry_the_revision_and_a_route_clamp(prof):
    class C:
        b451, rung, ctx_len, max_output = prof[B.KEY], None, None, 65536

        def budget(self, mt):
            return B.formula(self.b451, mt)
    cq = {"id": "CQ2-regex-engine-from-scratch", "category": "coding", "prompt": "x", "budget": 65536,
          "max_tokens": 6144}
    st = runner.budget_stamp(C(), cq, 6144)
    assert st["budget_rev"] == B.REV and "case budget" in st["budget_reason"]
    assert st["budget_clamp"] == {"route_max": 65536, "wanted": 131072, "granted": 65536, "source": "registry"}
    plain = runner.budget_stamp(C(), {"id": "I1", "category": "instruct", "prompt": "x"}, 768)
    assert "formula" in plain["budget_reason"] and "budget_clamp" not in plain


def _cut(**k):
    return {"profile": "bench45", "case_id": "I1", "repeat": 1, "category": "instruct", "finish_reason": "length",
            "max_tokens_sent": 16384, "response": "", "reasoning": "r", "grade": "fail", **k}


def test_only_old_budget_cut_rows_are_superseded(prof):
    assert attempts45.superseded(_cut()) == "budget raised"
    assert attempts45.superseded(_cut(finish_reason="stop")) is None                    # finished: kept
    assert attempts45.superseded(_cut(budget_rev=2)) is None                            # cut under the new budget
    assert attempts45.superseded(_cut(max_tokens_sent=33536)) is None                   # already at the new budget
    assert attempts45.superseded(_cut(profile="no-such-profile")) is None               # no block: nothing owed
    assert attempts45.superseded(_cut(category="codebank", max_tokens_sent=65536)) is None      # bank unchanged
    assert attempts45.superseded(_cut(category="codebank", max_tokens_sent=62700)) == "budget raised"  # was clamped
    assert attempts45.superseded(_cut(chain={"ceiling": 16384}, max_tokens_sent=16384)) == "budget raised"
    assert attempts45.superseded(_cut(budget_rung=65536)) is None                       # escalation rows: never
    assert not attempts45.is_infra(_cut())                                              # still scores until re-run


def test_a_cq_unit_reruns_whole(prof):
    cfg = {"defaults": {"repeats": {"coding": 1}}}
    cases = [{"id": "CQ1-format-spec-from-scratch", "category": "coding"}, {"id": "C1-x", "category": "coding"}]
    monkey = {"CQ1-format-spec-from-scratch": 2}
    rows = [_cut(case_id="CQ1-format-spec-from-scratch", category="coding", max_tokens_sent=65536),
            {**_cut(case_id="CQ1-format-spec-from-scratch", category="coding", max_tokens_sent=65536), "repeat": 2,
             "finish_reason": "stop"},
            {**_cut(case_id="C1-x", category="coding", max_tokens_sent=63488, budget_rev=2)}]
    import crucibleforge.config as config
    orig = config.repeats_for
    try:
        config.repeats_for = lambda c, cat, smoke=False, case=None: monkey.get((case or {}).get("id"), 1)
        owed = cli._first_reply_jobs("m", cfg, cases, rows=rows)
    finally:
        config.repeats_for = orig
    assert owed == {(None, "CQ1-format-spec-from-scratch", 1), (None, "CQ1-format-spec-from-scratch", 2)}


def test_the_plan_says_why_each_job_is_owed():
    assert cli.owed_reason([]) == "not run"
    assert cli.owed_reason([_cut()]) == "budget raised"
    assert cli.owed_reason([{"error_kind": "ctx_short", "grade": "error", "error": "ctx_short: x"}]) == "context"
    assert cli.owed_reason([{"error_kind": "transport", "grade": "error",
                             "error": "no data event for 600s, only keep-alive comments"}]) == "stall"
    assert cli.owed_reason([{"error_kind": "transport", "grade": "error", "error": "refused"}]) == "transport"


def test_a_route_maximum_clamps_and_is_read_only_from_openrouter():
    from crucibleforge import providers
    p = providers.Provider(name="or", type="openai", base_url="https://openrouter.ai/api/v1")
    p._route_max, p._route_max_read = {"vendor/m": 65536}, True        # as the /models listing recorded it
    assert p.route_max_output("vendor/m") == 65536 and p.route_max_output("vendor/other") is None
    ctx = runner._Ctx({"defaults": {}}, {"name": "m", "model_id": "vendor/m", "thinking": True}, p, 8192)
    assert ctx.max_output == 65536                                       # the registry named none: the route's own
    ctx = runner._Ctx({"defaults": {}}, {"name": "m", "model_id": "vendor/m", "thinking": True,
                                          "max_output_tokens": 32000}, p, 8192)
    assert ctx.max_output == 32000                                       # the registry wins
    rig = providers.Provider(name="rig", type="openai", base_url="http://192.168.200.7:1234/v1")  # scrub-ok: made-up test address
    assert rig.route_max_output("vendor/m") is None


def test_a_rung_above_the_output_maximum_is_not_offered():
    cfg = {"defaults": {"escalation_rungs": [65536, 131072], "thinking_max_tokens_cap": 24576}}
    assert runner.escalation_rungs(cfg) == [65536, 131072]
    assert runner.escalation_rungs(cfg, 65536) == [65536]          # a route capped at 65,536: no 131,072 label


def test_the_plan_counts_owed_chain_attempts_by_reason(monkeypatch):
    glued = {"role": "assistant", "content": 'Looking.CALL get_person {"person_id": "P-1"}'}
    rows = [{**_cut(case_id="FLAT-x", chain={"ceiling": 16384, "rung": 1, "attempt": 1}), "bench_run_id": "b"},
            {"case_id": "FLAT-t", "repeat": 1, "bench_run_id": "b", "grade": "fail", "score": 0.0,
             "response": "x", "finish_reason": "stop", "chain": {"rung": 1, "attempt": 1},
             "console": {"family": "toolworld"},
             "tool_conversation": [{"role": "system", "content": "- get_person {\"person_id\": \"P-1\"}"}, glued]}]
    monkeypatch.setattr(cli, "_resume_rows", lambda name, cfg: rows)
    monkeypatch.setattr(cli, "_stored_mismatch", lambda *a, **k: None)
    monkeypatch.setattr(cli, "_first_reply_jobs", lambda *a, **k: set())
    got = cli.owed_lines({"name": "m"}, {}, [], False)
    assert got[0] == "  owed on resume: 2 job(s): budget raised 1, tool tolerance 1"
    assert got[1].startswith("  owed price: 2 row(s), ")             # review 3: the owed rows are priced
