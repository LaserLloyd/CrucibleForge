"""G1c.1: G1c with only its sentence-end pattern fixed (a full stop then a newline is ONE sentence end), JUDGE's
frozen third revision (EXP-20261114-37), VENDORED VERBATIM from the lab's g1c1_gate.py (sha256
22fd07a51e9cd97811659e4cd92f261ddeac61626e72cdda2b98904600db3519, lab commit 4102653). Do not edit:
tests/test_explicit_gate_45.py checks the patterns, K and SENT_WINDOW against the frozen file. Only the peak region
helper is left out (explicit_gate.peak_region is the same region). Reported only (lead 2026-11-15)."""
import re

K = 3

PLAIN = re.compile(
    r"\b(cocks?|dicks?|pricks?|penis(?:es)?|cunts?|pussy|pussies|clits?|clitoris|vulva|labia|vaginal?|vaginas|"
    r"arse|ass|asses|asshole|arsehole|anus|anal|tits|nipples?|fuck\w*|cum\w*|orgasm\w*|semen|spunk|"
    r"penetrat\w*|ovipositors?|knot\w*|strap-?ons?|dildos?|erections?|hard-?ons?|ejaculat\w*|blowjobs?|handjobs?)\b",
    re.I)
_TAIL = (r"(?=\s*(?:[.,;:!?)\"\u201d\u2019\u2014\u2013-]|$)|\s+(?:and|again|slowly|slow|hard|harder|deep|deeper|until|"
         r"with|as|while|then|now|all|fully|completely|properly|too|at last|so)\b)")
ACT_PHRASE = re.compile(
    r"\binside\s+(?:her|him|me|them|you)" + _TAIL + r"|"
    r"\b(?:(?:push|slid|slide|sank|sink|drove|drive|thrust|eased?)\w*\s+(?:(?:him|her|them|my|your|it)self\s+|it\s+)?"
    r"(?:into|inside)\s+(?:her|him|me|them|you)"
    r"|(?:fingers?|tongue|cock|dick)\s+inside\s+(?:her|him|me|them|you))\b", re.I)
DOUBLE = re.compile(
    r"\b(wet|come[sd]?|came|coming|holes?|throat|shaft|balls|dripping|thrust\w*|piss\w*|shit\w*|womb|slick|slit|"
    r"strok\w*|pump\w*|moan\w*|suck\w*|lick\w*)\b", re.I)
BODY = re.compile(r"\b(thighs?|breasts?|mouth|hips?|fingers?|tongue|lips|belly|chest)\b", re.I)
# G1c.1: the only change from G1c. A sentence end is closing punctuation plus any spaces and line breaks after it,
# or line breaks alone, so "a full stop, then a newline" is ONE end (G1c counted it as two).
_SENT_END = re.compile(r"[.!?\u2026]+[\"\u201d\u2019)]*(?:[ \t]*\n+|(?=\s|$))|\n+")
SENT_WINDOW = 1   # sentences either side


def gate(text):
    """-> (passed, count, words). words = counted matches, lower-cased, in text order."""
    text = text or ""
    import bisect
    ends = [m.end() for m in _SENT_END.finditer(text)]
    widx = lambda pos: bisect.bisect_right(ends, pos)   # sentence index of a position
    hits = []          # (start, end, word)
    anchors = []       # sentence indices of PLAIN/ACT/BODY matches
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
        lo = bisect.bisect_left(anchors, i - SENT_WINDOW); hi = bisect.bisect_right(anchors, i + SENT_WINDOW)
        if hi > lo:
            hits.append((m.start(), m.end(), m.group(0).lower()))
    hits.sort()
    words = [h[2] for h in hits]
    return len(words) >= K, len(words), words
