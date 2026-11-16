"""WP-13a (4.5): element-class sampling, sequential majority, row pool sized
from the policy, row_timeout_s from the profile, registry judge block."""
import json
import threading

import pytest

from crucibleforge import judge

CLASSED = {**judge.JUDGE_POLICY_DEFAULTS,
           "element_class": {"fact": 1, "craft": 3}, "sequential_majority": True}


def row(rubric, cid="X", elements=(), **kw):
    return {"rubric": rubric, "case_id": cid, "prompt": "p", "response": "She crossed the harbour at dawn, carrying the lantern.",
            "judge_elements": list(elements), **kw}


def el(i="e1", **kw):
    return {"id": i, "q": "q?", **kw}


# ------------------------------------------------------------ element class
def test_element_class_explicit_field_wins():
    assert judge.element_class(el(**{"class": "craft"}), "steer") == "craft"
    assert judge.element_class(el(**{"class": "FACT", "dim": "voice"}), "rp_single") == "fact"


def test_element_class_defaults():
    # an rpdeep fact element (turns) and a flag/no-dim element are facts;
    # an element that caps a 0-10 dimension is craft
    assert judge.element_class(el(turns=[1]), "rp_deep") == "fact"
    assert judge.element_class(el(), "steer") == "fact"
    dim = judge.RUBRICS["rp_single"]["dims"][0]
    assert judge.element_class(el(dim=dim), "rp_single") == "craft"


@pytest.mark.archive_default
def test_no_element_class_config_is_byte_identical():
    base = judge.JUDGE_POLICY_DEFAULTS
    assert base["element_class"] is None and base["sequential_majority"] is False
    rows = [row("steer", elements=[el(dim="obeyed")]), row("rp_single"),
            row("rp_deep", elements=[el(turns=[1])]), row("safety"),
            row("rp_single", elements=[el(dim=judge.RUBRICS["rp_single"]["dims"][0])])]
    for r in rows:
        p = judge.judge_policy(r, base)
        assert not ({"classed", "sequential", "element_class"} & set(p)), p
    # the exact pre-change shape for the binary and craft kinds
    assert judge.judge_policy(rows[0], base) == {
        "kind": "binary", "samples": 3, "reason": "an element decides the 'obeyed' flag",
        "samples_source": "policy", "thinking": False, "max_tokens": 4096}
    assert judge.judge_policy(rows[1], base) == {
        "kind": "craft", "samples": 1, "reason": "craft dimensions only",
        "samples_source": "policy"}
    cfg = judge.judge_policy_config({})
    assert cfg["element_class"] is None
    assert cfg is not judge.JUDGE_POLICY_DEFAULTS


def test_classed_policy_samples_by_class():
    dim = judge.RUBRICS["rp_single"]["dims"][0]
    fact = judge.judge_policy(row("steer", elements=[el()]), CLASSED)
    assert (fact["kind"], fact["samples"]) == ("classed", 1) and "sequential" not in fact
    craft = judge.judge_policy(row("rp_single", elements=[el(dim=dim)]), CLASSED)
    assert craft["samples"] == 3 and craft["sequential"] and craft["classed"]
    mixed = judge.judge_policy(row("rp_single", elements=[el("a"), el("b", dim=dim)]), CLASSED)
    assert mixed["samples"] == 3                      # the largest class in the call
    # the private/binary tag rules are replaced by the class rule
    assert judge.judge_policy(row("steer", elements=[el(dim="obeyed")]), CLASSED)["samples"] == 1
    # an element-less dimension row keeps craft_samples
    assert judge.judge_policy(row("rp_single"), CLASSED)["samples"] == 1
    # a CLI --samples still overrides the count
    assert judge.judge_policy(row("rp_single", elements=[el(dim=dim)]), CLASSED, 2)["samples"] == 2


def test_policy_width():
    assert judge.policy_width({"samples": 3, "sequential": True}) == 2.0
    assert judge.policy_width({"samples": 3}) == 3.0
    assert judge.policy_width({"samples": 1}) == 1.0


