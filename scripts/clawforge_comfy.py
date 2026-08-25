#!/usr/bin/env python3
"""clawforge_comfy.py <free_vram|stop|start|restart|status> [--url URL]

Drive ClawForge's ComfyUI (a FOREIGN VRAM holder a StudioForge lease cannot
evict) before/after a CrucibleForge phase. `free_vram` unloads its models but
leaves it running — ClawForge's own recommendation "before running another
GPU job". Exit 0 on success, 2 on a tool error, 3 when the MCP endpoint is
unreachable (not fatal for a benchmark: the lease path reports the real
shortfall). Stdlib only; MCP streamable-http, no auth (ClawForge default)."""
import json
import re
import sys
import urllib.error
import urllib.request

DEFAULT_URL = "http://localhost:8700/mcp"


def _post(url, body, sid=None):
    hdrs = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
    if sid:
        hdrs["Mcp-Session-Id"] = sid
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers=hdrs, method="POST")
    with urllib.request.urlopen(req, timeout=180) as r:
        raw = r.read().decode("utf-8", "replace")
        sid = r.headers.get("Mcp-Session-Id") or sid
    m = re.search(r"data: (\{.*\})", raw)
    return (json.loads(m.group(1)) if m else (json.loads(raw) if raw.strip() else {})), sid


def main(argv):
    if not argv or argv[0] not in ("free_vram", "stop", "start", "restart", "status"):
        print(__doc__, file=sys.stderr)
        return 1
    action = argv[0]
    url = DEFAULT_URL
    if "--url" in argv:
        url = argv[argv.index("--url") + 1]
    try:
        _, sid = _post(url, {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                             "params": {"protocolVersion": "2025-03-26", "capabilities": {},
                                        "clientInfo": {"name": "crucibleforge", "version": "3.2"}}})
        tool = "comfy_status" if action == "status" else "comfy_control"
        args = {} if action == "status" else {"action": action}
        res, _ = _post(url, {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                             "params": {"name": tool, "arguments": args}}, sid)
    except (urllib.error.URLError, OSError, ValueError) as e:
        print(f"clawforge unreachable: {e}", file=sys.stderr)
        return 3
    result = res.get("result") or {}
    text = " ".join(b.get("text", "") for b in result.get("content", []) if isinstance(b, dict))
    print(text[:600])
    return 2 if result.get("isError") or res.get("error") else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
