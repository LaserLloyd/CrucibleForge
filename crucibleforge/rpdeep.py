"""rpdeep (deep-lore roleplay, suite 3.9 candidate): scoring.

A row's score is the WEIGHTED share of its graded facts that passed. One
grader per fact: a deterministic check (``checks`` with a ``fact`` key) or a
judge element. ``weight`` defaults to 1; the four phases of one secret and
the two halves of a split inductive item carry 0.5 each, so one leak cannot
cost three whole points (review M2).

A fact counts only when it is delivered IN CHARACTER (review C2):
- the judge marks a fact group ``in_character: false`` (judge.judge_deep_row
  then fails its elements), and
- every graded turn also passes a deterministic format gate
  (:func:`ooc_reason`): no dialogue at all (3.9 step 6: quoted speech, an
  ``*action*`` reply with spoken words outside the asterisks, dash dialogue or
  ``Name:`` lines all count — EVAL-FABLE S4), a bulleted/numbered list, a
  heading, or "As <name>, ..." / "Facts:" / OOC framing fails EVERY fact of
  that turn, regex facts included. A companion-chat case (``format_gate:
  chat``) talks to the user directly, so it has no dialogue requirement.

A row is only scored when it is complete (review C1): no verdict, a failed
verdict or any unjudged group makes it ``unjudged`` / ``judge_failed`` /
``partial`` with ``rate: None`` — it is re-judged, never scored on a smaller
denominator, and a judge outage is never counted as a model failure. A row the
judge marks refused scores 0 on every item.
"""
from __future__ import annotations

import re

_LIST_LINE = re.compile(r"^\s*(?:[-*•]|\d{1,2}[.)])\s+\S", re.M)
_HEADING = re.compile(r"^\s*#{1,6}\s", re.M)
# "As <Name>," / "Facts:" / "Summary:" / "Note:" framing counts only at the TOP
# of the reply (a fact sheet starts with it); inside prose "As always, she ..."
# or "Note: ..." are idioms, not notes to the user (review 2026-10-26: the
# multiline, case-insensitive form zeroed a whole turn on "As usual,"). OOC
# markers count anywhere. The name must be capitalised and not an idiom word.
_FRAMING = re.compile(
    r"\A\s*(?:As (?!always\b|usual\b|ever\b|if\b|though\b|such\b|far\b|for\b|long\b|soon\b"
    r"|well\b|before\b|promised\b|expected\b|it\b|she\b|he\b|they\b|we\b|you\b)"
    r"[A-Z][\w.'-]*\s*[,:]|Facts?\s*:|Response summary|Summary\s*:|Notes?\s*:)")
_OOC_MARK = re.compile(r"\(\(\s*OOC|^\s*OOC\s*:|\[OOC", re.M | re.I)
# straight/curly double quotes, guillemets and low-9 quotes all count as
# spoken dialogue for the format gate (the rules ask for "double quotes", but a
# model answering in «…» or „…“ has played the scene, not written a fact sheet)
MAX_TURN_WORDS = 600
CRAFT_GATE = 3
# Items whose card text changed AFTER rows were generated: a row whose prompt
# still carries the old text is not graded on that item (the item would grade
# the new rule against a model that was shown the old one).
LEGACY_CARD_TEXT = {
    ("RD2-trieste", "A2-no-knife-friday"): "she will not sell a knife to a captain on a Friday",
}
_QUOTE = re.compile(r"[\"“”«»„]")
# other ways a scene carries spoken words (3.9 step 6, EVAL-FABLE S4: the old
# "no quoted speech -> 0" gate zeroed `*action*`-style replies that got the
# lore right): paired curly single quotes, an em/en-dash dialogue line, a
# "Name:" / "**Name:**" script line, or an *action* reply with at least three
# words outside the asterisks
_SINGLE_QUOTED = re.compile(r"‘[^’\n]{2,}’|(?:^|(?<=\s))'[^'\n]{3,}?'(?=[\s.,!?;:]|$)", re.M)
_DASH_LINE = re.compile(r"^[ \t]*[—–][ \t]*\S", re.M)
_SPEAKER_LINE = re.compile(r"^[ \t]*(?:\*\*)?[A-Z][\w'’-]{1,30}(?:\*\*)?[ \t]*:(?:\*\*)?[ \t]+\S", re.M)
_ACTION = re.compile(r"\*[^*\n]+\*")
FORMAT_GATES = ("rp", "chat")


