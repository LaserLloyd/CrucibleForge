"""Pre-flight health check: prove each provider's DATA channel works.

Why this exists (the bug it prevents): a multi-hour run once burned itself out
because the remote server's CONTROL channel reported healthy (``GET /models``
→ 200) while its data channel silently dropped every stream (``peer closed
connection without sending complete message body``). So the check MUST probe
with a real streamed completion, not just poll the model list.

- ``studioforge``: probe the smallest selected model (cheapest JIT load).
- ``openai`` (hosted APIs / llama.cpp / Ollama / vLLM ...): probe each selected
  model id with a 4-token completion — this also catches a wrong model id or a
  missing API key up front, before the transcripts start filling with errors.
- ``lmstudio``: skipped — the explicit load-and-verify step in the runner is
  the check (a probe would force a model load here).
"""
from __future__ import annotations

import logging

from . import studioforge
from .api import TransportError, WrongModelError
from .providers import Provider, get_provider

log = logging.getLogger(__name__)


def _pick_studioforge_canary(cfg: dict, prov: Provider, selected_ids: list[str]) -> str | None:
    """Smallest selected StudioForge model (or the configured override)."""
    override = (cfg.get("link_check") or {}).get("canary_model_id")
    if override:
        return override
    records = {m["id"]: m for m in studioforge.list_models_full(prov.base_url, prov.api_key)}
    candidates: list[tuple[int, str]] = []
    for sid in selected_ids:
        rec = records.get(sid)
        if not rec:
            continue
        sf = rec.get("studioforge") or {}
        if sf.get("kind") == "embedding":
            continue
        size = sf.get("size_bytes")
        if size is None:
            continue
        candidates.append((int(size), sid))
    if not candidates:
        return selected_ids[0] if selected_ids else None
    candidates.sort(key=lambda t: t[0])
    return candidates[0][1]


def _probe(prov: Provider, model_id: str, extra_body: dict | None = None) -> None:
    prov.chat(model_id, [{"role": "user", "content": "Hi"}],
              max_tokens=4, temperature=0.0, seed=42, extra_body=extra_body or {})


def check_link_health(cfg: dict, entries: list[dict]) -> None:
    """Abort FAST if any selected provider is unreachable or drops its stream.
    Raises SystemExit with an actionable message."""
    by_provider: dict[str, list[dict]] = {}
    for e in entries:
        by_provider.setdefault(e["provider"], []).append(e)

    for pname, ents in by_provider.items():
        prov = get_provider(cfg, pname)
        if prov.type == "lmstudio":
            continue
        if not prov.alive():
            raise SystemExit(
                f"provider {pname} unreachable at {prov.base_url} — is the server "
                "running / is the API key set? Aborting.")
        if prov.type == "studioforge":
            canary = _pick_studioforge_canary(cfg, prov, [e["model_id"] for e in ents])
            if not canary:
                log.warning("link check: no %s canary model found; skipping probe", pname)
                continue
            probes = [(canary, {})]
        else:
            probes = [(e["model_id"], dict(e.get("extra_body") or {})) for e in ents]

        for model_id, extra in probes:
            try:
                _probe(prov, model_id, extra)
            except (TransportError, WrongModelError) as e1:
                log.warning("link check: %s/%s probe failed (%s); retrying once",
                            pname, model_id, str(e1)[:120])
                try:
                    _probe(prov, model_id, extra)
                except (TransportError, WrongModelError) as e2:
                    raise SystemExit(
                        f"provider {pname}: data channel down or model {model_id!r} "
                        f"unusable — {str(e2)[:200]}. Fix the server / model id / "
                        "API key, then re-run (or --no-link-check).")
        log.info("link check passed: %s reachable, data channel OK", pname)
