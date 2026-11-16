"""NETSURGEON pocket (CrucibleForge port of the fast-fail question ff/q/netsurgeon.py, branch t-netsurgeon @ 11b122c): the model gets a tiny
frozen 8-16-4 net (pure Python) and writes ``edit(net, probe, spec)`` that patches its weights. One task per item: REPAIR (layer 1 or 2 was
damaged: rows permuted / signs flipped / rows scaled) or REWRITE (a key region must map to class c'). The edit is run in the engine sandbox;
judging happens outside it on fresh hidden inputs.

LEVELS: 1 = the ff "pocket" tier (the only one ported). ``ffseed: N`` reproduces ff instance N byte for byte (ff kind = N % 4: 1 perm, 2 flip,
3 scale, 0 rewrite); without it the seed comes from the contract and its kind is random per item; ``variant`` (rewrite|perm|flip|scale) forces the kind.

Checks (``results``, so ``element: check`` works). REWRITE: five checks that sum to the ff q exactly (q = efficacy x specificity):
``hit/narrow`` + ``hit/wide`` (new-class rate on key inputs of sd 1 / sd 2, credit = rate x specificity) and ``keep/far`` + ``keep/near`` +
``keep/wide`` (unchanged-prediction share on non-key inputs far from / just outside / wide around the region, credit = share x efficacy),
weights .25 .25 and .5 x (200, 200, 150)/550. REPAIR: two checks over the hidden test inputs, split by what the damage did to them: ``restore/broken`` (readings the damaged net gets
wrong; credit = share the patched net classifies as the undamaged net does) and ``restore/intact`` (readings the damaged net still got right, absent when the damage
left none; credit = share still matching, times restore/broken, so leaving the net untouched earns 0 on both), weights = the two subsets' shares of the
test inputs (sum(weight x credit) = agreement = the ff q when the repair is complete). Touching the other layer, or an invalid net, zeroes every check (the prompt says so).
Item score = ff points / 100 (same ladder as ff): invalid 0, the ff baseline (SGD on the public probe; ``baseline``) 0.30, the reference 1.0
(q_ref measured by this grader), a valid answer below baseline floors at 0.10. Generation, prompt, reference and baseline live in
``_netsurgeon_ff.py``, the nn scaffold in ``_netsurgeon_nn.py``, the red-team floors in ``_netsurgeon_floors.py``; all pinned below
(chains.revision digests only this file)."""
import copy
import functools
import json
import secrets
import subprocess
import tempfile
import threading
from pathlib import Path

from .. import gen as _gen
from . import _det
from . import _netsurgeon_ff as _F
from . import _netsurgeon_nn as nn
from . import _netsurgeon_floors as _FL

FAMILY = "netsurgeon"
CPU_S = float(_F.CPU_S)           # stated in the prompt (10 s)
MAX_TOKENS = 98304     # single-item cell: the largest ff reply was 44k (MiniMax, invalid), the largest 100-point one 40.8k
#: tier = ff tier; answer_tokens = reference-answer tokens (chars/4), re-measured in tests/test_gen_netsurgeon.py
LEVELS = {1: {"tier": 1, "answer_tokens": 134}}
VARIANTS = tuple(_F.KINDS)
POINTS_BASE, POINTS_REF, POINTS_FLOOR = 30.0, 100.0, 10.0

#: pins of the helper modules (tests/test_gen_netsurgeon.py::test_helper_pins fails until this line changes with them)
_FF_SHA = "ede92bb52bf72f93"
_NN_SHA = "866be65e4a144004"
_FLOORS_SHA = "c494b90869744314"
_DET_SHA = "77fb46314ef63360"           # gen/_det.py: the shared deterministic launcher (one reply, one grade)

