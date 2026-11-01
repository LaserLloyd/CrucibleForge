"""Code bank: realistic repo tasks graded by hidden tests in the sandbox.

A ``repo_tests`` case carries a small project (``grader_config.files``), hidden
test files (``hidden``) and named test GROUPS. The model answers with whole
files (or Aider-style SEARCH/REPLACE blocks); the edits are applied to the
project, the hidden tests run inside the same bwrap sandbox as every other
graded code (no network, read-only system, env allowlist), one child process
per group, and every group is one element:

* ``f2p``   — fails on the untouched project, must pass after the fix;
* ``p2p``   — passes before and must still pass (existing behaviour);
* ``scope`` — agent family only: no decoy tool, no write outside scope.

Row score (``score``, 0..1) = passed f2p / f2p, halved when any p2p group
fails (a fix that breaks something else) and halved again when the scope
element fails. An untouched project therefore scores 0. The strict case grade
(``pass`` = every group passed) is kept as before. Thinking length never
enters the score.

Determinism: LC_ALL=C, TZ=UTC, PYTHONHASHSEED=0 in the sandbox; every test
FUNCTION runs in its own child process on a fresh copy of the tree.

Integrity (review 2026-10-26): the reporter (the sandbox parent) never imports
model code; a test passes only when its child exits 0 (a crash, os._exit with
another status, timeout or a test that never ran is a fail). Every hidden
file gets a planted always-failing "canary" test with a plausible random name;
if a canary exits 0 the answer forged results (exit(0) at import, a patched
exit, a racing thread) and the row fails closed (``tamper``). A static scan
(``tamper_findings``) also fails answers that reach into the harness. Residual
risk: an answer that inspects each test's own source and forges only real
tests; see PROGRESS.md.
"""
from __future__ import annotations

import json
import os
import posixpath
import re
import shutil
import sqlite3
import subprocess
import tempfile
from pathlib import Path

from .api import strip_thinking_tags
from . import graders as _g

DEFAULT_TIMEOUT_S = 60          # whole grading run (all groups)
PER_TEST_TIMEOUT_S = 10
MAX_OUTPUT = 400_000

# ------------------------------------------------------------- edit parsing
_PATH_TOKEN = r"[A-Za-z0-9_.\-/]+\.[A-Za-z0-9_]+|[A-Za-z0-9_.\-]+/[A-Za-z0-9_.\-/]+"
_HEADER_RES = [
    re.compile(rf"^\s*(?:#+\s*)?(?:\*\*)?(?:file|path)\s*[:=]\s*`?(?P<p>{_PATH_TOKEN})`?(?:\*\*)?\s*:?\s*$", re.I),
    re.compile(rf"^\s*#+\s*`?(?P<p>{_PATH_TOKEN})`?\s*:?\s*$"),
    re.compile(rf"^\s*\*\*`?(?P<p>{_PATH_TOKEN})`?\*\*\s*:?\s*$"),
    re.compile(rf"^\s*`(?P<p>{_PATH_TOKEN})`\s*:?\s*$"),
    re.compile(rf"^\s*(?P<p>{_PATH_TOKEN})\s*:?\s*$"),
]
_FENCE_OPEN = re.compile(r"^(?P<ticks>`{3,}|~{3,})[ \t]*(?P<info>[^\n`]*)$")
_SR_RE = re.compile(r"^<{5,9} SEARCH[ \t]*\n(?P<search>.*?)^={5,9}[ \t]*\n(?P<replace>.*?)^>{5,9} REPLACE[ \t]*$",
                    re.M | re.S)
_ELISION_RE = re.compile(
    r"(\.\.\.|…)\s*\(?\s*(rest|existing|unchanged|remaining|same|other|previous|omitted|as before)"
    r"|(rest|remainder) of (the )?(file|code|module|class|function)s? (is |stays |remains )?(unchanged|the same|as before)"
    r"|^\s*(#|//)\s*(\.\.\.|…)\s*$"
    r"|(unchanged|omitted) for brevity", re.I | re.M)


def _header_path(line: str) -> str | None:
    s = line.strip()
    if not s or len(s) > 200:
        return None
    for rx in _HEADER_RES:
        m = rx.match(s)
        if m:
            return m.group("p").strip("`*").rstrip(":")
    return None


def _info_path(info: str) -> str | None:
    """```python app/x.py  or  ```app/x.py  or ```python title="app/x.py" """
    info = info.strip()
    m = re.search(r'(?:title|file|path)\s*=\s*"?([^"\s]+)"?', info)
    if m:
        return m.group(1)
    for tok in info.split():
        if re.fullmatch(_PATH_TOKEN, tok) and ("/" in tok or "." in tok.strip(".")):
            if "." in tok or "/" in tok:
                return tok
    return None


