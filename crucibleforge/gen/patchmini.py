"""patchmini - small, cheap repairs on the PATCHFIX project (C3 "repairs existing code", low and mid cells).

Why (2026-11-14): on the reference (Qwen3.8-27B, thinking on) a patchfix L5 unit costs 37k tokens / 37 min:
it audits the whole 450-line program against the whole SPEC, function by function, with no point at which it
may stop ("how many is not known"). patchmini keeps everything that works in patchfix (the seeded program,
SPEC rules, symptom reports, differential hidden tests, per-bug isolation, keep groups) and cuts the reading:

* only the files that hold a bug are shown (plus ``config.py``, whose values every rule reads); at level 3 one
  more file WITHOUT a bug is shown, so the model must judge which file is wrong;
* only the SPEC sections those bugs break are shown (plus the header, section 1 settings and section 2 lines);
* the bug count is always given, every bug sits in its own file, and the answer is the changed lines only.

Levels (base program: no quota / extend / queue):
  level  bugs  reported  report style       extra clean file   ~prompt tokens
    1     2     2         got + expected     -                  ~1.5-2.5k
    2     3     3         got only           -                  ~2-3.5k
    3     3     2         got only           1                  ~3-4.5k
Score = passed f2p groups / bugs, halved when a keep group fails (engine ``repo_tests``); keep scripts are
untouched by every planted bug on its own (patchhard's rule), so a partial fix is never halved.
Same (key, level) -> byte-identical case; instance rng is seeded with this family's name.
"""
from __future__ import annotations

import hashlib
import json
import random
import re
from pathlib import Path

from . import _patchbase as pb
from . import patchfix as pf

#: gen/_det.py (the shared deterministic launcher, 2026-11-14): its digest, so a launcher change moves this family's revision
_DET_SHA = "77fb46314ef63360"

FAMILY = "patchmini"
MAX_TOKENS = 32768

LEVELS = {
    1: {"bugs": 2, "reported": 2, "style": "full", "decoys": 0, "answer_tokens": 500},
    2: {"bugs": 3, "reported": 3, "style": "got", "decoys": 0, "answer_tokens": 700},
    3: {"bugs": 3, "reported": 2, "style": "got", "decoys": 1, "answer_tokens": 800},
}
FEATURES = ()
#: sections whose rules a bug's section reads (section 3 = idle time and expiry is always shown): store and
#: coalescing (6) decide TOUCH's answer (5); the journal's events (8) are written by 4-6
RELATED = {"5": {"6"}, "6": {"5"}, "8": {"4", "5"}}

SYSTEM = """You are a software engineer fixing bugs in an existing project. You cannot run code or tests here.

Answer with your edits. For each change, write the file path on its own line and then a SEARCH/REPLACE block:

path/to/file.py
```python
<<<<<<< SEARCH
<lines copied exactly from the current file>
=======
<the new lines>
>>>>>>> REPLACE
```

The SEARCH text must match the current file exactly and occur in it once. Give only the lines you change
(a few lines of context are fine); do not repeat whole files. Use the standard library only."""

#: chains.revision() digests only THIS file: pins of the modules it builds on (tests/test_gen_patchmini.py)
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
    level = int(gen.get("level", 1))
    key = str(gen.get("key", "core"))
    ck = (FAMILY, level, key, release_seed, case_id)
    if ck in _CACHE:
        return _CACHE[ck]
    if level not in LEVELS:
        raise ValueError(f"patchmini: no level {level}")
    L = LEVELS[level]
    for attempt in range(40):
        rng = pb.instance_rng(FAMILY, release_seed, case_id, level, key, attempt)
        knobs = pb.sample_knobs(rng)
        rn = pb.sample_rename(rng)
        out = _try_instance(rng, knobs, rn, L)
        if out is not None:
            out["meta"]["attempt"] = attempt
            _CACHE[ck] = out
            return out
    raise RuntimeError("patchmini: no instance after 40 attempts")


