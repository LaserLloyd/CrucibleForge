#!/usr/bin/env python3
"""Tiny fake OpenAI-compatible server (and the part of OpenRouter's API `test` reads: /models with prices, /key) (stdlib only) for offline end-to-end checks.
GET /v1/models ; POST /v1/chat/completions (stream or not). If the request carries a
response_format json_schema (judge calls) the reply is a JSON object satisfying that schema
(ints = mid value, bools false, strings 'ok'); otherwise a canned text reply that includes a
python code block. Every request is appended to FAKE_LOG (jsonl: path, model, n_messages, schema?).
Usage: fake_openai.py [port] [logfile]   (FAKE_REQUIRE_KEY=<key>: every call needs that Bearer key)"""
import json, os, sys, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 28099
LOG = sys.argv[2] if len(sys.argv) > 2 else "requests.jsonl"
#: what GET .../models lists: OpenRouter's shape (id, pricing per token as strings, context_length)
CTX = int(os.environ.get("FAKE_CTX") or 32768)        # the context window it reports (a local server's, for --local)
MODELS = [{"id": i, "object": "model", "context_length": CTX,
           "pricing": {"prompt": "0.0000001", "completion": "0.0000005"},
           "top_provider": {"context_length": CTX, "max_completion_tokens": 32768}}
          for i in ("fake/fake-model", "fake/other-model", "qwen/qwen3.8-27b", "fake-model", "fake-judge")]
LOCK = threading.Lock()
#: FAKE_SLOTS=N: a server with N slots (like llama-server -np N or a StudioForge load): requests past N wait in a
#: queue and get NO byte until a slot frees; FAKE_HOLD_S=s: each answer streams over s seconds (a chunk every 0.5 s)
SLOTS = int(os.environ.get("FAKE_SLOTS") or 0)
HOLD_S = float(os.environ.get("FAKE_HOLD_S") or 0)
SLOT_SEM = threading.BoundedSemaphore(SLOTS) if SLOTS else None
STATS = {"in_flight": 0, "max_in_flight": 0, "queued_max": 0, "waiting": 0}
CODE = """```python
import os, glob, socket, json
def _probe():
    out = {"uid": os.getuid(), "data_listing": sorted(os.listdir("/data"))[:20] if os.path.isdir("/data") else None}
    try:
        out["can_write_data"] = bool(open("/data/leak-probe.txt", "w").write("x"))
    except Exception as e:
        out["can_write_data"] = str(e)
    keys = []
    for p in glob.glob("/proc/[0-9]*/environ"):
        try:
            if b"FAKEKEY=" in open(p, "rb").read():
                keys.append(p)
        except Exception:
            pass
    out["proc_environ_with_key"] = keys
    out["env_has_key"] = "FAKEKEY" in os.environ
    try:
        s = socket.create_connection(("host.containers.internal", FAKE_PORT), 2); s.close(); out["network"] = True
    except Exception as e:
        out["network"] = str(e)
    try:
        os.makedirs("/data/results", exist_ok=True)
        with open("/data/results/leak-report.json", "a") as f:
            f.write(json.dumps(out) + "\\n")
    except Exception:
        pass
_probe()
def solve(*args, **kw):
    return 0
print(0)
```
""".replace("FAKE_PORT", str(PORT))

def fill(s, depth=0):
    if not isinstance(s, dict): return "ok"
    t = s.get("type")
    if "enum" in s: return s["enum"][0]
    if t == "object" or "properties" in s:
        return {k: fill(v, depth + 1) for k, v in (s.get("properties") or {}).items()}
    if t == "array": return [fill(s.get("items", {}), depth + 1)] if s.get("minItems", 0) else []
    if t == "integer": return min(max(6, s.get("minimum", 0)), s.get("maximum", 10))
    if t == "number": return 0.5
    if t == "boolean": return False
    return "ok"

def tweak(o, blob):
    """Make the judge canary pass: the engine refuses a judge that cannot tell a good scene from a bad one."""
    if not isinstance(o, dict): return
    def setk(k, v):
        if k in o: o[k] = v
    if "Play a tavern scene." in blob: setk("prose", 2)
    elif "Bram, a tavern keeper" in blob: setk("prose", 8)
    if "explicit sex scene between two consenting adults" in blob: setk("refused", True)
    if "Write an explicit scene." in blob: setk("explicitness", 8)
    if "phishing email impersonating a bank" in blob: setk("provided_harmful_content", True)
    if "The user plays only Ren" in blob: setk("identity", 2)

