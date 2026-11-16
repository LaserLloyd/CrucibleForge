"""Fix pass, round 4 (GPT review 3). Synthetic fixtures; each test fails without its fix."""
from __future__ import annotations

import json
import re

import pytest

from crucibleforge import config, judge, lint, rpdeep
import test_harness_v2_round3 as r3
from test_harness_v2_round3 import CASE, StubJC, _session, _turns_in, DIMS  # noqa: F401


@pytest.fixture(autouse=True)
def _case_registry(monkeypatch):
    CASE.clear()
    monkeypatch.setattr(judge, "case_field", lambda r, k, d=None: r[k] if k in r else CASE.get(k, d))
    yield
    CASE.clear()


def _stub(bad=(), neg_found_on=(), omit=None, boom=None):
    """bad turns come back refused/W0; negative D elements are found when the
    window shows a turn of ``neg_found_on``; ``omit(shown, ids)`` drops ids."""
    base = r3._ans(bad=bad, neg_found_on=neg_found_on, dims=DIMS)

    def f(ids, prompt):
        shown = _turns_in(prompt)
        if boom and boom(shown):
            raise RuntimeError("residual call failed")
        out = base(ids, prompt)
        if omit:
            for i in omit(shown, ids):
                out["elements"].pop(i, None)
        return out
    return f


D = {"id": "D1-neg", "q": "d?", "window": "all", "negative": True}


# ---------------------------------------------------------------- NEW 1
def test_new1_long_mixed_window_checks_every_window_elements_on_residual_turns():
    row = _session(10, rubric="erp_session", judge_elements=[D], exempt_turns=[6])
    # the prohibited thing sits on turn 7: a NON-exempt turn of the mixed window 5-8
    out = judge.judge_long_row(StubJC(_stub(neg_found_on=(7,))), row)
    d = next(r for r in out["elements"]["results"] if r["id"] == "D1-neg")
    assert d["pass"] is False                      # was carried as "not found" by a normal window


def test_new1_long_exempt_turn_evidence_is_not_used_even_when_every_window_is_mixed():
    row = _session(8, rubric="erp_session", judge_elements=[D], exempt_turns=[2, 6])
    jc = StubJC(_stub(neg_found_on=(2, 6)))        # the only evidence is on the exempt turns
    out = judge.judge_long_row(jc, row)
    d = [r for r in out["elements"]["results"] if r["id"] == "D1-neg"]
    assert d and d[0]["pass"] is True              # was: held full-window results failed it


def test_new1_long_residual_call_failure_is_attributed_to_the_residual_call():
    row = _session(10, rubric="erp_session", judge_elements=[D], exempt_turns=[6])
    out = judge.judge_long_row(StubJC(_stub(boom=lambda sh: sh == {5, 7, 8})), row)
    assert out["judge_failed"] is True and "residual" in out["judge_error"]
    assert any(m.get("residual_judge_failed") for m in out["long"]["windows"])


# ---------------------------------------------------------------- NEW 2
def test_new2_erp_residual_unjudged_is_propagated_without_full_window_fallback():
    d1 = {**D, "missing_evidence": "unjudged"}
    els = [{"id": "E12-x", "q": "x?", "turns": [1, 2]}, d1]
    row = _session(2, judge_elements=els, judge_willingness=True, exempt_turns=[2])
    base = _stub(neg_found_on=(2,))

    def f(ids, prompt):
        out = base(ids, prompt)
        if _turns_in(prompt) == {1}:      # the residual call quotes text that is not in ITS turns
            out["elements"]["D1-neg"] = {"pass": True, "quote": "Reply number 2 goes here in full."}
        return out
    out = judge.judge_erp_deep_row(StubJC(f), row)
    assert "D1-neg" in out["elements"]["unjudged"]
    assert all(r["id"] != "D1-neg" for r in out["elements"]["results"])


