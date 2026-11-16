"""The 4.5 time budget check (`crucibleforge budget`, PLAN-45-v4 D1, E7).

The main test (Core) is budgeted for a HOME machine, literally: a non-thinking
model at ``budget.home.tps`` tokens/s on ``slots`` slots, plus the judge
(``judge_s_per_call`` per call) and the model load::

    core_home_min = nominal_tokens / (tps x slots) / 60
                    + judge_calls x judge_s_per_call / 60 + load_min

and it must be <= ``limit_min`` (30). The same sums are priced on two machines:
``home`` (one 24 GB card, the judge a Q5_K_M quant) and ``rig`` (the tested
model still one slot at ``tps``, the judge on the 5090 pair with parallel
slots, so ``judge_parallel`` calls overlap and prefill is faster); both must
fit. A ``thinker`` line prices a thinking model at ``thinker_factor`` x the
answer tokens, reported, never gated. Packs (the ladder sets) are priced the
same way and reported as ``packs_min``, never gated. Thinking models are not in
the gate (their real time is the board's ``bench_min``). The old 120B-anchor
framework is retired (D1). Everything is computed from committed data, so CI
needs no ``results/``:

* ``budget/targets-45.json``  per-case design targets (output tokens, expected
  judge calls, prefill seconds), the ESTIMATE source;
* ``budget/reference.json``   a digest of per-model per-case tokens, built by
  ``scripts/build_budget_reference.py`` from a results directory;
* the profile's ``budget:`` block: ``reference_models`` and ``home``.

Nominal tokens of a case, best source first:

NOMINAL   the median over the profile's ``reference_models`` of their ANSWER
          tokens (completion minus reasoning/thinking tokens; per-turn median
          over episodes, summed) on rows matching the case's CURRENT
          ``case_hash``;
ESTIMATE  ``meta.budget.nominal_tokens`` (else the older ``anchor_tokens``,
          with a note), else the committed per-case target, else a generated
          case's family ``LEVELS[level]['answer_tokens']``;
TARGET    the committed target of a Core instance that cannot be built in this
          tree yet (its family or level arrives with another branch): the same
          number an ESTIMATE would use, marked so nobody reads it as measured.

A reference model only counts as a NOMINAL source when it ran with thinking OFF:
with ``budget.thinking_off_required`` (bench45) a digest entry whose
``thought_share`` (the share of its rows that carry reasoning tokens, the
``scripts/budget_thinkoff`` rule) is above ``budget.max_thought_share`` (0.02),
or that states none, is refused and a note says so (A8 request 1: the answer
tokens of a thinking run are a different quantity).

A target entry with ``pending_from_hash`` says "this target is for the 4.5
rewrite of a case that still has its 4.1 content": rows of that old content
do not count until the case changes.
"""
from __future__ import annotations

import collections
import json
import re
import statistics as st
from pathlib import Path

from . import config as _config
from . import hard as _hard

SCHEMA_TARGETS = "budget-targets/1"
SCHEMA_REFERENCE = "budget-reference/1"
DEFAULT_TARGETS = "budget/targets-45.json"
DEFAULT_REFERENCE = "budget/reference.json"
#: a MEASURED/PROXY case more than this share over its target fails the check
#: unless it carries a retarget note
OVERSHOOT_FAIL = 0.25
#: expected judge calls of a craft element (sequential majority, PLAN-45-v3 2.3)
CRAFT_CALLS = 2.2
#: budget.home defaults (PLAN-45-v4 section 1): a non-thinking model at 20 tok/s,
#: one slot, 25 s per judge call, 3 min of model loads, 30 min limit
HOME_DEFAULTS = {"tps": 20.0, "slots": 1, "judge_s_per_call": 25.0, "load_min": 3.0,
                 "limit_min": 30.0, "judge_parallel": 1, "prefill_scale": 1.0}
#: budget.rig defaults: measured 2026-11-13 (EXP-20261113-25, anchors/rig_throughput.yaml): the
#: reference (~30B Q8_0, thinking on) on the 3090 pair at 73,728 ctx/slot, q8_0 KV, MTP serves 4 slots
#: at 131.7 tok/s aggregate = 32.9 per stream (the old 20 tok/s x 1 slot was the home-user figure).
#: The judge runs on the pair with 2 parallel slots; committed home prefill seconds shrink to 0.2x
RIG_DEFAULTS = {"tps": 32.9, "slots": 4, "judge_s_per_call": 15.0, "load_min": 2.0,
                "limit_min": 30.0, "judge_parallel": 2, "prefill_scale": 0.2}
#: a thinking model spends about this many times the answer tokens (PLAN-45-v5 c.3b: 2-4x)
THINKER_FACTOR = 3.0
#: a reference model's share of rows that carry reasoning above which it is a thinking run
MAX_THOUGHT_SHARE = 0.02
KIND_TO_SCORE = {k: s for s, ks in _hard.SCORE_CATEGORIES.items() for k in ks}


class BudgetError(Exception):
    """A configuration problem (missing file, bad block); exit code 2."""


