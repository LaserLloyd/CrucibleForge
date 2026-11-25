"""4.5 scoring-side helpers (PLAN-45-v2 section 6, v3 sections 2.6 and 8).

Pure functions, no I/O beyond what the caller hands in, so each rule is
testable alone. report.py / headline.py wire them in:

- :func:`job_missing_reason` / :func:`split_missing`: missing is not zero.
  An infrastructure-failed, empty, ``ctx_short``, unjudged or judge-failed job
  is MISSING: it leaves the scored rows and the model is ``pending``
  (unranked) until the job is re-run. A job that ran out of its budget
  (``finish_reason == "length"``) is the model's own failure and stays scored.
- :func:`simultaneous_separation` / :func:`rank_intervals`: which pairs of the
  top models the board can order at the level, with one max-statistic
  bootstrap so ten models do not give 45 chances to be "significant".
- :func:`variance_components`: case-sampling, generation and judge-sample
  spread, reported apart.
- :func:`bench_min`: wall minutes of the main test and of the hard sets.
"""
from __future__ import annotations

import math
import re
from collections import defaultdict
from datetime import datetime, timezone

#: profile key that turns the 4.5 missing-row convention on
MISSING_POLICY_KEY = "missing_rows"
PENDING = "pending"

#: categories whose rows carry no free-text ``response`` to be "empty". Bank rows (Predict sheets, agent loops) are
#: checked like every other row since the 2026-11-18 fix round (AUDIT-HARNESS F4): they carry ``response``, and a
#: provider filter or a dropped stream there is run health (missing), never the model's 0; an agent loop that made
#: tool calls is never "empty" (``agent.tool_calls``)
_NO_EMPTY_CHECK = {"perf"}


def pending_policy(prof: dict | None) -> bool:
    return bool(prof) and str(prof.get(MISSING_POLICY_KEY) or "").lower() == PENDING


# ----------------------------------------------------------- missing != 0

def _job_key(r: dict) -> tuple:
    return (r.get("case_id"), r.get("repeat"))


def _is_ctx_short(r: dict) -> bool:
    return bool(r.get("ctx_short") or r.get("error_kind") == "ctx_short"
                or r.get("finish_reason") == "ctx_short")


#: finish_reason (and row key) of a reply the server could not parse: a scored miss (runner.MODEL_OUTPUT_INVALID)
MODEL_OUTPUT_INVALID = "model_output_invalid"
#: finish_reason (and row key) of a job the model's own maximum context cannot hold: a scored miss
#: (runner.CONTEXT_MODEL_MAX, PLAN-451-LIMITS step 6)
CONTEXT_MODEL_MAX = "context_model_max"


#: row categories whose provider content-filter stop is the model side's
#: refusal (scored W=0 as in 4.1), not an infrastructure gap (WP-02 decision)
REFUSAL_CATEGORIES = frozenset({"nsfw"})


def _policy_block(r: dict) -> bool:
    """The provider refused the content (report.filtered_row: content_filter or a
    policy-error message), as opposed to a transport/infrastructure error."""
    from .report import filtered_row
    return filtered_row(r)


def _has_reasoning(r: dict) -> bool:
    return bool((r.get("reasoning") or "").strip()
                or ((r.get("metrics") or {}).get("reasoning_tokens") or 0) > 0)


