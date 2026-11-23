"""nsfwpack45 -- the staged NSFW+ packs (4.5 packs lane, 2026-11-20).

A pack item is an ``adult45`` item of the same variant, harder to do WELL, read from its own bank under
``cases/ladder/nsfw/packs45/`` instead of the standard bank. This module changes NOTHING in ``adult45``: it
borrows ``adult45.materialize`` by pointing its bank loader at the pack bank for the length of one call (under a
lock), then stamps the case with this family. The pack banks carry their own sha pins
(``nsfwpack45_pins.py``, rewritten by ``tools/pin_nsfwpack45_banks.py``). Variants:

  taboo   packs45/range-asks.json   one adult ask, no system prompt          (range pack, group n7)
  uask    packs45/will-asks.json    one dark ask, no system prompt           (willingness pack, group u1)
  write   packs45/write.json        system line + brief, one explicit piece  (erotica pack, group n6)
  bodies  packs45/bodies.json       eight numbered sentences, find the errors (anatomy pack, key)
  stored  nsfw_rp/set-1.json        Mechanisms cases NR1 / NR3 / NR5 / NR6 as built (adult role-play pack)

Limits: ``docs/CONTENT-RULES.md``. The staged set files live in ``cases/ladder/nsfw/<part>/set-N.json`` with
``"status": "staged"`` and the generated form (``generator`` + ``rungs``: rung r = the r-th item in id order), so the
content-attestation gate (``scripts/attest_check.py``, which reads ``cases`` / ``rungs`` / ``frames`` lists) has no
entry to check until the sets are wired. Nothing runs them until the release composition includes staged sets.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import threading
from pathlib import Path

from . import adult45 as _a
from .. import gen as _gen

FAMILY = "nsfwpack45"
VARIANTS = ("taboo", "uask", "write", "bodies", "stored")
LEVELS = {1: {"desc": "the staged pack set (each pack item is a harder card of its variant)"}}
GRADE_IS_ENGINE = True

BANK_DIR = "nsfw/packs45"
FILES = {"taboo": "range-asks.json", "uask": "will-asks.json", "write": "write.json", "bodies": "bodies.json"}
STORED_IDS = ("NR1-edge-count", "NR3-two-forms", "NR5-protocol", "NR6-two-clocks")
_LOCK = threading.RLock()


def bank_key(variant: str) -> str:
    return _a.STORED_FILE if variant == "stored" else f"{BANK_DIR}/{FILES[variant]}"


def bank_path(variant: str) -> Path:
    from .. import ladder
    return ladder.LADDER_DIR / bank_key(variant)


def _pins() -> dict:
    from . import nsfwpack45_pins
    return nsfwpack45_pins.BANK_SHA


def _checked(variant: str) -> dict:
    if variant not in VARIANTS:
        return {}            # adult45 asks for "session" overrides on a stored card: none exist for packs
    p = bank_path(variant)
    try:
        raw = p.read_bytes()
    except OSError:
        raise _gen.GeneratorError(f"nsfwpack45: bank not found: {p}") from None
    want = _pins().get(bank_key(variant))
    got = hashlib.sha256(raw).hexdigest()[:16]
    if want != got:
        raise _gen.GeneratorError(f"nsfwpack45: bank {bank_key(variant)} is {got}, pinned {want}: an edited bank is "
                                  "re-pinned with tools/pin_nsfwpack45_banks.py once its checks pass")
    return json.loads(raw.decode("utf-8"))


@contextlib.contextmanager
def _pointed():
    """adult45's bank loader and stored ids pointed at the pack banks; restored on exit."""
    with _LOCK:
        old_load, old_ids = _a.load_bank, _a.STORED_IDS
        _a.load_bank = _checked
        _a.STORED_IDS = STORED_IDS
        try:
            yield
        finally:
            _a.load_bank, _a.STORED_IDS = old_load, old_ids


def _variant(gen: dict) -> str:
    v = gen.get("variant")
    if v not in VARIANTS:
        raise _gen.GeneratorError(f"nsfwpack45: unknown variant {v!r}")
    return v


def items(variant: str) -> list[dict]:
    with _pointed():
        return _a.items(variant)


def _named(gen: dict) -> dict:
    """The generator with its item named: ``pin`` (the bank item id) or, in a generated set, the rung (``level``) =
    the 1-based position of the item in id order. A pack item is never dealt or drawn at random."""
    v = _variant(gen)
    if gen.get("pin"):
        return {**gen, "variant": v}
    its = items(v)
    lv = int(gen.get("level", 1))
    if not 1 <= lv <= len(its):
        raise _gen.GeneratorError(f"nsfwpack45: no {v} item at rung {lv} (the bank has {len(its)})")
    return {**gen, "variant": v, "pin": its[lv - 1]["id"]}


def pick(gen: dict, release_seed: str, case_id: str) -> dict:
    g = _named(gen)
    with _pointed():
        return _a.pick(g, release_seed, case_id)


def item_digest(gen: dict, release_seed: str) -> str:
    it = pick(gen, release_seed, "revision")
    return hashlib.sha256(json.dumps(it, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:12]


def materialize(gen: dict, *, release_seed: str, case_id: str) -> dict:
    g = _named(gen)
    with _pointed():
        case = _a.materialize(g, release_seed=release_seed, case_id=case_id)
    case = dict(case)
    case["meta"] = {**case.get("meta", {}), "family": FAMILY, "pack": True}
    return case


def reference(gen: dict, *, release_seed: str, case_id: str) -> str:
    with _pointed():
        return _a.reference(_named(gen), release_seed=release_seed, case_id=case_id)


def mutants(gen: dict, *, release_seed: str, case_id: str) -> list[str]:
    with _pointed():
        return _a.mutants(_named(gen), release_seed=release_seed, case_id=case_id)


def grade(answer: str, materialized: dict) -> float:
    return _a.grade(answer, materialized)


def item_problems(variant: str, it: dict) -> list[str]:
    return _a.item_problems(variant, it)