# ------------------------------------------------------------------ inputs

def _read_json(path: Path, what: str) -> dict:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise BudgetError(f"{what}: cannot read {path} ({e})") from e


def load_targets(path: Path) -> dict:
    t = _read_json(path, "budget targets")
    if t.get("schema") != SCHEMA_TARGETS or not isinstance(t.get("cases"), dict):
        raise BudgetError(f"budget targets: {path} is not a {SCHEMA_TARGETS} file")
    return t


def load_reference(path: Path) -> dict:
    r = _read_json(path, "budget reference")
    if r.get("schema") != SCHEMA_REFERENCE or not isinstance(r.get("models"), dict):
        raise BudgetError(f"budget reference: {path} is not a {SCHEMA_REFERENCE} file")
    return r


def settings(prof: dict, **over) -> dict:
    """The profile's ``budget:`` block with the ``home`` defaults filled in;
    ``over`` (None skipped; keys tps / slots / judge_s_per_call / load_min /
    limit_min) wins (command-line flags). Returns the block plus a resolved
    ``home`` mapping."""
    b = prof.get("budget")
    if not isinstance(b, dict):
        raise BudgetError(f"profile {prof.get('name')!r} has no budget: block")
    s = {"overshoot_fail": OVERSHOOT_FAIL, "reference_models": [],
         "targets": DEFAULT_TARGETS, "reference": DEFAULT_REFERENCE,
         "prefill_s_default": 0.0, "freeze": False, **b}
    # The enforced limit is the profile's run ceiling (R21: `default_run.ceiling_minutes`, "derived, never a
    # fixed 30"); the 20 tok/s x 1 slot home figure stays a reported number. An explicit budget.home/rig
    # limit_min (or a --limit flag) still wins.
    ceiling = (prof.get("default_run") or {}).get("ceiling_minutes")
    run_limit = {"limit_min": float(ceiling)} if ceiling else {}
    home = {**HOME_DEFAULTS, **run_limit, **(b.get("home") or {})}
    rig = {**RIG_DEFAULTS, **run_limit, **(b.get("rig") or {})}
    s["limit_source"] = "default_run.ceiling_minutes" if ceiling else "budget defaults"
    for k, v in over.items():
        if v is not None:
            home[k] = v
            if k in ("tps", "slots"):          # the tested model's speed: the same on both machines
                rig[k] = v
    for name, m in (("home", home), ("rig", rig)):
        for k, v in m.items():
            if k not in HOME_DEFAULTS:
                raise BudgetError(f"budget.{name}.{k}: unknown key (known: {sorted(HOME_DEFAULTS)})")
            if not isinstance(v, (int, float)) or isinstance(v, bool) or v <= 0:
                raise BudgetError(f"budget.{name}.{k} must be a positive number")
    s["home"] = home
    s["rig"] = rig
    s["thinker_factor"] = float(b.get("thinker_factor", THINKER_FACTOR))
    s["thinking_off_required"] = bool(b.get("thinking_off_required", False))
    s["max_thought_share"] = float(b.get("max_thought_share", MAX_THOUGHT_SHARE))
    return s


# ------------------------------------------------------------------ cases

def _score_of(category: str) -> str:
    return KIND_TO_SCORE.get(category, "code")


def _stub(c: dict, why: str) -> dict:
    """A Core instance this tree cannot build (family or level not here yet): enough of a case
    for the budget to price it from its committed target."""
    return {"id": c["id"], "category": c["category"], "meta": {"unbuilt": why}}


def case_list(prof: dict, cases_dir: Path | None = None) -> list[dict]:
    """The profile's Core cases to budget: [{id, score, category, hard, case}] in
    profile order (``hard`` is always False since 4.5 retired hard sets). ``cases_dir`` defaults to the tree.
    A generated instance that cannot be materialised here, and an instance held back as
    ``pending`` (its family is on a branch not merged), is listed with a stub case (priced from
    its committed target, source TARGET)."""
    cases_dir = Path(cases_dir) if cases_dir else _config.CASES_DIR
    # perf (speed probes) and any other unscored category are not part of the test
    cats = [k for k in (prof.get("cases") or {}) if k in KIND_TO_SCORE]
    loaded = {c["id"]: c for c in _config.load_cases(cats, cases_dir=cases_dir)}
    from . import gen as _gen
    from .profiles import core_generated_cases, core_pending
    for c in core_generated_cases(prof):        # Core instances are profile data, not case-file entries
        if c["id"] not in loaded and c["category"] in cats:
            try:
                loaded[c["id"]] = _gen.materialize_case(c)
            except Exception as e:  # noqa: BLE001 - a level this tree does not have yet
                loaded[c["id"]] = _stub(c, f"{type(e).__name__}: {e}")
    out = []
    for cat in cats:
        for i in prof["cases"][cat] or []:
            c = loaded.get(i)
            if c is None:
                continue
            out.append({"id": i, "score": _score_of(cat), "category": cat, "hard": False, "case": c})
    for p in core_pending(prof):
        if p["category"] in KIND_TO_SCORE:
            out.append({"id": p["id"], "score": _score_of(p["category"]), "category": p["category"],
                        "hard": False, "case": _stub(p, f"family {p['family']} is on {p['pending']}")})
    return out


