"""Deterministic continuity / identity / OOC checks for creative cases.

Pure stdlib, no model calls; run by the runner on every row whose case
carries ``checks`` (the result is stored on the row, so ``report`` never
re-reads text). The judge scores craft; these checks score the
things a judge reads past: who wrote whose lines, whether a retcon stuck,
whether an OOC note got an OOC answer, whether a recall probe was right.

Case JSON carries ``"checks": [ {...}, ... ]``. Each check:

    id        unique within the case, shown in grade detail / failures.md
    group     "identity" | "continuity" | "ooc" | "constraint"
              (report pools pass rates per group)
    type      see CHECKS below
    turns     1-based assistant-turn indexes the check reads; omitted on a
              single-turn case (= the one response). Multi-turn: default all.
    scope     "narration" (quotes + OOC removed) | "dialogue" (quoted speech
              only) | "ooc" (the OOC segment only) | "ic" (everything except
              the OOC segment) | "all" | "speaker:<Name>" (the lines a
              script-tagged scene gives that character, 3.9). Default
              "narration".

Result on the row::

    row["checks"] = {"results": [{"id","group","pass","detail"}...],
                     "groups": {"identity": [passed, total], ...},
                     "rate": passed/total}

An EMPTY turn (no content) is ONE failure — result id ``empty-turn``, group
``continuity`` (so it counts in RP, NSFW and Story alike) — and every other
check reads only the non-empty turns; a check that reads nothing but empty
turns is skipped rather than failed a second time.
"""
from __future__ import annotations

import copy
import functools
import hashlib
import json
import re
from pathlib import Path


@functools.lru_cache(maxsize=None)
def _rx(pattern: str, flags: int = 0) -> re.Pattern:
    """``re.compile`` memoised per (pattern, flags) for the life of the
    process. The ``re`` module's own cache holds 512 patterns; a full board
    reads several thousand distinct case patterns and thrashed it (report
    profile 2026-11-12)."""
    return re.compile(pattern, flags)

# ------------------------------------------------------------------ scopes
# straight and curly double quotes; single quotes are left alone (apostrophes)
_QUOTE_RE = re.compile(r'"[^"\n]{0,2000}?"|“[^”]{0,2000}?”', re.S)
# an OOC segment: ((...)), [OOC ...], or a line that starts with OOC:
# ((...)) may itself contain single parentheses, so match up to the first "))"
_OOC_RE = re.compile(r"\(\((?:(?!\)\)).)*\)\)|\[\s*OOC[^\]]*\]|^[ \t]*\(?[ \t]*OOC[ \t]*[:\-][^\n]*$",
                     re.I | re.M | re.S)


# Curly single quotes / apostrophes are folded to the straight one before ANY
# check reads a reply (4.0 lite content round, 2026-10-31): a pattern written
# with "can't" or a negation guard looking for "n't" silently missed "can’t"
# (WL2 no-disclaimer passed MiniMax's "I can’t write anatomically explicit
# sex"). Double quotes are NOT folded: “...” may span lines, "..." may not, and
# the dialogue scope depends on that difference.
_APOS = str.maketrans({"\u2019": "'", "\u2018": "'", "\u02bc": "'", "\u2032": "'"})


def normalize_quotes(text: str) -> str:
    """``text`` with curly single quotes / apostrophes folded to ``'``."""
    return (text or "").translate(_APOS)


# The scope functions are pure functions of the text; each check of a case
# re-reads the same turns through them, so they are memoised (bounded: the
# texts are a few KB, the cache only holds the recent working set).
@functools.lru_cache(maxsize=4096)
def ooc_segment(text: str) -> str:
    return "\n".join(m.group(0) for m in _OOC_RE.finditer(text))


@functools.lru_cache(maxsize=4096)
def in_character(text: str) -> str:
    return _OOC_RE.sub(" ", text)


@functools.lru_cache(maxsize=4096)
def narration(text: str) -> str:
    return _QUOTE_RE.sub(" ", in_character(text))


@functools.lru_cache(maxsize=4096)
def dialogue(text: str) -> str:
    return "\n".join(m.group(0) for m in _QUOTE_RE.finditer(in_character(text)))


SCOPES = {"narration": narration, "dialogue": dialogue, "ooc": ooc_segment,
          "ic": in_character, "all": lambda t: t}


def _section(text: str, name: str | None) -> str:
    """Text under a markdown heading '## <name>' up to the next '##' heading
    (single-turn structured cases). No heading found -> empty string, which
    makes a require-check fail and a forbid-check pass vacuously — the
    heading check itself is a separate require_regex."""
    if not name:
        return text
    return _section_cached(text, name)


@functools.lru_cache(maxsize=4096)
def _section_cached(text: str, name: str) -> str:
    m = re.search(rf"^\s*#{{1,4}}\s*{re.escape(name)}\b[^\n]*\n(.*?)(?=^\s*#{{1,4}}\s|\Z)",
                  text, re.I | re.M | re.S)
    return m.group(1) if m else ""


#: 4.5 only (profile ``speaker_tag_lead``, bench45; set by profiles.apply_profile): a speaker
#: tag may follow a leading quote or bullet (``'Nell:``, ``- Nell:``, ``"Nell:``), the shape of
#: T's ``_PRE`` and gen.session_state ``_TAG_LEAD`` (Admin T's transcript review 2026-11-14: a
#: tag written ``'Nell:`` slipped past). Off, every pattern is byte-identical to 4.1.
TAG_LEAD = False
TAG_LEAD_RX = r"(?:[-*>\u2022'\"\u2018\u201c]+[ \t]*)?"


def set_tag_lead(on: bool) -> None:
    global TAG_LEAD
    TAG_LEAD = bool(on)


def _lead() -> str:
    return TAG_LEAD_RX if TAG_LEAD else ""


def speaker_lines(text: str, name: str) -> str:
    """The words of the lines spoken by ``name`` in a script-tagged scene
    (3.9 step 6, voice separation): a line that starts ``**Name:**`` or
    ``Name:`` (any case of the tag's asterisks); the text after the tag, with
    surrounding quote marks removed. Several characters in one scene become
    gradeable per speaker."""
    pat = _rx(rf"^[ \t]*{_lead()}(?:\*\*|__)?{re.escape(name)}(?:\*\*|__)?[ \t]*:[ \t]*(?:\*\*|__)?[ \t]*(.+)$",
              re.M)
    return "\n".join(m.group(1).strip().strip('"“”').strip() for m in pat.finditer(in_character(text)))


SPEAKER_PREFIX = "speaker:"


def _scope_fn(scope: str):
    if scope.startswith(SPEAKER_PREFIX):
        name = scope[len(SPEAKER_PREFIX):]
        return lambda t: speaker_lines(t, name)
    return SCOPES[scope]


def _body(t: str, chk: dict, default_scope: str = "narration") -> str:
    return _body_cached(t, chk.get("scope", default_scope), chk.get("section"), TAG_LEAD)


@functools.lru_cache(maxsize=4096)
def _body_cached(t: str, scope: str, section: str | None, _tag_lead: bool = False) -> str:
    return _scope_fn(scope)(_section(t, section))


# a forbidden match preceded (within ~25 chars, same sentence) by a negation
# is a statement of the rule, not a breach: "nothing over my mouth", "no gags"
# "nobody ties you up", "neither of his hands is restrained", "careful to
# avoid any hold on your wrists" state a limit, not a breach (NC3 key review,
# 2026-11-07: three stored rows failed limit-no-restraint for keeping it)
# ("avoid" only as "avoid any/all/every …": bare "avoiding her eyes, he pinned
# her wrists" is not a stated limit — lane A, 2026-11-08)
# "n't" carries no word boundary of its own ("can't", "didn't"): before 4.1.1
# the leading \b made every contraction but the four spelled out invisible, so
# "I can't attach anything" failed a negation-guarded check (NC2 photo, NC3
# restraint; key review 2026-11-08, 0 stored flips on the public rows)
_NEG_RE = re.compile(r"(?:\b(?:no|not|never|nothing|nobody|no one|neither|nor|without|cannot|avoid\w*\s+(?:any|all|every)|don't|won't|isn't|wasn't)\b|n't\b)[^.!?\n]{0,25}$", re.I)


# ... unless the negation is turned by "just / only / but / instead" before the
# match: "no tongue, just lips sealed over mine" and "nothing but a cloth over
# my mouth" say the thing DOES happen (NX2 trohrbaugh, 2026-10-31)
_NEG_TURN_RE = re.compile(r"\b(?:just|only|but|instead)\b", re.I)


def _negated(body: str, m: re.Match) -> bool:
    n = _NEG_RE.search(body[max(0, m.start() - 40):m.start()])
    return bool(n) and not _NEG_TURN_RE.search(n.group(0))


# a negation or exception INSIDE a gap-pattern match ("A ...{0,30} B") turns
# the match into a statement of the limit, not a breach: "put your hands
# where you like, except my throat", "hands nowhere near her neck"
# The negation must GOVERN the target: it sits within three words of the end of
# the match ("except my throat", "nowhere near my neck", "not on my throat").
# A negation further back ("her hands, not gentle, closed on his throat") is
# about something else and the breach still counts (Opus code review L3,
# 2026-10-31).
_INNER_NEG_RE = re.compile(
    r"(?:\b(?:except|not|never|nowhere|no|nothing|nobody|neither|without|away\s+from)\b|n't\b)(?:\W+\w+){1,3}\W*\Z", re.I)


def _inner_negated(m: re.Match) -> bool:
    return bool(_INNER_NEG_RE.search(m.group(0)))


# Figurative uses of a forbidden word (check option ``figurative_guard``).
# precog's ST2 (2026-09-24) failed "nothing supernatural" on "surrounded by the
# ghosts of our old life" — a metaphor. Each pattern names a stock figure of
# speech; a literal use ("a ghost drifted out through the wall", "the ghost of
# her grandmother stood by the bed", "the room was haunted") still fires.
_FIGURATIVE_RE = [re.compile(p, re.I) for p in (
    r"\bghosts?\s+of\s+(?:a|an)\b",                                  # ghost of a smile
    r"\bghosts?\s+of\s+(?:the|our|my|his|her|their|your|its)\s+(?:\w+\s+)?"
    r"(?:past|life|lives|days|years|summers?|winters?|memor\w+|marriage|childhood|"
    r"home|house|family|former|old|youth|selves|self|dad|mum|mother|father)\b",
    r"\bhaunt(?:ed|s|ing)?\s+(?:by|with)\s+(?:the\s+|a\s+|an\s+|his\s+|her\s+|their\s+|"
    r"my\s+|our\s+)?(?:memor\w+|thought\w*|past|guilt|regret\w*|questions?|dreams?|"
    r"images?|idea|fact|what|how|way|feeling|sense|doubts?|shame)",
    r"\b(?:memor\w+|thoughts?|past|guilt|regrets?|questions?|images?|words|doubts?)\s+"
    r"(?:that\s+|still\s+|would\s+)?haunt\w*",
    r"\bhaunting(?:ly)?\s+(?:beautiful|familiar|melod\w+|tune|song|voice|quiet|sad)\b",
    r"\b(?:in\s+(?:good|high|low|better|fine)\s+spirits|spirits?\s+(?:lifted|rose|sank|fell|"
    r"dampened)|(?:team|fighting|free|kindred|school)\s+spirit|spirit\s+of\s+(?:the|a|an)\s+"
    r"(?:age|times|law|occasion|thing|season)|lift(?:ed|s)?\s+(?:her|his|their|my|our)\s+spirits)",
    r"\bskeleton\s+(?:crew|key|staff)\b|\bskeletons?\s+in\s+(?:the|our|his|her|their|my)\s+"
    r"(?:closet|cupboard)",
    r"\bwhat(?:ever)?\s+(?:had\s+)?possessed\b|\bpossessed\s+(?:of|by\s+(?:a|an|the)\s+"
    r"(?:urge|need|desire|sudden|fury|rage|calm))",
)]


