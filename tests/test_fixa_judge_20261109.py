"""4.5 fix pass A (REVIEW-45-WP): judge-side findings F2 F3 F5 F10 F13 F17 F21.

Every test here fails on 45b6b83 (checked once, see the fix-A report)."""
from __future__ import annotations

import json
import threading
from argparse import Namespace

import pytest

from crucibleforge import cli, config, judge, shard
from crucibleforge.api import ChatResult

from tests.test_run1_fixes_20260924 import _fake_jc, _verdicts


# --------------------------------------------------------------- F5 + F17
def test_bench_canary_keeps_the_4_1_probe_and_gate_of_four():
    text, floor, sha = judge.canary_explicit(False)
    assert (floor, sha) == (4, None)
    assert text == judge._canary_explicit_text() and text != judge.CANARY_EXPLICIT


def test_bench_local_override_still_wins_on_the_bench_path(tmp_path):
    p = tmp_path / "x.local.txt"
    assert judge._canary_explicit_text(p) == judge.CANARY_EXPLICIT_DEFAULT
    p.write_text("A stronger local paragraph.\n", encoding="utf-8")
    assert judge._canary_explicit_text(p) == "A stronger local paragraph."


def test_canary_gate_is_scoped_per_profile():
    jc, _ = _fake_jc(_verdicts(explicitness=4))
    info = judge.run_canary(jc, public=False)          # bench: >= 4 passes
    assert info == {"public": False, "explicit_min": 4}
    jc, _ = _fake_jc(_verdicts(explicitness=4))
    with pytest.raises(judge.JudgeError, match=r"\(<5\)"):
        judge.run_canary(jc, public=True)              # bench45: >= 5
    jc, _ = _fake_jc(_verdicts(explicitness=5))
    assert judge.run_canary(jc, public=True)["explicit_min"] == 5


def test_profiles_declare_their_canary():
    assert judge.judge_policy_config(None, "bench45")["public_canary"] is True
    assert judge.judge_policy_config(None, "bench")["public_canary"] is False


def test_public_canary_sha_is_checked_at_runtime_and_recorded(tmp_path, monkeypatch):
    jc, _ = _fake_jc(_verdicts(explicitness=8))
    info = judge.run_canary(jc, public=True)
    assert info["explicit_sha256"] == judge.CANARY_EXPLICIT_SHA256
    edited = tmp_path / "canary_explicit.txt"
    edited.write_text(judge.CANARY_EXPLICIT + " edited", encoding="utf-8")
    monkeypatch.setattr(judge, "CANARY_EXPLICIT_FILE", edited)
    with pytest.raises(judge.JudgeError, match="sha256"):
        judge.run_canary(_fake_jc(_verdicts())[0], public=True)
    # the 4.1 path does not look at the public file at all
    assert judge.run_canary(_fake_jc(_verdicts(explicitness=4))[0], public=False)["public"] is False


# ------------------------------------------------------------------ F3
def _steer_rows(label):
    el = [{"id": "e1", "q": "q?", "dim": "obeyed"}]
    return [{"bench_run_id": "r1", "case_id": f"S-{prof}", "repeat": 1, "turn": None,
             "rubric": "steer", "needs_judge": True, "prompt": "p", "profile": prof,
             "judge_elements": el, "response": f"{label} text for {prof}"}
            for prof in ("bench", "bench45")]


def _judge_setup(tmp_path, monkeypatch, seen):
    monkeypatch.setattr(config, "RESULTS_DIR", tmp_path)
    (tmp_path / "transcripts_m1.jsonl").write_text(
        "\n".join(json.dumps(r) for r in _steer_rows("m1")) + "\n", encoding="utf-8")

    class Prov:
        type, name = "openai", "fake"

    class JC:
        model_id = serve_id = label = "J"
        concurrency, thinking, provider = 4, False, Prov()
        instance = None

    monkeypatch.setattr(judge, "_load_judge", lambda *a, **k: JC())
    monkeypatch.setattr(judge, "run_canary", lambda jc, **k: {"public": k.get("public")})
    monkeypatch.setattr(judge, "_private_case_ids", lambda cfg: set())

    def jr(jc, row, samples=1):
        seen.append((row["profile"], dict(row["_judge_policy"])))
        return {"judge_failed": False, "judge_raw": "{}", "refused": False, "scores": {}}
    monkeypatch.setattr(judge, "judge_row", jr)


