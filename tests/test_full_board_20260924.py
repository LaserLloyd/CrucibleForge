"""The board written at the end of `all` lists every model, not just this run's."""
import types

from crucibleforge import cli, report


def _capture(monkeypatch):
    seen = []
    monkeypatch.setattr(report, "generate", lambda models, write=True: seen.append(models) or "")
    monkeypatch.setattr(report, "scorecard_section", lambda md: "")
    return seen


def test_all_writes_the_full_board(monkeypatch):
    seen = _capture(monkeypatch)
    cli.cmd_report(types.SimpleNamespace(models="joyfox-35b-rp"), {}, full_board=True)
    assert seen == [None]


def test_report_models_filter_still_works(monkeypatch):
    seen = _capture(monkeypatch)
    cli.cmd_report(types.SimpleNamespace(models="joyfox-35b-rp"), {})
    assert seen == ["joyfox-35b-rp"]
