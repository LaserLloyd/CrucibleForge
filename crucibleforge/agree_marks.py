"""The judged parts' marks (calibration/judge-marks-45.json) tested on a second-reader agreement table
(crucibleforge agree) and, for long scenes, the D1 conditions (DECISIONS-45 D1 with (c) replaced by the agreement
test). ``scripts/check_judge_marks.py`` is the command; the board reads the result file it writes
(``<results>/judge_marks.json``, report.judge_agreement_reason)."""
from __future__ import annotations

import fnmatch
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MARKS = ROOT / "calibration" / "judge-marks-45.json"
RESULT_NAME = "judge_marks.json"
#: a model's part score must move by more than the part's margin before the order test binds
OOC_DETAIL = "graded reply not played in character"


def load_marks(path: Path | str | None = None) -> dict:
    return json.loads(Path(path or MARKS).read_text(encoding="utf-8"))


def marks_for(marks: dict, part: str) -> dict | None:
    ps = marks.get("parts") or {}
    if part in ps:
        return ps[part]
    for k, v in ps.items():
        if fnmatch.fnmatch(part, k):
            return v
    return None


# ------------------------------------------------------------------ agreement marks
Z90 = 1.6448536269514722


def order_beyond_margin(means: dict, margin) -> list[str]:
    """Model pairs whose judge scores differ by more than their combined margin and whose order the reader
    reverses. ``margin`` = one share for every model, or {model: share}; combined = sqrt(m_a^2 + m_b^2)."""
    mg = margin if isinstance(margin, dict) else {m: margin for m in means}
    bad = []
    ms = sorted(means)
    for i, a in enumerate(ms):
        for b in ms[i + 1:]:
            (ja, ra), (jb, rb) = means[a], means[b]
            comb = ((mg.get(a) or 0.0) ** 2 + (mg.get(b) or 0.0) ** 2) ** 0.5
            if abs(ja - jb) > comb and (ja - jb) * (ra - rb) <= 0:
                bad.append(f"{a}/{b}")
    return bad


def judge_term_share(s: dict) -> float | None:
    """1.645 x the largest per-model judge SD (share of the part), else the pooled one."""
    v = [x for x in (s.get("judge_sd") or {}).values() if x is not None]
    sd = max(v) if v else s.get("judge_sd_pooled")
    return None if sd is None else Z90 * sd


def model_margins(part: str, s: dict, mk: dict, margins: dict | None) -> dict:
    """{model: margin share}: the board's (report) when given, else the default share with the judge term."""
    given = (margins or {}).get(part)
    out = {}
    for m in s.get("model_means") or {}:
        if isinstance(given, dict) and given.get(m) is not None:
            out[m] = given[m]
        else:
            jt = Z90 * ((s.get("judge_sd") or {}).get(m) or s.get("judge_sd_pooled") or 0.0)
            base = given if isinstance(given, (int, float)) else mk.get("default_margin_share", 0.05)
            out[m] = (base ** 2 + jt ** 2) ** 0.5
    return out


def check_primary(part: str, s: dict, mk: dict, primary: dict, margins: dict | None = None) -> list[str]:
    """The revised (2026-11-19) hard marks: judge term, order, coverage, measured units. Empty = pass."""
    fails = []
    jt = judge_term_share(s)
    cap = float(primary.get("max_judge_term_share", 0.05))
    if jt is None:
        fails.append("judge term not measurable (no unit scored by both readers)")
    elif jt > cap:
        fails.append(f"judge term {100 * jt:.1f}% of the part > {100 * cap:.0f}%")
    if primary.get("order_beyond_combined_margin"):
        bad = order_beyond_margin({m: tuple(v) for m, v in (s.get("model_means") or {}).items()},
                                  model_margins(part, s, mk, margins))
        if bad:
            fails.append(f"reader reverses models further apart than their combined margin: {', '.join(bad)}")
    nm = s.get("not_measured_by_model") or {}
    if nm:
        fails.append("agreement not measured: " + "; ".join(
            f"{m} {sum(w.values())} unit(s) ({', '.join(w)})" for m, w in sorted(nm.items(), key=str)))
    few = {m: n for m, n in (s.get("n_units_by_model") or {}).items() if n < int(mk.get("min_units_per_model", 1))}
    if few:
        fails.append("too few units read: " + ", ".join(f"{m} {n}" for m, n in sorted(few.items())))
    return fails


