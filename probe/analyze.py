#!/usr/bin/env python3
"""S2 probe analysis. Usage (from the worktree, via uv):
  uv run python probe/analyze.py [--results DIR] [--out FILE] [--api-rows transcripts.jsonl ...] [--template]
Scores every probe row with the family's own grader (re-materialised under release_seed 4.5.0), then
writes per family x level x model: mean score, answer tokens (median, per case = sum over turns), wall s,
slope per family, a suggested level where Fable is in 0.6-0.8, and the "does not scale" flags."""
import argparse, json, statistics as st, sys, collections, pathlib, re, datetime

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))  # --worktree (below) goes in front
MODELS = ["probe-haiku", "probe-sonnet", "probe-fable"]
NAMES = {"probe-haiku": "haiku-5-5", "probe-sonnet": "sonnet-5-5", "probe-fable": "fable-5-2"}
DEFAULT_OUT = str(ROOT / "S2-PROBE.md")   # pass --out to write it elsewhere
CASE = re.compile(r"^PRB-(?P<fam>.+)-L(?P<lv>\d+)-(?P<seed>p\d+)$")
BAND = (0.6, 0.8)
CAP = 24576          # the API board caps thinking + answer per call at this many tokens
ACCEPT_BAND = (0.45, 0.85)   # Fable's share at the chosen Core level


def row_tokens(r):
    m = r.get("metrics") or {}
    return (m.get("completion_tokens") or 0) + (m.get("reasoning_tokens") or 0)


def over_cap(rows, cap=CAP):
    """number of rows whose output (+ thinking, if reported separately) tokens exceed the cap. Accepts transcript
    rows or bare metrics dicts."""
    n = 0
    for r in rows:
        m = r.get("metrics", r)
        if (m.get("completion_tokens") or 0) + (m.get("reasoning_tokens") or 0) > cap:
            n += 1
    return n


def core_level(fable_pts, override):
    """the Core level the acceptance is judged at: the caller's pick, else Fable's top probed level"""
    if override is not None:
        return override
    lv = sorted(fable_pts.get("probe-fable", {}))
    return lv[-1] if lv else None


def accept(means, verdict, level, tol=0.0):
    """means = {model: {level: mean score}}. PASS needs: verdict not DOES NOT SCALE / INCOMPLETE, Fable in 0.45-0.85
    at `level`, haiku <= sonnet <= fable there (within tol). Returns (ok, reason)."""
    if verdict.startswith(("DOES NOT SCALE", "INCOMPLETE")):
        return False, verdict.split(":")[0]
    h, s, f = (means.get(m, {}).get(level) for m in MODELS)
    if None in (h, s, f):
        return False, f"missing scores at L{level}"
    why = []
    if not ACCEPT_BAND[0] <= f <= ACCEPT_BAND[1]:
        why.append(f"Fable {f:.2f} outside {ACCEPT_BAND[0]}-{ACCEPT_BAND[1]} at L{level}")
    if not (h <= s + tol and s <= f + tol):
        why.append(f"order broken at L{level} (haiku {h:.2f}, sonnet {s:.2f}, fable {f:.2f})")
    return (not why), "; ".join(why) or f"Fable {f:.2f} at L{level}, haiku <= sonnet <= fable"


def exit_code(results, required):
    """1 if any named family failed or has no data; else 0"""
    return 1 if any(not results.get(f, (False, ""))[0] for f in required) else 0


def load_rows(results):
    rows = []
    for m in MODELS:
        f = pathlib.Path(results) / f"transcripts_{m}.jsonl"
        if f.exists():
            rows += [json.loads(l) for l in f.read_text().splitlines() if l.strip()]
    return rows


