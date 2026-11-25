"""Bonus packs add on top (owner 2026-11-25, R37/R38): no rendered 4.5 text may still state the withdrawn rule, and a
total (standard + bonus) is never printed under a fixed "/20" or "/10" ceiling. Column heads over a total carry the
maximum with packs from the data ("Overall /34"); the standard ceiling stays only where no row has a total."""
from __future__ import annotations

import re
from pathlib import Path

from crucibleforge import report

OLD_PHRASES = ("apart from NSFW and Uncensored", "never part of Overall or of the Code and Chat",
               "Overall /20 = Code /10 + Chat /10", "NSFW+ is one number out of 20", "NSFW+ /20 = NSFW /10",
               "NSFW+ shown apart")
#: a figure followed by a fixed ceiling it exceeds: "21.6 /20", "24.0 of 20", "10.5 / 10"
OVER = re.compile(r"(\d+(?:\.\d+)?)\s*(?:/|of)\s*(20|10)\b")


def _g(total, mx):
    return {"total": total, "standard": total - 1.0, "bonus": 1.0, "max": 10.0, "max_with_packs": mx,
            "packs": [], "owed": []}


def _stats():
    above = {"totals45": {"code": _g(13.0, 17.0), "chat": _g(11.0, 17.0), "overall": _g(24.0, 34.0)},
             "nsfw_plus": {"numbers": {"nsfw": {"value": 9.5, "of": 10}, "uncensored": {"value": 9.0, "of": 10}},
                           "totals45": {"nsfw": _g(10.5, 12.0), "uncensored": _g(10.0, 12.0),
                                        "nsfw_plus": _g(20.5, 24.0)}}}
    plain = {"totals45": {"code": _g(8.0, 17.0), "chat": _g(7.0, 17.0), "overall": _g(15.0, 34.0)}}
    return {"big": above, "small": plain}


def _no_total_over_a_fixed_ceiling(text):
    for m in OVER.finditer(text):
        assert float(m.group(1)) <= float(m.group(2)), m.group(0)


def test_heads_over_a_total_use_the_maximum_with_packs():
    st = _stats()
    assert report.head45("Overall", "overall", st, 20) == "Overall /34"
    assert report.head45("Code", "code", st, 10) == "Code /17"
    assert report.head45("NSFW+", "nsfw_plus", st, 20) == "NSFW+ /24"
    # rows with different maxima: no ceiling at all rather than a wrong one
    st["small"]["totals45"]["overall"]["max_with_packs"] = 30.0
    assert report.head45("Overall", "overall", st, 20) == "Overall"
    # no row with a total (an older suite): the standard ceiling
    assert report.head45("Overall", "overall", {"x": {}}, 20) == "Overall /20"


def test_the_adult_section_renders_without_the_withdrawn_rule_or_a_fixed_ceiling():
    st = _stats()
    md = "\n".join(report.nsfw_plus_md(["big"], st))
    html = report.nsfw_plus_html(["big"], st)
    for text in (md, html):
        assert "NSFW+ /24" in text and "NSFW /12" in text
        assert not any(p in text for p in OLD_PHRASES)
        _no_total_over_a_fixed_ceiling(text)


def test_the_board_key_states_the_rule_in_force():
    st = _stats()
    text = " ".join(report.seg45_legend(["big", "small"], st)) + report.PACKS_NOTE45 + report.NSFW_PLUS_INTRO
    assert not any(p in text for p in OLD_PHRASES)
    assert "add on top" in text and "Overall = Code total + Chat total" in text


def test_every_45_board_head_over_a_total_comes_from_head45():
    """The scorecard, HTML board and adult tables take their heads from head45; a hard-coded "Overall /20",
    "NSFW+ /20", "Code /10" or "Chat /10" head left in the renderer would print a total under the old ceiling. The
    4.0 HTML_LEGEND (older suites, locked) is the only allowed holder of such text."""
    src = Path(report.__file__).read_text(encoding="utf-8")
    start = src.index("HTML_LEGEND = [")
    src = src[:start] + src[src.index("]\n", start):]
    code = "\n".join(ln for ln in src.splitlines() if not ln.lstrip().startswith("#"))
    for head in ('"Overall /20"', '"NSFW+ /20"', '"Code /10"', '"Chat /10"', "<th>NSFW+ /20</th>",
                 "<th>NSFW /10</th>", '"Overall /20 if'):
        assert head not in code, head
