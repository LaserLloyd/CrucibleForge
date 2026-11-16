"""patchhard - the top cell of the PATCHFIX ladder (C3 "repairs existing code", MakoBench 4.5 segment C3).

Same seeded lease-book project, SPEC, answer format, hidden differential tests and score rule as
``patchfix`` (it reuses that module and ``_patchbase``; nothing in them changes, so stored patchfix rows
keep their revision). What differs is the draw of bugs:

* every feature is in the program (quota, extend, the queued-request scheduler);
* no bug report and the count is hidden: every bug is found by auditing the code against SPEC.md;
* 12 bugs, at least 8 of them from ``HARD`` - bugs whose wrong answer only shows up LATER than the edited
  line runs (after a RESTART, at a later grant, in a later LIST): store writes that are skipped, leaked or
  coalesced, and the queue's grant / aging / ordering rules. On stored hosted rows (seg-c3-work/bugmiss.out)
  these are the bugs strong models miss (per-bug hit rate 0/2 .. 0.89), while local-rule bugs (an edge
  operator, a sort key, a split) sit at 0.95-1.0.

Every HARD bug breaks a sentence SPEC.md states (BUGS[...]["spec"] names the section); the one queue bug whose
rule is only implied (``queue_grant_journal``: a granted request's row is left in the store) is NOT in HARD.

Levels keep the patchfix numbering, so a patchhard level-8 instance is byte-identical to the instance the C3
probe ran under the name "patchfix level 8" (the instance rng is seeded with the family name "patchfix").

Score = passed f2p groups / 12, halved when any of the 4 p2p (keep) groups fails (engine ``repo_tests``).
Floors (tests/test_gen_patchhard.py): the given program 0; whole files unchanged 0; fixing only the
non-HARD bugs <= 4/12; reference 1.0.
"""
from __future__ import annotations

import hashlib
import json
import random
from pathlib import Path

from . import _patchbase as pb
from . import patchfix as pf

#: gen/_det.py (the shared deterministic launcher, 2026-11-14): its digest, so a launcher change moves this family's revision
_DET_SHA = "77fb46314ef63360"

FAMILY = "patchhard"
SYSTEM = pf.SYSTEM
MAX_TOKENS = 65536
RNG_FAMILY = "patchfix"      # instances shared with the C3 probe rows (seg-c3-work/results/*-L8-*)

LEVELS = {
    8: {"bugs": 12, "reported": 0, "style": "got", "features": ("quota", "extend", "queue"), "count": False,
        "hard": 8, "per_file": 5, "answer_tokens": 3000},
    # cycle 2: level 8 plus the IMPLIED bugs - SPEC no longer states the store rule each one breaks; the code's
    # own convention does (every other path writes the store, and only the store survives a RESTART)
    # 8 bugs, not 12: drop_keeps_row changes ~55% of random scripts, so beside 11 other bugs it is never isolated
    9: {"bugs": 8, "reported": 0, "style": "got", "features": ("quota", "extend", "queue"), "count": False,
        "hard": 5, "per_file": 5, "answer_tokens": 2500, "implied": True},   # every IMPLIED_CUTS bug
}

#: level 9: each implied bug -> the canonical (un-renamed) SPEC text removed for it. The rule it breaks is then
#: evident only from the code (every other mutation path writes or deletes the store row) and from SPEC's
#: "the store is the only thing that survives a RESTART". Judged by a blind 3-reader panel (keep at 2 of 3).
IMPLIED_CUTS = {
    # panel 2026-11-14 (3 blind Opus readers, task text only): listed by 3/3 ("only a careful one") -> kept. drop_keeps_row with SPEC's
    # store sentence cut was listed by 0/2 -> contestable, cut reverted; restore_unwritten triggers on 1-4 of 900
    # random scripts -> not isolatable beside 7 other bugs.
    "queue_grant_journal": ("", ""),      # never stated in SPEC: a granted request's row stays in the store
}
#: SPEC's "silent = the code is right" line contradicts an implied bug; level 9 says it neutrally
SILENT_LINE = ("Where this document is silent, the current code's behaviour is correct and must not change.",
               "Behaviour that is not part of a bug must not change.")

#: deferred-effect bugs (see the docstring); each names its SPEC section in _patchbase.BUGS
HARD = ("queue_grant_no_reclaim", "queue_aging_dir", "queue_status_fifo", "queue_not_on_wait",
        "queue_restore_lost", "queue_overtakes", "queue_no_aging", "coalesce_leak", "drop_keeps_row",
        "restore_unwritten", "reacquire_written", "quota_zero_kept", "extend_vacating", "extend_coalesced")

#: chains.revision() digests only THIS file; these pins make a change in patchfix.py, _patchbase.py or
#: _patchsrc/ change this file too (tests/test_gen_patchhard.py::test_pins fails until they are updated)
_PF_SHA = "b416a9fe6aa2e473"
_BASE_SHA = "1b5b8eb4ec832671"


