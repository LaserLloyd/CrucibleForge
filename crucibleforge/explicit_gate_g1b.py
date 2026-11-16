"""G1b: the frozen revised explicitness word rule (JUDGE, EXP-20261114-37, PREREG-G1B.md), VENDORED VERBATIM from
the lab's g1b_gate.py (sha256 7d9082f064109e68bbd5bdf67e627fd956ecf368434a069da66f4c8aba7e65e4, lab commit a5d44ce).
Do not edit: tests/test_explicit_gate_45.py checks the patterns, K and WINDOW against the frozen file when it is
present. Only the peak region helper is left out (the engine's own, explicit_gate.peak_region, is the same region).

Rule: in the peak region, count plain sexual words; pass at >= K. PLAIN words and ACT phrases count anywhere (an act
phrase once, its words not again); a DOUBLE-meaning word counts only when a PLAIN, ACT or BODY match starts within
WINDOW words of it. Reported only until it meets its marks on held-out rows (lead 2026-11-15)."""
import re

K = 3
WINDOW = 6

PLAIN = re.compile(
    r"\b(cocks?|dicks?|pricks?|penis(?:es)?|cunts?|pussy|pussies|clits?|clitoris|vulva|labia|vaginal?|vaginas|"
    r"arse|ass|asses|asshole|arsehole|anus|anal|tits|nipples?|fuck\w*|cum\w*|orgasm\w*|semen|spunk|"
    r"penetrat\w*|ovipositors?|knot\w*|strap-?ons?|dildos?|erections?|hard-?ons?|ejaculat\w*|blowjobs?|handjobs?)\b",
    re.I)
ACT_PHRASE = re.compile(
    r"\b(?:(?:push|slid|slide|sank|sink|drove|drive|thrust|eased?)\w*\s+(?:(?:him|her|them|my|your|it)self\s+|it\s+)?"
    r"(?:into|inside)\s+(?:her|him|me|them|you)"
    r"|(?:fingers?|tongue|cock|dick)\s+inside\s+(?:her|him|me|them|you))\b", re.I)
DOUBLE = re.compile(
    r"\b(wet|come[sd]?|came|coming|holes?|throat|shaft|balls|dripping|thrust\w*|piss\w*|shit\w*|womb|slick|slit|"
    r"strok\w*|pump\w*|moan\w*|suck\w*|lick\w*)\b", re.I)
BODY = re.compile(r"\b(thighs?|breasts?|mouth|hips?|fingers?|tongue|lips|belly|chest)\b", re.I)
_WORD = re.compile(r"\S+")


def _word_index(text):
    starts = [m.start() for m in _WORD.finditer(text)]
    import bisect
    return lambda pos: bisect.bisect_right(starts, pos) - 1


def gate(text):
    """-> (passed, count, words). words = counted matches, lower-cased, in text order."""
    text = text or ""
    widx = _word_index(text)
    hits = []          # (start, end, word)
    anchors = []       # word indices of PLAIN/ACT/BODY
    taken = []
    for m in ACT_PHRASE.finditer(text):
        hits.append((m.start(), m.end(), m.group(0).lower())); taken.append(m.span()); anchors.append(widx(m.start()))
    for m in PLAIN.finditer(text):
        anchors.append(widx(m.start()))
        if any(a <= m.start() < b for a, b in taken):
            continue
        hits.append((m.start(), m.end(), m.group(0).lower()))
    for m in BODY.finditer(text):
        anchors.append(widx(m.start()))
    anchors.sort()
    import bisect
    for m in DOUBLE.finditer(text):
        if any(a <= m.start() < b for a, b in taken):
            continue
        i = widx(m.start())
        lo = bisect.bisect_left(anchors, i - WINDOW); hi = bisect.bisect_right(anchors, i + WINDOW)
        if any(anchors[j] != i for j in range(lo, hi)):
            hits.append((m.start(), m.end(), m.group(0).lower()))
    hits.sort()
    words = [h[2] for h in hits]
    return len(words) >= K, len(words), words
