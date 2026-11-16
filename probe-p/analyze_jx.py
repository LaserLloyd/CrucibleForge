"""Exact P1 v2 scores for lane-P probe rows (isolated runner; patgen only).
usage: python analyze_jx.py RESULTS_DIR... > table.md"""
import json, pathlib, sys
sys.path.insert(0, ".")
from crucibleforge import graders
from crucibleforge.gen import patgen

rows = []
for d in sys.argv[1:]:
    for f in pathlib.Path(d).glob("transcripts_*.jsonl"):
        rows += [json.loads(l) for l in f.read_text().splitlines() if l.strip()]
print("| case | model | points | tier scores | cpu s | tokens | finish |")
print("|---|---|---|---|---|---|---|")
for r in sorted(rows, key=lambda r: (r["case_id"], r["model_label"])):
    cid = r["case_id"]
    code = graders.extract_python(r.get("response") or "", r.get("finish_reason"))
    met = r.get("metrics") or {}
    if isinstance(met, str): met = json.loads(met)
    tok = (met.get("completion_tokens") or 0) + (met.get("reasoning_tokens") or 0)
    seed = cid.rsplit("-", 1)[1]
    inst = patgen.instance(2, patgen.instance_key({"level": 2, "key": seed}, "4.5.0"))
    sets = [("t1", "t1")]
    if "-t1-" in cid:
        sets = [("t1", "t1"), ("t2", "t2 (turn-1 code on turn-2 streams)")]
    else:
        sets = [("t2", "t2")]
    for var, lab in sets:
        res = patgen.score_code(code, inst[var]) if code else {"points": 0, "tiers": {}, "cpu_s": None, "detail": "no code"}
        txt = " ".join(f"{k}={v:.2f}" for k, v in res["tiers"].items())
        print(f"| {cid} {lab} | {r['model_label'][6:]} | {res['points']:.1f} | {txt} {res['detail'][:50]} | {res['cpu_s']} | {tok} | {r.get('finish_reason')} |")
