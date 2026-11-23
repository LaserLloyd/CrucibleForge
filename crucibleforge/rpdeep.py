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

from .api import row_answer

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


def sections_declared(case: dict | None) -> bool:
    """4.5 (E4): a case whose format asks for headed sections (``format.sections``
    declared, or a check that names a section) must not be zeroed for them."""
    c = case or {}
    if (c.get("format") or {}).get("sections"):
        return True
    return any(isinstance(k, dict) and (k.get("section") or k.get("sections"))
               for k in c.get("checks") or [])


def ooc_reason(text: str, gate: str = "rp", sections: bool = False) -> str | None:
    """Why a graded reply is NOT played in character, or None. Deterministic
    and deliberately narrow: it catches fact sheets and notes to the user, not
    style. An empty reply is handled elsewhere (it fails as missing).
    ``gate="chat"`` (a companion talking to the user directly): no dialogue
    requirement — lists, headings and framing still fail. ``sections=True``
    (the case declares headed sections, see :func:`sections_declared`): markdown
    headings are part of the requested format and do not fail the gate."""
    t = text or ""
    if not t.strip():
        return None
    if len(_LIST_LINE.findall(t)) >= 2:
        return "bulleted/numbered list"
    if not sections and _HEADING.search(t):
        return "markdown heading"
    m = _FRAMING.search(t) or _OOC_MARK.search(t)
    if m:
        return f"out-of-character framing ({m.group(0).strip()[:20]!r})"
    if gate != "chat" and not has_dialogue(t):
        return "no dialogue at all"
    return None


SHORT_QUOTE_MIN = 4


#: ``*`` and ``_`` mapped to look-alikes the current quote normaliser keeps, so
#: judge._quote_found_one verifies exactly as the 4.1 verifier did (it did not
#: drop markdown emphasis; ``_`` stays a word character)
_LITERAL_41 = str.maketrans({"*": "\u2217", "_": "\ua7ff"})


def _stored_quote_ok(r: dict, replies: list[str], case: dict, recheck: bool = False) -> dict:
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
    from .judge import _norm_quote, _quote_found_one, quote_found, set_pass
    quote = r.get("quote") or ""
    if not recheck:
        # the 4.1 board verifier: markdown emphasis is ordinary text and a " / "
        # splice is one quote (the ops-segment verifier changes are bench45-only)
        quote_found = _quote_found_one        # noqa: F811
        quote = str(quote).translate(_LITERAL_41)
    q = _norm_quote(quote).strip(" \"'.,!?")
    e = next((e for e in case.get("judge_elements") or [] if e["id"] == r["id"]), {})
    if e.get("quote_scope") == "dialogue":
        return r
    raw = "\n".join(replies[t - 1] for t in e.get("turns") or [] if 0 < t <= len(replies))
    if not recheck:
        raw = raw.translate(_LITERAL_41)
    # several short sentences joined by the judge (suite 4.0 lite, RV1 K1): the
    # judge-time guard now accepts those; this applies it to stored verdicts
    # (the single-sample wording "its quote is not in the reply" counts too: one
    # sample, so the stored quote alone is the whole majority) -- only under the
    # profile's ``quote_recheck`` switch (bench45); the 4.1 bench board keeps the
    # 4.0-lite rule (lead decision 2026-11-09, review F1: it moved 6 models)
    d = r.get("detail") or ""
    if ("quote not in the reply" in d or (recheck and "quote is not in the reply" in d)) \
            and quote_found(quote, raw):
        return {**set_pass(r, e, True, restore=True), "detail": "judge: yes; quote verified (fragments)"}
    if _short_verbatim(quote, raw):
        return {**set_pass(r, e, True, restore=True), "detail": "judge: yes; short quote verified verbatim"}
    return r


