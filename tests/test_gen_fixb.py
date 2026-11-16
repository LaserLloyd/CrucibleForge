"""Review fix-b: engine == family grade (tracevm, every family), ISTACK/SESSION-STATE lazy answers,
release seed from the profile, case_hash covers generator output, plan cache isolation, ledger thinking."""
import copy

import pytest

from crucibleforge import gen, graders
from crucibleforge.api import ChatResult
from crucibleforge.gen import istack, session_state, tracevm

GENERIC = ("The evening was quiet and the house was calm. She walked to the window and looked out at the "
           "garden. Nothing much happened, and that was fine with everyone involved today, really.")


def _engine(case, text):
    v = graders.grade(ChatResult(response_text=text, finish_reason="stop", served_model="m"), case)
    for k in ("score", "rate"):
        if v.get(k) is not None:
            return float(v[k])
    return 1.0 if v["grade"] == "pass" else 0.0


# ---------------------------------------------------------------- H3
@pytest.mark.parametrize("lv", [1, 3, 5])
def test_tracevm_engine_equals_family_grade(lv):
    G = {"type": "tracevm", "level": lv, "key": "a"}
    kw = dict(release_seed="fb", case_id="TV-fb")
    m = tracevm.materialize(G, **kw)
    exp = m["expected"]
    wrong = list(exp)
    wrong[0] = "zzz"
    fence = lambda lines: "```text\n" + "\n".join(lines) + "\n```"  # noqa: E731
    answers = {
        "ref": fence(exp),
        "draft_right_final_wrong": fence(exp) + "\n\nOn reflection:\n" + fence(wrong),
        "draft_wrong_final_right": fence(wrong) + "\n\nFinal:\n" + fence(exp),
        "unfenced": "\n".join(exp),
        "output_fence": "```output\n" + "\n".join(exp) + "\n```",
        "shotgun": fence(wrong) + "\n" + fence(exp[:1] + ["q"]) + "\n" + fence(wrong),
        "empty": "",
    }
    for name, a in answers.items():
        assert _engine(m, a) == pytest.approx(tracevm.grade(a, m)), name
    assert _engine(m, answers["draft_right_final_wrong"]) == 0.0
    assert _engine(m, answers["unfenced"]) == 1.0 and _engine(m, answers["output_fence"]) == 1.0


def test_verify_case_checks_the_engine_path_for_every_family():
    case = {"id": "TV-vc", "category": "reasoning", "generator": {"type": "tracevm", "level": 2}}
    assert gen.verify_case(case, "fb") == []

    class Broken:                      # a family whose checks disagree with its grade
        FAMILY = "broken_fb"
        LEVELS = {1: {}}
        materialize = staticmethod(lambda g, *, release_seed, case_id: {
            "prompt": "p", "grader": "checks", "grader_config": {"checks": [
                {"id": "c", "type": "regex", "pattern": "x"}]}})
        reference = staticmethod(lambda g, *, release_seed, case_id: "x")
        mutants = staticmethod(lambda g, *, release_seed, case_id: ["y"])
        grade = staticmethod(lambda a, m: 1.0 if a == "x" else (0.5 if a == "y" else 0.0))
    gen.register(Broken)
    try:
        errs = gen.verify_case({"id": "B", "category": "reasoning", "generator": {"type": "broken_fb", "level": 1}}, "fb")
    finally:
        gen.unregister("broken_fb")
    assert any("engine" in e for e in errs), errs


# ---------------------------------------------------------------- H4
@pytest.mark.parametrize("fam,variants", [(istack, ("write", "conv")), (session_state, ("state", "multi"))])
@pytest.mark.parametrize("level", [1, 2, 3, 4, 5])
def test_generic_paragraph_scores_low(fam, variants, level):
    from crucibleforge.gen._stack_common import join_turns
    for variant in variants:
        for seed in range(30):
            g = {"type": fam.FAMILY, "level": level, "variant": variant, "key": str(seed)}
            m = fam.materialize(g, release_seed="fb", case_id=f"X{seed}")
            ans = join_turns([GENERIC] * len(m["turns"]))
            assert fam.grade(ans, m) < 0.2, (variant, level, seed)
            assert fam.grade(fam.reference(g, release_seed="fb", case_id=f"X{seed}"), m) == 1.0


def test_istack_every_turn_has_a_topic_bound_positive_rule():
    for variant, n in (("write", 1), ("conv", 3)):
        m = istack.materialize({"type": "istack", "level": 1, "variant": variant}, release_seed="fb", case_id="T")
        topics = [c for c in m["checks"] if c["note"] == "topic"]
        assert sorted(c["turns"][0] for c in topics) == list(range(1, n + 1))
        for c, t in zip(topics, m["turns"]):
            assert c["pattern"].strip("\\b") in t.lower() or c["pattern"].strip("\\b") in m["system"].lower()


