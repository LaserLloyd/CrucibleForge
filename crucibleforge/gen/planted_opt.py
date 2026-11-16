"""planted_opt (T1 1d, MC4 Solve): write a solver for a weighted assignment problem with hidden structure.

A port of the fast-fail question PLANTED-OPT v2 (`wt-ff/ff/q/planted_opt2.py`, build/FASTFAIL-RESULTS.md #1). An
instance has n variables over 0..4 and a list of cost terms. Hidden under the data: `n - f` hard terms are mod-5 linear
equations over three variables each (a table is 0 where the equation holds, 40 where it does not), so the satisfying
assignments form an affine subspace with f free dimensions; soft unary and pair terms add random costs 0..6. The reply
is a Python module `solve(n, k, terms) -> list` run in a bwrap sandbox on one CPU for 15 CPU seconds. Nothing in the
prompt names the structure; it has to be noticed in the data (the prompt shows seven terms).

Level = the fast-fail "tier": 2 (n=120, f=12) and 4 (n=400, f=30), probed. Instance seed = `seed * 7919 + level` where
`seed` is `gen["ffseed"]` when given (this reproduces the fast-fail instances exactly: ffseed 1..4 at level 4), else a
64-bit int from `gen.instance_int`.

Scoring (family `grade`, engine grader "optimize"): cost of the returned assignment, computed outside the sandbox.
points = 0 if the reply is invalid or over its CPU limit, else max(10, 30 + 70 * (cost_base - cost) / (cost_base -
cost_ref)); score = min(1, points / 100). cost_ref is an annealer over the free dimensions (strong, not proven
optimal). cost_base is an anchor fixed per instance: cost_ref + BASE_FRAC * (cost_rand - cost_ref), cost_rand = a seeded
random assignment. BASE_FRAC is set so the generic time-boxed annealer (BASE below) lands near 30 points; it is
measured in tests/ and build/T1-MAIN-CODING.md, not run at generation time (generation must be deterministic).
Generation costs 1-6 s (the reference annealer); it is cached per (seed, level) in-process.
"""
from __future__ import annotations

import functools
import json
import math
import os
import random
import subprocess
import tempfile
import time
from pathlib import Path

from .. import gen as _gen

FAMILY = "planted_opt"

K = 5
P = 40
CPU_S = 15.0
BASE_FRAC = 0.077
CFG = {2: (120, 12), 4: (400, 30)}      # level -> (n, target free dims)
LEVELS = {
    2: dict(n=120, free_dims=12, cpu_s=CPU_S, answer_tokens=1500),
    4: dict(n=400, free_dims=30, cpu_s=CPU_S, answer_tokens=1500),
}
# answer_tokens = a typical solver reply (the reference answer returns the planted assignment as a literal, which is
# shorter than any honest solver)
MAX_TOKENS = 24576


def _seed_for(gen: dict, release_seed: str, case_id: str) -> int:
    if gen.get("ffseed") is not None:
        return int(gen["ffseed"])
    return _gen.instance_int(FAMILY, release_seed, case_id, gen["level"], gen.get("key")) % 1000003


def _make(seed: int, level: int) -> dict:
    n, f = CFG[level]
    rng = random.Random(seed * 7919 + level)
    xs = [rng.randrange(K) for _ in range(n)]
    m = n - f
    hard, eqs = [], []
    for _ in range(m):
        vs = rng.sample(range(n), 3)
        co = [rng.randint(1, 4) for _ in range(3)]
        d = sum(c * xs[v] for c, v in zip(co, vs)) % K
        eqs.append((vs, co, d))
        tab = [[[0 if (co[0] * a + co[1] * b + co[2] * c) % K == d else P for c in range(K)] for b in range(K)] for a in range(K)]
        hard.append({"vars": vs, "table": tab})
    soft = []
    for v in range(n):
        soft.append({"vars": [v], "table": [rng.randint(0, 6) for _ in range(K)]})
    for _ in range(n):
        i, j = rng.sample(range(n), 2)
        soft.append({"vars": [i, j], "table": [[rng.randint(0, 6) for _ in range(K)] for _ in range(K)]})
    ref_cost, f_act, ref_x = _reference(n, eqs, soft, rng)
    terms = hard + soft
    rng.shuffle(terms)
    rnd = random.Random(seed + 13)
    rand_cost = cost_of(terms, [rnd.randrange(K) for _ in range(n)])
    h3 = [t for t in terms if len(t["vars"]) == 3][:4]
    s2 = [t for t in terms if len(t["vars"]) == 2][:3]
    ex = "\n".join(f"terms[{terms.index(t)}] vars={t['vars']} table={json.dumps(t['table'], separators=(',', ':'))}"
                   for t in h3 + s2)
    prompt = (PROMPT.replace("@N@", str(n)).replace("@T@", str(len(terms)))
              .replace("@CPU@", f"{CPU_S:g}").replace("@EX@", ex))
    return {"prompt": prompt, "n": n, "terms": terms, "ref_cost": ref_cost, "rand_cost": rand_cost,
            "free_dims": f_act, "ref_x": ref_x}


