"""Encoding-only change to NETSURGEON and PROBE (test_windows_portability): ``encoding="utf-8"`` on three text-I/O calls moves
both families' source digests (and so their chain revision), but nothing a model sees or a grader uses. The golden digests
below are sha256(json(materialize(...)))[:16] = prompt + grader + grader_config + meta, taken on the integration tip f978d8b
before the change; they must stay byte-identical."""
import hashlib
import json

import pytest

from crucibleforge.gen import netsurgeon as ns
from crucibleforge.gen import probe as pr
from crucibleforge.gen import _netsurgeon_ff as nff

#: (family module, level, key) -> digest at f978d8b
GOLD = {
    ("netsurgeon", 1, "a"): "da4b80a40143508f",
    ("netsurgeon", 1, "b"): "dbabf7f71cccdba8",
    ("netsurgeon", 1, "c"): "3b79e9afa588c182",
    ("probe", 3, "a"): "5f1ceb22855a8822",
    ("probe", 3, "b"): "dd7ed6236a2a02ae",
    ("probe", 3, "c"): "b0d06e5aabb5029f",
}
MODS = {"netsurgeon": ns, "probe": pr}


@pytest.mark.parametrize("fam,level,key", sorted(GOLD))
def test_rendered_instance_is_byte_identical_to_before_the_encoding_change(fam, level, key):
    m = MODS[fam].materialize({"type": fam, "level": level, "key": key}, release_seed="4.5.0", case_id="X")
    assert set(m) == {"difficulty", "grader", "grader_config", "max_tokens", "meta", "prompt", "temperature"}
    assert hashlib.sha256(json.dumps(m, sort_keys=True, default=str).encode()).hexdigest()[:16] == GOLD[(fam, level, key)]


def test_sandbox_nn_source_differs_only_by_the_encoding_argument():
    # the nn scaffold text is shipped into the grading sandbox; the harness replaces nn.load, so open() is never called there
    old = nff.NN_SRC.replace('open(path, encoding="utf-8")', "open(path)")
    assert old != nff.NN_SRC and old.count("open(path)") == 1
    assert "_nn.load = lambda" in ns._HARNESS
