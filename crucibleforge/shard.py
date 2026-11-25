"""Sharded judging: split one judge phase across N processes safely.

STORAGE UNIT. Every transcript row of a model lives in ONE append-only file,
``results/transcripts_<label>.jsonl``, and a verdict is written by appending
the judged row to that file (config.append_transcript; the loader keeps the
last row per key). Two processes appending to the same file could interleave
partial lines, and two verdicts for one row would leave "whichever came last"
on the board. So the shard unit is the LABEL (the file), never the row:

* a shard owns whole labels — no two shards ever write the same file;
* :class:`LabelLocks` takes ``results/.judge-label-<label>.lock`` (flock,
  non-blocking) for every label a judge phase writes, so even a wrong plan or
  an operator typo cannot make two processes judge one label: the second one
  skips it (rows stay pending) and says so.

Nothing else is written during a judge phase (the board/meta are rebuilt by
``report``/``bench-all`` afterwards, never by ``judge``).

PARTITION. ``--shard I/N`` alone assigns labels by a stable hash (the same
answer in every process, whatever they see). ``judge-shards`` writes a PLAN
file instead: labels balanced by pending judge work (longest-first greedy),
plus a ledger of every pending row key and each file's size at plan time,
which ``judge-shards --verify`` uses afterwards to prove nothing was lost or
judged twice.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
from pathlib import Path

log = logging.getLogger(__name__)

PLAN_VERSION = 1


def parse_shard(text: str) -> tuple[int, int]:
    """``"1/2"`` -> (1, 2) (1-based). Raises ValueError."""
    m = re.fullmatch(r"\s*(\d+)\s*/\s*(\d+)\s*", str(text or ""))
    if not m:
        raise ValueError(f"--shard wants I/N (e.g. 1/2), got {text!r}")
    i, n = int(m.group(1)), int(m.group(2))
    if n < 1 or not 1 <= i <= n:
        raise ValueError(f"--shard {text}: need 1 <= I <= N")
    return i, n


def hash_shard(label: str, n: int) -> int:
    """Stable 1-based shard of a label (sha256, process/seed independent)."""
    h = int.from_bytes(hashlib.sha256(label.encode("utf-8")).digest()[:8], "big")
    return h % n + 1


def balanced(weights: dict[str, float], n: int) -> list[list[str]]:
    """Labels split into ``n`` lists of near-equal total weight: longest
    first onto the lightest shard (ties: lower shard; labels sorted by name
    within equal weight, so the answer is deterministic)."""
    bins: list[list[str]] = [[] for _ in range(n)]
    load = [0.0] * n
    for label in sorted(weights, key=lambda l: (-float(weights[l]), l)):
        k = min(range(n), key=lambda j: (load[j], j))
        bins[k].append(label)
        load[k] += float(weights[label])
    return [sorted(b) for b in bins]


def row_key(r: dict) -> list:
    """The loader's dedupe key (config.load_transcripts)."""
    return [r.get("bench_run_id"), r.get("case_id"), r.get("repeat"), r.get("turn")]


def make_plan(labels: list[str], n: int, pending: dict[str, list[dict]], weigh,
              transcript_path) -> dict:
    """A shard plan: per shard its labels, its pending row count and weight;
    the ledger of pending keys per label; each file's byte size now."""
    weights = {l: sum(weigh(r) for r in pending.get(l) or []) for l in labels
               if pending.get(l)}
    shards = balanced(weights, n)
    files = {}
    for l in weights:
        p = Path(transcript_path(l))
        files[l] = p.stat().st_size if p.exists() else 0
    return {
        "version": PLAN_VERSION,
        "created": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "n": n,
        "shards": [{"index": i + 1, "labels": s,
                    "rows": sum(len(pending[l]) for l in s),
                    "weight": sum(weights[l] for l in s)} for i, s in enumerate(shards)],
        "ledger": {l: [row_key(r) for r in pending[l]] for l in weights},
        "file_bytes": files,
    }


def write_plan(path, plan: dict) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(plan, indent=1), encoding="utf-8")
    os.replace(tmp, p)


def load_plan(path) -> dict:
    plan = json.loads(Path(path).read_text(encoding="utf-8"))
    if plan.get("version") != PLAN_VERSION:
        raise ValueError(f"shard plan {path}: version {plan.get('version')} != {PLAN_VERSION}")
    return plan


def plan_labels(plan: dict, i: int) -> list[str]:
    for s in plan["shards"]:
        if int(s["index"]) == int(i):
            return list(s["labels"])
    raise ValueError(f"shard plan has no shard {i} (n={plan.get('n')})")


def shard_labels(labels: list[str], shard: tuple[int, int] | None,
                 plan: dict | None = None) -> list[str]:
    """The labels of ``shard`` among ``labels`` (plan first, else hash)."""
    if shard is None:
        return list(labels)
    i, n = shard
    if plan is not None:
        if int(plan["n"]) != n:
            raise ValueError(f"--shard {i}/{n} but the plan has {plan['n']} shards")
        mine = set(plan_labels(plan, i))
        return [l for l in labels if l in mine]
    return [l for l in labels if hash_shard(l, n) == i]