def case_list_ids(prof: dict, cases_dir: Path | None = None) -> list[dict]:
    """:func:`case_list` without the case bodies: [{id, score, category}]."""
    return [{k: r[k] for k in ("id", "score", "category")} for r in case_list(prof, cases_dir)]


def target_entry(targets: dict, cid: str) -> dict | None:
    """The committed target of a case id: its own entry, else (a generated Core instance
    ``<FAMILY>[-<variant>]-core-<level>-<i>``) the family entry ``<FAMILY>[-<variant>]-core-<level>``
    under ``targets["families"]``."""
    e = (targets.get("cases") or {}).get(cid)
    if e is not None:
        return e
    m = re.match(r"^(.+-core-\d+)-\d+$", cid)
    return (targets.get("families") or {}).get(m.group(1)) if m else None


def _hashes(cases_dir: Path | None) -> dict:
    from . import version
    return version.case_table(Path(cases_dir)) if cases_dir else version.case_table()


def _engine_expected_calls(case: dict, prof: dict | None) -> float | None:
    """The judge engine's own expected-call count for a case, when the engine
    exposes ``judge.expected_calls(case, policy_cfg)`` (4.5 fix pass); None
    otherwise, or when it raises, and the derivation below is used."""
    from . import judge
    fn = getattr(judge, "expected_calls", None)
    if fn is None:
        return None
    try:
        # the engine wants the effective policy (defaults < the profile's block)
        pcfg = {k: v for k, v in {**judge.JUDGE_POLICY_DEFAULTS,
                                  **((prof or {}).get("judge_policy") or {})}.items()
                if k in judge.JUDGE_POLICY_DEFAULTS}
        return float(fn(case, pcfg)["calls"])      # fix-d (B4): it returns a dict, not a number
    except Exception:  # noqa: BLE001 - a budget estimate must not hide the case
        return None


def tree_judge_calls(case: dict, prof: dict | None = None) -> float:
    """Expected judge calls for one case from its elements and the profile's
    judge policy: code (no rubric, no elements) 0; a fact group (elements that
    grade the same turns) costs the fact sample count; craft costs the
    sequential-majority estimate; other judged rows cost judge_policy samples.
    Element classes follow the engine's ``judge.element_class`` rule (an
    element with no ``class`` whose ``dim`` is a rubric dimension is craft)."""
    els = [e for e in (case.get("judge_elements") or []) if e.get("status") != "retire"]
    rubric = case.get("rubric")
    if not els and not rubric:
        return 0.0
    eng = _engine_expected_calls(case, prof)
    if eng is not None:
        return round(eng, 2)
    from . import judge
    from .rpdeep import fact_groups
    pol = (prof or {}).get("judge_policy") or {}
    ec = pol.get("element_class") or {}
    fact_n = int(ec.get("fact", 1))
    if rubric in ("rp_deep", "chat_deep") or els:
        facts = [e for e in els if judge.element_class(e, rubric) != "craft"]
        # grouped like the engine (group_facts / group_window), retired elements skipped
        groups = fact_groups(facts, pol.get("group_facts"), pol.get("group_window"))
        n = len(groups) * fact_n
        craft = any(judge.element_class(e, rubric) == "craft" for e in els) \
            or rubric in ("rp_deep", "chat_deep")
        return round(n + (CRAFT_CALLS if craft else 0.0), 2)
    try:
        return float(judge.judge_policy({"case_id": case.get("id"), "rubric": rubric, **{
            k: case.get(k) for k in ("willing", "explicit_required")}}, pol or None, None, set())["samples"])
    except Exception:  # noqa: BLE001 — a missing policy must not hide the case
        return 1.0


# ------------------------------------------------------------------ the digest

def _med(xs):
    return st.median(xs) if xs else None


def thought(row: dict) -> bool:
    """A stored row that thought: reasoning tokens, or reasoning text (the scripts/budget_thinkoff rule)."""
    m = row.get("metrics") or {}
    return bool((m.get("reasoning_tokens") or 0) > 0 or (row.get("reasoning") or "").strip())


def thinking_off(entry: dict | None, max_share: float = MAX_THOUGHT_SHARE) -> bool:
    """True when a digest entry states a ``thought_share`` at or below ``max_share``; an entry
    with none (a digest built before this check) is NOT thinking-off: unknown fails closed."""
    ts = (entry or {}).get("thought_share")
    return ts is not None and float(ts) <= max_share


def measured_tokens(entry: dict | None, h: str | None) -> float | None:
    """Total output tokens of a digest case entry if its content hash is h."""
    if not entry or not h or entry.get("hash") != h:
        return None
    tt = entry.get("turn_tokens") or []
    return float(sum(tt)) if tt else None


