"""Quarantine holds and the error record (R1), the circuit breaker (R4)
and the daily digest (R5).

A model problem writes one YAML record and stays ``open`` until a person
sets ``status`` to ``fixed``, ``escalated`` or ``rejected``. Nothing here
expires. Records are not board rows: they live in
``$CRUCIBLEFORGE_QUARANTINE`` or ``~/.local/share/crucibleforge/quarantine``.

Three consecutive model records with the same stage and class halt the
queue (a system fault). An auth failure alerts at once and does not
pretend the fixer can repair it. The digest is at most ten lines.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

import yaml

STATUSES = ("open", "fixed", "escalated", "rejected")
# early-stop signal -> failure class (design section 5). A blank or a
# cut-off answer is a broken run, not a score.
_STOP_CLASS = {
    "blank": "degenerate_output",
    "token_limit": "degenerate_output",
    "misrouted": "template_trap",
    "degenerate": "degenerate_output",
}


def store_dir() -> Path:
    env = os.environ.get("CRUCIBLEFORGE_QUARANTINE")
    p = Path(env).expanduser() if env else (
        Path.home() / ".local" / "share" / "crucibleforge" / "quarantine")
    p.mkdir(parents=True, exist_ok=True)
    return p


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d")


def _load(path: Path) -> dict:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    data["_path"] = str(path)
    return data


def records() -> list[dict]:
    out = []
    for path in sorted(store_dir().glob("q-*.yaml")):
        try:
            out.append(_load(path))
        except yaml.YAMLError:
            continue
    return out


def _next_id(day: str) -> str:
    nums = []
    for path in store_dir().glob(f"q-{day}-*.yaml"):
        tail = path.stem.rsplit("-", 1)[-1]
        if tail.isdigit():
            nums.append(int(tail))
    return f"q-{day}-{((max(nums) + 1) if nums else 1):03d}"


def _write(rec: dict) -> dict:
    rid = rec["id"]
    path = store_dir() / f"{rid}.yaml"
    body = {k: v for k, v in rec.items() if not k.startswith("_")}
    path.write_text(yaml.safe_dump(body, sort_keys=False, allow_unicode=True),
                    encoding="utf-8")
    rec["_path"] = str(path)
    return rec


def _alert(kind: str, rid: str, detail: str) -> None:
    """One line when an auth failure or a halt is new. No secrets."""
    line = json.dumps({"ts": _now(), "kind": kind, "id": rid, "detail": detail[:300]},
                      ensure_ascii=False)
    with (store_dir() / "alerts.jsonl").open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def hold(*, model: str, stage: str, klass: str, reason: str,
         scope: str = "model", evidence: dict | None = None,
         settings: dict | None = None, tripwire: dict | None = None) -> dict:
    """Open a record, or return the open one for the same model, class and
    stage. A repeat does not advance the circuit breaker."""
    for rec in records():
        if (rec.get("status") == "open" and rec.get("scope", "model") == scope
                and rec.get("model") == model and rec.get("class") == klass
                and rec.get("stage") == stage):
            rec["_new"] = False
            return rec
    rec = {
        "id": _next_id(_today()),
        "model": model,
        "stage": stage,
        "class": klass,
        "scope": scope,
        "evidence": evidence or {},
        "settings": settings or {},
        "tripwire": tripwire or {},
        "attempts": [],
        "reason": reason[:500],
        "status": "open",
        "opened": _now(),
    }
    rec = _write(rec)
    rec["_new"] = True
    if scope == "model":
        _circuit(rec)
    return rec


def hold_settings(label: str, reason: str, model_id: str | None = None) -> dict:
    """D1: no sampler rule and no settings_checked record."""
    return hold(model=label, stage="preflight", klass="needs_settings",
                reason=reason, settings={"source": "default", "model_id": model_id or ""})


def hold_early_stop(label: str, trip: dict, transcript: str | None = None) -> dict:
    sig = str((trip or {}).get("signal") or "")
    return hold(model=label, stage="run", klass=_STOP_CLASS.get(sig, "degenerate_output"),
                reason=f"early stop: {sig}" if sig else "early stop",
                evidence={"outputs": transcript or ""},
                tripwire={k: (trip or {}).get(k) for k in
                          ("signal", "value", "threshold", "after_answers")})


def note_auth(provider: str, detail: str) -> dict:
    """Auth is a system fault the fixer cannot repair. Alert once per open record."""
    rec = hold(model=provider or "provider", stage="preflight", klass="auth",
               reason=detail, scope="system")
    if rec.get("_new"):
        _alert("auth", rec["id"], detail)
    return rec


def set_status(rid: str, status: str) -> dict:
    """Close a record. ``open`` is not a closing status. This is the only
    way a halt stops blocking the queue."""
    if status not in ("fixed", "escalated", "rejected"):
        raise ValueError(f"status must be fixed, escalated or rejected (got {status})")
    path = store_dir() / f"{rid}.yaml"
    if not path.is_file():
        raise FileNotFoundError(rid)
    rec = _load(path)
    rec["status"] = status
    rec["closed"] = _now()
    return _write(rec)


def queue_halted() -> dict | None:
    """The open halt, if the circuit breaker has stopped the queue."""
    for rec in records():
        if rec.get("status") == "open" and rec.get("class") == "circuit":
            return rec
    return None


def _circuit(new: dict) -> dict | None:
    """Three consecutive OPEN model quarantines at one stage and class halt the
    queue. A closed record (fixed, escalated, rejected) never re-arms the halt."""
    model_recs = [r for r in records()
                  if r.get("scope", "model") == "model" and r.get("status") == "open"]
    if len(model_recs) < 3:
        return None
    last = model_recs[-3:]
    if len({r.get("stage") for r in last}) != 1 or len({r.get("class") for r in last}) != 1:
        return None
    if len({r.get("model") for r in last}) < 3:
        return None
    stage, klass = last[-1].get("stage"), last[-1].get("class")
    for rec in records():
        if (rec.get("status") == "open" and rec.get("class") == "circuit"
                and rec.get("stage") == stage and (rec.get("settings") or {}).get("repeated") == klass):
            return rec
    ids = [r.get("id") for r in last]
    rec = hold(model="queue", stage=stage or "run", klass="circuit", scope="system",
               reason=f"three consecutive {klass} at {stage}: {', '.join(ids)}",
               settings={"repeated": klass, "records": ids})
    if rec.get("_new"):
        _alert("halt", rec["id"], rec["reason"])
    return rec


def digest_lines(today: str | None = None) -> list[str]:
    """At most ten lines: published, open by class, escalated, halts, auth, the owner."""
    day = today or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    rows = records()
    open_rows = [r for r in rows if r.get("status") == "open"]
    by_class: dict[str, int] = {}
    for r in open_rows:
        if r.get("class") == "circuit" or r.get("scope") == "system":
            continue
        by_class[str(r.get("class") or "?")] = by_class.get(str(r.get("class") or "?"), 0) + 1
    escalated = [r.get("id") for r in rows if r.get("status") == "escalated"]
    halts = [r.get("id") for r in open_rows if r.get("class") == "circuit"]
    auths = [str(r.get("model")) for r in open_rows if r.get("class") == "auth"]
    fixed: dict[str, int] = {}
    for r in rows:
        opened = str(r.get("opened") or "")
        if r.get("status") == "fixed" and opened.startswith(day):
            fixed[str(r.get("class"))] = fixed.get(str(r.get("class")), 0) + 1
    classes = " ".join(f"{k}={v}" for k, v in sorted(by_class.items())) or "none"
    lines = [
        f"digest {day}",
        "published: off (human tap)",
        f"open: {classes}",
        "fixed: " + (" ".join(f"{k}={v}" for k, v in sorted(fixed.items())) or "none"),
        "escalated: " + (", ".join(escalated) or "none"),
        "halts: " + (", ".join(halts) or "none"),
        "auth: " + (", ".join(auths) or "none"),
        "owner: auth, unrecovered halt, publish",
    ]
    return lines[:10]
