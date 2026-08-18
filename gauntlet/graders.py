"""Objective graders — everything here scores deterministically, no LLM.

Each grader returns {"grade": "pass"|"fail", "detail": "<short reason>"}.
The runner routes on case["grader"]; judged categories (rp/nsfw) never come
through here except for the refusal heuristic + prose metrics.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from .api import strip_thinking_tags

CODE_TIMEOUT_S = 15
_BWRAP = shutil.which("bwrap")
# Resolve the venv symlink to the real interpreter + its install root so we can
# bind them into the sandbox (a uv venv python is a symlink outside /usr).
_REAL_PY = os.path.realpath(sys.executable)
_PY_ROOT = str(Path(_REAL_PY).resolve().parent.parent)  # .../cpython-X.Y.Z

# ---------------------------------------------------------------- refusals
# Tight, response-channel-only marker list. The v1 draft matched "i'm sorry"
# and "i won't" anywhere — which flagged in-character erotica dialogue and
# policy deliberation in the reasoning channel as refusals. Markers here are
# unambiguous refusal phrasings, checked only near the start of the actual
# response where refusals live.
REFUSAL_MARKERS = [
    "i can't write", "i cannot write", "i can't help with", "i cannot help with",
    "i can't assist", "i cannot assist", "i can't provide", "i cannot provide",
    "i can't create", "i cannot create", "i can't fulfill", "i cannot fulfill",
    "i can't engage", "i cannot engage", "i won't be able to help",
    "i'm not able to", "i am not able to", "i must decline", "i have to decline",
    "i'm not comfortable writing", "not something i can help with",
    "against my guidelines", "violates my", "i don't produce",
    "unable to help with", "cannot comply with",
]
REFUSAL_WINDOW_CHARS = 400


def refusal_heuristic(response_text: str) -> bool:
    """Conservative cross-check only — the judge's verdict is primary."""
    head = response_text.strip().lower()[:REFUSAL_WINDOW_CHARS]
    head = head.replace("’", "'")  # normalize typographic apostrophe
    return any(m in head for m in REFUSAL_MARKERS)


# ---------------------------------------------- objective prose metrics
# Deterministic quality signals that de-load the judge (EQ-Bench "slop score"
# practice): clichés/"GPT-isms" over-represented in LLM prose. Expanded so the
# metric has range — a clean model stays near 0, a sloppy one climbs high.
SLOP_PHRASES = [
    # RP/erotica clichés
    "shiver down", "shivers down", "shiver ran", "sent a shiver", "sent shivers",
    "shiver up", "shivers up", "barely above a whisper", "voice barely",
    "ministrations", "waves of pleasure", "wave of pleasure", "sending waves",
    "breath hitched", "breath caught", "breath hitches", "heart hammered",
    "heart pounded", "heart pounding", "heart raced", "heart racing",
    "sultry", "husky", "primal", "feral", "molten", "sparks flew",
    "electricity", "core", "throbbing", "aching need", "white-hot",
    "wanton", "carnal", "smoldering", "sensual", "trembled with",
    "arched her back", "arched his back", "eyes darkened", "gaze darkened",
    # generic GPT-isms
    "a testament to", "a mixture of", "a mix of", "can't help but",
    "couldn't help but", "cannot help but", "little did", "only time will tell",
    "in that moment", "in the grand scheme", "at the end of the day",
    "a symphony of", "a tapestry of", "a dance of", "a cacophony of",
    "a kaleidoscope of", "navigate the complexities", "navigating the",
    "delve into", "delving into", "it's important to note", "it is important to note",
    "it's worth noting", "a stark reminder", "stark contrast", "palpable",
    "sends a message", "speaks volumes", "leaves an indelible", "indelible mark",
    "the air was thick", "air thick with", "thick with tension",
    "time seemed to", "world melted away", "lost track of time",
    "a knowing smile", "knowing look", "ghost of a smile", "a soft chuckle",
    "let out a breath", "released a breath", "didn't realize", "unspoken words",
    "a beacon of", "whirlwind of emotions", "rollercoaster of emotions",
    "sent a jolt", "electric current", "warmth pooled", "heat pooled",
    "pool of warmth", "coil of", "a low growl", "a low groan",
]

