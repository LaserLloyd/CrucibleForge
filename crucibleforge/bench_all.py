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
refused) — back off and re-run later · 5 the rig is unreachable (no listing)
· 2 ``--only`` names no registered or rig model.

MakoBench operator flags (2026-11-06, OPERATOR-PLAN-v2 §6.3):
``--only LABELS`` scopes EVERYTHING above to those labels (the run never
registers, prunes, generates, escalates, ladders or judges another model);
``--lease-mode night|day`` overrides the models.yaml lease policy (night =
force idle residents only, never cut a busy one; day = no force, no ClawForge
vacate, one unprotected card or refuse, request priority 3, no judge);
``--no-judge`` generates only; ``--plan-json`` prints the plan as one JSON
object. A registry row with ``keep_on_board: true`` is never pruned. With
none of these flags bench-all behaves exactly as before.
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
#: --only names nothing that exists (never silently widened to "all")
EXIT_USAGE = 2

#: rig ids that are never contestants — the MakoBench SCOPE v2 out-of-scope
#: kinds (makobench-operator/SCOPE.md §3): output not text (TTS/ASR/audio,
#: image/video generation, 3D/GLB), vectors or labels (embeddings, rerankers,
#: classifiers, guard/reward models), single-task text tools (translation/MT,
#: captioners, OCR). Matched on word-ish boundaries of the rig id, so a
#: "…-MTP" chat model or a "clipped" name is not caught by accident.
#: Vision-language CHAT models are in scope (text suite only) — only a
#: captioner is refused here; registering one is the operator's call (--only).
_NOT_CHAT = re.compile(
    r"(?<![a-z0-9])(?:"
    r"embed\w*|rerank\w*|whisper|clip|tts|asr|stt|speech|audio|voice|music|wav2vec|"
    r"translat\w*|nllb|madlad|(?:hy-)?mt[0-9]?|"
    r"caption\w*|ocr|florence|blip|"
    r"3d|glb|gltf|cad|openscad|"
    r"diffus\w*|sdxl|flux|video|image-gen|text-to-image|txt2img|"
    r"guard|llamaguard|shieldgemma|moderation|reward|prm|classif\w*"
    r")(?![a-z])", re.IGNORECASE)


#: a vision-language CHAT model by name (the MakoBench operator's eligibility
#: uses the same words): in scope, tested on the text suite only
_VL_CHAT = re.compile(r"(?<![a-z0-9])(?:vl|vlm|vision|llava\w*|smolvlm|idefics\w*|internvl\w*|"
                      r"pixtral|molmo|minicpm-?v)(?![a-z])", re.IGNORECASE)
MOD_VL = "text+image->text"


def vl_chat(model_id: str) -> bool:
    """True for a rig id that names a vision-language chat model."""
    return bool(_VL_CHAT.search(str(model_id or ""))) and not out_of_scope(model_id)


def out_of_scope(model_id: str) -> bool:
    """True for a rig id of a kind MakoBench never benches (SCOPE v2 §3)."""
    return bool(_NOT_CHAT.search(str(model_id or "")))
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
    from .preflight import billing_error_row
    reps: dict[str, set] = {}
    for r in rows:
        if billing_error_row(r):
            continue  # a billing refusal says nothing about the model (ed5630a)
        if r.get("grade") != "error" or _permanent_error(r):
            reps.setdefault(r.get("case_id"), set()).add(r.get("repeat") or 1)
    attempts = attempts or {}
    repeats = repeats or {}
    return [c for c in case_ids
            if len(reps.get(c, ())) < repeats.get(c, 1)
            and attempts.get(c, 0) < MAX_CASE_ATTEMPTS]


def replay_rows(entry: dict | None, rows: list[dict]) -> list[dict]:
    """Alignment 2026-11-06 (review B1): when a registry row sets its own
    ``reasoning_replay`` (conformance c3 proved the default wrong), rows
    measured under the other replay setting are not usable — they measured
    the wrong thing — so bench-all regenerates them. A row without a
    protocol stamp was made with the replay off (the pre-2026-11-02 harness
    never replayed). Without the key every row counts, as before."""
    if not entry or entry.get("reasoning_replay") is None:
        return rows
    want = bool(entry["reasoning_replay"])
    return [r for r in rows
            if bool((r.get("protocol") or {}).get("reasoning_replay", False)) == want]


