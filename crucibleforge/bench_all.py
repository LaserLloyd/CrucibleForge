"""`crucibleforge bench-all` — the whole-board refresh, one idempotent command.

    uv run crucibleforge bench-all                  # the PLAN only (touches nothing)
    uv run crucibleforge bench-all --go --detach --run-id bench-all-<date>

What it does, in order (plan first, then — with ``--go`` — execute):

1. **Enumerate the rig** — the StudioForge provider's live model list.
2. **Sync the registry** — a chat model on the rig with no ``models.yaml``
   entry is appended (text append: the file's comments survive), labelled from
   its file name, with a dated note. Embedding models and the profile's judge
   are never added (the judge cannot score itself). Disabled entries are left
   disabled: ``enabled: false`` is a decision, not drift.
3. **Prune the board** — result files of a board label whose model is gone
   from the rig, disabled, or unregistered move to
   ``results/archive-<date>-pruned/`` (reversible: move them back).
4. **Generate only what is missing** — per enabled rig model, the benchmark
   case ids with no usable current row (no row, or only a transport-errored
   one). Models with the same missing set run together in one ``run
   --cases …`` (never ``--fresh``: that would discard valid rows); hosted-API
   models only with ``--include-api`` (metered).
5. **Judge** once over every model with pending rows, plus ONE retry of a
   failed verdict (``judge --retry-failed``, capped per row).
6. **Rebuild both boards** — ``report`` (local, private components) and
   ``report --public`` (scrubbed, leak-checked).

Lease etiquette is the normal one: each model load and the judge phase take
and release their own lease under the provider's bench-first policy; the rig
lock (results/.rig.lock) is held for the whole command, so no other bench
interleaves. ``503 priority_hold`` is waited out by the client.

Exit codes: 0 done (or nothing to do) · 1 some model failed (the rest were
still judged and boarded; per-model notes in the report) · 3 the public board
refused to write (a private term leaked) · 4 the rig is busy (judge lease
refused) — back off and re-run later · 5 the rig is unreachable (no listing).
"""
from __future__ import annotations

import argparse
import logging
import re
import shutil
from datetime import datetime
from pathlib import Path

log = logging.getLogger(__name__)

EXIT_OK, EXIT_MODEL_FAILED, EXIT_PUBLIC_LEAK, EXIT_RIG_BUSY, EXIT_RIG_DOWN = 0, 1, 3, 4, 5

#: rig ids that are never contestants (not chat models)
_NOT_CHAT = re.compile(r"embed|rerank|whisper|clip|tts", re.IGNORECASE)
#: a row with only these errors is retried (a template error is permanent: the
#: server cannot render that model's template — re-running changes nothing)
_RETRYABLE_ERRORS = {"transport", "timeout", "generation", None}
#: server messages that mean the case can never run on this model as loaded
#: (the prompt is longer than the model's context) — not worth a retry
_PERMANENT_MESSAGES = ("does not fit the context",)
#: a case that errored this many times (across runs) is left errored — the
#: board shows "N rows errored" — instead of being retried by every bench-all
MAX_CASE_ATTEMPTS = 2


# ------------------------------------------------------------------ plan

def _label_for(model_id: str, taken: set[str]) -> str:
    """A registry label from a rig id: the file stem, lowercased, quant
    suffix kept (two quants of one model stay distinguishable)."""
    stem = model_id.rsplit("/", 1)[-1]
    lab = re.sub(r"[^a-z0-9.]+", "-", stem.lower()).strip("-")[:48].strip("-")
    base, n = lab, 2
    while lab in taken:
        lab, n = f"{base}-{n}", n + 1
    return lab


def _permanent_error(r: dict) -> bool:
    return (r.get("error_kind") not in _RETRYABLE_ERRORS
            or any(m in str(r.get("error") or "") for m in _PERMANENT_MESSAGES))


def missing_cases(rows: list[dict], case_ids: list[str],
                  attempts: dict[str, int] | None = None) -> list[str]:
    """Benchmark case ids with no usable current row. ``rows`` = the label's
    board rows (report.board_filter). A case counts as done when it has any
    row that is not a retryable error (a pass, a fail, a skipped long-context
    case, a template error, a prompt longer than the model's context), or when
    it has already errored ``MAX_CASE_ATTEMPTS`` times (``attempts``: errored
    rows per case id across the label's whole transcript history)."""
    done = set()
    for r in rows:
        if r.get("grade") != "error" or _permanent_error(r):
            done.add(r.get("case_id"))
    attempts = attempts or {}
    return [c for c in case_ids
            if c not in done and attempts.get(c, 0) < MAX_CASE_ATTEMPTS]


