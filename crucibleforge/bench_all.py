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
#: 2026-10-24: a gemma4 model benched WITHOUT ``extra_body.reasoning_format``
#: leaks its thinking channel into the answer as literal ``<|channel>thought``
#: text — every row truncates, the deterministic graders all score 0 and the
#: model is never classified as a thinking model (no x4 token budget). The rig
#: reports the architecture of every model it serves, so an auto-registered
#: gemma4 gets the flag at REGISTRATION time.
_REASONING_ARCHES = {"gemma4"}
_REASONING_FORMAT = "deepseek"
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
                  attempts: dict[str, int] | None = None,
                  repeats: dict[str, int] | None = None) -> list[str]:
    """Benchmark case ids with no usable current row. ``rows`` = the label's
    CURRENT board rows (report.board_filter — never the old-values view). A
    case counts as done when it has a usable row (not a retryable error: a
    pass, a fail, a skipped long-context case, a template error, a prompt
    longer than the model's context) for each of its ``repeats`` (default 1;
    3.8.0 runs the deterministic categories 3 times), or when it has already
    errored ``MAX_CASE_ATTEMPTS`` times (``attempts``: errored rows per case id
    across the label's whole transcript history). A case short of repeats is
    re-run whole (``run --cases`` runs every repeat; the newest row of each
    repeat counts)."""
    reps: dict[str, set] = {}
    for r in rows:
        if r.get("grade") != "error" or _permanent_error(r):
            reps.setdefault(r.get("case_id"), set()).add(r.get("repeat") or 1)
    attempts = attempts or {}
    repeats = repeats or {}
    return [c for c in case_ids
            if len(reps.get(c, ())) < repeats.get(c, 1)
            and attempts.get(c, 0) < MAX_CASE_ATTEMPTS]


def error_attempts(label: str) -> dict[str, int]:
    """Errored benchmark rows per case id over the label's history at the
    CURRENT suite revision (an error under an older regime says nothing about
    whether the case can run now — 3.8.0 re-runs every case)."""
    from .config import load_transcripts
    from .profiles import DEFAULT_PROFILE
    from .version import row_current
    out: dict[str, int] = {}
    for r in load_transcripts(label):
        # 3.10 (D1): an error counts while its row answers the current case
        # content under the current regime (an edited case starts over)
        if (r.get("profile") == DEFAULT_PROFILE and r.get("grade") == "error"
                and row_current(r)):
            out[r.get("case_id")] = out.get(r.get("case_id"), 0) + 1
    return out


def rig_arches(prov) -> dict[str, str]:
    """model_id → architecture, from the rig's own listing (best effort).

    Empty when the listing cannot be read — the caller then registers the
    model exactly as before (never a guessed architecture)."""
    try:
        from . import studioforge
        records = studioforge.list_models_full(prov.base_url, prov.api_key)
    except Exception as e:  # noqa: BLE001 — an unreadable listing is not fatal
        log.warning("rig architecture listing unavailable: %s", e)
        return {}
    return {m.get("id"): str(m.get("arch") or "").lower()
            for m in records if m.get("id")}


