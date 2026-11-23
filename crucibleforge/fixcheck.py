"""Host-compared checker for the bug-fix tasks (4.5 realign; forgery record EXP-20261114-30).

The code bank's ``run_groups`` puts the hidden test files (expected values included) in the same sandbox
as the model's code and takes a child's exit status as the verdict. Model code can read the hidden file,
recognise the planted canary, and exit 0 everywhere else: full credit with the bug left in. This module
grades the same tasks (case CB-F1, families patchfix and patchmod) a different way:

* Every hidden test is split into a PROBE (inputs only: the scripts to run, or the calls to make) and its
  EXPECTED observations. Only the probe enters the sandbox. The expected values stay in this (host)
  process's memory; they are never written to any file the sandbox can see.
* Each probe unit runs in its own bwrap sandbox (no network, read-only system, private /tmp, address-space,
  CPU-time and file-size limits, a wall-clock timeout) holding a copy of the answer's tree, the driver and
  that one unit. The driver imports the model's code, makes the calls, and writes what it observed
  (strictly typed: str/int/float/bool/None/tuple/list/dict; any other object is opaque; an exception is
  its builtin class names) to an output file.
* The HOST compares the observations with the expected values. Pass/fail is never something the
  sandbox reports: model code that writes the output file, exits early, patches the driver or prints a
  pass line produces observations that are still compared; without the expected values it cannot
  produce the right ones except by computing them, which is the fix.

Group and score rules are the code bank's (``codebank.score_groups``): passed f2p / f2p, halved when a
p2p group fails (and when the agent's scope element fails). Floors: the given program scores 0, a null
patch 0, an unchanged tree 0, the reference 1.0, a partial fix the share of its f2p groups.

This module sits outside every family's grader-stamp closure on purpose (it is imported by no generator
and is not a ``static:*`` grading module), so adding it moves no stored row's ``grader_stamp``.
Public entry points: :func:`grade_answer` (one-shot reply), :func:`grade_tree` (a finished tree, e.g.
the agent session's), :func:`units_for` (the probe units of a case).
"""
from __future__ import annotations

import ast
import json
import math
import os
import re
import shutil
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import codebank as cb
from . import graders as _g

UNIT_TIMEOUT_S = 30            # wall clock per unit (one hidden test function of the old runner)
CPU_S = 20                     # RLIMIT_CPU per unit
AS_BYTES = 2 << 30             # RLIMIT_AS (box OOM rule, as the code bank)
FSIZE_BYTES = 64 << 20         # RLIMIT_FSIZE
TMPFS_BYTES = 256 << 20        # the sandbox /tmp
MAX_OBS_BYTES = 16 << 20       # the observation file is read up to this size
WORKERS = 4

# ------------------------------------------------------------------------------------------- the driver
#: Runs inside the sandbox. It holds no expected value. argv: none (fixed paths /h, /w, /out).
DRIVER_PY = r'''
import json, os, sys, resource
_exit, _open, _dumps = os._exit, open, json.dumps
_unit = json.load(_open("/h/unit.json"))
for _lim, _val in ((resource.RLIMIT_AS, _unit["as"]), (resource.RLIMIT_FSIZE, _unit["fsize"]),
                   (resource.RLIMIT_CPU, _unit["cpu"])):
    try:
        resource.setrlimit(_lim, (_val, _val))
    except (ValueError, OSError):
        pass
if _unit.get("prelude"):
    exec(compile(_unit["prelude"], "prelude", "exec"), {})
os.chdir("/w")
sys.path[:0] = [os.path.normpath(os.path.join("/w", p)) for p in _unit.get("pythonpath", ["."])]
_obs = []

def _enc(v, d=0):
    t = type(v)
    if d > 50:
        return {"o": "deep"}
    if v is None or t is bool or t is int or t is str:
        return v
    if t is float:
        return v if v == v and v not in (float("inf"), float("-inf")) else {"f": repr(v)}
    if t is tuple:
        return {"t": [_enc(x, d + 1) for x in v]}
    if t is list:
        return [_enc(x, d + 1) for x in v]
    if t is dict:
        return {"d": [[_enc(k, d + 1), _enc(x, d + 1)] for k, x in v.items()]}
    return {"o": t.__name__ if isinstance(getattr(t, "__name__", None), str) else "?"}

def _names(e):
    return [c.__name__ for c in type(e).__mro__ if getattr(c, "__module__", None) == "builtins"]

class _Caught:
    __slots__ = ("row",)
    def __init__(self, row):
        self.row = row

def catch(fn):
    try:
        return _Caught(["v", _enc(fn())])
    except Exception as e:
        return _Caught(["x", _names(e)])

def obs(v):
    _obs.append(v.row if type(v) is _Caught else ["v", _enc(v)])

if _unit["kind"] == "scripts":
    import importlib
    run = importlib.import_module(_unit["pkg"] + ".service").run
    for _s in _unit["scripts"]:
        obs(catch(lambda: run(_s)))
else:
    _ns = {"obs": obs, "catch": catch, "__name__": "probe"}
    exec(compile(_unit["source"], "probe", "exec"), _ns)
    _ns[_unit["fn"]]()
with _open("/out/obs.json", "w") as _fh:
    _fh.write(_dumps(_obs))
_exit(0)
'''


