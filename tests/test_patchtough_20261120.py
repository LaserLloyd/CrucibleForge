"""patchtough (staged Fixes pack 2): reference 1.0, every listed wrong implementation fails, the 5-file fix-checker stamp
and fixrows45.py are byte-identical to the 0c2374f pins, the set is staged and in no composition. Scratch: /var/tmp only."""
import hashlib
import json
from pathlib import Path

import pytest

from crucibleforge import fixcheck, fixrows45, ladder
from crucibleforge.gen import patchtough as pt
from crucibleforge.gen.patchfix import _files_answer

ROOT = Path(__file__).resolve().parents[1]
SEED = "4.5.0"
PINS = {   # sha256 at 0c2374f (the stamp files + fixrows45 + patchmod, whose bytes the pack's prompt/reference depend on)
    "crucibleforge/fixcheck.py": "64525c8d9bed2c1f18e21acc82d961e35b64ae6bfea2a451ef19dedc7eadc247",
    "crucibleforge/fixspecs.py": "acadd75482a7b3bebca25bdfa314277e48ecbeac91ffb168b926c870b144f22d",
    "crucibleforge/codebank.py": "4cc4ddfceb1d146b1575dc4ca4b53c62d08012357acd6a5d91ddbd96184f0ef2",
    "crucibleforge/gen/_patchbase.py": "e9603331acb9c8b947ebf323d89be7cffc83782a19b3f467952c115a40447b2c",
    "crucibleforge/graders.py": "17b051014a678da9ba5a4e051ae3ac5212e50ffaa70dbb4408f65d61dbd5b6aa",
    "crucibleforge/fixrows45.py": "3883bb991ce02bad620cd3d9a830760cf37d8e1e6d507e7194ca29104b91bc3a",   # step 2: + patchmini
    "crucibleforge/gen/patchmod.py": "18870070192e9d0f32091bbf96f224f375c396bcdf92d3e25b319127f8adb441",
}


def test_fix_checker_stamp_files_and_fixrows_are_byte_identical():
    for p, sha in PINS.items():
        assert hashlib.sha256((ROOT / p).read_bytes()).hexdigest() == sha, p
    h = hashlib.sha256()
    for f in ("fixcheck.py", "fixspecs.py", "codebank.py", "gen/_patchbase.py", "graders.py"):
        h.update(f.encode() + b"\0" + (ROOT / "crucibleforge" / f).read_bytes() + b"\0")
    assert h.hexdigest()[:12] == fixrows45.checker_stamp()
    assert "patchtough" not in fixrows45.FIX_FAMILIES
    assert len(pt.pack_stamp()) == 12 and pt.pack_stamp() != fixrows45.checker_stamp()


def test_pack_is_in_no_standard_composition_and_set_is_staged():
    assert "patchtough" not in (ROOT / "profiles/bench45.yaml").read_text(encoding="utf-8")
    spec = json.loads((ROOT / "cases/staged/work/c3_fix/set-2.json").read_text(encoding="utf-8"))
    assert ladder.is_staged(spec) and spec["generator"]["family"] == "patchtough"


@pytest.mark.parametrize("level,key", [(1, "b"), (1, "c"), (1, "e"), (1, "h"), (2, "b"), (2, "c")])
def test_reference_passes_given_fails_and_every_wrong_implementation_fails(level, key):
    g = {"type": "patchtough", "level": level, "key": key}
    m = pt.materialize(g, release_seed=SEED, case_id=f"T-{level}-{key}")
    cfg = m["grader_config"]
    assert m == pt.materialize(g, release_seed=SEED, case_id=f"T-{level}-{key}")
    assert fixcheck.grade_answer(pt.reference(g, release_seed=SEED, case_id=f"T-{level}-{key}"), cfg)["score"] == 1.0
    assert fixcheck.grade_answer("", cfg)["score"] == 0.0
    assert len(m["mutants"]) >= 5
    scores = {n: fixcheck.grade_answer(_files_answer(f), cfg)["score"] for n, f in m["mutants"].items()}
    assert all(s < 1.0 for s in scores.values()), scores
    assert sum(s < 0.95 for s in scores.values()) >= 3, scores
    for must in ("queue_overtakes", "queue_no_aging"):          # the two mutants patchmod's own tests let through
        assert scores[must] < 1.0, (must, scores)


@pytest.mark.parametrize("key", ["b", "c", "d"])
def test_level3_wrong_visible_tests_reference_and_wrong_fixes(key):
    g = {"type": "patchtough", "level": 3, "key": key}
    m = pt.materialize(g, release_seed=SEED, case_id=f"T-3-{key}")
    cfg = m["grader_config"]
    assert "tests/test_regression.py" in m["prompt"] and "fail today" in m["prompt"]
    assert fixcheck.grade_answer(pt.reference(g, release_seed=SEED, case_id=f"T-3-{key}"), cfg)["score"] == 1.0
    assert fixcheck.grade_answer("", cfg)["score"] == 0.0
    assert len(m["meta"]["wrong_tests"]) == 2
    scores = {n: fixcheck.grade_answer(_files_answer(f), cfg)["score"] for n, f in m["mutants"].items()}
    assert len(scores) >= 4 and all(s < 1.0 for s in scores.values()), scores
    assert any(n.startswith("pass-the-wrong-test") for n in scores)
    # the wrong tests fail on the reference too: run the shown tests against the reference tree
    import re
    blob = m["prompt"].split("tests/test_regression.py\n```python\n", 1)[1].split("```", 1)[0]
    ns = {}
    from crucibleforge.gen import _patchbase as pb
    tree = dict(cfg["files"])
    for p, txt in m["reference_files"].items():
        tree[p] = txt
    run = pb.load_runner(tree, m["meta"]["rename"])
    cases = re.findall(r"script = (.*)\n        want = (.*)\n", blob)
    assert len(cases) == 3
    verdicts = [run(eval(s)) == eval(w) for s, w in cases]
    assert verdicts.count(False) == 2 and verdicts.count(True) == 1, verdicts
