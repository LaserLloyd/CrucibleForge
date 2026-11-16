"""PYTRAP (CrucibleForge port of the fast-fail question ff/q/pytrap.py, branch t-pytrap @ c96cb21): predict the exact stdout of short
seeded Python snippets built from real semantic traps (late-binding closures, mutable defaults, floor-mod, banker's rounding, ...).
One item = a prompt with ``n`` snippets (default 5, ``n`` key). Every printed line is one graded check (exact match after rstrip,
positional inside the snippet's block), score = ff points / 100 (baseline 30, reference 100, floor 10, invalid 0).
The expected output is the snippet actually run in the engine sandbox (twice, different PYTHONHASHSEED; both runs must agree, rc 0)
at generation time; a naive reading (each trap replaced by its natural wrong reading) is run too and anchors the baseline.
The reply is plain text (not a code block): the grader reads only the printed-line predictions out of it.
Snippet j of (seed, tier) does not depend on ``n``. LEVELS 1 and 3 = the ff tiers. ``ffseed: N`` reproduces the ff instance."""
import base64
import functools
import json
import random
import re
import subprocess
import tempfile
from pathlib import Path

from .. import gen as _gen

FAMILY = "pytrap"
CPU_S = 20.0
MAX_TOKENS = 65536
DEFAULT_N = 5
LEVELS = {
    1: {"tier": 1, "answer_tokens": 79},
    3: {"tier": 3, "answer_tokens": 145},
}
VARIANTS = ()
POINTS_BASE, POINTS_REF, POINTS_FLOOR = 30.0, 100.0, 10.0

LINES = {1: (3, 6), 3: (8, 12)}
NBLOCKS = {1: (2, 3), 3: (4, 6)}

WORDS = ["lime", "pear", "plum", "fig", "kiwi", "date", "mango", "olive", "cherry", "grape", "peach", "melon", "berry",
         "apple", "lemon", "guava", "quince", "papaya", "banana", "orange", "nectar", "currant", "damson", "medlar"]
IDENTS = ("acc buf cur dat env grp hit idx job key log mem node out pos ref seq tmp val wrk zed ctr dst src msg row col tab "
          "res bag cfg dly eps fld gate hub inb jar knob lap mod nbr opt pkt qty rnk stk tgt unit vec wnd xfer yld zone").split()


class Names:
    def __init__(self, r):
        self.pool = IDENTS[:] + [w + "2" for w in IDENTS]
        r.shuffle(self.pool)

    def __call__(self):
        return self.pool.pop()


# ---------------------------------------------------------------- trap templates: (rng, N, k, o) -> (true_src, naive_src)
# k = source text of an int in 2..5 (a literal, or a parenthesised expression of an earlier block's result); o = out var.
def t_closure(r, N, k, o):
    f, i, n, g = N(), N(), N(), N()
    m = r.randint(2, 9)
    head = f"{n} = {k} + 1\n"
    tail = f"print([{g}() for {g} in {f}])\nprint({f}[0]())\n{o} = sum({g}() for {g} in {f})"
    return (head + f"{f} = [lambda: {i} * {m} for {i} in range({n})]\n" + tail,
            head + f"{f} = [lambda {i}={i}: {i} * {m} for {i} in range({n})]\n" + tail)


def t_mutdef(r, N, k, o):
    f, x, acc = N(), N(), N()
    b, c, d = r.sample(range(10, 60), 3)
    body = f"print({f}({k}))\nprint({f}({b}))\nprint({f}({c}, []))\nprint({f}({d}))\n{o} = len({f}({k}))"
    return (f"def {f}({x}, {acc}=[]):\n    {acc}.append({x})\n    return {acc}\n" + body,
            f"def {f}({x}, {acc}=None):\n    if {acc} is None:\n        {acc} = []\n    {acc}.append({x})\n    return {acc}\n" + body)


def t_alias(r, N, k, o):
    g = N()
    h = r.randint(2, 4)
    vv = r.randint(3, 9)
    body = f"{g}[{r.randrange(1, h)}][{r.randrange(2)}] = {vv}\nprint({g})\nprint({g}[0] is {g}[{h - 1}], sum({g}[0]))\n{o} = sum(map(sum, {g}))"
    return (f"{g} = [[0] * {k}] * {h}\n" + body, f"{g} = [[0] * {k} for _ in range({h})]\n" + body)


