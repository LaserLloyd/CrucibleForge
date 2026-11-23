"""Locator -> module routing by score (4.5 bank, BANK-DESIGN §3(b); rulings R28-R31).

A profile (or composition) declares::

    routing:
      calibration: <path to the versioned calibration table>   # scale.load_calibration
      section: all            # the locator ability's section (default all = pooled)
      locator: [<set>, ...]   # flat-set / ladder names run first
      modules:                # in ascending ability order
        - {name: low, sets: [...]}
        - {name: mid, sets: [...]}
        - {name: packs, sets: [...], unlock: true,    # "clears Main": unlocks the packs
           packs: [<score>/<part>, ...]}            # the pack parts the route opens (optional)
      cuts: [-1.0, 0.2]       # len(modules) - 1 ascending cut points (logit, the table's scale)
      straddle: half          # within one SE of a cut: half of each neighbouring module (none: off)

The locator runs with every set the routing does not name. When it has ended, the model's
ability on the locator units is estimated with the table's difficulties held fixed
(``scale.ability``) and the module(s) are picked from the cuts: never by name, id, vendor
or provider (``test_routing_reads_no_model_identity``). A set's seeds are its own (name +
release seed), so a module's instances are fixed per module per bench version and every
model routed to it takes the same ones; "half" = the first half of each module set's units
(the same half for every model). Every module row carries the route taken (``route``).

``packs`` (on the unlock module only) names the pack parts (max-0 parts of ``scores``) whose
ladder sets open when the model's route says ``unlock: true`` -- a score decision, the route's
alone. Such a part must not also declare ``unlock_part`` (one gate per pack, refused at load);
packs the routing does not name keep ``ladder.unlock`` on their own section's score.
"""
from __future__ import annotations

import hashlib
import re
import math
from pathlib import Path

from . import chains, scale
from .config import ConfigError

STRADDLE = ("half", "none")


def _set_names(prof: dict) -> set[str]:
    out = set()
    for sec in chains.sections(prof):
        out |= {lad["name"] for lad in sec["ladders"]}
        out |= {fs["name"] for fs in sec["flat"]}
    return out


def _expand(names, known: set[str], where: str) -> list[str]:
    """Set names; a levels-form set may be named by its base name (all its ``<name>_L<k>``)."""
    out = []
    for n in names or []:
        hit = [k for k in known if k == n or (k.startswith(f"{n}_L") and k[len(n) + 2:].isdigit())]
        if not hit:
            raise ConfigError(f"{where}: no flat set or ladder named {n!r}")
        out += sorted(hit)
    return out


def _packs(prof: dict, names, where: str) -> list[str]:
    """Validated ``<score>/<part>`` pack keys: an existing max-0 part with no ``unlock_part``."""
    if names is None:
        return []
    if not isinstance(names, list) or not all(isinstance(n, str) and n.count("/") == 1 for n in names):
        raise ConfigError(f"{where}.packs: a list of <score>/<part> keys")
    scores = (prof or {}).get("scores") or {}
    for n in names:
        sec, part = n.split("/")
        ps = ((scores.get(sec) or {}).get("parts") or {}).get(part)
        if ps is None:
            raise ConfigError(f"{where}.packs: no part {n!r} in scores")
        ps = ps or {}
        if ps.get("unlock_part"):
            raise ConfigError(f"{where}.packs: {n} also declares unlock_part -- a routed pack opens on "
                              f"the route alone (one gate per pack)")
        if float(ps.get("max", ps.get("weight", 0)) or 0) != 0:
            raise ConfigError(f"{where}.packs: {n} has points of its own (max > 0); a pack part is max 0")
    if len(set(names)) != len(names):
        raise ConfigError(f"{where}.packs: a pack is named twice")
    return list(names)


def pack_parts(prof: dict | None) -> set[str]:
    """The ``<score>/<part>`` packs a routed profile opens from the route (empty: unrouted)."""
    sp = spec(prof)
    return set() if sp is None else {k for m in sp["modules"] for k in m["packs"]}


