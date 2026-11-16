"""istack -- ISTACK instruction-following-under-stacked-constraints generator (G4).

A seed + level deterministically produce a short writing prompt (variant ``write``, one turn) or a
three-turn conversation (variant ``conv``, standing rules that carry across turns, a length
rule that changes in turn 3, per-piece content rules) plus N constraints drawn from a bank of 16
kinds: length window, paragraph count, acrostic initials, forbidden letters, forbidden words,
dossier facts reproduced verbatim, sentences per paragraph, exact closing line, headed sections
with given titles, entity first-mention order, no digits, exactly n question marks, keyword
exactly n times, no word longer than L letters, exactly n semicolons, a fixed opening word.

Everything is graded programmatically: one engine check (session_checks types word_range,
line_count, require_regex) per constraint -- one per fact for the facts rule -- weighted
x2 for facts, entity order and the per-turn topic word (every turn must use its topic's
keyword: a positive, topic-bound rule on top of the N constraints), x1 otherwise; the
"never do X" rules (forbidden letters/words, no digits, max word length, sentences per
paragraph) share 15% of the total weight (``_stack_common.cap_passive``), so a generic
off-topic paragraph that breaks no prohibition scores < 0.2. Score = weighted share
satisfied. Hard per-turn gates (zero-weight ``gate`` checks, ``session_checks.turn_gates``): a turn
scores 0 unless (a) it meets the lower bound of its length window (90%; 30 words with no window),
(b) under ECHO_MAX_SHARE of its 8-word runs are copied from the prompt text (dossier facts and the
closing line excluded) and (c) it meets at least half (by weight) of its content-specific
constraints (facts, closing, acrostic, sections, paragraphs, entity order, ...). Every turn
carries at least one such constraint. Every "passive" rule (forbid letter/word/digit, max length ...) also requires >= 30 words so an empty or
stub answer scores 0.0. Every item is verified at generation time: a template-built
reference answer is constructed that satisfies all constraints together and must grade 1.0
(otherwise the instance is re-drawn from the same seed, deterministically).

LEVELS (constraints N; facts K; entities m; pool = the kinds the draw may use):

  level  N   pool            forced kinds             K  m
    1    4   easy 7                                   -  -
    2    7   easy + 3                                 -  -
    3   10   + facts/acro/ents  window,acro,facts      2  3
    4   13   all 16          window,acro,facts,ents   3  3
    5   16   all 16 (all used)                        4  4
    6   16   all 16, longer dossier use               5  5

Measured sizes (30 seeds per level, chars/4 token estimate, release_seed "r"; the tests pin
these bounds). Prompt tokens / reference-answer tokens (min-max):

  level   write prompt   write answer   conv prompts (3)  conv answers (3)   checks (write / conv)
    1     194-228        54-138         332-396           167-450            5 / 9-15
    2     258-299        64-187         416-474           188-423            8 / 15-22
    3     398-454        146-204        552-622           271-577            12 / 20-29
    4     436-495        132-214        589-659           292-590            16 / 25-34
    5     524-541        167-236        659-709           360-647            20 / 32-42
    6     525-542        158-240        671-714           345-654            21 / 28-42

4.5 Core levels 7-8 (levels 1-6 unchanged except the ``ents`` fix: the article's first letter is free of case, so a
sentence may open with "The heron"; the prompt says so). Counts and rule changes, not more constraints of the same kind
(the 4.1 evidence: presence/absence rules do not order the frontier, counts are hard for everyone and light here):
  pwords (paragraph i has exactly n_i words), twords (total exactly N, the engine ``word_range`` grader; replaces
  ``window``), swords (every sentence exactly k words; replaces ``sents``; with pwords each paragraph holds n_i/k
  sentences), lcount (a rare letter exactly n times). All weigh 1 and are outside the half-met gate.
  conv adds rule changes: from piece 2 the first forbidden word flips to "use it exactly n times" (``kwf``, weight 2;
  L8 also flips the acrostic word in piece 3) and in piece 3 the semicolon rule is scoped to odd paragraphs.
  Every new-level case sends max_tokens 6144 (the 24,576 budget rule). ``mutants_named`` adds count-off-by-one,
  flip-ignored and scope-ignored. Measured (30 seeds, release_seed "r"): write L7 prompt 482-546 / answer 96-164 /
  18 checks, write L8 526-580 / 142-369 / 20, conv L7 764-828 / 246-422 / 33-42, conv L8 802-875 / 385-821 / 36-43.

Answer shape for grade()/reference()/mutants(): the reply text (write) or the three
replies joined with ``TURN_SEP`` (conv). ``materialize`` returns the engine case shape
(rpdeep-style: ``turns`` = scripted user turns, ``checks`` with ``fact``/``weight``).
"""
from __future__ import annotations

import copy
import functools
import json
import re
import sys
from pathlib import Path

from ._stack_common import (TURN_SEP, cap_passive, grade_weighted, join_turns, rng,
                            split_turns)

from .. import gen as _gen  # noqa: E402  (one seed derivation)

FAMILY = "istack"
#: pin of the shared module this family grades with (_stack_common.py: turn split, passive cap, weighted grade):
#: chains.revision() digests only THIS file, so a change to it must change this line (tests/test_gen_istack.py)
_STACK_SHA = "d9ed8da25a93decf"
VARIANTS = ("write", "conv", "nsfw")
#: the nsfw variant's framing bank, relative to cases/ladder (the bank lives with the WP-6 NSFW pack,
#: not in this module); a generator block may name another file with ``"frames": <path>``
FRAMES_DEFAULT = "nsfw/nsfw_writing/istack-frames.json"

_EASY = ["window", "paras", "forbl", "closing", "nodig", "quest", "forbw"]
_P2 = _EASY + ["sents", "sections", "kw"]
_P3 = _P2 + ["facts", "acro", "ents"]
_P4 = _P3 + ["maxlen", "semis", "opening"]
LEVELS = {
    1: {"n": 4, "pool": _EASY, "force": [], "K": 0, "m": 3},
    2: {"n": 7, "pool": _P2, "force": [], "K": 0, "m": 3},
    3: {"n": 10, "pool": _P3, "force": ["window", "acro", "facts"], "K": 2, "m": 3},
    4: {"n": 13, "pool": _P4, "force": ["window", "acro", "facts", "ents"], "K": 3, "m": 3},
    5: {"n": 16, "pool": _P4, "force": list(_P4), "K": 4, "m": 4},
    6: {"n": 16, "pool": _P4, "force": list(_P4), "K": 5, "m": 5},
    # 4.5 Core levels: exact counts (write) and rule changes across turns (conv). ``new`` = kinds added on top of the
    # N old-kind draw, per variant. twords replaces window and swords replaces sents where present (they would
    # conflict). pwords/twords/swords/lcount weigh 1 and sit outside the half-met gate: a miss costs points, it
    # cannot zero a turn. conv levels flip a standing rule (forbw -> exactly n; L8 also the acrostic word) and
    # scope the semicolon rule to odd paragraphs.
    7: {"n": 12, "pool": _P4, "force": ["window", "acro", "facts", "ents", "forbw", "semis"], "K": 4, "m": 4,
        "new": {"write": ["pwords", "lcount"], "conv": ["pwords", "lcount"]}, "flips": ["forbw"]},
    8: {"n": 12, "pool": _P4, "force": ["acro", "facts", "ents", "forbw", "semis"], "K": 4, "m": 4,
        "new": {"write": ["pwords", "twords", "swords", "lcount"], "conv": ["pwords", "swords", "lcount"]},
        "flips": ["forbw", "acro"]},
}

