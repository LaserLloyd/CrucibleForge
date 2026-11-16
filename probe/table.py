#!/usr/bin/env python3
"""GEN2-P probe table: per family x level x model, the stored engine score per seed, total output tokens
(completion + reasoning when reported separately), rows over 24,576 and 65,536 tokens, call timeouts (no
answer) and sandbox timeouts counted separately, patch-apply failures (edit blocks that did not apply or no
edits found), which hidden groups failed, and the score each row would get if cut at each cap.
  python probe/table.py RESULTS_DIR [RESULTS_DIR ...]"""
import collections
import json
import pathlib
import re
import statistics as st
import sys

MODELS = ["probe-haiku", "probe-sonnet", "probe-fable"]
CASE = re.compile(r"^PRB-(?P<fam>.+)-L(?P<lv>\d+)-(?P<seed>p\d+)$")
TIMEOUT = re.compile(r"time limit|timed out|timeout >|never ran", re.I)


def rows(dirs):
    for d in dirs:
        for m in MODELS:
            f = pathlib.Path(d) / f"transcripts_{m}.jsonl"
            if f.exists():
                for line in f.read_text().splitlines():
                    if line.strip():
                        yield m, json.loads(line)


def main(dirs):
    cell = collections.defaultdict(list)
    for m, r in rows(dirs):
        mm = CASE.match(r["case_id"])
        if not mm:
            continue
        met = r.get("metrics") or {}
        tok = (met.get("completion_tokens") or 0) + (met.get("reasoning_tokens") or 0)
        els = (r.get("elements") or {}).get("results") or []
        fe = r.get("format_errors") or []
        det = r.get("grade_detail") or ""
        no_ans = r.get("finish_reason") == "error" or (tok == 0 and not (r.get("response") or "").strip())
        rec = {"score": float(r.get("score") or 0.0), "tok": tok, "s": met.get("total_s") or 0,
               "apply_fail": bool(fe) or "no file edits" in det, "fe": fe, "no_answer": no_ans,
               "sbx_timeout": sum(1 for e in els if not e.get("pass") and TIMEOUT.search(e.get("detail") or "")),
               "failed": [e["id"] for e in els if not e.get("pass")], "seed": mm["seed"]}
        cell[(mm["fam"], int(mm["lv"]), m)].append(rec)
    out = ["| family | L | model | scores per seed | mean | mean of rows that applied (R0) | mean if cut at 24,576 | over 24,576 | over 65,536 | "
           "median tok | call timeouts | sandbox timeouts | apply failures | failed groups |",
           "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for (fam, lv, m) in sorted(cell):
        rs = sorted(cell[(fam, lv, m)], key=lambda r: r["seed"])
        sc = [r["score"] for r in rs]
        cut = [0.0 if r["tok"] > 24576 else r["score"] for r in rs]
        failed = collections.Counter(g for r in rs for g in r["failed"])
        out.append(f"| {fam} | {lv} | {m.split('-')[1]} | {' '.join(f'{x:.2f}' for x in sc)} | {st.mean(sc):.2f} | "
                   f"{(lambda a: f'{st.mean(a):.2f}' if a else '-')([r['score'] for r in rs if not r['apply_fail']])} | "
                   f"{st.mean(cut):.2f} | {sum(r['tok'] > 24576 for r in rs)} | {sum(r['tok'] > 65536 for r in rs)} | "
                   f"{int(st.median(r['tok'] for r in rs))} | {sum(r['no_answer'] for r in rs)} | "
                   f"{sum(r['sbx_timeout'] for r in rs)} | {sum(r['apply_fail'] for r in rs)} | "
                   f"{', '.join(f'{g}x{n}' for g, n in sorted(failed.items())) or '-'} |")
    notes = [f"- {fam} L{lv} {m} {r['seed']}: {'; '.join(r['fe'])[:200]}" for (fam, lv, m), rs in sorted(cell.items())
             for r in rs if r["fe"]]
    print("\n".join(out))
    if notes:
        print("\nFormat / apply problems:\n" + "\n".join(notes))


if __name__ == "__main__":
    main(sys.argv[1:])
