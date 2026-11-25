"""The benchmark definition: a named, fixed selection of cases + budget/judge
settings. There is ONE benchmark, ``bench`` (profiles/bench.yaml) — the
default for run/all/judge/recover — scored as two headline numbers, Chat and
Coding (see report.py). ``--profile`` still resolves other files so a local
experiment can be tried, but only ``bench`` rows count on the board.

A profile is a YAML file (``<repo>/profiles/<name>.yaml`` or
``<config dir>/profiles/<name>.yaml``):

    name: bench
    description: ...
    judge:                       # default judge when --judge is not given
      provider: studioforge
      model_id: ...
      extra_body: {chat_template_kwargs: {enable_thinking: false}}
    thinking_max_tokens_factor: 4
    thinking_max_tokens_cap: 16384
    no_recovery: [math]          # categories that skip reasoning-overflow recovery
    repeats: {perf: 2, rp: 1, nsfw: 1, coding: 1, ...}
    max_tokens: {rp: 1500, nsfw: 1500}     # per-category override of case budgets
    cases:
      perf: [P1-tiny, ...]
      rp: [...]
      ...

Profiles only SELECT from the case files (ids must exist); scores of a case are
comparable across profiles. Rows/meta are stamped with the profile name.

Private content (docs/CASES.md "Private categories") never sits in a tracked file:

- ``profiles/<name>.private.yaml`` is an OVERLAY merged into ``<name>.yaml``
  (``cases``/``repeats``/``max_tokens``/``labels`` merge per key,
  ``scoring.chat``/``scoring.coding`` merge per weight, ``private`` lists are
  unioned; any other key replaces). It is how the one benchmark gains a
  private component without the public profile naming it.
- ``profiles/private/<name>.yaml`` holds whole private profiles.
- ``cases/private/<cat>.json`` holds private case files (config.py).

All three are git-ignored and refused by ``scripts/scrub_check.py``.
:func:`private_scope` is the single source of truth for what a public
artifact must not contain.
"""
from __future__ import annotations

import copy
import os
from pathlib import Path

import yaml

from . import config as _config
from .config import ConfigError, ROOT_DIR, load_cases

PROFILES_DIR = ROOT_DIR / "profiles"
#: The benchmark a bare command selects (--profile not given): 4.5 since the release; 4.1 stays
#: selectable with --profile bench (ARCHIVE_PROFILE below).
DEFAULT_PROFILE = "bench45"
#: The 4.1 benchmark (ship plan C8, the release flip): what a stored row or verdict WITHOUT a profile was
#: made under, and the profile whose 4.1-only rules key on it (the release-seed fallback, the code-bank
#: clamp floor, the verdict-regrade gate, the escalation and tier-2/3 phases). It stays "bench" when
#: DEFAULT_PROFILE (the CLI's choice) moves to the 4.5 profile, so the flip changes which profile a bare
#: command selects and nothing about how either profile's rows are stamped, judged or scored.
ARCHIVE_PROFILE = "bench"


def list_profiles(cfg: dict | None = None) -> list[str]:
    names: set[str] = set()
    for d in _dirs(cfg):
        if d.is_dir():
            names.update(p.stem for p in d.glob("*.yaml")
                         if not p.stem.endswith(".private"))
    return sorted(names)


def _dirs(cfg: dict | None) -> list[Path]:
    dirs = []
    if cfg and cfg.get("_path"):
        dirs.append(Path(cfg["_path"]).parent / "profiles")
    dirs.append(PROFILES_DIR)
    if _config.PRIVATE_ENABLED:
        dirs.append(PROFILES_DIR / "private")
    return dirs


#: overlay keys merged per entry (everything else in an overlay replaces)
_MERGE_KEYS = ("cases", "repeats", "case_repeats", "max_tokens", "labels", "part_cases",
               "custom")


def _merge_overlay(prof: dict, over: dict, name: str) -> dict:
    out = dict(prof)
    for k, v in over.items():
        if k in _MERGE_KEYS:
            if not isinstance(v, dict):
                raise ConfigError(f"profile {name} overlay: {k!r} must be a mapping")
            out[k] = {**(prof.get(k) or {}), **v}
        elif k == "scoring":
            sc = copy.deepcopy(prof.get("scoring") or {})
            for grp, weights in (v or {}).items():
                sc[grp] = {**(sc.get(grp) or {}), **(weights or {})}
            out["scoring"] = sc
        elif k == "private":
            out["private"] = sorted(set(prof.get("private") or []) | set(v or []))
        else:
            out[k] = v
    return out


def activate_release_seed(prof: dict | None) -> None:
    """Make ``prof`` the ACTIVE profile for generated cases: its ``release_seed`` (or, when it has
    none, the default) becomes the process's profile seed, replacing whatever an earlier profile
    set. Called once at command entry (cli.main) and when a caller switches profile; merely
    loading a profile (a listing, a board build, judge layering) never changes the seed.
    ``$CRUCIBLEFORGE_RELEASE_SEED`` still wins (gen.release_seed)."""
    from . import gen as _gen
    seed = (prof or {}).get("release_seed")
    _gen.set_release_seed(None if seed is None else seed)


def activate_cases_root(prof: dict) -> None:
    """Select the case tree of the ACTIVE profile (key ``cases_dir``, relative to
    the repo root, default ``cases``). Called where a profile becomes the active
    one (CLI entry, apply_profile, the board), never from incidental loads."""
    root = ROOT_DIR / (prof.get("cases_dir") or "cases")
    if _config.CASES_DIR != root:
        _config.set_cases_root(root)


def _part_spec(prof: dict, part: str | None) -> dict | None:
    """A score part's spec; ``part`` is ``<part>`` or ``<score>/<part>``."""
    if not part:
        return None
    score, _, pk = str(part).rpartition("/")
    for sk, sv in (prof.get("scores") or {}).items():
        if score and sk != score:
            continue
        spec = ((sv or {}).get("parts") or {}).get(pk)
        if isinstance(spec, dict):
            return spec
    return None


def _part_category(prof: dict, part: str | None) -> str | None:
    """The case category a score part scores (its first ``scoring`` entry)."""
    spec = _part_spec(prof, part)
    return (spec.get("scoring") or [None])[0] if spec else None


def core_level_of(prof: dict, family: str, variant: str | None = None) -> int | None:
    """The Core level of a family from the profile's ``core_level`` block (data, so the
    levels P1 picks are a one-line change): ``<family>/<variant>`` wins over ``<family>``."""
    cl = prof.get("core_level") or {}
    for k in ((f"{family}/{variant}",) if variant else ()) + (family,):
        if cl.get(k) is not None:
            return cl[k]
    return None


def _core_entries(prof: dict) -> list[tuple[int, dict]]:
    """``core_generated`` entries that apply in this tree: an entry that names a branch it
    waits for (``pending: a7-nsfw``) is held back, loudly (see :func:`core_pending`), until its
    family is registered; every other entry is checked and raises on an unknown family."""
    from . import gen as _gen
    known = set(_gen.families())
    out = []
    for n, e in enumerate(prof.get("core_generated") or []):
        if isinstance(e, dict) and e.get("pending") and str(e.get("family")) not in known:
            continue
        out.append((n, e))
    return out


