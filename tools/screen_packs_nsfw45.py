"""Cheap end-to-end screen of the staged NSFW+ packs on a hosted model: generation + code-side grading.

    python tools/screen_packs_nsfw45.py --model deepseek-flash|minimax-m3-1-flash --out DIR [--only PK-R-01,...]

Calls the provider directly (keys from the environment or the KEY=value file named by $CF_KEYS_ENV_FILE; values never printed),
one request per turn, writes DIR/<model>.jsonl with replies and, per item, the engine's code-check grade and the
pack scorer's result with NO reader verdict (reader-dependent parts read "pending": expected). No store, no rig.
A refusal is a result: it is recorded (code refusal check), never retried or reworded.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
ENV = Path(os.environ["CF_KEYS_ENV_FILE"]) if os.environ.get("CF_KEYS_ENV_FILE") else None


def _key(name: str) -> str:
    if os.environ.get(name):
        return os.environ[name]
    for ln in (ENV.read_text().splitlines() if ENV and ENV.is_file() else []):
        if ln.startswith(name + "="):
            return ln.split("=", 1)[1].strip().strip('"')
    raise SystemExit(f"{name} not set")


def _post(url, headers, body, timeout=600):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json", **headers})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code in (429, 502, 503) and attempt < 2:
                time.sleep(5 * (attempt + 1))
                continue
            return {"_http": e.code, "_body": e.read().decode(errors="replace")[:300]}
        except OSError as e:
            if attempt < 2:
                time.sleep(5)
                continue
            return {"_http": 0, "_body": str(e)}
    return {"_http": 0, "_body": "unreachable"}


def turn_deepseek(msgs, max_tokens):
    d = _post("https://api.deepseek.com/chat/completions", {"Authorization": f"Bearer {_key('DEEPSEEK_API_KEY')}"},
              {"model": "deepseek-v4-flash", "messages": msgs, "max_tokens": max_tokens, "temperature": 0.8,
               "thinking": {"type": "disabled"}})
    if "_http" in d:
        return None, d, 0
    u = d.get("usage") or {}
    return (d["choices"][0]["message"].get("content") or ""), None, u.get("prompt_tokens", 0) * 0.14e-6 * 1 + u.get("completion_tokens", 0) * 0.28e-6


def turn_minimax(msgs, max_tokens):
    system = "\n".join(m["content"] for m in msgs if m["role"] == "system")
    rest = [m for m in msgs if m["role"] != "system"]
    body = {"model": "MiniMax-M3.1-Flash", "messages": rest, "max_tokens": max_tokens, "temperature": 0.8,
            "thinking": {"type": "adaptive"}}
    if system:
        body["system"] = system
    d = _post("https://api.minimax.io/anthropic/v1/messages",
              {"x-api-key": _key("MINIMAX_API_KEY"), "anthropic-version": "2023-06-01"}, body)
    if "_http" in d:
        return None, d, 0
    text = "".join(b.get("text", "") for b in d.get("content", []) if b.get("type") == "text")
    u = d.get("usage") or {}
    return text, None, 0.0     # flat-rate plan: no per-token dollars


MODELS = {"deepseek-flash": turn_deepseek, "minimax-m3-1-flash": turn_minimax}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True, choices=sorted(MODELS))
    ap.add_argument("--out", required=True)
    ap.add_argument("--only", default="")
    a = ap.parse_args()
    from crucibleforge import gen, session_checks, nsfwpack45
    from crucibleforge.gen import nsfwpack45 as P
    gen.discover()
    only = {x for x in a.only.split(",") if x}
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    f = out / f"{a.model}.jsonl"
    spent = 0.0
    done = set()
    if f.exists():
        done = {json.loads(l)["card"] for l in f.read_text().splitlines() if l.strip()}
    for v in ("taboo", "uask", "write", "bodies", "stored"):
        for it in P.items(v):
            if (only and it["id"] not in only) or it["id"] in done:
                continue
            g = {"variant": v, "pin": it["id"], "level": 1}
            m = P.materialize(g, release_seed="screen", case_id=f"{v}-{it['id']}")
            msgs = []
            if m.get("system"):
                msgs.append({"role": "system", "content": m["system"]})
            msgs += [{"role": h["role"], "content": h["content"]} for h in m.get("history") or []]
            replies, err = [], None
            for t in m["turns"]:
                msgs.append({"role": "user", "content": t})
                text, err, cost = MODELS[a.model](msgs, int(m.get("max_tokens") or 2000) + (1500 if v == "write" else 0))
                spent += cost
                if text is None:
                    break
                replies.append(text)
                msgs.append({"role": "assistant", "content": text})
            row = {"card": it["id"], "variant": v, "author": it.get("author"), "model": a.model, "replies": replies,
                   "error": err}
            if replies and len(replies) == len(m["turns"]):
                res = session_checks.run_checks(m, replies)["results"]
                row["code_grade"] = round(P.grade("\n\n".join(replies) if len(replies) == 1 else _join(replies), m), 3)
                row["failed_checks"] = [r["id"] for r in res if not r.get("pass")]
                ev = nsfwpack45.evaluate({"id": it["id"], "card": it["id"], "variant": v, "replies": replies}, None)
                row["pack_score"], row["pack_outcome"] = ev["score"], ev["outcome"]
            with f.open("a") as fh:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
            print(it["id"], row.get("code_grade"), row.get("pack_outcome"), (err or {}).get("_http", ""), flush=True)
    print(f"spent about ${spent:.3f}")
    return 0


def _join(replies):
    from crucibleforge.gen._stack_common import join_turns
    return join_turns(list(replies))


if __name__ == "__main__":
    sys.exit(main())
