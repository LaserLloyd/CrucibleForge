"""CIPHERCHAT generator (gen2 chat, 4.5; lab card EXP-20261114-26): work out a hidden word machine over a chat.

The conversational form of BLACK BOX (the one task shape that has split Haiku from Sonnet) and of Jake's R-ideas
("a system the model must characterise from measurements", "like cryptography"). A user has a gadget that turns a
word into another word by a FIXED sequence of up to three steps taken from a list the system prompt states in full.
Every turn the user reports new readings (`WORD -> OUTPUT`) and asks what the gadget will show for three new words.
Along the way the user corrects a misread reading, and flips the gadget to a second setting (a different hidden
sequence) and later back: readings belong to the setting they were taken on.

Fairness: every asked word has ONE answer given the readings so far on the current setting (corrections applied)
and the stated step list: the generator enumerates every step sequence (``_PIPES``, ~44k) and keeps a query only when
every sequence consistent with the evidence gives the same output. So the answer is inferable by a blind reader from
the prompt alone; no hidden convention.

Grading: one check per asked word (exact output, case-insensitive), ``element: check``. Lines are found anywhere in
the reply as `WORD -> OUTPUT` (also `=>`, `→`, `:`; bullets, numbering, quotes, backticks and bold tolerated); the
last line for a word wins.
"""
from __future__ import annotations

import copy
import functools
import re
import sys

from .. import gen as _gen
from ._stack_common import TURN_SEP, join_turns, rng, split_turns

FAMILY = "cipherchat"
A = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
VOW = "AEIOU"
MAX_TOKENS = 3000


# ------------------------------------------------------------ the step list (stated verbatim in the system prompt)
def _shift(w, k):
    return "".join(A[(A.index(c) + k) % 26] for c in w)


def _rot(w, r):
    r %= len(w)
    return w[r:] + w[:r]


def _swap(w):
    return "".join(w[i + 1] + w[i] for i in range(0, len(w) - 1, 2)) + (w[-1] if len(w) % 2 else "")


def _vow(w):
    return "".join(VOW[(VOW.index(c) + 1) % 5] if c in VOW else c for c in w)


def _atbash(w):
    return "".join(A[25 - A.index(c)] for c in w)


def _prog(w):
    return "".join(A[(A.index(c) + i) % 26] for i, c in enumerate(w))


STEPS = [("reverse", lambda w: w[::-1])]
STEPS += [(f"shift{k}", (lambda k: lambda w: _shift(w, k))(k)) for k in range(1, 26)]
STEPS += [(f"rotate{r}", (lambda r: lambda w: _rot(w, r))(r)) for r in (1, 2, 3)]
STEPS += [("swap", _swap), ("vowel", _vow), ("mirror", _atbash), ("ramp", _prog),
          ("droplast", lambda w: w[:-1] if len(w) > 1 else w), ("doublefirst", lambda w: w[0] + w)]
STEP_TEXT = (
    "- REVERSE: write the word backwards (STONE -> ENOTS).\n"
    "- SHIFT k (k is one fixed number from 1 to 25): move every letter k places on in the alphabet, wrapping Z to A "
    "(SHIFT 2: ZOO -> BQQ).\n"
    "- ROTATE r (r is 1, 2 or 3): move the first r letters to the end (ROTATE 2: STONE -> ONEST).\n"
    "- SWAP: swap letters 1-2, 3-4, 5-6 and so on; an odd last letter stays put (STONE -> TSNOE).\n"
    "- VOWEL: replace each vowel by the next vowel A->E->I->O->U->A; other letters stay (STONE -> STUNI).\n"
    "- MIRROR: replace each letter by its mirror in the alphabet, A<->Z, B<->Y, C<->X and so on (STONE -> HGLMV).\n"
    "- RAMP: move the 1st letter 0 places on, the 2nd 1 place, the 3rd 2 places and so on, wrapping Z to A "
    "(STONE -> SUQQI).\n"
    "- DROPLAST: remove the last letter (STONE -> STON).\n"
    "- DOUBLEFIRST: write the first letter twice (STONE -> SSTONE).\n")