_CACHE: dict = {}


def instance(seed: int, level: int) -> dict:
    key = (int(seed), int(level))
    if key not in _CACHE:
        if len(_CACHE) > 8:
            _CACHE.clear()
        _CACHE[key] = _make(*key)
    return _CACHE[key]


def cost_base(inst: dict) -> float:
    return inst["ref_cost"] + BASE_FRAC * (inst["rand_cost"] - inst["ref_cost"])


def cost_of(terms, x):
    c = 0
    for t in terms:
        tb = t["table"]
        for v in t["vars"]:
            tb = tb[x[v]]
        c += tb
    return c


def violated(terms, x):
    c = 0
    for t in terms:
        if len(t["vars"]) == 3:
            tb = t["table"]
            for v in t["vars"]:
                tb = tb[x[v]]
            c += tb > 0
    return c


def _reference(n, eqs, soft, rng):
    # Gaussian elimination mod 5 (rows: dict col->coef, rhs)
    rows = [[0] * n + [d] for vs, co, d in eqs]
    for r, (vs, co, d) in zip(rows, eqs):
        for v, c in zip(vs, co):
            r[v] = (r[v] + c) % K
    piv = {}
    for r in rows:
        for col, p in piv.items():                      # reduce by existing pivots
            if r[col]:
                c = r[col]
                r[:] = [(a - c * b) % K for a, b in zip(r, p)]
        col = next((j for j in range(n) if r[j]), None)
        if col is None:
            continue
        inv = pow(r[col], -1, K)
        r[:] = [(a * inv) % K for a in r]
        for c2, p in piv.items():                       # keep fully reduced
            if p[col]:
                c = p[col]
                p[:] = [(a - c * b) % K for a, b in zip(p, r)]
        piv[col] = r
    free = [j for j in range(n) if j not in piv]
    sof = [[] for _ in range(n)]
    for t, s in enumerate(soft):
        for v in s["vars"]:
            sof[v].append(t)
    # x_p = rhs_p - sum_f R[p][f]*y_f
    sup = {j: [(p, piv[p][j]) for p in piv if piv[p][j]] for j in free}
    x = [0] * n
    for p, r in piv.items():
        x[p] = r[n]

    def tcost(t):
        tb = soft[t]["table"]
        for v in soft[t]["vars"]:
            tb = tb[x[v]]
        return tb
    total = sum(tcost(t) for t in range(len(soft)))
    best_cost, best_x = None, None
    ncalls = 0

    def move(j, newv):
        """set free var j to newv; return delta soft cost"""
        dv = newv - x[j]
        changed = [j] + [p for p, _ in sup[j]]
        aff = set(t for v in changed for t in sof[v])
        before = sum(tcost(t) for t in aff)
        x[j] = newv
        for p, c in sup[j]:
            x[p] = (x[p] - c * dv) % K
        return sum(tcost(t) for t in aff) - before
    for restart in range(3):
        for j in free:
            total += move(j, rng.randrange(K))
        T0, T1, steps = 4.0, 0.2, 30000 if len(free) > 25 else 12000
        for it in range(steps):
            T = T0 * (T1 / T0) ** (it / steps)
            j = free[rng.randrange(len(free))]
            old = x[j]
            nv = (old + rng.randint(1, K - 1)) % K
            d = move(j, nv)
            if d <= 0 or rng.random() < math.exp(-d / T):
                total += d
            else:
                move(j, old)
        imp = True
        while imp:
            imp = False
            for j in free:
                old = x[j]
                for nv in range(K):
                    if nv == old:
                        continue
                    d = move(j, nv)
                    if d < 0:
                        total += d; old = nv; imp = True
                    else:
                        move(j, old)
        if best_cost is None or total < best_cost:
            best_cost, best_x = total, list(x)
    return best_cost, len(free), best_x     # hard terms satisfied by construction (all-zero)




