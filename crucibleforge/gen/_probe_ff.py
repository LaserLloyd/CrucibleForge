"""(CrucibleForge copy of ff/q/probe.py @ 4528b74 on branch t-probe: hidden systems, measure, accuracy, factor, prompts, reference and baseline unchanged;
the ff sandbox runner and ``check`` removed; the engine runner is in probe.py.)
PROBE: characterise a hidden seeded system through an isolated measurement interface. Stdlib only.
The hidden system lives in the GRADER process; the model's program (sandboxed, bwrap, no network, pinned core) talks to it
over stdin/stdout JSON lines. Score q = accuracy x measurement factor f(n); n = measurement requests (R23: measurements,
never tokens or time, are the efficiency control).
  tier 1: shock-absorber test bench: unit force step on a damped spring; measure(t) -> displacement + noise;
          report omega (rad/s) and zeta.
  tier 3: nonlinear bench (class NOT stated: cubic stiffening + friction + damping); measure(amp, t) with amp<=1, t<=10;
          predict displacement at 10 held-out (amp up to 3, t up to 16) queries."""
import json, math, random

NAME, TIERS, CPU_S = "probe", (1, 3), 20
CAP, FREE = 2000, 6
NOISE = {1: 0.03, 3: 0.004}
T_MAX = {1: 20.0, 3: 10.0}
TOL = {"omega": 0.10, "zeta": 0.30}   # tier 1: relative error that earns zero for a parameter
T3_R0 = 0.8        # tier 3: rmse/sd at which accuracy is 0 (log scale; 0.05 -> 1)


def factor(n):
    return 1.0 if n <= FREE else max(0.1, 1.0 - math.log2(n / FREE) / 8.5)


# ---------------------------------------------------------------- hidden systems
def hidden1(seed):
    r = random.Random(f"probe1:{seed}")
    return {"omega": round(math.exp(r.uniform(math.log(1.0), math.log(10.0))), 4), "zeta": round(r.uniform(0.05, 0.6), 4)}


def resp1(p, t):
    w, z = p["omega"], p["zeta"]
    wd = w * math.sqrt(1 - z * z)
    return 1 - math.exp(-z * w * t) * (math.cos(wd * t) + z * w / wd * math.sin(wd * t))


def hidden3(seed):
    r = random.Random(f"probe3:{seed}")
    return {"k": round(r.uniform(2, 6), 3), "c": round(r.uniform(0.2, 1.0), 3), "k3": round(r.uniform(10, 60), 3),
            "mu": round(r.uniform(0.05, 0.4), 3)}


def sim3(p, amp, ts, dt=0.01):
    """RK4 of x'' = amp - c x' - k x - k3 x^3 - mu tanh(x'/0.02) from rest; returns x at each t in ts (sorted ascending or not)."""
    k, c, k3, mu = p["k"], p["c"], p["k3"], p["mu"]

    def f(x, v):
        return v, amp - c * v - k * x - k3 * x ** 3 - mu * math.tanh(v / 0.02)
    tmax = max(ts)
    n = int(tmax / dt) + 2
    xs = [0.0]
    x = v = 0.0
    for _ in range(n):
        a1, b1 = f(x, v)
        a2, b2 = f(x + dt / 2 * a1, v + dt / 2 * b1)
        a3, b3 = f(x + dt / 2 * a2, v + dt / 2 * b2)
        a4, b4 = f(x + dt * a3, v + dt * b3)
        x += dt / 6 * (a1 + 2 * a2 + 2 * a3 + a4)
        v += dt / 6 * (b1 + 2 * b2 + 2 * b3 + b4)
        xs.append(x)
    out = []
    for t in ts:
        i = min(int(t / dt), n - 1)
        fr = t / dt - i
        out.append(xs[i] * (1 - fr) + xs[i + 1] * fr)
    return out


def gen(seed, tier):
    if tier == 1:
        hid = hidden1(seed)
        pub = {"tier": 1, "t_max": T_MAX[1]}
    else:
        hid = hidden3(seed)
        r = random.Random(f"probe3q:{seed}")
        qs = [[round(r.uniform(1.5, 3.0), 2), round(r.uniform(0.4, 16.0), 2)] for _ in range(10)]
        pub = {"tier": 3, "t_max": T_MAX[3], "queries": qs}
        hid = dict(hid, truth=[sim3(hid, a, [t], 0.005)[0] for a, t in qs])
    return {"prompt": _prompt(tier), "public": pub, "hidden": dict(hid, seed=seed, tier=tier)}


