"""Assistant basics (checked): a column shown OUTSIDE Overall on the 4.5 standard board.

Fifteen stored, machine-checked items (instruction format, word problems, a long document), two repeats each, no judge.
It cannot rank good models (sound models all land at 0.93-1.00); it tells a working assistant from a damaged one.
That is why it is read from this module only: it is not a ``scores:`` entry, so no total, rank, sort or pack gate
can see it, and the ruled Coding and Chat weights are untouched.

The cell is the share correct with its margin and a band word. Every rule is the board's own: a cut reply is left out
and counted, "not measured" when more than a quarter of the runs (and at least two) are cut, a run lost to the
infrastructure (error, dropped stream, provider filter) is missing and the cell is "pending", never a zero. An empty
reply after thinking, a refusal in words, or a wrong answer scores 0 for that run.
"""
from __future__ import annotations

import math

CEILING_SHARE = 0.95
NOTE = ("Assistant basics (checked) = 15 everyday items a working assistant gets right (reply formats, word "
        "problems, finding a fact in a long document), each run twice, every answer checked by code and no judge. "
        "The cell shows the share right with its margin (± points of 100) and a word: pass = 85% or more, partial = "
        "40% to 85%, weak = under 40%. It is outside Overall and changes no rank. It does not rank good models "
        "against each other (sound models all score 93% to 100%); it tells you whether a model, such as a heavily "
        "tuned role-play one, is still a usable assistant. ‡ = every model on this board measured on it (at least "
        "two) scored 95% or more. pending = not validated yet or runs missing; not run = no answers yet; not "
        "measured = more than a quarter of its runs were cut at the token ceiling (cut runs are left out and "
        "counted).")
PENDING_VALIDATION = "pending validation"
RESULT_NAME = "assistant_marks.json"


def pending_note(results_dir=None) -> str:
    """The page note while ``assistant.validated`` is false: the reason, from the marks output
    (scripts/check_assistant_marks.py --out <results>/assistant_marks.json), e.g. "cut-share mark failed;
    damaged-model set not run". No score from the column enters any total."""
    import json
    from pathlib import Path
    from .config import results_dir as _rd
    p = Path(results_dir or _rd()) / RESULT_NAME
    try:
        res = json.loads(p.read_text(encoding="utf-8"))
        why = "; ".join(res.get("reasons") or []) or ("its marks hold; the profile is not yet marked validated"
                                                      if res.get("ok") else "its marks are not met")
    except (OSError, ValueError, AttributeError):
        why = "the validation marks have not been run"
    return f"Assistant basics (checked) is pending validation: {why}. Its figures are not counted in any total."


def block(prof: dict | None) -> dict | None:
    b = (prof or {}).get("assistant")
    return b if isinstance(b, dict) and b.get("cases") else None


def case_ids(prof: dict | None) -> list[str]:
    b = block(prof)
    return list(b["cases"]) if b else []


def validated(prof: dict | None) -> bool:
    """True only when the profile says the validation marks passed (scripts/check_assistant_marks.py)."""
    v = (block(prof) or {}).get("validated")
    return v is True or str(v).strip().lower() in ("true", "yes", "validated")


def band(share: float, bands: dict) -> str:
    if share >= float(bands["pass"]) - 1e-9:
        return "pass"
    if share >= float(bands["partial"]) - 1e-9:
        return "partial"
    return "weak"


def margin(item_means: list[float], z: float = 1.645) -> float | None:
    """Half of the 90% interval of the share, over the items (a case-sampling interval): the larger of the items'
    own spread and a binomial spread with the share kept off 0 and 1 (15 items cannot show certainty at the
    ceiling). The repeats are inside each item's mean."""
    n = len(item_means)
    if n < 2:
        return None
    p = sum(item_means) / n
    sd = math.sqrt(sum((m - p) ** 2 for m in item_means) / (n - 1))
    q = min(max(p, 0.5 / n), 1 - 0.5 / n)
    return z * max(sd / math.sqrt(n), math.sqrt(q * (1 - q) / n))


