"""4.5.1 bare-closer tolerance (PLAN-451-LIMITS step 4c, lead ruling 2026-11-25).

A SEARCH/REPLACE block closed by a bare ``>>>>>>>`` (five to nine ``>``, no ``REPLACE``) used to make the whole
block unreadable, so a correct fix scored as no edit. :func:`install` wraps ``codebank.parse_edits`` (a module
global its callers resolve at call time: ``codebank._answer_tree`` for the code bank, the patch families through
``codebank.grade_repo_tests`` and the fix checker through ``fixcheck.grade_answer``): inside a block that has a
``<<<<<<< SEARCH`` line and then a ``=======`` line with no closer yet, the LAST line matching ``^>{5,9}[ \\t]*$`` before the next
``SEARCH`` line or the end of the text is rewritten to ``>>>>>>> REPLACE`` (a block that does reach a proper
closer keeps every bare line as replacement text: a markdown quote, a conflict marker in a file); then the original parser runs. Nothing else loosens. ``codebank.py`` is not
edited (its bytes decide grader stamps), so a row graded with the wrapper says so: ``tolerated: {bare_closer: n,
rev: "et-1"}``.

Stored rows: ``python -m crucibleforge.edittol MODEL [--go]`` re-grades every stored row that holds a bare closer
from its stored reply (no model call): chain and pack rows through ``crucibleforge.regrade`` (switch
``bare_closer``), single code-bank / fix rows here. Dry run by default."""
from __future__ import annotations

import re

#: the revision a tolerated row records
REV = "et-2"
_OPEN = re.compile(r"^<{5,9} SEARCH[ \t]*$")
_SEP = re.compile(r"^={5,9}[ \t]*$")
_CLOSE = re.compile(r"^>{5,9} REPLACE[ \t]*$")
_BARE = re.compile(r"^>{5,9}[ \t]*$")

_original = None


def rewrite(text: str | None) -> tuple[str, int]:
    """(``text`` with each bare closer of a well-formed open block written as ``>>>>>>> REPLACE``, how many)."""
    if not text or ">>>>>" not in text:
        return text or "", 0
    lines = text.replace("\r\n", "\n").split("\n")
    state, n = None, 0               # None: outside a block; "search": after SEARCH; "replace": after the separator
    cands: list[int] = []            # bare-closer lines of the open REPLACE part (review 3 note 5)

    def settle() -> None:
        # the block ends without a proper closer: its LAST bare line closes it; earlier bare lines are file content
        nonlocal n
        if cands:
            lines[cands[-1]] = ">>>>>>> REPLACE"
            n += 1
        cands.clear()
    for i, ln in enumerate(lines):
        if _OPEN.match(ln):
            if state == "replace":
                settle()
            state = "search"
        elif state == "search" and _SEP.match(ln):
            state, cands = "replace", []
        elif state == "replace" and _CLOSE.match(ln):
            state = None             # a proper closer: every bare line before it was replacement text
            cands.clear()
        elif state == "replace" and _BARE.match(ln):
            cands.append(i)
    if state == "replace":
        settle()
    return ("\n".join(lines), n) if n else (text, 0)


def count(text: str | None) -> int:
    """Bare closers :func:`rewrite` would accept in ``text``."""
    return rewrite(text)[1]


def parse_edits(text: str, finish_reason: str | None = None):
    """``codebank.parse_edits`` after :func:`rewrite`."""
    return _original(rewrite(text)[0], finish_reason)


parse_edits._edittol = True     # marks the wrapper (one install even if this file is also loaded as __main__)


def install() -> bool:
    """Point ``codebank.parse_edits`` at the tolerant wrapper (idempotent); True when installed."""
    global _original
    from . import codebank
    if getattr(codebank.parse_edits, "_edittol", False):
        return True
    _original = codebank.parse_edits
    codebank.parse_edits = parse_edits
    return True


def uninstall() -> None:
    """Restore the original parser (tests, the with/without comparison)."""
    global _original
    from . import codebank
    if _original is not None:
        codebank.parse_edits = _original
        _original = None


def active() -> bool:
    from . import codebank
    return bool(getattr(codebank.parse_edits, "_edittol", False))


def stamp(row: dict, text: str | None, verdict: dict | None) -> None:
    """Record the tolerance on a row graded from ``text`` by the edit parser (``verdict`` has ``applied`` or
    ``format_errors``, or the row carries a ``fixcheck`` block)."""
    if not active():
        return
    edit_graded = bool(verdict) and ("applied" in verdict or "format_errors" in verdict)
    if not (edit_graded or isinstance(row.get("fixcheck"), dict)):
        return
    n = count(text)
    if n:
        row["tolerated"] = {"bare_closer": n, "rev": REV}