def _fences(text: str) -> list[dict]:
    """Every fenced block: {start, end, info, body, path}. ``path`` from the
    info string, else the nearest header line within 3 lines above."""
    lines = text.split("\n")
    out, i = [], 0
    offsets = []
    pos = 0
    for ln in lines:
        offsets.append(pos)
        pos += len(ln) + 1
    while i < len(lines):
        m = _FENCE_OPEN.match(lines[i])
        if not m:
            i += 1
            continue
        ticks = m.group("ticks")
        j = i + 1
        while j < len(lines) and not (lines[j].strip().startswith(ticks[0] * len(ticks))
                                      and lines[j].strip().strip(ticks[0]) == ""):
            j += 1
        closed = j < len(lines)
        body = "\n".join(lines[i + 1:j])
        path = _info_path(m.group("info"))
        if path is None:
            k, seen = i - 1, 0
            while k >= 0 and seen < 3:
                if lines[k].strip():
                    seen += 1
                    path = _header_path(lines[k])
                    if path:
                        break
                    if seen >= 1 and not lines[k].strip().startswith(("#", "*", "`", "File", "FILE", "Path", "path")):
                        # a sentence between header and fence breaks the link
                        # only if it is not itself a header
                        pass
                k -= 1
        out.append({"start": offsets[i], "end": offsets[j] if closed else len(text),
                    "line": i, "info": m.group("info").strip(), "body": body,
                    "path": path, "closed": closed})
        i = j + 1
    return out


def parse_edits(text: str, finish_reason: str | None = None) -> tuple[list[dict], list[str]]:
    """Edits in answer order: {"kind": "file"|"sr", "path", ...}. Second
    value = format problems found while parsing (unlinked blocks...)."""
    text = strip_thinking_tags(text or "").replace("\r\n", "\n")
    problems: list[str] = []
    edits: list[dict] = []
    fences = _fences(text)
    for f in fences:
        if not f["closed"]:
            if finish_reason == "length":
                problems.append(f"cut-off block for {f['path'] or '?'} ignored")
                continue
            # a finished answer that never closed its last fence: keep it
        body = f["body"]
        if _SR_RE.search(body + "\n"):
            for m in _SR_RE.finditer(body + "\n"):
                path = f["path"]
                if path is None:
                    # path line inside the fence, just before the marker
                    before = body[:m.start()].rstrip("\n").split("\n")
                    path = _header_path(before[-1]) if before and before[-1].strip() else None
                if path is None:
                    problems.append("SEARCH/REPLACE block without a file path")
                    continue
                edits.append({"kind": "sr", "path": path, "search": m.group("search"),
                              "replace": m.group("replace"), "pos": f["start"] + m.start()})
            continue
        if f["path"] is None:
            continue           # an example/snippet without a path is prose
        edits.append({"kind": "file", "path": f["path"], "content": body, "pos": f["start"]})
    # SEARCH/REPLACE blocks written outside any fence
    covered = [(f["start"], f["end"]) for f in fences]
    for m in _SR_RE.finditer(text):
        if any(a <= m.start() < b for a, b in covered):
            continue
        before = text[:m.start()].rstrip("\n").split("\n")
        path = None
        for ln in reversed(before[-3:]):
            if ln.strip():
                path = _header_path(ln)
                break
        if path is None:
            problems.append("SEARCH/REPLACE block without a file path")
            continue
        edits.append({"kind": "sr", "path": path, "search": m.group("search"),
                      "replace": m.group("replace"), "pos": m.start()})
    edits.sort(key=lambda e: e["pos"])
    return edits, problems


def norm_path(p: str) -> str | None:
    """Repo-relative POSIX path, or None when it escapes the repo."""
    p = (p or "").strip().replace("\\", "/")
    while p.startswith("./"):
        p = p[2:]
    if not p or p.startswith("/") or re.match(r"^[A-Za-z]:", p):
        return None
    n = posixpath.normpath(p)
    if n.startswith("../") or n == ".." or n.startswith("/") or n == ".":
        return None
    return n


def _path_allowed(path: str, allowed: list[str]) -> bool:
    import fnmatch
    return any(path == a or fnmatch.fnmatchcase(path, a) for a in allowed)


def apply_edits(files: dict[str, str], edits: list[dict], allowed: list[str],
                new_paths: list[str] | None = None) -> tuple[dict[str, str], list[str], list[str]]:
    """Apply edits to a copy of ``files``. Returns (tree, applied paths,
    format errors). Edits to paths outside ``allowed`` / ``new_paths`` are
    dropped (reported)."""
    tree = dict(files)
    errors: list[str] = []
    applied: list[str] = []
    new_paths = new_paths or []
    for e in edits:
        path = norm_path(e["path"])
        if path is None:
            errors.append(f"path escapes the project: {e['path']!r}")
            continue
        if path not in tree and not _path_allowed(path, new_paths):
            # tolerate a header that names the file without its directory
            cands = [p for p in tree if p.endswith("/" + path) and _path_allowed(p, allowed)]
            if len(cands) == 1:
                path = cands[0]
        exists = path in tree
        if not ((exists and _path_allowed(path, allowed)) or (not exists and _path_allowed(path, new_paths))):
            errors.append(f"edit to {path} ignored (not an editable file)")
            continue
        if e["kind"] == "file":
            content = e["content"]
            old = tree.get(path)
            if old is not None and _ELISION_RE.search(content) and not _ELISION_RE.search(old) \
                    and len(content) < 0.6 * len(old):
                errors.append(f"{path}: whole-file block elides code ('... unchanged'); not applied")
                continue
            tree[path] = content if content.endswith("\n") else content + "\n"
            applied.append(path)
            if path.endswith(".py"):
                import ast
                try:
                    ast.parse(content)
                except SyntaxError as e:
                    # most often a ``` line inside the file closed the block
                    # early (review L1); the file is applied as written
                    errors.append(f"{path}: whole-file block does not parse (line {e.lineno}); a ``` line "
                                  f"inside the file ends a 3-backtick block, use a longer fence")
        else:
            old = tree.get(path)
            if old is None:
                if e["search"].strip() == "":
                    tree[path] = e["replace"]
                    applied.append(path)
                else:
                    errors.append(f"{path}: SEARCH/REPLACE on a file that does not exist")
                continue
            new = _apply_sr(old, e["search"], e["replace"])
            if new is None:
                errors.append(f"{path}: SEARCH text not found exactly once")
                continue
            tree[path] = new
            applied.append(path)
    return tree, sorted(set(applied)), errors


