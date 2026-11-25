"""Model-written code in the container runs as another user with no network (4.5.1, the container image only).

The image has no bubblewrap that works under Docker's default profile, so the graders run model code under the
documented unsandboxed switch. This module closes what that leaves open, without touching any grader file:

* :func:`harden_self` makes the benchmark process non-dumpable, so no other user can read its ``/proc/<pid>/environ``
  (where a provider key lives) or attach to it.
* :func:`install` (called once at CLI start) wraps ``subprocess.Popen`` so that every program the GRADERS start (calls
  made from the grader modules: graders, codebank, fixcheck, fixopen, gen.*) is started through the setuid helper
  named by ``$CRUCIBLEFORGE_JAIL`` (deploy/cf-jail.c): scratch directory handed to the jail user, resource limits,
  uid/gid 65534, no IPv4/IPv6 sockets, no ptrace, then exec. The argv, environment and working directory the grader
  chose are unchanged, so verdicts are the same; only who runs the program and what it can reach changes.

Nothing happens outside the image: with no ``$CRUCIBLEFORGE_JAIL`` both functions only make the process
non-dumpable (Linux) or do nothing. ``python -m crucibleforge.jail --selftest`` runs a hostile program through the
real path and prints what it could reach (the offline end-to-end check runs it in the built image)."""
from __future__ import annotations

import ctypes
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ENV = "CRUCIBLEFORGE_JAIL"
#: modules whose subprocesses run model-written code (or the graders' own drivers of it)
JAILED_MODULES = ("crucibleforge.graders", "crucibleforge.codebank", "crucibleforge.fixcheck",
                  "crucibleforge.fixopen", "crucibleforge.gen.", "crucibleforge.jail")
_PR_SET_DUMPABLE = 4
_installed = False


def harden_self() -> bool:
    """prctl(PR_SET_DUMPABLE, 0): /proc/<pid>/environ, /proc/<pid>/mem and ptrace of this process need root."""
    if not sys.platform.startswith("linux"):
        return False
    try:
        return ctypes.CDLL(None, use_errno=True).prctl(_PR_SET_DUMPABLE, 0, 0, 0, 0) == 0
    except (OSError, AttributeError):
        return False


def helper() -> str | None:
    h = os.environ.get(ENV, "").strip()
    return h if h and os.path.isfile(h) else None


def _caller_jailed(depth: int = 12) -> bool:
    f = sys._getframe(2)
    for _ in range(depth):
        if f is None:
            return False
        name = f.f_globals.get("__name__", "")
        if name.startswith("crucibleforge."):
            return name.startswith(JAILED_MODULES)
        f = f.f_back
    return False


def wrap_argv(args, h: str):
    """``args`` prefixed with the helper (a str command is left to the shell rules: graders never use one)."""
    if isinstance(args, (str, bytes)):
        return args
    args = list(args)
    if args and args[0] == h:
        return args
    return [h] + args


def install() -> bool:
    """Make this process non-dumpable and, inside the image, route the graders' programs through the helper."""
    global _installed
    h = helper()
    if h or os.environ.get("CRUCIBLEFORGE_ALLOW_UNSANDBOXED") == "1":
        harden_self()                       # model code may run as a user that could otherwise read this process
    if not h or _installed:
        return bool(h)
    base = subprocess.Popen

    class JailPopen(base):  # type: ignore[misc, valid-type]
        def __init__(self, args, *a, **kw):
            if _caller_jailed():
                args = wrap_argv(args, h)
            super().__init__(args, *a, **kw)

    subprocess.Popen = JailPopen  # type: ignore[misc]
    _installed = True
    return True