def _short_verbatim(quote: str, raw: str) -> bool:
    """The stored-verdict short-quote rule: the quote (>= SHORT_QUOTE_MIN chars once trimmed) is in ``raw`` as whole
    words (``_stored_quote_ok``; also the own-turn check, ``quote_in_own_turns``)."""
    from .judge import _norm_quote
    q = _norm_quote(quote).strip(" \"'.,!?")
    if len(q) < SHORT_QUOTE_MIN:
        return False
    return bool(re.search(r"(?<!\w)" + re.escape(q) + r"(?!\w)", _norm_quote(raw)))


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
    from .judge import set_pass
    from .session_checks import dialogue
    spoken = dialogue("\n".join(replies[t - 1] for t in e.get("turns") or []
                                 if 0 < t <= len(replies)))
    if e.get("veto"):
        m = re.search(e["veto"], spoken, re.I)
        if m:
            return {**set_pass(r, e, False), "detail": f"veto: {m.group(0)!r} in her spoken words"}
    if e.get("diag_require_any") and not re.search(e["diag_require_any"], spoken, re.I):
        return {**r, "floor_miss": True}
    return r


OOC_PREFIX = "graded reply not played in character / refused; "


def _group_of(j: dict, eid: str) -> dict | None:
    for g in j.get("groups") or []:
        if eid in (g.get("elements") or []):
            return g
    return None


def ooc_flag_counts(group: dict, replies: list[str], gate: str = "rp",
                    sections: bool = False) -> str | None:
    """Why a judge's out-of-character flag on one fact group COUNTS, or None when it must be ignored.

    DECISIONS-45 D1 (b), applied 2026-11-19 (lead, after the second-reader tool check: the production judge's
    lone in-character vote zeroed four correct, quote-verified CC3 facts): a group is zeroed as out of
    character only when the CODE records a reason on its graded turns:
    - a turn fails the deterministic format gate (:func:`ooc_reason`: list, heading, framing, no dialogue), or
    - the turns carry a refusal pattern (``graders.refusal_heuristic``).
    The judge's own evidence (its ``refused`` mark, an ``ooc_quote``, any number of not-in-character votes) is
    recorded on the row but never zeroes facts alone: it is counted as a judge/code disagreement
    (:func:`ooc_disagreement`). Before 2026-11-19 a verified ooc_quote in two samples or a judge ``refused`` mark
    counted too, and a verdict without the judge-side keys kept its zeroing."""
    from .graders import refusal_heuristic
    turns = [t for t in group.get("turns") or [] if 0 < t <= len(replies)]
    text = "\n\n".join(replies[t - 1] for t in turns)
    for t in turns:
        r = ooc_reason(replies[t - 1], gate, **({"sections": True} if sections else {}))
        if r:
            return f"format gate on t{t}: {r}"
    if text and refusal_heuristic(text):
        return "refusal pattern"
    return None


def ooc_disagreement(group: dict) -> str | None:
    """What the judge said against a group the code calls in character (recorded, never scored)."""
    bits = []
    if group.get("out_of_character"):
        bits.append("judge flagged out of character")
    if group.get("refused"):
        bits.append("judge marked refused")
    votes = group.get("in_character_votes") or []
    if any(v is False for v in votes):
        bits.append(f"{sum(1 for v in votes if v is False)}/{len(votes)} not-in-character votes")
    if (group.get("ooc_quote") or "").strip():
        bits.append("ooc_quote given")
    return "; ".join(bits) or None


#: group_meta keys the 4.5 judge stores per fact group (judge_deep_row). The
#: rescue needs both: without them the judge's own refusal / vote evidence is
#: unknown, so an old verdict keeps the 4.1 zeroing (review F1/F2)
JUDGE_SIDE_KEYS = ("refused", "in_character_votes")


def has_judge_side(group: dict) -> bool:
    return all(k in group for k in JUDGE_SIDE_KEYS)


def ooc_votes_gate(row: dict, case: dict) -> bool:
    """True when the OOC rescue applies: the case says ``ooc_gate: votes``, or
    (no case key) the profile the row was run under does (bench45). The 4.1
    ``bench`` profile has no such key: its stored verdicts score as in 4.1."""
    if "ooc_gate" in (case or {}):
        v = case["ooc_gate"]
    else:
        from .profiles import row_switch
        v = row_switch(row, "ooc_gate")
    return str(v or "").strip().lower() == "votes"


