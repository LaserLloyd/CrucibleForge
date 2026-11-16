"""Configuration: the models.yaml registry (providers + models + judge), case
files, result paths.

Config search order (first hit wins):
  1. ``--config PATH`` on the CLI / ``config_path`` argument
  2. ``$CRUCIBLEFORGE_CONFIG``
  3. ``./models.yaml`` (current working directory)
  4. ``<repo>/models.yaml`` (next to this package — the dev checkout)
  5. ``~/.config/crucibleforge/models.yaml``

Schema (v3):

    providers:
      lmstudio:    {type: lmstudio,    base_url: http://localhost:1234/v1, api_key: lm-studio}
      studioforge: {type: studioforge, base_url: http://gpu-box:1234/v1}
      deepseek:    {type: openai, base_url: https://api.deepseek.com/v1,
                    api_key_env: DEEPSEEK_API_KEY, concurrency: 8}
    defaults: {context_length: 32768, min_tok_per_s: 1.0, repeats: {...}}
    judge:
      candidates: [{provider: studioforge, model_id: ...}, {provider: deepseek, model_id: deepseek-v4-flash}]
    models:
      - {name: label, provider: deepseek, model_id: deepseek-v4-flash,
         context_length: 131072, price: {input: 0.14, output: 0.28},
         extra_body: {reasoning_format: deepseek}}

API keys are NEVER stored in the yaml for remote providers: ``api_key_env``
names an environment variable. A literal ``api_key`` is only meant for local
servers that want a placeholder token (LM Studio's ``lm-studio``).

The v2 shape (``endpoint:`` + ``endpoint.rig`` + per-model ``studioforge:``
blocks) is upgraded in memory by ``upgrade_legacy`` so old registries keep
working; ``crucibleforge config --upgrade`` writes the new shape out.
"""
from __future__ import annotations

import copy
import json
import threading
import os
import stat
from pathlib import Path

import yaml

ROOT_DIR = Path(__file__).resolve().parent.parent
CASES_DIR = ROOT_DIR / "cases"
DEFAULT_CONFIG_PATH = ROOT_DIR / "models.yaml"
EXAMPLE_CONFIG_PATH = ROOT_DIR / "models.example.yaml"
USER_CONFIG_PATH = Path(os.environ.get("XDG_CONFIG_HOME",
                                        Path.home() / ".config")) / "crucibleforge" / "models.yaml"

# Results dir: next to the config file by default (``<config dir>/results``),
# overridable with $CRUCIBLEFORGE_RESULTS. Set once per process by ``load_config``.
RESULTS_DIR = Path(os.environ.get("CRUCIBLEFORGE_RESULTS", ROOT_DIR / "results"))

_BASE_CATEGORIES = ["perf", "rp", "nsfw", "coding", "tooluse", "instruct",
                    "reasoning", "math", "steer", "overrefusal", "longctx", "planning"]
# A new category is just a new cases/<name>.json (plus, for a judged one, a
# rubric in judge.RUBRICS and a weight under scoring.chat) — see README
# "Adding a Chat component".
#
# PRIVATE categories live in cases/private/<name>.json: git-ignored (and
# refused by scripts/scrub_check.py if ever staged), loaded exactly like a
# public case file, kept out of the public suite revision (version.py), and
# scrubbed from every public artifact (report --public). A category is private
# because of WHERE its file lives — profiles.private_scope() is the one
# place that turns that (plus a profile's ``private:`` list) into the set of
# names/ids to hide. See docs/CASES.md "Private categories".
PRIVATE_CASES_DIR = CASES_DIR / "private"   # fixed: private cases never move with cases_dir
#: CRUCIBLEFORGE_NO_PRIVATE=1 = behave exactly like a clean clone: no private
#: case files, no private profiles/overlays. The test-suite sets it (conftest)
#: so it verifies what ships, whatever private files the operator has.
PRIVATE_ENABLED = os.environ.get("CRUCIBLEFORGE_NO_PRIVATE") != "1"


def _stems(d: Path) -> list[str]:
    return [p.stem for p in d.glob("*.json")] if d.is_dir() else []


#: categories whose case file is in cases/private/ (empty on a clean clone)
PRIVATE_CATEGORIES = (sorted(set(_stems(PRIVATE_CASES_DIR)) - set(_BASE_CATEGORIES))
                      if PRIVATE_ENABLED else [])
