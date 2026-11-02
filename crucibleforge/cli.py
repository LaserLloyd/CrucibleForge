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
    returns what was applied. A missing/unreadable file applies nothing."""
    applied: dict[str, str] = {}
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
    try:
        meta = _v2_meta(run_id, status=status, created=state["created"], finished=_utcnow_iso(),
                        title=state["title"], requester_session=state["requester_session"],
                        thread_id=state["thread_id"], task_run_id=state["task_run_id"],
                        extra=state.get("extra"))
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
        return (f"reachable, but it rejected the API key from {source} "
                f"(HTTP {code}): check the key")
    name = env or DEFAULT_API_KEY_ENV
    extra = "" if env else f" and add api_key_env: {name} to the provider in models.yaml"
    return f"reachable, but it wants an API key: set ${name}{extra} (HTTP {code})"


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
                if p.force_evict_policy or p.lease_devices_preferred or p.clawforge_mcp:
                    print(f"    bench-first: force_evict={'ON' if p.force_evict_policy else 'off'}"
                          f"  preferred cards={p.lease_devices_preferred or '-'} (else {p.lease_devices or 'all'})"
                          f"  vacate render lease via ClawForge={'yes' if p.clawforge_mcp else 'no'}"
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
    from .profiles import DEFAULT_PROFILE, apply_profile, list_profiles, load_profile
    print(f"profiles: {', '.join(list_profiles(cfg)) or 'none'}")
    try:
        _, bench = apply_profile(load_profile(DEFAULT_PROFILE, cfg), cfg)
        print(f"benchmark ({DEFAULT_PROFILE}): {len(bench)} cases — "
              f"`crucibleforge all --models <label> --fresh --yes`")
    except ConfigError as e:
        print(f"benchmark ({DEFAULT_PROFILE}): INVALID — {e}")
    return 0


def _apply_profile_arg(args, cfg):
    """The benchmark definition: --profile, else the one benchmark
    (``bench``). Returns (cfg with its budgets applied, its cases, profile)."""
    from .profiles import DEFAULT_PROFILE, apply_profile, load_profile, profile_judge
    name = getattr(args, "profile", None) or DEFAULT_PROFILE
    prof = load_profile(name, cfg)
    cfg2, cases = apply_profile(prof, cfg, smoke=getattr(args, "smoke", False))
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
    from .preflight import judge_unrunnable_reason, unrunnable_reason
    from .providers import provider_for
    from .runner import _write_meta
    from .version import revision
    if check_judge:
        why = judge_unrunnable_reason(cfg, judge)
        if why:
            raise SystemExit(f"judge check: {why}")
    ok, bad = [], {}
    for e in entries:
        try:
            why = unrunnable_reason(cfg, e)
        except Exception as ex:  # noqa: BLE001 — a flaky listing must not block the run
            log.warning("runnability check for %s skipped: %s", e["name"], ex)
            why = None
        if why:
            log.error("model %s cannot run: %s", e["name"], why)
            bad[e["name"]] = why
            _write_meta(e["name"], e, provider_for(cfg, e), failed=True, error=why,
                        profile=cfg.get("_profile"), revision=revision(cfg), reset=True)
        else:
            ok.append(e)
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
    if not entries:
        raise SystemExit("no models selected (all disabled? pass --models)")
    if not cases:
        raise SystemExit("no cases selected")
    if getattr(args, "fresh", False):
        _archive_labels([e["name"] for e in entries])
    needs_judge = any(c.get("rubric") or c.get("turns") for c in cases)
    entries, unrunnable = _fail_fast(cfg, entries, judge=getattr(args, "judge", None),
                                     check_judge=args.cmd == "all" and needs_judge)
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
        summary = run_models(cfg, entries, cases, smoke=args.smoke)
    finally:
        guard.restore()
    args.ran_labels = [e["name"] for e in entries if e["name"] in summary]
    for label, why in unrunnable.items():
        summary[label] = {"failed": True, "error": why}
    print("\nrun summary:")
    _print_run_summary(summary)
    return _run_rc(summary, [e["name"] for e in entries] + list(unrunnable))


def _run_rc(summary: dict, wanted: list[str]) -> int:
    """Non-zero when any requested model failed or never ran (a queue script
    must not stamp DONE over a half-finished batch)."""
    missing = [l for l in wanted if l not in summary]
    failed = [l for l, s in summary.items() if s.get("failed")]
    if missing:
        print(f"  NOT RUN: {', '.join(missing)}")
    return 1 if (missing or failed) else 0


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
    cfg, _prof_cases, _ = _apply_profile_arg(args, cfg)
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
    if not entries:
        raise SystemExit("no models selected (pass --models)")
    rungs = escalation_rungs(cfg)
    if getattr(args, "rungs", None):
        rungs = sorted({int(x) for x in _parse_list(args.rungs)})
    if not rungs:
        print("escalation is off for this profile (no escalation_rungs above the cap)")
        return 0
    todo = {e["name"]: len(escalation_todo(report.board_filter(e["name"], cfg, with_rungs=True),
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


def cmd_judge(args, cfg):
    cfg, prof_cases, _ = _apply_profile_arg(args, cfg)
    entries = resolve_models(cfg, args.models)
    labels = [e["name"] for e in entries]
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
    from .judge import pending_judge_rows
    retry_failed = bool(getattr(args, "retry_failed", False))
    only = _judge_cases(args, prof_cases)
    if pending_judge_rows(labels, force=bool(getattr(args, "force", False)),
                          retry_failed=retry_failed, cases=only) == 0:
        # Judge-free profile (e.g. `coding`) or already judged: do not touch
        # the rig at all — a lease here would evict residents and vacate
        # ComfyUI for a phase that has nothing to do.
        log.info("nothing to judge for %s — rig untouched", labels)
        print("nothing to judge")
        return 0
    with rig_phase(needed=not _judge_is_remote(args, cfg), what="judge phase"):
        return _cmd_judge_locked(args, cfg, labels, samples, retry_failed, only)


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
    if not only:
        return None
    known = {c["id"] for c in prof_cases}
    missing = sorted(set(only) - known)
    if missing:
        raise SystemExit(f"unknown case id(s): {missing}")
    return only


def _judge_is_remote(args, cfg) -> bool:
    a = copy.copy(args)
    a.cmd = "judge"
    return _remote_judge_only(a, cfg)


def _cmd_judge_locked(args, cfg, labels, samples, retry_failed, cases=None):
    from .judge import JudgeLeaseUnavailable, acquire_judge_lease, run_judge
    force_evict = getattr(args, "force_evict", False) or _judge_policy_force(cfg)
    # The guard's resident snapshot MUST be taken before the judge lease:
    # with bench-first the lease itself evicts a chat-tier resident, and a
    # snapshot taken afterwards is empty — so nothing was restored and the
    # rig sat empty after the judge phase (run 1, 2026-09-08 08:59).
    guard = _ProviderGuard(cfg, [], force_evict=force_evict,
                           judge=getattr(args, "judge", None))
    try:
        acquire_judge_lease(cfg, force_evict=force_evict,
                            override=getattr(args, "judge", None))
    except JudgeLeaseUnavailable as e:
        holder = f" (current holder: {e.holder})" if e.holder else ""
        print(f"judge lease: {e}{holder}", file=sys.stderr)
        log.error("judge aborted — could not reserve all GPUs: %s", e)
        # The lease was refused before any load, so restore is a no-op on
        # the residents; it still releases anything half-taken. Non-zero
        # keeps the queue script from stamping DONE over a batch that did
        # not run.
        guard.restore()
        return 4
    try:
        # stop: bench-all's window edge (an Event its timer sets); a SIGTERM
        # (stopflag.ABORT) stops the phase the same way inside run_judge —
        # in-flight verdicts finish, queued rows stay pending, no reloads
        result = run_judge(cfg, labels, force=args.force, samples=samples,
                           judge_override=getattr(args, "judge", None),
                           allow_fallback=getattr(args, "judge_fallback", None),
                           allow_self_judge=bool(getattr(args, "allow_self_judge", False)),
                           retry_failed=retry_failed,
                           stop=getattr(args, "judge_stop", None),
                           deadline=getattr(args, "judge_deadline", None),
                           cases=cases)
    except Exception as e:
        # the board names the reason next to the unjudged rows
        _stamp_judge_error(labels, str(e))
        raise
    finally:
        guard.restore()
    _stamp_judged(labels)
    print(f"judged {result['judged']} rows "
          f"({result['failed']} unparsable judge verdicts, "
          f"{result.get('empty', 0)} empty generations, "
          f"{result.get('errored', 0)} errored, samples={result.get('samples')}) "
          f"with {result.get('judge')}")
    if result.get("stopped"):
        print(f"judge phase stopped early: {result.get('left', 0)} row(s) left pending "
              f"(judged by the next run)")
    return 1 if result.get("errored") else 0


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
        p.write_text(json.dumps(meta, indent=2), encoding="utf-8")


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
        p.write_text(json.dumps(meta, indent=2), encoding="utf-8")


def cmd_report(args, cfg, full_board: bool = False):
    """Rebuild the board. Prints only the scorecard and where the files are
    (the full report is in report.md / failures.md / report.html).

    ``full_board`` (used at the end of ``all``): the shared board always lists
    EVERY benchmarked model, not just the one this run benched — otherwise each
    run overwrote results/report.md with a one-row board (2026-09-24)."""
    from .report import (PrivateLeak, failures_md_path, generate, public_dir,
                         report_html_path, report_md_path, scorecard_section)
    if getattr(args, "public", False):
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


def cmd_pack(args, cfg):
    """`crucibleforge pack new <category/part>` scaffolds the next ladder pack
    (draft); `crucibleforge pack verify <file>` checks one (docs/CUSTOM-TESTS.md)."""
    from pathlib import Path as _P
    from . import ladder
    from .config import ConfigError as _CE
    from .profiles import DEFAULT_PROFILE, load_profile
    prof = load_profile(DEFAULT_PROFILE, cfg or None)
    try:
        if args.action == "new":
            path = ladder.new_pack(prof, args.subscore, count=args.count,
                                   category=args.category, grading=args.grading,
                                   level=args.level)
            print(f"wrote {path} (draft, {args.count} question stub(s), 1 point in total)")
            print("\n".join(ladder._GRADING_HELP))
            return 0
        problems, notes = ladder.verify_pack(_P(args.file), prof)
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
    """The whole-board refresh (crucibleforge/bench_all.py)."""
    from .bench_all import execute
    return execute(args, cfg)


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
    if getattr(args, "plan", False):
        return cmd_plan(args, cfg)
    from . import timing
    clock = timing.Clock()
    args._clock = clock
    t0 = time.monotonic()
    rc = cmd_run(args, cfg)
    ran = getattr(args, "ran_labels", None)
    # the runner records each model's generation rounds in its own meta
    clock.add("generation", time.monotonic() - t0, ran or [], record_meta=False)
    if stopping():
        return TERM_EXIT      # SIGTERM: no escalation / judge / report after a stop
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
    from .profiles import DEFAULT_PROFILE
    rc_e = 0
    if (getattr(args, "profile", None) or DEFAULT_PROFILE) == DEFAULT_PROFILE and not getattr(args, "smoke", False):
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
    if uses_ladder(args, cfg):
        rc_t = _ladder_phases(args, cfg, ran or [])
    else:
        t1 = time.monotonic()
        rc_t = _tier_phases(args, cfg, ran or [])
        clock.add("tier phases", time.monotonic() - t1, ran or [])
    if stopping():
        return TERM_EXIT
    with clock.phase("report", ran or []):
        rc_r = cmd_report(args, cfg, full_board=True)
    print(clock.summary_line())
    if getattr(args, "timing", False):
        print(clock.table(ran or []))
    return rc_r or rc or rc_j or rc_t


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
    rows = report.board_filter(label, cfg)
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
    have = done_jobs(report.board_filter(label, cfg))
    jobs = {(None, c["id"], r) for c in cases
            for r in range(1, repeats_for(cfg, c["category"], smoke, c) + 1)
            if (c["id"], r) not in have}
    return scores, cases, jobs


def done_jobs(rows: list[dict]) -> set[tuple]:
    """(case id, repeat) of every job with a usable current row. An ERRORED
    row (grade ``error``: transport failure, rejected request) is not done
    — a resumed run retries it (EVAL-FABLE M4; bench_all.missing_cases has
    the same rule, bounded by MAX_CASE_ATTEMPTS)."""
    return {(r.get("case_id"), r.get("repeat")) for r in rows if r.get("grade") != "error"}


def _tier_phases(args, cfg, labels: list[str]) -> int:
    """Suite 3.9: tier 2, then tier 3, for every model that unlocked a score
    (tiers.py). Each tier: run the unlocked items (a tier-3 round loads the
    model at ``tiers.t3.context``), escalate what ran out, judge. A failure
    here never blocks the report; the board shows the tier as pending."""
    from .profiles import DEFAULT_PROFILE, load_profile
    from .runner import run_models
    from . import tiers as _tiers
    name = getattr(args, "profile", None) or DEFAULT_PROFILE
    if (name != DEFAULT_PROFILE or getattr(args, "smoke", False) or not labels
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
MAX_LADDER_ROUNDS = 12


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
        for n in ladder.due(pr.get("std"), pr.get("sets") or {}, seq, unlock):
            cs = ladder.set_cases(prof, key, n, smoke=smoke, sets=sets)
            if cs:
                cases += cs
                names.append(f"{key}#{n}")
    have = done_jobs(report.board_filter(label, cfg))
    jobs = {(None, c["id"], r) for c in cases
            for r in range(1, repeats_for(cfg, c["category"], smoke, c) + 1)
            if (c["id"], r) not in have and (c["id"], r) not in (tried or set())}
    return cases, jobs, names


def _ladder_phases(args, cfg, labels: list[str]) -> int:
    """Suite 3.9 ladders: after the standard judge, run every model's due
    ladder sets (all sets of the lowest open level of each sub-score
    together), escalate (only with --escalate), judge, then look again —
    the next level is due only when every level below scored >= the unlock
    share — until nothing is due. A failure here never blocks the report;
    the board shows an unrun set as pending."""
    from . import ladder
    from . import report
    from .profiles import DEFAULT_PROFILE, load_profile
    from .runner import run_models
    name = getattr(args, "profile", None) or DEFAULT_PROFILE
    if (name != DEFAULT_PROFILE or getattr(args, "smoke", False) or not labels
            or getattr(args, "no_tiers", False) or getattr(args, "no_ladder", False)):
        return 0
    try:
        prof = load_profile(name, cfg)
        sets = ladder.discover(prof)
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
        for label, entry in entries.items():
            if stopping():
                return TERM_EXIT
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
        if not ran:
            break
        if clock:
            clock.add(f"ladder round {level} generation", time.monotonic() - t0, ran)
        if stopping():
            return TERM_EXIT
        rc = rc or _escalate_phase(args, cfg, ran)
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


def cmd_plan(args, cfg) -> int:
    """`crucibleforge all --plan`: what `all` would run per model — every
    standard row (case, repeats, budget) in its load round(s) (a rig model:
    round A at the context floor, round B for the wide rows; a hosted API:
    one round), the ladder sets each sub-score could add, and a wall-time
    ESTIMATE (crucibleforge/estimate.py) — computed, no model or provider
    called. The ladder lists show what WOULD run once unlocked; the unlock
    rule decides at run time from the model's scores."""
    from . import estimate as est
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

    for e in entries:
        prov = provider_for(cfg_p, e)
        rig = prov.type in _LOADING_TYPES
        perf = [c for c in cases if c["category"] == "perf"]
        t1 = [c for c in cases if c["category"] != "perf"]
        n_all = sum(repeats_for(cfg_p, c["category"], smoke, c) for c in t1)
        out.append(f"== {e['name']} ({e.get('provider')}:{e['model_id']})")
        out.append(f"  T1 — every model: {n_all} rows ({len(t1)} cases) + {len(perf)} perf probes")
        rounds = plan_rounds(cfg_p, e, cases)
        gen_total, judged_total, parts = 0.0, 0, []
        for i, rd in enumerate(rounds):
            rcs = [c for c in rd["cases"] if c["category"] != "perf"]
            lines, n = rows_of(rcs, rd["ctx"], e)
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
        sets = ladder.discover(prof)
    except ConfigError as ex:
        return [f"  ladder: INVALID — {ex}"]
    unlock = ladder.unlock_share(prof)
    if not sets:
        return [f"  ladder: no sets yet (cases/ladder/<category>/<part>/set-<n>.json; "
                f"a set runs when every level below scored >= {unlock:.0%})"]
    out.append(f"  ladder — a set runs when every level below it scored >= {unlock:.0%} "
               f"(all sets of one level together):")
    gen_all, judged_all = 0.0, 0
    for key, seq in sets.items():
        for spec in seq:
            cs = ladder.set_cases(prof, key, spec["n"], smoke=getattr(args, "smoke", False),
                                  sets=sets)
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
                       difficulties=_parse_list(args.difficulty))
    if args.action == "list":
        for c in cases:
            print(f"{c['category']:11s} {c.get('difficulty', 'medium'):6s} "
                  f"{c.get('grader') or c.get('rubric') or '-':12s} {c['id']}")
        print(f"\n{len(cases)} cases")
        return 0
    if args.action == "verify":
        from .verify_cases import verify_all
        rc = verify_all(cases, verbose=args.verbose)
        # ladder packs: the set files themselves (unknown ids, empty packs,
        # a standard case reused in a pack, a case in two packs)
        from . import ladder
        from .profiles import DEFAULT_PROFILE, load_profile
        try:
            problems = ladder.verify(load_profile(DEFAULT_PROFILE, cfg or None))
        except Exception as e:  # noqa: BLE001 — report, don't crash the case check
            problems = [f"ladder: {e}"]
        for p in problems:
            print(f"FAIL ladder: {p}")
        return rc or (1 if problems else 0)
    return 1


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
RIG_LOCK_CMDS = ("run", "all", "judge", "recover", "escalate", "bench-all")


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