def t_slice(r, N, k, o):
    s = N()
    n = r.choice((8, 12, 14))
    s0 = r.randint(1, 40)
    p = r.randint(4, n - 2)
    q = r.randint(1, p - 1)
    pre = f"{s} = list(range({s0}, {s0 + n}))\n"
    t = pre + f"print({s}[::-{k}])\nprint({s}[{p}:{q}:-1])\nprint({s}[{q}:{p}:-1], {s}[{p}:{q}:-1][::2])\n{o} = len({s}[::-{k}])"
    v = pre + f"print({s}[::{k}][::-1])\nprint({s}[{q + 1}:{p}][::-1])\nprint({s}[{q}:{p}][::-1], {s}[{q + 1}:{p}][::-1][::2])\n{o} = len({s}[::{k}][::-1])"
    return t, v


def t_floormod(r, N, k, o):
    a, b = N(), N()
    x = r.choice((7, 11, 13, 17, 19, 23, 29, 31, 37, 41, 43, 47, 49, 53, 59))
    t = (f"{a} = -{x}\n{b} = {k}\nprint({a} // {b}, {a} % {b})\nprint(divmod({x}, -{b}))\n{o} = {a} // {b}")
    v = (f"{a} = -{x}\n{b} = {k}\nprint(int({a} / {b}), {a} - {b} * int({a} / {b}))\n"
         f"print((int({x} / -{b}), {x} - (-{b}) * int({x} / -{b})))\n{o} = int({a} / {b})")
    return t, v


def t_round(r, N, k, o):
    from decimal import Decimal, ROUND_HALF_UP
    while True:
        s = f"{r.randint(1, 9)}.{r.randint(0, 9)}{r.randint(0, 9)}5"
        if round(float(s), 2) != float(Decimal(s).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)):
            break
    A, B = r.choice((2, 4, 6, 8, 10)), r.choice((2, 4, 6, 8))
    rh = N()
    helper = (f"from decimal import Decimal, ROUND_HALF_UP\ndef {rh}(x, nd=0):\n    q = Decimal(repr(x)).quantize(Decimal(1).scaleb(-nd), rounding=ROUND_HALF_UP)\n"
              f"    return int(q) if nd == 0 else float(q)\n")
    t = f"print(round({k} * 2 + 0.5), round({A}.5))\nprint(round(-{B}.5))\nprint(round({s}, 2))\n{o} = round({k} * 2 + 0.5)"
    v = helper + f"print({rh}({k} * 2 + 0.5), {rh}({A}.5))\nprint({rh}(-{B}.5))\nprint({rh}({s}, 2))\n{o} = {rh}({k} * 2 + 0.5)"
    return t, v


def t_finally_order(r, N, k, o):
    f, n, e = N(), N(), N()
    m = r.randint(2, 6)
    head = f"def {f}({n}):\n    if {n} > 1:\n        try:\n            raise ValueError({n} * {m})\n        except ValueError as {e}:\n            print(\"caught\", {e})\n            return \"bad\" + str({n})\n"
    t = head + f"        finally:\n            print(\"fin\", {n})\n    return \"ok\"\nprint({f}({k}))\n{o} = {k} * {m}"
    v = head + f"    return \"ok\"\nprint({f}({k}))\nprint(\"fin\", {k})\n{o} = {k} * {m}"
    return t, v


def t_finally_value(r, N, k, o):
    f, g, x, l = N(), N(), N(), N()
    m = r.randint(2, 9)
    pre = (f"def {f}({x}):\n    try:\n        return {x}\n    finally:\n        {x} += {m}\n"
           f"def {g}({l}):\n    try:\n        return {l}\n    finally:\n        {l}.append({m})\n")
    pre_n = (f"def {f}({x}):\n    {x} += {m}\n    return {x}\n"
             f"def {g}({l}):\n    return {l}\n")
    body = f"print({f}({k}))\nprint({g}([{k}]))\n{o} = {f}({k})"
    return pre + body, pre_n + body


def t_generator(r, N, k, o):
    g, x = N(), N()
    m = r.randint(2, 7)
    body = f"print(sum({g}), list({g}))\nprint(len(list({g})))\nprint(next(iter({g}), {k}))\n{o} = len(list({g}))"
    return (f"{g} = ({x} * {m} + 1 for {x} in range({k} + 2))\n" + body,
            f"{g} = [{x} * {m} + 1 for {x} in range({k} + 2)]\n" + body)