def plan(cfg: dict, rig_ids: set[str], *, include_api: bool = False,
         board_labels: list[str] | None = None, rows_for=None,
         judge_id: str | None = None, case_ids: list[str] | None = None,
         pending_for=None, attempts_for=None, first: list[str] | None = None,
         rig_arch: dict[str, str] | None = None, room_for=None,
         rungs: list[int] | None = None, ladder_for=None, rejudge_for=None) -> dict:
    """What bench-all would do — computed, nothing touched. Pure given its
    inputs (the tests drive it with fakes). ``ladder_for(label)`` = the
    model's DUE ladder sets {"names": ["<cat>/<part>#n", ...], "jobs": n}
    (default: :func:`ladder_due` — what `all` would climb now)."""
    from . import report
    from .judge import needs_verdict
    from .config import repeats_for
    from .profiles import DEFAULT_PROFILE, apply_profile, load_profile, profile_judge
    repeats = None
    if case_ids is None:
        cfg_p, cases = apply_profile(load_profile(DEFAULT_PROFILE, cfg), cfg)
        case_ids = [c["id"] for c in cases]
        repeats = {c["id"]: repeats_for(cfg_p, c["category"], case=c) for c in cases}
    if judge_id is None:
        j = profile_judge(load_profile(DEFAULT_PROFILE, cfg))
        judge_id = j.get("model_id") if isinstance(j, dict) else j
    rows_for = rows_for or (lambda label: report.board_filter(label, cfg))
    room_for = room_for or (lambda label: report.board_filter(label, cfg, with_rungs=True))
    if rungs is None:
        from .runner import escalation_rungs
        try:
            rungs = escalation_rungs(apply_profile(load_profile(DEFAULT_PROFILE, cfg), cfg)[0])
        except Exception:  # noqa: BLE001 — no profile readable: no escalation planned
            rungs = []
    attempts_for = attempts_for or error_attempts
    rejudge_for = rejudge_for or rejudge_cases
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
            entry = {"name": lab, "model_id": mid}
            arch = str((rig_arch or {}).get(mid, "")).lower()
            if arch:
                # 3.10 (review I): the family warning reads the registry, not
                # the label — a fine-tune's file name rarely names its base
                entry["arch"] = arch
            if arch in _REASONING_ARCHES:
                entry["extra_body"] = {"reasoning_format": _REASONING_FORMAT}
            add.append(entry)

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
                                   {} if new else attempts_for(label), repeats))
        if miss:
            groups.setdefault(miss, []).append(label)
    ordered = []
    first = [f for f in (first or []) if f]
    heads = []
    for miss, labels in groups.items():       # --first: one group each, ahead,
        for l in labels:                       # in the order they were named
            if l in first:
                heads.append((first.index(l), (miss, [l], True)))
        rest = [l for l in labels if l not in first]
        if rest:
            ordered.append((miss, rest, False))
    ordered = [h for _, h in sorted(heads, key=lambda t: t[0])] + ordered
    # escalation round: jobs that ran out of the board budget, not yet re-run
    # on the top rung (a model about to be generated is checked after its run)
    escalate = []
    if rungs:
        from .runner import escalation_todo
        new_names = {a["name"] for a in add}
        for label in candidates:
            if label in new_names:
                continue
            n = len(escalation_todo(room_for(label), label, rungs))
            if n:
                escalate.append({"model": label, "jobs": n})
    # ladder packs (review B2): a model whose standard set is complete and
    # judged climbs the ladder like `all` does; due sets keep the nightly's
    # "nothing to do" false (a model about to be generated is checked after
    # its run, inside the same window)
    ladder_plan = []
    if ladder_for is None:
        def ladder_for(label):
            return ladder_due(cfg, label, attempts_for(label))
    in_groups = {l for labels in groups.values() for l in labels}
    new_names = {a["name"] for a in add}
    for label in candidates:
        if label in in_groups or label in new_names:
            continue
        try:
            due = ladder_for(label)
        except Exception as e:  # noqa: BLE001 — the plan never fails on one model's board
            log.warning("ladder plan for %s unavailable: %s", label, e)
            due = None
        if due and due.get("jobs"):
            ladder_plan.append({"model": label, "sets": list(due.get("names") or []),
                                "jobs": int(due["jobs"])})
    # 3.10 (D1.7): per model, what is re-GENERATED (no usable current row:
    # new or edited case content) vs only re-JUDGED from the stored
    # transcript (judge grading edited, more samples asked)
    miss_of = {l: list(miss) for miss, labels, _ in ordered for l in labels}
    per_model = {}
    for label in candidates:
        try:
            rj = [] if label in new_names else sorted(rejudge_for(label))
        except Exception as e:  # noqa: BLE001 — the plan never fails on one model
            log.warning("re-judge list for %s unavailable: %s", label, e)
            rj = []
        per_model[label] = {"regenerate": miss_of.get(label, []), "rejudge": rj}
    # 4.0 (review H2): a boarded HOSTED model the nightly does not run
    # (no --include-api) that is short of standard cases keeps the board
    # partial; the plan says so, and the rebuild is never "done" over it
    hosted_pending = []
    for label in board_labels or []:
        m = by_name.get(label)
        if (m is None or label in candidates or m.get("enabled", True) is False
                or not is_api(m)):
            continue
        try:
            miss = missing_cases(rows_for(label), case_ids, attempts_for(label), repeats)
        except Exception as e:  # noqa: BLE001 — the plan never fails on one model
            log.warning("hosted coverage for %s unavailable: %s", label, e)
            continue
        if miss:
            hosted_pending.append({"model": label, "cases": len(miss)})
    try:
        from .profiles import private_scope
        scope = private_scope(cfg)
        private = {"ids": set(scope["case_ids"]), "terms": list(scope["terms"])}
    except Exception:  # noqa: BLE001 — no profile: nothing private known
        private = {"ids": set(), "terms": []}
    to_judge = sorted({l for l in candidates if pending_for(l)}
                      | {l for labels in groups.values() for l in labels}
                      | {e["model"] for e in escalate})
    return {"rig_models": len(rig_ids), "add": add, "skipped_rig": skipped_rig,
            "candidates": candidates, "not_on_rig": not_on_rig, "prune": prune,
            "groups": [{"models": labels, "cases": list(miss)} for miss, labels, _ in ordered],
            "judge": to_judge, "case_count": len(case_ids),
            "escalate": escalate, "rungs": list(rungs or []), "ladder": ladder_plan,
            "per_model": per_model, "hosted_pending": hosted_pending, "_private": private}


