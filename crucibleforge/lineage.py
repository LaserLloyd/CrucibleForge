"""The one family / lineage mechanism (FABLE-GATE-FIXD item 11, the missing
"lineage map").

Everything that asks "do these two models share a family?" goes through here:

* the judge self-judge rule (``judge_elements.family_of`` / ``same_family`` /
  ``family_excluded_ids``): a family never judges its own family's rows;
* the same-family-judge flag of the willingness x capability block
  (``report.same_family_judge`` -> ``report._row_family``);
* authorship exclusion (``report.authored_cases_for``): a model is excluded
  from a case whose prose its own family wrote.

A *family* is the base model family named in a model id (``gemma-4-31B`` ->
``google``). A *tune* (a community fine-tune whose id does not name its base:
``Dark-Scarlett-27B`` is a Qwen tune, ``Split-Untied-31B`` a Gemma tune) is
mapped to its base family by :data:`TUNES`: ADD NEW TUNES HERE, nowhere else.
Tunes also form *sibling groups* (:data:`SIBLINGS`: models trained from the
same merge, so they share prose styling beyond the base family).

Authorship keeps its narrower rule (nsfw-v2 R2, pinned by its tests): a case
excludes the model's own literal family and its sibling group, so a Gemma tune
is NOT excluded from a case a plain Gemma wrote, and a case written by a tune
excludes the tune's siblings, never every model of its base family.
:data:`AUTHORSHIP_RESOLVES_TUNES` is the one switch to widen it (the tune's
base family then also counts on the model side); the self-judge rule and the
same-family-judge flag always resolve tunes.
"""
from __future__ import annotations

import re

#: substring of a model id / label (lower case) -> family. The default table;
#: a profile may extend it with ``judge_policy.self_judge_families``.
FAMILIES = {
    "grok": "xai", "xai": "xai",
    "gemma": "google", "gemini": "google", "google": "google",
    "qwen": "qwen", "qwq": "qwen",
    "deepseek": "deepseek",
    "kimi": "moonshot", "moonshot": "moonshot",
    "claude": "anthropic", "anthropic": "anthropic",
    "minimax": "minimax",
    "gpt": "openai", "openai": "openai", "chatgpt": "openai",
    "llama": "llama", "nemotron": "nemotron", "mistral": "mistral",
    "longcat": "longcat", "glm": "glm",
}

#: family -> the short name reports and authorship ledgers use
DISPLAY = {"xai": "grok", "google": "gemma", "moonshot": "kimi",
           "anthropic": "claude", "openai": "gpt"}

#: tune id (regex on the lower-cased id / label) -> base family. The lineage map.
TUNES: list[tuple[str, str]] = [
    (r"dark-?scarlett-?27b", "qwen"),
    (r"split-?untied", "google"),
    (r"swish-?31b", "google"),
    (r"omega-?arsenal", "google"),
]

#: authorship exclusion: does the MODEL side resolve a tune to its base family?
AUTHORSHIP_RESOLVES_TUNES = False

#: tunes trained from one shared merge: prefix groups that exclude each other
SIBLINGS = {"scotoma-2": ["swish-31b", "omega-arsenal-31b", "split-untied-31b"]}


def _norm(name) -> str:
    return str(name or "").strip().lower().replace("_", "-")


def family(name, table: dict | None = None, tunes: bool = True) -> str | None:
    """Family of a model id or label. A tune (``tunes=True``) resolves to its
    base family first; otherwise the family of the LONGEST table key found in
    the name (``gemma-4-31B`` -> google). None when nothing matches."""
    n = _norm(name)
    if tunes:
        for rx, fam in TUNES:
            if re.search(rx, n):
                return fam
    best = None
    for key, fam in (table or FAMILIES).items():
        if key in n and (best is None or len(key) > len(best[0])):
            best = (key, fam)
    return best[1] if best else None


def display(fam: str | None) -> str | None:
    return DISPLAY.get(fam, fam) if fam else None


def same_family(a, b, table: dict | None = None) -> bool:
    fa = family(a, table)
    return bool(fa) and fa == family(b, table)
