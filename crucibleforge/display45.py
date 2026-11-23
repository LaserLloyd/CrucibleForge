"""Reader-facing words for the 4.5 board: short score cells, the Points table lines and the "What is held back and
why" section. Display only: no number, mark, grader or selection rule lives here, and the machine strings stay
unchanged in ``marks45.json`` and ``report.json``. A machine reason this module does not know is still printed
(cut to one line), never dropped.

Kept apart from ``segments45.py`` on purpose: that file is part of the scorer stamp, and a wording change must not
change the stamp of a published number.
"""
from __future__ import annotations

import ast
import fnmatch
import re
from html import escape as _esc

from . import segments45 as _s45

#: roles named in the marks files -> the words a reader sees. ``reference``, ``heretic`` and ``dark_scarlett`` are
#: the model's own label on the board when it is there (see :func:`role_name`).
ROLE_NAMES = {
    "ds_flash": "DeepSeek v4 flash",
    "minimax": "MiniMax M3.1 Flash",
    "damaged": "the damaged stand-in model",
    "small": "a small model",
    "grok": "Grok",
}
ROLE_FALLBACK = {"reference": "the reference model", "heretic": "the heretic model",
                 "dark_scarlett": "the Dark Scarlett model"}

#: labels of the segments the board's tree does not hold (NSFW+), by key
SEG_LABELS = {"nsfw/erotica": "Erotica", "nsfw/roleplay": "Role-play", "nsfw/range": "Range",
              "nsfw/anatomy": "Anatomy", "unc/willingness": "Willingness", "unc/knowledge": "Knowledge"}

ONE_LINE = 110

_PARTS = r"(?:ceiling|primary|secondary\d*|written_primary|reference_share|damaged_gap)"


def one_line(text: str, n: int = ONE_LINE) -> str:
    t = " ".join(str(text or "").split())
    return t if len(t) <= n else t[: n - 3].rstrip() + "..."


def cap(text: str) -> str:
    return text[:1].upper() + text[1:] if text else text


def _norm(label: str) -> str:
    return str(label).lower().replace(".", "-")


def role_patterns() -> dict[str, str]:
    """{role: glob text} from the marks file (``pair_rule.models``); {} when it cannot be read."""
    try:
        return dict(((_s45._cal("segment-marks-45.json").get("pair_rule") or {}).get("models")) or {})
    except Exception:           # noqa: BLE001 -- display only
        return {}


def role_name(role: str, board_labels, patterns: dict[str, str] | None = None) -> str:
    """The reader's name for a role: a fixed phrase, or the model's label on this board when one matches."""
    if role in ROLE_NAMES:
        return ROLE_NAMES[role]
    pats = [_norm(p.strip()) for p in re.split(r"\s*/\s*", (patterns if patterns is not None else role_patterns())
                                               .get(role, ""))
            if p.strip()]
    hits = sorted(l for l in board_labels if any(fnmatch.fnmatchcase(_norm(l), p) for p in pats))
    if len(hits) == 1:
        return hits[0]
    return ROLE_FALLBACK.get(role, role.replace("_", " "))


def seg_labels(prof: dict | None) -> dict[str, str]:
    """{segment key: label}: ``code/fixes`` -> "Fixes and changes existing code"."""
    out = dict(SEG_LABELS)
    for sk in _s45.SECTIONS:
        for k, seg in ((_s45.tree(prof) or {}).get(sk) or {}).items():
            if isinstance(seg, dict):
                out[f"{sk}/{k}"] = str(seg.get("label") or k)
    return out


# ------------------------------------------------------------------ machine reason -> atoms
def _split_top(text: str) -> list[str]:
    """Split on "; " outside brackets."""
    out, depth, cur, i = [], 0, "", 0
    while i < len(text):
        ch = text[i]
        if ch in "([":
            depth += 1
        elif ch in ")]":
            depth = max(0, depth - 1)
        if depth == 0 and text.startswith("; ", i):
            out.append(cur)
            cur, i = "", i + 2
            continue
        cur += ch
        i += 1
    out.append(cur)
    return [x.strip() for x in out if x.strip()]