def nominal_tokens(models: dict, labels: list[str], cid: str, h: str | None) -> float | None:
    """The median over ``labels`` of their MEASURED output tokens on case
    ``cid`` at content hash ``h``; None when none of them ran this content."""
    vals = [t for t in (measured_tokens(((models.get(m) or {}).get("cases") or {}).get(cid), h)
                        for m in labels) if t]
    return float(st.median(vals)) if vals else None


def compute(prof: dict, *, cases_dir=None, targets: dict, reference: dict,
            only: list[str] | None = None, tps: float | None = None, slots: int | None = None,
            judge_s: float | None = None, extra_reference: dict | None = None,
            require_measured: bool | None = None, packs: bool = True) -> dict:
    """The whole report (see module doc). ``extra_reference`` = a digest built
    from a live results dir (``--results``), merged over the committed one."""
    cfg = settings(prof, tps=tps, slots=slots, judge_s_per_call=judge_s)
    home, rig = cfg["home"], cfg["rig"]
    freeze = bool(cfg.get("freeze")) if require_measured is None else bool(require_measured)
    notes: list[str] = []
    models = dict(reference.get("models") or {})
    if models and reference.get("token_basis") != "answer":
        # a digest built before fix-d counted thinking tokens: never a NOMINAL source
        notes.append("the committed reference digest counts thinking tokens (no token_basis: answer) "
                     "and is ignored; rebuild it with scripts/build_budget_reference.py")
        models = {}
    if extra_reference:
        models.update(extra_reference.get("models") or {})
    refs = [m for m in cfg["reference_models"] if m in models]
    missing_ref = [m for m in cfg["reference_models"] if m not in models]
    if missing_ref:
        notes.append("reference models not in the digest: " + ", ".join(missing_ref))
    if cfg.get("thinking_off_required"):
        think = [m for m in refs if not thinking_off(models[m], cfg["max_thought_share"])]
        if think:
            refs = [m for m in refs if m not in think]
            notes.append("reference models that are thinking runs (thought_share above "
                         f"{cfg['max_thought_share']:g} or not stated) are not a NOMINAL source: "
                         + ", ".join(think) + "; committed targets are used until a thinking-off "
                         "reference run exists")
    hashes = _hashes(cases_dir)
    n_slots = int(home["slots"])
    rows = []
    anchor_only: list[str] = []
    for item in case_list(prof, cases_dir):
        cid, c = item["id"], item["case"]
        te = target_entry(targets, cid) or {}
        h = (hashes.get(cid) or {}).get("case")
        pending = bool(te.get("pending_from_hash")) and te["pending_from_hash"] == h
        mb = (c.get("meta") or {}).get("budget") or {}
        target = mb.get("nominal_tokens")
        if target is None and mb.get("anchor_tokens") is not None:
            target = mb["anchor_tokens"]
            anchor_only.append(cid)
        if target is None:
            target = te.get("target_tokens")
        if target is None:
            target = _generated_tokens(c)
        note = te.get("retarget_note") or ((c.get("meta") or {}).get("budget") or {}).get("retarget_note")
        tokens = source = None
        if not pending:
            nt = nominal_tokens(models, refs, cid, h)
            if nt:
                tokens, source = nt, "NOMINAL"
        if tokens is None and target:
            tokens, source = float(target), ("TARGET" if (c.get("meta") or {}).get("unbuilt") else "ESTIMATE")
        # judge calls: the committed table for a standard case still at its
        # pending_from_hash; the tree once the case has been rewritten (and then
        # the tree may not exceed the table by more than overshoot_fail)
        calls_problem = None
        if "judge_calls" in te:
            table_calls = float(te["judge_calls"])
            if te.get("pending_from_hash") and te["pending_from_hash"] != h:
                calls, calls_src = tree_judge_calls(c, prof), "tree"
                if calls > table_calls * (1 + cfg["overshoot_fail"]) + 1e-9:
                    calls_problem = (f"{cid}: elements need {calls:.1f} judge calls, "
                                     f"{(calls / table_calls - 1) * 100:+.0f}% over the committed {table_calls:.1f}"
                                     if table_calls else
                                     f"{cid}: elements need {calls:.1f} judge calls, the committed table has none")
            else:
                calls, calls_src = table_calls, "table"
        else:
            calls, calls_src = tree_judge_calls(c, prof), "tree"
        prefill = float(te.get("prefill_s", cfg["prefill_s_default"]))
        r = {"id": cid, "score": item["score"], "hard": False, "tokens": tokens,
             "target": float(target) if target else None, "source": source or "NONE",
             "pending": pending, "calls": calls, "calls_src": calls_src, "prefill_s": prefill,
             "retarget_note": note, "hash": h, "calls_problem": calls_problem}
        r["minutes"] = ((tokens / home["tps"] + prefill) / n_slots / 60) if tokens else None
        r["rig_minutes"] = ((tokens / rig["tps"] + prefill * rig["prefill_scale"]) / int(rig["slots"]) / 60
                            if tokens else None)
        r["judge_min"] = _judge_min(calls, home)
        r["over"] = ((tokens / r["target"] - 1)
                     if (tokens and r["target"] and r["source"] == "NOMINAL") else None)
        rows.append(r)
    tgt = [r["id"] for r in rows if r["source"] == "TARGET"]
    if tgt:
        notes.append("TARGET = committed design target, not measured (the instance cannot be built in "
                     "this tree yet): " + ", ".join(tgt))
    if anchor_only:
        notes.append("meta.budget has anchor_tokens, not nominal_tokens (read as nominal): "
                     + ", ".join(anchor_only))
    core = [r for r in rows if r["tokens"]]
    gen_min = sum(r["minutes"] for r in core)
    rig_gen_min = sum(r["rig_minutes"] for r in core)
    calls_main = sum(r["calls"] for r in rows)
    judge_min = _judge_min(calls_main, home)
    rig_judge_min = _judge_min(calls_main, rig)
    total = home["load_min"] + gen_min + judge_min
    rig_total = rig["load_min"] + rig_gen_min + rig_judge_min
    # a thinking model: the same judge and loads, factor x the answer tokens (reported only)
    thinker_min = home["load_min"] + gen_min * cfg["thinker_factor"] + judge_min
    per_score = {}
    for sc in ("code", "chat", "nsfw"):
        rs = [r for r in rows if r["score"] == sc]
        per_score[sc] = {"cases": len(rs), "tokens": sum(r["tokens"] or 0 for r in rs),
                         "minutes": sum(r["minutes"] or 0 for r in rs), "calls": sum(r["calls"] for r in rs),
                         "judge_min": _judge_min(sum(r["calls"] for r in rs), home)}
    pack_rows, packs_min = (_pack_rows(prof, models, refs, targets["cases"], home)
                            if packs and not only else ([], 0.0))
    problems: list[str] = []
    for r in rows:
        if r["source"] == "NONE":
            problems.append(f"{r['id']}: no budget source (no reference-model measurement, no "
                            "meta.budget.anchor_tokens, no committed target)")
        elif r["over"] is not None and r["over"] > cfg["overshoot_fail"] and not r["retarget_note"]:
            problems.append(f"{r['id']}: NOMINAL {r['tokens']/1000:.1f}k is {r['over']*100:+.0f}% over its "
                            f"{r['target']/1000:.1f}k target (add a retarget_note or cut the case)")
        if r.get("calls_problem"):
            problems.append(r["calls_problem"])
        if freeze and r["source"] in ("ESTIMATE", "TARGET"):
            problems.append(f"{r['id']}: ESTIMATE only (freeze needs NOMINAL tokens)")
    partial = bool(only)
    if not partial:
        notes.append(f"Core {total:.1f} min at {home['tps']:g} tok/s x {n_slots} slot(s) (home figure, reported); "
                     f"{rig_total:.1f} min on the rig; enforced limit {home['limit_min']:g} min "
                     f"({cfg.get('limit_source', 'budget defaults')})")
    if not partial and total > home["limit_min"]:
        problems.append(f"Core {total:.1f} min at {home['tps']:g} tok/s x {n_slots} slot(s) > limit "
                        f"{home['limit_min']:g} (judge {home['judge_s_per_call']:g} s/call)")
    if not partial and rig_total > rig["limit_min"]:
        problems.append(f"Core {rig_total:.1f} min on the rig (rig column) > limit {rig['limit_min']:g}")
    if only:
        # --case: only the selected cases' own problems
        problems = [p for p in problems if p.split(":", 1)[0] in set(only)]
        rows = [r for r in rows if r["id"] in set(only)]
        unknown = set(only) - {r["id"] for r in rows}
        if unknown:
            notes.append("unknown --case ids: " + ", ".join(sorted(unknown)))
    return {"profile": prof.get("name"), "home": dict(home), "rig": dict(rig),
            "reference_models": refs,
            "cases": rows, "per_score": per_score,
            "core": {"tokens": sum(r["tokens"] for r in core), "load_min": home["load_min"],
                     "generation_min": gen_min, "judge_calls": calls_main, "judge_min": judge_min,
                     "total_min": total,
                     "rig": {"load_min": rig["load_min"], "generation_min": rig_gen_min,
                             "judge_min": rig_judge_min, "total_min": rig_total}},
            "core_home_min": total, "core_rig_min": rig_total,
            "thinker_min": thinker_min, "thinker_factor": cfg["thinker_factor"], "packs": pack_rows, "packs_min": packs_min,
            "partial": partial, "notes": notes, "problems": problems, "ok": not problems}


