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
import sys
from datetime import datetime, timezone
from pathlib import Path

from .config import (CATEGORIES, ConfigError, EXAMPLE_CONFIG_PATH,
                     USER_CONFIG_PATH, load_cases, load_config, resolve_models,
                     results_dir)

log = logging.getLogger("crucibleforge")

# WP-BENCH FIX-6 (r1-meta-schema.md, schema v1): the fleet-wide runs/<id>/
# {report.md,meta.json} write contract — deliberately NOT under this
# project's own `results/`, which is a different, tool-owned directory (see
# `results_dir()`). Fixed per-machine and absolute on purpose: every producer
# on the box writes here so ONE standing scanner (`runs-deliver`) can deliver
# all of them, independent of cwd/--results. Resolved via ``Path.home()``
# (never a literal username in the source — this is a public repo) with an
# env override for anyone whose fleet convention differs.
V2_RUNS_ROOT = Path(os.environ.get("CRUCIBLEFORGE_V2_RUNS_ROOT")
                    or (Path.home() / ".openclaw" / "workspace" / "runs"))
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
# runs/<id>/{report.md,meta.json} write contract (r1-meta-schema.md schema
# v1). Tracked for `run` and `all` only (see `main()`) — a whole CLI process
# is the "unit of work"; `cmd_all` calling `cmd_run` internally must not
# trip this twice, which is why it lives at the top-level dispatch and not
# inside cmd_run itself.

def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _mint_run_id() -> str:
    """Self-minted id per r1-meta-schema.md: w-<UTC stamp>-<4 hex>."""
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"w-{ts}-{secrets.token_hex(2)}"


_RUN_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,80}$")


