"""solversyn (T2 2b, FC2 Search): write a PROGRAM that searches a rewrite calculus for short terms.

A port of the fast-fail question SOLVERSYN graded tier 5 (`wt-ff/ff/q/solversyn.py`, build/FASTFAIL-RESULTS.md, SOLVERSYN
section). An instance is a calculus of 5 constants (arity 1-3, one rewrite rule each) and 18 targets (results over a, b, c)
whose certified minimum term sizes are 5 / 6 / 7 / 8 (2 / 3 / 5 / 8 targets). The reply is a Python module
`solve(rules, targets, limits) -> {target id: term}` run in a bwrap sandbox for 10 CPU seconds; each returned term T is
checked outside the sandbox by an independent evaluator: `T a b c` must normalise to exactly the target result (leftmost-
outermost, 2000 steps, 4000 symbols) with size <= min (credit 1.0) or min + 1 (credit 0.5). The instance score q is the
mean credit weighted by (min size - 3), so the size-8 rung carries 40%. The size-8 rung needs a smarter search than the
obvious enumerator (about 80M candidate pairs at size 8, 10 CPU s).

Level = the fast-fail "tier": only 5 is frozen. Instance seed = `gen["ffseed"]` (3, 4, 6 are the cached calculi in
`_solversyn_t5.json`, built offline in about 12 min each by `python -m crucibleforge.gen.solversyn --build <seed>`);
other seeds are refused at generation time (the offline build is far too slow to run in a case load).

Scoring (family `grade`, engine grader "solversyn"): points = 0 if the reply is invalid or over its CPU limit, else
max(10, 30 + 70 * (q - q_base) / (1 - q_base)), score = min(1, points / 100). q_base is the generic enumerator's q on
the instance (`Q_BASE`, measured in tests/: the same solver written out as BASE below, which solves sizes 5-6 only);
fixed per instance so generation stays deterministic.
"""
from __future__ import annotations

import functools
import inspect
import json
import random
import subprocess
import sys
import tempfile
from pathlib import Path

from . import _combsyn as C

FAMILY = "solversyn"

CPU_S = 10.0
MAX_STEPS = 2000
SIZES = {5: 2, 6: 3, 7: 5, 8: 8}                 # targets per certified minimum size (tier 5)
LEVELS = {5: dict(cpu_s=CPU_S, answer_tokens=2500)}
# answer_tokens = a typical solver reply (the real ones are 5-10k characters of code after 30-85k tokens of thinking)
MAX_TOKENS = 24576
DATA = Path(__file__).with_name("_solversyn_t5.json")
# generic enumerator's q per ffseed (13/73, 13/73, 17/73 in the weights below; measured through this grader)
Q_BASE = {3: 13 / 73, 4: 13 / 73, 6: 17 / 73}

_POOL = [(1, "x"), (2, "x"), (2, "y"), (2, "y x"), (2, "x y y"), (3, "x (y z)"), (3, "x z y"), (3, "x z (y z)"), (3, "z x y"),
         (3, "y z x"), (3, "y (x z)"), (3, "z y x"), (3, "x y z y"), (3, "z (x y)"), (3, "x z"), (3, "y x z"), (3, "x (z y)"),
         (3, "y z (x z)")]
_NAMES = "DEFGHJLNPQRUVXZ"


@functools.lru_cache(maxsize=1)
def _data() -> dict:
    return json.loads(DATA.read_text(encoding="utf-8"))


def _weight(m: int) -> int:
    return m - 3


def instance(seed: int, level: int = 5) -> dict:
    if int(level) != 5:
        raise ValueError(f"solversyn: only level 5 is frozen (got {level})")
    d = _data().get(f"5:{int(seed)}")
    if d is None:
        raise ValueError(f"solversyn: no cached calculus for ffseed {seed} (cached: "
                         f"{sorted(int(k[2:]) for k in _data())}); build one offline with --build (about 12 min)")
    pub = {"rules": d["rules"],
           "targets": [{"id": t["id"], "result": t["result"], "max_size": t["min"]} for t in d["targets"]],
           "limits": {"cpu_s": CPU_S, "max_steps": MAX_STEPS}}
    return {"public": pub, "targets": {t["id"]: (t["result"], t["min"]) for t in d["targets"]},
            "refs": {t["id"]: t["ref"] for t in d["targets"]}, "prompt": _prompt()}


def _toy():
    rules = {"M": (3, C._to_idx(C.parse("y (x z)", "xyz"))), "N": (2, C._to_idx(C.parse("y x", "xyz"))),
             "P": (1, C._to_idx(C.parse("x x", "xyz")))}
    term = C.parse("M N (N P)", set(rules))
    cur, tr = (((term, "a"), "b"), "c"), []
    while True:
        tr.append(C.show(cur))
        cur, ch = C.step(cur, rules)
        if not ch:
            break
    return C.struct_from_rules(rules), "M N (N P)", tr


