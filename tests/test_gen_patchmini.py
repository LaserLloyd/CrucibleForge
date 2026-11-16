"""patchmini (C3 low/mid cells): pins, shape per level, reference 1.0, floors (nothing, the given files,
one bug fixed), SPEC view covers every bug's section."""
import pytest

from crucibleforge import codebank
from crucibleforge.gen import _patchbase as pb
from crucibleforge.gen import patchmini as pm

SEED = "4.5.0"


def _case(level, key="t1"):
    g = {"type": "patchmini", "level": level, "key": key}
    cid = f"T-patchmini-L{level}-{key}"
    return g, cid, pm.materialize(g, release_seed=SEED, case_id=cid)


@pytest.fixture(scope="module", params=[1, 2, 3])
def case(request):
    return _case(request.param)


def _score(answer, mat):
    return codebank.grade_repo_tests(answer, mat["grader_config"])


def test_pins():
    assert pm.source_pins() == (pm._PF_SHA, pm._BASE_SHA)


def test_shape(case):
    g, cid, mat = case
    L = pm.LEVELS[g["level"]]
    me = mat["meta"]
    assert len(me["bugs"]) == L["bugs"]
    files = [pb.BUG_BY_NAME[b]["file"] for b in me["bugs"]]
    assert len(set(files)) == len(files)                      # one bug per file, as the prompt says
    assert len(me["shown"]) == L["bugs"] + L["decoys"] + 1     # + config.py
    assert len(me["decoys"]) == L["decoys"]
    for b in me["bugs"]:                                        # the rule each bug breaks is on the page
        assert pb.BUG_BY_NAME[b]["spec"].split(".")[0] in me["spec_sections"]
    p = mat["prompt"]
    assert f"exactly {L['bugs']} bugs" in p and p.count("### Report ") == L["reported"]
    assert me["prompt_tokens"] < 6000
    kinds = [x["kind"] for x in mat["grader_config"]["groups"]]
    assert kinds.count("f2p") == L["bugs"] and kinds.count("p2p") == 4


def test_reference_and_floors(case):
    g, cid, mat = case
    assert _score(pm.reference(g, release_seed=SEED, case_id=cid), mat)["score"] == 1.0
    assert _score("I found nothing to fix.", mat)["score"] == 0.0
    shown = {p: mat["grader_config"]["files"][p] for p in mat["meta"]["shown"]}
    assert _score(pm.pf._files_answer(shown), mat)["score"] == 0.0


def test_one_fix_scores_its_share(case):
    g, cid, mat = case
    me = mat["meta"]
    rn = {k: (tuple(v) if isinstance(v, list) else v) for k, v in me["rename"].items()}
    left = tuple(me["bugs"][1:])
    tree = pb.build_tree(me["knobs"], pm.FEATURES, left, rn)
    ans = pm.pf._files_answer({p: tree[p] for p in me["shown"]})
    v = _score(ans, mat)
    assert v["score"] == pytest.approx(1 / len(me["bugs"]), abs=1e-3)
    assert all(r["pass"] for r in v["results"] if r["type"] == "p2p")
