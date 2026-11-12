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
from pathlib import Path

import yaml

from . import config as _config
from .config import ConfigError, ROOT_DIR, load_cases

PROFILES_DIR = ROOT_DIR / "profiles"
#: The single benchmark. Used whenever --profile is not given.
DEFAULT_PROFILE = "bench"


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


# Parsed profile YAML, per (path, mtime_ns, size): the board re-loads the
# benchmark profile several hundred times per report and the pure-Python YAML
# parser was a third of its time (report profile 2026-11-12). A changed file
# has a new stat key and is parsed again; every caller gets its own deep copy.
_YAML_CACHE: dict[tuple[str, int, int], object] = {}


def _yaml_file(p: Path):
    st = p.stat()
    key = (str(p), st.st_mtime_ns, st.st_size)
    if key not in _YAML_CACHE:
        if len(_YAML_CACHE) > 256:
            _YAML_CACHE.clear()
        _YAML_CACHE[key] = yaml.safe_load(p.read_text(encoding="utf-8"))
    return copy.deepcopy(_YAML_CACHE[key])


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
            if not isinstance(prof.get("cases"), dict) or not prof["cases"]:
                raise ConfigError(f"profile {name}: 'cases' must map category -> [ids]")
            return prof
    raise ConfigError(f"unknown profile {name!r} (known: {list_profiles(cfg)})")


def private_scope(cfg: dict | None = None, name: str = DEFAULT_PROFILE) -> dict:
    """What a PUBLIC artifact must not contain — the single source of truth.

    ``categories``: the profile's (overlay-merged) ``private:`` list plus every
    category whose case file lives in cases/private/ (private by location, so
    forgetting the list entry cannot publish it). ``case_ids``: every case id
    of those categories. ``labels``: their display names (the profile's
    ``labels:``). ``terms`` = all of the above, for the leak guard."""
    try:
        prof = load_profile(name, cfg)
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
    return {"categories": cats, "case_ids": ids, "labels": labels,
            "terms": sorted(cats | ids | labels | paths, key=len, reverse=True)}


def apply_profile(prof: dict, cfg: dict, smoke: bool = False) -> tuple[dict, list[dict]]:
    """Return (cfg with profile budgets applied, selected cases in profile order)."""
    cfg2 = copy.deepcopy(cfg)
    d = cfg2.setdefault("defaults", {})
    for k in ("thinking_max_tokens_factor", "thinking_max_tokens_cap", "min_tok_per_s",
              # the "as deployed" regime (suite 3.8.0, runner.REGIME_DEPLOYED)
              "regime", "thinking_max_tokens_floor", "reasoning_overflow_recovery",
              "recommended_samplers", "escalation_rungs",
              # code bank: one generous output budget for every row (runner.bank_budget)
              "bank_budget",
              # code bank: tasks that still run but are not scored (anchors)
              "bank_anchors",
              # 3.9: the context every model is loaded at, at least (runner)
              "context_floor",
              # 3.9: how `all` runs a model (runner.run_plan: round split,
              # one budget, escalation wall cap)
              "run_plan"):
        if k in prof:
            d[k] = prof[k]
    if prof.get("repeats"):
        d["repeats"] = {**d.get("repeats", {}), **prof["repeats"]}
    if "no_recovery" in prof:
        d["no_recovery"] = list(prof.get("no_recovery") or [])
    selected = _select(prof, prof["cases"], smoke)
    cfg2["_profile"] = prof["name"]
    return cfg2, selected


def _select(prof: dict, mapping: dict, smoke: bool = False) -> list[dict]:
    """The cases of ``{category: [ids]}`` in profile order, each a copy with
    the profile's per-category ``max_tokens`` and per-case ``case_repeats``
    (``repeats`` on the case, read by config.repeats_for) applied."""
    all_cases = {c["id"]: c for c in load_cases(list(mapping.keys()))}
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
            c = dict(c)
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
    """model_id of the benchmark's judge (the 122B) — the only judge whose
    verdicts count on the board."""
    try:
        j = profile_judge(load_profile(DEFAULT_PROFILE, cfg))
    except ConfigError:
        return None
    if isinstance(j, dict):
        return j.get("model_id")
    return j
