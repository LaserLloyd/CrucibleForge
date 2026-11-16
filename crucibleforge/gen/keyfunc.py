"""keyfunc — seeded "nonsense function" generator for MakoBench 4.5 (prototype).

A 64-bit key + a level deterministically produce:
  * a fully specified English spec of a long, arbitrary function
        f(xs: list[int], t: str) -> tuple[int, str]
    built from numbered steps (integer / list / string transforms, conditionals,
    bounded repeats, step counters, edge clauses);
  * a hidden reference implementation (the rule list + a tiny interpreter,
    rendered as standalone Python);
  * a grouped hidden test set (examples, random, edge, every branch both ways)
    in the CrucibleForge `python_exec` + `test_groups` shape;
  * mutants (one rule dropped / one constant changed / branches swapped) that
    must fail the tests.

Stdlib only. Same (key, level) -> byte-identical spec, reference and tests.

Level table (30 seeds per level; spec tokens = chars/4, asserted in tests):
  level  steps  ifs  repeats  counters  test groups  spec tokens
    1      4     0     0        0          8            460-550
    2      8     2     0        0         11-12         560-725
    3     12     2     1        1         12-14         770-940
    4     18     3     2        2         16-18         955-1230
    5     26     4     3        3         19-22        1310-1620
Levels above 5 are one LEVELS line, but the observability pass needs re-tuning
there (a level-6 trial left a rarely-taken step), so only 1-5 ship.
Grading goes through the engine's sandboxed python_exec grader (bwrap).
"""
from __future__ import annotations

import copy
import hashlib
import inspect
import json
import random
import string
import sys

#: gen/_det.py (the shared deterministic launcher, 2026-11-14): its digest, so a launcher change moves this family's revision
_DET_SHA = "77fb46314ef63360"

MASK32 = 0xFFFFFFFF

# ----------------------------------------------------------------- interpreter
# Everything between the two BEGIN/END markers is copied verbatim into the
# reference solution (inspect.getsource), so it must stay self-contained.
# BEGIN-INTERP


def _rotl(v, r):
    v &= 0xFFFFFFFF
    return ((v << r) | (v >> (32 - r))) & 0xFFFFFFFF


def _cond(c, st):
    acc, xs, t = st["acc"], st["xs"], st["t"]
    k = c["kind"]
    if k == "acc_odd":
        return acc % 2 == 1
    if k == "acc_mod":
        return acc % c["m"] == c["r"]
    if k == "len_xs_gt":
        return len(xs) > c["n"]
    if k == "t_contains":
        return c["ch"] in t
    if k == "xs_nonempty":
        return len(xs) > 0
    if k == "xs_sum_mod":
        return sum(xs) % c["m"] == c["r"]
    if k == "t_len_odd":
        return len(t) % 2 == 1
    raise ValueError(k)


def _prim(r, st, counters):
    acc, xs, t = st["acc"], st["xs"], st["t"]
    k = r["kind"]
    if k == "add_k":
        acc = acc + r["k"]
    elif k == "mul_k":
        acc = acc * r["k"]
    elif k == "xor_k":
        acc = acc ^ r["k"]
    elif k == "rotl":
        acc = _rotl(acc, r["r"])
    elif k == "add_sum_even":
        acc = acc + sum(xs[i] for i in range(0, len(xs), 2))
    elif k == "fold_xs":
        for x in xs:
            acc = (acc * r["k"] + x) % 2**32
    elif k == "xor_len_t_k":
        acc = acc ^ (len(t) * r["k"])
    elif k == "add_ord_weighted":
        acc = acc + sum(ord(ch) * (i + 1) for i, ch in enumerate(t))
    elif k == "rev_low8":
        low = acc & 0xFF
        rev = int(f"{low:08b}"[::-1], 2)
        acc = (acc & ~0xFF) | rev
    elif k == "mod_p":
        acc = acc % r["p"]
    elif k == "counter":
        acc = acc + r["k"] * counters.get(r["step"], 0)
    elif k == "map_mul_mod":
        xs = [(x * r["k"]) % r["m"] for x in xs]
    elif k == "reverse_xs":
        xs = xs[::-1]
    elif k == "rotate_xs":
        if len(xs) >= 2:
            s = r["k"] % len(xs)
            xs = xs[s:] + xs[:s]
    elif k == "drop_every":
        xs = [x for i, x in enumerate(xs, 1) if i % r["k"] != 0]
    elif k == "keep_mod":
        xs = [x for x in xs if x % r["k"] == r["r"]]
    elif k == "prefix_sums_mod":
        out, run = [], 0
        for x in xs:
            run += x
            out.append(run % r["m"])
        xs = out
    elif k == "sort_by_mod":
        xs = sorted(xs, key=lambda x: (x % r["k"], x))
    elif k == "swap_pairs":
        xs = list(xs)
        for i in range(0, len(xs) - 1, 2):
            xs[i], xs[i + 1] = xs[i + 1], xs[i]
    elif k == "append_acc256":
        xs = xs + [acc % 256]
    elif k == "xor_prev":
        xs = [xs[0]] + [xs[i] ^ xs[i - 1] for i in range(1, len(xs))] if xs else []
    elif k == "rotate_t":
        if len(t) >= 2:
            s = r["k"] % len(t)
            t = t[s:] + t[:s]
    elif k == "reverse_t":
        t = t[::-1]
    elif k == "upper_every":
        t = "".join(ch.upper() if i % r["k"] == r["r"] else ch for i, ch in enumerate(t))
    elif k == "caesar":
        out = []
        for ch in t:
            if "a" <= ch <= "z":
                out.append(chr((ord(ch) - 97 + r["n"]) % 26 + 97))
            elif "A" <= ch <= "Z":
                out.append(chr((ord(ch) - 65 + r["n"]) % 26 + 65))
            else:
                out.append(ch)
        t = "".join(out)
    elif k == "remove_mod":
        t = "".join(ch for ch in t if ord(ch) % r["k"] != r["r"])
    elif k == "insert_acc":
        p = min(r["p"], len(t))
        t = t[:p] + str(acc % 100) + t[p:]
    elif k == "digit_shift":
        t = "".join(str((int(ch) + r["k"]) % 10) if ch.isdigit() else ch for ch in t)
    elif k == "swapcase_at_xs":
        if t:
            tl = list(t)
            for x in xs:
                i = x % len(tl)
                tl[i] = tl[i].swapcase()
            t = "".join(tl)
    elif k == "acc_xor_t_at":
        if t:
            acc = acc ^ ord(t[acc % len(t)])
    elif k == "xs_append_len_t":
        xs = xs + [len(t)]
    elif k == "t_append_xs_at":
        if xs:
            t = t + str(xs[acc % len(xs)])
    elif k == "noop":
        pass
    else:
        raise ValueError(k)
    st["acc"], st["xs"], st["t"] = acc % 2**32, xs, t


def _run_rules(rules, st, counters, trace):
    for r in rules:
        if r["kind"] == "if":
            v = _cond(r["cond"], st)
            if trace is not None:
                trace.append((r["id"], v))
            if v:
                _run_rules([r["then"]], st, counters, trace)
            elif r.get("else"):
                _run_rules([r["else"]], st, counters, trace)
        elif r["kind"] == "repeat":
            n = 0
            while n < r["max"] and _cond(r["cond"], st):
                _run_rules(r["body"], st, counters, trace)
                n += 1
            if trace is not None:
                trace.append((r["id"], n))
        else:
            counters[r["id"]] = counters.get(r["id"], 0) + 1
            _prim(r, st, counters)