def _num(x):
    if isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x):
        raise ValueError("arguments must be finite numbers")
    return float(x)


def _measure(hid, a, n, cache):
    tier = hid["tier"]
    noise = random.Random(f"probe-noise:{hid['seed']}:{tier}:{n}").gauss(0, NOISE[tier])
    if tier == 1:
        if len(a) != 1:
            raise ValueError("measure(t) takes one number")
        t = _num(a[0])
        if not 0 <= t <= T_MAX[1]:
            raise ValueError(f"t must be in [0, {T_MAX[1]:g}]")
        return round(resp1(hid, t) + noise, 6)
    if len(a) != 2:
        raise ValueError("measure(amp, t) takes two numbers")
    amp, t = _num(a[0]), _num(a[1])
    if not (0.2 <= amp <= 1.0 and 0 <= t <= T_MAX[3]):
        raise ValueError("amp must be in [0.2, 1], t in [0, 10]")
    key = round(amp, 6)
    if key not in cache:
        if len(cache) >= 60:
            raise ValueError("too many distinct amplitudes; reuse earlier ones")
        cache[key] = sim3(hid, key, [i * 0.01 for i in range(1001)], 0.01)
    tr = cache[key]
    i = min(int(t / 0.01), 999)
    fr = t / 0.01 - i
    return round(tr[i] * (1 - fr) + tr[i + 1] * fr + noise, 6)


# ---------------------------------------------------------------- scoring
LAST = {}


def accuracy(hid, ans):
    LAST.clear()
    if hid["tier"] == 1:
        if not isinstance(ans, dict):
            return None
        a = {}
        for k in ("omega", "zeta"):
            v = ans.get(k)
            if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v <= 0:
                return None
            a[k] = max(0.0, 1 - abs(v - hid[k]) / hid[k] / TOL[k])
        LAST.update(a)
        return 0.2 * (a["omega"] + a["zeta"]) / 2 + 0.8 * math.sqrt(a["omega"] * a["zeta"])
    pr = ans.get("pred") if isinstance(ans, dict) else ans
    if not isinstance(pr, list) or len(pr) != len(hid["truth"]):
        return None
    for v in pr:
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
            return None
    tr = hid["truth"]
    m = sum(tr) / len(tr)
    sd = math.sqrt(sum((x - m) ** 2 for x in tr) / len(tr))
    rmse = math.sqrt(sum((a - b) ** 2 for a, b in zip(pr, tr)) / len(tr))
    r = max(rmse / sd, 1e-9)
    return min(1.0, max(0.0, math.log(T3_R0 / r) / math.log(T3_R0 / 0.05)))


# ---------------------------------------------------------------- prompts
_COMMON = """You are testing a hidden mechanical device through a measurement interface. Write a Python 3 module (standard library only) that runs the experiment and returns the answer. The device is simulated in a separate process: your program cannot read its parameters, only call `measure`. Each call to `measure` is one measurement; the measurement count is the efficiency control, so plan the experiment. Every call is a fresh noisy reading (measurement noise is independent between calls; repeating a call gives a new value). You may call `measure` at most 2000 times in total (further calls raise ValueError). Your program has 20 CPU seconds in total (process CPU time, one core, no network; time spent waiting for measurements is not counted)."""

_P1 = _COMMON + """

THE DEVICE ("shock absorber test bench"): a mass on a spring with a damper, at rest, hit by a constant unit force at time 0 (a force step). Its displacement x(t) follows x'' + 2*zeta*omega*x' + omega^2*x = omega^2 for t >= 0, starting from x = 0, x' = 0, so it settles to x = 1. It is under-damped. Unknowns: omega (natural frequency, rad/s, between 1 and 10) and zeta (damping ratio, between 0.05 and 0.6). Times are in seconds.

INTERFACE: `measure(t)` with 0 <= t <= 20 (one float) returns the displacement at time t plus Gaussian noise with standard deviation 0.03. Anything else raises ValueError.

YOUR MODULE must define `solve(measure)` that performs the measurements and returns a dict `{"omega": float, "zeta": float}`.

SCORING: accuracy x measurement factor. Accuracy per unknown is 1 - (relative error)/tol, floored at 0 (tol 10% for omega, 30% for zeta); both must be right to score well: accuracy = 0.2*mean + 0.8*geometric mean of the two. Measurement factor is 1.0 for up to 6 measurements, then falls with the logarithm of the count: 1 - log2(n/6)/8.5 (0.88 at 12, 0.71 at 24, 0.53 at 60, 0.25 at 500, floor 0.1 from about 1000). A program that returns a guess without measuring has full factor but no information. Reply with the complete module in a single ```python block."""