def _judge_min(calls: float, m: dict) -> float:
    """Minutes the judge takes for ``calls`` on machine ``m`` (parallel slots overlap)."""
    return calls * m["judge_s_per_call"] / max(1, int(m.get("judge_parallel", 1))) / 60


def _generated_tokens(case: dict) -> float | None:
    """A generated case's family answer-token estimate (LEVELS[level]), else None."""
    from . import gen as _gen
    try:
        if not _gen.is_generated(case):
            return None
        g = case["generator"]
        lv = _gen.get(g["type"]).LEVELS.get(g.get("level")) or {}
        t = (lv.get("answer_tokens_by_variant") or {}).get(g.get("variant")) or lv.get("answer_tokens")
        return float(t) if t else None
    except Exception:  # noqa: BLE001 - an unsized case is reported, not fatal
        return None


def _pack_rows(prof, models, refs, tcases, home) -> tuple[list[dict], float]:
    """Ladder sets priced like the Core, every rung reached (worst case):
    [{set, cases, tokens, calls, minutes, unsized}], their total minutes.
    A case's tokens: the reference models' measurement, the committed target,
    else its generator family's ``LEVELS[level]['answer_tokens']``; a case
    with none counts 0 and is listed under ``unsized``."""
    from . import gen as _gen
    from . import ladder
    out, total = [], 0.0
    try:
        sets = ladder.discover(prof)
        all_cases = {c["id"]: c for c in _config.load_cases()} if sets else {}
    except Exception as e:  # noqa: BLE001 - packs are reported, never gated
        return [{"set": "?", "error": str(e)}], 0.0
    hashes = _hashes(None) if sets else {}
    for key, seq in sets.items():
        for spec in seq:
            tokens = calls = 0.0
            unsized = []
            for cid in spec["cases"]:
                c = all_cases.get(cid)
                if c is None:
                    unsized.append(cid)
                    continue
                t = nominal_tokens(models, refs, cid, (hashes.get(cid) or {}).get("case"))
                if t is None:
                    t = (tcases.get(cid) or {}).get("target_tokens")
                if t is None and _gen.is_generated(c):
                    try:
                        lv = _gen.get(c["generator"]["type"]).LEVELS.get(c["generator"].get("level")) or {}
                        t = (lv.get("answer_tokens_by_variant") or {}).get(c["generator"].get("variant")) \
                            or lv.get("answer_tokens")
                    except Exception:  # noqa: BLE001
                        t = None
                if not t:
                    unsized.append(cid)
                tokens += float(t or 0)
                calls += tree_judge_calls(c, prof)
            minutes = tokens / (home["tps"] * home["slots"]) / 60 + calls * home["judge_s_per_call"] / 60
            total += minutes
            out.append({"set": f"{key}#{spec['n']}", "cases": len(spec["cases"]), "tokens": tokens,
                        "calls": calls, "minutes": minutes, "unsized": unsized})
    return out, total