# settings: how many dial positions are introduced (one per turn from turn 1); new: readings for a new setting;
# qn: asked words on the new setting; qo: asked words on earlier settings (state across the chat); T: turns
LEVELS = {
    1: {"depth": (1, 2), "settings": 4, "T": 5, "new": 2, "qn": 2, "qo": 1},
    2: {"depth": (2, 2), "settings": 5, "T": 6, "new": 2, "qn": 2, "qo": 1},
    3: {"depth": (2, 3), "settings": 6, "T": 7, "new": 2, "qn": 2, "qo": 2},
    4: {"depth": (3, 3), "settings": 6, "T": 7, "new": 2, "qn": 2, "qo": 2},
    # cycle 2 (L3 probe: Sonnet 25/25 twice, Haiku .72): ONE reading per new setting, exactly three steps
    5: {"depth": (3, 3), "settings": 6, "T": 7, "new": 1, "qn": 2, "qo": 2},
}
SETTINGS = "ABCDEFGH"
WORDS = """STONE RIVER MAPLE CLOUD BRICK LEMON TIGER PLANT CHAIR OCEAN EAGLE MANGO PIANO QUILT ROBIN SALAD TOWEL
VELVET WAGON ZEBRA CANDLE DOLPHIN FOREST GARDEN HARBOR ISLAND JUNGLE KETTLE LANTERN MARBLE NAPKIN ORCHID PEPPER
RABBIT SADDLE TEAPOT VIOLIN WALNUT YOGURT BADGER CAMERA DONKEY FALCON GINGER HAMMER INSECT JACKET LADDER MEADOW
NICKEL OYSTER PARROT RIBBON SILVER TUNNEL BUTTON COTTON DESERT FOSSIL GOBLIN HELMET KITTEN LOCKET MUFFIN NUTMEG
PEBBLE RADISH SUMMIT TICKET VALLEY WINTER APPLE BEACH CORAL DAISY FLUTE GRAPE HONEY JEWEL LIGHT MOUSE NORTH
PEARL QUEEN SHEEP TRAIN UNCLE WHALE YACHT ARROW BLOOM CRANE DRIFT FROST GLOVE""".split()
USERS = ["Wren", "Corin", "Maren", "Teo", "Lio", "Sunny", "Ines", "Pell"]
GADGETS = ["word lock", "toy code box", "label printer", "puzzle radio", "old cipher wheel"]


def _pipes(dmax: int):
    out = [()]
    for d in range(1, dmax + 1):
        out += [p + (i,) for p in out if len(p) == d - 1 for i in range(len(STEPS))]
    return [p for p in out if p]


@functools.lru_cache(maxsize=4)
def _PIPES(dmax: int = 3):
    return _pipes(dmax)


def run(pipe, w: str) -> str:
    for i in pipe:
        w = STEPS[i][1](w)
    return w


def pipe_text(pipe) -> str:
    return ", then ".join(STEPS[i][0].upper().replace("SHIFT", "SHIFT ").replace("ROTATE", "ROTATE ") for i in pipe)


class _Fail(Exception):
    pass


