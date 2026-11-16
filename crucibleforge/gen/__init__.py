"""Generator registry (4.5, E1): seeded case families.

A case file entry (or a ladder set, see ``ladder.py``) may carry a
``generator`` block whose ``type`` names a family module in this package::

    {"id": "KF-core", "category": "coding",
     "generator": {"type": "keyfunc", "level": 2, "key": "core"}}

At load (``config.load_cases``) the family's ``materialize`` renders the
real case keys (prompt, system, turns, checks, tests, reference, mutants ...).
The instance seed is :func:`instance_seed`, so a new ``release_seed`` (a
profile key) regenerates every instance, and ``version.case_hash`` covers the
generator block plus the release seed (:func:`hash_extra`).

A family module (stdlib only) defines ``FAMILY``, ``LEVELS``,
``materialize(gen, *, release_seed, case_id) -> dict``,
``reference(gen, *, release_seed, case_id) -> str``,
``mutants(gen, *, release_seed, case_id) -> list[str]`` and
``grade(answer, materialized) -> float``. Modules that define ``FAMILY`` and
whose name does not start with ``_`` are discovered automatically; tests can
:func:`register` a toy family.

Block types that are NOT registered (the long-context haystacks of
``longctx_gen``: niah / multikey / count) keep their old path.
"""
from __future__ import annotations

import copy
import hashlib
import importlib
import os
import pkgutil

#: family name -> module (or any object with the family attributes)
REGISTRY: dict = {}

#: used when neither set_release_seed() nor the profile names one
DEFAULT_RELEASE_SEED = "4.5.0"
_RELEASE_SEED: list = [None]
_PROFILE_SEED: dict = {}

#: keys materialize() may never overwrite
_PROTECTED = ("id", "category", "generator", "ladder_pack")
REQUIRED_ATTRS = ("FAMILY", "LEVELS", "materialize", "reference", "mutants", "grade")


class GeneratorError(ValueError):
    pass


#: families that FAIL the concurrency guard (tests/test_gen_concurrency_20261114.py: the same
#: instances materialised serially and through a thread pool, interleaved with grading, must be
#: byte-identical). Until a family is fixed, its materialise and grade calls are serialised
#: (:func:`family_lock`); the guard's xfail(strict) on these names fails once a family passes,
#: so the name is removed here and the lock lifts. Empty since 2026-11-14: keyfunc (``_capture``
#: rebound the module global ``_prim``; KeyError 'id' on a 4-thread rig pool) was fixed in
#: 476ca11 (``_capture_rules``) and passes the guard.
THREAD_UNSAFE: set = set()
#: the safety net's switch (default on: correctness over a few milliseconds)
SERIALIZE_UNSAFE = True
_FAMILY_LOCKS: dict = {}
_FAMILY_LOCKS_GUARD = __import__("threading").Lock()


def family_lock(family: str | None):
    """A re-entrant lock serialising one THREAD_UNSAFE family's materialise / grade calls
    (other families, and the switch off, get a no-op context)."""
    import contextlib
    if not SERIALIZE_UNSAFE or not family or family not in THREAD_UNSAFE:
        return contextlib.nullcontext()
    with _FAMILY_LOCKS_GUARD:
        lk = _FAMILY_LOCKS.get(family)
        if lk is None:
            import threading
            lk = _FAMILY_LOCKS[family] = threading.RLock()
    return lk


def register(module) -> None:
    """Add a family (module or namespace object) to the registry."""
    missing = [a for a in REQUIRED_ATTRS if not hasattr(module, a)]
    if missing:
        raise GeneratorError(f"generator family {getattr(module, 'FAMILY', '?')!r} "
                             f"lacks {missing}")
    REGISTRY[str(module.FAMILY)] = module


def unregister(family: str) -> None:
    REGISTRY.pop(family, None)


def discover() -> dict:
    """(Re)scan the package for family modules."""
    for info in pkgutil.iter_modules(__path__):
        if info.name.startswith("_"):
            continue
        mod = importlib.import_module(f"{__name__}.{info.name}")
        if hasattr(mod, "FAMILY"):
            register(mod)
    return REGISTRY


def get(family: str):
    if family not in REGISTRY:
        discover()
    try:
        return REGISTRY[family]
    except KeyError:
        raise GeneratorError(f"unknown generator family {family!r} "
                             f"(known: {sorted(REGISTRY)})") from None


def families() -> list[str]:
    discover()
    return sorted(REGISTRY)


def is_registered(gen_type) -> bool:
    if not gen_type:
        return False
    if gen_type not in REGISTRY:
        try:
            discover()
        except Exception:  # noqa: BLE001 - a broken family module must not hide longctx cases
            pass
    return gen_type in REGISTRY