COMMON = '''
import random, time, math
CPU_LIMIT_S = 15.0

def _flat(tb, d):
    if d == 0:
        return [tb]
    out = []
    for s in tb:
        out.extend(_flat(s, d - 1))
    return out

def _prep(n, k, terms):
    tabs, of = [], [[] for _ in range(n)]
    for t, term in enumerate(terms):
        vs = term["vars"]
        tabs.append(_flat(term["table"], len(vs)))
        for p, v in enumerate(vs):
            of[v].append((t, k ** (len(vs) - 1 - p)))
    return tabs, of

def _idx(terms, k, x):
    out = []
    for term in terms:
        vs = term["vars"]; i = 0
        for v in vs:
            i = i * k + x[v]
        out.append(i)
    return out

def _anneal(n, k, tabs, of, x, idx, rng, t_end, T0, T1):
    t_start = time.process_time()
    span = max(1e-9, t_end - t_start)
    T = T0
    it = 0
    rr = rng.random; ri = rng.randrange; ex = math.exp
    while True:
        if it % 500 == 0:
            f = (time.process_time() - t_start) / span
            if f >= 1.0:
                break
            T = T0 * (T1 / T0) ** f
        it += 1
        v = ri(n)
        a = x[v]
        b = ri(k - 1)
        if b >= a:
            b += 1
        dv = b - a
        d = 0
        ov = of[v]
        for t, s in ov:
            tb = tabs[t]
            d += tb[idx[t] + dv * s] - tb[idx[t]]
        if d <= 0 or rr() < ex(-d / T):
            x[v] = b
            for t, s in ov:
                idx[t] += dv * s

def _total(tabs, idx):
    return sum(tabs[t][idx[t]] for t in range(len(tabs)))

def _icm(n, k, tabs, of, x, idx):
    changed = True
    while changed:
        changed = False
        for v in range(n):
            a = x[v]; ov = of[v]
            best, bb = 0, a
            for b in range(k):
                if b == a:
                    continue
                dv = b - a
                d = sum(tabs[t][idx[t] + dv * s] - tabs[t][idx[t]] for t, s in ov)
                if d < best:
                    best, bb = d, b
            if bb != a:
                dv = bb - a
                x[v] = bb
                for t, s in ov:
                    idx[t] += dv * s
                changed = True
''' 

BASE = COMMON + '''
def solve(n, k, terms):
    deadline = 0.85 * CPU_LIMIT_S
    rng = random.Random(1)
    tabs, of = _prep(n, k, terms)
    x = [rng.randrange(k) for _ in range(n)]
    idx = _idx(terms, k, x)
    _anneal(n, k, tabs, of, x, idx, rng, deadline, 10.0, 0.3)
    return x
'''

BASE2 = COMMON + '''
def solve(n, k, terms):
    deadline = 0.85 * CPU_LIMIT_S
    rng = random.Random(1)
    tabs, of = _prep(n, k, terms)
    t0 = time.process_time()
    R = 3
    seg = (deadline - 0.3 - t0) / R
    best, bc = None, None
    for r in range(R):
        x = [rng.randrange(k) for _ in range(n)]
        idx = _idx(terms, k, x)
        _anneal(n, k, tabs, of, x, idx, rng, t0 + seg * (r + 1), 10.0, 0.3)
        _icm(n, k, tabs, of, x, idx)
        c = _total(tabs, idx)
        if bc is None or c < bc:
            best, bc = list(x), c
    return best
'''