def informational(s: dict, mk: dict) -> list[str]:
    """The first marks (2026-11-19, per decision) and the placement figures, flagged, never failing a part."""
    inf = mk.get("informational") or {}
    out = []

    def flag(ok, ceiling=False):
        # the word follows the numbers: a ceiling that is missed is ABOVE it, a floor that is missed is below it
        return "ok" if ok else ("above" if ceiling else "below")
    if "min_raw" in inf and s.get("raw") is not None:
        out.append(f"raw agreement per decision {_p(s['raw'])} ({flag(s['raw'] >= inf['min_raw'])} "
                   f"{_p(inf['min_raw'])})")
    if "min_ac1" in inf and s.get("ac1") is not None:
        out.append(f"AC1 per decision {_n(s['ac1'])} ({flag(s['ac1'] >= inf['min_ac1'])} {inf['min_ac1']:.2f})")
    if "max_mad" in inf and s.get("mad") is not None:
        out.append(f"mean unit difference {_n(s['mad'])} ({flag(s['mad'] <= inf['max_mad'], ceiling=True)} {inf['max_mad']:.3f})")
    pb = s.get("placement") or {}
    if pb.get("n"):
        if "min_placement_weighted" in inf:
            out.append(f"placement agreement (distance-weighted) {_n(pb['weighted'])} "
                       f"({flag(pb['weighted'] >= inf['min_placement_weighted'])} {inf['min_placement_weighted']:.2f})")
        if "min_within_half_rung" in inf:
            out.append(f"placements within half a rung {_p(pb['within_half_rung'])} "
                       f"({flag(pb['within_half_rung'] >= inf['min_within_half_rung'])} "
                       f"{_p(inf['min_within_half_rung'])})")
        out.append(f"reader - judge {pb['bias_rungs']:+.2f} rungs on average")
    return out


def check_agreement(part: str, s: dict, mk: dict, margins: dict | None = None, primary: dict | None = None) -> list[str]:
    """Failed hard marks (empty = pass)."""
    return check_primary(part, s, mk, primary or load_marks().get("primary") or {}, margins)


def _p(x):
    return "-" if x is None else f"{100 * x:.1f}%"


def _n(x):
    return "-" if x is None else f"{x:.3f}"


# ------------------------------------------------------------------ D1 (long scenes)
def d1_a(rows_by_model: dict) -> list[str]:
    """(a) every long-scene unit judged, stamped (judge.instrument) and carrying the OOC rescue's keys."""
    from . import agree as AG
    bad = []
    for m, rows in rows_by_model.items():
        for uid, r in AG.long_units(rows).items():
            j = r.get("judge") or {}
            g = j.get("groups") or []
            ok = bool(j.get("instrument")) and bool(g) and all(
                all(k in x for k in ("refused", "ooc_quote", "in_character_votes")) for x in g)
            if not ok:
                bad.append(f"{m} {uid.split('|')[1]}")
    return [f"(a) not judged/stamped/rescue keys: {', '.join(bad[:6])}{' ...' if len(bad) > 6 else ''}"] if bad else []


def ooc_unexplained(row: dict, case: dict) -> list[str]:
    """Fact ids zeroed as out of character with neither a recorded refusal nor a format reason on their turns."""
    from . import rpdeep
    sr = rpdeep.score_row(row, case)
    j = row.get("judge") or {}
    groups = j.get("groups") or []
    replies = [x.get("content") or "" for x in row.get("conversation") or [] if x.get("role") == "assistant"]
    gate = case.get("format_gate") or "rp"
    out = []
    for it in sr.get("items") or []:
        if it.get("grader") != "judge" or OOC_DETAIL not in str(it.get("detail") or ""):
            continue
        g = next((x for x in groups if it["id"] in (x.get("elements") or [])), {})
        if g.get("refused"):
            continue
        if any(rpdeep.ooc_reason(replies[t - 1], gate) for t in g.get("turns") or [] if 0 < t <= len(replies)):
            continue
        out.append(it["id"])
    return out


