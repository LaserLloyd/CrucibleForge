"""crucibleforge CLI — status / run / judge / report / pairwise / all / gui / config
/ import-openclaw / models / cases.

    crucibleforge status
    crucibleforge all --smoke --models a,b --yes            # quick end-to-end
    crucibleforge run --models deepseek-flash --categories math,coding --difficulty hard
    crucibleforge judge --judge deepseek:deepseek-v4-flash   # judge on a remote model
    crucibleforge report
    crucibleforge gui                                       # http://127.0.0.1:8777

Shared-server etiquette (LM Studio): the bench unloads whatever the local
server is serving. It snapshots the loaded model at start and restores it at
the end, but other clients will still see their model swapped out mid-run —
prefer running overnight or with other consumers stopped.
"""
from __future__ import annotations

import argparse
import logging
import os
import shutil
import sys
from pathlib import Path

from .config import (CATEGORIES, ConfigError, EXAMPLE_CONFIG_PATH,
                     USER_CONFIG_PATH, load_cases, load_config, resolve_models,
                     results_dir)

log = logging.getLogger("crucibleforge")


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


class _ProviderGuard:
    """Leave the rig as we found it. LM Studio: snapshot/restore the served
    model. StudioForge: snapshot the residents, release our GPU lease at the
    end and bring the evicted residents back (a family bot's model should
    not stay cold because a benchmark ran)."""

    def __init__(self, cfg, entries, include_judge: bool = True):
        from .providers import provider_for, get_provider
        self.provs = {}
        for e in entries:
            p = provider_for(cfg, e)
            if p.type in ("lmstudio", "studioforge"):
                self.provs[p.name] = p
        if include_judge:
            for cand in (cfg.get("judge") or {}).get("candidates", []):
                try:
                    p = get_provider(cfg, cand["provider"])
                except Exception:
                    continue
                if p.type in ("lmstudio", "studioforge") and p.name not in self.provs:
                    self.provs[p.name] = p
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
        print(f"  {p.name:14s} {p.type:11s} {p.base_url:45s} {state:4s} "
              f"conc={p.concurrency} {key}{keyset}")
        loaded = p.loaded_models()
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
                print(f"    leases: {[(l.get('holder'), l.get('devices'), l.get('model_ids')) for l in leases] or 'none'}"
                      f"   lease mode: {'ON' if p.lease else 'off'}"
                      f"{' (no X-MCP-Pin header configured!)' if p.lease and not any(k.lower() == 'x-mcp-pin' for k in p.headers) else ''}")
            except Exception as e:  # noqa: BLE001
                print(f"    (management API: {e})")
    print("\nregistry (models):")
    for m in cfg["models"]:
        flag = "enabled " if m.get("enabled", True) else "disabled"
        price = m.get("price")
        pr = f" ${price.get('input', 0)}/{price.get('output', 0)} per 1M" if price else ""
        print(f"  [{flag}] {m['name']:28s} {m['provider']:12s} {m['model_id'][:60]:60s}{pr}")
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
    from .profiles import list_profiles
    print(f"profiles: {', '.join(list_profiles(cfg)) or 'none'}")
    return 0


def _apply_profile_arg(args, cfg):
    """--profile: returns (cfg, cases, profile) or (cfg, None, None)."""
    name = getattr(args, "profile", None)
    if not name:
        return cfg, None, None
    from .profiles import apply_profile, load_profile, profile_judge
    prof = load_profile(name, cfg)
    cfg2, cases = apply_profile(prof, cfg, smoke=getattr(args, "smoke", False))
    if not getattr(args, "judge", None):
        args.judge = profile_judge(prof)
    log.info("profile %s: %d cases, thinking cap %s, judge %s", name, len(cases),
             cfg2["defaults"].get("thinking_max_tokens_cap"),
             (args.judge.get("model_id") if isinstance(args.judge, dict) else args.judge) or "auto")
    return cfg2, cases, prof


def cmd_run(args, cfg):
    from .runner import run_models
    cfg, prof_cases, prof = _apply_profile_arg(args, cfg)
    entries = resolve_models(cfg, args.models)
    if prof_cases is not None:
        cases = prof_cases
        if args.categories:
            want = set(_parse_list(args.categories))
            cases = [c for c in cases if c["category"] in want]
    else:
        cases = load_cases(_parse_list(args.categories), smoke=args.smoke,
                           difficulties=_parse_list(getattr(args, "difficulty", None)))
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
    if not getattr(args, "no_link_check", False):
        from .preflight import check_link_health
        check_link_health(cfg, entries)
    log.info("run: models=%s cases=%d smoke=%s fresh=%s",
             [e["name"] for e in entries], len(cases), args.smoke,
             getattr(args, "fresh", False))
    if getattr(args, "fresh", False):
        _archive_labels([e["name"] for e in entries])

    guard = _ProviderGuard(cfg, entries)
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
    print("\nrun summary:")
    _print_run_summary(summary)
    return _run_rc(summary, [e["name"] for e in entries])


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
    guard = _ProviderGuard(cfg, entries)
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
    from .judge import run_judge
    cfg, _, _ = _apply_profile_arg(args, cfg)
    entries = resolve_models(cfg, args.models)
    labels = [e["name"] for e in entries]
    samples = getattr(args, "samples", None)
    if getattr(args, "smoke", False):
        samples = 1  # keep smoke fast regardless of config
    guard = _ProviderGuard(cfg, [])
    try:
        result = run_judge(cfg, labels, force=args.force, samples=samples,
                           judge_override=getattr(args, "judge", None),
                           allow_fallback=getattr(args, "judge_fallback", None))
    finally:
        guard.restore()
    print(f"judged {result['judged']} rows "
          f"({result['failed']} unparsable judge verdicts, "
          f"{result.get('empty', 0)} empty generations, "
          f"{result.get('errored', 0)} errored, samples={result.get('samples')}) "
          f"with {result.get('judge')}")
    return 1 if result.get("errored") else 0