def core_pending(prof: dict) -> list[dict]:
    """Core instances held back because their family is not in this tree yet (``pending:`` names
    the branch that brings it): ``[{id, category, part, family, level, variant, pending}]``.
    They are Core by declaration (the composition test and the budget count them); they run only
    once the family exists, when they stop being pending with no profile edit."""
    from . import gen as _gen
    known = set(_gen.families())
    out = []
    for n, e in enumerate(prof.get("core_generated") or []):
        if not (isinstance(e, dict) and e.get("pending") and str(e.get("family")) not in known):
            continue
        fam, var = str(e["family"]), e.get("variant")
        level = e.get("level") or core_level_of(prof, fam, var)
        cat = e.get("category") or _part_category(prof, e.get("part"))
        for i in range(1, int(e.get("count", 1)) + 1):
            out.append({"id": f"{fam.upper()}{'-' + str(var) if var else ''}-core-{level}-{i}",
                        "category": cat, "part": e.get("part"), "family": fam, "level": level,
                        "variant": var, "pending": str(e["pending"])})
    return out


def core_generated_cases(prof: dict) -> list[dict]:
    """Core composition as data (``core_generated: [{family, level, variant, count, part,
    category}]``): the generated instances a profile puts in Core, as unmaterialised case
    objects ``{id, category, core, generator}``. ``level`` may be left out: it then comes
    from the profile's ``core_level`` block. Ids ``<FAMILY>[-<variant>]-core-<level>-<i>``
    (i from 1), keyed on (release seed, family, variant, "core", level, i) so renaming a part
    or reordering the list never regenerates an instance. Pure and deterministic."""
    from . import gen as _gen
    from .ladder import instance_key
    out = []
    rs = _gen.release_seed()
    for n, e in _core_entries(prof):
        where = f"profile {prof.get('name')}: core_generated[{n}]"
        if not isinstance(e, dict) or not e.get("family"):
            raise ConfigError(f"{where}: needs a mapping with a 'family'")
        fam, var = str(e["family"]), e.get("variant")
        level, count = e.get("level") or core_level_of(prof, fam, var), e.get("count", 1)
        for k, v in (("level", level), ("count", count)):
            if not isinstance(v, int) or isinstance(v, bool) or v < 1:
                raise ConfigError(f"{where}: {k} is a positive integer"
                                  + (" (give it, or set core_level for the family)" if k == "level" else ""))
        try:
            _gen.check_variant(_gen.get(fam), {"type": fam, "variant": var})
        except Exception as ex:  # noqa: BLE001 - unknown family / variant
            raise ConfigError(f"{where}: {ex}") from ex
        cat = e.get("category") or _part_category(prof, e.get("part"))
        if not cat:
            raise ConfigError(f"{where}: give a 'category' or a 'part' that names a scored part")
        for i in range(1, count + 1):
            gen = {"type": fam, "level": level, "key": instance_key(rs, fam, var, "core", level, i)}
            if var:
                gen["variant"] = var
            case = {"id": f"{fam.upper()}{'-' + str(var) if var else ''}-core-{level}-{i}",
                    "category": cat, "core": True, "part": e.get("part"), "generator": gen}
            if e.get("budget"):
                # R23 (2026-11-13): the entry's own output budget (runner.fixed_budget), e.g. the
                # context maximum, instead of the board regime's thinking cap
                case["budget"] = int(e["budget"])
            out.append(case)
    ids = [c["id"] for c in out]
    if len(set(ids)) != len(ids):
        raise ConfigError(f"profile {prof.get('name')}: core_generated gives duplicate ids "
                          f"(two entries with the same family, variant and level)")
    return out


def ranked_panel(prof: dict, board: dict) -> dict:
    """The ranked panel of the final pass (FABLE-PLAN-REVIEW-v5 gap 11): the UNION of the SFW
    top class (``panel.sfw_hosted_top`` hosted + ``panel.sfw_local_top`` local models by Overall)
    and the NSFW top class (``panel.nsfw_top`` models of any kind by the NSFW score), from a 4.1
    board digest ``{label: {hosted: bool, overall: float, nsfw: float}}``. Picking by SFW class
    alone leaves out the NSFW leaders and ranks hosted models that refuse. Returns
    ``{sfw, nsfw, ranked}`` (label lists; ``ranked`` = the union, SFW order first)."""
    pn = prof.get("panel") or {}
    if not pn:
        raise ConfigError(f"profile {prof.get('name')}: no panel: block")

    def top(rows, key, n):
        return [l for l, _ in sorted(rows, key=lambda kv: (-(kv[1].get(key) or 0), kv[0]))[:int(n)]]
    rows = list(board.items())
    sfw = (top([r for r in rows if r[1].get("hosted")], "overall", pn.get("sfw_hosted_top", 15))
           + top([r for r in rows if not r[1].get("hosted")], "overall", pn.get("sfw_local_top", 3)))
    nsfw = top(rows, "nsfw", pn.get("nsfw_top", 15))
    return {"sfw": sfw, "nsfw": nsfw, "ranked": list(dict.fromkeys(sfw + nsfw))}


def flag_columns(prof: dict | None) -> list[dict]:
    """Flag columns a profile declares (``flags: {key: {label, file}}``, e.g. the
    NSFW ``lockout`` intensity ladder). ``available`` = its case file exists; an
    unavailable flag is "not run" (value None), never 0."""
    out = []
    for key, spec in ((prof or {}).get("flags") or {}).items():
        f = ROOT_DIR / str((spec or {}).get("file") or "")
        out.append({"key": key, "label": (spec or {}).get("label", key),
                    "file": f, "available": f.is_file(), "scored": bool((spec or {}).get("scored"))})
    return out


def apply_main_nsfw(prof: dict) -> dict:
    """T7: Main NSFW (MN1-MN4, the four stored quality cases) behind ``main_nsfw.enabled`` (OFF; pending
    owner decision J7). Enabled: the case ids join ``cases.nsfw``, each binds to its part (nsfw/mn1..mn4) and
    leaves the pack file it also sits in (``pack_exclude``). Disabled: nothing is added and the parts hold no
    cases (the NSFW score reads "not run"). Frontier NSFW is never wired (pending J11). In place; returns prof."""
    mn = prof.get("main_nsfw")
    if not isinstance(mn, dict) or not mn.get("enabled"):
        return prof
    comp = str(mn.get("composition") or "rc1")
    if comp == "rc2":
        return _apply_main_nsfw_rc2(prof, mn)
    if comp != "rc1":
        raise ConfigError(f"profile {prof.get('name')}: main_nsfw.composition is rc1 or rc2, not {comp!r}")
    cases = mn.get("cases") or {}
    prof.setdefault("cases", {})["nsfw"] = list(cases.values())
    parts = ((prof.get("scores") or {}).get("nsfw") or {}).get("parts") or {}
    for pk, cid in cases.items():
        if pk in parts:
            parts[pk]["cases"] = [cid]
            # Admin N (N1): per-part points (``main_nsfw.points: {part: pts}``), else points_each
            pts = (mn.get("points") or {}).get(pk, mn.get("points_each", parts[pk].get("max", 2.5)))
            parts[pk]["max"] = float(pts)
    prof["pack_exclude"] = list(dict.fromkeys(list(prof.get("pack_exclude") or []) + list(cases.values())))
    # Admin N (task C): ``main_nsfw.lockout_points`` scores the Lockout rungs as a part
    # (every rung one code-graded element, a refusal a 0 result); the flag column stays
    lk = mn.get("lockout_points")
    if lk and KEY_LOCKOUT in ((prof.get("flags") or {})):
        prof["flags"][KEY_LOCKOUT] = {**(prof["flags"][KEY_LOCKOUT] or {}), "scored": True}
        sc = (prof.get("scores") or {}).get("nsfw")
        if isinstance(sc, dict):
            sc.setdefault("parts", {})["lockout"] = {
                "label": "Lockout (intensity rungs)", "short": "Lock", "scoring": {"lockout": 1.0},
                "max": float(lk)}
    return prof