def rejudge_cases(label: str) -> set[str]:
    """Case ids of ``label``'s counting rows whose stored verdict is out of
    date (judge.verdict_outdated: grading edited / too few samples)."""
    from .config import load_transcripts
    from .judge import verdict_outdated
    return {r.get("case_id") for r in load_transcripts(label)
            if r.get("needs_judge") and verdict_outdated(r)}


def ladder_due(cfg: dict, label: str, attempts: dict[str, int] | None = None) -> dict | None:
    """The ladder sets ``label`` would run now (cli.ladder_todo on its
    current board rows: due sets, jobs without a usable row), minus jobs of
    a case that already errored MAX_CASE_ATTEMPTS times (so the nightly's
    done-check converges). None when the profile has no ladder."""
    from . import cli, ladder
    from .profiles import DEFAULT_PROFILE, apply_profile, load_profile
    prof = load_profile(DEFAULT_PROFILE, cfg)
    sets = ladder.discover(prof)
    if not sets:
        return None
    cfg_p, _ = apply_profile(prof, cfg)
    cases, jobs, names = cli.ladder_todo(label, cfg_p, prof, sets, ladder.unlock_share(prof))
    attempts = attempts or {}
    jobs = {j for j in jobs if attempts.get(j[1], 0) < MAX_CASE_ATTEMPTS}
    return {"names": names, "jobs": len(jobs), "cases": cases, "job_set": jobs}


def nothing_to_do(p: dict) -> bool:
    return not (p["add"] or p["prune"] or p["groups"] or p["judge"] or p.get("escalate")
                or p.get("ladder"))


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
    if p.get("escalate"):
        L.append(f"  > escalate to {'/'.join(str(r) for r in p['rungs'])} tokens: "
                 + ", ".join(f"{e['model']} ({e['jobs']} job(s))" for e in p["escalate"]))
    for lp in p.get("ladder") or []:
        # "> generate" on purpose: the nightly's done-check greps that prefix
        L.append(f"  > generate ladder pack(s) for {lp['model']}: {', '.join(lp['sets'])} "
                 f"({lp['jobs']} job(s))")
    if p["judge"]:
        L.append(f"  > judge pending rows of {', '.join(p['judge'])}")
    priv = (p.get("_private") or {})
    pids = set(priv.get("ids") or ())

    def ids_cell(ids):
        # review M2: the plan reaches the run report runs-deliver posts —
        # private case ids are counted, never named
        pub = [i for i in ids if i not in pids]
        n = len(ids) - len(pub)
        return ", ".join(pub + ([f"private ({n})"] if n else []))
    for label, pm in (p.get("per_model") or {}).items():
        # never "> generate" here: the nightly's done-check greps that prefix
        if pm.get("regenerate") or pm.get("rejudge"):
            L.append(f"    {label}: regenerate: [{ids_cell(pm.get('regenerate') or [])}] · "
                     f"re-judge: [{ids_cell(pm.get('rejudge') or [])}]")
    if p.get("hosted_pending"):
        # never "> " here: not work this (rig) run does — but the nightly's
        # done-check greps this line and does not declare the rebuild done
        L.append("  ! hosted rows pending: " + ", ".join(
            f"{h['model']} ({h['cases']} case(s))" for h in p["hosted_pending"])
            + " — hosted models need an attended `bench-all --include-api` run (metered)")
    for w in p.get("window") or []:
        L.append(w)
    if nothing_to_do(p):
        L.append("  nothing to do — every contestant is complete and judged"
                 + (" (hosted rows pending)" if p.get("hosted_pending") else ""))
    text = "\n".join(L)
    # anything private left (a private ladder set name, a label): the term goes
    import re as _re
    for term in priv.get("terms") or ():
        if term and term in text:
            text = _re.sub(r"(?<![\w-])" + _re.escape(term) + r"(?![\w-])", "private", text)
    return text


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
        if a.get("arch"):
            block += f"{ind}  arch: {a['arch']}\n"
        eb = a.get("extra_body") or {}
        if eb:
            block += f"{ind}  extra_body:\n"
            for line in yaml.safe_dump(eb, default_flow_style=False,
                                       sort_keys=False).rstrip("\n").splitlines():
                block += f"{ind}    {line}\n"
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


#: an --until further away than this is refused (a missed day-run end time)
MAX_WINDOW_H = 14


def deadlines(until: str | None, judge_reserve_min: float,
              now: datetime | None = None) -> tuple[datetime | None, datetime | None]:
    """(generation deadline, hard deadline) for ``--until HH:MM``.

    The hard deadline is the next HH:MM after ``now`` (a 01:00 start with
    ``--until 03:00`` ends at 03:00 the same night). Generation stops
    ``judge_reserve_min`` earlier so the judge can score what was generated
    and the in-flight rows can finish. (None, None) without ``--until``."""
    if not until:
        return None, None
    from datetime import timedelta
    now = now or datetime.now()
    try:
        hh, mm = (int(x) for x in until.split(":", 1))
        end = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    except ValueError:
        raise SystemExit(f"--until wants HH:MM, got {until!r}") from None
    if end <= now:
        end += timedelta(days=1)
    if end - now > timedelta(hours=MAX_WINDOW_H):
        # a day run launched after its end time would otherwise run until
        # tomorrow, through the quiet hours and over the nightly
        raise SystemExit(f"--until {until} is {(end - now).total_seconds() / 3600:.1f} h away "
                         f"(more than {MAX_WINDOW_H} h) — already past today? refusing")
    return end - timedelta(minutes=judge_reserve_min), end


