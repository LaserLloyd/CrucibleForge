"""JCARD: the judged Chat cards of rc3 (Admin N2, 2026-11-14), safe for work.

Two variants, one card per instance, scored by the judge in rubric mode (chains ``judge_mode``):

- ``person``: "sounds like a real person". A texting card (who the person is, the thread so far, what to
  write); the writer prompt is JUDGE's EXP-20261114-34 ``rp_prompt`` word for word, no system prompt.
  Judged by the ``real_person`` rubric (judge.py), whose prompt reads the card's ``rubric_card``.
- ``roleplay``: the 4.1 role-play cases reused by id from ``cases/rp.json`` (system, opening prompt or the
  scripted user turns, ``judge_key``), each judged by the rubric the bank names (``rp_single`` /
  ``rp_multi``).

Cards live in ``cases/judged/jcards.json``. With ``deal`` (chains ``deal: true``) slot ``s`` of ``of`` is
card ``s`` of a release-keyed shuffle of the variant's cards, so a set whose count equals the bank's size
asks every card once, the same cards for every model. Without ``deal`` the instance seed picks a card.

The only code check is "replied" (a non-empty final reply): the score is the judge's.
"""
from __future__ import annotations

import functools
import hashlib
import json
import re
import sys
from pathlib import Path

from ._stack_common import grade_weighted, rng
from .. import gen as _gen

FAMILY = "jcard"
VARIANTS = ("person", "roleplay")
LEVELS = {1: {"desc": "one card, judged by the rubric (thinking off)"}}

ROOT = Path(__file__).resolve().parents[2]
BANK = ROOT / "cases" / "judged" / "jcards.json"
RP_CASES = ROOT / "cases" / "rp.json"

#: the writer's reply budget before thinking (JUDGE's writers: 600 tokens for a text thread)
PERSON_MAX_TOKENS = 600
#: sha256 (first 16 hex) of the two data files the cards come from (CHAT-RP 2026-11-14 night, lead: the same
#: exposure as rpsession's bank). Data is not in chains.revision()'s family-source digest, so a card edit left every
#: stored row reading as current. These pins put it there: an edit must update them (``python -m
#: crucibleforge.gen.jcard --pin``), which moves the revision; ``crucibleforge.regrade`` then restamps the rows whose
#: prompts are unchanged (the stored verdict is carried) and refuses the changed cards (re-run).
BANK_SHA = "e6dba96d453b3a5c"
RP_CASES_SHA = "e7d2811f19ef99f8"


@functools.lru_cache(maxsize=4)
def _read(path: str, mtime: int) -> str:
    return Path(path).read_text(encoding="utf-8")


def file_sha(p: Path) -> str:
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()[:16]


@functools.lru_cache(maxsize=8)
def _checked(path: str, mtime: int, want: str) -> None:
    got = file_sha(Path(path))
    if got != want:
        raise _gen.GeneratorError(f"jcard: {path} is {got}, the family pins {want}: run "
                                  "`python -m crucibleforge.gen.jcard --pin` after a deliberate edit")


def _json(p: Path):
    try:
        m = p.stat().st_mtime_ns
    except OSError:
        raise _gen.GeneratorError(f"jcard: file not found: {p}") from None
    pin = {BANK: BANK_SHA, RP_CASES: RP_CASES_SHA}.get(p)
    if pin is not None:
        _checked(str(p), m, pin)
    return json.loads(_read(str(p), m))


def load_bank() -> dict:
    return _json(BANK)


def _rp_case(cid: str) -> dict:
    by_id = {c["id"]: c for c in _json(RP_CASES)}
    if cid not in by_id:
        raise _gen.GeneratorError(f"jcard: roleplay card {cid} is not in cases/rp.json")
    return by_id[cid]


def person_prompt(c: dict) -> str:
    """JUDGE's writer prompt (EXP-34 jx.rp_prompt), word for word."""
    return (f"You are texting as {c['person']}. Here is the conversation so far:\n\n{c['thread']}\n\n"
            f"{c['ask']} Reply with the texts only, one text per line, nothing else.")


