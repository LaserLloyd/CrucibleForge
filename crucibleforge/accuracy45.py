"""Each model's own accuracy range (INTENT-45, owner 2026-11-21; SPEC-BUILD-45 item 9).

The bar is each model's own accuracy: the true score is the model's long-run average on the frozen test, and a
printed score should sit within ``intent.accuracy_target_pct`` (2.5) of the score's maximum of it, never more than
``intent.accuracy_limit_pct`` (5). Nothing here compares models.

The range printed beside a score is the 90% half-width of that score from the model's OWN stored repeats in its own
run (run noise): every unit with two or more repeats adds w^2 x s^2 / r (w = the unit's points, s^2 = the sample
variance of its repeats, r = its repeats; for a part of the headline the same term comes from the engine's own
bootstrap with every case kept once and only its repeats redrawn, ``report.repeat_noise45``). Units are independent,
so a segment adds its units' variances, a section its segments', Overall = Code + Chat and NSFW+ = NSFW + Uncensored.

A unit with one repeat has no run noise this run can measure: the output says so, and the stored cross-run figure
(``calibration/run-noise-45.json``, the 3-run replicate table of SUITE-EFFICIENCY-45) is used for its segment, as a
labelled estimate. A score whose range is above the limit prints "range x% (above the 5% limit)"; nothing is
withheld. The engine's item bootstrap margin stays as a second figure (:data:`ITEM_MARGIN_LABEL`).
"""
from __future__ import annotations

import json
import math
from pathlib import Path

Z90 = 1.645
#: one-sided 95% Student t by degrees of freedom: the 90% multiplier for a spread estimated from few runs
#: (1.645 only once the spread is known from many). Step 8, 2026-11-24.
_T90 = (6.314, 2.920, 2.353, 2.132, 2.015, 1.943, 1.895, 1.860, 1.833, 1.812, 1.796, 1.782, 1.771, 1.761, 1.753,
        1.746, 1.740, 1.734, 1.729, 1.725, 1.721, 1.717, 1.714, 1.711, 1.708, 1.706, 1.703, 1.701, 1.699, 1.697)
#: a model's own spread is used from this many runs on; below it the spread is pooled over every model's repeats
OWN_MIN_RUNS = 5


def t90(df) -> float:
    """The 90% multiplier for a standard deviation estimated with ``df`` degrees of freedom (Z90 when unknown)."""
    try:
        df = int(df)
    except (TypeError, ValueError):
        return Z90
    if df < 1:
        return Z90
    return _T90[df - 1] if df <= len(_T90) else Z90
ROOT = Path(__file__).resolve().parents[1]
ESTIMATES_FILE = ROOT / "calibration" / "run-noise-45.json"
ITEM_MARGIN_LABEL = "how far a different question set could move this (item sampling)"
SINGLE_NOTE = "single repeat: run noise not measurable from this run"
DEFAULTS = {"target_pct": 2.5, "limit_pct": 5.0}
SECTION_MAX = {"code": 10.0, "chat": 10.0, "overall": 20.0, "nsfw": 10.0, "uncensored": 10.0, "nsfw_plus": 20.0}


def settings(prof: dict | None) -> dict:
    """{target_pct, limit_pct} from the profile's ``intent`` block (defaults 2.5 / 5)."""
    it = (prof or {}).get("intent") or {}
    return {"target_pct": float(it.get("accuracy_target_pct", DEFAULTS["target_pct"])),
            "limit_pct": float(it.get("accuracy_limit_pct", DEFAULTS["limit_pct"]))}


