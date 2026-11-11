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
import re

from . import studioforge
from .api import TransportError, WrongModelError
from .providers import Provider, get_provider

log = logging.getLogger(__name__)


def _pick_studioforge_canary(cfg: dict, prov: Provider, selected_ids: list[str]) -> str | None:
    """Smallest selected StudioForge model (or the configured override)."""
    override = (cfg.get("link_check") or {}).get("canary_model_id")
    if override:
        return override
    if getattr(prov, "lease", False):
        # Lease mode (2026-09-08): a chat probe against a NOT-resident model
        # makes the rig JIT-load it onto whatever cards are free right now —
        # a 1-slot "degenerate placement" the lease then has to unload and
        # re-plan (run 1), or a 507 while a foreign client sits on the cards
        # (run 2). Probe a model that is already resident instead; with none
        # resident, skip the data-channel probe — the lease load that follows
        # answers with structured errors of its own.
        try:
            resident = [r["model_id"] for r in studioforge.residents(prov.base_url, prov.api_key,
                                                                    prov.mgmt_headers())
                        if r.get("model_id") and r.get("state") == "ready"]
        except studioforge.StudioForgeError:
            resident = []
        if resident:
            return resident[0]
        log.info("link check: lease mode and nothing resident — skipping the chat probe "
                 "(it would JIT-load the benched model off-lease)")
        return None
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


def _probe_sampler(cfg: dict, entry: dict | None) -> dict:
    """The sampler the run itself will send, so the probe never asks for one
    the model refuses: under the "as deployed" regime (3.8.0) that is none at
    all (or the entry's pinned ``sampler:``) — kimi-k3 rejects any temperature
    but 1 and failed the old fixed temperature=0 probe (2026-10-25)."""
    if ((cfg or {}).get("defaults") or {}).get("regime") == "deployed":
        from .runner import resolve_sampler
        s, _ = resolve_sampler(cfg, entry or {})
        return {"temperature": s.get("temperature"), "top_p": s.get("top_p")}
    return {"temperature": 0.0}


def _probe(prov: Provider, model_id: str, extra_body: dict | None = None,
           sampler: dict | None = None) -> None:
    prov.chat(model_id, [{"role": "user", "content": "Hi"}],
              max_tokens=4, seed=42, extra_body=extra_body or {},
              **(sampler if sampler is not None else {"temperature": 0.0}))


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
            probes = [(canary, {}, _probe_sampler(cfg, None))]
        else:
            probes = [(e["model_id"], dict(e.get("extra_body") or {}), _probe_sampler(cfg, e))
                      for e in ents]

        for model_id, extra, sampler in probes:
            try:
                _probe(prov, model_id, extra, sampler)
            except (TransportError, WrongModelError) as e1:
                log.warning("link check: %s/%s probe failed (%s); retrying once",
                            pname, model_id, str(e1)[:120])
                try:
                    _probe(prov, model_id, extra, sampler)
                except (TransportError, WrongModelError) as e2:
                    raise SystemExit(
                        f"provider {pname}: data channel down or model {model_id!r} "
                        f"unusable — {str(e2)[:200]}. Fix the server / model id / "
                        "API key, then re-run (or --no-link-check).")
        log.info("link check passed: %s reachable, data channel OK", pname)


# ------------------------------------------------ hosted credit preflight
# 2026-11-08: eight API models sat behind "conformance FAIL ... HTTP 401" and
# a spent plan answered 402 only after five retries per call.  One cheap
# request per hosted provider says "out of credit" up front, by name.

_BILLING_RE = re.compile(
    r"insufficient|balance|spending.?limit|usage limit|out of credit|billing"
    r"|payment required|exceeded your (?:current )?quota", re.IGNORECASE)

#: provider host -> the account-balance URL (a GET that costs nothing)
_BALANCE_URLS = {"api.deepseek.com": "https://api.deepseek.com/user/balance"}


_ROW_STATUS_RE = re.compile(r"\bHTTP (\d{3})\b")


