"""Compare CrucibleForge judges on the same 3.4.0 chat rows (re-judge only).

usage: uv run python judge_compare.py name=dir [name=dir ...]
The first judge is the reference (122B). Each dir holds transcripts_<label>.jsonl.
Scores are computed with the project's own report code (board_filter +
model_stats + scorecard)."""
import itertools
import json
import math
import statistics as st
import sys
from pathlib import Path

from crucibleforge import config, report
from crucibleforge.config import load_config, set_results_dir

LABELS = ["deepseek-pro", "minimax-m3", "deepseek-flash", "joyfox-35b-rp", "precog-123b-v1a-q4"]
CONTESTANT_JUDGE = {"minimax": "minimax-m3", "dspro": "deepseek-pro"}
CHAT_COMPS = ["rp", "nsfw", "story", "explicit_peak", "willing", "steer"]
cfg = load_config(str(Path(__file__).resolve().parents[1] / "models.yaml"))
sc = report.scoring_config(cfg)

judges = [a.split("=", 1) for a in sys.argv[1:]]


def spearman(a, b):
    def ranks(x):
        order = sorted(range(len(x)), key=lambda i: x[i])
        r = [0.0] * len(x)
        i = 0
        while i < len(x):
            j = i
            while j + 1 < len(x) and x[order[j + 1]] == x[order[i]]:
                j += 1
            for k in range(i, j + 1):
                r[order[k]] = (i + j) / 2 + 1
            i = j + 1
        return r
    return pearson(ranks(a), ranks(b))


def pearson(a, b):
    if len(a) < 3:
        return float("nan")
    ma, mb = st.mean(a), st.mean(b)
    va = sum((x - ma) ** 2 for x in a)
    vb = sum((y - mb) ** 2 for y in b)
    if not va or not vb:
        return float("nan")
    return sum((x - ma) * (y - mb) for x, y in zip(a, b)) / math.sqrt(va * vb)


data = {}   # judge -> {"cards": {label: card}, "rows": {(label, case): scores}}
for name, d in judges:
    set_results_dir(Path(d))
    config.RESULTS_DIR = Path(d)
    cards, rows, meta = {}, {}, {"judge_models": set(), "failed": 0, "refused": 0, "n": 0}
    for lab in LABELS:
        rs = report.board_filter(lab, cfg)
        s = report.model_stats(lab, cfg, rs)
        cards[lab] = report.scorecard(s, sc)
        for r in rs:
            if not r.get("needs_judge"):
                continue
            meta["n"] += 1
            j = r.get("judge") or {}
            meta["judge_models"].add(r.get("judge_model"))
            if j.get("judge_failed"):
                meta["failed"] += 1
            if j.get("refused") or (j.get("scores") or {}).get("refused"):
                meta["refused"] += 1
            sc_ = {k: v for k, v in (j.get("scores") or {}).items()
                   if isinstance(v, (int, float)) and not isinstance(v, bool)}
            flags = {k: v for k, v in (j.get("scores") or {}).items() if isinstance(v, bool)}
            rows[(lab, r["case_id"])] = {"dims": sc_, "flags": flags}
    data[name] = {"cards": cards, "rows": rows, "meta": meta}

names = [n for n, _ in judges]
ref = names[0]
out = []
P = out.append

P("## Judge health")
for n in names:
    m = data[n]["meta"]
    P(f"- {n}: rows {m['n']}, judge_failed {m['failed']}, judge-flagged refused {m['refused']}, "
      f"judge_model {sorted(x for x in m['judge_models'] if x)}")

P("\n## Chat score per model (and rank)")
P("| model | " + " | ".join(names) + " |")
P("|---|" + "---|" * len(names))
chat = {n: [data[n]["cards"][l]["chat"] for l in LABELS] for n in names}
for i, l in enumerate(LABELS):
    cells = []
    for n in names:
        v = chat[n][i]
        rk = sorted(chat[n], reverse=True).index(v) + 1 if v is not None else "-"
        cells.append(f"{v:.1f} (#{rk})" if v is not None else "-")
    P(f"| {l} | " + " | ".join(cells) + " |")
