"""`test <model>`: one benchmark run with no config file and no flags to learn (4.5.1, the container's front door).

    crucibleforge-test [--yes] [--no-adult] [--local URL] [--judge-local URL MODEL] MODEL

What it does, in order: makes sure a config exists (the container's entrypoint seeds it; natively `config --init`
does), registers MODEL (an OpenRouter id, its price and context read from OpenRouter's public model list; or, with
--local, a model on an OpenAI-compatible server such as Ollama, llama-server or LM Studio), picks the judge (the
shipped OpenRouter Qwen 3.8 27B entry, or --judge-local), prints a short plan with the estimated cost and time, sets
the spend caps at about twice the estimate, asks "Continue? [y/N]", runs `all`, and prints the result in a few lines
and where the files are. Running the same command again on the same folder continues where it stopped.

Every failure a first-time user meets has one plain sentence and an exit code (EXIT_*). The engine itself is driven
through its normal CLI in a child process, so nothing here changes how anything is graded or scored."""
from __future__ import annotations

import argparse
import difflib
import json
import os
import re
import struct
import socket
import shutil
import signal
import ssl
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

OPENROUTER_URL = "https://openrouter.ai/api/v1"
#: the shipped OpenRouter judge candidate (models.example.yaml `qwen27-judge-openrouter`): its spec resolves to it
DEFAULT_JUDGE = "openrouter:qwen/qwen3.8-27b"
CAP_FACTOR = 2.0            # spend caps: about twice the plan's own figure (lead 2026-11-24: a cut run costs more)
MIN_CAP_USD = 0.50

from .firstrun import CODES, HEADLINES  # noqa: E402 - every message's headline and exit code live there

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_NO_KEY = CODES["NO_KEY"]
EXIT_KEY_REJECTED = CODES["KEY_REJECTED"]
EXIT_NO_CREDIT = CODES["NO_CREDIT"]
EXIT_UNKNOWN_MODEL = CODES["UNKNOWN_MODEL"]
EXIT_NOT_WRITABLE = CODES["NOT_WRITABLE"]
EXIT_NOT_CONFIRMED = CODES["NOT_CONFIRMED"]
EXIT_LOCAL_UNREACHABLE = CODES["LOCAL_UNREACHABLE"]
EXIT_RUN_FAILED = CODES["RUN_FAILED"]
EXIT_STOPPED = CODES["STOPPED"]
#: free space the run needs: its results (about 11 MB for one model) and the graded programs' scratch
MIN_FREE_DATA_MB = 200
MIN_FREE_TMP_MB = 500
#: keys of other services, by their fixed prefix (an OpenRouter key starts with sk-or-)
OTHER_KEYS = (("sk-ant-", "Anthropic"), ("sk-proj-", "OpenAI"), ("sk-svcacct-", "OpenAI"), ("AIza", "Google"),
              ("gsk_", "Groq"), ("xai-", "xAI"), ("hf_", "Hugging Face"), ("pplx-", "Perplexity"))

IN_CONTAINER = os.path.exists("/.dockerenv") or os.path.exists("/run/.containerenv") or \
    os.environ.get("CRUCIBLEFORGE_IN_CONTAINER") == "1"


class Fail(Exception):
    """A failure with a plain sentence for the user and an exit code."""

    def __init__(self, code: int, *lines: str):
        super().__init__(lines[0] if lines else "")
        self.code = code
        self.lines = lines


def fail(name: str, rest: str = "", *more: str) -> Fail:
    """Fail(code of `name`, its headline + rest, more lines...)."""
    return Fail(CODES[name], HEADLINES[name] + rest, *more)


PROXY_LINES = ("  Behind a company proxy? Pass it into the container:  -e HTTPS_PROXY=http://<proxy>:<port>",
               "  (and -e NO_PROXY=host.docker.internal when a model runs on this computer).")


def net_fail(e: BaseException, host: str) -> Fail:
    """A plain sentence for a connection that failed before any HTTP answer: DNS, TLS (a proxy that replaces
    certificates, or a wrong clock), or no route at all."""
    reason = getattr(e, "reason", e)
    text = str(reason)
    if isinstance(reason, ssl.SSLCertVerificationError) or "CERTIFICATE_VERIFY_FAILED" in text:
        if re.search(r"not yet valid|expired", text, re.I):
            return fail("CLOCK", f" ({text}).",
                        "  Set the date and time correctly (turn on automatic time), restart Docker, and run again.")
        return fail("TLS", f" {host} replaced its security certificate ({text}).",
                    "  This is usually a company proxy or an antivirus that inspects HTTPS. Pass the proxy and its",
                    "  certificate: put the certificate file in your results folder, then add",
                    "    -e HTTPS_PROXY=http://<proxy>:<port> -e SSL_CERT_FILE=/data/<certificate>.pem")
    if isinstance(reason, socket.gaierror) or re.search(r"Name or service not known|Temporary failure in name "
                                                         r"resolution|nodename nor servname", text):
        return fail("DNS", f" {host} (no internet, or no name lookups from containers).",
                    "  Check that this computer is online; if a browser works, restart Docker.", *PROXY_LINES)
    return fail("NETWORK", f" {host} ({text}).", "  Check the internet connection, then run the same command again.",
                *PROXY_LINES)


def http_fail(e: urllib.error.HTTPError, what: str) -> Fail | None:
    if e.code == 429:
        return Fail(CODES["RATE_LIMITED"], f"OpenRouter {HEADLINES['RATE_LIMITED']}",
                    "  Wait a minute and run the same command again; nothing is lost.")
    if e.code in (401, 403):
        return fail("KEY_REJECTED", " (it is wrong, revoked or mistyped).",
                    "  Copy it again from https://openrouter.ai/keys and run the same command.")
    if e.code == 402:
        return Fail(EXIT_NO_CREDIT, *_no_credit_lines())
    return None


def say(*lines: str) -> None:
    for ln in lines:
        print(ln, flush=True)


# ---------------------------------------------------------------- OpenRouter
def or_url() -> str:
    return os.environ.get("CRUCIBLEFORGE_OPENROUTER_URL", OPENROUTER_URL).rstrip("/")