def error_attempts(label: str) -> dict[str, int]:
    """Errored benchmark rows per case id over the label's whole history."""
    from .config import load_transcripts
    from .profiles import DEFAULT_PROFILE
    out: dict[str, int] = {}
    for r in load_transcripts(label):
        if r.get("profile") == DEFAULT_PROFILE and r.get("grade") == "error":
            out[r.get("case_id")] = out.get(r.get("case_id"), 0) + 1
    return out


def plan(cfg: dict, rig_ids: set[str], *, include_api: bool = False,
         board_labels: list[str] | None = None, rows_for=None,
         judge_id: str | None = None, case_ids: list[str] | None = None,
         pending_for=None, attempts_for=None, first: list[str] | None = None) -> dict:
    """What bench-all would do — computed, nothing touched. Pure given its
    inputs (the tests drive it with fakes)."""
    from . import report
    from .judge import needs_verdict
    from .profiles import DEFAULT_PROFILE, apply_profile, load_profile, profile_judge
    if case_ids is None:
        _, cases = apply_profile(load_profile(DEFAULT_PROFILE, cfg), cfg)
        case_ids = [c["id"] for c in cases]
    if judge_id is None:
        j = profile_judge(load_profile(DEFAULT_PROFILE, cfg))
        judge_id = j.get("model_id") if isinstance(j, dict) else j
    rows_for = rows_for or (lambda label: report.board_filter(label, cfg))
    attempts_for = attempts_for or error_attempts
    if pending_for is None:
        def pending_for(label):
            from .config import load_transcripts
            return sum(1 for r in load_transcripts(label) if needs_verdict(r, retry_failed=True))
    prov_type = {n: (p or {}).get("type") for n, p in (cfg.get("providers") or {}).items()}
    models = cfg.get("models") or []
    by_name = {m["name"]: m for m in models}
    registered = {m["model_id"] for m in models}
    taken = set(by_name)

    add, skipped_rig = [], []
    for mid in sorted(rig_ids - registered):
        if _NOT_CHAT.search(mid):
            skipped_rig.append((mid, "not a chat model"))
        elif mid == judge_id:
            skipped_rig.append((mid, "the benchmark's judge (cannot score itself)"))
        else:
            lab = _label_for(mid, taken)
            taken.add(lab)
            add.append({"name": lab, "model_id": mid})

    def on_rig(m):
        return prov_type.get(m.get("provider")) == "studioforge" and m["model_id"] in rig_ids

    def is_api(m):
        return prov_type.get(m.get("provider")) not in ("studioforge", "lmstudio")

    candidates, not_on_rig = [], []
    for m in models:
        if m.get("enabled", True) is False:
            continue
        if on_rig(m) or (include_api and is_api(m)):
            candidates.append(m["name"])
        elif prov_type.get(m.get("provider")) == "studioforge":
            not_on_rig.append(m["name"])
    candidates += [a["name"] for a in add]

    prune = []
    for label in board_labels or []:
        m = by_name.get(label)
        if m is None:
            prune.append((label, "no registry entry"))
        elif m.get("enabled", True) is False:
            prune.append((label, "disabled in models.yaml"))
        elif prov_type.get(m.get("provider")) == "studioforge" and m["model_id"] not in rig_ids:
            prune.append((label, "no longer on the rig"))

    groups: dict[tuple, list[str]] = {}
    for label in candidates:
        new = label in {a["name"] for a in add}
        miss = tuple(missing_cases([] if new else rows_for(label), case_ids,
                                   {} if new else attempts_for(label)))
        if miss:
            groups.setdefault(miss, []).append(label)
    ordered = []
    first = [f for f in (first or []) if f]
    for miss, labels in groups.items():       # --first: their own groups, ahead
        head = [l for l in labels if l in first]
        if head:
            ordered.insert(sum(1 for o in ordered if o[2]), (miss, head, True))
        rest = [l for l in labels if l not in first]
        if rest:
            ordered.append((miss, rest, False))
    to_judge = sorted({l for l in candidates if pending_for(l)}
                      | {l for labels in groups.values() for l in labels})
    return {"rig_models": len(rig_ids), "add": add, "skipped_rig": skipped_rig,
            "candidates": candidates, "not_on_rig": not_on_rig, "prune": prune,
            "groups": [{"models": labels, "cases": list(miss)} for miss, labels, _ in ordered],
            "judge": to_judge, "case_count": len(case_ids)}


