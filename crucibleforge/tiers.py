"""Tiered testing (suite 3.9, PLAN §4): every model runs Tier 1 (the profile's
``cases:``); Tier 2 and Tier 3 are unlocked PER SCORE (Code, Reason, Chat).

The profile's ``tiers:`` block::

    tiers:
      gates:
        t2: {code: 75, reason: 80, chat: 75}    # on that score's T1 (0-100)
        t3: {code: 60, reason: 60, chat: 60}    # on that score's T2 (0-100)
      t2:
        code: {coding: [CZ02-...], codebank: [...], codeagent: [...]}
        reason: {math: [...]}
        chat: {rpdeep: [...]}
      t3:
        context: 73728          # T3 items load the model at this context
        code: {codebank_long: [...], codeagent_long: [...]}
      repeats: {t2: 1, t3: 1}

Scoring (per score): ``T1 + 0.5*T2 + 0.5*T3`` (max 200). A tier that was not
unlocked scores 0 (it failed the gate — real information, never "n/a"). A
tier whose items the model's context cannot serve scores 0 and is labelled
"n/a (ctx)". A tier with no items for a score (T3 Reason/Chat are not built)
simply adds nothing. The board shows the total with the tier reached, e.g.
"112 (T2)".

Each tier's 0-100 is the same weighted scorecard as T1 (report.scorecard),
over the rows of that tier's cases only, with the profile's weights limited
to the components the tier measures.
"""
from __future__ import annotations

import re

SCORES = ("code", "reason", "chat")
#: score -> the report's scorecard group
GROUP = {"code": "coding", "reason": "reason", "chat": "chat"}

#: The scores of a profile WITHOUT a ``scores:`` block (suite 3.9 before the
#: 2026-10-28 split): Code, Reason, Chat, all in the total.
LEGACY_SCORES = [{"key": "code", "group": "coding", "label": "Code", "in_total": True},
                 {"key": "reason", "group": "reason", "label": "Reason", "in_total": True},
                 {"key": "chat", "group": "chat", "label": "Chat", "in_total": True}]


def scores(prof: dict | None) -> list[dict]:
    """The profile's headline scores, in board order: ``[{key, group, label,
    in_total, cases, parts}]``. A profile declares them (the maintainer 2026-10-28:
    Coding/Work and Chat make the total, NSFW is shown beside it and never
    summed)::

        scores:
          work: {label: Coding/Work}
          chat: {label: Chat, cases: [RPS1-..., RD1-...]}     # optional row filter
          nsfw:
            label: NSFW
            in_total: false
            parts:                                           # optional sub-scores
              uncensored: {label: Uncensored, weight: 1, cases: [...], scoring: {willing: 10}}
              nsfw_rp:    {label: NSFW RP, cases: [...], scoring: {nsfw: 10}}

    The score key is also its scorecard group (``scoring.<key>`` holds its
    component weights) and its key in the ``tiers:`` lists and gates.
    ``cases`` limits the TIER-1 rows a score (or a part) is computed from —
    the components are per category, so this is how two scores split one
    category's cases (e.g. the erotic-RP session must not feed Chat's RP).
    A score with ``parts`` is the weighted mean of its parts (each scored
    like a score, from its own ``scoring`` weights and ``cases``); the parts
    get their own headline S ± m."""
    decl = (prof or {}).get("scores")
    if not decl:
        return [dict(x) for x in LEGACY_SCORES]
    out = [_score_def(key, spec or {}) for key, spec in decl.items()]
    if any(isinstance((spec or {}).get("hard"), dict) for spec in decl.values()):
        # 4.5: each score's hard set is one more part (cases from cases/hard/)
        from . import profiles as _profiles
        for d in out:
            hp = _profiles.hard_part(prof, d["key"])
            if hp:
                d["parts"] = list(d["parts"]) + [hp]
    out += custom_scores(prof)
    _relabel45(prof, out)
    if (prof or {}).get("_public"):
        out = [d for d in out if d.get("public", True)]
        # 4.5 PUBOUT: a public board names its parts in plain words; the profile's working label
        # (concept codes, "(checked)", a dangling "(" from an unquoted YAML flow value) stays in
        # ``label_internal`` and the part's id stays in ``key``. Display only: no number moves.
        # 4.1 (no chains) is frozen: its public labels stay as they were
        for d in out if prof.get("chains") or prof.get("public_labels") else []:
            d["parts"] = [_public_part(p) for p in d.get("parts") or []]
    return out


