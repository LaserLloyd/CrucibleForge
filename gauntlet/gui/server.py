"""HTTP server for the Gauntlet GUI (stdlib only).

Endpoints (JSON unless noted):
  GET  /                      index.html
  GET  /static/<file>         app assets
  GET  /api/state             config path, providers, models, judge, cases, run status
  GET  /api/providers?refresh=1   liveness per provider (cached 30 s)
  GET  /api/discover?provider=X   model ids the provider serves
  GET  /api/log?since=N       log lines after sequence N
  GET  /api/report            report.md (+ rendered html)
  GET  /api/report.json       report.json
  GET  /api/pairwise          pairwise.md
  GET  /api/rows?model=L[&case=ID][&failed=1]   transcript rows (drill-down)
  POST /api/run               {models, categories, difficulty, smoke, fresh, samples,
                               judge, then_judge, then_report}
  POST /api/judge             {models, samples, force, judge}
  POST /api/report            {}
  POST /api/pairwise          {models, categories, judge}
  POST /api/stop              cooperative stop
  POST /api/models            add {name, provider, model_id, context_length, price_in, price_out, extra_body}
  POST /api/models/update     {name, enabled?, context_length?, price?, delete?}
  POST /api/providers         add/update {name, type, base_url, api_key_env, api_key, concurrency, headers}
  POST /api/providers/test    {name} → alive + first few model ids
  POST /api/judge-candidates  {candidates: [...]} replace list

Security: loopback binding needs no token unless --token is given; any other
host REQUIRES a token (auto-generated when omitted). The token is presented
once as ?token= (sets an HttpOnly SameSite=Strict cookie) or as the
X-Gauntlet-Token header. State-changing POSTs additionally require a
same-origin request (Sec-Fetch-Site / Origin) — fail closed.
"""
from __future__ import annotations

import collections
import hmac
import json
import logging
import mimetypes
import os
import secrets
import threading
import time
import traceback
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .. import config as cfgmod
from ..config import ConfigError, load_cases, load_config, resolve_models, save_config
from .markdown import md_to_html

log = logging.getLogger("gauntlet.gui")
STATIC_DIR = Path(__file__).parent / "static"


# ------------------------------------------------------------ log capture

class _RingLog(logging.Handler):
    def __init__(self, maxlen=5000):
        super().__init__()
        self.buf: collections.deque = collections.deque(maxlen=maxlen)
        self.seq = 0
        # NOTE: logging.Handler owns ``self.lock`` (an RLock taken by handle());
        # a separate name avoids shadowing it with a non-reentrant lock.
        self._buflock = threading.Lock()
        self.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s",
                                            "%H:%M:%S"))

    def emit(self, record):
        try:
            line = self.format(record)
        except Exception:
            return
        with self._buflock:
            self.seq += 1
            self.buf.append((self.seq, line))

    def since(self, n: int) -> tuple[int, list[str]]:
        with self._buflock:
            lines = [l for s, l in self.buf if s > n]
            return self.seq, lines


RING = _RingLog()


# --------------------------------------------------------------- job state

class Job:
    def __init__(self):
        self.lock = threading.Lock()
        self.thread: threading.Thread | None = None
        self.kind: str | None = None
        self.started: float | None = None
        self.finished: float | None = None
        self.error: str | None = None
        self.result = None
        self.stop_event = threading.Event()

    def running(self) -> bool:
        return bool(self.thread and self.thread.is_alive())

    def status(self) -> dict:
        return {"running": self.running(), "kind": self.kind,
                "started": self.started, "finished": self.finished,
                "error": self.error, "result": self.result}

    def start(self, kind: str, fn):
        with self.lock:
            if self.running():
                raise RuntimeError(f"a {self.kind} job is already running")
            self.kind, self.started, self.finished = kind, time.time(), None
            self.error, self.result = None, None
            self.stop_event.clear()

            def _wrap():
                try:
                    self.result = fn()
                except SystemExit as e:  # runner uses SystemExit for hard aborts
                    self.error = str(e) or "aborted"
                    log.error("%s aborted: %s", kind, self.error)
                except Exception as e:  # noqa: BLE001
                    self.error = f"{type(e).__name__}: {e}"
                    log.error("%s failed: %s\n%s", kind, e, traceback.format_exc())
                finally:
                    self.finished = time.time()

            self.thread = threading.Thread(target=_wrap, name=f"gauntlet-{kind}", daemon=True)
            self.thread.start()


