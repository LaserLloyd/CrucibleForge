"""NSFW+ carries no taste points. (1) Every taste name in the profile's ``nsfw_plus.zero_points`` carries 0 points:
none is a scored segment or part. (2) Mark AB5: the module reads no judge mark, and the checks it counts are never the
stock-phrase / repetition deductions."""
from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

from crucibleforge import nsfwplus45

_RAW = yaml.safe_load((Path(__file__).resolve().parent.parent / "profiles" / "bench45.yaml").read_text(encoding="utf-8"))
PROF = {**_RAW, **_RAW["compositions"]["standard"]}   # the composition-merged view the report sees


def _walk(o, out):
    if isinstance(o, dict):
        for k, v in o.items():
            out.append(str(k))
            _walk(v, out)
    elif isinstance(o, list):
        for v in o:
            out.append(str(v))


def test_zero_point_names_are_not_scored_anywhere():
    zero = set(nsfwplus45.zero_point_names(PROF))
    assert {"prose", "voice", "peak_erotic", "sensation", "ngram_repeat"} <= zero
    names = []
    _walk(PROF["compositions"]["standard"]["nsfw_plus"]["numbers"], names)
    assert not (zero & set(names))


def test_the_two_numbers_are_each_out_of_ten():
    for num in PROF["compositions"]["standard"]["nsfw_plus"]["numbers"].values():
        segs = [v["max"] for v in num.values() if isinstance(v, dict) and "max" in v]
        assert sum(segs) == num["of"] == 10

SRC = Path(nsfwplus45.__file__).read_text(encoding="utf-8")
TASTE = ["prose", "voice", "erotic", "real_partner", "peak_erotic", "peak_physical", "sensation_t2", "sensation_t3",
         "sensation_t4", "sensation", "bodies_right", "emotion", "character", "stock_phrase", "ngram_repeat"]


def test_module_reads_no_taste_mark():
    code = re.sub(r'""".*?"""', "", SRC, flags=re.S)
    code = "\n".join(ln.split("#")[0] for ln in code.splitlines())
    for name in TASTE:
        assert not re.search(rf"[\"']{name}[\"']", code), name
    assert not re.search(r"[\"']scores[\"']|[\"']judge[\"']|\bjudge\[", code)


def test_counted_groups_exclude_deductions():
    assert "ded" not in nsfwplus45._PACE_GROUPS


def test_profile_zero_points_names_are_not_read():
    prof = Path(__file__).resolve().parents[1] / "profiles" / "bench45.yaml"
    if not prof.exists():
        pytest.skip("profile fragment is W1's")
    zp = nsfwplus45.zero_point_names(PROF)
    assert zp
    for name in zp:
        assert f'"{name}"' not in SRC
