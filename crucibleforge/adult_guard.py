"""The adult page's per-model switch (lead 2026-11-15): ``adult_page: true | false`` on a models.yaml model entry.

Standing rule: adult-page cases never go to a model reached through the Claude Code route (provider ``type:
claude_code``, the owner's subscription CLI); those rows are validation only. A bare ``crucibleforge all --profile
bench45 --models <M>`` plans every adult unit for any model and ``--categories`` does not filter chain units, so the
rule is enforced here, not left to the launcher (a launcher slip sent 17 adult prompts to Claude Code on 2026-11-14).

* Default: ``False`` for a model whose provider has ``type: claude_code``, ``True`` for every other model.
  An explicit ``adult_page: true`` on a Claude Code model is refused (ConfigError), never honoured.
* Plan (:func:`plan_cases`, called by ``runner.run_models`` for every model before its rounds are planned):
  a model with the page off loses every adult unit (the adult chains and any adult pack) and the run prints
  one line, ``adult page: not run for <label> (adult_page off for this model)``.
* Explicit ids (:func:`refuse_explicit`, called by ``cli.cmd_run`` right after ``--cases`` is applied, before any
  call): naming an adult unit for such a model is refused.
* Send path (:func:`assert_sendable`, the first thing ``runner._run_case_repeat`` does; every generation of every
  path, i.e. a fresh run, a resume, a chain's next attempt, recover, escalate, a routed stage, goes through it):
  an adult unit that reaches such a model raises :class:`AdultPageRefused` before anything is sent.
* Board (:func:`board_rows`, called by ``report.stats_from_tier_rows``): such a model's adult rows (the 17 stray
  validation rows of 2026-11-14 included) never score, so its adult page reads "not run" and is never ranked.

An adult unit = a case or row of category ``nsfw`` (the 4.5 adult chains and the 4.x NSFW / Uncensored packs all
carry it) or a chain whose section part is under ``nsfw/``.
"""
from __future__ import annotations

from .config import ConfigError

CLAUDE_CODE = "claude_code"
PLAN_LINE = "adult page: not run for {label} (adult_page off for this model)"


class AdultPageRefused(RuntimeError):
    """An adult-page unit reached a model whose adult page is off (never sent)."""


def _provider_type(cfg: dict | None, entry: dict) -> str | None:
    p = ((cfg or {}).get("providers") or {}).get(entry.get("provider"))
    if isinstance(p, dict):
        return p.get("type", "openai")
    return None


def is_claude_code(cfg: dict | None, entry: dict) -> bool:
    """The model is reached through the Claude Code route: its provider's ``type`` is ``claude_code``."""
    return _provider_type(cfg, entry) == CLAUDE_CODE or entry.get("provider") == CLAUDE_CODE


def validate_entry(cfg: dict | None, entry: dict) -> None:
    v = entry.get("adult_page", None)
    if v is not None and not isinstance(v, bool):
        raise ConfigError(f"model {entry.get('name')}: adult_page is true or false, not {v!r}")
    if v is True and is_claude_code(cfg, entry):
        raise ConfigError(
            f"model {entry.get('name')}: adult_page: true is refused on the Claude Code route (provider type "
            f"claude_code). Adult-page cases never go to Claude Code rows; those rows are validation only. "
            f"Remove the key (the default there is off).")


def adult_page_on(cfg: dict | None, entry: dict) -> bool:
    """Whether the adult page runs for this model (validates the key first)."""
    validate_entry(cfg, entry)
    if is_claude_code(cfg, entry):
        return False
    return entry.get("adult_page", True) is not False


def is_adult(case: dict) -> bool:
    """A case, chain placeholder, chain instance or stored row of the adult page."""
    if str(case.get("category") or "").startswith("nsfw"):
        return True
    for k in ("chain_spec", "chain"):
        part = str((case.get(k) or {}).get("part") or "")
        if part.startswith("nsfw/"):
            return True
    return False


def plan_cases(cfg: dict | None, entry: dict, cases: list[dict], say=print) -> list[dict]:
    """The model's cases: unchanged (the same list) when its adult page is on, else every adult unit dropped and
    the plan line printed once."""
    if adult_page_on(cfg, entry):
        return cases
    kept = [c for c in cases if not is_adult(c)]
    if len(kept) != len(cases):
        say(PLAN_LINE.format(label=entry.get("name")))
    return kept


def refuse_explicit(cfg: dict | None, entries: list[dict], cases: list[dict], only: list[str] | None) -> None:
    """``--cases`` naming an adult unit for a model whose page is off: refused before any call."""
    if not only:
        return
    adult = sorted(c["id"] for c in cases if c.get("id") in set(only) and is_adult(c))
    off = [e["name"] for e in entries if not adult_page_on(cfg, e)]
    if adult and off:
        raise SystemExit(f"refusing --cases {', '.join(adult)} for {', '.join(off)}: adult-page cases never go to "
                         f"a model with adult_page off (the Claude Code route is always off)")


def assert_sendable(cfg: dict | None, entry: dict, case: dict) -> None:
    """The send-path guard: raises before an adult unit is sent to a model whose adult page is off."""
    if is_adult(case) and not adult_page_on(cfg, entry):
        raise AdultPageRefused(f"{entry.get('name')}: refusing to send adult-page case {case.get('id')!r} "
                               f"(adult_page off for this model)")


def page_off_for_label(label: str, cfg: dict | None, meta: dict | None) -> bool:
    """Board side: the model's adult page is off by its config entry, or its stored meta says the Claude Code
    route produced it (a row set copied without its models.yaml still reads as off)."""
    m = meta or {}
    if m.get("provider_type") == CLAUDE_CODE or m.get("provider") == CLAUDE_CODE:
        return True
    for e in (cfg or {}).get("models") or []:
        if e.get("name") == label:
            try:
                return not adult_page_on(cfg, e)
            except ConfigError:
                return True
    return False


def board_rows(label: str, cfg: dict | None, meta: dict | None, by_t: dict) -> dict:
    """``by_t`` without the adult rows when the model's adult page is off (the same object otherwise)."""
    if not page_off_for_label(label, cfg, meta):
        return by_t
    return {t: [r for r in rows if not is_adult(r)] for t, rows in by_t.items()}
