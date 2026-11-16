"""TOOLWORLD (C5) family: reference at 1.0, floors, determinism, protocol parsing, revision pin."""
import hashlib
import inspect

import pytest

from crucibleforge import gen
from crucibleforge.gen import toolworld as tw

SEEDS = range(25)


def _key(level, s):
    return f"L{level}:{s:016x}"


@pytest.mark.parametrize("level", sorted(tw.LEVELS))
def test_reference_is_perfect(level):
    for s in SEEDS:
        sess = tw.run_policy(tw.instance(_key(level, s)), "reference")
        r = tw.console_result(sess)
        assert r["score"] == 1.0, (level, s, r["detail"])
        if tw.LEVELS[level]["budget"]:
            assert len(sess.world.calls) <= sess.inst.budget


@pytest.mark.parametrize("level", sorted(tw.LEVELS))
def test_floors(level):
    caps = {"no_tool": 0.0, "call_everything": 0.40 if level >= 2 else 1.0,
            "first_result": 0.40 if level >= 2 else 1.0, "ignore_calendar": 0.40 if level >= 2 else 1.0}
    for pol, cap in caps.items():
        mean = sum(tw.console_result(tw.run_policy(tw.instance(_key(level, s)), pol))["score"]
                   for s in SEEDS) / len(SEEDS)
        assert mean <= cap, (level, pol, mean)


def test_deterministic_and_registered():
    a = tw.instance("L4:00000000000000aa")
    b = tw.instance("L4:00000000000000aa")
    assert (a.system, a.task) == (b.system, b.task)
    assert gen.get("toolworld") is tw
    m = tw.materialize({"type": "toolworld", "level": 3}, release_seed="4.5.0", case_id="TW-L3-001")
    assert m["console"]["family"] == "toolworld" and m["prompt"]
    ref = tw.reference({"level": 3}, release_seed="4.5.0", case_id="TW-L3-001")
    assert tw.grade(ref, m) == 1.0
    for mut in tw.mutants({"level": 3}, release_seed="4.5.0", case_id="TW-L3-001"):
        assert tw.grade(mut, m) < 0.5


def test_parse_tolerates_bullets_quotes_fences():
    reply = '- CALL find_person {"name": "Ana"}\n> CALL `get_room` {"room_id": "R-1"}\n```\nCALL x {bad\n```'
    calls = tw.parse_calls(reply)
    assert [c[0] for c in calls] == ["find_person", "get_room", "x"]
    assert calls[2][1] is None


def test_time_patterns():
    p = tw._time_pats(14)
    for ok in ("14:00", "at 2 pm", "2pm", "2:00 PM", "14.00"):
        assert p.search(ok), ok
    assert not p.search("4:00")
    assert tw._time_pats(9).search("09:00") and tw._time_pats(9).search("9 a.m.")


def test_no_tool_ends_session_at_zero():
    sess = tw.Session(tw.instance("L2:0000000000000001"))
    sess.step_reply("All booked.")
    assert sess.state == "stopped" and tw.console_result(sess)["score"] == 0.0


# Revision pin: any change to the family's source must bump REVISION (rows of another revision are another era).
PINNED = {"tw-3": "77e22bf3df26"}


def test_revision_pin():
    src = inspect.getsource(tw)
    digest = hashlib.sha256(src.encode()).hexdigest()[:12]
    assert tw.REVISION in PINNED
    pin = PINNED[tw.REVISION]
    assert pin is None or pin == digest, f"toolworld source changed ({digest}); bump REVISION and the pin"


def test_grader_config_names_family_and_engine_replay_agrees():
    from crucibleforge import graders
    m = tw.materialize({"type": "toolworld", "level": 5}, release_seed="4.5.0", case_id="TW-L5-001")
    assert m["grader_config"]["family"] == "toolworld"
    ref = tw.reference({"level": 5}, release_seed="4.5.0", case_id="TW-L5-001")
    v = graders._grade_console(ref, m["grader_config"])
    assert v["rate"] == 1.0 and v["grade"] == "pass" and len(v["results"]) == m["meta"]["checks"]
    bad = tw.mutants({"level": 5}, release_seed="4.5.0", case_id="TW-L5-001")[2]   # first_result
    assert graders._grade_console(bad, m["grader_config"])["rate"] == tw.grade(bad, m) < 0.5


