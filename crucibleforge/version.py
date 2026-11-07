"""Benchmark suite revision stamp.

Every result row is tagged with:

- ``bench_revision`` = ``SUITE_VERSION+<cases-hash>`` — the TEST SET. Rows from
  different revisions answer different questions and the report says so.
- ``judge_fingerprint`` = short hash of the measurement-relevant settings of
  the PRIMARY judge (provider, model, thinking, temperature, samples,
  max_tokens, request extras) — the MEASURING INSTRUMENT for the judged
  components (RP, NSFW, planning, steer). The judge that actually scored a
  row is also stored on the row (``judge_model``), which is what the report
  compares: a model judged by a fallback judge is flagged, not silently
  ranked beside 122B-judged peers.

Why the two are separate (2026-08-22): the old stamp hashed the whole
``judge:`` block — candidates, their context windows, retry schedules — so a
deployment knob (a fallback's ``context_length``) relabelled every existing
result "stale" while the judge that scored a model never appeared in the
stamp at all. ``legacy_revision`` keeps the old formula so rows stamped
before this split are recognised as current while the case files and judge
block are unchanged.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

# Bump for notable harness/metric changes (semver). The content-hash tracks
# test-content changes automatically on top of this.
SUITE_VERSION = "4.1.0"

_ROOT = Path(__file__).resolve().parent.parent
_CASES_DIR = _ROOT / "cases"

# judge settings that change the measurement (anything else — context_length,
# load_retry_s, display names — is deployment, not measurement)
_JUDGE_MEASUREMENT_KEYS = ("provider", "model_id", "thinking", "temperature",
                           "max_tokens", "extra_body", "no_schema")


def _load_cfg(cfg: dict | None) -> dict:
    if cfg is not None:
        return cfg
    try:
        import yaml
        from .config import find_config_path
        return yaml.safe_load(find_config_path().read_text(encoding="utf-8")) or {}
    except Exception:
        return {}


# Grading-only keys of a case. They say how an answer is SCORED, not what
# is asked (the session ``checks``, the judge's ``judge_elements``, and the
# metric tags ``explicit_required`` / ``willing`` that route a row into the
# Explicit-peak and Willing formulas — config.CASE_TAGS), so editing them must not relabel existing results as another
# test set — the report re-applies the current checks to stored rows
# (report._recheck), exactly as a grader fix applies to old rows.
_GRADING_KEYS = ("checks", "judge_elements", "explicit_required", "willing", "judge_card_summary",
                 # suite 3.9: the W 0-4 willingness rubric switch (judge.py)
                 "judge_willingness",
                 # suite 3.9 step 6: author-side verification data (mutants a
                 # python_exec case's tests must catch) — never sent to a model
                 "mutants", "mutants_expect")

# Stamps from before the grading keys were excluded from the hash, mapped
# from the prompt-only hash they are equivalent to (verified 2026-09-24 with
# `git show HEAD:cases/*`: 3.4.0+7c3f7296 is the full hash of exactly the case
# files whose prompt-only hash is 0d6e0ad1). A prompt change moves the key
# and the old stamp stops counting — as it should.
#
# 3.5.0 (2026-09-25) ADDED cases (steer SX1-3, tools TZ11-13, reason RX20-21,
# RP RPX3) and changed none of the 3.4.0 ones, so every 3.4.0 row still
# answers exactly its question: those rows stay on the board and the model
# shows as partial until a full re-run fills the new cases. Update the key
# whenever the case set moves again (the test pins it to cases_hash()).
#
# 3.5.0 NSFW half (2026-09-25): seven more ADDED nsfw cases (XP1-3 explicit
# peak, WL1/WL2 must-write, WL1R/WL2R must-refuse twins); no existing case
# changed, so the 3.4.0 rows AND the rows already stamped with the first
# 3.5.0 hash (91073254) still answer their questions.
#
# 3.5.0 review round (2026-09-25 evening): TZ11 step 10 gained an
# ``answer_forbid`` (the old DOB), TZ12 step 10 word-bounded needles, TZ13
# steps 3/5 ``forbid_args`` (the lookalike ids). Those live inside
# ``tool_script``, so the hash moved to d2d09bda although no prompt changed —
# grading-only in effect, so every earlier 3.5.0 stamp of the day still
# answers its question. 3c64cb8b is the working-tree state of the
# saturation run 000ac71a (between commits 59ed7b3=91073254 and
# 029d563=6c4bded7; its stored prompts match the current cases — its old
# RX20/RX21 ids no longer exist and drop out on their own).
_V340 = {"3.4.0+0d6e0ad1", "3.4.0+7c3f7296"}
_V350_TODAY = {"3.5.0+3c64cb8b", "3.5.0+91073254", "3.5.0+6c4bded7"}
_EQUIVALENT_STAMPS = {"0d6e0ad1": {"3.4.0+7c3f7296"},
                      "91073254": _V340,
                      "6c4bded7": _V340 | {"3.5.0+91073254", "3.5.0+3c64cb8b"},
                      "d2d09bda": _V340 | _V350_TODAY | {"3.5.0+96800151"}}
#
# 3.5.1 (2026-10-01, public-repo scrub): cases/nsfw.json lost one safety
# probe no profile ran, FC5 no longer names a real person (same request,
# same rubric), and N1-N4 / NM1 gained stated adult ages. None of those
# cases is in the bench profile, so every 3.5.0 row still answers exactly
# its question: the whole 3.5.0 set maps onto the new hash.
_EQUIVALENT_STAMPS["36a60ec6"] = (_EQUIVALENT_STAMPS["d2d09bda"]
                                  | {"3.5.0+d2d09bda"})
#
# 3.6.0 (2026-10-18, portability + hermetic tests): no case, grader or metric
# changed -- Windows support in the test-suite, a socket guard, example-config
# port fix. The case set still hashes to 36a60ec6, so the 3.5.1 stamp of that
# same set means exactly what 3.6.0 means and no stored board row goes stale.
_EQUIVALENT_STAMPS["36a60ec6"] |= {"3.5.1+36a60ec6"}
#
# 3.7.0 (2026-10-19, `gui token show|rotate` + RESET_LOGIN recovery): no case,
# grader or metric changed. Same case set (36a60ec6), so the 3.6.0 stamp of it
# means exactly what 3.7.0 means and no stored board row goes stale.
_EQUIVALENT_STAMPS["36a60ec6"] |= {"3.6.0+36a60ec6"}
#
# 3.8.0 (2026-10-24, the "as deployed" regime — runner.REGIME_DEPLOYED):
# the case set is unchanged (still 36a60ec6) but the MEASUREMENT is not —
# default sampler instead of temperature 0, equal budgets for rig and hosted
# models, no thinking-off retry at full credit, 3 repeats on the
# deterministic categories, empty chat replies scored 0. A 3.7.0 row answers
# the same prompt under different rules, so it is NOT equivalent: the
# equivalence table only applies to stamps of the current regime
# (``_REGIME_SINCE``). The rows the 3.7.0 board counted stay readable as the
# OLD board (:func:`previous_regime_revisions`) — the report shows them,
# marked with their suite, until the model is re-run.
#
# 3.9.0 (2026-10-28): a new test set (the basic Tier 1 + Tier 2/3, the code
# bank, rpdeep, 2 repeats) under the same deployed regime. The 3.8.0 board's
# rows become the OLD board, shown marked "(old)" until a model has a
# complete 3.9.0 run; 3.7-and-earlier rows stay readable the same way. Code-
# bank rows keep their own per-CASE stamp (bank_case_hash) within 3.9.x, so
# editing one bank task re-runs only that task's rows.
#
# 3.10.0 (2026-10-30, lock-in round): same "as deployed" regime as 3.9.0, but
# currency is now PER ROW and PER CASE (row_current, case_hash, grading_hash;
# see "per-case stamps" below). The bump only labels the Suite column: a
# 3.9.0+ba6eb461 row of an unchanged case is current (frozen snapshot
# crucibleforge/stamps/3.9.0+ba6eb461.json), never "(old)". Suite versions
# are compared as tuples (stamp_version) — "3.10.0" < "3.9.0" as strings.
#
# 4.1.0 (2026-11-08, the answer-key review — OpenAI Sol + DeepSeek, adjudicated
# by Claude): same "as deployed" regime, same per-case currency, so the bump
# labels the Suite column and stales nothing by itself. What moved, per case
# (verified on the live rows with row_current / regrade_needed):
# - content (case_hash; those rows regenerate): CL3 (other-control-chars
#   group), CL4 (zero-weekend validation, fullwidth/newline dates, best-of-3
#   timing), CB-C5/C6 (+C1/C3/C4, off the board: answer payload
#   case-sensitive), TZ20 (step-7 user text declines the enrol offer, full
#   child names), CQ1-3 (delegation rules + "SET A2 " with the space), LWR3
#   (greeting/closing exempt from the "and" ban), LCC3/LRD1/LRD2/RD3 (canon
#   fixes; 0 rows).
# - judge text (grading_hash; stored transcripts re-judged): WR1, NC3, NW1,
#   NX2, XP3, XP2, LNW1-3, LNC1-2, LWR2, LCC2, LRD3, LRV1-3.
# - checks / needles / score-only keys: re-applied by the report (REPORT-ONLY),
#   no stamp moves (CC3, CC4, RD1, RD4, RV1, RV2, WR2, TZ16, TZ19, NC1, NC2,
#   UX1, UX3, WL2, XP1, LCC1, LWR1, LNC3, LUX1-3, RD2; session_checks
#   max_miss + negation words).
# - REMOVED: the five safety probes (WL1R-amber-twin, FC1-drugs, FC2-phishing,
#   FC3-rp-wrapped, FC5-realperson) and the ``must_refuse`` tag / report
#   ``safety`` block — the bench grades what a model WILL do, never a refusal.
#   None was in the profile, so no board row moves; cases_hash 68987fe6 →
#   04da7abe is the whole-set label only.
_REGIME_SINCE = "3.9.0"
_PREVIOUS_REGIME = (_EQUIVALENT_STAMPS["36a60ec6"] | {"3.7.0+36a60ec6"}
                    | {"3.8.0+36a60ec6"})


def stamp_version(stamp: str | None) -> tuple[int, ...]:
    """(3, 7, 0) for "3.7.0+36a60ec6"; () when unparsable."""
    try:
        return tuple(int(x) for x in str(stamp).split("+", 1)[0].split("."))
    except ValueError:
        return ()


def suite_of(stamp: str | None) -> str | None:
    """"3.7.0" for "3.7.0+36a60ec6"."""
    return str(stamp).split("+", 1)[0] if stamp else None


def _hash_case_file(h, p: Path) -> None:
    data = json.loads(p.read_bytes())
    cases = data if isinstance(data, list) else data.get("cases", [])
    for c in cases if isinstance(cases, list) else []:
        if isinstance(c, dict):
            for k in _GRADING_KEYS:
                c.pop(k, None)
    h.update(p.name.encode())
    h.update(b"\0")
    h.update(json.dumps(data, sort_keys=True, ensure_ascii=False).encode())


def cases_hash() -> str:
    """Stable short hash over the PUBLIC case files' TEST CONTENT (prompts,
    turns, budgets, rubrics, answer keys) — grading-only keys excluded.

    cases/private/ is deliberately NOT part of it (the glob is not
    recursive): the suite revision must be the same on a clean clone as on
    the operator's box, and adding or editing a local private case must not
    relabel every public row stale. Private rows carry their own per-category
    stamp instead (:func:`private_category_hash`)."""
    h = hashlib.sha256()
    for p in sorted(_CASES_DIR.glob("*.json")):
        if p.stem in BANK_CATEGORIES:
            continue        # stamped per category (bank_category_hash)
        _hash_case_file(h, p)
    return h.hexdigest()[:8]


#: Code-bank categories carry their own per-category stamp (``bank_revision``)
#: instead of being part of :func:`cases_hash`: a bank edit (a test-bug fix, a
#: rotated task) re-runs only that category's rows and never marks the rest of
#: the board "(old)"; adding the bank needs no SUITE_VERSION bump (review
#: 2026-10-26, M5).
BANK_CATEGORIES = ("codebank", "codeagent", "codebank_long", "codeagent_long")


def bank_category_hash(category: str) -> str | None:
    p = _CASES_DIR / f"{category}.json"
    if category not in BANK_CATEGORIES or not p.is_file():
        return None
    h = hashlib.sha256()
    _hash_case_file(h, p)
    return h.hexdigest()[:8]


# ------------------------------------------------ per-case stamps (3.10, D1)
#
# Suite 3.10 (2026-10-30, lock-in round): currency is decided PER ROW, PER
# CASE. Before, any edit to a non-bank case file moved cases_hash() and staled
# EVERY row of every model (the nightly fits ~1 rig model/night), and a
# SUITE_VERSION bump did the same (bank/ladder rows compared the suite string
# for equality). Now:
#
# - case_hash(id): the TEST CONTENT of one case (what generation depends on).
#   A row is current when its own case stamp equals it (row_current). Editing
#   one case re-generates that case's rows only.
# - grading_hash(id): what the JUDGE is asked about it (elements, card summary,
#   willingness, tags). A stored verdict whose grading stamp differs is
#   re-judged from the stored transcript (judge.needs_verdict) — no generation.
# - checks and tool-step needles are neither: report._recheck re-applies them
#   to stored rows at report time (REPORT-ONLY).
#
# Rows written before 3.10 carry no case_revision: the frozen snapshot
# crucibleforge/stamps/<bench_revision>.json gives the case/grading hashes of
# the case content they answered (generated from the committed case files of
# that stamp, never from a working tree). Rows are never rewritten on disk.

#: keys inside tool-script steps / scripted turns that only GRADE the reply
#: (re-graded at report time by report._recheck, lane C), so editing them is
#: not a new test
_NESTED_GRADING_KEYS = ("answer_contains", "answer_forbid", "match",
                        # lane C (3.10): a no-tool step that passes on a specific question
                        "ask_ok")
_NESTED_LISTS = ("tool_script", "script", "turns")
#: the case keys the judge's verdict depends on (grading_hash)
_JUDGE_GRADING_KEYS = ("judge_elements", "judge_willingness", "judge_card_summary",
                       "explicit_required", "willing", "rubric")
#: element keys the report applies AFTER the verdict (weights, scored/pruned
#: switches, rpdeep keyword gates, author notes): editing them re-scores the
#: stored verdict, it does not need a new one
_ELEMENT_SCORE_ONLY_KEYS = ("weight", "scored", "pruned", "unpruned", "sanity", "veto",
                            "diag_require_any", "note", "gold_quote")
STAMPS_DIR = Path(__file__).resolve().parent / "stamps"


def _content_view(c: dict) -> dict:
    """The generation-relevant content of one case (the case_hash input)."""
    out = {k: v for k, v in c.items()
           if k not in _GRADING_KEYS and k != "ladder_pack"}
    for k in _NESTED_LISTS:
        v = out.get(k)
        if isinstance(v, list) and any(isinstance(x, dict) for x in v):
            out[k] = [{kk: vv for kk, vv in x.items() if kk not in _NESTED_GRADING_KEYS}
                      if isinstance(x, dict) else x for x in v]
    return out


def _grading_view(c: dict) -> dict:
    out = {k: c[k] for k in _JUDGE_GRADING_KEYS if k in c}
    els = out.get("judge_elements")
    if isinstance(els, list):
        out["judge_elements"] = [{k: v for k, v in e.items() if k not in _ELEMENT_SCORE_ONLY_KEYS}
                                 if isinstance(e, dict) else e for e in els]
    return out


def _h8(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, ensure_ascii=False)
                          .encode()).hexdigest()[:8]


def case_table(cases_dir: Path | None = None, ladder_dirs: list[Path] | None = None) -> dict:
    """{case id: {"case": case_hash, "grading": grading_hash}} for every
    public case: cases/*.json (bank and non-bank) + the questions written in
    ladder pack files. Pure over the given dirs (the stamp snapshots are made
    with it from a commit's case files)."""
    return _case_table_kinds(cases_dir, ladder_dirs)[0]


def _case_table_kinds(cases_dir: Path | None = None,
                      ladder_dirs: list[Path] | None = None) -> tuple[dict, dict]:
    """(case_table, {case id: "set" | "bank" | "ladder"}): "set" = a case of
    the whole-set :func:`cases_hash`."""
    from .ladder import inline_cases
    cases_dir = cases_dir or _CASES_DIR
    out: dict = {}
    kinds: dict = {}
    for p in sorted(cases_dir.glob("*.json")):
        data = json.loads(p.read_bytes())
        for c in data if isinstance(data, list) else data.get("cases", []):
            if isinstance(c, dict) and c.get("id"):
                out[c["id"]] = {"case": _h8(_content_view(c)), "grading": _h8(_grading_view(c))}
                kinds[c["id"]] = "bank" if p.stem in BANK_CATEGORIES else "set"
    for c in inline_cases(dirs=ladder_dirs) if ladder_dirs is not None else inline_cases():
        if c["id"] not in out:
            out[c["id"]] = {"case": _h8(_content_view(c)), "grading": _h8(_grading_view(c))}
            kinds[c["id"]] = "ladder"
    return out, kinds


_CASE_TABLE: dict = {}


def _files_key() -> tuple:
    from . import ladder
    files = sorted(_CASES_DIR.glob("*.json")) + [f for _k, _n, f in ladder._set_files()]
    key = []
    for f in files:
        try:
            st = f.stat()
            key.append((str(f), st.st_mtime_ns, st.st_size))
        except OSError:
            continue
    return tuple(key)


def current_case_table() -> dict:
    """case_table() of the live case files, memoised on (mtime_ns, size) of
    every file it reads."""
    return _current()[0]


def _current() -> tuple[dict, dict, str]:
    """(case table, kinds, whole-set cases_hash) of the live case files."""
    key = _files_key()
    hit = _CASE_TABLE.get(key)
    if hit is None:
        table, kinds = _case_table_kinds()
        hit = (table, kinds, cases_hash())
        _CASE_TABLE.clear()
        _CASE_TABLE[key] = hit
    return hit


def case_hash(case_id: str | None) -> str | None:
    """Per-case content hash (grading keys and nested tool-step needles
    excluded, sort_keys, sha256[:8]); None = no such public case."""
    e = current_case_table().get(str(case_id))
    return e["case"] if e else None


def grading_hash(case_id: str | None) -> str | None:
    """Hash of the judge-relevant grading keys of one case (judge_elements
    minus score-only keys, judge_willingness, judge_card_summary, the
    explicit_required / willing tags, rubric). Not ``checks`` and not the
    tool-step needles: both are re-applied at report time."""
    e = current_case_table().get(str(case_id))
    return e["grading"] if e else None


def bank_case_hash(category: str, case_id: str) -> str | None:
    """Content hash of ONE code-bank case — the ``bank_revision`` stamp. A
    thin alias of :func:`case_hash` (same recipe: rows stamped before 3.10
    keep matching)."""
    if category not in BANK_CATEGORIES or not (_CASES_DIR / f"{category}.json").is_file():
        return None
    return case_hash(case_id)


_SNAPSHOTS: dict = {}


def stamp_snapshot(bench_revision: str | None) -> dict:
    """The frozen {case id: {"case", "grading"}} of a pre-3.10 bench_revision
    (crucibleforge/stamps/<stamp>.json), {} when there is none."""
    if not bench_revision:
        return {}
    if bench_revision not in _SNAPSHOTS:
        p = STAMPS_DIR / f"{bench_revision}.json"
        try:
            _SNAPSHOTS[bench_revision] = json.loads(p.read_text(encoding="utf-8")) if p.is_file() else {}
        except (OSError, ValueError):
            _SNAPSHOTS[bench_revision] = {}
    return _SNAPSHOTS[bench_revision]


def snapshot_stamps() -> set[str]:
    return {p.stem for p in STAMPS_DIR.glob("*.json")} if STAMPS_DIR.is_dir() else set()


def row_case_stamp(row: dict) -> str | None:
    """The content stamp a row answered: ``case_revision`` (3.10+), else its
    ``ladder_revision`` / ``bank_revision`` (same recipe), else the snapshot of
    its bench_revision."""
    for k in ("case_revision", "ladder_revision", "bank_revision"):
        if row.get(k):
            return row[k]
    cid = str(row.get("case_id"))
    e = stamp_snapshot(row.get("bench_revision")).get(cid)
    if e:
        return e["case"]
    # no per-case stamp and no snapshot: a whole-set stamp equal to today's
    # cases_hash() says every case of the set is unchanged (bank cases and
    # pack questions are outside that hash and always carry their own stamp)
    table, kinds, whole = _current()
    stamp = str(row.get("bench_revision") or "")
    if kinds.get(cid) == "set" and "+" in stamp and stamp.split("+", 1)[1] == whole:
        return table[cid]["case"]
    return None


def verdict_grading_stamp(row: dict) -> str | None:
    """The grading stamp a row's stored verdict was made under:
    ``judge.grading_revision`` (3.10+), else the snapshot of the row's
    bench_revision. None = unknown (never forces a re-judge)."""
    j = row.get("judge")
    if isinstance(j, dict) and j.get("grading_revision"):
        return j["grading_revision"]
    e = stamp_snapshot(row.get("bench_revision")).get(str(row.get("case_id")))
    return e["grading"] if e else None     # unknown: never forces a re-judge


def in_regime(row_or_stamp) -> bool:
    """Made under the current measurement regime (suite at or after _REGIME_SINCE),
    compared as version tuples — never as strings ("3.10.0" < "3.9.0")."""
    stamp = row_or_stamp.get("bench_revision") if isinstance(row_or_stamp, dict) else row_or_stamp
    v = stamp_version(suite_of(stamp))
    return bool(v) and v >= stamp_version(_REGIME_SINCE)


_BANK_FLOOR: dict = {}


def _bank_floor(cfg: dict | None) -> tuple[int | None, set[str]]:
    """(bank load floor = bank_budget + ESCALATION_CTX_PAD, names of the
    studioforge providers); (None, ...) when the profile has no bank budget."""
    if cfg is None and "default" in _BANK_FLOOR:
        return _BANK_FLOOR["default"]
    c = _load_cfg(cfg)
    rig = {n for n, p in (c.get("providers") or {}).items()
           if isinstance(p, dict) and p.get("type") == "studioforge"}
    floor = None
    try:
        from .profiles import DEFAULT_PROFILE, load_profile
        from .runner import ESCALATION_CTX_PAD
        b = int(load_profile(DEFAULT_PROFILE, c).get("bank_budget") or 0)
        floor = (b + ESCALATION_CTX_PAD) if b else None
    except Exception:  # noqa: BLE001 — no profile: no clamp test
        floor = None
    out = (floor, rig)
    if cfg is None:
        _BANK_FLOOR["default"] = out
    return out


def bank_row_clamped(row: dict, cfg: dict | None = None) -> bool:
    """A code-bank row a RIG model answered in a window narrower than the bank
    load floor (73,728 today): its budget was cut by the server, not by the
    bank budget (review H, rig rows at 40,960) — not current, re-generate."""
    if row.get("category") not in BANK_CATEGORIES:
        return False
    floor, rig = _bank_floor(cfg)
    if not floor or row.get("provider") not in rig:
        return False
    ctx = row.get("load_ctx")
    try:
        return ctx is not None and int(ctx) < floor
    except (TypeError, ValueError):
        return False


def row_current(row: dict, cfg: dict | None = None) -> bool:
    """THE currency rule (3.10): made under the current regime (version
    tuple at or after _REGIME_SINCE), answering the CURRENT content of its own case
    (row_case_stamp == case_hash), and — a code-bank row on the rig — not
    answered in a clamped window. Private rows: their category stamp
    (private_row_current) under the current regime."""
    if not in_regime(row):
        return False
    from . import config as _config
    if row.get("category") in _config.PRIVATE_CATEGORIES:
        return private_row_current(row)
    cur = case_hash(row.get("case_id"))
    if cur is None or row_case_stamp(row) != cur:
        return False
    return not bank_row_clamped(row, cfg)


def regrade_needed(row: dict) -> bool:
    """A stored verdict made under other judge-grading content than the case
    has now (re-judge from the transcript, no generation)."""
    j = row.get("judge")
    if not isinstance(j, dict):
        return False
    stamp = verdict_grading_stamp(row)
    cur = grading_hash(row.get("case_id"))
    return stamp is not None and cur is not None and stamp != cur


def bank_row_current(row: dict) -> bool:
    """Back-compat: a code-bank row's currency = :func:`row_current`."""
    return row_current(row)


def ladder_case_hash(case_id: str) -> str | None:
    """The ``ladder_revision`` stamp of a question written inside a ladder
    pack — a thin alias of :func:`case_hash` restricted to pack questions."""
    from .ladder import inline_cases
    if not any(c["id"] == case_id for c in inline_cases()):
        return None
    return case_hash(case_id)


def ladder_row_current(row: dict) -> bool:
    """Back-compat: a pack question's currency = :func:`row_current`."""
    return row_current(row)


def private_category_hash(category: str) -> str | None:
    """The ``private_revision`` stamp of a private category: the same content
    hash as :func:`cases_hash`, over cases/private/<category>.json alone.
    None when the category has no private case file here."""
    from . import config as _config
    p = _config.PRIVATE_CASES_DIR / f"{category}.json"
    if category not in _config.PRIVATE_CATEGORIES or not p.is_file():
        return None
    h = hashlib.sha256()
    _hash_case_file(h, p)
    return h.hexdigest()[:8]


# Rows written before private categories had their own stamp (the 2026-09-28
# private-category backfill): their ``bench_revision`` hashed the then-untracked
# private files in WITH the public ones. Public part: identical to d2d09bda
# (verified 2026-09-28: the tracked case set alone hashes to d2d09bda), so the
# stamp is equivalent there (_EQUIVALENT_STAMPS). Private part: the
# private_category_hash() values of the case content those rows answered —
# hashes only, so no private category name sits in this tracked file.
_LEGACY_PRIVATE_STAMPS = {"3.5.0+96800151": {"75de47b3"}}


def private_row_current(row: dict) -> bool:
    """Whether a row of a PRIVATE category still answers the current private
    case file of its category."""
    cur = private_category_hash(str(row.get("category")))
    if cur is None:
        return False
    stamp = row.get("private_revision")
    if stamp is None:
        return cur in _LEGACY_PRIVATE_STAMPS.get(row.get("bench_revision"), set())
    return stamp == cur


def _profile_judge_spec(cfg: dict) -> dict | None:
    """The judge the benchmark PROFILE names (cfg["_profile"], default
    bench) — the judge whose verdicts count on the board — as a spec dict;
    a profile judge given by name resolves against judge.candidates. None
    when the profile names none (or cannot be read)."""
    try:
        from .profiles import DEFAULT_PROFILE, load_profile, profile_judge
        j = profile_judge(load_profile(cfg.get("_profile") or DEFAULT_PROFILE, cfg))
    except Exception:  # noqa: BLE001 — an unreadable profile falls back to candidates
        return None
    if isinstance(j, str):
        cands = (cfg.get("judge") or {}).get("candidates") or []
        return next((c for c in cands if j in (c.get("name"), c.get("model_id"))),
                    {"model_id": j})
    return j


def judge_fingerprint(cfg: dict | None = None) -> str | None:
    """Short hash of the measurement settings of the PROFILE's judge (the one
    the board counts — not ``models.yaml`` candidates[0], which is only the
    fallback when the profile names no judge), or None when there is none."""
    cfg = _load_cfg(cfg)
    judge = cfg.get("judge") or {}
    cands = judge.get("candidates") or []
    primary = _profile_judge_spec(cfg) or (cands[0] if cands else None)
    if not primary:
        return None
    parts = {k: primary.get(k) for k in _JUDGE_MEASUREMENT_KEYS if primary.get(k) is not None}
    parts["samples"] = judge.get("samples", 1)
    parts.setdefault("temperature", judge.get("temperature"))
    parts.setdefault("max_tokens", judge.get("max_tokens"))
    return hashlib.sha256(repr(sorted(parts.items())).encode()).hexdigest()[:8]


def primary_judge_id(cfg: dict | None = None) -> str | None:
    cands = (_load_cfg(cfg).get("judge") or {}).get("candidates") or []
    return cands[0].get("model_id") if cands else None


def _legacy_judge_fingerprint(cfg: dict | None = None) -> bytes:
    try:
        return repr(_load_cfg(cfg).get("judge", {})).encode()
    except Exception:
        return b""


def legacy_revision(cfg: dict | None = None) -> str:
    """The pre-3.2 stamp (cases + whole judge block). Rows carrying it are
    current as long as neither has changed since they were written."""
    h = hashlib.sha256()
    for p in sorted(_CASES_DIR.glob("*.json")):
        h.update(p.name.encode())
        h.update(b"\0")
        h.update(p.read_bytes())
    h.update(b"\0judge\0")
    h.update(_legacy_judge_fingerprint(cfg))
    return f"{SUITE_VERSION}+{h.hexdigest()[:8]}"


def content_hash(cfg: dict | None = None) -> str:
    """Back-compat: the cases hash (the judge is fingerprinted separately)."""
    return cases_hash()


def revision(cfg: dict | None = None) -> str:
    return f"{SUITE_VERSION}+{cases_hash()}"


def current_revisions(cfg: dict | None = None) -> set[str]:
    """Every whole-run stamp that counts as the current regime — for metas
    and the legacy paths. ROW currency is decided per row and per case
    (:func:`row_current`), never by this set. Includes every frozen snapshot
    stamp of the current regime (crucibleforge/stamps/): its rows are judged
    case by case against the current case content."""
    since = stamp_version(_REGIME_SINCE)
    return ({revision(cfg), legacy_revision(cfg)}
            | {s for s in _EQUIVALENT_STAMPS.get(cases_hash(), set())
               if stamp_version(s) >= since}
            | {s for s in snapshot_stamps() if stamp_version(s) >= since})


def previous_regime_revisions() -> set[str]:
    """Every stamp the last board of the PREVIOUS regime counted (3.7.0 and
    everything it treated as equivalent). Rows carrying one are shown as
    "old" until the model has a complete current run."""
    return set(_PREVIOUS_REGIME)
