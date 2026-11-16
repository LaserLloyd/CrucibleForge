"""The shared deterministic launcher (gen/_det.py, 2026-11-14): one stored reply, one grade.

Per family that executes model code, one reply whose result depends on random, Random(),
os.urandom/uuid4 and str hash() is graded three times: the grades are identical. Without the
launcher (``-I`` drops PYTHONHASHSEED, nothing seeded) the reply's coin differs run to run.
The grade detail names the interpreter; the regrade tool refuses another interpreter than the pin."""
from __future__ import annotations

import hashlib
import re
import subprocess
from pathlib import Path

import pytest

from crucibleforge import codebank, graders, regrade
from crucibleforge.gen import _det

SEED = "4.5.0"

pytestmark = pytest.mark.skipif(not (graders._BWRAP and graders.probe_bwrap(graders._BWRAP)[0]),
                                reason="needs the bwrap sandbox")

# the coin: random, Random(), os.urandom (via uuid4) and str hash() all feed it
_COIN = ("import random as _rr, uuid as _uu\n"
         "_COIN = (int(_rr.random() * 1e6) + int(_rr.Random().random() * 1e6) + _uu.uuid4().int"
         " + hash('bench-det') + len(str(set('abcdefgh')))) % 2 == 1\n")


def _spoil(fn):
    """Wrap entry point ``fn`` of a module so it raises on one side of the coin."""
    return (f"\n_orig_{fn} = {fn}\n"
            f"def {fn}(*a, **k):\n"
            f"    if _COIN or hash('bench-det-2') % 3 == 0:\n"
            f"        raise RuntimeError('unlucky')\n"
            f"    return _orig_{fn}(*a, **k)\n")


def _code_reply(ref: str, fn: str) -> str:
    code = re.search(r"```python\n(.*?)```", ref, re.S)
    code = code.group(1) if code else ref
    return "```python\n" + _COIN + code + _spoil(fn) + "```"


def _thrice(grade):
    out = []
    for _ in range(3):
        g = grade()
        out.append({k: g.get(k) for k in ("grade", "score", "rate", "points", "q")})
    assert out[0] == out[1] == out[2], out
    return out[0]


def test_launcher_fixes_every_source_of_draws():
    src = _det.with_prelude("import random, os, uuid\nprint(random.random(), random.Random().random(), "
                            "os.urandom(4).hex(), uuid.uuid4(), hash('x'), list(set('abcdef')))\n", "t")
    outs = set()
    for _ in range(3):
        import tempfile
        with tempfile.TemporaryDirectory(dir="/var/tmp") as td:
            p = Path(td) / "h.py"
            p.write_text(src, encoding="utf-8")
            r = subprocess.run(_det.det_cmd(graders._sandbox_cmd(str(p), td)), capture_output=True, text=True,
                               env=_det.det_env(), cwd=td, timeout=60)
            assert r.returncode == 0, r.stderr
            outs.add(r.stdout)
    assert len(outs) == 1


def test_det_cmd_swaps_only_the_isolation_flag():
    assert _det.det_cmd(["bwrap", "-I", "x", "py", "-I", "h.py"]) == ["bwrap", "-I", "x", "py", "-s", "-S", "-P", "h.py"]
    with pytest.raises(ValueError):
        _det.det_cmd(["py", "h.py"])


@pytest.mark.parametrize("fam,fn,grader", [("blackbox", "decode", "grade_blackbox"),
                                           ("netsurgeon", "edit", "grade_netsurgeon"),
                                           ("probe", "solve", "grade_probe")])
def test_harness_family_grades_a_random_reply_identically(fam, fn, grader):
    import importlib
    m = importlib.import_module(f"crucibleforge.gen.{fam}")
    g = {"type": fam, "level": {"blackbox": 1, "netsurgeon": 1, "probe": 3}[fam]}
    mat = m.materialize(g, release_seed=SEED, case_id="X")
    reply = _code_reply(m.reference(g, release_seed=SEED, case_id="X"), fn)
    _thrice(lambda: getattr(m, grader)(reply, mat["grader_config"]))
    d = getattr(m, grader)(reply, mat["grader_config"])["detail"]
    assert d.endswith(_det.py_note()) and "; py " in d