def _apply_sr(old: str, search: str, replace: str) -> str | None:
    if search == "":
        return None
    if old.count(search) == 1:
        return old.replace(search, replace, 1)
    # tolerate trailing whitespace differences, line by line
    def rstrip_lines(s):
        return "\n".join(l.rstrip() for l in s.split("\n"))
    o, s = rstrip_lines(old), rstrip_lines(search)
    if s and o.count(s) == 1:
        return o.replace(s, rstrip_lines(replace), 1)
    return None


# ----------------------------------------------------------- sandbox runner
#: Runs inside the sandbox. argv: base workroot ; stdin: nonce. Spawns one
#: child (same interpreter, -I) per group; the child imports the group's test
#: file and runs its test_* functions with a tiny pytest-compatible shim.
RUNNER_PY = r'''
# Sandbox test runner. "parent" mode imports NO model code: it runs every
# test FUNCTION (canaries included) in its own child process and judges it
# by the child's exit status alone; the child is the only process that
# imports model code. A crash, os._exit, timeout or missing test is a fail;
# a canary (a planted always-failing test) that "passes" means the answer
# forged results, and the parent reports tamper.
import json, os, shutil, subprocess, sys
base, work, mode = sys.argv[1], sys.argv[2], sys.argv[3]
if mode == "parent":
    spec = json.load(open(os.path.join(base, "spec.json")))
    for gi, g in enumerate(spec["groups"]):
        for fi, fname in enumerate(g["functions"]):
            wd = os.path.join(work, "g%d_%d" % (gi, fi))
            shutil.copytree(os.path.join(base, "src"), wd, symlinks=False)
            shutil.copytree(os.path.join(base, "hidden"), os.path.join(wd, "_hidden"))
            if g.get("runner") == "node":
                cmd = [spec["node"], os.path.join(base, "_cb_runner.mjs"), wd, g["file"], fname]
            else:
                cmd = [sys.executable, "-I", "-B", os.path.join(base, "_cb_runner.py"), base, wd, "child",
                       g["file"], fname]
            try:
                p = subprocess.run(cmd, stdin=subprocess.DEVNULL, capture_output=True, text=True, cwd=wd,
                                   timeout=g.get("timeout", 30), errors="replace")
                rc, err = p.returncode, p.stderr
            except subprocess.TimeoutExpired:
                rc, err = -9, "function timed out after %ss" % g.get("timeout", 30)
            tail = [l for l in (err or "").strip().splitlines() if l.strip()]
            print(json.dumps({"group": g["id"], "test": fname, "rc": rc,
                              "err": (tail[-1] if tail else "")[:400]}), flush=True)
            shutil.rmtree(wd, ignore_errors=True)
    sys.exit(0)

# ---------------- child: run ONE test function (all its parameters)
import resource
_cap = 2 << 30                       # 2 GiB address space (box OOM rule)
for _lim, _val in ((resource.RLIMIT_AS, _cap), (resource.RLIMIT_FSIZE, 256 << 20)):
    try:
        resource.setrlimit(_lim, (_val, _val))
    except (ValueError, OSError):    # macOS refuses RLIMIT_AS above its hard cap: best effort
        pass
_exit = os._exit                     # saved before any model code runs
_write = os.write
def _die(code, msg=""):
    if msg:
        try:
            _write(2, ("\n" + msg[:600] + "\n").encode("utf-8", "replace"))
        except Exception:
            pass
    _exit(code)
tfile, tname = sys.argv[4], sys.argv[5]
spec = json.load(open(os.path.join(base, "spec.json")))
sys.path[:0] = [os.path.join(base, "_shim"), os.path.join(work, "_hidden")] + [os.path.join(work, p) for p in spec.get("pythonpath", ["."])]
os.chdir(work)
import importlib.util, inspect, signal, traceback, pathlib, itertools
import pytest as _shim
_per = spec.get("per_test_timeout", 10)
def _alarm(*a): raise TimeoutError("test exceeded %ss" % _per)
signal.signal(signal.SIGALRM, _alarm)
try:
    sp = importlib.util.spec_from_file_location(pathlib.Path(tfile).stem, os.path.join(work, "_hidden", tfile))
    mod = importlib.util.module_from_spec(sp)
    sys.modules[sp.name] = mod
    signal.alarm(_per * 2)
    sp.loader.exec_module(mod)
    signal.alarm(0)
except BaseException as e:
    signal.alarm(0)
    _die(2, "collect: " + (type(e).__name__ + ": " + str(e))[:400])
fn = getattr(mod, tname, None)
if not callable(fn):
    _die(3, "test function %s not found" % tname)
if inspect.iscoroutinefunction(fn):
    _die(3, "async test functions are not supported by this runner")
fixtures = {n: f for n, f in vars(mod).items() if getattr(f, "_cb_fixture", False)}
params = getattr(fn, "_cb_params", None) or [((), ())]
cnt = itertools.count()
for argnames, argvals in params:
    tid = tname if not argnames else "%s[%s]" % (tname, "-".join(map(str, argvals))[:60])
    teardown = []
    try:
        signal.alarm(_per)
        given = dict(zip(argnames, argvals))
        cache = {}
        def resolve(pname):
            if pname in given: return given[pname]
            if pname in cache: return cache[pname]
            if pname == "tmp_path":
                p = pathlib.Path(work) / "_tmp" / ("%s_%d" % (tname, next(cnt)))
                p.mkdir(parents=True, exist_ok=True); v = p
            elif pname == "monkeypatch":
                v = _shim.MonkeyPatch(); teardown.append(v.undo)
            elif pname in fixtures:
                f = fixtures[pname]
                kw = {a: resolve(a) for a in inspect.signature(f).parameters}
                r = f(**kw)
                if inspect.isgenerator(r):
                    v = next(r)
                    teardown.append(lambda r=r: next(r, None))
                else:
                    v = r
            else:
                raise LookupError("unknown fixture %r" % pname)
            cache[pname] = v
            return v
        fn(**{a: resolve(a) for a in inspect.signature(fn).parameters})
        signal.alarm(0)
    except BaseException as e:
        signal.alarm(0)
        where = ""
        try:
            for fr in reversed(traceback.extract_tb(e.__traceback__)):
                if "_hidden" in fr.filename:
                    where = " (line %d: %s)" % (fr.lineno, (fr.line or "")[:120]); break
        except BaseException:
            pass
        _die(1, "%s%s%s" % (("[%s] " % tid[len(tname) + 1:-1]) if argnames else "",
                             (type(e).__name__ + ": " + str(e))[:300], where))
    finally:
        for t in reversed(teardown):
            try: t()
            except BaseException: pass
_die(0)
'''

