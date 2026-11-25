"""Docker user path 2026-11-25: a resume with nothing owed spent about 10 minutes re-planning the pack (ladder)
rounds. When the last check found nothing due and no row, mark, set or scorer changed since, `all` goes straight to
the report and says what it skipped; any change re-plans."""
from __future__ import annotations

from argparse import Namespace

from crucibleforge import cli, config, profiles


def _setup(tmp_path, monkeypatch):
    config.set_results_dir(tmp_path)
    (tmp_path / "transcripts_m.jsonl").write_text('{"id": "a"}\n', encoding="utf-8")
    prof = {"ladder": {"unlock": 0.9}}
    sets = {"work/p": [{"n": 1, "level": 1}]}
    monkeypatch.setattr(profiles, "load_profile", lambda name, cfg=None: prof)
    from crucibleforge import ladder
    monkeypatch.setattr(ladder, "discover", lambda p: sets)
    monkeypatch.setattr(cli, "_apply_profile_arg", lambda args, cfg: (cfg, None, prof))
    monkeypatch.setattr(cli, "resolve_models", lambda cfg, names: [{"name": "m"}])
    calls = []

    def todo(label, cfg_p, prof_, sets_, unlock, tried=None):
        calls.append(label)
        return [], set(), []

    monkeypatch.setattr(cli, "ladder_todo", todo)
    args = Namespace(profile="x", smoke=False, no_tiers=False, no_ladder=False, ladder_sections=None)
    return args, calls, prof, sets


def test_a_second_resume_with_nothing_owed_skips_the_pack_rounds(tmp_path, monkeypatch, capsys):
    args, calls, prof, sets = _setup(tmp_path, monkeypatch)
    assert cli._ladder_phases(args, {}, ["m"]) == 0
    assert calls == ["m"]                                        # first time: planned, nothing due, recorded
    assert cli.ladder_nothing_owed("m") == cli.ladder_fingerprint("m", prof, sets)
    assert cli._ladder_phases(args, {}, ["m"]) == 0
    assert calls == ["m"]                                        # second time: not planned again
    assert "pack rounds skipped: nothing owed and the stored state is current" in capsys.readouterr().out


def test_a_changed_row_file_plans_again(tmp_path, monkeypatch):
    args, calls, prof, sets = _setup(tmp_path, monkeypatch)
    cli._ladder_phases(args, {}, ["m"])
    with open(tmp_path / "transcripts_m.jsonl", "a", encoding="utf-8") as f:
        f.write('{"id": "b"}\n')
    cli._ladder_phases(args, {}, ["m"])
    assert calls == ["m", "m"]


def test_a_due_set_is_never_recorded_as_nothing_owed(tmp_path, monkeypatch):
    args, calls, prof, sets = _setup(tmp_path, monkeypatch)
    monkeypatch.setattr(cli, "ladder_todo", lambda *a, **k: (calls.append("m") or ([{"id": "c"}], set(),
                                                                                   ["work/p#1"])))
    cli._ladder_phases(args, {}, ["m"])
    assert cli.ladder_nothing_owed("m") is None


def test_all_ends_with_the_runs_result_not_the_whole_board():
    """Docker user path 2026-11-25: `all` printed the whole report; it now prints the run's row and the paths."""
    md = ("## Scorecard\n\n| # | Model | Overall /20 | Code /10 |\n|---|---|---|---|\n"
          "| 1 | a | **18**.5 | 9.5 |\n| 2 | b | **21**.3 of 22 ±0.30 = 19.3 standard ±0.20 + 2.0 bonus | - |\n")
    out = cli.run_summary(md, ["b"])
    assert "b (rank 2 of 2 on the board)" in out
    assert "  Overall /20: **21**.3 of 22 ±0.30 = 19.3 standard ±0.20 + 2.0 bonus" in out
    assert "| 1 | a |" not in out and "Code /10" not in out          # an empty cell is not printed
    assert "--print-report" in out
    assert "--print-report" in open(cli.__file__, encoding="utf-8").read()
