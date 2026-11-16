"""E1: the generator registry (crucibleforge/gen/__init__.py)."""
import json
import types

import pytest

from crucibleforge import config, gen, version


def _toy_module():
    m = types.SimpleNamespace(FAMILY="toy", LEVELS={i: {"n": i} for i in range(1, 6)})

    def _word(g, release_seed, case_id):
        return gen.instance_seed("toy", release_seed, case_id, g["level"], g.get("key"))[:8]

    def materialize(g, *, release_seed, case_id):
        w = _word(g, release_seed, case_id)
        return {"prompt": f"Reply with exactly: {w}", "grader": "contains",
                "grader_config": {"needles": [w]}, "reference": w}

    def reference(g, *, release_seed, case_id):
        return _word(g, release_seed, case_id)

    def mutants(g, *, release_seed, case_id):
        return ["nope", _word(g, release_seed, case_id)[:-1]]

    def grade(answer, mat):
        return 1.0 if mat["reference"] in answer else 0.0

    m.materialize, m.reference, m.mutants, m.grade = materialize, reference, mutants, grade
    return m


@pytest.fixture(autouse=True)
def toy():
    gen.register(_toy_module())
    gen.set_release_seed("T1")
    yield
    gen.unregister("toy")
    gen.set_release_seed(None)


def _write(tmp_path, cases):
    (tmp_path / "coding.json").write_text(json.dumps(cases), encoding="utf-8")
    return tmp_path


CASE = {"id": "TOY-1", "generator": {"type": "toy", "level": 2, "key": "k"}}


def test_registry_discovers_and_gets():
    assert "toy" in gen.REGISTRY and gen.get("toy").FAMILY == "toy"
    with pytest.raises(gen.GeneratorError):
        gen.get("no-such-family")
    with pytest.raises(gen.GeneratorError):
        gen.register(types.SimpleNamespace(FAMILY="bad"))


def test_load_cases_materialises(tmp_path):
    cases = config.load_cases(["coding"], cases_dir=_write(tmp_path, [dict(CASE)]))
    c = cases[0]
    assert c["prompt"].startswith("Reply with exactly: ") and c["generator"]["type"] == "toy"
    assert c["id"] == "TOY-1" and c["category"] == "coding"
    again = config.load_cases(["coding"], cases_dir=tmp_path)[0]
    assert again["prompt"] == c["prompt"]                 # deterministic


def test_release_seed_changes_instance(tmp_path):
    _write(tmp_path, [dict(CASE)])
    a = config.load_cases(["coding"], cases_dir=tmp_path)[0]["prompt"]
    gen.set_release_seed("T2")
    b = config.load_cases(["coding"], cases_dir=tmp_path)[0]["prompt"]
    assert a != b


def test_longctx_generator_still_works(tmp_path):
    c = {"id": "NIAH-x", "generator": {"type": "niah", "tokens": 500, "depth": 0.5, "seed": 1,
                                        "needle": "The code is 'abc-1'.", "question": "Code?"},
         "grader": "contains", "grader_config": {"needles": ["abc-1"]}}
    out = config.load_cases(["coding"], cases_dir=_write(tmp_path, [c]))[0]
    assert "abc-1" in out["prompt"] and not gen.is_generated(out)


def test_case_hash_covers_generator_and_release_seed(tmp_path):
    _write(tmp_path, [dict(CASE)])
    h1 = version.case_table(tmp_path)["TOY-1"]["case"]
    assert version.case_table(tmp_path)["TOY-1"]["case"] == h1
    gen.set_release_seed("T2")
    assert version.case_table(tmp_path)["TOY-1"]["case"] != h1
    gen.set_release_seed("T1")
    c2 = dict(CASE, generator={"type": "toy", "level": 3, "key": "k"})
    _write(tmp_path, [c2])
    assert version.case_table(tmp_path)["TOY-1"]["case"] != h1


def test_non_generated_hash_ignores_release_seed(tmp_path):
    _write(tmp_path, [{"id": "PLAIN", "prompt": "hi"}])
    h = version.case_table(tmp_path)["PLAIN"]["case"]
    gen.set_release_seed("T9")
    assert version.case_table(tmp_path)["PLAIN"]["case"] == h


def test_verify_case_passes_and_catches_defects(tmp_path):
    case = dict(CASE, category="coding")
    assert gen.verify_case(case) == []
    mod = gen.get("toy")
    orig = mod.mutants
    mod.mutants = lambda g, **kw: [mod.reference(g, **kw)]          # a mutant that scores 1.0
    try:
        assert any("mutant" in e for e in gen.verify_case(case))
    finally:
        mod.mutants = orig
    calls = []
    orig_m = mod.materialize

    def flaky(g, **kw):
        calls.append(1)
        out = orig_m(g, **kw)
        out["prompt"] += str(len(calls))
        return out
    mod.materialize = flaky
    try:
        assert any("deterministic" in e for e in gen.verify_case(case))
    finally:
        mod.materialize = orig_m


def test_cases_verify_hook(tmp_path):
    from crucibleforge import verify_cases
    case = config.load_cases(["coding"], cases_dir=_write(tmp_path, [dict(CASE)]))[0]
    assert not [e for e in verify_cases._check(case) if "generator" in e]
    mod = gen.get("toy")
    orig = mod.mutants
    mod.mutants = lambda g, **kw: [mod.reference(g, **kw)]
    try:
        assert any("generator mutant" in e for e in verify_cases._check(case))
    finally:
        mod.mutants = orig