# median reference-answer tokens (chars/4, 30 seeds; tests/test_gen_answer_tokens.py re-measures):
# the per-variant table is exact, ``answer_tokens`` is the variant mean (what ``budget`` falls back to)
_ANSWER_TOKENS = {7: {'write': 128, 'conv': 320}, 8: {'write': 194, 'conv': 696}, 1: {'write': 90, 'conv': 288}, 2: {'write': 119, 'conv': 359}, 3: {'write': 164, 'conv': 411}, 4: {'write': 178, 'conv': 458}, 5: {'write': 198, 'conv': 495}, 6: {'write': 202, 'conv': 494}}
for _lv, _row in LEVELS.items():
    _row["answer_tokens_by_variant"] = _ANSWER_TOKENS[_lv]
    _row["answer_tokens"] = round(sum(_ANSWER_TOKENS[_lv].values()) / len(_ANSWER_TOKENS[_lv]))

# standing kinds stay in force once stated (conv); the rest apply to one piece only
_STANDING = {"window", "paras", "acro", "forbl", "forbw", "sents", "closing", "sections", "nodig", "maxlen", "swords"}
_NEW = ("pwords", "twords", "swords", "lcount")
#: single-occurrence words per rare letter (each holds the letter exactly once; none holds another rare letter)
LCOUNT_WORDS = {
    "z": ["zinc", "dozen", "zebra", "glaze", "maze", "prize", "adze", "lazy"],
    "x": ["box", "fox", "relax", "taxi", "wax", "axle", "oxen", "mixed"],
    "q": ["quill", "queen", "squid", "quart", "quilt", "quest", "equal", "quote"],
    "j": ["jar", "jug", "jam", "jolly", "major", "jelly", "ajar", "enjoy"],
}

SUBSTANCE = 30
#: a turn scores 0 when more than this share of its 8-word runs are copied from the prompt text
#: (dossier facts and the closing line, which the rules ask for verbatim, are excluded)
ECHO_MAX_SHARE = 0.25
_SUBST = r"(?=\s*(?:\S+\s+){%d}\S)" % (SUBSTANCE - 1)
_WORD = re.compile(r"[A-Za-z0-9']+")

# (genre, subject, topic word): every turn must use its topic word, a positive, topic-bound rule
TOPICS = [
    ("short story", "a ferry crossing in fog", "ferry"), ("diary entry", "the first night at a lighthouse", "lighthouse"),
    ("letter to a neighbour", "a lost kite", "kite"), ("product description", "a hand-cranked radio", "radio"),
    ("news brief", "a village bake sale", "sale"), ("travel note", "a town with one bridge", "bridge"),
    ("short story", "a market stall that sells only clocks", "clocks"), ("speech", "the opening of a small library", "library"),
    ("diary entry", "a week of rain on the farm", "rain"), ("short story", "a train that stops at a field", "train"),
]
# "never do X" rules a generic off-topic paragraph satisfies: capped at 15% of the weight (cap_passive)
PASSIVE = {"forbl", "forbw", "nodig", "maxlen", "sents"}
FACTS = [
    "the east stair has forty-one steps", "Marrow Quay was rebuilt after the flood",
    "salt cod is dried on the north rail", "the brass bell weighs about two hundred kilos",
    "fog horns sound twice at dusk", "Tamsin Gale keeps the harbour ledger",
    "the old pier ends at a green door", "gulls nest on the cold chimney",
    "the keeper's cat is named Biscuit", "tea is served from a copper urn",
    "the lamp room faces due west", "a chalk line marks the tide at noon",
    "Oskar Venn mends the sails in March", "the cellar holds nine barrels of lamp oil",
]
ENTITIES = ["the heron", "Captain Pell", "the baker", "a stray goat", "Miss Arden", "the postman",
            "old Joss", "the ferry", "a red kite", "the tailor"]
TITLES = ["Arrival", "The Long Stair", "Weather", "Small Mercies", "Departure", "Lamp Room",
          "Supplies", "Night Watch", "The Keeper", "Morning", "Low Tide", "Visitors"]
CLOSINGS = ["That was the whole of it.", "The tide keeps its own time.", "Nobody locked the door.",
            "And the lamp stayed lit.", "Morning came without a sound.", "The rest is for another day."]
ACRO = ["TIDE", "LAMP", "ROPE", "SALT", "WAVES", "BRINE", "ANCHOR", "SAILS", "MARSH", "QUAY"]
KEYWORDS = ["lantern", "harbour", "ribbon", "copper", "anchor", "meadow", "basket", "orchard"]
FORBID_WORDS = ["very", "really", "just", "suddenly", "quietly", "thing", "like", "amazing", "beautiful"]
FORBID_LETTERS = "vkwybpgfjxzq"
OPENERS = ["Today", "Somewhere", "Slowly", "Nobody", "Between", "Morning", "Often", "Beyond"]
START = {
    "a": ["Another", "Along", "Always"], "b": ["Beyond", "Before", "Behind"], "c": ["Cold", "Calm", "Close"],
    "d": ["Down", "Dusty", "Deep"], "e": ["Every", "Early", "Empty"], "f": ["Far", "Fresh", "Faded"],
    "g": ["Grey", "Gentle", "Gulls"], "h": ["Here", "Heavy", "Hollow"], "i": ["Inside", "Idle", "Into"],
    "j": ["Just", "Jars", "Joined"], "k": ["Keen", "Keys", "Knots"], "l": ["Low", "Late", "Lamps"],
    "m": ["Most", "Mild", "Morning"], "n": ["Near", "Narrow", "Nobody"], "o": ["Old", "Over", "Often"],
    "p": ["Pale", "Past", "Paths"], "q": ["Quiet", "Quick", "Quay"], "r": ["Rough", "Round", "Rested"],
    "s": ["Slow", "Salt", "Still"], "t": ["Tall", "Thin", "Tired"], "u": ["Under", "Until", "Uneven"],
    "v": ["Vast", "Veiled", "Voices"], "w": ["Wide", "Wind", "Worn"], "x": ["Xenon"],
    "y": ["Yellow", "Yards", "Yet"], "z": ["Zigzag", "Zero"],
}
FILLER = ("quiet grey boats drift slowly across wide water while cold wind moves through tall grass near "
          "stone walls and small lamps glow behind dusty glass as old gulls circle above rusted chains "
          "under heavy clouds beside narrow paths where tired walkers carry baskets of bread over uneven "
          "planks toward distant hills and open fields before evening settles on empty benches along the "
          "silent shore mild sharp rough smooth bright dim round flat hollow solid thin thick faded painted "
          "woven folded buried hidden scattered gathered lifted turned rested wandered listened counted "
          "mended stacked polished brushed ropes nets sails oars barrels crates tools bells doors steps "
          "roofs stones fences gates ladders buckets kettles blankets candles whistles mirrors shelves").split()


def _words(text: str) -> int:
    return len(_WORD.findall(text))


def _letters(s: str) -> set[str]:
    return {c for c in s.lower() if c.isalpha()}


# ------------------------------------------------------------------ checks
def _esc(s: str) -> str:
    return re.escape(s)


_RUN = r"[A-Za-z0-9']+"                    # one word, as the layout note defines it
_SEP = r"[^A-Za-z0-9'\n]+"                 # what lies between two words of a line
_SEP_S = r"[^A-Za-z0-9'.!?\n]+"            # ... inside one sentence


def _head(e: str) -> str:
    """The word that names an entity: its last word ("the heron" -> heron, "Captain Pell" -> Pell)."""
    return e.split()[-1]