def run(rules, xs, t, acc0, trace=None):
    st = {"acc": acc0 % 2**32, "xs": list(xs), "t": t}
    counters = {}
    _run_rules(rules, st, counters, trace)
    st["acc"] = (st["acc"] + sum((i + 1) * x for i, x in enumerate(st["xs"]))) % 2**32
    return st["acc"], st["t"]
# END-INTERP


# ----------------------------------------------------------------- rendering
def _ren_cond(c):
    k = c["kind"]
    return {
        "acc_odd": "acc is odd",
        "acc_mod": f"acc mod {c.get('m')} == {c.get('r')}",
        "len_xs_gt": f"len(xs) > {c.get('n')}",
        "t_contains": f"t contains the character '{c.get('ch')}'",
        "xs_nonempty": "xs is not empty",
        "xs_sum_mod": f"(the sum of xs) mod {c.get('m')} == {c.get('r')}",
        "t_len_odd": "len(t) is odd",
    }[k]


def _ren_prim(r):
    if r.get("hidden"):
        return "(Not described in words: see HIDDEN STEPS below.)"
    k = r["kind"]
    if k == "add_k":
        return f"Set acc = acc + {r['k']}."
    if k == "mul_k":
        return f"Set acc = acc * {r['k']}."
    if k == "xor_k":
        return f"Set acc = acc XOR {r['k']}."
    if k == "rotl":
        return f"Set acc = rotl(acc, {r['r']})."
    if k == "add_sum_even":
        return "Set acc = acc + S, where S is the sum of xs[i] over every EVEN index i (0, 2, 4, ...); S = 0 if xs is empty."
    if k == "fold_xs":
        return f"For each x in xs, in order: set acc = (acc * {r['k']} + x) mod 2**32. (Nothing happens if xs is empty.)"
    if k == "xor_len_t_k":
        return f"Set acc = acc XOR (len(t) * {r['k']})."
    if k == "add_ord_weighted":
        return "Set acc = acc + the sum of ord(t[i]) * (i + 1) over every index i of t (0 if t is empty)."
    if k == "rev_low8":
        return "Replace the lowest 8 bits of acc by those same 8 bits in reverse order (bit 0 becomes bit 7, bit 1 becomes bit 6, and so on); all other bits of acc are unchanged."
    if k == "mod_p":
        return f"Set acc = acc mod {r['p']}."
    if k == "counter":
        return (f"Set acc = acc + {r['k']} * n, where n is the number of times step {r['step']} has been "
                f"applied so far (count every application, including inside repeats; n = 0 if it was never applied).")
    if k == "map_mul_mod":
        return f"Replace every element x of xs by (x * {r['k']}) mod {r['m']}."
    if k == "reverse_xs":
        return "Reverse the order of xs."
    if k == "rotate_xs":
        return f"If xs has at least 2 elements, rotate xs LEFT by ({r['k']} mod len(xs)) positions (those first elements move to the end, in order). Otherwise xs is unchanged."
    if k == "drop_every":
        return f"Remove every element of xs whose 1-based position (in the list as it is before this step) is a multiple of {r['k']} (positions {r['k']}, {2*r['k']}, {3*r['k']}, ...)."
    if k == "keep_mod":
        return f"Keep only the elements x of xs with x mod {r['k']} == {r['r']}, in their original order."
    if k == "prefix_sums_mod":
        return f"Replace xs by its running sums, each taken mod {r['m']}: the new xs[i] = (old xs[0] + old xs[1] + ... + old xs[i]) mod {r['m']}."
    if k == "sort_by_mod":
        return f"Sort xs in ascending order of the pair (x mod {r['k']}, x): first by x mod {r['k']}, ties by x."
    if k == "swap_pairs":
        return "Swap xs[0] with xs[1], xs[2] with xs[3], and so on; a final unpaired element stays where it is."
    if k == "append_acc256":
        return "Append (acc mod 256) to the end of xs."
    if k == "xor_prev":
        return "For i = 1, 2, ..., len(xs)-1 set xs[i] = (old xs[i]) XOR (old xs[i-1]), using the values xs had BEFORE this step; xs[0] is unchanged."
    if k == "rotate_t":
        return f"If len(t) >= 2, rotate t LEFT by ({r['k']} mod len(t)) characters (those first characters move to the end, in order). Otherwise t is unchanged."
    if k == "reverse_t":
        return "Reverse t."
    if k == "upper_every":
        return f"Uppercase every character t[i] whose index satisfies i mod {r['k']} == {r['r']} (characters that are not lowercase letters are unchanged)."
    if k == "caesar":
        return f"Shift every letter of t forward by {r['n']} places in the alphabet, wrapping around (z -> a), keeping its case; non-letters are unchanged."
    if k == "remove_mod":
        return f"Remove every character c of t with ord(c) mod {r['k']} == {r['r']}."
    if k == "insert_acc":
        return f"Insert the decimal digits of (acc mod 100), with no padding, into t at index p = min({r['p']}, len(t)) (so the new t is t[:p] + digits + t[p:])."
    if k == "digit_shift":
        return f"Replace every digit character d of t by the digit (d + {r['k']}) mod 10."
    if k == "swapcase_at_xs":
        return "If t is not empty: for each x in xs, in order, swap the case of the character t[x mod len(t)] (swapping twice restores it; non-letters are unchanged)."
    if k == "acc_xor_t_at":
        return "If t is not empty, set acc = acc XOR ord(t[acc mod len(t)]). Otherwise do nothing."
    if k == "xs_append_len_t":
        return "Append len(t) to the end of xs."
    if k == "t_append_xs_at":
        return "If xs is not empty, append the decimal digits of xs[acc mod len(xs)] to the end of t. Otherwise do nothing."
    raise ValueError(k)


def _render_steps(rules, lines, indent=""):
    for r in rules:
        sid = r["id"]
        if r["kind"] == "if":
            lines.append(f"{indent}Step {sid}. If {_ren_cond(r['cond'])}, do step {sid}a; otherwise "
                         + (f"do step {sid}b." if r.get("else") else "do nothing."))
            lines.append(f"{indent}  Step {sid}a. {_ren_prim(r['then'])}")
            if r.get("else"):
                lines.append(f"{indent}  Step {sid}b. {_ren_prim(r['else'])}")
        elif r["kind"] == "repeat":
            ids = ", ".join(b["id"] for b in r["body"])
            lines.append(f"{indent}Step {sid}. Repeat the sub-steps {ids} (in order) while {_ren_cond(r['cond'])}, "
                         f"at most {r['max']} times in total. Check the condition before each repetition; "
                         f"if it is false at the start, the sub-steps are not applied at all.")
            _render_steps(r["body"], lines, indent + "  ")
        else:
            lines.append(f"{indent}Step {sid}. {_ren_prim(r)}")


