"""Adaptive chains (4.5 Main, rc1; Admin G's design EXP-20261113-20, docs/CHAINS.md).

A Main section (a score part with points) may be built from many short, independent
CHAINS instead of fixed cases. A chain climbs a ladder of rungs, easy to hard, with ONE
freshly seeded, code-graded instance per rung:

* rung 1, 2, 3 ... are presented in order; a pass (score >= the rung's ``pass``,
  default 0.5) climbs to the next rung;
* on a failure the chain may use its slip guard (``slip_guard: 1`` = one retry per
  chain): ONE fresh instance of the same rung (attempt 2). A pass there climbs on as if
  the slip had not happened; a failure ends the chain. Any failure once the guard is
  spent ends the chain;
* the chain score is ``held / K``: the highest rung held (passed on its first try or on
  the retry) over the ladder's number of rungs. Rungs above the end are "not reached"
  (a state, never missing rows). Score never rises by failing: a fail only ever ends
  the climb or spends the guard (property-tested);
* a section's share = the mean chain score; its points = share x the part's ``max``.
  Unequal rung costs do not change points: points follow level held.

Instances are PAIRED: the generator key of (ladder, chain c, rung r, attempt a) is a pure
function of the release seed, so every model sees the same instance at the same step.

Effort is limited by turns, never by tokens or time (R23): a chain row is sent the model's
own maximum (the served window minus the prompt) and a reply cut off there is a failed
attempt (the early stop counts it as token_limit). Every attempt is a row with its tokens,
wall-clock and cost.

Profile syntax (``chains:`` keyed by the ``<score>/<part>`` the chains score)::

    chains:
      work/main:
        chains: 75          # the sizing lever: chains in this section (round-robin over ladders)
        slip_guard: 1       # retries per chain (default 1; 0 = stop at the first failure)
        pass: 0.5           # default pass threshold of every rung
        ladders:
          planted_opt: [{family: planted_opt, level: 2}, {family: planted_opt, level: 4}]
          mixed: [{family: keyfunc, level: 3}, {family: patchfix, level: 7, pass: 0.6}]

A rung may name ``variant`` and ``category`` (default: the part's first scoring category).
Every rung must be code-graded: a rung whose instance needs a judge is refused when the
profile is applied for a run (fail closed).
"""
from __future__ import annotations

import copy
import functools
import hashlib
import inspect
import json
import math
import re
import threading
from pathlib import Path

from .config import ConfigError

DEFAULT_PASS = 0.5
#: section SE unit: "element" (each element independent), "prompt" (the unit = a chain, a
#: flat item or a multi-item prompt; cluster-robust), "auto" = element while the measured
#: within-unit correlation (ICC) is below ``icc_max``, else prompt
SE_UNITS = ("auto", "element", "prompt")
DEFAULT_ICC_MAX = 0.1
DEFAULT_SLIP_GUARD = 0          # lead 2026-11-13 (G LEVERS.md): a retry is another costly failure
#: margin of error multiplier (two-sided 95 %) on the standard error of the section mean
Z95 = 1.96
#: the PRINTED margin (one level everywhere, as scale.py and the tie rule): 90 %, 1.645 x SE
Z90 = 1.645


# --------------------------------------------------------------------------- profile

def _part_spec(prof: dict, key: str) -> dict | None:
    score, _, part = str(key).partition("/")
    return ((((prof.get("scores") or {}).get(score) or {}).get("parts") or {}).get(part)
            if part else None)


def _pos_int(v, what: str, where: str, zero: bool = False) -> int:
    if not isinstance(v, int) or isinstance(v, bool) or v < (0 if zero else 1):
        raise ConfigError(f"{where}: {what} is a {'non-negative' if zero else 'positive'} integer")
    return v


def _threshold(v, where: str) -> float:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not 0 < float(v) <= 1:
        raise ConfigError(f"{where}: pass is a number in (0, 1]")
    return float(v)


#: ``ran_out`` per cell: ``zero`` (default: a "ran out" element scores the credit it earned,
#: 0 for an item never reached -- the ceiling is shown lossless) or ``exclude`` (the ceiling is
#: unvalidated or known to cut real work: reported, not scored as a failed answer)
RAN_OUT_RULES = ("zero", "exclude")
#: a set whose ran-out share is above this is flagged "ceiling too low"
RAN_OUT_FLAG = 0.20

#: difficulty bands of the 4.5 bank (BANK-DESIGN §3a; Top = the bonus packs)
BANDS = ("low", "mid", "high", "top")
_CONCEPT_RE = re.compile(r"^[A-Z][0-9]+$")


def _tags(r: dict, rw: str) -> dict:
    """A cell's bank tags: ``concept`` (a blueprint row id: C1, H2, N3, R1 ...) and ``band``
    (low / mid / high / top). Either may be absent here; a tagged profile must tag every cell
    (``_require_tags``)."""
    out = {}
    if r.get("concept") is not None:
        c = r["concept"]
        if not isinstance(c, str) or not _CONCEPT_RE.match(c):
            raise ConfigError(f"{rw}: concept is a blueprint id: a capital letter and digits (C1, H2, N3)")
        out["concept"] = c
    if r.get("band") is not None:
        if r["band"] not in BANDS:
            raise ConfigError(f"{rw}: band is one of {list(BANDS)}")
        out["band"] = r["band"]
    return out


def _ran_out(r: dict, rw: str) -> dict:
    v = r.get("ran_out")
    if v is None or v == "zero":
        return {}                  # the default is not stored: unchanged revisions stay byte-identical
    if v not in RAN_OUT_RULES:
        raise ConfigError(f"{rw}: ran_out is one of {list(RAN_OUT_RULES)}")
    return {"ran_out": v}


def _rung(r, rw: str, cat0, thr: float, ceil: dict | None, extra=()) -> dict:
    from . import gen as _gen
    if not isinstance(r, dict) or not r.get("family"):
        raise ConfigError(f"{rw}: a mapping with family and level")
    bad = set(r) - {"family", "level", "variant", "category", "pass", "ceiling", "concept", "band",
                    "ran_out", *extra}
    if bad:
        raise ConfigError(f"{rw}: unknown key(s) {sorted(bad)} (an item carries no budget, R23; "
                          f"its output ceiling is `ceiling`)")
    fam, var = str(r["family"]), r.get("variant")
    lv = _pos_int(r.get("level"), "level", rw)
    try:
        mod = _gen.get(fam)
        _gen.check_variant(mod, {"type": fam, "variant": var})
    except _gen.GeneratorError as e:
        raise ConfigError(f"{rw}: {e}") from e
    levels = getattr(mod, "LEVELS", None)
    if levels and all(isinstance(k, str) for k in levels):
        # per-variant level tables (attend: {"host": {1: ..., 6: ...}})
        levels = levels.get(var or next(iter(levels)), {})
    if levels and lv not in levels:
        raise ConfigError(f"{rw}: {fam} has no level {lv} (levels {sorted(levels)})")
    cat = r.get("category") or cat0
    if not cat:
        raise ConfigError(f"{rw}: give a category (the part names no scoring category)")
    rc = r.get("ceiling", (ceil or {}).get(fam, (ceil or {}).get("default")))
    if rc is not None:
        _pos_int(rc, "ceiling", rw)
    return {"family": fam, "variant": var, "level": lv, "category": cat,
            "pass": _threshold(r.get("pass", thr), rw), "ceiling": rc, **_tags(r, rw), **_ran_out(r, rw)}


def _name(n, w: str, seen: set) -> None:
    if not isinstance(n, str) or not n.replace("_", "").isalnum():
        raise ConfigError(f"{w}: a ladder / flat-set name is letters, digits and _")
    if n in seen:
        raise ConfigError(f"{w}: ladder and flat-set names are unique across the profile (they key the seeds)")
    seen.add(n)


#: keys a flat item may carry beside the rung keys
_FLAT_KEYS = ("count", "levels", "items_per_prompt", "element", "ceiling_per_turn", "name", "deal", "drop",
              "element_weight", "average", "seed_as", "history_budget", "gen_seed")
#: element weights: "one" (every element weighs 1, the default) or "check" (each element
#: weighs its grader check's own weight, e.g. the IMPLIED family's 2.0 / 1.0 / 0.25)
ELEMENT_WEIGHTS = ("one", "check")
ELEMENTS = ("item", "turn", "check")
#: rc3: writer seeds a flat set may name (runner.CREATIVE_SEEDS has five)
GEN_SEEDS = 5
#: rc3 judged parts: how the judge component is read off the verdict. "rubric" = one judge element per
#: unit, the mean of the listed 0-10 axes / 10 (``sexual_axes`` only on a card whose brief has sex)
JUDGE_MODE_KINDS = ("rubric", "ladder")
#: 2026-11-15 (lead): ``kind: ladder`` = the role-play sessions' judged half is where the session sits on its card's
#: ladder of fixed example sessions (crucibleforge/rpladder: pair judge, both orders), position / 4
LADDER_MODE_KEYS = ("kind", "ladder", "windows", "code_weight", "max_tokens")
#: the board's line for a ladder part (rpladder.BOARD_TEXT; SPEC "What the score is")
LADDER_BOARD_TEXT = "Where the model's sessions sit on a ladder of fixed example sessions."
#: rc3 rubric mode: how a piece's live axes become its score. "mean" = every listed axis must be on the
#: verdict, piece = their mean / 10 (N2). "weakest" (CHAT-RP 2026-11-14, EXP-20261114-35 variant (d)) =
#: ``axes`` lists every mark a card's rubric MAY ask, minus the profile's ``dead_axes`` (marks every capable
#: model maxes); the piece is the LOWEST of the live marks the verdict carries (a mark the card's rubric
#: does not ask is skipped; no live mark at all = pending). A scene is as good as its weakest side.
JUDGE_COMBINES = ("mean", "weakest")
#: rc3: the SE cluster of a judged part. "unit" = each piece; "card" = the pieces of one card (a set and
#: its ``seed_as`` aliases share unit i), so two seeds are averaged inside a card first
SE_CLUSTERS = ("unit", "card")
#: a multi-item prompt's ceiling = min(N x the per-item ceiling, this) (COMPOSE.md rc1-night)
MAX_PROMPT_CEILING = 65536


def _flat_sets(flat, where: str, cat0, thr: float, ceil: dict | None, seen: set) -> list[dict]:
    """The flat sets of a part. ``flat`` is a mapping {set name: item} or a list of items
    (a list item's name = its ``name``, else ``<family>[_<variant>]``). An item gives
    ``level`` + ``count``, or ``levels: {level: count, ...}`` (one set per level, named
    ``<name>_L<level>``), and optionally ``items_per_prompt`` (N sub-items answered in one
    prompt, each its own element), ``element: item | turn`` (``turn``: each graded turn of a
    multi-turn item is its own element), ``ceiling`` (per item; a multi-item prompt gets
    min(N x ceiling, 65,536)), ``ceiling_per_turn`` (per request of a multi-turn item) and
    ``history_budget`` (tokens: the room a multi-turn item's accumulated history may take;
    the model is loaded at ceiling + history_budget + the 8,192 pad, see runner.fixed_budget)."""
    if not flat:
        return []
    if isinstance(flat, dict):
        entries = [(str(k), v, f"{where}.flat.{k}") for k, v in flat.items()]
    elif isinstance(flat, list):
        entries = []
        for i, v in enumerate(flat):
            if not isinstance(v, dict) or not v.get("family"):
                raise ConfigError(f"{where}.flat[{i}]: a mapping with family and level(s)")
            nm = v.get("name") or (str(v["family"]) + (f"_{v['variant']}" if v.get("variant") else ""))
            entries.append((str(nm), v, f"{where}.flat[{i}]"))
    else:
        raise ConfigError(f"{where}: flat maps a set name to {{family, level, count}} (or is a list of them)")
    out = []
    for fname, item, fw in entries:
        if not isinstance(item, dict):
            raise ConfigError(f"{fw}: a mapping with family and level(s)")
        if "levels" in item:
            if "level" in item or "count" in item:
                raise ConfigError(f"{fw}: give levels: {{level: count}} or level + count, not both")
            lv = item["levels"]
            if not isinstance(lv, dict) or not lv:
                raise ConfigError(f"{fw}: levels maps a level to its count")
            per = []
            for k, c in lv.items():
                if isinstance(c, dict):
                    # a level with its own tags: {level: {count, concept?, band?}}
                    bad = set(c) - {"count", "concept", "band", "ran_out"}
                    if bad:
                        raise ConfigError(f"{fw}: levels[{k}] takes count, concept, band and ran_out ({sorted(bad)})")
                    per.append((f"{fname}_L{k}", {**item, "level": k, **c}))
                else:
                    per.append((f"{fname}_L{k}", {**item, "level": k, "count": c}))
        else:
            per = [(fname, item)]
        n = _pos_int(item.get("items_per_prompt", 1), "items_per_prompt", fw)
        element = item.get("element", "item")
        if element not in ELEMENTS:
            raise ConfigError(f"{fw}: element is one of {list(ELEMENTS)}")
        if element == "turn" and n > 1:
            raise ConfigError(f"{fw}: element: turn is a multi-turn item; it cannot be packed "
                              f"into a multi-item prompt")
        cpt = item.get("ceiling_per_turn")
        if cpt is not None:
            _pos_int(cpt, "ceiling_per_turn", fw)
        ew = item.get("element_weight")
        if ew is not None:
            if ew not in ELEMENT_WEIGHTS:
                raise ConfigError(f"{fw}: element_weight is one of {list(ELEMENT_WEIGHTS)}")
            if ew == "check" and element != "check":
                raise ConfigError(f"{fw}: element_weight: check needs element: check")
        hb = item.get("history_budget")
        if hb is not None:
            _pos_int(hb, "history_budget", fw)
            if n > 1:
                raise ConfigError(f"{fw}: history_budget is for a multi-turn item; a multi-item "
                                  f"prompt is one request")
        favg = item.get("average")
        if favg is not None and favg not in ("element", "unit"):
            raise ConfigError(f"{fw}: average is element or unit")
        for sname, it0 in per:
            sw = f"{fw}[{sname}]" if sname != fname else fw
            _name(sname, sw, seen)
            core = {k: v for k, v in it0.items() if k not in ("levels", "name")}
            it = _rung(core, sw, cat0, thr, ceil, extra=_FLAT_KEYS)
            per_item = it["ceiling"]
            if cpt is not None and n == 1:
                it["ceiling"] = cpt              # the per-request ceiling of a multi-turn item
            if n > 1 and per_item is not None:
                it["ceiling"] = min(n * int(per_item), MAX_PROMPT_CEILING)
            it["item_ceiling"] = per_item
            if hb is not None:
                it["history_budget"] = int(hb)  # load floor: ceiling + history (runner.fixed_budget)
            if item.get("deal"):
                it["deal"] = True              # disjoint hands of one release-keyed partition (unc_know L5)
            if item.get("gen_seed") is not None:
                # rc3: the writer's sampling seed is the runner's creative seed number gen_seed (1..5)
                # instead of the repeat's; with seed_as it re-asks the SAME instances at a second seed
                gs = _pos_int(item["gen_seed"], "gen_seed", sw)
                if gs > GEN_SEEDS:
                    raise ConfigError(f"{sw}: gen_seed is 1..{GEN_SEEDS}")
                it["gen_seed"] = gs
            if item.get("drop"):
                if not isinstance(item["drop"], list):
                    raise ConfigError(f"{sw}: drop is a list of generator card ids")
                it["exclude"] = sorted(str(x) for x in item["drop"])
            for k_ in ("element_weight", "average", "seed_as"):
                it.pop(k_, None)
            fo = {"name": sname, "count": _pos_int(it0.get("count"), "count", sw),
                  "n": n, "element": element, "item": it}
            sa = item.get("seed_as")
            if sa is not None:
                if not isinstance(sa, str) or not sa.strip():
                    raise ConfigError(f"{sw}: seed_as is the name of another flat set")
                # a levels-form set looks for the target's set of the same level first
                fo["seed_as"] = [f"{sa}_L{it0['level']}", sa] if "levels" in item else [sa, f"{sa}_L{it0['level']}"]
                fo["_where"] = sw
            if ew is not None:
                fo["element_weight"] = ew
            if favg is not None:
                fo["average"] = favg
            out.append(fo)
    return out


def _components(spec, where: str, mx: float) -> list[dict] | None:
    """``components: {name: {points, source: judge | code, groups: [check group, ...]}}`` (a bare
    number = points, source code). The points must add up to the part's max. A judge element
    goes to the judge component; a code element to the first code component whose ``groups``
    holds its check group, else to the code component without ``groups``. None = no
    components (the part is the plain mean of its elements)."""
    if spec is None:
        return None
    if not isinstance(spec, dict) or not spec:
        raise ConfigError(f"{where}: components maps a name to {{points, source, groups}}")
    out = []
    for name, c in spec.items():
        c = {"points": c} if isinstance(c, (int, float)) and not isinstance(c, bool) else c
        if not isinstance(c, dict) or isinstance(c.get("points"), bool) \
                or not isinstance(c.get("points"), (int, float)) or c["points"] <= 0:
            raise ConfigError(f"{where}: components.{name} needs positive points")
        src = c.get("source", "code")
        if src not in ("judge", "code"):
            raise ConfigError(f"{where}: components.{name}.source is judge or code")
        groups = c.get("groups")
        if groups is not None and (not isinstance(groups, list) or src != "code"):
            raise ConfigError(f"{where}: components.{name}.groups is a list (code components only)")
        out.append({"name": str(name), "points": float(c["points"]), "source": src,
                    "groups": [str(g) for g in groups] if groups else None})
    if sum(1 for c in out if c["source"] == "judge") > 1 or \
            sum(1 for c in out if c["source"] == "code" and not c["groups"]) > 1:
        raise ConfigError(f"{where}: at most one judge component and one code component without groups")
    tot = sum(c["points"] for c in out)
    if abs(tot - mx) > 1e-6:
        raise ConfigError(f"{where}: components add up to {tot:g} points, the part's max is {mx:g}")
    return out