#: R5 (fix round 2026-11-18, REVIEW finding 10, D7): what the 4.5.0 board calls a section or part, by key. Display
#: only: keys, weights and numbers are the profile's; the profile's own label stays in ``label_profile``. Parts: (label,
#: short name for the points line).
LABELS_45 = {
    "work": "Coding (small tasks)",
    "chat": "Role-play and memory",
    "work/c1_implied": ("Careful small functions", "Functions"),
    "work/c5_tools": ("Follows tool rules", "Tool rules"),
    "work/c2_trace": ("Steps through code", "Steps"),
    "work/c2_predict": ("Knows language traps", "Traps"),
}


def _relabel45(prof: dict | None, defs: list[dict]) -> None:
    """Apply LABELS_45 in place on a 4.5.x board's defs (a ``chains`` profile whose release seed is 4.5.*); every
    other profile (4.1 is frozen) keeps the labels it declares."""
    if not ((prof or {}).get("chains") and str((prof or {}).get("release_seed") or "").startswith("4.5")):
        return
    for d in defs:
        lab = LABELS_45.get(d["key"])
        if isinstance(lab, str) and d.get("label") != lab:
            d["label_profile"], d["label"] = d["label"], lab
        parts = []
        for p in d.get("parts") or []:
            new = LABELS_45.get(f"{d['key']}/{p['key']}")
            if new:
                p = {**p, "label_profile": p["label"], "label": new[0], "short": new[1]}
            parts.append(p)
        d["parts"] = parts


_PAREN_TAIL = re.compile(r"\s*\([^()]*\)\s*$")


def public_label(label) -> str:
    """The plain public name of a part: every trailing parenthetical dropped ("Builds what was
    meant (C1)" -> "Builds what was meant", "Sounds like a real person (judged, provisional)" ->
    "Sounds like a real person", "Lockout (intensity rungs)" -> "Lockout"), and an unbalanced
    "(" with whatever follows it cut at the last whole word before it ("Range (role-play" ->
    "Range"). What a part is (checked, judged, provisional) is said by the page's explainers,
    not by a tag in its name."""
    s = " ".join(str(label or "").split())
    if s.count("(") > s.count(")"):
        s = s[:s.rfind("(")].rstrip(" ,;:-")
    prev = None
    while prev != s:
        prev, s = s, _PAREN_TAIL.sub("", s).rstrip(" ,;:-")
    return s or " ".join(str(label or "").split())


def _public_part(p: dict) -> dict:
    """A part as a public board names it: plain ``label``, the working label in ``label_internal``,
    the stripped tags in ``tag`` (unless the profile declares one), ``id``/``note`` as declared."""
    lab = public_label(p["label"])
    out = {**p, "label": lab, "label_internal": p["label"]}
    if not p.get("tag") and lab != " ".join(str(p["label"] or "").split()):
        out["tag"] = " ".join(str(p["label"]).split())[len(lab):].strip(" ,;:-") or None
    return out