PREAMBLE = """Implement the Python function `def f(xs, t):` exactly as specified below. It takes a list of non-negative integers `xs` and an ASCII string `t`, and returns a tuple `(acc, t)` where `acc` is an integer and `t` is a string.

Definitions and conventions (all of them matter):
- Integers are unbounded Python integers. `a mod b` is the non-negative remainder (Python's `a % b`). `XOR` is bitwise exclusive-or on non-negative integers.
- `acc` is always kept in the range 0 .. 2**32-1: after EVERY step that assigns acc, set acc = acc mod 2**32.
- `rotl(v, r)` = ((v << r) | (v >> (32 - r))) mod 2**32, for 0 <= v < 2**32 and 0 < r < 32.
- Indices are 0-based. `len(x)` is the length. `ord(c)` is the ASCII code of a one-character string. "Digits" of an integer means its usual decimal representation without a sign or padding.
- The state is (acc, xs, t). Steps are applied strictly in numbered order, each to the state left by the previous step. A step that says "do nothing" leaves the state unchanged. Steps never raise exceptions on valid input.
- Initial state: acc = {acc0}, xs = a copy of the input list (the input list itself must not be modified), t = the input string.
- After the last step, set acc = (acc + the sum over every index i of (i + 1) * xs[i]) mod 2**32 (0 if xs is empty) and return (acc, t).

Steps:
"""

TAIL = """
Return only the function `f` (plus any helpers you need) inside a single ```python code block. No explanation."""


# ------------------------------------------------------------------ generator
_PRIMS_INT = ["add_k", "mul_k", "xor_k", "rotl", "add_sum_even", "fold_xs", "xor_len_t_k",
              "add_ord_weighted", "rev_low8", "mod_p"]
_PRIMS_LIST = ["map_mul_mod", "reverse_xs", "rotate_xs", "drop_every", "keep_mod", "prefix_sums_mod",
               "sort_by_mod", "swap_pairs", "append_acc256", "xor_prev"]
_PRIMS_STR = ["rotate_t", "reverse_t", "upper_every", "caesar", "remove_mod", "insert_acc",
              "digit_shift", "swapcase_at_xs"]
_PRIMS_CROSS = ["acc_xor_t_at", "xs_append_len_t", "t_append_xs_at"]
_ONCE = {"keep_mod", "remove_mod", "mod_p", "drop_every"}   # at most once per spec

LEVELS = {
    #        prims  ifs  repeats  counters
    1: dict(prims=4, ifs=0, reps=0, ctrs=0),
    2: dict(prims=6, ifs=2, reps=0, ctrs=0),
    3: dict(prims=8, ifs=2, reps=1, ctrs=1),
    4: dict(prims=11, ifs=3, reps=2, ctrs=2),
    5: dict(prims=16, ifs=4, reps=3, ctrs=3),
    # 4.5 hidden-arm levels: `hidden` if-arms are shown only as `snaps` before/after snapshots.
    # Round-2 knob (after P1 round 1: Haiku already 1.00 at 6/5/4 snapshots): 3/3/2 snapshots, int catalogue throughout.
    6: dict(prims=11, ifs=3, reps=2, ctrs=2, hidden=1, snaps=3, catalogue="int"),
    7: dict(prims=11, ifs=4, reps=2, ctrs=2, hidden=2, snaps=3, catalogue="int"),
    8: dict(prims=16, ifs=5, reps=3, ctrs=3, hidden=2, snaps=2, catalogue="int"),
}

# median reference-answer tokens (chars/4, 30 seeds; tests/test_gen_answer_tokens.py re-measures)
_ANSWER_TOKENS = {1: 1355, 2: 1453, 3: 1538, 4: 1675, 5: 1818, 6: 1675, 7: 1700, 8: 1850}
for _lv, _row in LEVELS.items():
    _row["answer_tokens"] = _ANSWER_TOKENS[_lv]



def _rng(key: int, level: int, salt: str = "") -> random.Random:
    h = hashlib.sha256(f"keyfunc:{key:016x}:{level}:{salt}".encode()).digest()
    return random.Random(int.from_bytes(h[:8], "big"))


def _make_prim(rng, kind):
    r = {"kind": kind}
    if kind in ("add_k", "xor_k"):
        r["k"] = rng.randrange(3, 100000)
    elif kind == "mul_k":
        r["k"] = rng.choice([3, 5, 7, 11, 13, 17, 31, 37, 101, 257])
    elif kind == "rotl":
        r["r"] = rng.randrange(1, 32)
    elif kind == "fold_xs":
        r["k"] = rng.choice([3, 5, 7, 31, 33])
    elif kind == "xor_len_t_k":
        r["k"] = rng.randrange(2, 500)
    elif kind == "mod_p":
        r["p"] = rng.choice([65521, 4093, 1000003, 9973, 255, 1024])
    elif kind == "map_mul_mod":
        r["k"] = rng.randrange(2, 50)
        r["m"] = rng.choice([97, 101, 256, 1000, 61])
    elif kind in ("rotate_xs", "rotate_t"):
        r["k"] = rng.randrange(1, 9)
    elif kind == "drop_every":
        r["k"] = rng.randrange(2, 5)
    elif kind == "keep_mod":
        r["k"] = rng.randrange(2, 5)
        r["r"] = rng.randrange(0, r["k"])
    elif kind == "prefix_sums_mod":
        r["m"] = rng.choice([100, 256, 1000, 97])
    elif kind == "sort_by_mod":
        r["k"] = rng.randrange(2, 7)
    elif kind == "upper_every":
        r["k"] = rng.randrange(2, 5)
        r["r"] = rng.randrange(0, r["k"])
    elif kind == "caesar":
        r["n"] = rng.randrange(1, 26)
    elif kind == "remove_mod":
        r["k"] = rng.randrange(5, 12)
        r["r"] = rng.randrange(0, r["k"])
    elif kind == "insert_acc":
        r["p"] = rng.randrange(0, 8)
    elif kind == "digit_shift":
        r["k"] = rng.randrange(1, 10)
    return r


def _make_cond(rng):
    kind = rng.choice(["acc_odd", "acc_mod", "len_xs_gt", "t_contains", "xs_nonempty", "xs_sum_mod", "t_len_odd"])
    c = {"kind": kind}
    if kind == "acc_mod" or kind == "xs_sum_mod":
        c["m"] = rng.randrange(2, 6)
        c["r"] = rng.randrange(0, c["m"])
    elif kind == "len_xs_gt":
        c["n"] = rng.randrange(1, 5)
    elif kind == "t_contains":
        c["ch"] = rng.choice("aeiost 1")
    return c


def _pick_prim(rng, used_once):
    pool = _PRIMS_INT * 3 + _PRIMS_LIST * 2 + _PRIMS_STR * 2 + _PRIMS_CROSS
    while True:
        k = rng.choice(pool)
        if k in _ONCE:
            if k in used_once:
                continue
            used_once.add(k)
        return _make_prim(rng, k)


