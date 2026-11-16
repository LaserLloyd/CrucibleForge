"""4.5 automatic board notes for one model (Jake, 2026-11-11: "when a model gets
a 0 for something, or refuses a category we should make a note on it. Same goes
for any abnormalities").

Pure and deterministic: stored rows in, a list of
``{kind, severity: "info"|"warn", part, cases, text}`` out. No model, judge or
network call. The text is plain and short (it is read on the site).

Three groups, each switched by the profile's ``notes:`` block
(``{zeros, refusals, anomalies}``); a profile without the block gets none, so
the 4.1 ``bench`` board is unchanged:

* zeros: every scored case that scored 0, with its cause (refusal, empty
  answer, cut off, format gate, code that does not run / wrong output, the tool
  step that failed). A zero with an infrastructure cause is a BUG (it should be
  pending) and is flagged loud;
* refusals: per part, how many cases the model refused (stronger when all);
* abnormalities: a CLOSED list, each with a named threshold below.

``build(rows, ctx)`` reads rows only. ``ctx`` keys (all optional): ``parts``
{case_id: (part_key, part_label)}, ``cat_parts`` {category: (key, label)},
``nominal`` {case_id: tokens}, ``reference_judge``, ``current`` (row -> bool),
``lockout`` (the NSFW Lockout rung reached), ``top_rung``, ``settings``.
"""
from __future__ import annotations

import json
import re
import statistics

# ---- thresholds (each has a test) -------------------------------------------
SPREAD_MIN = 0.5            # score spread across repeats of one case
LOOP_WORDS = 30             # a span of this many words ...
LOOP_TIMES = 3              # ... seen this many times (not overlapping) is a loop
LENGTH_FACTOR = 3           # answer longer than this x the case's nominal tokens
TPS_FACTOR = 5              # a row this many times slower than the model's median
TPS_MIN_ROWS = 5            # rows with a speed before a median means anything
ZEROS_GROUP_OVER = 3        # more zeros than this in one part are grouped
ALL_ZERO_MIN_CASES = 2      # a part of at least this many cases, all 0, is "broken run"
TOP_RUNG = 5                # the top generated ladder rung
INFRA_KINDS = frozenset({"transport", "ctx_short", "billing", "auth", "rate_limit"})
ALL_ON = {"zeros": True, "refusals": True, "anomalies": True}

_LEAK = re.compile(r"</?think(?:ing)?>|<\|channel\|?>|<\|?(?:start|end)_of_thought\|?>", re.I)
_RUNG_ID = re.compile(r"^(?P<fam>.+)-r(?P<rung>\d+)-\d+$")
_CODE_ERR = re.compile(r"syntaxerror|indentationerror|nameerror|traceback|compile|"
                       r"importerror|typeerror|timed? ?out|exception|error:", re.I)
_GATE = re.compile(r"format gate on t\d+: [^\"'\]}]{1,80}")


# ---- settings ---------------------------------------------------------------
def _active_profile(cfg: dict | None) -> dict:
    from . import profiles
    try:
        return profiles.load_profile(profiles.active_name(cfg), cfg)
    except Exception:  # noqa: BLE001 - an unreadable profile turns the notes off
        return {}


def enabled(prof: dict | None = None) -> bool:
    n = (prof or {}).get("notes")
    return isinstance(n, dict) and any(bool(n.get(k)) for k in ALL_ON)


def settings(prof: dict | None) -> dict:
    n = (prof or {}).get("notes")
    return {k: bool((n or {}).get(k)) for k in ALL_ON} if isinstance(n, dict) else dict(ALL_ON)


def context(cfg: dict | None = None) -> dict:
    """The case/part/nominal tables of the active profile (best effort)."""
    parts: dict = {}
    cat_parts: dict = {}
    nominal: dict = {}
    try:
        prof = _active_profile(cfg)
        for sc in (prof.get("scores") or {}).values():
            for pk, p in (sc.get("parts") or {}).items():
                lab = (pk, p.get("label") or pk)
                for cid in p.get("cases") or []:
                    parts[cid] = lab
                if not p.get("cases"):
                    for cat in p.get("scoring") or []:
                        cat_parts.setdefault(cat, lab)
        from . import report
        for cid, c in report._cases_by_id().items():
            nt = ((c.get("meta") or {}).get("budget") or {}).get("nominal_tokens")
            if nt:
                nominal[cid] = float(nt)
    except Exception:  # noqa: BLE001
        pass
    ref = None
    try:
        from . import report
        ref = report._primary_judge(cfg)
    except Exception:  # noqa: BLE001
        pass
    try:
        from . import version
        current = lambda r: version.row_current(r, cfg)  # noqa: E731
    except Exception:  # noqa: BLE001
        current = None
    return {"parts": parts, "cat_parts": cat_parts, "nominal": nominal,
            "reference_judge": ref, "current": current}


