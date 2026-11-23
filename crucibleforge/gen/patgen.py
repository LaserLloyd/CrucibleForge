"""patgen (JX lane P, family P1 ABSTRACT->GENERAL, the owner 2026-11-12): two-turn next-digit predictor. v2.

Turn 1 (variant ``t1``): a few clean repeating ternary strings; the model writes ``Predictor``
(predict() -> 0/1/2, update(digit)). Graded on clean repeating streams.
Turn 2 (variant ``t2``): the case carries the model's own turn-1 reply as ``history``; the prompt
asks for "any pattern" and shows a few unlabelled example streams. Hidden streams superimpose and
interleave patterns: noisy long periods, sum mod 3 of two periodic patterns, interleaved streams
with random phase jumps, a 3-way interleave with a base-3 counter and a noise tail, and (v2, the
edge tiers) STREAM DEMUX: 3-4 generators emitted on an irregular periodic schedule (which source
speaks at time t is itself a hidden pattern), with a three-period sum, a phase-jumping source and
a counter inside.

Bound: the oracle knows every generator parameter (incl. the schedule) but not the noise or the
jump times: a forward filter over each jumping slot's phase, the clean digit elsewhere = the exact
Bayes-optimal predictor given the parameters.

Score (v2). Per stream, accuracy is rescaled against two anchors measured on that very stream: the
oracle (=1) and a generic variable-order n-gram predictor (=ANCHOR_A = 0.2); chance is 0.
Floor tiers (noisy-period, turn-1 repeats) are scored chance..oracle. Points = sum of tier weights:
floor 10, climb 60 (sum, interleave-jump, mix3-tail), edge 30 (demux-irregular, demux-deep, demux-wide).

Grading runs the predictor in its OWN sandboxed process (bwrap, own pid namespace); the grader keeps
the streams and feeds one digit per round trip, after the guess for the previous one is in, so a
predictor can neither read the test streams nor look ahead.
"""
from __future__ import annotations

import json
import math
import random
import sys

FAMILY = "patgen"
VARIANTS = ("t1", "t2")
GRADE_IS_ENGINE = True
L_STREAM, WARM, PER_TIER = 800, 150, 3
TIMEOUT_S = 60            # CPU seconds for the whole test run, stated to the model, enforced by RLIMIT_CPU
WALL_S = 240
ANCHOR_A = 0.2
#: the k<=13 reference predictor is competent, not an oracle (85.3 on /100 with the floor tier; per-seed 0.70-0.76)
REFERENCE_GRADE = (0.65, 1.0)
LEVELS = {2: {"answer_tokens": 900}}

T1_TIERS = {"repeat-short": [("per", 3, 9)], "repeat-long": [("per", 10, 16)]}
# role: floor (chance..oracle) | climb / edge (n-gram anchored); w = points
T2_TIERS = {
    "noisy-period": {"slots": [("per", 20, 40)], "eps": 0.06, "role": "floor", "w": 10},
    "sum": {"slots": [("sum", 9, 17)], "eps": 0.04, "role": "climb", "w": 20},
    "interleave-jump": {"slots": [("per", 7, 13), ("shift", 9, 15)], "eps": 0.03, "role": "climb", "w": 20},
    "mix3-tail": {"slots": [("sum", 3, 7), ("ctr", 200, 2000), ("per", 3, 8)], "eps": 0.02, "tail": 0.25,
                  "role": "climb", "w": 20},
    "demux-irregular": {"slots": [("per", 4, 9), ("sum", 3, 6), ("per", 5, 11)], "sched": (7, 10),
                        "eps": 0.03, "role": "edge", "w": 10},
    "demux-deep": {"slots": [("sum3", 3, 8), ("shift", 6, 11), ("sum", 3, 7), ("ctr", 200, 2000)],
                   "sched": (9, 13), "eps": 0.03, "tail": 0.2, "role": "edge", "w": 10},
    # FC1 (Task 2a fallback, one extra demux tier): five sources on an 11-15 schedule. Hidden only: no example
    # stream of it is shown, so the turn-2 prompt is byte-identical to the one the stored rows answered.
    "demux-wide": {"slots": [("per", 4, 9), ("sum", 3, 6), ("sum3", 3, 7), ("shift", 6, 11), ("ctr", 200, 2000)],
                   "sched": (11, 15), "eps": 0.03, "tail": 0.2, "role": "edge", "w": 10, "ex": False},
}
JUMP_H = 1 / 80


# ---------------------------------------------------------------------------- generation
def _b3(n):
    s = ""
    while n:
        s = str(n % 3) + s
        n //= 3
    return s or "0"