P("| std across models | " + " | ".join(f"{st.pstdev(chat[n]):.1f}" for n in names) + " |")
P("| range | " + " | ".join(f"{max(chat[n]) - min(chat[n]):.1f}" for n in names) + " |")
P("| near-ties (<1.0 pt pairs) | " + " | ".join(
    str(sum(1 for a, b in itertools.combinations(chat[n], 2) if abs(a - b) < 1.0)) for n in names) + " |")

P("\n## Chat components (0-100) per judge")
for comp in CHAT_COMPS:
    P(f"\n**{comp}**  | " + " | ".join(names))
    for l in LABELS:
        vals = []
        for n in names:
            v = data[n]["cards"][l]["components"].get(comp)
            vals.append("-" if v is None else f"{v:.0f}")
        P(f"- {l}: " + " / ".join(vals))

# consensus = mean Chat of non-contestant judges other than the one evaluated
noncontest = [n for n in names if n not in CONTESTANT_JUDGE]
P("\n## Rank agreement (Spearman on Chat over 5 models)")
P("| judge | vs " + ref + " | vs leave-one-out consensus (non-contestant judges) | mean offset vs " + ref + " (Chat pts) |")
P("|---|---|---|---|")
for n in names:
    others = [m for m in noncontest if m != n] or [ref]
    cons = [st.mean(chat[m][i] for m in others) for i in range(len(LABELS))]
    off = st.mean(chat[n][i] - chat[ref][i] for i in range(len(LABELS)))
    P(f"| {n} | {spearman(chat[n], chat[ref]):.2f} | {spearman(chat[n], cons):.2f} "
      f"(MAE {st.mean(abs(chat[n][i]-cons[i]) for i in range(len(LABELS))):.1f}) | {off:+.1f} |")


def paired(a, b):
    xs, ys, per = [], [], {}
    for key, ra in data[a]["rows"].items():
        rb = data[b]["rows"].get(key)
        if not rb:
            continue
        for dim, va in ra["dims"].items():
            vb = rb["dims"].get(dim)
            if vb is None:
                continue
            xs.append(va); ys.append(vb)
            per.setdefault(dim, []).append((va, vb))
    return xs, ys, per


P("\n## Pairwise per-row agreement (all numeric dims pooled): Pearson r / mean abs diff / mean offset (row judge - col judge)")
P("| | " + " | ".join(names) + " |")
P("|---|" + "---|" * len(names))
for a in names:
    cells = []
    for b in names:
        if a == b:
            cells.append("—"); continue
        xs, ys, _ = paired(a, b)
        cells.append(f"{pearson(xs, ys):.2f} / {st.mean(abs(x-y) for x, y in zip(xs, ys)):.2f} / "
                     f"{st.mean(x-y for x, y in zip(xs, ys)):+.2f} (n={len(xs)})")
    P(f"| {a} | " + " | ".join(cells) + " |")

P(f"\n## Per-dimension vs {ref}: r / MAD / offset")
dims = sorted({d for r in data[ref]["rows"].values() for d in r["dims"]})
P("| dim | " + " | ".join(n for n in names if n != ref) + " |")
P("|---|" + "---|" * (len(names) - 1))
for dim in dims:
    cells = []
    for n in names:
        if n == ref:
            continue
        _, _, per = paired(n, ref)
        pr = per.get(dim, [])
        if len(pr) < 3:
            cells.append("-"); continue
        xs, ys = [p[0] for p in pr], [p[1] for p in pr]
        cells.append(f"{pearson(xs, ys):.2f} / {st.mean(abs(x-y) for x, y in pr):.1f} / "
                     f"{st.mean(x-y for x, y in pr):+.1f} (n={len(pr)})")
    P(f"| {dim} | " + " | ".join(cells) + " |")