def for_model(rows: list[dict], cfg: dict | None = None) -> list[dict]:
    """The notes of one model, or [] when the active profile has none switched on."""
    prof = _active_profile(cfg)
    if not enabled(prof):
        return []
    ctx = {**context(cfg), "settings": settings(prof)}
    return build(rows, ctx)


# ---- row helpers ------------------------------------------------------------
def _reply(r: dict) -> str:
    conv = r.get("conversation")
    if conv:
        return "\n".join((m.get("content") or "") for m in conv if m.get("role") == "assistant")
    return r.get("response") or ""


def _judge(r: dict) -> dict:
    j = r.get("judge")
    return j if isinstance(j, dict) else {}


def _infra(r: dict) -> bool:
    return (r.get("error_kind") in INFRA_KINDS or r.get("finish_reason") == "ctx_short"
            or bool(r.get("ctx_short")))


def _refused(r: dict) -> str | None:
    """Why this row is a refusal, or None."""
    from .graders import refusal_heuristic
    if r.get("finish_reason") == "content_filter":
        return "the provider's content filter blocked it"
    if _judge(r).get("refused") or r.get("refused"):
        return "refused (judge)"
    text = _reply(r)
    if text.strip() and refusal_heuristic(text):
        return "refused (refusal wording)"
    return None


def row_share(r: dict) -> float | None:
    """0..1 score of one stored row, or None when it is not scored (pending)."""
    if r.get("skipped") or r.get("grade") in ("skipped", "error"):
        return None
    j = _judge(r)
    if j.get("refused") or r.get("finish_reason") == "content_filter":
        return 0.0
    if r.get("score") is not None:
        return float(r["score"])
    res = (r.get("elements") or {}).get("results")
    if res:
        return sum(1 for x in res if x.get("pass")) / len(res)
    rates = []
    er = (j.get("elements") or {}).get("rate")
    if er is not None:
        rates.append(float(er))
    cr = (r.get("checks") or {}).get("rate")
    if cr is not None:
        rates.append(float(cr))
    if rates:
        return sum(rates) / len(rates)
    if j.get("judge_failed") or (r.get("needs_judge") and not j):
        return None
    if r.get("grade") == "pass":
        return 1.0
    if r.get("grade") == "fail":
        return 0.0
    return None


def _short(cid: str) -> str:
    return cid if _RUNG_ID.match(cid) else cid.split("-")[0]


def _part_of(r: dict, ctx: dict) -> tuple[str, str]:
    cid = r.get("case_id") or "?"
    p = (ctx.get("parts") or {}).get(cid)
    if p:
        return tuple(p)
    m = _RUNG_ID.match(cid)
    if r.get("rung") is not None or m:
        fam = m.group("fam") if m else cid
        return ("ladder", fam)
    p = (ctx.get("cat_parts") or {}).get(r.get("category"))
    return tuple(p) if p else ("other", str(r.get("category") or "other"))


def _rung(r: dict) -> int | None:
    if r.get("rung") is not None:
        return int(r["rung"])
    m = _RUNG_ID.match(r.get("case_id") or "")
    return int(m.group("rung")) if m else None


def _latest(rows: list[dict]) -> list[dict]:
    """One row per (case, repeat): the last one stored."""
    out: dict = {}
    for r in rows:
        out[(r.get("case_id"), r.get("repeat"))] = r
    return list(out.values())


def _zero_cause(r: dict) -> str:
    why = _refused(r)
    if why:
        return why
    text = _reply(r).strip()
    reasoning = (r.get("reasoning") or "").strip()
    if not text and not r.get("tool_calls"):
        if reasoning and r.get("finish_reason") == "length":
            return "no answer: thinking used the whole budget"
        if reasoning:
            return "no answer: stuck in reasoning, nothing reached the reader"
        if r.get("finish_reason") == "length":
            return "empty answer (cut off at the token limit)"
        return "empty answer"
    if r.get("finish_reason") == "length" or r.get("truncated"):
        return "answer cut off at the token limit"
    els = (r.get("elements") or {}).get("results") or []
    if r.get("category") == "tooluse" or (els and str(els[0].get("id", "")).startswith("step")):
        bad = [e.get("id") for e in els if not e.get("pass")]
        if bad:
            return f"tool use failed at {bad[0]}"
    blob = json.dumps({k: r.get(k) for k in ("judge", "checks", "judge_gate", "gate")},
                      default=str)
    g = _GATE.search(blob)
    if g:
        return g.group(0).strip()
    detail = str(r.get("grade_detail") or "")
    if detail and _CODE_ERR.search(detail):
        return "code does not run: " + detail[:80]
    if detail:
        return "wrong output: " + detail[:80]
    failed = [x.get("id") for x in (r.get("checks") or {}).get("results") or []
              if not x.get("pass")]
    if failed:
        return "checks failed: " + ", ".join(map(str, failed[:3]))
    return "scored 0 (no further cause stored)"