@pytest.mark.parametrize("phase", ["bench", "bench45"])
def test_each_row_is_judged_under_its_own_profiles_policy(tmp_path, monkeypatch, phase):
    seen = []
    _judge_setup(tmp_path, monkeypatch, seen)
    cfg = {"models": [{"name": "m1", "model_id": "m1", "provider": "fake"}],
           "judge": {"samples": 1}, "providers": {}, "defaults": {}, "_profile": phase}
    out = judge.run_judge(cfg, ["m1"])
    assert out["judged"] == 2
    kinds = {prof: pol["kind"] for prof, pol in seen}
    # whatever the phase profile; bench45 is element-mode (`mode: elements`) since PLAN-45-v4 s5
    assert kinds == {"bench": "binary", "bench45": "elements"}
    rows = {r["profile"]: r for r in config.load_transcripts("m1")}
    for prof, r in rows.items():
        assert r["judge"]["policy"]["profile"] == prof
    # a public canary is required as soon as one bench45 row is pending
    assert out["canary"]["public"] is True


def test_a_bench_only_phase_runs_the_bench_canary(tmp_path, monkeypatch):
    seen = []
    _judge_setup(tmp_path, monkeypatch, seen)
    rows = [r for r in _steer_rows("m1") if r["profile"] == "bench"]
    (tmp_path / "transcripts_m1.jsonl").write_text(json.dumps(rows[0]) + "\n", encoding="utf-8")
    cfg = {"models": [{"name": "m1", "model_id": "m1", "provider": "fake"}],
           "judge": {"samples": 1}, "providers": {}, "defaults": {}, "_profile": "bench45"}
    assert judge.run_judge(cfg, ["m1"])["canary"]["public"] is False


def test_policy_profile_mismatch_is_outdated_for_every_profile():
    for prof in ("bench", "bench45", "lite"):
        other = "bench" if prof != "bench" else "bench45"
        r = {"needs_judge": True, "profile": prof, "case_id": "X", "rubric": "steer",
             "judge": {"judge_failed": False, "policy": {"profile": other, "samples": 1}}}
        assert judge.verdict_outdated(r) == "policy" and judge.needs_verdict(r)
        r["judge"]["policy"]["profile"] = prof
        assert judge.verdict_outdated(r) != "policy"
    # verdicts from before the stamp are not flagged
    assert judge.verdict_outdated({"profile": "bench45", "judge": {"policy": {"samples": 1}}}) != "policy"


# ------------------------------------------------------------------ F10
# These F-tests pin the CLASSED mechanism (element classes, OOC evidence, craft chunks).
# bench45 later added `mode: elements` (PLAN-45-v4 section 5, E3), which supersedes it
# for live rows; the mechanism is unchanged, so it is tested with the mode cleared.
BENCH45 = {**judge.judge_policy_config(None, "bench45"), "mode": None}
BENCH = judge.judge_policy_config(None, "bench")


def test_bench45_classed_branch_beats_the_41_row_lists():
    row = {"case_id": "NC2-after-hours", "rubric": "steer",
           "judge_elements": [{"id": "x", "q": "q"}]}
    assert not BENCH45["samples3_rows"] and not BENCH45["samples1_rows"]   # lists dropped
    assert "NC2-after-hours" in BENCH["samples3_rows"]
    p = judge.judge_policy(row, BENCH45)
    assert (p["kind"], p["samples"]) == ("classed", 1)           # classed decides
    old = judge.judge_policy(row, BENCH)
    assert (old["kind"], old["samples"]) == ("override", 3)       # 4.1 unchanged


def test_bench45_turns_on_fact_windows():
    assert BENCH45["group_facts"] == "turn_window" and BENCH45["group_window"] == 4
    assert BENCH["group_facts"] is None


class _CountJC:
    thinking, context_length, max_tokens, row_timeout_s = False, 16384, 1024, None
    sample_temperature, extra_body, concurrency = 0.7, None, 4

    def __init__(self, reply):
        self.reply, self.calls, self.kw, self.lock = reply, 0, [], threading.Lock()

    def chat(self, messages, **kw):
        with self.lock:
            self.calls += 1
            self.kw.append(kw)
        prompt = messages[-1]["content"] if len(messages) > 1 else ""
        full = " ".join(m["content"] for m in messages)
        ids = [i for i in ("fa", "fb", "fc", "fd") if f'"{i}"' in full or f"{i}:" in full or f" {i} " in full]
        body = self.reply(full, ids)

        class R:
            finish_reason, completion_tokens, reasoning_tokens, reasoning_text = "stop", 1, 0, ""
            response_text = json.dumps(body)
        return R()


def _deep_row(policy, elements, turns=4):
    conv = []
    for t in range(1, turns + 1):
        conv += [{"role": "user", "content": f"u{t}"}, {"role": "assistant", "content": f"reply number {t}"}]
    return {"rubric": "rp_deep", "case_id": "D1", "model_label": "m", "judge_key": "k",
            "conversation": conv, "judge_elements": elements, "_judge_policy": policy}