# ------------------------------------------------- sequential majority draws
class FakeJC:
    thinking = False
    context_length = 16384
    max_tokens = 1024
    row_timeout_s = None
    sample_temperature = 0.7
    extra_body = None

    def __init__(self, passes):
        """``passes``: yes/no per call, in call order (the lock makes order = seed order)."""
        self.passes, self.calls, self.lock = list(passes), 0, threading.Lock()

    def chat(self, messages, **kw):
        with self.lock:
            i = self.calls
            self.calls += 1
        ok = self.passes[min(i, len(self.passes) - 1)]

        class R:
            finish_reason = "stop"
            completion_tokens = 1
            reasoning_tokens = 0
            reasoning_text = ""
            response_text = json.dumps({"refused": False, "elements": [
                {"id": "e1", "pass": ok, "quote": "crossed the harbour at dawn" if ok else ""}]})
        return R()


@pytest.fixture
def steer_row():
    return {**row("steer", elements=[el()]),
            "_judge_policy": {"kind": "classed", "samples": 3, "sequential": True, "classed": True}}


def _prep(monkeypatch):
    monkeypatch.setattr(judge, "_has_content", lambda r: True)
    monkeypatch.setattr(judge, "build_judge_input", lambda r, b: ("steer", "prompt"))
    monkeypatch.setattr(judge, "parse_verdict", lambda raw, rubric, elements=None, **k: _pv(raw))


def _pv(raw):
    d = json.loads(raw)
    spec = judge.RUBRICS["steer"]
    out = {f: False for f in spec["flags"]}
    out.update({k: 5 for k in spec["dims"]})
    out["elements"] = {e["id"]: {"pass": e["pass"], "quote": e["quote"]} for e in d["elements"]}
    return out


def test_sequential_majority_agreement_uses_two_calls(monkeypatch, steer_row):
    _prep(monkeypatch)
    jc = FakeJC([True, True, False])
    out = judge.judge_row(jc, steer_row, samples=3)
    assert jc.calls == 2
    assert out["samples_used"] == 2 and out["sequential"] is True
    assert out["votes"] == {"e1": [True, True]}
    assert out["elements"]["results"][0]["pass"] is True


def test_sequential_majority_split_draws_third(monkeypatch, steer_row):
    _prep(monkeypatch)
    jc = FakeJC([True, False, True])        # split, then the tie-break says yes
    out = judge.judge_row(jc, steer_row, samples=3)
    assert jc.calls == 3 and out["samples_used"] == 3
    assert sorted(out["votes"]["e1"]) == [False, True, True]
    assert out["elements"]["results"][0]["pass"] is True


def test_unverified_quote_counts_as_a_split(monkeypatch, steer_row):
    _prep(monkeypatch)
    monkeypatch.setattr(judge, "quote_found", lambda q, t, short=False: q == "crossed the harbour at dawn")
    jc = FakeJC([True, True])
    # both say yes with a verifiable quote -> no split
    judge.judge_row(jc, steer_row, samples=3)
    assert jc.calls == 2


def test_unclassed_row_is_unchanged(monkeypatch):
    _prep(monkeypatch)
    jc = FakeJC([True, True, False])
    r = {**row("steer", elements=[el()]), "_judge_policy": {"kind": "binary", "samples": 3}}
    out = judge.judge_row(jc, r, samples=3)
    assert jc.calls == 3
    assert "samples_used" not in out and "votes" not in out and "sequential" not in out


def test_verdicts_split_on_dims_and_flags():
    spec = judge.RUBRICS["rp_single"]
    a = {**{d: 5 for d in spec["dims"]}, **{f: False for f in spec["flags"]}}
    b = {**a}
    assert not judge.verdicts_split([a, b], [], spec, "")
    b2 = {**a, spec["dims"][0]: 5 + judge.SEQ_DIM_SPLIT}
    assert judge.verdicts_split([a, b2], [], spec, "")
    assert not judge.verdicts_split([a, {**a, spec["dims"][0]: 6}], [], spec, "")
    if spec["flags"]:
        assert judge.verdicts_split([a, {**a, spec["flags"][0]: True}], [], spec, "")


# ----------------------------------------------------------- rpdeep groups
def test_deep_group_rows_carry_the_element_class():
    r = judge.deep_group_row({"judge_key": "k", "model_label": "m", "case_id": "c"}, (1,),
                             [{"id": "a", "q": "q", "class": "craft", "turns": [1]},
                              {"id": "b", "q": "q2", "turns": [1]}])
    assert r["judge_elements"][0]["class"] == "craft" and "class" not in r["judge_elements"][1]