JOB = Job()


class App:
    """Config + cached provider liveness, shared by request handlers."""

    def __init__(self, cfg_path: str | None, token: str | None, require_token: bool):
        self.cfg_path = cfg_path
        self.token = token
        self.require_token = require_token
        self.lock = threading.Lock()
        self._alive: dict[str, tuple[float, bool]] = {}
        self.cfg = load_config(cfg_path)

    def reload(self):
        with self.lock:
            from ..providers import clear_cache
            clear_cache()
            self.cfg = load_config(self.cfg_path)
            return self.cfg

    def save(self):
        with self.lock:
            save_config(self.cfg, self.cfg["_path"])
        return self.reload()

    def provider_alive(self, name: str, refresh: bool = False) -> bool:
        from ..providers import get_provider
        now = time.time()
        hit = self._alive.get(name)
        if hit and not refresh and now - hit[0] < 30:
            return hit[1]
        try:
            ok = get_provider(self.cfg, name).alive()
        except Exception:
            ok = False
        self._alive[name] = (now, ok)
        return ok

    # -------------------------------------------------------------- state
    def state(self) -> dict:
        cfg = self.cfg
        from ..providers import get_provider
        from ..version import revision
        provs = []
        for name, p in cfg["providers"].items():
            try:
                prov = get_provider(cfg, name)
                key_env = p.get("api_key_env")
                provs.append({"name": name, "type": prov.type, "base_url": prov.base_url,
                              "concurrency": prov.concurrency, "api_key_env": key_env,
                              "key_set": bool(prov.api_key) if key_env else None,
                              "alive": self._alive.get(name, (0, None))[1]})
            except Exception as e:  # noqa: BLE001
                provs.append({"name": name, "error": str(e)})
        models = [{"name": m["name"], "provider": m["provider"], "model_id": m["model_id"],
                   "context_length": m.get("context_length"),
                   "enabled": m.get("enabled", True), "price": m.get("price"),
                   "tags": m.get("tags", []), "extra_body": m.get("extra_body")}
                  for m in cfg["models"]]
        cases = load_cases()
        by_cat: dict[str, dict] = {}
        for c in cases:
            d = by_cat.setdefault(c["category"], {"total": 0, "hard": 0, "smoke": 0})
            d["total"] += 1
            d["hard"] += c.get("difficulty") == "hard"
            d["smoke"] += bool(c.get("smoke"))
        results = cfgmod.results_dir()
        have = sorted(p.stem.removeprefix("transcripts_")
                      for p in results.glob("transcripts_*.jsonl"))
        from ..profiles import list_profiles, load_profile
        profs = []
        for name in list_profiles(cfg):
            try:
                pr = load_profile(name, cfg)
                profs.append({"name": name, "description": pr.get("description", ""),
                              "n_cases": sum(len(v) for v in pr["cases"].values()),
                              "categories": list(pr["cases"].keys())})
            except Exception as e:  # noqa: BLE001
                profs.append({"name": name, "description": f"invalid: {e}", "n_cases": 0})
        return {"config_path": cfg.get("_path"), "results_dir": str(results),
                "profiles": profs,
                "revision": revision(cfg), "providers": provs, "models": models,
                "judge": cfg["judge"], "categories": cfgmod.CATEGORIES,
                "cases": by_cat, "n_cases": len(cases),
                "results_models": have,
                "report_exists": (results / "report.md").exists(),
                "job": JOB.status(),
                "legacy": bool(cfg.get("_upgraded_from_v2"))}


# --------------------------------------------------------------- handler

def _json_bytes(obj) -> bytes:
    return json.dumps(obj, default=str).encode()