def _cap_free(text: str) -> str:
    """``text`` verbatim, its first letter free of case (a sentence may start with it)."""
    if text[0].isalpha() and text[0].islower():
        return rf"[{text[0].upper()}{text[0]}]{_esc(text[1:])}"
    return _esc(text)


def _ent_rx(e: str) -> str:
    """A mention of the entity = its head word with any article or describing word before it ("a grey heron",
    "The heron", "Pell"), a plural or possessive included. The prompt says so (H1 fairness pass: every strong model
    wrote "a heron" or "the old heron" for "the heron" and lost the order check; 29 of 29 such fails had the head
    words in the asked order)."""
    return rf"\b{_cap_free(_head(e))}s?\b"


def _check(cid: str, turn: int, kind: str, weight: float = 1, **kw) -> dict:
    return {"id": cid, "fact": cid, "ftype": "fact" if kind in ("fact", "ents", "topic") else "format", "group": "constraint",
            "turns": [turn], "scope": "all", "weight": weight, "type": kw.pop("type", "require_regex"),
            "note": kind, **kw}


POSITIVE = {"paras", "acro", "sections", "closing", "facts", "ents", "quest", "kw", "semis", "opening"}


def _checks_for(active: dict, turn: int) -> list[dict]:
    out = _checks_raw(active, turn)
    for c in out:                      # content-specific constraints: the half-met gate counts these
        if c["note"] in POSITIVE or c["note"] == "fact":
            c["positive"] = True
    lo = active["window"][0] if "window" in active else SUBSTANCE
    out.append(_check(f"t{turn}-gate-len", turn, "gate", 0, type="word_range", min=max(1, int(lo * 0.9)),
                      max=10 ** 6, tolerance=0.0, gate=True, scored=False))
    return out


def _echo_gate(turn: int, active: dict, source: str) -> dict:
    exempt = [t for _, t in active.get("facts", [])] + ([active["closing"]] if "closing" in active else [])
    return _check(f"t{turn}-gate-echo", turn, "gate", 0, type="echo_guard", source=source, exempt=exempt,
                  max_share=ECHO_MAX_SHARE, gate=True, scored=False)


def _checks_raw(active: dict, turn: int) -> list[dict]:
    p = f"t{turn}-"
    out: list[dict] = []
    close = active.get("closing")
    ign = r"#{1,6}\s.*" + (f"|{_esc(close)}" if close else "")
    out.append(_check(p + "topic", turn, "topic", 2, pattern=rf"\b{_esc(active['topic'])}\b"))
    if "window" in active:
        lo, hi = active["window"]
        out.append(_check(p + "window", turn, "window", type="word_range", min=lo, max=hi, tolerance=0.0))
    if "paras" in active:
        out.append(_check(p + "paras", turn, "paras", type="line_count", min=active["paras"],
                          max=active["paras"], ignore=ign))
    if "acro" in active:
        pieces = "".join(rf"(?:#{{1,6}}[^\n]*\n\s*)*{c}[^\n]*(?:\n\s*|\Z)" for c in active["acro"])
        tail = r"(?:[^\n]*\s*)?\Z" if close else r"\Z"
        out.append(_check(p + "acro", turn, "acro", pattern=rf"\A\s*{pieces}{tail}"))
    if "sections" in active:
        body = "".join(rf"(?-i:{_esc('## ' + t)})[ \t]*\n[^#]*" for t in active["sections"])
        out.append(_check(p + "sections", turn, "sections", pattern=rf"\A\s*{body}\Z"))
    if "forbl" in active:
        out.append(_check(p + "forbl", turn, "forbl", pattern=rf"\A{_SUBST}(?![\s\S]*[{active['forbl']}])"))
    if "forbw" in active:
        alt = "|".join(_esc(w) for w in active["forbw"])
        out.append(_check(p + "forbw", turn, "forbw", pattern=rf"\A{_SUBST}(?![\s\S]*\b(?:{alt})\b)"))
    for i, (num, text) in enumerate(active.get("facts", [])):
        out.append(_check(f"{p}fact{num}", turn, "fact", 2, pattern=rf"(?-i:{_cap_free(text)})"))
    if "sents" in active:
        s = active["sents"]
        line = rf"(?!#)(?:[^.!?\n]+[.!?]+){{{s}}}"
        closing = f"(?-i:{_esc(close)})|" if close else ""
        out.append(_check(p + "sents", turn, "sents",
                          pattern=rf"\A{_SUBST}(?:(?:#{{1,6}}[^\n]*|{closing}{line})[ \t]*(?:\n|\Z)|\n)*\Z"))
    if "closing" in active:
        out.append(_check(p + "closing", turn, "closing", pattern=rf"^(?-i:{_esc(close)})[ \t]*\s*\Z"))
    if "ents" in active:
        es = [_ent_rx(e) for e in active["ents"]]
        parts = [rf"\A(?:(?!{'|'.join(es)})[\s\S])*{es[0]}"]
        for i in range(1, len(es)):
            parts.append(rf"(?:(?!{'|'.join(es[i:])})[\s\S])*{es[i]}")
        out.append(_check(p + "ents", turn, "ents", 2, pattern="(?-i:" + "".join(parts) + ")"))
    if "nodig" in active:
        out.append(_check(p + "nodig", turn, "nodig", pattern=rf"\A{_SUBST}(?![\s\S]*\d)"))
    if "quest" in active:
        out.append(_check(p + "quest", turn, "quest", pattern=rf"\A{_SUBST}[^?]*(?:\?[^?]*){{{active['quest']}}}\Z"))
    if "kw" in active:
        w, n = active["kw"]
        out.append(_check(p + "kw", turn, "kw",
                          pattern=rf"\A(?:(?!\b{w}\b)[\s\S])*(?:\b{w}\b(?:(?!\b{w}\b)[\s\S])*){{{n}}}\Z"))
    if "maxlen" in active:
        out.append(_check(p + "maxlen", turn, "maxlen",
                          pattern=rf"\A{_SUBST}(?![\s\S]*[A-Za-z]{{{active['maxlen'] + 1}}})"))
    if "semis" in active:
        out.append(_check(p + "semis", turn, "semis", pattern=rf"\A{_SUBST}[^;]*(?:;[^;]*){{{active['semis']}}}\Z"))
    if "opening" in active:
        out.append(_check(p + "opening", turn, "opening",
                          pattern=rf"\A(?:\s*#{{1,6}}[^\n]*\n)*\s*(?-i:{_esc(active['opening'])})\b"))
    tail = r"(?:[^\n]*\s*)?\Z" if close else r"\Z"
    if "kwf" in active:                      # a flipped forbidden word: now "exactly n times" (weight 2)
        w, n = active["kwf"]
        out.append(_check(p + "kwf", turn, "kwf", 2,
                          pattern=rf"\A(?:(?!\b{w}\b)[\s\S])*(?:\b{w}\b(?:(?!\b{w}\b)[\s\S])*){{{n}}}\Z"))
    if "semis_scope" in active:              # scoped: exactly n semicolons, all inside odd paragraphs
        parts = "".join(rf"(?:#{{1,6}}[^\n]*\n\s*)*{'[^\\n]*' if q % 2 == 0 else '[^;\\n]*'}(?:\n\s*|\Z)"
                        for q in range(active["P"]))
        out = [c for c in out if c["note"] != "semis"]
        out.append(_check(p + "semis", turn, "semis",
                          pattern=rf"\A{_SUBST}(?=[^;]*(?:;[^;]*){{{active['semis']}}}\Z)\s*{parts}{tail}"))
    # exact counts: engine word_range for the total; anchored word runs for paragraphs / sentences (weight 1)
    if "twords" in active:
        out.append(_check(p + "twords", turn, "twords", type="word_range", min=active["twords"],
                          max=active["twords"], tolerance=0.0))
    if "pwords" in active:
        parts = "".join(rf"(?:#{{1,6}}[^\n]*\n\s*)*{_RUN}(?:{_SEP}{_RUN}){{{n - 1}}}[^A-Za-z0-9'\n]*(?:\n\s*|\Z)"
                        for n in active["pwords"])
        out.append(_check(p + "pwords", turn, "pwords", pattern=rf"\A\s*{parts}{tail}"))
    if "swords" in active:
        k = active["swords"]
        sent = rf"{_RUN}(?:{_SEP_S}{_RUN}){{{k - 1}}}[.!?]"
        closing = f"(?-i:{_esc(close)})|" if close else ""
        out.append(_check(p + "swords", turn, "swords",
                          pattern=rf"\A{_SUBST}(?:(?:#{{1,6}}[^\n]*|{closing}(?!#)(?:[ \t]*{sent})+)[ \t]*(?:\n|\Z)|\n)*\Z"))
    if "lcount" in active:
        ch, n, _w = active["lcount"]
        out.append(_check(p + "lcount", turn, "lcount", pattern=rf"\A{_SUBST}[^{ch}]*(?:{ch}[^{ch}]*){{{n}}}\Z"))
    return out


