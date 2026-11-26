"""4.5.1, findings from the rig's Windows runs: a hosted run never calls a local rig it does not use; the one-command
run shows plain progress lines (the raw log goes to run.log); a finished folder says what its results cost in all."""
import json
import time

from crucibleforge import cli, quicktest as qt, studioforge

PROVIDERS = {"openrouter": {"type": "openai", "base_url": "https://openrouter.ai/api/v1", "api_key_env": "OPENROUTER_API_KEY"},
             "studioforge": {"type": "studioforge", "base_url": "http://127.0.0.1:1234/v1"}}


def _cfg():
    return {"providers": PROVIDERS,
            "models": [{"name": "luna", "model_id": "openai/gpt-6-luna", "provider": "openrouter"}],
            "judge": {"candidates": [{"name": "rig-judge", "provider": "studioforge", "model_id": "q/q/q.gguf"},
                                     {"name": "hosted-judge", "provider": "openrouter", "model_id": "qwen/qwen3.8-27b"}],
                      "samples": 1},
            "defaults": {"context_length": 32768}}


def test_a_hosted_run_with_a_hosted_judge_never_asks_the_rig(monkeypatch):
    calls = []
    monkeypatch.setattr(studioforge, "residents", lambda *a, **k: calls.append(a) or [])
    cfg = _cfg()
    cli._ProviderGuard(cfg, cfg["models"], judge="hosted-judge")
    assert calls == []
    # without the run's judge the guard falls back to every candidate: the old behaviour, which asked the rig
    cli._ProviderGuard(cfg, cfg["models"])
    assert len(calls) == 1
    # an unknown judge spec guards no rig either (the judge step reports it)
    calls.clear()
    cli._ProviderGuard(cfg, cfg["models"], judge="no-such-judge")
    assert calls == []


def test_run_and_all_hand_their_judge_to_the_guard():
    src = open(cli.__file__, encoding="utf-8").read()
    assert src.count('_ProviderGuard(cfg, entries, force_evict=getattr(args, "force_evict", False),\n'
                     '                           judge=getattr(args, "judge", None))') == 3


def _log(level, msg):
    return f"2026-11-26 04:36:38,686 {level} {msg}\n"


def test_progress_lines_are_plain_and_say_how_far(monkeypatch, tmp_path):
    led = tmp_path / "l.jsonl"
    led.write_text(json.dumps({"cost_usd": 0.21}) + "\n", encoding="utf-8")
    p = qt.Progress(ledger=str(led))
    assert p.feed("Run cost: $0.31 table line\n") == []          # engine prints: run.log only
    assert p.feed(_log("INFO", "running 40 jobs with concurrency=4")) == []
    p.first_job = time.time() - 60
    out = []
    for i in range(8):
        out += p.feed(_log("INFO", f"[luna] CASE-{i} r1 done (1 rows)"))
    assert out and all(o.startswith("  ") and "INFO" not in o and "2026-" not in o for o in out)
    line = p.line()
    assert line.startswith("  8 of 40 answers · $0.2100 so far · about ") and line.endswith("min left for the answers")
    warn = p.feed(_log("WARNING", "OpenRouter answered 503 for 3 call(s); retrying"))
    assert warn == ["  note: OpenRouter answered 503 for 3 call(s); retrying"]
    assert p.feed(_log("WARNING", "OpenRouter answered 503 for 7 call(s); retrying")) == []   # said once
    p.feed(_log("INFO", "loading judge qwen/qwen3.8-27b @ openrouter~judge (68 rows)"))
    assert p.line().startswith("  judging the answers · $0.2100 so far · ")


def test_notes_stop_after_ten(monkeypatch):
    p = qt.Progress()
    out = []
    for i in range(30):
        out += p.feed(_log("WARNING", f"warning kind {chr(65 + i)}"))
    assert sum(o.startswith("  note:") for o in out) == qt.MAX_NOTES
    assert any("further notes only in" in o and "run.log" in o for o in out)


def _ledger(d, name, model, judge):
    d.mkdir(exist_ok=True)
    (d / name).write_text("\n".join([json.dumps({"kind": "reserve", "usd": 9.0}),
                                     json.dumps({"cost_usd": model, "model": "m"}),
                                     json.dumps({"cost_usd": judge, "kind": "judge"})]) + "\n", encoding="utf-8")