def opened(route: dict | None) -> bool:
    """Whether a model's route opens the routed packs: its ``unlock`` (score only). No
    route yet = closed."""
    return bool((route or {}).get("unlock"))


_CELL_RE = re.compile(r"^[A-Za-z0-9_]+:L\d+(?::x\d+)?$")


def _cells(prof: dict) -> set[str]:
    """Every calibration cell (scale.cell_of naming) the composition's sets produce."""
    out = set()
    for sec in chains.sections(prof):
        for lad in sec["ladders"]:
            out |= {f"{r['family']}:L{r['level']}" for r in lad["rungs"]}
        for fs in sec["flat"]:
            it = fs["item"]
            out.add(f"{it['family']}:L{it['level']}" + (f":x{fs['n']}" if fs.get("n", 1) > 1 else ""))
    return out


def _effort_cells(prof: dict, names, where: str) -> list[str]:
    """Validated ``effort_cells``: cells (``<family>:L<level>[:x<n>]``) whose order follows thinking
    effort, each produced by the composition's sets (a name without ``:x<n>`` also covers its packed
    forms). The board prints their effort line; an absent key prints none."""
    if names is None:
        return []
    if not isinstance(names, list) or not all(isinstance(n, str) and _CELL_RE.match(n) for n in names):
        raise ConfigError(f"{where}.effort_cells: a list of <family>:L<level>[:x<n>] cell names")
    if len(set(names)) != len(names):
        raise ConfigError(f"{where}.effort_cells: a cell is named twice")
    have = _cells(prof)
    for n in names:
        if not any(c == n or c.startswith(n + ":") for c in have):
            raise ConfigError(f"{where}.effort_cells: no set of this composition makes cell {n!r}")
    return list(names)


def spec(prof: dict | None) -> dict | None:
    """The validated ``routing`` block, or None. Raises ConfigError."""
    r = (prof or {}).get("routing")
    if not r:
        return None
    where = f"profile {(prof or {}).get('name')}: routing"
    # The routed path scores through scale.units, which knows only the plain cut rule (D10): it has no
    # did-not-finish reading, so cut_at_full_budget: zero would be silently ignored there. 4.5 ships no routed
    # composition; refuse the pair at load instead of scoring it under a rule the profile did not ask for.
    if chains.cut_at_full_budget(prof) == "zero":
        raise ConfigError(f"{where}: cut_at_full_budget: zero with a routing block: "
                          "routed compositions are not part of 4.5")
    if not isinstance(r, dict):
        raise ConfigError(f"{where}: a mapping")
    unknown = set(r) - {"calibration", "section", "locator", "modules", "cuts", "straddle", "effort_cells"}
    if unknown:
        raise ConfigError(f"{where}: unknown key(s) {sorted(unknown)}")
    known = _set_names(prof)
    loc = _expand(r.get("locator"), known, f"{where}.locator")
    if not loc:
        raise ConfigError(f"{where}: locator names the sets run first")
    mods = r.get("modules")
    if not isinstance(mods, list) or len(mods) < 2:
        raise ConfigError(f"{where}: modules is a list of at least two {{name, sets}}")
    out_m, seen = [], set(loc)
    for i, m in enumerate(mods):
        mw = f"{where}.modules[{i}]"
        if not isinstance(m, dict) or not m.get("name") or set(m) - {"name", "sets", "unlock", "packs"}:
            raise ConfigError(f"{mw}: {{name, sets, unlock?, packs?}}")
        if m.get("packs") and not m.get("unlock"):
            raise ConfigError(f"{mw}: only the unlock module names packs")
        ss = _expand(m.get("sets"), known, mw) if m.get("sets") else []
        if not ss and not m.get("unlock"):
            raise ConfigError(f"{mw}: a module names its sets (only an unlock module may be empty)")
        dup = seen & set(ss)
        if dup:
            raise ConfigError(f"{mw}: set(s) {sorted(dup)} already in the locator or another module")
        seen |= set(ss)
        out_m.append({"name": str(m["name"]), "sets": ss, "unlock": bool(m.get("unlock")),
                      "packs": _packs(prof, m.get("packs"), mw)})
    if any(m["unlock"] for m in out_m[:-1]):
        raise ConfigError(f"{where}: only the top module may unlock the packs")
    cuts = r.get("cuts")
    if (not isinstance(cuts, list) or len(cuts) != len(out_m) - 1
            or not all(isinstance(c, (int, float)) and not isinstance(c, bool) for c in cuts)
            or any(b <= a for a, b in zip(cuts, cuts[1:]))):
        raise ConfigError(f"{where}: cuts is {len(out_m) - 1} ascending number(s) (one between each "
                          f"pair of modules)")
    st = r.get("straddle", "half")
    if st not in STRADDLE:
        raise ConfigError(f"{where}: straddle is one of {list(STRADDLE)}")
    if not r.get("calibration"):
        raise ConfigError(f"{where}: calibration names the versioned table the cuts are on")
    # a part's ladder packs open behind the route's ``unlock_from`` module (report._routed_pack_gate)
    mnames = [m["name"] for m in out_m]
    for sec, sv in ((prof or {}).get("scores") or {}).items():
        for pk, ps in ((sv or {}).get("parts") or {}).items():
            frm = (ps or {}).get("unlock_from")
            if frm is not None and frm not in mnames:
                raise ConfigError(f"{where}: scores {sec}/{pk}.unlock_from: no module {frm!r} "
                                  f"(modules: {mnames})")
    return {"calibration": str(r["calibration"]), "section": str(r.get("section", scale.POOLED)),
            "locator": loc, "modules": out_m, "cuts": [float(c) for c in cuts], "straddle": st,
            "routed": seen, "effort_cells": _effort_cells(prof, r.get("effort_cells"), where)}