# In-sandbox harness. Code, net, probe, spec and the verdict marker arrive on stdin; ``nn`` is built in memory (its load() returns the net, no
# file to read), the verdict line goes straight to fd 1 and the process ends with os._exit (atexit/stdout tricks cannot forge it).
_HARNESS = r'''
import copy, json, os, signal, sys, types
_d = json.loads(sys.stdin.buffer.read().decode("utf-8"))
_M = _d["mark"]
def _fin(o):
    os.write(1, ("\n" + _M + json.dumps(o) + "\n").encode("utf-8", "replace"))
    os._exit(0)
def _alarm(*a):
    raise BaseException("alarm")
signal.signal(signal.SIGALRM, _alarm)
_nn = types.ModuleType("nn")
exec(compile(_d["nn"], "nn.py", "exec"), _nn.__dict__)
_NET = _d["net"]
_nn.load = lambda path="in.json": copy.deepcopy(_NET)
sys.modules["nn"] = _nn
solver = types.ModuleType("solver")
sys.modules["solver"] = solver
try:
    signal.alarm(8)
    exec(compile(_d["code"], "solver.py", "exec"), solver.__dict__)
    signal.alarm(0)
    _f = getattr(solver, "edit")
    solver.CPU_LIMIT_S = float(_d["cpu"])
except BaseException as _e:
    signal.alarm(0)
    _fin({"err": (type(_e).__name__ + ": " + str(_e))[:200]})
try:
    signal.alarm(int(_d["cpu"]) + 3)
    _r = _f(copy.deepcopy(_NET), _d["probe"], _d["spec"])
    signal.alarm(0)
    _fin({"out": json.loads(json.dumps(_r, default=str))})
except BaseException as _e:
    signal.alarm(0)
    _fin({"err": (type(_e).__name__ + ": " + str(_e))[:200]})
'''


def _run(code: str, inst: dict, cpu_s: float = CPU_S) -> dict:
    """Run ``code`` in the engine's bwrap sandbox on the public net/probe/spec. -> {ok, out, cpu_s, err}"""
    import resource
    from .. import graders as _g
    if not (code or "").strip():
        return {"ok": False, "out": None, "cpu_s": 0.0, "err": "no code in response"}
    _g.require_sandbox()
    mark = "BENCH_NETSURGEON_" + secrets.token_hex(12) + ":"
    p = inst["public"]
    blob = json.dumps({"code": code, "nn": _F.NN_SRC, "net": p["net"], "probe": p["probe"], "spec": p["spec"], "mark": mark,
                       "cpu": cpu_s}).encode("utf-8", "replace")
    lim = int(cpu_s) + 1

    def pre():
        resource.setrlimit(resource.RLIMIT_CPU, (lim, lim + 1))
        resource.setrlimit(resource.RLIMIT_AS, (2 << 30, 2 << 30))
    with tempfile.TemporaryDirectory(prefix="bench-netsurgeon-") as td:
        path = Path(td) / "harness.py"
        path.write_text(_det.with_prelude(_HARNESS, "netsurgeon"), encoding="utf-8")
        try:
            r0 = resource.getrusage(resource.RUSAGE_CHILDREN)
            pr = subprocess.run(_det.det_cmd(_g._sandbox_cmd(str(path), td)), input=blob, capture_output=True, timeout=cpu_s * 4 + 30, cwd=td,
                                env=_det.det_env(), preexec_fn=pre)
            r1 = resource.getrusage(resource.RUSAGE_CHILDREN)
            out, rc, err = pr.stdout.decode("utf-8", "replace"), pr.returncode, pr.stderr.decode("utf-8", "replace")[-300:]
        except subprocess.TimeoutExpired:
            return {"ok": False, "out": None, "cpu_s": cpu_s * 4, "err": "wall timeout"}
    cpu = (r1.ru_utime + r1.ru_stime) - (r0.ru_utime + r0.ru_stime)
    line = next((ln for ln in reversed(out.splitlines()) if ln.startswith(mark)), None)
    if line is None:
        return {"ok": False, "out": None, "cpu_s": round(cpu, 2),
                "err": ("over CPU limit; " if cpu > cpu_s else "") + "crash: " + ((err or f"rc={rc}").strip()[-200:])}
    try:
        res = json.loads(line[len(mark):])
    except ValueError:
        return {"ok": False, "out": None, "cpu_s": round(cpu, 2), "err": "unreadable output"}
    if "err" in res:
        return {"ok": False, "out": None, "cpu_s": round(cpu, 2), "err": res["err"]}
    if cpu > cpu_s:
        return {"ok": False, "out": None, "cpu_s": round(cpu, 2), "err": f"over CPU limit ({cpu:.1f}s > {cpu_s:g}s)"}
    return {"ok": True, "out": res["out"], "cpu_s": round(cpu, 2), "err": ""}


# ---------------------------------------------------------------- the checks
def _names(inst):
    """(id, weight, group) of every check of the item"""
    h = inst["hidden"]
    if h["kind"] == "rewrite":
        return ([("hit/narrow", .25, "hit"), ("hit/wide", .25, "hit")]
                + [(i, round(.5 * n / 550, 6), "keep") for i, n in (("keep/far", 200), ("keep/near", 200), ("keep/wide", 150))])
    nb = sum(1 for a, b in zip(nn.predict(h["damaged"], h["test"]), nn.predict(h["orig"], h["test"])) if a != b)
    n = len(h["test"])
    out = [("restore/broken", round(nb / n, 6), "restore")]
    if nb < n:     # a layer-2 permutation can leave no reading right: then there is no intact check
        out.append(("restore/intact", round(1 - nb / n, 6), "restore"))
    return out