KEY_LOCKOUT = "lockout"

#: rc1 part keys of the NSFW score; rc2 replaces them (pack parts stay)
RC1_PARTS = ("mn1", "mn2", "mn3", "mn4", "mn5")


def _apply_main_nsfw_rc2(prof: dict, mn: dict) -> dict:
    """rc2 (Admin N2, EXP-20261113-21 COMPOSITION.md rc2), expressed on the chains engine's judged
    flat parts (docs/CHAINS.md "Judged parts"): the NSFW score's Main parts become
    ``main_nsfw.rc2.parts`` (rc1's mn1..mn5 leave the score; pack parts stay) and
    ``main_nsfw.rc2.chains`` joins the profile's ``chains`` block, so nothing of rc2 is live while
    the composition is rc1. The Lockout part keeps its own path (``lockout_points``). In place;
    returns prof."""
    rc2 = mn.get("rc2") or {}
    sc = (prof.get("scores") or {}).get("nsfw")
    if not isinstance(sc, dict):
        raise ConfigError(f"profile {prof.get('name')}: main_nsfw rc2 needs scores.nsfw")
    new_parts = {pk: {**dict(spec or {}), "cases": [], "max": float((spec or {}).get("max", 0))}
                 for pk, spec in (rc2.get("parts") or {}).items()}
    old = sc.get("parts") or {}
    new_parts.update({k: v for k, v in old.items() if k not in RC1_PARTS and k not in new_parts})
    sc["parts"] = new_parts
    frag = rc2.get("chains") or {}
    clash = set(frag) & set(prof.get("chains") or {})
    if clash:
        raise ConfigError(f"profile {prof.get('name')}: main_nsfw.rc2.chains repeats {sorted(clash)}")
    prof["chains"] = {**(prof.get("chains") or {}), **copy.deepcopy(frag)}
    prof.setdefault("cases", {})["nsfw"] = []
    for pk in rc2.get("drop_parts") or []:
        # rc4 adult page (N3-WIRE): the older adult packs leave the score (they stop running)
        new_parts.pop(pk, None)
    for fk in rc2.get("flags_off") or []:
        # rc4: a flag column the page no longer carries (the Lockout ladder: its willingness is N7 / U1 now)
        (prof.get("flags") or {}).pop(fk, None)
    lk = rc2.get("lockout_points", mn.get("lockout_points"))
    if lk and KEY_LOCKOUT in (prof.get("flags") or {}):
        prof["flags"][KEY_LOCKOUT] = {**(prof["flags"][KEY_LOCKOUT] or {}), "scored": True}
        new_parts["lockout"] = {"label": "Lockout (intensity rungs)", "short": "Lock",
                                "scoring": {"lockout": 1.0}, "max": float(lk)}
    if rc2.get("subtotals"):
        # rc3: the NSFW score's two numbers (report.score_subtotals), each over parts of THIS score
        subs = {}
        for nm, st in rc2["subtotals"].items():
            bad = [pk for pk in (st or {}).get("parts") or [] if pk not in new_parts]
            if not (st or {}).get("parts") or bad:
                raise ConfigError(f"profile {prof.get('name')}: main_nsfw.rc2.subtotals.{nm} names no / "
                                  f"unknown parts {bad}")
            subs[nm] = {"label": st.get("label") or nm, "score": "nsfw", "parts": list(st["parts"])}
        if rc2.get("total"):
            # rc4: the page's printed total of its subtotals ("NSFW + Uncensored" /20; report.score_subtotals)
            t = rc2["total"]
            subs["total"] = {"label": t.get("label") or "Total", "score": "nsfw", "kind": "total",
                             "of_subtotals": [k for k in subs], "of": float(t.get("of", 10.0 * len(subs))),
                             "parts": [pk for st in subs.values() for pk in st["parts"]]}
        prof["score_subtotals"] = subs
    from .chains import sections as _chain_sections
    _chain_sections(prof)          # structure / families / levels / component points (fail closed)
    return prof


# Parsed profile YAML, per (path, mtime_ns, size): the board re-loads the
# benchmark profile several hundred times per report and the pure-Python YAML
# parser was a third of its time (report profile 2026-11-12). A changed file
# has a new stat key and is parsed again; every caller gets its own deep copy.
_YAML_CACHE: dict[tuple[str, int, int], object] = {}
_YAML_PLAIN: dict[tuple[str, int, int], bool] = {}


_ATOMS = (str, int, float, bool, type(None), bytes)


def _shared_or_odd(o, seen: set) -> bool:
    """True when ``o`` holds a container reachable twice (a YAML alias) or a value type other than dict / list /
    plain scalar: only then does ``copy.deepcopy`` do something a plain structural copy would not."""
    if isinstance(o, dict) or isinstance(o, list):
        if id(o) in seen:
            return True
        seen.add(id(o))
        it = o.values() if isinstance(o, dict) else o
        if isinstance(o, dict) and not all(type(k) in _ATOMS for k in o):
            return True
        return any(_shared_or_odd(v, seen) for v in it)
    return type(o) not in _ATOMS


def _plain_copy(o):
    if type(o) is dict:
        return {k: _plain_copy(v) for k, v in o.items()}
    if type(o) is list:
        return [_plain_copy(v) for v in o]
    return o


def _yaml_file(p: Path):
    st = p.stat()
    key = (str(p), st.st_mtime_ns, st.st_size)
    if key not in _YAML_CACHE:
        if len(_YAML_CACHE) > 256:
            _YAML_CACHE.clear()
            _YAML_PLAIN.clear()
        obj = yaml.safe_load(p.read_text(encoding="utf-8"))
        _YAML_CACHE[key] = obj
        # a profile is read tens of thousands of times in a board's bootstrap (profile of a 25-minute resolution run,
        # 2026-11-20: 12.8k loads, 8 minutes inside copy.deepcopy). A tree of plain dicts / lists / scalars with no
        # shared container copies identically without the memo and the type dispatch (several times faster).
        _YAML_PLAIN[key] = not _shared_or_odd(obj, set())
    return _plain_copy(_YAML_CACHE[key]) if _YAML_PLAIN[key] else copy.deepcopy(_YAML_CACHE[key])