def _parts(spec: dict) -> list[dict]:
    parts = []
    for pk, ps in (spec.get("parts") or {}).items():
        ps = ps or {}
        sc = ps.get("scoring") or {}
        label = ps.get("label", pk.replace("_", " ").title())
        parts.append({"key": pk, "label": label, "short": ps.get("short", label),
                      # points: the sub-score's maximum (its weight in the category)
                      "weight": float(ps.get("max", ps.get("weight", 1.0))),
                      "cases": list(ps.get("cases") or []) or None,
                      # a case part's own ran-out rule (report.case_part_ran_out); emitted only when
                      # set, so 4.1 parts stay byte-identical (lead 2026-11-15: it was dropped here)
                      **({"ran_out": ps["ran_out"]} if ps.get("ran_out") is not None else {}),
                      # a tool-use part's unit score (report.part_unit_score); only when set
                      **({"unit_score": ps["unit_score"]} if ps.get("unit_score") else {}),
                      # a list = weigh the components by case count (report)
                      "auto": isinstance(sc, list),
                      # harness v2: "wc" scores the part (and its packs) as
                      # willingness x capability (report.wc_block); default:
                      # the components' own formula, W/C reported beside it
                      "scoring_mode": str(ps.get("scoring_mode") or "standard"),
                      "scoring": ({k: None for k in sc} if isinstance(sc, list)
                                  else {k: float(v) for k, v in sc.items()}),
                      # 4.5: a max-0 Frontier part unlocks its packs off this Core score's
                      # result; emitted only when set (the 4.1 report never carries the key)
                      **({"unlock_part": ps["unlock_part"]} if ps.get("unlock_part") else {}),
                      # PUBOUT 4.5 (display only, emitted only when the profile sets them): the part's design
                      # id ("C1"), its internal tag ("(C1)", "(judged)"), and a note the board prints under
                      # the part ("scored by prefix: credit up to the first wrong line")
                      **{k: ps[k] for k in ("id", "tag", "note") if ps.get(k)}})
    return parts


def _score_def(key: str, spec: dict, custom: bool = False) -> dict:
    inc = spec.get("include_in")
    if inc is None:
        in_total = bool(spec.get("in_total", True)) and not custom
        inc = "overall" if in_total else "none"
    in_total = inc == "overall"
    return {"key": key, "group": spec.get("group", key),
            "label": spec.get("label", key.replace("_", " ").title()),
            "in_total": in_total, "include_in": inc,
            "public": bool(spec.get("public", not custom)), "custom": custom,
            "cases": list(spec.get("cases") or []) or None,
            "parts": _parts(spec)}


def custom_scores(prof: dict | None) -> list[dict]:
    """User tests (docs/CUSTOM-TESTS.md): the profile's — usually the private
    overlay's — ``custom:`` declarations, each a category of its own::

        custom:
          my_score:
            label: My Private Score
            include_in: none        # overall | none | <a category key, e.g. nsfw>
            public: false           # default false: scrubbed from --public
            parts:
              main: {label: Main, max: 10, scoring: [my-private-category]}

    A PRIVATE category (the profile's ``private:`` list) that no custom
    declaration names becomes one automatically (one sub-score worth 10,
    ``include_in: none``, private) — it never feeds a built-in category,
    whatever ``scoring:`` group an older overlay put its weight in: a private
    score is kept completely separate from the public ones (2026-10-28)."""
    prof = prof or {}
    out = [_score_def(k, v or {}, custom=True) for k, v in (prof.get("custom") or {}).items()]
    named = {c for d in out for p in d["parts"] for c in p["scoring"]}
    named |= {c for d in out for p in d["parts"] for c in (p["cases"] or [])}
    labels = prof.get("labels") or {}
    for cat in sorted(prof.get("private") or []):
        ids = (prof.get("cases") or {}).get(cat)
        if not ids or cat in named:
            continue
        lab = labels.get(cat, cat)
        out.append(_score_def(cat, {"label": lab, "include_in": "none", "public": False,
                                    "parts": {"main": {"label": lab, "max": 10,
                                                       "scoring": [cat]}}}, custom=True))
    return out


def score_keys(prof: dict | None) -> list[str]:
    return [x["key"] for x in scores(prof)]


def group_of(prof: dict | None, key: str) -> str:
    for x in scores(prof):
        if x["key"] == key:
            return x["group"]
    return GROUP.get(key, key)


