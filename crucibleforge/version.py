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
from pathlib import Path

# Bump for notable harness/metric changes (semver). The content-hash tracks
# test-content changes automatically on top of this.
SUITE_VERSION = "3.0.0"

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


def cases_hash() -> str:
    """Stable short hash over all case-file contents."""
    h = hashlib.sha256()
    for p in sorted(_CASES_DIR.glob("*.json")):
        h.update(p.name.encode())
        h.update(b"\0")
        h.update(p.read_bytes())
    return h.hexdigest()[:8]


def judge_fingerprint(cfg: dict | None = None) -> str | None:
    """Short hash of the primary judge's measurement settings, or None when
    no judge is configured."""
    judge = _load_cfg(cfg).get("judge") or {}
    cands = judge.get("candidates") or []
    if not cands:
        return None
    primary = cands[0]
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
    return {revision(cfg), legacy_revision(cfg)}