_P3 = _COMMON + """

THE DEVICE ("nonlinear shock absorber bench"): a mass on a damped spring pushed by a constant force of size `amp` switched on at time 0, starting at rest (x = 0, x' = 0). You are NOT told the exact physics: expect some linear damping, some spring law that may not be linear, and possibly some dry friction; there are a handful of hidden constants (for small displacements the stiffness is between 2 and 6). Displacement is in metres, time in seconds.

INTERFACE: `measure(amp, t)` with 0.2 <= amp <= 1.0 and 0 <= t <= 10 returns the displacement at time t after a force step of size amp, plus Gaussian noise with standard deviation 0.004. Anything outside those ranges raises ValueError (and still counts as a measurement). At most 60 distinct `amp` values may be used.

THE TASK: after your experiment, predict the noise-free displacement for 10 held-out cases. They are given to your program as `queries`, a list of 10 `[amp, t]` pairs, but they lie OUTSIDE the range you may measure: amp between 1.5 and 3.0, t up to 16. You must extrapolate, so identify the mechanism.

YOUR MODULE must define `solve(measure, queries)` returning a list of 10 floats (predicted displacement for each query, in order), or `{"pred": [...]}`.

SCORING: accuracy x measurement factor. Accuracy = log(0.8/r)/log(16), clipped to 0..1, where r = RMSE/s, RMSE is your root-mean-square prediction error over the 10 cases and s is the standard deviation of the true values over them (r = 0.8 or worse scores 0, r = 0.2 scores 0.5, r = 0.05 or better scores 1). Measurement factor is 1.0 for up to 6 measurements, then 1 - log2(n/6)/8.5 (0.88 at 12, 0.71 at 24, 0.53 at 60, 0.25 at 500, floor 0.1 from about 1000). Reply with the complete module in a single ```python block."""


def _prompt(tier):
    return _P1 if tier == 1 else _P3


# ---------------------------------------------------------------- reference solvers
_REF1 = '''
import math
def _resp(w, z, t):
    wd = w * math.sqrt(1 - z * z)
    return 1 - math.exp(-z * w * t) * (math.cos(wd * t) + z * w / wd * math.sin(wd * t))
def solve(measure):
    # planned experiment: 8 early points (resolve the fastest oscillation), 6 mid, 6 late (decay of the slow modes)
    ts = [0.12, 0.25, 0.4, 0.58, 0.8, 1.05, 1.35, 1.7, 2.1, 2.6, 3.2, 3.9, 4.8, 5.9, 7.2, 8.8, 10.6, 12.8, 15.3, 18.0]
    ys = [measure(t) for t in ts]
    best = (1e9, 3.0, 0.2)
    def sse(w, z):
        return sum((_resp(w, z, t) - y) ** 2 for t, y in zip(ts, ys))
    for i in range(0, 181):
        w = math.exp(math.log(1.0) + (math.log(10.0) - math.log(1.0)) * i / 180)
        for j in range(0, 45):
            z = 0.04 + 0.6 * j / 44
            e = sse(w, z)
            if e < best[0]:
                best = (e, w, z)
    e, w, z = best
    sw, sz = w * 0.03, 0.02
    for _ in range(40):
        for dw, dz in ((sw, 0), (-sw, 0), (0, sz), (0, -sz), (sw, sz), (-sw, -sz), (sw, -sz), (-sw, sz)):
            w2, z2 = w + dw, min(0.95, max(0.01, z + dz))
            e2 = sse(w2, z2)
            if e2 < e:
                e, w, z = e2, w2, z2
        sw *= 0.8; sz *= 0.8
    return {"omega": w, "zeta": z}
'''

