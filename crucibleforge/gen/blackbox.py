"""BLACK BOX (CrucibleForge port of the fast-fail question ff/q/blackbox.py, branch t-blackbox @ 86e63b1): the model gets a hex dump of a
seeded, undocumented binary flight-recorder format plus the decoded values of the first k records, and writes ``decode(data) -> list[dict]``.
The decoder is run in the engine sandbox on a FRESH hidden dump of the same schema (damaged frames from level 2 up); judging happens outside.

LEVELS = the ff tiers, same numbers: 1 framing + plain fields (one record kind, 5 fields); 2 + signed/endian/scale, bitfield, varint, damaged
frames and stray bytes; 3 two record kinds with a type byte, delta-coded and text-tag fields, a length byte, damaged frames (the pack cell);
4 the "pocket v2": 6 fields in a hidden wire order, only 3 shown records, no damage.

An item = one schema (``ffseed: N`` reproduces ff instance N byte for byte). Checks (``results``, so ``element: check`` works), summing to
the ff q: ``count`` (framing / record count, 0.1), ``skip`` (damaged frames dropped, 0.2, levels 2-3 only) and one per output key,
``<rung>/<key>`` with rung plain | scaled | enum | bits | varint | delta | tag (and ``kind``), sharing 0.7 (0.9 with no damage) by the ff field weights
(1 plain, 2 signed/endian/scale, 3 the rest). Item score = ff points / 100: invalid 0, nothing met 0, the best trivial decoder (``baseline``, measured by this
grader) 0.30, the reference 1.0, linear in between (no floor since 2026-11-14).
The generation, prompt, reference and baseline live in ``_blackbox_ff.py``, the red-team floors in ``_blackbox_floors.py``; both are pinned
below (chains.revision digests only this file)."""
import base64
import functools
import json
import os
import secrets
import subprocess
import tempfile
import threading
from pathlib import Path

from .. import gen as _gen
from . import _det
from . import _blackbox_ff as _F
from . import _blackbox_floors as _FL

FAMILY = "blackbox"
CPU_S = float(_F.CPU_S)           # stated in the prompt (ff: 5 s)
MAX_TOKENS = 65536
#: tier = ff tier; answer_tokens = reference-answer tokens (chars/4), re-measured in tests/test_gen_blackbox.py
LEVELS = {
    1: {"tier": 1, "answer_tokens": 1003},
    2: {"tier": 2, "answer_tokens": 1144},
    3: {"tier": 3, "answer_tokens": 1395},
    4: {"tier": 4, "answer_tokens": 1066},
}
VARIANTS = ()
POINTS_BASE, POINTS_REF = 30.0, 100.0

#: pins of the helper modules (chains.revision() digests only THIS file, so a change to a helper must change a line here;
#: tests/test_gen_blackbox.py::test_helper_pins fails until it does)
_FF_SHA = "1dec3f23bc12d5df"
_FLOORS_SHA = "82bd515db7648249"
_DET_SHA = "77fb46314ef63360"           # gen/_det.py: the shared deterministic launcher (one reply, one grade)

# In-sandbox harness. The blob (model code, hidden dump, verdict marker) arrives on stdin, so the model's module can neither find
# the marker in a file nor read it later; the verdict line is written straight to fd 1 and the process ends with os._exit, which
# skips atexit handlers and anything the module did to sys.stdout.
_HARNESS = r'''
import json, os, signal, sys, types
_d = json.loads(sys.stdin.buffer.read().decode("utf-8"))
_M = _d["mark"]
def _fin(o):
    os.write(1, ("\n" + _M + json.dumps(o) + "\n").encode("utf-8", "replace"))
    os._exit(0)
def _alarm(*a):
    raise BaseException("alarm")
signal.signal(signal.SIGALRM, _alarm)
solver = types.ModuleType("solver")
sys.modules["solver"] = solver
try:
    signal.alarm(8)
    exec(compile(_d["code"], "solver.py", "exec"), solver.__dict__)
    signal.alarm(0)
    _f = getattr(solver, "decode")
except BaseException as _e:
    signal.alarm(0)
    _fin({"err": (type(_e).__name__ + ": " + str(_e))[:200]})
try:
    signal.alarm(int(_d["cpu"]) + 3)
    _r = _f(bytes.fromhex(_d["hex"]))
    signal.alarm(0)
    if not isinstance(_r, list):
        _fin({"err": "decode() did not return a list"})
    _r = _r[:2000]
    _fin({"out": json.loads(json.dumps(_r, default=str))})
except BaseException as _e:
    signal.alarm(0)
    _fin({"err": (type(_e).__name__ + ": " + str(_e))[:200]})
'''


