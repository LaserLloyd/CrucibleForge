"""Pack points cell: the maximum is the points of every pack set that opened (engine ladder sets at the top of the
range, the Fixes pack, the Chat packs), and the printed points never exceed it."""
from __future__ import annotations

from crucibleforge import report

UNLOCK = 0.9
DEFS = [{"key": "work", "label": "Code", "parts": [{"key": "c2_predict", "weight": 1.25}]},
        {"key": "chat", "label": "Chat", "parts": [{"key": "h6_know", "weight": 0.5}]}]


def _card():
    return {
        "work": {
            "c2_predict": {"sets": [{"n": 1, "level": 1, "share": 0.347, "points": 1.0, "state": "scored"}]},
            "c1_implied": {"sets": [{"n": 1, "level": 1, "share": 0.92, "points": 1.0, "state": "locked"}]},
            "c3_fix": {"sets": [{"n": 3, "level": 1, "share": 0.91, "points": 1.0, "state": "locked"}]},
        },
        "chat": {"h6_know": {"sets": [{"n": 3, "level": 1, "share": 0.94, "points": 1.0, "state": "scored"}]}},
    }


def test_pack_max_counts_every_opened_pack():
    eng = {"work/c2_predict": {"gate_top": 1.0}, "work/c1_implied": {"gate_top": 0.89}, "chat/h6_know": {"gate_top": 2.4}}
    fix = {"state": "scored", "points": 0.91, "sets": {3: "scored"}}
    cp = {"remembers": {"state": "scored", "points": 0.6, "judged": 0.6, "code": None},
          "character": {"state": "locked", "points": 0.0},
          "cowrites": {"state": "scored", "points": 1.8, "judged": 0.95, "code": 0.85}}
    assert report.pack_max45("work", _card(), eng, fix, None, UNLOCK) == 2.0     # predict L1 + the Fixes pack
    assert report.pack_max45("chat", _card(), eng, None, cp, UNLOCK) == 4.0      # know L3 + remembers 1 + cowrites 2
    assert report.pack_max45("work", _card(), eng, {"state": "locked", "points": 0.0, "sets": {3: "locked"}},
                             None, UNLOCK) == 1.0


def test_cell_prints_points_of_the_opened_maximum():
    hl = {"S": {"work": 9.1, "chat": 9.9}, "T1": {"work": 7.8123, "chat": 8.2932}, "m": {"work": 1.11, "chat": 0.95},
          "P": {"work/c2_predict": 1.49, "chat/h6_know": 1.44},
          "unscaled": {"work": {"of": 2.25}, "chat": {"of": 1.5}},
          "pack_of": {"work": 2.0, "chat": 2.0}}
    cell = report.packs_cell45(hl, DEFS)
    assert cell == "Code +1.3 of 2 (total 9.1 ± 1.1); Chat +1.6 of 2 (total 9.9 ± 0.9)"


def test_cell_never_prints_points_over_the_maximum():
    hl = {"S": {"work": 9.1}, "T1": {"work": 7.8123}, "m": {}, "P": {"work/c2_predict": 1.49},
          "unscaled": {"work": {"of": 2.25}}, "pack_of": {"work": 1.0}}
    cell = report.packs_cell45(hl, DEFS[:1])
    assert cell.startswith("+1.0 of 1 ")


def test_cell_reads_the_unrounded_total():
    """S is rounded to 0.1; the pack points come from the unrounded total P (8.6935 + 1.2512)."""
    hl = {"S": {"work": 9.9}, "T1": {"work": 8.6935}, "m": {"work": 1.1}, "P": {"work": 9.9447, "work/c2_predict": 1.49},
          "unscaled": {"work": {"of": 2.25}}, "pack_of": {"work": 2.0}}
    assert report.packs_cell45(hl, DEFS[:1]).startswith("+1.3 of 2 ")
