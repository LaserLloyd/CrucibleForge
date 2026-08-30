"""Static verification of the case set — runs offline, no model needed.

`crucibleforge cases verify` (also run by the test-suite) checks every case for:
- required fields per grader / rubric, valid difficulty, unique ids;
- ``python_exec`` cases: the bundled ``reference`` solution (when present)
  passes the case's own tests inside the real sandbox — so a typo in a test
  can't silently fail every model;
- ``numeric`` / ``exact`` / ``contains`` cases: an optional ``verify`` block
  (``{"python": "<expr>"}``) whose evaluated value must equal the gold answer,
  so brute-force-checkable answers are re-derived rather than trusted;
- ``tool_call`` / ``tool_parallel``: expected tool names exist in ``tools``;
- generated long-context cases materialise and contain their needle.
"""
from __future__ import annotations

from .api import ChatResult
from .graders import grade

_REQ = {
    "python_exec": ["tests"], "numeric": ["answer"], "contains": ["needles"],
    "exact": [], "reference": ["reference"], "checks": ["checks"],
    "tool_call": [], "tool_parallel": ["expect_calls"], "tool_loop": [],
}


def _check(case: dict) -> list[str]:
    errs: list[str] = []
    if case.get("difficulty", "medium") not in ("easy", "medium", "hard"):
        errs.append("bad difficulty")
    if "max_tokens" not in case:
        errs.append("missing max_tokens")
    if not (case.get("prompt") or case.get("turns") or case.get("tool_script")):
        errs.append("no prompt/turns/tool_script")
    g = case.get("grader")
    gc = case.get("grader_config") or {}
    if g:
        if g not in _REQ:
            errs.append(f"unknown grader {g}")
        else:
            for k in _REQ[g]:
                if k not in gc:
                    errs.append(f"grader_config missing {k}")
            if g == "exact" and not (gc.get("answer") is not None or gc.get("answers")):
                errs.append("exact grader needs answer/answers")
            if g == "tool_call" and not (gc.get("expect_tool") or gc.get("expect_no_tool")):
                errs.append("tool_call needs expect_tool or expect_no_tool")
    elif not (case.get("rubric") or case.get("turns") or case.get("tool_script")
              or case.get("category") == "perf"):
        errs.append("no grader and no rubric")
    # tool names must exist
    tools = {t["function"]["name"] for t in case.get("tools") or [] if "function" in t}
    if g == "tool_call" and gc.get("expect_tool") and gc["expect_tool"] not in tools:
        errs.append(f"expect_tool {gc['expect_tool']} not in tools")
    if g == "tool_parallel":
        for spec in gc.get("expect_calls", []):
            if spec["name"] not in tools:
                errs.append(f"expected call {spec['name']} not in tools")
    for step in case.get("tool_script") or []:
        if step.get("expect_tool") and step["expect_tool"] not in tools:
            errs.append(f"tool_script expect_tool {step['expect_tool']} not in tools")
    # generated long context: needle must be present
    if case.get("generator"):
        gen = case["generator"]
        needles = [gen.get("needle")] if gen.get("needle") else gen.get("needles", [])
        for n in needles:
            if n and n not in case["prompt"]:
                errs.append("generated prompt lacks its needle")
        if gen.get("type") == "count":
            # the question may quote the marker; only the document body counts
            body = case["prompt"][: case["prompt"].rfind(gen["question"])]
            if body.count(gen["marker"]) != int(gen["count"]):
                errs.append("count generator: marker count mismatch")
    # reference solution must pass its own tests (real sandbox)
    if g == "python_exec" and case.get("reference"):
        res = ChatResult(response_text=f"```python\n{case['reference']}\n```")
        v = grade(res, case)
        if v["grade"] != "pass":
            errs.append(f"reference solution FAILS own tests: {v['detail']}")
    # answer re-derivation
    ver = case.get("verify")
    if ver and ver.get("python"):
        try:
            got = eval(ver["python"], {"__builtins__": __builtins__}, {})  # noqa: S307 (author-controlled)
        except Exception as e:  # noqa: BLE001
            errs.append(f"verify expr raised {e!r}")
        else:
            want = gc.get("answer")
            if want is None and gc.get("needles"):
                want = gc["needles"][0]
            if want is None and gc.get("answers"):
                want = gc["answers"][0]
            if isinstance(want, (int, float)):
                try:
                    if abs(float(got) - float(want)) > float(gc.get("tolerance", 1e-6)):
                        errs.append(f"verify: derived {got!r} != gold {want!r}")
                except (TypeError, ValueError):
                    errs.append(f"verify: derived {got!r} not numeric vs {want!r}")
            else:
                if str(got).strip().lower() != str(want).strip().lower():
                    errs.append(f"verify: derived {got!r} != gold {want!r}")
    return errs


def verify_all(cases: list[dict], verbose: bool = False) -> int:
    bad = 0
    n_ref = n_ver = 0
    for c in cases:
        errs = _check(c)
        n_ref += bool(c.get("reference"))
        n_ver += bool(c.get("verify"))
        if errs:
            bad += 1
            print(f"FAIL {c['category']}/{c['id']}: " + "; ".join(errs))
        elif verbose:
            print(f"ok   {c['category']}/{c['id']}")
    print(f"{len(cases)} cases checked, {bad} with problems "
          f"({n_ref} reference solutions executed, {n_ver} answers re-derived)")
    return 1 if bad else 0
