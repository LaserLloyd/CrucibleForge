"""Element lint (4.5 element standard, ELEMENTS-45.md section 4, rules EL-1..EL-17; EL-17 = NSFW ages, see lint_ages).

``crucibleforge cases verify`` runs :func:`lint_case` over every case that has
``judge_elements``: by default for the hard files and for any profile with a
``budget:`` block (the 4.5 profiles); ``--lint-elements`` forces it on any
profile. A case that declares ``"element_standard": 45``
is held to the standard (findings of severity ``error`` fail the verify); every
other case (the 4.1 files) gets the same findings as warnings only, so today's
suite stays green. EL-15 (the fixture run) needs a judge and is documented in
docs/JUDGE.md (only its structural half runs here: gold.md / foil.md must
exist); EL-16 (sampling comes from ``class`` only; a case in both
``samples3_rows`` and ``samples1_rows``) is checked here.

Element fields read (all optional in the schema, tolerant defaults): ``id``,
``q``, ``class`` (fact|craft), ``evidence`` or ``evidence_type``
(single|multi|negative; NB an older rpdeep element's ``evidence`` is a LIST of
canon excerpts, which is not a type), ``turns``, ``gold_quote``, ``anchor``,
``quote_scope`` (dialogue|narration), ``dim``, ``cap``/``cap_suggested``,
``gate``, ``confirm_no``, ``lexicon_check``, ``compound_ok``, ``short_quote``,
``named_term_count``, ``no_clause``, ``ooc_quote``, ``weight``, ``replaces``,
``carved_from``, ``template``, ``status``.

Deterministic helpers for judge-time fields live here too, as pure functions:
:func:`named_term_ok` (count of a judge-named term in the reply) and
:func:`lexicon_hit` (a lexicon check on a quote).
"""
from __future__ import annotations

import re
from pathlib import Path

CLASSES = ("fact", "craft")
EVIDENCE_TYPES = ("single", "multi", "negative")
QUOTE_SCOPES = ("dialogue", "narration")
ELEMENT_STANDARD_KEY = "element_standard"
ELEMENT_STANDARD = 45

#: element fields that are ONLY lint / authoring data (not asked of the judge)
LINT_ONLY_FIELDS = ("compound_ok", "short_quote", "template", "status", "replaces",
                    "carved_from", "weight_suggested", "cap_suggested", "note")

_NO_RE = re.compile(r"\bNO\b\s*(?:\(empty quote\)\s*)?(?:only\s+)?(?:if|for|unless|:|—|when)")
_NO_WEAK = re.compile(r"\b(?:is|are|=|counts as|means)\s+NO\b")
_PLACEHOLDER = re.compile(r"\{[A-Z][A-Z0-9_]*\}")
_QUOTED = re.compile(r"'[^']*'|\"[^\"]*\"|“[^”]*”")
#: the heat vote counts a dim >= this (report.nsfw_gated_row); a lower cap would
#: also fail the dim and double-count (ELEMENTS-45 section 0, finding 2)
GATED_HEAT_DIM = 8
QUOTE_MIN = 12


def evidence_type(e: dict) -> str | None:
    """The element's evidence type, from ``evidence_type`` or a string
    ``evidence``; None when only canon excerpts (a list) are given."""
    v = e.get("evidence_type")
    if v is None and isinstance(e.get("evidence"), str):
        v = e["evidence"]
    return v


def _norm(s: str) -> str:
    try:
        from .judge import _norm_quote
        return _norm_quote(s or "")
    except Exception:  # noqa: BLE001
        return (s or "").casefold()


def _strip_quoted(s: str) -> str:
    return _QUOTED.sub(" ", s)


def _joins(q: str) -> list[str]:
    from .verify_cases import requirement_joins
    qpart = " ".join(x for x in re.split(r"(?<=[.?!])\s+", q or "") if x.rstrip().endswith("?"))
    return requirement_joins(qpart), qpart


