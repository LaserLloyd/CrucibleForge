"""Claude Code rows on a STAGED board only (lead 2026-11-14): the ClaudeCodeOnBoard guard
(c7de3c7, Jake 2026-11-12: subscription CLI rows run under Claude Code's own sampling, not
comparable to API rows) stays on every path; an explicit staged-only opt-in renders them as
unranked validation rows, and a publish-shaped render refuses even with the opt-in set."""
from __future__ import annotations

import pytest

from crucibleforge import report

API = {"meta": {"provider": "deepseek", "provider_type": "openai"}, "coverage": {}}
CC = {"meta": {"provider": "claudecode", "provider_type": "claude_code"}, "coverage": {}}


def _stats():
    return {"api": {**API, "coverage": {}}, "cc1": {**CC, "coverage": {}}}


def test_without_the_switch_the_refusal_stands():
    with pytest.raises(report.ClaudeCodeOnBoard, match="cc1"):
        report.assert_no_claude_code(_stats())
    with pytest.raises(report.ClaudeCodeOnBoard, match="cc1"):
        report.assert_no_claude_code(_stats(), {"models": []})
    assert not report.staged_cc_allowed(None) and not report.staged_cc_allowed({})


def test_with_the_switch_the_rows_are_marked_validation_rows():
    cfg = {report.STAGED_CC_KEY: True}
    st = _stats()
    assert report.staged_cc_allowed(cfg)
    report.mark_staged_cc(st, cfg)
    report.assert_no_claude_code(st, cfg)                        # no raise
    assert st["cc1"]["validation_cc"] and not st["api"].get("validation_cc")
    assert st["cc1"]["coverage"]["provisional"] is True          # shown, never ranked or tied
    assert report.STAGED_CC_MARK in report.notes_cell(st["cc1"])
    assert report.STAGED_CC_MARK == "validation row: Claude Code route, default effort; not for publication"
    head = report._suite_head(st, cfg)
    assert any("STAGED board" in h and "not for publication" in h for h in head)


def test_a_publish_shaped_render_refuses_even_with_the_switch():
    for cfg in ({report.STAGED_CC_KEY: True, "_audience": report.PUBLIC},):
        assert not report.staged_cc_allowed(cfg)
        st = _stats()
        report.mark_staged_cc(st, cfg)
        assert not st["cc1"].get("validation_cc")
        with pytest.raises(report.ClaudeCodeOnBoard):
            report.assert_no_claude_code(st, cfg)


def test_hidden_scope_counts_as_publish(monkeypatch):
    monkeypatch.setattr(report, "_hidden", lambda cfg: {"categories": [], "case_ids": [], "terms": []})
    assert not report.staged_cc_allowed({report.STAGED_CC_KEY: True})


def test_html_rows_refuse_without_and_with_publish_cfg():
    with pytest.raises(report.ClaudeCodeOnBoard):
        report.html_rows(["cc1"], {"cc1": CC}, None)
    with pytest.raises(report.ClaudeCodeOnBoard):
        report.html_rows(["cc1"], {"cc1": CC}, {report.STAGED_CC_KEY: True, "_audience": report.PUBLIC})


def test_generate_public_refuses_the_switch(monkeypatch, tmp_path):
    from crucibleforge import config
    monkeypatch.setattr(config, "RESULTS_DIR", tmp_path)
    with pytest.raises(report.ClaudeCodeOnBoard, match="staged"):
        report.generate(None, write=False, public=True, cfg={report.STAGED_CC_KEY: True})


def test_cli_report_refuses_public_with_the_switch(capsys):
    import argparse
    from crucibleforge import cli
    args = argparse.Namespace(public=True, staged_claude_code=True, models=None, out=None)
    assert cli.cmd_report(args, {}) == 2
    assert "staged" in capsys.readouterr().err