def _try_instance(rng: random.Random, knobs: dict, rn: dict, L: dict) -> dict | None:
    feats = FEATURES
    clean_tree = pb.build_tree(knobs, feats, (), rn)
    clean = pb.load_runner(clean_tree, rn)
    live = [b["name"] for b in pb.BUGS if b.get("when", lambda k: True)(knobs) and b.get("feature") is None]
    runners = {b: pb.load_runner(pb.build_tree(knobs, feats, (b,), rn), rn) for b in live}
    pool = []
    for mix in pf.POOL_MIXES:
        pool += pf._scripts(rng, knobs, feats, pf.POOL // len(pf.POOL_MIXES), mix=mix, rn=rn)
    want = [clean(s) for s in pool]
    diff = {b: {i for i, s in enumerate(pool) if runners[b](s) != want[i]} for b in live}
    cand = [b for b in live if len(diff[b]) >= pf.MIN_ISO]

    def iso_of(b, among):
        return [i for i in sorted(diff[b]) if not any(i in diff[o] for o in among if o != b)]
    chosen, iso = [], {}
    for _shuffle in range(40):
        rng.shuffle(cand)
        chosen, files = [], set()
        for b in cand:
            f = pb.BUG_BY_NAME[b]["file"]
            if f in files:                      # one bug per file
                continue
            trial = chosen + [b]
            if all(len(iso_of(x, trial)) >= pf.MIN_ISO for x in trial):
                chosen, files = trial, files | {f}
                if len(chosen) == L["bugs"]:
                    break
        if len(chosen) < L["bugs"]:
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
    order = list(chosen)
    rng.shuffle(order)
    groups = {}
    for n, b in enumerate(order, 1):
        groups[f"bug-{n}"] = {"kind": "f2p", "bug": b, "cases": [[pool[i], want[i]] for i in iso[b][:pf.PER_GROUP]]}
    for gid, mix in pf.KEEP_MIXES.items():
        got, tries = [], 0
        while len(got) < pf.PER_GROUP and tries < 1500:
            tries += 1
            s = pb.to_rendered(pb.gen_script(rng, knobs, feats, mix=mix), rn)
            w = clean(s)
            if base(s) == w and s.count("\n") >= 10 and all(runners[b](s) == w for b in chosen):
                got.append([s, w])
        if len(got) < 4:
            return None
        groups[gid] = {"kind": "p2p", "cases": got}
    reported = order[:L["reported"]]
    reports = []
    for b in reported:
        r = pf._minimal_report(pool, iso[b], rn, clean, runners, b, [o for o in chosen if o != b], base)
        if r is None:
            return None
        reports.append(r)
    bug_files = sorted({pb.BUG_BY_NAME[b]["file"] for b in chosen})
    spare = sorted(f for f in {b["file"] for b in pb.BUGS if b.get("feature") is None}
                   if f not in bug_files and f != "config.py")
    rng.shuffle(spare)
    decoys = spare[:L["decoys"]]
    return _assemble(knobs, rn, L, chosen, order, reported, reports, groups, base_tree, clean_tree,
                     bug_files, decoys)


def _spec_view(spec: str, sections: set[str]) -> str:
    """SPEC.md cut to its header plus the ``## <n>.`` sections named (sub-sections travel with their parent)."""
    parts = re.split(r"(?m)^(?=## )", spec)
    out = [parts[0]]
    for p in parts[1:]:
        m = re.match(r"## (\d+)\.", p)
        if m and m.group(1) in sections:
            out.append(p)
    return "".join(out).rstrip("\n") + "\n"


def _assemble(knobs, rn, L, chosen, order, reported, reports, groups, base_tree, clean_tree, bug_files, decoys):
    pkg = rn["package"]
    K, R = L["bugs"], L["reported"]
    secs = {"1", "2", "3"} | {pb.BUG_BY_NAME[b]["spec"].split(".")[0] for b in chosen}
    for s_ in list(secs):                   # a rule's answer text is defined in a sibling section
        secs |= RELATED.get(s_, set())
    spec = _spec_view(base_tree["SPEC.md"], secs)
    shown = sorted(set(bug_files) | set(decoys) | {"config.py"})
    shown_paths = [f"{pkg}/{pb.apply_rename(f, rn)}" for f in shown]
    files_txt = [f"SPEC.md (the sections that matter here)\n```markdown\n{spec}```\n"]
    for p in shown_paths:
        files_txt.append(f"{p}\n```python\n{base_tree[p]}```\n")
    rep_txt = "\n".join(pf._render_report(i, r, L["style"]) for i, r in enumerate(reports, 1))
    others = K - R
    nfiles = len(shown) - 1
    prompt = (
        f"# Task\n\nThis project, `{pkg}`, is specified by SPEC.md, which is authoritative. Below are SPEC.md's "
        f"relevant sections and {nfiles} of the project's code files (plus its settings, `config.py`). "
        f"These files contain exactly {K} bugs, each in a different file. Each bug is one place in the code "
        f"that breaks a rule SPEC.md states; fixing it is a small local edit"
        + (f", and one of the files shown has no bug" if L["decoys"] else "") + ".\n\n"
        + (f"Users reported all {K} bugs (below). " if not others else
           f"Users reported {R} of the bugs (below); find the other {others} by checking the code against SPEC.md. ")
        + "A report shows a symptom, not where the bug is.\n\n"
        f"Fix all {K} bugs in one answer. Hidden tests run many command scripts and compare every answer line "
        f"with what SPEC.md says. They also check that everything that already works still works, so change "
        f"nothing else: no refactoring, renaming or reformatting.\n\n"
        f"## Bug reports\n\n{rep_txt}\n## The code\n\n" + "\n".join(files_txt))
    hidden, gspec = {}, []
    for gid, g in groups.items():
        fname = f"test_{gid.replace('-', '_')}.py"
        hidden[fname] = pb.hidden_file(pkg, gid, g["cases"])
        gspec.append({"id": gid, "file": fname, "kind": g["kind"]})
    ref_files = {p: clean_tree[p] for p in clean_tree if p.startswith(pkg + "/") and clean_tree[p] != base_tree[p]}
    meta = {"family": FAMILY, "knobs": knobs, "rename": dict(rn), "bugs": chosen,
            "group_bug": {g: v.get("bug") for g, v in groups.items()}, "reported": reported,
            "shown": shown_paths, "decoys": decoys, "spec_sections": sorted(secs, key=int),
            "answer_tokens": L["answer_tokens"], "prompt_tokens": pb.answer_tokens(SYSTEM + prompt)}
    cfg = {"files": dict(base_tree), "hidden": hidden, "groups": gspec, "allowed_paths": shown_paths,
           "new_paths": [], "pythonpath": ["."], "answer": "edits",
           "deterministic": True}
    return {"system": SYSTEM, "prompt": prompt, "temperature": 0.0, "max_tokens": MAX_TOKENS,
            "grader": "repo_tests", "grader_config": cfg, "reference_files": ref_files,
            "mutants": {}, "meta": meta, "difficulty": "medium"}


def materialize(gen: dict, *, release_seed: str, case_id: str) -> dict:
    c = _build(gen, release_seed, case_id)
    return json.loads(json.dumps({k: c[k] for k in ("system", "prompt", "temperature", "max_tokens", "grader",
                                                    "grader_config", "reference_files", "mutants", "meta",
                                                    "difficulty")}))


def reference(gen: dict, *, release_seed: str, case_id: str) -> str:
    return pf._files_answer(_build(gen, release_seed, case_id)["reference_files"])


def mutants(gen: dict, *, release_seed: str, case_id: str) -> list[str]:
    return []


GRADE_IS_ENGINE = True


def grade(answer: str, materialized: dict) -> float:
    from .. import codebank
    v = codebank.grade_repo_tests(answer, materialized["grader_config"])
    return float(v.get("score") or 0.0)