CATEGORIES = _BASE_CATEGORIES + sorted(
    (set(_stems(CASES_DIR)) | set(PRIVATE_CATEGORIES)) - set(_BASE_CATEGORIES))

# Categories whose rows need the LLM judge (everything else grades objectively).
JUDGED_CATEGORIES = {"rp", "nsfw", "story", "steer", "overrefusal", "planning", "rpdeep"}

DIFFICULTIES = ["easy", "medium", "hard"]

PROVIDER_TYPES = ("openai", "lmstudio", "studioforge", "anthropic", "claude_code")

CSV_COLUMNS = [
    "bench_run_id", "bench_revision", "run_id", "ts", "model_label", "model_id",
    "device", "category", "case_id", "difficulty", "repeat", "turn", "seed",
    "temperature", "top_p", "max_tokens_sent", "ttft_s", "gen_s", "total_s",
    "prompt_tokens", "completion_tokens", "reasoning_tokens", "tok_per_s",
    "cost_usd", "finish_reason", "grade", "grade_detail",
]


class ConfigError(RuntimeError):
    pass


# --------------------------------------------------------------- location

def find_config_path(explicit: str | os.PathLike | None = None) -> Path:
    if explicit:
        p = Path(explicit).expanduser()
        if not p.exists():
            raise ConfigError(f"config not found: {p}")
        return p
    env = os.environ.get("CRUCIBLEFORGE_CONFIG")
    if env:
        p = Path(env).expanduser()
        if not p.exists():
            raise ConfigError(f"$CRUCIBLEFORGE_CONFIG points at a missing file: {p}")
        return p
    for cand in (Path.cwd() / "models.yaml", DEFAULT_CONFIG_PATH, USER_CONFIG_PATH):
        if cand.exists():
            return cand
    raise ConfigError(
        "no models.yaml found. Copy models.example.yaml to ./models.yaml (or "
        f"{USER_CONFIG_PATH}), or run `crucibleforge config --init`.")


def set_results_dir(path: Path) -> None:
    global RESULTS_DIR
    RESULTS_DIR = Path(path)


#: the results dirs the loaded configs point at (load_config records each)
_CONFIG_RESULTS: list[Path] = []
SCRATCH_MARKER = ".cf-scratch"
LIVE_ENV = "CRUCIBLEFORGE_LIVE_RESULTS"       # extra live destinations (os.pathsep list)


def live_result_dirs(include_selected: bool = True) -> set[str]:
    """realpaths of every destination that is (or may be) the live board: the
    repo's own ``results/``, the default beside any loaded or findable config,
    the configured ``$CRUCIBLEFORGE_RESULTS`` and ``$CRUCIBLEFORGE_LIVE_RESULTS``
    — by path, never by directory name."""
    out = {os.path.realpath(ROOT_DIR / "results")}
    out |= {os.path.realpath(p) for p in _CONFIG_RESULTS}
    try:
        out.add(os.path.realpath(find_config_path(None).parent / "results"))
    except Exception:  # noqa: BLE001 — no config to find is fine
        pass
    # the process's own configured destination: an explicit $CRUCIBLEFORGE_RESULTS
    # IS where the board is written (fix pass 3, GPT 8); a scratch dir opts out
    # with the marker (see staged_isolated)
    env_rd = os.environ.get("CRUCIBLEFORGE_RESULTS", "").strip()
    if env_rd and include_selected:
        out.add(os.path.realpath(env_rd))
    for raw in os.environ.get(LIVE_ENV, "").split(os.pathsep):
        if raw.strip():
            out.add(os.path.realpath(raw.strip()))
    return out


def looks_like_live_board(path: str | os.PathLike) -> bool:
    """A results dir that carries the live board's signature (a report.json,
    per-model meta_*.json or transcripts_*.jsonl) and is not explicitly marked
    scratch (a ``.cf-scratch`` file)."""
    d = Path(path)
    if (d / SCRATCH_MARKER).exists():
        return False
    try:
        return ((d / "report.json").exists() or any(d.glob("meta_*.json"))
                or any(d.glob("transcripts_*.jsonl")))
    except OSError:
        return False