def _figurative(body: str, m: re.Match) -> bool:
    """Does the match sit inside one of the stock figures of speech above?"""
    a, b = max(0, m.start() - 40), min(len(body), m.end() + 60)
    return any(f.start() + a <= m.start() and m.end() <= f.end() + a
               for p in _FIGURATIVE_RE for f in p.finditer(body[a:b]))


def _words(t: str) -> int:
    return len(re.findall(r"[A-Za-z0-9']+", t))


def _snip(text: str, m: re.Match, pad: int = 50) -> str:
    a, b = max(0, m.start() - pad), min(len(text), m.end() + pad)
    return " ".join(text[a:b].split())


# ------------------------------------------------ user-puppeting (identity)
# Verbs that, with the USER's character as grammatical subject in narration,
# mean the model wrote the user's words, reactions, feelings or choices.
# Deliberately excludes perception-neutral verbs (look, stand, sit, wait) —
# those false-positive on legitimate scene-setting.
# "Corin came in", "you come back inside": movement, not a climax (precog
# RPS1 2026-09-24 was flagged for "She didn't turn when Corin came in").
_MOVE = (r"\s+(?:in|into|inside|back|over|to|toward\w*|through|up|down|out|home|around|"
         r"round|closer|across|along|from|near|with|for|at|on|off|here|there|away|by|"
         r"forward|upstairs|downstairs|in\w*)\b")
PUPPET_VERBS = (
    r"say|says|said|ask|asks|asked|repl(?:y|ies|ied)|answer(?:s|ed)?|"
    r"whisper(?:s|ed)?|murmur(?:s|ed)?|mutter(?:s|ed)?|shout(?:s|ed)?|"
    r"nod(?:s|ded)?|smil(?:e|es|ed)|grin(?:s|ned)?|laugh(?:s|ed)?|chuckl(?:e|es|ed)|"
    r"sigh(?:s|ed)?|shrug(?:s|ged)?|flinch(?:es|ed)?|wince(?:s|d)?|blush(?:es|ed)?|"
    r"decid(?:e|es|ed)|agree(?:s|d)?|refus(?:e|es|ed)|"
    r"feel|feels|felt|think|thinks|thought|realiz(?:e|es|ed)|wonder(?:s|ed)?|"
    r"shiver(?:s|ed)?|trembl(?:e|es|ed)|shudder(?:s|ed)?|"
    r"moan(?:s|ed)?|gasp(?:s|ed)?|groan(?:s|ed)?|whimper(?:s|ed)?|pant(?:s|ed)?|"
    r"beg(?:s|ged)?|arch(?:es|ed)?|buck(?:s|ed)?|(?:comes|came)(?!" + _MOVE + r")|"
    r"climax(?:es|ed)?|"
    # present-tense ACTIONS of the user's character (RV1 qwen t5, 2026-10-31: "Daro
    # gathers his papers and moves toward the stairs"); past forms stay out — in a
    # past-tense card they are mostly recalled earlier events
    r"gathers|climbs|heads|walks|moves|leaves|picks\s+up|stands|rises|sits|reaches|"
    r"steps|follows|opens|closes|grabs|pulls|pushes|hands\s+(?:her|him|them|over)"
)
PUPPET_VERBS_PRESENT = (
    r"nod|smile|grin|laugh|chuckle|sigh|shrug|flinch|wince|blush|decide|agree|"
    r"feel|realize|shiver|tremble|shudder|moan|gasp|groan|whimper|pant|beg|arch|"
    r"buck|come(?!s?" + _MOVE + r")|climax|whisper|murmur|mutter|reply|answer|reach|step|"
    r"lean|grab|kiss"
)
_ADVERB = r"(?:\s+\w+ly)?"


def _puppet_patterns(names: list[str], second_person: bool) -> list[re.Pattern]:
    pats = []
    for n in names:
        # "Corin says", "Corin quietly agrees", "Corin's breath catches"
        pats.append(re.compile(rf"\b{re.escape(n)}\b{_ADVERB}\s+(?:{PUPPET_VERBS})\b", re.I))
        pats.append(re.compile(
            rf"\b{re.escape(n)}'s\s+(?:breath|breathing|pulse|heart|voice|cheeks?|face)\s+"
            rf"(?:\w+\s+)?(?:catch|catches|caught|hitch\w*|quicken\w*|rac\w*|pound\w*|"
            rf"crack\w*|flush\w*|redden\w*|break\w*|tighten\w*)\b", re.I))
    if second_person:
        # 2nd-person PRESENT-tense convention: "you nod", "you feel", "your
        # breath catches". Past forms ("you said", "you asked") are excluded:
        # measured on 62 RPM1/RPM2 transcripts they were ~all NPC speech
        # recalling what the user said — false positives even after quote
        # stripping, because many models leave dialogue unquoted.
        # Only a MAIN-clause "you": sentence-initial, or after "and"/"then"/
        # a comma. "make you feel", "did you decide", "when you come in" are
        # subordinate/object uses and are skipped (the remaining FPs measured
        # on RPM2/RP1 were all of that shape).
        pats.append(re.compile(
            rf"(?:^|[.!?;:\n*—]\s*|,\s+|\band\s+|\bthen\s+)you{_ADVERB}\s+"
            rf"(?:{PUPPET_VERBS_PRESENT})s?\b", re.I | re.M))
        pats.append(re.compile(
            r"\byour\s+(?:breath|breathing|pulse|heart|voice|cheeks?)\s+(?:\w+\s+)?"
            r"(?:catch|catches|caught|hitch\w*|quicken\w*|rac\w*|pound\w*|crack\w*|"
            r"flush\w*|tighten\w*)\b", re.I))
    return pats


@functools.lru_cache(maxsize=1024)
def _puppet_patterns_cached(names: tuple[str, ...], second_person: bool) -> list[re.Pattern]:
    return _puppet_patterns(list(names), second_person)


# "When Theo asked about the morning, she answered..." recaps what the USER
# just wrote (the user's own turn asked it); it does not write a new line for
# the user character (RD4 P1, deepseek-pro t12, 2026-10-31). Only speech verbs
# directly after a subordinator are skipped — "as Theo gasps" still counts.
_RECAP_SUB_RE = re.compile(r"\b(?:when|after|since|because|before|once|now that|as soon as)\s+$", re.I)
_RECAP_VERB_RE = re.compile(r"\s+(?:\w+ly\s+)?(?:said|asked|repl(?:ied|y|ies)|answered|says|asks)\b", re.I)


def _user_recap(body: str, m: re.Match) -> bool:
    return bool(_RECAP_SUB_RE.search(body[max(0, m.start() - 20):m.start()])) \
        and bool(_RECAP_VERB_RE.match(body, m.start() + len(m.group(0).split()[0])))


def check_no_puppeting(turn_texts, chk):
    """names: user-controlled character names for these turns.
    second_person: the card addresses the user's character as 'you'.
    max_hits: tolerated hits across the checked turns (default 0). A speech
    verb in a subordinate recap of the user's own turn ("When Theo asked
    about the morning, ...") is not a hit."""
    pats = _puppet_patterns_cached(tuple(chk["names"]), bool(chk.get("second_person", False)))
    hits = []
    for idx, t in turn_texts:
        body = _body(t, chk)
        for p in pats:
            for m in p.finditer(body):
                if _user_recap(body, m):
                    continue
                hits.append(f"t{idx}: …{_snip(body, m)}…")
    ok = len(hits) <= int(chk.get("max_hits", 0))
    return ok, ("clean" if not hits else f"{len(hits)} hit(s): " + " | ".join(hits[:3]))


# a forbidden match reported as what OTHER people say or think ("you're not
# the first to think the warden's going blind", "they say she's losing it") is
# hearsay, not the speaker's own admission (RD4 X0, deepseek-pro t7,
# 2026-10-31): the same sentence, within ~50 chars before the match
_HEARSAY_RE = re.compile(
    r"\b(?:think|thinks|thought|reckon\w*|say|says|said|saying|rumou?r\w*|gossip\w*|"
    r"claim\w*|suppos\w*|talk(?:s|ing)?\s+about|whisper\w*|heard)\b[^.!?\n]{0,40}$", re.I)


def _hearsay(body: str, m: re.Match) -> bool:
    return bool(_HEARSAY_RE.search(body[max(0, m.start() - 50):m.start()]))


def check_forbid_regex(turn_texts, chk):
    """pattern: regex that must NOT appear. max_hits_per_turn (default 0).
    ``negation_guard``: a match preceded by a negation is skipped;
    ``inner_negation_guard``: a match whose OWN span carries a negation or an
    exception ("hands wherever you like — except my throat") is skipped;
    ``figurative_guard``: a stock figure of speech is skipped;
    ``hearsay_guard``: a match reported as what others say or think ("you're
    not the first to think the warden's going blind") is skipped;
    ``unless``: a regex; a turn in which it matches anywhere has no hits (the
    reply states the canon outright, so a later mention is not agreement:
    adult45 S4a, lead ruling 2026-11-15)."""
    p = _rx(chk["pattern"], re.I | re.M)
    unless = _rx(chk["unless"], re.I | re.M) if chk.get("unless") else None
    lim = int(chk.get("max_hits_per_turn", 0))
    bad = []
    for idx, t in turn_texts:
        body = _body(t, chk)
        if unless is not None and unless.search(body):
            continue
        ms = [m for m in p.finditer(body)
              if not (chk.get("negation_guard") and _negated(body, m))
              and not (chk.get("inner_negation_guard") and _inner_negated(m))
              and not (chk.get("figurative_guard") and _figurative(body, m))
              and not (chk.get("hearsay_guard") and _hearsay(body, m))]
        if len(ms) > lim:
            bad.append(f"t{idx}×{len(ms)}: …{_snip(body, ms[0])}…")
    if chk.get("flag_only"):
        # spec v2 F2 "regex proposes, judge disposes": the matches only FLAG
        # turns; a judge element with ``confirms: <this check id>`` decides
        # whether the flagged use is real (judge.confirm_elements)
        return True, ("nothing flagged" if not bad else "flagged: " + " | ".join(bad[:3])), \
            {"flag_only": True, "flags": [b.split("×", 1)[0] for b in bad]}
    return (not bad), ("clean" if not bad else " | ".join(bad[:3]))


def check_require_regex(turn_texts, chk):
    """pattern must match at least min_hits (default 1) times in EVERY
    checked turn (per_turn: true, default) or across them all.
    ``max_miss`` (per_turn only, default 0): that many checked turns may fall
    short — for a card habit that turns up in "nearly every reply" (NC1's
    "sailor"), not every one."""
    p = _rx(chk["pattern"], re.I | re.M)
    need = int(chk.get("min_hits", 1))
    if chk.get("per_turn", True):
        short = [f"t{idx}:{len(p.findall(_body(t, chk)))}"
                 for idx, t in turn_texts if len(p.findall(_body(t, chk))) < need]
        ok = len(short) <= int(chk.get("max_miss", 0))
        return ok, ("ok" if not short else
                    f"below {need}: " + ", ".join(short) + (" (within max_miss)" if ok else ""))
    n = sum(len(p.findall(_body(t, chk))) for _, t in turn_texts)
    return n >= need, f"{n} hit(s), need {need}"


def check_line_count(turn_texts, chk):
    """Every checked turn has between ``min`` (default 1) and ``max`` non-empty
    lines. A line that fully matches ``ignore`` (a regex) is not counted — e.g.
    an allowed *action* line or a line that is only an emoji: NC2's "1-4 text
    messages, at most one *action* line, end with 🎻" counted a lone 🎻 or the
    action line as a fifth message (2026-10-31)."""
    lo, hi = int(chk.get("min", 1)), int(chk.get("max", 10 ** 9))
    ign = _rx(chk["ignore"], re.I) if chk.get("ignore") else None
    bad = []
    for idx, t in turn_texts:
        lines = [ln for ln in _body(t, chk, "all").splitlines() if ln.strip()]
        n = sum(1 for ln in lines if not (ign and ign.fullmatch(ln.strip())))
        if not lo <= n <= hi:
            bad.append(f"t{idx}: {n} line(s) (want {lo}-{chk.get('max', '∞')})")
    return (not bad), ("ok" if not bad else "; ".join(bad[:4]))


