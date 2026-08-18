"""Import providers + models from an OpenClaw gateway config (openclaw.json).

OpenClaw's ``models.providers`` are OpenAI-compatible endpoints with
``${ENV_VAR}`` API-key references and per-model cost metadata — exactly the
shape Gauntlet's registry wants. This maps:

    providers.<name>.baseUrl            -> providers.<name>.base_url
    providers.<name>.apiKey "${X}"      -> providers.<name>.api_key_env: X
    providers.<name>.models[].id        -> models[].model_id (name = <prov>/<id>)
    providers.<name>.models[].cost      -> models[].price {input, output} (USD/1M)
    providers.<name>.models[].contextWindow -> context_length

Only ``api: openai-completions`` providers are imported. Literal API keys are
never copied (the value stays in OpenClaw's env; you export the same variable
for Gauntlet). Nothing is written unless the caller saves the config.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import urlparse

_ENV_REF = re.compile(r"^\$\{([A-Z0-9_]+)\}$")
_LOCAL_HOSTS = ("127.0.0.1", "localhost", "0.0.0.0", "::1")


def _is_local(url: str) -> bool:
    host = urlparse(url).hostname or ""
    if host in _LOCAL_HOSTS:
        return True
    # RFC1918 + tailscale CGNAT — "local" for our purposes
    return bool(re.match(r"^(10\.|192\.168\.|172\.(1[6-9]|2\d|3[01])\.|100\.(6[4-9]|[7-9]\d|1[01]\d|12[0-7])\.)", host))


def import_openclaw(cfg: dict, path: Path, include_local: bool = False) -> dict:
    data = json.loads(Path(path).read_text())
    providers = ((data.get("models") or {}).get("providers")) or {}
    added = {"providers": [], "models": []}
    existing_models = {(m["provider"], m["model_id"]) for m in cfg["models"]}
    for pname, p in providers.items():
        if p.get("api", "openai-completions") != "openai-completions":
            continue
        base = (p.get("baseUrl") or "").rstrip("/")
        if not base:
            continue
        if _is_local(base) and not include_local:
            continue
        gname = f"oc-{pname}" if pname in cfg["providers"] and \
            cfg["providers"][pname].get("base_url", "").rstrip("/") != base else pname
        if gname not in cfg["providers"]:
            prov: dict = {"type": "openai", "base_url": base, "concurrency": 4}
            key = p.get("apiKey")
            m = _ENV_REF.match(str(key or ""))
            if m:
                prov["api_key_env"] = m.group(1)
            elif key and str(key).lower() in ("local", "lm-studio", "ollama", ""):
                prov["api_key"] = str(key)
            elif key:
                # a literal secret in openclaw.json — do NOT copy it; point at
                # a conventional env var name and tell the user
                prov["api_key_env"] = f"{pname.upper()}_API_KEY"
                prov["_note"] = "openclaw.json held a literal key; export it as this env var"
            if p.get("timeoutSeconds"):
                prov["timeout"] = int(p["timeoutSeconds"])
            cfg["providers"][gname] = prov
            added["providers"].append(gname)
        for m in p.get("models") or []:
            mid = m.get("id")
            if not mid or (gname, mid) in existing_models:
                continue
            if m.get("input") and "text" not in m.get("input", ["text"]):
                continue
            entry = {"name": f"{gname}/{mid}".replace("/", "-", 1) if "/" not in mid else f"{gname}-{mid.rsplit('/', 1)[-1]}",
                     "provider": gname, "model_id": mid,
                     "context_length": min(int(m.get("contextWindow") or 32768), 131072)}
            cost = m.get("cost") or {}
            if cost.get("input") or cost.get("output"):
                entry["price"] = {"input": float(cost.get("input", 0)),
                                  "output": float(cost.get("output", 0))}
            if m.get("reasoning"):
                entry.setdefault("tags", []).append("reasoning")
            cfg["models"].append(entry)
            existing_models.add((gname, mid))
            added["models"].append(entry["name"])
    return added