#: minutes kept after the judge for the resident restore + both board rebuilds
REPORT_TAIL_MIN = 6.0
#: the judge hands out no new verdict this long before its cut (in-flight
#: verdicts then finish; anything still running at the cut is dropped)
JUDGE_STOP_LEAD_MIN = 3.0
#: rows in flight at the generation stop may finish this long, then they are
#: dropped (api.WindowClosed: no row, the job stays pending) — STEP 3.8 lost
#: ~55 min to one 65k-budget bank row finishing after the stop
GEN_GRACE_MIN = 10.0
#: a judge phase is not started with less than this left before its cut
MIN_JUDGE_MIN = 5.0
#: review M4: a first-in-queue model longer than the whole window still starts
#: when at least this much generation time is left (its rows resume next run)
OVERSIZE_MIN_MIN = 10.0


class Window:
    """The ``--until`` time window (review B1): everything — generation,
    escalation, ladder packs, the judge — is finished by ``hard_end``.

    judge_cut = hard_end - REPORT_TAIL_MIN: no judge call runs past it.
    Generation for a group/model stops at
    ``judge_cut - reserve - GEN_GRACE_MIN`` where ``reserve`` = the
    estimated judge minutes for every row then waiting for a verdict (the
    `all --plan` estimators, crucibleforge/estimate.py) — never below
    ``--judge-reserve-min`` (the floor). A group starts only when its
    estimated generation fits before that stop."""

    def __init__(self, hard_end: datetime, floor_min: float = 0.0):
        from datetime import timedelta
        self.hard_end = hard_end
        self.floor_min = float(floor_min or 0)
        self.judge_cut = hard_end - timedelta(minutes=REPORT_TAIL_MIN)
        self.judge_stop_at = self.judge_cut - timedelta(minutes=JUDGE_STOP_LEAD_MIN)

    def reserve_min(self, judge_eq: float) -> float:
        from . import estimate as est
        return max(self.floor_min, est.judge_min(judge_eq, 3) if judge_eq > 0 else 0.0)

    def gen_end(self, judge_eq: float) -> datetime:
        from datetime import timedelta
        return self.judge_cut - timedelta(minutes=self.reserve_min(judge_eq) + GEN_GRACE_MIN)

    def fits(self, now: datetime, gen_min: float, judge_eq: float) -> bool:
        """``gen_min`` of generation, then the judge for ``judge_eq`` rows
        (everything waiting INCLUDING this work), before the window ends."""
        from datetime import timedelta
        return now + timedelta(minutes=gen_min) <= self.gen_end(judge_eq)

    def gen_cut(self, gen_end: datetime) -> datetime:
        from datetime import timedelta
        return min(gen_end + timedelta(minutes=GEN_GRACE_MIN), self.judge_cut)


def _hm(t: datetime) -> str:
    return t.strftime("%H:%M")


def _entry_for(cfg: dict, label: str, add: list[dict], provider: str | None) -> dict | None:
    for m in cfg.get("models") or []:
        if m.get("name") == label:
            return m
    for a in add or []:
        if a["name"] == label:
            return {**a, "provider": provider}
    return None


def group_estimate(cfg: dict, labels: list[str], case_ids: list[str],
                   add: list[dict] | None = None, provider: str | None = None
                   ) -> tuple[float, float]:
    """(generation minutes, judged-row equivalents) for one bench-all group:
    its models run one after another, each over ``case_ids``. Same
    arithmetic as `all --plan` (estimate.model_estimate)."""
    from . import estimate as est
    from .profiles import DEFAULT_PROFILE, apply_profile, load_profile
    cfg_p, cases = apply_profile(load_profile(DEFAULT_PROFILE, cfg), cfg)
    want = set(case_ids)
    cs = [c for c in cases if c["id"] in want]
    gen = eq = 0.0
    for label in labels:
        e = _entry_for(cfg_p, label, add or [], provider)
        if e is None:
            continue
        g, q = est.model_estimate(cfg_p, e, cs, use_measured=True)
        gen, eq = gen + g, eq + q
    return gen, eq


def pending_equiv(labels, cfg: dict | None = None) -> float:
    """Judge work already waiting: rows of ``labels`` that need a verdict
    (judge --retry-failed), in judged-row equivalents."""
    from . import estimate as est
    from .config import load_transcripts
    from .judge import judge_policy_config, needs_verdict
    pcfg = judge_policy_config(cfg or {})
    return sum(est.row_judge_equiv(r, pcfg) for l in labels for r in load_transcripts(l)
               if needs_verdict(r, retry_failed=True))