def nothing_to_do(p: dict) -> bool:
    return not (p["add"] or p["prune"] or p["groups"] or p["judge"])


def format_plan(p: dict) -> str:
    L = [f"rig: {p['rig_models']} models · benchmark: {p['case_count']} cases · "
         f"contestants on the rig: {len(p['candidates'])}"]
    for a in p["add"]:
        L.append(f"  + register {a['name']}  ({a['model_id']})")
    for mid, why in p["skipped_rig"]:
        L.append(f"  · not registered: {mid} — {why}")
    for label in p["not_on_rig"]:
        L.append(f"  ! {label}: enabled but not on the rig — skipped (disable it or re-download)")
    for label, why in p["prune"]:
        L.append(f"  - off the board: {label} ({why})")
    for g in p["groups"]:
        L.append(f"  > generate {len(g['cases'])} case(s) for {', '.join(g['models'])}")
    if p["judge"]:
        L.append(f"  > judge pending rows of {', '.join(p['judge'])}")
    if nothing_to_do(p):
        L.append("  nothing to do — every contestant is complete and judged")
    return "\n".join(L)


# --------------------------------------------------------------- actions

def append_registry(path: str | Path, add: list[dict], provider: str, ctx: int = 32768) -> None:
    """Add entries to the END of the ``models:`` list of models.yaml as TEXT
    (save_config would drop every comment in the file). The result is parsed
    before it replaces the file and must hold exactly the new entries more —
    otherwise nothing is written (ValueError)."""
    import yaml
    if not add:
        return
    path = Path(path)
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)
    top = [i for i, ln in enumerate(lines) if re.match(r"^[A-Za-z_][\w-]*:", ln)]
    try:
        m_at = next(i for i in top if lines[i].startswith("models:"))
    except StopIteration:
        raise ValueError(f"{path}: no top-level models: key") from None
    nxt = next((i for i in top if i > m_at), len(lines))
    # comments/blank lines right above the next key belong to that key
    ins = nxt
    while ins - 1 > m_at and (not lines[ins - 1].strip() or lines[ins - 1].lstrip().startswith("#")):
        ins -= 1
    item = next((ln for ln in lines[m_at + 1:nxt] if ln.lstrip().startswith("- ")), "- ")
    ind = item[:len(item) - len(item.lstrip())]
    stamp = datetime.now().strftime("%Y-%m-%d")
    block = ""
    for a in add:
        block += (f"{ind}# {stamp}: added by `crucibleforge bench-all` — on the rig, not registered.\n"
                  f"{ind}- name: {a['name']}\n"
                  f"{ind}  model_id: {a['model_id']}\n"
                  f"{ind}  context_length: {ctx}\n"
                  f"{ind}  provider: {provider}\n"
                  f"{ind}  tags:\n{ind}  - rig\n{ind}  - auto-added\n")
    if ins > 0 and not lines[ins - 1].endswith("\n"):
        lines[ins - 1] += "\n"
    out = "".join(lines[:ins]) + block + "".join(lines[ins:])
    before = len((yaml.safe_load(text) or {}).get("models") or [])
    after_doc = yaml.safe_load(out) or {}
    names = [m.get("name") for m in after_doc.get("models") or []]
    if len(names) != before + len(add) or not all(a["name"] in names for a in add):
        raise ValueError(f"{path}: registry append did not parse as intended — nothing written")
    tmp = path.with_suffix(".yaml.tmp")
    tmp.write_text(out, encoding="utf-8")
    tmp.replace(path)


def prune_results(results: Path, labels: list[str]) -> Path | None:
    if not labels:
        return None
    dest = results / f"archive-{datetime.now().strftime('%Y-%m-%d')}-pruned"
    dest.mkdir(parents=True, exist_ok=True)
    for label in labels:
        for name in (f"transcripts_{label}.jsonl", f"meta_{label}.json"):
            src = results / name
            if src.exists():
                shutil.move(str(src), str(dest / name))
    readme = dest / "README.txt"
    with readme.open("a", encoding="utf-8") as f:
        f.write(f"{datetime.now().isoformat(timespec='seconds')}: bench-all pruned "
                f"{', '.join(labels)} (gone from the rig, disabled or unregistered). "
                "Restore = move the files back to results/.\n")
    return dest