def t_dict_order(r, N, k, o):
    d = N()
    a, b, c = r.sample(WORDS, 3)
    vb, vc = r.randint(2, 9), r.randint(2, 9)
    pre = f"{d} = {{\"{a}\": 1, \"{b}\": {vb}, \"{c}\": {vc}}}\n"
    tail = f"{d}[\"{a}\"] = {k}\nprint(list({d}))\nprint({d}.popitem())\nprint({d})\n{o} = sum({d}.values())"
    return pre + f"del {d}[\"{a}\"]\n" + tail, pre + tail


def t_split(r, N, k, o):
    s = N()
    w = r.sample(WORDS, 3)
    gaps = [r.randint(1, 3) for _ in range(2)]
    if max(gaps) < 2:
        gaps[r.randrange(2)] = 2
    text = " " * r.randint(1, 2) + w[0] + " " * gaps[0] + w[1] + " " * gaps[1] + w[2] + " "
    pre = f"{s} = {text!r}\n"
    t = pre + f"print({s}.split(\" \", 1))\nprint({s}.split(\" \"))\nprint(len({s}.split(\" \")))\n{o} = len({s}.split(\" \")) % 4 + {k}"
    v = pre + f"print({s}.split(None, 1))\nprint({s}.split())\nprint(len({s}.split()))\n{o} = len({s}.split()) % 4 + {k}"
    return t, v


def t_strip(r, N, k, o):
    s, s2 = N(), N()
    while True:
        cs = "".join(r.sample("xyzvwq", 3))
        cs2 = "".join(r.sample("xyzvwq", 3))
        c1, c2 = r.sample(WORDS, 2)
        if c1[0] in "xyzvwq" or c1[-1] in "xyzvwq" or c2[0] in "xyzvwq" or c2[-1] in "xyzvwq":
            continue
        pre = "".join(r.choice(cs) for _ in range(r.randint(2, 4)))
        suf = "".join(r.choice(cs) for _ in range(r.randint(2, 4)))
        pre2 = "".join(r.choice(cs2) for _ in range(r.randint(2, 4)))
        a, b = pre + c1 + suf, pre2 + c2 + r.choice(WORDS)[:2]
        if not a.startswith(cs) and not a.endswith(cs) and not b.startswith(cs2):
            break
    head = f"{s} = {a!r}\n{s2} = {b!r}\n"
    t = head + f"print({s}.strip({cs!r}))\nprint({s2}.lstrip({cs2!r}))\n{o} = len({s}.strip({cs!r})) % 3 + {k}"
    v = head + (f"print({s}.removeprefix({cs!r}).removesuffix({cs!r}))\nprint({s2}.removeprefix({cs2!r}))\n"
                f"{o} = len({s}.removeprefix({cs!r}).removesuffix({cs!r})) % 3 + {k}")
    return t, v


def t_chained(r, N, k, o):
    A, B = r.randint(20, 60), r.randint(6, 15)
    forms = [(f"{A} < {B} < {k}", f"({A} < {B}) < {k}"), (f"{B} < {A} > {k}", f"({B} < {A}) > {k}"),
             (f"{A} > {B} < {k}", f"({A} > {B}) < {k}"), (f"{k} < {A} > {B}", f"({k} < {A}) > {B}")]
    pick = r.sample(forms, 3)
    t = "\n".join(f"print({a})" for a, _ in pick) + f"\n{o} = {k} + {B}"
    v = "\n".join(f"print({b})" for _, b in pick) + f"\n{o} = {k} + {B}"
    return t, v


def t_sorted(r, N, k, o):
    data, n = N(), N()
    letters = r.sample("abcdefghjkmnpqrstuvwxyz", 5)
    lo, mid, hi = sorted(r.sample(range(1, 10), 3))
    keys = [lo, lo, hi, hi, mid]
    r.shuffle(keys)
    pre = f"{data} = {[(a, b) for a, b in zip(letters, keys)]!r}\n"
    t = pre + (f"print([{n} for {n}, _ in sorted({data}, key=lambda t: t[1], reverse=True)])\n"
               f"print(max({data}, key=lambda t: t[1])[0])\nprint(min({data}, key=lambda t: t[1])[0])\n"
               f"{o} = ord(max({data}, key=lambda t: t[1])[0]) % 10")
    v = pre + (f"print([{n} for {n}, _ in sorted({data}, key=lambda t: t[1])][::-1])\n"
               f"print([t for t in {data} if t[1] == max(u[1] for u in {data})][-1][0])\n"
               f"print([t for t in {data} if t[1] == min(u[1] for u in {data})][-1][0])\n"
               f"{o} = ord([t for t in {data} if t[1] == max(u[1] for u in {data})][-1][0]) % 10")
    return t, v


