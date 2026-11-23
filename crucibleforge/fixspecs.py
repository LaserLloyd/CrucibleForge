"""Probe specs for the fixed (non-generated) bug-fix cases graded by ``fixcheck``.

A spec splits each hidden test of a case into a probe function (inputs and calls only; this source is
what enters the sandbox) and its expected observations (kept in the host process). It is tied to the
exact hidden test files it was written from: ``spec_for`` matches the sha256 of the case's hidden files,
so a changed hidden test can never be graded by a stale spec (it raises instead).

Expected checks: ("eq", value) = the old ``assert got == value``; ("approx", value, rel) =
``pytest.approx(value, rel=rel)``; ("raises", "Name") = ``pytest.raises(Name)`` (builtin classes only).
"""
from __future__ import annotations

import hashlib
import json

# ------------------------------------------------------------------------------------------- CB-F1
_F1_SOURCE = r'''
from larder.recipes import Recipe
from larder.shopping import missing
from larder.store import Pantry
from larder.units import convert, normalize


def _conv(q, f, t):
    obs(convert(q, f, t))


def _fam(f, t):
    obs(catch(lambda: convert(1, f, t)))


def exact_0(): _conv(750, "g", "kg")
def exact_1(): _conv(1.5, "tsp", "ml")
def exact_2(): _conv(1, "tsp", "tbsp")
def exact_3(): _conv(2, "cup", "l")
def exact_4(): _conv(0.25, "l", "tbsp")
def exact_5(): _conv(3, "PCS", "pcs")
def exact_6(): _conv(1, "KG", "g")
def families_0(): _fam("g", "ml")
def families_1(): _fam("cup", "kg")
def families_2(): _fam("pcs", "g")
def families_3(): _fam("l", "pcs")


def add_sums_in_stored_unit():
    p = Pantry()
    p.add("Flour", 750, "g")
    p.add("flour", 0.5, "kg")
    obs(p.stock("flour"))
    p.add("FLOUR", 250, "G")
    obs(p.stock("flour"))


def add_wrong_family_rejected():
    p = Pantry()
    p.add("milk", 1, "l")
    obs(catch(lambda: p.add("milk", 2, "pcs")))
    obs(p.stock("milk"))


def use_converts():
    p = Pantry()
    p.add("rice", 2, "kg")
    p.use("rice", 500, "g")
    obs(p.stock("rice"))


def mixed_units_scaled():
    pancakes = Recipe("pancakes", 4, [("Flour", 250, "g"), ("Milk", 2, "cup"), ("Eggs", 2, "pcs"),
                                      ("Sugar", 2, "tbsp")])
    p = Pantry()
    p.add("flour", 0.2, "kg")
    p.add("milk", 0.5, "l")
    p.add("eggs", 6, "pcs")
    p.add("sugar", 1, "tsp")
    obs(missing(p, pancakes, 6))


def issue_story():
    p = Pantry()
    p.add("flour", 750, "g")
    p.add("flour", 500, "g")
    bread = Recipe("bread", 2, [("Flour", 1.25, "kg")])
    obs(missing(p, bread, 2))
    obs(missing(p, bread, 4))


def use_and_items():
    p = Pantry()
    p.add("rice", 2, "kg")
    p.use("rice", 1, "kg")
    obs(p.stock("rice"))
    obs(catch(lambda: p.use("rice", 2, "kg")))
    obs(catch(lambda: p.use("oats", 1, "g")))
    obs(p.items())


def units_basics():
    obs(convert(2, "tbsp", "ml"))
    obs(normalize(" Kg "))
    obs(catch(lambda: normalize("stone")))
'''

_R = 1e-12
_F1_GROUPS = {
    "convert": [
        ("exact_0", [("approx", 0.75, _R)]), ("exact_1", [("approx", 7.5, _R)]),
        ("exact_2", [("approx", 1 / 3, _R)]), ("exact_3", [("approx", 0.48, _R)]),
        ("exact_4", [("approx", 50 / 3, _R)]), ("exact_5", [("approx", 3, _R)]),
        ("exact_6", [("approx", 1000, _R)]),
        ("families_0", [("raises", "ValueError")]), ("families_1", [("raises", "ValueError")]),
        ("families_2", [("raises", "ValueError")]), ("families_3", [("raises", "ValueError")]),
    ],
    "add": [
        ("add_sums_in_stored_unit", [("eq", (1250.0, "g")), ("eq", (1500.0, "g"))]),
        ("add_wrong_family_rejected", [("raises", "ValueError"), ("eq", (1.0, "l"))]),
        ("use_converts", [("eq", (1.5, "kg"))]),
    ],
    "shopping": [
        ("mixed_units_scaled", [("eq", [("flour", 175.0, "g"), ("milk", 0.917, "cup"), ("sugar", 2.667, "tbsp")])]),
        ("issue_story", [("eq", []), ("eq", [("flour", 1.25, "kg")])]),
    ],
    "existing": [
        ("use_and_items", [("eq", (1.0, "kg")), ("raises", "ValueError"), ("raises", "KeyError"),
                           ("eq", ["rice"])]),
        ("units_basics", [("eq", 30), ("eq", "kg"), ("raises", "ValueError")]),
    ],
}

#: sha256 of the hidden files (sorted json) -> spec. The CB-F1 digest is pinned by a test.
F1_HIDDEN_SHA = "a2ae76b1e1fc71d1e96f8a6620e3b0bc5dbaf149f8fd2301ebfaf9ef95a7c093"
SPECS = {F1_HIDDEN_SHA: {"case": "CB-F1-larder-bughunt", "source": _F1_SOURCE, "groups": _F1_GROUPS}}


def hidden_sha(hidden: dict) -> str:
    return hashlib.sha256(json.dumps(hidden, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def spec_for(case_id: str | None, cfg: dict) -> dict | None:
    """The spec written for exactly this case's hidden files, or None."""
    spec = SPECS.get(hidden_sha(cfg.get("hidden") or {}))
    if spec is None or (case_id and not str(case_id).startswith(spec["case"][:6])):
        return None
    return spec
