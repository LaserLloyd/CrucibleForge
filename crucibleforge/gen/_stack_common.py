"""Shared helpers for the ISTACK and SESSION-STATE generators (no FAMILY here, so
gen auto-discovery skips this module).

* seeding: ``gen.instance_seed`` (the registry's); ``rng(seed, salt)`` gives an independent, version-stable random stream per salt.
* grading: every check the generators emit carries ``fact`` (= its id) and ``weight``;
  ``grade_weighted`` runs the ENGINE's own ``session_checks.run_checks`` and returns the
  weighted share of passed checks, which is exactly what ``rpdeep`` computes from the
  same ``checks`` (weight_passed / weight_total), so generator grade == engine score.
* multi-turn answers are one string: the assistant replies joined by ``TURN_SEP``.
"""
from __future__ import annotations

import hashlib
import random

from .. import session_checks

TURN_SEP = "\n<<<TURN>>>\n"


def rng(seed: str, salt: str) -> random.Random:
    return random.Random(int(hashlib.sha256(f"{seed}:{salt}".encode()).hexdigest()[:16], 16))


def split_turns(answer: str) -> list[str]:
    return answer.split(TURN_SEP)


def join_turns(replies: list[str]) -> str:
    return TURN_SEP.join(replies)


PASSIVE_CAP = 0.15


def cap_passive(checks: list[dict], passive_notes: set, cap: float = PASSIVE_CAP) -> list[dict]:
    """Checks whose ``note`` (else ``id``) is in ``passive_notes`` are "never do X" rules that any
    non-empty off-topic text satisfies. Their TOTAL weight is set to ``cap`` of the
    case's weight (split evenly), so a generic paragraph that obeys every prohibition
    still scores <= cap. Positive checks keep their weights."""
    def passive(c):
        return (c.get("note") or c["id"]) in passive_notes
    pas = [c for c in checks if passive(c)]
    pos = sum(float(c.get("weight", 1)) for c in checks if not passive(c))
    if not pas or not pos:
        return checks
    each = round(cap / (1 - cap) * pos / len(pas), 4)
    for c in pas:
        c["weight"] = each
    return checks


def grade_weighted(checks: list[dict], replies: list[str]) -> float:
    """Weighted share of passed checks (a missing result = failed)."""
    if not checks:
        return 0.0
    out = session_checks.run_checks({"checks": checks}, replies) or {"results": []}
    by_id = {r["id"]: r for r in out["results"]}
    zero = session_checks.turn_gates(checks, by_id)
    total = sum(float(c.get("weight", 1)) for c in checks)
    got = 0.0
    for c in checks:
        r = by_id.get(c["id"])
        if r and not session_checks.gated(c, zero):
            got += float(c.get("weight", 1)) * session_checks.check_credit(r)
    return round(got / total, 6) if total else 0.0