def units(p: dict) -> list[dict]:
    """The plan's groups one model at a time, in order: under a time window
    each model is admitted (estimate fits) and timed on its own, so one slow
    model never keeps the rest of a big group out of the window."""
    return [{"models": [m], "cases": g["cases"]} for g in p.get("groups") or []
            for m in g["models"]]


def schedule_lines(p: dict, cfg: dict, win: Window, start: datetime,
                   estimate_for=None, pending_for=None) -> list[str]:
    """The window as the plan sees it (``bench-all --until`` without --go):
    per group its estimated generation and the judge work it adds, whether it
    fits, when generation stops and the judge estimate. ``estimate_for(g)``
    / ``pending_for(labels)`` default to group_estimate / pending_equiv."""
    from datetime import timedelta
    from . import estimate as est
    estimate_for = estimate_for or (lambda g: group_estimate(cfg, g["models"], g["cases"],
                                                             p.get("add"), p.get("_provider")))
    pending_for = pending_for or (lambda labels: pending_equiv(labels, cfg))
    L = [f"  ~ window: start {_hm(start)}, everything done by {_hm(win.hard_end)} "
         f"(judge cut {_hm(win.judge_cut)}, board rebuild after; judge reserve = estimate, "
         f"floor {win.floor_min:g} min)"]
    t = start
    eq = pending_for(p.get("judge") or [])
    if eq:
        L.append(f"  ~ already waiting for the judge: {eq:.1f} judged-row equivalents "
                 f"(~{est.judge_min(eq, 3):.0f} min)")
    last_end = None
    skipped = []
    ran = []
    for g in units(p):
        try:
            gen, q = estimate_for(g)
        except Exception as e:  # noqa: BLE001 — a plan line, never a failure
            L.append(f"  ~ {', '.join(g['models'])}: no estimate ({e})")
            continue
        oversize_first = (not ran and win.gen_end(eq + q) > t + timedelta(minutes=OVERSIZE_MIN_MIN)
                          and not win.fits(t, gen, eq + q))
        if oversize_first:
            # review M4: the run starts it anyway (see execute)
            L.append(f"  ~ {_hm(t)}-{_hm(win.gen_end(eq + q))} generate {', '.join(g['models'])} "
                     f"({len(g['cases'])} cases): ~{gen:.0f} min is longer than the window — "
                     f"starts first, runs to the cut, the rest resumes next run")
            t = win.gen_end(eq + q)
            eq += q
            last_end = win.gen_end(eq)
            ran.append(g)
        elif win.fits(t, gen, eq + q):
            L.append(f"  ~ {_hm(t)}-{_hm(t + timedelta(minutes=gen))} generate "
                     f"{', '.join(g['models'])} ({len(g['cases'])} cases): ~{gen:.0f} min, "
                     f"+{q:.1f} judged rows (~{est.JUDGE_MIN_PER_ROW * q:.0f} judge min)")
            t += timedelta(minutes=gen)
            eq += q
            last_end = win.gen_end(eq)
            ran.append(g)
        else:
            skipped.append(f"{g['models'][0]} (~{gen:.0f} min)")
    if skipped:
        L.append(f"  ~ does not fit, left for the next run ({len(skipped)}): " + ", ".join(skipped))
    jm = est.judge_min(eq, 3) if eq else 0.0
    gen_end = last_end or win.gen_end(eq)
    L.append(f"  ~ generation stops by {_hm(gen_end)} at the latest (in-flight rows "
             f"+{GEN_GRACE_MIN:g} min, then dropped); judge ~{jm:.0f} min "
             f"(estimate; reserve {win.reserve_min(eq):.0f} min), stopped at "
             f"{_hm(win.judge_stop_at)} — unjudged rows stay pending for the next run")
    # 4.0: the single end-of-run retry pass (bench-all --escalate) — what ran
    # out of budget, known now plus an expected share of tonight's new rows,
    # re-run once at the next rung; it uses what the window has left
    if p.get("rungs"):
        per = {e["model"]: int(e.get("jobs") or 0) for e in p.get("escalate") or []}
        known = sum(per.values())
        expect = 0
        for g in ran:
            for m in g["models"]:
                n = int(round(est.ESCALATION_RATE * len(g["cases"])))
                per[m] = per.get(m, 0) + n
                expect += n
        models = sum(1 for n in per.values() if n)
        em = est.escalation_min(list(per.values()), p["rungs"][0])
        if known + expect:
            fits = win.fits(t, em, eq)
            L.append(f"  ~ {_hm(t)}-{_hm(t + timedelta(minutes=em))} retry pass (--escalate) at "
                     f"{p['rungs'][0]} tokens: {known} known + ~{expect} expected job(s) over "
                     f"{models} model(s), ~{em:.0f} min — "
                     + ("fits" if fits else "does NOT fit fully: cut at the window edge, "
                        "the rest next run"))
    if p.get("ladder"):
        L.append("  ~ ladder packs run after the judge only while their estimate fits "
                 "(checked per model at run time)")
    return L