# ------------------------------------------------------------ consistency
class _Space:
    """All step sequences of depth <= 3 with their outputs on a word pool (built prefix by prefix), narrowed by
    readings. ``mindepth[i]`` = the shortest depth of any sequence with the same outputs on the whole pool."""

    def __init__(self, words: list[str]):
        self.words = words
        self.pipes = _PIPES(3)
        pos_ = {p: i for i, p in enumerate(self.pipes)}
        self.out = {}
        for w in words:
            col = [None] * len(self.pipes)
            for i, p in enumerate(self.pipes):
                prev = w if len(p) == 1 else col[pos_[p[:-1]]]
                col[i] = STEPS[p[-1]][1](prev)
            self.out[w] = col
        sig_depth: dict = {}
        sigs = list(zip(*(self.out[w] for w in words)))
        for i, sg in enumerate(sigs):
            d = len(self.pipes[i])
            if sig_depth.get(sg, 9) > d:
                sig_depth[sg] = d
        self.mindepth = [sig_depth[sg] for sg in sigs]
        self.identity = [all(a == b for a, b in zip(sg, words)) for sg in sigs]

    def consistent(self, readings: list[tuple[str, str]]) -> list[int]:
        idx = range(len(self.pipes))
        for w, o in readings:
            col = self.out[w]
            idx = [i for i in idx if col[i] == o]
        return list(idx)

    def answers(self, idx: list[int], w: str) -> set:
        col = self.out[w]
        return {col[i] for i in idx}


# ------------------------------------------------------------ attempt
def _attempt(g, L: dict, sp: "_Space") -> dict:
    pipes, pool = sp.pipes, sp.words

    def pick_pipe(used):
        for _ in range(4000):
            i = g.randrange(len(pipes))
            p = pipes[i]
            if not L["depth"][0] <= len(p) <= L["depth"][1] or i in used:
                continue
            # canonical: no shorter sequence does the same on the pool, and no word comes out unchanged
            if sp.mindepth[i] < len(p) or any(sp.out[w][i] == w for w in pool):
                continue
            return i
        raise _Fail

    used: list = []
    for _ in range(L["settings"]):
        used.append(pick_pipe(used))
    free = list(pool)
    g.shuffle(free)
    ev = {}
    turns = []
    for t in range(1, L["T"] + 1):
        tr = {"t": t, "new": [], "q": []}
        if t <= L["settings"]:
            s = SETTINGS[t - 1]
            i = used[t - 1]
            ev[s] = []
            for _ in range(L["new"]):
                w = free.pop()
                ev[s].append((w, sp.out[w][i]))
            tr["setting"] = s
            tr["new"] = list(ev[s])
            asks = [s] * L["qn"]
            olds = list(SETTINGS[: t - 1])
            g.shuffle(olds)
            asks += olds[: L["qo"]]
        else:
            tr["setting"] = None
            olds = list(SETTINGS[: L["settings"]])
            g.shuffle(olds)
            asks = (olds * 2)[: L["qn"] + L["qo"]]
        for s in asks:
            i = used[SETTINGS.index(s)]
            idx = sp.consistent(ev[s])
            for _ in range(len(free)):
                w = free.pop(0)
                ans = sp.answers(idx, w)
                if len(ans) == 1 and next(iter(ans)) == sp.out[w][i]:
                    tr["q"].append((s, w, sp.out[w][i]))
                    break
                free.append(w)
            else:
                raise _Fail
        tr["n_consistent"] = {s: len(sp.consistent(ev[s])) for s in ev}
        turns.append(tr)
    return {"hid": {SETTINGS[k]: pipes[i] for k, i in enumerate(used)}, "turns": turns, "ev": ev}


def _turn_text(tr: dict, gadget: str) -> str:
    parts = []
    s = tr["setting"]
    if s:
        if tr["t"] == 1:
            parts.append(f"The {gadget} has a dial with several settings. I put it on setting {s} and got these "
                         f"readings:")
        else:
            parts.append(f"I turned the dial to setting {s}, which I had not tried before. Readings:")
        parts.append("\n".join(f"{w} -> {o}" for w, o in tr["new"]))
    else:
        parts.append("No new readings today, just questions.")
    parts.append("What will it show for these?")
    parts.append("\n".join(f"{w} on setting {ss}" for ss, w, _ in tr["q"]))
    return "\n".join(parts)


