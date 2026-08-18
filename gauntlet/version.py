"""Benchmark suite revision stamp.

Every result is tagged with a revision = `SUITE_VERSION+<content-hash>`:
- SUITE_VERSION is bumped by hand for notable changes to the harness/metrics.
- the content-hash is derived automatically from BOTH the test set (every case
  file) AND the judge configuration (which model judges, temperature, samples).

Why the judge is in the hash: the judge IS part of the measurement — RP/NSFW/
planning/steer scores from one judge are not comparable to scores from a
different judge. So swapping the judge (e.g. to `qwen3.5-122b-a10b-heretic`)
changes the revision automatically, and the report warns when transcripts of
different revisions are mixed.

Because it's a deterministic hash of (cases + judge), **changing the judge back
restores the exact prior revision** — old results scored under that judge become
directly comparable again. Nothing to track by hand; the revision does it.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

# Bump for notable harness/metric changes (semver). The content-hash tracks
# test-content and judge-config changes automatically on top of this.
SUITE_VERSION = "3.0.0"

_ROOT = Path(__file__).resolve().parent.parent
_CASES_DIR = _ROOT / "cases"


def _judge_fingerprint(cfg: dict | None = None) -> bytes:
    """Bytes that change when the judge config changes. Uses the ``judge:``
    block (candidates + temperature + samples + max_tokens) so any judge
    change — including reordering candidates — bumps the revision."""
    try:
        if cfg is None:
            import yaml
            from .config import find_config_path
            cfg = yaml.safe_load(find_config_path().read_text()) or {}
        judge = cfg.get("judge", {})
        # candidates carry provider names now; strip keys that don't affect
        # the measurement (a display-only 'name', for example)
        return repr(judge).encode()
    except Exception:
        return b""


def content_hash(cfg: dict | None = None) -> str:
    """Stable short hash over all case-file contents + the judge config."""
    h = hashlib.sha256()
    for p in sorted(_CASES_DIR.glob("*.json")):
        h.update(p.name.encode())
        h.update(b"\0")
        h.update(p.read_bytes())
    h.update(b"\0judge\0")
    h.update(_judge_fingerprint(cfg))
    return h.hexdigest()[:8]


# Back-compat alias (older callers/tests referenced cases_hash).
cases_hash = content_hash


def revision(cfg: dict | None = None) -> str:
    return f"{SUITE_VERSION}+{content_hash(cfg)}"
