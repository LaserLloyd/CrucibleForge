#!/usr/bin/env python3
"""From finished run folders to a rendered board: one command, resumable, with a printed plan first.

  release45.py --folders <run folder> [<run folder> ...] [--board <dir>] [--only STEP[,STEP]] [--dry-run]
               [--classing-readers a,b] [--classing-cap-usd N] [--chat-cap-usd N] [--pack-cap-usd N] [--no-chat-hosted]
               [--nsfw-pack-cap-usd N] [--nsfw-pack-willing LABEL,...]
               [--spend-cap-usd N] [--spend-run-cap-usd N] [--spend-ledger FILE] [--reference LABEL]
               [--profile bench45]

One folder = one run of one model (``transcripts_<label>.jsonl``); a model's replicates are separate folders. The
board dir (default ``<first folder>/../board45``) is the combined board built from them.

``--anchors <folder>...``: validation-only runs (the damaged stand-in, a small model) that the marks need. Their rows
are used by the mark checks (assistant marks, segment / Everyday / act-and-beat marks) and never assembled into the
board: they are never ranked and never shown as launch rows. The board lists them under "validation anchors" with
their Everyday share only (``<board>/anchors45.json``).

``--admission <file>``: a marks45.json-shaped file whose ``classing.C1_constructed`` and ``classing.C7_constructed``
results are copied into every folder's (and the board's) ``marks45.json`` before any step. Refused (exit 2) when a
result names a reader model id that no indexed reader addendum names, or a prompt sha that is not the indexed
classing prompt (C1) or flag prompt (C7).

Steps, in order (``--only`` takes a step name or its letter a-e)::

  a-fix           fix rows (the four Fixes units) generated into each folder that lacks them      [generates]
  a-everyday      Assistant-basics rows (15 cases) generated where a folder lacks them            [generates]
  b-c1            classing readers' constructed-set check (only with --classing-readers)          [hosted: cap]
  b-c6            reader refusal mark C6 from the combined reader store, BEFORE any read (lead ruling R1): the
                  board dir gets the folders' rows and stores (copies), their stored C1 when it has none, then
                  check_classing_marks.py --skip-c1; the block reaches every folder before b-classing
  b-classing      classing reader votes for every stored sexual / dark unit (scripts/read45.py)   [hosted: cap]
                  and for the NSFW+ pack units into readers45/nsfwpacks45/       [hosted: --nsfw-pack-cap-usd]
  b-chat          second reader of the eight long chat scenes (scripts/read_chat45.py + agree)    [hosted: cap]
  b-packs         second reader of the Chat pack scenes (read_chat45.py --packs + agree --parts chat/packs45)
                                                                                                    [hosted: --pack-cap-usd]
  c-assemble      the combined board dir (copies of the folders' stored rows, reader stores and forfeits)
  c-classing      classing marks (scripts/check_classing_marks.py, C2-C6 from the store)
  c-longscene     long-scene marks (scripts/check_rplong_marks45.py)
  c-packmarks     Chat pack marks P1-P5 (scripts/check_pack_marks45.py), spread into every folder's marks45.json
  c-nsfwpackmarks NSFW+ pack marks (scripts/check_nsfw_pack_marks45.py; --nsfw-pack-willing names the willing
                  cohort), spread into every folder's marks45.json (block nsfw_packs)
  c-assistant     Assistant-basics marks (scripts/check_assistant_marks.py)
  c-marks         segment / Everyday / act-and-beat marks (scripts/check_marks45.py)
  c-sanity        sanity block (scripts/check_sanity45.py): a variant classed the same for every model, a read of
                  an empty region, a unit id collision, a segment or NSFW+ part that is 0.0 for every model. Any
                  entry STOPS the release (exit 7) unless --accept-sanity <code> names it (logged with who / when)
  d-report        rescore + report of each folder (crucibleforge report), --report-jobs at a time (default 3)
  e-resolution    run-to-run spread on the board's scale, read from the folder reports (scripts/resolution45.py):
                  writes <board>/precision45.json (seconds; nothing rescored)
  e-board         report of the combined board: a replicated model at the mean of its runs, every range from
                  replicate runs (own, else pooled), labelled
  e-accuracy      each model's accuracy range per score, printed from the board's report.json

``--force STEP[,STEP]`` re-runs those steps even when they are "done at this commit".

A hosted pack reader that would be called needs ``--pack-cap-usd`` (exit 2 otherwise, before any call); the cap is
PER FOLDER (each folder's agree call gets the full amount, so N hosted folders may spend N x the cap). b-packs is
resumable: the store is checked first, so a second run with every pack scene read calls no reader and needs no cap.
A folder without pack rows has nothing to do. The NSFW+ pack units are read in b-classing under their own cap
``--nsfw-pack-cap-usd`` (passed to read45 as --pack-cap-usd, PER FOLDER; read45 exits 2 before any call when a hosted
reader would be called without it; a folder whose pack units are all read needs none).

Readers are never picked here: a reader is used only when the profile names it and a stored C1 pass admits it (or
``--classing-readers`` names it for C1). With none, the step stops with a message and exit 2.

Safe to run unattended (no TTY, no prompt). Resumable: a step writes ``<folder>/.release45/<step>.json`` with the
tree's commit and the folder's inputs, and is skipped while both are current. Refuses to start (exit 5) while the
file named by ``CF_MAINTENANCE_MARKER`` exists. Never deletes or rewrites a stored row: before and after each step that
generates, every pre-existing row line of every folder is hashed and must still be there (exit 4 otherwise). Spend
caps (``CF_SPEND_RUN_CAP_USD``, ``CF_SPEND_CAP_USD``, ``CF_SPEND_LEDGER``) pass through to every call. Logs:
``<folder>/release45.log`` (and ``<board>/release45.log``).

One local judge (owner 2026-11-21, INTENT-45): the adult page is classed by the run's judge inside the normal run
(``crucibleforge judge`` / ``all``, store ``judge45/``). The two-reader and hosted steps (b-c1, b-c6, b-classing,
b-chat, b-packs, c-classing) are out of the default order (``DEFAULT_STEPS``): they run only when ``--only`` names one
in full, and no hosted reader is ever picked (``judge45.HostedGrader``). c-assemble copies the judge store.

b-classing on a folder whose family is left with fewer than two readers (R1) is DONE-WITH-PENDING: read45 exits 4,
the reason is logged (``DONE-PENDING``) and kept in the step marker (``pending``), the release goes on and the board
shows "pending readers" for that model; such a step is tried again on every release (never "current").

Exit: 0 done (or nothing to do), 2 a reader / cap / input is missing (message printed), 3 a step failed, 4 a stored
row changed, 5 maintenance marker, 6 a generating step finished without the rows it should have made, 7 a sanity
entry not accepted (listed).
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

STEPS = ("a-fix", "a-everyday", "b-judge", "b-c1", "b-c6", "b-classing", "b-chat", "b-packs", "c-assemble",
         "c-classing", "c-longscene", "c-packmarks", "c-nsfwpackmarks", "c-assistant", "c-marks", "c-sanity",
         "d-report", "e-resolution", "e-board", "e-accuracy")
#: SPEC-BUILD-45 item 13 (owner 2026-11-21, INTENT-45), step 8 (reviews A Q3 and B finding 2): the default release is
#: judge anything unjudged (the one local judge, per folder, so the assembled board carries the verdicts), assemble,
#: the folder reports, the run-to-run spread read back from them (board scale), the board report (which reads it),
#: accuracy. Every other step served hosted readers, a second reader, a mark used as a gate, or fills rows (a model
#: call): out of the default order, kept runnable by its full name with --only, never by default.
DEFAULT_STEPS = ("b-judge", "c-assemble", "d-report", "e-resolution", "e-board", "e-accuracy")
#: folder reports rendered at the same time (each is one core; --report-jobs)
REPORT_JOBS = 3
LEGACY_STEPS = tuple(s for s in STEPS if s not in DEFAULT_STEPS)
GENERATING = ("a-fix", "a-everyday")
BOARD_STEPS = ("b-c1", "b-c6", "c-assemble", "c-classing", "c-longscene", "c-packmarks", "c-nsfwpackmarks",
               "c-assistant", "c-marks", "c-sanity", "e-board", "e-accuracy", "e-resolution")
MARKER_DIR = ".release45"
MARK_KEYS = ("segments", "everyday", "act_beat")
#: the board dir's list of validation anchors (report.py prints it apart, never ranked)
ANCHORS_FILE = "anchors45.json"


class Stop(Exception):
    def __init__(self, msg: str, code: int):
        super().__init__(msg)
        self.code = code


# ------------------------------------------------------------------ small helpers
def tree_sha() -> str:
    try:
        head = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--short=12", "HEAD"], capture_output=True,
                              text=True, check=True).stdout.strip()
        dirty = subprocess.run(["git", "-C", str(ROOT), "status", "--porcelain", "--untracked-files=no"],
                               capture_output=True, text=True, check=True).stdout.strip()
        return head + ("+dirty" if dirty else "")
    except (OSError, subprocess.CalledProcessError):
        return "nogit"


def transcripts(folder: Path) -> list[Path]:
    return sorted(folder.glob("transcripts_*.jsonl"))


def label_of(folder: Path) -> str:
    fs = transcripts(folder)
    if len(fs) != 1:
        raise Stop(f"{folder}: must hold exactly one transcripts_<label>.jsonl (found {len(fs)}); split a multi-model "
                   f"run folder into per-model copies with scripts/split_run_folder45.py (rows are copied untouched)", 2)
    return fs[0].name[len("transcripts_"):-len(".jsonl")]


def read_rows(folder: Path) -> list[dict]:
    rows = []
    for f in transcripts(folder):
        for ln in f.read_text(encoding="utf-8").splitlines():
            if ln.strip():
                try:
                    rows.append(json.loads(ln))
                except ValueError:
                    continue
    return rows


def row_hashes(folder: Path) -> collections.Counter:
    c: collections.Counter = collections.Counter()
    for f in transcripts(folder):
        for ln in f.read_text(encoding="utf-8").splitlines():
            if ln.strip():
                c[(f.name, hashlib.sha256(ln.encode("utf-8")).hexdigest())] += 1
    return c


def signature(folder: Path) -> str:
    """What a step's result depends on in this folder: the stored rows and the reader stores (name + size)."""
    h = hashlib.sha256()
    for pat in ("transcripts_*.jsonl", "meta_*.json", "readers45/*.jsonl", "readers45/nsfwpacks45/*.jsonl",
                "agree/*.jsonl", "judge45/*.jsonl", "judge45/check.json", "forfeits.json"):
        for f in sorted(folder.glob(pat)):
            h.update(f"{f.relative_to(folder)}:{f.stat().st_size}\n".encode())
    return h.hexdigest()[:16]