# Instance-content pin: what the model sees for these keys. The source pin above may move for non-content
# edits (grading metadata); this one must not move without a REVISION bump.
CONTENT_PIN = "a9fe5f16b3e3"


def test_instance_content_pin():
    h = hashlib.sha256()
    for level in sorted(tw.LEVELS):
        i = tw.instance(_key(level, 7))
        h.update((i.system + "\x00" + i.task + "\x00" + str(i.budget)).encode())
    digest = h.hexdigest()[:12]
    assert CONTENT_PIN is None or CONTENT_PIN == digest, f"instance content changed ({digest}): bump REVISION"


def test_profile_flat_set_runs_end_to_end(tmp_path, monkeypatch):
    """A profile line (flat set, element: check, concept/band tags, the session ceilings) runs the family
    through runner.run_models with a stub provider that plays the reference: every unit scores 1.0 and its
    per-check results are the unit's board elements."""
    from crucibleforge import chains, config, earlystop, providers, runner
    from crucibleforge.api import ChatResult
    prof = {"name": "tw-e2e", "cases": {"coding": []},
            "scores": {"work": {"parts": {"tools": {"max": 1, "scoring": ["coding"], "cases": []}}}},
            "chains": {"work/tools": {"flat": [
                {"family": "toolworld", "levels": {2: {"count": 1, "band": "low"}, 5: {"count": 1, "band": "mid"}},
                 "element": "check", "ceiling_per_turn": 16384, "history_budget": 49000, "ran_out": "exclude",
                 "concept": "C5", "category": "coding"}]}}}
    chains.check_gradeable(prof)
    # the stub: find the instance by its task text, then play the reference reply for this turn
    plays = {}
    for c in chains.chain_cases(prof):
        m = chains.materialize(chains.instance(c, 1, 1))
        key = m["console"]["key"]
        plays[m["prompt"]] = tw.run_policy(tw.instance(key), "reference").replies

    def chat(self, model_id, messages, **kw):
        task = next((x["content"] for x in messages if x["role"] == "user"), "")
        if task not in plays:                                  # the run-start probe, not an instance
            return ChatResult(response_text="395", finish_reason="stop", served_model=model_id,
                              completion_tokens=3)
        k = sum(1 for x in messages if x["role"] == "assistant")
        return ChatResult(response_text=plays[task][k], finish_reason="stop", served_model=model_id,
                          completion_tokens=5, prompt_tokens=20, reasoning_tokens=2, total_s=0.01)

    monkeypatch.setattr(config, "RESULTS_DIR", tmp_path)
    P = providers.Provider
    for name, fn in (("chat", chat), ("alive", lambda self: True), ("is_available", lambda self, mid: True),
                     ("switch_model", lambda self, mid, ctx=None: 1.0), ("live_context", lambda self, mid: None),
                     ("trained_context", lambda self, mid: None),
                     ("loaded_plan_for", lambda self, mid: {"parallel": 2}), ("workers", lambda self, mid: 2),
                     ("is_loaded", lambda self, mid: True), ("model_capabilities", lambda self, mid: set())):
        monkeypatch.setattr(P, name, fn)
    cfg = {"defaults": {"context_length": 73728, "repeats": {"coding": 1},
                        "early_stop": dict(earlystop.DEFAULTS, enabled=False)},
           "providers": {"p": {"type": "studioforge", "base_url": "http://p.invalid/v1"}},
           "judge": {"candidates": []}, "models": [],
           "_profile_case_ids": [c["id"] for c in chains.chain_cases(prof)]}
    runner.run_models(cfg, [{"name": "m", "model_id": "m", "provider": "p"}], chains.chain_cases(prof),
                      skip_conformance=True)
    rows = [r for r in config.load_transcripts("m") if r.get("chain")]
    assert len(rows) == 2, [r.get("error") for r in config.load_transcripts("m")]
    for r in rows:
        assert r["score"] == 1.0 and r["console"]["family"] == "toolworld", r.get("grade_detail")
        els = r["chain"]["elements"]
        assert len(els) in (8, 13) and all(e["score"] == 1.0 for e in els)
        assert all(e["id"].startswith(r["chain"]["id"] + "-c") for e in els)
    assert {r["chain"].get("band") for r in rows} == {"low", "mid"}