def component_of(e: dict, comps: list[dict] | None) -> str | None:
    """The component an element counts in (None = in no component)."""
    if not comps:
        return "all"
    if e.get("source") == "judge":
        return next((c["name"] for c in comps if c["source"] == "judge"), None)
    g = e.get("group")
    hit = next((c["name"] for c in comps if c["source"] == "code" and c["groups"] and g in c["groups"]), None)
    return hit or next((c["name"] for c in comps if c["source"] == "code" and not c["groups"]), None)


def _eng_nsfw(row: dict) -> float:
    """NSFW engagement (report.nsfw_gated_row, Admin N2's rc2 split): 0 for a refusal / empty
    reply / content filter, W/4 with a willingness verdict, SANITIZED_ENGAGEMENT for a
    sanitized row, else 1. Multiplies every element of the unit."""
    from . import report as _r
    if _r._refused_row(row) or _r.filtered_row(row):
        return 0.0
    return float(_r.nsfw_gated_row(row)["engagement"])


#: unit-level multipliers by name (section ``engagement:``), applied on the board to every
#: element of a unit: ``f(final row) -> 0..1``
ENGAGEMENT = {"nsfw": _eng_nsfw}


def sections(prof: dict | None) -> list[dict]:
    """The profile's generated Main parts, validated (structure, families, levels, variants;
    no materialising). ``[{part, chains, slip_guard, pass, start, ladders: [{name, rungs}],
    flat: [{name, count, item}]}]`` in profile order; an item / rung = {family, variant,
    level, category, pass, ceiling}. A part has ``ladders`` (chains), ``flat`` (independent
    seeded instances) or both. Raises ConfigError."""
    block = (prof or {}).get("chains")
    if not block:
        return []
    name = (prof or {}).get("name")
    if not isinstance(block, dict):
        raise ConfigError(f"profile {name}: chains maps '<score>/<part>' to a chain section")
    out, seen = [], set()
    for key, sec in block.items():
        where = f"profile {name}: chains.{key}"
        part = _part_spec(prof, key)
        if part is None:
            raise ConfigError(f"{where}: no such score part")
        if not isinstance(sec, dict):
            raise ConfigError(f"{where}: a mapping with chains + ladders and/or flat")
        unknown = set(sec) - {"chains", "slip_guard", "pass", "ladders", "flat", "start", "ceiling",
                              "se_unit", "icc_max", "judged", "engagement", "components", "average",
                              "element_weight", "judge_gate", "judge_mode", "se_cluster",
                              "scorer", "view_of", "view_sets"}
        if unknown:
            raise ConfigError(f"{where}: unknown key(s) {sorted(unknown)}")
        lads, flat = sec.get("ladders"), sec.get("flat")
        scorer, view_of = sec.get("scorer"), sec.get("view_of")
        if scorer is not None:
            from . import adult45 as _adult
            if scorer not in _adult.SCORERS:
                raise ConfigError(f"{where}: scorer is one of {sorted(_adult.SCORERS)}")
        if view_of is not None:
            # 4.5 adult page (N3-WIRE): a VIEW scores another part's units again (no generation of its
            # own beyond its flat sets); its units resample with the source's (same unit ids, same mult)
            if not scorer:
                raise ConfigError(f"{where}: view_of needs a scorer")
            if view_of == key or view_of not in block:
                raise ConfigError(f"{where}: view_of names another part of this chains block")
            vs = sec.get("view_sets")
            if vs is not None and (not isinstance(vs, list) or not all(isinstance(x, str) for x in vs)):
                raise ConfigError(f"{where}: view_sets is a list of the source part's set names")
        elif sec.get("view_sets") is not None:
            raise ConfigError(f"{where}: view_sets needs view_of")
        if not lads and not flat and not view_of:
            raise ConfigError(f"{where}: give ladders (chains) and/or flat (seeded instances)")
        n = _pos_int(sec.get("chains"), "chains", where) if lads else 0
        slip = _pos_int(sec.get("slip_guard", DEFAULT_SLIP_GUARD), "slip_guard", where, zero=True)
        thr = _threshold(sec.get("pass", DEFAULT_PASS), where)
        start = _start_block(sec, where)
        ceil = sec.get("ceiling")
        if ceil is not None and not isinstance(ceil, dict):
            ceil = {"default": ceil}
        for k_, v in (ceil or {}).items():
            _pos_int(v, f"ceiling[{k_}]", where)
        cat0 = (part.get("scoring") or [None])[0]
        ladders = []
        if lads:
            if not isinstance(lads, dict):
                raise ConfigError(f"{where}: ladders maps a ladder name to its list of rungs")
            for lname, rungs in lads.items():
                lw = f"{where}.ladders.{lname}"
                _name(lname, lw, seen)
                if not isinstance(rungs, list) or not rungs:
                    raise ConfigError(f"{lw}: a non-empty list of rungs")
                ladders.append({"name": lname, "rungs": [_rung(r, f"{lw}[{i}]", cat0, thr, ceil)
                                                        for i, r in enumerate(rungs, 1)]})
        flats = _flat_sets(flat, where, cat0, thr, ceil, seen)
        se_unit = sec.get("se_unit", "auto")
        if se_unit not in SE_UNITS:
            raise ConfigError(f"{where}: se_unit is one of {list(SE_UNITS)}")
        icc_max = sec.get("icc_max", DEFAULT_ICC_MAX)
        if isinstance(icc_max, bool) or not isinstance(icc_max, (int, float)) or not 0 <= icc_max < 1:
            raise ConfigError(f"{where}: icc_max is a number in [0, 1)")
        judged = bool(sec.get("judged", False))
        if judged:
            if lads:
                raise ConfigError(f"{where}: a judged part holds flat sets only (a chain needs an in-run verdict)")
            bad = [f["name"] for f in flats if f.get("element") != "check" or f.get("n", 1) != 1]
            if bad:
                raise ConfigError(f"{where}: a judged part's flat sets use element: check, one item per "
                                  f"prompt ({', '.join(bad)})")
        gate = sec.get("judge_gate")
        if gate is not None:
            if isinstance(gate, dict) or (isinstance(gate, str) and "{n}" in gate):
                try:
                    gate_spec(gate)
                except ValueError as e:
                    raise ConfigError(f"{where}: judge_gate: {e}") from e
            elif not isinstance(gate, str) or not gate.strip():
                raise ConfigError(f"{where}: judge_gate is a judge element id (a string)")
            if not judged:
                raise ConfigError(f"{where}: judge_gate needs judged: true")
        avg = sec.get("average", "element")
        if avg not in ("element", "unit"):
            raise ConfigError(f"{where}: average is element (every element weighs 1) or unit (per piece first)")
        sew = sec.get("element_weight", "one")
        if sew not in ELEMENT_WEIGHTS:
            raise ConfigError(f"{where}: element_weight is one of {list(ELEMENT_WEIGHTS)}")
        eng = sec.get("engagement")
        if eng is not None and eng not in ENGAGEMENT:
            raise ConfigError(f"{where}: engagement is one of {sorted(ENGAGEMENT)}")
        comps = _components(sec.get("components"), where, float(part.get("max", part.get("weight", 0)) or 0))
        jm = _judge_mode(sec.get("judge_mode"), where, judged or bool(view_of), gate, scorer)
        sec_cl = sec.get("se_cluster", "unit")
        if sec_cl not in SE_CLUSTERS:
            raise ConfigError(f"{where}: se_cluster is one of {list(SE_CLUSTERS)}")
        out.append({"part": key, "chains": n, "slip_guard": slip, "pass": thr, "start": start,
                    "ladders": ladders, "flat": flats, "se_unit": se_unit, "icc_max": float(icc_max),
                    "judged": judged, "engagement": eng, "components": comps, "average": avg,
                    "element_weight": sew, "judge_gate": gate, "judge_mode": jm, "se_cluster": sec_cl,
                    "scorer": scorer, "view_of": view_of,
                    "view_sets": list(sec["view_sets"]) if sec.get("view_sets") else None})
    _resolve_seed_alias(out)
    _require_tags(out, name)
    return out


def _judge_mode(jm, where: str, judged: bool, gate, scorer: str | None = None) -> dict | None:
    """rc3 ``judge_mode: {kind: rubric, axes: [...], sexual_axes: [...], max_tokens: N}``: the part's
    judge component is the rubric's 0-10 axes (no elements), judged with thinking off (JUDGE's
    EXP-34 spec). ``axes`` always count; ``sexual_axes`` only on a card whose brief has sex (its
    ``sexual`` / ``explicit_required`` field); every other dimension is reported, never scored."""
    if jm is None:
        return None
    if isinstance(jm, dict) and jm.get("kind") == "ladder":
        return _ladder_mode(jm, where, judged, gate, scorer)
    if not isinstance(jm, dict) or jm.get("kind") not in JUDGE_MODE_KINDS:
        raise ConfigError(f"{where}: judge_mode is {{kind: rubric, axes: [...]}}")
    if not judged:
        raise ConfigError(f"{where}: judge_mode needs judged: true")
    if gate is not None:
        raise ConfigError(f"{where}: judge_mode rubric has no judge elements to gate on (drop judge_gate)")
    axes, sx = jm.get("axes"), jm.get("sexual_axes", [])
    if scorer and axes is None:
        # an adult45 scorer reads the rubric's marks itself (crucibleforge/adult45.py): no axes to average
        axes = []
    elif not isinstance(axes, list) or not axes or not all(isinstance(a, str) and a for a in axes):
        raise ConfigError(f"{where}: judge_mode.axes is a non-empty list of rubric dimensions")
    if not isinstance(sx, list) or not all(isinstance(a, str) and a for a in sx) or set(sx) & set(axes):
        raise ConfigError(f"{where}: judge_mode.sexual_axes is a list of dimensions not in axes")
    bad = set(jm) - {"kind", "axes", "sexual_axes", "max_tokens", "dead_axes", "combine", "code_weight",
                     "sensation_call"}
    if bad:
        raise ConfigError(f"{where}: judge_mode takes kind, axes, sexual_axes, max_tokens, dead_axes, combine, "
                          f"code_weight ({sorted(bad)})")
    out = {"kind": "rubric", "axes": list(axes), "sexual_axes": list(sx)}
    if jm.get("sensation_call") is not None:
        # adult page N5 (lead 2026-11-14): its sensation marks ride in the session call ("shared") or get a
        # call of their own ("separate"): a profile switch, one line
        if jm["sensation_call"] not in ("shared", "separate"):
            raise ConfigError(f"{where}: judge_mode.sensation_call is shared or separate")
        out["sensation_call"] = jm["sensation_call"]
    comb = jm.get("combine", "mean")
    if comb not in JUDGE_COMBINES:
        raise ConfigError(f"{where}: judge_mode.combine is one of {list(JUDGE_COMBINES)} (got {comb!r})")
    dead = jm.get("dead_axes", [])
    if (not isinstance(dead, list) or not all(isinstance(a, str) and a in axes for a in dead)
            or (not scorer and not set(axes) - set(dead))):
        raise ConfigError(f"{where}: judge_mode.dead_axes is a list of axes that leaves at least one live axis")
    if comb != "mean" or dead:
        out["combine"] = comb
        out["dead_axes"] = list(dead)
    cw = jm.get("code_weight", 0)
    if isinstance(cw, bool) or not isinstance(cw, (int, float)) or not 0 <= cw < 1:
        raise ConfigError(f"{where}: judge_mode.code_weight is a number in [0, 1) (the code checks' share)")
    if cw:
        out["code_weight"] = float(cw)
    if jm.get("max_tokens") is not None:
        out["max_tokens"] = _pos_int(jm["max_tokens"], "judge_mode.max_tokens", where)
    return out


def _ladder_mode(jm: dict, where: str, judged: bool, gate, scorer) -> dict:
    """``{kind: ladder, ladder: rpsession, windows: [1, 3], code_weight: 0.5, max_tokens: 1024}``: see rpladder.
    ``max_tokens`` is the one-call rubric verdict the row still gets (kept beside the ladder: the switch back is a
    profile line); ``windows`` the scoring windows (the rung check always uses all three)."""
    if not judged:
        raise ConfigError(f"{where}: judge_mode ladder needs judged: true")
    if gate is not None or scorer:
        raise ConfigError(f"{where}: judge_mode ladder takes no judge_gate and no scorer")
    bad = set(jm) - set(LADDER_MODE_KEYS)
    if bad:
        raise ConfigError(f"{where}: judge_mode ladder takes {', '.join(LADDER_MODE_KEYS)} ({sorted(bad)})")
    if jm.get("ladder") != "rpsession":
        raise ConfigError(f"{where}: judge_mode ladder: ladder is rpsession (the only ladder file)")
    from . import rpladder as _rl
    w = jm.get("windows", list(_rl.DEFAULT_WINDOWS))
    if (not isinstance(w, list) or not w or sorted(set(w)) != w
            or not all(isinstance(x, int) and not isinstance(x, bool) and x in _rl.CHECK_WINDOWS for x in w)):
        raise ConfigError(f"{where}: judge_mode ladder: windows is an ascending list from [1, 2, 3]")
    cw = jm.get("code_weight", 0)
    if isinstance(cw, bool) or not isinstance(cw, (int, float)) or not 0 <= cw < 1:
        raise ConfigError(f"{where}: judge_mode.code_weight is a number in [0, 1) (the code checks' share)")
    out = {"kind": "ladder", "ladder": "rpsession", "windows": list(w), "axes": list(_rl.AXES), "sexual_axes": []}
    if cw:
        out["code_weight"] = float(cw)
    if jm.get("max_tokens") is not None:
        out["max_tokens"] = _pos_int(jm["max_tokens"], "judge_mode.max_tokens", where)
    return out


def _require_tags(secs: list[dict], name) -> None:
    """A profile that tags any cell is a bank (R28-R31): every scored cell then carries both
    ``concept`` and ``band``, else the profile is refused (a cell the blueprint cannot place
    would be scored nowhere)."""
    cells = [(f"chains.{sec['part']}.ladders.{lad['name']}[{i}]", r)
             for sec in secs for lad in sec["ladders"] for i, r in enumerate(lad["rungs"], 1)]
    cells += [(f"chains.{sec['part']}.flat.{fs['name']}", fs["item"]) for sec in secs for fs in sec["flat"]]
    if not any(r.get("concept") or r.get("band") for _w, r in cells):
        return
    bad = [f"{w} ({', '.join(k for k in ('concept', 'band') if not r.get(k))})"
           for w, r in cells if not (r.get("concept") and r.get("band"))]
    if bad:
        raise ConfigError(f"profile {name}: a tagged (bank) profile tags every scored cell with "
                          f"concept and band; untagged: {'; '.join(bad[:8])}"
                          + (f" (+{len(bad) - 8} more)" if len(bad) > 8 else ""))


def _resolve_seed_alias(secs: list[dict]) -> None:
    """``seed_as: <set>``: the set draws the SAME seeded instances as the named set (its unit
    i = the target's unit i, whatever the packing), so one family can be run single and
    packed on identical briefs. The target is a flat set of the same profile with the same
    family / variant / level (a levels-form set looks for ``<set>_L<level>`` first)."""
    names = {fs["name"]: fs for sec in secs for fs in sec["flat"]}
    for sec in secs:
        for fs in sec["flat"]:
            cands = fs.pop("seed_as", None)
            sw = fs.pop("_where", None)
            if not cands:
                continue
            tgt = next((names[c] for c in cands if c in names and c != fs["name"]), None)
            if tgt is None:
                raise ConfigError(f"{sw}: seed_as names no other flat set of this profile (tried {', '.join(cands)})")
            a, b = fs["item"], tgt["item"]
            if (a["family"], a.get("variant"), a["level"]) != (b["family"], b.get("variant"), b["level"]):
                raise ConfigError(f"{sw}: seed_as {tgt['name']} is a different family / variant / level")
            if bool(a.get("deal")) != bool(b.get("deal")) or a.get("exclude") != b.get("exclude"):
                raise ConfigError(f"{sw}: seed_as {tgt['name']} needs the same deal / drop as its target "
                                  f"(else the same keys draw different texts)")
            fs["seed_ladder"] = tgt["name"]
            if tgt.get("n", 1) > 1:
                # a packed target seeds sub-item i on ITS case id <unit>-i<k> (materialize)
                fs["seed_n"] = tgt["n"]
            if b.get("deal"):
                # hand c-1 of the TARGET's partition (its count), so unit i draws the target's text i
                fs["seed_deal_of"] = tgt["count"]
    # an alias of an aliased set draws the final target's seeds
    for _ in range(len(names)):
        for fs in names.values():
            t = names.get(fs.get("seed_ladder"))
            if t is not None and t.get("seed_ladder") and t["seed_ladder"] != fs["seed_ladder"]:
                fs["seed_ladder"] = t["seed_ladder"]
                if t.get("seed_deal_of"):
                    fs["seed_deal_of"] = t["seed_deal_of"]
                fs.pop("seed_n", None)
                if t.get("seed_n"):
                    fs["seed_n"] = t["seed_n"]