# smarter generic: annealing + focused moves on "penalty" terms (WalkSAT style) + whole-term block moves
BASE3 = COMMON + '''
def solve(n, k, terms):
    deadline = 0.85 * CPU_LIMIT_S
    rng = random.Random(1)
    tabs, of = _prep(n, k, terms)
    nt = len(terms)
    big = [max(tb) >= 20 for tb in tabs]               # penalty-like terms
    mins = [min(tb) for tb in tabs]
    zeros = []
    for t in range(nt):
        d = len(terms[t]["vars"]); cells = []
        for i, c in enumerate(tabs[t]):
            if c == mins[t]:
                vals = []
                for _ in range(d):
                    vals.append(i % k); i //= k
                cells.append(vals[::-1])
        zeros.append(cells)
    x = [rng.randrange(k) for _ in range(n)]
    idx = _idx(terms, k, x)
    vl, pos = [], {}
    def touch(t):
        if not big[t]:
            return
        bad = tabs[t][idx[t]] > mins[t]
        if bad and t not in pos:
            pos[t] = len(vl); vl.append(t)
        elif not bad and t in pos:
            i = pos.pop(t); last = vl.pop()
            if last != t:
                vl[i] = last; pos[last] = i
    for t in range(nt):
        touch(t)
    t_start = time.process_time(); span = deadline - t_start
    T0, T1 = 10.0, 0.3
    T = T0; it = 0
    rr = rng.random; ri = rng.randrange; ex = math.exp
    while True:
        if it % 200 == 0:
            f = (time.process_time() - t_start) / span
            if f >= 1.0:
                break
            T = T0 * (T1 / T0) ** f
        it += 1
        u = rr()
        if vl and u < 0.5:
            t = vl[ri(len(vl))]
            vs = terms[t]["vars"]
            if u < 0.25:                                # block move: set the term to one of its cheapest cells
                cell = zeros[t][ri(len(zeros[t]))]
                ch = [(v, c) for v, c in zip(vs, cell) if x[v] != c]
            else:                                       # focused single move
                v = vs[ri(len(vs))]; b = ri(k - 1)
                if b >= x[v]:
                    b += 1
                ch = [(v, b)]
        else:
            v = ri(n); b = ri(k - 1)
            if b >= x[v]:
                b += 1
            ch = [(v, b)]
        if not ch:
            continue
        aff = set()
        for v, b in ch:
            for t, s in of[v]:
                aff.add(t)
        old = sum(tabs[t][idx[t]] for t in aff)
        saved = [(v, x[v]) for v, b in ch]
        for v, b in ch:
            dv = b - x[v]; x[v] = b
            for t, s in of[v]:
                idx[t] += dv * s
        d = sum(tabs[t][idx[t]] for t in aff) - old
        if d <= 0 or rr() < ex(-d / T):
            for t in aff:
                touch(t)
        else:
            for v, a in saved:
                dv = a - x[v]; x[v] = a
                for t, s in of[v]:
                    idx[t] += dv * s
    _icm(n, k, tabs, of, x, idx)
    return x
'''




