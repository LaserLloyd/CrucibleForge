"""Per family x level x model table with token caps (24,576 and 65,536) and R0 flags.
usage: python probe-o/table_o.py RESULTS_DIR [RESULTS_DIR ...]"""
import json, sys, re, statistics as st, collections, pathlib
CASE = re.compile(r"^PRB-(?P<fam>.+)-L(?P<lv>\d+)-(?P<seed>p\d+)$")
MODELS = ["probe-haiku", "probe-sonnet", "probe-fable"]
rows = []
for d in sys.argv[1:]:
    for m in MODELS:
        f = pathlib.Path(d) / f"transcripts_{m}.jsonl"
        if f.exists():
            rows += [json.loads(l) for l in f.read_text().splitlines() if l.strip()]
cell = collections.defaultdict(list)
for r in rows:
    mm = CASE.match(r["case_id"])
    if not mm:
        continue
    met = r.get("metrics") or {}
    tok = (met.get("completion_tokens") or 0) + (met.get("reasoning_tokens") or 0)
    els = (r.get("elements") or {})
    rate = els.get("rate")
    if rate is None:
        rate = 1.0 if r.get("grade") == "pass" else 0.0
    det = r.get("grade_detail") or ""
    flag = ""
    if "no code" in det: flag = "nocode"
    elif "SyntaxError" in det and "line 1" in det: flag = "syntax1"
    elif "timeout" in det.lower() or "timed out" in det.lower(): flag = "timeout"
    cell[(mm["fam"], int(mm["lv"]), r["model_label"])].append((mm["seed"], rate, tok, r.get("finish_reason"), flag))
fams = sorted({k[0] for k in cell})
for fam in fams:
    print(f"\n### {fam}\n")
    print("| level | model | n | scores (per seed) | mean | timeouts | mean excl. timeouts | median / max tokens | over 24,576 | over 65,536 | finish | R0 flags |")
    print("|---|---|---|---|---|---|---|---|---|---|---|---|")
    for lv in sorted({k[1] for k in cell if k[0] == fam}):
        for m in MODELS:
            xs = sorted(cell.get((fam, lv, m), []))
            if not xs: continue
            sc = [x[1] for x in xs]; tk = [x[2] for x in xs]
            fin = collections.Counter(x[3] for x in xs)
            fl = collections.Counter(x[4] for x in xs if x[4])
            nto = [x[1] for x in xs if x[4] != "timeout"]
            ex = f"{st.mean(nto):.2f}" if nto else "n/a"
            print(f"| L{lv} | {m[6:]} | {len(xs)} | {' '.join(f'{s:.2f}' for s in sc)} | **{st.mean(sc):.2f}** | "
                  f"{sum(x[4] == 'timeout' for x in xs)} | {ex} | "
                  f"{int(st.median(tk))} / {max(tk)} | {sum(t > 24576 for t in tk)} | {sum(t > 65536 for t in tk)} | "
                  f"{dict(fin)} | {dict(fl) or '-'} |")

# per family x model summary over all levels and instances (mean, SD across instances, rates)
print("\n### summary (all levels)\n")
print("| family | model | n | mean | SD across instances | score if cut at 65,536 | timeout rate | over 24,576 | over 65,536 | median / max tokens |")
print("|---|---|---|---|---|---|---|---|---|---|")
for fam in fams:
    for m in MODELS:
        xs = [x for k, v in cell.items() if k[0] == fam and k[2] == m for x in v]
        if not xs: continue
        sc = [x[1] for x in xs]; tk = [x[2] for x in xs]
        sd = st.pstdev(sc) if len(sc) > 1 else 0.0
        cut = st.mean([x[1] if x[2] <= 65536 else 0.0 for x in xs])
        print(f"| {fam} | {m[6:]} | {len(xs)} | {st.mean(sc):.2f} | {sd:.2f} | {cut:.2f} | {sum(x[4] == 'timeout' for x in xs) / len(xs):.2f} | "
              f"{sum(t > 24576 for t in tk) / len(xs):.2f} | {sum(t > 65536 for t in tk) / len(xs):.2f} | {int(st.median(tk))} / {max(tk)} |")