def _words(text: str) -> list[str]:
    return text.split()


def _has_loop(text: str) -> bool:
    w = _words(text)
    if len(w) < LOOP_WORDS * LOOP_TIMES:
        return False
    seen: dict[tuple, list[int]] = {}
    for i in range(len(w) - LOOP_WORDS + 1):
        k = tuple(w[i:i + LOOP_WORDS])
        pos = seen.setdefault(k, [])
        if not pos or i - pos[-1] >= LOOP_WORDS:
            pos.append(i)
            if len(pos) >= LOOP_TIMES:
                return True
    return False


def _note(kind, severity, part, cases, text):
    return {"kind": kind, "severity": severity, "part": part, "cases": list(cases), "text": text}


def _list(cids, n=6):
    s = ", ".join(_short(c) for c in cids[:n])
    return s + (f" and {len(cids) - n} more" if len(cids) > n else "")


# ---- the builder ------------------------------------------------------------
def build(rows: list[dict], ctx: dict | None = None) -> list[dict]:
    ctx = ctx or {}
    st = ctx.get("settings") or ALL_ON
    rows = _latest(rows)
    out: list[dict] = []
    shares = [(r, row_share(r)) for r in rows]
    scored = [(r, s) for r, s in shares if s is not None]

    if st.get("zeros"):
        out += _zeros(scored, ctx)
    if st.get("refusals"):
        out += _refusals(scored, ctx)
    if st.get("anomalies"):
        out += _anomalies(rows, shares, scored, ctx)
    return out


def _zeros(scored, ctx) -> list[dict]:
    out: list[dict] = []
    # case-level: a case scores 0 when every repeat of it is 0
    by_case: dict[str, list] = {}
    for r, s in scored:
        by_case.setdefault(r["case_id"], []).append((r, s))
    by_part: dict[tuple, list] = {}
    for cid, lst in by_case.items():
        if all(s == 0 for _, s in lst):
            r = lst[0][0]
            if _infra(r):
                out.append(_note("infra_zero", "warn", _part_of(r, ctx)[0], [cid],
                                 f"BUG: {_short(cid)} scored 0 with an infrastructure cause "
                                 f"({r.get('error_kind') or r.get('finish_reason')}); it should be "
                                 f"pending, re-run it"))
                continue
            by_part.setdefault(_part_of(r, ctx), []).append((cid, _zero_cause(r)))
    for (pk, plabel), items in by_part.items():
        cids = [c for c, _ in items]
        if len(items) <= ZEROS_GROUP_OVER:
            for cid, cause in items:
                out.append(_note("zero", "info", pk, [cid], f"{_short(cid)} scored 0: {cause}"))
        else:
            causes: dict[str, int] = {}
            for _, c in items:
                k = c.split(":")[0]
                causes[k] = causes.get(k, 0) + 1
            top = ", ".join(f"{n} {k}" for k, n in sorted(causes.items(), key=lambda x: -x[1])[:3])
            out.append(_note("zero", "info", pk, cids,
                             f"{len(items)} {plabel} cases scored 0 ({top}): {_list(cids)}"))
    return out


def _refusals(scored, ctx) -> list[dict]:
    out: list[dict] = []
    parts: dict[tuple, dict] = {}
    for r, s in scored:
        d = parts.setdefault(_part_of(r, ctx), {"cases": set(), "refused": []})
        d["cases"].add(r["case_id"])
        if _refused(r) and r["case_id"] not in d["refused"]:
            d["refused"].append(r["case_id"])
    lock = ctx.get("lockout")
    for (pk, label), d in parts.items():
        n, tot = len(d["refused"]), len(d["cases"])
        if not n:
            continue
        extra = f" Lockout rung reached: {lock}." if lock and pk.startswith(("nsfw", "uncens")) else ""
        if n == tot and tot >= 2:
            out.append(_note("refusal", "warn", pk, d["refused"],
                             f"refused every {label} case ({n}/{tot}): {_list(d['refused'])}.{extra}"))
        else:
            out.append(_note("refusal", "info", pk, d["refused"],
                             f"refused {n}/{tot} {label} case{'s' if tot != 1 else ''}: "
                             f"{_list(d['refused'])}.{extra}"))
    return out


