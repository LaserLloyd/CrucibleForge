"""FC1 (Task 2a): patgen, the P1 two-turn pattern predictor."""
import random
from crucibleforge import gen
from crucibleforge.gen import patgen


def _mat(fam, **kw):
    return gen.get(fam).materialize({"type": fam, "level": 2, "key": "t", **kw}, release_seed="test", case_id="X")


def test_patgen_deterministic_and_shared_streams():
    a, b = _mat("patgen", variant="t2", t1_answer="x"), _mat("patgen", variant="t2", t1_answer="y")
    assert a["grader_config"] == b["grader_config"]
    assert a["history"][1]["content"] == "x" and a["history"][0]["content"] == _mat("patgen", variant="t1")["prompt"]


def test_patgen_oracle_is_bayes_for_clean_slots():
    r = random.Random(1)
    st = patgen.gen_stream(r, [("per", 5, 9)], 0.1)
    acc = patgen.oracle_acc(st)
    assert 0.8 < acc < 0.99            # ~ 1 - 2*eps/3
    st0 = patgen.gen_stream(r, [("shift", 5, 9)], 0.0)
    assert patgen.oracle_acc(st0) > 0.95


def test_patgen_engine_grades_reference_and_mutant():
    m = _mat("patgen", variant="t2")
    assert patgen.grade(patgen.reference({}, release_seed="", case_id=""), m) >= 0.6
    assert patgen.grade(patgen.mutants({}, release_seed="", case_id="")[0], m) < 0.1
    assert patgen.grade("", m) == 0.0


def test_patgen_isolation_and_lockstep():
    """the predictor cannot see the grader's data, and it never sees digit t before it has guessed digit t"""
    m = _mat("patgen", variant="t1")
    peek = ("```python\nimport sys, gc\nclass Predictor:\n    def __init__(self):\n        self.g = 0\n"
            "        try:\n            for o in gc.get_objects():\n                if isinstance(o, dict) and '_JXP_DATA' in o: self.g = 1\n"
            "            f = sys._getframe(1)\n            self.g = 2 if 'tiers' in f.f_locals else self.g\n        except Exception: pass\n"
            "    def predict(self): return 0\n    def update(self, d): pass\n```")
    assert patgen.grade(peek, m) < 0.1
    ahead = ("```python\nclass Predictor:\n    def __init__(self): self.s = ''\n"
             "    def predict(self): return int(__import__('sys').stdin.readline().strip() or 0)\n    def update(self, d): pass\n```")
    assert patgen.grade(ahead, m) < 0.2        # reading stdin stalls: there is no digit before the guess


def test_patgen_scale_anchors():
    s = patgen.stream_score
    assert s(1 / 3, 0.97, 0.6, "edge") == 0.0 and abs(s(0.6, 0.97, 0.6, "climb") - patgen.ANCHOR_A) < 1e-9
    assert abs(s(0.97, 0.97, 0.6, "edge") - 1.0) < 1e-9 and s(0.97, 0.97, 0.6, "floor") == 1.0


def test_patgen_demux_schedule_oracle():
    r = random.Random(3)
    st = patgen.gen_stream(r, [("per", 4, 9), ("sum3", 3, 6), ("per", 5, 11)], 0.0, sched=(7, 10))
    assert len(st["sched"]) >= 7 and patgen.oracle_acc(st) == 1.0 and st["digits"] == st["clean"]


def test_patgen_fc1_frozen_seeds_reproduce_stored_baselines():
    """FC1 seeds n1/n2/n3 (release 4.5.0): the generic n-gram baseline stays near 26 points, the period-finder
    under 40 and the reference far above (2a exit: baseline <= 40; measured ngram 25.5-26.4, period-finder 33.4-35.2,
    k<=13 reference 82.8-86.6)."""
    import pathlib
    base = pathlib.Path(__file__).resolve().parent.parent / "probe-p" / "baselines"
    ng, pf, ref = [], [], []
    for key in ("n1", "n2", "n3"):
        inst = patgen.instance(2, patgen.instance_key({"level": 2, "key": key}, "4.5.0"))
        ng.append(patgen.score_code((base / "ngram.py").read_text(encoding="utf-8"), inst["t2"])["points"])
        pf.append(patgen.score_code((base / "period_finder.py").read_text(encoding="utf-8"), inst["t2"])["points"])
        ref.append(patgen.score_code((base / "reference_k13.py").read_text(encoding="utf-8"), inst["t2"])["points"])
    assert all(22 < p < 30 for p in ng), ng
    assert all(p <= 40 for p in pf) and sum(pf) / 3 <= 38, pf
    assert all(80 < p < 92 for p in ref), ref


def test_patgen_fc1_tier_weights():
    assert sum(t["w"] for t in patgen.T2_TIERS.values()) == 100
    assert [n for n, t in patgen.T2_TIERS.items() if t["role"] == "edge"] == ["demux-irregular", "demux-deep", "demux-wide"]


def test_patgen_demux_wide_is_hidden_from_the_prompt():
    """the extra FC1 tier shows no example stream, so the turn-2 prompt is unchanged for the stored replies"""
    inst = patgen.instance(2, patgen.instance_key({"level": 2, "key": "n1"}, "4.5.0"))
    assert len(inst["ex2"]) == len(patgen.T2_TIERS) - 1 and "demux-wide" in inst["t2"]
