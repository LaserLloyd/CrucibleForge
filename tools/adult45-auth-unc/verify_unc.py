"""Prove AUTH-UNC banks against the N3-WIRE adult45 loader: ref grades 1.0 with every check passing,
every fail reply fails at least one check. Usage: python verify_unc.py <n3-wire tree> <auth-unc tree>"""
import json
import re
import sys
from pathlib import Path

n3, me = Path(sys.argv[1]), Path(sys.argv[2])
sys.path.insert(0, str(n3))
from crucibleforge import ladder, session_checks  # noqa: E402
from crucibleforge.gen import adult45 as A  # noqa: E402
from crucibleforge.gen._stack_common import split_turns  # noqa: E402

ladder.LADDER_DIR = me / "cases/ladder"
A.FILES["dark"] = "sessions-unc.json"

AGE_BAD = re.compile(r"\b(?:teen\w*|minor|minors|underage|schoolgirl\w*|animal)\b|\b\d{1,2}[- ]years?[- ]old\b", re.I)
SMALLNUM = re.compile(r"\b(\d{1,2})\s*[,)]")


def fails(chks, replies):
    out = session_checks.run_checks({"checks": chks}, replies) or {"results": []}
    by = {r["id"]: r for r in out["results"]}
    return [c["id"] for c in chks if not (by.get(c["id"]) and session_checks.check_credit(by[c["id"]]) >= 1.0)]


bad = 0
for v in ("uask", "dark", "open", "ground"):
    for it in A.items(v):
        g = {"variant": v, "pin": it["id"]}
        m = A.materialize(g, release_seed="t", case_id="c")
        ref = A.reference(g, release_seed="t", case_id="c")
        rf = fails(m["checks"], split_turns(ref))
        sc = A.grade(ref, m)
        line = f"{v:6} {it['id']:28} ref={sc:.3f}"
        if rf or sc < 1.0:
            line += f" REF-FAILS {rf}"
            bad += 1
        for name, rep in (it.get("fail_replies") or {}).items():
            from crucibleforge.gen._stack_common import join_turns
            txt = join_turns(list(rep))
            ff = fails(m["checks"], split_turns(txt))
            line += f" | {name}:{A.grade(txt, m):.2f}{ff}"
            if not ff:
                bad += 1
                line += " NO-FAIL"
        probs = A.item_problems(v, it)
        prompt = " ".join([m["system"]] + [h["content"] for h in m["history"]] + list(m["turns"]))
        if AGE_BAD.search(prompt):
            probs.append(f"forbidden word {AGE_BAD.search(prompt).group(0)!r}")
        for n in SMALLNUM.findall(prompt):
            if int(n) < 21:
                probs.append(f"small number {n} before , or )")
        if probs:
            bad += 1
            line += f" PROBLEMS {probs}"
        print(line)
print("BAD", bad)
