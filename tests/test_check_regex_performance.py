"""Every regex in a case's deterministic checks must run in linear-ish time.

2026-09-29: steer SX3's `fmt-two-paragraphs` forbid pattern backtracked
exponentially in the number of reply lines (`[^\\n]*\\S[^\\n]*` inside a lazy
repetition is ambiguous; ×16 per two lines). Python's `re` holds the GIL for
the whole search, so one long reply froze EVERY thread of a benchmark — the
HTTP readers included — for over an hour with the replies sitting unread in
the sockets. Each pattern here gets a few adversarial inputs in a child
process with a hard deadline.
"""
from __future__ import annotations

import json
import multiprocessing as mp
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
DEADLINE_S = 2.0

ADVERSARIAL = [
    "p1\n\n" + ("ab cd ef\n" * 400),       # paragraph, then many lines, no closing break
    "a\n" * 4000,                           # many one-char lines
    "w " * 20000,                           # one very long line
    "line of words here\n\n" * 400,         # many paragraphs
    ("word, " * 3000) + "\n" * 5,
    # 2026-10-18 review: the inputs above never trip a nested quantifier -- the
    # textbook ``^(a+)+$`` ran them all in 0.5 ms. A long run of one letter that
    # then fails to match is what makes ``(x+)+``, ``(x|xx)+`` and friends
    # explode (2^n ways to split the run); 40 letters is minutes.
    "a" * 40 + "!",
    ("a" * 40 + "!\n") * 50,
]

#: A pattern every regex engine without memoisation takes exponential time on.
#: The guards below must report it, or they prove nothing (see the meta-tests).
TEXTBOOK_CATASTROPHIC = r"^(a+)+$"


#: The flags each pattern is compiled with in the product, by where it lives:
#: session ``checks`` -> session_checks.py (re.I | re.M); the IFEval ``regex``
#: checks in ``grader_config.checks`` -> graders._check_one (no flags).
SESSION_FLAGS = re.I | re.M
GRADER_FLAGS = 0


def _patterns():
    """Every regex a case hands to ``re``: ``(pattern, flags)`` params.

    2026-10-18 (second review): this used to read only the session ``checks``
    and missed the 17 ``grader_config.checks`` regexes -- graded on the same
    benchmark threads, under the same GIL, by graders._check_one.
    """
    out = []
    for f in sorted((REPO / "cases").glob("*.json")):
        for c in json.loads(f.read_text(encoding="utf-8")):
            for k in c.get("checks") or []:
                if isinstance(k.get("pattern"), str):
                    out.append(pytest.param((k["pattern"], SESSION_FLAGS),
                                            id=f"{c['id']}:{k['id']}"))
            for i, k in enumerate((c.get("grader_config") or {}).get("checks") or []):
                if isinstance(k, dict) and isinstance(k.get("pattern"), str):
                    out.append(pytest.param((k["pattern"], GRADER_FLAGS),
                                            id=f"{c['id']}:grader-{k.get('id') or i}"))
    return out


def _search_all(item) -> None:
    pattern, flags = item if isinstance(item, tuple) else (item, SESSION_FLAGS)
    p = re.compile(pattern, flags)
    for s in ADVERSARIAL:
        for _ in p.finditer(s):
            pass


FORK = "fork" in mp.get_all_start_methods()


@pytest.mark.skipif(not FORK, reason="needs the 'fork' start method (POSIX); "
                    "Windows runs test_check_regexes_are_not_catastrophic_without_fork instead")
@pytest.mark.parametrize("pattern", _patterns())
def test_check_regex_is_not_catastrophic(pattern):
    ctx = mp.get_context("fork")
    proc = ctx.Process(target=_search_all, args=(pattern,))
    proc.start()
    proc.join(DEADLINE_S)
    if proc.is_alive():
        proc.kill()
        proc.join()
        pytest.fail(f"regex took > {DEADLINE_S}s on adversarial input "
                    f"(catastrophic backtracking): {pattern}")
    assert proc.exitcode == 0


def _slow_patterns_spawn(patterns: list[str]) -> list[str]:
    """The patterns in *patterns* that blow DEADLINE_S on the adversarial inputs.

    One persistent ``spawn``-ed worker takes the patterns in turn, so the
    deadline measures the regex, not interpreter start-up. A pattern that blows
    the deadline gets the worker killed and replaced; every offender is returned.
    """
    import _regex_worker

    ctx = mp.get_context("spawn")

    def start():
        parent, child = ctx.Pipe()
        proc = ctx.Process(target=_regex_worker.serve, args=(child, ADVERSARIAL), daemon=True)
        proc.start()
        child.close()
        # interpreter start-up is not the regex: wait for the worker's hello
        assert parent.poll(60) and parent.recv() == "ready", "regex worker did not start"
        return proc, parent

    proc, conn = start()
    slow = []
    try:
        for pat in patterns:
            conn.send(pat)
            if conn.poll(DEADLINE_S):
                conn.recv()
                continue
            slow.append(pat)
            proc.kill()
            proc.join()
            proc, conn = start()
    finally:
        if proc.is_alive():
            conn.send(None)
            proc.join(5)
            if proc.is_alive():
                proc.kill()
    return slow


def test_check_regexes_are_not_catastrophic_without_fork():
    """The same guarantee as the fork version, on platforms with no fork() (Windows)."""
    patterns = [p.values[0] for p in _patterns()]
    assert patterns, "no check patterns found - the cases directory moved?"
    assert any(flags == GRADER_FLAGS for _, flags in patterns), \
        "no grader_config.checks regexes collected - the case schema moved?"
    slow = _slow_patterns_spawn(patterns)
    assert not slow, (f"{len(slow)} regex(es) took > {DEADLINE_S}s on adversarial input "
                      f"(catastrophic backtracking): {slow[:3]}")


def test_the_spawn_guard_really_catches_a_catastrophic_pattern():
    """A guard that passes everything is worse than none: the textbook
    ``^(a+)+$`` must be reported, and a harmless neighbour must not."""
    assert _slow_patterns_spawn([TEXTBOOK_CATASTROPHIC, r"^a+$"]) == [TEXTBOOK_CATASTROPHIC]


@pytest.mark.skipif(not FORK, reason="needs the 'fork' start method (POSIX)")
def test_the_fork_guard_really_catches_a_catastrophic_pattern():
    """Same proof for the fork version: the child must still be running at the
    deadline on ``^(a+)+$`` (it is killed), and finish at once on ``^a+$``."""
    ctx = mp.get_context("fork")
    proc = ctx.Process(target=_search_all, args=(TEXTBOOK_CATASTROPHIC,))
    proc.start()
    proc.join(DEADLINE_S)
    hung = proc.is_alive()
    proc.kill()
    proc.join()
    assert hung, "the adversarial inputs no longer trip a nested quantifier"
    ok = ctx.Process(target=_search_all, args=(r"^a+$",))
    ok.start()
    ok.join(DEADLINE_S)
    assert not ok.is_alive() and ok.exitcode == 0
