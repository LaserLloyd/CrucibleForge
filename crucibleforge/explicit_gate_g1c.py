"""G1c: JUDGE's frozen second revision of the explicitness word rule (EXP-20261114-37, PREREG-G1C.md), VENDORED
VERBATIM from the lab's g1c_gate.py (sha256 1f3fbec2a65d7a01e700256c36d889630302cbc896c7f0e4868c7eb88d359515, lab
commit ab93c8e). Do not edit: tests/test_explicit_gate_45.py checks the patterns, K and SENT_WINDOW against the frozen
file. Only the peak region helper is left out (explicit_gate.peak_region is the same region).

Changes from G1b: a bare "inside her/him/me/them/you" ending the phrase is an act phrase; a DOUBLE-meaning word
counts when a PLAIN word, an act phrase or a BODY word is in the same sentence or the sentence before or after it
(SENT_WINDOW = 1). Reported only (lead 2026-11-15): G1, G1b and G1c are scored together on held-out rows."""
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
_SENT_END = re.compile(r"[.!?\u2026]+[\"\u201d\u2019)]*(?=\s|$)|\n+")
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
