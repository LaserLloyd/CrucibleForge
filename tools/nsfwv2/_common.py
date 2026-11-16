"""Shared loading for the NSFW v2 analysis tools (read-only over results dirs).

Rows are reduced to ids and numbers; no transcript or case text is kept.
Scoring (W, C, S) is delegated to ``crucibleforge.report`` so the tools can
never drift from the board's formula.
"""
from __future__ import annotations

import glob
import json
import os
import random
import re
import statistics as st
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from crucibleforge import report  # noqa: E402

DEFAULT_SETS = os.environ.get(
    "CF_NSFWV2_SETS",
    str(_ROOT / "cases" / "private" / "nsfw-v2" / "filled" / "sets"))
_ID_PACK = re.compile(r"^L([RUW])(\d)-")


def mean(xs):
    xs = list(xs)
    return sum(xs) / len(xs) if xs else None


def load_cases(sets_dir=None) -> dict:
    """{case_id: case dict} from a sets dir (read-only). Also primes the
    report module's case-tag cache so v2 tags (willing, judge_willingness...)
    resolve exactly as in the board."""
    d = sets_dir or DEFAULT_SETS
    out = {}
    if d and os.path.isdir(d):
        for f in sorted(glob.glob(os.path.join(d, "*.json"))):
            try:
                doc = json.load(open(f))
            except (OSError, ValueError):
                continue
            for c in doc.get("cases", []) if isinstance(doc, dict) else []:
                if isinstance(c, dict) and c.get("id"):
                    out[c["id"]] = c
    if out:
        report._CASES_BY_ID = {**(report._CASES_BY_ID or {}), **out}
    elif report._CASES_BY_ID is None:
        report._CASES_BY_ID = {}      # never fall back to the live case dirs
    return out


def pack_of(case_id: str, cases: dict | None = None) -> str | None:
    c = (cases or {}).get(case_id)
    if c and c.get("pack") in (1, 2):
        return f"P{c['pack']}"
    m = _ID_PACK.match(case_id or "")
    if not m:
        return None
    part, lvl = m.group(1), int(m.group(2))
    if part == "W":
        return "P1" if lvl == 3 else "P2"
    return "P1" if lvl == 2 else "P2"


def part_of(case_id: str, cases: dict | None = None) -> str | None:
    c = (cases or {}).get(case_id)
    if c and c.get("part"):
        return c["part"]
    m = _ID_PACK.match(case_id or "")
    return {"R": "rp", "U": "uncensored", "W": "writing"}.get(m.group(1)) if m else None


def find_transcripts(path) -> dict:
    """{model: jsonl path} in a results dir."""
    out = {}
    for f in sorted(glob.glob(os.path.join(str(path), "transcripts_*.jsonl"))):
        out[os.path.basename(f)[len("transcripts_"):-len(".jsonl")]] = f
    return out


def _read_jsonl(f):
    with open(f) as fh:
        for line in fh:
            line = line.strip()
            if line:
                try:
                    yield json.loads(line)
                except ValueError:
                    continue


GUARD_CHECKS = {"no-disclaimer", "no_disclaimer", "no-fade", "no_fade", "no-refusal", "no_refusal",
                "no-ooc-framing", "no_ooc", "no-ooc", "slop", "degradation", "term_density",
                "term-density", "speaker_spread", "speaker-spread"}


def is_guard(item_id: str, x: dict) -> bool:
    """Guard items pass for every competent model by design: flagged dead only
    when they FAIL for everyone. Named checks, any forbid_regex / flag-only
    check, and negative elements."""
    if item_id.startswith("chk:"):
        return (item_id[4:] in GUARD_CHECKS or x.get("type") == "forbid_regex"
                or bool(x.get("flag_only")) or "forbid" in str(x.get("kind") or x.get("check") or ""))
    return bool(x.get("negative"))


def element_items(r: dict) -> dict:
    """{item id: {'credit','pass','weight','yes':[0/1 per sample] | None}} for
    one judged row: judge elements by id, objective checks as ``chk:<id>``.
    Skips what ``report.wc_credit`` skips (unscored / flag-only / multiplier)."""
    j = r.get("judge") or {}
    n_s = j.get("samples")
    out = {}
    for x in (j.get("elements") or {}).get("results") or []:
        if x.get("scored") is False:
            continue
        c = x.get("credit")
        if c is None:
            c = 1.0 if x.get("pass") else 0.0
        yes = None
        vs = x.get("verified_samples")
        if isinstance(vs, list) and isinstance(n_s, int) and n_s > 0:
            yes = [1 if i in vs else 0 for i in range(n_s)]
        out[str(x["id"])] = {"credit": float(c), "pass": bool(x.get("pass")),
                             "weight": report._weight(x), "yes": yes,
                             "guard": is_guard(str(x["id"]), x)}
    for x in report._check_results(r):
        if x.get("scored") is False or (x.get("weight") is not None and float(x["weight"]) == 0):
            continue
        if x.get("multiplier") is not None or x.get("flag_only") or x.get("fallback") \
                or x.get("report_only"):
            continue
        out["chk:" + str(x["id"])] = {"credit": float(report._credit(x)), "pass": bool(x.get("pass")),
                                      "weight": report._weight(x), "yes": None,
                                      "guard": is_guard("chk:" + str(x["id"]), x)}
    return out