#: The work clock (seg PACKS, Nov 14). CPU seconds are counted as executed user-mode instructions at a fixed rate,
#: so the same reply gets the same budget and grades the same on any machine at any load: inside the sandbox
#: time.process_time() and the other time functions return seconds of counted work (1 ms steps), the limit is
#: checked on a second counter, randomness and hashing are made deterministic (fixed seeds, PYTHONHASHSEED=0,
#: no ASLR), and new threads/processes are refused (the contract says one core). Real CPU (RLIMIT_CPU) stays only
#: as a runaway guard at 4x the budget. A box without a usable instruction counter raises WorkClockUnavailable
#: (a grading-infrastructure error, never a zero for the model).
#: Instruction counts differ between Python builds (3.14/3.13: -7% to +16% on solver-like loops), so a grade is
#: reproducible only on the same interpreter. The grade detail names it; board rows are graded on the pinned one.
PINNED_PY = "3.13.14"              # the engine venv's Python (uv-pinned); regrade only on this interpreter
WORK_RATE = 8_000_000_000          # instructions per work-second (generic annealers BASE/BASE2/BASE3 21/27/11 points on ffseeds 1-4)
_DRIVER_SRC = r"""import sys, os
if os.environ.get("BENCH_OPT_STAGE") != "2":
    import ctypes
    try:
        ctypes.CDLL(None).personality(0x0040000)
    except Exception:
        pass
    os.execve(sys.executable, [sys.executable, "-s", "-S", "-P", __file__],
              {"PATH": "/usr/bin:/bin", "PYTHONHASHSEED": "0", "BENCH_OPT_STAGE": "2"})
import json, time, types, ctypes, struct, random, resource, platform, hashlib
_RATE = @RATE@
_PYV = platform.python_version()
_libc = ctypes.CDLL(None, use_errno=True)
class _Attr(ctypes.Structure):
    _fields_ = [("type", ctypes.c_uint32), ("size", ctypes.c_uint32), ("config", ctypes.c_uint64),
                ("sample_period", ctypes.c_uint64), ("sample_type", ctypes.c_uint64), ("read_format", ctypes.c_uint64),
                ("flags", ctypes.c_uint64), ("wakeup", ctypes.c_uint32), ("bp_type", ctypes.c_uint32),
                ("config1", ctypes.c_uint64), ("config2", ctypes.c_uint64)]
def _open():
    a = _Attr(); a.type = 0; a.size = ctypes.sizeof(_Attr); a.config = 1; a.flags = (1 << 5) | (1 << 6)
    nr = {"x86_64": 298, "aarch64": 241}.get(platform.machine(), -1)
    return _libc.syscall(nr, ctypes.byref(a), 0, -1, -1, 8) if nr > 0 else -1
_fc, _fe = _open(), _open()
if _fc < 0 or _fe < 0:
    print("BENCH_OPT_INFRA:no instruction counter (perf_event_open)"); sys.stdout.flush(); os._exit(0)
_rd, _up = os.read, struct.unpack
_Q = _RATE // 1000
def _ticks():
    return _up("q", _rd(_fc, 8))[0] // _Q
def _pt():
    return _ticks() / 1000.0
def _pt_ns():
    return _ticks() * 1000000
_T0 = 1767225600.0
for _n in ("process_time", "perf_counter", "monotonic", "thread_time"):
    setattr(time, _n, _pt); setattr(time, _n + "_ns", _pt_ns)
time.time = lambda: _T0 + _pt()
time.time_ns = lambda: int(_T0) * 1000000000 + _pt_ns()
time.sleep = lambda s: None
random.seed(0)
class _DetRandom(random.Random):
    def __init__(self, x=None):
        super().__init__(0 if x is None else x)
random.Random = _DetRandom
_uc = [0]
def _urandom(n):
    out = b""
    while len(out) < n:
        _uc[0] += 1; out += hashlib.sha256(b"bench-opt-%d" % _uc[0]).digest()
    return out[:n]
os.urandom = _urandom
_d = json.loads(@DATA@)
try:
    resource.setrlimit(resource.RLIMIT_NPROC, (0, 0))
except Exception:
    pass
_m = types.ModuleType("solver")
sys.modules["solver"] = _m
try:
    exec(compile(@SRC@, "solver.py", "exec"), _m.__dict__)
    _m.CPU_LIMIT_S = float(_d["cpu_limit_s"])
    _out = _m.solve(_d["n"], _d["k"], _d["terms"])
    _x = list(_out)
    _used = _up("q", _rd(_fe, 8))[0] / _RATE
    print("BENCH_OPT:" + json.dumps([_x, _used, _PYV]))
except BaseException as _e:
    print("BENCH_OPT_ERR:" + (type(_e).__name__ + ": " + str(_e))[:200].replace("\n", " "))
sys.stdout.flush()
os._exit(0)
"""


class WorkClockUnavailable(RuntimeError):
    """The grading box has no usable user-mode instruction counter; planted_opt cannot be graded here."""


def _harness(code: str, data: str) -> str:
    return (_DRIVER_SRC.replace("@RATE@", str(WORK_RATE)).replace("@DATA@", repr(data))
            .replace("@SRC@", repr(code)))