def _get_json(url: str, key: str | None = None, timeout: float = 30.0):
    req = urllib.request.Request(url, headers={"User-Agent": "crucibleforge-test",
                                               **({"Authorization": f"Bearer {key}"} if key else {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 - a fixed https URL or the test override
        return json.loads(r.read().decode("utf-8"))


def openrouter_models() -> list[dict]:
    """OpenRouter's public model list (no key needed)."""
    try:
        return list(_get_json(or_url() + "/models").get("data") or [])
    except urllib.error.HTTPError as e:
        f = http_fail(e, "the model list")
        raise (f or Fail(EXIT_RUN_FAILED, f"OpenRouter answered HTTP {e.code} for its model list.",
                         "  Try again in a few minutes.")) from e
    except (urllib.error.URLError, OSError) as e:
        raise net_fail(e, "openrouter.ai") from e
    except ValueError as e:
        raise net_fail(e, "openrouter.ai (the answer was not OpenRouter's: a proxy page?)") from e


def find_model(models: list[dict], wanted: str) -> dict:
    by_id = {m.get("id"): m for m in models if m.get("id")}
    if wanted in by_id:
        return by_id[wanted]
    low = {k.lower(): k for k in by_id}
    if wanted.lower() in low:
        return by_id[low[wanted.lower()]]
    tail = [k for k in by_id if k.split("/", 1)[-1].lower() == wanted.lower()]
    if len(tail) == 1:
        return by_id[tail[0]]
    near = difflib.get_close_matches(wanted, list(by_id), n=5, cutoff=0.4)
    if not near:
        near = [k for k in by_id if wanted.lower().split("/")[-1][:6] in k.lower()][:5]
    lines = [f"{HEADLINES['UNKNOWN_MODEL']} '{wanted}'."]
    if near:
        lines.append("Did you mean one of these?")
        lines += [f"  {n}" for n in near]
    lines.append("The ids are listed at https://openrouter.ai/models (copy the id shown under the name), or in a")
    lines.append("terminal:  curl -s https://openrouter.ai/api/v1/models | grep '\"id\"'")
    raise Fail(EXIT_UNKNOWN_MODEL, *lines)


def key_format(raw: str | None) -> str:
    """The key as OpenRouter needs it, or a plain failure for a key that cannot work (before any request)."""
    if raw is None:
        raise fail("NO_KEY", "", "Get one at https://openrouter.ai/keys, then put it in the command:",
                   "  docker run ... -e OPENROUTER_API_KEY=sk-or-... ...")
    key = raw.strip()
    if not key:
        raise fail("KEY_EMPTY", " The variable OPENROUTER_API_KEY is set but holds nothing.",
                   "  Put the key itself after the = sign:  -e OPENROUTER_API_KEY=sk-or-...",
                   "  (-e OPENROUTER_API_KEY alone passes the variable from your shell: set it there first.)")
    if key[0] in "'\"`" or key[-1] in "'\"`" or any(ch.isspace() for ch in key):
        raise fail("KEY_QUOTED", "",
                   "  Give the key without quote marks inside it:  -e OPENROUTER_API_KEY=sk-or-...",
                   "  In PowerShell, from a variable:  -e OPENROUTER_API_KEY=$env:OPENROUTER_API_KEY")
    for prefix, who in OTHER_KEYS:
        if key.startswith(prefix):
            raise fail("KEY_OTHER_PROVIDER", f" it looks like a {who} key. OpenRouter keys start with sk-or-.",
                       "  Make one at https://openrouter.ai/keys and put it in the command.")
    return key


def check_key(key: str | None) -> dict:
    """A missing, malformed, rejected or empty-credit OpenRouter key stops here, before anything is planned.
    Returns what it learned: {"balance": float | None, "skew_s": float | None}."""
    key = key_format(key)
    out: dict = {"balance": None, "skew_s": None}
    try:
        req = urllib.request.Request(or_url() + "/key", headers={"User-Agent": "crucibleforge-test",
                                                                 "Authorization": f"Bearer {key}"})
        with urllib.request.urlopen(req, timeout=30) as r:  # noqa: S310 - a fixed https URL or the test override
            info = json.loads(r.read().decode("utf-8")).get("data") or {}
            out["skew_s"] = _skew(r.headers.get("Date"))
    except urllib.error.HTTPError as e:
        f = http_fail(e, "the key")
        if f:
            raise f from e
        return out                               # another status: let the run itself report it
    except (urllib.error.URLError, OSError) as e:
        raise net_fail(e, "openrouter.ai") from e
    except ValueError:
        return out
    left = info.get("limit_remaining")
    if isinstance(left, (int, float)) and left <= 0:
        raise Fail(EXIT_NO_CREDIT, *_no_credit_lines())
    try:                                         # the account's balance (the key's own limit is checked above)
        cr = _get_json(or_url() + "/credits", key).get("data") or {}
        bal = float(cr["total_credits"]) - float(cr["total_usage"])
    except (urllib.error.URLError, OSError, ValueError, KeyError, TypeError):
        return out                               # not every key may read it: the run reports a 402 itself
    out["balance"] = bal
    if bal <= 0:
        raise Fail(EXIT_NO_CREDIT, *_no_credit_lines())
    return out


def _skew(date_header: str | None) -> float | None:
    from email.utils import parsedate_to_datetime
    try:
        return time.time() - parsedate_to_datetime(date_header).timestamp() if date_header else None
    except (TypeError, ValueError):
        return None


def _no_credit_lines() -> tuple[str, ...]:
    return (HEADLINES["NO_CREDIT"] + " (or the key's own limit is used up).",
            "Add credit at https://openrouter.ai/settings/credits, then run the same command: it continues.")


# ---------------------------------------------------------------- engine
def engine_argv(*args: str) -> list[str]:
    exe = shutil.which("crucibleforge")
    return ([exe] if exe else [sys.executable, "-m", "crucibleforge.cli"]) + list(args)


def engine_out(*args: str, env: dict | None = None, tick: str | None = None, every: float = 15.0,
               phases: bool = False) -> tuple[int, str]:
    """Run the engine, capture its output; with `tick`, print a 'still ...' line every `every` seconds meanwhile
    (the plan can take minutes and must not look stuck). With `phases`, the line names what the engine is doing
    (its own 'plan: still working (N s): <step>' lines)."""
    stop = threading.Event()
    t0 = time.time()
    phase = [""]
    chunks: list[str] = []

    def ticker():
        while not stop.wait(every):
            say(f"  ... {tick} ({time.time() - t0:.0f} s)" + (f": {phase[0]}" if phase[0] else ""))
    th = threading.Thread(target=ticker, daemon=True) if tick else None
    if th:
        th.start()
    try:
        p = subprocess.Popen(engine_argv(*args), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                             encoding="utf-8", errors="replace", env=env)
        assert p.stdout is not None
        for line in p.stdout:
            chunks.append(line)
            if phases:
                m = re.match(r"plan: still working \(\d+s\): (.+)", line.strip())
                if m:
                    phase[0] = m.group(1)
        rc = p.wait()
    finally:
        stop.set()
    return rc, "".join(chunks)


def config_path() -> Path:
    from . import config
    return Path(os.environ.get("CRUCIBLEFORGE_CONFIG") or config.DEFAULT_CONFIG_PATH)


def results_dir() -> Path:
    return Path(os.environ.get("CRUCIBLEFORGE_RESULTS") or Path(config_path()).parent / "results")


def load_cfg(notes: list[str] | None = None) -> dict:
    """models.yaml, checked the way the engine will check it; a hand-edit mistake is named by file, line and key
    with an example of a valid line, instead of a traceback later."""
    import yaml
    p = config_path()
    if not p.exists():
        rc, out = engine_out("config", "--init")
        if not p.exists():
            raise Fail(EXIT_RUN_FAILED, f"Could not create {p}:", out.strip()[-400:])
    text = p.read_text(encoding="utf-8", errors="replace")
    where = _host_path(p)
    try:
        cfg = yaml.safe_load(text) or {}
    except yaml.YAMLError as e:
        mark = getattr(e, "problem_mark", None)
        line = f", line {mark.line + 1}" if mark is not None else ""
        raise fail("BAD_CONFIG", f" ({where}{line}): {getattr(e, 'problem', None) or e}.", *_config_fix_lines()) \
            from e
    if not isinstance(cfg, dict):
        raise fail("BAD_CONFIG", f" ({where}): it should be a list of settings (providers:, models:, judge:, ...).",
                   *_config_fix_lines())
    from .config import ConfigError, upgrade_legacy, validate_config
    import copy
    try:
        validate_config(upgrade_legacy(copy.deepcopy(cfg)))
    except ConfigError as e:
        msg = str(e)
        tok = re.findall(r"'([^']+)'", msg) or re.findall(r"^(?:provider|model) (\S+?):", msg)
        line = next((f", line {n}" for n, ln in enumerate(text.splitlines(), 1) if tok and tok[0] in ln), "")
        raise fail("BAD_CONFIG", f" ({where}{line}): {msg}.", *_config_fix_lines()) from e
    except (KeyError, TypeError, AttributeError) as e:
        raise fail("BAD_CONFIG", f" ({where}): a section has the wrong shape ({e}).", *_config_fix_lines()) from e
    if notes is not None:
        notes += unknown_key_notes(cfg, text, where)
    return cfg


def _host_path(p: Path) -> str:
    return "models.yaml in your results folder" if IN_CONTAINER and str(p).startswith("/data/") else str(p)


def _config_fix_lines() -> tuple[str, ...]:
    return ("  A valid model line looks like:  - {name: my-model, provider: openrouter, model_id: openai/gpt-6-luna}",
            "  Fix that line, or rename models.yaml to models.yaml.old: the next run writes a fresh one (results are kept).")


def _known_keys() -> dict[str, set]:
    """Keys the shipped example config uses, per section (a key outside these is not wrong, only unseen)."""
    import yaml
    known: dict[str, set] = {"top": set(), "provider": set(), "model": set()}
    for f in (Path(__file__).resolve().parent.parent / "models.example.yaml", Path("/app/models.example.yaml")):
        try:
            ex = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError):
            continue
        known["top"] |= set(ex)
        for pv in (ex.get("providers") or {}).values():
            known["provider"] |= set(pv or {})
        for m in ex.get("models") or []:
            known["model"] |= set(m or {})
        break
    return known


def unknown_key_notes(cfg: dict, text: str, where: str) -> list[str]:
    """A key that is a near-miss of a known one (base_ulr, modle_id) is almost surely a typo: name it with the
    closest valid key. Keys with no close match are left alone (the engine has more keys than the example shows)."""
    known, out = _known_keys(), []

    def look(keys, section, ctx):
        for k in keys:
            if not isinstance(k, str) or not known[section] or k in known[section]:
                continue
            near = difflib.get_close_matches(k, sorted(known[section]), n=1, cutoff=0.8)
            if near:
                line = next((n for n, ln in enumerate(text.splitlines(), 1) if re.search(rf"\b{re.escape(k)}\s*:", ln)), 0)
                out.append(f"  note: {where}{f', line {line}' if line else ''}: unknown key '{k}' {ctx} "
                           f"(did you mean '{near[0]}'?)")
    look(cfg.keys(), "top", "at the top")
    for pn, pv in (cfg.get("providers") or {}).items():
        if isinstance(pv, dict):
            look(pv.keys(), "provider", f"in provider {pn}")
    for m in cfg.get("models") or []:
        if isinstance(m, dict):
            look(m.keys(), "model", f"in model {m.get('name')}")
    return out


def save_cfg(cfg: dict) -> None:
    import yaml
    p = config_path()
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(yaml.safe_dump(cfg, sort_keys=False, allow_unicode=True), encoding="utf-8")
    os.chmod(tmp, 0o600)                         # it may name keys' variables and local addresses: yours only
    tmp.replace(p)


def label_for(model_id: str) -> str:
    base = model_id.split("/")[-1].split(":")[0]
    return re.sub(r"[^a-z0-9.-]+", "-", base.lower()).strip("-.")[:60] or "model"


def _default_gateway() -> str | None:
    """The container's default gateway (on Linux Docker and Podman that is the host), from /proc/net/route."""
    try:
        for line in Path("/proc/net/route").read_text(encoding="ascii").splitlines()[1:]:
            f = line.split()
            if len(f) > 2 and f[1] == "00000000":
                return socket.inet_ntoa(struct.pack("<L", int(f[2], 16)))
    except (OSError, ValueError):
        pass
    return None


def host_address() -> str:
    """The name or address that reaches the person's own machine from inside the container: Docker Desktop and
    Podman name it (host.docker.internal / host.containers.internal); plain Docker on Linux does not, and there
    the bridge's gateway is the host."""
    for name in ("host.docker.internal", "host.containers.internal"):
        try:
            socket.gethostbyname(name)
            return name
        except OSError:
            continue
    return _default_gateway() or "host.docker.internal"


def host_url(url: str) -> str:
    """Inside a container, localhost is the container: point it at the host instead."""
    if IN_CONTAINER:
        url = re.sub(r"//(localhost|127\.0\.0\.1|0\.0\.0\.0)([:/]|$)", lambda m: "//" + host_address() + m.group(2), url)
    url = url.rstrip("/")
    return url if url.endswith("/v1") else url + "/v1"


def ensure_provider(cfg: dict, name: str, base_url: str, key_env: str | None = None) -> None:
    prov = cfg.setdefault("providers", {})
    cur = dict(prov.get(name) or {})
    cur.update({"type": "openai", "base_url": base_url, "concurrency": int(cur.get("concurrency") or 4)})
    if key_env:
        cur["api_key_env"] = key_env
    else:
        cur.pop("api_key_env", None)
    cur["hosted"] = False
    prov[name] = cur


def register(cfg: dict, label: str, provider: str, model_id: str, ctx: int | None, price: tuple | None) -> str:
    """The registry label for (provider, model_id): an existing entry is reused, else a new one is added."""
    models = cfg.get("models") or []
    for m in models:
        if m.get("provider") == provider and m.get("model_id") == model_id:
            return m["name"]
    taken = {m.get("name") for m in models}
    name, i = label, 2
    while name in taken:
        name, i = f"{label}-{i}", i + 1
    args = ["models", "add", name, provider, model_id]
    if ctx:
        args += ["--context-length", str(int(ctx))]
    if price is not None:
        args += ["--price-in", f"{price[0]:g}", "--price-out", f"{price[1]:g}"]
    rc, out = engine_out(*args)
    if rc != 0:
        raise Fail(EXIT_RUN_FAILED, f"Could not register {model_id}:", out.strip()[-600:])
    return name


def probe_local(url: str, model: str, key_env: str | None = None, what: str = "model") -> int | None:
    """The local server answers and serves `model`. Returns the model's context window when the server says it
    (vLLM max_model_len, LM Studio loaded_context_length, llama-server n_ctx, Ollama's context), else None.
    `key_env` names the variable holding the server's key (LOCAL_API_KEY / JUDGE_LOCAL_API_KEY); its value is
    sent as a Bearer key and never printed or written anywhere."""
    var = key_env or ("JUDGE_LOCAL_API_KEY" if what == "judge" else "LOCAL_API_KEY")
    key = os.environ.get(key_env, "").strip() if key_env else ""
    try:
        data = _get_json(url + "/models", key or None, timeout=10).get("data") or []
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            if not key:
                raise Fail(CODES["LOCAL_KEY_NEEDED"], f"{HEADLINES['LOCAL_KEY_NEEDED']} add -e {var}=<the server's key> "
                           f"to the docker command (the server at {url} answered HTTP {e.code}).",
                           "  The key stays in the container's memory: it is not written to the results folder.") from e
            raise Fail(CODES["LOCAL_KEY_REJECTED"], f"{HEADLINES['LOCAL_KEY_REJECTED']} {key_env} (HTTP {e.code} "
                       f"from {url}).", f"  Copy the key again from the server's settings and put it in -e {key_env}=...") \
                from e
        raise Fail(EXIT_LOCAL_UNREACHABLE, f"{HEADLINES['LOCAL_UNREACHABLE']} {url} with a model list "
                   f"(HTTP {e.code}).", "  Is this the server's OpenAI-compatible address (it usually ends in /v1)?") from e
    except (urllib.error.URLError, OSError, ValueError) as e:
        lines = [f"{HEADLINES['LOCAL_UNREACHABLE']} {url} ({getattr(e, 'reason', e)}).",
                 "  Is the model server running, and does it listen on all addresses (0.0.0.0), not only localhost?",
                 "    Ollama: OLLAMA_HOST=0.0.0.0   llama-server: --host 0.0.0.0   LM Studio: 'Serve on local network'"]
        if IN_CONTAINER and Path("/proc/net/route").exists() and not _docker_desktop():
            lines += ["  On Linux add to the docker command:  --add-host=host.docker.internal:host-gateway",
                      "  With rootless Docker the container cannot reach this computer by that name: give its network",
                      "  address instead (hostname -I prints it), e.g.  --local http://<that address>:11434"]
        raise Fail(EXIT_LOCAL_UNREACHABLE, *lines) from e
    ids = [d.get("id") for d in data if d.get("id")]
    if ids and model not in ids:
        near = difflib.get_close_matches(model, ids, n=5, cutoff=0.3)
        rest = [i for i in ids if i not in near]
        shown = near + rest[:max(0, 20 - len(near))]
        raise Fail(EXIT_UNKNOWN_MODEL, f"The server at {url} {HEADLINES['LOCAL_NO_MODEL']} '{model}'.",
                   "  Give the id exactly as the server lists it" + (" (closest first)" if near else "") + ":",
                   *[f"    {i}" for i in shown],
                   *([f"    ... and {len(ids) - len(shown)} more"] if len(ids) > len(shown) else []),
                   "  Or load the model in the server first (Ollama: ollama pull <name>; LM Studio: load it).")
    entry = next((d for d in data if d.get("id") == model), {})
    for k in ("max_model_len", "loaded_context_length", "context_length", "max_context_length"):
        if isinstance(entry.get(k), int) and entry[k] > 0:
            return int(entry[k])
    root = url[:-3] if url.endswith("/v1") else url
    try:                                      # llama-server
        n = ((_get_json(root + "/props", key or None, timeout=5).get("default_generation_settings") or {}).get("n_ctx"))
        if isinstance(n, int) and n > 0:
            return n
    except (urllib.error.URLError, OSError, ValueError):
        pass
    return None


def local_key_env(which: str, url: str, model_url: str | None = None) -> str | None:
    """The NAME of the variable that holds a local server's key (only names are written to models.yaml):
    LOCAL_API_KEY for the model; JUDGE_LOCAL_API_KEY for --judge-local, else LOCAL_API_KEY when the judge is on the
    same server as the model. None when no key was given (a server without auth)."""
    from urllib.parse import urlsplit
    if which == "judge":
        if os.environ.get("JUDGE_LOCAL_API_KEY", "").strip():
            return "JUDGE_LOCAL_API_KEY"
        if not (model_url and urlsplit(model_url).netloc == urlsplit(url).netloc):
            return None
    return "LOCAL_API_KEY" if os.environ.get("LOCAL_API_KEY", "").strip() else None


def _docker_desktop() -> bool:
    try:
        socket.gethostbyname("host.docker.internal")
        return True
    except OSError:
        return False


def context_check(plan_text: str, ctx: int | None, local: bool) -> list[str]:
    """Rows the plan marks 'n/a (ctx: needs N)': a local server can be restarted with a larger window (refused
    with the number needed); a hosted model's window is what it is (one note, those rows count as missed)."""
    needs = [int(n) for n in re.findall(r"n/a \(ctx: needs (\d+)\)", plan_text)]
    if not needs:
        if not local:
            return []
        if ctx:
            return [f"  note: context window {ctx} tokens: every question in this suite fits."]
        return ["  note: the server does not say the model's context window, so it could not be checked; a question "
                "that does not fit fails in the run (llama-server -c, LM Studio Context Length, "
                "Ollama OLLAMA_CONTEXT_LENGTH set it)."]
    need = max(needs)
    if local:
        raise Fail(CODES["LOCAL_CONTEXT"], f"The model's {HEADLINES['LOCAL_CONTEXT']}: it has {ctx or '?'} tokens, "
                   f"{len(needs)} question(s) need up to {need}.",
                   f"  Restart the server with a context of at least {need} tokens:",
                   f"    llama-server -c {need}   LM Studio: Context Length {need}   Ollama: OLLAMA_CONTEXT_LENGTH={need}",
                   "  then run the same command again.")
    return [f"  note: the model's context window ({ctx} tokens) is too small for {len(needs)} question(s) (they need "
            f"up to {need}); those count as missed."]


# ---------------------------------------------------------------- plan
_PRICE = re.compile(r"price \(estimate[^)]*\)[^:]*: ~\$([\d.]+) expected.*?\$([\d.]+) for a model that thinks at length")
_JUDGE = re.compile(r"judge cost \(estimate\)[^:]*: ~\$([\d.]+) expected.*?up to \$([\d.]+)"
                    r"(?:[^\n]*?\+ up to \$([\d.]+) more if every pack round unlocks)?")
_MIN = re.compile(r"-> ~([\d.]+) min")


def read_plan(text: str) -> dict:
    out: dict = {}
    m = _PRICE.search(text)
    if m:
        out["model_usd"], out["model_long_usd"] = float(m.group(1)), float(m.group(2))
    m = _JUDGE.search(text)
    if m:
        out["judge_usd"], out["judge_max_usd"] = float(m.group(1)), float(m.group(2))
        out["judge_packs_max_usd"] = float(m.group(3)) if m.group(3) else None
    m = _MIN.search(text)
    if m:
        out["minutes"] = float(m.group(1))
    out["rows"] = (re.search(r"T1 — every model: (\d+) rows", text) or [None, None])[1]
    out["owed"] = [ln.strip() for ln in text.splitlines() if ln.strip().startswith("owed on resume")]
    return out


def caps(plan: dict) -> tuple[float | None, float | None]:
    """(model cap, whole-run cap): about twice the plan's 'thinks at length' figure, and that plus twice the judge's
    expected figure for the run as a whole. None where the plan has no price (a local model / a local judge)."""
    mc = plan.get("model_long_usd")
    model_cap = max(MIN_CAP_USD, round(CAP_FACTOR * mc, 2)) if mc is not None else None
    jc = plan.get("judge_usd")             # its "up to" figure assumes every reply re-asked: far above any real run
    total = (model_cap or 0.0) + (max(MIN_CAP_USD, round(CAP_FACTOR * jc, 2)) if jc is not None else 0.0)
    return model_cap, (round(total, 2) if total else None)


# ---------------------------------------------------------------- result
def summary_lines(label: str, judge_desc: str) -> list[str]:
    p = results_dir() / "report.json"
    try:
        m = json.loads(p.read_text(encoding="utf-8"))["models"][label]
    except (OSError, ValueError, KeyError):
        return [f"No board was written for {label} (see the lines above)."]
    sc, seg = m.get("scorecard") or {}, m.get("segments45") or {}

    def split(t: dict) -> str:
        """'20.7 of 34 = 16.9 standard + 3.8 bonus' from the engine's own fields (total, standard_points,
        bonus_points, max_with_packs); a board without them prints the standard score alone."""
        std = t.get("standard_points", t.get("points"))
        if t.get("total") is None or t.get("max_with_packs") is None:
            return f"{std:.1f} of {t.get('max', 10):g}"
        return (f"{t['total']:.1f} of {float(t['max_with_packs']):g} = {std:.1f} standard"
                f" + {float(t.get('bonus_points') or 0.0):.1f} bonus")

    def part(k):
        s = seg.get(k) or {}
        if s.get("points") is None:
            return "pending"
        return split(s) + ("" if s.get("complete") else " (not complete yet)")
    ov = seg.get("overall") or {}           # the board's Overall (report.md/html); scorecard.total is the older sum
    if ov.get("points") is not None:
        overall = "Overall " + split({"max": 20, **ov}) + ("" if ov.get("complete", True) else " (not complete yet)")
    elif sc.get("total") is not None:
        overall = f"Overall {sc['total']:.1f} of 20"
    else:
        overall = f"Overall: {sc.get('unranked_why') or 'not ranked yet'}"
    lines = [overall, f"Code    {part('code')}", f"Chat    {part('chat')}"]
    n = m.get("nsfw_plus") or {}
    nt = n.get("total") or {}
    at = (n.get("totals45") or {}).get("nsfw_plus") if isinstance(n.get("totals45"), dict) else None
    label = "Adult (NSFW+, never part of Overall)"
    if n.get("state") == "not_run" or (not nt and not n):
        lines.append("Adult (NSFW+): not run")
    elif at and at.get("total") is not None:
        lines.append(f"{label} " + split({"standard_points": at.get("standard"), "bonus_points": at.get("bonus"),
                                          "total": at["total"], "max_with_packs": at.get("max_with_packs")}))
    elif nt.get("value") is not None:
        lines.append(f"{label} {nt['value']:.1f} of {nt.get('of', 20)}")
    else:
        # the judge has not classed every adult answer (its calls failed, or its check did not pass): the answers
        # are kept, the number waits; the same command asks the judge again
        why = str(n.get("judge_pending") or n.get("reason") or "not judged yet")
        lines.append(f"Adult (NSFW+): no number yet ({why}); the answers are kept, and the same command asks the "
                     "judge again")
    lines.append(f"Judged by {judge_desc}; MakoBench's published scores use the local 8-bit (Q8) judge.")
    return lines


# ---------------------------------------------------------------- run
def run_engine(args: list[str], env: dict) -> tuple[int, list[str]]:
    """Run the engine with its output shown live; the first Ctrl-C asks it to finish what is in flight and stop
    (its drain), a second one stops it at once. Returns (exit code, the output's last lines)."""
    proc = subprocess.Popen(engine_argv(*args), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                            encoding="utf-8", errors="replace", env=env, start_new_session=True)
    tail: list[str] = []
    stops = {"n": 0}

    def on_int(_sig, _frm):
        stops["n"] += 1
        if stops["n"] == 1:
            # never print() here: the handler can run while the main thread is inside sys.stdout.flush(), and a
            # buffered write then raises "reentrant call". os.write is unbuffered and safe in a handler.
            try:
                os.write(sys.stdout.fileno(),
                         b"\nStopping: finishing the requests already sent (Ctrl-C again to stop at once) ...\n")
            except (OSError, ValueError):
                pass
            try:
                proc.send_signal(getattr(signal, "SIGUSR1", signal.SIGTERM))
            except ProcessLookupError:
                pass
        else:
            try:
                proc.terminate()
            except ProcessLookupError:
                pass
    old = signal.signal(signal.SIGINT, on_int)
    old_term = signal.signal(signal.SIGTERM, on_int)
    try:
        assert proc.stdout is not None
        jailed, told = bool(os.environ.get("CRUCIBLEFORGE_JAIL")), False
        last, t0, done = [time.time()], time.time(), threading.Event()

        def quiet_ticker():                  # the engine can be quiet for minutes (grading, judging): say so
            while not done.wait(5):
                if time.time() - last[0] >= 30:
                    say(f"  ... still working ({(time.time() - t0) / 60:.0f} min so far)")
                    last[0] = time.time()
        threading.Thread(target=quiet_ticker, daemon=True).start()
        for line in proc.stdout:
            last[0] = time.time()
            if jailed and "--allow-unsandboxed is in force" in line:
                # the engine's generic warning; in the image the jail stands in for bubblewrap (jail.py)
                if not told:
                    say("  coding answers run in the container's jail: another user, no network, limits")
                    told = True
                continue
            sys.stdout.write(line)
            sys.stdout.flush()
            tail.append(line.rstrip("\n"))
            del tail[:-400]
        rc = proc.wait()
    finally:
        done.set()
        signal.signal(signal.SIGINT, old)
        signal.signal(signal.SIGTERM, old_term)
    return (EXIT_STOPPED if stops["n"] else rc), tail


def diagnose(tail: list[str]) -> tuple[int, list[str]]:
    """(exit code, a plain sentence) for the engine failures a first-time user can fix; (RUN_FAILED, []) else."""
    blob = "\n".join(tail[-200:])
    # the engine's own cap refusal is phrased as an HTTP 402: test it before the provider's 402
    if re.search(r"SpendCapExceeded|spend cap (reached|exceeded)|cap reached", blob, re.I):
        return CODES["SPEND_CAP"], [HEADLINES["SPEND_CAP"] + " (about twice the estimate) and stopped sending requests.",
                                    "Run the same command again to continue; each run gets a fresh cap."]
    if re.search(r"\b402\b|insufficient credits|requires more credits|Payment Required", blob, re.I):
        return EXIT_NO_CREDIT, list(_no_credit_lines())
    if re.search(r"\b401\b.*(openrouter|key)|invalid api key|User not found", blob, re.I):
        return EXIT_KEY_REJECTED, [HEADLINES["KEY_REJECTED"] + " during the run. Copy it again from "
                                   "https://openrouter.ai/keys and run the same command: it continues."]
    if re.search(r"\b429\b|rate.?limit", blob, re.I):
        return CODES["RATE_LIMITED"], [f"OpenRouter {HEADLINES['RATE_LIMITED']}",
                                       "Wait a few minutes and run the same command again; nothing is lost."]
    if re.search(r"No space left on device|Disk quota exceeded|ENOSPC|EDQUOT", blob):
        return CODES["DISK_FULL"], [HEADLINES["DISK_FULL"] + " (the disk or your quota is full).",
                                    "Free some space (Docker Desktop: Settings > Resources > Disk image size, or "
                                    "docker system prune) and run the same command again."]
    return EXIT_RUN_FAILED, []


def files_lines() -> list[str]:
    host = os.environ.get("CRUCIBLEFORGE_HOST_DIR", "").strip()
    where = host or ("the folder you mounted with -v (for example makobench)" if IN_CONTAINER else str(results_dir().parent))
    res = results_dir()
    rel = res.relative_to(config_path().parent) if res.is_relative_to(config_path().parent) else res
    return [f"Open {rel}/report.html in {where} in your browser (report.md is the same board as text)."]


def parse(argv: list[str]) -> argparse.Namespace:
    ap = argparse.ArgumentParser(prog="test", description="Benchmark one model with no setup: an OpenRouter id, "
                                 "or --local URL MODEL for a model on your own machine. `check` instead of `test` "
                                 "runs only the free pre-flight.")
    ap.add_argument("model", help="an OpenRouter model id (e.g. openai/gpt-6-luna), or the model name with --local")
    ap.add_argument("--yes", "-y", action="store_true", help="do not ask before running")
    ap.add_argument("--check", action="store_true",
                    help="only the free pre-flight: folder, key, model, config, cost estimate; nothing is spent")
    ap.add_argument("--no-adult", "--no-nsfw-plus", dest="no_adult", action="store_true",
                    help="leave out NSFW+ (adult fiction, never part of Overall)")
    ap.add_argument("--local", metavar="URL", help="the model runs on an OpenAI-compatible server at URL "
                    "(Ollama http://localhost:11434, llama-server/LM Studio http://localhost:8080 or :1234)")
    ap.add_argument("--judge-local", nargs=2, metavar=("URL", "MODEL"), help="judge with a model on your own server")
    ap.add_argument("--smoke", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--no-ladder", action="store_true", help=argparse.SUPPRESS)
    return ap.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    a = parse(sys.argv[1:] if argv is None else argv)
    try:
        return _main(a)
    except Fail as f:
        say("", *f.lines)
        return f.code
    except KeyboardInterrupt:
        say("", HEADLINES["STOPPED"] + ": run the same command again to continue.")
        return EXIT_STOPPED


class Preflight:
    """The free checks, in order. `test` stops at the first one that needs fixing; `check` runs every check it can
    and prints a PASS/FIX list."""

    def __init__(self, collect: bool):
        self.collect, self.first_code, self.rows = collect, 0, []

    def step(self, name: str, fn, *needs: bool):
        if not all(needs):
            self._row("SKIP", name, ["(needs the step above)"])
            return None
        try:
            val, detail = fn()
        except Fail as f:
            if not self.collect:
                raise
            self.first_code = self.first_code or f.code
            self._row("FIX ", name, [ln for ln in f.lines if ln], code=f.code)
            return None
        self._row("PASS", name, [detail] if detail else [])
        return val if val is not None else True

    def _row(self, tag: str, name: str, lines: list[str], code: int | None = None):
        head = f"  {tag}  {name:<7}" + (f" {lines[0].strip()}" if lines else "") + (f"  (exit {code})" if code else "")
        say(head, *[f"           {ln.strip()}" for ln in lines[1:]])


def folder_check() -> tuple[Path, str]:
    from . import firstrun
    data = config_path().parent
    if IN_CONTAINER and str(data) == "/data" and not firstrun.mount_of("/data").point:
        raise Fail(CODES["NO_FOLDER"], *firstrun.no_folder_lines())
    try:
        data.mkdir(parents=True, exist_ok=True)
        probe = data / f".write-test-{os.getpid()}"
        probe.write_text("x", encoding="utf-8")
        probe.unlink()
    except OSError as e:
        if e.errno in (28, 122):                     # ENOSPC, EDQUOT
            raise fail("DISK_FULL", f" in the results folder ({e.strerror}).",
                       "  Free some space, then run the same command again.") from e
        if not IN_CONTAINER:
            raise Fail(EXIT_NOT_WRITABLE, f"Cannot write to the results folder {data} ({e.strerror or e}).",
                       "  Use a folder you own: set CRUCIBLEFORGE_CONFIG to a models.yaml path in it.") from e
        m = firstrun.mount_of(str(data))
        try:
            owner = data.stat().st_uid
        except OSError:
            owner = None
        raise Fail(EXIT_NOT_WRITABLE, *firstrun.folder_help(m, owner, os.getuid(), firstrun.selinux_active())) from e
    free = shutil.disk_usage(data).free / 2**20
    tmp = Path(os.environ.get("TMPDIR") or "/tmp")
    tfree = shutil.disk_usage(tmp).free / 2**20 if tmp.exists() else MIN_FREE_TMP_MB
    if free < MIN_FREE_DATA_MB or tfree < MIN_FREE_TMP_MB:
        low, have, need = ("results folder", free, MIN_FREE_DATA_MB) if free < MIN_FREE_DATA_MB else \
            ("container's scratch space", tfree, MIN_FREE_TMP_MB)
        raise fail("DISK_FULL", f" in the {low}: {have:.0f} MB free, the run needs about {need} MB.",
                   "  Free some space (Docker Desktop: Settings > Resources > Disk image size, or docker system prune)",
                   "  and run the same command again.")
    note = ""
    if os.environ.get("CF_FOLDER_SHARED") == "1":
        note = "; " + firstrun.shared_note(firstrun.mount_of("/data"))
    where = "your results folder" if IN_CONTAINER else str(data)
    return data, f"{where} is writable, {free / 1024:.0f} GB free{note}"


def _main(a: argparse.Namespace) -> int:
    t0 = time.time()
    say("CrucibleForge " + ("check (free: nothing is spent)" if a.check else "test") + ": the folder, the key, "
        "the model ...")
    from . import jail
    jail.install()                          # this process holds the key: non-dumpable in the image
    pf = Preflight(collect=a.check)
    data = pf.step("folder", folder_check)
    uses_or = not a.local or not a.judge_local
    raw = os.environ.get("OPENROUTER_API_KEY")
    keyinfo = None
    if uses_or:
        def _key():
            k = check_key(raw)
            bal = k.get("balance")
            skew = k.get("skew_s")
            det = "accepted by OpenRouter" + (f"; balance ${bal:.2f}" if bal is not None else "")
            if skew is not None and abs(skew) > 300:
                det += f"; note: this computer's clock is {abs(skew) / 60:.0f} min off (fix it if HTTPS errors appear)"
            return k, det
        keyinfo = pf.step("key", _key)
    notes: list[str] = []
    cfg = pf.step("config", lambda: (load_cfg(notes), "models.yaml is valid" + ("" if not notes else
                                                                                 " (see the notes below)")), bool(data))
    for n in notes:
        say(n)
    if os.environ.get("CRUCIBLEFORGE_OPENROUTER_URL") and cfg and "openrouter" in (cfg.get("providers") or {}):
        cfg["providers"]["openrouter"]["base_url"] = or_url()      # tests: a fake OpenRouter
        cfg["providers"]["openrouter"]["hosted"] = True            # priced and capped like the real one
        for c in ((cfg.get("judge") or {}).get("candidates") or []):  # the fake answers judges in schema mode only
            if c.get("provider") == "openrouter" and c.get("thinking"):
                c["thinking"] = False
        save_cfg(cfg)

    ctx = None

    def _model():
        nonlocal ctx
        if a.local:
            url = host_url(a.local)
            kenv = local_key_env("model", url)
            ctx = probe_local(url, a.model, kenv, "model")
            ensure_provider(cfg, "local", url, kenv)
            save_cfg(cfg)
            lab = register(cfg, label_for(a.model), "local", a.model, ctx, None)
            return (lab, f"{a.model} on your server ({url})"), f"{a.model} answers at {url}" + (
                f" (key from {kenv})" if kenv else "") + (
                f"; context window {ctx} tokens" if ctx else "; the server does not say its context window")
        info = find_model(openrouter_models(), a.model)
        pr = info.get("pricing") or {}
        try:
            price = (float(pr.get("prompt") or 0) * 1e6, float(pr.get("completion") or 0) * 1e6)
        except (TypeError, ValueError):
            price = (0.0, 0.0)
        ctx = (info.get("top_provider") or {}).get("context_length") or info.get("context_length")
        lab = register(cfg, label_for(info["id"]), "openrouter", info["id"], ctx, price)
        desc = f"{info['id']} on OpenRouter (${price[0]:.2f} in / ${price[1]:.2f} out per million tokens)"
        return (lab, desc), desc
    got = pf.step("model", _model, bool(cfg))
    label, model_desc = got if got else (None, None)

    judge = judge_desc = None

    def _judge():
        nonlocal judge, judge_desc
        c2 = load_cfg()
        if a.judge_local:
            jurl = host_url(a.judge_local[0])
            kenv = local_key_env("judge", jurl, host_url(a.local) if a.local else None)
            probe_local(jurl, a.judge_local[1], kenv, "judge")
            ensure_provider(c2, "localjudge", jurl, kenv)
            save_cfg(c2)
            judge, judge_desc = f"localjudge:{a.judge_local[1]}", f"{a.judge_local[1]} on your server"
        else:
            judge, judge_desc = DEFAULT_JUDGE, "Qwen 3.8 27B through OpenRouter"
        return None, judge_desc
    pf.step("judge", _judge, bool(cfg))

    common = ["--models", label or "", "--judge", judge or ""] + (["--no-nsfw-plus"] if a.no_adult else []) + \
        (["--smoke"] if a.smoke else []) + (["--no-ladder"] if a.no_ladder else [])
    plan: dict = {}
    extra: list[str] = []

    def _plan():
        nonlocal plan, extra
        say("  ....  plan    working out the questions, the cost and the time (can take a few minutes) ...")
        rc, out = engine_out("all", *common, "--plan", tick="still planning", phases=True)
        if rc != 0:
            raise Fail(EXIT_RUN_FAILED, "The plan failed:", *out.strip().splitlines()[-15:])
        plan = read_plan(out)
        extra = context_check(out, ctx, local=bool(a.local))
        mc, rc_ = caps(plan)
        cost = sum(v for v in (plan.get("model_usd"), plan.get("judge_usd")) if v is not None)
        det = (f"about ${cost:.2f} for the model and the judge" if cost else "no hosted cost") + \
            (f"; the run stops at ${rc_:.2f}" if rc_ else "")
        bal = (keyinfo or {}).get("balance") if isinstance(keyinfo, dict) else None
        if bal is not None and rc_ and bal < rc_:
            det += f" (your balance ${bal:.2f} may run out first: the run then stops and continues later)"
        return True, det
    planned = pf.step("plan", _plan, bool(label), bool(judge))
    for ln in extra:
        say(ln)
    if a.check:
        if pf.first_code:
            say("", "Fix the FIX lines above, then run the same command again.")
            return pf.first_code
        say("", "Ready. Start it with the same command, with test instead of check.")
        return EXIT_OK
    assert planned and label and judge
    model_cap, run_cap = caps(plan)
    say("", "Plan", f"  model:  {model_desc}", f"  judge:  {judge_desc}",
        "  NSFW+ (adult fiction): " + ("left out (--no-adult)" if a.no_adult else
                                       "included, scored apart from Overall (--no-adult leaves it out)"))
    if plan.get("model_usd") is not None:
        say(f"  model cost:  about ${plan['model_usd']:.2f} (up to ${plan['model_long_usd']:.2f} if it thinks at "
            f"length); stops at ${model_cap:.2f}")
    if plan.get("judge_usd") is not None:
        more = plan.get("judge_packs_max_usd")
        say(f"  judge cost:  about ${plan['judge_usd']:.2f}, up to ${plan['judge_max_usd']:.2f} for the standard test"
            + (f" and ${more:.2f} more if every bonus pack opens" if more else "")
            + " (billed by OpenRouter, apart from the model's cost)")
    if run_cap:
        say(f"  whole run stops at ${run_cap:.2f} of spending")
    if plan.get("minutes"):
        say(f"  time:  the plan's rough estimate is {plan['minutes']:.0f} min (hosted models are often faster)")
    if plan.get("owed"):
        say("  An earlier run in this folder stopped before the end: this command continues it.")
    for ln in plan.get("owed") or []:
        say("  earlier results in this folder: owed again: " + ln.replace("owed on resume:", "").strip()
            + f" ({plan.get('rows') or 'the'} rows to run are in the cost above)")
    say("  The coding answers run inside this container (see 'Safety' in docs/FIRST-RUN-DOCKER.md).")

    if not a.yes:
        # no terminal (-it left out): stdin is closed and input() meets end-of-file at once; with -i but no -t
        # the question still works line by line
        say("Continue? [y/N] ")
        try:
            ans = sys.stdin.readline()
        except (OSError, ValueError):
            ans = ""
        if not ans:
            raise fail("NO_TERMINAL", " Add --yes to run without asking, or start docker with -it.",
                       "  Nothing was spent.")
        if ans.strip().lower() not in ("y", "yes"):
            raise fail("NOT_CONFIRMED")

    env = dict(os.environ)
    if run_cap:
        spend_dir = data / "spend"
        spend_dir.mkdir(exist_ok=True)
        env["CF_SPEND_LEDGER"] = str(spend_dir / f"{label}-{time.strftime('%Y%m%d-%H%M%S')}.jsonl")
        env["CF_SPEND_GLOBAL_CAP_USD"] = f"{run_cap:.2f}"
    run_args = ["all", *common] + (["--spend-cap-usd", f"{model_cap:.2f}"] if model_cap else [])
    say("", "Running (the same command continues it later if it stops) ...", "")
    rc, tail = run_engine(run_args, env)
    mins = (time.time() - t0) / 60
    if rc == EXIT_STOPPED:
        say("", HEADLINES["STOPPED"] + ": run the same command again to continue.")
        return EXIT_STOPPED
    say("", f"Finished in {mins:.0f} min." if rc == 0 else f"{HEADLINES['RUN_FAILED']} {rc} after {mins:.0f} min.")
    code, why = diagnose(tail) if rc else (EXIT_OK, [])
    for ln in why:
        say(ln)
    say("", *summary_lines(label, judge_desc))
    jc = next((ln for ln in reversed(tail) if ln.startswith("judge cost, whole run")), None)
    if jc:
        say(jc.replace("judge cost, whole run (every judge phase)", "Judge cost").split(" (billed apart")[0])
    say(*files_lines())
    return EXIT_OK if rc == 0 else code


if __name__ == "__main__":
    sys.exit(main())