def verify(plan: dict, transcript_path) -> dict:
    """Audit a finished sharded phase against its plan. Reads only the
    lines appended AFTER the plan (byte offset per file) and reports, per
    label: ledger rows judged, ledger rows still unjudged, rows judged more
    than once (duplicates), appended rows outside the ledger, torn lines."""
    out = {"labels": {}, "judged": 0, "unjudged": 0, "duplicates": 0, "foreign": 0, "torn": 0}
    for label, keys in plan["ledger"].items():
        want = {json.dumps(k) for k in keys}
        p = Path(transcript_path(label))
        seen: dict[str, int] = {}
        foreign = torn = 0
        if p.exists():
            with open(p, "rb") as f:
                f.seek(int(plan["file_bytes"].get(label, 0)))
                data = f.read().decode("utf-8", errors="replace")
            for line in data.splitlines():
                if not line.strip():
                    continue
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    torn += 1
                    continue
                k = json.dumps(row_key(r))
                if k in want:
                    seen[k] = seen.get(k, 0) + 1
                else:
                    foreign += 1
        dup = sum(1 for v in seen.values() if v > 1)
        rec = {"ledger": len(want), "judged": len(seen), "unjudged": len(want) - len(seen),
               "duplicates": dup, "foreign": foreign, "torn": torn}
        out["labels"][label] = rec
        for k in ("judged", "unjudged", "duplicates", "foreign", "torn"):
            out[k] += rec[k]
    out["ok"] = out["duplicates"] == 0 and out["torn"] == 0 and out["foreign"] == 0
    return out


# ------------------------------------------------------------------ locks
def _lock_name(label: str) -> str:
    return ".judge-label-" + re.sub(r"[^A-Za-z0-9_.-]+", "_", label) + ".lock"


class LabelLocks:
    """Exclusive, non-blocking per-label locks for one judge phase.

    ``acquire(labels)`` returns the labels it got; the others are held by a
    live process (another shard) and must be left alone. Released on
    ``release()`` / process exit (flock dies with the fd). On a platform
    without flock every label is granted (one-at-a-time is then on the
    operator, as for the rig lock)."""

    def __init__(self, results_dir):
        self.dir = Path(results_dir)
        self.files: dict[str, object] = {}

    def acquire(self, labels: list[str]) -> tuple[list[str], list[str]]:
        from .filelock import lock
        self.dir.mkdir(parents=True, exist_ok=True)
        got, held = [], []
        for label in labels:
            if label in self.files:
                got.append(label)
                continue
            f = open(self.dir / _lock_name(label), "a+", encoding="utf-8")
            try:
                lock(f, blocking=False)
            except BlockingIOError:
                f.close()
                held.append(label)
                continue
            f.seek(0)
            f.truncate()
            f.write(f"pid {os.getpid()} judge\n")
            f.flush()
            self.files[label] = f
            got.append(label)
        return got, held

    def release(self) -> None:
        from .filelock import unlock
        for f in self.files.values():
            try:
                unlock(f)
            except OSError:
                pass
            f.close()
        self.files.clear()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.release()
        return False


class DeviceLocks:
    """Exclusive, BLOCKING-with-poll locks on ``results/.rig.gpu<N>.lock``
    for the cards one sharded rig phase uses (two shards on the same card
    would share its VRAM and slots). Taken under the SHARED rig lock."""

    def __init__(self, results_dir, devices):
        self.dir = Path(results_dir)
        self.devices = sorted({int(d) for d in devices})
        self.files: list = []

    def acquire(self, wait_s: float = 0.0, poll_s: float = 2.0, stop=None) -> None:
        from .filelock import lock
        self.dir.mkdir(parents=True, exist_ok=True)
        deadline = time.time() + max(0.0, wait_s)
        for d in self.devices:
            f = open(self.dir / f".rig.gpu{d}.lock", "a+", encoding="utf-8")
            while True:
                try:
                    lock(f, blocking=False)
                    break
                except BlockingIOError:
                    if time.time() >= deadline or (stop is not None and stop()):
                        f.seek(0)
                        holder = f.read().strip()[:160]
                        f.close()
                        self.release()
                        raise DeviceBusy(d, holder)
                    time.sleep(poll_s)
            f.seek(0)
            f.truncate()
            f.write(f"pid {os.getpid()} judge shard\n")
            f.flush()
            self.files.append(f)

    def release(self) -> None:
        for f in self.files:
            try:
                from .filelock import unlock
                unlock(f)
            except OSError:
                pass
            f.close()
        self.files = []

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, *exc):
        self.release()
        return False


class DeviceBusy(RuntimeError):
    def __init__(self, device: int, holder: str = ""):
        super().__init__(f"GPU {device} is held by another sharded rig phase"
                         + (f" ({holder})" if holder else ""))
        self.device, self.holder = device, holder
