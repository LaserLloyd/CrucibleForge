"""Every grader a ladder case uses must have a verify_cases._REQ row.

A missing row makes verify_cases._check report "unknown grader X" for every
case of that family, and test_case_set_verifies_clean fails only after its
~12-minute run (2026-11-14: implied L5 ladder set). This test finds it in
seconds: materialise every ladder case (drafts too) and check the grader
name and its required grader_config keys.
"""
import pytest
from crucibleforge import config, verify_cases
from crucibleforge.ladder import inline_cases


def _ladder_cases():
    cases = inline_cases(include_drafts=True)
    for c in cases:
        if c.get("generator"):
            config._materialize_generated(c)
    return cases


@pytest.mark.live_ladder      # reads the live cases/ladder packs (conftest guard)
def test_every_ladder_grader_has_a_req_row():
    cases = _ladder_cases()
    graders = {c["grader"] for c in cases if c.get("grader")}
    assert graders, "no graded ladder cases found"
    missing = sorted(g for g in graders if g not in verify_cases._REQ)
    assert not missing, missing
    gaps = [(c["id"], k) for c in cases if c.get("grader")
            for k in verify_cases._REQ[c["grader"]] if k not in (c.get("grader_config") or {})
            and not (c["grader"] == "python_exec" and k == "tests"
                     and (c.get("grader_config") or {}).get("test_groups"))]
    assert not gaps, gaps


def test_generated_family_rows_present():
    for g, keys in {"implied": ["seed", "tier"], "blackbox": ["seed", "tier"],
                    "probe": ["seed", "tier"], "optimize": ["seed", "level"]}.items():
        assert verify_cases._REQ[g] == keys, g
