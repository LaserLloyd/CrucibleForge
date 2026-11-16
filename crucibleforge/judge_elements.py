"""4.5 judge ELEMENT-ONLY mode (PLAN-45-v4 section 5, engine change E3).

``judge_policy.mode: elements`` makes a row's judge cost short and bounded:
fact elements only, batched at most :data:`MAX_BATCH` per call, thinking off,
``max_tokens`` 400, one sample, JSON verdicts with a verbatim quote (the stored
quote recheck of ``judge.apply_elements`` stays on). No 0-10 craft dimension is
asked for, so none can reach a scored column. A call that cannot be parsed is
retried once; if it still fails the whole row is ``pending`` (``judge_failed``
+ ``pending``), never scored as 0. Every call logs its usage.

A model family never judges its own family's rows (:data:`FAMILIES`, by family,
not exact id): :func:`family_of`, :func:`same_family`.

Imports from ``judge`` are lazy (judge imports this module's helpers lazily too).
"""
from __future__ import annotations

import json
import logging
import re
import time

log = logging.getLogger(__name__)

MAX_BATCH = 8
DEFAULT_MAX_TOKENS = 400

from . import lineage as _lineage

#: the default family table; the one definition lives in :mod:`lineage`
#: (a profile may extend it with ``judge_policy.self_judge_families``).
FAMILIES = _lineage.FAMILIES


def family_of(name: str | None, table: dict | None = None) -> str | None:
    """The family of a model id or label (a tune resolves to its base family,
    see :mod:`lineage`): ``gemma-4-31B`` -> google, ``Dark-Scarlett-27B`` ->
    qwen, None when nothing matches."""
    return _lineage.family(name, table)


def same_family(judge_id: str, tested_ids, table: dict | None = None) -> bool:
    """Does the judge share a family with any model under test?"""
    jf = family_of(judge_id, table)
    return bool(jf) and any(family_of(t, table) == jf for t in tested_ids or ())


def family_table(pcfg: dict | None) -> dict:
    extra = (pcfg or {}).get("self_judge_families")
    return {**FAMILIES, **{str(k).lower(): str(v) for k, v in extra.items()}} \
        if isinstance(extra, dict) else dict(FAMILIES)


def family_excluded_ids(cfg: dict, benched_ids: set, pcfg: dict | None,
                        override_id: str | None = None) -> set[str]:
    """Judge candidate ids (and the forced override's id) that share a family
    with any model under test: with the exact-id exclusion they make the
    family-level self-judge rule."""
    table = family_table(pcfg)
    ids = [c.get("model_id") for c in (cfg.get("judge") or {}).get("candidates") or []]
    if override_id:
        ids.append(override_id)
    return {i for i in ids if i and same_family(i, benched_ids, table)}


def batch_size(pcfg: dict | None) -> int:
    n = int((pcfg or {}).get("elements_batch") or MAX_BATCH)
    return max(1, min(n, MAX_BATCH))


def batches(elements: list[dict], size: int = MAX_BATCH) -> list[list[dict]]:
    size = max(1, min(int(size or MAX_BATCH), MAX_BATCH))
    return [elements[i:i + size] for i in range(0, len(elements), size)]