def _row_empty(r: dict, refusal_ids=frozenset()) -> str | None:
    """Why a generated turn with no content at all is MISSING, or None.

    - a provider content filter (``finish_reason == "content_filter"``): on an
      NSFW row (category ``nsfw`` or a case id in ``refusal_ids``) it is the
      refusal 4.1 scores as W=0, so it stays scored; elsewhere (code, chat) it
      is infrastructure -> ``"content_filter"``;
    - an empty reply WITH reasoning: the model stopped inside its reasoning,
      its own failure scored 0 as in 3.8.0 (report._empty_scores_zero) -> None;
    - an empty reply with nothing at all that did not run out of budget (a
      dropped stream) -> ``"empty"``."""
    if r.get("category") in _NO_EMPTY_CHECK or r.get("skipped") or r.get("grade") == "skipped":
        return None
    if "response" not in r:
        return None
    if (r.get("response") or "").strip() or r.get("tool_calls") or (r.get("agent") or {}).get("tool_calls"):
        return None
    if r.get("finish_reason") == "tool_calls":
        # the turn ended in a tool call: that is the model's answer, graded as it stands. A scripted tool dialogue
        # stores no text for such a turn, and a model that reports no reasoning tokens was read as a dropped stream:
        # four passed runs of one dialogue became "missing" and the model lost its rank (2026-11-24).
        return None
    if r.get("finish_reason") == "length" or r.get("truncated"):
        return None
    if (r.get("finish_reason") in (MODEL_OUTPUT_INVALID, CONTEXT_MODEL_MAX) or r.get(MODEL_OUTPUT_INVALID)
            or r.get(CONTEXT_MODEL_MAX)):
        # the server could not parse what the model wrote (runner.invalid_output_result): the model's own miss,
        # graded as an empty answer, never a dropped stream to re-run (owner: a model that cannot do a case scores 0)
        return None
    if (r.get("judge") or {}).get("refused") or r.get("refused"):
        return None
    if r.get("finish_reason") == "content_filter" or _policy_block(r):
        if r.get("category") in REFUSAL_CATEGORIES or r.get("case_id") in refusal_ids:
            return None
        return "content_filter"
    if _has_reasoning(r):
        return None
    if (r.get("empty_stop_retry") or {}).get("chain"):
        # the run's empty-reply chain (regenerate, then thinking off) ran and every step came back with nothing
        # visible: that is the model's answer, scored 0, never a dropped stream to re-run (lead 2026-11-15)
        return None
    return "empty"


def _row_judge_gap(r: dict) -> str | None:
    """'unjudged' / 'judge_failed' when a judged row has no usable verdict."""
    if not r.get("needs_judge"):
        return None
    j = r.get("judge")
    if not isinstance(j, dict) or not j:
        return "unjudged"
    if j.get("empty_generation") or j.get("counted_failed"):
        return None                   # a counted-failed row (budget) is a verdict
    if j.get("judge_failed"):
        return "judge_failed"
    if (j.get("elements") or {}).get("unjudged"):
        return "unjudged"
    return None


def job_missing_reason(rows: list[dict], refusal_ids=frozenset()) -> str | None:
    """Why one job (every row of one (case, repeat)) is MISSING, or None.
    Order: error rows, ctx_short, empty turns, then judge gaps.
    ``refusal_ids``: case ids where a content-filter stop is a refusal."""
    for r in rows:
        if _is_ctx_short(r):
            return "ctx_short"
    for r in rows:
        if r.get("grade") == "error":
            # Admin N (N3): on an NSFW case a provider policy block that arrives as an HTTP
            # error (OpenAI/xAI/Anthropic moderation 400s) is the model side's refusal, scored
            # W0 like a content_filter stop -- never MISSING, so a refusing model keeps its row
            if _policy_block(r) and (r.get("category") in REFUSAL_CATEGORIES
                                     or r.get("case_id") in refusal_ids):
                continue
            return "error"
    for r in rows:
        why = _row_empty(r, refusal_ids)
        if why:
            return why
    for r in rows:
        gap = _row_judge_gap(r)
        if gap:
            return gap
    return None


def split_missing(by_t: dict[int, list[dict]], refusal_ids=frozenset()
                  ) -> tuple[dict[int, list[dict]], list[dict]]:
    """(rows without the missing jobs, [{case_id, repeat, reason}]). Jobs are
    judged across tiers (a session's turns share its key)."""
    jobs: dict[tuple, list[dict]] = defaultdict(list)
    for rows in by_t.values():
        for r in rows:
            if r.get("category") == "perf":
                continue
            jobs[_job_key(r)].append(r)
    missing = {}
    for k, rs in jobs.items():
        why = job_missing_reason(rs, refusal_ids)
        if why:
            missing[k] = why
    out = {t: [r for r in rows if r.get("category") == "perf" or _job_key(r) not in missing]
           for t, rows in by_t.items()}
    listed = [{"case_id": k[0], "repeat": k[1], "reason": why}
              for k, why in sorted(missing.items(), key=lambda kv: (str(kv[0][0]), kv[0][1] or 0))]
    return out, listed


# ------------------------------------------------- simultaneous separation

def _quantile(sorted_vals: list[float], q: float) -> float:
    if not sorted_vals:
        return float("nan")
    pos = q * (len(sorted_vals) - 1)
    lo = int(math.floor(pos))
    hi = min(lo + 1, len(sorted_vals) - 1)
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (pos - lo)


def _sd(xs: list[float]) -> float:
    n = len(xs)
    if n < 2:
        return 0.0
    m = sum(xs) / n
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (n - 1))


