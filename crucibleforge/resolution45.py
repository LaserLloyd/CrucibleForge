"""Run-to-run spread of the 4.5 board, on the board's own scale (INTENT-45: run noise is measured per model).

Inputs are replicate result folders: each folder holds ONE whole run of ONE model (``transcripts_<label>.jsonl``) and
its rendered ``report.json``; several folders with the same label are that model's replicates. Each folder's board
points (Code, Chat, Overall, NSFW, Uncensored, NSFW+ /20, every segment and the pack points) are read back from its
report (:func:`folder_points`): nothing is rescored and nothing is drawn, so this takes seconds. Then:

- per model and key: n runs (counted per key), mean, SD and min-max;
- the pooled SD = sqrt(sum (n_i - 1) s_i^2 / sum (n_i - 1)) over the models with at least two runs of the key, with
  its degrees of freedom (sum (n_i - 1)).

The board (report.py) shows a replicated model at the mean of its runs and prints each model's range from its own
runs (1.645 x SD / sqrt(n)), else from the pooled SD, labelled. :func:`resolve` / :func:`reference_share` (the old
pairwise separation) stay as library functions; INTENT-45 builds no check that tells models apart, and the release
path does not call them.
"""
from __future__ import annotations

import math
import random

from .accuracy45 import t90 as _t90

LEVEL = 0.90


def sd(xs) -> float | None:
    xs = [x for x in xs if x is not None]
    if len(xs) < 2:
        return None
    m = sum(xs) / len(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1))


def quantile(xs: list[float], q: float) -> float:
    v = sorted(xs)
    pos = q * (len(v) - 1)
    i = int(math.floor(pos))
    j = min(i + 1, len(v) - 1)
    return v[i] + (v[j] - v[i]) * (pos - i)


def run_to_run(points: dict[str, list[dict]]) -> dict:
    """{label: [ {key: point} per run ]} -> the precision45.json ``run_to_run`` block."""
    by_model, runs, means, ranges = {}, {}, {}, {}
    num: dict[str, float] = {}
    den: dict[str, int] = {}
    models_with: set = set()
    counts: dict = {}
    by_key: dict[str, list] = {}
    for label, rs in points.items():
        runs[label] = len(rs)
        keys = sorted({k for r in rs for k, v in r.items() if v is not None})
        by_model[label] = {}
        means[label], ranges[label], counts[label] = {}, {}, {}
        for k in keys:
            xs = [r.get(k) for r in rs if r.get(k) is not None]
            # runs are counted per key: a folder without the part (no adult rows, a pending segment) is no run of it
            counts[label][k] = len(xs)
            if xs:
                # the board shows a replicated model at the mean of its runs
                means[label][k], ranges[label][k] = sum(xs) / len(xs), [min(xs), max(xs)]
            s = sd(xs)
            by_model[label][k] = s
            if s is not None:
                num[k] = num.get(k, 0.0) + (len(xs) - 1) * s * s
                den[k] = den.get(k, 0) + len(xs) - 1
                by_key.setdefault(k, []).append(label)
                models_with.add(label)
    pooled = {k: math.sqrt(num[k] / den[k]) for k in num if den.get(k)}
    return {"by_model": by_model, "runs": runs, "n": counts, "pooled": pooled, "pooled_models": len(models_with),
            "pooled_df": {k: den[k] for k in pooled}, "pooled_by": {k: sorted(by_key[k]) for k in pooled},
            "means": means, "ranges": ranges}


def pair_noise(a: list[list[float]], b: list[list[float]], rnd: random.Random) -> list[float]:
    """D_i - mean(D) with a run of each model drawn per resample (runs resampled; draw i shared)."""
    n = min(min(len(x) for x in a), min(len(x) for x in b))
    d = []
    for i in range(n):
        ra, rb = rnd.randrange(len(a)), rnd.randrange(len(b))
        xa, xb = a[ra][i], b[rb][i]
        if xa is not None and xb is not None:
            d.append(xa - xb)
    if not d:
        return []
    m = sum(d) / len(d)
    return [x - m for x in d]


