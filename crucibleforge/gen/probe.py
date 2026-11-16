"""PROBE (CrucibleForge port of the fast-fail question ff/q/probe.py, branch t-probe @ 4528b74): characterise a hidden mechanical device
through an isolated measurement interface. Only tier 3 is ported: the "nonlinear shock absorber bench" (cubic stiffening + dry friction +
linear damping, class NOT stated). The model's program is run in the engine sandbox; the hidden system lives in the GRADER process and answers
``measure(amp, t)`` requests over stdin/stdout JSON lines (noise sd 0.004, amp in [0.2, 1], t in [0, 10]); the program then predicts 10
held-out cases that lie outside the measurable range (amp 1.5-3, t up to 16), so it has to identify the mechanism.

LEVELS: 3 = the ff tier (numbers kept; tier 1 is not ported). ``ffseed: N`` reproduces ff instance N byte for byte.
Item score = ff points / 100: q = accuracy x measurement factor, accuracy = log(0.8/r)/log(16) clipped to 0..1 (r = RMSE / sd of the 10 true
values), factor 1.0 up to 6 measurements then 1 - log2(n/6)/8.5 (floor 0.1); invalid 0, the ff baseline (500 dense readings, coarse grid fit
without the friction term; ``baseline``) 0.30, the reference (27 readings, Nelder-Mead fit of all four constants) 1.0 (q_ref measured by this
grader; ff q_ref of seed 1 is 0.224, so the item is a low-q ladder), a valid answer below baseline floors at 0.10.
Checks (``results``, so ``element: check`` works): ten, ``pred/q0`` .. ``pred/q9``, one per held-out case, weight 0.1 each. Credit of case i =
the item ladder applied to q_i = accuracy_i x factor, accuracy_i = the accuracy formula with r_i = |error_i| / s (the same s the prompt defines;
r^2 = mean r_i^2), so a check carries the measurement factor too. sum(weight x credit) is NOT the item score (the accuracy is a log of an RMSE);
the item score is the ff points ladder on the whole. An invalid answer (malformed, over the CPU limit, no answer) scores 0 on all ten.
The generation, prompt, reference and baseline live in ``_probe_ff.py``, the floors in ``_probe_floors.py``; both are pinned below
(chains.revision digests only this file)."""
import functools
import json
import math
import os
import resource
import secrets
import subprocess
import tempfile
import threading
from pathlib import Path

from .. import gen as _gen
from . import _det
from . import _probe_ff as _F
from . import _probe_floors as _FL

FAMILY = "probe"
CPU_S = float(_F.CPU_S)           # stated in the prompt (20 s of process CPU)
MAX_TOKENS = 98304                # single-item cell; the largest ff reply was 56.8k (Fable)
#: tier = ff tier; answer_tokens = reference-answer tokens (chars/4), re-measured in tests/test_gen_probe.py
LEVELS = {3: {"tier": 3, "answer_tokens": 622}}
VARIANTS = ()
POINTS_BASE, POINTS_REF, POINTS_FLOOR = 30.0, 100.0, 10.0
N_CHECKS = 10

#: pins of the helper modules (tests/test_gen_probe.py::test_helper_pins fails until this line changes with them)
_FF_SHA = "0990ceb74eb467ea"
_FLOORS_SHA = "cac85128e68dc6b0"
_DET_SHA = "77fb46314ef63360"           # gen/_det.py: the shared deterministic launcher (one reply, one grade)

