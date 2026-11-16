"""patchmod - one-shot "add a specified feature" family, MakoBench 4.5 GEN2-P prototype.

The model gets the same seeded lease-book project as ``patchfix`` (no planted bugs) and the SPEC
section(s) of one or two new commands, written as a feature request. The feature has to be threaded
through the layers the project already has: the parser, the command loop, the book's rules, the
persistent store (and so RESTART), the STATUS renderer and the journal, and it has to compose with
the existing rules (expiry boundary, coalesced writes, vacate marks, re-acquire). One answer, no tool
loop, no running code; SEARCH/REPLACE blocks or whole files.

Hidden tests (differential, frozen from the reference that has the feature):
  f2p ``<feature>-<area>``  scripts that use the feature, mixed toward one area each
                            (basic, restart, expiry, contention, status/log);
  p2p ``keep-<n>``          scripts without the new commands: the old behaviour, which the reference
                            leaves unchanged (asserted at build time), must stay byte-identical.
Score (code bank rule) = passed f2p / f2p groups, halved when any p2p group fails.

Level table:
  level  features to add      f2p groups  prompt tok
    1    extend               5           ~6.0k
    2    quota                5           ~6.1k
    3    quota + extend       10 + 2      ~6.4k
"""
from __future__ import annotations

import json

from . import _patchbase as pb
from .patchfix import SYSTEM, _files_answer

#: gen/_det.py (the shared deterministic launcher, 2026-11-14): its digest, so a launcher change moves this family's revision
_DET_SHA = "77fb46314ef63360"

FAMILY = "patchmod"
LEVELS = {
    1: {"add": ("extend",)},
    2: {"add": ("quota",)},
    3: {"add": ("quota", "extend")},
    # knob round 2: an intricate cross-cutting feature (a StudioForge-style wait queue: aging priority,
    # no overtaking, timeouts, grants before every command, persistence across RESTART)
    4: {"add": ("queue",)},
    5: {"add": ("quota", "extend", "queue")},
}
_ANSWER_TOKENS = {1: 900, 2: 1300, 3: 2100, 4: 2600, 5: 4200}
for _lv, _row in LEVELS.items():
    _row["answer_tokens"] = _ANSWER_TOKENS[_lv]

PER_GROUP = 8
MAX_TOKENS = 65536
AREAS = {
    "basic": {},
    "restart": {"RESTART": 16, "TOUCH": 20},
    "expiry": {"SWEEP": 14, "TOUCH": 18, "ACQ": 40},
    "contention": {"ACQ": 60, "REL": 3, "SWEEP": 3},
    "status": {"STATUS": 25, "LOG": 14},
}
FEATURE_CMD = {"quota": ("QUOTA", "OK quota "), "extend": ("EXTEND", " ttl="), "queue": (None, "GRANT W")}
QUEUE_AREAS = {
    "basic": {"ACQ": 50},
    "contention": {"ACQ": 70, "REL": 12, "SWEEP": 2},
    "aging": {"ACQ": 45, "TOUCH": 25, "STATUS": 14},
    "restart": {"RESTART": 14, "ACQ": 45},
    "status": {"STATUS": 25, "LOG": 12, "ACQ": 45},
}
KEEP_MIXES = {
    "keep-core": None,
    "keep-restart": {"RESTART": 18, "TOUCH": 20},
    "keep-status": {"STATUS": 25, "SWEEP": 12},
}

_CACHE: dict = {}


