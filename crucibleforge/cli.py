"""crucibleforge CLI — status / run / judge / report / pairwise / all / gui / config
/ import-openclaw / models / cases.

There is ONE benchmark (profiles/bench.yaml). Benchmark a model with ONE command:

    crucibleforge all --models <label> --fresh --yes   # generate + 122B judge + board

    crucibleforge status
    crucibleforge report                               # rebuild the board (Chat / Coding)
    crucibleforge gui                                  # http://127.0.0.1:8777

Shared-server etiquette (LM Studio): the bench unloads whatever the local
server is serving. It snapshots the loaded model at start and restores it at
the end, but other clients will still see their model swapped out mid-run —
prefer running overnight or with other consumers stopped.
"""
from __future__ import annotations

import argparse
import copy
import json
import logging
import os
import re
import secrets
import shlex
import shutil
import socket
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from . import stopflag
from .config import (CATEGORIES, ConfigError, EXAMPLE_CONFIG_PATH,
                     USER_CONFIG_PATH, load_cases, load_config, resolve_models,
                     results_dir)

log = logging.getLogger("crucibleforge")

# Operator-local defaults: a git-ignored crucibleforge.local.env next to
# models.yaml may set CRUCIBLEFORGE_* variables (KEY=VALUE lines, # comments)
# for every invocation on that machine — a cron unit, an agent's exec, a
# terminal — without exporting them in each caller. The real environment
# always wins; keys outside CRUCIBLEFORGE_* are ignored (this is not a
# secrets file: CRUCIBLEFORGE_ENV_FILE names one, it does not hold one).
LOCAL_ENV_PATH = Path(__file__).resolve().parents[1] / "crucibleforge.local.env"


def _load_local_env(path: Path = LOCAL_ENV_PATH) -> dict[str, str]:
    """setdefault each CRUCIBLEFORGE_* line of ``path`` into os.environ;
    returns what was applied. A missing/unreadable file applies nothing.

    CRUCIBLEFORGE_NO_PRIVATE=1 (clean-clone mode, set by the test-suite)
    applies nothing either: the file is part of the operator's local layer,
    and a test run once inherited its run-report root and wrote into a live
    delivery directory."""
    applied: dict[str, str] = {}
    if os.environ.get("CRUCIBLEFORGE_NO_PRIVATE") == "1":
        return applied
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return applied
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key, val = key.strip(), val.strip().strip("'\"")
        if key.startswith("CRUCIBLEFORGE_") and val and key not in os.environ:
            os.environ[key] = val
            applied[key] = val
    return applied


_load_local_env()

# The optional runs/<id>/{report.md,meta.json} run-report contract (schema
# v1) — deliberately NOT under this project's own `results/`, which is a
# different, tool-owned directory (see `results_dir()`). Absolute and
# per-machine on purpose: every producer on a box writes into one directory so
# one standing scanner can deliver all of them, independent of cwd/--results.
# OPT-IN: unset (the default) writes no run report at all; set
# $CRUCIBLEFORGE_V2_RUNS_ROOT to the directory your scanner watches.
_V2_RUNS_ENV = os.environ.get("CRUCIBLEFORGE_V2_RUNS_ROOT")
V2_RUNS_ROOT: Path | None = Path(_V2_RUNS_ENV).expanduser() if _V2_RUNS_ENV else None
V2_PRODUCER = "crucibleforge"
V2_KIND = "cron-worker"


def setup_logging(log_path: Path | None = None):
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if log_path:
        try:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            handlers.append(logging.FileHandler(log_path))
        except OSError:
            pass
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s",
                        handlers=handlers, force=True)
    logging.getLogger("httpx").setLevel(logging.WARNING)


# ------------------------------------------------------------------ helpers

def _parse_list(arg: str | None) -> list[str] | None:
    if not arg:
        return None
    return [s.strip() for s in arg.split(",") if s.strip()]


_PIN_ENV_REF = re.compile(r"^\$\{([A-Za-z_][A-Za-z0-9_]*)\}$")


def _pin_env_name(raw_headers: dict | None) -> str | None:
    """The ``${ENV_VAR}`` name behind a provider's RAW (pre-expansion)
    X-MCP-Pin header, so status can print ``pin: $NAME (set/NOT SET)`` in the
    same style as an API key's ``key: $NAME`` — providers.Provider only keeps
    the EXPANDED value, which has already lost the variable's name (WP-BENCH
    FIX-5). Returns None when the header isn't set up as an env reference at
    all (no X-MCP-Pin header, or a literal value)."""
    raw = next((v for k, v in (raw_headers or {}).items() if k.lower() == "x-mcp-pin"), None)
    if isinstance(raw, str):
        m = _PIN_ENV_REF.match(raw.strip())
        if m:
            return m.group(1)
    return None


def _archive_labels(labels: list[str]) -> None:
    """Move existing transcript+meta for these labels into a timestamped
    archive so a --fresh run starts clean instead of accumulating."""
    from datetime import datetime
    from .config import transcript_path
    dest = results_dir() / f"archive-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    moved = 0
    for label in labels:
        for p in (transcript_path(label), results_dir() / f"meta_{label}.json"):
            if p.exists():
                dest.mkdir(parents=True, exist_ok=True)
                p.rename(dest / p.name)
                moved += 1
    if moved:
        log.info("archived %d prior result files to %s", moved, dest)


# --------------------------------------------------------- WP-BENCH FIX-6
# runs/<id>/{report.md,meta.json} write contract (the run-report schema v1 schema
# v1). Tracked for `run` and `all` only (see `main()`) — a whole CLI process
# is the "unit of work"; `cmd_all` calling `cmd_run` internally must not
# trip this twice, which is why it lives at the top-level dispatch and not
# inside cmd_run itself.

def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _mint_run_id() -> str:
    """Self-minted id per the run-report schema v1: w-<UTC stamp>-<4 hex>."""
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"w-{ts}-{secrets.token_hex(2)}"


_RUN_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,80}$")


def _resolve_run_id(args) -> str:
    """RUN_DIR:/RUN_ID: equivalent for a CLI caller: --run-id, else
    $CRUCIBLEFORGE_RUN_ID, else self-mint. A worker dispatched with a brief
    that names a run id never needs to invent one; an ad-hoc human/cron
    invocation gets one anyway, so the write contract is unconditional.

    Validated against the run-report schema v1's own rule ("id — must equal the
    directory name"): an unchecked id containing e.g. `/` or `..` writes
    outside V2_RUNS_ROOT with a directory name that does not match the `id`
    field it records — and puts the report where the run-report scanner's
    `runs/*/meta.json` glob can never see it, so the run reports success and
    is never delivered (WP-BENCH review M1). An invalid candidate is logged
    and replaced with a self-minted id, never used as given."""
    candidate = getattr(args, "run_id", None) or os.environ.get("CRUCIBLEFORGE_RUN_ID")
    if candidate:
        if _RUN_ID_RE.match(candidate):
            return candidate
        log.warning("--run-id/CRUCIBLEFORGE_RUN_ID %r is not a bare id matching "
                    "[A-Za-z0-9._-]{1,80} (no '/') — self-minting one instead", candidate)
    return _mint_run_id()


def _v2_run_dir(run_id: str) -> Path:
    if V2_RUNS_ROOT is None:
        raise RuntimeError("no run-report directory: $CRUCIBLEFORGE_V2_RUNS_ROOT is not set")
    return V2_RUNS_ROOT / run_id


def _atomic_write_json(path: Path, obj: dict) -> None:
    """tmp + fsync(file) + os.replace + fsync(dir) — meta.json is the commit
    marker the fleet's run-report scanner keys on; a half-written file
    must never be visible at the final name. The directory fsync (WP-BENCH
    review M2, matching the run-report schema v1's own write order and the
    reference implementation in the fleet's run-report scanner's
    write_meta_atomic) makes the RENAME itself durable across a power loss,
    not just the tmp file's bytes — best-effort, never fails the write."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp.{os.getpid()}")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2)
        f.write("\n")
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
    _fsync_dir(path.parent)


def _fsync_dir(directory: Path) -> None:
    """fsync a directory so a just-completed rename survives a power loss.

    A no-op on Windows, by design: NTFS has no directory file descriptor
    (``os.open`` on a directory raises ``PermissionError`` there) and the rename
    is made durable by the filesystem journal instead. Elsewhere it is
    best-effort -- it never fails the write.
    """
    if sys.platform == "win32":
        return
    try:
        dir_fd = os.open(str(directory), os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    except OSError:
        pass  # best-effort durability nicety; never fail the write over this


def _v2_meta(run_id: str, *, status: str, created: str, finished: str | None, title: str,
            requester_session: str | None, thread_id: str | None,
            task_run_id: str | None, extra: dict | None = None) -> dict:
    """The 15-field schema v1 object, field order matching
    the run-report schema v1's own example verbatim. producer/kind are constants —
    crucibleforge is always the producer of its own runs, always dispatched
    as a cron-style worker (see V2_KIND)."""
    meta = {
        "schema": 1, "id": run_id, "producer": V2_PRODUCER, "kind": V2_KIND,
        "title": title[:80], "requester_session": requester_session,
        "thread_id": thread_id, "task_run_id": task_run_id, "status": status,
        "created": created, "finished": finished,
        "delivered": False, "delivered_at": None, "delivered_to": None, "delivery_mode": None,
    }
    # Fields a dispatching worker wrote into the run dir before we started
    # (e.g. child_session) ride along untouched — never our 15, never
    # delivery state.
    for k, v in (extra or {}).items():
        if k not in meta:
            meta[k] = v
    return meta


_V2_INHERIT = ("requester_session", "thread_id", "task_run_id")


def _v2_existing(run_id: str) -> dict:
    """The meta.json a worker may have pre-written into OUR run dir when it
    spawned us with a chosen --run-id (a dispatching worker does: producer worker, kind
    spawn, requester_session = the asking session). 2026-09-08: we used to
    overwrite it wholesale, so the run's routing was lost and the standing
    scanner delivered the report to the bot's daily thread instead of the
    thread that asked for the bench. Returns {} when there is none."""
    if V2_RUNS_ROOT is None:
        return {}
    try:
        p = _v2_run_dir(run_id) / "meta.json"
        if p.exists():
            d = json.loads(p.read_text(encoding="utf-8"))
            return d if isinstance(d, dict) else {}
    except (OSError, ValueError) as e:
        log.warning("V2 run-report: could not read existing meta.json for %s: %s", run_id, e)
    return {}


#: A "running" report with no pid (written before pids were recorded) is
#: taken for dead once it is this old.
V2_ORPHAN_AGE_S = 24 * 3600


def _pid_is_crucibleforge(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    try:
        cmd = Path(f"/proc/{pid}/cmdline").read_bytes()
    except OSError:
        return True                      # no /proc: trust the signal check
    return b"crucibleforge" in cmd


def _v2_sweep_orphans(own_run_id: str) -> list[str]:
    """Mark our own run reports that are still "running" but whose process is
    gone (a SIGKILL, a power cut: only SIGTERM is trapped) as failed, so they
    stop looking live forever (3.8 review, 2026-10-25: two such reports sat
    "running" for a day). Only reports this host wrote; never raises.
    Returns the run ids marked."""
    marked: list[str] = []
    if V2_RUNS_ROOT is None:
        return marked
    host = socket.gethostname()
    now = time.time()
    try:
        metas = sorted(Path(V2_RUNS_ROOT).glob("*/meta.json"))
    except OSError:
        return marked
    for path in metas:
        try:
            m = json.loads(path.read_text(encoding="utf-8"))
            if (not isinstance(m, dict) or m.get("producer") != V2_PRODUCER
                    or m.get("status") != "running" or m.get("id") == own_run_id):
                continue
            pid = m.get("pid")
            if pid is not None:
                if m.get("host") not in (None, host) or _pid_is_crucibleforge(int(pid)):
                    continue
            elif now - path.stat().st_mtime < V2_ORPHAN_AGE_S:
                continue
            m.update(status="failed", finished=_utcnow_iso(),
                     error="the run's process is gone (killed without a chance to write its "
                           "report); its finished rows are kept and the next run picks up the rest")
            _atomic_write_json(path, m)
            marked.append(str(m.get("id")))
            log.warning("V2 run-report: %s was left running by a dead process — marked failed",
                        m.get("id"))
        except (OSError, ValueError, TypeError):
            continue
    return marked


def _v2_start(args) -> dict:
    """FIRST act for a V2-tracked command, before any benchmark work at all:
    mint/resolve the run id and write meta.json with status "running". This
    is what lets the fleet-wide standing scanner flag a worker that dies
    mid-run as stale instead of it leaving no trace (the run-report schema v1
    "running-first" convention, matching the worker conventions).

    Never raises — a filesystem problem here is logged and the run proceeds;
    the V2 write contract must never be why a benchmark did not run."""
    run_id = _resolve_run_id(args)
    requester = getattr(args, "requester", None) or os.environ.get("CRUCIBLEFORGE_REQUESTER")
    deliver_to = getattr(args, "deliver_to", None) or os.environ.get("CRUCIBLEFORGE_DELIVER_TO")
    task_run_id = (getattr(args, "task_run_id", None)
                  or os.environ.get("CRUCIBLEFORGE_TASK_RUN_ID"))
    existing = _v2_existing(run_id)
    requester = requester or existing.get("requester_session")
    deliver_to = deliver_to or existing.get("thread_id")
    task_run_id = task_run_id or existing.get("task_run_id")
    extra = {k: v for k, v in existing.items()
             if k not in _V2_INHERIT and k not in ("schema", "id", "producer", "kind", "title",
                                                   "status", "created", "finished", "delivered",
                                                   "delivered_at", "delivered_to", "delivery_mode")}
    if existing:
        log.info("V2 run-report: inheriting routing from the pre-existing meta.json "
                 "(requester=%s thread=%s task_run=%s extra=%s)",
                 requester, deliver_to, task_run_id, sorted(extra))
    title = f"CrucibleForge {args.cmd}: {args.models}"
    created = _utcnow_iso()
    # who is running it, so the next run can tell a live report from one a
    # SIGKILL left "running" forever (_v2_sweep_orphans)
    extra = {**extra, "pid": os.getpid(), "host": socket.gethostname()}
    state = {"run_id": run_id, "requester_session": requester, "thread_id": deliver_to,
             "task_run_id": task_run_id, "title": title, "created": created, "extra": extra}
    if V2_RUNS_ROOT is None:
        log.info("run report off ($CRUCIBLEFORGE_V2_RUNS_ROOT not set)")
        return state
    _v2_sweep_orphans(run_id)
    try:
        meta = _v2_meta(run_id, status="running", created=created, finished=None, title=title,
                        requester_session=requester, thread_id=deliver_to,
                        task_run_id=task_run_id, extra=extra)
        _atomic_write_json(_v2_run_dir(run_id) / "meta.json", meta)
        log.info("V2 run-report: %s/meta.json written (status=running)", _v2_run_dir(run_id))
    except OSError as e:
        log.error("V2 run-report FIRST write failed for %s: %s — proceeding anyway", run_id, e)
    return state


def _next_hint(error: str) -> str:
    """What to do about a failed model, chosen by the kind of error."""
    e = (error or "").lower()
    if "not served" in e or "not a gguf" in e or "not a chat model" in e:
        return ("pick a GGUF chat model the provider actually serves "
                "(`crucibleforge models discover <provider>`), fix models.yaml, re-run")
    if "unable to generate parser" in e or "template" in e:
        return ("llama-server cannot build a parser for this model's chat template — try "
                "another quant/upload of the model, not a re-run")
    if ("exited with code" in e or "model_load_failed" in e or "load-recommended" in e
            or "failed to load" in e or "architecture" in e):
        return ("the engine could not load this model (not transient) — check the GGUF / "
                "`sfctl logs` before re-running")
    if ("lease" in e or "priority" in e or "held by another" in e or "409" in e
            or "503" in e or "busy" in e):
        return "the rig was busy (lease / priority hold) — retry later"
    if "judge" in e:
        return "judge phase failed — `crucibleforge judge --models <label>` once the rig is free"
    if "too slow" in e:
        return "the model is below the viability floor on this host — not worth a re-run"
    if "consecutive transport failures" in e or "unreachable" in e:
        return "the server dropped out mid-run — check the provider, then re-run"
    return "see results/crucibleforge.log for the phase that failed"


def _summary_bits(card: dict) -> list[str]:
    """The Result line's scores: Overall + each public category on the points
    board (3.9); the pre-points keys (Chat/Coding) otherwise."""
    if card.get("score_defs"):
        def _f(v):
            return f"{v:.1f}" if isinstance(v, (int, float)) else "-"
        return [f"Overall {_f(card.get('total'))}"] + [
            f"{d['label']} {_f(card.get(d['key']))}"
            for d in card["score_defs"] if d.get("public", True)]
    return [f"Chat {card['chat']:.1f}" if card.get("chat") is not None else "Chat -",
            f"Coding {card['coding']:.1f}" if card.get("coding") is not None else "Coding -"]


def _v2_report_body(cmd: str, args, cfg: dict, rc: int, state: dict) -> str:
    """runs/<id>/report.md: the 5-heading contract (Result / Evidence /
    Files / Failed / Next) + the scorecard. the run-report scanner posts only the
    ``## Result`` section when the report is long, so Result carries one
    self-contained line per model: ``label: Overall 17.2 · Coding/Work 9.4 · Chat 7.8 · NSFW 8.3 · 41 min``
    or ``label: FAILED — <the full error>``."""
    from .report import board_stats, run_minutes, scorecard_section, generate
    from .version import revision
    models_arg = getattr(args, "models", None)
    labels: list[str] = []
    try:
        entries = resolve_models(cfg, models_arg)
        labels = [e["name"] for e in entries]
    except Exception:  # noqa: BLE001 — a bad --models must not blank the report
        # `cfg` is the registry snapshot loaded at process dispatch. A command
        # may register its OWN labels mid-run — bench-all appends new models to
        # models.yaml and then benches them — so a freshly-added name is absent
        # here even though the board (which reloads the config itself and reads
        # saved results) already lists it. Re-read the registry from disk before
        # giving up, or the run's own report reads "(no models resolved)".
        try:
            from .config import set_results_dir
            here = results_dir()
            try:
                entries = resolve_models(load_config(cfg.get("_path")), models_arg)
            finally:
                set_results_dir(here)
            labels = [e["name"] for e in entries]
        except Exception as e2:  # noqa: BLE001
            log.warning("V2 report: could not resolve --models %r: %s", models_arg, e2)
    stats: dict = {}
    scorecard = None
    render_err = None
    if labels:
        try:
            _, stats, _ = board_stats(",".join(labels), cfg)
            # write=False (WP-BENCH review I1): never rewrite the SHARED
            # board as a side effect of building this run's report
            scorecard = scorecard_section(generate(",".join(labels), write=False))
        except Exception as e:  # noqa: BLE001 — never let a report bug eat the real rc
            render_err = str(e)
            log.warning("V2 report: scorecard failed: %s", e)
    try:
        rev = revision(cfg)
    except Exception:
        rev = None
    result_lines, failed, warnings = [], [], []
    for label in labels:
        s = stats.get(label) or {}
        meta = s.get("meta") or {}
        if not meta:
            try:
                meta = json.loads((results_dir() / f"meta_{label}.json").read_text(encoding="utf-8"))
            except (OSError, ValueError):
                meta = {}
        if meta.get("failed"):
            err = " ".join(str(meta.get("error") or "unknown error").split())
            result_lines.append(f"{label}: FAILED — {err}")
            failed.append((label, err))
            continue
        card = s.get("scorecard") or {}
        mins = run_minutes(meta)
        if cmd == "judge":
            # a re-judge: this phase's own duration, not "run start -> now"
            try:
                t0 = datetime.fromisoformat(state["created"].replace("Z", "+00:00"))
                mins = (datetime.now(timezone.utc) - t0).total_seconds() / 60
            except (KeyError, ValueError, TypeError):
                mins = None
        bits = _summary_bits(card)
        if mins is not None:
            bits.append(f"{mins:.0f} min")
        line = f"{label}: " + " · ".join(bits)
        notes = [n for n in ((s.get("coverage") or {}).get("notes") or [])]
        if notes:
            line += " — " + "; ".join(notes)
        result_lines.append(line)
        warnings += [f"{label}: {w}" for w in meta.get("warnings") or []]
    if not labels:
        result_lines.append("(no models resolved)")
    if rc and not failed:
        result_lines.append(f"run FAILED — exit {rc}")
        failed.append(("run", f"`crucibleforge {cmd}` exited {rc}"
                              + (f" — {render_err}" if render_err else "")))
    lines = [f"# CrucibleForge {cmd} — {', '.join(labels) or '(none)'}", "", "## Result"]
    lines += result_lines
    lines += [f"⚠️ {w}" for w in warnings]
    argv_bits = [f"crucibleforge {cmd} --models {models_arg}"]
    for flag in ("profile", "categories", "difficulty", "cases", "judge"):
        val = getattr(args, flag, None)
        if isinstance(val, dict):   # the profile's judge block
            val = val.get("name") or val.get("model_id")
        if val:
            argv_bits.append(f"--{flag} {val}")
    if cmd == "judge" and getattr(args, "force", False):
        argv_bits.append("--force")
    lines += ["", "## Evidence",
              f"- run id: `{state['run_id']}`",
              f"- command: `{' '.join(argv_bits)}`",
              f"- suite revision: `{rev}`" if rev else "- suite revision: unknown",
              f"- started: {state['created']}  finished: {_utcnow_iso()}",
              f"- exit code: {rc}"]
    files = [results_dir() / n for n in ("report.md", "failures.md", "report.html")]
    files += [results_dir() / f"transcripts_{label}.jsonl" for label in labels]
    files.append(results_dir() / "crucibleforge.log")
    present = [f for f in files if f.exists()]
    lines += ["", "## Files"] + ([f"- `{f}`" for f in present] or ["None."])
    lines += ["", "## Failed"]
    lines += [f"- {who}: {err}" for who, err in failed] or ["None."]
    plan = getattr(args, "bench_all_plan", None)
    if plan is not None:
        from .bench_all import format_plan
        lines += ["", "## Plan", "```", format_plan(plan), "```"]
        lines += [f"- {n}" for n in getattr(args, "bench_all_notes", None) or []]
    lines += ["", "## Next"]
    if failed:
        lines += [f"- {who}: {_next_hint(err)}" for who, err in failed]
    elif rc:
        lines.append("- " + _next_hint(""))
    else:
        lines.append("None.")
    if scorecard:
        lines += ["", scorecard.rstrip()]
    return "\n".join(lines) + "\n"


def _exit_code_from_exception(e: BaseException) -> int:
    """Best-effort real exit code for the V2 report's meta.json/report.md
    (WP-BENCH review M5): every abnormal exit used to be recorded as a flat
    "exit code: 1", so a `SystemExit(2)` (bad `--cases` id) or a SIGTERM's
    `SystemExit(143)` was misreported in the one file a human reads as
    evidence. A SystemExit's own ``.code`` wins when it is an int
    (``True``/``False`` count as 1/0, matching Python's own ``sys.exit()``
    semantics; ``None`` means a plain ``sys.exit()``, i.e. 0). A string
    message (``raise SystemExit("oops")``) or any other exception type has
    no numeric code, so this falls back to 1 — the same convention Python's
    own interpreter uses for an uncaught exception reaching the top."""
    if isinstance(e, SystemExit):
        code = e.code
        if code is None:
            return 0
        if isinstance(code, int):
            return code
        return 1
    return 1


def _v2_finish(cmd: str, args, cfg: dict, rc: int, state: dict) -> None:
    """LAST act for a V2-tracked command: write report.md, then rewrite
    meta.json atomically with the terminal status. Best-effort throughout —
    a bug in this delivery-plumbing code must never change the `rc` the
    process actually exits with (see the try/except around every step)."""
    if V2_RUNS_ROOT is None:
        return
    run_id = state["run_id"]
    run_dir = _v2_run_dir(run_id)
    try:
        body = _v2_report_body(cmd, args, cfg, rc, state)
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "report.md").write_text(body, encoding="utf-8")
    except Exception as e:  # noqa: BLE001
        log.error("V2 run-report report.md write failed for %s: %s", run_id, e)
    status = "done" if rc == 0 else "failed"
    extra = dict(state.get("extra") or {})
    # the real exit code beside status (WP4, 2026-11-06): "failed" covers
    # 1 model failed / 3 public leak / 4 rig busy / 5 rig down / 143 stopped
    extra["exit_code"] = int(rc)
    if cmd == "bench-all":
        extra["bench_all"] = {
            "only": _parse_list(getattr(args, "only", None)),
            "lease_mode": getattr(args, "lease_mode", None),
            "no_judge": bool(getattr(args, "no_judge", False)
                             or getattr(args, "lease_mode", None) == "day"),
            "models": _parse_list(getattr(args, "models", None)) or []}
    try:
        meta = _v2_meta(run_id, status=status, created=state["created"], finished=_utcnow_iso(),
                        title=state["title"], requester_session=state["requester_session"],
                        thread_id=state["thread_id"], task_run_id=state["task_run_id"],
                        extra=extra)
        _atomic_write_json(run_dir / "meta.json", meta)
        log.info("V2 run-report: %s/{report.md,meta.json} written (status=%s)", run_dir, status)
    except OSError as e:
        log.error("V2 run-report LAST write failed for %s: %s", run_id, e)


def _policy_force(p) -> bool:
    if getattr(p, "force_evict_policy", False):
        log.warning("%s: bench-first policy — lease claims start with force=true "
                    "(idle residents are evicted and restored at the end)", p.name)
        return True
    return False


def _judge_policy_force(cfg: dict) -> bool:
    """The judge's own provider, same standing policy."""
    from .providers import get_provider
    for cand in (cfg.get("judge") or {}).get("candidates", []):
        try:
            p = get_provider(cfg, cand["provider"])
        except Exception:
            continue
        if p.type == "studioforge" and _policy_force(p):
            return True
    return False


class _ProviderGuard:
    """Leave the rig as we found it. LM Studio: snapshot/restore the served
    model. StudioForge: snapshot the residents, release our GPU lease at the
    end and bring the evicted residents back (a chat-tier model should
    not stay cold because a benchmark ran)."""

    def __init__(self, cfg, entries, include_judge: bool = True, force_evict: bool = False,
                 judge=None):
        from .providers import provider_for, get_provider
        self.provs = {}
        for e in entries:
            p = provider_for(cfg, e)
            if p.type in ("lmstudio", "studioforge"):
                self.provs[p.name] = p
        # With a known judge only ITS provider is guarded: a hosted-API judge
        # must not snapshot/restore (i.e. reload models on) the rig.
        judge_cands = (cfg.get("judge") or {}).get("candidates", [])
        if include_judge and judge:
            from .judge import resolve_judge_spec
            judge_cands = [resolve_judge_spec(cfg, judge)]
        if include_judge:
            for cand in judge_cands:
                try:
                    p = get_provider(cfg, cand["provider"])
                except Exception:
                    continue
                if p.type in ("lmstudio", "studioforge") and p.name not in self.provs:
                    self.provs[p.name] = p
        # WP-BENCH FIX-2: the only place `force=true` ever gets authorised for
        # this run. Never set from a refusal message — only from the CLI's
        # explicit `--force-evict` flag, which the skill says a worker may
        # pass only on the rig owner's explicit go-ahead.
        # …or by the provider's STANDING bench-first policy
        # (``force_evict: true`` in models.yaml, the rig owner's call: the bench
        # outranks everything on the rig). Logged as loudly as the flag.
        for p in self.provs.values():
            p.force_evict = force_evict or _policy_force(p)
        self.saved = {n: p.snapshot() for n, p in self.provs.items()}

    def busy(self) -> list[str]:
        """LM Studio models someone may be using (the --yes gate)."""
        return [m for n, st in self.saved.items()
                if self.provs[n].type == "lmstudio" for m in (st or [])]

    def serving(self) -> list[str]:
        """StudioForge residents that are mid-request right now."""
        return [f"{r['model_id'].rsplit('/', 1)[-1]} ({r['active_requests']} active)"
                for n, st in self.saved.items() if self.provs[n].type == "studioforge"
                for r in (st or []) if r.get("active_requests")]

    def restore(self):
        for n, p in self.provs.items():
            try:
                p.restore(self.saved.get(n))
            except Exception as e:
                log.warning("restore of %s failed: %s", n, e)


_LmStudioGuard = _ProviderGuard  # back-compat name (GUI imports it)


# --------------------------------------------------------------- commands

# The documented env var for a StudioForge gateway key (models.example.yaml).
DEFAULT_API_KEY_ENV = "STUDIOFORGE_API_KEY"