def release_all_leases() -> None:
    """Release every GPU lease any provider of this process holds (bounded:
    the management client's connect timeout is seconds)."""
    from . import providers
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

    def __init__(self, needed: bool = True, what: str = "rig phase"):
        self.needed, self.what, self.mine = needed, what, False

    def __enter__(self):
        if self.needed and _RIG_LOCK["f"] is None:
            f = acquire_rig_lock()
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


def acquire_rig_lock():
    """Take results/.rig.lock (blocking). Returns the open file (keep it
    alive for the process lifetime) or None when not applicable."""
    try:
        import fcntl
    except ImportError:  # Windows: no flock — one-at-a-time is on the operator
        return None
    path = _rig_lock_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    if _ancestor_holds(path):
        log.info("rig lock %s is held by a parent process — running under it", path)
        return None
    f = open(path, "a+", encoding="utf-8")
    try:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        f.seek(0)
        holder = f.read().strip()[:200]
        log.warning("another CrucibleForge benchmark holds %s (%s) — waiting for it to "
                    "finish (one benchmark at a time)", path, holder or "holder unknown")
        # poll, not a blocking flock: a SIGTERM while waiting must end the wait
        while True:
            try:
                fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if stopflag.sleep(2.0):
                    f.close()
                    raise SystemExit(TERM_EXIT)
    f.seek(0)
    f.truncate()
    f.write(f"pid {os.getpid()} since {_utcnow_iso()}: crucibleforge {' '.join(sys.argv[1:])[:160]}\n")
    f.flush()
    return f


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
           f"--setenv=CRUCIBLEFORGE_RUN_ID={run_id}",
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

    def add_run_args(p):
        add_force_evict_arg(p)
        p.add_argument("--models", default="all",
                       help="comma-separated labels, or 'all' (= enabled)")
        p.add_argument("--categories", default=None,
                       help=f"comma-separated from: {','.join(CATEGORIES)}")
        p.add_argument("--smoke", action="store_true",
                       help="fast subset, 1 repeat per case")
        p.add_argument("--yes", action="store_true",
                       help="proceed even if LM Studio is serving a model")
        p.add_argument("--fresh", action="store_true",
                       help="archive prior transcripts for these models first "
                            "(default: accumulate across runs)")
        p.add_argument("--samples", type=int, default=None,
                       help="judge self-consistency samples (default: config)")
        p.add_argument("--difficulty", default=None,
                       help="comma-separated tiers to run: easy,medium,hard")
        p.add_argument("--cases", default=None,
                       help="comma-separated case ids to run (subset of the selection)")
        p.add_argument("--profile", default=None,
                       help="benchmark definition (default: bench — the only one on the board)")
        p.add_argument("--judge-fallback", action="store_true",
                       help="with --judge: allow the next judge candidate if the forced "
                            "judge cannot be loaded (default: strict — the judge phase fails)")
        p.add_argument("--judge", default=None,
                       help="judge to use: a judge.candidates name, a registry label (e.g. minimax-m3), or provider:model_id. Default: the profile's judge (bench: Gemma-4-31B heretic Q8). A hosted-API judge takes no GPU lease and no rig lock")
        p.add_argument("--no-link-check", action="store_true",
                       help="skip the pre-flight provider data-channel probe")
        add_v2_args(p)

    sub.add_parser("status", help="providers, registry, judge, cases")
    p_run = sub.add_parser("run", help="benchmark models")
    add_run_args(p_run)
    # explicit, so `--force` is never prefix-matched to `--force-evict`
    p_run.add_argument("--force", action="store_true",
                       help="with --fresh plus --cases/--categories: archive the whole "
                            "result set anyway")
    p_judge = sub.add_parser("judge", help="judge pending quality rows")
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
    p_judge.add_argument("--profile", default=None, help="default: bench (its 122B judge)")
    add_v2_args(p_judge)
    p_recover = sub.add_parser(
        "recover", help="re-run reasoning-overflow rows (empty answers) through recovery")
    add_force_evict_arg(p_recover)
    p_recover.add_argument("--models", default="all")
    p_recover.add_argument("--yes", action="store_true")
    p_recover.add_argument("--profile", default=None)
    p_recover.add_argument("--judge", default=None, help=argparse.SUPPRESS)
    p_recover.add_argument("--no-link-check", action="store_true")
    p_esc = sub.add_parser(
        "escalate", help="re-run the answers that ran out of budget on bigger budgets "
                         "(64k, then 128k) for the board's Room column")
    add_force_evict_arg(p_esc)
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
    p_report.add_argument("--public", action="store_true",
                          help="build the board for an audience other than you: private "
                               "categories (cases/private/, a profile's private: list) are "
                               "left out and every file is leak-checked before writing; "
                               "writes results/public/ (or --out), never the local board")
    p_report.add_argument("--out", default=None, help="with --public: output directory")
    p_cmp = sub.add_parser("compare", help="head-to-head: paired bootstrap difference of the "
                                           "headline scores of two models (no model is called)")
    p_cmp.add_argument("a")
    p_cmp.add_argument("b")
    p_cal = sub.add_parser("calibrate", help="flag sub-scores the reference model aces "
                                             "(too easy) and sub-scores a model maxed "
                                             "(needs a new pack); reads the board only")
    p_cal.add_argument("--reference", default=None,
                       help="reference model label (default: the profile's calibration.reference)")
    p_pack = sub.add_parser("pack", help="ladder packs: scaffold (new) or check (verify) one")
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
    p_pw = sub.add_parser("pairwise", help="head-to-head A/B Elo on creative categories")
    p_pw.add_argument("--models", default="all")
    p_pw.add_argument("--categories", default="rp,nsfw,story")
    p_pw.add_argument("--judge", default=None)
    p_pw.add_argument("--yes", action="store_true")
    p_all = sub.add_parser("all", help="run + escalate + judge + report")
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
    p_ba.add_argument("--include-api", action="store_true",
                      help="also complete hosted-API models (metered — costs money)")
    p_ba.add_argument("--no-sync", action="store_true",
                      help="do not register rig models missing from models.yaml")
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
    p_ba.add_argument("--no-ladder", action="store_true",
                      help="standard cases only: no ladder packs")
    p_ba.add_argument("--escalate", action="store_true",
                      help="4.0: after every model's single pass, ONE retry pass for the rows "
                           "that ran out of budget, at the next rung only (off by default)")
    p_ba.add_argument("--no-escalate", action="store_true",
                      help="accepted for compatibility (escalation is off unless --escalate)")
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

    p_cases = sub.add_parser("cases", help="list / verify the case set")
    cs = p_cases.add_subparsers(dest="action", required=True)
    for name in ("list", "verify"):
        pc = cs.add_parser(name)
        pc.add_argument("--categories", default=None)
        pc.add_argument("--difficulty", default=None)
        pc.add_argument("--verbose", "-v", action="store_true")

    args = parser.parse_args(argv)

    if args.allow_unsandboxed:
        from . import graders
        graders.ALLOW_UNSANDBOXED = True
        # Anything we spawn (the GUI's own runs) inherits the decision.
        os.environ["CRUCIBLEFORGE_ALLOW_UNSANDBOXED"] = "1"

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
    config_optional = args.cmd in ("cases", "pack")
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
    setup_logging((results_dir() / "crucibleforge.log") if cfg else None)
    # a SIGTERM (queue script killed, `pkill`, systemd stop) must end the run
    # in seconds, mark it stopped and release the GPU lease (M10)
    install_stop_handler()

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
    handler = {"status": cmd_status, "run": cmd_run, "judge": cmd_judge,
               "recover": cmd_recover, "escalate": cmd_escalate, "report": cmd_report, "compare": cmd_compare, "calibrate": cmd_calibrate,
               "pack": cmd_pack, "pairwise": cmd_pairwise, "all": cmd_all,
               "gui": cmd_gui, "import-openclaw": cmd_import_openclaw,
               "models": cmd_models, "cases": cmd_cases, "bench-all": cmd_bench_all}[args.cmd]
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
    if stopflag.ABORT.is_set():
        rc = TERM_EXIT
    if v2_tracked:
        _v2_finish(args.cmd, args, cfg, rc or 0, v2_state)
    if stopflag.ABORT.is_set():
        _exit_after_stop(rc)
    sys.exit(rc or 0)


if __name__ == "__main__":
    main()