class Handler(BaseHTTPRequestHandler):
    app: App = None  # set by serve()
    server_version = "gauntlet-gui/3"

    def log_message(self, fmt, *args):  # quiet access log → debug
        log.debug("http " + fmt, *args)

    # ---------------------------------------------------------- plumbing
    def _send(self, code: int, body: bytes, ctype="application/json; charset=utf-8",
              extra: dict | None = None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code=200, extra=None):
        self._send(code, _json_bytes(obj), extra=extra)

    def _err(self, code: int, msg: str):
        self._json({"error": msg}, code)

    def _cookie_token(self) -> str | None:
        raw = self.headers.get("Cookie", "")
        for part in raw.split(";"):
            k, _, v = part.strip().partition("=")
            if k == "gauntlet_token":
                return v
        return None

    def _authed(self, query: dict) -> bool:
        tok = self.app.token
        if not tok:
            return True
        cand = (self.headers.get("X-Gauntlet-Token") or self._cookie_token()
                or (query.get("token") or [None])[0])
        return bool(cand) and hmac.compare_digest(str(cand), tok)

    def _same_origin(self) -> bool:
        """State-changing requests must come from our own page (CSRF guard)."""
        sfs = self.headers.get("Sec-Fetch-Site")
        if sfs:
            return sfs in ("same-origin", "none")
        origin = self.headers.get("Origin")
        host = self.headers.get("Host", "")
        if origin:
            return urlparse(origin).netloc == host
        # non-browser client with the token header: allow
        return bool(self.headers.get("X-Gauntlet-Token"))

    def _read_json(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        if n > 2_000_000:
            raise ValueError("body too large")
        raw = self.rfile.read(n) if n else b"{}"
        data = json.loads(raw or b"{}")
        if not isinstance(data, dict):
            raise ValueError("expected a JSON object")
        return data

    # ------------------------------------------------------------- routes
    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        path = u.path
        if path == "/" or path == "/index.html":
            if not self._authed(q):
                return self._send(401, b"<h1>401</h1><p>Open the URL printed by <code>gauntlet gui</code> (it carries the access token).</p>",
                                  "text/html; charset=utf-8")
            extra = {}
            if self.app.token and q.get("token"):
                extra["Set-Cookie"] = ("gauntlet_token=" + self.app.token +
                                       "; HttpOnly; SameSite=Strict; Path=/")
            body = (STATIC_DIR / "index.html").read_bytes()
            return self._send(200, body, "text/html; charset=utf-8", extra)
        if path.startswith("/static/"):
            name = os.path.basename(path)  # no traversal
            f = STATIC_DIR / name
            if not f.is_file():
                return self._err(404, "not found")
            ctype = mimetypes.guess_type(name)[0] or "application/octet-stream"
            return self._send(200, f.read_bytes(), ctype + ("; charset=utf-8" if ctype.startswith("text") or "javascript" in ctype else ""))
        if not path.startswith("/api/"):
            return self._err(404, "not found")
        if not self._authed(q):
            return self._err(401, "unauthorized")
        try:
            return self._api_get(path, q)
        except ConfigError as e:
            return self._err(400, str(e))
        except Exception as e:  # noqa: BLE001
            log.error("GET %s failed: %s\n%s", path, e, traceback.format_exc())
            return self._err(500, f"{type(e).__name__}: {e}")

    def do_POST(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        if not self._authed(q):
            return self._err(401, "unauthorized")
        if not self._same_origin():
            return self._err(403, "cross-site request refused")
        try:
            data = self._read_json()
            return self._api_post(u.path, data)
        except ConfigError as e:
            return self._err(400, str(e))
        except (ValueError, KeyError) as e:
            return self._err(400, f"bad request: {e}")
        except RuntimeError as e:
            return self._err(409, str(e))
        except Exception as e:  # noqa: BLE001
            log.error("POST %s failed: %s\n%s", u.path, e, traceback.format_exc())
            return self._err(500, f"{type(e).__name__}: {e}")

    # -------------------------------------------------------------- GET api
    def _api_get(self, path, q):
        app = self.app
        if path == "/api/state":
            return self._json(app.state())
        if path == "/api/providers":
            refresh = q.get("refresh", ["0"])[0] == "1"
            out = {n: app.provider_alive(n, refresh) for n in app.cfg["providers"]}
            return self._json(out)
        if path == "/api/discover":
            from ..providers import get_provider
            name = q.get("provider", [""])[0]
            prov = get_provider(app.cfg, name)
            ids = sorted(prov.list_models(refresh=True))
            return self._json({"provider": name, "models": ids})
        if path == "/api/log":
            since = int(q.get("since", ["0"])[0])
            seq, lines = RING.since(since)
            return self._json({"seq": seq, "lines": lines, "job": JOB.status()})
        if path == "/api/report":
            p = cfgmod.results_dir() / "report.md"
            if not p.exists():
                return self._json({"md": "", "html": "<p class='muted'>No report yet — run a benchmark.</p>"})
            md = p.read_text()
            return self._json({"md": md, "html": md_to_html(md),
                               "mtime": p.stat().st_mtime})
        if path == "/api/report.json":
            p = cfgmod.results_dir() / "report.json"
            return self._send(200, p.read_bytes() if p.exists() else b"{}")
        if path == "/api/pairwise":
            p = cfgmod.results_dir() / "pairwise.md"
            md = p.read_text() if p.exists() else ""
            return self._json({"md": md, "html": md_to_html(md) if md else ""})
        if path == "/api/rows":
            from ..config import load_transcripts
            label = q.get("model", [""])[0]
            case = q.get("case", [None])[0]
            failed = q.get("failed", ["0"])[0] == "1"
            rows = load_transcripts(label)
            if case:
                rows = [r for r in rows if r.get("case_id") == case]
            if failed:
                rows = [r for r in rows if r.get("grade") == "fail"
                        or (r.get("judge") or {}).get("refused")]
            rows.sort(key=lambda r: (r.get("category", ""), r.get("case_id", ""),
                                     r.get("repeat", 0), r.get("turn") or 0))
            slim = []
            for r in rows[:500]:
                slim.append({k: r.get(k) for k in (
                    "case_id", "category", "difficulty", "repeat", "turn", "grade",
                    "grade_detail", "prompt", "response", "reasoning", "tool_calls",
                    "finish_reason", "truncated", "metrics", "judge", "cost_usd",
                    "system", "skipped")})
            return self._json({"model": label, "n": len(rows), "rows": slim})
        if path == "/api/cases":
            cats = q.get("categories", [None])[0]
            cases = load_cases([c for c in cats.split(",")] if cats else None)
            slim = [{"id": c["id"], "category": c["category"],
                     "difficulty": c.get("difficulty", "medium"),
                     "grader": c.get("grader") or c.get("rubric") or
                     ("tool_loop" if c.get("tool_script") else "-"),
                     "smoke": bool(c.get("smoke")),
                     "prompt": (c.get("prompt") or (c.get("turns") or [""])[0]
                                or (c.get("tool_script") or [{}])[0].get("user", ""))[:400]}
                    for c in cases]
            return self._json({"cases": slim})
        return self._err(404, "no such endpoint")

    # ------------------------------------------------------------- POST api
    def _api_post(self, path, data):
        app = self.app
        if path == "/api/stop":
            from ..runner import STOP
            STOP.set()
            JOB.stop_event.set()
            return self._json({"ok": True, "job": JOB.status()})

        if path == "/api/run":
            cfg = app.reload()
            models = data.get("models") or []
            entries = resolve_models(cfg, ",".join(models) if models else "all")
            if not entries:
                raise ValueError("no models selected")
            cats = data.get("categories") or None
            diffs = data.get("difficulty") or None
            smoke = bool(data.get("smoke"))
            judge_override = data.get("judge") or None
            if data.get("profile"):
                from ..profiles import apply_profile, load_profile, profile_judge
                prof = load_profile(data["profile"], cfg)
                cfg, cases = apply_profile(prof, cfg, smoke=smoke)
                if cats:
                    cases = [c for c in cases if c["category"] in set(cats)]
                judge_override = judge_override or profile_judge(prof)
            else:
                cases = load_cases(cats, smoke=smoke, difficulties=diffs)
            if not cases:
                raise ValueError("no cases match that selection")
            fresh = bool(data.get("fresh"))
            samples = data.get("samples")
            then_judge = bool(data.get("then_judge", True))
            then_report = bool(data.get("then_report", True))
            skip_link = bool(data.get("no_link_check"))
            labels = [e["name"] for e in entries]

            def _do():
                from ..runner import run_models
                from ..cli import _LmStudioGuard, _archive_labels
                if not skip_link:
                    from ..preflight import check_link_health
                    check_link_health(cfg, entries)
                if fresh:
                    _archive_labels(labels)
                guard = _LmStudioGuard(cfg, entries)
                try:
                    summary = run_models(cfg, entries, cases, smoke=smoke)
                finally:
                    guard.restore()
                out = {"run": summary}
                try:
                    if then_judge and not JOB.stop_event.is_set():
                        from ..judge import run_judge
                        out["judge"] = run_judge(cfg, labels, samples=(1 if smoke else samples),
                                                 judge_override=judge_override,
                                                 stop=JOB.stop_event)
                finally:
                    # the report is written even when judging fails — the
                    # objective results are already worth reading
                    if then_report:
                        from ..report import generate
                        generate(None)
                        out["report"] = "written"
                return out
            JOB.start("run", _do)
            return self._json({"ok": True, "job": JOB.status(),
                               "models": labels, "n_cases": len(cases)})

        if path == "/api/judge":
            cfg = app.reload()
            models = data.get("models") or []
            entries = resolve_models(cfg, ",".join(models) if models else None)
            labels = [e["name"] for e in entries] if models else [
                p.stem.removeprefix("transcripts_")
                for p in cfgmod.results_dir().glob("transcripts_*.jsonl")]

            def _do():
                from ..judge import run_judge
                from ..report import generate
                r = run_judge(cfg, labels, force=bool(data.get("force")),
                              samples=data.get("samples"),
                              judge_override=data.get("judge") or None,
                              stop=JOB.stop_event)
                generate(None)
                return {"judge": r, "report": "written"}
            JOB.start("judge", _do)
            return self._json({"ok": True, "job": JOB.status()})

        if path == "/api/report":
            from ..report import generate
            app.reload()
            md = generate(data.get("models") or None)
            return self._json({"ok": True, "html": md_to_html(md), "md": md})

        if path == "/api/pairwise":
            cfg = app.reload()
            models = data.get("models") or []
            if len(models) < 2:
                raise ValueError("pairwise needs at least 2 models")
            cats = data.get("categories") or ["rp", "nsfw"]

            def _do():
                from ..pairwise import run_pairwise, render_pairwise_md
                res = run_pairwise(cfg, models, cats, judge_override=data.get("judge") or None,
                                   stop=JOB.stop_event)
                (cfgmod.results_dir() / "pairwise.md").write_text(render_pairwise_md(res))
                return {"pairwise": {"n_pairings": res.get("n_pairings")}}
            JOB.start("pairwise", _do)
            return self._json({"ok": True, "job": JOB.status()})

        if path == "/api/models":
            cfg = app.reload()
            name = str(data["name"]).strip()
            if not name or any(m["name"] == name for m in cfg["models"]):
                raise ValueError("model name missing or already exists")
            if data["provider"] not in cfg["providers"]:
                raise ValueError("unknown provider")
            entry = {"name": name, "provider": data["provider"],
                     "model_id": str(data["model_id"]).strip(),
                     "context_length": int(data.get("context_length") or 32768)}
            if data.get("price_in") not in (None, "") or data.get("price_out") not in (None, ""):
                entry["price"] = {"input": float(data.get("price_in") or 0),
                                  "output": float(data.get("price_out") or 0)}
            if data.get("extra_body"):
                eb = data["extra_body"]
                entry["extra_body"] = json.loads(eb) if isinstance(eb, str) else dict(eb)
            if data.get("tags"):
                entry["tags"] = [t.strip() for t in str(data["tags"]).split(",") if t.strip()]
            cfg["models"].append(entry)
            app.save()
            return self._json({"ok": True})

        if path == "/api/models/update":
            cfg = app.reload()
            name = data["name"]
            idx = next((i for i, m in enumerate(cfg["models"]) if m["name"] == name), None)
            if idx is None:
                raise ValueError("unknown model")
            if data.get("delete"):
                cfg["models"].pop(idx)
            else:
                m = cfg["models"][idx]
                if "enabled" in data:
                    m["enabled"] = bool(data["enabled"])
                if data.get("context_length"):
                    m["context_length"] = int(data["context_length"])
                if "price" in data:
                    if data["price"]:
                        m["price"] = {"input": float(data["price"].get("input", 0)),
                                      "output": float(data["price"].get("output", 0))}
                    else:
                        m.pop("price", None)
                if "extra_body" in data:
                    eb = data["extra_body"]
                    if eb:
                        m["extra_body"] = json.loads(eb) if isinstance(eb, str) else dict(eb)
                    else:
                        m.pop("extra_body", None)
            app.save()
            return self._json({"ok": True})

        if path == "/api/providers":
            cfg = app.reload()
            name = str(data["name"]).strip()
            if not name:
                raise ValueError("provider name required")
            p = {"type": data.get("type") or "openai",
                 "base_url": str(data["base_url"]).strip().rstrip("/")}
            if data.get("api_key_env"):
                p["api_key_env"] = str(data["api_key_env"]).strip()
            elif data.get("api_key"):
                key = str(data["api_key"])
                if p["type"] == "openai" and len(key) > 12:
                    raise ValueError("remote API keys must be given as an environment "
                                     "variable name (api_key_env), not a literal key")
                p["api_key"] = key
            if data.get("concurrency"):
                p["concurrency"] = int(data["concurrency"])
            if data.get("headers"):
                h = data["headers"]
                p["headers"] = json.loads(h) if isinstance(h, str) else dict(h)
            if data.get("delete"):
                if any(m["provider"] == name for m in cfg["models"]):
                    raise ValueError("provider still used by models")
                cfg["providers"].pop(name, None)
            else:
                cfg["providers"][name] = p
            app.save()
            return self._json({"ok": True})

        if path == "/api/providers/test":
            from ..providers import get_provider
            app.reload()
            name = data["name"]
            prov = get_provider(app.cfg, name)
            alive = prov.alive()
            self.app._alive[name] = (time.time(), alive)
            ids = sorted(prov.list_models(refresh=True))[:50] if alive else []
            probe = None
            if alive and data.get("model_id"):
                try:
                    r = prov.chat(str(data["model_id"]), [{"role": "user", "content": "Say OK."}],
                                  max_tokens=64, temperature=0.0)
                    probe = {"ok": True, "text": r.response_text[:80],
                             "served_model": r.served_model,
                             "ttft_ms": round((r.ttft_s or 0) * 1000)}
                except Exception as e:  # noqa: BLE001
                    probe = {"ok": False, "error": str(e)[:300]}
            return self._json({"alive": alive, "models": ids, "n_models": len(ids),
                               "probe": probe})

        if path == "/api/judge-candidates":
            cfg = app.reload()
            cands = data.get("candidates")
            if not isinstance(cands, list):
                raise ValueError("candidates must be a list")
            clean = []
            for c in cands:
                if not c.get("provider") or not c.get("model_id"):
                    continue
                if c["provider"] not in cfg["providers"]:
                    raise ValueError(f"unknown provider {c['provider']}")
                d = {"provider": c["provider"], "model_id": c["model_id"]}
                if c.get("context_length"):
                    d["context_length"] = int(c["context_length"])
                if c.get("thinking"):
                    d["thinking"] = True
                clean.append(d)
            cfg["judge"]["candidates"] = clean
            if data.get("samples"):
                cfg["judge"]["samples"] = int(data["samples"])
            app.save()
            return self._json({"ok": True})

        return self._err(404, "no such endpoint")


# ------------------------------------------------------------------ serve

def serve(cfg_path: str | None, host: str = "127.0.0.1", port: int = 8777,
          token: str | None = None, open_browser: bool = True) -> int:
    loopback = host in ("127.0.0.1", "localhost", "::1")
    if not loopback and not token:
        token = secrets.token_urlsafe(24)
        print(f"non-loopback bind: generated access token (pass it as ?token=…)")
    app = App(cfg_path, token, require_token=bool(token))
    Handler.app = app
    logging.getLogger().addHandler(RING)
    httpd = ThreadingHTTPServer((host, port), Handler)
    httpd.daemon_threads = True
    url = f"http://{host if host != '0.0.0.0' else '127.0.0.1'}:{port}/"
    if token:
        url += f"?token={token}"
    print(f"Gauntlet GUI: {url}")
    print(f"config: {app.cfg.get('_path')}   results: {cfgmod.results_dir()}")
    if open_browser and loopback:
        try:
            webbrowser.open(url)
        except Exception:
            pass
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nbye")
    finally:
        httpd.server_close()
    return 0