_RE_ROLE = re.compile(r"^(?P<role>\w+) model not on the board: no folder holds a model matching .+$")
_RE_PEND = re.compile(r"^(?P<label>.+?): (?P<seg>(?:code|chat|nsfw|unc)/\w+) pending "
                      r"\((?:pending: )?(?P<why>.+)\)$")
_RE_NOVAL = re.compile(r"^(?P<label>.+?): no value for (?P<seg>(?:code|chat|nsfw|unc)/\w+)$")
_RE_GROUPS = re.compile(r"^\(d\) known order not testable: fewer than two of its groups on the board \((?P<l>.*)\)$")
_RE_SET = re.compile(r"^(?P<role>\w+)-model set not run$")
_RE_NOFOLDER = re.compile(r"^not run: no folder for (?P<role>\w+) \(")
_RE_AB1 = re.compile(r"^(?P<n>\d+) EXPLICIT-classed replies, the mark needs >= (?P<need>\d+)")


def parse_reason(text: str) -> list[dict]:
    """A machine reason string -> a list of atoms ``{"kind": ..., ...}``. Unknown text becomes one ``unknown``
    atom per sentence, so it still prints."""
    atoms: list[dict] = []
    text = str(text or "").replace("replies; the mark needs", "replies, the mark needs")
    for raw in _split_top(text):
        t = re.sub(rf"^{_PARTS}: ", "", raw)
        t = re.sub(r"^not tested: ", "", t)
        m = _RE_ROLE.match(t)
        if m:
            atoms.append({"kind": "role_missing", "role": m["role"]})
        elif t.startswith("grok:"):
            atoms.append({"kind": "grok_none"})
        elif _RE_PEND.match(t):
            m = _RE_PEND.match(t)
            atoms.append({"kind": "pending_rows", "label": m["label"], "seg": m["seg"], "why": m["why"]})
        elif _RE_NOVAL.match(t):
            m = _RE_NOVAL.match(t)
            atoms.append({"kind": "no_value", "label": m["label"], "seg": m["seg"]})
        elif t.startswith("every measured model is within its margin of the maximum"):
            atoms.append({"kind": "ceiling_top"})
        elif _RE_GROUPS.match(t):
            raw_l = _RE_GROUPS.match(t)["l"]
            try:
                ls = [str(x) for x in ast.literal_eval(raw_l)]
            except (ValueError, SyntaxError):
                ls = []
            atoms.append({"kind": "rplong_groups", "labels": ls})
        elif _RE_SET.match(t):
            atoms.append({"kind": "set_not_run", "role": _RE_SET.match(t)["role"]})
        elif _RE_NOFOLDER.match(t):
            atoms.append({"kind": "no_folder_role", "role": _RE_NOFOLDER.match(t)["role"]})
        elif t.startswith("no damaged or small model is on the board"):
            atoms.append({"kind": "no_damaged_small"})
        elif _RE_AB1.match(t):
            m = _RE_AB1.match(t)
            atoms.append({"kind": "ab_explicit", "n": int(m["n"]), "need": int(m["need"])})
        elif t.startswith("no reply on a card with an act key is classed SOFTENED"):
            atoms.append({"kind": "ab_softened"})
        elif t.startswith("needs writing-card replies of both classes"):
            atoms.append({"kind": "ab_both"})
        elif t == "not measured yet":
            atoms.append({"kind": "not_measured"})
        elif t == _s45.MISSING_REASON:
            atoms.append({"kind": "not_applied"})
        else:
            atoms.append({"kind": "unknown", "text": t})
    return atoms


# ------------------------------------------------------------------ pending-unit reasons
def pending_short(why: str) -> str:
    """A pending unit's reason in 2-5 words."""
    w = re.sub(r"^pending:\s*", "", str(why or "")).strip()
    lw = w.lower()
    if "no fix rows" in lw:
        return "no fix rows yet"
    if "second reader" in lw or "pinned reader" in lw:
        return "second reader not run"
    if "assistant basics not run" in lw:
        return "basics not run"
    if lw.startswith("not run") or "not run in this folder" in lw:
        return "not run"
    return one_line(w, 40) or "not measured"


