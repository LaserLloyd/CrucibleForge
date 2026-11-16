"""Hard sets (4.5): the 2 points of each score that sit above the standard 8.

A hard set is authored as a file ``cases/hard/<score>/<rev>.json`` (score is
``code``, ``chat`` or ``nsfw``)::

    {"schema": 1, "score": "code", "rev": "4.5", "cases": [<case>, ...]}

A case is a normal case object (same keys as cases/<category>.json) with an
explicit ``category`` (its kind: coding, tooluse, rpdeep, nsfw, ...) and a
``meta`` block (authorship, budget.anchor_tokens, pack, certified). ``meta``
and ``category`` are NOT part of the case hash, so promoting a case from hard
to standard (drop both keys, move it into cases/<category>.json) keeps its
``case_hash`` and the rows already run stay current.

Uncertified candidates live in ``cases/hard/<score>/shelf/`` and are never
loaded here (the loaders read only direct children of ``cases/hard/<score>/``).

This module only reads and checks; scoring of the hard part is report.py's.
Imports nothing from the package so version.py can use it.
"""
from __future__ import annotations

import json
from pathlib import Path

HARD_SCORES = ("code", "chat", "nsfw")
#: the case kinds (``category``) each hard score may hold
SCORE_CATEGORIES = {
    "code": ("coding", "codebank", "codeagent", "tooluse"),
    "chat": ("rpdeep", "rp", "story", "steer"),
    "nsfw": ("nsfw",),
}
#: keys left out of the case hash (location/authoring data, not test content)
NON_CONTENT_KEYS = ("meta", "category")


def hard_dir(cases_dir: Path) -> Path:
    return Path(cases_dir) / "hard"


def hard_files(cases_dir: Path) -> list[tuple[str, Path]]:
    """[(score, file)] of every revision file (direct children of
    cases/hard/<score>/; the shelf subdirectory is skipped)."""
    out = []
    for score in HARD_SCORES:
        d = hard_dir(cases_dir) / score
        if d.is_dir():
            out += [(score, p) for p in sorted(d.glob("*.json"))]
    return out


def read_file(path: Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def raw_cases(cases_dir: Path) -> list[tuple[str, Path, dict]]:
    """[(score, file, raw case)] — raw = as written, nothing added."""
    out = []
    for score, p in hard_files(cases_dir):
        try:
            spec = read_file(p)
        except (OSError, ValueError):
            continue
        for c in (spec.get("cases") if isinstance(spec, dict) else None) or []:
            if isinstance(c, dict) and c.get("id"):
                out.append((score, p, c))
    return out


def load(cases_dir: Path, score: str | None = None) -> list[dict]:
    """The hard case objects (copies), each given ``hard_score`` and
    ``hard_rev``; ``category`` is the one written in the file."""
    out = []
    for sc, p, c in raw_cases(cases_dir):
        if score and sc != score:
            continue
        c = dict(c)
        c["hard_score"] = sc
        c["hard_rev"] = p.stem
        out.append(c)
    return out


def is_private_path(path) -> bool:
    parts = Path(path).parts
    return "private" in parts or any(".local." in s for s in parts)


def problems(cases_dir: Path, standard_ids: dict[str, str] | None = None,
             declared: dict[str, str] | None = None) -> list[str]:
    """Static problems of the hard files (for `cases verify`).

    standard_ids: {id: where} of the standard cases (disjointness).
    declared: {score: file path as the profile names it}; each must exist
    and not sit under private / *.local.*."""
    errs: list[str] = []
    seen: dict[str, str] = dict(standard_ids or {})
    for score, path in (declared or {}).items():
        if is_private_path(path):
            errs.append(f"hard[{score}]: scored path {path} is under private or matches "
                        "*.local.* (the public tree only)")
        if not Path(path).is_file():
            errs.append(f"hard[{score}]: declared file {path} is missing")
    for score, p in hard_files(cases_dir):
        where = f"hard/{score}/{p.name}"
        if is_private_path(p):
            errs.append(f"{where}: scored hard file under a private / *.local.* path")
        try:
            spec = read_file(p)
        except (OSError, ValueError) as e:
            errs.append(f"{where}: unreadable ({e})")
            continue
        if not isinstance(spec, dict) or not isinstance(spec.get("cases"), list):
            errs.append(f"{where}: must be an object with a 'cases' list")
            continue
        if spec.get("schema") != 1:
            errs.append(f"{where}: schema must be 1")
        if spec.get("score") != score:
            errs.append(f"{where}: 'score' is {spec.get('score')!r}, the directory says {score!r}")
        if str(spec.get("rev", "")) != p.stem:
            errs.append(f"{where}: 'rev' {spec.get('rev')!r} must equal the file name {p.stem!r}")
        for c in spec["cases"]:
            if not isinstance(c, dict) or not c.get("id"):
                errs.append(f"{where}: a case without an id")
                continue
            cid = c["id"]
            if cid in seen:
                errs.append(f"{where}: id {cid} is not unique (also in {seen[cid]})")
            seen[cid] = where
            errs += [f"{where}: {cid}: {e}" for e in case_meta_errors(c, score)]
    return errs


def case_meta_errors(c: dict, score: str) -> list[str]:
    errs = []
    cat = c.get("category")
    if cat not in SCORE_CATEGORIES[score]:
        errs.append(f"category {cat!r} not one of {list(SCORE_CATEGORIES[score])} for score {score}")
    if c.get("element_standard") != 45:
        errs.append("element_standard must be 45 (a scored hard case meets the 4.5 element standard)")
    m = c.get("meta")
    if not isinstance(m, dict):
        return errs + ["missing meta"]
    if not (isinstance(m.get("authorship"), str) and m["authorship"]):
        errs.append("meta.authorship must be a non-empty string (who wrote it: grok, deepseek, ...)")
    elif score == "nsfw" and "claude" in m["authorship"].lower():
        errs.append("meta.authorship 'claude' is not allowed on an NSFW case "
                    "(Claude never writes explicit prose: grok, deepseek, minimax or a local tune)")
    b = m.get("budget")
    tok = b.get("anchor_tokens") if isinstance(b, dict) else None
    if not (isinstance(tok, (int, float)) and not isinstance(tok, bool) and tok > 0):
        errs.append("meta.budget.anchor_tokens must be a positive number")
    if not (isinstance(m.get("pack"), str) and m["pack"]):
        errs.append("meta.pack must be a non-empty string")
    if not isinstance(m.get("certified"), bool):
        errs.append("meta.certified must be true or false")
    elif not m["certified"]:
        errs.append("meta.certified is false: an uncertified case belongs in shelf/, not a scored file")
    return errs