def source_pins() -> tuple[str, str]:
    g = Path(__file__).resolve().parent
    a = hashlib.sha256((g / "patchfix.py").read_bytes()).hexdigest()[:16]
    h = hashlib.sha256()
    for p in [g / "_patchbase.py"] + sorted((g / "_patchsrc").iterdir()):
        h.update(p.name.encode())
        h.update(p.read_bytes())
    return a, h.hexdigest()[:16]


_CACHE: dict = {}


def _build(gen: dict, release_seed: str, case_id: str) -> dict:
    level = int(gen.get("level", 8))
    key = str(gen.get("key", "core"))
    ck = (FAMILY, level, key, release_seed, case_id)
    if ck in _CACHE:
        return _CACHE[ck]
    if level not in LEVELS:
        raise ValueError(f"patchhard: no level {level}")
    L = LEVELS[level]
    for attempt in range(40):
        rng = pb.instance_rng(RNG_FAMILY, release_seed, case_id, level, key, attempt)
        knobs = pb.sample_knobs(rng)
        rn = pb.sample_rename(rng)
        out = _try_instance(rng, knobs, rn, L, L["features"])
        if out is not None:
            out["meta"]["attempt"] = attempt
            out["meta"]["family"] = FAMILY
            out["meta"]["hard"] = [b for b in out["meta"]["bugs"] if b in HARD]
            out["meta"]["implied"] = [b for b in out["meta"]["bugs"] if L.get("implied") and b in IMPLIED_CUTS]
            _CACHE[ck] = out
            return out
    raise RuntimeError("patchhard: no instance after 40 attempts")