_PENDING_LONG = {"no fix rows yet": "no fix results yet", "second reader not run": "chat scenes not read by a second reader",
                 "basics not run": "Assistant basics not run"}


def atom_short(a: dict) -> str:
    """The 2-5 word reason for a Points table cell."""
    k = a["kind"]
    if k in ("role_missing", "grok_none", "rplong_groups", "no_damaged_small"):
        return "order check not testable"
    if k == "pending_rows":
        return pending_short(a["why"])
    if k == "no_value":
        return "not run"
    if k == "ceiling_top":
        return "ceiling: no model below the top"
    if k in ("set_not_run", "no_folder_role"):
        return "basics marks not met"
    if k in ("ab_explicit", "ab_softened", "ab_both"):
        return "act checks not met"
    if k in ("not_measured", "not_applied"):
        return "marks not met"
    return one_line(a.get("text"), 40)


def atom_reason(a: dict, board_labels=(), patterns=None) -> str:
    """One short reason for the marks table."""
    k = a["kind"]
    if k == "role_missing":
        return f"{role_name(a['role'], board_labels, patterns)} is not on the board"
    if k == "grok_none":
        return "no Grok run for this release"
    if k == "pending_rows":
        s = pending_short(a["why"])
        return _PENDING_LONG.get(s, s)
    if k == "no_value":
        return "no value yet"
    if k == "ceiling_top":
        return "no model below the top score"
    if k == "rplong_groups":
        return "needs two groups of models on the board"
    if k == "set_not_run":
        return f"{a['role']} model set not run"
    if k == "no_folder_role":
        return f"{a['role']} model set not run"
    if k == "no_damaged_small":
        return "no damaged or small model on the board"
    if k == "ab_explicit":
        return f"{a['n']} explicit replies, {a['need']} needed"
    if k == "ab_softened":
        return "no softened reply on a keyed card"
    if k == "ab_both":
        return "needs replies of both classes"
    if k == "not_measured":
        return "not measured yet"
    if k == "not_applied":
        return "not applied to this folder yet"
    return one_line(a.get("text"))


def _uniq(xs):
    out = []
    for x in xs:
        if x and x not in out:
            out.append(x)
    return out


def marks_reason(text: str, board_labels=(), patterns=None, atoms: list[dict] | None = None) -> str:
    """One short line for a mark's machine reason: the first two distinct short reasons."""
    atoms = atoms if atoms is not None else parse_reason(text)
    sets = [a["role"] for a in atoms if a["kind"] in ("set_not_run", "no_folder_role")]
    atoms = [a for a in atoms if a["kind"] not in ("set_not_run", "no_folder_role")]
    rs = _uniq(atom_reason(a, board_labels, patterns) for a in atoms)
    if sets:
        rs.append("model sets not run: " + ", ".join(_uniq(sets)) if len(sets) > 1 else f"{sets[0]} model set not run")
    if not rs:
        return ""
    return "; ".join(rs[:2]) + (f" and {len(rs) - 2} more" if len(rs) > 2 else "")


# ------------------------------------------------------------------ cells and lines
def _own_pending(g: dict) -> list[str]:
    return [pending_short(p.get("reason")) for p in (g.get("pending") or []) if p.get("unit") != "marks"]


def _held_short(g: dict, marks: dict | None) -> str:
    """Why a segment is held by its marks, in 2-5 words (the first failed mark, else the first untestable one)."""
    ms = g.get("marks") or {}
    for mid in list(ms.get("failed") or []) + list(ms.get("missing") or []):
        for a in parse_reason(((marks or {}).get(mid) or {}).get("reason") or ""):
            return atom_short(a)
    return "marks not met"


