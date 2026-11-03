"""Suite 3.9 runner: the stop path (EVAL-FABLE M10, STEP4 defect 1).

2026-10-28: the rig's Tailscale key expired mid-run; a SIGTERM then raised
SystemExit in the main thread, which joined worker threads stuck on the dead
host; systemd SIGABRTed the process 46-90 s later with the meta unfinished
(finished=null, failed=false) and the GPU lease held. Now a SIGTERM sets the
stop flags, the process leaves without joining stuck workers, the meta says
``stopped`` and the lease is released.
"""
from __future__ import annotations

import http.server
import json
import os
import signal
import socketserver
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from crucibleforge import config, runner, stopflag
from crucibleforge.api import ChatResult

# POSIX signal semantics (SIGTERM -> exit 143): on Windows send_signal(SIGTERM) is TerminateProcess
needs_posix_signals = pytest.mark.skipif(sys.platform == "win32", reason="POSIX SIGTERM semantics")

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def _clear_stop_flags():
    stopflag.ABORT.clear()
    runner.STOP.clear()
    yield
    stopflag.ABORT.clear()
    runner.STOP.clear()


# ------------------------------------------------- a server that never answers

class _HungServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True

    def server_bind(self):
        socketserver.TCPServer.server_bind(self)
        self.server_name = "localhost"


def _hung_server():
    """/v1/models answers (the provider is "up"); /v1/chat/completions accepts
    the request and then never sends a byte — what a worker stuck on a dead
    host looks like from inside the process."""
    release = threading.Event()
    chats = []

    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            body = json.dumps({"data": [{"id": "m"}]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            n = int(self.headers.get("Content-Length") or 0)
            self.rfile.read(n)
            chats.append(time.time())
            release.wait(120)

    srv = _HungServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.05},
                     daemon=True).start()
    return srv, release, chats


def _write_cfg(tmp_path: Path, port: int, thinking) -> Path:
    import yaml
    cfg = {
        "defaults": {"context_length": 32768},
        "providers": {"hung": {"type": "openai", "base_url": f"http://127.0.0.1:{port}/v1",
                               "concurrency": 4}},
        "judge": {"candidates": []},
        "models": [{"name": "m", "provider": "hung", "model_id": "m", "thinking": thinking}],
    }
    p = tmp_path / "models.yaml"
    p.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    return p


def _launch(tmp_path, cfg_path, grace_s):
    env = {k: v for k, v in os.environ.items()
           if not k.startswith("CRUCIBLEFORGE_")}
    env.update(CRUCIBLEFORGE_RESULTS=str(tmp_path / "results"),
               CRUCIBLEFORGE_STOP_GRACE_S=str(grace_s), PYTHONPATH=str(ROOT))
    argv = [sys.executable, "-m", "crucibleforge.cli", "--allow-unsandboxed",
            "--config", str(cfg_path), "--results", str(tmp_path / "results"), "run",
            "--models", "m", "--no-link-check",
            # the conformance gate would refuse the model before the run even
            # starts: the host here hangs, so the probes hang with it.  This
            # test is about the SIGTERM path, not about the gate.
            "--skip-conformance",
            "--cases", "WL2-lighthouse,UX1-tide-cell,UX3-brass-roast"]   # 4.0: three single-turn board cases
    return subprocess.Popen(argv, cwd=str(tmp_path), env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)


def _sigterm_and_time(proc, chats, n_chats):
    deadline = time.time() + 60
    while len(chats) < n_chats and time.time() < deadline and proc.poll() is None:
        time.sleep(0.1)
    assert len(chats) >= n_chats, proc.communicate(timeout=5)[0].decode("utf-8", "replace")[-3000:]
    time.sleep(0.5)
    t0 = time.time()
    proc.send_signal(signal.SIGTERM)
    try:
        out, _ = proc.communicate(timeout=20)
    except subprocess.TimeoutExpired:
        proc.kill()
        out, _ = proc.communicate()
        pytest.fail("the process did not end within 20 s of SIGTERM (it hung):\n"
                    + out.decode("utf-8", "replace")[-3000:])
    return time.time() - t0, proc.returncode, out.decode("utf-8", "replace")