# In-sandbox driver. Line 1 of stdin is the setup (code, queries); the following lines are the grader's replies to ``measure``. The driver keeps
# the two pipes on private dups and points sys.stdin/sys.stdout elsewhere, as the ff driver did, so a print() in the model's module cannot
# corrupt the channel. The hidden system is not in this process.
_DRIVER = r'''
import json, os, resource, signal, sys, types
_w = os.fdopen(os.dup(1), "w", buffering=1)
_r = os.fdopen(os.dup(0), "r")
sys.stdout = sys.stderr
sys.stdin = open(os.devnull)
def _alarm(*a):
    raise BaseException("alarm")
signal.signal(signal.SIGALRM, _alarm)
def _cpu():
    u = resource.getrusage(resource.RUSAGE_SELF)
    return u.ru_utime + u.ru_stime
def _bye(msg):
    _w.write(json.dumps({"fail": msg[:200], "cpu": _cpu()}) + "\n"); _w.flush(); os._exit(0)
def measure(*a):
    _w.write(json.dumps({"m": list(a)}) + "\n"); _w.flush()
    line = _r.readline()
    if not line:
        os._exit(0)
    r = json.loads(line)
    if "err" in r:
        raise ValueError(r["err"])
    return r["v"]
_d = json.loads(_r.readline())
solver = types.ModuleType("solver")
sys.modules["solver"] = solver
try:
    signal.alarm(8)
    exec(compile(_d["code"], "solver.py", "exec"), solver.__dict__)
    signal.alarm(0)
    _f = getattr(solver, "solve")
except BaseException as _e:
    signal.alarm(0)
    _bye(type(_e).__name__ + ": " + str(_e))
try:
    signal.alarm(int(_d["cpu"]) + 3)
    _ans = _f(measure, _d["queries"])
    signal.alarm(0)
    _w.write(json.dumps({"done": json.loads(json.dumps(_ans, default=str)), "cpu": _cpu()}) + "\n"); _w.flush()
except BaseException as _e:
    signal.alarm(0)
    _bye(type(_e).__name__ + ": " + str(_e))
os._exit(0)
'''


def _run(code: str, inst: dict, cpu_s: float = CPU_S) -> dict:
    """Run ``code`` in the engine's bwrap sandbox and serve its ``measure`` calls from the hidden system here. -> {ok, ans, n, cpu_s, err}"""
    from .. import graders as _g
    if not (code or "").strip():
        return {"ok": False, "ans": None, "n": 0, "cpu_s": 0.0, "err": "no code in response"}
    _g.require_sandbox()
    pub, hid = inst["public"], inst["hidden"]
    lim = int(cpu_s) + 1

    def pre():
        resource.setrlimit(resource.RLIMIT_CPU, (lim, lim + 1))
        resource.setrlimit(resource.RLIMIT_AS, (2 << 30, 2 << 30))
    n, ans, fail, cache, own = 0, None, "", {}, 0.0
    with tempfile.TemporaryDirectory(prefix="bench-probe-") as td:
        path = Path(td) / "driver.py"
        path.write_text(_det.with_prelude(_DRIVER, "probe"), encoding="utf-8")
        ef = open(Path(td) / "err.txt", "w+", encoding="utf-8")
        r0 = resource.getrusage(resource.RUSAGE_CHILDREN)
        p = subprocess.Popen(_det.det_cmd(_g._sandbox_cmd(str(path), td)), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=ef, text=True,
                             cwd=td, env=_det.det_env(), preexec_fn=pre)
        timer = threading.Timer(cpu_s * 4 + 60, p.kill)
        timer.start()
        try:
            p.stdin.write(json.dumps({"code": code, "queries": pub["queries"], "cpu": cpu_s}) + "\n")
            p.stdin.flush()
            while True:
                line = p.stdout.readline()
                if not line:
                    break
                try:
                    msg = json.loads(line)
                except ValueError:
                    msg = None
                if isinstance(msg, dict) and "done" in msg:
                    ans, own = msg["done"], float(msg.get("cpu") or 0)
                    break
                if isinstance(msg, dict) and "fail" in msg:
                    fail, own = str(msg["fail"]), float(msg.get("cpu") or 0)
                    break
                n += 1
                reply = {"err": "bad request"}
                if n > _F.CAP:
                    reply = {"err": f"measurement cap {_F.CAP} reached"}
                elif isinstance(msg, dict) and isinstance(msg.get("m"), list):
                    try:
                        reply = {"v": _F._measure(hid, msg["m"], n, cache)}
                    except (ValueError, TypeError) as e:
                        reply = {"err": str(e)}
                try:
                    p.stdin.write(json.dumps(reply) + "\n")
                    p.stdin.flush()
                except (BrokenPipeError, OSError):
                    break
        finally:
            timer.cancel()
            try:
                p.stdin.close()
            except OSError:
                pass
            try:
                p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                p.kill()
                p.wait()
        r1 = resource.getrusage(resource.RUSAGE_CHILDREN)
        ef.seek(0)
        err = ef.read()[-300:]
        ef.close()
    cpu = max(own, (r1.ru_utime + r1.ru_stime) - (r0.ru_utime + r0.ru_stime))   # the driver reports its own process CPU
    if cpu > cpu_s:
        return {"ok": False, "ans": None, "n": n, "cpu_s": round(cpu, 2), "err": f"over CPU limit ({cpu:.1f}s > {cpu_s:g}s)"}
    if ans is None:
        return {"ok": False, "ans": None, "n": n, "cpu_s": round(cpu, 2), "err": fail or ("no answer: " + (err.strip()[-200:] or "program ended"))}
    return {"ok": True, "ans": ans, "n": n, "cpu_s": round(cpu, 2), "err": ""}


