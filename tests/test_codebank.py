"""Code bank: edit parsing/applying, the sandboxed group runner, the gated
score and case verification."""
from __future__ import annotations

from pathlib import Path

import pytest

from crucibleforge import codebank as cb
from crucibleforge import graders

needs_sandbox = pytest.mark.skipif(not graders.sandbox_available(),
                                   reason="bwrap sandbox not available")

PROJ = {
    "calc/core.py": "def add(a, b):\n    return a - b\n\n\ndef mul(a, b):\n    return a * b\n",
    "calc/__init__.py": "",
    "tests/test_visible.py": "from calc.core import mul\n\ndef test_mul():\n    assert mul(2, 3) == 6\n",
}
HIDDEN = {
    "test_add.py": "from calc.core import add\nimport pytest\n\n"
                   "@pytest.mark.parametrize('a,b,c', [(1, 2, 3), (0, 0, 0)])\n"
                   "def test_add(a, b, c):\n    assert add(a, b) == c\n\n"
                   "def test_tmp(tmp_path):\n    (tmp_path / 'x').write_text('1')\n    assert (tmp_path / 'x').read_text() == '1'\n",
    "test_mul.py": "from calc.core import mul\n\ndef test_mul():\n    assert mul(4, 5) == 20\n",
}
GROUPS = [{"id": "add", "file": "test_add.py", "kind": "f2p"},
          {"id": "mul", "file": "test_mul.py", "kind": "p2p"}]
FIXED = "def add(a, b):\n    return a + b\n\n\ndef mul(a, b):\n    return a * b\n"
CFG = {"files": PROJ, "hidden": HIDDEN, "groups": GROUPS, "allowed_paths": ["calc/*.py"]}


# ------------------------------------------------------------ parsing
def test_parse_whole_file_with_header_variants():
    for hdr in ("FILE: calc/core.py", "### calc/core.py", "**calc/core.py**", "`calc/core.py`",
                "calc/core.py:"):
        text = f"Here is the fix.\n\n{hdr}\n```python\n{FIXED}```\n"
        edits, probs = cb.parse_edits(text)
        assert [e["path"] for e in edits] == ["calc/core.py"], hdr
        assert edits[0]["content"].strip() == FIXED.strip()


def test_parse_path_in_info_string_and_snippets_without_path_ignored():
    text = "Example:\n```python\nprint(1)\n```\n\n```python calc/core.py\n" + FIXED + "```\n"
    edits, _ = cb.parse_edits(text)
    assert [e["path"] for e in edits] == ["calc/core.py"]


def test_parse_search_replace_inside_and_outside_fence():
    inside = ("calc/core.py\n```python\n<<<<<<< SEARCH\n    return a - b\n=======\n"
              "    return a + b\n>>>>>>> REPLACE\n```\n")
    outside = ("calc/core.py\n<<<<<<< SEARCH\n    return a - b\n=======\n"
               "    return a + b\n>>>>>>> REPLACE\n")
    for text in (inside, outside):
        edits, probs = cb.parse_edits(text)
        assert len(edits) == 1 and edits[0]["kind"] == "sr", text
        tree, applied, errs = cb.apply_edits(PROJ, edits, ["calc/*.py"])
        assert tree["calc/core.py"] == FIXED and not errs


def test_thinking_block_is_ignored():
    text = "<think>FILE: calc/core.py\n```python\nbroken(\n```</think>\nFILE: calc/core.py\n```python\n" + FIXED + "```"
    edits, _ = cb.parse_edits(text)
    assert len(edits) == 1 and "broken" not in edits[0]["content"]


def test_cut_off_block_dropped_only_when_truncated():
    text = "FILE: calc/core.py\n```python\n" + FIXED
    assert cb.parse_edits(text, "length")[0] == []
    assert len(cb.parse_edits(text, "stop")[0]) == 1


