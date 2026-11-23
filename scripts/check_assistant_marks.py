#!/usr/bin/env python3
"""Validation marks of the Assistant basics (checked) column. Exit 0 when every mark holds, 1 when one fails, 2 on a
usage problem. Reads stored rows only: no model, no rig.

    scripts/check_assistant_marks.py RESULTS_DIR [RESULTS_DIR ...] [--marks FILE] [--set role=pattern,pattern ...]

Each RESULTS_DIR is one run (it holds transcripts_<label>.jsonl files). A model's score is the mean of its share over
the runs that hold it; the reference must therefore appear in at least two runs (the replicates).

  (a) the reference scores at least `a_reference_min` on every replicate run and the runs differ by at most
      `a_replicate_max_difference`;
  (b) order: every sound model scores above every damaged one, and every damaged one above every small one;
  (c) no item is failed by every sound model (a broken key); an item passed by every model, the small one included,
      is dead weight and is only reported;
  (d) at most `d_max_cut_share` of a sound model's runs were cut at the token ceiling.

The model set is a parameter: the marks file's `set` (role -> label patterns, fnmatch, case-insensitive) or
`--set role=pat1,pat2` (repeatable; replaces that role). Roles: reference, sound, damaged, small."""
from __future__ import annotations

import argparse
import fnmatch
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
DEFAULT_MARKS = ROOT / "calibration" / "assistant_marks_20261119.json"
ROLES = ("reference", "sound", "damaged", "small")


def _read(path: Path) -> list[dict]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def collect(dirs: list[Path], prof: dict) -> dict[str, list[dict]]:
    """{label: [one assistant45 block per run that holds the model]}"""
    from crucibleforge import assistant45
    from crucibleforge.config import dedupe_rows
    out: dict[str, list[dict]] = {}
    for d in dirs:
        for f in sorted(Path(d).glob("transcripts_*.jsonl")):
            label = f.stem[len("transcripts_"):]
            a = assistant45.compute_stored(prof, dedupe_rows(_read(f)))
            if a is not None and a["status"] != "not_run":
                out.setdefault(label, []).append(a)
    return out


def _match(labels, patterns) -> list[str]:
    pats = [p.lower() for p in patterns]
    return [l for l in labels if any(fnmatch.fnmatchcase(l.lower(), p) for p in pats)]


def _score(blocks: list[dict]) -> float | None:
    vals = [b["share"] for b in blocks if b["status"] == "ok"]
    return sum(vals) / len(vals) if len(vals) == len(blocks) and vals else None


def check(runs: dict[str, list[dict]], marks: dict, roles: dict[str, list[str]]) -> tuple[bool, list[str]]:
    mk = marks["marks"]
    ok, out = True, []

    def say(good: bool, text: str):
        nonlocal ok
        ok = ok and good
        out.append(("PASS  " if good else "FAIL  ") + text)

    found = {r: _match(runs, roles.get(r) or []) for r in ROLES}
    for r in ROLES:
        if not found[r]:
            why = ("not evaluable: no run of the damaged model exists" if r == "damaged"
                   else f"no model found for role '{r}'; patterns {roles.get(r)}")
            say(False, f"not run: no folder for {r} ({why}): its marks are unknown, so they are not met")
    if not found["reference"]:
        return False, out
    ref = found["reference"][0]
    sound = sorted(set(found["sound"]) | {ref})
    damaged, small = found["damaged"], found["small"]
    scores = {l: _score(runs[l]) for l in set(sound) | set(damaged) | set(small)}
    for l, v in sorted(scores.items()):
        out.append(f"INFO  {l}: " + ("not measured" if v is None else f"{v:.3f}")
                   + f" over {len(runs[l])} run(s)")
    # (a)
    ref_runs = [b["share"] for b in runs[ref] if b["status"] == "ok"]
    if len(runs[ref]) < int(mk["a_min_runs"]) or len(ref_runs) != len(runs[ref]):
        say(False, f"(a) the reference needs {mk['a_min_runs']} measured runs, found "
                   f"{len(ref_runs)} measured of {len(runs[ref])}")
    else:
        low, spread = min(ref_runs), max(ref_runs) - min(ref_runs)
        say(low >= float(mk["a_reference_min"]) - 1e-9,
            f"(a) reference lowest run {low:.3f} >= {mk['a_reference_min']}")
        say(spread <= float(mk["a_replicate_max_difference"]) + 1e-9,
            f"(a) replicate runs differ by {spread:.3f} <= {mk['a_replicate_max_difference']}")
    # (b)
    if not damaged or not small:
        say(False, "(b) not evaluable: the order mark needs a run of every role (damaged and small)")
    elif any(scores[l] is None for l in scores):
        say(False, "(b) a model in the set has no measured score (pending, not measured or runs missing)")
    else:
        lo_sound = min(scores[l] for l in sound)
        hi_dam, lo_dam = max(scores[l] for l in damaged), min(scores[l] for l in damaged)
        hi_small = max(scores[l] for l in small)
        say(lo_sound > hi_dam, f"(b) lowest sound {lo_sound:.3f} > highest damaged {hi_dam:.3f}")
        say(lo_dam > hi_small, f"(b) lowest damaged {lo_dam:.3f} > highest small {hi_small:.3f}")
    # (c)
    thr = float(mk["c_item_passed_at"])
    items = sorted({i for l in sound + small + damaged for b in runs[l] for i in (b.get("items") or {})})

    def item_mean(l, i):
        v = [b["items"][i] for b in runs[l] if i in (b.get("items") or {})]
        return sum(v) / len(v) if v else None
    broken = [i for i in items if all((item_mean(l, i) is not None and item_mean(l, i) < thr) for l in sound)]
    say(not broken, "(c) no item failed by every sound model" + (f": {broken}" if broken else ""))
    everyone = sorted(set(sound) | set(damaged) | set(small))
    dead = [i for i in items if all((item_mean(l, i) is not None and item_mean(l, i) >= thr) for l in everyone)]
    out.append("INFO  (c) items passed by every model, the small one included (dead weight, not a failure): "
               + (", ".join(dead) if dead else "none"))
    # (d)
    for l in sound:
        worst = max((b["cut"] / max(1, b["expected_runs"])) for b in runs[l])
        say(worst <= float(mk["d_max_cut_share"]) + 1e-9, f"(d) {l}: cut share {worst:.3f} <= {mk['d_max_cut_share']}")
    return ok, out


