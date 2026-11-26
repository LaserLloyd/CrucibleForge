"""4.5.1: a local server with fewer slots than requests in flight (the rig's 2-slot StudioForge, 2026-11-26).

The client kept more requests in flight than the server had slots; the rest waited in the server's queue, silent,
and that wait counted toward the 300 s stall clock: three "no data from the server for 300s" ended a healthy run.
These tests reproduce it with a small slotted server (one slot, a silent queue, answers that stream for a while)."""
from __future__ import annotations

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from crucibleforge import api, providers, quicktest as qt


class _Slotted(BaseHTTPRequestHandler):
    """One slot: a second request waits silently (no status line, no byte) until the first is answered.
    `hold` = seconds the answer streams for (a chunk every 0.2 s); `then_silent` = stop sending mid-answer."""
    protocol_version = "HTTP/1.1"
    slot = threading.Semaphore(1)
    hold = 1.2
    then_silent = 0.0
    props: dict | None = None

    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path.endswith("/props") and self.props is not None:
            body = json.dumps(self.props).encode()
        elif self.path.endswith("/models"):
            body = json.dumps({"data": [{"id": "m", "studioforge": {"parallel": 3}}, {"id": "plain"}]}).encode()
        else:
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        with self.slot:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()

            def ch(obj):
                d = ("data: " + json.dumps(obj) + "\n\n").encode()
                self.wfile.write(b"%x\r\n%s\r\n" % (len(d), d))
                self.wfile.flush()
            n = max(1, int(self.hold / 0.2))
            for i in range(n):
                ch({"choices": [{"index": 0, "delta": {"content": f"w{i} "}, "finish_reason": None}]})
                time.sleep(0.2)
            if self.then_silent:
                time.sleep(self.then_silent)
            ch({"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 5, "completion_tokens": n, "cost": 0.0123}})
            d = b"data: [DONE]\n\n"
            self.wfile.write(b"%x\r\n%s\r\n0\r\n\r\n" % (len(d), d))


@pytest.fixture()
def server():
    _Slotted.slot = threading.Semaphore(1)
    _Slotted.hold, _Slotted.then_silent, _Slotted.props = 1.2, 0.0, None
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Slotted)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}/v1"
    srv.shutdown()


def _three_at_once(url, **kw):
    def one(_):
        try:
            return api.stream_chat(url, "", "m", [{"role": "user", "content": "hi"}], max_tokens=50,
                                   verify_model=False, **kw)
        except api.StreamStalled as e:
            return e
    with ThreadPoolExecutor(3) as ex:
        return list(ex.map(one, range(3)))


def test_queue_wait_counted_as_a_stall_reproduces_the_bug(server):
    """Without a first-byte limit (the old behaviour) the third request waits ~2.4 s in the queue and is cut at the
    1 s stall limit, although the server is healthy and answering."""
    got = _three_at_once(server, stall_s=1.0)
    assert any(isinstance(r, api.StreamStalled) for r in got)


def test_queue_wait_is_not_a_stall_with_a_first_byte_limit(server):
    got = _three_at_once(server, stall_s=1.0, first_byte_s=30.0)
    assert all(isinstance(r, api.ChatResult) and r.response_text.startswith("w0") for r in got), got


def test_silence_after_the_answer_started_is_still_a_stall(server):
    _Slotted.then_silent = 3.0
    with pytest.raises(api.StreamStalled) as e:
        api.stream_chat(server, "", "m", [{"role": "user", "content": "hi"}], max_tokens=50, verify_model=False,
                        stall_s=1.0, first_byte_s=30.0)
    assert "no data from the server" in str(e.value)


def test_openrouter_usage_cost_is_the_billed_figure(server):
    r = api.stream_chat(server, "", "m", [{"role": "user", "content": "hi"}], max_tokens=50, verify_model=False)
    assert r.provider_cost_usd == pytest.approx(0.0123)
    assert api.provider_cost_usd({"cost": -1}) is None and api.provider_cost_usd({"cost": True}) is None


def test_a_local_openai_server_gets_one_request_at_a_time_unless_told():
    cfg = {"providers": {"mine": {"type": "openai", "base_url": "http://192.0.2.5:8080/v1", "hosted": False},
                         "lan": {"type": "openai", "base_url": "http://localhost:8080/v1"},
                         "said": {"type": "openai", "base_url": "http://localhost:8080/v1", "concurrency": 3},
                         "or": {"type": "openai", "base_url": "https://openrouter.ai/api/v1"}}}
    providers._CACHE.clear()
    assert providers.get_provider(cfg, "mine").concurrency == 1
    assert providers.get_provider(cfg, "lan").concurrency == 1
    assert providers.get_provider(cfg, "said").concurrency == 3
    assert providers.get_provider(cfg, "or").concurrency == providers.API_DEFAULT_CONCURRENCY
    providers._CACHE.clear()


def test_the_test_asks_the_server_how_many_it_serves_at_once(server):
    n, why = qt.local_slots(server, "m")
    assert n == 3 and "model list" in why                      # StudioForge's /v1/models entry
    _Slotted.props = {"total_slots": 2}
    n, why = qt.local_slots(server, "plain")
    assert n == 2 and "/props" in why                          # llama-server
    _Slotted.props = None
    n, why = qt.local_slots(server, "plain")
    assert n == 1 and "does not say" in why and "--parallel" in why
    n, why = qt.local_slots("http://127.0.0.1:9/v1", "x")      # nothing there: tolerated
    assert n == 1


def test_parallel_option_is_checked():
    assert qt.parse(["m", "--local", "http://x", "--parallel", "2"]).parallel == 2
    for bad in ("0", "-1", "x", str(qt.MAX_LOCAL_PARALLEL + 1)):
        with pytest.raises(SystemExit):
            qt.parse(["m", "--parallel", bad])


def test_a_stalled_local_run_says_what_was_seen_and_names_parallel_1():
    tail = ["transport failure on X: no data from the server for 300s (stalled after 300s)"] * 3 + [
        "HTTP 429 on /chat/completions (no error code): waiting 5s (0s of 900s spent)",
        "FAILED: 3 consecutive transport failures for m — server unreachable/unstable, aborting this model"]
    code, lines = qt.diagnose(tail, local=True)
    text = "\n".join(lines)
    assert code == qt.CODES["STALLED"] == 39
    assert "3 request(s)" in text and "Nothing is lost" in text and "--parallel 1" in text


def test_a_429_that_was_waited_out_is_not_why_a_run_ended():
    code, _ = qt.diagnose(["HTTP 429 on /chat/completions (no error code): waiting 5s (0s of 900s spent)"])
    assert code == qt.EXIT_RUN_FAILED
    code, _ = qt.diagnose(["HTTP 429 (no error code) still standing after 900s of waiting — giving up"])
    assert code == qt.CODES["RATE_LIMITED"]


def test_rate_limit_waits_back_off_and_stay_bounded():
    waits, spent = [], 0.0
    while True:
        w = api.rate_limit_wait(None, spent)
        if w <= 0:
            break
        waits.append(w)
        spent += w
    assert waits[:5] == [5.0, 5.0, 10.0, 20.0, 40.0] and max(waits) <= api.RATE_LIMIT_MAX_WAIT_S
    assert spent == pytest.approx(api.RATE_LIMIT_BUDGET_S)
    assert api.rate_limit_wait(30, 0) == 30
