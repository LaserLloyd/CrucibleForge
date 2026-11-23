"""profiles._yaml_file hands every caller its own copy: the fast structural copy must equal copy.deepcopy, never share
a container with the cache, and fall back to deepcopy for aliases and non-plain values (resolution45 profile, 2026-11-20)."""
from __future__ import annotations

import copy

from crucibleforge import profiles as P


def _load(tmp_path, text):
    p = tmp_path / "x.yaml"
    p.write_text(text, encoding="utf-8")
    return p


def test_plain_tree_copy_equals_deepcopy_and_is_independent(tmp_path):
    p = _load(tmp_path, "a: [1, 2, {b: x}]\nc: {d: 1.5, e: null, f: true}\n")
    first = P._yaml_file(p)
    assert first == copy.deepcopy(P._YAML_CACHE[next(iter(k for k in P._YAML_CACHE if k[0] == str(p)))])
    first["a"][2]["b"] = "changed"
    first["a"].append(9)
    assert P._yaml_file(p) == {"a": [1, 2, {"b": "x"}], "c": {"d": 1.5, "e": None, "f": True}}


def test_alias_keeps_its_sharing_through_deepcopy(tmp_path):
    p = _load(tmp_path, "a: &s {k: 1}\nb: *s\n")
    out = P._yaml_file(p)
    assert out["a"] is out["b"]                      # deepcopy preserves the alias, so the fast path is not used


def test_a_date_value_falls_back_to_deepcopy(tmp_path):
    p = _load(tmp_path, "d: 2026-11-20\nl: [1]\n")
    out = P._yaml_file(p)
    assert str(out["d"]) == "2026-11-20" and out["l"] == [1]
    key = next(k for k in P._YAML_PLAIN if k[0] == str(p))
    assert P._YAML_PLAIN[key] is False


def test_materialize_cache_is_off_by_default_and_gives_equal_independent_copies(monkeypatch):
    from crucibleforge import gen
    calls = []

    class Fam:
        @staticmethod
        def materialize(g, release_seed, case_id):
            calls.append(case_id)
            return {"prompt": f"{g['k']}-{release_seed}-{case_id}", "tests": [1, 2]}
    fam, g = Fam(), {"type": "t", "k": "a"}
    gen.enable_materialize_cache(False)
    gen._cached_materialize(fam, g, "s", "c")
    gen._cached_materialize(fam, g, "s", "c")
    assert len(calls) == 2                                   # off: every call renders (tests patch internals)
    gen.enable_materialize_cache(True)
    try:
        a = gen._cached_materialize(fam, g, "s", "c")
        a["tests"].append(99)
        b = gen._cached_materialize(fam, g, "s", "c")
        assert len(calls) == 3 and b == {"prompt": "a-s-c", "tests": [1, 2]}
        gen._cached_materialize(fam, g, "s2", "c")           # another release seed is another key
        gen._cached_materialize(fam, {"type": "t", "k": "b"}, "s", "c")
        assert len(calls) == 5
    finally:
        gen.enable_materialize_cache(False)
