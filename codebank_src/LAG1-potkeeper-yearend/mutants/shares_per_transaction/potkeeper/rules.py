"""Category rules: `pattern => category` (README "Category rules")."""
import re

UNCATEGORISED = "uncategorised"
_WS = re.compile(r"\s+")


def normalise_payee(text: str) -> str:
    return _WS.sub(" ", text.casefold()).strip()


class Rules:
    def __init__(self, rules=()):
        # [(normalised pattern, category)] in file order
        self.rules: list[tuple[str, str]] = [(normalise_payee(p), c.strip()) for p, c in rules]

    @classmethod
    def parse(cls, text: str) -> "Rules":
        rules = []
        for n, line in enumerate(text.splitlines(), 1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=>" not in line:
                raise ValueError(f"rules line {n}: expected 'pattern => category'")
            pattern, category = line.split("=>", 1)
            if not pattern.strip() or not category.strip():
                raise ValueError(f"rules line {n}: empty pattern or category")
            rules.append((pattern, category))
        return cls(rules)

    def match(self, payee: str) -> str:
        p = normalise_payee(payee)
        hits = [(len(pattern), n, category) for n, (pattern, category) in enumerate(self.rules) if pattern in p]
        if not hits:
            return UNCATEGORISED
        return min(hits, key=lambda h: (-h[0], h[1]))[2]   # longest pattern; equal length: first in the file

    def categorise(self, ledger) -> int:
        """Set the category of every uncategorised transaction; returns how many were set."""
        n = 0
        for t in ledger:
            if t.category is None:
                t.category = self.match(t.payee)
                n += 1
        return n