def simultaneous_separation(samples: dict[str, list], point: dict[str, float | None],
                            order: list[str], top_n: int = 10, level: float = 0.90) -> dict:
    """Max-statistic bootstrap over every pair of the top ``top_n`` models of
    ``order``. ``samples[label]`` = the per-resample values (aligned across
    labels: the same draws); ``point[label]`` = the point estimate. For each
    pair the paired difference D = point_a - point_b gets a band D +/- c * sd,
    where sd is the bootstrap sd of the paired difference and c the ``level``
    quantile of the per-resample MAXIMUM of |d_i - D| / sd over all pairs
    (one c for the whole family). A pair is ``separated`` when its band
    excludes 0, i.e. the board can order a above b at ``level`` for all the
    pairs at once. Not separated means "cannot be ordered", never "equal".
    Returns {"level", "top_n", "labels", "critical", "pairs": [...]}."""
    top = [l for l in order if point.get(l) is not None and l in samples][:top_n]
    pairs_idx = [(a, b) for i, a in enumerate(top) for b in top[i + 1:]]
    diffs: dict[tuple, list[float]] = {}
    for a, b in pairs_idx:
        sa, sb = samples[a], samples[b]
        diffs[(a, b)] = [None if (x is None or y is None) else x - y for x, y in zip(sa, sb)]
    stat: dict[tuple, tuple[float, float]] = {}
    for k, d in diffs.items():
        clean = [x for x in d if x is not None]
        stat[k] = (point[k[0]] - point[k[1]], _sd(clean))
    n = min((len(d) for d in diffs.values()), default=0)
    maxes: list[float] = []
    for i in range(n):
        m = 0.0
        for k, d in diffs.items():
            x = d[i]
            D, sd = stat[k]
            if x is None or sd <= 1e-12:
                continue
            m = max(m, abs(x - D) / sd)
        maxes.append(m)
    maxes.sort()
    crit = _quantile(maxes, level) if maxes else float("inf")
    pairs = []
    for (a, b) in pairs_idx:
        D, sd = stat[(a, b)]
        half = crit * sd if math.isfinite(crit) else float("inf")
        lo, hi = D - half, D + half
        sep = (D != 0.0) if sd <= 1e-12 else (lo > 0 or hi < 0)
        pairs.append({"a": a, "b": b, "diff": round(D, 3), "lo": round(lo, 3), "hi": round(hi, 3),
                      "sd": round(sd, 4), "separated": bool(sep)})
    return {"level": level, "top_n": top_n, "labels": top,
            "critical": None if not math.isfinite(crit) else round(crit, 3), "pairs": pairs}


def rank_intervals(samples: dict[str, list], labels: list[str],
                   level: float = 0.90) -> dict[str, list[int] | None]:
    """Per label, the central ``level`` interval of its rank over the shared
    resamples (rank 1 = highest; equal values share the better rank), among
    ``labels``. A label whose samples are missing gets None."""
    labels = [l for l in labels if l in samples]
    if not labels:
        return {}
    n = min(len(samples[l]) for l in labels)
    ranks: dict[str, list[int]] = {l: [] for l in labels}
    for i in range(n):
        vals = {l: samples[l][i] for l in labels if samples[l][i] is not None}
        for l, v in vals.items():
            ranks[l].append(1 + sum(1 for o in vals.values() if o > v + 1e-12))
    out = {}
    a = (1.0 - level) / 2.0
    for l in labels:
        r = sorted(ranks[l])
        out[l] = None if not r else [int(round(_quantile(r, a))), int(round(_quantile(r, 1 - a)))]
    return out


# ------------------------------------------------------ variance components

_N_OF = re.compile(r"\((\d+)/(\d+) samples\)")


def element_votes(res: dict) -> tuple[int, int] | None:
    """(yes votes, samples) of one stored element result when it was judged at
    more than one sample (13a stores ``n_samples``; older rows carry the
    '(k/n samples)' wording and ``verified_samples``)."""
    n = res.get("n_samples")
    if n is None:
        m = _N_OF.search(res.get("detail") or "")
        if m:
            n = int(m.group(2))
    if not n or int(n) < 2:
        return None
    n = int(n)
    if "votes_yes" in res:
        k = int(res["votes_yes"])
    elif "verified_samples" in res:
        k = len(res["verified_samples"])
    else:
        m = _N_OF.search(res.get("detail") or "")
        k = int(m.group(1)) if m else (n if res.get("pass") else 0)
    return min(k, n), n


