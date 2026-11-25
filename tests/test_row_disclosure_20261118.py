"""Per-row settings disclosure (fix round 2026-11-18, P2; D11, review finding 12): every row records thinking,
effort, reasoning_tokens, finish_reason and cut, hosted and rig alike; a hosted row carries settings_note for what
the registry cannot verify; the publish gate refuses a hosted row with no effort. The generation runs through the
real runner against the conformance tests' HTTP fake: no model, no provider."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from crucibleforge import api, cli, config, disclosure, settings_gate
from crucibleforge.providers import clear_cache

_spec = importlib.util.spec_from_file_location("_cg", Path(__file__).with_name("test_conformance_gate_20261103.py"))
_cg = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_cg)


@pytest.fixture(autouse=True)
def _fake_server(monkeypatch, tmp_path):
    _cg._FakeClient.bodies = []
    _cg._FakeClient.script = _cg._default_script
    monkeypatch.setattr(api.httpx, "Client", _cg._FakeClient)
    monkeypatch.setattr(config, "RESULTS_DIR", tmp_path)
    clear_cache()
    yield
    clear_cache()


def _one_row(cfg):
    summary = _cg._run(cfg)
    assert summary["m"]["rows"] == 1
    return _cg._rows()[0]


def test_a_hosted_row_records_the_effort_it_sent():
    cfg = _cg._cfg(entry={"extra_body": {"reasoning_effort": "high"}, "thinking": True})
    row = _one_row(cfg)
    for k in disclosure.FIELDS:
        assert k in row, k
    assert row["effort"] == "high"
    assert any((b or {}).get("reasoning_effort") == "high" for b in _cg._FakeClient.bodies)  # it WAS sent
    assert row["thinking"] is True
    assert row["cut"] is False and row["finish_reason"] == "stop"
    assert "settings_note" not in row                       # thinking and effort declared: nothing unverifiable


def test_a_hosted_row_without_an_effort_says_provider_default_and_why():
    row = _one_row(_cg._cfg())
    assert row["effort"] == "provider default"
    assert row["thinking"] in (True, False, "unknown")
    assert "reasoning effort" in row["settings_note"] and "thinking mode" in row["settings_note"]


def test_a_rig_row_records_the_fields_too(monkeypatch):
    _cg._fake_rig(monkeypatch)
    cfg = _cg._cfg(provider={"type": "studioforge"})
    row = _one_row(cfg)
    assert {k: k in row for k in disclosure.FIELDS} == {k: True for k in disclosure.FIELDS}
    assert row["effort"] == "provider default" and "settings_note" not in row


def test_the_fields_from_a_row():
    row = {"finish_reason": "length", "max_tokens_sent": 100, "reasoning": "",
           "metrics": {"completion_tokens": 100, "reasoning_tokens": 40, "reasoning_tokens_source": "server"}}
    f = disclosure.row_fields(row, settled_thinking=None, extra_body={"reasoning": {"effort": "low"}})
    assert f == {"thinking": True, "effort": "low", "reasoning_tokens": 40, "finish_reason": "length", "cut": True}
    est = {**row, "finish_reason": "stop", "metrics": {**row["metrics"], "reasoning_tokens_source": "estimated",
                                                        "completion_tokens": 100}}
    f = disclosure.row_fields(est, settled_thinking=False)
    # 4.5.1 (PLAN-451-LIMITS step 2, ruling 3): a reply that says it finished (stop) is not cut, even at the ceiling
    assert f["reasoning_tokens"] is None and f["cut"] is False
    assert disclosure.row_fields({**est, "finish_reason": None})["cut"] is True   # no finish reason: token test
    none = {"finish_reason": "stop", "max_tokens_sent": 100, "metrics": {"completion_tokens": 10}}
    assert disclosure.row_fields(none, settled_thinking=False) == {
        "thinking": False, "effort": "provider default", "reasoning_tokens": None, "finish_reason": "stop",
        "cut": False}
    assert disclosure.row_fields(none)["thinking"] == "unknown"
    assert disclosure.effort_sent({"thinking": {"type": "enabled", "budget_tokens": 8000}}) == "budget:8000"
    assert disclosure.effort_sent({}, cc_effort="high") == "high"


def test_settings_gate_no_longer_waves_a_hosted_model_through_unchecked():
    full = {"name": "h", "provider": "x", "model_id": "h", "thinking": True,
            "extra_body": {"reasoning_effort": "high"}}
    v = settings_gate.check({"defaults": {}}, full, "openai")
    assert v.ok and v.basis == "api-checked" and not v.reason
    v = settings_gate.check({"defaults": {}}, {"name": "h", "provider": "x", "model_id": "h"}, "openai")
    assert v.ok and v.basis == "api" and "not verifiable" in v.reason


def test_the_publish_gate_refuses_a_hosted_row_without_an_effort():
    assert disclosure.publish_refusal({"case_id": "c"}, "openai")
    assert disclosure.publish_refusal({"case_id": "c", "effort": "provider default"}, "openai") is None
    assert disclosure.publish_refusal({"case_id": "c"}, "studioforge") is None


def _board(tmp_path, monkeypatch, rows_by_label):
    monkeypatch.setattr(config, "RESULTS_DIR", tmp_path)
    for label, rows in rows_by_label.items():
        p = tmp_path / f"transcripts_{label}.jsonl"
        p.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    from crucibleforge import report
    monkeypatch.setattr(report, "board_filter", lambda label, cfg=None, **k: rows_by_label.get(label, []))
    return {"providers": {"h": {"type": "openai", "base_url": "http://h.invalid/v1"},
                          "r": {"type": "studioforge", "base_url": "http://r.invalid/v1"}},
            "models": [{"name": "hosted", "provider": "h", "model_id": "H"},
                       {"name": "rig", "provider": "r", "model_id": "R"}],
            "defaults": {}, "judge": {"candidates": []}}


def test_report_public_refuses_while_a_hosted_row_has_no_effort(tmp_path, monkeypatch, capsys):
    cfg = _board(tmp_path, monkeypatch, {"hosted": [{"case_id": "c1", "category": "coding"}],
                                         "rig": [{"case_id": "c1", "category": "coding"}]})
    called = []
    monkeypatch.setattr("crucibleforge.report.generate", lambda *a, **k: called.append(1) or "")
    args = cli.argparse.Namespace(models=None, public=True, out=None, staged_claude_code=False, profile=None, allow_placeholders=True)
    assert cli.cmd_report(args, cfg) == 4 and called == []
    err = capsys.readouterr().err
    assert "hosted" in err and "c1" in err and "effort" in err and "rig:" not in err


def test_the_check_verb_lists_rows_per_model_and_exits_non_zero(tmp_path, monkeypatch, capsys):
    cfg = _board(tmp_path, monkeypatch, {"hosted": [{"case_id": "c1"}, {"case_id": "c2", "effort": "high"}]})
    assert cli.disclosure_problems(cfg, ["hosted", "rig"]) == {"hosted": ["c1"]}
    cfg2 = _board(tmp_path, monkeypatch, {"hosted": [{"case_id": "c2", "effort": "high"}]})
    assert cli.disclosure_problems(cfg2, ["hosted"]) == {}
