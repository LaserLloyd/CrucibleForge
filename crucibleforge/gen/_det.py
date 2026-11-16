"""The shared deterministic launcher for 4.5 families whose grader executes model code (2026-11-14, the
lead's board-integrity item: one stored reply, one grade). Reference shape: T's IMPLIED fix 7f0315d.

- **Environment**: the graded run gets :data:`DET_ENV` and nothing else (``PYTHONHASHSEED=0`` fixes str
  ``hash()`` and set order, ``TZ=UTC``, C locale, a fixed ``HOME`` and ``PATH``).
- **Interpreter flags**: ``python -I`` implies ``-E``, which ignores ``PYTHONHASHSEED``; :func:`det_cmd`
  swaps the last ``-I`` for ``-s -S -P`` (no user site, no ``site`` import, no script dir on
  ``sys.path``), which keeps what ``-I`` isolates here because the env is :data:`DET_ENV` only.
- **Prelude** (:func:`prelude`, put in front of the in-sandbox harness before any model code runs):
  ``random.seed(0)``, ``random.Random()`` default-seeded (``_DetRandom``) and ``os.urandom`` a fixed
  stream (so ``uuid4`` and ``secrets.token_*`` are fixed too). ``random.SystemRandom`` keeps the real
  source (it bound ``os.urandom`` at import).
- **Interpreter stamp**: work-clock and timing grades differ between Python builds (planted_opt: up to
  .17 between 3.13 and 3.14), so every grade detail names the interpreter (:func:`py_note`, the wording
  of PACKS c57b5b5: ``; py 3.13.14``, plus `` (not the pinned 3.13.14)`` on any other) and the regrade
  tool refuses to regrade on an interpreter other than the pin (:func:`pin_mismatch`).

NOT pinned (open, lab card): wall clock and process time. A family whose grade reads either (solversyn,
patgen, combsyn time limits) is deterministic only up to the clock.

Families that use this module pin its digest (``_DET_SHA``) next to their own helper pins, because
``chains.revision()`` digests only the family file."""
from __future__ import annotations

import functools
import subprocess

#: the grading interpreter of every 4.5 board row: the project venv's Python (uv-pinned)
PINNED_PY = "3.13.14"

#: the graded run's whole environment
DET_ENV = {"PATH": "/usr/bin:/bin", "HOME": "/tmp", "PYTHONHASHSEED": "0", "TZ": "UTC", "LC_ALL": "C",
           "LANG": "C", "PYTHONDONTWRITEBYTECODE": "1"}

#: the interpreter flags that replace ``-I`` (``-I`` implies ``-E`` and so drops ``PYTHONHASHSEED``)
DET_FLAGS = ["-s", "-S", "-P"]

_PRELUDE = r"""
import random as _det_random, os as _det_os, hashlib as _det_hashlib
_det_random.seed(0)
class _DetRandom(_det_random.Random):
    def __init__(self, x=None):
        super().__init__(0 if x is None else x)
_det_random.Random = _DetRandom
_det_n = [0]
def _det_urandom(n, _sha=_det_hashlib.sha256):
    out = b""
    while len(out) < n:
        _det_n[0] += 1
        out += _sha(b"bench-@TAG@-%d" % _det_n[0]).digest()
    return out[:n]
_det_os.urandom = _det_urandom
del _det_random, _det_os, _det_hashlib
"""


def prelude(tag: str) -> str:
    """The prelude source for family ``tag`` (the ``os.urandom`` stream is named after it)."""
    if not tag.replace("_", "").isalnum():
        raise ValueError(f"prelude tag {tag!r}: letters, digits and _ only")
    return _PRELUDE.replace("@TAG@", tag)


def with_prelude(src: str, tag: str) -> str:
    """``src`` (a harness) with the prelude in front. A ``from __future__`` line stays first."""
    lines = src.lstrip("\n").splitlines(keepends=True)
    head = []
    while lines and lines[0].startswith("from __future__"):
        head.append(lines.pop(0))
    return "".join(head) + prelude(tag) + "".join(lines)


def det_cmd(cmd: list) -> list:
    """``cmd`` with its last ``-I`` replaced by ``-s -S -P``. A command with no ``-I`` is refused (the
    launcher never runs an interpreter it did not isolate)."""
    cmd = list(cmd)
    if "-I" not in cmd:
        raise ValueError("det_cmd: the command has no -I to replace")
    i = len(cmd) - 1 - cmd[::-1].index("-I")
    return cmd[:i] + DET_FLAGS + cmd[i + 1:]


def det_env() -> dict:
    return dict(DET_ENV)


@functools.lru_cache(maxsize=8)
def python_version(py: str) -> str:
    """``platform.python_version()`` of interpreter ``py`` (asked once, by the parent: a model cannot
    forge it). "?" when it cannot be run."""
    try:
        out = subprocess.run([py, "-I", "-c", "import platform; print(platform.python_version())"],
                             capture_output=True, text=True, timeout=20).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return "?"
    return out or "?"


def grading_python() -> str:
    """The version of the interpreter a graded run uses now (the sandbox runs ``graders._real_py()``)."""
    from .. import graders as _g
    return python_version(_g._real_py())


def py_note(version: str | None = None) -> str:
    """``"; py 3.13.14"`` for the grade detail, ``" (not the pinned 3.13.14)"`` added on any other."""
    v = version or grading_python()
    return f"; py {v}" + ("" if v == PINNED_PY else f" (not the pinned {PINNED_PY})")


def pinned_python(prof: dict | None = None) -> str:
    """The profile's grading interpreter: ``grader_pins.python`` when the profile names one, else
    :data:`PINNED_PY` (the venv)."""
    pins = (prof or {}).get("grader_pins") or {}
    v = pins.get("python") if isinstance(pins, dict) else None
    return str(v) if v else PINNED_PY


def pin_mismatch(prof: dict | None = None, version: str | None = None) -> str | None:
    """None when grading runs on the pinned interpreter, else the refusal message."""
    want, have = pinned_python(prof), version or grading_python()
    if have == want:
        return None
    return (f"the grading interpreter is Python {have}, not the pinned {want}: a regrade on another build can "
            f"move a work-clock or timing grade (planted_opt: up to .17 between 3.13 and 3.14); run the regrade "
            f"from the project venv (Python {want})")