def _auth_wall_note(cfg, p, code: int | None = None) -> str | None:
    """One line when the server answered /models with 401/403, else None.

    ``alive()`` deliberately counts such an answer as reachable (some gateways
    refuse the listing yet serve completions), so without this note a gateway
    that wants a key looks plainly UP."""
    code = code or p.auth_wall()
    if code not in (401, 403):
        return None
    pcfg = cfg["providers"].get(p.name) or {}
    env = pcfg.get("api_key_env") or ""
    literal = pcfg.get("api_key")
    if not env and isinstance(literal, str) and literal.startswith("${") and literal.endswith("}"):
        env = literal[2:-1]           # `api_key: ${VAR}` names its variable too
    if p.api_key:
        source = f"${env}" if env else "api_key in models.yaml"
        text = (f"reachable, but it rejected the API key from {source} "
                f"(HTTP {code}): check the key")
    else:
        name = env or DEFAULT_API_KEY_ENV
        extra = "" if env else f" and add api_key_env: {name} to the provider in models.yaml"
        text = f"reachable, but it wants an API key: set ${name}{extra} (HTTP {code})"
    try:
        from . import quarantine
        quarantine.note_auth(p.name, f"HTTP {code}")
    except Exception:  # noqa: BLE001 — the note still prints if the alert cannot be written
        log.error("auth alert for %s failed", p.name)
    return text


def cmd_status(args, cfg):
    from .providers import all_providers
    from .judge import select_judge, JudgeError
    from .version import revision
    print(f"config: {cfg.get('_path')}   results: {results_dir()}")
    if cfg.get("_upgraded_from_v2"):
        print("  (v2 registry auto-upgraded in memory — run `crucibleforge config --upgrade` to rewrite it)")
    print(f"suite revision: {revision(cfg)}")
    floor = cfg["defaults"].get("min_tok_per_s", 0)
    print(f"viability floor: {floor} tok/s (abort a model slower than this)"
          if floor else "viability floor: off")
    print("\nproviders:")
    for p in all_providers(cfg):
        state = "UP" if p.alive() else "DOWN"
        key = ("key: $" + (cfg["providers"][p.name].get("api_key_env") or "")
               if cfg["providers"][p.name].get("api_key_env") else "")
        keyset = ("" if not key else (" (set)" if p.api_key else " (NOT SET)"))
        # WP-BENCH FIX-5: show the management PIN in the same style as an API
        # key, and base it on the LIVE (expanded) header value, not just
        # whether the header key exists — an unset ${STUDIOFORGE_MCP_PIN}
        # still leaves the key present with an empty value (providers._expand_env
        # returns "" for an unset var), so a key-presence check alone always
        # said "fine" (RC-6: a worker that forgot to source the env file got a
        # green status here and a 403 twenty minutes later).
        pin_env = _pin_env_name(cfg["providers"][p.name].get("headers")) if p.type == "studioforge" else None
        live_pin = next((v for k, v in p.headers.items() if k.lower() == "x-mcp-pin"), None)
        pin = f" pin: ${pin_env} ({'set' if live_pin else 'NOT SET'})" if pin_env else ""
        print(f"  {p.name:14s} {p.type:11s} {p.base_url:45s} {state:4s} "
              f"conc={p.concurrency} {key}{keyset}{pin}")
        wall = _auth_wall_note(cfg, p)
        if wall:
            print(f"    {wall}")
        try:
            loaded = p.loaded_models()
        except Exception as e:  # noqa: BLE001 — a missing lms CLI must not kill status
            loaded = []
            print(f"    (loaded models unavailable: {e})")
        if loaded:
            print(f"    loaded now: {[m['identifier'] for m in loaded]}")
        if p.type == "studioforge" and state == "UP":
            from . import studioforge
            try:
                for r in studioforge.residents(p.base_url, p.api_key, p.mgmt_headers()):
                    print(f"      {r['model_id'].rsplit('/', 1)[-1][:48]:48s} {r['state']:8s} "
                          f"active={r['active_requests']} by={r.get('loaded_by')} "
                          f"ctx={r['plan'].get('ctx_size')} slots={r['plan'].get('parallel')} "
                          f"devices={r['plan'].get('devices')}")
                leases = studioforge.list_leases(p.base_url, p.api_key, p.mgmt_headers())
                pin_missing = p.lease and not live_pin
                # WP-BENCH review M6: name the ACTUAL ${VAR} behind this
                # provider's X-MCP-Pin (pin_env, already resolved above) —
                # a hardcoded "STUDIOFORGE_MCP_PIN" here was wrong for any
                # models.yaml that references a different env var name.
                pin_warn = ""
                if pin_missing:
                    pin_warn = (f" (X-MCP-Pin is EMPTY — ${pin_env} not set in this shell!)"
                               if pin_env else " (X-MCP-Pin is EMPTY!)")
                print(f"    leases: {[(l.get('holder'), l.get('devices'), l.get('model_ids')) for l in leases] or 'none'}"
                      f"   lease mode: {'ON' if p.lease else 'off'}"
                      f"{pin_warn}")
                if p.force_evict_policy or p.lease_devices_preferred or p.render_vacate_mcp:
                    print(f"    bench-first: force_evict={'ON' if p.force_evict_policy else 'off'}"
                          f"  preferred cards={p.lease_devices_preferred or '-'} (else {p.lease_devices or 'all'})"
                          f"  render-lease vacate={'yes' if p.render_vacate_mcp else 'no'}"
                          f"  cut a mid-request resident after={('%.0fs' % p.busy_unload_after_s) if p.busy_unload_after_s else 'never'}")
            except Exception as e:  # noqa: BLE001
                print(f"    (management API: {e})")
    print("\nregistry (models):")
    for m in cfg["models"]:
        flag = "enabled " if m.get("enabled", True) else "disabled"
        price = m.get("price")
        pr = f" ${price.get('input', 0)}/{price.get('output', 0)} per 1M" if price else ""
        print(f"  [{flag}] {m['name']:28s} {m['provider']:12s} {m['model_id'][:60]:60s}{pr}")
    # the benchmark's judge is the one the profile names (run/all/judge use
    # it); the judge.candidates list is only the fallback walk for ad-hoc runs
    try:
        from .profiles import DEFAULT_PROFILE as _DP, load_profile as _lp, profile_judge as _pj
        pj = _pj(_lp(_DP, cfg))
    except Exception:  # noqa: BLE001
        pj = None
    if isinstance(pj, dict):
        samples = int((cfg.get("judge") or {}).get("samples", 1))
        print(f"\njudge (profile {_DP}): {pj['model_id']} @ {pj['provider']}  samples={samples}")
    else:
        try:
            judge = select_judge(cfg, set())
            print(f"\njudge: {judge['model_id']} @ {judge['provider']}")
        except JudgeError as e:
            print(f"\njudge: UNAVAILABLE — {e}")
    cases = load_cases()
    by_cat: dict[str, int] = {}
    hard = 0
    for c in cases:
        by_cat[c["category"]] = by_cat.get(c["category"], 0) + 1
        hard += c.get("difficulty") == "hard"
    print(f"\ncases: {sum(by_cat.values())} total ({hard} hard)  " +
          "  ".join(f"{k}={v}" for k, v in by_cat.items()))
    smoke = sum(1 for c in cases if c.get("smoke"))
    print(f"smoke subset: {smoke} cases")
    from .profiles import active_name, apply_profile, list_profiles, load_profile
    print(f"profiles: {', '.join(list_profiles(cfg)) or 'none'}")
    try:
        _, bench = apply_profile(load_profile(active_name(cfg), cfg), cfg)
        print(f"benchmark ({active_name(cfg)}): {len(bench)} cases — "
              f"`crucibleforge all --models <label> --fresh --yes`")
    except ConfigError as e:
        print(f"benchmark ({active_name(cfg)}): INVALID — {e}")
    return 0


def _apply_profile_arg(args, cfg):
    """The benchmark definition: --profile, else the one benchmark
    (``bench``). Returns (cfg with its budgets applied, its cases, profile)."""
    from .profiles import DEFAULT_PROFILE, apply_profile, load_profile, profile_judge
    name = getattr(args, "profile", None) or DEFAULT_PROFILE
    prof = load_profile(name, cfg)
    cfg2, cases = apply_profile(prof, cfg, smoke=getattr(args, "smoke", False))
    # P1 (fix round 2026-11-18): the adult page is opt-in; --adult asks for it for every model of the run
    from .adult_guard import RUN_KEY as _ADULT_RUN, run_wants_adult
    from . import adult_guard as AG0
    cfg2 = {**cfg2, _ADULT_RUN: run_wants_adult(name, AG0.args_wants(args))}
    if not getattr(args, "judge", None):
        args.judge = profile_judge(prof)
    log.info("profile %s: %d cases, thinking cap %s, judge %s", name, len(cases),
             cfg2["defaults"].get("thinking_max_tokens_cap"),
             (args.judge.get("model_id") if isinstance(args.judge, dict) else args.judge) or "auto")
    return cfg2, cases, prof


def _fail_fast(cfg, entries, *, check_judge: bool, judge) -> tuple[list[dict], dict]:
    """R5: refuse in seconds what would otherwise fail minutes in (a model
    the provider does not serve / a non-GGUF id on StudioForge, a judge that
    cannot fit). Returns (runnable entries, {label: error}); a failed model
    gets its meta written as FAILED with the reason so the board and the V2
    run-report say why. A judge that cannot run fails the whole command."""
    from .preflight import (hosted_credit_problems, judge_unrunnable_reason,
                            unrunnable_reason)
    from .providers import provider_for
    from .runner import _write_meta
    from .version import revision
    if check_judge:
        why = judge_unrunnable_reason(cfg, judge)
        if why:
            raise SystemExit(f"judge check: {why}")
    credit = hosted_credit_problems(cfg, entries)
    ok, bad = [], {}
    for e in entries:
        try:
            why = unrunnable_reason(cfg, e)
        except Exception as ex:  # noqa: BLE001 — a flaky listing must not block the run
            log.warning("runnability check for %s skipped: %s", e["name"], ex)
            why = None
        why = why or credit.get(e["name"])
        if why:
            log.error("model %s cannot run: %s", e["name"], why)
            bad[e["name"]] = why
            _write_meta(e["name"], e, provider_for(cfg, e), failed=True, error=why,
                        profile=cfg.get("_profile"), revision=revision(cfg), reset=True)
        else:
            ok.append(e)
    return ok, bad


def _conformance_gate(cfg, entries, *, skip: bool = False) -> tuple[list[dict], dict]:
    """The conformance gate (Part 3): a model is benched only under a request
    protocol that has been PROVEN for it.

    The record has to be for the CURRENT fingerprint; when it is missing or
    stale the checks run now (an API model needs nothing loaded).  A FAIL
    means the model is NOT run -- its meta is written as FAILED with the
    reason, so the board and the run report say why.  A rig model is gated
    by the runner instead: it can only be checked while it is loaded.
    """
    from . import conformance
    from .providers import provider_for
    from .runner import _write_meta
    from .version import revision
    ok, bad = conformance.gate_entries(cfg, entries, skip=skip)
    for label, why in bad.items():
        log.error("model %s is not benched: %s", label, why)
        entry = next((e for e in entries if e.get("name") == label), None)
        if entry is not None:
            _write_meta(label, entry, provider_for(cfg, entry), failed=True,
                        error=why, profile=cfg.get("_profile"),
                        revision=revision(cfg), reset=True)
    return ok, bad


def _settings_gate(cfg, entries) -> tuple[list[dict], dict]:
    """(runnable entries, {label: why}) under settings_gate.check. Nothing is
    written: a refused model keeps whatever it has on the board."""
    from . import settings_gate
    from .providers import provider_for
    ok, bad = [], {}
    for e in entries:
        try:
            ptype = provider_for(cfg, e).type
        except Exception:  # noqa: BLE001 — resolved (and reported) later
            ok.append(e)
            continue
        v = settings_gate.check(cfg, e, ptype)
        if v.ok:
            ok.append(e)
        else:
            log.error("model %s is not benched: %s\n%s", e.get("name"), v.reason, v.fix)
            bad[e["name"]] = "needs_settings: needs recommended settings: " + v.reason
            try:
                from . import quarantine
                quarantine.hold_settings(e["name"], v.reason, e.get("model_id"))
            except Exception:  # noqa: BLE001 — the refusal stands even if the record cannot be written
                log.error("quarantine record for %s failed", e.get("name"))
    return ok, bad


def _refuse_fresh_with_filter(args) -> None:
    """``--fresh`` archives the model's WHOLE result set; combined with a
    ``--cases``/``--categories`` filter it would re-run a handful of cases
    and throw away every other row. Refuse unless ``--force``."""
    if not getattr(args, "fresh", False) or getattr(args, "force", False):
        return
    flt = [f for f, v in (("--cases", getattr(args, "cases", None)),
                          ("--categories", getattr(args, "categories", None))) if v]
    if flt:
        raise SystemExit(
            f"refusing --fresh with {' and '.join(flt)}: --fresh archives the model's "
            "WHOLE result set, not just the filtered cases. Use --fresh alone for a full "
            f"re-run, or {' / '.join(flt)} alone for a targeted re-run (the new rows "
            "supersede the old ones). Pass --force to archive everything anyway.")


def _unpriced_hosted(cfg: dict, entries: list[dict]) -> list[str]:
    """Labels of hosted (non-rig) models whose entry has no ``price`` block: a
    run would record no cost, and the board's Run cost would be blank."""
    from .providers import provider_for
    from .report import LOCAL_PROVIDER_TYPES
    out = []
    for e in entries:
        try:
            ptype = provider_for(cfg, e).type
        except Exception:  # noqa: BLE001 — resolved (and reported) later
            continue
        if ptype not in LOCAL_PROVIDER_TYPES and not e.get("price"):
            out.append(e["name"])
    return out


def cmd_run(args, cfg):
    from .runner import run_models
    # labels the generation phase actually ran (set below once run_models has
    # been called; None = the phase never started) — cmd_all judges these
    # even when another model of the batch failed
    args.ran_labels = None
    _refuse_fresh_with_filter(args)
    cfg, cases, prof = _apply_profile_arg(args, cfg)
    entries = resolve_models(cfg, args.models)
    if args.categories:
        want = set(_parse_list(args.categories))
        cases = [c for c in cases if c["category"] in want]
    diffs = _parse_list(getattr(args, "difficulty", None))
    if diffs:
        cases = [c for c in cases if c.get("difficulty", "medium") in diffs]
    only = _parse_list(getattr(args, "cases", None))
    if only:
        cases = [c for c in cases if c["id"] in only]
        missing = set(only) - {c["id"] for c in cases}
        if missing:
            raise SystemExit(f"unknown case id(s): {sorted(missing)}")
        # an explicit adult id for a model with adult_page off is refused before any call (adult_guard)
        from .adult_guard import RUN_KEY as _ADULT_RUN, explicit_opt_in, refuse_explicit
        refuse_explicit(cfg, entries, cases, only)
        if explicit_opt_in(cases, only):
            cfg = {**cfg, _ADULT_RUN: True}     # naming adult units is asking for them (P1)
    from . import ladder as _ladder
    if not _ladder.include_staged():
        staged = sorted(c["id"] for c in cases if c.get("ladder_staged"))
        if staged and only:
            raise SystemExit(f"case(s) {staged} belong to a staged pack: pass --include-staged "
                             "(and --results <scratch dir>) to run them")
        cases = [c for c in cases if not c.get("ladder_staged")]
    if not entries:
        raise SystemExit("no models selected (all disabled? pass --models)")
    if not cases:
        raise SystemExit("no cases selected")
    unpriced = _unpriced_hosted(cfg, entries)
    if unpriced:
        msg = (f"hosted model(s) without a price: {', '.join(unpriced)} — the board's Run cost "
               "would be missing. Add `price: {input: X, output: Y}` per million tokens to "
               "models.yaml (`basis: list` for a flat-plan provider billed outside tokens; "
               "`input: 0, output: 0` for a free endpoint).")
        if not getattr(args, "allow_unpriced", False):
            raise SystemExit(msg + " Pass --allow-unpriced to run anyway.")
        from . import spend as _spend
        capped = [e["name"] for e in entries if e["name"] in unpriced
                  and _spend.explicit_cap_set(str(e.get("model_id") or ""))]
        if capped:
            # the provider would refuse the first call (fail closed, an HTTP-402-shaped stop): say so now
            raise SystemExit(f"hosted model(s) {', '.join(capped)} have no price, so the spend cap that is "
                             "set cannot bound them: add `price: {input: X, output: Y}` per million tokens "
                             "to models.yaml, or drop the cap (--spend-cap-usd / $CF_SPEND_CAP_USD / "
                             "$CF_SPEND_GLOBAL_CAP_USD) for this run.")
        log.warning("%s (--allow-unpriced)", msg)
    if getattr(args, "allow_unpriced", False):
        from . import providers
        providers.ALLOW_UNPRICED = True
    if getattr(args, "fresh", False):
        _archive_labels([e["name"] for e in entries])
    needs_judge = any(c.get("rubric") or c.get("turns") for c in cases)
    entries, unrunnable = _fail_fast(cfg, entries, judge=getattr(args, "judge", None),
                                     check_judge=args.cmd == "all" and needs_judge
                                     and not getattr(args, "no_judge", False))
    # 2026-11-13: no rig model is benched before its recommended settings
    # were checked (settings_gate; registry sampler, profile rule or
    # settings_checked)
    entries, settings_bad = _settings_gate(cfg, entries)
    unrunnable.update(settings_bad)
    # the conformance gate (Part 3): a model is benched only under a request
    # protocol that has been proven for it
    entries, gate_bad = _conformance_gate(
        cfg, entries, skip=bool(getattr(args, "skip_conformance", False)))
    unrunnable.update(gate_bad)
    if not entries:
        print("\nrun summary:")
        for label, why in unrunnable.items():
            print(f"  {label}: FAILED — {why}")
        return 1
    if not getattr(args, "no_link_check", False):
        from .preflight import check_link_health
        check_link_health(cfg, entries)
    log.info("run: models=%s cases=%d smoke=%s fresh=%s",
             [e["name"] for e in entries], len(cases), args.smoke,
             getattr(args, "fresh", False))

    guard = _ProviderGuard(cfg, entries, force_evict=getattr(args, "force_evict", False))
    busy = guard.busy()
    if busy and not args.yes:
        print(f"LM Studio is currently serving {busy} — someone may be "
              f"using it.\nRe-run with --yes to proceed (the model will be "
              f"restored afterwards).")
        return 1
    serving = guard.serving()
    if serving:
        log.warning("StudioForge residents mid-request right now: %s — the load will wait "
                    "for them to go idle (never evicts a serving model)", serving)
    try:
        from . import routing as _routing
        if _routing.spec(prof) is not None:
            summary = _run_routed(cfg, entries, cases, prof, args)
        else:
            summary, entries = _run_selected(cfg, entries, cases, prof, args, only, run_models)
    finally:
        guard.restore()
    args.ran_labels = [e["name"] for e in entries if e["name"] in summary]
    if getattr(args, "no_judge", False):
        _stamp_no_judge(args.ran_labels, True)
    for label, why in unrunnable.items():
        summary[label] = {"failed": True, "error": why}
    print("\nrun summary:")
    _print_run_summary(summary)
    return _run_rc(summary, [e["name"] for e in entries] + list(unrunnable))


def _first_reply_rule(prof) -> bool:
    from . import attempts45 as _at45
    return str((prof or {}).get(_at45.RULE_KEY) or "").strip().lower() == _at45.RULE


def _first_reply_jobs(label: str, cfg: dict, cases: list[dict], smoke: bool = False) -> set:
    """``{(None, case id, repeat)}`` the label still owes under the first-reply rule: every (case, repeat) of
    ``cases`` that has no stored row that produced a model reply (attempts45.is_infra rows are owed again)."""
    from . import attempts45 as _at45
    from . import report
    from .config import repeats_for
    have = {(r.get("case_id"), r.get("repeat") or 1)
            for r in report.board_filter(label, cfg, sampler_view="current") if not _at45.is_infra(r)}
    return {(None, c["id"], n) for c in cases
            for n in range(1, repeats_for(cfg, c["category"], smoke, c) + 1)
            if (c["id"], n) not in have}


def _run_routed(cfg, entries, cases, prof, args) -> dict:
    """4.5 bank (routing.py): per model, the locator stage (with every set the routing does
    not name), then the module(s) its locator ability routes it to. The route is decided by
    score alone and rides on every module row. A model whose locator did not finish, or
    could not be scored on the calibration, is not routed (recorded in its summary)."""
    from . import config as _cfgmod
    from . import routing as _routing
    from .runner import run_models
    summary: dict = {}
    for e in entries:
        if halted():
            break             # a drain / stop: no next model
        label = e["name"]
        kw = dict(smoke=args.smoke, skip_conformance=getattr(args, "skip_conformance", False))
        p = _routing.plan(_cfgmod.load_transcripts(label), cases, prof, base=_cfgmod.ROOT_DIR)
        if p["stage"] == "locator":
            log.info("%s: routing -- locator stage (%d cases)%s", label, len(p["cases"]),
                     f", continuing run {p['bench_run_id']}" if p.get("bench_run_id") else "")
            s1 = run_models(cfg, [e], p["cases"], **kw, stage="locator",
                            bench_run_ids={label: p["bench_run_id"]} if p.get("bench_run_id") else None)
            summary.update(s1)
            if (s1.get(label) or {}).get("failed"):
                continue
            p = _routing.plan(_cfgmod.load_transcripts(label), cases, prof, base=_cfgmod.ROOT_DIR)
        if p["stage"] != "module":
            why = p.get("why") or f"locator unfinished ({len(p.get('pending') or [])} unit(s) ungraded)"
            log.error("%s: not routed: %s", label, why)
            summary.setdefault(label, {})["routing"] = {"routed": False, "why": why}
            continue
        r = p["route"]
        log.info("%s: %s by score: theta %.2f (SE %.2f) -> %s%s", label,
                 "resumed on its stored route" if r.get("reused") else "routed", r["theta"], r["se"],
                 " + ".join(f"{m['name']} ({m['share']})" for m in r["modules"]),
                 " -- packs unlocked" if r["unlock"] else "")
        # one run: the module stage continues the locator stage's bench_run_id
        rid = p.get("bench_run_id")
        s2 = (run_models(cfg, [e], p["cases"], **kw, stage="module", bench_run_ids={label: rid} if rid else None)
              if p["cases"] else {})
        merged = dict(summary.get(label) or {})
        merged.update(s2.get(label) or {})
        merged["routing"] = {"routed": True, **r}
        summary[label] = merged
    return summary


def _run_rc(summary: dict, wanted: list[str]) -> int:
    """Non-zero when any requested model failed or never ran (a queue script
    must not stamp DONE over a half-finished batch)."""
    missing = [l for l in wanted if l not in summary]
    failed = [l for l, s in summary.items() if s.get("failed")]
    if missing:
        print(f"  NOT RUN: {', '.join(missing)}")
    return 1 if (missing or failed) else 0


def _run_selected(cfg, entries, cases, prof, args, only, run_models) -> tuple[dict, list[dict]]:
    """The plain (non-routed) generation of ``run``: (summary, the entries that ran)."""
    jobs_by = None
    chain_sel: list[dict] = []
    all_entries = list(entries)
    if only and _first_reply_rule(prof):
        # retry fairness: a unit whose first attempt produced a reply is never presented again by a
        # resume (only an infrastructure failure is); to re-draw on purpose, archive the rows first.
        # A named chain (FLAT-<set>-<i>) is not a job of the recover path, which skips chains: it
        # runs in its own board pass below, where the chain driver resumes from the stored rows
        # (a graded attempt is never presented again), so only its owed attempts are drawn.
        chain_sel = [c for c in cases if c.get("chain_spec")]
        cases = [c for c in cases if not c.get("chain_spec")]
        jobs_by = {e["name"]: _first_reply_jobs(e["name"], cfg, cases, args.smoke) for e in entries}
        for label, js in jobs_by.items():
            print(f"{label}: {len(js)} job(s) to run (units that already have a reply are not re-drawn)"
                  + (f"; {len(chain_sel)} named chain(s) resume from their stored attempts"
                     if chain_sel else ""))
        entries = [e for e in entries if jobs_by[e["name"]]]
    summary = {}
    if entries:
        summary = run_models(cfg, entries, cases, smoke=args.smoke, jobs_by_label=jobs_by,
                             skip_conformance=getattr(args, "skip_conformance", False))
    if chain_sel and not halted():
        more = run_models(cfg, all_entries, chain_sel, smoke=args.smoke,
                          skip_conformance=getattr(args, "skip_conformance", False))
        summary = _merge_summaries(summary, more)
        entries = all_entries
    return summary, entries


def _merge_summaries(a: dict, b: dict) -> dict:
    """Two run_models summaries of the same command (the standard jobs, then the named chains) as one:
    rows, costs and error counts add up; a failure in either pass is the label's failure."""
    out = {k: dict(v) for k, v in a.items()}
    for label, s in b.items():
        if label not in out:
            out[label] = dict(s)
            continue
        m = out[label]
        if s.get("failed") and not m.get("failed"):
            m["failed"], m["error"] = True, s.get("error")
        for k in ("rows", "skipped", "case_errors"):
            if s.get(k) is not None:
                m[k] = (m.get(k) or 0) + s[k]
        if s.get("cost_usd") is not None:
            m["cost_usd"] = round((m.get("cost_usd") or 0.0) + s["cost_usd"], 4)
        m["warnings"] = list(m.get("warnings") or []) + list(s.get("warnings") or [])
        for k in ("load_s", "device"):
            m.setdefault(k, s.get(k))
    return out


def _print_run_summary(summary: dict) -> None:
    for label, s in summary.items():
        if s.get("failed"):
            print(f"  {label}: FAILED — {s.get('error')}")
        else:
            extra = f", {s['skipped']} skipped (ctx)" if s.get("skipped") else ""
            errs = f", {s['case_errors']} case error(s)" if s.get("case_errors") else ""
            cost = f", ${s['cost_usd']:.4f}" if s.get("cost_usd") is not None else ""
            plan = s.get("plan") or {}
            placement = (f", slots={plan.get('parallel')} ctx={plan.get('ctx_size')} "
                         f"devices={plan.get('devices')}" if plan else "")
            print(f"  {label}: {s['rows']} rows, load {s['load_s']}s "
                  f"({s['device']}{placement}){extra}{errs}{cost}")
            for w in s.get("warnings") or []:
                print(f"    WARNING: {w}")


def cmd_recover(args, cfg):
    """Re-run only the reasoning-overflow rows (finish=length, no content) of
    existing transcripts through the answer-recovery ladder, then re-judge
    them with `crucibleforge judge`. Rows are re-written under their original
    bench_run_id so they supersede the empty ones; nothing is deleted."""
    from .runner import recover_models, overflow_jobs
    from .config import load_transcripts
    cfg, _prof_cases, _prof = _apply_profile_arg(args, cfg)
    from . import attempts45 as _at45
    if str((_prof or {}).get(_at45.RULE_KEY) or "").strip().lower() == _at45.RULE:
        # retry fairness: a reply cut at its budget is the unit's score; a re-draw would be ignored at report time
        print("recover: refused under this profile's first-reply rule (attempt_rule: first_reply): a cut reply is the "
              "unit's score and a later attempt is ignored. Only infrastructure failures are retried (a plain resume).")
        return 2
    entries = resolve_models(cfg, args.models)
    # every overflow row is a candidate whatever profile/categories the run
    # used — the job list is filtered to the rows that overflowed anyway
    cases = load_cases()
    if not entries:
        raise SystemExit("no models selected (pass --models)")
    todo = {e["name"]: len(overflow_jobs(load_transcripts(e["name"]))) for e in entries}
    print("reasoning-overflow jobs to recover: " +
          ", ".join(f"{k}={v}" for k, v in todo.items()))
    if not any(todo.values()):
        print("nothing to recover")
        return 0
    if not getattr(args, "no_link_check", False):
        from .preflight import check_link_health
        check_link_health(cfg, [e for e in entries if todo[e["name"]]])
    guard = _ProviderGuard(cfg, entries, force_evict=getattr(args, "force_evict", False))
    busy = guard.busy()
    if busy and not args.yes:
        print(f"LM Studio is currently serving {busy} — re-run with --yes to proceed.")
        return 1
    try:
        summary = recover_models(cfg, entries, cases)
    finally:
        guard.restore()
    print("\nrecover summary:")
    _print_run_summary(summary)
    print("recovered rows carry no judge verdict — run `crucibleforge judge --models "
          f"{args.models}` (same --judge) to score them, then `crucibleforge report`.")
    return _run_rc(summary, [e["name"] for e in entries if todo.get(e["name"])])