def execute(args, cfg: dict) -> int:
    """The body of `crucibleforge bench-all` (called from cli with the rig lock held)."""
    from . import cli
    from .config import load_config, results_dir
    from .providers import get_provider
    sf = [n for n, p in (cfg.get("providers") or {}).items() if (p or {}).get("type") == "studioforge"]
    if not sf:
        print("bench-all: no studioforge provider in models.yaml")
        return EXIT_RIG_DOWN
    rig_ids = set(get_provider(cfg, sf[0]).list_models(refresh=True))
    if not rig_ids:
        print("bench-all: the rig returned no model list — is the rig up?")
        return EXIT_RIG_DOWN
    board = sorted({p.stem.removeprefix("transcripts_") for p in results_dir().glob("transcripts_*.jsonl")}
                   | {p.stem.removeprefix("meta_") for p in results_dir().glob("meta_*.json")})
    first = [x.strip() for x in (getattr(args, "first", None) or "").split(",") if x.strip()]
    p = plan(cfg, rig_ids, include_api=args.include_api, board_labels=board, first=first)
    print(format_plan(p))
    args.bench_all_plan = p
    # the run report names what this refresh touches — set now, so a report
    # written after an interrupt (SIGTERM mid-judge) says the same
    touched = sorted({m for g in p["groups"] for m in g["models"]} | set(p["judge"]))
    args.models = ",".join(touched) or None
    if not args.go:
        print("\n(plan only — add --go to execute)")
        return EXIT_OK
    notes: list[str] = []
    if p["add"] and not args.no_sync:
        append_registry(cfg["_path"], p["add"], sf[0])
        here = results_dir()
        cfg = load_config(cfg["_path"])
        from .config import set_results_dir
        set_results_dir(here)
        notes.append("registered: " + ", ".join(a["name"] for a in p["add"]))
    if p["prune"] and not args.no_prune:
        dest = prune_results(results_dir(), [l for l, _ in p["prune"]])
        notes.append(f"pruned to {dest.name}: " + ", ".join(f"{l} ({w})" for l, w in p["prune"]))
    for label in p["not_on_rig"]:
        notes.append(f"{label}: enabled but not on the rig — skipped")
    rc = EXIT_OK
    for g in p["groups"]:
        run_args = argparse.Namespace(
            cmd="run", models=",".join(g["models"]), cases=",".join(g["cases"]),
            categories=None, difficulty=None, fresh=False, force=False, smoke=False,
            yes=True, no_link_check=False, judge=None, force_evict=False, profile=None)
        log.info("bench-all: generating %d case(s) for %s", len(g["cases"]), run_args.models)
        try:
            r = cli.cmd_run(run_args, cfg)
        except SystemExit as e:        # a group-level refusal must not stop the others
            r = e.code if isinstance(e.code, int) else 1
            notes.append(f"generation for {run_args.models} stopped: {e}")
        if r:
            rc = EXIT_MODEL_FAILED
    if p["judge"] or p["groups"]:
        judge_models = sorted(set(p["judge"]) | {m for g in p["groups"] for m in g["models"]})
        j_args = argparse.Namespace(cmd="judge", models=",".join(judge_models), force=False,
                                    retry_failed=True, samples=None, smoke=False, judge=None,
                                    judge_fallback=None, allow_self_judge=False,
                                    force_evict=False, profile=None)
        try:
            rj = cli.cmd_judge(j_args, cfg)
        except Exception as e:  # noqa: BLE001 — the boards are still worth rebuilding
            log.error("bench-all: judge phase failed: %s", e)
            notes.append(f"judge phase failed: {e}")
            rj = 1
        if rj == 4:
            notes.append("judge lease refused — the rig is busy; re-run bench-all later")
            rc = rc or EXIT_RIG_BUSY
        elif rj:
            rc = rc or EXIT_MODEL_FAILED
    rep = argparse.Namespace(models=None, public=False, out=None)
    cli.cmd_report(rep, cfg, full_board=True)
    pub = argparse.Namespace(models=None, public=True, out=None)
    if cli.cmd_report(pub, cfg, full_board=True) == 3:
        notes.append("PUBLIC board refused: a private term would have leaked — nothing written")
        rc = rc or EXIT_PUBLIC_LEAK
    args.bench_all_notes = notes
    for n in notes:
        print(f"note: {n}")
    return rc
