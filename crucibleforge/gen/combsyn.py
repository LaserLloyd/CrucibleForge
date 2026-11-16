"""combsyn - planted combinator synthesis in a seeded rewrite calculus, MakoBench 4.5 GEN2 prototype.

Each instance defines a tiny applicative rewrite calculus that exists only in the prompt: 4-5
constants with seeded one-letter names, each with one seeded rule such as ``Q x y z -> y (x z)``.
The basis is arbitrary (it need not be complete, and the letters mean nothing). The model is asked
for K closed terms over those constants such that ``T a b c`` rewrites to a stated result, using at
most a stated number of constants. The answer is a literal dict of term strings; the hidden test
parses each term, checks its size, rewrites ``T a b c`` to normal form and compares.

Why this shape: the spec is ten lines and the answer is a few symbols, so there is nothing to
transcribe and no length to grow. Every target is the behaviour of a term the generator already
holds (so a solution inside the size limit always exists), found by enumerating all terms up to a
size S; the stated limit is at or just above the certified minimum, so the model has to search a
space that grows about 15-20x per constant, with no recipe: bracket abstraction needs S and K, which
a seeded basis usually lacks, and blows past the limit when they are present. Verification is one
normalisation.

Level table (30 seeds per level; S = the largest size the enumeration certifies, 5 or 6):
  level  constants  targets (certified minimum size m, or planted size P)   limit      prompt tok
    1    4          m = 2, 3, 3, 4                                           m + 2      420-560
    2    4          m = 3, 4, 4, 5, 5                                        m + 1      430-580
    3    5          m = 4, 5, 5, S, S                                        m + 1      450-600
    4    5          m = 5, S, S, then planted P = 8 and P = 9 (m > S)        m, P       450-610
    5    5          m = S five times, with S >= 6 (deeper enumeration)       m          450-620
    6    6          m = S three times (S >= 6), planted P = 12 and 14        m, P       470-640
    7    5          m = 7 five times (enumeration to size 7, ~15-60 s)        m          480-660
    8    5          level 7 inside certified-difficulty bands (see LEVELS)     m          480-660
Stdlib only. Same (key, level) -> byte-identical prompt, reference and tests.
"""
from __future__ import annotations

import hashlib
import json
import os
import random
import sys

LEVELS = {
    1: {"consts": 4, "sizes": [2, 3, 3, 4], "slack": 2, "planted": []},
    2: {"consts": 4, "sizes": [3, 4, 4, 5, 5], "slack": 1, "planted": []},
    3: {"consts": 5, "sizes": [4, 5, 5, "S", "S"], "slack": 1, "planted": []},
    4: {"consts": 5, "sizes": [5, "S", "S"], "slack": 0, "planted": [8, 9]},
    5: {"consts": 5, "sizes": ["S", "S", "S", "S", "S"], "slack": 0, "planted": [], "budget": 3_000_000, "min_s": 6},
    6: {"consts": 6, "sizes": ["S", "S", "S"], "slack": 0, "planted": [12, 14], "budget": 3_000_000, "min_s": 6},
    7: {"consts": 5, "sizes": ["S", "S", "S", "S", "S"], "slack": 0, "planted": [], "budget": 12_000_000, "min_s": 7},
    # level 8 = level 7 with certified-difficulty bands, so that instances of different releases are comparable:
    # the basis must have 2.0-5.0 million normal-form-distinct terms at size 7 (the exhaustive-search node count) and
    # 1,000-5,000 certified size-7 behaviours; every target has at most 2 minimal solutions, a witness that uses at
    # least 3 different constants, and a result of 5-9 symbols with no symbol more than 5 times
    8: {"consts": 5, "sizes": ["S", "S", "S", "S", "S"], "slack": 0, "planted": [], "budget": 12_000_000, "min_s": 7,
        "band": {"nodes": (2_000_000, 5_000_000), "beh": (1000, 5000), "sols": 2, "consts": 3, "leaves": (5, 9),
                 "rep": 5}},
}
_ANSWER_TOKENS = {1: 30, 2: 40, 3: 45, 4: 50, 5: 50, 6: 55, 7: 55, 8: 55}
for _lv, _row in LEVELS.items():
    _row["answer_tokens"] = _ANSWER_TOKENS[_lv]

