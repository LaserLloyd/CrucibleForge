"""4.5.1 tool-call line-break tolerance (PLAN-451-LIMITS step 4b, lead ruling 2026-11-25).

Two hosted models (a raw stream capture on 2026-11-25 shows it, streamed and not streamed) write several tool calls on
one line: ``CALL find_person {...}CALL find_person {...}`` or ``...the room.CALL get_person {...}``. The console's
parser (``gen/toolworld.parse_calls``, frozen) reads one call per line from the line start, so every call after the
first was lost and the task failed for a format slip the task text does ask about ("one line per call").

:func:`split` inserts a line break before a ``CALL <tool> {`` that does not start its line, only when ``<tool>`` is one
of the tools the task offered (read from the task's own system text, else the family's tool list) and the JSON object
after it parses (``json.JSONDecoder.raw_decode``), and (review 3, rev ct-2) only when the ``CALL`` directly follows a
``}`` (the previous call's object) or end-of-sentence punctuation (``. ! ? )``), spaces allowed between: never after
a quote, a backtick, a colon or a letter (``I should not CALL ...``, ``Earlier I ran CALL ...``, a
narrated ``Turn 1: CALL ...``), never inside a ``` fence or a
quoted span (an open ``"`` or backtick on the line). A ``CALL`` at the line start goes to the unchanged parser as
before; prose that mentions CALL, an unknown tool, or an object that does not parse is left as written. Only the
toolworld/toolgap families use it (``runner._run_console_loop``); the stored conversation keeps the model's own reply,
and the row says ``calltol: REV`` plus ``console.calls_without_line_break: n``."""
from __future__ import annotations

import json
import re

#: revision stamped on every console row made with the tolerance in force
REV = "ct-2"
FAMILIES = ("toolworld", "toolgap")
_CALL = re.compile(r"CALL\s+`?([A-Za-z_]\w*)`?\s*[:(]?\s*(?=\{)")
_OFFERED = re.compile(r"^- ([A-Za-z_]\w*) \{", re.M)
_DEC = json.JSONDecoder()


def offered_tools(system: str | None, family: str | None = None) -> frozenset[str]:
    """The tools a task's system text lists (``- name {...}`` lines); the family's whole list when none is found."""
    names = frozenset(_OFFERED.findall(system or ""))
    if names:
        return names
    if family == "toolworld":
        from .gen import toolworld
        return frozenset(toolworld.TOOLS_DOC)
    if family == "toolgap":
        from .gen import toolgap
        return frozenset(toolgap.TOOLS_DOC)
    return frozenset()


def _line_start_call(line: str) -> int | None:
    """Where the parser's own line-start CALL begins in ``line`` (None: the line has none)."""
    from .gen.toolworld import CALL_RE
    if not CALL_RE.match(line):
        return None
    m = _CALL.search(line)
    return m.start() if m else None


#: what may stand right before a glued call (spaces allowed between): the previous call's ``}`` or sentence end
_GLUE_BEFORE = frozenset("}.!?)")


def _glue_point(line: str, p: int) -> bool:
    """True when the ``CALL`` at ``p`` follows ``}`` or end-of-sentence punctuation and is outside a quoted span."""
    before = line[:p].rstrip(" \t")
    if not before or before[-1] not in _GLUE_BEFORE:
        return False
    # an open double quote or backtick before it: the CALL is quoted text (JSON strings close their own quotes)
    return before.count("`") % 2 == 0 and (before.count('"') - before.count('\\"')) % 2 == 0


def split(reply: str | None, tools) -> tuple[str, int]:
    """(``reply`` with a line break before each tolerable mid-line call, how many were split)."""
    if not reply or "CALL" not in reply or not tools:
        return reply or "", 0
    out, n, fenced = [], 0, False
    for line in reply.split("\n"):
        if line.lstrip().startswith("```"):
            fenced = not fenced
            out.append(line)
            continue
        if fenced:
            out.append(line)
            continue
        own = _line_start_call(line)
        cuts = []
        for m in _CALL.finditer(line):
            p = m.start()
            if p == 0 or p == own or m.group(1) not in tools or not _glue_point(line, p):
                continue
            try:
                obj, _end = _DEC.raw_decode(line, m.end())
            except ValueError:
                continue
            if isinstance(obj, dict):
                cuts.append(p)
        for p in reversed(cuts):
            line = line[:p] + "\n" + line[p:]
        n += len(cuts)
        out.append(line)
    return ("\n".join(out), n) if n else (reply, 0)


def count(reply: str | None, tools) -> int:
    return split(reply, tools)[1]


def glued_turns(row: dict) -> int:
    """Assistant turns of a stored console row that hold a call :func:`split` would separate."""
    fam = (row.get("console") or {}).get("family") or row.get("family")
    conv = row.get("tool_conversation") or []
    if fam not in FAMILIES and not any(m.get("role") == "system" for m in conv):
        return 0
    system = next((m.get("content") for m in conv if m.get("role") == "system"), None) or row.get("system")
    tools = offered_tools(system, fam)
    return sum(1 for m in conv if m.get("role") == "assistant" and count(m.get("content"), tools))


def superseded(row: dict) -> bool:
    """A toolworld/toolgap row made before the tolerance whose stored turns hold a glued call: owed again."""
    if row.get("calltol"):
        return False
    fam = (row.get("console") or {}).get("family") or row.get("family")
    if fam not in FAMILIES:
        return False
    return glued_turns(row) > 0