_CRUDE1 = '''
import math
def solve(measure):
    n = 500
    ts = [20.0 * i / (n - 1) for i in range(n)]
    ys = [measure(t) - 1.0 for t in ts]
    end = n
    for i in range(0, n - 10):
        if max(abs(v) for v in ys[i:i + 10]) < 0.1:
            end = i
            break
    cr = []
    for i in range(1, end):
        a, b = ys[i - 1], ys[i]
        if a * b < 0:
            cr.append(i)
    wd, z = 3.0, 0.3
    if len(cr) >= 3:
        # half periods between successive crossings (skip the rise crossing)
        k = min(len(cr) - 1, 6)
        half = (ts[cr[k]] - ts[cr[1]]) / (k - 1) if k > 1 else ts[cr[2]] - ts[cr[1]]
        wd = math.pi / half
        ext = [max(abs(v) for v in ys[cr[i]:cr[i + 1]]) for i in range(len(cr) - 1)]
        ext = [e for e in ext if e > 0.0]
        if len(ext) >= 2:
            delta = math.log(max(ext[0], 1e-3) / max(ext[1], 1e-3))
            z = delta / math.sqrt(math.pi ** 2 + delta ** 2) if delta > 0 else 0.1
    z = min(0.9, max(0.02, z))
    return {"omega": wd / math.sqrt(1 - z * z), "zeta": z}
'''

_REF3 = '''
import math
def sim(p, amp, ts, dt=0.02):
    k, c, k3, mu = p
    def f(x, v):
        return v, amp - c * v - k * x - k3 * x ** 3 - mu * math.tanh(v / 0.02)
    n = int(max(ts) / dt) + 2
    xs = [0.0]; x = v = 0.0
    for _ in range(n):
        a1, b1 = f(x, v); a2, b2 = f(x + dt / 2 * a1, v + dt / 2 * b1)
        a3, b3 = f(x + dt / 2 * a2, v + dt / 2 * b2); a4, b4 = f(x + dt * a3, v + dt * b3)
        x += dt / 6 * (a1 + 2 * a2 + 2 * a3 + a4); v += dt / 6 * (b1 + 2 * b2 + 2 * b3 + b4); xs.append(x)
    out = []
    for t in ts:
        i = min(int(t / dt), n - 1); fr = t / dt - i
        out.append(xs[i] * (1 - fr) + xs[i + 1] * fr)
    return out
def solve(measure, queries):
    amps = [0.3, 0.6, 1.0]
    ts = [0.3, 0.6, 0.9, 1.3, 1.8, 2.5, 3.5, 5.0, 7.0]
    data = [(a, [measure(a, t) for t in ts]) for a in amps]
    def sse(p):
        if p[0] <= 0 or p[1] < 0 or p[2] < 0 or p[3] < 0:
            return 1e9
        return sum(sum((m - y) ** 2 for m, y in zip(sim(p, a, ts), ys)) for a, ys in data)
    def nm(p0, iters, sc):
        S = [list(p0)] + [[p0[i] * (sc if i == j else 1.0) for i in range(4)] for j in range(4)]
        F = [sse(p) for p in S]
        for _ in range(iters):
            o = sorted(range(5), key=lambda i: F[i]); S = [S[i] for i in o]; F = [F[i] for i in o]
            cen = [sum(S[i][j] for i in range(4)) / 4 for j in range(4)]
            r = [cen[j] + (cen[j] - S[4][j]) for j in range(4)]; fr = sse(r)
            if fr < F[0]:
                e = [cen[j] + 2 * (cen[j] - S[4][j]) for j in range(4)]; fe = sse(e)
                S[4], F[4] = (e, fe) if fe < fr else (r, fr)
            elif fr < F[3]:
                S[4], F[4] = r, fr
            else:
                c = [cen[j] + 0.5 * (S[4][j] - cen[j]) for j in range(4)]; fc = sse(c)
                if fc < F[4]:
                    S[4], F[4] = c, fc
                else:
                    for i in range(1, 5):
                        S[i] = [S[0][j] + 0.5 * (S[i][j] - S[0][j]) for j in range(4)]; F[i] = sse(S[i])
        i = F.index(min(F))
        return F[i], S[i]
    starts = [[k, 0.5, k3, 0.2] for k in (2.5, 4.0, 5.5) for k3 in (15, 35, 55)]
    cands = sorted((sse(p), p) for p in starts)[:3]
    best = None
    for _, p0 in cands:
        f, p = nm(p0, 90, 1.2)
        f, p = nm(p, 90, 1.05)
        if best is None or f < best[0]:
            best = (f, p)
    p = best[1]
    return [sim(p, a, [t])[0] for a, t in queries]
'''

