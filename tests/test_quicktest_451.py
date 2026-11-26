"""`test` (the one-command Docker front door, crucibleforge/quicktest.py): each plain failure, with a local fake
OpenRouter. No model is called and nothing is spent."""
from __future__ import annotations

import io
import json
import re
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
    monkeypatch.setattr(qt, "load_cfg", lambda notes=None: {"providers": {}, "models": []})
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
    assert "Cannot write to the results folder" in out
    assert "-v" not in out, "outside a container there is no -v to talk about"


def _planned(monkeypatch):
    """Everything up to the question, with the plan faked (the real plan reads the whole suite)."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-good")
    monkeypatch.setattr(qt, "load_cfg", lambda notes=None: {"providers": {"openrouter": {}}, "models": []})
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
    # cap = twice the expected total (0.40 + 0.20), rounded up to the next $0.25
    assert "model cost:  about $0.40" in out and "the run stops at $1.25, about twice the estimate" in out


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


@pytest.mark.parametrize("model,judge,cap", [
    (0.47, 0.30, 1.75),      # the page's example: about $0.77 -> 1.54 -> $1.75 (the old rule said $5.00)
    (0.47, 0.21, 1.50),      # the measured gpt-6-luna plan, $0.68 (the run cost $0.82, 1.2x)
    (0.05, 0.02, 1.00),      # a very cheap run: the floor, not 0.14
    (3.10, 0.90, 8.00),      # $1 steps from $5
    (40.0, 14.0, 110.0),     # $5 steps from $50
    (0.40, None, 1.00),      # a local judge: the model alone
])
def test_cap_is_about_twice_the_plans_expected_total(model, judge, cap):
    m, r = qt.caps({"model_usd": model, "model_long_usd": model * 3, "judge_usd": judge, "judge_max_usd": 9.0})
    assert r == cap and m == cap
    assert cap >= 2 * (model + (judge or 0)) and (cap == qt.MIN_CAP_USD or cap <= 2 * (model + (judge or 0)) * 1.3)


def test_cap_edge_cases_and_override():
    assert qt.caps({}) == (None, None)
    assert qt.caps({"judge_usd": 0.6}) == (None, 1.25)                 # a local model, judged on OpenRouter
    assert qt.caps({"model_usd": 0.4, "judge_usd": 0.2}, 7.5) == (7.5, 7.5)
    assert qt.cap_step(1.5) == 1.5 and qt.cap_step(1.51) == 1.75 and qt.cap_step(5.2) == 6.0
    with pytest.raises(SystemExit):
        qt.parse(["openai/gpt-6-luna", "--cap", "0"])
    assert qt.parse(["openai/gpt-6-luna", "--cap", "$4"]).cap == 4.0


def test_cap_flag_reaches_the_plan_and_the_engine(fake, monkeypatch, capsys):
    _planned(monkeypatch)
    ran = []
    monkeypatch.setattr(qt, "run_engine", lambda args, env: ran.append((args, env)) or (0, []))
    monkeypatch.setattr(qt, "summary_lines", lambda *a: [])
    rc, out = _run(capsys, "openai/gpt-6-luna", "--yes", "--cap", "3")
    assert "the run stops at $3.00, set with --cap" in out
    args, env = ran[0]
    assert args[args.index("--spend-cap-usd") + 1] == "3.00" and env["CF_SPEND_GLOBAL_CAP_USD"] == "3.00"


def test_cap_stop_says_what_was_spent_and_how_to_go_on(tmp_path):
    led = tmp_path / "l.jsonl"
    led.write_text('{"cost_usd": 1.0}\n{"cost_usd": 0.62, "kind": "judge"}\nnot json\n', encoding="utf-8")
    spent = qt.ledger_spent(str(led))
    assert spent == pytest.approx(1.62) and qt.ledger_spent(None) is None
    code, lines = qt.diagnose(["crucibleforge global spend cap reached: $1.75 booked"], spent, 1.75)
    text = "\n".join(lines)
    assert code == qt.CODES["SPEND_CAP"] and "($1.75)" in text and "after spending $1.62" in text
    assert "Nothing is lost" in text and "--cap 3.50" in text


def test_no_adult_is_an_alias():
    assert qt.parse(["--no-adult", "m"]).no_adult and qt.parse(["--no-nsfw-plus", "m"]).no_adult


def test_host_url_in_a_container(monkeypatch):
    monkeypatch.setattr(qt, "IN_CONTAINER", True)
    monkeypatch.setattr(qt, "host_address", lambda: "host.docker.internal")
    assert qt.host_url("http://localhost:11434") == "http://host.docker.internal:11434/v1"
    assert qt.host_url("http://127.0.0.1:1234/v1") == "http://host.docker.internal:1234/v1"
    assert qt.host_url("http://192.0.2.5:8080") == "http://192.0.2.5:8080/v1"


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX signals: the engine is drained with SIGUSR1, which Windows "
                    "lacks, and a console Ctrl-C there reaches every process on the console, pytest included; the "
                    "image (Linux) is where this path runs")
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
    code, lines = qt.diagnose(ours)
    assert "spend cap" in lines[0] and code == qt.CODES["SPEND_CAP"]
    code, lines = qt.diagnose(["HTTP 402: Insufficient credits"])
    assert "out of credit" in lines[0] and code == qt.EXIT_NO_CREDIT


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


# ---------------------------------------------------------------- first-run messages (rig report 2026-11-25)
@pytest.mark.parametrize("raw,code,words", [
    (None, qt.CODES["NO_KEY"], "No OpenRouter key"),
    ("   ", qt.CODES["KEY_EMPTY"], "key is empty"),
    ('"sk-or-v1-abc"', qt.CODES["KEY_QUOTED"], "quote marks"),
    ("sk-or-v1 abc", qt.CODES["KEY_QUOTED"], "quote marks"),
    ("sk-ant-api03-x", qt.CODES["KEY_OTHER_PROVIDER"], "Anthropic key"),
    ("sk-proj-x", qt.CODES["KEY_OTHER_PROVIDER"], "OpenAI key"),
])
def test_a_key_that_cannot_work_is_named_before_any_request(raw, code, words):
    with pytest.raises(qt.Fail) as e:
        qt.key_format(raw)
    assert e.value.code == code and words in "\n".join(e.value.lines)


def test_an_openrouter_key_passes_the_format_check():
    assert qt.key_format("  sk-or-v1-abc\n") == "sk-or-v1-abc"


def test_network_failures_are_told_apart():
    import socket as so
    import ssl as sl
    import urllib.error as ue
    dns = qt.net_fail(ue.URLError(so.gaierror(-2, "Name or service not known")), "openrouter.ai")
    assert dns.code == qt.CODES["DNS"] and "HTTPS_PROXY" in "\n".join(dns.lines)
    tls = qt.net_fail(ue.URLError(sl.SSLCertVerificationError(1, "certificate verify failed: self-signed")), "x")
    assert tls.code == qt.CODES["TLS"] and "SSL_CERT_FILE" in "\n".join(tls.lines)
    clock = qt.net_fail(ue.URLError(sl.SSLCertVerificationError(1, "certificate is not yet valid")), "x")
    assert clock.code == qt.CODES["CLOCK"]
    down = qt.net_fail(ue.URLError(ConnectionRefusedError(111, "Connection refused")), "x")
    assert down.code == qt.CODES["NETWORK"]


def test_rate_limit_at_the_key_check_says_nothing_is_lost(fake, monkeypatch, capsys):
    import urllib.error as ue
    f = qt.http_fail(ue.HTTPError("u", 429, "Too Many", {}, None), "the key")
    assert f.code == qt.CODES["RATE_LIMITED"] and "nothing is lost" in "\n".join(f.lines)


def test_a_broken_models_yaml_names_the_line_and_shows_a_valid_one(fake, monkeypatch):
    p = fake / "data" / "models.yaml"
    p.parent.mkdir(parents=True)
    p.write_text("providers:\n  openrouter: {type: openai\nmodels: []\n", encoding="utf-8")
    with pytest.raises(qt.Fail) as e:
        qt.load_cfg()
    text = "\n".join(e.value.lines)
    assert e.value.code == qt.CODES["BAD_CONFIG"] and "line " in text and "model_id:" in text


def test_a_models_yaml_the_engine_refuses_is_named_with_the_engines_reason(fake, monkeypatch):
    p = fake / "data" / "models.yaml"
    p.parent.mkdir(parents=True)
    p.write_text("providers: {openrouter: {type: openai, base_url: http://x}}\njudge: {}\ndefaults: {}\n"
                 "models:\n  - {name: m, provider: nowhere, model_id: a/b}\n", encoding="utf-8")
    with pytest.raises(qt.Fail) as e:
        qt.load_cfg()
    assert e.value.code == qt.CODES["BAD_CONFIG"] and "nowhere" in e.value.lines[0] and "line 5" in e.value.lines[0]


def test_a_near_miss_key_is_named_with_the_valid_one():
    cfg = {"providers": {"openrouter": {"type": "openai", "base_ulr": "http://x"}}, "models": []}
    notes = qt.unknown_key_notes(cfg, "providers:\n  openrouter:\n    base_ulr: http://x\n", "models.yaml")
    assert notes and "'base_ulr'" in notes[0] and "'base_url'" in notes[0] and "line 3" in notes[0]


def test_context_too_small_is_refused_for_a_local_server_with_the_number_needed():
    plan = "    code   c1   x2  budget 4096  n/a (ctx: needs 40000)\n    code   c2   x2  budget 4096  n/a (ctx: needs 70000)\n"
    with pytest.raises(qt.Fail) as e:
        qt.context_check(plan, 32768, local=True)
    assert e.value.code == qt.CODES["LOCAL_CONTEXT"] and "70000" in "\n".join(e.value.lines)
    assert qt.context_check(plan, 32768, local=False)[0].strip().startswith("note:")
    assert qt.context_check("no such lines", 32768, local=False) == []
    assert "every question in this suite fits" in qt.context_check("no such lines", 32768, local=True)[0]
    assert "does not say" in qt.context_check("no such lines", None, local=True)[0]


def test_check_prints_pass_and_fix_lines_and_spends_nothing(fake, monkeypatch, capsys):
    calls = _planned(monkeypatch)
    ran = []
    monkeypatch.setattr(qt, "run_engine", lambda *a, **k: ran.append(a) or (0, []))
    rc, out = _run(capsys, "openai/gpt-6-luna", "--check")
    assert rc == 0 and not ran and all("--plan" in c for c in calls)
    for step in ("folder", "key", "config", "model", "plan"):
        assert re.search(rf"PASS  {step}", out), out
    assert "balance $4.00" in out and out.rstrip().endswith("test instead of check.")


@pytest.mark.parametrize("key,code", [("sk-rejected", qt.EXIT_KEY_REJECTED), ("sk-limit", qt.EXIT_NO_CREDIT)])
def test_check_collects_every_fix_and_exits_with_the_first_code(fake, monkeypatch, capsys, key, code):
    _planned(monkeypatch)
    monkeypatch.setenv("OPENROUTER_API_KEY", key)
    rc, out = _run(capsys, "openai/gpt-6-lune", "--check")
    assert rc == code
    assert "FIX   key" in out and "FIX   model" in out and "SKIP  plan" in out


def test_stopped_is_not_called_failed():
    from crucibleforge import cli
    from crucibleforge.runner import DRAIN_NOTE
    assert cli._failed_or_stopped("m", DRAIN_NOTE).startswith("m: stopped")
    assert cli._failed_or_stopped("m", "HTTP 500").startswith("m: FAILED")


def test_test_and_check_are_in_the_cli_help(capsys):
    from crucibleforge import cli
    with pytest.raises(SystemExit):
        cli.main(["--help"])
    out = capsys.readouterr().out
    assert re.search(r"^\s+test\s", out, re.M) and re.search(r"^\s+check\s", out, re.M)


# ---------------------------------------------------------------- a local server that asks for a key (4.5.1)
LOCAL_KEY = "local-secret-e2e-0001"
LOCAL_IDS = ["org/repo-a/model-a-Q8_0.gguf", "org/repo-b/small-Q8_0.gguf"] + [f"org/other-{i}/f.gguf" for i in range(30)]


class _Locked(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        if (self.headers.get("Authorization") or "") != f"Bearer {LOCAL_KEY}":
            b = b'{"error": "unauthorized"}'
            self.send_response(401)
        elif self.path.endswith("/models"):
            b = json.dumps({"data": [{"id": i, "max_model_len": 65536} for i in LOCAL_IDS]}).encode()
            self.send_response(200)
        else:
            b = b"{}"
            self.send_response(404)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)


@pytest.fixture()
def locked():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Locked)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}/v1"
    srv.shutdown()


def test_local_server_key_missing_wrong_and_right(locked, monkeypatch):
    monkeypatch.delenv("LOCAL_API_KEY", raising=False)
    with pytest.raises(qt.Fail) as e:
        qt.probe_local(locked, LOCAL_IDS[0], None)
    assert e.value.code == qt.CODES["LOCAL_KEY_NEEDED"] == 37
    assert "-e LOCAL_API_KEY=" in "\n".join(e.value.lines)
    monkeypatch.setenv("LOCAL_API_KEY", "wrong-key-xyz")
    with pytest.raises(qt.Fail) as e:
        qt.probe_local(locked, LOCAL_IDS[0], "LOCAL_API_KEY")
    text = "\n".join(e.value.lines)
    assert e.value.code == qt.CODES["LOCAL_KEY_REJECTED"] == 38 and "LOCAL_API_KEY" in text and "wrong-key-xyz" not in text
    monkeypatch.setenv("LOCAL_API_KEY", LOCAL_KEY)
    assert qt.probe_local(locked, LOCAL_IDS[0], "LOCAL_API_KEY") == 65536


def test_judge_key_is_its_own_or_the_model_servers_on_the_same_address(monkeypatch):
    monkeypatch.delenv("JUDGE_LOCAL_API_KEY", raising=False)
    monkeypatch.setenv("LOCAL_API_KEY", "k")
    assert qt.local_key_env("model", "http://h:1234/v1") == "LOCAL_API_KEY"
    assert qt.local_key_env("judge", "http://h:1234/v1", "http://h:1234/v1") == "LOCAL_API_KEY"
    assert qt.local_key_env("judge", "http://h:8000/v1", "http://h:1234/v1") is None
    monkeypatch.setenv("JUDGE_LOCAL_API_KEY", "j")
    assert qt.local_key_env("judge", "http://h:8000/v1", "http://h:1234/v1") == "JUDGE_LOCAL_API_KEY"
    monkeypatch.delenv("LOCAL_API_KEY")
    assert qt.local_key_env("model", "http://h:1234/v1") is None


def test_wrong_local_id_lists_the_servers_ids_exactly(locked, monkeypatch):
    monkeypatch.setenv("LOCAL_API_KEY", LOCAL_KEY)
    with pytest.raises(qt.Fail) as e:
        qt.probe_local(locked, "org/repo-a/model-a-Q8_0", "LOCAL_API_KEY")
    lines = e.value.lines
    assert e.value.code == qt.CODES["LOCAL_NO_MODEL"]
    assert "    org/repo-a/model-a-Q8_0.gguf" in lines and lines.index("    org/repo-a/model-a-Q8_0.gguf") < 4
    assert any("more" in ln for ln in lines) and len([ln for ln in lines if ln.startswith("    org/")]) == 20


def test_check_local_with_key_passes_and_writes_only_the_variable_name(locked, fake, monkeypatch, capsys):
    _planned(monkeypatch)
    saved = []
    monkeypatch.setattr(qt, "save_cfg", lambda cfg: saved.append(json.dumps(cfg)))
    monkeypatch.delenv("JUDGE_LOCAL_API_KEY", raising=False)
    monkeypatch.setenv("LOCAL_API_KEY", LOCAL_KEY)
    rc, out = _run(capsys, LOCAL_IDS[0], "--check", "--local", locked, "--judge-local", locked, LOCAL_IDS[1])
    assert rc == 0, out
    assert "PASS  model" in out and "65536" in out and "PASS  judge" in out and "fits" in out
    assert LOCAL_KEY not in out and saved and not any(LOCAL_KEY in s for s in saved)
    local = [s for s in saved if '"hosted": false' in s]
    assert len(local) == 2 and all('"api_key_env": "LOCAL_API_KEY"' in s for s in local), saved
    monkeypatch.delenv("LOCAL_API_KEY")
    rc, out = _run(capsys, LOCAL_IDS[0], "--check", "--local", locked)
    assert rc == 37 and "FIX   model" in out and "-e LOCAL_API_KEY=" in out


@pytest.mark.parametrize("name,art", [("Anthropic", "an"), ("OpenAI", "an"), ("Google", "a"), ("Groq", "a"),
                                      ("xAI", "an"), ("Hugging Face", "a"), ("Perplexity", "a"), ("HF", "an"),
                                      ("Unified", "a"), ("Ollama", "an")])
def test_the_article_fits_the_service_name(name, art):
    assert qt.article(name) == art


def test_another_services_key_reads_right_for_every_known_prefix():
    for prefix, who in qt.OTHER_KEYS:
        with pytest.raises(qt.Fail) as e:
            qt.key_format(prefix + "x" * 30)
        text = "\n".join(e.value.lines)
        assert f"looks like {qt.article(who)} {who} key" in text
        assert " a Anthropic" not in text and " a OpenAI" not in text and " a xAI" not in text


def test_no_folder_message_has_no_linux_only_hint():
    from crucibleforge.firstrun import no_folder_lines
    text = "\n".join(no_folder_lines())
    assert ":Z" not in text and "Fedora" not in text
    assert "PowerShell" in text and "cmd.exe" in text and "Linux/macOS" in text


# ---------------------------------------------------------------- a later run of the same command (4.5.1)
def test_a_complete_folder_is_recognised_before_planning(fake, monkeypatch, capsys):
    calls = _planned(monkeypatch)
    res = qt.results_dir()
    res.mkdir(parents=True, exist_ok=True)
    (res / "transcripts_gpt-6-luna.jsonl").write_text("{}\n", encoding="utf-8")
    args = ["--models", "gpt-6-luna", "--judge", qt.DEFAULT_JUDGE]
    assert not qt.record_complete("gpt-6-luna", args, ["Overall 1.0 of 34", "Code    pending"])
    assert qt.record_complete("gpt-6-luna", args, ["Overall 1.0 of 34 = 1.0 standard + 0.0 bonus"])
    assert qt.nothing_left("gpt-6-luna", args)
    ran = []
    monkeypatch.setattr(qt, "run_engine", lambda *a, **k: ran.append(a) or (0, []))
    monkeypatch.setattr(qt, "summary_lines", lambda *a: ["Overall 1.0 of 34"])
    rc, out = _run(capsys, "openai/gpt-6-luna", "--yes")
    assert rc == 0 and not ran and not calls, out             # no plan, no run
    assert "Nothing left to run: the results are complete" in out
    (res / "transcripts_gpt-6-luna.jsonl").write_text("{}\n{}\n", encoding="utf-8")   # any change: plan again
    assert not qt.nothing_left("gpt-6-luna", args)


def test_nothing_owed_is_said_plainly_and_no_rows_to_run(fake, monkeypatch, capsys):
    _planned(monkeypatch)
    plan = ("price (estimate, typical): ~$0.40 expected (a typical model), $0.90 for a model that thinks at length\n"
            "judge cost (estimate): ~$0.20 expected, up to $0.35\n-> ~42 min\nT1 — every model: 279 rows\n"
            "  owed on resume: none\n")
    monkeypatch.setattr(qt, "engine_out", lambda *a, **k: (0, plan))
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))
    rc, out = _run(capsys, "openai/gpt-6-luna")
    assert "Every answer is already in this folder" in out
    assert "stopped before the end" not in out and "rows to run" not in out


def test_the_adult_line_never_names_a_command_a_docker_user_cannot_type(tmp_path, monkeypatch):
    monkeypatch.setenv("CRUCIBLEFORGE_CONFIG", str(tmp_path / "models.yaml"))
    (tmp_path / "results").mkdir()
    rep = {"models": {"m": {"segments45": {}, "scorecard": {}, "nsfw_plus": {
        "state": "pending", "judge_pending": "not judged yet: 73 of 73 unit(s) have no judge vote (run `crucibleforge judge`)"}}}}
    (tmp_path / "results" / "report.json").write_text(json.dumps(rep), encoding="utf-8")
    monkeypatch.setattr(qt, "results_dir", lambda: tmp_path / "results")
    line = next(ln for ln in qt.summary_lines("m", "j") if ln.startswith("Adult"))
    assert "crucibleforge judge" not in line and "the same command asks the judge again" in line


def test_one_model_cost_one_judge_cost_and_their_total(tmp_path):
    led = tmp_path / "l.jsonl"
    led.write_text('{"cost_usd": 0.20}\n{"cost_usd": 0.11}\n{"cost_usd": 0.2247, "kind": "judge"}\n', encoding="utf-8")
    lines = qt.cost_lines(str(led), ["  Run cost: $0.31"])
    assert lines[0].startswith("Cost of this run: model $0.3100 + judge $0.2247 = $0.5347")
    assert "Run cost: $0.31" in lines[1] and "one standard Code and Chat run" in lines[1]
    assert qt.cost_lines(None, []) == []
    # no ledger: the engine's own lines (each phase's run summary, the whole-run judge line)
    tail = ["  m: 576 rows, load 0.0s (openrouter), $0.3792", "  m: 8 rows, load 0.0s (openrouter), $0.0089",
            "judge cost, whole run (every judge phase): $0.2247 for 871 call(s) to q on openrouter (billed apart)"]
    assert qt.cost_lines(None, tail)[0].startswith("Cost of this run: model $0.3881 + judge $0.2247 = $0.6128")