def _prompt() -> str:
    rl, term, tr = _toy()
    rtxt = "\n".join(f"    {r['name']} " + " ".join("xyz"[:r["arity"]]) + f"  ->  {r['body']}" for r in rl)
    return f"""Write a Python program (one module, standard library only) that searches a small rewriting calculus for short terms.

THE CALCULUS
- A term is a constant, or one term applied to another. Constants are single uppercase letters. Application is written by putting two terms next to each other and groups to the left: `F G H` means `(F G) H`. Parentheses group; spaces are optional.
- Every constant has an arity (1, 2 or 3) and one rewrite rule, written with the argument placeholders x, y, z (the first `arity` of them), for example `{rl[0]['name']} {' '.join('xyz'[:rl[0]['arity']])}  ->  {rl[0]['body']}`. A rule can be used on any subterm in which the constant is followed by at least `arity` argument terms: the constant and its first `arity` arguments are replaced by the right-hand side (each placeholder replaced by the argument it stands for, one copy for each occurrence); any further arguments stay after the result.
- The lowercase symbols a, b, c have no rule and are never part of a term you return. A term is finished when no rule can be used anywhere in it. Rewriting always takes the leftmost-outermost usable position first. Each use of a rule is one step. A rewrite run fails if it needs more than {MAX_STEPS} steps or if the term during the run ever has more than 4000 symbols.
- The size of a term is the number of constant occurrences in it (a, b, c count nothing in a term you return).
- Worked example. Rules:
{rtxt}
  The term `{term}` has size 4. `T a b c` with that T rewrites step by step as
    {(chr(10) + "    -> ").join(tr)}
  so `{term}` solves the target result `{tr[-1]}`.

THE INTERFACE
Your module must define `solve(rules, targets, limits)`; it is called once per instance.
- `rules`: a list of dicts, one per constant, e.g. `{{"name": "M", "arity": 3, "body": "y (x z)"}}`. The body is a term over the placeholders x, y, z written in the notation above.
- `targets`: a list of dicts `{{"id": "t01", "result": "b (a c)", "max_size": 5}}`. `result` is a finished term over a, b, c, written canonically: symbols separated by single spaces, parentheses around every argument that is itself an application, no outer parentheses. `max_size` is the smallest size at which any term can reach the result, so no valid term is smaller.
- `limits`: `{{"cpu_s": {CPU_S:g}, "max_steps": {MAX_STEPS}}}`.
- Return a dict mapping each target id to a term string (constants only, in the notation above) T such that `T a b c` rewrites to exactly `result` within the limits above. For this, T is applied to a, b, c in turn: `T a b c` means `((T a) b) c`. Targets you cannot solve may be left out.

SCORING
- Each target scores 1.0 if your term is correct and its size is at most `max_size`, 0.5 if it is correct and its size is `max_size + 1`, otherwise 0. Your score is the mean over all targets of the instance (missing or wrong = 0). The terms are checked by an independent trusted evaluator.
- An instance has about {sum(SIZES.values())} targets with `max_size` between {min(SIZES)} and {max(SIZES)}, and 5 constants. Your program is run on several different calculi, one process each, with no network, one CPU core, Python 3 standard library only, and a hard limit of {CPU_S:g} CPU seconds for the whole process (interpreter start included; process CPU time, not wall time). If it crashes, returns something that is not a dict, or goes over the CPU limit, that instance scores 0 and nothing you computed counts, so return in time: `limits["cpu_s"]` is given so your program can watch its own `time.process_time()`.
- Your whole reply is cut off at 20,000 tokens. Reply with the complete module in a single ```python block and nothing that has to be run besides it."""


def score_terms(inst: dict, out: dict) -> tuple[float, dict]:
    """q (size-weighted mean credit) and per-target (min size, credit); the trusted evaluator runs outside the sandbox."""
    rules = C.rules_from_struct(inst["public"]["rules"])
    names, detail = set(rules), {}
    for tid, (want, m) in inst["targets"].items():
        cr, txt = 0.0, out.get(tid)
        try:
            if isinstance(txt, str) and len(txt) <= 400:
                t = C.parse(txt, names)
                sz = C.size(t)
                if sz <= m + 1 and C.show(C.norm((((t, "a"), "b"), "c"), rules, MAX_STEPS)) == want:
                    cr = 1.0 if sz <= m else 0.5
        except (ValueError, RuntimeError, RecursionError):
            pass
        detail[tid] = (m, cr)
    return sum(_weight(m) * c for m, c in detail.values()) / sum(_weight(m) for m, _ in detail.values()), detail


