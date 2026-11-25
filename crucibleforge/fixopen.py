"""The bug-fix checker's units without bubblewrap, under the switch every code test honours (4.5.1).

``--allow-unsandboxed`` / ``CRUCIBLEFORGE_ALLOW_UNSANDBOXED=1`` (graders.ALLOW_UNSANDBOXED) says the user has declared
the environment itself to be the isolation (a container: the Docker image has no bubblewrap and its compose file sets
the switch). The code bank honours it; fixcheck refused every unit without a working bwrap, so the Fixes segment of a
run in the image stayed "fix rows not graded: no sandbox" and the run could never be ranked (third Docker run,
2026-11-25).

:func:`install` (called once at CLI start) points ``fixcheck.run_unit`` at :func:`run_unit_unsandboxed` ONLY when the
switch is in force AND no working bwrap exists in this process. Where bwrap works nothing changes. The checker's files
(fixrows45._FILES, the checker stamp) and fixrows45 itself are untouched, so no stored row goes stale and no verdict
moves: the same driver, unit limits, environment and host-side comparison run; only the mount namespace is missing,
and the model's code runs as the user, as every other code test does under that switch (same warning line)."""
from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path

_lock = threading.Lock()


def _open_driver(h: Path, w: Path, o: Path) -> str:
    """fixcheck's driver with its three fixed sandbox paths (/h, /w, /out) pointed at real directories; raises
    codebank.Unsupported when the driver no longer has the expected shape (fail closed: the row stays not graded)."""
    from . import codebank as _cb
    from . import fixcheck
    d = fixcheck.DRIVER_PY
    for a, b in (('"/h/unit.json"', repr(str(h / "unit.json"))), ('os.chdir("/w")', f"os.chdir({str(w)!r})"),
                 ('os.path.join("/w", p)', f"os.path.join({str(w)!r}, p)"),
                 ('"/out/obs.json"', repr(str(o / "obs.json")))):
        if d.count(a) != 1:
            raise _cb.Unsupported("fixcheck driver changed shape: no unsandboxed run")
        d = d.replace(a, b)
    return d


def run_unit_unsandboxed(tree: dict[str, str], payload: dict, timeout: int | None = None) -> tuple[list | None, str]:
    """fixcheck.run_unit without bwrap: (observations or None, stderr tail)."""
    from . import codebank as _cb
    from . import fixcheck
    from . import graders as _g
    if not _cb.posix_runner():
        raise _cb.Unsupported("fixcheck needs a POSIX host")
    _g.require_sandbox()                              # the switch must still be in force (raises otherwise)
    with _lock:
        _g._sandbox_cmd("fixcheck-unit.py", "")       # builds an argv only: the switch's one warning line, graders' words
    timeout = timeout or fixcheck.UNIT_TIMEOUT_S
    unit = {k: v for k, v in payload.items() if k != "flags"}
    unit.update({"as": fixcheck.AS_BYTES, "fsize": fixcheck.FSIZE_BYTES, "cpu": fixcheck.CPU_S})
    with tempfile.TemporaryDirectory(prefix="fixck-") as td:
        root = Path(td)
        h, w, o, t = root / "h", root / "w", root / "out", root / "tmp"
        for d in (h, w, o, t):
            d.mkdir()
        for rel, content in tree.items():
            p = w / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")
        (h / "driver.py").write_text(_open_driver(h, w, o), encoding="utf-8")
        (h / "unit.json").write_text(json.dumps(unit), encoding="utf-8")
        env = {"PATH": "/usr/bin:/bin", "HOME": str(t), "TMPDIR": str(t), "PYTHONHASHSEED": "0", "TZ": "UTC",
               "LC_ALL": "C", "LANG": "C", "PYTHONDONTWRITEBYTECODE": "1"}
        cmd = [_g._REAL_PY] + list(payload.get("flags") or ["-I", "-B"]) + [str(h / "driver.py")]
        try:
            p = subprocess.run(cmd, stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding="utf-8",
                               errors="replace", env=env, cwd=str(w), timeout=timeout)
            rc, err = p.returncode, p.stderr
        except subprocess.TimeoutExpired:
            rc, err = -9, f"unit timed out after {timeout}s"
        observed = fixcheck._read_obs(o / "obs.json") if rc == 0 else None
        shutil.rmtree(w, ignore_errors=True)
    tail = [ln for ln in (err or "").strip().splitlines() if ln.strip()]
    return observed, (tail[-1] if tail else ("" if rc == 0 else f"exit status {rc}"))[:300]


def install() -> bool:
    """Point fixcheck.run_unit at :func:`run_unit_unsandboxed` when the switch is in force and no working bwrap exists
    here; True when it did. Idempotent; never touches a process that has a sandbox."""
    from . import fixcheck
    from . import graders as _g
    if not _g.ALLOW_UNSANDBOXED or _g.sandbox_available():
        return False
    fixcheck.run_unit = run_unit_unsandboxed
    return True


# ------------------------------------------------------------------ rows stored while no route existed
#: why a stored fix row is graded again (regraded_by.reason)
REGRADE_REASON = "the fix check was unavailable (no sandbox) when the row was made; graded from the stored reply"


def route_available() -> bool:
    """A fix unit can be graded in this process: a working bwrap, or the unsandboxed switch (fixopen runner)."""
    from . import graders as _g
    install()
    return bool(_g.sandbox_available() or _g.ALLOW_UNSANDBOXED)


def unavailable(r: dict) -> bool:
    fc = r.get("fixcheck")
    return isinstance(fc, dict) and bool(fc.get("unavailable"))


def regrade_unavailable(label: str, cases: list[dict]) -> int:
    """Grade every stored one-shot fix row of ``label`` whose check was unavailable (``fixcheck: {unavailable}``),
    from its stored reply with the live run's code (fixrows45.reply_verdict, as runner.grade_single_row calls it), and
    append a superseding copy (config.dedupe_rows: the last row per job wins) that differs only in ``fixcheck`` and
    ``regraded_by``. No model call. Nothing happens without a grading route. A row whose case content changed is left
    (it is re-made anyway); an agent row (CB-F1) is left too: its final tree is not stored, so it cannot be graded
    from the row (cli._first_reply_jobs owes it again while a route exists). Returns the rows graded."""
    import copy
    import datetime as _dt
    from . import config, fixrows45
    from .regrade import _result_of
    from .runner import _answer_view
    from .version import row_current
    fix = {c["id"]: c for c in cases if fixrows45.is_fix_case(c) and not c.get("env")}
    if not fix or not route_available():
        return 0
    n = 0
    at = _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")
    for r in config.load_transcripts(label):
        c = fix.get(r.get("case_id"))
        if c is None or not unavailable(r) or not row_current(r):
            continue
        res = _answer_view(_result_of(r))
        v = fixrows45.reply_verdict(c, res.response_text, finish_reason=res.finish_reason,
                                    completion_tokens=res.completion_tokens, sent=r.get("max_tokens_sent"))
        if not v or v.get("unavailable"):
            continue
        new = copy.deepcopy(r)
        new["fixcheck"] = v
        new["regraded_by"] = {"tool": "crucibleforge.fixopen", "reason": REGRADE_REASON, "at": at,
                              "regraded": ["fixcheck"],
                              **({"previous": r["regraded_by"]} if r.get("regraded_by") else {})}
        config.append_transcript(label, new)
        n += 1
    return n
