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

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_NO_KEY = 20
EXIT_KEY_REJECTED = 21
EXIT_NO_CREDIT = 22
EXIT_UNKNOWN_MODEL = 23
EXIT_NOT_WRITABLE = 24
EXIT_NOT_CONFIRMED = 25
EXIT_LOCAL_UNREACHABLE = 26
EXIT_RUN_FAILED = 27
EXIT_STOPPED = 130

IN_CONTAINER = os.path.exists("/.dockerenv") or os.path.exists("/run/.containerenv") or \
    os.environ.get("CRUCIBLEFORGE_IN_CONTAINER") == "1"


class Fail(Exception):
    """A failure with a plain sentence for the user and an exit code."""

    def __init__(self, code: int, *lines: str):
        super().__init__(lines[0] if lines else "")
        self.code = code
        self.lines = lines


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
    try:
        return list(_get_json(or_url() + "/models").get("data") or [])
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise Fail(EXIT_RUN_FAILED, f"Could not reach OpenRouter to look the model up ({e}).",
                   "Check the internet connection and run the same command again.") from e


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
    lines = [f"OpenRouter has no model called '{wanted}'."]
    if near:
        lines.append("Did you mean one of these?")
        lines += [f"  {n}" for n in near]
    lines.append("The ids are listed at https://openrouter.ai/models (copy the id shown under the name).")
    raise Fail(EXIT_UNKNOWN_MODEL, *lines)


def check_key(key: str | None) -> None:
    """A missing, rejected or empty-credit OpenRouter key stops here, before anything is planned."""
    if not key:
        raise Fail(EXIT_NO_KEY, "No OpenRouter key was given.",
                   "Get one at https://openrouter.ai/keys, then put it in the command:",
                   "  docker run ... -e OPENROUTER_API_KEY=sk-or-... ...")
    try:
        info = _get_json(or_url() + "/key", key).get("data") or {}
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise Fail(EXIT_KEY_REJECTED, "OpenRouter rejected the key (it is wrong, revoked or mistyped).",
                       "Copy it again from https://openrouter.ai/keys and run the same command.") from e
        if e.code == 402:
            raise Fail(EXIT_NO_CREDIT, *_no_credit_lines()) from e
        return                                   # another status: let the run itself report it
    except (urllib.error.URLError, OSError, ValueError):
        return
    left = info.get("limit_remaining")
    if isinstance(left, (int, float)) and left <= 0:
        raise Fail(EXIT_NO_CREDIT, *_no_credit_lines())
    try:                                         # the account's balance (the key's own limit is checked above)
        cr = _get_json(or_url() + "/credits", key).get("data") or {}
        bal = float(cr["total_credits"]) - float(cr["total_usage"])
    except (urllib.error.URLError, OSError, ValueError, KeyError, TypeError):
        return                                   # not every key may read it: the run reports a 402 itself
    if bal <= 0:
        raise Fail(EXIT_NO_CREDIT, *_no_credit_lines())


def _no_credit_lines() -> tuple[str, ...]:
    return ("The OpenRouter account is out of credit (or the key's own limit is used up).",
            "Add credit at https://openrouter.ai/settings/credits, then run the same command: it continues.")


# ---------------------------------------------------------------- engine
def engine_argv(*args: str) -> list[str]:
    exe = shutil.which("crucibleforge")
    return ([exe] if exe else [sys.executable, "-m", "crucibleforge.cli"]) + list(args)


def engine_out(*args: str, env: dict | None = None, tick: str | None = None, every: float = 15.0) -> tuple[int, str]:
    """Run the engine, capture its output; with `tick`, print a 'still ...' line every `every` seconds meanwhile
    (the plan can take minutes and must not look stuck)."""
    stop = threading.Event()
    t0 = time.time()

    def ticker():
        while not stop.wait(every):
            say(f"  ... {tick} ({time.time() - t0:.0f} s)")
    th = threading.Thread(target=ticker, daemon=True) if tick else None
    if th:
        th.start()
    try:
        p = subprocess.run(engine_argv(*args), capture_output=True, text=True, encoding="utf-8", errors="replace",
                           env=env)
    finally:
        stop.set()
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def config_path() -> Path:
    from . import config
    return Path(os.environ.get("CRUCIBLEFORGE_CONFIG") or config.DEFAULT_CONFIG_PATH)