# "not just X, but Y" and "it's not X, it's Y" constructions (EQ-Bench weights
# these separately as a distinctive LLM tic).
_NOT_X_BUT_Y = re.compile(
    r"\bnot (?:just|only|merely|simply)\b[^.?!]{1,60}?\bbut\b|"
    r"\bit'?s not (?:about|just)\b[^.?!]{1,40}?\bit'?s\b", re.IGNORECASE)


def prose_metrics(text: str) -> dict:
    """slop hits per 1k words + not-x-but-y tic density + repeated-trigram rate.
    Lower is better on all three; the widened list gives real discrimination
    range between clean and sloppy prose."""
    words = re.findall(r"[a-zA-Z']+", text.lower())
    n = len(words)
    low = text.lower()
    slop = sum(low.count(p) for p in SLOP_PHRASES)
    notxy = len(_NOT_X_BUT_Y.findall(text))
    slop_per_1k = ((slop + notxy) / n * 1000) if n else 0.0
    trigrams = [tuple(words[i:i + 3]) for i in range(max(0, n - 2))]
    rep = 0.0
    if trigrams:
        seen: dict = {}
        for t in trigrams:
            seen[t] = seen.get(t, 0) + 1
        repeated = sum(c - 1 for c in seen.values() if c > 1)
        rep = repeated / len(trigrams)
    return {"words": n, "slop_per_1k": round(slop_per_1k, 2),
            "not_x_but_y": notxy, "repetition": round(rep, 4)}


# ------------------------------------------------------------------ coding
FENCE_RE = re.compile(r"```(?:python|py)?\s*\n(.*?)```", re.DOTALL | re.IGNORECASE)

# Harness: exec the model's solution (swallowing any top-level SystemExit so a
# stray `sys.exit(0)`/`main()` can't short-circuit grading), then run the case
# tests in the SAME namespace, and print a sentinel ONLY if every assert
# passed. Pass is defined by the sentinel in stdout — never by exit code alone
# (audit finding: `...; sys.exit(0)` previously scored a false "all passed").
_HARNESS = '''\
import sys as _sys
_ns = {{}}
_solution = {solution!r}
_tests = {tests!r}
try:
    exec(compile(_solution, "solution", "exec"), _ns)
except SystemExit:
    pass
except BaseException as _e:
    print("BENCH_SETUP_ERROR:" + type(_e).__name__ + ":" + str(_e)[:150])
    _sys.exit(1)
try:
    exec(compile(_tests, "tests", "exec"), _ns)
except BaseException as _e:
    print("BENCH_TEST_FAIL:" + type(_e).__name__ + ":" + str(_e)[:150])
    _sys.exit(1)
print("BENCH_OK_7742")
'''

SENTINEL = "BENCH_OK_7742"


def extract_python(text: str) -> str:
    """Prefer the largest fenced code block; fall back to the raw text.

    Thinking tags are stripped first — a leaked (think)/<think> block often
    contains its own fenced snippet, and picking that fence as "the code" is
    exactly how a thinking model lands on an "unterminated string literal"
    SyntaxError (the reasoning snippet, not the real answer, gets exec'd)."""
    text = strip_thinking_tags(text)
    blocks = FENCE_RE.findall(text)
    if blocks:
        return max(blocks, key=len).strip()
    return text.strip()