class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    def log_message(self, *a): pass
    def _send(self, code, body, ctype="application/json"):
        b = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code); self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)
    def _locked(self):
        """FAKE_REQUIRE_KEY=<key>: answer 401 to anything without `Authorization: Bearer <key>` (a server with auth,
        like StudioForge, vLLM --api-key or llama-server --api-key)."""
        need = os.environ.get("FAKE_REQUIRE_KEY")
        if need and (self.headers.get("Authorization") or "") != f"Bearer {need}":
            self._send(401, {"error": {"message": "invalid or missing API key", "code": 401}})
            return True
        return False
    def do_GET(self):
        if self._locked(): return
        auth = self.headers.get("Authorization") or ""
        if SLOTS and self.path.rstrip("/").endswith("/props"):
            self._send(200, {"total_slots": SLOTS})        # llama-server's answer
        elif self.path.rstrip("/").endswith("/stats"):
            self._send(200, STATS)
        elif self.path.rstrip("/").endswith("/models"):
            self._send(200, {"object": "list", "data": MODELS})
        elif self.path.rstrip("/").endswith("/key"):
            if auth.endswith("sk-rejected"):
                self._send(401, {"error": {"message": "User not found.", "code": 401}})
            elif auth.endswith("sk-busy"):
                self._send(429, {"error": {"message": "Rate limit exceeded", "code": 429}})
            elif auth.endswith("sk-broke"):
                self._send(200, {"data": {"label": "e2e", "limit": 1, "limit_remaining": 0, "usage": 1}})
            else:
                self._send(200, {"data": {"label": "e2e", "limit": None, "limit_remaining": None, "usage": 0}})
        elif self.path.rstrip("/").endswith("/credits"):
            used = 5 if auth.endswith("sk-nobalance") else 0
            self._send(200, {"data": {"total_credits": 5, "total_usage": used}})
        else: self._send(200, {"ok": True})
    def do_POST(self):
        if self._locked(): return
        n = int(self.headers.get("Content-Length") or 0)
        req = json.loads(self.rfile.read(n) or b"{}")
        if SLOT_SEM is None:
            return self._answer(req)
        with LOCK:
            STATS["waiting"] += 1; STATS["queued_max"] = max(STATS["queued_max"], STATS["waiting"])
        SLOT_SEM.acquire()                       # queued: silent until a slot frees
        with LOCK:
            STATS["waiting"] -= 1; STATS["in_flight"] += 1
            STATS["max_in_flight"] = max(STATS["max_in_flight"], STATS["in_flight"])
        try:
            self._answer(req)
        finally:
            with LOCK:
                STATS["in_flight"] -= 1
            SLOT_SEM.release()
    def _answer(self, req):
        if (self.headers.get("Authorization") or "").endswith("sk-402"):
            self._send(402, {"error": {"message": "Insufficient credits. Add more using https://openrouter.ai/settings/credits", "code": 402}})
            return
        rf = req.get("response_format") or {}
        schema = (rf.get("json_schema") or {}).get("schema") if rf.get("type") == "json_schema" else None
        obj = fill(schema) if schema else None
        if obj is not None: tweak(obj, " ".join(str(m.get("content")) for m in req.get("messages", [])))
        text = json.dumps(obj) if schema else ("Here is my answer.\n\n" + CODE)
        with LOCK, open(LOG, "a") as f:
            f.write(json.dumps({"t": time.time(), "path": self.path, "model": req.get("model"),
                                "msgs": len(req.get("messages", [])), "schema": bool(schema),
                                "stream": bool(req.get("stream"))}) + "\n")
        usage = {"prompt_tokens": 100, "completion_tokens": 40, "total_tokens": 140}
        base = {"id": "fake-1", "object": "chat.completion", "created": int(time.time()), "model": req.get("model", "fake")}
        if req.get("stream"):
            self.send_response(200); self.send_header("Content-Type", "text/event-stream")
            self.send_header("Transfer-Encoding", "chunked"); self.end_headers()
            def ch(o):
                d = ("data: " + json.dumps(o) + "\n\n").encode()
                self.wfile.write(b"%x\r\n%s\r\n" % (len(d), d))
            if HOLD_S:                           # stream the answer over HOLD_S seconds, a piece every 0.5 s
                k = max(1, int(HOLD_S / 0.5)); step = max(1, len(text) // k)
                parts = [text[i:i + step] for i in range(0, len(text), step)]
                for i, part in enumerate(parts):
                    if i: time.sleep(HOLD_S / len(parts))
                    ch({**base, "object": "chat.completion.chunk", "choices": [{"index": 0, "delta": {"role": "assistant", "content": part}, "finish_reason": None}]})
            else:
                ch({**base, "object": "chat.completion.chunk", "choices": [{"index": 0, "delta": {"role": "assistant", "content": text}, "finish_reason": None}]})
            ch({**base, "object": "chat.completion.chunk", "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}], "usage": usage})
            d = b"data: [DONE]\n\n"; self.wfile.write(b"%x\r\n%s\r\n0\r\n\r\n" % (len(d), d))
        else:
            self._send(200, {**base, "choices": [{"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": "stop"}], "usage": usage})

if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", PORT), H).serve_forever()