def make_rules(key: int, level: int, attempt: int = 0) -> tuple[list[dict], int]:
    cfg = LEVELS[level]
    rng = _rng(key, level, "rules" + (f"#{attempt}" if attempt else ""))
    acc0 = rng.randrange(1, 2**32)
    used_once: set[str] = set()
    # the sequence of top-level step kinds, shuffled but with repeats not first
    kinds = ["p"] * cfg["prims"] + ["i"] * cfg["ifs"] + ["r"] * cfg["reps"] + ["c"] * cfg["ctrs"]
    rng.shuffle(kinds)
    # counters must come after something; repeats not at position 0
    kinds.sort(key=lambda k: 0 if k == "p" else 1) if level == 1 else None
    if kinds and kinds[0] in ("r", "c"):
        j = kinds.index("p")
        kinds[0], kinds[j] = kinds[j], kinds[0]
    rules: list[dict] = []
    prim_ids: list[str] = []         # ids of primitive steps, for counters
    in_repeat_ids: list[str] = []
    n = 0
    for kd in kinds:
        n += 1
        sid = str(n)
        if kd == "p":
            r = _pick_prim(rng, used_once)
            r["id"] = sid
            rules.append(r)
            prim_ids.append(sid)
        elif kd == "i":
            then = _pick_prim(rng, used_once)
            then["id"] = sid + "a"
            els = None
            if rng.random() < 0.6:
                els = _pick_prim(rng, used_once)
                els["id"] = sid + "b"
            rules.append({"kind": "if", "id": sid, "cond": _make_cond(rng), "then": then, "else": els})
            prim_ids.append(sid + "a")
            if els:
                prim_ids.append(sid + "b")
        elif kd == "r":
            body = []
            for j in range(rng.randrange(2, 4)):
                b = _pick_prim(rng, used_once)
                b["id"] = sid + "abc"[j]
                body.append(b)
                in_repeat_ids.append(b["id"])
                prim_ids.append(b["id"])
            cond = {"kind": "acc_mod", "m": rng.randrange(3, 8), "r": 0}
            cond["r"] = rng.randrange(0, cond["m"])
            # "while acc mod m != r" is the natural loop guard; express as a cond kind
            rules.append({"kind": "repeat", "id": sid, "cond": {"kind": "not_acc_mod", **cond},
                          "body": body, "max": rng.randrange(2, 6)})
        elif kd == "c":
            target = rng.choice(in_repeat_ids) if in_repeat_ids else rng.choice(prim_ids)
            rules.append({"kind": "counter", "id": sid, "k": rng.randrange(2, 1000), "step": target})
            prim_ids.append(sid)
    if cfg.get("hidden"):
        _hide_arms(rules, rng, used_once, level)
        _tune_hidden_conds(rules, acc0, rng)
    _ensure_observable(rules, acc0, rng, used_once, level)
    return rules, acc0



def _walk_prims(rules):
    """(owner, container, key_or_index, prim) for every primitive step, nested ones included;
    owner is the enclosing if/repeat rule or None."""
    for i, r in enumerate(rules):
        if r["kind"] == "if":
            yield (r, r, "then", r["then"])
            if r.get("else"):
                yield (r, r, "else", r["else"])
        elif r["kind"] == "repeat":
            for j, b in enumerate(r["body"]):
                yield (r, r["body"], j, b)
        else:
            yield (None, rules, i, r)


def _ensure_observable(rules, acc0, rng, used_once, level=None):
    """Every primitive step must change at least one probe output when replaced
    by a no-op; a dead step is re-picked (so no spec carries an unobservable rule
    and every drop-one-step mutant is caught)."""
    probe_rng = random.Random(rng.randrange(2**62))
    probes = [_rand_input(probe_rng, big=i % 3 == 0) for i in range(48)] + [([], ""), ([5], "a"), ([0, 0, 0], "")]
    import copy

    def outputs(rs):
        return [run(rs, xs, t, acc0) for xs, t in probes]

    for _round in range(60):
        base = outputs(rules)
        dead = None
        for owner, cont, key, prim in _walk_prims(rules):
            saved = copy.deepcopy(prim)
            cont[key] = {"kind": "noop", "id": prim["id"]}
            changed = outputs(rules) != base
            cont[key] = saved
            if not changed:
                dead = (owner, cont, key, prim)
                break
        if dead is None:
            return
        owner, cont, key, prim = dead
        # a branch that is never (or always) taken: change the guard half the time
        if owner is not None and rng.random() < 0.5:
            if owner["kind"] == "if":
                owner["cond"] = _make_cond(rng)
            else:
                m = rng.randrange(3, 8)
                owner["cond"] = {"kind": "not_acc_mod", "m": m, "r": rng.randrange(0, m)}
                owner["max"] = rng.randrange(2, 6)
            continue
        if prim.get("hidden"):
            new = _pick_hidden(rng, used_once, level)
        else:
            new = _pick_prim(rng, used_once)
        new["id"] = prim["id"]
        cont[key] = new
    raise RuntimeError(f"could not make every step observable: {dead[2]}")


# the repeat guard is "acc mod m != r": register it in the interpreter/renderer
def _cond_not_acc_mod(c, st):
    return st["acc"] % c["m"] != c["r"]


_old_cond = _cond


def _cond(c, st):  # noqa: F811 — extends the interpreter's condition set
    if c["kind"] == "not_acc_mod":
        return _cond_not_acc_mod(c, st)
    return _old_cond(c, st)


_old_ren_cond = _ren_cond


def _ren_cond(c):  # noqa: F811
    if c["kind"] == "not_acc_mod":
        return f"acc mod {c['m']} != {c['r']}"
    return _old_ren_cond(c)


# ------------------------------------------------------------------ inputs
_ALPHABET = string.ascii_lowercase * 3 + string.ascii_uppercase + string.digits + " .,!-_"


def _rand_input(rng, big=False):
    if big:
        n = rng.randrange(8, 21)
        m = rng.randrange(10, 31)
    else:
        n = rng.randrange(0, 11)
        m = rng.randrange(0, 15)
    xs = [rng.randrange(0, 1000) for _ in range(n)]
    t = "".join(rng.choice(_ALPHABET) for _ in range(m))
    return xs, t


def _assert_line(xs, t, out):
    return f"assert f({xs!r}, {t!r}) == {out!r}"


def _branch_ids(rules):
    out = []
    for r in rules:
        if r["kind"] in ("if", "repeat"):
            out.append((r["id"], r["kind"]))
    return out


def make_tests(key: int, level: int, rules, acc0) -> dict:
    rng = _rng(key, level, "tests")
    groups: dict[str, list[str]] = {}

    def add(group, xs, t):
        out = run(rules, xs, t, acc0)
        groups.setdefault(group, []).append(_assert_line(xs, t, out))

    # shown examples (also a group)
    examples = [_rand_input(rng) for _ in range(2)]
    for xs, t in examples:
        add("examples", xs, t)
    for _ in range(10):
        add("random-small", *_rand_input(rng))
    for _ in range(8):
        add("random-large", *_rand_input(rng, big=True))
    add("edge-empty-both", [], "")
    add("edge-empty-xs", [], "".join(rng.choice(_ALPHABET) for _ in range(9)))
    add("edge-empty-t", [rng.randrange(0, 1000) for _ in range(6)], "")
    add("edge-zeros", [0] * rng.randrange(1, 7), "0000")
    add("edge-single", [rng.randrange(0, 1000)], rng.choice(string.ascii_letters))
    # every branch both ways, every repeat with 0 and >=1 iterations
    for bid, kind in _branch_ids(rules):
        for want in (True, False):
            found = 0
            for _ in range(600):
                xs, t = _rand_input(rng, big=rng.random() < 0.3)
                tr: list = []
                run(rules, xs, t, acc0, trace=tr)
                hits = [v for (i, v) in tr if i == bid]
                if not hits:
                    continue
                ok = (any(hits) if kind == "if" else any(h > 0 for h in hits)) if want else \
                     (not all(hits) if kind == "if" else any(h == 0 for h in hits))
                if ok:
                    gname = f"{'if' if kind == 'if' else 'repeat'}-{bid}-{'taken' if want else 'skipped'}"
                    add(gname, xs, t)
                    found += 1
                    if found >= 3:
                        break
    return {"examples": examples, "groups": {g: "\n".join(v) for g, v in groups.items()}}