# ------------------------------------------------------------------ digest builder

_EXCLUDE_DEFAULT = {
    "hy-mt2-30b-a3b": "translation specialist", "smolvlm-256m": "vision model",
    "qwen2.5-vl-7b-caption": "vision model", "qwen3.5-4b-glb-3d-coder.q8-0": "specialist (3D coder)",
    "kimi-k3": "superseded by kimi-k3-1-pro", "grok-4-6": "superseded by grok-4-7",
    "dark-scarlett-35b-iq4-xs-uncat": "duplicate of dark-scarlett-35b line",
    "dark-scarlett-35b-v2": "duplicate of dark-scarlett-35b line",
    "split-untied-31b-q8": "duplicate quant of split-untied-31b"}
_OPENROUTER = {"gpt-6-1-terra", "gpt-6-luna"}
_SMALL = {"qwen2.5-0.5b", "qwen2.5-1.5b", "gemma-2-2b-abliterated", "spark-x2.6-4b-q8-0", "deepseek-r1-0528-8b",
          "celeste-12b", "noromaid-7b", "gpt-oss-20b-q8-0", "cydonia-22b"}


def _slots_of(label, meta):
    rw = [r.get("workers") for r in (meta.get("rounds") or []) if r.get("workers")]
    par = (meta.get("plan") or {}).get("parallel")
    if meta.get("provider") == "studioforge":
        return int(par or (rw[0] if rw else 4))
    if label in _OPENROUTER:
        return 1
    return int(rw[0]) if rw else 4


def _class_of(label, meta):
    if meta.get("provider") != "studioforge":
        return "hosted"
    if label in _SMALL:
        return "local-small"
    return "local-26-35-think" if (meta.get("regime") or {}).get("thinking") else "local-26-35-nothink"


def _m(r, k):
    return ((r.get("metrics") or {}).get(k)) or 0


def answer_tokens(r: dict) -> float:
    """The ANSWER tokens of a transcript row: completion tokens minus the
    reasoning/thinking tokens the row carries (``metrics.reasoning_tokens``; a
    server that reports none gets len(reasoning text)/4), never below 0.
    PLAN-45-v4 section 1: thinking tokens are never in the nominal count."""
    comp = _m(r, "completion_tokens")
    reas = _m(r, "reasoning_tokens")
    if not reas:
        reas = len(r.get("reasoning") or "") // 4
    return max(0.0, comp - reas)