#: selects one of a profile's named ``compositions:`` (2026-11-14, rc1-night); unset = the profile's own
COMPOSITION_ENV = "CF_COMPOSITION"
#: keys a composition may replace whole (Main parts per section are replaced part by part)
#: ``judge_stamp_required`` (2026-11-15, lead): the 4.5 release switch, an unstamped judge verdict is stale
#: ``reuse`` (2026-11-15, lead): which stored rows of another profile may count; the shipping composition has none
_COMPOSITION_KEYS = ("cases", "core_level", "core_generated", "board_notes", "chains", "routing",
                     "judge_stamp_required", "reuse",
                     # 2026-11-19: Assistant basics (checked), a column outside Overall (assistant45.py)
                     "assistant",
                     # realignment 2026-11-19: the score tree and the NSFW+ numbers (segments45.py, nsfwplus45.py)
                     "segments45", "nsfw_plus", "rplong_score", "segment_marks", "marks_index",
                     # rc4 (release owner 2026-11-14): no scaling over pending parts
                     "pending_scaling",
                     # 2026-11-15 (lead): which of a chain section's ``judge_modes`` replaces its judge_mode
                     "judge_mode_select",
                     # lane-j (2026-11-19): judged parts count only when their second-reader marks pass
                     "judge_agreement",
                     # 2026-11-19 (b): a routed composition states the plain cut rule its scale applies
                     "cut_at_full_budget")


def _deep_merge(base, over):
    """``over`` laid onto ``base`` (new objects, neither input changes): mappings merge key by key,
    a YAML ``null`` in ``over`` deletes the key, anything else (lists included) replaces whole."""
    if not isinstance(base, dict) or not isinstance(over, dict):
        return copy.deepcopy(over)
    out = copy.deepcopy(base)
    for k, v in over.items():
        if v is None:
            out.pop(k, None)
        elif isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = _prune_nulls(v)
    return out


def _prune_nulls(v):
    """A mapping new to the profile keeps no ``null`` keys (they only mean delete, and there is nothing to delete)."""
    if isinstance(v, dict):
        return {k: _prune_nulls(x) for k, x in v.items() if x is not None}
    return copy.deepcopy(v)


def apply_composition(prof: dict, name: str) -> dict:
    """Apply the named composition (``$CF_COMPOSITION``) of a profile's ``compositions:`` block.

    A composition replaces ``cases`` / ``core_level`` / ``core_generated`` / ``board_notes`` whole, adds its
    ``pack_exclude`` to the profile's (and ``reuse``: the profile's ``default_composition`` always reuses
    nothing, a non-empty one there is refused), merges its ``case_repeats`` onto the profile's (per case id), deep-merges a
    ``main_nsfw`` block onto the profile's (null deletes, lists replace), and
    in ``scores.<section>.parts`` it removes the parts it lists under ``drop_parts`` and adds / replaces
    the parts it gives. Everything else (sampler rules, regime, judge, NSFW, packs) stays the profile's.
    The chosen name lands on the profile as ``composition`` (and from there on the run's meta); an
    unknown name is a ConfigError, never a silent fallback."""
    comps = prof.get("compositions") or {}
    aliases = prof.get("composition_aliases") or {}
    for old, new in aliases.items():
        if new not in comps:
            raise ConfigError(f"profile {name}: composition alias {old!r} -> {new!r}: no such composition")
    dflt = prof.get("default_composition")
    if dflt is not None and dflt not in comps:
        raise ConfigError(f"profile {name}: default_composition {dflt!r}: no such composition")
    # ship plan C8: the composition that ships is the profile's default (no env needed), and a renamed one
    # keeps its old name as an alias (tonight's run scripts and metas say rc3-main)
    want = os.environ.get(COMPOSITION_ENV, "").strip() or (dflt or "")
    want = aliases.get(want, want)
    if not want or want == "default" or not comps:
        # a profile without compositions (bench, 4.1) is always its own; only a profile that
        # declares compositions refuses a name it does not have
        prof["composition"] = "default"
        return prof
    if want not in comps:
        raise ConfigError(f"profile {name}: unknown composition {want!r} "
                          f"(known: default, {', '.join(sorted(comps)) or 'none'})")
    c = copy.deepcopy(comps[want])
    if dflt is not None and want == dflt:
        # 2026-11-15 (lead): no 4.1 row counts on a 4.5 board, in any part, pack or Main. The shipping
        # composition reuses no other profile's rows: a stored row of ``bench`` neither counts nor marks
        # its job done (done_jobs reads the board rows), so the 4.5 run makes every row itself.
        r = c.get("reuse")
        if r and (not isinstance(r, dict) or r.get("from") or r.get("ids")):
            raise ConfigError(f"profile {name}: composition {want!r} is the shipping one (default_composition) "
                              f"and declares reuse; a 4.5 board takes no 4.1 rows")
        c["reuse"] = {}
    out = dict(prof)
    for k in _COMPOSITION_KEYS:
        if k in c:
            out[k] = c[k]
    if c.get("pack_exclude"):
        # a composition that makes pack-file cases Main items takes them out of their packs (added to the
        # profile's own list, never replacing it), so a case is scored in exactly one place
        out["pack_exclude"] = list(dict.fromkeys(list(prof.get("pack_exclude") or []) + list(c["pack_exclude"])))
    if c.get("case_repeats"):
        # rc2-main (lead 2026-11-14): a composition's per-case repeats merge onto the profile's, so a count
        # raised for one composition never changes another's run
        if not isinstance(c["case_repeats"], dict):
            raise ConfigError(f"profile {name}: composition {want!r}: case_repeats must be a mapping")
        out["case_repeats"] = {**(prof.get("case_repeats") or {}), **c["case_repeats"]}
    scores = copy.deepcopy(prof.get("scores") or {})
    for sec, spec in (c.get("scores") or {}).items():
        parts = (scores.setdefault(sec, {}).setdefault("parts", {}))
        for pk in spec.get("drop_parts") or []:
            parts.pop(pk, None)
        for pk, part in (spec.get("parts") or {}).items():
            parts[pk] = part
    out["scores"] = scores
    _select_judge_modes(out, name, want)
    if c.get("main_nsfw") is not None:
        # a composition may reshape Main NSFW (a slice of rc2): deep-merged onto the profile's own block,
        # a null deletes a key (``parts: {b: null}``, ``chains: {nsfw/b: null}``), lists replace whole
        if not isinstance(c["main_nsfw"], dict):
            raise ConfigError(f"profile {name}: composition {want!r}: main_nsfw must be a mapping")
        out["main_nsfw"] = _deep_merge(prof.get("main_nsfw") or {}, c["main_nsfw"])
    out["composition"] = want
    return out