# ------------------------------------------------------------ applying
def test_apply_rejects_escape_tests_and_unknown_files():
    edits = [{"kind": "file", "path": "../etc/passwd", "content": "x"},
             {"kind": "file", "path": "/abs.py", "content": "x"},
             {"kind": "file", "path": "tests/test_visible.py", "content": "x"},
             {"kind": "file", "path": "calc/new.py", "content": "x"}]
    tree, applied, errs = cb.apply_edits(PROJ, edits, ["calc/*.py"], new_paths=[])
    assert tree == PROJ and applied == [] and len(errs) == 4


def test_apply_resolves_bare_filename_and_normalises_dots():
    edits = [{"kind": "file", "path": "core.py", "content": FIXED}]
    tree, applied, _ = cb.apply_edits(PROJ, edits, ["calc/*.py"])
    assert applied == ["calc/core.py"]
    edits = [{"kind": "file", "path": "./calc/../calc/core.py", "content": FIXED}]
    assert cb.apply_edits(PROJ, edits, ["calc/*.py"])[1] == ["calc/core.py"]


def test_elided_whole_file_is_not_applied():
    big = {"m.py": "\n".join(f"def f{i}():\n    return {i}\n" for i in range(40))}
    lazy = "def f0():\n    return 99\n\n# ... rest of the file unchanged ...\n"
    tree, applied, errs = cb.apply_edits(big, [{"kind": "file", "path": "m.py", "content": lazy}], ["m.py"])
    assert tree == big and errs and "elides" in errs[0]


def test_search_replace_tolerates_trailing_whitespace_but_not_ambiguity():
    files = {"a.py": "x = 1   \ny = 2\nx = 1   \n"}
    e = [{"kind": "sr", "path": "a.py", "search": "y = 2", "replace": "y = 3"}]
    assert cb.apply_edits(files, e, ["a.py"])[0]["a.py"].startswith("x = 1")
    e = [{"kind": "sr", "path": "a.py", "search": "x = 1", "replace": "x = 0"}]
    assert cb.apply_edits(files, e, ["a.py"])[2]          # two matches -> error


# ------------------------------------------------------------ scoring
def _res(**ok):
    return {k: {"ok": v, "detail": "d"} for k, v in ok.items()}


def test_gate_halves_on_regression_and_empty_scores_zero():
    groups = [{"id": "a", "kind": "f2p"}, {"id": "b", "kind": "f2p"}, {"id": "p", "kind": "p2p"}]
    assert cb.score_groups(groups, _res(a=True, b=True, p=True))["score"] == 1.0
    assert cb.score_groups(groups, _res(a=True, b=False, p=True))["score"] == 0.5
    assert cb.score_groups(groups, _res(a=True, b=True, p=False))["score"] == 0.5
    assert cb.score_groups(groups, _res(a=False, b=False, p=True))["score"] == 0.0
    v = cb.score_groups(groups, _res(a=True, b=True, p=True),
                        extra=[{"id": "scope", "type": "scope", "pass": False, "detail": "deploy"}])
    assert v["score"] == 0.5 and v["grade"] == "fail"


# ------------------------------------------------------------ sandbox
@needs_sandbox
def test_untouched_fails_fix_passes_regression_gated():
    base = cb.run_groups(PROJ, HIDDEN, GROUPS, CFG)
    assert not base["add"]["ok"] and base["mul"]["ok"]
    good = cb.grade_repo_tests(f"FILE: calc/core.py\n```python\n{FIXED}```", CFG)
    assert good["grade"] == "pass" and good["score"] == 1.0, good
    broke = FIXED.replace("return a * b", "return a ** b")
    v = cb.grade_repo_tests(f"FILE: calc/core.py\n```python\n{broke}```", CFG)
    assert v["score"] == 0.5 and [r["pass"] for r in v["results"]] == [True, False]


@needs_sandbox
def test_no_edits_scores_zero_without_running():
    v = cb.grade_repo_tests("I think the bug is in add().", CFG)
    assert v["score"] == 0.0 and v["format_errors"]


@needs_sandbox
def test_printed_fake_results_and_exit_cannot_pass():
    cheat = ("import sys, json\nprint('BENCH_OK_7742')\n"
             "print('x ' + json.dumps({'test': 'test_add', 'ok': True}))\n"
             "def add(a, b):\n    sys.exit(0)\n\ndef mul(a, b):\n    return a * b\n")
    v = cb.grade_repo_tests(f"FILE: calc/core.py\n```python\n{cheat}```", CFG)
    assert v["results"][0]["pass"] is False