@needs_posix_signals
def test_sigterm_with_workers_stuck_on_a_dead_host_exits_fast_and_marks_stopped(tmp_path):
    """Workers blocked mid-request (the rig stopped answering): SIGTERM ends
    the process in seconds through the normal path (the watchdog's 25 s grace
    never fires), exit 143, meta stopped + finished + failed."""
    srv, release, chats = _hung_server()
    try:
        proc = _launch(tmp_path, _write_cfg(tmp_path, srv.server_address[1], True), 25)
        took, rc, out = _sigterm_and_time(proc, chats, 3)
    finally:
        release.set()
        srv.shutdown()
    assert took < 10, out[-3000:]
    assert rc == 143, out[-3000:]
    meta = json.loads((tmp_path / "results" / "meta_m.json").read_text(encoding="utf-8"))
    assert meta["stopped"] is True and meta["failed"] is True
    assert meta["finished"] and meta["error"] == "stopped (SIGTERM)"


@needs_posix_signals
def test_sigterm_with_the_main_thread_blocked_ends_by_the_watchdog(tmp_path):
    """thinking: auto -> the main thread itself is blocked in the thinking
    probe on the hung server; the watchdog ends the process after the grace
    and still marks the run stopped."""
    srv, release, chats = _hung_server()
    try:
        proc = _launch(tmp_path, _write_cfg(tmp_path, srv.server_address[1], "auto"), 2)
        took, rc, out = _sigterm_and_time(proc, chats, 1)
    finally:
        release.set()
        srv.shutdown()
    assert took < 10, out[-3000:]
    assert rc == 143, out[-3000:]
    meta = json.loads((tmp_path / "results" / "meta_m.json").read_text(encoding="utf-8"))
    assert meta["stopped"] is True and meta["failed"] is True and meta["finished"]


# ----------------------------------------------------------- in process

def _cfg():
    return {"defaults": {"context_length": 4096, "repeats": {}},
            "providers": {"fake": {"type": "openai", "base_url": "http://127.0.0.1:9/v1",
                                   "concurrency": 4}},
            "judge": {"candidates": []}, "models": []}


def _cases(n=8):
    return [{"id": f"I{i}", "category": "instruct", "prompt": "x", "max_tokens": 8,
             "grader": "contains", "grader_config": {"needles": ["ok"]}} for i in range(n)]


def test_request_stop_returns_run_models_within_seconds(tmp_path, monkeypatch):
    """A fake provider that blocks on 'connect': request_stop (what the
    SIGTERM handler calls) makes run_models return in about a second, writes
    no row for the dropped jobs, and marks the meta stopped."""
    from crucibleforge import cli, providers
    monkeypatch.setattr(config, "RESULTS_DIR", tmp_path)
    monkeypatch.setenv("CRUCIBLEFORGE_STOP_GRACE_S", "3600")   # the watchdog must not fire
    hang = threading.Event()

    def chat(self, model_id, messages, **kw):
        hang.wait(60)
        return ChatResult(response_text="ok", finish_reason="stop", served_model="m")

    monkeypatch.setattr(providers.Provider, "chat", chat)
    monkeypatch.setattr(providers.Provider, "list_models", lambda self, refresh=False: {"m"})
    monkeypatch.setattr(providers.Provider, "alive", lambda self: True)
    entry = {"name": "m", "model_id": "m", "provider": "fake", "thinking": True}
    threading.Timer(0.5, cli.request_stop).start()
    t0 = time.time()
    try:
        summary = runner.run_models(_cfg(), [entry], _cases())
    finally:
        hang.set()
    assert time.time() - t0 < 5
    assert summary["m"]["failed"] and summary["m"]["stopped"]
    meta = json.loads((tmp_path / "meta_m.json").read_text(encoding="utf-8"))
    assert meta["stopped"] is True and meta["finished"] and meta["failed"] is True
    assert config.load_transcripts("m") == []


def test_abort_ends_transport_retry_backoff_at_once(monkeypatch):
    """The retry/backoff loop checks the stop flag: no 2 s + 4 s of back-off
    against a dead host after a SIGTERM."""
    from crucibleforge import api
    calls = []

    def boom(*a, **k):
        calls.append(1)
        stopflag.ABORT.set()
        raise api.TransportError("connect timeout")

    monkeypatch.setattr(api, "stream_chat", boom)
    t0 = time.time()
    with pytest.raises(api.Aborted):
        api.stream_chat_retried("http://x/v1", "", "m", [], max_tokens=8)
    assert time.time() - t0 < 1 and calls == [1]