def score_checks(inst: dict, net_raw):
    """-> (q, results): q is the ff q (REWRITE: sum(weight x credit) == q; REPAIR: ff q is the raw agreement, the results gate the intact
    share on the broken share, see the module docstring); results = [{id, credit, pass, weight, group, detail}]; net_raw None = invalid net."""
    h = inst["hidden"]
    names = _names(inst)
    zero = lambda why: (0.0, [{"id": i, "credit": 0.0, "pass": False, "weight": w, "group": g, "detail": why} for i, w, g in names])
    net = _F._clean(net_raw) if net_raw is not None else None
    if net is None:
        return zero("bad net (shape/finite/below 1e12)")
    orig = h["orig"]
    if h["kind"] == "rewrite":
        c = h["cls"]
        hit = lambda X: sum(1 for p in nn.predict(net, X) if p == c) / len(X)
        keep = lambda X: sum(1 for a, b in zip(nn.predict(net, X), nn.predict(orig, X)) if a == b) / len(X)
        hA, hB = hit(h["keysA"]), hit(h["keysB"])
        kF, kN, kW = keep(h["ctlA"]), keep(h["ctlN"]), keep(h["ctlB"])
        eff = .5 * hA + .5 * hB
        spe = (200 * kF + 200 * kN + 150 * kW) / 550
        cred = {"hit/narrow": hA * spe, "hit/wide": hB * spe, "keep/far": kF * eff, "keep/near": kN * eff, "keep/wide": kW * eff}
        det = {"hit/narrow": f"new class on {hA:.0%} of key inputs (sd 1), x specificity {spe:.2f}",
               "hit/wide": f"new class on {hB:.0%} of key inputs (sd 2), x specificity {spe:.2f}",
               "keep/far": f"{kF:.0%} unchanged far from the region, x efficacy {eff:.2f}",
               "keep/near": f"{kN:.0%} unchanged just outside the region, x efficacy {eff:.2f}",
               "keep/wide": f"{kW:.0%} unchanged on wide-spread non-key inputs, x efficacy {eff:.2f}"}
        res = [{"id": i, "credit": round(cred[i], 6), "pass": cred[i] >= .999, "weight": w, "group": g, "detail": det[i]} for i, w, g in names]
        return eff * spe, res
    k = h["layer"] - 1
    if _F._clean(inst["public"]["net"])["layers"][1 - k] != net["layers"][1 - k]:
        return zero("other layer changed (score 0)")
    X, y0 = h["test"], nn.predict(orig, h["test"])
    yn, yd = nn.predict(net, X), nn.predict(h["damaged"], X)
    broken = [j for j in range(len(X)) if yd[j] != y0[j]]
    intact = [j for j in range(len(X)) if yd[j] == y0[j]]
    fixed = sum(1 for j in broken if yn[j] == y0[j]) / len(broken)
    kept = sum(1 for j in intact if yn[j] == y0[j]) / len(intact) if intact else 1.0
    cred = {"restore/broken": fixed, "restore/intact": kept * fixed}
    det = {"restore/broken": f"{fixed:.0%} of the {len(broken)} readings the damage had flipped are classified as before",
           "restore/intact": f"{kept:.0%} of the {len(intact)} readings the damage left alone still match, x restore/broken {fixed:.2f}"}
    res = [{"id": i, "credit": round(cred[i], 6), "pass": cred[i] >= .999, "weight": w, "group": g, "detail": det[i]} for i, w, g in names]
    ag_all = sum(1 for a, b in zip(yn, y0) if a == b) / len(X)
    return ag_all, res


def judge(inst: dict, code: str) -> dict:
    """-> {valid, q, cpu_s, note, results} for one answer (code = the module)"""
    r = _run(code, inst)
    if not r["ok"]:
        q, res = score_checks(inst, None)
        for x in res:
            x["detail"] = "invalid: " + r["err"][:80]
        return {"valid": False, "q": 0.0, "cpu_s": r["cpu_s"], "note": r["err"], "results": res}
    q, res = score_checks(inst, r["out"])
    if q == 0.0 and not any(x["pass"] for x in res) and res and res[0]["detail"].startswith(("bad net", "other layer")):
        return {"valid": res[0]["detail"].startswith("other"), "q": 0.0, "cpu_s": r["cpu_s"], "note": res[0]["detail"], "results": res}
    return {"valid": True, "q": q, "cpu_s": r["cpu_s"], "results": res,
            "note": f"q={q:.3f} fail:" + ",".join(x["id"] for x in res if not x["pass"])}


