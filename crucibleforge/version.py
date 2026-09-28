"""Benchmark suite revision stamp.

Every result row is tagged with:

- ``bench_revision`` = ``SUITE_VERSION+<cases-hash>`` — the TEST SET. Rows from
  different revisions answer different questions and the report says so.
- ``judge_fingerprint`` = short hash of the measurement-relevant settings of
  the PRIMARY judge (provider, model, thinking, temperature, samples,
  max_tokens, request extras) — the MEASURING INSTRUMENT for the judged
  components (RP, NSFW, planning, steer). The judge that actually scored a
  row is also stored on the row (``judge_model``), which is what the report
  compares: a model judged by a fallback judge is flagged, not silently
  ranked beside 122B-judged peers.

Why the two are separate (2026-08-22): the old stamp hashed the whole
``judge:`` block — candidates, their context windows, retry schedules — so a
deployment knob (a fallback's ``context_length``) relabelled every existing
result "stale" while the judge that scored a model never appeared in the
stamp at all. ``legacy_revision`` keeps the old formula so rows stamped
before this split are recognised as current while the case files and judge
block are unchanged.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

# Bump for notable harness/metric changes (semver). The content-hash tracks
# test-content changes automatically on top of this.
SUITE_VERSION = "3.5.0"

_ROOT = Path(__file__).resolve().parent.parent
_CASES_DIR = _ROOT / "cases"

# judge settings that change the measurement (anything else — context_length,
# load_retry_s, display names — is deployment, not measurement)
_JUDGE_MEASUREMENT_KEYS = ("provider", "model_id", "thinking", "temperature",
                           "max_tokens", "extra_body", "no_schema")


def _load_cfg(cfg: dict | None) -> dict:
    if cfg is not None:
        return cfg
    try:
        import yaml
        from .config import find_config_path
        return yaml.safe_load(find_config_path().read_text(encoding="utf-8")) or {}
    except Exception:
        return {}


# Grading-only keys of a case. They say how an answer is SCORED, not what
# is asked (the session ``checks``, the judge's ``judge_elements``, and the
# metric tags ``explicit_required`` / ``willing`` that route a row into the
# Explicit-peak and Willing formulas — config.CASE_TAGS), so editing them must not relabel existing results as another
# test set — the report re-applies the current checks to stored rows
# (report._recheck), exactly as a grader fix applies to old rows.
_GRADING_KEYS = ("checks", "judge_elements", "explicit_required", "willing")

# Stamps from before the grading keys were excluded from the hash, mapped
# from the prompt-only hash they are equivalent to (verified 2026-09-24 with
# `git show HEAD:cases/*`: 3.4.0+7c3f7296 is the full hash of exactly the case
# files whose prompt-only hash is 0d6e0ad1). A prompt change moves the key
# and the old stamp stops counting — as it should.
#
# 3.5.0 (2026-09-25) ADDED cases (steer SX1-3, tools TZ11-13, reason RX20-21,
# RP RPX3) and changed none of the 3.4.0 ones, so every 3.4.0 row still
# answers exactly its question: those rows stay on the board and the model
# shows as partial until a full re-run fills the new cases. Update the key
# whenever the case set moves again (the test pins it to cases_hash()).
#
# 3.5.0 NSFW half (2026-09-25): seven more ADDED nsfw cases (XP1-3 explicit
# peak, WL1/WL2 must-write, WL1R/WL2R must-refuse twins); no existing case
# changed, so the 3.4.0 rows AND the rows already stamped with the first
# 3.5.0 hash (91073254) still answer their questions.
#
# 3.5.0 review round (2026-09-25 evening): TZ11 step 10 gained an
# ``answer_forbid`` (the old DOB), TZ12 step 10 word-bounded needles, TZ13
# steps 3/5 ``forbid_args`` (the lookalike ids). Those live inside
# ``tool_script``, so the hash moved to d2d09bda although no prompt changed —
# grading-only in effect, so every earlier 3.5.0 stamp of the day still
# answers its question. 3c64cb8b is the working-tree state of the
# saturation run 000ac71a (between commits 59ed7b3=91073254 and
# 029d563=6c4bded7; its stored prompts match the current cases — its old
# RX20/RX21 ids no longer exist and drop out on their own).
_V340 = {"3.4.0+0d6e0ad1", "3.4.0+7c3f7296"}
_V350_TODAY = {"3.5.0+3c64cb8b", "3.5.0+91073254", "3.5.0+6c4bded7"}
_EQUIVALENT_STAMPS = {"0d6e0ad1": {"3.4.0+7c3f7296"},
                      "91073254": _V340,
                      "6c4bded7": _V340 | {"3.5.0+91073254", "3.5.0+3c64cb8b"},
                      "d2d09bda": _V340 | _V350_TODAY | {"3.5.0+96800151"}}


def _hash_case_file(h, p: Path) -> None:
    data = json.loads(p.read_bytes())
    cases = data if isinstance(data, list) else data.get("cases", [])
    for c in cases if isinstance(cases, list) else []:
        if isinstance(c, dict):
            for k in _GRADING_KEYS:
                c.pop(k, None)
    h.update(p.name.encode())
    h.update(b"\0")
    h.update(json.dumps(data, sort_keys=True, ensure_ascii=False).encode())


def cases_hash() -> str:
    """Stable short hash over the PUBLIC case files' TEST CONTENT (prompts,
    turns, budgets, rubrics, answer keys) — grading-only keys excluded.

    cases/private/ is deliberately NOT part of it (the glob is not
    recursive): the suite revision must be the same on a clean clone as on
    the operator's box, and adding or editing a local private case must not
    relabel every public row stale. Private rows carry their own per-category
    stamp instead (:func:`private_category_hash`)."""
    h = hashlib.sha256()
    for p in sorted(_CASES_DIR.glob("*.json")):
        _hash_case_file(h, p)
    return h.hexdigest()[:8]


def private_category_hash(category: str) -> str | None:
    """The ``private_revision`` stamp of a private category: the same content
    hash as :func:`cases_hash`, over cases/private/<category>.json alone.
    None when the category has no private case file here."""
    from . import config as _config
    p = _config.PRIVATE_CASES_DIR / f"{category}.json"
    if category not in _config.PRIVATE_CATEGORIES or not p.is_file():
        return None
    h = hashlib.sha256()
    _hash_case_file(h, p)
    return h.hexdigest()[:8]


# Rows written before private categories had their own stamp (the 2026-09-28
# private-category backfill): their ``bench_revision`` hashed the then-untracked
# private files in WITH the public ones. Public part: identical to d2d09bda
# (verified 2026-09-28: the tracked case set alone hashes to d2d09bda), so the
# stamp is equivalent there (_EQUIVALENT_STAMPS). Private part: the
# private_category_hash() values of the case content those rows answered —
# hashes only, so no private category name sits in this tracked file.
_LEGACY_PRIVATE_STAMPS = {"3.5.0+96800151": {"75de47b3"}}


def private_row_current(row: dict) -> bool:
    """Whether a row of a PRIVATE category still answers the current private
    case file of its category."""
    cur = private_category_hash(str(row.get("category")))
    if cur is None:
        return False
    stamp = row.get("private_revision")
    if stamp is None:
        return cur in _LEGACY_PRIVATE_STAMPS.get(row.get("bench_revision"), set())
    return stamp == cur


def _profile_judge_spec(cfg: dict) -> dict | None:
    """The judge the benchmark PROFILE names (cfg["_profile"], default
    bench) — the judge whose verdicts count on the board — as a spec dict;
    a profile judge given by name resolves against judge.candidates. None
    when the profile names none (or cannot be read)."""
    try:
        from .profiles import DEFAULT_PROFILE, load_profile, profile_judge
        j = profile_judge(load_profile(cfg.get("_profile") or DEFAULT_PROFILE, cfg))
    except Exception:  # noqa: BLE001 — an unreadable profile falls back to candidates
        return None
    if isinstance(j, str):
        cands = (cfg.get("judge") or {}).get("candidates") or []
        return next((c for c in cands if j in (c.get("name"), c.get("model_id"))),
                    {"model_id": j})
    return j


def judge_fingerprint(cfg: dict | None = None) -> str | None:
    """Short hash of the measurement settings of the PROFILE's judge (the one
    the board counts — not ``models.yaml`` candidates[0], which is only the
    fallback when the profile names no judge), or None when there is none."""
    cfg = _load_cfg(cfg)
    judge = cfg.get("judge") or {}
    cands = judge.get("candidates") or []
    primary = _profile_judge_spec(cfg) or (cands[0] if cands else None)
    if not primary:
        return None
    parts = {k: primary.get(k) for k in _JUDGE_MEASUREMENT_KEYS if primary.get(k) is not None}
    parts["samples"] = judge.get("samples", 1)
    parts.setdefault("temperature", judge.get("temperature"))
    parts.setdefault("max_tokens", judge.get("max_tokens"))
    return hashlib.sha256(repr(sorted(parts.items())).encode()).hexdigest()[:8]


def primary_judge_id(cfg: dict | None = None) -> str | None:
    cands = (_load_cfg(cfg).get("judge") or {}).get("candidates") or []
    return cands[0].get("model_id") if cands else None


def _legacy_judge_fingerprint(cfg: dict | None = None) -> bytes:
    try:
        return repr(_load_cfg(cfg).get("judge", {})).encode()
    except Exception:
        return b""


def legacy_revision(cfg: dict | None = None) -> str:
    """The pre-3.2 stamp (cases + whole judge block). Rows carrying it are
    current as long as neither has changed since they were written."""
    h = hashlib.sha256()
    for p in sorted(_CASES_DIR.glob("*.json")):
        h.update(p.name.encode())
        h.update(b"\0")
        h.update(p.read_bytes())
    h.update(b"\0judge\0")
    h.update(_legacy_judge_fingerprint(cfg))
    return f"{SUITE_VERSION}+{h.hexdigest()[:8]}"


def content_hash(cfg: dict | None = None) -> str:
    """Back-compat: the cases hash (the judge is fingerprinted separately)."""
    return cases_hash()


def revision(cfg: dict | None = None) -> str:
    return f"{SUITE_VERSION}+{cases_hash()}"


def current_revisions(cfg: dict | None = None) -> set[str]:
    """Every stamp that means 'this test set, as configured now'."""
    return ({revision(cfg), legacy_revision(cfg)}
            | _EQUIVALENT_STAMPS.get(cases_hash(), set()))
