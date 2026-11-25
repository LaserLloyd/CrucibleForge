"""`test` (the one-command Docker front door, crucibleforge/quicktest.py): each plain failure, with a local fake
OpenRouter. No model is called and nothing is spent."""
from __future__ import annotations

import io
import json
import os
import signal
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from crucibleforge import quicktest as qt

MODELS = [{"id": i, "context_length": 32768, "pricing": {"prompt": "0.0000001", "completion": "0.0000005"}}
          for i in ("openai/gpt-6-luna", "openai/gpt-6-luna-mini", "qwen/qwen3.8-27b")]


class _H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, obj):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        auth = self.headers.get("Authorization") or ""
        if self.path.endswith("/models"):
            self._send(200, {"data": MODELS})
        elif self.path.endswith("/key"):
            if auth.endswith("sk-rejected"):
                self._send(401, {"error": {"message": "User not found."}})
            elif auth.endswith("sk-402"):
                self._send(402, {"error": {"message": "Insufficient credits"}})
            elif auth.endswith("sk-limit"):
                self._send(200, {"data": {"limit": 1, "limit_remaining": 0}})
            else:
                self._send(200, {"data": {"limit": None, "limit_remaining": None}})
        elif self.path.endswith("/credits"):
            self._send(200, {"data": {"total_credits": 5, "total_usage": 5 if auth.endswith("sk-empty") else 1}})
        else:
            self._send(404, {})


@pytest.fixture()
def fake(monkeypatch, tmp_path):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _H)
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    monkeypatch.setenv("CRUCIBLEFORGE_OPENROUTER_URL", f"http://127.0.0.1:{srv.server_port}/api/v1")
    monkeypatch.setenv("CRUCIBLEFORGE_CONFIG", str(tmp_path / "data" / "models.yaml"))
    monkeypatch.delenv("CRUCIBLEFORGE_JAIL", raising=False)
    yield tmp_path
    srv.shutdown()


def _run(capsys, *argv):
    rc = qt.main(list(argv))
    return rc, capsys.readouterr().out


def test_first_line_is_immediate_and_no_key(fake, monkeypatch, capsys):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    rc, out = _run(capsys, "openai/gpt-6-luna", "--yes")
    assert rc == qt.EXIT_NO_KEY
    assert out.splitlines()[0].startswith("CrucibleForge test:")
    assert "No OpenRouter key" in out and "openrouter.ai/keys" in out


@pytest.mark.parametrize("key,code,words", [
    ("sk-rejected", qt.EXIT_KEY_REJECTED, "rejected the key"),
    ("sk-402", qt.EXIT_NO_CREDIT, "out of credit"),
    ("sk-limit", qt.EXIT_NO_CREDIT, "out of credit"),
    ("sk-empty", qt.EXIT_NO_CREDIT, "out of credit"),
])
def test_key_failures(fake, monkeypatch, capsys, key, code, words):
    monkeypatch.setenv("OPENROUTER_API_KEY", key)
    rc, out = _run(capsys, "openai/gpt-6-luna", "--yes")
    assert rc == code and words in out
    assert "sk-" not in out, "the key itself must never be printed"


def test_unknown_model_suggests_close_ids(fake, monkeypatch, capsys):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-good")
    monkeypatch.setattr(qt, "load_cfg", lambda: {"providers": {}, "models": []})
    rc, out = _run(capsys, "openai/gpt-6-lune", "--yes")
    assert rc == qt.EXIT_UNKNOWN_MODEL
    assert "no model called 'openai/gpt-6-lune'" in out and "  openai/gpt-6-luna" in out


@pytest.mark.skipif(hasattr(os, "geteuid") and os.geteuid() == 0, reason="root ignores directory modes")
def test_unwritable_folder(fake, monkeypatch, capsys):
    d = fake / "data"
    d.mkdir()
    d.chmod(0o500)
    try:
        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-good")
        rc, out = _run(capsys, "openai/gpt-6-luna", "--yes")
    finally:
        d.chmod(0o700)
    assert rc == qt.EXIT_NOT_WRITABLE
    assert "Cannot write to the results folder" in out and ":Z" in out