def segment_text(g: dict, marks: dict | None = None) -> str:
    """A segment inside a Points line: ``3.7``, ``3.1 of 3.5 measured, 0.4 pending: reason``, ``pending: reason``
    or ``held: reason``."""
    own = _own_pending(g)
    if g.get("points") is not None:
        if g.get("complete") and not g.get("pending_points"):
            return f"{g['points']:.1f}"
        why = f": {_uniq(own)[0]}" if own else ""
        return f"{g['points']:.1f} of {g['measured_max']:.1f} measured, {g['pending_points']:g} pending{why}"
    if own:
        return f"pending: {_uniq(own)[0]}"
    return "pending: not run"


def section_cell(sect: dict | None, digits: int = 1) -> str:
    """Code / Chat scorecard cell: ``7.2``, ``4.4 of 5.0 measured`` or a few words."""
    if not sect:
        return "pending: not run"
    if sect.get("points") is None:
        return "pending: not run"
    pts = f"{sect['points']:.{digits}f}"
    if sect.get("complete", True) and not sect.get("pending_points"):
        acc = sect.get("accuracy")
        if acc and acc.get("half") is not None:
            from . import accuracy45 as _A
            return f"{pts} {_A.range_text(acc)}"
        return pts + (f" ± {sect['margin']:.1f}" if sect.get("margin") else "")
    return f"{pts} of {sect['measured_max']:.{digits}f} measured"


def overall_text(ov: dict) -> str | None:
    """The Overall cell of a row that is not ranked, or None when it is complete."""
    if not ov.get("pending"):
        return None
    if ov.get("measured_points") is not None:
        return f"{ov['measured_points']:.1f} of {ov['measured_max']:g} measured, not ranked"
    return "not ranked, nothing measured yet"


def points_line(tb: dict, sec: str, cut_fn=None, marks: dict | None = None) -> str:
    """``7.2 of 10 = Writes working code 3.7 + Fixes and changes existing code pending: no fix rows yet + ...``."""
    s = tb[sec]
    bits = []
    for g in s["segments"].values():
        t = segment_text(g, marks)
        w = cut_fn(g.get("cut")) if cut_fn else ""
        bits.append(f"{g['label']} {t}" + (f" ({w})" if w else ""))
    pts = "pending" if s["points"] is None else f"{s['points']:.1f}"
    return f"{pts} of {s['max']:g} = " + " + ".join(bits)


# ------------------------------------------------------------------ the held-back section
def _join_names(xs: list[str]) -> str:
    xs = list(xs)
    if len(xs) <= 1:
        return "".join(xs)
    return ", ".join(xs[:-1]) + " and " + xs[-1]


def _is(xs: list) -> str:
    return "is" if len(xs) == 1 else "are"


def _mark_name(mid: str, segs: dict[str, str]) -> str:
    grp, _, name = mid.partition(":")
    if grp == "segment":
        return f"Ceiling and known order: {segs.get(name, name)}"
    if mid == "chat_rplong":
        return "Long-scene judge check"
    if mid == "assistant":
        return "Assistant-basics marks"
    return _s45.MARK_LABEL.get(mid, mid)[:1].upper() + _s45.MARK_LABEL.get(mid, mid)[1:]