def t_float_repr(r, N, k, o):
    from decimal import Decimal
    while True:
        a, b = r.randint(1, 9), r.randint(1, 9)
        if repr(float(f"0.{a}") + float(f"0.{b}")) != str(Decimal(f"0.{a}") + Decimal(f"0.{b}")):
            break
    while True:
        n = r.randint(3, 10)
        if repr(sum([0.1] * n)) != str(Decimal("0.1") * n):
            break
    while True:
        c, m = r.randint(1, 9), r.randint(3, 9)
        if float(f"0.{c}") * m != float(Decimal(f"0.{c}") * m):
            break
    D = N()
    imp = "from decimal import Decimal\n"
    t = (f"print(0.{a} + 0.{b})\nprint(sum([0.1] * {n}))\nprint(0.{c} * {m} == {Decimal(f'0.{c}') * m}, 0.{c} * {m})\n"
         f"{o} = int(sum([0.1] * {n}) * 10) % 5 + {k}")
    v = (imp + f"print(Decimal(\"0.{a}\") + Decimal(\"0.{b}\"))\nprint(Decimal(\"0.1\") * {n})\n"
         f"print(Decimal(\"0.{c}\") * {m} == Decimal(\"{Decimal(f'0.{c}') * m}\"), Decimal(\"0.{c}\") * {m})\n{o} = int(Decimal(\"0.1\") * {n} * 10) % 5 + {k}")
    return t, v


def t_pow(r, N, k, o):
    B = r.choice((2, 3))
    p, q = r.randint(3, 9), r.randint(2, 9)
    t = f"print(-{k} ** 2)\nprint({B} ** 3 ** 2)\nprint({p} * {q} / {q})\n{o} = -{k} ** 2"
    v = f"print((-{k}) ** 2)\nprint(({B} ** 3) ** 2)\nprint({p} * {q} // {q})\n{o} = (-{k}) ** 2"
    return t, v


def t_tuple_iadd(r, N, k, o):
    t_, l = N(), N()
    a, b, c = r.randint(1, 9), r.randint(10, 29), r.randint(1, 9)
    head = f"{l} = [{b}]\n{t_} = ({a}, {l}, {c})\n"
    t = head + f"try:\n    {t_}[1] += [{k}]\nexcept TypeError:\n    print(\"TypeError\")\nprint({t_})\nprint({l})\n{o} = len({l})"
    v = head + f"print(\"TypeError\")\nprint({t_})\nprint({l})\n{o} = len({l})"
    return t, v


def t_comp_scope(r, N, k, o):
    x, y, sq, ws, i = N(), N(), N(), N(), N()
    A, B = r.randint(10, 99), r.randint(10, 99)
    body = (f"{x} = {A}\n{y} = {B}\n{sq} = [{x} * {x} for {x} in range({k})]\n{ws} = [({y} := {i}) * 2 for {i} in range({k})]\n")
    t = body + f"print({x})\nprint({y})\n{o} = {x} + {y}"
    v = body + f"{x} = {k} - 1\n{y} = {B}\nprint({x})\nprint({y})\n{o} = {x} + {y}"
    return t, v


def t_zip_consume(r, N, k, o):
    it, L, g = N(), "".join(r.sample("abcdefghjkm", 5)), N()
    pre = f"{it} = iter(range({k} + 10))\n"
    t = pre + (f"list(zip({it}, {L!r}[:{k}]))\nprint(next({it}))\nprint(list(zip(range({k} + 3), {L!r}[:{k}]))[-1])\n{o} = next({it})")
    v = pre + (f"from itertools import zip_longest\nlist(zip({L!r}[:{k}], {it}))\nprint(next({it}))\n"
               f"print(list(zip_longest(range({k} + 3), {L!r}[:{k}]))[-1])\n{o} = next({it})")
    return t, v


def t_enumerate(r, N, k, o):
    ws = N()
    w = r.sample(WORDS, 6)
    pre = f"{ws} = {w!r}\n"
    t = pre + f"print(list(enumerate({ws}, {k}))[1:3])\nprint(max(dict(enumerate({ws}, start={k}))))\n{o} = max(dict(enumerate({ws}, {k})))"
    v = pre + f"print(list(enumerate({ws}))[1:3])\nprint(max(dict(enumerate({ws}))))\n{o} = max(dict(enumerate({ws})))"
    return t, v