def _build(gen: dict, release_seed: str, case_id: str) -> dict:
    level = int(gen.get("level", 2))
    key = str(gen.get("key", "core"))
    ck = (FAMILY, level, key, release_seed, case_id)
    if ck in _CACHE:
        return _CACHE[ck]
    if level not in LEVELS:
        raise ValueError(f"patchmod: no level {level}")
    add = LEVELS[level]["add"]
    rng = pb.instance_rng(FAMILY, release_seed, case_id, level, key)
    knobs = pb.sample_knobs(rng)
    rn = pb.sample_rename(rng)
    base_tree = pb.build_tree(knobs, (), (), rn)
    ref_tree = pb.build_tree(knobs, add, (), rn)
    base, ref = pb.load_runner(base_tree, rn), pb.load_runner(ref_tree, rn)
    groups = {}
    combos = [(f,) for f in add] + ([tuple(add)] if len(add) > 1 else [])
    for combo in combos:
        if len(combo) > 1:
            areas = ({"basic": {"ACQ": 45}, "restart": {"RESTART": 14, "ACQ": 45}} if "queue" in combo
                     else {"basic": {}, "restart": AREAS["restart"]})
        else:
            areas = QUEUE_AREAS if combo == ("queue",) else AREAS
        for area, mix in areas.items():
            m = dict(mix)
            for f in add:
                if FEATURE_CMD[f][0]:
                    m[FEATURE_CMD[f][0]] = 12 if f in combo else 0
            # scripts of a single-feature group use only that feature (the queue's "q" needs "queue")
            feats_used = tuple(f for f in add if f in combo)
            got, tries = [], 0
            while len(got) < PER_GROUP and tries < 1500:
                tries += 1
                s = pb.to_rendered(pb.gen_script(rng, knobs, feats_used, mix=m), rn)
                w = ref(s)
                # the feature must succeed at least once in the script (not only its error paths)
                if not all(FEATURE_CMD[f][1] in w for f in combo):
                    continue
                if base(s) != w:
                    got.append([s, w])
            if len(got) < 4:
                raise RuntimeError(f"patchmod: group {combo}/{area} has {len(got)} scripts")
            groups[f"{'+'.join(combo)}-{area}"] = {"kind": "f2p", "cases": got}
    order = [g for g in groups]
    for gid, mix in KEEP_MIXES.items():
        got = []
        for _ in range(PER_GROUP):
            s = pb.to_rendered(pb.gen_script(rng, knobs, (), mix=mix), rn)
            w = base(s)
            if ref(s) != w:
                raise RuntimeError("patchmod: the reference changes old behaviour")
            got.append([s, w])
        groups[gid] = {"kind": "p2p", "cases": got}
    out = _assemble(knobs, rn, add, groups, base_tree, ref_tree)
    _CACHE[ck] = out
    return out