def _ladder_round(args, cfg, labels, win, jargs, want_esc, arm, disarm, waiting,
                  notes) -> int:
    """cli._ladder_phases for bench-all: the same climb `all` does, with a
    per-model admission check against the window (estimate of the set's
    generation + the judge work then waiting) and the generation window
    armed for each admitted run."""
    from . import cli
    from . import estimate as est
    skipped: list[str] = []

    def admit(label, entry, cases, jobs) -> bool:
        if not win:
            return True
        from .profiles import DEFAULT_PROFILE, apply_profile, load_profile
        try:
            cfg_p, _ = apply_profile(load_profile(DEFAULT_PROFILE, cfg), cfg)
            gen, q = est.model_estimate(cfg_p, entry, cases, only_jobs=jobs)
        except Exception as e:  # noqa: BLE001 — unknown cost: the timers still bound it
            log.warning("no ladder estimate for %s (%s)", label, e)
            gen, q = 0.0, float(len(jobs))
        eq = waiting([label]) + q
        if not win.fits(datetime.now(), gen, eq):
            skipped.append(label)
            return False
        arm(win.gen_end(eq))
        return True

    # 4.0: no inline per-model escalation — what a pack row runs out of is
    # retried by the next run's single end-of-run retry pass
    l_args = argparse.Namespace(models=",".join(labels), no_tiers=False, no_ladder=False,
                                escalate=False, no_escalate=True, yes=True,
                                no_link_check=False, rungs=None, ladder_admit=admit,
                                ladder_after=disarm, **jargs)
    try:
        rc = cli._ladder_phases(l_args, cfg, labels)
    except Exception as e:  # noqa: BLE001 — the board is still worth rebuilding
        log.error("bench-all: ladder phase failed: %s", e)
        notes.append(f"ladder phase failed: {e}")
        rc = 1
    if skipped:
        notes.append("time window: ladder packs left for the next run: "
                     + ", ".join(sorted(set(skipped))))
    return rc


