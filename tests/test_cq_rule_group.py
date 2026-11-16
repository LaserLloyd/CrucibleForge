import json
from pathlib import Path

from crucibleforge import graders
from crucibleforge.cq_rule_group import fix_cfg

PACK = Path(__file__).resolve().parent.parent / "cases/ladder/work/code/classic-code.json"


def _cases():
    return {c["id"][:3]: c for c in json.loads(PACK.read_text(encoding="utf-8"))["cases"] if isinstance(c, dict)}


def _fence(code):
    return "```python\n" + code + "\n```"


def test_violation_fails_only_the_rule_group():
    c = _cases()["CQ2"]
    ref = c["reference"]
    cfg = fix_cfg(c["grader_config"])
    assert list(cfg["test_groups"])[-1] == "source-rule"
    ok = graders.grade_python_exec(_fence(ref), cfg)
    assert ok["rate"] == 1.0, ok["detail"]
    bad = graders.grade_python_exec(_fence(ref + "\n_run = 1\n"), cfg)
    res = {r["id"]: r["pass"] for r in bad["results"]}
    assert res["source-rule"] is False and all(v for k, v in res.items() if k != "source-rule")
    # the 4.1 gate zeroed everything for the same reply
    old = graders.grade_python_exec(_fence(ref + "\n_run = 1\n"), c["grader_config"])
    assert old["rate"] == 0.0


def test_all_three_cases_transform():
    for cid, c in _cases().items():
        cfg = fix_cfg(c["grader_config"])
        assert "_RULE_VIOL" in cfg["tests"] and "source-rule" in cfg["test_groups"], cid
