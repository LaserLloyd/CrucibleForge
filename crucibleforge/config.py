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
                    api_key_env: DEEPSEEK_API_KEY, concurrency: 4}
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
import os
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

CATEGORIES = ["perf", "rp", "nsfw", "coding", "tooluse", "instruct",
              "reasoning", "math", "steer", "overrefusal", "longctx", "planning"]

# Categories whose rows need the LLM judge (everything else grades objectively).
JUDGED_CATEGORIES = {"rp", "nsfw", "steer", "overrefusal", "planning"}

DIFFICULTIES = ["easy", "medium", "hard"]

PROVIDER_TYPES = ("openai", "lmstudio", "studioforge")

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
        if not p.get("base_url"):
            raise ConfigError(f"provider {pname}: base_url is required")
        if p.get("api_key") and ptype == "openai" and not p.get("allow_literal_key"):
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
    if not os.environ.get("CRUCIBLEFORGE_RESULTS"):
        set_results_dir(p.parent / "results")
    return cfg


def save_config(cfg: dict, path: str | os.PathLike) -> None:
    """Write the registry back out (v3 shape). Internal keys are dropped."""
    out = {k: v for k, v in cfg.items() if not k.startswith("_")}
    tmp = Path(path).with_suffix(".yaml.tmp")
    tmp.write_text(yaml.safe_dump(out, sort_keys=False, allow_unicode=True),
                   encoding="utf-8")
    tmp.replace(path)


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

def load_cases(categories: list[str] | None = None, smoke: bool = False,
               difficulties: list[str] | None = None,
               cases_dir: Path | None = None) -> list[dict]:
    """Load case files for the requested categories (default: all).

    smoke=True keeps only cases tagged "smoke": true — a fast subset that
    still touches every category. difficulties (e.g. ["hard"]) filters to
    cases of those difficulty tiers (untagged cases count as "medium").
    Cases carrying a ``generator`` block (long-context haystacks) are
    materialised here so every consumer sees a plain ``prompt``.
    """
    from .longctx_gen import materialize
    cases_dir = cases_dir or CASES_DIR
    categories = categories or CATEGORIES
    unknown = [c for c in categories if c not in CATEGORIES]
    if unknown:
        raise ConfigError(f"unknown categories: {unknown}. Known: {CATEGORIES}")
    cases: list[dict] = []
    seen_ids: set[str] = set()
    for cat in categories:
        path = cases_dir / f"{cat}.json"
        if not path.exists():
            raise ConfigError(f"missing case file: {path}")
        for case in json.loads(path.read_text(encoding="utf-8")):
            case["category"] = cat
            if case["id"] in seen_ids:
                raise ConfigError(f"duplicate case id: {case['id']}")
            seen_ids.add(case["id"])
            if smoke and not case.get("smoke"):
                continue
            if difficulties and case.get("difficulty", "medium") not in difficulties:
                continue
            if case.get("generator"):
                materialize(case)
            cases.append(case)
    return cases


def repeats_for(cfg: dict, category: str, smoke: bool = False) -> int:
    if smoke:
        return 1
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
    rows: dict[tuple, dict] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue  # torn final line from a killed run — skip
        key = (r.get("bench_run_id"), r.get("case_id"), r.get("repeat"), r.get("turn"))
        rows[key] = r
    return list(rows.values())


def append_transcript(label: str, row: dict) -> None:
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