def t_star(r, N, k, o):
    a, b, c, s = N(), N(), N(), N()
    s0 = r.randint(10, 80)
    sep = r.choice("-+/;")
    rng = f"range({s0}, {s0} + {k} + 3)"
    tail = f"print({a}, {b}, {c})\nprint(*{b}, {c}, sep={sep!r})\n{o} = len({b})"
    return (f"{a}, *{b}, {c} = {rng}\n" + tail,
            f"{s} = list({rng})\n{a} = {s}[0]\n{b} = {s}[1:]\n{c} = {s}[-1]\n" + tail)


def t_none_return(r, N, k, o):
    xs, res = N(), N()
    vals = r.sample(range(10, 70), 4)
    pre = f"{xs} = {vals!r}\n"
    t = pre + f"{res} = {xs}.sort()\nprint({res}, len({xs}))\nprint({xs}.append({k}), {xs}.index({k}))\nprint({xs})\n{o} = len({xs}) + {k}"
    v = pre + f"{res} = sorted({xs})\n{xs} = {res}\nprint({res}, len({xs}))\n{xs} = {xs} + [{k}]\nprint({xs}, {xs}.index({k}))\nprint({xs})\n{o} = len({xs}) + {k}"
    return t, v


def t_default_eval(r, N, k, o):
    x, f, p = N(), N(), N()
    A, A2 = r.sample(range(5, 40), 2)
    t = f"{x} = {A}\ndef {f}({p}={x}):\n    return {p} * {k}\n{x} = {A2}\nprint({f}())\n{o} = {f}()"
    v = (f"{x} = {A}\ndef {f}({p}=None):\n    {p} = {x} if {p} is None else {p}\n    return {p} * {k}\n{x} = {A2}\nprint({f}())\n{o} = {f}()")
    return t, v


def t_count(r, N, k, o):
    s = N()
    u = "".join(r.sample("abcdefg", 2))
    word = r.choice(WORDS)
    pre = f"{s} = {u!r} * ({k} + 2) + {u[0]!r}\n"
    pat = u + u[0]
    t = pre + f"print({s}.count({pat!r}))\nprint({word!r}.count(\"\"))\n{o} = {s}.count({pat!r})"
    cnt = f"sum({s}.startswith({pat!r}, i) for i in range(len({s})))"
    v = pre + f"print({cnt})\nprint(len({word!r}))\n{o} = {cnt}"
    return t, v


def t_aug_alias(r, N, k, o):
    a, g, h, l, b = N(), N(), N(), N(), N()
    x, y = r.randint(10, 30), r.randint(31, 60)
    pre = f"{a} = [{x}, {y}]\n"
    tail = f"{g}({a})\nprint({a})\n{h}({a})\nprint({a})\n{b} = {a}\n"
    t = pre + f"def {g}({l}):\n    {l} += [{k}]\ndef {h}({l}):\n    {l} = {l} + [{k}]\n" + tail + f"{b} += [0]\nprint(len({a}) + {k})\n{o} = len({a})"
    v = pre + f"def {g}({l}):\n    {l} = {l} + [{k}]\ndef {h}({l}):\n    {l} = {l} + [{k}]\n" + tail + f"{b} = {b} + [0]\nprint(len({a}) + {k})\n{o} = len({a})"
    return t, v


TEMPLATES = {
    "closure_late": (t_closure, 2), "mutable_default": (t_mutdef, 4), "list_alias": (t_alias, 2), "slice_neg": (t_slice, 3),
    "floor_mod_neg": (t_floormod, 2), "round_half_even": (t_round, 3), "finally_order": (t_finally_order, 3),
    "finally_value": (t_finally_value, 2), "generator_exhaust": (t_generator, 3), "dict_reinsert": (t_dict_order, 3),
    "split_space": (t_split, 3), "strip_charset": (t_strip, 2), "chained_cmp": (t_chained, 3), "sort_stable_minmax": (t_sorted, 3),
    "float_repr": (t_float_repr, 3), "pow_precedence": (t_pow, 3), "tuple_iadd": (t_tuple_iadd, 3), "comp_scope": (t_comp_scope, 2),
    "zip_consume": (t_zip_consume, 2), "enumerate_start": (t_enumerate, 2), "star_unpack": (t_star, 2), "none_return": (t_none_return, 3),
    "default_eval": (t_default_eval, 1), "count_overlap": (t_count, 2), "aug_alias": (t_aug_alias, 3),
}
_ORDER = sorted(TEMPLATES)