def cmd_escalate(args, cfg):
    """Re-run the jobs that ran out of the board's budget on the profile's
    escalation rungs (runner.DEFAULT_ESCALATION_RUNGS: 64k, then 128k for what
    still runs out), the model reloaded with a context wide enough for the
    rung. The rows carry ``budget_rung``: they feed the report's Room column
    and never the board. They need the judge like any other row (`all` and
    `bench-all` judge right after)."""
    from .runner import escalate_models, escalation_rungs, escalation_todo
    from .profiles import all_tier_cases
    from . import report
    cfg, cases, prof = _apply_profile_arg(args, cfg)
    # suite 3.9: a tier-2/3 row that ran out of budget is escalated like a
    # tier-1 row (the jobs come from the rows; this is only what is selectable)
    cases = all_tier_cases(prof, smoke=getattr(args, "smoke", False))
    entries = resolve_models(cfg, args.models)
    # 2026-11-13: an escalation re-run is generation too (settings_gate)
    entries, _bad = _settings_gate(cfg, entries)
    if not entries:
        raise SystemExit("no models selected (pass --models)")
    rungs = escalation_rungs(cfg)
    if getattr(args, "rungs", None):
        rungs = sorted({int(x) for x in _parse_list(args.rungs)})
    if not rungs:
        print("escalation is off for this profile (no escalation_rungs above the cap)")
        return 0
    todo = {e["name"]: len(escalation_todo(report.board_filter(e["name"], cfg, with_rungs=True,
                                                                           sampler_view="current"),
                                           e["name"], rungs)) for e in entries}
    print(f"escalation rungs {rungs} — jobs out of budget: " +
          ", ".join(f"{k}={v}" for k, v in todo.items()))
    entries = [e for e in entries if todo[e["name"]]]
    if not entries:
        print("nothing to escalate")
        return 0
    if not getattr(args, "no_link_check", False):
        from .preflight import check_link_health
        check_link_health(cfg, entries)
    guard = _ProviderGuard(cfg, entries, force_evict=getattr(args, "force_evict", False))
    busy = guard.busy()
    if busy and not getattr(args, "yes", False):
        print(f"LM Studio is currently serving {busy} — re-run with --yes to proceed.")
        return 1
    try:
        by_rung = escalate_models(cfg, entries, cases, rungs=rungs)
    finally:
        guard.restore()
    rc = 0
    for rung, summary in by_rung.items():
        print(f"\nescalation {rung} summary:")
        _print_run_summary(summary)
        rc = rc or _run_rc(summary, list(summary))
    return rc


def _escalate_phase(args, cfg, labels: list[str]) -> int:
    """The escalation round inside `all` — OPT-IN since suite 3.9
    (``--escalate``; EVAL-FABLE M1, run side): the timed run never re-runs
    what ran out of budget. An out-of-budget row is a stored row with
    finish=length (the board scores it as run out); an escalated rerun is
    counted when one exists (standalone `crucibleforge escalate`, or
    ``--escalate``). ``--no-escalate`` stays accepted (a no-op). A failure
    here never stops the judge or the report."""
    if not getattr(args, "escalate", False) or getattr(args, "no_escalate", False) or not labels:
        return 0
    e_args = copy.copy(args)
    e_args.models = ",".join(labels)
    e_args.rungs = None
    e_args.yes = True
    try:
        return cmd_escalate(e_args, cfg)
    except SystemExit as e:
        log.error("escalation phase refused: %s", e)
        return 1
    except Exception as e:  # noqa: BLE001 — the board results are still worth judging
        log.error("escalation phase failed: %s", e)
        return 1


def _judge_model_id(args, cfg) -> str | None:
    """The canonical id of the judge this command uses (--judge, else the
    profile's judge)."""
    try:
        from .judge import resolve_judge_spec
        from .profiles import DEFAULT_PROFILE, load_profile, profile_judge
        spec = getattr(args, "judge", None) or profile_judge(
            load_profile(getattr(args, "profile", None) or DEFAULT_PROFILE, cfg))
        cand = resolve_judge_spec(cfg, spec)
        return cand["model_id"] if cand else None
    except Exception:  # noqa: BLE001
        return None


def judge_instance_arg(args, cfg=None) -> dict | None:
    """The judge copy a ``judge`` command names (instances.to_cand keys), or
    None for the ordinary single judge. ``--instance NAME`` takes the copy
    from models.yaml ``instances:`` (for the judge's model id); the explicit
    --judge-* flags override its fields."""
    from . import instances as I
    base = {}
    name = getattr(args, "instance", None)
    if name:
        mid = _judge_model_id(args, cfg or {})
        if not mid:
            raise SystemExit("--instance: the judge model could not be resolved")
        try:
            base = I.to_cand(I.find(cfg or {}, mid, name))
        except ValueError as e:
            raise SystemExit(str(e))
    keys = {
        "serve_model_id": getattr(args, "judge_serve_id", None) or None,
        "instance": getattr(args, "judge_instance", None) or None,
        "instance_name": getattr(args, "judge_instance_name", None) or None,
        "instance_devices": (list(I.parse_devices(args.judge_devices))
                             if getattr(args, "judge_devices", None) else None),
        "instance_parallel": (list(I.parse_parallel(args.judge_parallel))
                              if getattr(args, "judge_parallel", None) else None),
        "instance_allow_devices": (list(I.parse_devices(args.judge_allow_devices))
                                   if getattr(args, "judge_allow_devices", None) else None),
        "instance_window": getattr(args, "window", None) or None,
        "instance_vacate": True if getattr(args, "vacate", False) else None,
    }
    base.update({k: v for k, v in keys.items() if v is not None})
    if (getattr(args, "window", None) or "").lower() in ("any", "none", "off"):
        base.pop("instance_window", None)
    return base or None


def _shard_of(args):
    from .shard import parse_shard
    s = getattr(args, "shard", None)
    return parse_shard(s) if s else None


def sharded_judge(args) -> bool:
    """A judge run that shares the rig with other judge shards: it holds the
    rig lock SHARED (any other rig command still excludes it) plus exclusive
    per-GPU locks on its own cards."""
    return getattr(args, "cmd", None) == "judge" and bool(
        getattr(args, "shard", None) or getattr(args, "judge_devices", None)
        or getattr(args, "instance", None) or getattr(args, "pool", False))


def _apply_process_switches(args) -> None:
    """Flags that hold for the whole invocation (its run, judge and report
    phases, and anything it spawns): --include-staged and --spend-cap-usd
    travel as environment variables so every layer reads one switch."""
    from . import ladder
    if getattr(args, "include_staged", False) or ladder.staged_requested():
        # a staged pack is unapproved: its rows go to a scratch results dir,
        # never the board's (spec v2 §4.6, gate G0). The environment switch
        # (CRUCIBLEFORGE_INCLUDE_STAGED) is held to the same rule as the flag
        from .config import SCRATCH_MARKER, init_scratch, results_dir, staged_isolated
        rd = os.path.realpath(results_dir())
        live = not staged_isolated(rd)
        if not live and not os.path.exists(os.path.join(rd, SCRATCH_MARKER)):
            # the explicit scratch contract: a staged run initialises its own
            # results dir (a fresh or non-board directory) before writing a row
            init_scratch(rd)
        if live:
            os.environ.pop(ladder.STAGED_ENV, None)
            via = "flag" if getattr(args, "include_staged", False) else f"${ladder.STAGED_ENV}"
            raise SystemExit("--include-staged writes rows of unapproved packs: pass "
                             f"--results <scratch dir> (never the board's results/) "
                             f"[switch: {via}, results: {rd}]")
        os.environ[ladder.STAGED_ENV] = "1"
    cap = getattr(args, "spend_cap_usd", None)
    if cap is None and ladder.staged_requested():
        from . import spend
        if not os.environ.get(spend.CAP_ENV, "").strip() and not os.environ.get(spend.RUN_CAP_ENV, "").strip():
            # a staged (unapproved-pack) run always has a ceiling: the default
            # applies to staged runs ONLY, the live board's runs keep theirs
            os.environ[spend.RUN_CAP_ENV] = repr(spend.DEFAULT_STAGED_CAP_USD)
            spend.reset()
    if cap is not None:
        from . import spend
        # the flag caps THIS invocation's own spend and never reads the shared ledger; the env cap
        # ($CF_SPEND_CAP_USD) stays the cumulative budget across invocations (docs/RUNNING.md)
        os.environ[spend.RUN_CAP_ENV] = repr(float(cap))
        spend.reset()


def maintenance_marker_present() -> bool:
    """An operator's maintenance marker: the file $CF_MAINTENANCE_MARKER names exists (unset = no check)."""
    import os as _os
    p = _os.environ.get("CF_MAINTENANCE_MARKER", "").strip()
    return bool(p) and _os.path.exists(_os.path.expanduser(p))


def cmd_ladder_judge(args, cfg):
    """`ladder-judge`: crucibleforge/rpladder_run."""
    import datetime
    from . import rpladder_run as LR
    if not getattr(args, "profile", None):
        args.profile = "bench45"
    if maintenance_marker_present() and not args.plan:
        print("maintenance marker present: nothing done", file=sys.stderr)
        return 3
    cfg, _cases, prof = _apply_profile_arg(args, cfg)
    cfg = _guard_45_judge(args, cfg, prof)
    labels = [e["name"] for e in resolve_models(cfg, args.models)]
    windows = [int(x) for x in args.windows.split(",")] if args.windows else None
    windows = LR.part_windows(prof, windows)
    j = prof.get("judge") or {}
    jmodel = j.get("model_id") if isinstance(j, dict) else str(j)
    cfg = {**cfg, "_profile": args.profile}
    if args.plan:
        out = LR.plan(cfg, prof, labels, jmodel, windows)
        print(LR.dumps(out))
        return 0
    deadline = None
    if args.until:
        hh, mm = (int(x) for x in args.until.split(":"))
        now = datetime.datetime.now()
        t = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if t <= now:
            t += datetime.timedelta(days=1)
        deadline = t.timestamp()
    override = args.judge if isinstance(args.judge, (str, dict)) and args.judge else None
    # the pair judge takes its own GPU lease inside LR.run (a loaded judge); inside `all` the process lives on, so
    # the lease must go back here (a hosted or attached judge holds none: restore is then a no-op)
    guard = _ProviderGuard(cfg, [], judge=override)
    try:
        out = LR.run(cfg, prof, labels, windows=windows, judge_override=override, deadline=deadline,
                     rung_check_only=args.rung_check, force=args.force, attach=args.attach,
                     client=args.client)
    finally:
        guard.restore()
    for line in LR.summary_lines(out):
        print(line)
    return 0 if out.get("rung_check_complete") else 1


def cmd_agree(args, cfg):
    """`agree`: a second reader of another model family re-grades the stored judged units (crucibleforge/agree).
    Each folder goes to the first ``--reader`` whose family differs from both its production judge and the model
    it grades; a folder no reader fits is recorded as "agreement not measured" (no call made)."""
    from pathlib import Path as _P
    from . import agree as AG
    from .judge import JudgeClient, _load_judge, acquire_judge_lease, resolve_judge_spec, JudgeLeaseUnavailable
    folders = [_P(f) for f in args.folders]
    from . import packs45 as PK
    parts = tuple(p for p in (args.parts or f"{AG.LONG_PART},{AG.SESSION_PART}").split(",") if p)
    # the Chat pack scenes (crucibleforge/packs45.py) are read by their own routine and stored in the reader store
    do_packs = PK.PACKS_PART in parts
    parts = tuple(p for p in parts if p != PK.PACKS_PART)
    thinking = args.reader_thinking == "on"

    def cand_of(name):
        cand = dict(resolve_judge_spec(cfg, name))
        for k, v in (("instance_name", args.judge_instance_name or "agree"),
                     ("instance_devices", [int(x) for x in args.judge_devices.split(",")] if args.judge_devices
                      else None),
                     ("instance_parallel", [int(x) for x in args.judge_parallel.split(",")] if args.judge_parallel
                      else None),
                     ("instance_allow_devices", [int(x) for x in args.judge_allow_devices.split(",")]
                      if args.judge_allow_devices else None)):
            if v is not None:
                cand[k] = v
        cand.setdefault("context_length", 16384)
        ptype = ((cfg.get("providers") or {}).get(cand["provider"]) or {}).get("type")
        return cand, ptype not in ("studioforge", "lmstudio")

    readers = [cand_of(n) for n in args.reader]
    from . import judge45 as _J
    for cand, hosted in readers:
        if hosted:
            # owner 2026-11-21: no hosted model grades or checks anything (the agree step left the default pipeline)
            print(str(_J.HostedGrader(f"agree: reader {cand['model_id']} is served by a hosted API; MakoBench 4.5 "
                                      "grades and checks with one local judge only. Nothing was called.")),
                  file=sys.stderr)
            return 2
    info = {}
    for f in folders:
        label, rows = AG.read_folder(f)
        n = (len(AG.long_units(rows)) * (AG.LONG_PART in parts)
             + len(AG.session_units(rows)) * (AG.SESSION_PART in parts)
             + (len(AG.adult_units(rows)) if args.adult else 0)
             + (len(PK.pack_units(rows)) if do_packs else 0))
        info[f] = (label, AG.production_judge(rows), AG.graded_family(label, rows[0] if rows else None), n)
    plan: dict[int, list] = {}
    unread = []
    for f, (label, jid, gf, n) in info.items():
        pick = None
        for i, (cand, hosted) in enumerate(readers):
            try:
                AG.check_reader(cand["model_id"], jid, hosted=hosted, cap_usd=args.cap_usd)
            except AG.ReaderRefused as e:
                print(str(e), file=sys.stderr)
                return 2
            if gf and AG.family(cand["model_id"]) != gf:
                pick = i
                break
        if pick is None:
            unread.append(f)
        else:
            plan.setdefault(pick, []).append(f)
    total = 0.0
    for i, fs in sorted(plan.items()):
        cand, hosted = readers[i]
        n_units = sum(info[f][3] for f in fs)
        price = (next((m for m in cfg.get("models") or [] if m.get("model_id") == cand["model_id"]), {})
                 or {}).get("price") or {}
        calls = n_units * 30
        est = calls * (4000 * float(price.get("input") or 0) + 300 * float(price.get("output") or 0)) / 1e6
        total += est if hosted else 0.0
        print(f"agree: reader {cand['model_id']} ({AG.family(cand['model_id'])}, thinking "
              f"{'on' if thinking else 'off'}) on {', '.join(info[f][0] for f in fs)} vs judge "
              f"{', '.join(sorted({str(info[f][1]) for f in fs}))}; {n_units} judged units, about {calls} calls"
              + (f"; plan price about ${est:.2f} (this run: up to ${args.cap_usd:.2f})" if hosted else "; rig (free)"))
        from . import spend as _spend_mod
        if hosted and _spend_mod.budget_line():
            print("agree:" + _spend_mod.budget_line().strip())
    for f in unread:
        print(f"agree: {info[f][0]}: no reader of another family than its graded model ({info[f][2]}) and its "
              "judge: agreement not measured")
    if args.plan:
        return 0
    if total and args.cap_usd is not None and total > args.cap_usd:
        print(f"agree: the plan price ${total:.2f} is over the cap ${args.cap_usd:.2f}: nothing done", file=sys.stderr)
        return 2
    if do_packs:
        # the records are stamped with the PINNED reader's id: refuse a --reader that is not that model (no call made)
        from . import rplong_mean as _RM
        for i, fs in sorted(plan.items()):
            for f in fs:
                try:
                    pin = _RM.pinned_reader(info[f][2], _RM.load_readers(), True)
                except _RM.NoFittingReader:
                    continue                       # the packs loop below reports it and leaves the scenes pending
                if _RM.canonical_id(readers[i][0]["model_id"]) != pin.id:
                    print(f"agree: {info[f][0]}: --reader {readers[i][0]['model_id']} is not the pinned reader "
                          f"{pin.id} for a {info[f][2]} row; pack records are stored under the pinned reader's id: "
                          "nothing done", file=sys.stderr)
                    return 2
    if maintenance_marker_present():
        print("maintenance marker present: nothing done", file=sys.stderr)
        return 3
    recs = []
    for i, fs in sorted(plan.items()):
        cand, hosted = readers[i]
        reader = cand["model_id"]
        n_units = sum(info[f][3] for f in fs)
        guard = None
        if not hosted:
            guard = _ProviderGuard(cfg, [], judge=cand)
            for p in guard.provs.values():
                p.own_unload_only = True
            try:
                acquire_judge_lease(cfg, override=cand, instance={k: v for k, v in cand.items()
                                                                  if k.startswith("instance") or k == "serve_model_id"})
            except JudgeLeaseUnavailable as e:
                print(f"agree: reader lease: {e}", file=sys.stderr)
                guard.restore()
                return 4
        try:
            jc = _load_judge(cfg, set(), rows=n_units, samples=1, override=cand) if not hosted \
                else JudgeClient(cfg, cand)
            if hosted:
                price = (next((m for m in cfg.get("models") or [] if m.get("model_id") == reader), {})
                         or {}).get("price") or {}
                jc = AG.CappedClient(jc, price, args.cap_usd)
            if parts or args.adult:
                recs += AG.run(fs, jc, reader, adult=args.adult, parts=parts, thinking=thinking)
            if do_packs:
                from . import rplong_mean as RM
                for f in fs:
                    label, rows = AG.read_folder(f)
                    gf = info[f][2]
                    try:
                        pin = RM.pinned_reader(gf, RM.load_readers(), True)
                    except RM.NoFittingReader as e:
                        print(f"agree: {label}: no pinned reader for packs ({e}): pack scenes stay pending",
                              file=sys.stderr)
                        continue
                    got = PK.run_second_reader(rows, pin, jc=jc, results=f, label=label, graded_family=gf)
                    print(f"agree: {label}: {len(got)} pack scene record(s) in the reader store ({pin.id})")
        finally:
            if guard is not None:
                guard.restore()
    if not parts and not args.adult:
        return 0                                   # packs only: the records are in each folder's reader store
    if unread:
        # every unit clashes: run() records them as not measured and makes no call
        recs += AG.run(unread, None, readers[0][0]["model_id"], adult=args.adult, parts=parts, thinking=thinking)
    summary = AG.summarize(recs)
    judges = {info[f][1] for f in folders}
    jname = next(iter(judges)) if len(judges) == 1 else None
    rnames = ", ".join(readers[i][0]["model_id"] for i in sorted(plan)) or readers[0][0]["model_id"]
    out = _P(args.out) if args.out else folders[0] / "agree" / f"agreement-{AG.slug(readers[0][0]['model_id'])}.json"
    AG.write_summary(out, summary, rnames, jname, folders)
    print(AG.render_table(summary, rnames, jname))
    print(f"agreement table: {out}")
    return 0


def _guard_45_judge(args, cfg, prof):
    """4.5 profiles grade with one LOCAL judge (owner 2026-11-21): a hosted --judge / profile judge is refused with a
    plain message, and hosted judge candidates leave the config this command uses (no fallback can pick one)."""
    from . import judge45 as _J
    try:
        _J.guard_judge(cfg, getattr(args, "judge", None), prof)
    except _J.HostedGrader as e:
        raise SystemExit(str(e))
    cfg = _J.local_only_cfg(cfg, prof)
    if _J.is_45(prof) and isinstance(getattr(args, "judge", None), dict):
        # the profile names the judge by model; the config's judge candidate for that model says WHICH copy (devices,
        # parallel slots, instance name): keep them, as a `--judge <candidate name>` would (rig test 2026-11-21: the
        # profile dict alone loaded the judge on the default cards)
        args.judge = _J.with_candidate_copy(cfg, args.judge)
    return cfg


def _allow_self_judge(args) -> bool:
    """``--allow-self-judge``, or a 4.5 profile, whose judge grades its own rows (amendment r; the board notes it)."""
    from . import judge45 as _J
    return bool(getattr(args, "allow_self_judge", False)) or _J.self_grades(getattr(args, "_judge_prof", None))


def _adult_plan_of(args, cfg, prof, labels) -> dict:
    """{label: judge45.plan} of the NSFW+ units the run's judge still has to read (4.5 profiles with NSFW+ on;
    a `judge --cases/--part` re-judge leaves the adult page alone)."""
    from . import judge45 as _J
    from . import adult_guard as _AG
    if (not _J.is_45(prof) or not _AG.args_wants(args)
            or (getattr(args, "cmd", None) == "judge" and (getattr(args, "cases", None) or getattr(args, "part", None)))):
        return {}
    jid = _judge_model_id(args, cfg) or _J.judge_id(prof)
    out = {}
    for label in labels:
        if not (results_dir() / f"transcripts_{label}.jsonl").is_file():
            continue
        try:
            out[label] = _J.plan(results_dir(), label, prof, jid)
        except Exception as e:  # noqa: BLE001 -- a broken adult page never blocks the standard judge
            log.error("NSFW+ judge plan for %s failed: %s", label, e)
    return out


def _adult_judge_phase(args, cfg, prof, labels, inst=None) -> dict:
    """Class every unread NSFW+ unit and opinion-flag unit of ``labels`` with the run's judge (loaded here under the
    judge lease the caller holds; the same judge that graded the rows), one vote each, into ``judge45/``."""
    from . import judge45 as _J
    from .judge import _load_judge
    todo = {l: p for l, p in _adult_plan_of(args, cfg, prof, labels).items() if p["calls_needed"]}
    if not todo:
        return {}
    by_name = {m["name"]: m for m in cfg.get("models") or []}
    # 4.5: a judge under test grades its own adult page (amendment r); the report notes it
    benched = set() if _J.self_grades(prof) else {by_name[l]["model_id"] for l in labels if l in by_name}
    jc = _load_judge(cfg, benched, rows=sum(p["calls_needed"] for p in todo.values()), samples=1,
                     override=getattr(args, "judge", None), **({"instance": inst} if inst else {}))
    reader = _J.judge_reader(jc.model_id, provider=jc.provider.name, provider_type=jc.provider.type)
    chat = _J.client_chat(jc)
    out = {}
    for label in todo:
        if stopflag.ABORT.is_set():
            break
        try:
            out[label] = _J.judge_model(results_dir(), label, prof, reader, chat,
                                        width=max(1, int(getattr(jc, "concurrency", 1) or 1)))
        except _J.HostedGrader:
            raise
        except Exception as e:  # noqa: BLE001 -- one model's adult page never strands the others
            log.error("NSFW+ judge for %s failed: %s (its units stay not judged yet)", label, e)
            continue
        st = out[label]
        print(f"NSFW+ judge {jc.model_id} on {label}: {st['classed']} unit(s) classed, {st['flagged']} opinion-flag "
              f"unit(s) read, {st['pending_class'] + st['pending_flags']} not judged yet (unparsable), "
              f"{st['calls']} call(s)")
    return out


def cmd_judge_check(args, cfg):
    """`judge-check`: the run's judge on the constructed NSFW+ classing set (61 items) and the constructed opinion-flag
    set, one vote each, scored under the same rules as the adult page. The result is stored in
    ``<results>/judge45/check.json`` and printed; it never gates a score (INTENT-45: "the result is printed").
    ``--show`` prints the stored result only (no call)."""
    from . import judge45 as _J
    from .judge import JudgeLeaseUnavailable, _load_judge, acquire_judge_lease
    cfg, _cases, prof = _apply_profile_arg(args, cfg)
    cfg = _guard_45_judge(args, cfg, prof)
    jid = _judge_model_id(args, cfg) or _J.judge_id(prof)

    def show(chk):
        n = _J.check_numbers(chk)
        if not n:
            print(f"judge check: no stored result for {jid} (run `crucibleforge judge-check`)")
            return 1
        br = ", ".join(f"{b} {r} of {k}" for b, (r, k) in sorted((n["branches"] or {}).items()))
        fl = ", ".join(f"{f} {r} of {k}" for f, (r, k) in sorted((n["flags"] or {}).items()))
        h = n["hard"] or [0, 0]
        print(f"judge {n['judge']} classed {n['classing_right']} of {n['classing_n']} test items; {h[0]} of {h[1]} "
              f"hard items ({br}). Opinion flags: {fl}. Printed only; it never withholds a score.")
        return 0
    if getattr(args, "show", False):
        return show(_J.load_check(results_dir(), jid))
    force_evict = getattr(args, "force_evict", False) or _judge_policy_force(cfg)
    guard = _ProviderGuard(cfg, [], force_evict=force_evict, judge=getattr(args, "judge", None))
    try:
        try:
            acquire_judge_lease(cfg, force_evict=force_evict, override=getattr(args, "judge", None))
        except JudgeLeaseUnavailable as e:
            print(f"judge lease: {e}", file=sys.stderr)
            return 4
        jc = _load_judge(cfg, set(), rows=110, samples=1, override=getattr(args, "judge", None))
        reader = _J.judge_reader(jc.model_id, provider=jc.provider.name, provider_type=jc.provider.type)
        _J.run_check(results_dir(), reader, _J.client_chat(jc), prof,
                     width=max(1, int(getattr(jc, "concurrency", 1) or 1)))
        jid = jc.model_id
    except _J.HostedGrader as e:
        raise SystemExit(str(e))
    finally:
        guard.restore()
    return show(_J.load_check(results_dir(), jid))


def cmd_judge(args, cfg):
    cfg, prof_cases, prof = _apply_profile_arg(args, cfg)
    cfg = _guard_45_judge(args, cfg, prof)
    args._judge_prof = prof
    entries = resolve_models(cfg, args.models)
    labels = [e["name"] for e in entries]
    shard = _shard_of(args)
    args.steal_labels = None
    forced = bool(getattr(args, "force", False))
    if getattr(args, "pool", False):
        # every judge process gets the SAME model list and takes models from
        # it one at a time (label locks): the fast copy simply takes more
        if forced:
            raise SystemExit("--pool with --force would re-judge a model another process "
                             "already re-judged: use --shard (no stealing) for a forced re-judge")
        if shard:
            raise SystemExit("--pool and --shard are alternatives")
        args.steal_labels, labels = list(labels), []
        log.info("judge pool: %d model(s) shared with the other judge processes",
                 len(args.steal_labels))
    elif shard:
        from .shard import load_plan, shard_labels
        plan = load_plan(args.shard_plan) if getattr(args, "shard_plan", None) else None
        everyone = list(labels)
        labels = shard_labels(labels, shard, plan)
        if not getattr(args, "no_steal", False) and not forced:
            args.steal_labels = [l for l in everyone if l not in labels]
        elif forced:
            log.info("--force: no taking over of other shards' models (their rows never "
                     "stop being 'pending' under --force)")
        log.info("judge shard %d/%d (%s): %d label(s): %s", shard[0], shard[1],
                 "plan " + str(args.shard_plan) if plan else "hash", len(labels),
                 ", ".join(labels))
    if (shard or getattr(args, "pool", False)) and not labels and not args.steal_labels:
        print("nothing to judge (no label in this shard)")
        return 0
    inst_keys = judge_instance_arg(args, cfg)
    win = (inst_keys or {}).get("instance_window")
    if win:
        from .instances import window_state
        inside, end = window_state(win)
        if not inside:
            log.warning("judge instance %s: outside its window %s — not started (its rows "
                        "stay pending; another shard takes them over)",
                        inst_keys.get("instance_name") or inst_keys.get("serve_model_id"), win)
            print(f"outside the instance window {win} — nothing started")
            return 0
        cut = end.timestamp()
        if not getattr(args, "judge_deadline", None) or cut < args.judge_deadline:
            args.judge_deadline = cut
        log.info("judge instance window %s: stops handing out rows at %s", win, end)
    samples = getattr(args, "samples", None)
    if getattr(args, "smoke", False):
        samples = 1  # keep smoke fast regardless of config
    # Reserve every StudioForge GPU for the judge BEFORE the per-model load
    # happens. The provider's ``lease: true`` path would do this inside
    # ``_lease_load`` — but only after the runner is partway through a
    # multi-minute 122B download, when a 507 has no context to act on.
    # Acquiring the lease up-front (maintainer: "block out all the gpus
    # when running the judge") lets the rig either grant the lease and plan
    # around the named model, or refuse fast with a message naming the
    # holder, instead of timing out 178 rows in.
    from .judge import judge_gate
    retry_failed = bool(getattr(args, "retry_failed", False))
    only = _judge_cases(args, prof_cases)
    n_pending, restamp = judge_gate(cfg, list(labels) + list(args.steal_labels or []),
                                    override=getattr(args, "judge", None),
                                    allow_fallback=getattr(args, "judge_fallback", None),
                                    force=bool(getattr(args, "force", False)),
                                    retry_failed=retry_failed, cases=only)
    if restamp:
        print(restamp)     # 2026-11-15: verdicts this judge would only stamp the same way again
    adult = _adult_plan_of(args, cfg, prof, list(labels) + list(args.steal_labels or []))
    n_adult = sum(p["calls_needed"] for p in adult.values())
    if n_adult:
        print(f"NSFW+ judge: {n_adult} unit(s) to read on {len([p for p in adult.values() if p['calls_needed']])} "
              "model(s)")
    if n_pending == 0 and n_adult == 0:
        # Judge-free profile (e.g. `coding`) or already judged: do not touch
        # the rig at all — a lease here would evict residents and vacate
        # ComfyUI for a phase that has nothing to do.
        log.info("nothing to judge for %s — rig untouched", labels)
        print("nothing to judge")
        return 0
    remote = _judge_is_remote(args, cfg)
    if sharded_judge(args) and not remote:
        inst = inst_keys or {}
        if not inst.get("instance_devices"):
            raise SystemExit("a sharded rig judge needs --judge-devices (its own cards; two "
                             "shards must never share one)")
        from .shard import DeviceBusy, DeviceLocks
        dl = DeviceLocks(results_dir(), inst["instance_devices"])
        try:
            dl.acquire(wait_s=0.0)
        except DeviceBusy as e:
            print(f"judge shard: {e}", file=sys.stderr)
            return 4
        try:
            with rig_phase(needed=True, what="judge shard", shared=True):
                return _cmd_judge_locked(args, cfg, labels, samples, retry_failed, only,
                                         inst_keys)
        finally:
            dl.release()
    with rig_phase(needed=not remote, what="judge phase"):
        return _cmd_judge_locked(args, cfg, labels, samples, retry_failed, only)


