#!/usr/bin/env python3
"""Offline re-score: stored rows -> report segments under the current score tree. Never generates, never calls a
model, never writes into ``--results`` (it works on a copy under ``--out``).

  rescore45.py --results <dir> --out <dir> [--no-readers]

Prints, per family, the stale-row count (a row graded by code that changed since) and the Code / Chat segment
table with each pending segment's reason. ``--no-readers`` prints NSFW+ as "pending readers".
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def stale_summary(lines: list[str]) -> tuple[str, list[str], bool]:
    """(message, stale lines, ok) for the ``report --check-stale`` output. Zero models checked is a failure, never
    "every stored row current": nothing was verified."""
    stale = [ln for ln in lines if not ln.rstrip().endswith(" 0 stale row(s)")]
    if not lines:
        return "stale check: 0 model(s) checked: NOTHING VERIFIED (no model in this results folder or no models file)", [], False
    return (f"stale check: {len(lines)} model(s); " + ("every stored row current (0 stale)" if not stale else "STALE:")), \
        stale, not stale


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--results", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--readers-only", action="store_true", help="only fill the reader part (no-op until lane W4)")
    ap.add_argument("--no-readers", action="store_true", help="print NSFW+ as pending readers")
    a = ap.parse_args()
    src, out = Path(a.results), Path(a.out)
    if out.exists() and any(out.iterdir()):
        print(f"refusing: {out} is not empty", file=sys.stderr)
        return 2
    shutil.copytree(src, out, dirs_exist_ok=True)
    base = [str(Path(sys.executable).parent / "crucibleforge"), "--results", str(out)]
    st = subprocess.run(base + ["report", "--check-stale"], capture_output=True, text=True, cwd=ROOT)
    lines = [ln for ln in st.stdout.splitlines() if ln.strip()]
    msg, stale, ok_stale = stale_summary(lines)
    print(msg)
    for ln in stale:
        print("  " + ln)
    if not lines:
        return 3
    rp = subprocess.run(base + ["report"], capture_output=True, text=True, cwd=ROOT)
    if rp.returncode:
        print(rp.stderr[-2000:], file=sys.stderr)
        return rp.returncode
    from crucibleforge import segments45 as S
    d = json.loads((out / "report.json").read_text(encoding="utf-8"))
    for label, m in d["models"].items():
        tb = m.get("segments45")
        print(f"\n{label}")
        if not tb:
            print("  no score tree in this profile")
            continue
        for sk in ("code", "chat"):
            s = tb[sk]
            print(f"  {s['label']}: {S.cell(s)}  (max {s['max']:g})")
            for g in s["segments"].values():
                why = "; ".join(sorted({p["reason"] for p in g["pending"]}))
                print(f"    {g['label']:<40} {'-' if g['points'] is None else format(g['points'], '.2f'):>6} / {g['max']:g}"
                      + (f"   {why}" if why else ""))
        o = tb["overall"]
        print(f"  Overall: {o['points'] if o['points'] is not None else 'not ranked'}  "
              f"(measured {o['measured_points']} of {o['measured_max']:g})")
        print("  NSFW+: " + ("pending readers" if a.no_readers else json.dumps(m.get("nsfw_plus", "not run"))[:200]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
