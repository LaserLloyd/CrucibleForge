"""The on-disk cache of generated-case identity digests (4.5 step 2 follow-up, 2026-11-21).

version._content_view stamps a generated case with gen.content_digest, which renders it: every process that built the
case table rendered every generated instance (58 s before the Fixes pack went live, 131 s after). digestcache keeps
the digest on disk under a key that covers every input of the render; a hit returns exactly the stored value of
gen.content_digest, a miss or a damaged entry computes it afresh and rewrites the entry."""
from __future__ import annotations

import json
import shutil
import threading
from pathlib import Path

import pytest

from crucibleforge import digestcache, gen, ladder, version

pytestmark = [pytest.mark.shipping_default, pytest.mark.live_ladder]

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def cache(tmp_path, monkeypatch):
    d = tmp_path / "digests"
    monkeypatch.setenv(digestcache.ENV, str(d))
    digestcache.clear_memo()
    yield d
    digestcache.clear_memo()


def _generated_table_cases() -> list[dict]:
    """Every generated case the live case table stamps (cases files, ladder packs, the profile's Core)."""
    raw: dict = {}
    real = version._content_view
    version._content_view = lambda c: {}                 # collect only; nothing renders
    try:
        version._case_table_kinds(raw=raw)
        for c in version._core_generated():
            raw.setdefault(c["id"], c)
    finally:
        version._content_view = real
    return [c for c in raw.values() if gen.is_generated(c)]


def test_off_in_tests_unless_named_and_never_under_the_temp_dir(monkeypatch):
    monkeypatch.delenv(digestcache.ENV, raising=False)
    assert digestcache.cache_dir() is None                # a running test: off unless it names a directory
    monkeypatch.setenv(digestcache.ENV, "off")
    assert digestcache.cache_dir() is None
    monkeypatch.delenv("PYTEST_CURRENT_TEST")
    monkeypatch.delenv(digestcache.ENV)
    d = digestcache.cache_dir()
    import tempfile
    assert d is not None and d.name == "digests" and "crucibleforge" in d.parts
    assert not str(d.resolve()).startswith(str(Path(tempfile.gettempdir()).resolve()))


def test_every_generated_case_cached_equals_fresh(cache, monkeypatch):
    cases = _generated_table_cases()
    sets = {str(c.get("ladder_pack") or c.get("core") or c["id"]) for c in cases}
    assert len(cases) > 150 and len(sets) > 20
    fresh: dict = {}
    real = gen.content_digest

    def spy(c, with_location=False):
        d = real(c, with_location)
        fresh[c["id"]] = d
        return d
    monkeypatch.setattr(gen, "content_digest", spy)
    cold = {c["id"]: digestcache.digest(c) for c in cases}
    assert cold == fresh and len(fresh) == len(cases)                       # a miss is content_digest itself
    digestcache.clear_memo()
    fresh.clear()
    warm = {c["id"]: digestcache.digest(c) for c in cases}
    assert warm == cold and not fresh                                       # a hit renders nothing
    # one case of every family against an independent fresh digest (a plain dict copy, rendered from scratch)
    monkeypatch.setattr(gen, "content_digest", real)
    seen = set()
    for c in cases:
        if c["generator"]["type"] not in seen:
            seen.add(c["generator"]["type"])
            assert warm[c["id"]] == real(dict(c)), c["id"]


def test_a_lazy_case_has_the_eager_digest_and_stays_unrendered(cache):
    from crucibleforge import config
    from crucibleforge.lazycase import LazyCase
    lz = next(c for c in config.load_cases() if isinstance(c, LazyCase) and c.get("ladder_pack")
              and c["generator"]["type"] == "keyfunc")
    eager = gen.materialize_case(dict(dict.items(lz)))
    want = gen.content_digest(eager)
    assert digestcache.digest(lz) == want and not lz.rendered
    digestcache.clear_memo()
    assert digestcache.digest(lz) == want and not lz.rendered


