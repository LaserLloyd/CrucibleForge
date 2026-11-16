"""One ability scale per section (4.5 bank, BANK-DESIGN §3(c)-(d), rulings R28-R31).

    uv run python -m crucibleforge.scale LABEL[=RESULTS_DIR] [LABEL ...]
        [--calibration FILE] [--fit-out FILE] [--section NAME] [--json]

Inputs:
- a run's stored chain rows (``results/transcripts_<label>.jsonl``), read-only; the newest
  graded attempt of each instance (``--bench-run`` limits the rows to given runs). A *unit*
  is one graded chain block (a flat item, a multi-item prompt, a per-turn session, or one
  chain attempt) in cell ``<family>:L<level>[:x<n>]``: its proportion ``x`` = its elements'
  weighted mean (stored element weights), under the cell's ``ran_out`` rule (zero |
  exclude; the block's profile rule, ``--ran-out`` overrides). A unit counts once
  (``--weight unit``, the testlet as one unit, as G2's fit) or by its elements;
- stored CASE rows (the 4.1 anchors: TZ16, CB-C5, CL8...) when a calibration table names
  them: a case row with a 0-1 score (``notes45.row_share``: elements rate, judge rate or
  pass/fail) is one unit, cell = its case id, difficulty = the table key equal to the id or
  its longest ``-``-bounded prefix (``TZ16`` matches ``TZ16-pickup-dst-delay``); its section
  is the table row's ``section``; repeats of one case are one cluster; a reply cut by the
  budget (finish ``length``) is "ran out". Case rows the table does not name are not read;
- a calibration table (versioned JSON/YAML, owned by G2): either G2's calib.py output
  (``cells: [{cell, d, slope, ...}]``, ``family: {fam: [b, step, centre, slope]}``) or
  ``cells: {cell: {b, a?}}``, ``families: {fam: {base, step, centre?, a?}}`` (b = base +
  step x (level - centre)); slope ``a`` (2PL) defaults to 1. A packed cell missing from the
  table uses its unpacked row, never the family trend. The reference form is
  ``form: {<section>: {<cell>: weight}}`` (normalised per section; without it, equal weights
  over the table's cells of the section, flagged). ``--pooled`` = one section "all".

Per section:
- **Main cells only**: the Main ability is scored on the units of the section's form cells
  (``cells=`` overrides); a ``band: top`` cell or a unit in a pack part never enters it. The
  units left out are listed (``left_out``) and scored apart as ``<section>+packs`` with their
  own margin, so a pack cell can never pull a Main score;
- **ability** theta by maximum likelihood with the difficulties and slopes held fixed, on the
  units' proportions (quasi-binomial: sum w (x log P + (1 - x) log(1 - P)),
  P = sigmoid(a (theta - b)));
- **SE** (``se_method`` on the output): with at least ``MIN_CLUSTERS`` (8) clusters and a
  finite estimate, the cluster-robust sandwich SE alone (``cluster``), one cluster per unit
  (prompt / session; a domain cluster when the block carries ``domain``). The model SE
  (1 / sqrt(information), a unit counted as ONE Bernoulli trial) treats a 23-check prompt as a
  coin and was ~3x too wide (G2 refit 2026-11-14). With fewer clusters, or an extreme run, the
  conservative fallback ``max`` = the larger of the model SE and the cluster SE. ``se_quasi`` =
  the model SE x sqrt(phi), phi = Pearson chi2 / (G - 1), is reported beside them;
- **all-zero / all-one** runs have no finite MLE: a half-credit pseudo unit at the mean
  difficulty bounds the estimate and the row is flagged ``extreme``;
- **expected points** on the fixed reference form: 100 x sum_c w_c P_c(theta); its SE = the
  form curve's slope x SE(theta). A model that never took a cell still gets its expected
  points there;
- **ran-out** share per cell, flagged "ceiling too low" above 20%;
- **tie test** between two runs: |gap| < 1.645 x SE of the difference (90%). The SE comes from
  the per-cluster influence of both runs; shared clusters (same instance ids: shared module
  seeds) pair, so the paired SE is used when the runs share a module, the unpaired otherwise.

Without ``--calibration`` the difficulties are fitted from the given runs themselves (joint
maximum likelihood with a small ridge so perfect cells stay finite) and the result is marked
PROVISIONAL: a dry path, not a calibration (G2 owns that). ``--fit-out`` writes that table.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

from . import chains, config
from . import degenerate as _dg

Z_TIE = 1.645
THETA_BOUND = 8.0
MIN_CLUSTERS = 8           # below this the cluster SE is too noisy to stand alone (fallback: max)
RIDGE = 0.1               # provisional fit only: keeps a cell everyone passed (or failed) finite


def _sig(z: float) -> float:
    if z >= 0:
        return 1.0 / (1.0 + math.exp(-z))
    e = math.exp(z)
    return e / (1.0 + e)


def cell_of(blk: dict) -> str:
    """The calibration cell of a unit, in the calibration table's naming:
    ``<family>:L<level>`` plus ``:x<n>`` for a multi-item prompt of n sub-items (packing
    changes the cell's difficulty; G2 units45.py). The variant is not part of the cell."""
    n = len(blk.get("items") or []) if blk.get("kind") == "multi" else 1
    return f"{blk.get('family')}:L{blk.get('level')}" + (f":x{n}" if n > 1 else "")


def section_of(blk: dict) -> str:
    return str(blk.get("part") or "?").split("/")[0]


RAN_OUT = ("zero", "exclude")
#: the one section of a pooled scoring (every unit on one scale, as G2's field fit)
POOLED = "all"


def unit_score(blk: dict, ran_out: str = "zero") -> tuple[float | None, int, int]:
    """(proportion, elements used, ran-out elements) of one graded block. The proportion is
    the elements' weighted mean (each element's stored ``weight``, 1 by default: IMPLIED's
    check weights). ``ran_out``: ``zero`` (today's rule: a "ran out" element counts with the
    credit it earned, 0 for an item never reached) or ``exclude`` (left out of the
    proportion; a unit whose every element ran out gives no proportion)."""
    els = blk.get("elements")
    if not els:
        cut = blk.get("outcome") == "ran out"
        if cut and ran_out == "exclude":
            return None, 0, 1
        return float(blk["score"]), 1, int(cut)
    ro = [e for e in els if e.get("outcome") == "ran out"]
    use = [e for e in els if not (ran_out == "exclude" and e.get("outcome") == "ran out")]
    if not use:
        return None, 0, len(ro)
    # a stored weight of 0 stays 0 (a contested check: out of the proportion); missing = 1
    w = [chains._weight_of(e) for e in use]
    if sum(w) <= 0:
        return None, 0, len(ro)
    return sum(wi * float(e.get("score") or 0.0) for wi, e in zip(w, use)) / sum(w), len(use), len(ro)


def ran_out_rules(prof: dict | None) -> dict[str, str]:
    """{cell: rule} of the SCORING profile's own chain specs (lead 2026-11-14): a stored row is
    scored under the rule the profile scoring it declares, never the rule of the composition it
    was made under (``ran_out`` is not in the chain revision, so an old row stays current). A
    set that declares none is ``zero`` (chains.section_stats' default). Two sets that share a
    cell with different rules are refused (ConfigError): one cell, one rule. A cell the profile
    does not name is absent: :func:`units` then falls back to the stored block's own value."""
    rules: dict[str, str] = {}
    by: dict[str, str] = {}
    for c in chains.chain_cases(prof) if (prof or {}).get("chains") else []:
        sp = c.get("chain_spec") or {}
        for rung in sp.get("rungs") or []:
            cell = cell_of({"family": rung.get("family"), "level": rung.get("level"),
                            "kind": sp.get("kind"), "items": sp.get("items")})
            rule = rung.get("ran_out") or "zero"
            if rule not in RAN_OUT:
                raise config.ConfigError(f"{sp.get('ladder')}: ran_out is one of {list(RAN_OUT)} (got {rule!r})")
            if cell in rules and rules[cell] != rule:
                raise config.ConfigError(
                    f"cell {cell}: set {by[cell]} says ran_out {rules[cell]}, set {sp.get('ladder')} says {rule}: "
                    "one cell is scored under one rule (give the sets the same ran_out)")
            rules.setdefault(cell, rule)
            by.setdefault(cell, str(sp.get("ladder")))
    return rules


def _rule(ran_out, cell, own=None) -> str:
    rule = (ran_out.get(cell, ran_out.get("default")) if isinstance(ran_out, dict) else ran_out) \
        or own or "zero"
    if rule not in RAN_OUT:
        raise ValueError(f"ran_out is one of {list(RAN_OUT)} (got {rule!r} for {cell})")
    return rule


def case_key(cal: dict, case_id: str) -> str | None:
    """The table key of a stored case: the id itself, else its longest prefix ending at a
    ``-`` (``CB-C5`` for ``CB-C5-predict-pyhouse``); chain cells (``:``) never match."""
    cells = cal.get("cells") or {}
    if case_id in cells and ":" not in case_id:
        return case_id
    best = None
    for k in cells:
        if ":" not in k and case_id.startswith(k + "-") and (best is None or len(k) > len(best)):
            best = k
    return best


def case_units(rows: list[dict], cal: dict, ran_out=None, bench_runs=None) -> list[dict]:
    """Units of the stored CASE rows the table names (newest graded row per case and repeat)."""
    from .notes45 import row_share
    newest: dict[tuple, tuple] = {}
    for i, r in enumerate(rows):
        if r.get("chain") or not r.get("case_id") or r.get("turn") not in (None, "final"):
            continue
        if bench_runs and r.get("bench_run_id") not in bench_runs:
            continue
        if case_key(cal, str(r["case_id"])) is None:
            continue
        x = row_share(r)
        if x is None:
            continue
        k = (str(r["case_id"]), int(r.get("repeat") or 1))
        stamp = (str(r.get("ts") or ""), i)
        if k not in newest or stamp >= newest[k][0]:
            newest[k] = (stamp, r, x)
    out = []
    for (cid, rep), (_s, r, x) in sorted(newest.items()):
        key = case_key(cal, cid)
        rule = _rule(ran_out, cid)
        cut = int(r.get("finish_reason") == "length" or bool(r.get("truncated")))
        es = int(bool(r.get("empty_stop")))
        u = {"id": f"{cid}#{rep}", "cell": cid, "kind": "case", "case_key": key,
             "section": str((cal["cells"][key] or {}).get("section") or "case"), "rule": rule,
             "ran_out": cut, "empty_stop": es, "degenerate": int(_dg.is_degenerate(r)),
             "n": 1, "cluster": f"case:{cid}",
             "concept": (cal["cells"][key] or {}).get("concept"), "band": (cal["cells"][key] or {}).get("band")}
        if cut and rule == "exclude" and not u["degenerate"]:
            # a degenerate reply cut at the ceiling is a failure (a loop), never a ran-out to drop
            u["excluded"], u["n"] = True, 0
        else:
            u["x"] = min(1.0, max(0.0, float(x)))
        out.append(u)
    return out


def units(rows: list[dict], ran_out=None, bench_runs=None, cal: dict | None = None) -> list[dict]:
    """The graded units of one run: {id, cell, section, x, n, cluster, ran_out, kind}. Ungraded
    (pending, errored) blocks are left out. ``ran_out``: a rule for every cell, or a mapping
    {cell: rule, "default": rule}; else the block's own ``ran_out`` (profile), else "zero".
    With ``cal``, the stored case rows the table names are units too (:func:`case_units`)."""
    crow = [r for r in rows if r.get("chain") and (not bench_runs or r.get("bench_run_id") in bench_runs)]
    out = case_units(rows, cal, ran_out, bench_runs) if cal else []
    # 4.5 empty_stop_rule: a unit's turns stamped empty_stop (counted beside ran-out, never read)
    estop: dict[tuple, int] = {}
    # degenerate replies (degenerate.py): graded as failures, counted beside ran-out
    dgen: dict[tuple, int] = {}
    for r in crow:
        ch = r["chain"]
        k = (ch["id"], int(ch.get("rung") or 1), int(ch.get("attempt") or 1))
        if r.get("empty_stop"):
            estop[k] = estop.get(k, 0) + 1
        if _dg.is_degenerate(r):
            dgen[k] = dgen.get(k, 0) + 1
    for k, blk in chains._newest_blocks(crow).items():
        if blk.get("score") is None or blk.get("passed") is None:
            continue
        cell = cell_of(blk)
        rule = _rule(ran_out, cell, blk.get("ran_out"))
        # a degenerate reply is a failure even when the ceiling cut it (lead 2026-11-14): its "ran out"
        # elements score 0 under ``exclude`` instead of being dropped (they stay counted as ran out)
        uid = f"{blk['id']}-r{k[1]}-a{k[2]}"
        cluster = f"domain:{blk['domain']}" if blk.get("domain") else uid
        # tier cells: when the table names ``<cell>:<group>`` rows, a tiered unit is scored per group
        # (its checks of that group only), one unit per group, all in the parent prompt's cluster
        groups = _tier_split(cal, cell, blk)
        for g, sub in (groups.items() if groups else [(None, blk)]):
            x, n, ro = unit_score(sub, "zero" if dgen.get(k) else rule)
            u = {"id": uid if g is None else f"{uid}:{g}", "cell": cell if g is None else f"{cell}:{g}",
                 "kind": "chain", "section": section_of(blk), "part": blk.get("part"),
                 "rule": rule, "ran_out": ro, "empty_stop": estop.get(k, 0), "degenerate": dgen.get(k, 0),
                 "n": n, "cluster": cluster, "concept": blk.get("concept"), "band": blk.get("band")}
            if g is not None:
                u["tier"] = g
            if x is None:
                u["excluded"] = True
            else:
                u["x"] = min(1.0, max(0.0, x))
            out.append(u)
    return out


#: the default tier groups of a tier cell (G2 2026-11-14): careful vs base (competent + explicit +
#: unrated); contested checks are weight 0 and in no group. A table may declare its own ``tier_groups``.
TIER_GROUPS = {"careful": ("careful",), "base": ("competent", "explicit", "unrated")}


def tier_groups(cal: dict | None) -> dict[str, tuple]:
    g = (cal or {}).get("tier_groups")
    return {str(k): tuple(v) for k, v in g.items()} if isinstance(g, dict) and g else dict(TIER_GROUPS)


def _tier_split(cal: dict | None, cell: str, blk: dict) -> dict[str, dict] | None:
    """{group: block with that group's elements} when the table names a ``<cell>:<group>`` row and
    the stored unit carries tier labels (chains.tag_tiers); None = the unit stays whole (no tier
    cells for it, or an old row without labels: untiered, never dropped). A group with no element
    of positive weight gives no unit."""
    if not cal:
        return None
    gs = tier_groups(cal)
    cells = cal.get("cells") or {}
    if not any(f"{cell}:{g}" in cells for g in gs):
        return None
    els = blk.get("elements") or []
    if not els or not any(e.get("check_tier") for e in els):
        return None
    out = {}
    for g, members in gs.items():
        sub = [e for e in els if e.get("check_tier") in members]
        if sub and any(chains._weight_of(e) > 0 for e in sub):
            out[g] = {**blk, "elements": sub}
    return out or None


# --------------------------------------------------------------------------- calibration

def load_calibration(path) -> dict:
    p = Path(path)
    txt = p.read_text(encoding="utf-8")
    if p.suffix in (".yaml", ".yml"):
        import yaml
        cal = yaml.safe_load(txt)
    else:
        cal = json.loads(txt)
    cal = normalise(cal)
    if not (cal.get("cells") or cal.get("families")):
        raise ValueError(f"{p}: a calibration table needs cells and/or families")
    cal.setdefault("version", f"{p.name}")
    return cal


def normalise(cal: dict) -> dict:
    """Read either this module's table shape or G2's calibration output (calib.py:
    ``cells: [{cell, family, level, d, slope, concept, band}]``, ``family: {fam: [b, step,
    centre level, slope]}``, ``theta: {...}``) into {cells: {cell: {b, a, ...}}, families:
    {fam: {base, step, centre, a}}, form, ...}. The slope ``a`` (2PL) defaults to 1."""
    if not isinstance(cal, dict):
        raise ValueError("a calibration table is a mapping")
    out = {k: v for k, v in cal.items() if k not in ("cells", "family", "families")}
    cells = cal.get("cells") or {}
    if isinstance(cells, list):
        cells = {c["cell"]: {"b": c["d"], "a": c.get("slope", 1.0),
                             **{k: c[k] for k in ("concept", "band", "family", "level", "section") if k in c}}
                 for c in cells}
    out["cells"] = {k: ({"b": float(v)} if isinstance(v, (int, float)) and not isinstance(v, bool) else dict(v))
                    for k, v in cells.items()}
    fams = dict(cal.get("families") or {})
    for f, v in (cal.get("family") or {}).items():
        if isinstance(v, (list, tuple)) and len(v) >= 4:
            fams[f] = {"base": v[0], "step": v[1], "centre": v[2], "a": v[3]}
    out["families"] = fams
    if cal.get("theta"):
        out["field_theta"] = dict(cal["theta"])
    return out


def item(cal: dict, cell: str) -> tuple[float, float] | None:
    """(difficulty b, slope a) of a cell: its own row, else its family's level trend
    b = base + step x (level - centre) with the family's slope (a packed ``:x<n>`` cell
    has no trend fallback: packing changes the difficulty). None = uncalibrated."""
    c = (cal.get("cells") or {}).get(cell)
    if isinstance(c, dict) and c.get("b") is not None:
        return float(c["b"]), float(c.get("a", 1.0) or 1.0)
    if cell.rsplit(":", 1)[-1] in tier_groups(cal):
        return None                # a tier cell: its own row only (never the parent's row or a trend)
    if ":" not in cell:
        # a stored case: its table key (the id or its longest "-"-bounded prefix)
        k = case_key(cal, cell)
        c = (cal.get("cells") or {}).get(k) if k else None
        if isinstance(c, dict) and c.get("b") is not None:
            return float(c["b"]), float(c.get("a", 1.0) or 1.0)
        return None
    parts = cell.split(":")
    if len(parts) == 3:
        # a packing the table does not distinguish (its cell is named without :x<n>, e.g.
        # istack is only ever packed): that cell's own row, never the family trend
        c = (cal.get("cells") or {}).get(":".join(parts[:2]))
        if isinstance(c, dict) and c.get("b") is not None:
            return float(c["b"]), float(c.get("a", 1.0) or 1.0)
        return None
    if len(parts) != 2 or not parts[1].startswith("L") or not parts[1][1:].isdigit():
        return None
    f = (cal.get("families") or {}).get(parts[0])
    if isinstance(f, dict) and f.get("base") is not None:
        lv = int(parts[1][1:])
        return (float(f["base"]) + float(f.get("step", 0.0)) * (lv - float(f.get("centre", 0.0))),
                float(f.get("a", 1.0) or 1.0))
    return None


def difficulty(cal: dict, cell: str) -> float | None:
    it = item(cal, cell)
    return None if it is None else it[0]


def form_of(cal: dict, section: str, seen_cells) -> tuple[dict, bool]:
    """(cell -> weight summing to 1, declared?) for a section; without a declared form, equal
    weights over the table's cells of that section (the same form for every run), else over
    the cells seen (flagged either way)."""
    f = (cal.get("form") or {}).get(section)
    if f:
        w = {c: float(v) for c, v in f.items() if difficulty(cal, c) is not None}
        declared = True
    else:
        tab = [c for c, v in (cal.get("cells") or {}).items()
               if isinstance(v, dict) and (section == POOLED or v.get("section") == section)
               and item(cal, c) is not None]
        w = {c: 1.0 for c in sorted(tab or [c for c in seen_cells if difficulty(cal, c) is not None])}
        declared = False
    t = sum(w.values())
    return ({c: v / t for c, v in w.items()} if t else {}), declared


# --------------------------------------------------------------------------- estimation

#: a cell whose ran-out share is above this is flagged "ceiling too low" (never silently averaged)
RAN_OUT_FLAG = 0.20

#: 4.5 (lead 2026-11-14): on a ``ran_out: exclude`` cell, above this share of UNITS ran out (a unit
#: counts when any of its elements ran out) the cell is "not measured" for that model and its
#: finished units leave the ability (a survivor sample reads high). ``zero`` cells are untouched:
#: their ran-out units already score as failures. Unit basis, as G2's refit intake (units46.py).
RAN_OUT_NOT_MEASURED = 0.25


def unit_ran_out(us: list[dict]) -> dict:
    """Per cell: {units, ran_out_units, share (of units), rule}: a unit ran out when any of its
    elements did (``ran_out_report`` counts elements; this counts units)."""
    out: dict = {}
    for u in us:
        c = out.setdefault(u["cell"], {"units": 0, "ran_out_units": 0, "rule": u.get("rule", "zero")})
        c["units"] += 1
        # a degenerate unit is a failure, not a ran-out (it never counts toward the 25%)
        c["ran_out_units"] += int(bool(u.get("ran_out")) and not u.get("degenerate"))
    for c in out.values():
        c["share"] = c["ran_out_units"] / c["units"] if c["units"] else 0.0
    return out


def mark_not_measured(us: list[dict], limit: float = RAN_OUT_NOT_MEASURED) -> list[str]:
    """The ``exclude`` cells above ``limit`` ran-out units: every unit of theirs is marked
    ``excluded`` + ``not_measured`` (in place), so no ability reads them. Returns the cells."""
    rep = unit_ran_out(us)
    cells = sorted(c for c, v in rep.items() if v["rule"] == "exclude" and v["share"] > limit)
    for u in us:
        if u["cell"] in cells:
            u["excluded"] = True
            u["not_measured"] = True
    return cells


def ability(us: list[dict], cal: dict, weight: str = "unit") -> dict:
    """theta, SEs and per-cluster influence of one run's units on one section (2PL:
    P = sigmoid(a (theta - b)), a = the cell's slope). ``weight``: ``unit`` (each prompt /
    session counts once: the testlet as one unit, G2's calibration) or ``elements``."""
    us = [u for u in us if not u.get("excluded") and item(cal, u["cell"]) is not None]
    if not us:
        return {"theta": None, "units": 0}
    ab = [item(cal, u["cell"]) for u in us]
    xs = [u["x"] for u in us]
    ns = [float(u["n"]) if weight == "elements" else 1.0 for u in us]
    tot_n = sum(ns)
    got = sum(n * x for n, x in zip(ns, xs))
    extreme = None
    fit_ab, fit_x, fit_n = list(ab), list(xs), list(ns)
    if got <= 1e-9 or got >= tot_n - 1e-9:
        extreme = "all-zero" if got <= 1e-9 else "all-one"
        # bounded: one half-credit pseudo unit at the mean difficulty (mean slope)
        fit_ab.append((sum(b for b, _ in ab) / len(ab), sum(a for _, a in ab) / len(ab)))
        fit_x.append(0.5)
        fit_n.append(1.0)
    th = 0.0
    for _ in range(200):
        ps = [_sig(a * (th - b)) for b, a in fit_ab]
        g = sum(n * a * (x - p) for n, x, p, (_b, a) in zip(fit_n, fit_x, ps, fit_ab))
        info = sum(n * a * a * p * (1 - p) for n, p, (_b, a) in zip(fit_n, ps, fit_ab))
        step = g / info if info > 1e-12 else 0.0
        step = max(-2.0, min(2.0, step))
        th = max(-THETA_BOUND, min(THETA_BOUND, th + step))
        if abs(step) < 1e-10:
            break
    ps = [_sig(a * (th - b)) for b, a in ab]
    info = sum(n * a * a * p * (1 - p) for n, p, (_b, a) in zip(ns, ps, ab))
    se_model = 1.0 / math.sqrt(info) if info > 1e-12 else float("inf")
    psi: dict[str, float] = {}
    for u, n, x, p, (_b, a) in zip(us, ns, xs, ps, ab):
        psi[u["cluster"]] = psi.get(u["cluster"], 0.0) + (n * a * (x - p) / info if info > 1e-12 else 0.0)
    G = len(psi)
    se_cl = math.sqrt(G / (G - 1) * sum(v * v for v in psi.values())) if G > 1 else float("inf")
    # quasi-binomial dispersion over the units (Pearson chi2 / (G - 1)), reported beside
    chi2 = sum(n * (x - p) ** 2 / (p * (1 - p)) for n, x, p in zip(ns, xs, ps) if 1e-12 < p < 1 - 1e-12)
    phi = chi2 / (len(us) - 1) if len(us) > 1 else None
    if G >= MIN_CLUSTERS and math.isfinite(se_cl) and extreme is None:
        se, method = se_cl, "cluster"
    else:
        se, method = (max(se_model, se_cl) if math.isfinite(se_cl) else se_model), "max"
    return {"theta": th, "se": se, "se_method": method,
            "se_model": se_model, "se_cluster": se_cl if math.isfinite(se_cl) else None,
            "se_quasi": None if phi is None else se_model * math.sqrt(phi), "phi": phi,
            "units": len(us), "clusters": G, "weight": weight, "raw": got / tot_n,
            "extreme": extreme, "psi": psi}


def points(theta: float, form: dict, cal: dict) -> tuple[float, float]:
    """(expected points 0..100 on the reference form, d points / d theta)."""
    pts = slope = 0.0
    for c, w in form.items():
        b, a = item(cal, c)
        p = _sig(a * (theta - b))
        pts += 100.0 * w * p
        slope += 100.0 * w * a * p * (1 - p)
    return pts, slope


def ran_out_report(us: list[dict]) -> dict:
    """Per cell: units, ran-out elements / elements seen, rule, and the "ceiling too low"
    flag (ran-out share above RAN_OUT_FLAG)."""
    out: dict = {}
    for u in us:
        c = out.setdefault(u["cell"], {"units": 0, "ran_out": 0, "elements": 0, "excluded_units": 0,
                                       "rule": u.get("rule", "zero"), "empty_stop": 0, "degenerate": 0,
                                       "ran_out_degenerate": 0})
        c["units"] += 1
        c["empty_stop"] += int(u.get("empty_stop") or 0)
        c["degenerate"] += int(u.get("degenerate") or 0)
        c["ran_out"] += u.get("ran_out", 0)
        # both kinds of cut stay visible: ``ran_out`` counts every cut element, ``ran_out_degenerate``
        # the ones in a degenerate unit (scored 0 as failures, never dropped, so already inside ``n``)
        dg = bool(u.get("degenerate"))
        c["ran_out_degenerate"] += u.get("ran_out", 0) if dg else 0
        c["elements"] += u.get("n", 0) + (u.get("ran_out", 0) if u.get("rule") == "exclude" and not dg else 0)
        c["excluded_units"] += int(bool(u.get("excluded")))
    for c in out.values():
        c["share"] = c["ran_out"] / c["elements"] if c["elements"] else (1.0 if c["ran_out"] else 0.0)
        c["ceiling_too_low"] = c["share"] > RAN_OUT_FLAG
    return out


def _band(cal: dict, u: dict) -> str | None:
    c = (cal.get("cells") or {}).get(u.get("case_key") or u["cell"])
    return str(u.get("band") or (c.get("band") if isinstance(c, dict) else None) or "").lower() or None


def main_cells(cal: dict, section: str) -> set[str] | None:
    """The cells a section's MAIN ability is scored on: the calibration form's cells for the
    section (pooled: every form section's); None = the table declares no form (provisional
    self-fit: every cell)."""
    form = cal.get("form") or {}
    if not form:
        return None
    if section in form:
        return set(form[section])
    if section == POOLED:
        return set().union(*(set(v) for v in form.values()))
    return set()


def _in_main(u: dict, cal: dict, cells, pack_parts) -> bool:
    """A unit counts in the Main ability: its cell is in the section's Main cells, its band is
    not ``top`` and it does not sit in a pack part."""
    if _band(cal, u) == "top" or (pack_parts and u.get("part") in pack_parts):
        return False
    return cells is None or u["cell"] in cells or (u.get("case_key") in cells if u.get("case_key") else False)


def score_run(us: list[dict], cal: dict, sections=None, weight: str = "unit", cells=None,
              pack_parts=None) -> dict:
    """Per section: the Main ability (only the units of the section's Main cells: ``cells`` =
    {section: cells} or one set for every section, default the calibration form's cells; a
    ``band: top`` cell or a unit in a ``pack_parts`` part never enters Main), the units left
    out (``left_out`` by cell), and the left-out calibrated units scored apart under
    ``<section>+packs`` with their own margin (equal weights over their cells)."""
    out = {}
    for sec in sorted({u["section"] for u in us}):
        if sections and sec not in sections:
            continue
        allu = [u for u in us if u["section"] == sec]
        cs = (cells.get(sec) if isinstance(cells, dict) else set(cells)) if cells is not None \
            else main_cells(cal, sec)
        su = [u for u in allu if _in_main(u, cal, cs, pack_parts)]
        rest = [u for u in allu if not _in_main(u, cal, cs, pack_parts)]
        left: dict[str, int] = {}
        for u in rest:
            left[u["cell"]] = left.get(u["cell"], 0) + 1
        a = _score_section(su, cal, sec, weight)
        a["left_out"] = left
        out[sec] = a
        if any(item(cal, u["cell"]) is not None and not u.get("excluded") for u in rest):
            pk = _score_section(rest, cal, f"{sec}+packs", weight)
            pk["form_declared"] = pk.get("form_declared", False)
            out[f"{sec}+packs"] = pk
    return out


def _score_section(su: list[dict], cal: dict, sec: str, weight: str) -> dict:
    a = ability(su, cal, weight)
    a["uncalibrated_cells"] = sorted({u["cell"] for u in su if item(cal, u["cell"]) is None})
    rep = ran_out_report(su)
    a["ran_out"] = rep
    a["ceiling_too_low"] = sorted(c for c, r in rep.items() if r["ceiling_too_low"])
    if a.get("theta") is not None:
        if sec.endswith("+packs"):
            seen = sorted({u["cell"] for u in su if item(cal, u["cell"]) is not None})
            form, declared = {c: 1.0 / len(seen) for c in seen}, False
        else:
            form, declared = form_of(cal, sec, {u["cell"] for u in su})
        pts, slope = points(a["theta"], form, cal)
        a.update(points=pts, slope=slope, se_points=slope * a["se"], margin_90=Z_TIE * slope * a["se"],
                 form_declared=declared,
                 form_cells=len(form))
    return a


def tie(a: dict, b: dict) -> dict | None:
    """Tie test between two runs' results on one section (|gap| < 1.645 SE of the difference).
    Paired through shared clusters (the same instance ids), unpaired otherwise."""
    if a.get("theta") is None or b.get("theta") is None or "points" not in a or "points" not in b:
        return None
    keys = set(a["psi"]) | set(b["psi"])
    shared = len(set(a["psi"]) & set(b["psi"]))
    G = len(keys)
    # influence on points: slope x influence on theta, per cluster
    v_cl = sum((a["slope"] * a["psi"].get(k, 0.0) - b["slope"] * b["psi"].get(k, 0.0)) ** 2 for k in keys)
    v_cl *= G / (G - 1) if G > 1 else 1.0
    # paired: the per-cluster influences carry the covariance of shared instances; unpaired:
    # never below the model-based variance (as each run's own SE)
    # each run's own reported SE (cluster with >= MIN_CLUSTERS, else the conservative max)
    v_sel = (a["slope"] * a["se"]) ** 2 + (b["slope"] * b["se"]) ** 2
    both_cluster = a.get("se_method") == "cluster" and b.get("se_method") == "cluster"
    se_d = math.sqrt(max(v_cl, 0.0)) if (shared and both_cluster) else math.sqrt(max(v_cl, v_sel))
    d = a["points"] - b["points"]
    return {"gap": d, "se_diff": se_d, "paired": bool(shared), "shared_clusters": shared,
            "se_method": "cluster" if both_cluster else "max",
            "tied": abs(d) < Z_TIE * se_d or abs(d) < 1e-9}


# --------------------------------------------------------------------------- provisional fit

def fit_difficulties(runs: dict[str, list[dict]], iters: int = 200) -> dict:
    """Joint ML (ridge RIDGE on b and theta) over the given runs: a PROVISIONAL table so the
    dry path runs before G2's calibration exists. Abilities are fitted per (run, section)."""
    runs = {m: [u for u in us if not u.get("excluded")] for m, us in runs.items()}
    cells = sorted({u["cell"] for us in runs.values() for u in us})
    b = {c: 0.0 for c in cells}
    th = {(m, u["section"]): 0.0 for m, us in runs.items() for u in us}
    for _ in range(iters):
        delta = 0.0
        for key in th:
            m, sec = key
            g, h = -RIDGE * th[key], RIDGE
            for u in runs[m]:
                if u["section"] != sec:
                    continue
                p = _sig(th[key] - b[u["cell"]])
                g += u["n"] * (u["x"] - p)
                h += u["n"] * p * (1 - p)
            st = max(-2.0, min(2.0, g / h))
            th[key] += st
            delta = max(delta, abs(st))
        for c in cells:
            g, h = -RIDGE * b[c], RIDGE
            for m, us in runs.items():
                for u in us:
                    if u["cell"] != c:
                        continue
                    p = _sig(th[(m, u["section"])] - b[c])
                    g -= u["n"] * (u["x"] - p)
                    h += u["n"] * p * (1 - p)
            st = max(-2.0, min(2.0, g / h))
            b[c] += st
            delta = max(delta, abs(st))
        if delta < 1e-8:
            break
    sec_of = {u["cell"]: u["section"] for us in runs.values() for u in us}
    return {"version": "provisional-self-fit", "provisional": True, "ridge": RIDGE,
            "fitted_on": sorted(runs),
            "cells": {c: {"b": round(b[c], 4), "section": sec_of[c]} for c in cells}}


# --------------------------------------------------------------------------- CLI

def drop_tripped(rows: list[dict], meta: dict | None) -> tuple[list[dict], dict | None]:
    """The rows of a run the early stop / run-start probe TRIPPED are dropped (lead 2026-11-14:
    the serving state, not the model, is then the suspect, and the run is unpublishable). Rows of
    other runs, and degenerate rows of a run that did not trip (scored as failures), stay.
    Returns (rows kept, {bench_run_id, signal, rows} of what was dropped, or None)."""
    es = (meta or {}).get("early_stop") or {}
    rid = es.get("bench_run_id")
    if not es.get("signal") or not rid:
        return rows, None
    kept = [r for r in rows if r.get("bench_run_id") != rid]
    return kept, {"bench_run_id": rid, "signal": es["signal"], "rows": len(rows) - len(kept)}


def _load(spec: str) -> tuple[str, list[dict]]:
    label, _, d = spec.partition("=")
    base = Path(d) if d else config.results_dir()
    p = base / f"transcripts_{label}.jsonl"
    # the readers' shared rule (config.dedupe_rows): a judge re-append or a regrade's
    # superseding row replaces the row it repeats
    rows = config.dedupe_rows([json.loads(line) for line in p.read_text(encoding="utf-8").splitlines() if line.strip()])
    mp = base / f"meta_{label}.json"
    meta = json.loads(mp.read_text(encoding="utf-8")) if mp.exists() else None
    rows, dropped = drop_tripped(rows, meta)
    if dropped:
        print(f"{label}: dropped {dropped['rows']} row(s) of run {dropped['bench_run_id']} "
              f"(tripped: {dropped['signal']})", file=sys.stderr)
    return label, rows


def render(res: dict, ties: list) -> str:
    def f(v, p=2):
        return "-" if v is None else f"{v:.{p}f}"
    L = ["| model | section | ability | SE | SE from | SE model | SE cluster | units | clusters | raw | points | ± 1.645 SE (90%) | flags |",
         "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for m, secs in res.items():
        for s, a in secs.items():
            flags = [x for x in (a.get("extreme"),
                                 None if a.get("form_declared", True) else "form: equal weights (none declared)",
                                 f"uncalibrated: {', '.join(a['uncalibrated_cells'])}" if a.get("uncalibrated_cells") else None,
                                 "not in Main: " + ", ".join(f"{c} x{n}" for c, n in sorted(a["left_out"].items()))
                                 if a.get("left_out") else None,
                                 "ceiling too low: " + ", ".join(
                                     f"{c} {a['ran_out'][c]['share']:.0%} ran out" for c in a["ceiling_too_low"])
                                 if a.get("ceiling_too_low") else None,
                                 "empty stop: " + ", ".join(f"{c} {r['empty_stop']}" for c, r in sorted(a["ran_out"].items())
                                                            if r.get("empty_stop"))
                                 if any(r.get("empty_stop") for r in (a.get("ran_out") or {}).values()) else None,
                                 "degenerate: " + ", ".join(f"{c} {r['degenerate']}" for c, r in sorted(a["ran_out"].items())
                                                            if r.get("degenerate"))
                                 if any(r.get("degenerate") for r in (a.get("ran_out") or {}).values()) else None)
                     if x]
            L.append(f"| {m} | {s} | {f(a.get('theta'))} | {f(a.get('se'))} | {a.get('se_method', '-')} | "
                     f"{f(a.get('se_model'))} | "
                     f"{f(a.get('se_cluster'))} | {a.get('units', 0)} | {a.get('clusters', 0)} | {f(a.get('raw'))} | "
                     f"{f(a.get('points'), 1)} | {f(None if a.get('se_points') is None else Z_TIE * a['se_points'], 1)} | "
                     f"{'; '.join(flags)} |")
    if ties:
        L += ["", "| pair | section | gap (points) | SE diff | paired | tied (90%) |", "|---|---|---|---|---|---|"]
        for t in ties:
            L.append(f"| {t['pair']} | {t['section']} | {t['gap']:.1f} | {t['se_diff']:.1f} | "
                     f"{'yes (' + str(t['shared_clusters']) + ' shared)' if t['paired'] else 'no'} | "
                     f"{'TIED' if t['tied'] else 'apart'} |")
    return "\n".join(L)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="crucibleforge.scale", description=__doc__.split("\n\n")[0])
    ap.add_argument("runs", nargs="+", help="LABEL or LABEL=RESULTS_DIR")
    ap.add_argument("--calibration", help="calibration table (JSON/YAML); default: provisional self-fit")
    ap.add_argument("--fit-out", help="write the provisional self-fit table here")
    ap.add_argument("--section", action="append", help="only these sections (repeatable)")
    ap.add_argument("--pooled", action="store_true",
                    help="one ability over every section (the field fit's single scale)")
    ap.add_argument("--weight", choices=("unit", "elements"), default="unit",
                    help="a unit (prompt / session) counts once (default), or by its elements")
    ap.add_argument("--ran-out", choices=RAN_OUT,
                    help="rule for every cell (default: each block's profile rule, else zero)")
    ap.add_argument("--bench-run", action="append",
                    help="only rows of these bench_run_ids (repeatable; default: the newest graded "
                         "attempt of each instance over every stored run)")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    runs = dict(_load(s) for s in a.runs)
    table = load_calibration(a.calibration) if a.calibration else None
    us = {m: units(rows, a.ran_out, set(a.bench_run or ()) or None, cal=table) for m, rows in runs.items()}
    if a.pooled:
        us = {m: [{**u, "section": POOLED} for u in uu] for m, uu in us.items()}
    if table is not None:
        cal = table
    else:
        cal = fit_difficulties(us)
        if a.fit_out:
            Path(a.fit_out).write_text(json.dumps(cal, indent=1, sort_keys=True), encoding="utf-8")
    res = {m: score_run(u, cal, a.section, a.weight) for m, u in us.items()}
    ties = []
    ms = list(res)
    for i, x in enumerate(ms):
        for y in ms[i + 1:]:
            for s in set(res[x]) & set(res[y]):
                t = tie(res[x][s], res[y][s])
                if t:
                    ties.append({"pair": f"{x} - {y}", "section": s, **t})
    if a.json:
        clean = {m: {s: {k: v for k, v in r.items() if k != "psi"} for s, r in secs.items()} for m, secs in res.items()}
        print(json.dumps({"calibration": cal.get("version"), "provisional": bool(cal.get("provisional")),
                          "results": clean, "ties": ties}, indent=1, sort_keys=True))
    else:
        if cal.get("provisional"):
            print(f"PROVISIONAL: difficulties self-fitted on {', '.join(cal['fitted_on'])} "
                  f"(ridge {cal['ridge']}); not a calibration.\n")
        else:
            print(f"calibration: {cal.get('version', '?')}\n")
        print(render(res, ties))
    return 0


if __name__ == "__main__":
    sys.exit(main())