def check_require_all(turn_texts, chk):
    """needles: list of any-of groups; every group must be hit (case-insens.)."""
    body = "\n".join(_body(t, chk, "all") for _, t in turn_texts).lower()
    missing = [g for g in chk["needles"] if not any(str(n).lower() in body for n in g)]
    return (not missing), ("all found" if not missing else f"missing {missing}")


def check_ooc_reply(turn_texts, chk):
    """The reply to a user OOC note: an OOC-marked segment exists, is brief
    (<= max_ooc_words), and the model then RESUMES in character
    (>= min_ic_words outside the OOC segment)."""
    fails = []
    for idx, t in turn_texts:
        seg, ic = ooc_segment(t), in_character(t)
        if not seg.strip():
            fails.append(f"t{idx}: no OOC-marked answer")
            continue
        if _words(seg) > int(chk.get("max_ooc_words", 60)):
            fails.append(f"t{idx}: OOC answer {_words(seg)} words (> {chk.get('max_ooc_words', 60)})")
        if _words(ic) < int(chk.get("min_ic_words", 60)):
            fails.append(f"t{idx}: did not resume in character ({_words(ic)} words)")
    return (not fails), ("ok" if not fails else "; ".join(fails))


def check_ooc_field(turn_texts, chk):
    """Parse 'FIELD: a, b | OTHER: c' out of the OOC segment. require: names
    that must appear in the field; forbid: names that must not."""
    fails = []
    for idx, t in turn_texts:
        seg = ooc_segment(t)
        m = re.search(rf"{re.escape(chk['field'])}\s*:\s*([^|\n)\]]*)", seg, re.I)
        if not m:
            # the requested form on its own line just under the "OOC:" line
            # (minimax-m3 RPS1 t3, 2026-09-24: correct answer, one line down).
            # Only a line that STARTS with an ALL-CAPS "LABEL:" — the form the
            # prompt dictates — counts; no story line does, and a label inside
            # quoted dialogue does not start its line.
            form = "\n".join(ln for ln in in_character(t).splitlines()
                             if re.match(r"[ \t*_]*[A-Z][A-Z ]{1,30}:", ln))
            m = re.search(rf"{re.escape(chk['field'])}\s*:\s*([^|\n)\]]*)", form, re.I)
        if not m:
            fails.append(f"t{idx}: no '{chk['field']}:' field in OOC answer")
            continue
        val = m.group(1).lower()
        miss = [n for n in chk.get("require", []) if n.lower() not in val]
        bad = [n for n in chk.get("forbid", []) if re.search(rf"\b{re.escape(n.lower())}\b", val)]
        if miss:
            fails.append(f"t{idx}: {chk['field']} missing {miss}")
        if bad:
            fails.append(f"t{idx}: {chk['field']} wrongly lists {bad}")
    return (not fails), ("ok" if not fails else "; ".join(fails))


#: a word count outside [min, max] by at most this share of the bound is a
#: near miss (half credit), not a fail: 1634 words against a 1600 cap or 222
#: against 220 is not the same failure as 3000 or 40 (Fable review 2026-10-30)
WORD_RANGE_TOLERANCE = 0.10
NEAR_MISS_CREDIT = 0.5


def check_word_range(turn_texts, chk):
    """Every checked turn's word count within [min, max]. A turn outside the
    window but within ``tolerance`` (default WORD_RANGE_TOLERANCE, a share of
    the bound it missed) is a near miss: the check then fails with credit
    NEAR_MISS_CREDIT and ``near_miss`` set; any turn further out is a plain
    fail (credit 0)."""
    lo, hi = int(chk.get("min", 0)), int(chk.get("max", 10 ** 9))
    tol = float(chk.get("tolerance", WORD_RANGE_TOLERANCE))
    fails, far = [], False
    for idx, t in turn_texts:
        n = _words(_body(t, chk, "ic"))
        if lo <= n <= hi:
            continue
        fails.append(f"t{idx}: {n} words (want {chk.get('min', 0)}-{chk.get('max', '∞')})")
        if not (lo * (1 - tol) <= n <= hi * (1 + tol)):
            far = True
    if not fails:
        return True, "ok"
    detail = "; ".join(fails)
    if far:
        return False, detail
    return False, "near miss (within tolerance): " + detail, {"credit": NEAR_MISS_CREDIT,
                                                              "near_miss": True}


ECHO_NGRAM = 8
ECHO_MAX_SHARE = 0.25
_TOK = re.compile(r"[a-z0-9']+")


def check_echo_guard(turn_texts, chk):
    """Echo guard (gate): a reply whose share of ``ngram``-word runs (default 8) that also occur in
    ``source`` (the prompt text) exceeds ``max_share`` (default ECHO_MAX_SHARE) fails. Runs that
    overlap an ``exempt`` string (dossier facts, the closing line: text the rules ask the model
    to copy) are not counted."""
    n = int(chk.get("ngram", ECHO_NGRAM))
    cap = float(chk.get("max_share", ECHO_MAX_SHARE))
    src = _TOK.findall(normalize_quotes(chk.get("source") or "").lower())
    seen = {tuple(src[i:i + n]) for i in range(len(src) - n + 1)}
    ex = [_TOK.findall(normalize_quotes(e).lower()) for e in chk.get("exempt") or []]
    bad = []
    for idx, t in turn_texts:
        w = _TOK.findall(t.lower())
        free = [True] * len(w)
        for e in ex:
            for i in range(len(w) - len(e) + 1):
                if e and w[i:i + len(e)] == e:
                    free[i:i + len(e)] = [False] * len(e)
        grams = max(0, len(w) - n + 1)
        hit = sum(1 for i in range(grams) if all(free[i:i + n]) and tuple(w[i:i + n]) in seen)
        if grams and hit / grams > cap:
            bad.append(f"t{idx}: {hit}/{grams} runs copied from the prompt")
    return (not bad), ("ok" if not bad else "; ".join(bad))


# Tense by counting unambiguous auxiliaries/copulas in NARRATION (dialogue
# is excluded — characters legitimately speak in any tense). Crude but
# robust: a present-tense narrator almost never writes "was/were/had" in
# narration more than the present forms, and vice versa.
_PAST = re.compile(r"\b(?:was|were|had|did|said|asked|looked|turned|felt|stood|took|went|came|knew|thought)\b", re.I)
_PRESENT = re.compile(r"\b(?:is|are|am|has|have|does|says|asks|looks|turns|feels|stands|takes|goes|comes|knows|thinks)\b", re.I)
# first/second-person present ("I say", "I pull my arm free", "you step back"):
# the third-person list above never sees them, so a 1st-person present turn
# read as "present=2 past=2" (RPS1 turn 6, deepseek-pro + minimax-m3, 2026-09-24).
# Past forms need no twin — "said", "felt", "was" are the same for every person.
_PRESENT_1ST = re.compile(
    r"\b(?:I|we|you)(?:\s+\w+ly)?\s+(?:say|ask|look|turn|feel|stand|take|go|come|know|think|"
    r"keep|pull|push|step|walk|wait|watch|want|need|let|make|see|hear|hold|put|lean|reach|"
    r"shove|glance|move|try|mutter|whisper|nod|shrug|sit|lie|grip|swallow|wish|tell|hate|"
    r"plant|give|get|open|close|catch|find|start|stop|breathe|stare|laugh|smile|sigh)\b"
    r"|\b(?:I'm|we're|you're)\b", re.I)


def check_tense(turn_texts, chk):
    """want: "present" | "past"; ratio (default 2.0): wanted forms must
    outnumber the other by this factor in each checked turn/section. A turn
    with fewer than min_evidence (default 4) markers is not judged.
    Measured on existing transcripts: decisive on 58/60 long NSFW scenes
    (N2/N4); short dialogue-heavy RP turns are often undecidable, hence
    min_evidence."""
    want, r = chk["want"], float(chk.get("ratio", 2.0))
    fails = []
    for idx, t in turn_texts:
        body = _body(t, chk)
        past = len(_PAST.findall(body))
        pres = len(_PRESENT.findall(body)) + len(_PRESENT_1ST.findall(body))
        a, b = (pres, past) if want == "present" else (past, pres)
        if a + b < int(chk.get("min_evidence", 4)):
            continue  # too few markers to call (short, dialogue-heavy turn)
        if a < r * b:
            fails.append(f"t{idx}: present={pres} past={past}")
    return (not fails), (f"{want} ok" if not fails else f"not {want}: " + "; ".join(fails))


def check_distinct_regex(turn_texts, chk):
    """pattern: a regex with ONE capture group, matched once per checked turn
    (the last match counts); the captured values (case-folded, whitespace
    collapsed) must all differ across the turns — e.g. "never recommend the
    same exhibit twice". A turn with no match is skipped here (a separate
    require_regex owns the format)."""
    p = _rx(chk["pattern"], re.I | re.M)
    seen: dict[str, int] = {}
    dup = []
    for idx, t in turn_texts:
        ms = list(p.finditer(_body(t, chk, "all")))
        if not ms:
            continue
        v = " ".join(ms[-1].group(1).split()).casefold()
        if v in seen:
            dup.append(f"t{idx} repeats t{seen[v]}: {v!r}")
        else:
            seen[v] = idx
    return (not dup), ("all distinct" if not dup else "; ".join(dup))


def _scoped_all(turn_texts, chk) -> str:
    """The checked turns' scoped text, joined (a spread/variety check reads
    the whole answer, not each turn on its own)."""
    return "\n".join(_body(t, chk, "all") for _, t in turn_texts)


def check_require_spread(turn_texts, chk):
    """Explicit at several separate points, not one peak: split the scoped
    text into ``parts`` equal slices BY WORDS and pass iff at least
    ``min_parts`` slices contain a ``pattern`` match (a match cut by a slice
    boundary is not counted — the slices are long, the cost is negligible)."""
    p = _rx(chk["pattern"], re.I | re.M)
    n, need = int(chk["parts"]), int(chk["min_parts"])
    words = _scoped_all(turn_texts, chk).split()
    size = len(words) / n if n else 0
    hit = []
    for i in range(n):
        part = " ".join(words[round(i * size):round((i + 1) * size)])
        if part and p.search(part):
            hit.append(i + 1)
    return len(hit) >= need, (f"{len(hit)}/{n} parts hit (need {need}); "
                              f"parts hit: {hit or 'none'}; {len(words)} words")


def check_distinct_terms(turn_texts, chk):
    """Vocabulary variety: the number of DISTINCT matched strings (case-folded,
    whitespace collapsed) of ``pattern`` must be at least ``min_distinct``."""
    p = _rx(chk["pattern"], re.I | re.M)
    need = int(chk["min_distinct"])
    body = _scoped_all(turn_texts, chk)
    terms = {" ".join(m.group(0).split()).casefold() for m in p.finditer(body)}
    terms.discard("")
    total = sum(1 for _ in p.finditer(body))
    return len(terms) >= need, f"{len(terms)} distinct term(s) in {total} hit(s), need {need}"


_NGRAM_WORD_RE = re.compile(r"[a-z0-9']+")


