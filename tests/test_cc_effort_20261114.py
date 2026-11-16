"""Claude Code effort (lead 2026-11-14, Jake's decision to the lead): the route passes an explicit
``--effort`` (models-file ``effort:``, else $CRUCIBLEFORGE_CC_EFFORT, else ``high``), records it on every
row and in the meta; a row without it reads as ``default``. The regrade and seed-alias guards treat a
different effort as a different run setting; the staged board's validation-row note names the effort."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from crucibleforge import claudecode, config, regrade, report, runner

_spec = importlib.util.spec_from_file_location("_cc", Path(__file__).with_name("test_claude_code_provider_20261111.py"))
_cc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_cc)
fake = _cc.fake


def test_effort_from_entry_then_env_then_high(monkeypatch):
    monkeypatch.delenv(claudecode.EFFORT_ENV, raising=False)
    assert claudecode.effort_for({}) == "high"
    monkeypatch.setenv(claudecode.EFFORT_ENV, "medium")
    assert claudecode.effort_for({}) == "medium"
    assert claudecode.effort_for({"effort": "max"}) == "max"
    with pytest.raises(config.ConfigError, match="effort 'turbo'"):
        claudecode.effort_for({"effort": "turbo"})


def test_the_cli_is_passed_the_effort(fake):
    p = _cc._prov()
    p.chat("claude-haiku-5-5", [{"role": "user", "content": "x"}], max_tokens=5, cc_effort="high")
    a = fake()[0]["argv"]
    assert a[a.index("--effort") + 1] == "high"
    assert "--effort" not in claudecode.build_argv("claude", "m", "s", resume=None)   # nothing given, nothing sent


def test_the_run_context_stamps_every_row_and_sends_it(fake, monkeypatch):
    monkeypatch.delenv(claudecode.EFFORT_ENV, raising=False)
    prov = _cc._prov()
    ctx = runner._Ctx({"defaults": {}}, {"model_id": "claude-haiku-5-5", "name": "m-cc", "effort": "xhigh"},
                      prov, None)
    assert ctx.regime_stamp()["cc_effort"] == "xhigh"
    ctx._chat([{"role": "user", "content": "x"}], max_tokens=5)
    a = fake()[-1]["argv"]
    assert a[a.index("--effort") + 1] == "xhigh"


def test_an_api_run_has_no_effort_field():
    class P:
        type, name, reasoning_replay = "openai", "x", False
    ctx = runner._Ctx({"defaults": {}}, {"model_id": "m", "name": "m"}, P(), None)
    assert "cc_effort" not in ctx.regime_stamp() and ctx.cc_effort is None


def test_the_meta_records_the_effort(tmp_path, monkeypatch, fake):
    monkeypatch.setattr(runner, "results_dir", lambda: tmp_path)
    monkeypatch.delenv(claudecode.EFFORT_ENV, raising=False)
    runner._write_meta("m-cc", {"model_id": "claude-haiku-5-5"}, _cc._prov(), reset=True)
    assert json.loads((tmp_path / "meta_m-cc.json").read_text(encoding="utf-8"))["cc_effort"] == "high"


def test_a_row_without_the_field_reads_default():
    assert claudecode.row_effort({}) == "default" and claudecode.row_effort({"cc_effort": "high"}) == "high"


def test_regrade_refuses_an_attempt_whose_rows_ran_at_different_efforts():
    assert regrade.effort_refusal([{"cc_effort": "high"}, {}]) == \
        "the attempt's rows ran at different efforts (default, high)"
    assert regrade.effort_refusal([{"cc_effort": "high"}, {"cc_effort": "high"}]) is None
    assert regrade.effort_refusal([{}, {}]) is None


def test_the_alias_guard_refuses_a_row_at_another_effort():
    row = {"chain": {"id": "u"}, "case_id": "u"}
    assert "ran at effort default, this model's rows at high" in report.alias_refusal(row, {"id": "p"}, "high")


def test_with_seed_aliases_pools_only_same_effort_rows(monkeypatch):
    monkeypatch.setattr(report, "seed_aliases", lambda prof: {"S1": {"id": "PK-1"}})
    seen = []
    monkeypatch.setattr(report, "alias_refusal", lambda r, lc, effort=None: seen.append(effort) or None)
    monkeypatch.setattr(report, "alias_row", lambda r, lc: {**r, "seed_alias": True})
    rows = [{"case_id": "C1", "ts": "2", "cc_effort": "high"}, {"case_id": "S1", "chain": {"id": "S1"}, "ts": "1"}]
    out = report._with_seed_aliases(rows, {}, {})
    assert seen == ["high"] and out[1]["seed_alias"]


def test_the_staged_note_names_the_effort():
    st = {"cc1": {"meta": {"provider_type": "claude_code", "cc_effort": "high"}, "coverage": {}},
          "cc0": {"meta": {"provider_type": "claude_code"}, "coverage": {}}}
    cfg = {report.STAGED_CC_KEY: True}
    report.mark_staged_cc(st, cfg)
    assert "validation row: Claude Code route, high effort; not for publication" in report.notes_cell(st["cc1"])
    assert report.STAGED_CC_MARK in report.notes_cell(st["cc0"])        # old rows: default effort
