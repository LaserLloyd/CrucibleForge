"""A meta with ``publishable: false`` (early stop, 2026-11-13) is flagged on staged / local boards and
never reaches the public board."""
import json

import pytest

from crucibleforge import report


@pytest.fixture
def board(tmp_path, monkeypatch):
    monkeypatch.setattr(report, "results_dir", lambda: tmp_path)
    monkeypatch.setattr(report, "_tier_profile", lambda cfg: None)
    monkeypatch.setattr(report, "board_view", lambda label, cfg: ([{"ts": "t", "case_id": "c"}], {"old": False}))
    monkeypatch.setattr(report, "tier_model_stats", lambda *a, **k: {"coverage": {}})
    monkeypatch.setattr(report, "_packs_pending_provisional", lambda st, cfg: None)
    monkeypatch.setattr(report, "row_facts", lambda *a, **k: {})
    monkeypatch.setattr(report, "room_stats", lambda *a, **k: {})
    monkeypatch.setattr(report, "_cards", lambda *a, **k: None)
    monkeypatch.setattr(report, "_headline", lambda *a, **k: None)
    monkeypatch.setattr(report, "assert_no_claude_code", lambda *a, **k: None)
    # the stub rows carry no grader stamps; the stale/undisclosed hold has its own tests (lane-t hooks file)
    monkeypatch.setattr(report, "held45", lambda *a, **k: [])
    for label, meta in {"good": {}, "stopped": {"publishable": False,
                                                 "early_stop": {"reason": "token-limit"}}}.items():
        (tmp_path / f"meta_{label}.json").write_text(json.dumps({"model_label": label, **meta}), encoding="utf-8")
    return monkeypatch


def test_staged_board_flags_an_unpublishable_run(board):
    board.setattr(report, "_hidden", lambda cfg: False)
    labels, stats, dropped = report.board_stats("good,stopped", {})
    assert labels == ["good", "stopped"] and dropped == 0
    assert stats["stopped"]["provisional"] is True
    assert stats["stopped"]["provisional_reason"] == "not publishable: early stop (token-limit)"
    assert "not_publishable" not in stats["good"]


def test_public_board_leaves_an_unpublishable_run_off(board):
    board.setattr(report, "_hidden", lambda cfg: True)
    labels, stats, dropped = report.board_stats("good,stopped", {})
    assert labels == ["good"] and dropped == 1
