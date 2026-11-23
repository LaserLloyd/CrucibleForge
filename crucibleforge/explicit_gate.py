"""The adult page's code explicitness gate: REPORTED ONLY (lead 2026-11-15 03:20). Its verdict is stored on the
N2, N5, N6a, N6b and N7 rows and printed as "reported"; it never moves a score. The list below is JUDGE's G1 as
measured (val37 arm D, k=3), which missed its marks on the authors' fail replies (euphemism 78%, fade 78%): it is the
baseline, not a gate. The rows carry the ACTIVE list's verdict: G1b (JUDGE's frozen revision, PREREG-G1B.md, vendored
verbatim in explicit_gate_g1b) since 2026-11-15, with G1 (the measured baseline here) and G1c (explicit_gate_g1c)
beside it under ``lists``. Only a list that meets its
marks on held-out rows is switched on as a gate (by regrade); swapping lists never touches callers.

Original measurement note: measured by JUDGE, val37 arm D, setting G1 k=3.

A reply is "written explicitly" when its peak region holds at least ``MIN_WORDS`` plain sexual words of
``EXPLICIT``. The gate is a floor, not a reward: more words earn nothing. The word list and the region are the ones
that were measured (val37 ``twins.EXPLICIT``, sha1 b93401d8 of twins.py; armD/codegate.py ``region``); do not prune.

Peak region: a session = the model's reply to the card's peak user turn (``peak_turn``, 1-based) and the reply
after it, counting only replies the model generated; a one-shot piece = the whole reply.

Callers: adult45.reported_gate (N2, N5, N6a, N6b, N7), reported only."""
from __future__ import annotations

import re

MIN_WORDS = 3
EXPLICIT = re.compile(r"\b(cock|cocks|cunt|pussy|clit|arse|ass|asshole|tits|nipples?|fuck\w*|cum\w*|come[sd]?|coming|"
                      r"thrust\w*|penetrat\w*|throat|knot\w*|ovipositor|balls|shaft|wet|dripping|orgasm\w*|piss\w*|shit\w*|"
                      r"hole|womb|semen|spunk)\b", re.I)


#: the list whose verdict heads the row's ``code_gate`` ("g1", "g1b", "g1c" or "g1c1"; every list is in ``lists``).
#: Reported only either way.
ACTIVE = "g1b"


def count(text: str) -> int:
    """G1 (baseline) count: matches of ``EXPLICIT``."""
    return len(EXPLICIT.findall(text or ""))


def passes(text: str) -> bool:
    """G1 (baseline) verdict."""
    return count(text) >= MIN_WORDS


#: every list the rows report side by side (lead 2026-11-15: G1, G1b, G1c and G1c.1 are scored together on held-out
#: rows; the candidates are closed until held-out rows exist)
LISTS = ("g1", "g1b", "g1c", "g1c1")


_LV_CACHE: dict = {}


def list_verdict(name: str, text: str) -> dict:
    """{passed, words} of one list. A list's gate is a pure function of the text, and a board render asks for the same
    reply tens of thousands of times (profile of a 12-minute report, 2026-11-20: 22.9k calls x 3 lists = 70 s), so the
    result is kept per (list, text) for the life of the process; every caller gets a fresh dict."""
    if name == "g1":                    # a word count: cheap, and its threshold is a module constant
        return _list_verdict(name, text)
    key = (name, text)
    hit = _LV_CACHE.get(key)
    if hit is None:
        hit = _LV_CACHE[key] = _list_verdict(name, text)
    return dict(hit)


def _list_verdict(name: str, text: str) -> dict:
    if name == "g1":
        n = count(text)
        return {"passed": n >= MIN_WORDS, "words": n}
    if name == "g1b":
        from . import explicit_gate_g1b as mod
    elif name == "g1c":
        from . import explicit_gate_g1c as mod
    elif name == "g1c1":
        from . import explicit_gate_g1c1 as mod
    else:
        raise ValueError(f"unknown explicitness list {name!r}")
    ok, n, _ = mod.gate(text or "")
    return {"passed": bool(ok), "words": n}


def verdict(text: str) -> dict:
    """The active list's verdict ({passed, words, list}) plus every list's (``lists``)."""
    lists = {nm: list_verdict(nm, text) for nm in LISTS}
    return {**lists[ACTIVE], "list": ACTIVE, "lists": lists}


def generated_replies(rows: list[dict]) -> list[str]:
    """The replies the model generated, in turn order (pre-written history assistant turns dropped)."""
    from .chains import _replies
    reps = _replies(rows)
    n = len({r.get("turn") for r in rows if isinstance(r.get("turn"), int)}) or len(rows)
    return reps[-n:] if len(reps) > n else reps


def peak_region(replies: list[str], card: dict, *, session: bool) -> str:
    if not session:
        return "\n\n".join(replies)
    pt = int((card or {}).get("peak_turn") or 3)
    return "\n\n".join(replies[max(pt - 1, 0):pt + 1])


def reported(text: str) -> dict:
    """The verdict on a part the gate does not apply to (N5, N7): printed, never scored."""
    return {**verdict(text), "reported_only": True}
