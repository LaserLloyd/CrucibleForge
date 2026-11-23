"""patchtough - staged Fixes pack (MakoBench 4.5 FIXES lane, second attempt): patchmod's wait-queue feature request
graded by hidden tests that are built to catch the plausible wrong implementations.

Why: the first Fixes pack (patchgap) added traps to a repair task and mid-size models still scored 1.00. The
inventory showed a different place with headroom: PATCHMOD L4/L5 (add a StudioForge-style wait queue: aging priority,
no overtaking, timeouts, grants before every command, persistence over RESTART) scored .40-.89 on hosted models, but
the family's hidden tests were random differential scripts, so some seeds let a wrong queue (overtaking, no aging)
through at 1.0 (measured here: 4 of 8 L4 seeds pass `queue_overtakes`; that is why L5 was rejected).

This family keeps patchmod's project, prompt and reference byte for byte (it calls ``patchmod._assemble``) and
replaces the test draw:
* f2p ``<feature>-<area>``: patchmod's area groups, unchanged;
* f2p ``hit-<mutant>``: for every spec-violating variant of the feature (the BUGS table: overtaking, no aging, aging
  the wrong way, wait edge, queue lost at RESTART, no reclaim of expired leases before a grant, queue not offered on
  WAIT, STATUS not in priority order, grant not removed from the store, quota/extend slips) up to 3 scripts on which the
  reference and that variant differ, searched over the area mixes, so no listed wrong implementation can pass;
* p2p ``keep-*``: patchmod's keep groups.
A mutant that no script can tell from the reference on this draw (a knob made it equivalent) is not listed.
Score = passed f2p / f2p groups, halved when a keep group fails (code bank rule); graded by ``fixcheck.grade_answer``
(host-compared). This module imports fixcheck lazily and edits none of the five stamp files.

Levels: 1 = queue (patchmod L4 text), 2 = quota + extend + queue (patchmod L5 text), 3 = a patchhard repair
(6 bugs, SPEC authoritative) whose visible regression tests are partly WRONG: two tests expect what a different, plausible
wrong fix would answer, so they fail on the given code AND on the correct code. The right move is to fix per SPEC and
leave the tests; making them pass fails the hidden tests of that bug (machine-checked, no judgement of the reply text).
"""
from __future__ import annotations

import json

from . import _patchbase as pb
from . import patchmod as pm
from .patchfix import SYSTEM, _files_answer

FAMILY = "patchtough"
REVISION = "pt-1"
LEVELS = {1: {"add": ("queue",)}, 2: {"add": ("quota", "extend", "queue")}, 3: {"wrongtest": True}}
L3 = {"bugs": 6, "reported": 0, "style": "got", "features": ("quota", "extend", "queue"), "count": False,
      "hard": 5, "per_file": 5, "answer_tokens": 2500}
N_WRONG_TESTS = 2
PER_HIT = 3
MAX_TOKENS = 65536
_CACHE: dict = {}

_HIT_MIXES = {
    "queue": [pm.QUEUE_AREAS[a] for a in ("contention", "aging", "restart", "basic", "status")],
    "quota": [pm.AREAS[a] for a in ("contention", "expiry", "restart", "basic")],
    "extend": [pm.AREAS[a] for a in ("expiry", "restart", "basic", "contention")],
}


def _mutant_files(knobs, rn, add, name, base_tree):
    t = pb.build_tree(knobs, add, (name,), rn)
    return t, {p: t[p] for p in t if t[p] != base_tree.get(p)}