def rescue_ooc_zeroing(el: dict, replies: list[str], j: dict, gate: str = "rp",
                       sections: bool = False) -> dict:
    """Undo the judge-OOC zeroing of items the judge itself passed (verified
    quote) when the flag does not count (:func:`ooc_flag_counts`: the code records no reason). Works on
    stored verdicts, so no re-judge is needed, and on verdicts with or without the judge-side keys (D1 b,
    2026-11-19). The item keeps the judge's own detail plus a note and an ``ooc_disagreement`` field; a NO
    stays a NO. Only called under :func:`ooc_votes_gate`."""
    out = {}
    for k, r in el.items():
        d = r.get("detail") or ""
        if r.get("pass") or not d.startswith(OOC_PREFIX):
            out[k] = r
            continue
        g = _group_of(j, k)
        if g is None or ooc_flag_counts(g, replies, gate, sections):
            out[k] = r
            continue
        rest = d[len(OOC_PREFIX):]
        why = ooc_disagreement(g) or "judge flagged out of character"
        mark = {"ooc_disagreement": why}
        if rest.startswith("judge: yes") or (
                r.get("judge_pass") and rest.startswith("judge said yes")):
            ok = rest.startswith("judge: yes")
            out[k] = {**r, **mark, "pass": ok,
                      "detail": rest + f"; out-of-character flag ignored (judge/code disagreement: {why}; "
                                       "format gate and refusal pattern clear)"}
        else:
            out[k] = {**r, **mark, "detail": rest + "; out-of-character flag ignored"}
    return out


def _item_turns(spec: dict, n_turns: int) -> list[int]:
    return list(spec.get("turns") or range(1, n_turns + 1))


def is_programmatic(case: dict) -> bool:
    """A multi-turn case graded by its checks alone (generated ISTACK / SESSION-STATE
    items): ``programmatic: true``, or a generated case that declares no judge
    elements. It never goes to the judge and is never ``unjudged``."""
    if case.get("programmatic"):
        return True
    return bool(case.get("generator")) and "judge_elements" in case and not case["judge_elements"] \
        and not case.get("rubric")


def apply_overrides(case: dict, ov: dict | None) -> dict:
    """The 4.5 profile's own grading of a shared (4.1) long-scene case (``rpdeep_overrides``, scrub audit 2026-11-15
    items 10, 11, RD4 H2): ``{element or check id: {key: value, ..., "pattern_sub": [old, new]}}`` laid over the
    case's checks and judge elements. Only a row whose profile declares it reads it (bench45); the 4.1 ``bench``
    profile has no such key, so its board reads the case file unchanged."""
    if not ov:
        return case

    def lay(x):
        o = ov.get(x.get("id"))
        if not o:
            return x
        x = {**x, **{k: v for k, v in o.items() if k != "pattern_sub"}}
        if o.get("pattern_sub"):
            old, new = o["pattern_sub"]
            if old not in (x.get("pattern") or ""):
                raise ValueError(f"rpdeep_overrides: {case.get('id')} {x['id']}: pattern_sub text not in the pattern")
            x["pattern"] = x["pattern"].replace(old, new)
        return x
    return {**case, "checks": [lay(c) for c in case.get("checks") or []],
            "judge_elements": [lay(e) for e in case.get("judge_elements") or []]}


def quote_in_own_turns(r: dict, e: dict, replies: list[str]) -> dict:
    """Scrub audit 2026-11-15 item 12 (4.5 switch ``quote_own_turns``): a YES whose quote is not in the replies of
    the element's own turns (``quote_turns``, else ``turns``) proves nothing for that turn (MiniMax RD1 L2-tape:
    the quote is from turn 4, the element reads turn 1). A dialogue-scoped element, an element without turns or a
    verdict without a quote is left as it is. Same verifier as judge time (``quote_found``; a one-name answer,
    ``_short_answer``, may be a whole word; or the stored-quote short rule, ``_short_verbatim``), only the text is
    narrowed to those turns."""
    from .judge import _short_answer, quote_found
    turns = e.get("quote_turns") or e.get("turns")
    q = (r or {}).get("quote") or ""
    if not r or not r.get("pass") or not q or not turns or e.get("quote_scope") == "dialogue":
        return r
    own = "\n".join(replies[t - 1] for t in turns if 0 < int(t) <= len(replies))
    if quote_found(q, own, short_ok=_short_answer(e)):
        return r
    # scrub item 26 (2026-11-15): the short whole-word rule the board's stored-quote check applies on the same turns
    # (grok RV2 N2-kit: "No Warden." is a whole line of turn 2, after a "**Kit:**" speaker tag)
    if _short_verbatim(q, own):
        return r
    return {**r, "pass": False, "credit": 0.0 if r.get("credit") is not None else r.get("credit"),
            "detail": f"judge: yes, but its quote is not in turn(s) {list(turns)}"}


