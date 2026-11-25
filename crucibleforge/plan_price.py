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


#: Token profiles MEASURED on 4.5 runs, per model for ONE standard run (Code, Chat and NSFW+ rows; pack rows left
#: out), millions of tokens (input, output), from the rows' own token counts. Re-measured 2026-11-25 on the 12 hosted
#: production runs and the Docker user-path run: the 2026-11-24 table (typical 2.4 / 0.45, long thinker 3.3 / 1.6)
#: was low on INPUT for every model (the Docker run of GPT-6 Luna planned $0.47 and booked $0.74 = 4.39M in / 0.58M
#: out at list; its 4.5.0 run had 4.11 / 0.60, so the 4.5.1 budget change is not the cause) and about three times
#: low on input for thinkers (thinking is replayed into the chat history). "typical" = the mean of the six runs of
#: models with thinking off or light (Terra 3.72 / 0.43, Astra 1.75 / 0.18, Sol 2.03 / 0.24, Luna 4.11 / 0.60 and
#: 4.39 / 0.58, gpt-oss-120b 2.96 / 0.80); "long_thinker" = the median of the seven thinker runs (DeepSeek Flash
#: 10.93 / 2.00 and 10.53 / 1.86, GLM-5.3 Flash 7.77 / 2.68, Grok 4.7 4.72 / 5.40, Inkling 2 Mini 4.48 / 2.11, MiniMax
#: M3.1 Flash 15.32 / 3.35 and 12.31 / 2.35). A single model can sit about 40% above its group's figure (Luna: in
#: 4.4 against 3.2). ``packs_factor``: a full run WITH packs over the standard run (about x1.1 for light models, up
#: to x2 on output for thinkers; kept as the 2026-11-24 range). Every input token is priced at list; cached input
#: (64-80% on OpenAI models) usually bills lower.
MEASURED = {"date": "2026-11-25", "runs": 13, "ref_rows": 279,
            "typical": (3.2, 0.47), "long_thinker": (10.5, 2.4),
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