def _vals(r, p):
    v = [r.randrange(3) for _ in range(p)]
    while len(set(v)) < 2:
        v = [r.randrange(3) for _ in range(p)]
    return v


def _coprime_set(r, lo, hi, n):
    while True:
        ps = [r.randint(lo, hi) for _ in range(n)]
        if len(set(ps)) == n and all(math.gcd(a, b) == 1 for i, a in enumerate(ps) for b in ps[i + 1:]):
            return ps


def _draw_slot(r, spec):
    kind, lo, hi = spec
    if kind == "per":
        p = r.randint(lo, hi)
        return {"k": "per", "v": _vals(r, p), "phi": r.randrange(p)}
    if kind == "shift":
        p = r.randint(lo, hi)
        return {"k": "shift", "v": _vals(r, p), "phi": r.randrange(p), "h": JUMP_H}
    if kind in ("sum", "sum3"):
        ps = _coprime_set(r, lo, hi, 2 if kind == "sum" else 3)
        return {"k": kind, "a": [[r.randrange(3) for _ in range(p)] for p in ps],
                "ph": [r.randrange(p) for p in ps]}
    return {"k": "ctr", "n0": r.randint(lo, hi)}


def _draw_sched(r, k, mlo, mhi):
    """irregular periodic schedule: which of the k sources speaks at each step of a cycle of length m.
    Every source speaks at least once, the counts are unequal, and it is not a plain round robin."""
    while True:
        m = r.randint(mlo, mhi)
        sc = list(range(k)) + [r.randrange(k) for _ in range(m - k)]
        r.shuffle(sc)
        counts = [sc.count(j) for j in range(k)]
        if max(counts) - min(counts) >= 2 and not any(m % q == 0 and sc == sc[:q] * (m // q) for q in range(1, m)):
            return sc


def gen_stream(r, slots, eps0, tail=0.0, n=L_STREAM, sched=None):
    """returns digits, clean digits, and the generator parameters (the oracle reads them)"""
    slots = [_draw_slot(r, s) for s in slots]
    k = len(slots)
    sc = _draw_sched(r, k, *sched) if sched else list(range(k))
    m = len(sc)
    pos = [0] * m                                    # tau of cycle position c = earlier same-source uses in a cycle
    for c in range(m):
        pos[c] = sum(1 for x in sc[:c] if x == sc[c])
    per_cycle = [sc.count(j) for j in range(k)]
    ctr = {}
    for j, s in enumerate(slots):
        if s["k"] == "ctr":
            buf, mm, need = "", s["n0"], (n // m + 2) * per_cycle[j] + 2
            while len(buf) < need:
                buf += _b3(mm)
                mm += 1
            ctr[j] = buf
    phase = {j: s.get("phi", 0) for j, s in enumerate(slots)}
    out, clean, who = [], [], []
    t_tail = int(n * (1 - tail)) if tail else n
    for t in range(n):
        c = t % m
        j = sc[c]
        tau = (t // m) * per_cycle[j] + pos[c]
        s = slots[j]
        if s["k"] == "per":
            v = s["v"][(tau + s["phi"]) % len(s["v"])]
        elif s["k"] == "shift":
            if tau > 0 and r.random() < s["h"]:
                phase[j] = r.randrange(len(s["v"]))
            v = s["v"][(tau + phase[j]) % len(s["v"])]
        elif s["k"] in ("sum", "sum3"):
            v = sum(a[(tau + q) % len(a)] for a, q in zip(s["a"], s["ph"])) % 3
        else:
            v = int(ctr[j][tau])
        e = eps0 if t < t_tail else 0.5
        d = r.randrange(3) if r.random() < e else v
        clean.append(v)
        out.append(d)
        who.append((j, tau))
    return {"digits": out, "clean": clean, "slots": slots, "sched": sc, "who": who, "eps0": eps0, "t_tail": t_tail}


def oracle_acc(st, warm=WARM):
    """Bayes-optimal accuracy given the parameters (not the noise, not the jump times)."""
    slots, digits, clean, who = st["slots"], st["digits"], st["clean"], st["who"]
    post = {}
    for j, s in enumerate(slots):
        if s["k"] == "shift":
            post[j] = [1.0 if i == s["phi"] else 0.0 for i in range(len(s["v"]))]
    hit = tot = 0
    for t, d in enumerate(digits):
        j, tau = who[t]
        eps = st["eps0"] if t < st["t_tail"] else 0.5
        if j in post:
            s = slots[j]
            v, p, h = s["v"], len(s["v"]), s["h"]
            pr = post[j]
            if tau > 0:
                pr = [(1 - h) * x + h / p for x in pr]
            pc = [0.0, 0.0, 0.0]
            for ph, w in enumerate(pr):
                pc[v[(tau + ph) % p]] += w
            g = max(range(3), key=lambda c: pc[c])
            lik = [(1 - eps) * (1.0 if v[(tau + ph) % p] == d else 0.0) + eps / 3 for ph in range(p)]
            pr = [a * b for a, b in zip(pr, lik)]
            z = sum(pr)
            post[j] = [x / z for x in pr]
        else:
            g = clean[t]
        if t >= warm:
            tot += 1
            hit += g == d
    return hit / tot


def ngram_acc(digits, warm=WARM):
    """the generic anchor: variable-order context model, longest context with data wins (orders 1..12)"""
    x, c, hit, tot = [], {}, 0, 0
    for t, d in enumerate(digits):
        g = 0
        for k in range(min(12, len(x)), 0, -1):
            tab = c.get(tuple(x[len(x) - k:]))
            if tab and sum(tab) >= 2:
                g = max(range(3), key=tab.__getitem__)
                break
        if t >= warm:
            tot += 1
            hit += g == d
        n = len(x)
        for k in range(1, min(12, n) + 1):
            c.setdefault(tuple(x[n - k:]), [0, 0, 0])[d] += 1
        x.append(d)
    return hit / tot


# ---------------------------------------------------------------------------- instance
def _rng(key, salt):
    return random.Random(f"patgen:{key}:{salt}")


_CACHE: dict = {}


def _finish(st):
    st["oracle"] = oracle_acc(st)
    st["anchor"] = min(ngram_acc(st["digits"]), st["oracle"] - 0.02)
    return st


def instance(level, key):
    if (level, key) in _CACHE:
        return _CACHE[(level, key)]
    tiers1, tiers2 = {}, {}
    for name, slots in T1_TIERS.items():
        r = _rng(key, "t1:" + name)
        tiers1[name] = [_finish(gen_stream(r, slots, 0.0)) for _ in range(PER_TIER)]
    for name, sp in T2_TIERS.items():
        r = _rng(key, "t2:" + name)
        tiers2[name] = [_finish(gen_stream(r, sp["slots"], sp["eps"], sp.get("tail", 0.0), sched=sp.get("sched")))
                        for _ in range(PER_TIER)]
    ex1 = []
    r = _rng(key, "ex1")
    for p in r.sample(range(3, 8), 3):
        v = _vals(r, p)
        ex1.append("".join(str(v[i % p]) for i in range(36)))
    ex2 = []
    for name, sp in T2_TIERS.items():
        if sp.get("ex") is False:
            continue
        st = gen_stream(_rng(key, "ex2:" + name), sp["slots"], sp["eps"], 0.0, n=EX_LEN, sched=sp.get("sched"))
        ex2.append("".join(map(str, st["digits"])))
    _rng(key, "shuffle").shuffle(ex2)
    out = {"t1": tiers1, "t2": tiers2, "ex1": ex1, "ex2": ex2}
    _CACHE[(level, key)] = out
    return out


EX_LEN = 120


# ---------------------------------------------------------------------------- prompts
SYSTEM = "You are an expert Python programmer. Return the complete code inside a single ```python code block."

IFACE = (
    "Interface (standard library only):\n\n"
    "```python\nclass Predictor:\n    def __init__(self): ...\n"
    "    def predict(self) -> int: ...      # your guess for the next digit: 0, 1 or 2\n"
    "    def update(self, digit: int): ...  # the digit that actually came next\n```\n\n"
    "A fresh Predictor is made for every test stream; the tester calls predict() then update(d) for each "
    "digit in turn.\n")


def prompt_t1(inst):
    ex = "\n".join(inst["ex1"])
    n = sum(len(v) for v in inst["t1"].values())
    return ("Here are some strings of the digits 0, 1 and 2:\n\n" + ex + "\n\n"
            "Write a next-digit predictor for strings like these.\n\n" + IFACE +
            f"\nHidden test: {n} streams of {L_STREAM} digits; the score counts correct guesses from digit "
            f"{WARM} on. The whole test run must finish within {TIMEOUT_S} s of CPU time on one core.\n")


def prompt_t2(inst):
    ex = "\n".join(inst["ex2"])
    n = sum(len(v) for v in inst["t2"].values())
    return ("Good. Now make it handle any pattern. The streams in this round overlap several patterns at "
            "once, and some digits are random. Here are the first %d digits of a few streams like the hidden "
            "ones:\n\n" % EX_LEN + ex + "\n\n"
            f"Same interface. Hidden test: {n} streams of {L_STREAM} digits in {len(inst['t2'])} groups, in "
            "increasing difficulty; the score counts correct guesses from digit "
            f"{WARM} on, against the best accuracy possible on each stream. Your predictor runs in its own "
            "process and sees only the digits that have already been revealed. The whole test run must finish "
            f"within {TIMEOUT_S} s of CPU time on one core. Reply with the complete updated code.\n")


# ---------------------------------------------------------------------------- grading
def stream_score(acc, oracle, anchor, role):
    """floor: chance..oracle -> 0..1.  climb/edge: chance 0, generic n-gram ANCHOR_A, oracle 1."""
    c = 1 / 3
    if role == "floor":
        return max(0.0, min(1.0, (acc - c) / (oracle - c)))
    if acc <= c:
        return 0.0
    if acc < anchor:
        return ANCHOR_A * (acc - c) / (anchor - c)
    return min(1.0, ANCHOR_A + (1 - ANCHOR_A) * (acc - anchor) / (oracle - anchor))


def _cfg(tiers, specs):
    return {"warm": WARM, "timeout_s": TIMEOUT_S, "wall_s": WALL_S,
            "tiers": {name: {"role": specs[name]["role"], "w": specs[name]["w"],
                             "streams": [("".join(map(str, st["digits"])), st["oracle"], st["anchor"]) for st in sts]}
                      for name, sts in tiers.items()}}


RUNNER = r"""
import os, sys, json, resource
_cpu = int(sys.stdin.readline())
resource.setrlimit(resource.RLIMIT_CPU, (_cpu, _cpu))
_out = os.fdopen(os.dup(1), "w")
_devnull = open(os.devnull, "w")
os.dup2(_devnull.fileno(), 1)
sys.stdout = _devnull
_code = json.loads(sys.stdin.readline())
_ns = {}
try:
    exec(compile(_code, "solution", "exec"), _ns)
    _P = _ns["Predictor"]
    _out.write("OK\n")
except BaseException as _e:
    _out.write("ERR %s: %s\n" % (type(_e).__name__, str(_e)[:100]))
    _out.flush()
    sys.exit(1)
_out.flush()
_p = None
def _guess():
    try:
        g = _p.predict()
        g = int(g)
        return g if g in (0, 1, 2) else 0
    except BaseException:
        return 0
for _line in sys.stdin:
    _line = _line.strip()
    if _line == "c":
        import time as _t
        _out.write("%.1f\n" % _t.process_time())
        _out.flush()
        continue
    if _line == "s":
        try:
            _p = _P()
        except BaseException:
            _p = None
    elif _p is not None:
        try:
            _p.update(int(_line))
        except BaseException:
            pass
    _out.write(str(_guess() if _p is not None else 0) + "\n")
    _out.flush()
"""


def run_isolated(code: str, cfg: dict) -> dict:
    """Grade ``code`` against cfg (see _cfg) with the predictor in its own bwrap process.
    -> {"tiers": {name: score}, "points": 0..100, "rate": 0..1, "acc": {name: [..]}, "cpu_s", "detail"}"""
    import select
    import subprocess
    import tempfile
    import time
    from pathlib import Path
    from .. import graders
    tiers = cfg["tiers"]
    out = {"tiers": {n: 0.0 for n in tiers}, "acc": {}, "cpu_s": None, "detail": ""}

    def fin(detail):
        pts = sum(t["w"] * out["tiers"][n] for n, t in tiers.items())
        tw = sum(t["w"] for t in tiers.values())
        out["points"], out["rate"], out["detail"] = round(100 * pts / tw, 2), round(pts / tw, 4), out["detail"] or detail
        return out
    if not code:
        out["detail"] = "no code in response"
        return fin("")
    graders.require_sandbox()
    with tempfile.TemporaryDirectory(prefix="patgen-") as td:
        script = Path(td) / "runner.py"
        script.write_text(RUNNER, encoding="utf-8")
        proc = subprocess.Popen(graders._sandbox_cmd(str(script), td), stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, cwd=td,
                                env=graders._child_env(), bufsize=0)
        deadline = time.monotonic() + cfg["wall_s"]

        def readline():
            buf = b""
            while not buf.endswith(b"\n"):
                left = deadline - time.monotonic()
                if left <= 0 or not select.select([proc.stdout], [], [], left)[0]:
                    raise TimeoutError("wall limit")
                ch = proc.stdout.read(1)
                if not ch:
                    raise EOFError("predictor process ended (CPU limit or crash)")
                buf += ch
            return buf.decode("ascii", "replace").strip()
        try:
            proc.stdin.write((str(int(cfg["timeout_s"])) + "\n" + json.dumps(code) + "\n").encode())
            first = readline()
            if first != "OK":
                out["detail"] = "setup " + first
                return fin("")
            for name, t in tiers.items():
                vals, accs = [], []
                for digits, oracle, anchor in t["streams"]:
                    hit = tot = 0
                    g = None
                    try:
                        proc.stdin.write(b"s\n")
                        for i, ch in enumerate(digits):
                            g = int(readline())
                            d = ord(ch) - 48
                            if i >= cfg["warm"]:
                                tot += 1
                                hit += g == d
                            if i + 1 < len(digits):
                                proc.stdin.write(b"%d\n" % d)
                    except (TimeoutError, EOFError, BrokenPipeError) as e:
                        out["detail"] = f"{name}: {e}"
                        out["tiers"][name] = (sum(vals)) / len(t["streams"]) if vals else 0.0
                        out["acc"][name] = accs
                        return fin("")
                    acc = hit / tot
                    accs.append(round(acc, 4))
                    vals.append(stream_score(acc, oracle, anchor, t["role"]))
                out["tiers"][name] = round(sum(vals) / len(vals), 4)
                out["acc"][name] = accs
            try:
                proc.stdin.write(b"c\n")
                out["cpu_s"] = float(readline())
            except (TimeoutError, EOFError, BrokenPipeError, ValueError):
                pass
        finally:
            try:
                proc.stdin.close()
            except OSError:
                pass
            proc.kill()
            proc.wait()
    return fin("")


def materialize(g: dict, *, release_seed: str, case_id: str) -> dict:
    level = int(g.get("level", 2))
    var = g.get("variant") or "t1"
    key = instance_key(g, release_seed)
    inst = instance(level, key)
    tiers = inst[var]
    specs = {n: {"role": "floor", "w": 1} for n in T1_TIERS} if var == "t1" else T2_TIERS
    out = {"system": SYSTEM, "temperature": 0.0, "max_tokens": 16384, "grader": "patgen_iso",
           "grader_config": _cfg(tiers, specs),
           "meta": {"oracle": {n: [round(s["oracle"], 4) for s in v] for n, v in tiers.items()},
                    "anchor": {n: [round(s["anchor"], 4) for s in v] for n, v in tiers.items()},
                    "hard_item_cap": 65536}}
    if var == "t1":
        out["prompt"] = prompt_t1(inst)
    else:
        out["history"] = [{"role": "user", "content": prompt_t1(inst)},
                          {"role": "assistant", "content": g.get("t1_answer") or "(no answer)"}]
        out["prompt"] = prompt_t2(inst)
    return out


def instance_key(g, release_seed):
    from .. import gen as _gen
    # independent of case id / variant, so turn 1, turn 2 and every model see the same streams
    return _gen.instance_int(FAMILY, release_seed, "patgen", int(g.get("level", 2)), g.get("key"))


def reference(g: dict, *, release_seed: str, case_id: str) -> str:
    from pathlib import Path
    return "```python\n" + (Path(__file__).with_name("_patgen_ref.py")).read_text(encoding="utf-8") + "```"


def mutants(g: dict, *, release_seed: str, case_id: str) -> list[str]:
    const = "class Predictor:\n    def predict(self): return 0\n    def update(self, d): pass\n"
    return ["```python\n" + const + "```"]


def grade(answer: str, materialized: dict) -> float:
    from .. import graders
    return float(graders.grade_patgen_iso(answer, materialized["grader_config"]).get("rate") or 0.0)


def score_code(code: str, tiers: dict, specs: dict | None = None) -> dict:
    """Offline exact tier scores (same isolated runner): for the analysis script and the baselines."""
    specs = specs or {n: T2_TIERS.get(n) or {"role": "floor", "w": 1} for n in tiers}
    return run_isolated(code, _cfg(tiers, specs))


if __name__ == "__main__":
    m = materialize({"type": FAMILY, "level": 2, "key": sys.argv[1] if len(sys.argv) > 1 else "p1",
                     "variant": sys.argv[2] if len(sys.argv) > 2 else "t2"}, release_seed="dev", case_id="X")
    print(m.get("history", [{}])[0].get("content", ""))
    print("----")
    print(m["prompt"])
    print(json.dumps(m["meta"]))