def _try_instance(rng: random.Random, knobs: dict, rn: dict, L: dict, feats) -> dict | None:
    """patchfix._try_instance with the HARD-first draw (same rng call order as the probe build)."""
    clean_tree = pb.build_tree(knobs, feats, (), rn)
    clean = pb.load_runner(clean_tree, rn)
    live = [b["name"] for b in pb.BUGS if b.get("when", lambda k: True)(knobs)
            and (b.get("feature") is None or b["feature"] in feats)]
    runners = {b: pb.load_runner(pb.build_tree(knobs, feats, (b,), rn), rn) for b in live}
    pool = []
    for mix in pf.POOL_MIXES:
        pool += pf._scripts(rng, knobs, feats, pf.POOL // len(pf.POOL_MIXES), mix=mix, rn=rn)
    want = [clean(s) for s in pool]
    diff = {b: {i for i, s in enumerate(pool) if runners[b](s) != want[i]} for b in live}
    cand = [b for b in live if len(diff[b]) >= pf.MIN_ISO]
    must = [b for b in IMPLIED_CUTS if L.get("implied") and b in cand]
    if L.get("implied") and len(must) < len(IMPLIED_CUTS):
        return None

    def iso_of(b, among):
        return [i for i in sorted(diff[b]) if not any(i in diff[o] for o in among if o != b)]
    chosen, iso, base = [], {}, None
    for _shuffle in range(40):
        rng.shuffle(cand)
        cand = [b for b in cand if b in HARD] + [b for b in cand if b not in HARD]
        if must:
            cand = must + [b for b in cand if b not in must]
        chosen, per_file = [], {}
        for b in cand:
            f = pb.BUG_BY_NAME[b]["file"]
            if per_file.get(f, 0) >= L["per_file"]:
                continue
            trial = chosen + [b]
            if all(len(iso_of(x, trial)) >= pf.MIN_ISO for x in trial):
                chosen = trial
                per_file[f] = per_file.get(f, 0) + 1
                if len(chosen) == L["bugs"]:
                    break
        if len(chosen) < L["bugs"]:
            continue
        if sum(b in HARD for b in chosen) < L["hard"] or (must and any(b not in chosen for b in must)):
            chosen = []
            continue
        base_tree = pb.build_tree(knobs, feats, tuple(chosen), rn)
        base = pb.load_runner(base_tree, rn)
        allbut = {b: pb.load_runner(pb.build_tree(knobs, feats, tuple(o for o in chosen if o != b), rn), rn)
                  for b in chosen}
        iso = {b: [i for i in iso_of(b, chosen) if base(pool[i]) != want[i] and allbut[b](pool[i]) == want[i]]
               for b in chosen}
        if all(len(v) >= pf.MIN_ISO for v in iso.values()):
            break
        chosen = []
    if len(chosen) < L["bugs"]:
        return None
    groups = {}
    order = list(chosen)
    rng.shuffle(order)
    for n, b in enumerate(order, 1):
        idx = iso[b][:pf.PER_GROUP]
        groups[f"bug-{n}"] = {"kind": "f2p", "bug": b, "cases": [[pool[i], want[i]] for i in idx]}
    for gid, mix in pf.KEEP_MIXES.items():
        got = []
        tries = 0
        while len(got) < pf.PER_GROUP and tries < 1500:
            tries += 1
            s = pb.to_rendered(pb.gen_script(rng, knobs, feats, mix=mix), rn)
            w = clean(s)
            # a keep script must be untouched by EVERY planted bug on its own, not only by the whole set: in
            # patchfix a keep script can pass on the given program because two bugs cancel, and a model that
            # fixes one of them then loses half its score for a bug it did fix (seen on 1 of 5 seeds here)
            if base(s) == w and s.count("\n") >= 10 and all(runners[b](s) == w for b in chosen):
                got.append([s, w])
        if len(got) < 4:
            return None
        groups[gid] = {"kind": "p2p", "cases": got}
    return _assemble(knobs, rn, L, feats, chosen, groups, base_tree, clean_tree)


def _cut_spec(tree: dict, rn: dict) -> dict:
    t = dict(tree)
    spec = t["SPEC.md"]
    for old, new in [v for v in IMPLIED_CUTS.values() if v[0]] + [SILENT_LINE]:
        o = pb.apply_rename(old, rn)
        if spec.count(o) != 1:
            raise ValueError(f"patchhard: SPEC cut {old[:40]!r} found {spec.count(o)} times")
        spec = spec.replace(o, pb.apply_rename(new, rn))
    t["SPEC.md"] = spec
    return t


def _assemble(knobs, rn, L, feats, chosen, groups, base_tree, clean_tree):
    pkg = rn["package"]
    if L.get("implied"):
        base_tree, clean_tree = _cut_spec(base_tree, rn), _cut_spec(clean_tree, rn)
    files_txt = []
    for path in ["SPEC.md"] + sorted(p for p in base_tree if p != "SPEC.md"):
        lang = "markdown" if path.endswith(".md") else "python"
        body = base_tree[path]
        if path.endswith("__init__.py") and not body.strip():
            continue
        files_txt.append(f"{path}\n```{lang}\n{body}```\n")
    # level 9 (panel reader 3, 2026-11-14): "a rule SPEC.md states" told a careful reader to leave an implied
    # bug alone; the implied level says "states or directly implies" (names no rule). Level 8 text unchanged.
    rule = "states or directly implies" if L.get("implied") else "states"
    prompt = (
        f"# Task\n\nThis project, `{pkg}`, is specified by SPEC.md, which is authoritative. "
        "The code has bugs; how many is not known. Each bug is one place in the code that breaks a rule "
        f"SPEC.md {rule}; fixing it is a small local edit.\n\n"
        "No user has reported a problem yet: find the bugs by checking the code against SPEC.md."
        "\n\nFix every bug in one answer. Hidden tests run many command scripts and compare every "
        "answer line with what SPEC.md says. They also check that everything that already works still "
        "works, so change nothing else: no refactoring, renaming or reformatting.\n\n"
        "## The project\n\n" + "\n".join(files_txt))
    hidden, gspec = {}, []
    for gid, g in groups.items():
        fname = f"test_{gid.replace('-', '_')}.py"
        hidden[fname] = pb.hidden_file(pkg, gid, g["cases"])
        gspec.append({"id": gid, "file": fname, "kind": g["kind"]})
    files = dict(base_tree)
    ref_files = {p: clean_tree[p] for p in clean_tree if p.startswith(pkg + "/") and clean_tree[p] != base_tree[p]}
    soft = tuple(b for b in chosen if b not in HARD)
    hard_left = pb.build_tree(knobs, feats, tuple(b for b in chosen if b in HARD), rn)
    mutants = {"soft-only": {p: hard_left[p] for p in hard_left if hard_left[p] != base_tree[p]}} if soft else {}
    meta = {"family": FAMILY, "knobs": knobs, "rename": dict(rn), "bugs": chosen,
            "group_bug": {g: v.get("bug") for g, v in groups.items()}, "reported": [],
            "features": list(feats), "answer_tokens": L["answer_tokens"],
            "prompt_tokens": pb.answer_tokens(SYSTEM + prompt),
            "program_lines": sum(t.count("\n") for p, t in base_tree.items() if p.endswith(".py"))}
    cfg = {"files": files, "hidden": hidden, "groups": gspec, "allowed_paths": [f"{pkg}/*.py"],
           "new_paths": [], "pythonpath": ["."], "answer": "edits",
           "deterministic": True}
    return {"system": SYSTEM, "prompt": prompt, "temperature": 0.0, "max_tokens": MAX_TOKENS,
            "grader": "repo_tests", "grader_config": cfg, "reference_files": ref_files,
            "mutants": mutants, "meta": meta, "difficulty": "hard"}


def materialize(gen: dict, *, release_seed: str, case_id: str) -> dict:
    c = _build(gen, release_seed, case_id)
    return json.loads(json.dumps({k: c[k] for k in ("system", "prompt", "temperature", "max_tokens", "grader",
                                                    "grader_config", "reference_files", "mutants", "meta",
                                                    "difficulty")}))


def reference(gen: dict, *, release_seed: str, case_id: str) -> str:
    return pf._files_answer(_build(gen, release_seed, case_id)["reference_files"])


def mutants(gen: dict, *, release_seed: str, case_id: str) -> list[str]:
    return [pf._files_answer(m) for m in _build(gen, release_seed, case_id)["mutants"].values()]


GRADE_IS_ENGINE = True


def grade(answer: str, materialized: dict) -> float:
    from .. import codebank
    v = codebank.grade_repo_tests(answer, materialized["grader_config"])
    return float(v.get("score") or 0.0)