def _build(gen: dict, release_seed: str, case_id: str) -> dict:
    level = int(gen.get("level", 1))
    key = str(gen.get("key", "core"))
    ck = (level, key, release_seed, case_id)
    if ck in _CACHE:
        return _CACHE[ck]
    if level not in LEVELS:
        raise ValueError(f"patchtough: no level {level}")
    if level == 3:
        out = _build_wrongtest(release_seed, case_id, key)
        _CACHE[ck] = out
        return out
    add = LEVELS[level]["add"]
    rng = pb.instance_rng(FAMILY, release_seed, case_id, level, key)
    knobs = pb.sample_knobs(rng)
    rn = pb.sample_rename(rng)
    base_tree = pb.build_tree(knobs, (), (), rn)
    ref_tree = pb.build_tree(knobs, add, (), rn)
    base, ref = pb.load_runner(base_tree, rn), pb.load_runner(ref_tree, rn)
    groups: dict = {}
    combos = [(f,) for f in add] + ([tuple(add)] if len(add) > 1 else [])
    for combo in combos:
        if len(combo) > 1:
            areas = ({"basic": {"ACQ": 45}, "restart": {"RESTART": 14, "ACQ": 45}} if "queue" in combo
                     else {"basic": {}, "restart": pm.AREAS["restart"]})
        else:
            areas = pm.QUEUE_AREAS if combo == ("queue",) else pm.AREAS
        for area, mix in areas.items():
            m = dict(mix)
            for f in add:
                if pm.FEATURE_CMD[f][0]:
                    m[pm.FEATURE_CMD[f][0]] = 12 if f in combo else 0
            feats_used = tuple(f for f in add if f in combo)
            got, tries = [], 0
            while len(got) < pm.PER_GROUP and tries < 1500:
                tries += 1
                s = pb.to_rendered(pb.gen_script(rng, knobs, feats_used, mix=m), rn)
                w = ref(s)
                if not all(pm.FEATURE_CMD[f][1] in w for f in combo):
                    continue
                if base(s) != w:
                    got.append([s, w])
            if len(got) < 4:
                raise RuntimeError(f"patchtough: group {combo}/{area} has {len(got)} scripts")
            groups[f"{'+'.join(combo)}-{area}"] = {"kind": "f2p", "cases": got}
    # targeted groups: one per wrong implementation the spec rules out
    mut_files, hit_ids, skipped = {}, [], []
    for b in pb.BUGS:
        name, feat = b["name"], b.get("feature")
        if feat not in add or not b.get("when", lambda k: True)(knobs):
            continue
        try:
            t, files = _mutant_files(knobs, rn, add, name, base_tree)
        except Exception:
            skipped.append(name)
            continue
        if not files:
            skipped.append(name)
            continue
        mrun = pb.load_runner(t, rn)
        mixes = _HIT_MIXES.get(feat, _HIT_MIXES["queue"])
        got, tries = [], 0
        while len(got) < PER_HIT and tries < 2400:
            mix = dict(mixes[tries % len(mixes)])
            for f in add:
                if pm.FEATURE_CMD[f][0]:
                    mix[pm.FEATURE_CMD[f][0]] = 12
            tries += 1
            s = pb.to_rendered(pb.gen_script(rng, knobs, add, mix=mix), rn)
            w = ref(s)
            if w != mrun(s) and base(s) != w and s not in [g[0] for g in got]:
                got.append([s, w])
        if len(got) < 2:
            skipped.append(name)
            continue
        groups[f"hit-{name.replace('_', '-')}"] = {"kind": "f2p", "cases": got}
        mut_files[name] = files
        hit_ids.append(name)
    if len(hit_ids) < 5:
        raise RuntimeError(f"patchtough: only {len(hit_ids)} targeted groups")
    for gid, mix in pm.KEEP_MIXES.items():
        got = []
        for _ in range(pm.PER_GROUP):
            s = pb.to_rendered(pb.gen_script(rng, knobs, (), mix=mix), rn)
            w = base(s)
            if ref(s) != w:
                raise RuntimeError("patchtough: the reference changes old behaviour")
            got.append([s, w])
        groups[gid] = {"kind": "p2p", "cases": got}
    out = pm._assemble(knobs, rn, add, groups, base_tree, ref_tree)
    out["mutants"] = mut_files
    out["meta"] = dict(out["meta"], family=FAMILY, revision=REVISION, level=level, hits=hit_ids, skipped=skipped)
    out["meta"].pop("answer_tokens", None)
    _CACHE[ck] = out
    return out


def _build_wrongtest(release_seed, case_id, key):
    from . import patchhard as ph
    feats = L3["features"]
    for attempt in range(40):
        rng = pb.instance_rng(FAMILY, release_seed, case_id, 3, key, attempt)
        knobs = pb.sample_knobs(rng)
        rn = pb.sample_rename(rng)
        out = ph._try_instance(rng, knobs, rn, L3, feats)
        if out is None:
            continue
        res = _wrongtests(out, knobs, rn, feats)
        if res is not None:
            res["meta"]["attempt"] = attempt
            return res
    raise RuntimeError("patchtough: no level-3 instance after 40 attempts")


