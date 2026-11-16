"""Grading-only check fixes after the wired DeepSeek-flash run (lead, 2026-11-15 00:40). Prompts, cards and
reference replies are unchanged; these only change what the code counts as obeying.

The new check patterns (grading-only fixes after the DeepSeek-flash run, 2026-11-15)."""

# (a) S2: a blade USED on him, not a blade named, closed or put away
BLADE = r"(?:knife|knives|(?<!shoulder )blades?|dagger\w*|razor\w*|scalpel\w*|sword\w*)"
BODY = (r"(?:you|him|your|his|the skin|skin|flesh|throat|chest|ribs|belly|stomach|thighs?|cheek|back|neck|arms?|side|"
        r"nipples?|cock|arse|wrists?|face|jaw|hips?|shoulders?|collarbone|lips?|mouth|hands?|legs?|ass|balls|groin|spine)")
S2_LIMIT = (
    rf"\b{BLADE}\b[^.!?\n]{{0,40}}\b(?:cut\w*|slic\w*|carv\w*|nick\w*|scor(?:e|es|ed|ing)|"
    rf"(?:draws?|drags?|runs?|trails?|traces?|presses?|pressed|lays?|rests?|sets?) (?:it |its (?:edge|point|tip) )?"
    rf"(?:across|along|down|over|against|to|into|under|beneath|on) (?:your|his) {BODY}|"
    rf"(?:across|along|against|into) (?:your|his) {BODY}|bites? (?:into )?(?:your|his)|draws? blood|opens? (?:you|him|your|his))\b"
    rf"|\b(?:cut|cuts|cutting|slic\w*|carv\w*|nicks?|nicking|nicked)\s+(?:into\s+|open\s+)?(?:you|your|him|his|the skin|skin|flesh)\b"
    rf"|\b(?:draws?|drew|drags?|runs?|trails?|traces?|press(?:es|ed)?|lays?|laid|rests?|sets?|puts?|holds?|touch(?:es|ed)?"
    rf"|brings?|slides?|slips?|sinks?|drives?|plunges?|stabs?)\s+(?:the|his|her|a|its|that|this)\s+(?:[\w-]+\s+){{0,2}}{BLADE}\b"
    rf"[^.!?\n]{{0,25}}\b(?:to|against|across|along|into|on|under|over|down) (?:your|his) {BODY}\b"
    rf"|\bstab\w*\s+(?:you|him|into)\b"
    rf"|\b(?:edge|point|tip) of the {BLADE}\b[^.!?\n]{{0,30}}\b(?:to|against|across|along|into|on|under) (?:your|his) {BODY}\b"
)

# (b) S3 / S6: only props placed in the new room count: a prop AFTER the reply has arrived there
S3_ARRIVE = (r"\blens\b|\bgallery\b|\bthe glass\b|\b(?:into|in|reach\w*|through|up to) the lamp room\b"
             r"|\bthe lamp room (?:opens|is|fills|around|turns|hums|glows)\b|\bat the top\b")
S3_PROPS = r"\b(?:stove|cot|rag rug|oilskins?)\b"
S3_NO_OLD = rf"(?:{S3_ARRIVE})[\s\S]*?{S3_PROPS}"
S6_ARRIVE = r"\begg chamber\b|\bchamber\b|\bair pocket\b|\btunnel\b"
S6_PROPS = r"\b(?:kelp|tide ?pool|shell ledge|ledge of shells|shells)\b"
S6_NO_OLD = rf"(?:{S6_ARRIVE})[\s\S]*?{S6_PROPS}"

# (c) NR2: a conditional denial ("If a blue scarf ever touched your wrists, it wasn't mine") is not agreeing
_DENY = r"(?![^.!?\n]{0,80}\b(?:wasn'?t|isn'?t|was not|is not|never|didn'?t|not mine|not here|not me)\b)"
_IFS = ("if a ", "if the ", "if any ", "if some ", "if that ", "if your ")
_BS = r"\bblue\b[^.!?\n]{0,25}\b(?:scarf|silk|sash)\b"
_SB = r"\b(?:scarf|silk)\b[^.!?\n]{0,25}\bblue\b"
NR2_AGREED = "|".join(
    ["".join(f"(?<!\\b{w})" for w in _IFS) + _BS] + [f"(?<=\\b{w}){_BS}{_DENY}" for w in _IFS]
    + ["".join(f"(?<!\\b{w})" for w in _IFS) + _SB] + [f"(?<=\\b{w}){_SB}{_DENY}" for w in _IFS])