_HOSTILE = r'''
import glob, json, os, socket
out = {"uid": os.getuid(), "gid": os.getgid()}
out["key_in_env"] = sorted(k for k in os.environ if "KEY" in k or "TOKEN" in k)
seen = []
for p in glob.glob("/proc/[0-9]*/environ"):
    try:
        b = open(p, "rb").read()
    except OSError:
        continue
    if b"KEY=" in b or b"TOKEN=" in b:
        seen.append(p)
out["proc_environ_with_key"] = seen
def tryw(p):
    try:
        with open(p, "w") as f:
            f.write("x")
        os.unlink(p)
        return True
    except OSError as e:
        return e.__class__.__name__
def tryr(p):
    try:
        return len(open(p, "rb").read())
    except OSError as e:
        return e.__class__.__name__
data = os.environ.get("CF_SELFTEST_DATA", "/data")
out["write_data"] = tryw(os.path.join(data, "PWNED"))
out["write_results"] = tryw(os.path.join(data, "results", "PWNED"))
out["read_models_yaml"] = tryr(os.path.join(data, "models.yaml"))
out["write_app"] = tryw("/app/PWNED")
out["write_cwd"] = tryw("own-scratch-file")
def net(host, port):
    try:
        s = socket.create_connection((host, port), 2)
        s.close()
        return True
    except OSError as e:
        return e.__class__.__name__ + ":" + str(e.errno)
out["net_internet"] = net("1.1.1.1", 443)
out["net_host"] = net(os.environ.get("CF_SELFTEST_HOST", "host.docker.internal"), 80)
out["net_loopback"] = net("127.0.0.1", 9)
import resource
out["rlimit_nproc"] = resource.getrlimit(resource.RLIMIT_NPROC)[0]
out["rlimit_cpu"] = resource.getrlimit(resource.RLIMIT_CPU)[0]
out["rlimit_fsize"] = resource.getrlimit(resource.RLIMIT_FSIZE)[0]
print("SELFTEST " + json.dumps(out))
'''


def selftest() -> dict:
    """Run the hostile program the way a grader does (graders' interpreter, -I, a scratch cwd) and judge it."""
    from . import graders as _g
    with tempfile.TemporaryDirectory(prefix="jailtest-") as td:
        p = Path(td) / "hostile.py"
        p.write_text(_HOSTILE, encoding="utf-8")
        r = subprocess.run([_g._REAL_PY, "-I", str(p)], capture_output=True, text=True, cwd=td,
                           env=_g._child_env(), timeout=60)
    line = next((ln for ln in r.stdout.splitlines() if ln.startswith("SELFTEST ")), None)
    if not line:
        return {"ok": False, "error": (r.stderr or r.stdout)[-500:]}
    got = json.loads(line[len("SELFTEST "):])
    fails = []
    if got["uid"] == os.getuid() or got["uid"] == 0:
        fails.append(f"runs as uid {got['uid']} (the benchmark's own or root)")
    if got["key_in_env"]:
        fails.append(f"key names in its environment: {got['key_in_env']}")
    if got["proc_environ_with_key"]:
        fails.append(f"reads a key from {got['proc_environ_with_key']}")
    for k in ("write_data", "write_results", "write_app"):
        if got[k] is True:
            fails.append(f"{k}: allowed")
    if isinstance(got["read_models_yaml"], int):
        fails.append("reads models.yaml")
    if got["write_cwd"] is not True:
        fails.append(f"cannot write its own scratch directory ({got['write_cwd']})")
    for k in ("net_internet", "net_host", "net_loopback"):
        if got[k] is True:
            fails.append(f"{k}: connected")
    for k in ("rlimit_nproc", "rlimit_cpu", "rlimit_fsize"):
        if got[k] in (-1, resource_infinity()):
            fails.append(f"{k}: unlimited")
    return {"ok": not fails, "fails": fails, "seen": got}


def resource_infinity() -> int:
    import resource
    return resource.RLIM_INFINITY


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] != ["--selftest"]:
        print("usage: python -m crucibleforge.jail --selftest", file=sys.stderr)
        return 2
    from crucibleforge import jail as mod        # run as -m, this file is __main__: use the package module
    if not mod.install():
        print(f"jail selftest: ${ENV} is not set to the helper: nothing to test here", file=sys.stderr)
        return 3
    res = mod.selftest()
    print(json.dumps(res, indent=1))
    return 0 if res["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