def error_attempts(label: str) -> dict[str, int]:
    """Errored benchmark rows per case id over the label's history at the
    CURRENT suite revision (an error under an older regime says nothing about
    whether the case can run now — 3.8.0 re-runs every case)."""
    from .config import load_transcripts
    from .profiles import DEFAULT_PROFILE
    from .preflight import billing_error_row
    from .version import row_current
    out: dict[str, int] = {}
    for r in load_transcripts(label):
        if billing_error_row(r):
            # 4.1 F1: a provider that would not bill the request (credit
            # spent, rate/spend limit) says nothing about the case; it is due
            # again, never final
            continue
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


def first_ahead(labels, first) -> list[str]:
    """``labels`` with the ``--first`` ones ahead, in the order they were
    named; the rest keep their order."""
    first = [f for f in (first or []) if f]
    head = [f for f in first if f in labels]
    return head + [l for l in labels if l not in head]


def plan(cfg: dict, rig_ids: set[str], *, include_api: bool = False,
         board_labels: list[str] | None = None, rows_for=None,
         judge_id: str | None = None, case_ids: list[str] | None = None,
         pending_for=None, attempts_for=None, first: list[str] | None = None,
         rig_arch: dict[str, str] | None = None, room_for=None,
         rungs: list[int] | None = None, ladder_for=None, rejudge_for=None,
         only: list[str] | None = None, register_new: bool = False) -> dict:
    """What bench-all would do — computed, nothing touched. Pure given its
    inputs (the tests drive it with fakes). ``ladder_for(label)`` = the
    model's DUE ladder sets {"names": ["<cat>/<part>#n", ...], "jobs": n}
    (default: :func:`ladder_due` — what `all` would climb now).

    ``only`` (``--only``, MakoBench operator 2026-11-06): the run touches
    ONLY these labels (a rig model id names its would-be label too) — sync,
    prune, generation, escalation, ladder and the judge alike; every other
    model is left exactly as it is. Names that match nothing are listed in
    ``only_unknown``. A registry row with ``keep_on_board: true`` is never
    pruned (its rows stay on the board after its model leaves the rig).

    Registration (alignment 2026-11-06, review D2): a rig model with no
    registry row is registered ONLY when ``--only`` names it, or with
    ``register_new`` (``--register-new``) — the MakoBench operator is the
    registrar; a plain nightly never turns a downloaded tool model into a
    board row. Out-of-scope kinds (:func:`out_of_scope`) are never
    registered, and a registry row with ``bench: false`` (or an out-of-scope
    rig id) is never a contestant; its board rows are pruned like a
    disabled model's unless ``keep_on_board``."""
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

    only_set = {o for o in (only or []) if o} if only is not None else None
    add, skipped_rig = [], []
    for mid in sorted(rig_ids - registered):
        if out_of_scope(mid):
            skipped_rig.append((mid, "out of scope (not a text chat model)"))
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
            if vl_chat(mid):
                # alignment 2026-11-06: published as "text suite only"
                entry["modalities"] = MOD_VL
            if only_set is not None and lab not in only_set and mid not in only_set:
                continue          # --only: another model's registration waits
            if only_set is None and not register_new:
                taken.discard(lab)
                skipped_rig.append((mid, "not registered (the operator registers models: "
                                         "--only LABEL, or --register-new)"))
                continue
            add.append(entry)

    def on_rig(m):
        return prov_type.get(m.get("provider")) == "studioforge" and m["model_id"] in rig_ids

    def is_api(m):
        return prov_type.get(m.get("provider")) not in ("studioforge", "lmstudio")

    def wanted(label, mid=None):
        return only_set is None or label in only_set or (mid is not None and mid in only_set)

    def benched(m):
        return m.get("bench", True) is not False and not out_of_scope(m.get("model_id"))

    candidates, not_on_rig, kept_on_board, not_benched = [], [], [], []
    for m in models:
        if m.get("enabled", True) is False or not wanted(m["name"], m.get("model_id")):
            continue
        if not benched(m):
            not_benched.append(m["name"])
            continue
        if on_rig(m) or (include_api and is_api(m)):
            candidates.append(m["name"])
        elif prov_type.get(m.get("provider")) == "studioforge":
            (kept_on_board if m.get("keep_on_board") else not_on_rig).append(m["name"])
    candidates += [a["name"] for a in add]

    prune = []
    for label in board_labels or []:
        m = by_name.get(label)
        if not wanted(label, (m or {}).get("model_id")):
            continue
        if m is not None and m.get("keep_on_board"):
            continue          # the operator removed the model; its numbers stay
        if m is None:
            prune.append((label, "no registry entry"))
        elif m.get("enabled", True) is False:
            prune.append((label, "disabled in models.yaml"))
        elif not benched(m):
            prune.append((label, "out of scope (bench: false)"))
        elif prov_type.get(m.get("provider")) == "studioforge" and m["model_id"] not in rig_ids:
            prune.append((label, "no longer on the rig"))

    groups: dict[tuple, list[str]] = {}
    for label in candidates:
        new = label in {a["name"] for a in add}
        miss = tuple(missing_cases([] if new else replay_rows(by_name.get(label), rows_for(label)),
                                   case_ids,
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
    order = first_ahead([lp["model"] for lp in ladder_plan], first)
    ladder_plan.sort(key=lambda lp: order.index(lp["model"]))
    # 2026-11-03: --first used to reorder generation only — a named model with
    # nothing to generate silently waited behind everyone else's generation;
    # say where its work lands instead
    gen_labels = {l for _, labels, _ in ordered for l in labels}
    first_notes = []
    for f in first:
        if f in gen_labels:
            continue
        work = []
        if any(lp["model"] == f for lp in ladder_plan):
            work.append("ladder packs first in the ladder phase")
        if any(e["model"] == f for e in escalate):
            work.append("escalation only with --escalate")
        first_notes.append((f, ("nothing to generate — " + "; ".join(work)
                                + " (after every generation group and the judge)")
                            if work else "nothing to do"))
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
        if (m is None or not wanted(label, m.get("model_id")) or label in candidates or m.get("enabled", True) is False
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
            "per_model": per_model, "hosted_pending": hosted_pending, "_private": private,
            "first": first, "first_notes": first_notes,
            "only": sorted(only_set) if only_set is not None else None,
            "only_unknown": only_unknown(only_set, by_name, rig_ids, add, skipped_rig),
            "kept_on_board": kept_on_board, "not_benched": not_benched}


#: values of the models.yaml entry key ``quant_reason`` (row fact, K8)
QUANT_REASONS = frozenset({"q8_does_not_fit"})


def only_unknown(only_set, by_name, rig_ids, add, skipped_rig) -> list[str]:
    """--only names that are neither a registry label (or its model id) nor
    a rig model about to be registered."""
    if not only_set:
        return []
    known = set(by_name) | {m.get("model_id") for m in by_name.values()}
    known |= {a["name"] for a in add} | {a["model_id"] for a in add}
    skipped = {mid for mid, _ in skipped_rig}
    return sorted(o for o in only_set if o not in known and o not in skipped)


def rejudge_cases(label: str) -> set[str]:
    """Case ids of ``label``'s counting rows whose stored verdict is out of
    date (judge.verdict_outdated: grading edited / too few samples)."""
    from .config import load_transcripts
    from .judge import verdict_outdated
    return {r.get("case_id") for r in load_transcripts(label)
            if r.get("needs_judge") and verdict_outdated(r)}


def ladder_due(cfg: dict, label: str, attempts: dict[str, int] | None = None,
               prof: dict | None = None) -> dict | None:
    """The ladder sets ``label`` would run now (cli.ladder_todo on its
    current board rows: due sets, jobs without a usable row), minus jobs of
    a case that already errored MAX_CASE_ATTEMPTS times (so the nightly's
    done-check converges). None when the profile has no ladder. ``prof`` =
    the profile as a given audience sees it (the public board passes the
    public view, so a private pack is neither counted nor named)."""
    from . import cli, ladder
    from .profiles import DEFAULT_PROFILE, apply_profile, load_profile
    prof = prof or load_profile(DEFAULT_PROFILE, cfg)
    sets = ladder.discover(prof)
    if not sets:
        return None
    cfg_p, _ = apply_profile(prof, cfg)
    cases, jobs, names = cli.ladder_todo(label, cfg_p, prof, sets, ladder.unlock_share(prof))
    attempts = attempts or {}
    jobs = {j for j in jobs if attempts.get(j[1], 0) < MAX_CASE_ATTEMPTS}
    return {"names": names, "jobs": len(jobs), "cases": cases, "job_set": jobs}


def completeness(rows: list[dict], case_ids: list[str], *,
                 repeats: dict[str, int] | None = None,
                 attempts: dict[str, int] | None = None,
                 judge_rows: list[dict] | None = None,
                 ladder: dict | None = None,
                 escalation_jobs: int = 0) -> dict:
    """Is a model's run on the board FINISHED? (the board's "Incomplete" tag).
    Pure: it only combines the same checks bench-all uses to decide what is
    still to do, so the tag and the nightly agree on "done":

    * cases: :func:`missing_cases` over the label's CURRENT board ``rows`` (a
      case short of its repeats and not yet errored MAX_CASE_ATTEMPTS times,
      or errored with a retryable error);
    * judge: ``judge_rows`` (the board rows, escalations included) that
      :func:`judge.needs_verdict` (``retry_failed``) still sends to the judge
      — never judged, a failed verdict with attempts left, or a verdict out
      of date; a verdict that failed MAX_VERDICT_ATTEMPTS times is final;
    * ladder: ``ladder`` = :func:`ladder_due`'s answer; ``jobs`` still due;
    * escalation: ``escalation_jobs`` still owed on the top rung.

    Returns ``{"complete", "cases", "judge", "ladder", "escalation",
    "reasons"}`` where ``reasons`` is the tooltip text. A final error (a
    permanent one, or MAX_CASE_ATTEMPTS used up) is complete."""
    from .judge import needs_verdict
    miss = missing_cases(rows, case_ids, attempts, repeats)
    j_rows = rows if judge_rows is None else judge_rows
    waiting = sum(1 for r in j_rows if needs_verdict(r, retry_failed=True))
    lad = list((ladder or {}).get("names") or [])
    lad_jobs = int((ladder or {}).get("jobs") or 0)
    reasons = []
    if miss:
        reasons.append(f"{len(miss)} case{'s' if len(miss) != 1 else ''} not run")
    if waiting:
        reasons.append(f"{waiting} row{'s' if waiting != 1 else ''} awaiting judge")
    if lad_jobs:
        reasons.append("ladder: " + (", ".join(lad) if lad else f"{lad_jobs} job(s)") + " due")
    if escalation_jobs:
        reasons.append(f"{escalation_jobs} escalation job{'s' if escalation_jobs != 1 else ''} owed")
    return {"complete": not reasons, "cases": len(miss), "judge": waiting,
            "ladder": lad if lad_jobs else [], "escalation": int(escalation_jobs),
            "reasons": reasons}


def board_completeness(cfg: dict | None, labels: list[str]) -> dict[str, dict | None]:
    """:func:`completeness` for each board label, from the live results — the
    inputs are exactly :func:`plan`'s (case ids and repeats of the benchmark
    profile as this board's audience sees it, current board rows with the
    registry's replay rule, error attempts, escalation rungs, ladder_due).
    A label whose inputs cannot be read maps to None (no tag — never a
    guessed verdict)."""
    from . import report
    from .profiles import DEFAULT_PROFILE, apply_profile, load_profile
    cfg = cfg or {}
    out: dict[str, dict | None] = {}
    try:
        ids = report._profile_case_ids(cfg)
        prof = load_profile(DEFAULT_PROFILE, cfg)
        reps = report._profile_case_repeats(cfg)
        if ids is None or not reps:
            return {l: None for l in labels}
        case_ids = sorted(i for i in reps if i in ids)
        repeats = {i: reps[i] for i in case_ids}
        prof_view = report._audience_profile(prof, cfg)
        rungs = []
        try:
            from .runner import escalation_rungs
            rungs = escalation_rungs(apply_profile(prof, cfg)[0])
        except Exception:  # noqa: BLE001 — no escalation readable: none owed
            rungs = []
    except Exception as e:  # noqa: BLE001 — the board never fails on a tag
        log.warning("completeness: profile unreadable (%s) — no Incomplete tags", e)
        return {l: None for l in labels}
    by_name = {m["name"]: m for m in (cfg.get("models") or [])}
    for label in labels:
        try:
            att = error_attempts(label)
            rows = report.board_filter(label, cfg)
            room = report.board_filter(label, cfg, with_rungs=True)
            esc = 0
            if rungs:
                from .runner import escalation_todo
                esc = len(escalation_todo(room, label, rungs))
            out[label] = completeness(
                replay_rows(by_name.get(label), rows), case_ids, repeats=repeats,
                attempts=att, judge_rows=room,
                ladder=ladder_due(cfg, label, att, prof=prof_view), escalation_jobs=esc)
        except Exception as e:  # noqa: BLE001
            log.warning("completeness for %s unavailable: %s", label, e)
            out[label] = None
    return out


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
    if p.get("only") is not None:
        L.append(f"  · --only: {', '.join(p['only']) or '(none)'} — every other model is untouched")
    for o in p.get("only_unknown") or []:
        L.append(f"  ! --only {o}: no registry entry and not on the rig — nothing to do for it")
    for label in p["not_on_rig"]:
        L.append(f"  ! {label}: enabled but not on the rig — skipped (disable it or re-download)")
    for label in p.get("not_benched") or []:
        L.append(f"  · {label}: out of scope (bench: false / not a text chat model) — never benched")
    for label in p.get("kept_on_board") or []:
        L.append(f"  · {label}: not on the rig — its rows stay on the board (keep_on_board)")
    for label, why in p["prune"]:
        L.append(f"  - off the board: {label} ({why})")
    for label, why in p.get("first_notes") or []:
        # never "> " here: the nightly's done-check greps that prefix
        L.append(f"  ! --first {label}: {why}")
    for g in p["groups"]:
        L.append(f"  > generate {len(g['cases'])} case(s) for {', '.join(g['models'])}")
    if p.get("escalate"):
        L.append(f"  > escalate to {'/'.join(str(r) for r in p['rungs'])} tokens: "
                 + ", ".join(f"{e['model']} ({e['jobs']} job(s))" for e in p["escalate"]))
    for lp in p.get("ladder") or []:
        # "> generate" on purpose: the nightly's done-check greps that prefix
        L.append(f"  > generate ladder pack(s) for {lp['model']}: {', '.join(lp['sets'])} "
                 f"({lp['jobs']} job(s))")
    if p["judge"] and p.get("no_judge"):
        # never "> " here: nothing is judged by this run
        L.append(f"  · judge skipped (--no-judge): rows of {', '.join(p['judge'])} wait "
                 f"for a judged run")
    elif p["judge"]:
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
        if a.get("modalities"):
            block += f"{ind}  modalities: {a['modalities']}\n"
        if a.get("quant_reason"):
            block += f"{ind}  quant_reason: {a['quant_reason']}\n"
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

    def __init__(self, hard_end: datetime, floor_min: float = 0.0, no_judge: bool = False):
        from datetime import timedelta
        self.hard_end = hard_end
        # --no-judge: nothing is judged in this window, so no time is kept for it
        self.no_judge = bool(no_judge)
        self.floor_min = 0.0 if self.no_judge else float(floor_min or 0)
        self.judge_cut = hard_end - timedelta(minutes=REPORT_TAIL_MIN)
        self.judge_stop_at = self.judge_cut - timedelta(minutes=JUDGE_STOP_LEAD_MIN)

    def reserve_min(self, judge_eq: float) -> float:
        from . import estimate as est
        if self.no_judge:
            return 0.0
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


def window_schedule(p: dict, cfg: dict, win: "Window | None", start: datetime,
                    estimate_for=None, pending_for=None) -> dict:
    """The structured twin of :func:`schedule_lines` (same arithmetic, same
    admission order): ``{"per_label": {label: {gen_min, judge_eq, verdict,
    start, end}}, "judge_eq", "judge_min", "gen_stop", "judge_fits"}``.
    verdict: ``fits`` · ``oversize_first`` (longer than the window but first
    in the queue: starts, runs to the cut, resumes next run) ·
    ``does_not_fit`` (left for the next run) · ``no_window`` (no --until)."""
    from datetime import timedelta
    from . import estimate as est
    estimate_for = estimate_for or (lambda g: group_estimate(cfg, g["models"], g["cases"],
                                                             p.get("add"), p.get("_provider")))
    pending_for = pending_for or (lambda labels: pending_equiv(labels, cfg))
    per: dict[str, dict] = {}
    t = start
    eq = pending_for(p.get("judge") or [])
    ran = 0
    last_end = None
    for g in units(p):
        label = g["models"][0]
        try:
            gen, q = estimate_for(g)
        except Exception as e:  # noqa: BLE001 — a plan field, never a failure
            per[label] = {"gen_min": None, "judge_eq": None, "verdict": "no_estimate",
                          "error": str(e)[:200]}
            continue
        row = {"gen_min": round(gen, 1), "judge_eq": round(q, 2)}
        if win is None:
            row["verdict"] = "no_window"
        elif (not ran and win.gen_end(eq + q) > t + timedelta(minutes=OVERSIZE_MIN_MIN)
              and not win.fits(t, gen, eq + q)):
            row.update(verdict="oversize_first", start=_hm(t), end=_hm(win.gen_end(eq + q)))
            t = win.gen_end(eq + q)
            eq += q
            last_end = win.gen_end(eq)
            ran += 1
        elif win.fits(t, gen, eq + q):
            row.update(verdict="fits", start=_hm(t), end=_hm(t + timedelta(minutes=gen)))
            t += timedelta(minutes=gen)
            eq += q
            last_end = win.gen_end(eq)
            ran += 1
        else:
            row["verdict"] = "does_not_fit"
        per[label] = row
    out = {"per_label": per, "judge_eq": round(eq, 2)}
    if win is None:
        out.update(judge_min=round(est.judge_min(eq, 3), 1) if eq else 0.0,
                   gen_stop=None, judge_fits=None)
        return out
    jm = est.judge_min(eq, 3) if eq else 0.0
    gen_stop = last_end or win.gen_end(eq)
    room = (win.judge_stop_at - max(gen_stop, start)).total_seconds() / 60
    out.update(judge_min=round(jm, 1), gen_stop=_hm(gen_stop),
               judge_fits=(None if win.no_judge else bool(jm <= room)))
    return out


def plan_json(p: dict, cfg: dict, *, until: str | None, start: datetime,
              judge_reserve_min: float = 45, no_judge: bool = False,
              lease_mode: str | None = None, estimate_for=None, pending_for=None,
              pending_label=None) -> dict:
    """``bench-all --plan-json``: the plan as ONE JSON object for a machine
    caller (the MakoBench operator). Per label: the cases it would generate
    (public ids named, private ones only counted), estimated generation and
    judge minutes, escalation/ladder jobs, and the window verdict against
    ``--until``. Nothing is touched. ``pending_label(label)`` = rows of that
    label already waiting for a verdict, in judged-row equivalents."""
    from . import estimate as est
    from .version import SUITE_VERSION
    pending_for = pending_for or (lambda labels: pending_equiv(labels, cfg))
    pending_label = pending_label or (lambda label: pending_for([label]))
    priv = p.get("_private") or {}
    pids = set(priv.get("ids") or ())
    win = None
    hard_end = None
    if until:
        _, hard_end = deadlines(until, 0, now=start)
        win = Window(hard_end, float(judge_reserve_min or 0), no_judge=no_judge)
    sched = window_schedule(p, cfg, win, start, estimate_for=estimate_for,
                            pending_for=pending_for)
    esc = {e["model"]: int(e.get("jobs") or 0) for e in p.get("escalate") or []}
    lad = {lp["model"]: lp for lp in p.get("ladder") or []}
    labels = list(dict.fromkeys(list(p.get("candidates") or [])
                                + [a["name"] for a in p.get("add") or []]))
    models = {}
    for label in labels:
        pm = (p.get("per_model") or {}).get(label) or {}
        miss = list(pm.get("regenerate") or [])
        srow = sched["per_label"].get(label) or {}
        try:
            waiting = float(pending_label(label)) if label in (p.get("judge") or []) else 0.0
        except Exception:  # noqa: BLE001 — an estimate never fails the plan
            waiting = 0.0
        jeq = float(srow.get("judge_eq") or 0.0) + waiting
        lp = lad.get(label)
        if miss:
            verdict = srow.get("verdict", "no_window" if win is None else "does_not_fit")
        elif lp:
            verdict = "ladder_only"
        elif label in (p.get("judge") or []):
            verdict = "judge_skipped" if no_judge else "judge_only"
        elif esc.get(label):
            verdict = "escalate_only"
        else:
            verdict = "nothing"
        models[label] = {
            "register": label in {a["name"] for a in p.get("add") or []},
            "missing_cases": len(miss),
            "missing_public": [c for c in miss if c not in pids],
            "missing_private": sum(1 for c in miss if c in pids),
            "rejudge_cases": len(pm.get("rejudge") or []),
            "gen_min": srow.get("gen_min", 0.0 if not miss else None),
            "judge_min": 0.0 if no_judge else round(est.judge_min(jeq, 3), 1) if jeq else 0.0,
            "judge_deferred": bool(no_judge and jeq),
            "escalate_jobs": esc.get(label, 0),
            "ladder_jobs": int(lp["jobs"]) if lp else 0,
            "ladder_sets": len(lp.get("sets") or []) if lp else 0,
            "verdict": verdict,
            "slot": ({"start": srow["start"], "end": srow["end"]}
                     if srow.get("start") else None),
        }
    fits = [l for l, m in models.items() if m["verdict"] in ("fits", "oversize_first")]
    left = [l for l, m in models.items() if m["verdict"] == "does_not_fit"]
    out = {
        "schema": 1, "suite": SUITE_VERSION, "kind": "bench-all-plan",
        "only": p.get("only"), "only_unknown": list(p.get("only_unknown") or []),
        "lease_mode": lease_mode, "no_judge": bool(no_judge),
        "rig_models": p.get("rig_models"), "case_count": p.get("case_count"),
        "nothing_to_do": nothing_to_do(p),
        "register": [a["name"] for a in p.get("add") or []],
        "prune": [l for l, _ in p.get("prune") or []],
        "not_on_rig": list(p.get("not_on_rig") or []),
        "kept_on_board": list(p.get("kept_on_board") or []),
        "hosted_pending": list(p.get("hosted_pending") or []),
        "models": models,
        "window": {
            "until": until, "start": _hm(start),
            "hard_end": hard_end.isoformat(timespec="minutes") if hard_end else None,
            "judge_cut": _hm(win.judge_cut) if win else None,
            "gen_stop": sched.get("gen_stop"),
            "judge_min": 0.0 if no_judge else sched.get("judge_min"),
            "judge_fits": sched.get("judge_fits"),
            "fits": fits, "left": left,
            "verdict": ("no_window" if win is None else "fits" if not left
                        and sched.get("judge_fits") is not False else "partial"
                        if fits else "does_not_fit" if left else "fits"),
        },
    }
    return out


def dump_plan_json(obj: dict, p: dict) -> str:
    """One line of JSON, private terms replaced like format_plan does."""
    import json
    import re as _re
    text = json.dumps(obj, sort_keys=False, ensure_ascii=False)
    for term in (p.get("_private") or {}).get("terms") or ():
        if term and term in text:
            text = _re.sub(r"(?<![\w-])" + _re.escape(term) + r"(?![\w-])", "private", text)
    return text


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
    as_json = bool(getattr(args, "plan_json", False))

    def _down(msg: str) -> int:
        if as_json:
            import json
            print(json.dumps({"schema": 1, "kind": "bench-all-plan", "error": msg,
                              "exit": EXIT_RIG_DOWN}))
        else:
            print(f"bench-all: {msg}")
        return EXIT_RIG_DOWN
    if not sf:
        return _down("no studioforge provider in models.yaml")
    prov = get_provider(cfg, sf[0])
    rig_ids = set(prov.list_models(refresh=True))
    if not rig_ids:
        return _down("the rig returned no model list — is the rig up?")
    board = sorted({p.stem.removeprefix("transcripts_") for p in results_dir().glob("transcripts_*.jsonl")}
                   | {p.stem.removeprefix("meta_") for p in results_dir().glob("meta_*.json")})
    first = [x.strip() for x in (getattr(args, "first", None) or "").split(",") if x.strip()]
    only_arg = getattr(args, "only", None)
    only = ([x.strip() for x in only_arg.split(",") if x.strip()]
            if only_arg is not None else None)
    lease_mode = getattr(args, "lease_mode", None)
    # day mode never judges (the judge needs cards a day run may not take)
    no_judge = bool(getattr(args, "no_judge", False)) or lease_mode == "day"
    p = plan(cfg, rig_ids, include_api=args.include_api, board_labels=board, first=first,
             rig_arch=rig_arches(prov), only=only,
             register_new=bool(getattr(args, "register_new", False))
             and not getattr(args, "no_sync", False))
    p["_provider"] = sf[0]
    p["no_judge"] = no_judge
    qreason = getattr(args, "quant_reason", None)
    if qreason:
        # MakoBench sizing (2026-11-06): a Q6/Q5 registration says why it is not Q8_0
        if qreason not in QUANT_REASONS:
            print(f"bench-all: --quant-reason must be one of {', '.join(sorted(QUANT_REASONS))}")
            return EXIT_USAGE
        if only is None or len(only) != 1 or len(p["add"]) != 1:
            print("bench-all: --quant-reason needs --only with ONE label that is a new "
                  "registration (nothing else gets a quant reason)")
            return EXIT_USAGE
        p["add"][0]["quant_reason"] = qreason
    if only is not None and (not only or set(p.get("only_unknown") or []) >= set(only)):
        # --only named nothing that exists: say so, never fall back to "all"
        if getattr(args, "plan_json", False):
            print(dump_plan_json({"schema": 1, "kind": "bench-all-plan", "error":
                                  "--only names no registered or rig model",
                                  "only": only, "only_unknown": p.get("only_unknown")}, p))
        else:
            print(f"bench-all: --only {only_arg!r} names no registered or rig model — "
                  f"nothing to do")
        return EXIT_USAGE
    if getattr(args, "plan_json", False):
        start = datetime.now()
        ps = getattr(args, "plan_start", None)
        if ps:
            hh, mm = (int(x) for x in ps.split(":", 1))
            start = start.replace(hour=hh, minute=mm, second=0, microsecond=0)
        print(dump_plan_json(plan_json(
            p, cfg, until=getattr(args, "until", None), start=start,
            judge_reserve_min=float(getattr(args, "judge_reserve_min", 45) or 0),
            no_judge=no_judge, lease_mode=lease_mode), p))
        return EXIT_OK
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
                getattr(args, "judge_reserve_min", 45) or 0), no_judge=no_judge), start)
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
    win = (Window(hard_end, float(getattr(args, "judge_reserve_min", 45) or 0),
                  no_judge=no_judge) if hard_end else None)
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
                skip_conformance=bool(getattr(args, "skip_conformance", False)),
                allow_unpriced=bool(getattr(args, "allow_unpriced", False)))
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
        if judged_labels and no_judge:
            notes.append("judge skipped (--no-judge" + (", day lease mode" if lease_mode == "day"
                                                       else "") + "): rows of "
                         + ", ".join(judged_labels) + " wait for a judged run")
        elif judged_labels and not _judge_room():
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
        lad = first_ahead(sorted((set(judged_labels) | {lp["model"] for lp in p.get("ladder") or []})
                                 - set(left)), p.get("first"))
        if no_judge and (lad or p.get("ladder")):
            # a ladder set unlocks the next one from its JUDGED rows
            notes.append("ladder packs skipped (--no-judge) — they run in a judged run")
        elif lad and not cli.stopping() and not judge_stop.is_set() and _judge_room() \
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