def d1_b(rows_by_model: dict, cases: dict) -> list[str]:
    from . import agree as AG
    bad = []
    for m, rows in rows_by_model.items():
        for uid, r in AG.long_units(rows).items():
            c = cases.get(r["case_id"])
            if c is None:
                continue
            ids = ooc_unexplained(r, c)
            if ids:
                bad.append(f"{m} {r['case_id']} ({len(ids)} facts)")
    return [f"(b) facts zeroed out of character without a refusal or format reason: {', '.join(bad)}"] if bad else []


def d1_d(scores: dict, groups: list[list[str]]) -> list[str]:
    """(d) the written known order: every model matching an earlier group scores above every model of a later one.
    Fewer than two groups present = not testable = fail (the order is a condition, not a nicety)."""
    present = []
    for g in groups:
        ms = [m for m in scores if any(re.search(p, m, re.I) for p in g)]
        if ms:
            present.append(ms)
    if len(present) < 2:
        return [f"(d) known order not testable: fewer than two of its groups on the board ({sorted(scores)})"]
    bad = []
    for i, hi in enumerate(present):
        for lo in present[i + 1:]:
            for a in hi:
                for b in lo:
                    if a != b and not scores[a] > scores[b]:
                        bad.append(f"{a} <= {b}")
    return [f"(d) known order broken: {', '.join(bad)}"] if bad else []


def d1_e(rows_by_model: dict) -> list[str]:
    from . import agree as AG
    bad = []
    for m, rows in rows_by_model.items():
        have = {u.split("|")[1] for u in AG.long_units(rows)}
        miss = [c for c in AG.LONG_CASES if c not in have]
        if miss:
            bad.append(f"{m} misses {len(miss)}")
    return [f"(e) not every case measured: {', '.join(bad)}"] if bad else []


def constructed_rows(case: dict) -> dict[str, dict]:
    """Three constructed transcripts of one long-scene case: a refusal, an out-of-character fact sheet and an
    in-character scene, each with a verdict that passes every fact (the branch, not the judge, must decide)."""
    turns = [t.get("content") if isinstance(t, dict) else str(t) for t in case.get("turns") or []]
    els = [e for e in case.get("judge_elements") or [] if e.get("status") != "retire"]

    def row(replies, quote):
        conv = [{"role": "system", "content": case.get("system") or ""}]
        for u, a in zip(turns, replies):
            conv += [{"role": "user", "content": u}, {"role": "assistant", "content": a}]
        res = [{"id": e["id"], "type": "judge", "pass": True, "judge_pass": True, "quote": quote,
                "detail": "judge: yes", "credit": 1.0} for e in els]
        return {"case_id": case["id"], "rubric": "rp_deep", "profile": "bench45", "conversation": conv,
                "judge": {"judge_failed": False, "refused": False, "scores": None,
                          "elements": {"results": res, "unjudged": []},
                          "groups": [{"turns": list(range(1, len(turns) + 1)), "elements": [e["id"] for e in els],
                                      "refused": False, "ooc_quote": None, "in_character_votes": [True]}]}}
    n = len(turns)
    scene = '"Sit down," she says, setting two cups on the table. "We have the whole evening."'
    return {"refusal": row(["I'm sorry, but I can't help with that request."] * n, ""),
            "out_of_character": row(["- fact one\n- fact two\n- fact three"] * n, "fact one"),
            "in_character": row([scene] * n, "Sit down")}


def d1_f(case: dict) -> list[str]:
    """(f) each constructed transcript takes its branch: refusal -> refused by the code, 0; out of character ->
    every judged fact failed as not in character; in character -> the judged facts keep their credit."""
    from . import rpdeep
    rows = constructed_rows(case)
    bad = []
    r = rpdeep.score_row(rows["refusal"], case)
    if not (r.get("refused") and r.get("rate") in (0, 0.0)):
        bad.append(f"refusal scored {r.get('rate')} (refused={r.get('refused')})")
    o = rpdeep.score_row(rows["out_of_character"], case)
    js = [i for i in o.get("items") or [] if i.get("grader") == "judge"]
    if not js or any(i.get("pass") for i in js):
        bad.append("out-of-character fact sheet kept judged credit")
    c = rpdeep.score_row(rows["in_character"], case)
    jc = [i for i in c.get("items") or [] if i.get("grader") == "judge" and i.get("scored", True)]
    if not jc or not all(i.get("pass") for i in jc):
        bad.append("in-character scene lost judged credit")
    return [f"(f) constructed transcripts: {'; '.join(bad)}"] if bad else []