MAX_STEPS = 2000           # stated to the model
BUDGET_HINT = 40000        # reasoning budget stated to the model (round 3: unbudgeted searches ran past 900 s)
CAP = 65536                # the declared hard-item output cap, stated to the model (DECISIONS-45 stated-budget rule)
ENUM_BUDGET = 60000        # pair evaluations before the enumeration stops growing
VARS = ("a", "b", "c")
STATS: dict = {}            # filled by enumerate_basis: nodes per size, minimal-solution count per behaviour

# rule pool: (arity, body) with body over argument indices; application = 2-tuple
_POOL = [
    (1, 0),                                  # x -> x
    (2, 0),                                  # x y -> x
    (2, 1),                                  # x y -> y
    (2, (1, 0)),                             # x y -> y x
    (2, ((0, 1), 1)),                        # x y -> x y y
    (3, (0, (1, 2))),                        # x y z -> x (y z)
    (3, ((0, 2), 1)),                        # x y z -> x z y
    (3, ((0, 2), (1, 2))),                   # x y z -> x z (y z)
    (3, ((2, 0), 1)),                        # x y z -> z x y
    (3, ((1, 2), 0)),                        # x y z -> y z x
    (3, (1, (0, 2))),                        # x y z -> y (x z)
    (3, ((2, 1), 0)),                        # x y z -> z y x
    (3, (((0, 1), 2), 1)),                   # x y z -> x y z y
    (3, (2, (0, 1))),                        # x y z -> z (x y)
    (3, (0, 2)),                             # x y z -> x z
    (3, ((1, 0), 2)),                        # x y z -> y x z
    (3, (0, (2, 1))),                        # x y z -> x (z y)
    (3, ((1, 2), (0, 2))),                   # x y z -> y z (x z)
]
_NAMES = "DEFGHJLNPQRUVXZ"

# ---------------------------------------------------------------------------- calculus (embedded in the tests)
CALC_SRC = '''\
def _cs_parse(text, names):
    """Term text -> nested 2-tuples of names. Single-letter names, spaces optional, left-associative."""
    toks = [ch for ch in str(text) if not ch.isspace()]
    pos = [0]

    def atom():
        if pos[0] >= len(toks):
            raise ValueError("unexpected end of term")
        ch = toks[pos[0]]
        pos[0] += 1
        if ch == "(":
            t = seq()
            if pos[0] >= len(toks) or toks[pos[0]] != ")":
                raise ValueError("missing )")
            pos[0] += 1
            return t
        if ch not in names:
            raise ValueError("symbol not allowed: " + ch)
        return ch

    def seq():
        t = atom()
        while pos[0] < len(toks) and toks[pos[0]] != ")":
            t = (t, atom())
        return t
    t = seq()
    if pos[0] != len(toks):
        raise ValueError("unbalanced )")
    return t


def _cs_size(t):
    n, stack = 0, [t]
    while stack:
        u = stack.pop()
        if isinstance(u, tuple):
            stack.extend(u)
        else:
            n += 1
    return n


def _cs_subst(body, args):
    if isinstance(body, tuple):
        return (_cs_subst(body[0], args), _cs_subst(body[1], args))
    return args[body]


def _cs_step(t, rules):
    """One leftmost-outermost rewrite. -> (term, changed)."""
    args, head = [], t
    while isinstance(head, tuple):
        args.append(head[1])
        head = head[0]
    args.reverse()
    r = rules.get(head)
    if r is not None and len(args) >= r[0]:
        out = _cs_subst(r[1], args[:r[0]])
        for x in args[r[0]:]:
            out = (out, x)
        return out, True
    for i, x in enumerate(args):
        y, ch = _cs_step(x, rules)
        if ch:
            out = head
            for j, z in enumerate(args):
                out = (out, y if j == i else z)
            return out, True
    return t, False


def _cs_norm(t, rules, max_steps, max_size=4000):
    for _ in range(max_steps):
        t, ch = _cs_step(t, rules)
        if not ch:
            return t
        if _cs_size(t) > max_size:
            raise RuntimeError("term grew past the size guard")
    t2, ch = _cs_step(t, rules)
    if ch:
        raise RuntimeError("no result within the step limit")
    return t


def _cs_show(t, top=True):
    if not isinstance(t, tuple):
        return t
    s = _cs_show(t[0], True) + " " + _cs_show(t[1], False)
    return s if top else "(" + s + ")"


def _cs_check(text, rules, names, limit, want, max_steps):
    t = _cs_parse(text, names)
    assert _cs_size(t) <= limit, "term uses %d constants, limit %d" % (_cs_size(t), limit)
    got = _cs_show(_cs_norm((((t, "a"), "b"), "c"), rules, max_steps))
    assert got == want, "T a b c rewrites to %s, want %s" % (got[:60], want)
'''
_NS: dict = {}
exec(CALC_SRC, _NS)  # noqa: S102 - our own source
_parse, _size, _norm, _show, _check, _step = (_NS[k] for k in ("_cs_parse", "_cs_size", "_cs_norm", "_cs_show",
                                                                "_cs_check", "_cs_step"))