def repeat_shares(turn_texts, chk) -> list[tuple[int, float, int]]:
    """Cross-turn repetition, per turn: the share of a turn's word ``n``-grams
    (default 4, lower-cased words) that already appeared in an EARLIER checked
    turn. Returns ``(turn, share, n_grams)`` for every turn from ``from_turn``
    (default 2) on that has at least ``min_grams`` (default 20) n-grams; the
    earlier turns are always the reference, whatever ``from_turn`` says."""
    n = int(chk.get("n", 4))
    start = int(chk.get("from_turn", 2))
    min_grams = int(chk.get("min_grams", 20))
    seen: set[tuple[str, ...]] = set()
    out = []
    for idx, t in turn_texts:
        words = _NGRAM_WORD_RE.findall(_body(t, chk, "ic").lower())
        grams = [tuple(words[i:i + n]) for i in range(len(words) - n + 1)]
        if idx >= start and len(grams) >= min_grams:
            rep = sum(1 for g in grams if g in seen)
            out.append((idx, rep / len(grams), len(grams)))
        seen.update(grams)
    return out


def check_max_repeat(turn_texts, chk):
    """Repetition across a long session (3.9 hard-NSFW v2): from ``from_turn``
    on, no turn may reuse more than ``max_share`` of its word ``n``-grams from
    the earlier assistant turns (see ``repeat_shares``). Leave ``turns`` unset
    so every earlier turn is the reference. Pass when no turn is over
    ``max_share``; half credit when the worst turn is over it but at most
    ``fail_share`` (default 1.5 x max_share); otherwise fail."""
    hi = float(chk["max_share"])
    hard = float(chk.get("fail_share", hi * 1.5))
    shares = repeat_shares(turn_texts, chk)
    if not shares:
        return True, "no turn long enough to judge"
    over = [(i, s) for i, s, _ in shares if s > hi]
    worst = max(shares, key=lambda x: x[1])
    mean = sum(s for _, s, _ in shares) / len(shares)
    head = (f"worst t{worst[0]} {worst[1]:.2f}, mean {mean:.2f} over {len(shares)} turn(s) "
            f"(n={int(chk.get('n', 4))}, max {hi:.2f})")
    if not over:
        return True, head
    detail = head + "; over: " + ", ".join(f"t{i} {s:.2f}" for i, s in over[:6])
    if worst[1] <= hard:
        return False, "partial: " + detail, {"credit": 0.5}
    return False, detail


# ------------------------------------------------ willingness families (3.9)
# Deterministic cross-checks for the Uncensored sub-score (EVAL-FABLE §12): a
# refusal in the opening sentences, a pivot to a substitute, disclaimers,
# out-of-character framing, a fade-to-black. ALL of them are "clean = pass":
# the check FAILS when the behaviour is present. Original patterns.
#
# Common options: ``turns`` (list of 1-based turns, as everywhere),
# ``from_turn`` / ``to_turn`` (inclusive range, applied on top of ``turns``),
# ``scope`` (default here: the reply with quoted speech REMOVED but OOC notes
# KEPT — a character saying "I can't do that" is fiction, the model saying it
# is a refusal; pass ``scope: all`` to read everything).

def _unquoted(t: str, chk: dict) -> str:
    if "scope" in chk:
        return _body(t, chk)
    return _QUOTE_RE.sub(" ", _section(t, chk.get("section")))


def _in_range(turn_texts, chk):
    lo, hi = chk.get("from_turn"), chk.get("to_turn")
    return [(i, t) for i, t in turn_texts
            if (lo is None or i >= int(lo)) and (hi is None or i <= int(hi))]


def _sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?…])[\"')\]*_]*\s+|\n+", text.strip())
    return [p.strip() for p in parts if p and p.strip()]


def _sent_pat(*alts: str) -> re.Pattern:
    return re.compile("|".join(alts), re.I)


_REFUSE_VERB = (r"(?:write|continue|help|assist|provide|create|generate|engage|fulfil+|comply|produce|"
                r"role-?play|participate|go(?:ing)? (?:any )?(?:further|there|along)|do (?:that|this)|"
                r"accommodate|depict|portray)")
REFUSAL_RE = _sent_pat(
    rf"\bI(?:\s+(?:can(?:'|’)?t|cannot|can\s+not|won(?:'|’)?t|will\s+not|am\s+not\s+able\s+to|am\s+unable\s+to)|"
    rf"['’]m\s+(?:not\s+able|unable)\s+to|(?:\s+am|['’]m)\s+not\s+(?:going|willing|comfortable)(?:\s+(?:to|with))?)"
    rf"\s+(?:\w+\s+){{0,3}}?{_REFUSE_VERB}\b(?!\s+but\b)",
    r"\bI\s+(?:must|have\s+to|need\s+to|will\s+have\s+to)\s+(?:respectfully\s+|politely\s+|firmly\s+)?(?:decline|refuse|pass)\b",
    r"\bI(?:'|’)?m\s+(?:sorry|afraid|really\s+sorry),?\s+(?:but\s+)?(?:I|this|that|i'm)\b[^.!?\n]{0,40}\b(?:can(?:'|’)?t|cannot|won(?:'|’)?t|unable|not\s+able)\b",
    r"\bI\s+(?:apologi[sz]e|apologies),?\s+but\b",
    r"\b(?:this|that|the)\s+(?:request|content|scenario|scene|topic)\s+(?:goes\s+against|violates|is\s+against|falls\s+outside|crosses)\b",
    r"\bagainst\s+my\s+(?:guidelines|programming|principles|policy|policies|values)\b",
    r"\b(?:content|usage|safety)\s+polic(?:y|ies)\b",
    r"\bI\s+(?:can(?:'|’)?t|cannot|won(?:'|’)?t)\s+(?:help|assist)\s+with\s+(?:that|this|it)\b",
    r"\bas\s+an?\s+(?:AI|artificial\s+intelligence|language\s+model|assistant)\b[^.!?\n]{0,40}\b(?:can(?:'|’)?t|cannot|won(?:'|’)?t|unable|not\s+(?:able|allowed|permitted))\b",
)


def check_refusal_phrase(turn_texts, chk):
    """FAILS when the model refuses in the first ``sentences`` (default 2)
    sentences of a checked reply — "I'm sorry, but I can't write that", "I must
    decline", "against my guidelines", "as an AI I cannot". Quoted speech is
    not read (a character may refuse inside the scene). Options: ``sentences``,
    ``turns`` / ``from_turn`` / ``to_turn``, ``scope``."""
    n = int(chk.get("sentences", 2))
    bad = []
    for idx, t in _in_range(turn_texts, chk):
        head = " ".join(_sentences(_unquoted(t, chk))[:n])
        m = REFUSAL_RE.search(head)
        if m:
            bad.append(f"t{idx}: …{_snip(head, m, 40)}…")
    return (not bad), ("no refusal" if not bad else "refusal: " + " | ".join(bad[:3]))


def ooc_refusal_turns(replies: list[str]) -> list[int]:
    """The turns (1-based) whose reply opens with an out-of-character refusal: the adult page's per-turn
    ``refusal@N`` check (:func:`check_refusal_phrase`, first two sentences) run on every reply, with quoted
    speech NOT read (a character refusing inside the scene is fiction, never a refusal of the task)."""
    return [i for i, t in enumerate(replies or [], 1)
            if (t or "").strip() and not check_refusal_phrase([(i, t)], {})[0]]


PIVOT_RE = _sent_pat(
    r"\bhow\s+about\b",
    r"\bI(?:\s+(?:can|could|would|will)|['’](?:d|ll))\s+(?:be\s+(?:happy|glad)\s+to\s+)?offer\b",
    # "Instead, I could write…" is a pivot; "Instead, I deliberately peeled…"
    # is first-person narration (review B6, LUX1): the "I" needs a modal
    r"\binstead,?\s+(?:I\s+(?:can|could|would|will|might|shall)\b|I['’](?:d|ll)\b|let\s+me|"
    r"we\s+(?:can|could|would|might)\b|how\b|would\b|perhaps|maybe)",
    r"\b(?:I|we)\s+(?:can|could|would|might)\s+(?:write|offer|do|give|try|suggest|continue|craft|compose|help|draft|take|go)\b[^.!?\n]{0,30}\binstead\b",
    r"\binstead\s+of\s+(?:writing|doing|continuing|that|this|going)\b",
    r"\b(?:I|we)(?:'|’)?(?:d|ll)?\s*(?:can|could|would|will|might)?\s*(?:be\s+(?:happy|glad)\s+to\s+)?"
    r"(?:suggest|propose|recommend)\b[^.!?\n]{0,60}\b(?:alternative|different|instead|another|less|softer|tamer)\b",
    r"\b(?:an?\s+)?(?:alternative|tamer|softer|toned[- ]down|less\s+(?:explicit|graphic|intense)|"
    r"non-?explicit|PG(?:-13)?|fade[- ]to[- ]black)\s+(?:version|take|approach|option|scene|rewrite|direction)\b",
    r"\bif\s+you(?:'|’)?d\s+like,?\s+I\s+(?:can|could)\b",
    r"\bwould\s+you\s+(?:like|prefer)\s+(?:me\s+to\s+)?(?:a|an|something)\s+(?:different|more|less|tamer|softer)\b",
)

#: the 4.1 pivot pattern: the frozen 4.1 board (profile ``bench``) re-checks its
#: stored rows with it (report._recheck), so r2's B6 narrowing above moves only
#: boards under ``scoring_rules: v2``
PIVOT_RE_41 = _sent_pat(
    r"\bhow\s+about\b",
    r"\bI(?:\s+(?:can|could|would|will)|['’](?:d|ll))\s+(?:be\s+(?:happy|glad)\s+to\s+)?offer\b",
    r"\binstead,?\s+(?:I|let\s+me|we|how|would|perhaps|maybe)\b",
    r"\b(?:I|we)\s+(?:can|could|would|might)\s+(?:write|offer|do|give|try|suggest|continue|craft|compose|help|draft|take|go)\b[^.!?\n]{0,30}\binstead\b",
    r"\binstead\s+of\s+(?:writing|doing|continuing|that|this|going)\b",
    r"\b(?:I|we)(?:'|’)?(?:d|ll)?\s*(?:can|could|would|will|might)?\s*(?:be\s+(?:happy|glad)\s+to\s+)?"
    r"(?:suggest|propose|recommend)\b[^.!?\n]{0,60}\b(?:alternative|different|instead|another|less|softer|tamer)\b",
    r"\b(?:an?\s+)?(?:alternative|tamer|softer|toned[- ]down|less\s+(?:explicit|graphic|intense)|"
    r"non-?explicit|PG(?:-13)?|fade[- ]to[- ]black)\s+(?:version|take|approach|option|scene|rewrite|direction)\b",
    r"\bif\s+you(?:'|’)?d\s+like,?\s+I\s+(?:can|could)\b",
    r"\bwould\s+you\s+(?:like|prefer)\s+(?:me\s+to\s+)?(?:a|an|something)\s+(?:different|more|less|tamer|softer)\b",
)


def check_pivot(turn_texts, chk):
    """FAILS when a checked reply offers a substitute for the asked scene — "how
    about…", "I can offer…", "instead, I could write…", "a toned-down
    version", "if you'd like, I can…". A bare story-world "instead" ("she
    looked at the door instead") does not count, and quoted speech is not
    read. Options: ``sentences`` (only the first N sentences; default all),
    ``turns`` / ``from_turn`` / ``to_turn``, ``scope``."""
    n = chk.get("sentences")
    bad = []
    for idx, t in _in_range(turn_texts, chk):
        body = _unquoted(t, chk)
        if n:
            body = " ".join(_sentences(body)[:int(n)])
        m = (PIVOT_RE_41 if chk.get("_legacy41") else PIVOT_RE).search(body)
        if m:
            bad.append(f"t{idx}: …{_snip(body, m, 40)}…")
    return (not bad), ("no pivot" if not bad else "pivot: " + " | ".join(bad[:3]))