# ------------------------------------------------------------------ driver
def check(agreement: dict, marks: dict, rows_by_model: dict | None = None, cases: dict | None = None,
          scores: dict | None = None, margins: dict | None = None, parts: list[str] | None = None) -> dict:
    """{part: {pass, reason, fails, judge_sd, judge_sd_pooled, validates}} for every judged part with marks.
    ``agreement`` = an agree summary file's body; ``scores`` = {part: {model: production score}} for D1 (d)
    (default: the agreement table's judge means)."""
    out = {}
    table = agreement.get("parts") or {}
    want = parts or sorted(set(table) | {p for p in (marks.get("parts") or {}) if "*" not in p})
    for part in want:
        mk = marks_for(marks, part)
        if mk is None:
            continue
        s = table.get(part)
        if s is None:
            out[part] = {"pass": False, "reason": "judge agreement not measured", "fails": ["not measured"]}
            continue
        fails = check_agreement(part, s, mk, margins, marks.get("primary") or {})
        d1 = mk.get("d1") or {}
        if d1:
            rb = rows_by_model or {}
            if not rb and any(d1.get(k) for k in ("a_judged_stamped", "b_no_unexplained_ooc_zero", "e_all_cases")):
                # (a), (b) and (e) read the run folders: none given = nothing checked, never a pass
                fails.append("D1 (a, b, e): no run folders given (--folders), nothing checked")
            if d1.get("a_judged_stamped"):
                fails += d1_a(rb)
            if d1.get("b_no_unexplained_ooc_zero"):
                fails += d1_b(rb, cases or {})
            if d1.get("d_known_order"):
                sc = (scores or {}).get(part) or {m: v[0] for m, v in (s.get("model_means") or {}).items()}
                fails += d1_d(sc, d1["d_known_order"])
            if d1.get("e_all_cases"):
                fails += d1_e(rb)
            if d1.get("f_constructed"):
                c = next(iter((cases or {}).values()), None)
                fails += d1_f(c) if c else ["(f) no long-scene case loadable"]
        jt = judge_term_share(s)
        out[part] = {"pass": not fails, "reason": "all marks pass" if not fails else fails[0], "fails": fails,
                     "informational": informational(s, mk),
                     "judge_term_share": jt,
                     "judge_sd": s.get("judge_sd") or {}, "judge_sd_pooled": s.get("judge_sd_pooled"),
                     "validates": bool(mk.get("validates_pending_machine")) and not fails,
                     # lead 2026-11-19 (b): the board counts a model's judged part only when this run covered
                     # that model's own units with a fitting reader (agree.measured: another family than both
                     # the judge and the graded model)
                     "n_units_by_model": dict(s.get("n_units_by_model") or {}),
                     "reader": agreement.get("reader"), "statistic": agreement.get("statistic")}
    return out


def marks_sha256(path: Path | str | None = None) -> str:
    """The hash of a marks file (default: the frozen calibration/judge-marks-45.json)."""
    import hashlib
    return hashlib.sha256(Path(path or MARKS).read_bytes()).hexdigest()


def write_result(path: Path, res: dict, sources: list[str], covered: dict | None = None,
                 marks_path: Path | str | None = None) -> None:
    """``covered`` = {label: {"folders": [folder names], "run_ids": [bench run ids]}} of the run folders the check
    read; the board uses the result only for those models' rows (lead 2026-11-19 b). ``marks_sha256`` = the marks
    file checked against; the board compares it with the frozen file at render."""
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    body = {"marks": Path(marks_path or MARKS).name, "marks_sha256": marks_sha256(marks_path),
            "sources": [Path(x).name for x in sources], "covered": covered or {}, "parts": res}
    Path(path).write_text(json.dumps(body, indent=1), encoding="utf-8")
