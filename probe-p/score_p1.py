"""Offline exact P1 scores (isolated runner) for code files: python score_p1.py SEED[,SEED] FILE..."""
import sys
sys.path.insert(0, ".")
from crucibleforge.gen import patgen
for key in sys.argv[1].split(","):
    inst = patgen.instance(2, patgen.instance_key({"level": 2, "key": key}, "4.5.0"))
    for f in [a for a in sys.argv[2:] if a.endswith(".py")]:
        code = open(f).read()
        for var in ("t1", "t2"):
            r = patgen.score_code(code, inst[var])
            print(f"{key} {f.split('/')[-1]:22s} {var} points={r['points']:6.2f} " +
                  " ".join(f"{k}={v:.3f}" for k, v in r["tiers"].items()), f"cpu={r['cpu_s']}s", r["detail"])
