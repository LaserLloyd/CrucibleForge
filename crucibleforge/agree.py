"""Second reader (``crucibleforge agree``): how far the board's judged parts can trust one judge.

The production judge reads every judged unit once (temperature 0, fixed seed): a second pass of the SAME judge is a
replay and measures server jitter, not judge error (REVIEW-HOLISTIC finding 4, DECISIONS D1 (c) amended). This
module re-grades the stored judged units with a SECOND READER from a different model family, through the SAME
prompts and verdict parsers as the production judge path:

- ``chat/j_rplong`` (long scenes): each stored row goes through ``judge.judge_row`` with the row's own stored judge
  policy (the fact-element batches, thinking off). Unit of credit = one fact element's yes/no. Unit score = the
  case's fact rate as ``rpdeep.score_row`` computes it with the reader's verdict in place of the judge's.
- ``chat/j_rpsession`` (sessions, judged half): every comparison the judge made is asked again of the reader with
  the identical pair request (``rpladder.pair_requests``: same window, axis, rung text, both orders), and the reader
  places the unit on the ladder itself (``rpladder.place_units``, the production rung check's usable axes). Unit
  of credit = one comparison (both orders combined: subject better / split / rung better). Unit score = position / 4.
- ``--adult``: the adult page's judged rows through ``judge.judge_row`` with their stored policy; unit of credit =
  each scored mark, read in three bands (0-3 / 4-6 / 7-10); unit score = mean mark / 10.

Nothing in the folder's transcripts changes: the reader's verdicts go to ``<folder>/agree/`` (one jsonl of records
per reader, plus the reader's pair-verdict cache), and the agreement table goes to ``--out``.

Agreement statistic: Gwet's AC1. Fact and comparison verdicts are skewed (a capable model passes 75-90% of its facts):
with a skewed base rate Cohen's kappa collapses toward 0 even when two readers agree on nearly every decision (the
"kappa paradox": its chance term is computed from the two readers' own marginals, which are both near 1). AC1's
chance term is bounded by the share of decisions that are not in the majority class, so it stays stable when
prevalence is extreme and equals kappa when classes are balanced. Kappa is printed beside it for reference.

Judge term (``judge_sd``, share of the part, 0-1): with two exchangeable readers whose errors are independent, the
per-unit score difference d = s_judge - s_reader has variance 2 sigma^2 (sigma = one reader's per-unit error) and
mean = the leniency gap between them. The part score is the mean over n units, so one reader's error on it is
sqrt(var(d) / (2 n) + mean(d)^2 / 2): the noise term shrinks with n, the systematic term does not.
"""
from __future__ import annotations

import copy
import hashlib
import json
import logging
import math
import re
from pathlib import Path

log = logging.getLogger(__name__)

LONG_PART = "chat/j_rplong"
SESSION_PART = "chat/j_rpsession"
#: the long-scene cases (profiles/bench45.yaml standard: chat/j_rplong)
LONG_CASES = ("RD1-marisol", "RD4-kettle-saddle", "CC3-tea-hut", "CC4-second-pitch", "RV1-three-at-the-table",
              "RV2-skarren-hut", "WR1-ninth-string", "WR2-red-thread-key")
ADULT_RUBRICS = ("adult_session", "adult_piece")
#: the judged share of a session piece (profile judge_modes.ladder.code_weight 0.5: half code checks)
SESSION_JUDGED_WEIGHT = 0.5
STAT = "AC1"
#: the verdict keys a production row carries that the re-grade must not see
_JUDGE_KEYS = ("judge", "judge_model", "judge_id", "judged_ts", "judge_instance", "judge_ladder", "judge_ladder_ts",
               "judge_pending", "regraded_by")
#: adult mark bands (the adult page's gate sits at 7)
ADULT_BANDS = ((0, 3, "low"), (4, 6, "mid"), (7, 10, "high"))


class ReaderRefused(ValueError):
    """The reader may not read these rows (same family as the judge, or no spend cap for a hosted reader)."""


# ------------------------------------------------------------------ statistics
def raw_agreement(pairs: list[tuple]) -> float | None:
    return None if not pairs else sum(1 for a, b in pairs if a == b) / len(pairs)


def gwet_ac1(pairs: list[tuple]) -> float | None:
    """Gwet's AC1 over categorical (a, b) decisions. pe = sum_k pi_k (1 - pi_k) / (K - 1), pi_k = the mean of the
    two readers' shares of class k. One class only (both readers always agree on one value): 1.0."""
    if not pairs:
        return None
    cats = sorted({x for p in pairs for x in p}, key=str)
    po = raw_agreement(pairs)
    if len(cats) < 2:
        return 1.0
    n = len(pairs)
    pi = {k: (sum(1 for a, _ in pairs if a == k) + sum(1 for _, b in pairs if b == k)) / (2 * n) for k in cats}
    pe = sum(p * (1 - p) for p in pi.values()) / (len(cats) - 1)
    return 1.0 if pe >= 1 else (po - pe) / (1 - pe)


