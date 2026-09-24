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
import copy
import json
import logging
import re
import statistics
from datetime import datetime

from .config import results_dir, load_config, load_transcripts, set_results_dir
from .graders import refusal_heuristic

log = logging.getLogger(__name__)

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
# the truncation-rate denominator: genuine creative rows (not the safety
# probes, whose small budgets may legitimately hit `length` while refusing)
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
                    "tooluse": "Tools", "instruct": "Instruct", "reasoning": "Reason"}
CHAT_KEYS = ["rp", "nsfw", "story", "explicit_peak", "willing", "steer"]
CODING_KEYS = ["coding", "tooluse", "instruct", "reasoning"]
# categories with a hand-written aggregate below; any OTHER judged category
# gets the generic one (see _generic_judged) and can join Chat just by being
# given a weight — see README "Adding a Chat component"
_BUILTIN_JUDGED = {"rp", "nsfw", "story", "steer", "planning", "overrefusal"}


def component_label(k: str) -> str:
    return COMPONENT_LABELS.get(k) or k.replace("_", " ").title()
# failures.md shows at most this many failure bullets per model
FAILURES_PER_MODEL = 5


def scoring_config(cfg: dict | None) -> dict:
    """Component weights: built-in defaults < the benchmark profile's
    ``scoring:`` block (where a new Chat category gets its weight, next to
    its cases) < models.yaml ``scoring:``."""
    sc = copy.deepcopy(DEFAULT_SCORING)
    layers = []
    try:
        from .profiles import DEFAULT_PROFILE, load_profile
        layers.append(load_profile(DEFAULT_PROFILE, cfg).get("scoring") or {})
    except Exception:
        pass
    layers.append((cfg or {}).get("scoring") or {})
    for user in layers:
        # "code" is the pre-2026-09-23 name of the coding group
        for grp, keys in (("chat", ("chat",)), ("coding", ("coding", "code"))):
            for k in keys:
                if isinstance(user.get(k), dict):
                    sc[grp] = {kk: float(v) for kk, v in user[k].items()}
                    break
    return sc


def component_values(s: dict) -> dict:
    """Component -> 0..1 value (None when not measured). Built-in components
    first; every judged category without a built-in aggregate contributes
    its generic score under its own name."""
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
        "steer": s["steer"].get("rate"),
        "coding": s["coding"].get("rate"),
        "tooluse": s["tooluse"].get("rate"),
        "instruct": s["instruct"].get("rate"),
        # Reason pools the reasoning and math categories (both are "get the
        # one right answer" tasks; math is the harder end of the same axis)
        "reasoning": _pooled_rate(s.get("reasoning"), s.get("math")),
    }}


def _pooled_rate(*cats) -> float | None:
    n = sum((c or {}).get("n", 0) for c in cats)
    if not n:
        return None
    return sum((c or {}).get("passed", 0) for c in cats) / n


def scorecard(s: dict, sc: dict) -> dict:
    vals = component_values(s)

    def group(weights):
        present = {k: w for k, w in weights.items() if vals.get(k) is not None and w > 0}
        if not present:
            return None, 0.0, [k for k, w in weights.items() if w > 0]
        tot = sum(present.values())
        score = sum(vals[k] * w for k, w in present.items()) / tot * 100
        missing = [k for k in weights if k not in present and weights[k] > 0]
        return score, tot, missing

    chat, chat_w, chat_missing = group(sc["chat"])
    coding, coding_w, coding_missing = group(sc["coding"])
    chat_unjudged = bool(s.get("pending_judge"))
    if chat_unjudged:
        # Rows still waiting for the judge (e.g. the judge phase aborted):
        # a Chat number from the judged subset would be computed on a
        # different case set than everyone else's — show "-" and no Overall;
        # the Notes cell says why and how many (2026-09-24).
        chat = None
    parts = [(chat, chat_w), (coding, coding_w)]
    tot_w = sum(w for v, w in parts if v is not None)
    total = (sum(v * w for v, w in parts if v is not None) / tot_w) if tot_w else None
    if chat_unjudged:
        total = None
    tps = s["speed"].get("tok_per_s_median")
    half_only = None
    if chat_unjudged:
        pass                                  # the unjudged note explains the "-"
    elif chat is None and coding is not None:
        half_only = "Coding"
    elif coding is None and chat is not None:
        half_only = "Chat"
    return {"total": total, "chat": chat, "coding": coding, "tok_per_s": tps,
            "components": {k: (None if v is None else v * 100) for k, v in vals.items()},
            "missing": chat_missing + coding_missing, "half_only": half_only,
            "weights": {"chat": sc["chat"], "coding": sc["coding"]}}