# ------------------------------------------------------------------ hidden arms (levels 6-8)
# S8-DESIGN section 1: one or more if-arms are not described in words. The spec shows N snapshots of the
# state before/after the arm ran, and the arm is one primitive of a small catalogue. The condition of the
# `if` stays visible, so a faithful transcription of everything else scores the "transcription floor"
# (the visible test groups) and each correctly inferred arm adds its own groups.
_CAT_LIST_STR = ["reverse_xs", "rotate_xs", "swap_pairs", "drop_every", "sort_by_mod", "keep_mod",
                 "map_mul_mod", "prefix_sums_mod", "xor_prev", "reverse_t", "rotate_t", "caesar",
                 "upper_every", "digit_shift", "remove_mod"]
_CAT_INT = ["add_k", "xor_k", "mul_k"]
_MAP_M = [97, 101, 256, 1000, 61]
_PREFIX_M = [100, 256, 1000, 97]
_MUL_K = [3, 5, 7, 11, 13, 17, 31, 37, 101, 257]
_PARAM_DOC = {
    "rotate_xs": "<k> is 1..8", "drop_every": "<k> is 2..4", "sort_by_mod": "<k> is 2..6",
    "keep_mod": "<k> is 2..4, <r> is 0..<k>-1", "map_mul_mod": "<k> is 2..49, <m> is one of 61, 97, 101, 256, 1000",
    "prefix_sums_mod": "<m> is one of 97, 100, 256, 1000", "rotate_t": "<k> is 1..8", "caesar": "<n> is 1..25",
    "upper_every": "<k> is 2..4, <r> is 0..<k>-1", "digit_shift": "<k> is 1..9",
    "remove_mod": "<k> is 5..11, <r> is 0..<k>-1", "add_k": "<k> is 3..99999", "xor_k": "<k> is 3..99999",
    "mul_k": "<k> is one of 3, 5, 7, 11, 13, 17, 31, 37, 101, 257",
}
_HIDE_FRAC = (0.37, 0.63)       # asserted on 600 probes; the documented band is 35-65% of random inputs


def _cat_kinds(level):
    return _CAT_LIST_STR + (_CAT_INT if LEVELS[level].get("catalogue") == "int" else [])


def _space(kind):
    """Every parameter choice of a catalogue kind that the generator can draw (list/str kinds; the int
    kinds are derived from a snapshot, see _universe)."""
    if kind in ("reverse_xs", "swap_pairs", "xor_prev", "reverse_t"):
        return [{"kind": kind}]
    if kind in ("rotate_xs", "rotate_t"):
        return [{"kind": kind, "k": k} for k in range(1, 9)]
    if kind == "drop_every":
        return [{"kind": kind, "k": k} for k in range(2, 5)]
    if kind == "sort_by_mod":
        return [{"kind": kind, "k": k} for k in range(2, 7)]
    if kind in ("keep_mod", "upper_every"):
        return [{"kind": kind, "k": k, "r": r} for k in range(2, 5) for r in range(k)]
    if kind == "map_mul_mod":
        return [{"kind": kind, "k": k, "m": m} for k in range(2, 50) for m in _MAP_M]
    if kind == "prefix_sums_mod":
        return [{"kind": kind, "m": m} for m in _PREFIX_M]
    if kind == "caesar":
        return [{"kind": kind, "n": n} for n in range(1, 26)]
    if kind == "digit_shift":
        return [{"kind": kind, "k": k} for k in range(1, 10)]
    if kind == "remove_mod":
        return [{"kind": kind, "k": k, "r": r} for k in range(5, 12) for r in range(k)]
    raise ValueError(kind)


def _apply(cand, pre):
    """apply one primitive to a (acc, xs, t) state; returns the new (acc, xs, t)"""
    st = {"acc": pre[0], "xs": list(pre[1]), "t": pre[2]}
    _prim(cand, st, {})
    return (st["acc"], tuple(st["xs"]), st["t"])


def _universe(snap, level):
    """catalogue candidates worth testing once one snapshot is known (int kinds are derived from it)"""
    out = [c for kind in _CAT_LIST_STR for c in _space(kind)]
    if LEVELS[level].get("catalogue") == "int":
        (a0, _x0, _t0), (a1, _x1, _t1) = snap
        for k in ((a1 - a0) % 2**32,):
            if 3 <= k < 100000:
                out.append({"kind": "add_k", "k": k})
        k = a0 ^ a1
        if 3 <= k < 100000:
            out.append({"kind": "xor_k", "k": k})
        out += [{"kind": "mul_k", "k": k} for k in _MUL_K]
    return out


def _fits(cand, snap):
    return _apply(cand, snap[0]) == snap[1]


def _capture_rules(rules, st, counters, ids, got):
    """a copy of the interpreter's _run_rules (BEGIN-INTERP) that records (id, pre, post) for the primitive steps in
    ``ids``; tests/test_gen_keyfunc_threads.py checks it against the interpreter on every hidden level."""
    for r in rules:
        if r["kind"] == "if":
            if _cond(r["cond"], st):
                _capture_rules([r["then"]], st, counters, ids, got)
            elif r.get("else"):
                _capture_rules([r["else"]], st, counters, ids, got)
        elif r["kind"] == "repeat":
            n = 0
            while n < r["max"] and _cond(r["cond"], st):
                _capture_rules(r["body"], st, counters, ids, got)
                n += 1
        else:
            counters[r["id"]] = counters.get(r["id"], 0) + 1
            if r["id"] in ids:
                pre = (st["acc"], tuple(st["xs"]), st["t"])
                _prim(r, st, counters)
                got.append((r["id"], pre, (st["acc"], tuple(st["xs"]), st["t"])))
            else:
                _prim(r, st, counters)


def _capture(rules, xs, t, acc0, ids):
    """(id, pre_state, post_state) for every application of the primitive steps in ``ids`` while f runs
    on (xs, t). Thread-safe: it walks the rules itself and never rebinds the module's _prim (the old
    rebinding raised KeyError('id') / could pollute captures when runner threads materialised in parallel)."""
    got = []
    st = {"acc": acc0 % 2**32, "xs": list(xs), "t": t}
    _capture_rules(rules, st, {}, ids, got)
    return got


def _hidden_arms(rules):
    return [r["then"] for r in rules if r["kind"] == "if" and r["then"].get("hidden")]


def _pick_hidden(rng, used_once, level):
    kinds = [k for k in _cat_kinds(level) if k not in used_once or k not in _ONCE]
    k = rng.choice(kinds)
    if k in _ONCE:
        used_once.add(k)
    p = _make_prim(rng, k)
    p["hidden"] = True
    return p


def _hide_arms(rules, rng, used_once, level):
    ifs = [r for r in rules if r["kind"] == "if"]
    for r in rng.sample(ifs, LEVELS[level]["hidden"]):
        new = _pick_hidden(rng, used_once, level)
        new["id"] = r["then"]["id"]
        r["then"] = new