PRELUDE = '''\
import ast as _ast, __main__ as _main
_tree = _ast.parse(getattr(_main, "_solution", ""))
if not (len(_tree.body) == 1 and isinstance(_tree.body[0], _ast.Assign) and len(_tree.body[0].targets) == 1
        and isinstance(_tree.body[0].targets[0], _ast.Name) and _tree.body[0].targets[0].id == "TERMS"
        and isinstance(_tree.body[0].value, _ast.Dict)
        and all(isinstance(_n, _ast.Constant) and isinstance(_n.value, str)
                for _n in list(_tree.body[0].value.keys) + list(_tree.body[0].value.values))):
    raise ValueError("the answer must be exactly one literal assignment TERMS = {name: term text}")
'''


def _rng(key: int, level: int, tag: str) -> random.Random:
    return random.Random(f"combsyn:{key:016x}:{level}:{tag}")


def _pure(t) -> bool:
    stack = [t]
    while stack:
        u = stack.pop()
        if isinstance(u, tuple):
            stack.extend(u)
        elif u not in VARS:
            return False
    return True


def _try_norm(t, rules, steps, size):
    try:
        return _norm(t, rules, steps, size)
    except RuntimeError:
        return None


def enumerate_basis(rules: dict, budget: int = ENUM_BUDGET):
    """All closed terms by size, deduplicated by normal form, until the pair budget runs out.
    -> (S, behaviours) with behaviours = {shown result of ``T a b c``: (minimum size, witness term)} for the
    terms whose result contains only a, b, c. S is the largest fully enumerated size."""
    names = sorted(rules)
    seen = {}                       # normal form -> witness (a term of minimum size)
    by_size = {1: []}
    for n in names:
        nf = _try_norm(n, rules, 60, 80)
        if nf is not None and nf not in seen:
            seen[nf] = n
            by_size[1].append(nf)
    beh, used, size = {}, 0, 1
    STATS.clear()
    STATS.update(nodes={}, sols={})
    while True:
        STATS["nodes"][size] = len(by_size[size])
        for nf in by_size[size]:
            r = _try_norm((((nf, "a"), "b"), "c"), rules, 80, 120)
            if r is not None and _pure(r):
                k = _show(r)
                beh.setdefault(k, (size, seen[nf]))
                if beh[k][0] == size:
                    STATS["sols"][k] = STATS["sols"].get(k, 0) + 1
        nxt = size + 1
        cost = sum(len(by_size[i]) * len(by_size[nxt - i]) for i in range(1, nxt))
        if used + cost > budget or nxt > 7:
            return size, beh
        used += cost
        by_size[nxt] = []
        for i in range(1, nxt):
            for f in by_size[i]:
                for x in by_size[nxt - i]:
                    nf = _try_norm((f, x), rules, 60, 80)
                    if nf is not None and nf not in seen:
                        seen[nf] = (seen[f], seen[x])
                        by_size[nxt].append(nf)
        size = nxt