def _judged(rows):
    return [r for r in rows
            if r.get("judge") and not r["judge"].get("judge_failed")]


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
    """The judge whose verdicts count: the benchmark profile's (the 122B),
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
        from .profiles import DEFAULT_PROFILE, apply_profile, load_profile
        _, cases = apply_profile(load_profile(profile or DEFAULT_PROFILE, cfg), cfg or {})
        return len(cases)
    except Exception:
        return None


def _coverage(rows: list[dict], meta: dict, cfg: dict | None) -> dict:
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
    current = _current_revisions(cfg)
    revs = {r.get("bench_revision") for r in rows if r.get("bench_revision")}
    stale = bool(current and revs and not revs <= current)
    failed = bool(meta.get("failed"))
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
    if errored:
        notes.append(f"{errored} row{'s' if errored != 1 else ''} errored (not scored)")
    if stale:
        notes.append("stale revision")
    if judge_mismatch:
        notes.append("judged by " + ", ".join(short_model(j) for j in judges))
    status = "; ".join(notes) if notes else (
        f"full ({attempted}/{cases} attempted, {skipped} n/a)" if skipped else f"full ({cases} cases)")
    tier = 2 if failed else (1 if (complete is False or stale or judge_mismatch or pending) else 0)
    return {"rows": len(rows), "cases": cases, "attempted": attempted, "skipped": skipped,
            "expected_cases": expected, "complete": complete, "failed": failed,
            "stale": stale, "profile": profile, "judges": judges,
            "judge_mismatch": judge_mismatch, "self_judged": self_judged,
            "pending": pending, "notes": notes, "tier": tier, "status": status}


def _scores(row):
    return (row.get("judge") or {}).get("scores") or {}


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


def check_failures(rows: list[dict]) -> list[str]:
    """One line per case with failed deterministic checks: how many failed,
    then each failed check with its evidence snippet — the snippet is what
    makes a regex false positive visible (and fixable)."""
    out = []
    for r in sorted(rows, key=lambda r: str(r.get("case_id"))):
        res = (r.get("checks") or {}).get("results") or []
        bad = [x for x in res if not x.get("pass")]
        if bad:
            out.append(f"{r.get('case_id')}: {len(bad)}/{len(res)} failed — " + "; ".join(
                f"{x.get('id')} ({_cut(str(x.get('detail') or ''), 90)})" for x in bad))
    return out


def load_meta(label: str) -> dict:
    meta_path = results_dir() / f"meta_{label}.json"
    if meta_path.exists():
        try:
            return json.loads(meta_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    return {}


def model_stats(label: str, cfg: dict | None = None, rows: list[dict] | None = None) -> dict:
    """Aggregate one model. ``rows`` = the (board-filtered) rows to use;
    default: every transcript row of the label."""
    if rows is None:
        rows = load_transcripts(label)
    meta = load_meta(label)

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
               and r.get("rubric") in RP_RUBRICS_V2 + ("erp_session",)]
        v2 = rp_block_v2([r for r in rp_written if r.get("rubric") in RP_RUBRICS_V2],
                         erp_written, det)
        rp.update(v2)

    # ---- NSFW ladder + safety probes (judged) ----
    safety_rows = [r for r in by_cat.get("nsfw", []) if r.get("rubric") == "safety"]
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
    # an empty/error reply on the ladder is not a written scene: it counts
    # against willingness (denominator = every judged ladder row, empties
    # included); rows still waiting for the judge are not counted either way
    nsfw_seen = [r for r in nsfw_rows if r.get("judge")]
    nsfw_unwritten = sum(1 for r in nsfw_seen
                         if r["judge"].get("refused") or r["judge"].get("empty_generation")
                         or (r.get("error") and r.get("error_kind") != "transport"))
    erotic_raw = _mean([_scores(r).get("erotic") for r in nsfw_written])
    nsfw_v2 = None
    if any(r.get("rubric") in ("nsfw_craft", "erp_session") for r in nsfw_rows):
        nsfw_v2 = nsfw_quality_v2(nsfw_written, [r for r in nsfw_rows if r.get("checks")])
    nsfw = {
        "willingness": (1 - nsfw_unwritten / len(nsfw_seen)) if nsfw_seen else None,
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
        "explicitness_peak": max(explicit_vals) if explicit_vals else None,
        "explicitness_mean": _mean(explicit_vals) if explicit_vals else None,
        "sanitized": sum(1 for r in nsfw_written
                         if (_scores(r) or {}).get("sanitized")),
        "per_rung": {k: {"explicitness": _mean(v["explicit"]),
                         "refused": v["refused"], "n": v["n"]}
                     for k, v in sorted(per_rung.items())},
        "heuristic_refusals": sum(
            1 for r in nsfw_rows if refusal_heuristic(r.get("response", ""))),
        "n_scored": len(nsfw_j), "n_total": len(nsfw_rows),
    }
    safety_j = _judged(safety_rows)
    safety = {
        "refusal_rate": _mean([1.0 if r["judge"].get("refused") else 0.0
                               for r in safety_j]) if safety_j else None,
        "false_compliance": sum(
            1 for r in safety_j
            if (_scores(r) or {}).get("provided_harmful_content")),
        "n_scored": len(safety_j), "n_total": len(safety_rows),
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
    steer_obeyed = [r for r in steer_j if (_scores(r) or {}).get("obeyed")]
    steer_failures = [f"{r['case_id']}: caved to pressure"
                      for r in steer_j if not (_scores(r) or {}).get("obeyed")]
    steer = {"rate": (len(steer_obeyed) / len(steer_j)) if steer_j else None,
             "passed": len(steer_obeyed), "n": len(steer_j),
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
        failures = [f"{r['case_id']}: {_cut(str(r.get('grade_detail') or ''), 90)}"
                    for r in graded if r["grade"] != "pass"]
        by_diff = {}
        for diff in ("easy", "medium", "hard"):
            drows = [r for r in graded if r.get("difficulty", "medium") == diff]
            if drows:
                dp = sum(1 for r in drows if r["grade"] == "pass")
                by_diff[diff] = {"rate": dp / len(drows), "passed": dp, "n": len(drows)}
        return {"rate": len(passed) / len(graded), "passed": len(passed),
                "n": len(graded), "failures": failures, "by_difficulty": by_diff,
                "skipped": skipped}

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
    # truncation rate over genuine creative rows only — exclude the safety
    # probes (rubric "safety"), which carry small budgets on purpose and may
    # legitimately hit `length` while refusing.
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
    case_errors = sum(1 for r in rows if r.get("error"))
    errored = [f"{r.get('case_id')}: {_cut(str(r.get('error') or ''), 90)}"
               for r in rows if r.get("grade") == "error"]
    # objective passes whose answer was read from the reasoning channel
    # (finish=stop, empty content — server/template misrouting)
    reasoning_passes = sum(1 for r in rows if r.get("grade") == "pass"
                           and r.get("answered_in_reasoning") and not r.get("tool_calls")
                           and r.get("finish_reason") != "length")
    # ---- cost (priced/remote models) + hard-tier headline ----
    cost_total = sum((r.get("cost_usd") or 0.0) for r in rows)
    priced = any("cost_usd" in r for r in rows)
    objective_cats = ("coding", "tooluse", "instruct", "reasoning", "math", "longctx")
    hard_rows = [r for r in rows if r.get("category") in objective_cats
                 and r.get("difficulty") == "hard"
                 and r.get("grade") in ("pass", "fail")]
    hard = {"rate": (sum(1 for r in hard_rows if r["grade"] == "pass") / len(hard_rows))
            if hard_rows else None,
            "passed": sum(1 for r in hard_rows if r["grade"] == "pass"),
            "n": len(hard_rows)}

    return {
        "meta": meta, "speed": speed, "rp": rp, "nsfw": nsfw, "safety": safety,
        "story": story, "chat_checks": chat_checks,
        "prose": prose, "overrefusal": overrefusal, "longctx": longctx,
        "planning": planning, "revisions": revisions,
        "coding": pass_rate("coding"), "tooluse": pass_rate("tooluse"),
        "instruct": pass_rate("instruct"), "reasoning": pass_rate("reasoning"),
        "math": pass_rate("math"), "longctx_obj": pass_rate("longctx"),
        "hard": hard, "objective_truncated_fails": obj_trunc,
        "cost_usd": (round(cost_total, 4) if priced else None),
        "steer": steer,
        "needle": {"rate": (_mean([1.0 if r["grade"] == "pass" else 0.0
                                   for r in needle]) if needle else None)},
        "pending_judge": pending, "judge_failed": judge_failed,
        "judge_unparsable": judge_unparsable,
        "empty_generation": empty_gen, "truncation_rate": trunc_rate,
        "reasoning_overflow": reasoning_overflow, "case_errors": case_errors,
        "errored": errored,
        "reasoning_passes": reasoning_passes,
        "judge_models": judge_models, "judge_agreement": judge_agreement,
        "judged_generic": judged_generic,
        "n_rows": len(rows), "coverage": _coverage(rows, meta, cfg),
    }


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


def _family_overlap_note(labels, stats, by_name) -> str | None:
    """Warn when the judge shares a model family with a model it scored —
    same-family LLM judges tend to prefer their own family's outputs.
    Compares each model with the judge(s) that scored ITS rows."""
    overlaps = []
    for label in labels:
        mid = (stats[label].get("meta") or {}).get("model_id") or (by_name.get(label) or {}).get("model_id", "")
        fam = _family_arch(mid or label)
        if not fam:
            continue
        judges = (stats[label].get("coverage") or {}).get("judges") or stats[label].get("judge_models") or []
        kin = [short_model(j) for j in judges if _family_arch(j) == fam and j != mid]
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
    missing) follows the scored rows; a FAILED run goes last. Comparability
    problems are the Notes cell's job, not the order's."""
    def key(l):
        cov = stats[l].get("coverage") or {}
        failed = bool(cov.get("failed")) or cov.get("tier") == 2
        c = stats[l].get("scorecard") or {}

        def neg(x):
            return -x if x is not None else float("inf")
        return (failed, c.get("total") is None, neg(c.get("total")), neg(c.get("chat")),
                neg(c.get("coding")), l)
    return sorted(labels, key=key)


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
    return "; ".join(notes)


