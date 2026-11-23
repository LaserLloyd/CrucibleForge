"""Test the judged parts' marks (calibration/judge-marks-45.json) on a second-reader agreement table.

usage: python scripts/check_judge_marks.py --agreement <agree json> --folders <result folder>... \
           [--report <report.json>] [--out <results dir>/judge_marks.json] [--part chat/j_rplong]

One line per part: PASS/FAIL <part>: <reason>. Exit 0 when every checked part passes, 1 otherwise. ``--out``
writes the result file the board reads (a part that fails, or is missing, prints "pending" there).
``--report`` takes each part's margin (headline m, points / part max) for the order test and the production part
scores for D1 (d); without it the marks file's default margin and the agreement table's judge means are used.
With ``--folders`` the table is recomputed from the folders' stored second verdicts (agree/second-*.jsonl).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from crucibleforge import agree as AG          # noqa: E402
from crucibleforge import agree_marks as M     # noqa: E402


def _report_inputs(path: Path) -> tuple[dict, dict]:
    d = json.loads(path.read_text(encoding="utf-8"))
    scores, margins = {}, {}
    for label, s in (d.get("models") or {}).items():
        h = s.get("headline") or {}
        for part, v in (h.get("pts") or {}).items():
            mx = (h.get("max") or {}).get(part)
            if v is None or not mx:
                continue
            scores.setdefault(part, {})[label] = v / mx
            m = (h.get("m") or {}).get(part)
            if m is not None:
                margins.setdefault(part, {})[label] = m / mx
    return scores, margins


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--agreement", required=True, action="append")
    ap.add_argument("--folders", nargs="*", default=[])
    ap.add_argument("--report", default=None)
    ap.add_argument("--marks", default=None)
    ap.add_argument("--out", default=None)
    ap.add_argument("--part", action="append", default=None)
    a = ap.parse_args(argv)
    agreement = {"parts": {}}
    for p in a.agreement:
        body = json.loads(Path(p).read_text(encoding="utf-8"))
        agreement["parts"].update(body.get("parts") or {})
        agreement["reader"], agreement["statistic"] = body.get("reader"), body.get("statistic")
    rows = {}
    covered: dict[str, dict] = {}
    recs = []
    readers = [r.strip() for r in str(agreement.get("reader") or "").split(",") if r.strip()]
    for f in a.folders:
        label, rs = AG.read_folder(Path(f))
        rows[label] = rs
        cv = covered.setdefault(label, {"folders": [], "run_ids": []})
        cv["folders"].append(Path(f).name)
        cv["run_ids"] = sorted(set(cv["run_ids"]) | {str(r["bench_run_id"]) for r in rs if r.get("bench_run_id")})
        units = AG.long_units(rs)
        cases = AG._case_map()
        for rd in readers:
            for key in (AG.reader_key(rd, True), rd):
                for r in AG.load_records(Path(f), key):
                    row = units.get(r["unit"]) if r["part"] == AG.LONG_PART else None
                    if row is not None and r.get("reader_verdict") and AG.row_sha(row) == r.get("row_sha"):
                        r = AG.refresh_long(r, row, cases.get(row["case_id"]))      # today's scorer
                    recs.append(r)
    if recs:
        # recomputed from the stored second verdicts (placements, the family rule) for the parts they cover
        fresh = AG.summarize(AG.standard_long(recs))
        agreement["parts"].update({k: v for k, v in fresh.items() if not a.part or k in a.part})
    scores, margins = _report_inputs(Path(a.report)) if a.report else ({}, {})
    res = M.check(agreement, M.load_marks(a.marks), rows, AG._case_map(), scores, margins, a.part)
    for part, r in res.items():
        print(f"{'PASS' if r['pass'] else 'FAIL'} {part}: {r['reason']}")
        for extra in r.get("fails", [])[1:]:
            print(f"     {extra}")
        if r.get("judge_term_share") is not None:
            print(f"     judge term {100 * r['judge_term_share']:.1f}% of the part")
        for inf in r.get("informational") or []:
            print(f"     informational: {inf}")
    if a.out:
        M.write_result(Path(a.out), res, a.agreement, covered, a.marks)
        print(f"result: {a.out}")
    return 0 if res and all(r["pass"] for r in res.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