def billing_error_row(r: dict) -> bool:
    """True for a stored benchmark row that failed because the hosted
    provider would not bill the request (4.1 release blocker F1): an errored
    transport row whose message says HTTP 402 or 429, or carries a billing
    wording (credit balance too low, insufficient balance, suspended account,
    spending limit, usage limit ...).  Such a row says nothing about the
    model: it never counts toward the attempt limit, and the pack it belongs
    to reads "pending", not 0.  A timeout, a 5xx or a malformed reply is not
    billing."""
    if r.get("grade") == "fail" and not r.get("error_kind"):
        # an agentic episode whose provider call was refused for billing was
        # graded as the model's own failure by older engines (Fable 5.2 LAG1-3,
        # HTTP 400 "credit balance is too low"): recognise the stored shape
        end = str((r.get("agent") or {}).get("end") or "")
        return (end.startswith("request rejected mid-episode")
                and bool(_BILLING_RE.search(end) or re.search(r"suspended", end, re.IGNORECASE)))
    if r.get("grade") != "error" or r.get("error_kind") != "transport":
        return False
    msg = str(r.get("error") or r.get("grade_detail") or "")
    m = _ROW_STATUS_RE.search(msg)
    if m and m.group(1) in ("402", "429"):
        return True
    return bool(_BILLING_RE.search(msg) or re.search(r"suspended", msg, re.IGNORECASE))


def _is_billing_answer(status: int, body: str) -> bool:
    if status == 402:
        return True
    return status in (400, 401, 403, 429) and bool(
        _BILLING_RE.search(body or "") or re.search(r"suspended", body or "", re.IGNORECASE))


def billing_rejection(e: Exception) -> bool:
    """True when a RequestRejected is the provider refusing to bill (credit,
    quota, spend limit, suspended account), whatever its HTTP status: that is
    infra, never the model's answer."""
    status = getattr(e, "status", None)
    return isinstance(status, int) and _is_billing_answer(status, str(getattr(e, "body", "") or e))


def hosted_credit_problem(prov: Provider, model_id: str) -> str | None:
    """Why ``prov`` cannot be billed right now, or None (fine, or unknown).

    One request, no retries.  DeepSeek: ``GET /user/balance`` ->
    ``is_available``.  Any other hosted provider: a 1-token completion; only
    a billing answer (402, or an insufficient-balance / spending-limit body)
    counts -- a bad model id or sampler is the link check's business.  The
    key is never logged.  Any failure of the check itself returns None."""
    from urllib.parse import urlparse
    from . import api
    from .conformance import _is_hosted
    if prov.type in ("studioforge", "lmstudio") or not _is_hosted(prov):
        return None
    host = (urlparse(prov.base_url or "").hostname or "").lower()
    headers = {"Authorization": f"Bearer {prov.api_key}"} if prov.api_key else {}
    try:
        with api.httpx.Client(timeout=20.0) as http:
            url = _BALANCE_URLS.get(host)
            if url:
                resp = http.get(url, headers=headers)
                if resp.status_code != 200:
                    return None
                if resp.json().get("is_available") is False:
                    return f"{prov.name}: account balance is empty (DeepSeek is_available=false) - top up, then re-run"
                return None
            if prov.type == "anthropic":
                # native Messages API: x-api-key + version/workspace headers
                resp = http.post(f"{prov.base_url.rstrip('/')}/messages",
                                 headers=prov._headers(),
                                 json={"model": model_id, "max_tokens": 1,
                                       "messages": [{"role": "user", "content": "Hi"}]})
            else:
                resp = http.post(f"{prov.base_url.rstrip('/')}/chat/completions",
                                 headers={**headers, "Content-Type": "application/json"},
                                 json={"model": model_id, "max_tokens": 1,
                                       "messages": [{"role": "user", "content": "Hi"}]})
            body = resp.text if isinstance(resp.text, str) else ""
            if _is_billing_answer(resp.status_code, body):
                return (f"{prov.name}: out of credit or over its spending limit "
                        f"(HTTP {resp.status_code}) - fix the plan, then re-run")
    except Exception as e:  # noqa: BLE001 - a flaky probe must not block a run
        log.debug("credit check for %s skipped: %s", prov.name, type(e).__name__)
    return None