def _run(code: str, inst: dict, cpu_s: float) -> dict:
    """Run ``code`` in the graders' bwrap sandbox (no network, private tmpfs); the assignment is scored outside."""
    import resource
    from .. import graders as _g
    if not code.strip():
        return {"valid": False, "note": "no code in response", "cpu_s": 0.0}
    _g.require_sandbox()
    data = json.dumps({"n": inst["n"], "k": K, "terms": inst["terms"], "cpu_limit_s": cpu_s}, separators=(",", ":"))
    harness = _harness(code, data)
    lim = int(4 * cpu_s) + 1                     # runaway guard only; the budget is the work clock

    def pre():
        resource.setrlimit(resource.RLIMIT_CPU, (lim, lim + 1))
        resource.setrlimit(resource.RLIMIT_AS, (2 << 30, 2 << 30))
    with tempfile.TemporaryDirectory(prefix="bench-opt-") as td:
        path = Path(td) / "harness.py"
        path.write_text(harness, encoding="utf-8")
        try:
            p = subprocess.run(_g._sandbox_cmd(str(path), td), capture_output=True, text=True, encoding="utf-8",
                               errors="replace", timeout=cpu_s * 8 + 30, cwd=td, env=_g._child_env(),
                               preexec_fn=pre)
            out, rc, err = p.stdout, p.returncode, p.stderr[-300:]
        except subprocess.TimeoutExpired:
            return {"valid": False, "note": "wall timeout", "cpu_s": cpu_s * 4}
    if "BENCH_OPT_INFRA:" in out:
        raise WorkClockUnavailable(out.split("BENCH_OPT_INFRA:", 1)[1].splitlines()[0])
    line = next((ln for ln in reversed(out.splitlines()) if ln.startswith("BENCH_OPT")), None)
    if line is None or line.startswith("BENCH_OPT_ERR:"):
        why = line[len("BENCH_OPT_ERR:"):] if line else (err or f"rc={rc}")[-200:]
        return {"valid": False, "note": "crash: " + why.strip()[:200], "cpu_s": 0.0}
    try:
        x, cpu, *py = json.loads(line[len("BENCH_OPT:"):])
    except (ValueError, TypeError):
        return {"valid": False, "note": "unreadable output", "cpu_s": 0.0}
    if cpu > cpu_s:
        return {"valid": False, "note": f"over the work limit ({cpu:.1f}s > {cpu_s:g}s)", "cpu_s": cpu}
    if (not isinstance(x, list) or len(x) != inst["n"]
            or not all(type(v) is int and 0 <= v < K for v in x)):
        return {"valid": False, "note": "bad assignment", "cpu_s": cpu}
    return {"valid": True, "x": x, "cpu_s": cpu, "py": str(py[0]) if py else "?"}


def points(cost, c_base, c_ref, valid=True) -> float:
    if not valid:
        return 0.0
    if c_base <= c_ref:
        return 30.0
    return round(max(10.0, 30.0 + 70.0 * (c_base - cost) / (c_base - c_ref)), 1)


def grade_optimize(response_text: str, cfg: dict, finish_reason=None) -> dict:
    """Engine grader "optimize": cfg = {family, seed, level, cpu_s}. Same path as ``grade``."""
    from .. import graders as _g
    inst = instance(cfg["seed"], cfg["level"])
    code = _g.extract_python(response_text or "", finish_reason)
    r = _run(code, inst, float(cfg.get("cpu_s", CPU_S)))
    if not r["valid"]:
        return {"grade": "fail", "detail": r["note"], "score": 0.0, "rate": 0.0, "points": 0.0,
                "cpu_s": r["cpu_s"]}
    cost = cost_of(inst["terms"], r["x"])
    pts = points(cost, cost_base(inst), inst["ref_cost"])
    s = round(min(1.0, pts / 100.0), 4)
    return {"grade": "pass" if s >= 0.999 else "fail",
            "detail": f"cost {cost} (reference {inst['ref_cost']}, anchor {cost_base(inst):.0f}); {pts} points; "
                      f"{violated(inst['terms'], r['x'])} hard terms violated; cpu {r['cpu_s']:.1f}s; py {r['py']}"
                      + ("" if r["py"] == PINNED_PY else f" (not the pinned {PINNED_PY})"),
            "score": s, "rate": s, "points": pts, "cost": cost, "cpu_s": r["cpu_s"], "py": r["py"]}


