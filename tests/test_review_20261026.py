"""Regression tests for the 2026-10-26 code-bank review findings."""
import json
from pathlib import Path

import pytest

from crucibleforge import codebank as cb, codeenv, graders

CASES = Path(__file__).resolve().parent.parent / "cases"
pytestmark = pytest.mark.skipif(not cb._g.sandbox_available(), reason="needs bwrap")

HIDDEN = {"test_g.py": "from m import ok, crash\n"
                       "def test_a():\n    assert ok() == 1\n"
                       "def test_b():\n    crash()\n    assert False\n"
                       "def test_c():\n    assert ok() == 2\n"}
GROUPS = [{"id": "g", "file": "test_g.py", "kind": "f2p"}]


@pytest.mark.parametrize("crash", ["import os\n    os._exit(0)",
                                   "import ctypes\n    ctypes.string_at(0)",
                                   "import signal\n    signal.alarm(0); signal.signal(signal.SIGALRM, signal.SIG_IGN)\n"
                                   "    while True: pass"])
def test_crash_after_a_passing_test_is_not_a_pass(crash):
    tree = {"m.py": f"def ok():\n    return 1\ndef crash():\n    {crash}\n"}
    r = cb.run_groups(tree, HIDDEN, GROUPS, {"group_timeout_s": 4})["g"]
    assert not r["ok"], r


def test_expected_tests_static():
    assert cb.expected_tests(HIDDEN["test_g.py"]) == {"test_a", "test_b", "test_c"}
    assert cb.expected_tests("export function testA() {}\nexport async function testB() {}\n", "node") == {"testA", "testB"}


def _case(cat, cid):
    return {c["id"]: c for c in json.loads((CASES / f"{cat}.json").read_text(encoding="utf-8"))}[cid]


def test_python_nonce_forge_fails_closed():
    a1 = _case("codebank", "CB-A1-tabsplit-cents")
    init = a1["grader_config"]["files"]["tabsplit/__init__.py"]
    forge = ("import __main__ as _m, os as _o\n"
             "_m._out.write(_m.nonce + ' {\"test\": \"t\", \"ok\": true}\\n'); _o._exit(0)\n") + init
    v = cb.grade_repo_tests("FILE: tabsplit/__init__.py\n```python\n" + forge + "```\n", a1["grader_config"], "stop")
    assert v["grade"] == "fail" and v["score"] == 0 and v.get("tamper")


def test_node_stdout_forge_fails_closed():
    b3 = _case("codebank", "CB-B3-mdlite-links")
    md = b3["grader_config"]["files"]["mdlite.mjs"]
    patch = ("const __w = process.stdout.write.bind(process.stdout);\n"
             "process.stdout.write = (s, ...a) => __w(String(s).split('\"ok\":false').join('\"ok\":true'), ...a);\n")
    v = cb.grade_repo_tests("FILE: mdlite.mjs\n```javascript\n" + patch + md + "```\n", b3["grader_config"], "stop")
    assert v["grade"] == "fail" and v.get("tamper")


def test_legit_main_guard_is_not_tamper():
    assert not cb.tamper_findings({"a.py": 'if __name__ == "__main__":\n    main()\n'}, {"a.py": ""})


def test_hedged_predict_answer_scores_only_the_last_line():
    c1 = _case("codebank", "CB-C1-predict-python")
    golds = c1["golds"]
    hedge = "\n".join(f"{q}: {g}\n{q}: wrong guess" for q, g in golds.items())
    assert graders.grade_checks(hedge, c1["grader_config"])["rate"] == 0.0
    right = "\n".join(f"{q}: wrong guess\n{q}: {g}" for q, g in golds.items())
    assert graders.grade_checks(right, c1["grader_config"])["rate"] == 1.0


@pytest.mark.parametrize("name,args", [("read_file", '{"path": "README.md", "start_line": "first"}'),
                                       ("write_file", '{"path": "larder/units.py", "content": null}'),
                                       ("replace_in_file", '{"path": "larder/units.py", "old": "a", "new": "b", "count": "x"}')])
def test_bad_tool_argument_types_are_tool_errors(name, args):
    env = codeenv.RepoEnv(_case("codeagent", "CB-F1-larder-bughunt")["env"])
    assert env.call(name, args).startswith("error: bad arguments")


def test_estimate_with_reasoning_counts_the_replay():
    msgs = [{"role": "assistant", "content": "x" * 35, "reasoning_content": "y" * 3500}]
    assert codeenv.estimate_tokens(msgs, with_reasoning=True) >= 1000 + codeenv.SCHEMA_TOKENS
    assert codeenv.estimate_tokens(msgs) == 10
