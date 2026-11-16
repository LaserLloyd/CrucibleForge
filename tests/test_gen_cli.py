"""crucibleforge gen <family> --seed S --level L [--variant V] --show | --check"""
import pytest

from crucibleforge import cli, gen

FAMS = [("keyfunc", None), ("tracevm", None), ("ledger", None), ("istack", "conv"), ("session_state", "multi")]


def _run(capsys, *argv):
    try:
        rc = cli.main(["gen", *argv])
    except SystemExit as e:
        rc = e.code
    return rc or 0, capsys.readouterr().out


@pytest.mark.parametrize("fam,var", FAMS)
def test_show_prints_prompt_and_level_params(capsys, fam, var):
    lv = 2 if fam != "ledger" else 1
    args = [fam, "--seed", "s1", "--level", str(lv), "--show"] + (["--variant", var] if var else [])
    rc, out = _run(capsys, *args)
    assert rc == 0
    assert fam in out and "level" in out
    mat = gen.materialize_case({"id": "GEN", "category": "x", "generator": {"type": fam, "level": lv, **({"variant": var} if var else {})}}, "s1")
    first = (mat.get("prompt") or (mat.get("turns") or [""])[0]).strip().splitlines()[0]
    assert first in out
    # deterministic
    assert _run(capsys, *args)[1] == out


def test_check_passes_for_a_good_family(capsys):
    rc, out = _run(capsys, "tracevm", "--seed", "s1", "--level", "2", "--check")
    assert rc == 0 and "OK" in out


def test_check_fails_rc1_when_a_mutant_scores_one(capsys, monkeypatch):
    mod = gen.get("tracevm")
    monkeypatch.setattr(mod, "mutants", lambda g, **kw: [mod.reference(g, **kw)])
    rc, out = _run(capsys, "tracevm", "--seed", "s1", "--level", "2", "--check")
    assert rc == 1 and "mutant" in out


def test_unknown_family_and_level_are_errors(capsys):
    assert _run(capsys, "nope", "--seed", "1", "--level", "1", "--show")[0] != 0
    assert _run(capsys, "tracevm", "--seed", "1", "--level", "99", "--show")[0] != 0