def _wrongtests(out, knobs, rn, feats):
    cfg, meta = out["grader_config"], out["meta"]
    chosen, pkg = meta["bugs"], rn["package"]
    base_tree = dict(cfg["files"])
    clean_tree = dict(base_tree)
    clean_tree.update(out["reference_files"])
    base, clean = pb.load_runner(base_tree, rn), pb.load_runner(clean_tree, rn)
    live = [b["name"] for b in pb.BUGS if b.get("when", lambda k: True)(knobs)
            and (b.get("feature") is None or b["feature"] in feats) and b["name"] not in chosen]
    groups = {g["id"]: g for g in cfg["groups"]}
    cases = {gid: json.loads(_cases_blob(cfg["hidden"][g["file"]])) for gid, g in groups.items()}
    wrong, used_b2, wrong_trees = [], set(), {}
    for gid, bug in meta["group_bug"].items():
        if bug is None or len(wrong) >= N_WRONG_TESTS:
            continue
        for b2 in live:
            if b2 in used_b2:
                continue
            t2 = pb.build_tree(knobs, feats, (b2,), rn)
            # the wrong fix keeps the other bugs fixed: clean + b2; it must also change a keep script or not (free)
            try:
                run2 = pb.load_runner(t2, rn)
            except Exception:
                continue
            for s, want in cases[gid]:
                e2 = run2(s)
                if e2 != want and e2 != base(s):
                    wrong.append({"group": gid, "bug": bug, "script": s, "expected": e2, "correct": want, "via": b2})
                    used_b2.add(b2)
                    wrong_trees[b2] = t2
                    break
            else:
                continue
            break
    if len(wrong) < N_WRONG_TESTS:
        return None
    # the visible regression tests: the wrong ones, plus one correct one for a bug that is not in a wrong test
    right = None
    for gid, bug in meta["group_bug"].items():
        if bug and all(w["group"] != gid for w in wrong):
            s, want = cases[gid][-1]
            right = {"group": gid, "bug": bug, "script": s, "expected": want}
            break
    shown = [dict(w) for w in wrong] + ([right] if right else [])
    shown.sort(key=lambda w: w["script"])
    lines = ["import unittest", f"from {pkg}.service import run", "", "", "class Regression(unittest.TestCase):"]
    for i, w in enumerate(shown, 1):
        lines += [f"    def test_case_{i}(self):", f"        script = {w['script']!r}", f"        want = {w['expected']!r}",
                  "        self.assertEqual(run(script), want)", ""]
    test_txt = "\n".join(lines) + "\n"
    head, proj = out["prompt"].split("## The project\n\n", 1)
    head = head.replace("No user has reported a problem yet: find the bugs by checking the code against SPEC.md.",
                        "The team's regression tests (tests/test_regression.py, below the project files) fail today.")
    prompt = (head + "## The project\n\n" + proj + f"\ntests/test_regression.py\n```python\n{test_txt}```\n")
    mutants = dict(out["mutants"])
    for b2, t2 in wrong_trees.items():
        mutants["pass-the-wrong-test-" + b2.replace("_", "-")] = {p: t2[p] for p in t2 if t2[p] != base_tree.get(p)
                                                                 and p.startswith(pkg + "/")}
    for w in wrong:
        t3 = pb.build_tree(knobs, feats, (w["bug"],), rn)       # everything fixed except the bug the wrong test hides
        mutants["unfixed-" + w["bug"].replace("_", "-")] = {p: t3[p] for p in t3 if t3[p] != base_tree.get(p)
                                                          and p.startswith(pkg + "/")}
    meta2 = dict(meta, family=FAMILY, revision=REVISION, level=3, wrong_tests=[{k: w[k] for k in ("group", "bug", "via")}
                                                                                for w in wrong],
                 prompt_tokens=pb.answer_tokens(SYSTEM + prompt))
    return {"system": SYSTEM, "prompt": prompt, "temperature": 0.0, "max_tokens": MAX_TOKENS, "grader": "repo_tests",
            "grader_config": cfg, "reference_files": out["reference_files"], "mutants": mutants, "meta": meta2,
            "difficulty": "hard"}


def _cases_blob(src: str) -> str:
    import re
    m = re.search(r"CASES = json.loads\((.*)\)\n", src)
    blob = eval(m.group(1), {})
    return json.dumps(list(json.loads(blob).values())[0])


_PUBLIC = ("system", "prompt", "temperature", "max_tokens", "grader", "grader_config", "reference_files", "mutants",
           "meta", "difficulty")


def materialize(gen: dict, *, release_seed: str, case_id: str) -> dict:
    c = _build(gen, release_seed, case_id)
    return json.loads(json.dumps({k: c[k] for k in _PUBLIC}))


def reference(gen: dict, *, release_seed: str, case_id: str) -> str:
    return _files_answer(_build(gen, release_seed, case_id)["reference_files"])


def mutants(gen: dict, *, release_seed: str, case_id: str) -> list[str]:
    return [_files_answer(m) for m in _build(gen, release_seed, case_id)["mutants"].values()]


GRADE_IS_ENGINE = True


def grade(answer: str, materialized: dict) -> float:
    from .. import fixcheck
    v = fixcheck.grade_answer(answer, materialized["grader_config"], case_id=materialized.get("id"))
    return float(v.get("score") or 0.0)


def pack_stamp() -> str:
    """The pack's own stamp (12 hex): the fix-checker stamp it grades with + this module + patchmod.py (project, prompt,
    reference). A row of this pack is stale when it changes; the standard fix rows' stamp is not affected by it."""
    import hashlib
    from pathlib import Path

    from .. import fixrows45
    h = hashlib.sha256(fixrows45.checker_stamp().encode() + b"\0")
    for p in (Path(__file__), Path(pm.__file__)):
        h.update(p.name.encode() + b"\0" + p.read_bytes() + b"\0")
    return h.hexdigest()[:12]