def _planned(monkeypatch):
    """Everything up to the question, with the plan faked (the real plan reads the whole suite)."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-good")
    monkeypatch.setattr(qt, "load_cfg", lambda: {"providers": {"openrouter": {}}, "models": []})
    monkeypatch.setattr(qt, "save_cfg", lambda cfg: None)
    monkeypatch.setattr(qt, "register", lambda *a, **k: "gpt-6-luna")
    plan = ("price (estimate, typical): ~$0.40 expected (a typical model), $0.90 for a model that thinks at length\n"
            "judge cost (estimate): ~$0.20 expected, up to $0.35\n-> ~42 min\n")
    calls = []

    def fake_engine_out(*args, **kw):
        calls.append(args)
        return 0, plan
    monkeypatch.setattr(qt, "engine_out", fake_engine_out)
    return calls


def test_no_terminal_refuses_before_spending(fake, monkeypatch, capsys):
    calls = _planned(monkeypatch)
    ran = []
    monkeypatch.setattr(qt, "run_engine", lambda *a, **k: ran.append(a) or (0, []))
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))
    rc, out = _run(capsys, "openai/gpt-6-luna")
    assert rc == qt.EXIT_NOT_CONFIRMED and "--yes" in out
    assert not ran and all("--plan" in c for c in calls)
    assert "model cost:  about $0.40" in out and "stops at $1.80" in out    # cap = twice the long figure


def test_answer_no_starts_nothing(fake, monkeypatch, capsys):
    _planned(monkeypatch)
    ran = []
    monkeypatch.setattr(qt, "run_engine", lambda *a, **k: ran.append(a) or (0, []))

    class Tty(io.StringIO):
        def isatty(self):
            return True
    monkeypatch.setattr(sys, "stdin", Tty("n\n"))
    rc, out = _run(capsys, "openai/gpt-6-luna")
    assert rc == qt.EXIT_NOT_CONFIRMED and "Nothing was spent" in out and not ran


def test_caps_are_about_twice_the_estimate():
    m, r = qt.caps({"model_long_usd": 0.9, "judge_usd": 0.35, "judge_max_usd": 9.0})
    assert m == 1.8 and r == pytest.approx(1.8 + 0.7)
    assert qt.caps({}) == (None, None)
    assert qt.caps({"model_long_usd": 0.01})[0] == qt.MIN_CAP_USD


def test_no_adult_is_an_alias():
    assert qt.parse(["--no-adult", "m"]).no_adult and qt.parse(["--no-nsfw-plus", "m"]).no_adult


def test_host_url_in_a_container(monkeypatch):
    monkeypatch.setattr(qt, "IN_CONTAINER", True)
    monkeypatch.setattr(qt, "host_address", lambda: "host.docker.internal")
    assert qt.host_url("http://localhost:11434") == "http://host.docker.internal:11434/v1"
    assert qt.host_url("http://127.0.0.1:1234/v1") == "http://host.docker.internal:1234/v1"
    assert qt.host_url("http://192.0.2.5:8080") == "http://192.0.2.5:8080/v1"


def test_ctrl_c_stops_cleanly_and_says_how_to_continue(tmp_path):
    """A real child engine stand-in: the first Ctrl-C drains it, the wrapper reports 'run the same command'."""
    child = tmp_path / "eng.py"
    child.write_text("import signal,sys,time\n"
                     "signal.signal(signal.SIGUSR1, lambda *a: (print('drained', flush=True), sys.exit(0)))\n"
                     "print('started', flush=True)\ntime.sleep(30)\n", encoding="utf-8")
    drv = tmp_path / "drv.py"
    drv.write_text("import sys\nfrom crucibleforge import quicktest as qt\n"
                   f"qt.engine_argv = lambda *a: [sys.executable, {str(child)!r}]\n"
                   "rc, tail = qt.run_engine([], None)\n"
                   "if rc == qt.EXIT_STOPPED: qt.say('Stopped. Nothing is lost: run the same command again to continue.')\n"
                   "sys.exit(rc)\n", encoding="utf-8")
    root = Path(qt.__file__).resolve().parents[1]
    p = subprocess.Popen([sys.executable, str(drv)], stdout=subprocess.PIPE, text=True, cwd=root,
                         env={**os.environ, "PYTHONPATH": str(root)})
    assert p.stdout is not None
    assert p.stdout.readline().strip() == "started"
    p.send_signal(signal.SIGINT)
    out = p.stdout.read()
    assert p.wait(timeout=20) == qt.EXIT_STOPPED
    assert "drained" in out and "run the same command again" in out


def test_diagnose_tells_our_cap_from_the_providers_402():
    ours = ["the API rejected the call (HTTP 402): crucibleforge global spend cap reached: $1.00 booked"]
    assert "spend cap" in qt.diagnose(ours)[0]
    assert "out of credit" in qt.diagnose(["HTTP 402: Insufficient credits"])[0]


def test_summary_overall_is_the_boards_overall_not_the_older_scorecard_sum(tmp_path, monkeypatch):
    # a paid run printed "Overall 16.1" while report.md said 16.9: the summary read scorecard.total
    res = tmp_path / "results"
    res.mkdir()
    seg = {"code": {"points": 9.0832, "max": 10.0, "complete": True},
           "chat": {"points": 7.8339, "max": 10.0, "complete": True},
           "overall": {"points": 16.9171, "max": 20.0, "complete": True}}
    (res / "report.json").write_text(json.dumps({"models": {"m": {"scorecard": {"total": 16.12},
                                                                     "segments45": seg}}}), encoding="utf-8")
    monkeypatch.setattr(qt, "results_dir", lambda: res)
    lines = qt.summary_lines("m", "a judge")
    assert lines[0] == "Overall 16.9 of 20"
    assert lines[1] == "Code    9.1 of 10" and lines[2] == "Chat    7.8 of 10"


def test_summary_prints_each_total_with_its_split_from_the_engines_fields(tmp_path, monkeypatch):
    res = tmp_path / "results"
    res.mkdir()
    def t(std, bonus, mx, mxp):
        return {"points": std, "standard_points": std, "bonus_points": bonus, "total": std + bonus,
                "max": mx, "max_with_packs": mxp, "complete": True}
    seg = {"code": t(9.0832, 2.9889, 10, 17), "chat": t(7.8339, 0.7858, 10, 17), "overall": t(16.9171, 3.7747, 20, 34)}
    nsfw = {"state": "complete", "total": {"value": 8.99, "of": 20},
            "totals45": {"nsfw_plus": {"standard": 8.99, "bonus": 0.87, "total": 9.86, "max_with_packs": 26}}}
    (res / "report.json").write_text(json.dumps({"models": {"m": {"segments45": seg, "nsfw_plus": nsfw}}}),
                                     encoding="utf-8")
    monkeypatch.setattr(qt, "results_dir", lambda: res)
    lines = qt.summary_lines("m", "a judge")
    assert lines[:3] == ["Overall 20.7 of 34 = 16.9 standard + 3.8 bonus",
                         "Code    12.1 of 17 = 9.1 standard + 3.0 bonus",
                         "Chat    8.6 of 17 = 7.8 standard + 0.8 bonus"]
    assert lines[3] == "Adult (NSFW+, never part of Overall) 9.9 of 26 = 9.0 standard + 0.9 bonus"


def test_plan_reader_takes_the_judge_bound_with_and_without_the_pack_rounds():
    a = qt.read_plan("  judge cost (estimate) (openrouter): ~$0.21 expected for ~800 call(s), up to $0.55 for the "
                     "standard run (~1200 calls: x), + up to $0.30 more if every pack round unlocks; billed")
    assert (a["judge_usd"], a["judge_max_usd"], a["judge_packs_max_usd"]) == (0.21, 0.55, 0.30)
    b = qt.read_plan("  judge cost (estimate): ~$0.21 expected for ~800 call(s), up to $0.55 (~1 calls: x); billed")
    assert b["judge_packs_max_usd"] is None
