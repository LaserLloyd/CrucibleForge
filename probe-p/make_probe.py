"""Writes the probe's ladder set file and profile (idempotent).

  python probe/make_probe.py [--worktree DIR] [--families keyfunc:7,8 tracevm:7 istack-write:6 ...] [--seeds p1,p2]

The cases are generator specs ({type, level, key}); the generator that materialises them is whatever crucibleforge
code the worktree holds, so probing a new level straight from an A3/A4 branch = point --worktree at that branch's
checkout (it needs profiles/bench45.yaml). Family syntax: `family[-variant]:level[,level...]`.
Default (no --families) = the original S2 plan."""
import argparse, json, pathlib, sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
CATEGORY = {"keyfunc": "coding", "tracevm": "reasoning", "ledger": "reasoning",
            "istack": "rpdeep", "session_state": "rpdeep",
            "mealyid": "coding", "cipherinv": "coding", "specforge": "coding", "calforge": "coding", "tcount": "coding", "weights": "coding",
            "stacksyn": "coding", "srspath": "coding", "patgen": "coding"}
DEFAULT_FAMILIES = ["keyfunc:3,5", "tracevm:3,5", "ledger:3,4,5", "istack-write:4,6", "istack-conv:4,6",
                    "session_state-multi:4,6"]
ANCHORS = {"coding": [], "reasoning": [], "rpdeep": [],
           "codebank": []}


def parse_families(specs):
    """['keyfunc:7,8', 'istack-write:6'] -> [(family, variant|None, category, [levels])]"""
    if not specs:
        raise ValueError("no families given")
    plan = []
    for s in specs:
        name, sep, lv = s.partition(":")
        if not sep or not lv:
            raise ValueError(f"bad family spec {s!r}: want family[-variant]:level,level")
        fam, _, var = name.partition("-")
        if fam not in CATEGORY:
            raise ValueError(f"unknown family {fam!r} (known: {', '.join(sorted(CATEGORY))})")
        try:
            levels = [int(x) for x in lv.split(",")]
        except ValueError:
            raise ValueError(f"bad levels in {s!r}") from None
        plan.append((fam, var or None, CATEGORY[fam], levels))
    return plan


def build_cases(plan, seeds):
    cases, ids = [], {k: list(v) for k, v in ANCHORS.items()}
    for fam, var, cat, lvls in plan:
        tag = fam if not var else f"{fam}-{var}"
        for lv in lvls:
            for s in seeds:
                cid = f"PRB-{tag}-L{lv}-{s}"
                g = {"type": fam, "level": lv, "key": s}
                if var:
                    g["variant"] = var
                cases.append({"id": cid, "category": cat, "generator": g})
                ids[cat].append(cid)
    return cases, ids


def write(root, plan, seeds):
    import yaml
    root = pathlib.Path(root)
    cases, ids = build_cases(plan, seeds)
    d = root / "cases/ladder/probe/probe"
    d.mkdir(parents=True, exist_ok=True)
    (d / "set-1.json").write_text(json.dumps({"level": 1, "budget": 65536, "cases": cases}, indent=1))
    prof = yaml.safe_load((root / "profiles/bench45.yaml").read_text())
    prof["name"] = "probe45"
    prof["description"] = "S2 level probe (scratch)"
    prof["release_seed"] = "4.5.0"
    prof["cases"] = ids
    prof["repeats"] = {k: 1 for k in ["perf", "rp", "rpdeep", "nsfw", "story", "steer", "coding", "codebank",
                                      "codeagent", "tooluse", "instruct", "reasoning", "math"]}
    prof["case_repeats"] = {}
    prof["scores"] = {"work": {"label": "Probe", "parts": {"code": {"label": "Probe", "short": "P", "max": 8,
                      "scoring": ["coding", "reasoning", "rpdeep", "codebank"]}}}}
    prof.pop("run_plan", None); prof.pop("budget", None); prof.pop("core_generated", None)
    (root / "profiles/probe45.yaml").write_text(yaml.safe_dump(prof, sort_keys=False))
    return cases, ids


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--worktree", default=str(ROOT), help="checkout whose generators are probed (default: this one)")
    ap.add_argument("--families", nargs="*", default=DEFAULT_FAMILIES)
    ap.add_argument("--seeds", default="p1,p2")
    a = ap.parse_args(argv)
    try:
        plan = parse_families(a.families)
    except ValueError as e:
        ap.error(str(e))
    cases, ids = write(a.worktree, plan, a.seeds.split(","))
    print(len(cases), "generated cases;", sum(len(v) for v in ids.values()), "total ids")


if __name__ == "__main__":
    main()