def lint_element(e: dict, case: dict, fixture: str | None = None,
                 n_turns: int | None = None) -> list[tuple[str, str, str]]:
    """[(rule, severity, message)] for one element. Pure."""
    from .judge import GATING_DIMS
    out: list[tuple[str, str, str]] = []

    def add(rule, sev, msg):
        out.append((rule, sev, msg))

    t = e.get("q") or ""
    tmpl = bool(e.get("template"))
    cat = "chat" if (case.get("category") != "nsfw" and "nsfw" not in (case.get("rubric") or "")
                     and (case.get("rubric") or "") != "erp_session") else "nsfw"
    # EL-1 required fields
    if not e.get("id"):
        add("EL-1", "error", "missing id")
    if e.get("class") not in CLASSES:
        add("EL-1", "error", f"class {e.get('class')!r} (fact|craft)")
    et = evidence_type(e)
    if et not in EVIDENCE_TYPES:
        add("EL-1", "error", f"evidence type {et!r} (single|multi|negative; use `evidence_type` "
                             "when `evidence` holds canon excerpts)")
    turns = e.get("turns")
    if not tmpl and (not isinstance(turns, list) or not turns
                     or any(isinstance(x, bool) or not isinstance(x, int) or x < 1 for x in turns)):
        add("EL-1", "error", "turns must be a non-empty list of ints >= 1")
    if not t:
        add("EL-1", "error", "empty q")
    # EL-2 explicit NO clause
    if not _NO_RE.search(t):
        if _NO_WEAK.search(t):
            add("EL-2", "warn", 'weak NO form ("is NO" / "= NO"): write "NO if ..."')
        else:
            add("EL-2", "error", 'no explicit "NO if ..." clause')
    # EL-3 one question
    nq = _strip_quoted(t).count("?")
    if nq >= 3:
        add("EL-3", "error", f"{nq} question marks")
    elif nq == 2 and not e.get("compound_ok"):
        add("EL-3", "warn", "2 question marks")
    # EL-4 single requirement
    joins, qpart = _joins(t)
    gating = e.get("dim") in GATING_DIMS or e.get("gate")
    if joins and not e.get("compound_ok"):
        add("EL-4", "error" if gating else "warn", f"{len(joins) + 1} requirements: {'; '.join(joins)}")
    elif joins and len(str(e.get("compound_ok"))) < 10:
        add("EL-4", "warn", "compound_ok must say why (>= 10 chars)")
    if re.search(r"\bAND\b|\bBOTH\b", qpart) and not e.get("compound_ok"):
        add("EL-4", "warn", "uppercase AND/BOTH marks a compound in the question sentence")
    # EL-5 evidence vs structure
    if et == "multi" and len(turns or []) < 2 and not e.get("named_term_count"):
        add("EL-5", "warn", "multi evidence on a single turn: say which spans to quote")
    if et == "negative" and not e.get("anchor"):
        add("EL-5", "error", "negative-evidence element needs an anchor (the YES span)")
    if et == "single" and len(re.findall(r"\bQuote\b", t)) > 2:
        add("EL-5", "warn", "several quote instructions on a single-evidence element")
    # EL-6 quote instruction
    if not re.search(r"\bquote\b", t, re.I):
        add("EL-6", "error", "no quote instruction")
    # EL-7 gold quote
    if not tmpl:
        g = e.get("gold_quote")
        if cat == "chat" and not g:
            add("EL-7", "error" if e.get("class") == "fact" else "warn", "no gold_quote")
        elif cat == "nsfw" and not g and (e.get("gate") or e.get("dim") == "explicitness") \
                and not e.get("gold_quote_ref"):
            add("EL-7", "warn", "no gold_quote on a gate/explicitness element")
        if g:
            if len(_norm(g).strip(" \"'.,!?")) < QUOTE_MIN and not e.get("short_quote"):
                add("EL-7", "warn", f"gold shorter than {QUOTE_MIN} chars: set short_quote")
            if fixture is not None and e.get("status") != "retire" \
                    and _norm(g).strip(" \"'.,!?") not in _norm(fixture):
                add("EL-7", "error", "gold_quote not found in fixtures/gold.md")
    # EL-8 quote scope vs wording
    qs = e.get("quote_scope")
    if qs is not None and qs not in QUOTE_SCOPES:
        add("EL-8", "error", f"quote_scope {qs!r} (dialogue|narration)")
    if re.search(r"inside (?:the )?quotation marks|SPOKEN words|ALOUD|spoken line|SPOKEN", t) \
            and qs != "dialogue" and not re.search(r"narration sentence", t):
        add("EL-8", "warn", "wording asks for spoken words but quote_scope != dialogue")
    if re.search(r"in NARRATION|narration sentence|NARRATION shows|narration show", t) \
            and qs != "narration":
        add("EL-8", "warn", "wording asks for narration but quote_scope != narration")
    # EL-9 placeholders
    if _PLACEHOLDER.search(t) and not tmpl:
        add("EL-9", "error", "unresolved placeholder in a non-template element")
    # EL-10 explicitness gating
    if e.get("dim") == "explicitness":
        cap = e.get("cap", e.get("cap_suggested"))
        if cap is None or cap < GATED_HEAT_DIM:
            add("EL-10", "error", "explicitness element cap < 8 double-counts in the heat vote")
        if qs != "narration" and not e.get("lexicon_check"):
            add("EL-10", "warn", "explicitness element without quote_scope narration or lexicon_check")
    # EL-11 gate class
    if e.get("gate") and e.get("class") == "craft" and not e.get("confirm_no"):
        add("EL-11", "warn", "gate element classed craft: needs confirm_no or a fact rewrite")
    # EL-13 turn bound
    if n_turns and not tmpl and isinstance(turns, list) and turns \
            and all(isinstance(x, int) for x in turns) and max(turns) > n_turns:
        add("EL-13", "error", f"turn {max(turns)} > the case's {n_turns} user turn(s)")
    # EL-16 sampling comes from class only
    if "samples" in e:
        add("EL-16", "error", "element-level `samples`: sampling comes from `class` only")
    return out


