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
import re
import statistics
from datetime import datetime

from .api import row_answer
from .config import results_dir, load_config, load_transcripts, set_results_dir
from .graders import refusal_heuristic

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
# the truncation-rate denominator: genuine creative rows
CREATIVE_RUBRICS = ("rp_single", "rp_multi", "nsfw", "rp_session", "rp_scene",
                    "nsfw_craft", "erp_session", "story")
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
# given a weight — see docs/CASES.md "Adding cases"
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
            _PROFILE_LABELS = dict(load_profile(DEFAULT_PROFILE).get("labels") or {})
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


def _hidden(cfg: dict | None) -> dict | None:
    if (cfg or {}).get("_audience") != PUBLIC:
        return None
    from .profiles import private_scope
    return private_scope(cfg)


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
        prof = load_profile(DEFAULT_PROFILE, cfg)
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


#: components measured by deterministic graders only (never wait for the judge)
DETERMINISTIC_KEYS = {"coding", "tooluse", "instruct", "reasoning", *("codebank", "codeagent",
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
    return max(n, blk)


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
    defs = _tiers.scores(prof)
    _, pw = _declared_weights(prof, None, None)
    partial = frozenset(prof.get("partial_credit") or ())
    c1 = scorecard(s, {g: w for g, w in sc.items()}, partial=partial)
    subsets = s.get("subset_stats") or {}
    lstats = s.get("ladder_stats") or {}
    blocked = set(s.get("packs_blocked") or ())    # 4.1 F1: billing-refused packs
    sets = _ladder_sets(prof)
    unlock = _ladder.unlock_share(prof)
    pending = bool(s.get("pending_judge"))
    # review S3: standard cases this model has no row for yet (a run cut by
    # bench-all --until, or still in progress). A part they belong to is
    # PARTIAL: never extrapolated, and an in-total category with a partial
    # part leaves Overall empty (the model is unranked until complete).
    missing = set(s.get("missing_cases") or ())
    prof_cases = prof.get("cases") or {}
    card = dict(c1)
    card["units"] = "points"
    card["score_defs"] = defs
    card["parts"], card["ladder"], card["t1"], card["labels"] = {}, {}, {}, {}
    totals: dict[str, float | None] = {}
    partial_cats: list[str] = []
    for d in defs:
        g, key = d["group"], d["key"]
        parts_pts, detail = {}, {}
        std_pts = earned = meas = total_max = no_items = 0.0
        reached = 0
        partial_parts = []
        cat_blocked = False
        for p in d.get("parts") or []:
            pkey = f"{key}/{p['key']}"
            weights = {k: v for k, v in (pw.get(key, {}).get(p["key"]) or {}).items()
                       if k in (sc.get(g) or {}) and v}
            total_max += p["weight"]
            if not weights:
                no_items += p["weight"]      # nothing in the profile: scale over it
            elif missing:
                cats = set().union(*(_component_categories(k) for k in weights))
                ids = (set(p["cases"]) if p.get("cases")
                       else {c for cat, cids in prof_cases.items() if cat in cats
                             for c in cids or []})
                if ids & missing:
                    partial_parts.append(p["key"])
            src = subsets.get(pkey) if p.get("cases") or d.get("custom") else s
            if weights:
                cats = set().union(*(_component_categories(k) for k in weights))
                part_ids = (set(p["cases"]) if p.get("cases")
                            else {c for cat, cids in prof_cases.items() if cat in cats
                                  for c in cids or []})
                n_ex = _part_excluded(src, part_ids, weights)
            else:
                n_ex = 0
            share = None
            if src is not None and weights and not (src.get("pending_judge") and _judged_weights(weights)):
                v = scorecard(src, {g: weights}, partial=partial)[g]
                share = None if v is None else v / 100.0
            seq = sets.get(pkey) or []
            set_shares: dict[int, float | None] = {}
            set_ex: dict[int, int] = {}
            for st_ in seq:
                ls = lstats.get(f"{pkey}#{st_['n']}")
                sw = _set_weights(prof, st_["cases"], p.get("scoring"))
                v = None
                if f"{pkey}#{st_['n']}" in blocked:
                    cat_blocked = True       # provider credit: not run, not 0
                elif ls is not None and sw and not (ls.get("pending_judge") and _judged_weights(sw)):
                    v = scorecard(ls, {g: sw}, partial=partial)[g]
                set_shares[st_["n"]] = None if v is None else v / 100.0
                set_ex[st_["n"]] = _part_excluded(ls, None, sw or {})
            # 4.0 (fresh-clone smoke): a part with standard cases still missing
            # never unlocks its packs — two of six CL rows at 100% opened the
            # Functions pack on a partial run
            gate = None if p["key"] in partial_parts else share
            states = _ladder.states(gate, set_shares, seq, unlock)
            got = sum(set_shares.get(n) or 0.0 for n, stt in states.items() if stt == "scored")
            top = max([st_["level"] for st_ in seq if states.get(st_["n"]) == "scored"], default=0)
            pts = (None if share is None else
                   share * p["weight"] + got if gate is None else
                   _ladder.points(share, p["weight"], set_shares, seq, unlock))
            parts_pts[p["key"]] = pts
            # MAXED: >= the unlock share on the standard level and on every
            # pack that exists — nothing harder left to give (a new pack is due)
            maxed = (gate is not None and gate >= unlock
                     and all(set_shares.get(x["n"]) is not None and set_shares[x["n"]] >= unlock
                             for x in seq))
            # 3.10 D3 (review H): a part scored on fewer rows than it has (an
            # unparsable verdict, retried by the nightly, still failed) is
            # "partial (n excluded)", never MAXED-looking
            n_ex_all = n_ex + sum(n for x, n in set_ex.items()
                                  if states.get(x) == "scored")
            maxed = _maxed_unless_excluded(maxed, n_ex_all)
            if n_ex_all:
                card.setdefault("excluded", {})[pkey] = n_ex_all
            detail[p["key"]] = {"std_share": share, "unlock_share": gate,
                                "max": p["weight"], "points": pts,
                                "maxed": maxed, "n_excluded": n_ex_all,
                                "sets": [{"n": st_["n"], "level": st_["level"],
                                          "state": states[st_["n"]], "share": set_shares[st_["n"]],
                                          "n_excluded": set_ex.get(st_["n"], 0)}
                                         for st_ in seq],
                                "reached": top}
            if share is not None:
                std_pts += share * p["weight"]
                meas += p["weight"]
                earned += got
                reached = max(reached, top)
        card["parts"][key] = parts_pts
        card["ladder"][key] = detail
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
            scale_over = total_max if _BOOT.get() is not None else meas + no_items
            std = std_pts * (scale_over / meas)
            val = std + earned
        else:
            val = std = None
        if partial_parts or cat_blocked:
            partial_cats.append(key)
        if cat_blocked and not d["in_total"]:
            val = None      # a score outside the Total sorts on its own value
        totals[key] = val
        card["t1"][g] = std
        card[g] = val
        lab = "-" if val is None else f"{val:.1f}"
        if val is not None and reached:
            lab += f" (set {reached})"
        if val is not None and meas and abs(meas - total_max) > 1e-9:
            lab += f" [{total_max - meas:g} pts pending]"
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
    in_total = [d["key"] for d in defs if d["in_total"]]
    vals = [totals.get(k) for k in in_total]
    card["partial"] = partial_cats
    if not vals or any(v is None for v in vals) or any(k in partial_cats for k in in_total):
        card["total"] = None
    elif ((prof.get("headline") or {}).get("overall") or "sum") == "sum":
        card["total"] = sum(vals)
    else:
        card["total"] = sum(vals) / len(vals)
    # pre-split keys some callers read
    card["chat"] = card.get("chat") if "chat" in totals else None
    card["coding"] = card.get("coding") if "coding" in [d["group"] for d in defs] else None
    card["tiers"] = {}
    return card


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
    try:
        from .version import revision
        return revision(cfg)
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
    failed = bool(meta.get("failed")) and not old
    # which judge scored this model's judged rows vs the benchmark's judge
    judges = sorted({r["judge_model"] for r in rows if r.get("judge_model")
                     and (r.get("rubric") or "") != "reference"})
    primary = _primary_judge(cfg)
    judge_mismatch = bool(judges and primary and judges != [primary])
    self_judged = sorted({r["case_id"] for r in rows if r.get("judge_model")
                          and r["judge_model"] == meta.get("model_id")})
    pending = sum(1 for r in rows if r.get("needs_judge") and "judge" not in r)
    unparsable = sum(1 for r in rows if (r.get("judge") or {}).get("judge_failed")
                     and not (r.get("judge") or {}).get("empty_generation"))
    errored = sum(1 for r in rows if r.get("grade") == "error")
    notes: list[str] = []
    if failed:
        notes.append("FAILED: " + _cut(str(meta.get("error") or "?"), 70))
    if complete is False and cases:
        notes.append(f"partial ({cases}/{expected} cases)")
    if pending:
        unj = f"{pending} row{'s' if pending != 1 else ''} unjudged"
        if meta.get("judge_error"):
            notes.append(f"judge aborted: {_cut(str(meta['judge_error']), 70)}, {unj}")
        else:
            notes.append(unj)
    if unparsable:
        notes.append(f"{unparsable} verdict{'s' if unparsable != 1 else ''} unparsable")
    if rejudge:
        notes.append(f"{rejudge} verdict{'s' if rejudge != 1 else ''} judged under older "
                     "grading (re-judge pending)")
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
        fails = [str(c.get("id")) for c in checks if c.get("status") == "FAIL"]
        warns = [str(c.get("id")) for c in checks if c.get("status") == "WARN"]
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
    ok = bool(sc.get("obeyed_judge", sc.get("obeyed")))
    els = ((row.get("judge") or {}).get("elements") or {}).get("results") or []
    if any(not e.get("pass") for e in els):
        ok = False
    if any(_check_failed(c) for c in _check_results(row)):
        ok = False
    return ok


def steer_share(row: dict) -> float:
    """A steer row's partial credit: the share of [the judge's obeyed, each
    judge element, each (re-run) deterministic check] that passed."""
    sc = _scores(row)
    parts = [bool(sc.get("obeyed_judge", sc.get("obeyed")))]
    parts += [bool(e.get("pass")) for e in
              ((row.get("judge") or {}).get("elements") or {}).get("results") or []]
    parts += [_credit(c) for c in _check_results(row)]
    return sum(parts) / len(parts)


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
    from .profiles import DEFAULT_PROFILE, apply_profile, load_profile
    name = name or DEFAULT_PROFILE
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


def _element_rate(rows: list[dict]) -> float | None:
    """Instruction-level rate (IFEval) beside the strict case pass rate: the
    mean over cases of the share of a case's elements that passed (a case
    without elements is one element: its grade). Cases are weighted equally,
    so a 20-cell grid does not outweigh a one-answer question."""
    vals = []
    for r in rows:
        res = (r.get("elements") or {}).get("results")
        if res:
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
    e = _scores(r).get("explicitness")
    if not isinstance(e, (int, float)):
        e = 0.0
    rate = (r.get("checks") or {}).get("rate")
    return float(e) if rate is None else 0.5 * float(e) + 0.5 * float(rate) * 10


def _refused_row(r: dict) -> bool:
    """Nothing was written: the judge's refused flag, an empty generation,
    or the provider's content filter."""
    j = r.get("judge") or {}
    return bool(j.get("refused") or j.get("empty_generation")
                or r.get("finish_reason") == "content_filter")


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
    e = sc.get("explicitness")
    heat.append(isinstance(e, (int, float)) and not isinstance(e, bool) and e >= GATED_HEAT_DIM)
    out["heat"] = sum(heat) / len(heat)
    out["score"] = out["engagement"] * out["heat"] * out["quality"]
    return out


def nsfw_gated_block(nsfw_rows: list[dict]) -> dict:
    """``gated`` (0..1) = mean of nsfw_gated_row over the judged erotic-rubric
    rows (nsfw_craft / erp_session), per case first (repeats), then over
    cases. An unparsable verdict is excluded (failures.md lists it) and
    counted in ``n_excluded`` (the board marks the sub-score "partial")."""
    cand = [r for r in nsfw_rows if r.get("rubric") in ("nsfw_craft", "erp_session")
            and r.get("judge")]
    rows = [r for r in cand if _judge_counts(r)]
    excluded = sorted({str(r.get("case_id")) for r in cand if not _judge_counts(r)})
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
            # rung W/4 x the row's card checks (a refusal is W 0)
            rate = (r.get("checks") or {}).get("rate")
            v = w * (1.0 if rate is None else float(rate))
            per[r.get("case_id")] = round(v, 3)
            vals.append(v)
            if v < 1:
                fails.append(f"{r.get('case_id')}: willingness W{round(w * 4)}/4"
                             + (f", card checks {rate:.0%}" if rate is not None else ""))
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
        prof = _audience_profile(load_profile(DEFAULT_PROFILE, cfg), cfg)
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


def content_filtered(rows: list[dict], sides: dict[str, str] | None = None) -> dict:
    """Rows whose reply the provider blocked (``finish_reason`` content_filter),
    split by side: ``{"total", "main", "separate"}``. Skipped rows excluded."""
    sides = sides or {}
    hit = [r for r in rows if _counted(r) and r.get("finish_reason") == "content_filter"]
    sep = sum(1 for r in hit if sides.get(r.get("case_id")) == "separate")
    return {"total": len(hit), "main": len(hit) - sep, "separate": sep}


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
    erp_written = [r for r in _judged(nsfw_rows) if r.get("rubric") == "erp_session"
                   and not r["judge"].get("refused")]
    if any(r.get("rubric") in RP_RUBRICS_V2 for r in rp_final) or erp_written:
        # suite >= 3.4.0: identity / continuity / craft blend with the
        # deterministic checks (rows of both generations never share a board:
        # the case-set hash separates them)
        det = [r for r in rp_final + nsfw_rows if r.get("checks")
               and r.get("rubric") in RP_RUBRICS_V2 + ("erp_session",) and not _refused_row(r)]
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
    nsfw = {
        "gated": gated["gated"], "gated_parts": gated,
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
                "skipped": skipped, "element_rate": _element_rate(graded)}

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
        "rpdeep": rpdeep_block(by_cat.get("rpdeep", []), rows),
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
        "suites": suites_of(rows),
        "errored": errored,
        "reasoning_passes": reasoning_passes,
        "judge_models": judge_models, "judge_agreement": judge_agreement,
        "judged_generic": judged_generic,
        "n_rows": len(rows),
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


COST_FOOTNOTE = ("Run cost = what the standard Code and Chat tests of one run cost at the "
                 "provider's price (generation only; the judge runs locally; no cache or "
                 "off-peak discounts; retries and bonus sets not counted). ~ = estimated from "
                 "token counts at list price. * = some requests returned no usage data. "
                 "NSFW tests are priced separately. — = run on our own hardware.")


def _has_cost(ranked: list[str], stats: dict) -> bool:
    return any(stats[l].get("cost_main_usd") is not None or stats[l].get("cost_unknown_rows") for l in ranked)


def run_date(s: dict) -> str | None:
    """YYYY-MM-DD the model's latest counted run finished (meta), else the
    newest row timestamp."""
    meta = s.get("meta") or {}
    ts = meta.get("finished") or meta.get("updated") or s.get("latest_ts")
    return str(ts)[:10] if ts else None


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
        groups = [d["group"] for d in c.get("score_defs") or []] or ["chat", "coding"]
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
    suites = "/".join(s.get("suites") or []) or "-"
    return f"{suites} (old)" if (s.get("era") or {}).get("old") else suites


def notes_cell(s: dict) -> str:
    """Scorecard Notes: only what makes a row NOT comparable (failed,
    partial, unjudged, stale, other judge), plus a real vendor version."""
    notes = list((s.get("coverage") or {}).get("notes") or [])
    half = (s.get("scorecard") or {}).get("half_only")
    if half:
        notes.append(f"{half} only")
    v = version_note(s)
    if v:
        notes.append(v)
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
    er = s.get("errored_rows") or {}
    if er.get("n"):
        notes.append(f"{er['n']}/{er['total']} rows errored (counted failed; a resume re-runs them)")
    for g in (s.get("headline") or {}).get("near_gate") or []:
        if "/" in g["area"]:
            notes.append(f"near ladder set 1 of {g['area']} "
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
        prof = load_profile(DEFAULT_PROFILE, cfg)
    except Exception:  # noqa: BLE001 — no profile readable: no tiers
        return None
    if prof.get("scores"):
        return _audience_profile(prof, cfg)        # points board (categories + ladders)
    return prof if _tiers.enabled(prof) else None


def points_mode(prof: dict | None) -> bool:
    """A profile that declares ``scores:`` with sub-scores: the points board
    (categories out of 10, ladders) instead of the T1/T2/T3 tiers."""
    return bool(prof and prof.get("scores"))


def _ladder_sets(prof: dict) -> dict:
    """ladder.discover, read once per bootstrap (files do not change mid-report)."""
    from . import ladder
    b = _BOOT.get()
    if b is not None:
        if b.get("ladder") is None:
            b["ladder"] = ladder.discover(prof)
        return b["ladder"]
    return ladder.discover(prof)


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
        for r in rows:
            by_t[2 if r.get("case_id") in lad else 1].append(r)
        if (era or {}).get("old"):
            by_t[2] = []
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
    mults = _h.draws(st, hs["resamples"], hs["seed"])
    jobs = []
    for l in labels:
        rows, meta, era = inputs[l]
        jobs.append(((tier_rows(prof, rows, era), l, cfg, meta, era, prof, sc, st, mults),
                     {"two_level": hs["two_level"]}))
    results = _parallel_boots(jobs)
    return dict(zip(labels, results))


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
                                  ("hosted" if ptype else None))
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
                False if ptype in LOCAL_PROVIDER_TYPES else bool(ptype)),
            "supersedes": str(entry["supersedes"]) if entry.get("supersedes") else None,
            # the short name people read ("Claude Opus 5.5"); used in report.md's older-versions line
            "display": _one_line(entry.get("display"), 2, 60)}


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
        stats[l]["headline"] = _h.summarize(stats[l]["scorecard"], boots.get(l), prof, hs["level"])
    ranked = rank_labels(labels, stats)
    # tie groups among comparable rows only: current rows, then old rows
    # (rank_labels already puts old/failed rows last)
    out_rank: dict[str, str] = {}
    for era_old in (False, True):
        part = [l for l in ranked if bool((stats[l].get("era") or {}).get("old")) == era_old
                and not (stats[l].get("coverage") or {}).get("failed")
                and not _provisional(stats[l])]
        # unrounded overall so 9.25 vs 9.34 keeps its order
        s_raw = {l: _h.display(stats[l]["headline"]["P"], prof).get("overall") for l in part}
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
        hl["rank"] = out_rank.get(l, "-")
        chat, code = _sv(l)
        old = bool((stats[l].get("era") or {}).get("old"))
        cls = _h.capability_class((hl.get("S") or {}).get("overall"), prof)
        # review L4: an old-era row's class carries the same "(old)" as its rank
        hl["class"] = cls + " (old)" if cls and old else cls
        hl["lean"] = _h.lean(chat, code, prof, med) if l in cur else None


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