def is_generated(case: dict) -> bool:
    """True when the case's ``generator`` block names a registered family."""
    g = (case or {}).get("generator")
    return isinstance(g, dict) and is_registered(g.get("type"))


# ------------------------------------------------------------ release seed

def set_release_seed(seed) -> None:
    """Set the ACTIVE PROFILE's release seed (None = the default). Precedence in :func:`release_seed`:
    ``$CRUCIBLEFORGE_RELEASE_SEED`` > this > the default profile's file > DEFAULT_RELEASE_SEED.
    ``profiles.activate_release_seed`` calls it once per command (and again, with None or another
    value, when the process switches profile); loading a profile never does."""
    _RELEASE_SEED[0] = None if seed is None else str(seed)
    if seed is not None and os.environ.get("PYTEST_CURRENT_TEST"):
        _refuse_test_seed_on_live_ladder(str(seed))


def _refuse_test_seed_on_live_ladder(seed: str) -> None:
    """Test-suite tripwire, inert outside a running pytest test (PYTEST_CURRENT_TEST).

    The live ladder's generated packs are frozen for the production seed only; under any
    other seed every instance is enumerated afresh, and the combsyn level-7 set alone takes
    minutes per instance (2026-11-14: test_gen_registry ran ~30 minutes this way). A test
    that sets a non-production seed must point the ladder at its own packs first."""
    if seed in (DEFAULT_RELEASE_SEED, _profile_seed()):
        return
    from pathlib import Path
    from .. import ladder
    live = (Path(ladder.ROOT_DIR) / "cases" / "ladder").resolve()
    if any(Path(r).resolve() == live for r in ladder.roots()):
        _RELEASE_SEED[0] = None
        raise RuntimeError(
            f"test sets release seed {seed!r} while ladder.roots() includes the live {live}: "
            "every generated pack would be materialised under a test seed (minutes per combsyn "
            "level-7 instance). Point ladder.LADDER_DIR / ladder.roots at the test's own packs "
            "first, or drop the live_ladder marker.")


def release_seed() -> str:
    """The active release seed: $CRUCIBLEFORGE_RELEASE_SEED (explicit), else the active profile's
    ``release_seed`` (set_release_seed), else the default profile's, else DEFAULT_RELEASE_SEED."""
    env = os.environ.get("CRUCIBLEFORGE_RELEASE_SEED")
    if env:
        return env
    if _RELEASE_SEED[0] is not None:
        return _RELEASE_SEED[0]
    return _profile_seed()


def _profile_seed() -> str:
    """``release_seed:`` of the default profile, read with a one-line regex
    (no YAML parse: this runs on every case_hash lookup) and cached per file
    mtime."""
    try:
        import re
        from pathlib import Path
        from ..profiles import ARCHIVE_PROFILE, PROFILES_DIR
        p = Path(PROFILES_DIR) / f"{ARCHIVE_PROFILE}.yaml"
        key = (str(p), p.stat().st_mtime_ns)
        if key not in _PROFILE_SEED:
            _PROFILE_SEED.clear()
            m = re.search(r"^release_seed:\s*[\"']?([^\s\"'#]+)", p.read_text(encoding="utf-8"), re.M)
            _PROFILE_SEED[key] = m.group(1) if m else DEFAULT_RELEASE_SEED
        return _PROFILE_SEED[key]
    except Exception:  # noqa: BLE001 - no readable profile: the documented default
        return DEFAULT_RELEASE_SEED


def instance_seed(family: str, release_seed: str, case_id: str, level, key=None) -> str:
    """The contract's instance seed (hex sha256)."""
    return hashlib.sha256(
        f"{family}:{release_seed}:{case_id}:{level}:{key or ''}".encode()).hexdigest()


def instance_int(family: str, release_seed: str, case_id: str, level, key=None) -> int:
    """The same seed as a 64-bit int (for families that seed ``random``/``generate`` with an int)."""
    return int(instance_seed(family, release_seed, case_id, level, key)[:16], 16)


# ------------------------------------------------------------ materialising

def check_variant(fam, gen: dict) -> None:
    """An unknown ``variant`` is an error (a typo used to fall back silently to the default):
    the family lists its own in ``VARIANTS``; a family without one takes no variant."""
    v = gen.get("variant")
    if v is None:
        return
    allowed = tuple(getattr(fam, "VARIANTS", ()) or ())
    if v not in allowed:
        raise GeneratorError(f"generator {gen.get('type') or getattr(fam, 'FAMILY', '?')}: unknown variant {v!r} "
                             f"(allowed: {', '.join(allowed) if allowed else 'none, this family has no variants'})")