def held_back(labels: list[str], stats: dict, prof: dict | None, cut_fn=None, patterns: dict | None = None) -> dict:
    """``{"board": [sentences], "marks": [[name, state, reason]], "models": [[label, [lines]]]}``; empty lists when
    nothing is held back. ``cut_fn(cut_record)`` words a cut record (report.cut_words)."""
    labels = [l for l in labels if (stats.get(l) or {}).get("segments45")]
    out = {"board": [], "marks": [], "models": []}
    if not labels:
        return out
    pats = patterns if patterns is not None else role_patterns()
    segs = seg_labels(prof)
    marks = (stats[labels[0]].get("marks45") or {})
    np_in = any(((stats[l].get("nsfw_plus") or {}).get("numbers")) for l in labels)
    first = stats[labels[0]]["segments45"]
    seg_keys = [f"{sk}/{k}" for sk in _s45.SECTIONS if first.get(sk) for k in first[sk]["segments"]]
    held: dict[str, bool] = {}
    for sk in _s45.SECTIONS:
        for k, g in ((first.get(sk) or {}).get("segments") or {}).items():
            held[f"{sk}/{k}"] = False              # INTENT-45: a mark holds nothing (it prints as a note)
    if np_in:
        nums = (stats[labels[0]].get("nsfw_plus") or {}).get("numbers") or {}
        for nk, pre in (("nsfw", "nsfw"), ("uncensored", "unc")):
            for sk, g in ((nums.get(nk) or {}).get("segments") or {}).items():
                held[f"{pre}/{sk}"] = False
                seg_keys.append(f"{pre}/{sk}")

    def _board_name(role):
        return role_name(role, labels, pats)

    # ---- board level: from the marks governing a held segment
    role_segs: dict[str, list[str]] = {}
    grok_segs: list[str] = []
    ceil_segs: list[str] = []
    rpl: tuple[list[str], list[str]] | None = None
    sets: list[str] = []
    sets_segs: list[str] = []
    damaged_small = False
    other: list[str] = []
    marks_in_scope: list[str] = []
    for key in seg_keys:
        for mid in _s45.noted_marks(key):
            if mid not in marks_in_scope:
                marks_in_scope.append(mid)
            r = marks.get(mid) or {}
            if not held.get(key) or r.get("status") == "pass":
                continue
            lab = segs.get(key, key)
            for a in parse_reason(r.get("reason") or ""):
                k = a["kind"]
                if k == "role_missing":
                    role_segs.setdefault(a["role"], [])
                    if lab not in role_segs[a["role"]]:
                        role_segs[a["role"]].append(lab)
                elif k == "grok_none":
                    grok_segs.append(lab) if lab not in grok_segs else None
                elif k == "ceiling_top":
                    ceil_segs.append(lab) if lab not in ceil_segs else None
                elif k == "rplong_groups":
                    rpl = rpl or (a["labels"], [])
                    rpl[1].append(lab) if lab not in rpl[1] else None
                elif k == "set_not_run":
                    sets.append(a["role"]) if a["role"] not in sets else None
                    sets_segs.append(lab) if lab not in sets_segs else None
                elif k == "no_damaged_small":
                    damaged_small = True
                    role_segs.setdefault("damaged", [])
                    if lab not in role_segs["damaged"]:
                        role_segs["damaged"].append(lab)
                elif k in ("pending_rows", "no_value", "no_folder_role"):
                    continue                  # a model's own state or already said by a set line: not board level
                else:
                    other.append(f"{_mark_name(mid, segs)}: {atom_reason(a, labels, pats)}")
    board: list[str] = []
    for role, ss in role_segs.items():
        if role == "damaged":
            who = "No damaged or small model is" if damaged_small else "The damaged stand-in model is"
            board.append(f"{who} on this board, so {_join_names(ss)} cannot show that it tells a sound model from a "
                         "broken one.")
        else:
            nm = cap(_board_name(role))
            if len(ss) == 1:
                board.append(f"{nm} is not on this board, so the order check for {ss[0]} cannot be tested.")
            else:
                board.append(f"{nm} is not on this board, so the order check cannot be tested for "
                             f"{_join_names(ss)}.")
    if grok_segs:
        board.append(f"There is no Grok run for this release, so the order check for {_join_names(grok_segs)} "
                     "cannot be tested.")
    if ceil_segs:
        board.append(f"{cap(_join_names(ceil_segs))}: every measured model is within its margin of the top score, "
                     "so the ceiling mark is not met.")
    if rpl:
        have = _join_names(rpl[0]) or "no model"
        board.append(f"The long-scene check needs at least two groups of models on the board and only {have} "
                     f"{'is' if len(rpl[0]) == 1 else 'are'} there, so the order check for {_join_names(rpl[1])} "
                     "cannot be tested.")
    if sets:
        board.append(f"The Assistant basics were not run on the {_join_names(sets)} model "
                     f"{'set' if len(sets) == 1 else 'sets'}, so its marks are not met"
                     + (f" and {_join_names(sets_segs)} is held." if len(sets_segs) == 1 else
                        f" and {_join_names(sets_segs)} are held." if sets_segs else "."))
    board += _uniq(other)

    # ---- data every model on the board lacks (board level) vs one model's own
    per_model: dict[str, list[tuple[str, str]]] = {l: [] for l in labels}      # label -> [(seg label, short)]
    for l in labels:
        tb = stats[l]["segments45"]
        for sk in _s45.SECTIONS:
            for k, g in ((tb.get(sk) or {}).get("segments") or {}).items():
                for sh in _uniq(_own_pending(g)):
                    per_model[l].append((segs.get(f"{sk}/{k}", g["label"]), sh))
    shared: dict[str, list[str]] = {}
    if len(labels) >= 2:
        common = set(per_model[labels[0]])
        for l in labels[1:]:
            common &= set(per_model[l])
        for seg_l, sh in per_model[labels[0]]:
            if (seg_l, sh) in common:
                shared.setdefault(sh, []).append(seg_l)
    for sh, ss in shared.items():
        if sh == "no fix rows yet":
            board.append(f"No model on this board has fix results yet, so {_join_names(ss)} {_is(ss)} pending.")
        elif sh == "second reader not run":
            board.append(f"No model on this board has had its chat scenes read by a second reader yet, so "
                         f"{_join_names(ss)} {_is(ss)} pending.")
        elif sh == "basics not run":
            board.append(f"The Assistant basics were not run for any model on this board, so "
                         f"{_join_names(ss)} {_is(ss)} pending.")
        else:
            board.append(f"{cap(_join_names(ss))}: {sh} for every model on this board.")
    out["board"] = board

    # ---- marks table
    rows = []
    for mid in marks_in_scope:
        r = marks.get(mid) or {}
        st = r.get("status")
        state = {"pass": "met", "fail": "not met"}.get(st, "cannot be tested yet")
        why = "" if st == "pass" else (marks_reason(r.get("reason") or "", labels, pats) or "no reason recorded")
        rows.append([_mark_name(mid, segs), state, why])
    out["marks"] = rows

    # ---- per model: only what is specific to that model
    for l in labels:
        s = stats[l]
        lines = []
        for seg_l, sh in per_model[l]:
            if sh in shared and seg_l in shared[sh]:
                continue
            lines.append(f"{seg_l}: {_PENDING_LONG.get(sh, sh)}.")
        lines += _model_notes(s, cut_fn, np_in=bool((s.get("nsfw_plus") or {}).get("numbers")))
        if lines:
            out["models"].append([l, lines])
    return out