def cmd_report(args, cfg):
    from .report import generate, report_md_path
    print(generate(args.models))
    print(f"\nwrote {report_md_path()}")
    return 0


def cmd_pairwise(args, cfg):
    from .pairwise import run_pairwise, render_pairwise_md
    entries = resolve_models(cfg, args.models)
    labels = [e["name"] for e in entries]
    if len(labels) < 2:
        raise SystemExit("pairwise needs >= 2 models (--models a,b)")
    cats = _parse_list(args.categories) or ["rp", "nsfw"]
    results = run_pairwise(cfg, labels, cats, judge_override=getattr(args, "judge", None))
    md = render_pairwise_md(results)
    (results_dir() / "pairwise.md").write_text(md, encoding="utf-8")
    print(md)
    print(f"\nwrote {results_dir() / 'pairwise.md'}")
    return 0


def cmd_all(args, cfg):
    rc = cmd_run(args, cfg)
    if rc:
        return rc
    try:
        cmd_judge(args, cfg)
    except Exception as e:  # noqa: BLE001 — objective results are still worth a report
        log.error("judge phase failed: %s — rendering the report without judged rows", e)
        rc = 1
    return cmd_report(args, cfg) or rc


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
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(EXAMPLE_CONFIG_PATH, dest)
        print(f"wrote {dest} — edit providers/models, then `crucibleforge status`")
        return 0
    if args.upgrade:
        cfg = load_config(args.config)
        path = Path(cfg["_path"])
        backup = path.with_suffix(f".yaml.bak-v2")
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

    def add_run_args(p):
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
                       help="run profile (profiles/<name>.yaml): fixed case subset + budgets + judge")
        p.add_argument("--judge-fallback", action="store_true",
                       help="with --judge: allow the next judge candidate if the forced "
                            "judge cannot be loaded (default: strict — the judge phase fails)")
        p.add_argument("--judge", default=None,
                       help="force a judge: provider:model_id (must not be under test)")
        p.add_argument("--no-link-check", action="store_true",
                       help="skip the pre-flight provider data-channel probe")

    sub.add_parser("status", help="providers, registry, judge, cases")
    p_run = sub.add_parser("run", help="benchmark models")
    add_run_args(p_run)
    p_judge = sub.add_parser("judge", help="judge pending quality rows")
    p_judge.add_argument("--models", default="all")
    p_judge.add_argument("--force", action="store_true",
                         help="re-judge rows that already have verdicts")
    p_judge.add_argument("--samples", type=int, default=None)
    p_judge.add_argument("--judge", default=None, help="provider:model_id")
    p_judge.add_argument("--judge-fallback", action="store_true",
                         help="allow the next candidate if the forced --judge cannot load "
                              "(default: strict, the phase fails instead)")
    p_judge.add_argument("--profile", default=None, help="use the profile's judge/budgets")
    p_recover = sub.add_parser(
        "recover", help="re-run reasoning-overflow rows (empty answers) through recovery")
    p_recover.add_argument("--models", default="all")
    p_recover.add_argument("--yes", action="store_true")
    p_recover.add_argument("--profile", default=None)
    p_recover.add_argument("--judge", default=None, help=argparse.SUPPRESS)
    p_recover.add_argument("--no-link-check", action="store_true")
    p_report = sub.add_parser("report", help="generate comparison report")
    p_report.add_argument("--models", default=None)
    p_pw = sub.add_parser("pairwise", help="head-to-head A/B Elo on creative categories")
    p_pw.add_argument("--models", default="all")
    p_pw.add_argument("--categories", default="rp,nsfw")
    p_pw.add_argument("--judge", default=None)
    p_pw.add_argument("--yes", action="store_true")
    p_all = sub.add_parser("all", help="run + judge + report")
    add_run_args(p_all)
    p_all.add_argument("--force", action="store_true")

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
    try:
        cfg = load_config(args.config)
    except ConfigError as e:
        setup_logging()
        print(f"config error: {e}", file=sys.stderr)
        sys.exit(2)
    if args.results:
        from .config import set_results_dir
        set_results_dir(Path(args.results))
    setup_logging(results_dir() / "crucibleforge.log")
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
    try:
        rc = handler(args, cfg)
    except ConfigError as e:
        print(f"config error: {e}", file=sys.stderr)
        rc = 2
    sys.exit(rc or 0)


if __name__ == "__main__":
    main()