def _run(code: str, inst: dict, cpu_s: float = CPU_S) -> dict:
    """Run ``code`` in the engine's bwrap sandbox (no network, private tmpfs, 2 GiB, CPU limit) on the hidden dump. -> {ok, out, cpu_s, err}"""
    import resource
    from .. import graders as _g
    if not (code or "").strip():
        return {"ok": False, "out": None, "cpu_s": 0.0, "err": "no code in response"}
    _g.require_sandbox()
    mark = "BENCH_BLACKBOX_" + secrets.token_hex(12) + ":"
    blob = json.dumps({"code": code, "hex": inst["public"]["hex"], "mark": mark, "cpu": cpu_s}).encode("utf-8", "replace")
    lim = int(cpu_s) + 1

    def pre():
        resource.setrlimit(resource.RLIMIT_CPU, (lim, lim + 1))
        resource.setrlimit(resource.RLIMIT_AS, (2 << 30, 2 << 30))
    with tempfile.TemporaryDirectory(prefix="bench-blackbox-") as td:
        path = Path(td) / "harness.py"
        path.write_text(_det.with_prelude(_HARNESS, "blackbox"), encoding="utf-8")
        try:
            r0 = resource.getrusage(resource.RUSAGE_CHILDREN)
            p = subprocess.run(_det.det_cmd(_g._sandbox_cmd(str(path), td)), input=blob, capture_output=True, timeout=cpu_s * 4 + 30, cwd=td,
                               env=_det.det_env(), preexec_fn=pre)
            r1 = resource.getrusage(resource.RUSAGE_CHILDREN)
            out, rc, err = p.stdout.decode("utf-8", "replace"), p.returncode, p.stderr.decode("utf-8", "replace")[-300:]
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


def judge(inst: dict, code: str) -> dict:
    """-> {valid, q, cpu_s, note, results:[{id, credit, pass, weight, group, detail}]} for one answer (code = the module)."""
    r = _run(code, inst)
    names = _check_names(inst)
    if not r["ok"]:
        return {"valid": False, "q": 0.0, "cpu_s": r["cpu_s"], "note": r["err"],
                "results": [{"id": i, "credit": 0.0, "pass": False, "weight": w, "group": g, "detail": "invalid: " + r["err"][:80]}
                            for i, w, g in names]}
    q, results = _F.score_checks(inst, r["out"])
    alt = ""
    # the flags byte: the best of the layouts the prompt allows (the true one wins ties)
    for lay, rows in (inst["hidden"].get("flag_exps") or [])[1:]:
        q2, res2 = _F.score_checks({"public": inst["public"], "hidden": {**inst["hidden"], "exp": rows}}, r["out"])
        if q2 > q + 1e-12:
            q, results = q2, res2
            alt = f"; flags read as gps_lock bit {lay[0]}, armed bit {lay[1]}, link_q bits {lay[2]}-{lay[2] + 2} (a layout the prompt allows)"
    return {"valid": True, "q": q, "cpu_s": r["cpu_s"], "results": results,
            "note": f"n_out={len(r['out'])} n_exp={len(inst['hidden']['exp'])} fail:" + ",".join(x["id"] for x in results if not x["pass"]) + alt}


def _check_names(inst):
    """(id, weight, group) of every check of the instance (what an invalid answer is scored 0 on)"""
    q, res = _F.score_checks(inst, [])
    return [(r["id"], r["weight"], r["group"]) for r in res]


def check(inst, code):
    """The ff contract: {"q", "valid", "cpu_s", "note"}."""
    j = judge(inst, code)
    return {k: j[k] for k in ("q", "valid", "cpu_s", "note")}


def points(q: float, q_base: float, valid: bool = True, q_ref: float = 1.0) -> float:
    """invalid 0; q = 0 (nothing of the ladder met: an empty or do-nothing decoder) 0; the trivial baseline's q 30; the reference
    100. Below the baseline the points run linearly from 0 to 30 (until 2026-11-14 a floor of 10 and the anchor line paid an
    empty decoder 22-24 points while a crash got 0; scrub). The board's pack scale is the check share (``results``), where an
    empty decoder was already 0."""
    if not valid or q <= 0:
        return 0.0
    if q < q_base:
        return round(POINTS_BASE * q / q_base, 1)
    if q_ref <= q_base:
        return POINTS_BASE
    return round(POINTS_BASE + (POINTS_REF - POINTS_BASE) * (q - q_base) / (q_ref - q_base), 1)