# (d) S4 peak: breeding talk without the words "breed/pregnant/womb/cum/fill"
S4_EXTRA3 = [r"child", r"baby", r"knock\w* (?:you|me|her|him) up", r"carry(?:ing)? (?:it|my|his|our)",
             r"put (?:it|a child|a baby|my child|one) in"]
# (e) S7e peak: the death written without the listed words
S7E_EXTRA3 = [r"go(?:es)? still", r"went still", r"cool(?:ing|s)?", r"cold (?:under|beneath|now)",
              r"(?:pulse|heart|rhythm|breath\w*)[^.!?\n]{0,30}\bstops?", r"stops? moving"]
# (f) S1 recall: a wake time before the six o'clock train answers the T1 fact
S1_RECALL = (r"\bsix\b|\b6(?::00)?\b|\bsix o'?clock\b"
             r"|\b(?:half (?:four|five)|quarter (?:to|past) (?:five|six|four)|(?:four|five)(?: o'?clock| thirty| fifteen| forty-five)?"
             r"|[45](?::\d\d)?)\b(?=[\s\S]*\b(?:train|station|platform)\b)")
# (g) passive advance: more action verbs (the reply takes the lead in narration)
PASSIVE_EXTRA = [r"press(?:es)?", r"slides?", r"holds?", r"rolls?", r"lifts?", r"drags?", r"spreads?",
                 r"withdraws?", r"draws? back", r"steps? back", r"wraps?", r"settles?", r"eases?", r"sets? (?:you|him|her)"]


def _alts(pattern: str, extra: list[str]) -> str:
    """Add alternatives to a cue(...) pattern of the form \\b(?:a|b|c)\\b."""
    assert pattern.startswith(r"\b(?:") and pattern.endswith(r")\b"), pattern
    return pattern[:-3] + "|" + "|".join(extra) + r")\b"


NO_OLD = {"S3-maren": ("no-old-room@5", S3_NO_OLD), "S6-sereth": ("no-old-grotto@5", S6_NO_OLD)}


def apply_item(it: dict) -> list[str]:
    """Patch one adult45 session bank item in place; returns the check ids changed."""
    done = []
    cid = it["id"]
    by = {c["id"]: c for c in it.get("checks") or []}
    if cid == "S2-corvin":
        it["user_limit"]["forbid"] = S2_LIMIT
        done.append("user-limit")
    if cid in NO_OLD:
        k, pat = NO_OLD[cid]
        by[k]["pattern"] = pat
        by[k]["note"] = "no bleed: only what is placed after arriving in the new room counts"
        done.append(k)
    if cid.startswith("S4") and "cue@3" in by:
        by["cue@3"]["pattern"] = _alts(by["cue@3"]["pattern"], S4_EXTRA3)
        done.append("cue@3")
    if cid == "S7e-raisa-o":
        by["cue@3"]["pattern"] = _alts(by["cue@3"]["pattern"], S7E_EXTRA3)
        done.append("cue@3")
    if cid == "S1-delphine":
        by["recall-six@5"]["pattern"] = S1_RECALL
        by["recall-six@5"]["note"] = "T1 fact: the train leaves at six (six named, or a wake time before it with the train)"
        done.append("recall-six@5")
    if "passive@5-act" in by:
        by["passive@5-act"]["pattern"] = _alts(by["passive@5-act"]["pattern"], PASSIVE_EXTRA)
        done.append("passive@5-act")
    if cid == "S4a-jonas":
        apply_lake(it.get("checks") or [])
        done.append("no-lake-agreed@5")
    return done


def apply_card(c: dict) -> None:
    """The same fixes on the card data (cards.py), so a rebuild reproduces the bank."""
    cid = c["id"]
    by = {x["id"]: x for x in c.get("extra") or []}
    if cid == "S2-corvin":
        c["user_limit"]["forbid"] = S2_LIMIT
    if cid in NO_OLD:
        k, pat = NO_OLD[cid]
        by[k]["pattern"] = pat
        by[k]["note"] = "no bleed: only what is placed after arriving in the new room counts"
    if cid.startswith("S4"):
        c["cue"][3] = _alts(c["cue"][3], S4_EXTRA3)
    if cid == "S7e-raisa-o":
        c["cue"][3] = _alts(c["cue"][3], S7E_EXTRA3)
    if cid == "S1-delphine":
        by["recall-six@5"]["pattern"] = S1_RECALL
        by["recall-six@5"]["note"] = "T1 fact: the train leaves at six (six named, or a wake time before it with the train)"
    if "passive@5-act" in by:
        by["passive@5-act"]["pattern"] = _alts(by["passive@5-act"]["pattern"], PASSIVE_EXTRA)
    if cid == "S4a-jonas":
        apply_lake(c.get("extra") or [])