def _tune_hidden_conds(rules, acc0, rng):
    """redraw the guard of every hidden arm's `if` until it fires on 40-60% of random inputs"""
    probe_rng = random.Random(rng.randrange(2**62))
    probes = [_rand_input(probe_rng, big=i % 3 == 0) for i in range(120)]
    for r in rules:
        if r["kind"] == "if" and r["then"].get("hidden"):
            for _ in range(80):
                hits = 0
                for xs, t in probes:
                    tr: list = []
                    run(rules, xs, t, acc0, trace=tr)
                    hits += any(v for i, v in tr if i == r["id"])
                if 0.4 <= hits / len(probes) <= 0.6:
                    break
                r["cond"] = _make_cond(rng)


def _fire_rate(rules, acc0, arm_id, n=600):
    rng = random.Random(0xF1)
    owner = arm_id[:-1]
    hits = 0
    for i in range(n):
        xs, t = _rand_input(rng, big=i % 3 == 0)
        tr: list = []
        run(rules, xs, t, acc0, trace=tr)
        hits += any(v for j, v in tr if j == owner)
    return hits / n


def _fires(rules, acc0, xs, t, hid):
    tr: list = []
    out = run(rules, xs, t, acc0, trace=tr)
    return {a + "a" for a in hid if any(v for i, v in tr if i == a)}, out


class _Retry(Exception):
    pass


_GROUPS = {6: (8, 6, 0), 7: (8, 5, 2), 8: (8, 5, 2)}       # visible, per hidden arm, both-arms groups
_PER_GROUP = {"visible": 2}        # inputs per group (1 elsewhere: one wrong input is one lost group)


def _hidden_tests(key, level, rules, acc0, attempt):
    """Groups relabelled by which hidden arms fire: visible-*, hidden-<id>-*, both-*; two inputs each."""
    import copy
    rng = _rng(key, level, f"tests#{attempt}")
    hid = [r["id"] for r in rules if r["kind"] == "if" and r["then"].get("hidden")]
    noop = copy.deepcopy(rules)
    for r in noop:
        if r["kind"] == "if" and r["then"].get("hidden"):
            r["then"] = {"kind": "noop", "id": r["then"]["id"]}
    pools: dict = {"visible": [], "both": []}
    for h in hid:
        pools[h + "a"] = []
    cand = [([], ""), ([5], "a"), ([0, 0, 0], "0000")] + [_rand_input(rng, big=rng.random() < 0.3) for _ in range(1500)]
    seen = set()
    for xs, t in cand:
        if (tuple(xs), t) in seen:
            continue
        seen.add((tuple(xs), t))
        fired, out = _fires(rules, acc0, xs, t, hid)
        if not fired:
            pools["visible"].append((xs, t))
        else:
            if run(noop, xs, t, acc0) == out:          # hiding the arm changes nothing here: useless as a test
                continue
            if len(fired) == 1:
                pools[next(iter(fired))].append((xs, t))
            else:
                pools["both"].append((xs, t))
    nv, na, nb = _GROUPS[level]
    spec = {"visible": nv}
    spec.update({h + "a": na for h in hid})
    if len(hid) > 1:
        spec["both"] = nb
    groups, used = {}, {}
    for name, ngroups in spec.items():
        pool = pools[name]
        per = _PER_GROUP.get(name, 1)
        if len(pool) < ngroups * per:
            raise _Retry()
        pick = pool[:ngroups * per] if name == "visible" else rng.sample(pool, ngroups * per)
        used[name] = pick
        label = "visible" if name == "visible" else ("both" if name == "both" else f"hidden-{name}")
        for g in range(ngroups):
            groups[f"{label}-{g + 1}"] = pick[g * per:(g + 1) * per]
    # shown examples: visible inputs that are not tests
    taken = {(tuple(x), t) for v in used.values() for x, t in v}
    examples = [(x, t) for x, t in pools["visible"] if (tuple(x), t) not in taken][:2]
    if len(examples) < 2:
        raise _Retry()
    return groups, examples, pools


def _snapshots(key, level, rules, acc0, arm, test_pre, attempt):
    """``snaps`` snapshots of one hidden arm from fresh inputs, chosen greedily to eliminate candidates;
    accepted only when every surviving candidate behaves like the truth on all the test pre-states and on
    300 fresh pre-states."""
    n = LEVELS[level]["snaps"]
    rng = _rng(key, level, f"snaps-{arm['id']}#{attempt}")
    fresh = []
    tries = 0
    ids = {arm["id"]}
    while len(fresh) < 160 and tries < 4000:
        tries += 1
        xs = [rng.randrange(0, 1000) for _ in range(rng.randrange(5, 10))]
        t = "".join(rng.choice(_ALPHABET) for _ in range(rng.randrange(7, 12)))
        for _id, pre, post in _capture(rules, xs, t, acc0, ids)[:1]:
            fresh.append((pre, post))
    if len(fresh) < 30:
        raise _Retry()
    base_u = [c for kind in _CAT_LIST_STR for c in _space(kind)]
    # first snapshot: the one that kills most of the list/str candidates
    first = max(fresh, key=lambda s: -sum(_fits(c, s) for c in base_u))
    uni = _universe(first, level)
    chosen = [first]
    alive = [c for c in uni if _fits(c, first)]
    while len(chosen) < n:
        best = min(fresh, key=lambda s: sum(_fits(c, s) for c in alive))
        if best in chosen:
            rest = [s for s in fresh if s not in chosen]
            best = rest[0] if rest else None
            if best is None:
                break
        chosen.append(best)
        alive = [c for c in alive if _fits(c, best)]
    probes = test_pre + [pre for pre, _ in fresh[:0]]
    rng2 = random.Random(rng.randrange(2**62))
    for _ in range(300):
        st_xs = [rng2.randrange(0, 1000) for _ in range(rng2.randrange(0, 12))]
        st_t = "".join(rng2.choice(_ALPHABET) for _ in range(rng2.randrange(0, 15)))
        probes.append((rng2.randrange(0, 2**32), tuple(st_xs), st_t))
    for c in alive:
        for pre in probes:
            if _apply(c, pre) != _apply(arm, pre):
                raise _Retry()
    return chosen, uni, alive


def _group_share(rules_m, exp, acc0, groups, names=None):
    """(groups passed, groups) of a mutated rule list against the expected outputs ``exp``"""
    ok = 0
    sel = [g for g in groups if names is None or g.startswith(names)]
    for g in sel:
        if all(run(rules_m, xs, t, acc0) == e for (xs, t), e in zip(groups[g], exp[g])):
            ok += 1
    return ok, len(sel)


