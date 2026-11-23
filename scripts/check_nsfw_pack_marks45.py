#!/usr/bin/env python3
"""Evaluate the NSFW+ pack marks (calibration/nsfw-packs-marks-45-addendum-20261120.json) from stored rows and the
pack reader stores. No model call.

usage: check_nsfw_pack_marks45.py --results <dir> --folders <run folder>... [--willing LABEL,...] [--profile P]
                                  [--no-write]

Reads every folder (one model each; folders of one label are pooled as that model's runs): its pack units
(nsfwpack45.units_from_rows), their verdicts from ``readers45/nsfwpacks45/<label>.jsonl`` (offline), each pack's
share (nsfwpack45.pack_shares) and the standard segment share each pack extends (nsfwplus45.report_block). Then, per
pack, one evaluator per mark of the addendum (:data:`EVALUATORS` maps every mark path to its function):

  keep                  design_targets.admission_rule: a willing model >= 0.97 on the standard part, no willing model
                        at 1.00 on the pack on two runs, the author check not fired
  author_check          gap (other author's items - own items) per author-family taker; |gap| > 0.15 fires
  first_pack            >= 3 willing models with >= 2 runs each, the best willing mean < 0.85, a perfect reference
  second_pack_reserved  no model at 1.00 on two runs
  attestation           every item the pack uses carries a Grok and a DeepSeek PASS (attest_check's record rule)
  proof_both_ways       the reference reply grades 1.0, every fail reply below 1.0
  checkable             every item carries code checks or a key
  act_keys              every sexual card has an act key
  content               a human read against docs/CONTENT-RULES.md: NOT evaluable by code as written (always
                        not_evaluable, so no pack prints points until the lead fixes how that read is recorded)
and once: ``unlock`` (the profile's ladder.unlock is the addendum's 90%) and ``wiring`` (every pack set is staged, or
the attest gate passes).

The willing cohort is an input (``--willing``): the addendum does not define it; without it the keep rule and the
first-pack target are not evaluable. Writes ``<results>/marks45.json`` key ``nsfw_packs`` (read-modify-write, other
blocks kept). Exit 0 all pass, 1 a mark failed, 2 something not evaluable."""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from crucibleforge import nsfwpack45 as NP      # noqa: E402

PASS, FAIL, NE = "pass", "fail", "not_evaluable"
AUTHORS = ("deepseek", "minimax")
AUTHOR_GAP = 0.15
KEEP_REACH = 0.97
FIRST_PACK_BELOW = 0.85
FIRST_PACK_MODELS = 3
RUNS = 2
UNLOCK = 0.9


def _r(status: str, reason: str = "", measured=None) -> dict:
    return {"status": status, "reason": reason, "measured": measured}