def digest_model(label: str, rows: list[dict], meta: dict, ids: set[str],
                 anchor: str | None = None) -> dict | None:
    """One model's digest entry from its transcript rows (None if it has no
    measured main case). Per case: the stamp (case hash) of its latest complete
    episode, the per-turn median output tokens over the episodes/repeats that
    carry that stamp, and the median seconds."""
    from . import version
    by = collections.defaultdict(lambda: collections.defaultdict(dict))
    for r in rows:
        cid = r.get("case_id")
        if cid in ids:
            by[cid][(r.get("bench_run_id"), r.get("round"))][(r.get("run_id"), r.get("turn"), r.get("repeat"))] = r
    cases = {}
    for cid, eps in by.items():
        cand = []
        for k, d in eps.items():
            rl = list(d.values())
            if sum(_m(x, "completion_tokens") for x in rl) <= 0:
                continue
            cand.append((max(x.get("ts") or "" for x in rl), k, rl))
        if not cand:
            continue
        cand.sort(key=lambda x: x[0])
        stamp = version.row_case_stamp(cand[-1][2][0])
        same = [c for c in cand if version.row_case_stamp(c[2][0]) == stamp]
        # one unit per episode: tokens per turn bucket (tool-use steps and
        # repeats of a turn are summed, as WP-01 did); an episode that ran out
        # of its output budget is dropped when a clean one exists
        units = []
        for _ts, _k, rl in same:
            turns = collections.defaultdict(float)
            for x in rl:
                turns[x.get("turn") or 0] += answer_tokens(x)
            cut = any(x.get("truncated") or x.get("finish_reason") == "length" for x in rl)
            units.append((dict(turns), sum(_m(x, "total_s") for x in rl), cut))
        if any(not u[2] for u in units):
            units = [u for u in units if not u[2]]
        units = [(u[0], u[1]) for u in units]
        keys = sorted({t for u, _ in units for t in u})
        cases[cid] = {"hash": stamp,
                      "turn_tokens": [round(_med([u.get(t, 0) for u, _ in units])) for t in keys],
                      "seconds": round(_med([s for _, s in units]), 1), "episodes": len(units)}
    if not cases:
        return None
    tps = [_m(r, "tok_per_s") for r in rows if r.get("case_id") in ids and _m(r, "tok_per_s")]
    rr = [r for r in rows if r.get("case_id") in ids]
    share = round(sum(1 for r in rr if thought(r)) / len(rr), 3) if rr else None
    local = meta.get("provider") == "studioforge"
    return {"class": _class_of(label, meta), "hosted": not local,
            "stream_tps": round(_med(tps), 1) if tps else None, "slots": _slots_of(label, meta),
            "load_min": round((meta.get("load_s") or 0) / 60, 2) if local else 0.0,
            "thought_share": share, "cases": cases}