def cohen_kappa(pairs: list[tuple]) -> float | None:
    if not pairs:
        return None
    cats = sorted({x for p in pairs for x in p}, key=str)
    n = len(pairs)
    po = raw_agreement(pairs)
    pe = sum((sum(1 for a, _ in pairs if a == k) / n) * (sum(1 for _, b in pairs if b == k) / n) for k in cats)
    if pe >= 1:
        return 1.0 if po == 1 else 0.0
    return (po - pe) / (1 - pe)


def pearson(xs: list[float], ys: list[float]) -> float | None:
    n = len(xs)
    if n < 3:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    sx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    sy = math.sqrt(sum((y - my) ** 2 for y in ys))
    if sx == 0 or sy == 0:
        return None
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / (sx * sy)


def judge_sd(diffs: list[float]) -> float | None:
    """One reader's error on the mean of these units (share 0-1), from the unit differences d between the two
    readers: sqrt(var(d) / (2 n) + mean(d)^2 / 2) (module docstring)."""
    n = len(diffs)
    if n == 0:
        return None
    m = sum(diffs) / n
    var = sum((d - m) ** 2 for d in diffs) / (n - 1) if n > 1 else 0.0
    return math.sqrt(var / (2 * n) + m * m / 2)


# ------------------------------------------------------------------ family rule
def family(model_id: str | None) -> str | None:
    from . import lineage
    return lineage.family(model_id)


def check_reader(reader_id: str, judge_id: str | None, hosted: bool = False, cap_usd: float | None = None) -> None:
    """The reader rules: a model of the production judge's family never second-reads it (the judge self-judge rule
    in judge_elements.same_family is the precedent: two readers of one family share their blind spots, so their
    agreement would overstate the judge's precision); a hosted reader needs an explicit spend cap."""
    jf, rf = family(judge_id), family(reader_id)
    if not rf:
        raise ReaderRefused(f"agree: the reader {reader_id!r} has no known family (lineage.FAMILIES); "
                            "name a reader whose family the code can tell apart from the judge's")
    if jf and rf == jf:
        raise ReaderRefused(f"agree: the reader {reader_id} is of the judge's family ({jf}); "
                            "a second reader must come from a different model family")
    if hosted and (cap_usd is None or cap_usd <= 0):
        raise ReaderRefused("agree: a hosted reader needs --cap-usd (a positive spend cap)")


# ------------------------------------------------------------------ rows
def read_folder(folder: Path) -> tuple[str, list[dict]]:
    """(label, rows) of one result folder (exactly one transcripts_<label>.jsonl)."""
    folder = Path(folder)
    files = sorted(folder.glob("transcripts_*.jsonl"))
    if len(files) != 1:
        raise ValueError(f"agree: {folder} must hold exactly one transcripts_<label>.jsonl (found {len(files)})")
    label = files[0].name[len("transcripts_"):-len(".jsonl")]
    rows = []
    for line in files[0].open(encoding="utf-8"):
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return label, rows


def _strip(row: dict) -> dict:
    return {k: v for k, v in row.items() if k not in _JUDGE_KEYS}