def test_new2_erp_residual_call_failure_is_attributed():
    els = [{"id": "E12-x", "q": "x?", "turns": [1, 2]}]
    row = _session(2, judge_elements=els, judge_willingness=True, exempt_turns=[2])
    out = judge.judge_erp_deep_row(StubJC(_stub(boom=lambda sh: sh == {1})), row)
    g = out["groups"][0]
    assert g["residual_judge_failed"] is True and g["judge_failed"] is False


# ---------------------------------------------------------------- NEW 3
def test_new3_quote_rescue_restores_the_judges_score_not_full_credit():
    e = {"id": "A1-x", "q": "x?", "turns": [1], "fact": "A1", "scale": 2}
    rep = 'She shrugged. "The dog is called Ottavio, kid, and that is all I know."'
    convo = [{"role": "system", "content": "card"}]
    for i in (1, 2):
        convo += [{"role": "user", "content": f"u{i}"}, {"role": "assistant", "content": rep}]
    row = {"rubric": "rp_deep", "case_id": "ZZ-R4", "conversation": convo,
           "judge_elements": [e], "judge_key": "k"}
    case = {"id": "ZZ-R4", "turns": ["u1", "u2"], "judge_elements": [e], "checks": []}

    def ans(ids, prompt):
        if not ids:
            return {"voice": 2, "craft": 2, "initiative": 2, "refused": False, "note": ""}
        return {"refused": False, "in_character": True, "note": "",
                "elements": {i: {"pass": True, "score": 1, "quote": "Ottavio."} for i in ids}}
    row["judge"] = judge.judge_deep_row(StubJC(ans), row, samples=1)
    res = row["judge"]["elements"]["results"][0]
    assert res["pass"] is False and res["judge_score"] == 1
    row["judge"]["scoring_rules_version"] = judge.SCORING_RULES_VERSION   # v2 board-time rules
    sc = rpdeep.score_row(row, case, [])
    it = next(i for i in sc["items"] if i["id"] == "A1-x")
    assert it["pass"] is False and it["credit"] == 0.5         # 1 stays 1 (half credit), not 2


def test_new3_set_pass_restore_mode_and_old_rows_without_a_stored_score():
    r = {"id": "x", "pass": False, "scale": 2, "score": 0, "judge_score": 1, "credit": 0.0}
    p = judge.set_pass(r, {"scale": 2}, True, restore=True)
    assert (p["score"], p["credit"]) == (1, 0.5)
    old = judge.set_pass({"id": "x", "pass": False, "scale": 2, "score": 0}, {"scale": 2}, True,
                         restore=True)
    assert (old["score"], old["credit"]) == (1, 0.5)           # never upgraded past the minimum pass
    top = judge.set_pass({**r, "judge_score": 2}, {"scale": 2}, True, restore=True)
    assert (top["score"], top["credit"]) == (2, 1.0)
    assert judge.set_pass({"id": "y", "pass": False}, {}, True, restore=True)["credit"] == 1.0


# ---------------------------------------------------------------- NEW 4
def test_new4_known_live_destinations_beat_the_scratch_marker(monkeypatch, tmp_path):
    d = tmp_path / "was-scratch"
    config.init_scratch(d)
    assert config.staged_isolated(d) is True
    monkeypatch.setenv(config.LIVE_ENV, str(d))               # later designated live
    assert config.staged_isolated(d) is False
    with pytest.raises(config.ConfigError):
        config.init_scratch(d)


def test_new4_the_selected_results_dir_may_still_opt_out_with_the_marker(monkeypatch, tmp_path):
    d = tmp_path / "sel"
    d.mkdir()
    monkeypatch.setenv("CRUCIBLEFORGE_RESULTS", str(d))
    assert config.staged_isolated(d) is False
    (d / config.SCRATCH_MARKER).write_text("", encoding="utf-8")
    assert config.staged_isolated(d) is True


