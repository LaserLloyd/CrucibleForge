"""patchfix - one-shot "fix the seeded bugs" family, MakoBench 4.5 GEN2-P prototype.

The model gets a whole small project (a seeded lease-book service, see ``_patchbase``) plus its
authoritative SPEC.md, and is told the code has exactly K bugs: places where a SPEC rule is broken.
R of them come with a user bug report (a script and the wrong output it produced: a symptom, never
a location); the other K - R must be found by auditing the code against SPEC.md. One answer, no tool
loop, no running code. The answer is SEARCH/REPLACE blocks or whole files (the code bank's format).

Hidden tests (differential, frozen from the clean reference):
  f2p ``bug-<n>``   one group per planted bug: scripts where that bug alone changes the output and no
                    other planted bug does, so a group passes when its bug is fixed, whatever the rest;
  p2p ``keep-<n>``  scripts the broken program already answers right (core / restart / status / parse
                    mixes): any change that breaks one of them halves the score.
Score (code bank rule) = passed f2p / K, halved when any p2p group fails.

Level table (prompt tokens chars/4, measured in tests):
  level  bugs  reported  report style            program features   prompt tok
    1     3     3         got + expected          base               ~6.5k
    2     5     3         got + expected          base               ~6.6k
    3     7     3         got only                + quota            ~7.4k
    4     9     2         got only                + quota + extend   ~7.9k
"""
from __future__ import annotations

import json
import random

from . import _patchbase as pb

#: gen/_det.py (the shared deterministic launcher, 2026-11-14): its digest, so a launcher change moves this family's revision
_DET_SHA = "77fb46314ef63360"

FAMILY = "patchfix"
LEVELS = {
    1: {"bugs": 3, "reported": 3, "style": "full", "features": ()},
    2: {"bugs": 5, "reported": 3, "style": "full", "features": ()},
    3: {"bugs": 7, "reported": 3, "style": "got", "features": ("quota",)},
    4: {"bugs": 9, "reported": 2, "style": "got", "features": ("quota", "extend")},
    # knob round 2: the bug count is not given, so the model cannot stop when it has "found K"
    5: {"bugs": 9, "reported": 2, "style": "got", "features": ("quota", "extend"), "count": False},
    6: {"bugs": 12, "reported": 1, "style": "got", "features": ("quota", "extend"), "count": False},
    # knob round 3: the intricate queue is in the program and at least 4 bugs sit in it
    7: {"bugs": 8, "reported": 1, "style": "got", "features": ("quota", "extend", "queue"), "count": False,
        "focus": ("queue", 4)},
}
_ANSWER_TOKENS = {1: 700, 2: 1100, 3: 1500, 4: 1900, 5: 1900, 6: 2500, 7: 2200}
for _lv, _row in LEVELS.items():
    _row["answer_tokens"] = _ANSWER_TOKENS[_lv]

POOL = 900            # random scripts screened per instance
MIN_ISO = 4           # isolated trigger scripts a planted bug needs
POOL_MIXES = [None, {"RESTART": 16, "TOUCH": 22}, {"STATUS": 25, "SWEEP": 12}, {"LOG": 20, "ACQ": 40},
              {"ACQ": 60, "REL": 2, "SWEEP": 2}, {"BAD": 15}]
PER_GROUP = 8         # scripts per hidden group
KEEP_MIXES = {
    "keep-core": None,
    "keep-restart": {"RESTART": 18, "TOUCH": 20},
    "keep-status": {"STATUS": 25, "SWEEP": 12},
    "keep-parse": {"BAD": 18, "LOG": 10},
}
MAX_TOKENS = 65536

SYSTEM = """You are a senior software engineer working in an existing project. You cannot run code or tests here.

Answer with your edits. For each change, write the file path on its own line and then a SEARCH/REPLACE block:

path/to/file.py
```python
<<<<<<< SEARCH
<lines copied exactly from the current file>
=======
<the new lines>
>>>>>>> REPLACE
```

The SEARCH text must match the current file exactly and occur in it once. You may instead give the
whole new content of a file (path line, then one fenced block with the entire file); never abbreviate
a whole file with placeholders such as "... rest unchanged". Change only files under the package
directory. Use the standard library only. A short explanation before the edits is fine."""

_CACHE: dict = {}


def _scripts(rng, knobs, features, n, mix=None, rn=None):
    return [pb.to_rendered(pb.gen_script(rng, knobs, features, mix=mix), rn) for _ in range(n)]


def _build(gen: dict, release_seed: str, case_id: str) -> dict:
    level = int(gen.get("level", 2))
    key = str(gen.get("key", "core"))
    ck = (FAMILY, level, key, release_seed, case_id)
    if ck in _CACHE:
        return _CACHE[ck]
    if level not in LEVELS:
        raise ValueError(f"patchfix: no level {level}")
    L = LEVELS[level]
    feats = L["features"]
    for attempt in range(40):
        rng = pb.instance_rng(FAMILY, release_seed, case_id, level, key, attempt)
        knobs = pb.sample_knobs(rng)
        rn = pb.sample_rename(rng)
        out = _try_instance(rng, knobs, rn, L, feats)
        if out is not None:
            out["meta"]["attempt"] = attempt
            _CACHE[ck] = out
            return out
    raise RuntimeError("patchfix: no instance after 40 attempts")


