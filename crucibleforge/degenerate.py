"""Degenerate output (2026-11-14): a reply that is one repeated character or one short
n-gram ("════...", "and the, and the, ...", "10, 10, 10, ...") is not an answer the
model gave; it is a broken instance (a bad quant, a broken template, a degenerate load).

The test is deterministic and cheap: the TAIL of the text (the last :data:`WINDOW`
characters, where a loop takes over) is compressed with zlib level 9; a ratio below
:data:`RATIO_MAX` is degenerate. Text shorter than :data:`MIN_CHARS` is never judged
(short strings do not compress; a blank is the early stop's ``blank`` signal).

Calibration on stored rows (``d-work/real2``, 2026-11-14), the ratio of the last 4096
characters:

* the quarantined degenerate 8B rows (results-ds3v, results-calib-ds8b, results-ds3l):
  0.0079-0.0535 (every one of them; the "════" floods 0.010, the "and the," loop 0.011);
* every other stored text of 400+ characters, all models (2,753 response/reasoning
  texts): >= 0.184, except two real loops (the Qwen3.8 ref's reasoning on implied1_L3-004,
  0.0068, all "═"; a MiniMax session reply, 0.0112).

0.10 sits in the gap: 1.8x above the worst degenerate row and 1.8x below the lowest sane
text. Ordinary prose compresses to ~0.35-0.6, code and tables to ~0.2-0.3.

A ROW is degenerate when its effective answer (``api.row_answer``) is, or when its
content is empty and its reasoning channel is (the model looped while thinking and never
answered). A finished answer after a long think is judged on the answer, never on the think.
"""
from __future__ import annotations

import zlib

WINDOW = 4096
MIN_CHARS = 256
RATIO_MAX = 0.10


def tail_ratio(text: str) -> float | None:
    """zlib-9 compressed size / raw size of the last :data:`WINDOW` characters (UTF-8),
    or None for text shorter than :data:`MIN_CHARS`."""
    if not text or len(text) < MIN_CHARS:
        return None
    raw = text[-WINDOW:].encode("utf-8", "replace")
    return len(zlib.compress(raw, 9)) / len(raw)


def text_degenerate(text: str) -> dict | None:
    """``{"ratio", "chars"}`` when ``text`` is degenerate, else None."""
    r = tail_ratio(text)
    if r is None or r >= RATIO_MAX:
        return None
    return {"ratio": round(r, 4), "chars": len(text)}


def row_degenerate(row: dict) -> dict | None:
    """The row's degenerate verdict: ``{"channel": "answer"|"reasoning", "ratio", "chars"}``
    or None. Error / skipped rows are never judged."""
    if row.get("skipped") or row.get("error"):
        return None
    from .api import row_answer
    ans = row_answer(row) or ""
    hit = text_degenerate(ans)
    if hit:
        return {"channel": "answer", **hit}
    if not (row.get("response") or "").strip() and not row.get("tool_calls"):
        hit = text_degenerate(row.get("reasoning") or "")
        if hit:
            return {"channel": "reasoning", **hit}
    return None


#: the probe that must be a plausible short reply ("Hi"); every other perf probe gets the
#: same test (it is free: the rows are already there)
TINY_PROBE = "P1-tiny"


def probe_failure(rows: list[dict]) -> dict | None:
    """Run-start sanity on the perf probe rows: a probe whose reply hit its ceiling
    (finish=length), is degenerate, or carries no answer at all fails the run before any
    scored item is sent. Returns the record of the first failing probe (P1-tiny first)
    or None. Every stored sane probe (all models) finished ``stop`` with a short answer;
    every degenerate one hit the 16,384-token ceiling with "════...".

    Error rows are not judged here (transport errors have their own path)."""
    from .api import row_answer
    order = sorted((r for r in rows if not r.get("error") and not r.get("skipped")),
                   key=lambda r: (r.get("case_id") != TINY_PROBE, str(r.get("case_id"))))
    for r in order:
        cid = r.get("case_id")
        why = None
        deg = row_degenerate(r)
        if r.get("finish_reason") == "length":
            why = (f"probe {cid} hit its ceiling ({r.get('max_tokens_sent') or '?'} tokens, "
                   f"finish=length)")
        elif deg:
            why = f"probe {cid} reply is degenerate repetition"
        elif not (row_answer(r) or "").strip() and not r.get("tool_calls"):
            why = f"probe {cid} returned no answer"
        if why:
            if deg:
                why += f" (tail compresses to {deg['ratio']:.3f}, {deg['chars']} chars in the {deg['channel']})"
            return {"signal": "probe", "reason": why + ": the model instance is broken; "
                    "no scored item was sent", "case_id": cid,
                    "finish_reason": r.get("finish_reason"),
                    **({"degenerate": deg} if deg else {}),
                    "head": (r.get("response") or "")[:80]}
    return None


def is_degenerate(row: dict) -> bool:
    """The row's stamp, else the test on its text (rows stored before the stamp existed)."""
    return bool(row.get("degenerate")) or bool(row_degenerate(row))