DRIVER = """import sys, json, time, types
_src = {src!r}
_d = json.loads({data!r})
_m = types.ModuleType("solver")
sys.modules["solver"] = _m
try:
    exec(compile(_src, "solver.py", "exec"), _m.__dict__)
    _r = _m.solve(_d["rules"], _d["targets"], _d["limits"])
    if not isinstance(_r, dict):
        raise TypeError("solve() did not return a dict")
    print("BENCH_SYN:" + json.dumps([{{str(k): v for k, v in _r.items()}}, time.process_time()]))
except BaseException as _e:
    print("BENCH_SYN_ERR:" + (type(_e).__name__ + ": " + str(_e))[:200].replace("\\n", " "))
"""


def _run(code: str, inst: dict, cpu_s: float) -> dict:
    import resource
    from .. import graders as _g
    if not code.strip():
        return {"valid": False, "note": "no code in response", "cpu_s": 0.0}
    _g.require_sandbox()
    pub = dict(inst["public"], limits={"cpu_s": cpu_s, "max_steps": MAX_STEPS})
    harness = DRIVER.format(src=code, data=json.dumps(pub, separators=(",", ":")))
    lim = int(cpu_s) + 1

    def pre():
        resource.setrlimit(resource.RLIMIT_CPU, (lim, lim + 1))
        resource.setrlimit(resource.RLIMIT_AS, (2 << 30, 2 << 30))
    with tempfile.TemporaryDirectory(prefix="bench-syn-") as td:
        path = Path(td) / "harness.py"
        path.write_text(harness, encoding="utf-8")
        try:
            p = subprocess.run(_g._sandbox_cmd(str(path), td), capture_output=True, text=True, encoding="utf-8",
                               errors="replace", timeout=cpu_s * 4 + 30, cwd=td, env=_g._child_env(), preexec_fn=pre)
            out, rc, err = p.stdout, p.returncode, p.stderr[-300:]
        except subprocess.TimeoutExpired:
            return {"valid": False, "note": "wall timeout", "cpu_s": cpu_s * 4}
    line = next((ln for ln in reversed(out.splitlines()) if ln.startswith("BENCH_SYN")), None)
    if line is None or line.startswith("BENCH_SYN_ERR:"):
        why = line[len("BENCH_SYN_ERR:"):] if line else (err or f"rc={rc}")[-200:]
        return {"valid": False, "note": "crash: " + why.strip()[:200], "cpu_s": 0.0}
    try:
        res, cpu = json.loads(line[len("BENCH_SYN:"):])
    except ValueError:
        return {"valid": False, "note": "unreadable output", "cpu_s": 0.0}
    if cpu > cpu_s:
        return {"valid": False, "note": f"over CPU limit ({cpu:.1f}s > {cpu_s:g}s)", "cpu_s": cpu}
    return {"valid": True, "out": res, "cpu_s": cpu}


def points(q, q_base, valid=True) -> float:
    if not valid:
        return 0.0
    return round(max(10.0, 30.0 + 70.0 * (q - q_base) / (1.0 - q_base)), 1)


def grade_solversyn(response_text: str, cfg: dict, finish_reason=None) -> dict:
    """Engine grader "solversyn": cfg = {family, seed, level, cpu_s}."""
    from .. import graders as _g
    inst = instance(cfg["seed"], cfg["level"])
    r = _run(_g.extract_python(response_text or "", finish_reason), inst, float(cfg.get("cpu_s", CPU_S)))
    if not r["valid"]:
        return {"grade": "fail", "detail": r["note"], "score": 0.0, "rate": 0.0, "points": 0.0, "cpu_s": r["cpu_s"]}
    q, detail = score_terms(inst, r["out"])
    qb = Q_BASE[int(cfg["seed"])]
    pts = points(q, qb)
    by = {}
    for m, c in detail.values():
        by.setdefault(m, [0, 0.0])
        by[m][0] += 1
        by[m][1] += c
    s = round(min(1.0, pts / 100.0), 4)
    return {"grade": "pass" if s >= 0.999 else "fail",
            "detail": f"q {q:.3f} (baseline {qb:.3f}); {pts} points; credit by min size "
                      + str({m: f"{c:g}/{k}" for m, (k, c) in sorted(by.items())}) + f"; cpu {r['cpu_s']:.1f}s",
            "score": s, "rate": s, "points": pts, "q": q, "cpu_s": r["cpu_s"]}


def materialize(gen: dict, *, release_seed: str, case_id: str) -> dict:
    level = int(gen.get("level", 5))
    if gen.get("ffseed") is None:
        raise ValueError("solversyn: set generator.ffseed (3, 4 or 6; the cached calculi)")
    seed = int(gen["ffseed"])
    inst = instance(seed, level)
    return {"prompt": inst["prompt"], "temperature": 0.0, "max_tokens": MAX_TOKENS, "grader": "solversyn",
            "grader_config": {"family": FAMILY, "seed": seed, "level": level, "cpu_s": CPU_S},
            "meta": {"targets": len(inst["targets"]), "sizes": SIZES, "q_base": round(Q_BASE[seed], 4)}}


