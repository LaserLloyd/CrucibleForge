"""A tiny read-only results page for the container (4.5.1): open the run's results with one click.

``view`` serves the finished report until Ctrl-C; ``test ... --serve`` runs the same server beside the test and shows
a plain "running" page until the report exists. Docker Desktop shows a published port (``-p``) as a link in its
container list; the documented form is ``-p 127.0.0.1:8377:8377`` so the page is offered to this computer only.

Safety, by construction:
* an allow-list of three names, read from the results folder only: ``/`` (the report, or the running page),
  ``/report.html`` and ``/report.md``. Nothing else under ``/data`` is reachable: not ``models.yaml``, not the spend
  ledger, not the transcripts (reply text, the adult page's included). Any other path is 404, whatever it contains.
  The report is self-contained (styles, fonts and its one script are inline; it links no other file), so the
  allow-list is all it needs.
* a URL is never turned into a file path: the three names are fixed, and each file is opened only when it resolves
  inside the results folder and is a regular file (a symlink pointing elsewhere is refused).
* GET and HEAD only; no directory listing; no CORS headers; a Content-Security-Policy that allows the report's
  inline styles and fonts and its own inline script (by hash) and nothing else: no other script, no requests, no
  frames, no forms.
* it runs as the run user (the entrypoint drops root before this starts). Model-written code runs in the jail with
  no sockets at all, so it cannot reach this port (the jail self-test probes it).
Anyone who can reach the port can read the results: publish it on 127.0.0.1 only."""
from __future__ import annotations

import base64
import hashlib
import html
import json
import os
import re
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

#: the container port: away from the ports model servers and dev tools use (1234, 3000, 5000, 8000, 8080, 8765,
#: 11434) and from the GUI's 8777
DEFAULT_PORT = 8377
CSP = ("default-src 'none'; style-src 'unsafe-inline'; img-src data:; font-src data:; base-uri 'none'; "
       "form-action 'none'; frame-ancestors 'none'")
_INLINE_JS = re.compile(rb"<script>(.*?)</script>", re.S)


def csp_for(body: bytes) -> str:
    """The policy, plus a hash for each inline script the page carries (the board's sorting and filters): only those
    exact scripts run, and with connect-src unset (default 'none') they cannot send anything anywhere."""
    hashes = sorted({"'sha256-" + base64.b64encode(hashlib.sha256(m.group(1)).digest()).decode() + "'"
                     for m in _INLINE_JS.finditer(body)})
    return CSP + ("; script-src " + " ".join(hashes) if hashes else "")
ALLOWED = {"/report.html": ("report.html", "text/html; charset=utf-8"),
           "/report.md": ("report.md", "text/markdown; charset=utf-8")}
REFRESH_S = 15
GETTING_READY = "getting ready: checking the folder, the key and the model, then planning the test (1 to 3 minutes)"


def port_from_env(default: int = DEFAULT_PORT) -> int:
    try:
        p = int(os.environ.get("PORT") or default)
    except ValueError:
        return default
    return p if 1 <= p <= 65535 else default


def _results() -> Path:
    from .config import results_dir
    return results_dir()


def _safe_file(name: str) -> Path | None:
    """The allow-listed file, only if it is a regular file that resolves inside the results folder."""
    base = _results()
    try:
        root = base.resolve()
        p = (base / name).resolve()
    except OSError:
        return None
    if not p.is_relative_to(root) or not p.is_file():
        return None
    return p


class Status:
    """What the running page shows; the test updates it, the files fill in the counts."""

    def __init__(self, model: str = "", label: str = "", total: int | None = None, ledger: str | None = None):
        self.model, self.label, self.total, self.ledger = model, label, total, ledger
        self.step = "starting"
        self.started = time.time()
        self.finished: str | None = None

    def answers(self) -> int | None:
        if not self.label:
            return None
        from .config import transcript_path
        p = transcript_path(self.label)
        try:
            with open(p, "rb") as f:
                return sum(1 for _ in f)
        except OSError:
            return 0

    def cost(self) -> float | None:
        if not self.ledger:
            return None
        total = 0.0
        try:
            for ln in Path(self.ledger).read_text(encoding="utf-8").splitlines():
                try:
                    total += float(json.loads(ln).get("cost_usd") or 0.0)
                except (ValueError, TypeError, AttributeError):
                    continue
        except OSError:
            return None
        return total


