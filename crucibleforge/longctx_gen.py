"""Deterministic long-context haystack generator.

Long-context cases can carry a ``generator`` block instead of a literal
prompt, so a 24k-token haystack doesn't have to live verbatim in the case
file (and the suite-revision hash still covers it, because the *spec* is in
the file and generation is deterministic).

    {"id": "NIAH-16k-d50", "category": "longctx",
     "generator": {"type": "niah", "tokens": 16000, "depth": 0.5, "seed": 16050,
                   "needle": "The secret passphrase for the archive is 'amber-falcon-92'.",
                   "question": "What is the secret passphrase for the archive?"},
     "grader": "contains", "grader_config": {"needles": ["amber-falcon-92"], "answer_line": true},
     "min_context": 20000, ...}

Types:
- ``niah``: one needle at ``depth`` (0..1) inside filler prose.
- ``multikey``: several ``needles`` (list of sentences) scattered at the given
  ``depths``; the question asks about one/all of them.
- ``count``: a marker sentence repeated ``count`` times at random positions;
  the question asks how many times it appears (RULER frequent-words style).

Filler is generated from templated sentences with a seeded RNG — mundane,
varied prose that carries no useful signal, so the model must locate the
needle rather than pattern-match structure. ~4 chars/token is assumed for the
``tokens`` budget (conservative for English; real tokenizers usually see
fewer tokens than this, which keeps the case inside the stated min_context).
"""
from __future__ import annotations

import random

_SUBJECTS = ["The committee", "A local baker", "The night-shift engineer", "Our neighbour",
             "The museum curator", "A retired pilot", "The gardener", "The librarian",
             "The delivery driver", "A visiting professor", "The harbour master",
             "The school's caretaker", "A freelance translator", "The orchard owner",
             "The bus conductor", "The lighthouse keeper", "A young apprentice",
             "The town clerk", "The bookshop owner", "The ferry captain"]
_VERBS = ["noted that", "mentioned that", "reported that", "explained that",
          "recalled that", "observed that", "argued that", "confirmed that",
          "suggested that", "wrote that", "insisted that", "admitted that"]
_OBJECTS = ["the bridge repairs would finish before the autumn fair",
            "the old clock in the square had lost eleven minutes over the winter",
            "the river path floods only after three days of steady rain",
            "the market stalls now open an hour later on Thursdays",
            "the new bus timetable confused most of the regular passengers",
            "the apple harvest was smaller than last year but sweeter",
            "the choir needed two more tenors before the spring concert",
            "the recycling collection had moved to alternate Mondays",
            "the harbour lights were replaced with warmer LEDs",
            "the town archive had finally been catalogued by volunteers",
            "the bakery's rye loaf sells out by ten most mornings",
            "the footbridge railing was repainted a deep green",
            "the allotment waiting list had grown to forty names",
            "the ferry runs every forty minutes in the low season",
            "the library extended its opening hours during exams",
            "the annual kite festival drew a record crowd despite the wind",
            "the old cinema had been converted into a climbing gym",
            "the roundabout planting was chosen by the primary school",
            "the tide tables were reprinted after a misprint was spotted",
            "the station café changed hands for the third time in a decade"]
_TAILS = ["", "", "", ", which surprised nobody", ", at least according to the minutes",
          ", though opinions differed", ", and the matter was left there",
          ", weather permitting", ", pending a final vote", ", as usual"]


def _sentence(rng: random.Random) -> str:
    return (f"{rng.choice(_SUBJECTS)} {rng.choice(_VERBS)} {rng.choice(_OBJECTS)}"
            f"{rng.choice(_TAILS)}.")


def _paragraph(rng: random.Random, n: int) -> str:
    return " ".join(_sentence(rng) for _ in range(n))


def filler(chars: int, seed: int) -> list[str]:
    """Paragraphs of filler totalling roughly ``chars`` characters."""
    rng = random.Random(seed)
    out: list[str] = []
    total = 0
    while total < chars:
        para = _paragraph(rng, rng.randint(4, 8))
        out.append(para)
        total += len(para) + 2
    return out


def _insert(paragraphs: list[str], sentence: str, depth: float) -> list[str]:
    idx = max(0, min(len(paragraphs), int(round(depth * len(paragraphs)))))
    return paragraphs[:idx] + [sentence] + paragraphs[idx:]


def build(spec: dict) -> str:
    kind = spec.get("type", "niah")
    tokens = int(spec.get("tokens", 4000))
    seed = int(spec.get("seed", 1))
    chars = tokens * 4
    paras = filler(chars, seed)
    preamble = spec.get("preamble",
                        "Below is a long document. Read it carefully; a question follows at the end.")
    if kind == "niah":
        paras = _insert(paras, spec["needle"], float(spec.get("depth", 0.5)))
    elif kind == "multikey":
        needles = spec["needles"]
        depths = spec.get("depths") or [(i + 1) / (len(needles) + 1) for i in range(len(needles))]
        # insert from deepest to shallowest so earlier insertions don't shift later ones
        for needle, depth in sorted(zip(needles, depths), key=lambda t: -t[1]):
            paras = _insert(paras, needle, float(depth))
    elif kind == "count":
        rng = random.Random(seed + 7)
        n = int(spec["count"])
        positions = sorted(rng.sample(range(len(paras)), n), reverse=True)
        for pos in positions:
            paras.insert(pos, spec["marker"])
    else:
        raise ValueError(f"unknown longctx generator type {kind!r}")
    body = "\n\n".join(paras)
    return f"{preamble}\n\n{body}\n\n{spec['question']}"


def materialize(case: dict) -> dict:
    """Fill ``case['prompt']`` from ``case['generator']`` (in place)."""
    spec = case["generator"]
    case["prompt"] = build(spec)
    case.setdefault("min_context", int(spec.get("tokens", 4000)) + 2048)
    return case
