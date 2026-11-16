"""Turn-2 cases for one model from its turn-1 transcripts: set-<n>.json + profiles/probe45-<tag>.yaml.
usage: python make_t2.py WORKTREE TAG MODEL_NAME RESULTS_DIR_A [SET_N]
(TAG haiku|sonnet|fable keep their historic set numbers 2/3/4; any other tag needs SET_N, or gets 5+)"""
import json, os, pathlib, sys, yaml
wt, m, name, res = pathlib.Path(sys.argv[1]), sys.argv[2], sys.argv[3], pathlib.Path(sys.argv[4])
N = {"haiku": 2, "sonnet": 3, "fable": 4}.get(m) or int(sys.argv[5])
rows = [json.loads(l) for l in (res / f"transcripts_{name}.jsonl").read_text().splitlines() if l.strip()]
cases, ids = [], []
for r in rows:
    cid = r["case_id"]
    if not cid.startswith("PRB-patgen-t1-"):
        continue
    seed = cid.rsplit("-", 1)[1]
    nid = f"PRB-patgen-t2{m}-L2-{seed}"
    cases.append({"id": nid, "category": "coding",
                  "generator": {"type": "patgen", "level": 2, "key": seed, "variant": "t2",
                                "t1_answer": r.get("response") or ""}})
    ids.append(nid)
d = wt / "cases/ladder/probe/probe"
tmp = d / f".set-{N}.tmp"
tmp.write_text(json.dumps({"level": N, "budget": 65536, "cases": cases}, indent=1))
os.replace(tmp, d / f"set-{N}.json")
prof = yaml.safe_load((wt / f"profiles/{os.environ.get('BASEPROF','probe45')}.yaml").read_text())
prof["name"] = f"probe45-{m}"
prof["cases"] = {"coding": ids, "reasoning": [], "rpdeep": [], "codebank": []}
(wt / f"profiles/probe45-{m}.yaml").write_text(yaml.safe_dump(prof, sort_keys=False))
print(len(ids), "turn-2 cases for", m)