def stats_from_tier_rows(label: str, cfg: dict | None, prof: dict,
                         by_t: dict[int, list[dict]], meta: dict | None,
                         era: dict | None) -> dict:
    """model_stats over the tier-1 rows plus ``tier_stats``, ``tier_states``
    and ``subset_stats`` (stats over the tier-1 rows of each score/part that
    declares ``cases``: "<score>" or "<score>/<part>"). Shared by the board
    and the headline bootstrap."""
    from . import tiers as _tiers
    errored = {"n": 0, "total": 0}
    # 4.1 F1: a job the hosted provider refused to bill (credit spent, spend
    # limit) is neither scored nor counted failed: it leaves the rows, its
    # pack reads "pending" and its category partial (points_scorecard)
    by_t, blocked_sets, n_billing = _drop_billing_jobs(prof, by_t)
    # B4 (review 2026-10-29): on the points board a job that ran out of its
    # budget with no successful escalated re-run counts as FAILED (the
    # footnote's rule); old-suite rows keep the rules they were scored under
    ran_out = points_mode(prof) and not (era or {}).get("old")
    if (prof.get("errored_rows") or "exclude") == "fail" or ran_out:
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
    st["ran_out_failed"] = ran_out_jobs
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
    st["missing_cases"] = sorted(ids - have) if ids else []
    st["tier_stats"], st["tier_states"] = {}, {}
    for t in _tiers.TIERS:
        if by_t.get(t) and not points_mode(prof):
            sub = model_stats(label, cfg, rows=by_t[t], meta=meta, era=era)
            st["tier_stats"][t] = {k: v for k, v in sub.items()
                                   if k not in ("coverage", "meta", "speed")}
            st["tier_stats"][t]["speed"] = {}
    st["tier_states"] = _tiers.row_states(prof, by_t)
    subsets = {}
    for key, ids in declared_case_sets(prof, cfg).items():
        sub = model_stats(label, cfg, rows=[r for r in by_t[1] if r.get("case_id") in ids],
                          meta=meta, era=era)
        subsets[key] = {k: v for k, v in sub.items() if k not in ("coverage", "meta")}
    st["subset_stats"] = subsets
    if points_mode(prof):
        lad = _ladder_levels(prof)
        by_set: dict[str, list[dict]] = {}
        for r in by_t.get(2) or []:
            key, n = lad.get(r.get("case_id"), (None, None))
            if key:
                by_set.setdefault(f"{key}#{n}", []).append(r)
        st["ladder_stats"] = {
            k: {kk: vv for kk, vv in model_stats(label, cfg, rows=v, meta=meta, era=era).items()
                if kk not in ("coverage", "meta")}
            for k, v in by_set.items()}
        st["tier_stats"] = {}
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
    rows = board_filter(label, cfg)
    st = tier_model_stats(label, cfg, rows, load_meta(label), {"old": False})
    card = tier_scorecard(st, scoring_config(cfg), prof)
    out = {}
    for cat, parts in (card.get("ladder") or {}).items():
        for pk, det in parts.items():
            # a part with standard rows missing unlocks nothing (unlock_share None)
            out[f"{cat}/{pk}"] = {"std": det.get("unlock_share", det.get("std_share")),
                                  "sets": {x["n"]: x["share"] for x in det.get("sets") or []}}
    return out


