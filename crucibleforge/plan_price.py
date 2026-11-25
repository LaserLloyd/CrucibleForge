"""The plan price of one model (fix round 2026-11-18, P4 / D15): printed by ``all --plan`` before a hosted go so
a spend cap can be set from it. An ESTIMATE from committed data, never a measurement; no model is called.

4.5.1 (2026-11-24): the expected figures come from token profiles MEASURED on 4.5 runs (:data:`MEASURED`), scaled
by the share of the standard run's rows planned: a typical figure and one for a model that thinks at length. The
old per-row output estimate (answer targets x a thinker factor, prompts alone as input) was the wrong shape (far
too much output, too little input) and 5 to 15 times too high in price. The ceiling is still per row: the full
output budget each row is sent (``cli.plan_budget``; a chain unit counts once per turn of its target).

Dollars = tokens x the models.yaml ``price`` (USD per million, input / output), every input token at list price.
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


#: Token profiles MEASURED on 4.5 runs (2026-11-24, measured on 5 runs: GPT-6.1 Terra, GPT-6 Luna, gpt-oss-120b,
#: GLM-5.3 Flash and the light thinkers of batch 1), per model for ONE standard run of ``ref_rows`` rows (the
#: 4.5 T1 set with NSFW+). Millions of tokens, (input, output). The standard run is input-heavy (chat history is
#: sent again every turn): Terra 2.25 / 0.34, light thinkers 2.3-2.8 / 0.3-0.8. "long_thinker" is a model that
#: thinks at length, set so that long_thinker x the high pack factor is not under the measured full run of the
#: heaviest thinker (GLM-5.3 Flash: 7.99 / 3.76 WITH many packs; tests/test_any_judge_451.py checks it at that
#: model's price and at a dearer one). ``packs_factor``: a full run WITH packs over the standard run, the range of
#: the measured models (Luna 4.34 / 0.72, gpt-oss-120b 2.82 / 0.79; from about x1.2 to x3 per model). 64-80% of the
#: input was served from the provider's prompt cache on OpenAI models, billed at about a tenth of list: the figures
#: below price every input token at list, so a real bill is usually lower.
MEASURED = {"date": "2026-11-24", "runs": 5, "ref_rows": 279,
            "typical": (2.4, 0.45), "long_thinker": (3.3, 1.6),
            "packs_factor": (1.6, 2.5)}


def estimate(rows: list[tuple[dict, int, int]], price: dict | None, thinks: bool | None) -> dict:
    """``rows`` = [(case, repeats, output budget)]. -> {"rows", "in_tok", "out_tok", "long_in_tok", "long_out_tok",
    "ceiling_tok", "expected_usd", "long_usd", "ceiling_usd", "thinks"} (USD figures None when ``price`` is missing).

    ``expected`` and ``long`` = the MEASURED per-run profiles (:data:`MEASURED`) scaled by the share of the
    standard run's rows planned. ``thinks``: True = the model entry marks a thinking model, so the EXPECTED figure is
    the long-thinker profile (review F5: a thinker is never estimated low); False = a model with thinking off (the
    typical profile); None = not known before the run (typical, and the advice points at the long figure).
    ``ceiling`` = every row running to its full output budget (``row_tokens``) with the long-thinker input."""
    targets = _targets()
    n = 0
    tcap = 0.0
    for case, reps, budget in rows:
        _i, _o, c = row_tokens(case, budget, thinks is not False, targets)
        n += reps
        tcap += c * reps
    share = n / float(MEASURED["ref_rows"]) if MEASURED["ref_rows"] else 0.0
    lin, lout = (x * 1e6 * share for x in MEASURED["long_thinker"])
    tin, tout = (lin, lout) if thinks is True else (x * 1e6 * share for x in MEASURED["typical"])
    out = {"rows": n, "in_tok": tin, "out_tok": tout, "long_in_tok": lin, "long_out_tok": lout,
           "ceiling_tok": tcap, "expected_usd": None, "long_usd": None, "ceiling_usd": None, "thinks": thinks}
    if price:
        pin, pout = float(price.get("input", 0) or 0), float(price.get("output", 0) or 0)
        out["expected_usd"] = (tin * pin + tout * pout) / 1_000_000
        out["long_usd"] = (lin * pin + lout * pout) / 1_000_000
        out["ceiling_usd"] = max((lin * pin + tcap * pout) / 1_000_000, out["long_usd"])
    return out


def plan_line(est: dict, *, hosted: bool, priced: bool) -> str:
    """The plan price line. Machine-readable shape kept (tools parse it): ``~$X expected``, then
    ``$H for a model that thinks at length``, then ``$Y if every row ran to its budget`` (the last number)."""
    if not hosted:
        return "  price: rig model, no bill"
    if not priced:
        return ("  price: NOT SET - this hosted model has no `price:` in models.yaml, so a real run is refused "
                "(add `price: {input: X, output: Y}` per million tokens)")
    lo, hi = MEASURED["packs_factor"]
    return (f"  price (estimate, crucibleforge/plan_price.py, token profiles measured on {MEASURED['runs']} runs "
            f"{MEASURED['date']}): ~${est['expected_usd']:.2f} expected "
            f"({est['rows']} rows, {est['in_tok'] / 1e6:.2f}M in + {est['out_tok'] / 1e6:.2f}M out at the "
            f"models.yaml price), ${est['long_usd']:.2f} for a model that thinks at length, "
            f"${est['ceiling_usd']:.2f} if every row ran to its budget; packs are extra (about x{lo:g} to x{hi:g} "
            "the standard run), cached input usually bills lower; the judge is billed apart. Set --spend-cap-usd "
            + ("to about twice the expected figure (the model entry marks thinking on or off)"
               if est.get("thinks") is not None else "to about twice the 'thinks at length' figure"))
