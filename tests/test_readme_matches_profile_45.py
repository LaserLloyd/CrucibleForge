"""The public docs describe the test the code runs (review finding 1, 2026-11-18).

* README.md and docs/SCORING.md carry a parts table between ``<!-- parts:begin -->`` and ``<!-- parts:end -->``;
  its sections, part names and points are compared with the ``bench45`` profile as the 4.5 board names them.
* The profile name, the default command and the suite version stated in the README match the code.
* No doc mentions a CLI verb or flag that ``--help`` does not list.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

from crucibleforge import profiles, tiers, version

ROOT = Path(__file__).resolve().parent.parent
README = ROOT / "README.md"
DOCS = ROOT / "docs"
# docs whose commands and flags are checked against --help
CHECKED = [README, DOCS / "SCORING.md", DOCS / "RUNNING.md", DOCS / "CLI.md", DOCS / "BUDGET.md", DOCS / "JUDGE.md"]
pytestmark = pytest.mark.shipping_default   # the shipping composition, as a visitor runs it
PARTS_RE = re.compile(r"<!-- parts:begin -->\n(.*?)<!-- parts:end -->", re.S)


def _num(s: str) -> float:
    return float(s.strip())


def _profile_sections():
    """The board's score tree: [(section label, [(segment label, points)])] from ``segments45``."""
    from crucibleforge import segments45
    t = segments45.tree(dict(profiles.load_profile("bench45", {})))
    return [(t[k]["label"], [(g["label"], g["max"]) for g in t[k].values() if isinstance(g, dict)])
            for k in segments45.SECTIONS]


def _doc_rows(path: Path):
    m = PARTS_RE.search(path.read_text(encoding="utf-8"))
    assert m, f"{path.name}: no parts table between the parts markers"
    rows = []
    for line in m.group(1).strip().splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 3 or cells[0] in ("Section",) or set(cells[0]) <= {"-", " "}:
            continue
        rows.append((cells[0], cells[1], _num(cells[2])))
    return rows


@pytest.mark.parametrize("path", [README, DOCS / "SCORING.md"], ids=["README", "SCORING"])
def test_parts_table_equals_the_profile(path):
    want = [(sec, lab, pts) for sec, segs in _profile_sections() for lab, pts in segs]
    assert _doc_rows(path) == want


def test_sections_sum_to_ten_and_overall_is_twenty():
    secs = _profile_sections()
    assert [round(sum(pts for _, pts in segs), 6) for _, segs in secs] == [10.0, 10.0]
    text = (README.read_text(encoding="utf-8") + (DOCS / "SCORING.md").read_text(encoding="utf-8"))
    assert "Overall /20" in text


def test_profile_name_suite_and_default_command():
    text = README.read_text(encoding="utf-8")
    assert profiles.DEFAULT_PROFILE == "bench45"
    assert "`bench45`" in text
    assert version.SUITE_VERSION in text and version.SUITE_VERSION.startswith("4.5")
    assert "crucibleforge all --models <label> --judge <provider>:<judge-model-id>" in text
    # NSFW+ runs by default and the README names the opt-out
    assert "--no-nsfw-plus" in text
    # the 4.1 profile stays selectable
    assert profiles.ARCHIVE_PROFILE == "bench" and "--profile bench" in text


def test_help_states_the_default_profile_and_adult_opt_in():
    out = _help("all")
    assert "default: bench45" in out
    assert "--no-nsfw-plus" in out


# ---- no doc mentions a verb or flag that --help does not list ------------------------------------------------

_HELP: dict[tuple, str] = {}


def _help(*verb: str) -> str:
    if verb not in _HELP:
        args = [sys.executable, "-c", "from crucibleforge.cli import main; main()", *verb, "--help"]
        r = subprocess.run(args, capture_output=True, text=True, cwd=ROOT, timeout=120)
        assert r.returncode == 0, (verb, r.stderr[-300:])
        _HELP[verb] = r.stdout
    return _HELP[verb]


def _children(text: str) -> list[str]:
    m = re.search(r"\{([a-z0-9,-]+)\}", text)
    return m.group(1).split(",") if m else []


def _verbs() -> list[str]:
    kids = _children(_help())
    assert kids
    return kids


def _flags_in(text: str) -> set[str]:
    return set(re.findall(r"(?<![\w-])(--[a-z][a-z0-9-]*)", text))


def _verb_flags(verb: str) -> set[str]:
    """The flags of a verb and of every sub-command below it (gui token rotate, pack new, cases verify ...)."""
    out = _flags_in(_help(verb))
    if verb == "gui":     # its help lists `token` without the {show,rotate} braces
        out |= _flags_in(_help("gui", "token", "rotate")) | _flags_in(_help("gui", "token", "show"))
    for kid in _children(_help(verb)):
        if kid in _verbs():
            continue
        try:
            out |= _flags_in(_help(verb, kid))
            for k2 in _children(_help(verb, kid)):
                out |= _flags_in(_help(verb, kid, k2))
        except AssertionError:
            pass
    return out


def _all_listed_flags() -> set[str]:
    out = _flags_in(_help())
    for v in _verbs():
        out |= _verb_flags(v)
    return out