def _all_no(full, ids):
    return {"refused": False, "in_character": True, "note": "",
            "elements": {i: {"pass": False, "quote": ""} for i in ids}}


def test_budget_calls_equal_the_engines_calls_on_a_synthetic_row():
    els = [{"id": "fa", "q": "q", "turns": [1]}, {"id": "fb", "q": "q", "turns": [2]},
           {"id": "fc", "q": "q", "turns": [3], "class": "craft"}, {"id": "fd", "q": "q", "turns": [8]}]
    case = {"id": "D1", "rubric": "rp_deep", "judge_elements": els}
    exp = judge.expected_calls(case, BENCH45, ooc_rate=0.0)
    assert exp["groups"] == 2 and exp["kind"] == "flag-only"
    # window 4: turns 1-3 merge (craft class in it -> 3 samples, sequential = 2 asked), turn 8 alone (1)
    assert (exp["min"], exp["max"]) == (3, 4 + 2) and exp["calls"] == pytest.approx(2.25 + 1)
    pol = judge.judge_policy({"case_id": "D1", "rubric": "rp_deep", "judge_elements": els}, BENCH45)
    jc = _CountJC(_all_no)
    judge.judge_deep_row(jc, _deep_row(pol, els, turns=8), samples=1)
    assert jc.calls == exp["min"]               # no split -> the engine draws exactly the minimum
    # the 4.1 policy on the same case: one call per distinct turn set (4)
    assert judge.expected_calls(case, BENCH)["calls"] == 4


# ------------------------------------------------------------------ F2
OOC_REPLY = {"refused": True, "in_character": False, "note": "", "ooc_quote": "As an AI model"}


def _reply_ooc(full, ids):
    return {**OOC_REPLY, "elements": {i: {"pass": False, "quote": ""} for i in ids}}


def test_group_meta_carries_the_judges_refusal_and_ooc_evidence():
    els = [{"id": "fa", "q": "q", "turns": [1]}]
    pol = {**judge.judge_policy({"case_id": "D1", "rubric": "rp_deep", "judge_elements": els}, BENCH45)}
    assert pol["ooc_evidence"] is True
    jc = _CountJC(_reply_ooc)
    out = judge.judge_deep_row(jc, _deep_row(pol, els, turns=1), samples=1)
    g = out["groups"][0]
    assert g["refused"] is True and g["ooc_quote"] == "As an AI model"
    assert g["in_character_votes"] == [False]
    rf = jc.kw[0]["response_format"]["json_schema"]["schema"]
    assert "ooc_quote" in rf["properties"] and "ooc_quote" in rf["required"]


def test_41_policy_stores_and_asks_nothing_new():
    els = [{"id": "fa", "q": "q", "turns": [1]}]
    pol = judge.judge_policy({"case_id": "D1", "rubric": "rp_deep", "judge_elements": els}, BENCH)
    assert "ooc_evidence" not in pol
    jc = _CountJC(_reply_ooc)
    out = judge.judge_deep_row(jc, _deep_row(pol, els, turns=1), samples=1)
    assert not {"refused", "ooc_quote", "in_character_votes"} & set(out["groups"][0])
    rf = jc.kw[0]["response_format"]["json_schema"]["schema"]
    assert "ooc_quote" not in rf["properties"]


def test_in_character_votes_hold_one_entry_per_sample():
    els = [{"id": "fa", "q": "q", "turns": [1], "class": "craft"}]
    pol = judge.judge_policy({"case_id": "D1", "rubric": "rp_deep", "judge_elements": els}, BENCH45)
    votes = iter([False, False, True])
    seq = lambda full, ids: {"refused": False, "in_character": next(votes), "note": "",
                             "ooc_quote": "x", "elements": {i: {"pass": False, "quote": ""} for i in ids}}
    out = judge.judge_deep_row(_CountJC(seq), _deep_row(pol, els, turns=1), samples=1)
    assert out["groups"][0]["in_character_votes"] == [False, False]      # sequential: 2 agreed, no 3rd


