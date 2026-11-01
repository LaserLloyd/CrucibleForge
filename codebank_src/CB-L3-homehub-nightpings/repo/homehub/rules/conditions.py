"""A tiny condition language for rules.

    temp.living < 18 and not door.front == open
    time in 22:00-06:00 or (plug.tv.watts > 150)

Grammar: or-expr := and-expr ("or" and-expr)* ; and-expr := not-expr ("and"
not-expr)* ; not-expr := "not" not-expr | atom ; atom := "(" or-expr ")" |
"time in" WINDOW | NAME OP VALUE. NAME is "<kind>.<device>.<field>" or the
short "<kind>.<device>" (the kind's main field: temp -> celsius, door ->
open, plug -> watts, light -> on). OP is one of < <= > >= == !=. VALUE is a
number, open/closed, on/off, true/false.
"""
import operator
import re

from ..util.timeparse import Window

_TOKEN = re.compile(r"\s*(\(|\)|<=|>=|==|!=|<|>|[A-Za-z_][\w.]*|\d{1,2}:\d{2}-\d{1,2}:\d{2}|-?\d+(?:\.\d+)?)")
_OPS = {"<": operator.lt, "<=": operator.le, ">": operator.gt, ">=": operator.ge,
        "==": operator.eq, "!=": operator.ne}
_MAIN_FIELD = {"temp": "celsius", "door": "open", "window": "open", "plug": "watts", "light": "on"}
_WORDS = {"open": True, "closed": False, "on": True, "off": False, "true": True, "false": False}


def _tokens(text):
    pos, out = 0, []
    text = text.strip()
    while pos < len(text):
        m = _TOKEN.match(text, pos)
        if not m:
            raise ValueError(f"bad condition near {text[pos:pos + 12]!r}")
        out.append(m.group(1))
        pos = m.end()
    return out


class _Parser:
    def __init__(self, tokens):
        self.t, self.i = tokens, 0

    def peek(self):
        return self.t[self.i] if self.i < len(self.t) else None

    def take(self, want=None):
        tok = self.peek()
        if tok is None or (want is not None and tok != want):
            raise ValueError(f"expected {want or 'more'}, got {tok!r}")
        self.i += 1
        return tok

    def or_expr(self):
        node = self.and_expr()
        while self.peek() == "or":
            self.take()
            node = ("or", node, self.and_expr())
        return node

    def and_expr(self):
        node = self.not_expr()
        while self.peek() == "and":
            self.take()
            node = ("and", node, self.not_expr())
        return node

    def not_expr(self):
        if self.peek() == "not":
            self.take()
            return ("not", self.not_expr())
        return self.atom()

    def atom(self):
        tok = self.take()
        if tok == "(":
            node = self.or_expr()
            self.take(")")
            return node
        if tok == "time":
            self.take("in")
            return ("time", Window.parse(self.take()))
        name = tok
        op = self.take()
        if op not in _OPS:
            raise ValueError(f"bad operator {op!r}")
        raw = self.take()
        if raw.lower() in _WORDS:
            value = _WORDS[raw.lower()]
        else:
            try:
                value = float(raw)
            except ValueError:
                raise ValueError(f"bad value {raw!r}") from None
        parts = name.split(".")
        if len(parts) == 2:
            parts.append(_MAIN_FIELD.get(parts[0], "value"))
        if len(parts) != 3:
            raise ValueError(f"bad name {name!r}")
        return ("cmp", parts[1], parts[2], op, value)


def parse_condition(text: str):
    """Parse to a tree; ValueError on bad input."""
    p = _Parser(_tokens(text))
    node = p.or_expr()
    if p.peek() is not None:
        raise ValueError(f"unexpected {p.peek()!r}")
    return node


def evaluate(node, states: dict, local_time) -> bool:
    """`states`: {device_id: {field: value}}; `local_time`: datetime.time in the house zone.
    A comparison on a device or field with no known value is False."""
    kind = node[0]
    if kind == "or":
        return evaluate(node[1], states, local_time) or evaluate(node[2], states, local_time)
    if kind == "and":
        return evaluate(node[1], states, local_time) and evaluate(node[2], states, local_time)
    if kind == "not":
        return not evaluate(node[1], states, local_time)
    if kind == "time":
        return node[1].contains(local_time)
    _, dev, field, op, value = node
    have = states.get(dev, {}).get(field)
    if have is None:
        return False
    return _OPS[op](have, value)
