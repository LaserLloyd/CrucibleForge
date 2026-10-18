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
]


def _patterns():
    out = []
    for f in sorted((REPO / "cases").glob("*.json")):
        for c in json.loads(f.read_text(encoding="utf-8")):
            for k in c.get("checks") or []:
                if isinstance(k.get("pattern"), str):
                    out.append(pytest.param(k["pattern"], id=f"{c['id']}:{k['id']}"))
    return out


def _search_all(pattern: str) -> None:
    import re
    p = re.compile(pattern, re.I | re.M)
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


def test_check_regexes_are_not_catastrophic_without_fork():
    """The same guarantee on platforms with no fork() (Windows).

    One persistent ``spawn``-ed worker takes the patterns in turn, so the 2 s
    deadline measures the regex, not interpreter start-up. A pattern that blows
    the deadline gets the worker killed and replaced; every offender is reported.
    """
    import _regex_worker

    ctx = mp.get_context("spawn")
    patterns = [p.values[0] for p in _patterns()]
    assert patterns, "no check patterns found - the cases directory moved?"

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
    assert not slow, (f"{len(slow)} regex(es) took > {DEADLINE_S}s on adversarial input "
                      f"(catastrophic backtracking): {slow[:3]}")
