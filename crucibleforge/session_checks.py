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
              the OOC segment) | "all". Default "narration".

Result on the row::

    row["checks"] = {"results": [{"id","group","pass","detail"}...],
                     "groups": {"identity": [passed, total], ...},
                     "rate": passed/total}

A check whose target turn is EMPTY (reasoning overflow) FAILS — an empty
turn cannot honour a retcon — but the row's ``empty_generation`` already
flags why, so failures.md can say so.
"""
from __future__ import annotations

import re

# ------------------------------------------------------------------ scopes
# straight and curly double quotes; single quotes are left alone (apostrophes)
_QUOTE_RE = re.compile(r'"[^"\n]{0,2000}?"|“[^”]{0,2000}?”', re.S)
# an OOC segment: ((...)), [OOC ...], or a line that starts with OOC:
# ((...)) may itself contain single parentheses, so match up to the first "))"
_OOC_RE = re.compile(r"\(\((?:(?!\)\)).)*\)\)|\[\s*OOC[^\]]*\]|^[ \t]*\(?[ \t]*OOC[ \t]*[:\-][^\n]*$",
                     re.I | re.M | re.S)


def ooc_segment(text: str) -> str:
    return "\n".join(m.group(0) for m in _OOC_RE.finditer(text))


def in_character(text: str) -> str:
    return _OOC_RE.sub(" ", text)


def narration(text: str) -> str:
    return _QUOTE_RE.sub(" ", in_character(text))


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
    m = re.search(rf"^\s*#{{1,4}}\s*{re.escape(name)}\b[^\n]*\n(.*?)(?=^\s*#{{1,4}}\s|\Z)",
                  text, re.I | re.M | re.S)
    return m.group(1) if m else ""


def _body(t: str, chk: dict, default_scope: str = "narration") -> str:
    return SCOPES[chk.get("scope", default_scope)](_section(t, chk.get("section")))


# a forbidden match preceded (within ~25 chars, same sentence) by a negation
# is a statement of the rule, not a breach: "nothing over my mouth", "no gags"
_NEG_RE = re.compile(r"\b(?:no|not|never|nothing|without|don't|won't|isn't|wasn't|n't)\b[^.!?\n]{0,25}$", re.I)


def _negated(body: str, m: re.Match) -> bool:
    return bool(_NEG_RE.search(body[max(0, m.start() - 40):m.start()]))


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
PUPPET_VERBS = (
    r"say|says|said|ask|asks|asked|repl(?:y|ies|ied)|answer(?:s|ed)?|"
    r"whisper(?:s|ed)?|murmur(?:s|ed)?|mutter(?:s|ed)?|shout(?:s|ed)?|"
    r"nod(?:s|ded)?|smil(?:e|es|ed)|grin(?:s|ned)?|laugh(?:s|ed)?|chuckl(?:e|es|ed)|"
    r"sigh(?:s|ed)?|shrug(?:s|ged)?|flinch(?:es|ed)?|wince(?:s|d)?|blush(?:es|ed)?|"
    r"decid(?:e|es|ed)|agree(?:s|d)?|refus(?:e|es|ed)|"
    r"feel|feels|felt|think|thinks|thought|realiz(?:e|es|ed)|wonder(?:s|ed)?|"
    r"shiver(?:s|ed)?|trembl(?:e|es|ed)|shudder(?:s|ed)?|"
    r"moan(?:s|ed)?|gasp(?:s|ed)?|groan(?:s|ed)?|whimper(?:s|ed)?|pant(?:s|ed)?|"
    r"beg(?:s|ged)?|arch(?:es|ed)?|buck(?:s|ed)?|comes|came|climax(?:es|ed)?"
)
PUPPET_VERBS_PRESENT = (
    r"nod|smile|grin|laugh|chuckle|sigh|shrug|flinch|wince|blush|decide|agree|"
    r"feel|realize|shiver|tremble|shudder|moan|gasp|groan|whimper|pant|beg|arch|"
    r"buck|come|climax|whisper|murmur|mutter|reply|answer|reach|step|lean|grab|kiss"
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


def check_no_puppeting(turn_texts, chk):
    """names: user-controlled character names for these turns.
    second_person: the card addresses the user's character as 'you'.
    max_hits: tolerated hits across the checked turns (default 0)."""
    pats = _puppet_patterns(chk["names"], chk.get("second_person", False))
    hits = []
    for idx, t in turn_texts:
        body = _body(t, chk)
        for p in pats:
            for m in p.finditer(body):
                hits.append(f"t{idx}: …{_snip(body, m)}…")
    ok = len(hits) <= int(chk.get("max_hits", 0))
    return ok, ("clean" if not hits else f"{len(hits)} hit(s): " + " | ".join(hits[:3]))


def check_forbid_regex(turn_texts, chk):
    """pattern: regex that must NOT appear. max_hits_per_turn (default 0)."""
    p = re.compile(chk["pattern"], re.I | re.M)
    lim = int(chk.get("max_hits_per_turn", 0))
    bad = []
    for idx, t in turn_texts:
        body = _body(t, chk)
        ms = [m for m in p.finditer(body)
              if not (chk.get("negation_guard") and _negated(body, m))]
        if len(ms) > lim:
            bad.append(f"t{idx}×{len(ms)}: …{_snip(body, ms[0])}…")
    return (not bad), ("clean" if not bad else " | ".join(bad[:3]))


def check_require_regex(turn_texts, chk):
    """pattern must match at least min_hits (default 1) times in EVERY
    checked turn (per_turn: true, default) or across them all."""
    p = re.compile(chk["pattern"], re.I | re.M)
    need = int(chk.get("min_hits", 1))
    if chk.get("per_turn", True):
        short = [f"t{idx}:{len(p.findall(_body(t, chk)))}"
                 for idx, t in turn_texts if len(p.findall(_body(t, chk))) < need]
        return (not short), ("ok" if not short else f"below {need}: " + ", ".join(short))
    n = sum(len(p.findall(_body(t, chk))) for _, t in turn_texts)
    return n >= need, f"{n} hit(s), need {need}"


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


def check_word_range(turn_texts, chk):
    fails = []
    for idx, t in turn_texts:
        n = _words(_body(t, chk, "ic"))
        if not int(chk.get("min", 0)) <= n <= int(chk.get("max", 10 ** 9)):
            fails.append(f"t{idx}: {n} words (want {chk.get('min', 0)}-{chk.get('max', '∞')})")
    return (not fails), ("ok" if not fails else "; ".join(fails))


# Tense by counting unambiguous auxiliaries/copulas in NARRATION (dialogue
# is excluded — characters legitimately speak in any tense). Crude but
# robust: a present-tense narrator almost never writes "was/were/had" in
# narration more than the present forms, and vice versa.
_PAST = re.compile(r"\b(?:was|were|had|did|said|asked|looked|turned|felt|stood|took|went|came|knew|thought)\b", re.I)
_PRESENT = re.compile(r"\b(?:is|are|am|has|have|does|says|asks|looks|turns|feels|stands|takes|goes|comes|knows|thinks)\b", re.I)


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
        past, pres = len(_PAST.findall(body)), len(_PRESENT.findall(body))
        a, b = (pres, past) if want == "present" else (past, pres)
        if a + b < int(chk.get("min_evidence", 4)):
            continue  # too few markers to call (short, dialogue-heavy turn)
        if a < r * b:
            fails.append(f"t{idx}: present={pres} past={past}")
    return (not fails), (f"{want} ok" if not fails else f"not {want}: " + "; ".join(fails))


CHECKS = {
    "tense": check_tense,
    "no_puppeting": check_no_puppeting,
    "forbid_regex": check_forbid_regex,
    "require_regex": check_require_regex,
    "require_all": check_require_all,
    "ooc_reply": check_ooc_reply,
    "ooc_field": check_ooc_field,
    "word_range": check_word_range,
}


def run_checks(case: dict, replies: list[str]) -> dict | None:
    """replies = assistant contents in turn order (single-turn: [response])."""
    specs = case.get("checks")
    if not specs:
        return None
    results = []
    groups: dict[str, list[int]] = {}
    for chk in specs:
        turns = chk.get("turns") or list(range(1, len(replies) + 1))
        tt = [(i, replies[i - 1] if i - 1 < len(replies) else "") for i in turns]
        empty = [i for i, t in tt if not (t or "").strip()]
        if empty:
            ok, detail = False, f"empty turn(s) {empty} (no content to check)"
        else:
            fn = CHECKS.get(chk["type"])
            ok, detail = (fn(tt, chk) if fn else (False, f"unknown check {chk['type']!r}"))
        g = chk.get("group", "constraint")
        results.append({"id": chk["id"], "group": g, "pass": bool(ok), "detail": detail[:300]})
        tot = groups.setdefault(g, [0, 0])
        tot[0] += int(bool(ok))
        tot[1] += 1
    passed = sum(1 for r in results if r["pass"])
    return {"results": results, "groups": groups,
            "rate": round(passed / len(results), 3) if results else None}
