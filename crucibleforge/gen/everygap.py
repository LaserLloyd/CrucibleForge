"""EVERYGAP - staged Everyday-instructions pack (MakoBench 4.5 GAP lane): stacked, interacting format rules, and rules
that are amended in the conversation. Machine-checked (declarative ``checks`` grader, regex per line), no judge.

Why a new family: the Assistant basics items (IZ05 five lines of exactly 26 words with count rules) and KNOWGAP L6/L7
are solved at .93-1.00 by hosted models (bench45.yaml `assistant:` block, EXP-20261114-21). Those items stack
independent constraints, each checkable alone and satisfiable in any order. The axes here:

* INTERACTION: line k must contain a letter an exact number of times (character-level counting), start with the k-th letter of a given word (acrostic), have exactly ``base + k*step`` words
  (so each line's length is different and fixed), carry the keyword in one named line only, avoid the five most common
  function words, and the last line must end with a given word. The constraints compete for the same words.
* AMENDMENT (level 2): the request is a short conversation. Later user turns change the word, the length rule and the
  keyword's line; the net rule set is the last statement of each rule. A model that applies the first message, or only
  the last message, fails named checks (``stale`` mutants in the tests).

A word is a whitespace-separated token of letters, hyphens and apostrophes; a line ends with exactly one full stop
(stated in the prompt). Reference answers are built from a word bank and pass every check; the mutants break one rule each.
"""
from __future__ import annotations

import hashlib
import json
import random
import re

FAMILY = "everygap"
REVISION = "eg-1"
LEVELS = {1: {"amend": False}, 2: {"amend": True}}
MAX_TOKENS = 4096
SYSTEM = "You are a careful assistant. Follow the user's instructions exactly."
FORBID = ["the", "a", "and", "of", "is"]
TOPICS = ["a harbour at dawn", "a night train", "a mountain hut", "a village market", "a lighthouse keeper",
          "a rainy city", "an old library", "a river crossing"]
KEYWORDS = ["lantern", "anchor", "compass", "kettle", "ladder", "mirror"]
WORDS = """apple arrive amber boat bridge brave bright cold cedar clear calm drift dusk deep dawn east ember echo
early field fern fast fire gentle grey glow green harbor hill hush high iron isle inlet idle jade journey joy just
keen kind knot keep lake lane light low moon mist marsh mild north night nest noble ocean orchard open oak pale path
plain pine quiet quick quay quill river rain rock reed salt sand slow shore tide trail tall twin under urban upper
umber valley vast wind wave warm west yellow yard young yarn zinc zeal zone zest""".split()
BY_LETTER: dict = {}
for _w in WORDS:
    BY_LETTER.setdefault(_w[0].upper(), []).append(_w)
OK_LETTERS = {k for k, v in BY_LETTER.items() if len(v) >= 3}
ACROSTICS = [w for w in ["HARBOR", "LANTERN", "ANCHOR", "RIVER", "MOUNTAIN", "NIGHT", "WINDOW", "CASTLE", "GARDEN",
                         "FOREST", "BRIDGE", "SHADOW", "TIMBER", "VALLEY", "ORCHARD", "SILVER", "PLANET", "EMBER"]
             if set(w) <= OK_LETTERS]
FILL = [w for w in WORDS if w not in FORBID]


def _rng(key: str) -> random.Random:
    return random.Random(int.from_bytes(hashlib.sha256(key.encode()).digest()[:8], "big"))


def _spec_draw(r: random.Random, acro=None, kwline=None):
    word = acro or r.choice(ACROSTICS)
    n = len(word)
    return {"word": word, "base": r.randint(3, 5), "step": r.choice([1, 2]), "uniform": None, "letter": r.choice("aeo"),
            "keyword": r.choice(KEYWORDS), "kwline": kwline or r.randint(2, n - 1), "final": r.choice(
                ["morning", "harbor", "silence", "water", "winter", "home"])}


def lens(spec) -> list[int]:
    n = len(spec["word"])
    if spec["uniform"]:
        return [spec["uniform"]] * n
    return [spec["base"] + spec["step"] * k for k in range(n)]


def targets(spec) -> list[int]:
    """How many times the spec's letter appears in line k (cycle 2, 1, 3)."""
    return [[2, 1, 3][k % 3] for k in range(len(spec["word"]))]


def _count(line: str, letter: str) -> int:
    return line.lower().count(letter)


