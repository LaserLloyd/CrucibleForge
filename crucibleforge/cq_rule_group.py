"""FC4 (CQ1-3) scoring fix: the AST source-rule check of a CQ case becomes ONE test group.

In 4.1 the check ran in the shared prelude and raised, so a single banned identifier (for example a helper
that happens to be called ``_cmp``) failed every group at once. ``fix_cfg`` rewrites a CQ grader_config so a
violation is recorded and only the added ``source-rule`` group fails; the behaviour groups still score.
"""
from __future__ import annotations

import copy

SENTINEL_LINE = "for _n in _ast.walk(_tree):"


def fix_cfg(cfg: dict) -> dict:
    cfg = copy.deepcopy(cfg)
    lines = cfg["tests"].split("\n")
    i = next(k for k, l in enumerate(lines) if l.startswith(SENTINEL_LINE))
    j = i + 1
    while j < len(lines) and (lines[j].strip() == "" or lines[j][0] in " \t"):
        j += 1
    block = ["    " + l if l.strip() else l for l in lines[i:j]]
    new = (lines[:i] + ["_RULE_VIOL = None", "try:"] + block +
           ["except AssertionError as _e:", "    _RULE_VIOL = str(_e)"] + lines[j:])
    cfg["tests"] = "\n".join(new)
    groups = dict(cfg["test_groups"])
    groups["source-rule"] = "assert _RULE_VIOL is None, _RULE_VIOL\n"
    cfg["test_groups"] = groups
    return cfg