def total_keys(prof: dict | None) -> list[str]:
    """The scores the Overall is the mean of (``in_total``)."""
    return [x["key"] for x in scores(prof) if x["in_total"]]
TIERS = (2, 3)
TIER_WEIGHT = 0.5
#: components derived from another category's rows (report.component_values)
_DERIVED = {"nsfw": ("explicit_peak", "willing", "nsfw_gated")}


def block(prof: dict | None) -> dict:
    return (prof or {}).get("tiers") or {}


def enabled(prof: dict | None) -> bool:
    return bool(block(prof))


def gate(prof: dict, tier: int, score: str) -> float | None:
    g = ((block(prof).get("gates") or {}).get(f"t{tier}") or {}).get(score)
    return None if g is None else float(g)


def tier_lists(prof: dict, tier: int) -> dict[str, dict[str, list[str]]]:
    """{score: {category: [case ids]}} of tier 2 or 3 (empty scores dropped)."""
    t = block(prof).get(f"t{tier}") or {}
    return {s: {cat: list(ids) for cat, ids in (t.get(s) or {}).items() if ids}
            for s in score_keys(prof) if any((t.get(s) or {}).values())}


def tier_context(prof: dict, tier: int) -> int | None:
    v = (block(prof).get(f"t{tier}") or {}).get("context")
    return int(v) if v else None


def tier_repeats(prof: dict, tier: int) -> int:
    return int((block(prof).get("repeats") or {}).get(f"t{tier}", 1))


def case_tiers(prof: dict) -> dict[str, tuple[int, str | None]]:
    """case id -> (tier, score). Tier-1 cases carry score None (their rows
    feed every T1 score through the scorecard groups)."""
    out: dict[str, tuple[int, str | None]] = {}
    for ids in (prof.get("cases") or {}).values():
        for cid in ids or []:
            out[cid] = (1, None)
    for tier in TIERS:
        for score, cats in tier_lists(prof, tier).items():
            for ids in cats.values():
                for cid in ids:
                    out[cid] = (tier, score)
    return out


def tier_of(prof: dict, case_id: str | None) -> int:
    return case_tiers(prof).get(case_id or "", (1, None))[0]


def weight_keys(categories) -> set[str]:
    """The scorecard component keys a set of categories can produce."""
    keys = set()
    for c in categories:
        keys.add("reasoning" if c in ("reasoning", "math") else c)
        keys.update(_DERIVED.get(c, ()))
    return keys


def tier_scoring(sc: dict, prof: dict, tier: int) -> dict:
    """The scorecard weights for one tier: every group limited to the
    components that tier's categories produce (so T1 does not list the T3
    long-tier components as "missing", and T2 Code is weighted over what T2
    Code runs). A tier category with no weight of its own counts 10."""
    if tier == 1:
        keys = weight_keys((prof.get("cases") or {}).keys())
        return {g: {k: w for k, w in ws.items() if k in keys} for g, ws in sc.items()}
    lists = tier_lists(prof, tier)
    out = {g: {} for g in sc}
    for score, cats in lists.items():
        g = group_of(prof, score)
        ws = sc.get(g) or {}
        out[g] = {k: ws.get(k, 10.0) for k in sorted(weight_keys(cats))}
    return out


def unlocked(prof: dict, tier: int, prev: dict[str, float | None]) -> list[str]:
    """The scores whose gate into ``tier`` is passed by ``prev`` (that
    score's result on the tier below, 0-100) and that have items there."""
    out = []
    for s in tier_lists(prof, tier):
        g, v = gate(prof, tier, s), prev.get(s)
        if g is not None and v is not None and v >= g:
            out.append(s)
    return out


def total(t1: float | None, t2: float | None, t3: float | None) -> float | None:
    """T1 + 0.5*T2 + 0.5*T3 (max 200); a missing tier counts 0."""
    if t1 is None:
        return None
    return t1 + TIER_WEIGHT * (t2 or 0.0) + TIER_WEIGHT * (t3 or 0.0)


