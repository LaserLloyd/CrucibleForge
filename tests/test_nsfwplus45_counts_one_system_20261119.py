"""One counts system (review S7): the printed counts line comes from the scoring outcomes, has an 'act missing'
bucket, and its buckets add up to the units scored."""
from __future__ import annotations

import re

import pytest

from crucibleforge import gen
from crucibleforge import nsfwplus45 as N
from crucibleforge.gen import adult45

pytestmark = pytest.mark.live_ladder


@pytest.fixture(autouse=True, scope="module")
def _discover():
    gen.discover()


def _units():
    out, ver = [], {}
    for i, it in enumerate(adult45.items("taboo")):
        u = {"id": f"range-{i}", "segment": "range", "card": it["id"], "replies": [it["ref_reply"]]}
        out.append(u)
        ver[u["id"]] = {"cls": "EXPLICIT"}
    return out, ver


def test_act_missing_is_its_own_bucket_and_not_explicit():
    units, ver = _units()
    units[0] = {**units[0], "code": {"acts": False}}
    units[1] = {**units[1], "status": {"cut": True}}
    ver[units[2]["id"]] = {"cls": "SOFTENED", "third_used": True}
    del ver[units[3]["id"]]                                       # not read yet: pending
    c = N.counts(units, ver)["nsfw"]
    assert c["act_missing"] == 1 and c["cut"] == 1 and c["softened"] == 1 and c["pending"] == 1
    assert c["explicit"] == 21 - 4
    buckets = [k for k in N.COUNT_BUCKETS]
    assert sum(c[k] for k in buckets) == c["asks"] == 21


def test_the_printed_line_adds_up():
    units, ver = _units()
    units[0] = {**units[0], "code": {"acts": False}}
    line = N.format_counts(N.counts(units, ver)["nsfw"], "nsfw")
    assert "act missing 1" in line and "explicit 20" in line and "of 21 asks" in line
    nums = [int(x) for x in re.findall(r"(\d+)(?= ·| of)", line.split(" of ")[0] + " of")]
    assert sum(nums) == 21, line


def test_report_block_prints_the_outcome_line(tmp_path):
    from crucibleforge import nsfwplus45 as NP
    units, ver = _units()
    units[0] = {**units[0], "code": {"acts": False}}
    c = NP.counts(units, ver)
    assert set(c["nsfw"]) >= set(NP.COUNT_BUCKETS) | {"asks", "third_reader", "undecided", "hosted"}
