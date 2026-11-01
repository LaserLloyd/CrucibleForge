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
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import logging

from . import config as _config
from .config import ConfigError, ROOT_DIR

log = logging.getLogger(__name__)

LADDER_DIR = ROOT_DIR / "cases" / "ladder"
DEFAULT_UNLOCK = 0.9
_SET = re.compile(r"^set-(\d+)\.json$")
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


def _read_set(f: Path) -> dict:
    try:
        spec = json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise ConfigError(f"ladder set {f}: {e}") from e
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
                        yield f"{cat_dir.name}/{part_dir.name}", int(m.group(1)), f


def discover(prof: dict | None, dirs: list[Path] | None = None) -> dict[str, list[dict]]:
    """{"<category>/<part>": [set, ...] sorted by (level, n)} for every
    declared sub-score; a set = {n, level, cases (ids), budget, context,
    note, path}. ``n`` (the file number) identifies a set; ``level`` orders
    the unlocks. A set marked ``"draft": true`` (what ``pack new`` writes)
    is ignored until its author clears the flag."""
    keys = set(part_keys(prof))
    found: dict[str, dict[int, dict]] = {}
    # a profile as a public board sees it (report._audience_profile) never
    # gets the private sets
    for key, n, f, spec in _readable_sets(dirs, public=bool((prof or {}).get("_public")),
                                          include_drafts=False):
        if key not in keys:
            continue
        ids = [c if isinstance(c, str) else c["id"] for c in spec["cases"]]
        found.setdefault(key, {})[n] = {
            "n": n, "level": int(spec.get("level", n)), "cases": ids,
            "budget": spec.get("budget"), "context": spec.get("context"),
            "note": spec.get("note"), "path": str(f)}
    return {key: sorted(by_n.values(), key=lambda x: (x["level"], x["n"]))
            for key, by_n in found.items() if by_n}


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
           seq: list[dict], unlock: float) -> dict[int, str]:
    """{set n: "scored" | "pending" (unlocked, not run/judged) | "locked"}.
    Levels open in order: level L is open when the standard share and every
    set of every lower level are >= ``unlock``."""
    out: dict[int, str] = {}
    ok = std_share is not None and std_share >= unlock
    for level in sorted({s["level"] for s in seq}):
        group = [s for s in seq if s["level"] == level]
        if not ok:
            out.update({s["n"]: "locked" for s in group})
            continue
        for s in group:
            out[s["n"]] = "scored" if set_shares.get(s["n"]) is not None else "pending"
        ok = all(set_shares.get(s["n"]) is not None and set_shares[s["n"]] >= unlock
                 for s in group)
    return out


def due(std_share: float | None, set_shares: dict[int, float | None],
        seq: list[dict], unlock: float) -> list[int]:
    """The set numbers to run next for one sub-score (every unlocked set
    without a result; all sets of one level together)."""
    st = states(std_share, set_shares, seq, unlock)
    return [s["n"] for s in seq if st.get(s["n"]) == "pending"]


def points(std_share: float | None, std_max: float, set_shares: dict[int, float | None],
           seq: list[dict], unlock: float) -> float | None:
    """standard share x max + the share of every scored set (each set is
    worth 1 point in total)."""
    if std_share is None:
        return None
    st = states(std_share, set_shares, seq, unlock)
    return std_share * std_max + sum(set_shares.get(n) or 0.0
                                     for n, v in st.items() if v == "scored")


def set_cases(prof: dict, key: str, n: int, smoke: bool = False,
              sets: dict | None = None) -> list[dict]:
    """The case dicts of set n of a sub-score, stamped ``ladder`` =
    "<key>#<n>", ``ladder_budget`` (the set's output budget, if any) and
    ``min_context`` (the set's context, if any). Repeats follow the
    profile's per-category repeats (config.repeats_for)."""
    from .profiles import _select
    sets = discover(prof) if sets is None else sets
    spec = next((s for s in sets.get(key) or [] if s["n"] == n), None)
    if spec is None:
        return []
    all_cases = {c["id"]: c for c in _config.load_cases()}
    missing = [i for i in spec["cases"] if i not in all_cases]
    if missing:
        raise ConfigError(f"ladder set {spec['path']}: unknown case id(s) {missing}")
    mapping: dict[str, list[str]] = {}
    for cid in spec["cases"]:
        mapping.setdefault(all_cases[cid]["category"], []).append(cid)
    out = []
    for c in _select(prof, mapping, smoke):
        c["ladder"] = f"{key}#{n}"
        if spec.get("budget"):
            c["ladder_budget"] = int(spec["budget"])
        if spec.get("context"):
            c["min_context"] = max(int(c.get("min_context") or 0), int(spec["context"]))
        out.append(c)
    return out


def all_ladder_case_ids(prof: dict | None) -> set[str]:
    return set(case_levels(prof))


def verify(prof: dict | None) -> list[str]:
    """Problems of the ladder files (for `cases verify`): unknown sub-score
    dirs, empty sets, unknown case ids, a case in two sets, a standard case
    reused in a set."""
    problems = []
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
    try:
        sets = discover(prof)
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
    d = root / key
    existing = [(n, _read_set(f)) for k, n, f in _set_files([root]) if k == key]
    n = max([x[0] for x in existing], default=0) + 1
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


def verify_pack(path: Path, prof: dict | None = None) -> tuple[list[str], list[str]]:
    """(problems, notes) of one pack file: at least one question; worth 1
    point (no other ``points``); a positive integer ``level``; every case id
    known or written inline; ids unique across the whole case set; each
    inline question passes the case checks (verify_cases: required fields,
    reference passes its tests, answers re-derived, repo-task mutants bite);
    a python_exec question has >= 1 ``mutants`` entry and every mutant FAILS
    its tests; a judged question's judge input fits the judge budget with a
    full-length answer; the draft flag is reported."""
    from . import verify_cases
    path = Path(path)
    problems: list[str] = []
    notes: list[str] = []
    m = _SET.match(path.name)
    if not m:
        problems.append(f"{path.name}: a pack file is named set-<n>.json")
    try:
        spec = _read_set(path)
    except ConfigError as e:
        return [str(e)], notes
    if spec.get("draft"):
        notes.append("draft: true — runs ignore this pack until the flag is cleared")
    if "points" in spec and float(spec["points"]) != 1.0:
        problems.append(f"points {spec['points']}: a pack is worth exactly 1 point")
    lvl = spec.get("level", int(m.group(1)) if m else 1)
    if not isinstance(lvl, int) or lvl < 1:
        problems.append(f"level {lvl!r}: a positive integer")
    entries = spec["cases"]
    if not entries:
        problems.append("no question (a pack needs >= 1)")
    problems += _size_problems(spec)
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
    for c in entries:
        if not isinstance(c, dict):
            continue
        c = dict(c)
        errs = verify_cases._check(c)
        if c.get("grader") == "python_exec":
            errs += _mutant_errors(c)
        if c.get("rubric") and not c.get("turns"):
            # multi-turn sessions are judged per turn group (rp_deep) or with
            # the judge's own clamp (erp_session), like the standard sessions
            errs += _judge_budget_errors(c)
        problems += [f"{c['id']}: {e}" for e in errs]
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
    cases = [c for c in cases if c]
    try:
        mins = est.pack_minutes(prof, cases, spec.get("budget"), spec.get("context"))
    except Exception as e:  # noqa: BLE001 — an estimate never fails a verify
        return [f"no time estimate ({e})"]
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
    from .judge import JUDGE_SYSTEM, RUBRICS, build_judge_input, judge_input_budget_chars
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
    need = len(JUDGE_SYSTEM) + len(prompt)
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
