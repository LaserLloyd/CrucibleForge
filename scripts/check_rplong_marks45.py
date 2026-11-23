"""Test the long chat scenes' pre-registered marks (mean of judge and second reader) on stored reader verdicts.

usage: python scripts/check_rplong_marks45.py --results <dir> [--folders <run folder>...] [--readers <id>...]
           [--report <report.json>] [--no-write]

Reads the reader store ``<results>/readers45/*.jsonl`` and, for ``--folders``, the ``agree/second-*.jsonl`` files an
earlier agree run left there (no model call either way). Prints the judge term overall and per scene group, then
PASS/FAIL. Writes ``<results>/marks45.json`` (key chat_rplong). Exit 0 pass / 1 fail / 2 not measured."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from crucibleforge import agree as AG            # noqa: E402
from crucibleforge import rplong_marks as RK     # noqa: E402
from crucibleforge import rplong_mean as RM      # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--results", required=True)
    ap.add_argument("--folders", nargs="*", default=[])
    ap.add_argument("--readers", nargs="*", default=[])
    ap.add_argument("--report", default=None)
    ap.add_argument("--no-write", action="store_true")
    a = ap.parse_args(argv)
    recs, rows = [], {}
    for p in sorted((Path(a.results) / RM.STORE_DIR).glob("*.jsonl")):
        for line in p.open(encoding="utf-8"):
            if line.strip():
                r = json.loads(line)
                # the store is shared with the classing reader's vote lines (they carry `vote`, no `part`); only the
                # long-scene records (which carry `part`) belong here, as classing45.ReaderStore keeps only `vote` lines
                if isinstance(r, dict) and "part" in r:
                    recs.append(r)
    for f in a.folders:
        label, rs = AG.read_folder(Path(f))
        rows[label] = rs
        for rd in a.readers:
            for key in (AG.reader_key(rd, True), rd):
                recs += [r for r in AG.load_records(Path(f), key) if r.get("part") == AG.LONG_PART]
    recs = AG.standard_long([r for r in recs if not AG.measured(r)])            # the family rule, again
    best = {}
    for r in recs:
        best[(r["model"], r["unit"], r.get("reader"))] = r
    recs = list(best.values())
    scores, margins = {}, {}
    if a.report:
        from check_judge_marks import _report_inputs
        scores, margins = _report_inputs(Path(a.report))
    res = RK.check(recs, RK.load_marks(), rows_by_model=rows or None, cases=AG._case_map(),
                   margins=margins, scores=scores)
    print(f"{'PASS' if res['pass'] else 'FAIL'} chat/j_rplong (mean of two readers): {res['reason']}")
    for f in res["fails"][1:]:
        print(f"     {f}")
    for g, t in res.get("groups", {}).items():
        if t["term"] is not None:
            print(f"     {g:10s} n={t['n']:2d} gap {t['gap']:+.3f} judge term {100 * t['term']:.2f}% "
                  f"(single reader {100 * t['single']:.2f}%)")
    for i in res.get("informational", []):
        print(f"     informational: {i}")
    if not a.no_write:
        print("result:", RK.write_result(a.results, res))
    return RK.exit_code(res)


if __name__ == "__main__":
    sys.exit(main())