def init_scratch(path: str | os.PathLike) -> Path:
    """The explicit scratch-initialisation contract: create ``path`` (if need
    be) and mark it ``.cf-scratch`` so staged / unapproved rows may be written
    there. Refuses a directory that already carries the live board's signature
    or is a known live destination: a live board is never converted."""
    d = Path(path)
    marked = (d / SCRATCH_MARKER).exists()
    if os.path.realpath(d) in live_result_dirs(include_selected=False) or (
            not marked and (os.path.realpath(d) in live_result_dirs() or looks_like_live_board(d))):
        raise ConfigError(f"{d} is (or looks like) the live board's results dir (the repo's, a "
                          f"config's, ${LIVE_ENV} or $CRUCIBLEFORGE_RESULTS); refusing to mark "
                          "it scratch — pick another directory (pass it as --results)")
    d.mkdir(parents=True, exist_ok=True)
    (d / SCRATCH_MARKER).write_text("staged / scratch results; never the board\n", encoding="utf-8")
    return d


def staged_isolated(rd: str | os.PathLike | None = None) -> bool:
    """May staged (unapproved) pack rows be written to ``rd`` (default: this
    process's results dir)? Only to a directory that is not a known live
    destination and does not carry the live board's signature — or one that
    carries the explicit ``.cf-scratch`` marker. By PATH, never by name."""
    d = Path(rd) if rd is not None else results_dir()
    # independently known live destinations (the repo's results/, a config's, the
    # $CRUCIBLEFORGE_LIVE_RESULTS list) win over any marker; the marker only
    # opts out of the directory the process itself SELECTED (round 4, NEW 4)
    if os.path.realpath(d) in live_result_dirs(include_selected=False):
        return False
    if (d / SCRATCH_MARKER).exists():
        return True
    return os.path.realpath(d) not in live_result_dirs() and not looks_like_live_board(d)


def results_dir() -> Path:
    """Always read through this: importers that bound RESULTS_DIR at import
    time would miss a later ``load_config`` relocation."""
    return RESULTS_DIR


# ------------------------------------------------------------ legacy shape

def upgrade_legacy(cfg: dict) -> dict:
    """Translate a v2 registry (endpoint/rig/studioforge blocks) into the
    v3 providers shape. Idempotent: a v3 config is returned unchanged."""
    if "providers" in cfg:
        return cfg
    cfg = copy.deepcopy(cfg)
    ep = cfg.pop("endpoint", None) or {}
    providers: dict = {}
    if ep:
        providers["lmstudio"] = {
            "type": "lmstudio",
            "base_url": ep.get("base_url", "http://localhost:1234/v1"),
            "api_key": ep.get("api_key", "lm-studio"),
        }
        b4 = ep.get("rig")
        if b4:
            providers["studioforge"] = {
                "type": "studioforge",
                "base_url": b4.get("base_url"),
                "api_key": b4.get("api_key", ""),
            }
    cfg["providers"] = providers
    for m in cfg.get("models", []):
        sf = m.pop("studioforge", None)
        if sf:
            m.setdefault("extra_body", {}).update(sf)
        # v2 resolved the device by membership at runtime; a full StudioForge id
        # (publisher/repo/file) is the tell for the remote rig, anything else is local.
        if "provider" not in m:
            m["provider"] = ("studioforge" if "studioforge" in providers
                             and m.get("model_id", "").count("/") >= 2
                             else "lmstudio")
    for cand in (cfg.get("judge") or {}).get("candidates", []):
        if "provider" not in cand:
            cand["provider"] = ("studioforge" if "studioforge" in providers
                                and cand.get("model_id", "").count("/") >= 2
                                else "lmstudio")
    cfg["_upgraded_from_v2"] = True
    return cfg


# ------------------------------------------------------------------ load