# ------------------------------------------------------------------ F21
def test_classed_craft_chunks_draw_the_craft_class_count(monkeypatch):
    seen = []

    def fake_call(jc, sub, samples, sem, policy=None):
        seen.append((sub.get("rubric"), samples, dict(policy or {})))
        return {"judge_failed": False, "refused": False, "scores": {"in_character": True,
                "voice": 7, "craft": 7, "initiative": 7},
                "elements": {"results": [{"id": e["id"], "pass": True, "quote": "x", "type": "judge",
                                          "judge_pass": True, "detail": ""}
                                         for e in sub.get("judge_elements") or []]}}
    monkeypatch.setattr(judge, "_deep_call", fake_call)
    monkeypatch.setattr(judge, "DEEP_CRAFT", True)
    els = [{"id": "fa", "q": "q", "turns": [1]}]            # fact elements only
    pol = judge.judge_policy({"case_id": "D1", "rubric": "rp_deep", "judge_elements": els}, BENCH45)
    judge.judge_deep_row(_CountJC(_all_no), _deep_row({**pol}, els, turns=4), samples=1)
    craft = [(s, p) for r, s, p in seen if r == "rp_deep_craft"]
    fact = [s for r, s, p in seen if r != "rp_deep_craft"]
    assert craft and all(s == 3 and p.get("sequential") for s, p in craft)
    assert fact == [1]


# ------------------------------------------------------------------ F13
def test_a_held_label_is_not_stamped_and_the_command_fails(tmp_path, monkeypatch):
    seen = []
    monkeypatch.setattr(config, "RESULTS_DIR", tmp_path)
    for l in ("m1", "m2"):
        rows = [r for r in _steer_rows(l) if r["profile"] == "bench"]
        (tmp_path / f"transcripts_{l}.jsonl").write_text(json.dumps(rows[0]) + "\n", encoding="utf-8")
        (tmp_path / f"meta_{l}.json").write_text(json.dumps({"started": "t0", "judge_error": "old"}),
                                                  encoding="utf-8")
    _judge_setup_patches(monkeypatch, seen)
    other = shard.LabelLocks(tmp_path)
    other.acquire(["m2"])
    try:
        args = Namespace(force_evict=False, judge=None, instance=None, force=False, cmd="judge",
                         judge_fallback=None, allow_self_judge=False, judge_stop=None,
                         judge_deadline=None)
        cfg = {"models": [{"name": l, "model_id": l, "provider": "fake"} for l in ("m1", "m2")],
               "judge": {"samples": 1}, "providers": {}, "defaults": {}}
        rc = cli._cmd_judge_locked(args, cfg, ["m1", "m2"], None, False)
    finally:
        other.release()
    assert rc != 0
    m1 = json.loads((tmp_path / "meta_m1.json").read_text(encoding="utf-8"))
    m2 = json.loads((tmp_path / "meta_m2.json").read_text(encoding="utf-8"))
    assert "judged" in m1 and "judge_error" not in m1
    assert "judged" not in m2 and m2["judge_error"] == "old"


def _judge_setup_patches(monkeypatch, seen):
    class Prov:
        type, name = "openai", "fake"

    class JC:
        model_id = serve_id = label = "J"
        concurrency, thinking, provider = 4, False, Prov()
        instance = None

    class Guard:
        provs = {}

        def __init__(self, *a, **k):
            pass

        def restore(self):
            pass
    monkeypatch.setattr(judge, "_load_judge", lambda *a, **k: JC())
    monkeypatch.setattr(judge, "run_canary", lambda jc, **k: None)
    monkeypatch.setattr(judge, "acquire_judge_lease", lambda *a, **k: None)
    monkeypatch.setattr(judge, "_private_case_ids", lambda cfg: set())
    monkeypatch.setattr(cli, "_ProviderGuard", Guard)
    monkeypatch.setattr(judge, "judge_row", lambda jc, row, samples=1: {
        "judge_failed": False, "judge_raw": "{}", "refused": False, "scores": {}})


# ------------------------------------------------------- OOC escalation
def _fact_pol(els):
    return judge.judge_policy({"case_id": "D1", "rubric": "rp_deep", "judge_elements": els}, BENCH45)


def _votes_judge(seq):
    it = iter(seq)
    return _CountJC(lambda full, ids: {"refused": False, "in_character": next(it), "note": "",
                                       "ooc_quote": "As an AI", "elements": {
                                           i: {"pass": False, "quote": ""} for i in ids}})


def test_a_lone_ooc_flag_escalates_to_a_majority_and_stores_every_vote():
    els = [{"id": "fa", "q": "q", "turns": [1]}]
    jc = _votes_judge([False, False])                     # 2nd agrees: no 3rd
    g = judge.judge_deep_row(jc, _deep_row(_fact_pol(els), els, turns=1), samples=1)["groups"][0]
    assert jc.calls == 2 and g["in_character_votes"] == [False, False] and g["ooc_quote"] == "As an AI"
    jc = _votes_judge([False, True, False])               # split: 3rd decides
    g = judge.judge_deep_row(jc, _deep_row(_fact_pol(els), els, turns=1), samples=1)["groups"][0]
    assert jc.calls == 3 and g["in_character_votes"] == [False, True, False]
    assert sum(1 for v in g["in_character_votes"] if v is False) >= 2