def _system(user: str, gadget: str) -> str:
    return (
        f"You are helping {user} work out how their {gadget} works, over a chat. The {gadget} has a dial with "
        "settings named by letters. On each setting it turns a word into another word by applying a FIXED sequence "
        "of one, two or three steps, always the same steps in the same order for that setting (the same step may "
        "appear more than once; different settings use different sequences). Every step is one of these, and nothing "
        "else:\n"
        + STEP_TEXT +
        f"{user} reports readings as `WORD -> OUTPUT` together with the setting they were taken on, and asks what "
        f"the {gadget} will show for new words on a given setting. Use the readings of that setting from anywhere in "
        "the chat; readings from other settings say nothing about it.\n"
        "In every reply, for each word asked about in the user's latest message, write one line `WORD -> OUTPUT` "
        "(capital letters, exactly the asked word on the left). You may add a short explanation before those lines.\n")


def _checks(p: dict) -> list[dict]:
    out = []
    for tr in p["turns"]:
        for ss, w, a in tr["q"]:
            kind = "new" if ss == tr["setting"] else "old"
            out.append({"id": f"t{tr['t']}-{ss}-{w}", "fact": f"t{tr['t']}-{w}", "turns": [tr["t"]], "scope": "all",
                        "type": "word_answer", "word": w, "answer": a, "group": kind, "weight": 1.0,
                        "ftype": "fact"})
    return out


_LINE = re.compile(r"^[\s>*_`#•\-\"'“]*(?:\d{1,2}[.)]\s+)?[\s*_`\"'“]*([A-Za-z]+)[\s*_`\"'”]*"
                   r"(?:[(\[]?\s*(?:on|for|at)?\s*setting\s+[A-Ha-h]\s*[)\]]?[\s*_`]*)?"
                   r"\s*(?:->|=>|→|:|=)\s*[\s*_`\"'“]*([A-Za-z]+)")


def parse_answers(text: str) -> dict:
    got = {}
    for raw in (text or "").splitlines():
        m = _LINE.match(raw.strip())
        if m:
            got[m.group(1).upper()] = m.group(2).upper()
    return got


def check_word(turn_texts, chk) -> tuple[bool, str]:
    _, text = turn_texts[0]
    got = parse_answers(text).get(chk["word"])
    return got == chk["answer"], f"{chk['word']}: {got} vs {chk['answer']}"


@functools.lru_cache(maxsize=64)
def _plan_cached(level: int, key, release_seed: str, case_id: str) -> dict:
    L = LEVELS[level]
    seed = _gen.instance_seed(FAMILY, release_seed, case_id, level, key)
    g0 = rng(seed, "pool")
    sp = _Space(g0.sample(WORDS, 48))
    for attempt in range(60):
        g = rng(seed, f"plan{attempt}")
        try:
            p = _attempt(g, L, sp)
        except (_Fail, IndexError, ValueError):
            continue
        p.update(level=level, seed=seed, attempt=attempt)
        p["user"] = g.choice(USERS)
        p["gadget"] = g.choice(GADGETS)
        p["system"] = _system(p["user"], p["gadget"])
        p["turns_text"] = [_turn_text(tr, p["gadget"]) for tr in p["turns"]]
        p["checks"] = _checks(p)
        p["reference"] = ["\n".join(f"{w} -> {a}" for _, w, a in tr["q"]) for tr in p["turns"]]
        return p
    raise RuntimeError(f"cipherchat: no valid draw for {case_id} L{level}")


def _plan(gen: dict, release_seed: str, case_id: str) -> dict:
    _gen.check_variant(sys.modules[__name__], gen)
    return copy.deepcopy(_plan_cached(int(gen.get("level", 1)), gen.get("key"), release_seed, case_id))


def grade_replies(checks: list[dict], replies: list[str]) -> float:
    from ._stack_common import grade_weighted
    return grade_weighted(checks, replies)