def load_model(f: str, cases: dict | None = None, only_packs=None) -> list[dict]:
    """Scored rows of one model: one dict per generation rep (the last judged
    row wins, as in the mini-pilot matrix). Keys: case, pack, rep, bench,
    w, engaged, c, s, items, n_samples. Errored / unjudged / judge-failed /
    excluded reps are dropped (counted in the ``dropped`` pseudo-field of
    the first row's loader stats via the module-level helper below)."""
    by = {}
    for r in _read_jsonl(f):
        if r.get("category") != "nsfw":
            continue
        pk = pack_of(r.get("case_id"), cases)
        if pk is None or (only_packs and pk not in only_packs):
            continue
        by.setdefault((r["case_id"], r.get("repeat"), r.get("bench_run_id")), []).append(r)
    out = []
    for (cid, rep, bench), rs in sorted(by.items(), key=lambda kv: (kv[0][0], str(kv[0][1]), str(kv[0][2]))):
        if any(r.get("error") for r in rs):
            continue
        judged = [r for r in rs if r.get("judge")]
        if not judged:
            continue
        r = judged[-1]
        if (r["judge"] or {}).get("judge_failed") and not report.filtered_row(r):
            continue
        split, _ = report.wc_rows_split([r])
        if not split:
            continue
        x = report.wc_row(r)
        out.append({"case": cid, "pack": pack_of(cid, cases), "rep": rep, "bench": bench,
                    "w": x["w"], "engaged": bool(x["engaged"]), "c": x["c"], "s": x["s"],
                    "items": element_items(r), "n_samples": (r["judge"] or {}).get("samples"),
                    "key": f"{cid}|{rep}|{bench}", "pair": f"{cid}|{rep}"})
    return out


def load_dirs(dirs, cases=None, models=None, only_packs=None) -> dict:
    """{model: [rows]} over one or more results dirs."""
    out = {}
    for d in dirs:
        for m, f in find_transcripts(d).items():
            if models and m not in models:
                continue
            out.setdefault(m, []).extend(load_model(f, cases, only_packs))
    return out


def case_scores(rows) -> dict:
    """{case: mean row S (0..1)}"""
    by = {}
    for r in rows:
        by.setdefault(r["case"], []).append(r["s"])
    return {k: mean(v) for k, v in by.items()}


def pack_scores(data: dict, packs=("P1", "P2")) -> dict:
    """{model: {pack: {'S': 0..100, 'cases': {case: S01}}}}"""
    out = {}
    for m, rows in data.items():
        for p in packs:
            cs = case_scores([r for r in rows if r["pack"] == p])
            if cs:
                out.setdefault(m, {})[p] = {"S": 100 * mean(cs.values()), "cases": cs}
    return out


def percentile(sorted_xs, q):
    if not sorted_xs:
        return None
    k = (len(sorted_xs) - 1) * q
    lo = int(k)
    hi = min(lo + 1, len(sorted_xs) - 1)
    return sorted_xs[lo] + (sorted_xs[hi] - sorted_xs[lo]) * (k - lo)


def split_heldout(data: dict, cases: dict | None = None, extra=()) -> tuple[dict, dict]:
    """(tune models, held-out models): held-out subjects are never pooled into
    the tune bars or statistics; they get their own table."""
    held = {m for m in data if report.is_heldout(m, cases, extra)}
    return ({m: r for m, r in data.items() if m not in held},
            {m: r for m, r in data.items() if m in held})


def add_common_args(ap):
    ap.add_argument("--heldout", nargs="*", default=[],
                    help="model id prefixes treated as held-out subjects (default also: hermes-4.3-36b "
                         "and meta.authorship.heldout); reported apart, never pooled")
    ap.add_argument("--sets-dir", default=None,
                    help="filled sets dir (read-only; case tags + pack ids). Default: $CF_NSFWV2_SETS or the live filled/sets")
    ap.add_argument("--out", default=None, help="write the JSON result here")
    ap.add_argument("--models", nargs="*", default=None,
                    help="only these model labels (default: every transcripts_<model>.jsonl found)")


def write_out(path, obj):
    if path:
        Path(path).write_text(json.dumps(obj, indent=1, sort_keys=True))