def _run_share(r: dict) -> float | None:
    """One run's credit: the weighted share of its checks (instruction and reasoning items, the board's partial
    credit), else its grade. None = not a graded run."""
    if r.get("grade") not in ("pass", "fail"):
        return None
    if r.get("category") == "longctx":
        return 1.0 if r["grade"] == "pass" else 0.0
    from .report import _element_rate
    return _element_rate([r], weighted=True)


def compute(prof: dict | None, rows: list[dict], missing_jobs=()) -> dict | None:
    """The column's data for one model from its tier-1 rows (after the board's missing-job split) and the missing
    jobs. None when the profile has no such block."""
    b = block(prof)
    if b is None:
        return None
    from .report import case_part_rows
    ids = set(b["cases"])
    reps = int(b.get("repeats") or 2)
    expected = len(ids) * reps
    mine = [r for r in rows if r.get("case_id") in ids and not r.get("chain")]
    miss = [m for m in missing_jobs or () if m.get("case_id") in ids]
    out = {"status": "not_run", "share": None, "margin": None, "band": None, "n_items": 0, "n_runs": 0,
           "expected_runs": expected, "cut": 0, "empty": 0, "missing": 0, "validated": validated(prof),
           "label": b.get("label"), "items": {}}
    if not mine and not miss:
        return out
    kept, rec = case_part_rows(mine, "exclude")
    calls = int((rec or {}).get("calls") or 0)
    out["cut"] = int((rec or {}).get("ran_out") or 0)
    out["empty"] = int((rec or {}).get("empty") or 0)
    out["missing"] = len(miss) + max(0, expected - calls - len(miss))
    if out["missing"]:
        out["status"] = "pending"
        return out
    if rec and rec.get("not_measured"):
        out["status"] = "not_measured"
        return out
    per: dict[str, list[float]] = {}
    for r in kept:
        v = _run_share(r)
        if v is not None:
            per.setdefault(r["case_id"], []).append(v)
    if not per:
        out["status"] = "not_measured"
        return out
    means = [sum(v) / len(v) for v in per.values()]
    share = sum(means) / len(means)
    out["items"] = {k: sum(v) / len(v) for k, v in per.items()}
    out.update(status="ok", share=share, margin=margin(means, float(b.get("z") or 1.645)),
               band=band(share, b["bands"]), n_items=len(means), n_runs=sum(len(v) for v in per.values()))
    return out


def ceiling(ranked: list[str], stats: dict) -> bool:
    """The board's ceiling rule: at least two published rows (staged validation rows and old-suite rows do not
    count) are measured and every one scored at least CEILING_SHARE."""
    shares = []
    for l in ranked:
        s = stats.get(l) or {}
        if s.get("validation_cc") or (s.get("era") or {}).get("old"):
            continue
        a = s.get("assistant45") or {}
        if a.get("status") == "ok" and a.get("share") is not None:
            shares.append(float(a["share"]))
    return len(shares) >= 2 and min(shares) >= CEILING_SHARE - 1e-9


def cell(a: dict | None, validated: bool, ceiling: bool) -> str:
    """The board cell: the word and the share with its margin; "pending" until validated or with runs missing."""
    if a is None or a.get("status") == "not_run":
        return "not run"
    if not validated:
        return PENDING_VALIDATION
    st = a.get("status")
    if st == "not_measured":
        return "not measured"
    if st == "pending":
        n = int(a.get("missing") or 0)
        return "pending" + (f" ({n} of {int(a.get('expected_runs') or 0)} runs missing)" if n else "")
    m = a.get("margin")
    txt = f"{a['band']} {100 * a['share']:.0f}%" + (f" ± {100 * m:.0f}" if m is not None else "")
    if a.get("cut"):
        txt += f" ({int(a['cut'])} cut)"
    return txt + (" ‡" if ceiling else "")


def compute_stored(prof: dict | None, rows: list[dict]) -> dict | None:
    """compute() over stored rows exactly as the board prepares them: the missing-job split first."""
    from .scoring45 import split_missing
    kept, missing = split_missing({1: [r for r in rows if r.get("case_id") in set(case_ids(prof))]})
    return compute(prof, kept[1], missing)