def chain_cases(prof: dict | None) -> list[dict]:
    """One placeholder case per unit (what ``apply_profile`` adds to the run's case list):
    ``{id, category, chain_spec}``. Chain c of a part (1-based) climbs ladder ``(c - 1) % L``,
    id ``CHAIN-<ladder>-<c:03d>``; a flat instance is a one-rung unit with no retry, id
    ``FLAT-<set>-<i:03d>``, scored by its own (partial-credit) score."""
    out = []
    for sec in sections(prof):
        lads = sec["ladders"]
        for c in range(1, sec["chains"] + 1):
            lad = lads[(c - 1) % len(lads)]
            cid = f"CHAIN-{lad['name']}-{c:03d}"
            out.append({"id": cid, "category": lad["rungs"][0]["category"],
                        "chain_spec": {"id": cid, "kind": "chain", "part": sec["part"],
                                       "ladder": lad["name"], "c": c,
                                       "K": len(lad["rungs"]), "slip_guard": sec["slip_guard"],
                                       "start": dict(sec["start"]),
                                       "rungs": copy.deepcopy(lad["rungs"])}})
        for fs in sec["flat"]:
            n = fs.get("n", 1)
            for p in range(1, -(-fs["count"] // n) + 1):
                cid = f"FLAT-{fs['name']}-{p:03d}"
                sp = {"id": cid, "kind": "flat" if n == 1 else "multi", "part": sec["part"],
                      "ladder": fs["name"], "c": p, "K": 1, "slip_guard": 0,
                      "start": {"policy": "first"}, "rungs": [copy.deepcopy(fs["item"])]}
                if n > 1:
                    # sub-item k of prompt p is the set's item (p-1)*N + k: the SAME seeded
                    # instance a single-item set would present as unit i (packing never
                    # changes which instances are asked)
                    sp["items"] = list(range((p - 1) * n + 1, min(p * n, fs["count"]) + 1))
                if fs.get("element", "item") != "item":
                    sp["element"] = fs["element"]
                if fs["item"].get("deal"):
                    sp["deal_of"] = fs.get("seed_deal_of") or fs["count"]
                for k_ in ("element_weight", "average", "seed_ladder", "seed_n"):
                    if fs.get(k_):
                        sp[k_] = fs[k_]
                if sec.get("judge_mode"):
                    sp["judge_mode"] = dict(sec["judge_mode"])
                out.append({"id": cid, "category": fs["item"]["category"], "chain_spec": sp})
    return out


def chain_ids(prof: dict | None) -> set[str]:
    try:
        return {c["id"] for c in chain_cases(prof)}
    except ConfigError:
        return set()


# --------------------------------------------------------------------------- instances

def instance_key(release_seed: str, ladder: str, c: int, r: int, a: int, rung: dict) -> str:
    """The generator key of (ladder, chain c, rung r, attempt a): the same for every model
    (paired seeds); a retry (a >= 2) never repeats a seen instance."""
    return hashlib.sha256(f"{release_seed}:chain:{ladder}:{c}:{r}:{a}:{rung['family']}:"
                          f"{rung.get('variant') or ''}:{rung['level']}".encode()).hexdigest()[:16]


def instance(chain_case: dict, r: int, a: int, release_seed: str | None = None,
             start: int = 1) -> dict:
    """The unmaterialised instance case of rung ``r`` (1-based), attempt ``a`` of a chain.
    The instance (its key) never depends on ``start``: seeds stay paired whatever rung a
    model's chain started at; ``start`` is only recorded on the row. A multi-item unit's
    instance carries ``multi`` = its sub-items' generator blocks (ids ``<unit>-i<k>``)."""
    from . import gen as _gen
    sp = chain_case["chain_spec"] if "chain_spec" in chain_case else chain_case
    rung = sp["rungs"][r - 1]
    rs = _gen.release_seed() if release_seed is None else str(release_seed)

    def gblock(c):
        key = instance_key(rs, sp.get("seed_ladder") or sp["ladder"], c, r, a, rung)
        g = {"type": rung["family"], "level": rung["level"], "key": key}
        if rung.get("variant"):
            g["variant"] = rung["variant"]
        if rung.get("deal"):
            # one release-keyed partition of the family's bank, hand c - 1 of the set's count
            g["deal"] = {"key": hashlib.sha256(f"{rs}:deal:{sp.get('seed_ladder') or sp['ladder']}".encode()).hexdigest()[:16],
                         "slot": c - 1, "of": int(sp.get("deal_of") or sp["c"])}
        if rung.get("exclude"):
            g["exclude"] = list(rung["exclude"])
        if sp.get("seed_ladder"):
            # an aliased set's instance i is the target's unit i, case id included (a family
            # may seed the whole instance on its case id, as implied does)
            # (a packed target materialises unit i as sub-item k of its prompt p: id <unit>-i<k>)
            sn = int(sp.get("seed_n") or 1)
            g["seed_case"] = (f"FLAT-{sp['seed_ladder']}-{(c - 1) // sn + 1:03d}-i{(c - 1) % sn + 1}" if sn > 1
                              else f"FLAT-{sp['seed_ladder']}-{c:03d}-r{r}-a{a}")
        return g

    items = sp.get("items")
    if items:
        subs = [{"k": k, "i": i, "generator": gblock(i)} for k, i in enumerate(items, 1)]
        key = hashlib.sha256(":".join(s_["generator"]["key"] for s_ in subs).encode()).hexdigest()[:16]
        g = None
    else:
        g = gblock(sp["c"])
        key, subs = g["key"], None
    blk = {"id": sp["id"], "kind": sp.get("kind", "chain"), "part": sp["part"], "ladder": sp["ladder"],
           "c": sp["c"], "rung": r, "attempt": a, "K": sp["K"], "slip_guard": sp["slip_guard"],
           "family": rung["family"], "variant": rung.get("variant"),
           "level": rung["level"], "seed_id": key, "pass_at": rung["pass"],
           "ceiling": rung.get("ceiling"), "start_rung": int(start)}
    if sp.get("element", "item") != "item":
        blk["element"] = sp["element"]
    if rung.get("exclude"):
        blk["exclude"] = list(rung["exclude"])
    for t_ in ("concept", "band", "ran_out"):
        if rung.get(t_):
            blk[t_] = rung[t_]
    if sp.get("deal_of"):
        blk["deal_of"] = sp["deal_of"]
    if sp.get("seed_ladder"):
        blk["seed_ladder"] = sp["seed_ladder"]
    if sp.get("judge_mode"):
        blk["judge_mode"] = dict(sp["judge_mode"])        # how the judge stage scores this unit (rc3)
    out = {"id": f"{sp['id']}-r{r}-a{a}", "category": rung["category"], "chain": blk}
    if rung.get("gen_seed"):
        blk["gen_seed"] = int(rung["gen_seed"])
        out["gen_seed"] = int(rung["gen_seed"])           # runner: the writer's creative seed number
    if subs:
        blk["items"] = [s_["generator"]["key"] for s_ in subs]
        out["multi"] = subs
    else:
        out["generator"] = g
    return out


def block_instance(row: dict, release_seed: str | None = None) -> dict | None:
    """The unmaterialised instance a stored flat chain row answered, rebuilt from its own chain
    block through :func:`instance` (the same key and deal), or None when it cannot be rebuilt
    here (a multi-item or ``seed_as`` row, or a key that does not come back: the row is
    another release's). Used by the ``seed_case`` alias (report.alias_refusal)."""
    blk = row.get("chain") or {}
    if (blk.get("kind") != "flat" or blk.get("items") or blk.get("seed_ladder")
            or not all(k in blk for k in ("id", "ladder", "c", "rung", "attempt", "family", "level", "seed_id"))):
        return None
    r = int(blk["rung"])
    rung = {"family": blk["family"], "level": blk["level"], "category": row.get("category"),
            "pass": blk.get("pass_at"), "ceiling": blk.get("ceiling")}
    if blk.get("variant"):
        rung["variant"] = blk["variant"]
    if blk.get("exclude"):
        rung["exclude"] = list(blk["exclude"])
    if blk.get("deal_of"):
        rung["deal"] = True
    sp = {"id": blk["id"], "kind": "flat", "part": blk.get("part"), "ladder": blk["ladder"], "c": blk["c"],
          "K": blk.get("K", 1), "slip_guard": blk.get("slip_guard", 0), "rungs": [rung] * r}
    if blk.get("deal_of"):
        sp["deal_of"] = blk["deal_of"]
    inst = instance(sp, r, int(blk["attempt"]), release_seed=release_seed)
    return inst if inst["chain"]["seed_id"] == blk["seed_id"] and inst["id"] == row.get("case_id") else None


def ladder_seed_cases(prof: dict | None, ladder: str, id_prefix: str) -> list[dict]:
    """Ladder pack cases that ARE a flat set's measured instances (rung 1, attempt 1): each
    case's generator is the instance's own block plus ``seed_case`` = the stored row's case id,
    so it materialises byte-identically and a stored chain row of that set stands as its row
    (report.seed_aliases). For writing a pack's set file from the composition it was
    measured under, never at run time."""
    out = []
    for c in chain_cases(prof):
        sp = c.get("chain_spec") or c
        if sp.get("ladder") != ladder or sp.get("kind") != "flat":
            continue
        inst = instance(c, 1, 1)
        if inst.get("multi"):
            raise ValueError(f"{ladder}: a multi-item set cannot seed ladder cases")
        g = copy.deepcopy(inst["generator"])
        g["seed_case"] = inst["id"]
        out.append({"id": f"{id_prefix}-{int(sp['c']):02d}", "category": inst["category"], "generator": g})
    return out


def materialize(inst: dict) -> dict:
    """A runnable copy of an instance (the family's materialize; slow families take seconds,
    so the runner calls this in the worker that runs the attempt). A multi-item instance
    becomes one single-turn case: the N sub-items under ``=== TASK k ===`` headers, answers
    asked under ``=== ITEM k ===`` (G's EXP-20261113-49 format, 100 % split), the
    materialised sub-cases kept in ``multi_items`` for grading."""
    from . import gen as _gen
    if not inst.get("multi"):
        return _gen.materialize_case(copy.deepcopy(inst))
    unit = inst["chain"]["id"]
    subs = []
    for s_ in inst["multi"]:
        c = _gen.materialize_case({"id": f"{unit}-i{s_['k']}", "category": inst["category"],
                                   "generator": copy.deepcopy(s_["generator"])})
        subs.append(c)
    texts = [_sub_prompt(c) for c in subs]
    n = len(subs)
    head = MULTI_HEAD if all(wants_fence(c) for c in subs) else MULTI_HEAD_PLAIN
    body = head.format(n=n) + "".join(f"\n=== TASK {k} ===\n{t}\n" for k, t in enumerate(texts, 1))
    first = subs[0]
    out = {k: v for k, v in inst.items() if k != "multi"}
    out.update({"prompt": body, "temperature": first.get("temperature", 0.0),
                "top_p": first.get("top_p", 1.0),
                "max_tokens": int(inst["chain"].get("ceiling") or MAX_PROMPT_CEILING),
                "multi_items": subs})
    if first.get("system"):
        out["system"] = first["system"]
    return out


#: the multi-item header (EXP-20261113-49, measured: 100 % of segments found on MiniMax)
MULTI_HEAD = ("You will get {n} independent tasks. They do not share anything: solve each one on its own.\n"
              "Answer EVERY task. For task k, write a line `=== ITEM k ===` and then that task's answer in "
              "exactly the format the task asks for (its own fenced block). Put the {n} answers in order, "
              "nothing else after the last one.\n")
#: the header for families whose own task does not ask for a fenced answer (prose, e.g.
#: istack-write): asking for "its own fenced block" made the model wrap each piece in ```
#: lines, which the line-counting checks then counted (EXP-20261113-47, item 1 0.00 vs 0.87)
MULTI_HEAD_PLAIN = ("You will get {n} independent tasks. They do not share anything: solve each one on its own.\n"
                    "Answer EVERY task. For task k, write a line `=== ITEM k ===` and then answer that task in "
                    "exactly the format that task asks for, under its `=== ITEM k ===` header. Put the {n} "
                    "answers in order, nothing else after the last one.\n")
_FENCE = "```"
_ITEM_RE = re.compile(r"^[ \t*#`>_]*=+\s*ITEM\s+(\d+)\s*=+[ \t*#`_]*$", re.M | re.I)


def _sub_prompt(c: dict) -> str:
    if c.get("turns"):
        return c["turns"][0]
    return c["prompt"]


def wants_fence(c: dict) -> bool:
    """True when the item's own task asks for a fenced answer (code: keyfunc's ```python
    block, tracevm's ```text block, IMPLIED's code). Read off the materialised task text, so
    a family needs no flag: a task that never mentions a fence expects prose."""
    texts = list(c.get("turns") or []) + [c.get("prompt") or "", c.get("system") or ""]
    return any(_FENCE in (t if isinstance(t, str) else json.dumps(t)) for t in texts)


def unwrap_fence(seg: str) -> str | None:
    """The segment without ONE fence pair that wraps all of it (a ``` line first, a ``` line
    last, no other fence line), else None (left as written)."""
    lines = (seg or "").strip().splitlines()
    if len(lines) < 2 or not lines[0].strip().startswith(_FENCE) or lines[-1].strip() != _FENCE:
        return None
    if any(ln.strip().startswith(_FENCE) for ln in lines[1:-1]):
        return None
    return "\n".join(lines[1:-1]).strip()


def item_segment(sub: dict, seg: str) -> tuple[str, bool]:
    """The text a sub-item is graded on: for an item whose task does not ask for a fence, a
    wrapping fence pair is removed (the multi-item format, not the model's answer, put it
    there); a code item's segment is never touched. Returns (text, unfenced)."""
    if seg is None or wants_fence(sub):
        return seg, False
    un = unwrap_fence(seg)
    return (seg, False) if un is None else (un, True)


def split_items(answer: str, n: int) -> list[str | None]:
    """The N answer segments of a multi-item reply, by their ``=== ITEM k ===`` header
    (the first non-empty segment of each k wins). None = no segment for that item.
    Also the segments' order of appearance is returned via :func:`item_order`."""
    return _split(answer, n)[0]


def _split(answer: str, n: int):
    seg: dict[int, str] = {}
    order: list[int] = []
    ms = list(_ITEM_RE.finditer(answer or ""))
    for j, m in enumerate(ms):
        k = int(m.group(1))
        end = ms[j + 1].start() if j + 1 < len(ms) else len(answer)
        body = answer[m.end():end].strip()
        if 1 <= k <= n and body and k not in seg:
            seg[k] = body
            order.append(k)
    return [seg.get(k) for k in range(1, n + 1)], order


@functools.lru_cache(maxsize=None)
def _family_source_digest(family: str) -> str:
    from . import gen as _gen
    try:
        src = Path(inspect.getsourcefile(_gen.get(family)) or "").read_bytes()
    except (OSError, TypeError):
        src = family.encode()
    return hashlib.sha256(src).hexdigest()[:12]


def revision(chain_block: dict, release_seed: str | None = None) -> str:
    """The content stamp of one chain instance (row ``chain_revision``): its generator
    identity, the release seed and a digest of the family's source file, so a new release
    or a family code change makes the old rows stale. Pure on the row's ``chain`` block."""
    from . import gen as _gen
    rs = _gen.release_seed() if release_seed is None else str(release_seed)
    view = {k: chain_block.get(k) for k in ("ladder", "c", "rung", "attempt", "K", "slip_guard",
                                            "family", "variant", "level", "seed_id", "pass_at")}
    if chain_block.get("ceiling") is not None:
        view["ceiling"] = chain_block["ceiling"]
    if chain_block.get("kind", "chain") != "chain":
        view["kind"] = chain_block["kind"]
    for k in ("items", "element", "exclude", "deal_of", "gen_seed"):
        if chain_block.get(k) is not None:
            view[k] = chain_block[k]
    view["_rs"] = rs
    view["_src"] = _family_source_digest(str(chain_block.get("family")))
    item = _item_digest(chain_block, rs)
    if item is not None:
        view["_item"] = item
    return hashlib.sha256(json.dumps(view, sort_keys=True).encode()).hexdigest()[:8]


def _item_digest(chain_block: dict, rs: str) -> str | None:
    """A JSON-bank family's ``item_digest(gen, release_seed)`` for this instance (lead 2026-11-14: a bank is data,
    not in the family-source digest, so without this a card edit left its stored rows reading as current). The
    generator block is rebuilt from the row's chain block the way :func:`instance` makes it (key = seed_id, the
    release-keyed deal). Only the instance's OWN item counts: an edit to another card leaves this row current.
    None for a family without the hook (its revision is unchanged) or a multi-item block."""
    from . import gen as _gen
    try:
        fam = _gen.get(str(chain_block.get("family")))
    except Exception:  # noqa: BLE001 - an unknown family: the source digest alone
        return None
    hook = getattr(fam, "item_digest", None)
    if hook is None or chain_block.get("items"):
        return None
    g = {"type": chain_block.get("family"), "level": chain_block.get("level"), "key": chain_block.get("seed_id")}
    if chain_block.get("variant"):
        g["variant"] = chain_block["variant"]
    if chain_block.get("deal_of"):
        lad = chain_block.get("seed_ladder") or chain_block.get("ladder")
        g["deal"] = {"key": hashlib.sha256(f"{rs}:deal:{lad}".encode()).hexdigest()[:16],
                     "slot": int(chain_block.get("c") or 1) - 1, "of": int(chain_block["deal_of"])}
    if chain_block.get("exclude"):
        g["exclude"] = list(chain_block["exclude"])
    try:
        d = hook(g, rs)
        return None if d is None else str(d)
    except Exception as e:  # noqa: BLE001 - a bank that no longer deals this slot: stale by construction
        return f"unbuildable:{type(e).__name__}"


def check_gradeable(prof: dict) -> None:
    """Fail closed: every rung must grade in-run with no judge (rc1). One instance per
    (family, variant, level) is materialised; a rubric, judge elements or the absence of
    any code grader refuses the profile."""
    for sec in sections(prof):
        if sec["start"]["policy"] not in START_POLICIES:
            # the board reads the start recorded on the rows; only a RUN needs the policy
            raise ConfigError(f"profile {prof.get('name')}: chains.{sec['part']}: unknown start "
                              f"policy {sec['start']['policy']!r} (known {sorted(START_POLICIES)})")
        items = [(lad["name"], i, rung) for lad in sec["ladders"] for i, rung in enumerate(lad["rungs"], 1)]
        for fs in sec["flat"]:
            items.append((fs["name"], 1, fs["item"]))
            why = _shape_reason(fs["item"]["family"], fs["item"].get("variant"), fs["item"]["level"],
                                fs.get("n", 1), fs.get("element", "item"))
            if why:
                raise ConfigError(f"profile {prof.get('name')}: chains.{sec['part']} flat set "
                                  f"{fs['name']} ({fs['item']['family']} L{fs['item']['level']}): {why}")
        for lname, i, rung in items:
            if True:
                why = _judge_reason(rung["family"], rung.get("variant"), rung["level"])
                if why and sec.get("judged") and why.startswith("it needs a judge"):
                    why = None                 # a judged part (element: check): judge elements join on the board
                if why:
                    raise ConfigError(f"profile {prof.get('name')}: chains.{sec['part']} ladder "
                                      f"{lname} rung {i} ({rung['family']} L{rung['level']}) "
                                      f"cannot be in a chain: {why}")


@functools.lru_cache(maxsize=None)
def _shape_reason(family: str, variant: str | None, level: int, n: int, element: str) -> str | None:
    """Why a flat set's packing / element cannot work for this family (None = fine): a
    multi-item prompt needs single-turn sub-items; per-turn elements need a multi-turn item
    with at least two turns that have checks of their own."""
    from . import gen as _gen
    if n == 1 and element == "item":
        return None
    g = {"type": family, "level": level, "key": "chain-gradeable-probe"}
    if variant:
        g["variant"] = variant
    c = _gen.materialize_case({"id": "CHAIN-PROBE", "category": "x", "generator": g})
    if n > 1 and len(c.get("turns") or []) > 1:
        return "items_per_prompt needs single-turn items (this one has several turns)"
    if element == "check":
        try:
            fam = _gen.get(family)
            ref = fam.reference(dict(g), release_seed=_gen.release_seed(), case_id="CHAIN-PROBE") \
                if hasattr(fam, "reference") else ""
        except Exception:  # noqa: BLE001 - no reference: probe with an empty answer
            ref = ""
        res = check_results(c, ref) if ref else None
        if not res:
            res = check_results(c, "")
        if not res:
            return "element: check needs a grader that returns named sub-results (this family's gives none)"
    if element == "turn":
        if len(c.get("turns") or []) < 2:
            return "element: turn needs a multi-turn item"
        if len(turn_weights(c.get("checks") or [])) < 2:
            return "element: turn needs at least two turns with checks of their own"
    return None


@functools.lru_cache(maxsize=None)
def _judge_reason(family: str, variant: str | None, level: int) -> str | None:
    from . import gen as _gen
    from . import rpdeep
    g = {"type": family, "level": level, "key": "chain-gradeable-probe"}
    if variant:
        g["variant"] = variant
    c = _gen.materialize_case({"id": "CHAIN-PROBE", "category": "x", "generator": g})
    if c.get("rubric") or c.get("judge_elements") or c.get("judge_key"):
        return "it needs a judge (rubric / judge elements)"
    if c.get("turns"):
        if not (rpdeep.is_programmatic(c) and c.get("checks")):
            return "a multi-turn instance not scored by its checks alone"
        return None
    if not c.get("grader"):
        return "no code grader"
    return None


# --------------------------------------------------------------------------- the algorithm

def advance(K: int, slip_guard: int, results: dict, start: int = 1) -> dict:
    """Replay a chain from its graded attempts. ``results`` = {(rung, attempt): passed}.
    ``start`` = the chain's first presented rung (default 1): rungs below it are CREDITED
    (held, not presented). Returns {next: (r, a) | None, ended, held, path: [(r, a, passed)],
    retries_used, start}. ``next`` is the attempt to present now (None once the chain ended)."""
    start = min(max(int(start or 1), 1), K) if K else 1
    r, a, held, used, path = start, 1, start - 1, 0, []
    while r <= K:
        res = results.get((r, a))
        if res is None:
            return {"next": (r, a), "ended": False, "held": held, "path": path, "retries_used": used,
                    "start": start}
        path.append((r, a, bool(res)))
        if res:
            held, r, a = r, r + 1, 1
            continue
        if used < slip_guard:
            used, a = used + 1, a + 1
            continue
        break
    return {"next": None, "ended": True, "held": held, "path": path, "retries_used": used,
            "start": start}


def chain_score(held: int, K: int) -> float:
    return held / K if K else 0.0


def rung_states(K: int, path: list, start: int = 1) -> dict[int, str]:
    """{rung: "credited" | "held" | "failed" | "not reached"} of a finished (or partial)
    chain. "credited" = below the chain's start rung: held without being presented."""
    out = {r: ("credited" if r < start else "not reached") for r in range(1, K + 1)}
    for r, _a, ok in path:
        out[r] = "held" if ok else ("held" if out[r] == "held" else "failed")
    return out


# --------------------------------------------------------------------------- start rung (hook)

def _start_first(spec: dict, ended: dict, params: dict) -> int:
    return 1


#: start-rung policies by name (profile ``start: {policy: <name>, ...params}``). A policy is
#: ``f(chain_spec, ended, params) -> int | None``: the chain's first presented rung, or None
#: = "not decidable yet" (the chain waits until another chain of the run ends, e.g. an
#: adaptive policy that calibrates on its first chains). ``ended`` = {chain id: {"held",
#: "K", "start", "ladder", "c"}} of this model's chains that have ended so far (stored rows
#: included). Rungs below the returned rung are credited as held. Register more with
#: :func:`register_start_policy`; "first" (always rung 1) is the default.
START_POLICIES = {"first": _start_first}


def register_start_policy(name: str, fn) -> None:
    START_POLICIES[str(name)] = fn


def _start_block(sec: dict, where: str) -> dict:
    st = sec.get("start") or {"policy": "first"}
    if not isinstance(st, dict) or not isinstance(st.get("policy", "first"), str):
        raise ConfigError(f"{where}: start is {{policy: <name>, ...params}}")
    return {"policy": "first", **st}


# --------------------------------------------------------------------------- verdicts

def attempt_score(rows: list[dict], case: dict) -> float | None:
    """The 0..1 score of one graded attempt (in-run, the engine's own grading): a
    multi-turn programmatic session = rpdeep.score_row's rate on its final row; a single
    row = its stored ``score``, else its element share, else pass/fail. None = not graded
    (an error / skipped row: infrastructure, the attempt is retried on resume)."""
    if not rows:
        return None
    last = rows[-1]
    if last.get("skipped") or last.get("grade") == "error" or last.get("error"):
        return None
    if case.get("turns"):
        from . import rpdeep
        res = rpdeep.score_row(last, case, rows)
        return None if res.get("rate") is None else float(res["rate"])
    if last.get("score") is not None:
        return float(last["score"])
    els = (last.get("elements") or {}).get("results")
    if els:
        return sum(1 for x in els if x.get("pass")) / len(els)
    if last.get("grade") in ("pass", "fail"):
        return 1.0 if last["grade"] == "pass" else 0.0
    return None


def turn_weights(checks: list[dict]) -> dict:
    """{turn: summed weight} of the checks that read exactly one turn (the per-turn element's
    checks; a check spanning several turns belongs to no turn element)."""
    out: dict = {}
    for ch in checks:
        ts = ch.get("turns") or []
        if len(ts) == 1:
            out[ts[0]] = out.get(ts[0], 0.0) + float(ch.get("weight", 1))
    return {t: w for t, w in out.items() if w}


def turn_scores(checks: list[dict], replies: list[str]) -> dict:
    """{turn: 0..1} = weighted credit of the single-turn checks of each graded turn, gates
    applied (G's g-work/turnicc.py, the measured definition: ICC about 0 on attend)."""
    from . import session_checks
    out = session_checks.run_checks({"checks": checks}, replies) or {"results": []}
    by_id = {r["id"]: r for r in out["results"]}
    zero = session_checks.turn_gates(checks, by_id)
    got: dict = {}
    for ch in checks:
        ts = ch.get("turns") or []
        if len(ts) != 1:
            continue
        r = by_id.get(ch["id"])
        if r and not session_checks.gated(ch, zero):
            got[ts[0]] = got.get(ts[0], 0.0) + float(ch.get("weight", 1)) * session_checks.check_credit(r)
    return {t: round(got.get(t, 0.0) / w, 6) for t, w in sorted(turn_weights(checks).items(), key=lambda x: str(x[0]))}


#: finish reasons that end a reply before its answer: the ceiling ("length") and the
#: session's exhausted context ("context": a turn the runner could not send)
_CUT = ("length", "context")


def _is_ctx(r: dict) -> bool:
    """A turn the session's context stopped: not sent / refused for the window, or cut while
    the window had clamped its budget below the ceiling (runner ``context_exhausted``)."""
    return bool(r.get("context_exhausted")) or r.get("finish_reason") == "context"


def _cut_outcome(rows: list[dict]) -> str:
    return "context" if any(_is_ctx(r) for r in rows) else "ran out"


def _blank_row(row: dict) -> bool:
    """4.5: a turn graded as nothing -- stamped ``empty_stop`` (grade only the response given)
    or degenerate (a loop is a failure, like a blank; lead 2026-11-14)."""
    from . import degenerate
    return bool(row.get("empty_stop") or row.get("degenerate") or degenerate.row_degenerate(row))


def _replies(rows: list[dict]) -> list[str]:
    conv = rows[-1].get("conversation") or []
    rep = [m.get("content") or "" for m in conv if m.get("role") == "assistant"]
    if not rep:
        rep = [r.get("response") or "" for r in sorted(rows, key=lambda r: r.get("turn") or 0)]
    for r in rows:
        t = r.get("turn")
        if isinstance(t, int) and 1 <= t <= len(rep) and _blank_row(r):
            rep[t - 1] = ""
    return rep


def turn_elements(rows: list[dict], case: dict) -> list[dict]:
    """Per-turn elements of a multi-turn item (``element: turn``): one per turn with checks
    of its own. A turn whose reply hit the ceiling keeps its check credit and is marked
    "ran out"; a turn never answered (the session ended early) scores 0, "ran out" if any
    turn was cut, else "missing". When the session's context ended it (runner
    ``context_exhausted``), the stopped turn and every turn after it are "context" (an unsent
    turn scores 0; a turn cut by the window keeps its credit)."""
    unit = case["chain"]["id"]
    replies = _replies(rows)
    n = len(case.get("turns") or replies)
    cut_turns = {int(r.get("turn") or 0) for r in rows if r.get("finish_reason") in _CUT}
    ctx_turns = {int(r.get("turn") or 0) for r in rows if _is_ctx(r)}
    unsent = {int(r.get("turn") or 0) for r in rows if r.get("finish_reason") == "context"}
    sc = turn_scores(case.get("checks") or [], (replies + [""] * n)[:n])
    out = []
    for t, v in sc.items():
        e = {"id": f"{unit}-t{t}", "turn": t, "score": v}
        if isinstance(t, int) and (t > len(replies) or t in unsent):
            oc = "context" if ctx_turns else ("ran out" if cut_turns else "missing")
            e.update(score=0.0, outcome=oc,
                     reason="not sent: the session's context was exhausted" if oc == "context"
                     else "no reply for this turn")
        elif t in ctx_turns:
            e["outcome"] = "context"
        elif t in cut_turns:
            e["outcome"] = "ran out"
        out.append(e)
    return out


def _row_text(row: dict) -> str:
    """The graded text of a single-turn row: its content, else (a finished reply only) its
    reasoning channel (the engine's ``scoreable_text``)."""
    from .api import strip_thinking_tags
    if _blank_row(row):
        return ""
    txt = (row.get("response") or "").strip()
    if not txt and row.get("finish_reason") != "length":
        txt = (row.get("reasoning") or "").strip()
    return strip_thinking_tags(txt)


def multi_elements(row: dict, case: dict) -> list[dict]:
    """The sub-item elements of a multi-item prompt's reply, each graded alone by the
    engine (``gen.engine_score``: the family's own grader / checks). A sub-item with no
    segment scores 0: "ran out" when the reply hit the ceiling, else "missing". When the
    reply was cut, the segment it was writing (the last one) keeps its grade and is marked
    "ran out"."""
    from . import gen as _gen
    subs = case["multi_items"]
    n = len(subs)
    segs, order = _split(_row_text(row), n)
    cut = row.get("finish_reason") == "length"
    out = []
    for k, (sub, seg) in enumerate(zip(subs, segs), 1):
        e = {"id": sub["id"], "k": k, "seed_id": ((sub.get("generator") or {}).get("key"))}
        if seg is None:
            e.update(score=0.0, outcome="ran out" if cut else "missing",
                     reason="the reply ran out before this item" if cut else "no `=== ITEM k ===` segment")
        else:
            seg, unf = item_segment(sub, seg)
            if unf:
                e["unfenced"] = True
            try:
                e["score"] = round(float(_gen.engine_score(sub, seg)), 6)
            except Exception as ex:  # noqa: BLE001 - a grader crash on one segment fails that element only
                e.update(score=0.0, reason=f"grader error: {str(ex)[:120]}")
            if cut and order and k == order[-1]:
                e["outcome"] = "ran out"
        out.append(e)
    return out


def check_results(case: dict, answer) -> list[tuple] | None:
    """The named sub-results of one graded answer: [(id, score 0..1, weight, group)], or None
    when the family's grading gives none. A session (``turns``) = its checks of positive
    weight that are not gates (every check runs, so gates apply; credit as ``check_credit``;
    ``answer`` = the list of replies, or one text for a single-turn session); a single-turn
    case = the ``results`` its grader returns (``credit`` when given, else pass = 1)."""
    from . import session_checks
    if case.get("turns"):
        allc = case.get("checks") or []
        checks = [c for c in allc if float(c.get("weight", 1)) and not c.get("gate")]
        if not checks:
            return None
        n = len(case["turns"])
        replies = [answer] if isinstance(answer, str) else list(answer)
        replies = (replies + [""] * n)[:n]
        out = session_checks.run_checks({"checks": allc}, replies) or {"results": []}
        by_id = {r["id"]: r for r in out["results"]}
        zero = session_checks.turn_gates(allc, by_id)
        res = []
        for ch in checks:
            r = by_id.get(ch["id"])
            v = 0.0 if (r is None or session_checks.gated(ch, zero)) else session_checks.check_credit(r)
            res.append((str(ch["id"]), float(v), float(ch.get("weight", 1)), ch.get("group")))
        return res
    from . import graders
    from .api import ChatResult
    text = answer if isinstance(answer, str) else "\n".join(answer)
    v = graders.grade(ChatResult(response_text=text, finish_reason="stop", served_model="m"), case)
    rs = (v or {}).get("results")
    if not rs:
        return None
    res = []
    for i, r in enumerate(rs):
        cr = r.get("credit")
        sc = float(cr) if isinstance(cr, (int, float)) and not isinstance(cr, bool) else (1.0 if r.get("pass") else 0.0)
        # a weight of 0 is a weight (IMPLIED's contested checks: out of numerator and denominator);
        # only a missing weight is 1
        res.append((str(r.get("id", i + 1)), sc, _weight_of(r), r.get("group")))
    return res


def _check_els(base: str, res, extra: dict | None = None) -> list[dict]:
    out = []
    for i, v, w, *g in res or []:
        e = {"id": f"{base}-c{i}", "check": i, "score": round(v, 6), "weight": w, "source": "code"}
        if g and g[0] is not None:
            e["group"] = g[0]
        out.append({**e, **(extra or {})})
    return out


def check_elements(rows: list[dict], case: dict) -> list[dict]:
    """``element: check`` on one item: each named sub-result of its grading is an element
    (ids ``<unit>-c<check id>``). A reply cut at the ceiling keeps the credit it earned and
    its elements are marked "ran out"."""
    unit = case["chain"]["id"]
    cut = any(r.get("finish_reason") in _CUT for r in rows)
    live = ((rows[-1].get("elements") or {}).get("results") if case.get("console") else None)
    if live:
        # a console family (runner._run_console_loop) is scored by its simulator while it runs:
        # its per-check results ARE the elements (a replay of the last reply could not rebuild them)
        res = [(str(r.get("id", i + 1)), 1.0 if r.get("pass") else 0.0, 1.0, r.get("group"))
               for i, r in enumerate(live)]
        els = _check_els(unit, res)
    else:
        ans = _replies(rows) if case.get("turns") else _row_text(rows[-1])
        els = _check_els(unit, check_results(case, ans))
    if not els:
        els = [{"id": f"{unit}-c?", "score": 0.0, "outcome": "missing", "reason": "the grader gave no sub-results"}]
    if cut:
        oc = _cut_outcome(rows)
        for e in els:
            e.setdefault("outcome", oc)
    return els


def multi_check_elements(row: dict, case: dict) -> list[dict]:
    """``element: check`` under ``items_per_prompt``: every sub-item's named sub-results
    (ids ``<unit>-i<k>-c<id>``). A sub-item with no segment gets the sub-results of an empty
    answer, all 0 ("missing", or "ran out" when the reply hit the ceiling)."""
    subs = case["multi_items"]
    segs, order = _split(_row_text(row), len(subs))
    cut = row.get("finish_reason") == "length"
    out = []
    for k, (sub, seg) in enumerate(zip(subs, segs), 1):
        extra = {"k": k}
        if seg is None:
            res = [(i, 0.0, *rest[1:]) for i, *rest in (check_results(sub, "") or [("?", 0.0, 1.0, None)])]
            extra.update(outcome="ran out" if cut else "missing",
                         reason="the reply ran out before this item" if cut else "no `=== ITEM k ===` segment")
        else:
            seg, unf = item_segment(sub, seg)
            if unf:
                extra["unfenced"] = True
            try:
                res = check_results(sub, seg) or [("?", 0.0, 1.0, None)]
            except Exception as ex:  # noqa: BLE001 - a grader crash fails that sub-item only
                res = [("?", 0.0, 1.0, None)]
                extra["reason"] = f"grader error: {str(ex)[:120]}"
            if cut and order and k == order[-1]:
                extra["outcome"] = "ran out"
        out += _check_els(sub["id"], res, extra)
    return out


TIER_KEYS = ("check_tier", "domain")


def tag_tiers(els: list[dict], case: dict) -> list[dict]:
    """Record each check element's tier and its brief's domain (``check_tier``, ``domain``) where the
    family declares them: ``grader_config.check_tiers`` {check id: tier} and ``meta.domain`` of the
    case, or of the element's sub-item (``multi_items[k-1]``) in a packed prompt. Labels only: no
    score, weight or outcome changes. An element of a family that declares no tiers is untouched
    (untiered). Returns ``els`` (modified in place)."""
    subs = case.get("multi_items") or []
    for e in els:
        k = e.get("k")
        src = subs[k - 1] if subs and isinstance(k, int) and 1 <= k <= len(subs) else (None if subs else case)
        if src is None:
            continue
        tiers = (src.get("grader_config") or {}).get("check_tiers") or {}
        if e.get("check") in tiers:
            e["check_tier"] = tiers[e["check"]]
            dom = (src.get("meta") or {}).get("domain")
            if dom:
                e["domain"] = dom
    return els


def stamp_verdict(rows: list[dict], case: dict) -> None:
    """Write the attempt's verdict on its final row (``chain`` block: score, passed, and for
    a multi-item prompt / per-turn item its ``elements``). A reply cut off at the ceiling
    (finish=length) is a failed attempt, its score kept for the report (R23)."""
    if not rows or not case.get("chain"):
        return
    last = rows[-1]
    infra = bool(last.get("skipped") or last.get("grade") == "error" or last.get("error"))
    els = None
    mode = case["chain"].get("element")
    if not infra and case.get("multi_items"):
        els = multi_check_elements(last, case) if mode == "check" else multi_elements(last, case)
    elif not infra and mode == "check":
        els = check_elements(rows, case)
    elif not infra and mode == "turn" and case.get("turns"):
        els = turn_elements(rows, case)
    if els is not None:
        tag_tiers(els, case)       # tier cells (scale.py): labels only
        # a weight-0 element (a contested IMPLIED check, pytrap's float_repr line, planopt's
        # valid) is on record and earns nothing: it is out of the mean (lead 2026-11-14)
        live = [e for e in els if _weight_of(e) > 0] or els
        sc = (sum(e["score"] for e in live) / len(live)) if live else 0.0
    else:
        sc = attempt_score(rows, case)
    blk = dict(last.get("chain") or case["chain"])
    cut = any(r.get("finish_reason") in _CUT for r in rows)
    if cut and sc is None and not infra:
        sc = 0.0                   # ran out with nothing gradeable: a failed attempt, not missing
    blk["score"] = None if sc is None else round(sc, 4)
    blk["passed"] = None if sc is None else bool(sc >= float(blk["pass_at"]) - 1e-12 and not cut)
    if blk["passed"] is not None:
        # "ran out" = the reply hit the attempt's ceiling (the model's output/context maximum):
        # a FAILED attempt, counted by the chain like a wrong answer (R23)
        # "context" = the session's context was exhausted (runner context accounting): failed
        # like "ran out", reported apart (never a transport error, never a silent truncation)
        blk["outcome"] = "pass" if blk["passed"] else (_cut_outcome(rows) if cut else "wrong")
    if cut:
        blk["cut_off"] = True
        if any(_is_ctx(r) for r in rows):
            blk["context_exhausted"] = True
    if mode == "check" and (case.get("rubric") or case.get("judge_elements")):
        # a judged item: its judge elements arrive with the judge stage; until then the unit
        # is pending on the board (never scored on its code elements alone)
        blk["judged"] = True
    if els is not None and blk["passed"] is not None:
        for e in els:
            e.setdefault("outcome", "pass" if e["score"] >= float(blk["pass_at"]) - 1e-12 else "wrong")
        blk["elements"] = els
    last["chain"] = blk


def row_verdict(row: dict) -> bool | None:
    return (row.get("chain") or {}).get("passed")


# --------------------------------------------------------------------------- stored rows

def scores_from_rows(rows: list[dict]) -> dict[str, dict]:
    """{unit id: {(rung, attempt): score}} (the newest graded row of each instance)."""
    out: dict[str, dict] = {}
    newest: dict[tuple, tuple] = {}
    for i, r in enumerate(rows):
        ch = r.get("chain")
        if not ch or ch.get("passed") is None:
            continue
        k = (ch["id"], int(ch["rung"]), int(ch["attempt"]))
        stamp = (str(r.get("ts") or ""), i)
        if k not in newest or stamp >= newest[k][0]:
            newest[k] = (stamp, float(ch.get("score") or 0.0))
    for (cid, rg, at), (_s, v) in newest.items():
        out.setdefault(cid, {})[(rg, at)] = v
    return out


def results_from_rows(rows: list[dict]) -> dict[str, dict]:
    """{chain id: {(rung, attempt): passed}} from stored rows (the newest graded row of each
    instance wins; ungraded rows are ignored, so their attempt is presented again)."""
    out: dict[str, dict] = {}
    newest: dict[tuple, tuple] = {}
    for i, r in enumerate(rows):
        ch = r.get("chain")
        if not ch or ch.get("passed") is None:
            continue
        k = (ch["id"], int(ch["rung"]), int(ch["attempt"]))
        stamp = (str(r.get("ts") or ""), i)
        if k not in newest or stamp >= newest[k][0]:
            newest[k] = (stamp, bool(ch["passed"]))
    for (cid, rg, at), (_s, ok) in newest.items():
        out.setdefault(cid, {})[(rg, at)] = ok
    return out


def starts_from_rows(rows: list[dict]) -> dict[str, int]:
    """{chain id: start rung} recorded on the chain's rows (default 1)."""
    out: dict[str, int] = {}
    for r in rows:
        ch = r.get("chain")
        if ch and ch.get("id") not in out:
            out[ch["id"]] = int(ch.get("start_rung") or 1)
    return out


def ended_from_rows(rows: list[dict]) -> set[str]:
    """Chain ids whose chain has ended on these rows (pure on the rows: each row's chain
    block carries K and slip_guard). What bench-all's missing-case check reads."""
    res, starts = results_from_rows(rows), starts_from_rows(rows)
    shape = {}
    for r in rows:
        ch = r.get("chain")
        if ch and ch.get("K"):
            shape[ch["id"]] = (int(ch["K"]), int(ch.get("slip_guard") or 0))
    return {cid for cid, (K, slip) in shape.items()
            if advance(K, slip, res.get(cid, {}), starts.get(cid, 1))["ended"]}


def usable_row(row: dict) -> bool:
    """A stored chain row a resumed run may build on: current regime and the stamp of the
    instance it answered still current."""
    ch = row.get("chain")
    if not ch:
        return False
    from .version import in_regime
    return in_regime(row) and row.get("chain_revision") == revision(ch)


def completed(prof: dict | None, rows: list[dict]) -> set[str]:
    """Chain ids of the profile whose chain has ended on these rows."""
    crow = [r for r in rows if r.get("chain")]
    res, starts = results_from_rows(crow), starts_from_rows(crow)
    done = set()
    for c in chain_cases(prof):
        sp = c["chain_spec"]
        if advance(sp["K"], sp["slip_guard"], res.get(c["id"], {}), starts.get(c["id"], 1))["ended"]:
            done.add(c["id"])
    return done


def profile_index(prof: dict | None) -> dict[str, dict]:
    """{chain id: placeholder case} of a profile ({} when it has no valid chains block)."""
    try:
        return {c["id"]: c for c in chain_cases(prof)}
    except ConfigError:
        return {}


def in_profile(row: dict, index: dict[str, dict]) -> bool:
    """A chain row belongs to the profile's chain composition (``index`` =
    :func:`profile_index`): its chain id exists and the instance it answered is the one the
    profile's spec gives at that step."""
    ch = row.get("chain") or {}
    spec = index.get(ch.get("id"))
    if spec is None:
        return False
    try:
        want = instance(spec, int(ch["rung"]), int(ch["attempt"]))["chain"]
    except (IndexError, KeyError, TypeError, ValueError):
        return False
    return (all(want[k] == ch.get(k) for k in ("seed_id", "K", "slip_guard", "level", "family"))
            and want.get("ceiling") == ch.get("ceiling"))   # a row made under another ceiling is not this instance


# --------------------------------------------------------------------------- runner driver

class Driver:
    """Breadth-first scheduler of a model's chains inside one run. ``start()`` gives the
    first missing attempt of every chain (all first presented rungs before any deeper one);
    ``record(instance_id, rows)`` takes a graded attempt and gives the chain's next attempt,
    plus the first attempt of any chain whose start rung became decidable (or nothing: the
    chain ended, or halted on an ungraded row). Thread-safe.

    Start rung (the hook): a chain's first presented rung comes from its section's
    ``start`` policy (:data:`START_POLICIES`), asked once per chain; a chain with stored
    rows keeps the start recorded on them (resume never re-decides)."""

    def __init__(self, chain_cases_: list[dict], prior_rows: list[dict] | None = None):
        self.specs = {c["id"]: c for c in chain_cases_}
        self.results = {cid: {} for cid in self.specs}
        # A stored row counts only when it answered the instance the CURRENT spec gives at
        # that step (same stamp): usable_row alone checks a row against its own chain block,
        # so a spec change that keeps the chain id (ceiling 2,500 -> 16,384, 2026-11-14) would
        # let the old rows stand in for the new instances and the chain would never re-run.
        usable = [r for r in prior_rows or [] if usable_row(r) and self._current(r)]
        for cid, res in results_from_rows(usable).items():
            if cid in self.results:
                self.results[cid].update(res)
        self.start_of = {cid: st for cid, st in starts_from_rows(usable).items() if cid in self.specs}
        self.waiting: list[str] = []          # chains whose start policy said "not yet"
        self.halted: dict[str, str] = {}
        self.lock = threading.Lock()

    def _current(self, row: dict) -> bool:
        ch = row.get("chain") or {}
        spec = self.specs.get(ch.get("id"))
        if spec is None:
            return False
        try:
            want = instance(spec, int(ch["rung"]), int(ch["attempt"]))["chain"]
        except (IndexError, KeyError, TypeError, ValueError):
            return False
        return row.get("chain_revision") == revision(want)

    def _state(self, cid: str) -> dict:
        sp = self.specs[cid]["chain_spec"]
        return advance(sp["K"], sp["slip_guard"], self.results[cid], self.start_of.get(cid, 1))

    def _ended(self) -> dict:
        out = {}
        for cid in self.start_of:
            st = self._state(cid)
            if st["ended"]:
                sp = self.specs[cid]["chain_spec"]
                out[cid] = {"held": st["held"], "K": sp["K"], "start": st["start"],
                            "ladder": sp["ladder"], "c": sp["c"]}
        return out

    def _decide(self, cid: str) -> bool:
        if cid in self.start_of:
            return True
        sp = self.specs[cid]["chain_spec"]
        pol = dict(sp.get("start") or {"policy": "first"})
        fn = START_POLICIES[pol.pop("policy", "first")]
        st = fn(sp, self._ended(), pol)
        if st is None:
            return False
        self.start_of[cid] = min(max(int(st), 1), sp["K"])
        return True

    def _next(self, cid: str) -> dict | None:
        if not self._decide(cid):
            if cid not in self.waiting:
                self.waiting.append(cid)
            return None
        st = self._state(cid)
        if st["next"] is None:
            return None
        return instance(self.specs[cid], *st["next"], start=st["start"])

    def _release_waiting(self) -> list[dict]:
        out, still = [], []
        for cid in self.waiting:
            if self._decide(cid):
                nxt = self._next(cid)
                if nxt is not None:
                    out.append(nxt)
            else:
                still.append(cid)
        self.waiting = still
        return out

    def start(self) -> list[dict]:
        with self.lock:
            return [i for i in (self._next(cid) for cid in self.specs) if i is not None]

    def record(self, inst_id: str, rows: list[dict] | None) -> list[dict]:
        cid, _, tail = inst_id.rpartition("-r")
        rg, _, at = tail.partition("-a")
        with self.lock:
            if cid not in self.specs:
                return []
            ok = None if not rows else row_verdict(rows[-1])
            if ok is None:
                self.halted[cid] = "no graded row (stopped, errored or skipped): resumed next run"
                return []
            self.results[cid][(int(rg), int(at))] = bool(ok)
            nxt = self._next(cid)
            out = [nxt] if nxt is not None else []
            if self.waiting and nxt is None:          # this chain ended: a policy may decide now
                out += self._release_waiting()
            return out

    def summary(self) -> dict:
        with self.lock:
            ended = sum(1 for cid in self.start_of if self._state(cid)["ended"])
            return {"chains": len(self.specs), "ended": ended, "halted": len(self.halted),
                    "waiting": len(self.waiting)}


# --------------------------------------------------------------------------- scoring

def _num(v) -> float:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else 0.0


def _newest_blocks(rows: list[dict]) -> dict[tuple, dict]:
    """{(unit id, rung, attempt): chain block} of the newest graded row of each instance."""
    newest: dict[tuple, tuple] = {}
    for i, r in enumerate(rows):
        ch = r.get("chain")
        if not ch or ch.get("passed") is None:
            continue
        k = (ch["id"], int(ch["rung"]), int(ch["attempt"]))
        stamp = (str(r.get("ts") or ""), i)
        if k not in newest or stamp >= newest[k][0]:
            newest[k] = (stamp, ch)
    return {k: v[1] for k, v in newest.items()}


def _newest_judge(rows: list[dict]) -> dict[tuple, dict]:
    """{(unit, rung, attempt): judge block} of the newest graded row of each instance that
    carries judge element results (the judge stage writes them on the row after the run)."""
    out: dict[tuple, tuple] = {}
    for i, r in enumerate(rows):
        ch = r.get("chain")
        j = r.get("judge")
        if not ch or ch.get("passed") is None or not isinstance(j, dict):
            continue
        if not ((j.get("elements") or {}).get("results")):
            continue
        k = (ch["id"], int(ch["rung"]), int(ch["attempt"]))
        stamp = (str(r.get("ts") or ""), i)
        if k not in out or stamp >= out[k][0]:
            out[k] = (stamp, j)
    return {k: v[1] for k, v in out.items()}


def _newest_rubric(rows: list[dict]) -> dict[tuple, dict]:
    """rc3: {(unit, rung, attempt): judge block} of the newest graded row of each instance whose
    verdict is a rubric-mode verdict (``policy.kind == "rubric"``) with scores. A verdict made under
    any other policy (an element pass, a thinking-on craft call) is not read: the unit waits for a
    rubric-mode judge pass."""
    out: dict[tuple, tuple] = {}
    for i, r in enumerate(rows):
        ch = r.get("chain")
        j = r.get("judge")
        if not ch or ch.get("passed") is None or not isinstance(j, dict):
            continue
        if (j.get("policy") or {}).get("kind") != "rubric" or j.get("judge_failed") \
                or not isinstance(j.get("scores"), dict):
            continue
        k = (ch["id"], int(ch["rung"]), int(ch["attempt"]))
        stamp = (str(r.get("ts") or ""), i)
        if k not in out or stamp >= out[k][0]:
            out[k] = (stamp, j)
    return {k: v[1] for k, v in out.items()}


def card_sexual(row: dict) -> bool:
    """Does the card's brief have sex? (the row's ``sexual``, else ``explicit_required``; a case
    with neither, e.g. a safe-for-work card, has none)."""
    for k in ("sexual", "explicit_required"):
        v = (row or {}).get(k)
        if v is not None:
            return bool(v)
    return False


def rubric_axes(jm: dict, row: dict) -> list[str]:
    """The axes a rubric-mode piece is scored on: ``axes``, plus ``sexual_axes`` on a sexual card."""
    return list(jm["axes"]) + (list(jm.get("sexual_axes") or []) if card_sexual(row) else [])


def _mark(v) -> float | None:
    """A 0-10 rubric mark clamped, or None when the verdict does not carry a number."""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return min(10.0, max(0.0, float(v)))


def rubric_element(blk: dict, judge: dict, row: dict, jm: dict) -> dict | None:
    """rc3: a rubric-mode unit's one judge element: the mean of its axes / 10, or under ``combine: weakest``
    the lowest live axis the verdict carries (see JUDGE_COMBINES) (JUDGE's EXP-34
    spec: constraints and explicitness never in the mean; erotic only where the brief has sex); a
    verdict with ``refused: true`` scores 0. None = an axis is missing (the unit stays pending)."""
    sc = judge.get("scores") or {}
    axes = rubric_axes(jm, row)
    weakest = jm.get("combine", "mean") == "weakest"
    if weakest:
        dead = set(jm.get("dead_axes") or [])
        axes = [a for a in axes if a not in dead and _mark(sc.get(a)) is not None]
        if not axes:
            return None
    vals = []
    for a in axes:
        v = _mark(sc.get(a))
        if v is None:
            return None
        vals.append(v)
    refused = sc.get("refused") is True or judge.get("refused") is True
    jshare = (min(vals) if weakest else sum(vals) / len(vals)) / 10.0
    score = 0.0 if refused else jshare
    out = {"id": f"{blk['id']}-jrubric", "score": round(score, 6), "source": "judge",
           "outcome": "refused" if refused else "pass", "axes": axes,
           "axis_scores": {a: sc.get(a) for a in axes}}
    cw = float(jm.get("code_weight") or 0)
    if cw:
        # CHAT-RP session cards: the piece = (1 - w) x the rubric share + w x the code share (the block's mean
        # over its check elements: rules held, recall, canon corrections, no puppeting, no bleed); a refusal is 0
        code = blk.get("score")
        code = 0.0 if not isinstance(code, (int, float)) or isinstance(code, bool) else max(0.0, min(1.0, code))
        out.update(judge_share=round(jshare, 6), code_share=round(float(code), 6), code_weight=cw)
        if not refused:
            out["score"] = round((1 - cw) * jshare + cw * code, 6)
    return out


def _newest_ladder(rows: list[dict], prof: dict | None) -> dict[tuple, dict]:
    """{(unit, rung, attempt): ladder placement} of the newest row of each instance carrying a COMPLETE, CURRENT
    ``judge_ladder`` (rpladder.stale_reason: the card's ladder, the pair prompt, the benchmark's judge model and
    thinking off). A stale or partial placement is not read: the unit waits for a ladder pass ("judge pending")."""
    from . import rpladder as _rl
    try:
        cards = _rl.load()
    except _rl.LadderError:
        return {}
    j = (prof or {}).get("judge")
    jmodel = j.get("model_id") if isinstance(j, dict) else None
    out: dict[tuple, tuple] = {}
    for i, r in enumerate(rows):
        ch = r.get("chain")
        rec = r.get("judge_ladder")
        if not ch or ch.get("passed") is None or not isinstance(rec, dict) or not rec.get("complete"):
            continue
        if _rl.stale_reason(rec, cards, None, jmodel) is not None:
            continue
        k = (ch["id"], int(ch["rung"]), int(ch["attempt"]))
        stamp = (str(r.get("ts") or ""), i)
        if k not in out or stamp >= out[k][0]:
            out[k] = (stamp, rec)
    return {k: v[1] for k, v in out.items()}


def ladder_element(blk: dict, rec: dict, jm: dict) -> dict:
    """A ladder-mode unit's one element: (1 - code_weight) x position / 4 + code_weight x the code share.
    When its card has no usable ladder axis (lead 2026-11-15: "not judged on this card: no valid ladder" and the
    card keeps its code checks) the piece is the code share alone, marked ``no_ladder`` (the board names the card)."""
    if rec.get("no_ladder"):
        code = blk.get("score")
        code = 0.0 if not isinstance(code, (int, float)) or isinstance(code, bool) else max(0.0, min(1.0, code))
        return {"id": f"{blk['id']}-jladder", "score": round(float(code), 6), "source": "code", "outcome": "pass",
                "no_ladder": True, "card": rec.get("card"), "code_share": round(float(code), 6)}
    pos = max(0.0, min(4.0, float(rec["position"])))
    jshare = pos / 4.0
    out = {"id": f"{blk['id']}-jladder", "score": round(jshare, 6), "source": "judge", "outcome": "pass",
           "axes": list(rec.get("axes") or []), "position": round(pos, 6), "card": rec.get("card"),
           "level_cells": rec.get("level_cells")}
    cw = float(jm.get("code_weight") or 0)
    if cw:
        code = blk.get("score")
        code = 0.0 if not isinstance(code, (int, float)) or isinstance(code, bool) else max(0.0, min(1.0, code))
        out.update(judge_share=round(jshare, 6), code_share=round(float(code), 6), code_weight=cw,
                   score=round((1 - cw) * jshare + cw * code, 6))
    return out


#: a reasoning channel that repeats one line this often is looping (MiniMax rc3_person_s2-008: "SHE SAID YES JO"
#: six times in 1,005 characters, too short for degenerate.py's tail-compression test, which needs a long tail)
#: Long drafts cut at the real ceiling repeat dialogue lines too ("Hold still." 7 times in 133k characters), so a
#: loop also needs repeats to be a large share of all lines: 0.39 on the person row, at most 0.24 on every other
#: length-cut row of the four rc2 folders (MiniMax, Grok, DeepSeek flash, 8B; checked 2026-11-14).
LINE_LOOP_MIN = 4
LINE_LOOP_CHARS = 12
LINE_LOOP_SHARE = 0.30


def line_loop(text: str) -> bool:
    """True when one line of LINE_LOOP_CHARS+ characters occurs LINE_LOOP_MIN+ times AND repeated lines are at
    least LINE_LOOP_SHARE of all non-empty lines. Used only on a judged-part reply that was cut with no answer
    (chains.rubric_decided): a loop there is a degenerate 0, not a ceiling cut to exclude."""
    from collections import Counter
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    if not lines:
        return False
    c = Counter(lines)
    if max((v for k, v in c.items() if len(k) >= LINE_LOOP_CHARS), default=0) < LINE_LOOP_MIN:
        return False
    return sum(v - 1 for v in c.values() if v > 1) / len(lines) >= LINE_LOOP_SHARE


#: rc3 judged parts: how every unit ended (lead 2026-11-14). judged = the judge's rubric; refused / empty (after
#: the run's empty-reply chain) / degenerate score 0; cut = at its ceiling (the part's ``ran_out`` rule: excluded
#: and counted toward the 25 % rule, or 0); pending = no usable verdict yet.
UNIT_CLASSES = ("judged", "refused", "softened", "empty", "degenerate", "cut", "pending")
_UNIT_CLASS_OF = {"pass": "judged", "refused": "refused", "filtered": "refused", "empty": "empty",
                  "softened": "softened",
                  "degenerate": "degenerate", "ran out": "cut"}


def unit_classes_text(uc: dict | None) -> str:
    """"judged 21, cut 2, degenerate 1" (the non-zero classes, in UNIT_CLASSES order); "" when none."""
    return ", ".join(f"{k} {uc[k]}" for k in UNIT_CLASSES if uc and uc.get(k))


def rubric_decided(blk: dict, rows: list[dict]) -> dict | None:
    """rc3 judged parts (lead 2026-11-14, Jake: "regenerate, then thinking off if still empty", only replies
    given are graded): a rubric-mode unit that needs no judge. ``rows`` = the unit's rows of its final attempt
    (one per turn). A degenerate reply = a 0 piece; a reply cut at its ceiling = a "ran out" piece (the
    part's ``ran_out`` rule then excludes it or scores it 0); a reply still empty after the run's empty-reply
    chain (any turn) is the model's answer = a 0 piece. Never sent to the judge's model (judge_row stores an
    ``empty_generation`` verdict without a call) and never pending. None = an ordinary reply: the judge decides."""
    from . import degenerate as _dg
    from .willing import final_reply
    bid = f"{blk.get('id', '?')}-jrubric"
    rows = [r for r in rows if r] or []
    cut_blank = [r for r in rows if (r.get("finish_reason") == "length" or r.get("truncated"))
                 and not final_reply(r).strip()]
    if any(_dg.is_degenerate(r) for r in rows) or any(line_loop(r.get("reasoning") or "") for r in cut_blank):
        return {"id": bid, "score": 0.0, "source": "judge", "outcome": "degenerate"}
    if blk.get("outcome") == "ran out" or any(
            (r.get("finish_reason") == "length" or r.get("truncated")) and not final_reply(r).strip() for r in rows):
        return {"id": bid, "score": 0.0, "source": "judge", "outcome": "ran out"}
    if any(not final_reply(r).strip() for r in rows):
        return {"id": bid, "score": 0.0, "source": "judge", "outcome": "empty"}
    return None


def empty_piece(dec: dict, blk: dict, jm: dict | None) -> dict:
    """A unit still empty after the empty-reply chain (``rubric_decided`` outcome "empty") on a part whose piece
    mixes the judge with its code checks (``code_weight``, the RPSESSION cards): the judged half is 0 and the code
    half stands, piece = w x the code share (lead 2026-11-15: Grok FLAT-chatrp_pilot_s2-004, turn 10 empty on all
    three steps of the chain: .5 x 0 + .5 x .76). Empty is the model's answer: scored, never excluded, never pending
    (only a ceiling cut is excluded). Every other decided outcome, and a part without code_weight, is unchanged."""
    cw = float((jm or {}).get("code_weight") or 0)
    if dec.get("outcome") != "empty" or not cw:
        return dec
    code = blk.get("score")
    code = 0.0 if not isinstance(code, (int, float)) or isinstance(code, bool) else max(0.0, min(1.0, code))
    return {**dec, "score": round(cw * code, 6), "judge_share": 0.0, "code_share": round(float(code), 6),
            "code_weight": cw}


def _judge_results(judge: dict | None) -> list[dict]:
    return list(((judge or {}).get("elements") or {}).get("results") or [])


_GATE_SEP = "-_.:"


def gate_spec(gate) -> tuple | None:
    """The validated ``judge_gate``: None; ("unit", id) = one gate for the whole unit; or
    ("turn", gate pattern, scope regex) = a gate per turn. The per-turn form is a pattern with
    ``{n}`` (``"t{n}-act"``: turn n's gate is ``t<n>-act``, its scope every judge element whose
    id starts with the pattern through the separator after ``{n}``, ``t<n>-``) or a map
    ``{gate: "t{n}-act", scope: "t{n}-"}``. Raises ValueError."""
    if gate is None or gate == "":
        return None
    if isinstance(gate, str) and "{n}" not in gate:
        return ("unit", gate)
    if isinstance(gate, dict):
        if set(gate) != {"gate", "scope"}:
            raise ValueError("a per-turn map is {gate: '<..{n}..>', scope: '<prefix with {n}>'}")
        pat, scope = str(gate["gate"]), str(gate["scope"])
    elif isinstance(gate, str):
        pat = gate
        i = pat.index("{n}") + 3
        if i >= len(pat) or pat[i] not in _GATE_SEP:
            raise ValueError(f"a per-turn pattern needs a separator ({_GATE_SEP}) right after {{n}} "
                             f"(its turn scope is the id through it): {pat!r}")
        scope = pat[:i + 1]
    else:
        raise ValueError("a judge element id, a pattern with {n}, or {gate, scope}")
    if pat.count("{n}") != 1 or scope.count("{n}") != 1:
        raise ValueError("gate and scope each carry {n} once")
    if not pat.format(n=7).startswith(scope.format(n=7)):
        raise ValueError(f"turn n's gate {pat!r} lies outside its scope {scope!r}")
    a, b = scope.split("{n}")
    return ("turn", pat, re.compile(re.escape(a) + r"(\d+)" + re.escape(b)))


def _turn_of(rid: str, rx) -> int | None:
    m = rx.match(rid)
    return int(m.group(1)) if m else None


def _turn_gates(res: list[dict], spec: tuple) -> dict[int, dict | None]:
    """{turn: its gate result or None (missing)} for every turn with judge elements."""
    _k, pat, rx = spec
    by_id = {str(r.get("id")): r for r in res}
    turns = {t for t in (_turn_of(str(r.get("id")), rx) for r in res) if t is not None}
    return {t: by_id.get(pat.format(n=t)) for t in sorted(turns)}


def judge_gate_missing(judge: dict | None, gate) -> bool:
    """True when a part gates on ``gate`` and the unit's judge results lack that element (a
    judge failure, never read as a fade: the unit has not ended). Per-turn: any turn with
    judge elements and no gate result."""
    if not gate or not judge:
        return False
    spec = gate_spec(gate)
    if spec[0] == "unit":
        return not any(str(r.get("id")) == spec[1] for r in _judge_results(judge))
    return any(g is None for g in _turn_gates(_judge_results(judge), spec).values())


def board_elements(blk: dict, judge: dict | None, gate: str | None = None) -> list[dict] | None:
    """A unit's elements on the board: the code-graded ones stamped in-run plus, for
    ``element: check``, the judge stage's element results (``<unit>-j<id>``) when the row
    carries them, each scored on its ``credit`` (a 0/1/2 element: YES 1, PARTIAL 0.5, NO 0),
    or pass = 1 / 0 when the result gives no credit. None = the unit is a single-element unit (its score).

    ``gate`` (the part's ``judge_gate``): when that judge element's result is present and
    did not pass (NO; a PARTIAL gate passes, keeps its 0.5 and gates nothing), every OTHER
    judge element of the unit scores 0 (outcome ``gated``); the
    gate keeps its own 0; code elements are untouched. A missing gate result gates nothing
    (see :func:`judge_gate_missing`).

    A per-turn gate (``"t{n}-act"``, :func:`gate_spec`): turn n's gate NO zeroes only turn
    n's other judge elements; a turn whose gate result is missing gates nothing and marks only
    its own elements ``judge_gate_missing`` (the unit is not ended, section_stats)."""
    els = list(blk.get("elements") or [])
    if judge and blk.get("element") == "check":
        res = _judge_results(judge)
        spec = gate_spec(gate) if gate else None
        shut_of: dict[int, bool] = {}
        gate_ids: set[str] = set()
        if spec and spec[0] == "turn":
            tg = _turn_gates(res, spec)
            gate_ids = {spec[1].format(n=t) for t in tg}
            shut_of = {t: (g is not None and not g.get("pass")) for t, g in tg.items()}
            g, shut = None, False
        else:
            g = next((r for r in res if spec and str(r.get("id")) == spec[1]), None)
            shut = g is not None and not g.get("pass")
        for r in res:
            cr = r.get("credit")
            if isinstance(cr, (int, float)) and not isinstance(cr, bool):
                sc = min(1.0, max(0.0, float(cr)))   # the judge's own credit: PARTIAL on a 0/1/2 element = 0.5
            else:
                sc = 1.0 if r.get("pass") else 0.0   # no credit given: pass = 1
            e = {"id": f"{blk['id']}-j{r.get('id')}", "score": sc,
                 "outcome": "pass" if r.get("pass") else "wrong", "source": "judge"}
            if shut and r is not g:
                e.update(score=0.0, outcome="gated", reason=f"judge gate {gate} failed")
            elif spec and spec[0] == "turn":
                t = _turn_of(str(r.get("id")), spec[2])
                if t is not None and str(r.get("id")) not in gate_ids:
                    if shut_of.get(t):
                        e.update(score=0.0, outcome="gated", reason=f"judge gate {spec[1].format(n=t)} failed")
                    elif t in shut_of and spec[1].format(n=t) not in {str(x.get("id")) for x in res}:
                        e["judge_gate_missing"] = True
            els.append(e)
    return els or None


def icc_anova(groups: list[list[float]]) -> float | None:
    """One-way ANOVA intraclass correlation of the values in ``groups`` (unequal sizes, the
    n0 form); groups of one value are ignored. None with fewer than two groups of >= 2 or
    no variance."""
    gs = [g for g in groups if len(g) >= 2]
    G = len(gs)
    N = sum(len(g) for g in gs)
    if G < 2 or N <= G:
        return None
    gm = sum(sum(g) for g in gs) / N
    means = [sum(g) / len(g) for g in gs]
    msb = sum(len(g) * (m - gm) ** 2 for g, m in zip(gs, means)) / (G - 1)
    msw = sum((x - m) ** 2 for g, m in zip(gs, means) for x in g) / (N - G)
    n0 = (N - sum(len(g) ** 2 for g in gs) / N) / (G - 1)
    den = msb + (n0 - 1) * msw
    return None if den <= 0 else (msb - msw) / den


def mean_se(clusters: list[list[float]], unit: str) -> float | None:
    """SE of the mean over every element in ``clusters`` (a cluster = a unit: a chain, a
    flat item, a multi-item prompt, a per-turn item). ``unit`` "element": sd / sqrt(N) over
    the elements; "prompt": cluster-robust (between-unit) SE of the same mean."""
    xs = [x for c in clusters for x in c]
    N = len(xs)
    if N < 2:
        return None
    m = sum(xs) / N
    if unit == "element":
        sd = math.sqrt(sum((x - m) ** 2 for x in xs) / (N - 1))
        return sd / math.sqrt(N)
    C = len(clusters)
    if C < 2:
        return None
    return math.sqrt(C / (C - 1) * sum(sum(x - m for x in c) ** 2 for c in clusters)) / N


def _decided_without_judge(row: dict) -> bool:
    """A judged unit whose row needs no judge to be scored: the provider's content filter
    blocked it (a scored refusal, engagement 0)."""
    try:
        from . import report as _r
        return bool(row) and bool(_r.filtered_row(row))
    except Exception:  # noqa: BLE001
        return False


def _weight_of(e: dict) -> float:
    """An element's stored check weight (1 when absent)."""
    w = e.get("weight")
    try:
        return 1.0 if w is None else float(w)
    except (TypeError, ValueError):
        return 1.0


def _unit_means(clusters: list[list[tuple]]) -> list[list[tuple]]:
    """``average: unit``: each cluster's elements replaced by one value per component (the
    cluster's mean in that component), so every piece weighs the same inside a component."""
    out = []
    for cl in clusters:
        acc: dict = {}
        for t in cl:
            x, k, w = t[0], t[1], (t[2] if len(t) > 2 else 1.0)
            a = acc.setdefault(k, [0.0, 0.0])
            a[0] += w * x
            a[1] += w
        # weighted mean per component (sum w x / sum w); the piece then weighs 1
        out.append([(a[0] / a[1] if a[1] else 0.0, k, 1.0) for k, a in acc.items() if a[1]])
    return out


def _points_and_se(clusters: list[list[tuple]], comps: list[dict]):
    """Points = sum over components of points x (mean of its elements), and the
    cluster-robust SE of that estimate (linearised: per cluster z = sum over components of
    points x (S - m N) / N_total); a cluster is one element (element unit) or one unit.
    Each element is (x, component) or (x, component, weight): with weights, S and N are
    weighted sums (the component mean is sum w x / sum w) and z uses w (x - m) / N.
    Returns (points | None, se_points | None, {component: {points, max, share, n}})."""
    S: dict = {}
    N: dict = {}
    for cl in clusters:
        for t in cl:
            x, k, wt = t[0], t[1], (t[2] if len(t) > 2 else 1.0)
            S[k] = S.get(k, 0.0) + wt * x
            N[k] = N.get(k, 0) + wt
    out = {}
    pts = 0.0
    known = True
    for c in comps:
        k = c["name"]
        m = S[k] / N[k] if N.get(k) else None
        out[k] = {"points": None if m is None else m * c["points"], "max": c["points"], "share": m,
                  "n": sum(1 for cl in clusters for t in cl if t[1] == k), "weight": N.get(k, 0)}
        if m is None:
            known = False
        else:
            pts += m * c["points"]
    if not known:
        return None, None, out
    w = {c["name"]: c["points"] for c in comps}
    m = {k: S[k] / N[k] for k in N}
    C = len(clusters)
    if C < 2:
        return pts, None, out
    z2 = 0.0
    for cl in clusters:
        z = 0.0
        for t in cl:
            x, k, wt = t[0], t[1], (t[2] if len(t) > 2 else 1.0)
            if k in w:
                z += w[k] * wt * (x - m[k]) / N[k]
        z2 += z * z
    return pts, math.sqrt(C / (C - 1) * z2), out


def card_reps(prof: dict | None) -> dict[str, str]:
    """{unit id: its card's unit id} for the parts with ``se_cluster: card``: a ``seed_as`` unit maps to
    the unit of its target set that asks the same card (same ladder + slot), a target unit to itself.
    The headline bootstrap draws one id per card (headline.strata), and section_stats gives each of the
    card's seeds that draw's count, so a card's pieces are resampled together."""
    secs = {s["part"] for s in sections(prof) if s.get("se_cluster") == "card"}
    if not secs:
        return {}
    cs = [c for c in chain_cases(prof) if c["chain_spec"]["part"] in secs]
    first = {}
    for c in cs:
        sp = c["chain_spec"]
        if not sp.get("seed_ladder"):
            first[(sp["part"], sp["ladder"], sp["c"])] = c["id"]
    out = {}
    for c in cs:
        sp = c["chain_spec"]
        out[c["id"]] = first.get((sp["part"], sp.get("seed_ladder") or sp["ladder"], sp["c"]), c["id"])
    return out


def in_section(c: dict, sec: dict) -> bool:
    """Is unit ``c`` scored by section ``sec``: its own, or (a ``view_of`` section) the source part's units of
    ``view_sets`` (all of them when view_sets is unset)."""
    sp = c["chain_spec"]
    if sp["part"] == sec["part"]:
        return True
    v = sec.get("view_of")
    return bool(v) and sp["part"] == v and (not sec.get("view_sets") or sp["ladder"] in sec["view_sets"])


def section_stats(rows: list[dict], prof: dict | None, mult: dict | None = None) -> dict[str, dict]:
    """Per generated part ``<score>/<part>``. The part's ELEMENTS are what it averages
    (COMPOSE.md rc1-night: section = mean over elements, each weight 1): a chain = one
    element (held / K); a flat item = one element (its partial credit); a multi-item prompt
    = one element per sub-item; a per-turn item = one element per graded turn. Share = the
    mean element score over ended units; points = share x the part's ``max``.

    Resolution: SE of that mean, either per element (sd / sqrt(N)) or per unit (the
    cluster-robust between-unit SE, unit = chain / item / prompt), chosen by ``se_unit``
    (auto: element while the within-unit ICC, measured on stratum-centred scores, is below
    ``icc_max``; prompt otherwise or when it cannot be measured with multi-element units
    present); margin = 1.96 x SE x max. Also units / elements ended, mean level held
    (chains), turns (attempts), outcomes (per element), tokens (and per element), wall,
    cost, and per unit its rung states and elements. ``complete`` is False while any unit
    has not ended (the part is then partial, never extrapolated).

    ``mult`` (a headline bootstrap resample, report._BOOT): {unit id: times drawn}; a unit
    counts that many times and an undrawn one not at all. Only when it names a unit of the
    profile (a routed profile's units are not resampled: scale45 carries its margins)."""
    secs = sections(prof)
    if not secs:
        return {}
    chain_rows = [r for r in rows if r.get("chain")]
    res, starts = results_from_rows(chain_rows), starts_from_rows(chain_rows)
    blocks = _newest_blocks(chain_rows)
    judged = _newest_judge(chain_rows)
    rubric_judged = _newest_rubric(chain_rows)
    ladder_judged = (_newest_ladder(chain_rows, prof)
                     if any((s_.get("judge_mode") or {}).get("kind") == "ladder" for s_ in secs) else {})
    row_of = {id(r["chain"]): r for r in chain_rows}
    by_chain: dict[str, list[dict]] = {}
    for r in chain_rows:
        by_chain.setdefault(r["chain"]["id"], []).append(r)
    # 4.5 empty_stop_rule: turns stamped empty_stop (a finished reply with no visible content,
    # still empty after its regenerate), per unit -- counted per set beside ran-out
    estop_unit: dict[str, int] = {}
    # degenerate replies (degenerate.py): graded as failures, counted per set the same way
    dgen_unit: dict[str, int] = {}
    from . import degenerate as _dg
    for r in chain_rows:
        if r.get("empty_stop"):
            estop_unit[r["chain"]["id"]] = estop_unit.get(r["chain"]["id"], 0) + 1
        if _dg.is_degenerate(r):
            dgen_unit[r["chain"]["id"]] = dgen_unit.get(r["chain"]["id"], 0) + 1
    cases = chain_cases(prof)
    from . import routing as _routing
    # a routed (bank) profile: only the units on this model's route count (locator, unrouted
    # sets and its module); the modules it was not given are not taken, never pending
    on_route = _routing.taken(cases, prof, _routing.route_of(chain_rows))
    if on_route is not None:
        cases = [c for c in cases if c["id"] in on_route]
    if mult and any(c["id"] in mult for c in cases):
        # se_cluster: card -- a card's seeds are drawn together: every unit takes its card's count
        rep = card_reps(prof)
        cases = [c for c in cases for _ in range(int(mult.get(rep.get(c["id"], c["id"]), 0) or 0))]
    out = {}
    for sec in secs:
        part = _part_spec(prof, sec["part"]) or {}
        mx = float(part.get("max", part.get("weight", 0)) or 0)
        clusters: list[list[float]] = []
        ccomps: list[list[str]] = []
        cweights: list[list[float]] = []
        cavg: list[str] = []
        strata: list[list[tuple]] = []
        comps = sec.get("components")
        code_comp = component_of({"source": "code"}, comps)
        eng_fn = ENGAGEMENT.get(sec.get("engagement")) if sec.get("engagement") else None
        scorer = sec.get("scorer")
        jmode = sec.get("judge_mode") or ({"kind": "rubric", "axes": []} if scorer else None)
        is_ladder = bool(jmode) and jmode.get("kind") == "ladder"
        no_ladder_cards: dict[str, int] = {}
        n_noel = 0
        by_card = sec.get("se_cluster") == "card"
        #: rubric mode: every unit in one class (lead 2026-11-14), so the page says why units are missing
        ucls = {k: 0 for k in UNIT_CLASSES} if jmode else None
        card_ix: dict[tuple, int] = {}
        n_ended = 0
        pending = 0
        gate_missing = 0
        helds, per = [], {}
        turns = 0
        tok = {"prompt": 0.0, "thinking": 0.0, "completion": 0.0}
        wall = cost = 0.0
        priced = False
        outcomes: dict[str, int] = {}
        set_ro: dict[str, dict] = {}
        estop_set: dict[str, int] = {}
        dgen_set: dict[str, int] = {}
        n_units = 0
        occ: dict[str, int] = {}
        for c in (x for x in cases if in_section(x, sec)):
            # the n-th copy of a unit in a bootstrap draw joins the n-th copy of its card
            occ[c["id"]] = occ.get(c["id"], 0) + 1
            n_units += 1
            sp = c["chain_spec"]
            if estop_unit.get(c["id"]):
                estop_set[sp["ladder"]] = estop_set.get(sp["ladder"], 0) + estop_unit[c["id"]]
            if dgen_unit.get(c["id"]):
                dgen_set[sp["ladder"]] = dgen_set.get(sp["ladder"], 0) + dgen_unit[c["id"]]
            st = advance(sp["K"], sp["slip_guard"], res.get(c["id"], {}), starts.get(c["id"], 1))
            per[c["id"]] = {"ladder": sp["ladder"], "K": sp["K"], "ended": st["ended"],
                            "held": st["held"], "start": st["start"], "attempts": len(st["path"]),
                            "rungs": rung_states(sp["K"], st["path"], st["start"])}
            kind = sp.get("kind", "chain")
            continue_unit = True
            rung = sp["rungs"][0]
            stratum = (rung["family"], rung.get("variant"), rung["level"])
            if st["ended"]:
                if kind == "chain":
                    vals = [chain_score(st["held"], sp["K"])]
                    cps = [code_comp]
                    ws = [1.0]
                    helds.append(st["held"])
                    stratum = ("chain", sp["ladder"])
                    ocs = []
                else:
                    blk = blocks.get((c["id"], 1, 1)) or {}
                    fin = row_of.get(id(blk)) or {}
                    jdg = ((ladder_judged if is_ladder else rubric_judged) if jmode else judged).get(
                        (c["id"], 1, 1))
                    gate = sec.get("judge_gate")
                    dec = None
                    if jmode and (blk.get("judged") or scorer):
                        # degenerate / cut at the ceiling / still empty: decided without the judge, never pending.
                        # A code-only adult part (N8 bodies, U4 ground: a scorer, no judge) too: a sheet cut at its
                        # ceiling is "ran out" (the set's ran_out rule excludes it and counts it toward "not
                        # measured"), never a refusal (lead 2026-11-15, D's rc2 reference rows)
                        fts = fin.get("ts")
                        dec = rubric_decided(blk, [r for r in by_chain.get(c["id"], [])
                                                   if fts is None or r.get("ts") == fts] or [fin])
                        if dec is not None and not scorer:
                            dec = empty_piece(dec, blk, jmode)
                    if dec is not None:
                        continue_unit = True
                    elif blk.get("judged") and jdg is not None and judge_gate_missing(jdg, gate):
                        # the gate element is missing from the judge results: a judge failure,
                        # not a fade -- nothing is gated and the unit has not ended
                        per[c["id"]]["judge_gate_missing"] = True
                        per[c["id"]]["ended"] = False
                        pending += 1
                        gate_missing += 1
                        continue_unit = False
                    elif blk.get("judged") and jdg is None and not _decided_without_judge(fin):
                        # judge results pending: the unit has not ended (never scored on code alone)
                        per[c["id"]]["pending_judge"] = True
                        if scorer:
                            # the adult page's willingness lines count an explicit refusal the code saw before the
                            # judge has run (lead 2026-11-15); the unit still waits for its verdict
                            from . import adult45 as _adult
                            _chk = {e.get("check"): e for e in blk.get("elements") or [] if e.get("check")}
                            if _adult._refused_code(_chk):
                                per[c["id"]]["code_refused"] = True
                                jc_ = fin.get("judge_card") or {}
                                per[c["id"]]["card"] = jc_.get("card")
                                per[c["id"]]["row"] = jc_.get("row")
                        per[c["id"]]["ended"] = False
                        pending += 1
                        continue_unit = False
                        if ucls is not None:
                            ucls["pending"] += 1
                    else:
                        continue_unit = True
                    if scorer:
                        # the code explicitness gate's verdict, REPORTED ONLY (lead 2026-11-15 03:20): stored on the
                        # unit whether it is pending, decided or scored; it never moves a score or a pending state
                        from . import adult45 as _adult
                        if scorer in _adult.REPORT_GATE:
                            _fts = fin.get("ts")
                            _card = fin.get("judge_card") or {}
                            per[c["id"]]["code_gate"] = _adult.reported_gate({
                                "rows": [r for r in by_chain.get(c["id"], []) if _fts is None or r.get("ts") == _fts]
                                or [fin], "card": _card, "variant": _card.get("variant") or (rung.get("variant") or "")})
                    rub_el = dec
                    sels = None
                    if scorer and continue_unit:
                        # 4.5 adult page: the part's scorer turns the unit's code elements + the judge's marks
                        # into its element(s) (crucibleforge/adult45.py); None = a mark it needs is missing
                        from . import adult45 as _adult
                        var = (rung.get("variant") or "")
                        if dec is not None:
                            sels = [{**dec, "source": "code", "group": _adult.unit_group(scorer, var)}]
                        elif blk.get("judged") and jdg is None:
                            # only a provider content filter gets here (judge pending was handled above)
                            sels = [{"id": f"{c['id']}-{scorer}", "score": 0.0, "source": "code",
                                     "outcome": "refused", "group": _adult.unit_group(scorer, var)}]
                        else:
                            fts = fin.get("ts")
                            urows = [r for r in by_chain.get(c["id"], []) if fts is None or r.get("ts") == fts] \
                                or [fin]
                            card = fin.get("judge_card") or {}
                            sels = _adult.score_unit(scorer, {"unit": c["id"], "blk": blk, "rows": urows,
                                                              "judge": jdg if blk.get("judged") else None,
                                                              "card": card, "variant": card.get("variant") or var})
                        cid_ = (fin.get("judge_card") or {}).get("card")
                        for e_ in sels or []:
                            if cid_:
                                e_.setdefault("card", cid_)      # the bank item: the board names it, not the unit
                        if sels is None:
                            per[c["id"]]["pending_judge"] = True
                            per[c["id"]]["ended"] = False
                            pending += 1
                            continue_unit = False
                            ucls["pending"] += 1
                        elif not sels:
                            # nothing of this unit counts in this part (a view's card without such checks)
                            per[c["id"]]["no_elements"] = True
                            n_noel += 1
                            continue_unit = False
                    if dec is None and continue_unit and is_ladder and blk.get("judged") and jdg is not None:
                        rub_el = ladder_element(blk, jdg, jmode)
                        if rub_el.get("no_ladder"):
                            # the card has no valid ladder: scored on its code checks alone, named on the board
                            per[c["id"]]["no_ladder"] = True
                            per[c["id"]]["card"] = rub_el.get("card")
                            no_ladder_cards[str(rub_el.get("card"))] = no_ladder_cards.get(
                                str(rub_el.get("card")), 0) + 1
                    elif dec is None and continue_unit and jmode and blk.get("judged") and jdg is not None \
                            and not scorer:
                        rub_el = rubric_element(blk, jdg, fin, jmode)
                        if rub_el is None:
                            # the verdict lacks an axis: a judge failure, the unit has not ended
                            per[c["id"]]["pending_judge"] = True
                            per[c["id"]]["ended"] = False
                            pending += 1
                            continue_unit = False
                            ucls["pending"] += 1
                    if continue_unit:
                        if sels:
                            els = list(sels)
                            ucls[_UNIT_CLASS_OF.get(els[0].get("outcome"), "judged")] += 1
                        elif jmode:
                            # rubric mode: the piece is its rubric element (code elements are on record in
                            # the block, not scored here); a filtered row is a 0 piece
                            els = [rub_el] if rub_el is not None else [
                                {"id": f"{blk.get('id', c['id'])}-jrubric", "score": 0.0, "source": "judge",
                                 "outcome": "filtered"}]
                            ucls[_UNIT_CLASS_OF.get(els[0].get("outcome"), "judged")] += 1
                        else:
                            els = board_elements(blk, jdg, gate)
                        eng = float(eng_fn(fin)) if eng_fn and fin else 1.0
                        if els:
                            els = [e for e in els if component_of(e, comps) is not None]
                            rule = rung.get("ran_out", "zero")
                            ro = sum(1 for e in els if e.get("outcome") == "ran out")
                            ss = set_ro.setdefault(sp["ladder"], {"elements": 0, "ran_out": 0, "rule": rule,
                                                                  "excluded_units": 0})
                            ss["elements"] += len(els)
                            ss["ran_out"] += ro
                            if rule == "exclude" and ro and not _dg.is_degenerate(fin):
                                # the ceiling is unvalidated / known to cut real work: a "ran out"
                                # element is reported, not scored as a failed answer
                                els = [e for e in els if e.get("outcome") != "ran out"]
                                if els:
                                    outcomes["ran out"] = outcomes.get("ran out", 0) + ro   # still reported
                                if not els:
                                    ss["excluded_units"] += 1
                                    continue_unit = False
                        else:
                            # an element-less unit (TRACEVM, SESSION_STATE: one score per reply or
                            # session) is ONE element here: a cut reply is excluded under ``exclude``
                            # like an all-ran-out unit (lead 2026-11-14, D's 8B rc2 run); a degenerate
                            # cut reply stays a 0 (a loop is a failure, never a ran-out to drop)
                            rule = rung.get("ran_out", "zero")
                            ro = int(blk.get("outcome") == "ran out")
                            ss = set_ro.setdefault(sp["ladder"], {"elements": 0, "ran_out": 0, "rule": rule,
                                                                  "excluded_units": 0})
                            ss["elements"] += 1
                            ss["ran_out"] += ro
                            if (rule == "exclude" and ro and not _dg.is_degenerate(fin)
                                    and not dgen_unit.get(c["id"])):
                                ss["excluded_units"] += 1
                                continue_unit = False
                        if not continue_unit:
                            # every element ran out under `ran_out: exclude`: reported, not scored
                            vals, ocs = [], ["ran out"] * ro
                            per[c["id"]]["ran_out_excluded"] = True
                        elif els:
                            vals = [eng * float(e.get("score") or 0.0) for e in els]
                            cps = [component_of(e, comps) for e in els]
                            ew = sp.get("element_weight") or sec.get("element_weight", "one")
                            ws = [_weight_of(e) if ew == "check" else 1.0 for e in els]
                            ocs = [e.get("outcome") for e in els]
                            per[c["id"]]["elements"] = [
                                {**{k: e.get(k) for k in ("id", "score", "outcome", "reason", "row", "card",
                                                          "position", "judge_share", "code_share")
                                    if k in ("id", "score", "outcome", "reason") or (e.get(k) is not None and (
                                        k in ("row", "card") or e.get("position") is not None))},
                                 "component": component_of(e, comps)} for e in els]
                        else:
                            vals = [eng * float(blk.get("score") or 0.0)]
                            cps = [code_comp]
                            ws = [1.0]
                            ocs = [blk.get("outcome")]
                        if eng_fn:
                            per[c["id"]]["engagement"] = eng
                        for oc in ocs:
                            if oc:
                                outcomes[oc] = outcomes.get(oc, 0) + 1
                if kind == "chain" or continue_unit:
                    n_ended += 1
                    ck = (sp.get("seed_ladder") or sp["ladder"], sp["c"], occ[c["id"]]) if by_card else None
                    if ck is not None and ck in card_ix:
                        # se_cluster: card -- this seed's piece joins its card's cluster (averaged with
                        # the card's other seeds under average: unit, one SE cluster per card)
                        j_ = card_ix[ck]
                        clusters[j_] = clusters[j_] + vals
                        ccomps[j_] = ccomps[j_] + cps
                        cweights[j_] = cweights[j_] + ws
                        strata[j_] = strata[j_] + [stratum] * len(vals)
                    else:
                        if ck is not None:
                            card_ix[ck] = len(clusters)
                        clusters.append(vals)
                        ccomps.append(cps)
                        cweights.append(ws)
                        cavg.append(sp.get("average") or sec.get("average", "element"))
                        strata.append([stratum] * len(vals))
            for r in by_chain.get(c["id"], []):
                m = r.get("metrics") or {}
                tok["prompt"] += _num(m.get("prompt_tokens"))
                tok["thinking"] += _num(m.get("reasoning_tokens"))
                tok["completion"] += _num(m.get("completion_tokens"))
                wall += _num(m.get("total_s"))
                if r.get("cost_usd") is not None:
                    priced = True
                    cost += _num(r.get("cost_usd"))
                if r["chain"].get("passed") is not None:
                    turns += 1
                    if kind == "chain":
                        oc = r["chain"].get("outcome")
                        if oc:
                            outcomes[oc] = outcomes.get(oc, 0) + 1
        xs = [x for cl in clusters for x in cl]
        n = len(xs)
        n_el_declared = sum(len(c["chain_spec"].get("items") or [1]) for c in cases
                            if in_section(c, sec)
                            and c["chain_spec"].get("element", "item") == "item")
        mean = sum(xs) / n if n else None
        # ICC on stratum-centred scores (family / level means removed: a prompt holds one
        # family, so raw scores would read family differences as within-prompt correlation)
        smean: dict = {}
        for cl, stl in zip(clusters, strata):
            for x, k in zip(cl, stl):
                smean.setdefault(k, []).append(x)
        smean = {k: sum(v) / len(v) for k, v in smean.items()}
        centred = [[x - smean[k] for x, k in zip(cl, stl)] for cl, stl in zip(clusters, strata)]
        multi = any(len(cl) > 1 for cl in clusters)
        icc = icc_anova(centred) if multi else None
        unit = sec.get("se_unit", "auto")
        if unit == "auto":
            unit = ("element" if not multi or (icc is not None and icc < sec.get("icc_max", DEFAULT_ICC_MAX))
                    else "prompt")
        units_cl = [list(zip(cl, kc, wc)) for cl, kc, wc in zip(clusters, ccomps, cweights)]
        # average: unit = per piece first (one weighted mean per (unit, component), each piece
        # then weighs 1); the set's own average overrides the part's
        units_cl = [_unit_means([cl])[0] if av == "unit" else cl for cl, av in zip(units_cl, cavg)]
        cl_for_se = [[p] for cl in units_cl for p in cl] if unit == "element" else units_cl
        pts, se_pts, comp_out = _points_and_se(cl_for_se, comps or [{"name": "all", "points": mx}])
        sd = (math.sqrt(sum((x - mean) ** 2 for x in xs) / (n - 1)) if n > 1 else None)
        se = None if se_pts is None or not mx else se_pts / mx
        n_excl = sum(v["excluded_units"] for v in set_ro.values())
        complete = n_ended + n_excl + n_noel == n_units
        sets_ro = {k: {**v, "share": (v["ran_out"] / v["elements"]) if v["elements"] else 0.0}
                   for k, v in set_ro.items()}
        for k, n_es in estop_set.items():
            sets_ro.setdefault(k, {"elements": 0, "ran_out": 0, "rule": "zero", "excluded_units": 0,
                                   "share": 0.0})["empty_stop"] = n_es
        for k, n_dg in dgen_set.items():
            sets_ro.setdefault(k, {"elements": 0, "ran_out": 0, "rule": "zero", "excluded_units": 0,
                                   "share": 0.0})["degenerate"] = n_dg
        for v in sets_ro.values():
            v.setdefault("empty_stop", 0)
            v.setdefault("degenerate", 0)
            # more than RAN_OUT_FLAG of a set's elements ran out: the ceiling, not the model,
            # may be deciding the score -- flagged, never silently averaged
            v["ceiling_too_low"] = v["share"] > RAN_OUT_FLAG
        out[sec["part"]] = {
            "share": None if pts is None or not mx else pts / mx, "points": pts, "max": mx,
            # the plain element mean: what a max-0 (pack-only) part scored, shown apart
            "mean": mean,
            "components": comp_out if comps else None, "pending_judge": pending,
            "judge_gate_missing": gate_missing,
            "chains": sec["chains"], "flat": n_units - sec["chains"], "units": n_units,
            "ended": n_ended + n_excl, "elements": n, "complete": complete,
            "ran_out_excluded_units": n_excl, "sets": sets_ro, "unit_classes": ucls,
            **({"ladder": {"no_ladder_cards": sorted(no_ladder_cards), "windows": list(jmode.get("windows") or []),
                           "text": LADDER_BOARD_TEXT}}
               if is_ladder else {}),
            "empty_stop": sum(estop_set.values()),
            "degenerate": sum(dgen_set.values()),
            "ceiling_too_low": sorted(k for k, v in sets_ro.items() if v["ceiling_too_low"]),
            "elements_declared": n_el_declared if all(c["chain_spec"].get("element", "item") == "item"
                                                      for c in cases if in_section(c, sec)) else None,
            "mean_level_held": (sum(helds) / len(helds)) if helds else None,
            "sd": sd, "se": se, "se_unit": unit, "icc": icc,
            "se_points": se_pts,
            "margin_points": None if se_pts is None else Z95 * se_pts,
            "margin_90": None if se_pts is None else Z90 * se_pts,
            "turns": turns, "outcomes": outcomes, "tokens": {k: int(v) for k, v in tok.items()},
            "tokens_per_element": (round(sum(tok.values()) / n, 1) if n else None),
            "wall_s": round(wall, 1), "cost_usd": round(cost, 6) if priced else None,
            "per_chain": per}
    return out


def score_stats(stats: dict[str, dict]) -> dict[str, dict]:
    """Per score (``work``, ``chat`` ...): the generated parts combined, weights = points:
    points = sum of the parts' points, SE = sqrt(sum se_points^2) (parts are independent
    samples), margin = 1.96 x SE. Hand-written fixed parts carry no sampling variance here
    and are not in it."""
    out: dict[str, dict] = {}
    for key, s in stats.items():
        score = key.split("/", 1)[0]
        o = out.setdefault(score, {"parts": [], "points": 0.0, "max": 0.0, "var": 0.0,
                                   "complete": True, "known": True})
        o["parts"].append(key)
        o["max"] += s["max"]
        o["complete"] &= bool(s["complete"])
        if s["points"] is None or s["se_points"] is None:
            o["known"] = False
        else:
            o["points"] += s["points"]
            o["var"] += s["se_points"] ** 2
    for o in out.values():
        se = math.sqrt(o.pop("var")) if o["known"] else None
        if not o.pop("known"):
            o["points"] = None
        o["se_points"] = se
        o["margin_points"] = None if se is None else Z95 * se
        o["margin_90"] = None if se is None else Z90 * se
    return out


def overall_stats(stats: dict[str, dict], prof: dict | None = None) -> dict:
    """Every generated part combined into the Overall (weights = points): points = sum of
    the parts' points, SE = sqrt(sum se_points^2); with ``headline.overall: mean`` the
    Overall is the mean of the scores, so both are divided by the number of scores.
    Hand-written fixed parts carry no sampling variance here."""
    mode = (((prof or {}).get("headline") or {}).get("overall") or "sum")
    n_scores = max(1, len((prof or {}).get("scores") or {})) if mode == "mean" else 1
    pts, var, mx, known, complete = 0.0, 0.0, 0.0, True, True
    for s_ in stats.values():
        mx += s_["max"]
        complete &= bool(s_["complete"])
        if s_["points"] is None or s_["se_points"] is None:
            known = False
        else:
            pts += s_["points"]
            var += s_["se_points"] ** 2
    se = math.sqrt(var) / n_scores if known and stats else None
    return {"parts": list(stats), "points": pts / n_scores if known and stats else None,
            "max": mx / n_scores, "se_points": se, "margin_points": None if se is None else Z95 * se,
            "margin_90": None if se is None else Z90 * se,
            "complete": complete, "mode": mode}


_COLS = ("Points", "Units ended", "Elements", "Mean level held", "SE (pts)", "SE unit (ICC)",
         "Margin +/- 90% (pts)", "Turns", "Tokens (prompt/think/out)", "Tokens/element", "Cost")


def _cells(s: dict, margin: float | None = None, own_margin: bool = True) -> list[str]:
    def f(v):
        return "-" if v is None else f"{v:.2f}"
    t = s["tokens"]
    unit = s.get("se_unit") or "element"
    icc = s.get("icc")
    ucx = unit_classes_text(s.get("unit_classes"))
    return [f"{f(s['points'])} / {s['max']:g}", f"{s['ended']}/{s['units']}" + (f" ({ucx})" if ucx else ""),
            str(s.get("elements", s["ended"])),
            f(s["mean_level_held"]), f(s["se_points"]),
            unit + ("" if icc is None else f" ({icc:.2f})"),
            f(s.get("margin_90")) if own_margin else ("-" if margin is None else f"{margin:.1f}"), str(s["turns"]),
            f"{t['prompt']}/{t['thinking']}/{t['completion']}",
            "-" if s.get("tokens_per_element") is None else f"{s['tokens_per_element']:.0f}",
            "-" if s["cost_usd"] is None else "$" + format(s["cost_usd"], ".4f")]


def render_lines(stats: dict[str, dict]) -> list[str]:
    """Markdown lines: one row per chain section of one model."""
    if not stats:
        return []
    L = ["| Section | " + " | ".join(_COLS) + " |", "|---" * (len(_COLS) + 1) + "|"]
    L += ["| " + " | ".join([key] + _cells(s)) + " |" for key, s in stats.items()]
    return L


def render_board(labels: list[str], per_model: dict[str, dict], prof: dict | None = None,
                 margins: dict[str, dict] | None = None) -> list[str]:
    """The board's "Generated items" block: one row per (model, generated part) and one per
    (model, score) combining them, each carrying its measured resolution (SE and margin of
    error from the between-unit variance).

    ``margins`` ({label: the headline's ``m``}; a non-routed points board, lead 2026-11-14): the
    board shows ONE margin per part, section and Overall -- the Scorecard's (its bootstrap
    resamples every part's units). The part rows' Margin column then carries that number, and the
    generated-parts-only score rows (a different sum, which printed a second margin for "the
    same" section) are left out: the sections are the Scorecard's rows."""
    rows = [(l, key, s) for l in labels for key, s in (per_model.get(l) or {}).items()]
    if not rows:
        return []
    one = margins is not None
    L = ["", "### Generated items (chains and flat sets)", "",
         "| Model | Part | " + " | ".join(_COLS) + " |", "|---" * (len(_COLS) + 2) + "|"]
    L += ["| " + " | ".join([l, key] + _cells(s, ((margins or {}).get(l) or {}).get(key), not one)) + " |"
          for l, key, s in rows]
    comb = [] if one else [(l, sc, o) for l in labels for sc, o in score_stats(per_model.get(l) or {}).items()
                           if len(o["parts"]) > 1]
    comb += [] if one else [(l, "Overall", overall_stats(per_model.get(l) or {}, prof)) for l in labels
                            if len(per_model.get(l) or {}) > 1
                            and len(score_stats(per_model.get(l) or {})) > 1]
    if comb:
        def f(v):
            return "-" if v is None else f"{v:.2f}"
        L += ["", "| Model | Score (generated parts; Overall = all) | Points | SE (pts) | Margin +/- 90% (pts) |",
              "|---|---|---|---|---|"]
        L += [f"| {l} | {sc} | {f(o['points'])} / {o['max']:g} | {f(o['se_points'])} | "
              f"{f(o.get('margin_90'))} |" for l, sc, o in comb]
    marg = ("Margin = the Scorecard's ± for that part (half the 90 % interval of its bootstrap, the "
            "part's units resampled; the section and Overall margins are the Scorecard's); SE = the "
            "analytic standard error of the part's mean, per element or per unit (cluster) when the "
            "within-unit correlation (ICC) is not about 0 (its resolution, for diagnosis)."
            if one else
            "Margin = 1.645 x the standard error of that mean (90 %), per element or per unit (cluster) when "
            "the within-unit correlation (ICC) is not about 0; a score and the Overall combine "
            "their parts by points.")
    L += ["", "*A chain climbs its rungs (one seeded, code-graded instance each) until it fails "
              "(a profile may allow a retry); chain score = highest rung held / rungs, rungs below "
              "a later start are credited. A flat instance scores its own partial credit. Points = "
              "mean element score x the part's maximum (an element = a chain, a flat item, a "
              "sub-item of a multi-item prompt, or a graded turn of a per-turn item). " + marg +
              " Turns = attempts; "
              "\"ran out\" = the attempt hit its ceiling and failed; \"context\" = a multi-turn "
              "session's context was exhausted and it failed there. Tokens and cost are recorded, "
              "never capped (R23).*"]
    return L