def test_keyfunc_grades_a_random_reply_identically():
    from crucibleforge.gen import keyfunc as K
    g = {"type": "keyfunc", "level": 3}
    mat = K.materialize(g, release_seed=SEED, case_id="KF-det")
    reply = _code_reply(K.reference(g, release_seed=SEED, case_id="KF-det"), "f")
    _thrice(lambda: graders.grade_python_exec(reply, mat["grader_config"]))
    assert graders.grade_python_exec(reply, mat["grader_config"])["detail"].endswith(_det.py_note())


@pytest.mark.parametrize("fam", ["patchmini", "patchfix"])
def test_codebank_family_grades_a_random_edit_identically(fam):
    import importlib
    m = importlib.import_module(f"crucibleforge.gen.{fam}")
    g = {"type": fam, "level": 1}
    mat = m.materialize(g, release_seed=SEED, case_id="PM-det")
    ref = m.reference(g, release_seed=SEED, case_id="PM-det")
    # the coin at import of the first edited file: one side breaks every test that imports it
    blk = re.search(r"(```python[^\n]*\n)", ref)
    assert blk, "reference has no python file block"
    reply = ref[:blk.end()] + _COIN + "if _COIN or hash('bench-det-2') % 3 == 0:\n    raise ImportError('unlucky')\n" \
        + ref[blk.end():]
    _thrice(lambda: codebank.grade_repo_tests(reply, mat["grader_config"]))
    assert codebank.grade_repo_tests(reply, mat["grader_config"])["detail"].endswith(_det.py_note())


def test_a_41_case_without_the_flag_runs_as_before():
    # no ``deterministic`` key: the 4.1 path (``-I``, the host's child env, no prelude, no py note)
    cfg = {"tests": "", "test_groups": {"g1": "assert f() == 1"}}
    r = graders.grade_python_exec("```python\ndef f():\n    return 1\n```", cfg)
    assert r["rate"] == 1.0 and "; py " not in r["detail"]


def test_pins_track_the_launcher():
    sha = hashlib.sha256(Path(_det.__file__).read_bytes()).hexdigest()[:16]
    import importlib
    for fam in ("blackbox", "netsurgeon", "probe", "keyfunc", "patchfix", "patchmod", "patchmini", "patchhard"):
        assert importlib.import_module(f"crucibleforge.gen.{fam}")._DET_SHA == sha, fam


def test_py_note_wording_matches_planted_opt():
    assert _det.py_note("3.13.14") == "; py 3.13.14"
    assert _det.py_note("3.14.10") == "; py 3.14.10 (not the pinned 3.13.14)"
    from crucibleforge.gen import planted_opt
    assert planted_opt.PINNED_PY == _det.PINNED_PY


def test_regrade_refuses_another_interpreter(monkeypatch):
    monkeypatch.setattr(_det, "grading_python", lambda: "3.14.10")
    assert "not the pinned 3.13.14" in _det.pin_mismatch()
    assert _det.pin_mismatch(version="3.13.14") is None
    assert _det.pin_mismatch({"grader_pins": {"python": "3.14.10"}}) is None


def test_regrade_plan_refuses_every_regrade_on_another_interpreter(monkeypatch):
    import copy
    import json
    from crucibleforge import chains
    fx = json.loads((Path(__file__).parent / "fixtures" / "regrade_20261114.json").read_text(encoding="utf-8"))
    rows = copy.deepcopy(fx["pytrap_stale"]["rows"])
    real = chains._family_source_digest
    monkeypatch.setattr(chains, "_family_source_digest", lambda f: "0" * 12 if f == "pytrap" else real(f))
    import importlib.util
    spec_ = importlib.util.spec_from_file_location("_rg", Path(__file__).parent / "test_regrade_20261114.py")
    rg = importlib.util.module_from_spec(spec_)
    spec_.loader.exec_module(rg)
    specs = {r["chain"]["id"]: rg._spec_from_row(r) for r in rows}
    monkeypatch.setattr(_det, "grading_python", lambda: "3.14.10")
    recs = regrade.plan(rows, specs, "t", pin="3.13.14")
    assert recs and all(r["status"] == "refused" and "not the pinned 3.13.14" in r["why"] for r in recs)
