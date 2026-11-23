"""Step 2 forgery fixture: before patchmini carries points, a reply that fakes a pass without a real repair must
fail at every generated Fixes level that is now scored (patchmini L1-3, patchfix L1-4, patchmod L3).

The profile's 2026-11-14 note says patchfix L5 "can be passed without a real repair": that was the code bank's
in-sandbox verdict (EXP-20261114-30). The scored rows are graded by the host-compared checker (``fixcheck``, via
``fixrows45``); these tests show each trick of ``test_fixcheck_kill_20261119`` gains nothing there, that the
reference still scores 1.0 and the given program 0, and (the test without the fix) that the trick is real under the
old verdict on a patchmini instance. No model is called; every run is a local sandbox run."""
from __future__ import annotations

import pytest

from crucibleforge import codebank, fixcheck, fixrows45, graders
from crucibleforge.gen import patchfix, patchmini, patchmod

from test_fixcheck_kill_20261119 import TRICKS

pytestmark = [pytest.mark.shipping_default, pytest.mark.live_ladder,
              pytest.mark.skipif(not graders.sandbox_available(), reason="needs a working bwrap")]

SEED = "4.5.0"
FAMS = {"patchmini": patchmini, "patchfix": patchfix, "patchmod": patchmod}
SCORED = [("patchmini", 1), ("patchmini", 2), ("patchmini", 3),
          ("patchfix", 1), ("patchfix", 2), ("patchfix", 3), ("patchfix", 4), ("patchmod", 3)]
#: tricks that patch the project's ``run`` helper need patchfix's service module; the others work on any tree
NEEDS_RUN = {"read_hidden", "always_equal", "atexit_marker"}
_MAT: dict = {}


def _mat(fam, level):
    if (fam, level) not in _MAT:
        gen = {"type": fam, "level": level}
        mat = FAMS[fam].materialize(gen, release_seed=SEED, case_id=f"FORGE-{level}")
        _MAT[(fam, level)] = (gen, mat)
    return _MAT[(fam, level)]


def _whole(files: dict) -> str:
    return "\n".join(f"{p}\n````python\n{t}````\n" for p, t in sorted(files.items()))


#: patchmini lets a reply edit only the files it shows with a bug, never service.py: the tricks that wrap ``run``
#: are then delivered from an editable file that wraps ``run`` when the service module finishes loading (the output compares equal to anything)
HOOK_EQUAL = r'''
import sys as _s
class _Eq(str):
    def __eq__(s, o): return True
    def __ne__(s, o): return False
    __hash__ = str.__hash__
def _prof(frame, event, arg):
    if (event == "return" and frame.f_code.co_name == "<module>"
            and frame.f_code.co_filename.endswith("service.py") and "run" in frame.f_globals):
        _o = frame.f_globals["run"]
        frame.f_globals["run"] = lambda script, _o=_o: _Eq(_o(script))
        _s.setprofile(None)
_s.setprofile(_prof)
'''


def _target(mat) -> str:
    """The file the forged reply edits: the service module where the answer may touch it, else the first file the
    reply is allowed to edit."""
    cfg = mat["grader_config"]
    pkg = mat["meta"]["rename"]["package"]
    allowed = cfg.get("allowed_paths") or []
    svc = f"{pkg}/service.py"
    if any(a == svc or (a.endswith("*.py") and svc.startswith(a[:-4])) for a in allowed):
        return svc
    return next(a for a in allowed if a in cfg["files"])


def _forge(mat, trick: str) -> str:
    """The given (buggy) file with the trick appended, as a whole-file reply; no bug is fixed."""
    path = _target(mat)
    if trick in NEEDS_RUN and not path.endswith("/service.py"):
        trick = HOOK_EQUAL
    else:
        trick = TRICKS.get(trick, trick)
    return _whole({path: mat["grader_config"]["files"][path] + "\n" + trick})


def _cases():
    return [(f, lv, t) for f, lv in SCORED for t in sorted(TRICKS) + ["hook_equal"]
            if f != "patchmod" or t not in NEEDS_RUN]


@pytest.mark.parametrize("fam,level,trick", _cases())
def test_a_faked_pass_scores_zero_at_every_scored_level(fam, level, trick):
    _gen, mat = _mat(fam, level)
    v = fixcheck.grade_answer(_forge(mat, HOOK_EQUAL if trick == "hook_equal" else trick), mat["grader_config"])
    assert v["score"] == 0.0, (fam, level, trick, v.get("detail"))


@pytest.mark.parametrize("fam,level", SCORED)
def test_floors_at_every_scored_level(fam, level):
    gen, mat = _mat(fam, level)
    cfg = mat["grader_config"]
    ref = FAMS[fam].reference(gen, release_seed=SEED, case_id=f"FORGE-{level}")
    assert fixcheck.grade_answer(ref, cfg)["score"] == 1.0
    pkg = mat["meta"]["rename"]["package"]
    given = {p: t for p, t in cfg["files"].items() if p.startswith(pkg + "/")}
    assert fixcheck.grade_answer(_whole(given), cfg)["score"] == 0.0
    assert fixcheck.grade_answer("I could not find the bugs.", cfg)["score"] == 0.0


def test_before_the_trick_is_real_on_patchmini_under_the_in_sandbox_verdict():
    """Without the host-compared checker (patchmini was outside fixrows45.FIX_FAMILIES): full credit, bug left in."""
    _gen, mat = _mat("patchmini", 1)
    v = codebank.grade_repo_tests(_forge(mat, HOOK_EQUAL), mat["grader_config"])
    assert v["score"] == 1.0, v.get("detail")


def test_every_scored_family_is_graded_by_the_host_compared_checker():
    for fam, level in SCORED:
        _gen, mat = _mat(fam, level)
        case = {"id": f"{fam.upper()}-core-{level}-1", "generator": {"type": fam, "level": level},
                "grader_config": mat["grader_config"]}
        assert fixrows45.is_fix_case(case), fam