# ------------------------------------------------------------------------------------------- comparison
class _Opaque:
    """An object the driver could not encode: equal to nothing."""
    def __eq__(self, other):
        return False

    __hash__ = None


def _dec(v):
    if isinstance(v, list):
        return [_dec(x) for x in v]
    if isinstance(v, dict):
        if set(v) == {"t"} and isinstance(v["t"], list):
            return tuple(_dec(x) for x in v["t"])
        if set(v) == {"d"} and isinstance(v["d"], list):
            out = {}
            for kv in v["d"]:
                if not (isinstance(kv, list) and len(kv) == 2):
                    return _Opaque()
                k = _dec(kv[0])
                try:
                    out[k] = _dec(kv[1])
                except TypeError:
                    return _Opaque()
            return out
        if set(v) == {"f"} and isinstance(v["f"], str):
            try:
                return float(v["f"])
            except ValueError:
                return _Opaque()
        return _Opaque()
    return v


def _num(x) -> bool:
    return type(x) in (int, float)


def _match(row, exp) -> bool:
    """One observation against one expected check: ("eq", value) | ("approx", value, rel) | ("raises", Name)."""
    if not (isinstance(row, list) and len(row) == 2 and row[0] in ("v", "x")):
        return False
    kind = exp[0]
    if kind == "raises":
        return row[0] == "x" and isinstance(row[1], list) and exp[1] in row[1]
    if row[0] != "v":
        return False
    got = _dec(row[1])
    if kind == "eq":
        try:
            return bool(got == exp[1]) and type(got) is not _Opaque
        except Exception:  # noqa: BLE001
            return False
    if kind == "approx":
        want, rel = exp[1], exp[2]
        if not _num(got) or isinstance(got, bool):
            return False
        return math.isfinite(got) and abs(got - want) <= max(rel * abs(want), 1e-12)
    return False


def compare(observed: list | None, expected: list) -> tuple[bool, str]:
    """(passed, why). ``observed`` is the decoded JSON list the driver wrote (None when it wrote none)."""
    if observed is None:
        return False, "the unit wrote no observations (exited, crashed or timed out before the end)"
    if not isinstance(observed, list) or len(observed) != len(expected):
        return False, f"{len(observed) if isinstance(observed, list) else '?'} observations, {len(expected)} expected"
    for i, (row, exp) in enumerate(zip(observed, expected)):
        if not _match(row, exp):
            return False, f"observation {i}: differs from the expected value"
    return True, ""


