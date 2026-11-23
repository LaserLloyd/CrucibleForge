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

Opt-in (fix round 2026-11-18, P1, DECISIONS-45 D2): the adult page is no longer part of a default run for ANY
model. The page runs for a model only when the run asks for it (``--adult`` on ``run``, ``all``, ``recover``,
``escalate`` and ``bench-all``, carried as ``cfg[RUN_KEY]``), when the model's entry says ``adult_page: true``, or
when ``--cases`` names adult units explicitly. Without that, :func:`plan_cases` drops the adult units and prints
``adult page: not included (add --adult)``. "Allowed" (:func:`adult_page_on`) is unchanged and still decides the
send path and the board: the Claude Code route and ``adult_page: false`` are never sent an adult unit.

An adult unit = a case or row of category ``nsfw`` (the 4.5 adult chains and the 4.x NSFW / Uncensored packs all
carry it) or a chain whose section part is under ``nsfw/``.
"""
from __future__ import annotations

from .config import ConfigError

CLAUDE_CODE = "claude_code"
PLAN_LINE = "NSFW+: not run for {label} (nsfw_plus off for this model)"
#: the plan line of a model whose page is allowed but was not asked for (P1, 2026-11-18)
OPT_IN_LINE = "NSFW+: not run (--no-nsfw-plus)"
#: the run-level switch ``--adult`` sets on the applied config (``cli._apply_profile_arg``)
RUN_KEY = "adult_run"
#: the 4.1 archive profile runs as it always did (its NSFW cases are part of its board): no opt-in there
ARCHIVE_PROFILES = frozenset({"bench"})


def run_wants_adult(profile_name: str | None, flag: bool) -> bool:
    """The run-level answer. Since the realignment NSFW+ is ON in every standard run: ``flag`` is the run's
    answer (``args_wants``: True unless ``--no-nsfw-plus`` / ``--no-adult``); a profile from the archive (4.1)
    is never changed."""
    return bool(flag) or (profile_name in ARCHIVE_PROFILES)


def args_wants(args) -> bool:
    """NSFW+ for a parsed command line: on by default; ``--no-nsfw-plus`` (alias ``--no-adult``) opts out.
    ``--adult`` is a silent alias of the default."""
    return not (getattr(args, "no_nsfw_plus", False) or getattr(args, "no_adult", False))


def entry_flag(entry: dict):
    """A model entry's switch: the new ``nsfw_plus`` key, else the old ``adult_page`` key (both still read)."""
    return entry["nsfw_plus"] if "nsfw_plus" in entry else entry.get("adult_page", None)


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
    v = entry_flag(entry)
    if v is not None and not isinstance(v, bool):
        raise ConfigError(f"model {entry.get('name')}: nsfw_plus is true or false, not {v!r}")
    if v is True and is_claude_code(cfg, entry):
        raise ConfigError(
            f"model {entry.get('name')}: nsfw_plus: true is refused on the Claude Code route (provider type "
            f"claude_code). NSFW+ cases never go to Claude Code rows; those rows are validation only. "
            f"Remove the key (the default there is off).")


def adult_page_on(cfg: dict | None, entry: dict) -> bool:
    """Whether the adult page runs for this model (validates the key first)."""
    validate_entry(cfg, entry)
    if is_claude_code(cfg, entry):
        return False
    return entry_flag(entry) is not False


def adult_page_planned(cfg: dict | None, entry: dict) -> bool:
    """Whether a run plans the adult page for this model: allowed (:func:`adult_page_on`) AND asked for, by the
    run (``--adult``) or by the entry (``adult_page: true``). Default: no (P1)."""
    if not adult_page_on(cfg, entry):
        return False
    return bool((cfg or {}).get(RUN_KEY)) or entry_flag(entry) is True


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
    """The model's cases: unchanged (the same list) when its adult page is planned, else every adult unit dropped
    and one plan line printed (``PLAN_LINE`` when the page is off for the model, ``OPT_IN_LINE`` when it was
    simply not asked for)."""
    if adult_page_planned(cfg, entry):
        return cases
    kept = [c for c in cases if not is_adult(c)]
    if len(kept) != len(cases):
        say(PLAN_LINE.format(label=entry.get("name")) if not adult_page_on(cfg, entry) else OPT_IN_LINE)
    return kept


def explicit_opt_in(cases: list[dict], only: list[str] | None) -> bool:
    """``--cases`` names at least one adult unit: an explicit adult invocation (counts as ``--adult``)."""
    if not only:
        return False
    want = set(only)
    return any(c.get("id") in want and is_adult(c) for c in cases)


def refuse_explicit(cfg: dict | None, entries: list[dict], cases: list[dict], only: list[str] | None) -> None:
    """``--cases`` naming an adult unit for a model whose page is off: refused before any call."""
    if not only:
        return
    adult = sorted(c["id"] for c in cases if c.get("id") in set(only) and is_adult(c))
    off = [e["name"] for e in entries if not adult_page_on(cfg, e)]
    if adult and off:
        raise SystemExit(f"refusing --cases {', '.join(adult)} for {', '.join(off)}: NSFW+ cases never go to "
                         f"a model with nsfw_plus off (the Claude Code route is always off)")


def assert_sendable(cfg: dict | None, entry: dict, case: dict) -> None:
    """The send-path guard: raises before an adult unit is sent to a model whose adult page is off."""
    if is_adult(case) and not adult_page_on(cfg, entry):
        raise AdultPageRefused(f"{entry.get('name')}: refusing to send NSFW+ case {case.get('id')!r} "
                               f"(nsfw_plus off for this model)")


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