def _rule_text(name, arity, body):
    xs = "xyz"[:arity]
    return f"{name} {' '.join(xs)}  ->  {_show(_NS['_cs_subst'](body, list(xs)))}"


def _interesting(shown: str) -> bool:
    letters = [ch for ch in shown if ch in VARS]
    return 2 <= len(letters) <= 9 and len(set(letters)) >= 2


def _random_term(rng, names, size):
    if size == 1:
        return rng.choice(names)
    i = rng.randrange(1, size)
    return (_random_term(rng, names, i), _random_term(rng, names, size - i))


SYSTEM = ("You are an expert in term rewriting and combinatory logic. Follow the calculus defined in the message "
          "exactly and return only the requested literal inside a single ```python code block. No explanation.")


def generate(key: int, level: int) -> dict:
    if level not in LEVELS:
        raise ValueError(f"combsyn: unknown level {level}")
    lv = LEVELS[level]
    attempt = 0
    while True:
        rng = _rng(key, level, f"build-{attempt}")
        attempt += 1
        picks = rng.sample(_POOL, lv["consts"])
        ar = [p[0] for p in picks]
        if 3 not in ar or sum(1 for a in ar if a == 3) < 2:
            continue
        names = rng.sample(_NAMES, lv["consts"])
        rules = {n: (p[0], p[1]) for n, p in zip(names, picks)}
        s_max, beh = enumerate_basis(rules, lv.get("budget", ENUM_BUDGET))
        if s_max < lv.get("min_s", 5):
            continue
        band = lv.get("band")
        sols, nodes = dict(STATS["sols"]), dict(STATS["nodes"])
        by_m = {}
        for shown, (m, wit) in beh.items():
            if _interesting(shown):
                by_m.setdefault(m, []).append((shown, wit))
        if band:
            if not (band["nodes"][0] <= nodes.get(s_max, 0) <= band["nodes"][1]
                    and band["beh"][0] <= len(by_m.get(s_max, [])) <= band["beh"][1]):
                continue

            def fits(shown, wit):
                letters = [ch for ch in shown if ch in VARS]
                return (sols[shown] <= band["sols"] and len(set(_show(wit)) - set(" ()")) >= band["consts"]
                        and band["leaves"][0] <= len(letters) <= band["leaves"][1]
                        and max(letters.count(v) for v in VARS) <= band["rep"])
            by_m = {m: [(sh, w) for sh, w in v if fits(sh, w)] for m, v in by_m.items()}
        want_sizes = [s_max if s == "S" else s for s in lv["sizes"]]
        if any(len(by_m.get(m, [])) < want_sizes.count(m) for m in set(want_sizes)):
            continue
        targets = []
        for m in sorted(set(want_sizes)):
            pool = sorted(by_m[m])
            for shown, wit in rng.sample(pool, want_sizes.count(m)):
                targets.append({"want": shown, "limit": m + lv["slack"], "ref": _show(wit), "min": m, "planted": False,
                                "solutions": sols.get(shown)})
        ok = True
        for p in lv["planted"]:
            for _ in range(20000):
                t = _random_term(rng, sorted(names), p)
                r = _try_norm((((t, "a"), "b"), "c"), rules, 200, 300)
                if r is None or not _pure(r):
                    continue
                shown = _show(r)
                if shown in beh or not _interesting(shown) or any(shown == q["want"] for q in targets):
                    continue
                targets.append({"want": shown, "limit": p, "ref": _show(t), "min": None, "planted": True,
                                "solutions": None})
                break
            else:
                ok = False
        if ok:
            break
    # a worked example that is not one of the answers
    ex = None
    erng = _rng(key, level, "example")
    for _ in range(500):
        t = _random_term(erng, sorted(names), 3)
        cur, trace = (((t, "a"), "b"), "c"), []
        for _s in range(8):
            trace.append(_show(cur))
            cur, ch = _step(cur, rules)
            if not ch:
                break
        if 3 <= len(trace) <= 6 and _pure(cur) and all(_show(t) != q["ref"] for q in targets) \
                and all(trace[-1] != q["want"] for q in targets):
            ex = (_show(t), trace)
            break
    names_s = ", ".join(sorted(names))
    tasks = {f"T{i}": q for i, q in enumerate(targets, 1)}
    shown_tasks = "\n".join(f"{tid}. T a b c  ->  {q['want']}        (at most {q['limit']} constants)"
                            for tid, q in tasks.items())
    example = ""
    if ex:
        example = (f"- Example with these rules: `{ex[0]}` applied to a, b, c rewrites as\n    "
                   + "\n    -> ".join(ex[1]) + "\n")
    prompt = (
        "This task uses a small rewriting calculus that is defined only here. You will write "
        f"{len(tasks)} terms in it.\n\n"
        "CALCULUS\n"
        "- A term is a symbol, or one term applied to another. Application is written by putting the two terms next "
        "to each other and it groups to the left: `F G H` means `(F G) H`. Parentheses group. Every symbol is a "
        "single letter, so spaces are optional.\n"
        f"- There are {len(names)} constants, {names_s}. Each has one rewrite rule (x, y, z stand for any terms):\n"
        + "\n".join("    " + _rule_text(n, *rules[n]) for n in sorted(names)) + "\n"
        "- A rule can be used on any subterm in which the constant is followed by at least as many argument terms as "
        "its rule shows. The constant and those arguments are replaced by the right-hand side as one unit; any "
        "further arguments stay after it. The letters in the names carry no meaning beyond these rules.\n"
        "- The lowercase symbols a, b, c have no rule. A term is finished when no rule can be used anywhere in it. "
        "Rewriting always takes the leftmost-outermost usable position first.\n"
        + example + "\n"
        "TASKS\n"
        f"For each task give a term T built only from the constants {names_s} (no a, b, c) such that `T a b c` "
        f"rewrites to exactly the stated finished term within {MAX_STEPS} rewrite steps. The number of constant "
        "occurrences in T must not exceed the limit shown. A term within the limit exists for every task.\n\n"
        + shown_tasks + "\n\n"
        "ANSWER\n"
        "Return exactly one ```python block that contains only this literal assignment and nothing else:\n\n"
        "TERMS = {\n" + "".join(f'    "{t}": "<term>",\n' for t in tasks) + "}\n\n"
        "The block is parsed as a literal; any code makes the whole answer invalid. Score: one point per correct "
        "term; a wrong term costs nothing beyond its own point. Your whole reply, reasoning included, is cut off at "
        f"{CAP:,} tokens, and a reply that is cut off scores 0 for every task. So budget your search: aim to finish "
        f"within about {BUDGET_HINT:,} tokens of reasoning, and "
        "always give the block with your best candidate for every task, including the ones you could not settle. "
        "No explanation.")
    cfg_rules = {n: [a, b] for n, (a, b) in rules.items()}
    groups = {}
    for tid, q in tasks.items():
        groups[tid] = (CALC_SRC + f"\n_rules = {{k: (v[0], v[1]) for k, v in {rules!r}.items()}}\n"
                       f"assert isinstance(TERMS, dict) and isinstance(TERMS.get({tid!r}), str), 'no term {tid}'\n"
                       f"_cs_check(TERMS[{tid!r}], _rules, {sorted(names)!r}, {q['limit']}, {q['want']!r}, {MAX_STEPS})\n")

    def render(terms):
        return "TERMS = {\n" + "".join(f'    "{t}": "{v}",\n' for t, v in terms.items()) + "}"
    ref = {tid: q["ref"] for tid, q in tasks.items()}
    reference = render(ref)
    mutants = []
    for tid, q in tasks.items():          # one term wrong: a different target's term, or a bare constant
        other = next((v for t2, v in ref.items() if t2 != tid), sorted(names)[0])
        mutants.append(render({**ref, tid: other}))
    mutants.append(reference.replace("TERMS = {", "TERMS = dict({", 1) + ")")
    return {
        "id": f"CS-L{level}-{key:016x}",
        "difficulty": "hard",
        "system": SYSTEM,
        "prompt": prompt,
        "temperature": 0.0,
        "max_tokens": 6144,
        "grader": "python_exec",
        "grader_config": {"tests": PRELUDE, "test_groups": groups},
        "reference": reference,
        "mutants": mutants,
        "generator": {"type": "combsyn", "key": f"{key:016x}", "level": level},
        "meta": {"rules": {n: _rule_text(n, *rules[n]) for n in sorted(names)}, "certified_size": s_max,
                 "targets": [{k: q[k] for k in ("want", "limit", "min", "planted", "solutions")} for q in targets],
                 "search_nodes": nodes.get(s_max), "behaviours_at_min": len(by_m.get(s_max, [])),
                 "n_groups": len(tasks), "behaviours": len(beh), "_rules": cfg_rules,
                 "prompt_tokens_est": round(len(prompt) / 4), "answer_tokens_est": round(len(reference) / 4)},
    }


