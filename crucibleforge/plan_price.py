"""The plan price of one model (fix round 2026-11-18, P4 / D15): printed by ``all --plan`` before a hosted go so
a spend cap can be set from it. An ESTIMATE from committed data, never a measurement; no model is called.

Per planned row (a case x its repeats; a chain unit counts once per turn of its target, attempts beyond the
first are not foreseen):

* input tokens: the case prompt (and system) at 3 characters a token plus 64 (``spend.estimate_call_cost``'s
  rule), else ``INPUT_PER_TURN`` tokens per turn (generated and chain units carry no prompt in the plan);
* expected output tokens: the committed answer target (``budget/targets-45.json``, by case id or Core family,
  else the generator family's ``LEVELS`` answer tokens) x ``budget.THINKER_FACTOR`` for a model that thinks,
  capped at the row's budget; with no target, half the row's budget;
* ceiling output tokens: the full output budget the row is sent (``cli.plan_budget``).

Dollars = tokens x the models.yaml ``price`` (USD per million, input / output). ``expected`` is the number to
size a cap from (with headroom); ``ceiling`` is what every row running to its budget would cost.
"""
from __future__ import annotations

import re

#: assumed prompt tokens per turn of a row whose prompt is not in the plan (chat history grows; a round mean)
INPUT_PER_TURN = 2000


def _targets() -> dict:
    try:
        from . import budget as B
        from pathlib import Path
        return B.load_targets(Path(__file__).resolve().parent.parent / "budget" / "targets-45.json")
    except Exception:  # noqa: BLE001 - no targets: every row falls back to half its budget
        return {}


def budget_int(text: str) -> int:
    m = re.match(r"\s*(\d+)", str(text or ""))
    return int(m.group(1)) if m else 0


def row_tokens(case: dict, budget: int, thinks: bool, targets: dict) -> tuple[float, float, float]:
    """(input, expected output, ceiling output) tokens of ONE row of ``case``."""
    from . import budget as B
    tgt = B.target_entry(targets, str(case.get("id") or "")) if targets else None
    turns = int((tgt or {}).get("turns") or len(case.get("turns") or []) or 1)
    chars = len(str(case.get("prompt") or "")) + len(str(case.get("system") or ""))
    inp = (chars / 3.0 + 64) if chars else float(INPUT_PER_TURN * turns)
    ans = (tgt or {}).get("target_tokens") or B._generated_tokens(case)
    ceiling = float(budget * turns) if budget else 0.0
    if ans:
        exp = float(ans) * (B.THINKER_FACTOR if thinks else 1.0)
        exp = min(exp, ceiling) if ceiling else exp
    else:
        exp = ceiling / 2.0
    return inp, exp, ceiling


def estimate(rows: list[tuple[dict, int, int]], price: dict | None, thinks: bool) -> dict:
    """``rows`` = [(case, repeats, output budget)]. -> {"rows", "in_tok", "out_tok", "ceiling_tok",
    "expected_usd", "ceiling_usd"} (the two USD figures None when ``price`` is missing)."""
    targets = _targets()
    n = 0
    tin = tout = tcap = 0.0
    for case, reps, budget in rows:
        i, o, c = row_tokens(case, budget, thinks, targets)
        n += reps
        tin += i * reps
        tout += o * reps
        tcap += c * reps
    out = {"rows": n, "in_tok": tin, "out_tok": tout, "ceiling_tok": tcap,
           "expected_usd": None, "ceiling_usd": None}
    if price:
        pin, pout = float(price.get("input", 0) or 0), float(price.get("output", 0) or 0)
        out["expected_usd"] = (tin * pin + tout * pout) / 1_000_000
        out["ceiling_usd"] = (tin * pin + tcap * pout) / 1_000_000
    return out


def plan_line(est: dict, *, hosted: bool, priced: bool) -> str:
    if not hosted:
        return "  price: rig model, no bill"
    if not priced:
        return ("  price: NOT SET - this hosted model has no `price:` in models.yaml, so a real run is refused "
                "(add `price: {input: X, output: Y}` per million tokens)")
    return (f"  price (estimate, crucibleforge/plan_price.py): ~${est['expected_usd']:.2f} expected "
            f"({est['rows']} rows, {est['in_tok'] / 1e6:.2f}M in + {est['out_tok'] / 1e6:.2f}M out at the "
            f"models.yaml price), ${est['ceiling_usd']:.2f} if every row ran to its budget; set "
            f"--spend-cap-usd from these (packs and the judge are extra)")