def row_sha(row: dict) -> str:
    """The stored answer a verdict is about (conversation or response), so a resumed run never mixes rows."""
    body = json.dumps(row.get("conversation") or row.get("response") or "", sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()[:16]


def standard_long(recs: list[dict]) -> list[dict]:
    """Drop long-scene records of anything but the eight standard scenes (pack scenes share the part id and the
    reader store); records of other parts pass through."""
    out = []
    for r in recs:
        if r.get("part") == LONG_PART:
            u = str(r.get("unit") or "").split("|")
            if len(u) < 2 or u[1] not in LONG_CASES:
                continue
        out.append(r)
    return out


def long_units(rows: list[dict]) -> dict[str, dict]:
    """{unit id: the newest JUDGED long-scene row} per (run, case, repeat) (check_rplong's rule)."""
    out: dict[str, dict] = {}
    for r in rows:
        if r.get("rubric") != "rp_deep" or r.get("case_id") not in LONG_CASES:
            continue
        j = r.get("judge")
        if not isinstance(j, dict) or not (j.get("elements") or {}).get("results"):
            continue
        out[f"{r.get('bench_run_id')}|{r['case_id']}|{r.get('repeat') or 1}"] = r
    return out


def session_units(rows: list[dict]) -> dict[str, dict]:
    """{unit id: the newest row with a complete ladder placement} of the session part."""
    out: dict[str, dict] = {}
    for r in rows:
        ch = r.get("chain") or {}
        rec = r.get("judge_ladder")
        if ch.get("part") != SESSION_PART or not isinstance(rec, dict) or not rec.get("complete"):
            continue
        if rec.get("position") is None:
            continue
        out[str(ch.get("id") or r.get("case_id"))] = r
    return out


def adult_units(rows: list[dict]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for r in rows:
        if r.get("rubric") not in ADULT_RUBRICS:
            continue
        j = r.get("judge")
        if not isinstance(j, dict) or not isinstance(j.get("scores"), dict) or j.get("judge_failed"):
            continue
        ch = r.get("chain") or {}
        out[f"{ch.get('id') or r.get('case_id')}|{r.get('repeat') or 1}|{r.get('turn') or 0}"] = r
    return out


def adult_part(row: dict) -> str:
    return str((row.get("chain") or {}).get("part") or f"nsfw/{row.get('case_id')}")


# ------------------------------------------------------------------ long scenes
def fact_decisions(verdict: dict) -> dict[str, bool]:
    return {r["id"]: bool(r.get("pass")) for r in ((verdict or {}).get("elements") or {}).get("results") or []
            if r.get("id")}


def _weighted_rate(verdict: dict) -> float | None:
    res = ((verdict or {}).get("elements") or {}).get("results") or []
    w = sum(float(r.get("weight", 1)) for r in res)
    if not w:
        return None
    return sum(float(r.get("weight", 1)) * (1.0 if r.get("pass") else float(r.get("credit") or 0)) for r in res) / w


def long_score(row: dict, verdict: dict, case: dict | None) -> float | None:
    """The case's fact rate with ``verdict`` as its judge block (rpdeep.score_row); the judged facts' weighted
    rate when the case is not loadable."""
    if case is not None:
        try:
            from . import rpdeep
            sr = rpdeep.score_row({**_strip(row), "judge": verdict}, case)
            if sr.get("status") == "ok":
                return sr.get("rate")
            return None
        except Exception as e:  # noqa: BLE001 — a scorer error falls back, never stops the table
            log.warning("agree: score_row failed on %s: %s", row.get("case_id"), e)
    return _weighted_rate(verdict)


#: lane-j (lead 2026-11-19): the second reader thinks by default (same rubric, prompts and parser). The production
#: judge's policy turns thinking off for fact batches and ladder pairs; on the proof rows a thinking-off reader
#: quoted the right line and still answered no on 10 of 23 long-scene disagreements. ``--reader-thinking off``
#: reads exactly as production does.
READER_THINKING = True
#: the output budget of one thinking call (the production 800 / ladder budget holds the answer only)
READER_THINK_MAX_TOKENS = 8192


def reader_key(reader_id: str, thinking: bool) -> str:
    """The id the reader's files and pair cache are kept under: a thinking reader is another reading."""
    return f"{reader_id}+think" if thinking else reader_id


def regrade_long(jc, row: dict, thinking: bool = False) -> dict:
    """The reader's verdict on a stored long-scene row: the production entry point (judge.judge_row) with the
    row's own stored policy, so the prompts, batches, schema and parser are the production ones. ``thinking``
    turns the reader's thinking on (and widens the output budget for it); nothing else changes."""
    from . import judge as J
    pol = dict(((row.get("judge") or {}).get("policy")) or {})
    if thinking:
        pol["thinking"] = True
        pol["max_tokens"] = max(int(pol.get("max_tokens") or 0), READER_THINK_MAX_TOKENS)
    prow = _strip(row)
    if pol:
        prow["_judge_policy"] = pol
    return J.judge_row(jc, prow, samples=int(pol.get("samples") or 1))


def scored_decisions(row: dict, verdict: dict, case: dict | None) -> dict[str, bool]:
    """{fact id: credited} as the board scores it (rpdeep.score_row with ``verdict``: the OOC rule, quote checks,
    keyword gates applied), else the verdict's raw passes."""
    if case is not None:
        try:
            from . import rpdeep
            sr = rpdeep.score_row({**_strip(row), "judge": verdict}, case)
            got = {i["id"]: bool(i.get("pass")) for i in sr.get("items") or [] if i.get("grader") == "judge"}
            if got:
                return got
        except Exception as e:  # noqa: BLE001 — a scorer error falls back, never stops the table
            log.warning("agree: score_row failed on %s: %s", row.get("case_id"), e)
    return fact_decisions(verdict)


def refresh_long(rec: dict, row: dict, case: dict | None) -> dict:
    """A stored long-scene record re-scored under today's scorer (both readers' stored verdicts, no call)."""
    pv, rv = row.get("judge") or {}, rec.get("reader_verdict") or {}
    pd, rd = scored_decisions(row, pv, case), scored_decisions(row, rv, case)
    both = sorted(set(pd) & set(rd))
    return {**rec, "decisions": [[pd[k], rd[k]] for k in both], "decision_ids": both,
            "score_judge": long_score(row, pv, case), "score_reader": long_score(row, rv, case)}


def long_record(label: str, uid: str, row: dict, reader_v: dict, case: dict | None, reader_id: str) -> dict:
    pv = row.get("judge") or {}
    pd, rd = scored_decisions(row, pv, case), scored_decisions(row, reader_v, case)
    both = sorted(set(pd) & set(rd))
    return {"part": LONG_PART, "model": label, "unit": uid, "row_sha": row_sha(row), "reader": reader_id,
            "decisions": [[pd[k], rd[k]] for k in both], "decision_ids": both,
            "score_judge": long_score(row, pv, case), "score_reader": long_score(row, reader_v, case),
            "weight": 1.0, "reader_verdict": reader_v}


# ------------------------------------------------------------------ sessions
def comparison_outcomes(pairs: list[dict]) -> dict[tuple, float]:
    """{(window, axis, rung): 1 / .5 / 0} from a placement's stored pairs (both orders of each comparison)."""
    from . import rpladder as RL
    by: dict[tuple, dict] = {}
    for p in pairs or []:
        by.setdefault((p["window"], p["axis"], p["rung"]), {})[p["subj_is"]] = p
    out = {}
    for k, d in by.items():
        if "A" in d and "B" in d:
            out[k] = RL.combine(RL.side(d["A"].get("better"), "A"), RL.side(d["B"].get("better"), "B"))
    return out


def _check_from_rows(cards: dict, rows: dict[str, dict]) -> dict:
    """A rung check rebuilt from the stored placements: each card's chosen ladder and its usable axes, as the
    production judge recorded them (used when the folder's own rung-check files do not match today's ladders)."""
    from . import rpladder as RL
    cells, checked = {}, list(RL.flat(cards))
    for r in rows.values():
        rec = r.get("judge_ladder") or {}
        key = f"{rec.get('card')}#{rec.get('ladder')}"
        for ax in RL.AXES:
            ok = ax in (rec.get("axes") or [])
            cells[f"{key}|{ax}"] = {"usable": bool(ok or cells.get(f"{key}|{ax}", {}).get("usable"))}
    for fk in checked:
        for ax in RL.AXES:
            cells.setdefault(f"{fk}|{ax}", {"usable": False})
    return {"cells": cells, "checked": checked}


def _anchor_for(lad: dict, sha: str, units: list[dict]) -> list[str] | None:
    for k in ("1", "2", "3", "4"):
        if lad["rungs"][k]["sha"] == sha:
            return lad["rungs"][k]["replies"]
    from . import rpladder as RL
    for u in units:
        if RL.anchor_sha(u.get("replies") or []) == sha:
            return u["replies"]
    return None


def session_records(label: str, rows: dict[str, dict], cards: dict, judge_fn, cache, reader_id: str,
                    check: dict | None = None, judged_weight: float = SESSION_JUDGED_WEIGHT) -> list[dict]:
    """The reader on the session part of one model: (1) every stored comparison asked again, identical request;
    (2) the reader's own placement of each unit (production rung check, today's frozen-rung rule)."""
    from . import rpladder as RL
    units, by = [], {}
    for uid, r in rows.items():
        u = RL.unit_from_row(r, cards)
        if u is None:
            continue
        u["unit"] = uid
        units.append(u)
        by[(u["card"], u["seed"])] = u

    def other(u):
        for (cid, seed), v in by.items():
            if cid == u["card"] and seed != u["seed"]:
                return v
        return None

    chk = check or _check_from_rows(cards, rows)
    lads = RL.flat(cards)
    axes_text = RL.axis_lines()
    # (1) the judge's own comparisons, re-asked of the reader
    exact: dict[str, list[dict]] = {}
    for u in units:
        rec = rows[u["unit"]]["judge_ladder"]
        lad = lads.get(f"{rec.get('card')}#{rec.get('ladder')}")
        if lad is None or (rec.get("instrument") or {}).get("ladder_sha") != lad["ladder_sha"]:
            exact[u["unit"]] = []
            continue
        reqs = []
        seen = set()
        for p in rec.get("pairs") or []:
            k = (p["window"], p["axis"], p["rung"], p.get("anchor_sha"))
            if k in seen:
                continue
            seen.add(k)
            anchor = _anchor_for(lad, p.get("anchor_sha"), units)
            if anchor is None:
                continue
            for q in RL.pair_requests(lad, u, p["window"], p["axis"], p["rung"], anchor, "standard", reader_id,
                                      axes_text):
                q["anchor_sha"] = p.get("anchor_sha")
                reqs.append(q)
        exact[u["unit"]] = reqs
    RL._store(cache, judge_fn, [q for v in exact.values() for q in v], reader_id, True)
    # (2) the reader's own placements
    windows = sorted({w for r in rows.values() for w in (r["judge_ladder"].get("windows") or [])}) or list(
        RL.DEFAULT_WINDOWS)
    placed = RL.place_units(units, cards, judge_fn, cache, judge_model=reader_id, check=chk,
                            windows_used=tuple(windows), own_other=other)
    out = []
    for u in units:
        r = rows[u["unit"]]
        rec = r["judge_ladder"]
        prod = comparison_outcomes(rec.get("pairs") or [])
        got = [{**q, **(cache.get(q["key"]) or {})} for q in exact[u["unit"]]]
        mine = comparison_outcomes([{"window": g["window"], "axis": g["axis"], "rung": g["rung"],
                                     "subj_is": g["subj_is"], "better": g.get("better")}
                                    for g in got if g.get("better") is not None])
        both = sorted(set(prod) & set(mine), key=str)
        pl = placed.get(u["unit"]) or {}
        stale = RL.stale_reason(rec, cards, None, unit_sha=RL.anchor_sha(u["replies"]))
        pos_r = pl.get("position") if pl.get("complete") else None
        out.append({"part": SESSION_PART, "model": label, "unit": u["unit"], "row_sha": row_sha(r),
                    "reader": reader_id,
                    "decisions": [[prod[k], mine[k]] for k in both],
                    "decision_ids": ["W{}|{}|R{}".format(*k) for k in both],
                    "score_judge": None if stale else rec["position"] / 4.0,
                    "score_reader": None if (stale or pos_r is None) else pos_r / 4.0,
                    "score_excluded": stale,
                    "weight": judged_weight,
                    "reader_placement": {k: pl.get(k) for k in ("position", "cells", "complete", "axes")}})
    return out


# ------------------------------------------------------------------ adult
def adult_bands(scores: dict) -> dict[str, str]:
    out = {}
    for k, v in (scores or {}).items():
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            continue
        for lo, hi, name in ADULT_BANDS:
            if lo <= round(v) <= hi:
                out[k] = name
    return out


def adult_record(label: str, uid: str, row: dict, reader_v: dict, reader_id: str) -> dict:
    ps, rs = (row.get("judge") or {}).get("scores") or {}, (reader_v or {}).get("scores") or {}
    pb, rb = adult_bands(ps), adult_bands(rs)
    both = sorted(set(pb) & set(rb))

    def mean10(s):
        xs = [float(s[k]) for k in both]
        return sum(xs) / len(xs) / 10.0 if xs else None
    return {"part": adult_part(row), "model": label, "unit": uid, "row_sha": row_sha(row), "reader": reader_id,
            "decisions": [[pb[k], rb[k]] for k in both], "decision_ids": both,
            "score_judge": mean10(ps), "score_reader": mean10(rs), "weight": 1.0,
            "reader_verdict": {k: reader_v.get(k) for k in ("scores", "judge_failed", "refused") if k in reader_v}}


# ------------------------------------------------------------------ table
def reader_family_of(rec: dict) -> str | None:
    return family(str(rec.get("reader") or "").removesuffix("+think"))


def measured(rec: dict) -> str | None:
    """None when the record counts, else why its unit's agreement is not measured (lead 2026-11-19: the reader
    must be of another family than BOTH the judge and the graded model; a record made before the rule is checked
    here too, from its model label)."""
    if rec.get("not_measured"):
        return rec["not_measured"]
    gf = rec.get("graded_family", family(rec.get("model")))
    rf = reader_family_of(rec)
    if not gf:
        return "graded model family unknown"
    if rf == gf:
        return f"reader and graded model are both {gf}"
    return None


def placement_block(scored: list[dict]) -> dict:
    """Sessions (lead 2026-11-19): agreement on the PLACEMENT a session lands on (0-4 rungs; unit score =
    position / 4), weighted by distance: 1 - mean |judge - reader| / 4 rungs; plus the share within half a rung
    and the mean signed difference (reader - judge, rungs)."""
    d = [4.0 * (r["score_reader"] - r["score_judge"]) for r in scored]
    if not d:
        return {"n": 0, "weighted": None, "within_half_rung": None, "bias_rungs": None, "mad_rungs": None}
    return {"n": len(d), "weighted": 1.0 - sum(abs(x) for x in d) / len(d) / 4.0,
            "within_half_rung": sum(1 for x in d if abs(x) <= 0.5) / len(d),
            "bias_rungs": sum(d) / len(d), "mad_rungs": sum(abs(x) for x in d) / len(d)}


def summarize(records: list[dict]) -> dict:
    """The agreement table per part: {part: {n_units, n_decisions, raw, ac1, kappa, mad, corr: {model: r},
    model_means: {model: (judge, reader)}, same_order, judge_sd: {model: sd}, judge_sd_pooled}}; sessions also
    ``placement`` (:func:`placement_block`). Units whose agreement is not measured (:func:`measured`) are left
    out and counted in ``not_measured_by_model``."""
    parts: dict[str, list[dict]] = {}
    skipped: dict[str, dict[str, dict[str, int]]] = {}
    best: dict[tuple, dict] = {}
    for r in records:
        k = (r["part"], r.get("model"), r.get("unit"))
        if k not in best or (measured(best[k]) and not measured(r)):
            best[k] = r
    for r in best.values():
        why = measured(r)
        if why:
            skipped.setdefault(r["part"], {}).setdefault(r.get("model"), {}).setdefault(why, 0)
            skipped[r["part"]][r.get("model")][why] += 1
            continue
        parts.setdefault(r["part"], []).append(r)
    for part in skipped:
        parts.setdefault(part, [])
    out = {}
    for part, rs in sorted(parts.items()):
        dec = [tuple(d) for r in rs for d in r.get("decisions") or []]
        scored = [r for r in rs if r.get("score_judge") is not None and r.get("score_reader") is not None]
        diffs = [abs(r["score_judge"] - r["score_reader"]) for r in scored]
        models = sorted({r["model"] for r in rs})
        corr, means, jsd = {}, {}, {}
        for m in models:
            ms = [r for r in scored if r["model"] == m]
            corr[m] = pearson([r["score_judge"] for r in ms], [r["score_reader"] for r in ms])
            if ms:
                means[m] = (sum(r["score_judge"] for r in ms) / len(ms), sum(r["score_reader"] for r in ms) / len(ms))
                w = ms[0].get("weight", 1.0)
                jsd[m] = judge_sd([w * (r["score_judge"] - r["score_reader"]) for r in ms])
        order_j = sorted(means, key=lambda m: -means[m][0])
        order_r = sorted(means, key=lambda m: -means[m][1])
        vals = [v for v in jsd.values() if v is not None]
        out[part] = {"n_units": len(rs), "n_scored_units": len(scored), "n_decisions": len(dec),
                     "raw": raw_agreement(dec), "ac1": gwet_ac1(dec), "kappa": cohen_kappa(dec),
                     "mad": (sum(diffs) / len(diffs)) if diffs else None,
                     "corr": corr, "model_means": means, "order_judge": order_j, "order_reader": order_r,
                     "same_order": order_j == order_r,
                     "n_units_by_model": {m: sum(1 for r in rs if r["model"] == m) for m in models},
                     "judge_sd": jsd,
                     "judge_sd_pooled": math.sqrt(sum(v * v for v in vals) / len(vals)) if vals else None,
                     "excluded_scores": sum(1 for r in rs if r.get("score_excluded")),
                     "not_measured_by_model": skipped.get(part, {})}
        if part == SESSION_PART:
            out[part]["placement"] = placement_block(scored)
    return out


def _f(x, nd=2):
    return "-" if x is None else f"{x:.{nd}f}"


def render_table(summary: dict, reader: str, judge: str | None) -> str:
    L = [f"Second reader {reader} vs judge {judge or '?'} (statistic {STAT}; kappa for reference)",
         f"{'part':18} {'units':>5} {'decisions':>9} {'raw':>5} {'AC1':>5} {'kappa':>6} {'MAD':>5} "
         f"{'judge_sd':>8}  same order  per model (r; judge/reader mean)"]
    for part, s in summary.items():
        per = "; ".join(f"{m}: r={_f(s['corr'].get(m))} {_f((s['model_means'].get(m) or (None, None))[0])}/"
                        f"{_f((s['model_means'].get(m) or (None, None))[1])}" for m in s["n_units_by_model"])
        L.append(f"{part:18} {s['n_units']:>5} {s['n_decisions']:>9} {_f(s['raw']):>5} {_f(s['ac1']):>5} "
                 f"{_f(s['kappa']):>6} {_f(s['mad']):>5} {_f(s['judge_sd_pooled'], 3):>8}  "
                 f"{'yes' if s['same_order'] else 'NO':10}  {per}")
        if s.get("placement", {}).get("n"):
            pb = s["placement"]
            L.append(f"  placements: n {pb['n']}, weighted agreement {_f(pb['weighted'], 3)}, within half a rung "
                     f"{_f(pb['within_half_rung'])}, reader - judge {pb['bias_rungs']:+.2f} rungs")
        for m, why in (s.get("not_measured_by_model") or {}).items():
            L.append(f"  {m}: agreement not measured on {sum(why.values())} unit(s): {', '.join(why)}")
        if s.get("excluded_scores"):
            L.append(f"  ({s['excluded_scores']} unit score(s) left out: the stored placement met a substitute rung)")
    return "\n".join(L)


# ------------------------------------------------------------------ files
def slug(model_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", model_id.rsplit("/", 1)[-1])[:80]


def second_path(folder: Path, reader_id: str) -> Path:
    return Path(folder) / "agree" / f"second-{slug(reader_id)}.jsonl"


def cache_path(folder: Path, reader_id: str) -> Path:
    return Path(folder) / "agree" / f"pairs-{slug(reader_id)}.jsonl"


def load_records(folder: Path, reader_id: str) -> list[dict]:
    p = second_path(folder, reader_id)
    if not p.exists():
        return []
    out = {}
    for line in p.open(encoding="utf-8"):
        line = line.strip()
        if line:
            r = json.loads(line)
            out[(r["part"], r["unit"], r["row_sha"])] = r
    return list(out.values())


def append_records(folder: Path, reader_id: str, recs: list[dict]) -> None:
    p = second_path(folder, reader_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        for r in recs:
            f.write(json.dumps(r, ensure_ascii=False, default=str) + "\n")


# ------------------------------------------------------------------ driver
def graded_family(label: str, row: dict | None) -> str | None:
    """The family of the model a row grades: its stored model id, else the board label."""
    return family((row or {}).get("model_id")) or family(label)


def production_judge(rows: list[dict]) -> str | None:
    ids = [r.get("judge_model") for r in rows if r.get("judge_model")]
    return max(set(ids), key=ids.count) if ids else None


def _case_map() -> dict[str, dict]:
    try:
        from . import config
        return {c["id"]: c for c in config.load_cases() if c.get("id") in LONG_CASES}
    except Exception as e:  # noqa: BLE001
        log.warning("agree: cases not loadable (%s): long-scene scores fall back to the judged facts", e)
        return {}


def reader_judge_fn(jc, stats: dict, thinking: bool):
    """The ladder pair caller for the reader: production's (rpladder_run.make_judge_fn) when not thinking, else the
    same request, schema and parser with the reader's thinking left on and a thinking-sized output budget."""
    from .rpladder_run import make_judge_fn
    if not thinking:
        return make_judge_fn(jc, stats)
    from . import rpladder as RL
    from .api import strip_thinking_tags

    def one(req):
        kw = dict(response_format=RL.SCHEMA, max_tokens=max(RL.CALL["max_tokens"], READER_THINK_MAX_TOKENS),
                  temperature=RL.CALL["temperature"])
        for _ in (1, 2):
            try:
                res = jc.chat(req["messages"], **kw)
            except Exception as e:  # noqa: BLE001 - one call never stops the pass; the cell stays pending
                log.warning("agree: reader pair call failed (%s): %s", type(e).__name__, str(e)[:200])
                stats["errors"] = stats.get("errors", 0) + 1
                return req["key"], None
            stats["calls"] = stats.get("calls", 0) + 1
            v = RL.parse_verdict(strip_thinking_tags(res.response_text or "").strip())
            if v.get("better") in ("A", "B", "none"):
                return req["key"], {"better": v["better"], "reason": str(v.get("reason") or "")[:300]}
        stats["unparsable"] = stats.get("unparsable", 0) + 1
        return req["key"], None

    def judge(reqs):
        from concurrent.futures import ThreadPoolExecutor
        n = max(1, int(getattr(jc, "concurrency", None) or 4))
        with ThreadPoolExecutor(n) as ex:
            return dict(ex.map(one, reqs))
    return judge


def run(folders: list[Path], jc, reader_id: str, *, adult: bool = False, parts: tuple = (LONG_PART, SESSION_PART),
        judge_fn=None, cards: dict | None = None, cases: dict | None = None, ladder_check=None,
        thinking: bool = READER_THINKING) -> list[dict]:
    """Re-grade every folder's judged units with the reader ``jc`` (a JudgeClient-like object with ``chat``);
    ``judge_fn`` = the pair-request caller for sessions (default rpladder_run.make_judge_fn(jc)). Records already
    in ``<folder>/agree/`` for the same unit and row are reused (a resumed run asks nothing twice). Returns every
    record of these folders. ``thinking`` = the reader thinks (default); its records are kept apart
    (:func:`reader_key`)."""
    from . import rpladder as RL
    all_recs: list[dict] = []
    rkey = reader_key(reader_id, thinking)
    cases = _case_map() if cases is None else cases
    for folder in folders:
        folder = Path(folder)
        label, rows = read_folder(folder)
        jid = production_judge(rows)
        check_reader(reader_id, jid)
        have = {(r["part"], r["unit"], r["row_sha"]): r for r in load_records(folder, rkey)}
        new: list[dict] = []
        rfam = family(reader_id)
        clash: list[dict] = []
        gf_of: dict = {}

        def fits(part, uid, row):
            gf = gf_of[uid] = graded_family(label, row)
            if gf and gf != rfam:
                return True
            clash.append({"part": part, "model": label, "unit": uid, "row_sha": row_sha(row), "reader": rkey,
                          "graded_family": gf, "decisions": [], "decision_ids": [],
                          "not_measured": "graded model family unknown" if not gf
                          else f"reader and graded model are both {gf}"})
            return False
        if LONG_PART in parts:
            for uid, row in sorted(long_units(rows).items()):
                k = (LONG_PART, uid, row_sha(row))
                # a stored record whose reader verdict could not be scored (score_reader None: elements the reader left
                # unjudged) is asked again on resume; before, such a unit stuck for good (pilot 2, RD4-kettle-saddle)
                if (k in have and have[k].get("score_reader") is not None) or not fits(LONG_PART, uid, row):
                    continue
                v = regrade_long(jc, row, thinking)
                if v.get("judge_failed") or not (v.get("elements") or {}).get("results"):
                    log.warning("agree: reader could not judge %s %s", label, uid)
                    continue
                new.append(long_record(label, uid, row, v, cases.get(row["case_id"]), rkey))
        if SESSION_PART in parts:
            srows = {u: r for u, r in session_units(rows).items() if (SESSION_PART, u, row_sha(r)) not in have
                     and fits(SESSION_PART, u, r)}
            if srows:
                cards = cards if cards is not None else RL.load()
                if judge_fn is None:
                    judge_fn = reader_judge_fn(jc, {}, thinking)
                cache = RL.VerdictCache(cache_path(folder, rkey))
                chk = ladder_check
                if chk is None and jid:
                    c0 = RL.load_check(folder, cards, jid)
                    chk = c0 if set(c0.get("checked") or []) == set(RL.flat(cards)) else None
                new += session_records(label, srows, cards, judge_fn, cache, rkey, chk)
        if adult:
            for uid, row in sorted(adult_units(rows).items()):
                k = (adult_part(row), uid, row_sha(row))
                if k in have or not fits(adult_part(row), uid, row):
                    continue
                from . import judge as J
                pol = dict((row.get("judge") or {}).get("policy") or {})
                if thinking:
                    pol["thinking"] = True
                    pol["max_tokens"] = max(int(pol.get("max_tokens") or 0), READER_THINK_MAX_TOKENS)
                prow = _strip(row)
                if pol:
                    prow["_judge_policy"] = pol
                v = J.judge_row(jc, prow, samples=int(pol.get("samples") or 1))
                if v.get("judge_failed"):
                    continue
                new.append(adult_record(label, uid, row, v, rkey))
        new += clash
        for r in new:
            r.setdefault("graded_family", gf_of.get(r["unit"], graded_family(label, None)))
        append_records(folder, rkey, new)
        all_recs += list(have.values()) + new
    return all_recs


def write_summary(path: Path, summary: dict, reader: str, judge: str | None, folders: list) -> None:
    body = {"reader": reader, "reader_family": family(reader), "judge": judge, "judge_family": family(judge),
            "statistic": STAT, "folders": [str(f) for f in folders], "parts": summary}
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(body, indent=1, default=list), encoding="utf-8")


def copy_for_test(x):
    return copy.deepcopy(x)


class SpendCapReached(RuntimeError):
    pass


class CappedClient:
    """A hosted reader under a spend cap: every call's usage is priced (models.yaml ``price``, USD per million
    input / output tokens) and the run stops before a call once the running total reaches the cap."""

    def __init__(self, jc, price: dict, cap_usd: float):
        self._jc, self.price, self.cap, self.spent = jc, price or {}, float(cap_usd), 0.0

    def __getattr__(self, k):
        return getattr(self._jc, k)

    def chat(self, messages, **kw):
        if self.spent >= self.cap:
            raise SpendCapReached(f"agree: spend cap ${self.cap:.2f} reached (${self.spent:.2f})")
        res = self._jc.chat(messages, **kw)
        u = getattr(res, "usage", None) or {}
        if not isinstance(u, dict):
            u = getattr(u, "__dict__", {}) or {}
        pin = float(u.get("prompt_tokens") or 0)
        pout = float(u.get("completion_tokens") or 0)
        self.spent += (pin * float(self.price.get("input") or 0) + pout * float(self.price.get("output") or 0)) / 1e6
        return res