def calibration(sp: dict, base: Path | None = None) -> tuple[dict, dict]:
    """(table, {path, sha}) of the routing's calibration (relative paths from ``base``)."""
    p = Path(sp["calibration"])
    if not p.is_absolute() and base is not None:
        p = base / p
    cal = scale.load_calibration(p)
    return cal, {"path": str(sp["calibration"]), "sha256": hashlib.sha256(p.read_bytes()).hexdigest()[:16],
                 "version": cal.get("version")}


def locator_ability(rows: list[dict], sp: dict, cal: dict, prof: dict | None = None) -> dict:
    """The ability on the locator units (difficulties held fixed), or {"theta": None} with
    ``pending`` = locator units not yet graded. With ``prof``, each cell's ran-out rule is the
    profile's own (scale.ran_out_rules), not the stored block's."""
    loc = set(sp["locator"])
    mine = [r for r in rows if (r.get("chain") or {}).get("ladder") in loc]
    us = scale.units(mine, ran_out=scale.ran_out_rules(prof) if prof is not None else None)
    if sp["section"] != scale.POOLED:
        us = [u for u in us if u["section"] == sp["section"]]
    a = scale.ability(us, cal)
    a.pop("psi", None)
    return a


def choose(theta: float, se: float, sp: dict) -> dict:
    """The route for a locator ability: the module whose band holds theta; within one SE of
    a cut (``straddle: half``), half of each neighbouring module. Depends on the score only."""
    cuts, mods = sp["cuts"], sp["modules"]
    k = sum(1 for c in cuts if theta >= c)
    picks = [{"name": mods[k]["name"], "share": "all"}]
    near = None
    if sp["straddle"] == "half" and se is not None and math.isfinite(se):
        near = min(range(len(cuts)), key=lambda i: abs(theta - cuts[i]))
        if abs(theta - cuts[near]) < se:
            picks = [{"name": mods[near]["name"], "share": "half"},
                     {"name": mods[near + 1]["name"], "share": "half"}]
        else:
            near = None
    # the packs open only when the unlock module is picked WHOLE: a model straddling the top
    # cut takes its half-modules and no packs (it has not cleared Main by a margin)
    unlock = any(mods[[m["name"] for m in mods].index(p["name"])]["unlock"] and p["share"] == "all"
                 for p in picks)
    return {"modules": picks, "unlock": unlock, "straddled_cut": None if near is None else cuts[near]}