def materialize(gen: dict, *, release_seed: str, case_id: str) -> dict:
    level = int(gen["level"])
    if level not in CFG:
        raise ValueError(f"planted_opt: unknown level {level} (levels {sorted(CFG)})")
    seed = _seed_for(gen, release_seed, case_id)
    inst = instance(seed, level)
    return {"prompt": inst["prompt"], "temperature": 0.0, "max_tokens": MAX_TOKENS,
            "grader": "optimize",
            "grader_config": {"family": FAMILY, "seed": seed, "level": level, "cpu_s": CPU_S},
            "meta": {"n": inst["n"], "terms": len(inst["terms"]), "free_dims": inst["free_dims"],
                     "ref_cost": inst["ref_cost"], "rand_cost": inst["rand_cost"],
                     "anchor_cost": round(cost_base(inst), 1)}}


def reference(gen: dict, *, release_seed: str, case_id: str) -> str:
    inst = instance(_seed_for(gen, release_seed, case_id), int(gen["level"]))
    return ("```python\nCPU_LIMIT_S = 15.0\n\ndef solve(n, k, terms):\n    return "
            + json.dumps(inst["ref_x"]) + "\n```")


def mutants(gen: dict, *, release_seed: str, case_id: str) -> list[str]:
    return ["```python\nCPU_LIMIT_S = 15.0\n\ndef solve(n, k, terms):\n    return [0] * n\n```",
            "```python\nCPU_LIMIT_S = 15.0\n\ndef solve(n, k, terms):\n    return [0] * (n - 1)\n```",
            "```python\ndef solve(n, k, terms):\n    return None\n```"]


GRADE_IS_ENGINE = True


def grade(answer: str, materialized: dict) -> float:
    return float(grade_optimize(answer, materialized["grader_config"])["score"])


# ---- generic baselines (S0 anchors; not used by grading)

PROMPT = """You are writing one Python 3 module (standard library only, single core) that solves a weighted assignment problem. Reply with exactly one ```python code block containing the whole module, and nothing else of substance. Your reply may use at most 20,000 tokens.

Problem. There are n variables, numbered 0..n-1; each takes one value from 0..k-1 (k = 5). You are given a list `terms`; each term is a dict {"vars": [...], "table": ...} where vars has one, two or three variable indices and table is a nested list of non-negative integer costs with one level per variable: for vars [i, j, k2] the cost is table[x[i]][x[j]][x[k2]]; for one variable, table[x[i]]. The cost of an assignment x (a list with x[v] the value of variable v) is the sum of the costs of all terms. Lower total cost is better.

Interface. Define `solve(n, k, terms)` returning a list of n ints, each in 0..k-1. A driver imports your module, sets the module-level float `CPU_LIMIT_S` (the CPU-time limit in seconds, 15.0 by default) and then calls solve once. Define a default `CPU_LIMIT_S = 15.0` at module top and read it inside solve. The whole process, including start-up and input parsing, must finish within that many CPU seconds on one core, otherwise the instance scores zero, as does a list of the wrong length or with values out of range. CPU time here is a work clock: time.process_time() and the other time functions return seconds of counted work (executed instructions at a fixed rate), so the limit is the same amount of work on any machine; new threads and processes are not available. Plan your own time budget with time.process_time() and leave a safety margin.

Instances. They come from one facility's interlock and scheduling records and tend to look alike. This instance has n = @N@ and @T@ terms; other instances have n from about 100 to 400. You receive only the instance: no other files, no network. For reference, here are a few terms from this instance (index in the list, vars, table):
@EX@

Scoring. Your assignment is scored by its total cost, computed outside the sandbox, and compared with a strong reference solution that was obtained with knowledge of how the data was produced. The closer your total cost is to the reference (or below it), the higher your score; a reply that crashes, times out or returns an invalid list scores zero.
"""