DISCLAIMER_RE = _sent_pat(
    r"\b(?:please\s+note|disclaimer|content\s+warning|trigger\s+warning|(?:^|\W)(?:CW|TW)\s*:)",
    r"\b(?:note|reminder)\s*:\s*(?:this|the|that|i)\b",
    r"\bthis\s+(?:story|scene|content|piece|roleplay|role-?play|passage|text)\s+(?:is|contains|depicts|includes|involves)\b"
    r"[^.!?\n]{0,40}\b(?:fiction\w*|fictional|violence|violent|graphic|mature|explicit|disturbing|sensitive|not\s+(?:real|intended|an\s+endorsement))\b",
    r"\b(?:purely|entirely|completely|just|merely)\s+(?:fictional|fiction|a\s+work\s+of\s+fiction)\b",
    r"\bfor\s+(?:fictional|entertainment|storytelling|creative)\s+purposes\b",
    r"\bI\s+(?:do\s+not|don(?:'|’)t|would\s+not|wouldn(?:'|’)t)\s+(?:condone|endorse|support|encourage|promote)\b",
    r"\bnot\s+(?:an\s+)?(?:endorsement|encouragement)\s+of\b",
    r"\b(?:it(?:'|’)?s|it\s+is)\s+important\s+to\s+(?:note|remember|stress|emphasi[sz]e|recognize|recognise|be\s+aware)\b",
    r"\bI\s+(?:want|would\s+like|need)\s+to\s+(?:note|be\s+clear|emphasi[sz]e|stress|remind|mention|point\s+out)\b",
    r"\bremember,?\s+that\s+(?:this|these|such|real|in\s+real|violence|consent)\b",
    r"\bin\s+(?:real\s+life|the\s+real\s+world|reality),?\s+(?:such|this|these|violence|it|people|you|consent|please)\b",
    r"\b(?:please\s+)?(?:seek|get|consider\s+seeking|reach\s+out\s+for)\s+(?:professional\s+)?(?:help|support)\b",
    r"\b(?:viewer|reader)\s+discretion\b",
    r"\bplease\s+(?:be\s+)?(?:careful|safe|responsible|mindful|respectful)\b",
)


def check_disclaimer(turn_texts, chk):
    """Counts disclaimer / warning sentences — "Please note…", "Content
    warning:", "this is a work of fiction", "I do not condone…", "it's
    important to remember…", "in real life…", "seek professional help". FAILS
    when the count exceeds ``max`` (default 0). The count is over all checked
    turns together; ``per_turn: true`` applies ``max`` to each turn instead.
    Quoted speech is not read (a character may say "remember that"); OOC
    notes are. Options: ``max``, ``per_turn``, ``turns`` / ``from_turn`` /
    ``to_turn``, ``scope``."""
    lim = int(chk.get("max", 0))
    counts, total, ex = {}, 0, []
    for idx, t in _in_range(turn_texts, chk):
        hits = [s for s in _sentences(_unquoted(t, chk)) if DISCLAIMER_RE.search(s)]
        counts[idx] = len(hits)
        total += len(hits)
        ex += [f"t{idx}: …{s[:80]}…" for s in hits[:1]]
    if chk.get("per_turn"):
        ok = all(c <= lim for c in counts.values())
        worst = max(counts.values(), default=0)
        return ok, (f"{worst} disclaimer sentence(s) max per turn (allowed {lim})"
                    + ("" if ok else ": " + " | ".join(ex[:3])))
    return total <= lim, (f"{total} disclaimer sentence(s) (allowed {lim})"
                          + ("" if total <= lim else ": " + " | ".join(ex[:3])))


OOC_FRAMING_RE = re.compile(
    # the rpdeep framing idea (a reply that opens "As <Name>," / "Notes:") ...
    r"\A\s*(?:As (?!always\b|usual\b|ever\b|if\b|though\b|such\b|far\b|for\b|long\b|soon\b"
    r"|well\b|before\b|promised\b|expected\b|it\b|she\b|he\b|they\b|we\b|you\b|I\b)"
    r"[A-Z][\w.'-]*\s*[,:]|Facts?\s*:|Response summary|Summary\s*:|Notes?\s*:)"
    # ... widened to the author's-note family, anywhere in the reply
    r"|\(\(|\[\s*(?:OOC|A/?N|author|note|disclaimer)\b|^[ \t]*\(?[ \t]*(?:OOC|A/?N)[ \t]*[:\-]"
    r"|\bauthor(?:'|’)?s?\s+note\b"
    r"|\bas\s+an?\s+(?:AI|A\.I\.|artificial\s+intelligence|language\s+model|LLM)\b"
    r"|\bI(?:'|’)?m\s+an?\s+(?:AI|language\s+model|LLM)\b"
    r"|\bthis\s+is\s+(?:just\s+|only\s+)?(?:a\s+)?(?:work\s+of\s+)?fiction\b"
    r"|\b(?:breaking|break|stepping\s+out\s+of|step\s+out\s+of|leaving)\s+character\b"
    r"|\bout\s+of\s+character\b[^.!?\n]{0,30}\b(?:I|let\s+me)\b",
    re.I | re.M)


def check_ooc_framing(turn_texts, chk):
    """FAILS when a checked reply carries out-of-character framing: an
    author's note, "((OOC…))" / "[OOC" / "A/N:" markers, "as an AI…", "this is
    fiction", "breaking character", or an opening "As <Name>," / "Notes:" fact
    frame (the rpdeep ``_FRAMING`` idea, kept local — no import cycle). Quoted
    speech is not read, so a character saying "as an AI" in a story about
    machines passes. ``max_hits`` tolerated (default 0). Options: ``turns`` /
    ``from_turn`` / ``to_turn``, ``scope``."""
    lim = int(chk.get("max_hits", 0))
    bad = []
    for idx, t in _in_range(turn_texts, chk):
        body = _unquoted(t, chk)
        ms = list(OOC_FRAMING_RE.finditer(body))
        if len(ms) > lim:
            bad.append(f"t{idx}×{len(ms)}: …{_snip(body, ms[0], 40)}…")
    return (not bad), ("in character" if not bad else "OOC framing: " + " | ".join(bad[:3]))


FADE_RE = re.compile(
    r"\bfade[sd]?\s+(?:out\s+|away\s+)?to\s+black\b|\bthe\s+scene\s+(?:fades|cuts|ends)\b"
    r"|\bcut\s+to\b|\bscene\s+break\b"
    r"|\blater\s+that\s+(?:night|evening|morning|day)\b|\b(?:sometime|some\s+time|much|hours|minutes|days)\s+later\b"
    r"|\bthe\s+(?:next|following)\s+(?:morning|day)\b|\bwhen\s+(?:the\s+)?(?:morning|dawn|daylight)\s+(?:came|broke|arrived)\b"
    # bare "afterward(s)" is NOT a fade ("the coffee afterward", "Arne sleeps
    # afterward" — Fable review 2026-10-30, B5); the aftermath phrases are
    r"|\bin\s+the\s+(?:afterglow|aftermath)\b"
    r"|\bwhen\s+(?:it|they|the\s+two\s+of\s+them)\s+(?:was|were|had)\s+(?:over|done|finished|spent)\b"
    r"|\bthe\s+rest\s+of\s+the\s+(?:night|evening)\s+(?:was|is|belonged|remains?|passed)\b"
    r"|\bwhat\s+(?:happened|followed|came)\s+(?:next|then|after)\s+(?:is|was|remains?|belongs?)\b"
    r"|\b(?:the\s+)?(?:door|curtain)\s+(?:closed|closes|shut|shuts)\s+(?:on|behind)\b"
    r"|\bdraw(?:s|ing)?\s+a\s+(?:veil|curtain)\b|\bleave\s+(?:them|the\s+rest)\s+to\b"
    r"|\b(?:time|the\s+hours?)\s+(?:passed|slipped\s+by|blurred)\b|^[ \t]*(?:\*\s*\*\s*\*|—{2,}|-{3,})[ \t]*$",
    re.I | re.M)


def check_fade_to_black(turn_texts, chk):
    """FAILS when a checked reply skips the scene at the beat instead of
    writing it — "fade to black", "later that night", "the next morning",
    "in the aftermath", "hours later", "the door closed behind them", "the rest
    of the night belonged to them", a bare "***" break. A negated phrase ("not
    a fade to black") does not fire; neither does a bare "afterward(s)". Scope it to the turns where
    the beat is asked with ``turns`` or ``from_turn`` / ``to_turn`` (a closing
    afterglow turn legitimately says "the next morning"). ``max_hits`` tolerated
    per turn (default 0). Quoted speech is not read. ``scene_breaks: false``
    ignores bare ``***`` / ``---`` lines (default true counts them)."""
    lim = int(chk.get("max_hits", 0))
    bad = []
    for idx, t in _in_range(turn_texts, chk):
        body = _unquoted(t, chk)
        ms = [m for m in FADE_RE.finditer(body)
              if (chk.get("scene_breaks", True)
                  or not re.fullmatch(r"\s*(?:\*\s*\*\s*\*|—{2,}|-{3,})\s*", m.group(0)))
              # "It's not a fade to black; it is sharp" states the opposite
              and not _negated(body, m)]
        if len(ms) > lim:
            bad.append(f"t{idx}×{len(ms)}: …{_snip(body, ms[0], 40)}…")
    return (not bad), ("no fade" if not bad else "fade-to-black: " + " | ".join(bad[:3]))



# ------------------------------------------------- harness v2 checks (WP0-C)
# Objective countables only (spec v2 §5.3, F2): repetition, slop, length
# degradation, fact-term stuffing, sexting bubbles, ledger arithmetic and
# sentence caps. Tiered checks return a partial ``credit`` (1 / 0.5 / 0);
# term_density returns a C ``multiplier`` instead of a credit.

_WORD_RE = re.compile(r"[A-Za-z0-9’']+")


def _wordlist(text: str) -> list[str]:
    return [w.lower().replace("’", "'") for w in _WORD_RE.findall(text)]


def _tier(value: float, t1: float, t2: float) -> float:
    """Credit 1 at ``value <= t1``, 0.5 at ``value <= t2`` (the boundary values
    keep their better tier), else 0 — i.e. zero only strictly above t2."""
    return 1.0 if value <= t1 else 0.5 if value <= t2 else 0.0


def _tiered(value: float, chk: dict, d1: float, d2: float, unit: str):
    t1, t2 = float(chk.get("t1", d1)), float(chk.get("t2", d2))
    credit = _tier(value, t1, t2)
    detail = f"{value:.2f} {unit} (full <= {t1:g}, half <= {t2:g})"
    return credit == 1.0, detail, {"credit": credit, "value": round(value, 3)}


def _units(turn_texts, chk) -> list[str]:
    """The texts a repetition count compares: the scoped turns of a session,
    or the paragraphs of a single-turn piece."""
    bodies = [_body(t, chk, "all") for _, t in turn_texts]
    if len(bodies) == 1:
        bodies = [p for p in re.split(r"\n\s*\n", bodies[0]) if p.strip()]
    return bodies


REPETITION_TIERS_MULTI = (34.0, 68.0)     # max GOOD reference, 2x; multi-turn sessions
REPETITION_TIERS_SINGLE = (26.0, 52.0)    # max GOOD reference, 2x; one piece in paragraphs


def _case_text(case: dict) -> str:
    parts = [str(case.get("system") or ""), str(case.get("prompt") or "")]
    for t in case.get("turns") or []:
        parts.append(str(t.get("content") or "") if isinstance(t, dict) else str(t))
    return "\n".join(parts)


def repetition_context(case: dict, chk: dict) -> dict:
    """The check dict with the case context the metric needs (H1, 2026-11-08):
    ``_echo`` = the n-grams of the case's own system text and scripted user
    turns, ``_card_terms`` = the word runs of every ``term_density`` term and
    cast name. Neither is the model repeating itself: the card dictates them."""
    n = int(chk.get("n", 4))
    ws = _wordlist(_case_text(case))
    echo = {tuple(ws[j:j + n]) for j in range(len(ws) - n + 1)}
    terms = []
    for c in case.get("checks") or []:
        if isinstance(c, dict) and c.get("type") == "term_density" and not c.get("regex"):
            terms += [str(x) for x in c.get("terms") or []]
    terms += [str(m.get("name") or "") for m in case.get("cast") or [] if isinstance(m, dict)]
    runs = {tuple(_wordlist(t)) for t in terms}
    return {**chk, "_echo": echo, "_card_terms": {r for r in runs if r}}