def build_reference(results_dir: Path, prof: dict, cases_dir: Path | None = None,
                    exclude: dict | None = None, min_cases: int = 20, labels: list[str] | None = None) -> dict:
    """The digest of a results directory (public case ids, numbers only).
    Roster = every model with >= ``min_cases`` of the profile's standard cases
    (the board), minus ``exclude``; ``labels`` restricts it (live check)."""
    results_dir = Path(results_dir)
    from . import profiles
    hidden = set(profiles.private_scope(None, prof.get("name") or profiles.DEFAULT_PROFILE).get("case_ids") or ())
    ids = {i for cat, v in (prof.get("cases") or {}).items() if cat in KIND_TO_SCORE
           for i in v or [] if i not in hidden}
    anchor = (prof.get("budget") or {}).get("anchor")      # legacy key, only kept in the digest
    excl = _EXCLUDE_DEFAULT if exclude is None else exclude
    models = {}
    for p in sorted(results_dir.glob("transcripts_*.jsonl")):
        label = p.name[len("transcripts_"):-len(".jsonl")]
        if label in excl or (labels and label not in labels):
            continue
        rows = []
        for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                rows.append(json.loads(line))
            except ValueError:
                continue
        try:
            meta = json.loads((results_dir / f"meta_{label}.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            meta = {}
        d = digest_model(label, rows, meta, ids, anchor)
        if d and (len(d["cases"]) >= min_cases or labels):
            models[label] = d
    return {"schema": SCHEMA_REFERENCE, "token_basis": "answer", "anchor": anchor, "note": "public case ids and numbers only; no prompts or responses",
            "excluded": excl, "models": models}


# ------------------------------------------------------------------ output

def _k(x):
    return "-" if x is None else f"{x/1000:.1f}k"


def render(rep: dict) -> str:
    L = []
    h = rep["home"]
    L.append(f"budget: profile {rep['profile']}, home {h['tps']:g} tok/s x {h['slots']:g} slot(s), "
             f"judge {h['judge_s_per_call']:g} s/call, load {h['load_min']:g} min, limit {h['limit_min']:g} min")
    L.append(f"{'case':30} {'score':5} {'tokens':>7} {'target':>7} {'over':>6} {'min':>5} {'calls':>5} {'j.min':>5}  source")
    for r in rep["cases"]:
        over = "" if r["over"] is None else f"{r['over']*100:+.0f}%"
        flag = ""
        if r["over"] is not None and r["over"] > OVERSHOOT_FAIL and not r["retarget_note"]:
            flag = "  OVER"
        elif r["over"] is not None and r["over"] > 0:
            flag = "  over"
        tag = r["source"] + (" (pending 4.5 rewrite)" if r["pending"] else "")
        L.append(f"{r['id'][:30]:30} {r['score']:5} {_k(r['tokens']):>7} {_k(r['target']):>7} {over:>6} "
                 f"{(r['minutes'] or 0):5.2f} {r['calls']:5.1f} {r['judge_min']:5.2f}  {tag}{flag}")
    L.append("")
    for sc, v in rep["per_score"].items():
        L.append(f"  {sc:5} {v['cases']:2} cases  {v['tokens']/1000:6.1f}k  {v['minutes']:5.2f} min gen  "
                 f"{v['calls']:5.1f} calls  {v['judge_min']:5.2f} min judge")
    c = rep["core"]
    rg = c["rig"]
    r = rep["rig"]
    L.append(f"{'':16} {'rig':>8} {'home':>8}   (nominal = answer tokens only; thinking tokens are never counted)")
    L.append(f"{'load min':16} {rg['load_min']:8.2f} {c['load_min']:8.2f}")
    L.append(f"{'generation min':16} {rg['generation_min']:8.2f} {c['generation_min']:8.2f}   "
             f"{c['tokens']/1000:.1f}k nominal tokens at {h['tps']:g} tok/s, one slot")
    L.append(f"{'judge min':16} {rg['judge_min']:8.2f} {c['judge_min']:8.2f}   {c['judge_calls']:.1f} calls "
             f"({r['judge_s_per_call']:g} s on {r['judge_parallel']:g} slots / {h['judge_s_per_call']:g} s)")
    L.append(f"{'total min':16} {rg['total_min']:8.2f} {c['total_min']:8.2f}   limit {r['limit_min']:g} / "
             f"{h['limit_min']:g}")
    L.append(f"core_home_min: {rep['core_home_min']:.2f} = load {c['load_min']:.2f} + generation "
             f"{c['generation_min']:.2f} ({c['tokens']/1000:.1f}k nominal tokens) + judge {c['judge_min']:.2f} "
             f"({c['judge_calls']:.1f} calls)  (limit {h['limit_min']:g})")
    L.append(f"core_rig_min: {rep['core_rig_min']:.2f}  (limit {r['limit_min']:g})")
    L.append(f"thinker (about {rep['thinker_factor']:g}x the answer tokens, home machine; reported, not gated): "
             f"{rep['thinker_min']:.1f} min")
    for p in rep["packs"]:
        if p.get("error"):
            L.append(f"  pack: cannot price ({p['error']})")
            continue
        un = f"  [{len(p['unsized'])} unsized]" if p["unsized"] else ""
        L.append(f"  pack {p['set']:22} {p['cases']:3} cases {p['tokens']/1000:6.1f}k {p['calls']:5.1f} calls "
                 f"{p['minutes']:6.2f} min{un}")
    if rep["packs"]:
        L.append(f"packs_min: {rep['packs_min']:.1f} (every rung reached; reported, not gated)")
    for n in rep["notes"]:
        L.append(f"note: {n}")
    for p in rep["problems"]:
        L.append(f"FAIL: {p}")
    if rep["partial"]:
        L.append("(partial: --case given, the limit is not checked)")
    L.append("budget OK" if rep["ok"] else "budget FAILED")
    return "\n".join(L)


def paths(prof: dict, root: Path | None = None) -> tuple[Path, Path]:
    root = Path(root) if root else _config.ROOT_DIR
    b = prof.get("budget") or {}
    return root / b.get("targets", DEFAULT_TARGETS), root / b.get("reference", DEFAULT_REFERENCE)


def run(prof: dict, *, cases_dir=None, targets_path=None, reference_path=None, results=None,
        only=None, tps=None, slots=None, judge_s=None, require_measured=None) -> dict:
    """Load the committed inputs (and an optional live results digest) and compute."""
    tp, rp = paths(prof)
    targets = load_targets(Path(targets_path) if targets_path else tp)
    reference = load_reference(Path(reference_path) if reference_path else rp)
    extra = build_reference(Path(results), prof, cases_dir, min_cases=1,
                            labels=list((prof.get("budget") or {}).get("reference_models") or [])
                            ) if results else None
    return compute(prof, cases_dir=cases_dir, targets=targets, reference=reference, only=only,
                   tps=tps, slots=slots, judge_s=judge_s, extra_reference=extra,
                   require_measured=require_measured)


def check_problems(prof: dict, cases_dir=None) -> list[str]:
    """For `cases verify`: the budget check's problems ([] if it passes)."""
    try:
        return run(prof, cases_dir=cases_dir)["problems"]
    except BudgetError as e:
        return [str(e)]