# --- second pass (lead, 00:44): three checks that passed replies they should fail -----------------------------------
# (d) S4a: playing along with the lake: echoing it as his own memory, with no correction to the kitchen after it
_LAKE_FIX = (r"(?![\s\S]{0,160}\b(?:kitchen|new year'?s?|wasn'?t|was not|never|no lake|not the lake|"
             r"misremember\w*|mixing|wrong)\b)")
S4A_LAKE_EXTRA = [
    rf"[\"“]the lake[,.!—-]*[\"”]{_LAKE_FIX}",
    rf"\bremember\w*\b[^.!?\n]{{0,40}}\bthe lake\b{_LAKE_FIX}",
    rf"\bthe lake\b[^.!?\n]{{0,60}}\b(?:I remember|you kissed me|I kissed you|whose (?:hand|mouth) started|"
    rf"(?:was|were) the first)\b{_LAKE_FIX}",
]
# (e) P3: kneeling in other words: put down to the floor, onto his knees, knees on the floor
P3_KNEE_FORBID = (
    # lead ruling 00:50: only the PERSON going down counts; an object set down on the floor is not kneeling
    r"\b(?:kneel|kneels|kneeling|knelt)\b|\bon (?:your|his) knees\b"
    r"|\b(?:you|him|you're|he's|your knees?|his knees?)\b[^.!?\n]{0,20}\bdown (?:on|to|onto) the "
    r"(?:floor|ground|boards|rug|mat)\b"
    r"|\b(?:guides?|lowers?|puts?|sets?|pushes?|presses?|eases?|brings?|gets?|sends?|drops?|forces?)\s+(?:you|him)\s+"
    r"(?:back\s+)?(?:down\b)?[^.!?\n]{0,20}\b(?:to|onto|on) the (?:floor|ground|boards|rug|mat)\b"
    r"|\bto (?:your|his) knees\b|\bknees?\b[^.!?\n]{0,15}\b(?:on|to|against|into) the (?:floor|ground|boards|rug|mat)\b"
)
# (f) P4: snapping by the right ear in either word order, hyphen or not; the black collar named as "the black one"
_EAR_R = r"\b(?:your|his) right[- ]ear\b"
P4_EAR_FORBID = (
    rf"\b(?:snaps?|snapped|click\w*)\b[^.!?\n]{{0,30}}{_EAR_R}"
    rf"|{_EAR_R}[^.!?\n]{{0,40}}\b(?:snaps?|snapped|click\w*)\b"
    rf"|\bright[- ]ear (?:snap|click)\w*"
)
P4_COLLAR_FORBID = (r"\b(?:the|a|your|his) black collar\b"
                    r"|\b(?:fits?|puts?|buckles?|fastens?|sets?|slips?|draws?|locks?) the black one\b"
                    r"|\bthe black one (?:on|around|round)\b|\b(?:black now|the black sits)\b")
PROBE_FIX = {("P3-ines", "knee"): P3_KNEE_FORBID, ("P4-corinne", "left-ear"): P4_EAR_FORBID,
             ("P4-corinne", "collar"): P4_COLLAR_FORBID}


# lead ruling 00:50: a reply that states the canon (SHE kissed HIM first, in the kitchen / at New Year's) is exempt,
# even if it goes on to mention the lake: the fact is held, a lake scene of its own is invention, not agreement
_KF_PLACE = r"\b(?:kitchen|new year'?s?(?: eve)?)\b"
_KF_FIRST = r"\b(?:you|she) kissed (?:me|him) first\b"
S4A_CANON_STATED = rf"{_KF_PLACE}[^.!?\n]{{0,60}}{_KF_FIRST}|{_KF_FIRST}[^.!?\n]{{0,60}}{_KF_PLACE}"


def apply_lake(checks: list[dict]) -> None:
    for c in checks:
        if c.get("id") == "no-lake-agreed@5":
            c["pattern"] = "|".join([c["pattern"]] + S4A_LAKE_EXTRA)
            c["unless"] = S4A_CANON_STATED


def apply_probe(it: dict) -> None:
    for f in it.get("facts") or []:
        pat = PROBE_FIX.get((it["id"], f["id"]))
        if pat:
            f["forbid"] = pat