def _pick_nearmiss(rules, acc0, arm, snaps, uni, pool, groups):
    """The near-miss candidate for one hidden arm: the candidate that fits all but the fewest snapshots
    (never one that fits them all: those behave like the truth, see _snapshots), is caught by at least one
    input of the arm's pool and survives at least one. The arm's groups are then arranged so that one group
    catches it and one does not, so the near-miss mutant scores strictly between the transcription floor and
    1.0 (a test set that never separates the nearest wrong candidates would not measure the inference)."""
    owner = next(r for r in rules if r["kind"] == "if" and r["id"] == arm["id"][:-1])
    label = f"hidden-{arm['id']}"
    scored = sorted((sum(not _fits(c, s) for s in snaps), i) for i, c in enumerate(uni))
    sample = pool[:80]
    true_then = owner["then"]
    expect = [run(rules, xs, t, acc0) for xs, t in sample]
    pick = None
    tried = 0
    for miss, i in scored:
        if miss == 0:
            continue
        tried += 1
        if tried > 60:
            break
        owner["then"] = {**uni[i], "id": arm["id"]}
        diff = [j for j, (xs, t) in enumerate(sample) if run(rules, xs, t, acc0) != expect[j]]
        if diff and len(diff) < len(sample):
            pick = (i, diff)
            break
    owner["then"] = true_then
    if pick is None:
        raise _Retry()
    i, diff = pick
    dset = set(diff)
    same = [j for j in range(len(sample)) if j not in dset]
    names = sorted(g for g in groups if g.startswith(label))
    cur = [groups[g][0] for g in names]
    exp_cur = [run(rules, xs, t, acc0) for xs, t in cur]
    owner["then"] = {**uni[i], "id": arm["id"]}
    caught = [run(rules, xs, t, acc0) != e for (xs, t), e in zip(cur, exp_cur)]
    owner["then"] = true_then
    if not any(caught):
        groups[names[0]] = [sample[diff[0]]]
    if all(caught):
        groups[names[-1]] = [sample[same[0]]]
    return uni[i]


def _hidden_mutants(rules, acc0, near):
    """transcribe-only (every hidden arm a no-op) and near-miss (every hidden arm replaced by its near-miss
    candidate, see _pick_nearmiss)"""
    import copy
    out = []
    m = copy.deepcopy(rules)
    for r in m:
        if r["kind"] == "if" and r["then"].get("hidden"):
            r["then"] = {"kind": "noop", "id": r["then"]["id"]}
    out.append(("transcribe-only", reference_source(m, acc0)))
    m = copy.deepcopy(rules)
    for r in m:
        if r["kind"] == "if" and r["then"].get("hidden"):
            r["then"] = {**near[r["then"]["id"]], "id": r["then"]["id"]}
    out.append(("near-miss", reference_source(m, acc0)))
    return out


def _src_share(src, groups):
    """share of test groups a generated reference/mutant source passes (it is our own generator's output)"""
    ns: dict = {}
    exec(src, ns)
    ok = 0
    for code in groups.values():
        try:
            exec(code, {"f": ns["f"]})
            ok += 1
        except AssertionError:
            pass
    return ok / len(groups)


def _snap_text(snap):
    (a0, x0, t0), (a1, x1, t1) = snap
    return f"before: acc={a0}, xs={list(x0)!r}, t={t0!r}  ->  after: acc={a1}, xs={list(x1)!r}, t={t1!r}"


def _hidden_text(rules, snaps_by_arm, level):
    arms = _hidden_arms(rules)
    names = [f"{a['id']}" for a in arms]
    ids = f"Step {names[0]}" if len(names) == 1 else "Steps " + " and ".join(names)
    lines = [f"\nHIDDEN STEPS\n{ids} {'is' if len(arms) == 1 else 'are'} not described in words. "
             f"Each hidden step is exactly one entry of the catalogue below, with one choice of its constants. "
             f"The snapshots after the catalogue show the state (acc, xs, t) just before and just after one application "
             f"of that step. They identify it: any catalogue entry that fits all the snapshots of a step behaves exactly "
             f"like the real step, and that is the one you must implement. The condition that decides whether the step "
             f"runs is described above as usual.\n", "Catalogue (<k>, <r>, <m> and <n> stand for the constants of an entry; their ranges follow):"]
    for kind in _cat_kinds(level):
        if kind == "drop_every":
            txt = ("Remove every element of xs whose 1-based position (in the list as it is before this step) "
                   "is a multiple of <k>.")
        else:
            ph = {"id": "-", "kind": kind, "k": "<k>", "r": "<r>", "m": "<m>", "n": "<n>"}
            txt = _ren_prim(ph)
        doc = _PARAM_DOC.get(kind)
        lines.append(f"- {txt}" + (f" [{doc}]" if doc else ""))
    for a in arms:
        lines.append(f"\nSnapshots of step {a['id']}:")
        for i, s in enumerate(snaps_by_arm[a["id"]], 1):
            lines.append(f"  {i}. {_snap_text(s)}")
    return "\n".join(lines) + "\n"


def _generate_hidden(key, level):
    for attempt in range(80):
        try:
            rules, acc0 = make_rules(key, level, attempt)
        except RuntimeError:
            continue
        arms = _hidden_arms(rules)
        if len(arms) != LEVELS[level]["hidden"]:
            continue
        if any(not _HIDE_FRAC[0] <= _fire_rate(rules, acc0, a["id"]) <= _HIDE_FRAC[1] for a in arms):
            continue
        try:
            groups, examples, pools = _hidden_tests(key, level, rules, acc0, attempt)
            hid_ids = {a["id"] for a in arms}

            def test_pre_of(groups):
                tp: dict = {i: [] for i in hid_ids}
                for g in groups.values():
                    for xs, t in g:
                        for i, pre, post in _capture(rules, xs, t, acc0, hid_ids):
                            tp[i].append(pre)
                return tp
            test_pre = test_pre_of(groups)
            snaps_by, uni_by, alive_by, near = {}, {}, {}, {}
            for a in arms:
                snaps_by[a["id"]], uni_by[a["id"]], alive_by[a["id"]] = _snapshots(
                    key, level, rules, acc0, a, test_pre[a["id"]], attempt)
                near[a["id"]] = _pick_nearmiss(rules, acc0, a, snaps_by[a["id"]], uni_by[a["id"]],
                                               pools[a["id"]], groups)
            test_pre = test_pre_of(groups)           # the groups moved: the survivors must still match the truth
            for a in arms:
                for c in alive_by[a["id"]]:
                    if any(_apply(c, pre) != _apply(a, pre) for pre in test_pre[a["id"]]):
                        raise _Retry()
            hm = _hidden_mutants(rules, acc0, near)
        except _Retry:
            continue
        return rules, acc0, groups, examples, snaps_by, hm, attempt
    raise RuntimeError("keyfunc: no acceptable hidden-arm instance")


# ------------------------------------------------------------------ artefacts
def _interp_source() -> str:
    src = inspect.getsource(sys.modules[__name__])
    a = src.index("# BEGIN-INTERP") + len("# BEGIN-INTERP")
    b = src.index("# END-INTERP")
    body = src[a:b]
    # the module-level override of _cond for the repeat guard must be in the reference too
    body += "\n\n_base_cond = _cond\n\n\ndef _cond(c, st):\n    if c['kind'] == 'not_acc_mod':\n        return st['acc'] % c['m'] != c['r']\n    return _base_cond(c, st)\n"
    return body


def reference_source(rules, acc0) -> str:
    return (_interp_source() + "\n\nRULES = " + repr(rules) + f"\nACC0 = {acc0}\n\n\n"
            "def f(xs, t):\n    return run(RULES, list(xs), t, ACC0)\n")