def _select_judge_modes(out: dict, name: str, want: str) -> None:
    """A chain section may carry alternative judge modes (``judge_modes: {ladder: {...}}``, lead 2026-11-15: the
    role-play sessions' ladder pair judge). The composition's ``judge_mode_select: {<section>: <mode name>}`` puts
    the named one in place of the section's ``judge_mode``; ``rubric`` / absent keeps today's. Either way the
    alternatives are removed, so a composition that selects nothing loads exactly as before the switch existed."""
    sel = out.pop("judge_mode_select", None) or {}
    if not isinstance(sel, dict):
        raise ConfigError(f"profile {name}: composition {want!r}: judge_mode_select must be a mapping")
    chains_ = out.get("chains")
    if not isinstance(chains_, dict):
        if sel:
            raise ConfigError(f"profile {name}: composition {want!r}: judge_mode_select names no chain section")
        return
    if not any(isinstance(v, dict) and "judge_modes" in v for v in chains_.values()) and not sel:
        return
    chains_ = dict(chains_)
    for sk in sel:
        if sk not in chains_:
            raise ConfigError(f"profile {name}: composition {want!r}: judge_mode_select: no chain section {sk!r}")
    for sk, sec in list(chains_.items()):
        if not isinstance(sec, dict) or ("judge_modes" not in sec and sk not in sel):
            continue
        alts = sec.get("judge_modes") or {}
        pick = sel.get(sk)
        sec = {k: v for k, v in sec.items() if k != "judge_modes"}
        if pick not in (None, "rubric", "default"):
            if pick not in alts:
                raise ConfigError(f"profile {name}: composition {want!r}: {sk} has no judge mode {pick!r} "
                                  f"(known: rubric, {', '.join(sorted(alts)) or 'none'})")
            sec["judge_mode"] = copy.deepcopy(alts[pick])
            if (alts[pick] or {}).get("kind") == "ladder":
                _ladder_part_note(out, sk)
        chains_[sk] = sec
    out["chains"] = chains_


def _ladder_part_note(out: dict, sk: str) -> None:
    """The board's words for a ladder-judged part (SPEC): its part note says what the score is."""
    from .chains import LADDER_BOARD_TEXT
    sec_key, _, part_key = sk.partition("/")
    scores = out.get("scores")
    part = (((scores or {}).get(sec_key) or {}).get("parts") or {}).get(part_key) if isinstance(scores, dict) else None
    if not isinstance(part, dict):
        return
    scores = dict(scores)
    sec = dict(scores[sec_key])
    sec["parts"] = dict(sec["parts"])
    note = LADDER_BOARD_TEXT.rstrip(".") + " (0-4; judged half = position / 4, the rest code-checked)"
    sec["parts"][part_key] = {**part, "note": note + ("; " + part["note"] if part.get("note") else "")}
    scores[sec_key] = sec
    out["scores"] = scores


def load_profile(name: str, cfg: dict | None = None) -> dict:
    for d in _dirs(cfg):
        p = d / f"{name}.yaml"
        if p.exists():
            prof = _yaml_file(p) or {}
            prof.setdefault("name", name)
            over_p = p.with_name(f"{name}.private.yaml")
            if _config.PRIVATE_ENABLED and over_p.exists():
                over = _yaml_file(over_p) or {}
                prof = _merge_overlay(prof, over, name)
                prof["_overlay"] = str(over_p)
            prof["_path"] = str(p)
            prof = apply_composition(prof, name)
            if not isinstance(prof.get("cases"), dict) or not prof["cases"]:
                raise ConfigError(f"profile {name}: 'cases' must map category -> [ids]")
            # core_generated: the instances become Core case ids (and part members)
            for c in core_generated_cases(prof) if prof.get("core_generated") else []:
                ids = prof["cases"].setdefault(c["category"], [])
                if c["id"] not in ids:
                    ids.append(c["id"])
                spec = _part_spec(prof, c.get("part"))
                if spec is not None and isinstance(spec.get("cases"), list) and c["id"] not in spec["cases"]:
                    spec["cases"].append(c["id"])      # a part that lists its cases gets the instances
            if prof.get("chains"):
                from .chains import sections as _chain_sections
                _chain_sections(prof)          # structure / families / levels (no materialising)
            return apply_main_nsfw(prof)
    raise ConfigError(f"unknown profile {name!r} (known: {list_profiles(cfg)})")


#: board-time rescoring switches of a profile (4.5). Absent = off, so the 4.1
#: ``bench`` profile keeps its stored verdicts' scores byte-for-byte:
#: ``ooc_gate: votes`` (rpdeep: a lone judge out-of-character flag only zeroes
#: passed facts when the judge-side votes/quote back it, rpdeep.ooc_flag_counts)
#: and ``quote_recheck: true`` (report._recheck_quotes).
SCORING_SWITCHES = ("ooc_gate", "quote_recheck", "scoring_rules")
#: ``scoring_rules: v2`` (harness v2, merged r2): the nsfw-v2 board-time scoring --
#: the Uncensored row score uses the judge elements AND the card checks
#: (report.row_quality), an unfactual no_puppeting check is a penalty item, a
#: stored element credit (0/1/2, negative elements) is scored (rpdeep.score_row).
#: Absent on ``bench`` (4.1): its board must not move; a row judged under the v2
#: rules (``judge.scoring_rules_version``) always scores by them.

_SWITCH_CACHE: dict = {}


_ACTIVE: list = [None]


def set_active(name: str | None) -> None:
    """Name the profile this process builds boards / climbs ladders for (CLI entry,
    ``report --profile``). None = the default profile."""
    _ACTIVE[0] = name or None


def active_name(cfg: dict | None = None) -> str:
    """THE active profile of a process (fix-d, Fable gate WP-4 item 4): ``cfg["_profile"]``
    (a phase's applied profile), else the name set at the command entry, else the
    default. Every board, ladder and unlock path asks this, never DEFAULT_PROFILE, so
    under bench45 the report reads bench45's tree and rows."""
    return (cfg or {}).get("_profile") or _ACTIVE[0] or DEFAULT_PROFILE


def switch_name(row: dict) -> str:
    """The profile whose switches (billing, quote recheck, ooc gate) apply to a stored
    ``row`` at board time: its own profile, except a row REUSED from another profile
    (the active profile's ``reuse.from``), which is scored by the active profile."""
    rp, act = row.get("profile"), active_name()
    if rp and rp != act:
        try:
            if rp in ((load_profile(act).get("reuse") or {}).get("from") or []):
                return act
        except Exception:  # noqa: BLE001
            pass
    return rp or act


def row_switch(row: dict, key: str, default=None):
    """``profile_switch`` for a stored row, with the reuse rule of :func:`switch_name`."""
    return profile_switch(switch_name(row), key, default)


def profile_switch(name: str | None, key: str, default=None):
    """The value of a top-level profile key for the profile a ROW was run
    under (``row["profile"]``), cached per (profile dirs, name, key). An
    unknown or unreadable profile, or no name, gives ``default`` (off)."""
    if not name:
        return default
    ck = (tuple(str(d) for d in _dirs(None)), str(name), key, bool(_config.PRIVATE_ENABLED))
    if ck not in _SWITCH_CACHE:
        try:
            v = load_profile(str(name)).get(key, default)
        except Exception:  # noqa: BLE001 - an unreadable profile turns nothing on
            v = default
        if len(_SWITCH_CACHE) > 256:
            _SWITCH_CACHE.clear()
        _SWITCH_CACHE[ck] = v
    return _SWITCH_CACHE[ck]


#: PUBOUT 4.5: a checkout whose git-ignored private files the guard should read too. A worktree (the
#: integration tree, a release worktree) has no cases/private of its own: those files live only in the
#: checkout they were made in, so its guard loaded 0 terms. Nothing is copied: the files are read in place.
PRIVATE_FROM_ENV = "CRUCIBLEFORGE_PRIVATE_FROM"


