"""H5: `report --public` refuses to render while a {{MEASURED: ...}} placeholder is left in README, SCORING or RUNNING."""
import argparse

from crucibleforge import cli, report


def test_the_scan_finds_placeholders_across_lines(tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "README.md").write_text("a `{{MEASURED: one}}` b", encoding="utf-8")
    (tmp_path / "docs" / "SCORING.md").write_text("x {{MEASURED: two\nlines}} y", encoding="utf-8")
    (tmp_path / "docs" / "RUNNING.md").write_text("clean", encoding="utf-8")
    assert report.doc_placeholders(tmp_path) == [("README.md", "{{MEASURED: one}}"),
                                                 ("docs/SCORING.md", "{{MEASURED: two lines}}")]


def test_public_report_refuses_while_a_placeholder_remains(monkeypatch, capsys):
    monkeypatch.setattr(report, "doc_placeholders", lambda root=None: [("README.md", "{{MEASURED: x}}")])
    called = []
    monkeypatch.setattr(report, "generate", lambda *a, **k: called.append(1) or "md")
    args = argparse.Namespace(public=True, models=None, profile="bench45", out=None, check_stale=False,
                              staged_claude_code=False)
    assert cli.cmd_report(args, {}, full_board=True) == 5
    assert "README.md: {{MEASURED: x}}" in capsys.readouterr().err and not called


def test_allow_placeholders_lets_the_render_go_on(monkeypatch):
    monkeypatch.setattr(report, "doc_placeholders", lambda root=None: [("README.md", "{{MEASURED: x}}")])
    monkeypatch.setattr(cli, "disclosure_problems", lambda cfg, labels: {})
    monkeypatch.setattr(report, "generate", lambda *a, **k: "md")
    monkeypatch.setattr(report, "scorecard_section", lambda md: "card")
    args = argparse.Namespace(public=True, models=None, profile="bench45", out=None, check_stale=False,
                              staged_claude_code=False, allow_placeholders=True)
    assert cli.cmd_report(args, {}, full_board=True) == 0