#: pytest compatibility shim (the sandbox has the stdlib only).
SHIM_PY = r'''
import contextlib, math, os, re, sys
class _Raises:
    def __init__(self, exc, match=None):
        self.exc, self.match, self.value = exc, match, None
    def __enter__(self): return self
    def __exit__(self, et, ev, tb):
        if et is None:
            raise AssertionError("DID NOT RAISE %r" % (self.exc,))
        if not issubclass(et, self.exc): return False
        if self.match and not re.search(self.match, str(ev)):
            raise AssertionError("exception %r does not match %r" % (str(ev), self.match))
        self.value = ev
        return True
def raises(exc, *args, match=None, **kw):
    if args:
        with _Raises(exc, match): args[0](*args[1:], **kw)
        return None
    return _Raises(exc, match)
class approx:
    def __init__(self, v, rel=1e-6, abs=1e-12): self.v, self.rel, self.abs = v, rel, abs
    def __eq__(self, o):
        if isinstance(self.v, (list, tuple)):
            return len(o) == len(self.v) and all(approx(a, self.rel, self.abs) == b for a, b in zip(self.v, o))
        return math.isclose(o, self.v, rel_tol=self.rel, abs_tol=self.abs)
    def __repr__(self): return "approx(%r)" % (self.v,)
def fixture(fn=None, **kw):
    def deco(f):
        f._cb_fixture = True
        return f
    return deco(fn) if callable(fn) else deco
class _Mark:
    def parametrize(self, names, values, **kw):
        names = [n.strip() for n in names.split(",")] if isinstance(names, str) else list(names)
        vals = [v if len(names) > 1 else (v,) for v in values]
        def deco(f):
            f._cb_params = [(tuple(names), tuple(v)) for v in vals]
            return f
        return deco
    def __getattr__(self, name):
        return lambda *a, **k: (lambda f: f)
mark = _Mark()
def fail(msg=""): raise AssertionError(msg)
class MonkeyPatch:
    _NOT = object()
    def __init__(self): self._undo = []
    def setattr(self, target, name, value=_NOT, raising=True):
        if value is MonkeyPatch._NOT:
            mod, _, attr = target.rpartition(".")
            import importlib
            value, target, name = name, importlib.import_module(mod), attr
        old = getattr(target, name, MonkeyPatch._NOT)
        self._undo.append(lambda: delattr(target, name) if old is MonkeyPatch._NOT else setattr(target, name, old))
        setattr(target, name, value)
    def setenv(self, k, v):
        old = os.environ.get(k)
        self._undo.append(lambda: os.environ.pop(k, None) if old is None else os.environ.__setitem__(k, old))
        os.environ[k] = str(v)
    def delenv(self, k, raising=True):
        old = os.environ.get(k)
        if old is None and raising: raise KeyError(k)
        self._undo.append(lambda: old is not None and os.environ.__setitem__(k, old))
        os.environ.pop(k, None)
    def chdir(self, p):
        old = os.getcwd(); self._undo.append(lambda: os.chdir(old)); os.chdir(p)
    def undo(self):
        while self._undo: self._undo.pop()()
'''