_CACHE: dict = {}
_LOCK = threading.Lock()


def _instance(seed: int, tier: int) -> dict:
    with _LOCK:
        if (seed, tier) not in _CACHE:
            inst = _F.gen(seed, tier)
            inst["hidden"]["flag_exps"] = _flag_exps(seed, tier, inst)
            _CACHE[(seed, tier)] = inst
        return _CACHE[(seed, tier)]


#: the visible dump's frame counts per tier, as ``_blackbox_ff.gen`` makes it: (frames, damaged, clean head)
_PUB_FRAMES = {1: (14, 0, 7), 2: (18, 2, 4), 3: (24, 4, 2), 4: (3, 0, 3)}
_FLAG_KEYS = ("gps_lock", "armed", "link_q")


def flag_layouts(seed: int, tier: int, inst: dict) -> list[tuple[int, int, int]]:
    """Every flags-byte layout the PROMPT allows (scrub review 2026-11-14): (gps_lock bit, armed bit, lowest link_q bit), link_q
    3 contiguous bits, all five disjoint, that reproduces every decoded record the prompt shows of the kind that carries the
    flags AND leaves no set bit of any intact visible frame of that kind unexplained. The true layout first; [] without flags.
    The prompt never states the layout, so when a shown record has gps_lock == armed, or a byte like 0x00, the generator's
    own convention is one of several readings a careful decoder may take."""
    S = inst["hidden"]["S"]
    kinds = [t for t in S["types"] if any(f["enc"] == "bits" for f in t["fields"])]
    if not kinds:
        return []
    t = kinds[0]
    sh = {m["name"]: m["shift"] for f in t["fields"] if f["enc"] == "bits" for m in f["members"]}
    if sorted(sh) != sorted(_FLAG_KEYS) or len(kinds) != 1:
        raise ValueError("blackbox: flag layouts assume one flags byte of gps_lock / armed / link_q")
    nf, nc, k = _PUB_FRAMES[tier]
    _, vis, _ = _F.make_dump(S, f"blackbox:pub:{seed}:{tier}", nf, nc, k, "hi" if tier == 4 else None)

    def byte(r):
        return (int(r["gps_lock"]) << sh["gps_lock"]) | (int(r["armed"]) << sh["armed"]) | (int(r["link_q"]) << sh["link_q"])
    own = (lambda r: r.get("kind", t["name"]) == t["name"])
    shown = [r for r in inst["public"]["kdec"] if own(r)]
    seen = {byte(r["rec"]) for r in vis if own(r["rec"])}
    true = (sh["gps_lock"], sh["armed"], sh["link_q"])
    out = []
    for g in range(8):
        for a in range(8):
            for lo in range(6):
                if len({g, a, lo, lo + 1, lo + 2}) < 5:
                    continue
                mask = (1 << g) | (1 << a) | (7 << lo)
                if any(b & ~mask for b in seen):
                    continue
                if all((bool(byte(r) >> g & 1), bool(byte(r) >> a & 1), byte(r) >> lo & 7)
                       == (bool(r["gps_lock"]), bool(r["armed"]), int(r["link_q"])) for r in shown):
                    out.append((g, a, lo))
    if true not in out:
        raise ValueError(f"blackbox: the true flag layout {true} does not fit its own prompt")
    return [true] + [x for x in out if x != true]


def _flag_exps(seed: int, tier: int, inst: dict) -> list:
    """[(layout, hidden expected rows read under that layout)], the true layout first (its rows are the key itself)."""
    S = inst["hidden"]["S"]
    lays = flag_layouts(seed, tier, inst)
    if not lays:
        return []
    sh = dict(zip(_FLAG_KEYS, lays[0]))
    out = [(lays[0], inst["hidden"]["exp"])]
    for g, a, lo in lays[1:]:
        rows = []
        for row in inst["hidden"]["exp"]:
            vals = {kk: v for kk, v, _, _ in row}
            if "gps_lock" not in vals:
                rows.append(row)
                continue
            b = (int(vals["gps_lock"]) << sh["gps_lock"]) | (int(vals["armed"]) << sh["armed"]) | (int(vals["link_q"]) << sh["link_q"])
            rep = {"gps_lock": bool(b >> g & 1), "armed": bool(b >> a & 1), "link_q": b >> lo & 7}
            rows.append([(kk, rep.get(kk, v), tol, w) for kk, v, tol, w in row])
        out.append(((g, a, lo), rows))
    return out