def _external_private(root: Path) -> tuple[set[str], set[str], set[str], set[str]]:
    """(categories, case ids, labels, ladder paths) of the private files of another checkout ``root``:
    cases/private/<cat>.json (a category private by location, its case ids), its private ladder sets,
    and the ``private:`` / ``labels:`` of its profile overlays (profiles/*.private.yaml,
    profiles/private/*.yaml). Read only; unreadable files are skipped."""
    import json as _json
    cats: set[str] = set()
    ids: set[str] = set()
    labels: set[str] = set()
    paths: set[str] = set()
    pdir = root / "cases" / "private"
    base = set(_config._BASE_CATEGORIES)
    for f in sorted(pdir.glob("*.json")) if pdir.is_dir() else []:
        if f.stem not in base:
            cats.add(f.stem)
        try:
            data = _json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for c in data if isinstance(data, list) else (data.get("cases") or []) if isinstance(data, dict) else []:
            if isinstance(c, dict) and c.get("id"):
                ids.add(str(c["id"]))
    try:
        from .ladder import _read_set, _set_files
        lroot = pdir / "ladder"
        for _key, _n, f in _set_files([lroot]):
            paths.add(str(f.relative_to(root)))
            try:
                spec = _read_set(f)
            except ConfigError:
                continue
            for c in spec.get("cases") or []:
                if isinstance(c, dict) and c.get("id"):
                    ids.add(str(c["id"]))
    except Exception:  # noqa: BLE001 - no readable private ladder there
        pass
    overlays = sorted((root / "profiles").glob("*.private.yaml")) + sorted((root / "profiles" / "private").glob("*.yaml"))
    for f in overlays:
        try:
            over = _yaml_file(f) or {}
        except Exception:  # noqa: BLE001
            continue
        oc = set(over.get("private") or [])
        cats |= oc
        for k, v in (over.get("labels") or {}).items():
            if k in cats | oc and v:
                labels.add(str(v))
        for k, v in (over.get("cases") or {}).items():
            if k in cats | oc and isinstance(v, list):
                ids.update(str(x) for x in v)
    return cats, ids, labels, paths


def public_private_scope(cfg: dict | None = None) -> dict:
    """PUBOUT 4.5: the scope a PUBLIC render checks against = the UNION of :func:`private_scope`
    over every profile (the default, the active one, every other one this tree knows), so moving
    DEFAULT_PROFILE to the 4.5 profile cannot drop a private name only the 4.1 overlay declares;
    plus, with ``$CRUCIBLEFORGE_PRIVATE_FROM`` = another checkout, that checkout's private files
    (read in place, never copied). Hiding more is the safe direction: a public render refuses on
    any hit. Only report._hidden() uses it; the judge and planner keep :func:`private_scope`."""
    names = [DEFAULT_PROFILE, active_name(cfg)]
    try:
        names += [n for n in list_profiles(cfg) if n not in names]
    except Exception:  # noqa: BLE001
        pass
    out = {"categories": set(), "case_ids": set(), "labels": set(), "paths": set()}
    for n in dict.fromkeys(names):
        try:
            sc = private_scope(cfg, n)
        except Exception:  # noqa: BLE001 - an unreadable profile adds nothing; the others still count
            continue
        for k in ("categories", "case_ids", "labels", "paths"):
            out[k] |= set(sc.get(k) or ())
    ext = os.environ.get(PRIVATE_FROM_ENV)
    if ext:  # an explicit checkout is read even under CRUCIBLEFORGE_NO_PRIVATE (hiding more is safe)
        root = Path(ext).expanduser()
        if not (root / "cases").is_dir():
            raise ConfigError(f"${PRIVATE_FROM_ENV} is not a CrucibleForge checkout (no cases/ dir)")
        c, i, l, p = _external_private(root)
        out["categories"] |= c
        out["case_ids"] |= i
        out["labels"] |= l
        out["paths"] |= p
    allt = out["categories"] | out["case_ids"] | out["labels"] | out["paths"]
    return {"categories": out["categories"], "case_ids": out["case_ids"], "labels": out["labels"],
            "paths": out["paths"], "terms": sorted(allt, key=len, reverse=True)}


def private_scope(cfg: dict | None = None, name: str | None = None) -> dict:
    """What a PUBLIC artifact must not contain — the single source of truth.

    ``categories``: the profile's (overlay-merged) ``private:`` list plus every
    category whose case file lives in cases/private/ (private by location, so
    forgetting the list entry cannot publish it). ``case_ids``: every case id
    of those categories. ``labels``: their display names (the profile's
    ``labels:``). ``terms`` = all of the above, for the leak guard."""
    try:
        # the default is read at call time (the release flip moves DEFAULT_PROFILE)
        prof = load_profile(name or DEFAULT_PROFILE, cfg)
    except ConfigError:
        prof = {}
    cats = set(prof.get("private") or []) | set(_config.PRIVATE_CATEGORIES)
    ids: set[str] = set()
    for cat in cats:
        ids.update((prof.get("cases") or {}).get(cat) or [])
        try:
            ids.update(c["id"] for c in load_cases([cat]))
        except ConfigError:
            pass    # listed private but no file here: its NAME is still hidden
    labels = {v for k, v in (prof.get("labels") or {}).items() if k in cats and v}
    # private ladder packs (cases/private/ladder): their question ids are
    # private whatever category they name (review S4); the set paths are
    # terms too
    paths: set[str] = set()
    try:
        from .ladder import private_pack_terms
        pack_ids, paths = private_pack_terms()
        ids |= pack_ids
    except Exception:  # noqa: BLE001 — no private ladder readable: nothing to add
        pass
    return {"categories": cats, "case_ids": ids, "labels": labels, "paths": paths,
            "terms": sorted(cats | ids | labels | paths, key=len, reverse=True)}


