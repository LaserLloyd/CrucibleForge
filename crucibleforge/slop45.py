"""Main NSFW "slop" craft metric (MakoBench 4.5, Admin N). REPORT-ONLY: 0 points, never read by any
score, tier or points computation. Method = EQ-Bench slop-score (sam-paech/slop-score, MIT): slop-list
words per 1k words, slop trigrams (stopword-stripped) per 1k, "not X, but Y" per 1k chars. The lists
are vendored per release under ``data/slop/<release>/`` (manifest with sha256) and never shown to a model.

credit = clamp((BAD - s) / (BAD - GOOD), 0, 1) over slop words per 1k, GOOD = 2.0, BAD = 15.0.
Anchors come from stored 4.1 NSFW replies: frontier models 1.9-4.4 per 1k, roleplay tunes 10-19.
The published human baseline (6.9 per 1k) is NOT the top anchor: these prompts produce less slop than
open fiction, so a human-fiction anchor would hand every model full marks.
Gate: under MIN_WORDS engaged words there is no credit (terse replies cannot game the metric)."""
from __future__ import annotations

import json
import pathlib
import re

RELEASE = "4.5.0"
DATA = pathlib.Path(__file__).parent / "data" / "slop" / RELEASE
GOOD, BAD = 2.0, 15.0
MIN_WORDS = 1500

STOP = set("i me my myself we our ours ourselves you your yours yourself yourselves he him his himself she her hers "
           "herself it its itself they them their theirs themselves what which who whom this that these those am is are "
           "was were be been being have has had having do does did doing a an the and but if or because as until while "
           "of at by for with about against between into through during before after above below to from up down in out "
           "on off over under again further then once here there when where why how all any both each few more most "
           "other some such no nor not only own same so than too very s t can will just don should now".split())
RE_NOT_BUT = re.compile(r"\b(?:(?:is\s+not|are\s+not|was\s+not|were\s+not|isn't|aren't|wasn't|weren't|ain't)|"
                        r"not(?!\s+(?:that|only)\b))\s+(?:(?!\bbut\b|[.?!]).){1,100}?[,;:]\s*but\s+"
                        r"(?!when\b|while\b|which\b|who\b|whom\b|whose\b|where\b|if\b|that\b|as\b|because\b|although\b|"
                        r"though\b|till\b|until\b|unless\b|here\b|there\b|then\b|my\b|we\b|I\b|you\b|it\s+seems\b|"
                        r"it\s+appears\b|it\s+felt\b|it\s+looks?\b|anything\b)", re.I)

_LISTS: tuple[set, set] | None = None


def _load(name: str) -> set[str]:
    out = set()
    for item in json.loads((DATA / name).read_text(encoding="utf-8")):
        if item:
            m = re.findall(r"[a-z]+(?:'[a-z]+)?(?:\s+[a-z]+(?:'[a-z]+)?)*", str(item[0]).lower())
            if m:
                out.add(m[0])
    return out


def lists() -> tuple[set, set]:
    global _LISTS
    if _LISTS is None:
        _LISTS = (_load("slop_list.json"), _load("slop_list_trigrams.json"))
    return _LISTS


def words(t: str) -> list[str]:
    return re.findall(r"[a-z]+(?:'[a-z]+)?", t.lower())


def credit(slop_words_1k: float) -> float:
    return max(0.0, min(1.0, (BAD - slop_words_1k) / (BAD - GOOD)))


def metrics(texts: list[str]) -> dict:
    sw_set, st_set = lists()
    allw = [w for t in texts for w in words(t)]
    n = len(allw)
    if not n:
        return {"words": 0, "slop_words_1k": None, "slop_tri_1k": None, "notbut_1k": None}
    sw = sum(1 for w in allw if w in sw_set)
    content = [w for w in allw if w not in STOP]
    st = sum(1 for i in range(len(content) - 2) if " ".join(content[i:i + 3]) in st_set)
    chars = sum(len(t) for t in texts) or 1
    nb = sum(len(RE_NOT_BUT.findall(t)) for t in texts)
    return {"words": n, "slop_words_1k": round(1000 * sw / n, 2), "slop_tri_1k": round(1000 * st / n, 3),
            "notbut_1k": round(1000 * nb / chars, 3)}


def _engaged(r: dict) -> bool:
    j = r.get("judge") or {}
    if j.get("refused") or j.get("empty_generation") or r.get("finish_reason") in ("content_filter", "error"):
        return False
    return bool((r.get("response") or "").strip())


def score_rows(rows: list[dict]) -> dict:
    """Pooled over a model's engaged Main NSFW replies. Report-only."""
    texts = [r["response"] for r in rows if _engaged(r)]
    m = metrics(texts)
    out = {**m, "credit": None, "gated": None, "release": RELEASE, "n_rows": len(texts)}
    if m["words"] < MIN_WORDS:
        out["gated"] = "too_little_text"
        return out
    out["credit"] = round(credit(m["slop_words_1k"]), 3)
    return out


def main_nsfw_rows(nsfw_rows: list[dict], prof: dict | None) -> list[dict]:
    """Rows of the profile's Main NSFW cases when ``main_nsfw.enabled``, else every nsfw row."""
    mn = (prof or {}).get("main_nsfw")
    if isinstance(mn, dict) and mn.get("enabled"):
        ids = set((mn.get("cases") or {}).values())
        return [r for r in nsfw_rows if r.get("case_id") in ids]
    return list(nsfw_rows)