#: node runner: imports the group's test module and runs every exported
#: function whose name starts with "test" (await if async).
RUNNER_MJS = r'''
// Child: run ONE exported test function; the exit status is the result
// (0 pass, 1 fail, 2 collect error, 3 missing). Exits through a bound copy
// of process.exit taken before any model code is imported.
import { pathToFileURL } from "node:url";
import { writeSync } from "node:fs";
import path from "node:path";
const exit = process.exit.bind(process);
const say = (s) => { try { writeSync(2, "\n" + String(s).slice(0, 600) + "\n"); } catch (e) {} };
const [wd, file, name] = process.argv.slice(2);
process.chdir(wd);
let mod;
try {
  mod = await import(pathToFileURL(path.join(wd, "_hidden", file)).href);
} catch (e) {
  say("collect: " + String(e && e.stack || e).slice(0, 400));
  exit(2);
}
const fn = mod[name];
if (typeof fn !== "function") { say("test function " + name + " not found"); exit(3); }
let timer;
try {
  await Promise.race([
    Promise.resolve().then(() => fn()),
    new Promise((_, rej) => { timer = setTimeout(() => rej(new Error("test exceeded 10s")), 10000); }),
  ]);
} catch (e) {
  say(String(e && e.message || e).slice(0, 300));
  exit(1);
}
clearTimeout(timer);
exit(0);
'''


TMPFS_BYTES = 1 << 30


_HAS_SIZE: dict[str, bool] = {}


def _bwrap_has_size() -> bool:
    if _g._BWRAP not in _HAS_SIZE:
        try:
            _HAS_SIZE[_g._BWRAP] = "--size" in subprocess.run(
                [_g._BWRAP, "--help"], capture_output=True, text=True, timeout=10).stdout
        except Exception:  # noqa: BLE001
            _HAS_SIZE[_g._BWRAP] = False
    return _HAS_SIZE[_g._BWRAP]


class Unsupported(RuntimeError):
    """The code bank cannot execute on this host (Windows has no ``resource``/``SIGALRM``)."""


class NodeMissing(Unsupported):
    """A node-runner case or snippet was asked for but no node is on PATH."""


_BASH_MAJOR: list = []


def bash_major() -> int | None:
    """Major version of the ``bash`` on PATH (macOS ships 3.2), None when absent."""
    if not _BASH_MAJOR:
        major = None
        b = shutil.which("bash")
        if b:
            try:
                out = subprocess.run([b, "-c", "echo ${BASH_VERSINFO[0]}"], capture_output=True,
                                     text=True, timeout=10).stdout.strip()
                major = int(out)
            except (OSError, ValueError, subprocess.SubprocessError):
                major = None
        _BASH_MAJOR.append(major)
    return _BASH_MAJOR[0]


def posix_runner() -> bool:
    """The code-bank child runner needs POSIX ``resource`` + ``SIGALRM``."""
    return os.name != "nt"


def node_path() -> str | None:
    p = shutil.which("node")
    return os.path.realpath(p) if p else None


def node_version() -> str | None:
    n = node_path()
    if not n:
        return None
    try:
        return subprocess.run([n, "--version"], capture_output=True, text=True, timeout=10).stdout.strip()
    except Exception:
        return None


def env_stamp() -> dict:
    """What the graded code ran against (stamped on rows and meta)."""
    return {"sqlite": sqlite3.sqlite_version, "node": node_version(),
            "python": _g._REAL_PY.rsplit("/", 3)[-3] if "/" in _g._REAL_PY else _g._REAL_PY}


def _vtuple(v: str) -> tuple:
    return tuple(int(x) for x in re.findall(r"\d+", v)[:3])


_CANARY_WORDS = ["totals", "rounding", "order", "empty", "unicode", "limits", "reset", "refund", "window",
                 "cursor", "zone", "names", "retry", "batch", "cache", "merge", "parse", "report", "flags", "sync"]


def _canary(runner: str) -> tuple[str, str]:
    """(name, source) of a planted test that always fails. A row in which a
    canary 'passes' forged its results (os._exit(0) at import, a patched
    exit, a thread that exits 0...): the whole row fails closed."""
    import random
    rnd = random.SystemRandom()
    a, b = rnd.sample(_CANARY_WORDS, 2)
    x = rnd.randint(2, 999)
    if runner == "node":
        name = "test" + a.title() + b.title() + str(rnd.randint(2, 99))
        return name, (f"\nexport function {name}() {{\n  const got = {x};\n"
                      f"  if (got !== {x + 1}) throw new Error(`expected {x + 1}, got ${{got}}`);\n}}\n")
    name = f"test_{a}_{b}_{rnd.randint(2, 99)}"
    return name, f"\n\ndef {name}():\n    got = {x}\n    assert got == {x + 1}, got\n"