def hosted_credit_problems(cfg: dict, entries: list[dict]) -> dict[str, str]:
    """{label: reason} for every entry whose hosted provider is out of
    credit; one request per provider."""
    verdict: dict[str, str | None] = {}
    bad: dict[str, str] = {}
    for e in entries:
        pname = e["provider"]
        if pname not in verdict:
            try:
                prov = get_provider(cfg, pname)
                verdict[pname] = hosted_credit_problem(prov, e["model_id"])
            except Exception:  # noqa: BLE001
                verdict[pname] = None
            if verdict[pname]:
                log.error("credit check: %s", verdict[pname])
        if verdict[pname]:
            bad[e["name"]] = verdict[pname]
    return bad


# ------------------------------------------------ fail-fast runnability
# 2026-09-23: three of the last board's failures took minutes (a lease, a
# load attempt) to say something knowable in one GET: the model id is not
# served at all (a safetensors/NVFP4 HF repo registered on StudioForge,
# which only serves GGUF files), or the 122B judge cannot fit on the rig.

_GGUF_HINT = re.compile(r"gguf|guff|(?:^|[-_.])(?:i?q\d|bf16|f16|f32)", re.IGNORECASE)


def _is_gguf_id(model_id: str) -> bool:
    """A GGUF repo name, or a llama.cpp quant tag (Q4_K_M, IQ4_XS, Q8_0,
    BF16…) in the file stem. An HF safetensors id (…-NVFP4, …-AWQ) has
    neither."""
    return bool(_GGUF_HINT.search(model_id))


def unrunnable_reason(cfg: dict, entry: dict) -> str | None:
    """Why ``entry`` cannot be benched at all, or None. Seconds, no load."""
    prov = get_provider(cfg, entry["provider"])
    mid = entry["model_id"]
    if prov.type == "studioforge":
        records = {m.get("id"): m for m in studioforge.list_models_full(prov.base_url, prov.api_key)}
        if not records:
            return None  # listing unavailable — the run's own load answers
        rec = records.get(mid)
        if rec is None:
            if not _is_gguf_id(mid):
                return (f"{mid} is not served by provider {prov.name}: it is not a GGUF id and "
                        f"StudioForge only serves GGUF files — pick a GGUF quant the rig has")
            return f"{mid} is not served by provider {prov.name} — pick a model the rig serves"
        sf = rec.get("studioforge") or {}
        if sf.get("kind") and sf.get("kind") != "chat":
            return f"{mid} is a {sf.get('kind')} model on {prov.name}, not a chat model"
        if sf.get("arch_supported") is False:
            return f"{mid}: {prov.name} reports its architecture as unsupported by the engine"
        return None
    if prov.type == "lmstudio":
        return None  # the runner's explicit load-and-verify is the check
    ids = prov.list_models()
    if ids and mid not in ids:
        return f"{mid} is not served by provider {prov.name}"
    return None


def judge_unrunnable_reason(cfg: dict, judge: dict | str | None) -> str | None:
    """Why the benchmark's judge cannot run, or None. For a StudioForge
    judge: it must be served, and the rig's own planner must say it fits on
    every card (bench-first leases the whole rig for it)."""
    if not isinstance(judge, dict) or judge.get("provider") not in (cfg.get("providers") or {}):
        return None
    prov = get_provider(cfg, judge["provider"])
    mid = judge["model_id"]
    if prov.type != "studioforge":
        return None
    records = {m.get("id") for m in studioforge.list_models_full(prov.base_url, prov.api_key)}
    if records and mid not in records:
        return f"judge {mid} is not served by {prov.name}"
    try:
        devices = list(prov.lease_devices or studioforge.gpu_indices(
            prov.base_url, prov.api_key, prov.mgmt_headers()))
    except studioforge.StudioForgeError:
        return None
    fits = studioforge.placement_fits(prov.base_url, prov.api_key, prov.mgmt_headers(),
                                      mid, devices)
    if fits is False:
        return (f"judge {mid.rsplit('/', 1)[-1]} does not fit on {prov.name} cards {devices} "
                f"even empty (planner) — the judge phase would fail after generation")
    return None