def reference(gen: dict, *, release_seed: str, case_id: str) -> str:
    inst = instance(int(gen["ffseed"]), int(gen.get("level", 5)))
    return ("```python\ndef solve(rules, targets, limits):\n    return " + json.dumps(inst["refs"]) + "\n```")


def mutants(gen: dict, *, release_seed: str, case_id: str) -> list[str]:
    return ["```python\ndef solve(rules, targets, limits):\n    return {}\n```",
            "```python\ndef solve(rules, targets, limits):\n    return {t['id']: rules[0]['name'] for t in targets}\n```",
            "```python\ndef solve(rules, targets, limits):\n    return None\n```"]


GRADE_IS_ENGINE = True


def grade(answer: str, materialized: dict) -> float:
    return float(grade_solversyn(answer, materialized["grader_config"])["score"])


# ---- generic baseline (S0 anchor; not used by grading): size-ordered enumeration deduplicated by normal form

_LIB = "\n\n".join(inspect.getsource(f) for f in (C.parse, C.size, C.subst, C.step, C.norm, C.show, C._to_idx)) + '''

import time


class _Stop(Exception):
    pass


def _setup(rules, targets, limits):
    R = {r["name"]: (int(r["arity"]), _to_idx(parse(r["body"], "xyz"))) for r in rules}
    want = {t["result"]: t["id"] for t in targets}
    t0, lim = time.process_time(), limits["cpu_s"] * 0.85
    ticks = [0]

    def tick():
        ticks[0] += 1
        if ticks[0] % 256 == 0 and time.process_time() - t0 > lim:
            raise _Stop()
    return R, want, tick


def _probe(t, R, want, ans, tick, wit=None, steps=100):
    tick()
    try:
        r = norm((((t, "a"), "b"), "c"), R, steps, 400)
    except RuntimeError:
        return
    k = show(r)
    if k in want and want[k] not in ans:
        ans[want[k]] = show(t if wit is None else wit)
'''
BASE = _LIB + '''
def solve(rules, targets, limits):
    R, want, tick = _setup(rules, targets, limits)
    ans, by, top = {}, {1: sorted(R)}, max(t["max_size"] for t in targets) + 1
    try:
        for n in by[1]:
            _probe(n, R, want, ans, tick)
        for sz in range(2, top + 1):
            by[sz] = []
            for i in range(1, sz):
                for f in by[i]:
                    for x in by[sz - i]:
                        t = (f, x)
                        by[sz].append(t)
                        _probe(t, R, want, ans, tick)
            if len(ans) == len(targets):
                break
    except _Stop:
        pass
    return ans
'''


def _build(seed: int):
    """Offline: find and cache the tier-5 calculus for `seed` (about 12 min). Same search as the fast-fail build_instance."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    for attempt in range(80):
        rng = random.Random(f"solversyn:{seed}:5:{attempt}")
        while True:
            picks = rng.sample(_POOL, 5)
            if sum(1 for a, _ in picks if a == 3) >= 2:
                names = rng.sample(_NAMES, 5)
                rules = {n: (a, C._to_idx(C.parse(b, "xyz"))) for n, (a, b) in zip(names, picks)}
                break
        st = {}
        C.enumerate_behaviours(rules, 6, st)
        if st["pairs"][6] < 380000:
            continue
        st7 = {}
        C.enumerate_behaviours(rules, 7, st7)
        if st7["pairs"][7] < 6_000_000:
            continue
        st = {}
        beh = C.enumerate_behaviours(rules, 8, st)
        if st["pairs"][7] < 6_000_000:
            continue
        by = {}
        for shown, (m, wit) in beh.items():
            if C.interesting(shown):
                by.setdefault(m, []).append((shown, C.show(wit)))
        if all(len(by.get(m, [])) >= k for m, k in SIZES.items()):
            break
    else:
        raise RuntimeError("no calculus found")
    tg = []
    for m, k in SIZES.items():
        tg += [{"result": s, "min": m, "ref": w} for s, w in rng.sample(sorted(by[m]), k)]
    rng.shuffle(tg)
    for i, t in enumerate(tg, 1):
        t["id"] = f"t{i:02d}"
    return {"rules": C.struct_from_rules(rules), "targets": tg, "pairs": st["pairs"],
            "nodes": {k: v for k, v in st.items() if k != "pairs"}}


if __name__ == "__main__" and "--build" in sys.argv:
    s = int(sys.argv[sys.argv.index("--build") + 1])
    d = json.loads(DATA.read_text(encoding="utf-8"))
    d[f"5:{s}"] = _build(s)
    DATA.write_text(json.dumps(d, indent=0), encoding="utf-8")
    print("built", s)
