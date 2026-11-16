"""patchhard (C3 top cell): pins, shape, reference 1.0, floors (given program, soft-only fix, a whole-file copy)."""
import pytest

from crucibleforge import codebank
from crucibleforge.gen import _patchbase as pb
from crucibleforge.gen import patchhard as ph

SEED = "4.5.0"
GEN = {"type": "patchhard", "level": 8, "key": "t1"}
CID = "T-patchhard-t1"


@pytest.fixture(scope="module")
def built():
    return ph._build(GEN, SEED, CID)


@pytest.fixture(scope="module")
def mat():
    return ph.materialize(GEN, release_seed=SEED, case_id=CID)


def _score(answer, mat):
    return codebank.grade_repo_tests(answer, mat["grader_config"])


def test_pins():
    # a change in patchfix.py, _patchbase.py or _patchsrc/ must change patchhard.py (revision digests only it)
    assert ph.source_pins() == (ph._PF_SHA, ph._BASE_SHA)


def test_hard_bugs_have_a_spec_section():
    for b in ph.HARD:
        assert pb.BUG_BY_NAME[b].get("spec"), b
    assert "queue_grant_journal" not in ph.HARD      # implied only: contestable, kept out


def test_shape(built, mat):
    me = built["meta"]
    assert len(me["bugs"]) == 12 and len(set(me["bugs"])) == 12
    assert sum(b in ph.HARD for b in me["bugs"]) >= 8
    assert set(me["features"]) == {"quota", "extend", "queue"}
    p = mat["prompt"]
    assert "## Bug reports" not in p and "how many is not known" in p
    assert "Where this document is silent, the current code's behaviour is correct" in p
    kinds = [g["kind"] for g in mat["grader_config"]["groups"]]
    assert kinds.count("f2p") == 12 and kinds.count("p2p") == 4
    assert mat["max_tokens"] == 65536


def test_deterministic(mat):
    ph._CACHE.clear()
    assert ph.materialize(GEN, release_seed=SEED, case_id=CID)["prompt"] == mat["prompt"]


def test_reference_scores_one(mat):
    v = _score(ph.reference(GEN, release_seed=SEED, case_id=CID), mat)
    assert v["score"] == 1.0, v["detail"]


def test_floor_given_program(mat, built):
    # do nothing / copy the given files back: every f2p group fails
    assert _score("No bugs found.", mat)["score"] == 0.0
    pkg = built["meta"]["rename"]["package"]
    files = {p: t for p, t in built["grader_config"]["files"].items() if p.startswith(pkg + "/")}
    assert _score(ph.pf._files_answer(files), mat)["score"] == 0.0


def test_floor_soft_only(mat, built):
    # the local-rule bugs fixed, every HARD bug left: at most 4/12, keep groups intact
    soft = [b for b in built["meta"]["bugs"] if b not in ph.HARD]
    v = _score(ph.mutants(GEN, release_seed=SEED, case_id=CID)[0], mat)
    assert v["score"] == pytest.approx(len(soft) / 12, abs=1e-3)
    assert v["score"] <= 4 / 12 + 1e-9
    assert all(r["pass"] for r in v["results"] if r["type"] == "p2p")


def test_partial_fixes_keep_groups_intact(built, mat):
    # fixing any subset of the bugs never fails a keep group: credit = fixed / 12, never halved
    import random
    me = built["meta"]
    rng = random.Random(7)
    pkg = me["rename"]["package"]
    for _ in range(6):
        left = tuple(b for b in me["bugs"] if rng.random() < 0.5)
        tree = pb.build_tree(me["knobs"], tuple(me["features"]), left, me["rename"])
        ans = ph.pf._files_answer({p: t for p, t in tree.items() if p.startswith(pkg + "/")})
        v = _score(ans, mat)
        assert all(r["pass"] for r in v["results"] if r["type"] == "p2p"), (left, v["detail"])
        assert v["score"] == pytest.approx((12 - len(left)) / 12, abs=1e-3)


def test_rule_wording_per_level(mat):
    # level 8 keeps "a rule SPEC.md states"; level 9 (an implied bug, panel reader 3) says "states or directly implies"
    assert "breaks a rule SPEC.md states;" in mat["prompt"]
    g9 = {"type": "patchhard", "level": 9, "key": "p7"}
    m9 = ph.materialize(g9, release_seed=SEED, case_id="PRB-patchhard-L9-p7")
    assert "breaks a rule SPEC.md states or directly implies;" in m9["prompt"]
    assert "queue_grant_journal" in m9["meta"]["bugs"] and m9["meta"]["implied"] == ["queue_grant_journal"]
    assert _score(ph.reference(g9, release_seed=SEED, case_id="PRB-patchhard-L9-p7"), m9)["score"] == 1.0
