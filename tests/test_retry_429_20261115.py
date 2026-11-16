"""HTTP 429 is waited out, not a transport failure (lead 2026-11-15; a judge run stopped at 06:32).

StudioForge answers 429 when its per-address credential lockout fires (1 s doubling to at most 300 s,
with Retry-After; code ``too_many_credential_attempts``) and, for one key, at a 20-requests-a-minute
cap (Retry-After at most 60 s; code ``key_rate_limited``). A 429 used to be a plain TransportError:
three tries at 2 s and 4 s, Retry-After ignored, then an errored row. Now: wait Retry-After (5 s when
absent), each wait capped at 300 s, at most ~15 minutes of waiting per call, then it is an ordinary
transport error. The waits spend no transport retry. One log line per wait names the server's error
code, never the key. The management calls (lease, load, unload, status) wait the same way."""
from __future__ import annotations

import http.server
import json
import logging
import socketserver
import threading

import pytest

from crucibleforge import api, studioforge

KEY = "sk-test-SECRET-key-0042"


class _H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _serve(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n:
            self.rfile.read(n)
        srv = self.server
        srv.paths.append(self.path)
        q = srv.queue
        status, body, headers = q.pop(0) if len(q) > 1 else q[0]
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(status)
        ctype = "text/event-stream" if status == 200 and isinstance(body, bytes) else "application/json"
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    do_GET = do_POST = do_DELETE = _serve


class _Srv(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True

    def server_bind(self):
        socketserver.TCPServer.server_bind(self)
        self.server_name = "localhost"


@pytest.fixture
def stub():
    srv = _Srv(("127.0.0.1", 0), _H)
    srv.queue, srv.paths = [], []
    t = threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
    t.start()
    srv.url = "http://127.0.0.1:%d" % srv.server_address[1]
    yield srv
    srv.shutdown()
    srv.server_close()


@pytest.fixture
def waits(monkeypatch):
    """The pauses THIS test's calls made. ``api.time`` is the global ``time`` module, so a background
    thread an earlier test left running (a lease toucher, a poller) also calls the patched sleep: only
    the test's own thread is recorded (and skips the pause); any other thread really sleeps, as before.
    Without this the 507 test read four stray 2 s sleeps in a long single-process run (D, 2026-11-15)."""
    import time as _time
    real, me, out = _time.sleep, threading.current_thread(), []

    def fake(s):
        if threading.current_thread() is me:
            out.append(s)
        else:
            real(s)

    monkeypatch.setattr(api.time, "sleep", fake)
    monkeypatch.setattr(studioforge.time, "sleep", fake)
    return out


OK = (200, b'data: {"model": "m", "choices": [{"index": 0, "delta": {"content": "hi"}, '
           b'"finish_reason": "stop"}]}\n\ndata: [DONE]\n\n', {})


def _lock(ra=None, code="too_many_credential_attempts"):
    return (429, {"error": {"code": code, "message": "slow down"}},
            ({"Retry-After": str(ra)} if ra is not None else {}))


def _chat(srv):
    return api.stream_chat_retried(srv.url, KEY, "m", [{"role": "user", "content": "x"}],
                                   max_tokens=16, verify_model=False)


def test_a_429_with_retry_after_is_waited_out_and_spends_no_retry(stub, waits, caplog):
    # four lockouts in a row: more than MAX_TRANSPORT_RETRIES (3), so only a wait that spends no
    # retry can reach the success
    stub.queue[:] = [_lock(7), _lock(7), _lock(7), _lock(7), OK]
    with caplog.at_level(logging.INFO, logger="crucibleforge"):
        r = _chat(stub)
    assert r.response_text == "hi"
    assert waits == [7.0, 7.0, 7.0, 7.0]
    lines = [rec.getMessage() for rec in caplog.records if "429" in rec.getMessage()]
    assert len(lines) == 4 and all("too_many_credential_attempts" in m for m in lines)
    assert not any(KEY in rec.getMessage() for rec in caplog.records)


def test_no_retry_after_waits_five_seconds_and_a_long_one_is_capped_at_300(stub, waits):
    stub.queue[:] = [_lock(None, "key_rate_limited"), _lock(5000), OK]
    assert _chat(stub).response_text == "hi"
    assert waits == [api.RATE_LIMIT_DEFAULT_S, api.RATE_LIMIT_MAX_WAIT_S] == [5.0, 300.0]


def test_after_about_15_minutes_of_waiting_it_is_an_ordinary_transport_error(stub, waits):
    stub.queue[:] = [_lock(300)]               # every answer is a lockout
    with pytest.raises(api.TransportError) as ei:
        _chat(stub)
    assert sum(waits) == api.RATE_LIMIT_BUDGET_S == 900.0
    assert waits == [300.0, 300.0, 300.0]
    assert "429" in str(ei.value) and KEY not in str(ei.value)


def test_a_normal_call_is_unchanged(stub, waits):
    stub.queue[:] = [OK]
    assert _chat(stub).response_text == "hi"
    assert waits == [] and len(stub.paths) == 1


def test_a_503_with_a_retry_hint_still_uses_the_transport_retry(stub, waits):
    busy = (503, {"error": {"code": "busy", "retry_after_s": 3}}, {})
    stub.queue[:] = [busy, OK]
    assert _chat(stub).response_text == "hi"
    assert waits == [3.0]                      # max(backoff 2 s, hint 3 s), as before
    stub.queue[:] = [busy]
    stub.paths.clear()
    waits.clear()
    with pytest.raises(api.VramContention):
        _chat(stub)
    assert len(stub.paths) == api.MAX_TRANSPORT_RETRIES == 3


def test_a_full_507_is_still_terminal(stub, waits):
    stub.queue[:] = [(507, {"error": {"code": "insufficient_vram"}}, {})]
    with pytest.raises(api.VramExhausted):
        _chat(stub)
    assert waits == [] and len(stub.paths) == 1


def test_a_management_call_waits_out_a_429_too(stub, waits, caplog):
    stub.queue[:] = [_lock(2), (200, {"leases": []}, {})]
    with caplog.at_level(logging.INFO, logger="crucibleforge"):
        code, data = studioforge._mgmt("GET", stub.url + "/v1", KEY, None, "/api/leases")
    assert code == 200 and data["leases"] == []
    assert waits == [2.0]
    assert any("too_many_credential_attempts" in r.getMessage() for r in caplog.records)
    assert not any(KEY in r.getMessage() for r in caplog.records)


def test_a_management_429_past_the_budget_is_returned_as_before(stub, waits):
    stub.queue[:] = [_lock(300)]
    code, data = studioforge._mgmt("POST", stub.url + "/v1", KEY, None, "/api/leases", json={})
    assert code == 429 and sum(waits) == 900.0


def test_a_billing_429_keeps_the_plain_transport_path(stub, waits):
    """A hosted 429 that says the quota is spent is billing, not a lockout: three tries at 2 s and
    4 s as before, never the 15-minute wait."""
    stub.queue[:] = [(429, {"error": {"code": "insufficient_quota",
                                      "message": "You exceeded your current quota"}}, {"Retry-After": "20"})]
    with pytest.raises(api.TransportError) as ei:
        _chat(stub)
    assert not isinstance(ei.value, api.RateLimited)
    assert waits == [2.0, 4.0] and len(stub.paths) == 3