def _variant(gen: dict) -> str:
    v = gen.get("variant") or "person"
    if v not in VARIANTS:
        raise _gen.GeneratorError(f"jcard: unknown variant {v!r}")
    return v


def _pick(gen: dict, release_seed: str, case_id: str) -> dict:
    v = _variant(gen)
    cards = sorted(load_bank()[v], key=lambda c: c["id"])
    deal = gen.get("deal")
    if deal:
        of, slot = int(deal.get("of", 1)), int(deal.get("slot", 0))
        if not 0 <= slot < of:
            raise _gen.GeneratorError(f"jcard: deal slot {slot} not in 0..{of - 1}")
        if of > len(cards):
            raise _gen.GeneratorError(f"jcard: {v} bank has {len(cards)} cards, cannot deal {of}")
        order = list(cards)
        rng(str(deal.get("key") or "deal"), f"jcard-{v}").shuffle(order)
        return order[slot]
    seed = _gen.instance_seed(FAMILY, release_seed, case_id, int(gen.get("level", 1)), gen.get("key"))
    return rng(seed, "pick").choice(cards)


def _replied_check(turn: int) -> dict:
    return {"id": "replied", "fact": "replied", "ftype": "format", "group": "replied", "turns": [turn],
            "scope": "ic", "weight": 1, "scored": True, "type": "word_range", "min": 1, "max": 10 ** 6,
            "tolerance": 0.0, "note": "a non-empty reply (the score is the judge's)"}


def materialize(gen: dict, *, release_seed: str, case_id: str) -> dict:
    v = _variant(gen)
    card = _pick(gen, release_seed, case_id)
    if v == "person":
        return {"id": case_id, "category": "rpdeep", "system": "", "history": [],
                "turns": [person_prompt(card)], "temperature": 0.8, "top_p": 0.95,
                "max_tokens": PERSON_MAX_TOKENS, "checks": [_replied_check(1)], "programmatic": False,
                "rubric": "real_person", "sexual": False, "explicit_required": False,
                "rubric_card": {"person": card["person"], "thread": card["thread"]},
                "meta": {"card": card["id"], "variant": v}}
    src = _rp_case(card["id"])
    turns = list(src.get("turns") or []) or [src["prompt"]]
    out = {"id": case_id, "category": "rpdeep", "system": src["system"], "history": [], "turns": turns,
           "temperature": src.get("temperature", 0.8), "top_p": src.get("top_p", 0.95),
           "max_tokens": src.get("max_tokens", 2000), "checks": [_replied_check(len(turns))],
           "programmatic": False, "rubric": card["rubric"], "sexual": False, "explicit_required": False,
           "meta": {"card": card["id"], "variant": v, "source": "cases/rp.json"}}
    if src.get("judge_key"):
        out["judge_key"] = src["judge_key"]
    return out


def reference(gen: dict, *, release_seed: str, case_id: str) -> str:
    """A fixture reply (code checks only; it says nothing about quality)."""
    m = materialize(gen, release_seed=release_seed, case_id=case_id)
    from ._stack_common import join_turns
    return join_turns(["ok, on my way"] * len(m["turns"]))


def mutants(gen: dict, *, release_seed: str, case_id: str) -> list[str]:
    return [""]


def grade(answer: str, materialized: dict) -> float:
    from ._stack_common import split_turns
    return grade_weighted(materialized["checks"], split_turns(answer))


def _pin() -> None:
    """Rewrite BANK_SHA / RP_CASES_SHA in this file from the data files as they are now."""
    me = Path(__file__)
    src = me.read_text(encoding="utf-8")
    for name, f in (("BANK_SHA", BANK), ("RP_CASES_SHA", RP_CASES)):
        src = re.sub(rf'^{name} = "[0-9a-f]{{16}}"$', f'{name} = "{file_sha(f)}"', src, count=1, flags=re.M)
    me.write_text(src, encoding="utf-8")
    print("pinned", file_sha(BANK), file_sha(RP_CASES))


if __name__ == "__main__" and "--pin" in sys.argv:
    _pin()