def has_dialogue(text: str) -> bool:
    """Does the reply carry any spoken words at all (any common convention)?"""
    t = text or ""
    if _QUOTE.search(t) or _SINGLE_QUOTED.search(t) or _DASH_LINE.search(t) \
            or _SPEAKER_LINE.search(t):
        return True
    if _ACTION.search(t):
        rest = _ACTION.sub(" ", t)
        return len(re.findall(r"[A-Za-z][A-Za-z'’]*", rest)) >= 3
    return False


def ooc_reason(text: str, gate: str = "rp") -> str | None:
    """Why a graded reply is NOT played in character, or None. Deterministic
    and deliberately narrow: it catches fact sheets and notes to the user, not
    style. An empty reply is handled elsewhere (it fails as missing).
    ``gate="chat"`` (a companion talking to the user directly): no dialogue
    requirement — lists, headings and framing still fail."""
    t = text or ""
    if not t.strip():
        return None
    if len(_LIST_LINE.findall(t)) >= 2:
        return "bulleted/numbered list"
    if _HEADING.search(t):
        return "markdown heading"
    m = _FRAMING.search(t) or _OOC_MARK.search(t)
    if m:
        return f"out-of-character framing ({m.group(0).strip()[:20]!r})"
    if gate != "chat" and not has_dialogue(t):
        return "no dialogue at all"
    return None


SHORT_QUOTE_MIN = 4


def _stored_quote_ok(r: dict, replies: list[str], case: dict) -> dict:
    """The generic quote guard rejects quotes under 12 characters. In a graded
    rpdeep reply a short verbatim quote can be the whole evidence ("Ottavio.",
    the dog's name; MiniMax RD1 r1 t6 2026-10-26: 3/3 samples said YES and were
    failed for quoting the name alone). When the judge's majority said YES and
    its quote (>= SHORT_QUOTE_MIN chars, a dialogue-scope element excepted —
    that check already ran on the full text) is verbatim in a graded turn, it
    passes. Likewise a quote that the judge-time guard now verifies as
    sentence fragments of the graded turns."""
    if r.get("pass") or not r.get("judge_pass") or "spoken" in (r.get("detail") or "") \
            or "in character" in (r.get("detail") or ""):
        return r
    from .judge import _norm_quote, quote_found
    q = _norm_quote(r.get("quote") or "").strip(" \"'.,!?")
    e = next((e for e in case.get("judge_elements") or [] if e["id"] == r["id"]), {})
    if e.get("quote_scope") == "dialogue":
        return r
    raw = "\n".join(replies[t - 1] for t in e.get("turns") or [] if 0 < t <= len(replies))
    # several short sentences joined by the judge (suite 4.0 lite, RV1 K1): the
    # judge-time guard now accepts those; this applies it to stored verdicts
    if "quote not in the reply" in (r.get("detail") or "") \
            and quote_found(r.get("quote") or "", raw):
        return {**r, "pass": True, "detail": "judge: yes; quote verified (fragments)"}
    if len(q) < SHORT_QUOTE_MIN:
        return r
    text = _norm_quote(raw)
    if re.search(r"(?<!\w)" + re.escape(q) + r"(?!\w)", text):
        return {**r, "pass": True, "detail": "judge: yes; short quote verified verbatim"}
    return r