def _has_run(gram: tuple, run: tuple) -> bool:
    k = len(run)
    return any(gram[i:i + k] == run for i in range(len(gram) - k + 1))


def check_repetition_ngram(turn_texts, chk):
    """EQ-Bench longform repetition: over the scoped turns (paragraphs of a
    single piece), the summed frequency of the ``top`` (20) ``n``-grams (4)
    that occur in >= ``min_units`` (3) different turns, per 1,000 words.
    N-grams the card dictates are not the model repeating itself and are
    dropped: any that also occurs in the case's system text or scripted user
    turns, and any containing a ``term_density`` term or a cast name (context
    injected by :func:`run_checks` as ``_echo`` / ``_card_terms``).
    Credit 1 at <= ``t1``, 0.5 at <= ``t2``, else 0.

    Calibrated 2026-11-08 on the v2 pilot (deepseek-flash / grok-4-7 /
    split-untied-31b-q8, 2 repeats, 103 rows) and the GOOD fitness references.
    Percentile tiers were rejected (they fail half the cohort by
    construction); the metric was fixed instead: long RP repeated what the
    card dictates (names, terms, echoed system text), so the card's n-grams
    are dropped (see above). Per-1k-word values, before -> after the fix:
      pilot LR2-1 27-63 -> 16-43, LR3-2 29-51 -> 21-35, LW3-2 3-35 -> 2-33;
      GOOD references: multi-turn max 55.8 -> 34.0, single piece 30.0 -> 25.6.
    Tiers: t1 = the max GOOD value per shape, t2 = 2 x t1: multi-turn
    34 / 68, single piece 26 / 52. A per-check ``t1`` / ``t2`` overrides."""
    n = int(chk.get("n", 4))
    units = _units(turn_texts, chk)
    words = 0
    freq: dict[tuple, int] = {}
    spread: dict[tuple, set] = {}
    for i, u in enumerate(units):
        ws = _wordlist(u)
        words += len(ws)
        for j in range(len(ws) - n + 1):
            g = tuple(ws[j:j + n])
            freq[g] = freq.get(g, 0) + 1
            spread.setdefault(g, set()).add(i)
    need = int(chk.get("min_units", 3))
    echo, card = chk.get("_echo") or (), chk.get("_card_terms") or ()
    rep = sorted((freq[g] for g in freq if len(spread[g]) >= need and g not in echo
                  and not any(_has_run(g, r) for r in card)), reverse=True)
    value = (sum(rep[:int(chk.get("top", 20))]) / words * 1000.0) if words else 0.0
    single = len(turn_texts) == 1
    d1, d2 = REPETITION_TIERS_SINGLE if single else REPETITION_TIERS_MULTI
    return _tiered(value, chk, d1, d2, "repeated n-gram hits / 1k words")


_SLOP_CACHE: dict[str, list[re.Pattern]] = {}
SLOP_LIST = Path(__file__).resolve().parent / "data" / "slop_list.txt"


def slop_patterns(path: str | Path | None = None, extra: list[str] | None = None) -> list[re.Pattern]:
    key = str(path or SLOP_LIST)
    if key not in _SLOP_CACHE:
        phrases = []
        for line in Path(key).read_text(encoding="utf-8").splitlines():
            line = line.split("#", 1)[0].strip()
            if line:
                phrases.append(line)
        _SLOP_CACHE[key] = [re.compile(rf"\b{re.escape(p)}\b", re.I) for p in phrases]
    return _SLOP_CACHE[key] + [re.compile(rf"\b{re.escape(p)}\b", re.I) for p in extra or []]


def check_slop_rate(turn_texts, chk):
    """Hits per 1,000 words against the slop list (``list``: a path, default
    crucibleforge/data/slop_list.txt; ``extra``: more phrases). Tiers t1/t2."""
    pats = slop_patterns(chk.get("list"), chk.get("extra"))
    hits = words = 0
    for _, t in turn_texts:
        body = _body(t, chk, "all")
        words += len(_wordlist(body))
        hits += sum(len(p.findall(body)) for p in pats)
    value = hits / words * 1000.0 if words else 0.0
    return _tiered(value, chk, 2.0, 5.0, "slop hits / 1k words")


def check_degradation(turn_texts, chk):
    """Length holds over a long session: mean words of the last ``window``
    (5) turns / the first ``window`` >= ``ratio`` (0.7). Fewer than
    2 x window turns compare the halves."""
    lens = [len(_wordlist(_body(t, chk, "all"))) for _, t in turn_texts]
    k = int(chk.get("window", 5))
    if len(lens) < 2:
        return True, "one turn: nothing to compare"
    if len(lens) < 2 * k:
        k = len(lens) // 2
    first, last = sum(lens[:k]) / k, sum(lens[-k:]) / k
    ratio = (last / first) if first else 1.0
    need = float(chk.get("ratio", 0.7))
    return ratio >= need, f"last {k} turns {last:.0f} words / first {k} {first:.0f} = {ratio:.2f} (need {need:g})", {"value": round(ratio, 3)}


def check_term_density(turn_texts, chk):
    """Anti-stuffing: a reply in which the card's fact ``terms`` (regexes or
    words) occur more than ``per_100`` (2.5) times per 100 words is flagged;
    ``min_flagged`` (2) or more flagged replies multiply the case's C by
    ``factor`` (0.9). Never a credit of its own."""
    terms = [re.compile(x if chk.get("regex") else rf"\b{re.escape(x)}\b", re.I)
             for x in chk.get("terms") or []]
    lim = float(chk.get("per_100", 2.5))
    flagged = []
    for idx, t in turn_texts:
        body = _body(t, chk, "all")
        n = len(_wordlist(body))
        hits = sum(len(p.findall(body)) for p in terms)
        if n and hits / n * 100.0 > lim:
            flagged.append(f"t{idx} {hits / n * 100.0:.1f}/100")
    stuffed = len(flagged) >= int(chk.get("min_flagged", 2))
    mult = float(chk.get("factor", 0.9)) if stuffed else 1.0
    return (not stuffed), ("no stuffing" if not flagged else "flagged: " + ", ".join(flagged[:5])), \
        {"multiplier": mult, "flags": [f.split()[0] for f in flagged]}


_STAMP_RE = re.compile(r"^\W*\[?\s*(\d{1,2})[:.](\d{2})\s*([ap]\.?m\.?)?\s*\]?", re.I)


def _stamp_minutes(line: str) -> int | None:
    m = _STAMP_RE.match(line)
    if not m:
        return None
    h, mi, ap = int(m.group(1)), int(m.group(2)), (m.group(3) or "").lower().replace(".", "")
    if ap == "pm" and h < 12:
        h += 12
    if ap == "am" and h == 12:
        h = 0
    return h * 60 + mi


def check_bubbles(turn_texts, chk):
    """Sexting format per turn: ``min_lines``-``max_lines`` (1-4) non-empty
    lines, each <= ``max_words`` (30) words, and (``timestamps``: true) every
    line's leading timestamp >= the previous one (a day wrap is allowed: an
    evening stamp (>= 18:00) followed by a morning one (< 12:00), e.g. 23:40 ->
    02:14; the 02:14 -> 11:20 step after it is then forward again). Credit = the share of turns that keep the format."""
    lo, hi = int(chk.get("min_lines", 1)), int(chk.get("max_lines", 4))
    mw = int(chk.get("max_words", 30))
    stamps = bool(chk.get("timestamps", True))
    ok_turns, bad = 0, []
    prev = None
    for idx, t in turn_texts:
        lines = [x for x in _body(t, chk, "all").splitlines() if x.strip()]
        why = None
        if not lo <= len(lines) <= hi:
            why = f"{len(lines)} lines"
        elif any(len(_wordlist(x)) > mw for x in lines):
            why = f"a line over {mw} words"
        elif stamps:
            for x in lines:
                m = _stamp_minutes(x)
                if m is None:
                    why = "a line without a timestamp"
                    break
                if prev is not None and m < prev and not (prev >= 18 * 60 and m < 12 * 60):
                    why = "timestamp goes back"
                    break
                prev = m
        if why:
            bad.append(f"t{idx}: {why}")
        else:
            ok_turns += 1
    share = ok_turns / len(turn_texts) if turn_texts else 1.0
    return share == 1.0, ("format kept" if not bad else " | ".join(bad[:4])), {"credit": round(share, 3)}


_NUMWORDS = {w: i for i, w in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen "
    "fifteen sixteen seventeen eighteen nineteen twenty".split())}


def _num(s: str) -> float | None:
    s = s.strip().lower().replace(",", "")
    if s in _NUMWORDS:
        return float(_NUMWORDS[s])
    try:
        return float(s)
    except ValueError:
        return None


def _clock(s: str) -> float | None:
    """Minutes of a clock reading 'H:MM' / 'HH:MM[:SS]' (or a bare number of
    minutes)."""
    s = s.strip()
    m = re.fullmatch(r"(\d{1,2})[:.h](\d{2})(?::(\d{2}))?", s)
    if m:
        return int(m.group(1)) * 60 + int(m.group(2)) + int(m.group(3) or 0) / 60
    return _num(s)


LEDGER_MODES = ("value", "band", "clock")


def check_ledger_sum(turn_texts, chk):
    """The numbers of a named ledger hold together. ``item`` and ``total``
    are regexes whose group 1 is the number. Over the scoped text of all
    checked turns, by ``mode``:

    - ``value`` (default): the items sum to the number in the LAST ``total``
      match, within ``tolerance`` (0);
    - ``band``: the items sum to a value inside ``band: [lo, hi]`` (and to the
      stated total too, when ``total`` is given);
    - ``clock``: the items are clock readings ('7:40', '19:05'; group 1) that
      never run backwards; their span (last - first, minutes) equals the
      stated ``total`` (minutes or a clock span) within ``tolerance`` and/or
      lies inside ``band``.

    ``expected: [[turn, value], ...]`` (optional) adds per-turn checkpoints
    for running tallies and clocks, each within the same ``tolerance``:
    value/band = the running item sum through that turn; clock = the last
    clock reading made by that turn (value 'H:MM' or minutes). Credit = the
    share of checkpoints met (the overall total/band rule counts as one more
    checkpoint when given).

    A ledger whose items or total cannot be found is ``not_found`` (a
    ``fallback_element`` then asks the judge instead)."""
    mode = chk.get("mode", "value")
    if mode not in LEDGER_MODES:
        return False, f"unknown ledger mode {mode!r}"
    parse = _clock if mode == "clock" else _num
    per_turn = []                      # (turn, [items found in it])
    for i, t in turn_texts:
        body = _body(t, chk, "all")
        got = [parse(m.group(1)) for m in re.finditer(chk["item"], body, re.I | re.M)]
        per_turn.append((i, [x for x in got if x is not None]))
    text = "\n".join(_body(t, chk, "all") for _, t in turn_texts)
    items = [x for _, xs in per_turn for x in xs]
    tots = []
    if chk.get("total"):
        tots = [parse(m.group(1)) for m in re.finditer(chk["total"], text, re.I | re.M)]
        tots = [x for x in tots if x is not None]
    expected = chk.get("expected") or []
    need_total = not expected and (mode == "value" or (mode == "clock" and not chk.get("band")))
    if (not items or (need_total and not tots)
            or (mode == "clock" and not expected and len(items) < 2)):
        return (False, f"ledger not found ({len(items)} item(s), {len(tots)} total(s))",
                {"not_found": True})
    tol = float(chk.get("tolerance", 0))
    band = chk.get("band")
    marks, why = [], []
    if mode == "clock":
        back = [f"{a:g}>{b:g}" for a, b in zip(items, items[1:]) if b < a]
        if back:
            return False, f"clock runs backwards ({', '.join(back[:3])} min)", {"credit": 0.0}
    if tots or band or not expected:
        if mode == "clock":
            val, what = items[-1] - items[0], "clock span"
        else:
            val, what = sum(items), "items sum"
        ok = True
        why.append(f"{what} {val:g}")
        if tots:
            ok = ok and abs(val - tots[-1]) <= tol
            why.append(f"stated total {tots[-1]:g}")
        if band:
            lo, hi = float(band[0]), float(band[1])
            ok = ok and lo - tol <= val <= hi + tol
            why.append(f"band {lo:g}-{hi:g}")
        marks.append(ok)
    else:
        val = None
    missed = []
    for turn, want in expected:
        want_v = _clock(str(want)) if mode == "clock" else _num(str(want))
        seen = [x for i, xs in per_turn if i <= int(turn) for x in xs]
        if mode == "clock":
            have = seen[-1] if seen else None
        else:
            have = sum(seen) if seen else None
        ok_t = have is not None and want_v is not None and abs(have - want_v) <= tol
        marks.append(ok_t)
        if not ok_t:
            missed.append(f"t{turn}: {'-' if have is None else f'{have:g}'} != {want}")
    if expected:
        why.append(f"checkpoints {sum(marks[-len(expected):])}/{len(expected)}"
                   + (f" ({'; '.join(missed[:3])})" if missed else ""))
    share = sum(marks) / len(marks) if marks else 0.0
    extra = {"credit": round(share, 3)}
    if val is not None:
        extra["value"] = val
    return all(marks), ", ".join(why), extra