# ------------------------------------------------------------------ stored rows
def regrade_static(label: str, cases: list[dict], go: bool = False) -> list[dict]:
    """Single-call rows of ``label`` (not chains, not packs) whose stored reply holds a bare closer and that carry no
    ``tolerated`` stamp: graded again from the stored reply with the wrapper (runner.grade_single_row, the live
    run's code) and, with ``go``, appended as superseding rows marked ``regraded_by``. Returns one record per row."""
    import copy
    import datetime as _dt
    from . import config
    from .regrade import ROW_GRADE_KEYS, _result_of
    from .runner import grade_single_row
    from .version import row_current
    from .regrade import _pack_cases
    install()
    by_id = {c["id"]: c for c in cases if not c.get("chain")}
    for cid, c in _pack_cases().items():
        by_id.setdefault(cid, c)
    at = _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")
    out = []
    for r in config.load_transcripts(label):
        c = by_id.get(r.get("case_id"))
        if (c is None or r.get("chain") or r.get("tolerated") or r.get("error") or r.get("grade") == "error"
                or r.get("turn") not in (None, 0) or not count(r.get("response"))):
            continue
        if c.get("turns") or c.get("multi_items") or c.get("console") or c.get("env"):
            continue
        if c.get("generator") and not c.get("grader_config"):
            c = copy.deepcopy(c)
            config._materialize_generated(c)
            by_id[r.get("case_id")] = c
        rec = {"case_id": r.get("case_id"), "repeat": r.get("repeat"), "bench_run_id": r.get("bench_run_id"),
               "bare_closer": count(r.get("response")), "grade_before": r.get("grade"), "score_before": r.get("score")}
        if not row_current(r):
            rec.update(status="refused", why="case content changed since the row was made (it is re-made)")
            out.append(rec)
            continue
        new = copy.deepcopy(r)
        for k in ROW_GRADE_KEYS + ("fixcheck",):
            new.pop(k, None)
        res = _result_of(r)
        grade_single_row(new, res, c, False, r.get("max_tokens_sent"))
        new["tolerated"] = {"bare_closer": rec["bare_closer"], "rev": REV}
        new["regraded_by"] = {"tool": "crucibleforge.edittol", "reason": "bare >>>>>>> closer accepted (4.5.1)",
                              "at": at, "regraded": ["grade"],
                              **({"previous": r["regraded_by"]} if r.get("regraded_by") else {})}
        rec.update(status="regrade", grade_after=new.get("grade"), score_after=new.get("score"))
        out.append(rec)
        if go:
            config.append_transcript(label, new)
    return out


def main(argv: list[str] | None = None) -> int:
    """``python -m crucibleforge.edittol MODEL [--go]``: chain and pack rows via the regrade tool (switch
    ``bare_closer``), then single code-bank and fix rows."""
    import argparse
    import sys
    from . import config, profiles, regrade
    ap = argparse.ArgumentParser(prog="python -m crucibleforge.edittol", description=main.__doc__)
    ap.add_argument("model")
    ap.add_argument("--go", action="store_true", help="append the superseding rows (default: dry run)")
    a = ap.parse_args(argv)
    install()
    rc = regrade.main([a.model, "--switch", "bare_closer", "--reason", "bare >>>>>>> closer accepted (4.5.1)"]
                      + (["--go"] if a.go else []))
    rows = config.load_transcripts(a.model)
    pname = next((r.get("profile") for r in reversed(rows) if r.get("profile")), None)
    if not pname:
        return rc
    cfg = config.load_config()
    _cfg, cases = profiles.apply_profile(profiles.load_profile(pname, cfg), cfg)
    recs = regrade_static(a.model, cases, go=a.go)
    for r in recs:
        print(f"  {r['case_id']} r{r.get('repeat')}: {r['status']} ({r['bare_closer']} bare closer(s)) "
              f"{r.get('grade_before')} -> {r.get('grade_after', '-')}" + (f"; {r['why']}" if r.get("why") else ""))
    n = sum(1 for r in recs if r["status"] == "regrade")
    print(f"{'appended' if a.go else 'dry run:'} {n} single row(s) {'regraded' if a.go else 'would be regraded'}",
          file=sys.stderr)
    return rc


if __name__ == "__main__":
    import sys
    from crucibleforge.edittol import main as _main     # one module object, one installed wrapper
    sys.exit(_main())