def seed_case_id(case: dict) -> str:
    """The case id a family seeds the instance on: the generator's ``seed_case`` when given
    (chains ``seed_as``: an aliased set asks its target's instances, whose families may seed
    on the case id, e.g. implied), else the case's own id."""
    return str((case.get("generator") or {}).get("seed_case") or case["id"])


def _fam_gen(gen: dict) -> dict:
    """A copy of the generator block for the family (``seed_case`` is the engine's, not the
    family's)."""
    g = copy.deepcopy(gen)
    g.pop("seed_case", None)
    return g


def materialize_case(case: dict, release_seed_: str | None = None) -> dict:
    """Render a generated case IN PLACE (what config.load_cases calls) and
    return it. The generator block stays on the case; the materialised keys
    are added (never ``id`` / ``category`` / ``generator``)."""
    gen = case["generator"]
    fam = get(gen["type"])
    check_variant(fam, gen)
    rs = release_seed() if release_seed_ is None else str(release_seed_)
    with family_lock(gen["type"]):
        out = fam.materialize(_fam_gen(gen), release_seed=rs, case_id=seed_case_id(case))
    if not isinstance(out, dict):
        raise GeneratorError(f"{case['id']}: {gen['type']}.materialize must return a dict")
    for k, v in out.items():
        if k not in _PROTECTED:
            case[k] = v
    case["_gen_release_seed"] = rs
    return case


#: materialised keys that are not what the generator produces (left out of content_digest)
_NOT_CONTENT = ("meta", "_gen_release_seed", "ladder_pack")


def content_digest(case: dict, with_location: bool = False) -> str:
    """Digest of what the generator actually produces for ``case`` under the active
    release seed (prompt, system, turns, checks, tests, reference ... everything
    materialize() returns but ``meta``): a change to a family's code, levels or
    spec text changes it, so version.case_hash re-stamps the instance. ``with_location`` is the
    recipe before 2026-11-14 (the set file's absolute path included), kept only so
    ``regrade --stamp pack_location`` can prove a stored stamp was this case at another path."""
    import json
    try:
        mat = materialize_case(copy.deepcopy(case))
        # ``ladder_pack`` is where the set file lives on THIS checkout, not content: in the view
        # it tied every PK- row to the tree path it ran from (D, rc2 board 2026-11-14).
        # version._content_view / generation_view already leave it out.
        view = {k: v for k, v in mat.items()
                if k not in _NOT_CONTENT or (with_location and k == "ladder_pack")}
    except Exception as e:  # noqa: BLE001 - a broken family must not break hashing; verify reports it
        view = {"_materialize_error": type(e).__name__}
    return hashlib.sha256(json.dumps(view, sort_keys=True, default=str).encode()).hexdigest()[:16]


def hash_extra(case: dict):
    """What ``version.case_hash`` adds for a generated case (None otherwise):
    the release seed, so bumping it nulls the instance's rows."""
    return release_seed() if is_generated(case) else None


def describe_item(case: dict, release_seed_: str, show: bool = True, check: bool = False) -> str:
    """Text for ``crucibleforge gen``: the family, seed, level parameters and the item's
    prompt (system + scripted turns for a multi-turn case)."""
    gen = case["generator"]
    fam = get(gen["type"])
    mat = materialize_case(copy.deepcopy(case), release_seed_)
    lv = fam.LEVELS.get(gen.get("level")) or {}
    params = {k: v for k, v in lv.items() if not isinstance(v, (list, dict, tuple, set))}
    out = [f"family {gen['type']}  level {gen.get('level')}"
           + (f"  variant {gen['variant']}" if gen.get("variant") else "")
           + f"  release seed {release_seed_}  case id {case['id']}",
           f"level params: {params}"]
    if check:
        mut = len(fam.mutants(_fam_gen(gen), release_seed=release_seed_, case_id=seed_case_id(case)))
        out.append(f"mutants: {mut}")
    if not show:
        return "\n".join(out) + "\n"
    if mat.get("system"):
        out += ["", "SYSTEM:", str(mat["system"])]
    if mat.get("history"):
        out += ["", f"HISTORY: {len(mat['history'])} messages"]
    turns = mat.get("turns")
    if turns:
        for i, t in enumerate(turns, 1):
            out += ["", f"--- USER TURN {i} ---", str(t)]
    else:
        out += ["", "PROMPT:", str(mat.get("prompt", ""))]
    return "\n".join(out) + "\n"


def _agree(a: float, b: float) -> bool:
    return abs(a - b) < 1e-9