# ------------------------------------------------------------------ statements
def _stmt(kind: str, a: dict, ctx: dict) -> str:
    v = a[kind]
    if kind == "topic":
        return f"Use the word '{v}' at least once."
    if kind == "window":
        return f"Total length between {v[0]} and {v[1]} words, inclusive (headings and the closing line count)."
    if kind == "paras":
        return f"Exactly {v} paragraphs (headings and the closing line are not paragraphs)."
    if kind == "acro":
        return f"The first letters of the paragraphs, in order, spell {v} ({len(v)} paragraphs, nothing before the first one)."
    if kind == "sections":
        return ("Put a heading line directly above each paragraph, written exactly as '## <title>', using "
                "these titles in this order: " + "; ".join(v) + ".")
    if kind == "forbl":
        return f"Never use the letter{'s' if len(v) > 1 else ''} {', '.join(c.upper() for c in v)} (either case) anywhere, headings included."
    if kind == "forbw":
        return "Never use the word" + ("s " if len(v) > 1 else " ") + ", ".join(f"'{w}'" for w in v) + " (any case)."
    if kind == "facts":
        nums = ", ".join(str(n) for n, _ in v)
        return (f"Copy dossier fact{'s' if len(v) > 1 else ''} {nums} into the text word for word (exact wording and "
                "capitalisation, inside any sentence; a capital at the start of a sentence is fine).")
    if kind == "sents":
        return f"Every paragraph has exactly {v} sentences."
    if kind == "closing":
        return f"The last line of the reply is exactly: {v}   (alone on its line; it is not a paragraph)."
    if kind == "ents":
        heads = [_head(e) for e in v]
        return ("Mention " + ", ".join(v[:-1]) + f" and {v[-1]}; the first mention of each must come in that order, "
                "so the words " + ", ".join(heads[:-1]) + f" and {heads[-1]} first appear in that order (any article "
                "or describing word is fine, as 'a grey heron' would be for 'the heron'; a capital at the start of a "
                "sentence is fine).")
    if kind == "nodig":
        return "No digits anywhere; write every number as a word."
    if kind == "quest":
        return f"Exactly {v} question mark{'s' if v > 1 else ''} in the whole reply."
    if kind == "kw":
        return f"Use the word '{v[0]}' exactly {v[1]} times (whole word, any case)."
    if kind == "maxlen":
        return f"No word longer than {v} letters."
    if kind == "semis":
        return f"Exactly {v} semicolon{'s' if v > 1 else ''} in the whole reply."
    if kind == "opening":
        return f"The first word of the first paragraph is '{v}'."
    if kind == "pwords":
        return ("Paragraph lengths, in order: " + ", ".join(str(n) for n in v) + " words exactly (headings and the "
                "closing line are not paragraphs).")
    if kind == "twords":
        return f"The whole reply is exactly {v} words (headings and the closing line count)."
    if kind == "swords":
        return f"Every sentence has exactly {v} words (the closing line, if there is one, is exempt)."
    if kind == "lcount":
        return f"The letter {v[0].upper()} (either case) appears exactly {v[1]} times in the whole reply."
    raise KeyError(kind)


_LAYOUT_NOTE = ("Layout: write each paragraph as one unbroken line, with one blank line between paragraphs. "
                "A word is a run of letters, digits or apostrophes. A sentence ends with . ! or ? and those marks "
                f"are used only to end sentences. Write at least {SUBSTANCE} words. Reply with the piece only.")


# ------------------------------------------------------------------ layout + render
class Infeasible(Exception):
    """A draw whose exact counts cannot be met (the plan is re-drawn from the next attempt)."""


def _iwords(sn: dict) -> int:
    """Words a sentence holds before any filler: its head word plus its items."""
    return 1 + sum(_words(x) for x in sn["items"])


def _place_capped(active: dict, g, sents: list[dict], cap: int) -> bool:
    """swords: put every item into a sentence so that no sentence outgrows k words (False = re-draw)."""
    S = len(sents)
    load = [1] * S

    def put(i: int, item: str) -> None:
        sents[i]["items"].append(item)
        load[i] += _words(item)

    def fits(i: int, item: str) -> bool:
        return load[i] + _words(item) <= cap

    prev = -1
    for e in active.get("ents", []):                         # first mentions strictly in order
        c = [i for i in range(prev + 1, S) if fits(i, e)]
        if not c:
            return False
        prev = g.choice(c)
        put(prev, e)
    loose = [t for _, t in active.get("facts", [])] + [active["topic"]]
    for key in ("kw", "kwf"):
        if key in active:
            loose += [active[key][0]] * active[key][1]
    if "lcount" in active:
        loose += list(active["lcount"][2])
    for item in loose:
        c = [i for i in range(S) if fits(i, item)]
        if not c:
            return False
        put(g.choice(c), item)
    return True


