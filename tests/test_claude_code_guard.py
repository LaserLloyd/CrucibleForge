"""A claude_code-provider model never reaches a board or report (Jake 2026-11-12)."""
import pytest

from crucibleforge import report

API = {"meta": {"provider": "deepseek", "provider_type": "openai"}}
CC_TYPE = {"meta": {"provider": "claudecode", "provider_type": "claude_code"}}
CC_SIG = {"meta": {"provider": "cc", "cost_basis": "subscription", "sampling": "harness-default"}}


def test_labels_found_by_type_and_by_signature():
    stats = {"api": API, "cc1": CC_TYPE, "cc2": CC_SIG}
    assert report.claude_code_labels(stats) == ["cc1", "cc2"]
    assert report.claude_code_labels({"api": API, "empty": {}}) == []


def test_api_only_passes_and_claude_code_raises():
    report.assert_no_claude_code({"api": API})
    with pytest.raises(report.ClaudeCodeOnBoard, match="cc1"):
        report.assert_no_claude_code({"api": API, "cc1": CC_TYPE})
    assert issubclass(report.ClaudeCodeOnBoard, RuntimeError)


def test_html_rows_refuses_claude_code():
    with pytest.raises(report.ClaudeCodeOnBoard):
        report.html_rows(["cc1"], {"cc1": CC_TYPE}, None)


def test_board_stats_refuses_claude_code(monkeypatch, tmp_path):
    """The real path: a results dir whose meta says claude_code cannot produce a board."""
    import json
    from crucibleforge import config
    monkeypatch.setattr(report, "board_view", lambda label, cfg: ([{"case_id": "x"}], {"old": False}))
    monkeypatch.setattr(report, "load_meta", lambda label: {"provider_type": "claude_code", "provider": "claudecode"})
    monkeypatch.setattr(report, "_hidden", lambda cfg: None)
    monkeypatch.setattr(report, "_tier_profile", lambda cfg: {})
    monkeypatch.setattr(report, "tier_model_stats", lambda label, cfg, rows, meta, era: {"meta": meta, "coverage": {}})
    monkeypatch.setattr(report, "_packs_pending_provisional", lambda *a, **k: None)
    monkeypatch.setattr(report, "row_facts", lambda *a, **k: {})
    monkeypatch.setattr(report, "room_stats", lambda *a, **k: {})
    monkeypatch.setattr(report, "_cards", lambda *a, **k: None)
    monkeypatch.setattr(report, "_headline", lambda *a, **k: None)
    with pytest.raises(report.ClaudeCodeOnBoard):
        report.board_stats("cc1", None)