def _rows_state(rows: list[dict]) -> str:
    """'none' / 'na_ctx' (every row skipped for context) / 'rows'."""
    if not rows:
        return "none"
    if all(r.get("grade") == "skipped" for r in rows):
        return "na_ctx"
    return "rows"


def row_states(prof: dict, rows_by_tier: dict[int, list[dict]]) -> dict[int, dict[str, str]]:
    """{tier: {score: none|na_ctx|rows}} from the board rows of each tier."""
    out = {}
    for t in TIERS:
        out[t] = {}
        for score, cats in tier_lists(prof, t).items():
            ids = {cid for ids_ in cats.values() for cid in ids_}
            out[t][score] = _rows_state([r for r in rows_by_tier.get(t, [])
                                         if r.get("case_id") in ids])
    return out


def combine(prof: dict, t1: dict, cards: dict[int, dict],
            states: dict[int, dict[str, str]]) -> dict:
    """Per score: ``{t1, t2, t3, total, reached, state: {2: ..., 3: ...}}``.

    ``t1`` = the T1 scorecard; ``cards`` = {tier: scorecard over that tier's
    rows}; ``states`` = :func:`row_states` (for the n/a (ctx) and pending
    states). States per tier: ``locked`` (gate not passed, 0),
    ``na_ctx`` (context cannot serve it, 0), ``pending`` (unlocked, not run or
    not judged yet, 0 for now), ``scored``, ``none`` (no items for this score)."""
    out = {}
    for s in score_keys(prof):
        g = group_of(prof, s)
        v1 = t1.get(g)
        vals = {1: v1}
        state: dict[int, str] = {}
        prev_ok = v1 is not None
        for tier in TIERS:
            lists = tier_lists(prof, tier)
            if s not in lists:
                state[tier] = "none"
                vals[tier] = None
                prev_ok = False
                continue
            gv = gate(prof, tier, s)
            prev = vals[tier - 1]
            if not prev_ok or gv is None or prev is None or prev < gv:
                state[tier] = "locked"
                vals[tier] = 0.0
                prev_ok = False
                continue
            st = (states.get(tier) or {}).get(s, "none")
            if st == "na_ctx":
                state[tier], vals[tier], prev_ok = "na_ctx", 0.0, False
                continue
            v = (cards.get(tier) or {}).get(g) if st == "rows" else None
            if v is None:
                state[tier], vals[tier], prev_ok = "pending", 0.0, False
                continue
            state[tier], vals[tier] = "scored", v
        reached = max([1] + [t for t in TIERS if state.get(t) == "scored"])
        out[s] = {"t1": v1, "t2": vals[2], "t3": vals[3], "state": state,
                  "reached": reached, "total": total(v1, vals[2], vals[3])}
    return out


def label(entry: dict | None) -> str:
    """"112 (T2)", "96 (T1, T2 pending)", "n/a (ctx)" annotations for a cell."""
    if not entry or entry.get("total") is None:
        return "-"
    txt = f"{entry['total']:.1f} (T{entry['reached']})"
    notes = []
    for tier in TIERS:
        st = (entry.get("state") or {}).get(tier)
        if st == "na_ctx":
            notes.append(f"T{tier} n/a (ctx)")
        elif st == "pending":
            notes.append(f"T{tier} pending")
    return txt + (f" [{'; '.join(notes)}]" if notes else "")


def overall(tier_scores: dict, prof: dict | None = None) -> float | None:
    """Overall = the mean of the present score totals (each 0-200) of the
    scores that count in the total (a profile's ``in_total: false`` score,
    NSFW, is shown beside it and never summed)."""
    keys = set(total_keys(prof)) if prof is not None else set(tier_scores)
    vals = [e["total"] for k, e in tier_scores.items()
            if k in keys and e.get("total") is not None]
    return sum(vals) / len(vals) if vals else None