def engine_score(case: dict, answer: str) -> float:
    """What the engine would score ``answer`` for the MATERIALISED case: single-turn cases
    through ``graders.grade`` (the case's own grader/checks/tests), scripted multi-turn cases
    through the real ``_run_multiturn`` + ``rpdeep.score_row`` with the answer split on
    TURN_SEP (no model, no network)."""
    from .. import graders, rpdeep, runner
    from ..api import ChatResult
    if case.get("turns"):
        import threading
        from ._stack_common import split_turns
        n = len(case["turns"])
        replies = (split_turns(answer) + [""] * n)[:n]

        class _Ctx:
            lock = threading.Lock()

            def budget(self, k):
                return k

            def recover_for(self, c):
                return False

            def call(self, messages, **kw):
                return ChatResult(response_text=replies.pop(0), finish_reason="stop",
                                  completion_tokens=5, served_model="m")
        rows = runner._run_multiturn(case, {"case_id": case["id"]}, _Ctx(), 2000, 0.7, 0.95, 1)
        res = rpdeep.score_row(rows[-1], case, rows)
        return float(res["rate"]) if res.get("status") == "ok" else 0.0
    v = graders.grade(ChatResult(response_text=answer, finish_reason="stop", served_model="m"), case)
    if v is None:
        return 0.0
    for k in ("score", "rate"):
        if v.get(k) is not None:
            return float(v[k])
    return 1.0 if v["grade"] == "pass" else 0.0


def _draft_wrong(ref: str, mutants: list) -> str | None:
    """A draft-right/final-wrong answer: the reference, then a wrong one (single turn only)."""
    return None if not mutants else ref + "\n\nOn second thought, my final answer:\n\n" + mutants[0]


def verify_case(case: dict, release_seed_: str | None = None) -> list[str]:
    """Problems of a generated case (``cases verify``): materialising twice
    must give identical output; the reference must grade 1.0 and every mutant
    below 1.0 (the family's own ``grade``); a lazy answer must not pass."""
    errs: list[str] = []
    gen = case["generator"]
    fam = get(gen["type"])
    rs = release_seed() if release_seed_ is None else str(release_seed_)
    kw = {"release_seed": rs, "case_id": seed_case_id(case)}
    gen = _fam_gen(gen)
    try:
        a = fam.materialize(copy.deepcopy(gen), **kw)
        b = fam.materialize(copy.deepcopy(gen), **kw)
    except Exception as e:  # noqa: BLE001
        return [f"generator {gen['type']}: materialize failed: {e}"]
    if a != b:
        errs.append("generator is not deterministic (two materialisations differ)")
    try:
        ref = fam.reference(copy.deepcopy(gen), **kw)
        g = fam.grade(ref, a)
        # a family whose reference is a competent-but-not-perfect answer (COMPACTOR 0.85, PATTERN's k<=13
        # predictor ~0.72) declares REFERENCE_GRADE = (lo, hi); every other family must grade 1.0
        lo, hi = getattr(fam, "REFERENCE_GRADE", (1.0, 1.0))
        if not (lo - 1e-9 <= g <= hi + 1e-9):
            errs.append(f"generator reference grades {g:.3f}, not "
                        + (f"1.0" if (lo, hi) == (1.0, 1.0) else f"within {lo}-{hi}"))
        for i, m in enumerate(fam.mutants(copy.deepcopy(gen), **kw), 1):
            gm = fam.grade(m, a)
            if gm >= 1.0 - 1e-9:
                errs.append(f"generator mutant {i} still scores {gm:.3f}")
        if fam.grade("", a) > 0.0:
            errs.append("generator: an empty answer scores above 0")
        # the score that counts is the ENGINE's (checks/tests/grader of the materialised case):
        # it must equal the family grade for the reference, every mutant, a draft-right /
        # final-wrong answer and the empty answer
        if not getattr(fam, "GRADE_IS_ENGINE", False):
            probe = copy.deepcopy(case)
            for k, v in a.items():
                if k not in _PROTECTED:
                    probe[k] = copy.deepcopy(v)
            muts = fam.mutants(copy.deepcopy(gen), **kw)
            answers = [("reference", ref)] + [(f"mutant {i}", m) for i, m in enumerate(muts, 1)] + [("empty", "")]
            if not a.get("turns"):
                d = _draft_wrong(ref, muts)
                if d:
                    answers.append(("draft-right/final-wrong", d))
            for name, ans in answers:
                fg, eg = float(fam.grade(ans, a)), engine_score(probe, ans)
                if abs(fg - eg) > 1e-3:          # rpdeep rounds its rate to 3 places
                    errs.append(f"engine scores the {name} {eg:.3f} but the family grade is {fg:.3f}")
    except Exception as e:  # noqa: BLE001
        errs.append(f"generator {gen['type']}: reference/mutant check failed: {e}")
    return errs