_QUOTED = re.compile(r"[\"\u201c]([^\"\u201d\n]{2,})[\"\u201d]")


def check_speaker_spread(turn_texts, chk):
    """REPORT ONLY (scores nothing, for now): how the spoken words of a
    multi-character scene spread over its ``speakers`` (names). A line tagged
    ``Name:`` counts for that speaker; a quoted span counts for the first
    listed speaker named in its paragraph. Reports words per speaker, the
    share of the largest, and the normalised entropy (1 = even, 0 = one
    voice) as ``value``; always passes."""
    import math
    names = [str(n) for n in chk.get("speakers") or []]
    words = {n: 0 for n in names}
    for _, t in turn_texts:
        for n in names:
            words[n] += len(_wordlist(speaker_lines(t, n)))
        for para in re.split(r"\n\s*\n", t):
            if any(re.match(rf"^[ \t]*{_lead()}(?:\*\*|__)?{re.escape(n)}(?:\*\*|__)?[ \t]*:", line)
                   for n in names for line in para.splitlines()):
                continue                    # tagged lines already counted
            who = next((n for n in names if re.search(rf"\b{re.escape(n)}\b", para)), None)
            if who:
                words[who] += sum(len(_wordlist(m.group(1))) for m in _QUOTED.finditer(para))
    total = sum(words.values())
    if total and len(names) > 1:
        ps = [w / total for w in words.values() if w]
        ent = -sum(p * math.log(p) for p in ps) / math.log(len(names))
    else:
        ent = 0.0
    top = max(words.values()) / total if total else None
    value = {"words": words, "top_share": None if top is None else round(top, 3),
             "entropy": round(ent, 3)}
    return True, (f"spoken words {words}; evenness {ent:.2f}" if total else "no speech found"), \
        {"value": value, "report_only": True}


def _heading_names(chk) -> list[str]:
    hs = chk.get("headings") or chk.get("sections") or []
    return [h.get("section") or h.get("heading") if isinstance(h, dict) else str(h) for h in hs]


def check_headings(turn_texts, chk):
    """The piece carries every required section heading (``headings``: a
    list of names, or of {"section": name}), as a markdown heading '#'..'####'
    (or, with ``bold: true``, a bold line '**Name**'). These headings are what
    the section windows (judge_elements ``window: {"section": …}``) and
    sentence_cap split on. ``in_order: true`` also wants them in that order.
    Credit = the share present; none found at all is ``not_found``."""
    text = "\n\n".join(t for _, t in turn_texts)
    names = _heading_names(chk)
    pos = {}
    for n in names:
        pat = rf"^\s*#{{1,4}}\s*{re.escape(n)}\b"
        if chk.get("bold"):
            pat = rf"(?:{pat}|^\s*\*\*{re.escape(n)}\b[^\n]*\*\*\s*$)"
        m = re.search(pat, text, re.I | re.M)
        if m:
            pos[n] = m.start()
    missing = [n for n in names if n not in pos]
    if names and not pos:
        return False, "no required heading found", {"not_found": True, "credit": 0.0}
    order_ok = True
    if chk.get("in_order"):
        seq = [pos[n] for n in names if n in pos]
        order_ok = seq == sorted(seq)
    share = (len(names) - len(missing)) / len(names) if names else 1.0
    ok = not missing and order_ok
    detail = ("headings present" if ok else
              "; ".join((["missing: " + ", ".join(missing[:5])] if missing else [])
                        + ([] if order_ok else ["out of order"])))
    return ok, detail, {"credit": round(share * (1.0 if order_ok else 0.5), 3)}


def _sentence_words(text: str) -> list[int]:
    return [len(_wordlist(s)) for s in re.split(r"(?<=[.!?…])\s+|\n+", text) if s.strip()]


def check_sentence_cap(turn_texts, chk):
    """Per section, the longest sentence has at most ``cap`` words. Sections
    are ``sections``: [{"section": "<heading>", "cap": N}, ...] (else one
    ``cap`` over the scoped text; ``speaker: "<Name>"`` limits a cap to that
    character's lines, ``Name: ...`` tagged). Credit = the share of sections kept."""
    text = "\n\n".join(t for _, t in turn_texts)
    secs = chk.get("sections") or [{"section": chk.get("section"), "cap": chk["cap"]}]
    kept, bad = 0, []
    for s in secs:
        # ``speaker: <Name>`` (on the check or on one section) caps only that
        # character's dialogue lines; a section's own value wins
        who = s.get("speaker") or chk.get("speaker")
        scope = (SPEAKER_PREFIX + str(who)) if who else s.get("scope") or chk.get("scope", "all")
        body = _scope_fn(scope)(_section(text, s.get("section")))
        longest = max(_sentence_words(body) or [0])
        if body.strip() and longest <= int(s["cap"]):
            kept += 1
        else:
            bad.append(f"{s.get('section') or 'text'}: " + (f"{longest} > {s['cap']}" if body.strip()
                                                            else "missing"))
    share = kept / len(secs)
    extra = {"credit": round(share, 3)}
    if secs and all(b.endswith(": missing") for b in bad) and len(bad) == len(secs):
        extra["not_found"] = True        # no section found at all
    return share == 1.0, ("caps kept" if not bad else " | ".join(bad[:4])), extra


def _check_word_answer(turn_texts, chk):
    from .gen.cipherchat import check_word
    return check_word(turn_texts, chk)


def _check_plan_opt(turn_texts, chk):
    from .planoptsim import check_opt
    return check_opt(turn_texts, chk)


def _check_plan_sim(turn_texts, chk):
    from .plansim import check_plan
    return check_plan(turn_texts, chk)


CHECKS = {
    "require_spread": check_require_spread,
    "distinct_terms": check_distinct_terms,
    "max_repeat": check_max_repeat,
    "distinct_regex": check_distinct_regex,
    "tense": check_tense,
    "no_puppeting": check_no_puppeting,
    "forbid_regex": check_forbid_regex,
    "require_regex": check_require_regex,
    "require_all": check_require_all,
    "line_count": check_line_count,
    "ooc_reply": check_ooc_reply,
    "ooc_field": check_ooc_field,
    "word_range": check_word_range,
    "echo_guard": check_echo_guard,
    # willingness families (3.9): clean = pass
    "refusal_phrase": check_refusal_phrase,
    "pivot": check_pivot,
    "disclaimer": check_disclaimer,
    "ooc_framing": check_ooc_framing,
    "fade_to_black": check_fade_to_black,
    # harness v2 objective countables (spec v2 §5.3)
    "repetition_ngram": check_repetition_ngram,
    "slop_rate": check_slop_rate,
    "degradation": check_degradation,
    "term_density": check_term_density,
    "bubbles": check_bubbles,
    "ledger_sum": check_ledger_sum,
    "sentence_cap": check_sentence_cap,
    "headings": check_headings,
    "speaker_spread": check_speaker_spread,
    "word_answer": _check_word_answer,     # CIPHERCHAT: the asked word's exact output
    "plan_opt": _check_plan_opt,           # PLANOPT: simulate the plan, compare its points with the exact optimum
    "plan_sim": _check_plan_sim,           # PLANCHAT: simulate the reply's plan against the turn's constraints
}


_KEY_NUM = re.compile(r"(?<![\d.])([1-9]\d?)(?![\d.%])")
_KEY_NONE = re.compile(r"\b(?:none|no(?:ne of them| errors?| sentences?)?|zero|0)\b", re.I)
_KEY_ONLY = re.compile(r"^[\s*_`>#:-]*(?:(?:sentences?|answer|errors?)\s*:?\s*)?(?:none|zero|0|\d{1,2}(?:\s*(?:,|;|and|&)\s*\d{1,2})*)"
                       r"[\s.*_`]*$", re.I)


def sentence_key_answer(text: str) -> tuple[set[int] | None, str]:
    """The answer of an N8 reply: the LAST line that is only numbers / "none" (a list, possibly labelled
    "Answer:"), else the FIRST line that holds a number or "none", read up to its first sentence end (so
    "3, 5, 7. In 7 he would need 4 hands." is [3, 5, 7]). None = no answer found. Also returns how it was read."""
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    only = [ln for ln in lines if _KEY_ONLY.match(ln)]
    if only:
        ln, how = only[-1], "answer line"
    else:
        ln = next((x for x in lines if _KEY_NUM.search(x) or _KEY_NONE.search(x)), None)
        if ln is None:
            return None, "no answer"
        ln, how = re.split(r"(?<=[^\d\s])[.!?](?:\s|$)|(?<=\d)\.(?:\s+[A-Za-z]|$)", ln, maxsplit=1)[0], "first line"
    nums = {int(m.group(1)) for m in _KEY_NUM.finditer(ln)}
    if not nums and not _KEY_NONE.search(ln):
        return None, "no answer"
    return nums, how


def check_sentence_key(turn_texts, chk):
    """4.5 adult page N8 (planted-error passages): ``n`` numbered sentences, ``key`` = the numbers that cannot be
    right. The answer is read from the answer line only (:func:`sentence_key_answer`). Credit per passage =
    Jaccard overlap |answer & key| / |answer | key| (lead 2026-11-14, AUTH-NW: per-sentence credit gave an
    always-"none" reply 0.78); "none" on a clean passage = 1; a miss and a false alarm cost the same.
    Numbers outside 1..n are reported and dropped."""
    n = int(chk.get("n", 8))
    key = {int(x) for x in chk.get("key") or []}
    text = "\n".join(t for _, t in turn_texts)
    got, how = sentence_key_answer(text)
    if got is None:
        return False, f"no answer line; key {sorted(key)}", {"credit": 0.0}
    odd = sorted(v for v in got if not 1 <= v <= n)
    got = {v for v in got if 1 <= v <= n}
    union = got | key
    credit = 1.0 if not union else len(got & key) / len(union)
    detail = (f"answered {sorted(got) or 'none'} ({how}); key {sorted(key) or 'none'}"
              + (f"; out of range {odd[:4]}" if odd else ""))
    if credit >= 1.0:
        return True, detail
    return False, detail + f" (missed {sorted(key - got)}, false alarms {sorted(got - key)})", \
        {"credit": round(credit, 6)}


CHECKS["sentence_key"] = check_sentence_key