def run_groups(tree: dict[str, str], hidden: dict[str, str], groups: list[dict],
               cfg: dict) -> dict[str, dict]:
    """Run every group's test file against ``tree`` in the sandbox, one child
    process per test FUNCTION, judged by its exit status (the reporter never
    shares a process with model code). Each group's file gets a planted
    canary; a canary that passes sets ``tamper`` on every group.
    Returns {group_id: {"ok": bool, "tests": [...], "detail": str, "tamper": bool}}."""
    if not posix_runner():
        raise Unsupported("the code bank needs a POSIX host (resource limits and SIGALRM)")
    _g.require_sandbox()
    if any(name.endswith(".sh") for name in tree) and (bash_major() or 0) < 4:
        raise Unsupported("this case's shell scripts need bash 4 or newer on PATH (macOS ships 3.2)")
    needs_node = any(g.get("runner") == "node" for g in groups)
    node = node_path() if needs_node else None
    if needs_node and not node:
        raise NodeMissing("this case needs node and none is on PATH")
    files = dict(hidden)
    plan, canaries = [], {}
    for g in groups:
        runner = g.get("runner", "python")
        want = sorted(expected_tests(hidden.get(g["file"], ""), runner))
        if g["file"] not in canaries:
            cname, csrc = _canary(runner)
            canaries[g["file"]] = cname
            files[g["file"]] = hidden.get(g["file"], "") + csrc
        plan.append({"id": g["id"], "file": g["file"], "runner": runner, "want": want,
                     "functions": want + [canaries[g["file"]]],
                     "timeout": int(cfg.get("group_timeout_s", 30))})
    spec = {"groups": plan, "pythonpath": cfg.get("pythonpath", ["."]),
            "per_test_timeout": int(cfg.get("per_test_timeout_s", PER_TEST_TIMEOUT_S)), "node": node}
    n_funcs = sum(len(p["functions"]) for p in plan)
    stdout, stderr = "", ""
    with tempfile.TemporaryDirectory(prefix="cb-") as td:
        root = Path(td)
        for rel, content in tree.items():
            p = root / "src" / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")
        (root / "src").mkdir(exist_ok=True)
        for rel, content in files.items():
            p = root / "hidden" / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")
        (root / "hidden").mkdir(exist_ok=True)
        (root / "_shim").mkdir()
        (root / "_shim" / "pytest.py").write_text(SHIM_PY, encoding="utf-8")
        (root / "_cb_runner.py").write_text(RUNNER_PY, encoding="utf-8")
        (root / "_cb_runner.mjs").write_text(RUNNER_MJS, encoding="utf-8")
        (root / "spec.json").write_text(json.dumps(spec), encoding="utf-8")
        envs = {"LC_ALL": "C", "LANG": "C", "TZ": "UTC", "PYTHONHASHSEED": "0",
                "PYTHONDONTWRITEBYTECODE": "1", "HOME": "/tmp",
                # node children have no RLIMIT_AS: cap the V8 heap instead
                "NODE_OPTIONS": "--max-old-space-size=1024"}
        if _g._BWRAP and _g.probe_bwrap(_g._BWRAP)[0]:
            path = "/usr/bin:/bin"
            # the sandbox /tmp is a tmpfs charged to THIS process's memory: bound it
            extra = ["--size", str(TMPFS_BYTES), "--tmpfs", "/tmp"] if _bwrap_has_size() else []
            if node:
                ndir = os.path.dirname(node)
                extra += ["--ro-bind", ndir, ndir]
                path = ndir + ":" + path
            cmd = _g._bwrap_args(_g._BWRAP) + ["--ro-bind", td, "/work-src"] + extra
            for k, v in {**envs, "PATH": path}.items():
                cmd += ["--setenv", k, v]
            cmd += [_g._REAL_PY, "-I", "-B", "/work-src/_cb_runner.py", "/work-src", "/tmp", "parent"]
            env = _g._child_env()
        else:
            work = root / "_w"
            work.mkdir()
            cmd = [_g._REAL_PY, "-I", "-B", str(root / "_cb_runner.py"), td, str(work), "parent"]
            env = {**_g._child_env(), **envs}
        try:
            proc = subprocess.run(cmd, stdin=subprocess.DEVNULL, capture_output=True, text=True,
                                  encoding="utf-8", errors="replace", env=env,
                                  timeout=int(cfg.get("timeout_s", DEFAULT_TIMEOUT_S)) + 3 * n_funcs)
            stdout, stderr = proc.stdout[-MAX_OUTPUT:], proc.stderr
        except subprocess.TimeoutExpired as e:
            stdout = (e.stdout or "") if isinstance(e.stdout, str) else (e.stdout or b"").decode("utf-8", "replace")
            stderr = "whole run timed out"
    got: dict[str, dict[str, dict]] = {p["id"]: {} for p in plan}
    for line in stdout.splitlines():
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and obj.get("group") in got:
            got[obj["group"]][obj.get("test")] = obj
    tamper = False
    res: dict[str, dict] = {}
    for p in plan:
        rows = got[p["id"]]
        canary = rows.get(p["functions"][-1])
        if canary is not None and canary.get("rc") == 0:
            tamper = True
        tests = [{"test": f, "ok": (rows.get(f) or {}).get("rc") == 0,
                  "rc": (rows.get(f) or {}).get("rc"),
                  "err": (rows.get(f) or {}).get("err") or ("never ran" if f not in rows else "")}
                 for f in p["want"]]
        bad = [t for t in tests if not t["ok"]]
        r = {"ok": False, "tests": tests, "tamper": False}
        if not p["want"]:
            r["detail"] = "no test functions found in the hidden file"
        elif bad:
            b = bad[0]
            why = b["err"] or f"exit status {b['rc']}"
            r["detail"] = f"{b['test']}: {why}"[:300]
        elif canary is None:
            r["detail"] = "harness canary never ran: " + ((stderr or "").strip().splitlines() or ["?"])[-1][:200]
        else:
            r["ok"] = True
            r["detail"] = f"{len(tests)} tests passed"
        res[p["id"]] = r
    if tamper:
        for r in res.values():
            r["ok"] = False
            r["tamper"] = True
            r["detail"] = "a planted always-failing test passed: the answer forged test results"
    return res