# ------------------------------------------------------------------------------------------- units
def _patch_unit(src: str) -> tuple[str, str, list] | None:
    """(pkg, gid, [[script, expected], ...]) when ``src`` is exactly a patch-family hidden test file
    (``gen/_patchbase.hidden_file`` rebuilds it byte for byte), else None."""
    m = re.search(r"^from (\w+)\.service import run$", src or "", re.M)
    if not m:
        return None
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return None
    blob = None
    for n in tree.body:
        if (isinstance(n, ast.Assign) and len(n.targets) == 1 and isinstance(n.targets[0], ast.Name)
                and n.targets[0].id == "CASES" and isinstance(n.value, ast.Call) and n.value.args
                and isinstance(n.value.args[0], ast.Constant) and isinstance(n.value.args[0].value, str)):
            blob = n.value.args[0].value
    if blob is None:
        return None
    try:
        cases = json.loads(blob)
    except ValueError:
        return None
    if not (isinstance(cases, dict) and len(cases) == 1):
        return None
    (gid, pairs), = cases.items()
    from .gen import _patchbase as _pb
    if not isinstance(pairs, list) or _pb.hidden_file(m.group(1), gid, pairs) != src:
        return None
    return m.group(1), gid, pairs


def units_for(cfg: dict, case_id: str | None = None) -> dict[str, list[dict]]:
    """{group id: [unit, ...]} for a case's grader config. A unit is {"name", "payload" (what enters the
    sandbox), "expected" (host only)}. Raises ValueError for a case this checker does not cover."""
    hidden = cfg.get("hidden") or {}
    out: dict[str, list[dict]] = {}
    spec = None
    for g in cfg["groups"]:
        shp = _patch_unit(hidden.get(g["file"], ""))
        if shp is not None:
            pkg, gid, pairs = shp
            from .gen import _det
            out[g["id"]] = [{"name": g["id"], "expected": [("eq", w) for _s, w in pairs],
                             "payload": {"kind": "scripts", "pkg": pkg, "scripts": [s for s, _w in pairs],
                                         "pythonpath": cfg.get("pythonpath", ["."]),
                                         "prelude": _det.prelude("codebank"), "flags": _det.DET_FLAGS + ["-B"]}}]
            continue
        if spec is None:
            from . import fixspecs
            spec = fixspecs.spec_for(case_id, cfg)
        if spec is None or g["id"] not in spec["groups"]:
            raise ValueError(f"fixcheck: no probe spec for group {g['id']!r} of case {case_id!r}")
        out[g["id"]] = [{"name": name, "expected": list(expected),
                         "payload": {"kind": "probe", "source": spec["source"], "fn": name,
                                     "pythonpath": cfg.get("pythonpath", ["."]), "flags": ["-I", "-B"]}}
                        for name, expected in spec["groups"][g["id"]]]
    return out


# ------------------------------------------------------------------------------------------- sandbox
def _require() -> None:
    """Fail closed: without a working bwrap the model's code could read the host's case files."""
    if not cb.posix_runner():
        raise cb.Unsupported("fixcheck needs a POSIX host")
    if not (_g._BWRAP and _g.probe_bwrap(_g._BWRAP)[0]):
        raise _g.SandboxUnavailable("fixcheck refuses to run without a working bwrap sandbox "
                                    "(the expected values would be reachable from the model's code)")


def _read_obs(path: Path) -> list | None:
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError:
        return None
    try:
        with os.fdopen(fd, "rb") as fh:
            data = fh.read(MAX_OBS_BYTES + 1)
    except OSError:
        return None
    if len(data) > MAX_OBS_BYTES:
        return None
    try:
        v = json.loads(data.decode("utf-8", "replace"))
    except ValueError:
        return None
    return v if isinstance(v, list) else None