def spec_text(rules, acc0, examples, snaps=None, level=None) -> str:
    lines: list[str] = []
    _render_steps(rules, lines)
    ex = "\n".join(f"- f({xs!r}, {t!r}) returns {run(rules, xs, t, acc0)!r}" for xs, t in examples)
    hid = _hidden_text(rules, snaps, level) if snaps else ""
    return (PREAMBLE.format(acc0=acc0) + "\n".join(lines) + "\n" + hid + "\nExamples (your function must reproduce these exactly):\n"
            + ex + "\n" + TAIL)


def _mutants(key: int, level: int, rules, acc0, salt: str = "") -> list[tuple[str, str]]:
    """(name, source) pairs that must FAIL the tests (every step is observable, see
    _ensure_observable, so dropping any one of them changes some output)."""
    import copy
    rng = _rng(key, level, "mutants" + salt)
    out = []
    # 1. one primitive step (possibly nested) becomes a no-op
    m = copy.deepcopy(rules)
    owner, cont, k, prim = rng.choice(list(_walk_prims(m)))
    cont[k] = {"kind": "noop", "id": prim["id"]}
    out.append(("drop-one-step", reference_source(m, acc0)))
    # 2. one numeric constant off by one (any primitive with an int parameter)
    m = copy.deepcopy(rules)
    cands = [p for _, _, _, p in _walk_prims(m) if any(k2 != "id" and isinstance(v, int) for k2, v in p.items())]
    if cands:
        r = rng.choice(cands)
        for k2 in ("k", "p", "m", "n", "r"):
            if isinstance(r.get(k2), int):
                r[k2] = r[k2] + 1
                break
        out.append(("off-by-one-constant", reference_source(m, acc0)))
    else:  # every step is parameterless: swap the first two steps instead
        m[0], m[1] = m[1], m[0]
        out.append(("swapped-steps", reference_source(m, acc0)))
    # 3. wrong initial acc
    out.append(("wrong-initial-acc", reference_source(rules, (acc0 + 1) % 2**32)))
    return out


_HIDDEN_CACHE: dict = {}


def generate(key: int, level: int) -> dict:
    if LEVELS[level].get("hidden"):          # a hidden-arm instance takes ~1 s to search: build it once
        hit = _HIDDEN_CACHE.get((key, level))   # one read: another thread may clear() the cache in between
        if hit is None:
            hit = _generate(key, level)
            if len(_HIDDEN_CACHE) > 256:
                _HIDDEN_CACHE.clear()
            _HIDDEN_CACHE[(key, level)] = hit
        return copy.deepcopy(hit)
    return _generate(key, level)


def _generate(key: int, level: int) -> dict:
    snaps, extra, names = None, [], None
    if LEVELS[level].get("hidden"):
        rules, acc0, groups, examples, snaps, extra, _att = _generate_hidden(key, level)
        groups_raw = groups
        tests = {"examples": examples,
                 "groups": {g: "\n".join(_assert_line(xs, t, run(rules, xs, t, acc0)) for xs, t in v)
                            for g, v in groups.items()}}
    else:
        rules, acc0 = make_rules(key, level)
        tests = make_tests(key, level, rules, acc0)
    spec = spec_text(rules, acc0, tests["examples"], snaps, level)
    base_muts = _mutants(key, level, rules, acc0)
    if extra:                          # on the small hidden-level test sets a slip can hide: redraw until each is caught
        for salt in range(1, 40):
            if all(_src_share(src, tests["groups"]) < 1.0 for _, src in base_muts):
                break
            base_muts = _mutants(key, level, rules, acc0, f"#{salt}")
    if extra:
        names = [n for n, _ in base_muts] + [n for n, _ in extra]
    case = {
        "id": f"KF-L{level}-{key:016x}",
        "difficulty": {1: "easy", 2: "medium"}.get(level, "hard"),
        "system": "You are an expert Python programmer. Return only the requested function inside a single ```python code block. No explanation.",
        "prompt": spec,
        "temperature": 0.0,
        "max_tokens": 6144,
        "grader": "python_exec",
        "grader_config": {"tests": "", "test_groups": tests["groups"]},
        "reference": reference_source(rules, acc0),
        "mutants": [src for _, src in base_muts] + [src for _, src in extra],
        "generator": {"type": "keyfunc", "key": f"{key:016x}", "level": level},
        "meta": {"n_steps": len(rules), "n_if": sum(r["kind"] == "if" for r in rules),
                 "n_repeat": sum(r["kind"] == "repeat" for r in rules),
                 "n_counter": sum(r["kind"] == "counter" for r in rules),
                 "n_groups": len(tests["groups"]),
                 "spec_chars": len(spec), "spec_words": len(spec.split()),
                 "spec_tokens_est": round(len(spec) / 4)},
    }
    if extra:
        case["_hidden"] = {"rules": rules, "acc0": acc0, "snaps": snaps, "groups": groups_raw}
        case["meta"].update(n_hidden=len(snaps), n_snaps={i: len(v) for i, v in snaps.items()},
                            mutant_names=names, hidden_ids=sorted(snaps),
                            group_classes={c: sum(g.startswith(c) for g in tests["groups"])
                                           for c in ("visible", "hidden", "both")})
    return case


# ------------------------------------------------------------ generator interface
from .. import gen as _gen  # noqa: E402  (one seed derivation: gen.instance_seed)

FAMILY = "keyfunc"


def _instance_key(gen: dict, release_seed: str, case_id: str) -> tuple[int, int]:
    level = int(gen["level"])
    return _gen.instance_int(FAMILY, release_seed, case_id, level, gen.get("key")), level


def _build(gen: dict, release_seed: str, case_id: str) -> dict:
    key, level = _instance_key(gen, release_seed, case_id)
    if level not in LEVELS:
        raise ValueError(f"keyfunc: unknown level {level}")
    return generate(key, level)


def materialize(gen: dict, *, release_seed: str, case_id: str) -> dict:
    """Case keys for the engine's ``python_exec`` grader (``test_groups``)."""
    c = _build(gen, release_seed, case_id)
    out = {k: c[k] for k in ("system", "prompt", "temperature", "max_tokens", "grader",
                             "grader_config", "reference", "mutants", "meta")}
    # the 4.5 grading path (gen/_det.py): one reply, one grade; generate() stays the 4.1 generator byte for byte
    out["grader_config"] = {**out["grader_config"], "deterministic": True}
    return out


def reference(gen: dict, *, release_seed: str, case_id: str) -> str:
    return "```python\n" + _build(gen, release_seed, case_id)["reference"] + "\n```"


def mutants(gen: dict, *, release_seed: str, case_id: str) -> list[str]:
    return ["```python\n" + m + "\n```" for m in _build(gen, release_seed, case_id)["mutants"]]


GRADE_IS_ENGINE = True      # grade() IS the engine's python_exec grader: no second sandbox pass in verify_case


def grade(answer: str, materialized: dict) -> float:
    """Share of test groups passed, run through the engine's own sandboxed
    ``python_exec`` grader (bwrap; never exec'd in this process)."""
    from .. import graders
    cfg = materialized["grader_config"]
    return float(graders.grade_python_exec(answer, cfg).get("rate") or 0.0)


if __name__ == "__main__":
    _g = {"type": FAMILY, "level": int(sys.argv[2]) if len(sys.argv) > 2 else 2}
    _m = materialize(_g, release_seed=sys.argv[1] if len(sys.argv) > 1 else "dev", case_id="KF-demo")
    print(_m["prompt"])
    print("\n# meta:", json.dumps(_m["meta"]))