def _count_of(x) -> int:
    if isinstance(x, (list, tuple, set, dict)):
        return len(x)
    try:
        return int(x or 0)
    except (TypeError, ValueError):
        return 0


_NUM_WORDS = {1: "one", 2: "two", 3: "three"}


def readers_removed_text(rr: dict | None) -> str | None:
    """The C6 stamp in plain words (lead ruling R1: "the row is stamped"): "one reader was removed for this run: it
    refused more than 2% of its reads (5 of 59)", plus what it moved on this model. None when nothing was removed."""
    gone = (rr or {}).get("removed") or []
    if not gone:
        return None
    counts = rr.get("counts") or {}
    each = []
    for r in gone:
        bad, n = (counts.get(r.get("name")) or [None, None])[:2]
        each.append(f"({bad} of {n})" if bad is not None and n else "")
    n_ = len(gone)
    head = (f"{_NUM_WORDS.get(n_, n_)} reader{'s were' if n_ > 1 else ' was'} removed for this run: "
            f"{'they' if n_ > 1 else 'it'} refused more than 2% of {'their' if n_ > 1 else 'its'} reads")
    t = head + (" " + " ".join(x for x in each if x) if any(each) else "")
    mv = rr.get("moved") or {}
    ch, pe = int(mv.get("changed") or 0), int(mv.get("to_pending") or 0)
    if ch or pe:
        t += (f"; on this model {ch} verdict{'s' if ch != 1 else ''} changed and {pe} "
              f"{'wait' if pe != 1 else 'waits'} for readers")
    return t


