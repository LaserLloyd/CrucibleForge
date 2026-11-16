"""T1 1c: PATCHFIX L7 as a Main item. Reference = 1.0 as whole files and as SEARCH/REPLACE blocks;
lazy answers (prose, an elided whole file, '...' blocks, the unchanged program) = 0.0."""
import difflib

import pytest

from crucibleforge.gen import patchfix

SEED = "4.5.0"
GEN = {"type": "patchfix", "level": 7, "key": "t1a"}
CID = "T1-t1a"


@pytest.fixture(scope="module")
def mat():
    return patchfix.materialize(GEN, release_seed=SEED, case_id=CID)


def _sr_answer(mat):
    base = mat["grader_config"]["files"]
    ref = patchfix._build(GEN, SEED, CID)["reference_files"]
    out = []
    for p, t in ref.items():
        o = base.get(p)
        if o is None or o == t:
            continue
        a, b = o.splitlines(True), t.splitlines(True)
        for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b).get_opcodes():
            if tag == "equal":
                continue
            lo, hi = max(0, i1 - 3), min(len(a), i2 + 3)
            s = "".join(a[lo:hi])
            r = "".join(a[lo:i1]) + "".join(b[j1:j2]) + "".join(a[i2:hi])
            if o.count(s) == 1:
                out.append(f"{p}\n```python\n<<<<<<< SEARCH\n{s}=======\n{r}>>>>>>> REPLACE\n```\n")
    assert out
    return "\n".join(out)


def test_reference_whole_files(mat):
    assert patchfix.grade(patchfix.reference(GEN, release_seed=SEED, case_id=CID), mat) == 1.0


def test_reference_search_replace(mat):
    assert patchfix.grade(_sr_answer(mat), mat) == 1.0


def test_lazy_answers_score_zero(mat):
    f = sorted(mat["grader_config"]["files"])[0]
    base = "\n".join(f"{p}\n```python\n{t}```\n" for p, t in sorted(mat["grader_config"]["files"].items())
                     if p.endswith(".py"))
    for ans in ("I fixed the bugs by reviewing the code.\n",
                f"{f}\n```python\n# ... rest of the file unchanged\n```\n",
                f"{f}\n```python\n<<<<<<< SEARCH\n...\n=======\n...\n>>>>>>> REPLACE\n```\n",
                base):
        assert patchfix.grade(ans, mat) == 0.0