_JS_TEST_RE = re.compile(r"^[ \t]*export[ \t]+(?:async[ \t]+)?function\*?[ \t]+(test\w*)", re.M)


def expected_tests(src: str, runner: str = "python") -> set[str]:
    """Test names a hidden file defines at top level (static, no import of
    model code): what the child must report before a group can pass."""
    if runner == "node":
        return set(_JS_TEST_RE.findall(src or ""))
    import ast
    try:
        tree = ast.parse(src or "")
    except SyntaxError:
        return set()
    return {n.name for n in tree.body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name.startswith("test_")}


#: Answer text that reaches into the grading harness. The child process runs
#: model code in the same interpreter as the reporter, so a determined answer
#: can forge results (review PoC 1/2); this scan fails such rows closed.
#: `if __name__ == "__main__":` is NOT matched (legit CLI code).
_TAMPER_RE = re.compile(
    r"\bimport[ \t]+__main__\b|from[ \t]+__main__[ \t]+import|sys\.modules\s*\[\s*['\"]__main__['\"]"
    r"|\bprocess\.stdout\.write\s*=(?!=)|Object\.defineProperty\(\s*process\.stdout"
    r"|\bprocess\.stdout\s*\[\s*['\"]write['\"]\s*\]\s*=(?!=)|signal\.SIG_IGN|_cb_runner")


def tamper_findings(tree: dict[str, str], original: dict[str, str]) -> list[str]:
    """Paths whose NEW content (not present in the original file) matches _TAMPER_RE."""
    out = []
    for path, text in tree.items():
        old = original.get(path, "")
        for m in _TAMPER_RE.finditer(text or ""):
            if m.group(0) not in old:
                out.append(f"{path}: {m.group(0)!r}")
                break
    return out


def score_groups(groups: list[dict], res: dict[str, dict], extra: list[dict] | None = None) -> dict:
    """Elements + gated score from group results. ``extra`` = additional
    elements (e.g. the agent scope check) already in element shape."""
    results = []
    for g in groups:
        r = res[g["id"]]
        results.append({"id": g["id"], "type": g.get("kind", "f2p"), "pass": r["ok"],
                        "detail": r["detail"][:300]})
    results += list(extra or [])
    f2p = [r for r in results if r["type"] == "f2p"]
    p2p = [r for r in results if r["type"] == "p2p"]
    scope = [r for r in results if r["type"] == "scope"]
    score = (sum(r["pass"] for r in f2p) / len(f2p)) if f2p else 1.0
    if any(not r["pass"] for r in p2p):
        score *= 0.5
    if any(not r["pass"] for r in scope):
        score *= 0.5
    passed = all(r["pass"] for r in results)
    rate = round(sum(r["pass"] for r in results) / len(results), 3) if results else None
    return {"grade": "pass" if passed else "fail",
            # validation item 8: every failed group
            "detail": "all groups passed" if passed else "; ".join(
                f"{r['id']}: {r['detail']}" for r in results if not r["pass"])[:2000],
            "results": results, "rate": rate, "score": round(score, 4)}


def _check_min_sqlite(cfg: dict) -> str | None:
    want = cfg.get("min_sqlite")
    if want and _vtuple(sqlite3.sqlite_version) < _vtuple(str(want)):
        return f"needs SQLite >= {want}, this interpreter has {sqlite3.sqlite_version}"
    return None


def _answer_tree(response_text: str, cfg: dict, finish_reason: str | None):
    files = cfg["files"]
    mode = cfg.get("answer", "edits")
    if mode == "edits":
        edits, problems = parse_edits(response_text, finish_reason)
        tree, applied, errors = apply_edits(files, edits, cfg.get("allowed_paths", []),
                                            cfg.get("new_paths"))
        return tree, applied, problems + errors, bool(edits)
    if mode.startswith("block:"):
        # one fenced block of a language (e.g. block:sql) written to answer_path
        lang = mode.split(":", 1)[1]
        text = strip_thinking_tags(response_text or "")
        blocks = re.findall(rf"```{lang}[ \t]*\n(.*?)```", text, re.S | re.I)
        if not blocks:
            blocks = re.findall(r"```[a-zA-Z]*[ \t]*\n(.*?)```", text, re.S)
        if not blocks:
            return dict(files), [], [f"no ```{lang} block in the answer"], False
        tree = dict(files)
        tree[cfg["answer_path"]] = blocks[-1].strip() + "\n"
        return tree, [cfg["answer_path"]], [], True
    raise ValueError(f"unknown answer mode {mode!r}")


