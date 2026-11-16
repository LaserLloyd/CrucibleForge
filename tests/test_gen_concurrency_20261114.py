"""Generator determinism under concurrency (2026-11-14, Admin D on the rig).

``gen/keyfunc.py`` was not thread-safe: ``_capture`` rebinds the module global ``_prim`` while
it runs, so a second runner thread materialising another keyfunc unit called through the
wrapper (KeyError 'id': "the instance could not be generated -- chain halted"), and an overlap
can also produce a WRONG instance silently.

Run rule: the board's instances are identical regardless of concurrency. For every registered
family, a few units (lowest and highest level, the first variant, two seeds) are materialised
serially, then again through a thread pool interleaved with grading of other units; the
instances, references and grader configs must be byte-identical, every reference must grade
as it does serially, and nothing may raise.

A family listed in ``gen.THREAD_UNSAFE`` is expected to fail (xfail, strict: once it passes,
remove it from the set, which also lifts its lock in the runner). The runner's safety net is
tested separately: through ``gen.materialize_case`` / ``graders.grade`` the same pool is
clean for those families too, because they are serialised under ``gen.family_lock``.
"""
from __future__ import annotations

import json
import random
from concurrent.futures import ThreadPoolExecutor

import pytest

from crucibleforge import gen

gen.discover()
FAMILIES = sorted(gen.REGISTRY)
POOL = 8
#: families whose materialise takes tens of seconds per instance (probe 2026-11-14, nice 15:
#: combsyn 0.1-1.2 s at L1-L4, 20 s at L5, 24 s at L6, L7 over 13 min): one seed, top level capped
SLOW = {"combsyn": 5}
#: generator keys a family needs beyond type/level/variant
EXTRA = {"solversyn": {"ffseed": 3}}


def _units(fam: str) -> list[tuple[dict, str]]:
    m = gen.REGISTRY[fam]
    vs = list(getattr(m, "VARIANTS", ()) or [None])[:1]
    out = []
    for v in vs:
        levels = m.LEVELS[v] if (v is not None and v in m.LEVELS) else m.LEVELS
        lv = sorted(k for k in levels if isinstance(k, int))
        top = max([x for x in lv if x <= SLOW[fam]] or lv[:1]) if fam in SLOW else lv[-1]
        for level in sorted({lv[0], top}):
            for cid in (("D1",) if fam in SLOW else ("D1", "D2")):
                out.append(({"type": fam, "level": level, **({"variant": v} if v else {}),
                             **EXTRA.get(fam, {})}, cid))
    return out


def _snap(m, g, cid) -> str:
    inst = m.materialize(dict(g), release_seed="r", case_id=cid)
    ref = m.reference(dict(g), release_seed="r", case_id=cid)
    return json.dumps(inst, sort_keys=True, default=str) + "\x00" + ref


def _guard(fam: str, materialise, grade):
    m = gen.REGISTRY[fam]
    us = _units(fam)
    try:
        base = [_snap(m, g, c) for g, c in us]
    except gen.GeneratorError as e:
        if "bank not found" in str(e):
            pytest.skip(str(e))              # a family whose data ships with a pack (theme_rp)
        raise
    insts = [m.materialize(dict(g), release_seed="r", case_id=c) for g, c in us]
    refs = [m.reference(dict(g), release_seed="r", case_id=c) for g, c in us]
    #: the serial grade of each reference (1.0 for most families; a console family's
    #: reference is scored by its simulator, compactor's = 0.85): the pool must reproduce it
    want = [grade(m, us[i], refs[i], insts[i]) for i in range(len(us))]
    jobs = [(k, i) for i in range(len(us)) for k in ("m", "g")] * 3
    random.Random(7).shuffle(jobs)

    def do(job):
        k, i = job
        if k == "m":
            return k, i, materialise(m, us[i])
        j = (i + 1) % len(us)
        return k, j, grade(m, us[j], refs[j], insts[j])

    errors, wrong = [], []
    with ThreadPoolExecutor(POOL) as ex:
        for f in [ex.submit(do, j) for j in jobs]:
            try:
                k, i, v = f.result()
            except Exception as e:  # noqa: BLE001 - the guard reports every error
                errors.append(f"{type(e).__name__}: {e}"[:120])
                continue
            if k == "m" and v != base[i]:
                wrong.append(f"instance {us[i]} differs")
            if k == "g" and v != want[i]:
                wrong.append(f"reference of {us[i]} graded {v}, serially {want[i]}")
    assert not errors and not wrong, (errors[:3], wrong[:3])


