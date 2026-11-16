"""H4: lazy ISTACK answers must fail (family grader AND engine path)."""
import json
import re
import statistics

import pytest

from crucibleforge import gen
from crucibleforge.gen import istack
from crucibleforge.gen._stack_common import join_turns

PARA = ("The evening was quiet and the house was calm. She walked to the window and looked out at the "
        "garden. Nothing much happened, and that was fine with everyone involved.")
SEEDS = [f"L{i}" for i in range(30)]


def _case(level, variant, seed):
    c = {"id": f"LZ-{seed}", "category": "rpdeep",
         "generator": {"type": "istack", "level": level, "variant": variant}}
    gen.materialize_case(c, "4.5.0")
    return c


def _topic(case, i):
    for c in case["checks"]:
        if c.get("note") == "topic" and c["turns"] == [i]:
            return c["pattern"].replace("\\b", "")
    return "topic"


def _subject(case, i):
    m = re.search(r"about ([^.\n]+)\.", case["turns"][i - 1])
    return m.group(1) if m else "it"


def shapes(case):
    n = len(case["turns"])
    ts = [_topic(case, i) for i in range(1, n + 1)]
    ss = [_subject(case, i) for i in range(1, n + 1)]
    return {
        "generic": [PARA] * n,
        "oneword": [t.capitalize() + "." for t in ts],
        "echo": list(case["turns"]),
        "echo+filler": [u + "\n\n" + PARA for u in case["turns"]],
        "topic+generic": [f"Here is a piece about {s}. {PARA} It was all about the {t}." for s, t in zip(ss, ts)],
        "generic30": [" ".join(PARA.split()[:30])] * n,
    }


@pytest.mark.parametrize("variant", ["write", "conv"])
@pytest.mark.parametrize("level", [1, 2, 3, 4, 5, 6])
def test_lazy_shapes_fail(variant, level):
    fam = {k: [] for k in shapes(_case(level, variant, "x"))}
    eng = {k: [] for k in fam}
    for s in SEEDS:
        c = _case(level, variant, s)
        for k, ans in shapes(c).items():
            a = join_turns(ans)
            fam[k].append(istack.grade(a, c))
            eng[k].append(gen.engine_score(c, a))
    for name, d in (("family", fam), ("engine", eng)):
        for k, v in d.items():
            assert statistics.mean(v) < 0.2 and max(v) < 0.35, (name, k, statistics.mean(v), max(v))