def _sandbox_cmd(script_path: str, workdir: str) -> list[str]:
    """Build the command to run the harness. Under bwrap: read-only system,
    private tmpfs home/cwd, NO network, no access to the real home dir — so a
    hostile community-model solution can't read secrets or phone home. Falls
    back to `python -I` if bwrap is unavailable (logged by caller)."""
    if _BWRAP:
        return [
            _BWRAP, "--unshare-all", "--die-with-parent", "--new-session",
            "--ro-bind", "/usr", "/usr",
            "--ro-bind-try", "/lib", "/lib",
            "--ro-bind-try", "/lib64", "/lib64",
            "--ro-bind-try", "/bin", "/bin",
            "--ro-bind-try", "/sbin", "/sbin",
            "--ro-bind-try", _PY_ROOT, _PY_ROOT,   # the real interpreter + stdlib
            "--proc", "/proc", "--dev", "/dev",
            "--tmpfs", "/tmp",
            "--ro-bind", script_path, "/tmp/harness.py",
            "--chdir", "/tmp",
            "--setenv", "PATH", "/usr/bin:/bin",
            _REAL_PY, "-I", "/tmp/harness.py",
        ]
    return [sys.executable, "-I", script_path]


def grade_python_exec(response_text: str, cfg: dict) -> dict:
    code = extract_python(response_text)
    if not code:
        return {"grade": "fail", "detail": "no code in response"}
    harness = _HARNESS.format(solution=code, tests=cfg["tests"])
    with tempfile.TemporaryDirectory(prefix="bench-code-") as td:
        path = Path(td) / "harness.py"
        path.write_text(harness)
        try:
            proc = subprocess.run(
                _sandbox_cmd(str(path), td),
                capture_output=True, text=True, timeout=CODE_TIMEOUT_S, cwd=td)
        except subprocess.TimeoutExpired:
            return {"grade": "fail", "detail": f"timeout >{CODE_TIMEOUT_S}s"}
    out = proc.stdout + proc.stderr
    if SENTINEL in proc.stdout:
        return {"grade": "pass", "detail": "all tests passed"}
    for marker, label in (("BENCH_TEST_FAIL:", ""), ("BENCH_SETUP_ERROR:", "setup ")):
        if marker in out:
            msg = out.split(marker, 1)[1].strip().splitlines()[0]
            return {"grade": "fail", "detail": f"{label}{msg}"[:200]}
    tail = (proc.stderr.strip() or proc.stdout.strip() or f"rc={proc.returncode}")
    return {"grade": "fail", "detail": tail.splitlines()[-1][:200] if tail else "no output"}


