"""The staged ladder sets the case verifiers leave out (2026-11-20, integration fix pass).

A staged set is a finished pack waiting for the owner's approval: it loads (so ``pack verify <file>`` reports it)
but the runner refuses it and the board never counts it. The three whole-tree verifiers (``test_every_committed_
pack_verifies``, ``test_cases_verify_clean``, ``test_case_set_verifies_clean``) cover WIRED sets only. The staged
NSFW+ sets fail the 4.1 case lint for reasons that belong to their own build (adult45 check groups the lint does
not know, ``explicit_required`` without an erotic rubric, and the stored-variant items carry no authored reference
reply, so the family reference grades 0.33-0.45); they get their gate when they are wired.

This list is the ONLY place a set is left out. ``test_staged_verify_list`` asserts it equals the staged sets in the
tree, so a newly staged set must be named here and a set that gets wired (loses its staged flag) fails until it is
taken off and passes the verifiers like every other wired set."""
from __future__ import annotations

from pathlib import Path

LADDER = Path(__file__).resolve().parents[1] / "cases" / "ladder"

STAGED_SETS = frozenset({
    "chat/h6_know/set-1.json",          # KNOWGAP
    # the six NSFW+ pack sets were wired 2026-11-21 (owner ruling): verify_cases checks them by the pack's own rules
    "nsfw/nsfw_writing/set-3.json",
    "nsfw/nsfw_writing/set-4.json",
    "nsfw/nsfw_writing/set-5.json",
    "nsfw/uncensored/set-1.json",
})


def rel(path) -> str:
    return Path(path).resolve().relative_to(LADDER.resolve()).as_posix()


def listed(path) -> bool:
    """True when ``path`` (a set file, absolute or relative to the repo) is on :data:`STAGED_SETS`."""
    try:
        return rel(path) in STAGED_SETS
    except ValueError:          # not under cases/ladder (a temp tree in a test): never left out
        return False