def cmd_judge_shards(args, cfg):
    """Plan (``--out``) or audit (``--verify``) a sharded judge phase. Reads
    the transcripts only; never touches the rig."""
    from .config import transcript_path
    from . import shard as sh
    if getattr(args, "verify", None):
        plan = sh.load_plan(args.verify)
        res = sh.verify(plan, transcript_path)
        for label, r in sorted(res["labels"].items()):
            flag = "" if not (r["duplicates"] or r["torn"] or r["foreign"]) else "  <-- CHECK"
            print(f"  {label:44s} ledger {r['ledger']:4d}  judged {r['judged']:4d}  "
                  f"pending {r['unjudged']:4d}  dup {r['duplicates']}  foreign {r['foreign']}  "
                  f"torn {r['torn']}{flag}")
        print(f"total: judged {res['judged']}  still pending {res['unjudged']}  "
              f"duplicates {res['duplicates']}  foreign {res['foreign']}  torn {res['torn']}  "
              f"-> {'OK' if res['ok'] else 'PROBLEM'}")
        return 0 if res["ok"] else 1
    from .judge import _pending_rows, judge_input_weight, judge_policy, judge_policy_config
    cfg, _, _ = _apply_profile_arg(args, cfg)
    labels = [e["name"] for e in resolve_models(cfg, args.models)]
    pending = {l: _pending_rows(l, bool(args.force), bool(args.retry_failed)) for l in labels}
    pcfg = judge_policy_config(cfg)

    def weigh(r):
        # judge time ~ samples drawn x (fixed thinking/verdict cost + input
        # read); 8000 chars of input ~ one extra verdict's worth (measured
        # order of magnitude, 4.1 pass: 1.3 min/row, longest first)
        try:
            n = judge_policy(r, pcfg)["samples"]
        except Exception:  # noqa: BLE001 — a weight never fails the plan
            n = 1
        return round(n * (1.0 + judge_input_weight(r) / 8000.0), 3)

    plan = sh.make_plan(labels, max(1, int(args.shards)), pending, weigh, transcript_path)
    for s in plan["shards"]:
        print(f"shard {s['index']}/{plan['n']}: {s['rows']} rows, weight {s['weight']:.1f}, "
              f"{len(s['labels'])} model(s): {', '.join(s['labels'])}")
    if args.out:
        sh.write_plan(args.out, plan)
        print(f"plan written: {args.out}")
    return 0


def _judge_cases(args, prof_cases: list[dict]) -> list[str] | None:
    """The case ids a ``judge`` run is restricted to (None = every case).

    ``judge --cases NC3-palm-house`` re-judges ONE case and leaves every
    other case's rows pending — the re-judge after a grading-only case fix
    must not spend a rig-hour re-scoring 170 unrelated rows.

    Only the ``judge`` subcommand filters: ``all``/``run`` already use
    ``--cases`` to pick what to GENERATE, and their judge phase must keep
    judging everything that is pending (a run of one case must not leave the
    other cases' rows unjudged). So the filter applies when the command IS
    ``judge`` and is ignored when ``all``/``run`` call the judge phase.
    """
    if getattr(args, "cmd", None) != "judge":
        return None
    only = _parse_list(getattr(args, "cases", None))
    if only:
        known = {c["id"] for c in prof_cases}
        missing = sorted(set(only) - known)
        if missing:
            raise SystemExit(f"unknown case id(s): {missing}")
    if getattr(args, "part", None):
        # re-judge path (no generation): every stored row of one sub-score,
        # standard and pack questions, judged again from its transcript
        args.force = True
        only = sorted(set(only or []) | set(part_case_ids(args.part,
                                                          getattr(args, "profile", None))))
    return only or None


def part_case_ids(part: str, profile: str | None = None, cfg: dict | None = None) -> list[str]:
    """The case ids of one sub-score ("<category>/<part>"): its declared
    standard ``cases`` (else every profile case of its components'
    categories) plus every question of its ladder packs (staged ones only
    with --include-staged)."""
    from . import ladder
    from . import tiers as _tiers
    from .profiles import DEFAULT_PROFILE, load_profile
    from .report import _component_categories
    prof = load_profile(profile or DEFAULT_PROFILE, cfg)
    cat, _, pk = part.partition("/")
    for d in _tiers.scores(prof):
        if d["key"] != cat:
            continue
        for p in d.get("parts") or []:
            if p["key"] != pk:
                continue
            if p.get("cases"):
                ids = set(p["cases"])
            else:
                cats = set().union(*(_component_categories(k) for k in p["scoring"] or {}))
                ids = {c for k, cids in (prof.get("cases") or {}).items() if k in cats
                       for c in cids or []}
            for s in ladder.discover(prof).get(part) or []:
                ids |= set(s["cases"])
            return sorted(ids)
    raise SystemExit(f"unknown sub-score {part!r} (known: {', '.join(ladder.part_keys(prof))})")


def _judge_is_remote(args, cfg) -> bool:
    a = copy.copy(args)
    a.cmd = "judge"
    return _remote_judge_only(a, cfg)


def _vacate_host(args, cfg) -> str:
    h = (getattr(args, "vacate_host", None)
         or ((cfg.get("instance_vacate") or {}).get("host"))
         or os.environ.get("CRUCIBLEFORGE_VACATE_HOST"))
    if not h:
        raise SystemExit("a vacating judge instance needs an address the rig can reach: "
                         "--vacate-host, models.yaml instance_vacate.host or "
                         "$CRUCIBLEFORGE_VACATE_HOST (this box's tailnet address)")
    return str(h)


def _cmd_judge_locked(args, cfg, labels, samples, retry_failed, cases=None, inst=None):
    from .judge import JudgeLeaseUnavailable, acquire_judge_lease, run_judge, vacate_instance
    force_evict = getattr(args, "force_evict", False) or _judge_policy_force(cfg)
    if inst is None:
        inst = judge_instance_arg(args, cfg)
    vac = None
    if inst and inst.get("instance_vacate"):
        from .vacate import VacateListener
        stop_ev = getattr(args, "judge_stop", None) or threading.Event()
        args.judge_stop = stop_ev
        port = int(getattr(args, "vacate_port", None)
                   or (cfg.get("instance_vacate") or {}).get("port") or 0)
        vac = VacateListener(_vacate_host(args, cfg), port,
                             on_vacate=lambda body: vacate_instance(cfg, args.judge_stop,
                                                                    getattr(args, "judge", None),
                                                                    inst, body)).start()
    # The guard's resident snapshot MUST be taken before the judge lease:
    # with bench-first the lease itself evicts a chat-tier resident, and a
    # snapshot taken afterwards is empty — so nothing was restored and the
    # rig sat empty after the judge phase (run 1, 2026-09-08 08:59).
    if inst is not None:
        # a judge copy never evicts: its lease names its own cards only
        force_evict = False
    guard = _ProviderGuard(cfg, [], force_evict=force_evict,
                           judge=getattr(args, "judge", None))
    if inst is not None or sharded_judge(args):
        for p in guard.provs.values():
            p.own_unload_only = True
    try:
        extra = {k: v for k, v in (("instance", inst),
                                   ("vacate", (vac.url, vac.token) if vac else None))
                 if v is not None}
        lease = acquire_judge_lease(cfg, force_evict=force_evict,
                                    override=getattr(args, "judge", None), **extra)
        if vac is not None and lease:
            vac.lease_id = lease.get("_lease_id")
    except JudgeLeaseUnavailable as e:
        holder = f" (current holder: {e.holder})" if e.holder else ""
        print(f"judge lease: {e}{holder}", file=sys.stderr)
        log.error("judge aborted — could not reserve all GPUs: %s", e)
        # The lease was refused before any load, so restore is a no-op on
        # the residents; it still releases anything half-taken. Non-zero
        # keeps the queue script from stamping DONE over a batch that did
        # not run.
        guard.restore()
        if vac is not None:
            vac.close()
        return 4
    try:
        # stop: bench-all's window edge (an Event its timer sets); a SIGTERM
        # (stopflag.ABORT) stops the phase the same way inside run_judge —
        # in-flight verdicts finish, queued rows stay pending, no reloads
        result = run_judge(cfg, labels, force=args.force, samples=samples,
                           judge_override=getattr(args, "judge", None),
                           allow_fallback=getattr(args, "judge_fallback", None),
                           allow_self_judge=_allow_self_judge(args),
                           retry_failed=retry_failed,
                           stop=getattr(args, "judge_stop", None),
                           deadline=getattr(args, "judge_deadline", None),
                           cases=cases,
                           **{k: v for k, v in (("instance", inst),
                                                ("steal", getattr(args, "steal_labels", None)))
                              if v is not None})
        # one local judge classes the adult page (owner 2026-11-21): the same judge, under the same lease
        if not (vac is not None and vac.event.is_set()) and not result.get("stopped"):
            prof = getattr(args, "_judge_prof", None)
            if prof is not None:
                _adult_judge_phase(args, cfg, prof, list(labels) + list(result.get("stolen_labels") or []), inst)
    except Exception as e:
        # the board names the reason next to the unjudged rows
        _stamp_judge_error(labels, str(e))
        raise
    finally:
        guard.restore()
        if vac is not None:
            vac.close()
    # F13: a label another judge process holds was NOT judged here: it is not
    # stamped `judged`, and the command says so with a non-zero exit
    held = list(result.get("skipped_labels") or [])
    _stamp_judged([l for l in labels if l not in held] + list(result.get("stolen_labels") or []))
    if held:
        print(f"judge: {len(held)} requested label(s) are held by another judge process and "
              f"were NOT judged (rows stay pending): {', '.join(held)}", file=sys.stderr)
    if vac is not None and vac.event.is_set():
        print(f"judge instance VACATED (the rig gave its cards to a better class): "
              f"{result.get('left', 0)} row(s) left pending for the other shard")
        return 0
    print(f"judged {result['judged']} rows "
          f"({result['failed']} unparsable judge verdicts, "
          f"{result.get('empty', 0)} empty generations, "
          f"{result.get('errored', 0)} errored, samples={result.get('samples')}) "
          f"with {result.get('judge')}")
    if result.get("stopped"):
        print(f"judge phase stopped early: {result.get('left', 0)} row(s) left pending "
              f"(judged by the next run)")
    if result.get("errored"):
        return 1
    return 5 if held else 0


