"""Run-to-run spread of the 4.5 board on its own scale, from replicate folders (crucibleforge/resolution45.py).

usage: python scripts/resolution45.py <folder>... [--write-precision <board results dir>] [--json <out.json>]

Each folder = one whole run of one model with its rendered report.json; folders with the same label are its
replicates. Nothing is rescored: each folder's board points are read back from its report (seconds). Prints, per
model and key, n runs, mean, SD and min-max, then the pooled SD with its degrees of freedom. ``--write-precision``
writes <dir>/precision45.json ("scale": "board45"), which the board reads to show a replicated model at the mean
of its runs and to print every model's range from replicate runs.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from crucibleforge import resolution45 as RS       # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("folders", nargs="+")
    ap.add_argument("--write-precision", default=None)
    ap.add_argument("--json", default=None)
    # accepted and ignored (the old pairwise-separation inputs; release45 no longer passes them)
    ap.add_argument("--marks", default=None, help=argparse.SUPPRESS)
    ap.add_argument("--reference", default=None, help=argparse.SUPPRESS)
    ap.add_argument("--parts", action="store_true", help=argparse.SUPPRESS)
    a = ap.parse_args(argv)
    points: dict[str, list[dict]] = {}
    for f in a.folders:
        try:
            label, pt = RS.folder_points(f)
        except ValueError as e:
            print(str(e), file=sys.stderr)
            return 2
        points.setdefault(label, []).append(pt)
    body = RS.precision_file(points, a.folders)
    print(RS.render_precision(body["run_to_run"]))
    if a.write_precision:
        p = Path(a.write_precision) / "precision45.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(p.name + f".tmp{os.getpid()}")
        tmp.write_text(json.dumps(body, indent=1), encoding="utf-8")
        os.replace(tmp, p)
        print(f"precision: {p}")
    if a.json:
        Path(a.json).write_text(json.dumps(body, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
