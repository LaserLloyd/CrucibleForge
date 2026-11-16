"""Weight <-> test rule (SYSTEM-45 section 3): the profile and the MakoBench lab registry must agree.

The lab registry is a folder of ``items/<NAME>.json`` files (the ``mblab item`` tool), one per test item,
with ``pack`` (smoke | main | bonus | candidate | retired), ``section`` (work | chat | nsfw), ``points`` and,
when measured, ``minutes_measured`` (whole-item minutes on the reference model, SYSTEM-45 section 5).

The profile names the registry item of every scored unit:

* a Main part with points carries ``item: <NAME>`` (its points = the part's ``max``);
* a bonus pack set file carries ``"item": "<NAME>"`` (its points = the set's ``points``).

:func:`check` fails closed: a Main part or a discovered bonus set with no ``item``, an item missing from the
registry, a pack / section / points disagreement, or a registry item marked ``main`` / ``bonus`` that the
profile does not use is an error. Minutes are never required (they are filled by measured runs): an item
without them prints as "unmeasured", never as 0. :func:`table` prints, per section, the points, the measured
minutes and each item's minutes share against its points share.

CLI: ``crucibleforge registry-check [--lab DIR] [--profile bench45]``; the lab dir defaults to
``$CF_LAB_DIR``, then ``~/Documents/Research/makobench-lab``.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

LAB_ENV = "CF_LAB_DIR"
DEFAULT_LAB = Path.home() / "Documents" / "Research" / "makobench-lab"
#: SYSTEM-45 3.2 (start): an item may take at most this multiple of its points share of the section's minutes
MAX_SHARE_RATIO = 2.0
SECTIONS = ("work", "chat", "nsfw")


def lab_dir(explicit: str | os.PathLike | None = None) -> Path:
    if explicit:
        return Path(explicit)
    env = os.environ.get(LAB_ENV, "").strip()
    return Path(env) if env else DEFAULT_LAB


def load_registry(lab: Path) -> dict[str, dict]:
    d = Path(lab) / "items"
    if not d.is_dir():
        raise FileNotFoundError(f"lab registry not found: {d} (set ${LAB_ENV} or pass --lab)")
    return {f.stem: json.loads(f.read_text(encoding="utf-8")) for f in sorted(d.glob("*.json"))}


def profile_units(prof: dict) -> list[dict]:
    """Every scored unit of the profile: Main parts with points and discovered bonus sets.
    Each = {kind: main|bonus, section, where, item, points}."""
    from . import ladder
    out = []
    for sec, spec in (prof.get("scores") or {}).items():
        for pk, p in ((spec or {}).get("parts") or {}).items():
            if float(p.get("max") or 0) > 0:
                out.append({"kind": "main", "section": sec, "where": f"{sec}/{pk}",
                            "item": p.get("item"), "points": float(p["max"])})
    for key, seq in ladder.discover(prof).items():
        sec = key.split("/", 1)[0]
        for s in seq:
            out.append({"kind": "bonus", "section": sec, "where": f"{key}#{s['n']} ({Path(s['path']).name})",
                        "item": s.get("item"), "points": float(s.get("points", 1.0))})
    return out


def check(prof: dict, registry: dict[str, dict]) -> list[str]:
    """The disagreements (empty = profile and registry agree)."""
    errs: list[str] = []
    units = profile_units(prof)
    by_item: dict[tuple[str, str], list[dict]] = {}
    for u in units:
        if not u["item"]:
            errs.append(f"{u['where']}: no 'item' naming its lab registry entry")
            continue
        by_item.setdefault((u["item"], u["kind"]), []).append(u)
    for (name, kind), us in sorted(by_item.items()):
        it = registry.get(name)
        if it is None:
            errs.append(f"{name}: used by {', '.join(u['where'] for u in us)} but not in the lab registry")
            continue
        if it.get("pack") != kind:
            errs.append(f"{name}: registry pack {it.get('pack')!r}, profile uses it as {kind!r}")
        secs = {u["section"] for u in us}
        if it.get("section") not in secs or len(secs) != 1:
            errs.append(f"{name}: registry section {it.get('section')!r}, profile section(s) {sorted(secs)}")
        pts = sum(u["points"] for u in us)
        if it.get("points") is None or abs(float(it["points"]) - pts) > 1e-9:
            errs.append(f"{name}: registry points {it.get('points')!r}, profile points {pts:g}")
    used = {n for n, _k in by_item}
    for name, it in sorted(registry.items()):
        if it.get("pack") in ("main", "bonus") and name not in used:
            errs.append(f"{name}: registry says pack {it['pack']!r} but the profile does not use it")
    return errs


def table(prof: dict, registry: dict[str, dict]) -> str:
    """Per section and kind: item, points, measured minutes, minutes share vs points share."""
    units = profile_units(prof)
    lines = []
    for kind in ("main", "bonus"):
        for sec in SECTIONS:
            agg: dict[str, float] = {}
            for u in units:
                if u["kind"] == kind and u["section"] == sec and u["item"]:
                    agg[u["item"]] = agg.get(u["item"], 0.0) + u["points"]
            if not agg:
                continue
            tot_pts = sum(agg.values())
            mins = {n: (registry.get(n) or {}).get("minutes_measured") for n in agg}
            known = [m for m in mins.values() if m is not None]
            tot_min = sum(known) if len(known) == len(mins) else None
            lines.append(f"{kind} {sec}: {tot_pts:g} points, "
                         + (f"{tot_min:g} min measured" if tot_min is not None else
                            f"minutes: {len(known)} of {len(mins)} items measured"))
            lines.append(f"  {'item':<16} {'points':>6} {'pts%':>5} {'minutes':>11} {'min%':>6}  verdict")
            for n, pts in agg.items():
                m = mins[n]
                if m is None:
                    ms, share, verdict = "unmeasured", "-", "unmeasured"
                elif tot_min:
                    sh = m / tot_min
                    ms, share = f"{m:g}", f"{100 * sh:.0f}%"
                    verdict = ("OVER (>2x its points share)" if sh > MAX_SHARE_RATIO * pts / tot_pts else "ok")
                else:
                    ms, share, verdict = f"{m:g}", "-", "section not fully measured"
                lines.append(f"  {n:<16} {pts:>6g} {100 * pts / tot_pts:>4.0f}% {ms:>11} {share:>6}  {verdict}")
    return "\n".join(lines)


def main(prof_name: str = "bench45", lab: str | None = None, cfg: dict | None = None) -> int:
    from .profiles import load_profile
    prof = load_profile(prof_name, cfg)
    reg = load_registry(lab_dir(lab))
    print(table(prof, reg))
    errs = check(prof, reg)
    for e in errs:
        print(f"MISMATCH {e}")
    print(f"registry-check: {'FAIL' if errs else 'OK'} ({len(errs)} mismatch(es)), lab {lab_dir(lab)}")
    return 1 if errs else 0