def n_calls(n_elements: int, size: int = MAX_BATCH) -> int:
    return -(-n_elements // max(1, min(size, MAX_BATCH))) if n_elements else 0


def usage_record(result, jc, n_elements: int, seconds: float) -> dict:
    return {"judge_id": getattr(jc, "model_id", None), "elements": n_elements,
            "prompt_tokens": getattr(result, "prompt_tokens", None),
            "completion_tokens": getattr(result, "completion_tokens", None),
            "finish_reason": getattr(result, "finish_reason", None),
            "seconds": round(seconds, 2)}


def willingness_verdict(row: dict) -> dict:
    """A verdict for a row that has no elements but a willingness rung: decided
    programmatically (E5), no judge call."""
    from .willing import assess
    from .judge import willing_case
    a = assess(row, willing_case(row))
    return {"judge_failed": False, "judge_raw": "", "refused": a["refused"],
            "scores": {"note": "willingness decided programmatically"},
            "agreement": {"n_samples": 0, "dim_spread_mean": None, "flag_unanimity": None},
            "willingness": {"w": a["w"], "rung": a["rung"], "quote": ""},
            "willingness_flags": a["flags"], "mode": "elements", "usage": []}


_FENCE = re.compile(r"^\s*```[a-zA-Z]*\s*|\s*```\s*$")


def _elements_list_to_dict(data):
    """``"elements": [{"id", "pass", "quote"}, ...]`` -> the schema's dict keyed
    by id. None when an entry is not an object, lacks an id, or repeats one."""
    if not isinstance(data, dict):
        return None
    key = next((k for k in data if str(k).strip().lower() == "elements"), None)
    if key is None or not isinstance(data[key], list):
        return None
    out = {}
    for item in data[key]:
        if not isinstance(item, dict):
            return None
        eid = next((v for k, v in item.items() if str(k).strip().lower() == "id"), None)
        if not isinstance(eid, str) or not eid.strip() or eid.strip().lower() in out:
            return None
        out[eid.strip().lower()] = {k: v for k, v in item.items()
                                    if str(k).strip().lower() != "id"}
    return {**{k: v for k, v in data.items() if k != key}, "elements": out}


def parse_element_verdict(raw: str, rubric: str, batch: list[dict], **kw):
    """``judge.parse_verdict`` with two shape tolerances (WP-7b): ``elements``
    given as a LIST of ``{"id", "pass", "quote"}`` objects, and a verdict whose
    outer object lacks exactly its final ``}`` (WP-7: Qwen3.8 heretic, NC3 and
    WL2, finish_reason stop). Every other check of ``parse_verdict`` still
    applies (each element present, boolean pass), so a truncated or otherwise
    malformed object is still None. Returns the verdict; the repair applied, if
    any, is in ``verdict["_parse_repair"]`` (popped by the caller)."""
    from . import judge as J
    v = J.parse_verdict(raw, rubric, batch, **kw)
    if v is not None:
        return v
    text = _FENCE.sub("", (raw or "").strip()).strip()
    for repair, cand in (("", text), ("outer-brace", text + "}")):
        try:
            data = json.loads(cand)
        except ValueError:
            continue
        if not isinstance(data, dict):
            continue
        tag = repair
        listed = _elements_list_to_dict(data)
        if listed is not None:
            data, tag = listed, "+".join(x for x in (repair, "elements-list") if x)
        if not tag:
            continue
        v = J.parse_verdict(json.dumps(data), rubric, batch, **kw)
        if v is not None:
            v["_parse_repair"] = tag
            return v
    return None


def judge_elements_row(jc, row: dict, pol: dict) -> dict:
    """Judge ``row`` in element-only mode (see the module docstring)."""
    from . import judge as J
    if not J._has_content(row):
        return {"judge_failed": True, "empty_generation": True, "judge_raw": "",
                "refused": False, "scores": None, "mode": "elements", "usage": []}
    elements = [e for e in J.row_elements(row) if e.get("status") != "retire"]
    if not elements:
        if J.row_willingness(row):
            return willingness_verdict(row)
        return {"judge_failed": True, "judge_error": "no elements to judge", "judge_raw": "",
                "pending": True, "refused": False, "scores": None, "mode": "elements",
                "usage": []}
    cap = int(pol.get("max_tokens") or DEFAULT_MAX_TOKENS)
    size = int(pol.get("batch") or MAX_BATCH)
    text = J.assistant_text(row)
    usage: list[dict] = []
    merged: dict = {}
    first_raw = ""
    for batch in batches(elements, size):
        sub = {**row, "rubric": "elements_only", "judge_elements": batch}
        sub.pop("_judge_policy", None)
        rubric, prompt = J.build_judge_input(sub, J.judge_input_budget_chars(
            getattr(jc, "context_length", None), getattr(jc, "max_tokens", None)))
        schema = J.element_schema(J.RUBRICS[rubric]["schema"], batch)
        verdict = None
        raw = ""
        for attempt in (0, 1):
            msgs = [{"role": "system", "content": J.judge_system(row)},
                    {"role": "user", "content": prompt}]
            if attempt:
                msgs.append({"role": "user", "content": "Your previous reply was not valid JSON "
                             "for the schema. Respond again with ONLY the JSON object."})
            kw = {"seed": 42, "max_tokens": min(cap, int(getattr(jc, "max_tokens", None) or cap)),
                  "response_format": schema}
            if getattr(jc, "thinking", False):
                kw["extra_body"] = J.merge_extra_body(jc.extra_body, J._NO_THINK)
                kw.pop("response_format")
            t0 = time.monotonic()
            try:
                res = jc.chat(msgs, **kw)
            except (J.Aborted, J.WindowClosed):
                raise
            except Exception as e:  # noqa: BLE001 - a failed call is retried once, then pending
                log.warning("elements judge call failed (%s/%s): %s", row.get("model_label"),
                            row.get("case_id"), str(e)[:200])
                usage.append({"judge_id": getattr(jc, "model_id", None), "elements": len(batch),
                              "error": type(e).__name__, "seconds": round(time.monotonic() - t0, 2)})
                continue
            usage.append(usage_record(res, jc, len(batch), time.monotonic() - t0))
            raw = J.strip_thinking_tags(res.response_text or "").strip()
            first_raw = first_raw or raw
            verdict = parse_element_verdict(raw, rubric, batch)
            if verdict is not None:
                repair = verdict.pop("_parse_repair", None)
                if repair:
                    usage[-1]["parse_repair"] = repair
                break
        if verdict is None:
            return {"judge_failed": True, "pending": True, "judge_raw": (first_raw or raw)[:2000],
                    "judge_error": "unparsable or failed element verdict after one retry",
                    "refused": False, "scores": None, "mode": "elements", "usage": usage}
        merged.update(J._aggregate_elements([verdict], batch, text))
    scores = {"note": "element-only verdict"}
    refused = J.refusal_heuristic(row.get("response", "") or text)
    out = {"judge_failed": False, "judge_raw": first_raw[:2000], "refused": refused,
           "scores": scores,
           "agreement": {"n_samples": 1, "dim_spread_mean": None, "flag_unanimity": None},
           "elements": J.apply_elements(row, scores, elements, merged, "elements_only"),
           "mode": "elements", "samples_used": 1, "usage": usage}
    if J.row_willingness(row):
        from .willing import assess
        a = assess(row, J.willing_case(row))
        out["willingness"] = {"w": a["w"], "rung": a["rung"], "quote": ""}
        out["willingness_flags"] = a["flags"]
    return out
