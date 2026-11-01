"""Per-phase wall clock of a run (suite 3.9, `all --timing`).

Every phase of `crucibleforge all` is timed and recorded in each model's
meta under ``timing`` (a list of {phase, s, ...}): the runner writes one
entry per generation round (runner._run_one_model), the CLI one per
escalation / judge / ladder phase / report. ``all`` prints a one-line
summary at the end; ``--timing`` prints the table. The meta list starts
fresh with each new board run (the runner's reset write).
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone

from .config import results_dir

log = logging.getLogger(__name__)


def record(labels, phase: str, seconds: float, **extra) -> None:
    """Append {phase, s, at, ...} to ``meta.timing`` of every label."""
    at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    for label in labels or []:
        path = results_dir() / f"meta_{label}.json"
        try:
            meta = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue          # no meta (the model never started): nothing to time
        meta.setdefault("timing", []).append(
            {"phase": phase, "s": round(float(seconds), 1), "at": at, **extra})
        path.write_text(json.dumps(meta, indent=2), encoding="utf-8")


class Clock:
    """The phases of one `all` invocation, in order: (phase, seconds, labels)."""

    def __init__(self):
        self.t0 = time.monotonic()
        self.rows: list[tuple[str, float, list[str]]] = []

    def phase(self, name: str, labels=None, record_meta: bool = True):
        return _Phase(self, name, list(labels or []), record_meta)

    def add(self, name: str, seconds: float, labels=None, record_meta: bool = True) -> None:
        labels = list(labels or [])
        self.rows.append((name, float(seconds), labels))
        if record_meta and labels:
            try:
                record(labels, name, seconds)
            except Exception as e:  # noqa: BLE001 — timing never breaks a run
                log.warning("timing record for %s failed: %s", name, e)

    def total_s(self) -> float:
        return time.monotonic() - self.t0

    def summary_line(self) -> str:
        by: dict[str, float] = {}
        for name, s, _ in self.rows:
            key = name.split(" (")[0]
            by[key] = by.get(key, 0.0) + s
        parts = ", ".join(f"{k} {_min(v)}" for k, v in by.items())
        return f"timing: {parts}{', ' if parts else ''}total {_min(self.total_s())}"

    def table(self, labels=None) -> str:
        """The phases of this invocation, then each model's generation rounds
        as the runner recorded them (meta.timing)."""
        lines = ["", "phase                                      wall      models",
                 "-----------------------------------------  --------  ------"]
        for name, s, labs in self.rows:
            lines.append(f"{name[:41]:<41}  {_min(s):>8}  {','.join(labs)[:60]}")
        lines.append(f"{'total':<41}  {_min(self.total_s()):>8}")
        for label in labels or []:
            try:
                meta = json.loads((results_dir() / f"meta_{label}.json").read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            gen = [t for t in meta.get("timing") or [] if str(t.get("phase", "")).startswith("generation")]
            if gen:
                lines.append(f"  {label}: " + "; ".join(
                    f"{t['phase']} {_min(t['s'])}"
                    + (f" (ctx {t['ctx']}, {t.get('workers')} wide, {t.get('jobs')} jobs)"
                       if t.get("ctx") else "") for t in gen))
        return "\n".join(lines)


class _Phase:
    def __init__(self, clock: Clock, name: str, labels: list[str], record_meta: bool):
        self.clock, self.name, self.labels, self.record_meta = clock, name, labels, record_meta

    def __enter__(self):
        self.t = time.monotonic()
        return self

    def __exit__(self, *exc):
        self.clock.add(self.name, time.monotonic() - self.t, self.labels, self.record_meta)
        return False


def _min(seconds: float) -> str:
    return f"{seconds / 60:.1f} min" if seconds >= 60 else f"{seconds:.0f} s"