# ------------------------------------------------------------ generator interface
from .. import gen as _gen  # noqa: E402

FAMILY = "combsyn"
_CACHE: dict = {}
_FROZEN_DIR = __import__("pathlib").Path(__file__).resolve().parent.parent / "data" / "combsyn-frozen"
with open(__file__, "rb") as _fh:
    _SRC_HASH = hashlib.sha256(_fh.read()).hexdigest()[:12]


def _build(gen: dict, release_seed: str, case_id: str) -> dict:
    v = gen.get("variant")
    if v:                       # a single-target case shares the instance of the case id without the variant tag
        base = dict(gen)
        base.pop("variant")
        return _single(_build(base, release_seed, case_id.replace(f"combsyn-{v}", "combsyn")), int(v[1:]))
    level = int(gen["level"])
    # T7: `seed_case_id` pins the instance to the case id it was frozen under (the instance seed includes the
    # case id; FC2's p1-p4 were measured as PRB-combsyn-L7-p<k>)
    key = _gen.instance_int(FAMILY, release_seed, gen.get("seed_case_id") or case_id, level, gen.get("key"))
    frozen = _FROZEN_DIR / f"combsyn-{level}-{key:016x}.json"
    if (key, level) not in _CACHE and frozen.is_file():
        # a frozen instance (level 7 takes minutes to enumerate): committed data, independent of this file's hash
        _CACHE[(key, level)] = json.loads(frozen.read_text(encoding="utf-8"))
    if (key, level) not in _CACHE:
        if len(_CACHE) > 256:
            _CACHE.clear()
        disk = os.environ.get("CRUCIBLEFORGE_GEN_CACHE")       # optional: deep levels take minutes to enumerate
        path = os.path.join(disk, f"combsyn-{level}-{key:016x}-{_SRC_HASH}.json") if disk else None
        if path and os.path.exists(path):
            with open(path, encoding="utf-8") as fh:
                _CACHE[(key, level)] = json.load(fh)
        else:
            _CACHE[(key, level)] = generate(key, level)
            if path:
                os.makedirs(disk, exist_ok=True)
                tmp = path + f".{os.getpid()}.tmp"
                with open(tmp, "w", encoding="utf-8") as fh:
                    json.dump(_CACHE[(key, level)], fh)
                os.replace(tmp, path)
    return _CACHE[(key, level)]