def test_in_character_and_refused_groups_do_not_escalate():
    els = [{"id": "fa", "q": "q", "turns": [1]}]
    jc = _votes_judge([True])
    judge.judge_deep_row(jc, _deep_row(_fact_pol(els), els, turns=1), samples=1)
    assert jc.calls == 1
    jc = _CountJC(_reply_ooc)                              # refused: counts alone
    judge.judge_deep_row(jc, _deep_row(_fact_pol(els), els, turns=1), samples=1)
    assert jc.calls == 1


def test_41_policy_never_escalates():
    els = [{"id": "fa", "q": "q", "turns": [1]}]
    pol = judge.judge_policy({"case_id": "D1", "rubric": "rp_deep", "judge_elements": els}, BENCH)
    jc = _votes_judge([False, False, False])
    judge.judge_deep_row(jc, _deep_row(pol, els, turns=1), samples=1)
    assert jc.calls == 1


def test_expected_calls_prices_the_ooc_escalation():
    els = [{"id": "fa", "q": "q", "turns": [1]}]
    case = {"id": "D1", "rubric": "rp_deep", "judge_elements": els}
    base = judge.expected_calls(case, BENCH45, ooc_rate=0.0)
    e = judge.expected_calls(case, BENCH45)
    assert e["calls"] == pytest.approx(base["calls"] + judge.OOC_ESCALATION_RATE * judge.OOC_ESCALATION_CALLS)
    assert e["min"] == base["min"] and e["max"] == base["max"] == 3
    assert judge.expected_calls(case, BENCH)["calls"] == 1


# ------------------------------------------------------------------ S10: zero fact groups
def test_rpdeep_row_with_nothing_to_judge_is_not_judge_failed():
    """STATE-45 17:30 (WP-7b): an rp-deep row whose elements give zero fact groups (every
    element retired, or none on its turns) and no craft chunk made no judge call and came back
    judge_failed. Nothing to judge is not a failure: no call, judge_failed False, no results."""
    els = [{"id": "fa", "q": "q", "turns": [1], "status": "retire"}]
    live = judge.judge_policy_config(None, "bench45")          # the live element-only policy
    assert live["mode"] == "elements"
    pol = judge.judge_policy({"case_id": "D1", "rubric": "rp_deep", "judge_elements": els}, live)
    jc = _CountJC(_all_no)
    out = judge.judge_deep_row(jc, _deep_row(pol, els, turns=1), samples=1)
    assert jc.calls == 0
    assert out["judge_failed"] is False and out.get("nothing_to_judge") is True
    assert out["elements"]["results"] == []
    # a row with no elements at all on the element-only policy: the same
    out = judge.judge_deep_row(jc, _deep_row(pol, [], turns=1), samples=1)
    assert jc.calls == 0 and out["judge_failed"] is False


def test_rpdeep_row_whose_only_group_fails_is_still_judge_failed():
    """The fix must not hide a real failure: a group that was asked and could not be parsed."""
    els = [{"id": "fa", "q": "q", "turns": [1]}]
    live = judge.judge_policy_config(None, "bench45")
    pol = judge.judge_policy({"case_id": "D1", "rubric": "rp_deep", "judge_elements": els}, live)
    jc = _CountJC(lambda full, ids: "not json")
    out = judge.judge_deep_row(jc, _deep_row(pol, els, turns=1), samples=1)
    assert jc.calls >= 1 and out["judge_failed"] is True and out.get("nothing_to_judge") is not True


def test_expected_calls_craft_chunks_count_retired_elements_turns(monkeypatch):
    """S10 review 6: leaving retire records out of the fact groups must not shrink the craft
    chunk count derived from the highest element turn."""
    monkeypatch.setattr(judge, "DEEP_CRAFT", True)
    live = [{"id": "fa", "q": "q", "turns": [1]}]
    case = {"id": "D1", "rubric": "rp_deep",
            "judge_elements": live + [{"id": "fz", "q": "q", "turns": [9], "status": "retire"}]}
    ref = {"id": "D1", "rubric": "rp_deep",
           "judge_elements": live + [{"id": "fz", "q": "q", "turns": [9]}]}
    a, b = judge.expected_calls(case, BENCH, ooc_rate=0.0), judge.expected_calls(ref, BENCH, ooc_rate=0.0)
    assert a["groups"] == 1 and b["groups"] == 2
    assert a["calls"] == b["calls"] - 1                      # one fact group fewer, same craft chunks