# ---------------------------------------------------------------- sandbox runner (truth)
# The engine's bwrap sandbox (no network, private tmpfs, CPU limit). The harness runs each source twice as a child
# interpreter (PYTHONHASHSEED 0 and 123) and prints one JSON line.
_DRIVER = r'''
import base64, json, subprocess, sys
d = json.loads(base64.b64decode("@BLOB@").decode("utf-8"))
res = []
for src in d["srcs"]:
    outs = []
    for hs in ("0", "123"):
        env = {"PYTHONHASHSEED": hs, "PATH": "/usr/bin"}
        try:
            p = subprocess.run([sys.executable, "-B", "-W", "ignore", "-c", src], capture_output=True, text=True, timeout=5, env=env)
            outs.append([p.returncode, p.stdout, p.stderr[-200:]])
        except subprocess.TimeoutExpired:
            outs.append([-9, "", "timeout"])
    res.append(outs)
sys.stdout.write("\nBENCH_PYTRAP:" + json.dumps(res) + "\n")
'''


def _run_srcs(srcs):
    import resource
    from .. import graders as _g
    _g.require_sandbox()
    blob = base64.b64encode(json.dumps({"srcs": srcs}).encode("utf-8")).decode("ascii")
    lim = int(CPU_S) + 1

    def pre():
        resource.setrlimit(resource.RLIMIT_CPU, (lim, lim + 1))
        resource.setrlimit(resource.RLIMIT_AS, (2 << 30, 2 << 30))
    with tempfile.TemporaryDirectory(prefix="bench-pytrap-") as td:
        path = Path(td) / "harness.py"
        path.write_text(_DRIVER.replace("@BLOB@", blob), encoding="utf-8")
        p = subprocess.run(_g._sandbox_cmd(str(path), td), capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=CPU_S * 4 + 30, cwd=td, env=_g._child_env(), preexec_fn=pre)
    line = next((ln for ln in reversed(p.stdout.splitlines()) if ln.startswith("BENCH_PYTRAP:")), None)
    if line is None:
        raise RuntimeError("pytrap sandbox failed: " + (p.stderr or f"rc={p.returncode}")[-300:])
    out = []
    for (rc0, o0, _e0), (rc1, o1, _e1) in json.loads(line[len("BENCH_PYTRAP:"):]):
        if rc0 != 0 or rc1 != 0 or o0 != o1:
            return None
        out.append(o0.split("\n")[:-1] if o0.endswith("\n") else o0.split("\n"))
    return out


def _build(seed, tier, j, attempt):
    r = random.Random(f"pytrap:{seed}:{tier}:{j}:{attempt}")
    lo, hi = LINES[tier]
    while True:
        nb = r.randint(*NBLOCKS[tier])
        pick = r.sample(_ORDER, nb)
        if lo <= sum(TEMPLATES[p][1] for p in pick) <= hi:
            break
    N = Names(r)
    tsrc, nsrc, tags, prev = [], [], [], None
    for bi, name in enumerate(pick):
        k = str(r.randint(2, 5)) if (tier == 1 or prev is None) else f"({prev} % 4 + 2)"
        o = f"v{bi + 1}"
        t, v = TEMPLATES[name][0](r, N, k, o)
        tsrc.append(t)
        nsrc.append(v)
        tags += [name] * TEMPLATES[name][1]
        prev = o
    return "\n".join(tsrc) + "\n", "\n".join(nsrc) + "\n", tags, pick


@functools.lru_cache(maxsize=None)
def _snippet(seed, tier, j):
    best = None
    for attempt in range(80):
        t, v, tags, pick = _build(seed, tier, j, attempt)
        res = _run_srcs([t, v])
        if res is None:
            continue
        tl, nl = res
        if len(tl) != len(tags) or len(nl) != len(tags) or any((not x) or x.startswith("###") for x in tl) or any(not x for x in nl):
            continue
        diff = [a != b for a, b in zip(tl, nl)]
        frac = sum(diff) / len(diff)
        if frac >= 0.7 or (attempt >= 50 and frac >= 0.5):
            best = {"id": f"S{j}", "code": t, "true": tl, "naive": nl, "tags": tags, "diff": diff, "traps": pick}
            break
    if best is None:
        raise RuntimeError(f"no snippet for seed {seed} tier {tier} j {j}")
    return best


