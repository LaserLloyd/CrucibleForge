"""Line wrapping for the status pane (see docs/wrap.md)."""
import re
import unicodedata

SGR = re.compile(r"\x1b\[[0-9;]*m")
RESET = ("\x1b[0m", "\x1b[m")


def _zero(ch: str) -> bool:
    return unicodedata.category(ch) in ("Mn", "Me", "Cf")


def _cw(ch: str) -> int:
    return 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1


def _units(word: str) -> list[tuple[str, int]]:
    """[(text, width)]: each cluster with the SGR sequences before it; trailing SGRs join the last."""
    units: list[list] = []
    pending = ""
    i = 0
    while i < len(word):
        m = SGR.match(word, i)
        if m:
            pending += m.group()
            i = m.end()
            continue
        ch = word[i]
        if _zero(ch) and units and not pending:
            units[-1][0] += ch
        else:
            units.append([pending + ch, _cw(ch)])
            pending = ""
        i += 1
    if pending:
        if units:
            units[-1][0] += pending
        else:
            units.append([pending, 0])
    return [(t, w) for t, w in units]


def _apply(style: list[str], s: str) -> None:
    for seq in SGR.findall(s):
        if seq in RESET:
            style.clear()
        else:
            style.append(seq)


def wrap(text: str, width: int) -> list[str]:
    """Wrap ``text`` into lines of at most ``width`` display columns."""
    if width < 2:
        raise ValueError("width must be >= 2")
    style: list[str] = []
    out: list[str] = []
    for para in text.split("\n"):
        lines: list[tuple[str, int]] = [("", 0)]
        for word in (w for w in para.split(" ") if w):
            units = _units(word)
            ww = sum(w for _, w in units)
            cur, cw = lines[-1]
            if ww <= width:
                if not cur:
                    lines[-1] = (word, ww)
                elif cw + 1 + ww <= width:
                    lines[-1] = (cur + " " + word, cw + 1 + ww)
                else:
                    lines.append((word, ww))
                continue
            if cur:
                lines.append(("", 0))
            piece, pw = "", 0
            for t, w in units:
                if pw + w > width:
                    lines[-1] = (piece, pw)
                    lines.append(("", 0))
                    piece, pw = "", 0
                piece += t
                pw += w
            lines[-1] = (piece, pw)
        for content, _ in lines:
            start = "".join(style)
            _apply(style, content)
            out.append(start + content + ("\x1b[0m" if style else ""))
    return out