def _direct_m(m, u):
    return _snap(m, *u)


def _direct_g(m, u, ref, inst):
    return m.grade(ref, inst)


@pytest.mark.parametrize("fam", [pytest.param(f, marks=pytest.mark.xfail(
    reason="gen.THREAD_UNSAFE: not thread-safe yet (serialised by gen.family_lock in the runner)", strict=True))
    if f in gen.THREAD_UNSAFE else f for f in FAMILIES])
def test_instances_do_not_depend_on_concurrency(fam):
    _guard(fam, _direct_m, _direct_g)


def test_the_runner_path_serialises_an_unsafe_family(monkeypatch):
    """A family listed in THREAD_UNSAFE is clean through the family lock: a toy whose materialise
    rebinds a module global mid-call (the keyfunc defect's shape) gives identical instances."""
    import threading
    import time
    import types
    state = {"prim": None}

    def materialize(g, release_seed, case_id):
        state["prim"] = case_id                      # rebinds shared state ...
        time.sleep(0.001)                            # ... yields to another thread ...
        return {"prompt": f"{g['level']}:{state['prim']}"}   # ... and reads it back

    m = types.SimpleNamespace(FAMILY="toyunsafe", LEVELS={1: {}, 2: {}}, materialize=materialize,
                              reference=lambda g, release_seed, case_id: case_id,
                              grade=lambda ref, inst: 1.0, mutants=lambda *a, **k: [])
    gen.register(m)
    monkeypatch.setattr(gen, "THREAD_UNSAFE", {"toyunsafe"})
    monkeypatch.setattr(gen, "SERIALIZE_UNSAFE", True)
    try:
        def via_engine_m(mm, u):
            g, cid = u
            with gen.family_lock("toyunsafe"):
                return _snap(mm, g, cid)

        def via_engine_g(mm, u, ref, inst):
            with gen.family_lock("toyunsafe"):
                return mm.grade(ref, inst)

        _guard("toyunsafe", via_engine_m, via_engine_g)
        assert isinstance(gen.family_lock("toyunsafe"), type(threading.RLock()))
    finally:
        gen.unregister("toyunsafe")


def test_family_lock_is_a_no_op_for_safe_families_and_when_switched_off(monkeypatch):
    import contextlib
    monkeypatch.setattr(gen, "THREAD_UNSAFE", {"keyfunc"})
    safe = next(f for f in FAMILIES if f not in gen.THREAD_UNSAFE)
    assert isinstance(gen.family_lock(safe), contextlib.nullcontext)
    unsafe = "keyfunc"
    assert not isinstance(gen.family_lock(unsafe), contextlib.nullcontext)
    assert gen.family_lock(unsafe) is gen.family_lock(unsafe)            # one lock per family
    monkeypatch.setattr(gen, "SERIALIZE_UNSAFE", False)
    assert isinstance(gen.family_lock(unsafe), contextlib.nullcontext)


def test_materialize_case_and_grade_take_the_family_lock(monkeypatch):
    """The two engine entry points hold the lock of an unsafe family while they run."""
    from crucibleforge import graders
    from crucibleforge.api import ChatResult
    fam = "keyfunc"
    monkeypatch.setattr(gen, "THREAD_UNSAFE", {fam})
    seen = []
    m = gen.REGISTRY[fam]
    real_mat = m.materialize
    def mat(*a, **k):
        lk = gen.family_lock(fam)
        seen.append(("m", lk._is_owned()))
        return real_mat(*a, **k)
    monkeypatch.setattr(m, "materialize", mat)
    lv = sorted(k for k in m.LEVELS if isinstance(k, int))[0]
    case = gen.materialize_case({"id": "K-1", "category": "coding", "generator": {"type": fam, "level": lv}})
    name = case["grader"]
    real_grader = graders.GRADERS[name]

    def g(result, cfg):
        seen.append(("g", gen.family_lock(fam)._is_owned()))
        return real_grader(result, cfg)
    monkeypatch.setitem(graders.GRADERS, name, g)
    graders.grade(ChatResult(response_text="x", finish_reason="stop", served_model="m"), case)
    assert seen == [("m", True), ("g", True)]