def _tree(tmp: Path) -> Path:
    """A copy of the source dirs the key hashes."""
    dst = tmp / "src"
    for d in digestcache.SOURCE_DIRS:
        if (ROOT / d).exists():
            shutil.copytree(ROOT / d, dst / d, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    return dst


def test_one_source_byte_or_one_spec_field_misses(cache, tmp_path, monkeypatch):
    src = _tree(tmp_path)
    monkeypatch.setattr(digestcache, "ROOT", src)
    c = {"id": "KF-x", "category": "coding", "generator": {"type": "keyfunc", "level": 1, "key": "core"}}
    k0 = digestcache.entry_key(c)
    assert k0 and digestcache.entry_key(dict(c)) == k0
    # one generator source byte (a family file, a helper module, a data file): every key moves
    for rel in ("crucibleforge/gen/keyfunc.py", "crucibleforge/gen/_patchsrc/SPEC.md.txt", "crucibleforge/graders.py"):
        p = src / rel
        b = p.read_bytes()
        p.write_bytes(b + b" ")
        digestcache.clear_memo()
        assert digestcache.entry_key(c) != k0, rel
        p.write_bytes(b)
        digestcache.clear_memo()
        assert digestcache.entry_key(c) == k0, rel
    # a pack / bank file the families read
    bank = next((src / "cases" / "ladder").rglob("*.json"))
    bank.write_bytes(bank.read_bytes() + b"\n")
    digestcache.clear_memo()
    assert digestcache.entry_key(c) != k0
    bank.write_bytes(bank.read_bytes()[:-1])
    digestcache.clear_memo()
    assert digestcache.entry_key(c) == k0
    # one spec field, the case's own keys, the release seed, the ladder root
    for change in ({"generator": {"type": "keyfunc", "level": 2, "key": "core"}},
                   {"generator": {"type": "keyfunc", "level": 1, "key": "core", "per_rung": 2}},
                   {"generator": {"type": "keyfunc", "level": 1, "key": "core", "seed_case": "KF-y"}},
                   {"id": "KF-y"}, {"max_tokens": 9}):
        assert digestcache.entry_key({**c, **change}) != k0, change
    monkeypatch.setenv("CRUCIBLEFORGE_RELEASE_SEED", "other")
    assert digestcache.entry_key(c) != k0
    monkeypatch.delenv("CRUCIBLEFORGE_RELEASE_SEED")
    monkeypatch.setattr(ladder, "LADDER_DIR", tmp_path / "elsewhere")
    digestcache.clear_memo()
    assert digestcache.entry_key(c) != k0


def test_the_quarantine_store_path_does_not_move_the_key(cache, monkeypatch):
    """Where the run's quarantine store lives cannot change a case: a per-test store
    (conftest) must not turn every warmed entry into a miss (the parallel-judge
    children rebuilt the case table cold and timed out, 2026-11-22)."""
    c = {"id": "KF-x", "category": "coding", "generator": {"type": "keyfunc", "level": 1, "key": "core"}}
    monkeypatch.setenv("CRUCIBLEFORGE_QUARANTINE", "/q/one")
    digestcache.clear_memo()
    k1 = digestcache.entry_key(c)
    monkeypatch.setenv("CRUCIBLEFORGE_QUARANTINE", "/q/two")
    digestcache.clear_memo()
    assert digestcache.entry_key(c) == k1
    monkeypatch.delenv("CRUCIBLEFORGE_QUARANTINE")
    digestcache.clear_memo()
    assert digestcache.entry_key(c) == k1


def test_a_test_family_is_never_cached(cache):
    import types
    toy = types.SimpleNamespace(FAMILY="toy_dc", LEVELS={1: {}}, materialize=lambda g, **k: {"prompt": "p"},
                                reference=lambda *a, **k: "", mutants=lambda *a, **k: [], grade=lambda *a: 0.0)
    gen.register(toy)
    try:
        c = {"id": "T-1", "category": "coding", "generator": {"type": "toy_dc", "level": 1}}
        assert digestcache.entry_key(c) is None
        assert digestcache.digest(c) == gen.content_digest(c)
        assert not cache.exists() or not list(cache.rglob("*.json"))
    finally:
        gen.unregister("toy_dc")


def test_a_damaged_entry_is_recomputed_and_rewritten(cache):
    c = {"id": "KF-x", "category": "coding", "generator": {"type": "keyfunc", "level": 1, "key": "core"}}
    want = gen.content_digest(c)
    assert digestcache.digest(c) == want
    path = digestcache.entry_path(digestcache.entry_key(c))
    for bad in (b"", b"{not json", json.dumps({"key": "x", "digest": "0" * 16}).encode(),
                json.dumps({"key": digestcache.entry_key(c), "digest": 5}).encode(), b"\xff\xfe"):
        path.write_bytes(bad)
        digestcache.clear_memo()
        assert digestcache.digest(c) == want
        assert json.loads(path.read_text(encoding="utf-8"))["digest"] == want
    assert not [p for p in path.parent.iterdir() if p.suffix == ".tmp"]


def test_an_unwritable_cache_still_answers(tmp_path, monkeypatch):
    blocker = tmp_path / "file"
    blocker.write_text("x", encoding="utf-8")
    monkeypatch.setenv(digestcache.ENV, str(blocker / "digests"))     # a directory under a file: cannot exist
    digestcache.clear_memo()
    c = {"id": "KF-x", "category": "coding", "generator": {"type": "keyfunc", "level": 1, "key": "core"}}
    assert digestcache.digest(c) == gen.content_digest(c)


def test_concurrent_writers_leave_one_valid_entry(cache):
    c = {"id": "KF-x", "category": "coding", "generator": {"type": "keyfunc", "level": 1, "key": "core"}}
    want = gen.content_digest(c)
    out, errs = [], []

    def run():
        try:
            out.append(digestcache.digest(dict(c)))
        except Exception as e:  # noqa: BLE001
            errs.append(e)
    ts = [threading.Thread(target=run) for _ in range(8)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert not errs and out == [want] * 8
    entries = list(cache.rglob("*"))
    assert [p for p in entries if p.is_file() and p.suffix == ".json"] and not [p for p in entries if p.suffix == ".tmp"]


def test_the_case_table_uses_the_cache(cache, monkeypatch):
    calls = []
    real = digestcache.digest
    monkeypatch.setattr(digestcache, "digest", lambda c: calls.append(c["id"]) or real(c))
    c = {"id": "KF-x", "category": "coding", "generator": {"type": "keyfunc", "level": 1, "key": "core"}}
    view = version._content_view(c)
    assert calls == ["KF-x"] and view["_generated"] == gen.content_digest(c)


def test_threads_build_the_live_case_table_once(monkeypatch):
    built = []
    gate = threading.Event()

    def slow(*a, **k):
        built.append(1)
        gate.wait(2)
        return {"X": {"case": "c", "grading": "g"}}, {"X": "set"}
    monkeypatch.setattr(version, "_case_table_kinds", slow)
    monkeypatch.setattr(version, "cases_hash", lambda: "h")
    monkeypatch.setattr(version, "_files_key", lambda: ("k-threads",))
    version._CASE_TABLE.clear()
    res = []
    ts = [threading.Thread(target=lambda: res.append(version.current_case_table())) for _ in range(4)]
    for t in ts:
        t.start()
    import time
    time.sleep(0.5)                      # all four are asking before the first build returns
    gate.set()
    for t in ts:
        t.join()
    version._CASE_TABLE.clear()
    assert len(built) == 1 and len(res) == 4 and all(r == {"X": {"case": "c", "grading": "g"}} for r in res)
