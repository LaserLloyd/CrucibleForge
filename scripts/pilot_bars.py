#!/usr/bin/env python3
"""G-Headroom: do the pilot results leave room at the top? Exit 0 = pass.

    python3 scripts/pilot_bars.py pilot.json [--json]

Input (one JSON file, numbers only):
  {"order":   ["haiku-5-5", "sonnet-5-5", "fable-5-2"],        # weakest -> strongest
   "scores":  {"<model>": {"<score>": 7.1, ...}},               # each /10 score
   "frontier":{"<model>": {"<part>": 0.31, ...}},               # share 0..1 per Frontier part
   "families":{"<family>": {"sfw": true, "core_level": 3, "top_level": 5,
                            "shares": {"<model>": {"3": 0.62, "5": 0.2}}}},
   "noise":   {"measured": 0.11, "assumed": 0.10},              # item noise (sd of a share)
   "local":   ["<model>", ...]}                                  # local models (floor check)

Bars (PLAN-45 v5 section 5, G-Headroom). All of:
  1 best panel model < 8.5 on each /10 score
  2 no model reaches 0.9 on any Frontier part (no unlock)
  3 per family at its Core level: best share in [0.45, 0.85] and panel sd >= 0.15
  4 capability order holds (ties allowed) at Core level on >= 4 of the 5 SFW families
  5 best share at each family's top rung < 0.5
  6 measured item noise <= 1.5 x the assumed noise
  7 REPORTED, not gated: the top local model's mean share on Frontier parts
A missing input a bar needs FAILS that bar (never passes it by default); the panel
sd is the sample sd (n-1) over the models that ran the level.
Exit: 0 pass, 1 a bar failed, 2 unreadable input.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys

SCORE_MAX = 8.5
UNLOCK = 0.9
SHARE_LO, SHARE_HI, SD_MIN = 0.45, 0.85, 0.15
TOP_RUNG_MAX = 0.5
NOISE_FACTOR = 1.5
ORDER_FAMILIES_MIN = 4


def _share(fam: dict, model: str, level) -> float | None:
    v = ((fam.get("shares") or {}).get(model) or {}).get(str(level))
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def evaluate(d: dict) -> dict:
    """-> {'ok', 'bars': {n: {'ok', 'why'}}, 'reported': {...}}"""
    order = list(d.get("order") or [])
    fams = d.get("families") or {}
    bars: dict[int, dict] = {}

    # 1
    scores = d.get("scores") or {}
    names = sorted({k for m in scores.values() for k in (m or {})})
    bad, miss = [], not names
    for n in names:
        vals = [(m or {}).get(n) for m in scores.values() if isinstance((m or {}).get(n), (int, float))]
        best = max(vals) if vals else None
        if best is None or best >= SCORE_MAX:
            bad.append(f"{n} best {best}")
    bars[1] = {"ok": not bad and not miss,
               "why": "no scores given" if miss else ("; ".join(bad) or f"every best < {SCORE_MAX}")}

    # 2
    hits = [f"{m}:{p}={v}" for m, parts in (d.get("frontier") or {}).items()
            for p, v in (parts or {}).items() if isinstance(v, (int, float)) and v >= UNLOCK]
    bars[2] = {"ok": bool(d.get("frontier")) and not hits,
               "why": ("no frontier shares given" if not d.get("frontier")
                       else ("unlocked: " + ", ".join(hits) if hits else f"no share >= {UNLOCK}"))}

    # 3, 4, 5 per family
    why3, why5, ok3, ok5 = [], [], bool(fams), bool(fams)
    order_ok, sfw_n = 0, 0
    for name, fam in sorted(fams.items()):
        core, top = fam.get("core_level"), fam.get("top_level")
        at = {m: _share(fam, m, core) for m in (fam.get("shares") or {})}
        at = {m: v for m, v in at.items() if v is not None}
        if len(at) < 2:
            ok3 = False
            why3.append(f"{name}: fewer than 2 models at core level {core}")
        else:
            best, sd = max(at.values()), statistics.stdev(at.values())
            if not (SHARE_LO <= best <= SHARE_HI) or sd < SD_MIN:
                ok3 = False
                why3.append(f"{name}: best {best:.2f} sd {sd:.2f}")
        tops = [v for v in (_share(fam, m, top) for m in (fam.get("shares") or {})) if v is not None]
        if not tops or max(tops) >= TOP_RUNG_MAX:
            ok5 = False
            why5.append(f"{name}: top rung {'missing' if not tops else f'best {max(tops):.2f}'}")
        if fam.get("sfw", True):
            sfw_n += 1
            seq = [at.get(m) for m in order]
            if len(order) >= 2 and all(v is not None for v in seq) and \
                    all(a <= b for a, b in zip(seq, seq[1:])):
                order_ok += 1
    bars[3] = {"ok": ok3, "why": "; ".join(why3) or f"best in [{SHARE_LO},{SHARE_HI}], sd >= {SD_MIN}"}
    bars[4] = {"ok": order_ok >= ORDER_FAMILIES_MIN,
               "why": f"order holds on {order_ok} of {sfw_n} SFW families (need {ORDER_FAMILIES_MIN})"}
    bars[5] = {"ok": ok5, "why": "; ".join(why5) or f"every top-rung best < {TOP_RUNG_MAX}"}

    # 6
    nz = d.get("noise") or {}
    m_, a_ = nz.get("measured"), nz.get("assumed")
    if isinstance(m_, (int, float)) and isinstance(a_, (int, float)) and a_ > 0:
        bars[6] = {"ok": m_ <= NOISE_FACTOR * a_, "why": f"measured {m_} vs {NOISE_FACTOR} x {a_} = {NOISE_FACTOR * a_:.3f}"}
    else:
        bars[6] = {"ok": False, "why": "measured/assumed noise missing"}

    # 7 reported
    local = d.get("local") or []
    fr = d.get("frontier") or {}
    means = {m: statistics.fmean([v for v in (fr.get(m) or {}).values() if isinstance(v, (int, float))])
             for m in local if any(isinstance(v, (int, float)) for v in (fr.get(m) or {}).values())}
    top_local = max(means, key=means.get) if means else None
    reported = {"top_local": top_local, "mean_frontier_share": round(means[top_local], 3) if top_local else None}
    return {"ok": all(b["ok"] for b in bars.values()), "bars": {str(k): v for k, v in bars.items()},
            "reported": reported}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("pilot", help="pilot results JSON")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    try:
        with open(a.pilot, encoding="utf-8") as f:
            d = json.load(f)
        if not isinstance(d, dict):
            raise ValueError("top level must be an object")
    except (OSError, ValueError) as e:
        print(f"pilot_bars: cannot read {a.pilot}: {e}", file=sys.stderr)
        return 2
    rep = evaluate(d)
    if a.json:
        print(json.dumps(rep, indent=1))
    else:
        for k, b in rep["bars"].items():
            print(f"G-Headroom {k}: {'PASS' if b['ok'] else 'FAIL'}  {b['why']}")
        r = rep["reported"]
        print(f"G-Headroom 7 (reported): top local {r['top_local']} mean Frontier share {r['mean_frontier_share']}")
        print("G-Headroom: " + ("PASS" if rep["ok"] else "FAIL"))
    return 0 if rep["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