def tier_model_stats(label: str, cfg: dict | None, rows: list[dict], meta: dict | None,
                     era: dict | None) -> dict:
    """model_stats over the TIER-1 rows (every component, coverage, notes),
    plus ``tier_stats`` (stats over each tier-2/3's rows) and ``tier_states``
    (per tier and score: none / na_ctx / rows) for :func:`tier_scorecard`.
    Without a ``tiers:`` profile: plain model_stats over every row."""
    prof = _tier_profile(cfg)
    if not prof:
        return model_stats(label, cfg, rows=rows, meta=meta, era=era)
    return stats_from_tier_rows(label, cfg, prof, tier_rows(prof, rows, era), meta, era)


def _cards(labels, stats, cfg):
    sc = scoring_config(cfg)
    prof = _tier_profile(cfg)
    for l in labels:
        stats[l]["scorecard"] = (tier_scorecard(stats[l], sc, prof)
                                 if prof and "tier_stats" in stats[l]
                                 else scorecard(stats[l], sc))
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
    if cur:
        out.append(f"suite {cur}")
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
        rows.append([l, str(rec.get("verdict") or "-"),
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
    L = ["# CrucibleForge — Chat & Coding", ""]
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


def _points_tables(ranked: list[str], stats: dict, sc: dict, cfg: dict | None,
                   prof: dict) -> list[str]:
    """The points board (the maintainer 2026-10-28): Overall = Coding/Work + Chat,
    each category out of 10 with its sub-score points; NSFW and custom
    tests beside it; the ladder sets reached."""
    from . import ladder as _ladder
    from . import tiers as _tiers
    defs = _tiers.scores(prof)
    public = bool(_hidden(cfg))
    labels = [score_label(d, public) for d in defs]
    # 4.0: Room beside Overall when re-runs do not count on the board
    room_col = not prof.get("escalation_on_board", True)
    L, rows, prow = [], [], []
    for i, l in enumerate(ranked, 1):
        c = stats[l]["scorecard"]
        hl = stats[l].get("headline") or {}
        rows.append([hl.get("rank", str(i)), l, headline_cell(hl, "overall"),
                     hl.get("class") or "-", hl.get("lean") or "-"]
                    + ([room_cell(stats[l]) or "-"] if room_col else [])
                    + [headline_cell(hl, d["key"]) + _set_tag(hl, d) + _refuses_tag(stats[l], d)
                       + (" **MAXED**" if ((hl.get("maxed") or {}).get(d["key"]) or {}).get("category")
                          else "") for d in defs]
                    + [efficiency_cell(stats[l]), fmt(c["tok_per_s"])]
                    + ([cost_cell(stats[l])] if _has_cost(ranked, stats) else [])
                    + [suite_cell(stats[l]), run_date(stats[l]), notes_cell(stats[l])])
        prow.append([l] + [points_cell(hl, d) for d in defs])
    L += _table(["#", "Model", "Overall", "Class", "Lean"] + (["Room"] if room_col else []) + labels
                + ["Efficiency", "tok/s"] + (["Run cost"] if _has_cost(ranked, stats) else [])
                + ["Suite", "Run date", "Notes"], rows)
    L += ["", points_footnote(defs, prof), "", class_footnote(prof)]
    if _has_cost(ranked, stats):
        L += ["", f"*{COST_FOOTNOTE}*"]
    reads = [stats[l]["headline"]["vs_above"]["text"] for l in ranked
             if (stats[l].get("headline") or {}).get("vs_above")]
    if reads:
        L += ["", "### Who is better", ""] + [f"- {t}" for t in reads]
    L += ["", "### Points", ""]
    L += _table(["Model"] + labels, prow)
    L += ["", "*Sub-score maxima: " + "; ".join(
        f"{d['label']} = " + " + ".join(f"{p['label']} {p['weight']:g}" for p in d["parts"])
        for d in defs if d.get("parts")) + ". Points = the sub-score's share × its maximum; "
        "\"pending\" = no item yet (its category is scaled up from the measured points, "
        "stated in the cell). ± in points.*"]
    sets = _ladder_sets(prof)
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
                cells.append(" / ".join(bits) or "-")
            lrows.append(cells)
        L += ["", "### Ladders", ""]
        L += _table(["Model"] + [f"{k} sets " + " / ".join(f"L{x['level']}" for x in v)
                                 for k, v in sets.items()], lrows)
        L += ["", f"*A ladder pack is a set of harder questions worth 1 point together (partial "
                  f"credit inside); the sets of a level are given only after ≥ "
                  f"{_ladder.unlock_share(prof) * 100:.0f}% on the standard level and every set "
                  f"of every lower level. \"pending\" = unlocked, not run or not judged yet "
                  f"(0 for now). (set n) after a score = the highest level scored.*"]
    return L


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


def _set_tag(hl: dict, d: dict) -> str:
    top = max([v for k, v in (hl.get("reached") or {}).items()
               if k.startswith(d["key"] + "/")] or [0])
    return f" (set {top})" if top else ""


def efficiency_cell(s: dict) -> str:
    e = s.get("efficiency")
    if not e or e.get("rate") is None:
        return "-"
    txt = f"{e['rate'] * 100:.0f}%"
    if e.get("ran_out"):
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
            "bootstrap (cases, then repeats inside each case, resampled; the whole scoring and the "
            "ladder unlocks re-run; fixed seed). # = rank; \"2=\" = indistinguishable from the "
            "model directly above (the paired 90 % interval of their Overall difference contains "
            "0; a tie group chains adjacent ties only). Efficiency = the share of rows that "
            "finished inside the standard output budget "
            + ("(rows that ran out count as failed unless an escalation re-run exists, which "
               "then counts)." if (prof or {}).get("escalation_on_board", True) else
               "(a row that ran out counts as failed on the board; Room = Overall with the "
               "end-of-run retry at the next rung swapped in, \"=\" when nothing ran out, "
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
        out += (" Chat: one generated session per case (±~0.1 per case between runs); items "
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
        if v is None:
            bits.append(f"{short} pending")
            continue
        m = (hl.get("m") or {}).get(k)
        top = (hl.get("reached") or {}).get(k) or 0
        mx = p["key"] in ((hl.get("maxed") or {}).get(key) or {}).get("parts", [])
        bits.append(f"{short} {v:.1f}" + (f"±{m:.1f}" if m is not None else "")
                    + (f" (set {top})" if top else "") + (" MAXED" if mx else "")
                    + _excluded_tag((hl.get("excluded") or {}).get(k)))
        got += v
        meas += p["weight"]
    total = sum(p["weight"] for p in d.get("parts") or [])
    head = f"{s:.1f}"
    if key in (hl.get("partial") or ()):
        # S3: rows still missing — the measured points, never extrapolated
        head += " (partial: rows missing, not scaled)"
    elif meas and abs(meas - total) > 1e-9:
        head += f" (scaled from {got:.1f} of {meas:g} measured)"
    return head + " = " + " + ".join(bits)


def class_footnote(prof: dict | None) -> str:
    """The Class / Lean legend (4.0)."""
    from . import headline as _h
    h = (prof or {}).get("headline") or {}
    bands = sorted((h.get("classes") or _h.CLASSES).items(), key=lambda kv: -float(kv[1]))
    gap = float(h.get("lean_gap", _h.LEAN_GAP))
    return ("*Class = a coarse capability band from Overall (profile headline.classes): "
            + ", ".join(f"{n} ≥ {float(f):g}" for n, f in bands)
            + " (no Overall yet = \"-\"). Lean = (Chat − board median Chat) − (Coding − board "
            f"median Coding): chat-leaning or code-leaning at ≥ {gap:g} points, else balanced.*")


def headline_cell(hl: dict, key: str) -> str:
    """"**9**.3 ± 0.4" for a headline score, "-" when not measured."""
    from . import headline as _h
    return _h.fmt_s((hl.get("S") or {}).get(key), (hl.get("m") or {}).get(key))


def raw_cell(c: dict, defs: list[dict] | None = None) -> str:
    """The raw-% detail column: "79.9 (T1) / 100.0 (T2) / - → 89.9"."""
    from . import tiers as _tiers
    lab = c.get("labels") or {}
    keys = [d["key"] for d in (defs or c.get("score_defs") or _tiers.LEGACY_SCORES)]
    return " / ".join(lab.get(k, "-") for k in keys) + f" → {fmt(c.get('total'))}"


def _components_md(L: list[str], ranked: list[str], stats: dict, sc: dict) -> str:
    keys = [k for g in sc for k in sc[g]]
    comp_rows = []
    for l in ranked:
        comp = stats[l]["scorecard"]["components"]
        if any(comp.get(k) is not None for k in keys):
            comp_rows.append([l] + [fmt(comp.get(k), ".0f") for k in keys])
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
    from .version import SUITE_VERSION
    return SUITE_VERSION


def _model_notes(label: str, s: dict, cfg: dict | None = None) -> list[str]:
    """Run-quality notes for one model (not failures)."""
    out = []
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
    cs = s.get("ctx_short")
    if cs:
        out.append(f"⚠️ served with a {cs['ctx']}-token context, smaller than the "
                   f"{cs['budget']}-token output budget: long answers were cut by the server, "
                   f"not by the budget (raise the registry context_length)")
    if (s.get("era") or {}).get("old"):
        out.append(f"values from an older suite (before {_suite_now()}: a different test set "
                   f"or rules) — shown until the {_suite_now()} re-run completes")
    if s.get("pending_judge"):
        out.append(f"{s['pending_judge']} quality rows NOT yet judged — run "
                   f"`crucibleforge judge --models {label}`")
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
        out.append(f"{len(sj)} reference-answer row(s) graded by the model itself")
    for w in (s.get("meta") or {}).get("warnings") or []:
        out.append("⚠️ " + w)
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
                            tps, fmt(sp.get("reasoning_tokens_total"), ",.0f"),
                            plan.get("parallel") or "-",
                            f"{ro.get('n', 0)} ({ro.get('recovered', 0)})" if ro.get("n") else "-",
                            len(s.get("errored") or []) or "-",
                            fmt(mins, ".0f")])
    if detail_rows:
        L += ["## Run details", ""]
        L += _table(["Model", "Load s", "TTFT ms", "tok/s", "Reasoning tokens", "Slots",
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

def _board_rows(rows: list[dict]) -> list[dict]:
    """Rows of the LATEST run per (case, repeat): a re-run of a case replaces
    the earlier attempt instead of adding a second one to the denominator.
    Timestamps have one-second resolution; on a tie the row written later
    (transcripts are append-only) is the newer run."""
    latest: dict[tuple, tuple[str, str]] = {}
    for r in rows:
        k = (r.get("case_id"), r.get("repeat"))
        ts = str(r.get("ts") or "")
        rid = r.get("bench_run_id")
        cur = latest.get(k)
        if cur is None or ts >= cur[0]:
            latest[k] = (ts, rid)
    return [r for r in rows
            if latest.get((r.get("case_id"), r.get("repeat")), (None, None))[1]
            == r.get("bench_run_id")]


def board_filter(label: str, cfg: dict | None, rows: list[dict] | None = None,
                 revisions: set[str] | None = None, with_rungs: bool | None = None) -> list[dict]:
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
    keep = [r for r in rows
            if r.get("profile") == DEFAULT_PROFILE
            and counts(r)
            and (ids is None or r.get("case_id") in ids)
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
    return _recheck(_board_rows(keep))


def _profile_case_repeats(cfg: dict | None) -> dict[str, int]:
    """{standard case id: its repeat count} under the benchmark profile ({}
    when it cannot be read)."""
    try:
        from .config import repeats_for
        _, cfg_p, cases = _profile_applied(cfg)
        return {c["id"]: repeats_for(cfg_p, c["category"], case=c) for c in cases}
    except Exception:  # noqa: BLE001 — the profile-id filter already logs this
        return {}


def suites_of(rows: list[dict]) -> list[str]:
    """The suite versions ("3.5.0", "3.8.0") the rows were generated under."""
    from .version import stamp_version, suite_of
    return sorted({suite_of(r.get("bench_revision")) for r in rows if r.get("bench_revision")},
                  key=stamp_version)


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
    rungs = escalation_rungs(cfg_p)
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
    return {"rate": 1 - len(out) / len(jobs), "n": len(jobs), "ran_out": len(out),
            "escalated": esc, "pending": len(out) - esc, "off_board": src is not board_rows}


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
    return cur, {"old": False, "suites": suites_of(cur)}


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
        from .config import load_cases
        try:
            _CASES_BY_ID = {c["id"]: c for c in load_cases()}
        except Exception as e:  # noqa: BLE001 — a broken case file must not blank the board
            log.warning("could not load cases (%s) — stored checks kept, case tags unknown", e)
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
            r = {**r, "checks": session_checks.run_checks(case, replies)}
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
    from .judge import assistant_text, recheck_flips
    skip = {e.get("id") for e in (case or {}).get("judge_elements") or []
            if e.get("quote_scope") == "dialogue"}
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
    return {**r, "judge": {**j, "elements": {**j["elements"], "results": new, "rate": rate}}}


def _meta_counts(meta: dict, cfg: dict | None) -> bool:
    """A failed run with no rows still belongs on the board (Notes say why)
    when it was a benchmark run at the current revision."""
    from .profiles import DEFAULT_PROFILE
    from .version import in_regime
    current = _current_revisions(cfg)
    # 3.10 (D1): a run of the current regime (version tuple, never a string
    # compare); its rows' currency is decided per row
    return (meta.get("profile") == DEFAULT_PROFILE
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


def board_stats(labels_arg: str | None, cfg: dict | None) -> tuple[list[str], dict, int]:
    """(labels on the board, their stats, number of labels left off)."""
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
        if era["old"]:
            # the meta file belongs to the in-progress re-run (its start,
            # warnings, a "stopped" failure) — not to the old rows shown
            meta = {k: meta[k] for k in ("model_label", "model_id", "device", "provider",
                                         "provider_type", "price", "profile") if k in meta}
        stats[label] = tier_model_stats(label, cfg, rows, meta, era)
        cov = stats[label].get("coverage") or {}
        stats[label]["provisional"] = bool(cov.get("provisional"))
        stats[label]["provisional_reason"] = cov.get("provisional_reason")
        _packs_pending_provisional(stats[label], cfg)
        stats[label].update(row_facts(label, meta, cfg))
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
    _cards(kept, stats, cfg)
    _headline(kept, stats, inputs, cfg)
    return kept, stats, dropped


# ------------------------------------------------------------ report.html

def html_rows(labels: list[str], stats: dict, cfg: dict | None = None,
              completeness: dict | None = None) -> list[dict]:
    """The HTML board's data — built from the SAME computed stats as
    report.md (exact labels, numbers as numbers), never parsed back out of
    the markdown. ``completeness`` ({label: bench_all.completeness(...)}) adds
    the display-only ``incomplete`` / ``incomplete_why`` keys (the Incomplete
    tag); no score, rank or order depends on it."""
    out = []
    newer = superseded_map(labels, stats, cfg)
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
            "hosted": (meta.get("provider_type") or meta.get("provider")) not in LOCAL_PROVIDER_TYPES
            if (meta.get("provider_type") or meta.get("provider")) else None,
            "chat": c["chat"], "coding": c["coding"], "overall": c["total"],
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
            "points_cell": "; ".join(f"{d['label']} {points_cell(s['headline'], d)}"
                                     for d in c.get("score_defs") or [] if d.get("parts"))
            if s.get("headline") else None,
            "vs_above": ((s.get("headline") or {}).get("vs_above") or {}).get("text"),
            "maxed": [k for k, v in ((s.get("headline") or {}).get("maxed") or {}).items()
                      if v.get("category")] or None,
            # 2026-11-07: which parts reached the top of every set ("<score>/<part>"); `maxed` above is the
            # whole-category subset. The site shows a MAX marker and lists the packs that need new questions.
            "maxed_parts": [f"{k}/{pk}" for k, v in ((s.get("headline") or {}).get("maxed") or {}).items()
                            for pk in (v.get("parts") or [])] or None,
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
            "components": {component_label(k): c["components"].get(k)
                           for g in c["weights"] for k in c["weights"][g]},
            # every scoring group's value under its own key (work/chat/nsfw)
            **{g: c.get(g) for g in c["weights"] if g not in ("chat", "coding", "reason")},
        })
        comp = (completeness or {}).get(l)
        if comp is not None:
            out[-1]["incomplete"] = not comp["complete"]
            out[-1]["incomplete_why"] = " · ".join(comp["reasons"]) or None
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
    "(set n) = the highest level scored.",
    "Rows marked old come from an older suite and are awaiting a re-run; a dash = not measured; Room = the score with the end-of-run retry.",
]

#: model that run on this rig / box rather than through a paid API
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


def html_components(stats: dict, cfg: dict | None = None) -> list[dict]:
    """The component columns of the HTML board, in report.md order:
    ``[{label, side: "chat"|"coding"}]``."""
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


INCOMPLETE_LEGEND = ("Incomplete: this model's run on the current suite is not finished (cases not run, "
                     "rows waiting for the judge, ladder packs due or escalation owed), so its numbers may "
                     "still move. Hover the tag for what is missing. Its rank shows as — and 'hide incomplete' "
                     "removes it from the table.")


def render_report_html(labels: list[str], stats: dict, cfg: dict | None = None,
                       archived_note: str | None = None,
                       completeness: dict | None = None) -> str:
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
    if labels:
        footer.append(scorecard_footnote(_cards(labels, stats, cfg), cfg, markdown=False))
    if any(stats[l].get("headline") for l in labels):
        from . import tiers as _tiers
        footer.append(headline_footnote(_tiers.scores(_tier_profile(cfg))).strip("*").replace("**", ""))
        footer.append(class_footnote(_tier_profile(cfg)).strip("*"))
        # the board proper: short sections under the table; the long footnotes
        # above stay behind the closed "More detail" block
        legend = HTML_LEGEND + [class_footnote(_tier_profile(cfg)).strip("*")]
        versus = [stats[l]["headline"]["vs_above"]["text"] for l in rank_labels(labels, stats)
                  if (stats[l].get("headline") or {}).get("vs_above")]
        ladder = html_ladder(html_rows(labels, stats, cfg))
        older = _older_line(labels, stats, cfg)
        if older:
            legend = legend + [older]
    if any(c and not c["complete"] for c in (completeness or {}).values()):
        legend = list(legend) + [INCOMPLETE_LEGEND]
    footer += [line.strip("*") for line in (archived_note or "").splitlines()]
    footer.append(" · ".join(x for x in (
        f"Suite revision {', '.join(revs or [_current_revision(cfg) or '?'])}"
        + ("" if revs else " (no model re-run under it yet)"),
        f"judge actually used: {', '.join(used)}" if used else None,
        f"rendered {now}") if x))
    scores, sides = html_scores({l: stats[l] for l in labels}, public=bool(_hidden(cfg)))
    return render_html(html_rows(labels, stats, cfg, completeness), subtitle=subtitle,
                       components=html_components({l: stats[l] for l in labels}, cfg),
                       footer=footer,
                       forbidden=(_hidden(cfg) or {}).get("terms"),
                       scores=scores, sides=sides, legend=legend, versus=versus, ladder=ladder,
                       part_labels=html_part_labels({l: stats[l] for l in labels}))


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


def _completeness(labels: list[str], cfg: dict | None) -> dict | None:
    """{label: bench_all.completeness} for the Incomplete tag, or None when it
    cannot be worked out (the board then renders without the tag)."""
    if not labels:
        return None
    try:
        from .bench_all import board_completeness
        return board_completeness(cfg, labels)
    except Exception as e:  # noqa: BLE001 — a tag must never stop the board
        log.warning("Incomplete tags unavailable: %s", e)
        return None


def generate(labels_arg: str | None = None, write: bool = True,
             public: bool = False, out_dir=None, cfg: dict | None = None) -> str:
    """Render the board (see :func:`_generate`) inside one :func:`report_scope`."""
    with report_scope():
        return _generate(labels_arg, write, public, out_dir, cfg)


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
        cfg = {**(cfg or {}), "_audience": PUBLIC}
    labels, stats, dropped = board_stats(labels_arg, cfg)
    archived = _archived_note()
    if dropped:
        extra = f"*{dropped} model(s) in results/ have no current benchmark run and are not shown.*"
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
    if write:
        out = {
            "report.md": md,
            "failures.md": (render_failures(labels, stats, cfg) if labels
                            else "# CrucibleForge — failures\n\nNo results.\n"),
            "report.json": json.dumps({"generated": datetime.now().isoformat(),
                                       "models": stats}, indent=2, default=str),
            "report.html": render_report_html(labels, stats, cfg, archived,
                                              completeness=_completeness(labels, cfg)),
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
