"""CIPHERCHAT (lab card EXP-20261114-26): unique answers, reference 1.0, floors, parser tolerance, step examples."""
import pytest

from crucibleforge.gen import cipherchat as C
from crucibleforge.gen._stack_common import join_turns

RS = "4.5.0"


def _gen(lv, key):
    return {"type": "cipherchat", "level": lv, "key": key}


@pytest.mark.parametrize("lv", [1, 2, 3, 4])
@pytest.mark.parametrize("key", ["s1", "s2"])
def test_reference_and_floors(lv, key):
    cid = f"T-cc-L{lv}-{key}"
    m = C.materialize(_gen(lv, key), release_seed=RS, case_id=cid)
    assert C.grade(C.reference(_gen(lv, key), release_seed=RS, case_id=cid), m) == 1.0
    p = C._plan(_gen(lv, key), RS, cid)
    for k in ("echo", "nearest-copy", "best-one-step"):
        assert C.grade(join_turns(C.floor_replies(p, k)), m) <= 0.3, (lv, key, k)
    # skill-partial strategies (solve the newest setting only / pool all settings) stay well below 1
    for k in ("pooled", "latest-setting"):
        assert C.grade(join_turns(C.floor_replies(p, k)), m) <= 0.7, (lv, key, k)


@pytest.mark.parametrize("key", ["s1", "s2", "s3"])
def test_every_asked_word_is_determined(key):
    p = C._plan(_gen(3, key), RS, f"T-cc-u-{key}")
    sp = C._Space(sorted({w for tr in p["turns"] for w, _ in tr["new"]} | {w for tr in p["turns"] for _, w, _ in tr["q"]}))
    for tr in p["turns"]:
        for ss, w, a in tr["q"]:
            idx = sp.consistent(p["ev"][ss])
            assert sp.answers(idx, w) == {a}


def test_step_examples_match_definitions():
    S = dict(C.STEPS)
    assert S["reverse"]("STONE") == "ENOTS" and C._shift("ZOO", 2) == "BQQ" and C._rot("STONE", 2) == "ONEST"
    assert C._swap("STONE") == "TSNOE" and C._vow("STONE") == "STUNI" and C._atbash("STONE") == "HGLMV"
    assert C._prog("STONE") == "SUQQI" and S["droplast"]("STONE") == "STON" and S["doublefirst"]("STONE") == "SSTONE"
    for line in ("REVERSE", "SHIFT", "ROTATE", "SWAP", "VOWEL", "MIRROR", "RAMP", "DROPLAST", "DOUBLEFIRST"):
        assert f"- {line}" in C.STEP_TEXT


def test_parser_tolerance():
    txt = '1. **STONE** -> ENOTS\n- "ZOO" => BQQ\n> `TIGER`: REGIT\nPLANT → TNALP\nnot a line\nMAPLE on setting C -> ELPAM\nCLOUD (setting B): DUOLC'
    got = C.parse_answers(txt)
    assert got == {"STONE": "ENOTS", "ZOO": "BQQ", "TIGER": "REGIT", "PLANT": "TNALP", "MAPLE": "ELPAM",
                   "CLOUD": "DUOLC"}