def maintenance_marker() -> Path | None:
    p = os.environ.get("CF_MAINTENANCE_MARKER", "").strip()
    return Path(p) if p and Path(p).exists() else None


def default_runner(cmd: list[str], env: dict, log: Path | None) -> int:
    """Run a command with no TTY, appending its output to ``log``. Returns its exit code."""
    out = open(log, "ab") if log else subprocess.DEVNULL
    try:
        out_f = out if log else subprocess.DEVNULL
        r = subprocess.run(cmd, env=env, cwd=ROOT, stdin=subprocess.DEVNULL, stdout=out_f, stderr=subprocess.STDOUT)
        return r.returncode
    finally:
        if log:
            out.close()


def capture(cmd: list[str], env: dict) -> tuple[int, str]:
    r = subprocess.run(cmd, env=env, cwd=ROOT, stdin=subprocess.DEVNULL, capture_output=True, text=True)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


# ------------------------------------------------------------------ the release
class Release:
    def __init__(self, folders: list[Path], board: Path, opts: argparse.Namespace, runner=default_runner,
                 capturer=capture, out=print):
        self.folders, self.board, self.o = folders, board, opts
        self.run_cmd, self.capture, self.say = runner, capturer, out
        self.commit = tree_sha()
        self.cf = str(Path(sys.executable).parent / "crucibleforge")
        self.env = dict(os.environ)
        for flag, name in (("spend_run_cap_usd", "CF_SPEND_RUN_CAP_USD"), ("spend_cap_usd_total", "CF_SPEND_CAP_USD"),
                           ("spend_ledger", "CF_SPEND_LEDGER")):
            v = getattr(opts, flag, None)
            if v is not None:
                self.env[name] = str(v)
        self._prof = None
        self.dry = bool(opts.dry_run)
        self.anchors = [Path(x).resolve() for x in (getattr(opts, "anchors", None) or [])]
        #: stored runs of Assistant-basics set models, read by the assistant marks only (may hold several models)
        self.assistant_runs = [Path(x).resolve() for x in (getattr(opts, "assistant_runs", None) or [])]
        self.force = tuple(x for x in re.split(r"[,\s]+", (getattr(opts, "force", None) or "").strip()) if x)
        self.report_jobs = max(1, int(getattr(opts, "report_jobs", None) or REPORT_JOBS))
        self.step = ""
        self._pending: dict | None = None
        self._c6plan = None

    # ---- profile
    def profile(self) -> dict:
        if self._prof is None:
            from crucibleforge import profiles
            self._prof = profiles.load_profile(self.o.profile)
        return self._prof

    def script(self, name: str, *args) -> list[str]:
        return [sys.executable, str(ROOT / "scripts" / name), *map(str, args)]

    # ---- logging, markers
    def log(self, where: Path, text: str) -> None:
        if self.dry:
            return
        where.mkdir(parents=True, exist_ok=True)
        with open(where / "release45.log", "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%dT%H:%M:%S')} {text}\n")

    def marker(self, where: Path, step: str) -> Path:
        return where / MARKER_DIR / f"{step}.json"

    def forced(self, step: str) -> bool:
        return step in (getattr(self, "force", None) or ())

    def is_current(self, where: Path, step: str, sig: str) -> bool:
        if self.forced(step):
            return False                                  # --force: run it again even when done at this commit
        try:
            m = json.loads(self.marker(where, step).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return False
        return (m.get("commit") == self.commit and m.get("signature") == sig and m.get("ok") is True
                and not m.get("pending"))          # done-with-pending is tried again every release

    def mark_done(self, where: Path, step: str, sig: str, rc: int = 0, note: str = "",
                  pending: dict | None = None) -> None:
        if self.dry:
            return
        p = self.marker(where, step)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(p.name + f".tmp{os.getpid()}")
        tmp.write_text(json.dumps({"step": step, "commit": self.commit, "signature": sig, "ok": True, "rc": rc,
                                   "note": note, "pending": pending or {},
                                   "ts": time.strftime("%Y-%m-%dT%H:%M:%S")}, indent=1),
                       encoding="utf-8")
        os.replace(tmp, p)

    # ---- what each generating step still needs (read-only)
    def fix_needed(self, folder: Path) -> tuple[list[str], list[str]]:
        """(cases to generate, cases blocked by rows of an older checker)."""
        from crucibleforge import fixrows45, segments45
        fx = ((segments45.tree(self.profile()) or {}).get("code") or {}).get("fixes") or {}
        cases, reps = list(fx.get("cases") or []), fx.get("case_repeats") or {}
        sc = fixrows45.case_scores(read_rows(folder), sorted(set(cases)))["cases"]
        need, blocked = [], []
        for c in sorted(set(cases)):
            e = sc.get(c) or {}
            if len(e.get("scores") or []) >= int(reps.get(c, 1)):
                continue
            (blocked if e.get("stale") or e.get("old_grader") else need).append(c)
        return need, blocked

    def everyday_needed(self, folder: Path) -> list[str]:
        from crucibleforge import assistant45
        from crucibleforge.config import dedupe_rows
        prof = self.profile()
        if assistant45.block(prof) is None:
            return []
        a = assistant45.compute_stored(prof, dedupe_rows(read_rows(folder)))
        if a is not None and a.get("status") == "ok":
            return []
        return list(assistant45.case_ids(prof))

    # ---- the plan
    def plan(self) -> list[tuple[str, Path, str]]:
        """[(step, where, text)] for every step that has work (a step that is current is listed as skipped)."""
        items = []
        for st in self.selected():
            for where in self.wheres(st):
                sig = self.sig_for(st, where)
                cur = self.is_current(where, st, sig)
                items.append((st, where, ("skip (done at this commit): " if cur else "") + self.describe(st, where)))
        return items

    def sig_for(self, step: str, where: Path) -> str:
        if where == self.board:
            base = "".join(signature(f) for f in [*self.folders, *self.anchors, *getattr(self, "assistant_runs", [])])
            # what the step reads besides the stored rows: e-resolution reads the folder reports; the board report
            # reads the spread file and the assembled marks (a new precision45.json re-renders the board)
            extra = {"e-resolution": [f / "report.json" for f in self.folders],
                     "e-board": [self.board / "precision45.json", self.board / "marks45.json"]}.get(step, [])
            for p in extra:
                try:
                    base += f"{p}:{hashlib.sha256(p.read_bytes()).hexdigest()[:16]}"
                except OSError:
                    base += f"{p}:-"
            return hashlib.sha256(base.encode()).hexdigest()[:16]
        return signature(where)

    def wheres(self, step: str) -> list[Path]:
        if step == "d-report":
            return list(self.folders)
        if step == "b-packs":
            return [*self.folders, *self.anchors]           # the anchors' pack scenes are read too (P2 needs them)
        return self.folders if step not in BOARD_STEPS else [self.board]

    def describe(self, step: str, where: Path) -> str:
        o = self.o
        if step == "a-fix":
            need, blocked = self.fix_needed(where)
            return (f"generate {', '.join(need) or 'nothing (fix rows present)'}"
                    + (f"; BLOCKED, rows graded by an older checker (archive first, the owner's call): {', '.join(blocked)}"
                       if blocked else ""))
        if step == "a-everyday":
            need = self.everyday_needed(where)
            return f"generate {len(need)} Assistant-basics cases" if need else "nothing (Assistant basics present)"
        if step == "b-c1":
            return ("C1 for readers " + o.classing_readers) if o.classing_readers else \
                "not run (no --classing-readers named; the stored C1 admits readers)"
        if step == "b-c6":
            blk, _picks, err = self.planned_c6()
            if err:
                return f"C6 from the combined reader store before any read (not computable now: {err})"
            gone = [r["name"] for r in C6_removed(blk)]
            over = [t for t in (blk.get("arithmetic") or {}).values() if "> 2%" in t]
            return (f"C6 from the combined reader store before any read: {blk.get('status')}"
                    + (f"; removed {', '.join(gone)}" if gone else "") + ("; " + "; ".join(over) if over else ""))
        if step == "b-classing":
            _blk, picks, err = self.planned_c6()
            pk = picks.get(label_of(where)) if not err else f"not computable now ({err})"
            return (f"read45 votes (readers named by the profile and admitted by a stored C1 pass, C6 removal in "
                    f"force); {label_of(where)}: {pk}")
        if step == "b-chat":
            return "chat-scene second reader (pinned reader of calibration/chat-readers-45.yaml)"
        if step == "b-packs":
            n = len(self.pack_units_of(where))
            return (f"chat-pack second reader ({n} pack scene unit(s); pinned reader of calibration/chat-readers-45.yaml; "
                    "hosted needs --pack-cap-usd)") if n else "nothing (no Chat pack rows)"
        if step == "b-judge":
            return f"crucibleforge judge (the profile's local judge; unjudged rows and the adult page only) on {label_of(where)}"
        if step == "e-accuracy":
            return "each model's own accuracy range per score (accuracy45.py over the board's report.json); prints, never withholds"
        if step == "c-assemble":
            return f"combine {len(self.folders)} folder(s) into {self.board}"
        if step == "d-report":
            return "crucibleforge report (this folder alone)"
        if step == "e-board":
            return "crucibleforge report (combined board; reads precision45.json)"
        return {"c-classing": "classing marks C1-C6 (check_classing_marks.py --skip-c1) on the combined store",
                "c-sanity": "sanity block (check_sanity45.py); any entry stops the release unless --accept-sanity "
                            "names it" + (f" (accepted: {', '.join(self.o.accept_sanity)})"
                                          if getattr(self.o, "accept_sanity", None) else ""),
                "c-longscene": "long-scene marks (check_rplong_marks45.py) over every folder",
                "c-packmarks": "Chat pack marks P1-P5 (check_pack_marks45.py) over every folder and anchor",
                "c-nsfwpackmarks": "NSFW+ pack marks (check_nsfw_pack_marks45.py) over every folder",
                "c-assistant": "Assistant-basics marks (check_assistant_marks.py) over every folder",
                "c-marks": "segment / Everyday / act-and-beat marks (check_marks45.py) over every folder",
                "e-resolution": "run-to-run spread on the board's scale from every folder's report.json "
                                "(resolution45.py) -> precision45.json"}.get(step, step)

    def selected(self) -> list[str]:
        if not self.o.only:
            return list(DEFAULT_STEPS)
        want = []
        for tok in re.split(r"[,\s]+", self.o.only.strip()):
            # a step letter expands to the default steps of that letter; a legacy step runs only by its full name
            hit = [s for s in DEFAULT_STEPS if s.startswith(tok + "-")] if len(tok) == 1 else \
                [s for s in STEPS if s == tok]
            if not hit:
                raise Stop(f"unknown step {tok!r}; steps: {', '.join(STEPS)}", 2)
            want += hit
        return [s for s in STEPS if s in want]

    def print_plan(self) -> None:
        self.say(f"release45 plan (tree {self.commit}); {len(self.folders)} folder(s), board {self.board}")
        if self.anchors:
            self.say(f"  validation anchors (marks only, never ranked): {', '.join(f.name for f in self.anchors)}")
        if self.assistant_runs:
            self.say(f"  extra stored assistant runs (Assistant-basics marks only): "
                     f"{', '.join(f.name for f in self.assistant_runs)}")
        for k in ("CF_SPEND_RUN_CAP_USD", "CF_SPEND_CAP_USD", "CF_SPEND_LEDGER", "CF_MAINTENANCE_MARKER"):
            self.say(f"  {k} = {self.env.get(k, '(unset)')}")
        if self.o.spend_cap_usd is not None:
            self.say(f"  generating steps get --spend-cap-usd {self.o.spend_cap_usd} (this invocation's own spend)")
        self.say("  price: generated rows of a hosted model, reader votes and the agree calls count against these "
                 "caps; for the whole-run figure run `crucibleforge --results <folder> all --models <label> --yes "
                 "--plan`. A rig model costs no money.")
        for st, where, text in self.plan():
            self.say(f"  [{st}] {where.name}: {text}")

    # ---- run
    def check_marker(self) -> None:
        m = maintenance_marker()
        if m:
            raise Stop(f"maintenance marker present ({m}): not starting (an operator is working on the box)", 5)

    def preflight_env(self) -> None:
        """One plain line instead of a 401 deep inside a step: the config the steps resolve models from, and the hosted
        key of every hosted model a selected step will call (a model under test, the chat/classing reader)."""
        steps = self.selected()
        cfg_path = os.environ.get("CRUCIBLEFORGE_CONFIG", "").strip()
        if not cfg_path or not Path(cfg_path).is_file():
            raise Stop("CRUCIBLEFORGE_CONFIG is not set to a models yaml file (see docs/RUNNING.md, 'Environment for a "
                       "release run'): steps cannot resolve model names without it", 2)
        import yaml
        cfg = yaml.safe_load(Path(cfg_path).read_text(encoding="utf-8")) or {}   # read only: no results-dir side effect
        names: list[str] = []
        if any(st in GENERATING for st in steps):
            names += [label_of(f) for f in self.folders]
        if any(st in ("b-chat", "b-packs", "b-classing") for st in steps) and not self.o.no_chat_hosted:
            names.append("deepseek-flash")
        if "b-c1" in steps and self.o.classing_readers:
            names += [x.strip() for x in self.o.classing_readers.split(",") if x.strip()]
        provs = cfg.get("providers") or {}
        models = {m.get("name"): m for m in cfg.get("models") or []}
        for n in dict.fromkeys(names):
            m = models.get(n)
            key = (provs.get((m or {}).get("provider")) or {}).get("api_key_env")
            if key and not os.environ.get(key):
                raise Stop(f"{key} is not set but {n} is a hosted model a selected step calls: source the env file "
                           f"that holds it (docs/RUNNING.md, 'Environment for a release run')", 2)

    def go(self) -> int:
        try:
            self.check_marker()
            bad = [x for x in self.force if x not in STEPS]
            if bad:
                raise Stop(f"--force: unknown step(s) {', '.join(bad)}; steps: {', '.join(STEPS)}", 2)
            for f in self.folders:
                if not f.is_dir():
                    raise Stop(f"not a folder: {f}", 2)
                label_of(f)
            launch = {label_of(f) for f in self.folders}
            for f in self.anchors:
                if not f.is_dir():
                    raise Stop(f"not a folder: {f}", 2)
                if label_of(f) in launch:
                    raise Stop(f"anchor {f.name} holds {label_of(f)!r}, a launch label: an anchor is a validation-only "
                               "model and is never a copy of a launch row", 2)
            for f in self.assistant_runs:
                if not f.is_dir():
                    raise Stop(f"--assistant-runs: not a folder: {f}", 2)
            seeds = self.admission_seeds() if getattr(self.o, "admission", None) else None
            self.print_plan()
            if self.dry:
                self.say("dry run: nothing executed, nothing written")
                return 0
            self.preflight_env()
            self.board.mkdir(parents=True, exist_ok=True)
            if seeds:
                self.seed_admission(seeds)
            for st in self.selected():
                self.check_marker()
                self.do_step(st)
            self.say("release45: done")
            return 0
        except Stop as e:
            self.say(f"release45 stopped: {e}")
            for f in self.folders:
                self.log(f, f"STOP {e}")
            return e.code

    def do_step(self, st: str) -> None:
        wheres = self.wheres(st)
        if st == "b-c1" and not self.o.classing_readers:
            return
        if st == "d-report" and self.report_jobs > 1:
            return self.do_reports(st, wheres)
        for where in wheres:
            sig = self.sig_for(st, where)
            if self.is_current(where, st, sig):
                self.say(f"[{st}] {where.name}: current, skipped")
                continue
            self.say(f"[{st}] {where.name}: start")
            self.log(where, f"START {st} commit={self.commit}")
            self.step = st
            self._pending = None
            before = {f: row_hashes(f) for f in self.folders} if st in GENERATING else None
            getattr(self, "step_" + st.replace("-", "_"))(where)
            if before is not None:
                for f, b in before.items():
                    after = row_hashes(f)
                    lost = [k for k, n in b.items() if after[k] < n]
                    if lost:
                        self.log(f, f"STORED ROW CHANGED in {lost[0][0]}")
                        raise Stop(f"{f.name}: a stored row line is missing or changed after {st} "
                                   f"({len(lost)} line(s) in {lost[0][0]}); nothing else was run", 4)
            pend = self._pending
            self.mark_done(where, st, self.sig_for(st, where), pending=pend)
            if pend:
                for lab, why in pend.items():
                    self.say(f"  {where.name}: {lab}: {why}")
                self.log(where, f"DONE-PENDING {st}: " + "; ".join(f"{k}: {v}" for k, v in pend.items()))
            else:
                self.log(where, f"DONE {st}")

    def do_reports(self, st: str, wheres: list[Path]) -> None:
        """The folder reports, ``--report-jobs`` at a time (each folder has its own results dir; nothing shared)."""
        from concurrent.futures import ThreadPoolExecutor
        todo = []
        for where in wheres:
            if self.is_current(where, st, self.sig_for(st, where)):
                self.say(f"[{st}] {where.name}: current, skipped")
            else:
                todo.append(where)
        self.step = st

        def one(where: Path) -> None:
            self.say(f"[{st}] {where.name}: start")
            self.log(where, f"START {st} commit={self.commit}")
            self.step_d_report(where)
            self.mark_done(where, st, self.sig_for(st, where))
            self.log(where, f"DONE {st}")

        errors = []
        with ThreadPoolExecutor(max_workers=self.report_jobs) as ex:
            for fut in [ex.submit(one, w) for w in todo]:
                try:
                    fut.result()
                except Stop as e:
                    errors.append(e)
        if errors:
            raise errors[0]

    def call(self, where: Path, cmd: list[str], ok=(0,), what: str = "") -> int:
        cmd = [str(c) for c in cmd]
        self.log(where, "RUN " + " ".join(cmd))
        rc = self.run_cmd(cmd, self.env, None if self.dry else where / "release45.log")
        if rc not in ok:
            self.log(where, f"FAILED rc={rc}: {what or cmd[0]}")
            if rc == 2 and what.startswith("reader"):
                raise Stop(f"{where.name}: {what}: no reader named or admitted, or a hosted reader without a cap "
                           f"(see {where / 'release45.log'}); nothing was picked for you", 2)
            raise Stop(f"{where.name}: {what or Path(cmd[0]).name} failed (rc {rc}); see {where / 'release45.log'}", 3)
        return rc

    # ---- mark checkers: exit 0/1/2 are all verdicts, but only when the checker wrote one in THIS invocation
    @staticmethod
    def _stamp(path: Path):
        try:
            st = path.stat()
        except OSError:
            return None
        return (st.st_mtime_ns, st.st_size, st.st_ino)

    def checked_call(self, where: Path, cmd: list[str], what: str,
                     verdicts: list[tuple[Path, tuple[str, ...]]]) -> int:
        """Run a mark checker that accepts exit 0 (pass), 1 (mark failed) and 2 (not measured). The step is DONE only
        when the checker wrote its verdict entry during this call (each file in ``verdicts`` rewritten since before
        the call, and holding each named key) and did not die with a traceback. A crash, or no fresh verdict, is a
        STOP (exit 3) naming the step and its log: a stale block left by an earlier run is never taken as a result."""
        log = where / "release45.log"
        try:
            offset = log.stat().st_size
        except OSError:
            offset = 0
        before = {p: self._stamp(p) for p, _ in verdicts}
        self.call(where, cmd, ok=(0, 1, 2), what=what)
        try:
            with open(log, "rb") as fh:
                fh.seek(offset)
                tail = fh.read().decode("utf-8", "replace")
        except OSError:
            tail = ""
        if "Traceback (most recent call last)" in tail:
            self.log(where, f"CRASHED {self.step}: {what}")
            raise Stop(f"{self.step}: {what} crashed (traceback in {log}); the step is not marked done", 3)
        for path, keys in verdicts:
            now = self._stamp(path)
            fresh = now is not None and now != before[path]
            if fresh and keys:
                try:
                    body = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    body = None
                fresh = isinstance(body, dict) and all(k in body for k in keys)
            if not fresh:
                self.log(where, f"NO VERDICT {self.step}: {what} wrote nothing to {path.name}")
                raise Stop(f"{self.step}: {what} wrote no fresh verdict entry to {path} (a stale block is not a "
                           f"result); the step is not marked done; see {log}", 3)
        return 0

    def run_gen(self, where: Path, cases: list[str]) -> None:
        cmd = [self.cf, "--results", str(where), "run", "--profile", self.o.profile, "--models", label_of(where),
               "--cases", ",".join(cases), "--yes"]
        if self.o.spend_cap_usd is not None:
            cmd += ["--spend-cap-usd", str(self.o.spend_cap_usd)]
        self.call(where, cmd, what="generate")

    # ---- steps
    def step_a_fix(self, where: Path) -> None:
        need, blocked = self.fix_needed(where)
        if blocked:
            self.say(f"  {where.name}: rows graded by an older checker are never rewritten: {', '.join(blocked)}")
        if need:
            self.run_gen(where, need)
            still, _ = self.fix_needed(where)
            if still:
                raise Stop(f"{where.name}: after generating, fix rows are still missing for {', '.join(still)}", 6)

    def step_a_everyday(self, where: Path) -> None:
        need = self.everyday_needed(where)
        if need:
            self.run_gen(where, need)
            if self.everyday_needed(where):
                raise Stop(f"{where.name}: after generating, Assistant basics is still incomplete", 6)

    def step_b_c1(self, where: Path) -> None:
        o = self.o
        cmd = self.script("check_classing_marks.py", "--results", self.board, "--readers", o.classing_readers)
        if o.classing_cap_usd is not None:
            cmd += ["--cap-usd", o.classing_cap_usd]
        self.checked_call(self.board, cmd, "reader C1", [(self.board / "marks45.json", ("classing",))])
        self.spread(("classing",))

    # ---- C6 before any read (lead ruling R1, review M1)
    def c1_source(self) -> list[dict]:
        """The stored C1 the readers are admitted by: the board's, else the folders' (all that hold one agree)."""
        from crucibleforge import classing45 as C
        c1 = C.load_c1(self.board)
        if c1:
            return c1
        blocks = self.folder_blocks("C1_constructed")
        return [dict(v) for v in ((blocks[0] if blocks else {}).get("per_reader") or {}).values() if isinstance(v, dict)]

    def folder_blocks(self, key: str) -> list[dict]:
        out = []
        for f in self.folders:
            try:
                blk = (json.loads((f / "marks45.json").read_text(encoding="utf-8")).get("classing") or {}).get(key)
            except (OSError, ValueError, AttributeError):
                blk = None
            if isinstance(blk, dict):
                out.append(blk)
        return out

    def planned_c6(self):
        """(C6 block, {label: pick text}, error) computed in memory from the folders' stores (dry run and plan): the
        same computation b-c6 runs, nothing written, no reader called."""
        if self._c6plan is None:
            try:
                from crucibleforge import classing45 as C, lineage
                prof = self.profile()
                c1 = self.c1_source()
                marks = json.loads(C.MARKS_PATH.read_text(encoding="utf-8"))
                blk = C.store_marks(self.folders, prof, marks, c1=c1)["C6_reader_refusal"]
                sha = C.load_prompt(prof)[1]
                picks = {}
                for f in self.folders:
                    lab = label_of(f)
                    if not c1:
                        picks[lab] = "no stored C1: no reader admitted (read45 stops)"
                        continue
                    try:
                        rs = C.pick_readers(lineage.family(lab) or lab, prof, c1=c1, prompt_sha=sha,
                                            removed=C.removed_of(blk))
                        picks[lab] = ", ".join(r.name for r in rs)
                    except C.ReadersUnavailable as e:
                        picks[lab] = f"pending readers ({e})"
                    except Exception as e:          # noqa: BLE001 -- printed, read45 refuses the same way
                        picks[lab] = f"refused ({e})"
                self._c6plan = (blk, picks, None)
            except Stop:
                raise
            except Exception as e:                  # noqa: BLE001 -- the plan line says why
                self._c6plan = (None, {}, f"{type(e).__name__}: {e}")
        return self._c6plan

    def seed_c1_from_folders(self) -> None:
        """A fresh board takes the folders' stored C1 / C7 admission (refused when the folders disagree)."""
        p = self.board / "marks45.json"
        try:
            cur = json.loads(p.read_text(encoding="utf-8")) if p.is_file() else {}
        except ValueError:
            cur = {}
        cur = cur if isinstance(cur, dict) else {}
        cls = cur.get("classing") if isinstance(cur.get("classing"), dict) else {}
        seeded = []
        for key in ("C1_constructed", "C7_constructed"):
            if cls.get(key):
                continue
            blocks = self.folder_blocks(key)
            if not blocks:
                continue
            if any(b != blocks[0] for b in blocks[1:]):
                raise Stop(f"b-c6: the folders hold different stored {key} results; seed the board with "
                           "--admission <file> (nothing was picked for you)", 2)
            cls[key] = blocks[0]
            seeded.append(key)
        if seeded:
            cur["classing"] = cls
            self.board.mkdir(parents=True, exist_ok=True)
            tmp = p.with_name(p.name + f".tmp{os.getpid()}")
            tmp.write_text(json.dumps(cur, indent=1), encoding="utf-8")
            os.replace(tmp, p)
            self.log(self.board, f"SEEDED {', '.join(seeded)} from the folders' marks45.json")

    def step_b_c6(self, where: Path) -> None:
        from crucibleforge import classing45 as C
        self.step_c_assemble(where)                      # copies only: the board's rows and reader stores
        self.seed_c1_from_folders()
        self.checked_call(self.board, self.script("check_classing_marks.py", "--results", self.board, "--skip-c1",
                                                  "--profile", self.o.profile),
                          "reader refusal C6 (before any read)", [(self.board / "marks45.json", ("classing",))])
        c6 = C.load_c6(self.board)
        if not C.c6_current(c6):
            raise Stop(f"b-c6: {self.board / 'marks45.json'} holds no C6 block of the current rule; no read is "
                       "planned", 3)
        self.log(self.board, f"C6 {c6.get('status')}: " + " | ".join((c6.get("arithmetic") or {}).values()))
        gone = C.removed_of(c6)
        self.say(f"  C6 before any read: {c6.get('status')}" + (f", {len(gone)} reader(s) removed for the run"
                                                                if gone else ""))
        self.spread(("classing",))

    def step_b_classing(self, where: Path) -> None:
        o = self.o
        c1 = self.board / "marks45.json"
        base = self.script("read45.py", "--results", where, "--profile", o.profile)
        if o.classing_readers:
            base += ["--readers", o.classing_readers]
        if c1.is_file():
            base += ["--c1", c1]
        self.call(where, base + ["--dry-run"], ok=(0, 4), what="reader check (no reader admitted?)")
        cmd = base + (["--cap-usd", o.classing_cap_usd] if o.classing_cap_usd is not None else [])
        if o.nsfw_pack_cap_usd is not None:
            cmd += ["--pack-cap-usd", o.nsfw_pack_cap_usd]
        summ = where / "readers45" / "summary.json"
        before = self._stamp(summ)
        rc = self.call(where, cmd, ok=(0, 4), what="reader votes")
        if rc == 4:
            # done with pending (R1): read45 wrote, in THIS call, which models wait for readers and why
            body = None
            if self._stamp(summ) != before:
                try:
                    body = json.loads(summ.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    body = None
            pend = {k: v["pending"] for k, v in (body or {}).items() if isinstance(v, dict) and v.get("pending")}
            if not pend:
                raise Stop(f"{where.name}: read45 said 'done with pending' but wrote no pending reason to {summ}; "
                           "the step is not marked done", 3)
            self._pending = pend

    def step_b_chat(self, where: Path) -> None:
        o = self.o
        plan_cmd = self.script("read_chat45.py", "--results", where, "--folders", where) + \
            (["--no-hosted"] if o.no_chat_hosted else [])
        rc, text = self.capture(plan_cmd, self.env)
        self.log(where, f"read_chat45 plan rc={rc}: {text.strip()[:300]}")
        if rc or "NO FITTING READER" in text:
            raise Stop(f"{where.name}: no chat-scene reader is named for this model's family "
                       f"(calibration/chat-readers-45.yaml): {text.strip()[:200]}; nothing was picked for you", 2)
        m = re.search(r"run: (crucibleforge agree .*)$", text, re.M)
        if not m:
            raise Stop(f"{where.name}: read_chat45 printed no agree command: {text.strip()[:200]}", 3)
        argv = m.group(1).split()[1:]
        if "needs --cap-usd" in text:
            if o.chat_cap_usd is None:
                raise Stop(f"{where.name}: the chat-scene reader is hosted and needs --chat-cap-usd", 2)
            argv += ["--cap-usd", str(o.chat_cap_usd)]
        self.call(where, [self.cf, *argv], what="chat-scene reader (agree)")
        self.call(where, plan_cmd + ["--import-agree"], what="import chat-scene reader records")
        # done only when every judged long scene has the second reading; otherwise the step stays resumable
        rc, text = self.capture(self.script("read_chat45.py", "--results", where, "--folders", where, "--verify")
                                + (["--no-hosted"] if o.no_chat_hosted else []), self.env)
        self.log(where, f"read_chat45 verify rc={rc}: {text.strip()[:500]}")
        if rc:
            raise Stop(f"{where.name}: chat-scene second reading incomplete, step not marked done "
                       f"(re-run to resume): {text.strip().splitlines()[-1][:300] if text.strip() else ''}", 3)

    def pack_units_of(self, folder: Path) -> dict:
        from crucibleforge import packs45
        return packs45.pack_units(read_rows(folder))

    def step_b_packs(self, where: Path) -> None:
        o = self.o
        if not self.pack_units_of(where):
            self.log(where, "b-packs: no Chat pack rows, nothing to read")
            return
        base = self.script("read_chat45.py", "--results", where, "--folders", where, "--packs") + \
            (["--no-hosted"] if o.no_chat_hosted else [])
        rc, text = self.capture(base, self.env)
        self.log(where, f"read_chat45 --packs plan rc={rc}: {text.strip()[:300]}")
        if rc or "NO FITTING READER" in text:
            raise Stop(f"{where.name}: no pack-scene reader is named for this model's family "
                       f"(calibration/chat-readers-45.yaml): {text.strip()[:200]}; nothing was picked for you", 2)
        # resumable: when the store already holds every pack scene's reading there is nothing to call (and no cap)
        rc, text_v = self.capture(base + ["--verify"], self.env)
        self.log(where, f"read_chat45 --packs verify rc={rc}: {text_v.strip()[:300]}")
        if rc == 0:
            return
        m = re.search(r"run: (crucibleforge agree .*)$", text, re.M)
        if not m:
            raise Stop(f"{where.name}: read_chat45 --packs printed no agree command: {text.strip()[:200]}", 3)
        argv = m.group(1).split()[1:]
        if "needs --cap-usd" in text:
            if o.pack_cap_usd is None:
                raise Stop(f"{where.name}: the pack-scene reader is hosted and needs --pack-cap-usd "
                           "(no reader was called)", 2)
            argv += ["--cap-usd", str(o.pack_cap_usd)]
        self.call(where, [self.cf, *argv], what="pack-scene reader (agree)")
        rc, text_v = self.capture(base + ["--verify"], self.env)
        self.log(where, f"read_chat45 --packs verify rc={rc}: {text_v.strip()[:500]}")
        if rc:
            raise Stop(f"{where.name}: pack-scene second reading incomplete, step not marked done "
                       f"(re-run to resume): {text_v.strip().splitlines()[-1][:300] if text_v.strip() else ''}", 3)

    def step_c_assemble(self, where: Path) -> None:
        """The combined board dir: copies only (the folders are never touched). A model's rows from several runs sit
        in one transcripts file, the same way an accumulating run stores them."""
        b = self.board
        seen: dict[str, set] = {}
        for f in self.folders:
            for src in (transcripts(f) + sorted(f.glob("meta_*.json")) + sorted(f.glob("readers45/*.jsonl"))
                        + sorted(f.glob("readers45/nsfwpacks45/*.jsonl")) + sorted(f.glob("judge45/*.jsonl"))
                        + sorted(f.glob("judge45/check.json"))):
                rel = src.relative_to(f)
                dst = b / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                if src.suffix == ".jsonl":
                    have = seen.setdefault(str(rel), set(dst.read_text(encoding="utf-8").splitlines())
                                           if dst.is_file() and str(rel) not in seen else set())
                    new = [ln for ln in src.read_text(encoding="utf-8").splitlines() if ln.strip() and ln not in have]
                    if new:
                        with open(dst, "a", encoding="utf-8") as fh:
                            fh.write("\n".join(new) + "\n")
                        have.update(new)
                    dst.touch(exist_ok=True)
                elif not dst.exists():
                    dst.write_bytes(src.read_bytes())
        self.assemble_marks()
        self.assemble_forfeits()

    def assemble_forfeits(self) -> None:
        """4.5.1: the board carries every folder's declared forfeits (forfeits.json, keyed by model, case and
        repeat), so a job a folder counts as a failed answer is counted the same way on the combined board. A record
        applies on the board only while that job is still missing there (a later run's answer counts instead).
        Rebuilt from the folders every time: a forfeit undone in its folder leaves the board too."""
        recs: dict[tuple, dict] = {}
        for f in self.folders:
            p = f / "forfeits.json"
            if not p.is_file():
                continue
            try:
                body = json.loads(p.read_text(encoding="utf-8"))
            except ValueError:
                raise Stop(f"c-assemble: {p} is unreadable; fix or remove it (a forfeit is never guessed)", 2)
            for r in body if isinstance(body, list) else ():
                if isinstance(r, dict) and r.get("model") and r.get("case_id"):
                    recs.setdefault((r["model"], r["case_id"], r.get("repeat")), r)
        p = self.board / "forfeits.json"
        if not recs:
            if p.is_file():
                p.unlink()
            return
        tmp = p.with_name(p.name + f".tmp{os.getpid()}")
        tmp.write_text(json.dumps(list(recs.values()), indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
        os.replace(tmp, p)

    def assemble_marks(self) -> None:
        """Review A F6/F7: the board carries the folders' mark results (``marks45.json``), so a check that FAILED in
        the folders shows as failed on the board, not as "cannot be tested yet". A block is copied when every folder
        that holds it holds the same result; a block the folders disagree on is not copied (logged): the board then
        says the check was not applied rather than picking one folder's verdict. Blocks only the board writes (the
        c-* steps) are kept when the folders do not hold them."""
        blocks: dict[str, list] = {}
        for f in self.folders:
            p = f / "marks45.json"
            if not p.is_file():
                continue
            try:
                body = json.loads(p.read_text(encoding="utf-8"))
            except ValueError:
                self.log(self.board, f"ASSEMBLE marks: {p} unreadable, not used")
                continue
            for k, v in (body.items() if isinstance(body, dict) else ()):
                blocks.setdefault(k, []).append(v)
        if not blocks:
            return
        p = self.board / "marks45.json"
        try:
            cur = json.loads(p.read_text(encoding="utf-8")) if p.is_file() else {}
        except ValueError:
            cur = {}
        cur = cur if isinstance(cur, dict) else {}
        for k, vs in blocks.items():
            if all(v == vs[0] for v in vs[1:]):
                cur[k] = vs[0]
            else:
                self.log(self.board, f"ASSEMBLE marks: the folders disagree on block {k!r}; not copied")
        tmp = p.with_name(p.name + f".tmp{os.getpid()}")
        tmp.write_text(json.dumps(cur, indent=1, default=list), encoding="utf-8")
        os.replace(tmp, p)

    def step_c_classing(self, where: Path) -> None:
        self.checked_call(where, self.script("check_classing_marks.py", "--results", where, "--skip-c1"),
                          "classing marks", [(where / "marks45.json", ("classing",))])
        self.spread(("classing",))

    def step_c_longscene(self, where: Path) -> None:
        cmd = self.script("check_rplong_marks45.py", "--results", where, "--folders", *self.folders)
        self.checked_call(where, cmd, "long-scene marks", [(where / "marks45.json", ("chat_rplong",))])
        self.spread(("chat_rplong",))

    def step_c_packmarks(self, where: Path) -> None:
        cmd = self.script("check_pack_marks45.py", "--results", where, "--folders", *self.folders)
        if self.anchors:
            cmd += ["--anchors", *self.anchors]
        self.checked_call(where, cmd, "pack marks", [(where / "marks45.json", ("chat_packs",))])
        self.spread(("chat_packs",), dsts=[*self.folders, *self.anchors])

    def step_c_nsfwpackmarks(self, where: Path) -> None:
        cmd = self.script("check_nsfw_pack_marks45.py", "--results", where, "--profile", self.o.profile,
                          "--folders", *self.folders)
        if self.o.nsfw_pack_willing:
            cmd += ["--willing", self.o.nsfw_pack_willing]
        self.checked_call(where, cmd, "nsfw pack marks", [(where / "marks45.json", ("nsfw_packs",))])
        self.spread(("nsfw_packs",))

    def assistant_identity(self) -> list[str]:
        """``--set`` arguments naming the reference and the damaged stand-in: read, never typed here, from the marks
        evaluation addendum (EV3_identity: the models segment-marks-45.json pair_rule.models names, or --reference).
        Identity only: the marks and thresholds of assistant_marks_20261119.json are untouched."""
        import check_marks45 as CM
        try:
            marks = CM._load_marks()
        except CM.Integrity as e:
            raise Stop(f"assistant marks: the marks files are not indexed or unreadable: {e}", 2)
        if "EV3_identity" not in marks["evaluation"]["body"]:
            raise Stop("assistant marks: the evaluation addendum no longer names EV3_identity; nothing was guessed", 2)
        models = dict(marks["segments"]["body"]["pair_rule"]["models"])
        if self.o.reference:
            models["reference"] = self.o.reference
        out: list[str] = []
        for role in ("reference", "damaged"):
            out += ["--set", f"{role}={','.join(CM._patterns(models[role]))}"]
        return out

    def step_c_assistant(self, where: Path) -> None:
        dirs: list[Path] = []
        for d in [*self.folders, *self.anchors, *self.assistant_runs]:
            if d not in dirs:
                dirs.append(d)
        out_file = where / "assistant_marks.json"
        cmd = self.script("check_assistant_marks.py", *dirs, *self.assistant_identity(), "--profile", self.o.profile,
                          "--out", out_file)
        self.checked_call(where, cmd, "assistant marks", [(out_file, None)])
        for f in self.folders:
            (f / "assistant_marks.json").write_bytes(out_file.read_bytes())

    def step_c_marks(self, where: Path) -> None:
        # reports first: the check reads each report's marks_input45 (the values before any mark holds a segment)
        for f in [*self.folders, *self.anchors]:
            self.call(f, [self.cf, "--results", str(f), "report", "--profile", self.o.profile], what="report")
        cmd = self.script("check_marks45.py", "--folders", *self.folders, "--no-refresh")
        if self.anchors:
            cmd += ["--anchors", *self.anchors]
        if self.o.reference:
            cmd += ["--reference", self.o.reference]
        self.checked_call(where, cmd, "marks", [(f / "marks45.json", (*MARK_KEYS, "check_marks45"))
                                                for f in self.folders])
        # the same result blocks on the combined board
        self.spread(MARK_KEYS + ("check_marks45",), src=self.folders[0], dsts=[self.board])
        if self.anchors:
            self.write_anchors()

    # ---- validation anchors: used by the marks, listed apart, never ranked
    def write_anchors(self) -> None:
        out = []
        for f in self.anchors:
            label = label_of(f)
            share = None
            try:
                m = (json.loads((f / "report.json").read_text(encoding="utf-8")).get("models") or {}).get(label) or {}
                ev = ((m.get("marks_input45") or {}).get("segments") or {}).get("chat/everyday") or {}
                if ev.get("points") is not None and ev.get("max"):
                    share = round(float(ev["points"]) / float(ev["max"]), 4)
            except (OSError, ValueError, TypeError):
                share = None
            out.append({"label": label, "folder": f.name, "everyday_share": share})
        if not self.dry:
            p = self.board / ANCHORS_FILE
            tmp = p.with_name(p.name + f".tmp{os.getpid()}")
            tmp.write_text(json.dumps({"anchors": out}, indent=1), encoding="utf-8")
            os.replace(tmp, p)

    # ---- admission seeding
    def admission_seeds(self) -> dict:
        """{C1_constructed, C7_constructed} from --admission, each result checked against the indexed readers and
        prompts; Stop(2) on any result that is not."""
        from crucibleforge import classing45 as C, marksindex45
        try:
            body = json.loads(Path(self.o.admission).read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            raise Stop(f"--admission: unreadable ({e})", 2)
        cls = (body.get("classing") if isinstance(body, dict) else None) or {}
        named = {str(d.get("model_id")) for d in C.named_readers().values() if d.get("model_id")}
        prompts = {"C1_constructed": "nsfw-classing-prompt-45", "C7_constructed": "nsfw-opinion-flags-prompt-45"}
        shas = {k: {sha for path, sha in marksindex45.rows() if Path(path).name.startswith(stem)}
                for k, stem in prompts.items()}
        seeds = {}
        for k in prompts:
            blk = cls.get(k)
            if not isinstance(blk, dict):
                continue
            for rid, r in (blk.get("per_reader") or {}).items():
                if not isinstance(r, dict):
                    raise Stop(f"--admission: {k} {rid}: not a result", 2)
                if str(r.get("model")) not in named:
                    raise Stop(f"--admission: {k} {rid}: reader model {r.get('model')!r} is not named by an indexed "
                               "reader addendum", 2)
                if r.get("prompt_sha") not in shas[k]:
                    raise Stop(f"--admission: {k} {rid}: prompt sha {str(r.get('prompt_sha'))[:12]} is not an indexed "
                               f"{prompts[k]} file", 2)
            seeds[k] = blk
        if not seeds:
            raise Stop("--admission: the file holds no classing.C1_constructed or classing.C7_constructed results", 2)
        return seeds

    def seed_admission(self, seeds: dict) -> None:
        for d in [*self.folders, *self.anchors, self.board]:
            p = d / "marks45.json"
            try:
                cur = json.loads(p.read_text(encoding="utf-8")) if p.is_file() else {}
            except ValueError:
                cur = {}
            cur = cur if isinstance(cur, dict) else {}
            cls = cur.get("classing") if isinstance(cur.get("classing"), dict) else {}
            merged = {**cls, **seeds}
            for k, blk in seeds.items():                       # a reader measured since the seed file was written is kept
                have = (cls.get(k) or {}).get("per_reader") if isinstance(cls.get(k), dict) else None
                if isinstance(have, dict) and isinstance(blk.get("per_reader"), dict):
                    merged[k] = {**blk, "per_reader": {**blk["per_reader"], **have}}
            cur["classing"] = merged
            tmp = p.with_name(p.name + f".tmp{os.getpid()}")
            tmp.write_text(json.dumps(cur, indent=1, default=list), encoding="utf-8")
            os.replace(tmp, p)
            self.log(d, f"SEEDED admission {', '.join(sorted(seeds))} from {self.o.admission}")

    def step_c_sanity(self, where: Path) -> None:
        cmd = self.script("check_sanity45.py", "--results", where, "--folders", *self.folders,
                          "--profile", self.o.profile)
        self.checked_call(where, cmd, "sanity block", [(where / "marks45.json", ("sanity",))])
        try:
            blk = json.loads((where / "marks45.json").read_text(encoding="utf-8")).get("sanity") or {}
        except (OSError, ValueError):
            blk = {}
        entries = [e for e in blk.get("entries") or [] if isinstance(e, dict)]
        acc = set(getattr(self.o, "accept_sanity", None) or [])
        who = getattr(self.o, "accepted_by", None) or os.environ.get("USER") or "unknown"
        for e in entries:
            if e.get("code") in acc:
                self.log(where, f"ACCEPTED sanity {e['code']} by {who} at {time.strftime('%Y-%m-%dT%H:%M:%S')}: "
                                f"{e.get('detail', '')}")
        open_ = [e for e in entries if e.get("code") not in acc]
        if open_:
            for e in open_:
                self.say(f"  SANITY {e.get('code')}: {e.get('detail', '')}")
            raise Stop(f"c-sanity: {len(open_)} sanity entr{'y' if len(open_) == 1 else 'ies'} not accepted ("
                       + ", ".join(str(e.get("code")) for e in open_) + "); the lead reads them and re-runs with "
                       "--accept-sanity <code> for each one accepted", 7)

    def step_d_report(self, where: Path) -> None:
        self.call(where, [self.cf, "--results", str(where), "report", "--profile", self.o.profile], what="report")
        self.say(f"  {where.name}: report.md / report.json / report.html written")

    def step_e_board(self, where: Path) -> None:
        self.step_d_report(where)

    def step_b_judge(self, where: Path) -> None:
        """The one local judge reads anything still unjudged in this folder (stored verdicts are reused; the adult page
        is classed in the same pass). Never a hosted judge (judge45.HostedGrader)."""
        self.call(where, [self.cf, "--results", str(where), "judge", "--profile", self.o.profile,
                          "--models", label_of(where)], what="judge")

    def step_e_accuracy(self, where: Path) -> None:
        self.call(where, self.script("accuracy45.py", where / "report.json"), what="accuracy")

    def step_e_resolution(self, where: Path) -> None:
        """The replicate folders' spread on the board's own scale, read back from each folder's rendered report.json
        (no rescoring, no bootstrap): <board>/precision45.json, which the board report reads."""
        self.call(where, self.script("resolution45.py", *self.folders, "--write-precision", self.board),
                  what="resolution")

    # ---- result blocks travel between the board dir and the folders (read-modify-write, other keys kept)
    def spread(self, keys: tuple[str, ...], src: Path | None = None, dsts: list[Path] | None = None) -> None:
        src = src or self.board
        try:
            body = json.loads((src / "marks45.json").read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            raise Stop(f"{self.step}: {src / 'marks45.json'} is unreadable ({e}); nothing to relay", 3)
        absent = [k for k in keys if k not in body]
        if absent:
            raise Stop(f"{self.step}: {src / 'marks45.json'} holds no {', '.join(absent)} block; the blocks in the "
                       "folders would be stale and are not relayed", 3)
        for d in (dsts if dsts is not None else self.folders):
            p = d / "marks45.json"
            try:
                cur = json.loads(p.read_text(encoding="utf-8")) if p.is_file() else {}
            except ValueError:
                cur = {}
            changed = False
            for k in keys:
                if k in body and cur.get(k) != body[k]:
                    cur[k] = body[k]
                    changed = True
            if changed:
                tmp = p.with_name(p.name + f".tmp{os.getpid()}")
                tmp.write_text(json.dumps(cur, indent=1, default=list), encoding="utf-8")
                os.replace(tmp, p)


def C6_removed(blk) -> list[dict]:
    from crucibleforge import classing45 as C
    return C.removed_of(blk)


def parse(argv=None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--folders", nargs="+", required=True)
    ap.add_argument("--board", default=None)
    ap.add_argument("--only", default=None, help="a step name or letter a-e (comma list)")
    ap.add_argument("--force", default=None, help="comma list of steps to re-run even when done at this commit")
    ap.add_argument("--report-jobs", type=int, default=REPORT_JOBS,
                    help=f"folder reports rendered at the same time (default {REPORT_JOBS})")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--profile", default="bench45")
    ap.add_argument("--classing-readers", default=None, help="names for C1 / read45 (never picked for you)")
    ap.add_argument("--classing-cap-usd", type=float, default=None)
    ap.add_argument("--chat-cap-usd", type=float, default=None)
    ap.add_argument("--pack-cap-usd", type=float, default=None,
                    help="spend cap of the hosted Chat-pack reader, PER FOLDER (b-packs passes it to each folder's agree "
                         "call, so N hosted folders may spend N x this; refused with exit 2 when absent and a "
                         "hosted reader would be called)")
    ap.add_argument("--nsfw-pack-cap-usd", type=float, default=None,
                    help="spend cap of the hosted classing readers on the NSFW+ pack units, PER FOLDER (b-classing; "
                         "refused with exit 2 when absent and a hosted reader would be called)")
    ap.add_argument("--nsfw-pack-willing", default=None,
                    help="comma list of run labels forming the willing pilot cohort of the NSFW+ pack marks")
    ap.add_argument("--no-chat-hosted", action="store_true")
    ap.add_argument("--spend-cap-usd", type=float, default=None, help="--spend-cap-usd of each generating call")
    ap.add_argument("--spend-run-cap-usd", type=float, default=None, help="sets CF_SPEND_RUN_CAP_USD")
    ap.add_argument("--spend-total-cap-usd", dest="spend_cap_usd_total", type=float, default=None,
                    help="sets CF_SPEND_CAP_USD")
    ap.add_argument("--spend-ledger", default=None, help="sets CF_SPEND_LEDGER")
    ap.add_argument("--reference", default=None, help="reference label for the headroom step and the marks")
    ap.add_argument("--anchors", nargs="*", default=[], help="validation-only run folders: used by the marks, never "
                                                             "ranked or shown as launch rows")
    ap.add_argument("--assistant-runs", nargs="*", default=[], help="extra run folders (any number of models each) "
                    "holding stored Assistant-basics runs of set models: read by the assistant marks only, never "
                    "ranked, never on the board (every stored run of a set model counts)")
    ap.add_argument("--accept-sanity", action="append", default=[], metavar="CODE",
                    help="a sanity entry the lead accepts (one per entry, repeatable); logged with who and when")
    ap.add_argument("--accepted-by", default=None, help="who accepts the sanity entries (default: $USER)")
    ap.add_argument("--admission", default=None, help="a marks45.json-shaped file holding the stored C1 / C7 "
                                                      "admission results to seed into every folder")
    return ap.parse_args(argv)


def main(argv=None, runner=default_runner, capturer=capture) -> int:
    a = parse(argv)
    folders = [Path(f).resolve() for f in a.folders]
    board = Path(a.board).resolve() if a.board else folders[0].parent / "board45"
    return Release(folders, board, a, runner, capturer).go()


if __name__ == "__main__":
    sys.exit(main())