def _layout(active: dict, g, pglobal: int | None) -> dict | None:
    has_p = any(k in active for k in ("acro", "paras", "sections")) or "pwords" in active
    P = pglobal if (has_p and pglobal) else g.randint(3, 5)
    if "swords" in active and "pwords" in active:            # paragraph i holds n_i / k sentences
        sp = [n // active["swords"] for n in active["pwords"]]
    else:
        s = active.get("sents") or g.randint(2, 3)
        sp = [s] * P
    off = [sum(sp[:p]) for p in range(P)]
    S = sum(sp)
    sents = [{"first": None, "items": [], "term": ".", "semi": False} for _ in range(S)]
    for p in range(P):
        if p == 0 and "opening" in active:
            sents[0]["first"] = active["opening"]
        elif "acro" in active:
            sents[off[p]]["first"] = active["acro_words"][p]
    if "swords" in active:
        if not _place_capped(active, g, sents, active["swords"]):
            return None
    else:
        if "ents" in active:
            idx = sorted(g.randrange(S) for _ in active["ents"])
            for i, e in zip(idx, active["ents"]):
                sents[i]["items"].append(e)
        for _, text in active.get("facts", []):
            sents[g.randrange(S)]["items"].append(text)
        sents[g.randrange(S)]["items"].append(active["topic"])
        if "kw" in active:
            for _ in range(active["kw"][1]):
                sents[g.randrange(S)]["items"].append(active["kw"][0])
    if "quest" in active:
        for i in g.sample(range(S), active["quest"]):
            sents[i]["term"] = "?"
    if "semis" in active:
        pool = range(S)
        if "semis_scope" in active:                          # odd-numbered paragraphs only (1st, 3rd, ...)
            pool = [i for p in range(0, P, 2) for i in range(off[p], off[p] + sp[p])]
        for i in g.sample(list(pool), active["semis"]):
            sents[i]["semi"] = True
    if "swords" not in active:
        for key in ("kwf",):
            if key in active:
                for _ in range(active[key][1]):
                    sents[g.randrange(S)]["items"].append(active[key][0])
        if "lcount" in active:
            for w in active["lcount"][2]:
                sents[g.randrange(S)]["items"].append(w)
    for sn in sents:
        g.shuffle(sn["items"])
    return {"P": P, "s": sp[0] if len(set(sp)) == 1 else None, "sp": sp, "off": off, "sents": sents}


def _render(lay: dict, active: dict, counts: list[int], bank: list[str], g, exact: list[int] | None = None) -> str:
    """Render the layout. ``counts`` = filler per sentence (the 4.1 rule); ``exact`` = the word total of every
    sentence (head + items + filler), for the exact-count kinds."""
    lines: list[str] = []
    P = lay["P"]
    for p in range(P):
        sent_txt = []
        for j in range(lay["sp"][p]):
            i = lay["off"][p] + j
            sn = lay["sents"][i]
            if exact is not None:
                head = sn["first"] or g.choice(bank).capitalize()
                nf = exact[i] - _iwords(sn)
                if nf < 0:
                    raise Infeasible(f"sentence {i} needs {_iwords(sn)} words, target {exact[i]}")
            else:
                f = counts[i]
                if sn["first"]:
                    head, nf = sn["first"], f
                else:
                    head, nf = g.choice(bank).capitalize(), f - 1
            pieces = list(sn["items"]) + [g.choice(bank) for _ in range(nf)]
            g.shuffle(pieces)
            toks = [head] + pieces
            if sn["semi"]:
                if len(toks) < 2:
                    raise Infeasible("a one-word sentence cannot carry a semicolon")
                toks[g.randrange(len(toks) - 1)] += ";"
            sent_txt.append(" ".join(toks) + sn["term"])
        if "sections" in active:
            lines.append("## " + active["sections"][p])
        lines.append(" ".join(sent_txt))
    if "closing" in active:
        lines.append(active["closing"])
    return "\n\n".join(lines)


def _bank(active_all: dict) -> list[str]:
    """filler words usable under EVERY standing/per-turn rule of this item."""
    fl = active_all.get("forbl", "")
    fw = {w.lower() for w in active_all.get("forbw", [])}
    kw = active_all.get("kw", ("",))[0]
    mx = active_all.get("maxlen", 99)
    lc = active_all["lcount"][0] if "lcount" in active_all else ""
    return sorted({w for w in FILLER if not (set(w) & set(fl)) and w not in fw and w != kw and len(w) <= mx
                   and not (lc and lc in w)})


# ------------------------------------------------------------------ frames (nsfw variant)
def frames_path(gen: dict) -> Path:
    from .. import ladder
    p = Path(gen.get("frames") or FRAMES_DEFAULT)
    return p if p.is_absolute() else ladder.LADDER_DIR / p


@functools.lru_cache(maxsize=8)
def _load_frames(path: str, mtime: int) -> tuple:
    d = json.loads(Path(path).read_text(encoding="utf-8"))
    rows = d.get("frames") if isinstance(d, dict) else d
    if not isinstance(rows, list) or not rows:
        raise _gen.GeneratorError(f"istack nsfw: {path}: no frames (a list of {{id, frame, characters}})")
    for r in rows:
        if not (isinstance(r, dict) and r.get("id") and r.get("frame") and r.get("characters")
                and all(isinstance(c, dict) and c.get("name") and c.get("age") for c in r["characters"])):
            raise _gen.GeneratorError(f"istack nsfw: {path}: a frame needs id, frame and characters [{{name, age}}]")
    return tuple(json.dumps(r, sort_keys=True) for r in rows)


def load_frames(gen: dict) -> list[dict]:
    p = frames_path(gen)
    try:
        m = p.stat().st_mtime_ns
    except OSError:
        raise _gen.GeneratorError(f"istack nsfw variant: framing bank not found: {p} "
                                  "(it ships with the NSFW pack; or name one with generator 'frames')") from None
    return [json.loads(x) for x in _load_frames(str(p), m)]


# ------------------------------------------------------------------ plan
def _draw_items(seed: str, level: int, attempt: int, nturn: int = 1, frame: dict | None = None) -> dict | None:
    L = LEVELS[level]
    g = rng(seed, f"items{attempt}")
    new = list(L.get("new", {}).get("conv" if nturn == 3 else "write", []))
    pool = [k for k in L["pool"] if not (k == "window" and "twords" in new) and not (k == "sents" and "swords" in new)]
    force = [k for k in L["force"] if k in pool]
    rest = [k for k in pool if k not in force]
    kinds = force + g.sample(rest, max(0, min(L["n"], len(pool)) - len(force)))
    kinds = [k for k in pool if k in kinds] + new
    it: dict = {"kinds": kinds}
    dossier_idx = g.sample(range(len(FACTS)), 6)
    it["dossier"] = [FACTS[i] for i in dossier_idx]
    if "facts" in kinds:
        K = L["K"] or g.randint(1, 2)
        pick = sorted(g.sample(range(1, 7), min(K, 6)))
        it["facts"] = [(n, it["dossier"][n - 1]) for n in pick]
    if "ents" in kinds:
        it["ents"] = g.sample(ENTITIES, L["m"])
    if "closing" in kinds:
        it["closing"] = g.choice(CLOSINGS)
    if "acro" in kinds:
        it["acro"] = g.choice(ACRO)
    P = len(it["acro"]) if "acro" in it else (g.randint(3, 6) if "paras" in kinds else None)
    if "sections" in kinds:
        P = P or g.randint(3, 5)
        it["sections"] = g.sample(TITLES, P)
    if "paras" in kinds:
        it["paras"] = P
    it["P"] = P
    if "nodig" in kinds:
        it["nodig"] = True
    if "sents" in kinds:
        it["sents"] = g.randint(2, 3)
    if "kw" in kinds:
        it["kw"] = (g.choice(KEYWORDS), g.randint(2, 3))
    if "quest" in kinds:
        it["quest"] = g.randint(1, 2)
    if "semis" in kinds:
        it["semis"] = g.randint(1, 2)
    if frame:
        it["frame"] = frame
        it["topics"] = [("scene", frame["frame"], frame["characters"][0]["name"])]
    else:
        it["topics"] = rng(seed, f"topics{attempt}").sample(TOPICS, nturn)
    fixed = [t[2] for t in it["topics"]] + list(it.get("sections", [])) + [t for _, t in it.get("facts", [])] + list(it.get("ents", [])) \
        + ([it["closing"]] if "closing" in it else []) + ([it["kw"][0]] if "kw" in it else [])
    if "lcount" in kinds:
        # a rare letter that no fixed string (nor the acrostic) holds; its words are the only carriers
        seen = set().union(*[_letters(x) for x in fixed]) | (_letters(it["acro"]) if "acro" in it else set())
        letters = [c for c in "zxqj" if c not in seen]
        if not letters:
            return None
        ch = g.choice(letters)
        it["lcount"] = (ch, g.randint(3, 6), [])
        it["lcount"] = (ch, it["lcount"][1], g.sample(LCOUNT_WORDS[ch], it["lcount"][1]))
        fixed = fixed + list(it["lcount"][2])
    used = set().union(*[_letters(x) for x in fixed]) if fixed else set()
    if "acro" in it:
        used |= _letters(it["acro"])
    free = [c for c in FORBID_LETTERS if c not in used]
    if "forbl" in kinds:
        nl = 2 if level >= 3 else 1
        if len(free) < nl:
            return None
        it["forbl"] = "".join(sorted(g.sample(free, nl)))
    if "forbw" in kinds:
        low = " ".join(fixed).lower()
        cand = [w for w in FORBID_WORDS if not re.search(rf"\b{w}\b", low) and not (set(w) & set(it.get("forbl", "")))]
        if len(cand) < 2:
            return None
        it["forbw"] = g.sample(cand, 2)
    fw_all = [w for x in fixed for w in re.findall(r"[A-Za-z]+", x)]
    if "maxlen" in kinds:
        it["maxlen"] = max([8] + [len(w) for w in fw_all]) + g.randint(0, 1)
    bank = _bank(it)
    if len(bank) < 40:
        return None
    def ok_word(w: str) -> bool:
        lw = w.lower()
        return (not (set(lw) & set(it.get("forbl", ""))) and lw not in {x.lower() for x in it.get("forbw", [])}
                and len(w) <= it.get("maxlen", 99) and lw != it.get("kw", ("",))[0]
                and not ("lcount" in it and it["lcount"][0] in lw))

    if "acro" in it:
        ws = []
        for c in it["acro"].lower():
            opts = [w for w in START[c] if ok_word(w)]
            if not opts:
                return None
            ws.append(g.choice(opts))
        it["acro_words"] = ws
    if "opening" in kinds:
        pool = [w for w in (START[it["acro"][0].lower()] if "acro" in it else OPENERS) if ok_word(w)]
        if not pool:
            return None
        it["opening"] = g.choice(pool)
        if "acro" in it:
            it["acro_words"][0] = it["opening"]
    if "swords" in kinds:                    # k covers the longest fact plus its head and a little room
        longest = max([_words(t) for _, t in it.get("facts", [])] + [3])
        it["swords"] = max(g.randint(11, 14), longest + 4)
        if it["swords"] > 14:
            return None
    if nturn == 3 and level >= 7:            # conv: a standing forbidden word flips to "exactly n"; L8 flips the acrostic too
        it["flips"] = list(L["flips"])
        it["kwf_n"] = g.randint(2, 3)
        if "acro" in L["flips"]:
            alts = [a for a in ACRO if len(a) == len(it["acro"]) and a != it["acro"]
                    and not ("lcount" in it and it["lcount"][0] in a.lower())]
            if not alts:
                return None
            it["acro2"] = g.choice(alts)
            ws = []
            for c in it["acro2"].lower():
                opts = [w for w in START[c] if ok_word(w)]
                if not opts:
                    return None
                ws.append(g.choice(opts))
            it["acro2_words"] = ws
    if "ents" in it:                         # an entity's head word must not occur in any other text of the item
        others = " ".join([t[1] + " " + t[2] for t in it["topics"]] + [x for x in fixed if x not in it["ents"]]
                          + list(it.get("acro_words", [])) + list(it.get("acro2_words", [])) + bank).lower()
        if any(re.search(rf"\b{_head(e).lower()}s?\b", others) for e in it["ents"]):
            return None
    it["bank"] = bank
    # an opening word must not itself be a fixed string clash; forbidden-letter rules already filtered it
    return it


def _plan(gen: dict, release_seed: str, case_id: str) -> dict:
    _gen.check_variant(sys.modules[__name__], gen)
    variant = gen.get("variant", "write")
    # the nsfw frame is drawn deterministically from the bank by the instance seed
    frame = None
    if variant == "nsfw":
        bank = load_frames(gen)
        seed = _gen.instance_seed(FAMILY, release_seed, case_id, int(gen.get("level", 1)), gen.get("key"))
        frame = bank[int(rng(seed, "frame").random() * len(bank))]
    # the cached plan is shared: hand out a copy so no consumer can corrupt it
    return copy.deepcopy(_plan_cached(int(gen.get("level", 1)), variant, gen.get("key"), release_seed, case_id,
                                      json.dumps(frame, sort_keys=True) if frame else None))


@functools.lru_cache(maxsize=256)
def _plan_cached(level: int, variant: str, key, release_seed: str, case_id: str, frame_json=None) -> dict:
    gen = {"level": level, "variant": variant, "key": key}
    frame = json.loads(frame_json) if frame_json else None
    seed = _gen.instance_seed(FAMILY, release_seed, case_id, level, gen.get("key"))
    for attempt in range(60 if level < 7 else 400):
        it = _draw_items(seed, level, attempt, 3 if variant == "conv" else 1, frame)
        if it is None:
            continue
        plan = _assemble(it, seed, level, variant, attempt)
        if plan is None:
            continue
        try:
            ref = _reference_from(plan)
        except Infeasible:
            continue
        if grade_weighted(plan["checks"], split_turns(join_turns(ref))) == 1.0:
            plan["reference"] = ref
            return plan
    raise RuntimeError(f"istack: no satisfiable draw for {case_id} level {level}")


def _assemble(it: dict, seed: str, level: int, variant: str, attempt: int) -> dict | None:
    g = rng(seed, f"asm{attempt}")
    kinds = it["kinds"]
    nturn = 3 if variant == "conv" else 1
    intro = {}
    for k in kinds:
        intro[k] = 1 if nturn == 1 else g.randint(1, 3)
    if nturn == 3:
        # keep turn 1 non-trivial: the standing format rules mostly start there
        for k in kinds:
            if k in _STANDING and g.random() < 0.5:
                intro[k] = 1
    hi7 = level >= 7
    stand = _STANDING | ({"semis"} if nturn == 3 and hi7 else set())
    if nturn == 3 and hi7:                    # the rules that flip or get scoped must be in force from the first piece
        for k in ("forbw", "acro", "semis", "swords"):
            if k in intro:
                intro[k] = 1
    pw_turns = ({1} if nturn == 1 else {1, 3}) if "pwords" in kinds else set()
    topics = it["topics"]
    # per-turn active dicts
    turns = []
    wspan: list[list[int]] = []     # turns that share one window
    for t in range(1, nturn + 1):
        a: dict = {}
        for k in kinds:
            if k in ("pwords", "twords"):
                continue                     # drawn per turn once the layout is known
            if k in stand:
                if intro[k] <= t and k != "window":
                    a[k] = it[k]
            elif intro[k] == t:
                a[k] = it[k]
        if "acro" in a:
            a["acro_words"] = it["acro_words"]
        if "opening" in a and "acro" in it and "acro_words" not in a:
            a["acro_words"] = it["acro_words"]
        a["topic"] = topics[t - 1][2]
        turns.append(a)
    # opening needs acro_words only if acro active in that turn
    for a in turns:
        if "opening" in a and "acro" not in a:
            a.pop("acro_words", None)
    # sections/paras consistency with P: if sections active but acro/paras not, P = len(sections) already
    for a in turns:
        if "opening" in a and "acro" in a:
            pass
    if nturn == 3 and hi7:                    # rule changes across the three pieces
        if "forbw" in it and len(it["forbw"]) > 1:
            for t in (1, 2):                 # from piece 2: the first forbidden word is allowed, and wanted n times
                turns[t]["forbw"] = list(it["forbw"][1:])
                turns[t]["kwf"] = (it["forbw"][0], it["kwf_n"])
        if "acro2" in it and "acro" in turns[2]:
            turns[2]["acro"], turns[2]["acro_words"] = it["acro2"], it["acro2_words"]
        if "semis" in turns[2]:
            turns[2]["semis_scope"], turns[2]["P"] = True, it["P"]
    for t, a in enumerate(turns, 1):
        if t in pw_turns and "swords" in a and it["P"]:      # paragraph i = s_i sentences of k words
            gp = rng(seed, f"pw{attempt}:{t}")
            a["pwords"] = [gp.randint(1, 3) * a["swords"] for _ in range(it["P"])]
    allfix = dict(it)
    lays, c0s = [], []
    for t, a in enumerate(turns, 1):
        lay = _layout(a, rng(seed, f"lay{attempt}:{t}"), it["P"])
        if lay is None:
            return None
        # the acrostic start words are only used when acro is active
        lays.append(lay)
        c0s.append(_words(_render(lay, a, [2] * len(lay["sents"]), it["bank"], rng(seed, f"c0:{attempt}:{t}"))))
    # exact word targets per sentence (swords / pwords / twords) and the turn totals they imply
    exact: list = [None] * nturn
    exT: list = [None] * nturn
    for t, a in enumerate(turns, 1):
        lay = lays[t - 1]
        mins = [_iwords(sn) for sn in lay["sents"]]
        H = sum(_words(x) for x in a["sections"]) if "sections" in a else 0
        C = _words(a["closing"]) if "closing" in a else 0
        gx = rng(seed, f"ex{attempt}:{t}")
        ex = None
        if "swords" in a:
            if max(mins) > a["swords"]:
                return None
            ex = [a["swords"]] * len(mins)
        elif t in pw_turns:
            ns, used, ex = [], set(), []
            for q in range(lay["P"]):
                ids = range(lay["off"][q], lay["off"][q] + lay["sp"][q])
                base = sum(mins[i] for i in ids)
                n = base + gx.randint(2, 8)
                while n in used:
                    n += 1
                used.add(n)
                ns.append(n)
                cur = [mins[i] for i in ids]
                for _ in range(n - base):
                    cur[gx.randrange(len(cur))] += 1
                ex += cur
            a["pwords"] = ns
        elif t == 1 and nturn == 1 and "twords" in kinds:
            ex = list(mins)
            for _ in range(gx.randint(8, 20)):
                ex[gx.randrange(len(ex))] += 1
        if ex is not None:
            exact[t - 1], exT[t - 1] = ex, sum(ex) + H + C
            if "twords" in kinds and t == 1 and nturn == 1:
                a["twords"] = exT[0]
    # window(s): one standing window from its intro turn; optionally replaced in turn 3 (conv)
    windows: list = [None] * nturn
    if "window" in kinds:
        t0 = intro["window"] - 1
        change = nturn == 3 and t0 < 2 and g.random() < 0.5
        spans = [(t0, 2 if change else 3 - 1 + 1)] if not change else [(t0, 2), (2, 3)]
        spans = [(t0, 2), (2, 3)] if change else [(t0, nturn)]
        for (a0, b0) in spans:
            need = max(c0s[a0:b0])
            lo = max(40, 5 * ((need + g.randint(15, 40) + 4) // 5))
            hi = lo + g.choice([20, 25, 30, 40])
            Ts = [exT[t] for t in range(a0, b0) if exT[t] is not None]
            if Ts:                           # a turn with exact counts has a known total: the window must hold it
                lo = max(20, min(lo, 5 * ((min(Ts) - g.randint(5, 25)) // 5)))
                hi = max(hi, 5 * ((max(Ts) + g.randint(5, 25) + 4) // 5))
            for t in range(a0, b0):
                windows[t] = (lo, hi)
        for t in range(nturn):
            if windows[t]:
                turns[t]["window"] = windows[t]
    checks: list[dict] = []
    for t, a in enumerate(turns, 1):
        checks += _checks_for(a, t)
    cap_passive(checks, PASSIVE)
    # statements and prompts
    plan = {"level": level, "variant": variant, "turns": turns, "lays": lays, "c0": c0s, "it": it, "exact": exact,
            "topics": topics, "checks": checks, "seed": seed, "attempt": attempt, "intro": intro,
            "changed_window": nturn == 3 and "window" in kinds and
            len({w for w in windows if w}) > 1}
    plan["prompts"], plan["system"] = _prompts(plan)
    for t, a in enumerate(turns, 1):
        if not any(k in POSITIVE for k in a):
            return None            # every turn needs a content-specific rule the gate can count
    src = "\n".join(plan["prompts"] + [plan["system"]])
    for t, a in enumerate(turns, 1):
        checks.append(_echo_gate(t, a, src))
    return plan


def _prompts(plan: dict) -> tuple[list[str], str]:
    it, turns, topics = plan["it"], plan["turns"], plan["topics"]
    dossier = "\n".join(f"{i}. {f}" for i, f in enumerate(it["dossier"], 1))
    has_facts = "facts" in it["kinds"]
    order = ["topic", "window", "paras", "acro", "sections", "forbl", "forbw", "facts", "sents", "closing", "ents",
             "nodig", "quest", "kw", "maxlen", "semis", "opening", "pwords", "twords", "swords", "lcount"]
    if plan["variant"] != "conv":
        a = turns[0]
        genre, subj, _w = topics[0]
        rules = [_stmt(k, a, it) for k in order if k in a and k != "acro_words"]
        # nsfw: the bank's frame is the request, verbatim (names and ages included)
        txt = (subj if plan["variant"] == "nsfw" else f"Write a {genre} about {subj}.") + "\n\n"
        if has_facts:
            txt += f"Dossier (reference facts, used only where a rule asks):\n{dossier}\n\n"
        txt += "Rules (all of them must hold at once):\n" + "\n".join(f"{i}. {r}" for i, r in enumerate(rules, 1))
        txt += f"\n\n{_LAYOUT_NOTE}"
        sys_ = ("You are a careful writing assistant. Follow every rule in the user's message exactly. "
                "Reply with the requested piece only, with no preface and no commentary.")
        return [txt], sys_
    prompts = []
    prev: dict = {}
    for t, a in enumerate(turns, 1):
        genre, subj, _w = topics[t - 1]
        stand = _STANDING | ({"semis"} if plan["level"] >= 7 else set())
        new_standing = [k for k in order if k in a and k in stand and k != "window" and k not in prev]
        once = [k for k in order if k in a and k not in stand]
        changes = []
        if "kwf" in a and "kwf" not in prev:
            changes.append(f"CHANGE: the word '{a['kwf'][0]}' is no longer forbidden (any other forbidden word stays "
                           f"forbidden); from now on use it exactly {a['kwf'][1]} times in every piece.")
        if prev.get("acro") and a.get("acro") and a["acro"] != prev["acro"]:
            changes.append(f"CHANGE: the acrostic word is now {a['acro']} (replacing {prev['acro']}); same number of "
                           "paragraphs, nothing before the first one.")
        if "semis_scope" in a and "semis_scope" not in prev:
            changes.append(f"CHANGE: the semicolon rule now covers only the odd-numbered paragraphs (1st, 3rd, ...): "
                           f"exactly {a['semis']} semicolon{'s' if a['semis'] > 1 else ''} in the whole reply, all of them "
                           "inside odd-numbered paragraphs, none in the even-numbered ones.")
        win_new = "window" in a and (t == 1 or "window" not in prev or prev["window"] != a["window"])
        parts = []
        if t == 1:
            parts.append(f"Let's write three short pieces, one per message, all as a {genre} or close to it. "
                         f"First piece: about {subj}.")
        elif t == 2:
            parts.append(f"Thanks. Second piece: about {subj}. Every standing rule from my first message still applies.")
        else:
            parts.append(f"Last piece: about {subj}. Every standing rule so far still applies, including any change "
                         f"I made to the length.")
        if new_standing or win_new or changes:
            hdr = "Standing rules (they stay in force until I change them):" if t == 1 else \
                  "New standing rules, in force from now on:"
            lines = [_stmt(k, a, it) for k in new_standing]
            if win_new:
                if "window" in prev:
                    lines.append(f"CHANGE: the length rule is now {a['window'][0]} to {a['window'][1]} words "
                                 f"(replacing {prev['window'][0]} to {prev['window'][1]}).")
                else:
                    lines.append(_stmt("window", a, it))
            lines += changes
            parts.append(hdr + "\n" + "\n".join(f"- {x}" for x in lines))
        if once:
            parts.append("For this piece only:\n" + "\n".join(f"- {_stmt(k, a, it)}" for k in once))
        if t == 1:
            parts.append(_LAYOUT_NOTE)
        prompts.append("\n\n".join(parts))
        prev = a
    sys_ = ("You are a careful writing assistant in a three-message session. Follow every rule the user has set, "
            "including standing rules from earlier messages, exactly. Reply with the requested piece only, with no "
            "preface and no commentary.")
    if has_facts:
        sys_ += f"\n\nDossier (reference facts, used only where a rule asks):\n{dossier}"
    return prompts, sys_


def _reference_from(plan: dict) -> list[str]:
    out = []
    for t, a in enumerate(plan["turns"], 1):
        lay = plan["lays"][t - 1]
        S = len(lay["sents"])
        counts = [2] * S
        c0 = plan["c0"][t - 1]
        g = rng(plan["seed"], f"cnt{plan['attempt']}:{t}")
        if "window" in a:
            lo, hi = a["window"]
            target = lo + (hi - lo) // 2
        else:
            target = max(c0 + g.randint(6, 14), SUBSTANCE + 6)
        for _ in range(max(0, target - c0)):
            counts[g.randrange(S)] += 1
        out.append(_render(lay, a, counts, plan["it"]["bank"], rng(plan["seed"], f"fill{plan['attempt']}:{t}"),
                           exact=plan.get("exact", [None] * len(plan["turns"]))[t - 1]))
    return out


# ------------------------------------------------------------------ public API
def materialize(gen: dict, *, release_seed: str, case_id: str) -> dict:
    plan = _plan(gen, release_seed, case_id)
    n_con = len(plan["it"]["kinds"])
    return {
        "id": case_id, "category": "rpdeep", "system": plan["system"], "history": [],
        "turns": plan["prompts"], "temperature": 0.7, "top_p": 0.95, "max_tokens": 6144 if plan["level"] >= 7 else 3000,
        "user_name": "the user", "checks": plan["checks"], "judge_elements": [], "programmatic": True,
        "generator": dict(gen),
        **({"characters": plan["it"]["frame"]["characters"]} if plan["it"].get("frame") else {}),
        "meta": {"family": FAMILY, "variant": plan["variant"],
                 **({"frame": plan["it"]["frame"]["id"]} if plan["it"].get("frame") else {}), "level": plan["level"], "n_constraints": n_con,
                 "kinds": plan["it"]["kinds"], "n_checks": sum(1 for c in plan["checks"] if not c.get("gate")),
                 "prompt_tokens_est": round(sum(len(p) for p in plan["prompts"]) / 4 + len(plan["system"]) / 4),
                 "reference_tokens_est": round(sum(len(r) for r in plan["reference"]) / 4)},
    }


def reference(gen: dict, *, release_seed: str, case_id: str) -> str:
    return join_turns(_plan(gen, release_seed, case_id)["reference"])


def grade(answer: str, materialized: dict) -> float:
    return grade_weighted(materialized["checks"], split_turns(answer))


def mutants(gen: dict, *, release_seed: str, case_id: str) -> list[str]:
    return list(mutants_named(gen, release_seed=release_seed, case_id=case_id).values())


def mutants_named(gen: dict, *, release_seed: str, case_id: str) -> dict[str, str]:
    plan = _plan(gen, release_seed, case_id)
    mat = materialize(gen, release_seed=release_seed, case_id=case_id)
    ref = plan["reference"]
    cands: list[list[str]] = []

    def per_turn(fn):
        for t in range(len(ref)):
            r = list(ref)
            r[t] = fn(ref[t])
            cands.append(r)

    per_turn(lambda s: s.rsplit("\n\n", 1)[0])                                  # drop the last block
    per_turn(lambda s: s + "\n\nIt was 7 o'clock.")                             # extra paragraph + digit
    per_turn(lambda s: "\n\n".join(reversed(s.split("\n\n"))))                  # reversed block order
    per_turn(lambda s: s.replace(" ", "  ", 1).replace(",", "") + " Really, very.")
    per_turn(lambda s: " ".join(s.split()[: max(8, len(s.split()) // 3)]))     # truncated
    per_turn(lambda s: s.replace("a", "").replace("e", "").replace("o", "")[:200])
    per_turn(lambda s: s.lower().replace("the", "The"))
    per_turn(lambda s: s.replace(".", "?", 1))
    cands.append(["Here is a short piece for you."] * len(ref))
    named: dict[str, list[str]] = {}
    filler = plan["it"]["bank"][0]
    kinds = plan["it"]["kinds"]
    if plan["level"] >= 7:
        if any(k in kinds for k in ("pwords", "twords", "swords")):
            r = list(ref)                                    # one word added to the first sentence of piece 1
            r[0] = re.sub(r"([.!?])", lambda m: f" {filler}{m.group(1)}", ref[0], count=1)
            named["count-off-by-one"] = r
        kw = next(((t, a["kwf"][0]) for t, a in enumerate(plan["turns"]) if "kwf" in a), None)
        if kw:                                               # obeys the OLD rule after the flip: the word never appears
            r = list(ref)
            for t in range(kw[0], len(ref)):
                r[t] = re.sub(rf"\b{kw[1]}\b", filler, ref[t])
            named["flip-ignored"] = r
        sc = next((t for t, a in enumerate(plan["turns"]) if "semis_scope" in a), None)
        if sc is not None:                                   # the semicolons all land in an even paragraph
            n = plan["turns"][sc]["semis"]
            close = plan["turns"][sc].get("closing")
            blocks = ref[sc].replace(";", "").split("\n\n")
            paras = [i for i, b in enumerate(blocks) if not b.startswith("#") and b != close]
            blocks[paras[1]] = re.sub(r"(\w) ", r"\1; ", blocks[paras[1]], count=n)
            r = list(ref)
            r[sc] = "\n\n".join(blocks)
            named["scope-ignored"] = r
    out: dict[str, str] = {}
    for n, c in list(named.items()) + [(f"m{i}", c) for i, c in enumerate(cands)]:
        ans = join_turns(c)
        if grade(ans, mat) < 1.0 and ans not in out.values():
            out[n] = ans
    return out


def _main(argv: list[str] | None = None) -> None:
    import json
    import sys
    argv = [FAMILY] + (sys.argv[1:] if argv is None else argv)
    level = int(argv[1]) if len(argv) > 1 else 3
    variant = argv[2] if len(argv) > 2 else "write"
    gen = {"type": FAMILY, "level": level, "variant": variant}
    m = materialize(gen, release_seed="4.5.0", case_id="ISTACK-demo")
    print("SYSTEM:\n" + m["system"])
    for i, t in enumerate(m["turns"], 1):
        print(f"\n--- USER TURN {i} ---\n{t}")
    ref = reference(gen, release_seed="4.5.0", case_id="ISTACK-demo")
    print("\n--- REFERENCE ---\n" + ref)
    print("\n# meta:", json.dumps(m["meta"]))
    print("# grade(reference) =", grade(ref, m))


if __name__ == "__main__":
    _main()