# ---------------------------------------------------------------- L1
@pytest.mark.parametrize("fam", [istack, session_state])
def test_cached_plan_is_not_shared(fam):
    g = {"type": fam.FAMILY, "level": 2}
    a = fam.materialize(copy.deepcopy(g), release_seed="fb", case_id="L1")
    ref = fam.reference(copy.deepcopy(g), release_seed="fb", case_id="L1")
    a["turns"].append("junk")
    a["checks"].clear()
    b = fam.materialize(copy.deepcopy(g), release_seed="fb", case_id="L1")
    assert "junk" not in b["turns"] and b["checks"]
    assert fam.grade(ref, b) == 1.0


# ---------------------------------------------------------------- H5
def _two_profiles(tmp_path, monkeypatch):
    from crucibleforge import profiles
    d = tmp_path / "profiles"
    d.mkdir()
    for name, seed in (("p_a", ""), ("p_b", "release_seed: 9.9.9\n"), ("p_c", "release_seed: 7.7.7\n")):
        (d / f"{name}.yaml").write_text(f"{seed}cases:\n  coding: [X]\n", encoding="utf-8")
    monkeypatch.setattr(profiles, "_dirs", lambda cfg: [d])
    return profiles


def test_profile_release_seed_reaches_the_generator(tmp_path, monkeypatch):
    from crucibleforge import version
    profiles = _two_profiles(tmp_path, monkeypatch)
    monkeypatch.delenv("CRUCIBLEFORGE_RELEASE_SEED", raising=False)
    case = {"id": "KF-h5", "category": "coding", "generator": {"type": "keyfunc", "level": 1, "key": "core"}}
    gen.set_release_seed(None)
    try:
        profiles.activate_release_seed(profiles.load_profile("p_a"))
        base = gen.materialize_case(copy.deepcopy(case))["prompt"]
        h0 = version._content_view(case)
        profiles.activate_release_seed(profiles.load_profile("p_b"))
        assert gen.release_seed() == "9.9.9"
        assert gen.materialize_case(copy.deepcopy(case))["prompt"] != base
        assert version._content_view(case) != h0
        # resetting between profiles in one process: a profile without the key goes back to the default
        profiles.activate_release_seed(profiles.load_profile("p_a"))
        assert gen.release_seed() == gen.DEFAULT_RELEASE_SEED or gen.release_seed() == gen._profile_seed()
        assert version._content_view(case) == h0
    finally:
        gen.set_release_seed(None)


def test_loading_a_profile_never_pins_and_env_wins(tmp_path, monkeypatch):
    profiles = _two_profiles(tmp_path, monkeypatch)
    monkeypatch.delenv("CRUCIBLEFORGE_RELEASE_SEED", raising=False)
    gen.set_release_seed(None)
    try:
        before = gen.release_seed()
        for n in ("p_a", "p_b", "p_c"):          # a GUI-style listing / judge layering
            profiles.load_profile(n)
        assert gen.release_seed() == before
        profiles.activate_release_seed(profiles.load_profile("p_b"))
        profiles.load_profile("p_c")             # loading another does not re-pin
        assert gen.release_seed() == "9.9.9"
        monkeypatch.setenv("CRUCIBLEFORGE_RELEASE_SEED", "4.5.0")
        assert gen.release_seed() == "4.5.0"     # explicit env beats the active profile
        monkeypatch.delenv("CRUCIBLEFORGE_RELEASE_SEED")
        assert gen.release_seed() == "9.9.9"
    finally:
        gen.set_release_seed(None)


# ---------------------------------------------------------------- M1
def test_case_hash_follows_generator_output(monkeypatch):
    from crucibleforge import version
    case = {"id": "TV-m1", "category": "reasoning", "generator": {"type": "tracevm", "level": 2}}
    gen.set_release_seed("m1")
    try:
        a = version._content_view(case)
        assert a == version._content_view(case)                     # stable
        orig = tracevm._spec
        monkeypatch.setattr(tracevm, "_spec", lambda program, level: orig(program, level) + "\nBe exact.")
        assert version._content_view(case) != a                     # generator output changed
    finally:
        gen.set_release_seed(None)


def test_case_hash_follows_family_spec_text(monkeypatch):
    from crucibleforge import version
    case = {"id": "IS-m1", "category": "rpdeep", "generator": {"type": "istack", "level": 1}}
    gen.set_release_seed("m1")
    try:
        a = version._content_view(case)
        monkeypatch.setattr(istack, "_LAYOUT_NOTE", istack._LAYOUT_NOTE + " Be kind.")
        istack._plan_cached.cache_clear()
        assert version._content_view(case) != a
    finally:
        istack._plan_cached.cache_clear()
        gen.set_release_seed(None)


# ---------------------------------------------------------------- L3
def test_ledger_grade_ignores_thinking_like_the_engine():
    from crucibleforge.gen import ledger
    m = ledger.materialize({"type": "ledger", "level": 2}, release_seed="l3", case_id="L-1")
    ref = ledger.reference({"type": "ledger", "level": 2}, release_seed="l3", case_id="L-1")
    answer = "<think>\n" + ref + "\n</think>\nI am not sure."
    assert ledger.grade(answer, m) == pytest.approx(_engine(m, answer)) == 0.0
    assert ledger.grade("<think>x</think>" + ref, m) == 1.0