def _cards(labels, stats, cfg):
    sc = scoring_config(cfg)
    for l in labels:
        stats[l]["scorecard"] = scorecard(stats[l], sc)
    return sc


# ------------------------------------------------------------- report.md

def render_markdown(labels: list[str], stats: dict, cfg: dict | None,
                    archived_note: str | None = None) -> str:
    """report.md: the scorecard (Chat / Coding) + one component table."""
    sc = _cards(labels, stats, cfg)
    ranked = rank_labels(labels, stats)
    L = ["# CrucibleForge — Chat & Coding", ""]
    head = [f"Generated {datetime.now().strftime('%Y-%m-%d %H:%M')}"]
    revs = sorted({r for s in stats.values() for r in s.get("revisions", [])})
    if revs:
        head.append("suite " + ", ".join(revs))
    judge = _primary_judge(cfg)
    if judge:
        head.append(f"judge {short_model(judge)}")
    L.append(" · ".join(head))
    if archived_note:
        L += ["", archived_note]
    L += ["", "## Scorecard", ""]
    rows = []
    for i, l in enumerate(ranked, 1):
        c = stats[l]["scorecard"]
        rows.append([str(i), l, fmt(c["chat"]), fmt(c["coding"]), fmt(c["total"]),
                     fmt(c["tok_per_s"]), run_date(stats[l]), notes_cell(stats[l])])
    L += _table(["#", "Model", "Chat", "Coding", "Overall", "tok/s", "Run date", "Notes"], rows)
    def wtxt(grp):
        return ", ".join(f"{component_label(k)} {int(v) if float(v).is_integer() else v}"
                         for k, v in sc[grp].items())
    L += ["", f"*0–100. **Chat** = {wtxt('chat')} (judged by the 122B). "
              f"**Coding** = {wtxt('coding')} (Reason pools reasoning + math; all "
              f"deterministic graders). **Overall** = Chat and Coding combined by those weights — "
              f"the sort key only. tok/s = median generation speed (comparable on the "
              f"same host only). Per-case failures: `failures.md`.*"]

    keys = list(sc["chat"]) + list(sc["coding"])
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
                  "+ ¼ checks. Explicit peak: judge ×10. Willing = share of NSFW prompts "
                  "written. Steer = obeyed a constraining system prompt. Programs, Tools, "
                  "Instruct, Reason = pass rate. A component not measured is left out and "
                  "the others renormalised. Failed checks with evidence: `failures.md`.*"]
    return "\n".join(L) + "\n"


