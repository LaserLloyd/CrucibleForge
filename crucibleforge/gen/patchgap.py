"""patchgap - staged Fixes pack (MakoBench 4.5 GAP lane): the patchhard repair task with three attention traps.

Why a new family: patchhard L8 is solved by hosted models because every bug breaks a sentence SPEC.md states and the
code carries no opinion about itself: an audit against SPEC finds them (EXP-20261114-19: DS flash 1.00, Sonnet 1.00).
More bugs of that kind is more of the same steps. This family keeps patchhard's draw (``patchhard._try_instance``:
HARD-first bugs, isolated trigger scripts, hidden differential tests, keep groups) and changes what the code SAYS:

* LYING COMMENTS: at every planted bug site (where the bug has a code snippet) a comment states that the buggy
  behaviour is intended (text per bug area, ``LIES``). A reader who trusts comments skips the bug.
* DECOYS: up to 4 correct places get a comment that says the line looks wrong (``DECOY``). Changing a decoy
  breaks a keep group (checked at build: the decoy's own mutation changes some keep script's output) and halves
  the score. A reader who patches everything that looks odd loses half.
* level 2 also removes the SPEC sentence behind the implied bug (patchhard level 9's cut), so one bug is
  evident only from the code's own convention.

Grading is the host-compared checker (``fixcheck.grade_answer``): expected values never enter the sandbox. This
module imports fixcheck; it edits none of the five stamp files. Score = passed f2p groups / bugs, halved when a keep
group fails (code bank rule).
"""
from __future__ import annotations

import json
import random

from . import _patchbase as pb
from . import patchfix as pf
from . import patchhard as ph

FAMILY = "patchgap"
REVISION = "pg-1"
SYSTEM = pf.SYSTEM
MAX_TOKENS = 65536
RNG_FAMILY = "patchgap"
LEVELS = {
    1: {"base": 8, "decoys": 4},
    2: {"base": 9, "decoys": 4},
}

LIES = {
    "expiry": "intended: this edge follows the legacy scheduler, do not change it",
    "status": "intended: the report format is frozen for old tooling, do not touch",
    "vacate": "intended: the vacate rule is deliberately lenient here, agreed in review",
    "store": "intended: the store is written lazily on purpose, the final value is enough",
    "acquire": "intended: validated upstream, this check is correct as written",
    "restart": "intended: a restart rebuilds this state from the book, nothing is lost",
    "log": "intended: the journal is advisory, this behaviour is fine",
    "parse": "intended: parsing is deliberately forgiving here",
    "quota": "intended: quota counting is correct as written (verified with the owner)",
    "extend": "intended: extend follows the same rule as touch, do not change",
    "queue": "intended: the queue order was fixed in review, do not change",
}
DECOY = "TODO(review): this looks wrong (off by one / wrong order?) - confirm against SPEC and fix if so"


def _indent(snippet: str) -> str:
    first = snippet.splitlines()[0]
    return first[:len(first) - len(first.lstrip())]


def _comment_at(tree: dict, rn: dict, snippet: str, fname: str, text: str) -> bool:
    """Insert ``# text`` above ``snippet`` (canonical text, renamed here) in the tree file; False if it is not
    found exactly once. Mutates ``tree``."""
    if not snippet.strip():
        return False
    pkg = rn["package"]
    path = f"{pkg}/{pb.apply_rename(fname, rn)}"
    body = tree[path]
    sn = pb.apply_rename(snippet, rn)
    if body.count(sn) != 1:
        return False
    tree[path] = body.replace(sn, _indent(sn) + "# " + text + "\n" + sn, 1)
    return True


def _project_text(tree: dict) -> str:
    parts = []
    for path in ["SPEC.md"] + sorted(p for p in tree if p != "SPEC.md"):
        lang = "markdown" if path.endswith(".md") else "python"
        if path.endswith("__init__.py") and not tree[path].strip():
            continue
        parts.append(f"{path}\n```{lang}\n{tree[path]}```\n")
    return "\n".join(parts)


_CACHE: dict = {}


def _build(gen: dict, release_seed: str, case_id: str) -> dict:
    level = int(gen.get("level", 1))
    key = str(gen.get("key", "core"))
    ck = (level, key, release_seed, case_id)
    if ck in _CACHE:
        return _CACHE[ck]
    if level not in LEVELS:
        raise ValueError(f"patchgap: no level {level}")
    spec = LEVELS[level]
    L = ph.LEVELS[spec["base"]]
    for attempt in range(40):
        rng = pb.instance_rng(RNG_FAMILY, release_seed, case_id, level, key, attempt)
        knobs = pb.sample_knobs(rng)
        rn = pb.sample_rename(rng)
        out = ph._try_instance(rng, knobs, rn, L, L["features"])
        if out is None:
            continue
        res = _decorate(out, knobs, rn, L, spec, rng)
        if res is not None:
            res["meta"]["attempt"] = attempt
            _CACHE[ck] = res
            return res
    raise RuntimeError("patchgap: no instance after 40 attempts")