_MARK_WORDS = {"(a)": "reference mark failed", "(b)": "order mark failed", "(c)": "broken-key mark failed",
               "(d)": "cut-share mark failed"}


def reasons(lines: list[str]) -> list[str]:
    """Short phrases for the failed or not-run marks of a result: "cut-share mark failed", "damaged-model set not
    run" (what the board's Assistant basics note names while the column is pending validation)."""
    import re
    out: list[str] = []
    for ln in lines:
        if not ln.startswith("FAIL"):
            continue
        body = ln[4:].strip()
        if body.startswith("(b) not evaluable"):          # already said as "<role>-model set not run"
            continue
        m = re.match(r"not run: no folder for (\w+)", body)
        w = (f"{m.group(1)}-model set not run" if m else
             next((v for k, v in _MARK_WORDS.items() if body.startswith(k)), "a mark failed"))
        if w not in out:
            out.append(w)
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("dirs", nargs="+", type=Path, help="results directories, one run each")
    ap.add_argument("--marks", type=Path, default=DEFAULT_MARKS)
    ap.add_argument("--set", action="append", default=[], metavar="ROLE=PATTERN[,PATTERN]")
    ap.add_argument("--profile", default="bench45")
    ap.add_argument("--out", type=Path, default=None,
                    help="write the result (ok, reasons, lines) to this JSON file; the board reads "
                         "<results>/assistant_marks.json for its pending-validation note")
    a = ap.parse_args(argv)
    try:
        marks = json.loads(a.marks.read_text(encoding="utf-8"))
        roles = {k: list(v) for k, v in marks["set"].items()}
        for spec in a.set:
            role, _, pats = spec.partition("=")
            if role not in ROLES or not pats:
                raise ValueError(f"--set wants ROLE=PATTERN with ROLE in {ROLES}: {spec!r}")
            roles[role] = [p for p in pats.split(",") if p]
        for d in a.dirs:
            if not Path(d).is_dir():
                raise ValueError(f"not a directory: {d}")
        from crucibleforge import profiles
        prof = profiles.load_profile(a.profile)
        from crucibleforge import assistant45
        if assistant45.block(prof) is None:
            raise ValueError(f"profile {a.profile} has no assistant block")
    except (OSError, ValueError, KeyError) as e:
        print(f"usage problem: {e}", file=sys.stderr)
        return 2
    runs = collect(a.dirs, prof)
    try:
        ok, lines = check(runs, marks, roles)
    except (KeyError, ValueError, TypeError, ZeroDivisionError, IndexError) as e:
        ok, lines = False, [f"FAIL  not run: the marks could not be evaluated ({type(e).__name__}: {e})"]
    print("\n".join(lines))
    if a.out:
        a.out.write_text(json.dumps({"ok": ok, "reasons": reasons(lines), "lines": lines}, indent=1),
                         encoding="utf-8")
    print("ALL MARKS HOLD" if ok else "MARKS NOT MET: the column stays 'pending'")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
