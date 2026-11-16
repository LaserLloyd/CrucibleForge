"""Was a scripted tool-use task done right? (lead 2026-11-15 08:27, the ``half_task`` unit score)

The pass half of a tool-use run used to be the strict grade: every step right. A run that did the task
right but made an EXTRA read-only lookup beside a correct step (searched a room twice, looked up a
booking before cancelling it) lost the whole half, the same as a run that booked on someone else's
number. Now the task is done when every failed step failed only in a harmless way:

* a tool step: one call is the due call with the right arguments; every other call is read-only;
* a prose step (the model was to answer or ask): every call it made is read-only, it ALSO wrote a
  reply, and that reply passes the step's own needles (the user got the right answer).

Still a failed task: a write call that was not due, a wrong argument on a due call, a prose step with a
call and no text, a wrong or missing answer, a run ended on the context cap, a step never reached.

Each tool's effect comes from the case: ``meta.tool_effects`` {tool name: "read" | "write"}. ``meta`` is
outside every content and grading hash (version._content_view / _hash_case_file / grading_signature),
so declaring effects moves no stored row's stamp. An undeclared tool counts as ``write`` (fail closed).

Rows made under the tool revision (``tool_call_feedback: all``) carry each step's ``calls`` and ``said``
(and a prose step's ``text``) on its element; older rows are read from ``tool_conversation`` (one
assistant message per step), and a row that cannot be aligned keeps its strict grade.
"""
from __future__ import annotations

from .api import strip_thinking_tags
from .graders import grade_tool_call
from .stepgrade import grade_answer_step

EFFECTS = ("read", "write")


def tool_effects(case: dict) -> dict[str, str]:
    """{tool name: read|write} for every tool of the case; an undeclared or unknown effect is write."""
    declared = ((case.get("meta") or {}).get("tool_effects") or {})
    out = {}
    for t in case.get("tools") or []:
        name = (t.get("function") or {}).get("name")
        if name:
            out[name] = declared.get(name) if declared.get(name) in EFFECTS else "write"
    return out


def effects_problems(case: dict) -> list[str]:
    """What is wrong with a case's effect markers ([] = every tool declares read or write)."""
    declared = ((case.get("meta") or {}).get("tool_effects") or {})
    names = [(t.get("function") or {}).get("name") for t in case.get("tools") or []]
    out = [f"tool {n!r} has no effect (meta.tool_effects: read|write)" for n in names if n not in declared]
    out += [f"tool {n!r}: effect {v!r} is not read|write" for n, v in declared.items() if v not in EFFECTS]
    out += [f"meta.tool_effects names {n!r}, which is not a tool of the case" for n in declared if n not in names]
    return out


def _norm(calls) -> list[dict]:
    out = []
    for c in calls or []:
        f = c.get("function") if isinstance(c.get("function"), dict) else c
        out.append({"name": f.get("name"), "arguments": f.get("arguments")})
    return out


def _turns(row: dict, n_steps: int) -> list[tuple[list[dict], str]] | None:
    """Per step: (calls, reply text), from the elements (new rows) or the stored conversation."""
    els = (row.get("elements") or {}).get("results") or []
    if len(els) == n_steps and all("said" in e for e in els):
        return [(_norm(e.get("calls")), str(e.get("text") or ("x" if e.get("said") else ""))) for e in els]
    conv = row.get("tool_conversation") or []
    asst = [m for m in conv if m.get("role") == "assistant"]
    if len(asst) != n_steps:
        return None          # a parallel batch or a cut-short row: no safe alignment
    return [(_norm(m.get("tool_calls") or m.get("tool_calls_not_sent")),
             strip_thinking_tags(str(m.get("content") or "")).strip()) for m in asst]


def _step_ok(step: dict, calls: list[dict], text: str, eff: dict[str, str]) -> bool:
    if step.get("expect_tool"):
        spec = {"expect_tool": step["expect_tool"], "required_args": step.get("required_args", {}),
                "forbid_args": step.get("forbid_args", {}), "max_calls": 1}
        for i, c in enumerate(calls):
            if c.get("name") == step["expect_tool"] and grade_tool_call([c], "", spec)["grade"] == "pass":
                rest = calls[:i] + calls[i + 1:]
                return all(eff.get(x.get("name")) == "read" for x in rest)
        return False
    # a prose step: read-only calls at most, AND a reply that passes the step
    if not calls or not all(eff.get(x.get("name")) == "read" for x in calls):
        return False
    return bool(text) and grade_answer_step(step, text, False)["grade"] == "pass"


def task_done(row: dict, case: dict) -> bool:
    """True when the run did the task right (see the module doc). Fails closed."""
    if row.get("grade") == "pass":
        return True
    if row.get("grade") != "fail" or row.get("end_reason") == "context cap":
        return False
    script = case.get("tool_script") or []
    els = (row.get("elements") or {}).get("results") or []
    if not script or len(els) != len(script):
        return False
    turns = _turns(row, len(script))
    if turns is None:
        return False
    eff = tool_effects(case)
    for k, e in enumerate(els):
        if e.get("pass"):
            continue
        if e.get("not_reached"):
            return False
        calls, text = turns[k]
        if not _step_ok(script[k], calls, text, eff):
            return False
    return True