def running_page(st: Status | None) -> str:
    rows = []
    if st is not None:
        n = st.answers()
        mins = (time.time() - st.started) / 60
        rows = [("Model", st.model or "-"),
                ("Step", st.finished or st.step),
                ("Answers stored", f"{n}" + (f" of about {st.total}" if st.total and n is not None else "")
                 if n is not None else "-"),
                ("Cost so far", f"${st.cost():.4f}" if st.cost() is not None else "-"),
                ("Started", time.strftime("%Y-%m-%d %H:%M", time.localtime(st.started)) + f" ({mins:.0f} min ago)")]
        head = ("The test has ended" if st.finished else "Getting ready" if st.step == GETTING_READY
                else "The test is running")
    else:
        head = "No report in this folder yet"
    trs = "".join(f"<tr><th>{html.escape(k)}</th><td>{html.escape(str(v))}</td></tr>" for k, v in rows)
    return (f"<!doctype html><html lang=en><head><meta charset=utf-8><meta http-equiv=refresh content={REFRESH_S}>"
            "<meta name=viewport content='width=device-width,initial-scale=1'><title>CrucibleForge results</title>"
            "<style>body{font:16px/1.5 system-ui,sans-serif;max-width:40em;margin:2em auto;padding:0 1em}"
            "th{text-align:left;padding-right:1em;font-weight:600}td,th{padding:.2em 0}small{color:#666}</style>"
            f"</head><body><h1>{html.escape(head)}</h1><table>{trs}</table>"
            f"<p>This page refreshes every {REFRESH_S} seconds and shows the report as soon as it exists.</p>"
            "<p><small>Anyone who can reach this port can read these results: publish it as "
            "<code>-p 127.0.0.1:8377:8377</code> so it stays on this computer.</small></p></body></html>")


def make_handler(status: Status | None):
    class H(BaseHTTPRequestHandler):
        server_version = "crucibleforge-view"
        sys_version = ""

        def log_message(self, *a):
            pass

        def _send(self, code: int, body: bytes, ctype: str, head_only: bool):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Content-Security-Policy", csp_for(body) if ctype.startswith("text/html") else CSP)
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            if not head_only:
                self.wfile.write(body)

        def _serve(self, head_only: bool):
            path = unquote(urlsplit(self.path).path)
            if path in ("/", "/index.html"):
                f = _safe_file("report.html")
                if f is None:
                    return self._send(200, running_page(status).encode(), "text/html; charset=utf-8", head_only)
                return self._send(200, f.read_bytes(), "text/html; charset=utf-8", head_only)
            if path in ALLOWED:
                name, ctype = ALLOWED[path]
                f = _safe_file(name)
                if f is not None:
                    return self._send(200, f.read_bytes(), ctype, head_only)
            return self._send(404, b"not found\n", "text/plain; charset=utf-8", head_only)

        def do_GET(self):
            self._serve(False)

        def do_HEAD(self):
            self._serve(True)

        def _refuse(self):
            self._send(405, b"read only\n", "text/plain; charset=utf-8", self.command == "HEAD")

        do_POST = do_PUT = do_DELETE = do_PATCH = do_OPTIONS = _refuse
    return H


class PortTaken(OSError):
    pass


def start(port: int, status: Status | None = None, host: str = "0.0.0.0") -> ThreadingHTTPServer:
    """Serve in a daemon thread. Inside the container it listens on all addresses so a published port reaches it;
    the host side of `-p` decides who can (127.0.0.1: this computer only)."""
    try:
        srv = ThreadingHTTPServer((host, port), make_handler(status))
    except OSError as e:
        raise PortTaken(str(e)) from e
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, name="results-page", daemon=True).start()
    return srv


def taken_lines(port: int) -> list[str]:
    from .firstrun import HEADLINES
    return [f"{HEADLINES['PORT_TAKEN']} {port} inside the container.",
            f"  Pick another: add -e PORT=<n> and publish it the same way: -p 127.0.0.1:<n>:<n>. (Docker itself says "
            f"'port is already allocated' when the port is taken on your computer: change the first number, "
            f"-p 127.0.0.1:8378:{port}.)"]


def wait_forever(srv) -> None:
    """Keep serving until Ctrl-C / docker stop."""
    import signal
    stop = threading.Event()

    def on_sig(*_):
        stop.set()
    old = signal.signal(signal.SIGINT, on_sig)
    old_t = signal.signal(signal.SIGTERM, on_sig)
    try:
        while not stop.wait(1.0):
            pass
    finally:
        signal.signal(signal.SIGINT, old)
        signal.signal(signal.SIGTERM, old_t)
        srv.shutdown()


def main(argv: list[str] | None = None) -> int:
    import argparse
    from .firstrun import CODES
    ap = argparse.ArgumentParser(prog="view", description="Serve this folder's results page (read only) until Ctrl-C. "
                                 "Publish it as -p 127.0.0.1:8377:8377 and open http://localhost:8377.")
    ap.add_argument("--port", type=int, default=port_from_env(), help=f"container port (default {DEFAULT_PORT}, or $PORT)")
    a = ap.parse_args(sys.argv[1:] if argv is None else argv)
    try:
        srv = start(a.port)
    except PortTaken:
        print("\n".join(taken_lines(a.port)), flush=True)
        return CODES["PORT_TAKEN"]
    has = _safe_file("report.html") is not None
    print(("Your results are open at" if has else "No report in this folder yet; the page is at")
          + f" http://localhost:{a.port} (press Ctrl-C to close).", flush=True)
    wait_forever(srv)
    return 0


if __name__ == "__main__":
    sys.exit(main())