# ---------------------------------------------------------------- scoring
def _predictions(hid, ans):
    """the 10 predictions as floats, or None when the answer is malformed (the ff rule)"""
    pr = ans.get("pred") if isinstance(ans, dict) else ans
    if not isinstance(pr, list) or len(pr) != len(hid["truth"]):
        return None
    for v in pr:
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
            return None
    return [float(v) for v in pr]


def _acc_of_r(r):
    r = max(r, 1e-9)
    return min(1.0, max(0.0, math.log(_F.T3_R0 / r) / math.log(_F.T3_R0 / 0.05)))


def score_checks(inst, ans, n, qb=None, qr=None):
    """-> (q, acc, f, results). q = accuracy x factor (the ff q; None for a malformed answer). results: ten checks pred/q<i>."""
    hid = inst["hidden"]
    pr = _predictions(hid, ans)
    ids = [f"pred/q{i}" for i in range(N_CHECKS)]
    if pr is None:
        return None, 0.0, 0.0, [{"id": i, "credit": 0.0, "pass": False, "weight": 1 / N_CHECKS, "group": "pred", "detail": "answer malformed"} for i in ids]
    tr = hid["truth"]
    m = sum(tr) / len(tr)
    sd = math.sqrt(sum((x - m) ** 2 for x in tr) / len(tr))
    rmse = math.sqrt(sum((a - b) ** 2 for a, b in zip(pr, tr)) / len(tr))
    acc = _acc_of_r(rmse / sd)
    f = _F.factor(n)
    q = acc * f
    res = []
    for i, (a, b) in enumerate(zip(pr, tr)):
        ai = _acc_of_r(abs(a - b) / sd)
        qi = ai * f
        pts = points(qi, qb, True, qr) if qb is not None else 100.0 * qi
        c = min(1.0, max(0.0, pts / POINTS_REF))
        qa = inst["public"]["queries"][i]
        res.append({"id": ids[i], "credit": round(c, 6), "pass": c >= .999, "weight": 1 / N_CHECKS, "group": "pred",
                    "detail": f"query (amp {qa[0]}, t {qa[1]}): error {abs(a - b):.4f} = {abs(a - b) / sd:.2f} s, accuracy {ai:.2f} x factor {f:.2f}"})
    return q, acc, f, res


def judge(inst: dict, code: str, anchors=None) -> dict:
    """-> {valid, q, acc, f, n, cpu_s, note, results} for one answer (code = the module)"""
    r = _run(code, inst)
    qb, qr = anchors if anchors else (None, None)
    if not r["ok"]:
        _, _, _, res = score_checks(inst, None, r["n"])
        for x in res:
            x["detail"] = "invalid: " + r["err"][:80]
        return {"valid": False, "q": 0.0, "acc": 0.0, "f": 0.0, "n": r["n"], "cpu_s": r["cpu_s"], "note": r["err"], "results": res}
    q, acc, f, res = score_checks(inst, r["ans"], r["n"], qb, qr)
    if q is None:
        return {"valid": False, "q": 0.0, "acc": 0.0, "f": 0.0, "n": r["n"], "cpu_s": r["cpu_s"], "note": "answer malformed", "results": res}
    return {"valid": True, "q": round(q, 4), "acc": round(acc, 3), "f": round(f, 3), "n": r["n"], "cpu_s": r["cpu_s"], "results": res,
            "note": f"measurements {r['n']}, accuracy {acc:.2f}, factor {f:.2f}"}


def check(inst, code):
    """The ff contract: {"q", "valid", "cpu_s", "n", "acc", "f", "note"}."""
    j = judge(inst, code)
    return {k: j[k] for k in ("q", "valid", "cpu_s", "n", "acc", "f", "note")}


def points(q: float, q_base: float, valid: bool = True, q_ref: float = 1.0) -> float:
    if not valid:
        return 0.0
    if q_ref <= q_base:
        return POINTS_BASE
    return round(max(POINTS_FLOOR, POINTS_BASE + (POINTS_REF - POINTS_BASE) * (q - q_base) / (q_ref - q_base)), 1)