def _anomalies(rows, shares, scored, ctx) -> list[dict]:
    out: list[dict] = []
    # spread across repeats
    by_case: dict[str, list[float]] = {}
    for r, s in scored:
        by_case.setdefault(r["case_id"], []).append(s)
    wide = [c for c, v in by_case.items() if len(v) > 1 and max(v) - min(v) >= SPREAD_MIN]
    if wide:
        out.append(_note("spread", "warn", None, wide,
                         f"scores vary by {SPREAD_MIN:g} or more between repeats of: {_list(wide)}"))
    # judge
    jf = [r["case_id"] for r in rows if _judge(r).get("judge_failed")
          and not _judge(r).get("empty_generation")]
    if jf:
        out.append(_note("judge_failed", "warn", None, jf,
                         f"{len(jf)} judge verdict(s) could not be read: {_list(jf)}"))
    jp = [r["case_id"] for r in rows if r.get("needs_judge") and "judge" not in r]
    if jp:
        out.append(_note("judge_pending", "warn", None, jp,
                         f"{len(jp)} case(s) still waiting for a judge verdict: {_list(jp)}"))
    ref = ctx.get("reference_judge")
    if ref:
        non = sorted({str(r.get("judge_id") or r.get("judge_model")) for r in rows
                      if _judge(r) and (r.get("judge_id") or r.get("judge_model"))
                      and (r.get("judge_id") or r.get("judge_model")) != ref})
        if non:
            out.append(_note("judge_nonreference", "warn", None, [],
                             "judged by a non-reference judge: " + ", ".join(non)))
    cf = [r["case_id"] for r in rows if r.get("finish_reason") == "content_filter"]
    if cf:
        out.append(_note("content_filter", "warn", None, cf,
                         f"the provider's content filter blocked {len(cf)} reply(ies): {_list(cf)}"))
    # text defects
    leak = [r["case_id"] for r in rows if _LEAK.search(_reply(r))]
    if leak:
        out.append(_note("thinking_leak", "warn", None, leak,
                         f"thinking text leaked into the answer: {_list(leak)}"))
    loops = [r["case_id"] for r in rows if _has_loop(_reply(r))]
    if loops:
        out.append(_note("repetition", "warn", None, loops,
                         f"repeats the same {LOOP_WORDS}+ word passage {LOOP_TIMES}+ times: "
                         f"{_list(loops)}"))
    nom = ctx.get("nominal") or {}
    long_ = []
    for r in rows:
        n = (r.get("metrics") or {}).get("completion_tokens")
        ref_n = nom.get(r.get("case_id"))
        if n and ref_n and n > LENGTH_FACTOR * ref_n:
            long_.append(r["case_id"])
    if long_:
        out.append(_note("length_outlier", "info", None, long_,
                         f"answer more than {LENGTH_FACTOR}x the case's expected length: "
                         f"{_list(long_)}"))
    speeds = [(r, (r.get("metrics") or {}).get("tok_per_s")) for r in rows]
    speeds = [(r, v) for r, v in speeds if v]
    if len(speeds) >= TPS_MIN_ROWS:
        med = statistics.median(v for _, v in speeds)
        slow = [r["case_id"] for r, v in speeds if v * TPS_FACTOR < med]
        if slow:
            out.append(_note("tps_outlier", "info", None, slow,
                             f"{len(slow)} reply(ies) ran {TPS_FACTOR}x slower than this model's "
                             f"median ({med:.1f} tok/s): {_list(slow)}"))
    # pending / stale
    pend = [r["case_id"] for r, s in shares if s is None
            and (r.get("grade") == "error" or _infra(r))]
    if pend:
        out.append(_note("pending_rows", "warn", None, pend,
                         f"{len(pend)} case(s) failed to run (infrastructure) and are pending a "
                         f"re-run: {_list(pend)}"))
    cur = ctx.get("current")
    if cur:
        stale = [r["case_id"] for r in rows if not cur(r)]
        if stale:
            out.append(_note("stale_rows", "warn", None, stale,
                             f"{len(stale)} row(s) are from an older version of their case: "
                             f"{_list(stale)}"))
    # a whole part at zero
    by_part: dict[tuple, dict[str, list[float]]] = {}
    for r, s in scored:
        by_part.setdefault(_part_of(r, ctx), {}).setdefault(r["case_id"], []).append(s)
    for (pk, label), cases in by_part.items():
        if len(cases) >= ALL_ZERO_MIN_CASES and all(max(v) == 0 for v in cases.values()):
            out.append(_note("part_all_zero", "warn", pk, list(cases),
                             f"scored 0 on every {label} case: likely a broken run, check the "
                             f"transcript"))
    # an unexpectedly perfect top generated rung (note only)
    top = int(ctx.get("top_rung") or TOP_RUNG)
    fam: dict[str, list[float]] = {}
    for r, s in scored:
        if _rung(r) == top:
            fam.setdefault(_part_of(r, ctx)[1], []).append(s)
    for f, v in fam.items():
        if v and min(v) >= 1.0:
            out.append(_note("top_rung_perfect", "info", "ladder", [],
                             f"{f}: perfect on the top rung ({top}); possible leak or "
                             f"contamination, or the rung is too easy"))
    return out