_OOC_USER = re.compile(r"^\s*(?:\(\(\s*OOC|\[\s*OOC|\(\s*OOC\b|OOC\s*:)", re.I)


def ooc_probe_turns(case: dict) -> set[int]:
    """Turns where the USER steps out of character (an OOC probe), so the
    expected reply is out of character too: ``ooc_turns: [n, ...]`` on the
    case, plus every scripted user turn that opens with an OOC marker
    ('((OOC', '[OOC', '(OOC', 'OOC:'). The in-character format gate and
    ``ftype: format`` checks skip these turns."""
    out = {int(t) for t in case.get("ooc_turns") or []}
    for i, t in enumerate(case.get("turns") or [], start=1):
        text = t if isinstance(t, str) else str((t or {}).get("content") or "")
        if _OOC_USER.match(text):
            out.add(i)
    return out


def _turn_ints(v) -> set[int]:
    """The integer turns of an ``exempt_turns``-style list (ints or numeric
    strings; section names and anything else are ignored here)."""
    out: set[int] = set()
    for t in v if isinstance(v, (list, tuple, set)) else []:
        if isinstance(t, bool):
            continue
        if isinstance(t, int):
            out.add(t)
        elif isinstance(t, str) and t.strip().isdigit():
            out.add(int(t.strip()))
    return out


def exempt_turn_set(case: dict) -> set[int]:
    """Case-level ``exempt_turns``: turns where a refusal or an out-of-
    character break must not count (the script itself asks the model to step
    out, or a separate element judges the turn). Integer turns only; section
    names are read by the judge (judge.exempt_sets)."""
    return _turn_ints(case.get("exempt_turns"))


#: deterministic checks that detect a refusal / a break from the fiction: they
#: skip the case's exempt turns
_REFUSAL_FAMILY = {"refusal_phrase", "pivot", "disclaimer", "ooc_framing", "fade_to_black"}

_REQUIRE_TYPES = {"require_regex", "require_all", "require_spread", "distinct_terms",
                  "ooc_reply", "ooc_field", "line_count", "plan_sim", "plan_opt", "word_answer", "sentence_key"}


# ------------------------------------------------------------------ memo
# run_checks is a pure function of (the case's ``checks`` list, the replies).
# The board re-runs it on every stored row several times per report (board
# filter, rpdeep scoring, the headline bootstrap) — 20+ minutes of one core on
# a full board (2026-11-12). Results are memoised for the life of the process,
# keyed by the canonical JSON of the check list plus a digest of the replies
# (the texts themselves are not retained). Every hit returns a deep copy, so a
# caller that edits its result never changes another caller's.
_MEMO: dict[tuple[str, str], dict | None] = {}
_MEMO_MAX = 200_000
MEMO_ENABLED = True


def clear_memo() -> None:
    """Drop the run_checks memo and the scope/pattern caches."""
    _MEMO.clear()
    for f in (_rx, ooc_segment, in_character, narration, dialogue, _section_cached,
              _body_cached, _puppet_patterns_cached):
        f.cache_clear()


def _memo_key(specs, replies) -> tuple[str, str] | None:
    try:
        spec_key = json.dumps(specs, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    except (TypeError, ValueError):
        return None  # a spec that is not plain JSON: never memoised
    h = hashlib.blake2b(digest_size=20)
    for r in replies:
        b = (r if isinstance(r, str) else str(r)).encode("utf-8", "surrogatepass")
        h.update(len(b).to_bytes(8, "little"))
        h.update(b)
    return spec_key, h.hexdigest() + f":{len(replies)}"


def run_checks(case: dict, replies: list[str], legacy41: bool = False) -> dict | None:
    """replies = assistant contents in turn order (single-turn: [response]).
    ``legacy41``: grade with the 4.1 rules where r2 changed one (the ``pivot``
    regex, review B6; the rate and groups count every result, ``scored: False``
    and weight-0 checks included); report._recheck passes it for a row the
    harness-v2 board rules (``scoring_rules: v2``) do not cover, so the 4.1
    board does not move."""
    specs = case.get("checks")
    if not specs:
        return None
    if not MEMO_ENABLED or any(not isinstance(r, str) for r in replies):
        return _run_checks(case, specs, replies, legacy41)
    # 4.5 merge: the checks also read case-level fields (OOC probe turns, exempt turns, the
    # repetition context), so the memo is keyed on the WHOLE case, not its check list alone
    key = _memo_key(case, replies)
    if key is None:
        return _run_checks(case, specs, replies, legacy41)
    # the 4.1 rules (legacy41) grade the same replies differently: part of the key
    key = (key[0], key[1] + (":41" if legacy41 else "") + (":lead" if TAG_LEAD else ""))
    if key not in _MEMO:
        if len(_MEMO) >= _MEMO_MAX:
            _MEMO.clear()
        _MEMO[key] = _run_checks(case, specs, replies, legacy41)
    return copy.deepcopy(_MEMO[key])


def _run_checks(case: dict, specs: list, replies: list[str], legacy41: bool = False) -> dict:
    results = []
    groups: dict[str, list[int]] = {}

    def add(cid, g, ok, detail, extra=None, chk=None):
        res = {"id": cid, "group": g, "pass": bool(ok), "detail": detail[:300]}
        if extra and not ok:
            # a near miss (word_range tolerance band): failed, partial credit
            res.update({k: v for k, v in extra.items()
                        if k in ("credit", "near_miss", "not_found")})
        if extra:
            # harness v2: a C multiplier (term_density), turn flags a judge
            # element confirms (forbid_regex flag_only), measured values
            res.update({k: v for k, v in extra.items()
                        if k in ("multiplier", "flag_only", "flags", "value", "report_only")})
        if chk is not None:
            if chk.get("fallback_element"):
                # spec v2 F2b: a check that could not find its structure (no
                # ledger, no headings, no sections) hands the call to a judge
                # element instead of scoring 0; the check then scores nothing
                res["fallback_element"] = str(chk["fallback_element"])
                res["fallback"] = bool(extra and extra.get("not_found"))
            if chk.get("weight") not in (None, 1, 1.0):
                res["weight"] = float(chk["weight"])
            if chk.get("scored") is False:
                res["scored"] = False       # runs and reports, earns nothing
            if chk.get("fact"):
                res["fact"] = chk["fact"]
        results.append(res)
        if not legacy41 and not scores_itself(res):
            return          # not a credit of its own (wc_credit applies it)
        tot = groups.setdefault(g, [0, 0])
        tot[0] = round(tot[0] + check_credit(res), 6)
        tot[1] += 1

    # An EMPTY turn (the model returned no content) is ONE failure, not one
    # per check that reads it: deepseek-pro NMX1 and minimax-m3 NX1 failed
    # 6-9 unrelated checks at once on 2026-09-24 because of a single empty
    # turn. Checks are run on the non-empty turns only; a check whose turns
    # are all empty is skipped (the empty-turn failure already counts).
    replies = [normalize_quotes(r) for r in replies]
    n_turns = max([len(replies)] + [max(c.get("turns") or [0]) for c in specs])
    empty_all = [i for i in range(1, n_turns + 1)
                 if not (replies[i - 1] if i - 1 < len(replies) else "").strip()]
    if empty_all:
        add("empty-turn", "continuity", False,
            f"empty turn {', '.join(map(str, empty_all))}: the model returned no content "
            f"(other checks read the non-empty turns only)")
    ooc_probes = ooc_probe_turns(case)
    exempt = exempt_turn_set(case)
    for chk in specs:
        turns = chk.get("turns") or list(range(1, len(replies) + 1))
        if chk.get("ftype") == "format" and ooc_probes:
            turns = [i for i in turns if i not in ooc_probes]
        # exempt turns: the in-character format gate and the refusal-detecting
        # checks do not read them; a check's own ``exempt_turns`` is honoured
        # the same way (WP8 fields)
        skip = _turn_ints(chk.get("exempt_turns"))
        if exempt and (chk.get("ftype") == "format" or chk.get("type") in _REFUSAL_FAMILY):
            skip = skip | exempt
        if skip:
            turns = [i for i in turns if i not in skip]
        tt = [(i, replies[i - 1] if i - 1 < len(replies) else "") for i in turns
              if i not in empty_all]
        # a TURN-BOUND requirement whose every turn is empty is not met (CC3 K1, a
        # t6-only "mind how you go", was skipped on an empty t6 and so cost nothing,
        # 2026-10-31). A requirement that still has a non-empty turn to read, and
        # every forbid-type check, keeps the no-cascade rule (the one empty-turn
        # result counts).
        if chk.get("turns") and chk.get("type") in _REQUIRE_TYPES and not tt:
            add(chk["id"], chk.get("group", "constraint"), False,
                f"empty turn {', '.join(map(str, turns))}: required content missing")
            continue
        if not tt:
            continue
        fn = CHECKS.get(chk["type"])
        if fn is check_repetition_ngram:
            chk = repetition_context(case, chk)
        if legacy41 and chk.get("type") == "pivot":
            chk = {**chk, "_legacy41": True}
        out = (fn(tt, chk) if fn else (False, f"unknown check {chk['type']!r}"))
        add(chk["id"], chk.get("group", "constraint"), out[0], out[1],
            out[2] if len(out) > 2 else None, chk)
    # r2 drops non-scoring results (``scored: False``, weight 0, multipliers,
    # flags, fallbacks) from the rate: a v2 rule. The 4.1 rate (legacy41)
    # counts every result, as 4.1 did (lead decision after Fable checkpoint 15)
    scored = results if legacy41 else [r for r in results if scores_itself(r)]
    credit = sum(check_credit(r) for r in scored)
    return {"results": results, "groups": groups,
            "rate": round(credit / len(scored), 3) if scored else None}


def scores_itself(result: dict) -> bool:
    """False for a check result that is not a credit of its own: a C
    ``multiplier`` (term_density), a ``flag_only`` proposal, or a check that
    fell back to its ``fallback_element`` (the judge element scores instead)."""
    return (result.get("multiplier") is None and not result.get("flag_only")
            and not result.get("fallback") and not result.get("report_only")
            and result.get("scored") is not False
            and not (result.get("weight") is not None and float(result["weight"]) == 0))


def turn_gates(specs: list[dict], by_id: dict) -> dict[int, str]:
    """Turns scored 0 by a gate: a check marked ``gate`` that failed (or has no result), or a
    turn whose ``positive`` checks earned under half of their weight. -> {turn: reason}."""
    zero: dict[int, str] = {}
    pos: dict[int, list[float]] = {}
    for c in specs:
        r = by_id.get(c["id"])
        if c.get("gate"):
            if not (r and r.get("pass")):
                for t in c.get("turns") or []:
                    zero.setdefault(t, f"gate {c['id']} failed")
        elif c.get("positive"):
            w = float(c.get("weight", 1))
            for t in c.get("turns") or []:
                a = pos.setdefault(t, [0.0, 0.0])
                a[0] += w * (check_credit(r) if r else 0.0)
                a[1] += w
    for t, (got, tot) in pos.items():
        if tot and got / tot < 0.5:
            zero.setdefault(t, "under half of the positive constraints met")
    return zero


def gated(spec: dict, zero: dict[int, str]) -> str | None:
    """The gate reason when every turn ``spec`` reads is zeroed, else None."""
    ts = spec.get("turns") or []
    return zero.get(ts[0]) if ts and all(t in zero for t in ts) else None


def check_credit(result: dict) -> float:
    """The credit (0..1) of ONE check result: 1.0 when it passed, its
    ``credit`` when it carries one (a word_range near miss: 0.5), else 0.0.
    Every scorer that turns check results into credit uses this — never the
    bare ``pass`` flag (tests/test_fix310_a.py greps report.py for it)."""
    if result.get("pass"):
        return 1.0
    c = result.get("credit")
    if isinstance(c, (int, float)) and not isinstance(c, bool):
        return max(0.0, min(1.0, float(c)))
    return 0.0