# flags of other programs that the docs legitimately show (uv, docker, podman, curl, git, systemd)
EXTERNAL_FLAGS = {
    # scripts/read_chat45.py (a script of its own, documented beside the chat scenes' second reading)
    "--stored-reader", "--import-agree", "--folders",
    # scripts/release45.py and scripts/check_marks45.py (scripts of their own: "From a finished run to a board")
    "--board", "--chat-cap-usd", "--classing-cap-usd", "--classing-readers", "--dry-run", "--spend-ledger",
    "--spend-run-cap-usd", "--spend-total-cap-usd", "--anchors", "--admission", "--only", "--reference", "--out-name", "--report-jobs",
    "--python", "--frozen", "--rm", "--build", "--detach-keys", "--force-recreate", "--offline", "--no-cache",
    "--profile-dir", "--user", "--version", "--help", "--pull", "--quiet", "--all", "--tag", "--env-file", "--file",
    "--project", "--no-dev", "--reinstall", "--only-binary", "--follow", "--since", "--read-only", "--no-sync",
    "--cap-drop", "--security-opt", "--tmpfs", "--userns", "--init", "--format", "--filter", "--name", "--publish",
    "--volume", "--workdir", "--entrypoint", "--network", "--no-stream", "--unit", "--now", "--timeout", "--silent",
    "--fail", "--show-error", "--location", "--header", "--data", "--output", "--from", "--with", "--tools",
}


def test_every_flag_a_doc_mentions_is_listed_by_help():
    listed = _all_listed_flags()
    missing = {}
    for path in CHECKED:
        found = _flags_in(path.read_text(encoding="utf-8")) - listed - EXTERNAL_FLAGS
        if found:
            missing[path.name] = sorted(found)
    assert not missing, missing


def test_every_verb_a_command_line_in_the_docs_names_exists():
    verbs = set(_verbs())
    bad = []
    pat = re.compile(r"crucibleforge(?:\s+--[a-z-]+(?:\s+(?!--)\S+)?)*\s+([a-z][a-z0-9-]*)\b")
    for path in CHECKED:
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if "crucibleforge" not in line or line.lstrip().startswith("|---"):
                continue
            for m in pat.finditer(line):
                w = m.group(1)
                # prose such as "crucibleforge is" / a package or path name is not a command
                if line[m.start() - 1:m.start()] in "/._-" and m.start():
                    continue
                if f"crucibleforge {w}" in line and (line.lstrip().startswith(("uv run", "$", "|", "-", "docker", "crucibleforge"))
                                                       or "`" in line or line.startswith("    ")):
                    if w not in verbs:
                        bad.append((path.name, n, w))
    assert not bad, bad


def test_flags_beside_a_verb_belong_to_that_verb():
    wrong = []
    pat = re.compile(r"crucibleforge\s+(?:--[a-z-]+\s+\S+\s+)*([a-z][a-z0-9-]*)((?:\s+[^`|]*)?)")
    globals_ = _flags_in(_help().split("positional arguments")[0])
    for path in CHECKED:
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            m = pat.search(line) if "crucibleforge" in line else None
            if not m or m.group(1) not in _verbs():
                continue
            own = _verb_flags(m.group(1)) | globals_ | EXTERNAL_FLAGS
            extra = _flags_in(m.group(2)) - own
            if extra:
                wrong.append((path.name, n, m.group(1), sorted(extra)))
    assert not wrong, wrong


# G2 (2026-11-19): the docs state the real per-part output budgets (Trace is sent 69,632-71,680, not 65,536)
BUDGETS_RE = re.compile(r"<!-- budgets:begin -->\n(.*?)<!-- budgets:end -->", re.S)


def _budget_rows(path: Path):
    m = BUDGETS_RE.search(path.read_text(encoding="utf-8"))
    assert m, f"{path.name}: no budgets table between the budgets markers"
    rows = {}
    for line in m.group(1).strip().splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 2 or cells[0] == "Part" or set(cells[0]) <= {"-", " "}:
            continue
        rows[cells[0]] = {int(x.replace(",", "")) for x in re.findall(r"\d[\d,]{2,}", cells[1])}
    return rows


def _real_budgets():
    """{part label: set of token figures} from the profile and the case files: a chain part's rung ceiling (x its items
    per prompt) and history room, a bank case's ``bank_budget``, a case's own ``budget``, else the regime's (4 x the
    case's max_tokens, between the floor and the cap)."""
    from crucibleforge import config
    prof = dict(profiles.load_profile("bench45", {}))
    prof["_public"] = True
    by_id = {}
    for cat in {"tooluse", "codebank", "rpdeep"}:
        for c in config.load_cases([cat]):
            by_id[c["id"]] = c
    got = {}
    for key, spec in (prof.get("chains") or {}).items():
        for r in spec.get("flat") or []:
            ceil = r.get("ceiling") or r.get("ceiling_per_turn")
            if ceil:
                got.setdefault(key, set()).add(int(ceil) * int(r.get("items_per_prompt") or 1))
            if r.get("history_budget"):
                got[key].add(int(r["history_budget"]))
    out = {}
    for sec in tiers.scores(prof):
        if not sec.get("in_total"):
            continue
        for p in sec["parts"]:
            key = f"{sec['key']}/{p['key']}"
            if key in got:
                out[p["label"]] = got[key]
            elif p.get("cases") and all(c in by_id for c in p["cases"]):
                vals = set()
                for cid in p["cases"]:
                    c = by_id[cid]
                    fb = prof["bank_budget"] if c["category"] == "codebank" else c.get("budget")
                    vals.add(int(fb) if fb else max(int(prof["thinking_max_tokens_floor"]), min(
                        int(prof["thinking_max_tokens_cap"]),
                        int(prof["thinking_max_tokens_factor"]) * int(c["max_tokens"]))))
                out[p["label"]] = vals
    return out


@pytest.mark.parametrize("path", [DOCS / "SCORING.md", DOCS / "RUNNING.md"], ids=["SCORING", "RUNNING"])
def test_budget_table_equals_the_code(path):
    assert _budget_rows(path) == _real_budgets()