VARIANTS = ("t1", "t2", "t3", "t4", "t5")
SINGLE_CAP, SINGLE_BUDGET = 24576, 15000


def _single(c: dict, k: int) -> dict:
    """One-target case cut from a built multi-target instance: same calculus and example, task k only (shown as
    T1), the standard 24,576 cap and a 15,000-token budget in the prompt. Five such cases replace one bundled
    instance, so a reply that is cut costs one target and not five."""
    import re
    tid = f"T{k}"
    tasks = re.findall(r"^T\d\. T a b c  ->  .*$", c["prompt"], re.M)
    if not 1 <= k <= len(tasks):
        raise ValueError(f"combsyn: no target {k} in this instance")
    prompt = c["prompt"].replace(f"You will write {len(tasks)} terms in it.", "You will write one term in it.")
    prompt = prompt.replace("\n".join(tasks), "T1." + tasks[k - 1][3:])
    prompt = prompt.replace("".join(f'    "T{i}": "<term>",\n' for i in range(1, len(tasks) + 1)), '    "T1": "<term>",\n')
    prompt = prompt.replace(f"{CAP:,} tokens", f"{SINGLE_CAP:,} tokens").replace(f"{BUDGET_HINT:,} tokens", f"{SINGLE_BUDGET:,} tokens")
    prompt = prompt.replace("For each task give a term T", "Give a term T").replace("for every task.", "for the task.")
    prompt = prompt.replace("scores 0 for every task", "scores 0").replace("for every task, including the ones you could not settle", "for the task, even if you could not settle it")
    ref = re.search(rf'"{tid}": "([^"]*)"', c["reference"]).group(1)
    other = re.search(rf'"T{k % len(tasks) + 1}": "([^"]*)"', c["reference"]).group(1)
    out = dict(c)
    out.update(prompt=prompt, reference=f'TERMS = {{\n    "T1": "{ref}",\n}}',
               mutants=[f'TERMS = {{\n    "T1": "{other}",\n}}', f'TERMS = dict({{\n    "T1": "{ref}",\n}})'],
               grader_config={"tests": c["grader_config"]["tests"],
                              "test_groups": {"T1": c["grader_config"]["test_groups"][tid].replace(repr(tid), "'T1'")
                                              .replace(f"no term {tid}", "no term T1")}})
    meta = dict(c["meta"])
    meta.update(targets=[c["meta"]["targets"][k - 1]], n_groups=1, single_target=k, cap=SINGLE_CAP,
                prompt_tokens_est=round(len(prompt) / 4))
    out["meta"] = meta
    return out