def run_unit(tree: dict[str, str], payload: dict, timeout: int | None = None) -> tuple[list | None, str]:
    """Run one probe unit against ``tree`` in its own sandbox: (observations or None, stderr tail)."""
    _require()
    timeout = timeout or UNIT_TIMEOUT_S
    unit = {k: v for k, v in payload.items() if k != "flags"}
    unit.update({"as": AS_BYTES, "fsize": FSIZE_BYTES, "cpu": CPU_S})
    with tempfile.TemporaryDirectory(prefix="fixck-") as td:
        root = Path(td)
        h, w, o = root / "h", root / "w", root / "out"
        for d in (h, w, o):
            d.mkdir()
        for rel, content in tree.items():
            p = w / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")
        (h / "driver.py").write_text(DRIVER_PY, encoding="utf-8")
        (h / "unit.json").write_text(json.dumps(unit), encoding="utf-8")
        cmd = _g._bwrap_args(_g._BWRAP)
        if cb._bwrap_has_size():
            cmd += ["--size", str(TMPFS_BYTES), "--tmpfs", "/tmp"]
        cmd += ["--ro-bind", str(h), "/h", "--bind", str(w), "/w", "--bind", str(o), "/out", "--chdir", "/w"]
        env = {"PATH": "/usr/bin:/bin", "HOME": "/tmp", "PYTHONHASHSEED": "0", "TZ": "UTC", "LC_ALL": "C",
               "LANG": "C", "PYTHONDONTWRITEBYTECODE": "1"}
        for k, v in env.items():
            cmd += ["--setenv", k, v]
        cmd += [_g._REAL_PY] + list(payload.get("flags") or ["-I", "-B"]) + ["/h/driver.py"]
        try:
            p = subprocess.run(cmd, stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding="utf-8",
                               errors="replace", env=env, timeout=timeout)
            rc, err = p.returncode, p.stderr
        except subprocess.TimeoutExpired:
            rc, err = -9, f"unit timed out after {timeout}s"
        observed = _read_obs(o / "obs.json") if rc == 0 else None
        shutil.rmtree(w, ignore_errors=True)
    tail = [ln for ln in (err or "").strip().splitlines() if ln.strip()]
    return observed, (tail[-1] if tail else ("" if rc == 0 else f"exit status {rc}"))[:300]


def check_tree(tree: dict[str, str], units: dict[str, list[dict]]) -> dict[str, dict]:
    """{group id: {"ok", "detail", "tests"}}: every unit run in its own sandbox, judged on the host."""
    jobs = [(gid, u) for gid, us in units.items() for u in us]
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        outs = list(ex.map(lambda j: run_unit(tree, j[1]["payload"]), jobs))
    res: dict[str, dict] = {gid: {"ok": True, "detail": "", "tests": []} for gid in units}
    for (gid, u), (observed, err) in zip(jobs, outs):
        ok, why = compare(observed, u["expected"])
        if not ok and observed is None and err:
            why = f"{why}: {err}"
        res[gid]["tests"].append({"test": u["name"], "ok": ok, "err": why})
        if not ok and res[gid]["ok"]:
            res[gid]["ok"] = False
            res[gid]["detail"] = f"{u['name']}: {why}"[:300]
    for r in res.values():
        if r["ok"]:
            r["detail"] = f"{len(r['tests'])} units passed"
    return res


def grade_tree(tree: dict[str, str], cfg: dict, *, case_id: str | None = None, changed: bool = True,
               extra: list[dict] | None = None) -> dict:
    """Verdict (``codebank.score_groups`` shape, plus ``checker``) for a finished tree. ``changed`` False
    (no file written / no edit) fails every group without running anything. ``extra`` = more elements,
    e.g. the agent session's scope element (``codeenv.RepoEnv.scope_element()``)."""
    groups = cfg["groups"]
    units = units_for(cfg, case_id)
    if not changed:
        res = {g["id"]: {"ok": False, "detail": "no file was changed"} for g in groups}
    else:
        res = check_tree(tree, units)
    v = cb.score_groups(groups, res, extra=extra)
    v["checker"] = "fixcheck-host-compared-1"
    return v


def grade_answer(response_text: str, cfg: dict, *, case_id: str | None = None,
                 finish_reason: str | None = None) -> dict:
    """A one-shot reply (SEARCH/REPLACE blocks or whole files, the code bank's format)."""
    tree, applied, errors, any_edit = cb._answer_tree(response_text, cfg, finish_reason)
    changed = bool(any_edit) and any(tree.get(p) != cfg["files"].get(p) for p in set(tree) | set(cfg["files"]))
    v = grade_tree(tree, cfg, case_id=case_id, changed=changed)
    v["format_errors"] = errors
    v["applied"] = applied
    return v


def grade_agent(env) -> dict:
    """The agent session's final tree (``codeenv.RepoEnv``), scope element included."""
    changed = bool(env.writes) and env.tree != env.cfg["files"]
    return grade_tree(env.tree, env.cfg, case_id=env.cfg.get("case_id"), changed=changed,
                      extra=[env.scope_element()])