def _model_notes(s: dict, cut_fn, np_in: bool) -> list[str]:
    bits = []
    rr = readers_removed_text((s.get("nsfw_plus") or {}).get("readers_removed"))
    if rr:
        bits.append(cap(rr) + ".")
    ign = _count_of((s.get("attempts45") or {}).get("ignored"))
    if ign:
        bits.append(f"{ign} later attempt(s) ignored (the first reply of a unit is the scored one).")
    npb = s.get("nsfw_plus") or {}
    multi = _count_of((npb.get("attempts") or {}).get("sessions_multi"))
    if multi:
        bits.append(f"{multi} role-play session(s) have more than one attempt for a turn (the oldest reply of "
                    "each turn is scored).")
    tb = s.get("segments45") or {}
    segs_ = [g for sec in ("code", "chat") if tb.get(sec) for g in tb[sec]["segments"].values()]
    segs_ += [g for n in ((npb.get("numbers") or {}).values()) for g in (n.get("segments") or {}).values()]
    if cut_fn:
        cuts = [f"{g['label']}: {cut_fn(g.get('cut'))}" for g in segs_ if cut_fn(g.get("cut"))]
        if cuts:
            bits.append("Cut replies: " + "; ".join(cuts) + ".")
    if np_in:            # a note about opinion flags only means something when the folder has NSFW+ rows
        bits += [cap(n) + "." for n in (npb.get("notes") or [])]
    return bits


def held_back_md(hb: dict) -> list[str]:
    """The section as Markdown lines; [] when there is nothing to say."""
    if not (hb["board"] or hb["models"] or any(r[1] != "met" for r in hb["marks"])):
        return []
    L = ["### Notes: marks and missing results (they hold no number)", ""]
    if hb["board"]:
        L += ["**On this board**", ""] + [f"- {t}" for t in hb["board"]] + [""]
    if hb["marks"]:
        L += ["**Marks**", "", "| Mark | State | Reason |", "|---|---|---|"]
        L += [f"| {a} | {b} | {c} |" for a, b, c in hb["marks"]] + [""]
    if hb["models"]:
        L += ["**By model**", ""]
        for l, lines in hb["models"]:
            L.append(f"- {l}: " + " ".join(lines))
        L.append("")
    return L[:-1] if L and L[-1] == "" else L


HELD_CSS = ("section.heldback{padding:12px 16px;max-width:960px;font-size:13px;color:var(--muted);line-height:1.5;}"
            "section.heldback summary{cursor:pointer;color:var(--fg);font-size:15px;margin:8px 0;}"
            "section.heldback h3{font-size:13px;color:var(--fg);margin:12px 0 4px;}"
            "section.heldback table{border-collapse:collapse;margin:8px 0;}"
            "section.heldback th,section.heldback td{padding:4px 10px;text-align:left;"
            "border-bottom:1px solid var(--border);}")


def held_back_html(hb: dict) -> str:
    """The same section as a collapsible block; '' when there is nothing to say."""
    if not (hb["board"] or hb["models"] or any(r[1] != "met" for r in hb["marks"])):
        return ""
    e = _esc
    o = ["<style>" + HELD_CSS + "</style>",
         '<section class="heldback" id="held-back"><details><summary>Notes: marks and missing results (they hold no number)</summary>']
    if hb["board"]:
        o.append("<h3>On this board</h3><ul>" + "".join(f"<li>{e(t)}</li>" for t in hb["board"]) + "</ul>")
    if hb["marks"]:
        o.append("<h3>Marks</h3><table><thead><tr><th>Mark</th><th>State</th><th>Reason</th></tr></thead><tbody>"
                 + "".join(f"<tr><td>{e(a)}</td><td>{e(b)}</td><td>{e(c)}</td></tr>" for a, b, c in hb["marks"])
                 + "</tbody></table>")
    if hb["models"]:
        o.append("<h3>By model</h3><ul>" + "".join(
            f"<li><strong>{e(l)}</strong>: {e(' '.join(lines))}</li>" for l, lines in hb["models"]) + "</ul>")
    o.append("</details></section>")
    return "".join(o)