def materialize(gen: dict, *, release_seed: str, case_id: str) -> dict:
    p = _plan(gen, release_seed, case_id)
    return {
        "id": case_id, "category": "rpdeep", "system": p["system"], "history": [], "turns": p["turns_text"],
        "temperature": 0.7, "top_p": 0.95, "max_tokens": MAX_TOKENS, "user_name": p["user"],
        "checks": p["checks"], "judge_elements": [], "programmatic": True, "generator": dict(gen),
        "meta": {"family": FAMILY, "level": p["level"], "n_turns": len(p["turns_text"]), "n_checks": len(p["checks"]),
                 "hidden": {k: pipe_text(v) for k, v in p["hid"].items()},
                 "n_consistent": p["turns"][-1]["n_consistent"],
                 "prompt_tokens_est": round((len(p["system"]) + sum(len(x) for x in p["turns_text"])) / 4)},
    }


def reference(gen: dict, *, release_seed: str, case_id: str) -> str:
    return join_turns(_plan(gen, release_seed, case_id)["reference"])


def grade(answer: str, materialized: dict) -> float:
    return grade_replies(materialized["checks"], split_turns(answer))


# ------------------------------------------------------------ floors
def _fit(ev, cands):
    best, bp = -1, None
    for pp in cands:
        sc = sum(run(pp, a_) == b_ for a_, b_ in ev)
        if sc > best:
            best, bp = sc, pp
            if sc == len(ev):
                break
    return bp


def floor_replies(p: dict, kind: str) -> list[str]:
    """echo | nearest-copy | best-one-step | pooled (one sequence fitted to every setting's readings) |
    latest-setting (answers every word with the newest setting's sequence)."""
    out = []
    ev: dict = {}
    for tr in p["turns"]:
        if tr["setting"]:
            ev[tr["setting"]] = list(tr["new"])
            latest = tr["setting"]
        lines = []
        for ss, w, _ in tr["q"]:
            if kind == "echo":
                a = w
            elif kind == "nearest-copy":
                a = max(ev[ss], key=lambda r: sum(x == y for x, y in zip(r[0], w)))[1]
            elif kind == "best-one-step":
                a = run(_fit(ev[ss], _PIPES(1)), w)
            elif kind == "pooled":
                a = run(_fit([r for v in ev.values() for r in v], _PIPES(3)), w)
            elif kind == "latest-setting":
                a = run(_fit(ev[latest], _PIPES(3)), w)
            else:
                raise ValueError(kind)
            lines.append(f"{w} -> {a}")
        out.append("\n".join(lines))
    return out


FLOOR_KINDS = ("echo", "nearest-copy", "best-one-step", "pooled", "latest-setting")


def mutants(gen: dict, *, release_seed: str, case_id: str) -> list[str]:
    mat = materialize(gen, release_seed=release_seed, case_id=case_id)
    p = _plan(gen, release_seed, case_id)
    out = [join_turns(floor_replies(p, k)) for k in FLOOR_KINDS]
    out.append(join_turns(["I am not sure."] * len(mat["turns"])))
    return [a for a in dict.fromkeys(out) if grade(a, mat) < 1.0]


def _main(argv: list[str] | None = None) -> None:
    import json
    argv = sys.argv[1:] if argv is None else argv
    level = int(argv[0]) if argv else 3
    g = {"type": FAMILY, "level": level, "key": argv[1] if len(argv) > 1 else None}
    m = materialize(g, release_seed="4.5.0", case_id="EN-demo")
    print("SYSTEM:\n" + m["system"])
    for i, t in enumerate(m["turns"], 1):
        print(f"\n--- USER TURN {i} ---\n{t}")
    ref = reference(g, release_seed="4.5.0", case_id="EN-demo")
    print("\n--- REFERENCE ---\n" + ref.replace(TURN_SEP, "\n-----\n"))
    print("\n# meta:", json.dumps(m["meta"]))
    print("# grade(reference) =", grade(ref, m))


if __name__ == "__main__":
    _main()
