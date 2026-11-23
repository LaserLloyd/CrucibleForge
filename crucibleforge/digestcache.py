"""On-disk cache of generated-case identity digests (4.5, 2026-11-21).

``version._content_view`` stamps a generated case with ``gen.content_digest``, which renders the case: every process
that built the case table rendered every generated instance (about a minute, two with the Fixes pack). This module
keeps each digest on disk. A hit returns the stored value of ``gen.content_digest`` for exactly the same inputs; a
miss, an unreadable or damaged entry computes it with ``gen.content_digest`` and rewrites the entry (write then
rename, so concurrent processes never read half an entry).

The key covers every input of the render:
* the bytes of every file under the source dirs the families import or read (the whole engine package with its
  data, the case trees with the ladder banks and the private ladder, the 4.1 archive, the profiles, the code bank
  sources), hashed whole rather than hand-picked, plus the ladder / case dirs in use when one lies outside them;
* the case as handed in (its generator block with every field, its id and any loader keys), the release seed,
  the interpreter version and platform, and every ``CRUCIBLEFORGE_*`` setting.
Only a family that is a module file of the generator package is cached (a family registered at run time, as tests
do, is not). Inside a running test the cache is off unless ``CRUCIBLEFORGE_DIGEST_CACHE`` names a directory (tests
patch family internals that no file hash can see).
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import platform
import sys
import threading
from pathlib import Path

#: the cache directory; "off" disables it
ENV = "CRUCIBLEFORGE_DIGEST_CACHE"
#: settings that only say where run state lives and cannot change a case: left out of the key
#: (a per-test quarantine store otherwise turned every warmed entry into a miss)
_ENV_NOT_KEYED = frozenset({ENV, "CRUCIBLEFORGE_QUARANTINE"})
ROOT = Path(__file__).resolve().parent.parent
#: every tree a family imports or reads (relative to ROOT)
SOURCE_DIRS = ("crucibleforge", "cases", "archive", "profiles", "codebank_src")
_RECIPE = 1

_FILE_MEMO: dict = {}           # (path, mtime_ns, size) -> sha256 of the bytes
_PIN = threading.local()
_LOCK = threading.Lock()


def clear_memo() -> None:
    with _LOCK:
        _FILE_MEMO.clear()


def _user_cache() -> Path:
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    elif sys.platform == "darwin":
        base = str(Path.home() / "Library" / "Caches")
    else:
        base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "crucibleforge" / "digests"


def cache_dir() -> Path | None:
    v = os.environ.get(ENV)
    if v:
        return None if v.strip().lower() == "off" else Path(v)
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return None
    return _user_cache()


# ---------------------------------------------------------------- the key
def _file_sha(p: Path) -> str:
    st = p.stat()
    k = (str(p), st.st_mtime_ns, st.st_size)
    h = _FILE_MEMO.get(k)
    if h is None:
        h = hashlib.sha256(p.read_bytes()).hexdigest()
        with _LOCK:
            _FILE_MEMO[k] = h
    return h


def _tree_into(h, top: Path, label: str) -> None:
    if not top.exists():
        h.update(f"{label}\0absent\0".encode())
        return
    files = sorted(p for p in top.rglob("*") if p.is_file() and "__pycache__" not in p.parts
                   and p.suffix not in (".pyc", ".tmp"))
    for p in files:
        h.update(f"{label}/{p.relative_to(top).as_posix()}\0{_file_sha(p)}\0".encode())


def _extra_dirs() -> list[Path]:
    from . import config, ladder
    return [Path(d) for d in (config.CASES_DIR, config.PRIVATE_CASES_DIR, ladder.LADDER_DIR, *ladder.roots())]


def source_fingerprint() -> str:
    pinned = getattr(_PIN, "fp", None)
    if pinned is not None:
        return pinned
    h = hashlib.sha256()
    root = Path(ROOT)
    for d in SOURCE_DIRS:
        _tree_into(h, root / d, d)
    covered = [(root / d).resolve() for d in SOURCE_DIRS]
    for d in _extra_dirs():
        r = d.resolve()
        h.update(f"dir\0{r.as_posix()}\0".encode())
        if not any(r == c or c in r.parents for c in covered):
            _tree_into(h, r, f"extra:{r.as_posix()}")
    h.update(f"py\0{sys.version}\0{platform.system()}\0{platform.machine()}\0".encode())
    for k in sorted(os.environ):
        if k.startswith("CRUCIBLEFORGE_") and k not in _ENV_NOT_KEYED:
            h.update(f"env\0{k}\0{os.environ[k]}\0".encode())
    return h.hexdigest()


@contextlib.contextmanager
def pinned():
    """Inside the block the source fingerprint is computed once (one case table build)."""
    if getattr(_PIN, "fp", None) is not None:
        yield
        return
    _PIN.fp = source_fingerprint()
    try:
        yield
    finally:
        _PIN.fp = None


def _state(case: dict) -> dict:
    """The case as it stands, without rendering a lazy one (an unrendered lazy case and its rendered self have the
    same digest: the render's keys are overwritten by the digest's own render)."""
    return dict(dict.items(case))


def _cacheable_family(gen_type) -> bool:
    from . import gen as _gen
    try:
        fam = _gen.get(gen_type)
    except Exception:  # noqa: BLE001
        return False
    name = getattr(fam, "__name__", "")
    f = getattr(fam, "__file__", None)
    if not (name.startswith(_gen.__name__ + ".") and f and sys.modules.get(name) is fam):
        return False
    gdir = Path(_gen.__file__).resolve().parent
    return Path(f).resolve().parent == gdir


def entry_key(case: dict) -> str | None:
    """The cache key of ``case`` (None = not cacheable: an unregistered or run-time family)."""
    from . import gen as _gen
    try:
        g = dict.get(case, "generator")
        if not isinstance(g, dict) or not _cacheable_family(g.get("type")):
            return None
        blob = json.dumps({"recipe": _RECIPE, "src": source_fingerprint(), "seed": _gen.release_seed(),
                           "case": _state(case)}, sort_keys=True, default=str)
    except Exception:  # noqa: BLE001 - no key: compute afresh
        return None
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def entry_path(key: str) -> Path | None:
    d = cache_dir()
    return None if d is None else d / key[:2] / f"{key}.json"


def _read(path: Path, key: str) -> str | None:
    try:
        e = json.loads(path.read_text(encoding="utf-8"))
        d = e.get("digest") if isinstance(e, dict) and e.get("key") == key else None
        if isinstance(d, str) and len(d) == 16 and all(ch in "0123456789abcdef" for ch in d):
            return d
    except Exception:  # noqa: BLE001 - absent, unreadable or damaged: recompute
        pass
    return None


def _write(path: Path, key: str, digest: str) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
        tmp.write_text(json.dumps({"key": key, "digest": digest}), encoding="utf-8")
        os.replace(tmp, path)
    except Exception:  # noqa: BLE001 - an unwritable cache only costs the render next time
        with contextlib.suppress(Exception):
            tmp.unlink()


def digest(case: dict) -> str:
    """``gen.content_digest(case)``, from the cache when the same inputs were digested before."""
    from . import gen as _gen
    if cache_dir() is None:
        return _gen.content_digest(case)
    key = entry_key(case)
    path = entry_path(key) if key else None
    if path is None:
        return _gen.content_digest(case)
    hit = _read(path, key)
    if hit is not None:
        return hit
    d = _gen.content_digest(_state(case))
    _write(path, key, d)
    return d
