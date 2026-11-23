"""Grader stamps (fix round 2026-11-18, P3; review finding 6): every row carries the hash of the code and data that
graded it; a checker edit makes exactly that family's rows stale; ``report --check-stale`` lists stale rows per model
and exits non-zero. No model is called."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from crucibleforge import api, cli, config, gen
from crucibleforge import grader_stamp as G
from crucibleforge.providers import clear_cache


@pytest.fixture(autouse=True)
def _fresh():
    gen.discover()
    G.clear()
    yield
    G.clear()


def _row(family, **kw):
    return {"case_id": f"{family}-1", "chain": {"family": family}, "grade": "pass",
            "grader_family": family, "grader_stamp": G.stamp(family), **kw}


def test_the_stamp_is_stable_and_differs_per_family():
    assert G.stamp("implied") == G.stamp("implied")
    assert len({G.stamp(f) for f in ("implied", "tracevm", "ledger")}) == 3
    assert G.stamp("static:coding") != G.stamp("static:codebank")


def test_editing_one_familys_checker_makes_only_its_rows_stale(monkeypatch):
    rows = [_row("implied"), _row("tracevm"), _row("ledger")]
    assert G.stale_rows(rows) == []
    # a temp copy of the tree where one character of a HELPER of implied changed (the checker of finding 6 lived
    # in a helper module; chains.revision hashes the family's own file only)
    helper = Path(gen.get("implied").__file__).with_name("_implied_more.py")
    real = G._read
    monkeypatch.setattr(G, "_read", lambda p: real(p) + b"#" if Path(p) == helper else real(p))
    G.clear()
    stale = G.stale_rows(rows)
    assert [r["case_id"] for r, _ in stale] == ["implied-1"]
    assert "implied" in stale[0][1] and "graded under" in stale[0][1]


def test_a_row_without_a_stamp_is_stale_and_an_error_row_is_not_scored():
    assert G.row_is_stale({"chain": {"family": "tracevm"}})[0] is True
    assert G.stale_rows([{"chain": {"family": "tracevm"}, "grade": "error"}]) == []
    ok = _row("tracevm")
    assert G.row_is_stale(ok) == (False, "")


def test_the_runner_stamps_every_row(monkeypatch, tmp_path):
    spec = importlib.util.spec_from_file_location("_cg2", Path(__file__).with_name("test_conformance_gate_20261103.py"))
    cg = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cg)
    cg._FakeClient.bodies = []
    cg._FakeClient.script = cg._default_script
    monkeypatch.setattr(api.httpx, "Client", cg._FakeClient)
    monkeypatch.setattr(config, "RESULTS_DIR", tmp_path)
    clear_cache()
    cg._run(cg._cfg())
    row = cg._rows()[0]
    assert row["grader_family"] == "static:instruct"
    assert row["grader_stamp"] == G.stamp("static:instruct")
    assert G.row_is_stale(row) == (False, "")


def test_report_check_stale_lists_per_model_and_exits_non_zero(tmp_path, monkeypatch, capsys):
    from crucibleforge import report
    boards = {"a": [_row("tracevm")], "b": [_row("tracevm"), {"case_id": "old", "chain": {"family": "ledger"},
                                                               "grade": "fail"}]}
    monkeypatch.setattr(report, "board_filter", lambda label, cfg=None, **k: boards.get(label, []))
    cfg = {"models": [{"name": "a"}, {"name": "b"}], "providers": {}, "defaults": {}}
    args = cli.argparse.Namespace(models=None, public=False, out=None, staged_claude_code=False, profile=None,
                                  check_stale=True)
    assert cli.cmd_report(args, cfg) == 1
    out = capsys.readouterr().out
    assert "b: 1 stale" in out and "old" in out and "a: 0 stale" in out
    boards["b"] = [_row("ledger")]
    assert cli.cmd_report(args, cfg) == 0


def test_the_flag_parses():
    seen = {}
    mp = pytest.MonkeyPatch()
    mp.setattr(cli, "load_config", lambda p=None: {})
    mp.setattr(cli, "cmd_report", lambda a, c, **k: seen.setdefault("a", a) and 0)
    try:
        try:
            cli.main(["report", "--check-stale"])
        except SystemExit:
            pass
    finally:
        mp.undo()
    assert seen["a"].check_stale is True