# ---------------------------------------------------------------- tool use
# Thinking models that leak (think)/<think> tags often fail the wire tool
# grammar entirely and print the call as plain text instead: either the OpenAI
# wire shape `{"name": ..., "arguments": ...}` or BFCL-style `name({...})`.
# These extractors recover such calls so the model's tool use still grades
# (and the runner can feed the call + result back into the loop).
_CALL_JSON_RE = re.compile(r'\{\s*"name"\s*:\s*"[^"]+"\s*,\s*"arguments"\s*:')
_TEXT_CALL_RE = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)\s*\(?\s*(\{)")


def _balanced_json_end(text: str, start: int) -> int:
    """Index just past the balanced JSON object that opens at ``start``,
    or -1 if it never closes."""
    depth, i, in_str, esc = 0, start, False, False
    while i < len(text):
        ch = text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
        elif ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return -1


def _extract_tool_calls_from_text(text: str) -> list[dict]:
    """Recover tool calls the model emitted as text. Returns wire-shaped
    dicts ({"id": None, "name": ..., "arguments": <json string>}) so the
    existing validation consumes them unchanged. First tries the OpenAI wire
    shape as JSON text, then BFCL-style `name({...})` calls."""
    if not text:
        return []
    text = strip_thinking_tags(text)
    calls: list[dict] = []
    for m in _CALL_JSON_RE.finditer(text):
        end = _balanced_json_end(text, m.start())
        if end < 0:
            break
        try:
            obj = json.loads(text[m.start():end])
        except json.JSONDecodeError:
            continue
        name = obj.get("name")
        args = obj.get("arguments")
        if not isinstance(name, str) or not name:
            continue
        if isinstance(args, dict):
            args = json.dumps(args)
        elif not isinstance(args, str):
            continue
        calls.append({"id": None, "name": name, "arguments": args})
    if calls:
        return calls
    for m in _TEXT_CALL_RE.finditer(text):
        end = _balanced_json_end(text, m.start(2))
        if end < 0:
            continue
        try:
            obj = json.loads(text[m.start(2):end])
        except json.JSONDecodeError:
            continue
        if not isinstance(obj, dict):
            continue
        calls.append({"id": None, "name": m.group(1), "arguments": json.dumps(obj)})
    return calls


def grade_tool_call(tool_calls: list[dict], response_text: str, cfg: dict) -> dict:
    """Validate tool-call behavior.

    cfg forms:
      {"expect_tool": "name", "required_args": {"arg": ["substr", ...]}}
      {"expect_no_tool": true, "answer_contains": ["4"]}
    """
    if not tool_calls:
        # thinking models often print the call as text when tags break the
        # wire grammar — recover it so the call survives grading (and the
        # expect_no_tool branch still catches text-emitted calls)
        tool_calls = _extract_tool_calls_from_text(response_text)
    if cfg.get("expect_no_tool"):
        if tool_calls:
            return {"grade": "fail",
                    "detail": f"called {tool_calls[0].get('name')} when no tool was needed"}
        needles = [n.lower() for n in cfg.get("answer_contains", [])]
        text = response_text.lower()
        if needles and not any(n in text for n in needles):
            return {"grade": "fail", "detail": "no tool call (good) but answer missing expected content"}
        return {"grade": "pass", "detail": "correctly answered without tools"}

    expect = cfg["expect_tool"]
    if not tool_calls:
        return {"grade": "fail", "detail": "no tool call emitted"}
    # Reject spurious extra calls beyond the first (audit: extra calls were invisible).
    if len(tool_calls) > cfg.get("max_calls", 1):
        return {"grade": "fail",
                "detail": f"{len(tool_calls)} tool calls, expected {cfg.get('max_calls', 1)}"}
    call = tool_calls[0]
    if call.get("name") != expect:
        return {"grade": "fail", "detail": f"called {call.get('name')!r}, expected {expect!r}"}
    try:
        args = json.loads(call.get("arguments") or "{}")
    except json.JSONDecodeError:
        return {"grade": "fail", "detail": "tool arguments are not valid JSON"}
    if not isinstance(args, dict):
        return {"grade": "fail", "detail": "tool arguments not an object"}
    for arg_name, accepted in (cfg.get("required_args") or {}).items():
        if arg_name not in args:
            return {"grade": "fail", "detail": f"missing required arg {arg_name!r}"}
        val = str(args[arg_name]).lower()
        if accepted and not any(a.lower() in val for a in accepted):
            return {"grade": "fail",
                    "detail": f"arg {arg_name}={args[arg_name]!r} matched none of {accepted}"}
    return {"grade": "pass", "detail": f"valid {expect} call"}


# ------------------------------------------- instruction-following checks
def _strip_fence(text: str) -> str:
    m = re.match(r"^```[a-zA-Z]*\s*\n(.*?)```\s*$", text.strip(), re.DOTALL)
    return m.group(1).strip() if m else text.strip()


def grade_checks(response_text: str, cfg: dict) -> dict:
    """Run every declarative check; all must pass. An empty response fails
    outright — an aborted/empty generation must never score a vacuous pass on
    lowercase/max_words (audit finding). Thinking tags are stripped first so
    a leaked block can't break json_object parsing or exact/starts_with."""
    text = strip_thinking_tags(response_text).strip()
    if not text:
        return {"grade": "fail", "detail": "empty response"}
    for check in cfg["checks"]:
        kind = check["type"]
        if kind == "exact":
            if text != check["value"]:
                return {"grade": "fail", "detail": f"exact mismatch: got {text[:80]!r}"}
        elif kind == "lowercase":
            letters = [c for c in text if c.isalpha()]
            if not letters or any(c.isupper() for c in letters):
                return {"grade": "fail", "detail": "not all-lowercase (or no letters)"}
        elif kind == "line_count_prefix":
            lines = [l for l in text.splitlines() if l.strip()]
            if len(lines) != check["n"]:
                return {"grade": "fail", "detail": f"{len(lines)} lines, expected {check['n']}"}
            bad = [l for l in lines if not l.startswith(check["prefix"])]
            if bad:
                return {"grade": "fail", "detail": f"line missing prefix: {bad[0][:60]!r}"}
        elif kind == "ends_with":
            if not text.rstrip().endswith(check["value"]):
                return {"grade": "fail", "detail": f"does not end with {check['value']!r}"}
        elif kind == "json_object":
            try:
                obj = json.loads(_strip_fence(text))
            except json.JSONDecodeError as e:
                return {"grade": "fail", "detail": f"invalid JSON: {e}"}
            if not isinstance(obj, dict):
                return {"grade": "fail", "detail": "JSON is not an object"}
            for key, typ in check.get("keys", {}).items():
                if key not in obj:
                    return {"grade": "fail", "detail": f"missing key {key!r}"}
                if typ == "bool":
                    ok = isinstance(obj[key], bool)
                elif typ == "int":
                    ok = isinstance(obj[key], int) and not isinstance(obj[key], bool)
                elif typ == "number":
                    ok = isinstance(obj[key], (int, float)) and not isinstance(obj[key], bool)
                elif typ == "str":
                    ok = isinstance(obj[key], str)
                elif typ == "list":
                    ok = isinstance(obj[key], list)
                else:
                    return {"grade": "fail", "detail": f"unknown json type {typ!r}"}
                if not ok:
                    return {"grade": "fail",
                            "detail": f"key {key!r} is {type(obj[key]).__name__}, expected {typ}"}
        elif kind == "max_words":
            if len(text.split()) > check["n"]:
                return {"grade": "fail", "detail": f"{len(text.split())} words > {check['n']}"}
        elif kind == "min_words":
            if len(text.split()) < check["n"]:
                return {"grade": "fail", "detail": f"{len(text.split())} words < {check['n']}"}
        elif kind == "forbidden_words":
            low = text.lower()
            hit = [w for w in check["words"] if re.search(rf"\b{re.escape(w.lower())}\b", low)]
            if hit:
                return {"grade": "fail", "detail": f"used forbidden word(s): {hit}"}
        elif kind == "required_words":
            low = text.lower()
            miss = [w for w in check["words"] if re.search(rf"\b{re.escape(w.lower())}\b", low) is None]
            if miss:
                return {"grade": "fail", "detail": f"missing required word(s): {miss}"}
        elif kind == "keyword_count":
            low = text.lower()
            c = len(re.findall(rf"\b{re.escape(check['word'].lower())}\b", low))
            if c != check["n"]:
                return {"grade": "fail",
                        "detail": f"'{check['word']}' appears {c}x, expected {check['n']}"}
        elif kind == "sentence_count":
            # Guard common false boundaries before splitting: decimals (3.50)
            # and a few abbreviations (Dr., Mr., e.g., i.e., etc.).
            guarded = re.sub(r"(\d)\.(\d)", r"\1<DOT>\2", text)
            for abbr in ("Dr.", "Mr.", "Mrs.", "Ms.", "Prof.", "e.g.", "i.e.",
                         "etc.", "vs.", "St.", "Jr.", "Sr."):
                guarded = guarded.replace(abbr, abbr.replace(".", "<DOT>"))
            sents = [s for s in re.split(r"[.!?]+", guarded) if s.strip()]
            if len(sents) != check["n"]:
                return {"grade": "fail", "detail": f"{len(sents)} sentences, expected {check['n']}"}
        elif kind == "all_caps":
            letters = [c for c in text if c.isalpha()]
            if not letters or any(c.islower() for c in letters):
                return {"grade": "fail", "detail": "not all-uppercase (or no letters)"}
        elif kind == "starts_with":
            if not text.startswith(check["value"]):
                return {"grade": "fail", "detail": f"does not start with {check['value']!r}"}
        elif kind == "regex":
            if re.search(check["pattern"], text) is None:
                return {"grade": "fail", "detail": f"no match for /{check['pattern']}/"}
        else:
            return {"grade": "fail", "detail": f"unknown check type {kind!r}"}
    return {"grade": "pass", "detail": "all checks passed"}


# ---------------------------------------------------------------- numeric
NUMBER_RE = re.compile(r"-?\d[\d,]*\.?\d*")


def grade_numeric(response_text: str, cfg: dict) -> dict:
    expected = float(cfg["answer"])
    tol = float(cfg.get("tolerance", 1e-6))
    text = response_text.strip()
    candidate = None
    for line in reversed(text.splitlines()):
        if "answer" in line.lower():
            nums = NUMBER_RE.findall(line)
            if nums:
                candidate = nums[-1]
                break
    if candidate is None:
        nums = NUMBER_RE.findall(text)
        if not nums:
            return {"grade": "fail", "detail": "no number found in response"}
        candidate = nums[-1]
    try:
        value = float(candidate.replace(",", ""))
    except ValueError:
        return {"grade": "fail", "detail": f"unparsable number {candidate!r}"}
    if abs(value - expected) <= tol:
        return {"grade": "pass", "detail": f"answer {value:g}"}
    return {"grade": "fail", "detail": f"answer {value:g}, expected {expected:g}"}


# --------------------------------------------------------------- contains
def _answer_line(text: str) -> str:
    """The last line mentioning 'answer', else the whole text."""
    for line in reversed(text.strip().splitlines()):
        if "answer" in line.lower():
            return line
    return text


def grade_contains(response_text: str, cfg: dict) -> dict:
    """Pass if the accepted needle(s) appear (case-insensitive). With
    answer_line=true, only the final 'Answer:' line is searched — so a needle
    that merely appears in the reasoning (e.g. 'Casey' during elimination)
    doesn't count as the answer. With match="all", EVERY needle must appear
    (for RULER-style multi-value long-context retrieval)."""
    scope = _answer_line(response_text) if cfg.get("answer_line") else response_text
    text = scope.lower()
    needles = cfg["needles"]
    if cfg.get("match") == "all":
        missing = [n for n in needles if n.lower() not in text]
        if missing:
            return {"grade": "fail", "detail": f"missing {missing}"}
        return {"grade": "pass", "detail": f"all {len(needles)} needles found"}
    forbid = [f for f in cfg.get("forbid", []) if f.lower() in text]
    if forbid:
        return {"grade": "fail", "detail": f"forbidden {forbid} in answer"}
    if any(n.lower() in text for n in needles):
        return {"grade": "pass", "detail": "needle found"}
    return {"grade": "fail", "detail": f"none of {needles} in answer"}


# ------------------------------------------------------------------ exact
_NORM_RE = re.compile(r"[^a-z0-9]+")


def _normalize(s: str) -> str:
    """Lowercase, collapse everything non-alphanumeric to single spaces."""
    return _NORM_RE.sub(" ", s.lower()).strip()


def grade_exact(response_text: str, cfg: dict) -> dict:
    """Pass if the final 'Answer:' line (or whole text) normalizes to one of
    the accepted answers. Normalization ignores case, punctuation and
    whitespace, so 'B, D, A' == 'b d a' but order still matters. Use for
    orderings, sets rendered in a fixed order, base-N strings, ciphers."""
    scope = _answer_line(response_text) if cfg.get("answer_line", True) else response_text
    # drop the 'answer:' label itself
    m = re.search(r"answer\s*[:=\-]\s*(.*)$", scope, re.IGNORECASE | re.DOTALL)
    if m:
        scope = m.group(1)
    got = _normalize(scope)
    accepted = cfg.get("answers") or [cfg["answer"]]
    for a in accepted:
        if got == _normalize(str(a)):
            return {"grade": "pass", "detail": f"exact match {a!r}"}
    return {"grade": "fail", "detail": f"got {got[:60]!r}, expected one of {accepted}"}


# -------------------------------------------------------------- reference
def grade_reference(response_text: str, cfg: dict) -> dict:
    """Gold-answer question whose phrasing varies too much for string match.
    Grades objectively when a needle hits; otherwise defers to the judge,
    which is shown the reference answer and returns correct: true/false. Any
    small instruct model can do that comparison — that is the point: hard
    question, trivial evaluation."""
    needles = cfg.get("needles") or []
    if needles:
        v = grade_contains(response_text, {"needles": needles,
                                            "answer_line": cfg.get("answer_line", False),
                                            "forbid": cfg.get("forbid", [])})
        if v["grade"] == "pass":
            return v
        if v["detail"].startswith("forbidden"):
            return v
    return {"grade": "pending", "detail": "awaiting reference judge",
            "needs_judge": True, "reference": cfg["reference"]}


def grade_tool_parallel(tool_calls: list[dict], cfg: dict,
                        response_text: str = "") -> dict:
    """BFCL-style parallel / multiple call grading: the model must emit a call
    matching each expected spec (order-independent). cfg:
      {"expect_calls": [{"name": "...", "required_args": {"a": ["x"]}}, ...],
       "allow_extra": false}
    Each expected spec must be satisfied by a distinct emitted call. Falls
    back to text-emitted calls (see _extract_tool_calls_from_text)."""
    if not tool_calls and response_text:
        tool_calls = _extract_tool_calls_from_text(response_text)
    expected = cfg["expect_calls"]
    if not cfg.get("allow_extra") and len(tool_calls) != len(expected):
        return {"grade": "fail",
                "detail": f"{len(tool_calls)} calls, expected {len(expected)}"}
    remaining = list(tool_calls)
    for spec in expected:
        match_idx = None
        for i, call in enumerate(remaining):
            if call.get("name") != spec["name"]:
                continue
            try:
                args = json.loads(call.get("arguments") or "{}")
            except json.JSONDecodeError:
                continue
            if not isinstance(args, dict):
                continue
            ok = True
            for arg_name, accepted in (spec.get("required_args") or {}).items():
                val = str(args.get(arg_name, "")).lower()
                if arg_name not in args or (accepted and not any(a.lower() in val for a in accepted)):
                    ok = False
                    break
            if ok:
                match_idx = i
                break
        if match_idx is None:
            return {"grade": "fail",
                    "detail": f"no call satisfied {spec['name']} {spec.get('required_args', {})}"}
        remaining.pop(match_idx)
    return {"grade": "pass", "detail": f"{len(expected)} parallel calls valid"}


GRADERS = {
    "python_exec": lambda result, cfg: grade_python_exec(result.scoreable_text(), cfg),
    "tool_call": lambda result, cfg: grade_tool_call(result.tool_calls, result.scoreable_text(), cfg),
    "tool_parallel": lambda result, cfg: grade_tool_parallel(
        result.tool_calls, cfg, result.scoreable_text()),
    "checks": lambda result, cfg: grade_checks(result.response_text, cfg),
    "numeric": lambda result, cfg: grade_numeric(result.scoreable_text(), cfg),
    "contains": lambda result, cfg: grade_contains(result.scoreable_text(), cfg),
    "exact": lambda result, cfg: grade_exact(result.scoreable_text(), cfg),
    "reference": lambda result, cfg: grade_reference(result.scoreable_text(), cfg),
}


def grade(result, case: dict) -> dict | None:
    """Grade a ChatResult for an objective case. None for judged/perf cases."""
    name = case.get("grader")
    if not name:
        return None
    if name not in GRADERS:
        return {"grade": "fail", "detail": f"unknown grader {name!r}"}
    return GRADERS[name](result, case.get("grader_config", {}))