def _write_meta_atomic(p: Path, meta: dict) -> None:
    """Whole-file replace (judge shards may stamp the same meta file one
    after the other: never a torn JSON, last stamp wins)."""
    tmp = p.with_name(f".{p.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    os.replace(tmp, p)


def _stamp_judged(labels: list[str]) -> None:
    """meta ``judged`` = when the judge phase for these models ended (the
    run-report's per-model minutes run from ``started`` to here)."""
    from .runner import _now
    for label in labels:
        p = results_dir() / f"meta_{label}.json"
        try:
            meta = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        meta["judged"] = _now()
        meta.pop("judge_error", None)
        meta.pop("no_judge", None)
        _write_meta_atomic(p, meta)


def _stamp_no_judge(labels: list[str], on: bool) -> None:
    """meta ``no_judge`` = this model was run with ``--no-judge`` (its judged
    facts are "unjudged" on the board); cleared when a judge phase completes."""
    for label in labels:
        p = results_dir() / f"meta_{label}.json"
        try:
            meta = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if on:
            meta["no_judge"] = True
        else:
            meta.pop("no_judge", None)
        _write_meta_atomic(p, meta)


def _stamp_judge_error(labels: list[str], error: str) -> None:
    """meta ``judge_error`` = why the last judge phase for these models
    aborted (cleared by the next one that completes)."""
    for label in labels:
        p = results_dir() / f"meta_{label}.json"
        try:
            meta = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        meta["judge_error"] = error[:500]
        _write_meta_atomic(p, meta)


def disclosure_problems(cfg: dict | None, labels: list[str]) -> dict[str, list[str]]:
    """{label: [case ids]} of the board rows of HOSTED models that do not record the reasoning effort they ran
    at (P2, D11: such a row is never published). Rig models are checked by settings_gate instead."""
    from . import disclosure, report
    from .providers import provider_for
    out: dict[str, list[str]] = {}
    by = {m["name"]: m for m in (cfg or {}).get("models") or []}
    for label in labels:
        e = by.get(label)
        if e is None:
            continue
        try:
            ptype = provider_for(cfg, e).type
        except Exception:  # noqa: BLE001 — an unresolvable provider: treat as hosted (fail closed)
            ptype = None
        bad = [str(r.get("case_id")) for r in report.board_filter(label, cfg) or []
               if disclosure.publish_refusal(r, ptype)]
        if bad:
            out[label] = sorted(set(bad))
    return out


def check_stale(args, cfg) -> int:
    """``report --check-stale`` (P3): per model, the board rows whose grader stamp is missing or no longer matches
    the code and data that grade them (grader_stamp.row_is_stale). Exit 1 when any scored row is stale."""
    from . import grader_stamp, report
    labels = ([x for x in str(args.models).split(",") if x] if getattr(args, "models", None)
              else [m["name"] for m in (cfg or {}).get("models") or []])
    any_stale = False
    for label in labels:
        stale = grader_stamp.stale_rows(report.board_filter(label, cfg) or [])
        any_stale = any_stale or bool(stale)
        print(f"{label}: {len(stale)} stale row(s)")
        for r, why in stale[:50]:
            print(f"  {r.get('case_id')} r{r.get('repeat') or 1}: {why}")
        if len(stale) > 50:
            print(f"  ... {len(stale) - 50} more")
    return 1 if any_stale else 0


def cmd_report(args, cfg, full_board: bool = False):
    """Rebuild the board. Prints only the scorecard and where the files are
    (the full report is in report.md / failures.md / report.html).

    ``full_board`` (used at the end of ``all``): the shared board always lists
    EVERY benchmarked model, not just the one this run benched — otherwise each
    run overwrote results/report.md with a one-row board (2026-09-24)."""
    from . import gen as _gen
    was = bool(_gen._MAT_CACHE_ON[0])
    _gen.enable_materialize_cache()      # a board render asks for each generated case ~5 times (see gen._MAT_CACHE)
    try:
        return _cmd_report(args, cfg, full_board)
    finally:
        # the cache is for this render only: left on, a later materialise in the same process (the run that follows
        # a board, or the next test on the same worker) read a cached view of an older case
        _gen.enable_materialize_cache(was)


def _cmd_report(args, cfg, full_board: bool = False):
    from .report import (STAGED_CC_KEY, PrivateLeak, failures_md_path, generate, public_dir,
                         report_html_path, report_md_path, scorecard_section)
    if getattr(args, "check_stale", False):
        return check_stale(args, cfg)
    if getattr(args, "staged_claude_code", False):
        if getattr(args, "public", False):
            # claude_code rows never leave the box: the staged opt-in and a public board exclude each other
            print("refused: --staged-claude-code is for a staged board only; it cannot be combined "
                  "with --public", file=sys.stderr)
            return 2
        cfg = {**(cfg or {}), STAGED_CC_KEY: True}
    if getattr(args, "public", False):
        from .report import doc_placeholders
        left = [] if getattr(args, "allow_placeholders", False) else doc_placeholders()
        if left:
            print("refused: the docs still carry placeholders that a launch render must not ship "
                  "(--allow-placeholders to render anyway):", file=sys.stderr)
            for f, t in left:
                print(f"  {f}: {t}", file=sys.stderr)
            return 5
        from .adult_guard import ARCHIVE_PROFILES
        if (getattr(args, "profile", None) or "bench45") not in ARCHIVE_PROFILES:
            # P2 (D11): a hosted row that does not record its reasoning effort is never published
            labels = ([x for x in str(args.models).split(",") if x] if args.models and not full_board
                      else [m["name"] for m in (cfg or {}).get("models") or []])
            bad = disclosure_problems(cfg, labels)
            if bad:
                for label, ids in bad.items():
                    print(f"refused: {label}: {len(ids)} hosted row(s) without a recorded reasoning effort "
                          f"({', '.join(ids[:8])}{' ...' if len(ids) > 8 else ''}); re-run them",
                          file=sys.stderr)
                return 4
        try:
            md = generate(None if full_board else args.models, write=True, public=True,
                          out_dir=getattr(args, "out", None), cfg=cfg or None)
        except PrivateLeak as e:
            print(f"refused: {e}", file=sys.stderr)
            return 3
        print(scorecard_section(md))
        print(f"wrote the PUBLIC board (private categories excluded) to "
              f"{getattr(args, 'out', None) or public_dir()}")
        return 0
    md = generate(None if full_board else args.models, write=True, cfg=cfg or None)
    print(scorecard_section(md))
    print(f"wrote {report_md_path()}")
    print(f"      {failures_md_path()}")
    print(f"      {report_html_path()}")
    return 0


def cmd_compare(args, cfg):
    """`crucibleforge compare A B`: is A better than B? The paired bootstrap
    difference of the headline S (headline.py) per score and overall."""
    from .report import compare, render_compare
    try:
        res = compare(args.a, args.b, cfg or None)
    except ValueError as e:
        print(f"compare: {e}", file=sys.stderr)
        return 2
    print(render_compare(res))
    return 0


def cmd_calibrate(args, cfg):
    """`crucibleforge calibrate`: too-easy sub-scores (reference model) and
    sub-scores some model maxed (needs a new pack). Reads the board only."""
    from .report import calibration, render_calibration
    try:
        res = calibration(cfg or None, getattr(args, "reference", None))
    except ValueError as e:
        print(f"calibrate: {e}", file=sys.stderr)
        return 2
    print(render_calibration(res))
    return 0


def _pack_list(args, cfg, prof) -> int:
    """`crucibleforge pack list [--json] [--no-board]`: the public optional packs per
    sub-score, and which board models maxed each part (docs/PACKS.md)."""
    from . import ladder
    maxed = None
    if not getattr(args, "no_board", False):
        try:
            from .report import calibration, maxed_from_report
            # the last written report.json already holds every model's scorecard; recomputing all of
            # them (calibration) takes minutes on a full board, so that is the --recompute fallback
            maxed = None if getattr(args, "recompute", False) else maxed_from_report()
            if maxed is None:
                maxed = {r["key"]: r["maxed_by"] for r in calibration(cfg or None)["rows"]}
        except Exception as e:  # noqa: BLE001 — no readable board: list the packs anyway
            log.warning("pack list: board not read (%s)", e)
    ov = ladder.pack_overview(prof, maxed)
    print(json.dumps(ov, indent=2) if args.json else ladder.render_pack_overview(ov))
    return 0


def cmd_pack(args, cfg):
    """`crucibleforge pack new <category/part>` scaffolds the next ladder pack
    (draft); `crucibleforge pack verify <file>` checks one (docs/CUSTOM-TESTS.md)."""
    from pathlib import Path as _P
    from . import ladder
    from .config import ConfigError as _CE
    from .profiles import active_name, load_profile
    prof = load_profile(getattr(args, "profile", None) or active_name(), cfg or None)
    if args.action == "list":
        return _pack_list(args, cfg, prof)
    try:
        if args.action == "new":
            path = ladder.new_pack(prof, args.subscore, count=args.count,
                                   category=args.category, grading=args.grading,
                                   level=args.level)
            print(f"wrote {path} (draft, {args.count} question stub(s), 1 point in total)")
            print("\n".join(ladder._GRADING_HELP))
            return 0
        problems, notes = ladder.verify_pack(_P(args.file), prof,
                                             allow=getattr(args, "allow", None))
    except _CE as e:
        print(f"pack: {e}", file=sys.stderr)
        return 2
    for n in notes:
        print(f"note: {n}")
    for p in problems:
        print(f"FAIL: {p}")
    print("ok" if not problems else f"{len(problems)} problem(s)")
    return 1 if problems else 0


def cmd_bench_all(args, cfg):
    """The whole-board refresh (crucibleforge/bench_all.py). ``--lease-mode``
    overrides the models.yaml lease policy for this process only."""
    from .bench_all import execute
    from . import providers
    mode = getattr(args, "lease_mode", None)
    if not mode:
        return execute(args, cfg)
    providers.set_lease_mode(mode)
    log.info("bench-all: lease mode %s — %s", mode,
             "force idle residents only, never cut a busy one, priority 2" if mode == "night"
             else "no force, no render-lease vacate, one unprotected card, priority 3, no judge")
    try:
        return execute(args, cfg)
    finally:
        providers.set_lease_mode(None)


def cmd_pairwise(args, cfg):
    from .pairwise import run_pairwise, render_pairwise_md
    entries = resolve_models(cfg, args.models)
    labels = [e["name"] for e in entries]
    if len(labels) < 2:
        raise SystemExit("pairwise needs >= 2 models (--models a,b)")
    cats = _parse_list(args.categories) or ["rp", "nsfw", "story"]
    results = run_pairwise(cfg, labels, cats, judge_override=getattr(args, "judge", None))
    md = render_pairwise_md(results)
    (results_dir() / "pairwise.md").write_text(md, encoding="utf-8")
    print(md)
    print(f"\nwrote {results_dir() / 'pairwise.md'}")
    return 0


def _ladder_sections(args) -> set[str] | None:
    """``all --ladder-sections work,chat``: the score sections whose ladder sets run (None = every
    set). Replaces the run-cc.sh patch of ``ladder.discover`` (a Claude Code run, standard board only)."""
    raw = getattr(args, "ladder_sections", None)
    if not raw:
        return None
    return {s.strip() for s in str(raw).split(",") if s.strip()}


def _only_sections(sets: dict, want: set[str] | None) -> dict:
    """The ladder sets ("<section>/<part>" keys) of the named score sections."""
    return sets if want is None else {k: v for k, v in sets.items() if k.split("/", 1)[0] in want}


def _check_ladder_sections(args, cfg) -> str | None:
    """None, or why ``--ladder-sections`` names a section the profile does not score."""
    want = _ladder_sections(args)
    if want is None:
        return None
    from .profiles import DEFAULT_PROFILE, load_profile
    try:
        known = set((load_profile(getattr(args, "profile", None) or DEFAULT_PROFILE, cfg).get("scores") or {}))
    except (SystemExit, Exception) as e:  # noqa: BLE001
        return f"--ladder-sections: the profile cannot be read ({e})"
    bad = sorted(want - known)
    if bad:
        return (f"--ladder-sections: unknown score section(s) {', '.join(bad)} "
                f"(this profile scores: {', '.join(sorted(known)) or 'none'})")
    return None


def _stored_labels(args, cfg) -> list[str]:
    """``all --no-run``: the selected labels that have stored rows (the others get an error line)."""
    from .config import load_transcripts
    cfg2, _cases, _prof = _apply_profile_arg(args, cfg)
    out = []
    for e in resolve_models(cfg2, args.models):
        if load_transcripts(e["name"]):
            out.append(e["name"])
        else:
            print(f"{e['name']}: no stored rows in this results folder; left out", file=sys.stderr)
    return out


def cmd_all(args, cfg):
    """run -> judge -> ladder sets -> report. A model-level generation failure
    (not served, OOM, lease refused, transport abort) is that MODEL's
    failure: it is recorded in its meta (the board and the run-report say
    FAILED and why) and the batch's exit code stays non-zero — but the judge
    phase still runs over every model the run phase got to (a model with no
    pending rows is a no-op there), and the board is rebuilt. Only a run
    phase that never started (nothing runnable, LM Studio busy without
    --yes) skips the later phases. Before 2026-09-28 any single failure
    returned here, so one unservable model in a 31-model backfill left the
    25 good transcripts unjudged.

    Suite 3.9: the escalation round is OPT-IN (``--escalate``; it is outside
    the timed run). After the standard judge, each model's due ladder sets
    (crucibleforge/ladder.py) run, are judged, and the next due sets follow
    until none is due (:func:`_ladder_phases`); a profile without sub-scores
    keeps the legacy tier-2/3 phases. Every phase is timed (meta ``timing``;
    ``--timing`` prints the table)."""
    bad = _check_ladder_sections(args, cfg)
    if bad:
        print(bad, file=sys.stderr)
        return 2
    if getattr(args, "plan", False):
        return cmd_plan(args, cfg)
    from . import timing
    clock = timing.Clock()
    args._clock = clock
    t0 = time.monotonic()
    w0 = timing.waited_total()
    if getattr(args, "no_run", False):
        if getattr(args, "fresh", False):
            print("refusing --no-run with --fresh: --fresh archives rows and --no-run generates none",
                  file=sys.stderr)
            return 2
        ran = args.ran_labels = _stored_labels(args, cfg)
        if not ran:
            print("--no-run: none of the selected models has stored rows in this results folder; "
                  "nothing to judge or report", file=sys.stderr)
            return 2
        rc = 0
        if len(ran) < len(resolve_models(cfg, args.models)):
            args = copy.copy(args)
            args.models = ",".join(ran)       # the judge and the report cover the labels that have rows
    else:
        rc = cmd_run(args, cfg)
        ran = getattr(args, "ran_labels", None)
    # the runner records each model's generation rounds in its own meta; a refused lease's wait is "waiting"
    clock.add_split("generation", time.monotonic() - t0, ran or [], timing.waited_total() - w0,
                    record_meta=False)
    if stopping():
        return TERM_EXIT      # SIGTERM: no escalation / judge / report after a stop
    if draining():
        return DRAIN_EXIT     # drained: exit at once, no escalation / judge / report
    if rc and not ran:
        return rc
    if rc:
        log.error("run phase: some models failed — judging the %d that ran: %s",
                  len(ran), ",".join(ran))
        args = copy.copy(args)
        args.models = ",".join(ran)
    # escalation round (opt-in since 3.9): re-run what ran out of budget on
    # bigger rungs before the judge, so one judge pass scores both. Only for
    # the benchmark profile (the Room column lives on its board).
    from .profiles import ARCHIVE_PROFILE, DEFAULT_PROFILE
    rc_e = 0
    if (getattr(args, "profile", None) or DEFAULT_PROFILE) == ARCHIVE_PROFILE and not getattr(args, "smoke", False):
        if getattr(args, "escalate", False):
            with clock.phase("escalation", ran or []):
                rc_e = _escalate_phase(args, cfg, ran or [])
        else:
            rc_e = _escalate_phase(args, cfg, ran or [])
    if stopping():
        return TERM_EXIT
    # WP-BENCH review fix: cmd_judge signals failure by RETURNING non-zero in
    # two cases (4 = start-of-judge lease unavailable, 1 = rows errored) —
    # neither raises, so the bare `cmd_judge(args, cfg)` this used to be
    # silently discarded both and `all` could exit 0 with the judge phase
    # never having scored a single row (a delivered, confidently-worded V2
    # "succeeded" report for a run that did not succeed). Capture it.
    rc_j = 0
    if getattr(args, "no_judge", False):
        _stamp_no_judge(ran or [], True)
        log.info("--no-judge: judge phase skipped — judged facts stay unjudged, Overall blank")
    else:
        try:
            with clock.phase("judge", ran or []):
                rc_j = cmd_judge(args, cfg)
        except Exception as e:  # noqa: BLE001 — objective results are still worth a report
            log.error("judge phase failed: %s — rendering the report without judged rows", e)
            rc_j = 1
        else:
            if rc_j:
                log.error("judge phase exited %d — rendering the report without judged rows", rc_j)
    if rc_e:
        # not the exit code: the board's own run is complete; the Room column
        # shows the jobs still waiting ("…") and the next run retries them
        log.error("escalation round exited %d — the Room column stays pending for those jobs",
                  rc_e)
    if stopping():
        return TERM_EXIT
    if draining():
        return DRAIN_EXIT     # a drain during escalation or the judge: no ladder, no board
    # F1 (first fresh reference run, 2026-11-19): the role-play sessions are judged on the ladder
    # (``ladder-judge``); `all` places the sessions of the models it ran, as the standard judge scored the long
    # scenes above. ``--no-judge`` skips it (it is the one opt-out of every judge step).
    rc_s = 0
    if not getattr(args, "no_judge", False) and _session_judge_due(args, cfg):
        j_args = copy.copy(args)
        j_args.models = ",".join(ran or [])
        for k, v in (("windows", None), ("plan", False), ("rung_check", False), ("force", False),
                     ("until", None), ("attach", False), ("client", None)):
            if not hasattr(j_args, k):
                setattr(j_args, k, v)
        try:
            with clock.phase("role-play session judge", ran or []):
                rc_s = cmd_ladder_judge(j_args, cfg)
        except Exception as e:  # noqa: BLE001 — the board is still worth rendering
            log.error("role-play session judge failed: %s — the sessions stay pending", e)
            rc_s = 1
        else:
            if rc_s:
                log.error("role-play session judge exited %d — rendering the report without every placement", rc_s)
        if stopping():
            return TERM_EXIT
        if draining():
            return DRAIN_EXIT
    if uses_ladder(args, cfg):
        rc_t = _ladder_phases(args, cfg, ran or [])
        if not (stopping() or draining()):
            rc_t = rc_t or _lockout_phase(args, cfg, ran or [])
    else:
        t1 = time.monotonic()
        rc_t = _tier_phases(args, cfg, ran or [])
        clock.add("tier phases", time.monotonic() - t1, ran or [])
    if stopping():
        return TERM_EXIT
    if draining():
        return DRAIN_EXIT     # drained in the ladder / Lockout phase: exit, no board (a later run resumes)
    with clock.phase("report", ran or []):
        rc_r = cmd_report(args, cfg, full_board=True)
    print(clock.summary_line())
    if getattr(args, "timing", False):
        print(clock.table(ran or []))
    return rc_r or rc or rc_j or rc_s or rc_t


def _session_judge_due(args, cfg) -> bool:
    """The profile judges its role-play session part on the ladder (chains ``chat/j_rpsession`` with
    ``judge_mode.kind: ladder``) and this is not a smoke run: `all` then runs the ladder judge pass for its models."""
    from . import rpladder_run as LR
    from .profiles import DEFAULT_PROFILE, load_profile
    if getattr(args, "smoke", False):
        return False
    try:
        prof = load_profile(getattr(args, "profile", None) or DEFAULT_PROFILE, cfg)
        jm = ((prof.get("chains") or {}).get(LR.PART) or {}).get("judge_mode") or {}
        return jm.get("kind") == "ladder"
    except Exception:  # noqa: BLE001 — an unreadable profile: the other phases decide
        return False


def uses_ladder(args, cfg) -> bool:
    """The default `all` path climbs ladders (crucibleforge/ladder.py) when
    the profile declares sub-scores (``scores.<cat>.parts``) or ladder set
    files exist for it; an old tiered profile keeps the tier phases."""
    from . import ladder
    from .profiles import DEFAULT_PROFILE, load_profile
    try:
        prof = load_profile(getattr(args, "profile", None) or DEFAULT_PROFILE, cfg)
        return bool(ladder.part_keys(prof) or ladder.discover(prof))
    except Exception:  # noqa: BLE001 — an unreadable profile: the legacy path decides
        return False


def tier_progress(label: str, cfg: dict, prof: dict) -> dict:
    """{score: tiers.combine entry} for ``label`` from its CURRENT board rows
    (report.board_filter): what it scored per tier, and which tiers are open."""
    from . import report
    rows = report.board_filter(label, cfg, sampler_view="current")
    st = report.tier_model_stats(label, cfg, rows, report.load_meta(label), {"old": False})
    return report.tier_scorecard(st, report.scoring_config(cfg), prof)["tiers"]


def tier_todo(label: str, cfg: dict, prof: dict, tier: int,
              progress: dict | None = None, smoke: bool = False) -> tuple[list[str], list[dict], set]:
    """(unlocked scores, their tier cases, jobs still to run) for ``label``.
    A score is unlocked by its result on the tier below (tiers.unlocked); a
    case that already has current rows is not run again (a resumed nightly)."""
    from . import report
    from . import tiers as _tiers
    from .config import repeats_for
    from .profiles import tier_cases
    progress = progress if progress is not None else tier_progress(label, cfg, prof)
    prev = {}
    for score, e in progress.items():
        below = tier - 1
        if below == 1:
            prev[score] = e.get("t1")
        else:
            prev[score] = e.get(f"t{below}") if (e.get("state") or {}).get(below) == "scored" else None
    scores = _tiers.unlocked(prof, tier, prev)
    cases = tier_cases(prof, tier, scores=scores, smoke=smoke) if scores else []
    have = done_jobs(report.board_filter(label, cfg, sampler_view="current"))
    jobs = {(None, c["id"], r) for c in cases
            for r in range(1, repeats_for(cfg, c["category"], smoke, c) + 1)
            if (c["id"], r) not in have}
    return scores, cases, jobs


def done_jobs(rows: list[dict]) -> set[tuple]:
    """(case id, repeat) of every job with a usable current row. An ERRORED
    row (grade ``error``: transport failure, rejected request) is not done
    — a resumed run retries it (EVAL-FABLE M4; bench_all.missing_cases has
    the same rule, bounded by MAX_CASE_ATTEMPTS)."""
    from .preflight import billing_error_row
    return {(r.get("case_id"), r.get("repeat")) for r in rows
            if r.get("grade") != "error" and not billing_error_row(r)}


def _tier_phases(args, cfg, labels: list[str]) -> int:
    """Suite 3.9: tier 2, then tier 3, for every model that unlocked a score
    (tiers.py). Each tier: run the unlocked items (a tier-3 round loads the
    model at ``tiers.t3.context``), escalate what ran out, judge. A failure
    here never blocks the report; the board shows the tier as pending."""
    from .profiles import ARCHIVE_PROFILE, DEFAULT_PROFILE, load_profile
    from .runner import run_models
    from . import tiers as _tiers
    name = getattr(args, "profile", None) or DEFAULT_PROFILE
    if (name != ARCHIVE_PROFILE or getattr(args, "smoke", False) or not labels
            or getattr(args, "no_tiers", False)):
        return 0
    try:
        prof = load_profile(name, cfg)
        if not _tiers.enabled(prof):
            return 0
        cfg_p, _, _ = _apply_profile_arg(args, cfg)
        entries = {e["name"]: e for e in resolve_models(cfg_p, ",".join(labels))}
    except (SystemExit, Exception) as e:  # noqa: BLE001 — the tier-1 board is still worth a report
        log.error("tier phases refused: %s", e)
        return 1
    rc = 0
    for tier in _tiers.TIERS:
        ran = []
        for label, entry in entries.items():
            try:
                scores, cases, jobs = tier_todo(label, cfg_p, prof, tier)
            except Exception as e:  # noqa: BLE001 — one model's board must not stop the rest
                log.error("tier %d: cannot score %s (%s) — tier skipped", tier, label, e)
                rc = 1
                continue
            log.info("tier %d: %s unlocked %s — %d job(s) to run", tier, label,
                     scores or "nothing", len(jobs))
            if not jobs:
                continue
            guard = _ProviderGuard(cfg_p, [entry], force_evict=getattr(args, "force_evict", False))
            try:
                summary = run_models(cfg_p, [entry], cases, jobs_by_label={label: jobs},
                                     min_ctx=_tiers.tier_context(prof, tier), record_key="tiers")
            finally:
                guard.restore()
            _print_run_summary(summary)
            rc = rc or _run_rc(summary, [label])
            ran.append(label)
        if not ran:
            continue
        rc = rc or _escalate_phase(args, cfg, ran)
        j_args = copy.copy(args)
        j_args.models = ",".join(ran)
        try:
            rc = rc or cmd_judge(j_args, cfg)
        except Exception as e:  # noqa: BLE001 — the board is still worth rendering
            log.error("tier %d judge phase failed: %s", tier, e)
            rc = 1
    return rc


#: A ladder climb inside one `all` stops after this many levels (a guard: a
#: set is due again only when its result is missing, so this is never hit
#: by a working profile).
MAX_LADDER_ROUNDS = 40


def ladder_todo(label: str, cfg: dict, prof: dict, sets: dict, unlock: float,
                progress: dict | None = None, smoke: bool = False,
                tried: set | None = None) -> tuple[list[dict], set, list[str]]:
    """(cases of every due set, jobs still to run, ["<key>#<n>", ...]) for
    ``label``. Due = ``ladder.due`` on the model's shares
    (report.ladder_progress: {"cat/part": {"std": share, "sets": {n: share}}});
    a job with a usable current row is not re-run, an errored one is
    (:func:`done_jobs`); ``tried`` = jobs this invocation already ran (never
    twice in one `all`)."""
    from . import ladder
    from . import report
    from .config import repeats_for
    if progress is None:
        progress = report.ladder_progress(label, cfg)
    cases, names = [], []
    for key, seq in sets.items():
        pr = progress.get(key) or {}
        # 4.5 bank: a routed pack opens on the model's route (pr["opened"]), never a share
        due = ladder.due(pr.get("std"), pr.get("sets") or {}, seq, unlock, opened=pr.get("opened"))
        st = ladder.states(pr.get("std"), pr.get("sets") or {}, seq, unlock, opened=pr.get("opened"))
        stop = ladder.rung_stop(prof)
        for spec in seq:
            n = spec["n"]
            rungs = None
            if spec.get("rungs"):
                # generated set: unlocked (pending or partly run) -> its next
                # rungs (adaptive stop, ladder.rungs_due)
                if st.get(n) not in ("pending", "scored"):
                    continue
                rungs = ladder.rungs_due(list(spec["rungs"]),
                                         (pr.get("rungs") or {}).get(n) or {}, stop)
            elif n not in due:
                continue
            cs = ladder.set_cases(prof, key, n, smoke=smoke, sets=sets, rungs=rungs)
            if cs and pr.get("pack_gate"):
                # routed part packs: each pack row records what its gate compared
                cs = [{**c, "pack_gate": dict(pr["pack_gate"], part=key)} for c in cs]
            if cs:
                cases += cs
                names.append(f"{key}#{n}")
    have = done_jobs(report.board_filter(label, cfg, sampler_view="current"))
    jobs = {(None, c["id"], r) for c in cases
            for r in range(1, repeats_for(cfg, c["category"], smoke, c) + 1)
            if (c["id"], r) not in have and (None, c["id"], r) not in (tried or set())}
    return cases, jobs, names


def _lockout_phase(args, cfg, labels: list[str]) -> int:
    """The NSFW Lockout column (crucibleforge/lockout.py): for every model, run the rungs of
    the profile's ``flags.lockout`` file in order, stopping at the first refusal. No file, no
    NSFW score or --no-ladder: nothing runs and the column reads "not run". A failure here
    never blocks the report."""
    from . import lockout
    from .config import load_transcripts
    from .profiles import active_name, flag_columns, load_profile
    from .runner import run_models
    if (getattr(args, "smoke", False) or not labels or getattr(args, "no_ladder", False)
            or getattr(args, "no_lockout", False)):
        return 0
    try:
        prof = load_profile(getattr(args, "profile", None) or active_name(), cfg)
        col = next((f for f in flag_columns(prof) if f["key"] == lockout.KEY and f["available"]), None)
        if col is None:
            return 0
        rungs = lockout.load(col["file"])
        cfg_p, _, _ = _apply_profile_arg(args, cfg)
        entries = {e["name"]: e for e in resolve_models(cfg_p, ",".join(labels))}
    except (SystemExit, Exception) as e:  # noqa: BLE001
        log.error("lockout phase refused: %s", e)
        return 1
    rc = 0
    for label, entry in entries.items():
        if stopping():
            return TERM_EXIT
        if draining():
            return DRAIN_EXIT

        def run_one(case, label=label, entry=entry):
            summary = run_models(cfg_p, [entry], [case], jobs_by_label={label: {(None, case["id"], 1)}},
                                 record_key="lockout")
            _print_run_summary(summary)

        try:
            # Admin N (task C): a scored ladder runs every rung (a refusal is a 0 result, not a stop)
            runner_ = lockout.run_all if col.get("scored") else lockout.run_ladder
            ev = runner_(rungs, lambda label=label: load_transcripts(label), run_one)
            log.info("lockout: %s reached rung %s of %d%s", label, ev["rung"], ev["of"],
                     f" (stopped at {ev['stopped_at']})" if ev["stopped_at"] else "")
        except Exception as e:  # noqa: BLE001 - one model's column must not stop the rest
            log.error("lockout: %s failed (%s)", label, e)
            rc = 1
    return rc


def _ladder_phases(args, cfg, labels: list[str]) -> int:
    """Suite 3.9 ladders: after the standard judge, run every model's due
    ladder sets (all sets of the lowest open level of each sub-score
    together), escalate (only with --escalate), judge, then look again —
    the next level is due only when every level below scored >= the unlock
    share — until nothing is due. A failure here never blocks the report;
    the board shows an unrun set as pending."""
    from . import timing as _timing_mod
    from . import ladder
    from . import report
    from .profiles import DEFAULT_PROFILE, load_profile
    from .runner import run_models
    name = getattr(args, "profile", None) or DEFAULT_PROFILE
    if (getattr(args, "smoke", False) or not labels
            or getattr(args, "no_tiers", False) or getattr(args, "no_ladder", False)):
        return 0
    try:
        prof = load_profile(name, cfg)
        # fix-d (N9): a profile that declares ``ladder:`` climbs it, whatever its name
        if not isinstance(prof.get("ladder"), dict):
            return 0
        sets = _only_sections(ladder.discover(prof), _ladder_sections(args))
        if not sets:
            return 0
        if not hasattr(report, "ladder_progress"):
            log.error("ladder sets exist but this build has no report.ladder_progress — "
                      "ladder phases skipped")
            return 1
        unlock = ladder.unlock_share(prof)
        cfg_p, _, _ = _apply_profile_arg(args, cfg)
        entries = {e["name"]: e for e in resolve_models(cfg_p, ",".join(labels))}
    except (SystemExit, Exception) as e:  # noqa: BLE001 — the standard board is still worth a report
        log.error("ladder phases refused: %s", e)
        return 1
    clock = getattr(args, "_clock", None)
    # bench-all (review B2): ``ladder_admit(label, entry, cases, jobs)`` says
    # whether a model's due sets fit its time window (and arms it);
    # ``ladder_after()`` disarms it; ``judge_stop`` set = the window is over
    admit = getattr(args, "ladder_admit", None)
    after = getattr(args, "ladder_after", None)
    window_over = getattr(args, "judge_stop", None)
    tried: dict[str, set] = {l: set() for l in entries}
    rc = 0
    for level in range(1, MAX_LADDER_ROUNDS + 1):
        ran = []
        t0 = time.monotonic()
        w0 = _timing_mod.waited_total()
        for label, entry in entries.items():
            if stopping():
                return TERM_EXIT
            if draining():
                # a drain (SIGUSR1 / the stop file) ends the ladder phase: nothing new starts and
                # the run exits DRAIN_EXIT (review 2026-11-15: the loop used to go round after round,
                # every run skipped, and never exited)
                return DRAIN_EXIT
            if window_over is not None and window_over.is_set():
                break
            try:
                cases, jobs, names = ladder_todo(label, cfg_p, prof, sets, unlock,
                                                 tried=tried[label])
            except Exception as e:  # noqa: BLE001 — one model's board must not stop the rest
                log.error("ladder: cannot score %s (%s) — skipped", label, e)
                rc = 1
                continue
            log.info("ladder round %d: %s — due %s, %d job(s) to run", level, label,
                     names or "nothing", len(jobs))
            if not jobs:
                continue
            if admit is not None and not admit(label, entry, cases, jobs):
                log.info("ladder round %d: %s — does not fit the time window, next run",
                         level, label)
                continue
            tried[label] |= jobs
            guard = _ProviderGuard(cfg_p, [entry], force_evict=getattr(args, "force_evict", False))
            try:
                summary = run_models(cfg_p, [entry], cases, jobs_by_label={label: jobs},
                                     record_key="ladder")
            finally:
                guard.restore()
                if after is not None:
                    after()
            _print_run_summary(summary)
            rc = rc or _run_rc(summary, [label])
            ran.append(label)
        if clock and ran:
            clock.add_split(f"ladder round {level} generation", time.monotonic() - t0, ran,
                            _timing_mod.waited_total() - w0)
        if stopping():
            return TERM_EXIT
        if draining():
            return DRAIN_EXIT              # no escalation, no judge, no next round
        if not ran:
            break
        rc = rc or _escalate_phase(args, cfg, ran)
        if getattr(args, "no_judge", False):
            continue                       # nothing judged: the next round sees only programmatic rows
        j_args = copy.copy(args)
        j_args.models = ",".join(ran)
        t1 = time.monotonic()
        try:
            rc = rc or cmd_judge(j_args, cfg)
        except Exception as e:  # noqa: BLE001 — the board is still worth rendering
            log.error("ladder round %d judge phase failed: %s", level, e)
            rc = 1
        if clock:
            clock.add(f"ladder round {level} judge", time.monotonic() - t1, ran)
    return rc


def plan_budget(cfg_p: dict, entry: dict, case: dict, ctx_len: int | None) -> str:
    """The output budget a case's rows will be sent, without calling a model:
    a fixed budget (the code bank's, a ladder set's) clamped to the window,
    else the board regime (case budget x factor within [floor, cap] — for
    every model under the one-budget rule (S8); otherwise a model the run
    has not probed yet shows both)."""
    from .runner import (BANK_CATEGORIES, BANK_WINDOW_SLACK, REGIME_DEPLOYED, _Ctx,
                         _prompt_tokens, fixed_budget, run_plan)
    d = cfg_p["defaults"]
    mt = int(case.get("max_tokens") or 0)
    fixed = fixed_budget(cfg_p, case)
    if fixed:
        msgs = ([{"role": "system", "content": case.get("system") or ""},
                 {"role": "user", "content": case.get("prompt") or ""}])
        b = int(fixed)
        if ctx_len:
            b = min(b, int(ctx_len) - _prompt_tokens(msgs) - BANK_WINDOW_SLACK)
        kind = ("bank" if case["category"] in BANK_CATEGORIES else "ladder" if case.get("ladder_budget") else "case")
        return f"{max(b, 256)} ({kind})"
    fake = type("C", (), {})()
    fake.thinking = True
    fake.think_factor = float(d.get("thinking_max_tokens_factor", 4))
    fake.think_cap = int(d.get("thinking_max_tokens_cap", 32768))
    fake.think_floor = int(d.get("thinking_max_tokens_floor") or 0)
    think = _Ctx.budget(fake, mt)
    one = d.get("regime") == REGIME_DEPLOYED and bool(run_plan(cfg_p).get("one_budget", True))
    t = entry.get("thinking", "auto")
    if one or t is True or (entry.get("extra_body") or {}).get("reasoning_format"):
        return str(think)
    if t is False:
        return str(mt)
    return f"{think} if thinking, else {mt}"


def plan_reader_line(n_units: int, graded_family: str | None, prof: dict | None) -> str:
    """The `plan` line for the NSFW+ classing: the run's judge reads each classed unit and each opinion-flag unit
    once (one local judge, owner 2026-11-21); no reader, no hosted call, nothing priced."""
    from . import judge45 as _J
    if not n_units:
        return "  NSFW+ judge: no units to read"
    jid = _J.judge_id(prof) or "the run's judge"
    note = " (same family as the graded model: the board notes it)" if _J.same_family(jid, graded_family) else ""
    return (f"  NSFW+ judge: at most {n_units} calls by {jid}, one vote per classed or opinion-flag unit "
            f"(local, free){note}")


def cmd_plan(args, cfg) -> int:
    """`crucibleforge all --plan`: what `all` would run per model — every
    standard row (case, repeats, budget) in its load round(s) (a rig model:
    round A at the context floor, round B for the wide rows; a hosted API:
    one round), the ladder sets each sub-score could add, and a wall-time
    ESTIMATE (crucibleforge/estimate.py) — computed, no model or provider
    called. The ladder lists show what WOULD run once unlocked; the unlock
    rule decides at run time from the model's scores."""
    from . import estimate as est
    from . import plan_price as _plan_price
    from . import spend as _spend
    from .config import repeats_for
    from .runner import _LOADING_TYPES, plan_rounds
    from .providers import provider_for
    cfg_p, cases, prof = _apply_profile_arg(args, cfg)
    entries = resolve_models(cfg_p, args.models)
    if not entries:
        raise SystemExit("no models selected (pass --models)")
    smoke = getattr(args, "smoke", False)
    samples = int((cfg_p.get("judge") or {}).get("samples", 3) or 3)
    out = []

    def rows_of(cs, ctx_len, entry):
        lines, n = [], 0
        for c in cs:
            r = repeats_for(cfg_p, c["category"], smoke, c)
            need = int(c.get("min_context") or 0)
            na = f"  n/a (ctx: needs {need})" if need and ctx_len and need > ctx_len else ""
            n += r
            lines.append(f"    {c['category']:<15} {c['id']:<36} x{r}  budget "
                         f"{plan_budget(cfg_p, entry, c, ctx_len)}{na}")
        return lines, n

    from .judge import judge_policy, judge_policy_config
    pcfg = judge_policy_config(cfg)

    def jobs_of(cs, rig):
        """(job seconds, judged rows weighted by the per-row sample policy
        relative to the 3-sample measurement: a samples=1 row counts 1/3)."""
        secs, judged = [], 0.0
        for c in cs:
            r = repeats_for(cfg_p, c["category"], smoke, c)
            secs += [est.job_seconds(c, rig)] * r
            if est.judged(c):
                try:
                    n = judge_policy({**c, "case_id": c["id"]}, pcfg)["samples"]
                except Exception:  # noqa: BLE001 — an estimate never fails the plan
                    n = samples
                judged += r * n / 3.0
        return secs, judged

    from .adult_guard import is_adult as _is_adult, plan_cases as _adult_plan
    all_cases = cases
    for e in entries:
        prov = provider_for(cfg_p, e)
        rig = prov.type in _LOADING_TYPES
        said: list[str] = []
        cases = _adult_plan(cfg_p, e, all_cases, say=said.append)
        perf = [c for c in cases if c["category"] == "perf"]
        t1 = [c for c in cases if c["category"] != "perf"]
        n_all = sum(repeats_for(cfg_p, c["category"], smoke, c) for c in t1)
        out.append(f"== {e['name']} ({e.get('provider')}:{e['model_id']})")
        out.append(f"  T1 — every model: {n_all} rows ({len(t1)} cases) + {len(perf)} perf probes")
        n_adult = sum(1 for c in t1 if _is_adult(c))
        out.append(f"  {said[0]}; 0 NSFW+ units" if said else f"  NSFW+: included, {n_adult} NSFW+ units")
        if not said:
            out.append(plan_reader_line(
                sum(repeats_for(cfg_p, c["category"], smoke, c) for c in t1 if _is_adult(c)),
                __import__("crucibleforge.lineage", fromlist=["family"]).family(e["name"]), prof))
        rounds = plan_rounds(cfg_p, e, cases)
        gen_total, judged_total, parts = 0.0, 0, []
        priced_rows = []
        for i, rd in enumerate(rounds):
            rcs = [c for c in rd["cases"] if c["category"] != "perf"]
            lines, n = rows_of(rcs, rd["ctx"], e)
            for c in rcs:
                priced_rows.append((c, repeats_for(cfg_p, c["category"], smoke, c),
                                    _plan_price.budget_int(plan_budget(cfg_p, e, c, rd["ctx"]))))
            slots = est.rig_slots(rd["ctx"]) if rig else max(1, int(prov.concurrency or 1))
            if rig:
                what = {"A": "standard rows", "B": "wide rows (bank / ladder budget)",
                        "all": "every row"}[rd["name"]]
                out.append(f"  round {rd['name']} — {what}: {n} rows, loaded at ctx {rd['ctx']} "
                           f"(~{slots} slots)")
            else:
                out.append(f"  one round — hosted API (nothing loaded): {n} rows, "
                           f"{slots} requests at once")
            out += lines
            secs, judged = jobs_of(rcs, rig)
            m = est.generation_min(secs, slots) + (est.RELOAD_S / 60 if rig and i else 0)
            gen_total += m
            judged_total += judged
            parts.append(f"round {rd['name']} {m:.1f} min" if rig else f"generation {m:.1f} min")
        jm = est.judge_min(judged_total, 3)
        out.append(f"  estimate (not a measurement — "
                   f"{'omega-class rig seconds per row (a verbose 27B is ~x2)' if rig else 'hosted-API seconds per row'}"
                   f", x{est.CALIBRATION} calibration, crucibleforge/estimate.py): "
                   + ", ".join(parts) + f"; judge {jm:.1f} min ({judged_total:.1f} judged-row "
                   f"equivalents at 3 samples, per-row sample policy) -> ~{gen_total + jm:.0f} min")
        # D15 (fix round 2026-11-18): the plan price per model, printed before any hosted go
        price = e.get("price") or prov.prices.get(str(e.get("model_id")))
        out.append(_plan_price.plan_line(
            _plan_price.estimate(priced_rows, price, thinks=e.get("thinking", "auto") is not False),
            hosted=not rig and _spend.is_hosted(prov.type), priced=bool(price)))
        if not rig and _spend.is_hosted(prov.type) and _spend.budget_line():
            out.append(_spend.budget_line())
        out += _plan_ladder(args, cfg_p, prof, e, rig, prov, rows_of, jobs_of, samples) \
            if uses_ladder(args, cfg) else _plan_tiers(args, cfg_p, prof, e, rows_of)
    print("\n".join(out))
    print("\n(plan only — no model, provider or judge was called; times are estimates)")
    return 0


def _plan_ladder(args, cfg_p, prof, e, rig, prov, rows_of, jobs_of, samples) -> list[str]:
    from . import estimate as est
    from . import ladder
    from .runner import load_context
    out = []
    try:
        sets = _only_sections(ladder.discover(prof), _ladder_sections(args))
    except ConfigError as ex:
        return [f"  ladder: INVALID — {ex}"]
    unlock = ladder.unlock_share(prof)
    if not sets:
        return [f"  ladder: no sets yet (cases/ladder/<category>/<part>/set-<n>.json; "
                f"a set runs when every level below scored >= {unlock:.0%})"]
    out.append(f"  ladder — a set runs when every level below it scored >= {unlock:.0%} "
               f"(all sets of one level together):")
    gen_all, judged_all = 0.0, 0
    from .adult_guard import plan_cases as _adult_plan
    for key, seq in sets.items():
        for spec in seq:
            cs = ladder.set_cases(prof, key, spec["n"], smoke=getattr(args, "smoke", False),
                                  sets=sets)
            cs = _adult_plan(cfg_p, e, cs, say=lambda _s: None)   # an adult pack only with --adult (P1)
            if not cs:
                continue
            ctx = load_context(cfg_p, e, cases=cs)
            lines, n = rows_of(cs, ctx, e)
            below = "standard" if spec.get("level", spec["n"]) == 1 else "standard + every lower level"
            budget = f", budget {spec['budget']}" if spec.get("budget") else ""
            out.append(f"  {key} set {spec['n']} (level {spec.get('level', spec['n'])}): "
                       f"{len(cs)} cases, {n} rows{budget}, loaded at ctx {ctx} — runs if "
                       f">= {unlock:.0%} on {below}")
            out += lines
            secs, judged = jobs_of(cs, rig)
            slots = est.rig_slots(ctx) if rig else max(1, int(prov.concurrency or 1))
            gen_all += est.generation_min(secs, slots) + (est.RELOAD_S / 60 if rig else 0)
            judged_all += judged
    jm = est.judge_min(judged_all, 3) if judged_all else 0.0
    out.append(f"  estimate if every set unlocks: +{gen_all:.0f} min generation, "
               f"+{jm:.0f} min judge (one load and judge pass per set, an upper bound)")
    return out


def _plan_tiers(args, cfg_p, prof, e, rows_of) -> list[str]:
    """Legacy (a tiered profile without sub-scores): tier-2/3 lists."""
    from .profiles import tier_cases
    from .runner import load_context
    from . import tiers as _tiers
    out = []
    for tier in _tiers.TIERS:
        gates = {s: _tiers.gate(prof, tier, s) for s in _tiers.SCORES}
        tc = tier_cases(prof, tier, smoke=getattr(args, "smoke", False))
        for score in _tiers.SCORES:
            sc_cases = [c for c in tc if c["tier_score"] == score]
            if not sc_cases:
                out.append(f"  T{tier} {score}: no items")
                continue
            ctx_t = load_context(cfg_p, e, min_ctx=_tiers.tier_context(prof, tier),
                                 cases=sc_cases)
            lines, n = rows_of(sc_cases, ctx_t, e)
            below = "T1" if tier == 2 else "T2"
            out.append(f"  T{tier} {score} — if {score} >= {gates[score]:g} on {below}: "
                       f"{n} rows, loaded at ctx {ctx_t}")
            out += lines
    return out


def cmd_gui(args, cfg):
    from .gui.server import serve
    return serve(cfg_path=cfg.get("_path"), host=args.host, port=args.port,
                 token=args.token, open_browser=not args.no_open)


def cmd_gui_token(args) -> int:
    """`crucibleforge gui token show|rotate` -- the GUI's only credential.

    Works from the config path alone (no models.yaml validation), so it still
    runs when the registry is broken. The data dir is the folder holding
    models.yaml; it is printed on every call."""
    from . import accounts
    from .config import find_config_path
    try:
        cfg_path = find_config_path(args.config)
    except ConfigError as e:
        print(f"config error: {e}", file=sys.stderr)
        return 2
    data = accounts.data_dir_for(cfg_path)
    print(f"data dir: {data} (models.yaml: {cfg_path})", file=sys.stderr)
    if args.token_action == "show":
        tok, source = accounts.effective_token(data)
        problem = accounts.stored_token_problem(data)
        if problem:
            print(f"warning: {problem}", file=sys.stderr)
        if not tok:
            print("no stored GUI token. Loopback (127.0.0.1) needs none; a non-loopback "
                  "bind prints a fresh one on every start. "
                  "Make a stable one with: crucibleforge gui token rotate", file=sys.stderr)
            return 1
        where = f"${accounts.ENV_TOKEN}" if source == "env" else str(accounts.token_path(data))
        print(f"token source: {where}", file=sys.stderr)
        print(tok)
        return 0
    chosen = None
    if args.token_stdin:
        chosen = sys.stdin.readline().lstrip("\ufeff").strip()
        if not chosen:
            print("--token-stdin: nothing on stdin", file=sys.stderr)
            return 2
    try:
        tok = accounts.rotate_token(data, chosen)
    except (accounts.AccountError, OSError) as e:
        print(f"cannot write the token: {e}", file=sys.stderr)
        return 1
    print(f"wrote {accounts.token_path(data)}", file=sys.stderr)
    print("restart the GUI for the new token to take effect (the old one stops working then); "
          "a running GUI keeps the token it started with.", file=sys.stderr)
    if os.environ.get(accounts.ENV_TOKEN, "").strip():
        print(f"warning: ${accounts.ENV_TOKEN} is set and takes precedence over this file; "
              "unset it (Docker: edit deploy/compose/.env) for the rotation to matter.",
              file=sys.stderr)
    print(tok)
    return 0


def cmd_quarantine(args, cfg):
    """Close one quarantine or halt record (R1). Does not publish."""
    from . import quarantine
    try:
        rec = quarantine.set_status(args.qid, args.status)
    except FileNotFoundError:
        print(f"no record {args.qid}", file=sys.stderr)
        return 1
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 2
    print(f"{rec['id']} {rec['status']}")
    return 0


def cmd_digest(args, cfg):
    """Ten-line daily digest of quarantine records (R5). Publishes nothing."""
    from . import quarantine
    lines = quarantine.digest_lines()
    print("\n".join(lines[:10]))
    return 0


def cmd_registry_check(args, cfg):
    """``crucibleforge registry-check [--profile bench45] [--lab DIR]``: SYSTEM-45 section 3 — the
    profile's points per item against the MakoBench lab registry (fails closed, exit 1)."""
    from . import registry_check
    try:
        return registry_check.main(args.profile or "bench45", args.lab, cfg or None)
    except FileNotFoundError as e:
        print(f"registry-check: {e}", file=sys.stderr)
        return 2


def cmd_scratch_init(args, cfg):
    """``crucibleforge scratch-init DIR``: the one explicit way to make a
    results directory a staged-pack scratch dir (creates DIR + ``.cf-scratch``)."""
    from .config import init_scratch
    try:
        d = init_scratch(args.dir)
    except ConfigError as e:
        print(f"scratch-init: {e}", file=sys.stderr)
        return 2
    print(f"scratch results dir ready: {d}")
    return 0


def cmd_config(args, cfg_or_none):
    from .config import find_config_path, save_config
    if args.init:
        dest = Path(args.init if isinstance(args.init, str) else "models.yaml")
        if dest.exists():
            raise SystemExit(f"{dest} already exists — refusing to overwrite")
        if not EXAMPLE_CONFIG_PATH.exists():
            # Only a clone carries models.example.yaml; a wheel/site-packages
            # install does not. Say so instead of raising FileNotFoundError.
            print(f"no template to copy: {EXAMPLE_CONFIG_PATH} is missing — "
                  "run this from a CrucibleForge checkout (git clone + uv sync)",
                  file=sys.stderr)
            return 2
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(EXAMPLE_CONFIG_PATH, dest)
        print(f"wrote {dest} — edit providers/models, then `crucibleforge status`")
        return 0
    if args.upgrade:
        cfg = load_config(args.config)
        path = Path(cfg["_path"])
        backup = path.with_suffix(".yaml.bak-v2")
        shutil.copy(path, backup)
        save_config(cfg, path)
        print(f"rewrote {path} in v3 providers shape (backup: {backup})")
        return 0
    try:
        p = find_config_path(args.config)
        print(p)
    except ConfigError as e:
        print(f"no config: {e}")
        print(f"user location: {USER_CONFIG_PATH}")
        return 1
    return 0


def cmd_import_openclaw(args, cfg):
    from .openclaw_import import import_openclaw
    from .config import save_config
    added = import_openclaw(cfg, Path(args.openclaw_json).expanduser(),
                            include_local=args.include_local)
    if not added["providers"] and not added["models"]:
        print("nothing new to import")
        return 0
    print(f"providers added: {added['providers']}")
    print(f"models added: {added['models']}")
    if args.write:
        save_config(cfg, cfg["_path"])
        print(f"wrote {cfg['_path']}")
    else:
        print("(dry run — pass --write to save into models.yaml)")
    return 0


def cmd_models(args, cfg):
    from .providers import get_provider
    if args.action == "discover":
        prov = get_provider(cfg, args.provider)
        ids = sorted(prov.list_models(refresh=True))
        if not ids:
            wall = _auth_wall_note(cfg, prov, prov.auth_wall(probe=True))
            if wall:
                print(f"{args.provider}: no model listing: {wall}")
                return 1
            print(f"{args.provider}: no model listing (endpoint has no /models or key missing)")
            return 1
        known = {m["model_id"] for m in cfg["models"] if m["provider"] == args.provider}
        for i in ids:
            print(("  * " if i in known else "    ") + i)
        print(f"\n{len(ids)} models on {args.provider} (* = in registry)")
        return 0
    if args.action == "add":
        from .config import save_config
        if any(m["name"] == args.name for m in cfg["models"]):
            raise SystemExit(f"model {args.name!r} already exists")
        entry = {"name": args.name, "provider": args.provider, "model_id": args.model_id,
                 "context_length": args.context_length}
        if args.price_in is not None or args.price_out is not None:
            entry["price"] = {"input": args.price_in or 0, "output": args.price_out or 0}
        if args.tags:
            entry["tags"] = _parse_list(args.tags)
        cfg["models"].append(entry)
        save_config(cfg, cfg["_path"])
        print(f"added {args.name} → {cfg['_path']}")
        return 0
    return 1


def cmd_cases(args, cfg):
    cases = load_cases(_parse_list(args.categories),
                       difficulties=_parse_list(args.difficulty), include_hard=True)
    if args.action == "list":
        for c in cases:
            print(f"{c['category']:11s} {c.get('difficulty', 'medium'):6s} "
                  f"{c.get('grader') or c.get('rubric') or '-':12s} {c['id']}")
        print(f"\n{len(cases)} cases")
        return 0
    if args.action == "verify":
        from .verify_cases import verify_all
        rc = verify_all(cases, verbose=args.verbose)
        from . import ladder
        from .profiles import DEFAULT_PROFILE, load_profile
        from .profiles import apply_profile, profile_problems
        prof, problems = {}, []
        try:
            prof = load_profile(getattr(args, "profile", None) or DEFAULT_PROFILE, cfg or None)
            problems = ladder.verify(prof)
            # 4.5: hard sets, id disjointness, budget block, unknown standard ids
            problems += profile_problems(prof)
            apply_profile(prof, cfg or {})
        except Exception as e:  # noqa: BLE001 — report, don't crash the case check
            problems = [f"profile/ladder: {e}"]
        # 4.5 element standard: lint by default for the hard files and for any
        # profile with a budget: block; --lint-elements forces it elsewhere.
        # Errors only for cases declaring element_standard: 45, warnings for the
        # 4.1 files, except a template element in a scored case.
        has_budget = isinstance(prof.get("budget"), dict)
        has_hard = any(c.get("hard_score") for c in cases)
        if getattr(args, "lint_elements", False) or has_budget or has_hard:
            from . import element_lint
            scored = {i for ids in (prof.get("cases") or {}).values() for i in ids or []} \
                if has_budget else set()
            n_err, n_warn, lines = element_lint.lint_cases(cases, scored_ids=scored)
            for ln in lines:
                print(ln)
            sp = element_lint.standard_problems(prof, cases, element_lint.pending_ids(prof)) \
                if (has_budget or has_hard) else []
            sp += element_lint.lint_profile(prof)
            for ln in sp:
                print(f"FAIL element standard: {ln}")
            print(f"element lint: {n_err + len(sp)} error(s), {n_warn} warning(s)")
            rc = rc or (1 if (n_err or sp) else 0)
        for p in problems:
            print(f"FAIL ladder: {p}")
        bmode = getattr(args, "budget", None) or "warn"
        bprob = []
        if bmode != "off" and isinstance(prof.get("budget"), dict):
            from . import budget as _budget
            bprob = _budget.check_problems(prof)
            for p in bprob:
                print(f"{'FAIL' if bmode == 'error' else 'WARN'} budget: {p}")
        return rc or (1 if problems else 0) or (1 if bprob and bmode == "error" else 0)
    return 1


def cmd_gen(args, cfg) -> int:
    """One generated item, no model: ``--show`` prints it, ``--check`` verifies it."""
    from . import gen
    try:
        fam = gen.get(args.family)
        if args.level not in fam.LEVELS:
            raise gen.GeneratorError(f"{args.family}: level {args.level} not in {sorted(fam.LEVELS)}")
    except gen.GeneratorError as e:
        print(f"gen: {e}", file=sys.stderr)
        return 2
    block = {"type": args.family, "level": args.level}
    if args.variant:
        block["variant"] = args.variant
    if args.key:
        block["key"] = args.key
    seed = gen.release_seed() if args.seed is None else str(args.seed)
    case = {"id": "GEN", "category": "gen", "generator": block}
    try:
        print(gen.describe_item(case, seed, show=args.show or not args.check, check=args.check), end="")
    except Exception as e:  # noqa: BLE001
        print(f"gen: {e}", file=sys.stderr)
        return 2
    if args.check:
        errs = gen.verify_case(case, seed)
        if errs:
            print("FAIL")
            for e in errs:
                print(f"  - {e}")
            return 1
        print("OK: deterministic; reference 1.0; every mutant < 1.0; empty answer 0")
    return 0


def cmd_spend(args) -> int:
    """`spend status`: read-only; exit 1 when the total is at or over the global cap."""
    from . import spend
    if args.ledger:
        os.environ["CF_SPEND_LEDGER"] = args.ledger
    if args.action == "status":
        st = spend.status(ledger=args.ledger, helper=args.helper_journal, rerun_dir=args.rerun_dir,
                          baseline=args.baseline, cap_usd=args.cap,
                          count_plan=True if args.count_plan else None)
        print(json.dumps(st, indent=1) if args.json else spend.render_status(st))
        return 1 if st["over"] else 0
    return 2


def cmd_budget(args, cfg) -> int:
    """The 4.5 time budget check (PLAN-45-v4 D1/E7, docs/BUDGET.md): per-case
    home minutes and the Core total (core_home_min) against budget.home.limit_min;
    packs reported. Reads only committed data unless --results is given."""
    from . import budget
    from .profiles import DEFAULT_PROFILE, load_profile
    try:
        prof = load_profile(args.profile or "bench45", cfg or None)
        rep = budget.run(prof, results=args.budget_results, only=args.case or None,
                         tps=args.tps, slots=args.slots, judge_s=args.judge_s,
                         require_measured=True if getattr(args, "require_measured", False) else None)
    except (budget.BudgetError, ConfigError) as e:
        print(f"budget: {e}", file=sys.stderr)
        return 2
    print(json.dumps(rep, indent=1) if args.json else budget.render(rep))
    return 0 if rep["ok"] else 1


def cmd_reverify_quotes(args, cfg) -> int:
    """Dry-run report of verdicts the quote verifier would flip now."""
    from . import judge
    from .config import results_dir
    rep = judge.reverify_quotes(results_dir())
    if getattr(args, "json", False):
        print(json.dumps(rep, indent=2))
        return 0
    print(f"rows with element verdicts scanned: {rep['rows_scanned']}")
    print(f"would flip to PASS: {rep['flip_count']}   possible (sample count unknown): "
          f"{rep['possible_count']}")
    for key in ("flips", "possible"):
        for x in rep[key]:
            print(f"  [{key}] {x['model']} {x['case_id']} {x['element']}: {x['quote']!r}")
    print("nothing was written (dry run; use `judge --force` / regrade to apply)")
    return 0


def _rejudge_hint(label: str, case_ids: set, known: set | None) -> str:
    """The ``judge`` command that re-judges a label's repaired rows. ``judge --cases`` only knows the profile's
    standard cases and refuses the whole run on a ladder-pack case id ("unknown case id(s)", 2026-11-13), so with
    one of those among the repaired rows the hint drops the filter: every pending row of the model is judged."""
    ids = sorted(case_ids)
    if known is not None and not set(ids) <= known:
        return (f"crucibleforge judge --models {label}   (no --cases: "
                f"{', '.join(i for i in ids if i not in known)} are ladder-pack cases the filter does not know; "
                f"this judges every pending row of the model)")
    return f"crucibleforge judge --models {label} --cases {','.join(ids)}"


def cmd_rejudge_reasoning(args, cfg) -> int:
    """List (default) or repair the judged rows whose answer sat in the
    reasoning channel of a FINISHED reply (finish=stop, empty content, no
    tool call) and were judged as empty — see :mod:`reasoning_repair`. No
    generation and no judge call: ``--apply`` appends repaired, PENDING rows
    and prints the ``judge`` command that re-judges exactly those cases."""
    from . import reasoning_repair as rr
    from .config import load_cases
    labels = (rr.labels_all() if args.models in (None, "", "all")
              else _parse_list(args.models))
    cases_by_id = {c["id"]: c for c in load_cases()}
    if args.apply:
        # never append beside a benchmark that is still writing the file
        try:
            import fcntl
            path = _rig_lock_path()
            if path.exists():
                with open(path, "a+", encoding="utf-8") as f:
                    try:
                        fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                        fcntl.flock(f.fileno(), fcntl.LOCK_UN)
                    except BlockingIOError:
                        print(f"a benchmark holds {path} — run --apply after it ends",
                              file=sys.stderr)
                        return 4
        except ImportError:
            pass
    rep = rr.run(labels, apply=bool(args.apply), cases_by_id=cases_by_id)
    try:  # the ids `judge --cases` accepts (the profile's standard cases; ladder-pack cases are not among them)
        known = {c["id"] for c in _apply_profile_arg(copy.copy(args), cfg)[1]}
    except (SystemExit, Exception):  # noqa: BLE001 — the hint is advice, never a reason to fail
        known = None
    if getattr(args, "json", False):
        print(json.dumps(rep, indent=2))
        return 0
    total = 0
    for label, got in rep.items():
        items = got["rows"]
        for sk in got["skipped"]:
            print(f"{label}: skip {sk['case_id']} repeat={sk['repeat']} turns {sk['turns']}: "
                  f"{sk['why']}")
        if not items:
            continue
        total += len(items)
        print(f"{label}: {len(items)} judged row(s) answered in the reasoning channel")
        for it in items:
            turns = f" turns {it['turns']}" if it["turns"] else ""
            print(f"  {it['case_id']} repeat={it['repeat']} run={it['bench_run_id']} "
                  f"[{it['kind']}{turns}] stored verdict: {it['verdict']}")
        print("  re-judge: " + _rejudge_hint(label, {it["case_id"] for it in items}, known))
    print(f"total: {total} row(s)")
    if not args.apply:
        print("nothing was written (dry run; --apply appends the repaired rows as pending)")
    return 0


def cmd_card_check(args, cfg):
    """``card-check``: the settings proposal for one model (cardcheck.py)."""
    from . import cardcheck, settings_gate
    tpl = None
    _, mid, entry = cardcheck.repo_of(cfg, args.model)
    if mid:
        try:
            from .providers import get_provider
            prov = get_provider(cfg, (entry or {}).get("provider") or next(
                n for n, p in (cfg.get("providers") or {}).items()
                if (p or {}).get("type") == "studioforge"))
            if prov.type == "studioforge":
                tpl = settings_gate.rig_templates(prov).get(mid)
        except Exception:  # noqa: BLE001 — the rig template is a bonus
            tpl = None
    res = cardcheck.check(cfg, args.model, rig_template=tpl)
    # 4.5 replay rule: what the served (else the repo's) template gives
    from . import replay_rule
    try:
        from . import report as _report
        cfg_p = _report._profile_applied(cfg)[1]
    except Exception:  # noqa: BLE001
        cfg_p = cfg
    rtpl = tpl or next((c.get("template") for c in res.get("cards") or [] if c.get("template")), None)
    rec = replay_rule.resolve(cfg_p, entry or {"model_id": mid or args.model}, rtpl, "studioforge")
    res["reasoning_replay"] = rec
    for c in res.get("cards") or []:
        c.pop("template", None)
    if getattr(args, "json", False):
        print(json.dumps(res, indent=2, ensure_ascii=False))
    else:
        print(cardcheck.format_check(res))
        print(f"reasoning replay (4.5 rule): " + (replay_rule.flow(rec) if rec else
              "undecided -- no chat template could be read/rendered"))
    return 0 if any(c["found"] for c in res["cards"]) else 1


def cmd_replay_rule(args, cfg):
    """``replay-rule LABELS [--write]``: render each model's chat template and
    show (or record) its reasoning-replay value under the 4.5 rule."""
    from . import replay_rule, settings_gate
    from .providers import get_provider, reasoning_replay_default
    try:
        from . import report as _report
        cfg_p = _report._profile_applied(cfg)[1]
    except Exception:  # noqa: BLE001
        cfg_p = cfg
    entries = resolve_models(cfg, args.models)
    if not entries:
        print("no models selected")
        return 1
    tpl_cache: dict = {}
    todo, rc = {}, 0
    for e in entries:
        pname = e.get("provider")
        pr = (cfg.get("providers") or {}).get(pname) or {}
        ptype = pr.get("type")
        tpl = None
        if ptype in replay_rule.RIG_TYPES:
            if pname not in tpl_cache:
                try:
                    tpl_cache[pname] = settings_gate.rig_templates(get_provider(cfg, pname))
                except Exception:  # noqa: BLE001
                    tpl_cache[pname] = {}
            tpl = tpl_cache[pname].get(e.get("model_id"))
        v = replay_rule.check(cfg_p, e, ptype, tpl, provider_default=reasoning_replay_default(pr))
        rec = v.record
        print(f"{v.label}: {v.status.upper()}"
              + (f" -> {replay_rule.flow(rec)}" if rec else "")
              + (f"  (was {v.was})" if v.was is not None and v.status != "ok" else "")
              + (f"\n    {v.reason}" if v.reason else ""))
        if v.status in ("new", "stale", "mismatch") and rec is not None:
            todo[v.label] = rec
        elif v.status == "unreadable":
            rc = 1
    if todo and args.write:
        done = replay_rule.write_records(cfg["_path"], todo)
        print(f"recorded {len(done)}: " + ", ".join(done))
    elif todo:
        print(f"{len(todo)} record(s) to write -- add --write")
    return rc


def cmd_conform(args, cfg):
    """Prove the harness's request protocol works for the selected models.

    The checks run through the bench's own request path (Provider.chat and
    the runner's message builders), so a PASS means the bench itself would
    talk to the model correctly -- not that a hand-rolled probe did.
    """
    from . import conformance
    entries = resolve_models(cfg, args.models or "all")
    if not entries:
        print("no models selected")
        return 1
    # the checks must see the profile the bench would send (its sampler
    # rules and its budgets), so the profile is applied first
    cfg = conformance.with_profile(cfg, getattr(args, "profile", None))
    # --allow-load puts a model on the rig: that must not happen beside a
    # running benchmark (it would evict its residents and corrupt its
    # timings), so the rig lock is taken for that case only.
    from .providers import get_provider

    def _is_rig(e):
        try:
            return get_provider(cfg, e.get("provider")).type in ("lmstudio", "studioforge")
        except Exception:  # noqa: BLE001
            return False

    need_lock = bool(getattr(args, "allow_load", False)) and any(_is_rig(e) for e in entries)
    # 2026-11-13: the recommended-settings gate + the chat template's facts
    # (settings_gate). A model that fails it is not probed: it would not be
    # benched either.
    from . import settings_gate
    templates = {}
    if any(_is_rig(e) for e in entries):
        try:
            from .providers import provider_for
            rig = next(provider_for(cfg, e) for e in entries if _is_rig(e))
            templates = settings_gate.rig_templates(rig)
        except Exception:  # noqa: BLE001 — facts are information only
            templates = {}
    verdicts = {}
    for e in entries:
        try:
            ptype = get_provider(cfg, e.get("provider")).type
        except Exception:  # noqa: BLE001
            ptype = None
        verdicts[e["name"]] = settings_gate.check(cfg, e, ptype, templates.get(e.get("model_id")))
    blocked = [e for e in entries if not verdicts[e["name"]].ok]
    runnable = [e for e in entries if verdicts[e["name"]].ok]
    for e in blocked:
        try:
            from . import quarantine
            quarantine.hold_settings(e["name"], verdicts[e["name"]].reason, e.get("model_id"))
        except Exception:  # noqa: BLE001
            log.error("quarantine record for %s failed", e.get("name"))
    with rig_phase(needed=need_lock, what="conform (--allow-load)"):
        records = conformance.conform(
            cfg, runnable, allow_load=bool(getattr(args, "allow_load", False))) if runnable else []
    if getattr(args, "json", False):
        out = [dict(r.to_dict(), settings=verdicts[r.label].to_dict())
               if r.label in verdicts else r.to_dict() for r in records]
        out += [{"label": e["name"], "model_id": e.get("model_id"), "verdict": "SETTINGS",
                 "settings": verdicts[e["name"]].to_dict()} for e in blocked]
        print(json.dumps(out, indent=2))
    else:
        for e in blocked:
            v = verdicts[e["name"]]
            print(f"\n{e['name']} ({e.get('model_id')}) — NOT CHECKED: needs_settings")
            print(f"  settings BLOCK {v.reason}")
            print("  " + v.fix.replace("\n", "\n  "))
            for line in settings_gate.format_facts(v.facts):
                print(line)
        for r in records:
            print(f"\n{r.label} ({r.model_id}) via {r.provider} [{r.provider_type}]"
                  f" — {r.verdict}")
            for c in r.checks:
                print(f"  {c.id} {c.status:4s} {c.detail}")
            v = verdicts.get(r.label)
            if v is not None:
                print(f"  settings OK   {v.basis}")
                for line in settings_gate.format_facts(v.facts):
                    print(line)
            if r.note:
                print(f"  note: {r.note}")
            print(f"  record: {conformance.record_path(r.label)}")
    # A model that ran and FAILED is the gate's signal. A model blocked for
    # settings is recorded and skipped: the batch continues (R4). The command
    # fails only when every selected model was blocked, or one that ran failed.
    if not runnable and blocked:
        return 1
    return 1 if any(r.verdict == conformance.FAIL for r in records) else 0


# ------------------------------------------------------------------- main

# ------------------------------------------------------------ --detach
# 2026-09-08: a worker's `exec` tool kills whatever it started after
# `tools.exec.timeoutSec` (default 1800 s) — the retry of run 2 died at
# exactly 30 min with one case left, no report, and its GPU lease orphaned.
# A bench must not live inside an agent's tool call at all: `--detach`
# re-launches this exact command as a transient systemd --user unit and
# returns at once, printing the unit and run id. The unit sources
# $CRUCIBLEFORGE_ENV_FILE itself when one is named (so a secret such as the
# rig PIN never appears in argv), writes the runs/<id>/{report.md,meta.json}
# contract when $CRUCIBLEFORGE_V2_RUNS_ROOT is set, and survives the agent and
# any exec timeout. Poll: `systemctl --user is-active <unit>` (and the run
# report's meta.json `status`, when enabled).
_ENV_FILE_ENV = os.environ.get("CRUCIBLEFORGE_ENV_FILE")
ENV_FILE: Path | None = Path(_ENV_FILE_ENV).expanduser() if _ENV_FILE_ENV else None


# ------------------------------------------------------------- rig lock
# R4 (2026-09-23): one benchmark at a time, enforced by the tool itself.
# Two runs on one rig share the lease holder name and the GPUs; queue
# scripts used to take this flock by hand, and anyone who forgot got two
# runs corrupting each other's timings. The CLI now takes it for every
# command that touches the rig (run / all / judge / recover), BLOCKING with a
# log line. A parent that already holds it (a queue script's `flock
# results/.rig.lock …`) is detected by walking the process ancestry for an
# open fd on the lock file, so the child does not deadlock on its own parent.
RIG_LOCK_CMDS = ("run", "all", "judge", "recover", "escalate", "bench-all", "ladder-judge", "judge-check")


# ------------------------------------------------------------- stop path
# M10 / STEP4 defect 1 (2026-10-28): a SIGTERM while the rig was unreachable
# raised SystemExit in the main thread, which then joined worker threads stuck
# in a socket connect; systemd SIGABRTed the process 46-90 s later with the
# meta unfinished and the lease held. Now the handler only SETS the stop flags
# (runner.STOP + stopflag.ABORT): the runner's pool stops waiting at once, the
# retry/backoff loops and polling sleeps give up, the meta is marked stopped,
# the `finally` blocks release the lease, and the process exits without
# joining stuck workers. A watchdog ends the process after
# ``STOP_GRACE_S`` whatever the main thread is blocked in (it marks the
# in-flight runs stopped and releases every lease first). A second SIGTERM
# raises SystemExit at once (the old behaviour).
TERM_EXIT = 128 + 15
STOP_GRACE_S = 30.0
_TERMS = {"n": 0}


def _stop_grace_s() -> float:
    try:
        return float(os.environ.get("CRUCIBLEFORGE_STOP_GRACE_S") or STOP_GRACE_S)
    except ValueError:
        return STOP_GRACE_S


def request_stop(signum: int = 15) -> None:
    """What SIGTERM does (also callable directly, e.g. by tests)."""
    from . import runner
    runner.STOP.set()
    stopflag.ABORT.set()
    log.warning("signal %d — stopping: no new work starts, waits end now, the run is marked "
                "stopped and the GPU lease released (hard exit in %.0fs at the latest)",
                signum, _stop_grace_s())
    t = threading.Thread(target=_stop_watchdog, args=(128 + signum,),
                         name="crucibleforge-stop-watchdog", daemon=True)
    t.start()


def _term(signum, frame):
    _TERMS["n"] += 1
    if _TERMS["n"] > 1:
        raise SystemExit(128 + signum)
    request_stop(signum)


def install_stop_handler() -> None:
    import signal
    signal.signal(signal.SIGTERM, _term)
    if hasattr(signal, "SIGUSR1"):           # not on Windows: the stop file works everywhere
        signal.signal(signal.SIGUSR1, _usr1)


# ------------------------------------------------------- cooperative drain
# Lead 2026-11-14: "finish in-flight requests, send no new ones, then exit". SIGUSR1
# (`systemctl --user kill -s USR1 <unit>`, `kill -USR1 <pid>`) or the stop file
# (``<results>/.drain``; a number in it = the ceiling in minutes) sets runner.STOP + DRAIN.
# No stream is cut unless --drain-max-minutes (or $CRUCIBLEFORGE_DRAIN_MAX_MINUTES, or the
# file's number) names a ceiling. After the drain the process exits at once: no escalation,
# judge, ladder or board (a later run resumes them). Exit code DRAIN_EXIT.
DRAIN_EXIT = 75                   # EX_TEMPFAIL: partial, resume later
DRAIN_FILE = ".drain"
DRAIN_POLL_S = 2.0
DRAIN_ENV = "CRUCIBLEFORGE_DRAIN_MAX_MINUTES"
DRAIN_CMDS = ("run", "all", "recover", "escalate", "bench-all")
_DRAIN = {"max_min": None}


def drain_max_minutes(args=None) -> float | None:
    v = getattr(args, "drain_max_minutes", None) if args is not None else None
    if v is None:
        raw = (os.environ.get(DRAIN_ENV) or "").strip()
        try:
            v = float(raw) if raw else None
        except ValueError:
            log.warning("%s=%r is not a number — no drain ceiling", DRAIN_ENV, raw)
            v = None
    return v if v and v > 0 else None


def request_drain(max_minutes: float | None = None, why: str = "request") -> bool:
    from . import runner
    return runner.request_drain(max_minutes if max_minutes is not None else _DRAIN["max_min"],
                                why=why)


def draining() -> bool:
    return stopflag.DRAIN.is_set()


def halted() -> bool:
    """A hard stop or a drain: nothing new (escalation, judge, ladder, board) starts."""
    return stopping() or draining()


def _usr1(signum, frame):
    request_drain(why=f"signal {signum}")


def drain_file() -> Path:
    return results_dir() / DRAIN_FILE


def _read_drain_file(path: Path) -> float | None:
    """The ceiling a stop file names (minutes), else None (no ceiling); the file is consumed."""
    try:
        txt = path.read_text(encoding="utf-8").strip()
    except OSError:
        txt = ""
    try:
        path.unlink()
    except OSError:
        pass
    try:
        v = float(txt) if txt else None
    except ValueError:
        v = None
    return v if v and v > 0 else None


def start_drain_watch(path: Path | None = None, started: float | None = None,
                      poll_s: float = DRAIN_POLL_S) -> threading.Thread:
    """Watch the stop file. A file older than this process is a leftover nobody acted on:
    it is removed with a warning, never obeyed."""
    path = path or drain_file()
    started = time.time() if started is None else started
    try:
        if path.exists() and path.stat().st_mtime < started:
            log.warning("removing a stale drain file from before this run: %s", path)
            path.unlink()
    except OSError:
        pass

    def watch():
        while not draining() and not stopflag.ABORT.is_set():
            if path.exists():
                n = _read_drain_file(path)
                request_drain(n, why=f"stop file {path}")
                return
            time.sleep(poll_s)
    t = threading.Thread(target=watch, name="crucibleforge-drain-watch", daemon=True)
    t.start()
    return t


def release_all_leases() -> None:
    """Release every GPU lease any provider of this process holds (bounded:
    the management client's connect timeout is seconds)."""
    from . import providers, subject_copies
    n = subject_copies.tear_down_all()     # subject copies' own leases + instances (virtual ids only)
    if n:
        log.warning("tore down %d subject-copy placement(s) on the way out", n)
    for _cfg, p in list(providers._CACHE.values()):
        if getattr(p, "_lease", None) is None and not getattr(p, "_vacated_render", False):
            continue
        try:
            p.release_lease()
        except Exception as e:  # noqa: BLE001 — best effort on the way out
            log.warning("lease release on %s failed: %s", p.name, e)


def _stop_watchdog(code: int) -> None:
    time.sleep(_stop_grace_s())
    log.error("still running %.0fs after the stop signal — marking the run stopped, "
              "releasing leases and ending the process", _stop_grace_s())
    _hard_exit(code)


def _hard_exit(code: int) -> None:
    from . import runner
    try:
        marked = runner.mark_active_stopped()
        if marked:
            log.warning("marked stopped: %s", ", ".join(marked))
        release_all_leases()
    finally:
        logging.shutdown()
        os._exit(code)


def _exit_after_stop(rc: int) -> None:
    """End a stopped process: release leases now (atexit would too, but only
    after joining threads that may never end), then exit — hard when a
    worker thread is still blocked in a socket."""
    release_all_leases()
    stuck = [t.name for t in threading.enumerate()
             if t is not threading.main_thread() and t.is_alive() and not t.daemon]
    if stuck:
        log.warning("leaving %d blocked worker thread(s) behind: %s", len(stuck),
                    ", ".join(stuck[:8]))
        sys.stdout.flush()
        sys.stderr.flush()
        logging.shutdown()
        os._exit(rc or TERM_EXIT)
    sys.exit(rc or TERM_EXIT)


def stopping() -> bool:
    return stopflag.ABORT.is_set()


def _remote_judge_only(args, cfg) -> bool:
    """`judge` with a hosted-API judge never touches the rig (no lease, no
    load, no guard), so it does not wait behind — or block — a benchmark
    holding results/.rig.lock. Do not re-judge a model that a running
    benchmark is still writing."""
    if getattr(args, "cmd", None) != "judge":
        return False
    try:
        from .judge import resolve_judge_spec
        from .profiles import DEFAULT_PROFILE, load_profile, profile_judge
        from .providers import get_provider
        spec = getattr(args, "judge", None) or profile_judge(
            load_profile(getattr(args, "profile", None) or DEFAULT_PROFILE, cfg))
        cand = resolve_judge_spec(cfg, spec)
        if not cand:
            return False
        remote = get_provider(cfg, cand["provider"]).type not in ("lmstudio", "studioforge")
    except Exception:  # noqa: BLE001 — unsure: take the lock
        return False
    if remote:
        log.info("judge %s is a hosted API — the rig lock is not taken", cand["model_id"])
    return remote


#: The rig lock this process holds (main's up-front lock or a phase lock).
_RIG_LOCK: dict = {"f": None}


def _is_rig_provider(cfg, name: str | None) -> bool:
    from .providers import get_provider
    try:
        return get_provider(cfg, name).type in ("lmstudio", "studioforge")
    except Exception:  # noqa: BLE001 — unsure: treat as rig
        return True


def all_hosted(args, cfg) -> bool:
    """True when every model the command selects is a hosted API (S10): such
    a run takes results/.rig.lock only around its rig phases (a rig judge),
    so it runs in parallel with a rig model's benchmark (STEP4: MiniMax had
    to run from a copy of the results dir to get past omega's lock)."""
    if getattr(args, "cmd", None) not in ("run", "all", "escalate", "recover"):
        return False
    try:
        entries = resolve_models(cfg, getattr(args, "models", None) or "all")
    except (SystemExit, Exception):  # noqa: BLE001 — unsure: take the lock
        return False
    return bool(entries) and not any(_is_rig_provider(cfg, e.get("provider")) for e in entries)


class rig_phase:
    """Hold results/.rig.lock for one rig phase when the command did not take
    it up front (S10). No-op when ``needed`` is false or the lock is held."""

    def __init__(self, needed: bool = True, what: str = "rig phase", shared: bool = False):
        self.needed, self.what, self.mine, self.shared = needed, what, False, shared

    def __enter__(self):
        if self.needed and _RIG_LOCK["f"] is None:
            f = acquire_rig_lock(shared=self.shared)
            if f is not None:
                _RIG_LOCK["f"], self.mine = f, True
                log.info("rig lock taken for the %s", self.what)
        return self

    def __exit__(self, *exc):
        if self.mine:
            f, _RIG_LOCK["f"] = _RIG_LOCK["f"], None
            try:
                import fcntl
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
            except (ImportError, OSError):
                pass
            f.close()
            log.info("rig lock released after the %s", self.what)
        return False


def _rig_lock_path() -> Path:
    return results_dir() / ".rig.lock"


def _ancestor_holds(path: Path) -> bool:
    """True when this process (an inherited fd) or one of its ancestors has
    ``path`` open (Linux /proc)."""
    try:
        target = os.path.realpath(path)
        pid = os.getpid()
        for _ in range(64):
            if pid <= 1:
                return False
            fd_dir = Path(f"/proc/{pid}/fd")
            try:
                for fd in fd_dir.iterdir():
                    try:
                        if os.path.realpath(os.readlink(fd)) == target:
                            return True
                    except OSError:
                        continue
            except OSError:
                pass
            stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
            pid = int(stat.rsplit(")", 1)[1].split()[1])
    except (OSError, ValueError, IndexError):
        return False
    return False


def acquire_rig_lock(shared: bool = False):
    """Take results/.rig.lock (blocking). Returns the open file (keep it
    alive for the process lifetime) or None when not applicable.

    ``shared`` (judge shards only): a SHARED flock — any number of judge
    shards hold it together, while every other rig command (run, all,
    bench-all, an unsharded judge, recover, escalate) still needs it
    EXCLUSIVE and so waits for all shards to end, and a shard waits for
    them. Two shards are kept off each other's cards by the per-GPU locks
    (shard.DeviceLocks)."""
    try:
        import fcntl
    except ImportError:  # Windows: no flock — one-at-a-time is on the operator
        return None
    path = _rig_lock_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    if _ancestor_holds(path):
        log.info("rig lock %s is held by a parent process — running under it", path)
        return None
    mode = fcntl.LOCK_SH if shared else fcntl.LOCK_EX
    f = open(path, "a+", encoding="utf-8")
    try:
        fcntl.flock(f.fileno(), mode | fcntl.LOCK_NB)
    except BlockingIOError:
        f.seek(0)
        holder = f.read().strip()[:200]
        log.warning("another CrucibleForge benchmark holds %s (%s) — waiting for it to "
                    "finish (one benchmark at a time)", path, holder or "holder unknown")
        # poll, not a blocking flock: a SIGTERM while waiting must end the wait
        while True:
            try:
                fcntl.flock(f.fileno(), mode | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if stopflag.sleep(2.0):
                    f.close()
                    raise SystemExit(TERM_EXIT)
    if not shared:
        # a shared holder never rewrites the note (the other shards' notes
        # would be lost); the per-GPU lock files carry theirs
        f.seek(0)
        f.truncate()
        f.write(f"pid {os.getpid()} since {_utcnow_iso()}: crucibleforge {' '.join(sys.argv[1:])[:160]}\n")
        f.flush()
    return f


#: seconds a detached unit gets between SIGTERM and SIGKILL on `systemctl stop`
DETACH_STOP_TIMEOUT_S = 45


#: spend-cap variables a detached re-launch carries into its unit (spend.py)
SPEND_ENV = ("CF_SPEND_CAP_USD", "CF_SPEND_RUN_CAP_USD", "CF_SPEND_JOURNAL", "CF_SPEND_ALLOW_CASES", "CF_SPEND_LEDGER",
             "CF_SPEND_CAP_MODEL", "CF_SPEND_GLOBAL_CAP_USD", "CF_SPEND_BASELINE_USD",
             "CF_SPEND_HELPER_JOURNAL", "CF_SPEND_RERUN_DIR", "CF_SPEND_COUNT_PLAN")


def _detach_argv(args, argv: list[str] | None) -> tuple[str, list[str], str]:
    """(unit name, systemd-run argv, run id) for a detached re-launch."""
    run_id = _resolve_run_id(args)
    raw = list(argv if argv is not None else sys.argv[1:])
    inner = [a for a in raw if a != "--detach"]
    if not any(a == "--run-id" or a.startswith("--run-id=") for a in inner):
        inner += ["--run-id", run_id]
    unit = "crucibleforge-" + re.sub(r"[^A-Za-z0-9_.-]+", "-", run_id)[:120]
    cwd = str(Path(__file__).resolve().parents[1])
    inner_cmd = " ".join(shlex.quote(a) for a in inner)
    source = (f"set -a; [ -r {shlex.quote(str(ENV_FILE))} ] && . {shlex.quote(str(ENV_FILE))}; set +a; "
              if ENV_FILE is not None else "")
    script = f"{source}cd {shlex.quote(cwd)} && exec uv run crucibleforge {inner_cmd}"
    cmd = ["systemd-run", "--user", "--collect", "--quiet", f"--unit={unit}",
           f"--description=CrucibleForge {args.cmd}: {args.models} (run {run_id})",
           # a stop is bounded (OPERATOR-PLAN-v2 C4): SIGTERM to the main
           # process only (uv forwards it to the bench, whose handler releases
           # the lease and unloads its own models), SIGKILL to the whole unit
           # after DETACH_STOP_TIMEOUT_S
           f"--property=TimeoutStopSec={DETACH_STOP_TIMEOUT_S}", "--property=KillMode=mixed",
           f"--setenv=CRUCIBLEFORGE_RUN_ID={run_id}",
           # the spend cap and journal (spend.py) must reach the unit: a
           # systemd --user unit does not inherit the caller's environment,
           # and a cap that silently stays behind is no cap
           *[f"--setenv={k}={os.environ[k]}" for k in SPEND_ENV if os.environ.get(k)],
           f"--working-directory={cwd}", "/bin/bash", "-c", script]
    return unit, cmd, run_id


def _detach(args, argv: list[str] | None) -> int:
    import subprocess
    unit, cmd, run_id = _detach_argv(args, argv)
    if shutil.which("systemd-run") is None:
        print("--detach needs systemd-run (systemd --user); run without --detach", file=sys.stderr)
        return 2
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
    if r.returncode != 0:
        print(f"detach failed (systemd-run rc={r.returncode}): {(r.stderr or r.stdout).strip()[:400]}",
              file=sys.stderr)
        return r.returncode or 1
    print(f"detached: unit={unit} run_id={run_id}")
    print(f"  status: systemctl --user is-active {unit}   (active = running, inactive = finished)")
    if V2_RUNS_ROOT is not None:
        print(f"  result: {_v2_run_dir(run_id)}/meta.json (status done|failed) and report.md")
    print(f"  log:    journalctl --user -u {unit} -n 50")
    return 0


def package_version() -> str:
    """Installed distribution version; the suite version from a bare checkout."""
    from importlib import metadata
    try:
        return metadata.version("crucibleforge-bench")
    except metadata.PackageNotFoundError:
        from .version import SUITE_VERSION
        return SUITE_VERSION


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="crucibleforge",
        description="CrucibleForge — LLM capability benchmark (hard tier, deterministic "
                    "graders, local or remote judge, web GUI)")
    parser.add_argument("--version", action="version",
                        version=f"crucibleforge {package_version()}")
    parser.add_argument("--config", default=None, help="path to models.yaml")
    parser.add_argument(
        "--allow-unsandboxed", action="store_true",
        help="execute model-authored code with NO isolation when bubblewrap "
             "is unavailable (always the case on macOS and Windows). Off by "
             "default: graded code would run as you, with the network "
             "reachable and your home directory readable.")
    parser.add_argument("--results", default=None,
                        help="results directory (default: <config dir>/results)")
    sub = parser.add_subparsers(dest="cmd", required=True)

    def add_force_evict_arg(p):
        p.add_argument(
            "--force-evict", action="store_true",
            help="send force=true on the FIRST lease attempt. The rig honours this for "
                 "an IDLE resident of EITHER refusal dialect — a plain 'pinned' resident "
                 "OR a priority-tier one (e.g. a pinned, priority-1 chat model) — it can "
                 "and will evict either; it never overrides a resident mid-request. This "
                 "tool never sets it in response to a refusal — pass it only with an "
                 "explicit go-ahead from whoever owns the rig, exactly because it CAN reach "
                 "a priority-tier resident (a provider's standing `force_evict: true` "
                 "policy in models.yaml does the same for every run).")

    def add_staged_arg(p):
        p.add_argument("--include-staged", action="store_true",
                       help="also run and score ladder packs marked \"staged\": true "
                            "(a finished pack waiting for the owner's approval); without it "
                            "they are skipped and never scored")

    def add_spend_cap_arg(p):
        p.add_argument("--spend-cap-usd", type=float, default=None, metavar="USD",
                       help="refuse further hosted-API calls once this process has spent "
                            "this much (summed cost_usd; also $CF_SPEND_CAP_USD; "
                            "$CF_SPEND_LEDGER names a ledger file shared across processes)")

    def add_v2_args(p):
        """--detach + the fleet run-report routing (run / all / judge)."""
        p.add_argument("--detach", action="store_true",
                       help="re-launch this exact command as a transient systemd --user unit and "
                            "return immediately (prints the unit + run id). Use from any agent "
                            "tool call — the bench then outlives the call's timeout.")
        p.add_argument("--run-id", default=None,
                       help="id for this run's "
                            "$CRUCIBLEFORGE_V2_RUNS_ROOT/<id>/{report.md,meta.json} (else "
                            "$CRUCIBLEFORGE_RUN_ID, else self-minted)")
        p.add_argument("--deliver-to", default=None,
                       help="DisPatch thread id to route the V2 run report to "
                            "(else $CRUCIBLEFORGE_DELIVER_TO, else the standing scanner "
                            "falls back to the daily thread)")
        p.add_argument("--requester", default=None,
                       help="gateway session key that dispatched this run, e.g. "
                            "agent:example:thread-1 (else $CRUCIBLEFORGE_REQUESTER)")
        p.add_argument("--task-run-id", default=None,
                       help="gateway runId for this unit of work, for blocked-task "
                            "reconciliation (else $CRUCIBLEFORGE_TASK_RUN_ID)")

    def add_drain_arg(p):
        p.add_argument("--drain-max-minutes", type=float, default=None, metavar="N",
                       help="on a drain request (SIGUSR1 or the <results>/.drain file), cut a "
                            "stream still running after N minutes (default: no ceiling, nothing "
                            "is cut; also $CRUCIBLEFORGE_DRAIN_MAX_MINUTES)")

    def add_adult_arg(p):
        p.add_argument("--no-nsfw-plus", dest="no_nsfw_plus", action="store_true",
                       help="leave NSFW+ out of this run (it runs in every standard run by default). "
                            "A model entry's `nsfw_plus: false` leaves it out for that model alone; the Claude "
                            "Code route never runs it")
        p.add_argument("--adult", action="store_true", help=argparse.SUPPRESS)
        p.add_argument("--no-adult", dest="no_adult", action="store_true", help=argparse.SUPPRESS)

    def add_run_args(p):
        add_force_evict_arg(p)
        add_adult_arg(p)
        add_drain_arg(p)
        p.add_argument("--models", default="all",
                       help="comma-separated labels, or 'all' (= enabled)")
        p.add_argument("--categories", default=None,
                       help=f"comma-separated from: {','.join(CATEGORIES)}")
        p.add_argument("--smoke", action="store_true",
                       help="fast subset, 1 repeat per case")
        p.add_argument("--yes", action="store_true",
                       help="proceed even if LM Studio is serving another model (skips nothing else)")
        p.add_argument("--fresh", action="store_true",
                       help="archive prior transcripts for these models first "
                            "(default: accumulate across runs)")
        p.add_argument("--skip-conformance", action="store_true",
                       help="do not gate generation on a current conformance "
                            "record: a model is benched without a proven "
                            "request protocol (the override is logged)")
        p.add_argument("--samples", type=int, default=None,
                       help="judge self-consistency samples (default: config)")
        p.add_argument("--difficulty", default=None,
                       help="comma-separated tiers to run: easy,medium,hard")
        p.add_argument("--cases", default=None,
                       help="comma-separated case ids to run (subset of the selection)")
        p.add_argument("--profile", default=None,
                       help="benchmark definition (default: bench45; the 4.1 archive: --profile bench)")
        p.add_argument("--judge-fallback", action="store_true",
                       help="with --judge: allow the next judge candidate if the forced "
                            "judge cannot be loaded (default: strict — the judge phase fails)")
        p.add_argument("--judge", default=None,
                       help="judge to use: a judge.candidates name, a registry label (e.g. minimax-m3), or provider:model_id. Default: the profile's judge (bench: Gemma-4-31B heretic Q8). A hosted-API judge takes no GPU lease and no rig lock")
        p.add_argument("--no-judge", action="store_true", dest="no_judge",
                       help="no judge at all: every programmatic column scores, the judged "
                            "facts stay unjudged (the board says so), Overall is blank and the "
                            "model is unranked; `crucibleforge judge` scores them later")
        p.add_argument("--allow-unpriced", action="store_true",
                       help="run a hosted model that has no `price:` in models.yaml "
                            "(its Run cost stays blank)")
        p.add_argument("--no-link-check", action="store_true",
                       help="skip the pre-flight provider data-channel probe")
        add_staged_arg(p)
        add_spend_cap_arg(p)
        add_v2_args(p)

    sub.add_parser("status", help="providers, registry, judge, cases")
    p_run = sub.add_parser("run", help="benchmark models")
    add_run_args(p_run)
    # explicit, so `--force` is never prefix-matched to `--force-evict`
    p_run.add_argument("--force", action="store_true",
                       help="with --fresh plus --cases/--categories: archive the whole "
                            "result set anyway")
    p_lj = sub.add_parser("ladder-judge", help="place stored role-play session rows on their cards' ladders "
                                               "(pair judge; rung check first; no model is re-run)")
    p_lj.add_argument("--models", default="all")
    p_lj.add_argument("--profile", default=None, help="default: bench45")
    p_lj.add_argument("--judge", default=None, help="must resolve to the profile's judge (verdicts are stamped)")
    p_lj.add_argument("--windows", default=None, help="scoring windows, e.g. 1,3 (default: the part's ladder "
                                                      "judge mode, else 1,3); the rung check always uses 1,2,3")
    p_lj.add_argument("--plan", action="store_true", help="no judge: print calls and minutes")
    p_lj.add_argument("--rung-check", action="store_true", help="only the rung check of the ladders")
    p_lj.add_argument("--force", action="store_true", help="re-place rows with a current placement (verdicts "
                                                           "stay cached: costs nothing unless the prompt changed)")
    p_lj.add_argument("--until", default=None, metavar="HH:MM", help="stop judging at this local time")
    p_lj.add_argument("--attach", action="store_true", help="use the judge copy already resident on the rig (the "
                      "operator's load): no lease, no load, no unload; refuses unless it is ready")
    p_lj.add_argument("--client", default=None, help="with --attach: the X-SF-Client name every judge call "
                      "declares (the operator's lease holder)")
    p_ag = sub.add_parser("agree", help="second reader of another model family re-grades the stored judged "
                                        "units; prints the agreement table per part (production verdicts untouched)")
    p_ag.add_argument("folders", nargs="+", help="result folders (one transcripts_<label>.jsonl each)")
    p_ag.add_argument("--reader", required=True, action="append",
                      help="the reader: judge candidate name, registry label or provider:model_id; never of the "
                           "production judge's family. Repeat it: each folder is read by the first reader of "
                           "another family than both its judge and its graded model (none = not measured)")
    p_ag.add_argument("--parts", default=None, help="comma list (default chat/j_rplong,chat/j_rpsession; chat/packs45 = the Chat pack scenes)")
    p_ag.add_argument("--nsfw-plus", "--adult", dest="adult", action="store_true", help="also re-grade the NSFW+ judged rows")
    p_ag.add_argument("--cap-usd", type=float, default=None, help="spend cap; required for a hosted reader")
    p_ag.add_argument("--reader-thinking", choices=("on", "off"), default="on",
                      help="the reader thinks (default on; 'off' reads exactly as the production judge's policy)")
    p_ag.add_argument("--plan", action="store_true", help="count units and calls; no model is loaded or called")
    p_ag.add_argument("--out", default=None, help="agreement table json (default <first folder>/agree/)")
    p_ag.add_argument("--judge-devices", default=None, help="rig reader: exactly these cards (e.g. 2,3)")
    p_ag.add_argument("--judge-parallel", default=None, help="rig reader: slot ladder, e.g. 4,2")
    p_ag.add_argument("--judge-allow-devices", default=None, help="cards the judge never takes by default (3), "
                      "named on purpose")
    p_ag.add_argument("--judge-instance-name", default=None, help="lease holder suffix (default agree)")
    p_jc = sub.add_parser("judge-check", help="the judge's own accuracy on the constructed NSFW+ classing set "
                          "(61 items) and opinion-flag set: stored in judge45/check.json and printed, never a gate")
    add_force_evict_arg(p_jc)
    p_jc.add_argument("--judge", default=None, help="judge to check (default: the profile's judge)")
    p_jc.add_argument("--profile", default=None, help="default: bench45")
    p_jc.add_argument("--show", action="store_true", help="print the stored result only (no model call)")
    p_judge = sub.add_parser("judge", help="judge pending quality rows (4.5: also classes the NSFW+ units and reads "
                             "the opinion flags with the same local judge, one vote each)")
    add_force_evict_arg(p_judge)
    p_judge.add_argument("--models", default="all")
    p_judge.add_argument("--force", action="store_true",
                         help="re-judge rows that already have verdicts")
    p_judge.add_argument("--retry-failed", action="store_true",
                         help="also re-judge rows whose stored verdict FAILED (row timeout, "
                              "unparsable reply) — without --force's re-judging of every row")
    p_judge.add_argument("--samples", type=int, default=None)
    p_judge.add_argument("--cases", default=None,
                         help="comma-separated case ids to judge (only these cases' rows; "
                              "the others stay pending — use it to re-judge one case after "
                              "a grading-only case fix)")
    p_judge.add_argument("--judge", default=None, help="judge to use: a judge.candidates name, a registry label (e.g. minimax-m3), or provider:model_id. Default: the profile's judge (bench: Gemma-4-31B heretic Q8). A hosted-API judge takes no GPU lease and no rig lock")
    p_judge.add_argument("--allow-self-judge", action="store_true",
                         help="experiments only: let the judge score its own rows "
                              "(the report flags them as self-judged)")
    p_judge.add_argument("--judge-fallback", action="store_true",
                         help="allow the next candidate if the forced --judge cannot load "
                              "(default: strict, the phase fails instead)")
    p_judge.add_argument("--profile", default=None, help="default: bench45 (its judge); --profile bench for the 4.1 judge")
    g = p_judge.add_argument_group(
        "sharding / judge instances",
        "run one judge phase as N processes, each judging whole models (one writer per "
        "transcript file) on its own copy of the SAME judge; rows keep the canonical judge "
        "id, the copy is recorded in judge_instance. See `judge-shards`.")
    g.add_argument("--shard", default=None, metavar="I/N",
                   help="judge only the models of shard I of N (stable hash of the label, or "
                        "--shard-plan's balanced split)")
    g.add_argument("--pool", action="store_true",
                   help="share the selected models with every other judge process started "
                        "with --pool: each takes the next free model (one writer per "
                        "transcript file), so a faster copy simply judges more")
    g.add_argument("--shard-plan", default=None, metavar="FILE",
                   help="the plan `judge-shards --out` wrote (balanced by pending work)")
    g.add_argument("--judge-serve-id", default=None, metavar="ID",
                   help="registry id that SERVES this judge copy (an alias over the same "
                        "GGUF); the rows still record the judge's own id")
    g.add_argument("--judge-instance", default=None, metavar="TAG",
                   help="native instance tag/port of the copy, when the server runs several "
                        "copies under one id")
    g.add_argument("--judge-instance-name", default=None, metavar="NAME",
                   help="short name (a, b): lease holder crucibleforge-judge-NAME")
    g.add_argument("--judge-devices", default=None, metavar="LIST",
                   help="lease exactly these cards for the judge (e.g. 2,3); never force, "
                        "never vacate. Required for a sharded rig judge")
    g.add_argument("--judge-parallel", default=None, metavar="LIST",
                   help="slot ladder to load this copy with, widest first (e.g. 16,14,12); "
                        "a refused width steps down")
    g.add_argument("--instance", default=None, metavar="NAME",
                   help="a judge copy from models.yaml instances: (by name, e.g. a / b): "
                        "its serve id, cards, slot ladder, window, vacate; the flags below "
                        "override single fields")
    g.add_argument("--no-steal", action="store_true",
                   help="do not take over the other shards' labels once their locks free up")
    g.add_argument("--window", default=None, metavar="HH:MM-HH:MM",
                   help="local time this copy may judge in (default: the instance's "
                        "window; 'any' = no window). Outside it nothing starts; at its end "
                        "the copy stops and its rows stay pending for the other shard")
    g.add_argument("--vacate", action="store_true",
                   help="register a vacate URL on the lease: when a better class asks for "
                        "the cards, give them back within the rig's 180 s")
    g.add_argument("--vacate-host", default=None, metavar="ADDR",
                   help="address the rig can POST the vacate request to (this box's tailnet "
                        "address; or models.yaml instance_vacate.host, "
                        "$CRUCIBLEFORGE_VACATE_HOST)")
    g.add_argument("--vacate-port", type=int, default=None, metavar="PORT",
                   help="port for the vacate listener (default: any free port)")
    g.add_argument("--judge-allow-devices", default=None, metavar="LIST",
                   help="cards the judge normally never takes (GPU 3: embedder, ComfyUI, "
                        "voice) that this copy may take — an explicit decision")
    p_judge.add_argument("--part", default=None, metavar="CAT/PART",
                         help="re-judge every stored row of one sub-score from its transcript "
                              "(standard + pack questions, e.g. nsfw/uncensored); implies "
                              "--force for those rows, never generates")
    add_spend_cap_arg(p_judge)
    add_staged_arg(p_judge)
    add_v2_args(p_judge)
    p_js = sub.add_parser(
        "judge-shards",
        help="plan a sharded judge phase (balanced by pending work) or verify one afterwards")
    p_js.add_argument("--models", default="all")
    p_js.add_argument("--shards", type=int, default=2)
    p_js.add_argument("--out", default=None, help="write the plan here (JSON)")
    p_js.add_argument("--verify", default=None, metavar="PLAN",
                      help="audit the transcripts against a plan: judged / still pending / "
                           "judged twice / torn lines (exit 1 on duplicates or torn lines)")
    p_js.add_argument("--force", action="store_true")
    p_js.add_argument("--retry-failed", action="store_true")
    p_js.add_argument("--profile", default=None)
    p_recover = sub.add_parser(
        "recover", help="re-run reasoning-overflow rows (empty answers) through recovery")
    add_force_evict_arg(p_recover)
    add_adult_arg(p_recover)
    p_recover.add_argument("--models", default="all")
    p_recover.add_argument("--yes", action="store_true")
    p_recover.add_argument("--profile", default=None)
    p_recover.add_argument("--judge", default=None, help=argparse.SUPPRESS)
    p_recover.add_argument("--no-link-check", action="store_true")
    p_esc = sub.add_parser(
        "escalate", help="re-run the answers that ran out of budget on bigger budgets "
                         "(64k, then 128k) for the board's Room column")
    add_force_evict_arg(p_esc)
    add_adult_arg(p_esc)
    p_esc.add_argument("--models", default="all")
    p_esc.add_argument("--rungs", default=None,
                       help="comma list of output budgets (default: the profile's "
                            "escalation_rungs)")
    p_esc.add_argument("--yes", action="store_true")
    p_esc.add_argument("--profile", default=None)
    p_esc.add_argument("--judge", default=None, help=argparse.SUPPRESS)
    p_esc.add_argument("--no-link-check", action="store_true")
    p_report = sub.add_parser("report", help="generate comparison report")
    p_report.add_argument("--models", default=None)
    p_report.add_argument("--profile", default=None,
                          help="the profile the board is built for (default: bench45)")
    p_report.add_argument("--public", action="store_true",
                          help="build the board for an audience other than you: private "
                               "categories (cases/private/, a profile's private: list) are "
                               "left out and every file is leak-checked before writing; "
                               "writes results/public/ (or --out), never the local board")
    p_report.add_argument("--out", default=None, help="with --public: output directory")
    p_report.add_argument("--allow-placeholders", action="store_true",
                          help="with --public: render although README.md, docs/SCORING.md or docs/RUNNING.md "
                               "still hold a {{MEASURED: ...}} placeholder (refused by default)")
    p_report.add_argument("--check-stale", action="store_true",
                          help="list, per model, the board rows graded by code or data that has changed since "
                               "(grader stamp missing or different); exit 1 when any scored row is stale")
    p_report.add_argument("--staged-claude-code", action="store_true",
                          help="STAGED board only (never --public, never the site / HF paths): show "
                               "claude_code (subscription CLI) rows as unranked validation rows marked "
                               "'not for publication'; without it they are refused")
    p_cmp = sub.add_parser("compare", help="head-to-head: paired bootstrap difference of the "
                                           "headline scores of two models (no model is called)")
    p_cmp.add_argument("a")
    p_cmp.add_argument("b")
    p_cal = sub.add_parser("calibrate", help="flag sub-scores the reference model aces "
                                             "(too easy) and sub-scores a model maxed "
                                             "(needs a new pack); reads the board only")
    p_cal.add_argument("--reference", default=None,
                       help="reference model label (default: the profile's calibration.reference)")
    p_pack = sub.add_parser("pack", help="ladder packs: list, scaffold (new) or check (verify) them")
    pks = p_pack.add_subparsers(dest="action", required=True)
    p_pn = pks.add_parser("new", help="scaffold the next pack of a sub-score (category/part)")
    p_pn.add_argument("subscore", help="e.g. work/code")
    p_pn.add_argument("--count", type=int, default=3, help="questions (default 3; any >= 1)")
    p_pn.add_argument("--level", type=int, default=None,
                      help="unlock level (default: one above the highest existing)")
    p_pn.add_argument("--category", default=None, help="case category of the stubs")
    p_pn.add_argument("--grading", default=None, choices=["exact", "python_exec", "judged"])
    p_pv = pks.add_parser("verify", help="check one pack file")
    p_pv.add_argument("file")
    p_pv.add_argument("--allow", action="append", default=None, metavar="CASE:ruleN:TEXT=REASON",
                      help="let one content-rules lint hit stand, with the reviewer's reason "
                           "(repeatable; the hit line prints the exact CASE:ruleN:TEXT)")
    p_pl = pks.add_parser("list", help="list the optional packs per sub-score and which models maxed them")
    p_pl.add_argument("--json", action="store_true", help="machine-readable output")
    p_pl.add_argument("--no-board", action="store_true", help="do not read the board (skip maxed data)")
    p_pl.add_argument("--recompute", action="store_true",
                      help="rebuild the maxed data from the raw results instead of reading results/report.json (slow)")
    for _pp in (p_pn, p_pv, p_pl):
        _pp.add_argument("--profile", default=None,
                         help="profile whose case tree is used (default: bench45, the live cases/; "
                              "a pack is never written into archive/)")
    p_pw = sub.add_parser("pairwise", help="head-to-head A/B Elo on creative categories")
    p_pw.add_argument("--models", default="all")
    p_pw.add_argument("--categories", default="rp,nsfw,story")
    p_pw.add_argument("--judge", default=None)
    p_pw.add_argument("--yes", action="store_true")
    p_all = sub.add_parser("all", help="run, judge, ladder packs and report (escalate only with --escalate)")
    add_run_args(p_all)
    p_all.add_argument("--plan", action="store_true",
                       help="print what would run per model (tier-1 rows, repeats, budgets, "
                            "the context it loads at, the tier-2/3 items and their gates) "
                            "without calling any model, provider or judge")
    p_all.add_argument("--no-tiers", action="store_true",
                       help="standard only: no ladder sets / tier-2/3 phases (= --no-ladder)")
    p_all.add_argument("--escalate", action="store_true",
                       help="also run the escalation round: answers that ran out of budget "
                            "re-run on the profile's escalation_rungs (4.0: 64k only). Off by default since "
                            "3.9 — it is outside the timed run; `crucibleforge escalate` "
                            "does the same on its own")
    p_all.add_argument("--no-escalate", action="store_true",
                       help="accepted for compatibility (escalation is off unless --escalate)")
    p_all.add_argument("--no-ladder", action="store_true",
                       help="standard cases only: no ladder sets (suite 3.9)")
    p_all.add_argument("--ladder-sections", default=None, metavar="LIST",
                       help="run only the ladder sets of these score sections (comma-separated, "
                            "e.g. work); an unknown section is refused before anything runs")
    p_all.add_argument("--no-lockout", action="store_true",
                       help="4.1 archive only: skip the NSFW Lockout phase (the 4.5 board has no Lockout column)")
    p_all.add_argument("--no-run", dest="no_run", action="store_true",
                       help="skip the generation phase: judge what is stored, run the due ladder packs, report "
                            "(refused with --fresh)")
    p_all.add_argument("--timing", action="store_true",
                       help="print the per-phase wall-clock table at the end (always recorded "
                            "in each model's meta under `timing`)")
    p_all.add_argument("--force", action="store_true",
                       help="re-judge rows that already have verdicts; also lets --fresh "
                            "combine with --cases/--categories (archives the whole set)")

    p_ba = sub.add_parser(
        "bench-all", help="refresh the whole board: sync the registry with the rig, "
                          "generate only missing cases, judge, rebuild both boards "
                          "(plan only unless --go)")
    p_ba.add_argument("--go", action="store_true", help="execute (default: print the plan)")
    add_drain_arg(p_ba)
    add_adult_arg(p_ba)
    p_ba.add_argument("--include-api", action="store_true",
                      help="also complete hosted-API models (metered — costs money)")
    p_ba.add_argument("--no-sync", action="store_true",
                      help="do not register rig models missing from models.yaml "
                           "(even ones --only names)")
    p_ba.add_argument("--register-new", action="store_true",
                      help="also register EVERY in-scope rig model missing from models.yaml "
                           "(default: only models --only names are registered)")
    p_ba.add_argument("--no-prune", action="store_true",
                      help="keep board rows of models gone from the rig / disabled")
    p_ba.add_argument("--first", default=None,
                      help="comma list of labels to generate before everything else")
    p_ba.add_argument("--until", default=None, metavar="HH:MM",
                      help="time window: EVERYTHING (generation, escalation, ladder packs, "
                           "judge, boards) is done by HH:MM — a model starts only when its "
                           "estimated generation + judge fit, in-flight rows are capped, the "
                           "judge stops before the end (unjudged rows wait for the next run)")
    p_ba.add_argument("--judge-reserve-min", type=float, default=45,
                      help="the FLOOR of the judge reserve: generation stops early enough for "
                           "the estimated judge work (crucibleforge/estimate.py) but never "
                           "less than this many minutes before the judge (default 45)")
    p_ba.add_argument("--plan-start", default=None, metavar="HH:MM",
                      help="plan only: estimate the --until window as if the run started then")
    add_staged_arg(p_ba)
    add_spend_cap_arg(p_ba)
    p_ba.add_argument("--no-ladder", action="store_true",
                      help="standard cases only: no ladder packs")
    p_ba.add_argument("--escalate", action="store_true",
                      help="4.0: after every model's single pass, ONE retry pass for the rows "
                           "that ran out of budget, at the next rung only (off by default)")
    p_ba.add_argument("--no-escalate", action="store_true",
                      help="accepted for compatibility (escalation is off unless --escalate)")
    p_ba.add_argument("--allow-unpriced", action="store_true",
                      help="run a hosted model that has no `price:` in models.yaml "
                           "(its Run cost stays blank)")
    p_ba.add_argument("--skip-conformance", action="store_true",
                      help="do not gate generation on a current conformance "
                           "record (the override is logged)")
    p_ba.add_argument("--only", default=None, metavar="LABELS",
                      help="comma list of labels (or rig model ids): touch ONLY these — "
                           "registration, generation, escalation, ladder packs and the judge; "
                           "every other model is left alone (no prune of other rows)")
    p_ba.add_argument("--lease-mode", choices=("night", "day"), default=None,
                      help="override the models.yaml lease policy: night = evict IDLE "
                           "residents only, never cut a busy one; day = no force, no render-lease "
                           "vacate, one card no priority-1/2/pinned model is on (else "
                           "refuse), request priority 3, no judge. Default: models.yaml")
    p_ba.add_argument("--no-judge", action="store_true",
                      help="generate only: no judge phase and no ladder packs (rows wait "
                           "for the next judged run); implied by --lease-mode day")
    p_ba.add_argument("--quant-reason", default=None, metavar="REASON",
                      help="with --only LABEL naming ONE new registration: record why the quant "
                           "is below Q8_0 in the models.yaml entry (q8_does_not_fit)")
    p_ba.add_argument("--inherit-from", default=None, metavar="LABEL",
                      help="with --only LABEL: when LABEL is a NEW registration, copy the model-behaviour "
                           "settings (thinking, censored, sampler, max_output_tokens, "
                           "extra_body, tags) of registry entry LABEL; for a re-test of the same model at another quant. "
                           "Its reasoning replay is NOT copied: the new file's own chat template is rendered (replay-rule)")
    p_ba.add_argument("--plan-json", action="store_true",
                      help="print the plan as ONE JSON object (per label: missing cases, "
                           "estimated generation/judge minutes, window verdict vs --until); "
                           "touches nothing")
    p_ba.add_argument("--models", default=None, help=argparse.SUPPRESS)
    add_v2_args(p_ba)

    p_gui = sub.add_parser("gui", help="web GUI (local, no CDN)")
    p_gui.add_argument("--host", default="127.0.0.1")
    p_gui.add_argument("--port", type=int, default=8777)
    p_gui.add_argument("--token", default=None,
                       help="access token (required when binding a non-loopback host; "
                            "default: $CRUCIBLEFORGE_GUI_TOKEN, then <data dir>/gui_token, "
                            "else auto-generated)")
    p_gui.add_argument("--no-open", action="store_true", help="don't open a browser tab")
    gsub = p_gui.add_subparsers(dest="gui_cmd", metavar="token")
    p_gtok = gsub.add_parser(
        "token", help="show / rotate the GUI access token (the only login CrucibleForge has)")
    gt = p_gtok.add_subparsers(dest="token_action", required=True)
    gt.add_parser("show", help="print the token the GUI would use, and where it comes from")
    p_rot = gt.add_parser("rotate", help="write a new token to <data dir>/gui_token and print it")
    p_rot.add_argument("--token-stdin", action="store_true",
                       help="read the new token from stdin instead of generating one "
                            "(12-256 chars of A-Z a-z 0-9 . _ ~ -)")

    p_dg = sub.add_parser("digest", help="10-line daily quarantine digest (R5)")
    p_dg.set_defaults(profile=None)
    p_q = sub.add_parser("quarantine", help="close a quarantine or halt record")
    p_q.add_argument("qid", help="record id, q-YYYYMMDD-NNN")
    p_q.add_argument("--status", required=True, choices=("fixed", "escalated", "rejected"))

    p_reg = sub.add_parser("registry-check",
                           help="check the profile's points per item against the lab registry "
                                "(SYSTEM-45 section 3; fails closed)")
    p_reg.add_argument("--profile", default="bench45")
    p_reg.add_argument("--lab", default=None, help="lab dir (default $CF_LAB_DIR, then "
                                                   "~/.local/share/crucibleforge/lab)")

    p_scr = sub.add_parser("scratch-init",
                           help="create a scratch results dir (a .cf-scratch marker) for staged packs")
    p_scr.add_argument("dir", help="the directory to create / mark (never the board's results/)")

    p_cfg = sub.add_parser("config", help="locate / init / upgrade models.yaml")
    p_cfg.add_argument("--init", nargs="?", const=True, default=False,
                       help="write models.example.yaml to ./models.yaml (or PATH)")
    p_cfg.add_argument("--upgrade", action="store_true",
                       help="rewrite a v2 registry in the v3 providers shape")

    p_imp = sub.add_parser("import-openclaw",
                           help="import providers + models from an OpenClaw openclaw.json")
    p_imp.add_argument("--openclaw-json", default="~/.openclaw/openclaw.json")
    p_imp.add_argument("--include-local", action="store_true",
                       help="also import loopback/LAN providers (default: remote APIs only)")
    p_imp.add_argument("--write", action="store_true", help="save into models.yaml")

    p_models = sub.add_parser("models", help="discover / add models")
    ms = p_models.add_subparsers(dest="action", required=True)
    p_disc = ms.add_parser("discover", help="list model ids a provider serves")
    p_disc.add_argument("provider")
    p_add = ms.add_parser("add", help="add a model to the registry")
    p_add.add_argument("name")
    p_add.add_argument("provider")
    p_add.add_argument("model_id")
    p_add.add_argument("--context-length", type=int, default=32768)
    p_add.add_argument("--price-in", type=float, default=None, help="USD per 1M input tokens")
    p_add.add_argument("--price-out", type=float, default=None, help="USD per 1M output tokens")
    p_add.add_argument("--tags", default=None)

    p_bud = sub.add_parser(
        "budget", help="4.5 time budget: home minutes per case and for the Core main test, packs "
                       "reported (exit 1 over the limit; docs/BUDGET.md)")
    p_bud.add_argument("--profile", default=None, help="profile with a budget: block (default bench45)")
    p_bud.add_argument("--case", nargs="+", default=None, metavar="ID", help="show only these cases (limit not checked)")
    p_bud.add_argument("--home", action="store_true",
                       help="the home budget (the only mode; accepted for clarity: budget.home of the profile)")
    p_bud.add_argument("--tps", type=float, default=None, help="tokens/s per stream (default: budget.home.tps, 20)")
    p_bud.add_argument("--slots", type=float, default=None, help="parallel slots (default: budget.home.slots, 1)")
    p_bud.add_argument("--judge-s", dest="judge_s", type=float, default=None, metavar="S",
                       help="seconds per judge call (default: budget.home.judge_s_per_call, 25)")
    p_bud.add_argument("--results", dest="budget_results", default=None, metavar="DIR",
                       help="also MEASURE from this results directory (read only; default: committed digest only)")
    p_bud.add_argument("--require-measured", dest="require_measured", action="store_true",
                       help="freeze gate: fail on any standard case whose tokens are only an ESTIMATE "
                            "(also profile budget.freeze: true)")
    p_bud.add_argument("--json", action="store_true", help="machine-readable report")

    p_gen = sub.add_parser(
        "gen", help="show or check one generated item (no model): "
                    "gen <family> --seed S --level L [--variant V] [--key K] --show | --check")
    p_gen.add_argument("family", help="generator family (keyfunc, tracevm, ledger, istack, session_state ...)")
    p_gen.add_argument("--seed", default=None, help="release seed (default: the profile's)")
    p_gen.add_argument("--level", type=int, required=True, help="rung / level of the family")
    p_gen.add_argument("--variant", default=None, help="family variant (istack: write|conv; session_state: state|multi)")
    p_gen.add_argument("--key", default=None, help="instance key (default none; the case id is GEN)")
    p_gen.add_argument("--show", action="store_true", help="print the item: level parameters and prompt")
    p_gen.add_argument("--check", action="store_true",
                       help="reference grades 1.0, every mutant below 1.0, lazy answer 0, deterministic (exit 1 on failure)")

    p_cases = sub.add_parser("cases", help="list / verify the case set")
    cs = p_cases.add_subparsers(dest="action", required=True)
    for name in ("list", "verify"):
        pc = cs.add_parser(name)
        pc.add_argument("--categories", default=None)
        pc.add_argument("--difficulty", default=None)
        pc.add_argument("--lint-elements", action="store_true", dest="lint_elements",
                        help="also lint judge_elements against the 4.5 element standard (EL-1..)")
        pc.add_argument("--profile", default=None,
                        help="profile whose ladder / 4.5 hard sets are checked (default: bench45)")
        pc.add_argument("--verbose", "-v", action="store_true")
        if name == "verify":
            pc.add_argument("--budget", choices=("off", "warn", "error"), default=None,
                            help="run the time-budget check on profiles that have a budget: block "
                                 "(default warn there; bench.yaml has none and is unchanged)")

    p_rq = sub.add_parser(
        "reverify-quotes", help="read-only: list stored element verdicts that "
                                "the current quote verifier would flip to PASS "
                                "(no judge call, writes nothing)")
    p_rq.add_argument("--json", action="store_true", help="print the report as JSON")

    p_rr = sub.add_parser(
        "rejudge-reasoning",
        help="list (or with --apply, make pending) judged rows whose finished answer "
             "sat in the reasoning channel and was judged empty; no generation")
    p_rr.add_argument("--models", default="all",
                      help="comma-separated transcript labels (default: every transcript)")
    p_rr.add_argument("--apply", action="store_true",
                      help="append the repaired rows (verdict moved aside, so they are "
                           "pending for `judge`); refused while a benchmark holds the rig lock")
    p_rr.add_argument("--json", action="store_true", help="print the report as JSON")

    p_conf = sub.add_parser(
        "conform", help="check that the harness's request protocol works for "
                        "a model (served / reasoning / chain / tools / sampler "
                        "/ budget) before it is benched")
    p_conf.add_argument("--models", default="all",
                        help="comma list of labels, or 'all' (default)")
    p_conf.add_argument("--json", action="store_true",
                        help="print the records as JSON (the per-check detail "
                             "goes to the log)")
    p_conf.add_argument("--allow-load", action="store_true",
                        help="load a rig model that is not resident yet "
                             "(off by default: conform never loads a model "
                             "behind the bench's back)")
    p_conf.add_argument("--profile", default=None,
                        help="the benchmark profile whose sampler and budgets "
                             "the checks use (default: bench45)")

    p_sp = sub.add_parser(
        "spend", help="global API spend: `spend status` adds the bench ledger, the helper journal "
                      "and the Claude rerun journals to the baseline against the global cap "
                      "(exit 1 when over)")
    p_sp.add_argument("action", choices=["status"])
    p_sp.add_argument("--ledger", default=None, metavar="FILE",
                      help="the bench ledger (default $CF_SPEND_LEDGER, else the fixed bench45 path)")
    p_sp.add_argument("--helper-journal", default=None, metavar="FILE",
                      help="the helper's llmcall journal (default $CF_SPEND_HELPER_JOURNAL)")
    p_sp.add_argument("--rerun-dir", default=None, metavar="DIR",
                      help="folder of the Claude rerun journals, *.jsonl (default $CF_SPEND_RERUN_DIR)")
    p_sp.add_argument("--baseline", type=float, default=None, metavar="USD",
                      help="the earlier global ledger (default $CF_SPEND_BASELINE_USD, 0)")
    p_sp.add_argument("--cap", type=float, default=None, metavar="USD",
                      help="the global cap (default $CF_SPEND_GLOBAL_CAP_USD, else 330)")
    p_sp.add_argument("--count-plan", action="store_true",
                      help="count plan models (Grok, MiniMax) at list price in the total")
    p_sp.add_argument("--json", action="store_true")
    p_cc = sub.add_parser(
        "card-check", help="read a model's Hugging Face card(s) and chat template and PROPOSE "
                           "its recommended sampler / settings_checked entry (writes nothing)")
    p_cc.add_argument("model", help="a registry label, a rig model id, or an HF repo")
    p_cc.add_argument("--json", action="store_true", help="print the proposal as JSON")

    p_rr = sub.add_parser(
        "replay-rule", help="render each model's chat template and show its reasoning-replay value "
                            "under the 4.5 rule (template at default settings; a card/API rule overrides)")
    p_rr.add_argument("models", help="labels (comma-separated) or 'all'")
    p_rr.add_argument("--write", action="store_true",
                      help="record new/changed values in models.yaml (reasoning_replay: {value, source, ...})")

    args = parser.parse_args(argv)
    if args.cmd == "bench-all" and args.plan_json and (args.go or args.detach):
        parser.error("--plan-json is a plan only: drop --go/--detach")

    if args.allow_unsandboxed:
        from . import graders
        graders.ALLOW_UNSANDBOXED = True
        # Anything we spawn (the GUI's own runs) inherits the decision.
        os.environ["CRUCIBLEFORGE_ALLOW_UNSANDBOXED"] = "1"

    if args.cmd == "spend":
        sys.exit(cmd_spend(args))
    if args.cmd == "config":
        setup_logging()
        sys.exit(cmd_config(args, None) or 0)
    if args.cmd == "gui" and getattr(args, "gui_cmd", None) == "token":
        setup_logging()
        sys.exit(cmd_gui_token(args) or 0)

    if args.results:
        os.environ["CRUCIBLEFORGE_RESULTS"] = args.results
        from .config import set_results_dir
        set_results_dir(Path(args.results))
    # `cases` reads only the case files that ship with the repo — no provider,
    # no models, no results. A clean checkout (CI) has no models.yaml, so
    # requiring one here would fail `cases verify` on every fresh runner.
    config_optional = args.cmd in ("cases", "pack", "budget", "gen", "digest", "quarantine")
    try:
        cfg = load_config(args.config)
    except ConfigError as e:
        if not config_optional:
            setup_logging()
            print(f"config error: {e}", file=sys.stderr)
            sys.exit(2)
        cfg = {}
    if args.results:
        from .config import set_results_dir
        set_results_dir(Path(args.results))
    if args.cmd in ("budget", "pack") and not getattr(args, "profile", None):
        args.profile = "bench45"    # fix-d: maintainer commands read the live tree (never the frozen archive/)
    from .profiles import set_active as _set_active
    _set_active(getattr(args, "profile", None))   # ONE active profile for report / ladder / unlock paths
    try:    # the active profile may name its own case tree (G-41Freeze)
        from .profiles import DEFAULT_PROFILE as _DPN, activate_cases_root, load_profile as _lp
        activate_cases_root(_lp(getattr(args, "profile", None) or _DPN, cfg or None))
    except Exception:  # noqa: BLE001 - no/unknown profile: the default tree
        pass
    setup_logging((results_dir() / "crucibleforge.log") if cfg else None)
    # the command's profile owns the generated-case release seed (env still wins)
    try:
        from .profiles import DEFAULT_PROFILE as _DP0, activate_release_seed, load_profile as _lp0
        activate_release_seed(_lp0(getattr(args, "profile", None) or _DP0, cfg or None))
    except Exception:  # noqa: BLE001
        pass            # an unknown/broken profile is reported by the command itself
    # a SIGTERM (queue script killed, `pkill`, systemd stop) must end the run
    # in seconds, mark it stopped and release the GPU lease (M10)
    install_stop_handler()
    if args.cmd in DRAIN_CMDS and cfg:
        _DRAIN["max_min"] = drain_max_minutes(args)
        start_drain_watch()

    # Fail fast: refuse before a multi-hour run rather than at the first
    # coding case. "cases list" and the read-only commands never execute
    # anything, so they are not gated.
    from . import graders
    executes_code = args.cmd in {"run", "all", "recover", "escalate", "gui", "bench-all"} or (
        args.cmd in ("cases", "pack") and getattr(args, "action", None) == "verify")
    if executes_code:
        try:
            graders.require_sandbox()
        except graders.SandboxUnavailable as e:
            print(f"sandbox: {e}", file=sys.stderr)
            sys.exit(3)
        why = graders.unsandboxed_reason()
        if why:
            # require_sandbox() passed without a sandbox: the opt-in is in
            # force. Say so now, on the terminal, before any work starts --
            # not once in the log at the first coding case hours into a run.
            # A bwrap that is installed but broken lands here too, which is
            # easy to mistake for "I have the sandbox, the flag is a no-op".
            log.warning("%s is in force and there is NO sandbox: %s. Coding cases will "
                        "execute model-written code as this user, with network access "
                        "and this user's files readable.", graders.ALLOW_FLAG, why)

    if stopflag.ABORT.is_set():
        _exit_after_stop(TERM_EXIT)
    _apply_process_switches(args)
    handler = {"status": cmd_status, "run": cmd_run, "judge": cmd_judge, "ladder-judge": cmd_ladder_judge,
               "agree": cmd_agree, "judge-check": cmd_judge_check,
               "recover": cmd_recover, "escalate": cmd_escalate, "report": cmd_report, "compare": cmd_compare, "calibrate": cmd_calibrate,
               "pack": cmd_pack, "pairwise": cmd_pairwise, "all": cmd_all,
               "gui": cmd_gui, "import-openclaw": cmd_import_openclaw,
               "models": cmd_models, "cases": cmd_cases, "bench-all": cmd_bench_all,
               "conform": cmd_conform, "card-check": cmd_card_check, "replay-rule": cmd_replay_rule, "reverify-quotes": cmd_reverify_quotes,
               "judge-shards": cmd_judge_shards, "budget": cmd_budget, "gen": cmd_gen,
               "scratch-init": cmd_scratch_init, "registry-check": cmd_registry_check,
               "digest": cmd_digest, "quarantine": cmd_quarantine,
               "rejudge-reasoning": cmd_rejudge_reasoning}[args.cmd]
    # WP-BENCH FIX-6: `run`/`all` are the two commands that do real benched
    # work end to end, so they are the ones tracked by the fleet-wide
    # runs/<id>/{report.md,meta.json} contract. This wraps the dispatch
    # itself (not cmd_run's body) so `cmd_all` calling `cmd_run` internally
    # writes the contract exactly once, for the WHOLE all=run+judge+report
    # unit of work, not a premature "done" the moment run() alone finishes.
    v2_tracked = args.cmd in ("run", "all", "judge") or (
        args.cmd == "bench-all" and (getattr(args, "go", False) or getattr(args, "detach", False)))
    if v2_tracked and getattr(args, "detach", False):
        sys.exit(_detach(args, argv))
    v2_state = _v2_start(args) if v2_tracked else None
    rig_lock = None
    try:
        plan_only = args.cmd == "bench-all" and not getattr(args, "go", False)
        if (args.cmd in RIG_LOCK_CMDS and not plan_only and not _remote_judge_only(args, cfg)
                and not getattr(args, "plan", False)):
            if all_hosted(args, cfg):
                log.info("every selected model is a hosted API — results/.rig.lock is taken "
                         "only around rig phases (a rig judge), not for the whole run")
            elif sharded_judge(args):
                # taken SHARED inside cmd_judge (with the per-GPU locks),
                # after the shard's labels are known
                pass
            else:
                rig_lock = acquire_rig_lock()  # noqa: F841 — held until exit
                _RIG_LOCK["f"] = rig_lock
        rc = handler(args, cfg)
    except ConfigError as e:
        print(f"config error: {e}", file=sys.stderr)
        rc = 2
    except BaseException as e:
        if stopflag.ABORT.is_set():
            log.warning("stopped (SIGTERM) during %s: %s", args.cmd, type(e).__name__)
            if v2_tracked:
                _v2_finish(args.cmd, args, cfg, TERM_EXIT, v2_state)
            _exit_after_stop(TERM_EXIT)
        if v2_tracked:
            _v2_finish(args.cmd, args, cfg, _exit_code_from_exception(e), v2_state)
        raise
    finally:
        # Drop the module-level handle so an in-process caller (the test
        # suite) does not leave a stale lock that makes later rig_phase()s
        # no-ops. A real run exits right after, which released it anyway.
        if rig_lock is not None:
            _RIG_LOCK["f"] = None
            rig_lock.close()
    if stopflag.ABORT.is_set():
        rc = TERM_EXIT
    if v2_tracked:
        _v2_finish(args.cmd, args, cfg, rc or 0, v2_state)
    if stopflag.ABORT.is_set():
        _exit_after_stop(rc)
    sys.exit(rc or 0)


if __name__ == "__main__":
    main()
