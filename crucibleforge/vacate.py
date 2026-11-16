"""Vacate receiver for a GPU lease (StudioForge D56).

A model instance that borrows shared cards (the judge copy on GPUs 2,3, which
SpeechForge voice and the memory embedder also use) registers a
``vacate_url`` + ``vacate_token`` on its lease. When a strictly better class
asks for those cards, StudioForge POSTs ONE request to that URL:

    headers  X-SF-Vacate-Token: <our token>, X-SF-Lease-Id: <lease id>
    body     {"lease_id", "devices", "requester", "requester_priority",
              "deadline_s", "deadline_at"}

and the holder has ``deadline_s`` (180 s by default) to DELETE the lease.
This module is the receiver: a tiny HTTP server on one address/port, one
token minted per process (never logged), answering 202 to a genuine request
for our lease and 403/404/400 to anything else. What vacating MEANS is the
caller's ``on_vacate`` callback (judge: stop sending, drop in-flight rows so
they stay pending, release the lease, free the labels for the other shard).
"""
from __future__ import annotations

import hmac
import json
import logging
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

log = logging.getLogger(__name__)

TOKEN_HEADER = "X-SF-Vacate-Token"
LEASE_HEADER = "X-SF-Lease-Id"
MAX_BODY = 16384
#: the holder's budget after a vacate request (StudioForge DEFAULT_VACATE_TIMEOUT_S)
DEFAULT_DEADLINE_S = 180.0


class VacateListener:
    """Listen for a vacate request for ONE lease.

    ``host``: the address the rig can reach (this box's tailnet address; a
    loopback address only when the server runs on this box). ``port`` 0 =
    any free port. ``lease_id`` is set once the lease exists (a request for
    another lease id is refused). ``on_vacate(body)`` runs once, on its own
    thread, for the first genuine request."""

    def __init__(self, host: str, port: int = 0, on_vacate=None, path: str = "/vacate"):
        self.token = secrets.token_urlsafe(32)
        self.lease_id: str | None = None
        self.on_vacate = on_vacate
        self.path = path
        self.event = threading.Event()
        self.request: dict | None = None
        self._lock = threading.Lock()
        listener = self

        class _H(BaseHTTPRequestHandler):
            def log_message(self, fmt, *args):  # quiet: never echo headers
                log.debug("vacate listener: " + fmt, *args)

            def _answer(self, code: int, msg: str) -> None:
                data = json.dumps({"status": msg}).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                self._answer(405, "POST only")

            def do_POST(self):
                if self.path.split("?", 1)[0] != listener.path:
                    return self._answer(404, "not here")
                got = self.headers.get(TOKEN_HEADER) or ""
                if not hmac.compare_digest(got.encode(), listener.token.encode()):
                    log.warning("vacate listener: request with a wrong/missing token refused")
                    return self._answer(403, "bad token")
                try:
                    n = int(self.headers.get("Content-Length") or 0)
                except ValueError:
                    n = 0
                if n > MAX_BODY:
                    return self._answer(413, "body too large")
                raw = self.rfile.read(n) if n else b"{}"
                try:
                    body = json.loads(raw or b"{}")
                except json.JSONDecodeError:
                    return self._answer(400, "bad json")
                if not isinstance(body, dict):
                    return self._answer(400, "bad body")
                lid = str(self.headers.get(LEASE_HEADER) or body.get("lease_id") or "")
                if listener.lease_id and lid and lid != listener.lease_id:
                    return self._answer(404, "not our lease")
                listener._fire(body)
                return self._answer(202, "vacating")

        self.server = ThreadingHTTPServer((host, int(port)), _H)
        self.server.daemon_threads = True
        self.host = host
        self.port = self.server.server_address[1]
        self._thread = threading.Thread(target=self.server.serve_forever,
                                        name="crucibleforge-vacate-listener", daemon=True)

    @property
    def url(self) -> str:
        h = f"[{self.host}]" if ":" in self.host else self.host
        return f"http://{h}:{self.port}{self.path}"

    def start(self) -> "VacateListener":
        self._thread.start()
        log.info("vacate listener on %s (token minted for this process, not logged)", self.url)
        return self

    def _fire(self, body: dict) -> None:
        with self._lock:
            if self.event.is_set():
                return
            self.request = body
            self.event.set()
        log.warning("VACATE requested for lease %s by %s (priority %s, %ss to release)",
                    body.get("lease_id") or self.lease_id, body.get("requester"),
                    body.get("requester_priority"), body.get("deadline_s"))
        if self.on_vacate is not None:
            threading.Thread(target=self.on_vacate, args=(body,),
                             name="crucibleforge-vacate", daemon=True).start()

    def close(self) -> None:
        try:
            self.server.shutdown()
        finally:
            self.server.server_close()