def _keyword_gates(r: dict, replies: list[str], case: dict) -> dict:
    """Scoring-time rules under a judge YES (re-scoring stored verdicts needs no
    new judge run):
    ``veto`` — a pattern in her SPOKEN words that by itself makes the item wrong
    ("the red one" for a listener who said he cannot tell red from green); it
    fails the item.
    ``diag_require_any`` — a keyword floor kept as a DIAGNOSTIC only (Fable
    final review 2026-10-26: the floors were post-hoc word lists that failed
    both ways — "a hundred and three" satisfied the coffee floor). A miss is
    recorded on the item (``floor_miss``), never scored."""
    e = next((e for e in case.get("judge_elements") or [] if e["id"] == r["id"]), {})
    if not (e.get("diag_require_any") or e.get("veto")) or not r.get("pass"):
        return r
    from .session_checks import dialogue
    spoken = dialogue("\n".join(replies[t - 1] for t in e.get("turns") or []
                                 if 0 < t <= len(replies)))
    if e.get("veto"):
        m = re.search(e["veto"], spoken, re.I)
        if m:
            return {**r, "pass": False, "detail": f"veto: {m.group(0)!r} in her spoken words"}
    if e.get("diag_require_any") and not re.search(e["diag_require_any"], spoken, re.I):
        return {**r, "floor_miss": True}
    return r


def _item_turns(spec: dict, n_turns: int) -> list[int]:
    return list(spec.get("turns") or range(1, n_turns + 1))