def _half(sp_case: dict, counts: dict) -> bool:
    """Whether a unit is in the first half of its set (the same units for every model)."""
    n = counts.get(sp_case["ladder"], 1)
    return int(sp_case["c"]) <= math.ceil(n / 2)


def stage_cases(cases: list[dict], sp: dict, route: dict | None = None) -> list[dict]:
    """The cases of one stage: route None = the locator stage (the locator sets + every case
    the routing does not name); else the route's module cases, each carrying ``route``."""
    if route is None:
        return [c for c in cases
                if not c.get("chain_spec") or c["chain_spec"]["ladder"] not in sp["routed"]
                or c["chain_spec"]["ladder"] in sp["locator"]]
    by_name = {m["name"]: m for m in sp["modules"]}
    counts: dict[str, int] = {}
    for c in cases:
        if c.get("chain_spec"):
            counts[c["chain_spec"]["ladder"]] = max(counts.get(c["chain_spec"]["ladder"], 0),
                                                   int(c["chain_spec"]["c"]))
    out = []
    for p in route["modules"]:
        sets = set(by_name[p["name"]]["sets"])
        for c in cases:
            cs = c.get("chain_spec")
            if cs and cs["ladder"] in sets and (p["share"] == "all" or _half(cs, counts)):
                out.append({**c, "route": dict(route, module=p["name"], share=p["share"])})
    return out


def locator_cells(cases: list[dict], sp: dict) -> set[str]:
    """The calibration cells the locator's units fall in (``scale.cell_of`` naming)."""
    out = set()
    for c in cases:
        cs = c.get("chain_spec")
        if not cs or cs["ladder"] not in sp["locator"]:
            continue
        n = len(cs.get("items") or [])
        for rg in cs["rungs"]:
            out.add(f"{rg['family']}:L{rg['level']}" + (f":x{n}" if n > 1 else ""))
    return out


def required_cells(prof: dict, sp: dict | None = None) -> dict[str, list[str]]:
    """{cell: [sets]} the routed scale needs a difficulty for (``scale.cell_of`` naming, packed
    ``:x<n>`` forms included): every locator set's cells, and every module set's cells in a part
    that scores (max > 0) and is not a routed pack. A measured 0-point part's cells are not
    required (the scale reports them, it does not score on them)."""
    sp = sp or spec(prof)
    if sp is None:
        return {}
    loc = set(sp["locator"])
    mods = {s for m in sp["modules"] for s in m["sets"]}
    packs = {k for m in sp["modules"] for k in m["packs"]}
    scores = (prof or {}).get("scores") or {}

    def scores_points(part: str) -> bool:
        sec, _, pk = part.partition("/")
        ps = ((scores.get(sec) or {}).get("parts") or {}).get(pk) or {}
        return part not in packs and float(ps.get("max", ps.get("weight", 0)) or 0) > 0

    out: dict[str, list[str]] = {}
    # per CASE, not per set (G2 2026-11-14): a set of 5 items packed in twos has a single-item tail
    # prompt whose cell is ``istack:L2``, not ``istack:L2:x2``; each case's cell is scale.cell_of's
    for c in chains.chain_cases(prof):
        cs = c.get("chain_spec") or {}
        name, part = cs.get("ladder"), cs.get("part", "")
        if not (name in loc or (name in mods and scores_points(part))):
            continue
        for rg in cs.get("rungs") or []:
            cell = scale.cell_of({"family": rg.get("family"), "level": rg.get("level"),
                                  "kind": cs.get("kind"), "items": cs.get("items")})
            if name not in out.setdefault(cell, []):
                out[cell].append(name)
    return out


def calibrated(cal: dict, cell: str) -> bool:
    """A cell has a difficulty: its own row (or trend), or a row for every tier group of it
    (scale tier cells: a tiered unit is scored per group)."""
    if scale.item(cal, cell) is not None:
        return True
    return all(scale.item(cal, f"{cell}:{g}") is not None for g in scale.tier_groups(cal))