def _assemble(knobs, rn, add, groups, base_tree, ref_tree):
    pkg = rn["package"]
    files_txt = []
    for path in ["SPEC.md"] + sorted(p for p in base_tree if p != "SPEC.md"):
        if path.endswith("__init__.py") and not base_tree[path].strip():
            continue
        lang = "markdown" if path.endswith(".md") else "python"
        files_txt.append(f"{path}\n```{lang}\n{base_tree[path]}```\n")
    req = pb.render_spec("".join(pb.apply_rename(pb.FEATURE_SPEC[f], rn) for f in pb.FEATURES if f in add),
                         knobs, ())
    if "queue" not in add:      # levels 1-3 keep their round-1 wording byte for byte
        words = " and ".join(f"`{rn['cmd'][FEATURE_CMD[f][0]]}`" for f in pb.FEATURES if f in add)
        what = (f"the new command{'s' if len(add) > 1 else ''} {words}, specified below as new SPEC "
                f"section{'s' if len(add) > 1 else ''}. Every rule of the existing SPEC keeps applying, to the "
                f"new command{'s' if len(add) > 1 else ''} too.")
    else:
        names = [f"the `{rn['cmd'][FEATURE_CMD[f][0]]}` command" if FEATURE_CMD[f][0] else "queued requests"
                 for f in pb.FEATURES if f in add]
        words = ", ".join(names[:-1]) + (" and " if len(names) > 1 else "") + names[-1]
        what = (f"{words}, specified below as new SPEC section{'s' if len(add) > 1 else ''}. Every rule of "
                f"the existing SPEC keeps applying to the new behaviour too.")
    prompt = (
        f"# Task\n\nThis project, `{pkg}`, is specified by SPEC.md, which is authoritative; the code "
        f"implements it correctly. Add {what}\n\n"
        f"Hidden tests run many command scripts and compare every answer line with what SPEC.md plus the "
        f"new section{'s' if len(add) > 1 else ''} say. They also check that every existing behaviour is "
        f"unchanged, so change nothing else: no refactoring, renaming or reformatting.\n\n"
        f"## Feature request (new SPEC section{'s' if len(add) > 1 else ''})\n{req}\n"
        f"## The project\n\n" + "\n".join(files_txt))
    hidden, gspec = {}, []
    for gid, g in groups.items():
        fname = "test_" + gid.replace("-", "_").replace("+", "_") + ".py"
        hidden[fname] = pb.hidden_file(pkg, gid, g["cases"])
        gspec.append({"id": gid, "file": fname, "kind": g["kind"]})
    ref_files = {p: ref_tree[p] for p in ref_tree if p.startswith(pkg + "/") and ref_tree[p] != base_tree[p]}
    mutants = {}
    if len(add) > 1:
        half = pb.build_tree(knobs, add[:1], (), rn)
        mutants["first-feature-only"] = {p: half[p] for p in half if half[p] != base_tree[p]}
    # the feature with a classic layering slip: quota state forgotten at RESTART / extend write coalesced
    if "quota" in add:
        t = dict(ref_tree)
        sv = next(p for p in t if p.endswith("/service.py"))
        line = f"        if cmd == \"{rn['cmd']['RESTART']}\":\n"
        assert t[sv].count(line) == 1
        t[sv] = t[sv].replace(line, line + "            self.store.quotas = {}\n")
        mutants["quota-lost-at-restart"] = {p: t[p] for p in t if t[p] != base_tree.get(p)}
    if "extend" in add:
        t = pb.build_tree(knobs, add, ("extend_coalesced",), rn)
        mutants["extend-coalesced"] = {p: t[p] for p in t if t[p] != base_tree.get(p)}
    if "queue" in add:
        for name in ("queue_overtakes", "queue_no_aging"):
            t = pb.build_tree(knobs, add, (name,), rn)
            mutants[name] = {p: t[p] for p in t if t[p] != base_tree.get(p)}
    meta = {"family": FAMILY, "knobs": knobs, "rename": dict(rn), "add": list(add),
            "answer_tokens": LEVELS_ANSWER(add), "prompt_tokens": pb.answer_tokens(SYSTEM + prompt),
            "program_lines": sum(t.count("\n") for p, t in base_tree.items() if p.endswith(".py")),
            "ref_lines_changed": sum(abs(ref_tree[p].count("\n") - base_tree[p].count("\n")) for p in ref_files)}
    cfg = {"files": dict(base_tree), "hidden": hidden, "groups": gspec, "allowed_paths": [f"{pkg}/*.py"],
           "new_paths": [f"{pkg}/*.py"], "pythonpath": ["."], "answer": "edits",
           "deterministic": True}
    return {"system": SYSTEM, "prompt": prompt, "temperature": 0.0, "max_tokens": MAX_TOKENS,
            "grader": "repo_tests", "grader_config": cfg, "reference_files": ref_files,
            "mutants": mutants, "meta": meta, "difficulty": "hard"}


def LEVELS_ANSWER(add):
    return next(L["answer_tokens"] for L in LEVELS.values() if L["add"] == tuple(add))


def materialize(gen: dict, *, release_seed: str, case_id: str) -> dict:
    c = _build(gen, release_seed, case_id)
    return json.loads(json.dumps({k: c[k] for k in ("system", "prompt", "temperature", "max_tokens", "grader",
                                                    "grader_config", "reference_files", "mutants", "meta",
                                                    "difficulty")}))


def reference(gen: dict, *, release_seed: str, case_id: str) -> str:
    return _files_answer(_build(gen, release_seed, case_id)["reference_files"])


def mutants(gen: dict, *, release_seed: str, case_id: str) -> list[str]:
    return [_files_answer(m) for m in _build(gen, release_seed, case_id)["mutants"].values()]


GRADE_IS_ENGINE = True


def grade(answer: str, materialized: dict) -> float:
    from .. import codebank
    v = codebank.grade_repo_tests(answer, materialized["grader_config"])
    return float(v.get("score") or 0.0)