def _try_instance(rng: random.Random, knobs: dict, rn: dict, L: dict, feats) -> dict | None:
    clean_tree = pb.build_tree(knobs, feats, (), rn)
    clean = pb.load_runner(clean_tree, rn)
    live = [b["name"] for b in pb.BUGS if b.get("when", lambda k: True)(knobs)
            and (b.get("feature") is None or b["feature"] in feats)]
    runners = {b: pb.load_runner(pb.build_tree(knobs, feats, (b,), rn), rn) for b in live}
    pool = []
    for mix in POOL_MIXES:
        pool += _scripts(rng, knobs, feats, POOL // len(POOL_MIXES), mix=mix, rn=rn)
    want = [clean(s) for s in pool]
    diff = {b: {i for i, s in enumerate(pool) if runners[b](s) != want[i]} for b in live}
    # pick K bugs greedily: each must keep >= MIN_ISO scripts that it alone (among the chosen) breaks;
    # at most 2 (K <= 5) or 3 bugs per file
    cand = [b for b in live if len(diff[b]) >= MIN_ISO]

    def iso_of(b, among):
        return [i for i in sorted(diff[b]) if not any(i in diff[o] for o in among if o != b)]
    chosen, iso, base_tree, base = [], {}, None, None
    for _shuffle in range(40):
        rng.shuffle(cand)
        if L.get("focus"):
            area, need = L["focus"]
            focus = [b for b in cand if pb.BUG_BY_NAME[b]["area"] == area]
            cand = focus[:need] + [b for b in cand if b not in focus[:need]]
        chosen, per_file = [], {}
        for b in cand:
            f = pb.BUG_BY_NAME[b]["file"]
            if per_file.get(f, 0) >= (2 if L["bugs"] <= 5 else 3 if not L.get("focus") else 4):
                continue
            trial = chosen + [b]
            if all(len(iso_of(x, trial)) >= MIN_ISO for x in trial):
                chosen = trial
                per_file[f] = per_file.get(f, 0) + 1
                if len(chosen) == L["bugs"]:
                    break
        if len(chosen) < L["bugs"]:
            continue
        # exact check with the joint programs: a group script must (a) be broken by the given program,
        # (b) be right once its own bug alone is fixed, (c) be broken by its bug alone. Bugs that mask
        # each other (fixing one exposes the other) fail (a) and the set is redrawn.
        base_tree = pb.build_tree(knobs, feats, tuple(chosen), rn)
        base = pb.load_runner(base_tree, rn)
        allbut = {b: pb.load_runner(pb.build_tree(knobs, feats, tuple(o for o in chosen if o != b), rn), rn)
                  for b in chosen}
        iso = {b: [i for i in iso_of(b, chosen) if base(pool[i]) != want[i] and allbut[b](pool[i]) == want[i]]
               for b in chosen}
        if all(len(v) >= MIN_ISO for v in iso.values()):
            break
        chosen = []
    if len(chosen) < L["bugs"]:
        return None
    groups = {}
    order = list(chosen)
    rng.shuffle(order)          # group numbers do not reveal the bug order in the catalogue
    for n, b in enumerate(order, 1):
        idx = iso[b][:PER_GROUP]
        groups[f"bug-{n}"] = {"kind": "f2p", "bug": b, "cases": [[pool[i], want[i]] for i in idx]}
    # p2p: scripts the broken program already gets right
    for gid, mix in KEEP_MIXES.items():
        got = []
        tries = 0
        while len(got) < PER_GROUP and tries < 200:
            tries += 1
            s = pb.to_rendered(pb.gen_script(rng, knobs, feats, mix=mix), rn)
            w = clean(s)
            if base(s) == w and s.count("\n") >= 10:
                got.append([s, w])
        if len(got) < 4:
            return None
        groups[gid] = {"kind": "p2p", "cases": got}
    # reports: shortest isolated trigger for R of the bugs
    reported = order[:L["reported"]]
    reports = []
    for b in reported:
        r = _minimal_report(pool, iso[b], rn, clean, runners, b, [o for o in chosen if o != b], base)
        if r is None:
            return None
        reports.append(r)
    return _assemble(knobs, rn, L, feats, chosen, order, reported, reports, groups, base_tree, clean_tree)


def _minimal_report(pool, iso_idx, rn, clean, runners, bug, others, base):
    """Shortest isolated trigger: start from the shortest isolated pool script, then delete lines greedily
    while the bug still changes the output and no other planted bug does."""
    def ok(lines):
        s = "\n".join(lines) + "\n"
        return runners[bug](s) != clean(s) and all(runners[o](s) == clean(s) for o in others)
    starts = sorted(iso_idx, key=lambda i: pool[i].count("\n"))[:3]
    best = None
    for i in starts:
        lines = pool[i].rstrip("\n").split("\n")
        j = 0
        while j < len(lines):
            trial = lines[:j] + lines[j + 1:]
            if trial and ok(trial):
                lines = trial
            else:
                j += 1
        if best is None or len(lines) < len(best):
            best = lines
    if best is None:
        return None
    s = "\n".join(best) + "\n"
    return {"bug": bug, "script": s, "got": base(s), "want": clean(s)}


def _render_report(n, r, style):
    t = (f"### Report {n}\n\nRunning this script:\n\n```\n{r['script']}```\n\ngives:\n\n```\n{r['got']}```\n")
    if style == "full":
        t += f"\nbut by SPEC.md it should give:\n\n```\n{r['want']}```\n"
    else:
        t += "\nAt least one of these answer lines is not what SPEC.md says.\n"
    return t


def _assemble(knobs, rn, L, feats, chosen, order, reported, reports, groups, base_tree, clean_tree):
    pkg = rn["package"]
    K, R = L["bugs"], L["reported"]
    files_txt = []
    for path in ["SPEC.md"] + sorted(p for p in base_tree if p != "SPEC.md"):
        lang = "markdown" if path.endswith(".md") else "python"
        body = base_tree[path]
        if path.endswith("__init__.py") and not body.strip():
            continue
        files_txt.append(f"{path}\n```{lang}\n{body}```\n")
    rep_txt = "\n".join(_render_report(i, r, L["style"]) for i, r in enumerate(reports, 1))
    unreported = K - R
    counted = L.get("count", True)
    prompt = (
        f"# Task\n\nThis project, `{pkg}`, is specified by SPEC.md, which is authoritative. "
        + (f"The code has exactly {K} bugs. " if counted else
           "The code has bugs; how many is not known. ")
        + "Each bug is one place in the code that breaks a rule SPEC.md states; fixing it is a small local "
        "edit.\n\n"
        + (f"Users reported {R} of the bugs (below). A report shows a symptom, not where the bug is."
           + (f" The other {unreported} bugs were not reported: find them by checking the code against "
              f"SPEC.md." if unreported else "") if counted else
           f"Users reported {R} problem{'s' if R > 1 else ''} (below). A report shows a symptom, not where "
           f"the bug is. Other bugs were not reported: find them by checking the code against SPEC.md.")
        + (f"\n\nFix all {K} bugs in one answer." if counted else "\n\nFix every bug in one answer.")
        + " Hidden tests run many command scripts and compare every "
        f"answer line with what SPEC.md says. They also check that everything that already works still "
        f"works, so change nothing else: no refactoring, renaming or reformatting.\n\n"
        f"## Bug reports\n\n{rep_txt}\n## The project\n\n" + "\n".join(files_txt))
    hidden, gspec = {}, []
    for gid, g in groups.items():
        fname = f"test_{gid.replace('-', '_')}.py"
        hidden[fname] = pb.hidden_file(pkg, gid, g["cases"])
        gspec.append({"id": gid, "file": fname, "kind": g["kind"]})
    files = {p: t for p, t in base_tree.items()}
    ref_files = {p: clean_tree[p] for p in clean_tree if p.startswith(pkg + "/") and clean_tree[p] != base_tree[p]}
    # mutants: fix only the reported bugs; fix all and break one working rule
    rep_tree = pb.build_tree(knobs, feats, tuple(b for b in chosen if b not in reported), rn)
    mutants = ({"reported-only": {p: rep_tree[p] for p in rep_tree if rep_tree[p] != base_tree[p]}}
               if len(reported) < len(chosen) else {})
    spare = [b["name"] for b in pb.BUGS if b["name"] not in chosen and b.get("when", lambda k: True)(knobs)]
    meta = {"family": FAMILY, "knobs": knobs, "rename": {k: v for k, v in rn.items()},
            "bugs": chosen, "group_bug": {g: v.get("bug") for g, v in groups.items()},
            "reported": reported, "features": list(feats), "answer_tokens": L["answer_tokens"],
            "prompt_tokens": pb.answer_tokens(SYSTEM + prompt),
            "program_lines": sum(t.count("\n") for p, t in base_tree.items() if p.endswith(".py")),
            "spare_bugs": spare}
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


def _files_answer(files: dict) -> str:
    return "\n".join(f"{p}\n```python\n{t}```\n" for p, t in sorted(files.items()))


def reference(gen: dict, *, release_seed: str, case_id: str) -> str:
    return _files_answer(_build(gen, release_seed, case_id)["reference_files"])


def mutants(gen: dict, *, release_seed: str, case_id: str) -> list[str]:
    return [_files_answer(m) for m in _build(gen, release_seed, case_id)["mutants"].values()]


GRADE_IS_ENGINE = True


def grade(answer: str, materialized: dict) -> float:
    from .. import codebank
    v = codebank.grade_repo_tests(answer, materialized["grader_config"])
    return float(v.get("score") or 0.0)