def test_deep_row_group_samples_follow_the_class(monkeypatch):
    seen = []

    def fake_call(jc, sub, samples, sem, policy=None):
        seen.append((sub.get("rubric"), samples, dict(policy or {})))
        els = {e["id"]: {"id": e["id"], "pass": True, "quote": "x", "type": "judge",
                         "judge_pass": True, "detail": ""} for e in sub["judge_elements"]}
        return {"judge_failed": False, "refused": False, "scores": {"in_character": True},
                "elements": {"results": list(els.values())}}

    monkeypatch.setattr(judge, "_deep_call", fake_call)
    monkeypatch.setattr(judge, "_deep_live_turns", lambda r: [("u", "a reply"), ("u", "b reply")])

    class JC:
        concurrency, context_length, max_tokens = 4, 16384, 1024
    r = {"rubric": "rp_deep", "case_id": "c", "model_label": "m", "judge_key": "k",
         "judge_elements": [{"id": "f1", "q": "q", "turns": [1]},
                            {"id": "c1", "q": "q", "turns": [2], "class": "craft"}],
         "_judge_policy": {"classed": True, "sequential": True, "thinking": False,
                           "max_tokens": 4096, "element_class": {"fact": 1, "craft": 3}}}
    judge.judge_deep_row(JC(), r, samples=1)
    by_samples = sorted(s for _, s, _ in seen)
    assert by_samples == [1, 3]
    three = next(p for _, s, p in seen if s == 3)
    assert three["sequential"] and three["classed"]


# ------------------------------------------------------------ row pool size
@pytest.mark.archive_default
def test_row_pool_is_sized_from_policy_width(tmp_path, monkeypatch):
    from crucibleforge import config
    monkeypatch.setattr(config, "RESULTS_DIR", tmp_path)
    dim = judge.RUBRICS["rp_single"]["dims"][0]
    rows = [{"bench_run_id": "r", "case_id": f"C{i}", "repeat": 1, "turn": None,
             "rubric": "rp_single", "needs_judge": True, "response": "scene", "prompt": "p",
             "judge_elements": [{"id": "e", "q": "q", "dim": dim}]} for i in range(6)]
    (tmp_path / "transcripts_m.jsonl").write_text(
        "\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")

    class FakeProv:
        type, name = "openai", "fake"

    class JC:
        model_id, concurrency, thinking, label, provider = "j", 8, False, "j @ fake", FakeProv()

    pools = []
    monkeypatch.setattr(judge, "_load_judge", lambda *a, **k: JC())
    monkeypatch.setattr(judge, "run_canary", lambda jc, **k: None)
    monkeypatch.setattr(judge, "_private_case_ids", lambda cfg: set())
    monkeypatch.setattr(judge, "judge_row", lambda jc, r, samples=1: {
        "judge_failed": False, "judge_raw": "{}", "refused": False, "scores": {}})
    monkeypatch.setattr(judge, "run_rows", lambda jobs, fn, workers, stop=None: (
        pools.append(workers), [fn(*j) for j in jobs]))
    cfg = {"models": [{"name": "m", "model_id": "m", "provider": "fake"}],
           "judge": {"samples": 1, "policy": {"element_class": {"fact": 1, "craft": 3},
                                              "sequential_majority": True}},
           "providers": {}, "defaults": {}}
    judge.run_judge(cfg, ["m"])
    assert pools == [4]                        # 8 slots / width 2 (sequential 3), not ceil(8/3)=3
    # without the policy block: the old rule (mean of the samples)
    pools.clear()
    cfg2 = {**cfg, "judge": {"samples": 1}}
    judge.run_judge(cfg2, ["m"], force=True)
    assert pools == [8]


# ---------------------------------------------- row timeout + judge block
def test_row_timeout_from_profile_then_default(monkeypatch):
    class Prov:
        type, name = "openai", "fake"

        def workers(self, _):
            return 2
    monkeypatch.setattr(judge, "get_provider", lambda cfg, name: Prov())
    cand = {"provider": "fake", "model_id": "j"}
    assert judge.JudgeClient({}, dict(cand)).row_timeout_s == judge.DEFAULT_ROW_TIMEOUT_S
    monkeypatch.setattr(judge, "_profile_value", lambda cfg, key, default=None: 1200)
    assert judge.JudgeClient({}, dict(cand)).row_timeout_s == 1200.0
    assert judge.JudgeClient({}, {**cand, "row_timeout_s": 90}).row_timeout_s == 90.0