P("\n## Identity dim on RPS1 / RPS2 / NMX1 (judge scores) + deterministic identity checks")
P("| model/case | " + " | ".join(names) + " | det. identity pass |")
P("|---|" + "---|" * len(names) + "---|")
set_results_dir(Path(judges[0][1])); config.RESULTS_DIR = Path(judges[0][1])
det = {}
for l in LABELS:
    for r in report.board_filter(l, cfg):
        if r["case_id"].split("-")[0] in ("RPS1", "RPS2", "NMX1"):
            g = ((r.get("checks") or {}).get("groups") or {}).get("identity")
            det[(l, r["case_id"])] = f"{g[0]}/{g[1]}" if g else "-"
for key in sorted(det):
    cells = [str(data[n]["rows"].get(key, {}).get("dims", {}).get("identity", "-")) for n in names]
    P(f"| {key[0]} {key[1].split('-')[0]} | " + " | ".join(cells) + f" | {det[key]} |")

P("\n## Self-preference / family bias: judge's Chat for model minus that judge's mean for the other 4, "
  "compared with the same quantity under the non-contestant judges")
for target in ["minimax-m3", "deepseek-pro", "joyfox-35b-rp", "precog-123b-v1a-q4", "deepseek-flash"]:
    i = LABELS.index(target)
    rel = {n: chat[n][i] - st.mean(chat[n][k] for k in range(len(LABELS)) if k != i) for n in names}
    P(f"- {target}: " + ", ".join(f"{n} {rel[n]:+.1f}" for n in names))

print("\n".join(out))
json.dump({n: {"chat": dict(zip(LABELS, chat[n])),
               "components": {l: {c: data[n]["cards"][l]["components"].get(c) for c in CHAT_COMPS} for l in LABELS}}
           for n in names}, open(Path(__file__).parent / "judge_compare.json", "w"), indent=1)

# ---- discrimination: does the judge tell the 5 models apart on the SAME case?
print("\n## Discrimination per case (mean over dims of the std across the 5 models; higher = separates more) and identical-verdict cases")
cases = sorted({k[1] for n in names for k in data[n]["rows"]})
print("| judge | mean within-case std | cases where >=3 models got the identical dim vector |")
print("|---|---|---|")
for n in names:
    stds, ident = [], []
    for c in cases:
        vecs = [data[n]["rows"].get((l, c), {}).get("dims") for l in LABELS]
        vecs = [v for v in vecs if v]
        dims_ = sorted(set().union(*[set(v) for v in vecs])) if vecs else []
        for d in dims_:
            vals = [v[d] for v in vecs if d in v]
            if len(vals) >= 3:
                stds.append(st.pstdev(vals))
        tup = [tuple(sorted(v.items())) for v in vecs]
        if tup and max(tup.count(t) for t in tup) >= 3:
            ident.append(c.split("-")[0])
    print(f"| {n} | {st.mean(stds):.2f} | {len(ident)} {ident} |")

# ---- per-row agreement with the leave-one-out consensus of the non-contestant judges
print("\n## Per-row agreement with the leave-one-out consensus (mean of the OTHER non-contestant judges, repeat runs excluded)")
print("| judge | r | MAD | offset | MAD after removing offset |")
print("|---|---|---|---|---|")
base = [n for n in noncontest if not n.endswith("2")]
for n in names:
    others = [m for m in base if m != n and m.rstrip("2") != n.rstrip("2")]
    xs, ys = [], []
    for key, ra in data[n]["rows"].items():
        for dim, va in ra["dims"].items():
            vs = [data[m]["rows"].get(key, {}).get("dims", {}).get(dim) for m in others]
            vs = [v for v in vs if v is not None]
            if len(vs) == len(others) and vs:
                xs.append(va); ys.append(st.mean(vs))
    off = st.mean(x - y for x, y in zip(xs, ys))
    print(f"| {n} (vs {'+'.join(others)}) | {pearson(xs, ys):.2f} | {st.mean(abs(x-y) for x, y in zip(xs, ys)):.2f} | "
          f"{off:+.2f} | {st.mean(abs(x-y-off) for x, y in zip(xs, ys)):.2f} |")