def case_turn_count(case: dict) -> int:
    return len(case.get("turns") or []) or 1


_PREV_FILE = Path(__file__).resolve().parent / "stamps" / "prev" / "element-ids-4fb921c.json"
_PREV: dict = {}


def prev_element_ids(case_id: str) -> set[str] | None:
    """Element ids of the previous (4.1, base 4fb921c) revision of a case, from
    the committed snapshot; None for a case that did not exist then."""
    if not _PREV:
        try:
            import json
            _PREV.update(json.loads(_PREV_FILE.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            _PREV["_"] = []
    v = _PREV.get(case_id)
    return set(v) if v is not None else None


def user_turn_texts(case: dict) -> list[tuple[int, str]]:
    """[(turn number, normalised user text)] of a case: the scripted turns
    (1-based) and, as turn 0, the history a session starts with."""
    out: list[tuple[int, str]] = []
    h = case.get("history")
    if isinstance(h, str):
        out.append((0, _norm(h)))
    elif isinstance(h, list):
        out.append((0, _norm(" ".join(str(x.get("content", x.get("user", ""))) if isinstance(x, dict)
                                      else str(x) for x in h))))
    for i, t in enumerate(case.get("turns") or [], 1):
        txt = t if isinstance(t, str) else (t.get("user") or t.get("content") or "") if isinstance(t, dict) else ""
        out.append((i, _norm(str(txt))))
    if not case.get("turns") and case.get("prompt"):
        out.append((1, _norm(str(case["prompt"]))))
    return out


def fixture_for(case: dict, cases_dir: Path | None = None) -> str | None:
    """fixtures/gold.md of an rpdeep case (cases/rp-deep/<id>/fixtures/gold.md)."""
    if cases_dir is None:
        from . import config as _c
        cases_dir = _c.CASES_DIR
    p = Path(cases_dir) / "rp-deep" / str(case.get("id")) / "fixtures" / "gold.md"
    try:
        return p.read_text(encoding="utf-8") if p.exists() else None
    except OSError:
        return None


def lint_case(case: dict, strict: bool | None = None, cases_dir: Path | None = None,
              fixture: str | None = None, prev_ids: set[str] | None = None,
              scored: bool = False) -> list[dict]:
    """Findings for a case's ``judge_elements``: [{element, rule, severity,
    message}]. ``strict`` (default: the case declares ``element_standard: 45``)
    keeps the rule severities; otherwise every ``error`` is reported as a
    ``warn`` (the 4.1 files) - except a template element in a ``scored`` case,
    which is an error regardless. ``prev_ids`` = the previous revision's
    element ids (default: the committed 4.1 snapshot). Case-wide rules: EL-12
    (duplicate ids, replaces/carved_from targets, migration), EL-14 (child
    weights), EL-15 (structural half: gold.md / foil.md exist)."""
    all_els = [e for e in case.get("judge_elements") or [] if isinstance(e, dict)]
    els = [e for e in all_els if e.get("status") != "retire"]
    if not all_els:
        return []
    if strict is None:
        strict = case.get(ELEMENT_STANDARD_KEY) == ELEMENT_STANDARD
    if prev_ids is None:
        prev_ids = prev_element_ids(str(case.get("id")))
    fx = fixture if fixture is not None else fixture_for(case, cases_dir)
    n = case_turn_count(case)
    found: list[tuple[str, str, str, str]] = []
    hard_errors: set[tuple[str, str]] = set()      # (element, rule) never downgraded
    seen: set[str] = set()
    for e in els:
        eid = e.get("id") or "?"
        if eid in seen:
            found.append((eid, "EL-12", "error", "duplicate element id"))
        seen.add(eid)
        found += [(eid, r, s, m) for r, s, m in lint_element(e, case, fx, n)]
        if scored and e.get("template"):
            found.append((eid, "EL-9", "error", "template element in a scored case"))
            hard_errors.add((eid, "EL-9"))
    # EL-12 targets exist (this file, retire records included, or the previous revision);
    # migration: every previous-revision id is present, retired with a note, or named by a replaces
    ids = {e.get("id") for e in all_els}
    allowed = ids | set(prev_ids or ())
    named: set[str] = set()
    kids: dict[str, list[dict]] = {}
    for e in els:
        for k in ("replaces", "carved_from"):
            v = e.get(k)
            if v:
                named.add(v)
                kids.setdefault(v, []).append(e)
                if v not in allowed:
                    found.append((e.get("id") or "?", "EL-12", "error",
                                  f"{k} target {v!r} is not an element of this case or its previous revision"))
                if v == e.get("id"):
                    found.append((v, "EL-12", "error", f"{k} points at the element itself"))
    for e in all_els:
        if e.get("status") == "retire" and not (isinstance(e.get("note"), str) and e["note"].strip()):
            found.append((e.get("id") or "?", "EL-12", "error", "retired without a note"))
    for pid in sorted(prev_ids or ()):
        if pid not in ids and pid not in named:
            found.append((pid, "EL-12", "error",
                          "previous-revision element dropped without a retire record or a `replaces`"))
    # EL-13 evidence prefix: a `src: turn` evidence text must sit in a user turn at or before the element's last turn
    users = user_turn_texts(case)
    for e in els:
        if e.get("template") or not isinstance(e.get("evidence"), list):
            continue
        ts = [x for x in (e.get("turns") or []) if isinstance(x, int) and not isinstance(x, bool)]
        last = max(ts) if ts else None
        for ev in e["evidence"]:
            if not (isinstance(ev, dict) and ev.get("src") == "turn" and ev.get("text")):
                continue
            pre = _norm(str(ev["text"])).strip()[:35]
            if not pre or last is None:
                continue
            hit = [i for i, t in users if pre in t and i <= last]
            if not hit:
                found.append((e.get("id") or "?", "EL-13", "error",
                              f"evidence text {pre!r} is in no user turn at or before turn {last}"))
        dep = e.get("depends_on_user_turns")
        if isinstance(dep, list) and any(not isinstance(x, int) or x < 1 or x > n for x in dep):
            found.append((e.get("id") or "?", "EL-13", "error", f"depends_on_user_turns outside 1..{n}"))
    # EL-14 child weights conserve the parent's
    for parent, ks in kids.items():
        w_kids = [k.get("weight") for k in ks]
        pw = next((k.get("weight_parent") for k in ks if k.get("weight_parent") is not None), None)
        if pw is not None and all(isinstance(w, (int, float)) for w in w_kids) \
                and abs(sum(w_kids) - pw) > 0.011:
            found.append((parent, "EL-14", "warn",
                          f"child weights {sum(w_kids):.3f} != parent {pw}"))
    # EL-15 (structural half; the judge run is documented in docs/JUDGE.md): a
    # case with a fixtures directory needs its reference files
    d = _fixture_dir(case, cases_dir)
    if strict and d is not None and d.parent.is_dir():
        if any(e.get("gold_quote") and not e.get("template") for e in els) and not (d / "gold.md").exists():
            found.append(("-", "EL-15", "error", "fixtures/gold.md is missing"))
        if any(e.get("foil_fails") for e in els) and not (d / "foil.md").exists():
            found.append(("-", "EL-15", "error", "elements name `foil_fails` but fixtures/foil.md is missing"))
    out = []
    for eid, rule, sev, msg in found:
        if sev == "error" and not strict and (eid, rule) not in hard_errors:
            sev = "warn"
        out.append({"element": eid, "rule": rule, "severity": sev, "message": msg})
    return out


def _fixture_dir(case: dict, cases_dir: Path | None) -> Path | None:
    if cases_dir is None:
        from . import config as _c
        cases_dir = _c.CASES_DIR
    return Path(cases_dir) / "rp-deep" / str(case.get("id")) / "fixtures"


def lint_profile(prof: dict) -> list[str]:
    """Profile-level lint: EL-16 row lists (a case in both ``samples3_rows`` and
    ``samples1_rows`` draws two sample counts)."""
    pol = prof.get("judge_policy") or {}
    both = sorted(set(pol.get("samples3_rows") or []) & set(pol.get("samples1_rows") or []))
    return [f"EL-16: {c} is in both judge_policy.samples3_rows and samples1_rows" for c in both]


def standard_problems(prof: dict, cases: list[dict], pending: set[str] | None = None) -> list[str]:
    """Cases the 4.5 element standard requires ``element_standard: 45`` of:
    every case a profile with a ``budget:`` block scores, and every hard case.
    ``pending`` = case ids still awaiting their 4.5 rewrite (the budget targets'
    ``pending_from_hash`` equals the current hash): they keep their 4.1 content
    and are exempt until the content moves."""
    pending = pending or set()
    by_id = {c["id"]: c for c in cases if isinstance(c, dict) and c.get("id")}
    want: set[str] = set()
    if isinstance(prof.get("budget"), dict):
        from . import hard as _hard
        scored_cats = {k for ks in _hard.SCORE_CATEGORIES.values() for k in ks}
        want |= {i for cat, ids in (prof.get("cases") or {}).items() if cat in scored_cats
                 for i in ids or []}
    want |= {i for i, c in by_id.items() if c.get("hard_score")}
    return [f"{i}: element_standard: 45 is required on a scored 4.5 case"
            for i in sorted(want) if i in by_id and i not in pending
            and by_id[i].get(ELEMENT_STANDARD_KEY) != ELEMENT_STANDARD]


def pending_ids(prof: dict) -> set[str]:
    """Ids whose budget target is still pending the 4.5 rewrite (content hash
    unchanged since the target was set)."""
    try:
        from . import budget, version
        tp, _rp = budget.paths(prof)
        t = budget.load_targets(tp)["cases"]
        return {i for i, e in t.items()
                if e.get("pending_from_hash") and e["pending_from_hash"] == version.case_hash(i)}
    except Exception:  # noqa: BLE001 - no targets: nothing is exempt
        return set()


# ------------------------------------------------------------------ EL-17

NSFW_RUBRICS = ("nsfw", "nsfw_craft", "erp_session", "rp_dark_session")
ADULT_MIN = 18
_NAME = r"((?:[A-Z][\w'’.-]*|[A-Z]{2,})(?:[ -](?:[A-Z][\w'’.-]*|[A-Z]{2,})){0,3})"
#: NAME (32)  NAME (she/her, 41)  NAME, 41,  NAME is 41  NAME, a 41-year-old
_AGE_AFTER = re.compile(_NAME + r"(?:\s*\((?:[^()]*?,\s*)?(\d{1,3})\)|\s*\([^()]*\)\s*,\s*(\d{1,3})\b|,\s*(\d{1,3})\b(?!\s*(?:words|min))"
                        r"|\s+is\s+(\d{1,3})\b|,?\s+(?:a|an|the)\s+(\d{1,3})[- ]year[- ]old)")
_AGE_BEFORE = re.compile(r"\b(\d{1,3})[- ]year[- ]old\s+(?:[a-z]+\s+){0,2}" + _NAME)
_AGE_ANY = re.compile(r"\b(\d{1,3})[- ]year[- ]old|\bage[sd]?\s+(\d{1,3})\b|\(\s*(?:[^()]*?,\s*)?(\d{2})\s*\)")
_NAME_PRON = re.compile(_NAME + r"\s*\((?:she|he|they)/(?:her|him|them)[^)]*\)")
_AMBIGUOUS = re.compile(r"\b(?:minor|underage|under-age|teen(?:ager|age)?|schoolgirl|schoolboy|"
                        r"high[- ]school(?:er|\s+(?:student|girl|boy|senior|junior|sophomore|freshman))|child|kid|little girl|little boy|young girl|"
                        r"young boy|barely legal|lolita|loli|shota|jailbait|"
                        r"student|undergrad(?:uate)?|freshman|sophomore|dorm|academy|boarding school|"
                        r"young[- ]looking|youthful[- ]looking|baby[- ]?faced|petite|girlish|boyish|"
                        r"coed|co-ed|prep school|school uniform)\b", re.I)

#: words that are capitalised in a card without being a character
_NAME_STOP = frozenset("""
a about after again all also an and any are as at be because before both but by can do does each
every few for from had has have he her here hers him his how i if in into is it its just keep last
make may me more most must my no not now of on once one only or other our out over she so some
such than that the their them then there these they this those to too under until up use was we
were what when where which while who whom why will with without would you your yours
write begin end start stop let get give take put say tell ask answer reply replies scene scenes
first second third next also still even both either neither always never sometimes often
monday tuesday wednesday thursday friday saturday sunday january february march april may june
july august september october november december
mr mrs ms dr sir lady lord miss mx
heading headings before during after pov poi
ooc nsfw erp rp sfw
victorian slow english japanese french german spanish chinese american british tokyo london paris york
""".split())
_NOT_NAME_BEFORE = frozenset("the called owns at in of".split())
_PLACE_AFTER = re.compile(r"\s+(?:&|and\s+[A-Z]|(?:House|Room|Hall|Studio|Street|Lane|Road|Bar|Cafe|Club|Hotel|Inn|"
                          r"Station|Park|Garden|Gardens|Library|Museum|Theatre|Theater|Tower|Bridge|Island)\b)")
#: a capitalised word in the MIDDLE of a sentence (a sentence-start capital proves nothing);
#: ALL-CAPS names are covered by the pronoun / age patterns above
_NAME_TOKEN = re.compile(r"(?<=[a-z0-9,;)\]\"”’'] )([A-Z][a-z]{2,})(?:['’]s)?(?![\w-])")


def is_nsfw_case(case: dict) -> bool:
    return (case.get("category") == "nsfw" or case.get("rubric") in NSFW_RUBRICS
            or "nsfw" in str(case.get("rubric") or ""))


def _case_text(case: dict) -> str:
    parts = [str(case.get("system") or ""), str(case.get("prompt") or "")]
    t = case.get("turns")
    parts += [str(x) for x in (t if isinstance(t, list) else [t] if t else [])]
    parts += [str(m.get("content") or "") for m in case.get("history") or [] if isinstance(m, dict)]
    return "\n".join(parts)


_CUE_AFTER = re.compile(r"(?:['’]s\b|\s+(?:said|says|asks?|asked|replies|replied|whispers?|laughs?|"
                        r"smiles?|nods?|grins?|his|her|their|she|he|they)\b)", re.I)


def _person_cue(text: str, start: int, end: int, tok: str) -> bool:
    """A capitalised word is only a possible CHARACTER when a person cue sits
    next to it: a speaker tag "Name:", said/asks/her/his/their/'s after it,
    or said/asks/tells before it."""
    if re.match(r":", text[end:end + 1]) and re.search(r"(?:^|\n)[\s>*_-]*$", text[:start]):
        return True
    if _CUE_AFTER.match(text, end):
        return True
    return bool(re.search(r"\b(?:said|says|asks?|asked|tells?|told)\s+$", text[max(0, start - 12):start], re.I))


def _unaged_names(text: str, aged: dict[str, int]) -> list[str]:
    """Capitalised names in the text (minus a stop list; a word that also
    appears in lower case is a common word) that no stated age is attached
    to: "Mara, 34 ... Juno ... stays the night" -> Juno."""
    lower_words = {w.casefold() for w in re.findall(r"\b[a-z][a-z']+\b", text)}
    aged_tokens = {t for k in aged for t in re.split(r"[\s-]+", k)}
    names: dict[str, str] = {}
    for m in _NAME_TOKEN.finditer(text):
        tok = m.group(1)
        key = tok.casefold()
        prev = re.search(r"([A-Za-z']+) $", text[:m.start()])
        if prev and prev.group(1).casefold() in _NOT_NAME_BEFORE:
            continue                      # "the Harrow", "called Needle", "owns Tideglass"
        if _PLACE_AFTER.match(text, m.end()):
            continue                      # "Fern Room", "Needle & Gull"
        if key in _NAME_STOP or key in lower_words or len(key) < 3:
            continue
        names.setdefault(key, tok)
    out = []
    for key, tok in names.items():
        if key in aged_tokens:
            continue
        if not _person_cue(text, m.start(), m.end(), tok):
            continue                      # "Lisbon", "Latin", "Fire": no person cue
        near = re.search(r"\b" + re.escape(tok) + r"\b[^\n.;]{0,40}?\b(?:\(|,\s*|is\s+|aged?\s+)?(\d{2})\b", text, re.I)
        if near and int(near.group(1)) >= ADULT_MIN:
            continue
        before = re.search(r"\b(\d{2})[- ]year[- ]old\s+(?:[\w-]+\s+){0,2}" + re.escape(tok) + r"\b", text, re.I)
        if before:
            continue
        out.append(f"{tok} is named but has no stated age")
    return out


def lint_ages(case: dict) -> list[str]:
    """EL-17 ERROR messages only (see :func:`lint_ages_split`)."""
    return lint_ages_split(case)[0]


def _declared_age_stated(text: str, name: str, age: int) -> bool:
    """The declared age appears within ~60 chars of the name: "Mara, 34",
    "Mara (34)", "34-year-old Mara"."""
    n = re.escape(name)
    a = rf"(?<!\d){age}(?!\d)"
    return bool(re.search(rf"\b{n}\b[^\n]{{0,60}}?{a}", text, re.I)
                or re.search(rf"{a}[- ]year[- ]old\s+(?:[\w-]+\s+){{0,2}}\b{n}\b", text, re.I))


#: 4.5 (cases declaring ``element_standard: 45``): the wording list is a prompt to READ the case,
#: never a failure or a drop (the owner 2026-11-14: the only content limits are docs/CONTENT-RULES.md as
#: written; rule 1 "No sexual or sexualised content involving minors" is judged on what is depicted,
#: not on words; no word lists). A hit is a warning naming the case and the term until a reviewer
#: records a reading here. 4.1 cases (no key) keep the error, byte-identical.
#:
#: Shape (JSON): {"reviews": [{"case": "<case id>", "term": "<the word as the lint prints it>",
#:                "rule": 1, "reading": "<why the depiction is adult>", "by": "<reviewer>",
#:                "date": "YYYY-MM-DD"}]}
#: One entry waives exactly one (case, term) pair (term compared case-insensitively); every field
#: except ``by``/``date`` is required, and a malformed file is an error, never a silent waiver.
CONTENT_REVIEWS_FILE = Path(__file__).resolve().parents[1] / "docs" / "content-reviews.json"
_REVIEWS_CACHE: dict[tuple, dict] = {}


def load_content_reviews(path: Path | None = None) -> dict[tuple[str, str], dict]:
    """{(case id, term casefolded): entry} from :data:`CONTENT_REVIEWS_FILE` (absent file = none)."""
    import json
    p = Path(path) if path else CONTENT_REVIEWS_FILE
    try:
        st = p.stat()
    except FileNotFoundError:
        return {}
    key = (str(p), st.st_mtime_ns, st.st_size)
    if key in _REVIEWS_CACHE:
        return _REVIEWS_CACHE[key]
    data = json.loads(p.read_text(encoding="utf-8"))
    out: dict[tuple[str, str], dict] = {}
    for i, e in enumerate((data or {}).get("reviews") or []):
        missing = [k for k in ("case", "term", "reading") if not str((e or {}).get(k) or "").strip()]
        if missing or not isinstance(e.get("rule"), int) or isinstance(e.get("rule"), bool):
            raise ValueError(f"{p.name}: review #{i} needs case, term, rule (int) and reading "
                             f"(missing {missing or ['rule']})")
        out[(str(e["case"]), str(e["term"]).casefold())] = e
    _REVIEWS_CACHE.clear()
    _REVIEWS_CACHE[key] = out
    return out


def _wording_reads(case: dict, text: str, reviews: dict | None) -> list[str]:
    """4.5 EL-17 wording hits as warnings ("read this case against rule 1"), minus reviewed ones."""
    rv = load_content_reviews() if reviews is None else reviews
    cid = str(case.get("id"))
    out = []
    for m in _AMBIGUOUS.finditer(text):
        term = m.group(0)
        if (cid, term.casefold()) in rv:
            continue
        out.append(f"read {cid} against docs/CONTENT-RULES.md rule 1 (wording {term!r}): judge what is "
                   f"depicted; if the characters are adults written as adults, record the reading in "
                   f"docs/content-reviews.json")
    return out


def lint_ages_split(case: dict, reviews: dict | None = None) -> tuple[list[str], list[str]]:
    """EL-17 (4.5 NSFW track) -> (errors, warnings). ERRORS: a stated age under
    18; ambiguous youth wording (teen, schoolgirl, student, dorm, ...; 4.1 cases only:
    under element_standard 45 it is a WARNING to read the case, see :func:`_wording_reads`); no age
    stated at all; a name given pronouns but no age; a DECLARED ``characters``
    entry whose age is missing / under 18 / not stated within ~60 chars of its
    name in the card, prompt or turns; an element_standard-45 NSFW case with no
    ``characters`` declaration. WARNINGS: a capitalised name with a person cue
    (said/her/'s, "Name:") and no stated age (a heuristic, never an error); an
    older NSFW case that declares no ``characters``."""
    if not is_nsfw_case(case):
        return [], []
    text = _case_text(case)
    out: list[str] = []
    warns: list[str] = []
    aged: dict[str, int] = {}
    for m in _AGE_AFTER.finditer(text):
        a = next(g for g in m.groups()[1:] if g)
        aged[m.group(1).strip().casefold()] = int(a)
    for m in _AGE_BEFORE.finditer(text):
        aged[m.group(2).strip().casefold()] = int(m.group(1))
    ages = [int(next(g for g in m.groups() if g)) for m in _AGE_ANY.finditer(text)]
    ages += list(aged.values())
    if not ages:
        out.append("no character age is stated in the card/prompt text")
    for a in sorted(set(ages)):
        if a < ADULT_MIN:
            out.append(f"a stated age of {a} is under {ADULT_MIN}")
    if case.get(ELEMENT_STANDARD_KEY) == ELEMENT_STANDARD:
        warns += _wording_reads(case, text, reviews)
    else:
        for m in _AMBIGUOUS.finditer(text):
            out.append(f"ambiguous age wording {m.group(0)!r}: state an adult age instead")
    warns += _unaged_names(text, aged)
    for m in _NAME_PRON.finditer(text):
        n = m.group(1).strip().casefold()
        if n not in aged:
            out.append(f"{m.group(1).strip()} has pronouns but no stated age")
    chars = case.get("characters")
    if chars:
        for c in chars:
            name = str(c.get("name") if isinstance(c, dict) else c).strip()
            a = c.get("age") if isinstance(c, dict) else None
            if not isinstance(a, int) or isinstance(a, bool):
                out.append(f"declared character {name!r} has no age")
            elif a < ADULT_MIN:
                out.append(f"declared character {name!r} age {a} is under {ADULT_MIN}")
            elif not _declared_age_stated(text, name, a):
                out.append(f"declared character {name!r} age {a} is not stated within 60 characters "
                           "of the name in the card/prompt text")
    elif case.get(ELEMENT_STANDARD_KEY) == ELEMENT_STANDARD:
        out.append("an element_standard-45 NSFW case must declare `characters` "
                   "[{name, age}] (every character, age >= 18)")
    else:
        warns.append("no `characters` declared (name + adult age per character)")
    return list(dict.fromkeys(out)), list(dict.fromkeys(warns))


def lint_cases(cases: list[dict], cases_dir: Path | None = None,
               scored_ids: set[str] | None = None) -> tuple[int, int, list[str]]:
    """(errors, warnings, printable lines) over many cases. ``scored_ids`` =
    cases a profile scores (hard cases always count): a template element in
    one is an error whatever the case's standard."""
    errs = warns = 0
    lines: list[str] = []
    for c in cases:
        _e, _w = lint_ages_split(c)
        for msg in _e:
            errs += 1
            lines.append(f"FAIL {c.get('id')}/- EL-17: {msg}")
        for msg in _w:
            warns += 1
            lines.append(f"warn {c.get('id')}/- EL-17: {msg}")
        if not c.get("judge_elements"):
            continue
        strict = c.get(ELEMENT_STANDARD_KEY) == ELEMENT_STANDARD
        scored = bool(c.get("hard_score")) or c.get("id") in (scored_ids or ())
        for f in lint_case(c, cases_dir=cases_dir, scored=scored):
            if f["severity"] == "error":
                errs += 1
            else:
                warns += 1
            tag = "FAIL" if f["severity"] == "error" else ("warn" if strict else "warn(4.1)")
            lines.append(f"{tag} {c['id']}/{f['element']} {f['rule']}: {f['message']}")
    return errs, warns, lines


# ------------------------------------------------------------ deterministic

def named_term_ok(reply: str, term: str, n: int, ignore_case: bool = True) -> tuple[bool, int]:
    """``named_term_count``: the judge names the term (a physical object or
    feature, field ``term``); the engine counts whole-word occurrences in the
    graded reply and the element passes only when the count equals ``n``. A
    term under 3 letters or empty never passes (nothing to count)."""
    term = (term or "").strip()
    if len(term) < 3:
        return False, 0
    flags = re.I if ignore_case else 0
    cnt = len(re.findall(r"(?<!\w)" + re.escape(term) + r"(?!\w)", reply or "", flags))
    return cnt == int(n), cnt


def lexicon_hit(quote: str, lexicon: list[str] | None) -> bool | None:
    """``lexicon_check``: does the verified quote contain a word/phrase of the
    profile's lexicon (``judge_policy.lexicon`` / ``lexicon_terms``)? None when
    no lexicon is configured (the check is then not applicable, never a pass or
    a fail). The lexicon is data the board's owner supplies; none ships here."""
    terms = [t for t in (lexicon or []) if t]
    if not terms:
        return None
    q = _norm(quote or "")
    return any(re.search(r"(?<!\w)" + re.escape(_norm(t)) + r"(?!\w)", q) for t in terms)
