"""Difficulty ladders per sub-score (suite 3.9, the maintainer 2026-10-28):

    "We should also be able to add questions to the repo later, which enable
    moving to higher scores in the future ... we'd add in sets of 3 for each
    additional point: if the model scores basically 100 the harder questions
    are given and added to the score."

Every sub-score of a category (profile ``scores.<cat>.parts.<part>``) has a
standard level (level 0: its standard cases, worth its whole-number ``max``
points) and, optionally, a ladder of harder SETS, each worth +1 point:

    cases/ladder/<category>/<part>/set-<n>.json      (n = 1, 2, ...)
    cases/private/ladder/<category>/<part>/set-<n>.json   (local only)

A set file names its cases (existing case ids from cases/*.json — a new
question is added to its category's case file first, then listed here)::

    {"cases": ["CZ11-lex-triple-substring", "CZ08-...", "CZ09-..."],
     "level": 1,                # unlock order (default: the file's n); sets
                                # of one level are given together
     "budget": 65536,           # optional: output budget for its rows
     "context": 73728,          # optional: load the model at least this wide
     "repeats": 2,              # optional: samples per item (wins over the profile's repeats)
     "note": "why these three"}

Dropping a file is all it takes — no code change; ``all --plan`` and the
board discover it. A set is worth ONE point in total (its 3 questions ~0.33
each, partial credit inside), whatever its difficulty — sets need not step
up evenly ("some additions could be much harder"). Rule (``ladder.unlock``
in the profile, default 0.9 = "basically 100"): the sets of level L are
given only when the model scored at least that share on the standard level
and on EVERY set of every lower level. Score = standard points + the share
of each set it earned, plain additive; a category passes 10 (and Overall
20) only through sets. A set that unlocked but has not run yet is
"pending" (0 for now).

GENERATED sets (4.5, E2): a set file may carry a ``generator`` block (a family
of crucibleforge/gen) instead of a ``cases`` list::

    {"category": "coding", "level": 1,
     "generator": {"family": "keyfunc", "rungs": [1, 2, 3, 4, 5], "per_rung": 2}}

It expands to ``rungs x per_rung`` cases ``<FAMILY>-r<rung>-<i>`` (each a
seeded instance, tagged ``rung``; its key = sha256(release_seed, set path,
rung, i)). Rungs run adaptively (:func:`rungs_due`): rung r+1 runs only when
rung r's mean share reached ``ladder.rung_stop`` (default 0.5); the set's
share is the MEAN over its rungs, an unrun rung counting 0
(:func:`rung_set_share`).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import zlib
from pathlib import Path

import logging

from . import config as _config
from .config import ConfigError, ROOT_DIR

log = logging.getLogger(__name__)

LADDER_DIR = ROOT_DIR / "cases" / "ladder"
DEFAULT_UNLOCK = 0.9
DEFAULT_RUNG_STOP = 0.5
_SET = re.compile(r"^(?:set-(\d+)|classic-[a-z0-9_]+(?:-[a-z0-9_]+)*)\.json$")
#: a classic-fixed pack (``classic-<name>.json``, 4.5) has no number in its name: its n is
#: CLASSIC_N0 + crc32(name) % 900000, a pure function of the file name (stable when a sibling
#: is added or removed), so ``<key>#<n>`` stays unique and never collides with a ``set-<n>``
CLASSIC_N0 = 1000
#: the default number of questions ``pack new`` scaffolds (any count >= 1
#: is allowed; a pack is worth ONE point in total, each question 1/n)
SET_SIZE = 3
#: the fewest questions a pack may hold without ``"allow_small": true`` and
#: an ``"allow_small_reason"`` (review B7: "sets of 3")
MIN_SET_SIZE = 3


def private_root() -> Path:
    return _config.PRIVATE_CASES_DIR / "ladder"


def roots(public: bool = False) -> list[Path]:
    """Where set files live. A PUBLIC board never reads the private ladder
    dir (review S4: a private pack's points must not reach public NSFW/Chat)."""
    out = [LADDER_DIR]
    if _config.PRIVATE_ENABLED and not public:
        out.append(private_root())
    return out


def unlock_share(prof: dict | None) -> float:
    return float(((prof or {}).get("ladder") or {}).get("unlock", DEFAULT_UNLOCK))


def part_keys(prof: dict | None) -> list[str]:
    from . import tiers as _tiers
    return [f"{d['key']}/{p['key']}" for d in _tiers.scores(prof) for p in d.get("parts") or []]


def rung_stop(prof: dict | None) -> float:
    return float(((prof or {}).get("ladder") or {}).get("rung_stop", DEFAULT_RUNG_STOP))


def _set_key(f: Path) -> str:
    """``<score>/<part>/set-<n>`` of a set file (the rung-key salt)."""
    return "/".join(Path(f).parts[-3:]).removesuffix(".json")


def instance_key(release_seed: str, family: str, variant: str | None, role: str,
                 level: int, i: int) -> str:
    """The generator key of one instance: (release seed, family, variant, role, level, i).
    ``role`` is "core" (a profile's core_generated entry) or "ladder" (a pack rung), so the
    same family at the same level gives different items in Core and in a pack. Never the
    part path, the set number or the count."""
    return hashlib.sha256(f"{release_seed}:{family}:{variant or ''}:{role}:{level}:{i}"
                          .encode()).hexdigest()[:16]


def expand_generated(spec: dict, f: Path, release_seed: str | None = None) -> list[dict]:
    """The cases of a generated set (``generator`` + ``rungs`` + ``per_rung``),
    ids ``<FAMILY>-r<rung>-<i>`` (i from 1), each ``{"id", "category",
    "rung", "generator": {"type", "level": rung, "key"}}``. Pure: the same
    file and release seed always give the same cases."""
    from . import gen as _gen
    g = spec["generator"]
    if not isinstance(g, dict):
        raise ConfigError(f"ladder set {f}: 'generator' is a mapping")
    fam = g.get("family") or g.get("type")
    if not fam:
        raise ConfigError(f"ladder set {f}: generator needs a 'family'")
    rungs = g.get("rungs", spec.get("rungs", [1, 2, 3, 4, 5]))
    per = g.get("per_rung", spec.get("per_rung", 1))
    if (not isinstance(rungs, list) or not rungs
            or not all(isinstance(r, int) and not isinstance(r, bool) and r >= 1 for r in rungs)
            or len(set(rungs)) != len(rungs)):
        raise ConfigError(f"ladder set {f}: rungs is a list of distinct positive integers")
    if not isinstance(per, int) or isinstance(per, bool) or per < 1:
        raise ConfigError(f"ladder set {f}: per_rung is a positive integer")
    cat = spec.get("category") or g.get("category")
    if not cat:
        raise ConfigError(f"ladder set {f}: a generated set names its case 'category'")
    rs = _gen.release_seed() if release_seed is None else str(release_seed)
    # id prefix: explicit id_prefix wins; a set with a ``variant`` is named
    # <FAMILY>-<variant> (the PLAN §3.4 map gives each same-family set a
    # distinct variant: ISTACK-conv/-write/-nsfw, SESSION-STATE-state/-multi);
    # otherwise bare <FAMILY> (one set per non-variant family, ids unchanged)
    try:
        _gen.check_variant(_gen.get(fam), g)
    except _gen.GeneratorError as e:
        raise ConfigError(f"ladder set {f}: {e}") from e
    var = g.get("variant")
    prefix = str(spec.get("id_prefix")
                 or (f"{str(fam).upper()}-{var}" if var else str(fam).upper()))
    extra = {k: v for k, v in g.items() if k not in ("family", "rungs", "per_rung")}
    extra.pop("type", None)
    out = []
    for r in sorted(rungs):
        for i in range(1, per + 1):
            # the key never names the part or the set number (Fable gate WP-4 item 6):
            # moving a set file between parts leaves every instance, and its hash, unchanged
            key = instance_key(rs, fam, var, "ladder", r, i)
            out.append({"id": f"{prefix}-r{r}-{i}", "category": cat, "rung": r,
                        "generator": {**extra, "type": fam, "level": r, "key": key}})
    return out


def _read_set(f: Path) -> dict:
    try:
        spec = json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise ConfigError(f"ladder set {f}: {e}") from e
    if isinstance(spec, dict) and spec.get("generator") and not spec.get("cases"):
        spec = dict(spec)
        spec["cases"] = expand_generated(spec, f)
        spec["_generated"] = True
    if not isinstance(spec, dict) or not spec.get("cases"):
        raise ConfigError(f"ladder set {f}: needs a 'cases' list")
    for c in spec["cases"]:
        if not (isinstance(c, str) or (isinstance(c, dict) and c.get("id") and c.get("category"))):
            raise ConfigError(f"ladder set {f}: a case is a case id or a case object "
                              "with 'id' and 'category'")
    return spec


def _readable_sets(dirs: list[Path] | None = None, public: bool = False,
                   include_drafts: bool = True):
    """(key, n, path, spec) of every set file that READS; a broken file
    (bad JSON, no cases) is logged and skipped — one bad pack must never take
    the whole case set, the board or bench-all down (3.10 B7). `cases
    verify` / `pack verify` report it."""
    for key, n, f in _set_files(dirs, public=public):
        try:
            spec = _read_set(f)
        except ConfigError as e:
            log.error("ladder pack skipped (fix it; `crucibleforge pack verify %s`): %s", f, e)
            continue
        if spec.get("draft") and not include_drafts:
            continue
        yield key, n, f, spec


def _set_files(dirs: list[Path] | None = None, public: bool = False):
    """(key "<category>/<part>", n, path) of every set file, drafts included."""
    for root in (dirs if dirs is not None else roots(public=True) if public else roots()):
        if not root.is_dir():
            continue
        for cat_dir in sorted(p for p in root.iterdir() if p.is_dir()):
            for part_dir in sorted(p for p in cat_dir.iterdir() if p.is_dir()):
                for f in sorted(part_dir.iterdir()):
                    m = _SET.match(f.name)
                    if m:
                        if m.group(1) is None:
                            yield (f"{cat_dir.name}/{part_dir.name}",
                                   CLASSIC_N0 + zlib.crc32(f.stem.encode()) % 900000, f)
                        else:
                            yield f"{cat_dir.name}/{part_dir.name}", int(m.group(1)), f


#: env switch behind ``run --include-staged`` / ``bench-all --include-staged``
#: (the CLI sets it for the whole invocation, so the run, its judge phase and
#: the report it writes all see the staged sets)
STAGED_ENV = "CRUCIBLEFORGE_INCLUDE_STAGED"


def staged_requested() -> bool:
    """The raw switch (``$CRUCIBLEFORGE_INCLUDE_STAGED``), isolation not checked."""
    return os.environ.get(STAGED_ENV, "") not in ("", "0", "false", "no")


def include_staged() -> bool:
    """Whether staged sets (``"status": "staged"``) count in this process."""
    if not staged_requested():
        return False
    # fail closed in the LIBRARY too (the cli guard is not the only entry): the
    # switch is honoured only when the results dir is an initialised scratch dir
    # or at least not the live board (fix pass 3, N6)
    from .config import staged_isolated
    return staged_isolated()


def is_staged(spec: dict) -> bool:
    """A finished pack waiting for the owner's approval: ``"staged": true``
    (spec v2 §4.6) or the equivalent ``"status": "staged"``."""
    return spec.get("staged") is True or str(spec.get("status") or "").strip().lower() == "staged"


def discover(prof: dict | None, dirs: list[Path] | None = None,
             staged: bool | None = None) -> dict[str, list[dict]]:
    """{"<category>/<part>": [set, ...] sorted by (level, n)} for every
    declared sub-score; a set = {n, level, cases (ids), budget, context,
    note, path, staged}. ``n`` (the file number) identifies a set; ``level`` orders
    the unlocks. A set marked ``"draft": true`` (what ``pack new`` writes)
    is ignored until its author clears the flag. A set marked
    ``"status": "staged"`` (a finished pack waiting for the owner's approval
    on a dev branch) is neither run nor scored unless ``staged`` is true
    (default: :func:`include_staged`, i.e. ``--include-staged``)."""
    keys = set(part_keys(prof))
    if staged is None:
        staged = include_staged()
    found: dict[str, dict[int, dict]] = {}
    # a profile as a public board sees it (report._audience_profile) never
    # gets the private sets
    for key, n, f, spec in _readable_sets(dirs, public=bool((prof or {}).get("_public")),
                                          include_drafts=False):
        if key not in keys:
            continue
        if is_staged(spec) and not staged:
            continue
        ids = [c if isinstance(c, str) else c["id"] for c in spec["cases"]]
        excl = set((prof or {}).get("pack_exclude") or ())
        if excl and not spec.get("frontier"):   # a Frontier set may re-list an excluded case
            # T7: cases that are Main items live in exactly one place
            ids = [i for i in ids if i not in excl]
            if not ids:
                continue
        found.setdefault(key, {})[n] = {
            "n": n, "level": int(spec.get("level", n)), "cases": ids,
            "points": float(spec.get("points", 1.0)),
            "item": spec.get("item"),   # the lab registry item (registry_check, SYSTEM-45 section 3)
            "budget": spec.get("budget"), "context": spec.get("context"),
            "repeats": spec.get("repeats"),
            "note": spec.get("note"), "path": str(f), "staged": is_staged(spec),
            "rungs": _rungs_of(spec)}
    return {key: sorted(by_n.values(), key=lambda x: (x["level"], x["n"]))
            for key, by_n in found.items() if by_n}


def _rungs_of(spec: dict) -> dict[int, list[str]] | None:
    """{rung: [case ids]} of a generated set (None for an ordinary set)."""
    if not spec.get("_generated"):
        return None
    out: dict[int, list[str]] = {}
    for c in spec["cases"]:
        out.setdefault(int(c["rung"]), []).append(c["id"])
    return dict(sorted(out.items()))


def rungs_due(rungs: list[int], shares: dict[int, float | None], stop: float) -> list[int]:
    """The rungs of a generated set to run now: every rung up to and
    including the first one that has no share yet or whose share is below
    ``stop`` (adaptive stop: rung r+1 needs rung r's mean share >= stop)."""
    out: list[int] = []
    for r in sorted(rungs):
        out.append(r)
        v = shares.get(r)
        if v is None or v < stop:
            break
    return out


def rung_set_share(rungs: list[int], shares: dict[int, float | None]) -> float | None:
    """A generated set's share: the mean over ALL its rungs, an unrun rung
    counting 0. None when no rung has a share (nothing measured)."""
    if not rungs or all(shares.get(r) is None for r in rungs):
        return None
    return sum(shares.get(r) or 0.0 for r in rungs) / len(rungs)


def inline_cases(dirs: list[Path] | None = None, include_drafts: bool = False) -> list[dict]:
    """The case objects written INSIDE set files (a pack's own questions),
    each stamped ``ladder_pack`` (its set file). They join the case set
    (config.load_cases) but not the suite hash (version.cases_hash reads
    cases/*.json only): adding a pack never makes existing rows stale; each
    pack question carries its own stamp (version.ladder_case_hash)."""
    out = []
    for _key, _n, f, spec in _readable_sets(dirs, include_drafts=include_drafts):
        for c in spec["cases"]:
            if isinstance(c, dict):
                c = dict(c)
                c["ladder_pack"] = str(f)
                if spec.get("v2") is True:
                    c.setdefault("v2", True)    # a v2 set's questions are v2 cases
                if is_staged(spec):
                    # loads (so `cases verify`, `pack verify` and an explicit
                    # `run --cases ... --include-staged` see it), but the runner
                    # refuses it without the flag and the board never counts it
                    c["ladder_staged"] = True
                out.append(c)
    return out


def case_levels(prof: dict | None, sets: dict | None = None) -> dict[str, tuple[str, int]]:
    """{case id: ("<category>/<part>", n)} of every ladder case."""
    sets = discover(prof) if sets is None else sets
    out: dict[str, tuple[str, int]] = {}
    for key, seq in sets.items():
        for s in seq:
            for cid in s["cases"]:
                out.setdefault(cid, (key, s["n"]))
    return out


def states(std_share: float | None, set_shares: dict[int, float | None],
           seq: list[dict], unlock: float, terminal=(), opened: bool | None = None) -> dict[int, str]:
    """{set n: "scored" | "pending" (unlocked, not run/judged) | "errored" |
    "locked"}. Levels open in order: level L is open when the standard share
    and every set of every lower level are >= ``unlock``. ``terminal`` = the
    sets whose errored questions used up their retries (infrastructure
    failure, not model quality): state "errored", no score, never due again,
    and — having no share — they block the next level and MAXED. An explicit
    re-run replaces the errored rows and clears it. ``opened`` (4.5 bank, a routed
    profile's packs): the first level opens on the route's ``unlock`` (a score
    decision), not on ``std_share``; later levels still need ``unlock`` on every
    set below."""
    out: dict[int, str] = {}
    ok = (bool(opened) if opened is not None
          else std_share is not None and std_share >= unlock)
    for level in sorted({s["level"] for s in seq}):
        group = [s for s in seq if s["level"] == level]
        if not ok:
            out.update({s["n"]: "locked" for s in group})
            continue
        for s in group:
            out[s["n"]] = ("scored" if set_shares.get(s["n"]) is not None else
                           "errored" if s["n"] in terminal else "pending")
        ok = all(set_shares.get(s["n"]) is not None and set_shares[s["n"]] >= unlock
                 for s in group)
    return out


def due(std_share: float | None, set_shares: dict[int, float | None],
        seq: list[dict], unlock: float, opened: bool | None = None) -> list[int]:
    """The set numbers to run next for one sub-score (every unlocked set
    without a result; all sets of one level together)."""
    st = states(std_share, set_shares, seq, unlock, opened=opened)
    return [s["n"] for s in seq if st.get(s["n"]) == "pending"]


def points(std_share: float | None, std_max: float, set_shares: dict[int, float | None],
           seq: list[dict], unlock: float) -> float | None:
    """standard share x max + the share of every scored set (each set is
    worth 1 point in total)."""
    if std_share is None:
        return None
    st = states(std_share, set_shares, seq, unlock)
    pts = {s["n"]: float(s.get("points", 1.0)) for s in seq}
    return std_share * std_max + sum((set_shares.get(n) or 0.0) * pts.get(n, 1.0)
                                     for n, v in st.items() if v == "scored")


def set_cases(prof: dict, key: str, n: int, smoke: bool = False,
              sets: dict | None = None, rungs: list[int] | None = None) -> list[dict]:
    """The case dicts of set n of a sub-score, stamped ``ladder`` =
    "<key>#<n>", ``ladder_budget`` (the set's output budget, if any) and
    ``min_context`` (the set's context, if any); ``rungs`` limits a generated
    set to those rungs (adaptive stop). Repeats follow the
    profile's per-category repeats (config.repeats_for)."""
    from .profiles import _select
    sets = discover(prof) if sets is None else sets
    spec = next((s for s in sets.get(key) or [] if s["n"] == n), None)
    if spec is None:
        return []
    all_cases = {c["id"]: c for c in _config.load_cases()}
    ids = spec["cases"]
    if rungs is not None and spec.get("rungs"):
        keep = {i for r in rungs for i in spec["rungs"].get(r, [])}
        ids = [i for i in ids if i in keep]
    missing = [i for i in ids if i not in all_cases]
    if missing:
        raise ConfigError(f"ladder set {spec['path']}: unknown case id(s) {missing}")
    mapping: dict[str, list[str]] = {}
    for cid in ids:
        mapping.setdefault(all_cases[cid]["category"], []).append(cid)
    out = []
    for c in _select(prof, mapping, smoke):
        c["ladder"] = f"{key}#{n}"
        out.append(_stamp_set_limits(c, spec))
    return out


def _stamp_set_limits(c: dict, spec: dict) -> dict:
    """A set's ``budget`` / ``context`` / ``repeats`` on one of its case dicts
    (in place): ``ladder_budget``, ``min_context`` and ``repeats``. The one place both the runner
    (set_cases) and `pack verify`'s time estimate read them from."""
    if spec.get("budget"):
        c["ladder_budget"] = int(spec["budget"])
    if spec.get("repeats"):
        # a set may run each item more than once (samples per item, e.g. a
        # pack of two long agent tasks: 2 items x 2 samples); it wins over the
        # profile's per-category / per-case repeats for the set's own rows
        c["repeats"] = int(spec["repeats"])
    if spec.get("context"):
        c["min_context"] = max(int(c.get("min_context") or 0), int(spec["context"]))
    return c


def all_ladder_case_ids(prof: dict | None) -> set[str]:
    return set(case_levels(prof))


def generated_id_clashes(only: Path | None = None) -> list[str]:
    """Problems for generated sets whose case ids collide across set files
    (set ``variant`` or ``id_prefix`` to tell them apart). Reads set files
    only, so it works where config.load_cases would raise on the clash."""
    seen: dict[str, str] = {}
    out: list[str] = []
    for _key, _n, f, spec in _readable_sets():
        if not spec.get("_generated"):
            continue
        for c in spec["cases"]:
            cid = c["id"]
            if cid in seen and seen[cid] != str(f):
                if only is None or str(only.resolve()) in (str(Path(f).resolve()),
                                                          str(Path(seen[cid]).resolve())):
                    out.append(f"{f}: generated case id {cid} already used by {seen[cid]} "
                               "(give the set a generator 'variant' or an 'id_prefix')")
            seen.setdefault(cid, str(f))
    return sorted(set(out))


def verify(prof: dict | None) -> list[str]:
    """Problems of the ladder files (for `cases verify`): unknown sub-score
    dirs, empty sets, unknown case ids, a case in two sets, a standard case
    reused in a set."""
    problems = generated_id_clashes()
    if problems:
        return problems          # the case set cannot load until these are fixed
    keys = set(part_keys(prof))
    all_ids = {c["id"] for c in _config.load_cases()}
    standard = {i for ids in ((prof or {}).get("cases") or {}).values() for i in ids or []}
    seen: dict[str, str] = {}
    for root in roots():
        if not root.is_dir():
            continue
        for cat_dir in sorted(p for p in root.iterdir() if p.is_dir()):
            for part_dir in sorted(p for p in cat_dir.iterdir() if p.is_dir()):
                key = f"{cat_dir.name}/{part_dir.name}"
                if key not in keys:
                    problems.append(f"{part_dir}: no sub-score {key} in the profile")
    for _key, _n, f in _set_files():
        try:
            spec = _read_set(f)
        except ConfigError as e:
            problems.append(str(e))
            continue
        if not spec.get("draft"):
            problems += [f"{f}: {p}" for p in _size_problems(spec)]
        if spec.get("v2") is True and not spec.get("draft"):
            # spec v2 WP0-L: `cases verify` carries the v2 lint and the content
            # rules too (attestations only; --allow belongs to `pack verify`)
            from . import lint
            problems += [f"{f}: {p}" for p in lint.v2_set_problems(spec, prof)]
            for c in spec["cases"]:
                if isinstance(c, dict):
                    problems += [f"{f}: {c['id']}: {p}"
                                 for p in lint.v2_case_problems(c, spec)
                                 + lint.v2_window_report(c, spec)[0]]
            problems += [f"{f}: {p}" for p in lint.lint_pack_content(spec, f.parent.name)[0]]
    try:
        sets = discover(prof, staged=True)       # a staged pack is checked like any other
    except ConfigError as e:
        return problems + [str(e)]
    for key, seq in sets.items():
        for s in seq:
            if not s["cases"]:
                problems.append(f"{s['path']}: no question")
            for cid in s["cases"]:
                if cid not in all_ids:
                    problems.append(f"{s['path']}: unknown case {cid}")
                if cid in standard:
                    problems.append(f"{s['path']}: {cid} is a standard case")
                if cid in seen:
                    problems.append(f"{s['path']}: {cid} already in {seen[cid]}")
                seen[cid] = s["path"]
    return problems


# ------------------------------------------------------------- packs (CLI)

_GRADING_HELP = [
    "A pack is worth 1 point in total: each of its n questions counts 1/n, with",
    "partial credit inside a question where the grader gives it.",
    "Grading options per question (see docs/CUSTOM-TESTS.md):",
    "  exact / numeric  - one right answer; add verify.python to re-derive it",
    "  python_exec      - write a function; grader_config.tests + reference +",
    "                     mutants (wrong solutions the tests must catch)",
    "  tool_call / tool_script - tool use; expected calls per step",
    "  judged (rubric)  - RP/NSFW/story: judge_elements (YES/NO with a quote)",
    "                     plus deterministic checks (session_checks); keep the",
    "                     judge input under the 16k-context budget",
    "Clear \"draft\": true when the questions are real; then",
    "  crucibleforge pack verify <this file>",
]

_STUBS = {
    "exact": lambda cid, cat: {
        "id": cid, "category": cat, "difficulty": "hard", "max_tokens": 6144,
        "prompt": "TODO: the question. End with: 'Answer: <value>'.",
        "grader": "exact", "grader_config": {"answer": "TODO"},
        "verify": {"python": "TODO: an expression that re-derives the answer"}},
    "python_exec": lambda cid, cat: {
        "id": cid, "category": cat, "difficulty": "hard", "max_tokens": 6144,
        "prompt": "TODO: implement `def solve(...)`.",
        "grader": "python_exec",
        "grader_config": {"tests": "TODO: assert-based tests calling solve(...)"},
        "reference": "def solve():\n    raise NotImplementedError  # TODO",
        "mutants": ["def solve():\n    return None  # TODO: a plausible wrong solution"]},
    "judged": lambda cid, cat: {
        "id": cid, "category": cat, "difficulty": "hard", "max_tokens": 2000,
        "prompt": "TODO: the brief / card.", "rubric": "TODO (a judge.RUBRICS name)",
        "judge_elements": [{"id": "e1", "text": "TODO: a YES/NO question answered with a quote"}]},
}

_DEFAULT_GRADING = {"coding": "python_exec", "reasoning": "exact", "math": "exact"}


def _part_category(prof: dict, key: str) -> str | None:
    """The case category a sub-score's first component comes from."""
    from . import tiers as _tiers
    cat, _, pk = key.partition("/")
    for d in _tiers.scores(prof):
        if d["key"] != cat:
            continue
        for p in d.get("parts") or []:
            if p["key"] == pk and p["scoring"]:
                comps = list(p["scoring"])
                # prefer a component with a simple stub (program / one answer)
                comp = next((c for c in comps if c in _DEFAULT_GRADING), comps[0])
                return {"reasoning": "reasoning", "explicit_peak": "nsfw",
                        "willing": "nsfw", "nsfw_gated": "nsfw"}.get(comp, comp)
    return None


def new_pack(prof: dict, key: str, count: int = SET_SIZE, category: str | None = None,
             grading: str | None = None, level: int | None = None,
             root: Path | None = None) -> Path:
    """Scaffold the next pack of a sub-score: ``<root>/<cat>/<part>/set-<n>.json``
    with ``count`` question stubs, ``level`` = one above the highest existing
    (or given), ``"draft": true`` (ignored by runs until cleared) and the
    grading options note. Returns the path."""
    if key not in set(part_keys(prof)):
        raise ConfigError(f"no sub-score {key!r} (known: {', '.join(part_keys(prof))})")
    if count < 1:
        raise ConfigError("a pack needs at least one question")
    root = root or LADDER_DIR
    if (ROOT_DIR / "archive").resolve() in Path(root).resolve().parents:
        raise ConfigError(f"refusing to write a pack into the frozen archive ({root}); "
                          "use a profile that reads the live cases/ (default bench45)")
    d = root / key
    existing = [(n, _read_set(f)) for k, n, f in _set_files([root]) if k == key]
    # a classic pack's number is a crc of its name, not a sequence position (S10 review)
    n = max([x[0] for x in existing if x[0] < CLASSIC_N0], default=0) + 1
    if level is None:
        level = max([int(spec.get("level", m)) for m, spec in existing], default=0) + 1
    category = category or _part_category(prof, key) or "coding"
    if category not in _config.CATEGORIES:
        raise ConfigError(f"unknown category {category!r}")
    grading = grading or _DEFAULT_GRADING.get(category, "judged")
    if grading not in _STUBS:
        raise ConfigError(f"grading must be one of {sorted(_STUBS)}")
    tag = re.sub(r"[^A-Za-z0-9]+", "-", key.split("/")[1]).upper()
    cases = [_STUBS[grading](f"{tag}-P{n}-Q{i}-CHANGEME", category) for i in range(1, count + 1)]
    spec = {"level": level, "draft": True, "note": "TODO: what this pack tests and why it is harder",
            "_readme": _GRADING_HELP, "cases": cases}
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"set-{n}.json"
    path.write_text(json.dumps(spec, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def _size_problems(spec: dict) -> list[str]:
    """Packs come in sets of 3 (review B7); a smaller one needs
    ``"allow_small": true`` and a stated ``"allow_small_reason"``."""
    n = len(spec.get("cases") or [])
    if not n or n >= MIN_SET_SIZE:
        return []
    if not spec.get("allow_small"):
        return [f"{n} question(s): a pack holds at least {MIN_SET_SIZE} questions "
                "(or set \"allow_small\": true with an \"allow_small_reason\")"]
    if not str(spec.get("allow_small_reason") or "").strip():
        return ["allow_small without an allow_small_reason (say why this pack is small)"]
    return []


def verify_pack(path: Path, prof: dict | None = None,
                allow: list[str] | None = None) -> tuple[list[str], list[str]]:
    """(problems, notes) of one pack file: at least one question; worth 1
    point (no other ``points``); a positive integer ``level``; every case id
    known or written inline; ids unique across the whole case set; each
    inline question passes the case checks (verify_cases: required fields,
    reference passes its tests, answers re-derived, repo-task mutants bite);
    a python_exec question has >= 1 ``mutants`` entry and every mutant FAILS
    its tests; a judged question's judge input fits the judge budget with a
    full-length answer; the draft flag is reported."""
    from . import lint, verify_cases
    path = Path(path)
    problems: list[str] = []
    notes: list[str] = []
    m = _SET.match(path.name)
    if not m:
        problems.append(f"{path.name}: a pack file is named set-<n>.json or classic-<name>.json")
    try:
        spec = _read_set(path)
    except ConfigError as e:
        return [str(e)], notes
    if spec.get("draft"):
        notes.append("draft: true — runs ignore this pack until the flag is cleared")
    if "staged" in spec and not isinstance(spec["staged"], bool):
        problems.append(f"staged {spec['staged']!r}: true or false")
    if is_staged(spec):
        notes.append("staged — not run or scored unless --include-staged is passed "
                     "(the owner clears the flag to approve the pack)")
    if spec.get("status") not in (None, "", "live", "staged"):
        problems.append(f"status {spec.get('status')!r}: \"staged\" or absent")
    if "points" in spec and float(spec["points"]) != 1.0 and not spec.get("frontier"):
        problems.append(f"points {spec['points']}: a pack is worth exactly 1 point")
    lvl = spec.get("level", int(m.group(1)) if m and m.group(1) else 1)
    if not isinstance(lvl, int) or lvl < 1:
        problems.append(f"level {lvl!r}: a positive integer")
    entries = spec["cases"]
    if not entries:
        problems.append("no question (a pack needs >= 1)")
    problems += _size_problems(spec)
    if spec.get("_generated"):
        from . import gen as _gen
        for c in entries:
            try:
                problems += [f"{c['id']}: {e}" for e in _gen.verify_case(dict(c))]
            except Exception as e:  # noqa: BLE001
                problems.append(f"{c['id']}: {e}")
    clash = generated_id_clashes(Path(path)) if spec.get("_generated") else []
    if clash:
        return problems + clash, notes
    own = {c["id"] for c in entries if isinstance(c, dict)}
    me = str(Path(path).resolve())      # inline cases are stamped with an absolute set path
    others = {c["id"]: c for c in _config.load_cases()
              if not c.get("ladder_pack") or str(Path(c["ladder_pack"]).resolve()) != me}
    ids = [c if isinstance(c, str) else c["id"] for c in entries]
    if len(set(ids)) != len(ids):
        problems.append("a question id appears twice")
    for cid in ids:
        if cid in own and cid in others:
            problems.append(f"{cid}: id already used by another case")
        if cid not in own and cid not in others:
            problems.append(f"{cid}: unknown case id")
        if "CHANGEME" in cid:
            problems.append(f"{cid}: still the scaffold id")
    v2 = spec.get("v2") is True
    if "v2" in spec and not isinstance(spec["v2"], bool):
        problems.append(f"v2 {spec['v2']!r}: true or false")
    for c in entries:
        if not isinstance(c, dict):
            continue
        c = dict(c)
        if c.get("generator"):
            from . import gen as _gen
            if _gen.is_generated(c):
                _gen.materialize_case(c)
        errs = verify_cases._check(c)
        if c.get("grader") == "python_exec":
            errs += _mutant_errors(c)
        if v2:
            # spec v2 WP0-L / WP0-V: the v2 structure, and every judge window
            # with a full-length synthetic answer (replaces the whole-row
            # budget check: v2 rows are judged per window)
            errs += lint.v2_case_problems(c, spec)
            notes += [f"{c['id']}: {w}" for w in lint.term_density_warnings(c) + lint.element_key_warnings(c)]
            p_w, n_w = lint.v2_window_report(c, spec)
            errs += p_w
            notes += n_w
        else:
            errs += lint.wp8_field_problems(c)      # v2 cases get it in v2_case_problems
            if c.get("rubric") and not c.get("turns"):
                # multi-turn sessions are judged per turn group (rp_deep) or with
                # the judge's own clamp (erp_session), like the standard sessions
                errs += _judge_budget_errors(c)
        problems += [f"{c['id']}: {e}" for e in errs]
    # content rules (docs/CONTENT-RULES.md) over the pack's own questions
    allows, bad_allow = lint.parse_allows(allow)
    problems += bad_allow
    p_c, n_c = lint.lint_pack_content(spec, path.parent.name, allows)
    problems += p_c
    notes += n_c
    if v2:
        problems += lint.v2_set_problems(spec, prof)
    if prof is not None:
        try:
            key = "/".join(path.parent.parts[-2:])
            if key not in set(part_keys(prof)):
                problems.append(f"{path.parent}: no sub-score {key} in the profile")
        except Exception:  # noqa: BLE001
            pass
    notes.append(f"{len(entries)} question(s), 1 point in total ({1 / max(1, len(entries)):.2f} each)")
    if prof is not None:
        notes += _time_warnings(spec, entries, own, others, prof)
    return problems, notes


def _time_warnings(spec, entries, own, others, prof) -> list[str]:
    """4.0 pack time rule (the maintainer 2026-10-31): a pack adds at most ~5 min wall
    on a local reference model; the planner's estimate says when it would not."""
    from . import estimate as est
    cases = [dict(c) if isinstance(c, dict) else others.get(c) for c in entries]
    # stamp the set's budget/context the way set_cases does, so `pack verify`
    # times a pack exactly like `pack list` (review B4: verify read the raw case
    # dicts, ignored the pack's 65,536 budget and understated packs ~2x)
    cases = [_stamp_set_limits(dict(c), spec) for c in cases if c]
    try:
        mins = est.pack_minutes(prof, cases, spec.get("budget"), spec.get("context"))
    except Exception as e:  # noqa: BLE001 — an estimate never fails a verify
        return [f"no time estimate ({e})"]
    long_ok = str(spec.get("allow_long") or "").strip() if isinstance(spec.get("allow_long"), str) else ""
    if mins > est.PACK_LIMIT_MIN and long_ok:
        return [f"time: planner estimate ~{mins:.1f} min, over the {est.PACK_LIMIT_MIN:g}-min "
                f"pack limit, accepted (allow_long: {long_ok})"]
    if mins > est.PACK_LIMIT_MIN:
        return [f"WARNING: planner estimate ~{mins:.1f} min on the reference rig model, over the "
                f"{est.PACK_LIMIT_MIN:g}-min pack limit — use fewer or shorter questions, or split "
                "it into two packs (never lower thinking caps or budgets)"]
    return [f"time: planner estimate ~{mins:.1f} min (limit {est.PACK_LIMIT_MIN:g})"]


def _mutant_errors(case: dict) -> list[str]:
    """A python_exec pack question: >= 1 mutant, and each one fails the tests."""
    from .api import ChatResult
    from .graders import grade
    muts = case.get("mutants") or []
    if not muts:
        return ["python_exec question without 'mutants' (wrong solutions the tests must catch)"]
    errs = []
    for i, code in enumerate(muts, 1):
        v = grade(ChatResult(response_text=f"```python\n{code}\n```"), case)
        if v["grade"] == "pass":
            errs.append(f"mutant {i} PASSES the tests (they do not bite)")
    return errs


def _judge_budget_errors(case: dict) -> list[str]:
    """A judged question with a full-length answer must fit the judge's
    input budget (16k context) without clamping."""
    from .judge import RUBRICS, judge_system, build_judge_input, judge_input_budget_chars
    rubric = case.get("rubric")
    if rubric not in RUBRICS:
        return []                       # verify_cases reports the unknown rubric
    answer = "x" * (int(case.get("max_tokens") or 2000) * 4)
    row = {**case, "rubric": rubric, "response": answer}
    if case.get("turns"):
        conv = []
        for t in case["turns"]:
            conv.append({"role": "user", "content": t if isinstance(t, str) else str(t)})
            conv.append({"role": "assistant", "content": answer})
        row["conversation"] = conv
    budget = judge_input_budget_chars()
    _, prompt = build_judge_input(row, budget_chars=10 ** 9)
    need = len(judge_system(row)) + len(prompt)
    if need > budget:
        return [f"judge input with a full-length answer is {need} chars > the judge budget "
                f"{budget} (lower max_tokens or split the turns)"]
    return []


def private_pack_terms() -> tuple[set[str], set[str]]:
    """(ids of the questions written inside, set paths) of every PRIVATE
    ladder set (drafts included) — for profiles.private_scope, so the public leak guard and
    the public board filter catch a private pack (review S4)."""
    ids: set[str] = set()
    paths: set[str] = set()
    root = private_root()
    for _key, _n, f in _set_files([root]):
        try:
            spec = _read_set(f)
        except ConfigError:
            continue
        paths.add(str(f.relative_to(root.parent.parent)) if root.parent.parent in f.parents
                  else str(f))
        for c in spec.get("cases") or []:
            # a pack's OWN questions (an id reference names a case that is
            # private or public by its own file)
            if isinstance(c, dict) and c.get("id"):
                ids.add(str(c["id"]))
    return ids, paths


# ----------------------------------------------------------- pack overview

def _first_sentence(text: str | None, limit: int = 160) -> str:
    t = " ".join(str(text or "").split())
    m = re.search(r"(?<=[a-z0-9\)])[.;] ", t)
    if m and m.start() < limit:
        t = t[:m.start()]
    return t if len(t) <= limit else t[:limit - 1].rstrip() + "…"


def pack_overview(prof: dict | None, maxed_by: dict[str, list[str]] | None = None,
                  dirs: list[Path] | None = None, minutes: bool = True) -> dict:
    """`crucibleforge pack list`: every sub-score with the optional packs that
    ship in the repo (PUBLIC set files only; private packs are never read or
    named). ``maxed_by`` = {"<category>/<part>": [model labels]} from the
    board (report.calibration), or None when no board is readable. A part is
    ``needs_pack`` when some model maxed it: the next pack is due."""
    from . import tiers as _tiers
    dirs = dirs if dirs is not None else [LADDER_DIR]
    prof = {**(prof or {}), "_public": True}    # never name a private custom score
    # staged packs are listed (marked "staged"), never hidden from the owner
    sets = discover(prof, dirs=dirs, staged=True)
    drafts: dict[str, int] = {}
    for key, _n, _f, spec in _readable_sets(dirs, include_drafts=True):
        if spec.get("draft"):
            drafts[key] = drafts.get(key, 0) + 1
    parts = []
    for d in _tiers.scores(prof):
        for p in d.get("parts") or []:
            key = f"{d['key']}/{p['key']}"
            rows = []
            for s in sets.get(key, []):
                mins = None
                if minutes:
                    try:
                        from . import estimate as est
                        mins = round(est.pack_minutes(
                            prof, set_cases(prof, key, s["n"], sets=sets),
                            s.get("budget"), s.get("context")), 1)
                    except Exception:  # noqa: BLE001 — an estimate never fails a listing
                        mins = None
                rows.append({"n": s["n"], "level": s["level"], "cases": len(s["cases"]),
                             "points": 1, "est_minutes": mins, "budget": s.get("budget"),
                             "status": "staged" if s.get("staged") else "live",
                             "case_ids": list(s["cases"]), "note": _first_sentence(s.get("note"))})
            mb = None if maxed_by is None else list(maxed_by.get(key, []))
            live_rows = [r for r in rows if r["status"] != "staged"]
            parts.append({"key": key, "label": f"{d['label']} / {p['label']}",
                          "standard_points": p["weight"], "sets": rows,
                          "bonus_points": len(live_rows), "drafts": drafts.get(key, 0),
                          "staged": len(rows) - len(live_rows),
                          "maxed_by": mb, "needs_pack": bool(mb)})
    return {"unlock": unlock_share(prof), "pack_minutes_limit": None if not minutes else _limit(),
            "board_read": maxed_by is not None, "parts": parts}


def _limit() -> float | None:
    try:
        from . import estimate as est
        return est.PACK_LIMIT_MIN
    except Exception:  # noqa: BLE001
        return None


def render_pack_overview(ov: dict) -> str:
    L = [f"Optional packs (ladder sets): a part unlocks its next set at >= "
         f"{ov['unlock'] * 100:.0f}% on its standard cases and on every earlier set; "
         "each set adds up to +1 point.",
         "Guide: docs/PACKS.md. Private packs (if any) are documented privately and not listed.",
         ""]
    L.append(f"{'part':30} {'std':>4} {'sets':>4} {'cases':>5} {'bonus':>5} {'~min':>5}  status")
    for p in ov["parts"]:
        n_cases = sum(s["cases"] for s in p["sets"])
        mins = [s["est_minutes"] for s in p["sets"] if s["est_minutes"] is not None]
        if p["maxed_by"]:
            st = "MAXED by " + ", ".join(p["maxed_by"]) + f": next pack is set {max([s['n'] for s in p['sets'] if s['n'] < CLASSIC_N0], default=0) + 1}"
        elif p["maxed_by"] is None:
            st = "board not read (no maxed data)"
        elif not p["sets"]:
            st = "no pack yet"
        else:
            st = "room left"
        if p["drafts"]:
            st += f" ({p['drafts']} draft{'s' if p['drafts'] != 1 else ''} ignored)"
        if p.get("staged"):
            st += f" ({p['staged']} staged: not run or scored without --include-staged)"
        L.append(f"{p['label'][:30]:30} {p['standard_points']:>4g} {len(p['sets']):>4} {n_cases:>5} "
                 f"{'+' + str(p['bonus_points']):>5} {('%.0f' % sum(mins)) if mins else '-':>5}  {st}")
        for s in p["sets"]:
            L.append(f"    set {s['n']} (level {s['level']}, {s['cases']} cases"
                     + (", STAGED" if s.get("status") == "staged" else "")
                     + (f", ~{s['est_minutes']:g} min" if s["est_minutes"] is not None else "")
                     + "): " + ", ".join(s["case_ids"]))
    L.append("")
    L.append("New pack: `crucibleforge pack new <category/part>`, then `crucibleforge pack verify <file>`.")
    return "\n".join(L)