_B3 = '''
import math
def sim(k, c, k3, mu, amp, ts, dt=0.04):
    x = v = 0.0
    n = int(max(ts) / dt) + 2
    xs = [0.0]
    def f(x, v):
        return v, amp - c * v - k * x - k3 * x ** 3 - mu * math.tanh(v / 0.03)
    for _ in range(n):
        a1, b1 = f(x, v); a2, b2 = f(x + dt / 2 * a1, v + dt / 2 * b1)
        a3, b3 = f(x + dt / 2 * a2, v + dt / 2 * b2); a4, b4 = f(x + dt * a3, v + dt * b3)
        x += dt / 6 * (a1 + 2 * a2 + 2 * a3 + a4); v += dt / 6 * (b1 + 2 * b2 + 2 * b3 + b4); xs.append(x)
    out = []
    for t in ts:
        i = min(int(t / dt), n - 1); fr = t / dt - i
        out.append(xs[i] * (1 - fr) + xs[i + 1] * fr)
    return out
def solve(measure, queries):
    # naive dense grid (4 amplitudes x 125 times = 500 readings), then a coarse grid fit of k, c, k3 (no friction term)
    amps = [0.25, 0.5, 0.75, 1.0]
    ts = [10.0 * (j + 0.5) / 125 for j in range(125)]
    data = {a: [measure(a, t) for t in ts] for a in amps}
    sub = ts[::5]
    best = (1e18, 4.0, 0.5, 20.0, 0.1)
    for k in (2.0, 3.0, 4.0, 5.0, 6.0):
        for c in (0.2, 0.5, 0.8, 1.0):
            for k3 in (10, 25, 40, 60):
                for mu in (0.05, 0.2, 0.35):
                    e = sum(sum((m - y) ** 2 for m, y in zip(sim(k, c, k3, mu, a, sub), data[a][::5])) for a in amps)
                    if e < best[0]:
                        best = (e, k, c, k3, mu)
    def sse(p):
        if min(p) <= 0:
            return 1e9
        return sum(sum((m - y) ** 2 for m, y in zip(sim(p[0], p[1], p[2], p[3], a, sub), data[a][::5])) for a in amps)
    p, e = list(best[1:]), best[0]
    sc = 0.15
    for _ in range(14):
        for j in range(4):
            for d in (1 + sc, 1 - sc):
                q = list(p); q[j] *= d
                e2 = sse(q)
                if e2 < e:
                    p, e = q, e2
        sc *= 0.75
    return [sim(p[0], p[1], p[2], p[3], a, [t])[0] for a, t in queries]
'''

_LS1 = '''
import math
def _resp(w, z, t):
    wd = w * math.sqrt(1 - z * z)
    return 1 - math.exp(-z * w * t) * (math.cos(wd * t) + z * w / wd * math.sin(wd * t))
def fit(ts, ys):
    step = max(1, len(ts) // 100)
    sub = [(t, y) for t, y in zip(ts[::step], ys[::step])]
    def sse(w, z, pts):
        return sum((_resp(w, z, t) - y) ** 2 for t, y in pts)
    best = (1e18, 3.0, 0.2)
    for i in range(0, 181):
        w = math.exp(math.log(10.0) * i / 180)
        for j in range(0, 45):
            z = 0.04 + 0.6 * j / 44
            e = sse(w, z, sub)
            if e < best[0]:
                best = (e, w, z)
    pts = list(zip(ts, ys))
    e, w, z = best[0], best[1], best[2]
    e = sse(w, z, pts)
    sw, sz = w * 0.03, 0.02
    for _ in range(25):
        for dw, dz in ((sw, 0), (-sw, 0), (0, sz), (0, -sz), (sw, sz), (-sw, -sz), (sw, -sz), (-sw, sz)):
            w2, z2 = w + dw, min(0.95, max(0.01, z + dz))
            e2 = sse(w2, z2, pts)
            if e2 < e:
                e, w, z = e2, w2, z2
        sw *= 0.8; sz *= 0.8
    return {"omega": w, "zeta": z}
def solve(measure):
    N = @N@
    ts = [20.0 * i / (N - 1) for i in range(N)]
    return fit(ts, [measure(t) for t in ts])
'''


def baseline(inst):
    return _LS1.replace("@N@", "500") if inst["hidden"]["tier"] == 1 else _B3


def _unused(inst):
    return _CRUDE1 if inst["hidden"]["tier"] == 1 else _B3


def reference(inst):
    return _REF1 if inst["hidden"]["tier"] == 1 else _REF3