def refused_by_code(replies: list[str], case: dict, n_turns: int | None = None) -> bool:
    """True when ``graders.refusal_heuristic`` flags MORE THAN HALF of the graded turns (every turn of the
    script except an OOC probe / exempt turn, where stepping out is asked for). An empty reply is not a
    refusal (the empty-reply rules handle it)."""
    from . import session_checks
    from .graders import refusal_heuristic
    n = int(n_turns or len(replies))
    skip = session_checks.ooc_probe_turns(case) | session_checks.exempt_turn_set(case)
    graded = [t for t in range(1, n + 1) if t not in skip]
    if not graded:
        return False
    hits = sum(1 for t in graded if t <= len(replies) and (replies[t - 1] or "").strip()
               and refusal_heuristic(replies[t - 1]))
    return hits * 2 > len(graded)


#: the verdict stub a programmatic final row carries: "judged" without a judge
PROGRAMMATIC_VERDICT = {"programmatic": True, "elements": {"results": [], "unjudged": []}}


def score_row(row: dict, case: dict, turn_rows: list[dict] | None = None) -> dict:
    from . import session_checks
    turn_rows = turn_rows or []
    from .profiles import row_switch
    case = apply_overrides(case, (row_switch(row, "rpdeep_overrides") or {}).get(case.get("id")))
    # harness v2 board-time scoring: bench45 (profile ``scoring_rules: v2``), a v2 case, or a
    # row judged under the v2 rules; the 4.1 ``bench`` profile scores as it always did
    rules_v2 = (str(row_switch(row, "scoring_rules") or "").strip().lower() == "v2"
                or case.get("v2") is True
                or bool((row.get("judge") or {}).get("scoring_rules_version")))
    replies = [m.get("content") or "" for m in row.get("conversation") or []
               if m.get("role") == "assistant"]
    n_turns = len(case.get("turns") or replies)
    gate_mode = case.get("format_gate") or "rp"
    sections = sections_declared(case)
    sec_kw = {"sections": True} if sections else {}
    prog = is_programmatic(case)
    # a programmatic (instruction-following) case asks for lists, headings, a stated format: no in-character gate
    ooc = {} if prog else {i: ooc_reason(replies[i - 1], gate_mode, **sec_kw) for i in range(1, len(replies) + 1)}
    # rule 6 asks for 150-350 words; a reply over MAX_TURN_WORDS takes more shots
    # at every fact (Fable 2026-10-26: Dark-Scarlett was over 350 on 70% of turns)
    words = {i: len((replies[i - 1] or "").split()) for i in range(1, len(replies) + 1)}
    # harness v2 (spec v2 F4): a case may raise the gate (``max_turn_words``,
    # e.g. 450 for the v2 packs) so length never voids content
    cap = int(case.get("max_turn_words") or MAX_TURN_WORDS)
    for i, n in words.items():
        if not prog and not ooc.get(i) and n > cap:
            ooc[i] = f"over {cap} words ({n})"
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
    # an OOC probe turn (the user steps out of character) expects an OOC
    # answer: the format, length and craft gates do not apply to it
    for i in session_checks.ooc_probe_turns(case) | session_checks.exempt_turn_set(case):
        if ooc.get(i):
            ooc[i] = None
    # r2's check changes (pivot regex, review B6) are v2 board rules, keyed like
    # report._recheck on the profile (plus a v2 case, which 4.1 never had) — not
    # on the row stamp: the 4.1 ``bench`` board keeps the 4.1 patterns
    legacy41 = not (str(row_switch(row, "scoring_rules") or "").strip().lower() == "v2"
                    or case.get("v2") is True)
    chk = session_checks.run_checks(case, replies, legacy41=legacy41) or {"results": []}
    by_id = {r["id"]: r for r in chk["results"]}
    zero = session_checks.turn_gates(case.get("checks") or [], by_id)
    items = []

    def gate(item, spec):
        bad = [f"t{t}: {ooc[t]}" for t in _item_turns(spec, n_turns) if ooc.get(t)]
        if bad and (item["pass"] or item.get("credit")):
            item["pass"] = False
            item.pop("credit", None)
            item["detail"] = "not in character — " + "; ".join(bad)
        why = session_checks.gated(spec, zero) if spec.get("type") else None
        if why and (item["pass"] or item.get("credit")):
            item["pass"] = False
            item.pop("credit", None)
            item["detail"] = "turn scored 0: " + why
        return item

    for c in case.get("checks") or []:
        # review B8: puppeting is always scored. A no_puppeting check without
        # a fact used to be skipped, so speaking for the user cost nothing; it
        # is now a PENALTY: an item only when the model puppeted (a clean
        # session earns nothing extra, so a foil still scores ~0)
        # harness v2: on a v2 case every check without a fact is an item too
        v2 = case.get("v2") is True
        if not c.get("fact") and (c.get("type") != "no_puppeting" or not rules_v2) and not v2:
            continue
        r = by_id.get(c["id"])
        if not c.get("fact") and not v2 and (r is None or r.get("pass")):
            continue
        if r is not None and (r.get("flag_only") or r.get("fallback") or r.get("report_only")
                              or r.get("multiplier") is not None):
            continue        # not a credit of its own
        items.append(gate({"fact": c.get("fact") or c["id"], "ftype": c.get("ftype", "constraint"),
                           "grader": "regex",
                           "id": c["id"], "weight": float(c.get("weight", 1)),
                           "scored": c.get("scored", True), "sanity": bool(c.get("sanity")),
                           "pass": bool(r and r["pass"]),
                           # a word_range near miss: failed, half credit (fix 3.10)
                           **({"credit": session_checks.check_credit(r)}
                              if r and not r["pass"] and session_checks.check_credit(r) else {}),
                           "detail": (r or {}).get("detail", "graded turn empty")}, c))
    j = row.get("judge") or {}
    el = {r["id"]: r for r in ((j.get("elements") or {}).get("results") or [])}
    if ooc_votes_gate(row, case):
        el = rescue_ooc_zeroing(el, replies, j, gate_mode, **({"sections": True} if sections else {}))
    from .profiles import row_switch
    recheck = bool(row_switch(row, "quote_recheck"))
    el = {k: _stored_quote_ok(v, replies, case, recheck) for k, v in el.items()}
    el = {k: _keyword_gates(v, replies, case) for k, v in el.items()}
    if row_switch(row, "quote_own_turns"):
        by_e = {e["id"]: e for e in case.get("judge_elements") or []}
        el = {k: (quote_in_own_turns(v, by_e[k], replies) if k in by_e else v) for k, v in el.items()}
    unjudged = set((j.get("elements") or {}).get("unjudged") or [])
    refused = bool(j.get("refused"))
    # 4.5 (rules v2, fix round 2026-11-18, AUDIT-ITEMS FIX-NOW 2): the code's own refusal test, not only the judge's
    # flag. A reply that refuses (graders.refusal_heuristic) on a MAJORITY of the graded turns is a refused row: 0
    # on every item, decided without the judge. One refusal-shaped line (a character's own "I'm not able to")
    # never decides a row; the 4.1 board keeps its rule
    code_refused = rules_v2 and refused_by_code(replies, case, n_turns)
    refused = refused or code_refused
    system = next((m.get("content") or "" for m in row.get("conversation") or []
                   if m.get("role") == "system"), "")
    for e in case.get("judge_elements") or []:
        if e["id"] in unjudged or e.get("status") == "retire":
            continue
        legacy = LEGACY_CARD_TEXT.get((case.get("id"), e["id"]))
        if legacy and legacy in system:
            e = {**e, "scored": False}
        r = el.get(e["id"])
        # harness v2: a 0/1/2 or negative element scores its stored credit
        # (a verified YES on a negative element is the failure)
        cr = (r or {}).get("credit") if rules_v2 else None
        if cr is not None and r and ((not r["pass"] and cr >= 1.0) or (r["pass"] and cr <= 0.0)):
            # a stored verdict whose gate flipped ``pass`` but left a stale credit
            # (written before fix pass 3): the verdict wins, never the credit
            cr = 1.0 if r["pass"] else 0.0
        ok = bool(r and r["pass"]) if cr is None else cr >= 1.0
        items.append(gate({"fact": e.get("fact") or e["id"].split("-")[0],
                           "ftype": e.get("ftype", "fact"),
                           "grader": "judge", "id": e["id"], "weight": float(e.get("weight", 1)),
                           "scored": e.get("scored", True), "sanity": bool(e.get("sanity")),
                           "floor_miss": bool((r or {}).get("floor_miss")),
                           "pass": ok,
                           **({"credit": float(cr)} if cr is not None and not ok and cr else {}),
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
        "empty_turns": [t.get("turn") for t in turn_rows if not row_answer(t).strip()],
        "ooc_turns": {t: r for t, r in ooc.items() if r},
        # D1 (b): facts the judge called out of character on turns the code calls in character (not zeroed)
        "ooc_judge_code_disagreements": sorted(k for k, v in el.items() if v.get("ooc_disagreement")),
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
    if is_programmatic(case):
        j = j or PROGRAMMATIC_VERDICT       # checks only: no verdict needed, none can fail
        unjudged = set()
    # 4.5 (rules v2, lead 2026-11-15): a session still empty after the run's empty-reply chain is the model's
    # answer: its verdict is ``empty_generation`` (stored with judge_failed, no judge call), every item failed
    # above, so it scores 0 -- never "judge_failed" left out of the mean. The 4.1 bench board keeps its rule.
    empty_zero = rules_v2 and bool(j.get("empty_generation"))
    if code_refused:
        # a refusal the code saw needs no verdict: scored 0 (every item failed above), never left unjudged
        scored = [i for i in items if i.get("scored", True)]
        w = sum(i["weight"] for i in scored)
        return {"status": "ok", "items": items, "rate": 0.0 if w else None, "n": len(scored), "passed": 0,
                "sanity_failed": [i["id"] for i in items if i.get("sanity")], "weight_total": w,
                "weight_passed": 0.0, "unjudged": [], "refused": True, "refused_by": "code",
                "craft": j.get("scores"), **integrity}
    if not j or (j.get("judge_failed") and not empty_zero) or unjudged:
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


# ------------------------------------------------ cut-ladder rung 2 (4.5)

#: judge_policy.group_facts value that merges fact groups into turn windows
GROUP_TURN_WINDOW = "turn_window"
#: default window: graded turns spanned by one merged call
DEFAULT_WINDOW = 4


def fact_groups(elements: list[dict], group_facts: str | None = None,
                window: int | None = None) -> list[tuple[tuple[int, ...], list[dict]]]:
    """The fact-call groups of one rpdeep row, same shape as
    ``judge.deep_groups`` ([(turns tuple, [elements])], turn order).

    Default (``group_facts`` unset/off): one group per distinct ``turns`` set,
    exactly ``deep_groups``. With ``group_facts: turn_window`` (cut-ladder rung
    2, PLAN-45-v3 2.5): adjacent groups are merged while the merged turns span
    at most ``window`` turns (default 4), so one judge call carries every fact of
    that window. Per-element verdicts are untouched: elements keep their own
    ``turns`` (the scorer's in-character gate and the quote scope use those, not
    the call's), and an element's id stays the key of its result."""
    groups: dict[tuple, list] = {}
    for e in elements:
        groups.setdefault(tuple(sorted(e.get("turns") or [])), []).append(e)
    base = sorted(groups.items())
    if group_facts != GROUP_TURN_WINDOW:
        return base
    win = max(1, int(window or DEFAULT_WINDOW))
    merged: list[tuple[tuple[int, ...], list[dict]]] = []
    for turns, els in base:
        if merged:
            ts = set(merged[-1][0]) | set(turns)
            if turns and ts and max(ts) - min(ts) + 1 <= win:
                merged[-1] = (tuple(sorted(ts)), merged[-1][1] + list(els))
                continue
        merged.append((tuple(turns), list(els)))
    return merged