def check_calibration(prof: dict | None, base: Path | None = None) -> None:
    """Fail closed at load (lead 2026-11-14, D's 8B route-only pass): every cell the routed scale
    needs (:func:`required_cells`) has a difficulty in the calibration file the routing names;
    otherwise ConfigError naming the missing cells and the file, before a model is loaded or a
    locator item is run. An unreadable file is refused the same way."""
    sp = spec(prof)
    if sp is None:
        return
    try:
        cal, _ = calibration(sp, base)
    except Exception as e:  # noqa: BLE001 - missing / unparsable / empty table: refused at load
        raise ConfigError(f"profile {(prof or {}).get('name')}: routing.calibration {sp['calibration']}: "
                          f"cannot be read ({e})") from e
    need = required_cells(prof, sp)
    miss = sorted(c for c in need if not calibrated(cal, c))
    if miss:
        loc = set(sp["locator"])
        raise ConfigError(
            f"profile {(prof or {}).get('name')}: {len(miss)} cell(s) the routed scale needs have no difficulty in "
            f"{sp['calibration']}: " + "; ".join(
                f"{c} ({'locator' if loc & set(need[c]) else 'module'}: {', '.join(sorted(set(need[c])))})"
                for c in miss) + " -- never routed on an uncalibrated cell")


def uncalibrated(cases: list[dict], sp: dict, cal: dict) -> list[str]:
    """Locator cells the table cannot place: routing on them is refused (never route on an
    uncalibrated cell -- its units would silently drop out of the locator ability)."""
    return sorted(c for c in locator_cells(cases, sp) if scale.item(cal, c) is None)


def _same_spec(route: dict, sp: dict, cal_id: dict) -> bool:
    """A stored route belongs to this routing when its cuts, modules and calibration file
    are this profile's (else it is an earlier version's route, never reused)."""
    names = {m["name"] for m in sp["modules"]}
    return (list(route.get("cuts") or []) == sp["cuts"]
            and all(m.get("name") in names for m in route.get("modules") or [])
            and (route.get("calibration") or {}).get("sha256") == cal_id["sha256"])


def run_id(rows: list[dict], sp: dict) -> str | None:
    """The one bench_run_id of a routed run (both stages carry it): the stored module rows'
    run, else the newest locator row's run; None = a new run."""
    rs = [r for r in rows if isinstance(r.get("route"), dict) and r.get("bench_run_id")]
    if rs:
        return rs[-1]["bench_run_id"]
    loc = [r for r in rows if (r.get("chain") or {}).get("ladder") in set(sp["locator"]) and r.get("bench_run_id")]
    return loc[-1]["bench_run_id"] if loc else None


def plan(rows: list[dict], cases: list[dict], prof: dict, base: Path | None = None) -> dict:
    """What a model runs next: {"stage": "locator", "cases", "bench_run_id"} while a locator
    unit has no graded verdict; else {"stage": "module", "cases", "route", "bench_run_id"}.
    Both stages are ONE run (one bench_run_id). A resumed run keeps its stored route (never
    re-routed from a re-estimated locator: ``reused``). Fails closed: an ability that cannot
    be estimated (no calibrated locator unit) routes nowhere (stage "blocked")."""
    sp = spec(prof)
    rid = run_id(rows, sp)
    stored = route_of(rows)
    cal, cal_id = calibration(sp, base)
    bad = uncalibrated(cases, sp, cal)
    if bad:
        return {"stage": "blocked", "cases": [], "bench_run_id": rid,
                "why": f"locator cell(s) {', '.join(bad)} not calibrated in {cal_id['path']}: never routed on"}
    if stored and _same_spec(stored, sp, cal_id):
        route = {k: v for k, v in stored.items() if k not in ("module", "share")}
        route["reused"] = True
        return {"stage": "module", "cases": stage_cases(cases, sp, route), "route": route,
                "bench_run_id": rid}
    loc_ids = {c["id"] for c in cases if c.get("chain_spec") and c["chain_spec"]["ladder"] in sp["locator"]}
    graded = {r["chain"]["id"] for r in rows if r.get("chain") and r["chain"].get("passed") is not None
              and r["chain"].get("id") in loc_ids}
    if loc_ids - graded:
        return {"stage": "locator", "cases": stage_cases(cases, sp), "pending": sorted(loc_ids - graded),
                "bench_run_id": rid}
    a = locator_ability(rows, sp, cal, prof)
    if a.get("theta") is None:
        return {"stage": "blocked", "cases": [], "why": "no calibrated locator unit: cannot route"}
    ch = choose(a["theta"], a.get("se"), sp)
    route = {"rule": "score", "theta": round(a["theta"], 4), "se": round(a["se"], 4),
             "locator_units": a["units"], "extreme": a.get("extreme"), "cuts": sp["cuts"],
             "section": sp["section"], "calibration": cal_id, **ch}
    return {"stage": "module", "cases": stage_cases(cases, sp, route), "route": route, "bench_run_id": rid}


