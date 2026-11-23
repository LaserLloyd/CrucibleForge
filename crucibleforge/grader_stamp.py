"""Grader stamps (fix round 2026-11-18, P3; review finding 6): every row carries the hash of the code and data
that graded it, so a key or checker fix after the rows were graded shows up as STALE instead of mixing old and
new grades silently.

A row's grading family (``grader_family``):

* a generated unit (a chain instance or a generated case): its generator family, e.g. ``implied``;
* any other case: ``static:<category>``.

Its stamp (``grader_stamp``, 12 hex) is a sha256 over, in a fixed order:

* the family's module source and the source of every ``crucibleforge.gen`` helper module it imports, followed
  transitively (``_implied_more``, ``_det``, ``_stack_common`` ...): checker functions, key generation, the
  family's own judge rubric and prompt text all live there. ``chains.revision`` (row ``chain_revision``) hashes
  the family's own file only, so a fix in a helper module never moved it;
* every data file in ``crucibleforge/gen`` the sources name (``_solversyn_t5.json``, ``_patchsrc/*``);
* the family's ``REVISION`` / ``GRADER_REVISION`` attribute when it has one (``toolworld`` does);
* for ``static:<category>``: the grading module(s) of that category (``graders.py``; the code bank's
  ``codebank.py``; tool use ``tooltask.py``).

A judge verdict is invalidated by its own instrument stamp (``judge_digest``: rubric sha + judge settings), which
this does not replace. :func:`row_is_stale` is pure on a row (plus the current tree) and is what the report layer
and ``crucibleforge report --check-stale`` call.
"""
from __future__ import annotations

import hashlib
import importlib
import inspect
import re
import types
from functools import lru_cache
from pathlib import Path

PKG = Path(__file__).resolve().parent
GEN_DIR = PKG / "gen"
GEN_PREFIX = "crucibleforge.gen"
#: the grading modules of the non-generated categories (relative to the package)
STATIC_MODULES = {"codebank": ("codebank.py",), "codeagent": ("codebank.py",),
                  "codebank_long": ("codebank.py",), "codeagent_long": ("codebank.py",),
                  "tooluse": ("tooltask.py", "graders.py")}
DEFAULT_STATIC = ("graders.py",)
_DATA_RE = re.compile(r"[\w./-]*(?:_[\w-]+\.json|_patchsrc)")


def _read(path: Path) -> bytes:
    """One source or data file's bytes (tests swap this to edit a checker in a temp copy)."""
    try:
        return path.read_bytes()
    except OSError:
        return b""


def family_of(row_or_case: dict) -> str:
    """The grading family of a row or a case."""
    ch = row_or_case.get("chain") or row_or_case.get("chain_spec") or {}
    if isinstance(ch, dict) and ch.get("family"):
        return str(ch["family"])
    g = row_or_case.get("generator") or {}
    if isinstance(g, dict) and g.get("type"):
        return str(g["type"])
    if row_or_case.get("grader_family"):
        return str(row_or_case["grader_family"])
    return f"static:{row_or_case.get('category') or 'unknown'}"


def _helper_modules(mod: types.ModuleType) -> list[types.ModuleType]:
    """``mod`` and every crucibleforge.gen module it reaches through its globals (modules, functions, classes)."""
    seen: dict[str, types.ModuleType] = {}
    todo = [mod]
    while todo:
        m = todo.pop()
        name = getattr(m, "__name__", "")
        if name in seen or not name.startswith(GEN_PREFIX + "."):
            continue                    # never the package itself: its registry names every family
        seen[name] = m
        for v in vars(m).values():
            if isinstance(v, types.ModuleType):
                todo.append(v)
            else:
                owner = getattr(v, "__module__", None)
                if isinstance(owner, str) and owner.startswith(GEN_PREFIX + ".") and owner not in seen:
                    try:
                        todo.append(importlib.import_module(owner))
                    except Exception:  # noqa: BLE001 - an unimportable helper is hashed by name only
                        pass
    return [seen[k] for k in sorted(seen)]


def _data_files(src: bytes) -> list[Path]:
    out = set()
    for m in _DATA_RE.finditer(src.decode("utf-8", "replace")):
        name = Path(m.group(0)).name
        p = GEN_DIR / name
        if p.is_dir():
            out.update(q for q in p.rglob("*") if q.is_file())
        elif p.is_file():
            out.add(p)
    return sorted(out)


@lru_cache(maxsize=None)
def stamp(family: str) -> str:
    """The grader stamp of ``family`` against the current tree (cached; :func:`clear` forgets)."""
    h = hashlib.sha256()
    h.update(family.encode())
    if family.startswith("static:"):
        cat = family.split(":", 1)[1]
        for rel in STATIC_MODULES.get(cat, DEFAULT_STATIC):
            h.update(rel.encode())
            h.update(_read(PKG / rel))
        return h.hexdigest()[:12]
    from . import gen as _gen
    try:
        mod = _gen.get(family)
    except Exception:  # noqa: BLE001 - an unknown family: hashed by name (stable, and never "current" by accident)
        return h.hexdigest()[:12]
    files: list[Path] = []
    for m in _helper_modules(mod):
        try:
            f = Path(inspect.getsourcefile(m) or "")
        except TypeError:
            continue
        if f.is_file():
            files.append(f)
    data: set[Path] = set()
    for f in files:
        src = _read(f)
        h.update(f.name.encode())
        h.update(src)
        data.update(_data_files(src))
    for f in sorted(data):
        h.update(str(f.relative_to(GEN_DIR)).encode())
        h.update(_read(f))
    for attr in ("REVISION", "GRADER_REVISION"):
        if getattr(mod, attr, None) is not None:
            h.update(f"{attr}={getattr(mod, attr)}".encode())
    return h.hexdigest()[:12]


def clear() -> None:
    stamp.cache_clear()


def fields_for(case: dict) -> dict:
    """``{"grader_family", "grader_stamp"}`` for a row of ``case`` (the runner stamps every row)."""
    fam = family_of(case)
    return {"grader_family": fam, "grader_stamp": stamp(fam)}


def row_is_stale(row: dict) -> tuple[bool, str]:
    """(stale?, reason). A row is stale when its grader stamp is missing (graded before stamps existed) or no
    longer matches the current code and data of its family."""
    fam = family_of(row)
    have = row.get("grader_stamp")
    if not have:
        return True, f"{fam}: no grader stamp (graded before 2026-11-18 grader stamps)"
    now = stamp(fam)
    if have != now:
        return True, f"{fam}: graded under {have}, the grading code is now {now}"
    return False, ""


def scored(row: dict) -> bool:
    """A row that counts toward a score (an error row never does)."""
    return row.get("grade") != "error" and not row.get("error")


def stale_rows(rows: list[dict]) -> list[tuple[dict, str]]:
    out = []
    for r in rows:
        if not scored(r):
            continue
        bad, why = row_is_stale(r)
        if bad:
            out.append((r, why))
    return out