def score_row(row: dict, case: dict, turn_rows: list[dict] | None = None) -> dict:
    from . import session_checks
    turn_rows = turn_rows or []
    replies = [m.get("content") or "" for m in row.get("conversation") or []
               if m.get("role") == "assistant"]
    n_turns = len(case.get("turns") or replies)
    gate_mode = case.get("format_gate") or "rp"
    ooc = {i: ooc_reason(replies[i - 1], gate_mode) for i in range(1, len(replies) + 1)}
    # rule 6 asks for 150-350 words; a reply over MAX_TURN_WORDS takes more shots
    # at every fact (Fable 2026-10-26: Dark-Scarlett was over 350 on 70% of turns)
    words = {i: len((replies[i - 1] or "").split()) for i in range(1, len(replies) + 1)}
    for i, n in words.items():
        if not ooc.get(i) and n > MAX_TURN_WORDS:
            ooc[i] = f"over {MAX_TURN_WORDS} words ({n})"
    # craft as an in-character gate (only when the opt-in craft chunks were
    # judged): a chunk with voice or craft <= CRAFT_GATE marks its turns out of
    # character — an in-character info-dump ("She said: 'I was born ...'")
    # passes the format gate but not this one
    jv = row.get("judge") or {}
    for ch in jv.get("craft_chunks") or []:
        sc_ = ch.get("scores") or {}
        if any(isinstance(sc_.get(d), (int, float)) and sc_[d] <= CRAFT_GATE
               for d in ("voice", "craft")):
            lo, hi = ch["turns"]
            for i in range(lo, hi + 1):
                ooc.setdefault(i, None)
                ooc[i] = ooc[i] or f"craft gate (voice/craft <= {CRAFT_GATE})"
    chk = session_checks.run_checks(case, replies) or {"results": []}
    by_id = {r["id"]: r for r in chk["results"]}
    items = []

    def gate(item, spec):
        bad = [f"t{t}: {ooc[t]}" for t in _item_turns(spec, n_turns) if ooc.get(t)]
        if bad and (item["pass"] or item.get("credit")):
            item["pass"] = False
            item.pop("credit", None)
            item["detail"] = "not in character — " + "; ".join(bad)
        return item

    for c in case.get("checks") or []:
        if not c.get("fact"):
            continue
        r = by_id.get(c["id"])
        items.append(gate({"fact": c["fact"], "ftype": c["ftype"], "grader": "regex",
                           "id": c["id"], "weight": float(c.get("weight", 1)),
                           "scored": c.get("scored", True), "sanity": bool(c.get("sanity")),
                           "pass": bool(r and r["pass"]),
                           # a word_range near miss: failed, half credit (fix 3.10)
                           **({"credit": session_checks.check_credit(r)}
                              if r and not r["pass"] and session_checks.check_credit(r) else {}),
                           "detail": (r or {}).get("detail", "graded turn empty")}, c))
    j = row.get("judge") or {}
    el = {r["id"]: r for r in ((j.get("elements") or {}).get("results") or [])}
    el = {k: _stored_quote_ok(v, replies, case) for k, v in el.items()}
    el = {k: _keyword_gates(v, replies, case) for k, v in el.items()}
    unjudged = set((j.get("elements") or {}).get("unjudged") or [])
    refused = bool(j.get("refused"))
    system = next((m.get("content") or "" for m in row.get("conversation") or []
                   if m.get("role") == "system"), "")
    for e in case.get("judge_elements") or []:
        if e["id"] in unjudged:
            continue
        legacy = LEGACY_CARD_TEXT.get((case.get("id"), e["id"]))
        if legacy and legacy in system:
            e = {**e, "scored": False}
        r = el.get(e["id"])
        items.append(gate({"fact": e.get("fact") or e["id"].split("-")[0], "ftype": e["ftype"],
                           "grader": "judge", "id": e["id"], "weight": float(e.get("weight", 1)),
                           "scored": e.get("scored", True), "sanity": bool(e.get("sanity")),
                           "floor_miss": bool((r or {}).get("floor_miss")),
                           "pass": bool(r and r["pass"]),
                           "quote": (r or {}).get("quote", ""),
                           "detail": (r or {}).get("detail", "no verdict")}, e))
    if refused or j.get("empty_generation"):
        # a refused row scores 0 on EVERY item, regex included (an OOC refusal
        # must not collect the no-leak item for free); so does a session the
        # board counts as failed (errored / ran out: an empty-generation
        # verdict, report._counted_failed)
        for i in items:
            i["pass"] = False
            i.pop("credit", None)
    integrity = {
        "overflow_possible": [t.get("turn") for t in turn_rows
                              if t.get("context_overflow_possible") or t.get("context_overflow")],
        "overflow_actual": [t.get("turn") for t in turn_rows if t.get("context_overflow_actual")],
        "truncated": [t.get("turn") for t in turn_rows if t.get("finish_reason") == "length"],
        "empty_turns": [t.get("turn") for t in turn_rows if not (t.get("response") or "").strip()],
        "ooc_turns": {t: r for t, r in ooc.items() if r},
        "turns_over_350_words": sum(1 for n in words.values() if n > 350),
        "prompt_tokens_last": max([(t.get("metrics") or {}).get("prompt_tokens") or 0
                                   for t in turn_rows] or [0]),
        "craft_error": j.get("craft_error"),
        "judge_errors": j.get("judge_errors") or [],
        "groups_clamped": sum(1 for g in j.get("groups") or [] if g.get("clamped")),
        "craft_clamped": (sum(1 for m in j["craft_meta"] if m.get("clamped"))
                          if isinstance(j.get("craft_meta"), list) else
                          int(bool((j.get("craft_meta") or {}).get("clamped")))),
    }
    if not j or j.get("judge_failed") or unjudged:
        return {"status": "unjudged" if not j else
                ("judge_failed" if j.get("judge_failed") else "partial"),
                "items": items, "unjudged": sorted(unjudged), "rate": None, **integrity}
    # pruned facts (``scored: false`` — every recent model passed them on every
    # run, the maintainer 2026-10-26) still run and are reported, but carry no weight; a
    # ``sanity: true`` fact that FAILS flags the row as a suspect (broken) run
    scored = [i for i in items if i.get("scored", True)]
    w = sum(i["weight"] for i in scored)
    got = sum(i["weight"] * session_checks.check_credit(i) for i in scored)
    sanity_failed = [i["id"] for i in items if i.get("sanity") and not i["pass"]]
    return {"status": "ok", "items": items, "rate": got / w if w else None,
            "n": len(scored), "passed": sum(i["pass"] for i in scored),
            "sanity_failed": sanity_failed,
            "weight_total": w, "weight_passed": got,
            "unjudged": [], "refused": refused, "craft": j.get("scores"), **integrity}
