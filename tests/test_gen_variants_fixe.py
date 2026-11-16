"""H2: unknown variants are errors; the istack "nsfw" variant draws its frame from a bank (tiny SFW fixture here)."""
import json
import statistics

import pytest

from crucibleforge import gen, ladder
from crucibleforge.config import ConfigError
from crucibleforge.gen import istack, session_state
from crucibleforge.gen._stack_common import join_turns

FRAMES = [{"id": f"F{i}", "frame": f"Write a warm scene about {a}, {20 + i}, and {b}, {30 + i}, two neighbours "
           f"who share a garden fence and a pot of tea.",
           "characters": [{"name": a, "age": 20 + i}, {"name": b, "age": 30 + i}]}
          for i, (a, b) in enumerate([("Nora", "Pavel"), ("Iris", "Tomas"), ("Mara", "Joel"), ("Lena", "Hugo")])]


@pytest.fixture
def bank(tmp_path):
    p = tmp_path / "frames.json"
    p.write_text(json.dumps({"kind": "x", "frames": FRAMES}), encoding="utf-8")
    return str(p)


def test_unknown_variant_materialize_errors():
    for fam in (istack, session_state):
        with pytest.raises(gen.GeneratorError, match="unknown variant"):
            fam.materialize({"type": fam.FAMILY, "level": 1, "variant": "Write"}, release_seed="r", case_id="V")
    case = {"id": "V", "category": "rpdeep", "generator": {"type": "keyfunc", "level": 1, "variant": "conv"}}
    with pytest.raises(gen.GeneratorError, match="no variants"):
        gen.materialize_case(case, "r")
    case = {"id": "V", "category": "rpdeep", "generator": {"type": "istack", "level": 1, "variant": "typo"}}
    with pytest.raises(gen.GeneratorError):
        gen.materialize_case(case, "r")
    assert "typo" in "\n".join(gen.verify_case(case, "r"))


def test_unknown_variant_in_ladder_set(tmp_path):
    f = tmp_path / "cat" / "part" / "set-1.json"
    f.parent.mkdir(parents=True)
    f.write_text(json.dumps({"category": "rpdeep", "generator": {"family": "istack", "variant": "nsfw_x"}}),
                 encoding="utf-8")
    with pytest.raises(ConfigError, match="unknown variant"):
        ladder._read_set(f)


def test_variants_declared():
    assert set(istack.VARIANTS) == {"write", "conv", "nsfw"} and set(session_state.VARIANTS) == {"state", "multi", "multi_hard"}


def test_nsfw_variant(bank):
    g = {"type": "istack", "level": 3, "variant": "nsfw", "frames": bank}
    seen = set()
    for i in range(12):
        cid = f"N{i}"
        m = istack.materialize(g, release_seed="r", case_id=cid)
        assert m == istack.materialize(g, release_seed="r", case_id=cid)
        fr = next(f for f in FRAMES if f["id"] == m["meta"]["frame"])
        seen.add(fr["id"])
        assert len(m["turns"]) == 1 and fr["frame"] in m["turns"][0]      # frame verbatim, ages included
        assert m["characters"] == fr["characters"]
        ref = istack.reference(g, release_seed="r", case_id=cid)
        assert istack.grade(ref, m) == 1.0 and gen.engine_score({**m, "id": cid}, ref) == 1.0
        for x in istack.mutants(g, release_seed="r", case_id=cid):
            assert istack.grade(x, m) < 1.0
        assert istack.grade("", m) == 0.0
        assert istack.grade(m["turns"][0], m) < 0.2
    assert len(seen) >= 3


def test_nsfw_lazy(bank):
    para = ("The evening was quiet and the house was calm. She walked to the window and looked out at the "
            "garden. Nothing much happened, and that was fine with everyone involved.")
    sc = []
    for i in range(30):
        g = {"type": "istack", "level": 2, "variant": "nsfw", "frames": bank}
        m = istack.materialize(g, release_seed="r", case_id=f"Z{i}")
        sc += [istack.grade(para, m), istack.grade(m["turns"][0] + "\n\n" + para, m)]
    assert statistics.mean(sc) < 0.2 and max(sc) < 0.35


def test_nsfw_missing_bank_is_a_clear_error(tmp_path):
    with pytest.raises(gen.GeneratorError, match="framing bank not found"):
        istack.materialize({"type": "istack", "level": 1, "variant": "nsfw", "frames": str(tmp_path / "no.json")},
                           release_seed="r", case_id="M")
