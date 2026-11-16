"""RPSESSION: long scripted role-play sessions for Chat's judged role-play part (CHAT-RP, lead 2026-11-14 night).

Safe for work. One card per instance from ``cases/judged/rpsession.json`` (built by
``tools/build_rpsession_bank.py``). Each card is 8-12 scripted user turns that stack the multi-turn failures the rc3
role-play cards trigger only once (EXP-20261114-35 CHAT-JUDGED-STRETCH.md section 4):

* a RESTRICTIVE VOICE: a word cap per reply, banned words (the names of the feeling the scene is about, so the
  emotion has to come through subtext), and a speech habit;
* a user MISSTATEMENT OF CANON (turn 4-5) to correct in character, and a SUBTLER second one later;
* TWO STANDING RULES the user pushes twice each; each rule withholds something only the character knows (a name,
  a number, a code, a place), so breaking it is visible in the text and the user can never be echoed into it;
* LATE RECALL (turn 8+) of two or three details the user planted before turn 4;
* ONE SCENE CHANGE that must not bleed: named fixtures and people of scene one stay out of scene two.

Code checks (deterministic, ``session_checks``; one element each, weight 1): ``voice-cap``, ``voice-banned``,
``voice-habit`` (and on some cards a second voice rule), ``ruleA@n`` / ``ruleB@n`` per push, ``canon1@n`` +
``canon1-held`` + ``canon2@n``, ``recall-*@n``, ``nopuppet``, ``bleed``. The judge reads the whole session through
the ``rp_long`` rubric (prose, dialogue, emotion, atmosphere: no mark that every capable model maxes). How the
code share and the rubric combine is the part's ``judge_mode`` (``code_weight``).

``reference`` = the card's hand-written passing transcript (every check passes); ``mutants`` = its hand-written
foil (named checks fail, listed in the bank) plus one single-fault mutant per check family. Neither says anything
about quality: they prove the checks fire both ways.
Answer shape for grade()/reference()/mutants(): the replies joined with ``TURN_SEP``.
"""
from __future__ import annotations

import functools
import hashlib
import json
from pathlib import Path

from ._stack_common import grade_weighted, join_turns, rng, split_turns
from .. import gen as _gen

FAMILY = "rpsession"
#: ``scene`` is judged: rpdeep's engine path waits for a verdict, so verify_case cannot compare it. grade() is what
#: the board scores as the code share (chains.check_results over the same checks, all weight 1: the mean of the
#: check elements); tests/test_chatrp_sessions_20261114.py proves grade() == that mean, and == the engine score on
#: the judge-off pack variants (``programmatic``: checks only, no in-character gate)
GRADE_IS_ENGINE = True
#: ``scene`` = every check + the rp_long rubric (Chat's judged role-play). The judge-off pack variants run the SAME
#: session with no rubric and a subset of its checks (lead 2026-11-14 night, PACKS2): ``state`` (rules at each push,
#: recall, canon corrections, bleed, no puppeting), ``tally`` (a counted quantity changed over 4+ turns, asked once at
#: turn 9+, exact) and ``know`` (a false "as you agreed / as you said" the character must decline in character).
VARIANTS = ("scene", "state", "tally", "know")
VARIANT_KINDS = {"state": ("rule", "recall", "canon", "bleed", "nopuppet"), "tally": ("tally",), "know": ("know",)}
LEVELS = {1: {"desc": "one scripted session card (8-12 turns): code checks (+ the rp_long rubric on scene)"}}

ROOT = Path(__file__).resolve().parents[2]
BANK = ROOT / "cases" / "judged" / "rpsession.json"
#: sha256 (first 16 hex) of the bank file. The bank is DATA, so it is not in chains.revision()'s family-source
#: digest: without this pin a card or check edit would leave every stored row reading as current (no regrade, no
#: re-run). tools/build_rpsession_bank.py rewrites it; load_bank() refuses a bank that does not match, so the
#: revision moves with every bank edit and ``crucibleforge.regrade`` sorts the rows: identical prompts are
#: regraded, changed prompts refused (re-run).
BANK_SHA = "26eb25bc1caafcd3"
RUBRIC = "rp_long"
#: the bank's check spec -> a session_checks spec (every check is its own scored element)
_GROUPS = {"voice": "identity", "rule": "constraint", "canon": "continuity", "recall": "continuity",
           "nopuppet": "identity", "bleed": "continuity", "tally": "continuity", "know": "continuity"}


@functools.lru_cache(maxsize=4)
def _read(path: str, mtime: int) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def bank_sha(path: Path | None = None) -> str:
    return hashlib.sha256(Path(path or BANK).read_bytes()).hexdigest()[:16]


