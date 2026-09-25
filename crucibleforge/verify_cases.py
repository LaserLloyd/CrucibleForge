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


def _check_step_args(step: dict) -> list[str]:
    """``required_args`` / ``forbid_args`` are {arg: [substring, ...]};
    ``forbid_args`` only makes sense on a step that expects a call; ``re:``
    needles must compile."""
    import re
    errs = []
    for key in ("required_args", "forbid_args"):
        spec = step.get(key)
        if spec is None:
            continue
        if not isinstance(spec, dict):
            errs.append(f"{key} must map arg -> [substrings]")
            continue
        for arg, subs in spec.items():
            if not isinstance(subs, list) or not all(isinstance(x, str) for x in subs):
                errs.append(f"{key}.{arg} must be a list of strings")
            elif key == "forbid_args" and not subs:
                errs.append(f"forbid_args.{arg} is empty")
    if step.get("forbid_args") and not step.get("expect_tool"):
        errs.append("forbid_args on a step that expects no tool call")
    for grp in step.get("answer_contains") or []:
        for n in grp if isinstance(grp, list) else [grp]:
            if isinstance(n, str) and n.startswith("re:"):
                try:
                    re.compile(n[3:])
                except re.error as e:
                    errs.append(f"bad regex needle {n!r}: {e}")
    return errs


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
    if case.get("rubric"):
        from .judge import RUBRICS
        if case["rubric"] not in RUBRICS:
            errs.append(f"unknown rubric {case['rubric']}")
    errs += _check_session_checks(case)
    errs += _check_judge_elements(case)
    errs += _check_tags(case)
    if g == "exact" and gc.get("elements") == "positions":
        from .graders import _normalize
        gold = _normalize(str((gc.get("answers") or [gc.get("answer")])[0])).split()
        labels = gc.get("element_labels")
        if labels is not None and len(labels) != len(gold):
            errs.append(f"element_labels: {len(labels)} labels for {len(gold)} answer words")
        if labels is not None and len(set(labels)) != len(labels):
            errs.append("element_labels: duplicate label")
    # tool names must exist
    tools = {t["function"]["name"] for t in case.get("tools") or [] if "function" in t}
    if g == "tool_call" and gc.get("expect_tool") and gc["expect_tool"] not in tools:
        errs.append(f"expect_tool {gc['expect_tool']} not in tools")
    if g == "tool_parallel":
        for spec in gc.get("expect_calls", []):
            if spec["name"] not in tools:
                errs.append(f"expected call {spec['name']} not in tools")
    for n, step in enumerate(case.get("tool_script") or [], 1):
        if step.get("expect_tool") and step["expect_tool"] not in tools:
            errs.append(f"tool_script expect_tool {step['expect_tool']} not in tools")
        errs += [f"tool_script step {n}: {e}" for e in _check_step_args(step)]
    if g == "tool_call":
        errs += _check_step_args(gc)
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


# required keys per session_checks type (see crucibleforge/session_checks.py)
_CHECK_REQ = {"no_puppeting": ["names"], "forbid_regex": ["pattern"],
              "require_regex": ["pattern"], "require_all": ["needles"],
              "ooc_reply": [], "ooc_field": ["field"], "tense": ["want"],
              "word_range": [], "distinct_regex": ["pattern"],
              "require_spread": ["pattern", "parts", "min_parts"],
              "distinct_terms": ["pattern", "min_distinct"]}
_CHECK_GROUPS = ("identity", "continuity", "ooc", "constraint")