def apply_profile(prof: dict, cfg: dict, smoke: bool = False) -> tuple[dict, list[dict]]:
    """Return (cfg with profile budgets applied, selected cases in profile order)."""
    activate_cases_root(prof)
    cfg2 = copy.deepcopy(cfg)
    d = cfg2.setdefault("defaults", {})
    for k in ("thinking_max_tokens_factor", "thinking_max_tokens_cap", "min_tok_per_s",
              # the "as deployed" regime (suite 3.8.0, runner.REGIME_DEPLOYED)
              "regime", "thinking_max_tokens_floor", "reasoning_overflow_recovery",
              "recommended_samplers", "escalation_rungs",
              # 4.5: card/API overrides of the reasoning-replay rule (replay_rule)
              "reasoning_replay_rules",
              # 2026-11-14: observed chat-template defects per family (a record, not a setting)
              "template_traps",
              # 4.5: a reply with no visible content is asked once more with thinking off
              "thinking_fallback",
              # 4.5: finish=stop with no visible content -> row flag, blank, empty history turn
              "empty_stop_rule",
              # 4.5: regenerate | thinking_off | none for a finished reply with no visible content
              "on_empty_stop",
              # code bank: one generous output budget for every row (runner.bank_budget)
              "bank_budget",
              # code bank: tasks that still run but are not scored (anchors)
              "bank_anchors",
              # 3.9: the context every model is loaded at, at least (runner)
              "context_floor",
              # 4.5: registry thinkers load at least this wide (D14); rows cut by
              # the context window are retried then errored (runner.row_ctx_short);
              # judged cases are generated first (runner.judged_first)
              "thinker_context", "ctx_short_retry", "judged_first",
              # 4.5.1 (PLAN-451-LIMITS): reasoning + answer budgets, case budgets, chain ceilings (budgets451.py)
              "budgets_451",
              # M1 (R3): stop a model whose first answers are blank or
              # cut off (earlystop.py; thresholds live in the profile)
              "early_stop",
              # 3.9: how `all` runs a model (runner.run_plan: round split,
              # one budget, escalation wall cap)
              "run_plan"):
        if k in prof:
            d[k] = prof[k]
    # 4.5 only: speaker tags may follow a leading quote / bullet (session_checks.TAG_LEAD);
    # every profile without the key (4.1) grades byte-identically
    from . import session_checks as _sc
    _sc.set_tag_lead(bool(prof.get("speaker_tag_lead")))
    if prof.get("repeats"):
        d["repeats"] = {**d.get("repeats", {}), **prof["repeats"]}
    if "no_recovery" in prof:
        d["no_recovery"] = list(prof.get("no_recovery") or [])
    selected = _select(prof, prof["cases"], smoke)
    if prof.get("chains") and not smoke:
        # 4.5 chains (chains.py): one placeholder per chain; every rung must grade in-run
        # with no judge (fail closed here, before anything runs)
        from . import chains as _chains
        _chains.check_gradeable(prof)
        selected += _chains.chain_cases(prof)
        from . import routing as _routing
        _routing.spec(prof)        # a routing block is validated at load (fail closed)
        _routing.check_calibration(prof, ROOT_DIR)   # every cell the routed scale needs is calibrated
        from . import scale as _scale
        _scale.ran_out_rules(prof)  # one rule per cell: two sets disagreeing are refused here
    cfg2["_profile"] = prof["name"]
    # the run-start case set: earlystop.targeted_reason tells a whole run
    # (judged) from a targeted rerun of a subset (not judged)
    cfg2["_profile_case_ids"] = [c["id"] for c in selected if not c.get("ladder_staged")]
    return cfg2, selected


def _select(prof: dict, mapping: dict, smoke: bool = False) -> list[dict]:
    """The cases of ``{category: [ids]}`` in profile order, each a copy with
    the profile's per-category ``max_tokens`` and per-case ``case_repeats``
    (``repeats`` on the case, read by config.repeats_for) applied."""
    all_cases = {c["id"]: c for c in load_cases(list(mapping.keys()))}
    from . import gen as _gen
    from .lazycase import LazyCase
    for c in core_generated_cases(prof):        # Core instances live in the profile, not in a file
        if c["id"] not in all_cases and c["category"] in mapping:
            # rendered when first read (lazycase.py): a profile load renders no Core instance a command does not use
            all_cases[c["id"]] = LazyCase(c, _gen.materialize_case)
    per_case = prof.get("case_repeats") or {}
    selected: list[dict] = []
    for cat, ids in mapping.items():
        for cid in ids or []:
            c = all_cases.get(cid)
            if c is None:
                raise ConfigError(f"profile {prof['name']}: unknown case id {cid!r} in {cat}")
            if c["category"] != cat:
                raise ConfigError(f"profile {prof['name']}: {cid} is not in category {cat}")
            if smoke and not c.get("smoke"):
                continue
            c = c.lazy_copy() if isinstance(c, LazyCase) else dict(c)
            mt = (prof.get("max_tokens") or {}).get(cat)
            if mt:
                c["max_tokens"] = int(mt)
            if cid in per_case:
                c["repeats"] = int(per_case[cid])
            selected.append(c)
    return selected


def tier_cases(prof: dict, tier: int, scores=None, smoke: bool = False) -> list[dict]:
    """Tier 2/3 cases (``tiers:`` block, crucibleforge/tiers.py) of the given
    scores (default: all), each stamped ``tier`` and ``tier_score``, run
    ``tiers.repeats.t<n>`` times, and — when the tier names a ``context`` —
    needing that context (``min_context``: a model that cannot be served at it
    skips the case, "n/a (ctx)")."""
    from . import tiers as _tiers
    lists = _tiers.tier_lists(prof, tier)
    ctx = _tiers.tier_context(prof, tier)
    out = []
    for score, mapping in lists.items():
        if scores is not None and score not in scores:
            continue
        for c in _select(prof, mapping, smoke):
            c.update(tier=tier, tier_score=score, repeats=_tiers.tier_repeats(prof, tier))
            if ctx:
                c["min_context"] = max(int(c.get("min_context") or 0), ctx)
            out.append(c)
    return out


def all_tier_cases(prof: dict, smoke: bool = False) -> list[dict]:
    """Tier 1 + every tier-2/3 case (escalation, the board's case ids)."""
    from . import tiers as _tiers
    return _select(prof, prof["cases"], smoke) + [
        c for t in _tiers.TIERS for c in tier_cases(prof, t, smoke=smoke)]


def profile_judge(prof: dict) -> dict | str | None:
    """The profile's default judge spec (dict with provider/model_id/...), or None."""
    j = prof.get("judge")
    if not j:
        return None
    if isinstance(j, str):
        return j
    if not j.get("provider") or not j.get("model_id"):
        raise ConfigError(f"profile {prof['name']}: judge needs provider + model_id")
    return dict(j)


def default_judge_id(cfg: dict | None = None) -> str | None:
    """model_id of the active profile's judge (:func:`active_name`): the only judge
    whose verdicts count on that board (a 4.1 render names the 4.1 judge, 4.5 its own since amendment r)."""
    try:
        j = profile_judge(load_profile(active_name(cfg), cfg))
    except ConfigError:
        return None
    if isinstance(j, dict):
        return j.get("model_id")
    return j


# ------------------------------------------------------------ 4.5 hard sets, budget

#: keys of a profile's ``budget:`` block (PLAN-45-v3 9.3; crucibleforge budget, WP-07)
BUDGET_NUMBERS = ("anchor_tps_stream", "anchor_slots", "occupancy", "anchor_load_min",
                  "anchor_limit_min", "board_avg_limit_min", "judge_rate_calls_per_min")
#: 4.5 (E7): the home budget block, ``budget.home`` (budget.HOME_DEFAULTS)
BUDGET_HOME_KEYS = ("tps", "slots", "judge_s_per_call", "load_min", "limit_min")


def hard_declared(prof: dict) -> dict[str, dict]:
    """{score key: {"max", "file" (absolute Path), "hard_score", "label", "short"}}
    of the scores that declare ``hard: {max, file}`` (4.5). ``file`` is relative
    to the repo root; its parent directory names the hard score (code|chat|nsfw)."""
    out = {}
    for key, spec in (prof.get("scores") or {}).items():
        h = (spec or {}).get("hard")
        if not isinstance(h, dict):
            continue
        f = Path(str(h.get("file", "")))
        out[key] = {"max": float(h.get("max", 2)), "file": ROOT_DIR / f,
                    "hard_score": f.parent.name, "label": h.get("label", "Hard"),
                    "short": h.get("short", "Hard")}
    return out


def hard_cases(prof: dict, cases_dir: Path | None = None) -> list[dict]:
    """The hard-set case objects of the scores the profile declares (each
    carries ``hard_score``, ``hard_rev`` and its own ``category``)."""
    from . import hard
    out: list[dict] = []
    for d in hard_declared(prof).values():
        out += [c for c in hard.load(cases_dir or _config.CASES_DIR, d["hard_score"])
                if c["hard_rev"] == d["file"].stem]
    return out