def test_profile_value_reads_the_active_profile(monkeypatch):
    from crucibleforge import profiles
    seen = []
    monkeypatch.setattr(profiles, "load_profile",
                        lambda name, cfg=None: (seen.append(name), {"row_timeout_s": 1200})[1])
    assert judge._profile_value({"_profile": "bench45"}, "row_timeout_s") == 1200
    assert seen == ["bench45"]
    assert judge._profile_value({}, "nope", 7) == 7


def test_registry_judge_block_context_alias_and_pins():
    entry = {"provider": "xai", "model_id": "grok-4.7", "name": "grok",
             "judge": {"thinking": True, "context": 24576, "temperature": 1,
                       "top_p": 0.95, "sample_temperature": 1, "bogus": 1}}
    c = judge.judge_from_registry(entry)
    assert c["thinking"] is True and c["context_length"] == 24576 and "context" not in c
    assert c["top_p"] == 0.95 and c["sample_temperature"] == 1 and "bogus" not in c
    plain = judge.judge_from_registry({"provider": "p", "model_id": "m", "name": "n"})
    assert plain["context_length"] == judge.JUDGE_DEFAULT_CONTEXT


# ----------------------------------------- N judge instances (not just two)
def test_three_shards_equal_one_judge_and_record_the_instance(tmp_path):
    from crucibleforge import config, shard
    import tests.test_parallel_judge_20261108 as T
    data = {"m1": 9, "m2": 6, "m3": 7, "m4": 4, "m5": 5, "m6": 3}
    single, sharded = tmp_path / "single", tmp_path / "sharded"
    T._write(single, data)
    T._write(sharded, data)
    p = T._spawn(single, list(data))
    _, err = p.communicate(timeout=180)
    assert p.returncode == 0, err
    config.set_results_dir(sharded)
    pend = {l: judge._pending_rows(l) for l in data}
    plan = shard.make_plan(list(data), 3, pend, lambda r: 1, config.transcript_path)
    parts = {i: shard.plan_labels(plan, i) for i in (1, 2, 3)}
    assert sorted(sum(parts.values(), [])) == sorted(data)
    insts = {1: {"instance_name": "a"},
             2: {"serve_model_id": "J@b", "instance_name": "b", "instance_devices": [2, 3]},
             3: {"serve_model_id": "J@c", "instance_name": "c", "instance_devices": [4, 5]}}
    procs = {i: T._spawn(sharded, parts[i],
                         steal=[l for j in parts if j != i for l in parts[j]], inst=insts[i])
             for i in parts}
    for pr in procs.values():
        _, err = pr.communicate(timeout=180)
        assert pr.returncode == 0, err
    v = shard.verify(plan, config.transcript_path)
    assert v["ok"] and v["judged"] == sum(data.values()) and v["unjudged"] == 0
    seen = set()
    for label in data:
        one, three = T._final(single, label), T._final(sharded, label)
        for cid in one:
            assert one[cid]["judge"]["scores"] == three[cid]["judge"]["scores"]
            assert three[cid]["judge_model"] == "J"
            seen.add(three[cid]["judge_instance"]["serve_id"])
    assert seen <= {"J", "J@b", "J@c"} and len(seen) >= 2


def test_instances_config_takes_any_number_of_disjoint_copies():
    from crucibleforge import instances
    cfg = {"instances": {"J": [{"name": "a", "devices": [0, 1]},
                               {"name": "b", "serve_id": "J@b", "devices": [2, 3]},
                               {"name": "c", "serve_id": "J@c", "devices": [4, 5]}]}}
    got = instances.configured(cfg, "J")
    assert [i.name for i in got] == ["a", "b", "c"]
    assert instances.find(cfg, "J", "c").audit()["serve_id"] == "J@c"
    cfg["instances"]["J"].append({"name": "d", "serve_id": "J@d", "devices": [5, 6]})
    with pytest.raises(ValueError):
        instances.configured(cfg, "J")