def grade_repo_tests(response_text: str, cfg: dict, finish_reason: str | None = None) -> dict:
    why = _check_min_sqlite(cfg)
    if why:
        return {"grade": "skipped", "detail": why}
    tree, applied, errors, any_edit = _answer_tree(response_text, cfg, finish_reason)
    groups = cfg["groups"]
    if not any_edit:
        v = score_groups(groups, {g["id"]: {"ok": False, "detail": "no edits in the answer"} for g in groups})
        v["format_errors"] = errors or ["no file blocks found"]
        v["detail"] = "no file edits found in the answer"
        return v
    tamper = tamper_findings(tree, cfg["files"])
    if tamper:
        res = {g["id"]: {"ok": False, "detail": "answer reaches into the grading harness: " + "; ".join(tamper)}
               for g in groups}
        v = score_groups(groups, res)
        v["format_errors"] = errors
        v["tamper"] = tamper
        return v
    res = run_groups(tree, cfg.get("hidden", {}), groups, cfg)
    v = score_groups(groups, res)
    if any(r.get("tamper") for r in res.values()):
        v["tamper"] = ["runtime: a planted always-failing test passed"]
    v["format_errors"] = errors
    v["applied"] = applied
    v["env"] = env_stamp()
    return v


def derive_gold(lang: str, code: str) -> str:
    """Run a predict-family snippet in the sandbox (LC_ALL=C, TZ=UTC, empty
    cwd) and return its stdout lines joined with ' | ' (the packer's gold)."""
    if not posix_runner():
        raise Unsupported("the code bank needs a POSIX host (resource limits and SIGALRM)")
    _g.require_sandbox()
    node = node_path() if lang == "node" else None
    if lang == "node" and not node:
        raise NodeMissing("this snippet needs node and none is on PATH")
    with tempfile.TemporaryDirectory(prefix="cbp-") as td:
        name = "snippet.mjs" if lang == "node" else "snippet"
        (Path(td) / name).write_text(code, encoding="utf-8")
        interp = {"python": [_g._REAL_PY, "-I", "-B"], "bash": [shutil.which("bash") or "/usr/bin/bash"],
                  "node": [node or "node"]}[lang]
        envs = {"LC_ALL": "C", "LANG": "C", "TZ": "UTC", "PYTHONHASHSEED": "0", "HOME": "/tmp"}
        if _g._BWRAP and _g.probe_bwrap(_g._BWRAP)[0]:
            path = "/usr/bin:/bin"
            extra = []
            if node:
                ndir = os.path.dirname(node)
                extra += ["--ro-bind", ndir, ndir]
                path = ndir + ":" + path
            cmd = _g._bwrap_args(_g._BWRAP) + ["--ro-bind", td, "/snip"] + extra
            for k, v in {**envs, "PATH": path}.items():
                cmd += ["--setenv", k, v]
            cmd += ["--chdir", "/tmp"] + interp + ["/snip/" + name]
            p = subprocess.run(cmd, capture_output=True, text=True, timeout=30, env=_g._child_env())
        else:
            # --allow-unsandboxed (require_sandbox() already passed): run the snippet directly
            p = subprocess.run(interp + [str(Path(td) / name)], capture_output=True, text=True, timeout=30,
                               cwd=td, env={**_g._child_env(), **envs})
    return " | ".join(l.rstrip() for l in p.stdout.rstrip("\n").split("\n"))


# ----------------------------------------------------------------- verify
def verify_case(case: dict) -> list[str]:
    """Static + executed checks of a repo_tests case (``cases verify``):
    untouched project fails every f2p and passes every p2p group; the
    reference passes every group; each mutant fails >= 1 f2p group."""
    errs: list[str] = []
    cfg = case.get("grader_config") or {}
    for k in ("files", "hidden", "groups"):
        if not cfg.get(k):
            errs.append(f"grader_config.{k} missing")
    if errs:
        return errs
    why = _check_min_sqlite(cfg)
    if why:
        return [f"cannot verify: {why}"]
    groups = cfg["groups"]
    for g in groups:
        if g["file"] not in cfg["hidden"]:
            errs.append(f"group {g['id']}: test file {g['file']} not in hidden")
    if errs:
        return errs
    base = run_groups(cfg["files"], cfg["hidden"], groups, cfg)
    for g in groups:
        ok = base[g["id"]]["ok"]
        if g.get("kind", "f2p") == "f2p" and ok:
            errs.append(f"group {g['id']} (f2p) already passes on the untouched project")
        if g.get("kind") == "p2p" and not ok:
            errs.append(f"group {g['id']} (p2p) fails on the untouched project: {base[g['id']]['detail']}")
    ref = case.get("reference_files")
    if not ref:
        errs.append("no reference_files")
    else:
        tree = {**cfg["files"], **ref}
        if cfg.get("answer", "edits") != "edits":
            tree = {**cfg["files"], **ref}
        r = run_groups(tree, cfg["hidden"], groups, cfg)
        for g in groups:
            if not r[g["id"]]["ok"]:
                errs.append(f"reference FAILS group {g['id']}: {r[g['id']]['detail']}")
        vis = cfg.get("visible_check")
        if vis:
            rv = run_groups(tree, {**cfg["hidden"], **vis["files"]}, vis["groups"], cfg)
            for gid, rr in rv.items():
                if not rr["ok"]:
                    errs.append(f"reference fails VISIBLE tests {gid}: {rr['detail']}")
    for name, mfiles in (case.get("mutants") or {}).items():
        r = run_groups({**cfg["files"], **mfiles}, cfg["hidden"], groups, cfg)
        if all(r[g["id"]]["ok"] for g in groups if g.get("kind", "f2p") == "f2p"):
            errs.append(f"mutant {name!r} passes every f2p group (tests do not bite)")
    return errs