#: score key -> the hard-set directory it must read (cases/hard/<dir>/)
HARD_SCORE_OF = {"work": "code", "chat": "chat", "nsfw": "nsfw"}

#: hard-case kind -> the scorecard component its rows feed
HARD_COMPONENT = {"coding": "coding", "codebank": "codebank", "codeagent": "codeagent",
                  "tooluse": "tooluse", "rpdeep": "rpdeep", "rp": "rp", "story": "story",
                  "steer": "steer", "nsfw": "nsfw_gated"}


_HARD_CACHE: dict = {}


def _check_hard_file(score_key: str, d: dict) -> None:
    """Raise ConfigError when the hard file a score declares (as loaded:
    cases/hard/<score>/<rev>.json under the cases dir) is missing, unreadable,
    not JSON, or has no ``cases`` list."""
    from . import hard
    path = hard.hard_dir(_config.CASES_DIR) / d["hard_score"] / d["file"].name
    try:
        spec = hard.read_file(path)
    except (OSError, ValueError) as e:
        raise ConfigError(f"scores.{score_key}.hard.file: cannot read {path.name} "
                          f"({type(e).__name__}: {e})") from e
    if not isinstance(spec, dict) or not isinstance(spec.get("cases"), list):
        raise ConfigError(f"scores.{score_key}.hard.file: {path.name} has no 'cases' list")


def hard_part(prof: dict, score_key: str) -> dict | None:
    """The synthetic ``hard`` part of one score (tiers part format), or None
    when the score declares no hard set or its file holds no cases (then the
    score is just its standard parts and says so in ``score_max``). Weight =
    ``hard.max``; scored from the hard cases' rows only (``cases``), each kind
    by its component, weighted by its number of cases."""
    d = hard_declared(prof).get(score_key)
    if not d:
        return None
    try:
        mt = d["file"].stat().st_mtime if d["file"].exists() else None
    except OSError:
        mt = None
    ck = (score_key, str(d["file"]), mt, id(hard_cases), str(_config.CASES_DIR))
    cases = _HARD_CACHE.get(ck)
    if cases is None:
        # review F12: a DECLARED hard file that is missing or unreadable raises;
        # only a valid file with ``cases: []`` takes the standard-only (/8) path
        # (a silent [] here turned a /10 score into /8 without a word)
        _check_hard_file(score_key, d)
        cases = [c for c in hard_cases(prof) if c.get("hard_score") == d["hard_score"]]
        _HARD_CACHE.clear() if len(_HARD_CACHE) > 64 else None
        _HARD_CACHE[ck] = cases
    if not cases:
        return None
    scoring: dict[str, float] = {}
    for c in cases:
        comp = HARD_COMPONENT.get(c.get("category"))
        if comp:
            scoring[comp] = scoring.get(comp, 0.0) + 1.0
    return {"key": "hard", "label": d["label"], "short": d["short"], "weight": d["max"],
            "cases": [c["id"] for c in cases], "auto": False, "scoring": scoring, "hard": True}


def hard_case_ids(prof: dict | None) -> set[str]:
    if not prof:
        return set()
    out: set[str] = set()
    for k in hard_declared(prof):
        p = hard_part(prof, k)
        out |= set((p or {}).get("cases") or ())
    return out


def profile_problems(prof: dict, cases_dir: Path | None = None) -> list[str]:
    """Static problems of a profile's 4.5 additions (for `cases verify`):
    hard declarations, hard files, id disjointness, the budget block."""
    from . import hard
    cases_dir = Path(cases_dir) if cases_dir else _config.CASES_DIR
    errs: list[str] = []
    standard = {i: f"profile {cat}" for cat, ids in (prof.get("cases") or {}).items()
                for i in ids or []}
    decl = hard_declared(prof)
    used: dict[str, str] = {}
    for key, d in decl.items():
        # review F18: each score reads its own hard set (work -> code, chat ->
        # chat, nsfw -> nsfw), and one file never serves two scores
        want = HARD_SCORE_OF.get(key)
        if want and d["hard_score"] in hard.HARD_SCORES and d["hard_score"] != want:
            errs.append(f"scores.{key}.hard.file: {key} reads cases/hard/{want}/, "
                        f"not cases/hard/{d['hard_score']}/")
        fk = str(d["file"].resolve())
        if fk in used:
            errs.append(f"scores.{key}.hard.file: {d['file'].name} under "
                        f"cases/hard/{d['hard_score']}/ is already the hard set of "
                        f"scores.{used[fk]}")
        else:
            used[fk] = key
        if d["hard_score"] not in hard.HARD_SCORES:
            errs.append(f"scores.{key}.hard.file: {d['file']} is not under cases/hard/"
                        f"<{'|'.join(hard.HARD_SCORES)}>/")
        if d["max"] <= 0:
            errs.append(f"scores.{key}.hard.max must be > 0")
        parts = (prof.get("scores") or {})[key].get("parts") or {}
        total = d["max"] + sum(float(p.get("max", p.get("weight", 1.0))) for p in parts.values())
        if abs(total - 10) > 1e-9:
            errs.append(f"scores.{key}: parts + hard add up to {total}, not 10")
    errs += hard.problems(cases_dir, standard_ids=standard,
                          declared={k: d["file"] for k, d in decl.items()})
    for key, d in decl.items():
        for c in hard.load(cases_dir, d["hard_score"]):
            if c["hard_rev"] == d["file"].stem and c["category"] in (prof.get("cases") or {}) \
                    and c["id"] in (prof["cases"][c["category"]] or []):
                errs.append(f"{c['id']}: listed as standard AND hard")
    b = prof.get("budget")
    if b is not None:
        if not isinstance(b, dict):
            errs.append("budget: must be a mapping")
        else:
            # the 120B-anchor keys are retired (D1) but tolerated; present ones must be sane
            if "anchor" in b and not (isinstance(b["anchor"], str) and b["anchor"]):
                errs.append("budget.anchor must be a model label")
            for k in BUDGET_NUMBERS:
                if k not in b:
                    continue
                v = b.get(k)
                if isinstance(v, bool) or not isinstance(v, (int, float)) or v <= 0:
                    errs.append(f"budget.{k} must be a positive number")
            home = b.get("home")
            if home is not None:
                if not isinstance(home, dict):
                    errs.append("budget.home must be a mapping")
                else:
                    for k, v in home.items():
                        if k not in BUDGET_HOME_KEYS:
                            errs.append(f"budget.home.{k}: unknown key (known: {list(BUDGET_HOME_KEYS)})")
                        elif isinstance(v, bool) or not isinstance(v, (int, float)) or v <= 0:
                            errs.append(f"budget.home.{k} must be a positive number")
            if not isinstance(b.get("reference_models", []), list):
                errs.append("budget.reference_models must be a list")
    ec = (prof.get("judge_policy") or {}).get("element_class")
    if ec is not None and not (isinstance(ec, dict) and all(
            isinstance(v, int) and not isinstance(v, bool) and v >= 1 for v in ec.values())):
        errs.append("judge_policy.element_class must map class -> samples (int >= 1)")
    return errs