def test_the_cost_line_on_a_finished_folder_gives_the_folder_total(monkeypatch, tmp_path):
    spend = tmp_path / "spend"
    _ledger(spend, "luna-20261125-101010.jsonl", 0.47, 0.21)
    _ledger(spend, "luna-2-20261125-101010.jsonl", 5.0, 5.0)          # another model: never counted
    _ledger(spend, "luna-20261126-090000.jsonl", 0.0, 0.0)            # this invocation: nothing new
    monkeypatch.setattr(qt, "config_path", lambda: tmp_path / "models.yaml")
    assert qt.folder_cost("luna") == (0.47, 0.21, 1)
    lines = qt.cost_lines(str(spend / "luna-20261126-090000.jsonl"), [], "luna")
    assert lines == ["Nothing new was run this time; the results in this folder cost $0.6800 in total: "
                     "model $0.4700 + judge $0.2100."]
    # a run that added something: its own line, then the folder's total over its runs
    _ledger(spend, "luna-20261126-100000.jsonl", 0.5, 0.25)
    lines = qt.cost_lines(str(spend / "luna-20261126-100000.jsonl"), [], "luna")
    assert lines[0].startswith("Cost of this run: model $0.5000 + judge $0.2500 = $0.7500")
    assert lines[1] == "  The results in this folder cost $1.43 in total over 2 runs: model $0.97 + judge $0.46."


def test_a_finished_folder_from_an_earlier_build_is_recognised_after_its_plan():
    assert qt.owed_nothing({"owed": ["owed on resume: none (all 279 answers stored)", "owed on resume: none"]})
    assert not qt.owed_nothing({"owed": ["owed on resume: none", "owed on resume: 12 rows"]})
    assert not qt.owed_nothing({"owed": []})                          # a fresh folder: nothing stored yet
    assert not qt.owed_nothing({})


def test_the_minutes_cell_never_says_not_recorded():
    from crucibleforge import report
    assert report.minutes45_cell({"run_minutes45": None, "run_ids45": 4}) == "—"
    assert "not recorded" not in report.MINUTES_NOTE


def test_a_legacy_windows_console_gets_replacements_not_a_crash(monkeypatch):
    import io
    import sys
    from crucibleforge.firstrun import safe_console
    out = io.TextIOWrapper(io.BytesIO(), encoding="cp437")
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(sys, "stderr", io.TextIOWrapper(io.BytesIO(), encoding="cp1252"))
    safe_console()
    print("42 of 279 answers · $0.21 — done ✓", file=out)
    out.flush()
    assert out.buffer.getvalue().decode("cp437").startswith("42 of 279 answers · $0.21 ? done ?")


def test_the_first_ctrl_c_says_what_it_waits_for():
    p = qt.Progress()
    assert "Press Ctrl-C again to stop now" in p.stopping_line()
    p.feed(_log("INFO", "running 279 jobs with concurrency=2"))
    p.first_job = time.time() - 600
    for i in range(10):
        p.feed(_log("INFO", f"[m] C-{i} r1 done (1 rows)"))
    line = p.stopping_line()
    assert line == ("Stopping: finishing the 2 answers already in progress (up to about 2 min at this speed). "
                    "Press Ctrl-C again to stop now; those 2 would be asked again next time.")


def test_the_in_use_providers_gate_reports_others_down_without_a_request(monkeypatch):
    from crucibleforge import providers
    assert providers.PROVIDERS_IN_USE_ENV == qt.PROVIDERS_IN_USE_ENV
    cfg = _cfg()
    assert qt.providers_in_use(cfg, "luna", "hosted-judge") == ["openrouter"]
    assert qt.providers_in_use(cfg, "luna", "openrouter:qwen/qwen3.8-27b") == ["openrouter"]
    monkeypatch.setenv(providers.PROVIDERS_IN_USE_ENV, "openrouter")
    assert providers.provider_in_use("openrouter") and providers.provider_in_use("openrouter~judge")
    assert not providers.provider_in_use("studioforge")
    p = providers.get_provider(cfg, "studioforge")
    monkeypatch.setattr(p, "_headers", lambda: (_ for _ in ()).throw(AssertionError("asked the rig")))
    assert p.alive() is False
    monkeypatch.delenv(providers.PROVIDERS_IN_USE_ENV)
    assert providers.provider_in_use("studioforge")          # unset: nothing changes for the engine's own commands