@needs_sandbox
def test_hang_and_crash_are_contained_per_group():
    hang = "def add(a, b):\n    while True:\n        pass\n\ndef mul(a, b):\n    return a * b\n"
    cfg = {**CFG, "per_test_timeout_s": 1}
    v = cb.grade_repo_tests(f"FILE: calc/core.py\n```python\n{hang}```", cfg)
    assert not v["results"][0]["pass"] and v["results"][1]["pass"], v
    crash = "import os\nos._exit(3)\n"
    v = cb.grade_repo_tests(f"FILE: calc/core.py\n```python\n{crash}```", CFG)
    assert not any(r["pass"] for r in v["results"])


@needs_sandbox
def test_sandbox_env_is_deterministic_and_offline():
    hidden = {"test_env.py": (
        "import os, socket, subprocess\n"
        "def test_env():\n"
        "    assert os.environ['LC_ALL'] == 'C' and os.environ['TZ'] == 'UTC'\n"
        "    assert subprocess.run(['sort'], input='b\\nB\\na\\n', capture_output=True, text=True).stdout == 'B\\na\\nb\\n'\n"
        "def test_no_network():\n"
        "    s = socket.socket(); s.settimeout(1)\n"
        "    assert s.connect_ex(('1.1.1.1', 53)) != 0\n"
        "def test_no_home():\n"
        f"    assert not os.path.exists({str(Path.home() / '.config')!r})\n"
        f"    assert not os.path.exists({str(Path.home() / '.ssh')!r})\n")}
    r = cb.run_groups({"x.py": ""}, hidden, [{"id": "env", "file": "test_env.py"}], {})
    assert r["env"]["ok"], r


@needs_sandbox
def test_groups_get_fresh_trees():
    hidden = {"test_a.py": "def test_w():\n    open('state.txt','w').write('a')\n",
              "test_b.py": "import os\ndef test_r():\n    assert not os.path.exists('state.txt')\n"}
    r = cb.run_groups({"x.py": ""}, hidden, [{"id": "a", "file": "test_a.py"},
                                              {"id": "b", "file": "test_b.py"}], {})
    assert r["a"]["ok"] and r["b"]["ok"], r


@needs_sandbox
def test_fixtures_monkeypatch_and_raises():
    hidden = {"test_f.py": (
        "import pytest, os\n"
        "@pytest.fixture\n"
        "def db(tmp_path):\n"
        "    p = tmp_path / 'db'\n    p.write_text('x')\n    yield p\n"
        "def test_fx(db, monkeypatch):\n"
        "    monkeypatch.setenv('Q', '1')\n    assert db.read_text() == 'x' and os.environ['Q'] == '1'\n"
        "def test_raises():\n"
        "    with pytest.raises(ValueError, match='bad'):\n        raise ValueError('bad thing')\n"
        "def test_approx():\n    assert 0.1 + 0.2 == pytest.approx(0.3)\n")}
    r = cb.run_groups({"x.py": ""}, hidden, [{"id": "f", "file": "test_f.py"}], {})
    assert r["f"]["ok"] and len(r["f"]["tests"]) == 3, r


@needs_sandbox
@pytest.mark.skipif(cb.node_path() is None, reason="node not installed")
def test_node_group_runs():
    files = {"lib.mjs": "export const twice = (x) => x * 2;\n"}
    hidden = {"t.test.mjs": ("import assert from 'node:assert/strict';\n"
                             "import { twice } from '../lib.mjs';\n"
                             "export function testTwice() { assert.equal(twice(2), 4); }\n"
                             "export async function testFail() { assert.equal(twice(2), 5); }\n")}
    r = cb.run_groups(files, hidden, [{"id": "js", "file": "t.test.mjs", "runner": "node"}], {})
    tests = {t["test"]: t["ok"] for t in r["js"]["tests"]}
    assert tests == {"testTwice": True, "testFail": False}, r