@functools.lru_cache(maxsize=4)
def _checked(path: str, mtime: int) -> None:
    got = bank_sha(Path(path))
    if got != BANK_SHA:
        raise _gen.GeneratorError(f"rpsession: bank {path} is {got}, the family pins {BANK_SHA}: rebuild it with "
                                  "tools/build_rpsession_bank.py (which updates the pin)")


def load_bank(path: Path | None = None) -> dict:
    p = Path(path or BANK)
    try:
        m = p.stat().st_mtime_ns
    except OSError:
        raise _gen.GeneratorError(f"rpsession: bank not found: {p}") from None
    if path is None:
        _checked(str(p), m)
    return _read(str(p), m)


def _variant(gen: dict) -> str:
    v = gen.get("variant") or "scene"
    if v not in VARIANTS:
        raise _gen.GeneratorError(f"rpsession: unknown variant {v!r}")
    return v


def cards(variant: str = "scene") -> list[dict]:
    """The bank's cards a variant can draw: ``scene`` / ``state`` all six; ``tally`` / ``know`` the cards that carry
    such a check."""
    cs = sorted(load_bank()["cards"], key=lambda c: c["id"])
    kinds = VARIANT_KINDS.get(variant)
    if variant in ("tally", "know"):
        cs = [c for c in cs if any(k["kind"] in kinds for k in c["checks"])]
    return cs


def _pick(gen: dict, release_seed: str, case_id: str) -> dict:
    cs = cards(_variant(gen))
    if gen.get("card"):
        by = {c["id"]: c for c in cs}
        if gen["card"] not in by:
            raise _gen.GeneratorError(f"rpsession: no card {gen['card']!r} for variant {_variant(gen)!r}")
        return by[gen["card"]]
    deal = gen.get("deal")
    if deal:
        of, slot = int(deal.get("of", 1)), int(deal.get("slot", 0))
        if not 0 <= slot < of:
            raise _gen.GeneratorError(f"rpsession: deal slot {slot} not in 0..{of - 1}")
        if of > len(cs):
            raise _gen.GeneratorError(f"rpsession: bank has {len(cs)} cards, cannot deal {of}")
        order = list(cs)
        rng(str(deal.get("key") or "deal"), "rpsession").shuffle(order)
        return order[slot]
    seed = _gen.instance_seed(FAMILY, release_seed, case_id, int(gen.get("level", 1)), gen.get("key"))
    return rng(seed, "pick").choice(cs)


def checks_of(card: dict, variant: str = "scene") -> list[dict]:
    kinds = VARIANT_KINDS.get(variant)
    out = []
    for c in card["checks"]:
        c = dict(c)
        kind = c.pop("kind")
        if kinds is not None and kind not in kinds:
            continue
        c.setdefault("group", _GROUPS[kind])
        c.update(fact=c["id"], ftype="constraint", weight=1, scored=True)
        c.setdefault("scope", "ic")
        out.append(c)
    return out


def materialize(gen: dict, *, release_seed: str, case_id: str) -> dict:
    v = _variant(gen)
    card = _pick(gen, release_seed, case_id)
    out = {"id": case_id, "category": "rpdeep", "system": card["system"], "history": [],
           "turns": list(card["turns"]), "temperature": 0.8, "top_p": 0.95, "max_tokens": 2000,
           "checks": checks_of(card, v), "programmatic": v != "scene", "sexual": False, "explicit_required": False,
           "meta": {"card": card["id"], "variant": v, "genre": card["genre"],
                    "scene_change_turn": card["scene_change_turn"]}}
    if v == "scene":
        out.update(rubric=RUBRIC, judge_key=card["judge_key"])
    return out


def reference_replies(card: dict) -> list[str]:
    return list(card["reference"])


def foil_replies(card: dict) -> list[str]:
    reps = list(card["reference"])
    for t, text in card["foil"]["replies"].items():
        reps[int(t) - 1] = text
    return reps


def reference(gen: dict, *, release_seed: str, case_id: str) -> str:
    return join_turns(reference_replies(_pick(gen, release_seed, case_id)))


def mutants(gen: dict, *, release_seed: str, case_id: str) -> list[str]:
    card = _pick(gen, release_seed, case_id)
    ref = reference_replies(card)
    out = [join_turns(foil_replies(card))]
    # a single fault: the recall turn answered without the details (the tally, where it is asked there, kept)
    rt = next(int(c["turns"][0]) for c in card["checks"] if c["kind"] == "recall")
    forgot = list(ref)
    forgot[rt - 1] = card["forgetful_recall"]
    out.append(join_turns(forgot))
    v = _variant(gen)
    if v != "scene":
        # a pack variant grades only its own checks: keep the mutants that move them
        mat = materialize(gen, release_seed=release_seed, case_id=case_id)
        out = [m for m in out if grade(m, mat) < 1.0 - 1e-9] or [join_turns(foil_replies(card))]
    return out


def grade(answer: str, materialized: dict) -> float:
    return grade_weighted(materialized["checks"], split_turns(answer))