def build_lines(spec: dict, r: random.Random, *, skip=()) -> list[str]:
    """A reference answer for ``spec``; ``skip`` names a rule to break on purpose (mutants)."""
    L = lens(spec)
    out = []
    n = len(spec["word"])
    for k in range(n):
        letter = spec["word"][k]
        cnt = L[k]
        if "length" in skip:
            cnt = max(2, cnt - 1 if k % 2 else cnt + 1)
        want = targets(spec)[k] + (1 if ("letter" in skip and k == 0) else 0)
        line = None
        for _try in range(6000):
            words = [r.choice(BY_LETTER[letter]).capitalize()]
            while len(words) < cnt:
                w = r.choice(FILL)
                if w == spec["keyword"]:
                    continue
                words.append(w)
            if k + 1 == spec["kwline"] and "keyword" not in skip:
                words[1] = spec["keyword"]
            if k == n - 1 and "final" not in skip:
                words[-1] = spec["final"]
            if "forbidden" in skip and k == 0 and len(words) > 2:
                words[2] = "the"
            cand = " ".join(words) + "."
            if _count(cand, spec["letter"]) == want:
                line = cand
                break
        if line is None:
            raise RuntimeError("everygap: no line found for the letter rule")
        out.append(line)
    if "acrostic" in skip:
        out = out[::-1] if len(set(l[0] for l in out)) > 1 else out
    return out


def checks_for(spec: dict) -> list[dict]:
    L = lens(spec)
    n = len(L)
    ch = [{"id": "lines", "type": "regex", "pattern": r"\A[^\n]+(?:\n[^\n]+){%d}\Z" % (n - 1)},
          {"id": "charset", "type": "regex", "pattern": r"\A[A-Za-z'\- .\n]+\Z"}]
    for k in range(n):
        ch.append({"id": f"line{k + 1}-words-and-letter", "type": "regex",
                   "pattern": r"\A(?:[^\n]*\n){%d}%s[A-Za-z'\-]*(?: [A-Za-z'\-]+){%d}\.(?:\n|\Z)" % (k, spec["word"][k], L[k] - 1)})
    for k, t in enumerate(targets(spec)):
        x = spec["letter"]
        ch.append({"id": f"line{k + 1}-letter-{x}-count", "type": "regex",
                   "pattern": r"\A(?:[^\n]*\n){%d}(?:[^%s%s\n]*[%s%s]){%d}[^%s%s\n]*(?:\n|\Z)" % (k, x, x.upper(), x, x.upper(), t, x, x.upper())})
    ch.append({"id": "no-extra-full-stops", "type": "regex", "pattern": r"\A(?:[^.\n]+\.(?:\n|\Z))+\Z"})
    ch.append({"id": "keyword-once", "type": "keyword_count", "word": spec["keyword"], "n": 1})
    ch.append({"id": "keyword-line", "type": "regex",
               "pattern": r"\A(?:[^\n]*\n){%d}\S+ %s\b" % (spec["kwline"] - 1, spec["keyword"])})
    ch.append({"id": "forbidden-words", "type": "forbidden_words", "words": FORBID})
    ch.append({"id": "final-word", "type": "regex", "pattern": r"\b%s\.\Z" % spec["final"]})
    return ch


def _rules_text(spec) -> str:
    n = len(spec["word"])
    if spec["uniform"]:
        ln = f"every line has exactly {spec['uniform']} words"
    else:
        ln = (f"line k has exactly {spec['base']} + {spec['step']}*(k-1) words (line 1 has {spec['base']}, "
              f"line 2 has {spec['base'] + spec['step']}, and so on)")
    return (f"write one line per letter of the word {spec['word']}, so {n} lines, each starting with that letter "
            f"in order (line 1 starts with {spec['word'][0]}); {ln}; the letter '{spec['letter']}' (upper or lower "
            f"case) appears in line k exactly as often as the k-th number of this list says: {', '.join(map(str, targets(spec)))}; the word '{spec['keyword']}' appears exactly "
            f"once, as the second word of line {spec['kwline']}; never use the words {', '.join(FORBID)}; the last "
            f"line ends with the word '{spec['final']}'")


FORMAT = ("A word is a run of letters, hyphens or apostrophes between spaces. Each line is one sentence ending with "
          "exactly one full stop and has no other punctuation. Output only the lines, one per line, no title, no "
          "blank lines.")


def _feasible(*specs) -> bool:
    for sp in specs:
        try:
            build_lines(sp, random.Random(7))
        except RuntimeError:
            return False
    return True


