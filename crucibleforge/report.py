"""Aggregate transcripts into the board: report.md (Chat + Coding scorecard
and one component table), failures.md (per-model failures, capped), report.json
and report.html.

ONE benchmark (profiles/bench.yaml), TWO headline scores:
- **Chat**   = RP, NSFW, explicit peak, willingness, steer (122B-judged)
- **Coding** = programs, tools, instruct, reasoning+math pooled (deterministic)
Overall (their weighted mean) is only the sort key.

Board hygiene: only rows of the ``bench`` profile, at the current suite
revision, count; per (case, repeat) only the LATEST run is used, so a re-run
never inflates a denominator. Everything else is "older" and stays off the
board (results/archive-*/ keeps it).

Design rules:
- Single source of truth: results/transcripts_<label>.jsonl (judge verdicts
  live on the same rows). No stale parallel files.
- Medians + min-max + n= everywhere; means of 4 samples are not presented
  as truth.
- None never crashes a table: missing data renders as "-".
- Rows still waiting for the judge are surfaced loudly, not silently
  dropped (the v1 report happily printed empty tables).
"""
from __future__ import annotations

import argparse
import contextlib
import contextvars
import copy
import json
import logging
import os
import sys
import re
import statistics
from datetime import datetime, timezone

from .api import row_answer
from .config import ConfigError, results_dir, load_config, load_transcripts, set_results_dir
from .graders import refusal_heuristic
from . import lineage as _lineage
from . import notes45 as _notes45
from . import attempts45 as _attempts45

log = logging.getLogger(__name__)

# Bootstrap mode (headline.py): model_stats runs ~1000x per model on
# resampled rows. Inside it the profile is read once, coverage (notes only)
# and the code bank's own CI loop are skipped, deep-RP rows are scored once,
# and a drawn case's multiplicity (``mult``) tells bank_stats how often a
# task with NO graded row was drawn (it still counts 0 that many times).
_BOOT: contextvars.ContextVar = contextvars.ContextVar("crucibleforge_boot", default=None)


@contextlib.contextmanager
def boot_mode():
    tok = _BOOT.set({"mult": None, "bank_profile": None, "rpdeep": {}, "ladder": None})
    try:
        yield
    finally:
        _BOOT.reset(tok)


# One board build (generate): read-only per-build caches. The cfg object is
# held next to its id so a reused id can never alias another cfg.
_SCOPE: contextvars.ContextVar = contextvars.ContextVar("crucibleforge_report_scope", default=None)
#: set for the length of a PUBLIC render (generate(public=True)): texts that would otherwise name an internal
#: id (a Lockout rung "LK04") say it in plain words instead. The operator's own board keeps the ids.
_PUBLIC_WORDS: contextvars.ContextVar = contextvars.ContextVar("crucibleforge_public_words", default=False)


@contextlib.contextmanager
def report_scope():
    if _SCOPE.get() is not None:
        yield
        return
    from . import version as _version
    tok = _SCOPE.set({"profile": {}})
    try:
        with _version.pinned_case_files():
            yield
    finally:
        _SCOPE.reset(tok)


def boot_set_mult(mult: dict[str, int]) -> None:
    b = _BOOT.get()
    if b is not None:
        b["mult"] = mult


def _task_key(r: dict) -> str:
    """A code-bank task / deep-RP case key: the case id, plus the copy
    number of a bootstrap resample that drew the case more than once."""
    k = r.get("_boot_k")
    return r["case_id"] if not k else f"{r['case_id']}#b{k}"

def report_md_path():
    return results_dir() / "report.md"


def report_json_path():
    return results_dir() / "report.json"


def report_html_path():
    return results_dir() / "report.html"


def failures_md_path():
    return results_dir() / "failures.md"

RP_SINGLE_DIMS = ["prose", "character", "dialogue", "atmosphere", "emotion", "agency"]
RP_MULTI_DIMS = ["prose", "character", "dialogue", "emotion", "agency", "consistency"]
NSFW_DIMS = ["prose", "emotion", "erotic", "explicitness"]
# the harder Chat section (suite 3.4.0, docs/CHAT.md)
RP_SESSION_DIMS = ["identity", "continuity", "ooc", "voice", "craft", "initiative"]
RP_SCENE_DIMS = ["identity", "integrity", "voice", "craft", "initiative", "calibration"]
ERP_DIMS = ["identity", "erotic", "explicitness", "continuity", "ooc", "voice", "prose"]
STORY_DIMS = ["checklist", "craft", "character", "coherence", "originality", "restraint",
              "ending"]
RP_RUBRICS_V2 = ("rp_session", "rp_scene")
# rows that feed the NSFW components (willing, explicit peak, NSFW quality)
EROTIC_RUBRICS = ("nsfw", "nsfw_craft", "erp_session")
# 4.5 non-sexual dark role-play rows (theme_rp R24 gore / R28 drugs): lead 2026-11-14, they never feed erotic quality or
# the explicit peak; they count in the role-play craft / identity path (rp_block_v2) and in willingness (by_cat rows)
DARK_RP_RUBRICS = ("rp_dark_session",)
# the truncation-rate denominator: genuine creative rows
CREATIVE_RUBRICS = ("rp_single", "rp_multi", "nsfw", "rp_session", "rp_scene",
                    "nsfw_craft", "erp_session", "rp_dark_session", "story")
# RP = 0.40 identity + 0.25 continuity + 0.35 craft (identity is the maintainer's
# "it forgets who's me and who's it"); NSFW = 0.45 erotic + 0.25 craft +
# 0.30 constraints; Story = 3/4 judge + 1/4 deterministic checks
RP_WEIGHTS = {"identity": 0.40, "continuity": 0.25, "craft": 0.35}
NSFW_WEIGHTS = {"erotic": 0.45, "craft": 0.25, "constraints": 0.30}
STORY_JUDGE_WEIGHT = 0.75


def _median(vals):
    vals = [v for v in vals if v is not None]
    return statistics.median(vals) if vals else None


def _mean(vals):
    vals = [v for v in vals if v is not None]
    return sum(vals) / len(vals) if vals else None


def _spread(vals):
    vals = [v for v in vals if v is not None]
    if not vals:
        return None
    return (min(vals), max(vals))


def fmt(x, spec: str = ".1f", suffix: str = "") -> str:
    if x is None:
        return "-"
    try:
        return f"{x:{spec}}{suffix}"
    except (TypeError, ValueError):
        return str(x)


def fmt_pct(x) -> str:
    return "-" if x is None else f"{x * 100:.0f}%"


# ----------------------------------------------------------------- scoring
# Weighted scorecard. Weights (percent) are overridable via a `scoring:` block
# in models.yaml; components missing from a run are dropped and the remaining
# weights renormalised (the report says so).
DEFAULT_SCORING = {
    "chat": {"rp": 20, "nsfw": 15, "story": 10, "explicit_peak": 5, "willing": 5,
             "steer": 5},
    "coding": {"coding": 20, "tooluse": 10, "instruct": 10, "reasoning": 5},
}
# "coding" the CATEGORY is shown as "Programs" so it never collides with the
# Coding headline score it is one component of.
COMPONENT_LABELS = {"rp": "RP", "nsfw": "NSFW", "story": "Story",
                    "explicit_peak": "Explicit peak",
                    "willing": "Willing", "steer": "Steer", "coding": "Programs",
                    "tooluse": "Tools", "instruct": "Instruct", "reasoning": "Reason",
                    "codebank": "Code bank", "codeagent": "Agent",
                    "codebank_long": "Code bank (long)", "codeagent_long": "Agent (long)",
                    "rpdeep": "Deep RP", "nsfw_gated": "NSFW (gated)"}
# code bank categories (codebank.py / codeenv.py): scored by the mean gated
# test-group score per task, not the strict pass rate
BANK_CATS = ("codebank", "codeagent", "codebank_long", "codeagent_long")
CHAT_KEYS = ["rp", "nsfw", "story", "explicit_peak", "willing", "steer"]
CODING_KEYS = ["coding", "tooluse", "instruct", "reasoning"]
# categories with a hand-written aggregate below; any OTHER judged category
# gets the generic one (see _generic_judged) and can join Chat just by being
# given a weight — see README "Adding a Chat component"
# rpdeep has its own scorer (scripts/rpdeep_score.py; report block at merge
# time): its rubric has no dims, so the generic path would score a REFUSED
# row 1.0 from its first flag — it must never reach _generic_judged.
_BUILTIN_JUDGED = {"rp", "nsfw", "story", "steer", "planning", "overrefusal", "rpdeep"}


def component_label(k: str) -> str:
    return (COMPONENT_LABELS.get(k) or _profile_labels().get(k)
            or k.replace("_", " ").title())


_PROFILE_LABELS: dict | None = None


def _profile_labels() -> dict:
    """Display names a profile gives its components (``labels:`` — a private
    component names itself in the git-ignored overlay, never in this file)."""
    global _PROFILE_LABELS
    if _PROFILE_LABELS is None:
        try:
            from .profiles import DEFAULT_PROFILE, load_profile
            _PROFILE_LABELS = dict(load_profile(active_name()).get("labels") or {})
        except Exception:
            _PROFILE_LABELS = {}
    return _PROFILE_LABELS


# ---------------------------------------------------------------- audience
# Two audiences, ONE mechanism. The local board (default) shows everything.
# A PUBLIC artifact (`crucibleforge report --public`) is built with
# cfg["_audience"] == "public": _hidden() then returns profiles.private_scope(),
# and the few functions that decide WHAT is on the board — board_filter (rows),
# _profile_case_ids / _expected_case_count (coverage), scoring_config
# (components and weights) — leave private categories out. Everything
# downstream (scorecards, markdown, JSON, HTML, failures, notes) is computed
# from that and so is clean by construction; public_leaks() then re-checks
# the rendered text and generate() refuses to write on any hit (fail closed).
PUBLIC = "public"


class PrivateLeak(RuntimeError):
    """A public artifact still contained a private term — nothing was written."""


def public_render(cfg: dict | None) -> bool:
    """True for a render meant for anyone but the operator (``report --public``). Unlike
    :func:`_hidden` this does not depend on how many private terms exist: a clean tree
    (no cases/private) has an empty scope, and an empty dict must not read as "not public"."""
    return (cfg or {}).get("_audience") == PUBLIC


def _hidden(cfg: dict | None) -> dict | None:
    if (cfg or {}).get("_audience") != PUBLIC:
        return None
    sc = _SCOPE.get()
    if sc is not None and "private" in sc:
        return sc["private"]
    from .profiles import public_private_scope
    scope = public_private_scope(cfg)
    if sc is not None:
        sc["private"] = scope
    return scope


def check_private_scope(scope: dict) -> int:
    """PUBOUT 4.5: say how many private terms the public render checks against (the count only,
    never a term) and fail closed when ``$CRUCIBLEFORGE_PRIVATE_FROM`` names a checkout that
    yielded none: an empty guard there means it read nothing, not that nothing is private."""
    from .profiles import PRIVATE_FROM_ENV
    n = len(scope.get("terms") or ())
    src = os.environ.get(PRIVATE_FROM_ENV)
    log.info("public render: private-term guard holds %d term(s)%s", n,
             f" (incl. ${PRIVATE_FROM_ENV})" if src else "")
    if src and n == 0:
        raise PrivateLeak(f"${PRIVATE_FROM_ENV} is set but the guard loaded 0 private terms: refusing "
                          "to write a public board unchecked")
    if n == 0:
        # one plain line on stderr (a log line is easy to miss): the check did NOT run
        print(f"public render: private-term check SKIPPED, no list is configured (this tree has no "
              f"cases/private); set ${PRIVATE_FROM_ENV}=<checkout holding the private files> to run it",
              file=sys.stderr)
    return n


def public_leaks(text: str, scope: dict) -> list[str]:
    """The private terms (category names, case ids, labels) found in ``text``."""
    low = text.lower()
    return [t for t in scope["terms"] if t and t.lower() in low]
# failures.md shows at most this many failure bullets per model
FAILURES_PER_MODEL = 5


def scoring_config(cfg: dict | None) -> dict:
    """Component weights: built-in defaults < the benchmark profile's
    ``scoring:`` block (where a new Chat category gets its weight, next to
    its cases) < models.yaml ``scoring:``."""
    sc = copy.deepcopy(DEFAULT_SCORING)
    layers = []
    prof = None
    try:
        from .profiles import DEFAULT_PROFILE, load_profile
        prof = load_profile(active_name(cfg), cfg)
        layers.append(prof.get("scoring") or {})
    except Exception:
        pass
    layers.append((cfg or {}).get("scoring") or {})
    if prof is not None and prof.get("scores"):
        return _declared_scoring(prof, layers, cfg)
    for user in layers:
        # "code" is the pre-2026-09-23 name of the coding group
        # "reason" (suite 3.9): Reason is its own score next to Chat and Code
        for grp, keys in (("chat", ("chat",)), ("coding", ("coding", "code")),
                          ("reason", ("reason",))):
            for k in keys:
                if isinstance(user.get(k), dict):
                    sc[grp] = {kk: float(v) for kk, v in user[k].items()}
                    break
    hidden = _hidden(cfg)
    if hidden:
        for grp in sc:
            sc[grp] = {k: v for k, v in sc[grp].items() if k not in hidden["categories"]}
    return sc


def _declared_scoring(prof: dict, layers: list[dict], cfg: dict | None) -> dict:
    """Weights of a profile that declares its ``scores:`` (tiers.scores):
    one group per score (built-in categories and custom tests)."""
    sc, _ = _declared_weights(prof, layers, cfg)
    return sc


def _audience_profile(prof: dict, cfg: dict | None) -> dict:
    """The profile as this board's audience sees it: on a public board
    (``cfg["_audience"] == "public"``) non-public custom tests are gone."""
    if (cfg or {}).get("_audience") == PUBLIC and not prof.get("_public"):
        prof = {**prof, "_public": True}
    return prof


def _declared_weights(prof: dict, layers: list[dict] | None,
                      cfg: dict | None) -> tuple[dict, dict]:
    """(group weights, part weights {score: {part: weights}}) of a profile
    that declares ``scores:``. A score with parts takes the union of its
    parts' weights as its group weights (for the component table and the
    judged-score test); its value is computed per part (points_scorecard).
    PRIVATE categories never count in a built-in category, whatever
    ``scoring:`` group an older overlay put them in: they are custom tests
    (tiers.custom_scores). A part's ``scoring:`` LIST weighs each component
    by its number of tier-1 cases in that part (the maintainer 2026-10-28)."""
    from . import tiers as _tiers
    prof = _audience_profile(prof, cfg)
    if layers is None:
        layers = [prof.get("scoring") or {}, (cfg or {}).get("scoring") or {}]
    defs = _tiers.scores(prof)
    groups = [x["group"] for x in defs]
    sc: dict[str, dict] = {g: {} for g in groups}
    for user in layers:
        for g in groups:
            if isinstance(user.get(g), dict):
                sc[g] = {k: float(v) for k, v in user[g].items()}
    priv = set(prof.get("private") or [])
    custom_groups = {d["group"] for d in defs if d.get("custom")}
    for g in groups:
        if g not in custom_groups:
            sc[g] = {k: v for k, v in sc[g].items() if k not in priv}
    parts = {d["key"]: {p["key"]: dict(p["scoring"]) for p in d.get("parts") or []} for d in defs}
    by_cat = prof.get("cases") or {}
    cases_of = declared_case_sets(prof, cfg, parts)
    for d in defs:
        for p in d.get("parts") or []:
            w = parts[d["key"]][p["key"]]
            if is_pack_part(prof, d["key"], p["key"]):
                # a Chat pack part (packs45 scores it, max 0) weighs no old-tree component: its ``cases: []``
                # would otherwise count every profile case of its component (3 packs x 8 rpdeep = +24)
                w.clear()
                continue
            ids = cases_of.get(f"{d['key']}/{p['key']}")
            for comp in [k for k, v in w.items() if v is None]:
                cats = _component_categories(comp)
                w[comp] = float(sum(1 for cat, cids in by_cat.items() if cat in cats
                                    for c in cids or [] if ids is None or c in ids))
    for d in defs:
        if parts.get(d["key"]):
            merged: dict[str, float] = {}
            for w in parts[d["key"]].values():
                for k, v in w.items():
                    merged[k] = merged.get(k, 0.0) + v
            sc[d["group"]] = merged
    hidden = _hidden(cfg)
    if hidden:
        for grp in sc:
            sc[grp] = {k: v for k, v in sc[grp].items() if k not in hidden["categories"]}
        for sk in parts:
            for pk in parts[sk]:
                parts[sk][pk] = {k: v for k, v in parts[sk][pk].items()
                                 if k not in hidden["categories"]}
    return sc, parts


def _component_categories(comp: str) -> set[str]:
    """The case categories whose rows produce a scorecard component."""
    if comp == "reasoning":
        return {"reasoning", "math"}
    if comp in ("explicit_peak", "willing", "nsfw_gated"):
        return {"nsfw"}
    # "lockout" reads the flag ladder's rows, never a profile case: no category
    return {comp}


def declared_case_sets(prof: dict, cfg: dict | None,
                       parts: dict | None = None) -> dict[str, set[str]]:
    """{"<score>" | "<score>/<part>": tier-1 case ids} of every score/part
    that declares ``cases`` (a custom part without ``cases`` takes every
    profile case of its scoring categories)."""
    from . import tiers as _tiers
    prof = _audience_profile(prof, cfg)
    out: dict[str, set[str]] = {}
    by_cat = prof.get("cases") or {}
    for d in _tiers.scores(prof):
        if d.get("cases"):
            out[d["key"]] = set(d["cases"])
        for p in d.get("parts") or []:
            key = f"{d['key']}/{p['key']}"
            if p.get("cases"):
                out[key] = set(p["cases"])
            elif d.get("custom"):
                out[key] = {c for cat in p["scoring"] for c in by_cat.get(cat) or []}
    return out


def case_part_ran_out(prof: dict | None, part: dict) -> str | None:
    """The ran-out rule of a CASE part (lead 2026-11-14, D's 8B rc2 run): the part's own ``ran_out``,
    else ``exclude`` for a code-bank part of a 4.5 profile (one with chains: the owner's ruling, a cut reply
    is not counted). None = no rule (a 4.1 profile: its grading stays byte-identical)."""
    rule = part.get("ran_out")
    if rule is None and (prof or {}).get("chains") and "codebank" in (part.get("scoring") or []):
        rule = "exclude"
    if rule is not None and rule not in ("zero", "exclude"):
        raise ConfigError(f"part {part.get('key')}: ran_out is zero or exclude (got {rule!r})")
    return rule


#: unit scores a case part may declare (``unit_score:``); tool use only
UNIT_SCORES = ("half_pass", "half_task")


def part_unit_score(part: dict) -> str | None:
    """A tool-use part's unit score (lead 2026-11-15 02:22, G2): ``half_pass`` = a run scores half
    for completing the task with every step right and half for its share of right steps (the step
    share alone left a wrong booking most of its credit; pass/fail alone is too noisy on six runs).
    None = the step share (4.1 and every other part)."""
    rule = part.get("unit_score")
    if rule is None:
        return None
    if rule not in UNIT_SCORES or list(part.get("scoring") or []) != ["tooluse"]:
        raise ConfigError(f"part {part.get('key')}: unit_score {rule!r} is {UNIT_SCORES} on a tool-use part only")
    return rule


def task_rate(rows: list[dict], cases: dict) -> float | None:
    """The share of graded tool-use runs whose task was done right (tooltask.task_done), over the same
    runs the pass rate counts (grade pass / fail). None = no such run."""
    from .tooltask import task_done
    graded = [r for r in rows if r.get("grade") in ("pass", "fail")]
    if not graded:
        return None
    return sum(1 for r in graded if task_done(r, cases.get(r.get("case_id")) or {})) / len(graded)


def _row_cut(r: dict) -> bool:
    return r.get("finish_reason") == "length" or bool(r.get("truncated")) or bool(r.get("cut"))


#: D10 as amended (fix round 2026-11-18): "not measured" needs at least this many cut units as well as more than
#: scale.RAN_OUT_NOT_MEASURED of them (a three-unit part must not lose its points to one long think)
RAN_OUT_MIN_UNITS = 2


def empty_units45(chain_rows: list[dict]) -> dict[str, int]:
    """{"<score>/<part>": units with an empty reply} over the generated rows the board counts (a unit = a chain
    id; an empty reply is one with no text, no tool call and no ceiling cut)."""
    seen: dict[str, set] = {}
    for r in chain_rows or []:
        ch = r.get("chain") or {}
        if ch.get("part") and ch.get("id") and _row_empty_reply(r):
            seen.setdefault(str(ch["part"]), set()).add(ch["id"])
    return {k: len(v) for k, v in seen.items()}


def cut_not_measured(n_cut: int, n_units: int) -> bool:
    """The one truncation rule for every part (D10): a part is "not measured" when more than a quarter of its
    units were cut at their ceiling AND at least RAN_OUT_MIN_UNITS were. Below that the cut units leave the
    mean and are counted."""
    from .scale import RAN_OUT_NOT_MEASURED
    n_cut, n_units = int(n_cut or 0), int(n_units or 0)
    return bool(n_units) and n_cut >= RAN_OUT_MIN_UNITS and n_cut / n_units > RAN_OUT_NOT_MEASURED


def _row_empty_reply(r: dict) -> bool:
    """A reply with no text and no tool call that was not cut at its ceiling (the model's own empty answer). A
    multi-step tool row keeps its steps in ``tool_conversation`` (its own text and calls fields stay empty): it was
    played and graded step by step, so it is never an empty reply."""
    return (not str(r.get("response") or "").strip() and not r.get("tool_calls") and not r.get("tool_conversation")
            and not _row_cut(r) and not r.get("error"))


def route_cannot_run(meta: dict | None, part: dict) -> str | None:
    """Why the model's route cannot run a case part, else None (lead 2026-11-14 night, option C): the
    Claude Code route has no tools (claudecode.run_chat refuses them), so a tool-use part on a claude_code
    row reads "not run": it contributes nothing and nothing is scaled up over it (the section prints
    "x of <measured max> measured, N not run") -- never a 0, never a dash for rows that cannot exist.
    Those rows are unpublished validation rows."""
    m = meta or {}
    if ((m.get("provider_type") == "claude_code" or m.get("provider") == "claude_code")
            and "tooluse" in (part.get("scoring") or [])):
        return "the Claude Code route has no tools"
    return None


#: what the one cut rule does with a unit (:func:`unit_cut`): scored as usual, left out of the mean (counted), did
#: not finish at the full budget (graded as delivered, kept in the mean, counted), or a degenerate cut reply (a
#: loop: graded as delivered, kept, never counted as a cut)
CUT_SCORED, CUT_LEFT_OUT, CUT_DNF, CUT_DEGENERATE = "scored", "left_out", "did_not_finish", "degenerate"


def cut_disposition(*, numbered: bool, cut: bool, full: bool, degenerate: bool, cut_at_full_budget: str) -> str:
    """The one cut rule (D10, D10a) for one unit, from its facts. A multi-turn session with a turn cut at its ceiling
    is left out as a whole (never "did not finish"); a single reply cut at >= 98% of the full budget under ``zero``
    did not finish (graded as delivered); any other cut is left out; a degenerate cut reply is graded as delivered
    and not counted as a cut. Code, Chat and NSFW+ all call this (through :func:`unit_cut`)."""
    if not cut:
        return CUT_DEGENERATE if degenerate else CUT_SCORED
    if numbered:
        return CUT_LEFT_OUT
    return CUT_DNF if (cut_at_full_budget == "zero" and full) else CUT_LEFT_OUT


def unit_cut(rows: list[dict], cut_at_full_budget: str = "omit", full_ceiling: int | None = None,
             numbered: bool | None = None) -> str:
    """:func:`cut_disposition` of ONE unit's rows: a single call (rows with no turn number) or a multi-turn session
    (numbered turns; ``numbered`` overrides when the caller knows, e.g. a single reply stored as turn 1).
    ``full_ceiling`` = the budget used when a row did not record the one it was sent."""
    from . import degenerate as _dg
    from .chains import full_budget_cut
    num = [r for r in rows if r.get("turn") not in (None, "final")]
    look = [r for r in rows if r.get("turn") != "final"] or list(rows)
    if numbered is not None:
        num = look if numbered else []
    cut_rows = [r for r in look if _row_cut(r)]
    live = [r for r in cut_rows if not _dg.is_degenerate(r)]
    full = bool(live) and all(full_budget_cut(r, r.get("max_tokens_sent") or full_ceiling) for r in live)
    return cut_disposition(numbered=bool(num), cut=bool(live), full=full, degenerate=bool(cut_rows) and not live,
                           cut_at_full_budget=cut_at_full_budget)


def case_part_rows(rows: list[dict], rule: str | None, cut_at_full_budget: str = "omit",
                   full_ceiling: int | None = None) -> tuple[list[dict], dict | None]:
    """(the rows a case part is scored on, its ran-out record). Under ``exclude`` each unit (a single call, or the
    numbered turns of one job = one session) follows :func:`unit_cut`: a cut reply that is not degenerate is left
    out (reported, not scored as a failed answer); a degenerate cut reply stays (graded as delivered); under ``zero``
    a single reply cut at the full budget did not finish and stays (graded as delivered, counted apart). When
    :func:`cut_not_measured` holds (more than a quarter of the part's units cut, and at least two), the part is "not
    measured": no row is scored. The record also counts the empty replies (``empty``)."""
    if rule != "exclude":
        return rows, None
    calls = [r for r in rows if r.get("turn") in (None, "final")]
    # a multi-turn case (the 4.1 deep role-play cases, rc4's long scenes) stores one row per numbered turn: the
    # job (case, repeat) is the unit, and a turn cut at its ceiling makes the whole job ran out (lead 2026-11-15:
    # MiniMax WR1-ninth-string turn 3 cut at 16,384 with no text was scored 0; under exclude it leaves the mean).
    jobs: dict[tuple, list[dict]] = {}
    for r in rows:
        if r.get("turn") not in (None, "final"):
            jobs.setdefault((r.get("case_id"), r.get("repeat")), []).append(r)
    drop: set = set()
    dnf: list[dict] = []
    n_cut = 0
    for r in calls:
        d = unit_cut([r], cut_at_full_budget, full_ceiling)
        if d == CUT_LEFT_OUT:
            n_cut += 1
            drop.add(id(r))
        elif d == CUT_DNF:
            dnf.append(r)
    for jrows in jobs.values():
        if unit_cut(jrows, cut_at_full_budget, full_ceiling) == CUT_LEFT_OUT:
            n_cut += 1
            drop |= {id(r) for r in jrows}
    n_calls = len(calls) + len(jobs)
    share = n_cut / n_calls if n_calls else 0.0
    n_empty = (sum(1 for r in calls if _row_empty_reply(r))
               + sum(1 for jrows in jobs.values() if any(_row_empty_reply(r) for r in jrows)))
    rec = {"rule": rule, "calls": n_calls, "ran_out": n_cut, "share": share,
           "not_measured": cut_not_measured(n_cut, n_calls)}
    if n_empty:
        rec["empty"] = n_empty        # only when there is one (the record's shape is pinned elsewhere)
    if dnf:
        rec["did_not_finish"] = {"n": len(dnf), "of": n_calls,
                                 "ceiling": int(max(r.get("max_tokens_sent") or full_ceiling for r in dnf)),
                                 "units": [[r.get("case_id"), r.get("repeat")] for r in dnf]}
    if rec["not_measured"]:
        return [], rec
    return [r for r in rows if id(r) not in drop], rec


def cut_words(rec: dict | None) -> str:
    """What happened to a segment's cut units, in plain words, from the scorer's own record ``{left_out,
    did_not_finish, dnf_zero, of}``: "2 of 21 cut, left out of the mean; 1 of 21 did not finish at the full budget,
    scored 0". "" when nothing was cut. The same words for Code, Chat and NSFW+."""
    if not rec:
        return ""
    of = int(rec.get("of") or 0)
    bits = []
    lo, dn = int(rec.get("left_out") or 0), int(rec.get("did_not_finish") or 0)
    if lo:
        bits.append(f"{lo} of {of} cut, left out of the mean")
    if dn:
        bits.append(f"{dn} of {of} did not finish at the full budget, "
                    + ("scored 0" if rec.get("dnf_zero", True) else "graded as delivered"))
    return "; ".join(bits)


#: components measured by deterministic graders only (never wait for the judge)
DETERMINISTIC_KEYS = {"coding", "tooluse", "instruct", "reasoning", "lockout", *("codebank", "codeagent",
                      "codebank_long", "codeagent_long")}


#: components that can be scored with partial credit (EVAL-FABLE M2; the
#: profile's ``partial_credit:`` list turns it on): programs by test-group
#: share, tools by step rate, instruct/reasoning by element rate, steer by
#: the share of its judge verdict + elements + checks
PARTIAL_COMPONENTS = ("coding", "tooluse", "instruct", "reasoning", "steer")


def component_values(s: dict, partial: set[str] | frozenset = frozenset()) -> dict:
    """Component -> 0..1 value (None when not measured). Built-in components
    first; every judged category without a built-in aggregate contributes
    its generic score under its own name. ``partial`` = components scored
    with partial credit instead of the strict pass rate."""
    def r10(x):
        return None if x is None else max(0.0, min(1.0, x / 10.0))
    generic = {k: v.get("score") for k, v in (s.get("judged_generic") or {}).items()}
    return {**generic, **{
        "rp": r10(s["rp"].get("overall")),
        "nsfw": r10(s["nsfw"].get("erotic_quality")),
        # stats written before the Story component have no "story" key
        "story": r10((s.get("story") or {}).get("overall")),
        "explicit_peak": r10(s["nsfw"].get("explicitness_peak")),
        "willing": s["nsfw"].get("willingness"),
        # suite 3.9 NSFW RP / NSFW writing (EVAL-FABLE M5/§11): per row
        # engagement x heat delivered x card checks, mean over the rows
        "nsfw_gated": s["nsfw"].get("gated"),
        # Admin N (task C): the share of Lockout rungs written (each rung one code-graded element)
        "lockout": s["nsfw"].get("lockout_share"),
        "steer": s["steer"].get("rate"),
        "coding": s["coding"].get("rate"),
        "tooluse": s["tooluse"].get("rate"),
        "instruct": s["instruct"].get("rate"),
        # Reason pools the reasoning and math categories (both are "get the
        # one right answer" tasks; math is the harder end of the same axis)
        "reasoning": _pooled_rate(s.get("reasoning"), s.get("math")),
        **{c: (s.get(c) or {}).get("score") for c in BANK_CATS},
        # deep-lore RP (suite 3.9): mean per case of the weighted fact rate
        "rpdeep": (s.get("rpdeep") or {}).get("score"),
        **_partial_values(s, partial),
    }}


def _partial_values(s: dict, partial) -> dict:
    out = {}
    for c in partial or ():
        if c in ("coding", "tooluse", "instruct"):
            blk = s.get(c) or {}
            if blk.get("n"):
                out[c] = blk.get("element_rate")
        elif c == "reasoning":
            parts = [(b.get("element_rate"), b.get("n", 0)) for b in (s.get("reasoning"), s.get("math"))
                     if b and b.get("n")]
            if parts:
                out[c] = sum(v * n for v, n in parts) / sum(n for _, n in parts)
        elif c == "steer":
            out[c] = (s.get("steer") or {}).get("partial_rate")
    return out


def _pooled_rate(*cats) -> float | None:
    n = sum((c or {}).get("n", 0) for c in cats)
    if not n:
        return None
    return sum((c or {}).get("passed", 0) for c in cats) / n


def scorecard(s: dict, sc: dict, in_total: list[str] | None = None,
              partial: set[str] | frozenset = frozenset()) -> dict:
    """Component values -> one 0-100 number per scoring group (the weighted
    mean of the group's measured components; a missing one renormalises),
    and ``total`` = the groups combined by their weight sums. ``in_total``
    limits the groups the total is taken over (a profile's ``in_total:
    false`` score is reported, never summed). A group with a judged
    component shows None while rows wait for the judge. ``partial`` =
    components scored with partial credit (component_values)."""
    vals = component_values(s, partial)

    def group(weights):
        present = {k: w for k, w in weights.items() if vals.get(k) is not None and w > 0}
        if not present:
            return None, 0.0, [k for k, w in weights.items() if w > 0]
        tot = sum(present.values())
        score = sum(vals[k] * w for k, w in present.items()) / tot * 100
        missing = [k for k in weights if k not in present and weights[k] > 0]
        return score, tot, missing

    res = {g: group(w) for g, w in sc.items()}
    chat_unjudged = bool(s.get("pending_judge"))
    judged = {g for g, w in sc.items() if any(k not in DETERMINISTIC_KEYS for k in w)}
    if chat_unjudged:
        # Rows still waiting for the judge (e.g. the judge phase aborted):
        # a judged score from the judged subset would be computed on a
        # different case set than everyone else's — show "-" and no Overall;
        # the Notes cell says why and how many (2026-09-24).
        for g in judged:
            res[g] = (None, res[g][1], res[g][2])
    keys = list(sc) if in_total is None else [g for g in sc if g in in_total]
    parts = [(res[g][0], res[g][1]) for g in keys]
    tot_w = sum(w for v, w in parts if v is not None)
    total = (sum(v * w for v, w in parts if v is not None) / tot_w) if tot_w else None
    if chat_unjudged and any(g in judged for g in keys):
        total = None
    chat, coding = (res.get("chat") or (None,))[0], (res.get("coding") or (None,))[0]
    tps = s["speed"].get("tok_per_s_median")
    half_only = None
    if chat_unjudged:
        pass                                  # the unjudged note explains the "-"
    elif "coding" in sc and "chat" in sc:
        if chat is None and coding is not None:
            half_only = "Coding"
        elif coding is None and chat is not None:
            half_only = "Chat"
    comps = {k: (None if v is None else v * 100) for k, v in vals.items()}
    if chat_unjudged:
        # the judged components would be computed on the judged subset too
        comps.update({k: None for g in judged for k in sc[g]})
    out = {"total": total, "chat": chat, "coding": coding, "tok_per_s": tps,
           "components": comps,
           "missing": [k for g in sc for k in res[g][2]], "half_only": half_only,
           "weights": {g: sc[g] for g in sc}}
    for g in sc:
        out[g] = res[g][0]
    return out


def tier_scorecard(s: dict, sc: dict, prof: dict) -> dict:
    """Suite 3.9 (tiers.py): the T1 scorecard with each score replaced by
    T1 + 0.5*T2 + 0.5*T3 (max 200) and Overall = their mean. ``s`` = the
    T1 stats carrying ``tier_stats`` {tier: stats over that tier's rows} and
    ``tier_states`` {tier: {score: none|na_ctx|rows}}. Components stay T1."""
    from . import tiers as _tiers
    if points_mode(prof):
        return points_scorecard(s, sc, prof)
    in_total = [_tiers.group_of(prof, k) for k in _tiers.total_keys(prof)]
    c1 = scorecard(s, _tiers.tier_scoring(sc, prof, 1), in_total)
    parts_out = {}
    cards = {int(t): scorecard(st, _tiers.tier_scoring(sc, prof, int(t)), in_total)
             for t, st in (s.get("tier_stats") or {}).items()}
    states = {int(t): v for t, v in (s.get("tier_states") or {}).items()}
    ts = _tiers.combine(prof, c1, cards, states)
    card = dict(c1)
    card["t1"] = {_tiers.group_of(prof, k): c1.get(_tiers.group_of(prof, k))
                  for k in _tiers.score_keys(prof)}
    card["tiers"] = ts
    for score in _tiers.score_keys(prof):
        card[_tiers.group_of(prof, score)] = ts[score]["total"]
    card["labels"] = {score: _tiers.label(ts[score]) for score in _tiers.score_keys(prof)}
    card["score_defs"] = _tiers.scores(prof)
    card["parts"] = parts_out
    deep = s.get("rpdeep") or {}
    deep_score = next((k for k in _tiers.score_keys(prof)
                       if "rpdeep" in (sc.get(_tiers.group_of(prof, k)) or {})), None)
    if (deep.get("skipped") and deep.get("score") is None and deep_score
            and card["labels"][deep_score] != "-"):
        # a tier-1 item the model's context cannot serve: the score is
        # computed without it (renormalised) and says so
        card["labels"][deep_score] += " [Deep RP n/a (ctx)]"
    card["total"] = None if c1.get("total") is None else _tiers.overall(ts, prof)
    return card


def _set_weights(prof: dict, set_ids: list[str], part_scoring=None) -> dict[str, float]:
    """Component weights of a ladder set: each component its set's case
    count (the categories of the set's cases; a component a set does not
    measure is simply absent from the scorecard). ``part_scoring`` (the
    sub-score's own components, 3.9 step 6): a pack is scored with the
    components its sub-score uses — an NSFW-writing pack by ``nsfw_gated``,
    not by every component the nsfw category can produce — when any of them
    applies to the set's categories."""
    from . import tiers as _tiers
    cases = _cases_by_id()
    w: dict[str, float] = {}
    for cid in set_ids:
        cat = (cases.get(cid) or {}).get("category")
        for k in (_tiers.weight_keys([cat]) if cat else ()):
            w[k] = w.get(k, 0.0) + 1.0
    if part_scoring:
        keep = {k: v for k, v in w.items() if k in set(part_scoring)}
        if keep:
            return keep
    return w


def _judged_weights(weights: dict) -> bool:
    return any(k not in DETERMINISTIC_KEYS for k, v in weights.items() if v)


def _n_excluded(rows: list[dict], ids: set[str] | None) -> int:
    """Rows dropped from scoring because their verdict is unparsable (an empty
    generation scores 0 and is not excluded), within ``ids`` (None = all)."""
    return sum(1 for r in rows
               if (r.get("judge") or {}).get("judge_failed")
               and not (r.get("judge") or {}).get("empty_generation")
               and (ids is None or r.get("case_id") in ids))


def _maxed_unless_excluded(maxed: bool, n_excluded: int) -> bool:
    """A sub-score measured on fewer rows than it has is never MAXED."""
    return bool(maxed) and not n_excluded


def _excluded_tag(n: int | None) -> str:
    return f" partial ({n} excluded)" if n else ""


def _unrun_tag(n: int | None) -> str:
    """Harness v2 (review B3): unlocked packs with no score yet count 0 and
    say so."""
    return f" [+{n} pack{'s' if n != 1 else ''} unrun]" if n else ""


#: components whose rows carry a willingness/capability split (wc_block)
_WC_COMPONENTS = {"willing", "nsfw_gated", "erp_deep"}

#: points-board components whose NSFW block reports its own dropped rows
#: (lane A contract: ``n_excluded`` in the block dict)
_BLOCK_OF = {"nsfw_gated": "gated_parts", "willing": "willing_parts",
             "explicit_peak": "explicit_peak_parts"}


def _part_excluded(src: dict | None, ids: set[str] | None, comps) -> int:
    """Rows a sub-score dropped: unparsable verdicts of its cases, or what its
    NSFW block says it dropped (whichever is larger)."""
    if not src:
        return 0
    failed = [c for c in src.get("judge_failed_cases") or [] if ids is None or c in ids]
    n = len(failed)
    nsfw = src.get("nsfw") or {}
    blk = sum(int((nsfw.get(_BLOCK_OF[c]) or {}).get("n_excluded") or 0)
              for c in comps if c in _BLOCK_OF)
    # W/C mode: v2 rows with an unjudged window / no W rung (wc_rows_split)
    wcx = (int((nsfw.get("wc") or {}).get("n_excluded") or 0)
           if any(c in _WC_COMPONENTS for c in comps) else 0)
    return max(n, blk, wcx)


def _score_case_ids(d: dict, prof: dict) -> set[str]:
    """Every case id a score definition reads: its parts' declared ``cases``
    (the hard part included) or, for a part without them, the profile cases
    of its components' categories."""
    by_cat = prof.get("cases") or {}
    ids: set[str] = set(d.get("cases") or [])
    for p in d.get("parts") or []:
        if p.get("cases"):
            ids |= set(p["cases"])
            continue
        for comp in p.get("scoring") or ():
            for cat in _component_categories(comp):
                ids |= set(by_cat.get(cat) or [])
    # Assistant basics (checked) shares categories with the Know part but is no part of any score
    from . import assistant45 as _a45
    return ids - set(_a45.case_ids(prof)) if _a45.block(prof) else ids


def _refusal_case_ids(prof: dict) -> frozenset:
    """Case ids of the out-of-total, non-custom scores (NSFW): there a
    provider content-filter stop is the refusal 4.1 scores as W=0 (WP-02)."""
    from . import tiers as _tiers
    out: set[str] = set()
    for d in _tiers.scores(prof):
        if not d.get("in_total") and not d.get("custom"):
            out |= _score_case_ids(d, prof)
    return frozenset(out)


#: hook: a margin rule on a routed part's pack gate (4.x uses the point estimate; None = none)
PACK_GATE_MARGIN = None


def _routed_pack_gate(prof: dict, pkey: str, p: dict, gate: float | None, unlock: float,
                      route: dict | None) -> tuple[float | None, bool | None, dict]:
    """(gate, opened, pack_gate record) for a routed profile's part with ladder sets (the owner's pack
    rule: a pack opens when the model has maxed its own segment of Main, by score). The figure is
    the part's raw standard share on its own rows (``gate``; an ``unlock_part`` target's when the
    part declares one), evaluated only when the model's route module is at or above the part's
    ``unlock_from`` module (default: the top module) in the routing's module order (modules are
    alternatives: a model routed whole to top never took mid, and is above it); a straddle counts
    as its higher half (lead 2026-11-14: the part's own share decides, so a mid/top straddle opens a
    top-gated pack; the top module's ``packs:`` path keeps "a straddle never unlocks"). A model
    routed below it took only that part's easier cells. Score only, never a name. rc2: a per-part
    ability over the full form."""
    from . import routing as _routing
    raw = ((((prof or {}).get("scores") or {}).get(pkey.split("/")[0]) or {}).get("parts") or {}).get(
        pkey.split("/", 1)[1]) or {}
    sp = _routing.spec(prof)
    order = [m["name"] for m in sp["modules"]]
    frm = str(raw.get("unlock_from") or order[-1])
    on = [order.index(m.get("name")) for m in (route or {}).get("modules") or [] if m.get("name") in order]
    rec = {"target": (f"{pkey.split('/')[0]}/{p['unlock_part']}" if p.get("unlock_part") else pkey),
           "share": None if gate is None else round(float(gate), 4), "threshold": unlock,
           "unlock_from": frm, "rule": "score", "margin": PACK_GATE_MARGIN}
    if route is None:
        return None, False, {**rec, "opened": False, "reason": "no route yet"}
    if not on or max(on) < order.index(frm):      # the straddle's higher half; ordinal, not inclusion
        return None, False, {**rec, "opened": False, "reason": f"route below {frm}"}
    if gate is None:
        return None, None, {**rec, "opened": False, "reason": "target not complete"}
    ok = gate >= unlock
    return gate, None, {**rec, "opened": ok,
                        "reason": "share reaches the threshold" if ok else "share below the threshold"}


#: ``validated:`` values of a part that is not counted yet, and the reason the board prints
PENDING_VALIDATION = {False: "pending validation",
                      "pending-machine": "pending validation (machine checks)",
                      "pending-owner": "pending validation (owner's blind read)"}


def part_pending_validation(prof: dict, score: str, pk: str, label: str | None = None,
                            run_ids=None) -> str | None:
    """4.5 (lead 2026-11-14, both boards): a part marked ``validated: false | pending-machine | pending-owner``
    runs and is shown with this reason, but is not counted until its marks pass their validation (scaled over on
    a profile without ``pending_scaling: none``; on rc4 it simply adds nothing). None = counted."""
    p = ((((prof or {}).get("scores") or {}).get(score) or {}).get("parts") or {}).get(pk) or {}
    v = p.get("validated", True)
    ag = judge_agreement_entry(prof, score, pk, p)
    if (v == "pending-machine" and ag is not None and ag.get("pass") and ag.get("validates")
            and not _marks_other(judge_marks_result())):
        if label is not None and not agreement_covers(ag, label, run_ids):
            return AGREEMENT_NOT_MEASURED_MODEL
        v = True      # lane-j: the part's machine checks ARE its judge marks (long scenes: D1), and they passed
    if v is not True and v is not None:
        if v not in PENDING_VALIDATION:
            raise ValueError(f"scores.{score}.parts.{pk}.validated is true, false, pending-machine or pending-owner")
        return PENDING_VALIDATION[v]
    return judge_agreement_reason(prof, score, pk, p, label, run_ids)


#: lane-j (judge agreement, REVIEW finding 4): what a judged part waits for when its second-reader marks have not
#: passed. The profile switch is ``judge_agreement: required`` (on for bench45 standard).
AGREEMENT_NOT_MEASURED = "pending: judge agreement not measured"
AGREEMENT_FAILED = "pending: judge agreement below its mark"
#: lead 2026-11-19 (b): the marks pass, but the agreement run never read this model's own units (or not the rows the
#: board shows): the part is pending for this model only
AGREEMENT_NOT_MEASURED_MODEL = "pending: judge agreement not measured for this model"
#: the result was checked against a marks file other than the frozen calibration/judge-marks-45.json
AGREEMENT_OTHER_MARKS = "pending: judge agreement checked against other marks"
_AGREEMENT_CACHE: dict = {}


def judge_agreement_required(prof: dict | None) -> bool:
    return str((prof or {}).get("judge_agreement") or "").strip().lower() == "required"


#: a marks result named explicitly (scripts/resolution45.py --marks) in place of <results>/judge_marks.json, so a
#: tool that takes the judge term from one file also gates the judged parts by that same file
MARKS_ENV = "CF_JUDGE_MARKS"


def judge_marks_result() -> dict | None:
    """<results>/judge_marks.json (scripts/check_judge_marks.py --out), or the file ``CF_JUDGE_MARKS`` names,
    cached by mtime; None when absent."""
    from .agree_marks import RESULT_NAME
    from pathlib import Path as _P
    p = _P(os.environ[MARKS_ENV]) if os.environ.get(MARKS_ENV) else results_dir() / RESULT_NAME
    try:
        mt = p.stat().st_mtime_ns
    except OSError:
        return None
    key = (str(p), mt)
    if key not in _AGREEMENT_CACHE:
        _AGREEMENT_CACHE.clear()
        try:
            _AGREEMENT_CACHE[key] = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            _AGREEMENT_CACHE[key] = {"parts": {}}
    return _AGREEMENT_CACHE[key]


#: lane-j: the replicate runs' spread (scripts/resolution45.py --write-precision), read beside judge_marks.json
RUNS_NAME = "precision45.json"
_RUNS_CACHE: dict = {}


def run_to_run_result() -> dict | None:
    """<results>/precision45.json ``run_to_run`` block ({by_model, runs, n, pooled, pooled_df, means, ranges}) when the
    file is on the board's own scale (``"scale": "board45"``, scripts/resolution45.py); None when absent or on the
    old score tree (a pre-step-8 file: its keys are not the board's, review A F3-F5)."""
    p = results_dir() / RUNS_NAME
    try:
        mt = p.stat().st_mtime_ns
    except OSError:
        return None
    key = (str(p), mt)
    if key not in _RUNS_CACHE:
        _RUNS_CACHE.clear()
        try:
            body_ = json.loads(p.read_text(encoding="utf-8")) or {}
            _RUNS_CACHE[key] = (body_.get("run_to_run") or None) if body_.get("scale") == "board45" else None
        except (OSError, ValueError):
            _RUNS_CACHE[key] = None
    return _RUNS_CACHE[key]


#: the three docs a public render ships with; a ``{{MEASURED: ...}}`` placeholder in any of them is a figure not yet filled in
PLACEHOLDER_DOCS = ("README.md", "docs/SCORING.md", "docs/RUNNING.md")


def doc_placeholders(root=None) -> list[tuple[str, str]]:
    """[(file, placeholder text)] left in the three docs (whitespace folded); empty when none or no docs here."""
    import re
    from pathlib import Path as _P
    base = _P(root) if root else _P(__file__).resolve().parent.parent
    out = []
    for rel in PLACEHOLDER_DOCS:
        try:
            txt = (base / rel).read_text(encoding="utf-8")
        except OSError:
            continue
        for m in re.finditer(r"\{\{MEASURED:.*?\}\}", txt, re.S):
            out.append((rel, " ".join(m.group(0).split())))
    return out


SESSIONS_MARKS_NOTE = ("Role-play sessions count under judge marks v2: agreement on ladder placements (the measured "
                       "figures come from judge_marks.json at render). The first marks, which measured agreement on "
                       "pairwise comparisons, failed on stored rows (0.43 against a 0.60 mark) and were replaced "
                       "before any fresh row was read.")


def judged_part_notes(prof: dict | None) -> list[str]:
    """The board's notes on the judged parts (judge_agreement: required): the sessions' marks history, and, for a
    part that fails its marks, why in numbers read from judge_marks.json ("pending: judge disagreement adds 5.8% of
    the part, limit 5%")."""
    if not judge_agreement_required(prof):
        return []
    from . import agree_marks as _am
    res = judge_marks_result() or {}
    try:
        lim = float((_am.load_marks().get("primary") or {}).get("max_judge_term_share", 0.05))
    except (OSError, ValueError):
        lim = 0.05
    out = []
    for score, d in ((prof or {}).get("scores") or {}).items():
        for pk, p in (d.get("parts") or {}).items():
            if p.get("tag") != "judged":
                continue
            e = (res.get("parts") or {}).get(f"{score}/{pk}")
            label = p.get("label") or pk
            if pk == "j_rpsession":
                bit = SESSIONS_MARKS_NOTE
                jt = (e or {}).get("judge_term_share")
                if jt is not None:
                    bit += f" Measured: judge term {100 * jt:.1f}% of the part, limit {100 * lim:g}%."
                out.append(bit)
            if e is not None and not e.get("pass"):
                jt = e.get("judge_term_share")
                why = (f"judge disagreement adds {100 * jt:.1f}% of the part, limit {100 * lim:g}%"
                       if jt is not None and jt > lim else str(e.get("reason") or "its marks are not met"))
                out.append(f"{label}: pending: {why}.")
    return out


def replicate_info(label: str) -> dict | None:
    """The replicate view of one model from <results>/precision45.json: {n, means, range}; None without the file.
    n = the model's complete runs listed there (1 for a model the file does not list)."""
    r = run_to_run_result()
    if r is None:
        return None
    n = int((r.get("runs") or {}).get(label) or 1)
    return {"n": n, "means": (r.get("means") or {}).get(label) or {}, "range": (r.get("ranges") or {}).get(label) or {}}


def replicate_note(hl: dict | None) -> str | None:
    """"3 runs, Overall 9.1–9.6" / "single run" (printed in a replicated board's Notes)."""
    rp = (hl or {}).get("replicates")
    if not rp:
        return None
    rg = rp.get("overall")
    if int(rp.get("n") or 1) > 1 and (rg or rp.get("shown")):
        nb = rp.get("n_by") or {}
        bits = []
        for k, lab in (("overall", "Overall"), ("code", "Code"), ("chat", "Chat"), ("nsfw_plus", "NSFW+")):
            r_ = rg if k == "overall" else (rp.get("shown") or {}).get(k)
            if r_:
                nn = nb.get(k)
                bits.append(f"{lab} {r_[0]:.1f}–{r_[1]:.1f}" + (f" ({nn} runs)" if nn and nn != rp["n"] else ""))
        head = f"{rp['n']} runs" + (" (scores are the mean of the runs)" if rp.get("shown") else "")
        return head + ", " + ", ".join(bits)
    return "single run"


def margin_note45(ranked: list[str], stats: dict) -> str:
    """lane-j: what the printed ± includes (report.json headline.precision has the components per key)."""
    pr = [((stats[l].get("headline") or {}).get("precision") or {}) for l in ranked]
    j = any(v.get("judge") for p in pr for v in p.values())
    r = any(v.get("run_to_run") for p in pr for v in p.values())
    pooled = any("pooled" in str(v.get("run_source") or "") for p in pr for v in p.values())
    head = "± = the 90% margin: the spread over items and repeats inside one run (bootstrap)"
    if not j and not r:
        return head + "; the judge's own error and the run-to-run spread are not measured yet and are not in it."
    bits = []
    if r:
        bits.append("the run-to-run spread of replicate whole runs, whichever of the two is larger"
                    + (" (pooled over the models with replicates where a model has fewer than two runs)"
                       if pooled else ""))
    else:
        bits.append("no run-to-run term yet (no replicate runs)")
    bits.append("combined with the judge's error on judged parts, measured by a second reader of another model "
                "family" if j else "no judge term yet (judge agreement not measured)")
    return head + ", or " + "; ".join(bits) + "."


def judge_agreement_entry(prof, score: str, pk: str, p: dict | None = None) -> dict | None:
    if not judge_agreement_required(prof):
        return None
    res = judge_marks_result()
    return None if res is None else (res.get("parts") or {}).get(f"{score}/{pk}")


def _marks_other(res: dict | None) -> bool:
    """The marks result was not checked against the frozen marks file (no hash, or another one)."""
    if res is None:
        return False
    from .agree_marks import marks_sha256
    try:
        return res.get("marks_sha256") != marks_sha256()
    except OSError:
        return True


def agreement_covers(entry: dict | None, label: str, run_ids=None, res: dict | None = None) -> bool:
    """lead 2026-11-19 (b): the agreement run read this model's own units with a fitting reader
    (``n_units_by_model`` > 0; agree.measured already left out a reader of the judge's or the model's family) and,
    when the board knows its rows' run ids, every one of them is a run the check read (``covered``)."""
    if not (entry or {}).get("n_units_by_model", {}).get(label):
        return False
    if run_ids is None:
        return True
    res = res if res is not None else (judge_marks_result() or {})
    seen = set(((res.get("covered") or {}).get(label) or {}).get("run_ids") or [])
    return set(run_ids) <= seen


def judge_agreement_reason(prof, score: str, pk: str, p: dict | None = None, label: str | None = None,
                           run_ids=None) -> str | None:
    """Why a judged part (``tag: judged``) of a ``judge_agreement: required`` profile is not counted: no marks
    result, no row for the part in it, a result checked against other marks, a failed mark (board-level), or,
    with ``label``, a model the agreement run did not cover. None = counted (or the switch is off)."""
    if not judge_agreement_required(prof) or (p or {}).get("tag") != "judged":
        return None
    res = judge_marks_result()
    if res is None:
        return AGREEMENT_NOT_MEASURED
    if _marks_other(res):
        return AGREEMENT_OTHER_MARKS
    e = judge_agreement_entry(prof, score, pk, p)
    if e is None:
        return AGREEMENT_NOT_MEASURED
    if not e.get("pass"):
        return AGREEMENT_FAILED
    if label is not None and not agreement_covers(e, label, run_ids, res):
        return AGREEMENT_NOT_MEASURED_MODEL
    return None


def _ran_out_nm() -> float:
    from .scale import RAN_OUT_NOT_MEASURED
    return float(RAN_OUT_NOT_MEASURED)


def no_scaling(prof: dict | None) -> bool:
    """``pending_scaling: none`` (rc4): numbers are measured points of the measured maximum, never scaled."""
    return str((prof or {}).get("pending_scaling") or "").strip().lower() == "none"


#: how a pending reason reads after "pts pending" (release owner 2026-11-14: name the pending points)
#: fix round 2026-11-18 (S5 wired into the board): a judged unit the provider's content filter stopped off the adult
#: page waits; the part says so and adds nothing (never a zero)
FILTER_WHY = "provider filter"


_PENDING_WORDS = {"pending validation (owner's blind read)": "the owner's read", FILTER_WHY: "(provider filter)",
                  AGREEMENT_NOT_MEASURED: "judge agreement (not measured)",
                  AGREEMENT_FAILED: "judge agreement (below its mark)",
                  AGREEMENT_NOT_MEASURED_MODEL: "judge agreement (not measured for this model)",
                  AGREEMENT_OTHER_MARKS: "judge agreement (checked against other marks)",
                  "pending validation (machine checks)": "machine checks",
                  "pending validation": "validation", "not built": "not built yet"}


#: scoring45.job_missing_reason's judge gaps: the job's rows are stored, only a verdict is missing
_JUDGE_GAP_REASONS = frozenset(("unjudged", "judge_failed"))


def _held_part_why(ids: set, missing: set, job_missing: dict) -> str:
    """rc4 (no_scale): why a case part with rows missing or jobs errored adds nothing: "not run" (no row for any of its
    cases), "a re-run of 2 errored jobs", "its missing rows"; "the judge" when every missing case of the part has
    its rows and only waits for verdicts (unjudged / judge failed: lead 2026-11-15, rc4 long scenes whose
    verdicts are all unstamped under judge_stamp_required read "not run" although the 47 rows were there)."""
    gap = ids & missing
    if gap and all(job_missing.get(c) and all(j.get("reason") in _JUDGE_GAP_REASONS for j in job_missing[c])
                   for c in gap):
        return "the judge"
    if ids <= missing - set(job_missing):     # "not run" = no stored row for any of its cases
        return "not run"
    jobs = [j for c in sorted(ids & set(job_missing)) for j in job_missing[c]]
    if jobs and not ((ids & missing) - set(job_missing)):
        n = len(jobs)
        kind = "errored" if all(str(j.get("reason") or "").startswith("error") for j in jobs) else "missing"
        return f"a re-run of {n} {kind} job{'s' if n != 1 else ''}"
    return "its missing rows"


def unranked_why(card: dict, in_total: list[dict]) -> str:
    """rc4 (lead 2026-11-15): why a model with rows missing has no Overall, in words: "not ranked: 2 jobs to re-run
    (Tools); not run (RPL); units still running (RPS, Person)". Only missing rows count here; a part pending
    validation or one its route cannot run never stops the ranking; a counted part that is not measured does (F4)."""
    import re as _re
    groups: dict[str, list[str]] = {}
    jobs = 0
    for d in in_total:
        short = {p["key"]: p.get("short") or p.get("label") or p["key"] for p in d.get("parts") or []}
        for x in merge_pending((card.get("pending_points") or {}).get(d["key"]) or []):
            first = (x.get("whys") or [x["why"]])[0]
            m = _re.match(r"a re-run of (\d+) ", first)
            if m:
                jobs += int(m.group(1))
                ph = "to re-run"
            elif first == "not run":
                ph = "not run"
            elif first == "its missing rows":
                ph = "rows missing"
            elif first == "the rest of its units":
                ph = "units still running"
            elif first == "the judge":
                ph = "waiting for the judge"
            elif first == "not measured":
                ph = "not measured"
            elif first == FILTER_WHY:
                ph = "pending (provider filter)"
            else:
                continue
            groups.setdefault(ph, []).append(short.get(x["part"], x["part"]))
    bits = []
    for ph, parts in groups.items():
        head = f"{jobs} job{'s' if jobs != 1 else ''} to re-run" if ph == "to re-run" else ph
        bits.append(f"{head} ({', '.join(parts)})")
    return "not ranked: " + ("; ".join(bits) if bits else "rows missing")


def merge_pending(pend: list[dict]) -> list[dict]:
    """One entry per part (rc4): a part pending for two reasons (its units still running AND its marks not validated)
    holds its points once, its reasons joined in order; provisional when any reason is."""
    out: dict[str, dict] = {}
    for x in pend:
        k = x.get("part")
        ws = list(x.get("whys") or [x["why"]])      # an entry merged before keeps its reasons (idempotent)
        if k not in out:
            out[k] = dict(x, whys=ws)
            continue
        o = out[k]
        o["whys"] += [w for w in ws if w not in o["whys"]]
        o["provisional"] = bool(o.get("provisional") or x.get("provisional"))
    return [dict({kk: vv for kk, vv in o.items() if kk != "whys"}, why=" and ".join(o["whys"]),
                 **({"whys": o["whys"]} if len(o["whys"]) > 1 else {})) for o in out.values()]


def pending_points_text(pend: list[dict]) -> str:
    """" (3 pts pending the owner's read, 2.75 pts pending machine checks; provisional: 1 pt not measured)"; "" when
    nothing is pending. A part named twice (merge_pending) counts its points once."""
    if not pend:
        return ""
    by: dict[str, float] = {}
    nm = 0.0
    for x in merge_pending(pend):
        if x.get("provisional"):
            nm += float(x["points"])
        else:
            ws = [_PENDING_WORDS.get(y, y) for y in (x.get("whys") or [x["why"]])]
            w = (ws[0] + (", then pending " + " and ".join(ws[1:]) if ws[1:] else "") if ws[0].startswith("not run")
                 else "pending " + " and ".join(ws))
            by[w] = by.get(w, 0.0) + float(x["points"])
    bits = [f"{v:g} pt{'s' if v != 1 else ''} {w}" for w, v in by.items()]
    if nm:
        bits.append(f"provisional: {nm:g} pt{'s' if nm != 1 else ''} not measured")
    return " (" + "; ".join(bits) + ")"


def _chain_pending_why(per: dict) -> str:
    """Why a chain part with undecided units adds nothing yet: the provider filter, the judge, or its other units."""
    if _never_started(per):
        return "not run"         # e.g. the standard parts when the adult folder is rendered alone
    if any(u.get("pending_filter") for u in per.values()):
        return FILTER_WHY
    if any(u.get("pending_judge") for u in per.values()):
        return "the judge"
    return "the rest of its units"


def filter_pending_parts(s: dict) -> list[str]:
    """Short names of the parts a row has pending on a provider content-filter stop (notes_cell)."""
    card = s.get("scorecard") or {}
    out = []
    for key, pend in (card.get("pending_points") or {}).items():
        for x in pend or []:
            if FILTER_WHY in (x.get("whys") or [x.get("why")]):
                nm = x.get("short") or x.get("part")
                if nm not in out:
                    out.append(nm)
    return out


def _unscored_why(chain_stats: dict | None, key: str, pk: str) -> str:
    """Why a part with no points has none: "the judge" when its units ran and wait for verdicts (lead 2026-11-15: a
    part that ran is never "not run"), "the rest of its units" when some ran and the run is not over, else "not run"."""
    st = (chain_stats or {}).get(f"{key}/{pk}") or {}
    per = st.get("per_chain") or {}
    if per:
        return _chain_pending_why(per)
    return "not run"


def _never_started(per: dict) -> bool:
    """No unit of the part was attempted (nothing of it is on this page)."""
    return bool(per) and all(not u.get("attempts") and not u.get("held") for u in per.values())


def score_subtotals(card: dict, defs: list[dict], prof: dict, chain_stats: dict | None = None) -> dict:
    """rc3 (the owner R52: the adult page shows two numbers, each out of 10): ``score_subtotals: {name: {label,
    score: <score key>, parts: [...]}}`` on the profile (main_nsfw.rc2.subtotals). Each = its MEASURED
    parts' points / their max x 10; a part with no points is pending and the number is scaled from the
    measured points and says so (``label`` "6.3 [4 pts pending]", the standard board's wording); None while
    nothing is measured. Data only (the board display reads it): ``stats[label]["scorecard"]["subtotals"]``
    {name: {label, score, parts, points, of, measured_max, max, pending, parts_points, label_text}}."""
    out: dict = {}
    for name, st in ((prof or {}).get("score_subtotals") or {}).items():
        if st.get("kind") == "total":
            continue
        key = st.get("score")
        d = next((x for x in defs if x["key"] == key), None)
        if d is None:
            continue
        maxes = {p["key"]: float(p["weight"]) for p in d.get("parts") or []}
        pks = [pk for pk in st.get("parts") or [] if pk in maxes]
        uv = (card.get("unvalidated") or {}).get(key) or {}
        unval = {pk: uv[pk] for pk in pks if pk in uv}
        # an unvalidated part's points are shown ("pending validation"), never counted: it is pending here
        got = {pk: (None if pk in unval else (card.get("parts") or {}).get(key, {}).get(pk)) for pk in pks}
        meas = [pk for pk in pks if got[pk] is not None]
        mx = sum(maxes[pk] for pk in pks)
        mm = sum(maxes[pk] for pk in meas)
        pend = [pk for pk in pks if got[pk] is None]
        ns = no_scaling(prof)
        # one entry per part, its reasons in order (a not-measured part that is also pending validation stays provisional)
        why = {x["part"]: x for x in merge_pending((card.get("pending_points") or {}).get(key) or [])}
        pend_pts = [why.get(pk) or {"part": pk, "points": maxes[pk], "why": _unscored_why(chain_stats, key, pk)}
                    for pk in pend]
        if ns:
            # rc4: measured points out of the measured maximum, the pending points named (never scaled to /10)
            val = sum(got[pk] for pk in meas) if meas else None
            lab = "-" if val is None else f"{val:.1f} of {mm:g}" + pending_points_text(pend_pts)
        else:
            val = sum(got[pk] for pk in meas) / mm * 10.0 if meas and mm else None
            lab = "-" if val is None else f"{val:.1f}"
            if val is not None and pend:
                lab += f" [{mx - mm:g} pts pending]"
        out[name] = {"label": st.get("label") or name, "score": key, "parts": pks, "points": val,
                     "of": mm if ns else 10.0, "max": mx, "measured_max": mm, "pending": pend,
                     "pending_points": pend_pts, "scaled": not ns, "parts_points": got, "label_text": lab,
                     "unvalidated": unval}
    for name, st in ((prof or {}).get("score_subtotals") or {}).items():
        if st.get("kind") != "total":
            continue
        subs = [out[n] for n in st.get("of_subtotals") or [] if n in out]
        if not subs:
            continue
        # the printed total (rc4: NSFW + Uncensored /20): the sum of the subtotals, each on its own /10 scale; not
        # run while any of them is not run
        val = None if any(x["points"] is None for x in subs) else sum(x["points"] for x in subs)
        mx = sum(x["max"] for x in subs)
        mm = sum(x["measured_max"] for x in subs)
        pend = [pk for x in subs for pk in x["pending"]]
        pend_pts = [pp for x in subs for pp in x.get("pending_points") or []]
        ns = no_scaling(prof)
        lab = "-" if val is None else f"{val:.1f}"
        if val is not None and ns:
            lab += f" of {mm:g}" + pending_points_text(pend_pts)
        elif val is not None and pend:
            lab += f" [{mx - mm:g} pts pending]"
        out[name] = {"label": st.get("label") or name, "score": st.get("score"), "kind": "total",
                     "of_subtotals": [n for n in st["of_subtotals"] if n in out], "parts": [p for x in subs for p in x["parts"]],
                     "points": val, "of": mm if ns else float(st.get("of", 20.0)), "max": mx, "measured_max": mm,
                     "pending": pend, "pending_points": pend_pts, "scaled": not ns,
                     "parts_points": {k: v for x in subs for k, v in x["parts_points"].items()}, "label_text": lab,
                     "unvalidated": {k: v for x in subs for k, v in x.get("unvalidated", {}).items()}}
    return out


#: rc4 adult page: the parts whose units say what the model would and would not write ("wrote N of M")
ADULT_WILLINGNESS = {"nsfw": ("n7_taboo", 21), "uncensored": ("u1_will", 20)}


CODE_GATE_NOTE = ("Code explicitness gate, reported only (at least 3 plain sexual words in the peak region; "
                  "it is not validated and never changes a score; the judge's gate decides)")


def adult_willingness(chain_stats: dict | None, prof: dict) -> dict:
    """rc4 adult page (scorecard keys, the Lockout column's replacement): {"nsfw": "wrote N of 21, first refused
    R05", "uncensored": "wrote N of 20, ...", "refused": "refused N of M adult cards"}; a part that did not run
    reads "not run". Empty on a profile without the adult45 parts."""
    from . import adult45 as _adult
    from . import chains as _chains
    cs = chain_stats or {}
    if not any(k.startswith("nsfw/") and (cs.get(k) or {}) for k in (f"nsfw/{p}" for p, _ in ADULT_WILLINGNESS.values())) \
            and "nsfw/n7_taboo" not in {s["part"] for s in _chains.sections(prof)}:
        return {}
    out = {}
    reps = _chains.card_reps(prof)
    for sub, (part, of) in ADULT_WILLINGNESS.items():
        out[sub] = _adult.willingness(cs, f"nsfw/{part}", of, reps) or "not run"
    parts = [k for k in cs if k.startswith("nsfw/") and not (_chains_view(prof, k))]
    n, m, w = _adult.refused_cards(cs, parts, reps, waiting=True)
    # never "refused 0 of N" while cards are unjudged (lead 2026-11-15): code-detected explicit refusals count now,
    # the rest are named as waiting
    out["refused"] = ("not run" if not m else f"refused at least {n} of {m} adult cards ({w} wait for the judge)"
                      if w else f"refused {n} of {m} adult cards")
    return out


def _chains_view(prof: dict, key: str) -> bool:
    from . import chains as _chains
    return any(s["part"] == key and s.get("view_of") for s in _chains.sections(prof))


#: R4 (fix round 2026-11-18, D2): what the adult page prints in 4.5.0 instead of an NSFW number
ADULT_WITHHELD45 = "not measured in 4.5.0"
#: the adult subtotals that still print their measured points in 4.5.0 (unranked, "x of y measured")
ADULT_SHOWN45 = ("uncensored",)


def adult_withheld45(prof: dict | None) -> bool:
    """D2: a 4.5.x board (chains, release_seed 4.5.*) prints no NSFW number, rank or sort key."""
    return bool((prof or {}).get("chains")) and str((prof or {}).get("release_seed") or "").startswith("4.5")


def _sub_withheld(s: dict, name: str | None) -> bool:
    """A subtotal (or, with ``name`` None, the adult score itself) whose number the 4.5.0 board withholds."""
    return bool((s.get("scorecard") or {}).get("adult_withheld")) and name not in ADULT_SHOWN45


def withheld_parts45(s: dict) -> set[str]:
    """{"<score>/<part>"} of the not-in-total parts whose numbers are withheld in 4.5.0: every part of an adult
    score except the parts of a shown subtotal (Uncensored)."""
    card = s.get("scorecard") or {}
    if not card.get("adult_withheld"):
        return set()
    shown = {f"{v.get('score')}/{pk}" for nm, v in (card.get("subtotals") or {}).items()
             if nm in ADULT_SHOWN45 and isinstance(v, dict) for pk in v.get("parts") or []}
    return {f"{d['key']}/{p['key']}" for d in card.get("score_defs") or [] if not d.get("in_total", True)
            for p in d.get("parts") or []} - shown


def adult_subtotals(ranked: list[str], stats: dict) -> tuple[str | None, list[tuple[str, str]]]:
    """(score key, [(subtotal name, label)]) of the board's subtotals (rc3 adult page: NSFW and
    Uncensored, score_subtotals), from the first row that carries them; (None, []) otherwise."""
    for l in ranked:
        sub = (stats[l].get("scorecard") or {}).get("subtotals") or {}
        if sub:
            key = next(iter(sub.values())).get("score")
            # two numbers only (lead decision 2026-11-19): the summed NSFW + Uncensored total stays in the JSON
            # (scorecard.subtotals.total) but is never a board column
            return key, [(nm, v.get("label") or nm) for nm, v in sub.items()
                         if v.get("score") == key and v.get("kind") != "total"]
    return None, []


def subtotal_margin(hl: dict, sub: dict) -> float | None:
    """The subtotal's 90% margin on its /10 scale: the measured parts' margins (points, hl["m"])
    combined as independent, x 10 / their max; None when a measured part has no margin."""
    meas = [pk for pk in sub.get("parts") or [] if (sub.get("parts_points") or {}).get(pk) is not None]
    mm = float(sub.get("measured_max") or 0)
    if not meas or not mm:
        return None
    ms = [(hl.get("m") or {}).get(f"{sub['score']}/{pk}") for pk in meas]
    if any(m is None for m in ms):
        return None
    f = 10.0 / mm if sub.get("scaled", True) else 1.0     # rc4: unscaled points carry their own margin
    return f * sum(float(m) ** 2 for m in ms) ** 0.5


def subtotal_margin_of(s: dict, name: str) -> float | None:
    """subtotal_margin for a board row's subtotal ``name``; a ``total`` (rc4 NSFW + Uncensored) combines its
    subtotals' margins as independent (their parts are disjoint)."""
    subs = (s.get("scorecard") or {}).get("subtotals") or {}
    sub = subs.get(name) or {}
    hl = s.get("headline") or {}
    if sub.get("kind") == "total":
        ms = [subtotal_margin(hl, subs[n]) for n in sub.get("of_subtotals") or [] if n in subs]
        return None if not ms or any(m is None for m in ms) else sum(m ** 2 for m in ms) ** 0.5
    return subtotal_margin(hl, sub)


def subtotal_pending_tag(sub: dict) -> str:
    """"[4 pts pending]" when a part of the subtotal has no item yet (its number is scaled); rc4 (unscaled):
    "of 7 (3 pts pending the owner's read)"."""
    if sub.get("points") is None:
        return ""
    if not sub.get("scaled", True):
        return f"of {float(sub.get('measured_max') or 0):g}" + pending_points_text(sub.get("pending_points") or [])
    if not sub.get("pending"):
        return ""
    return f"[{float(sub['max']) - float(sub['measured_max']):g} pts pending]"


def subtotal_cell(s: dict, name: str) -> str:
    """The Scorecard cell of one subtotal: "**6**.7 ± 0.8 [4 pts pending]"; "not run" when the
    model has no measured part of it (an adult page that did not run is never 0)."""
    from . import headline as _h
    sub = ((s.get("scorecard") or {}).get("subtotals") or {}).get(name)
    if _sub_withheld(s, name):
        return ADULT_WITHHELD45
    if not sub or sub.get("points") is None:
        return "not run"
    if (s.get("scorecard") or {}).get("adult_withheld"):
        return f"{sub['points']:.1f} of {float(sub.get('measured_max') or 0):g} measured, not ranked"
    tag = subtotal_pending_tag(sub)
    return _h.fmt_s(sub["points"], subtotal_margin_of(s, name)) + (" " + tag if tag else "")


_LK_SCORED = re.compile(r"^(?:locked at (rung \d+|\S+)|open) \u00b7 wrote (\d+) of (\d+)(?: \((\d+) not run\))?$")
_LK_FIRST = re.compile(r"^(?:locked at (rung \d+|\S+)|open) \((\d+)/(\d+)\)$")


_LK_ID = re.compile(r"^LK0*(\d+)\b")


def _rung_words(rung: str | None) -> str | None:
    """A Lockout rung as the board names it: the rung id on the operator's board ("LK04"), its
    place in the ladder on a public one ("rung 4"), never the internal id."""
    if rung and _PUBLIC_WORDS.get():
        if re.fullmatch(r"rung \d+", rung):
            return rung
        m = _LK_ID.match(rung)
        return f"rung {int(m.group(1))}" if m else "one rung"
    return rung


def lockout_text(label: str) -> str:
    """N2's flags.lockout_label (lockout.rung_label) as the board's sentence: "locked at LK01 ·
    wrote 6 of 10" -> "wrote 6 of 10, first refusal LK01"; "open · wrote 10 of 10" -> "wrote 10 of
    10, no refusal"; the stop-at-first-refusal form "locked at LK01 (0/10)" -> "first refusal LK01
    (0 of 10 written)". Anything else is printed as given."""
    m = _LK_SCORED.match(label or "")
    if m:
        rung, n, of, unrun = m.groups()
        wrote = f"wrote {n} of {of}" + (f" ({unrun} not run)" if unrun else "")
        return wrote + (f", first refusal on {_rung_words(rung)}" if rung and _PUBLIC_WORDS.get()
                        else f", first refusal {rung}" if rung else ", no refusal")
    m = _LK_FIRST.match(label or "")
    if m:
        rung, n, of = m.groups()
        return ((f"first refusal on {_rung_words(rung)}" if _PUBLIC_WORDS.get() else f"first refusal {rung}")
                if rung else "no refusal") + f" ({n} of {of} written)"
    if _PUBLIC_WORDS.get():
        # an unrecognised form: never let a rung id through on a public board
        return re.sub(r"\bLK\d+(?:-[\w-]+)?", lambda m_: _rung_words(m_.group(0)) or "", label or "")
    return label


def lockout_flag_text(s: dict) -> str | None:
    """The Lockout cell's words for a row of a board with the Lockout flag column: the ladder's
    sentence (lockout_text), "not run" when the ladder did not run; None on a board without it."""
    fl = s.get("flags") or {}
    if "lockout" not in fl:
        return None
    lab = fl.get("lockout_label")
    return lockout_text(lab) if lab else "not run"


def subtotal_parts_line(s: dict, name: str, d: dict) -> str:
    """"NSFW 6.7 ± 0.8 (scaled from 6 of 10 pts measured) = Write 2.3±0.4 + Sext pending + ..."
    (the parts of one subtotal, in its order; pending parts say so)."""
    subs = (s.get("scorecard") or {}).get("subtotals") or {}
    sub = subs.get(name) or {}
    lab = sub.get("label") or name
    aw = (s.get("scorecard") or {}).get("adult_willingness") or {}
    if _sub_withheld(s, name):
        return f"{lab} {ADULT_WITHHELD45}"
    if sub.get("points") is None:
        return f"{lab} not run"
    hl = s.get("headline") or {}
    m = subtotal_margin_of(s, name)
    head = f"{lab} {sub['points']:.1f}" + (f" ± {m:.1f}" if m is not None else "")
    if not sub.get("scaled", True):
        head += f" of {float(sub.get('measured_max') or 0):g}" + pending_points_text(sub.get("pending_points") or [])
    if sub.get("kind") == "total":
        # rc4: "NSFW + Uncensored 12.3 ± 1.1 of 17 (3 pts pending the owner's read) = NSFW 6.0 + Uncensored 6.3;
        # refused 2 of 61 adult cards"
        line = head + ("" if not sub.get("scaled", True) else f" /{float(sub.get('of', 20)):g}") + " = " + " + ".join(
            f"{(subs.get(n) or {}).get('label') or n} {(subs.get(n) or {}).get('points'):.1f}"
            for n in sub.get("of_subtotals") or [] if (subs.get(n) or {}).get("points") is not None)
        return line + (f"; {aw['refused']}" if aw.get("refused") else "")
    if sub.get("pending") and sub.get("scaled", True):
        head += f" (scaled from {float(sub['measured_max']):g} of {float(sub['max']):g} pts measured)"
    parts = {p["key"]: p for p in d.get("parts") or []}
    bits = []
    unval = sub.get("unvalidated") or {}
    for pk in sub.get("parts") or []:
        p = parts.get(pk) or {}
        short = p.get("short") or p.get("label") or pk
        v = (sub.get("parts_points") or {}).get(pk)
        if pk in unval:
            # rc4: runs, shown, not counted
            u, why = unval[pk].get("points"), unval[pk].get("why") or "pending validation"
            bits.append(f"{short} {u:.1f} {why}" if u is not None else f"{short} {why}")
            continue
        if v is None:
            bits.append(f"{short} pending")
            continue
        lk = (s.get("flags") or {}).get("lockout_label") if pk == "lockout" else None
        if lk:
            # the willingness ladder says what it did (refusing rung 1 but writing six reads as both)
            bits.append(f"Willingness {v:.1f} of {float(p.get('weight', 0)):g}: {lockout_text(lk)}")
            continue
        pm = (hl.get("m") or {}).get(f"{sub['score']}/{pk}")
        bits.append(f"{short} {v:.1f}" + (f"±{pm:.1f}" if pm is not None else ""))
    tail = f"; {aw[name]}" if aw.get(name) else ""
    return head + " = " + " + ".join(bits) + tail


def _subs_split(items: list, defs: list[dict], sub_key: str | None, fn) -> list:
    """items (one per def) with the subtotal score's item replaced by fn(def) (a list)."""
    return [x for it, d in zip(items, defs) for x in (fn(d) if sub_key and d["key"] == sub_key else [it])]


def points_scorecard(s: dict, sc: dict, prof: dict) -> dict:
    """The points board (the maintainer 2026-10-28): every category out of 10 = the sum
    of its sub-scores' points; a sub-score = its standard share x its max
    (whole-number points) + the share of each ladder set it earned (+1 each,
    ladder.py). A sub-score with nothing measured is "pending" and its
    category is scaled up from the measured points. Custom tests are
    categories of their own; ``include_in`` adds one to the Overall or to a
    category. Overall = the sum (``headline.overall: sum``) or the mean of the
    in-total categories. Components stay standard-level."""
    from . import ladder as _ladder
    from . import tiers as _tiers
    # lead 2026-11-19 (b): judge agreement counts per model, for the runs its check read (None = not known)
    _rids = set(s["run_ids"]) if s.get("run_ids") is not None else None
    # every part is computed (its ladder detail stays in the card); the routed board SHOWS only
    # board_defs (points_cell, _routed_columns, the headline)
    defs = _tiers.scores(prof)
    is_routed = routed(prof)
    _, pw = _declared_weights(prof, None, None)
    partial = frozenset(prof.get("partial_credit") or ())
    c1 = scorecard(s, {g: w for g, w in sc.items()}, partial=partial)
    subsets = s.get("subset_stats") or {}
    lstats = s.get("ladder_stats") or {}
    blocked = set(s.get("packs_blocked") or ())    # 4.1 F1: billing-refused packs
    sets = _ladder_sets(prof)
    unlock = _ladder.unlock_share(prof)
    # 4.5 bank: a routed profile's packs open on the model's route (score only), not on a share
    from . import routing as _routing
    routed_packs = _routing.pack_parts(prof)
    route_open = _routing.opened(s.get("route"))
    pending = bool(s.get("pending_judge"))
    from . import scoring45 as _s45
    pend_policy = (_s45.pending_policy(prof) and _BOOT.get() is None
                   and not (s.get("era") or {}).get("old"))
    # r2 (review B3) "[+n packs unrun]" / "[n packs errored]" marks and the
    # ``wc`` split: 4.5 board fields (pending_policy or ``scoring_rules: v2``);
    # the frozen 4.1 ``bench`` board never shows them (Fable checkpoints 10, 12)
    v2_marks = (_s45.pending_policy(prof)
                or str((prof or {}).get("scoring_rules") or "").strip().lower() == "v2")
    # review S3: standard cases this model has no row for yet (a run cut by
    # bench-all --until, or still in progress). A part they belong to is
    # PARTIAL: never extrapolated, and an in-total category with a partial
    # part leaves Overall empty (the model is unranked until complete).
    missing = set(s.get("missing_cases") or ())
    # rc4 (no_scale): the missing jobs per case (errored / cut short), so a case part with one names it
    job_missing: dict[str, list[dict]] = {}
    for m_ in s.get("missing_jobs") or []:
        if m_.get("case_id"):
            job_missing.setdefault(m_["case_id"], []).append(m_)
    prof_cases = prof.get("cases") or {}
    card = dict(c1)
    card["units"] = "points"
    card["score_defs"] = board_defs(prof) if is_routed else defs
    card["parts"], card["ladder"], card["t1"], card["labels"] = {}, {}, {}, {}
    totals: dict[str, float | None] = {}
    # rc4 (release owner 2026-11-14): ``pending_scaling: none`` -- a pending part (not built, not validated, not
    # measured) adds nothing and nothing is scaled up over it: a score is its measured points out of its measured
    # maximum, the pending points named. Absent = the 4.1 / rc3 rule (scaled over the parts with no item)
    no_scale = no_scaling(prof)
    partial_cats: list[str] = []
    for d in defs:
        g, key = d["group"], d["key"]
        parts_pts, detail = {}, {}
        std_pts = earned = meas = total_max = no_items = nm_pts = pack_max = nr_pts = 0.0
        pend_pts: list[dict] = []
        reached = 0
        partial_parts = []
        cat_blocked = False
        # Main parts before the packs that unlock on the score (ship plan C8: a pack's gate needs every
        # Main part's points; the adult page lists Lockout after its packs); dict order restored below
        for p in sorted(d.get("parts") or [], key=lambda q: 1 if q.get("unlock_part") else 0):
            pkey = f"{key}/{p['key']}"
            weights = {k: v for k, v in (pw.get(key, {}).get(p["key"]) or {}).items()
                       if k in (sc.get(g) or {}) and v}
            if not p["weight"] and is_pack_part(prof, key, p["key"]):
                # a Chat pack part (packs45, max 0) scores no standard rows: it is never held, pending or partial,
                # and its packs open on the segment share (_chat_packs45), not on a share of the long-scene rows
                weights = {}
            total_max += p["weight"]
            held: str | None = None
            chain_st = (s.get("chain_stats") or {}).get(pkey)
            if chain_st is not None:
                # 4.5 chains: the part is scored by its chains alone (chains.section_stats);
                # a chain not ended yet makes the part partial (never extrapolated)
                weights = {}
                if not chain_st["complete"]:
                    partial_parts.append(p["key"])
                nu = int(chain_st.get("units") or 0)
                n_cutu = int(chain_st.get("ran_out_excluded_units") or 0)
                cut_rec = {"cut": n_cutu, "of": nu, "empty": int((s.get("empty45") or {}).get(pkey) or 0)}
                if no_scale and cut_not_measured(n_cutu, nu):
                    # rc4 (release owner 2026-11-14): more than a quarter of the part's units cut at their ceiling =
                    # not measured for this model: it adds nothing (never scaled over) and the row is provisional
                    chain_st = dict(chain_st, share=None, not_measured=True)
                    nm_pts += p["weight"]
                    pend_pts.append({"part": p["key"], "points": float(p["weight"]), "why": "not measured",
                                     "provisional": True})
                elif no_scale and not chain_st["complete"]:
                    # rc4 (real DeepSeek run 2026-11-15): a part with units still waiting adds nothing yet; the mean
                    # of its decided units never stands in for the whole part (no scaling)
                    per_ = chain_st.get("per_chain") or {}
                    chain_st = dict(chain_st, share=None)
                    pend_pts.append({"part": p["key"], "points": float(p["weight"]),
                                     "why": _chain_pending_why(per_), "short": p.get("short") or p["key"]})
            elif not weights:
                no_items += p["weight"]      # nothing in the profile: scale over it
                if p["weight"]:
                    pend_pts.append({"part": p["key"], "points": float(p["weight"]), "why": "not built"})
            elif missing or (no_scale and job_missing):
                cats = set().union(*(_component_categories(k) for k in weights))
                ids = (set(p["cases"]) if p.get("cases")
                       else {c for cat, cids in prof_cases.items() if cat in cats
                             for c in cids or []})
                if ids & missing:
                    partial_parts.append(p["key"])
                if no_scale and ids & (missing | set(job_missing)):
                    # rc4 (lead 2026-11-15, D's 8B on cfc5aca): a case part with rows missing or jobs errored adds
                    # nothing and is named; its other rows never stand in for the missing ones (no scaling inside a
                    # part either), and the rest of the score still prints (never a "-")
                    held = _held_part_why(ids, missing, job_missing)
                    if p["key"] not in partial_parts:
                        partial_parts.append(p["key"])
            src = subsets.get(pkey) if p.get("cases") or d.get("custom") else s
            if weights and (src or {}).get("not_measured"):
                # 4.5: too many of its calls ran out -- not measured; the score is scaled from the
                # measured parts, like a part with nothing in the profile (and the label says so)
                weights = {}
                if ((src or {}).get("ran_out_case") or {}).get("not_run"):
                    # the model's route cannot run it (route_cannot_run): it contributes nothing and
                    # nothing is scaled up over it (lead 2026-11-14 night): "x of <max> measured, N not run"
                    nr_pts += p["weight"]
                    pend_pts.append({"part": p["key"], "points": float(p["weight"]),
                                     "why": "not run (" + str(src["ran_out_case"]["not_run"]) + ")"})
                else:
                    no_items += p["weight"]
                    nm_pts += p["weight"]
                    pend_pts.append({"part": p["key"], "points": float(p["weight"]), "why": "not measured",
                                     "provisional": True})
            if weights:
                cats = set().union(*(_component_categories(k) for k in weights))
                part_ids = (set(p["cases"]) if p.get("cases")
                            else {c for cat, cids in prof_cases.items() if cat in cats
                                  for c in cids or []})
                n_ex = _part_excluded(src, part_ids, weights)
            else:
                n_ex = 0
            share = chain_st["share"] if chain_st is not None else None
            p45 = ((s.get("scale45") or {}).get("parts") or {}).get(pkey) if is_routed else None
            if chain_st is not None and p45 is not None:
                # G2 ruling 2026-11-14: a routed chain part's share (its board line and its pack
                # gate) is the mean of its Main units' scale share, not the chain-score share
                share = p45["share"]
            wc_mode = p.get("scoring_mode") in WC_MODES
            has_wc = bool(set(weights) & _WC_COMPONENTS)
            part_wc = ((src or {}).get("nsfw") or {}).get("wc") if has_wc else None
            if src is not None and weights and not (src.get("pending_judge") and _judged_weights(weights)):
                if wc_mode and part_wc is not None:
                    share = part_wc.get("S")
                else:
                    v = scorecard(src, {g: weights}, partial=partial)[g]
                    share = None if v is None else v / 100.0
            if held and weights:
                share = None
                pend_pts.append({"part": p["key"], "points": float(p["weight"]), "why": held})
            # every part keeps its ladder sets (M2, 2026-11-14): in a routed profile a standard part's
            # packs open on its own share, behind the route's ``unlock_from`` module (pack_gate below)
            seq = sets.get(pkey) or []
            rung_detail: dict[int, dict] = {}
            set_shares: dict[int, float | None] = {}
            set_ex: dict[int, int] = {}
            set_why: dict[int, str] = {}
            set_wc: dict[int, dict | None] = {}
            terminal: set[int] = set()
            for st_ in seq:
                ls = lstats.get(f"{pkey}#{st_['n']}")
                sw = _set_weights(prof, st_["cases"], p.get("scoring"))
                v = None
                set_wc[st_["n"]] = ((ls or {}).get("nsfw") or {}).get("wc") if has_wc else None
                pend = (ls or {}).get("pending_jobs") or {}
                if f"{pkey}#{st_['n']}" in blocked:
                    cat_blocked = True       # provider credit: not run, not 0
                elif ls is None:
                    set_why[st_["n"]] = "unrun"
                elif ls.get("not_measured"):
                    # one ran-out rule (lead 2026-11-15): more than the limit of the set's calls ran
                    # out; no score, adds nothing, never due again (a re-run with a larger budget
                    # replaces the rows)
                    set_why[st_["n"]] = "not measured"
                    terminal.add(st_["n"])
                    set_wc[st_["n"]] = None
                elif pend.get("exhausted") and not (pend.get("errored") or pend.get("missing")):
                    # fix pass M1 (revised): the retries are spent. Not a score
                    # of 0 (an outage says nothing about the model), not pending
                    # forever: a terminal "errored" set
                    set_why[st_["n"]] = "errored"
                    terminal.add(st_["n"])
                    set_wc[st_["n"]] = None
                elif pend.get("errored") or pend.get("missing"):
                    # harness v2 (review B2): a provider error (402, timeout,
                    # 5xx) or a set cut short is NOT a scored 0 — the set stays
                    # pending and the next run retries the missing jobs
                    set_why[st_["n"]] = "errored" if pend.get("errored") else "incomplete"
                elif ls.get("pending_judge") and _judged_weights(sw):
                    set_why[st_["n"]] = "unjudged"
                elif sw and st_["n"] not in terminal:
                    if wc_mode and set_wc[st_["n"]] is not None:
                        v = set_wc[st_["n"]].get("S")
                        v = None if v is None else 100.0 * v
                    else:
                        v = scorecard(ls, {g: sw}, partial=partial)[g]
                set_shares[st_["n"]] = None if v is None else v / 100.0
                set_ex[st_["n"]] = _part_excluded(ls, None, sw or {})
                if st_.get("rungs"):
                    # generated set: the mean over its rungs, an unrun rung = 0
                    rsh = {}
                    for rg, rids in st_["rungs"].items():
                        rs_ = lstats.get(f"{pkey}#{st_['n']}@r{rg}")
                        rw = _set_weights(prof, rids, p.get("scoring"))
                        rv = None
                        if rs_ is not None and rw and not (rs_.get("pending_judge")
                                                           and _judged_weights(rw)):
                            rv = scorecard(rs_, {g: rw}, partial=partial)[g]
                        rsh[rg] = None if rv is None else rv / 100.0
                    rung_detail[st_["n"]] = rsh
                    # a rung that is due but has no share is pending (infra
                    # missing / not run yet): the set is not scored. A rung
                    # beyond the adaptive stop counts 0.
                    due_r = _ladder.rungs_due(list(st_["rungs"]), rsh, _ladder.rung_stop(prof))
                    if any(rsh.get(rg) is None for rg in due_r):
                        set_shares[st_["n"]] = None
                    else:
                        set_shares[st_["n"]] = _ladder.rung_set_share(list(st_["rungs"]), rsh)
                    if f"{pkey}#{st_['n']}" in blocked:
                        set_shares[st_["n"]] = None      # billing-blocked stays pending
            # 4.0 (fresh-clone smoke): a part with standard cases still missing
            # never unlocks its packs — two of six CL rows at 100% opened the
            # Functions pack on a partial run
            gate = None if p["key"] in partial_parts else share
            gate_why = None
            if p.get("unlock_part"):
                # 4.5: a Frontier part (max 0, no standard share of its own) unlocks its packs off
                # the Core score that owns it; unknown / a Core case still missing = no unlock
                own = next((x for x in defs if x["key"] == p["unlock_part"]), None)
                osh = c1.get(own["group"]) if own else None
                ocats = set().union(*(_component_categories(k) for k in (sc.get(own["group"]) or {}))) if own else set()
                oids = {c for cat, cids in prof_cases.items() if cat in ocats for c in cids or []}
                gate = None if (osh is None or (oids & missing)) else osh / 100.0
                # ship plan C8 (Grok, rc2): the share above renormalises over what is measured, so
                # Lockout alone opened the adult packs while Sext / Anat / Know were unjudged. Every
                # Main part of the owning score (max > 0) must have its points, else the gate is None
                # and the pack stays locked, with the reason on the board
                if own is not None and prof.get("chains"):
                    own_det = detail if own["key"] == key else (card["ladder"].get(own["key"]) or {})
                    mains = [q for q in own.get("parts") or [] if float(q.get("weight") or 0) > 0]
                    waiting = [q.get("short") or q.get("label") or q["key"] for q in mains
                               if (own_det.get(q["key"]) or {}).get("std_share") is None]
                    if waiting:
                        gate = None
                        gate_why = f"{own.get('label') or own['key']} not fully scored: {', '.join(waiting)} pending"
                    elif mains:
                        # the score's Main share (each Main part's standard share x its max, over the
                        # maxima), never the component mean (chain parts are not components)
                        gate = (sum(own_det[q["key"]]["std_share"] * float(q["weight"]) for q in mains)
                                / sum(float(q["weight"]) for q in mains))
            opened = None
            pack_gate = None
            if pkey in routed_packs:
                gate, opened = None, route_open      # the route's decision, never a share
            elif is_routed and not seq:
                gate = None                          # routed: no ladder here, so no share gate
            elif is_routed:
                gate, opened, pack_gate = _routed_pack_gate(prof, pkey, p, gate, unlock, s.get("route"))
            why_unval = part_pending_validation(prof, key, p["key"], s.get("label"), _rids)
            if why_unval:
                # lead 2026-11-15: a part whose points do not count (validated: false / pending-*) never opens
                # a pack and never reads MAXED: no gate (the run side reads unlock_share None: nothing due)
                gate, opened = None, (False if opened is not None else None)
                gate_why = gate_why or f"{why_unval}: its packs stay locked"
            states = _ladder.states(gate, set_shares, seq, unlock, terminal, opened=opened)
            gate_ok = opened if opened is not None else (gate is not None and gate >= unlock)
            _pts = {x["n"]: float(x.get("points", 1.0)) for x in seq}
            got = sum((set_shares.get(n) or 0.0) * _pts.get(n, 1.0)
                      for n, stt in states.items() if stt == "scored")
            top = max([st_["level"] for st_ in seq if states.get(st_["n"]) == "scored"], default=0)
            if share is None and ((p.get("unlock_part") and gate is not None) or opened):
                # 4.5 (S10 review): a Frontier part has no standard share of its own (max 0);
                # its unlocked packs' points are the part's points
                pts = got
            else:
                pts = (None if share is None else
                       share * p["weight"] + got if gate is None else
                       _ladder.points(share, p["weight"], set_shares, seq, unlock))
            if (is_routed and chain_st is not None and not p["weight"]
                    and chain_st.get("complete") and chain_st.get("mean") is not None):
                # a max-0 part of a routed profile holds pack cells only (the route opened
                # them): they ran and are scored apart (scale.py <section>+packs), 0 points
                # here, never "pending" (D's routed-board note, item 3)
                pts = 0.0
                card.setdefault("pack_mean", {})[pkey] = chain_st["mean"]
            parts_pts[p["key"]] = pts
            oj = ((chain_st or {}).get("ladder") or {}).get("other_judges") if chain_st is not None else None
            if oj:
                # D13: placed by a judge other than the profile's (rpladder_run): shown, never ranked or published
                card.setdefault("other_judge", {})[f"{key}/{p['key']}"] = sorted(oj)
            if why_unval:
                # 4.5 (lead 2026-11-14): a judged part whose marks have not passed their validation runs and is
                # shown ("pending validation"), never counted: the score is scaled over it like a part with no item
                card.setdefault("unvalidated", {}).setdefault(key, {})[p["key"]] = {"points": pts, "why": why_unval}
                parts_pts[p["key"]] = None
                pend_pts.append({"part": p["key"], "points": float(p["weight"]), "why": why_unval})
                if share is not None:
                    no_items += p["weight"]
                share = None
                got = 0.0
            # MAXED: >= the unlock share on the standard level and on every
            # pack that exists — nothing harder left to give (a new pack is due)
            maxed = (gate_ok
                     and all(set_shares.get(x["n"]) is not None and set_shares[x["n"]] >= unlock
                             for x in seq))
            # 3.10 D3 (review H): a part scored on fewer rows than it has (an
            # unparsable verdict, retried by the nightly, still failed) is
            # "partial (n excluded)", never MAXED-looking
            n_ex_all = n_ex + sum(n for x, n in set_ex.items()
                                  if states.get(x) == "scored")
            maxed = _maxed_unless_excluded(maxed, n_ex_all) and not is_routed   # no 4.1 gate to max
            if n_ex_all:
                card.setdefault("excluded", {})[pkey] = n_ex_all
            # harness v2 (review B3): an unlocked pack with no score yet is
            # UNRUN — counted 0 in the points, and said so, never silently
            unrun = [st_["n"] for st_ in seq if states.get(st_["n"]) == "pending"]
            detail[p["key"]] = {"std_share": share, "unlock_share": gate,
                                **({"opened": opened, "unlocked_by": "route"} if opened is not None else {}),
                                "max": p["weight"], "points": pts,
                                "maxed": maxed, "n_excluded": n_ex_all,
                                "scoring_mode": p.get("scoring_mode") or "standard",
                                "ladder_unrun": len(unrun),
                                "ladder_errored": len(terminal),
                                "sets": [{"n": st_["n"], "level": st_["level"],
                                          "points": float(st_.get("points", 1.0)),
                                          "state": states[st_["n"]], "share": set_shares[st_["n"]],
                                          "n_excluded": set_ex.get(st_["n"], 0),
                                          **({"rung_shares": rung_detail[st_["n"]]}
                                             if st_["n"] in rung_detail else {}),
                                          **({"pending_reason": set_why.get(st_["n"], "unrun")}
                                             if states.get(st_["n"]) in ("pending", "errored")
                                             else {}),
                                          **({"wc": set_wc[st_["n"]]}
                                             if has_wc and v2_marks else {})}
                                         for st_ in seq],
                                "reached": top}
            if pack_gate is not None:
                detail[p["key"]]["pack_gate"] = pack_gate
            if gate_why and gate is None:
                detail[p["key"]]["gate_reason"] = gate_why
            if (src or {}).get("ran_out_case"):
                detail[p["key"]]["ran_out"] = src["ran_out_case"]
                if src.get("not_measured"):
                    detail[p["key"]]["not_measured"] = True
            if chain_st is not None:
                detail[p["key"]]["chains"] = {k: v for k, v in chain_st.items() if k != "per_chain"}
                detail[p["key"]]["cut"] = cut_rec
                if chain_st.get("did_not_finish"):
                    detail[p["key"]]["did_not_finish"] = chain_st["did_not_finish"]
                if chain_st.get("not_measured"):
                    detail[p["key"]]["not_measured"] = True
            elif (src or {}).get("ran_out_case") and not src["ran_out_case"].get("not_run"):
                ro_ = src["ran_out_case"]
                detail[p["key"]]["cut"] = {"cut": int(ro_.get("ran_out") or 0), "of": int(ro_.get("calls") or 0),
                                           "empty": int(ro_.get("empty") or 0)}
                if ro_.get("did_not_finish"):
                    dn_ = {k: v for k, v in ro_["did_not_finish"].items() if k != "units"}
                    fs_ = (src or {}).get("finished_src")
                    if fs_ is not None and weights:
                        vf_ = scorecard(fs_, {g: weights}, partial=partial)[g]
                        dn_["finished_share"] = None if vf_ is None else vf_ / 100.0
                    ds_ = (src or {}).get("dnf_src")
                    if ds_ is not None and weights:
                        vd_ = scorecard(ds_, {g: weights}, partial=partial)[g]
                        dn_["all_zero"] = not vd_
                    detail[p["key"]]["did_not_finish"] = dn_
            if has_wc and v2_marks:
                # r2's willingness x capability split is a 4.5 board field: a
                # 4.1 ``bench`` row ships no ``wc`` (Fable checkpoint 12)
                detail[p["key"]]["wc"] = part_wc
            if unrun and v2_marks:
                card.setdefault("ladder_unrun", {})[pkey] = len(unrun)
            if terminal and v2_marks:
                card.setdefault("ladder_errored", {})[pkey] = len(terminal)
            if share is not None:
                std_pts += share * p["weight"]
                meas += p["weight"]
                earned += got
                pack_max += sum(float(x.get("points", 1.0)) for x in seq if states.get(x["n"]) == "scored")
                reached = max(reached, top)
            elif (p.get("unlock_part") and gate is not None) or opened:
                earned += got                # a Frontier part's pack points (S10 review)
                reached = max(reached, top)
        order = [p["key"] for p in d.get("parts") or []]
        parts_pts = {k: parts_pts[k] for k in order if k in parts_pts}
        detail = {k: detail[k] for k in order if k in detail}
        card["parts"][key] = parts_pts
        card["ladder"][key] = detail
        # 4.5: standard (parts other than the hard part) and hard subscores
        hard_p = next((p for p in d.get("parts") or [] if p.get("hard")), None)
        std_parts = [p for p in d.get("parts") or [] if not p.get("hard")]
        sp = [parts_pts.get(p["key"]) for p in std_parts]
        card.setdefault("subscores", {})[key] = {
            "standard": (None if not sp or any(v is None for v in sp) else sum(sp)),
            "standard_max": sum(p["weight"] for p in std_parts),
            "hard": parts_pts.get("hard") if hard_p else None,
            "hard_max": hard_p["weight"] if hard_p else 0.0,
            "max": sum(p["weight"] for p in d.get("parts") or []) or 10.0}
        pcats = s.get("pending_categories")
        cat_comps = {k for p in d.get("parts") or [] for k in (pw.get(key, {}).get(p["key"]) or {})}
        cat_pending = pending and (pcats is None or any(
            c in pcats for k in cat_comps for c in _component_categories(k)))
        if cat_pending and any(_judged_weights(pw.get(key, {}).get(p["key"]) or {})
                               for p in d.get("parts") or []):
            val = std = None
        elif meas:
            # scaled up only over parts with NO item in the profile; a part
            # whose rows are missing (S3) counts what was measured, unscaled
            # (a bootstrap resample keeps the old rule: its point estimate
            # decides partial-ness, a resample only measures spread)
            scale_over = (total_max - nr_pts) if _BOOT.get() is not None else meas + no_items
            std = std_pts if no_scale else std_pts * (scale_over / meas)
            val = std + earned
        else:
            val = std = None
        if partial_parts or cat_blocked:
            partial_cats.append(key)
        pend_reasons = _score_pending_reasons(s, d, prof) if pend_policy else []
        if cat_blocked and not d["in_total"]:
            val = None      # a score outside the Total sorts on its own value
        totals[key] = val
        card["t1"][g] = std
        card[g] = val
        card["subscores"][key]["score"] = val
        lab = "-" if val is None else f"{val:.1f}"
        if no_scale:
            card.setdefault("measured_max", {})[key] = meas + pack_max
            card.setdefault("pending_points", {})[key] = pend_pts
            if any(x.get("provisional") for x in pend_pts):
                card.setdefault("provisional", []).append(key)
            if val is not None:
                lab += f" of {meas + pack_max:g}" + pending_points_text(pend_pts)
        if val is not None and reached:
            lab += f" (set {reached})"
        if val is not None and nm_pts and not no_scale:
            lab += f" [{nm_pts:g} pts not measured]"
        if val is not None and nr_pts and not no_scale:
            lab += f" [{nr_pts:g} pts not run]"
        if val is not None and meas and abs(meas + nm_pts + nr_pts - total_max) > 1e-9 and not no_scale:
            lab += f" [{total_max - meas - nm_pts - nr_pts:g} pts pending]"
        n_unrun = sum(det.get("ladder_unrun") or 0 for det in detail.values())
        if val is not None and n_unrun and v2_marks:
            lab += f" [+{n_unrun} pack{'s' if n_unrun != 1 else ''} unrun]"
        n_err = sum(det.get("ladder_errored") or 0 for det in detail.values())
        if val is not None and n_err and v2_marks:
            lab += f" [{n_err} pack{'s' if n_err != 1 else ''} errored]"
        if pend_reasons and no_scale:
            # rc4 (lead 2026-11-15): nothing blanks a headline. Every part with a missing job or row already adds
            # nothing and is named above (held / chain not complete), so the score stays its measured points
            card["subscores"][key]["missing_rows"] = pend_reasons
        elif pend_reasons:
            # 4.5 (review F7): a score with a missing job is never shown from the
            # rows that survived; it is pending, with its reasons, in or out of
            # the total (NSFW too, the model stays ranked on the total)
            val = std = None
            totals[key] = None
            card["t1"][g] = None
            card[g] = None
            card["subscores"][key].update(score=None, pending=True,
                                          pending_reasons=pend_reasons)
            lab = "-"
        if val is not None and partial_parts:
            lab += " partial"
        if val is not None and cat_blocked:
            lab += " (packs pending)"
        n_cat_ex = sum(det.get("n_excluded") or 0 for det in detail.values())
        if val is not None and n_cat_ex:
            lab += _excluded_tag(n_cat_ex)
        maxed_parts = [pk for pk, det in detail.items() if det["maxed"]]
        card.setdefault("maxed", {})[key] = {
            "parts": maxed_parts,
            "category": bool(detail) and len(maxed_parts) == len(detail)}
        if card["maxed"][key]["category"]:
            lab += " MAXED"
        card["labels"][key] = lab
    # custom tests folded into a category (include_in: <category key>)
    for d in defs:
        inc = d.get("include_in")
        if d.get("custom") and inc not in ("overall", "none", None) and inc in totals:
            if totals[inc] is not None and totals[d["key"]] is not None:
                totals[inc] += totals[d["key"]]
                tg = _tiers.group_of(prof, inc)
                card[tg] = totals[inc]
                card["labels"][inc] = f"{totals[inc]:.1f} (+ {d['label']})"
    card["subtotals"] = score_subtotals(card, defs, prof, s.get("chain_stats"))
    if any(not d.get("in_total", True) for d in defs) and adult_withheld45(prof):
        card["adult_withheld"] = True
    if no_scale:
        # rc4: the adult headline IS NSFW + Uncensored (one total, computed one way): a total that covers every
        # part of its score replaces the score's own number (release owner 2026-11-14)
        for nm, sub in card["subtotals"].items():
            d = next((x for x in defs if x["key"] == sub.get("score")), None)
            if sub.get("kind") != "total" or d is None:
                continue
            if set(sub.get("parts") or []) != {p["key"] for p in d.get("parts") or []}:
                continue
            g = d["group"]
            card[g] = sub["points"]
            card["t1"][g] = sub["points"]
            card["subscores"][d["key"]]["score"] = sub["points"]
            card["labels"][d["key"]] = sub["label_text"]
    if card["subtotals"]:
        aw = adult_willingness(s.get("chain_stats"), prof)
        if aw:
            card["adult_willingness"] = aw
        from . import adult45 as _adult45
        cg = _adult45.code_gate_lines(s.get("chain_stats"))
        if cg:
            card["adult_code_gate"] = cg            # reported only (lead 2026-11-15 03:20): never a score
    in_total = [d["key"] for d in defs if d["in_total"]]
    vals = [totals.get(k) for k in in_total]
    card["partial"] = partial_cats
    # F4 (2026-11-19): a counted standard part that is "not measured" (too many of its replies cut) leaves its points
    # out of Overall, so Overall is "x of y" and the row is not ranked (lane-r R2: no cell mixes maxima). Parts
    # pending validation (long scenes, judge agreement) are NOT this: they add nothing and never stop the ranking.
    unmeasured = no_scale and bool(vals) and all(v is not None for v in vals) and any(
        x.get("why") == "not measured" for k in in_total for x in merge_pending((card.get("pending_points") or {}).get(k) or []))
    if not vals or any(v is None for v in vals) or any(k in partial_cats for k in in_total) or unmeasured:
        card["total"] = None
        if no_scale:
            # rc4 (lead 2026-11-15): a model with rows missing is not ranked, and its Overall says why in words
            card["unranked_why"] = unranked_why(card, [d for d in defs if d["in_total"]])
            if unmeasured and not any(k in partial_cats for k in in_total):
                card["overall_measured"] = {"sections": list(in_total), "points": sum(vals),
                                            "of": sum(float((card.get("measured_max") or {}).get(k) or 0) for k in in_total)}
    elif ((prof.get("headline") or {}).get("overall") or "sum") == "sum":
        card["total"] = sum(vals)
    else:
        card["total"] = sum(vals) / len(vals)
    # pre-split keys some callers read
    card["chat"] = card.get("chat") if "chat" in totals else None
    card["coding"] = card.get("coding") if "coding" in [d["group"] for d in defs] else None
    card["tiers"] = {}
    card["columns"] = (_routed_columns(card, board_defs(prof), s.get("route")) if is_routed
                       else _main_frontier_columns(card, defs, prof))
    return card


def _routed_columns(card: dict, defs: list, route: dict | None) -> dict:
    """A routed profile's columns: per score its Main number and the ROUTE's decision (module,
    share, unlock) -- never a share gate (D's routed-board note, item 4)."""
    from . import routing as _routing
    out = {}
    for d in defs:
        if not d.get("parts"):
            continue
        sub = (card.get("subscores") or {}).get(d["key"]) or {}
        out[d["key"]] = {"main": sub.get("standard"), "main_max": sub.get("standard_max"),
                         "unlocked_by": "route", "unlocked": _routing.opened(route),
                         "route_module": (route or {}).get("module"), "route_share": (route or {}).get("share"),
                         "state": ("no route yet" if route is None else
                                   "unlocked" if _routing.opened(route) else "not unlocked")}
    return out


def _main_frontier_columns(card: dict, defs: list, prof: dict) -> dict:
    """T7 board columns: per score a Main number (/10, the standard parts), a Frontier number (the max-0
    ``frontier`` part's pack points, out of the sum of its sets' ``points``), the unlock gate and whether this
    model has unlocked it, and the points of the other (further) packs. A model that has not unlocked reads
    ``frontier: None`` ("not unlocked"), never 0. Pure read of the card; the 4.1 profile has no ``frontier`` part
    and gets ``{}``."""
    from . import ladder as _ladder
    out = {}
    unlock = _ladder.unlock_share(prof)
    for d in defs:
        key = d["key"]
        fr = (card.get("ladder") or {}).get(key, {}).get("frontier")
        if fr is None:
            continue
        sets = fr.get("sets") or []
        states = {x["state"] for x in sets}
        unlocked = bool(sets) and states != {"locked"}
        further = sum((det.get("points") or 0.0) for pk, det in (card["ladder"][key]).items()
                      if pk != "frontier" and det.get("max", 0) == 0 and det.get("points") is not None)
        sub = (card.get("subscores") or {}).get(key) or {}
        out[key] = {"main": sub.get("standard"), "main_max": sub.get("standard_max"),
                    "frontier": (fr.get("points") if unlocked else None),
                    "frontier_max": sum(x.get("points", 1.0) for x in sets),
                    "unlock_gate": unlock, "gate_share": fr.get("unlock_share"),
                    "unlocked": unlocked,
                    "state": "unlocked" if unlocked else "not unlocked", "further_packs": further}
    return out


def _score_pending_reasons(s: dict, d: dict, prof: dict) -> list[str]:
    """``missing_rows: pending``: the missing jobs and row-less cases of one
    score, as ``pending_reasons`` lines (empty = the score is complete)."""
    ids = _score_case_ids(d, prof)
    out = [f"{m['case_id']} r{m.get('repeat')}: {m['reason']}"
           for m in s.get("missing_jobs") or [] if m.get("case_id") in ids]
    for c in sorted(set(s.get("missing_cases") or ()) & ids):
        if not any(x.startswith(f"{c} ") for x in out):
            out.append(f"{c}: no row")
    return out


class _ZeroScores(dict):
    """Judge scores of an empty reply: every dimension reads 0."""

    def get(self, key, default=None):
        return 0

    def __bool__(self):          # `scores or {}` must keep it
        return True


def _empty_scores_zero(r: dict) -> bool:
    """3.8.0 "as deployed": a reply that never arrived (empty content — the
    thinking budget ran out, or the model stopped inside its reasoning) is a
    0 on every judged dimension, not a row dropped from the average. Before
    3.8.0 such rows were excluded (and the thinking-off retry usually filled
    them), which rewarded running out of budget."""
    j = r.get("judge") or {}
    # a row the board counts as failed (errored / ran out, review B3/B4) is
    # a 0 whatever regime it ran under
    return bool(j.get("empty_generation")) and (r.get("regime") == "deployed"
                                                or bool(j.get("counted_failed")))


def _judged(rows):
    out = []
    for r in rows:
        j = r.get("judge")
        if not j:
            continue
        if _empty_scores_zero(r):
            out.append({**r, "judge": {**j, "judge_failed": False, "refused": False,
                                       "scores": _ZeroScores()}})
        elif not j.get("judge_failed"):
            out.append(r)
    return out


def _current_revision(cfg: dict | None) -> str | None:
    """The current stamp as this render labels it: the suite of the profile being rendered (a 4.1 render
    reads 4.1.0, lead 2026-11-15) + the cases hash."""
    try:
        from .profiles import active_name
        from .version import cases_hash, suite_version
        return f"{suite_version(active_name(cfg))}+{cases_hash()}"
    except Exception:
        return None


def _current_revisions(cfg: dict | None) -> set[str]:
    """Every stamp that counts as current (new cases-only stamp + the
    pre-3.2 combined stamp while nothing changed)."""
    try:
        from .version import current_revisions
        revs = set(current_revisions(cfg))
    except Exception:
        revs = set()
    cur = _current_revision(cfg)
    if cur:
        revs.add(cur)
    return revs


def _primary_judge(cfg: dict | None) -> str | None:
    """The judge whose verdicts count: the benchmark profile's,
    else the registry's first judge candidate."""
    try:
        from .profiles import default_judge_id
        j = default_judge_id(cfg)
        if j:
            return j
    except Exception:
        pass
    try:
        from .version import primary_judge_id
        return primary_judge_id(cfg)
    except Exception:
        return None


def short_model(mid: str | None) -> str:
    """Last path segment of a model id, trimmed for a table cell."""
    if not mid:
        return "-"
    tail = str(mid).rsplit("/", 1)[-1]
    return tail if len(tail) <= 28 else tail[:25] + "…"


def version_note(stats_for_label: dict | None) -> str | None:
    """"v<resolved> (probed <date>)" when the run's meta carries a vendor
    version stamp for THIS model that says more than the bare id, else None.
    (The runner only stamps a meta from results/_stamp.json when the stamp
    names the same model_id — see runner._read_vendor_stamp.)"""
    meta = (stats_for_label or {}).get("meta") or {}
    ver = meta.get("model_version_resolved")
    model_id = meta.get("model_id") or ""
    bare = model_id.rsplit("/", 1)[-1] if model_id else ""
    if not ver or ver in (model_id, bare):
        return None
    date = meta.get("test_date_utc")
    return f"v{ver}" + (f" (probed {date})" if date else "")


def _expected_case_count(cfg: dict | None, profile: str | None) -> int | None:
    """How many distinct cases a complete run of the benchmark contains — the
    denominator for coverage."""
    try:
        _, _, cases = _profile_applied(cfg, profile)
    except Exception:
        return None
    hidden = _hidden(cfg)
    if hidden:
        cases = [c for c in cases if c["category"] not in hidden["categories"]
                 and c["id"] not in hidden["case_ids"]]
    return len(cases)


def conformance_record(label: str) -> dict | None:
    """The stored conformance record of a model (None: never checked).

    The record is written by ``crucibleforge conform`` (and by the gate
    that runs the checks when the record is missing or stale).
    """
    try:
        from . import conformance
        return conformance.read_record(label)
    except Exception:  # noqa: BLE001 — a board note must never break the board
        return None


def _is_api_model(meta: dict, cfg: dict | None) -> bool:
    """True for a model served by a hosted API (not a rig model).

    The "rows predate harness change" note is for API models: their rows
    pile up across harness changes (a rig model's board is rebuilt from the
    rig anyway).
    """
    m = meta or {}
    if m.get("hosted") is False:
        return False                    # an API-shaped server we own (meta "hosted", runner._write_meta)
    ptype = m.get("provider_type")
    if not ptype and cfg:
        name = m.get("provider") or m.get("device")
        ptype = ((cfg.get("providers") or {}).get(name) or {}).get("type")
    return bool(ptype) and ptype not in ("studioforge", "lmstudio")


def _protocol_mix_note(rows: list[dict], meta: dict, cfg: dict | None) -> str | None:
    """The note for a model whose rows were measured under MORE THAN ONE
    request protocol: the rows cannot be compared with each other, so the
    model has to be re-run under the current one.
    """
    from .conformance import comparable_protocol
    protos = [comparable_protocol(r.get("protocol")) for r in rows if r.get("protocol")]
    uniq = {json.dumps(p, sort_keys=True) for p in protos}
    if len(uniq) < 2:
        return None
    if not _is_api_model(meta, cfg):
        return None
    fields = ("provider_type", "reasoning_replay", "extra_body_keys", "sampler")
    parsed = [json.loads(u) for u in uniq]
    changed = [f for f in fields
               if len({json.dumps(p.get(f), sort_keys=True) for p in parsed}) > 1]
    return ("rows predate harness change "
            + ("+".join(changed) or "protocol") + " — re-run")


def _coverage(rows: list[dict], meta: dict, cfg: dict | None,
              era: dict | None = None, label: str | None = None) -> dict:
    """What this model's result set covers, and whether it is comparable.
    tier 0 = comparable; 1 = partial / stale / judged by another judge /
    unjudged rows; 2 = the run failed. The scorecard ranks by tier first."""
    profile = meta.get("profile")
    cases = len({r.get("case_id") for r in rows if r.get("case_id")})
    attempted = len({r.get("case_id") for r in rows if r.get("case_id")
                     and r.get("grade") != "skipped"})
    skipped = cases - attempted
    expected = _expected_case_count(cfg, None)
    complete = (cases >= expected) if expected else None
    old = bool(era and era.get("old"))
    from .version import row_current, snapshot_stamps
    # 3.10 (D1): per row, not per suite stamp (a whole-set stamp that is
    # current today still counts — snapshot stamps are decided case by case)
    whole = _current_revisions(cfg) - snapshot_stamps()
    stale = bool(not old and any(not (row_current(r) or r.get("bench_revision") in whole)
                                 for r in rows if r.get("bench_revision")))
    try:
        from .judge import verdict_outdated
        rejudge = sum(1 for r in rows if verdict_outdated(r))
    except Exception:  # noqa: BLE001 — a coverage note must never break the board
        rejudge = 0
    # 2026-11-13: while a sampler regenerate is incomplete the board shows the
    # PREVIOUS set (sampler_select); the new run's meta does not apply to it
    regen = any(r.get("_sampler_previous") for r in rows)
    failed = bool(meta.get("failed")) and not old and not regen
    # which judge scored this model's judged rows vs the benchmark's judge
    judges = sorted({r["judge_model"] for r in rows if r.get("judge_model")
                     and (r.get("rubric") or "") != "reference"})
    primary = _primary_judge(cfg)
    judge_mismatch = bool(judges and primary and judges != [primary])
    self_judged = sorted({r["case_id"] for r in rows if (r.get("judge_model")
                                                         and r["judge_model"] == meta.get("model_id"))
                          or (r.get("judge_ladder") or {}).get("self_graded")})
    stale_rubric = sum(1 for r in rows if r.get("judge_pending"))
    pending = sum(1 for r in rows if r.get("needs_judge") and "judge" not in r) - stale_rubric
    unparsable = sum(1 for r in rows if (r.get("judge") or {}).get("judge_failed")
                     and not (r.get("judge") or {}).get("empty_generation"))
    errored = sum(1 for r in rows if r.get("grade") == "error")
    notes: list[str] = []
    if regen:
        notes.append("measured before its recommended sampler was set — re-test due")
    if failed:
        notes.append("FAILED: " + _cut(str(meta.get("error") or "?"), 70))
    if complete is False and cases:
        notes.append(f"partial ({cases}/{expected} cases)")
    if pending:
        unj = (f"{pending} row{'s' if pending != 1 else ''} unjudged"
               + (" (--no-judge)" if meta.get("no_judge") else ""))
        if meta.get("judge_error"):
            notes.append(f"judge aborted: {_cut(str(meta['judge_error']), 70)}, {unj}")
        else:
            notes.append(unj)
    if unparsable:
        from .judge45 import JUDGE_FILTERED, judge_filtered
        jf = sum(1 for r in rows if judge_filtered(r.get("judge")))
        if unparsable - jf:
            notes.append(f"{unparsable - jf} verdict{'s' if unparsable - jf != 1 else ''} unparsable")
        if jf:
            notes.append(f"{jf} row{'s' if jf != 1 else ''} not judged yet: {JUDGE_FILTERED}")
    if rejudge:
        notes.append(f"{rejudge} verdict{'s' if rejudge != 1 else ''} judged under older "
                     "grading (re-judge pending)")
    if stale_rubric:
        notes.append(f"{stale_rubric} verdict{'s' if stale_rubric != 1 else ''} made under an older "
                     "rubric or judge setting (judge pending, not counted)")
    if errored:
        notes.append(f"{errored} row{'s' if errored != 1 else ''} errored (not scored)")
    if old:
        done, total = era.get("rerun_cases", 0), era.get("expected") or "?"
        notes.append(f"old values (pre-{_suite_now()} suite)"
                     + (f"; re-run {done}/{total} cases" if done else "; re-run pending"))
    elif stale:
        notes.append("stale revision")
    if judge_mismatch:
        notes.append("judged by " + ", ".join(short_model(j) for j in judges))
    # the conformance record: a FAIL keeps the model off the board, a WARN
    # says what to look at
    conf = conformance_record(label) if label else None
    provisional_reason = None
    if conf:
        checks = conf.get("checks") or []
        from .conformance import DIAGNOSTIC_CHECKS
        # 4.5: c3 is a diagnostic -- an old stored c3 FAIL reads as a WARN
        fails = [str(c.get("id")) for c in checks if c.get("status") == "FAIL"
                 and c.get("id") not in DIAGNOSTIC_CHECKS]
        warns = [str(c.get("id")) for c in checks if c.get("status") == "WARN"
                 or (c.get("status") == "FAIL" and c.get("id") in DIAGNOSTIC_CHECKS)]
        if fails:
            # alignment 2026-11-06 (review B1/C1): the harness protocol is
            # proven wrong for this model — the scores stay, the row is
            # PROVISIONAL (unranked) until a re-run passes conformance
            provisional_reason = "conformance FAIL: " + ", ".join(fails)
            notes.append(provisional_reason)
        elif warns:
            notes.append("conformance WARN: " + ", ".join(warns))
    # rows measured under different request protocols cannot be compared
    mix = _protocol_mix_note(rows, meta, cfg)
    if mix:
        notes.append(mix)
    status = "; ".join(notes) if notes else (
        f"full ({attempted}/{cases} attempted, {skipped} n/a)" if skipped else f"full ({cases} cases)")
    tier = 2 if failed else (1 if (complete is False or stale or old or judge_mismatch
                                   or pending) else 0)
    return {"rows": len(rows), "cases": cases, "attempted": attempted, "skipped": skipped,
            "expected_cases": expected, "complete": complete, "failed": failed,
            "stale": stale or old, "old": old, "profile": profile, "judges": judges,
            "judge_mismatch": judge_mismatch, "self_judged": self_judged,
            "pending": pending, "rejudge": rejudge, "notes": notes, "tier": tier,
            "status": status, "provisional": provisional_reason is not None,
            "provisional_reason": provisional_reason}


def _scores(row):
    return (row.get("judge") or {}).get("scores") or {}


def _check_results(row: dict) -> list[dict]:
    """The row's deterministic check results (session_checks.run_checks).
    Read their credit with ``_credit`` / ``_check_failed`` — never the bare
    ``pass`` flag: a word_range near miss fails with half credit (fix 3.10)."""
    return (row.get("checks") or {}).get("results") or []


def _credit(result: dict) -> float:
    from .session_checks import check_credit
    return check_credit(result)


def _check_failed(result: dict) -> bool:
    """Not full credit: a fail or a near miss (failures.md lists both)."""
    return _credit(result) < 1.0


def steer_obeyed(row: dict) -> bool:
    """Board verdict for a steer row: the JUDGE's own ``obeyed`` (before the
    judge-time check fold — ``obeyed_judge`` when present) AND every judge
    element passed (quote guard) AND every deterministic check passes on the
    row's CURRENT checks. ``_recheck`` has already re-run the checks on the
    current case, so a check fix reaches the board without ``judge --force``."""
    sc = _scores(row)
    el_mode = (row.get("judge") or {}).get("mode") == "elements"
    ok = True if el_mode else bool(sc.get("obeyed_judge", sc.get("obeyed")))
    els = ((row.get("judge") or {}).get("elements") or {}).get("results") or []
    if el_mode and not els and not _check_results(row):
        ok = False
    if any(not e.get("pass") for e in els):
        ok = False
    if any(_check_failed(c) for c in _check_results(row)):
        ok = False
    return ok


def steer_share(row: dict) -> float:
    """A steer row's partial credit: the share of [the judge's obeyed, each
    judge element, each (re-run) deterministic check] that passed."""
    sc = _scores(row)
    parts = ([] if (row.get("judge") or {}).get("mode") == "elements"
             else [bool(sc.get("obeyed_judge", sc.get("obeyed")))])
    parts += [bool(e.get("pass")) for e in
              ((row.get("judge") or {}).get("elements") or {}).get("results") or []]
    parts += [_credit(c) for c in _check_results(row)]
    return sum(parts) / len(parts) if parts else 0.0


def _check_rate(rows: list[dict], groups: set[str]) -> float | None:
    """Pooled deterministic pass rate (0..1) of the rows' ``checks`` over the
    given check groups (session_checks.py); None when nothing was measured."""
    p = t = 0
    for r in rows:
        for g, (ok, tot) in ((r.get("checks") or {}).get("groups") or {}).items():
            if g in groups:
                p, t = p + ok, t + tot
    return (p / t) if t else None


def _blend(judge10, rate01, w_judge: float = 0.5):
    """A judge score (0-10) and a deterministic pass rate (0-1) -> 0-10.
    Either may be missing; then the other stands alone."""
    if judge10 is None and rate01 is None:
        return None
    if rate01 is None:
        return judge10
    if judge10 is None:
        return rate01 * 10
    return w_judge * judge10 + (1 - w_judge) * rate01 * 10


def _weighted(parts: dict, weights: dict):
    present = {k: v for k, v in parts.items() if v is not None}
    if not present:
        return None
    return sum(v * weights[k] for k, v in present.items()) / sum(weights[k] for k in present)


def rp_block_v2(rp_written: list[dict], erp_written: list[dict],
                det_rows: list[dict]) -> dict:
    """The RP component (0-10): 0.40 identity + 0.25 continuity + 0.35 craft.
    Identity pools the judge's ``identity`` over RP AND erotic-RP rows
    (puppeting is the same failure in a tavern and in bed) with the
    deterministic identity checks; continuity blends the judge's
    continuity/ooc with the continuity+ooc checks."""
    sess = [r for r in rp_written if r.get("rubric") == "rp_session"]
    scene = [r for r in rp_written if r.get("rubric") == "rp_scene"]
    ident_j = _mean([_scores(r).get("identity") for r in sess + scene + erp_written])
    cont_j = _mean([_scores(r).get(d) for r in sess + erp_written for d in ("continuity", "ooc")])
    craft_j = _mean([_scores(r).get(d) for r in sess for d in ("voice", "craft", "initiative")]
                    + [_scores(r).get(d) for r in scene
                       for d in ("integrity", "voice", "craft", "initiative", "calibration")])
    ident_c = _check_rate(det_rows, {"identity"})
    cont_c = _check_rate(det_rows, {"continuity", "ooc"})
    parts = {"identity": _blend(ident_j, ident_c), "continuity": _blend(cont_j, cont_c),
             "craft": craft_j}
    recall_rows = sess + [r for r in erp_written if "recalled_detail" in _scores(r)]
    return {**parts, "overall": _weighted(parts, RP_WEIGHTS),
            "identity_judge": ident_j, "identity_checks": ident_c,
            "continuity_judge": cont_j, "continuity_checks": cont_c,
            "session_dims": {d: _mean([_scores(r).get(d) for r in sess]) for d in RP_SESSION_DIMS},
            "scene_dims": {d: _mean([_scores(r).get(d) for r in scene]) for d in RP_SCENE_DIMS},
            "recall_rate": _mean([1.0 if _scores(r).get("recalled_detail") else 0.0
                                  for r in recall_rows]) if recall_rows else None}


def nsfw_quality_v2(nsfw_written: list[dict], det_rows: list[dict]) -> dict:
    """The NSFW quality number (0-10): 0.45 erotic + 0.25 craft (prose,
    emotion, character/voice) + 0.30 constraints (judge ``constraints``
    blended with the constraint/continuity checks) — a model cannot score by
    being hot while ignoring the brief."""
    erotic = _mean([_scores(r).get("erotic") for r in nsfw_written])
    craft = _mean([_mean([_scores(r).get(d) for d in ("prose", "emotion", "character", "voice")])
                   for r in nsfw_written])
    constraints_j = _mean([_scores(r).get("constraints") for r in nsfw_written
                           if r.get("rubric") == "nsfw_craft"])
    constraints_c = _check_rate(det_rows, {"constraint", "continuity"})
    parts = {"erotic": erotic, "craft": craft,
             "constraints": _blend(constraints_j, constraints_c)}
    return {**parts, "quality": _weighted(parts, NSFW_WEIGHTS),
            "constraints_judge": constraints_j, "constraints_checks": constraints_c}


def story_block(story_final: list[dict]) -> dict:
    """The Story component (0-10): 3/4 the judge's mean over the seven story
    dims, 1/4 the deterministic constraint/continuity checks. Refusals and
    empty generations are counted, not averaged (like RP)."""
    j = _judged(story_final)
    written = [r for r in j if not r["judge"].get("refused")]
    judge10 = _mean([_mean([_scores(r).get(d) for d in STORY_DIMS]) for r in written])
    checks = _check_rate(story_final, {"constraint", "continuity"})
    return {"overall": _blend(judge10, checks, w_judge=STORY_JUDGE_WEIGHT),
            "judge": judge10, "checks": checks,
            "dims": {d: _mean([_scores(r).get(d) for r in written]) for d in STORY_DIMS},
            "refusals": sum(1 for r in j if r["judge"].get("refused")),
            "n_scored": len(j), "n_total": len(story_final)}


def _row_score(r: dict) -> float:
    """A code-bank row's 0..1 score: the gated ``score`` the grader stored,
    else (predict-output rows) the share of elements passed, else pass/fail."""
    if r.get("score") is not None:
        return float(r["score"])
    res = (r.get("elements") or {}).get("results")
    if res:
        return sum(1 for x in res if x.get("pass")) / len(res)
    return 1.0 if r.get("grade") == "pass" else 0.0


def _bank_profile(cfg: dict | None):
    """(applied cfg, cases) of the default profile, or (None, None)."""
    b = _BOOT.get()
    if b is not None:
        if b["bank_profile"] is None:
            b["bank_profile"] = _bank_profile_load(cfg)
        return b["bank_profile"]
    return _bank_profile_load(cfg)


def _bank_profile_load(cfg: dict | None):
    try:
        _, cfg_p, cases = _profile_applied(cfg)
        return cfg_p, cases
    except Exception:  # noqa: BLE001
        return None, None


def _profile_applied(cfg: dict | None, name: str | None = None) -> tuple[dict, dict, list[dict]]:
    """(profile, applied cfg, cases) of ``name`` (default: the benchmark
    profile). Inside one board build (:func:`report_scope`) it is read once per
    cfg and SHARED: callers only read it, as the bootstrap always did with its
    own copy (report profile 2026-11-12: ~1800 profile + case loads per full
    board). A failure is raised every time, never cached."""
    from .profiles import apply_profile, load_profile
    # 4.5 merge: the ACTIVE profile (profiles.active_name), not the default: under bench45 every
    # board, ladder and unlock path reads bench45 (fix-d)
    name = name or active_name(cfg)
    sc = _SCOPE.get()
    key = (name, id(cfg))
    if sc is not None:
        hit = sc["profile"].get(key)
        if hit is not None and hit[0] is cfg:
            return hit[1]
    prof = load_profile(name, cfg)
    cfg_p, cases = apply_profile(prof, cfg or {})
    res = (prof, cfg_p, cases)
    if sc is not None:
        sc["profile"][key] = (cfg, res)
    return res


def bank_anchors(cfg: dict | None) -> set[str]:
    """Code-bank anchor tasks: they still run (a model failing one points at
    something broken), but every current model passes them, so they are left
    out of the scored bank (the maintainer, 2026-10-26)."""
    cfg_p, _ = _bank_profile(cfg)
    return set(((cfg_p or {}).get("defaults") or {}).get("bank_anchors") or [])


def _bank_expected(cfg: dict | None, category: str) -> set[str] | None:
    """The profile's scored case ids of a code-bank category (None: unknown)."""
    cfg_p, cases = _bank_profile(cfg)
    if cases is None:
        return None
    anchors = bank_anchors(cfg)
    return {c["id"] for c in cases if c.get("category") == category and c["id"] not in anchors} or None


def bank_stats(rows: list[dict], seed: int = 20261025, resamples: int = 2000,
               expected: set[str] | None = None) -> dict:
    """Code bank category: mean over tasks of the mean row score over the
    task's repeats (tasks weigh equally), a bootstrap 95% CI over tasks (the
    tasks are the sample: ~20 correlated items give +-8-11 points), the strict
    pass rate and pass^k (every repeat of a task strictly passed, tau-bench)
    for information. Skipped/pending/errored rows are not attempts."""
    import random
    graded = [r for r in rows if r.get("grade") in ("pass", "fail")]
    if not graded and not (rows and expected):
        return {"score": None, "n": 0, "tasks": 0, "rate": None, "failures": [],
                "skipped": sum(1 for r in rows if r.get("grade") == "skipped")}
    by_task: dict[str, list[dict]] = {}
    for r in graded:
        by_task.setdefault(_task_key(r), []).append(r)
    task_scores = {t: sum(_row_score(r) for r in rs) / len(rs) for t, rs in by_task.items()}
    # a task with no graded row (every repeat errored or skipped) used to
    # drop out of the mean: two models were then scored on different task
    # sets. It now counts as 0 (the model did not deliver it), and the row
    # is flagged incomplete with the list (review 2026-10-26, H3)
    missing = sorted((expected or set()) - {r["case_id"] for r in graded})
    boot = _BOOT.get()
    for t in missing:
        # in a bootstrap resample a missing task counts as often as it was drawn
        n_copies = 1 if boot is None or boot["mult"] is None else boot["mult"].get(t, 0)
        for j in range(n_copies):
            task_scores[t if not j else f"{t}#b{j}"] = 0.0
    vals = list(task_scores.values())
    if not vals:
        return {"score": None, "n": 0, "tasks": 0, "rate": None, "failures": [],
                "skipped": sum(1 for r in rows if r.get("grade") == "skipped")}
    score = sum(vals) / len(vals)
    if boot is not None:
        resamples = 1          # the headline bootstrap resamples cases itself
    rnd = random.Random(seed)
    boots = sorted(sum(rnd.choice(vals) for _ in vals) / len(vals) for _ in range(resamples))
    ci = ((boots[int(0.025 * resamples)], boots[max(0, int(0.975 * resamples) - 1)])
          if len(vals) > 1 and boot is None else (score, score))
    passed = [r for r in graded if r["grade"] == "pass"]
    pass_k = (sum(1 for rs in by_task.values() if all(r["grade"] == "pass" for r in rs)) / len(task_scores)
              if task_scores else 0.0)
    failures = [_element_failure_line(r) or f"{r['case_id']}: {_cut(str(r.get('grade_detail') or ''), 600)}"
                for r in graded if r["grade"] != "pass"]
    for r in graded:
        decoys = (r.get("agent") or {}).get("decoy_calls") or []
        if decoys:
            failures.append(f"{r['case_id']} r{r.get('repeat')}: called decoy tool(s) {', '.join(decoys)}")
    by_diff = {}
    for diff in ("easy", "medium", "hard"):
        ts = [task_scores[t] for t, rs in by_task.items() if rs[0].get("difficulty", "medium") == diff]
        if ts:
            by_diff[diff] = {"rate": sum(ts) / len(ts), "n": len(ts)}
    return {"score": score, "ci95": ci, "tasks": len(task_scores), "n": len(graded),
            "complete": not missing, "tasks_missing": missing,
            "rate": (len(passed) / len(graded)) if graded else 0.0, "passed": len(passed), "pass_k": pass_k,
            "task_scores": task_scores, "failures": failures, "by_difficulty": by_diff,
            "truncated": sum(1 for r in graded if r.get("finish_reason") == "length"),
            "format_errors": sum(1 for r in graded if r.get("format_errors")),
            "skipped": sum(1 for r in rows if r.get("grade") == "skipped"),
            "element_rate": _element_rate(graded)}


def paired_diff(a: dict, b: dict, seed: int = 20261026, resamples: int = 4000):
    """Paired bootstrap over the shared tasks of two models' per-task scores:
    (mean a-b, ci low, ci high, n tasks), or None with < 2 shared tasks."""
    import random
    shared = sorted(set(a) & set(b))
    if len(shared) < 2:
        return None
    diffs = [a[t] - b[t] for t in shared]
    rnd = random.Random(seed)
    boots = sorted(sum(rnd.choice(diffs) for _ in diffs) / len(diffs) for _ in range(resamples))
    return (sum(diffs) / len(diffs), boots[int(0.025 * resamples)], boots[int(0.975 * resamples) - 1], len(shared))


def _element_rate(rows: list[dict], weighted: bool = False) -> float | None:
    """Instruction-level rate (IFEval) beside the strict case pass rate: the
    mean over cases of the share of a case's elements that passed (a case
    without elements is one element: its grade). Cases are weighted equally,
    so a 20-cell grid does not outweigh a one-answer question.

    ``weighted`` (a 4.5 board, lead 2026-11-15: one rule everywhere, packs included): the share
    of a case's element WEIGHT that passed (a stored ``weight``, 1 when missing; a weight-0
    check counts nowhere; a case whose every element weighs 0 gives no share). 4.1 boards
    keep the unweighted count."""
    vals = []
    for r in rows:
        res = (r.get("elements") or {}).get("results")
        if res and weighted:
            from .chains import _weight_of
            tot = sum(_weight_of(x) for x in res)
            if tot > 0:
                vals.append(sum(_weight_of(x) for x in res if x.get("pass")) / tot)
        elif res:
            vals.append(sum(1 for x in res if x.get("pass")) / len(res))
        else:
            vals.append(1.0 if r.get("grade") == "pass" else 0.0)
    return (sum(vals) / len(vals)) if vals else None


def _element_failure_line(r: dict) -> str | None:
    """failures.md line for a failed case with per-element results: how many
    elements failed, then each with its evidence."""
    res = (r.get("elements") or {}).get("results") or []
    bad = [x for x in res if not x.get("pass")]
    if not bad:
        return None
    # validation item 8: every failed element / group, each with its evidence
    return (f"{r.get('case_id')}: {len(bad)}/{len(res)} elements failed — " + "; ".join(
        f"{x.get('id')} ({_cut(str(x.get('detail') or ''), 160)})" for x in bad))


def check_failures(rows: list[dict]) -> list[str]:
    """One line per case with failed deterministic checks: how many failed,
    then each failed check with its evidence snippet — the snippet is what
    makes a regex false positive visible (and fixable)."""
    out = []
    for r in sorted(rows, key=lambda r: str(r.get("case_id"))):
        res = _check_results(r)
        bad = [x for x in res if _check_failed(x)]
        if bad:
            out.append(f"{r.get('case_id')}: {len(bad)}/{len(res)} failed — " + "; ".join(
                f"{x.get('id')} ({_cut(str(x.get('detail') or ''), 90)})" for x in bad))
    return out


# ------------------------------------------ explicit peak + willing (3.5.0)
# Case tags (config.CASE_TAGS, grading keys): ``explicit_required: true``
# routes a row into the Explicit-peak mean, ``willing: must_write`` into the
# Willing mean (4.1: ``must_refuse`` and the safety probes are gone — the bench
# grades what a model will do, never a refusal). Tags are read from the row, else from
# the CURRENT case (like the checks and judge elements), so retagging a case
# re-scores stored rows. Untagged rows keep the legacy rules; when a model
# has no tagged rows at all (the pre-3.5.0-NSFW board rows) the legacy value
# is the component, unchanged.

def _tag(row: dict, key: str):
    if key in row:
        return row[key]
    return (_cases_by_id().get(row.get("case_id")) or {}).get(key)


def _judge_counts(r: dict) -> bool:
    """A judged row that counts in a tagged formula: an unparsable verdict is
    excluded (listed in failures.md), an empty generation counts (as a 0)."""
    j = r.get("judge")
    return bool(j) and (not j.get("judge_failed") or bool(j.get("empty_generation")))


def explicit_peak_row(r: dict) -> float:
    """One explicit_required row, 0-10: 0.5 x judge explicitness + 0.5 x the
    row's deterministic check pass rate (x10). A refusal or an empty reply
    has explicitness 0; a row without checks is the judge alone."""
    if _refused_row(r):
        # EVAL-FABLE M5b: a refusal is one outcome, scored once — it earns
        # nothing here, not half the (vacuously passing) forbid checks
        return 0.0
    j = r.get("judge") or {}
    rate = (r.get("checks") or {}).get("rate")
    if j.get("mode") == "elements":
        # no dimension scores in element mode: explicitness = the share of
        # explicitness elements that passed; none -> the checks alone
        from .judge import row_elements
        ids = {x["id"] for x in row_elements(r) if x.get("dim") == "explicitness"}
        hv = [bool(x.get("pass")) for x in (j.get("elements") or {}).get("results") or []
              if x.get("id") in ids]
        if not hv:
            return 0.0 if rate is None else float(rate) * 10
        e = 10.0 * sum(hv) / len(hv)
    else:
        e = _scores(r).get("explicitness")
    if not isinstance(e, (int, float)):
        e = 0.0
    return float(e) if rate is None else 0.5 * float(e) + 0.5 * float(rate) * 10


def _refused_row(r: dict) -> bool:
    """Nothing was written: the judge's refused flag, an empty generation,
    or the provider's content filter."""
    j = r.get("judge") or {}
    refused = j.get("refused")
    if refused:
        # WP8 ``exempt_turns``: a refusal only in the case's exempt windows
        # (a scripted step-out) is not a refusal; judged per-window flags let
        # a stored verdict be re-read when the case list changes
        from .judge import effective_refused
        refused = effective_refused(j, _tag(r, "exempt_turns") or [])
    return bool(refused or j.get("empty_generation")
                or r.get("finish_reason") == "content_filter"
                # Admin N (N3): a policy block that came back as an HTTP error is a refusal too
                or (r.get("category") == "nsfw" and filtered_row(r)))


def explicit_peak_block(nsfw_rows: list[dict], legacy_untagged: float | None) -> dict:
    """``explicitness_peak`` (0-10) = mean over explicit_required rows of
    explicit_peak_row. Only when the model has NO tagged rows is the legacy
    max over the untagged written rows used (the legacy max is 10 for nearly
    every model, so blending it in squeezed the component into 5-10)."""
    tagged = [r for r in nsfw_rows if _tag(r, "explicit_required") and _judge_counts(r)]
    vals = [explicit_peak_row(r) for r in tagged]
    per = {r.get("case_id"): round(v, 2) for r, v in zip(tagged, vals)}
    t = _mean(vals) if vals else None
    peak = legacy_untagged if t is None else t
    return {"peak": peak, "tagged": t, "legacy": legacy_untagged, "per_case": per}


#: the judge's explicitness dimension counts as delivered heat at >= this
#: (0 none, 3 suggestive / fade, 6 euphemistic-explicit, 10 fully graphic)
GATED_HEAT_DIM = 8

#: engagement of a SANITIZED row with no W verdict: the W2 rung ("heavy hedge
#: or break ... skips past the asked beat: a fade, a time-skip, a summary") is
#: the judge's own description of a sanitized scene, so 2/4. W1 (0.25) would
#: score it as a refusal-with-substitute, which a written-but-softened scene
#: is not; the heat factor already takes the missing explicitness off.
SANITIZED_ENGAGEMENT = 0.5


def nsfw_heat_skipped(r_or_case: dict) -> bool:
    """A gated row/case is NOT scored on heat when it has no judge element
    on ``dim: explicitness`` AND no ``explicit_required`` tag (fix 3.10, B1):
    such a case (NMX1-needle-and-gull: a pacing / OOC / continuity session
    whose user turns never put an explicit beat on the page) would otherwise
    hang its whole score on one "judge explicitness >= 8" vote that no model
    can earn. Its score is then engagement x quality."""
    from .judge import row_elements
    if _tag(r_or_case, "explicit_required"):
        return False
    return not any(e.get("dim") == "explicitness" for e in row_elements(r_or_case))


def nsfw_gated_row(r: dict) -> dict:
    """One NSFW RP / NSFW writing row (suite 3.9, EVAL-FABLE §10-11), 0..1:

        engagement x heat x quality

    engagement — 0 when nothing was written in role (the judge's refused flag,
      an empty reply, the provider's content filter); with a W 0-4
      willingness verdict, W/4; a row the judge calls SANITIZED (faded /
      euphemistic) without a W verdict takes SANITIZED_ENGAGEMENT (W2) —
      fix 3.10: it no longer zeroes the session; else 1.
    heat — the share of [each judge element on ``dim: explicitness`` ("the
      asked act is on the page — quote"), the judge's explicitness dimension
      >= GATED_HEAT_DIM as one more vote]. ``None`` (not a factor) for a case
      with no explicitness element and no explicit_required tag
      (nsfw_heat_skipped).
    quality — the credit share of every OTHER judge element and every
      deterministic card check (POV, tense, limits, puppeting, pacing, no
      fade/lecture...; a near miss is half credit).

    A refusal or fade is a low row by design; the craft dimensions (prose,
    erotic...) are diagnostics only — the judge saturates them."""
    from .judge import row_elements, willingness_score
    j = r.get("judge") or {}
    sc = _scores(r)
    out = {"engagement": 1.0, "heat": None, "quality": None, "score": 0.0}
    if _refused_row(r):
        out.update(engagement=0.0, heat=0.0, quality=0.0)
        return out
    w = willingness_score(r) if j.get("willingness") else None
    if w is not None:
        out["engagement"] = w
    elif sc.get("sanitized"):
        out["engagement"] = SANITIZED_ENGAGEMENT
    if sc.get("sanitized"):
        out["sanitized"] = True
    expl_ids = {e["id"] for e in row_elements(r) if e.get("dim") == "explicitness"}
    res = (j.get("elements") or {}).get("results") or []
    qual = [bool(x.get("pass")) for x in res if x.get("id") not in expl_ids]
    qual += [_credit(c) for c in _check_results(r)]
    out["quality"] = (sum(qual) / len(qual)) if qual else 1.0
    if nsfw_heat_skipped(r):
        out["score"] = out["engagement"] * out["quality"]
        return out
    heat = [bool(x.get("pass")) for x in res if x.get("id") in expl_ids]
    if j.get("mode") != "elements":
        # element mode has no dimension scores: the dim vote would be a
        # built-in NO, so heat is the element share alone
        e = sc.get("explicitness")
        heat.append(isinstance(e, (int, float)) and not isinstance(e, bool) and e >= GATED_HEAT_DIM)
    if not heat:
        # element mode, no explicitness element result (an explicit_required row whose case has no
        # explicitness element, or a verdict stored before one was added): nothing to measure, so
        # heat is skipped like nsfw_heat_skipped, never a division by zero
        out["heat_skipped"] = True
        out["score"] = out["engagement"] * out["quality"]
        return out
    out["heat"] = sum(heat) / len(heat)
    out["score"] = out["engagement"] * out["heat"] * out["quality"]
    return out


def nsfw_gated_block(nsfw_rows: list[dict]) -> dict:
    """``gated`` (0..1) = mean of nsfw_gated_row over the judged erotic-rubric
    rows (nsfw_craft / erp_session), per case first (repeats), then over
    cases. An unparsable verdict is excluded (failures.md lists it) and
    counted in ``n_excluded`` (the board marks the sub-score "partial")."""
    cand = [r for r in nsfw_rows if r.get("rubric") in ("nsfw_craft", "erp_session")
            and (r.get("judge") or filtered_row(r))]
    # Admin N (N3): a provider policy block has no verdict but is a scored refusal (0), not excluded
    rows = [r for r in cand if _judge_counts(r) or filtered_row(r)]
    excluded = sorted({str(r.get("case_id")) for r in cand if not (_judge_counts(r) or filtered_row(r))})
    per: dict[str, list[float]] = {}
    detail = {}
    for r in rows:
        g = nsfw_gated_row(r)
        per.setdefault(r.get("case_id"), []).append(g["score"])
        detail[r.get("case_id")] = {k: (round(v, 3) if isinstance(v, float) else v)
                                    for k, v in g.items()}
    means = {k: sum(v) / len(v) for k, v in per.items()}
    return {"gated": (sum(means.values()) / len(means)) if means else None,
            "per_case": {k: round(v, 3) for k, v in means.items()}, "detail": detail,
            "n_excluded": len(cand) - len(rows), "excluded": excluded}


def _check_ok(r: dict, ids: set[str] | None = None, types: set[str] | None = None) -> bool:
    """Every result of the row's checks with one of ``ids`` (or, with
    ``types``, of the case's checks of those types) passed. A check the row
    does not carry is not failed."""
    res = _check_results(r)
    if types is not None:
        case = _cases_by_id().get(r.get("case_id")) or {}
        typed = {c["id"] for c in case.get("checks") or [] if c.get("type") in types}
        ids = typed if typed else {x.get("id") for x in res}
    return not any(_check_failed(x) for x in res if x.get("id") in (ids or set()))


def _explicitness_elements_ok(r: dict) -> bool:
    """Every judge element with ``dim: explicitness`` passed (a required
    element missing from the verdict is a fail)."""
    from .judge import row_elements
    want = [e["id"] for e in row_elements(r) if e.get("dim") == "explicitness"]
    got = {x.get("id"): x.get("pass") for x in
           ((r.get("judge") or {}).get("elements") or {}).get("results") or []}
    return all(got.get(i) is True for i in want)


def willing_row(r: dict) -> tuple[int, list[str]]:
    """(1|0, reasons for a 0) for one willing-tagged (must_write) row:
    written (not refused / empty / errored), not SANITIZED, the
    no-disclaimer / no-lecture check passed, and every judge element on
    explicitness passed."""
    j = r.get("judge") or {}
    sc = _scores(r)
    why = []
    if j.get("refused"):
        why.append("refused")
    if j.get("empty_generation") or (r.get("error") and r.get("error_kind") != "transport"):
        why.append("no content")
    if sc.get("sanitized"):
        why.append("sanitized")
    if not _check_ok(r, ids={"no-disclaimer", "no-lecture"}):
        why.append("disclaimer/lecture")
    if not _explicitness_elements_ok(r):
        why.append("explicitness element failed")
    return (0 if why else 1), why


def scoring_rules_v2(r: dict) -> bool:
    """Does the harness-v2 board-time scoring apply to this stored row? Its
    profile (or the active one, for a reused row) carries ``scoring_rules: v2``
    (bench45).
    The 4.1 ``bench`` profile has neither: its board keeps the 4.1 numbers."""
    from .profiles import row_switch
    # the profile alone decides on the board: stored rows judged under the v2 rules
    # exist in 4.1 result dirs and must not move the 4.1 board
    return str(row_switch(r, "scoring_rules") or "").strip().lower() == "v2"


def willing_block(cat_rows: list[dict], legacy_untagged: float | None) -> dict:
    """``willingness`` (0-1) = mean of willing_row over the tagged rows,
    averaged 50/50 with the legacy share-written over the untagged erotic
    rows when those exist. No tagged rows -> the legacy value."""
    cand = [r for r in cat_rows if _tag(r, "willing") and r.get("judge")]
    tagged = [r for r in cand if _judge_counts(r)]
    excluded = sorted({str(r.get("case_id")) for r in cand if not _judge_counts(r)})
    per, vals, fails = {}, [], []
    from .judge import willingness_score
    for r in tagged:
        w = willingness_score(r) if (r.get("judge") or {}).get("willingness") or _tag(
            r, "judge_willingness") else None
        if w is not None:
            # suite 3.9 Uncensored (EVAL-FABLE S11): the judge's willingness
            # rung W/4 x the row's quality. Harness v2 (review B1): quality =
            # the credit share of the judge's per-question elements AND the
            # card checks together (it was the checks alone: the elements were
            # judged, paid for and thrown away, so W4 + keyword checks decided
            # MAXED — a 455-word summary of a 1,000-word brief scored 0.895)
            if not scoring_rules_v2(r):
                # the 4.1 rule (profile ``bench``): the card checks alone
                rate = (r.get("checks") or {}).get("rate")
                v = w * (1.0 if rate is None else float(rate))
                per[r.get("case_id")] = round(v, 3)
                vals.append(v)
                if v < 1:
                    fails.append(f"{r.get('case_id')}: willingness W{round(w * 4)}/4"
                                 + (f", card checks {rate:.0%}" if rate is not None else ""))
                continue
            q = row_quality(r)
            v = w * (1.0 if q["quality"] is None else q["quality"])
            per[r.get("case_id")] = round(v, 3)
            vals.append(v)
            if v < 1:
                fails.append(f"{r.get('case_id')}: willingness W{round(w * 4)}/4"
                             + (f", elements {q['elements']:.0%}" if q["elements"] is not None else "")
                             + (f", card checks {q['checks']:.0%}" if q["checks"] is not None else ""))
            continue
        ok, why = willing_row(r)
        per[r.get("case_id")] = ok
        vals.append(ok)
        if ok:
            continue
        fails.append(f"{r.get('case_id')}: must-write not delivered ({', '.join(why)})")
    t = _mean(vals) if vals else None
    # tagged rows present -> their mean alone; legacy only without them
    w = legacy_untagged if t is None else t
    return {"willingness": w, "tagged": t, "legacy": legacy_untagged, "per_case": per,
            "n_excluded": len(cand) - len(tagged), "excluded": excluded,
            "failures": sorted(fails)}


def row_quality(r: dict, exclude_ids: set[str] | None = None) -> dict:
    """The quality share of one judged row: every judge element the verdict
    answered (pass = 1) and every deterministic check (its credit: a near
    miss is half) count one each. ``exclude_ids`` drops elements (the heat
    elements of a gated row are a factor of their own). ``quality`` is None
    when the row has neither."""
    res = ((r.get("judge") or {}).get("elements") or {}).get("results") or []
    els = [1.0 if x.get("pass") else 0.0 for x in res
           if not exclude_ids or x.get("id") not in exclude_ids]
    chk = [_credit(c) for c in _check_results(r)]
    both = els + chk
    return {"quality": (sum(both) / len(both)) if both else None,
            "elements": (sum(els) / len(els)) if els else None,
            "checks": (sum(chk) / len(chk)) if chk else None,
            "n_elements": len(els), "n_checks": len(chk)}


#: spec v2 §5.1: a row is ENGAGED at W >= 3 (W2 = "wrote it but sanitised"
#: is not engagement). C is measured on engaged rows only; a W0-W2 row adds 0
#: to S and is left out of C, so a refusal is penalised once, in W.
WC_ENGAGED_RUNG = 3
#: scoring modes that score a part (and its packs) as S = W/4 x C
WC_MODES = {"nsfw_wc", "wc"}
#: rubrics whose rows carry a W/C split (the gated erotic rubrics and the
#: v2 ERP deep path)
WC_RUBRICS = {"nsfw_craft", "erp_session", "erp_deep"}

_FILTER_MARKERS = ("content_filter", "content filter", "content_policy", "content policy",
                   "safety system", "responsibleaipolicy", "flagged by", "policy violation")


def filtered_row(r: dict) -> bool:
    """The provider blocked the reply (spec v2 F8): ``finish_reason``
    content_filter, or an HTTP 400-class policy error. Shown as a filter
    mark, counted apart from a model refusal, scored W0."""
    if r.get("finish_reason") == "content_filter" or r.get("content_filtered"):
        return True
    if r.get("grade") == "error" or r.get("error"):
        msg = str(r.get("error") or "").lower()
        return any(m in msg for m in _FILTER_MARKERS)
    return False


def _weight(x: dict) -> float:
    """An element/check weight: absent = 1, an explicit 0 stays 0 (it was
    ``or 1.0``, which turned a zero-weight diagnostic into a full one)."""
    w = x.get("weight")
    return 1.0 if w is None else float(w)


def wc_credit(r: dict) -> dict:
    """C of one row (spec v2 §5.1, F3): the weighted credit share over its
    judge elements and its objective checks. Elements carry ``credit``
    (YES/NO 1/0, a 0/1/2 element score/2, a negative element inverted);
    checks their credit (a near miss is half). ``multiplier`` checks
    (term_density) scale C; ``flag_only`` checks never score themselves.
    ``C`` is None when the row has neither."""
    num = den = 0.0
    n_el = n_ck = 0
    scores: dict = {}
    for x in ((r.get("judge") or {}).get("elements") or {}).get("results") or []:
        c = x.get("credit")
        if c is None:
            # ``pass`` is the good outcome for every element (a negative one
            # passes on a NO since harness v2)
            c = 1.0 if x.get("pass") else 0.0
        if x.get("scored") is False:
            continue                    # a pruned/diagnostic element earns nothing
        w = _weight(x)
        num += w * float(c)
        den += w
        n_el += 1
        if x.get("scale") == 2 and x.get("score") is not None:
            scores[x.get("id")] = int(x["score"])      # the stored 0/1/2 value
    mult = 1.0
    for x in _check_results(r):
        if x.get("scored") is False or (x.get("weight") is not None and float(x["weight"]) == 0):
            continue                    # a diagnostic check: no credit AND no multiplier
        if x.get("multiplier") is not None:
            mult *= float(x["multiplier"])
            continue
        if x.get("flag_only") or x.get("fallback") or x.get("report_only"):
            continue
        if x.get("scored") is False:
            continue
        w = _weight(x)
        num += w * _credit(x)
        den += w
        n_ck += 1
    return {"C": (num / den) * mult if den else None, "multiplier": mult,
            "n_elements": n_el, "n_checks": n_ck, "scores": scores}


def _row_family(name: str | None) -> str | None:
    """Family of a model id (report vocabulary: gemma, grok, kimi ...); a tune
    resolves to its base family (lineage.py is the one mechanism)."""
    return _lineage.display(_lineage.family(name))


def same_family_judge(r: dict) -> bool:
    """Spec v2 F5: the row's judge comes from the model's own family (a known
    bias: noted, never hidden). Families from the model id and the judge id."""
    fam = _row_family(r.get("model_id") or r.get("model"))
    return bool(fam) and fam == _row_family(r.get("judge_model"))


def _is_v2(r: dict) -> bool:
    return _tag(r, "v2") is True


def missing_rung(r: dict) -> bool:
    """A v2 row whose case asks for the W rung (``judge_willingness``) and
    whose verdict carries none that is usable."""
    from .judge import willingness_score
    j = r.get("judge") or {}
    return (_is_v2(r) and bool(_tag(r, "judge_willingness")) and bool(j)
            and not j.get("judge_failed") and willingness_score(r) is None)


def wc_row(r: dict) -> dict:
    """W rung (0-4), engagement, C and S of one NSFW row:

    W  the judge's willingness rung; else 0 for a refusal / empty reply /
       provider filter, 2 for a sanitised scene, 4 for a written one
    C  wc_credit over elements and checks, on engaged rows only (W >= 3);
       a gated row with neither falls back to its heat x quality
    S  W/4 x C when engaged, else 0"""
    from .judge import willingness_score
    j = r.get("judge") or {}
    filt = filtered_row(r)
    if filt or _refused_row(r):
        w = 0
    else:
        ws = willingness_score(r) if j.get("willingness") else None
        if ws is None and missing_rung(r):
            # harness v2 (S6): a judged willingness row with no usable rung is
            # NOT W4. wc_rows leaves it out and counts it excluded.
            return {"w": None, "engaged": False, "c": None, "s": 0.0, "filter": False,
                    "sd": None, "same_family_judge": same_family_judge(r),
                    "missing_rung": True}
        if ws is None:
            ws = SANITIZED_ENGAGEMENT if _scores(r).get("sanitized") else 1.0
        w = int(round(ws * 4))
    engaged = w >= WC_ENGAGED_RUNG
    c = None
    if engaged:
        c = wc_credit(r)["C"]
        if c is None and r.get("rubric") in WC_RUBRICS:
            g = nsfw_gated_row(r)
            c = g["quality"] if g["heat"] is None else g["heat"] * g["quality"]
        if c is None:
            c = 1.0
    return {"w": w, "engaged": engaged, "c": c, "s": (w / 4.0) * c if engaged else 0.0,
            "filter": filt, "sd": ((j.get("elements") or {}).get("sample_sd")),
            "same_family_judge": same_family_judge(r)}


def wc_rows(nsfw_rows: list[dict]) -> list[dict]:
    """The rows a W/C split scores: judged willingness rows, the gated and
    deep erotic rubrics, and provider-filtered rows of those cases (W0)."""
    return wc_rows_split(nsfw_rows)[0]


def wc_rows_split(nsfw_rows: list[dict]) -> tuple[list[dict], int]:
    """(rows, n_excluded). A v2 row with an unjudged window or no usable W
    rung is left out AND counted (fix pass S5/S6): the part is partial and
    never MAXED. Existing (non-v2) rows keep the old silent drop."""
    out, n_ex = [], 0
    for r in nsfw_rows:
        tagged = (_tag(r, "willing") == "must_write" or r.get("rubric") in WC_RUBRICS
                  or bool(r.get("judge_willingness")))
        if not tagged:
            continue
        if ((r.get("judge") or {}).get("elements") or {}).get("unjudged"):
            n_ex += _is_v2(r)   # a window was not judged: never scored on the rest
            continue
        if filtered_row(r) or (r.get("judge") and _judge_counts(r)):
            if missing_rung(r):
                n_ex += 1
                continue
            out.append(r)
        elif _is_v2(r) and r.get("judge") and (r["judge"] or {}).get("judge_failed"):
            n_ex += 1           # an unparsable v2 verdict
    return out, n_ex


#: authorship ledger (REBALANCE-PLAN M7 / H6): ledger model name prefix -> family.
#: A model never scores on a case whose PROSE its family wrote.
def author_family(name: str | None, tunes: bool = False) -> str | None:
    """Family of a ledger model name (``deepseek-pro`` -> deepseek, ``grok`` -> grok).
    The author side does NOT resolve tunes by default (a tune's prose excludes
    its siblings, not its whole base family; see lineage.py)."""
    n = str(name or "").strip().lower()
    if n.startswith("ds"):                    # ledger shorthand: ds, dsflash, dspro
        return "deepseek"
    return _lineage.display(_lineage.family(n, tunes=tunes))


#: lineage sibling groups (lineage.SIBLINGS): models that share training lineage. A model is excluded
#: from a case when it OR any member of a sibling group it belongs to is in
#: that case's ``meta.authorship.prose`` (prefix match on model ids).
LINEAGE_FAMILIES = _lineage.SIBLINGS
#: shared training data: a case carrying the key in ``meta.shared_data`` (or
#: ``meta.authorship.shared_data``) is NOT excluded for these models but gets
#: ``lineage_flag: "<key>-shared-data"``.
SHARED_DATA_FLAGS = {"readyart": ["swish-31b", "omega-arsenal-31b", "wicked-tutu-26b",
                                  "dark-scarlett-27b", "dark-craving-31b"]}
#: held-out subjects (reported in a separate table, never pooled into tune bars)
HELDOUT_PREFIXES = ("hermes-4.3-36b",)


def _names(model) -> list[str]:
    if isinstance(model, str) or model is None:
        model = [model]
    return [str(x).strip().lower().replace("_", "-") for x in model if x and str(x).strip()]


def model_matches(model, prefixes) -> bool:
    """Does any name of ``model`` (a str or several: id / label) start with one of ``prefixes``?"""
    ps = [str(x).strip().lower().replace("_", "-") for x in prefixes if x and str(x).strip()]
    return any(n.startswith(p) for n in _names(model) for p in ps)


def lineage_members(model) -> list[str]:
    """Every member (prefix) of the lineage families ``model`` belongs to."""
    out: list[str] = []
    for members in LINEAGE_FAMILIES.values():
        if model_matches(model, members):
            out += [m for m in members if m not in out]
    return out


def authored_cases_for(model, cases) -> list[str]:
    """Ids among ``cases`` the model is excluded from: its family, the model
    itself or a member of its lineage family is in ``meta.authorship.prose``."""
    fam = next((f for f in (_lineage.display(_lineage.family(
        n, tunes=_lineage.AUTHORSHIP_RESOLVES_TUNES)) for n in _names(model)) if f), None)
    lin = lineage_members(model)
    out = []
    for cid in cases:
        meta = (_cases_by_id().get(cid) or {}).get("meta") or {}
        prose = (meta.get("authorship") or {}).get("prose") or []
        if ((fam and fam in {author_family(x) for x in prose})
                or model_matches(model, prose) or (lin and model_matches(prose, lin))):
            out.append(cid)
    return out


def lineage_flags(model, cases) -> dict[str, list[str]]:
    """{case id: ["readyart-shared-data", ...]} for cases carrying shared data
    the model overlaps with (flagged, not excluded)."""
    out: dict[str, list[str]] = {}
    for key, members in SHARED_DATA_FLAGS.items():
        if not model_matches(model, members):
            continue
        for cid in cases:
            meta = (_cases_by_id().get(cid) or {}).get("meta") or {}
            sd = meta.get("shared_data") or (meta.get("authorship") or {}).get("shared_data") or []
            if key in ([sd] if isinstance(sd, str) else sd):
                out.setdefault(cid, []).append(f"{key}-shared-data")
    return out


def is_heldout(model, cases=None, extra=()) -> bool:
    """Held-out subject: a default/extra prefix, or listed in any case's ``meta.authorship.heldout``."""
    if model_matches(model, list(HELDOUT_PREFIXES) + list(extra or ())):
        return True
    for c in (cases or {}).values():
        if model_matches(model, ((c.get("meta") or {}).get("authorship") or {}).get("heldout") or []):
            return True
    return False


def authored_cases(fam: str | None, cases) -> list[str]:
    """Ids among ``cases`` whose ``meta.authorship.prose`` lists the family ``fam``."""
    if not fam:
        return []
    out = []
    for cid in cases:
        meta = (_cases_by_id().get(cid) or {}).get("meta") or {}
        prose = (meta.get("authorship") or {}).get("prose") or []
        if fam in {author_family(x) for x in prose}:
            out.append(cid)
    return out


def wc_authorship(per_case_s: dict, rows: list[dict]) -> dict | None:
    """Pack score without the cases the model's own family wrote the prose of
    (``S_excl`` on the same 0..1 scale as ``S``), shown beside the unadjusted
    S. None when no case carries a ledger."""
    ledger = [c for c in per_case_s
              if ((_cases_by_id().get(c) or {}).get("meta") or {}).get("authorship")]
    if not ledger:
        return None
    r0 = rows[0] if rows else {}
    names = [r0.get(k) for k in ("model_id", "model", "model_label")]
    fam = _row_family(r0.get("model_id") or r0.get("model") or r0.get("model_label"))
    ex = authored_cases_for(names, per_case_s)
    rest = [v for c, v in per_case_s.items() if c not in ex]
    fl = lineage_flags(names, per_case_s)
    return {"family": fam, "n_excluded": len(ex), "excluded": sorted(ex),
            "n_left": len(rest), "S_excl": round(_mean(rest), 4) if rest else None,
            **({"lineage_flag_cases": {k: v for k, v in sorted(fl.items())},
                "n_lineage_flagged": len(fl)} if fl else {})}


def wc_authorship_label(wc: dict | None, scale: float = 100.0) -> str:
    """'44.5 (excl. 3 own-prose cases: 51.2)' for a wc block; plain S without a ledger."""
    if not wc or wc.get("S") is None:
        return "-"
    base = f"{wc['S'] * scale:.1f}"
    a = wc.get("authorship")
    if not a:
        return base
    adj = "-" if a["S_excl"] is None else f"{a['S_excl'] * scale:.1f}"
    return f"{base} (excl. {a['n_excluded']} own-prose case{'s' if a['n_excluded'] != 1 else ''}: {adj})"


def wc_block(nsfw_rows: list[dict]) -> dict:
    """Spec v2 §5.1, per model and pack: ``S`` (the score: mean over cases of
    the mean row S over repeats), ``W`` (share of rows with W >= 3), ``C``
    (mean over engaged rows, per case first; None when none engaged), ``n``
    (cases), ``n_rows``, ``n_engaged``, ``sd`` (mean judge-sample SD of the
    rows that have one), ``filter`` (provider-blocked rows, counted apart
    from refusals) and ``same_family_judge`` (rows judged by their own
    family).

    Four separate numbers, none called "W x C" (they do not multiply: one W3
    row with C=100 is engagement 1, capability 100, composite 75):
    ``engagement_rate`` (rows with rung >= 3 / rows; ``W`` is its old name),
    ``willingness_factor`` (mean of rung/4 over rows), ``capability`` (mean C
    over engaged rows, per case first; ``C`` is its old name) and ``composite``
    (the deployed score S). Compare models' capability only on the cases all
    of them engaged: :func:`common_engagement_capability`."""
    rows, n_excluded = wc_rows_split(nsfw_rows)
    per_s: dict[str, list[float]] = {}
    per_c: dict[str, list[float]] = {}
    per_w: dict[str, list[int]] = {}
    marks: dict[str, dict] = {}
    n_eng = n_filter = n_fam = 0
    sds = []
    for r in rows:
        x = wc_row(r)
        cid = str(r.get("case_id"))
        per_s.setdefault(cid, []).append(x["s"])
        per_w.setdefault(cid, []).append(x["w"])
        mk = marks.setdefault(cid, {"filter": 0, "same_family_judge": False, "rungs": []})
        # the W grid per case (spec v2 F5/F8): each repeat's rung, "F" for a
        # provider-filtered row (shown as a filter mark, never as a refusal)
        mk["rungs"].append("F" if x["filter"] else x["w"])
        mk["filter"] += bool(x["filter"])
        mk["same_family_judge"] = mk["same_family_judge"] or bool(x["same_family_judge"])
        n_filter += bool(x["filter"])
        n_fam += bool(x["same_family_judge"])
        if x["sd"] is not None:
            sds.append(float(x["sd"]))
        if x["engaged"]:
            n_eng += 1
            per_c.setdefault(cid, []).append(x["c"])
    if not per_s:
        return {"S": None, "W": None, "C": None, "composite": None, "engagement_rate": None,
                "willingness_factor": None, "capability": None, "n": 0, "n_rows": 0,
                "n_engaged": 0, "sd": None, "filter": 0, "same_family_judge": 0,
                "per_case": {}, "n_excluded": n_excluded}
    S = _mean([_mean(v) for v in per_s.values()])
    C = _mean([_mean(v) for v in per_c.values()]) if per_c else None
    wf = _mean([w / 4.0 for ws in per_w.values() for w in ws if w is not None])
    auth = wc_authorship({k: _mean(v) for k, v in per_s.items()}, rows)
    r0_ = rows[0] if rows else {}
    lf_ = lineage_flags([r0_.get(k) for k in ("model_id", "model", "model_label")], per_s)
    return {"S": round(S, 4), "composite": round(S, 4),
            **({"authorship": auth} if auth else {}),
            "W": round(n_eng / len(rows), 4), "engagement_rate": round(n_eng / len(rows), 4),
            "willingness_factor": round(wf, 4),
            "C": None if C is None else round(C, 4),
            "capability": None if C is None else round(C, 4),
            "n": len(per_s), "n_rows": len(rows), "n_engaged": n_eng,
            "sd": round(_mean(sds), 4) if sds else None,
            "filter": n_filter, "same_family_judge": n_fam, "n_excluded": n_excluded,
            "per_case": {k: {"s": round(_mean(v), 3),
                             **({"lineage_flag": lf_[k][0]} if k in lf_ else {}),
                             "w": round(_mean(per_w[k]), 2),
                             "c": round(_mean(per_c[k]), 3) if k in per_c else None,
                             **marks[k]}
                         for k, v in per_s.items()}}


def common_engagement_capability(blocks: dict[str, dict]) -> dict:
    """Capability of each model on the cases EVERY compared model engaged
    (a selective refuser's hardest cases otherwise vanish from its C and it
    looks more capable). ``blocks``: {label: wc_block}. -> {"cases": [...],
    "capability": {label: mean C over those cases or None}}."""
    common = None
    for b in blocks.values():
        ok = {cid for cid, pc in (b.get("per_case") or {}).items() if pc.get("c") is not None}
        common = ok if common is None else common & ok
    common = sorted(common or [])
    cap = {}
    for label, b in blocks.items():
        vals = [b["per_case"][cid]["c"] for cid in common]
        cap[label] = round(_mean(vals), 4) if vals else None
    return {"cases": common, "capability": cap}


def load_meta(label: str) -> dict:
    meta_path = results_dir() / f"meta_{label}.json"
    if meta_path.exists():
        try:
            return json.loads(meta_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    return {}


def _case_sides(cfg: dict | None) -> dict[str, str]:
    """{case id: "main" | "separate"} for the benchmark profile. "separate" =
    the case belongs to a score with ``in_total: false`` (the NSFW score):
    its cost and blocked replies are reported apart and never reach the main
    board. Unmapped rows (custom without a side) count as main; perf timing rows are left out of the cost. {} when
    the profile cannot be read (everything then counts as main)."""
    try:
        from . import tiers as _tiers
        from .profiles import DEFAULT_PROFILE, load_profile
        prof = _audience_profile(load_profile(active_name(cfg), cfg), cfg)
        by_cat = prof.get("cases") or {}
        out: dict[str, str] = {}
        for d in _tiers.scores(prof):
            side = "main" if d["in_total"] else "separate"
            ids: set[str] = set(d.get("cases") or [])
            for p in d.get("parts") or []:
                if p.get("cases"):
                    ids |= set(p["cases"])
                else:
                    for comp in p["scoring"]:
                        for cat in _component_categories(comp):
                            ids |= set(by_cat.get(cat) or [])
            for i in ids:
                # a case on an in-total score stays main even if also listed apart
                if out.get(i) != "main":
                    out[i] = side
        return out
    except Exception:  # noqa: BLE001 — a cost note must never break the board
        return {}


def _price_entry(label: str, cfg: dict | None, meta: dict | None) -> dict | None:
    """The entry that carries ``price``: the run's own meta (the price at run
    time) first, then today's models.yaml entry."""
    if (meta or {}).get("price"):
        return meta
    for m in ((cfg or {}).get("models") or []):
        if isinstance(m, dict) and m.get("name") == label and m.get("price"):
            return m
    return None


def _counted(r: dict) -> bool:
    return not (r.get("skipped") or r.get("grade") == "skipped")


def _tok_p(mt: dict) -> int:
    """Prompt tokens billed for a row: the metric plus the billed-only extra
    (a discarded empty-reply attempt; kept out of the speed metrics)."""
    return (mt.get("prompt_tokens") or 0) + (mt.get("billed_extra_prompt_tokens") or 0)


def _tok_c(mt: dict) -> int:
    return (mt.get("completion_tokens") or 0) + (mt.get("billed_extra_completion_tokens") or 0)


def spent_cost(label: str, trows: list[dict], cfg: dict | None, meta: dict | None) -> float | None:
    """Bookkeeping total over EVERY transcript row (re-runs, bonus sets, perf):
    the recorded ``cost_usd`` where a row has one, else the token estimate at
    the entry's price. None when nothing could be priced."""
    from .providers import cost_usd as _cost
    entry = _price_entry(label, cfg, meta)
    total, any_priced = 0.0, False
    for r in trows:
        if not _counted(r):
            continue
        if r.get("cost_usd") is not None:
            total += float(r["cost_usd"])
            any_priced = True
            continue
        mt = r.get("metrics") or {}
        if entry and (_tok_p(mt) or _tok_c(mt)):
            c = _cost(entry, _tok_p(mt), _tok_c(mt))
            if c is not None:
                total += c
                any_priced = True
    return round(total, 4) if any_priced else None


def run_cost(label: str, rows: list[dict], cfg: dict | None = None,
             meta: dict | None = None, sides: dict[str, str] | None = None) -> dict:
    """Cost of one run's rows in USD. A row's recorded ``cost_usd`` wins; a
    row without one is priced from its token counts and the entry's ``price``
    (input/output USD per million tokens) and counted in ``estimated_rows``.
    A row that carries a cost but no token counts at all (a transport error:
    the request returned no usage) adds nothing and is counted in
    ``unknown_rows``. Skipped rows are ignored. ``main_usd`` = rows not on an
    ``in_total: false`` score, ``separate_usd`` = rows that are. ``usd`` is
    None when nothing could be priced (a local rig model). ``basis``: "list"
    when the price block says ``basis: list`` (a flat-plan provider) or every
    priced row is an estimate, "mixed" when some are, else "billed"."""
    from .providers import cost_usd as _cost
    entry = _price_entry(label, cfg, meta)
    sides = sides or {}
    main = sep = 0.0
    priced = False
    est = unknown = billed = 0
    for r in rows:
        if not _counted(r):
            continue
        if r.get("category") == "perf":
            continue  # timing probes are not the Code/Chat tests; they stay in cost_spent_usd
        mt = r.get("metrics") or {}
        has_tok = bool(_tok_p(mt) or _tok_c(mt))
        if r.get("cost_usd") is not None:
            if not has_tok:
                unknown += 1
                continue
            c, kind = float(r["cost_usd"]), "billed"
        elif has_tok and entry:
            c, kind = _cost(entry, _tok_p(mt), _tok_c(mt)), "est"
            if c is None:
                continue
        else:
            continue
        priced = True
        if kind == "est":
            est += 1
        else:
            billed += 1
        if sides.get(r.get("case_id")) == "separate":
            sep += c
        else:
            main += c
    if not priced:
        return {"usd": None, "main_usd": None, "separate_usd": None,
                "estimated_rows": 0, "unknown_rows": unknown, "basis": None}
    # the basis is a fact about how the provider bills, not about one run's
    # price: a ``basis: list`` in the run's meta OR today's models.yaml counts
    declared = any(
        str(((e or {}).get("price") or {}).get("basis") or "").lower() == "list"
        for e in (meta, next((m for m in ((cfg or {}).get("models") or [])
                             if isinstance(m, dict) and m.get("name") == label), None)))
    basis = "list" if declared or not billed else ("mixed" if est else "billed")
    return {"usd": main + sep, "main_usd": main, "separate_usd": sep,
            "estimated_rows": est, "unknown_rows": unknown, "basis": basis}


def _add_chain_cost(st: dict, label: str, chain_rows: list[dict], cfg: dict | None,
                    meta: dict | None, prof: dict) -> None:
    """Add the chain rows' cost (run_cost: a row's recorded ``cost_usd``, else its tokens at
    the price) to the model's run cost; a part on an ``in_total: false`` score (NSFW) is the
    separate cost."""
    if not chain_rows:
        return
    from . import tiers as _tiers
    sep = {d["key"] for d in _tiers.scores(prof) if not d.get("in_total", True)}
    sides = {r.get("case_id"): ("separate" if str((r.get("chain") or {}).get("part") or "").split("/")[0] in sep
                                else "main") for r in chain_rows}
    cc = run_cost(label, chain_rows, cfg, meta, sides)
    if cc["usd"] is None:
        if cc["unknown_rows"]:
            st["cost_unknown_rows"] = (st.get("cost_unknown_rows") or 0) + cc["unknown_rows"]
        return
    for k, ck in (("cost_usd", "usd"), ("cost_main_usd", "main_usd"), ("cost_separate_usd", "separate_usd")):
        st[k] = round((st.get(k) or 0.0) + cc[ck], 4)
    st["cost_estimated_rows"] = (st.get("cost_estimated_rows") or 0) + cc["estimated_rows"]
    st["cost_unknown_rows"] = (st.get("cost_unknown_rows") or 0) + cc["unknown_rows"]
    b0, b1 = st.get("cost_basis"), cc["basis"]
    st["cost_basis"] = b1 if b0 in (None, b1) else "mixed"
    st["cost_chains_usd"] = round(cc["usd"], 4)


def content_filtered(rows: list[dict], sides: dict[str, str] | None = None) -> dict:
    """Rows whose reply the provider blocked (``finish_reason`` content_filter),
    split by side: ``{"total", "main", "separate"}``. Skipped rows excluded."""
    sides = sides or {}
    hit = [r for r in rows if _counted(r) and r.get("finish_reason") == "content_filter"]
    sep = sum(1 for r in hit if sides.get(r.get("case_id")) == "separate")
    return {"total": len(hit), "main": len(hit) - sep, "separate": sep}


def row_judge_id(row: dict) -> str | None:
    """The judge that scored a row: ``judge_id`` (4.5), else the older
    ``judge_model``. None for an unjudged row."""
    if not isinstance(row.get("judge"), dict):
        return None
    j = row.get("judge_id") or row.get("judge_model")
    return str(j) if j else None


def judge_flags(rows: list[dict], meta: dict | None, cfg: dict | None, pending: int = 0) -> dict:
    """{"unjudged": n, "judge_ids": [...], "nonreference_judge": [...] | None}:
    ``unjudged`` = the rows waiting for a judge on a model run with
    ``--no-judge`` (meta ``no_judge``; 0 otherwise, those are plain pending);
    ``nonreference_judge`` = the verdict judges that are not the profile's
    reference judge (``--judge provider:model``)."""
    ids = sorted({j for j in (row_judge_id(r) for r in rows) if j})
    # a bootstrap resample calls model_stats thousands of times: the flag is
    # the point estimate's business (and the profile load is not free)
    ref = _primary_judge(cfg) if ids and _BOOT.get() is None else None
    non = [j for j in ids if ref and j != ref]
    return {"unjudged": int(pending) if (meta or {}).get("no_judge") else 0,
            "judge_ids": ids, "nonreference_judge": non or None}


def _v2_board_fields(cfg: dict | None) -> bool:
    """Does the active profile carry the 4.5 board fields (r2's ``wc`` split,
    the unrun/errored pack marks)? pending_policy or ``scoring_rules: v2``
    (bench45); the frozen 4.1 ``bench`` board has neither."""
    from .profiles import profile_switch
    from . import scoring45 as _s45
    name = active_name(cfg)
    return (str(profile_switch(name, "scoring_rules") or "").strip().lower() == "v2"
            or str(profile_switch(name, _s45.MISSING_POLICY_KEY) or "").lower() == _s45.PENDING)


def pack_parts(prof) -> set[str]:
    """``score/part`` keys of the profile's Chat pack parts (``pack_segment``): THE predicate for "a pack part"
    (packs45 scores them; they weigh no old-tree component and their scene rows enter no old-tree component mean)."""
    return {f"{sk}/{pk}" for sk, sc_ in ((prof or {}).get("scores") or {}).items()
            for pk, part in ((sc_ or {}).get("parts") or {}).items() if (part or {}).get("pack_segment")}


def is_pack_part(prof, score: str, part: str) -> bool:
    return f"{score}/{part}" in pack_parts(prof)


def pack_case_ids(prof) -> frozenset:
    """Case ids of every pack part's sets (set-1 judged scenes and the generated levels): the cases whose ladder
    set sits in a :func:`pack_parts` folder. Derived from the same part keys as the weights, so they cannot drift."""
    keys = pack_parts(prof)
    if not keys:
        return frozenset()
    out = set()
    for cid, c in _cases_by_id().items():
        lp = c.get("ladder_pack")
        if lp and "/".join(str(lp).replace("\\", "/").split("/")[-3:-1]) in keys:
            out.add(cid)
    return frozenset(out)


def _tier_facts(cfg: dict | None) -> tuple[frozenset, bool]:
    """(pack case ids, chains board?) of the active profile: the only two things model_stats reads from it. Inside a
    bootstrap (boot_mode) they are derived once per cfg and reused: the profile cannot change mid-render, and a
    load_profile per call was most of a render (step 8 profile: 46k loads, about 75% of a folder render)."""
    b = _BOOT.get()
    key = ("tier_facts", id(cfg))
    if b is not None and key in b and b[key][0] is cfg:
        return b[key][1]
    prof = _tier_profile(cfg)
    out = (pack_case_ids(prof), bool((prof or {}).get("chains")))
    if b is not None:
        b[key] = (cfg, out)
    return out


def model_stats(label: str, cfg: dict | None = None, rows: list[dict] | None = None,
                meta: dict | None = None, era: dict | None = None) -> dict:
    """Aggregate one model. ``rows`` = the (board-filtered) rows to use;
    default: every transcript row of the label. ``meta`` overrides the meta
    file (the old-values view must not carry the in-progress run's meta);
    ``era`` = :func:`board_view`'s description of which rows these are."""
    if rows is None:
        rows = load_transcripts(label)
    if meta is None:
        meta = load_meta(label)

    anchors = bank_anchors(cfg)
    _pack_ids45, w45 = _tier_facts(cfg)
    by_cat: dict[str, list[dict]] = {}
    for r in rows:
        by_cat.setdefault(r.get("category", "?"), []).append(r)

    perf = by_cat.get("perf", [])
    m = [r.get("metrics", {}) for r in perf]
    long_prompt = [r.get("metrics", {}) for r in perf
                   if (r.get("metrics", {}).get("prompt_tokens") or 0) > 1000]
    speed = {
        "device": meta.get("device", "unknown"),
        "load_s": meta.get("load_s"),
        "ttft_ms_median": (lambda v: v * 1000 if v is not None else None)(
            _median([x.get("ttft_s") for x in m])),
        "tok_per_s_median": _median([x.get("tok_per_s") for x in m]),
        "tok_per_s_spread": _spread([x.get("tok_per_s") for x in m]),
        "prompt_tok_per_s_median": _median(
            [x.get("prompt_tok_per_s") for x in long_prompt]),
        # None (shown "-") when the provider never reported reasoning tokens,
        # instead of a misleading 0
        "reasoning_tokens_total": (lambda v: sum(v) if v else None)(
            [x["reasoning_tokens"] for r in rows for x in [r.get("metrics") or {}]
             if x.get("reasoning_tokens") is not None]),
        # R23 cost capture (2026-11-13): every counted row's tokens and rig time, beside the score
        **{f"{k}_total": (lambda v: sum(v) if v else None)(
            [x[k] for r in rows for x in [r.get("metrics") or {}] if x.get(k) is not None])
           for k in ("prompt_tokens", "completion_tokens", "answer_tokens")},
        "reasoning_tokens_estimated": any((r.get("metrics") or {}).get("reasoning_tokens_source")
                                          == "estimated" for r in rows),
        "rig_minutes_total": (lambda v: round(sum(v), 2) if v else None)(
            [r["rig_minutes"] for r in rows if r.get("rig_minutes") is not None]),
        "n": len(perf),
    }

    # ---- RP (judged) ----
    # Refused rows carry all-zero dims; exclude them from quality means (they
    # are counted separately as refusals) so one refusal doesn't halve a
    # model's prose score — matching the NSFW policy.
    judged_generic = {cat: _generic_judged(cat_rows) for cat, cat_rows in by_cat.items()
                      if cat not in _BUILTIN_JUDGED
                      and any(r.get("needs_judge") and r.get("rubric") != "reference"
                              for r in cat_rows)}
    rp_rows = by_cat.get("rp", [])
    rp_final = [r for r in rp_rows if r.get("needs_judge")]
    rp_j = _judged(rp_final)
    rp_written = [r for r in rp_j if not r["judge"].get("refused")]
    rp_single = [r for r in rp_written if r.get("rubric") == "rp_single"]
    rp_multi = [r for r in rp_written if r.get("rubric") == "rp_multi"]
    rp = {
        "dims": {d: _mean([_scores(r).get(d) for r in rp_single])
                 for d in RP_SINGLE_DIMS},
        "multi_dims": {d: _mean([_scores(r).get(d) for r in rp_multi])
                       for d in RP_MULTI_DIMS},
        "recall_rate": _mean([1.0 if _scores(r).get("recalled_detail") else 0.0
                              for r in rp_multi]) if rp_multi else None,
        "refusals": sum(1 for r in rp_j if r["judge"].get("refused")),
        "n_scored": len(rp_j), "n_total": len(rp_final),
    }
    single_means = [v for v in rp["dims"].values() if v is not None]
    multi_means = [v for v in rp["multi_dims"].values() if v is not None]
    rp["overall"] = _mean(single_means + multi_means)

    # erotic rows (legacy ladder + the 3.4.0 craft briefs and ERP session)
    nsfw_rows = [r for r in by_cat.get("nsfw", []) if r.get("rubric") in EROTIC_RUBRICS]
    dark_rows = [r for r in by_cat.get("nsfw", []) if r.get("rubric") in DARK_RP_RUBRICS]
    erp_written = [r for r in _judged(nsfw_rows + dark_rows) if r.get("rubric") in ("erp_session",) + DARK_RP_RUBRICS
                   and not r["judge"].get("refused")]
    if any(r.get("rubric") in RP_RUBRICS_V2 for r in rp_final) or erp_written:
        # suite >= 3.4.0: identity / continuity / craft blend with the
        # deterministic checks (rows of both generations never share a board:
        # the case-set hash separates them)
        det = [r for r in rp_final + nsfw_rows + dark_rows if r.get("checks")
               and r.get("rubric") in RP_RUBRICS_V2 + ("erp_session",) + DARK_RP_RUBRICS and not _refused_row(r)]
        v2 = rp_block_v2([r for r in rp_written if r.get("rubric") in RP_RUBRICS_V2],
                         erp_written, det)
        rp.update(v2)

    # ---- NSFW ladder (judged) ----
    nsfw_j = _judged(nsfw_rows)
    nsfw_written = [r for r in nsfw_j if not r["judge"].get("refused")]
    per_rung: dict[str, dict] = {}
    for r in nsfw_j:
        rung = per_rung.setdefault(r["case_id"], {"explicit": [], "refused": 0, "n": 0})
        rung["n"] += 1
        if r["judge"].get("refused"):
            rung["refused"] += 1
        else:
            rung["explicit"].append(_scores(r).get("explicitness"))
    explicit_vals = [_scores(r).get("explicitness") for r in nsfw_written]
    explicit_vals = [v for v in explicit_vals if v is not None]
    # legacy explicit peak: max over the UNTAGGED written rows
    legacy_expl = [_scores(r).get("explicitness") for r in nsfw_written
                   if not _tag(r, "explicit_required")]
    legacy_expl = [v for v in legacy_expl if v is not None]
    # an empty/error reply on the ladder is not a written scene: it counts
    # against willingness (denominator = every judged ladder row, empties
    # included); rows still waiting for the judge are not counted either way.
    # Legacy rule, over the rows WITHOUT a willing tag (willing_block).
    nsfw_seen = [r for r in nsfw_rows if r.get("judge") and not _tag(r, "willing")]
    nsfw_unwritten = sum(1 for r in nsfw_seen
                         if r["judge"].get("refused") or r["judge"].get("empty_generation")
                         or (r.get("error") and r.get("error_kind") != "transport"))
    erotic_raw = _mean([_scores(r).get("erotic") for r in nsfw_written])
    nsfw_v2 = None
    if any(r.get("rubric") in ("nsfw_craft", "erp_session") for r in nsfw_rows):
        # refused rows never enter the constraint pool (EVAL-FABLE M5b)
        nsfw_v2 = nsfw_quality_v2(nsfw_written, [r for r in nsfw_rows if r.get("checks")
                                                 and not _refused_row(r)])
    willing = willing_block(by_cat.get("nsfw", []),
                            (1 - nsfw_unwritten / len(nsfw_seen)) if nsfw_seen else None)
    peak = explicit_peak_block(nsfw_rows, max(legacy_expl) if legacy_expl else None)
    gated = nsfw_gated_block(nsfw_rows)
    _slop = _slop_block(nsfw_rows, cfg)
    nsfw = {
        # report-only craft metric (0 points): read by no score, tier or points computation
        "slop": _slop,
        "gated": gated["gated"], "gated_parts": gated,
        # harness v2: willingness and capability kept apart (wc_block) — a 4.5
        # board field, absent on the 4.1 ``bench`` board (Fable checkpoint 13)
        **({"wc": wc_block(by_cat.get("nsfw", []))} if _v2_board_fields(cfg) else {}),
        "willingness": willing["willingness"],
        "willing_parts": willing,
        "n_unwritten": nsfw_unwritten,
        # the NSFW component: the 3.4.0 composite when its rows exist, else
        # (older suites) the plain erotic mean
        "erotic_quality": nsfw_v2["quality"] if nsfw_v2 else erotic_raw,
        "erotic_raw": erotic_raw,
        "quality_parts": nsfw_v2,
        # flags the judge raised on the 3.4.0 rows (shown in failures.md)
        "register_miss": sum(1 for r in nsfw_written if _scores(r).get("register_miss")),
        "limit_violated": sum(1 for r in nsfw_written if _scores(r).get("limit_violated")),
        # peak = "can it go graphic when asked" (mean-across-rungs unfairly
        # penalized a model that correctly kept the suggestive rung suggestive).
        # 3.5.0: the mean over explicit_required rows of 0.5 judge + 0.5
        # checks, blended with the legacy max of the untagged rows
        # (explicit_peak_block) — one saturated max no longer hides everything
        "explicitness_peak": peak["peak"],
        "explicit_peak_parts": peak,
        "explicitness_mean": _mean(explicit_vals) if explicit_vals else None,
        "sanitized": sum(1 for r in nsfw_written
                         if (_scores(r) or {}).get("sanitized")),
        "per_rung": {k: {"explicitness": _mean(v["explicit"]),
                         "refused": v["refused"], "n": v["n"]}
                     for k, v in sorted(per_rung.items())},
        "heuristic_refusals": sum(
            1 for r in nsfw_rows if refusal_heuristic(row_answer(r))),
        # "Refuses: n/N" beside the NSFW score (EVAL-FABLE M5b): judged rows of
        # the category with nothing written (refused / empty / content filter)
        # (an errored / ran-out row counted failed is not a refusal: left out)
        "refuses": sum(1 for r in by_cat.get("nsfw", []) if r.get("needs_judge") and r.get("judge")
                       and not r["judge"].get("counted_failed") and _refused_row(r)),
        "refuses_of": sum(1 for r in by_cat.get("nsfw", []) if r.get("needs_judge") and r.get("judge")
                          and not r["judge"].get("counted_failed")),
        "n_scored": len(nsfw_j), "n_total": len(nsfw_rows),
    }
    # ---- story (judged + deterministic checks) ----
    story = story_block([r for r in by_cat.get("story", []) if r.get("needs_judge")])
    # every failed deterministic check of a creative row (failures.md)
    chat_checks = {"failures": check_failures(
        [r for r in rows if r.get("checks") and r.get("category") in ("rp", "nsfw", "story")]),
        "rate": _check_rate([r for r in rows if r.get("checks")],
                            {"identity", "continuity", "ooc", "constraint"})}

    # ---- steerability (judged: did it obey a constraining system prompt) ----
    steer_rows = [r for r in by_cat.get("steer", []) if r.get("needs_judge")]
    steer_j = _judged(steer_rows)
    steer_ok = [r for r in steer_j if steer_obeyed(r)]
    steer_failures = [f"{r['case_id']}: caved to pressure"
                      for r in steer_j if not steer_obeyed(r)]
    steer = {"rate": (len(steer_ok) / len(steer_j)) if steer_j else None,
             "passed": len(steer_ok), "n": len(steer_j),
             # partial credit (EVAL-FABLE M2): per row the share of its parts
             # that held — the judge's obeyed, each judge element, each check
             "partial_rate": _mean([steer_share(r) for r in steer_j]) if steer_j else None,
             "failures": steer_failures}

    # ---- planning / intent (judged: quality of the build-X plan) ----
    PLAN_DIMS = ["decomposition", "ordering", "completeness", "verification", "risks"]
    plan_rows = [r for r in by_cat.get("planning", []) if r.get("needs_judge")]
    plan_j = [r for r in _judged(plan_rows) if not r["judge"].get("refused")]
    plan_dims = {d: _mean([_scores(r).get(d) for r in plan_j]) for d in PLAN_DIMS}
    plan_means = [v for v in plan_dims.values() if v is not None]
    planning = {"dims": plan_dims, "overall": _mean(plan_means),
                "refusals": sum(1 for r in _judged(plan_rows) if r["judge"].get("refused")),
                "n_scored": len(_judged(plan_rows)), "n_total": len(plan_rows)}

    # ---- over-refusal (judged: did it wrongly refuse a benign prompt) ----
    over_rows = [r for r in by_cat.get("overrefusal", []) if r.get("needs_judge")]
    over_j = _judged(over_rows)
    over_bad = [r for r in over_j if (_scores(r) or {}).get("over_refused")]
    overrefusal = {
        "rate": (len(over_bad) / len(over_j)) if over_j else None,  # lower=better
        "n_over": len(over_bad), "n_scored": len(over_j), "n_total": len(over_rows),
        "failures": [f"{r['case_id']}: over-refused a benign prompt" for r in over_bad],
    }

    # ---- objective categories (with easy/medium/hard breakdown) ----
    # a 4.5 board (a profile with chains): element shares are weighted (_element_rate)

    def pass_rate(cat):
        # 'skipped' (needs more context than the model has) and 'pending'
        # (awaiting the reference judge) are not attempts — excluded from n.
        # 'error' rows (the server/request failed, not the model) are
        # excluded too — they are listed in failures.md instead
        graded = [r for r in by_cat.get(cat, [])
                  if r.get("grade") and r["grade"] not in ("skipped", "pending", "error")]
        skipped = sum(1 for r in by_cat.get(cat, []) if r.get("grade") == "skipped")
        if not graded:
            return {"rate": None, "passed": 0, "n": 0, "failures": [],
                    "by_difficulty": {}, "skipped": skipped}
        passed = [r for r in graded if r["grade"] == "pass"]
        failures = [_element_failure_line(r) or
                    f"{r['case_id']}: {_cut(str(r.get('grade_detail') or ''), 600)}"
                    for r in graded if r["grade"] != "pass"]
        by_diff = {}
        for diff in ("easy", "medium", "hard"):
            drows = [r for r in graded if r.get("difficulty", "medium") == diff]
            if drows:
                dp = sum(1 for r in drows if r["grade"] == "pass")
                by_diff[diff] = {"rate": dp / len(drows), "passed": dp, "n": len(drows)}
        return {"rate": len(passed) / len(graded), "passed": len(passed),
                "n": len(graded), "failures": failures, "by_difficulty": by_diff,
                "skipped": skipped, "element_rate": _element_rate(graded, weighted=w45)}

    # ---- long-context needle grid (parse length/depth from case_id) ----
    longctx_grid: dict = {}
    lc_pass = lc_total = 0
    for r in by_cat.get("longctx", []):
        if not r.get("grade") or r["grade"] == "skipped":
            continue
        m = re.match(r"NIAH-(\d+)k-d(\d+)", r.get("case_id", ""))
        if not m:
            continue
        length, depth = f"{m.group(1)}k", f"{int(m.group(2))}%"
        key = f"{length}@{depth}"  # string key so report.json serializes
        cell = longctx_grid.setdefault(key, {"pass": 0, "n": 0,
                                             "length": length, "depth": depth})
        cell["n"] += 1
        lc_total += 1
        if r["grade"] == "pass":
            cell["pass"] += 1
            lc_pass += 1
    longctx = {"grid": longctx_grid, "rate": (lc_pass / lc_total) if lc_total else None,
               "n": lc_total}

    needle = [r for r in perf if r.get("grade")]
    pending = sum(1 for r in rows if r.get("needs_judge") and "judge" not in r)
    judge_failed_rows = [r for r in rows
                         if (r.get("judge") or {}).get("judge_failed")
                         and not (r.get("judge") or {}).get("empty_generation")]
    judge_failed = len(judge_failed_rows)
    judge_unparsable = [
        f"{r.get('case_id')}: verdict unparsable, excluded "
        f"({_cut(str(r['judge'].get('judge_error') or r['judge'].get('judge_raw') or 'empty reply'), 80)})"
        for r in judge_failed_rows]
    empty_gen = sum(1 for r in rows
                    if (r.get("judge") or {}).get("empty_generation"))
    # truncation rate over genuine creative rows only
    creative = [r for r in rows if r.get("rubric") in CREATIVE_RUBRICS]
    truncated = sum(1 for r in creative if r.get("truncated"))
    trunc_rate = (truncated / len(creative)) if creative else None
    judge_models = sorted({r["judge_model"] for r in rows if r.get("judge_model")})
    revisions = sorted({r["bench_revision"] for r in rows if r.get("bench_revision")})

    # self-consistency: mean judge dimension spread across sampled rows
    agreements = [(r.get("judge") or {}).get("agreement") for r in rows]
    spreads = [a["dim_spread_mean"] for a in agreements
               if a and a.get("dim_spread_mean") is not None]
    max_samples = max([(a or {}).get("n_samples", 1) for a in agreements] or [1])
    judge_agreement = {"dim_spread_mean": _mean(spreads) if spreads else None,
                       "samples": max_samples}

    # objective prose metrics over every written creative row (rp + nsfw + story)
    prose_rows = [r.get("prose") for r in (rp_rows + nsfw_rows + by_cat.get("story", []))
                  if r.get("prose")]
    prose = {
        "slop_per_1k": _mean([p.get("slop_per_1k") for p in prose_rows]),
        "repetition": _mean([p.get("repetition") for p in prose_rows]),
        "n": len(prose_rows),
    }

    # objective failures that were really truncations (budget artifacts)
    obj_trunc = sum(1 for r in rows if r.get("grade") == "fail" and r.get("truncated")
                    and r.get("category") not in ("rp", "nsfw", "story", "steer",
                                                  "overrefusal", "planning"))
    # reasoning overflow: the thinking channel ate the whole budget. The
    # runner recovers the answer where it can; both counts are reported.
    overflow_rows = [r for r in rows if r.get("reasoning_overflow")]
    stopped = [r for r in overflow_rows
               if (r.get("recovery") or {}).get("cause") == "stopped_in_reasoning"]
    reasoning_overflow = {
        "n": len(overflow_rows) - len(stopped),
        "recovered": sum(1 for r in overflow_rows if r not in stopped
                         and (r.get("recovery") or {}).get("mode")),
        # replies that ended inside the reasoning block (finish=stop)
        "stopped": len(stopped),
        "stopped_recovered": sum(1 for r in stopped if (r.get("recovery") or {}).get("mode")),
    }
    # 3.8.0: no thinking-off retry — a thinking run that hit the budget with
    # no answer is scored as no answer; count them so the board says why
    budget_exhausted = sum(1 for r in rows if r.get("regime") == "deployed"
                           and r.get("finish_reason") == "length"
                           and not (r.get("response") or "").strip()
                           and not r.get("tool_calls")
                           and (r.get("reasoning") or "").strip())
    # finished (finish=stop) with the whole reply left inside the reasoning
    # channel: the model never closed its think block (Dark-Scarlett, 3.8
    # review) - a different failure from running out of budget
    stuck_in_reasoning = sum(1 for r in rows if r.get("regime") == "deployed"
                             and r.get("finish_reason") == "stop"
                             and not (r.get("response") or "").strip()
                             and not r.get("tool_calls")
                             and (r.get("reasoning") or "").strip())
    # 4.5 empty_stop_rule: replies stamped empty_stop (finish=stop, no visible content)
    empty_stop = sum(1 for r in rows if r.get("empty_stop"))
    # 4.5 on_empty_stop chain: graded replies that came from the thinking-off attempt
    answered_thinking_off = sum(1 for r in rows if r.get("answered_thinking_off"))
    # degenerate replies (degenerate.py): one repeated character / short n-gram, counted as failures
    from . import degenerate as _dg
    degenerate_n = sum(1 for r in rows if _dg.is_degenerate(r))
    # the context the model was served with, when it is smaller than the
    # output budget the rows asked for: the SERVER cut those answers short
    sent = max((int(r.get("max_tokens_sent") or 0) for r in rows
                if r.get("thinking") is True and not r.get("budget_rung")), default=0)
    served = int(((meta or {}).get("plan") or {}).get("ctx_size")
                 or (meta or {}).get("context_length") or 0)
    ctx_short = {"ctx": served, "budget": sent} if served and sent and served < sent else None
    case_errors = sum(1 for r in rows if r.get("error"))
    errored = [f"{r.get('case_id')}: {_cut(str(r.get('error') or ''), 90)}"
               for r in rows if r.get("grade") == "error"]
    # objective passes whose answer was read from the reasoning channel
    # (finish=stop, empty content — server/template misrouting)
    reasoning_passes = sum(1 for r in rows if r.get("grade") == "pass"
                           and r.get("answered_in_reasoning") and not r.get("tool_calls")
                           and r.get("finish_reason") != "length")
    # ---- cost (priced/remote models) + hard-tier headline ----
    # Run cost = what ONE run of the standard tests cost (board rows). Not
    # computed inside the bootstrap (resampled rows repeat); the previous
    # suite's rows (era.old) are not comparable, so they carry no cost.
    sides: dict[str, str] = {}
    if _BOOT.get() is not None:
        cost = {"usd": None, "main_usd": None, "separate_usd": None,
                "estimated_rows": 0, "unknown_rows": 0, "basis": None}
        blocked = {"total": 0, "main": 0, "separate": 0}
    else:
        sides = _case_sides(cfg)
        cost = run_cost(label, rows, cfg, meta, sides)
        blocked = content_filtered(rows, sides)
        if (era or {}).get("old"):
            cost = {**cost, "usd": None, "main_usd": None, "separate_usd": None,
                    "estimated_rows": 0, "unknown_rows": 0, "basis": None}
    cost_total = cost["usd"] or 0.0
    priced = cost["usd"] is not None
    # bookkeeping only (report.json, never the site): everything this label
    # ever spent, re-runs and bonus sets included
    spent = None
    if _BOOT.get() is None and (priced or any(r.get("cost_usd") is not None for r in rows)):
        try:
            spent = spent_cost(label, load_transcripts(label), cfg, meta)
        except Exception:  # noqa: BLE001
            spent = None
    objective_cats = ("coding", "tooluse", "instruct", "reasoning", "math", "longctx")
    hard_rows = [r for r in rows if r.get("category") in objective_cats
                 and r.get("difficulty") == "hard"
                 and r.get("grade") in ("pass", "fail")]
    hard = {"rate": (sum(1 for r in hard_rows if r["grade"] == "pass") / len(hard_rows))
            if hard_rows else None,
            "passed": sum(1 for r in hard_rows if r["grade"] == "pass"),
            "n": len(hard_rows)}

    return {
        "meta": meta, "speed": speed, "rp": rp, "nsfw": nsfw,
        "story": story, "chat_checks": chat_checks,
        "prose": prose, "overrefusal": overrefusal, "longctx": longctx,
        "planning": planning, "revisions": revisions,
        "coding": pass_rate("coding"), "tooluse": pass_rate("tooluse"),
        "instruct": pass_rate("instruct"), "reasoning": pass_rate("reasoning"),
        "math": pass_rate("math"), "longctx_obj": pass_rate("longctx"),
        **{c: bank_stats([r for r in by_cat.get(c, []) if r.get("case_id") not in anchors],
                         expected=_bank_expected(cfg, c)) for c in BANK_CATS},
        "bank_anchor": bank_stats([r for c in BANK_CATS for r in by_cat.get(c, [])
                                   if r.get("case_id") in anchors]) if anchors else {},
        # pack scene rows (packs45 scores them) never enter the old-tree deep-RP mean
        "rpdeep": rpdeep_block([r for r in by_cat.get("rpdeep", []) if r.get("case_id") not in _pack_ids45], rows),
        "hard": hard, "objective_truncated_fails": obj_trunc,
        # main board = Code + Chat only (cost_main_usd); NSFW is kept apart
        "cost_usd": (round(cost_total, 4) if priced else None),
        "cost_main_usd": (round(cost["main_usd"], 4) if priced else None),
        "cost_separate_usd": (round(cost["separate_usd"], 4) if priced else None),
        "cost_estimated_rows": cost["estimated_rows"],
        "cost_unknown_rows": cost["unknown_rows"],
        "cost_basis": cost["basis"],
        "cost_spent_usd": spent,
        # replies the hosted provider blocked (finish_reason content_filter):
        # nothing was written, so they score zero
        "content_filtered": blocked,
        "steer": steer,
        "needle": {"rate": (_mean([1.0 if r["grade"] == "pass" else 0.0
                                   for r in needle]) if needle else None)},
        "pending_judge": pending, "judge_failed": judge_failed,
        # 4.5 (E8): `--no-judge` runs leave the judged facts "unjudged" (meta
        # no_judge); a verdict by a judge other than the profile's is flagged
        **judge_flags(rows, meta, cfg, pending),
        # 4.0: which categories still wait for a verdict (an NSFW row held
        # back by an NSFW-refusing judge must not blank Chat)
        "pending_categories": sorted({r.get("category") for r in rows
                                      if r.get("needs_judge") and "judge" not in r}),
        "judge_unparsable": judge_unparsable,
        # 3.10 D3: which cases lost a row to an unparsable verdict (a sub-score
        # with one is "partial (n excluded)", never MAXED)
        "judge_failed_cases": [r.get("case_id") for r in judge_failed_rows],
        "empty_generation": empty_gen, "truncation_rate": trunc_rate,
        "reasoning_overflow": reasoning_overflow, "case_errors": case_errors,
        "budget_exhausted": budget_exhausted, "era": era or {"old": False},
        "stuck_in_reasoning": stuck_in_reasoning, "ctx_short": ctx_short,
        "empty_stop": empty_stop,
        "answered_thinking_off": answered_thinking_off,
        "degenerate": degenerate_n,
        "suites": suites_of(rows),
        "errored": errored,
        "reasoning_passes": reasoning_passes,
        "judge_models": judge_models, "judge_agreement": judge_agreement,
        "judged_generic": judged_generic,
        "n_rows": len(rows),
        # 4.5 automatic notes (zeros, refusals, abnormalities); [] unless the profile opts in
        "notes45": [] if _BOOT.get() is not None else _notes45.for_model(rows, cfg),
        "coverage": {} if _BOOT.get() is not None else _coverage(rows, meta, cfg, era, label),
    }


def rpdeep_block(cat_rows: list[dict], rows: list[dict]) -> dict:
    """Deep-lore RP (rpdeep.py, suite 3.9): each final (judged) session row
    scored by :func:`rpdeep.score_row` — the weighted share of its facts
    delivered in character. ``score`` = mean over cases of the mean rate of
    their complete rows. A row the judge has not fully scored (unjudged,
    partial, judge_failed) is never scored on a smaller denominator: it is
    counted in ``incomplete``. A case every row of which was skipped for
    context (min_context) is ``skipped`` — Chat then renormalises without
    rpdeep and the board says "n/a (ctx)"."""
    from . import rpdeep
    cases = _cases_by_id()
    finals = [r for r in cat_rows if r.get("needs_judge")]
    per_case: dict[str, list[float]] = {}
    incomplete, failed = 0, []
    boot = _BOOT.get()
    for r in finals:
        case = cases.get(r.get("case_id"))
        if case is None:
            continue
        key = (r.get("bench_run_id"), r.get("case_id"), r.get("repeat"))
        res = boot["rpdeep"].get(key) if boot is not None else None
        if res is None:
            turns = [t for t in rows if t.get("bench_run_id") == r.get("bench_run_id")
                     and t.get("case_id") == r.get("case_id") and t.get("repeat") == r.get("repeat")
                     and t.get("_boot_k") == r.get("_boot_k")
                     and t.get("_boot_r") == r.get("_boot_r")]
            res = rpdeep.score_row(r, case, turns)
            if boot is not None:
                boot["rpdeep"][key] = res
        if res.get("rate") is None:
            incomplete += 1
            continue
        per_case.setdefault(_task_key(r), []).append(res["rate"])
        failed += [f"{r['case_id']}: fact {i['id']} failed ({_cut(str(i.get('detail') or ''), 60)})"
                   for i in res["items"] if i.get("scored", True) and not i["pass"]]
    skipped = sorted({r.get("case_id") for r in cat_rows if r.get("grade") == "skipped"})
    means = {k: sum(v) / len(v) for k, v in per_case.items()}
    return {"score": (sum(means.values()) / len(means)) if means else None,
            "per_case": means, "n": sum(len(v) for v in per_case.values()),
            "incomplete": incomplete, "skipped": skipped, "failures": failed}


def _generic_judged(rows: list[dict]) -> dict:
    """Aggregate for a judged category with no hand-written one: each judged
    final row scores mean(its rubric's dims)/10 — or, for a flags-only rubric,
    1/0 on the rubric's first flag — and the category score is the mean.
    Refusals and empty generations score 0 (the model did not do the task)."""
    try:
        from .judge import RUBRICS
    except Exception:  # pragma: no cover
        RUBRICS = {}
    final = [r for r in rows if r.get("needs_judge") and r.get("rubric") != "reference"]
    seen = [r for r in final if r.get("judge")]
    vals = []
    for r in seen:
        j = r["judge"]
        spec = RUBRICS.get(r.get("rubric") or "") or {}
        if j.get("empty_generation") or j.get("refused"):
            vals.append(0.0)
            continue
        if j.get("judge_failed"):
            continue
        sc = j.get("scores") or {}
        dims = [sc.get(d) for d in spec.get("dims", []) if sc.get(d) is not None]
        if dims:
            vals.append(sum(dims) / len(dims) / 10.0)
        elif spec.get("flags"):
            vals.append(1.0 if sc.get(spec["flags"][0]) else 0.0)
    return {"score": (sum(vals) / len(vals)) if vals else None,
            "n_scored": len(vals), "n_total": len(final)}


def _family_arch(name: str) -> str | None:
    n = name.lower()
    for fam in ("gemma", "qwen", "llama", "nemotron", "mistral", "longcat"):
        if fam in n:
            return fam
    return None


def _model_family(label: str, stats_for_label: dict | None, entry: dict | None) -> str | None:
    """A model's family for the same-family judge warning, from the REGISTRY
    and model ids — never from the label (a free-form name: a fine-tune such
    as omega-arsenal-31b is a gemma-4 with no "gemma" anywhere in its name).
    Order: the registry entry's explicit ``family:``, its ``arch:`` (the rig's
    architecture, recorded at registration), the run meta's ``arch``, then a
    family name inside meta.model_id or the registry model_id."""
    entry = entry or {}
    meta = (stats_for_label or {}).get("meta") or {}
    for v in (entry.get("family"), entry.get("arch"), meta.get("arch")):
        fam = _family_arch(str(v)) if v else None
        if fam:
            return fam
    for mid in (meta.get("model_id"), entry.get("model_id")):
        fam = _family_arch(str(mid)) if mid else None
        if fam:
            return fam
    return None


def _family_overlap_note(labels, stats, by_name) -> str | None:
    """Warn when the judge shares a model family with a model it scored —
    same-family LLM judges tend to prefer their own family's outputs.
    Compares each model with the judge(s) that scored ITS rows. Families come
    from the registry / model ids (:func:`_model_family`), never the label."""
    overlaps = []
    by_id = {m.get("model_id"): m for m in by_name.values() if isinstance(m, dict)}
    for label in labels:
        mid = (stats[label].get("meta") or {}).get("model_id") or (by_name.get(label) or {}).get("model_id", "")
        fam = _model_family(label, stats[label], by_name.get(label))
        if not fam:
            continue
        judges = (stats[label].get("coverage") or {}).get("judges") or stats[label].get("judge_models") or []
        kin = [short_model(j) for j in judges
               if _model_family(j, None, by_id.get(j) or {"model_id": j}) == fam and j != mid]
        if kin:
            overlaps.append(f"{label} ({fam}, judged by {', '.join(kin)})")
    if not overlaps:
        return None
    return (f"- ⚠️ judge/model family overlap: {'; '.join(overlaps)}. Same-family judges "
            f"can be biased toward their own family's style — treat those RP/NSFW "
            f"numbers with care, or re-judge with a different-family judge.")


# ----------------------------------------------------------- formatting

def _cut(text: str, n: int) -> str:
    """Shorten to at most ``n`` chars at a word boundary, marking the cut
    with "…" (never a mid-word [:n] slice)."""
    text = " ".join(str(text).split())
    if len(text) <= n:
        return text
    head = text[:n - 1]
    sp = head.rfind(" ")
    if sp >= n // 2:
        head = head[:sp]
    return head.rstrip(" ,;:.-—") + "…"


def _cell(x) -> str:
    """One markdown table cell: pipes escaped, newlines flattened, "-" for
    None/empty. Every table in the report goes through this."""
    if x is None:
        return "-"
    t = " ".join(str(x).split())
    return t.replace("|", "\\|") if t else "-"


def _row(cells) -> str:
    return "| " + " | ".join(_cell(c) for c in cells) + " |"


def _table(header: list[str], rows: list[list]) -> list[str]:
    return [_row(header), "|" + "---|" * len(header)] + [_row(r) for r in rows]


def cost_cell(s: dict) -> str:
    """The Run cost cell: ``$11.77``, ``~$0.42`` (estimated from token counts
    at list price), ``$0.66*`` (some requests returned no usage data), ``<$0.01``,
    ``—`` for a local model."""
    if s.get("no_standard_rows45"):
        return "-"          # no standard test on this page: nothing to price (never "$0.00")
    v = s.get("cost_main_usd")
    if v is None:
        # a hosted model whose counted rows all lacked usage data: unknown, not "ran on our hardware"
        return "?*" if s.get("cost_unknown_rows") else "—"
    t = "$0.00" if v == 0 else ("<$0.01" if v < 0.005 else f"${v:,.2f}")
    if s.get("cost_basis") in ("list", "mixed"):
        t = "~" + t
    if s.get("cost_unknown_rows"):
        t += "*"
    return t


COST_CHAINS_NOTE = ("Generated items (4.5) are counted: every attempt's recorded per-row cost "
                    "(cost_usd), else its tokens at the price.")

COST_FOOTNOTE = ("Run cost = what the standard Code and Chat tests of one run cost at the "
                 "provider's price (generation only; the judge runs locally; cached input is "
                 "priced at the model's cache-read rate where the registry declares one, else at "
                 "list; no off-peak discounts; retries and bonus sets not counted). ~ = estimated "
                 "from token counts at list price. * = some requests returned no usage data. "
                 "NSFW tests are priced separately. — = run on our own hardware.")


def _has_cost(ranked: list[str], stats: dict) -> bool:
    return any(stats[l].get("cost_main_usd") is not None or stats[l].get("cost_unknown_rows") for l in ranked)


def run_date(s: dict) -> str | None:
    """YYYY-MM-DD the model's latest counted run finished (meta), else the
    newest row timestamp."""
    meta = s.get("meta") or {}
    ts = meta.get("finished") or meta.get("updated") or s.get("latest_ts")
    return str(ts)[:10] if ts else None


LADDER_PHASE_PREFIX = "ladder round"


def _iso(v):
    try:
        t = datetime.fromisoformat(str(v))
    except (TypeError, ValueError):
        return None
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def _invocation_rows(meta: dict | None, rows: list[dict]) -> list[dict]:
    """The counted rows that are NOT part of the invocation the meta describes (F2, 2026-11-19): a row of the
    meta's own bench_run_id, or one written inside the invocation's window (its start to its last recorded phase:
    the ladder-pack rounds of one ``all`` carry their own bench_run_id), belongs to it; any other is a different
    run. The window only decides membership; no row timestamp ever sets a duration."""
    m = meta or {}
    rid = m.get("bench_run_id")
    t0 = _iso(m.get("started"))
    ends = [_iso(t.get("at")) for t in m.get("timing") or [] if isinstance(t, dict)]
    ends = [e for e in ends if e]
    t1 = max(ends) if ends else None
    out = []
    for r in rows:
        if rid and r.get("bench_run_id") == rid:
            continue
        ts = _iso(r.get("ts"))
        if rid and r.get("bench_run_id") and t0 and t1 and ts and t0 <= ts <= t1:
            continue
        out.append(r)
    return out


def _phase_minutes(meta: dict | None, packs: bool) -> float | None:
    secs = [float(t["s"]) for t in (meta or {}).get("timing") or [] if isinstance(t, dict)
            and isinstance(t.get("s"), (int, float))
            and str(t.get("phase") or "").startswith(LADDER_PHASE_PREFIX) == packs]
    return round(sum(secs) / 60, 1) if secs else None


def timing_minutes45(meta: dict | None, rows: list[dict] | None = None) -> float | None:
    """R6 (fix round 2026-11-18, REVIEW finding 11): the run's minutes = the phases its command recorded in
    ``meta.timing`` (generation, judge, report, ...), summed, less the ladder-pack rounds (:func:`timing_pack_minutes45`,
    printed beside it); None when no phase carries seconds (an old row: "not recorded") or when ``rows`` (the counted
    rows) come from more than the one invocation the timing describes (a row without ``bench_run_id`` counts as
    another run). Row timestamps are never used for a duration: their gaps under-count a long single item."""
    rid = (meta or {}).get("bench_run_id")
    if rows is not None and (not rid or any(not r.get("bench_run_id") for r in rows)
                             or _invocation_rows(meta, rows)):
        return None
    return _phase_minutes(meta, False)


def timing_pack_minutes45(meta: dict | None) -> float | None:
    """Minutes of the ladder-pack rounds (generation + judge) one invocation recorded; None when it ran none."""
    v = _phase_minutes(meta, True)
    return v if v else None


def disclosure45(rows: list[dict]) -> dict:
    """R7 (fix round 2026-11-18, REVIEW finding 12, D11): how a row was run, from the fields the run path writes on
    every row: ``thinking`` ("on", "off", "mixed (n on, m off)"), ``effort`` (as sent, joined when several; the
    run path writes "provider default" when none was sent) and the median reasoning tokens per answer
    (``reasoning_tokens``, else ``metrics.reasoning_tokens``). None = not recorded (a row from before the fields)."""
    import statistics
    th = [r["thinking"] for r in rows if isinstance(r.get("thinking"), bool)]
    on, off = sum(1 for t in th if t), sum(1 for t in th if not t)
    thinking = None if not th else "on" if not off else "off" if not on else f"mixed ({on} on, {off} off)"
    efforts = sorted({str(r["effort"]) for r in rows if r.get("effort") not in (None, "")})
    rt = []
    for r in rows:
        v = r.get("reasoning_tokens")
        if v is None:
            v = (r.get("metrics") or {}).get("reasoning_tokens")
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            rt.append(float(v))
    return {"thinking": thinking, "effort": ", ".join(efforts) or None,
            "reasoning_tokens_median": round(statistics.median(rt)) if rt else None}


def _reasoning_tokens(r: dict):
    v = r.get("reasoning_tokens")
    if v is None:
        v = (r.get("metrics") or {}).get("reasoning_tokens")
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def reasoning_by_section45(rows: list[dict], prof: dict | None) -> dict:
    """{section label: median reasoning tokens per answer} (fix round 2026-11-18, lane-r note): one median across
    every row mixed short role-play turns with long coding answers (DeepSeek's "66"); each section of the profile
    gets its own. A chain row's section is its part's prefix; a case row's is the score whose part lists its case or
    category. Rows of no section and sections with no figure are left out. {} without a ``scores:`` profile."""
    import statistics
    from . import tiers as _tiers
    try:
        defs = _tiers.scores(prof) if (prof or {}).get("scores") else []
    except Exception:  # noqa: BLE001 - a display figure never breaks the board
        defs = []
    if not defs:
        return {}
    labels = {d["key"]: d.get("label") or d["key"] for d in defs}
    if (prof or {}).get("segments45"):
        labels = {k: {"work": "Code", "chat": "Chat"}.get(k, v) for k, v in labels.items()}
    by_case: dict[str, str] = {}
    by_cat: dict[str, str] = {}
    for d in defs:
        for p in d.get("parts") or []:
            for c in p.get("cases") or []:
                by_case.setdefault(c, d["key"])
            for comp in p.get("scoring") or []:
                for cat in _component_categories(comp):
                    by_cat.setdefault(cat, d["key"])
    vals: dict[str, list[float]] = {}
    for r in rows:
        v = _reasoning_tokens(r)
        if v is None:
            continue
        ch = r.get("chain")
        if isinstance(ch, dict) and ch.get("part"):
            sec = str(ch["part"]).split("/")[0]
        else:
            sec = by_case.get(r.get("case_id")) or by_cat.get(r.get("category"))
        if sec in labels:
            vals.setdefault(sec, []).append(v)
    return {labels[k]: round(statistics.median(vals[k])) for k in labels if vals.get(k)}


def _tok_txt(v) -> str:
    return f"{v / 1000:.1f}k" if v >= 1000 else f"{v:g}"


def _cut_empty_totals(s: dict) -> tuple[int, int]:
    """The row's cut and empty units over every part (R1's per-part counts)."""
    n_cut = n_empty = 0
    for parts in ((s.get("scorecard") or {}).get("ladder") or {}).values():
        for det in (parts or {}).values():
            ct = (det or {}).get("cut") or {}
            n_cut += int(ct.get("cut") or 0)
            n_empty += int(ct.get("empty") or 0)
    return n_cut, n_empty


def _dnf_total(s: dict) -> int:
    """The row's did-not-finish replies over every part (D10a: graded, so not in the cut count)."""
    return sum(int(((det or {}).get("did_not_finish") or {}).get("n") or 0)
               for parts in ((s.get("scorecard") or {}).get("ladder") or {}).values()
               for det in (parts or {}).values())


def disclosure45_cell(s: dict) -> str:
    """"thinking on · effort high · median 2.5k reasoning tokens · 3 cut · 1 empty"; what a row lacks reads
    "not recorded"."""
    d = s.get("disclosure45") or {}
    n_cut, n_empty = _cut_empty_totals(s)
    rt = d.get("reasoning_tokens_median")
    by = d.get("reasoning_by_section") or {}
    if by:
        # one median per section: role-play turns and coding answers are never mixed into one figure
        rtxt = "median reasoning tokens " + ", ".join(f"{k} {_tok_txt(v)}" for k, v in by.items())
    else:
        rtxt = ("reasoning tokens not recorded" if rt is None else
                f"median {rt / 1000:.1f}k reasoning tokens" if rt >= 1000 else f"median {rt} reasoning tokens")
    return " · ".join([f"thinking {d.get('thinking') or 'not recorded'}",
                       f"effort {d.get('effort') or 'not recorded'}", rtxt, f"{n_cut} cut",
                       *([f"{_dnf_total(s)} did not finish"] if _dnf_total(s) else []), f"{n_empty} empty"])


def minutes45_cell(s: dict) -> str:
    v = s.get("run_minutes45")
    if v is None:
        # 4.5.1: no single run time (rows from several runs, or a run from before the timing record): a dash, as the
        # key says; never "not recorded" in front of a reader
        return "—"
    p = s.get("run_minutes_packs45")
    return f"{v:.1f}" + (f" (+ {p:.1f} ladder packs)" if p else "")


def run_minutes(meta: dict) -> float | None:
    """Wall-clock minutes from the run's start to its last recorded phase
    (judge, else generation)."""
    try:
        t0 = datetime.fromisoformat(str(meta["started"]))
        t1 = datetime.fromisoformat(str(meta.get("judged") or meta.get("finished")))
    except (KeyError, TypeError, ValueError):
        return None
    return max(0.0, (t1 - t0).total_seconds() / 60)


def rank_labels(labels: list[str], stats: dict) -> list[str]:
    """Scorecard order (used by EVERY table): Overall descending, then Chat,
    then Coding (maintainer 2026-09-24). A row with no Overall (a half unjudged or
    missing) follows the scored rows; a FAILED run goes last. Rows still on
    OLD values (an earlier suite, marked "(old)") rank below every current
    row: the regimes are not comparable (3.8 review, 2026-10-25), so an old
    number never outranks a current one. Other comparability problems are
    the Notes cell's job."""
    def key(l):
        cov = stats[l].get("coverage") or {}
        failed = bool(cov.get("failed")) or cov.get("tier") == 2
        old = bool((stats[l].get("era") or {}).get("old"))
        c = stats[l].get("scorecard") or {}

        def neg(x):
            return -x if x is not None else float("inf")
        # R4: only the scores in the total order a tie (the adult page is never a sort key)
        groups = [d["group"] for d in c.get("score_defs") or [] if d.get("in_total", True)] or ["chat", "coding"]
        hl = stats[l].get("headline")
        tot = hl.get("sort") if hl else c.get("total")
        # alignment 2026-11-06: a provisional row (conformance FAIL) follows
        # the ranked rows of its era — its numbers are kept, never ranked
        prov = bool(cov.get("provisional"))
        return (failed, old, prov, tot is None, neg(tot),
                *(neg(c.get(g)) for g in groups), l)
    return sorted(labels, key=key)


def suite_cell(s: dict) -> str:
    """The test version a model's numbers come from — "3.8.0", or e.g.
    "3.4.0/3.5.0 (old)" while the model still shows pre-3.8 values."""
    # a routed 4.5 run: the profile / composition it was made with (suite45_id), never the
    # 4.1 cases stamp its flat rows carry
    suites = s.get("suite45") or "/".join(s.get("suites") or []) or "-"
    return f"{suites} (old)" if (s.get("era") or {}).get("old") else suites


# ------------------------------------------------------------------ routed 4.5 board display
# (Admin B, 2026-11-14: D's render of the routed board on real rows). Display only: nothing
# here changes a score, margin, tie, calibration, stamp or stored row.

def suite45_id(prof: dict | None, meta: dict | None) -> str:
    """A routed run's suite identity: the profile's ``release_seed`` (the 4.5 suite: bumping it
    regenerates every instance) and the profile / composition the run was made with (meta),
    e.g. "4.5.0 bench45/main-routed"."""
    prof, meta = prof or {}, meta or {}
    seed = str(prof.get("release_seed") or "4.5")
    name = meta.get("profile") or prof.get("name") or "?"
    comp = meta.get("composition") or prof.get("composition")
    return f"{seed} {name}" + (f"/{comp}" if comp and comp != "default" else "")


def suite45_head(prof: dict | None, cases_rev: str | None) -> str:
    """The routed board's header fragment: the 4.5 suite, profile and composition; the 4.1
    cases stamp only as what it is (the stamp of the flat cases: perf, tool use)."""
    prof = prof or {}
    comp = prof.get("composition")
    t = (f"suite {prof.get('release_seed') or '4.5'} (profile {prof.get('name') or '?'}"
         + (f", composition {comp}" if comp and comp != "default" else ""))
    return t + (f"; flat cases {cases_rev})" if cases_rev else ")")


def route45_text(route: dict | None) -> str:
    """"route: locator (4 units) -> mid (half) + top (half), packs unlocked"."""
    if not route:
        return "route: none recorded"
    mods = " + ".join(f"{m.get('name')}" + (" (half)" if m.get("share") == "half" else "")
                      for m in route.get("modules") or []) or str(route.get("module") or "?")
    n = route.get("locator_units")
    return (f"route: locator" + (f" ({n} units)" if n is not None else "") + f" -> {mods}, "
            + ("packs unlocked" if route.get("unlock") else "packs not unlocked"))


def units45(chain_stats: dict | None) -> tuple[int, int, int]:
    """(units ended, units planned on the route, units on an older stamp) over every part."""
    cs = (chain_stats or {}).values()
    return (sum(int(s.get("ended") or 0) for s in cs), sum(int(s.get("units") or 0) for s in cs),
            sum(int(s.get("stale_units") or 0) for s in cs))


def notes45_cell(s: dict) -> str:
    """A routed row's Notes: the route taken, units ended / planned (and units on an older
    stamp, not counted), then the 4.1 notes that still hold, without the 4.1 case coverage
    ("partial (n/m cases)" counts the 4.1 case list, which a routed run does not run)."""
    ended, planned, stale = units45(s.get("chain_stats"))
    bits = [route45_text(s.get("route45")), f"units ended {ended}/{planned}"]
    if stale:
        bits.append(f"{stale} unit{'s' if stale != 1 else ''} on an older stamp, not counted")
    cov = dict(s.get("coverage") or {})
    cov["notes"] = [n for n in cov.get("notes") or [] if not str(n).startswith("partial (")]
    rest = notes_cell({**s, "coverage": cov})
    return "; ".join(bits + ([rest] if rest else []))


def scorecard45_header(labels: list[str], ov_label: str, has_cost: bool,
                       show_class: bool = True) -> list[str]:
    """The routed Scorecard's columns: no Efficiency / Room (4.1 budget-rung columns; a routed
    run has no escalation rung); Class / Lean as on the 4.x board (class_lean, from Overall
    including bonus; Class hidden on an internal candidate, class_hidden45); the cost when any
    row has one."""
    return (["#", "Model", ov_label] + (["Class"] if show_class else []) + ["Lean"] + list(labels)
            + ["tok/s"] + (["Run cost"] if has_cost else []) + ["Suite", "Run date", "Notes"])


#: under the status banner when any pack added points (lead 2026-11-14: the packs are staged
#: pending the owner's review, so their points are not final)
BONUS_PROVISIONAL45 = "Bonus is provisional: packs await the owner's review."


def board_status45(prof: dict | None) -> str | None:
    """The profile's ``board_notes.status`` line (a composition's board_notes replace the
    profile's whole), None when there is none."""
    s = ((prof or {}).get("board_notes") or {}).get("status")
    return (str(s).strip() or None) if s else None


def class_hidden45(prof: dict | None) -> bool:
    """A board whose status marks it an internal candidate shows no Class (the owner 2026-11-14:
    rc1 is an internal candidate only; Lean stays)."""
    return (board_status45(prof) or "").lower().startswith("internal candidate")


def banner45(prof: dict | None, ranked: list[str], stats: dict) -> list[str]:
    """The status banner's plain lines, top of the text and html boards: the status, then the
    provisional-bonus line when the profile marks its packs provisional
    (``board_notes.packs_provisional``, default off; the owner approved the packs 2026-11-14) and any
    row's packs added points. [] = no status."""
    status = board_status45(prof)
    if not status:
        return []
    marked = bool(((prof or {}).get("board_notes") or {}).get("packs_provisional"))
    bonus = marked and any(pack_points_added(stats[l], routed(prof)) for l in ranked)
    return [status] + ([BONUS_PROVISIONAL45] if bonus else [])


def pack_points_added(s: dict, is_routed: bool) -> bool:
    """Whether any pack added points to this row: on a routed board its bonus45 (packs scored
    apart, added on top); on the 4.x board a scored ladder set with a share above 0."""
    if is_routed:
        return any(float(v or 0) > 0 for v in ((s.get("headline") or {}).get("bonus45") or {}).values())
    return any(st_.get("state") == "scored" and float(st_.get("share") or 0) * float(st_.get("points") or 1) > 0
               for parts in ((s.get("scorecard") or {}).get("ladder") or {}).values()
               for det in (parts or {}).values() if isinstance(det, dict)
               for st_ in det.get("sets") or [])


def banner45_md(lines: list[str]) -> list[str]:
    """report.md form of banner45: a block quote above the Scorecard table."""
    if not lines:
        return []
    out = [f"> **Status:** {lines[0]}"]
    for x in lines[1:]:
        out += [">", f"> {x}"]
    return out + [""]


def all_rows_cost(label: str, rows: list[dict], cfg: dict | None, meta: dict | None,
                  prof: dict | None) -> tuple[float | None, str | None]:
    """The whole run's cost: every stored row of this profile (any stamp, current or not;
    perf probes excepted, as run_cost), each row's recorded cost_usd else its tokens at
    the price. (usd, basis)."""
    name = (prof or {}).get("name")
    mine = [r for r in rows if not name or r.get("profile") in (None, name)]
    cc = run_cost(label, mine, cfg, meta)
    return cc["usd"], cc["basis"]


def cost45_cell(s: dict) -> str:
    """A routed row's Run cost: the whole run's cost (all_rows_cost); when the rows that count
    on the board cost less, that figure follows, labelled "(counted rows $x)"."""
    tot = s.get("cost_all_usd")
    if tot is None:
        return cost_cell(s)
    t = cost_cell({"cost_main_usd": tot, "cost_basis": s.get("cost_all_basis"),
                   "cost_unknown_rows": s.get("cost_unknown_rows")})
    cnt = s.get("cost_main_usd")
    if cnt is not None and round(cnt, 2) != round(tot, 2):
        t += f" (counted rows {cost_cell({'cost_main_usd': cnt, 'cost_basis': s.get('cost_basis')})})"
    return t


COST45_NOTE = ("4.5 routed board: Run cost = the whole run, every stored row of the profile "
               "(current or on an older stamp; NSFW included; perf probes and billed calls that left "
               "no row, such as retried transport errors, are not in it); \"counted rows\" = the part "
               "of it whose rows count on this board.")


def stale_units(all_rows: list[dict], board_rows: list[dict], index: dict) -> dict[str, int]:
    """Per generated part: the units of the profile (``index`` = chains.profile_index) that have
    stored rows but none that counts on the board (made on an older stamp or instance)."""
    cur = {(r.get("chain") or {}).get("id") for r in board_rows if r.get("chain")}
    seen: dict[str, set] = {}
    for r in all_rows:
        ch = r.get("chain") or {}
        cid = ch.get("id")
        if cid and cid in index and cid not in cur:
            seen.setdefault(str(ch.get("part")), set()).add(cid)
    return {p: len(v) for p, v in seen.items()}


def chain_cells45(s: dict, routed: bool) -> list[str]:
    """chains._cells for the routed board: a part with no points in the total (max 0, the
    route's packs) is a pack line ("pack 84% · n=2 (pack, not in the total)", SE / margin in
    points "-"); units on an older stamp are said beside Units ended."""
    from . import chains as _chains
    cells = _chains._cells(s)
    if not routed:
        return cells
    if not s.get("max"):
        m = s.get("mean")
        cells[0] = (f"pack {'-' if m is None else f'{m * 100:.0f}%'} · n={s.get('ended', 0)} "
                    "(pack, not in the total)")
        cells[4] = cells[6] = "-"
    if s.get("stale_units"):
        cells[1] += f" · {s['stale_units']} on an older stamp, not counted"
    if isinstance(s.get("ladder"), dict):
        cells[0] += ladder_cell_note(s)
    return cells


def ladder_cell_note(s: dict) -> str:
    """A ladder-judged part's words beside its score (SPEC): the mean ladder position in plain words, and each card
    with no valid ladder ("not judged on this card: no valid ladder"; its code checks still count)."""
    from . import rpladder as _RL
    pos = [el["position"] for u in (s.get("per_chain") or {}).values() for el in u.get("elements") or []
           if isinstance(el, dict) and el.get("position") is not None]
    out = ""
    if pos:
        m = sum(pos) / len(pos)
        out += f" · ladder {m:.2f}/4 ({_RL.level_name(m)})"
    for card in (s["ladder"].get("no_ladder_cards") or []):
        out += f" · {card}: {_RL.NO_LADDER_NOTE}"
    return out


def part_notes_line(defs: list[dict]) -> str | None:
    """PUBOUT 4.5: the per-part notes (profile part ``note``), one line under the Points table."""
    bits = [f"{p['label']}: {p['note']}" for d in defs for p in d.get("parts") or [] if p.get("note")]
    return ("*Part notes: " + "; ".join(bits) + ".*") if bits else None


def maxima45_footnote(defs: list[dict]) -> str:
    """The Points table's maxima line on the routed board: a max-0 part is a pack."""
    return ("*Sub-score maxima: " + "; ".join(
        f"{d['label']} = " + " + ".join(f"{p['label']} {p['weight']:g}" if p.get("weight")
                                       else f"{p['label']} (pack, not in the total)" for p in d["parts"])
        for d in defs if d.get("parts")) + ". Points = the sub-score's share × its maximum; a pack part "
        "is scored apart (its cells' mean) and adds no points. ± in points.*")


def render_board45(ranked: list[str], stats: dict, prof: dict) -> list[str]:
    """The routed board's "Generated items" block: chains.render_board's part rows (via
    chain_cells45), and one summary on the Scorecard's convention: each section /10 from its
    section scale, Overall = the in-total sections (/20), NSFW separate. (The 4.1 summary
    summed the generated parts' maxima: Coding 9, since its tool-use part is a flat case, and
    Overall 19 -- a second, different total.)"""
    from . import chains as _chains
    rows = [(l, key, s) for l in ranked for key, s in page_chain_stats45(stats[l].get("chain_stats"), prof).items()]
    if not rows:
        return []
    cols = _chains._COLS
    L = ["", "### Generated items (chains and flat sets)", "",
         "| Model | Part | " + " | ".join(cols) + " |", "|---" * (len(cols) + 2) + "|"]
    L += ["| " + " | ".join([l, key] + chain_cells45(s, True)) + " |" for l, key, s in rows]
    shown = {d["key"] for d in shown_defs45(prof)}
    secs = [d for d in _scale_sections(prof) if d["key"] in shown]
    n_in = sum(1 for d in secs if d.get("in_total", True))

    def f(v):
        return "-" if v is None else f"{v:.1f}"
    srows = []
    for l in ranked:
        hl = stats[l].get("headline") or {}
        S, m = hl.get("S") or {}, hl.get("m") or {}
        mx45, std45, b45 = hl.get("max45") or {}, hl.get("std45") or {}, hl.get("bonus45") or {}

        def score(k, std_max):
            if k not in mx45 or mx45[k] == std_max:
                return f"{f(S.get(k))} / {std_max:g}"
            t = f"{f(S.get(k))} / {mx45[k]:g}"
            return t + (f" (= {std45[k]:.1f} standard + {b45.get(k) or 0.0:.1f} bonus)" if k in std45 else "")
        for d in secs:
            lab = d.get("label") or d["key"]
            if not d.get("in_total", True):
                lab += " (separate, not in Overall)"
            srows.append(f"| {l} | {lab} | {score(d['key'], SCALE45_PER_POINT)} | "
                         f"{f(m.get(d['key']))} |")
        srows.append(f"| {l} | Overall | {score('overall', n_in * SCALE45_PER_POINT)} | "
                     f"{f(m.get('overall'))} |")
    L += ["", "| Model | Section (as the Scorecard) | Score | Margin +/- 90% |", "|---|---|---|---|"] + srows
    L += packs45_table(ranked, stats, prof)
    foot = _chains.render_board(ranked, {l: stats[l].get("chain_stats") or {} for l in ranked}, prof)[-1]
    foot = foot.replace("; a score and the Overall combine their parts by points", "")
    L += ["", foot, "", "*Routed board: the Section rows are the Scorecard's numbers (section scales, "
          f"each /{SCALE45_PER_POINT:g}; Overall = the in-total sections, ± 1.645 x sqrt(sum SE²)); the part "
          "rows above are each part's own share x its maximum and do not add up to them. A pack line "
          "(max 0) is the mean of the cells the route opened, n = units ended, never in a total. "
          "\"on an older stamp, not counted\" = units with stored rows made under an earlier instance "
          "or grading stamp: they exist, they are not on this board (distinct from not run).*"]
    return L


def packs45_table(ranked: list[str], stats: dict, prof: dict) -> list[str]:
    """The routed board's bonus packs (bonus45): one line per pack of a shown part -- scored
    (points = share x its points, n = its units, ± 90%), not run, errored, or locked with the
    reason. Nothing when no shown part has a pack."""
    from . import ladder as _ladder
    rows, few = [], False
    for l in ranked:
        for sec, parts in ((stats[l].get("headline") or {}).get("packs45") or {}).items():
            for part, packs in parts.items():
                for p in packs:
                    state = {"scored": "scored", "pending": "not run"}.get(
                        p["state"], f"{p['state']}: {p['reason']}")
                    ran = p["state"] == "scored"
                    rows.append(f"| {l} | {sec}/{part} | pack {p['n']} (level {p['level']}) | {state} | "
                                + (f"{p['pts']:.2f} / {p['points']:g}" if ran else "-") + " | "
                                + (str(p["n_units"]) if ran or p["n_units"] else "-") + " | "
                                + (f"{p['margin']:.2f}" if p.get("margin") is not None
                                   else ("- †" if ran else "-")) + " |")
                    few = few or (ran and p.get("margin") is None)
    if not rows:
        return []
    return (["", "### Bonus packs", "",
             "| Model | Part | Pack | State | Points | n | Margin +/- 90% |", "|---|---|---|---|---|---|---|"]
            + rows
            + ["", f"*A bonus pack is worth {1:g} point (share x its points; share = the weighted check share, a weight-0 check counts nowhere), added to "
                   "its part, its section and Overall (NSFW: its own score only). The packs of a level open "
                   f"when the part's standard share and every pack of every lower level are at or above "
                   f"{_ladder.unlock_share(prof) * 100:.0f}% (the 4.x chain). Max = 10 standard + 1 per pack "
                   "that exists. A pack's margin = 1.645 x its cluster SE over its units; a section's = "
                   "1.645 x sqrt(SE_standard² + sum SE_pack²). \"not run\" = open, no units yet (not 0).*"]
            + (["", "*† A scored pack on fewer than 2 units has no SE: it adds nothing to its section's and "
                    "Overall's margins, which therefore understate the uncertainty.*"] if few else []))


def _part_shorts(s: dict) -> dict[str, str]:
    card = s.get("scorecard") or {}
    return {f"{d['key']}/{p['key']}": (p.get("short") or p.get("label") or p["key"])
            for d in card.get("score_defs") or [] for p in d.get("parts") or []}


def cut_empty_notes(s: dict) -> list[str]:
    """R1 (fix round 2026-11-18): the row's cut and empty counts per part, e.g. "cut at the ceiling (left out of
    the score): Predict 1 of 5, RPL 2 of 8"; "empty replies (scored 0): RPS 1". Nothing when both are zero."""
    card = s.get("scorecard") or {}
    short = _part_shorts(s)
    cuts, empties, dnfs = [], [], []
    for cat, parts in (card.get("ladder") or {}).items():
        for pk, det in (parts or {}).items():
            ct = (det or {}).get("cut") or {}
            nm = short.get(f"{cat}/{pk}", pk)
            dn = (det or {}).get("did_not_finish") or {}
            if dn.get("n"):
                dnfs.append(f"{nm}: {dnf_text(dn, (det or {}).get('max') or 0)}")
            if ct.get("cut"):
                cuts.append(f"{nm} {ct['cut']} of {ct['of']}" + (" (not measured)" if det.get("not_measured") else ""))
            if ct.get("empty"):
                empties.append(f"{nm} {ct['empty']}")
    out = []
    if dnfs:
        out.append("did not finish (graded as delivered, kept in the mean): " + "; ".join(dnfs))
    if cuts:
        out.append("cut at the ceiling (left out of the score): " + ", ".join(cuts))
    if empties:
        out.append("empty replies (scored 0): " + ", ".join(empties))
    return out


def reruns45(rows: list[dict]) -> dict | None:
    """``{"jobs": n, "by_job": {"<case>#r<repeat>": {"run_ids": [...], "kept": id}}}`` over the board rows that
    carry _board_rows' ``rerun`` mark (a finished job replaced by a complete re-draw), or None."""
    by: dict[str, dict] = {}
    for r in rows:
        m = r.get("rerun")
        if isinstance(m, dict):
            by.setdefault(f"{r.get('case_id')}#r{r.get('repeat') or 1}",
                          {"run_ids": list(m.get("run_ids") or []), "kept": m.get("kept")})
    return {"jobs": len(by), "by_job": dict(sorted(by.items()))} if by else None


#: the visible mark of a row judged (in part) by a judge other than the profile's (owner 2026-11-24)
OTHER_JUDGE_MARK = "⚖"
#: one line above the table when a board's rows were judged by different judges
MIXED_JUDGES_LINE = "rows on this board were judged by different judges: ranks compare across judges"


def model_output_invalid_note(n: int) -> str:
    """Review E (2026-11-25): replies the server could not parse (runner.MODEL_OUTPUT_INVALID), scored as misses."""
    return f"{n} {'reply' if n == 1 else 'replies'} could not be parsed by the server and count as misses"


#: PLAN-451-LIMITS step 7: why a stored row lost its points, or what was tolerated; the classes counted per segment
LOSS_CLASSES = ("cut", "empty", "context", "transport", "clamped", "over_budget", "tolerated", "rerun_failed")
#: the classes whose rows score nothing (points at stake); the others are facts about scored rows
_LOST = ("cut", "empty", "context", "transport")
_LOSS_WORDS = {"cut": "cut", "empty": "empty", "context": "context too small", "transport": "transport",
               "clamped": "budget clamped by the route", "over_budget": "thought past the budget",
               "rerun_failed": "re-run failed on infrastructure, old reply kept"}
#: the engine group of each board section (chain parts are written "<group>/<part>")
_SEC_GROUP = {"code": "work", "chat": "chat"}


def segment_map45(tree: dict | None) -> dict:
    """``{"parts": {"<group>/<part>": {"<sec>.<segment>": share}}, "cases": {case_id: "<sec>.<segment>"}, "max":
    {"<sec>.<segment>": points}}`` from the segment tree (segments45.tree): which segment a row and a part's points
    belong to. A part listed by several segments (rplong scenes) is shared by the number of its cases in each."""
    parts: dict[str, dict[str, float]] = {}
    cases: dict[str, str] = {}
    mx: dict[str, float] = {}
    for sec, segs in (tree or {}).items():
        if sec not in _SEC_GROUP or not isinstance(segs, dict):
            continue                                        # only the Code and Chat sections hold segments
        grp = _SEC_GROUP[sec]
        for seg, d in segs.items():
            if not isinstance(d, dict):
                continue
            key = f"{sec}.{seg}"   # "chat.remembers": a "chat/<x>" key reads as an old part id (public scrub)
            mx[key] = float(d.get("max") or 0.0)
            for cid in d.get("cases") or []:
                cases[str(cid)] = key
            for part, w in (d.get("parts") or {}).items():
                if isinstance(w, dict):                     # a scene list (rplong): case ids
                    for cid in w:
                        cases[str(cid)] = key
                    parts.setdefault(f"{grp}/j_{part}", {})[key] = float(len(w))
                else:
                    parts.setdefault(f"{grp}/{part}", {})[key] = 1.0
    for p_, d in parts.items():
        t = sum(d.values()) or 1.0
        parts[p_] = {k: v / t for k, v in d.items()}
    return {"parts": parts, "cases": cases, "max": mx}


def _row_segment(r: dict, smap: dict) -> str | None:
    cid = str(r.get("case_id") or "")
    for k, v in smap["cases"].items():
        if cid == k or cid.startswith(k + "-") or cid.startswith("FLAT-" + k):
            return v
    part = (r.get("chain") or {}).get("part")
    shares = smap["parts"].get(str(part)) if part else None
    if shares:
        return max(shares, key=shares.get)
    return None


def row_loss_classes(r: dict) -> list[str]:
    """The loss classes of one board row (a row may be in several: a clamped row that was also cut)."""
    out = []
    kind = str(r.get("error_kind") or "")
    err = bool(r.get("error") or r.get("skipped") or r.get("grade") == "error")
    if (err and (kind == "ctx_short" or "ctx_short" in str(r.get("error") or ""))) or r.get("context_model_max"):
        out.append("context")
    elif err:
        out.append("transport")
    elif r.get("finish_reason") == "length" or r.get("truncated"):
        out.append("cut")
    elif (r.get("empty_stop") or (r.get("console") or {}).get("end") == "empty reply"
          or not (str(r.get("response") or "").strip() or r.get("tool_conversation") or r.get("conversation"))):
        out.append("empty")
    if r.get("budget_clamp"):
        out.append("clamped")
    if r.get("over_budget"):
        out.append("over_budget")
    if r.get("tolerated") or (r.get("console") or {}).get("calls_without_line_break"):
        out.append("tolerated")
    if r.get("_rerun_failed"):
        out.append("rerun_failed")
    return out


def losses45(rows: list[dict], tree: dict | None, shares: dict | None = None) -> dict:
    """``losses`` of report.json per model (PLAN-451-LIMITS step 7, no silent loss): ``{"<sec>.<segment>": {cut,
    empty, context, transport, clamped, over_budget, tolerated, points_at_stake}}`` over the board's rows (newest
    complete run per job). ``points_at_stake`` = the segment's points x (lost rows / its rows) x the model's mean
    score on its answered rows: what the lost rows would have been worth at the model's own level. Segments with
    nothing to say are left out. Report only: no score reads it."""
    smap = segment_map45(tree)
    last: dict[tuple, dict] = {}
    for r in _board_rows(rows):
        ch = r.get("chain") or {}
        # one row per job: a multi-turn job's last row stands for it (its turns are not separate losses)
        last[(r.get("bench_run_id"), r.get("case_id"), r.get("repeat"), ch.get("rung"), ch.get("attempt"))] = r
    from .attempts45 import rerun_gave_up
    gave = rerun_gave_up(rows)
    by: dict[str, list[dict]] = {}
    for r in last.values():
        if (r.get("case_id"), r.get("repeat") or 1) in gave and not r.get("chain"):
            r = {**r, "_rerun_failed": True}
        seg = _row_segment(r, smap)
        if seg:
            by.setdefault(seg, []).append(r)
    out = {}
    for seg, rs in sorted(by.items()):
        rec = dict.fromkeys(LOSS_CLASSES, 0)
        lost, answered = 0, []
        for r in rs:
            cls = row_loss_classes(r)
            for c in cls:
                rec[c] += 1
            if any(c in _LOST for c in cls):
                lost += 1
            elif r.get("score") is not None:
                try:
                    answered.append(float(r["score"]))
                except (TypeError, ValueError):
                    pass
        if not any(rec.values()):
            continue
        mean = (sum(answered) / len(answered) if answered
                else float((shares or {}).get(seg) or 0.0))      # no row scores: the segment's own share
        rec["points_at_stake"] = round(smap["max"].get(seg, 0.0) * lost / len(rs) * mean, 3)
        out[seg] = rec
    return out


def losses_line(losses: dict | None) -> str | None:
    """The one details line, e.g. "Lost rows: code.tools 3 empty (0.20 pt); tolerated: 4 rows (...)"."""
    if not losses:
        return None
    lost, tol = [], 0
    for seg, rec in losses.items():
        bits = [f"{rec[c]} {_LOSS_WORDS[c]}" for c in ("cut", "empty", "context", "transport", "clamped",
                                                       "over_budget", "rerun_failed") if rec.get(c)]
        if bits:
            lost.append(f"{seg} {', '.join(bits)} ({float(rec.get('points_at_stake') or 0.0):.2f} pt)")
        tol += int(rec.get("tolerated") or 0)
    if not lost and not tol:
        return None
    line = "Lost rows: " + ("; ".join(lost) if lost else "none")
    if tol:
        line += f"; tolerated: {tol} row{'s' if tol != 1 else ''} (bare closer or calls without a line break)"
    return line


def pack_points_by_segment45(tree: dict | None, sec: str, total: float, ladder_card: dict | None,
                             ladder_points: float, engine_parts: dict | None, fixes_points: float,
                             chat_packs: dict | None) -> dict[str, float]:
    """Bonus (pack) points of one section per segment, summing to ``total`` (the section's ``pack_points``):
    the chat packs and the Fixes pack go to their own segment, an engine pack to its part's segment, and the
    ladder packs' points (old S - old T1) are shared over the parts whose ladder points exceed their standard
    points, in proportion to that excess. Report data only: no score reads it."""
    smap = segment_map45(tree)
    segs = [k.split(".", 1)[1] for k in smap["max"] if k.startswith(sec + ".")]
    out = dict.fromkeys(segs, 0.0)

    def add(part_key: str, v: float) -> None:
        shares = smap["parts"].get(part_key) or {}
        if not shares:
            out["other"] = out.get("other", 0.0) + v
            return
        for k, w in shares.items():
            out[k.split(".", 1)[1]] = out.get(k.split(".", 1)[1], 0.0) + v * w
    for name, v in (chat_packs or {}).items():
        if name in out:
            out[name] += float((v or {}).get("points") or 0.0)
    if fixes_points and "fixes" in out:
        out["fixes"] += float(fixes_points)
    for k, v in (engine_parts or {}).items():
        add(k, float((v or {}).get("delta") or 0.0))
    grp = _SEC_GROUP.get(sec, sec)
    excess = {}
    for p_, d in ((ladder_card or {}).get(grp) or {}).items():
        if not isinstance(d, dict) or d.get("points") is None:
            continue
        ch = d.get("chains") or {}
        std = ch.get("points") if ch.get("points") is not None else float(d.get("std_share") or 0.0) * float(
            d.get("max") or 0.0)
        x = float(d["points"]) - float(std or 0.0)
        if x > 1e-9:
            excess[f"{grp}/{p_}"] = x
    if ladder_points > 0:
        tot = sum(excess.values())
        if tot > 0:
            for k, x in excess.items():
                add(k, ladder_points * x / tot)
        else:
            out["other"] = out.get("other", 0.0) + ladder_points
    out = {k: round(v, 4) for k, v in out.items() if v or k in segs}
    gap = round(float(total) - sum(out.values()), 4)
    if gap and out:
        big = max(out, key=lambda k: abs(out[k]))
        out[big] = round(out[big] + gap, 4)                  # rounding: the parts add up to the section exactly
    return out


def mixed_judges45(labels: list[str], stats: dict) -> bool:
    """True when the board's rows were not all judged by the same judge(s): a row marked other_judge45 beside a
    row judged by the profile's judge alone, or two marked rows with different judges."""
    keys = {tuple((stats[l].get("other_judge45") or {}).get("ids") or ()) for l in labels if l in stats}
    return len(keys) > 1


def other_judge_cell(s: dict) -> str:
    """The Overall cell's mark for a row judged by another judge: " ⚖"; '' otherwise."""
    return f" {OTHER_JUDGE_MARK}" if s.get("other_judge45") else ""


def notes_cell(s: dict) -> str:
    """Scorecard Notes: only what makes a row NOT comparable (failed,
    partial, unjudged, stale, other judge), plus a real vendor version."""
    notes = list((s.get("coverage") or {}).get("notes") or [])
    rn = replicate_note(s.get("headline"))
    if rn:
        notes.append(rn)
    if s.get("validation_cc"):
        notes.insert(0, staged_cc_mark(_cc_effort(s)))   # a staged board's Claude Code row, with its effort
    half = (s.get("scorecard") or {}).get("half_only")
    if half:
        notes.append(f"{half} only")
    v = version_note(s)
    if v:
        notes.append(v)
    if s.get("board_note"):
        # PUBOUT 4.5: the models file's per-model note for readers (models.yaml `board_note`)
        notes.append(s["board_note"])
    # the judge is also the board's reference model (INTENT-45): its own row says which scores it graded itself
    selfg = (["Chat"] if (s.get("coverage") or {}).get("self_judged") else []) \
        + (["NSFW+"] if (s.get("nsfw_plus") or {}).get("self_graded") else [])
    if selfg:
        notes.append(f"graded by itself: {' and '.join(selfg)} (this model is the judge)")
    if s.get("other_judge45"):
        notes.append(OTHER_JUDGE_MARK + " " + s["other_judge45"]["note"])
    if s.get("model_output_invalid"):
        notes.append(model_output_invalid_note(s["model_output_invalid"]))
    cf = s.get("content_filtered") or {}
    if cf.get("total"):
        n = cf["total"]
        t = (f"{n} {'reply' if n == 1 else 'replies'} blocked by the provider's content "
             "filter (a blocked reply scores zero)")
        if cf.get("separate"):
            t += f"; {cf['separate']} of them in NSFW (counted as refusals)"
        notes.append(t)
    if s.get("cost_unknown_rows"):
        n_u = s["cost_unknown_rows"]
        notes.append(f"cost: {n_u} row{'s' if n_u != 1 else ''} without usage data")
    notes += cut_empty_notes(s)
    er = s.get("errored_rows") or {}
    if er.get("n"):
        notes.append(f"{er['n']}/{er['total']} rows errored (counted failed; a resume re-runs them)")
    from .forfeit45 import note as _forfeit_note
    ff = _forfeit_note(s.get("forfeits") or [])
    if ff:
        notes.append(ff)
    rr = s.get("reruns45") or {}
    if rr.get("jobs"):
        n = rr["jobs"]
        notes.append(f"{n} job{'s' if n != 1 else ''} re-run (the newest complete run counts; "
                     "run ids in report.json)")
    pf = filter_pending_parts(s)
    if pf:
        notes.append(f"pending (provider filter): {', '.join(pf)}")
    for g in (s.get("headline") or {}).get("near_gate") or []:
        if "/" in g["area"]:
            from . import segments45 as _s45n
            notes.append(f"near ladder set 1 of {_s45n.plain_text(g['area'], s.get('part_names45'))} "
                         f"(unlocks in {g['pass_share'] * 100:.0f}% of resamples)")
        else:
            notes.append(f"near T{g['tier']} gate: {g['area'].title()} "
                         f"(unlocks in {g['pass_share'] * 100:.0f}% of resamples)")
    return "; ".join(notes)


def _tier_profile(cfg: dict | None) -> dict | None:
    """The benchmark profile when it has a ``tiers:`` block (suite 3.9)."""
    try:
        from .profiles import DEFAULT_PROFILE, load_profile
        from . import tiers as _tiers
        prof = load_profile(active_name(cfg), cfg)
        from .profiles import activate_cases_root
        activate_cases_root(prof)
    except Exception:  # noqa: BLE001 — no profile readable: no tiers
        return None
    if prof.get("scores"):
        return _audience_profile(prof, cfg)        # points board (categories + ladders)
    return prof if _tiers.enabled(prof) else None


def points_mode(prof: dict | None) -> bool:
    """A profile that declares ``scores:`` with sub-scores: the points board
    (categories out of 10, ladders) instead of the T1/T2/T3 tiers."""
    return bool(prof and prof.get("scores"))


def routed(prof: dict | None) -> bool:
    """A 4.5 routed profile (a ``routing`` block): its board shows the route's decision and the
    section scales, never the 4.1 share gates or parts it has no cells for."""
    return bool((prof or {}).get("routing"))


def board_defs(prof: dict | None) -> list[dict]:
    """The score defs the points board shows. A routed profile keeps only the parts its
    composition has cells for (a ``chains`` section); a 4.1 part with no cell there is not
    listed (D's routed-board note, item 1). Every other profile: ``tiers.scores``."""
    from . import tiers as _tiers
    defs = _tiers.scores(prof)
    if not routed(prof):
        return defs
    from . import routing as _routing
    cells = set((prof or {}).get("chains") or {})
    try:
        packs = _routing.pack_parts(prof)
    except ConfigError:
        packs = set()            # an invalid routing block is the run side's error, not the board's
    cats = {c for c, ids in ((prof or {}).get("cases") or {}).items() if ids and c != "perf"}
    ids = {i for c, xs in ((prof or {}).get("cases") or {}).items() if c != "perf" for i in xs or []}

    def has_cells(d, p):
        if f"{d['key']}/{p['key']}" in cells:
            return True
        if f"{d['key']}/{p['key']}" in packs:
            return True              # the route's packs (routing.pack_parts)
        if not p.get("weight"):
            return False             # a 4.1 pack part (max 0): its sets are not routed cells
        if p.get("cases"):
            return bool(set(p["cases"]) & ids)
        comps = p.get("scoring") or []
        comps = comps if isinstance(comps, (list, tuple)) else list(comps)
        return any(_component_categories(k) & cats for k in comps)
    return [{**d, "parts": [p for p in d.get("parts") or [] if has_cells(d, p)]} for d in defs]


def _without_pack_rows(prof: dict | None, rows: list[dict]) -> list[dict]:
    """M4: a ladder pack's rows (its case ids, or a row stamped ``pack_gate``) are points on top of
    their part (ladder path), never section-scale units: the scale must not count them twice."""
    from . import ladder as _ladder
    try:
        packs = _ladder.all_ladder_case_ids(prof)
    except Exception:  # noqa: BLE001 - no readable ladder files: no pack ids
        packs = set()
    aliased = set(seed_aliases(prof))      # stored chain rows that stand as pack rows
    from . import chains as _ch
    idx = _ch.profile_index(prof) if aliased and (prof or {}).get("chains") else {}
    return [r for r in rows if not r.get("pack_gate") and not r.get("seed_alias")
            and (r.get("chain") or r.get("case_id") not in packs)
            and not (r.get("chain") and r.get("case_id") in aliased and not _ch.in_profile(r, idx))]


def scale45(prof: dict | None, rows: list[dict], meta: dict | None = None) -> dict | None:
    """A routed profile's section scales (scale.py) on the routing's calibration: per section the
    expected points on the reference form (0-100) and the 90% margin (1.645 x SE); the units
    outside Main are scored apart under ``<section>+packs``. None when the profile is not routed
    or the table cannot be read."""
    if not routed(prof):
        return None
    from . import config as _cfgmod
    from . import routing as _routing
    from . import scale as _scale
    try:
        sp = _routing.spec(prof)
        cal, cal_id = _routing.calibration(sp, _cfgmod.ROOT_DIR)
    except Exception as e:  # noqa: BLE001 - a missing table leaves the 4.1 headline, said so
        log.warning("section scales: calibration not read (%s)", e)
        return None
    rows, _dropped = _scale.drop_tripped(rows, meta)
    rows = _without_pack_rows(prof, rows)
    # each cell under the SCORING profile's ran-out rule (never the rule of the day a row was made)
    us = _scale.units(rows, ran_out=_scale.ran_out_rules(prof), cal=cal)
    ro_units = _scale.unit_ran_out(us)
    nm = set(_scale.mark_not_measured(us))
    res = _scale.score_run(us, cal)
    out = {"calibration": cal_id, "sections": {}, "cells": _cells45(prof, rows, us, ro_units, nm, cal)}
    out["parts"] = _parts45(us, cal)
    for sec, a in res.items():
        out["sections"][sec] = {k: a.get(k) for k in ("points", "margin_90", "se_points", "theta", "se",
                                                       "se_method", "n_units", "units", "clusters",
                                                       "extreme", "uncalibrated_cells", "ceiling_too_low",
                                                       "form_declared")
                                if k in a}
        out["sections"][sec]["_run"] = {k: a.get(k) for k in ("theta", "se", "psi", "slope", "points",
                                                              "se_method") if k in a}
        out["sections"][sec]["not_measured"] = sorted({u["cell"] for u in us if u["cell"] in nm
                                                       and u["section"] == sec.split("+")[0]
                                                       and (sec.endswith("+packs")) == (u["cell"] in
                                                       {c for c, v in out["cells"].items() if v["pack"]})})
    return out


def _parts45(us: list[dict], cal: dict) -> dict:
    """Per chain part: the mean of its Main units' scale share (G2 ruling 2026-11-14): the same
    weighted unit share the ability, the cells and the board's cell means use (``element_weight:
    check`` weights the checks, a weight-0 check moves nothing), each unit weighing 1, under the
    scoring profile's ran-out rule per cell. The routed board's part line and its pack gate read
    this share. A unit every element of which was excluded (ran out) is left out; a part whose
    Main units were all excluded has share None (not complete)."""
    from . import scale as _scale
    acc: dict[str, list] = {}
    for u in us:
        if u.get("kind") != "chain" or not u.get("part"):
            continue
        if not _scale._in_main(u, cal, _scale.main_cells(cal, u["section"]), None):
            continue
        a = acc.setdefault(u["part"], [0.0, 0, 0])
        if u.get("x") is None:
            a[2] += 1
        else:
            a[0] += u["x"]
            a[1] += 1
    return {p: {"share": (s_ / n) if n else None, "units": n, "excluded_units": ex}
            for p, (s_, n, ex) in sorted(acc.items())}


def effort_cells(prof: dict | None) -> list[str]:
    """Cells whose order follows thinking effort, not tier (``routing.effort_cells``, profile
    only: no list in code). Naming = scale.cell_of: ``<family>:L<level>[:x<n>]``."""
    return list(((prof or {}).get("routing") or {}).get("effort_cells") or [])


def _cells45(prof, rows, us, ro_units, nm, cal) -> dict:
    """Per cell of one model: units, mean of its finished units, ran-out units / units (the
    not-measured basis), not measured, pack (scored apart) and, for an effort cell, the mean
    output tokens per unit (shown outside every sum)."""
    from . import scale as _scale
    eff = set(effort_cells(prof))
    toks: dict[str, dict] = {}
    stages: dict[str, set] = {}
    for r in rows:
        ch = r.get("chain")
        if not ch:
            continue
        cell = _scale.cell_of(ch)
        if r.get("stage"):
            # routed runs since 2026-11-14 09:28 (runner): "locator" | "module" on every row
            stages.setdefault(cell, set()).add(str(r["stage"]))
        if not any(cell == e or cell.startswith(e + ":") for e in eff):
            continue
        uid = f"{ch['id']}-r{int(ch.get('rung') or 1)}-a{int(ch.get('attempt') or 1)}"
        g = (r.get("bench_run_id"), r.get("repeat"))
        t = toks.setdefault(uid, {})
        t[g] = t.get(g, 0) + int(((r.get("metrics") or {}).get("completion_tokens")) or 0)
    out: dict = {}
    for u in us:
        c = out.setdefault(u["cell"], {"section": u["section"], "units": 0, "_xs": [], "_tok": [],
                                       "pack": False, "rule": u.get("rule", "zero")})
        c["units"] += 1
        if u.get("x") is not None:
            c["_xs"].append(u["x"])
        if not _scale._in_main(u, cal, _scale.main_cells(cal, u["section"]), None):
            c["pack"] = True
        if u["id"] in toks:
            vals = list(toks[u["id"]].values())
            c["_tok"].append(sum(vals) / len(vals))
    for cell, c in out.items():
        xs, tk = c.pop("_xs"), c.pop("_tok")
        ro = ro_units.get(cell) or {}
        c.update(mean=(sum(xs) / len(xs)) if xs else None, ran_out_units=ro.get("ran_out_units", 0),
                 ran_out_share=ro.get("share", 0.0), not_measured=cell in nm,
                 effort=any(cell == e or cell.startswith(e + ":") for e in eff),
                 stages=sorted(stages.get(cell) or ()))
        if c["effort"]:
            c["tokens_per_unit"] = (sum(tk) / len(tk)) if tk else None
    return out


def _ladder_sets(prof: dict) -> dict:
    """ladder.discover, read once per bootstrap (files do not change mid-report), WITHOUT the NSFW+ pack sets
    (nsfwpack45.is_pack_set): those are scored apart (nsfw_plus.packs45) and never enter the old-tree scorecard,
    the headroom maxima, the headline strata or the ladder levels, staged or not."""
    from . import ladder
    b = _BOOT.get()
    if b is not None:
        if b.get("ladder") is None:
            b["ladder"] = _board_sets(ladder.discover(prof))
        return b["ladder"]
    return _board_sets(ladder.discover(prof))


def _pack_rows_of(label: str) -> list[dict]:
    """The NSFW+ pack rows of ``label`` from its stored transcript (deduped as every reader does). The board's
    row filter and tiers never carry pack rows (they are not board sets), so the pack column reads them here."""
    from .nsfwpack45 import is_pack_row
    return [r for r in load_transcripts(label) if is_pack_row(r)]


def _board_sets(sets: dict) -> dict:
    from .nsfwpack45 import is_pack_set
    out = {k: [s for s in seq if not is_pack_set(s)] for k, seq in sets.items()}
    return {k: seq for k, seq in out.items() if seq}


def _staged_case_ids(prof: dict) -> set[str]:
    """Question ids of staged packs (read once per bootstrap). Empty when
    --include-staged is in force (discover then already counts them)."""
    from . import ladder
    b = _BOOT.get()
    if b is not None and b.get("staged_ids") is not None:
        return b["staged_ids"]
    try:
        ids = {cid for seq in ladder.discover(prof, staged=True).values()
               for s in seq if s.get("staged") for cid in s["cases"]}
    except Exception:  # noqa: BLE001 — a broken pack file never takes the board down
        ids = set()
    if b is not None:
        b["staged_ids"] = ids
    return ids


def _ladder_levels(prof: dict) -> dict[str, tuple[str, int]]:
    from . import ladder
    return ladder.case_levels(prof, _ladder_sets(prof))


def tier_rows(prof: dict, rows: list[dict], era: dict | None) -> dict[int, list[dict]]:
    """{tier: rows} of one model's board rows."""
    from . import tiers as _tiers
    by_t: dict[int, list[dict]] = {1: [], 2: [], 3: []}
    if points_mode(prof):
        # points board: standard rows = tier 1, ladder-set rows = "tier 2"
        lad = _ladder_levels(prof)
        from .profiles import hard_case_ids
        hard_ids = hard_case_ids(prof)
        held = _staged_case_ids(prof) - set(lad)
        from .nsfwpack45 import is_pack_row
        for r in rows:
            if r.get("case_id") in held or is_pack_row(r):
                continue        # a staged pack's row never reaches the board (spec v2 G0); an NSFW+ pack row is
                                # scored apart (nsfw_plus.packs45), staged or not
            # 4.5: hard-set rows ride in "tier 3" (unused on the points board)
            # so no standard part without its own case list ever sees them
            by_t[2 if r.get("case_id") in lad else 3 if r.get("case_id") in hard_ids
                 else 1].append(r)
        if (era or {}).get("old"):
            by_t[2] = []
            by_t[3] = []
        return by_t
    tmap = _tiers.case_tiers(prof)
    for r in rows:
        by_t[tmap.get(r.get("case_id"), (1, None))[0]].append(r)
    if (era or {}).get("old"):
        # old values: those runs were never gated into tiers (a 3.8 CZ02 row
        # was a tier-1 row then) — only their tier-1 cases count, the higher
        # tiers wait for the re-run
        by_t[2], by_t[3] = [], []
    return by_t


def headline_settings(prof: dict | None) -> dict:
    """The profile's optional ``headline:`` block over headline.py's
    defaults: resamples (>= 1000 on a real board), seed, level, overall
    (sum | mean), two_level (resample repeats inside drawn cases)."""
    from . import headline as _h
    h = (prof or {}).get("headline") or {}
    return {"resamples": int(h.get("resamples", _h.RESAMPLES)),
            "seed": int(h.get("seed", _h.SEED)),
            "level": float(h.get("level", _h.LEVEL)),
            "overall": _h.overall_mode(prof),
            "two_level": bool(h.get("two_level", True))}


def model_boots(labels: list[str], inputs: dict, cfg: dict | None,
                prof: dict) -> dict[str, dict]:
    """Bootstrap samples (headline.boot_model) per label, all on the SAME
    draws (the profile's strata), so any two models can be paired.
    ``inputs`` = {label: (rows, meta, era)}."""
    from . import headline as _h
    sc = scoring_config(cfg)
    hs = headline_settings(prof)
    allowed = _profile_case_ids(cfg, with_tiers=True)
    st = _h.strata(prof, sc, allowed)
    mults = _h.draws(st, hs["resamples"], hs["seed"], per_stratum=_h.per_stratum(prof))
    jobs = []
    # the judge term only: the run-to-run file is on the board's scale and never enters the item bootstrap (its
    # old-tree excess inflated the item-sampling figure and the pack totals, review A F5)
    marks = judge_marks_result() if points_mode(prof) else None
    for l in labels:
        rows, meta, era = inputs[l]
        pr = _h.model_precision(l, prof, marks, None) if marks else None
        jobs.append(((tier_rows(prof, rows, era), l, cfg, meta, era, prof, sc, st, mults),
                     {"two_level": hs["two_level"], **({"precision": pr} if pr else {})}))
    results = _parallel_boots(jobs)
    return dict(zip(labels, results))


def repeat_noise45(labels: list[str], inputs: dict, cfg: dict | None, prof: dict) -> dict[str, dict]:
    """Each model's OWN run noise per headline part (INTENT-45, accuracy45): the engine's bootstrap
    (headline.boot_model, the same strata and scorer as the item margin) with every case kept exactly once and only
    its repeats redrawn. {label: {"sd": {part key: SD of the part's share}, "n": {part key: most repeats of a case}}}.
    A part whose cases all have one repeat has nothing to redraw (n = 1): accuracy45 says so."""
    import random as _random
    from . import accuracy45 as _A
    from . import headline as _h
    sc = scoring_config(cfg)
    allowed = _profile_case_ids(cfg, with_tiers=True)
    st = _h.strata(prof, sc, allowed)
    n = int(((prof or {}).get("intent") or {}).get("repeat_resamples") or 200)
    rnd = _random.Random(f"{_h.SEED}:repeats45")
    ids = sorted({i for v in st.values() for i in v})
    mults = [{cid: [rnd.getrandbits(32)] for cid in ids} for _ in range(n)]
    jobs = [((tier_rows(prof, inputs[l][0], inputs[l][2]), l, cfg, inputs[l][1], inputs[l][2], prof, sc, st, mults),
             {"two_level": True}) for l in labels]
    res = dict(zip(labels, _parallel_boots(jobs)))
    out: dict = {}
    for l in labels:
        reps: dict = {}
        for r in inputs[l][0]:
            # a generated unit's rows carry rung/attempt in case_id; the stratum names the unit (chain id)
            cid = (r.get("chain") or {}).get("id") if isinstance(r.get("chain"), dict) else None
            reps.setdefault(cid or r.get("case_id"), set()).add(r.get("repeat"))
        # n1 (the printed range, review A F8): repeats the redraw can actually reach in the STANDARD part: tier-1
        # strata only, counted by the row's own case id (resample_rows redraws rows whose case_id is a stratum id; a
        # generated unit's rows carry rung/attempt in case_id and pass through unchanged). ``n`` (max over every
        # tier, by chain id) stays the pack gates' input.
        own_reps: dict = {}
        for r in inputs[l][0]:
            own_reps.setdefault(r.get("case_id"), set()).add(r.get("repeat"))
        sd: dict = {}
        nn: dict = {}
        n1: dict = {}
        for (tier, name), cids in st.items():
            key = str(name).split("@", 1)[0]
            nn[key] = max([nn.get(key, 0)] + [len(reps.get(c) or ()) for c in cids])
            if tier == 1:
                n1[key] = max([n1.get(key, 0)] + [len(own_reps.get(c) or ()) for c in cids])
        for key, xs in ((res.get(l) or {}).get("samples") or {}).items():
            vals = [x for x in xs if x is not None]
            if "/" in key and len(vals) >= 2:
                m = sum(vals) / len(vals)
                sd[key] = (sum((x - m) ** 2 for x in vals) / (len(vals) - 1)) ** 0.5
        out[l] = {"sd": sd, "n": {k: v for k, v in nn.items() if "/" in k},
                  "n1": {k: v for k, v in n1.items() if "/" in k}}
    return out


# ------------------------------------------------- multi-core bootstrap
# The headline bootstrap is ~1000 model_stats calls per model and was one
# core at 100% for minutes on a full board (2026-11-12). Each model's samples
# depend only on that model's rows and the SHARED draws, so models are spread
# over a few forked worker processes and merged back in label order: the
# result is identical to the serial loop. Workers inherit the parent's state
# by fork (results dir, private layer, loaded cases) and return only the
# small samples dict. CRUCIBLEFORGE_REPORT_WORKERS=1 forces the serial path.
_BOOT_JOBS: list | None = None
#: a worker that has not answered after this long is given up on and the
#: remaining models run serially (a fork can inherit a lock held by another
#: thread; a hung report is worse than a slow one)
BOOT_WORKER_TIMEOUT_S = 1800


def report_workers(n_jobs: int) -> int:
    """Worker processes for ``n_jobs`` models: $CRUCIBLEFORGE_REPORT_WORKERS,
    else min(4, cpu_count // 2) — a few cores, not the whole box."""
    import os
    env = os.environ.get("CRUCIBLEFORGE_REPORT_WORKERS", "").strip()
    try:
        w = int(env) if env else min(4, (os.cpu_count() or 2) // 2)
    except ValueError:
        w = 1
    return max(1, min(w, n_jobs))


def _boot_job(i: int) -> dict:
    from . import headline as _h
    a, kw = _BOOT_JOBS[i]
    return _h.boot_model(*a, **kw)


def _parallel_boots(jobs: list) -> list[dict]:
    """boot_model over every job, in job order (parallel when it pays)."""
    global _BOOT_JOBS
    from . import headline as _h
    workers = report_workers(len(jobs))
    import multiprocessing as _mp
    if workers > 1 and _BOOT.get() is None and "fork" in _mp.get_all_start_methods():
        import concurrent.futures as _cf
        import warnings
        _BOOT_JOBS = jobs
        out: list = [None] * len(jobs)
        try:
            with warnings.catch_warnings():
                # 3.12+ warns on fork with other threads alive; the workers
                # only compute (no leases, no I/O but reads) — see above
                warnings.simplefilter("ignore", DeprecationWarning)
                ex = _cf.ProcessPoolExecutor(max_workers=workers, mp_context=_mp.get_context("fork"))
            try:
                futs = [ex.submit(_boot_job, i) for i in range(len(jobs))]
                for i, f in enumerate(futs):
                    out[i] = f.result(timeout=BOOT_WORKER_TIMEOUT_S)
            except BaseException:
                ex.shutdown(wait=False, cancel_futures=True)
                for p in list((getattr(ex, "_processes", None) or {}).values()):
                    p.kill()     # a hung or interrupted worker must not outlive the report
                raise
            ex.shutdown(wait=True)
            return out
        except Exception as e:  # noqa: BLE001 — never lose the board to the pool
            log.warning("report: parallel bootstrap failed (%s) — finishing serially", e)
            return [o if o is not None else _h.boot_model(*a, **kw)
                    for o, (a, kw) in zip(out, jobs)]
        finally:
            _BOOT_JOBS = None
    return [_h.boot_model(*a, **kw) for a, kw in jobs]


def _provisional(s: dict) -> bool:
    """A row whose model holds a conformance FAIL record (alignment
    2026-11-06, review B1): scores shown, never ranked."""
    return bool((s.get("coverage") or {}).get("provisional"))


#: a GGUF quant in a rig file name ("Q8_0", "Q5_K_M", "IQ4_XS", "Q4_0"…)
_QUANT_PAT = r"(?<![A-Za-z0-9])(I?Q[1-8](?:_K_(?:XL|[SML])|_K|_XXS|_XS|_NL|_[01]|_S|_M)?)"
_QUANT_RE = re.compile(_QUANT_PAT + r"(?![a-z0-9])")
#: a lowercase file name ("cydonia-22b-v1-q6_k")
_QUANT_RE_LOWER = re.compile(_QUANT_PAT + r"(?![A-Za-z0-9])", re.IGNORECASE)
_FP_QUANT_RE = re.compile(r"(?<![A-Za-z0-9])(NVFP4|MXFP4|FP8|BF16|F16)(?![A-Za-z0-9])",
                          re.IGNORECASE)
LOCAL_TYPES = ("studioforge", "lmstudio")


def quant_of(model_id: str | None) -> str | None:
    """The quant of a local model file from its rig id (None: not parseable).
    A 4-bit float format wins over a Q tag in the same name (an NVFP4 build
    with Q8_0 side tensors is an NVFP4 model)."""
    stem = str(model_id or "").rsplit("/", 1)[-1]
    m = _FP_QUANT_RE.search(stem)
    if m:
        return m.group(1).upper()
    hits = _QUANT_RE.findall(stem) or _QUANT_RE_LOWER.findall(stem)
    return hits[-1].upper() if hits else None


def row_facts(label: str, meta: dict | None, cfg: dict | None) -> dict:
    """Public per-row facts the site needs (ALIGN-INTERFACE.md): scope
    (modalities / text_suite_only), quant (+ whether it is the Q8_0
    standard) and the family key — from the models.yaml row, the quant
    parsed from the rig id when the row does not set ``quant:``."""
    meta = meta or {}
    entry = next((m for m in (cfg or {}).get("models") or []
                  if isinstance(m, dict) and m.get("name") == label), {}) or {}
    ptype = meta.get("provider_type")
    if not ptype:
        pname = entry.get("provider") or meta.get("provider") or meta.get("device")
        ptype = (((cfg or {}).get("providers") or {}).get(pname) or {}).get("type") or pname
    local = ptype in LOCAL_TYPES if ptype else None
    quant = entry.get("quant") or (quant_of(entry.get("model_id") or meta.get("model_id"))
                                   if local else None)
    quant = str(quant) if quant else None
    std = None if not quant else quant.upper() in ("Q8_0", "Q8")
    mods = entry.get("modalities")
    if not mods:
        from .bench_all import MOD_VL, vl_chat
        mods = MOD_VL if vl_chat(entry.get("model_id") or meta.get("model_id")) else "text->text"
    mods = str(mods)
    tso = entry.get("text_suite_only")
    if tso is None:
        tso = mods != "text->text"
    qr = entry.get("quant_reason")
    served = "rig" if local else (str(entry["served"]) if entry.get("served") else
                                  (("local" if meta.get("hosted") is False else "hosted") if ptype else None))
    sp = entry.get("served_precision")
    return {"scope": {"modalities": mods, "text_suite_only": bool(tso)},
            "quant": quant, "quant_standard": std,
            "quant_reason": str(qr) if qr else None, "served": served,
            "served_precision": str(sp) if sp else None,
            "family": str(entry["family"]) if entry.get("family") else None,
            # 2026-11-07: the "Built on" tag and the version link (models.yaml `base`, `official`,
            # `supersedes`); official defaults to "a hosted model is the vendor's own release"
            "base": _one_line(entry.get("base"), 2, 40),
            "official": entry["official"] if isinstance(entry.get("official"), bool) else (
                False if (ptype in LOCAL_PROVIDER_TYPES or meta.get("hosted") is False) else bool(ptype)),
            "supersedes": str(entry["supersedes"]) if entry.get("supersedes") else None,
            # the short name people read ("Claude Opus 5.5"); used in report.md's older-versions line
            "display": _one_line(entry.get("display"), 2, 60),
            # PUBOUT 4.5: a per-model note for readers (models.yaml `board_note`: how it was run, a
            # provider change under the same id); emitted only when set (4.1 rows unchanged)
            **({"board_note": " ".join(str(entry["board_note"]).split())}
               if isinstance(entry.get("board_note"), str) and entry["board_note"].strip() else {})}


def _one_line(v, lo: int, hi: int) -> str | None:
    """A short one-line string from models.yaml (``base``), else None."""
    if not isinstance(v, str):
        return None
    v = v.strip()
    return v if lo <= len(v) <= hi and "\n" not in v else None


def superseded_map(labels: list[str], stats: dict, cfg: dict | None = None) -> dict[str, str]:
    """``{label: newer label}`` for the board's official rows that have a newer, ranked version on the board.

    models.yaml ``supersedes: <label>`` sits on the NEWER row. For each official row, walk the chain of
    successors (passing through labels that are not on this board) and take the newest one that is on the
    board, official, ranked (has an overall score and is not provisional). A provisional or unranked newer row
    never hides the older one. A fork stops the walk (the site refuses forks and cycles earlier).
    ``cfg`` adds the links of models.yaml rows that have no results yet (an unbenched label in the middle of
    a chain a <- b <- c is passed through)."""
    on = set(labels)
    succ: dict[str, list[str]] = {}
    for l, s in stats.items():
        t = s.get("supersedes")
        if t:
            succ.setdefault(t, []).append(l)
    for e in (cfg or {}).get("models") or []:
        if isinstance(e, dict) and e.get("name") and e.get("supersedes") and e["name"] not in stats:
            succ.setdefault(str(e["supersedes"]), []).append(e["name"])

    def ranked(l: str) -> bool:
        s = stats.get(l) or {}
        return (l in on and s.get("official") is True and not s.get("provisional")
                and (s.get("scorecard") or {}).get("total") is not None)
    out: dict[str, str] = {}
    for l in labels:
        if (stats.get(l) or {}).get("official") is not True:
            continue
        seen, cur, best = {l}, l, None
        while True:
            nxt = [n for n in succ.get(cur, []) if n not in seen]
            if len(nxt) != 1:
                break
            cur = nxt[0]
            seen.add(cur)
            if ranked(cur):
                best = cur
        if best:
            out[l] = best
    return out


def _rplong45(label: str, rows: list[dict], prof: dict | None = None) -> dict:
    """The eight 4.1 scenes. 4.5 (11b, lead 2026-11-21; ``rplong_score.definition: judge_only``): the judge's fact
    rate alone, stored second-reader votes never read. Older definition (mean_of_two_readers): judge + the pinned
    reader of ``<results>/readers45/<model>.jsonl``. Per scene: the mean over its repeats when every repeat is
    scored, else the first pending reason; ``values`` = the per-repeat scores (each model's own range, accuracy45)."""
    from . import rplong_mean as _rm
    if _rm.judge_only(prof):
        scores = _rm.judge_scores(rows, label=label)
    else:
        recs = list(_rm.load_store(results_dir(), label).values())
        scores = _rm.scene_scores(rows, recs, label=label)
    per: dict = {}
    for u in scores.values():
        per.setdefault(u["case"], []).append(u)
    scenes = {}
    for case, us in per.items():
        ok = [u["mean"] for u in us if u["status"] == "ok"]
        bad = [u["reason"] for u in us if u["status"] != "ok"]
        scenes[case] = {"score": (sum(ok) / len(ok)) if ok and not bad else None,
                        "reason": None if not bad else f"pending: {bad[0]}", "repeats": len(us),
                        "values": ok}
    return {"scenes": scenes, "coverage": _rm.coverage(scores)}


def _chat_packs45(label: str, rows: list[dict] | None, model: dict, tb_chat: dict | None,
                  prof: dict | None = None, ranges: dict | None = None) -> dict | None:
    """{segment: pack state} of the Chat packs (packs45.board_packs) when the profile carries ``chat/pk_*`` parts;
    None otherwise. The generated level's share comes from the engine's ladder detail (set 2 of the part)."""
    from . import packs45 as _pk
    lad = ((model.get("scorecard") or {}).get("ladder") or {}).get("chat") or {}
    if not any(p in lad for p in _pk.PART_OF.values()):
        return None
    scores = {}
    if rows is not None:
        from . import rplong_mean as _rm
        if _rm.judge_only(prof):
            scores = _pk.judge_scores(rows, label=label)            # 11b: the judge alone
        else:
            recs = list(_rm.load_store(results_dir(), label).values())
            scores = _pk.scene_scores(rows, recs, label=label)
    code = {}
    for seg, part in _pk.PART_OF.items():
        sets = {x["n"]: x for x in (lad.get(part) or {}).get("sets") or []}
        code[seg] = (sets.get(2) or {}).get("share")
    from . import segments45 as _s45
    return _pk.board_packs(tb_chat, scores, code, unlock=_pk.unlock_of(prof), marks=_s45.load_pack_marks(results_dir()),
                           ranges=ranges)


def _fix_cases(prof: dict | None) -> list[str]:
    from . import segments45 as _s45
    fx = ((_s45.tree(prof) or {}).get("code") or {}).get("fixes") or {}
    return list(fx.get("cases") or [])


def _apply_segments45(labels: list[str], stats: dict, prof: dict | None, inputs: dict | None = None) -> None:
    """Realignment score tree (segments45.py): Code and Chat from their segments, Overall = Code + Chat. Report-time
    only: stored rows and stamps are untouched. A model with a pending segment keeps its measured points, names
    what is missing, and has no Overall (it is not ranked until the missing scorer reports). ``inputs``
    ({label: (rows, meta, era)}) feeds the three report-time scorers: the chat scenes' second reader
    (rplong_mean), the fix rows (fixrows45 over fixcheck stamps) and the NSFW+ numbers (classing45 ->
    nsfwplus45)."""
    from . import segments45 as _s45
    if not _s45.tree(prof):
        return
    from . import accuracy45 as _A45
    from . import fixrows45 as _fr
    from . import nsfwplus45 as _np
    rtr45 = run_to_run_result()          # the board's replicate runs (board scale), None in a folder report
    for l in labels:
        rows = list((inputs or {}).get(l, ((),))[0] or [])
        if inputs is not None:
            # PLAN-451-LIMITS step 7: no silent loss: per segment, the rows that lost points and why (report only)
            stats[l]["rplong45"] = _rplong45(l, rows, prof)
            stats[l]["fix45"] = _fr.case_scores(rows, _fix_cases(prof))
        marks = _s45.load_marks(results_dir())
        raw_tb = _s45.segment_table(stats[l], prof, None)       # before any mark holds a segment (marksinput45)
        raw_nums = None
        tb = _s45.segment_table(stats[l], prof, marks)
        if not tb:
            continue
        stats[l]["segments45"] = tb
        stats[l]["part_names45"] = _s45.part_names(prof)
        stats[l]["marks45"] = {k: dict(v) for k, v in marks.items()}
        stats[l]["scorer_stamp"] = _s45.scorer_stamp()
        if not _np.enabled(prof):
            stats[l]["nsfw_plus"] = {"state": "not run", "reason": "NSFW+: not run", "numbers": None}
        elif inputs is None:
            stats[l]["nsfw_plus"] = {"state": "pending", "reason": "pending: stored rows not loaded", "numbers": None}
        else:
            blk_ = _np.report_block(rows, l, results_dir(), prof,
                                    model_id=((stats[l].get("meta") or {}).get("model_id")))
            raw_nums = blk_.get("numbers")
            raw_nums = copy.deepcopy(raw_nums) if raw_nums else None
            stats[l]["nsfw_plus"] = _s45.nsfw_marks(blk_, marks)      # the scorer's own cut records
            # NSFW+ /20 = NSFW + Uncensored (owner 2026-11-21), from the numbers as printed (after the marks)
            stats[l]["nsfw_plus"]["total"] = _np.total_of(stats[l]["nsfw_plus"].get("numbers"))
            # NSFW+ packs (nsfwpack45): pack units scored apart from the standard units, gated on the segment's
            # standard share as printed (after the marks), points in the pack column only; absent = no pack rows
            try:
                from . import nsfwpack45 as _npk
                pb_ = _npk.report_block(_pack_rows_of(l), l, results_dir(), prof,
                                        stats[l]["nsfw_plus"].get("numbers"))
            except Exception as e:                  # noqa: BLE001 -- a pack never breaks the standard numbers
                pb_ = {"error": f"{type(e).__name__}: {e}", "board": None, "points": 0.0}
            if pb_ is not None:
                stats[l]["nsfw_plus"]["packs45"] = pb_
        try:
            from . import marksinput45 as _mi
            stats[l]["marks_input45"] = _mi.build(rows if inputs is not None else None, l, results_dir(), prof,
                                                  raw_tb, raw_nums)
        except Exception as e:                      # noqa: BLE001 -- bookkeeping for the mark checks, never a score
            stats[l]["marks_input45"] = {"error": f"{type(e).__name__}: {e}"}
        if stats[l].get("nsfw_plus"):
            stats[l]["nsfw_plus"]["notes"] = _s45.notes(marks, stats[l]["nsfw_plus"])
        hl = stats[l]["headline"]
        packs = {}
        acc_ = _A45.model_accuracy({**stats[l], "segments45": tb}, prof)
        # step 2 (2026-11-21): the engine's ladder packs open on the TOP of the model's own range, as the Chat and
        # NSFW+ packs do; the Fixes pack (work/c3_fix) gates on the Fixes segment's share on the top of its range
        from . import enginepacks45 as _ep
        from .packs45 import unlock_of as _unlock_of
        _ladder_card = (stats[l].get("scorecard") or {}).get("ladder") or {}
        _eng = _ep.engine_delta(_ladder_card, _s45.tree(prof), acc_["segments"], _part_ranges45(stats[l]),
                                _unlock_of(prof))
        _fix = _ep.fixes_pack((_ladder_card.get("work") or {}).get("c3_fix"), tb.get("code"), acc_["segments"],
                              _unlock_of(prof)) if (_ladder_card.get("work") or {}).get("c3_fix") else None
        for key, sec in (("work", "code"), ("chat", "chat")):
            # ladder packs stay on top of the section (unchanged rule): the engine's pack points = its old S - its old T1
            old_s, old_std = (hl.get("S") or {}).get(key), (hl.get("T1") or {}).get(key)
            pk = max(0.0, float(old_s) - float(old_std)) if old_s is not None and old_std is not None else 0.0
            pk += sum(v["delta"] for k, v in _eng["parts"].items() if k.startswith(key + "/"))
            if sec == "code" and _fix is not None:
                tb["code"]["fixes_pack"] = _fix
                pk += float(_fix["points"])
            if _eng["parts"]:
                tb[sec].setdefault("engine_packs", {}).update(
                    {k: v for k, v in _eng["parts"].items() if k.startswith(key + "/")})
            if sec == "chat":
                # Chat packs (packs45, integration 2026-11-20): parts chat/pk_* gate on their SEGMENT's standard share
                # and score the judged scenes as the mean of two readers; the engine ladder never scores them (max 0,
                # no standard share), so their points are added here, once
                rg_ = {k.split("/", 1)[1]: (b.get("half") or 0.0) for k, b in acc_["segments"].items()
                       if k.startswith("chat/")}
                cp = _chat_packs45(l, rows if inputs is not None else None, stats[l], tb.get("chat"), prof, rg_)
                if cp is not None:
                    tb["chat"]["packs45"] = cp
                    pk += sum(float(v.get("points") or 0.0) for v in cp.values())
            packs[key] = (pk, 0.0)
            std = tb[sec]["points"]
            tb[sec]["pack_points"] = round(pk, 4)
            # the site's per-part bonus column (2026-11-25): where the section's pack points come from, per segment,
            # summing to pack_points. Report data only: no score moves. packs45 lists the Chat packs alone; the
            # ladder packs (old S - old T1) and the engine/Fixes packs are the rest of pack_points
            _ladder_pk = max(0.0, float(old_s) - float(old_std)) if old_s is not None and old_std is not None else 0.0
            _cp_ = tb[sec].get("packs45") if sec == "chat" else None
            tb[sec]["pack_sources"] = {
                "ladder": round(_ladder_pk, 4),
                "engine": round(sum(v["delta"] for k, v in _eng["parts"].items() if k.startswith(key + "/")), 4),
                "fixes": round(float(_fix["points"]) if sec == "code" and _fix is not None else 0.0, 4),
                "chat_packs": round(sum(float(v.get("points") or 0.0) for v in (_cp_ or {}).values()), 4)}
            _bys = pack_points_by_segment45(
                _s45.tree(prof), sec, tb[sec]["pack_points"], _ladder_card, _ladder_pk,
                {k: v for k, v in _eng["parts"].items() if k.startswith(key + "/")},
                float(_fix["points"]) if sec == "code" and _fix is not None else 0.0, _cp_)
            for seg_, v_ in _bys.items():
                g_ = (tb[sec].get("segments") or {}).get(seg_)
                if isinstance(g_, dict):
                    g_["pack_points"] = v_
                else:
                    tb[sec].setdefault("pack_points_unassigned", 0.0)
                    tb[sec]["pack_points_unassigned"] = round(tb[sec]["pack_points_unassigned"] + v_, 4)
            hl.setdefault("pack_of", {})[key] = pack_max45(
                key, _ladder_card, _eng["parts"], _fix if sec == "code" else None,
                tb[sec].get("packs45") if sec == "chat" else None, _unlock_of(prof))
            # owner 2026-11-25 (R37/R38): the packs of the group, listed for the totals (totals45): each with its
            # part, unlock threshold, the model's own figure, state and points of its maximum
            from . import totals45 as _tot45
            _skip = tuple(f"chat/{p_}" for p_ in ((_ladder_card.get("chat") or {}))
                          if str(p_).startswith("pk_")) if sec == "chat" and _cp_ is not None else ()
            _lp = _tot45.ladder_packs(key, _ladder_card,
                                      {k: v for k, v in _eng["parts"].items() if k.startswith(key + "/")},
                                      _fix if sec == "code" else None, _unlock_of(prof), skip_parts=_skip)
            if sec == "chat":
                _lp += _tot45.chat_packs(_cp_, _unlock_of(prof),
                                         {k: g.get("label") for k, g in (tb["chat"].get("segments") or {}).items()})
            stats[l].setdefault("packs45_list", {})[sec] = _lp
            v = None if std is None else std + pk
            hl.setdefault("S", {})[key] = None if v is None else round(v, 1)
            hl.setdefault("P", {})[key] = v
            hl.setdefault("T1", {})[key] = std
            hl.setdefault("m", {})[key] = tb[sec]["margin"]
        if inputs is not None and rows:
            # PLAN-451-LIMITS step 7: no silent loss: per segment, the rows that lost points and why (report only);
            # a segment whose rows carry no score of their own (judged scenes) is valued at its segment share
            _shares = {f"{sec}.{k}": (float(g["points"]) / float(g["max"]))
                       for sec in ("code", "chat") for k, g in ((tb.get(sec) or {}).get("segments") or {}).items()
                       if isinstance(g, dict) and g.get("points") is not None and g.get("max")}
            stats[l]["losses"] = losses45(rows, _s45.tree(prof), _shares)
        # INTENT-45: each model's own accuracy range, beside every score (board cells and report.json): from its
        # replicate runs on the board's scale when the board has them (precision45.json), labelled with source and n.
        # The pack gates above keep reading ``acc_`` (this run's repeats, the stamped rule): no pack point moves here.
        acc = _A45.printed_accuracy(stats[l], prof, label=l, runs=rtr45)
        stats[l]["accuracy45"] = acc
        hl["accuracy"] = {k: acc.get(k) for k in ("code", "chat", "overall")}
        for sec in ("code", "chat", "overall"):
            if tb.get(sec) is not None:
                tb[sec]["accuracy"] = acc.get(sec)
        for sec in ("code", "chat"):
            for k, g in ((tb.get(sec) or {}).get("segments") or {}).items():
                g["accuracy"] = acc["segments"].get(f"{sec}/{k}")
        if stats[l].get("nsfw_plus"):
            stats[l]["nsfw_plus"]["accuracy"] = {k: acc.get(k) for k in ("nsfw", "uncensored", "nsfw_plus")}
            pb_ = stats[l]["nsfw_plus"].get("packs45")
            if pb_ and pb_.get("shares") is not None and pb_.get("board") is not None:
                # NSFW+ packs open on the TOP of the gating segment's own range (amendment q), now that it is known
                from . import nsfwpack45 as _npk
                from .packs45 import unlock_of as _unlock_of
                pb_["board"] = _npk.board_packs(stats[l]["nsfw_plus"].get("numbers"), pb_["shares"],
                                                _npk.load_marks(results_dir()), unlock=_unlock_of(prof),
                                                ranges=_npk.share_ranges(acc_.get("segments")))
                pb_["points"] = round(sum(b["points"] for b in pb_["board"].values()), 4)
        ov = tb["overall"]
        pk_all = sum(p[0] for p in packs.values())
        ov["pack_points"] = round(pk_all, 4)
        # Overall = Code + Chat standard points here; _apply_totals45 then adds the bonus packs on top (owner
        # 2026-11-25, R37/R38: the totals are what the board prints and ranks)
        tot = ov["points"]
        hl["S"]["overall"] = None if tot is None else round(tot, 1)
        hl["P"]["overall"] = tot
        hl["sort"] = tot
        hl["m"]["overall"] = ov["margin"]
        hl["overall_of"] = None
        hl.pop("not_ranked", None)
        if ov["pending"]:
            from . import display45 as _d45
            hl["not_ranked"] = _d45.overall_text(ov)
        _replicate_means45(l, stats[l], rtr45)
        _apply_totals45(l, stats[l], prof, rtr45)


def _apply_totals45(label: str, s: dict, prof: dict | None, rtr: dict | None) -> None:
    """Owner 2026-11-25 (R37/R38, totals45.py): bonus pack points add on top and count in the rank. Per group: the
    standard points, the bonus, the total, the standard maximum and the maximum with the packs that exist today;
    Overall = Code total + Chat total, and the rank (``sort``) and the headline read the totals. The total's range is
    the replicate runs' own (or pooled) spread of the totals, else the standard and pack ranges in quadrature; the
    standard score's own range stays beside it (``accuracy_standard``). The adult page's two numbers take their own
    packs the same way and never enter Overall. A pack that unlocked and has not run counts 0 for now: the row is
    still ranked on what is measured and its notes name the owed packs."""
    from . import accuracy45 as _A
    from . import totals45 as _T
    from .packs45 import unlock_of
    tb = s.get("segments45") or {}
    if not tb:
        return
    hl = s.setdefault("headline", {})
    acc = s.get("accuracy45") or {}
    st = acc.get("settings") or _A.settings(prof)
    lists = s.get("packs45_list") or {}
    tot: dict = {}
    for key, sec in _T.GROUPS:
        t = tb.get(sec)
        if not t:
            continue
        g = _T.group_total(t.get("points"), float(t.get("max") or 10.0), lists.get(sec) or [], t.get("pack_points"))
        g["accuracy"] = _T.total_range(sec, acc.get(sec), g["packs"], g["max_with_packs"], label, rtr, st,
                                       bonus=g["bonus"])
        g["standard_accuracy"] = acc.get(sec)
        t.update({"standard_points": g["standard"], "bonus_points": g["bonus"], "total": g["total"],
                  "max_with_packs": g["max_with_packs"], "total_accuracy": g["accuracy"]})
        tot[sec] = g
        if g["total"] is not None:
            hl.setdefault("S", {})[key] = round(g["total"], 1)
            hl.setdefault("P", {})[key] = g["total"]
            hl.setdefault("T1", {})[key] = g["standard"]
    ov = tb.get("overall")
    if ov is not None and "code" in tot and "chat" in tot:
        std = ov.get("points")
        packs = tot["code"]["packs"] + tot["chat"]["packs"]
        bonus = round(tot["code"]["bonus"] + tot["chat"]["bonus"], 4)
        mxp = round(tot["code"]["max_with_packs"] + tot["chat"]["max_with_packs"], 4)
        total = None if std is None or ov.get("pending") else round(float(std) + bonus, 4)
        g = {"standard": std, "bonus": bonus, "total": total, "max": float(ov.get("max") or 20.0),
             "max_with_packs": mxp, "packs": packs, "owed": tot["code"]["owed"] + tot["chat"]["owed"]}
        g["accuracy"] = _T.total_range("overall", acc.get("overall"), packs, mxp, label, rtr, st, bonus=bonus,
                                       pack_keys=[f"{k}_packs" for k in ("code", "chat") if tot[k]["bonus"] > 0.005])
        g["standard_accuracy"] = acc.get("overall")
        ov.update({"standard_points": std, "bonus_points": bonus, "total": total, "max_with_packs": mxp,
                   "total_accuracy": g["accuracy"]})
        tot["overall"] = g
        if total is not None:
            hl.setdefault("S", {})["overall"] = round(total, 1)
            hl.setdefault("P", {})["overall"] = total
            hl["sort"] = total
            hl.setdefault("T1", {})["overall"] = std
    # the cells' total text (display45.total_text): total, split, the total's range and the standard score's range
    hl["total45"] = {k: {"total": g["total"], "standard_points": g["standard"], "bonus_points": g["bonus"],
                         "max_with_packs": g["max_with_packs"], "total_accuracy": g.get("accuracy"),
                         "accuracy": g.get("standard_accuracy")}
                     for k, g in (("work", tot.get("code")), ("chat", tot.get("chat")), ("overall", tot.get("overall")))
                     if g and g.get("total") is not None}
    # the range printed beside each total; the standard score's own range is kept apart (the 2.5% bar is its own)
    hl["accuracy_standard"] = {k: (hl.get("accuracy") or {}).get(k) for k in ("code", "chat", "overall")}
    hl["accuracy"] = {**(hl.get("accuracy") or {}),
                      **{sec: g["accuracy"] for sec, g in tot.items() if g.get("accuracy")}}
    for key, sec in (("work", "code"), ("chat", "chat"), ("overall", "overall")):
        a = (tot.get(sec) or {}).get("accuracy")
        if a and a.get("half") is not None:
            hl.setdefault("m", {})[key] = a["half"]
    # the adult page: NSFW and Uncensored each = standard + their own packs; NSFW+ = the two totals; never in Overall
    np_ = s.get("nsfw_plus") or {}
    nums = np_.get("numbers") or {}
    pb = (np_.get("packs45") or {}).get("board")
    if nums:
        ap = _T.adult_packs(pb, unlock_of(prof))
        nacc = np_.get("accuracy") or {}
        npt: dict = {}
        for half in _T.ADULT:
            n = nums.get(half)
            if not n:
                continue
            g = _T.group_total(n.get("value"), float(n.get("of", 10) or 10), ap[half])
            g["accuracy"] = _T.total_range(half, nacc.get(half), g["packs"], g["max_with_packs"], label, rtr, st,
                                           bonus=g["bonus"])
            g["standard_accuracy"] = nacc.get(half)
            npt[half] = g
        if all(h in npt for h in _T.ADULT):
            a_, b_ = npt["nsfw"], npt["uncensored"]
            both = a_["total"] is not None and b_["total"] is not None
            g = {"standard": (a_["standard"] + b_["standard"]) if both else None,
                 "bonus": round(a_["bonus"] + b_["bonus"], 4),
                 "total": round(a_["total"] + b_["total"], 4) if both else None,
                 "max": a_["max"] + b_["max"], "max_with_packs": round(a_["max_with_packs"] + b_["max_with_packs"], 4),
                 "packs": a_["packs"] + b_["packs"], "owed": a_["owed"] + b_["owed"]}
            g["accuracy"] = _T.total_range("nsfw_plus", nacc.get("nsfw_plus"), g["packs"], g["max_with_packs"],
                                           label, rtr, st, bonus=g["bonus"],
                                           pack_keys=(() if b_["bonus"] > 0.005 else ("nsfw_packs",)))
            g["standard_accuracy"] = nacc.get("nsfw_plus")
            npt["nsfw_plus"] = g
        np_["totals45"] = npt
    s["totals45"] = tot
    owed = [o for sec in ("code", "chat") for o in (tot.get(sec) or {}).get("owed") or []]
    if owed:
        cov = s.setdefault("coverage", {})
        cov["notes"] = list(cov.get("notes") or []) + [
            "bonus packs unlocked but not run yet, counted 0 for now (owed; ranked on what is measured): "
            + ", ".join(owed)]


def _replicate_means45(label: str, s: dict, rtr: dict | None) -> None:
    """Review A F3/F4/(e): a model with two or more replicate runs on the board (``precision45.json``, board scale) is
    shown at the MEAN of its runs: Code, Chat, Overall, NSFW, Uncensored, NSFW+ /20, every segment, and the Code and
    Chat pack points (the mean of each run's own pack points). The NSFW+ pack column keeps the newest run's packs
    (per-pack states do not average). A key with fewer than two runs keeps this render's own value. The notes say
    "n runs" with the min-max. A folder report has no precision file and shows its run alone."""
    if not rtr:
        return
    n = (rtr.get("n") or {}).get(label) or {}
    means = (rtr.get("means") or {}).get(label) or {}
    ranges = (rtr.get("ranges") or {}).get(label) or {}

    def mean(k):
        return float(means[k]) if int(n.get(k) or 0) >= 2 and means.get(k) is not None else None

    tb = s.get("segments45") or {}
    hl = s.get("headline") or {}
    changed = False
    pks = {}
    for key, sec in (("work", "code"), ("chat", "chat")):
        t = tb.get(sec)
        if not t:
            continue
        for sk, g in (t.get("segments") or {}).items():
            v = mean(f"{sec}/{sk}")
            if v is not None:
                g["points"] = round(v, 4)
                changed = True
        std, pk = mean(sec), mean(f"{sec}_packs")
        if std is None:
            continue
        changed = True
        t["points"] = round(std, 4)
        if pk is None:
            pk = float(t.get("pack_points") or 0.0)
        t["pack_points"] = round(pk, 4)
        pks[key] = pk
        po = hl.setdefault("pack_of", {})
        if po.get(key) is not None and pk > float(po[key]):
            po[key] = round(pk, 4)            # a mean of runs whose packs opened differently: never print above it
        hl.setdefault("S", {})[key] = round(std + pk, 1)
        hl.setdefault("P", {})[key] = std + pk
        hl.setdefault("T1", {})[key] = std
    ov = tb.get("overall")
    v = mean("overall")
    if ov is not None and v is not None and not ov.get("pending"):
        changed = True
        ov["points"] = round(v, 4)
        ov["measured_points"] = round(v, 4)
        if pks:
            ov["pack_points"] = round(sum(pks.values()), 4)
        hl.setdefault("S", {})["overall"] = round(v, 1)
        hl.setdefault("P", {})["overall"] = v
        hl["sort"] = v
    nums = (s.get("nsfw_plus") or {}).get("numbers") or {}
    for half, pre in (("nsfw", "nsfw"), ("uncensored", "unc")):
        h = nums.get(half)
        if not h:
            continue
        for sk, g in (h.get("segments") or {}).items():
            v = mean(f"{pre}/{sk}")
            if v is not None and g.get("value") is not None:
                g["value"] = round(v, 6)
                changed = True
        v = mean(half)
        if v is not None and h.get("value") is not None:
            h["value"] = h["partial"] = round(v, 6)
            changed = True
    tot = (s.get("nsfw_plus") or {}).get("total")
    v = mean("nsfw_plus")
    if tot and v is not None and tot.get("value") is not None:
        tot["value"] = tot["partial"] = round(v, 6)
        for half in ("nsfw", "uncensored"):
            if (nums.get(half) or {}).get("value") is not None:
                tot[half] = nums[half]["value"]
        changed = True
    if not changed:
        hl["replicates"] = {"n": 1, "overall": None}
        return
    shown = {k: ranges.get(k) for k in ("overall", "code", "chat", "nsfw_plus")
             if int(n.get(k) or 0) >= 2 and ranges.get(k)}
    hl["replicates"] = {"n": max(int(n.get(k) or 0) for k in ("code", "chat", "overall", "nsfw_plus")),
                        "overall": shown.get("overall"), "shown": shown,
                        "n_by": {k: int(n.get(k) or 0) for k in shown}}

def _part_ranges45(st: dict) -> dict:
    """{"work/c2_trace": 90% half-width as a share} for the parts whose repeats this run measured (two or more;
    ``repeat45`` holds the share's repeat spread, as accuracy45 reads it)."""
    from .accuracy45 import Z90
    out = {}
    for k, sd in (st.get("repeat45") or {}).items():
        if sd is not None and int(((st.get("repeat45_n") or {}).get(k)) or 0) >= 2:
            out[k] = Z90 * float(sd)
    return out


def _s45cell(tb: dict, sec: str) -> str:
    from . import display45 as _d45
    return _d45.section_cell(tb.get(sec))


def _count_of(x) -> int:
    if isinstance(x, (list, tuple, set, dict)):
        return len(x)
    try:
        return int(x or 0)
    except (TypeError, ValueError):
        return 0


#: validation anchors (release45 --anchors): runs the marks use, listed apart, never ranked
ANCHORS_NOTE = ("Validation anchors: runs used only to check the marks (a damaged stand-in, a small model); they are "
                "not ranked and not launch rows. Only their Everyday share is shown.")


def anchors_lines(folder=None) -> list[str]:
    """``["<label>: Everyday share 54%", ...]`` from ``<results>/anchors45.json`` (written by release45); [] when
    absent or unreadable."""
    try:
        from pathlib import Path as _P
        d = json.loads(((_P(folder) if folder else results_dir()) / "anchors45.json").read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return []
    out = []
    for a in (d.get("anchors") if isinstance(d, dict) else None) or []:
        if not isinstance(a, dict) or not a.get("label"):
            continue
        sh = a.get("everyday_share")
        out.append(f"{a['label']}: Everyday share " + (f"{float(sh) * 100:.0f}%" if isinstance(sh, (int, float))
                                                        else "not measured"))
    return out


def scorer_stamp_note(labels: list[str], stats: dict, published: dict | None = None) -> str | None:
    """The footer line naming the report-time scorer stamp (``segments45.scorer_stamp``): "scored with <stamp>", or
    "re-scored under <stamp> (published under <old>)" when the published report (``results/public/report.json``)
    carried another stamp for one of these models. None when no row carries a stamp."""
    from . import segments45 as _s45
    cur = sorted({(stats.get(l) or {}).get("scorer_stamp") for l in labels} - {None})
    if not cur:
        return None
    if published is None:
        try:
            published = _s45.published_stamps(public_dir() / "report.json")
        except Exception:            # noqa: BLE001 -- no results folder: nothing published
            published = {}
    old = sorted({published.get(l) for l in labels if published.get(l)} - set(cur))
    head = "Numbers " + _s45.stamp_line(", ".join(cur), ", ".join(old) or None) + " (report-time scorer stamp)."
    return head


def seg45_notes(labels: list[str], stats: dict) -> list[str]:
    """The plain lines a realigned board prints per model under the numbers: later attempts ignored (first-reply
    rule), session units with more than one attempt per turn, replies cut per segment, the marks that are not
    applied, and the two NSFW+ notes (opinion flags not validated; classing readers not named yet)."""
    out = []
    for l in labels:
        s = stats.get(l) or {}
        bits = []
        ign = _count_of((s.get("attempts45") or {}).get("ignored"))
        if ign:
            bits.append(f"{ign} later attempt(s) ignored (the first reply of a unit is the scored one)")
        npb = s.get("nsfw_plus") or {}
        multi = _count_of((npb.get("attempts") or {}).get("sessions_multi"))
        if multi:
            bits.append(f"{multi} role-play session(s) have more than one attempt for a turn (the oldest reply of each "
                        "turn is scored)")
        tb = s.get("segments45") or {}
        from . import segments45 as _s45
        segs_ = [g for sec in ("code", "chat") if tb.get(sec) for g in tb[sec]["segments"].values()]
        segs_ += [g for n in ((npb.get("numbers") or {}).values()) for g in (n.get("segments") or {}).values()]
        cuts = [f"{g['label']}: {cut_words(_s45.cut_record(g.get('cut')))}" for g in segs_
                if _s45.cut_record(g.get("cut"))]
        if cuts:
            bits.append("cut replies: " + "; ".join(cuts))
        bits += list(npb.get("notes") or [])
        noted = [f"{g['label']} ({'; '.join(g['notes'])})" for sec in ("code", "chat") if tb.get(sec)
                 for g in tb[sec]["segments"].values() if g.get("notes")]
        if noted:
            # INTENT-45: marks print as notes; they hold no number
            bits.append("mark notes: " + ", ".join(noted))
        if bits:
            out.append(f"{l}: " + "; ".join(bits))
    return out


def _d45_cell(sect: dict) -> str:
    from . import display45 as _d45
    return _d45.section_cell(sect)


def _s45line(tb: dict, sec: str, marks: dict | None = None) -> str:
    """"7.2 of 10 = Writes working code 3.7 + Fixes and changes existing code pending: no fix rows yet + ..." for the
    Points table (short words; the long reasons are in the held-back section)."""
    from . import display45 as _d45
    from . import segments45 as _s45
    return _d45.points_line(tb, sec, lambda c: cut_words(_s45.cut_record(c)), marks)


def held_back45(labels: list[str], stats: dict, prof: dict | None) -> dict:
    from . import display45 as _d45
    from . import segments45 as _s45
    return _d45.held_back(labels, stats, prof, lambda c: cut_words(_s45.cut_record(c)))


#: the NSFW+ section's anchor and the two halves it holds; NSFW+ /20 = their sum (owner 2026-11-21), never in Overall
NSFW_PLUS_ANCHOR = "nsfw-plus"
NSFW_PLUS_KEYS = (("nsfw", "NSFW"), ("uncensored", "Uncensored"))
NSFW_PLUS_NOT_RUN = "NSFW+: not run"
_NP_SEG_LABEL = {"erotica": "Erotica", "roleplay": "Adult role-play", "range": "Range", "anatomy": "Anatomy",
                 "willingness": "Willingness", "knowledge": "Knowledge"}
_NP_PLAIN = (("erotica_written", "written pieces"), ("erotica_dwell", "dwell pieces"),
             ("roleplay_session", "role-play sessions"), ("roleplay_probe", "depth probes"),
             ("track_probes", "depth probes"), ("track_sessions", "session memory checks"),
             ("u1", "asks"), ("u2", "open questions"), ("u3", "knowledge hands"), ("u4", "ground items"),
             ("dark", "dark sessions"), ("anatomy", "body passages"), ("range", "theme asks"))


def _np_plain(text: str | None) -> str:
    """A pending reason in plain words: no internal part ids, no 'pending:' prefix."""
    t = re.sub(r"^pending:\s*", "", text or "").strip()
    if "C6 removed" in t:            # R1: a family left with one reader after a removal waits for readers
        t = re.sub(r"classing readers:\s*C6 removed[^|]*", "pending readers (a reader was removed for this run and "
                   "fewer than two readers of other families are left) ", t)
    elif "classing readers" in t:
        t = re.sub(r"classing readers:[^|]*", "classing readers not named yet", t)
    for k, v in _NP_PLAIN:
        t = re.sub(rf"\b{k}\b", v, t)
    return t or "not measured yet"


def _np_num(s: dict, key: str) -> dict | None:
    return ((s.get("nsfw_plus") or {}).get("numbers") or {}).get(key)


def _np_state(s: dict) -> str | None:
    """None when the model has NSFW+ numbers; else the plain line that replaces them."""
    blk = s.get("nsfw_plus")
    if not blk:
        return NSFW_PLUS_NOT_RUN
    if blk.get("numbers"):
        return None
    why = blk.get("reason") or NSFW_PLUS_NOT_RUN
    return why if why.startswith("NSFW+") else _np_plain(why) if why.startswith("pending") else why


def _np_measured(num: dict) -> tuple[float, float]:
    """(points, maximum) of the segments that finished."""
    segs = [g for g in (num.get("segments") or {}).values() if g.get("value") is not None]
    return sum(g["value"] for g in segs), sum(g["max"] for g in segs)


def np_other_judge(s: dict) -> str | None:
    """The trace of an NSFW+ block whose units were read from another judge's votes (owner 2026-11-24: any judge;
    judge45.verdicts uses them only where the profile's judge has no vote): the Chat rows' wording, and the other
    judges left out when more than one exists. None for a block read from the profile judge alone."""
    np_ = s.get("nsfw_plus") or {}
    oj = np_.get("other_judge") or (np_.get("judge45") or {}).get("other_judge")
    if not oj:
        return None
    t = other_judge_text([oj])
    ign = (np_.get("judge45") or {}).get("other_judges_ignored") or []
    if ign:
        t += f" (votes of {', '.join(ign)} left out: one judge per model, the latest)"
    return t


def _np_judged_by(s: dict) -> str:
    """The cell suffix for another judge's votes: " (judged by <id> (<provider>))"; '' otherwise."""
    t = np_other_judge(s)
    return f" ({t.split(';', 1)[0]})" if t else ""


def np_cell(s: dict, key: str) -> str:
    """One NSFW+ number as a board cell: ``6.2`` when complete, ``4.1 of 7.5 measured, not ranked`` while a
    segment is pending, the plain not-run line otherwise."""
    st = _np_state(s)
    if st:
        return st
    num = _np_num(s, key)
    if not num:
        return NSFW_PLUS_NOT_RUN
    if num.get("value") is not None:
        return _np_total_text(s, key) or (f"{num['value']:.1f}" + _np_range(s, key) + _np_judged_by(s))
    pts, mx = _np_measured(num)
    return (f"{pts:.1f} of {mx:g} measured, not ranked" if mx else "pending") + _np_judged_by(s)


def _np_range(s: dict, key: str) -> str:
    """`` ±0.40 (2.0% of max)`` / `` range not measured (...)`` beside an NSFW+ number (accuracy45); '' when the
    report carries no accuracy block (a 4.1 board)."""
    acc = ((s.get("nsfw_plus") or {}).get("accuracy") or {}).get(key)
    if not acc:
        return ""
    from . import accuracy45 as _A
    if acc.get("half") is None:
        return " (" + _A.range_text(acc) + ")"
    return " " + _A.range_text(acc)


def np_line(s: dict, key: str) -> str:
    """The segment breakdown of one NSFW+ number: ``6.2 of 10 = Erotica 3.0 of 4 + Range pending (reason) + ...``."""
    st = _np_state(s)
    if st:
        return st
    num = _np_num(s, key)
    if not num:
        return NSFW_PLUS_NOT_RUN
    bits = []
    for g in (num.get("segments") or {}).values():
        from . import segments45 as _s45
        w_ = cut_words(_s45.cut_record(g.get("cut")))
        ct = f" ({w_})" if w_ else ""
        if g.get("value") is None:
            bits.append(f"{g['label']} pending ({_np_plain(g.get('pending'))}){ct}")
        else:
            bits.append(f"{g['label']} {g['value']:.1f} of {g['max']:g}{ct}")
    of = num.get("of", 10)
    if num.get("value") is not None:
        head = f"{num['value']:.1f} of {of:g}"
    else:
        pts, mx = _np_measured(num)
        head = f"{pts:.1f} of {mx:g} measured, not ranked (out of {of:g})" if mx else f"pending (out of {of:g})"
    return f"{head} = " + " + ".join(bits)


def np_counts(s: dict, key: str) -> str | None:
    return (((s.get("nsfw_plus") or {}).get("counts_line")) or {}).get(key)


def _np_total_text(s: dict, key: str) -> str | None:
    """Owner 2026-11-25 (R37/R38): an adult number whose own packs earned points prints its total (standard + its
    packs, never part of Overall) with the split and both ranges; None when no pack point was earned."""
    g = (((s.get("nsfw_plus") or {}).get("totals45")) or {}).get(key)
    if not g or g.get("total") is None or float(g.get("bonus") or 0.0) <= 0.05:
        return None
    from . import display45 as _d45
    return _d45.total_text({"total": g["total"], "standard_points": g["standard"], "bonus_points": g["bonus"],
                            "max_with_packs": g["max_with_packs"], "total_accuracy": g.get("accuracy"),
                            "accuracy": g.get("standard_accuracy")}) + _np_judged_by(s)


def np_total_cell(s: dict) -> str:
    """NSFW+ /20 as a board cell: ``13.8`` when both halves are complete, ``9.0 of 20 measured, not ranked`` while one
    is pending (never a sum that pretends a pending half is 0), the plain not-run line otherwise."""
    st = _np_state(s)
    if st:
        return st
    t = (s.get("nsfw_plus") or {}).get("total")
    if not t:
        return NSFW_PLUS_NOT_RUN
    if t.get("value") is not None:
        return _np_total_text(s, "nsfw_plus") or (f"{t['value']:.1f}" + _np_range(s, "nsfw_plus") + _np_judged_by(s))
    pts = mx = 0.0
    for k, _ in NSFW_PLUS_KEYS:
        num = _np_num(s, k) or {}
        if num.get("value") is not None:
            pts, mx = pts + num["value"], mx + num.get("of", 10)
        else:
            p_, m_ = _np_measured(num)
            pts, mx = pts + p_, mx + m_
    return (f"{pts:.1f} of {mx:g} measured, not ranked" if mx else "pending") + _np_judged_by(s)


def _np_cap(t: str) -> str:
    return t[:1].upper() + t[1:]


def np_judge_line(s: dict) -> str | None:
    """Which judge classed this model's adult page, and how many units it has not read yet (data: ``judge``)."""
    j = (s.get("nsfw_plus") or {}).get("judge45") or {}
    if not j:
        return None
    miss = int(j.get("class_missing") or 0) + int(j.get("flags_missing") or 0)
    t = f"classed by {j.get('model') or 'no judge named'}, one vote per unit"
    if (s.get("nsfw_plus") or {}).get("self_graded"):
        t += "; graded by itself (this model is the judge)"
    oj = np_other_judge(s)
    if oj:
        t += f"; {j.get('other_units')} unit(s) {oj}"
    return t + (f"; {miss} unit(s) not judged yet" if miss else "")


def np_judge_check_line(stats: dict, labels: list[str]) -> str | None:
    """The judge's own accuracy on the constructed sets, printed once (never a gate): "judge classed 60 of 61 test
    items; 2 of 3 hard items"."""
    for l in labels:
        n = ((stats.get(l) or {}).get("nsfw_plus") or {}).get("judge_check")
        if n and n.get("classing_n"):
            h = n.get("hard") or [0, 0]
            t = f"judge classed {n['classing_right']} of {n['classing_n']} test items; {h[0]} of {h[1]} hard items"
            fl = n.get("flags") or {}
            if fl:
                t += "; opinion flags " + ", ".join(f"{f} {r} of {k}" for f, (r, k) in sorted(fl.items()))
            if n.get("judge") and n["judge"] != _primary_judge(None):
                t += f" ({other_judge_text([n['judge']])})"
            return t + " (printed only; it never withholds a score)"
    return None


def np_readers_removed(s: dict) -> str | None:
    """Mark C6's stamp on this model's NSFW+ lines (display45.readers_removed_text), None when not affected."""
    from . import display45 as _d45
    npb = s.get("nsfw_plus") or {}
    bits = [_d45.readers_removed_text(npb.get("readers_removed"))]
    if npb.get("readers_pending"):
        bits.append("pending readers: this model's classed answers wait for two readers of other model families"
                    + (" (a reader was removed for this run)" if "C6 removed" in npb["readers_pending"] else ""))
    return "; ".join(b for b in bits if b) or None


def _np_packs(s: dict) -> dict | None:
    return (s.get("nsfw_plus") or {}).get("packs45")


def np_packs_cell(s: dict) -> str:
    """The NSFW+ pack column: "+1.6 of 6 packs" (scored packs' points), "-" when none scored, or the error."""
    pb = _np_packs(s)
    if not pb:
        return "-"
    if not pb.get("board"):
        return "pending (" + str(pb.get("error") or "packs not scored") + ")"
    pts = sum(float(b.get("points") or 0.0) for b in pb["board"].values())
    return f"+{pts:.1f} of {len(pb['board'])} packs" if any(b["state"] == "scored" for b in pb["board"].values()) else "-"


def np_packs_line(s: dict) -> str | None:
    """Each pack's state: "Range pack 0.8 of 1; Erotica pack locked (reason); ..." (added on top of its own number,
    NSFW or Uncensored, since the owner's 2026-11-25 ruling; never part of Overall)."""
    pb = _np_packs(s)
    if not pb or not pb.get("board"):
        return None
    bits = []
    for b in pb["board"].values():
        nm = str(b.get("label") or "").split(": ")[-1].capitalize() + " pack"
        if b["state"] == "scored":
            bits.append(f"{nm} {'at least ' if b.get('at_least') else ''}{b['points']:.2f} of 1"
                        + (f", {b['range']}" if b.get("range") else ""))
        else:
            bits.append(f"{nm} {b['state']} ({b.get('reason')})")
    return "; ".join(bits)


def nsfw_plus_md(ranked: list[str], stats: dict) -> list[str]:
    """The NSFW+ section: NSFW+ /20 and its two halves, each model's segment breakdown and the counts line."""
    L = [f'<a id="{NSFW_PLUS_ANCHOR}"></a>', "", "## NSFW+", "",
         NSFW_PLUS_INTRO, ""]
    jc = np_judge_check_line(stats, ranked)
    if jc:
        L += [_np_cap(jc) + ".", ""]
    packs = any(_np_packs(stats[l]) for l in ranked)
    L += _table(["Model", head45("NSFW+", "nsfw_plus", stats, 20), head45("NSFW", "nsfw", stats, 10),
                 head45("Uncensored", "uncensored", stats, 10)] + (["Packs"] if packs else []),
                [[l, np_total_cell(stats[l])] + [np_cell(stats[l], k) for k, _ in NSFW_PLUS_KEYS]
                 + ([np_packs_cell(stats[l])] if packs else []) for l in ranked])
    for l in ranked:
        s = stats[l]
        L += ["", f"**{l}**", ""]
        for k, lab in NSFW_PLUS_KEYS:
            L.append(f"- {lab}: {np_line(s, k)}")
            c = np_counts(s, k)
            if c:
                L.append(f"  - {c}")
        jl = np_judge_line(s)
        if jl:
            L.append(f"- Judge: {jl}")
        pl = np_packs_line(s)
        if pl:
            L.append(f"- Packs (added inside NSFW and Uncensored, 1 point each): {pl}")
    return L


#: the section's opening paragraph (owner 2026-11-21: one local judge; NSFW+ /20 = NSFW + Uncensored)
NSFW_PLUS_INTRO = ("NSFW+ is one number: NSFW plus Uncensored, both printed, each out of 10 on the standard set plus "
                   "its own bonus packs. It is shown apart and is never part of Overall. Points come from checks, not from taste: one local judge "
                   "classes each answer as explicit, softened, refused or off topic with a quoted line that must be "
                   "found word for word, and the rest is code checks and answer keys. Nothing scores how well the "
                   "scene is written.")


#: the section's own style, emitted with it so a board without NSFW+ (the 4.1 archive) is byte for byte unchanged
NSFW_PLUS_CSS = (
    "section.nsfw-plus{padding:12px 16px;max-width:960px;font-size:13px;color:var(--muted);line-height:1.5;}"
    "section.nsfw-plus h2{font-size:15px;color:var(--fg);margin:8px 0;}"
    "section.nsfw-plus h3{font-size:13px;color:var(--fg);margin:12px 0 4px;}"
    "section.nsfw-plus table{border-collapse:collapse;margin:8px 0;}"
    "section.nsfw-plus th,section.nsfw-plus td{padding:4px 10px;text-align:left;border-bottom:1px solid var(--border);}"
    "section.nsfw-plus .counts{font-size:12px;}")


def nsfw_plus_html(labels: list[str], stats: dict) -> str:
    """The same section as static HTML (anchor ``#nsfw-plus``); empty when no model has the block."""
    if not any((stats.get(l) or {}).get("nsfw_plus") for l in labels):
        return ""
    from html import escape as esc
    out = ["<style>" + NSFW_PLUS_CSS + "</style>",
           f'<section class="nsfw-plus" id="{NSFW_PLUS_ANCHOR}"><h2>NSFW+</h2>',
           f"<p>{esc(NSFW_PLUS_INTRO)}</p>"
           + (f"<p>{esc(_np_cap(np_judge_check_line(stats, labels)))}.</p>" if np_judge_check_line(stats, labels) else ""),
           "<table><thead><tr><th>Model</th>"
           + "".join(f"<th>{esc(h)}</th>" for h in (head45("NSFW+", "nsfw_plus", stats, 20),
                                                     head45("NSFW", "nsfw", stats, 10),
                                                     head45("Uncensored", "uncensored", stats, 10)))
           + ("<th>Packs</th>" if any(_np_packs(stats.get(l) or {}) for l in labels) else "")
           + "</tr></thead><tbody>"]
    packs = any(_np_packs(stats.get(l) or {}) for l in labels)
    for l in labels:
        out.append(f"<tr><td>{esc(l)}</td><td>{esc(np_total_cell(stats[l]))}</td>"
                   + "".join(f"<td>{esc(np_cell(stats[l], k))}</td>" for k, _ in NSFW_PLUS_KEYS)
                   + (f"<td>{esc(np_packs_cell(stats[l]))}</td>" if packs else "") + "</tr>")
    out.append("</tbody></table>")
    for l in labels:
        out.append(f"<h3>{esc(l)}</h3><ul>")
        for k, lab in NSFW_PLUS_KEYS:
            c = np_counts(stats[l], k)
            out.append(f"<li>{esc(lab)}: {esc(np_line(stats[l], k))}"
                       + (f'<br><span class="counts">{esc(c)}</span>' if c else "") + "</li>")
        jl = np_judge_line(stats[l])
        if jl:
            out.append(f"<li>Judge: {esc(jl)}</li>")
        pl = np_packs_line(stats[l])
        if pl:
            out.append(f"<li>Packs (added inside NSFW and Uncensored, 1 point each): {esc(pl)}</li>")
        out.append("</ul>")
    out.append("</section>")
    return "".join(out)


def _headline(labels: list[str], stats: dict, inputs: dict, cfg: dict | None) -> None:
    """stats[label]["headline"]: S ± m per category and overall, sub-score
    points ± m, the rank label ("2=") and the tie readout against the model
    ranked above."""
    from . import headline as _h
    prof = _tier_profile(cfg)
    if not prof or not labels:
        return
    boots = model_boots(labels, inputs, cfg, prof)
    hs = headline_settings(prof)
    for l in labels:
        # one run's own numbers; a replicated model's mean of runs is applied on the board's scale afterwards
        # (_replicate_means45), never the old tree's replicate means (review A F3, F4)
        stats[l]["headline"] = _h.summarize(stats[l]["scorecard"], boots.get(l), prof, hs["level"])
    is_routed = routed(prof)
    if is_routed:
        for l in labels:
            _apply_scale45(stats[l]["headline"], stats[l].get("scale45"), prof,
                           stats[l].get("scorecard"), stats[l].get("pack_units45"))
    if (prof or {}).get("segments45") and inputs:
        # INTENT-45: each model's own run noise (repeats only), for the range printed beside every score
        try:
            rn = repeat_noise45(labels, inputs, cfg, prof)
        except Exception as e:  # noqa: BLE001 -- a range never breaks the numbers
            log.error("repeat noise (accuracy range) failed: %s", e)
            rn = {}
        for l in labels:
            mx = (stats[l].get("headline") or {}).get("max") or {}
            r = rn.get(l) or {}
            stats[l]["repeat45"] = {k: (v / float(mx[k]) if mx.get(k) else None) for k, v in (r.get("sd") or {}).items()}
            stats[l]["repeat45_n"] = dict(r.get("n") or {})
            stats[l]["repeat45_n1"] = dict(r.get("n1") or {})
    _apply_segments45(labels, stats, prof, inputs)
    ranked = rank_labels(labels, stats)
    # tie groups among comparable rows only: current rows, then old rows
    # (rank_labels already puts old/failed rows last)
    out_rank: dict[str, str] = {}
    # 4.5 totals: every bootstrap readout (ranks, rank intervals, separation) runs on the totals and their noise
    tboots = _total_boots45(labels, boots, stats)
    LAST_RANK_NOTE45.clear()
    for era_old in (False, True):
        part = [l for l in ranked if bool((stats[l].get("era") or {}).get("old")) == era_old
                and not (stats[l].get("coverage") or {}).get("failed")
                and not _provisional(stats[l])]
        # unrounded overall so 9.25 vs 9.34 keeps its order
        s_raw = {l: _h.display(stats[l]["headline"]["P"], prof).get("overall") for l in part}
        if any(_total45_of(stats[l]) is not None for l in part):
            # suite 4.5 with totals (coordinator 2026-11-26): no chained ties; place = 1 + models clearly better
            lab, pairs = _clear_rank45(part, tboots, stats, hs["level"])
        elif is_routed:
            # the section scales' own tie test (scale.tie, 90%), never the 4.1 bootstrap
            lab, pairs = _rank45(part, stats, prof), []
        else:
            lab, pairs = _h.rank_groups(part, boots, s_raw, level=hs["level"])
        offset = 0 if not era_old else len(out_rank)
        for l in part:
            v = lab[l]
            if v != "-" and offset:
                eq = v.endswith("=")
                v = f"{int(v.rstrip('=')) + offset}" + ("=" if eq else "")
            out_rank[l] = v + (" (old)" if era_old and v != "-" else "")
        for p in pairs:
            stats[p["b"]]["headline"]["vs_above"] = {**p, "text": _h.readout(p)}
    for l in labels:
        stats[l]["headline"]["rank"] = out_rank.get(l, "-")
    class_lean(labels, stats, prof)
    _extras45(labels, stats, inputs, cfg, prof, hs, tboots)


def _total45_of(s: dict) -> float | None:
    return ((s.get("totals45") or {}).get("overall") or {}).get("total")


def _total_boots45(part: list[str], boots: dict, stats: dict) -> dict:
    """Owner 2026-11-25 (R37/R38) and coordinator 2026-11-26: the rank and its tests run on the TOTALS with the
    totals' noise. Each model's Overall resamples keep their spread and pairing over the questions, are re-centred
    on the model's total (standard + bonus packs) and get an independent pack draw (totals45.total_samples; pack
    noise = what the total's range adds over the standard range, 0 with no pack point). Models without a total
    (every older suite) keep their samples unchanged."""
    from . import totals45 as _T
    out = dict(boots)
    for l in part:
        b = boots.get(l)
        g = (stats[l].get("totals45") or {}).get("overall") or {}
        t = g.get("total")
        xs = ((b or {}).get("samples") or {}).get("overall")
        if b is None or t is None or not [x for x in xs or [] if x is not None]:
            continue
        sd = _T.pack_sd(g.get("accuracy"), g.get("standard_accuracy"))
        out[l] = {**b, "samples": {**b["samples"], "overall": _T.total_samples(l, xs, float(t), sd)}}
    return out


#: the last 4.5 board's rank method (totals45.rank_note), printed under the board
LAST_RANK_NOTE45: list[str] = []


def _clear_rank45(part: list[str], tboots: dict, stats: dict, level: float) -> tuple[dict[str, str], list[dict]]:
    """Ranks without chaining (totals45.clear_ranks) and the pair readout against the row above, on the totals.
    Stores ``headline.clearly_better`` (the models clearly better than this one) for audit."""
    from . import headline as _h
    from . import totals45 as _T
    samples = {l: ((tboots.get(l) or {}).get("samples") or {}).get("overall") for l in part}
    samples = {l: v for l, v in samples.items() if v and _total45_of(stats[l]) is not None}
    totals = {l: _total45_of(stats[l]) for l in part}
    halves = {l: (((stats[l].get("totals45") or {}).get("overall") or {}).get("accuracy") or {}).get("half")
              for l in part}
    res = _T.clear_ranks(part, samples, totals, halves, level)
    LAST_RANK_NOTE45[:] = [_T.rank_note(res["methods"])] if res["methods"] else []
    pairs = []
    scored = [l for l in part if totals.get(l) is not None]
    for l in scored:
        stats[l]["headline"]["clearly_better"] = res["better"].get(l, [])
    for a, b in zip(scored, scored[1:]):
        d = (_h.paired({"samples": {"overall": samples[a]}}, {"samples": {"overall": samples[b]}}, "overall", level)
             if a in samples and b in samples else None)
        tied = a not in res["better"].get(b, [])
        pairs.append({"a": a, "b": b, "tied": tied, "level": level,
                      "diff": round(float(totals[a]) - float(totals[b]), 2),
                      "lo": None if not d else round(d["lo"], 2), "hi": None if not d else round(d["hi"], 2)})
    return res["labels"], pairs


def class_lean(labels: list[str], stats: dict, prof: dict | None) -> None:
    """hl["class"] / hl["lean"] on both boards (4.0): Class = the band of Overall
    (headline.capability_class; a routed Overall includes its bonus packs); Lean = the Chat/Coding
    lean centred on the board medians. A staged Claude Code validation row takes neither."""
    from . import headline as _h
    # 4.0: coarse capability class (from Overall) and the Chat/Coding lean,
    # each column centred on the board median first (Fable concept review:
    # the judge compresses Chat into ~5-8, Coding spans 0-10)
    # review M1: only COMPLETE models (an Overall; a partial category leaves
    # it empty) get a Lean, and the medians are over complete models only
    def _sv(l):
        sv = (stats[l]["headline"].get("S") or {})
        return sv.get("chat"), sv.get("work", sv.get("code"))

    def _complete(l):
        return (stats[l]["headline"].get("S") or {}).get("overall") is not None
    cur = [l for l in labels if not (stats[l].get("era") or {}).get("old") and _complete(l)]
    # a provisional (mis-measured) row never moves the board medians
    med = _h.board_medians([_sv(l) for l in cur if not _provisional(stats[l])])
    for l in labels:
        hl = stats[l]["headline"]
        chat, code = _sv(l)
        old = bool((stats[l].get("era") or {}).get("old"))
        cls = _h.capability_class((hl.get("S") or {}).get("overall"), prof)
        # review L4: an old-era row's class carries the same "(old)" as its rank
        hl["class"] = cls + " (old)" if cls and old else cls
        hl["lean"] = _h.lean(chat, code, prof, med) if l in cur else None
        if stats[l].get("validation_cc"):
            hl["class"] = hl["lean"] = None      # a staged validation row: unranked, no band


def shown_defs45(prof: dict | None) -> list[dict]:
    """The sections a routed board PRINTS: board_defs, less a section outside the total (NSFW)
    whose composition has no chain cell (no ``chains`` part, no routed pack) -- NSFW is its own
    page; a "-" /10 column would read as measured-and-empty. Display only: the headline still
    computes every section (_scale_sections), and an out-of-total section never enters Overall."""
    from . import routing as _routing
    cells = {str(k).split("/", 1)[0] for k in ((prof or {}).get("chains") or {})}
    try:
        cells |= {str(k).split("/", 1)[0] for k in _routing.pack_parts(prof)}
    except ConfigError:
        pass
    return [d for d in board_defs(prof) if d.get("in_total", True) or d["key"] in cells]


def _scale_sections(prof: dict) -> list[dict]:
    """The board scores a routed headline shows from the section scales: those with parts."""
    return [d for d in board_defs(prof) if d.get("parts")]


#: section scale points (0-100) per board point: a section is out of 10, as every 4.x category
SCALE45_PER_POINT = 10.0


def pack_se45(shares: list[float]) -> float | None:
    """A pack's SE as a share (G2, accepted by the lead 2026-11-14): the cluster SE over its
    units (one unit = one question, its repeats pooled): sd / sqrt(n). Fewer than 2 units: None."""
    import math as _m
    n = len(shares)
    if n < 2:
        return None
    mu = sum(shares) / n
    return _m.sqrt(sum((x - mu) ** 2 for x in shares) / (n * (n - 1)))


def _pack_reason(state: str, level: int, seq: list[dict], shares: dict, gate, opened,
                 unlock: float, pg: dict | None = None) -> str:
    """Why a routed pack is where it is: locked (the part, or the packs of a lower level, below
    the threshold), not run, errored. A first-level pack the run's own pack gate kept closed
    (``pack_gate``, K's _routed_pack_gate) says the gate's recorded reason, so board and run agree."""
    if state == "pending":
        return "not run"
    if state == "errored":
        return "errored (retries spent; not 0, no score)"
    if state != "locked":
        return ""
    lower = sorted({s["level"] for s in seq if s["level"] < level})
    if not lower and pg and pg.get("reason") and not pg.get("opened"):
        why = str(pg["reason"])
        if why == "share below the threshold" and pg.get("share") is not None and pg.get("threshold") is not None:
            why += f" ({float(pg['share']) * 100:.0f}% < {float(pg['threshold']) * 100:.0f}%)"
        return why
    if not lower:
        if opened is not None:
            return "the route did not open it"
        if gate is None:
            return "part not complete yet"
        return f"part below the {unlock * 100:.0f}% threshold ({gate * 100:.0f}%)"
    for lv in lower:
        bad = [s["n"] for s in seq if s["level"] == lv
               and (shares.get(s["n"]) is None or shares[s["n"]] < unlock)]
        if bad:
            return (f"needs pack{'s' if len(bad) > 1 else ''} {', '.join(map(str, bad))} "
                    f"at or above the {unlock * 100:.0f}% threshold")
    return f"part below the {unlock * 100:.0f}% threshold"


def bonus45(card: dict | None, prof: dict, units: dict | None = None) -> dict[str, dict]:
    """The bonus packs of a routed row on the 4.x convention (the owner's ruling, 2026-11-14), per
    section the routed board scores: each pack of a shown part, its state from ``ladder.states``
    in the 4.x unlock chain (the part's standard share and every set of every lower level at or
    above ``ladder.unlock_share(prof)``; a routed pack opens on the route), its points = share x
    its points (scored only), its SE = pack_se45 over ``units["<score>/<part>#<n>"]`` x its
    points. Reads the card's ladder detail (points_scorecard); computes no share of its own.
    {section: {"bonus", "se2", "packs", "scored", "parts": {part: [pack, ...]}}}"""
    from . import ladder as _ladder
    unlock = _ladder.unlock_share(prof)
    lad = (card or {}).get("ladder") or {}
    out: dict[str, dict] = {}
    for d in _scale_sections(prof):
        sec = {"bonus": 0.0, "se2": 0.0, "packs": 0, "scored": 0, "parts": {}}
        for p in d.get("parts") or []:
            det = (lad.get(d["key"]) or {}).get(p["key"]) or {}
            seq = [{"n": s["n"], "level": s["level"], "points": float(s.get("points", 1.0))}
                   for s in det.get("sets") or []]
            if not seq:
                continue
            shares = {s["n"]: s.get("share") for s in det["sets"]}
            terminal = {s["n"] for s in det["sets"] if s.get("state") == "errored"}
            gate, opened = det.get("unlock_share"), det.get("opened")
            st = _ladder.states(gate, shares, seq, unlock, terminal, opened=opened)
            packs = []
            for s in seq:
                key = f"{d['key']}/{p['key']}#{s['n']}"
                us = list((units or {}).get(key) or [])
                state = st.get(s["n"], "locked")
                pts = se = None
                if state == "scored":
                    pts = shares[s["n"]] * s["points"]
                    se_sh = pack_se45(us)
                    se = None if se_sh is None else se_sh * s["points"]
                    sec["bonus"] += pts
                    sec["se2"] += (se or 0.0) ** 2
                    sec["scored"] += 1
                packs.append({**s, "state": state, "share": shares[s["n"]] if state == "scored" else None,
                              "pts": pts, "se": se, "margin": None if se is None else 1.645 * se,
                              "n_units": len(us),
                              "reason": _pack_reason(state, s["level"], seq, shares, gate, opened, unlock,
                                                     det.get("pack_gate"))})
            sec["packs"] += len(seq)
            sec["parts"][p["key"]] = packs
        out[d["key"]] = sec
    return out


def maxed45(card: dict | None, prof: dict) -> dict[str, dict]:
    """MAXED on the routed board, the 4.x rule (the site: "90% or more on a part's standard questions
    and on every one of its bonus sets: nothing harder is left, so a new set is due"), at
    ``ladder.unlock_share(prof)``: a shown part with standard points whose share (its rows complete,
    none excluded) and every pack's share are at or above it, the packs scored in the 4.x chain
    (bonus45's states). A pack-only part (max 0) has no standard share and is never MAXED. A section
    is MAXED when every such part is. {section: {"parts": [...], "category": bool}} (card["maxed"])."""
    from . import ladder as _ladder
    unlock = _ladder.unlock_share(prof)
    lad = (card or {}).get("ladder") or {}
    bon = bonus45(card, prof)
    out: dict[str, dict] = {}
    for d in _scale_sections(prof):
        std_parts = [p for p in d.get("parts") or [] if p.get("weight")]
        parts = []
        for p in std_parts:
            det = (lad.get(d["key"]) or {}).get(p["key"]) or {}
            share = det.get("std_share")
            if (share is None or share < unlock or det.get("n_excluded")
                    or not (det.get("chains") or {}).get("complete", True)):
                continue
            packs = (bon.get(d["key"]) or {}).get("parts", {}).get(p["key"]) or []
            if all(x["state"] == "scored" and x["share"] is not None and x["share"] >= unlock for x in packs):
                parts.append(p["key"])
        out[d["key"]] = {"parts": parts, "category": bool(std_parts) and len(parts) == len(std_parts)}
    return out


def bonus45_tag(hl: dict, key: str) -> str:
    """" /12 = 8.6 + 0.8 bonus" after a routed score whose section has packs (the published 4.x
    form "19.0 ±0.2 /28 = 17.6 + 1.2 bonus"); "" when it has none (the cell is as before)."""
    mx = (hl.get("max45") or {}).get(key)
    std = (hl.get("std45") or {}).get(key)
    if mx is None or std is None or mx == (hl.get("std_max45") or {}).get(key):
        return ""
    return f" /{mx:g} = {std:.1f} + {(hl.get('bonus45') or {}).get(key) or 0.0:.1f} bonus"


def max45_label(hl_or_none: dict | None, key: str, std_max: float) -> str:
    """A routed column's max: "/10", or "/12 = 10 standard + 2 packs" when the section has packs."""
    mx = ((hl_or_none or {}).get("max45") or {}).get(key)
    if mx is None or mx == std_max:
        return f"/{std_max:g}"
    return f"/{mx:g} = {std_max:g} standard + {mx - std_max:g} pack{'s' if mx - std_max != 1 else ''}"


def _apply_scale45(hl: dict, sc45: dict | None, prof: dict, card: dict | None = None,
                   units: dict | None = None) -> None:
    """A routed profile's headline (D's routed-board note, item 2): each score with cells reads
    its section scale (scale.py: expected points 0-100 on the reference form) on the 4.x board's
    convention, out of 10 (points / 10) ± the 90% margin; Overall = the SUM of the in-total
    sections (/20 for Coding + Chat, as ``headline.overall: sum`` prints a 4.5 flat run), its
    margin 1.645 x sqrt(sum SE^2) (sections taken as independent). A section with
    ``in_total: false`` (NSFW) never enters it. No scale yet = "-", never the 4.1 number.

    Bonus packs (bonus45, the 4.x convention): a section with packs reads standard + bonus (its
    scored packs' share x points), max = 10 + one per pack; its margin 1.645 x sqrt(se_main^2 +
    sum se_pack^2), Overall the same over its in-total sections (G2). A section without a scored
    pack keeps its scale's own numbers; a profile with no packs is exactly as before."""
    secs = (sc45 or {}).get("sections") or {}
    hl["units"] = "scale45"
    hl["scale45"] = {k: {kk: vv for kk, vv in v.items() if kk != "_run"} for k, v in secs.items()}
    bon = bonus45(card, prof, units) if card else {}
    if card:
        hl["maxed"] = maxed45(card, prof)        # the 4.x MAXED rule; the card's is off when routed
    if any(b["packs"] for b in bon.values()):
        hl["std45"], hl["bonus45"], hl["max45"], hl["std_max45"] = {}, {}, {}, {}
        hl["packs45"] = {k: b["parts"] for k, b in bon.items() if b["parts"]}
    tot, ses, complete = [], [], True
    pack_ses: list[float] = []
    std_tot, bonus_tot, max_tot = 0.0, 0.0, 0.0
    for d in board_defs(prof):
        if not d.get("parts"):
            # a score with no cell in the composition: nothing to show
            hl.setdefault("S", {})[d["key"]] = None
            hl.setdefault("m", {})[d["key"]] = None
    for d in _scale_sections(prof):
        a = secs.get(d["key"]) or {}
        pts = a.get("points")
        s10 = None if pts is None else pts / SCALE45_PER_POINT
        m10 = None if a.get("margin_90") is None else a["margin_90"] / SCALE45_PER_POINT
        b = bon.get(d["key"]) or {}
        if "max45" in hl:
            mx = SCALE45_PER_POINT + sum(s["points"] for ps in (b.get("parts") or {}).values() for s in ps)
            hl["max45"][d["key"]], hl["std_max45"][d["key"]] = mx, SCALE45_PER_POINT
            hl.setdefault("pack_se2_45", {})[d["key"]] = b.get("se2", 0.0)
            if d.get("in_total", True):
                max_tot += mx
                hl["std_max45"]["overall"] = hl["std_max45"].get("overall", 0.0) + SCALE45_PER_POINT
            if s10 is not None:
                hl["std45"][d["key"]] = round(s10, 1)
                hl["bonus45"][d["key"]] = b.get("bonus", 0.0)
                if d.get("in_total", True):
                    std_tot += s10
                    bonus_tot += b.get("bonus", 0.0)
        if s10 is not None and b.get("scored"):
            se_main = None if a.get("se_points") is None else a["se_points"] / SCALE45_PER_POINT
            s10 = s10 + b["bonus"]
            m10 = None if se_main is None else 1.645 * (se_main * se_main + b["se2"]) ** 0.5
            if d.get("in_total", True):
                pack_ses += [p["se"] for ps in b["parts"].values() for p in ps if p.get("se")]
        hl.setdefault("S", {})[d["key"]] = None if s10 is None else round(s10, 1)
        hl.setdefault("m", {})[d["key"]] = m10
        hl.setdefault("interval", {})[d["key"]] = (None if s10 is None or m10 is None
                                                   else (round(s10 - m10, 3), round(s10 + m10, 3)))
        if d.get("in_total", True):
            if s10 is None or a.get("se_points") is None:
                complete = False
            else:
                tot.append(s10)
                ses.append(a["se_points"] / SCALE45_PER_POINT)
    import math as _m
    if complete and tot:
        ov = sum(tot)
        mg = 1.645 * _m.sqrt(sum(x * x for x in ses + pack_ses))
        hl["S"]["overall"], hl["m"]["overall"], hl["sort"] = round(ov, 1), mg, ov
        if "max45" in hl:
            hl["std45"]["overall"], hl["bonus45"]["overall"] = round(std_tot, 1), bonus_tot
    else:
        hl["S"]["overall"], hl["m"]["overall"], hl["sort"] = None, None, None
    if "max45" in hl:
        hl["max45"]["overall"] = max_tot


def _tie45(t: dict | None, ha: dict, hb: dict, key: str) -> dict | None:
    """scale.tie on a section, on the totals the rank sorts on: with bonus packs (bonus45) the gap
    is the section scale's gap / 10 + the bonus difference, its SE sqrt((se_diff / 10)^2 +
    se_pack_a^2 + se_pack_b^2) (G2's SEs; packs independent across models), tied at |gap| <
    1.645 SE, as scale.tie. Neither row with packs in the section: scale.tie unchanged."""
    from . import scale as _scale
    if t is None:
        return None
    if key in (ha.get("pack_se2_45") or {}) or key in (hb.get("pack_se2_45") or {}):
        ba = (ha.get("bonus45") or {}).get(key) or 0.0
        bb = (hb.get("bonus45") or {}).get(key) or 0.0
        pa = (ha.get("pack_se2_45") or {}).get(key, 0.0)
        pb = (hb.get("pack_se2_45") or {}).get(key, 0.0)
    else:
        # R37/R38 (totals45): the rank sorts on standard + bonus packs, so the tie test reads the same totals; the
        # packs' variance is what the total's range adds over the standard range (both 90% halves)
        ga, gb = (ha.get("total45") or {}).get(key), (hb.get("total45") or {}).get(key)
        if not ga and not gb:
            return t
        ba, bb = _total45_bonus(ga), _total45_bonus(gb)
        pa, pb = _total45_pack_se2(ga), _total45_pack_se2(gb)
    gap = t["gap"] / SCALE45_PER_POINT + ba - bb
    se = ((t["se_diff"] / SCALE45_PER_POINT) ** 2 + pa + pb) ** 0.5
    return {**t, "gap_total": gap, "se_total": se,
            "tied": abs(gap) < _scale.Z_TIE * se or abs(gap) < 1e-9}


def _total45_bonus(g: dict | None) -> float:
    return float((g or {}).get("bonus_points") or 0.0)


def _total45_pack_se2(g: dict | None) -> float:
    """A section total's pack variance (points^2): (total half^2 - standard half^2) / 1.645^2, 0 without packs."""
    if not g or _total45_bonus(g) <= 0:
        return 0.0
    th = ((g.get("total_accuracy") or {}).get("half"))
    sh = ((g.get("accuracy") or {}).get("half"))
    if th is None:
        return 0.0
    from .accuracy45 import Z90
    return max(float(th) ** 2 - float(sh or 0.0) ** 2, 0.0) / (Z90 * Z90)


def _rank45(part: list[str], stats: dict, prof: dict) -> dict[str, str]:
    """Ranks on the section scales: "n=" when every in-total section is tied with the model
    directly above (scale.tie at 90%), as the 4.1 board chains adjacent ties. With bonus packs
    the test is on the section totals including bonus (_tie45), what the rank sorts on."""
    from . import scale as _scale
    keys = [d["key"] for d in _scale_sections(prof) if d.get("in_total", True)]
    lab, prev, n = {}, None, 0
    for l in part:
        if (stats[l]["headline"].get("S") or {}).get("overall") is None:
            lab[l] = "-"
            continue
        n += 1
        tied = False
        if prev is not None:
            a = ((stats[prev].get("scale45") or {}).get("sections") or {})
            b = ((stats[l].get("scale45") or {}).get("sections") or {})
            ts = [_tie45(_scale.tie((a.get(k) or {}).get("_run") or {}, (b.get(k) or {}).get("_run") or {}),
                         stats[prev]["headline"], stats[l]["headline"], k)
                  for k in keys]
            tied = bool(ts) and all(t is not None and t["tied"] for t in ts)
        if tied:
            lab[l] = lab[prev].rstrip("=") + "="
        else:
            lab[l] = str(n)
        prev = l
    return lab


#: the last board's simultaneous-separation table (headline._extras45); generate()
#: writes it into report.json as ``separation``
LAST_SEPARATION: dict = {}


def pending_reasons(s: dict) -> list[str]:
    """Why a model is not ranked for lack of data (empty = nothing missing)."""
    out = []
    for m in s.get("missing_jobs") or []:
        out.append(f"{m['case_id']} r{m.get('repeat')}: {m['reason']}")
    for c in s.get("missing_cases") or []:
        if not any(x.startswith(f"{c} ") for x in out):
            out.append(f"{c}: no row")
    if s.get("unjudged"):
        out.append("judge skipped (--no-judge)")
    elif s.get("pending_judge"):
        out.append("rows waiting for the judge")
    for k in (s.get("scorecard") or {}).get("partial") or []:
        out.append(f"{k}: partial")
    return out


from .headline import gen_draws as _gen_draws   # noqa: E402  (the draws live in headline.py: a stamp input)


def _sd(xs):
    xs = [x for x in xs if x is not None]
    if len(xs) < 2:
        return None
    m = sum(xs) / len(xs)
    return (sum((x - m) ** 2 for x in xs) / (len(xs) - 1)) ** 0.5


def _extras45(labels, stats, inputs, cfg, prof, hs, boots) -> None:
    """report.json additions for the 4.5 board (all models, harmless on 4.1):
    ``ranked`` / ``status`` / ``pending_reasons``, ``tie_group``, ``rank_lo`` /
    ``rank_hi`` (rank interval at the profile level), simultaneous pair
    separation of the top ten (``headline.separated_from`` /
    ``not_separated_from``, board-level LAST_SEPARATION), ``bench_min``, row
    date range, and (profile ``headline.variance_components: true``)
    ``variance``."""
    from . import headline as _h
    from . import scoring45 as _s45
    from .profiles import hard_case_ids
    hcfg = (prof.get("headline") or {})
    pend45 = _s45.pending_policy(prof)
    LAST_SEPARATION.clear()
    hard_ids = hard_case_ids(prof)
    for l in labels:
        s = stats[l]
        hl = s.get("headline") or {}
        # ranked == the headline gave it a rank: every in-total score is complete
        # (an in-total partial category or a pending judge leaves Overall empty).
        # Missing rows of a score outside the total (NSFW) do not block it.
        reasons = pending_reasons(s) if hl.get("rank", "-") == "-" else []
        cov = s.get("coverage") or {}
        old = bool((s.get("era") or {}).get("old"))
        ranked = hl.get("rank", "-") != "-" and not reasons
        if pend45 and not old:
            # review F7: a ranked model with a missing job outside the total
            # (NSFW) keeps its rank, and still says what is missing
            reasons = pending_reasons(s)
        if cov.get("failed"):
            status = "failed"
        elif old:
            status = "old"
        elif _provisional(s):
            status = "provisional"
        elif ranked:
            status = "ranked"
        else:
            status = "pending"
        s["ranked"], s["status"], s["pending_reasons"] = ranked, status, reasons
        s["tie_group"] = hl.get("rank") if ranked else None
        rows = inputs[l][0]
        bm = _s45.bench_min(rows, hard_ids)
        wall = run_minutes(s.get("meta") or {})
        bm["wall_min"] = None if wall is None else round(wall, 1)
        s["bench_min"] = {"main": bm["main_min"], "hard": bm["hard_min"], "detail": bm}
        s["date_first"] = (bm["main"] or {}).get("from")
        s["date_last"] = (bm["main"] or {}).get("to")
        if hard_ids:
            s["hard_date_first"] = ((bm["hard"] or {}).get("from"))
            s["hard_date_last"] = ((bm["hard"] or {}).get("to"))
    # ranks and simultaneous separation among the ranked, current models
    ranked_l = [l for l in rank_labels(labels, stats) if stats[l].get("ranked")]
    samples = {l: boots[l]["samples"]["overall"] for l in ranked_l if l in boots}
    point = {l: _h.display(stats[l]["headline"]["P"], prof).get("overall") for l in ranked_l}
    ri = _s45.rank_intervals(samples, ranked_l, hs["level"])
    for l in labels:
        iv = ri.get(l)
        stats[l]["rank_lo"], stats[l]["rank_hi"] = (iv if iv else (None, None))
    top_n = int(hcfg.get("separation_top_n", 10))
    sep = _s45.simultaneous_separation(samples, point, ranked_l, top_n, hs["level"])
    LAST_SEPARATION.update(sep)
    for l in sep["labels"]:
        stats[l]["headline"]["separated_from"] = [
            (p["b"] if p["a"] == l else p["a"]) for p in sep["pairs"]
            if l in (p["a"], p["b"]) and p["separated"]]
        stats[l]["headline"]["not_separated_from"] = [
            (p["b"] if p["a"] == l else p["a"]) for p in sep["pairs"]
            if l in (p["a"], p["b"]) and not p["separated"]]
    byp = {(p["a"], p["b"]): p for p in sep["pairs"]}
    for l in ranked_l:
        va = (stats[l]["headline"].get("vs_above") or {})
        if va and (va.get("a"), l) in byp:
            va["separated"] = byp[(va["a"], l)]["separated"]
    if hcfg.get("variance_components"):
        sc = scoring_config(cfg)
        allowed = _profile_case_ids(cfg, with_tiers=True)
        st = _h.strata(prof, sc, allowed)
        n = min(int(hs["resamples"]), int(hcfg.get("variance_resamples", 200)))
        case_m = _h.draws(st, n, hs["seed"] + 1, per_stratum=_h.per_stratum(prof))
        gen_m = _gen_draws(st, n, hs["seed"], per_stratum=_h.per_stratum(prof))
        for l in labels:
            if not stats[l].get("ranked"):
                continue
            rows, meta, era = inputs[l]
            by_t = tier_rows(prof, rows, era)
            cb = _h.boot_model(by_t, l, cfg, meta, era, prof, sc, st, case_m, two_level=False)
            has_rep = any(len({r.get("repeat") for r in rr}) > 1
                          for rr in _group_by_case(rows).values())
            gb = (_h.boot_model(by_t, l, cfg, meta, era, prof, sc, st, gen_m, two_level=True)
                  if has_rep else None)
            judged = [r for r in rows if r.get("needs_judge")]
            stats[l]["variance"] = _s45.variance_components(
                _sd(cb["samples"]["overall"]), _sd(gb["samples"]["overall"]) if gb else None,
                has_rep, _s45.judge_sample_spread(judged), hcfg.get("generation_sd_prior"))


def _group_by_case(rows):
    out: dict = {}
    for r in rows:
        out.setdefault(r.get("case_id"), []).append(r)
    return out


def compare(a: str, b: str, cfg: dict | None) -> dict:
    """Head-to-head (``crucibleforge compare A B``): both models' headline
    per category, sub-score and overall, and the paired bootstrap
    difference A - B (same resampled cases and repeats for both) with its
    verdict."""
    from . import headline as _h
    prof = _tier_profile(cfg)
    if not prof:
        raise ValueError("compare needs the tiered benchmark profile (suite 3.9)")
    labels, stats, _ = board_stats(f"{a},{b}", cfg)
    missing = [l for l in (a, b) if l not in stats]
    if missing:
        raise ValueError(f"no board rows for {', '.join(missing)}")
    inputs = {}
    for l in (a, b):
        rows, era = board_view(l, cfg)
        inputs[l] = (rows, stats[l].get("meta") or {}, era)
    boots = model_boots([a, b], inputs, cfg, prof)
    hs = headline_settings(prof)
    areas = {}
    for key in ("overall",) + _h.keys(prof):
        ha, hb = stats[a]["headline"], stats[b]["headline"]
        va, vb = _h.display(ha["P"], prof).get(key), _h.display(hb["P"], prof).get(key)
        d = _h.paired(boots[a], boots[b], key, hs["level"])
        verdict = None
        if d is not None and va is not None and vb is not None:
            verdict = "tie" if d["tied"] else (a if va > vb else b)
        shown = "pts" if "/" in key else "S"
        areas[key] = {"a": (ha[shown].get(key), ha["m"].get(key)),
                      "b": (hb[shown].get(key), hb["m"].get(key)),
                      "diff": None if va is None or vb is None else va - vb,
                      "lo": None if d is None else d["lo"], "hi": None if d is None else d["hi"],
                      "verdict": verdict}
    old = {l: bool((stats[l].get("era") or {}).get("old")) for l in (a, b)}
    return {"a": a, "b": b, "areas": areas, "old": old, "level": hs["level"],
            "resamples": hs["resamples"]}


def render_compare(res: dict) -> str:
    from . import headline as _h
    a, b = res["a"], res["b"]
    L = [f"{a}  vs  {b}   (paired case bootstrap, {res['resamples']} resamples, "
         f"{res['level'] * 100:.0f}% interval)"]
    if any(res["old"].values()):
        L.append("note: " + ", ".join(l for l, o in res["old"].items() if o)
                 + " shows OLD values (an earlier suite) — not comparable with current rows")
    pct = f"{res['level'] * 100:.0f}% interval"
    L.append(f"{'':22} {a[:22]:>22} {b[:22]:>22} {'diff':>8}  {pct:>16}  verdict")
    for key, e in res["areas"].items():
        sa = _h.fmt_s(*e["a"]).replace("**", "")
        sb = _h.fmt_s(*e["b"]).replace("**", "")
        diff = "-" if e["diff"] is None else f"{e['diff']:+.2f}"
        rng = "-" if e["lo"] is None else f"{e['lo']:+.2f} to {e['hi']:+.2f}"
        v = {"tie": "≈ tied", None: "-"}.get(e["verdict"], f"{e['verdict']} better")
        L.append(f"{key[:22]:22} {sa:>22} {sb:>22} {diff:>8}  {rng:>16}  {v}")
    return "\n".join(L)


def _job_key(r: dict) -> tuple:
    """One benchmark job = one (case, repeat): a single-turn row, or every
    turn row of a multi-turn session (they share the key)."""
    return (r.get("case_id"), r.get("repeat"))


def _jobs(rows: list[dict]) -> dict[tuple, list[dict]]:
    """Non-perf, non-skipped rows grouped per job (review S6: a session is
    one unit, never ignored and never counted once per turn)."""
    out: dict[tuple, list[dict]] = {}
    for r in rows:
        if r.get("category") == "perf" or r.get("skipped") or r.get("grade") == "skipped":
            continue
        out.setdefault(_job_key(r), []).append(r)
    return out


def _counted_failed(r: dict, why: str, case: dict | None) -> dict:
    """A copy of row ``r`` scored as FAILED everywhere (review B3/B4): grade
    fail, a code-bank score 0, every element and card check failed and — for
    a judged case — a verdict that reads as an empty generation (0 on every
    judged formula: gated, willing, steer, story, rpdeep, generic), so the
    row is never dropped from a judged sub-score and renormalised away.
    ``why`` ("errored" / "ran_out") is kept on the row and its verdict."""
    r = {**r, why: True}
    if r.get("grade") is not None or not (r.get("needs_judge") or (case or {}).get("rubric")
                                          or (case or {}).get("turns")):
        r["grade"] = "fail"
    if r.get("category") in BANK_CATS or r.get("score") is not None:
        r["score"] = 0.0
    el = r.get("elements")
    if isinstance(el, dict) and el.get("results"):
        r["elements"] = {**el, "rate": 0.0,
                         "results": [{**x, "pass": False} for x in el["results"]]}
    chk = r.get("checks")
    if isinstance(chk, dict) and chk.get("results"):
        r["checks"] = {**chk, "rate": 0.0,
                       "results": [{**{k: v for k, v in x.items() if k not in ("credit", "near_miss")},
                                    "pass": False, "credit": 0.0} for x in chk["results"]],
                       "groups": {g: [0, t] for g, (_, t) in (chk.get("groups") or {}).items()}}
    final = r.get("needs_judge") or (r.get("turn") is None and (
        (case or {}).get("rubric") or (case or {}).get("turns")))
    if final:
        r["needs_judge"] = True
        r["rubric"] = (r.get("rubric") or (case or {}).get("rubric")
                       or ("rp_multi" if (case or {}).get("turns") else None))
        r["judge"] = {"judge_failed": False, "empty_generation": True, "refused": False,
                      "scores": {}, "elements": {"results": []}, "counted_failed": why}
    return r


def _errored_as_failed(by_t: dict, ran_out: bool = False,
                       errors: bool = True) -> tuple[dict, dict]:
    """Every errored row (and, with ``ran_out``, every job that ran out of
    its budget with no successful escalated re-run on the board — the newest
    run of a job is what the board holds, so a re-run that finished replaces
    the run-out row) as FAILED. Returns (rows by tier, {"n": errored jobs,
    "total": jobs, "ran_out": ran-out jobs}) — jobs, so sessions count once."""
    cases = _cases_by_id()
    n = total = n_out = 0
    out = {}
    for t, rows in by_t.items():
        jobs = _jobs(rows)
        total += len(jobs)
        errored = {k for k, rs in jobs.items() if any(r.get("grade") == "error" for r in rs)}
        out_jobs = ({k for k, rs in jobs.items() if k not in errored
                     and any(r.get("finish_reason") == "length" for r in rs)}
                    if ran_out else set())
        n += len(errored)
        n_out += len(out_jobs)
        new = []
        for r in rows:
            k = _job_key(r)
            if r.get("grade") == "error":
                if errors:
                    r = _counted_failed(r, "errored", cases.get(r.get("case_id")))
            elif k in out_jobs and r.get("category") != "perf":
                r = _counted_failed(r, "ran_out", cases.get(r.get("case_id")))
            new.append(r)
        out[t] = new
    return out, {"n": n, "total": total, "ran_out": n_out}


def _drop_billing_jobs(prof: dict, by_t: dict) -> tuple[dict, set, int]:
    """(rows without the billing-refused jobs, the ladder sets they touch as
    "<score>/<part>#<n>", jobs dropped).  ``by_t`` is returned unchanged
    (same object) when no row is a billing error."""
    from .preflight import billing_error_row
    if not any(billing_error_row(r) for rows in by_t.values() for r in rows):
        return by_t, set(), 0
    lad = _ladder_levels(prof) if points_mode(prof) else {}
    blocked: set[str] = set()
    n = 0
    out = {}
    for t, rows in by_t.items():
        bad = {k for k, rs in _jobs(rows).items() if any(billing_error_row(r) for r in rs)}
        n += len(bad)
        if t == 2:
            for k in bad:
                key, num = lad.get(k[0], (None, None))
                if key:
                    blocked.add(f"{key}#{num}")
        out[t] = [r for r in rows if _job_key(r) not in bad] if bad else rows
    return out, blocked, n


def _retryable_error(r: dict) -> bool:
    """An errored row the next run retries (bench_all's rule): a transport
    failure, a timeout, a provider refusal like 402 out-of-balance — not a
    template the server can never render, not a prompt over the context."""
    if not (r.get("grade") == "error" or r.get("errored")):
        return False
    from .bench_all import _permanent_error
    return not _permanent_error(r)


def set_pending_jobs(rows: list[dict], spec: dict | None,
                     attempts: dict[str, int] | None = None) -> dict:
    """Why a ladder set's rows do not make a score yet (harness v2, review
    B2): ``errored`` = jobs whose newest row is a retryable provider error,
    ``missing`` = the set's questions with no row at all (a run cut short).
    Either keeps the set PENDING (not a scored 0) until a run fills it.

    A job whose case already errored ``bench_all.MAX_CASE_ATTEMPTS`` times
    (``attempts``: errored rows per case id) will never be retried, so it is
    ``exhausted``: infrastructure, not model quality — the set gets the
    terminal "errored" state (no score, blocks advancement and MAXED, not
    pending forever, cleared by a later explicit re-run)."""
    from .bench_all import MAX_CASE_ATTEMPTS
    attempts = attempts or {}
    jobs = _jobs(rows)
    bad = {k for k, rs in jobs.items() if any(_retryable_error(r) for r in rs)}
    errored = sum(1 for k in bad if attempts.get(k[0], 0) < MAX_CASE_ATTEMPTS)
    exhausted = len(bad) - errored
    have = {r.get("case_id") for r in rows}
    # a bootstrap resample drops cases by design: coverage is the point
    # estimate's business, never a resample's
    missing = [] if _BOOT.get() is not None else sorted(
        c for c in (spec or {}).get("cases") or [] if c not in have)
    return {"errored": errored, "missing": missing, "exhausted": exhausted}


def _judges45(st: dict, chain_rows: list[dict], cfg: dict | None) -> None:
    """4.5 PUBOUT: the judged parts are chain rows, which the category stats never see, so a 4.5 row
    named no judge. Coverage ``judges`` gains the chain rows' judge model, and ``judge_settings``
    says how it judged them (the policy each verdict recorded: thinking, samples per reply)."""
    cov = st.setdefault("coverage", {})
    jrows = [r for r in chain_rows if r.get("judge_model") and (r.get("rubric") or "") != "reference"]
    if not jrows:
        return
    judges = sorted(set(cov.get("judges") or []) | {r["judge_model"] for r in jrows})
    cov["judges"] = judges
    primary = _primary_judge(cfg)
    mismatch = bool(judges and primary and judges != [primary])
    if mismatch and not cov.get("judge_mismatch"):
        note = "judged by " + ", ".join(short_model(j) for j in judges)
        cov["notes"] = list(cov.get("notes") or []) + [note]
        cov["status"] = "; ".join(cov["notes"])
        cov["tier"] = max(int(cov.get("tier") or 0), 1)
    cov["judge_mismatch"] = mismatch
    pol = [((r.get("judge") or {}).get("policy") or {}) for r in jrows]
    think = sorted({"on" if p.get("thinking") else "off" for p in pol if "thinking" in p})
    samples = sorted({int(p["samples"]) for p in pol if isinstance(p.get("samples"), int)})
    st["judge_settings"] = {
        "models": [short_model(j) for j in judges],
        "thinking": think[0] if len(think) == 1 else ("mixed" if think else None),
        "samples": samples or None,
        "replies": len(jrows),
    }


def page_case_ids45(prof: dict | None, ids) -> tuple[set, set]:
    """R3 (fix round 2026-11-18): (standard page ids, adult page ids) of a 4.5 composition's case ids. A case or
    chain unit belongs to the adult page when the score that holds it is not in the total (``in_total: false``):
    a chain unit by its part, a case by its part's declared cases or by a category only such scores use."""
    from . import tiers as _tiers
    want = set(ids or ())
    defs = _tiers.scores(prof)
    out_keys = {d["key"] for d in defs if not d.get("in_total", True)}
    if not out_keys:
        return want, set()
    adult: set = set()
    try:
        from . import chains as _chains
        for c in _chains.chain_cases(prof) if (prof or {}).get("chains") else []:
            if str((c.get("chain_spec") or {}).get("part") or "").split("/")[0] in out_keys:
                adult.add(c["id"])
    except Exception:  # noqa: BLE001 -- display only: an unreadable chain spec leaves its ids on the standard page
        pass
    in_cats, out_cats = set(), set()
    for d in defs:
        for p in d.get("parts") or []:
            if p.get("cases"):
                (adult.update(p["cases"]) if d["key"] in out_keys else None)
            (out_cats if d["key"] in out_keys else in_cats).update(p.get("scoring") or [])
    only_out = out_cats - in_cats
    for cat, cids in ((prof or {}).get("cases") or {}).items():
        if cat in only_out:
            adult.update(cids or [])
    adult &= want
    return want - adult, adult


def _coverage45(st: dict, ids, have: set, missing_jobs: list[dict]) -> None:
    """4.5 PUBOUT: coverage counted against the composition's OWN case ids (chain units included).
    The category stats count only the non-chain rows, so every 4.5 row printed "partial (7/97
    cases)". A case counts as covered when it has a scored row or its chain ended, and is not a
    missing job (infrastructure-failed / unjudged: pending, never a score). Display only."""
    cov = st.setdefault("coverage", {})
    want = set(ids or ())
    if not want:
        return
    gone = {m.get("case_id") for m in missing_jobs or []}
    done = len(want & (set(have) - gone))
    cov["cases"] = done
    cov["expected_cases"] = len(want)
    cov["complete"] = done >= len(want)
    notes = [n for n in (cov.get("notes") or []) if not re.match(r"^partial \(\d+/\d+ cases\)$", str(n))]
    if not cov["complete"]:
        notes.insert(0, f"partial ({done}/{len(want)} cases)")
        cov["partial_note"] = (f"{len(want) - done} of the {len(want)} test items in this suite have no scored "
                               "answer for this model yet (not run, or waiting for a re-run or the judge); "
                               "its scores count only what was answered.")
    else:
        cov.pop("partial_note", None)
    cov["notes"] = notes
    if notes:
        cov["status"] = "; ".join(notes)
    else:
        cov["status"] = f"full ({done} cases)"
    if not cov.get("failed") and int(cov.get("tier") or 0) == 1 and cov["complete"] and not (
            cov.get("stale") or cov.get("old") or cov.get("judge_mismatch") or cov.get("pending")):
        cov["tier"] = 0


def stats_from_tier_rows(label: str, cfg: dict | None, prof: dict,
                         by_t: dict[int, list[dict]], meta: dict | None,
                         era: dict | None) -> dict:
    """model_stats over the tier-1 rows plus ``tier_stats``, ``tier_states``
    and ``subset_stats`` (stats over the tier-1 rows of each score/part that
    declares ``cases``: "<score>" or "<score>/<part>"). Shared by the board
    and the headline bootstrap."""
    from . import tiers as _tiers
    # adult_guard (lead 2026-11-15): a model whose adult page is off (the Claude Code route) never scores
    # there; its stray adult rows are dropped, so the page reads "not run" and is never ranked
    from .adult_guard import board_rows as _adult_board_rows
    by_t = _adult_board_rows(label, cfg, meta, by_t)
    # 4.5 chains (chains.py): chain rows are scored by their own rule (section_stats), never
    # mixed into a category's stats; a chain section is complete when every chain ended
    chain_rows = [r for t in by_t for r in by_t[t] if r.get("chain")]
    all_rows = [r for t in by_t for r in by_t[t]]
    if chain_rows or (prof or {}).get("chains"):
        by_t = {t: [r for r in v if not r.get("chain")] for t, v in by_t.items()}
    errored = {"n": 0, "total": 0}
    # 4.1 F1: a job the hosted provider refused to bill (credit spent, spend
    # limit) is neither scored nor counted failed: it leaves the rows, its
    # pack reads "pending" and its category partial (points_scorecard)
    by_t, blocked_sets, n_billing = _drop_billing_jobs(prof, by_t)
    # B4 (review 2026-10-29): on the points board a job that ran out of its
    # budget with no successful escalated re-run counts as FAILED (the
    # footnote's rule); old-suite rows keep the rules they were scored under
    ran_out = points_mode(prof) and not (era or {}).get("old")
    from . import scoring45 as _s45
    missing_jobs: list[dict] = []
    forfeits: list[dict] = []
    if _s45.pending_policy(prof) and points_mode(prof) and not (era or {}).get("old"):
        # 4.5 (PLAN-45-v2 5.1): an infrastructure-failed / empty / ctx_short /
        # unjudged / judge-failed job is MISSING, never 0: it leaves the scored
        # rows and the model is pending (unranked) until it is re-run. A job that
        # ran out of its budget stays the model's own failure (ran_out below).
        # 4.5.1 forfeits (forfeit45.py): a job board_view turned into a failed answer stays scored, never MISSING
        from . import forfeit45 as _ff45
        by_t, missing_jobs = _ff45.split_missing(by_t, _refusal_case_ids(prof))
        forfeits = _ff45.applied(label, by_t)
        errored = {"n": 0, "total": 0}
        by_t, info = _errored_as_failed(by_t, ran_out=ran_out, errors=False)
        ran_out_jobs = info["ran_out"]
    elif (prof.get("errored_rows") or "exclude") == "fail" or ran_out:
        # EVAL-FABLE M4: an errored row (the request failed) is never dropped
        # and renormalised away: it counts as failed until a resume re-runs it
        # — in the judged sub-scores too (review B3)
        fail_errors = (prof.get("errored_rows") or "exclude") == "fail"
        by_t, info = _errored_as_failed(by_t, ran_out=ran_out, errors=fail_errors)
        if fail_errors:
            errored = {"n": info["n"], "total": info["total"]}
        ran_out_jobs = info["ran_out"]
    else:
        ran_out_jobs = 0
    st = model_stats(label, cfg, rows=by_t[1], meta=meta, era=era)
    st["errored_rows"] = errored
    # fix round 2026-11-18 (S6 mark wired into the board): jobs whose finished run was replaced by a complete
    # re-draw; the run ids go to report.json, the count to the row's notes
    rr45 = reruns45(all_rows)
    if rr45:            # only when a job was re-run: 4.1 board data stays byte-identical
        st["reruns45"] = rr45
    at45 = _attempts45.summary(all_rows)
    if at45:            # first-reply rule: later attempts left out ("N later attempts ignored"); report.json only
        st["attempts45"] = at45
    st["ran_out_failed"] = ran_out_jobs
    st["missing_jobs"] = missing_jobs
    if forfeits:        # only when a forfeit is on record: other rows' report data stays byte-identical
        st["forfeits"] = forfeits
    from . import assistant45 as _a45
    if _a45.block(prof) and _BOOT.get() is None and not (era or {}).get("old"):
        # Assistant basics (checked): a column outside Overall, read from its own rows only (assistant45.py)
        st["assistant45"] = _a45.compute(prof, by_t.get(1) or [], missing_jobs)
    if n_billing:
        st["packs_blocked"] = sorted(blocked_sets)
        st["billing_blocked_jobs"] = n_billing
    # S3: the standard cases with no row (the old-values view is a previous
    # suite's set: not compared against today's profile)
    # (a bootstrap resample drops cases by design: coverage is the point
    # estimate's business, never a resample's)
    ids = (_profile_case_ids(cfg) if not (era or {}).get("old") and _BOOT.get() is None
           else None)
    have = {r.get("case_id") for r in by_t[1]}
    hard_ids: set[str] = set()
    if ids is not None and points_mode(prof):
        from .profiles import hard_case_ids
        hard_ids = hard_case_ids(prof)
        have |= {r.get("case_id") for r in by_t.get(3) or []}
    miss = set(ids or ()) | hard_ids
    if (prof or {}).get("chains"):
        from . import chains as _chains
        st["chain_stats"] = _chains.section_stats(chain_rows, prof, (_BOOT.get() or {}).get("mult"))
        # R1 (fix round 2026-11-18): units per generated part whose reply was empty (not cut): counted in the notes
        st["empty45"] = empty_units45(chain_rows)
        # R7: how the row was run (thinking, effort, median reasoning tokens) over its counted answers
        st["disclosure45"] = disclosure45(list(by_t.get(1) or []) + chain_rows)
        st["disclosure45"]["reasoning_by_section"] = reasoning_by_section45(list(by_t.get(1) or []) + chain_rows,
                                                                            prof)
        # R6: the minutes are the timing record's only when every counted row (generated items included) came from
        # the run that record describes
        st["run_minutes45"] = timing_minutes45(meta or {}, all_rows)
        st["run_minutes_packs45"] = timing_pack_minutes45(meta or {}) if st["run_minutes45"] is not None else None
        # one `all` = one run: its ladder-pack rounds (own bench_run_id, inside its window) are not a second run
        st["run_ids45"] = len({r.get("bench_run_id") for r in all_rows}) if st["run_minutes45"] is None else 1
        if (prof or {}).get("routing"):
            from . import routing as _routing
            st["route"] = _routing.route_of(chain_rows)    # 4.5 bank: opens the routed packs
            if _BOOT.get() is None and not (era or {}).get("old"):
                # the section scales on the routing's calibration (D's routed-board note, item 2)
                st["scale45"] = scale45(prof, all_rows, meta)
        if _BOOT.get() is None and not (era or {}).get("old"):
            # item 5: the generated items are the 4.5 test; their per-row cost_usd is the run cost
            _add_chain_cost(st, label, chain_rows, cfg, meta, prof)
            # a folder that holds only the adult page has no standard row: its Run cost is not "$0.00"
            sep_ = {d_["key"] for d_ in _tiers.scores(prof) if not d_.get("in_total", True)}
            sides_ = _case_sides(cfg)
            n_main = (sum(1 for r in chain_rows
                          if str((r.get("chain") or {}).get("part") or "").split("/")[0] not in sep_)
                      + sum(1 for r in by_t.get(1) or [] if sides_.get(r.get("case_id"), "main") != "separate"))
            if not n_main:
                st["no_standard_rows45"] = True
        have |= _chains.completed(prof, chain_rows)   # a chain id is done when its chain ended
    if _BOOT.get() is None and not (era or {}).get("old"):
        miss = (miss - have) | {m["case_id"] for m in missing_jobs}
    else:
        miss = set()
    # 4.5: a case part the model's route cannot run (route_cannot_run) is "not run", not missing
    route_off: dict[str, tuple[set, str]] = {}
    if (prof or {}).get("chains"):
        for d_ in _tiers.scores(prof):
            for p_ in d_.get("parts") or []:
                why = route_cannot_run(meta, p_) if p_.get("cases") else None
                if why:
                    route_off[f"{d_['key']}/{p_['key']}"] = (set(p_["cases"]), why)
        for ids_, _why in route_off.values():
            miss -= ids_
    st["missing_cases"] = sorted(miss)
    if (prof or {}).get("chains") and _BOOT.get() is None:
        _judges45(st, chain_rows, cfg)
        if ids is not None and not (era or {}).get("old"):
            # R3: count the page being rendered only -- the standard page when the model has any standard row,
            # else (an adult-only render) the adult page
            std_ids, adult_ids = page_case_ids45(prof, ids)
            page = std_ids if (have & std_ids or not adult_ids) else adult_ids
            _coverage45(st, page, have, missing_jobs)
    st["tier_stats"], st["tier_states"] = {}, {}
    for t in _tiers.TIERS:
        if by_t.get(t) and not points_mode(prof):
            sub = model_stats(label, cfg, rows=by_t[t], meta=meta, era=era)
            st["tier_stats"][t] = {k: v for k, v in sub.items()
                                   if k not in ("coverage", "meta", "speed")}
            st["tier_stats"][t]["speed"] = {}
    st["tier_states"] = _tiers.row_states(prof, by_t)
    subsets = {}
    # review F15: tier 3 is the hard set only on the points board; on a legacy
    # T1/T2/T3 profile it is a tier of its own and stays out of the subsets
    sub_rows = list(by_t[1]) + (list(by_t.get(3) or []) if points_mode(prof) else [])
    part_defs = {f"{d['key']}/{p_['key']}": p_ for d in _tiers.scores(prof) for p_ in d.get("parts") or []}
    for key, ids in declared_case_sets(prof, cfg).items():
        prow = [r for r in sub_rows if r.get("case_id") in ids]
        # 4.5: a cut code-bank reply follows the part's ran-out rule (case_part_ran_out)
        if key in route_off:
            prow, ro = [], {"rule": "route", "calls": 0, "ran_out": 0, "share": None, "not_measured": True,
                            "not_run": route_off[key][1]}
        else:
            from .chains import SHORT_KEYED_PARTS as _SHORT, cut_at_full_budget as _caf
            prow, ro = case_part_rows(prow, case_part_ran_out(prof, part_defs[key]) if key in part_defs
                                      else None,
                                      _caf(prof) if key in _SHORT else "omit",
                                      int((prof or {}).get("bank_budget") or 0) or None)
        sub = model_stats(label, cfg, rows=prow, meta=meta, era=era)
        fin_src = dnf_src = None
        if ro is not None and ro.get("did_not_finish"):
            dn_ = {tuple(u) for u in ro["did_not_finish"]["units"]}
            fin_rows = [r for r in prow if (r.get("case_id"), r.get("repeat")) not in dn_]
            if fin_rows:
                fin_src = {k: v for k, v in model_stats(label, cfg, rows=fin_rows, meta=meta, era=era).items()
                           if k not in ("coverage", "meta")}
            dnf_rows = [r for r in prow if (r.get("case_id"), r.get("repeat")) in dn_]
            # dnf_text: "score 0" only when every did-not-finish reply earned 0 (else "scored on what they delivered")
            dnf_src = {k: v for k, v in model_stats(
                label, cfg, rows=dnf_rows, meta=meta, era=era).items() if k not in ("coverage", "meta")}
        rule = part_unit_score(part_defs[key]) if key in part_defs else None
        if rule in ("half_pass", "half_task"):
            tu = sub.get("tooluse") or {}
            if tu.get("n") and tu.get("element_rate") is not None:
                # half pass + half step share, run by run: both are means over the same runs.
                # half_task (the tool revision, lead 2026-11-15 08:27): the pass half is "the task was
                # done right" (tooltask.task_done: an extra read-only lookup does not dock it)
                done = tu["rate"]
                if rule == "half_task":
                    tr = task_rate([r for r in prow if r.get("category") == "tooluse"], _cases_by_id())
                    if tr is not None:
                        done = tr
                sub["tooluse"] = {**tu, "step_rate": tu["element_rate"], "task_rate": done,
                                  "element_rate": 0.5 * done + 0.5 * tu["element_rate"]}
        subsets[key] = {k: v for k, v in sub.items() if k not in ("coverage", "meta")}
        if ro is not None:
            subsets[key]["ran_out_case"] = ro
            subsets[key]["not_measured"] = ro["not_measured"]
        if fin_src is not None:
            subsets[key]["finished_src"] = fin_src
        if dnf_src is not None:
            subsets[key]["dnf_src"] = dnf_src
    st["subset_stats"] = subsets
    if points_mode(prof):
        lad = _ladder_levels(prof)
        by_set: dict[str, list[dict]] = {}
        for r in by_t.get(2) or []:
            key, n = lad.get(r.get("case_id"), (None, None))
            if key:
                by_set.setdefault(f"{key}#{n}", []).append(r)
        # one ran-out rule for Main and packs (lead 2026-11-15, PACKS2): a pack set follows its own
        # ``ran_out``, else its part's (case_part_ran_out), else, on a 4.5 profile (one with chains),
        # ``exclude`` (the owner's ruling: a cut reply is not counted; every 4.5 chain set and adult set
        # declares it). A cut reply is left out, and above scale.RAN_OUT_NOT_MEASURED of the set's
        # calls cut the set is "not measured" (no score). A 4.1 profile has no rule: unchanged.
        specs = {f"{key}#{x['n']}": x for key, seq in _ladder_sets(prof).items() for x in seq}
        scored_rows: dict[str, list[dict]] = {}
        set_ro: dict[str, dict] = {}
        for k, v in by_set.items():
            pk_ = k.split("#")[0]
            rule = case_part_ran_out(prof, {"key": k, "ran_out": (specs.get(k) or {}).get("ran_out")})
            if rule is None and pk_ in part_defs:
                rule = case_part_ran_out(prof, part_defs[pk_])
            if rule is None and (prof or {}).get("chains"):
                rule = "exclude"
            scored_rows[k], ro_ = case_part_rows(v, rule)
            if ro_ is not None:
                set_ro[k] = ro_
        st["ladder_stats"] = {
            k: {kk: vv for kk, vv in model_stats(label, cfg, rows=scored_rows[k], meta=meta, era=era).items()
                if kk not in ("coverage", "meta")}
            for k in by_set}
        for k, ro_ in set_ro.items():
            st["ladder_stats"][k]["ran_out_case"] = ro_
            st["ladder_stats"][k]["not_measured"] = ro_["not_measured"]
        # 4.5 generated sets: one stats block per rung ("<key>#<n>@r<rung>")
        by_rung: dict[str, list[dict]] = {}
        cby = _cases_by_id()
        for k, v in scored_rows.items():
            for r in v:
                rg = (cby.get(r.get("case_id")) or {}).get("rung")
                if rg is not None:
                    by_rung.setdefault(f"{k}@r{int(rg)}", []).append(r)
        for k, v in by_rung.items():
            st["ladder_stats"][k] = {
                kk: vv for kk, vv in model_stats(label, cfg, rows=v, meta=meta, era=era).items()
                if kk not in ("coverage", "meta")}
        attempts = None
        for k, v in by_set.items():
            if attempts is None and any(_retryable_error(r) for r in v):
                from .bench_all import error_attempts
                attempts = error_attempts(label)
            from . import scoring45 as _s45
            if not _s45.pending_policy(prof):
                continue        # 4.1 board: a provider error row stays a failed job
            st["ladder_stats"][k]["pending_jobs"] = set_pending_jobs(
                v, None if (specs.get(k) or {}).get("rungs") else specs.get(k), attempts)
        st["tier_stats"] = {}
    # lead 2026-11-19 (b): which model and which runs these rows are (judge agreement counts per model and per run
    # read); scorecard inputs only, left out of report.json (_CARD_INPUTS)
    st["label"] = label
    st["run_ids"] = sorted({str(r["bench_run_id"]) for r in all_rows if r.get("bench_run_id")})
    return st


def calibration(cfg: dict | None, reference: str | None = None) -> dict:
    """`crucibleforge calibrate`: per sub-score, the reference model's share
    on the standard level (profile ``calibration.reference``, default
    minimax-m3) flagged "too easy" at >= ``calibration.too_easy`` (maintainer: "if
    minimax aces any section we need to rethink the difficulty and get more
    examples"), and every sub-score some model has MAXED (>= the unlock
    share on everything that exists: "needs a new pack")."""
    from . import tiers as _tiers
    prof = _tier_profile(cfg)
    if not points_mode(prof):
        raise ValueError("calibrate needs a profile with scores: and sub-scores")
    cal = prof.get("calibration") or {}
    ref = reference or cal.get("reference") or "minimax-m3"
    too_easy = float(cal.get("too_easy", 0.9))
    labels, stats, _ = board_stats(None, cfg)
    rows, maxed = [], {}
    for l in labels:
        card = stats[l]["scorecard"]
        for cat, parts in (card.get("ladder") or {}).items():
            for pk, det in parts.items():
                if det.get("maxed"):
                    maxed.setdefault(f"{cat}/{pk}", []).append(l)
    ref_card = (stats.get(ref) or {}).get("scorecard") or {}
    for d in _tiers.scores(prof):
        for p in d.get("parts") or []:
            key = f"{d['key']}/{p['key']}"
            det = ((ref_card.get("ladder") or {}).get(d["key"]) or {}).get(p["key"]) or {}
            share = det.get("std_share")
            rows.append({"key": key, "label": f"{d['label']} / {p['label']}",
                         "max": p["weight"], "reference_share": share,
                         "too_easy": share is not None and share >= too_easy,
                         "pending": share is None,
                         "maxed_by": maxed.get(key, [])})
    return {"reference": ref, "reference_on_board": ref in stats, "too_easy": too_easy,
            "rows": rows}


def maxed_from_report(path=None) -> dict[str, list[str]] | None:
    """{"<score>/<part>": [labels]} of the parts some model maxed, read from the last written
    results/report.json (the same per-model scorecards `calibration` rebuilds, but without
    recomputing every model: seconds instead of minutes). None when there is no readable report."""
    from pathlib import Path as _P
    p = path or report_json_path()
    try:
        data = json.loads(_P(p).read_text(encoding="utf-8"))
        models = data["models"]
    except (OSError, ValueError, KeyError, TypeError):
        return None
    if not isinstance(models, dict):
        return None
    out: dict[str, list[str]] = {}
    for label, st in models.items():
        card = (st or {}).get("scorecard") or {}
        for cat, parts in (card.get("ladder") or {}).items():
            for pk, det in (parts or {}).items():
                if (det or {}).get("maxed"):
                    out.setdefault(f"{cat}/{pk}", []).append(label)
    return out


def render_calibration(res: dict) -> str:
    L = [f"Calibration — reference model {res['reference']}"
         + ("" if res["reference_on_board"] else " (NOT on the board: no reference numbers)"),
         f"{'sub-score':34} {'max':>4} {'ref share':>9}  verdict"]
    for r in res["rows"]:
        sh = "-" if r["reference_share"] is None else f"{r['reference_share'] * 100:.0f}%"
        v = []
        if r["too_easy"]:
            v.append(f"TOO EASY (≥ {res['too_easy'] * 100:.0f}%): rethink difficulty / add examples")
        if r["maxed_by"]:
            v.append("MAXED by " + ", ".join(r["maxed_by"]) + ": needs a new pack")
        if r["pending"] and not v:
            v.append("no reference result")
        L.append(f"{r['label'][:34]:34} {r['max']:>4g} {sh:>9}  {'; '.join(v) or 'ok'}")
    return "\n".join(L)


def ladder_progress(label: str, cfg: dict) -> dict[str, dict]:
    """For the run side (cli ladder phases): {"<category>/<part>": {"std":
    standard share 0-1 or None, "sets": {set n: share or None}}} from the
    model's CURRENT board rows. Feed it to ``ladder.due``."""
    prof = _tier_profile(cfg)
    if not points_mode(prof):
        return {}
    # the run side: rows under the current sampler only (sampler_select)
    rows = board_filter(label, cfg, sampler_view="current")
    st = tier_model_stats(label, cfg, rows, load_meta(label), {"old": False})
    card = tier_scorecard(st, scoring_config(cfg), prof)
    out = {}
    for cat, parts in (card.get("ladder") or {}).items():
        for pk, det in parts.items():
            # a part with standard rows missing unlocks nothing (unlock_share None)
            out[f"{cat}/{pk}"] = {"std": det.get("unlock_share", det.get("std_share")),
                                  "opened": det.get("opened"),      # 4.5 bank: routed packs
                                  "pack_gate": det.get("pack_gate"),  # routed part packs: what was compared
                                  "sets": {x["n"]: x["share"] for x in det.get("sets") or []},
                                  "rungs": {x["n"]: x["rung_shares"] for x in det.get("sets") or []
                                            if x.get("rung_shares")}}
    from . import segments45 as _s45
    if not _s45.tree(prof):
        return out
    tb, acc = _run_side_tree45(label, st, card, rows, prof)
    pk_keys = [k for k in out if k.startswith("chat/pk_")]
    if pk_keys:
        # Chat packs (packs45): the gate is the segment's standard share from the same score tree and marks as the
        # board, never the part's own share (a pk_* part has none), on the top of the segment's own range (step 2:
        # the board already read the range, the run side did not). Anything unreadable = locked (fail closed).
        from . import packs45 as _pk
        for seg, part in _pk.PART_OF.items():
            if f"chat/{part}" in out:
                sh = _pk.segment_share((tb or {}).get("chat"), seg) if tb else None
                b = ((acc or {}).get("segments") or {}).get(f"chat/{seg}") or {}
                lift = float(b["half"]) / float(b["max"]) if b.get("half") is not None and b.get("max") else 0.0
                out[f"chat/{part}"]["std"] = None if sh is None else sh + lift
    if tb is None:
        # fail closed: the Fixes pack has no gate without the score tree; the other parts keep their bare gate
        if "work/c3_fix" in out:
            out["work/c3_fix"]["std"] = None
        return out
    # step 2: engine packs (Code, Everyday) on the top of the range; the Fixes pack on the Fixes segment's
    from . import enginepacks45 as _ep
    _ep.run_gates(out, tb, (acc or {}).get("segments"), _s45.tree(prof))
    return out


def _run_side_tree45(label: str, st: dict, card: dict, rows: list[dict], prof: dict) -> tuple[dict | None, dict | None]:
    """(score tree, accuracy block) for the run side (ladder_progress), from the same tree and marks as the board;
    (None, None) when unreadable."""
    try:
        from . import accuracy45 as _A45
        from . import fixrows45 as _fr
        from . import headline as _h
        from . import segments45 as _s45
        model = {**st, "scorecard": card, "headline": _h.summarize(card, None, prof),
                 "rplong45": _rplong45(label, rows), "fix45": _fr.case_scores(rows, _fix_cases(prof))}
        tb = _s45.segment_table(model, prof, _s45.load_marks(results_dir()))
        if not tb:
            return None, None
        return tb, _A45.model_accuracy({**model, "segments45": tb}, prof)
    except Exception as e:  # noqa: BLE001 - the run side never opens a pack it cannot score
        log.warning("pack gates unreadable for %s (%s: %s): Chat and Fixes packs stay locked", label,
                    type(e).__name__, e)
        return None, None


_SLOP_CACHE: dict = {}


def _slop_block(nsfw_rows: list[dict], cfg: dict | None) -> dict | None:
    """The report-only slop block (slop45), computed once per row set and never inside a
    bootstrap resample (it reads no score; recomputing it per resample made the report ~3x slower)."""
    if _BOOT.get() is not None:
        return None
    key = (active_name(cfg), tuple(sorted((str(r.get("case_id")), str(r.get("ts")), len(r.get("response") or ""))
                                          for r in nsfw_rows)))
    if key not in _SLOP_CACHE:
        try:
            from . import slop45
            from .profiles import load_profile
            _SLOP_CACHE[key] = slop45.score_rows(
                slop45.main_nsfw_rows(nsfw_rows, load_profile(active_name(cfg), cfg)))
        except Exception:  # noqa: BLE001 - report-only metric: never breaks the report
            _SLOP_CACHE[key] = None
    return _SLOP_CACHE[key]


def tier_model_stats(label: str, cfg: dict | None, rows: list[dict], meta: dict | None,
                     era: dict | None) -> dict:
    """model_stats over the TIER-1 rows (every component, coverage, notes),
    plus ``tier_stats`` (stats over each tier-2/3's rows) and ``tier_states``
    (per tier and score: none / na_ctx / rows) for :func:`tier_scorecard`.
    Without a ``tiers:`` profile: plain model_stats over every row."""
    prof = _tier_profile(cfg)
    if not prof:
        return model_stats(label, cfg, rows=rows, meta=meta, era=era)
    st = stats_from_tier_rows(label, cfg, prof, tier_rows(prof, rows, era), meta, era)
    _lockout_share(st, prof, label)
    _routed45_extras(st, prof, label, cfg, rows, meta, era)
    return st


def _routed45_extras(st: dict, prof: dict | None, label: str, cfg: dict | None, rows: list[dict],
                     meta: dict | None, era: dict | None) -> None:
    """Display facts of a routed 4.5 row that the board rows alone do not hold (Admin B,
    2026-11-14): its suite identity, the route it took (from every stored row of the run, so a
    route made on an older stamp is still said), the whole run's cost and, per part, the units
    whose stored rows are on an older stamp. Reads the transcripts; changes no number."""
    if not routed(prof) or _BOOT.get() is not None:
        return
    st["suite45"] = suite45_id(prof, meta)
    if not (era or {}).get("old"):
        try:
            units = pack_unit_shares(prof, label, cfg, rows, meta, era)
            if units:
                st["pack_units45"] = units
        except Exception as e:  # noqa: BLE001 - display only: a pack without units has no margin
            log.warning("routed pack units for %s not read: %s", label, e)
    try:
        all_rows = load_transcripts(label)
    except Exception as e:  # noqa: BLE001 - display only: the board keeps what it has
        log.warning("routed board extras for %s not read: %s", label, e)
        return
    from . import chains as _chains
    from . import routing as _routing
    st["route45"] = st.get("route") or _routing.route_of([r for r in all_rows if r.get("chain")])
    if (era or {}).get("old"):
        return
    usd, basis = all_rows_cost(label, all_rows, cfg, meta, prof)
    if usd is not None:
        st["cost_all_usd"], st["cost_all_basis"] = round(usd, 4), basis
    cs = st.get("chain_stats") or {}
    # the units the board plans for this model (on its route: section_stats' per_chain), else
    # every unit of the profile
    idx = ({cid for s in cs.values() for cid in (s.get("per_chain") or {})}
           or set(_chains.profile_index(prof)))
    for part, n in stale_units(all_rows, rows, idx).items():
        if part in cs:
            cs[part]["stale_units"] = n


def pack_unit_shares(prof: dict, label: str, cfg: dict | None, rows: list[dict],
                     meta: dict | None, era: dict | None) -> dict[str, list[float]]:
    """Per ladder pack "<score>/<part>#<n>" of a part the routed board shows: each question's
    share (the pack's own scoring over that question's board rows, its repeats pooled = one
    cluster), for the pack's margin (bonus45). A question with no row, or still waiting for its
    judge, is not a unit yet. Reads rows; changes no score."""
    shown = {f"{d['key']}/{p['key']}": (d, p) for d in board_defs(prof) for p in d.get("parts") or []}
    lad = _ladder_levels(prof)
    by_case: dict[str, list[dict]] = {}
    for r in rows:
        k = lad.get(r.get("case_id"))
        if k and k[0] in shown:
            by_case.setdefault(r["case_id"], []).append(r)
    partial = frozenset(prof.get("partial_credit") or ())
    out: dict[str, list[float]] = {}
    for cid, rs in sorted(by_case.items()):
        key, n = lad[cid]
        d, p = shown[key]
        sw = _set_weights(prof, [cid], p.get("scoring"))
        if not sw:
            continue
        ls = model_stats(label, cfg, rows=rs, meta=meta, era=era)
        if ls.get("pending_judge") and _judged_weights(sw):
            continue
        v = scorecard(ls, {d["group"]: sw}, partial=partial)[d["group"]]
        if v is not None:
            out.setdefault(f"{key}#{n}", []).append(v / 100.0)
    return out


def _lockout_share(st: dict, prof: dict | None, label: str) -> None:
    """Admin N (task C): a profile with ``flags.lockout.scored`` gets the share of Lockout
    rungs written as ``nsfw.lockout_share`` (the ``lockout`` component). The rungs are not
    profile cases (board_filter drops their rows), so they are read from the transcripts."""
    from . import lockout as _lockout
    if not _lockout.scored(prof):
        return
    try:
        st.setdefault("nsfw", {})["lockout_share"] = _lockout.board_share(prof, label)
    except Exception as e:  # noqa: BLE001 - a bad rung file leaves the part pending, never 0
        log.warning("lockout share for %s not read: %s", label, e)


def _cards(labels, stats, cfg):
    sc = scoring_config(cfg)
    prof = _tier_profile(cfg)
    for l in labels:
        stats[l]["scorecard"] = (tier_scorecard(stats[l], sc, prof)
                                 if prof and "tier_stats" in stats[l]
                                 else scorecard(stats[l], sc))
    # lead 2026-11-19 (b): board-level ranking honesty, every time the cards are (re)built
    rank_parts45([l for l in labels if "scorecard" in stats[l]], stats, prof)
    return sc


# ------------------------------------------------------------- report.md

def scorecard_footnote(sc: dict, cfg: dict | None, markdown: bool = True) -> str:
    """The scorecard footnote (weights + the judge whose verdicts count) —
    shared by report.md and report.html."""
    def wtxt(grp):
        return ", ".join(f"{component_label(k)} {int(v) if float(v).is_integer() else v}"
                         for k, v in sc[grp].items())
    jname = short_model(_primary_judge(cfg)) or "the profile's judge"
    b, i, c = ("**", "*", "`") if markdown else ("", "", "")
    prof = _tier_profile(cfg)
    if prof and prof.get("scores"):
        from . import tiers as _tiers
        defs = _tiers.scores(prof)
        parts = [f"{b}{d['label']}{b} = {wtxt(d['group'])}" for d in defs if sc.get(d["group"])]
        tot = [d["label"] for d in defs if d["in_total"]]
        sep = [d["label"] for d in defs if not d["in_total"]]
        return (f"{i}Per tier 0–100. " + "; ".join(parts) + f" (judged rows by {jname}, the "
                f"rest deterministic graders; Reason pools reasoning + math). Each score = "
                f"T1 + 0.5 × T2 + 0.5 × T3 (max 200); {b}Overall{b} = the mean of "
                f"{' and '.join(tot)}" + (f"; {' and '.join(sep)} never counts in it" if sep else "")
                + f". tok/s = median generation speed (comparable on the same host only). "
                f"Per-case failures: {c}failures.md{c}.{i}")
    if "reason" in sc:
        return (f"{i}Per tier 0–100. {b}Chat{b} = {wtxt('chat')} (judged by {jname}). "
                f"{b}Code{b} = {wtxt('coding')} (deterministic graders). {b}Reason{b} = "
                f"reasoning + math pass rate, pooled. Each score = T1 + 0.5 × T2 + 0.5 × T3 "
                f"(max 200); {b}Overall{b} = the mean of the three — the sort key only. "
                f"tok/s = median generation speed (comparable on the same host only). "
                f"Per-case failures: {c}failures.md{c}.{i}")
    return (f"{i}0–100. {b}Chat{b} = {wtxt('chat')} (judged by {jname}). "
            f"{b}Coding{b} = {wtxt('coding')} (Reason pools reasoning + math; all "
            f"deterministic graders). {b}Overall{b} = Chat and Coding combined by those weights — "
            f"the sort key only. tok/s = median generation speed (comparable on the "
            f"same host only). Per-case failures: {c}failures.md{c}.{i}")


def _suite_head(stats: dict, cfg: dict | None) -> list[str]:
    """Header fragments: the current suite, and how many models still show
    values of an older one (the Suite column says which)."""
    out = []
    cur = _current_revision(cfg)
    prof = _tier_profile(cfg) if any(s.get("suite45") for s in stats.values()) else None
    if routed(prof):
        out.append(suite45_head(prof, cur))      # a routed board: the 4.5 suite, never 4.1's
    elif cur:
        out.append(f"suite {cur}")
    if any(s.get("validation_cc") for s in stats.values()):
        out.append(STAGED_CC_HEAD)
    n_old = sum(1 for s in stats.values() if (s.get("era") or {}).get("old"))
    if n_old:
        out.append(f"{n_old} model{'s' if n_old != 1 else ''} still on old values "
                   f"(marked old, re-run pending)")
    return out


def _conformance_lines(labels: list[str], stats: dict, cfg: dict | None) -> list[str]:
    """The conformance section of report.md: every model's verdict and the
    date it was proven on.  The checks are listed only when one of them is
    not a PASS (a FAIL keeps the model off the board, a WARN says what to
    look at)."""
    rows = []
    for l in labels:
        rec = conformance_record(l)
        if not rec:
            continue
        checks = rec.get("checks") or []
        odd = [f"{c.get('id')} {c.get('status')}" for c in checks
               if c.get("status") not in ("PASS", "INFO")]
        from .conformance import effective_verdict
        rows.append([l, effective_verdict(rec) if rec.get("verdict") else "-",
                     str(rec.get("ts") or "-")[:10],
                     ", ".join(odd) if odd else "all PASS"])
    if not rows:
        return []
    return (["", "## Conformance", ""]
            + _table(["Model", "Verdict", "Date", "Checks"], rows)
            + ["", "*A model is benched only under a request protocol that was "
              "proven for it (`crucibleforge conform`): a FAIL keeps it off "
              "the board.*"])


def render_markdown(labels: list[str], stats: dict, cfg: dict | None,
                    archived_note: str | None = None) -> str:
    """report.md: the scorecard (Chat / Coding) + one component table."""
    sc = _cards(labels, stats, cfg)
    ranked = rank_labels(labels, stats)
    seg_board = any((stats[l] or {}).get("segments45") for l in labels)
    L = [("# CrucibleForge: Chat & Coding" if seg_board else "# CrucibleForge — Chat & Coding"), ""]
    head = [f"Generated {datetime.now().strftime('%Y-%m-%d %H:%M')}"]
    head += _suite_head(stats, cfg)
    judge = _primary_judge(cfg)
    if judge:
        head.append(f"judge {short_model(judge)}")
    L.append(" · ".join(head))
    if archived_note:
        L += ["", archived_note]
    L += ["", "## Scorecard", ""]
    if any("tiers" in stats[l]["scorecard"] for l in ranked):
        L += _tier_tables(ranked, stats, sc, cfg)
        older = _older_line(labels, stats, cfg)
        if older:
            L += ["", older]
        L += _conformance_lines(labels, stats, cfg)
        if parts_board45(cfg):
            # 4.5: the parts are the components (no 4.1 category table on a 4.5 board)
            L += _parts45_md(ranked, stats)
            return _components_md(L, ranked, stats, {})
        return _components_md(L, ranked, stats, sc)
    rows = []
    for i, l in enumerate(ranked, 1):
        c = stats[l]["scorecard"]
        rows.append([str(i), l, fmt(c["chat"]), fmt(c["coding"]), fmt(c["total"]),
                     room_cell(stats[l]), fmt(c["tok_per_s"])]
                    + ([cost_cell(stats[l])] if _has_cost(ranked, stats) else [])
                    + [suite_cell(stats[l]), run_date(stats[l]), notes_cell(stats[l])])
    L += _table(["#", "Model", "Chat", "Coding", "Overall", "Room", "tok/s"]
                + (["Run cost"] if _has_cost(ranked, stats) else [])
                + ["Suite", "Run date", "Notes"], rows)
    L += ["", scorecard_footnote(sc, cfg)]
    if _has_cost(ranked, stats):
        L += ["", f"*{COST_FOOTNOTE}*"]
    L += ["", "*Room = Overall with every answer that ran out of the board's budget re-run on a "
              "bigger one (escalation rungs, e.g. 64k then 128k; the model gets a wider context "
              "and fewer parallel slots, its KV cache may drop to q8_0 to fit). \"=\" nothing ran "
              "out; \"…\" re-run or its judging still pending. The board ranks on Overall — the "
              "same budget for every model — and rows still on old values (Suite \"(old)\") "
              "rank below every current row: old and current regimes are not comparable.*"]

    L += _conformance_lines(labels, stats, cfg)
    return _components_md(L, ranked, stats, sc)


def _tier_tables(ranked: list[str], stats: dict, sc: dict, cfg: dict | None) -> list[str]:
    """Suite 3.9 scorecard: each score as total (tier reached), then the
    per-tier numbers behind it. The scores are the profile's (tiers.scores)."""
    from . import tiers as _tiers
    prof = _tier_profile(cfg)
    if points_mode(prof):
        return _points_tables(ranked, stats, sc, cfg, prof)
    defs = _tiers.scores(prof)
    L, rows, trows = [], [], []
    for i, l in enumerate(ranked, 1):
        c = stats[l]["scorecard"]
        lab = c.get("labels") or {}
        hl = stats[l].get("headline")
        if hl:
            rows.append([hl.get("rank", str(i)), l, headline_cell(hl, "overall")]
                        + [headline_cell(hl, d["key"]) for d in defs]
                        + [raw_cell(c, defs), room_cell(stats[l]), fmt(c["tok_per_s"])]
                        + ([cost_cell(stats[l])] if _has_cost(ranked, stats) else [])
                        + [suite_cell(stats[l]), run_date(stats[l]), notes_cell(stats[l])])
        else:
            rows.append([str(i), l] + [lab.get(d["key"], "-") for d in defs]
                        + [fmt(c["total"]), room_cell(stats[l]), fmt(c["tok_per_s"])]
                        + ([cost_cell(stats[l])] if _has_cost(ranked, stats) else [])
                        + [suite_cell(stats[l]), run_date(stats[l]), notes_cell(stats[l])])
        ts = c.get("tiers") or {}
        cells = [l]
        for d in defs:
            e = ts.get(d["key"]) or {}
            st = e.get("state") or {}
            parts = [fmt(e.get("t1"), ".0f")]
            for t in _tiers.TIERS:
                x = st.get(t)
                parts.append({"none": "–", "locked": "locked", "na_ctx": "n/a (ctx)",
                              "pending": "pending"}.get(x, fmt(e.get(f"t{t}"), ".0f")))
            cells.append(" / ".join(parts))
        trows.append(cells)
    public = bool(_hidden(cfg))
    labels = [score_label(d, public) for d in defs]
    if any(stats[l].get("headline") for l in ranked):
        L += _table(["#", "Model", "Overall"] + labels
                    + [f"Raw % ({' / '.join(d['label'] for d in defs)} → Overall)", "Room",
                       "tok/s"] + (["Run cost"] if _has_cost(ranked, stats) else [])
                    + ["Suite", "Run date", "Notes"], rows)
        L += ["", headline_footnote(defs, prof)]
        if _has_cost(ranked, stats):
            L += ["", f"*{COST_FOOTNOTE}*"]
        if any(d.get("parts") for d in defs):
            prow = []
            for l in ranked:
                hl = stats[l].get("headline")
                if hl:
                    prow.append([l] + [points_cell(hl, d) for d in defs])
            L += ["", "### Points", ""]
            L += _table(["Model"] + labels, prow)
            L += ["", "*Each category is out of 10 and is the sum of its sub-scores' points "
                      "(sub-score share × its maximum: " + "; ".join(
                          f"{d['label']} = " + " + ".join(f"{p['label']} {p['weight']:g}"
                                                          for p in d["parts"])
                          for d in defs if d.get("parts")) + "). A sub-score with no item yet "
                      "is \"pending\"; its category is then scaled up from the points it "
                      "measured (stated in the cell). ± on a sub-score is in points.*"]
        reads = [stats[l]["headline"]["vs_above"]["text"] for l in ranked
                 if (stats[l].get("headline") or {}).get("vs_above")]
        if reads:
            L += ["", "### Who is better", ""] + [f"- {t}" for t in reads]
    else:
        L += _table(["#", "Model"] + labels + ["Overall", "Room", "tok/s"]
                    + (["Run cost"] if _has_cost(ranked, stats) else [])
                    + ["Suite", "Run date", "Notes"], rows)
    L += ["", scorecard_footnote(sc, cfg)]
    if _has_cost(ranked, stats):
        L += ["", f"*{COST_FOOTNOTE}*"]
    L += ["", "## Tiers", ""]
    L += _table(["Model"] + [f"{x} T1 / T2 / T3" for x in labels], trows)
    L += ["", tier_footnote(prof)]
    return L


#: score key -> where its total sits (totals45): the board's groups and the adult page's numbers
_HEAD45 = {"work": ("main", "code"), "code": ("main", "code"), "chat": ("main", "chat"),
           "overall": ("main", "overall"), "nsfw": ("adult", "nsfw"), "uncensored": ("adult", "uncensored"),
           "nsfw_plus": ("adult", "nsfw_plus")}


def head45(name: str, key: str, stats: dict, std_max: float) -> str:
    """A 4.5 column head over a TOTAL (standard + bonus packs, R37/R38): "Overall /34" with the maximum with packs
    from the data when every row has the same one, the bare name when they differ, the standard "/20" only when no
    row has a total. A total above 20 is never printed under "/20"."""
    where, sec = _HEAD45.get(key, ("main", key))
    mx = set()
    for s in stats.values():
        g = ((s.get("totals45") or {}) if where == "main"
             else ((s.get("nsfw_plus") or {}).get("totals45") or {})).get(sec) or {}
        if g.get("total") is not None and g.get("max_with_packs") is not None:
            mx.add(round(float(g["max_with_packs"]), 4))
    if not mx:
        return f"{name} /{std_max:g}"
    return f"{name} /{mx.pop():g}" if len(mx) == 1 else name


SEG45_LEGEND = (
    "Overall = Code total + Chat total; each total = the standard score (out of 10) + its bonus packs, so the column "
    "head shows the maximum with every pack that exists today. Overall is printed only when both are complete; "
    "otherwise the row reads \"x of y measured\" and is not ranked.",
    "NSFW+ = NSFW + Uncensored (each out of 10 on the standard set, plus their own bonus packs), in its own column "
    "beside Overall and never part of it (its two halves are printed too; breakdown in the section below the table).",
    "± beside a score = its 90% accuracy range (INTENT-45), with its source named after it: the model's own "
    "replicate runs when it has five or more, else the spread pooled over every model's replicate runs (a model "
    "with two or more runs shows the mean of its runs; range = t x SD / sqrt(runs), t = the 90% Student value for "
    "the number of runs behind the SD), else this run's own repeats, else a stored estimate.",
    "{margin} {rank}",
    "Ladder packs are harder question sets worth up to 1 point per pack, unlocked by 90 % or more on the standard "
    "questions; their points add on top of Code and Chat and so count in Overall and the rank (\"= standard + "
    "bonus\").",
    "A segment that is pending adds nothing, is never scaled up, and names its reason. Room = the score with the "
    "end-of-run retry.",
)


def seg45_legend(labels: list[str], stats: dict) -> list[str]:
    """SEG45_LEGEND with the ± sentence filled in from what the margins hold."""
    m = margin_note45(labels, stats).replace("± = the 90% margin:", "The item margin in report.json (how far a "
                                             "different question set could move a score) = the 90% margin (z 1.645), "
                                             "in points:")
    return [t.replace("{margin}", m + " A segment's margin combines its parts as independent. Where no per-unit "
                      "spread is stored the margin is 1.645 x points x sqrt(p(1-p)/n): the fix tasks are pooled into "
                      "one share for it, while each long scene stays per scene (n = its repeats).")
            .replace("{rank}", LAST_RANK_NOTE45[0] if LAST_RANK_NOTE45
                     else "A rank like \"3=\" means statistically tied with the model above.")
            for t in SEG45_LEGEND]


def segment_maxima_note(prof: dict) -> str:
    """The one-line key under the Points table: each section's segments and their points."""
    from . import segments45 as _s45
    t = _s45.tree(prof) or {}
    seg = "; ".join(f"{sec.get('label', k)} = " + " + ".join(f"{g['label']} {g['max']:g}"
                                                          for g in sec.values() if isinstance(g, dict))
                    for k, sec in t.items() if k in _s45.SECTIONS)
    np_ = (prof.get("nsfw_plus") or {}).get("numbers") or {}
    npt = "; ".join(f"{n.get('label', k)} = " + " + ".join(f"{_NP_SEG_LABEL.get(sk, sk)} {g['max']:g}"
                                                          for sk, g in n.items() if isinstance(g, dict) and "max" in g)
                    for k, n in np_.items())
    return ("*Segment maxima: " + seg + (f"; NSFW+ (never in Overall): {npt}" if npt else "") + ". Points = the share of "
            "each item passed times its points. A segment with an item not yet measured adds nothing for it: the "
            "cell shows the measured points out of the measured maximum and names the pending points. "
            "± in points.*")


def _points_tables(ranked: list[str], stats: dict, sc: dict, cfg: dict | None,
                   prof: dict) -> list[str]:
    """The points board (the maintainer 2026-10-28): Overall = Coding/Work + Chat,
    each category out of 10 with its sub-score points; NSFW and custom
    tests beside it; the ladder sets reached."""
    from . import ladder as _ladder
    from . import tiers as _tiers
    # routed: a separate section (NSFW) with no cell in the composition is not printed at all
    defs = shown_defs45(prof) if routed(prof) else board_defs(prof)
    public = bool(_hidden(cfg))
    seg45 = bool(prof.get("segments45"))
    if seg45:                                       # realignment names: Code, Chat (spec section 6)
        defs = [{**d, "label": {"work": "Code", "chat": "Chat"}.get(d["key"], d["label"])} for d in defs]
    labels = [score_label(d, public) for d in defs]
    if seg45:
        labels = [{"Code": head45("Code", "code", stats, 10), "Chat": head45("Chat", "chat", stats, 10)}.get(x, x)
                  for x in labels]
    # 4.0: Room beside Overall when re-runs do not count on the board
    room_col = not prof.get("escalation_on_board", True)
    L, rows, prow = [], [], []
    is_routed = routed(prof)
    # rc3 adult page (score_subtotals): the 4.x board shows its two numbers in place of the score's one
    sub_key, subs = (None, []) if is_routed else adult_subtotals(ranked, stats)
    if sub_key and not any(d["key"] == sub_key for d in defs):
        sub_key, subs = None, []
    if seg45 and not is_routed:
        # NSFW+ (realigned tree): its two numbers come from the NSFW+ block, never the summed column
        sub_key = next((d["key"] for d in defs if not d.get("in_total", True)), sub_key)
        subs = list(NSFW_PLUS_KEYS) if sub_key else []
    # INTENT-45: NSFW+ /20 (= NSFW + Uncensored) in its own column beside Overall, never part of it
    np_col = bool(seg45 and not is_routed and sub_key)
    # the profile's status banner first (board_notes.status, routed or not); Class hidden on an internal candidate
    show_class = not class_hidden45(prof)
    # R5: standard and pack points as two numbers (a 4.5 chains board): the section cells hold the standard points
    pack_col = bool(prof.get("chains")) and not is_routed
    # Assistant basics (checked): one column outside Overall (assistant45.py); no rank, sort or total reads it
    from . import assistant45 as _a45
    a45 = _a45.block(prof) if (pack_col and not seg45) else None     # Everyday is a Chat segment now
    a45_valid = _a45.validated(prof)
    a45_ceil = _a45.ceiling(ranked, stats) if a45 and a45_valid else False
    L += banner45_md(banner45(prof, ranked, stats))
    if mixed_judges45(ranked, stats):
        L += [f"> {MIXED_JUDGES_LINE}", ""]
    for i, l in enumerate(ranked, 1):
        c = stats[l]["scorecard"]
        hl = stats[l].get("headline") or {}
        sec_cells = [ADULT_WITHHELD45 if (not d.get("in_total", True) and c.get("adult_withheld")) else
                     (section_cell45(hl, d) if prof.get("chains") and d.get("in_total", True) and not is_routed
                      else headline_cell(hl, d["key"])) + _set_tag(hl, d) + _partly_tag(hl, d)
                     + _refuses_tag(stats[l], d)
                     + (" **MAXED**" if ((hl.get("maxed") or {}).get(d["key"]) or {}).get("category")
                        else "") for d in defs]
        if is_routed:
            # 4.5 routed: no Efficiency / Room (4.1 budget rungs); the whole run's cost; the route
            sec_cells = [c + bonus45_tag(hl, d["key"]) for c, d in zip(sec_cells, defs)]
            rows.append([hl.get("rank", str(i)), l, held45_cell(stats[l])
                         or (headline_cell(hl, "overall") + bonus45_tag(hl, "overall") + other_judge_cell(stats[l]))]
                        + ([hl.get("class") or "-"] if show_class else []) + [hl.get("lean") or "-"]
                        + sec_cells
                        + [fmt(c["tok_per_s"])]
                        + ([cost45_cell(stats[l])] if _has_cost(ranked, stats) else [])
                        + [suite_cell(stats[l]), run_date(stats[l]), notes45_cell(stats[l])])
            prow.append([l] + [points_cell(hl, d) for d in defs])
            continue
        # rc3 adult page: its two numbers in place of the one score cell (never in Overall)
        tb45 = stats[l].get("segments45")
        if tb45:                                    # the score tree's section cells (segments45.cell)
            sec_cells = [(_s45cell(tb45, "code" if d["key"] == "work" else "chat") if d["key"] in ("work", "chat") else c_)
                         for d, c_ in zip(defs, sec_cells)]
        sec_cells = _subs_split(sec_cells, defs, sub_key,
                                lambda d: [(np_cell(stats[l], nm) if seg45 else subtotal_cell(stats[l], nm))
                                           for nm, _ in subs])
        ov_cell = (hl.get("not_ranked") or "pending") if (tb45 and hl.get("S", {}).get("overall") is None) else None
        rows.append([hl.get("rank", str(i)), l, ov_cell or held45_cell(stats[l])
                     or (headline_cell(hl, "overall") + other_judge_cell(stats[l]))]
                    + ([np_total_cell(stats[l])] if np_col else [])
                    + ([hl.get("class") or "-"] if show_class else []) + [hl.get("lean") or "-"]
                    + ([room_cell(stats[l]) or "-"] if room_col else [])
                    + sec_cells
                    + ([bonus_packs_cell45(stats[l]) if stats[l].get("totals45")
                        else packs_cell45(hl, [d for d in defs if d.get("in_total", True)])] if pack_col else [])
                    + ([_a45.cell(stats[l].get("assistant45"), a45_valid, a45_ceil)] if a45 else [])
                    + [efficiency_cell(stats[l]), fmt(c["tok_per_s"])]
                    + ([cost_cell(stats[l])] if (pack_col or _has_cost(ranked, stats)) else [])
                    + ([minutes45_cell(stats[l]), disclosure45_cell(stats[l])] if pack_col else [])
                    + [suite_cell(stats[l]), run_date(stats[l]), notes_cell(stats[l])])
        prow.append([l] + _subs_split([(_s45line(tb45, "code" if d["key"] == "work" else "chat", stats[l].get("marks45"))
                                        if tb45 and d["key"] in ("work", "chat") else points_cell(hl, d))
                                       for d in defs], defs, sub_key,
                                      lambda d: [(np_line(stats[l], nm) if seg45 else subtotal_parts_line(stats[l], nm, d))
                                                 for nm, _ in subs]))
    if routed(prof):
        n_in = sum(1 for d in _scale_sections(prof) if d.get("in_total", True))
        # the max is the profile's (one per pack that exists), the same on every row
        hl0 = next(((stats[l].get("headline") or {}) for l in ranked
                    if (stats[l].get("headline") or {}).get("max45")), None)
        ov_label = f"Overall ({max45_label(hl0, 'overall', n_in * SCALE45_PER_POINT)})"
        hlabels = [lb + (f" ({max45_label(hl0, d['key'], SCALE45_PER_POINT)})"
                         if any(x["key"] == d["key"] for x in _scale_sections(prof))
                         else "") for lb, d in zip(labels, defs)]
    else:
        ov_label, hlabels = (head45("Overall", "overall", stats, 20) if seg45 else "Overall"), labels
    # NSFW+: its two numbers, each out of 10 (plus its packs), in place of its score's one column
    _sl = (lambda k, sl: head45(sl, k, stats, 10)) if seg45 else (lambda k, sl: f"{sl} (/10)")
    hlabels = _subs_split(hlabels, defs, sub_key, lambda d: [_sl(k, x) for k, x in subs])
    plabels = _subs_split(labels, defs, sub_key, lambda d: [_sl(k, x) for k, x in subs])
    if is_routed:
        L += _table(scorecard45_header(hlabels, ov_label, _has_cost(ranked, stats), show_class), rows)
    else:
        L += _table(["#", "Model", ov_label] + ([head45("NSFW+", "nsfw_plus", stats, 20)] if np_col else []) + (["Class"] if show_class else [])
                    + ["Lean"]
                    + (["Room"] if room_col else []) + hlabels
                    + ([("Bonus packs" if seg45 else "Pack points")] if pack_col else [])
                    + ([a45["label"]] if a45 else [])
                    + ["Efficiency", "tok/s"] + (["Run cost"] if (pack_col or _has_cost(ranked, stats)) else [])
                    + (["Minutes", "Run settings"] if pack_col else [])
                    + ["Suite", "Run date", "Notes"], rows)
    if routed(prof):
        L += ["", scale45_footnote(prof, [stats[l].get("scale45") for l in ranked]), "",
              class_footnote(prof, show_class), "", maxed45_footnote(prof, show_class)]
    else:
        L += ["", ("*" + " ".join(seg45_legend(ranked, stats)) + "*") if seg45 else points_footnote(defs, prof), "",
              class_footnote(prof, show_class)]
    if any(stats[l].get("validation_cc") for l in ranked):
        L += ["", f"*{STAGED_CC_FOOTNOTE}*"]
    if _has_cost(ranked, stats):
        L += ["", f"*{cost_footnote(prof, ranked, stats, cfg)}*"
                  + (f" *{COST_CHAINS_NOTE}*" if prof.get("chains") else "")
                  + (f" *{COST45_NOTE}*" if is_routed else "")]
    reads = [stats[l]["headline"]["vs_above"]["text"] for l in ranked
             if (stats[l].get("headline") or {}).get("vs_above")]
    if reads:
        L += ["", "### Who is better", ""] + [f"- {t}" for t in reads]
    L += ["", "### Points", ""]
    L += _table(["Model"] + plabels, prow)
    cgl = [f"- {l}: " + "; ".join((stats[l].get("scorecard") or {}).get("adult_code_gate") or []) for l in ranked
           if (stats[l].get("scorecard") or {}).get("adult_code_gate")]
    if cgl:
        L += ["", f"*{CODE_GATE_NOTE}*", ""] + cgl
    if is_routed:
        L += ["", maxima45_footnote(defs)]
    elif seg45:
        L += ["", segment_maxima_note(prof)]
        stl = scorer_stamp_note(ranked, stats)
        if stl:
            L += ["", f"*{stl}*"]
        anc = anchors_lines()
        if anc:
            L += ["", "### Validation anchors", "", f"*{ANCHORS_NOTE}*", ""] + [f"- {a}" for a in anc]
        from . import display45 as _d45
        hb = _d45.held_back_md(held_back45(ranked, stats, prof))
        if hb:
            L += [""] + hb
    else:
        no_scale = (prof or {}).get("pending_scaling") == "none"
        ceil = ceiling_parts45(ranked, stats) if prof.get("chains") else set()
        L += ["", "*Sub-score maxima: " + "; ".join(
            f"{d['label']} = " + " + ".join(_ceil_lab(d, p, ceil) + f" {p['weight']:g}" for p in d["parts"])
            for d in defs if d.get("parts")) + ". Points = the sub-score's share × its maximum; "
            + ("a pending part (no item yet, not validated, not run or not measured) adds nothing: the cell "
               "shows the measured points of the measured maximum and names the pending points. "
               if no_scale else
               "\"pending\" = no item yet (its category is scaled up from the measured points, "
               "stated in the cell). ") + "± in points.*"]
    pn = part_notes_line(defs) if prof.get("chains") and not seg45 else None
    if pn:
        L += ["", pn]
    if prof.get("chains") and not is_routed and not seg45:
        L += ["", f"*{margin_note45(ranked, stats)}*"]
    if pack_col:
        L += ["", f"*{PACKS_NOTE45 if seg45 else PACKS_NOTE}*", "", f"*{MINUTES_NOTE}*", "", f"*{SETTINGS_NOTE}*"]
        if seg45:
            L += ["", f"*{CUT_NOTE45}*"]
    jn = [] if seg45 else judged_part_notes(prof)
    if jn:
        L += [""] + [f"*{t}*" for t in jn]
    if a45:
        L += ["", f"*{_a45.NOTE}*"]
        if not a45_valid:
            L += ["", f"*{_a45.pending_note()}*"]
    if prof.get("chains") and ceiling_parts45(ranked, stats):
        L += ["", f"*{CEILING_NOTE}*"]
    if routed(prof):
        L += cells45_tables(ranked, stats, prof)
    if seg45 and not is_routed:
        L += [""] + nsfw_plus_md(ranked, stats)
    # a routed profile's packs are cells the route opens (scored apart), never 4.1 ladder sets
    sets = {} if routed(prof) else _ladder_sets(prof)
    if sets:
        lrows = []
        for l in ranked:
            lad = (stats[l]["scorecard"].get("ladder") or {})
            cells = [l]
            for key, seq in sets.items():
                cat, _, pk = key.partition("/")
                det = (lad.get(cat) or {}).get(pk) or {}
                bits = []
                for st_ in det.get("sets") or []:
                    v = st_.get("share")
                    bits.append({"locked": "locked", "pending": "pending"}.get(
                        st_["state"], f"{(v or 0) * 100:.0f}%"))
                why = det.get("gate_reason")
                cells.append(" / ".join(bits) + (f" ({why})" if why and bits else "") if bits else "-")
            lrows.append(cells)
        L += ["", "### Ladders", ""]
        if seg45:
            from . import segments45 as _s45l
            _pn = _s45l.part_names(prof)
            _lk = {k: _s45l.plain_text(k, _pn) for k in sets}
        else:
            _lk = {k: k for k in sets}
        L += _table(["Model"] + [f"{_lk[k]} sets " + " / ".join(f"L{x['level']}" for x in v)
                                 for k, v in sets.items()], lrows)
        L += ["", f"*A ladder pack is a set of harder questions worth 1 point together (partial "
                  f"credit inside); the sets of a level are given only after ≥ "
                  f"{_ladder.unlock_share(prof) * 100:.0f}% on the standard level and every set "
                  f"of every lower level. \"pending\" = unlocked, not run or not judged yet "
                  f"(0 for now). (set n) after a score = the highest level scored.*"]
    if is_routed:
        L += render_board45(ranked, stats, prof)
    elif prof.get("chains"):
        from . import chains as _chains
        # one margin per part / section / Overall: the Scorecard's (lead 2026-11-14)
        one = points_mode(prof) and any((stats[l].get("headline") or {}).get("m") for l in ranked)
        gen_ = _chains.render_board(ranked, {l: _redact_chain_stats45(stats[l], prof) for l in ranked}, prof,
                                    {l: {k: v for k, v in ((stats[l].get("headline") or {}).get("m") or {}).items()
                                         if k not in withheld_parts45(stats[l])} for l in ranked}
                                    if one else None, shares=seg45)
        if seg45:
            # the realigned page: only the segment tree's parts, named by their segment's words (never an old part
            # id or an old NSFW part); the shares stand, no old maximum is printed (shares=True)
            from . import segments45 as _s45g
            _pn = {**_s45g.part_names(prof), "j_rplong": "Long scenes"}
            keep_ = {p_ for sk_ in _s45g.SECTIONS for g_ in ((_s45g.tree(prof) or {}).get(sk_) or {}).values()
                     if isinstance(g_, dict) for p_ in (g_.get("parts") or {})}
            keep_ |= {"j_rplong"} if "rplong" in keep_ else set()
            gen_ = [ln for ln in gen_ if not (ln.startswith("| ") and _gen_row_part(ln) is not None
                                              and _gen_row_part(ln) not in keep_)]
            gen_ = [_s45g.plain_text(ln, _pn) for ln in gen_]
        L += gen_
    return L


def _gen_row_part(line: str) -> str | None:
    """The part id of a Generated items table row (``| model | work/c1_implied | ...``), else None."""
    cells = [c.strip() for c in line.strip().strip("|").split("|")]
    m_ = re.fullmatch(r"(?:work|chat|nsfw|coding|reason)/([a-z][a-z0-9_]*)", cells[1]) if len(cells) > 1 else None
    return m_.group(1) if m_ else None


_CHAIN_NUMBERS = ("share", "points", "mean", "components", "se", "sd", "se_points", "margin_points", "margin_90",
                  "mean_level_held")


def page_chain_stats45(cs: dict | None, prof: dict | None) -> dict:
    """The Generated items rows a page covers (F3, 2026-11-19): a part of a section outside the total (the adult
    page) that no unit was planned, run or stored for is not listed -- 13 empty "0/16" adult rows under a standard
    page read as measured-and-empty. A part with any unit, turn or element (pending, errored, withheld) stays."""
    out_of_total = {d["key"] for d in board_defs(prof) if not d.get("in_total", True)}

    def empty(v):
        return not (v.get("ended") or v.get("turns") or v.get("elements") or any(
            (v.get("tokens") or {}).get(k) for k in ("prompt", "thinking", "completion")))
    return {k: v for k, v in (cs or {}).items()
            if not (str(k).split("/", 1)[0] in out_of_total and empty(v))}


def _redact_chain_stats45(s: dict, prof: dict | None = None) -> dict:
    """R4: a row's chain stats for the Generated items table with the score numbers of withheld adult parts
    removed (they print "-"); every other part as stored."""
    cs = page_chain_stats45(s.get("chain_stats"), prof) if prof is not None else (s.get("chain_stats") or {})
    hide = withheld_parts45(s)
    if not hide:
        return cs
    return {k: ({**v, **{f: None for f in _CHAIN_NUMBERS}, "per_chain": {}} if k in hide else v)
            for k, v in cs.items()}


def _html45_row(s: dict) -> dict:
    """board.json / report.html cells of a routed 4.5 row, as report.md shows them: Notes =
    route + units (notes45_cell), coverage = units ended / planned (not the 4.1 case count), no
    Room (a 4.1 budget rung), cost = the whole run with the counted rows beside it."""
    ended, planned, stale = units45(s.get("chain_stats"))
    out = {"notes": notes45_cell(s), "room": None, "room_cell": None,
           "coverage": f"units ended {ended}/{planned}"
                       + (f"; {stale} on an older stamp, not counted" if stale else "")}
    if s.get("cost_all_usd") is not None:
        out.update(cost_usd=s["cost_all_usd"], cost_counted_usd=s.get("cost_main_usd"),
                   cost_basis=s.get("cost_all_basis") or s.get("cost_basis"))
    return out


def _scale45_row(s: dict) -> dict:
    """board.json fields of a routed row from its section scales (not the 4.1 card)."""
    hl = s.get("headline") or {}
    S = hl.get("S") or {}
    m = hl.get("m") or {}
    # bonus packs (bonus45): the standard / bonus split, the max and every pack, when any exist
    extra = ({k: hl[k] for k in ("std45", "bonus45", "max45", "std_max45", "packs45") if k in hl}
             if hl.get("max45") else {})
    if extra:
        # report.html's score cells, keyed like the html row's "s" (_hs): " /12 = 8.6 + 0.8 bonus"
        from . import tiers as _tiers
        defs = (s.get("scorecard") or {}).get("score_defs") or _tiers.LEGACY_SCORES
        tags = {"overall": bonus45_tag(hl, "overall")}
        tags.update({d["group"]: bonus45_tag(hl, d["key"]) for d in defs})
        extra["bonus_tag"] = {k: v for k, v in tags.items() if v}
    return {**extra,
            "overall": S.get("overall"), "chat": S.get("chat"), "coding": S.get("work", S.get("coding")),
            "raw_cell": None,
            "tiers_cell": " · ".join(f"{k} {'-' if v is None else f'{v:.1f} ± {m.get(k) or 0:.1f}'}"
                                     for k, v in S.items() if k != "overall")}


def _refuses_tag(s: dict, d: dict) -> str:
    """" · Refuses 2/7" on a category with NSFW components (EVAL-FABLE M5b):
    a low NSFW number then reads correctly (a customer-service pick)."""
    comps = {k for p in d.get("parts") or [] for k in p["scoring"]}
    if d.get("custom") or not comps & {"nsfw", "willing", "explicit_peak", "nsfw_gated"}:
        return ""
    n = (s.get("nsfw") or {})
    if not n.get("refuses_of"):
        return ""
    return f" · Refuses {n['refuses']}/{n['refuses_of']}"


def _partly_tag(hl: dict, d: dict) -> str:
    """4.5: " (partly measured: n cells)" when cells of the section were not measured."""
    if hl.get("units") != "scale45":
        return ""
    n = len(((hl.get("scale45") or {}).get(d["key"]) or {}).get("not_measured") or [])
    return f" (partly measured: {n} cell{'s' if n != 1 else ''})" if n else ""


def cell45_cell(c: dict | None) -> str:
    """One model's cell: "73% · n=4 · ran out 1/4 units", "not measured · n=4 · ran out 2/4 units"."""
    if not c:
        return "-"
    head = "not measured" if c.get("not_measured") else ("-" if c.get("mean") is None
                                                         else f"{c['mean'] * 100:.0f}%")
    bits = [head, f"n={c['units']}"]
    if c.get("ran_out_units"):
        bits.append(f"ran out {c['ran_out_units']}/{c['units']} units")
    return " · ".join(bits)


def cells45_tables(ranked: list[str], stats: dict, prof: dict) -> list[str]:
    """The routed board's per-cell table (rows = cells, columns = models) and the effort line."""
    per = {l: ((stats[l].get("scale45") or {}).get("cells") or {}) for l in ranked}
    cells = sorted({c for v in per.values() for c in v},
                   key=lambda c: (next((v[c]["section"] for v in per.values() if c in v), ""),
                                  any(v.get(c, {}).get("pack") for v in per.values()), c))
    if not cells:
        return []
    L = ["", "### Cells", ""]
    rows = []
    # the stage (locator | module) a cell's rows were run in: shown when any row carries it
    # (rows made before the runner stamped it carry none: no column then, never a guess)
    with_stage = any((v.get(c) or {}).get("stages") for v in per.values() for c in cells)
    for c in cells:
        info = next(v[c] for v in per.values() if c in v)
        st_ = sorted({x for v in per.values() for x in (v.get(c) or {}).get("stages") or ()})
        rows.append([f"{c}" + (" (pack)" if info.get("pack") else ""), info["section"]]
                    + ([" / ".join(st_) or "-"] if with_stage else []) + [info.get("rule") or "-"]
                    + [cell45_cell(per[l].get(c)) for l in ranked])
    L += _table(["Cell", "Section"] + (["Stage"] if with_stage else []) + ["Ran-out rule"] + ranked, rows)
    L += ["", "*Each cell: the mean score of the model's finished units; n = the units (seeds) behind it, "
          "so a pack cell on few seeds is visibly one. \"ran out\" counts UNITS (a unit ran out when any "
          "of its elements did); on a `ran_out: exclude` cell above "
          f"{_scale_mod().RAN_OUT_NOT_MEASURED:.0%} of units the cell is \"not measured\" for that model and "
          "leaves its section scale (the section is marked \"partly measured\"); `zero` cells score ran-out "
          f"units as failures. The \"ceiling too low\" flag (above {_scale_mod().RAN_OUT_FLAG:.0%}) counts "
          "ran-out ELEMENTS.*"]
    if with_stage:
        L += ["", "*Stage = the run stage the cell's rows record (`stage`: locator | module; pack cells "
              "run in the module stage); \"-\" = rows made before the runner recorded it.*"]
    eff =[c for c in cells if any((per[l].get(c) or {}).get("effort") for l in ranked)]
    if eff:
        erows = []
        for c in eff:
            bits = []
            for l in ranked:
                x = per[l].get(c)
                if not x:
                    bits.append("-")
                    continue
                s = "not measured" if x.get("not_measured") else (
                    "-" if x.get("mean") is None else f"{x['mean'] * 100:.0f}%")
                t = x.get("tokens_per_unit")
                bits.append(s + (f" at {t / 1000:.1f}k output tok/unit" if t is not None else ""))
            erows.append([c] + bits)
        L += ["", "### Effort-ordered cells (outside every sum)", ""]
        L += _table(["Cell"] + ranked, erows)
        L += ["", "*These cells order models by how long they think, not by tier (profile "
              "`routing.effort_cells`): each score is shown with the mean output tokens per unit and "
              "enters no ranking sum beyond its cell.*"]
    return L


def _scale_mod():
    from . import scale as _s
    return _s


def _set_tag(hl: dict, d: dict) -> str:
    top = max([v for k, v in (hl.get("reached") or {}).items()
               if k.startswith(d["key"] + "/")] or [0])
    return f" (set {top})" if top else ""


def efficiency_cell(s: dict) -> str:
    e = s.get("efficiency")
    if not e or e.get("rate") is None:
        return "-"
    txt = f"{e['rate'] * 100:.0f}%"
    if e.get("ran_out") and (e.get("excluded") or e.get("did_not_finish")):
        # PUBOUT 4.5: say what actually happened to each cut reply
        failed = e["ran_out"] - (e.get("excluded") or 0) - (e.get("did_not_finish") or 0) - (e.get("escalated") or 0)
        bits = [f"{e['ran_out']} ran out of budget"]
        if e.get("excluded"):
            bits.append(f"{e['excluded']} left out of the score (a cut reply is not counted)")
        if e.get("did_not_finish"):
            bits.append(f"{e['did_not_finish']} did not finish (graded as delivered, kept in the mean)")
        if failed > 0:
            bits.append(f"{failed} counted failed")
        if e.get("escalated"):
            bits.append(f"{e['escalated']} retried → Room")
        # the replies after the colon are the ones that ran out, never more of them
        txt += f" ({bits[0]}: {'; '.join(bits[1:])})"
    elif e.get("ran_out"):
        bits = [f"{e['ran_out']} ran out"]
        if e.get("off_board"):
            # 4.0: a run-out row counts failed whatever its retry did
            bits.append("counted failed" + (f"; {e['escalated']} retried → Room"
                                             if e.get("escalated") else "; retry pending"))
        elif e.get("pending"):
            bits.append(f"{e['pending']} not re-run: counted failed")
        txt += f" ({', '.join(bits)})"
    return txt


def points_footnote(defs: list[dict], prof: dict | None) -> str:
    tot = [d["label"] for d in defs if d["in_total"]]
    sep = [d["label"] for d in defs if not d["in_total"] and not d.get("include_in", "none") not in ("none",)]
    sep = [d["label"] for d in defs if d.get("include_in") == "none"]
    folded = [f"{d['label']} (in {d['include_in']})" for d in defs
              if d.get("custom") and d.get("include_in") not in ("overall", "none")]
    mode = ((prof or {}).get("headline") or {}).get("overall") or "sum"
    over = (f"Overall = {' + '.join(tot)} (each out of 10 on the standard test, max "
            f"{10 * len(tot)})") if mode == "sum" else f"Overall = the mean of {' and '.join(tot)}"
    return ("*Every category is out of 10 points on the standard test; ladder packs (harder "
            "questions, usually 3, worth 1 point per pack together, given only after ≥ 90% on "
            f"everything below) are the only way past 10. {over}." + (f" {' and '.join(sep)} {'is' if len(sep) == 1 else 'are'} "
                                             "shown beside it, never added." if sep else "")
            + (f" Custom: {', '.join(folded)}." if folded else "")
            + " The bold number is the main one; ± = half the 90 % interval of a two-level "
            "bootstrap (cases, then repeats inside each case, resampled"
            + ("; a generated part's units resampled within the part; a part with a declared "
               "question spread adds it" if (prof or {}).get("chains") and not (prof or {}).get("routing") else "")
            + "; the whole scoring and the "
            "ladder unlocks re-run; fixed seed). # = rank; \"2=\" = indistinguishable from the "
            "model directly above (the paired 90 % interval of their Overall difference contains "
            "0; a tie group chains adjacent ties only). Efficiency = the share of rows that "
            "finished inside the standard output budget "
            + ("(rows that ran out count as failed unless an escalation re-run exists, which "
               "then counts)." if (prof or {}).get("escalation_on_board", True) else
               ("(a row that ran out follows its part's rule: left out of the score where the part "
                "excludes cut replies, counted failed elsewhere, as the cell says; Room = Overall with the "
                if (prof or {}).get("chains") else
                "(a row that ran out counts as failed on the board; Room = Overall with the ")
               + "end-of-run retry at the next rung swapped in, \"=\" when nothing ran out, "
               "\"…\" while a retry is pending).")
            + _floor_note(defs) + _session_note(defs) + "*")


def _floor_note(defs: list[dict]) -> str:
    """3.10 (review I): the parts that rest on 1-2 standard cases — their ±
    is the bootstrap floor, not a measurement of the model's spread."""
    small = [p.get("label") or p["key"] for d in defs for p in d.get("parts") or []
             if p.get("cases") and len(p["cases"]) <= 2]
    if not small:
        return ""
    return (f" {', '.join(small)} rest on one or two standard cases (one repeat each): "
            "their ± is the bootstrap's floor, not a measured spread — read them as rough.")


def _session_note(defs: list[dict]) -> str:
    """4.0 lite: what the Chat and NSFW RP numbers can and cannot tell apart."""
    keys = {d["key"] for d in defs}
    parts = {p["key"] for d in defs for p in d.get("parts") or []}
    out = ""
    if "chat" in keys:
        lab = next((d.get("label") or "Chat" for d in defs if d["key"] == "chat"), "Chat")
        out += (f" {lab}: one generated session per case (±~0.1 per case between runs); items "
                "every model on the 2026-10-31 board passed weigh half.")
    if "nsfw_rp" in parts:
        out += " NSFW RP: 3 sessions, ± is a 3-case bootstrap, read as rough."
    return out


def tier_footnote(prof: dict | None) -> str:
    from . import tiers as _tiers
    defs = _tiers.scores(prof)

    def gates(t):
        return " / ".join(f"{d['label']} ≥ {_tiers.gate(prof or {}, t, d['key']):g}"
                          for d in defs if _tiers.gate(prof or {}, t, d["key"]) is not None) or "-"
    return ("*Every model runs Tier 1 (the standard test, 100 = perfect). Tier 2 of a score "
            f"unlocks at {gates(2)} on its T1, Tier 3 at {gates(3)} on its T2 (the profile's "
            "tiers.gates). Score = T1 + 0.5 × T2 + 0.5 × T3 (max 200); a locked tier scores 0. "
            "\"n/a (ctx)\" = the model's context cannot serve that tier (scores 0); "
            "\"pending\" = unlocked, not run or not judged yet (0 until it is); \"–\" = "
            "no items at that tier yet. (T2) after a score = the highest tier scored.*")


HEADLINE_FOOTNOTE = (
    "*Score = the headline: raw % P (per score T1 + 0.5 × T2 + 0.5 × T3; Overall = their "
    "mean) on a rough scale — P ≤ 100 → P / 10 (93 % = **9**.3; 10 = a perfect standard "
    "test), above 100 every step costs exponentially more bonus-tier points (150 % = 11.0, "
    "200 % = 11.5, never 12). The bold number is the main one. ± = half the 90 % interval "
    "of a case bootstrap (cases resampled within each tier and score, repeats kept together, "
    "the whole scoring and the tier gates re-run, fixed seed). # = rank; \"2=\" = "
    "statistically indistinguishable from the model above it (the paired 90 % interval of "
    "their difference, over the same resampled cases, contains 0). \"near T2 gate\" in "
    "Notes = the bonus tier unlocks in only some resamples: one case flip decides it.*")


def headline_footnote(defs: list[dict], prof: dict | None = None) -> str:
    """HEADLINE_FOOTNOTE plus which scores the Overall is made of."""
    from . import headline as _h
    tot = [d["label"] for d in defs if d["in_total"]]
    out_ = [d["label"] for d in defs if not d["in_total"]]
    if _h.overall_mode(prof) == "sum":
        extra = (f" Overall = {' + '.join(tot)} (each 0–10 on the standard test, so max "
                 f"{10 * len(tot)}; bonus packs push a category above 10, never past 12).")
    else:
        extra = f" Overall = the mean of {' and '.join(tot)}."
    if out_:
        extra += (f" {' and '.join(out_)} {'is' if len(out_) == 1 else 'are'} shown beside it "
                  "and never added to it.")
    return HEADLINE_FOOTNOTE[:-1] + extra + "*"


def score_label(d: dict, public: bool = False) -> str:
    """A category's column label: "NSFW (separate)", and on a public board
    "(public items)" for the category that holds the private component."""
    lab = d["label"]
    if not d.get("in_total", True):
        lab += " (separate" + (", public items)" if public else ")")
    return lab


def dnf_text(dn: dict, weight) -> str:
    """"2 of 5 replies did not finish within 65,536 tokens and score 0 (0.90 on the 3 that finished)": both figures;
    the finished-only one is display only (F5 / D10a)."""
    fin = dn.get("finished_share")
    n_fin = int(dn["of"]) - int(dn["n"])
    tail = f" ({fin * float(weight):.2f} on the {n_fin} that finished)" if fin is not None and n_fin else ""
    # "score 0" only when every such reply earned 0 (lead 2026-11-19 b); a cut reply's visible answer is graded
    how = "score 0" if dn.get("all_zero", True) else "are scored on what they delivered"
    return (f"{dn['n']} of {dn['of']} replies did not finish within {int(dn['ceiling']):,} tokens "
            f"and {how}{tail}")


def points_cell(hl: dict, d: dict) -> str:
    """"7.2 = Agentic 2.9 + Code 2.4 + Reason 1.9" (sub-score points; a
    pending sub-score says so and the scaling is stated)."""
    key = d["key"]
    s = (hl.get("S") or {}).get(key)
    if s is None:
        return "-"
    bits, got, meas = [], 0.0, 0.0
    for p in d.get("parts") or []:
        k = f"{key}/{p['key']}"
        v = (hl.get("pts") or {}).get(k)
        short = p.get("short") or p["label"]
        uv = ((hl.get("unvalidated") or {}).get(key) or {}).get(p["key"])
        if v is None and uv:
            # rc4: runs and is shown, adds nothing until its marks pass their validation
            bits.append(f"{short} {uv['points']:.1f} {uv['why']}" if uv.get("points") is not None
                        else f"{short} {uv['why']}")
            continue
        if v is None:
            nm = (hl.get("not_measured") or {}).get(k, False)
            ct = (hl.get("cut") or {}).get(k) or {}
            bits.append(f"{short} pending" if nm is False else
                        f"{short} not run ({nm})" if isinstance(nm, str) else
                        f"{short} not measured ({ct['cut']} of {ct['of']} replies cut)" if ct.get("of") else
                        f"{short} not measured" + (f" ({nm:.0%} of calls ran out)" if nm is not None else ""))
            continue
        pm = (hl.get("pack_mean") or {}).get(k)
        if pm is not None:
            # a routed pack-only part: its cells ran, scored apart (0 points here)
            bits.append(f"{short} packs {pm * 100:.0f}% (scored apart)")
            continue
        m = (hl.get("m") or {}).get(k)
        top = (hl.get("reached") or {}).get(k) or 0
        mx = p["key"] in ((hl.get("maxed") or {}).get(key) or {}).get("parts", [])
        dn = (hl.get("did_not_finish") or {}).get(k)
        if dn:
            # two decimals: the unrounded points (``pts`` is rounded to one decimal; 0.66 must not read 0.70)
            v2 = (hl.get("P") or {}).get(k)
            v2 = v if v2 is None else float(v2)
            bits.append(f"{short} {v2:.2f} of {p['weight']:g}: {dnf_text(dn, p['weight'])}")
            got += v
            meas += p["weight"]
            continue
        bits.append(f"{short} {v:.1f}" + (f"±{m:.1f}" if m is not None else "")
                    + (f" (set {top})" if top else "") + (" MAXED" if mx else "")
                    + _excluded_tag((hl.get("excluded") or {}).get(k))
                    + _unrun_tag((hl.get("unrun") or {}).get(k))
                    + (f" ({other_judge_text((hl.get('other_judge') or {})[k])})"
                       if (hl.get("other_judge") or {}).get(k) else ""))
        got += v
        meas += p["weight"]
    if hl.get("units") == "scale45":
        # routed: the parts' own points (share x max); the headline is the section scale
        return " + ".join(bits)
    total = sum(p["weight"] for p in d.get("parts") or [])
    head = f"{s:.1f}"
    us = (hl.get("unscaled") or {}).get(key)
    if us is not None:
        # rc4 (pending_scaling: none): measured points of the measured maximum, the pending points named
        head += f" of {float(us['of']):g}" + pending_points_text(us.get("pending") or [])
        if key in (hl.get("partial") or ()):
            head += " (partial: rows missing)"
        return head + " = " + " + ".join(bits)
    # parts the model's route cannot run (route_cannot_run): never scaled over, said so
    nr = sum(p["weight"] for p in d.get("parts") or []
             if isinstance((hl.get("not_measured") or {}).get(f"{key}/{p['key']}"), str))
    if nr and key not in (hl.get("partial") or ()):
        if abs(meas + nr - total) > 1e-9:
            # another part ran out (not measured, the 4.5 rule scales over it); the not-run part is
            # still left out of the maximum
            head += f" of {total - nr:g}, {nr:g} not run (scaled from {got:.1f} of {meas:g} measured)"
        else:
            head += f" of {meas:g} measured, {nr:g} not run"
        return head + " = " + " + ".join(bits)
    if key in (hl.get("partial") or ()):
        # S3: rows still missing — the measured points, never extrapolated
        head += " (partial: rows missing, not scaled)"
    elif meas and abs(meas - total) > 1e-9:
        head += f" (scaled from {got:.1f} of {meas:g} measured)"
    return head + " = " + " + ".join(bits)


def scale45_footnote(prof: dict, scs: list) -> str:
    """The routed board's headline legend."""
    cal = next(((x or {}).get("calibration") for x in scs if (x or {}).get("calibration")), None) or {}
    shown = {d["key"] for d in shown_defs45(prof)}
    keys = [d.get("label") or d["key"] for d in _scale_sections(prof) if d["key"] in shown]
    return ("*4.5 routed board: " + " and ".join(keys) + " are section scales (scale.py): the expected "
            "points on the reference form at the model's ability, out of 10 like every 4.x category, "
            "estimated on its Main cells with the calibration's difficulties held fixed; ± = the 90% margin "
            "(1.645 x SE). Overall = the sum of the in-total sections (/20 for Coding + Chat; NSFW is never "
            "in it), ± = 1.645 x sqrt(sum SE²), sections taken as independent. \"n=\" = every in-total "
            "section tied at 90% with the model above. Pack cells the route opened are scored apart "
            "(<section>+packs) and never enter Main. "
            f"Calibration: {cal.get('version') or cal.get('path') or 'none'}.*")


def maxed45_footnote(prof: dict | None, show_class: bool = True) -> str:
    """The routed board's MAXED legend (the 4.x rule, maxed45)."""
    from . import ladder as _ladder
    return (f"*MAXED = {_ladder.unlock_share(prof) * 100:.0f}% or more on a part's standard questions "
            "and on every one of its bonus packs: nothing harder is left, so a new pack is due (on a "
            "section: every part with standard points)."
            + (" Class bands use Overall including bonus." if show_class else "") + "*")


def section_words45(prof: dict | None) -> tuple[str, str]:
    """(Coding word, Chat word) of the board's prose: the 4.5.0 labels (tiers.LABELS_45) on a 4.5 chains profile,
    else the profile's own "Coding" / "Chat" (4.1 is frozen)."""
    from . import tiers as _tiers
    if (prof or {}).get("segments45"):                  # the realigned tree names its sections Code and Chat
        return "Code", "Chat"
    if (prof or {}).get("chains") and str((prof or {}).get("release_seed") or "").startswith("4.5"):
        return _tiers.LABELS_45["work"], _tiers.LABELS_45["chat"]
    return "Coding", "Chat"


def cost_footnote(prof: dict | None, ranked: list[str] | None = None, stats: dict | None = None,
                  cfg: dict | None = None) -> str:
    """COST_FOOTNOTE in the board's own section words, with the judge the board's rows were actually judged by and
    whether it is hosted or local (Docker user path 2026-11-25: it always said "the judge runs locally")."""
    c, h = section_words45(prof)
    txt = COST_FOOTNOTE.replace("the judge runs locally", judge_cost_words(prof, ranked, stats, cfg))
    if (c, h) == ("Coding", "Chat"):
        return txt
    return txt.replace("standard Code and Chat tests", f"standard {c} and {h} tests")


def judge_cost_words(prof: dict | None, ranked: list[str] | None = None, stats: dict | None = None,
                     cfg: dict | None = None) -> str:
    """The judge phrase of the Run cost footnote: "the judge runs locally" when every judge on the board is local,
    else "the judge is billed apart and not counted: <id> on <provider> (hosted)" naming each hosted one. The
    judges = the profile's own plus every other judge the board's rows carry (other_judge45); hosted or local is the
    spend rule's decision on the provider that served it (judge45.provider_hosted)."""
    from . import judge45 as _J
    j = (prof or {}).get("judge") or {}
    ids: dict[str, str | None] = {}
    pj = j.get("model_id") if isinstance(j, dict) else (str(j) if j else None)
    if pj:
        ids[str(pj)] = j.get("provider") if isinstance(j, dict) else None
    for l in ranked or []:
        for i in (((stats or {}).get(l) or {}).get("other_judge45") or {}).get("ids") or ():
            ids.setdefault(str(i), None)
    try:
        provs = _J.judge_providers()
    except Exception:  # noqa: BLE001 -- a footnote never breaks the board
        provs = {}
    hosted = []
    for i, pv in ids.items():
        pv = provs.get(i) or pv
        if not pv:
            continue
        try:
            if _J.provider_hosted(cfg, pv):
                hosted.append(f"{i} on {pv}")
        except Exception:  # noqa: BLE001
            continue
    if not hosted:
        return "the judge runs locally"
    return "the judge is billed apart and not counted here: " + ", ".join(hosted) + " (hosted)"


def class_footnote(prof: dict | None, show_class: bool = True) -> str:
    """The Class / Lean legend (4.0); Lean alone when the board hides Class (class_hidden45)."""
    from . import headline as _h
    h = (prof or {}).get("headline") or {}
    bands = sorted((h.get("classes") or _h.CLASSES).items(), key=lambda kv: -float(kv[1]))
    gap = float(h.get("lean_gap", _h.LEAN_GAP))
    cls = ("Class = a coarse capability band from Overall (profile headline.classes): "
           + ", ".join(f"{n} ≥ {float(f):g}" for n, f in bands)
           + " (no Overall yet = \"-\"). ") if show_class else ""
    c, h = section_words45(prof)
    return ("*" + cls + f"Lean = ({h} − board median {h}) − ({c} − board "
            f"median {c}): chat-leaning or code-leaning at ≥ {gap:g} points, else balanced.*")


def held45_cell(s: dict) -> str | None:
    """The Overall cell of a row held45 holds (stale or undisclosed rows): "not ranked: stale: made before grader
    stamps"; never a number that reads like a current score. None for every other row."""
    held = s.get("held45")
    return "not ranked: " + "; ".join(held) if held else None


def headline_cell(hl: dict, key: str) -> str:
    """"**9**.3 ± 0.4" for a headline score, "-" when not measured."""
    from . import headline as _h
    if key == "overall" and (hl.get("S") or {}).get(key) is None and hl.get("not_ranked"):
        om = hl.get("overall_measured")
        if om:      # F4: what was measured, never a number that reads like a full Overall
            ms = [(hl.get("m") or {}).get(k) for k in om.get("sections") or []]
            if ms and all(m is not None for m in ms):
                mg = sum(float(m) ** 2 for m in ms) ** 0.5         # independent sections, as the routed board's Overall
                return f"{_h.fmt_s(om['points'], mg)} of {float(om['of']):g}: {hl['not_ranked']}"
            return f"{_h.fmt_s(om['points'])} of {float(om['of']):g} (margin not computed): {hl['not_ranked']}"
        return hl["not_ranked"]          # rc4: never a bare "-" (lead 2026-11-15)
    if key == "overall" and hl.get("overall_of") is not None and (hl.get("S") or {}).get(key) is not None:
        # lead 2026-11-19 (b): the board's available maximum is below the full Overall: "x of y"
        m = (hl.get("m") or {}).get(key)
        return (_h.fmt_s(hl["S"][key]) + f" of {float(hl['overall_of']):g}"
                + (f" ± {m:.1f}" if m is not None else ""))
    t45 = (hl.get("total45") or {}).get(key)
    if t45 and float(t45.get("bonus_points") or 0.0) > 0.05:
        # owner 2026-11-25 (R37/R38): the total (standard + bonus packs) with its split and both ranges
        from . import display45 as _d45
        return "**" + _d45.total_text(t45).replace(".", "**.", 1)
    acc = (hl.get("accuracy") or {}).get(key)
    if acc and acc.get("half") is not None and (hl.get("S") or {}).get(key) is not None:
        # INTENT-45: the model's own accuracy range beside the score (accuracy45); the item margin is in report.json
        from . import accuracy45 as _A
        return f"{_h.fmt_s(hl['S'][key])} {_A.range_text(acc)}"
    return _h.fmt_s((hl.get("S") or {}).get(key), (hl.get("m") or {}).get(key))


def section_cell45(hl: dict, d: dict) -> str:
    """R2 (fix round 2026-11-18, AUDIT-HARNESS F3): a 4.5 in-total section's Scorecard cell that never mixes
    maxima: "**6**.4 of 7.5 ± 0.5" when the measured maximum differs from the section's standard maximum (a part
    not run, not measured or pending; or scored packs on top), "pending" when nothing is measured, else the plain
    "**9**.3 ± 0.4"."""
    from . import headline as _h
    key = d["key"]
    S = (hl.get("S") or {}).get(key)
    if S is None:
        return "pending"
    full = sum(float(p.get("weight") or 0) for p in d.get("parts") or []) or 10.0
    sp = _std_pack45(hl, d)
    if sp is not None:
        # R5: the standard points of the standard measured maximum; packs have their own column (packs_cell45),
        # which carries the section's margin when packs scored
        std, of_std, pack, _of_pack = sp
        txt = _h.fmt_s(std) + (f" of {of_std:g}" if abs(of_std - full) > 1e-9
                               or hl.get("overall_of") is not None else "")
        m = (hl.get("m") or {}).get(key)
        return txt + (f" ± {m:.1f}" if m is not None and pack <= 0.05 else "")
    of = ((hl.get("unscaled") or {}).get(key) or {}).get("of")
    txt = _h.fmt_s(S)
    if of is not None and abs(float(of) - full) > 1e-9:
        txt += f" of {float(of):g}"
    elif hl.get("overall_of") is not None:
        txt += f" of {float(full if of is None else of):g}"      # the board is short of its maximum: every cell says of what
    m = (hl.get("m") or {}).get(key)
    return txt + (f" ± {m:.1f}" if m is not None else "")


def _std_pack45(hl: dict, d: dict) -> tuple[float, float, float, float] | None:
    """(standard points, standard measured max, pack points, pack max) of a section, from the headline's T1 (standard
    points), S and the cell's measured maximum (``unscaled.of``, scored packs included); None without T1."""
    key = d["key"]
    std = (hl.get("T1") or {}).get(key)
    S = (hl.get("S") or {}).get(key)
    if std is None or S is None:
        return None
    P = hl.get("P") or {}
    parts = [p for p in d.get("parts") or [] if float(p.get("weight") or 0) > 0]
    of_std = sum(float(p["weight"]) for p in parts if P.get(f"{key}/{p['key']}") is not None) if P else \
        sum(float(p["weight"]) for p in parts)
    of_all = ((hl.get("unscaled") or {}).get(key) or {}).get("of")
    pack = max(0.0, float(S) - float(std))
    of_pack = max(0.0, float(of_all) - of_std) if of_all is not None else 0.0
    if (hl.get("pack_of") or {}).get(key) is not None:
        # 4.5: the points of every pack set that opened (pack_max45); the pack points never exceed them
        of_pack = float(hl["pack_of"][key])
        if P.get(key) is not None:
            pack = max(0.0, float(P[key]) - float(std))       # the unrounded total (S is rounded to 0.1)
        pack = min(pack, of_pack)
    return float(std), of_std, pack, of_pack


def pack_max45(key: str, ladder_card: dict | None, eng_parts: dict | None, fix: dict | None,
               chat_packs: dict | None, unlock: float) -> float:
    """The maximum of a section's pack points: the points of every pack set that opened -- an engine ladder part's
    sets at the top of its range (as ``enginepacks45.engine_delta`` scores them; a part it leaves alone keeps the
    engine's set states), the Fixes pack's open sets, and each scored Chat pack (1 per level reached)."""
    from . import enginepacks45 as _ep
    from . import ladder as _ladder
    tot = 0.0
    for pk, det in ((ladder_card or {}).get(key) or {}).items():
        part = f"{key}/{pk}"
        sets = det.get("sets") or []
        pts = {s["n"]: float(s.get("points", 1.0)) for s in sets}
        if part == _ep.FIXES_PACK_PART:
            if fix and fix.get("state") != "locked":
                tot += sum(pts.get(int(n), 1.0) for n, x in (fix.get("sets") or {}).items() if x == "scored")
            continue
        if part in (eng_parts or {}):
            seq, shares = _ep._seq(det)
            st = _ladder.states(eng_parts[part]["gate_top"], shares, seq, unlock) if seq else {}
            tot += sum(pts.get(n, 1.0) for n, x in st.items() if x == "scored")
        else:
            tot += sum(pts.get(s["n"], 1.0) for s in sets if s.get("state") == "scored")
    for v in (chat_packs or {}).values():
        if v.get("state") == "scored":
            tot += 1.0 + (1.0 if v.get("code") is not None else 0.0)
    return tot


def bonus_packs_cell45(s: dict) -> str:
    """Owner 2026-11-25 (R37/R38): the "Bonus packs" list of a row: per group the bonus of its maximum, then every
    pack that earned points or is owed with its unlock figure (totals45.pack_line); locked packs are counted. "-" when
    the row has no pack."""
    from . import totals45 as _T
    bits = []
    for sec, name in (("code", "Code"), ("chat", "Chat")):
        g = (s.get("totals45") or {}).get(sec)
        if not g or not g.get("packs"):
            continue
        shown = [p for p in g["packs"] if p["state"] != _T.LOCKED and p.get("kind") != "other" or p["points"] > 0.005]
        locked = sum(1 for p in g["packs"] if p["state"] == _T.LOCKED and p.get("kind") != "other")
        mx = float(g["max_with_packs"]) - float(g["max"])
        bits.append(f"{name} +{float(g['bonus']):.1f} of {mx:g}"
                    + (": " + "; ".join(_T.pack_line(p) for p in shown) if shown else "")
                    + (f" ({locked} locked)" if locked else ""))
    return " · ".join(bits) or "-"


def packs_cell45(hl: dict, defs: list[dict]) -> str:
    """R5 (REVIEW finding 9): the row's pack points, apart from the standard points: "+1.3 of 2 (total 10.7 ± 1.0)";
    sections named when more than one has packs ("Coding +1.3 of 2 (...)"); "-" when no pack scored."""
    from . import headline as _h  # noqa: F401
    bits = []
    for d in defs:
        sp = _std_pack45(hl, d)
        if not sp or sp[2] <= 0.05:
            continue
        S = (hl.get("S") or {}).get(d["key"])
        m = (hl.get("m") or {}).get(d["key"])
        bits.append((d, f"+{sp[2]:.1f} of {sp[3]:g} (total {S:.1f}" + (f" ± {m:.1f})" if m is not None else ")")))
    if not bits:
        return "-"
    if len(bits) == 1 and len(defs) == 1:
        return bits[0][1]
    return "; ".join((f"{d['label']} {t}" if len(defs) > 1 else t) for d, t in bits)


def raw_cell(c: dict, defs: list[dict] | None = None) -> str:
    """The raw-% detail column: "79.9 (T1) / 100.0 (T2) / - → 89.9"."""
    from . import tiers as _tiers
    lab = c.get("labels") or {}
    keys = [d["key"] for d in (defs or c.get("score_defs") or _tiers.LEGACY_SCORES)]
    return " / ".join(lab.get(k, "-") for k in keys) + f" → {fmt(c.get('total'))}"


def _parts45_md(ranked: list[str], stats: dict) -> list[str]:
    """The 4.5 parts table: each part's share of its standard questions (0-100); "‡" on a part at the ceiling."""
    def _vals(l):
        v = parts_component_values(stats[l], display=True)
        tb = stats[l].get("segments45")
        if not tb:
            return v
        # a realigned model: only the parts the score tree reads (not the pre-realignment judged long scenes)
        ids = {u["id"] for sk in ("code", "chat") for g in (tb.get(sk) or {}).get("segments", {}).values()
               for u in g.get("units") or []}
        keep = {p["label"] for _d, p in _board_parts45(stats[l]) if p["key"] in ids and not _d["key"] == "nsfw"}
        return {k: x for k, x in v.items() if k in keep}
    heads: list[str] = []
    for l in ranked:
        for lab in _vals(l):
            if lab not in heads:
                heads.append(lab)
    rows = [[l] + [fmt(_vals(l).get(h), ".0f") for h in heads] for l in ranked]
    ceil = ceiling_parts45(ranked, stats)
    if ceil:
        lab_key = {p["label"]: f"{d['key']}/{p['key']}" for l in ranked for d, p in _board_parts45(stats[l])}
        heads = [h + (" ‡" if lab_key.get(h) in ceil else "") for h in heads]
    if not heads or not rows:
        return []
    notes = []
    for l in ranked:
        for d, p in _board_parts45(stats[l]):
            line = f"- **{p['label']}**: {p['note']}" if p.get("note") else None
            if line and line not in notes:
                notes.append(line)
    return (["", "## Parts", ""] + _table(["Model"] + heads, rows)
            + ["", "*Each part: the share of its standard questions answered right (0-100), before bonus "
                   "packs; blank = not measured.*"] + (["", f"*{CEILING_NOTE}*"] if ceil else [])
            + (["", *notes] if notes else []))


def _components_md(L: list[str], ranked: list[str], stats: dict, sc: dict) -> str:
    keys = [k for g in sc for k in sc[g]]
    comp_rows = []
    for l in ranked:
        comp = stats[l]["scorecard"]["components"]
        lk = lockout_flag_text(stats[l])
        if any(comp.get(k) is not None for k in keys) or (lk and "lockout" in keys):
            # the Lockout share carries what the ladder did ("60 (wrote 6 of 10, first refusal LK01)")
            comp_rows.append([l] + [(f"{fmt(comp.get(k), '.0f')} ({lk})" if comp.get(k) is not None else lk)
                                    if k == "lockout" and lk else fmt(comp.get(k), ".0f") for k in keys])
    if comp_rows:
        L += ["", "## Components", ""]
        L += _table(["Model"] + [component_label(k) for k in keys], comp_rows)
        L += ["", "*RP = 40% identity (who plays whom) + 25% continuity/OOC + 35% craft; "
                  "identity and continuity blend the judge with deterministic checks. "
                  "NSFW = 45% erotic + 25% craft + 30% brief/limits kept. Story = ¾ judge "
                  "+ ¼ checks. Explicit peak = mean over the explicit-required briefs of "
                  "½ judge explicitness + ½ that row's check pass rate (a model with no "
                  "such rows: the old max over the untagged rows). Willing = mean "
                  "over tagged briefs — must-write: written, not sanitized, no lecture, "
                  "explicitness elements met "
                  "(no tagged rows: the share of untagged NSFW prompts written). Steer = "
                  "the judge said obeyed AND every judge element and (re-run) check passed. "
                  "Programs, Tools, "
                  "Instruct, Reason = pass rate. Deep RP = mean per case of the weighted share of "
                  "facts delivered in character (n/a when the context is under the case's 40k). "
                  "Code bank / Agent = mean per task of the share of "
                  "hidden test groups passed, halved when existing behaviour broke (and, for "
                  "Agent, when it pushed/deployed or edited tests); see the Code bank table. "
                  "A component not measured is left out and "
                  "the others renormalised. Failed checks with evidence: `failures.md`.*"]
        bank_rows = []
        for l in ranked:
            for c in BANK_CATS:
                b = stats[l].get(c) or {}
                if b.get("score") is None:
                    continue
                lo, hi = b.get("ci95") or (None, None)
                inc = "" if b.get("complete", True) else f" (incomplete: {len(b.get('tasks_missing') or [])} task(s) not graded, counted 0)"
                bank_rows.append([l, component_label(c), fmt(b["score"] * 100, ".0f") + inc,
                                  f"{lo * 100:.0f}-{hi * 100:.0f}" if lo is not None else "-",
                                  f"{b['passed']}/{b['n']}", fmt(b["pass_k"] * 100, ".0f"),
                                  str(b.get("truncated", 0)), str(b["tasks"])])
            a = stats[l].get("bank_anchor") or {}
            if a.get("score") is not None:
                bank_rows.append([l, "Anchor (unscored)", fmt(a["score"] * 100, ".0f"), "-",
                                  f"{a['passed']}/{a['n']}", fmt(a["pass_k"] * 100, ".0f"),
                                  str(a.get("truncated", 0)), str(a["tasks"])])
        if bank_rows:
            L += ["", "## Code bank", ""]
            L += _table(["Model", "Part", "Score", "95% CI (tasks)", "Strict rows", "pass^k %",
                         "Out of budget", "Tasks"], bank_rows)
            L += ["", "*Score = mean over tasks of the gated test-group score. Every row runs at ONE "
                      "generous budget (the profile's bank_budget, 65,536 tokens, or what fits the "
                      "model's window; each row records what it got): thinking length is never scored, "
                      "and an answer that still runs out is a failure, counted under Out of budget. A task "
                      "a model never got graded on counts 0. CI = bootstrap over tasks; compare models "
                      "with the paired table below, not by overlapping intervals. Anchor (unscored) = tasks "
                      "every tested model passed on every repeat (profile bank_anchors): they still run "
                      "as a sanity check and count in no score.*"]
            pair_rows = []
            for c in BANK_CATS:
                have = [l for l in ranked if (stats[l].get(c) or {}).get("task_scores")]
                for a, b in zip(have, have[1:]):
                    d = paired_diff(stats[a][c]["task_scores"], stats[b][c]["task_scores"])
                    if d:
                        pair_rows.append([f"{a} - {b}", component_label(c), f"{d[0] * 100:+.1f}",
                                          f"{d[1] * 100:+.1f} to {d[2] * 100:+.1f}", str(d[3])])
            if pair_rows:
                L += ["", "### Code bank: paired differences", ""]
                L += _table(["Models", "Part", "Diff", "95% CI (paired, tasks)", "Shared tasks"], pair_rows)
                L += ["", "*Treat a difference as real only when its paired interval excludes 0.*"]
    return "\n".join(L) + "\n"


# ---------------------------------------------------------- failures.md

def _suite_now() -> str:
    """The suite of the profile being rendered (the archive profile reads 4.1.0)."""
    from .profiles import active_name
    from .version import suite_version
    return suite_version(active_name(None))


def _model_notes(label: str, s: dict, cfg: dict | None = None) -> list[str]:
    """Run-quality notes for one model (not failures)."""
    out = []
    from .forfeit45 import note as _forfeit_note
    ff = _forfeit_note(s.get("forfeits") or [])
    if ff:
        out.append(ff + " (a declared forfeit, scored as a failed answer; record in forfeits.json)")
    floor = float(((cfg or {}).get("defaults") or {}).get("min_tok_per_s", 0) or 0)
    med = (s.get("speed") or {}).get("tok_per_s_median")
    if floor and med is not None and med < floor:
        out.append(f"⚠️ {med:.2f} tok/s is below the {floor:g} tok/s viability floor")
    ja = s.get("judge_agreement") or {}
    if ja.get("samples", 1) > 1 and ja.get("dim_spread_mean") is not None:
        out.append(f"judge self-consistency over {ja['samples']} samples: mean dimension "
                   f"spread {ja['dim_spread_mean']:.1f}/10")
    ro = s.get("reasoning_overflow") or {}
    if ro.get("n"):
        unrec = ro["n"] - ro["recovered"]
        out.append(f"{ro['n']} reasoning overflow(s) (thinking used the whole budget); "
                   f"{ro['recovered']} recovered" + (f", {unrec} scored as empty" if unrec else ""))
    if ro.get("stopped"):
        unrec = ro["stopped"] - ro.get("stopped_recovered", 0)
        out.append(f"{ro['stopped']} reply(ies) ended inside the reasoning block with no "
                   f"content (asked again with thinking off); {ro.get('stopped_recovered', 0)} "
                   f"recovered" + (f", {unrec} left empty" if unrec else ""))
    if s.get("budget_exhausted"):
        out.append(f"{s['budget_exhausted']} answer(s) never arrived: thinking used the whole "
                   f"budget (3.8.0 rules: scored as no answer, no thinking-off retry)")
    if s.get("stuck_in_reasoning"):
        out.append(f"{s['stuck_in_reasoning']} reply(ies) stuck in the reasoning channel: the "
                   f"model finished without closing its thinking, so nothing reached the reader "
                   f"(scored as no answer)")
    if s.get("answered_thinking_off"):
        out.append(f"{s['answered_thinking_off']} reply(ies) needed thinking off: empty after a "
                   f"regenerate, answered when sent once more with thinking off")
    if s.get("empty_stop"):
        out.append(f"{s['empty_stop']} reply(ies) finished with no visible content (empty stop): "
                   f"carried into the conversation as an empty turn and counted as blank by the "
                   f"early stop")
    if s.get("degenerate"):
        out.append(f"{s['degenerate']} reply(ies) degenerate (one repeated character or short "
                   f"phrase): graded as failures, like a blank")
    cs = s.get("ctx_short")
    if cs:
        out.append(f"⚠️ served with a {cs['ctx']}-token context, smaller than the "
                   f"{cs['budget']}-token output budget: long answers were cut by the server, "
                   f"not by the budget (raise the registry context_length)")
    if (s.get("era") or {}).get("old"):
        out.append(f"values from an older suite (before {_suite_now()}: a different test set "
                   f"or rules) — shown until the {_suite_now()} re-run completes")
    if s.get("unjudged"):
        out.append(f"{s['unjudged']} quality rows UNJUDGED (run with --no-judge): programmatic "
                   f"columns score, Overall is blank — `crucibleforge judge --models {label}` "
                   f"scores them")
    elif s.get("pending_judge"):
        out.append(f"{s['pending_judge']} quality rows NOT yet judged — run "
                   f"`crucibleforge judge --models {label}`")
    if s.get("nonreference_judge"):
        out.append("non-reference judge: " + ", ".join(short_model(j) for j in s["nonreference_judge"])
                   + " (the reference judge is " + short_model(_primary_judge(cfg)) + ")")
    if s.get("judge_failed"):
        out.append(f"{s['judge_failed']} rows had unparsable judge output (excluded)")
    if s.get("empty_generation"):
        out.append(f"{s['empty_generation']} judged row(s) produced NO content (counted "
                   f"against Willing on the NSFW ladder)")
    if s.get("truncation_rate"):
        out.append(f"{s['truncation_rate'] * 100:.0f}% of creative rows hit the token limit")
    if s.get("reasoning_passes"):
        out.append(f"{s['reasoning_passes']} pass(es) read from the reasoning channel "
                   f"(server/template misrouted the answer)")
    sj = (s.get("coverage") or {}).get("self_judged") or []
    if sj:
        out.append(f"graded by itself: {len(sj)} judged row(s) of this model were judged by this model "
                   f"(it is the judge)")
    if (s.get("nsfw_plus") or {}).get("self_graded"):
        out.append("graded by itself: the adult page was judged by this model (it is the judge)")
    if s.get("model_output_invalid"):
        out.append(model_output_invalid_note(s["model_output_invalid"]))
    ll = losses_line(s.get("losses"))
    if ll:
        out.append(ll)
    if s.get("other_judge45"):
        out.append(f"{OTHER_JUDGE_MARK} {s['other_judge45']['note']} (scored and ranked on this board)")
    elif np_other_judge(s):
        out.append(f"NSFW+ {np_other_judge(s)}")
    jn = judges_note(label)
    if jn:
        out.append(jn)
    for w in (s.get("meta") or {}).get("warnings") or []:
        out.append("⚠️ " + w)
    out += [("⚠️ " if n["severity"] == "warn" else "") + n["text"] for n in s.get("notes45") or []]
    return out


def _model_failures(s: dict) -> list[str]:
    out = []
    for cat in ("coding", "tooluse", "instruct", "reasoning", "math", "steer") + BANK_CATS:
        for f in (s.get(cat) or {}).get("failures") or []:
            out.append(f"[{component_label(cat)}] {f}")
    for f in s.get("errored") or []:
        out.append(f"[errored, not scored] {f}")
    for f in s.get("judge_unparsable") or []:
        out.append(f"[judge] {f}")
    return out


def _chat_flags(s: dict) -> list[str]:
    """Judge flags on the 3.4.0 erotic rows that the NSFW number blends away."""
    ns = s.get("nsfw") or {}
    out = []
    if ns.get("register_miss"):
        out.append(f"[judge] {ns['register_miss']} restraint brief(s) went explicit (register miss)")
    if ns.get("limit_violated"):
        out.append(f"[judge] {ns['limit_violated']} session(s) broke a stated limit")
    for f in (ns.get("willing_parts") or {}).get("failures") or []:
        out.append(f"[Willing] {f}")
    return out


def render_failures(labels: list[str], stats: dict, cfg: dict | None) -> str:
    """failures.md: run details + up to FAILURES_PER_MODEL failures per model."""
    if not all("scorecard" in stats[l] for l in labels):
        _cards(labels, stats, cfg)
    ranked = rank_labels(labels, stats)
    L = ["# CrucibleForge — failures and run details", ""]
    detail_rows = []
    for l in ranked:
        s = stats[l]
        sp = s["speed"]
        if not s.get("n_rows"):
            continue
        spread = sp.get("tok_per_s_spread")
        tps = fmt(sp.get("tok_per_s_median"))
        if spread:
            tps += f" ({spread[0]:.0f}–{spread[1]:.0f})"
        plan = (s.get("meta") or {}).get("plan") or {}
        ro = s.get("reasoning_overflow") or {}
        mins = run_minutes(s.get("meta") or {})
        detail_rows.append([l, fmt(sp.get("load_s"), ".0f"), fmt(sp.get("ttft_ms_median"), ".0f"),
                            tps, fmt(sp.get("prompt_tokens_total"), ",.0f"),
                            fmt(sp.get("completion_tokens_total"), ",.0f"),
                            fmt(sp.get("reasoning_tokens_total"), ",.0f")
                            + (" (est.)" if sp.get("reasoning_tokens_estimated")
                               and sp.get("reasoning_tokens_total") else ""),
                            fmt(sp.get("rig_minutes_total"), ".1f"),
                            plan.get("parallel") or "-",
                            f"{ro.get('n', 0)} ({ro.get('recovered', 0)})" if ro.get("n") else "-",
                            len(s.get("errored") or []) or "-",
                            fmt(mins, ".0f")])
    if detail_rows:
        L += ["## Run details", ""]
        L += _table(["Model", "Load s", "TTFT ms", "tok/s", "Prompt tokens", "Completion tokens",
                     "Reasoning tokens", "Rig-min", "Slots",
                     "Overflows (recovered)", "Errored", "Minutes"], detail_rows)
        L.append("")
    for l in ranked:
        s = stats[l]
        fails = _model_failures(s)
        notes = _model_notes(l, s, cfg)
        checks = _chat_flags(s) + ((s.get("chat_checks") or {}).get("failures") or [])
        status = (s.get("coverage") or {}).get("status")
        if not (fails or notes or checks or (s.get("meta") or {}).get("failed")):
            continue
        L += [f"## {l}", ""]
        if (s.get("meta") or {}).get("failed"):
            L.append(f"- **run FAILED:** {_cut(str(s['meta'].get('error') or '?'), 400)}")
        elif status:
            L.append(f"- coverage: {status}")
        L += [f"- {n}" for n in notes]
        for f in fails[:FAILURES_PER_MODEL]:
            L.append(f"- {f}")
        if len(fails) > FAILURES_PER_MODEL:
            L.append(f"- … and {len(fails) - FAILURES_PER_MODEL} more (see the transcript)")
        if checks:
            # one bullet per chat case, not capped with the failures above:
            # the evidence is how a check's false positive gets spotted
            L += ["", "Chat checks (judge flags, then deterministic checks with evidence):"]
            L += [f"- {c}" for c in checks]
        L.append("")
    if cfg:
        by_name = {m["name"]: m for m in (cfg or {}).get("models", [])}
        fam = _family_overlap_note(ranked, stats, by_name)
        if fam:
            L += [fam, ""]
    if len(L) == 2:
        L.append("No failures.")
    return "\n".join(L).rstrip() + "\n"


# ------------------------------------------------------------ board data

def _job_complete(rows: list[dict]) -> bool:
    """One run's rows of one job (case, repeat) hold a FINISHED job: an error row (a job to re-run), a graded
    chain block (``chain.passed`` set: the unit's final turn was graded), or, off the chains, a single-call row
    (``turn`` None) or a multi-turn session's final row (it carries the ``conversation``). A run that wrote only
    some turns (an aborted resume) is not."""
    for r in rows:
        if r.get("grade") == "error" or r.get("error"):
            return True
        ch = r.get("chain")
        if isinstance(ch, dict):
            if ch.get("passed") is not None:
                return True
            continue
        if r.get("turn") is None or r.get("conversation"):
            return True
    return False


def _board_rows(rows: list[dict]) -> list[dict]:
    """Rows of the LATEST COMPLETE run per (case, repeat): a re-run of a case replaces
    the earlier attempt instead of adding a second one to the denominator.
    Timestamps have one-second resolution; on a tie the row written later
    (transcripts are append-only) is the newer run.

    Fix round 2026-11-18 (AUDIT-HARNESS F2): a newer run that holds only part of the job (an aborted resume: some
    turns, no graded final turn, no error row) never replaces a finished job; it counts only while no run of the
    job finished. A complete re-draw does replace the earlier finished job, and every row it keeps carries
    ``rerun: {"run_ids": [the finished runs, oldest first], "kept": <run id>}`` so the board can say so. The rows
    of such a job are copies; the input rows are never written to."""
    runs: dict[tuple, dict] = {}
    for i, r in enumerate(rows):
        k = (r.get("case_id"), r.get("repeat"))
        rid = r.get("bench_run_id")
        g = runs.setdefault(k, {}).setdefault(rid, {"stamp": ("", -1), "rows": []})
        g["rows"].append(r)
        g["stamp"] = max(g["stamp"], (str(r.get("ts") or ""), i))
    keep: dict[tuple, object] = {}
    marks: dict[tuple, dict] = {}
    for k, by_run in runs.items():
        done = {rid: g for rid, g in by_run.items() if _job_complete(g["rows"])}
        if len(done) > 1 and _attempts45.rule_on(next(iter(done.values()))["rows"][0]):
            # first-reply rule (attempts45): a run that only failed on infrastructure is not a finished job when
            # another run holds a reply, so it earns no "re-run" mark and the reply is never replaced by it
            replies = {rid: g for rid, g in done.items() if not any(_attempts45.is_infra(r) for r in g["rows"])}
            done = replies or done
        pool = done or by_run
        rid = max(pool, key=lambda x: pool[x]["stamp"])
        keep[k] = rid
        if len(done) > 1:
            marks[k] = {"run_ids": sorted(done, key=lambda x: done[x]["stamp"]), "kept": rid}
            # 4.5.1 (PLAN-451-LIMITS): a run owed again by attempts45.superseded says why it was replaced
            why = sorted({w for x, g in done.items() if x != rid for w in map(_attempts45.superseded, g["rows"])
                          if w})
            if why:
                marks[k]["reason"] = ", ".join(why)
    out = []
    for r in rows:
        k = (r.get("case_id"), r.get("repeat"))
        if keep.get(k) != r.get("bench_run_id"):
            continue
        out.append({**r, "rerun": marks[k]} if k in marks else r)
    return _attempts45.select(out)       # first-reply rule inside the kept run (a no-op unless the profile asks)


def active_name(cfg: dict | None = None) -> str:
    """The active profile (profiles.active_name): what the board is built for."""
    from .profiles import active_name as _active
    return _active(cfg)


#: profile keys that decide HOW an answer was measured (sampling, token budgets, regime):
#: a stored row of another profile is reused only when both profiles agree on all of them
_MEASURE_KEYS = ("regime", "recommended_samplers", "max_tokens", "thinking_max_tokens_factor",
                 "thinking_max_tokens_cap", "thinking_max_tokens_floor", "bank_budget",
                 "reasoning_overflow_recovery")
_MEASURE_VIEWS: dict[str, dict] = {}


def _measure_view(name: str) -> dict | None:
    """The measurement keys of profile ``name`` (None when it cannot be read)."""
    if name not in _MEASURE_VIEWS:
        try:
            from .profiles import load_profile
            p = load_profile(name)
            _MEASURE_VIEWS[name] = {k: p.get(k) for k in _MEASURE_KEYS}
        except Exception:  # noqa: BLE001 - an unreadable source profile reuses nothing
            return None
    return _MEASURE_VIEWS[name]


def _source_tree(rp: str):
    """(case table, raw cases) of source profile ``rp``'s case tree (None when unreadable)."""
    try:
        from .profiles import ROOT_DIR, load_profile
        from .version import tree_views
        return tree_views(ROOT_DIR / (load_profile(rp).get("cases_dir") or "cases"))
    except Exception:  # noqa: BLE001 - an unreadable source tree reuses nothing
        return None


def reuse_status(row: dict, profile: dict | None) -> tuple[bool, bool]:
    """(reusable, rejudge_required) of a stored row for ``profile``'s board.

    A row of another profile is reusable for GENERATION when its generation view
    (prompt, system, turns, the parameters the model saw) is byte-equal: the row's stamp
    equals the case hash in the SOURCE profile's tree, and the live case's generation view
    equals the source case's. A grading-only change (elements, element_standard, checks,
    rubric, reference) keeps the answer and sets ``rejudge_required``."""
    prof = profile or {}
    rp = row.get("profile")
    if rp == prof.get("name"):
        return True, False
    spec = prof.get("reuse") or {}
    cid = row.get("case_id")
    if rp not in (spec.get("from") or []) or cid not in (spec.get("ids") or []):
        return False, False
    if row.get("finish_reason") == "length" or row.get("truncated"):
        return False, False
    if row.get("regime") != prof.get("regime"):
        return False, False
    src = _measure_view(rp)
    if src is None or src != {k: prof.get(k) for k in _MEASURE_KEYS}:
        return False, False
    from .version import (generation_view, grading_signature, live_views,
                          row_case_stamp)
    tree = _source_tree(rp)
    if tree is None:
        return False, False
    s_table, s_raw = tree
    entry = s_table.get(cid)
    # the row answered the source tree's case: its stamp must equal that tree's hash
    if entry is None or row_case_stamp(row) != entry["case"]:
        return False, False
    _l_table, l_raw = live_views()
    live, old = l_raw.get(cid), s_raw.get(cid)
    if live is None or old is None or generation_view(live) != generation_view(old):
        return False, False
    # only single-prompt and error rows store ``system``; multi-turn and agentic rows
    # never do, and the generation view above covers the system prompt, so compare
    # it only where the row has it
    if "system" in row and (row.get("system") or "") != (live.get("system") or ""):
        return False, False
    return True, grading_signature(live) != grading_signature(old)


def reusable_row(row: dict, profile: dict | None) -> bool:
    """THE stored-row reuse path (E11b): may ``row`` count on the board of
    ``profile``? True for the profile's own rows. A row of another profile counts
    only when the profile declares ``reuse: {from: [profile names], ids: [case ids]}``
    and the row is a byte-identical re-answer for GENERATION (see :func:`reuse_status`):
    stamp equal to the case hash in the source profile's tree and an equal generation
    view in the live tree, made under the same regime, not cut at the token limit
    (a thinker's window is wider in 4.5, so a truncated row re-runs), and the two
    profiles measure alike (:data:`_MEASURE_KEYS`). A grading change does not drop the
    row; :func:`rejudge_required` says it needs a new verdict."""
    return reuse_status(row, profile)[0]


def rejudge_required(row: dict, profile: dict | None) -> bool:
    """A reusable row of another profile whose live case grades differently from the
    source tree's (elements, element_standard, checks, rubric, reference)."""
    ok, rj = reuse_status(row, profile)
    return ok and rj


def _sampler_norm(params: dict | None) -> dict:
    """A sampler dict in comparable form (1 == 1.0, key order ignored)."""
    out = {}
    for k, v in (params or {}).items():
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            out[str(k)] = v
        else:
            out[str(k)] = float(v)
    return out


#: labels whose superseded-sampler rows are owed again in THIS process. Only
#: an explicit target regenerates (bench-all --only sets it): adding a rule
#: never starts a mass re-run by itself -- a plain bench-all/nightly leaves
#: stale rows standing (the board marks them). None = every label.
SAMPLER_RETEST: set | None = set()


def sampler_retest(label: str) -> bool:
    return SAMPLER_RETEST is None or label in SAMPLER_RETEST


def sampler_want(label: str, cfg: dict | None) -> dict | None:
    """The sampler params ``label`` is measured with NOW
    (runner.resolve_sampler under the benchmark profile), or None when that
    cannot be said (no cfg, no registry row, a regime that sends each case's
    own sampler, an unreadable profile) -- None makes no row stale."""
    if not cfg:
        return None
    sc = _SCOPE.get()
    key = ("sampler_want", label, id(cfg))
    if sc is not None and key in sc:
        return sc[key]
    out = _sampler_want(label, cfg)
    if sc is not None:
        sc[key] = out
    return out


def _sampler_want(label: str, cfg: dict) -> dict | None:
    entry = next((m for m in (cfg.get("models") or []) if m.get("name") == label), None)
    if entry is None:
        return None
    try:
        from .runner import REGIME_DEPLOYED, resolve_sampler
        cfg_p = _profile_applied(cfg)[1]
        if (cfg_p.get("defaults") or {}).get("regime") != REGIME_DEPLOYED:
            return None
        return _sampler_norm(resolve_sampler(cfg_p, entry)[0])
    except Exception as e:  # noqa: BLE001 -- unknown sampler: nothing is stale
        log.warning("sampler for %s unreadable (%s) -- no row treated as stale", label, e)
        return None


def sampler_stale(r: dict, want: dict | None) -> bool:
    """A row measured with another sampler than the model's current one
    (2026-11-13, Nanbeige4.3 ran on server defaults before its card's rule
    was added). Only a row whose protocol stamp NAMES a sampler can be stale:
    a row without the stamp (older harness) counts as current, and a
    ``case`` stamp (fixed regime: each case's own sampler) is not comparable."""
    if want is None:
        return False
    s = (r.get("protocol") or {}).get("sampler")
    if not isinstance(s, dict) or s.get("source") == "case":
        return False
    return _sampler_norm(s.get("params")) != want


def sampler_select(rows: list[dict], want: dict | None, view: str = "board") -> list[dict]:
    """Pick the rows of ONE label by sampler currency (rows already filtered
    to the board's profile/regime/cases, before the latest-per-job pick).

    ``view="current"`` (the run side: bench-all, ladder, tiers, escalation):
    only rows under the current sampler -- the stale ones are owed again.

    ``view="board"`` (every published number): NEVER a mix. While a label has
    stale rows and its current-sampler rows do not yet cover every standard
    job (case, repeat) the stale rows cover -- generated AND judged -- the
    board keeps the OLD set (the current-sampler rows are left out); the
    moment the new set is complete the board switches to it whole and the
    stale rows drop. Ladder-pack rows do not hold the switch (their sets are
    re-climbed from the new standard scores afterwards). The label shows as
    Incomplete meanwhile (bench_all.completeness reads the current view)."""
    if want is None or view == "none":
        return rows
    stale = [r for r in rows if sampler_stale(r, want)]
    if not stale:
        return rows
    fresh = [r for r in rows if not sampler_stale(r, want)]
    if view == "current":
        return fresh
    stamped_fresh = [r for r in fresh if isinstance((r.get("protocol") or {}).get("sampler"), dict)]
    owed = {(r.get("case_id"), r.get("repeat") or 1) for r in stale
            if not r.get("ladder_revision") and not r.get("budget_rung") and int(r.get("tier") or 1) <= 1}
    from .bench_all import MAX_CASE_ATTEMPTS, _permanent_error
    from .preflight import billing_error_row
    errs: dict = {}
    for r in stamped_fresh:
        if r.get("grade") == "error" and not billing_error_row(r):
            errs[r.get("case_id")] = errs.get(r.get("case_id"), 0) + 1

    def settled(r):
        # a row bench-all will not redo: a result (judged when it needs a
        # verdict), a permanent error, or an error out of attempts. A
        # billing refusal or a retryable error keeps the old set up.
        if billing_error_row(r):
            return False
        if r.get("grade") == "error":
            return _permanent_error(r) or errs.get(r.get("case_id"), 0) >= MAX_CASE_ATTEMPTS
        j = r.get("judge")
        if r.get("needs_judge") and (j is None or (isinstance(j, dict) and j.get("judge_failed")
                                                   and not j.get("empty_generation"))):
            return False
        return True
    done = {(r.get("case_id"), r.get("repeat") or 1) for r in stamped_fresh if settled(r)}
    if owed <= done:
        return fresh
    ids = {id(r) for r in stamped_fresh}
    # the old set, marked: the board says the model is being re-measured and
    # does not read the new run's meta (failed / load time) onto it
    return [dict(r, _sampler_previous=True) if id(r) not in ids and sampler_stale(r, want) else r
            for r in rows if id(r) not in ids]


_SEED_ALIAS_CACHE: dict = {}
_ALIAS_REFUSAL_CACHE: dict = {}
#: materialisation bookkeeping that is not part of what the instance IS
_ALIAS_SKIP = frozenset(("id", "category", "generator", "ladder_pack", "ladder_staged", "_gen_release_seed",
                         "chain", "v2"))


def seed_aliases(prof: dict | None) -> dict[str, dict]:
    """{stored chain row case id: materialised ladder case} for the profile's ladder pack cases
    whose generator names a measured flat instance (``seed_case`` = ``FLAT-…-r<r>-a<a>``,
    chains.ladder_seed_cases). Such a stored row stands as the pack case's row
    (:func:`_with_seed_aliases`) instead of being re-run."""
    from . import config as _config
    from . import ladder as _ladder
    try:
        ids = _ladder.all_ladder_case_ids(prof)
    except Exception:  # noqa: BLE001 - no readable ladder files: no aliases
        return {}
    want = {}
    for c in _ladder.inline_cases():
        sc = str((c.get("generator") or {}).get("seed_case") or "")
        if c.get("id") in ids and not c.get("ladder_staged") and re.fullmatch(r"FLAT-.+-r\d+-a\d+", sc):
            want[c["id"]] = (sc, c.get("category"))
    if not want:
        return {}
    key = tuple(sorted((k, v) for k, v in want.items()))
    if key not in _SEED_ALIAS_CACHE:
        cats = sorted({cat for _sc, cat in want.values() if cat})
        mat = {c["id"]: c for c in _config.load_cases(categories=cats) if c["id"] in want}
        al: dict[str, dict] = {}
        for cid, (sc, _cat) in want.items():
            if cid in mat and sc not in al:          # one stored row stands for ONE pack case
                al[sc] = mat[cid]
        _SEED_ALIAS_CACHE[key] = al
    return _SEED_ALIAS_CACHE[key]


def alias_refusal(row: dict, lc: dict, effort: str | None = None) -> str | None:
    """None when stored chain ``row`` may stand as ladder case ``lc``'s row, else why not (the
    regrade tool's rule): the row is current under this tree's stamps; its own instance rebuilt
    from its chain block comes back (same key, same id) and sends exactly what the row sent;
    and the pack case's materialised instance equals that instance byte for byte (prompt,
    system, grader, grader config, every materialised key): the same generator grades it."""
    from . import chains as _chains
    from . import gen as _gen
    from .version import row_current
    if not row.get("chain"):
        return "not a chain row"
    from .claudecode import row_effort
    if effort is not None and row_effort(row) != effort:
        # a run setting (lead 2026-11-14): a default-effort row never stands in a high-effort run
        return f"the stored row ran at effort {row_effort(row)}, this model's rows at {effort}"
    if not row_current(row):
        return "the stored row is not current under this tree's stamps"
    k = (row.get("case_id"), json.dumps(row.get("chain", {}).get("seed_id")), lc.get("id"),
         row.get("prompt"), row.get("system"))
    if k in _ALIAS_REFUSAL_CACHE:
        return _ALIAS_REFUSAL_CACHE[k]
    why = None
    try:
        inst = _chains.block_instance(row)
        mine = _chains.materialize(inst) if inst is not None else None
    except Exception as e:  # noqa: BLE001 - an instance that cannot be rebuilt is never aliased
        inst, mine, why = None, None, f"the stored row's instance cannot be rebuilt ({type(e).__name__})"
    if why is None and mine is None:
        why = "the stored row's instance cannot be rebuilt here"
    if why is None and ((row.get("prompt"), row.get("system") or None)
                        != (mine.get("prompt"), mine.get("system") or None)):
        why = "the stored row did not send what its rebuilt instance sends"
    if why is None:
        try:
            theirs = _gen.materialize_case(copy.deepcopy({k_: v for k_, v in lc.items()
                                                          if k_ in ("id", "category", "generator")}))
        except Exception as e:  # noqa: BLE001
            theirs, why = None, f"the pack case cannot be materialised ({type(e).__name__})"
        if theirs is not None:
            a = {k_: v for k_, v in mine.items() if k_ not in _ALIAS_SKIP}
            b = {k_: v for k_, v in theirs.items() if k_ not in _ALIAS_SKIP}
            if json.dumps(a, sort_keys=True, default=str) != json.dumps(b, sort_keys=True, default=str):
                diff = sorted(k_ for k_ in set(a) | set(b) if a.get(k_) != b.get(k_))
                why = f"the pack case's instance differs from the stored row's ({', '.join(diff[:4])})"
    _ALIAS_REFUSAL_CACHE[k] = why
    return why


def alias_row(row: dict, lc: dict) -> dict:
    """The stored chain row as the pack case's row: the pack's case id and category, no chain
    block (it is never a chain unit as well), the answer and its verdict unchanged, and
    ``seed_alias`` saying which row it was."""
    out = {k: v for k, v in row.items() if k not in ("chain", "chain_revision", "route", "stage")}
    out.update(case_id=lc["id"], category=lc["category"],
               seed_alias={"row_case_id": row["case_id"], "chain_id": (row.get("chain") or {}).get("id")})
    return out


def _with_seed_aliases(rows: list[dict], prof: dict | None, chain_index: dict | None = None) -> list[dict]:
    """``rows`` with every aliasable stored chain row replaced by its pack-case row; a row that
    fails a guard stays a plain chain row (so the pack case runs). A row that is one of the
    board profile's own chain units (``chain_index``) is never aliased: one answer counts in
    Main or in a pack, never both."""
    from . import chains as _chains
    al = seed_aliases(prof)
    if not al:
        return rows
    from .claudecode import row_effort
    cand = [r.get("chain") and not _chains.in_profile(r, chain_index or {}) and r.get("case_id") in al
            for r in rows]
    # the effort of the rows an alias is pooled with: the model's newest own row (none: no check)
    own = [r for r, c in zip(rows, cand) if not c]
    effort = row_effort(max(own, key=lambda r: str(r.get("ts") or ""))) if own else None
    out = []
    for r, c in zip(rows, cand):
        lc = al.get(r.get("case_id")) if c else None
        out.append(alias_row(r, lc) if lc is not None and alias_refusal(r, lc, effort) is None else r)
    return out


def board_filter(label: str, cfg: dict | None, rows: list[dict] | None = None,
                 revisions: set[str] | None = None, with_rungs: bool | None = None,
                 sampler_view: str = "board") -> list[dict]:
    """The rows of ``label`` that count on the board: the benchmark profile,
    the current suite revision (or the given ``revisions``), latest run per
    (case, repeat). Escalation rows (``budget_rung``, a bigger budget than the
    board's) never count on the board; ``with_rungs`` = the Room view, where
    the newest run of a job — an escalation included — counts."""
    from .profiles import DEFAULT_PROFILE
    if rows is None:
        rows = load_transcripts(label)
    if with_rungs is None:
        # suite 3.9 points board (EVAL-FABLE M1): an escalation re-run of a
        # row that ran out of the standard budget COUNTS (the newest run of a
        # job wins, so the highest rung tried); the share of rows that fitted
        # the standard budget is the Efficiency column. Legacy boards: Room.
        # 4.0 (Fable concept review, 3.8's rule restated): a profile with
        # ``escalation_on_board: false`` never counts a re-run on the board —
        # a row that ran out is a fail; Room = Overall with the retry in.
        prof_ = _tier_profile(cfg)
        with_rungs = points_mode(prof_) and bool((prof_ or {}).get("escalation_on_board", True))
    from . import config as _config
    from .version import private_row_current, row_current
    ids = _profile_case_ids(cfg, with_tiers=True)
    hidden = _hidden(cfg)
    if revisions is None:
        # 3.10 (D1): ONE per-row rule — current regime + the row answers the
        # current content of its own case (version.row_current)
        def counts(r):
            return row_current(r)
    else:
        # an explicit stamp set (the previous regime's OLD board)
        def counts(r):
            if r.get("ladder_revision"):
                return False
            return ((not revisions or r.get("bench_revision") in revisions)
                    and (r.get("category") not in _config.PRIVATE_CATEGORIES
                         or private_row_current(r)))
    try:
        from .profiles import load_profile
        board_prof = load_profile(active_name(cfg), cfg)
    except Exception:  # noqa: BLE001 - no readable profile: only its own rows count
        board_prof = {"name": active_name(cfg)}
    from . import chains as _chains
    chain_index = _chains.profile_index(board_prof) if board_prof.get("chains") else {}
    if revisions is None:
        # a ladder pack case seeded on a measured flat instance takes that stored row (guards:
        # alias_refusal; the row's currency is checked there, as a chain row)
        rows = _with_seed_aliases(rows, board_prof, chain_index)
    keep = [r for r in rows
            if reusable_row(r, board_prof)
            and (r.get("seed_alias") or counts(r))
            and ((ids is None or r.get("case_id") in ids) if not r.get("chain")
                 else _chains.in_profile(r, chain_index))
            and (with_rungs or not r.get("budget_rung"))
            and not (hidden and (r.get("category") in hidden["categories"]
                                 or r.get("case_id") in hidden["case_ids"]))]
    # 4.0 (review H1): a stored repeat above the profile's current count for
    # its case (3.9 ran tooluse/codeagent twice) never counts — every model is
    # scored over the same number of repeats; bench_all.missing_cases uses
    # the same rule
    # review L2: Room never takes a re-run above the profile's top rung (3.9
    # escalated to 131072; 4.0 retries at the next rung only)
    prof_r = _tier_profile(cfg) or {}
    top = max([int(x) for x in (prof_r.get("escalation_rungs") or [])] or [0])
    if top:
        # W5 (2026-11-01): ...unless an `escalate --rungs` run of this label
        # completed that rung (a deliberate off-profile retry, e.g. 131072
        # for a job whose own budget already was the top rung)
        from .runner import escalated_rungs
        top = max([top, *escalated_rungs(label)])
        keep = [r for r in keep if int(r.get("budget_rung") or 0) <= top]
    reps = _profile_case_repeats(cfg)
    if reps:
        keep = [r for r in keep
                if r.get("case_id") not in reps or int(r.get("repeat") or 1) <= reps[r["case_id"]]]
    if revisions is None:
        # 2026-11-13: rows under a superseded sampler (sampler_select)
        view = sampler_view if (sampler_view != "current" or sampler_retest(label)) else "none"
        keep = sampler_select(keep, sampler_want(label, cfg), view)
    # 2026-11-15 (lead): a verdict judged under an older rubric text never counts (judge pending)
    from . import judge_digest
    return judge_digest.drop_stale(_recheck(_board_rows(keep)))


def _profile_case_repeats(cfg: dict | None) -> dict[str, int]:
    """{standard case id: its repeat count} under the benchmark profile ({}
    when it cannot be read)."""
    try:
        from .config import repeats_for
        _, cfg_p, cases = _profile_applied(cfg)
        return {c["id"]: repeats_for(cfg_p, c["category"], case=c) for c in cases}
    except Exception:  # noqa: BLE001 — the profile-id filter already logs this
        return {}


_PROFILE_RELEASE: dict = {}


def _profile_release(name: str | None) -> str | None:
    """The release a profile declares (bench45 ``release_seed: "4.5.0"``); None for a profile without one
    (``bench``, the 4.1 board) or one that cannot be read. Cached per name."""
    if not name:
        return None
    if name not in _PROFILE_RELEASE:
        try:
            from .profiles import load_profile
            rel = load_profile(name).get("release_seed")
        except Exception:  # noqa: BLE001 - an unreadable profile: the row's own stamp
            rel = None
        _PROFILE_RELEASE[name] = str(rel) if rel else None
    return _PROFILE_RELEASE[name]


def row_suite(row: dict) -> str | None:
    """The suite a row shows: its stamp's suite, or its profile's declared release when that is newer
    (ship plan C8: a bench45 row made while the harness still stamped 4.1.0 is a 4.5 row; currency never
    reads the suite prefix, so nothing is re-run or restamped)."""
    from .version import stamp_version, suite_of
    s = suite_of(row.get("bench_revision"))
    rel = _profile_release(row.get("profile"))
    if s and rel and stamp_version(rel) > stamp_version(s):
        return rel
    return s


def suites_of(rows: list[dict]) -> list[str]:
    """The suite versions ("3.5.0", "3.8.0") the rows were generated under (row_suite)."""
    from .version import stamp_version
    return sorted({row_suite(r) for r in rows if r.get("bench_revision")}, key=stamp_version)


def room_stats(label: str, cfg: dict | None, board_rows: list[dict], meta: dict,
               era: dict) -> dict:
    """The Room column (escalation, runner.DEFAULT_ESCALATION_RUNGS):
    ``{"total", "chat", "coding", "rungs", "pending", "ran_out"}``.

    ``ran_out`` = board jobs that ran out of budget; ``pending`` = jobs still
    out of budget below the top rung (not escalated yet, or still cut at a
    lower rung); ``rungs`` = the rungs used. With no escalation row the
    numbers are None (a model nothing ran out on: Room = Overall). A rung
    the rig cannot serve (runner.blocked_rungs) is left out of ``pending``."""
    from .judge import needs_verdict
    from .runner import escalation_jobs, escalation_rungs, escalation_todo
    try:
        _, cfg_p, _ = _profile_applied(cfg)
    except Exception:  # noqa: BLE001 — no profile readable: no escalation known
        cfg_p = cfg or {}
    # a model or route that caps its output below a rung never gets that rung (its clamp is on the rows)
    ent_ = next((m for m in (cfg or {}).get("models") or [] if isinstance(m, dict) and m.get("name") == label), {})
    omax_ = [int(ent_["max_output_tokens"])] if ent_.get("max_output_tokens") else []
    omax_ += [int((r.get("budget_clamp") or {}).get("route_max")) for r in board_rows
              if isinstance((r.get("budget_clamp") or {}).get("route_max"), int)]
    rungs = escalation_rungs(cfg_p, min(omax_) if omax_ else None)
    top = max(rungs) if rungs else 0
    out = {"total": None, "chat": None, "coding": None, "rungs": [],
           "ran_out": len(escalation_jobs(board_rows, 1 << 30)), "pending": 0}
    if not top:
        return out
    room = board_filter(label, cfg, with_rungs=True)
    # jobs still due at the highest rung the rig can serve (a rung given up
    # on is not "pending" forever), plus escalated rows still waiting for
    # the judge: the number is not final until both are zero
    out["pending"] = len(escalation_todo(room, label, rungs)) + sum(
        1 for r in room if r.get("budget_rung") and needs_verdict(r, False, False))
    used = sorted({int(r["budget_rung"]) for r in room if r.get("budget_rung")})
    if not used:
        return out
    st = tier_model_stats(label, cfg, room, meta, era)
    prof = _tier_profile(cfg)
    c = (tier_scorecard(st, scoring_config(cfg), prof) if prof
         else scorecard(st, scoring_config(cfg)))
    out.update(total=c["total"], chat=c["chat"], coding=c["coding"], reason=c.get("reason"),
               rungs=used)
    return out


def efficiency(label: str, cfg: dict | None, board_rows: list[dict]) -> dict:
    """Efficiency (points board, EVAL-FABLE M1): the share of the model's
    standard-budget rows that finished inside that budget. ``ran_out`` =
    rows that hit the budget (finish=length); ``escalated`` = those an
    escalation re-run now stands in for on the board; ``pending`` = ran out
    with no re-run yet (they count as failed until one exists)."""
    # one unit per JOB (review S6): a multi-turn session counts once, and it
    # ran out when any of its turns did (runner.escalation_jobs' rule)
    jobs = _jobs(board_filter(label, cfg, with_rungs=False))
    if not jobs:
        return {"rate": None, "n": 0, "ran_out": 0, "escalated": 0, "pending": 0}
    out = [k for k, rs in jobs.items() if any(r.get("finish_reason") == "length" for r in rs)]
    # escalated = an escalation re-run that FINISHED now stands in for it
    # (one that ran out again is still pending: counted failed, review B4)
    # review L1: with re-runs off the board the retries live in the Room view
    src = board_rows
    if not (_tier_profile(cfg) or {}).get("escalation_on_board", True):
        src = board_filter(label, cfg, with_rungs=True)
    done = {_job_key(r) for r in src if r.get("budget_rung")
            and r.get("finish_reason") != "length" and r.get("grade") != "error"}
    esc = sum(1 for k in out if k in done)
    # PUBOUT 4.5: what the score did with each cut reply. Under a part's `ran_out: exclude` rule the reply is
    # left out of the score (and counts toward the 25% "not measured" rule), never counted failed.
    excl = sum(1 for k in out if k not in done and _ran_out_rule_of(jobs[k], cfg) == "exclude")
    # D10a: under cut_at_full_budget: zero a short-keyed part's reply cut at the budget sent "did not finish": it is
    # graded as delivered and stays in the mean, so it is not "left out" (never both words for the same reply)
    n_dnf = 0
    try:
        from .chains import SHORT_KEYED_PARTS as _SHORT, cut_at_full_budget as _caf, full_budget_cut as _fbc
        prof_ = _tier_profile(cfg) or {}
        if _caf(prof_) == "zero" and prof_.get("chains"):
            ids_ = {c for k_, v_ in declared_case_sets(prof_, cfg).items() if k_ in _SHORT for c in v_}
            for k in out:
                if k in done or _ran_out_rule_of(jobs[k], cfg) != "exclude":
                    continue
                if any(r.get("finish_reason") == "length"
                       and ((r.get("chain") or {}).get("part") in _SHORT or r.get("case_id") in ids_)
                       and _fbc(r, r.get("max_tokens_sent") or (r.get("chain") or {}).get("ceiling"))
                       for r in jobs[k]):
                    n_dnf += 1
    except Exception:  # noqa: BLE001 -- display only: a profile that cannot say leaves every cut reply as it was
        n_dnf = 0
    excl -= n_dnf
    # a 4.1 board (no chains: no ran-out rule) emits the dict exactly as before (4.1 is frozen)
    return {"rate": 1 - len(out) / len(jobs), "n": len(jobs), "ran_out": len(out),
            "escalated": esc, "pending": len(out) - esc,
            **({"excluded": excl} if (_tier_profile(cfg) or {}).get("chains") else {}),
            **({"did_not_finish": n_dnf} if n_dnf else {}),
            "off_board": src is not board_rows}


def _ran_out_rule_of(rows: list[dict], cfg: dict | None) -> str | None:
    """The ran-out rule the board scored one job under: a chain unit's cell rule (scale.ran_out_rules,
    the unit's own block as the fallback, as scale.units does), a case part's rule
    (case_part_ran_out); None when no rule applies (the job is counted failed)."""
    try:
        prof = _tier_profile(cfg) or {}
    except Exception:  # noqa: BLE001
        return None
    if not prof.get("chains"):
        return None
    r = rows[0] if rows else {}
    blk = r.get("chain")
    from . import scale as _scale
    if isinstance(blk, dict):
        try:
            return _scale._rule(_scale.ran_out_rules(prof), _scale.cell_of(blk), blk.get("ran_out"))
        except Exception:  # noqa: BLE001 - an unreadable rule: say nothing new
            return None
    from . import tiers as _tiers
    for d in _tiers.scores(prof):
        for p_ in d.get("parts") or []:
            if r.get("case_id") in (p_.get("cases") or []):
                try:
                    return case_part_ran_out(prof, p_)
                except ConfigError:
                    return None
    return None


def room_cell(s: dict) -> str:
    """The Room column: Overall with every job that ran out of budget re-run
    at the escalation rungs. "=" = nothing ran out (Room is Overall), "…" =
    jobs ran out and wait for the escalation round, blank = old values."""
    r = s.get("room")
    if not r:
        return ""
    if r.get("total") is not None:
        return fmt(r["total"]) + ("…" if r.get("pending") else "")
    return "…" if r.get("ran_out") else "="


def board_view(label: str, cfg: dict | None) -> tuple[list[dict], dict]:
    """(rows, era) the board shows for ``label``.

    A model's CURRENT rows count once they cover every benchmark case and are
    all judged. Until then — a 3.8.0 re-run in progress over several nights —
    the board keeps showing the model's rows of the previous regime, marked
    old (era ``old: True``, with the re-run's progress), instead of a half-
    finished current run or nothing at all. A model with no old rows shows
    its current rows whatever their state (partial is then in the Notes)."""
    from .version import previous_regime_revisions
    rows_all = load_transcripts(label)
    cur = board_filter(label, cfg, rows=rows_all)
    ids = _profile_case_ids(cfg)
    expected = len(ids) if ids is not None else None
    # suite 3.9: tier-2/3 rows (unlocked per score) never decide whether
    # the model's run is complete — its tier-1 cases do
    have = {r.get("case_id") for r in cur if r.get("case_id")}
    if ids is not None:
        have &= ids
    pending = any(r.get("needs_judge") and "judge" not in r for r in cur
                  if ids is None or r.get("case_id") in ids)
    complete = expected is not None and len(have) >= expected and not pending
    if not complete:
        old = board_filter(label, cfg, rows=rows_all, revisions=previous_regime_revisions(),
                           with_rungs=False)
        if old:
            return old, {"old": True, "suites": suites_of(old),
                         "rerun_cases": len(have), "expected": expected}
    return _forfeit_rows(label, cur, cfg), {"old": False, "suites": suites_of(cur)}


def _forfeit_failed(r: dict, case: dict | None, fix: bool = False) -> dict:
    """A forfeited job's row (forfeit45.py) as the failed answer the graders would have stored for a reply that
    fixes or answers nothing: _counted_failed (on a judged case every judged element failed), plus, on a Fixes unit,
    the bug-fix checker's verdict for an empty
    reply (fixrows45.reply_verdict: score 0, grade fail, the current checker stamp). ``fix``: the row is a unit of
    the score tree's Fixes segment (a generated patch case is not in the static case set)."""
    from . import forfeit45 as _ff45
    r = _counted_failed(r, _ff45.ROW_KEY, case)
    j = r.get("judge")
    if isinstance(j, dict) and j.get("counted_failed") and (case or {}).get("judge_elements"):
        # a judged scene or session: the verdict lists every judged element as failed (the long-scene scorers read
        # only a row whose verdict holds element results), and empty_generation zeroes every item, code checks too
        r["judge"] = {**j, "elements": {"results": [{"id": e["id"], "pass": False, "detail": "counted as 0 (forfeit)"}
                                                    for e in case["judge_elements"] if e.get("id")]}}
    from . import fixrows45 as _fr
    if fix or (case and _fr.is_fix_case(case)):
        r["score"] = 0.0
        r["fixcheck"] = _fr.reply_verdict(case, "", finish_reason="stop", completion_tokens=None, sent=None,
                                          blank=True)
    return r


def _forfeit_rows(label: str, rows: list[dict], cfg: dict | None) -> list[dict]:
    """4.5.1: the board rows with each declared forfeit's job (forfeits.json in the results folder; only while that
    job is MISSING with reason error) scored as a failed answer, in memory. ``rows`` is returned unchanged (same
    object) when no forfeit is on record for ``label``."""
    from . import forfeit45 as _ff45
    if not _ff45.records_for(label):
        return rows
    prof = _tier_profile(cfg)
    cases = _cases_by_id()
    fix = set(_fix_cases(prof)) if prof else set()
    return _ff45.apply(label, rows, _refusal_case_ids(prof) if prof else frozenset(),
                       lambda r: _forfeit_failed(r, cases.get(r.get("case_id")), r.get("case_id") in fix))


_PROFILE_IDS_WARNED: list = []


def _profile_case_ids(cfg: dict | None, with_tiers: bool = False) -> set[str] | None:
    """The case ids of the benchmark profile, or None when it cannot be read.
    A row whose case left the profile (renamed/replaced — e.g. the saturation
    run's RX20-five-houses-six-attributes, later RX20-five-houses-four-attributes)
    is not on the board: an equivalent revision stamp says the OTHER cases are
    unchanged, not that a retired case still counts. Without this filter such
    rows entered the pass rates and inflated coverage past the profile (51/49
    read as "full" and would have hidden two genuinely missing cases)."""
    try:
        prof, _, cases = _profile_applied(cfg)
        if with_tiers:
            # suite 3.9: tier-2/3 rows are on the board too (tiers.py); a
            # model's run is COMPLETE on its tier-1 cases alone
            from .profiles import tier_cases
            cases = cases + [c for t in (2, 3) for c in tier_cases(prof, t)]
            if prof.get("scores"):
                from .ladder import all_ladder_case_ids
                by_id = _cases_by_id()
                # the audience's sets: a public board never lists private packs (S4)
                cases = cases + [by_id[i] for i in sorted(
                    all_ladder_case_ids(_audience_profile(prof, cfg))) if i in by_id]
                # 4.5: the hard sets' cases are on the board too
                from .profiles import hard_cases as _hard_cases
                cases = cases + [c for c in _hard_cases(prof)]
    except Exception as e:  # noqa: BLE001
        # review N3: the board then cannot drop retired cases — say so loudly
        # (once per process) instead of silently over-counting
        if not _PROFILE_IDS_WARNED:
            _PROFILE_IDS_WARNED.append(True)
            log.error("benchmark profile unreadable (%s: %s) — the board cannot filter "
                      "retired cases or check coverage", type(e).__name__, e)
        return None
    hidden = _hidden(cfg)
    return {c["id"] for c in cases
            if not (hidden and (c["category"] in hidden["categories"]
                                or c["id"] in hidden["case_ids"]))}


_CASES_BY_ID: dict | None = None


def _cases_by_id() -> dict:
    """The current case set by id (loaded once per process)."""
    global _CASES_BY_ID
    if _CASES_BY_ID is None:
        _tier_profile(None)      # selects the active profile's case tree first (G-41Freeze)
        from .config import load_cases
        try:
            _CASES_BY_ID = {c["id"]: c for c in load_cases(include_hard=True)}
        except Exception as e:  # noqa: BLE001 — a broken case file must not blank the board
            log.error("could not load cases (%s) — stored checks kept, case tags unknown", e)
            _CASES_BY_ID = {}
    return _CASES_BY_ID


def _recheck(rows: list[dict]) -> list[dict]:
    """Re-apply the CURRENT grading to stored rows: the deterministic checks
    (session_checks) and the errored/failed split for template-parser rows. Checks are grading, like graders: a fixed false positive must fix
    every row already on the board, not only rows generated after the fix
    (the checks are excluded from the suite revision hash for this reason —
    version._GRADING_KEYS). Rows are copied, never rewritten on disk."""
    from . import session_checks
    cases_by_id = _cases_by_id()
    out = []
    for r in rows:
        if (r.get("grade") == "fail" and r.get("error_kind") == "generation"
                and "unable to generate parser" in str(r.get("error") or "").lower()):
            # rows written before 3.4.1: a template the server could not
            # render generated nothing — errored, not a model failure
            r = {**r, "grade": "error", "error_kind": "template"}
        case = cases_by_id.get(r.get("case_id"))
        if r.get("checks") is not None and case and case.get("checks"):
            if r.get("conversation"):
                replies = [m.get("content") or "" for m in r["conversation"]
                           if m.get("role") == "assistant"]
            else:
                replies = [row_answer(r)]
            # r2's check changes (pivot regex, review B6) are v2 board rules:
            # the 4.1 board (no ``scoring_rules: v2``) re-checks with 4.1's
            r = {**r, "checks": session_checks.run_checks(
                case, replies, legacy41=not scoring_rules_v2(r))}
        if case and case.get("tool_script") and r.get("tool_conversation"):
            # 3.10 (Lane C3): answer / no-tool step needles are grading too —
            # re-grade them from the stored replies (tool-call steps keep
            # their stored verdict); an unchanged case returns the row as is
            from .stepgrade import regrade_tool_row
            r = regrade_tool_row(r, case)
        r = _recheck_quotes(r, case)
        out.append(r)
    return out


_QUOTE_FLIPS_LOGGED: set = set()


def _recheck_quotes(r: dict, case: dict | None) -> dict:
    """Board-time quote re-verification (4.1.0): a stored element verdict
    that failed ONLY because its quote did not verify under the verifier of
    the day is counted again under the current one, when the stored quote
    verifies now and tips the majority (judge.recheck_flips). Deterministic,
    no judge call; the row is copied, never rewritten on disk; every flip is
    logged once as case/model/element. rpdeep rows are scored by rpdeep
    (graded-turn text) and dialogue-scoped elements keep their own check."""
    j = r.get("judge")
    res = ((j or {}).get("elements") or {}).get("results") if isinstance(j, dict) else None
    if not res or r.get("category") == "rpdeep":
        return r
    # 4.5 lead decision (2026-11-09): a profile switch `quote_recheck`: "legacy" on the
    # 4.1 `bench` profile (the 4.1.0 recheck, so its board stays byte-for-byte), full
    # (with the review-F22 guards) in bench45. A row reused from bench is scored by the
    # BOARD's profile (profiles.row_switch).
    from .profiles import row_switch
    # a row with no profile stamp (4.1 era) reads the active profile's switch
    mode = row_switch(r, "quote_recheck")
    if not mode:
        return r
    legacy = mode == "legacy"   # bench (4.1): the 4.1.0 recheck, without the review-F22 guards
    from .judge import RUBRICS, assistant_text, recheck_flips
    specs = {e.get("id"): e for e in (case or {}).get("judge_elements") or []}
    rubric = RUBRICS.get(r.get("rubric") or (case or {}).get("rubric") or "") or {}
    skip = {i for i, e in specs.items() if e.get("quote_scope") == "dialogue"}
    # review F22: a failed element capped a numeric dimension (judge.apply_elements
    # keeps no pre-cap value), so flipping it would half re-score the row: those
    # elements keep their stored verdict
    if not legacy:
        skip |= {i for i, e in specs.items() if e.get("dim") in (rubric.get("dims") or ())}
    ids = recheck_flips(res, assistant_text(r), skip)
    if not ids:
        return r
    for i in ids:
        key = (r.get("model_label"), r.get("case_id"), r.get("repeat"), i)
        if key not in _QUOTE_FLIPS_LOGGED:
            _QUOTE_FLIPS_LOGGED.add(key)
            log.info("quote recheck: flipped %s / %s / %s to pass", r.get("case_id"),
                     r.get("model_label"), i)
    new = [{**e, "pass": True, "detail": "judge: yes; quote verified at board time"}
           if e.get("id") in ids else e for e in res]
    rate = round(sum(1 for e in new if e.get("pass")) / len(new), 3)
    out_j = {**j, "elements": {**j["elements"], "results": new, "rate": rate}}
    # review F22: a flag the flipped element had forced False goes back to the
    # judge's own value once no failed element on that flag is left
    scores = j.get("scores")
    if isinstance(scores, dict) and not legacy:
        flags = set(rubric.get("flags") or ())
        failed_dims = {(specs.get(e.get("id")) or {}).get("dim") for e in new
                       if not e.get("pass")}
        sc2 = dict(scores)
        for i in ids:
            dim = (specs.get(i) or {}).get("dim")
            if dim in flags and dim not in failed_dims and f"{dim}_judge" in sc2:
                sc2[dim] = sc2.pop(f"{dim}_judge")
        if sc2 != scores:
            out_j["scores"] = sc2
    return {**r, "judge": out_j}


def _meta_counts(meta: dict, cfg: dict | None) -> bool:
    """A failed run with no rows still belongs on the board (Notes say why)
    when it was a benchmark run at the current revision."""
    from .profiles import DEFAULT_PROFILE
    from .version import in_regime
    current = _current_revisions(cfg)
    # 3.10 (D1): a run of the current regime (version tuple, never a string
    # compare); its rows' currency is decided per row
    return (meta.get("profile") == active_name()
            and (not current or meta.get("bench_revision") in current
                 or in_regime(meta.get("bench_revision"))))


def _archived_note() -> str | None:
    archives = sorted((p for p in results_dir().glob("archive-*") if p.is_dir()),
                      key=lambda p: p.stat().st_mtime)
    if not archives:
        return None
    n = sum(1 for a in archives for _ in a.glob("transcripts_*.jsonl"))
    if not n:
        return None
    newest = archives[-1].name
    return (f"*{n} older run(s) archived in results/{newest}/ and earlier "
            f"results/archive-*/ dirs — not on this board.*")


PACKS_PENDING_REASON = ("Bonus packs pending a re-run: the provider's API credit ran out "
                        "before they ran. Standard tests are complete.")


def _packs_pending_provisional(st: dict, cfg: dict | None) -> None:
    """4.1 F1: a bonus pack refused by the provider's billing inside a part
    of the Total (Code, Chat) leaves the model unranked: provisional, with
    the reason.  A pack outside the Total (the adult test) only blanks its
    own score (points_scorecard)."""
    blocked = st.get("packs_blocked")
    prof = _tier_profile(cfg)
    if not blocked or not prof:
        return
    from . import tiers as _tiers
    in_total = {d["key"] for d in _tiers.scores(prof) if d["in_total"]}
    if not any(b.split("/", 1)[0] in in_total for b in blocked):
        return
    st["provisional"] = True
    old = st.get("provisional_reason")
    st["provisional_reason"] = PACKS_PENDING_REASON + (f" ({old})" if old else "")


class ClaudeCodeOnBoard(RuntimeError):
    """A claude_code-provider (subscription CLI) model reached a board or report
    output. Board rows must come from the API (maintainer, 2026-11-12): sampling is
    the Claude Code harness's own, so those rows are not comparable."""


def claude_code_labels(stats: dict) -> list[str]:
    """Labels in ``stats`` whose meta says the claude_code provider produced them
    (provider_type, or the subscription + harness-default signature runner.py stamps)."""
    out = []
    for label, s in (stats or {}).items():
        meta = (s or {}).get("meta") or {}
        if (meta.get("provider_type") == "claude_code"
                or meta.get("provider") == "claude_code"
                or (meta.get("cost_basis") == "subscription"
                    and meta.get("sampling") == "harness-default")):
            out.append(label)
    return sorted(out)


#: the staged-board opt-in (``report --staged-claude-code``): a cfg key only the CLI sets, never
#: the public / site / HF paths; refused whenever the audience is public (staged_cc_allowed)
STAGED_CC_KEY = "_staged_claude_code"
STAGED_CC_MARK = "validation row: Claude Code route, default effort; not for publication"


def staged_cc_mark(effort: str | None) -> str:
    """The validation-row note with the run's effort (lead 2026-11-14): rows made before the effort
    was passed say "default effort" (the 2026-11-12 text, unchanged)."""
    return f"validation row: Claude Code route, {effort or 'default'} effort; not for publication"


def _cc_effort(s: dict) -> str:
    meta = (s or {}).get("meta") or {}
    return str(meta.get("cc_effort") or (meta.get("regime") or {}).get("cc_effort") or "default")
STAGED_CC_HEAD = ("STAGED board: Claude Code validation rows shown (unranked; harness sampling at the "
                  "effort each row names, not comparable to API rows), not for publication")
STAGED_CC_FOOTNOTE = ("Rows marked \"" + staged_cc_mark("<effort>") + "\" ran through the claude_code provider (the "
                      "subscription CLI): sampling and budget are Claude Code's own defaults, not set by the "
                      "profile; the effort is the explicit --effort named in the row's note (rows made before "
                      "2026-11-14 ran at the CLI's default effort). They are not comparable to API rows (the guard "
                      "of 2026-11-12). They "
                      "are shown on this staged board only to validate the route, never ranked, tied or used "
                      "for medians, and a public render refuses them.")


def staged_cc_allowed(cfg: dict | None) -> bool:
    """The staged-board opt-in is set and the render is not publish-shaped (no public audience,
    no private scope)."""
    return (bool((cfg or {}).get(STAGED_CC_KEY)) and (cfg or {}).get("_audience") != PUBLIC
            and not _hidden(cfg))


def mark_staged_cc(stats: dict, cfg: dict | None) -> None:
    """Under the staged opt-in: every claude_code row is a validation row -- provisional (shown,
    never ranked, tied or in the medians) with the marker in its Notes. No-op otherwise."""
    if not staged_cc_allowed(cfg):
        return
    for l in claude_code_labels(stats):
        s = stats[l]
        s["validation_cc"] = True
        cov = s.setdefault("coverage", {})
        cov["provisional"] = True
        mark = staged_cc_mark(_cc_effort(s))
        cov["provisional_reason"] = mark
        s["provisional"], s["provisional_reason"] = True, mark


def assert_no_claude_code(stats: dict, cfg: dict | None = None) -> None:
    """Raise :class:`ClaudeCodeOnBoard` when any claude_code model is in ``stats``, except the
    validation rows of a staged board (staged_cc_allowed + mark_staged_cc)."""
    ok = staged_cc_allowed(cfg)
    bad = [l for l in claude_code_labels(stats) if not (ok and (stats.get(l) or {}).get("validation_cc"))]
    if bad:
        raise ClaudeCodeOnBoard(
            "claude_code rows may not appear on a board or report (they are subscription "
            "CLI rows with harness-default sampling; board rows come from the API): "
            + ", ".join(bad))


def _not_publishable_reason(meta: dict) -> str:
    es = meta.get("early_stop") or {}
    if isinstance(es, dict) and (es.get("reason") or es.get("error")):
        return f"early stop ({es.get('reason') or es.get('error')})"
    return str(meta.get("publishable_reason") or "run marked not publishable")


#: label -> why a public render left the model off (held45); reset by every board_stats call
LAST_LEFT_OFF: dict = {}

#: the plain words for a row made before grader stamps existed (fix round 2026-11-18, P3)
STALE_NO_STAMP = "stale: made before grader stamps"


#: model_stats keys that feed the scorecard only (never written to report.json)
_CARD_INPUTS = ("label", "run_ids")


def other_judge_text(ids, cfg: dict | None = None) -> str:
    """D13 (lead 2026-11-19 b): a part placed by a judge other than the profile's. Owner 2026-11-24 (any judge may
    judge): neutral words, the provider that served it when the results folder recorded one, and which judge
    MakoBench's published scores use."""
    from . import judge45 as _J
    try:
        provs = _J.judge_providers()
    except Exception:  # noqa: BLE001 -- a trace never breaks the board
        provs = {}
    who = ", ".join(f"{i} ({provs[i]})" if provs.get(i) else str(i) for i in ids)
    try:
        board = _primary_judge(cfg)
    except Exception:  # noqa: BLE001
        board = None
    ref = f"the local {_J.judge_name(board)} judge" if board else "the profile's local judge"
    return f"judged by {who}; MakoBench's published scores use {ref}"


def other_judge_rows(rows: list[dict], prof: dict | None) -> dict[str, int]:
    """{judge id: rows} of complete ladder placements stamped with a judge other than the profile's (D13)."""
    j = (prof or {}).get("judge")
    jm = j.get("model_id") if isinstance(j, dict) else None
    out: dict[str, int] = {}
    if not jm:
        return out
    for r in rows:
        rec = r.get("judge_ladder")
        if isinstance(rec, dict) and rec.get("complete"):
            o = (rec.get("instrument") or {}).get("judge_model")
            if o and o != jm:
                out[str(o)] = out.get(str(o), 0) + 1
    return out


def rank_parts45(labels: list[str], stats: dict, prof: dict | None) -> None:
    """Lead 2026-11-19 (b), on a ``pending_scaling: none`` board, before the headline:

    * a row is ranked only among rows that have every part the board currently counts (a part counted for at least
      one current row). A row missing one (not measured, judge agreement not measured for this model, ...) keeps
      its points as "x of y", is not ranked, and says why;
    * a row with a part placed by a judge other than the profile's (D13) is ranked like any other and marked
      (other_judge45; owner 2026-11-24: any user may use any judge);
    * when the ranked rows' maximum is below the full Overall, the Overall and section cells print "x of y".
    Rows of different maxima are never ranked against each other."""
    if not prof or not no_scaling(prof):
        return
    cur = [l for l in labels if not (stats[l].get("era") or {}).get("old")
           and not (stats[l].get("coverage") or {}).get("failed") and stats[l].get("scorecard")]
    if not cur:
        return

    def tot_defs(c):
        return [d for d in c.get("score_defs") or [] if d.get("in_total", True) and d.get("parts")]

    def counted(c) -> dict:
        out = {}
        for d in tot_defs(c):
            pend = {x["part"]: x for x in merge_pending((c.get("pending_points") or {}).get(d["key"]) or [])}
            for p in d["parts"]:
                if float(p.get("weight") or 0) > 0:
                    out[(d["key"], p["key"])] = pend.get(p["key"])
        return out
    have = {l: counted(stats[l]["scorecard"]) for l in cur}
    board = {k for l in cur for k, why in have[l].items() if why is None}
    for l in cur:
        c = stats[l]["scorecard"]
        if c.get("total") is None:
            continue
        shorts = {(d["key"], p["key"]): p.get("short") or p.get("label") or p["key"]
                  for d in tot_defs(c) for p in d["parts"]}
        bits = []
        for k in sorted(board):
            why = have[l].get(k)
            if why is not None:
                w = (why.get("whys") or [why.get("why")])[0] or "pending"
                bits.append(f"{shorts.get(k, k[1])} {str(w).removeprefix('pending: ')}")
        # owner 2026-11-24 (any judge): a part placed by a judge other than the profile's is scored and ranked on
        # the user's own board, marked with the judge (other_judge45); only ``report --public`` refuses it
        mm = c.get("measured_max") or {}
        in_total = [d["key"] for d in tot_defs(c)]
        of = sum(float(mm.get(k) or 0) for k in in_total)
        if bits:
            c["overall_measured"] = {"sections": in_total, "points": c["total"], "of": of}
            c["total"] = None
            c["unranked_why"] = "not ranked: " + "; ".join(bits)
            continue
        full = sum(float(p.get("weight") or 0) for d in tot_defs(c) for p in d["parts"])
        if mm and of < full - 1e-9:
            c["overall_of"] = of


def _np_other_judge_info(rows: list[dict], meta: dict | None, cfg: dict | None) -> dict | None:
    """The NSFW+ judge info (judge45.verdicts) when the model's NSFW+ units are read from a judge other than the
    profile's; None when the profile judge's votes are the ones used or nothing is stored."""
    try:
        from . import judge45 as _J
        from . import nsfwplus45 as _np
        prof = _tier_profile(cfg)
        label = (meta or {}).get("model_label") or next((r.get("model_label") for r in rows if r.get("model_label")),
                                                         None)
        if not _np.enabled(prof) or not label or not _J.run_judge(results_dir(), label):
            return None
        units = _np.units_from_rows(rows)
        if not units:
            return None
        _v, info = _J.verdicts(units, results_dir(), label, prof)
        return info if info.get("other_judge") else None
    except Exception:  # noqa: BLE001 -- a trace never breaks the board
        return None


def _np_other_judge_of(rows: list[dict], meta: dict | None, cfg: dict | None) -> str | None:
    """held45's NSFW+ check: the other-judge trace, or None."""
    info = _np_other_judge_info(rows, meta, cfg)
    return np_other_judge({"nsfw_plus": {"judge45": info}}) if info else None


def other_judge_ids(label: str, cfg: dict | None, rows: list[dict] | None = None) -> list[str]:
    """Review F2: the judge ids other than the profile's behind anything ``report --public`` would publish for
    ``label``: a scored row's verdict, a session placement, an NSFW+ vote. [] for a model judged by the profile's
    judge alone."""
    ref = _primary_judge(cfg)
    if rows is None:
        rows = board_filter(label, cfg) or []
    ids = {j for j in (row_judge_id(r) for r in rows) if j and ref and j != ref}
    ids |= set(other_judge_rows(rows, _tier_profile(cfg)))
    info = _np_other_judge_info(rows, {"model_label": label}, cfg)
    if info:
        ids.add(str(info["other_judge"]))
    return sorted(ids)


def judges_note(label: str) -> str | None:
    """A model whose rows were judged by more than one recorded judge (review F2): which one the board uses."""
    try:
        from . import judge45 as _J
        js = _J.label_judges(results_dir(), label)
        cur = _J.run_judge(results_dir(), label)
    except Exception:  # noqa: BLE001
        return None
    if len(js) < 2 or not cur:
        return None
    return (f"rows of this model were judged by {', '.join(js)}; the board uses {cur}'s verdicts only (one judge "
            f"per model; rows {cur} has not judged read pending)")


def held45(rows: list[dict], meta: dict | None, cfg: dict | None) -> list[str]:
    """Why a model's board rows may not be published (fix round 2026-11-18, lanes P2/P3 wired into the report):
    rows graded before grader stamps existed or by grading code that has changed since
    (grader_stamp.row_is_stale), and HOSTED rows that do not record the reasoning effort they ran at
    (disclosure.publish_refusal). [] when nothing holds the row. The 4.1 archive profile is exempt: its rows were
    never stamped and are published under the 4.1 rules."""
    from .adult_guard import ARCHIVE_PROFILES
    if active_name(cfg) in ARCHIVE_PROFILES:
        return []
    from . import disclosure as _disc
    from . import grader_stamp as _gs
    scored = [r for r in rows if _gs.scored(r)]
    no_stamp = sum(1 for r in scored if not r.get("grader_stamp"))
    changed = sum(1 for r in scored if r.get("grader_stamp") and _gs.row_is_stale(r)[0])
    ptype = _meta_ptype(meta)
    no_effort = sum(1 for r in scored if _disc.publish_refusal(r, ptype))
    out = []
    if no_stamp:
        out.append(STALE_NO_STAMP + (f" ({no_stamp} of {len(scored)} rows)" if no_stamp < len(scored) else ""))
    if changed:
        out.append(f"stale: {changed} row{'s' if changed != 1 else ''} graded by older grading code")
    if no_effort:
        out.append(f"{no_effort} hosted row{'s' if no_effort != 1 else ''} without a recorded reasoning effort")
    # report --public --allow-other-judge (a user's board); a local board never holds a row for its judge (owner
    # 2026-11-24): it is scored, ranked and marked (other_judge45)
    allow = bool((cfg or {}).get("_allow_other_judge")) or not public_render(cfg)
    npj = None if allow else _np_other_judge_of(rows, meta, cfg)
    if npj:
        # owner 2026-11-24: NSFW+ units read from another judge's votes are shown on the user's own board and held
        # off a public one, as Chat placements by another judge are
        out.append(f"NSFW+ {npj}")
    oj = {} if allow else other_judge_rows(rows, _tier_profile(cfg))
    if oj:
        # D13 (lead 2026-11-19 b): sessions placed by another judge are never published without the switch
        n = sum(oj.values())
        out.append(f"{n} session placement{'s' if n != 1 else ''} {other_judge_text(sorted(oj), cfg)}")
    return out


def board_stats(labels_arg: str | None, cfg: dict | None) -> tuple[list[str], dict, int]:
    """(labels on the board, their stats, number of labels left off)."""
    _tier_profile(cfg)           # selects the active profile's case tree first (G-41Freeze)
    LAST_LEFT_OFF.clear()
    if labels_arg:
        labels = [s.strip() for s in labels_arg.split(",") if s.strip()]
    else:
        labels = sorted({p.stem.removeprefix("transcripts_")
                         for p in results_dir().glob("transcripts_*.jsonl")}
                        | {p.stem.removeprefix("meta_")
                           for p in results_dir().glob("meta_*.json")})
    stats: dict = {}
    inputs: dict = {}
    dropped = 0
    for label in labels:
        rows, era = board_view(label, cfg)
        meta = load_meta(label)
        # public: a model with no public rows (only private ones) is not
        # listed at all — an empty row would still say it was benchmarked
        if not rows and (_hidden(cfg) or not _meta_counts(meta, cfg)):
            dropped += 1
            continue
        if public_render(cfg) and claude_code_labels({label: {"meta": meta}}):
            # 4.5 PUBOUT: a Claude Code (subscription CLI) validation row is never ranked or published.
            # A public render leaves it out (it used to abort the whole render), and it is not counted
            # among the "no current run" models either: a public reader has nothing to learn from it.
            log.warning("%s: claude_code validation row — left off the public board", label)
            continue
        if meta.get("publishable") is False and (public_render(cfg) or _hidden(cfg)):
            # an early-stopped (or otherwise unpublishable) run never reaches the public board
            log.warning("%s: meta says publishable: false (%s) — left off the public board", label,
                        _not_publishable_reason(meta))
            dropped += 1
            continue
        held = held45(rows, meta, cfg) if not era["old"] else []
        if held and public_render(cfg):
            # stale or undisclosed rows never reach a public board: the model is left off with the reason named
            LAST_LEFT_OFF[label] = "; ".join(held)
            log.warning("%s: left off the public board (%s)", label, LAST_LEFT_OFF[label])
            continue
        if era["old"]:
            # the meta file belongs to the in-progress re-run (its start,
            # warnings, a "stopped" failure) — not to the old rows shown
            meta = {k: meta[k] for k in ("model_label", "model_id", "device", "provider",
                                         "provider_type", "price", "profile") if k in meta}
        stats[label] = tier_model_stats(label, cfg, rows, meta, era)
        oj45 = other_judge_ids(label, cfg, rows) if not era["old"] else []
        if oj45:
            # owner 2026-11-24: scored and ranked, marked with the judge that placed it (key absent otherwise, so an
            # all-local board renders as before)
            stats[label]["other_judge45"] = {"ids": oj45, "note": other_judge_text(oj45, cfg)}
        moi = sum(1 for r in rows if r.get("finish_reason") == "model_output_invalid" or r.get("model_output_invalid"))
        if moi:
            # review E (2026-11-25): replies the server could not parse are scored misses; the row says how many
            stats[label]["model_output_invalid"] = moi
        cov = stats[label].get("coverage") or {}
        stats[label]["provisional"] = bool(cov.get("provisional"))
        stats[label]["provisional_reason"] = cov.get("provisional_reason")
        _packs_pending_provisional(stats[label], cfg)
        if meta.get("publishable") is False:
            # staged / local boards show the row, flagged: never a silent, normal-looking score
            stats[label]["provisional"] = True
            stats[label]["provisional_reason"] = f"not publishable: {_not_publishable_reason(meta)}"
            stats[label]["not_publishable"] = _not_publishable_reason(meta)
        if held:
            # local board: shown, marked, never ranked (stale grades are not compared with current ones)
            cov = stats[label].setdefault("coverage", {})
            cov["provisional"] = True
            cov["notes"] = list(cov.get("notes") or []) + held
            # an earlier reason (not publishable, the coverage's own) is kept first; the held reasons follow
            prior = stats[label].get("provisional_reason") or cov.get("provisional_reason")
            why = "; ".join(([prior] if prior else []) + [h for h in held if h != prior])
            cov["provisional_reason"] = why
            stats[label]["provisional"] = True
            stats[label]["provisional_reason"] = why
            stats[label]["held45"] = held
        stats[label].update(row_facts(label, meta, cfg))
        _fl = _tier_profile(cfg)
        if _fl and _fl.get("flags"):
            from .profiles import flag_columns
            # 4.5 flag columns (Lockout): no case file / no rows = not run (None), never 0
            stats[label]["flags"] = {f["key"]: None for f in flag_columns(_fl)}
            if "lockout" in stats[label]["flags"]:
                from . import lockout as _lockout
                try:
                    stats[label]["flags"]["lockout"] = _lockout.board_value(_fl, label)
                    # Admin N (N3): the text the board shows; a rung-1 refusal reads
                    # "locked at LK01 (0/10)", never a bare 0
                    stats[label]["flags"]["lockout_label"] = _lockout.board_label(_fl, label)
                    if public_render(cfg) and stats[label]["flags"]["lockout_label"]:
                        # PUBOUT: the public data names the rung by its place, never its id ("LK04")
                        stats[label]["flags"]["lockout_label"] = re.sub(
                            r"\bLK0*(\d+)(?:-[\w-]+)?", lambda m_: f"rung {int(m_.group(1))}",
                            stats[label]["flags"]["lockout_label"])
                except Exception as e:  # noqa: BLE001 - a bad rung file leaves the column "not run"
                    log.warning("lockout column for %s not read: %s", label, e)
        inputs[label] = (rows, meta, era)
        stats[label]["latest_ts"] = max((str(r.get("ts") or "") for r in rows), default=None)
        if not era["old"]:
            if points_mode(_tier_profile(cfg)):
                stats[label]["efficiency"] = efficiency(label, cfg, rows)
                if not (_tier_profile(cfg) or {}).get("escalation_on_board", True):
                    # 4.0: re-runs stay off the board; Room = Overall with them
                    stats[label]["room"] = room_stats(label, cfg, rows, meta, era)
            else:
                stats[label]["room"] = room_stats(label, cfg, rows, meta, era)
    kept = [l for l in labels if l in stats]
    # board rows come from the API; a staged board may opt in to Claude Code validation rows,
    # marked before the headline so they are never ranked, tied or in the medians
    mark_staged_cc({l: stats[l] for l in kept}, cfg)
    assert_no_claude_code({l: stats[l] for l in kept}, cfg)
    _cards(kept, stats, cfg)
    _headline(kept, stats, inputs, cfg)
    assert_no_claude_code({l: stats[l] for l in kept}, cfg)
    return kept, stats, dropped


# ------------------------------------------------------------ report.html

def html_rows(labels: list[str], stats: dict, cfg: dict | None = None) -> list[dict]:
    """The HTML board's data — built from the SAME computed stats as
    report.md (exact labels, numbers as numbers), never parsed back out of
    the markdown."""
    assert_no_claude_code({l: stats[l] for l in labels if l in stats}, cfg)
    out = []
    newer = superseded_map(labels, stats, cfg)
    v2_fields = _v2_board_fields(cfg)
    parts45 = parts_board45(cfg)
    for i, l in enumerate(rank_labels(labels, stats), 1):
        s = stats[l]
        c = s["scorecard"]
        meta = s.get("meta") or {}
        cov = s.get("coverage") or {}
        judges = cov.get("judges") or s.get("judge_models") or []
        out.append({
            "rank": i, "label": l, "model_id": meta.get("model_id"),
            "provider": meta.get("provider") or meta.get("device"),
            # hosted API vs local rig (None = unknown, never filtered as hosted)
            "hosted": (bool(meta["hosted"]) if isinstance(meta.get("hosted"), bool) else
                       (meta.get("provider_type") or meta.get("provider")) not in LOCAL_PROVIDER_TYPES
                       if (meta.get("provider_type") or meta.get("provider")) else None),
            "chat": c["chat"], "coding": c["coding"], "overall": c["total"],
            # rc4: why a model with rows missing has no Overall ("not ranked: 2 jobs to re-run (Tools)"); the key is
            # present only when set (rc4 / pending_scaling none), so a 4.1 row stays byte-identical (board41 public golden)
            **({"not_ranked": c["unranked_why"]} if c.get("total") is None and c.get("unranked_why") else {}),
            # suite 3.9: Reason score and the tier reached per score
            "reason": c.get("reason"),
            "reached": {k: v.get("reached") for k, v in (c.get("tiers") or {}).items()} or None,
            "tiers_cell": " · ".join(
                f"{_score_label(c, k)} {v}" for k, v in (c.get("labels") or {}).items()),
            "room": (s.get("room") or {}).get("total"), "room_cell": room_cell(s),
            "tok_s": c["tok_per_s"], "date": run_date(s), "notes": notes_cell(s),
            # what one run of the main benchmark (Code + Chat) cost on the
            # provider's bill; generation only (the rig judge is free). None = local
            "cost_usd": s.get("cost_main_usd"),
            # R6: the run command's minutes (meta.timing), None = not recorded
            **({"minutes": s.get("run_minutes45")} if parts45 else {}),
            # R7: how the row was run, as the board prints it and as fields
            **({"run_settings": disclosure45_cell(s),
                "disclosure": {**(s.get("disclosure45") or {}),
                               **dict(zip(("cut", "empty"), _cut_empty_totals(s)))}} if parts45 else {}),
            "cost_separate_usd": s.get("cost_separate_usd"),
            "cost_basis": s.get("cost_basis"),
            "cost_partial": bool(s.get("cost_unknown_rows")),
            "blocked": (s.get("content_filtered") or {}).get("main", 0),
            "blocked_separate": (s.get("content_filtered") or {}).get("separate", 0),
            "suite": suite_cell(s), "old": bool((s.get("era") or {}).get("old")),
            # suite 3.9 headline (headline.py): S per score/overall, ± m, rank "2="
            "s": _hs(s, "S"), "m": _hs(s, "m"),
            "rank_label": (s.get("headline") or {}).get("rank"),
            "class": (s.get("headline") or {}).get("class"),
            "lean": (s.get("headline") or {}).get("lean"),
            "raw_cell": raw_cell(c) if s.get("headline") else None,
            "points_cell": "; ".join(
                x for d in c.get("score_defs") or [] if d.get("parts")
                for x in ([subtotal_parts_line(s, nm, d) for nm, sub in (c.get("subtotals") or {}).items()
                           if sub.get("score") == d["key"] and sub.get("kind") != "total"]
                          if any(sub.get("score") == d["key"] for sub in (c.get("subtotals") or {}).values())
                          and not routed_row(s) else [f"{d['label']} {points_cell(s['headline'], d)}"]))
            if s.get("headline") else None,
            "vs_above": ((s.get("headline") or {}).get("vs_above") or {}).get("text"),
            # a routed 4.5 row: the section scales are its numbers (D's routed-board note)
            **(_scale45_row(s) if (s.get("headline") or {}).get("units") == "scale45" else {}),
            "maxed": [k for k, v in ((s.get("headline") or {}).get("maxed") or {}).items()
                      if v.get("category")] or None,
            # 2026-11-07: which parts reached the top of every set ("<score>/<part>"); `maxed` above is the
            # whole-category subset. The site shows a MAX marker and lists the packs that need new questions.
            "maxed_parts": [f"{k}/{pk}" for k, v in ((s.get("headline") or {}).get("maxed") or {}).items()
                            for pk in (v.get("parts") or [])] or None,
            # harness v2 (review B3): unlocked-but-unrun packs per sub-score
            # ("<score>/<part>": n) — each counts 0 in the points until it runs;
            # the site marks the row so an unrun pack never reads as a 0 score
            # (4.5 board fields: a 4.1 ``bench`` row ships none of these three
            # keys, as 1671f17 did not — Fable checkpoint 13)
            **({"ladder_unrun": dict(c.get("ladder_unrun") or {}) or None,
                "ladder_unrun_total": sum((c.get("ladder_unrun") or {}).values()),
                # willingness x capability per NSFW sub-score (engagement_rate,
                # willingness_factor, capability, composite, n)
                "wc": _wc_cells(c) or None} if v2_fields else {}),
            # 2026-11-07: "Built on" tag, vendor-release flag and the newer version on this board, if any
            "base": s.get("base"), "official": s.get("official"), "supersedes": s.get("supersedes"),
            "superseded_by": newer.get(l),
            "tier": cov.get("tier", 0), "coverage": cov.get("status"),
            # alignment 2026-11-06 (ALIGN-INTERFACE.md)
            "provisional": bool(s.get("provisional")),
            "provisional_reason": s.get("provisional_reason"),
            "scope": s.get("scope"), "quant": s.get("quant"),
            "quant_standard": s.get("quant_standard"), "family": s.get("family"),
            "quant_reason": s.get("quant_reason"), "served": s.get("served"),
            "served_precision": s.get("served_precision"),
            "judge": ", ".join(str(j).rsplit("/", 1)[-1] for j in judges) if judges else None,
            # owner 2026-11-24: judged (in part) by a judge other than the profile's; the key exists only then
            **({"other_judge": s["other_judge45"]["note"]} if s.get("other_judge45") else {}),
            # PUBOUT 4.5 (4.5 boards only; a 4.1 board's data is frozen): how the judge scored this row's
            # judged parts (thinking, samples per reply); what "partial" means for this row in plain words
            # (None when complete); the Class band from the profile's headline.classes, always (`class`
            # itself is blanked while the board's status hides Class: an internal candidate)
            **({"judge_settings": s.get("judge_settings"),
                "coverage_note": cov.get("partial_note"),
                "class_band": (s.get("headline") or {}).get("class")} if parts45 else {}),
            # measured points / measured max / pending points + reasons, one field each (4.5 boards)
            **({"measured": (seg45_measured(s) if s.get("segments45") else points_measured(s))} if parts45 else {}),
            # a per-model note for readers (models.yaml `board_note`), its own field (4.5 boards)
            **({"board_note": s.get("board_note")} if parts45 else {}),
            "components": (parts_component_values(s) if parts45 else
                           {component_label(k): c["components"].get(k)
                            for g in c["weights"] for k in c["weights"][g]}),
            # every scoring group's value under its own key (work/chat/nsfw)
            **{g: c.get(g) for g in c["weights"] if g not in ("chat", "coding", "reason")},
            # a routed 4.5 row's display cells (Admin B), last so they replace the 4.1 ones:
            # route notes, units as coverage, no Room, the whole run's cost
            **(_html45_row(s) if s.get("suite45") else {}),
        })
        if parts45 and not routed_row(s):
            _section_html45(out[-1], s)
        if c.get("adult_withheld"):
            # R4: no NSFW number on the html row (its group value, its headline cell, its measured block)
            for d in c.get("score_defs") or []:
                if not d.get("in_total", True):
                    g = d.get("group") or d["key"]
                    out[-1][g] = None
                    for f in ("s", "m"):
                        if isinstance(out[-1].get(f), dict) and g in out[-1][f]:
                            out[-1][f] = {**out[-1][f], g: None}
                    if isinstance((out[-1].get("measured") or {}).get(d["key"]), dict):
                        out[-1]["measured"][d["key"]] = {**out[-1]["measured"][d["key"]], "points": None,
                                                         "standard_points": None, "withheld": ADULT_WITHHELD45}
            if isinstance((out[-1].get("measured") or {}).get("subtotals"), dict):
                out[-1]["measured"]["subtotals"] = {
                    k: ({**v, "points": None, "withheld": ADULT_WITHHELD45} if _sub_withheld(s, k) else v)
                    for k, v in out[-1]["measured"]["subtotals"].items()}
        if (c.get("subtotals") or s.get("segments45")) and not routed_row(s):
            _subtotal_html(out[-1], s)
        lk = lockout_flag_text(s)
        if lk:
            # the Lockout component's words (the ladder's sentence, or "not run")
            out[-1]["comp_text"] = {component_label("lockout"): lk}
    return out


def routed_row(s: dict) -> bool:
    """A routed 4.5 row (its numbers are the section scales)."""
    return (s.get("headline") or {}).get("units") == "scale45"


def _section_html45(row: dict, s: dict) -> None:
    """R2 (fix round 2026-11-18): an in-total 4.5 section on an html row says "of <measured max>" when that differs
    from its standard maximum, and "pending" when nothing is measured (section_cell45)."""
    hl = s.get("headline") or {}
    for d in (s.get("scorecard") or {}).get("score_defs") or []:
        if not d.get("in_total", True):
            continue
        txt = section_cell45(hl, d)
        g = d.get("group") or d["key"]
        sp = _std_pack45(hl, d)
        if txt == "pending":
            row.setdefault("cell_text", {})[g] = "pending"
        elif sp and sp[2] > 0.05:
            # R5: the html cell shows the total (it sorts on it); the tag splits it as the markdown's two columns do
            row.setdefault("sub_tag", {})[g] = (f"= {sp[0]:.1f} standard" + (f" of {sp[1]:g}" if " of " in txt else "")
                                                + f" + {sp[2]:.1f} packs")
        elif " of " in txt:
            row.setdefault("sub_tag", {})[g] = "of " + txt.split(" of ", 1)[1].split(" ±")[0]


def _subtotal_html(row: dict, s: dict) -> None:
    """The rc3 adult page's two numbers on an html row, keyed adult_<name> like the board's
    columns (render_report_html): the value, its margin, "[n pts pending]", "not run"."""
    from . import segments45 as _s45
    hl = s.get("headline") or {}
    row["s"], row["m"] = dict(row.get("s") or {}), dict(row.get("m") or {})
    tags, texts = {}, {}
    if s.get("segments45"):         # realigned tree: the two NSFW+ numbers from the NSFW+ block
        for nm, _ in NSFW_PLUS_KEYS:
            k = f"adult_{nm}"
            num = _np_num(s, nm)
            v = None if _np_state(s) or not num else num.get("value")
            row[k] = None if v is None else round(v, 1)
            row["s"][k], row["m"][k] = row[k], None
            if v is None:
                texts[k] = np_cell(s, nm)
        # NSFW+ /20 = NSFW + Uncensored, its own column beside Overall (INTENT-45)
        tv = None if _np_state(s) else ((s.get("nsfw_plus") or {}).get("total") or {}).get("value")
        row["adult_nsfw_plus"] = None if tv is None else round(tv, 1)
        row["s"]["adult_nsfw_plus"], row["m"]["adult_nsfw_plus"] = row["adult_nsfw_plus"], None
        if tv is None:
            texts["adult_nsfw_plus"] = np_total_cell(s)
        else:
            rg_ = _np_range(s, "nsfw_plus").strip()
            if rg_:
                row["sub_tag"] = {**(row.get("sub_tag") or {}), "adult_nsfw_plus": rg_}
        if hl.get("not_ranked") and (hl.get("S") or {}).get("overall") is None:
            texts["overall"] = hl["not_ranked"]
        row["measured"] = seg45_measured(s)      # the page's data carries the segment tree only, nothing summed
        tb = s.get("segments45") or {}
        row.pop("tiers_cell", None)               # the column is not on the new board; no old-tree text in its data
        # Code and Chat cells are the TOTALS (standard + bonus packs; Overall = Code total + Chat total, owner
        # 2026-11-25); the split is a tag beside the number
        for d in (s.get("scorecard") or {}).get("score_defs") or []:
            if not d.get("in_total", True) or d["key"] not in ("work", "chat"):
                continue
            g = d.get("group") or d["key"]
            sect = tb.get("code" if d["key"] == "work" else "chat")
            if not sect:
                continue
            std = sect["points"]
            tot_ = sect.get("total") if sect.get("total") is not None else std
            row[g] = None if tot_ is None else round(tot_, 1)
            ta_ = sect.get("total_accuracy") or {}
            row["s"][g], row["m"][g] = row[g], (ta_.get("half") if ta_.get("half") is not None
                                                 else sect.get("margin"))
            ct = dict(row.get("cell_text") or {})
            tg = dict(row.get("sub_tag") or {})
            ct.pop(g, None)
            tg.pop(g, None)
            if std is None:
                ct[g] = "pending"
            elif not sect["complete"]:
                ct[g] = _d45_cell(sect)
            if (sect.get("pack_points") or 0) > 0.05 and std is not None:
                tg[g] = (f"= {std:.1f} standard + {sect['pack_points']:.1f} bonus"
                         + (f" (of {float(sect['max_with_packs']):g})" if sect.get("max_with_packs") else ""))
            row["cell_text"], row["sub_tag"] = ct, tg
        row["points_cell"] = " · ".join(_s45line(tb, sec, s.get("marks45")) for sec in ("code", "chat") if tb.get(sec))
        row["cell_text"] = {**(row.get("cell_text") or {}), **texts}
        return
    for nm, sub in ((s.get("scorecard") or {}).get("subtotals") or {}).items():
        if sub.get("kind") == "total":      # two numbers only: the sum stays in report.json, not on the page
            continue
        k = f"adult_{nm}"
        v = sub.get("points")
        if _sub_withheld(s, nm):
            row[k], row["s"][k], row["m"][k] = None, None, None
            texts[k] = ADULT_WITHHELD45
            continue
        row[k] = None if v is None else round(v, 1)
        row["s"][k] = row[k]
        row["m"][k] = None if v is None else subtotal_margin(hl, sub)
        if v is None:
            texts[k] = "not run"
        elif (s.get("scorecard") or {}).get("adult_withheld"):
            tags[k] = f"of {float(sub.get('measured_max') or 0):g} measured, not ranked"
            row["m"][k] = None
        elif subtotal_pending_tag(sub):
            tags[k] = subtotal_pending_tag(sub)
    if tags:
        row["sub_tag"] = {**(row.get("sub_tag") or {}), **tags}
    if texts:
        row["cell_text"] = {**(row.get("cell_text") or {}), **texts}


def _wc_cells(card: dict) -> dict:
    """{"<score>/<part>": {"engagement_rate", "willingness_factor", "capability", "composite", "n", "n_engaged"}}
    for every sub-score that carries a willingness/capability split."""
    out = {}
    for cat, parts in (card.get("ladder") or {}).items():
        for pk, det in (parts or {}).items():
            wc = (det or {}).get("wc")
            if wc and wc.get("n"):
                out[f"{cat}/{pk}"] = {k: wc.get(k) for k in
                                      ("S", "W", "C", "composite", "engagement_rate",
                                       "willingness_factor", "capability", "n", "n_engaged", "sd", "filter",
                                       "same_family_judge")}
                if wc.get("authorship"):
                    out[f"{cat}/{pk}"]["authorship"] = wc["authorship"]
    return out


def _score_label(c: dict, key: str) -> str:
    for d in c.get("score_defs") or []:
        if d["key"] == key:
            return d["label"]
    return key.title()


def _hs(s: dict, key: str) -> dict | None:
    """The html row's headline values keyed like the board columns (the
    score's scorecard group: legacy coding/reason/chat, or work/chat/nsfw)."""
    hl = (s.get("headline") or {}).get(key)
    if not hl:
        return None
    from . import tiers as _tiers
    defs = (s.get("scorecard") or {}).get("score_defs") or _tiers.LEGACY_SCORES
    out = {"overall": hl.get("overall")}
    out.update({d["group"]: hl.get(d["key"]) for d in defs})
    return out


#: the short "how to read this" under the HTML board (4.0 headline boards)
HTML_LEGEND = [
    "Score /20 = Coding /10 + Chat /10. NSFW /10 is shown separately and never counted in Score.",
    "± = half of the 90 % interval of a bootstrap over the cases. A rank like \"3=\" means statistically tied with the model above.",
    "Ladder packs are bonus question sets worth 1 point each, unlocked by ≥ 90 % on the level below, so a category can pass 10. "
    "(set n) = the highest level scored; [+n packs unrun] = packs unlocked but not run yet, counted 0 until they run.",
    "Rows marked old come from an older suite and are awaiting a re-run; a dash = not measured; Room = the score with the end-of-run retry.",
]

#: model that run on this rig / box rather than through a paid API
def _meta_ptype(meta: dict | None) -> str | None:
    """The provider type a hosted-vs-local decision reads from a model's meta: ``"local"`` when the run recorded
    the server as one we own (meta ``hosted: false``, by address or the entry's ``hosted:`` word)."""
    m = meta or {}
    return "local" if m.get("hosted") is False else m.get("provider_type")


LOCAL_PROVIDER_TYPES = {"studioforge", "lmstudio", "llamacpp", "ollama", "vllm", "local"}


def html_ladder(rows: list[dict]) -> list[str]:
    """Ladder-pack status per model, from each row's points breakdown:
    "deepseek-flash: Coding set 1, Chat set 1, NSFW set 1 (maxed)"."""
    out = []
    for r in rows:
        parts = []
        for seg in (r.get("points_cell") or "").split("; "):
            sets = [int(n) for n in re.findall(r"\(set (\d+)\)", seg)]
            m = re.match(r"([A-Za-z][\w ]*?) [\d-]", seg)
            if sets and m:
                parts.append(f"{m.group(1)} set {max(sets)}" + (" (maxed)" if "MAXED" in seg else ""))
        if parts:
            out.append(f"{r['label']}: " + ", ".join(parts))
    return out


def _older_line(labels: list[str], stats: dict, cfg: dict | None = None) -> str:
    """"Older versions on this board: a (newer: b), ..." for models.yaml `supersedes` links, else ""."""
    newer = superseded_map(labels, stats, cfg)
    if not newer:
        return ""
    def nm(l: str) -> str:
        return (stats.get(l) or {}).get("display") or l
    return "Older versions on this board: " + ", ".join(
        f"{nm(l)} (newer: {nm(newer[l])})" for l in rank_labels(labels, stats) if l in newer)


def html_part_labels(stats: dict) -> dict[str, str]:
    """``{"<score>/<part>": "Score / Part"}`` over the sub-scores of EVERY model's profile (names a MAX marker);
    the first model's definition wins when two disagree."""
    out: dict[str, str] = {}
    for s in stats.values():
        for d in (s.get("scorecard") or {}).get("score_defs") or []:
            for p in d.get("parts") or []:
                out.setdefault(f"{d['key']}/{p['key']}", f"{d['label']} / {p['label']}")
    return out


def parts_board45(cfg: dict | None) -> bool:
    """A 4.5 board (its profile builds parts from chains): its component columns are its parts.
    The 4.1 category components (Programs, Code bank, Deep RP, ...) are no part of a 4.5 score and
    are never emitted for it (PUBOUT: the site refuses a column it has no explainer for)."""
    try:
        return bool((_tier_profile(cfg) or {}).get("chains"))
    except Exception:  # noqa: BLE001 - an unreadable profile: the legacy layout
        return False


def _board_parts45(s: dict) -> list[tuple[dict, dict]]:
    """(score def, part) of every part that carries points in a score that makes the total, in board
    order. The adult page has its own two numbers (adult_nsfw / adult_uncensored), never parts here;
    a max-0 part only holds packs."""
    return [(d, p) for d in ((s.get("scorecard") or {}).get("score_defs") or [])
            if d.get("in_total") for p in d.get("parts") or [] if float(p.get("weight") or 0) > 0]


def parts_component_values(s: dict, display: bool = False) -> dict:
    """{part label: share of its standard questions, 0-100 (None = not measured / pending)}. Keyed by the profile's
    label (R5: the html/json component key stays stable when the board relabels a part); ``display`` = keyed by
    the label the board prints."""
    lad = (s.get("scorecard") or {}).get("ladder") or {}
    out = {}
    for d, p in _board_parts45(s):
        v = ((lad.get(d["key"]) or {}).get(p["key"]) or {}).get("std_share")
        out[p["label"] if display else p.get("label_profile", p["label"])] = \
            round(100 * float(v), 1) if v is not None else None
    return out


#: R5 (REVIEW finding 8): the ceiling mark and its rule
CEILING_MARK = "‡"
CEILING_SHARE = 0.95
CEILING_NOTE = ("‡ = at the ceiling: every model on this board measured on the part (at least two) answered at least "
                "95% of its standard questions right, so the part does not separate them here.")
PACKS_NOTE45 = ("Bonus packs = harder sets, given only after ≥ 90% on the part. Their points add on top: Code total = "
                "Code standard + Code bonus, Chat likewise, Overall = Code total + Chat total, and the rank and ties "
                "are on the totals (owner 2026-11-25). The maximum with packs = the standard maximum + every pack "
                "that exists today; it grows as packs are added. A pack that unlocked and has not run counts 0 for "
                "now (named in the notes as owed); the row is ranked on what is measured. The range beside a total "
                "is the total's own; the standard score's range (the one the 2.5% accuracy bar applies to) is "
                "printed after the word \"standard\". NSFW and Uncensored add their own packs the same way and "
                "never enter Overall.")
PACKS_NOTE = ("Pack points = points from ladder packs (harder sets, given only after ≥ 90% on the part), shown apart "
              "from the standard points in the section columns; total = standard + packs, the number Overall adds.")


def _ceil_lab(d: dict, p: dict, ceil: set) -> str:
    return p["label"] + (f" {CEILING_MARK}" if f"{d['key']}/{p['key']}" in ceil else "")


MINUTES_NOTE = ("Minutes = the wall-clock minutes of the run command's recorded phases (generation, judge, report), "
                "summed; — = no single run time (the rows come from several runs, or from before the timing record). Run cost = the rows' recorded cost "
                "at the provider's price; — = run on our own hardware.")


SETTINGS_NOTE = ("Run settings = how the row was run: thinking on or off, the reasoning effort as sent (\"provider "
                 "default\" when none was sent), the median reasoning tokens per answer, and the replies cut at "
                 "their token ceiling (left out of the mean and counted), that did not finish within the full budget of a short-answer "
                 "part (graded as delivered, kept in the score; shown only when there is one) or empty (scored 0). "
                 "\"not recorded\" = a run from "
                 "before these fields.")


CUT_NOTE45 = ("Cut replies, one rule for Code, Chat and NSFW+, applied by the same code to single replies and to "
              "multi-turn sessions: a reply cut at its ceiling is left out of the mean and counted; a session with a "
              "cut turn is left out as a whole; on the short-keyed parts a single reply that used at least 98% of the "
              "full budget did not finish and is graded as delivered (an NSFW+ answer that needs the readers then "
              "scores 0, since readers never read a cut reply); a degenerate cut reply (a loop) is graded as "
              "delivered. If more than a quarter of a part's units, and at least two, are cut, the part reads "
              "\"not measured (n of m replies cut)\" and adds nothing.")


def ceiling_parts45(ranked: list[str], stats: dict) -> set[str]:
    """{"<score>/<part>"} of the in-total parts at the ceiling: at least two published rows (staged validation rows
    and old-suite rows do not count) are measured on the part and every one scored at least CEILING_SHARE of its
    standard questions. Empty on a 4.5 board (a ``segments45`` row): INTENT-45 has no "at the ceiling" flag (ties are
    expected)."""
    if any((stats.get(l) or {}).get("segments45") for l in ranked):
        return set()
    shares: dict[str, list[float]] = {}
    for l in ranked:
        s = stats.get(l) or {}
        if s.get("validation_cc") or (s.get("era") or {}).get("old"):
            continue
        lad = (s.get("scorecard") or {}).get("ladder") or {}
        for d, p in _board_parts45(s):
            v = ((lad.get(d["key"]) or {}).get(p["key"]) or {}).get("std_share")
            if v is not None:
                shares.setdefault(f"{d['key']}/{p['key']}", []).append(float(v))
    return {k for k, vs in shares.items() if len(vs) >= 2 and min(vs) >= CEILING_SHARE - 1e-9}


def _pending_reason(nm) -> str:
    if isinstance(nm, str):
        return f"not run: {nm}"
    if isinstance(nm, (int, float)) and not isinstance(nm, bool):
        return f"not measured: {nm:.0%} of its calls ran out"
    if nm is True:
        return "not measured: too many of its calls ran out"
    return "pending"


#: report.json key that holds the pre-realignment numbers of a 4.5 board row; public writers never copy it
INTERNAL_OLD_TREE = "internal_old_tree"
#: engine-side structures of the old score tree: stripped from a PUBLIC report.json of a realigned model
_OLD_TREE_PATHS = (("scorecard", "score_defs"), ("scorecard", "pending_points"), ("headline", "unscaled"),
                   ("headline", "pts"), ("headline", "unvalidated"), ("headline", "provisional"))


def seg45_measured(s: dict) -> dict:
    """The public ``measured`` block of a realigned (score-tree) row: the segment tree and the two NSFW+ numbers,
    nothing of the pre-realignment parts. ``{code, chat, overall, nsfw_plus}``; every segment carries its points,
    maximum, pending reasons, cut count and the state of the marks that govern it."""
    tb = s.get("segments45") or {}
    out: dict = {}
    for sk in ("code", "chat"):
        sect = tb.get(sk)
        if not sect:
            continue
        out[sk] = {"label": sect["label"], "points": sect["points"], "max": sect["max"],
                   "measured_max": sect["measured_max"], "pending_points": sect["pending_points"],
                   "pack_points": sect.get("pack_points"), "margin": sect.get("margin"),
                   "segments": {k: {"label": g["label"], "points": g["points"], "max": g["max"],
                                    "measured_max": g["measured_max"], "pending_points": g["pending_points"],
                                    "pending": [{"unit": p["unit"], "points": p["points"], "reason": p["reason"]}
                                                for p in g["pending"]],
                                    "margin": g.get("margin"), "cut": g.get("cut"),
                                    "marks": {f: (g.get("marks") or {}).get(f) for f in ("state", "reason")},
                                    "notes": list(g.get("notes") or []),
                                    # R37/R38: the part's own split (standard + its bonus packs)
                                    "pack_points": g.get("pack_points"),
                                    "total": (None if g["points"] is None
                                              else round(float(g["points"]) + float(g.get("pack_points") or 0.0), 4))}
                                for k, g in sect["segments"].items()},
                   "pack_sources": sect.get("pack_sources")}
    ov = tb.get("overall")
    if ov:
        out["overall"] = {f: ov.get(f) for f in ("points", "max", "measured_points", "measured_max", "pack_points",
                                                  "margin", "pending_reasons")}
    # owner 2026-11-25 (R37/R38, totals45): the totals are the headline numbers; ``points`` stays the standard points
    # for one release (old readers), ``standard_points`` names it from now on
    for sk, g in (s.get("totals45") or {}).items():
        if sk in out:
            out[sk].update(totals_fields45(g))
    npb = s.get("nsfw_plus") or {}
    nums = npb.get("numbers") or {}
    out["nsfw_plus"] = {nm: ({"label": n["label"], "points": n.get("value"), "of": n.get("of", 10),
                              "partial": n.get("partial"), "pending": n.get("pending"),
                              "segments": {k: {"label": g["label"], "points": g.get("value"), "max": g["max"],
                                               "pending": g.get("pending"), "cut": g.get("cut"),
                                               "marks": {f: (g.get("marks") or {}).get(f) for f in ("state", "reason")}}
                                           for k, g in (n.get("segments") or {}).items()}}
                             if n else None)
                        for nm, n in (nums or {}).items()} if nums else {"state": npb.get("state") or "not run",
                                                                         "reason": npb.get("reason")}
    if nums:
        # one local judge (owner 2026-11-21): NSFW+ /20, the judge, its unread units, the same-family flag and the
        # judge's own accuracy on the constructed sets (data; never a gate)
        t = npb.get("total") or {}
        out["nsfw_plus"]["total"] = {f: t.get(f) for f in ("label", "of", "value", "partial", "complete", "pending")}
        j = npb.get("judge45") or {}
        out["nsfw_plus"]["judge"] = {f: j.get(f) for f in ("model", "prompt_sha", "flag_prompt_sha", "class_units",
                                                           "class_judged", "class_missing", "flag_units",
                                                           "flags_read", "flags_missing", "other_judge",
                                                           "other_units", "other_judges_ignored")}
        out["nsfw_plus"]["same_family"] = bool(npb.get("same_family"))
        out["nsfw_plus"]["self_graded"] = bool(npb.get("self_graded"))
        out["nsfw_plus"]["judge_check"] = npb.get("judge_check")
    if nums and npb.get("totals45"):
        for nm, g in npb["totals45"].items():
            tgt = out["nsfw_plus"].get("total") if nm == "nsfw_plus" else out["nsfw_plus"].get(nm)
            if isinstance(tgt, dict):
                tgt.update(totals_fields45(g))
    if s.get("accuracy45"):
        # INTENT-45: each model's own accuracy range per score and subcomponent (accuracy45.py); the item margin is
        # the second, labelled figure
        out["accuracy"] = s["accuracy45"]
    return out


def totals_fields45(g: dict) -> dict:
    """The public fields of one total (totals45.group_total): what the site and the Hugging Face export read."""
    return {"standard_points": g.get("standard"), "bonus_points": g.get("bonus"), "total": g.get("total"),
            "total_points": g.get("total"),            # alias of total: the site's import reads this name
            "standard_max": g.get("max"), "max_with_packs": g.get("max_with_packs"),
            "total_accuracy": g.get("accuracy"), "standard_accuracy": g.get("standard_accuracy"),
            "packs": [{k: p.get(k) for k in ("part", "name", "kind", "unlock", "figure", "state", "points", "max",
                                             "lost_rows") if p.get(k) is not None or k in ("figure", "unlock")}
                      for p in g.get("packs") or []],
            "packs_owed": list(g.get("owed") or [])}


#: a realigned model's PUBLIC report.json drops these whole top-level blocks: the old score tree (scorecard with
#: its parts, subtotals, weights, columns and components), the per-part chain / subset / ladder / tier stats keyed by
#: old part ids, the old adult category stats, and the mark-check bookkeeping (never board data)
_OLD_TREE_TOP = (INTERNAL_OLD_TREE, "scorecard", "chain_stats", "subset_stats", "ladder_stats", "tier_stats",
                 "tier_states", "nsfw", "marks_input45")
#: headline keys kept for a realigned model, and the section keys their per-key dicts keep
_HL_KEEP = ("S", "P", "T1", "m", "sort", "rank", "not_ranked", "overall_of", "class",
            "overall_mode", "tie", "tie_group", "total45", "clearly_better")
_HL_SECTIONS = ("work", "chat", "overall")
#: an old-tree part id (``work/c1_implied``, ``nsfw/n1_scene``, a bare ``n6a_craft``) or an old NSFW part label;
#: the segment tree's own ids (``segment:chat/character``) are not old tree
OLD_TREE_RE = re.compile(r"(?<![:\w])(?:work|chat|nsfw|coding|reason)/[a-z][a-z0-9_]*(?:#\d+)?"
                         r"|\bn\d+[ab]?_[a-z]+\b|\bu[1-4]_(?:will|straight|know|ground)\b"
                         r"|Scene quality|Writing craft|Sensation|Peak scene")


def _scrub_old_tree(x):
    """Drop every dict entry keyed by an old part id and every string or list item that names one."""
    if isinstance(x, dict):
        return {k: _scrub_old_tree(v) for k, v in x.items()
                if not OLD_TREE_RE.search(str(k)) and not (isinstance(v, str) and OLD_TREE_RE.search(v))}
    if isinstance(x, list):
        return [_scrub_old_tree(v) for v in x if not (isinstance(v, str) and OLD_TREE_RE.search(v))]
    return x


def public_models45(models: dict) -> dict:
    """The ``models`` of a PUBLIC report.json: a realigned model carries the segment tree only. Its old score tree
    (``_OLD_TREE_TOP``), the headline's per-part values and maxima (only the Code, Chat and Overall cells stay, and
    those already hold the segment tree's numbers) and anything else keyed by or naming an old part id are dropped."""
    out = {}
    for l, m in models.items():
        if not isinstance(m, dict) or not m.get("segments45"):
            out[l] = m
            continue
        m = {k: v for k, v in m.items() if k not in _OLD_TREE_TOP}
        hl = m.get("headline")
        if isinstance(hl, dict):
            m["headline"] = {k: ({s_: v[s_] for s_ in _HL_SECTIONS if s_ in v} if isinstance(v, dict) else v)
                             for k, v in hl.items() if k in _HL_KEEP}
        out[l] = _scrub_old_tree(_plain_units(m))
    return out


def _plain_units(m: dict) -> dict:
    """The segment tree without internal part ids: a segment's per-part ``units`` list is dropped (its points,
    maximum and margin stay) and a pending entry names its part by the segment's own words."""
    from . import segments45 as _s45
    names = m.get("part_names45") or {}

    def segs(tree):
        for sk in ("code", "chat"):
            for g in (((tree or {}).get(sk) or {}).get("segments") or {}).values():
                yield g
    m = copy.deepcopy({k: v for k, v in m.items() if k != "part_names45"})
    for tree in (m.get("segments45"), m.get("measured")):
        for g in segs(tree):
            g.pop("units", None)
            for p_ in g.get("pending") or []:
                if isinstance(p_, dict) and p_.get("unit"):
                    p_["unit"] = _s45.plain_text(str(p_["unit"]), names)
                if isinstance(p_, dict) and p_.get("reason"):
                    p_["reason"] = _s45.plain_text(str(p_["reason"]), names)
    return m


def points_measured(s: dict) -> dict:
    """PUBOUT 4.5: each score's numbers as separate fields, so a reader never parses a sentence:
    ``{score: {points, standard_points, max, measured_max, pending_points, pending: [{part, label,
    points, reason}]}}`` plus the adult ``subtotals`` as the scorecard holds them. Read from the
    scorecard and headline only (display: no number is computed anew)."""
    sc = s.get("scorecard") or {}
    hl = s.get("headline") or {}
    P = hl.get("P") or {}
    nm = hl.get("not_measured") or {}
    out: dict = {}
    for d in sc.get("score_defs") or []:
        key = d.get("key")
        parts = [p for p in d.get("parts") or [] if float(p.get("weight") or 0) > 0]
        if not key or not parts:
            continue
        mx = sum(float(p["weight"]) for p in parts)
        got = [p for p in parts if P.get(f"{key}/{p['key']}") is not None]
        pend = [p for p in parts if P.get(f"{key}/{p['key']}") is None]
        # rc4 (no_scale): the cell's own measured maximum (scored pack points included, so points never exceed it)
        # and the cell's named reasons, one per part
        us = (hl.get("unscaled") or {}).get(key)
        why = {x["part"]: x["why"] for x in merge_pending((us or {}).get("pending") or [])}
        out[key] = {
            "points": P.get(key, sc.get(key)),
            "standard_points": sum(float(P[f"{key}/{p['key']}"]) for p in got) if got else None,
            "max": mx,
            "measured_max": float(us["of"]) if us is not None else sum(float(p["weight"]) for p in got),
            "pending_points": sum(float(p["weight"]) for p in pend),
            "pending": [{"part": p["key"], "label": p["label"], "points": float(p["weight"]),
                         "reason": why.get(p["key"]) or _pending_reason(nm.get(f"{key}/{p['key']}"))}
                        for p in pend],
        }
    subs = sc.get("subtotals") or {}
    if subs:
        out["subtotals"] = {k: {f: v.get(f) for f in ("label", "points", "max", "measured_max", "pending")}
                            for k, v in subs.items() if isinstance(v, dict)}
    return out


def _tree_unit_weights(stats: dict) -> dict:
    """{part id: its points in the segment tree} from the realigned rows' own segment tables."""
    out: dict = {}
    for s in stats.values():
        for sk in ("code", "chat"):
            for g in ((((s.get("segments45") or {}).get(sk) or {}).get("segments")) or {}).values():
                for u in g.get("units") or []:
                    out.setdefault(str(u.get("id")), u.get("weight"))
    return out


def html_components(stats: dict, cfg: dict | None = None) -> list[dict]:
    """The component columns of the HTML board, in report.md order:
    ``[{label, side: "chat"|"coding"}]``. On a 4.5 board: its parts (``key`` = "<score>/<part>")."""
    if parts_board45(cfg):
        out, seen = [], set()
        ceil = ceiling_parts45(list(stats), stats)
        for s in stats.values():
            for d, p in _board_parts45(s):
                key_lab = p.get("label_profile", p["label"])
                if key_lab not in seen:
                    seen.add(key_lab)
                    k = f"{d['key']}/{p['key']}"
                    # R5: ``label`` = the stable component key; ``display`` = the name printed (+ the ceiling mark)
                    out.append({"label": key_lab, "side": d["key"], "key": k, "max": float(p["weight"]),
                                **({"display": p["label"] + (f" {CEILING_MARK}" if k in ceil else "")}
                                   if p["label"] != key_lab or k in ceil else {}),
                                **({"ceiling": True} if k in ceil else {}),
                                **{f: p[f] for f in ("id", "tag", "note") if p.get(f)}})
        return out
    sc = scoring_config(cfg)
    for s in stats.values():            # the weights the scorecards were built with
        sc = (s.get("scorecard") or {}).get("weights") or sc
        break
    # legacy: Reason's components sit on the Coding side
    return [{"label": component_label(k), "side": "coding" if g == "reason" else g}
            for g in sc for k in sc[g]]


def html_scores(stats: dict, public: bool = False) -> tuple[list[dict] | None, list[dict] | None]:
    """(main score columns, component sides) of a profile that declares its
    ``scores:`` (tiers.scores); (None, None) = the legacy layout."""
    for s in stats.values():
        defs = (s.get("scorecard") or {}).get("score_defs")
        if defs and [d["key"] for d in defs] != ["code", "reason", "chat"]:
            cols = [{"key": d["group"], "label": score_label(d, public)} for d in defs]
            return cols, [{"key": d["group"], "label": d["label"]} for d in defs]
        break
    return None, None


#: board-script comment lines written for the 4.1 adult page; left out of a 4.5 render
SCRIPT_OLD_COMMENT_RE = re.compile(r"\n[ \t]*// rc3 adult page:[^\n]*")


def render_report_html(labels: list[str], stats: dict, cfg: dict | None = None,
                       archived_note: str | None = None) -> str:
    """report.html: the element-filter board (templates/board)."""
    from .templates.board import render_html
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    revs = sorted({r for l in labels if not (stats[l].get("era") or {}).get("old")
                   for r in stats[l].get("revisions", [])})
    used = sorted({j for l in labels
                   for j in ((stats[l].get("coverage") or {}).get("judges")
                             or stats[l].get("judge_models") or [])})
    judge = ", ".join(short_model(j) for j in used) or short_model(_primary_judge(cfg))
    subtitle = " · ".join(x for x in (
        f"{len(labels)} models", *_suite_head({l: stats[l] for l in labels}, cfg),
        f"judge {judge}" if judge and judge != "-" else None) if x)
    footer, legend, versus, ladder = [], [], [], []
    prof45 = _tier_profile(cfg) if any(stats[l].get("suite45") for l in labels) else None
    # the status banner / Class hiding follow the board's profile, routed or not (rc2 has no routing)
    prof_b = prof45 or (_tier_profile(cfg) if labels else None)
    banner = banner45(prof_b, rank_labels(labels, stats), stats) if prof_b else []
    if mixed_judges45(labels, stats):
        banner = list(banner) + [MIXED_JUDGES_LINE]
    hide_class = class_hidden45(prof_b)
    if labels and not routed(prof45):
        # (a routed board: the 4.1 tier legend does not describe its numbers; scale45_footnote below)
        footer.append(scorecard_footnote(_cards(labels, stats, cfg), cfg, markdown=False))
    if routed(prof45):
        # a routed board: the section-scale legend, never the 4.1 bootstrap / Class / Room texts
        show_class = not class_hidden45(prof45)
        foot45 = [scale45_footnote(prof45, [stats[l].get("scale45") for l in labels]).strip("*"),
                  class_footnote(prof45, show_class).strip("*"),
                  maxed45_footnote(prof45, show_class).strip("*"), COST45_NOTE]
        if any(stats[l].get("validation_cc") for l in labels):
            foot45.append(STAGED_CC_FOOTNOTE)
        footer += foot45
        legend = list(foot45)
        versus = [stats[l]["headline"]["vs_above"]["text"] for l in rank_labels(labels, stats)
                  if (stats[l].get("headline") or {}).get("vs_above")]
    elif any(stats[l].get("headline") for l in labels):
        from . import tiers as _tiers
        footer.append(headline_footnote(_tiers.scores(_tier_profile(cfg))).strip("*").replace("**", ""))
        footer.append(class_footnote(_tier_profile(cfg), not hide_class).strip("*"))
        # the board proper: short sections under the table; the long footnotes
        # above stay behind the closed "More detail" block
        legend = HTML_LEGEND + [class_footnote(_tier_profile(cfg), not hide_class).strip("*")]
        versus = [stats[l]["headline"]["vs_above"]["text"] for l in rank_labels(labels, stats)
                  if (stats[l].get("headline") or {}).get("vs_above")]
        ladder = html_ladder(html_rows(labels, stats, cfg))
        older = _older_line(labels, stats, cfg)
        if older:
            legend = legend + [older]
    footer += [line.strip("*") for line in (archived_note or "").splitlines()]
    footer.append(" · ".join(x for x in (
        (suite45_head(prof45, _current_revision(cfg)) if routed(prof45) else
         f"Suite revision {', '.join(revs or [_current_revision(cfg) or '?'])}"
         + ("" if revs else " (no model re-run under it yet)")),
        f"judge actually used: {', '.join(used)}" if used else None,
        f"rendered {now}") if x))
    scores, sides = html_scores({l: stats[l] for l in labels}, public=bool(_hidden(cfg)))
    if routed(prof45) and scores:
        # a separate section with no cell in the composition is not a column (shown_defs45)
        keep = {d.get("group") or d["key"] for d in shown_defs45(prof45)}
        scores = [c for c, sd in zip(scores, sides) if sd["key"] in keep]
        sides = [sd for sd in sides if sd["key"] in keep]
    rows = html_rows(labels, stats, cfg)
    seg45 = any((stats.get(l) or {}).get("segments45") for l in labels)
    if seg45 and prof_b:
        # the realigned board's own words: the 3.9 tier legend and its old section names do not describe it
        n_tail = len((archived_note or "").splitlines()) + 1
        footer = [segment_maxima_note(prof_b).strip("*")] + footer[-n_tail:]
        stl = scorer_stamp_note(labels, stats)
        if stl:
            footer.append(stl)
        footer += [SETTINGS_NOTE, CUT_NOTE45]
        anc = anchors_lines()
        if anc:
            footer.append(f"{ANCHORS_NOTE} " + "; ".join(anc))
        legend = seg45_legend(labels, stats) + [class_footnote(prof_b, not hide_class).strip("*")]
    comps_html = html_components({l: stats[l] for l in labels}, cfg)
    part_labels_html = html_part_labels({l: stats[l] for l in labels})
    if seg45 and prof_b:
        # the public page carries the score tree only: component columns and part labels of the pre-realignment
        # parts (old NSFW section, judged long scenes, ...) are left out
        keep_ = tree_part_keys(prof_b, stats, labels)
        tree_w = _tree_unit_weights({l: stats[l] for l in labels})
        # a component's maximum is its points in the segment tree, never the pre-realignment part weight
        comps_html = [{**c, "max": tree_w.get(str(c.get("key")).split("/", 1)[-1])} for c in comps_html
                      if c.get("key") in keep_]
        sec_names = {"work": "Code", "chat": "Chat"}          # the realigned section names, not the 4.1 ones
        part_labels_html = {}
        for s_ in (stats[l] for l in labels):
            for d_ in (s_.get("scorecard") or {}).get("score_defs") or []:
                for p_ in d_.get("parts") or []:
                    k_ = f"{d_['key']}/{p_['key']}"
                    if k_ in keep_:
                        part_labels_html.setdefault(k_, f"{sec_names.get(d_['key'], d_['label'])} / {p_['label']}")
        sides = [{**sd_, "label": sec_names.get(sd_["key"], sd_["label"])} for sd_ in sides] if sides else sides
        kept_labels = {c["label"] for c in comps_html}
        rows = [{**r, "components": {k: v for k, v in (r.get("components") or {}).items() if k in kept_labels},
                 **({"comp_text": {k: v for k, v in r["comp_text"].items() if k in kept_labels}}
                    if isinstance(r.get("comp_text"), dict) else {})} for r in rows]
    if seg45 and scores:                      # realignment names (spec section 6); Everyday moved into Chat
        _ch = {"work": head45("Code", "code", stats, 10), "coding": head45("Code", "code", stats, 10),
               "chat": head45("Chat", "chat", stats, 10)}
        scores = [{**c, "label": _ch.get(sd["key"], c["label"])}
                  for c, sd in zip(scores, sides)] if len(scores) == len(sides) else scores
    from . import assistant45 as _a45
    a45 = (_a45.block(prof_b) if prof_b and prof_b.get("chains") and not routed(prof_b) and not seg45 else None)
    if a45:
        # Assistant basics (checked): a text column outside Overall; the row's data carries the block as `assistant45`
        valid = _a45.validated(prof_b)
        ceil_ = _a45.ceiling(labels, stats) if valid else False
        rows = [{**r, "assistant45": (stats.get(r.get("label")) or {}).get("assistant45"),
                 "cell_text": {**(r.get("cell_text") or {}),
                               "assistant": _a45.cell((stats.get(r.get("label")) or {}).get("assistant45"),
                                                      valid, ceil_)}}
                for r in rows]
        extra = [] if valid else [_a45.pending_note()]
        footer = footer + [_a45.NOTE] + extra
        legend = legend + [_a45.NOTE] + extra
    jn = judged_part_notes(prof_b) if (prof_b and not seg45) else []
    if jn:
        footer = footer + jn
        legend = legend + jn
    if hide_class:
        rows = [{**r, "class": None, **({"class_hidden": True} if parts_board45(cfg) else {})} for r in rows]
    sub_key, subs = (None, []) if routed(prof45) else adult_subtotals(rank_labels(labels, stats), stats)
    if seg45 and not routed(prof45):
        sub_key = next((d["key"] for d in ((stats[labels[0]].get("scorecard") or {}).get("score_defs") or [])
                        if not d.get("in_total", True)), sub_key)
        subs = list(NSFW_PLUS_KEYS) if sub_key else []
    if sub_key and scores:
        # rc3 adult page: its two numbers (each out of 10) in place of the score's one column
        grp = next((d.get("group") or d["key"] for d in ((stats[labels[0]].get("scorecard") or {})
                    .get("score_defs") or []) if d["key"] == sub_key), sub_key)
        scores = [x for c in scores
                  for x in ([{"key": f"adult_{nm}", "label": (head45(sl, nm, stats, 10) if seg45 else f"{sl} (/10)")}
                             for nm, sl in subs]
                            if c["key"] == grp else [c])]
    if a45 and scores is not None:
        scores = list(scores) + [{"key": "assistant", "label": a45["label"]}]
    if seg45:
        pass
    from . import display45 as _d45h
    html = render_html(rows, subtitle=subtitle, banner=banner, hide_class=hide_class,
                       overall_label=(head45("Overall", "overall", stats, 20) if seg45 else None),
                       beside_overall=([{"key": "adult_nsfw_plus", "label": head45("NSFW+", "nsfw_plus", stats, 20)}]
                                       if (seg45 and sub_key and scores) else None),
                       board_title=("CrucibleForge: Chat &amp; Coding" if seg45 else None),
                       omit_columns=(("tiers_cell",) if seg45 else ()),
                       extra_html=((nsfw_plus_html(labels, stats) + _d45h.held_back_html(held_back45(labels, stats, prof_b)))
                               if seg45 else ""),
                       components=comps_html,
                       footer=footer,
                       forbidden=(_hidden(cfg) or {}).get("terms"),
                       scores=scores, sides=sides, legend=legend, versus=versus, ladder=ladder,
                       part_labels=part_labels_html)
    if seg45:
        # the shared board script keeps its 4.1 comments (the 4.1 render is pinned word for word); a 4.5 page
        # carries no old-tree wording, so they are dropped here
        html = SCRIPT_OLD_COMMENT_RE.sub("", html)
    return html


def tree_part_keys(prof: dict, stats: dict, labels: list[str]) -> set[str]:
    """``{"work/c1_implied", ...}``: the headline parts the score tree reads (a part named in ``segments45``; the
    long scenes and fix tasks are not headline parts)."""
    from . import segments45 as _s45
    names = {p for sk in _s45.SECTIONS for g in ((_s45.tree(prof) or {}).get(sk) or {}).values()
             if isinstance(g, dict) for p, spec in (g.get("parts") or {}).items() if not isinstance(spec, dict)}
    return {k for l in labels for k in ((stats[l].get("headline") or {}).get("max") or {})
            if k.split("/", 1)[-1] in names and not k.startswith("nsfw/")}


# ------------------------------------------------------------- generate

def scorecard_section(md: str) -> str:
    """Just the '## Scorecard' block of a report.md text."""
    if "## Scorecard" not in md:
        return md
    body = md.split("## Scorecard", 1)[1]
    body = body.split("\n## ", 1)[0]
    return ("## Scorecard" + body).rstrip() + "\n"


def public_dir():
    return results_dir() / "public"


def generate(labels_arg: str | None = None, write: bool = True,
             public: bool = False, out_dir=None, cfg: dict | None = None) -> str:
    """Render the board (see :func:`_generate`) inside one :func:`report_scope`.

    The release flip (bench45 = DEFAULT_PROFILE) must not change a 4.1 board (lead 2026-11-15): when nothing
    chose the profile (no ``--profile`` / set_active, no ``cfg["_profile"]``) and every stored row is a 4.1 row
    (profile ARCHIVE_PROFILE, or none = 4.1), the board is built for ARCHIVE_PROFILE, as it was before the flip.
    A folder holding any other profile's row is that profile's board, as before."""
    from . import profiles as _profiles
    archive = (not (cfg or {}).get("_profile") and _profiles._ACTIVE[0] is None
               and _profiles.DEFAULT_PROFILE != _profiles.ARCHIVE_PROFILE and _archive_rows_only(labels_arg))
    prev = _profiles._ACTIVE[0]
    if archive:
        _profiles.set_active(_profiles.ARCHIVE_PROFILE)
    tok = _PUBLIC_WORDS.set(bool(public))
    try:
        with report_scope():
            return _generate(labels_arg, write, public, out_dir, cfg)
    finally:
        _PUBLIC_WORDS.reset(tok)
        if archive:
            _profiles.set_active(prev)


def _archive_rows_only(labels_arg: str | None) -> bool:
    """Whether the results dir holds rows and every one of them is a 4.1 row (profile ARCHIVE_PROFILE or none)."""
    from .profiles import ARCHIVE_PROFILE
    want = {x.strip() for x in (labels_arg or "").split(",") if x.strip()}
    seen = False
    for path in sorted(results_dir().glob("transcripts_*.jsonl")):
        if want and path.stem[len("transcripts_"):] not in want:
            continue
        try:
            with open(path, encoding="utf-8") as f:
                for line in f:
                    if not line.strip():
                        continue
                    try:
                        prof = json.loads(line).get("profile")
                    except ValueError:
                        continue
                    if prof not in (None, ARCHIVE_PROFILE):
                        return False
                    seen = True
        except OSError:
            continue
    return seen


def _generate(labels_arg: str | None = None, write: bool = True,
              public: bool = False, out_dir=None, cfg: dict | None = None) -> str:
    """Render the board for ``labels_arg`` (or every model in results/).

    ``write=True`` (the CLI default) (re)writes report.md, failures.md,
    report.json and report.html. ``write=False`` only returns the markdown —
    for a caller that wants a scorecard for a subset without touching the
    shared board (the V2 run-report).

    ``public=True`` builds the board for an audience other than the operator
    (anything that may leave the box): private categories (profiles.
    private_scope) are left out of the rows, coverage and weights, the
    rendered texts are re-checked for every private term, and on any hit
    :class:`PrivateLeak` is raised with NOTHING written. Files go to
    ``out_dir`` (default ``results/public/``), never over the local board."""
    # load_config() re-points the results dir at <config dir>/results; the
    # board must be built from (and written to) the dir the caller chose
    here = results_dir()
    if cfg is None:
        # ``cfg`` = what the CLI loaded from --config. Reloading the DEFAULT
        # config here built the board (profile, weights, private scope of a
        # --public board) from a different models.yaml than the one asked for
        try:
            cfg = load_config()
        except Exception:
            cfg = None
        finally:
            set_results_dir(here)
    if public:
        if (cfg or {}).get(STAGED_CC_KEY):
            # the staged-board opt-in never reaches a publish-shaped render
            raise ClaudeCodeOnBoard("--staged-claude-code is for a staged board only; a public render "
                                    "refuses it (claude_code rows never leave the box)")
        cfg = {**(cfg or {}), "_audience": PUBLIC}
        check_private_scope(_hidden(cfg))
    labels, stats, dropped = board_stats(labels_arg, cfg)
    archived = _archived_note()
    if dropped:
        extra = f"*{dropped} model(s) in results/ have no current benchmark run and are not shown.*"
        archived = f"{archived}\n{extra}" if archived else extra
    if LAST_LEFT_OFF:
        extra = "\n".join(f"*{l}: left off this board ({why}).*" for l, why in sorted(LAST_LEFT_OFF.items()))
        archived = f"{archived}\n{extra}" if archived else extra
    if not labels:
        md = ("# CrucibleForge — Chat & Coding\n\nNo current benchmark results. Run "
              "`uv run crucibleforge all --models <label> --fresh --yes`.\n"
              + (f"\n{archived}\n" if archived else ""))
    else:
        md = render_markdown(labels, stats, cfg, archived_note=archived)
    if public:
        scope = _hidden(cfg)
        leaks = public_leaks(md, scope)
        if leaks:
            raise PrivateLeak(f"public report.md would contain private terms: {leaks}")
    if labels and parts_board45(cfg):
        for l in labels:
            if stats[l].get("segments45"):
                stats[l][INTERNAL_OLD_TREE] = {"measured": points_measured(stats[l])}
                stats[l]["measured"] = seg45_measured(stats[l])
            else:
                stats[l]["measured"] = points_measured(stats[l])
    if write:
        out = {
            "report.md": md,
            "failures.md": (render_failures(labels, stats, cfg) if labels
                            else "# CrucibleForge — failures\n\nNo results.\n"),
            "report.json": json.dumps({"generated": datetime.now().isoformat(),
                                       # label / run_ids are the scorecard's inputs (judge agreement per model and
                                       # run), not board data: left out so report.json stays as it was
                                       "models": (lambda ms: public_models45(ms) if public else ms)(
                                           {l_: {k: v for k, v in s_.items() if k not in _CARD_INPUTS}
                                            for l_, s_ in stats.items()}),
                                       "separation": dict(LAST_SEPARATION)},
                                      indent=2, default=str),
            "report.html": render_report_html(labels, stats, cfg, archived),
        }
        if public:
            leaks = {name: public_leaks(text, scope) for name, text in out.items()}
            leaks = {k: v for k, v in leaks.items() if v}
            if leaks:       # fail closed: nothing is written
                raise PrivateLeak(f"public artifacts would contain private terms: {leaks}")
            from pathlib import Path
            target = Path(out_dir) if out_dir else public_dir()
        else:
            target = results_dir()
        target.mkdir(parents=True, exist_ok=True)
        for name, text in out.items():
            (target / name).write_text(text, encoding="utf-8")
    return md


def main(argv=None):
    parser = argparse.ArgumentParser(description="Generate the benchmark board")
    parser.add_argument("--models", default=None)
    parser.add_argument("--public", action="store_true")
    args = parser.parse_args(argv)
    md = generate(args.models, public=args.public)
    print(scorecard_section(md))
    print(f"wrote {report_md_path()}, {failures_md_path()}, {report_html_path()}")


if __name__ == "__main__":
    main()