def results_dir() -> Path:
    return Path(os.environ.get("CRUCIBLEFORGE_RESULTS") or Path(config_path()).parent / "results")


def load_cfg() -> dict:
    import yaml
    p = config_path()
    if not p.exists():
        rc, out = engine_out("config", "--init")
        if not p.exists():
            raise Fail(EXIT_RUN_FAILED, f"Could not create {p}:", out.strip()[-400:])
    return yaml.safe_load(p.read_text(encoding="utf-8")) or {}


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


def probe_local(url: str, model: str) -> None:
    try:
        data = _get_json(url + "/models", timeout=10).get("data") or []
    except (urllib.error.URLError, OSError, ValueError) as e:
        hint = ("On Linux the server must listen on all addresses, not only localhost (Ollama: OLLAMA_HOST=0.0.0.0; "
                "llama-server: --host 0.0.0.0; LM Studio: 'Serve on local network'). With rootless Docker the "
                "container cannot reach localhost at all: give your computer's network address instead "
                "(http://<that address>:11434 for Ollama; `hostname -I` prints it)." if IN_CONTAINER else "")
        raise Fail(EXIT_LOCAL_UNREACHABLE, f"Nothing answered at {url} ({e}).",
                   "Is the model server running?" + (" " + hint if hint else "")) from e
    ids = [d.get("id") for d in data if d.get("id")]
    if ids and model not in ids:
        near = difflib.get_close_matches(model, ids, n=5, cutoff=0.3) or ids[:5]
        raise Fail(EXIT_UNKNOWN_MODEL, f"The server at {url} has no model called '{model}'.",
                   "It lists: " + ", ".join(near))


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
        lines.append(f"Adult (NSFW+): {n.get('reason') or n.get('judge_check') or 'not judged yet'}")
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


def diagnose(tail: list[str]) -> list[str]:
    """A plain sentence for the engine failures a first-time user can fix."""
    blob = "\n".join(tail[-200:])
    # the engine's own cap refusal is phrased as an HTTP 402: test it before the provider's 402
    if re.search(r"SpendCapExceeded|spend cap (reached|exceeded)|cap reached", blob, re.I):
        return ["The run reached its spend cap (about twice the estimate) and stopped sending requests.",
                "Run the same command again to continue; each run gets a fresh cap."]
    if re.search(r"\b402\b|insufficient credits|requires more credits|Payment Required", blob, re.I):
        return list(_no_credit_lines())
    if re.search(r"\b401\b.*(openrouter|key)|invalid api key|User not found", blob, re.I):
        return ["OpenRouter rejected the key during the run. Copy it again from https://openrouter.ai/keys."]
    if re.search(r"No space left on device", blob):
        return ["The disk is full. Free some space and run the same command again."]
    return []


def files_lines() -> list[str]:
    host = os.environ.get("CRUCIBLEFORGE_HOST_DIR", "").strip()
    where = host or ("the folder you mounted with -v (for example makobench)" if IN_CONTAINER else str(results_dir().parent))
    res = results_dir()
    rel = res.relative_to(config_path().parent) if res.is_relative_to(config_path().parent) else res
    return [f"Open {rel}/report.html in {where} in your browser (report.md is the same board as text)."]


def parse(argv: list[str]) -> argparse.Namespace:
    ap = argparse.ArgumentParser(prog="test", description="Benchmark one model with no setup: an OpenRouter id, "
                                 "or --local URL MODEL for a model on your own machine.")
    ap.add_argument("model", help="an OpenRouter model id (e.g. openai/gpt-6-luna), or the model name with --local")
    ap.add_argument("--yes", "-y", action="store_true", help="do not ask before running")
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
        say("", "Stopped. Nothing is lost: run the same command again to continue.")
        return EXIT_STOPPED