def validate_config(cfg: dict) -> None:
    for key in ("providers", "models", "judge", "defaults"):
        if key not in cfg:
            raise ConfigError(f"models.yaml missing required section: {key}")
    if not isinstance(cfg["providers"], dict) or not cfg["providers"]:
        raise ConfigError("providers: must be a non-empty mapping")
    for pname, p in cfg["providers"].items():
        if not isinstance(p, dict):
            raise ConfigError(f"provider {pname}: must be a mapping")
        ptype = p.get("type", "openai")
        if ptype not in PROVIDER_TYPES:
            raise ConfigError(f"provider {pname}: unknown type {ptype!r} "
                              f"(known: {PROVIDER_TYPES})")
        if not p.get("base_url") and ptype != "claude_code":
            raise ConfigError(f"provider {pname}: base_url is required")
        if p.get("api_key") and ptype in ("openai", "anthropic") and not p.get("allow_literal_key"):
            # A literal key for a remote provider would end up in the repo.
            key = str(p["api_key"])
            if key and len(key) > 12 and not key.startswith("${"):
                raise ConfigError(
                    f"provider {pname}: put remote API keys in an environment "
                    f"variable and reference it with api_key_env, not api_key "
                    f"(set allow_literal_key: true to override for a local server)")
    names = [m["name"] for m in cfg["models"]]
    if len(names) != len(set(names)):
        raise ConfigError("duplicate model names in models.yaml")
    # A model's name is not just a label — it IS a filename
    # (``results/transcripts_<name>.jsonl`` and ``meta_<name>.json``). macOS
    # (APFS) and Windows (NTFS) are case-insensitive, so two entries differing
    # only in case resolve to ONE file there: two models' rows silently merge
    # into one transcript and the report scores a chimera. Linux would keep
    # them apart, so this can't be left to the filesystem to catch.
    folded: dict[str, str] = {}
    for n in names:
        prev = folded.setdefault(n.casefold(), n)
        if prev != n:
            raise ConfigError(
                f"model names {prev!r} and {n!r} differ only in case. They map "
                f"to the same results file on a case-insensitive filesystem "
                f"(macOS/Windows), which would merge their transcripts — "
                f"rename one.")
    for m in cfg["models"]:
        for req in ("name", "model_id", "provider"):
            if not m.get(req):
                raise ConfigError(f"model entry missing {req!r}: {m}")
        if m["provider"] not in cfg["providers"]:
            raise ConfigError(f"model {m['name']}: unknown provider {m['provider']!r}")
        # adult_page (lead 2026-11-15): a bool; refused true on the Claude Code route (adult_guard)
        from .adult_guard import validate_entry as _adult_validate
        _adult_validate(cfg, m)
    for cand in cfg["judge"].get("candidates", []):
        if cand.get("provider") not in cfg["providers"]:
            raise ConfigError(f"judge candidate {cand.get('model_id')}: unknown "
                              f"provider {cand.get('provider')!r}")


