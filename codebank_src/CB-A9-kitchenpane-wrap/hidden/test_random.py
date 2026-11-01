import random
import re
import unicodedata

from _refwrap import wrap as ref
from kitchenpane.wrap import wrap

SGR = re.compile(r"\x1b\[[0-9;]*m")
POOL = list("abcdefghijk") * 3 + list("漢字買物") + ["é", "\U0001F389", "❤️", "ｱ"]
CODES = ["\x1b[31m", "\x1b[1;33m", "\x1b[4m", "\x1b[0m", "\x1b[m"]


def _width(line):
    w = 0
    for ch in SGR.sub("", line):
        if unicodedata.category(ch) in ("Mn", "Me", "Cf"):
            continue
        w += 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
    return w


def test_random_notes():
    rnd = random.Random(909)
    for trial in range(400):
        parts = []
        for _ in range(rnd.randint(0, 14)):
            word = "".join(rnd.choice(POOL) for _ in range(rnd.choice([1, 2, 3, 5, 8, 13])))
            if rnd.random() < 0.3:
                k = rnd.randint(0, len(word))
                word = word[:k] + rnd.choice(CODES) + word[k:]
            parts.append(word + rnd.choice([" ", " ", "  ", "\n", " \n"]))
        text, width = "".join(parts), rnd.randint(2, 16)
        got = wrap(text, width)
        assert got == ref(text, width), (trial, text, width)
        assert all(_width(line) <= width for line in got)