def _main(a: argparse.Namespace) -> int:
    t0 = time.time()
    say("CrucibleForge test: checking the folder, the key and the model ...")
    from . import jail
    jail.install()                          # this process holds the key: non-dumpable in the image
    data = config_path().parent
    try:
        data.mkdir(parents=True, exist_ok=True)
        probe = data / f".write-test-{os.getpid()}"
        probe.write_text("x", encoding="utf-8")
        probe.unlink()
    except OSError as e:
        raise Fail(EXIT_NOT_WRITABLE, f"Cannot write to the results folder ({e.strerror or e}).",
                   "On Fedora/RHEL (SELinux) add :Z to the folder: -v \"$PWD/makobench:/data:Z\".",
                   "Otherwise make sure the folder belongs to you: mkdir makobench before docker run.") from e
    key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    uses_or = not a.local or not a.judge_local
    if uses_or:
        check_key(key)
    cfg = load_cfg()
    if os.environ.get("CRUCIBLEFORGE_OPENROUTER_URL") and "openrouter" in (cfg.get("providers") or {}):
        cfg["providers"]["openrouter"]["base_url"] = or_url()      # tests: a fake OpenRouter
        cfg["providers"]["openrouter"]["hosted"] = True            # priced and capped like the real one
        for c in ((cfg.get("judge") or {}).get("candidates") or []):  # the fake answers judges in schema mode only
            if c.get("provider") == "openrouter" and c.get("thinking"):
                c["thinking"] = False
        save_cfg(cfg)

    if a.local:
        url = host_url(a.local)
        probe_local(url, a.model)
        ensure_provider(cfg, "local", url)
        save_cfg(cfg)
        label = register(cfg, label_for(a.model), "local", a.model, None, None)
        model_desc = f"{a.model} on your server ({url})"
    else:
        info = find_model(openrouter_models(), a.model)
        pr = info.get("pricing") or {}
        try:
            price = (float(pr.get("prompt") or 0) * 1e6, float(pr.get("completion") or 0) * 1e6)
        except (TypeError, ValueError):
            price = (0.0, 0.0)
        ctx = (info.get("top_provider") or {}).get("context_length") or info.get("context_length")
        label = register(cfg, label_for(info["id"]), "openrouter", info["id"], ctx, price)
        model_desc = f"{info['id']} on OpenRouter (${price[0]:.2f} in / ${price[1]:.2f} out per million tokens)"

    cfg = load_cfg()
    if a.judge_local:
        jurl = host_url(a.judge_local[0])
        probe_local(jurl, a.judge_local[1])
        ensure_provider(cfg, "localjudge", jurl)
        save_cfg(cfg)
        judge, judge_desc = f"localjudge:{a.judge_local[1]}", f"{a.judge_local[1]} on your server"
    else:
        judge, judge_desc = DEFAULT_JUDGE, "Qwen 3.8 27B through OpenRouter"

    common = ["--models", label, "--judge", judge] + (["--no-nsfw-plus"] if a.no_adult else []) + \
        (["--smoke"] if a.smoke else []) + (["--no-ladder"] if a.no_ladder else [])
    say(f"Planning the test of {model_desc} ...")
    rc, out = engine_out("all", *common, "--plan", tick="still planning")
    if rc != 0:
        raise Fail(EXIT_RUN_FAILED, "The plan failed:", *out.strip().splitlines()[-15:])
    plan = read_plan(out)
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
    for ln in plan.get("owed") or []:
        say("  earlier results in this folder: owed again: " + ln.replace("owed on resume:", "").strip()
            + f" ({plan.get('rows') or 'the'} rows to run are in the cost above)")
    say("  The coding answers run inside this container (see 'Safety' in docs/FIRST-RUN-DOCKER.md).")

    if not a.yes:
        if not sys.stdin.isatty():
            raise Fail(EXIT_NOT_CONFIRMED, "No terminal to ask \"Continue?\" on. Add --yes to run without asking "
                       "(or start docker with -it).")
        try:
            ans = input("Continue? [y/N] ").strip().lower()
        except EOFError:
            ans = ""
        if ans not in ("y", "yes"):
            raise Fail(EXIT_NOT_CONFIRMED, "Not started. Nothing was spent.")

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
        say("", "Stopped. Nothing is lost: run the same command again to continue.")
        return EXIT_STOPPED
    say("", f"Finished in {mins:.0f} min." if rc == 0 else f"The run ended with code {rc} after {mins:.0f} min.")
    for ln in diagnose(tail) if rc else []:
        say(ln)
    say("", *summary_lines(label, judge_desc))
    jc = next((ln for ln in reversed(tail) if ln.startswith("judge cost, whole run")), None)
    if jc:
        say(jc.replace("judge cost, whole run (every judge phase)", "Judge cost").split(" (billed apart")[0])
    say(*files_lines())
    return EXIT_OK if rc == 0 else (EXIT_NO_CREDIT if diagnose(tail)[:1] == [_no_credit_lines()[0]]
                                    else EXIT_RUN_FAILED)


if __name__ == "__main__":
    sys.exit(main())