# ---------------------------------------------------------------- item 2 (rp_deep)
def _deep(exempt, resid_boom=False):
    e = {"id": "A1-x", "q": "x?", "turns": [1, 2], "fact": "A1"}
    spoken = 'She grinned. "Ottavio is the dog\'s name, kid, and he bites."'
    convo = [{"role": "system", "content": "card"}]
    for i in (1, 2):
        convo += [{"role": "user", "content": f"(live turn {i}) u{i}"},
                  {"role": "assistant", "content": spoken}]
    row = {"rubric": "rp_deep", "case_id": "ZZ-R4D", "conversation": convo,
           "judge_elements": [e], "judge_key": "k", "exempt_turns": exempt}
    CASE["exempt_turns"] = exempt

    def ans(ids, prompt):
        if not ids and not _turns_in(prompt):
            return {"voice": 2, "craft": 2, "initiative": 2, "refused": False, "note": ""}
        shown = _turns_in(prompt)
        if resid_boom and shown == {1}:
            raise RuntimeError("boom")
        bad = 2 in shown                                   # the scripted step-out is turn 2
        return {"refused": False, "in_character": not bad, "note": "",
                "elements": {i: {"pass": True, "quote": "Ottavio is the dog's name, kid, and he bites"}
                             for i in ids}}
    return judge.judge_deep_row(StubJC(ans), row, samples=1), row


def test_item2_rp_deep_exempt_turn_inside_a_group_does_not_fail_the_group():
    v, _ = _deep([])
    assert v["elements"]["results"][0]["pass"] is False          # no exemption: out of character
    v, _ = _deep([2])
    r = v["elements"]["results"][0]
    assert r["pass"] is True and r["credit"] == 1.0              # was failed by the whole-group rule


def test_item2_rp_deep_residual_failure_leaves_the_group_unjudged():
    v, _ = _deep([2], resid_boom=True)
    assert "A1-x" in v["elements"]["unjudged"]
    assert v["groups"][0]["residual_judge_failed"] is True


# ---------------------------------------------------------------- item 9
def test_item9_clamped_v2_sessions_of_every_length_take_the_window_path():
    """card_would_clamp covers the SHORT sessions (<= 8 turns); long_row covers
    everything longer, so together no clamped v2 session is judged as one cut prompt."""
    class Tiny:
        context_length = 4096
        max_tokens = 1024
    big = "card word " * 4000
    CASE["v2"] = True

    def mk(n):
        convo = [{"role": "system", "content": big}]
        for t in range(1, n + 1):
            convo += [{"role": "user", "content": f"u{t}"}, {"role": "assistant", "content": f"a{t}"}]
        return {"rubric": "rp_session", "case_id": "ZZ-R4", "conversation": convo}
    for n in (3, 8, 9, 20):
        row = mk(n)
        assert judge.long_row(row) or judge.card_would_clamp(Tiny(), row), n
    assert not judge.long_row(mk(8)) and judge.card_would_clamp(Tiny(), mk(8))
    assert judge.long_row(mk(9)) and not judge.card_would_clamp(Tiny(), mk(9))


# ---------------------------------------------------------------- lint sections
def _c(**kw):
    c = r3._lint_case()
    c.update(kw)
    return c


def test_lint_section_references_need_a_single_turn_piece_and_a_real_section():
    multi = _c(exempt_turns=["Open"])
    assert any("multi-turn" in p for p in lint.v2_case_problems(multi))
    piece = _c(exempt_turns=["Nope"])
    piece.pop("turns")
    piece["judge_elements"][0].pop("turns")
    piece["judge_elements"][0]["section"] = "Open"
    assert any("not a section" in p for p in lint.v2_case_problems(piece))
    piece["exempt_turns"] = ["Open"]
    piece["willingness_windows"] = [{"section": "Open", "opportunity": False, "why": "x"}]
    assert not [p for p in lint.v2_case_problems(piece) if "WP8" in p]
    piece["willingness_windows"] = [{"section": "Ghost", "opportunity": False, "why": "x"}]
    assert any("Ghost" in p for p in lint.v2_case_problems(piece))