def check(inst, code):
    """The ff contract: {"q", "valid", "cpu_s", "note"}."""
    j = judge(inst, code)
    return {k: j[k] for k in ("q", "valid", "cpu_s", "note")}


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
    """``ffseed`` reproduces the fast-fail instance of that seed; otherwise a positive int from the contract seed whose ff kind
    (seed % 4) is the ``variant`` when one is given"""
    if gen.get("ffseed") is not None:
        return int(gen["ffseed"])
    s = 1 + _gen.instance_int(FAMILY, release_seed, case_id, gen.get("level"), gen.get("key"))
    v = gen.get("variant")
    if v is not None:
        s += (_F.KINDS.index(v) - s) % 4
    return s


def _level(gen: dict) -> int:
    lv = int(gen.get("level", 1))
    if lv not in LEVELS:
        raise ValueError(f"netsurgeon: no level {lv} (levels {sorted(LEVELS)})")
    return lv


def _inst_of(gen: dict, release_seed: str, case_id: str) -> dict:
    return _instance(_seed_for(gen, release_seed, case_id), LEVELS[_level(gen)]["tier"])


@functools.lru_cache(maxsize=None)
def _anchors(seed: int, tier: int):
    """(q_base, q_ref): the q of the ff baseline (SGD) and of the planted reference under this grader"""
    inst = _instance(seed, tier)
    return check(inst, _F.baseline(inst))["q"], check(inst, _F.reference(inst))["q"]


def materialize(gen: dict, *, release_seed: str, case_id: str) -> dict:
    lv = _level(gen)
    seed = _seed_for(gen, release_seed, case_id)
    inst = _instance(seed, LEVELS[lv]["tier"])
    return json.loads(json.dumps({
        "prompt": inst["prompt"], "temperature": 0.0, "max_tokens": MAX_TOKENS,
        "grader": "netsurgeon",
        "grader_config": {"family": FAMILY, "seed": seed, "tier": LEVELS[lv]["tier"], "cpu_s": CPU_S},
        "meta": {"tier": LEVELS[lv]["tier"], "kind": inst["hidden"]["kind"], "n_checks": len(_names(inst)),
                 "answer_tokens": LEVELS[lv]["answer_tokens"]},
        "difficulty": "hard"}))


def reference_code(inst: dict) -> str:
    return _F.reference(inst)


def reference(gen: dict, *, release_seed: str, case_id: str) -> str:
    return "```python\n" + reference_code(_inst_of(gen, release_seed, case_id)) + "\n```"


def mutants(gen: dict, *, release_seed: str, case_id: str) -> list[str]:
    fl = _FL.floors(_inst_of(gen, release_seed, case_id))
    return ["```python\n" + fl[k] + "\n```" for k in ("nothing", "baseline", "zero", "cprime", "stat")]


GRADE_IS_ENGINE = True


def grade_netsurgeon(response_text: str, cfg: dict, finish_reason=None) -> dict:
    """Engine grader "netsurgeon": cfg = {seed, tier, cpu_s}. The reply's last python block is the module; ``results`` holds one entry per
    check (id, credit, pass, weight, group) for per-check elements."""
    from .. import graders as _g
    inst = _instance(int(cfg["seed"]), int(cfg["tier"]))
    code = _g.extract_python(response_text or "", finish_reason)
    j = judge(inst, code)
    if not j["valid"]:
        return {"grade": "fail", "detail": j["note"] + _det.py_note(), "score": 0.0, "rate": 0.0, "points": 0.0, "q": 0.0,
                "cpu_s": j["cpu_s"], "results": j["results"]}
    qb, qr = _anchors(int(cfg["seed"]), int(cfg["tier"]))
    pts = points(j["q"], qb, True, qr)
    s = round(min(1.0, pts / POINTS_REF), 4)
    return {"grade": "pass" if s >= 0.999 else "fail",
            "detail": f"q={j['q']:.3f} (anchor {qb:.3f}, reference {qr:.3f}); {pts} points; {j['note']}" + _det.py_note(),
            "score": s, "rate": s, "points": pts, "q": j["q"], "anchor_q": qb, "cpu_s": j["cpu_s"], "results": j["results"]}


def grade(answer: str, materialized: dict) -> float:
    return float(grade_netsurgeon(answer, materialized["grader_config"])["score"])