_CACHE: dict = {}
_LOCK = threading.Lock()


def _instance(seed: int, tier: int) -> dict:
    with _LOCK:
        if (seed, tier) not in _CACHE:
            _CACHE[(seed, tier)] = _F.gen(seed, tier)
        return _CACHE[(seed, tier)]


def _seed_for(gen: dict, release_seed: str, case_id: str) -> int:
    """``ffseed`` reproduces the fast-fail instance of that seed; otherwise a 64-bit int from the contract seed"""
    if gen.get("ffseed") is not None:
        return int(gen["ffseed"])
    return 1 + _gen.instance_int(FAMILY, release_seed, case_id, gen.get("level"), gen.get("key"))


def _level(gen: dict) -> int:
    lv = int(gen.get("level", 3))
    if lv not in LEVELS:
        raise ValueError(f"probe: no level {lv} (levels {sorted(LEVELS)})")
    return lv


def _inst_of(gen: dict, release_seed: str, case_id: str) -> dict:
    return _instance(_seed_for(gen, release_seed, case_id), LEVELS[_level(gen)]["tier"])


@functools.lru_cache(maxsize=None)
def _anchors(seed: int, tier: int):
    """(q_base, q_ref): the q of the ff baseline (500 dense readings) and of the reference under this grader"""
    inst = _instance(seed, tier)
    return check(inst, _F.baseline(inst))["q"], check(inst, _F.reference(inst))["q"]


def materialize(gen: dict, *, release_seed: str, case_id: str) -> dict:
    lv = _level(gen)
    seed = _seed_for(gen, release_seed, case_id)
    inst = _instance(seed, LEVELS[lv]["tier"])
    return json.loads(json.dumps({
        "prompt": inst["prompt"], "temperature": 0.0, "max_tokens": MAX_TOKENS,
        "grader": "probe",
        "grader_config": {"family": FAMILY, "seed": seed, "tier": LEVELS[lv]["tier"], "cpu_s": CPU_S},
        "meta": {"tier": LEVELS[lv]["tier"], "n_checks": N_CHECKS, "answer_tokens": LEVELS[lv]["answer_tokens"]},
        "difficulty": "hard"}))


def reference_code(inst: dict) -> str:
    return _F.reference(inst)


def reference(gen: dict, *, release_seed: str, case_id: str) -> str:
    return "```python\n" + reference_code(_inst_of(gen, release_seed, case_id)) + "\n```"


def mutants(gen: dict, *, release_seed: str, case_id: str) -> list[str]:
    fl = _FL.floors(_inst_of(gen, release_seed, case_id))
    return ["```python\n" + fl[k] + "\n```" for k in ("zero_const", "zero_lin", "dense2000", "baseline")]


GRADE_IS_ENGINE = True


def grade_probe(response_text: str, cfg: dict, finish_reason=None) -> dict:
    """Engine grader "probe": cfg = {seed, tier, cpu_s}. The reply's last python block is the module; ``results`` holds one entry per
    held-out case (id, credit, pass, weight, group)."""
    from .. import graders as _g
    seed, tier = int(cfg["seed"]), int(cfg["tier"])
    inst = _instance(seed, tier)
    code = _g.extract_python(response_text or "", finish_reason)
    j = judge(inst, code, _anchors(seed, tier))
    if not j["valid"]:
        return {"grade": "fail", "detail": j["note"] + _det.py_note(), "score": 0.0, "rate": 0.0, "points": 0.0, "q": 0.0, "n": j["n"],
                "cpu_s": j["cpu_s"], "results": j["results"]}
    qb, qr = _anchors(seed, tier)
    pts = points(j["q"], qb, True, qr)
    s = round(min(1.0, pts / POINTS_REF), 4)
    return {"grade": "pass" if s >= 0.999 else "fail",
            "detail": f"q={j['q']:.3f} (anchor {qb:.3f}, reference {qr:.3f}); {pts} points; {j['note']}" + _det.py_note(),
            "score": s, "rate": s, "points": pts, "q": j["q"], "anchor_q": qb, "n": j["n"], "acc": j["acc"], "f": j["f"],
            "cpu_s": j["cpu_s"], "results": j["results"]}


def grade(answer: str, materialized: dict) -> float:
    return float(grade_probe(answer, materialized["grader_config"])["score"])