def execute(args, cfg: dict) -> int:
    """The body of `crucibleforge bench-all` (called from cli with the rig lock held)."""
    from . import cli
    from .config import load_config, results_dir
    from .providers import get_provider
    sf = [n for n, p in (cfg.get("providers") or {}).items() if (p or {}).get("type") == "studioforge"]
    if not sf:
        print("bench-all: no studioforge provider in models.yaml")
        return EXIT_RIG_DOWN
    prov = get_provider(cfg, sf[0])
    rig_ids = set(prov.list_models(refresh=True))
    if not rig_ids:
        print("bench-all: the rig returned no model list — is the rig up?")
        return EXIT_RIG_DOWN
    board = sorted({p.stem.removeprefix("transcripts_") for p in results_dir().glob("transcripts_*.jsonl")}
                   | {p.stem.removeprefix("meta_") for p in results_dir().glob("meta_*.json")})
    first = [x.strip() for x in (getattr(args, "first", None) or "").split(",") if x.strip()]
    p = plan(cfg, rig_ids, include_api=args.include_api, board_labels=board, first=first,
             rig_arch=rig_arches(prov))
    p["_provider"] = sf[0]
    if getattr(args, "until", None):
        # the window as estimated now (or at --plan-start HH:MM, plan only)
        start = datetime.now()
        ps = getattr(args, "plan_start", None)
        if ps and not args.go:
            hh, mm = (int(x) for x in ps.split(":", 1))
            start = start.replace(hour=hh, minute=mm, second=0, microsecond=0)
        _, end = deadlines(args.until, 0, now=start)
        try:
            p["window"] = schedule_lines(p, cfg, Window(end, float(
                getattr(args, "judge_reserve_min", 45) or 0)), start)
        except Exception as e:  # noqa: BLE001 — the plan lines are information only
            p["window"] = [f"  ~ window estimate unavailable: {e}"]
    print(format_plan(p))
    args.bench_all_plan = p
    # the run report names what this refresh touches — set now, so a report
    # written after an interrupt (SIGTERM mid-judge) says the same
    touched = sorted({m for g in p["groups"] for m in g["models"]} | set(p["judge"])
                     | {e["model"] for e in p.get("escalate") or []}
                     | {lp["model"] for lp in p.get("ladder") or []})
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
    import threading
    from . import runner
    _, hard_end = deadlines(getattr(args, "until", None),
                            float(getattr(args, "judge_reserve_min", 45) or 0))
    win = Window(hard_end, float(getattr(args, "judge_reserve_min", 45) or 0)) if hard_end else None
    if win:
        log.info("bench-all: time window — everything done by %s (judge cut %s)",
                 _hm(win.hard_end), _hm(win.judge_cut))
    timers: list = []

    def _close_window():
        runner.WINDOW_STOP.set()
        runner.STOP.set()

    def _arm(gen_end: datetime) -> None:
        """Generation window for the next run: STOP (cooperative) at
        ``gen_end``, in-flight requests dropped at gen_end + grace."""
        _disarm()
        t = threading.Timer(max(0.0, (gen_end - datetime.now()).total_seconds()), _close_window)
        t.daemon = True
        t.start()
        timers.append(t)
        runner.GEN_DEADLINE[0] = win.gen_cut(gen_end).timestamp()

    def _disarm() -> bool:
        """Cancel the generation window; True when it had closed (STOP set)."""
        for t in timers:
            t.cancel()
        timers.clear()
        closed = runner.WINDOW_STOP.is_set()
        runner.GEN_DEADLINE[0] = None
        runner.WINDOW_STOP.clear()
        if closed:
            runner.STOP.clear()
        return closed

    def _waiting(extra=()):
        try:
            return pending_equiv(sorted(set(p["judge"]) | set(extra)), cfg)
        except Exception as e:  # noqa: BLE001 — an estimate never stops the run
            log.warning("pending-judge estimate unavailable: %s", e)
            return 0.0

    left: list[str] = []
    admitted = False
    try:
        seq = units(p) if win else p["groups"]
        for gi, g in enumerate(seq):
            if cli.stopping():
                break             # SIGTERM: nothing new starts
            if win:
                try:
                    g_gen, g_eq = group_estimate(cfg, g["models"], g["cases"], p.get("add"),
                                                 sf[0])
                except Exception as e:  # noqa: BLE001 — unknown cost: the timers still bound it
                    log.warning("no estimate for %s (%s)", g["models"], e)
                    g_gen, g_eq = 0.0, 0.0
                eq = _waiting(g["models"]) + g_eq
                now = datetime.now()
                # review M4: a model whose estimate is longer than the whole
                # window would be skipped every night; when it is FIRST in the
                # queue it starts anyway, runs to the generation cut, and its
                # partial rows resume the next night
                from datetime import timedelta as _td
                oversize_first = (not admitted
                                  and win.gen_end(eq) > now + _td(minutes=OVERSIZE_MIN_MIN)
                                  and not win.fits(now, g_gen, eq))
                if oversize_first:
                    notes.append(f"{','.join(g['models'])}: estimate ~{g_gen:.0f} min is longer "
                                 f"than the window — started first, runs to the cut, the rest "
                                 f"resumes next run")
                elif not win.fits(now, g_gen, eq):
                    left += g["models"]
                    log.info("bench-all: %s does not fit the window (~%.0f min generation, "
                             "generation must stop by %s) — next run", ",".join(g["models"]),
                             g_gen, _hm(win.gen_end(eq)))
                    continue
                gen_end = win.gen_end(eq)
                log.info("bench-all: %s — ~%.0f min generation, stop at %s at the latest",
                         ",".join(g["models"]), g_gen, _hm(gen_end))
                _arm(gen_end)
                admitted = True
            run_args = argparse.Namespace(
                cmd="run", models=",".join(g["models"]), cases=",".join(g["cases"]),
                categories=None, difficulty=None, fresh=False, force=False, smoke=False,
                yes=True, no_link_check=False, judge=None, force_evict=False, profile=None,
                skip_conformance=bool(getattr(args, "skip_conformance", False)))
            log.info("bench-all: generating %d case(s) for %s", len(g["cases"]), run_args.models)
            try:
                r = cli.cmd_run(run_args, cfg)
            except SystemExit as e:        # a group-level refusal must not stop the others
                r = e.code if isinstance(e.code, int) else 1
                notes.append(f"generation for {run_args.models} stopped: {e}")
            if win and _disarm():
                notes.append(f"generation stopped at the window edge during {run_args.models} "
                             f"— partial rows kept, the rest next run")
                left += [m for g2 in seq[gi + 1:] for m in g2["models"]]
                break
            if r:
                rc = EXIT_MODEL_FAILED
    finally:
        if win:
            _disarm()
    if left:
        notes.append(f"time window: left for the next run: {', '.join(sorted(set(left)))}")
    if cli.stopping():
        # SIGTERM (M10): no escalation, judge or board rebuild after a stop
        args.bench_all_notes = notes + ["stopped (SIGTERM) — the rest waits for the next run"]
        return cli.TERM_EXIT
    # escalation round: what ran out of the board budget (this run's models
    # and earlier ones) re-run on the bigger rungs, inside the same window
    esc = sorted({e["model"] for e in p.get("escalate") or []}
                 | {m for g in p["groups"] for m in g["models"] if m not in left})
    # suite 3.9: escalation is opt-in here too (`--escalate`), like `all` —
    # the board must not depend on which entry point produced a model's rows
    want_esc = bool(getattr(args, "escalate", False)) and not getattr(args, "no_escalate", False)
    if esc and p.get("rungs") and not want_esc and p.get("escalate"):
        notes.append("escalation pending for " + ", ".join(
            f"{e['model']} ({e['jobs']} job(s))" for e in p["escalate"])
            + " — off by default; re-run with --escalate (or `crucibleforge escalate`)")
    window_open = True
    if esc and p.get("rungs") and want_esc and win:
        jobs = sum(int(e.get("jobs") or 0) for e in p.get("escalate") or [])
        eq = _waiting(esc) + jobs
        # an escalation job is a long one (a 64k+ budget): at least 15 min
        window_open = win.fits(datetime.now(), 15.0, eq)
        if window_open:
            _arm(win.gen_end(eq))
    if esc and p.get("rungs") and window_open and want_esc:
        # 4.0 (lite): ONE retry pass for every model's out-of-budget rows, at
        # the next rung only (the profile's first escalation rung)
        e_args = argparse.Namespace(cmd="escalate", models=",".join(esc),
                                    rungs=str(p["rungs"][0]), yes=True,
                                    profile=None, judge=None, no_link_check=False,
                                    force_evict=False, no_escalate=False)
        log.info("bench-all: escalation round for %s", e_args.models)
        try:
            re_ = cli.cmd_escalate(e_args, cfg)
        except SystemExit as e:
            re_ = 1
            notes.append(f"escalation stopped: {e}")
        finally:
            closed = _disarm() if win else False
        if closed:
            notes.append("escalation stopped at the window edge — partial rows kept, "
                         "the rest next run")
        if re_:
            notes.append(f"escalation round exited {re_} — the Room column stays pending for "
                         f"those jobs; the next run retries them")
    elif esc and p.get("rungs") and not window_open and want_esc:
        notes.append("time window closed — escalation round left for the next run")
    if cli.stopping():
        args.bench_all_notes = notes + ["stopped (SIGTERM) — the rest waits for the next run"]
        return cli.TERM_EXIT
    # the judge phase(s): one stop event for the whole run, set before the
    # judge cut; calls still in flight at the cut are dropped (rows pending)
    judge_stop = threading.Event()
    jt = None
    if win:
        jt = threading.Timer(max(0.0, (win.judge_stop_at - datetime.now()).total_seconds()),
                             judge_stop.set)
        jt.daemon = True
        jt.start()

    def _judge_room() -> bool:
        if not win:
            return True
        return (win.judge_stop_at - datetime.now()).total_seconds() / 60 >= MIN_JUDGE_MIN

    jargs = dict(cmd="judge", force=False, retry_failed=True, samples=None, smoke=False,
                 judge=None, judge_fallback=None, allow_self_judge=False, force_evict=False,
                 profile=None, judge_stop=judge_stop if win else None,
                 judge_deadline=win.judge_cut.timestamp() if win else None)
    judged_labels = sorted(set(p["judge"]) | {m for g in p["groups"] for m in g["models"]})
    try:
        if judged_labels and not _judge_room():
            notes.append("time window: no time left to judge — the rows wait for the next run")
        elif judged_labels:
            j_args = argparse.Namespace(models=",".join(judged_labels), **jargs)
            try:
                rj = cli.cmd_judge(j_args, cfg)
            except Exception as e:  # noqa: BLE001 — the boards are still worth rebuilding
                log.error("bench-all: judge phase failed: %s", e)
                notes.append(f"judge phase failed: {e}")
                rj = 1
            if judge_stop.is_set():
                notes.append(f"judge stopped at {_hm(win.judge_stop_at)} (window) — unjudged rows "
                             f"stay pending for the next run")
            if rj == 4:
                notes.append("judge lease refused — the rig is busy; re-run bench-all later")
                rc = rc or EXIT_RIG_BUSY
            elif rj:
                rc = rc or EXIT_MODEL_FAILED
        # ladder packs (review B2), like `all`: every due set, judged, until
        # none is due — inside the same window (each model's set starts only
        # when its estimate fits)
        lad = sorted((set(judged_labels) | {lp["model"] for lp in p.get("ladder") or []})
                     - set(left))
        if lad and not cli.stopping() and not judge_stop.is_set() and _judge_room() \
                and not getattr(args, "no_ladder", False):
            rc_l = _ladder_round(args, cfg, lad, win, jargs, want_esc, _arm, _disarm,
                                 _waiting, notes)
            if rc_l == cli.TERM_EXIT:
                args.bench_all_notes = notes + ["stopped (SIGTERM) — the rest waits for the next run"]
                return cli.TERM_EXIT
            if rc_l:
                notes.append(f"ladder packs exited {rc_l} — the board shows unrun sets as pending")
        elif p.get("ladder") and win:
            notes.append("time window: ladder packs left for the next run")
    finally:
        if jt:
            jt.cancel()
        if win:
            _disarm()
    if cli.stopping():
        args.bench_all_notes = notes + ["stopped (SIGTERM) — the rest waits for the next run"]
        return cli.TERM_EXIT
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