# Cheap-strategy table (ff red team): for (trap template, k-th line of that template) the most common true line over 400
# snippets. baseline = this table where it has an entry, else the naive reading; it anchors the 30 points.
CONST = {
 "chained_cmp:0": "True",
 "chained_cmp:1": "False",
 "chained_cmp:2": "False",
 "generator_exhaust:1": "0",
 "tuple_iadd:0": "TypeError",
 "none_return:0": "None 4",
 "none_return:1": "None 4",
 "pow_precedence:1": "512",
 "count_overlap:0": "3",
 "count_overlap:1": "7",
 "float_repr:1": "0.6000000000000001",
 "enumerate_start:1": "7"
}


def _baseline_lines(s):
    pos, lines = {}, []
    for tag, nv in zip(s["tags"], s["naive"]):
        i = pos.get(tag, 0)
        pos[tag] = i + 1
        lines.append(CONST.get(f"{tag}:{i}", nv))
    return lines


def _fmt(snips, key):
    return "\n".join(f"### {s['id']}\n" + "\n".join(s[key]) for s in snips) + "\n"


_HDR_TEXT = ("For each Python program below, give the lines it prints, under headers `### S1`, `### S2`, ... "
             "(the line count is in each title).\n")


def make_instance(seed: int, tier: int, n: int = DEFAULT_N) -> dict:
    snips = [_snippet(seed, tier, j) for j in range(1, n + 1)]
    parts = [_HDR_TEXT]
    for s in snips:
        parts.append(f"### {s['id']} (prints {len(s['true'])} lines)\n```python\n{s['code']}```\n")
    return {"prompt": "\n".join(parts), "public": {"n": len(snips), "tier": tier}, "hidden": {"snips": snips}}


# ---------------------------------------------------------------- grading (outside the sandbox; the reply is trusted for nothing
# but the predicted lines)
_HDR = re.compile(r"^[#*>\s`_-]*(?:S|snippet\s*)(\d+)\b[^\w(]*(\(.*\))?[^\w]*$", re.I)


def parse(reply: str) -> dict:
    """{'S1': [lines]}: header lines `### S1` / `S1:` / `**S1**` / `Snippet 1`; fence lines and blank lines dropped; last occurrence wins."""
    secs, cur = {}, None
    for raw in (reply or "").replace("\r", "").split("\n"):
        line = raw.rstrip()
        m = _HDR.match(line)
        if m:
            cur = f"S{int(m.group(1))}"
            secs[cur] = []
            continue
        if cur is None or line.strip().startswith("```") or not line.strip():
            continue
        secs[cur].append(line)
    return secs


#: C2 fairness pass (2026-11-14): checks that carry no weight. ("float_repr", 1) = the repr of sum([0.1] * n), which
#: asks for IEEE-754 accumulation to 17 digits rather than reading: on 1,400 stored rows it inverted (Grok .69 > Haiku
#: .14, MiniMax .25). The line stays in the reply (positions do not shift); it is graded with weight 0.
ZERO_WEIGHT = {("float_repr", 1)}


def line_weights(tags: list) -> list:
    """weight per printed line: 0 for the (trap, k-th line of that trap block) pairs in ZERO_WEIGHT, else 1"""
    pos, out = {}, []
    for tag in tags:
        i = pos.get(tag, 0)
        pos[tag] = i + 1
        out.append(0.0 if (tag, i) in ZERO_WEIGHT else 1.0)
    return out


def _judge(snips: list, reply: str) -> dict:
    """snips = [{id, true, tags}] -> {valid, q, results:[{id, pass, weight, kind, detail}]}; one check per printed line.
    A predicted line is compared with leading whitespace stripped on both sides (a reply indented
    under its header is not wrong). q = weighted share (see ZERO_WEIGHT)."""
    secs = parse(reply)
    results = []
    for s in snips:
        got = secs.get(s["id"], [])
        ws = line_weights(s["tags"])
        for i, t in enumerate(s["true"]):
            ok = i < len(got) and got[i].lstrip() == t.lstrip()
            results.append({"id": f"{s['id']}-L{i + 1}", "pass": ok, "weight": ws[i], "kind": s["tags"][i],
                            "detail": ("ok" if ok else "wrong" if i < len(got) else "missing") + f" ({s['tags'][i]})"})
    valid = any(secs.values())
    tw = sum(r["weight"] for r in results)
    q = sum(r["weight"] for r in results if r["pass"]) / tw if tw else 0.0
    return {"valid": valid, "q": q, "results": results}


