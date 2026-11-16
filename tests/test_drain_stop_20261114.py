"""The cooperative drain (lead 2026-11-14): "finish in-flight requests, send no new ones, then exit".

- SIGUSR1 or the stop file ``<results>/.drain`` asks for it (a stale file is removed, never obeyed);
- the requests in flight finish and are kept, nothing new starts, the meta reads "stopped on request,
  the rest next run" (partial, not FAILED); the process exits with no judge or board (DRAIN_EXIT);
- no ceiling by default; ``--drain-max-minutes`` cuts a stream still running after N minutes: no
  row, and the notes say to unload and reload the model before the next run;
- bench-all starts no next model after a drain and its window never clears a drain's STOP.
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import threading
import time

import pytest

from crucibleforge import api, bench_all, cli, config, providers, runner, stopflag
from crucibleforge.api import ChatResult
from test_runner_stop_39 import _cases, _cfg


@pytest.fixture(autouse=True)
def _clean():
    def reset():
        runner.reset_drain()
        stopflag.ABORT.clear()
        runner.STOP.clear()
        runner.WINDOW_STOP.clear()
        runner.GEN_DEADLINE[0] = None
        cli._DRAIN["max_min"] = None
    reset()
    yield
    reset()


def _fake(monkeypatch, chat):
    monkeypatch.setattr(providers.Provider, "chat", chat)
    monkeypatch.setattr(providers.Provider, "list_models", lambda self, refresh=False: {"m"})
    monkeypatch.setattr(providers.Provider, "alive", lambda self: True)
    return {"name": "m", "model_id": "m", "provider": "fake", "thinking": True}


def test_a_drain_finishes_what_is_in_flight_and_starts_nothing_new(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "RESULTS_DIR", tmp_path)
    started = []
    go = threading.Event()

    def chat(self, model_id, messages, **kw):
        started.append(1)
        go.wait(10)                    # every in-flight request is still running at the drain
        return ChatResult(response_text="ok", finish_reason="stop", served_model="m")
    entry = _fake(monkeypatch, chat)

    def drain_then_release():
        while not started:
            time.sleep(0.01)
        time.sleep(0.2)
        runner.request_drain(why="test")
        go.set()
    threading.Thread(target=drain_then_release, daemon=True).start()
    summary = runner.run_models(_cfg(), [entry], _cases(12))
    rows = config.load_transcripts("m")
    assert len(rows) == len(started) and 0 < len(rows) < 12      # in flight kept, nothing new sent
    assert summary["m"]["error"] == runner.DRAIN_NOTE and summary["m"]["drained"]
    assert not summary["m"].get("stream_cut")
    meta = json.loads((tmp_path / "meta_m.json").read_text(encoding="utf-8"))
    assert meta["failed"] is False and meta["drain_note"] == runner.DRAIN_NOTE   # partial, not FAILED
    assert not meta.get("stopped")


def test_no_ceiling_by_default_nothing_is_cut(tmp_path, monkeypatch):
    runner.request_drain(None, why="test")
    assert runner._DRAIN_TIMER[0] is None and not stopflag.CUT.is_set()


def test_the_ceiling_cuts_a_stream_and_the_notes_say_reload(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "RESULTS_DIR", tmp_path)

    def chat(self, model_id, messages, **kw):
        runner.request_drain(max_minutes=0.3 / 60, why="test")   # asked while this stream runs
        while not stopflag.CUT.is_set():          # a long stream, as api.stream_chat sees it
            time.sleep(0.01)
        raise api.WindowClosed("drain ceiling reached: stream cut")
    entry = _fake(monkeypatch, chat)
    summary = runner.run_models(_cfg(), [entry], _cases(4))
    assert config.load_transcripts("m") == []                         # no row: the job stays pending
    assert summary["m"]["stream_cut"] and runner.CUT_NOTE in summary["m"]["error"]
    meta = json.loads((tmp_path / "meta_m.json").read_text(encoding="utf-8"))
    assert meta["stream_cut"] is True and meta["failed"] is False
    assert bench_all._drain_notes([])[-1].startswith(runner.CUT_NOTE)


def test_the_stream_loop_and_the_retry_loop_honour_the_cut(monkeypatch):
    stopflag.CUT.set()
    with pytest.raises(api.WindowClosed, match="drain ceiling"):
        api.stream_chat_retried("http://x/v1", "", "m", [], max_tokens=8)


def test_the_stream_is_cut_mid_body(monkeypatch):
    class Resp:
        status_code = 200
        headers = {}

        def iter_text(self):
            for i in range(1000):
                if i == 3:
                    stopflag.CUT.set()
                yield 'data: {"choices":[{"delta":{"content":"x"}}]}\n'

    class Stream:
        def __enter__(self):
            return Resp()

        def __exit__(self, *a):
            return False

    class Client:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def stream(self, *a, **k):
            return Stream()
    monkeypatch.setattr(api.httpx, "Client", Client)
    with pytest.raises(api.WindowClosed, match="stream cut"):
        api.stream_chat("http://x/v1", "", "m", [{"role": "user", "content": "x"}], max_tokens=8)


@pytest.mark.skipif(not hasattr(signal, "SIGUSR1"), reason="POSIX signals")
def test_sigusr1_asks_for_a_drain():
    old_usr1, old_term = signal.getsignal(signal.SIGUSR1), signal.getsignal(signal.SIGTERM)
    try:
        cli.install_stop_handler()
        os.kill(os.getpid(), signal.SIGUSR1)
        for _ in range(100):
            if cli.draining():
                break
            time.sleep(0.01)
        assert cli.draining() and runner.STOP.is_set() and not stopflag.ABORT.is_set()
    finally:
        signal.signal(signal.SIGUSR1, old_usr1)
        signal.signal(signal.SIGTERM, old_term)


def test_the_stop_file_asks_for_a_drain_and_may_name_the_ceiling(tmp_path):
    f = tmp_path / ".drain"
    t = cli.start_drain_watch(f, started=time.time() - 5, poll_s=0.02)
    f.write_text("12\n", encoding="utf-8")
    t.join(5)
    assert cli.draining() and not f.exists()                        # consumed
    assert runner._DRAIN_TIMER[0] is not None                       # 12-minute ceiling armed


def test_a_stale_stop_file_is_removed_not_obeyed(tmp_path):
    f = tmp_path / ".drain"
    f.write_text("", encoding="utf-8")
    os.utime(f, (time.time() - 3600, time.time() - 3600))
    cli.start_drain_watch(f, poll_s=0.02)
    time.sleep(0.2)
    assert not f.exists() and not cli.draining()
    stopflag.ABORT.set()                                            # end the watcher


def test_the_ceiling_setting_flag_then_env(monkeypatch):
    monkeypatch.delenv(cli.DRAIN_ENV, raising=False)
    assert cli.drain_max_minutes(argparse.Namespace(drain_max_minutes=None)) is None
    monkeypatch.setenv(cli.DRAIN_ENV, "20")
    assert cli.drain_max_minutes(argparse.Namespace(drain_max_minutes=None)) == 20
    assert cli.drain_max_minutes(argparse.Namespace(drain_max_minutes=5.0)) == 5.0
    monkeypatch.setenv(cli.DRAIN_ENV, "soon")
    assert cli.drain_max_minutes(None) is None
    for cmd in (["run"], ["all"], ["bench-all"]):
        assert "--drain-max-minutes" in _help(cmd)


def _help(cmd):
    import contextlib
    import io
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), pytest.raises(SystemExit):
        cli.main(cmd + ["--help"])
    return buf.getvalue()


def test_all_exits_after_the_drain_with_no_judge(monkeypatch):
    called = []

    def run(args, cfg):
        runner.request_drain(why="test")
        args.ran_labels = ["m"]
        return 1
    monkeypatch.setattr(cli, "cmd_run", run)
    for name in ("cmd_judge", "cmd_escalate", "cmd_report"):
        monkeypatch.setattr(cli, name, lambda *a, _n=name, **k: called.append(_n) or 0)
    args = argparse.Namespace(plan=False, models="m", no_judge=False, escalate=False)
    assert cli.cmd_all(args, {}) == cli.DRAIN_EXIT
    assert called == []


def _bench_all_stubs(tmp_path, monkeypatch, groups, run):
    import crucibleforge.providers as pv
    monkeypatch.setattr(config, "RESULTS_DIR", tmp_path)

    class Prov:
        def list_models(self, refresh=False):
            return {"x/m"}
    monkeypatch.setattr(pv, "get_provider", lambda cfg, name: Prov())
    monkeypatch.setattr(bench_all, "rig_arches", lambda prov: {})
    monkeypatch.setattr(bench_all, "format_plan", lambda p: "plan")
    monkeypatch.setattr(bench_all, "plan", lambda *a, **k: {
        "add": [], "prune": [], "not_on_rig": [], "escalate": [], "rungs": [],
        "groups": groups, "judge": ["a"]})
    called = []
    monkeypatch.setattr(cli, "cmd_run", lambda a, c: called.append(("run", a.models)) or run(a))
    for name in ("cmd_judge", "cmd_escalate"):
        monkeypatch.setattr(cli, name, lambda *a, _n=name, **k: called.append(_n) or 0)
    monkeypatch.setattr(cli, "cmd_report", lambda *a, **k: called.append("report") or 0)
    args = argparse.Namespace(go=True, include_api=False, first=None, no_sync=True,
                              no_prune=True, until=None, judge_reserve_min=45)
    cfg = {"providers": {"sf": {"type": "studioforge"}}, "_path": str(tmp_path / "m.yaml")}
    return args, cfg, called


def test_bench_all_starts_no_next_model_after_a_drain(tmp_path, monkeypatch, capsys):
    def run(a):
        runner.request_drain(why="test")      # the drain arrives while the first group runs
        return 0
    groups = [{"models": ["a"], "cases": ["I1"]}, {"models": ["b"], "cases": ["I1"]}]
    args, cfg, called = _bench_all_stubs(tmp_path, monkeypatch, groups, run)
    assert bench_all.execute(args, cfg) == cli.DRAIN_EXIT
    assert called == [("run", "a")]                                  # no b, no judge, no board
    assert any("not started (drain): b" in n for n in args.bench_all_notes)
    assert args.bench_all_notes[0].startswith(runner.DRAIN_NOTE)


def test_bench_all_with_a_drain_before_it_starts_runs_nothing(tmp_path, monkeypatch):
    runner.request_drain(why="test")
    args, cfg, called = _bench_all_stubs(tmp_path, monkeypatch, [{"models": ["a"], "cases": ["I1"]}],
                                         lambda a: 0)
    assert bench_all.execute(args, cfg) == cli.DRAIN_EXIT and called == []


def test_the_window_end_never_clears_a_drains_stop():
    runner.WINDOW_STOP.set()
    runner.STOP.set()
    runner.request_drain(why="test")
    assert bench_all.end_window() is True and runner.STOP.is_set()
    runner.reset_drain()
    runner.WINDOW_STOP.set()
    assert bench_all.end_window() is True and not runner.STOP.is_set()   # the window's own STOP


def test_a_drain_record_does_not_block_a_rung(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "RESULTS_DIR", tmp_path)
    recs = [{"failed": True, "rung": 2, "ts": "2026-11-14T10:00:00",
             "error": "stopped by user (drain)"}] * 3
    (tmp_path / "meta_m.json").write_text(json.dumps({"escalated": recs}), encoding="utf-8")
    assert runner.blocked_rungs("m") == set()


@pytest.mark.skipif(sys.platform == "win32", reason="docs line is platform-neutral; one check is enough")
def test_the_engine_docs_say_how_to_drain():
    from pathlib import Path
    txt = (Path(__file__).resolve().parent.parent / "docs" / "RUNNING.md").read_text(encoding="utf-8")
    assert "SIGUSR1" in txt and "<results>/.drain" in txt and "--drain-max-minutes" in txt


def test_a_drain_survives_the_next_phase(tmp_path, monkeypatch):
    """run_models clears STOP at the start of each phase; a drain asked in an earlier phase (D
    2026-11-14: a SIGUSR1 lost at the next phase) must still stop the next one before it sends."""
    monkeypatch.setattr(config, "RESULTS_DIR", tmp_path)
    sent = []

    def chat(self, model_id, messages, **kw):
        sent.append(1)
        return ChatResult(response_text="ok", finish_reason="stop", served_model="m")
    entry = _fake(monkeypatch, chat)
    runner.request_drain(why="test")
    summary = runner.run_models(_cfg(), [entry], _cases(3))
    assert sent == [] and config.load_transcripts("m") == [] and "m" not in summary
    assert runner.STOP.is_set()