def _spec_net(key: str, level: int):
    for attempt in range(60):
        out = _spec_net_once(f"{key}#{attempt}", level)
        if _feasible(*out[1]):
            return out
    raise RuntimeError("everygap: no feasible spec")


def _spec_net_once(key: str, level: int):
    r = _rng(key)
    topic = r.choice(TOPICS)
    s1 = _spec_draw(r)
    if level == 1:
        return topic, [s1], s1
    s2 = _spec_draw(r, kwline=None)
    while s2["word"] == s1["word"]:
        s2 = _spec_draw(r)
    net = dict(s1)
    net["word"] = s2["word"]                      # message 2 changes the word
    net["kwline"] = r.choice([k for k in range(2, len(net["word"])) if k != s1["kwline"]])   # message 3 moves it
    net["uniform"] = r.randint(5, 7)              # message 3 scraps the growing lengths
    net["letter"] = r.choice([c for c in "aeo" if c != s1["letter"]])   # and counts another letter
    return topic, [s1, s2, net], net


def materialize(gen: dict, *, release_seed: str, case_id: str) -> dict:
    level = int(gen.get("level", 1))
    key = f"{level}:{release_seed}:{case_id}:{gen.get('key', '')}"
    topic, specs, net = _spec_net(key, level)
    if level == 1:
        prompt = f"Write about {topic}. Rules: {_rules_text(net)}. {FORMAT}"
    else:
        s1, s2, _ = specs
        prompt = (
            "Here is our conversation so far.\n\n"
            f"User: Write about {topic}. Rules: {_rules_text(s1)}. {FORMAT}\n"
            "Assistant: (a first draft, now discarded)\n"
            f"User: Change of plan on the word: use {s2['word']} instead of {s1['word']}. Everything else stays as "
            "I said.\n"
            "Assistant: (a second draft, now discarded)\n"
            f"User: Three more changes. Scrap the growing line lengths: every line has exactly {net['uniform']} words. "
            f"Count the letter '{net['letter']}' instead of '{s1['letter']}' (same list of numbers). "
            f"And the word '{net['keyword']}' now goes in line {net['kwline']} (still the second word of that line, "
            "still exactly once). Keep every other rule from my first message and the word from my second.\n\n"
            "Now write the final version. " + FORMAT)
    return {"id": case_id, "category": "instruct", "system": SYSTEM, "prompt": prompt, "temperature": 0.0,
            "max_tokens": MAX_TOKENS, "grader": "checks", "grader_config": {"checks": checks_for(net)},
            "generator": dict(gen),
            "meta": {"family": FAMILY, "level": level, "checks": len(checks_for(net)), "net": net,
                     "specs": specs, "authorship": {"structure": "claude", "bank": "generated (no bank)"}}}


def _net_of(gen, release_seed, case_id):
    level = int(gen.get("level", 1))
    return _spec_net(f"{level}:{release_seed}:{case_id}:{gen.get('key', '')}", level)


def reference(gen: dict, *, release_seed: str, case_id: str) -> str:
    _t, _s, net = _net_of(gen, release_seed, case_id)
    return "\n".join(build_lines(net, _rng("ref" + case_id)))


def mutants(gen: dict, *, release_seed: str, case_id: str) -> list[str]:
    _t, specs, net = _net_of(gen, release_seed, case_id)
    r = _rng("mut" + case_id)
    out = []

    def add(spec, **kw):
        try:
            out.append("\n".join(build_lines(spec, r, **kw)))
        except RuntimeError:
            pass          # this break is not buildable for this spec: the other mutants still bite
    for s_ in ("length", "keyword", "final", "forbidden", "acrostic", "letter"):
        add(net, skip=(s_,))
    if len(specs) > 1:
        for stale in specs[:2]:           # the first message's spec, the second message's spec
            add(stale)
        add(dict(net, letter=specs[0]["letter"]))          # letter amendment missed
        add(dict(net, uniform=None))      # the word and keyword line amended, the length amendment missed
        add(dict(net, kwline=specs[0]["kwline"]))          # keyword amendment missed
    out.append("Here is a short poem about the sea.")
    return out


def grade(answer: str, materialized: dict) -> float:
    from .. import graders
    v = graders.grade_checks(answer, materialized["grader_config"])
    return 1.0 if v["grade"] == "pass" else float(v.get("rate") or 0.0)