def g90(noise: list[float], level: float = LEVEL) -> float | None:
    if len(noise) < 10:
        return None
    a = (1 - level) / 2
    h = (quantile(noise, 1 - a) - quantile(noise, a)) / 2
    return h - quantile(noise, 0.10)


def resolve(samples: dict[str, list[dict]], keys: list[str], seed: int = 20261119, level: float = LEVEL) -> dict:
    """{label: [ {key: [sample per resample]} per run ]} -> {key: {median, max, pairs: {a/b: g90}}}."""
    rnd = random.Random(seed)
    labels = sorted(samples)
    out = {}
    for k in keys:
        pairs = {}
        for i, a in enumerate(labels):
            for b in labels[i + 1:]:
                sa = [r.get(k) or [] for r in samples[a]]
                sb = [r.get(k) or [] for r in samples[b]]
                if not all(sa) or not all(sb):
                    continue
                g = g90(pair_noise(sa, sb, rnd), level)
                if g is not None:
                    pairs[f"{a}/{b}"] = g
        vals = sorted(pairs.values())
        out[k] = {"median": quantile(vals, 0.5) if vals else None, "max": vals[-1] if vals else None,
                  "pairs": pairs}
    return out


def reference_share(points: dict[str, list[dict]], ref: str | None, maxima: dict[str, float],
                    measured: dict[str, float] | None = None) -> dict:
    """{key: the reference's mean point over its runs / the key's measured maximum (the maximum when every part
    was measured; a pending part adds nothing to the points, so it must not add to the denominator either)}."""
    if not ref or ref not in points:
        return {}
    out = {}
    for k, mx in maxima.items():
        mx = (measured or {}).get(k) or mx
        xs = [r.get(k) for r in points[ref] if r.get(k) is not None]
        if xs and mx:
            out[k] = sum(xs) / len(xs) / mx
    return out


def render(res: dict, share: dict, rtr: dict, ref: str | None, measured: dict | None = None,
           maxima: dict | None = None) -> str:
    L = ["Smallest gap the board resolves 90% of the time (paired: cases, then repeats, then runs; judge term in)",
         f"{'key':24} {'g90 median':>10} {'g90 max':>8} {'run SD pooled':>13} {'measured':>10}  reference share"]
    short = []
    for k, v in res.items():
        f = lambda x: "-" if x is None else f"{x:.2f}"     # noqa: E731
        sh = share.get(k)
        mm, mx = (measured or {}).get(k), (maxima or {}).get(k)
        cov = "-" if mm is None or mx is None else f"{mm:g} of {mx:g}"
        if mm is not None and mx is not None and mm < mx - 1e-9:
            short.append(f"{k} ({cov})")
        L.append(f"{k:24} {f(v['median']):>10} {f(v['max']):>8} {f((rtr.get('pooled') or {}).get(k)):>13} "
                 f"{cov:>10}  " + ("-" if sh is None else f"{100 * sh:.0f}%"))
    L.append(f"runs per model: {rtr.get('runs')}; reference: {ref or '-'}")
    if short:
        L.append("NOTE: measured on part of the maximum (pending parts add nothing, so their gaps are not in these "
                 "figures): " + ", ".join(short))
    return "\n".join(L)


# ------------------------------------------------------------------ folders: the board's own scale, read back
#: the board-scale keys of a whole run (``precision45.json`` "scale": "board45"): sections, NSFW+ and its halves,
#: every segment (``code/<seg>``, ``chat/<seg>``, ``nsfw/<seg>``, ``unc/<seg>``) and the pack points
BOARD_SCALE = "board45"
SECTION_KEYS = ("code", "chat", "overall", "nsfw", "uncensored", "nsfw_plus")


def _pt(x):
    try:
        return None if x is None else float(x)
    except (TypeError, ValueError):
        return None