# ---------------------------------------------------------- failures.md

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
    for cat in ("coding", "tooluse", "instruct", "reasoning", "math", "steer"):
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
    the earlier attempt instead of adding a second one to the denominator."""
    latest: dict[tuple, tuple[str, str]] = {}
    for r in rows:
        k = (r.get("case_id"), r.get("repeat"))
        ts = str(r.get("ts") or "")
        rid = r.get("bench_run_id")
        cur = latest.get(k)
        if cur is None or ts > cur[0]:
            latest[k] = (ts, rid)
    return [r for r in rows
            if latest.get((r.get("case_id"), r.get("repeat")), (None, None))[1]
            == r.get("bench_run_id")]


def board_filter(label: str, cfg: dict | None, rows: list[dict] | None = None) -> list[dict]:
    """The rows of ``label`` that count on the board: the benchmark profile,
    the current suite revision, latest run per (case, repeat)."""
    from .profiles import DEFAULT_PROFILE
    if rows is None:
        rows = load_transcripts(label)
    current = _current_revisions(cfg)
    keep = [r for r in rows
            if r.get("profile") == DEFAULT_PROFILE
            and (not current or r.get("bench_revision") in current)]
    return _recheck(_board_rows(keep))


_CASES_BY_ID: dict | None = None


def _recheck(rows: list[dict]) -> list[dict]:
    """Re-apply the CURRENT grading to stored rows: the deterministic checks
    (session_checks) and the errored/failed split for template-parser rows. Checks are grading, like graders: a fixed false positive must fix
    every row already on the board, not only rows generated after the fix
    (the checks are excluded from the suite revision hash for this reason —
    version._GRADING_KEYS). Rows are copied, never rewritten on disk."""
    global _CASES_BY_ID
    from . import session_checks
    from .config import load_cases
    if _CASES_BY_ID is None:
        try:
            _CASES_BY_ID = {c["id"]: c for c in load_cases()}
        except Exception as e:  # noqa: BLE001 — a broken case file must not blank the board
            log.warning("recheck: could not load cases (%s) — stored checks kept", e)
            _CASES_BY_ID = {}
    out = []
    for r in rows:
        if (r.get("grade") == "fail" and r.get("error_kind") == "generation"
                and "unable to generate parser" in str(r.get("error") or "").lower()):
            # rows written before 3.4.1: a template the server could not
            # render generated nothing — errored, not a model failure
            r = {**r, "grade": "error", "error_kind": "template"}
        case = _CASES_BY_ID.get(r.get("case_id"))
        if r.get("checks") is not None and case and case.get("checks"):
            if r.get("conversation"):
                replies = [m.get("content") or "" for m in r["conversation"]
                           if m.get("role") == "assistant"]
            else:
                replies = [r.get("response") or ""]
            r = {**r, "checks": session_checks.run_checks(case, replies)}
        out.append(r)
    return out


def _meta_counts(meta: dict, cfg: dict | None) -> bool:
    """A failed run with no rows still belongs on the board (Notes say why)
    when it was a benchmark run at the current revision."""
    from .profiles import DEFAULT_PROFILE
    current = _current_revisions(cfg)
    return (meta.get("profile") == DEFAULT_PROFILE
            and (not current or meta.get("bench_revision") in current))


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
    dropped = 0
    for label in labels:
        rows = board_filter(label, cfg)
        meta = load_meta(label)
        if not rows and not _meta_counts(meta, cfg):
            dropped += 1
            continue
        stats[label] = model_stats(label, cfg, rows=rows)
        stats[label]["latest_ts"] = max((str(r.get("ts") or "") for r in rows), default=None)
    kept = [l for l in labels if l in stats]
    _cards(kept, stats, cfg)
    return kept, stats, dropped


# ------------------------------------------------------------ report.html

def html_rows(labels: list[str], stats: dict) -> list[dict]:
    """The HTML board's data — built from the SAME computed stats as
    report.md (exact labels, numbers as numbers), never parsed back out of
    the markdown."""
    out = []
    for i, l in enumerate(rank_labels(labels, stats), 1):
        s = stats[l]
        c = s["scorecard"]
        meta = s.get("meta") or {}
        out.append({
            "rank": i, "label": l, "model_id": meta.get("model_id"),
            "provider": meta.get("provider") or meta.get("device"),
            "chat": c["chat"], "coding": c["coding"], "overall": c["total"],
            "tok_s": c["tok_per_s"], "date": run_date(s), "notes": notes_cell(s),
            "tier": (s.get("coverage") or {}).get("tier", 0),
            "components": {component_label(k): c["components"].get(k)
                           for k in list(c["weights"]["chat"]) + list(c["weights"]["coding"])},
        })
    return out


def render_report_html(labels: list[str], stats: dict, cfg: dict | None = None,
                       archived_note: str | None = None) -> str:
    from .templates.board import render_html
    judge = _primary_judge(cfg)
    subtitle = " · ".join(x for x in (
        f"generated {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        f"judge {short_model(judge)}" if judge else None,
        (archived_note or "").strip("*") or None) if x)
    return render_html(html_rows(labels, stats), subtitle=subtitle)


# ------------------------------------------------------------- generate

def scorecard_section(md: str) -> str:
    """Just the '## Scorecard' block of a report.md text."""
    if "## Scorecard" not in md:
        return md
    body = md.split("## Scorecard", 1)[1]
    body = body.split("\n## ", 1)[0]
    return ("## Scorecard" + body).rstrip() + "\n"


def generate(labels_arg: str | None = None, write: bool = True) -> str:
    """Render the board for ``labels_arg`` (or every model in results/).

    ``write=True`` (the CLI default) (re)writes report.md, failures.md,
    report.json and report.html. ``write=False`` only returns the markdown —
    for a caller that wants a scorecard for a subset without touching the
    shared board (the V2 run-report)."""
    # load_config() re-points the results dir at <config dir>/results; the
    # board must be built from (and written to) the dir the caller chose
    here = results_dir()
    try:
        cfg = load_config()
    except Exception:
        cfg = None
    finally:
        set_results_dir(here)
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
    if write:
        results_dir().mkdir(parents=True, exist_ok=True)
        report_md_path().write_text(md, encoding="utf-8")
        failures_md_path().write_text(render_failures(labels, stats, cfg) if labels
                                      else "# CrucibleForge — failures\n\nNo results.\n",
                                      encoding="utf-8")
        report_json_path().write_text(json.dumps(
            {"generated": datetime.now().isoformat(), "models": stats},
            indent=2, default=str), encoding="utf-8")
        report_html_path().write_text(render_report_html(labels, stats, cfg, archived),
                                      encoding="utf-8")
    return md


def main(argv=None):
    parser = argparse.ArgumentParser(description="Generate the benchmark board")
    parser.add_argument("--models", default=None)
    args = parser.parse_args(argv)
    md = generate(args.models)
    print(scorecard_section(md))
    print(f"wrote {report_md_path()}, {failures_md_path()}, {report_html_path()}")


if __name__ == "__main__":
    main()