def load_estimates(path: Path | str | None = None) -> dict:
    """{score or segment key: stored cross-run 90% half-width, % of max}; {} when the file is absent."""
    try:
        body = json.loads(Path(path or ESTIMATES_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {k: float(v) for k, v in (body.get("r1_pct") or {}).items() if v is not None}


def _var(xs: list[float]) -> float:
    m = sum(xs) / len(xs)
    return sum((x - m) ** 2 for x in xs) / (len(xs) - 1)


def repeat_var(groups) -> float | None:
    """sum of w^2 x s^2 / r over the groups (w, [repeat values]) with r >= 2; None when no group has two repeats."""
    terms = [float(w) ** 2 * _var([float(x) for x in xs]) / len(xs) for w, xs in groups if len(xs) >= 2]
    return sum(terms) if terms else None


def repeat_half(groups) -> float | None:
    v = repeat_var(groups)
    return None if v is None else Z90 * math.sqrt(v)


def block(half: float | None, mx: float, source: str | None, st: dict, note: str | None = None,
          basis: str | None = None, n: int | None = None) -> dict:
    """One range. ``basis`` (printed after the percentage) says where the figure comes from and on how many runs."""
    pct = None if half is None or not mx else 100.0 * float(half) / float(mx)
    return {"half": None if half is None else round(float(half), 4), "max": mx,
            "pct": None if pct is None else round(pct, 2), "source": source,
            "above_target": pct is not None and pct > st["target_pct"] + 1e-9,
            "above_limit": pct is not None and pct > st["limit_pct"] + 1e-9,
            "limit_pct": st["limit_pct"], **({"note": note} if note else {}),
            **({"basis": basis} if basis else {}), **({"n": n} if n is not None else {})}


def range_text(b: dict | None) -> str:
    """``±0.30 (3.0% of max)``; ``±0.40 estimate (4.0% of max)``; ``±0.60 (range 6.0% (above the 5% limit))``;
    ``range not measured`` when there is none."""
    if not b or b.get("half") is None:
        return "range not measured" + (f" ({b['note']})" if b and b.get("note") else "")
    est = " estimate" if b.get("source") == "estimate" else ""
    basis = f"; {b['basis']}" if b.get("basis") else ""
    if b.get("above_limit"):
        return f"±{b['half']:.2f}{est} (range {b['pct']:.1f}% (above the {b['limit_pct']:g}% limit){basis})"
    return f"±{b['half']:.2f}{est} ({b['pct']:.1f}% of max{basis})"


def _combine(parts: list[dict], mx: float, st: dict) -> dict:
    got = [p for p in parts if p.get("half") is not None]
    if not got:
        notes = sorted({p["note"] for p in parts if p.get("note")})
        return block(None, mx, None, st, "; ".join(notes) or None)
    half = math.sqrt(sum(p["half"] ** 2 for p in got))
    srcs = {p["source"] for p in got}
    src = "repeats" if srcs == {"repeats"} else "estimate" if srcs == {"estimate"} else "repeats+estimate"
    miss = [p for p in parts if p.get("half") is None]
    basis = None
    if any(p.get("basis") for p in got):
        basis = {"repeats": "this run's repeats", "estimate": "stored estimates"}.get(
            src, "this run's repeats and stored estimates") + ", parts added in quadrature"
    return block(half, mx, src, st, f"{len(miss)} part(s) with no range" if miss else None, basis=basis)


def _segment(model: dict, sec: str, key: str, g: dict, seg_spec: dict, est: dict, st: dict,
             printed: bool = False) -> dict:
    """One segment's range from this run's repeats. ``printed`` (the figure printed beside a score; review A F1/F8):
    a measured figure is never replaced by a larger stored one, and a part counts as measured only when its
    STANDARD units have two or more repeats (``repeat45_n1``). Without it (the pack gates' input) the rule is the
    stamped one: the larger of measured and stored, part repeats counted over every tier."""
    from . import segments45 as _s45
    mx = float(g["max"])
    measured: list[tuple[float, list[float]]] = []
    extra_var = 0.0
    single: list[str] = []
    for u in g.get("units") or []:
        if u.get("share") is None:
            continue
        w = float(u["weight"])
        if seg_spec.get("cases"):
            continue                                         # fix tasks: per case below
        part = None
        for p, spec in (seg_spec.get("parts") or {}).items():
            if (isinstance(spec, dict) and u["id"] in spec) or u["id"] == p:
                part = p
        sc = (((model.get("rplong45") or {}).get("scenes") or {}).get(u["id"])) if part == "rplong" else None
        if sc is not None:
            vals = [float(x) for x in sc.get("values") or [] if x is not None]
            (measured.append((w, vals)) if len(vals) >= 2 else single.append(u["id"]))
            continue
        fk = next((k for k in (model.get("repeat45") or {}) if k.split("/", 1)[-1] == u["id"]), None)
        n_src = (model.get("repeat45_n1") if printed and model.get("repeat45_n1") is not None
                 else model.get("repeat45_n")) or {}
        n = int((n_src.get(fk)) or 0) if fk else 0
        sd = (model.get("repeat45") or {}).get(fk) if fk else None
        if sd is not None and n >= 2:
            extra_var += (w * float(sd)) ** 2
        else:
            single.append(u["id"])
    if seg_spec.get("cases"):
        cases = (model.get("fix45") or {}).get("cases") or {}
        cw = _s45.case_unit_weights({**seg_spec, "max": seg_spec.get("max", mx)})
        for c in seg_spec["cases"]:
            w1 = cw[c]
            sc = [float(x) for x in ((cases.get(c) or {}).get("scores") or []) if x is not None]
            if not sc:
                continue
            (measured.append((w1 * len(sc), sc)) if len(sc) >= 2 else single.append(c))
    v = repeat_var(measured)
    has = v is not None or extra_var > 0 or (not single and (g.get("units") or []))
    mv = (v or 0.0) + extra_var
    half_m = Z90 * math.sqrt(mv) if has else None
    note = None
    src = "repeats" if has else None
    half = half_m
    if single and printed:
        e = est.get(f"{sec}/{key}")
        note = f"{len(single)} unit(s) with a {SINGLE_NOTE}"
        if half_m is not None:
            note += " (not in this figure)"
            return block(half_m, mx, "repeats", st, note, basis="this run's repeats")
        if e is not None:
            note += f"; the stored cross-run figure ({e:g}% of max) is used as an estimate"
            return block(e / 100.0 * mx, mx, "estimate", st, note, basis="stored estimate")
        return block(None, mx, None, st, note + "; no stored cross-run figure")
    if single:
        e = est.get(f"{sec}/{key}")
        note = f"{len(single)} unit(s) with a {SINGLE_NOTE}"
        if e is not None:
            eh = e / 100.0 * mx
            note += f"; the stored cross-run figure ({e:g}% of max) is used as an estimate"
            half = eh if half_m is None else max(half_m, eh)
            src = "estimate" if half_m is None or eh > half_m else "repeats+estimate"
            if half_m is not None:
                src = "repeats+estimate"
        else:
            note += "; no stored cross-run figure"
    return block(half, mx, src, st, note, basis="this run's repeats" if printed and src == "repeats" else None)


def _nsfw(model: dict, est: dict, st: dict, printed: bool = False) -> dict:
    nums = ((model.get("nsfw_plus") or {}).get("numbers")) or {}
    out = {}
    for nk, pre in (("nsfw", "nsfw"), ("uncensored", "unc")):
        segs = {}
        for sk, g in (((nums.get(nk) or {}).get("segments")) or {}).items():
            e = est.get(f"{pre}/{sk}")
            note = f"one repeat per unit: {SINGLE_NOTE}"
            if e is not None:
                segs[f"{pre}/{sk}"] = block(e / 100.0 * float(g.get("max") or 0), float(g.get("max") or 0),
                                            "estimate", st, note + f"; stored cross-run figure {e:g}% used",
                                            basis="stored estimate" if printed else None)
            else:
                segs[f"{pre}/{sk}"] = block(None, float(g.get("max") or 0), None, st, note + "; no stored cross-run figure")
        out[nk] = (segs, _combine(list(segs.values()), SECTION_MAX[nk], st) if segs else
                   block(None, SECTION_MAX[nk], None, st, "NSFW+ not run"))
    return out


def model_accuracy(model: dict, prof: dict | None, estimates: dict | None = None, printed: bool = False) -> dict:
    """The accuracy block of one model's report: ``segments`` {"code/writes": block, ..., "nsfw/erotica": block},
    and one block per score: code, chat, overall, nsfw, uncensored, nsfw_plus; plus the item margins (second
    figure)."""
    from . import segments45 as _s45
    st = settings(prof)
    est = load_estimates() if estimates is None else estimates
    tb = model.get("segments45") or {}
    tree = _s45.tree(prof) or {}
    segs: dict = {}
    secs: dict = {}
    for sec in _s45.SECTIONS:
        sect = tb.get(sec)
        if not sect:
            continue
        mine = [_segment(model, sec, k, g, (tree.get(sec) or {}).get(k) or {}, est, st, printed=printed)
                for k, g in sect["segments"].items()]
        for k, b in zip(sect["segments"], mine):
            segs[f"{sec}/{k}"] = b
        secs[sec] = _combine(mine, SECTION_MAX[sec], st)
    out = {"settings": st, "segments": segs, **secs}
    out["overall"] = _combine([secs[s] for s in ("code", "chat") if s in secs], SECTION_MAX["overall"], st)
    ns = _nsfw(model, est, st, printed=printed)
    for nk, (ss, b) in ns.items():
        segs.update(ss)
        out[nk] = b
    out["nsfw_plus"] = _combine([out["nsfw"], out["uncensored"]], SECTION_MAX["nsfw_plus"], st)
    out["item_margin"] = {"label": ITEM_MARGIN_LABEL,
                          **{sec: (tb.get(sec) or {}).get("margin") for sec in ("code", "chat", "overall")
                             if sec in tb}}
    return out


# ------------------------------------------------------------------ the printed range (review A F1, F2, F9)
REPLICATES_SRC = "replicates"
POOLED_SRC = "pooled"


def _replicate_block(key: str, base: dict | None, label: str, rtr: dict, st: dict) -> dict | None:
    """The range of ``key`` from replicate whole runs, or None when no run-to-run figure covers it. The board shows
    the MEAN of a model's n runs, so the range is t x SD / sqrt(n), t = :func:`t90` of the SD's degrees of freedom.
    SD = the model's own from ``OWN_MIN_RUNS`` runs on; with fewer, the SD pooled over every model that has repeat
    runs (a spread from two or three runs is too uncertain to print alone), labelled as pooled."""
    mx = (base or {}).get("max") or SECTION_MAX.get(key)
    if not mx:
        return None
    n = int(((rtr.get("n") or {}).get(label) or {}).get(key) or 0)
    own = ((rtr.get("by_model") or {}).get(label) or {}).get(key)
    pooled = (rtr.get("pooled") or {}).get(key)
    if n >= 2 and own is not None and (n >= OWN_MIN_RUNS or pooled is None):
        return block(t90(n - 1) * float(own) / math.sqrt(n), mx, REPLICATES_SRC, st,
                     basis=f"mean of {n} runs, own spread ({n - 1} df)", n=n)
    if pooled is None:
        return None
    df = (rtr.get("pooled_df") or {}).get(key)
    if n >= 2:
        return block(t90(df) * float(pooled) / math.sqrt(n), mx, POOLED_SRC, st,
                     basis=f"mean of {n} runs; spread pooled over every model's replicate runs ({df} df)", n=n)
    by = [m for m in (rtr.get("pooled_by") or {}).get(key) or [] if m != label]
    return block(t90(df) * float(pooled), mx, POOLED_SRC, st,
                 basis=f"single run; spread pooled from {len(by)} other model(s)' replicate runs ({df} df)", n=1)


def printed_accuracy(model: dict, prof: dict | None, label: str | None = None, runs: dict | None = None,
                     estimates: dict | None = None) -> dict:
    """The accuracy block printed beside every score (board cells, report.json ``accuracy``). ``runs`` = the board's
    ``precision45.json`` run-to-run block (board scale). Per score and segment: the model's own replicate runs when
    it has two or more; else the pooled replicate SD of other models (labelled); else this run's own repeats (a
    measured figure is never replaced by a larger stored one); else the stored cross-run figure (labelled estimate).
    Every block carries ``basis`` (source and n). Scores are whole-run figures: a section's range is the spread of
    the section itself, not the quadrature of its segments."""
    base = model_accuracy(model, prof, estimates, printed=True)
    if not runs or not label:
        return base
    st = base["settings"]
    out = {**base, "segments": dict(base["segments"])}
    for k, b in list(base["segments"].items()):
        r = _replicate_block(k, b, label, runs, st)
        if r is not None:
            out["segments"][k] = r
    for k in SECTION_MAX:
        if k not in base:
            continue
        r = _replicate_block(k, base[k], label, runs, st)
        if r is not None:
            out[k] = r
    return out