def test_management_calls_have_a_bounded_connect_timeout(monkeypatch):
    """A rig that does not accept the connection fails in seconds: the lease /
    load calls keep their long READ timeout (a cold load) but connect is
    bounded."""
    from crucibleforge import studioforge
    seen = {}

    class C:
        def __init__(self, timeout):
            seen["t"] = timeout

        def __enter__(self):
            raise studioforge.httpx.ConnectTimeout("dead host")

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(studioforge.httpx, "Client", C)
    with pytest.raises(studioforge.StatusUnavailable):
        studioforge._mgmt("POST", "http://x/v1", "", None, "/api/leases", json={},
                          timeout=studioforge.WARMUP_TIMEOUT_S)
    assert seen["t"].connect <= 10 and seen["t"].read == studioforge.WARMUP_TIMEOUT_S


def test_lease_wait_ends_on_abort(monkeypatch):
    """A lease refusal being waited out (wait_busy_s 600) ends at once on a
    hard stop instead of sleeping on."""
    from crucibleforge import studioforge
    monkeypatch.setattr(studioforge, "_mgmt", lambda *a, **k: (
        503, {"error": {"code": "model_busy"}, "_retry_after_s": 30.0}))
    threading.Timer(0.3, stopflag.ABORT.set).start()
    t0 = time.time()
    with pytest.raises(studioforge.StudioForgeError, match="stopping"):
        studioforge.acquire_lease("http://x/v1", "", None, [0], model_ids=["m"],
                                  wait_busy_s=600)
    assert time.time() - t0 < 3


def test_restore_after_a_stop_releases_the_lease_and_skips_resident_reloads(monkeypatch):
    from crucibleforge import providers, studioforge
    calls = []
    monkeypatch.setattr(studioforge, "release_lease",
                        lambda *a, **k: calls.append(("release", a[-1])) or True)
    monkeypatch.setattr(studioforge, "_mgmt", lambda *a, **k: calls.append(("mgmt", a[4])) or (200, {}))
    monkeypatch.setattr(studioforge, "unload_all", lambda *a, **k: calls.append(("unload",)))
    p = providers.Provider(name="sf", type="studioforge", base_url="http://x/v1")
    p._lease = {"_lease_id": "L1", "_model_id": "m"}
    p._plans = {"m": {}}
    stopflag.ABORT.set()
    p.restore([{"model_id": "chat-model", "loaded_by": "pin"}])
    assert calls == [("release", "L1")]


def test_session_turns_check_the_stop_flag(tmp_path, monkeypatch):
    """A multi-turn session stops between turns on a hard stop (no new turn
    is sent once the flag is up)."""
    n = {"chat": 0}

    class P:
        name, type = "fake", "openai"

        def chat(self, model_id, messages, **kw):
            n["chat"] += 1
            stopflag.ABORT.set()
            return ChatResult(response_text="hi", finish_reason="stop", served_model="m")

        def switch_model(self, *a, **k):
            return 0.0

    ctx = runner._Ctx({"defaults": {}}, {"name": "m", "model_id": "m", "thinking": True}, P(), 8192)
    case = {"id": "S", "category": "rp", "turns": ["a", "b", "c"], "max_tokens": 64}
    with pytest.raises(runner.RunStopped):
        runner._run_multiturn(case, {}, ctx, 64, 0.0, 1.0, 42)
    assert n["chat"] == 1


def test_bench_all_after_a_stop_starts_nothing_and_skips_judge_and_report(tmp_path, monkeypatch):
    import argparse
    from crucibleforge import bench_all, cli
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
        "groups": [{"models": ["m"], "cases": ["I1"]}], "judge": ["m"]})
    called = []
    for name in ("cmd_run", "cmd_judge", "cmd_escalate"):
        monkeypatch.setattr(cli, name, lambda *a, _n=name, **k: called.append(_n) or 0)
    monkeypatch.setattr(cli, "cmd_report", lambda *a, **k: called.append("report") or 0)
    stopflag.ABORT.set()
    args = argparse.Namespace(go=True, include_api=False, first=None, no_sync=True,
                              no_prune=True, until=None, judge_reserve_min=45)
    cfg = {"providers": {"sf": {"type": "studioforge"}}, "_path": str(tmp_path / "m.yaml")}
    assert bench_all.execute(args, cfg) == cli.TERM_EXIT
    assert called == []