def route_of(rows: list[dict]) -> dict | None:
    """The route a model's stored rows record (newest module row), or None."""
    rs = [r["route"] for r in rows if isinstance(r.get("route"), dict)]
    return rs[-1] if rs else None


def taken(cases: list[dict], prof: dict, route: dict | None) -> set[str] | None:
    """Case ids on a model's route (locator + unrouted sets + its module units), or None
    when the profile has no routing or the model has no route yet."""
    sp = spec(prof)
    if sp is None or route is None:
        return None
    return {c["id"] for c in stage_cases(cases, sp)} | {c["id"] for c in stage_cases(cases, sp, route)}


def lint(name: str = "bench45") -> list[tuple[str, str]]:
    """[(composition, message)] for every routed composition of a profile: "ok", or why a run of it
    would be refused at load (:func:`check_calibration`, one ran-out rule per cell). A warning list,
    not a refusal: only a composition a run names is refused (profiles.apply_profile)."""
    import os
    from . import profiles as _profiles
    from .config import ROOT_DIR
    out = []
    old = os.environ.get(_profiles.COMPOSITION_ENV)
    try:
        os.environ.pop(_profiles.COMPOSITION_ENV, None)
        comps = sorted((_profiles.load_profile(name).get("compositions") or {}))
        for comp in ["default", *comps]:
            os.environ[_profiles.COMPOSITION_ENV] = comp
            try:
                prof = _profiles.load_profile(name)
                if not prof.get("routing"):
                    continue
                check_calibration(prof, ROOT_DIR)
                scale.ran_out_rules(prof)
                out.append((comp, "ok"))
            except ConfigError as e:
                out.append((comp, str(e)))
    finally:
        if old is None:
            os.environ.pop(_profiles.COMPOSITION_ENV, None)
        else:
            os.environ[_profiles.COMPOSITION_ENV] = old
    return out


def main(argv: list[str] | None = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="python -m crucibleforge.routing",
                                 description="lint: every routed composition's calibration and ran-out rules")
    ap.add_argument("cmd", choices=("lint", "cells"))
    ap.add_argument("--profile", default="bench45")
    ap.add_argument("--composition", help="cells: the routed composition whose required cells to print")
    a = ap.parse_args(argv)
    if a.cmd == "lint":
        res = lint(a.profile)
        for comp, msg in res:
            print(f"{'ok  ' if msg == 'ok' else 'WARN'} {comp}: {msg if msg != 'ok' else ''}".rstrip())
        return 1 if any(m != "ok" for _, m in res) else 0
    import os
    from . import profiles as _profiles
    from .config import ROOT_DIR
    if a.composition:
        os.environ[_profiles.COMPOSITION_ENV] = a.composition
    prof = _profiles.load_profile(a.profile)
    sp = spec(prof)
    if sp is None:
        print("not a routed composition")
        return 2
    cal, _ = calibration(sp, ROOT_DIR)
    need = required_cells(prof, sp)
    print(f"{len(need)} cells required by {prof.get('composition')} (routing.calibration = {sp['calibration']})")
    for c in sorted(need):
        kind = "locator" if set(need[c]) & set(sp["locator"]) else "module"
        ok = calibrated(cal, c)
        print(f"  {c:22s} {kind:7s} {'calibrated' if ok else 'MISSING':10s} {', '.join(sorted(set(need[c])))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