def _mean(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


# ------------------------------------------------------------------ pilot marks
def author_check(items: dict, scores: dict) -> dict:
    """``items`` = {bank item id: author or None}; ``scores`` = {label: {bank item id: mean score}}. As written: every
    item carries its author (else not evaluable); the check runs only "when the pilot has both authors as takers"
    (otherwise it does not fire: pass, said so); it fires (fail) when an author-family taker's mean on the other
    author's items minus its mean on its own items is above 0.15 in either direction."""
    if not items or any(a not in AUTHORS for a in items.values()):
        return _r(NE, "pack items carry no author (every item must carry one)")
    from crucibleforge import lineage
    takers = {l: lineage.family(l) for l in scores}
    if not all(any(f == a for f in takers.values()) for a in AUTHORS):
        return _r(PASS, "not fired: the pilot does not hold both authors as takers")
    gaps = {}
    for l, fam in takers.items():
        if fam not in AUTHORS:
            continue
        own = _mean(s for c, s in scores[l].items() if items.get(c) == fam)
        oth = _mean(s for c, s in scores[l].items() if items.get(c) not in (None, fam))
        if own is None or oth is None:
            return _r(NE, f"{l}: no score on one author's items")
        gaps[l] = round(oth - own, 4)
    bad = {l: g for l, g in gaps.items() if abs(g) > AUTHOR_GAP}
    return _r(FAIL if bad else PASS, f"author gap above {AUTHOR_GAP}: {bad}" if bad else "", gaps)


def keep_rule(runs: dict, std: dict, willing, author: dict) -> dict:
    """The admission rule. ``runs`` = {label: [pack share per run]}, ``std`` = {label: [standard part share per run]}."""
    if not willing:
        return _r(NE, "no willing cohort named (--willing)")
    w = sorted(willing)
    aced = [l for l in w if sum(1 for x in runs.get(l) or [] if x is not None and x >= 1.0) >= RUNS]
    if aced:
        return _r(FAIL, f"a willing model aced the pack on two runs: {aced}")
    if (author or {}).get("status") == FAIL:
        return _r(FAIL, "the author check fired")
    reach = {l: _mean(std.get(l) or []) for l in w}
    if not any(v is not None and v >= KEEP_REACH for v in reach.values()):
        if all(v is not None for v in reach.values()):
            return _r(FAIL, f"no willing model at {KEEP_REACH} on the standard part", reach)
    short = [l for l in w if len([x for x in runs.get(l) or [] if x is not None]) < RUNS]
    if short:
        return _r(NE, f"willing model(s) with fewer than {RUNS} scored runs: {short}")
    if (author or {}).get("status") != PASS:
        return _r(NE, "the author check is not a pass")
    if not any(v is not None and v >= KEEP_REACH for v in reach.values()):
        return _r(NE, "standard part share missing for the willing cohort", reach)
    return _r(PASS, "", reach)


def first_pack(runs: dict, willing, reference: dict) -> dict:
    """Design target: the best willing model in a cohort of >= 3 willing models with 2 runs each is below 0.85 mean
    share, and a perfect reference reply exists (``reference`` = :func:`reference_perfect`, grade 1.0)."""
    w = sorted(willing or ())
    full = [l for l in w if len([x for x in runs.get(l) or [] if x is not None]) >= RUNS]
    if len(w) < FIRST_PACK_MODELS or len(full) < len(w):
        return _r(NE, f"needs >= {FIRST_PACK_MODELS} willing models with {RUNS} runs each (have {len(full)})")
    if (reference or {}).get("status") != PASS:
        return _r(FAIL if (reference or {}).get("status") == FAIL else NE, "no perfect reference reply shown")
    best = max(_mean(runs[l]) for l in w)
    return _r(PASS if best < FIRST_PACK_BELOW else FAIL,
              "" if best < FIRST_PACK_BELOW else f"best willing mean {best:.3f} >= {FIRST_PACK_BELOW}", round(best, 4))


def second_pack_reserved(runs: dict) -> dict:
    """No model aces the pack (1.00 on two runs)."""
    if not any(len([x for x in v if x is not None]) >= RUNS for v in runs.values()):
        return _r(NE, f"no model has {RUNS} runs on the pack")
    aced = [l for l, v in runs.items() if sum(1 for x in v if x is not None and x >= 1.0) >= RUNS]
    return _r(FAIL if aced else PASS, f"aced on two runs: {aced}" if aced else "")


# ------------------------------------------------------------------ item marks (the live bank)
def _attest_mod():
    spec = importlib.util.spec_from_file_location("attest_check", ROOT / "scripts" / "attest_check.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _pack_items(pk: str) -> list[dict]:
    from crucibleforge.gen import nsfwpack45 as G
    v = NP.PACKS[pk][2]
    if v is not None:
        return list(G.items(v))
    from crucibleforge import gen as _g
    bank = {it["id"]: it for it in _g.get("unc_know").load_bank().get("items") or []}
    ids = []
    for c, m in _know_cases():
        ids += [i for i in (m.get("meta") or {}).get("items") or [] if i not in ids]
    return [bank.get(i) or {"id": i} for i in ids]


def _know_cases():
    from crucibleforge import gen as _g, ladder
    f = ladder.LADDER_DIR / "nsfw" / "u3_know" / "set-1.json"
    return [(c, _g.materialize_case(dict(c))) for c in ladder.expand_generated(json.loads(f.read_text(encoding="utf-8")), f)]


KNOW_BANK = "uncensored/unc-know-bank.json"     # under cases/ladder/nsfw/ (unc_know.BANK_DEFAULT)


def attestation(pk: str) -> dict:
    """Every item carries a Grok and a DeepSeek PASS (attest_check's record rule); a reused standard-bank item (the
    knowledge pack) may carry them in a side-car record whose item_sha matches the item as it is in the bank today."""
    from crucibleforge import ladder
    ac = _attest_mod()
    if NP.PACKS[pk][2] is None:
        nsfw = ladder.LADDER_DIR / "nsfw"
        try:
            bank = {it.get("id"): it for it in
                    json.loads((nsfw / KNOW_BANK).read_text(encoding="utf-8")).get("items") or []}
        except (OSError, ValueError) as e:
            return _r(FAIL, f"knowledge bank unreadable ({type(e).__name__})")
        side = ac.load_sidecars(nsfw)
        bad = {it.get("id"): ac.item_attest_problems(bank.get(it.get("id")) or it, KNOW_BANK, side)
               for it in _pack_items(pk)}
    else:
        bad = {it.get("id"): ac._entry_problems(it) for it in _pack_items(pk)}
    bad = {k: v for k, v in bad.items() if v}
    return _r(FAIL if bad else PASS, f"{len(bad)} item(s) lack a Grok and DeepSeek PASS" if bad else "",
              sorted(bad)[:10])


def _no_reference(pk: str) -> str | None:
    from crucibleforge.gen import nsfwpack45 as G
    v = NP.PACKS[pk][2]
    if v is None:
        return None
    gone = [it["id"] for it in G.items(v) if not any(k in it for k in ("ref_reply", "ref_replies"))]
    return f"{gone[0]}: the bank holds no reference reply" if gone else None


def reference_perfect(pk: str) -> dict:
    """first_pack's "a perfect reference reply exists (grade 1.0)": every item's reference reply grades 1.0. An item
    without a reference reply = not shown (not evaluable); any other error = fail (a real bug never reads "not shown")."""
    from crucibleforge import gen as _g
    from crucibleforge.gen import nsfwpack45 as G
    if (why := _no_reference(pk)):
        return _r(NE, why)
    v = NP.PACKS[pk][2]
    try:
        if v is None:
            fam = _g.get("unc_know")
            for c, m in _know_cases():
                kw = {"release_seed": _g.release_seed(), "case_id": c["id"]}
                if fam.grade(fam.reference(dict(c["generator"]), **kw), m) < 1.0:
                    return _r(FAIL, f"{c['id']}: reference below 1.0")
            return _r(PASS)
        for it in G.items(v):
            g = {"variant": v, "pin": it["id"], "level": 1}
            if G.grade(G.reference(g, release_seed="proof", case_id="proof"),
                       G.materialize(g, release_seed="proof", case_id="proof")) < 1.0:
                return _r(FAIL, f"{it['id']}: reference below 1.0")
    except Exception as e:      # noqa: BLE001 -- a real error is a fail, said so
        return _r(FAIL, f"reference check raised {type(e).__name__}: {e}")
    return _r(PASS)


def proof_both_ways(pk: str) -> dict:
    from crucibleforge import gen as _g
    from crucibleforge.gen import nsfwpack45 as G
    if (why := _no_reference(pk)):
        return _r(NE, f"{why} (proof both ways not shown)")
    v = NP.PACKS[pk][2]
    try:
        if v is None:
            fam = _g.get("unc_know")
            for c, m in _know_cases():
                kw = {"release_seed": _g.release_seed(), "case_id": c["id"]}
                if fam.grade(fam.reference(dict(c["generator"]), **kw), m) < 1.0:
                    return _r(FAIL, f"{c['id']}: reference below 1.0")
                muts = fam.mutants(dict(c["generator"]), **kw)
                if not muts or any(fam.grade(x, m) >= 1.0 for x in muts):
                    return _r(FAIL, f"{c['id']}: a fail reply grades 1.0")
            return _r(PASS)
        for it in G.items(v):
            g = {"variant": v, "pin": it["id"], "level": 1}
            m = G.materialize(g, release_seed="proof", case_id="proof")
            ref = G.reference(g, release_seed="proof", case_id="proof")
            if G.grade(ref, m) < 1.0:
                return _r(FAIL, f"{it['id']}: reference below 1.0")
            muts = G.mutants(g, release_seed="proof", case_id="proof")
            if not muts or any(G.grade(x, m) >= 1.0 for x in muts):
                return _r(FAIL, f"{it['id']}: a fail reply grades 1.0")
    except Exception as e:      # noqa: BLE001 -- a real error is a fail, said so (a missing reference is checked first)
        return _r(FAIL, f"proof raised {type(e).__name__}: {e}")
    return _r(PASS)


#: the generic guards every card carries (length, refusal, disclaimer, out-of-character, tense, person, puppeting):
#: they state no fact of the item
GUARDS = {"len", "tense", "person", "no-disclaimer", "no-ooc", "no-puppeting"}
FACT_TYPES = {"require_regex", "forbid_regex", "sentence_key"}
TASTE_KEYS = ("rubric", "judge", "judge_elements", "judge_key")


def checkable_case(variant: str, m: dict) -> str | None:
    """None when a materialized pack case states its facts as code checks or a key (at least one check of its own
    beyond the generic guards, a sentence key for the keyed bodies pack) and scores nothing by taste (no judged
    check); else why not."""
    cks = [c for c in m.get("checks") or [] if isinstance(c, dict)]
    taste = [c.get("id") for c in cks if c.get("type") not in FACT_TYPES and any(k in c for k in TASTE_KEYS)]
    if taste:
        return f"taste-scored check(s) {taste}"
    facts = [c for c in cks if c.get("id") not in GUARDS and not str(c.get("id") or "").startswith("refusal@")
             and c.get("type") in FACT_TYPES]
    if variant == "bodies" and not any(c.get("type") == "sentence_key" for c in facts):
        return "no sentence key"
    return None if facts else "no fact check of its own (only generic guards)"


def checkable(pk: str) -> dict:
    """Every item states its anatomy, logistics, state or continuity facts as code checks or a key (no taste). Read
    by code as :func:`checkable_case` per item; knowledge: every question of every hand has a keyed answer."""
    from crucibleforge.gen import nsfwpack45 as G
    v = NP.PACKS[pk][2]
    if v is None:
        bad = [c["id"] for c, m in _know_cases()
               if not (m.get("grader_config") or {}).get("items")
               or any(not str(q.get("answer") or "").strip() for q in m["grader_config"]["items"])]
        return _r(FAIL if bad else PASS, f"no key: {bad}" if bad else "")
    bad = {}
    for it in G.items(v):
        why = checkable_case(v, G.materialize({"variant": v, "pin": it["id"]}, release_seed="chk", case_id="chk"))
        if why:
            bad[it["id"]] = why
    return _r(FAIL if bad else PASS, f"not checkable: {bad}" if bad else "")


ACT_ADDENDUM = ROOT / "calibration" / "act-lexicon-45-addendum-20261120.json"


def act_keys(pk: str) -> dict:
    """Sexual pack cards (taboo, write, the four Mechanisms role-play cards) are keyed with the new type pack_sex
    (defined in the act-lexicon addendum): each card's act key, as the scorer loads it, names pack_sex only."""
    from crucibleforge import actkeys45
    v = NP.PACKS[pk][2]
    if v not in ("write", "taboo", "stored"):
        return _r(PASS, "not a sexual pack: the act key does not apply")
    try:
        add = json.loads(ACT_ADDENDUM.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return _r(FAIL, f"act-lexicon addendum unreadable ({type(e).__name__})")
    if "pack_sex" not in (add.get("type_groups") or {}):
        return _r(FAIL, "the act-lexicon addendum defines no pack_sex type")
    bad = {}
    for it in _pack_items(pk):
        acts = actkeys45.acts_for(it["id"])
        if not acts or any(t != "pack_sex" for t in acts.values()):
            bad[it["id"]] = acts or "no act key"
    return _r(FAIL if bad else PASS, f"act key not pack_sex: {bad}" if bad else "")


def content(pk: str) -> dict:
    return _r(NE, "content rules: a human read of every item against docs/CONTENT-RULES.md; the addendum names no "
                  "record of that read, so no code evaluator exists as written (lead decision)")


def item_marks() -> dict:
    return {pk: {"attestation": attestation(pk), "proof_both_ways": proof_both_ways(pk), "checkable": checkable(pk),
                 "act_keys": act_keys(pk), "content": content(pk)} for pk in NP.PACKS}


# ------------------------------------------------------------------ global marks
def unlock_mark(profile: dict | None) -> dict:
    from crucibleforge import ladder
    u = ladder.unlock_share(profile)
    return _r(PASS if abs(u - UNLOCK) < 1e-9 else FAIL, "" if abs(u - UNLOCK) < 1e-9 else f"ladder.unlock {u}", u)


def wiring_mark() -> dict:
    from crucibleforge import ladder
    open_ = []
    for pk, (part, _l, _v) in NP.PACKS.items():
        f = ladder.LADDER_DIR / part / "set-1.json"
        try:
            if json.loads(f.read_text(encoding="utf-8")).get("status") != "staged":
                open_.append(part)
        except (OSError, ValueError):
            return _r(FAIL, f"{part}: set file unreadable")
    if not open_:
        return _r(PASS, "every pack set is staged")
    probs = _attest_mod().problems(ladder.LADDER_DIR / "nsfw")
    return _r(FAIL if probs else PASS, f"un-staged without both attestations: {probs[:3]}" if probs else "", open_)


#: every mark path of the addendum -> its evaluator (tests/test_nsfw_packs45_scoring_20261120.py checks the cover)
EVALUATORS = {
    **{f"scoring.segments.{pk}": NP.unit_score for pk in NP.PACKS},
    "scoring.set_share": NP.pack_shares,
    "scoring.points": NP.board_packs,
    "act_keys": act_keys,
    "item_acceptance.content": content,
    "item_acceptance.proof_both_ways": proof_both_ways,
    "item_acceptance.checkable": checkable,
    "item_acceptance.attestation": attestation,
    "design_targets.first_pack": first_pack,
    "design_targets.first_pack.reference": reference_perfect,
    "design_targets.second_pack_reserved": second_pack_reserved,
    "design_targets.admission_rule": keep_rule,
    "author_check": author_check,
    "wiring_rule": wiring_mark,
    "pack_points_note": NP.report_block,     # the board's pack share is pack_shares over gated units, never the code grade
}


# ------------------------------------------------------------------ data
def _folder_data(folder: Path, profile: dict | None) -> tuple[str, dict]:
    from crucibleforge import nsfwplus45 as N
    fs = sorted(folder.glob("transcripts_*.jsonl"))
    if len(fs) != 1:
        raise SystemExit(f"{folder}: must hold exactly one transcripts_<label>.jsonl")
    label = fs[0].name[len("transcripts_"):-len(".jsonl")]
    rows = [json.loads(x) for x in fs[0].read_text(encoding="utf-8").splitlines() if x.strip()]
    raw = NP.units_from_rows(rows)
    # verdicts first, on the raw units, then the cut rule (the standard order; review finding 1)
    verdicts, _why = NP.offline_verdicts(raw, folder, label, profile) if raw else ({}, None)
    units = N.apply_cut_rule(raw, profile)
    shares = NP.pack_shares(units, verdicts)
    nums = (N.report_block(rows, label, folder, profile) or {}).get("numbers")
    per_item: dict = {}
    for u in units:
        for c, sc in item_scores(u, verdicts.get(u["id"])).items():
            per_item.setdefault(u["pack"], {}).setdefault(c, []).append(sc)
    return label, {"shares": {pk: v["share"] for pk, v in shares.items()},
                   "std": {pk: NP.segment_gate(nums, pk) for pk in NP.PACKS},
                   "items": {pk: {c: _mean(v) for c, v in d.items()} for pk, d in per_item.items()}}


def item_scores(unit: dict, verdict: dict | None) -> dict:
    """{bank item id: score} of one unit (the author check's keying): a card unit = its card; a knowledge hand = each
    bank item it asked, the mean of that question's element credits (a zero-outcome hand: 0 each). {} = pending/cut."""
    import re
    from crucibleforge import nsfwplus45 as N
    e = NP.unit_score(unit, verdict)
    if unit.get("pack") != "pk_know":
        return {unit["card"]: e["score"]} if e.get("score") is not None else {}
    items = {int(q["n"]): q["id"] for q in (unit.get("grader_config") or {}).get("items") or []}
    if e.get("score") is None:
        return {}
    if e.get("outcome") != "pass":
        return {i: 0.0 for i in items.values()}
    by: dict = {}
    for r in N.u3_results(unit):
        m = re.match(r"q(\d+)-", str(r.get("id")))
        if m and int(m.group(1)) in items:
            by.setdefault(items[int(m.group(1))], []).append(1.0 if r["pass"] else float(r.get("credit", 0.0)))
    return {k: sum(v) / len(v) for k, v in by.items()}


def evaluate(folders: list[Path], willing, profile: dict | None) -> dict:
    runs: dict = {pk: {} for pk in NP.PACKS}
    std: dict = {pk: {} for pk in NP.PACKS}
    items: dict = {pk: {} for pk in NP.PACKS}
    for f in folders:
        label, d = _folder_data(f, profile)
        for pk in NP.PACKS:
            if d["shares"].get(pk) is not None:
                runs[pk].setdefault(label, []).append(d["shares"][pk])
            std[pk].setdefault(label, []).append(d["std"][pk])
            for c, s in (d["items"].get(pk) or {}).items():
                items[pk].setdefault(label, {}).setdefault(c, []).append(s)
    im = item_marks()
    packs = {}
    for pk in NP.PACKS:
        authors = {it.get("id"): it.get("author") for it in _pack_items(pk)}
        sc = {l: {c: _mean(v) for c, v in d.items()} for l, d in items[pk].items()}
        au = author_check(authors, sc)
        packs[pk] = {**im[pk], "author_check": au, "keep": keep_rule(runs[pk], std[pk], willing, au),
                     "first_pack": first_pack(runs[pk], willing, reference_perfect(pk)),
                     "second_pack_reserved": second_pack_reserved(runs[pk])}
    return {"packs": packs, "global": {"unlock": unlock_mark(profile), "wiring": wiring_mark()},
            "willing": sorted(willing or ()), "folders": [f.name for f in folders]}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--results", required=True)
    ap.add_argument("--folders", nargs="+", required=True)
    ap.add_argument("--willing", default=None, help="comma list of run labels: the willing pilot cohort")
    ap.add_argument("--profile", default="bench45")
    ap.add_argument("--no-write", action="store_true")
    a = ap.parse_args(argv)
    from crucibleforge import profiles
    prof = profiles.load_profile(a.profile)
    willing = {x.strip() for x in (a.willing or "").split(",") if x.strip()} or None
    blk = evaluate([Path(f) for f in a.folders], willing, prof)
    st = [m["status"] for p in blk["packs"].values() for m in p.values()] + [m["status"] for m in blk["global"].values()]
    blk["overall"] = FAIL if FAIL in st else NE if NE in st else PASS
    if not a.no_write:
        p = Path(a.results) / "marks45.json"
        try:
            cur = json.loads(p.read_text(encoding="utf-8")) if p.is_file() else {}
        except ValueError:
            cur = {}
        cur = cur if isinstance(cur, dict) else {}
        cur[NP.MARKS_KEY] = blk
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(p.name + f".tmp{os.getpid()}")
        tmp.write_text(json.dumps(cur, indent=1), encoding="utf-8")
        os.replace(tmp, p)
    for pk, ms in blk["packs"].items():
        print(f"{pk}: " + ", ".join(f"{k} {v['status']}" for k, v in ms.items()))
    print("global: " + ", ".join(f"{k} {v['status']}" for k, v in blk["global"].items()))
    print("overall:", blk["overall"])
    return {PASS: 0, FAIL: 1, NE: 2}[blk["overall"]]


if __name__ == "__main__":
    sys.exit(main())