def _resolve_run_id(args) -> str:
    """RUN_DIR:/RUN_ID: equivalent for a CLI caller: --run-id, else
    $CRUCIBLEFORGE_RUN_ID, else self-mint. A worker dispatched with a brief
    that names a run id never needs to invent one; an ad-hoc human/cron
    invocation gets one anyway, so the write contract is unconditional.

    Validated against r1-meta-schema.md's own rule ("id — must equal the
    directory name"): an unchecked id containing e.g. `/` or `..` writes
    outside V2_RUNS_ROOT with a directory name that does not match the `id`
    field it records — and puts the report where runs-deliver's
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
    return V2_RUNS_ROOT / run_id


def _atomic_write_json(path: Path, obj: dict) -> None:
    """tmp + fsync(file) + os.replace + fsync(dir) — meta.json is the commit
    marker the standing `runs-deliver` scanner keys on; a half-written file
    must never be visible at the final name. The directory fsync (WP-BENCH
    review M2, matching r1-meta-schema.md's own write order and the
    reference implementation in ~/.local/bin/runs-deliver's
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
    try:
        dir_fd = os.open(str(path.parent), os.O_RDONLY)
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
    r1-meta-schema.md's own example verbatim. producer/kind are constants —
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
    spawned us with a chosen --run-id (Flash does: producer ds_flash, kind
    spawn, requester_session = the asking session). 2026-09-08: we used to
    overwrite it wholesale, so the run's routing was lost and the standing
    scanner delivered the report to the bot's daily thread instead of the
    thread that asked for the bench. Returns {} when there is none."""
    try:
        p = _v2_run_dir(run_id) / "meta.json"
        if p.exists():
            d = json.loads(p.read_text(encoding="utf-8"))
            return d if isinstance(d, dict) else {}
    except (OSError, ValueError) as e:
        log.warning("V2 run-report: could not read existing meta.json for %s: %s", run_id, e)
    return {}


def _v2_start(args) -> dict:
    """FIRST act for a V2-tracked command, before any benchmark work at all:
    mint/resolve the run id and write meta.json with status "running". This
    is what lets the fleet-wide standing scanner flag a worker that dies
    mid-run as stale instead of it leaving no trace (r1-meta-schema.md
    "running-first" convention, matching workspace-ds-flash/AGENTS.md).

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
    state = {"run_id": run_id, "requester_session": requester, "thread_id": deliver_to,
             "task_run_id": task_run_id, "title": title, "created": created, "extra": extra}
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


def _v2_report_body(cmd: str, args, cfg: dict, rc: int, state: dict) -> str:
    """runs/<id>/report.md: the 5-heading contract (Result / Evidence /
    Files / Failed / Next) + the scorecard. runs-deliver posts only the
    ``## Result`` section when the report is long, so Result carries one
    self-contained line per model: ``label: Chat 88.7 · Coding 69.4 · 41 min``
    or ``label: FAILED — <the full error>``."""
    from .report import board_stats, run_minutes, scorecard_section, generate
    from .version import revision
    models_arg = getattr(args, "models", None)
    labels: list[str] = []
    try:
        entries = resolve_models(cfg, models_arg)
        labels = [e["name"] for e in entries]
    except Exception as e:  # noqa: BLE001 — a bad --models must not blank the report
        log.warning("V2 report: could not resolve --models %r: %s", models_arg, e)
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
        bits = [f"Chat {card['chat']:.1f}" if card.get("chat") is not None else "Chat -",
                f"Coding {card['coding']:.1f}" if card.get("coding") is not None else "Coding -"]
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
    end and bring the evicted residents back (a family bot's model should
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


def cmd_judge(args, cfg):
    from .judge import JudgeLeaseUnavailable, acquire_judge_lease, run_judge
    cfg, _, _ = _apply_profile_arg(args, cfg)
    entries = resolve_models(cfg, args.models)
    labels = [e["name"] for e in entries]
    samples = getattr(args, "samples", None)
    if getattr(args, "smoke", False):
        samples = 1  # keep smoke fast regardless of config
    # Reserve every StudioForge GPU for the judge BEFORE the per-model load
    # happens. The provider's ``lease: true`` path would do this inside
    # ``_lease_load`` — but only after the runner is partway through a
    # multi-minute 122B download, when a 507 has no context to act on.
    # Acquiring the lease up-front (Lloyd 2026-08-31: "block out all the gpus
    # when running the judge") lets the rig either grant the lease and plan
    # around the named model, or refuse fast with a message naming the
    # holder, instead of timing out 178 rows in.
    from .judge import pending_judge_rows
    if pending_judge_rows(labels, force=bool(getattr(args, "force", False))) == 0:
        # Judge-free profile (e.g. `coding`) or already judged: do not touch
        # the rig at all — a lease here would evict residents and vacate
        # ComfyUI for a phase that has nothing to do.
        log.info("nothing to judge for %s — rig untouched", labels)
        print("nothing to judge")
        return 0
    force_evict = getattr(args, "force_evict", False) or _judge_policy_force(cfg)
    # The guard's resident snapshot MUST be taken before the judge lease:
    # with bench-first the lease itself evicts the family bot's model, and a
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
        result = run_judge(cfg, labels, force=args.force, samples=samples,
                           judge_override=getattr(args, "judge", None),
                           allow_fallback=getattr(args, "judge_fallback", None),
                           allow_self_judge=bool(getattr(args, "allow_self_judge", False)))
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
                          out_dir=getattr(args, "out", None))
        except PrivateLeak as e:
            print(f"refused: {e}", file=sys.stderr)
            return 3
        print(scorecard_section(md))
        print(f"wrote the PUBLIC board (private categories excluded) to "
              f"{getattr(args, 'out', None) or public_dir()}")
        return 0
    md = generate(None if full_board else args.models, write=True)
    print(scorecard_section(md))
    print(f"wrote {report_md_path()}")
    print(f"      {failures_md_path()}")
    print(f"      {report_html_path()}")
    return 0


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
    """run -> judge -> report. A model-level generation failure (not served,
    OOM, lease refused, transport abort) is that MODEL's failure: it is
    recorded in its meta (the board and the run-report say FAILED and why) and
    the batch's exit code stays non-zero — but the judge phase still runs over
    every model the run phase got to (a model with no pending rows is a no-op
    there), and the board is rebuilt. Only a run phase that never started
    (nothing runnable, LM Studio busy without --yes) skips the later phases.
    Before 2026-09-28 any single failure returned here, so one unservable
    model in a 31-model backfill left the 25 good transcripts unjudged."""
    rc = cmd_run(args, cfg)
    ran = getattr(args, "ran_labels", None)
    if rc and not ran:
        return rc
    if rc:
        log.error("run phase: some models failed — judging the %d that ran: %s",
                  len(ran), ",".join(ran))
        args = copy.copy(args)
        args.models = ",".join(ran)
    # WP-BENCH review fix: cmd_judge signals failure by RETURNING non-zero in
    # two cases (4 = start-of-judge lease unavailable, 1 = rows errored) —
    # neither raises, so the bare `cmd_judge(args, cfg)` this used to be
    # silently discarded both and `all` could exit 0 with the judge phase
    # never having scored a single row (a delivered, confidently-worded V2
    # "succeeded" report for a run that did not succeed). Capture it.
    try:
        rc_j = cmd_judge(args, cfg)
    except Exception as e:  # noqa: BLE001 — objective results are still worth a report
        log.error("judge phase failed: %s — rendering the report without judged rows", e)
        rc_j = 1
    else:
        if rc_j:
            log.error("judge phase exited %d — rendering the report without judged rows", rc_j)
    return cmd_report(args, cfg, full_board=True) or rc or rc_j


def cmd_gui(args, cfg):
    from .gui.server import serve
    return serve(cfg_path=cfg.get("_path"), host=args.host, port=args.port,
                 token=args.token, open_browser=not args.no_open)


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
        return verify_all(cases, verbose=args.verbose)
    return 1


# ------------------------------------------------------------------- main

# ------------------------------------------------------------ --detach
# 2026-09-08: a worker's `exec` tool kills whatever it started after
# `tools.exec.timeoutSec` (default 1800 s) — the retry of run 2 died at
# exactly 30 min with one case left, no report, and its GPU lease orphaned.
# A bench must not live inside an agent's tool call at all: `--detach`
# re-launches this exact command as a transient systemd --user unit and
# returns at once, printing the unit and run id. The unit sources the
# gateway env file itself (the PIN never appears in argv), writes the same
# runs/<id>/{report.md,meta.json} contract, and survives the agent, the
# gateway, and any exec timeout. Poll: `systemctl --user is-active <unit>`
# and runs/<id>/meta.json `status`.
ENV_FILE = Path(os.environ.get("CRUCIBLEFORGE_ENV_FILE",
                               str(Path.home() / ".openclaw" / "gateway.systemd.env")))


# ------------------------------------------------------------- rig lock
# R4 (2026-09-23): one benchmark at a time, enforced by the tool itself.
# Two runs on one rig share the lease holder name and the GPUs; queue
# scripts used to take this flock by hand, and anyone who forgot got two
# runs corrupting each other's timings. The CLI now takes it for every
# command that touches the rig (run / all / judge / recover), BLOCKING with a
# log line. A parent that already holds it (a queue script's `flock
# results/.rig.lock …`) is detected by walking the process ancestry for an
# open fd on the lock file, so the child does not deadlock on its own parent.
RIG_LOCK_CMDS = ("run", "all", "judge", "recover")


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
            stat = Path(f"/proc/{pid}/stat").read_text()
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
    f = open(path, "a+")
    try:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        f.seek(0)
        holder = f.read().strip()[:200]
        log.warning("another CrucibleForge benchmark holds %s (%s) — waiting for it to "
                    "finish (one benchmark at a time)", path, holder or "holder unknown")
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)
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
    script = (f"set -a; [ -r {shlex.quote(str(ENV_FILE))} ] && . {shlex.quote(str(ENV_FILE))}; set +a; "
              f"cd {shlex.quote(cwd)} && exec uv run crucibleforge {inner_cmd}")
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
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print(f"detach failed (systemd-run rc={r.returncode}): {(r.stderr or r.stdout).strip()[:400]}",
              file=sys.stderr)
        return r.returncode or 1
    run_dir = _v2_run_dir(run_id)
    print(f"detached: unit={unit} run_id={run_id}")
    print(f"  status: systemctl --user is-active {unit}   (active = running, inactive = finished)")
    print(f"  result: {run_dir}/meta.json (status done|failed) and report.md")
    print(f"  log:    journalctl --user -u {unit} -n 50")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="crucibleforge",
        description="CrucibleForge — LLM capability benchmark (hard tier, deterministic "
                    "graders, local or remote judge, web GUI)")
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
                            f"{V2_RUNS_ROOT}/<id>/{{report.md,meta.json}} (else "
                            "$CRUCIBLEFORGE_RUN_ID, else self-minted)")
        p.add_argument("--deliver-to", default=None,
                       help="DisPatch thread id to route the V2 run report to "
                            "(else $CRUCIBLEFORGE_DELIVER_TO, else the standing scanner "
                            "falls back to the daily thread)")
        p.add_argument("--requester", default=None,
                       help="gateway session key that dispatched this run, e.g. "
                            "agent:main:daily-main-... (else $CRUCIBLEFORGE_REQUESTER)")
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
    p_judge.add_argument("--samples", type=int, default=None)
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
    p_report = sub.add_parser("report", help="generate comparison report")
    p_report.add_argument("--models", default=None)
    p_report.add_argument("--public", action="store_true",
                          help="build the board for an audience other than you: private "
                               "categories (cases/private/, a profile's private: list) are "
                               "left out and every file is leak-checked before writing; "
                               "writes results/public/ (or --out), never the local board")
    p_report.add_argument("--out", default=None, help="with --public: output directory")
    p_pw = sub.add_parser("pairwise", help="head-to-head A/B Elo on creative categories")
    p_pw.add_argument("--models", default="all")
    p_pw.add_argument("--categories", default="rp,nsfw,story")
    p_pw.add_argument("--judge", default=None)
    p_pw.add_argument("--yes", action="store_true")
    p_all = sub.add_parser("all", help="run + judge + report")
    add_run_args(p_all)
    p_all.add_argument("--force", action="store_true",
                       help="re-judge rows that already have verdicts; also lets --fresh "
                            "combine with --cases/--categories (archives the whole set)")

    p_gui = sub.add_parser("gui", help="web GUI (local, no CDN)")
    p_gui.add_argument("--host", default="127.0.0.1")
    p_gui.add_argument("--port", type=int, default=8777)
    p_gui.add_argument("--token", default=None,
                       help="access token (required when binding a non-loopback host; "
                            "auto-generated if omitted)")
    p_gui.add_argument("--no-open", action="store_true", help="don't open a browser tab")

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

    if args.results:
        os.environ["CRUCIBLEFORGE_RESULTS"] = args.results
        from .config import set_results_dir
        set_results_dir(Path(args.results))
    # `cases` reads only the case files that ship with the repo — no provider,
    # no models, no results. A clean checkout (CI) has no models.yaml, so
    # requiring one here would fail `cases verify` on every fresh runner.
    config_optional = args.cmd == "cases"
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
    # a SIGTERM (queue script killed, `pkill`) must still release the GPU
    # lease and restore residents: turn it into SystemExit so the `finally`
    # blocks and atexit handlers run instead of the process just vanishing
    import signal

    def _term(signum, frame):
        raise SystemExit(128 + signum)
    signal.signal(signal.SIGTERM, _term)

    # Fail fast: refuse before a multi-hour run rather than at the first
    # coding case. "cases list" and the read-only commands never execute
    # anything, so they are not gated.
    from . import graders
    executes_code = args.cmd in {"run", "all", "recover", "gui"} or (
        args.cmd == "cases" and getattr(args, "action", None) == "verify")
    if executes_code:
        try:
            graders.require_sandbox()
        except graders.SandboxUnavailable as e:
            print(f"sandbox: {e}", file=sys.stderr)
            sys.exit(3)

    handler = {"status": cmd_status, "run": cmd_run, "judge": cmd_judge,
               "recover": cmd_recover, "report": cmd_report, "pairwise": cmd_pairwise, "all": cmd_all,
               "gui": cmd_gui, "import-openclaw": cmd_import_openclaw,
               "models": cmd_models, "cases": cmd_cases}[args.cmd]
    # WP-BENCH FIX-6: `run`/`all` are the two commands that do real benched
    # work end to end, so they are the ones tracked by the fleet-wide
    # runs/<id>/{report.md,meta.json} contract. This wraps the dispatch
    # itself (not cmd_run's body) so `cmd_all` calling `cmd_run` internally
    # writes the contract exactly once, for the WHOLE all=run+judge+report
    # unit of work, not a premature "done" the moment run() alone finishes.
    v2_tracked = args.cmd in ("run", "all", "judge")
    if v2_tracked and getattr(args, "detach", False):
        sys.exit(_detach(args, argv))
    v2_state = _v2_start(args) if v2_tracked else None
    rig_lock = None
    try:
        if args.cmd in RIG_LOCK_CMDS and not _remote_judge_only(args, cfg):
            rig_lock = acquire_rig_lock()  # noqa: F841 — held until exit
        rc = handler(args, cfg)
    except ConfigError as e:
        print(f"config error: {e}", file=sys.stderr)
        rc = 2
    except BaseException as e:
        if v2_tracked:
            _v2_finish(args.cmd, args, cfg, _exit_code_from_exception(e), v2_state)
        raise
    if v2_tracked:
        _v2_finish(args.cmd, args, cfg, rc or 0, v2_state)
    sys.exit(rc or 0)


if __name__ == "__main__":
    main()