def score_cases(rows):
    """{(model, case_id): dict(score, tokens, wall, turns)} for probe cases + CL3/CC4 records."""
    from crucibleforge import config, gen, profiles
    from crucibleforge.gen import _stack_common as sc
    prof = profiles.load_profile("probe45", None)
    profiles.activate_release_seed(prof)
    cases = {c["id"]: c for c in config.load_cases() if c["id"].startswith("PRB-")}
    by = collections.defaultdict(list)
    for r in rows:
        by[(r["model_label"], r["case_id"])].append(r)
    out = {}
    for (m, cid), rs in by.items():
        rs.sort(key=lambda r: (r.get("turn") or 0))
        mt = [r.get("metrics") or {} for r in rs]
        rec = {"tokens": sum(x.get("completion_tokens") or 0 for x in mt),
               "wall": sum(x.get("total_s") or 0 for x in mt), "turns": len(rs),
               "finish": rs[-1].get("finish_reason"), "score": None,
               "over": over_cap(rs), "maxrow": max(row_tokens(r) for r in rs)}
        if cid in cases:
            c = cases[cid]
            fam = gen.get(c["generator"]["type"])
            ans = sc.join_turns([r.get("response") or "" for r in rs]) if c.get("turns") else (rs[-1].get("response") or "")
            try:
                rec["score"] = float(fam.grade(ans, c))
            except Exception as e:  # noqa: BLE001
                rec["error"] = f"{type(e).__name__}: {e}"
        elif cid.startswith(("CL3", "CL7")):
            els = (rs[-1].get("elements") or {}).get("results") or []
            rec["score"] = (sum(1 for e in els if e.get("pass")) / len(els)) if els else (1.0 if rs[-1].get("grade") == "pass" else 0.0)
        else:
            rec["answer"] = rs[-1].get("response")
        out[(m, cid)] = rec
    return out


def slope(pts):
    """least-squares slope of score vs level over [(level, mean)]; None for < 2 points"""
    if len(pts) < 2:
        return None
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    mx, my = st.mean(xs), st.mean(ys)
    d = sum((x - mx) ** 2 for x in xs)
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / d if d else None


def suggest(pts):
    """level where the model's mean is 0.6-0.8: a probed level in the band, else a linear extrapolation"""
    inb = [lv for lv, s in pts if BAND[0] <= s <= BAND[1]]
    if inb:
        return f"L{inb[0]} (probed, in band)"
    s = slope(pts)
    if s is None or abs(s) < 1e-6:
        return "no trend: flat at %.2f" % (pts[-1][1] if pts else float('nan'))
    mx, my = st.mean(p[0] for p in pts), st.mean(p[1] for p in pts)
    lv = mx + (0.7 - my) / s
    if s > 0:
        return f"~L{lv:.1f} (extrapolated; scores rise with level, check the generator)"
    return f"~L{lv:.1f} (extrapolated, outside the probed range)" if not (pts[0][0] <= lv <= pts[-1][0]) else f"~L{lv:.1f} (interpolated)"


