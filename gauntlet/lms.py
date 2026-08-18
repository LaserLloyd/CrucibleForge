"""LM Studio model management via the `lms` CLI.

LM Link facts this module encodes (verified 2026-08-12):
- `lms ls --json` lists every model with `deviceIdentifier`: null = a local
  copy on this box, non-null = a remote LM Link peer.
  A model with BOTH copies runs locally.
- `lms ps --json` shows loaded instances; `lms load <key> -y` loads on the
  owning device. Only ONE model may be loaded at a time across both devices
  (house rule), so we always unload-all before loading.
- JIT loading is ON: any API request for an unloaded model auto-loads it
  with a 10-minute TTL. The bench never relies on JIT — explicit load with
  verification, so load time is measured and the served model is known.
"""
from __future__ import annotations

import json
import logging
import subprocess
import time
from pathlib import Path

log = logging.getLogger(__name__)

def _find_lms() -> str:
    """`lms` on PATH, else LM Studio's default install location."""
    import shutil
    return (shutil.which("lms")
            or str(Path.home() / ".lmstudio" / "bin" / "lms"))


LMS_BIN = _find_lms()

LOAD_TIMEOUT_S = 900        # big MoEs / 27B on CPU take a while
VERIFY_DEADLINE_S = 120
POLL_INTERVAL_S = 2.0
CMD_TIMEOUT_S = 120


class LmsError(RuntimeError):
    pass


def _run(*args: str, timeout: int = CMD_TIMEOUT_S, check: bool = True) -> subprocess.CompletedProcess:
    log.debug("lms %s", " ".join(args))
    try:
        proc = subprocess.run([LMS_BIN, *args], capture_output=True, text=True,
                              timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise LmsError(f"`lms {' '.join(args)}` timed out after {timeout}s") from exc
    if check and proc.returncode != 0:
        raise LmsError(f"`lms {' '.join(args)}` rc={proc.returncode}: "
                       f"{proc.stderr.strip() or proc.stdout.strip()}")
    return proc


def list_models() -> list[dict]:
    """All downloadable-model entries from `lms ls --json` (LLMs only)."""
    proc = _run("ls", "--json")
    data = json.loads(proc.stdout)
    return [m for m in data if m.get("type") == "llm"]


def link_peers() -> list[dict]:
    """Peers from `lms link status --json`. Returns [] on ANY error/timeout —
    a link-status hiccup must never crash the bench (the caller treats [] as
    'link down' and aborts with a clear message)."""
    try:
        proc = _run("link", "status", "--json", check=False)
    except LmsError:
        return []
    if proc.returncode != 0:
        log.warning("lms link status rc=%d: %s", proc.returncode, proc.stderr[:200])
        return []
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return []
    if not isinstance(data, dict):
        return []
    peers = data.get("peers") or []
    return peers if isinstance(peers, list) else []


def device_map() -> dict[str, str]:
    """model key -> 'local' | 'remote' (LM Link peer). A key present on both devices is
    'local' (the local copy wins at load time)."""
    mapping: dict[str, str] = {}
    for m in list_models():
        key = m["modelKey"]
        dev = "local" if m.get("deviceIdentifier") is None else "remote"
        if key in mapping:
            mapping[key] = "local" if "local" in (mapping[key], dev) else "remote"
        else:
            mapping[key] = dev
    return mapping


def loaded_models() -> list[dict]:
    """Currently loaded instances: [{identifier, device, status}, ...]."""
    proc = _run("ps", "--json", check=False)
    if proc.returncode != 0:
        log.warning("lms ps failed rc=%d: %s", proc.returncode, proc.stderr[:200])
        return []
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return []
    out = []
    for m in data:
        out.append({
            "identifier": m.get("identifier") or m.get("modelKey"),
            "model_key": m.get("modelKey"),
            "device": m.get("deviceIdentifier"),
        })
    return out


def is_loaded(model_id: str) -> bool:
    return any(m["identifier"] == model_id or m["model_key"] == model_id
               for m in loaded_models())


def unload_all() -> None:
    proc = _run("unload", "--all", check=False)
    combined = (proc.stderr + proc.stdout).lower()
    if proc.returncode != 0 and "no model" not in combined:
        log.warning("lms unload --all rc=%d: %s", proc.returncode, proc.stderr[:300])


def load_model(model_id: str, context_length: int | None = None) -> float:
    """Load a model, verify it is serving, return wall-clock load seconds.

    Kept deliberately strict: a model that fails to load raises, and the
    caller decides whether to skip it (runner) or abort (judge)."""
    t0 = time.perf_counter()
    if is_loaded(model_id):
        log.info("%s already loaded", model_id)
        return 0.0
    args = ["load", model_id, "-y"]
    if context_length:
        args += ["--context-length", str(context_length)]
    proc = _run(*args, timeout=LOAD_TIMEOUT_S, check=False)
    if proc.returncode != 0:
        raise LmsError(f"load {model_id} failed rc={proc.returncode}: "
                       f"{proc.stderr.strip() or proc.stdout.strip()}")
    deadline = time.monotonic() + VERIFY_DEADLINE_S
    while time.monotonic() < deadline:
        if is_loaded(model_id):
            load_s = time.perf_counter() - t0
            log.info("%s loaded in %.1fs", model_id, load_s)
            return load_s
        time.sleep(POLL_INTERVAL_S)
    raise LmsError(f"{model_id} not in `lms ps` within {VERIFY_DEADLINE_S}s of load")


def switch_model(model_id: str, context_length: int | None = None) -> float:
    """Unload everything then load the requested model. Returns load seconds."""
    unload_all()
    return load_model(model_id, context_length)


def snapshot() -> list[str]:
    """Identifiers currently loaded — capture before the bench, restore after."""
    return [m["identifier"] for m in loaded_models() if m.get("identifier")]


def restore(identifiers: list[str]) -> None:
    """Best-effort: put the pre-bench model back so shared users recover."""
    unload_all()
    for ident in identifiers:
        try:
            load_model(ident)
        except LmsError as e:
            log.warning("restore of %s failed: %s", ident, e)
