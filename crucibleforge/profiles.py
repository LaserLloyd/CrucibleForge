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
"""
from __future__ import annotations

import copy
from pathlib import Path

import yaml

from .config import ConfigError, ROOT_DIR, load_cases

PROFILES_DIR = ROOT_DIR / "profiles"
#: The single benchmark. Used whenever --profile is not given.
DEFAULT_PROFILE = "bench"


def list_profiles(cfg: dict | None = None) -> list[str]:
    names: set[str] = set()
    for d in _dirs(cfg):
        if d.is_dir():
            names.update(p.stem for p in d.glob("*.yaml"))
    return sorted(names)


def _dirs(cfg: dict | None) -> list[Path]:
    dirs = []
    if cfg and cfg.get("_path"):
        dirs.append(Path(cfg["_path"]).parent / "profiles")
    dirs.append(PROFILES_DIR)
    return dirs


def load_profile(name: str, cfg: dict | None = None) -> dict:
    for d in _dirs(cfg):
        p = d / f"{name}.yaml"
        if p.exists():
            prof = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
            prof.setdefault("name", name)
            prof["_path"] = str(p)
            if not isinstance(prof.get("cases"), dict) or not prof["cases"]:
                raise ConfigError(f"profile {name}: 'cases' must map category -> [ids]")
            return prof
    raise ConfigError(f"unknown profile {name!r} (known: {list_profiles(cfg)})")


def apply_profile(prof: dict, cfg: dict, smoke: bool = False) -> tuple[dict, list[dict]]:
    """Return (cfg with profile budgets applied, selected cases in profile order)."""
    cfg2 = copy.deepcopy(cfg)
    d = cfg2.setdefault("defaults", {})
    for k in ("thinking_max_tokens_factor", "thinking_max_tokens_cap", "min_tok_per_s"):
        if k in prof:
            d[k] = prof[k]
    if prof.get("repeats"):
        d["repeats"] = {**d.get("repeats", {}), **prof["repeats"]}
    if "no_recovery" in prof:
        d["no_recovery"] = list(prof.get("no_recovery") or [])
    cats = list(prof["cases"].keys())
    all_cases = {c["id"]: c for c in load_cases(cats)}
    selected: list[dict] = []
    for cat, ids in prof["cases"].items():
        for cid in ids:
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
            selected.append(c)
    cfg2["_profile"] = prof["name"]
    return cfg2, selected


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
