#!/usr/bin/env python3
"""Margin-isolation kill test (stamp-2 item B). Offline: never calls a model or the rig.

  killtest_margins45.py --folders <results copy> [<results copy> ...] --out <dir>

For each folder (COPIES; the originals are never touched) the SAME tree renders the board twice: once with every
``cases/ladder`` pack set present, once in a throwaway copy of the tree with the pack sets removed (see PACK_ITEMS). Every printed value that is
not a pack column must be byte-identical: standard cells, margins, intervals, ``precision45`` values, Overall.
Differences are listed; any difference outside pack-named paths exits 1. Slow (a render is minutes per folder).
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PACK_WORDS = ("pack", "pk_", "ladder", "gap", "tough", "@units")


def flatten(o, path=""):
    if isinstance(o, dict):
        for k, v in o.items():
            yield from flatten(v, f"{path}/{k}")
    elif isinstance(o, list):
        for i, v in enumerate(o):
            yield from flatten(v, f"{path}[{i}]")
    else:
        yield path, o


def is_pack_path(p: str) -> bool:
    return any(w in p.lower() for w in PACK_WORDS)


def diff(a: dict, b: dict) -> list[tuple[str, object, object]]:
    fa, fb = dict(flatten(a)), dict(flatten(b))
    return [(k, fa.get(k), fb.get(k)) for k in sorted(set(fa) | set(fb)) if fa.get(k) != fb.get(k)]


#: a ladder file is a PACK set when its ``item`` starts with one of the GAP/tough pack names (the PACK-* items are standard banks and stay), or it sits in a
#: pk_* part. Standard sets (including the classic banks) stay. Override with --pack-items (comma list of prefixes).
# KNOWGAP is left out on purpose (lead ruling 2026-11-21, KILLTEST-STAMP2-45.md): its set files are also read by the
# standard part chat/h6_know, so deleting them changes that part's own maxed/unrun state and its "[+1 pack unrun]"
# label (an effect of the removal, not of the draws). The whole-batch run with KNOWGAP removed showed every standard
# cell, point, margin and interval identical; pass --pack-items to include it again.
PACK_ITEMS = ("TOOLGAP", "EVERYGAP", "PATCHGAP", "PATCHTOUGH")


def pack_files(root: Path, items=PACK_ITEMS) -> list[Path]:
    out = []
    for f in sorted((root / "cases" / "ladder").rglob("*.json")):
        try:
            item = str(json.loads(f.read_text(encoding="utf-8")).get("item") or "")
        except (ValueError, OSError):
            continue
        if f.parent.name.startswith("pk_") or item.startswith(tuple(items)):
            out.append(f)
    return out


def make_stripped_tree(dest: Path, items=PACK_ITEMS) -> list[str]:
    """A throwaway copy of the tree (no venv, no git history, no results) with every ladder PACK set removed.
    Returns the removed paths."""
    if dest.exists():
        shutil.rmtree(dest)
    left_out: list[str] = []
    root_skip = {".venv", ".git", "results", "work"}           # root level ONLY (a ladder part named "work" stays)

    def ignore(d, names):
        out = {n for n in names if n in ("__pycache__", ".pytest_cache") or n.endswith(".pyc")}
        if Path(d).resolve() == Path(ROOT).resolve():
            hit = {n for n in names if n in root_skip}
            out |= hit
            left_out.extend(sorted(hit))
        return out
    shutil.copytree(ROOT, dest, ignore=ignore)
    print(f"left out of the tree copy (root level only; bytecode caches everywhere): {', '.join(left_out) or 'nothing'}")
    gone = pack_files(dest, items)
    for f in gone:
        f.unlink()
    return [str(f.relative_to(dest)) for f in gone]            # main() prints this list


def render(tree: Path, folder: Path) -> int:
    env = dict(os.environ, CRUCIBLEFORGE_LIVE_RESULTS="", PYTHONPATH=str(tree))
    cmd = [sys.executable, "-m", "crucibleforge.cli", "--config", str(tree / "models.example.yaml"),
           "--results", str(folder), "report"]
    r = subprocess.run(cmd, cwd=tree, env=env, capture_output=True, text=True)
    if r.returncode:
        print(r.stderr[-1500:], file=sys.stderr)
    return r.returncode


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--folders", nargs="+", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--pack-items", default=",".join(PACK_ITEMS), help="item-name prefixes that mark a pack set")
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    stripped = out / "tree-no-packs"
    gone = make_stripped_tree(stripped, tuple(a.pack_items.split(",")))
    print(f"removed {len(gone)} pack set file(s): " + ", ".join(gone))
    bad = 0
    for f in map(Path, a.folders):
        name = f.name
        full, none = out / f"{name}.all-packs", out / f"{name}.no-packs"
        for d in (full, none):
            if d.exists():
                shutil.rmtree(d)
            shutil.copytree(f, d)
        rc = render(ROOT, full) or render(stripped, none)
        if rc:
            print(f"{name}: render failed (exit {rc})")
            return 2
        ja = json.loads((full / "report.json").read_text(encoding="utf-8"))
        jb = json.loads((none / "report.json").read_text(encoding="utf-8"))
        files = {"report.json": diff(ja, jb)}
        for extra in ("precision45.json",):
            pa, pb = full / extra, none / extra
            if pa.is_file() or pb.is_file():
                files[extra] = diff(json.loads(pa.read_text(encoding="utf-8")) if pa.is_file() else {},
                                    json.loads(pb.read_text(encoding="utf-8")) if pb.is_file() else {})
        for fn, ds in files.items():
            unexpected = [d for d in ds if not is_pack_path(d[0])]
            print(f"{name} {fn}: {len(ds)} differing value(s), {len(ds) - len(unexpected)} pack-named, "
                  f"{len(unexpected)} UNEXPECTED")
            for p, x, y in unexpected[:200]:
                print(f"  {p}: {x!r} != {y!r}")
            bad += len(unexpected)
    print("FAIL: a non-pack value moved" if bad else "OK: every non-pack value identical")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