def judge_sample_spread(rows: list[dict]) -> dict:
    """How much the judge samples disagreed, from stored element results.
    ``multi`` = elements judged at >= 2 samples; ``split`` = those whose
    samples did not agree; ``rate_sd`` = sd of the mean element pass share
    caused by judge sampling alone (sum of w^2 p(1-p)/n over the row's
    elements, per row, averaged) on the 0-1 share scale, weights = 1."""
    multi = split = 0
    row_sds = []
    for r in rows:
        res = ((r.get("judge") or {}).get("elements") or {}).get("results") or []
        var = 0.0
        n_el = len(res)
        row_multi = 0      # review F16: this row's own count, not the running total
        for x in res:
            v = element_votes(x)
            if not v:
                continue
            k, n = v
            multi += 1
            row_multi += 1
            if 0 < k < n:
                split += 1
            p = k / n
            var += p * (1 - p) / n
        if n_el and row_multi:
            row_sds.append(math.sqrt(var) / n_el)
    return {"multi_sample_elements": multi, "split_elements": split,
            "split_rate": None if not multi else round(split / multi, 4),
            "rate_sd": None if not row_sds else round(sum(row_sds) / len(row_sds), 4),
            "basis": "stored element votes" if multi else "none (no element judged at >= 2 samples)"}


def variance_components(case_sd: float | None, gen_sd: float | None, has_repeats: bool,
                        judge: dict, gen_prior: float | None = None) -> dict:
    """The three components as one block. ``case_sd`` and ``gen_sd`` are bootstrap
    sds of Overall (case-only resample / repeats-only resample). Where the
    model has no repeats the generation component is absent unless the profile
    gives a prior (``headline.generation_sd_prior``), then it is labelled
    ``model-based``."""
    if has_repeats and gen_sd is not None:
        gen = {"sd": round(gen_sd, 4), "basis": "observed repeats"}
    elif gen_prior is not None:
        gen = {"sd": round(float(gen_prior), 4), "basis": "model-based"}
    else:
        gen = {"sd": None, "basis": "absent (single generation, no prior)"}
    return {"case_sampling": {"sd": None if case_sd is None else round(case_sd, 4),
                              "basis": "case bootstrap"},
            "generation": gen, "judge_sample": judge, "unit": "points on the Overall scale "
            "(judge_sample: share of one row)"}


# --------------------------------------------------------------- bench_min

def _ts(v) -> datetime | None:
    if not v:
        return None
    try:
        t = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def _span_minutes(stamps: list[datetime], gap_s: float) -> float:
    """Minutes of activity in a set of timestamps: gaps longer than ``gap_s``
    (the machine was off, the next night) are not counted."""
    s = sorted(stamps)
    if len(s) < 2:
        return 0.0
    tot = 0.0
    for a, b in zip(s, s[1:]):
        d = (b - a).total_seconds()
        if d <= gap_s:
            tot += d
    return tot / 60.0


def bench_min(rows: list[dict], hard_ids: set[str] | frozenset = frozenset(),
              gap_s: float = 1800.0) -> dict:
    """Measured wall minutes of the main test and of the hard sets.

    Per row set: generation = the span of the rows' ``ts`` stamps; judging =
    the span of the ``judge_ts`` stamps (when the judge wrote them). Idle gaps
    over ``gap_s`` (a stopped run, the next night) are cut out. Also the date
    range of each set. Where stamps are missing a value is None, never 0."""
    def block(sel: list[dict]) -> dict:
        gen = [t for t in (_ts(r.get("ts")) for r in sel) if t]
        jud = [t for t in (_ts(r.get("judged_ts") or r.get("judge_ts") or (r.get("judge") or {}).get("ts"))
                           for r in sel) if t]
        g = _span_minutes(gen, gap_s) if len(gen) > 1 else None
        j = _span_minutes(jud, gap_s) if len(jud) > 1 else None
        allts = gen + jud
        return {"gen_min": None if g is None else round(g, 1),
                "judge_min": None if j is None else round(j, 1),
                "min": None if g is None and j is None else round((g or 0) + (j or 0), 1),
                "from": min(allts).date().isoformat() if allts else None,
                "to": max(allts).date().isoformat() if allts else None,
                "rows": len(sel)}
    main = [r for r in rows if r.get("category") != "perf" and r.get("case_id") not in hard_ids]
    hard = [r for r in rows if r.get("case_id") in hard_ids]
    out = {"main": block(main), "hard": block(hard) if hard else None}
    out["main_min"] = out["main"]["min"]
    out["hard_min"] = (out["hard"] or {}).get("min")
    out["basis"] = ("row ts stamps (generation) + judge stamps; gaps over "
                    f"{int(gap_s / 60)} min cut out")
    return out
