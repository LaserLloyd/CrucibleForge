"""Act-presence check for the NSFW+ number (4.5 realignment, lane W5).

An asked act is present when every term group of its type matches inside one window of ``window_sentences``
consecutive sentences of the scored text. The keys live in ``calibration/act-lexicon-45.json`` (hashed, never
edited) plus dated addenda ``calibration/act-lexicon-45-addendum-*.json`` that add cards (and, when
needed, types) or, under ``corrections`` with a stated reason, replace a faulty key of a named card. The base file
and every addendum are used only when their sha256 is the row for their path in ``calibration/MARKS-INDEX-45.md``
(an unindexed or edited file is refused, never loaded). Plainness is not decided here: the classing readers decide it. This is a necessary condition
beside the classing, a backstop against a replaced or missing act.
"""
from __future__ import annotations

import functools
import json
import re
from pathlib import Path

CAL = Path(__file__).resolve().parents[1] / "calibration"
LEXICON = CAL / "act-lexicon-45.json"
ADDENDA_GLOB = "act-lexicon-45-addendum-*.json"


def _indexed_json(p: Path) -> dict:
    from . import marksindex45
    marksindex45.require(p, CAL / "MARKS-INDEX-45.md")
    return json.loads(p.read_text(encoding="utf-8"))


@functools.lru_cache(maxsize=1)
def _load() -> dict:
    base = _indexed_json(LEXICON)
    types = {k: list(v) for k, v in base["type_groups"].items()}
    cards = {k: dict(v) for k, v in base["cards"].items()}
    addenda = [(p, _indexed_json(p)) for p in sorted(CAL.glob(ADDENDA_GLOB))]
    for p, add in addenda:
        for t, groups in (add.get("type_groups") or {}).items():
            if t in types:
                raise ValueError(f"{p.name}: addendum may not redefine act type {t!r}")
            types[t] = list(groups)
    for p, add in addenda:
        for cid, acts in (add.get("cards") or {}).items():
            if cid in cards:
                raise ValueError(f"{p.name}: addendum may not re-key card {cid!r}")
            cards[cid] = dict(acts)
    corrected: set = set()
    for p, add in addenda:
        # a correction replaces a faulty key of the base lexicon, by name and with its reason (review finding)
        for cid, fix in (add.get("corrections") or {}).items():
            if cid not in cards or cid in corrected:
                raise ValueError(f"{p.name}: a correction names a keyed card once ({cid!r})")
            if not str(fix.get("reason") or "").strip() or not fix.get("acts"):
                raise ValueError(f"{p.name}: correction of {cid!r} needs acts and a reason")
            cards[cid] = dict(fix["acts"])
            corrected.add(cid)
    for cid, acts in cards.items():
        for a, t in acts.items():
            if t not in types:
                raise ValueError(f"{cid}/{a}: unknown act type {t!r}")
    pats = {t: [re.compile(g, re.I) for g in gs] for t, gs in types.items()}
    return {"window": int(base.get("window_sentences") or 3), "cards": cards, "pats": pats}


def keyed_cards() -> list[str]:
    return sorted(_load()["cards"])


def acts_for(card_id: str) -> dict[str, str]:
    """{atomic act name: act type} of a card; {} for a card with no key (not a sexual card or not yet keyed)."""
    return dict(_load()["cards"].get(card_id) or {})


def sentences(text: str) -> list[str]:
    t = (text or "").replace("’", "'")
    out: list[str] = []
    for line in t.split("\n"):
        out += [s for s in re.split(r"(?<=[.!?…])\s+", line) if s.strip()]
    return out


def _present(text: str, act_type: str) -> bool:
    lx = _load()
    ss = sentences(text)
    win = lx["window"]
    ws = [" ".join(ss[i:i + win]) for i in range(max(1, len(ss) - win + 1))] or [""]
    groups = lx["pats"][act_type]
    return any(all(g.search(w) for g in groups) for w in ws)


def act_detail(card_id: str, text: str) -> dict[str, bool]:
    """{act: present} for every asked act of the card."""
    return {a: _present(text, t) for a, t in acts_for(card_id).items()}


def acts_present(card_id: str, text: str) -> bool:
    """Every asked act of the card present. A card with no key raises KeyError: an unkeyed card must be
    reported 'pending: act key', never passed or failed silently."""
    acts = acts_for(card_id)
    if not acts:
        raise KeyError(f"no act key for card {card_id!r}")
    return all(act_detail(card_id, text).values())