def build(results, api_rows, core=None, tol=0.0):
    rows = load_rows(results)
    sc_ = score_cases(rows)
    fams = collections.OrderedDict()
    for (m, cid), rec in sc_.items():
        mm = CASE.match(cid)
        if mm:
            fams.setdefault(mm["fam"], {}).setdefault(int(mm["lv"]), {}).setdefault(m, []).append(rec)
    L = [f"# S2 probe: Claude Code on existing levels", "",
         f"Generated {datetime.datetime.now():%Y-%m-%d %H:%M} from `{results}` ({len(rows)} rows). "
         "Score = the family's own grader, 0-1, mean over seeds p1,p2 (release_seed 4.5.0). "
         "Tokens = median answer tokens per case (sum over turns; thinking counted if the CLI reports it in output tokens). "
         "Wall = median seconds per case (sum over turns). Rows are `claude -p` runs on the subscription, `--effort` as logged; "
         "not comparable to API rows.", ""]
    verdicts, results = [], {}
    for fam, lvls in fams.items():
        L += [f"## {fam}", "", "| level | " + " | ".join(f"{NAMES[m]} score / tok / s (n)" for m in MODELS)
              + " | would truncate at 24,576 |", "|---|" + "---|" * (len(MODELS) + 1)]
        pts = {m: [] for m in MODELS}
        for lv in sorted(lvls):
            cells, trunc = [], []
            for m in MODELS:
                allr = lvls[lv].get(m, [])
                if any(r["over"] for r in allr):
                    trunc.append(f"{NAMES[m].split('-')[0]} {sum(1 for r in allr if r['over'])}/{len(allr)}")
                rs = [r for r in allr if r["score"] is not None]
                if not rs:
                    cells.append("n/a")
                    continue
                mean = st.mean(r["score"] for r in rs)
                pts[m].append((lv, mean))
                cells.append(f"{mean:.2f} / {int(st.median(r['tokens'] for r in rs))} / {st.median(r['wall'] for r in rs):.0f} ({len(rs)})")
            L.append(f"| L{lv} | " + " | ".join(cells) + f" | {', '.join(trunc) or '-'} |")
        L.append("")
        sl = {m: slope(pts[m]) for m in MODELS}
        L.append("Slope per level step: " + ", ".join(f"{NAMES[m]} {sl[m]:+.3f}" if sl[m] is not None else f"{NAMES[m]} n/a" for m in MODELS))
        fp = pts["probe-fable"]
        L.append(f"Suggested level for Fable in 0.6-0.8: {suggest(fp) if fp else 'n/a (no Fable rows)'}")
        flags = []
        if fp and fp[-1][1] >= 0.95:
            flags.append(f"Fable {fp[-1][1]:.2f} at top probed level L{fp[-1][0]}")
        hp = dict(pts["probe-haiku"])
        if fp and hp and all(abs(hp.get(lv, -9) - s) <= 0.05 for lv, s in fp):
            flags.append("haiku ~ fable at every probed level")
        if not fp:
            v = "INCOMPLETE (no Fable rows)"
        elif flags:
            v = "DOES NOT SCALE (redesign): " + "; ".join(flags)
        else:
            v = "scales (Fable below 0.95 at the top level, haiku separated)"
        L += [f"Verdict: **{v}**", ""]
        cl = core_level({"probe-fable": dict(fp)}, (core or {}).get(fam))
        means = {m: dict(pts[m]) for m in MODELS}
        ok, why = accept(means, v, cl, tol) if cl is not None else (False, "no Fable rows")
        results[fam] = (ok, why)
        L.append(f"Acceptance (Core level {"L%s" % cl if cl is not None else "n/a"}): **{'PASS' if ok else 'FAIL'}** - {why}")
        L.append("")
        verdicts.append((fam, v, suggest(fp) if fp else "n/a", ok, why))
    # parity
    L += ["## Harness parity (CL3, CC4)", ""]
    for m in MODELS:
        for cid in ("CL3-paragraph-wrap", "CL7-span-by-example", "CB-C6-predict-node", "CC4-second-pitch"):
            r = sc_.get((m, cid))
            if r:
                if cid.startswith(("CL3", "CL7")):
                    L.append(f"- {NAMES[m]} {cid}: score {r['score']:.2f}, {r['tokens']} tok, {r['wall']:.0f} s")
                else:
                    L.append(f"- {NAMES[m]} {cid}: {r['turns']} turns, {r['tokens']} tok, {r['wall']:.0f} s (judged case, answers recorded only; see transcripts)")
    for f in api_rows or []:
        L.append(f"- stored API rows to compare: `{f}` (compare CL3 grade by model by hand or extend this block)")
    L += ["", "## Summary", "", "| family | verdict | Fable level target | acceptance |", "|---|---|---|---|"]
    L += [f"| {f} | {v} | {t} | {'PASS' if ok else 'FAIL'}: {why} |" for f, v, t, ok, why in verdicts]
    return "\n".join(L) + "\n", results


TEMPLATE = """# S2 probe: Claude Code on existing levels

Not run yet. The probe runs from the timer `mb45-probe` (see build/s2-probe-prep.md); the morning step is:

    cd /var/tmp/mb45-build/wt-probe && CRUCIBLEFORGE_CONFIG=$PWD/probe/models.yaml \\
      uv run python probe/analyze.py --results /var/tmp/mb45-build/probe-results

This file is then overwritten with, per family x level x model: mean score, median answer tokens, wall s; slope per
family; the level where Fable is 0.6-0.8; and the flags `does not scale` (Fable >= 0.95 at the top probed level, or haiku ~ fable
at every level).
"""

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", default="/var/tmp/mb45-build/probe-results")
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--api-rows", nargs="*")
    ap.add_argument("--template", action="store_true")
    ap.add_argument("--worktree", help="checkout whose crucibleforge + generators grade the rows (default: this one)")
    ap.add_argument("--require", nargs="*", default=[], help="families that must PASS acceptance; exit 1 otherwise")
    ap.add_argument("--core", nargs="*", default=[], help="fam=LEVEL: the Core level to judge (default: top probed)")
    ap.add_argument("--tol", type=float, default=0.0, help="slack on haiku<=sonnet<=fable")
    a = ap.parse_args()
    if a.worktree:
        sys.path.insert(0, a.worktree)
    core = {k: int(v) for k, v in (x.split("=") for x in a.core)}
    results = {}
    if a.template:
        txt = TEMPLATE
    else:
        txt, results = build(a.results, a.api_rows, core, a.tol)
    pathlib.Path(a.out).write_text(txt)
    print("wrote", a.out)
    for f, (ok, why) in results.items():
        print(f"ACCEPT {f}: {'PASS' if ok else 'FAIL'} - {why}")
    sys.exit(exit_code(results, a.require))