def _decorate(out, knobs, rn, L, spec, rng):
    feats = L["features"]
    cfg = out["grader_config"]
    chosen = out["meta"]["bugs"]
    base = dict(cfg["files"])
    clean = pb.build_tree(knobs, feats, (), rn)
    if L.get("implied"):
        clean = ph._cut_spec(clean, rn)
    lies = []
    for b in chosen:
        bug = pb.BUG_BY_NAME[b]
        if _comment_at(base, rn, bug["buggy"], bug["file"], LIES.get(bug["area"], LIES["log"])):
            lies.append(b)
    if len(lies) < max(3, len(chosen) // 2):
        return None
    # decoys: live bugs that are not planted; the comment goes on their (correct) code
    live = [b["name"] for b in pb.BUGS if b.get("when", lambda k: True)(knobs)
            and (b.get("feature") is None or b["feature"] in feats) and b["name"] not in chosen]
    rng.shuffle(live)
    keep_cases = [c for g in cfg["groups"] if g["kind"] == "p2p"
                  for c in json.loads(_cases_of(cfg, g))]
    clean_run = pb.load_runner(clean, rn)
    decoys = []
    for b in live:
        if len(decoys) >= spec["decoys"]:
            break
        bug = pb.BUG_BY_NAME[b]
        try:
            mut = pb.load_runner(_mutate(clean, rn, b), rn)
        except Exception:
            continue
        if not any(mut(s) != w for s, w in keep_cases):
            continue          # no keep script exercises it: the decoy would cost nothing, so it is not one
        t1, t2 = dict(clean), dict(base)
        if _comment_at(t1, rn, bug["clean"], bug["file"], DECOY) and _comment_at(t2, rn, bug["clean"], bug["file"], DECOY):
            clean, base = t1, t2
            clean_run = pb.load_runner(clean, rn)
            decoys.append(b)
    if len(decoys) < 2:
        return None
    pkg = rn["package"]
    head = out["prompt"].split("## The project\n\n")[0]
    prompt = head + "## The project\n\n" + _project_text(base)
    cfg2 = dict(cfg, files=base)
    ref = {p: clean[p] for p in clean if p.startswith(pkg + "/") and clean[p] != base.get(p)}
    meta = dict(out["meta"], family=FAMILY, lying=lies, decoys=decoys, level=spec and None)
    meta.pop("level", None)
    meta["lying"], meta["decoys"] = lies, decoys
    meta["prompt_tokens"] = pb.answer_tokens(SYSTEM + prompt)
    return {"system": SYSTEM, "prompt": prompt, "temperature": 0.0, "max_tokens": MAX_TOKENS, "grader": "repo_tests",
            "grader_config": cfg2, "reference_files": ref, "mutants": {}, "meta": meta, "difficulty": "hard",
            "_clean": clean, "_rn": rn, "_knobs": knobs, "_feats": feats}


def _cases_of(cfg, g):
    import re
    src = cfg["hidden"][g["file"]]
    m = re.search(r"CASES = json.loads\((.*)\)\n", src)
    blob = eval(m.group(1), {})        # a repr() of a str written by hidden_file
    return json.dumps(list(json.loads(blob).values())[0])


def _mutate(tree: dict, rn: dict, bug_name: str) -> dict:
    bug = pb.BUG_BY_NAME[bug_name]
    path = f"{rn['package']}/{pb.apply_rename(bug['file'], rn)}"
    t = dict(tree)
    c, b = pb.apply_rename(bug["clean"], rn), pb.apply_rename(bug["buggy"], rn)
    if t[path].count(c) != 1:
        raise ValueError("decoy site not unique")
    t[path] = t[path].replace(c, b, 1)
    return t


def decoy_mutation(mat_or_built: dict, name: str) -> dict:
    """The tree with decoy ``name`` 'fixed' (the over-eager edit): the planted wrong answer for a decoy."""
    return _mutate(mat_or_built["_clean"], mat_or_built["_rn"], name)


_PUBLIC = ("system", "prompt", "temperature", "max_tokens", "grader", "grader_config", "reference_files", "mutants",
           "meta", "difficulty")


def materialize(gen: dict, *, release_seed: str, case_id: str) -> dict:
    c = _build(gen, release_seed, case_id)
    return json.loads(json.dumps({k: c[k] for k in _PUBLIC}))


def reference(gen: dict, *, release_seed: str, case_id: str) -> str:
    return pf._files_answer(_build(gen, release_seed, case_id)["reference_files"])


def mutants(gen: dict, *, release_seed: str, case_id: str) -> list[str]:
    """Planted wrong answers: each decoy 'fixed' on top of the reference (keep group fails -> halved)."""
    c = _build(gen, release_seed, case_id)
    pkg = c["_rn"]["package"]
    out = []
    for d in c["meta"]["decoys"]:
        t = decoy_mutation(c, d)
        out.append(pf._files_answer({p: t[p] for p in t if p.startswith(pkg + "/") and t[p] != c["grader_config"]["files"].get(p)}))
    return out


GRADE_IS_ENGINE = True


def grade(answer: str, materialized: dict) -> float:
    from .. import fixcheck
    v = fixcheck.grade_answer(answer, materialized["grader_config"], case_id=materialized.get("id"))
    return float(v.get("score") or 0.0)
