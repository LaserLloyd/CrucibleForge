"""Re-grade the stored rows of the U1-U4 ladders against the banks on disk (code grade only; the judge is not re-run).
Usage: CF_COMPOSITION=rc4-main python tools/adult45-auth-unc/regrade_rows.py <transcripts.jsonl> <out.json>"""
import hashlib
import importlib
import json
import sys

from crucibleforge import chains, ladder, profiles, session_checks
from crucibleforge.gen import adult45_pins
from crucibleforge.gen._stack_common import join_turns

for k in adult45_pins.BANK_SHA:  # grade what is on disk, pinned or not
    adult45_pins.BANK_SHA[k] = hashlib.sha256((ladder.LADDER_DIR / k).read_bytes()).hexdigest()[:16]

LADDERS = ("a45_uask", "a45_dark", "a45_open", "a45_ground", "a45_know")
p = profiles.load_profile("bench45")
rs = p["release_seed"]
ccs = {c["id"]: c for c in chains.chain_cases(p) if c["chain_spec"]["ladder"].startswith(LADDERS)}
rows = [json.loads(line) for line in open(sys.argv[1], encoding="utf-8")]
by: dict[str, dict[int, str]] = {}
for r in rows:
    by.setdefault(r["case_id"], {})[int(r["turn"] or 1)] = r["response"] or ""
out = {}
for cid, turns in sorted(by.items()):
    chain_id = cid.rsplit("-r", 1)[0]
    if chain_id not in ccs:
        continue
    inst = chains.instance(ccs[chain_id], 1, 1, release_seed=rs)
    gen = inst["generator"]
    fam = importlib.import_module(f"crucibleforge.gen.{gen['type']}")
    m = fam.materialize(gen, release_seed=rs, case_id=inst["id"])
    ans = join_turns([turns[t] for t in sorted(turns)])
    res = session_checks.run_checks({"checks": m["checks"]}, [turns[t] for t in sorted(turns)]) if m.get("checks") else None
    failed = [x["id"] for x in (res or {"results": []})["results"] if session_checks.check_credit(x) < 1.0]
    if gen["type"] == "unc_know":
        ids = {it["n"]: it["id"] for it in m["grader_config"]["items"]}
        failed = [f"{ids.get(int(x['id'][1:].split('-')[0]), x['id'])}:{x['id']}" for x in fam.elements(ans, m) if not x["pass"]]
    out[cid] = {"card": (m.get("meta") or {}).get("card"), "grade": fam.grade(ans, m), "failed": failed}
json.dump(out, open(sys.argv[2], "w"), indent=1)
print(len(out), "rows")