def check(inst, reply):
    j = _judge(inst["hidden"]["snips"], reply)
    return {"q": j["q"], "valid": j["valid"], "cpu_s": 0.0,
            "note": "" if j["valid"] else "no parsable snippet blocks"}


def points(q: float, q_base: float, valid: bool = True, q_ref: float = 1.0) -> float:
    if not valid:
        return 0.0
    if q_ref <= q_base:
        return POINTS_BASE
    return round(max(POINTS_FLOOR, POINTS_BASE + (POINTS_REF - POINTS_BASE) * (q - q_base) / (q_ref - q_base)), 1)


def baseline(inst):
    return "\n".join(f"### {s['id']}\n" + "\n".join(_baseline_lines(s)) for s in inst["hidden"]["snips"]) + "\n"


def naive_reading(inst):
    return _fmt(inst["hidden"]["snips"], "naive")


def reference_text(inst):
    return _fmt(inst["hidden"]["snips"], "true")


# ---------------------------------------------------------------- engine adapter
def _seed_for(gen: dict, release_seed: str, case_id: str) -> int:
    if gen.get("ffseed") is not None:
        return int(gen["ffseed"])
    return 1 + _gen.instance_int(FAMILY, release_seed, case_id, gen.get("level"), gen.get("key"))


def _inst_of(gen: dict, release_seed: str, case_id: str) -> dict:
    lv = int(gen.get("level", 1))
    if lv not in LEVELS:
        raise ValueError(f"pytrap: no level {lv} (levels {sorted(LEVELS)})")
    n = int(gen.get("n", DEFAULT_N))
    if n < 1:
        raise ValueError("pytrap: n must be >= 1")
    return make_instance(_seed_for(gen, release_seed, case_id), LEVELS[lv]["tier"], n)


def materialize(gen: dict, *, release_seed: str, case_id: str) -> dict:
    lv = int(gen.get("level", 1))
    inst = _inst_of(gen, release_seed, case_id)
    snips = inst["hidden"]["snips"]
    # the grader needs only the expected lines (computed above by running the snippets), the trap tags and the
    # baseline anchor; carrying them makes grading a pure function of (config, reply)
    cfg_snips = [{"id": s["id"], "true": s["true"], "tags": s["tags"]} for s in snips]
    qb = _judge(cfg_snips, baseline(inst))["q"]
    return json.loads(json.dumps({
        "prompt": inst["prompt"], "temperature": 0.0, "max_tokens": MAX_TOKENS,
        "grader": "pytrap",
        "grader_config": {"family": FAMILY, "tier": LEVELS[lv]["tier"], "snips": cfg_snips, "anchor_q": qb},
        "meta": {"tier": inst["public"]["tier"], "n_snippets": len(snips),
                 "n_lines": sum(len(s["true"]) for s in snips), "traps": [s["traps"] for s in snips]},
        "difficulty": "hard"}))


def reference(gen: dict, *, release_seed: str, case_id: str) -> str:
    return reference_text(_inst_of(gen, release_seed, case_id))


def mutants(gen: dict, *, release_seed: str, case_id: str) -> list[str]:
    inst = _inst_of(gen, release_seed, case_id)
    return [naive_reading(inst), baseline(inst), ""]


GRADE_IS_ENGINE = True


def grade_pytrap(response_text: str, cfg: dict, finish_reason=None) -> dict:
    """Engine grader "pytrap": cfg = {snips:[{id,true,tags}], anchor_q}. ``results`` = one entry per printed line."""
    j = _judge(cfg["snips"], response_text or "")
    if not j["valid"]:
        return {"grade": "fail", "detail": "no parsable snippet blocks", "score": 0.0, "rate": 0.0, "points": 0.0,
                "q": 0.0, "results": [dict(r, **{"pass": False}) for r in j["results"]]}
    qb = float(cfg["anchor_q"])
    pts = points(j["q"], qb)
    s = round(min(1.0, pts / POINTS_REF), 4)
    nfail = sum(1 for r in j["results"] if not r["pass"])
    return {"grade": "pass" if s >= 0.999 else "fail",
            "detail": f"{j['q']:.3f} of the printed lines right (anchor {qb:.3f}); {pts} points; {nfail} wrong",
            "score": s, "rate": s, "points": pts, "q": j["q"], "anchor_q": qb, "results": j["results"]}


def grade(answer: str, materialized: dict) -> float:
    return float(grade_pytrap(answer, materialized["grader_config"])["score"])