def _check_session_checks(case: dict) -> list[str]:
    """A case's deterministic ``checks`` must be well-formed: known type and
    group, required keys, compilable regex, turn indexes inside the script —
    a broken check would silently fail (or pass) every model."""
    import re

    from .session_checks import CHECKS, SCOPES
    errs: list[str] = []
    specs = case.get("checks")
    if specs is None:
        return errs
    if case.get("grader"):
        errs.append("session 'checks' on a case with an objective grader")
    n_turns = len(case.get("turns") or []) or 1
    seen: set[str] = set()
    for c in specs:
        cid = c.get("id") or "?"
        if cid in seen:
            errs.append(f"duplicate check id {cid}")
        seen.add(cid)
        t = c.get("type")
        if t not in CHECKS:
            errs.append(f"check {cid}: unknown type {t!r}")
            continue
        for k in _CHECK_REQ.get(t, []):
            if k not in c:
                errs.append(f"check {cid}: missing {k}")
        if c.get("group", "constraint") not in _CHECK_GROUPS:
            errs.append(f"check {cid}: unknown group {c.get('group')!r}")
        if c.get("scope", "narration") not in SCOPES:
            errs.append(f"check {cid}: unknown scope {c.get('scope')!r}")
        for i in c.get("turns") or []:
            if not 1 <= int(i) <= n_turns:
                errs.append(f"check {cid}: turn {i} outside 1..{n_turns}")
        if c.get("pattern"):
            try:
                rx = re.compile(c["pattern"])
            except re.error as e:
                errs.append(f"check {cid}: bad regex ({e})")
            else:
                if t == "distinct_regex" and rx.groups != 1:
                    errs.append(f"check {cid}: distinct_regex needs exactly one group")
        if t == "require_spread" and all(k in c for k in ("parts", "min_parts")):
            try:
                n, m = int(c["parts"]), int(c["min_parts"])
            except (TypeError, ValueError):
                errs.append(f"check {cid}: parts/min_parts must be integers")
            else:
                if not 1 <= m <= n:
                    errs.append(f"check {cid}: need 1 <= min_parts <= parts (got {m}/{n})")
        if t == "distinct_terms" and "min_distinct" in c:
            try:
                if int(c["min_distinct"]) < 1:
                    errs.append(f"check {cid}: min_distinct must be >= 1")
            except (TypeError, ValueError):
                errs.append(f"check {cid}: min_distinct must be an integer")
    return errs


def _check_tags(case: dict) -> list[str]:
    """Grading tags (config.CASE_TAGS): closed value sets, and each tag on a
    case it can mean something for — ``explicit_required`` / ``willing:
    must_write`` on an erotic-rubric case, ``willing: must_refuse`` on a
    ``safety`` case (the judge's ``refused`` / ``provided_harmful_content``
    flags are what score it)."""
    from .config import case_tag_errors
    errs = case_tag_errors(case)
    erotic = case.get("rubric") in ("nsfw", "nsfw_craft", "erp_session")
    if case.get("explicit_required") and not erotic:
        errs.append("explicit_required on a non-erotic rubric")
    if case.get("willing") == "must_write" and not erotic:
        errs.append("willing=must_write on a non-erotic rubric")
    if case.get("willing") == "must_refuse" and case.get("rubric") != "safety":
        errs.append("willing=must_refuse needs rubric safety")
    return errs


def _check_judge_elements(case: dict) -> list[str]:
    """``judge_elements`` (judge.py): each needs an id and a question, needs a
    rubric to be judged under, and a ``dim`` must name a dimension or flag of
    that rubric — otherwise a failed element would silently count nowhere."""
    els = case.get("judge_elements")
    if els is None:
        return []
    from .judge import RUBRICS
    errs: list[str] = []
    rub = RUBRICS.get(case.get("rubric") or "")
    if not rub:
        return ["judge_elements on a case without a known rubric"]
    seen: set[str] = set()
    for e in els:
        eid = e.get("id") or "?"
        if eid in seen:
            errs.append(f"duplicate judge element {eid}")
        seen.add(eid)
        if not e.get("q"):
            errs.append(f"judge element {eid}: missing q")
        if e.get("dim") and e["dim"] not in rub["dims"] + rub["flags"]:
            errs.append(f"judge element {eid}: dim {e['dim']!r} not in rubric {case['rubric']}")
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
