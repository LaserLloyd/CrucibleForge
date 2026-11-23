#!/usr/bin/env python3
"""Split a run folder that holds several models into one folder per model (what release45 --anchors needs).

    scripts/split_run_folder45.py SRC DEST [--models label,label]

For every ``transcripts_<label>.jsonl`` in SRC that holds at least one row, DEST/<label>/ gets a byte-for-byte copy of
that file and of ``meta_<label>.json`` when it exists. No row is read as JSON, reordered, deduplicated or rewritten;
the copy is verified by SHA-256. A model with a meta file but no rows gets no folder. Refuses (exit 2) to write into
an existing DEST/<label>. Never touches SRC."""
from __future__ import annotations

import argparse
import hashlib
import shutil
import sys
from pathlib import Path


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def split(src: Path, dest: Path, models: list[str] | None = None) -> list[Path]:
    src, dest = Path(src), Path(dest)
    plan = []
    for t in sorted(src.glob("transcripts_*.jsonl")):
        label = t.name[len("transcripts_"):-len(".jsonl")]
        if models is not None and label not in models:
            continue
        if not any(ln.strip() for ln in t.read_bytes().splitlines()):
            continue
        if (dest / label).exists():
            raise SystemExit(f"refusing to write over {dest / label}")
        plan.append((label, t))
    made = []
    for label, t in plan:
        d = dest / label
        d.mkdir(parents=True)
        pairs = [(t, d / t.name)]
        meta = src / f"meta_{label}.json"
        if meta.is_file():
            pairs.append((meta, d / meta.name))
        for a, b in pairs:
            shutil.copyfile(a, b)
            if _sha(a) != _sha(b):
                raise SystemExit(f"copy of {a} does not match its source")
        made.append(d)
    return made


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("src", type=Path)
    ap.add_argument("dest", type=Path)
    ap.add_argument("--models", default=None, help="comma list of labels to copy (default: every model with rows)")
    a = ap.parse_args(argv)
    if not a.src.is_dir():
        print(f"not a folder: {a.src}", file=sys.stderr)
        return 2
    try:
        made = split(a.src, a.dest, [m for m in a.models.split(",") if m] if a.models else None)
    except SystemExit as e:
        print(e, file=sys.stderr)
        return 2
    for d in made:
        print(d)
    return 0 if made else 2


if __name__ == "__main__":
    sys.exit(main())