def report_points(model: dict) -> dict:
    """{board-scale key: points} of ONE run from its rendered ``report.json`` model entry (``measured`` block): a
    section or segment counts only when it is complete (no pending points), so a part that was not measured in a run
    never counts as a run of that part. Nothing is rescored and nothing is drawn."""
    m = (model or {}).get("measured") or {}
    out: dict = {}
    for sec in ("code", "chat"):
        b = m.get(sec) or {}
        for sk, g in (b.get("segments") or {}).items():
            if g.get("points") is not None and not g.get("pending_points"):
                out[f"{sec}/{sk}"] = _pt(g["points"])
        if b.get("points") is not None and not b.get("pending_points"):
            out[sec] = _pt(b["points"])
            out[f"{sec}_packs"] = _pt(b.get("pack_points")) or 0.0
    ov = m.get("overall") or {}
    if ov.get("points") is not None:
        out["overall"] = _pt(ov["points"])
    npb = m.get("nsfw_plus") or {}
    for half, pre in (("nsfw", "nsfw"), ("uncensored", "unc")):
        h = npb.get(half) or {}
        for sk, g in (h.get("segments") or {}).items():
            if g.get("points") is not None and g.get("pending") is None:
                out[f"{pre}/{sk}"] = _pt(g["points"])
        if h.get("points") is not None and h.get("pending") is None:
            out[half] = _pt(h["points"])
    if out.get("nsfw") is not None and out.get("uncensored") is not None:
        out["nsfw_plus"] = out["nsfw"] + out["uncensored"]
    pk = ((model or {}).get("nsfw_plus") or {}).get("packs45") or {}
    if out.get("nsfw_plus") is not None and pk.get("board") is not None:
        out["nsfw_packs"] = _pt(pk.get("points")) or 0.0
    return {k: v for k, v in out.items() if v is not None}


def folder_points(folder) -> tuple[str, dict]:
    """(label, {board-scale key: points}) of one replicate folder, read from its rendered ``report.json``."""
    import json
    from pathlib import Path
    folder = Path(folder)
    files = sorted(folder.glob("transcripts_*.jsonl"))
    if len(files) != 1:
        raise ValueError(f"resolution45: {folder} must hold exactly one transcripts_<label>.jsonl")
    label = files[0].name[len("transcripts_"):-len(".jsonl")]
    try:
        body = json.loads((folder / "report.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise ValueError(f"resolution45: {folder}/report.json is missing or unreadable ({e}); render the folder "
                         "report first") from None
    model = (body.get("models") or {}).get(label)
    if not isinstance(model, dict):
        raise ValueError(f"resolution45: {folder}/report.json holds no model {label!r}")
    return label, report_points(model)


def precision_file(points: dict[str, list[dict]], folders: list[str]) -> dict:
    """The board's ``precision45.json`` body (board scale): the run-to-run block and the folders it was read from."""
    return {"scale": BOARD_SCALE, "run_to_run": run_to_run(points), "folders": [str(f) for f in folders]}


def render_precision(rtr: dict) -> str:
    """Per model and key: n runs, mean, SD, min-max; then the pooled SD with its degrees of freedom."""
    keys = [k for k in SECTION_KEYS] + sorted({k for m in (rtr.get("n") or {}).values() for k in m
                                               if "/" in k or k.endswith("_packs")})
    L = ["Run-to-run spread on the board's own scale (replicate folders; nothing rescored)",
         f"{'model':34} {'key':22} {'n':>2} {'mean':>8} {'SD':>7} {'min-max':>15}"]
    f = lambda x: "-" if x is None else f"{x:.3f}"     # noqa: E731
    for label in sorted(rtr.get("n") or {}):
        for k in keys:
            n = ((rtr.get("n") or {}).get(label) or {}).get(k)
            if not n:
                continue
            mn = ((rtr.get("means") or {}).get(label) or {}).get(k)
            rg = ((rtr.get("ranges") or {}).get(label) or {}).get(k) or [None, None]
            sd_ = ((rtr.get("by_model") or {}).get(label) or {}).get(k)
            L.append(f"{label[:34]:34} {k:22} {n:>2} {f(mn):>8} {f(sd_):>7} {f(rg[0]) + '-' + f(rg[1]):>15}")
    L.append("pooled SD (degrees of freedom; models with two or more runs):")
    for k in keys:
        if k in (rtr.get("pooled") or {}):
            L.append(f"  {k:22} {rtr['pooled'][k]:.3f} ({rtr['pooled_df'][k]} df; "
                     f"{', '.join(rtr['pooled_by'][k])}); 90% half of one run {_t90(rtr['pooled_df'][k]) * rtr['pooled'][k]:.2f}")
    return "\n".join(L)