def _seed_for(gen: dict, release_seed: str, case_id: str) -> int:
    """``ffseed`` reproduces the fast-fail instance of that seed; otherwise a 64-bit int from the contract seed"""
    if gen.get("ffseed") is not None:
        return int(gen["ffseed"])
    return 1 + _gen.instance_int(FAMILY, release_seed, case_id, gen.get("level"), gen.get("key"))


def _level(gen: dict) -> int:
    lv = int(gen.get("level", 3))
    if lv not in LEVELS:
        raise ValueError(f"blackbox: no level {lv} (levels {sorted(LEVELS)})")
    return lv


def _inst_of(gen: dict, release_seed: str, case_id: str) -> dict:
    return _instance(_seed_for(gen, release_seed, case_id), LEVELS[_level(gen)]["tier"])


@functools.lru_cache(maxsize=None)
def _q_base(seed: int, tier: int) -> float:
    """The anchor: the q of the ff baseline (the lazy generic decoder) under this grader."""
    inst = _instance(seed, tier)
    return check(inst, _F.baseline(inst))["q"]


def materialize(gen: dict, *, release_seed: str, case_id: str) -> dict:
    lv = _level(gen)
    seed = _seed_for(gen, release_seed, case_id)
    inst = _instance(seed, LEVELS[lv]["tier"])
    return json.loads(json.dumps({
        "prompt": inst["prompt"], "temperature": 0.0, "max_tokens": MAX_TOKENS,
        "grader": "blackbox",
        "grader_config": {"family": FAMILY, "seed": seed, "tier": LEVELS[lv]["tier"], "cpu_s": CPU_S},
        "meta": {"tier": LEVELS[lv]["tier"], "n_hidden": len(inst["hidden"]["exp"]), "n_damaged": inst["hidden"]["nbad"],
                 "n_checks": len(_check_names(inst)), "answer_tokens": LEVELS[lv]["answer_tokens"]},
        "difficulty": "hard"}))


def reference_code(inst: dict) -> str:
    return _F.reference(inst)


def reference(gen: dict, *, release_seed: str, case_id: str) -> str:
    return "```python\n" + reference_code(_inst_of(gen, release_seed, case_id)) + "\n```"


def mutants(gen: dict, *, release_seed: str, case_id: str) -> list[str]:
    inst = _inst_of(gen, release_seed, case_id)
    fl = _FL.floors(inst)
    return ["```python\n" + fl[k] + "\n```" for k in ("nothing", "copy", "baseline", "greedy")]


GRADE_IS_ENGINE = True


def grade_blackbox(response_text: str, cfg: dict, finish_reason=None) -> dict:
    """Engine grader "blackbox": cfg = {seed, tier, cpu_s}. The reply's last python block is the module; ``results`` holds one entry
    per check (id, credit, pass, weight, group) for per-check elements."""
    from .. import graders as _g
    inst = _instance(int(cfg["seed"]), int(cfg["tier"]))
    code = _g.extract_python(response_text or "", finish_reason)
    j = judge(inst, code)
    if not j["valid"]:
        return {"grade": "fail", "detail": j["note"] + _det.py_note(), "score": 0.0, "rate": 0.0, "points": 0.0, "q": 0.0,
                "cpu_s": j["cpu_s"], "results": j["results"]}
    qb = _q_base(int(cfg["seed"]), int(cfg["tier"]))
    pts = points(j["q"], qb)
    s = round(min(1.0, pts / POINTS_REF), 4)
    return {"grade": "pass" if s >= 0.999 else "fail",
            "detail": f"{j['q']:.3f} of the ladder met (anchor {qb:.3f}); {pts} points; {j['note']}" + _det.py_note(),
            "score": s, "rate": s, "points": pts, "q": j["q"], "anchor_q": qb, "cpu_s": j["cpu_s"], "results": j["results"]}


def grade(answer: str, materialized: dict) -> float:
    return float(grade_blackbox(answer, materialized["grader_config"])["score"])