def load_config(path: str | os.PathLike | None = None) -> dict:
    p = find_config_path(path)
    with open(p, encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    cfg = upgrade_legacy(cfg)
    validate_config(cfg)
    cfg["_path"] = str(p)
    # results live next to the config unless overridden
    _CONFIG_RESULTS.append(p.parent / "results")
    if not os.environ.get("CRUCIBLEFORGE_RESULTS"):
        set_results_dir(p.parent / "results")
    return cfg


def save_config(cfg: dict, path: str | os.PathLike) -> None:
    """Write the registry back out (v3 shape). Internal keys are dropped."""
    out = {k: v for k, v in cfg.items() if not k.startswith("_")}
    tmp = Path(path).with_suffix(".yaml.tmp")
    tmp.write_text(yaml.safe_dump(out, sort_keys=False, allow_unicode=True),
                   encoding="utf-8")
    # A literal api_key must never land in a world-readable file; otherwise
    # keep whatever mode the existing file has (never loosen a 0600).
    try:
        if _has_literal_api_key(out):
            os.chmod(tmp, 0o600)
        elif Path(path).exists():
            os.chmod(tmp, stat.S_IMODE(Path(path).stat().st_mode))
    except OSError:
        pass  # platforms where chmod is a no-op or unsupported (Windows)
    tmp.replace(path)


def _has_literal_api_key(node) -> bool:
    """True when any nested mapping carries a non-empty string ``api_key``."""
    if isinstance(node, dict):
        for k, v in node.items():
            if k == "api_key" and isinstance(v, str) and v.strip():
                return True
            if _has_literal_api_key(v):
                return True
    elif isinstance(node, (list, tuple)):
        return any(_has_literal_api_key(v) for v in node)
    return False


def provider_of(cfg: dict, name: str) -> dict:
    try:
        p = dict(cfg["providers"][name])
    except KeyError:
        raise ConfigError(f"unknown provider {name!r}") from None
    p["name"] = name
    p.setdefault("type", "openai")
    return p


def resolve_models(cfg: dict, arg: str | None) -> list[dict]:
    """Resolve --models (comma list of labels, or 'all' = enabled entries)."""
    by_name = {m["name"]: m for m in cfg["models"]}
    if arg in (None, "", "all"):
        return [m for m in cfg["models"] if m.get("enabled", True)]
    labels = [s.strip() for s in arg.split(",") if s.strip()]
    unknown = [l for l in labels if l not in by_name]
    if unknown:
        raise ConfigError(
            f"unknown model label(s): {unknown}. Known: {sorted(by_name)}")
    # explicit selection overrides the enabled flag
    return [by_name[l] for l in labels]


# ----------------------------------------------------------------- cases

# Case grading tags (closed sets; version._GRADING_KEYS — they say how a
# row is SCORED, not what is asked):
#   explicit_required: true      the row counts in the Explicit peak mean
#   willing: "must_write"        the row counts in Willing
# (4.1: ``must_refuse`` and the safety probes are gone — the bench grades what
# a model will do, never a refusal; see verify_cases._check_tags)
CASE_TAGS = {"explicit_required": (True, False),
             "willing": ("must_write",)}


def case_tag_errors(case: dict) -> list[str]:
    """Problems with a case's grading tags (empty list = fine)."""
    errs = []
    for k, allowed in CASE_TAGS.items():
        # type-strict: 1 is not True, "true" is not True
        if k in case and not any(type(case[k]) is type(a) and case[k] == a for a in allowed):
            errs.append(f"{k}={case[k]!r} not one of {list(allowed)}")
    return errs


def set_cases_root(path: Path) -> None:
    """Point every case loader (CASES_DIR, ladder sets, case hashes) at one tree.
    A profile's ``cases_dir`` selects it (profiles.activate_cases_root); the
    default is ``cases/``. Private cases stay in cases/private."""
    global CASES_DIR
    path = Path(path)
    CASES_DIR = path
    # Fable gate WP-4 item 8: CATEGORIES is read once from the live tree; a root whose
    # category files differ would silently load a different set, so say so loudly
    try:
        have = set(_stems(path))
        want = set(CATEGORIES) - set(PRIVATE_CATEGORIES)
        if have and have != want and (have - want or want - have):
            import logging
            logging.getLogger(__name__).error("case root %s: category files differ from the live tree (missing %s, extra %s)",
                      path, sorted(want - have), sorted(have - want))
    except Exception:  # noqa: BLE001 - a guard must not break the switch
        pass
    from . import ladder as _ladder, version as _version, report as _report
    _ladder.LADDER_DIR = path / "ladder"
    _version._CASES_DIR = path
    _report._CASES_BY_ID = None


def load_cases(categories: list[str] | None = None, smoke: bool = False,
               difficulties: list[str] | None = None,
               cases_dir: Path | None = None, include_hard: bool = False) -> list[dict]:
    """Load case files for the requested categories (default: all).

    smoke=True keeps only cases tagged "smoke": true — a fast subset that
    still touches every category. difficulties (e.g. ["hard"]) filters to
    cases of those difficulty tiers (untagged cases count as "medium").
    Cases carrying a ``generator`` block (long-context haystacks) are
    materialised here so every consumer sees a plain ``prompt``.
    """
    cases_dir = cases_dir or CASES_DIR
    categories = categories or CATEGORIES
    unknown = [c for c in categories if c not in CATEGORIES]
    if unknown:
        raise ConfigError(f"unknown categories: {unknown}. Known: {CATEGORIES}")
    cases: list[dict] = []
    seen_ids: set[str] = set()
    for cat in categories:
        path = cases_dir / f"{cat}.json"
        if not path.exists() and cat in PRIVATE_CATEGORIES:
            path = PRIVATE_CASES_DIR / f"{cat}.json"
        if not path.exists():
            raise ConfigError(f"missing case file: {path}")
        for case in json.loads(path.read_text(encoding="utf-8")):
            case["category"] = cat
            if case["id"] in seen_ids:
                raise ConfigError(f"duplicate case id: {case['id']}")
            seen_ids.add(case["id"])
            bad = case_tag_errors(case)
            if bad:
                raise ConfigError(f"case {case['id']}: " + "; ".join(bad))
            if smoke and not case.get("smoke"):
                continue
            if difficulties and case.get("difficulty", "medium") not in difficulties:
                continue
            if case.get("generator"):
                _materialize_generated(case)
            cases.append(case)
    if include_hard:
        # 4.5 hard sets (cases/hard/<score>/<rev>.json, crucibleforge/hard.py):
        # only on request; a profile run does not see them until hard scoring lands
        from . import hard as _hard
        for case in _hard.load(cases_dir):
            if case["category"] not in categories:
                continue
            if case["id"] in seen_ids:
                raise ConfigError(f"duplicate case id: {case['id']} (hard set {case['hard_score']})")
            seen_ids.add(case["id"])
            bad = case_tag_errors(case)
            if bad:
                raise ConfigError(f"case {case['id']}: " + "; ".join(bad))
            if smoke and not case.get("smoke"):
                continue
            if difficulties and case.get("difficulty", "medium") not in difficulties:
                continue
            cases.append(case)
    if cases_dir == CASES_DIR:
        # ladder packs may carry their own questions (ladder.inline_cases):
        # part of the case set, outside the suite hash
        from .ladder import inline_cases
        for case in inline_cases():
            if case["category"] not in categories:
                continue
            if case["id"] in seen_ids:
                raise ConfigError(f"duplicate case id: {case['id']} (ladder pack {case['ladder_pack']})")
            seen_ids.add(case["id"])
            bad = case_tag_errors(case)
            if bad:
                raise ConfigError(f"case {case['id']}: " + "; ".join(bad))
            if smoke and not case.get("smoke"):
                continue
            if difficulties and case.get("difficulty", "medium") not in difficulties:
                continue
            if case.get("generator"):
                _materialize_generated(case)
            cases.append(case)
    return cases


def _materialize_generated(case: dict) -> None:
    """Fill a case from its ``generator`` block: a registered family
    (crucibleforge/gen, 4.5) or the long-context haystack types."""
    from . import gen as _gen
    if _gen.is_generated(case):
        _gen.materialize_case(case)
    else:
        from .longctx_gen import materialize
        materialize(case)


def repeats_for(cfg: dict, category: str, smoke: bool = False, case: dict | None = None) -> int:
    """Repeats of a case: 1 for smoke; the case's own ``repeats`` (a profile's
    ``case_repeats`` or a tier's ``repeats``, profiles.py); else the
    category's."""
    if smoke:
        return 1
    if case and case.get("repeats"):
        return int(case["repeats"])
    return int(cfg["defaults"].get("repeats", {}).get(category, 1))


# --------------------------------------------------------------- results

def transcript_path(label: str) -> Path:
    return results_dir() / f"transcripts_{label}.jsonl"


def load_transcripts(label: str) -> list[dict]:
    """Load a model's transcript rows, deduped by
    (bench_run_id, case_id, repeat, turn): the file is append-only, so the LAST
    row for a key wins. Including bench_run_id in the key means separate runs
    ACCUMULATE (n grows across nightly runs) while a judge re-append for the
    same row still supersedes its earlier unjudged version."""
    path = transcript_path(label)
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue  # torn final line from a killed run — skip
    return dedupe_rows(rows)


def dedupe_rows(rows: list[dict]) -> list[dict]:
    """The stored-row rule every reader shares: the LAST row per (bench_run_id, case_id,
    repeat, turn) wins (a judge re-append, a regrade's superseding row), first-seen order kept."""
    out: dict[tuple, dict] = {}
    for r in rows:
        out[(r.get("bench_run_id"), r.get("case_id"), r.get("repeat"), r.get("turn"))] = r
    return list(out.values())


#: one writer at a time per process: generation workers and an overlapping judge
#: (judge_overlap) append to the same transcript; a long row is written in several
#: buffered chunks, which two threads could interleave
_APPEND_LOCK = threading.Lock()


def append_transcript(label: str, row: dict) -> None:
    with _APPEND_LOCK:
        _append_transcript(label, row)


def _append_transcript(label: str, row: dict) -> None:
    results_dir().mkdir(parents=True, exist_ok=True)
    path = transcript_path(label)
    # Self-heal a torn final line from a killed run: if the file doesn't end
    # in a newline, our append would concatenate onto the fragment and make
    # BOTH lines unparsable (dropping this valid row on the next read).
    if path.exists() and path.stat().st_size > 0:
        with open(path, "rb") as f:
            f.seek(-1, 2)
            needs_nl = f.read(1) != b"\n"
    else:
        needs_nl = False
    with open(path, "a", encoding="utf-8") as f:
        if needs_nl:
            f.write("\n")
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