@needs_sandbox
def test_verify_case_catches_weak_tests_and_bad_reference():
    case = {"grader_config": CFG, "reference_files": {"calc/core.py": FIXED},
            "mutants": {"swap": {"calc/core.py": FIXED.replace("a + b", "b + a")}}}
    errs = cb.verify_case(case)
    assert any("mutant 'swap'" in e for e in errs)            # a+b == b+a: tests can't bite
    case["mutants"] = {"off_by_one": {"calc/core.py": FIXED.replace("a + b", "a + b + 1")}}
    assert cb.verify_case(case) == []
    case["reference_files"] = {"calc/core.py": PROJ["calc/core.py"]}
    assert any("reference FAILS" in e for e in cb.verify_case(case))


def test_min_sqlite_refuses():
    assert cb._check_min_sqlite({"min_sqlite": "99.0"})
    assert cb._check_min_sqlite({"min_sqlite": "3.30"}) is None
    v = cb.grade_repo_tests("x", {**CFG, "min_sqlite": "99.0"})
    assert v["grade"] == "skipped"


@needs_sandbox
def test_memory_hog_is_contained():
    hog = "def add(a, b):\n    x = bytearray(3 << 30)\n    return a + b\n\ndef mul(a, b):\n    return a * b\n"
    v = cb.grade_repo_tests(f"FILE: calc/core.py\n```python\n{hog}```", CFG)
    assert not v["results"][0]["pass"] and "MemoryError" in v["results"][0]["detail"], v
    assert v["results"][1]["pass"]


# ---------------------------------------------- integrity (review 2026-10-26)
@needs_sandbox
@pytest.mark.parametrize("forge", [
    "getattr(__import__('o' + 's'), '_ex' + 'it')(0)\n",                      # obfuscated exit at import
    "import threading, time\nthreading.Thread(target=lambda: (time.sleep(0.01), getattr(__import__('o'+'s'), '_ex'+'it')(0))).start()\ntime.sleep(1)\n",
    "import sys\nsys.excepthook = None\nimport atexit\natexit.register(lambda: None)\nraise SystemExit(0)\n",
])
def test_blanket_forgery_is_caught_by_the_canary(forge):
    bad = forge + FIXED.replace("a + b", "a - b")
    v = cb.grade_repo_tests(f"FILE: calc/core.py\n```python\n{bad}```", CFG)
    assert v["grade"] == "fail" and v["score"] == 0.0, v


@needs_sandbox
def test_obfuscated_exit_zero_is_tamper():
    bad = "getattr(__import__('o' + 's'), '_ex' + 'it')(0)\n" + FIXED
    v = cb.grade_repo_tests(f"FILE: calc/core.py\n```python\n{bad}```", CFG)
    assert v.get("tamper") and v["score"] == 0.0, v


@needs_sandbox
@pytest.mark.skipif(cb.node_path() is None, reason="node not installed")
def test_node_exit_zero_forgery_is_tamper():
    files = {"lib.mjs": "process.exit(0);\nexport const twice = (x) => x * 3;\n"}
    hidden = {"t.test.mjs": ("import assert from 'node:assert/strict';\n"
                             "import { twice } from '../lib.mjs';\n"
                             "export function testTwice() { assert.equal(twice(2), 4); }\n")}
    r = cb.run_groups(files, hidden, [{"id": "js", "file": "t.test.mjs", "runner": "node"}], {})
    assert r["js"]["tamper"] and not r["js"]["ok"], r


@needs_sandbox
def test_each_function_isolated_and_counted():
    hidden = {"test_x.py": "import calc.core as c\n\ndef test_one():\n    assert c.mul(2, 2) == 4\n\n"
                           "def test_two():\n    import os; os._exit(3)\n\ndef test_three():\n    assert c.mul(1, 1) == 1\n"}
    r = cb.run_groups(PROJ, hidden, [{"id": "x", "file": "test_x.py"}], {})["x"]
    assert not r["ok"] and {t["test"]: t["ok"] for t in r["tests"]} == {"test_one": True, "test_two": False,
                                                                      "test_three": True} and not r["tamper"], r