def materialize(gen: dict, *, release_seed: str, case_id: str) -> dict:
    c = _build(gen, release_seed, case_id)
    out = json.loads(json.dumps({k: c[k] for k in ("system", "prompt", "temperature", "max_tokens", "grader",
                                                   "grader_config", "reference", "mutants", "meta")}))
    if not gen.get("variant"):
        out["meta"]["hard_item_cap"] = CAP     # T2 2b: a reply cut at the stated cap scores 0 (applied by the runner, as patgen)
    return out


def reference(gen: dict, *, release_seed: str, case_id: str) -> str:
    return "```python\n" + _build(gen, release_seed, case_id)["reference"] + "\n```"


def mutants(gen: dict, *, release_seed: str, case_id: str) -> list[str]:
    return ["```python\n" + m + "\n```" for m in _build(gen, release_seed, case_id)["mutants"]]


GRADE_IS_ENGINE = True


def grade(answer: str, materialized: dict) -> float:
    from .. import graders
    return float(graders.grade_python_exec(answer, materialized["grader_config"]).get("rate") or 0.0)


if __name__ == "__main__":
    _g = {"type": FAMILY, "level": int(sys.argv[2]) if len(sys.argv) > 2 else 3}
    _m = materialize(_g, release_seed=sys.argv[1] if len(sys.argv) > 1 else "dev", case_id="CS-demo")
    print(_m["prompt"])
    print("\n# reference:\n" + _m["reference"])
    print("\n# meta:", json.dumps(_m["meta"]))
