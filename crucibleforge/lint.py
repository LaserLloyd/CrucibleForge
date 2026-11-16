"""Pack lint (harness v2: the content-rules lint of `pack verify`, and the v2
case lint, spec v2 WP0-L / WP0-V).

Content rules (docs/CONTENT-RULES.md) — every pack question written inside a
set file, every pack (existing questions referenced by id stand):

  rule 1  minor-coded words in a SEXUAL case
  rule 2  sex with a non-sapient animal (sexual cases)
  rule 3  an ask to build a weapon, explosive or CBRN agent (any framing)
  rule 4  working harm: intrusion into a real system, a self-harm method,
          a lethal amount
  rule 5  a named living public figure (cases/content-rules-figures.txt)

Each hit is reported as ``rule N``. A reviewer may let one hit stand with a
stated reason: ``pack verify --allow 'CASE:ruleN:<matched text>=<reason>'``,
or an attestation on the case (``content_rules_allow: [{"rule": N, "match":
"<matched text>", "reason": "..."}]``; the set may carry the same list with
a ``case`` per entry). The patterns are deliberately narrow (a negated
mention, an eponym, a fictional character or a fight in a story does not
fire); the reviewer's read stays the real check.

The v2 case lint (a set with ``"v2": true``) adds the structure the v2 packs
are graded on (spec v2 §5.0-§5.6): a cast with stated adult ages and
sapience, elements with ref / fact / window, no keyword-presence checks,
flag-only forbids, knowledge without doses or covert administration, the
pack's rows in samples3_rows, the 64k budget, and each judge group within
21,000 characters with a full-length synthetic answer (``allow_long`` with
a reason accepts up to the judge's hard budget).
"""
from __future__ import annotations

import os
import re
from pathlib import Path

FIGURES_FILE = Path(__file__).resolve().parent.parent / "cases" / "content-rules-figures.txt"
FIGURES_ENV = "CF_CONTENT_RULES_FIGURES"

_NEG = re.compile(r"\b(?:no|not|never|nothing|non|without|nor|neither|zero|isn't|aren't|"
                  r"wasn't|weren't|n't|forbid\w*|banned|excluded?|avoid\w*)\b[^.!?\n]{0,30}$", re.I)

_RULE1 = re.compile(
    r"\b(?:child|children|childlike|kids?(?!\s+gloves?|\s+leather)|minors|underage|under-age|"
    r"teens?|teenage(?:rs?)?|pre-?teens?|pre-?pubescent|pubescent|schoolgirls?|schoolboys?|"
    r"school\s+uniforms?|"
    # an adult's job or past ("a high-school music teacher", "her high school
    # sweetheart") is not a school setting
    r"(?:high|middle|elementary|grade)[- ]school(?:ers?)?(?!\s+(?:\w+\s+)?(?:teachers?|principals?|"
    r"coach(?:es)?|counsel\w*|reunions?|sweethearts?|diplomas?|dropouts?|equivalency|days|years)\b)|"
    r"junior\s+high|lol(?:i|ita)s?|shotas?|little\s+(?:girl|boy)s?|"
    r"young\s+(?:girl|boy)s?|jailbait|barely\s+(?:legal|eighteen|18)|just\s+turned\s+(?:18|eighteen)|"
    r"(?:1[0-7]|[1-9])[- ]?(?:years?|yrs?)[- ]?old|aged?\s+(?:1[0-7]|[1-9])\b|"
    r"(?:ten|eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen)[- ]?(?:years?|yrs?)[- ]?old|"
    # "16yo", "15 y/o", "16 y.o.", "sixteen y/o"
    r"(?:1[0-7]|[1-9]|ten|eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen)"
    r"\s?y(?:/|\.)?\s?o\b|"
    r"(?:age\s+of|aged|turned|turning|(?:she|he|they|girl|boy)\s+(?:is|was|are|were|turns))\s+(?:ten|eleven|twelve|thirteen|fourteen|fifteen|sixteen|"
    r"seventeen)|\bat\s+(?:ten|eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen)\b|"
    # school grades and years: "eighth grader", "10th grade", "a freshman"
    r"(?:\d{1,2}(?:st|nd|rd|th)|first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth|"
    r"eleventh|twelfth)[- ]grade(?:rs?)?|freshm[ae]n|sophomores?)\b"
    r"|\ba\s+minor\b(?!\s+(?:detail|chord|key|thing|issue|point|change|injur\w*|cut|role|"
    r"inconvenience|scratch|wound|burn|character|god|noble|planet|league|offen[cs]e))", re.I)

_ANIMALS = r"(?:dog|dogs|horse|horses|stallion|mare|goat|sheep|pig|donkey|pony|hound|bull|cow)"
_RULE2 = re.compile(
    r"\b(?:bestiality|zoophil\w*|animal\s+sex)\b"
    rf"|\b(?:fuck\w*|mount\w*|bred|breed\w*|sex\s+with|knott?\w*|rut\w*\s+with)\b[^.!?\n]{{0,30}}"
    rf"\b(?:a|the|his|her|their|its)\s+{_ANIMALS}\b"
    rf"|\b{_ANIMALS}(?:'s)?\s+(?:cock|dick|penis|knot)\b", re.I)

_TARGETS = (r"(?:bombs?|pipe\s+bombs?|explosives?|IEDs?|detonators?|napalm|thermite|TATP|RDX|semtex|"
            r"C-?4|nerve\s+agents?|sarin|VX|tabun|novichok|mustard\s+gas|ricin|anthrax|botulinum|"
            r"smallpox|bio-?weapons?|chemical\s+weapons?|dirty\s+bombs?|weapons?-grade|"
            r"ghost\s+guns?|silencers?|suppressors?|firearms?|guns?|rifles?|grenades?|molotov\w*)")
_RULE3 = re.compile(
    r"\b(?:how\s+(?:to|do\s+(?:I|you|we|they))|steps?\s+(?:to|for)|instructions?\s+(?:to|for)|"
    r"recipe\s+for|guide\s+to|teach\s+(?:me|us|them|him|her)\s+(?:to|how)|explain\s+how|"
    r"walk\s+(?:me|us|them)\s+through|step[- ]by[- ]step|exact\s+(?:quantities|amounts|steps)|"
    r"precise\s+(?:quantities|amounts|steps))\b[^.!?]{0,80}?"
    r"\b(?:make|making|build|building|synthesi[sz]e|synthesi[sz]ing|assemble|assembling|"
    r"manufactur\w+|cook|cooking|brew|brewing|weaponi[sz]\w+|culture|enrich\w*|3d[- ]print\w*|"
    rf"print(?:ing)?|produce|producing|create|creating)\b[^.!?]{{0,40}}?\b{_TARGETS}\b"
    rf"|\brecipe\s+for\s+(?:an?\s+|the\s+)?{_TARGETS}\b", re.I)

_DOMAIN = (r"\b(?!example\.)(?:[a-z0-9-]+\.)+(?:com|org|net|gov|edu|io|co|uk|jp|de|fr|ru|cn|mil|"
           r"bank|info|biz)\b(?!\.)")
_SAFE_HOST = re.compile(r"(?:^|\.)(?:example\.(?:com|org|net)|[a-z0-9-]+\.(?:test|invalid|example|"
                        r"local|localhost))$", re.I)
_INTRUDE = (r"(?:hack\w*|break\s+into|breaking\s+into|exploit\w*|ddos\w*|phish\w*|brute[- ]?forc\w*|"
            r"crack\w*|bypass\w*|sql[- ]?inject\w*|gain\w*\s+(?:unauthori[sz]ed\s+)?access\s+to|"
            r"take\s+down|deface\w*|steal\w*\s+(?:the\s+)?(?:passwords?|credentials))")
_RULE4 = re.compile(
    rf"\b{_INTRUDE}\b[^.!?\n]{{0,60}}?(?P<dom>{_DOMAIN})"
    rf"|\b{_INTRUDE}\b[^.!?\n]{{0,40}}\b(?:a|the|this|that)\s+(?:real|live|real-world|production|"
    r"actual)\s+(?:bank|website|site|server|system|network|account|company|hospital|grid)\b"
    r"|\bworking\s+exploit\b"
    r"|\b(?:method|way|ways|steps?|how)\s+(?:to|for)\s+(?:kill\s+(?:myself|yourself|oneself|"
    r"themselves)|commit\s+suicide|end\s+(?:my|your|their)\s+(?:own\s+)?life|self[- ]harm)\b"
    r"|\blethal\s+(?:dose|amount|quantity)\b|\bhow\s+(?:much|many)\b[^.!?\n]{0,40}\bto\s+(?:kill|overdose)\b",
    re.I)

#: case fields a lint reads (what a model, or the judge, is shown)
_TEXT_FIELDS = ("prompt", "system", "judge_key", "judge_card_summary", "reference", "note")


def case_texts(case: dict) -> list[tuple[str, str]]:
    """(where, text) of every model- or judge-facing text in a case."""
    out = [(k, case[k]) for k in _TEXT_FIELDS if isinstance(case.get(k), str)]
    for i, t in enumerate(case.get("turns") or [], start=1):
        out.append((f"turn {i}", t if isinstance(t, str) else str(t.get("content") or t)))
    for i, m in enumerate(case.get("history") or case.get("messages") or [], start=1):
        if isinstance(m, dict):
            out.append((f"history {i}", str(m.get("content") or "")))
    for e in case.get("judge_elements") or []:
        out.append((f"element {e.get('id')}", " ".join(str(e.get(k) or "") for k in ("q", "ref"))))
        # ``evidence`` is the legacy list of canon excerpts OR the new policy
        # string ("yes"|"no"|"either"|"none"): only a list holds texts
        evs = e.get("evidence")
        for ev in (evs if isinstance(evs, list) else []):
            if isinstance(ev, dict):
                out.append((f"element {e.get('id')} evidence", str(ev.get("text") or "")))
    for f in case.get("facts") or []:
        if isinstance(f, dict):
            out.append((f"fact {f.get('id')}", str(f.get("text") or "")))
    return out


def is_sexual(case: dict, part: str | None = None) -> bool:
    """Rules 1 and 2 apply to sexual cases: the NSFW parts other than
    Uncensored, any explicit_required case, or ``"sexual": true``."""
    if case.get("sexual") is not None:
        return bool(case["sexual"])
    if case.get("explicit_required") or case.get("rubric") in ("nsfw", "nsfw_craft", "erp_session",
                                                                "erp_deep"):
        return True
    return case.get("category") == "nsfw" and (part or "") != "uncensored"


def load_figures(path: str | Path | None = None) -> list[str]:
    p = Path(path or os.environ.get(FIGURES_ENV) or FIGURES_FILE)
    try:
        lines = p.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    return [x.strip() for x in lines if x.strip() and not x.lstrip().startswith("#")]


# Adult family relations (FR-A18: a parent and an adult child, siblings):
# "child"/"children"/"kid(s)" right after a relation word, or "child of". Not
# a hit ONLY in a case whose every cast member carries an adult age statement
# (adult_cast); everywhere else rule 1 fires as before.
_FAMILY_WORDS = {"child", "children", "kid", "kids"}
_FAMILY_BEFORE = re.compile(
    r"\b(?:adult|grown(?:[- ]up)?|her|his|their|my|your|our|its|only|eldest|oldest|youngest|"
    r"middle|first|second|third|step|foster|adopted|estranged|grown)\s+(?:\w+\s+)?$", re.I)
_FAMILY_AFTER = re.compile(r"^\s+of\b", re.I)


def adult_cast(case: dict) -> bool:
    """Every cast member carries an adult age statement: a human ``age`` >= 21
    that the card (system/prompt) states, a non-human ``adult`` and
    ``sapient``. No cast list = False."""
    cast = case.get("cast")
    if isinstance(cast, dict):
        # the adult45 session format nests the cast ({model: [...], user: {...}, others: [...]})
        cast = [c for k in ("model", "user", "others")
                for c in (cast.get(k) if isinstance(cast.get(k), list) else [cast.get(k)]) if c]
    if not isinstance(cast, list) or not cast:
        return False
    card = "\n".join(str(case.get(k) or "") for k in ("system", "prompt"))
    for ch in cast:
        if not isinstance(ch, dict):
            return False
        if str(ch.get("species") or "human").lower() == "human":
            age = ch.get("age")
            if not isinstance(age, int) or age < 21 or not re.search(rf"\b{age}\b", card):
                return False
        elif ch.get("adult") is not True or ch.get("sapient") is not True:
            return False
    return True


def _family_relation(text: str, m: re.Match) -> bool:
    if m.group(0).strip().lower() not in _FAMILY_WORDS:
        return False
    return bool(_FAMILY_BEFORE.search(text[max(0, m.start() - 40):m.start()])
                or _FAMILY_AFTER.match(text[m.end():m.end() + 6]))


def _negated(text: str, start: int) -> bool:
    return bool(_NEG.search(text[max(0, start - 45):start]))


def content_hits(case: dict, part: str | None = None,
                 figures: list[str] | None = None) -> list[dict]:
    """Every rule hit of one case: {"case", "rule", "match", "where"} (one per
    distinct (rule, match) — the first place it occurs)."""
    sexual = is_sexual(case, part)
    figs = load_figures() if figures is None else figures
    fig_re = (re.compile(r"(?<!\w)(?:" + "|".join(re.escape(f) for f in figs) + r")(?!\w)")
              if figs else None)
    hits: dict[tuple, dict] = {}
    adults = adult_cast(case)

    def add(rule, m, where, text, neg_guard=True):
        if neg_guard and _negated(text, m.start()):
            return
        key = (rule, m.group(0).strip().lower())
        hits.setdefault(key, {"case": case.get("id"), "rule": rule,
                              "match": " ".join(m.group(0).split()), "where": where})

    for where, text in case_texts(case):
        if sexual:
            for m in _RULE1.finditer(text):
                if adults and _family_relation(text, m):
                    continue        # "her adult child", "the child of" in an all-adult cast
                # no negation guard: a minor in a sexual case is a hard error
                # whatever surrounds it ("no idea she is 15"); a reviewer
                # allowlist entry is the only way to let a hit stand
                add(1, m, where, text, neg_guard=False)
            for m in _RULE2.finditer(text):
                add(2, m, where, text)
        for m in _RULE3.finditer(text):
            add(3, m, where, text)
        for m in _RULE4.finditer(text):
            if m.groupdict().get("dom") and _SAFE_HOST.search(m.group("dom")):
                continue
            add(4, m, where, text)
        if fig_re:
            for m in fig_re.finditer(text):
                add(5, m, where, text, neg_guard=False)
    return sorted(hits.values(), key=lambda h: (h["rule"], h["where"]))


def allow_key(case_id: str, rule: int, match: str) -> str:
    return f"{case_id}:rule{rule}:{' '.join(match.split()).lower()}"


def parse_allows(items: list[str] | None) -> tuple[dict[str, str], list[str]]:
    """``CASE:ruleN:<match>=<reason>`` -> ({key: reason}, problems)."""
    out, bad = {}, []
    for raw in items or []:
        key, sep, reason = str(raw).partition("=")
        m = re.fullmatch(r"\s*([^:]+):rule([1-5]):(.+?)\s*", key)
        if not sep or not m or len(reason.strip()) < 8:
            bad.append(f"--allow {raw!r}: write CASE:ruleN:<matched text>=<reason of 8+ chars>")
            continue
        out[allow_key(m.group(1).strip(), int(m.group(2)), m.group(3))] = reason.strip()
    return out, bad


def attested(spec: dict, case: dict) -> tuple[dict[str, str], list[str]]:
    """The content_rules_allow attestations of a case and its set."""
    out, bad = {}, []
    entries = [(case.get("id"), a) for a in case.get("content_rules_allow") or []]
    entries += [(a.get("case"), a) for a in spec.get("content_rules_allow") or []
                if isinstance(a, dict) and a.get("case") == case.get("id")]
    for cid, a in entries:
        if not isinstance(a, dict) or not a.get("match") or len(str(a.get("reason") or "").strip()) < 8:
            bad.append(f"{cid}: content_rules_allow needs rule, match and a reason (8+ chars)")
            continue
        try:
            out[allow_key(str(cid), int(a.get("rule")), str(a["match"]))] = str(a["reason"]).strip()
        except (TypeError, ValueError):
            bad.append(f"{cid}: content_rules_allow rule must be 1-5")
    return out, bad


def lint_pack_content(spec: dict, part: str | None,
                      allows: dict[str, str] | None = None) -> tuple[list[str], list[str]]:
    """(problems, notes) of the content rules over a pack's inline questions."""
    problems, notes = [], []
    figures = load_figures()
    for c in spec.get("cases") or []:
        if not isinstance(c, dict):
            continue
        ok, bad = attested(spec, c)
        problems += bad
        ok.update(allows or {})
        for h in content_hits(c, part, figures):
            k = allow_key(h["case"], h["rule"], h["match"])
            if k in ok:
                notes.append(f"rule {h['rule']} allowed: {h['case']}: \"{h['match']}\" "
                             f"({h['where']}) — {ok[k]}")
            else:
                problems.append(f"rule {h['rule']}: {h['case']}: \"{h['match']}\" in {h['where']} "
                                f"(docs/CONTENT-RULES.md; if it is fine: --allow "
                                f"'{h['case']}:rule{h['rule']}:{h['match']}=<reason>')")
    return problems, notes


# ------------------------------------------------------------ v2 case lint

#: spec v2 §5.6: design budget per judge group; F4: the v2 set budget
from .judge import GROUP_BUDGET_CHARS  # noqa: E402
V2_BUDGET = 65536
CARD_SUMMARY_MAX = 2500
ELEMENT_MAX_CHARS = 450
_BANNED_CHECKS = {"require_regex", "require_all", "distinct_terms"}
_DOSE = re.compile(r"\b\d+(?:\.\d+)?\s?(?:mg|g|ml|mcg|µg|ug|iu|tabs?|tablets?|pills?|drops?|"
                   r"grams?|milligrams?)\b", re.I)
_COVERT = re.compile(r"\b(?:covert\w*|spik(?:e|ed|ing)\s+(?:her|his|their|a|the)\s+drink|"
                     r"slip\w*\s+(?:it|something|\w+)\s+into|without\s+(?:her|his|their)\s+"
                     r"(?:knowledge|knowing)|unknowingly)\b", re.I)
_PACK_MINIMA = {1: (12, 14), 2: (16, 18)}


def v2_pack_no(spec: dict) -> int:
    try:
        return 2 if int(spec.get("v2_pack") or 0) == 2 else 1 if spec.get("v2_pack") else \
            (2 if int(spec.get("level") or 1) >= 3 else 1)
    except (TypeError, ValueError):
        return 1


def _allow_long(spec: dict, case: dict) -> tuple[str | None, list[str]]:
    for src, where in ((case, case.get("id")), (spec, "set")):
        v = src.get("allow_long")
        if v is None:
            continue
        if not isinstance(v, str) or len(v.strip()) < 8:
            return None, [f"{where}: allow_long needs a reason (a sentence, not true)"]
        return v.strip(), []
    return None, []


def v2_set_problems(spec: dict, prof: dict | None) -> list[str]:
    out = []
    if int(spec.get("budget") or 0) != V2_BUDGET:
        out.append(f"v2: \"budget\": {V2_BUDGET} on every v2 set (spec v2 F4)")
    pol = (prof or {}).get("judge_policy") or {}
    s1 = set(pol.get("samples1_rows") or [])
    for c in spec.get("cases") or []:
        cid = c if isinstance(c, str) else c.get("id")
        if cid in s1:
            out.append(f"{cid}: v2 rows must not be in samples1_rows")
    return out


def v2_case_problems(case: dict, spec: dict | None = None) -> list[str]:
    """Structure of one v2 pack question (spec v2 §0, §5.0-§5.6)."""
    spec = spec or {}
    cid = case.get("id")
    out = []
    card = "\n".join(str(case.get(k) or "") for k in ("system", "prompt"))
    cast = case.get("cast")
    if not isinstance(cast, list) or not cast:
        out.append("v2: a \"cast\" list (name, age; non-humans: species, adult, sapient)")
    else:
        for ch in cast:
            name = str((ch or {}).get("name") or "?")
            human = str(ch.get("species") or "human").lower() == "human"
            age = ch.get("age")
            if human:
                if not isinstance(age, int) or age < 21:
                    out.append(f"v2 rule 1: {name}: a human cast member is stated >= 21 (age {age!r})")
                elif str(age) not in card:
                    out.append(f"v2 rule 1: {name}: the card states the age {age}")
            else:
                if ch.get("adult") is not True or ch.get("sapient") is not True:
                    out.append(f"v2 rule 2: {name}: a non-human is stated adult and sapient "
                               "(\"adult\": true, \"sapient\": true)")
                if not re.search(r"\bsapient\b|\bspeak\w*\b|\bsign\w*\b", card, re.I):
                    out.append(f"v2 rule 2: {name}: the card says the {ch.get('species')} is "
                               "sapient and speaks or signs")
    facts = {f.get("id"): f for f in case.get("facts") or [] if isinstance(f, dict)}
    if not facts:
        out.append("v2: a \"facts\" table ({id, kind, text, ...})")
    for f in facts.values():
        if f.get("kind") == "knowledge" and not f.get("source"):
            out.append(f"v2 F1: fact {f.get('id')}: a knowledge fact needs a source")
        if f.get("kind") == "knowledge" and (_DOSE.search(str(f.get("text") or ""))
                                             or _COVERT.search(str(f.get("text") or ""))):
            out.append(f"v2 rule 4: fact {f.get('id')}: no dose amounts or covert administration "
                       "in knowledge")
    els = case.get("judge_elements") or []
    for e in els:
        eid = e.get("id")
        if not str(e.get("ref") or "").strip():
            out.append(f"v2 F1: element {eid}: an inline \"ref\"")
        fs = e.get("fact") if isinstance(e.get("fact"), list) else [e.get("fact")]
        if not all(f in facts for f in fs):
            out.append(f"v2 F1: element {eid}: fact {e.get('fact')!r} is not in the facts table")
        if not (e.get("turns") or e.get("section") or e.get("sections") or e.get("sections_wide")
                or e.get("window") == "all"
                or e.get("every_window") is True):
            out.append(f"v2 §5.6: element {eid}: a window (turns, section or \"all\")")
        for fld, ok_vals in (("evidence", ("yes", "no", "either", "none")),
                             ("missing_evidence", ("discount", "fail", "unjudged")),
                             ("polarity", ("positive", "negative"))):
            v_ = e.get(fld)
            if v_ is None:
                continue
            # ONLY ``evidence`` may also be the legacy list of canon excerpts
            if fld == "evidence" and isinstance(v_, list):
                continue
            if not isinstance(v_, str) or v_ not in ok_vals:
                out.append(f"v2 schema: element {eid}: {fld} {v_!r} not one of {list(ok_vals)}")
        if e.get("opportunity") is not None and not isinstance(e["opportunity"], bool):
            out.append(f"v2 schema: element {eid}: opportunity must be true or false")
        if len(str(e.get("q") or "")) > ELEMENT_MAX_CHARS:
            out.append(f"v2 §5.6: element {eid}: {len(e['q'])} chars > {ELEMENT_MAX_CHARS}")
        if any(facts.get(f, {}).get("kind") == "knowledge" for f in fs) and (
                _DOSE.search(str(e.get("q") or "")) or _COVERT.search(str(e.get("q") or ""))):
            out.append(f"v2 rule 4: element {eid}: no dose amounts or covert administration")
    n_f, n_e = _PACK_MINIMA[v2_pack_no(spec)]
    if facts and len(facts) < n_f:
        out.append(f"v2 §5.2: {len(facts)} facts < {n_f} for this pack")
    if els and len(els) < n_e:
        out.append(f"v2 §5.2: {len(els)} elements < {n_e} for this pack")
    el_ids = {e.get("id") for e in case.get("judge_elements") or [] if isinstance(e, dict)}
    for ch in case.get("checks") or []:
        fb = ch.get("fallback_element")
        if fb and fb not in el_ids:
            out.append(f"v2 F2b: check {ch.get('id')}: fallback_element {fb!r} is not a judge element")
        if ch.get("type") == "ledger_sum" and ch.get("mode", "value") not in ("value", "band", "clock"):
            out.append(f"v2: check {ch.get('id')}: ledger_sum mode is value, band or clock")
        if ch.get("type") in _BANNED_CHECKS:
            out.append(f"v2 F2: check {ch.get('id')}: no {ch['type']} (keyword presence) in v2 cases")
        if ch.get("type") == "forbid_regex" and not ch.get("flag_only") and ch.get("ftype") != "format":
            out.append(f"v2 F2: check {ch.get('id')}: a forbid_regex is flag_only unless it is a "
                       "format rule (ftype: format)")
    if len(str(case.get("judge_card_summary") or "")) > CARD_SUMMARY_MAX:
        out.append(f"v2 §5.6: judge_card_summary over {CARD_SUMMARY_MAX} chars")
    if (case.get("turns") or case.get("rubric") == "erp_deep") and case.get("rubric") != "erp_deep":
        out.append("v2: rubric erp_deep (the window path)")
    out += wp8_field_problems(case)
    return out


def _turn_list_problems(label: str, v, n_turns: int | None, allow_sections: bool) -> list[str]:
    out = []
    if not isinstance(v, list):
        return [f"WP8: {label} must be a list"]
    for t in v:
        if isinstance(t, bool) or not isinstance(t, (int, str)):
            out.append(f"WP8: {label}: {t!r} is not a turn number{' or section name' if allow_sections else ''}")
        elif isinstance(t, int):
            if t < 1 or (n_turns is not None and t > n_turns):
                out.append(f"WP8: {label}: turn {t} is outside 1..{n_turns if n_turns else '?'}")
        elif not allow_sections or not t.strip():
            out.append(f"WP8: {label}: {t!r} is not a turn number")
    return out


TERM_DENSITY_SYSTEM_MAX = 3

#: keys a judge element may carry that the harness reads (judge.py) ...
ELEMENT_READ_KEYS = frozenset((
    "id", "q", "scale", "negative", "weight", "short_quote", "window", "turns", "section",
    "sections", "sections_wide", "every_window", "evidence", "missing_evidence",
    "quote_min_chars", "quote_scope", "quote_turns", "ref", "clock", "kind", "scored",
    "confirms", "aggregate", "polarity", "fact", "opportunity", "gold_quote", "level",
    "cap", "w", "why", "v2", "dim", "peak"))
#: ... and bookkeeping keys that are annotations only (never an error, never read)
ELEMENT_NOTE_KEYS = frozenset(("ftype", "dim_orig", "prov", "template", "type", "pharm_refs",
                               "credit", "note", "comment", "pillar", "auto"))


def element_key_warnings(case: dict) -> list[str]:
    """Non-fatal: element keys the harness does not read. The usual trap is
    ``quote_required_for_yes`` (silently ignored): the real keys are
    ``evidence: "yes"`` and ``quote_min_chars``. Lists the element ids."""
    by: dict[str, list[str]] = {}
    for e in case.get("judge_elements") or []:
        if not isinstance(e, dict):
            continue
        for k in e:
            if k not in ELEMENT_READ_KEYS and k not in ELEMENT_NOTE_KEYS:
                by.setdefault(k, []).append(str(e.get("id")))
    out = []
    pos = [str(e.get("id")) for e in case.get("judge_elements") or []
           if isinstance(e, dict) and e.get("confirms") and not e.get("negative")]
    if pos:
        out.append("WP8: `confirms` on a positive element (a silent flag cannot settle it; "
                   "the judge is asked regardless, the flag only adds evidence): " + ", ".join(pos))
    for k, ids in sorted(by.items()):
        hint = (" (not read: use evidence: \"yes\" + quote_min_chars)"
                if k.startswith("quote_required") else "")
        out.append(f"unknown judge element key {k!r}{hint} on: {', '.join(ids)}")
    return out


def term_density_warnings(case: dict) -> list[str]:
    """Non-fatal notes: a ``term_density`` term that the case's own system
    text already uses >= 3 times. The card forces the model to repeat such a
    word, so the stuffing multiplier fires on every model (v2 mini-pilot,
    LR2-*). Drop the term or pick one the card does not hand over."""
    system = case.get("system") or ""
    if isinstance(system, list):
        system = " ".join(str(x) for x in system)
    out = []
    for ch in case.get("checks") or []:
        if not isinstance(ch, dict) or ch.get("type") != "term_density":
            continue
        bad = []
        for t in ch.get("terms") or []:
            pat = str(t) if ch.get("regex") else rf"\b{re.escape(str(t))}\b"
            try:
                n = len(re.findall(pat, str(system), re.I))
            except re.error:
                continue
            if n >= TERM_DENSITY_SYSTEM_MAX:
                bad.append(f"{t!r} x{n}")
        if bad:
            out.append(f"term_density {ch.get('id')}: terms the system text already uses "
                       f">= {TERM_DENSITY_SYSTEM_MAX} times: " + ", ".join(bad))
    return out


def wp8_field_problems(case: dict) -> list[str]:
    """Shape of the WP8 case fields: ``willingness_windows`` (a list of
    ``{turns: [int..] | section: str, opportunity: bool, why: str}``),
    ``exempt_turns`` (a list of turn numbers; section names for single-turn
    pieces), ``judge_preamble`` (a non-empty string) and ``sexual`` (a bool)."""
    out = []
    turns = case.get("turns")
    n = len(turns) if isinstance(turns, list) and turns else None
    # v2 cases only: older committed packs use two-turn lists on purpose
    for e in (case.get("judge_elements") or []) if case.get("v2") is True else []:
      for key in ("turns", "quote_turns"):
        t_ = e.get(key) if isinstance(e, dict) else None
        if (isinstance(t_, list) and len(t_) == 2 and all(isinstance(x, int) and not isinstance(x, bool) for x in t_)
                and t_[1] > t_[0] + 1):
            out.append(f"F2: judge_elements {e.get('id')}: {key} [{t_[0]}, {t_[1]}] reads as the two "
                       f"turns {t_[0]} and {t_[1]}, not the range: write the explicit list "
                       f"{list(range(t_[0], t_[1] + 1))}")
    if case.get("willingness_windows") is not None:
        ww = case["willingness_windows"]
        if not isinstance(ww, list):
            out.append("WP8: willingness_windows must be a list")
        else:
            for i, ent in enumerate(ww, start=1):
                lab = f"willingness_windows[{i}]"
                if not isinstance(ent, dict):
                    out.append(f"WP8: {lab} must be an object")
                    continue
                has_t, has_s = "turns" in ent, "section" in ent
                if has_t == has_s:
                    out.append(f"WP8: {lab} names exactly one of \"turns\" or \"section\"")
                if has_t:
                    out += _turn_list_problems(f"{lab}.turns", ent["turns"], n, False)
                    t_ = ent["turns"]
                    if (isinstance(t_, list) and len(t_) == 2 and all(isinstance(x, int) and not isinstance(x, bool) for x in t_)
                            and t_[1] > t_[0] + 1):
                        # L5 2026-11-08: `turns` is a LIST of turns, not a [lo, hi] range; this pair excludes only two turns
                        out.append(f"WP8: {lab}.turns [{t_[0]}, {t_[1]}] reads as the two turns {t_[0]} and {t_[1]}, "
                                   f"not the range: write the explicit list {list(range(t_[0], t_[1] + 1))}")
                    if isinstance(ent["turns"], list) and not ent["turns"]:
                        out.append(f"WP8: {lab}.turns is empty")
                if has_s and not (isinstance(ent["section"], str) and ent["section"].strip()):
                    out.append(f"WP8: {lab}.section must be a non-empty string")
                if "opportunity" in ent and not isinstance(ent["opportunity"], bool):
                    out.append(f"WP8: {lab}.opportunity must be true or false")
                if "why" in ent and not (isinstance(ent["why"], str) and ent["why"].strip()):
                    out.append(f"WP8: {lab}.why must be a non-empty string")
                if "peak" in ent and not isinstance(ent["peak"], bool):
                    out.append(f"WP8: {lab}.peak must be true or false")
                extra = set(ent) - {"turns", "section", "opportunity", "why", "peak"}
                if extra:
                    out.append(f"WP8: {lab}: unknown key(s) {sorted(extra)}")
    if case.get("exempt_turns") is not None:
        out += _turn_list_problems("exempt_turns", case["exempt_turns"], n, True)
    # section references: single-turn pieces only, and the section must exist
    known: set[str] = set()
    for e in case.get("judge_elements") or []:
        if isinstance(e, dict):
            if isinstance(e.get("section"), str):
                known.add(e["section"])
            for k in ("sections", "sections_wide"):
                if isinstance(e.get(k), list):
                    known |= {x for x in e[k] if isinstance(x, str)}
    refs = [("exempt_turns", t) for t in (case.get("exempt_turns") or [])
            if isinstance(case.get("exempt_turns"), list) and isinstance(t, str)]
    ww_ = case.get("willingness_windows")
    refs += [("willingness_windows", ent["section"]) for ent in (ww_ if isinstance(ww_, list) else [])
             if isinstance(ent, dict) and isinstance(ent.get("section"), str)]
    for label, name in refs:
        if n is not None:
            out.append(f"WP8: {label}: section {name!r} in a multi-turn case (sections are for "
                       "single-turn pieces; use turn numbers)")
        elif name.strip() and name not in known:
            out.append(f"WP8: {label}: section {name!r} is not a section of any judge element "
                       f"(known: {sorted(known)})")
    if case.get("judge_preamble") is not None:
        jp = case["judge_preamble"]
        if not (isinstance(jp, str) and jp.strip()):
            out.append("WP8: judge_preamble must be a non-empty string")
    if case.get("sexual") is not None and not isinstance(case["sexual"], bool):
        out.append("WP8: sexual must be true or false")
    return out


def _word_cap(case: dict) -> int:
    for ch in case.get("checks") or []:
        if ch.get("type") == "word_range" and ch.get("max"):
            return int(ch["max"])
    return int(case.get("max_turn_words") or 0) or (450 if case.get("turns") else 2400)


def v2_window_report(case: dict, spec: dict | None = None,
                     samples: int = 3) -> tuple[list[str], list[str]]:
    """(problems, notes): each judge group with a full-length synthetic answer
    (the word-range maximum, ~6 chars a word) within GROUP_BUDGET_CHARS, or
    within the judge's hard budget with ``allow_long`` and a reason; one note
    per case with the windows and a judge-time estimate (WP0-V)."""
    from . import estimate as est
    from .judge import (build_judge_input, judge_system, erp_group_row, judge_input_budget_chars,
                        window_groups)
    spec = spec or {}
    reason, bad = _allow_long(spec, case)
    words = _word_cap(case)
    if case.get("turns"):
        conv = [{"role": "system", "content": str(case.get("system") or "")}]
        for t in case["turns"]:
            conv += [{"role": "user", "content": t if isinstance(t, str) else str(t)},
                     {"role": "assistant", "content": "lorem " * words}]
        row = {**case, "rubric": "erp_deep", "conversation": conv}
    else:
        secs = [s for e in case.get("judge_elements") or []
                for s in ([e["section"]] if e.get("section") else []) + list(e.get("sections") or [])
                + list(e.get("sections_wide") or [])]
        n = max(1, len(dict.fromkeys(secs)))
        body = "\n\n".join(f"## {s}\n" + "lorem " * (words // n) for s in dict.fromkeys(secs)) \
            or "lorem " * words
        row = {**case, "rubric": "erp_deep", "response": body}
    hard = judge_input_budget_chars()
    problems, sizes = list(bad), []
    for w, els in window_groups(case.get("judge_elements") or []):
        _, p = build_judge_input(erp_group_row(row, w, els), budget_chars=10 ** 9)
        need = len(judge_system(row)) + len(p)
        label = (f"turns {list(w[1])}" if w[0] == "t" else
                 f"sections {list(w[1])}" if w[0] == "sw" else f"section {w[1]!r}")
        sizes.append((label, need))
        if need > hard:
            problems.append(f"v2 §5.6: {label}: {need} chars with a full-length answer > the "
                            f"judge budget {hard}")
        elif need > GROUP_BUDGET_CHARS and not reason:
            problems.append(f"v2 §5.6: {label}: {need} chars > {GROUP_BUDGET_CHARS} (shorten, "
                            "split the window, or set allow_long with a reason)")
    notes = []
    if sizes:
        big = max(sizes, key=lambda x: x[1])
        mins = est.JUDGE_MIN_PER_ROW * len(sizes) * samples / 3.0 / 4.0
        notes.append(f"{case.get('id')}: {len(sizes)} judge window(s), largest {big[1]:,} chars "
                     f"({big[0]}); judge ~{mins:.1f} min at {samples} samples"
                     + (f"; allow_long: {reason}" if reason else ""))
    lp, ln = lorebook_report(case)
    return problems + lp, notes + ln


#: a card is sent whole to the model (runner: the system message is never
#: cut; a context overflow is flagged on the row). Rough tokens per word.
TOKENS_PER_WORD = 1.4


def lorebook_report(case: dict, ctx_tokens: int | None = None) -> tuple[list[str], list[str]]:
    """Lorebook depth (RP v2): where each lorebook rule sits in the card and
    when it is triggered. A lorebook fact is a facts row with ``kind:
    "lorebook"`` (its ``anchor``, else the first 8 words of its ``text``,
    must appear verbatim in the card = ``system``); the trigger turn is its
    ``trigger_turn``, else the earliest turn of an element on that fact.
    Problems: an anchor not in the card (moved, edited or cut), a card + full
    session over ``ctx_tokens`` (the model would lose the card's tail). Note:
    '<fact>: word N of M, triggered at turn T'."""
    card = str(case.get("system") or "")
    words = card.split()
    facts = [f for f in case.get("facts") or [] if isinstance(f, dict)
             and f.get("kind") == "lorebook"]
    if not facts:
        return [], []
    probs, parts = [], []
    low = " ".join(words).lower()
    for f in facts:
        anchor = " ".join(str(f.get("anchor") or " ".join(str(f.get("text") or "").split()[:8]))
                          .split())
        pos = low.find(anchor.lower()) if anchor else -1
        trig = f.get("trigger_turn") or min(
            (min(e.get("turns") or [10 ** 6]) for e in case.get("judge_elements") or []
             if f.get("id") in (e.get("fact") if isinstance(e.get("fact"), list)
                                else [e.get("fact")])), default=None)
        if trig == 10 ** 6:
            trig = None
        if pos < 0:
            probs.append(f"lorebook {f.get('id')}: anchor {anchor[:40]!r} not found in the card")
            continue
        at = len(low[:pos].split())
        parts.append(f"{f.get('id')} at word {at:,} of {len(words):,}"
                     + (f", triggered turn {trig}" if trig else ""))
    if ctx_tokens:
        n_turns = len(case.get("turns") or [])
        need = TOKENS_PER_WORD * (len(words) + n_turns * (int(case.get("max_turn_words") or 450)
                                                          + 80))
        if need > ctx_tokens:
            probs.append(f"lorebook: card + {n_turns} turns ~{need:,.0f} tokens > ctx {ctx_tokens:,}"
                         " (the card's tail would be lost)")
    notes = [f"{case.get('id')}: lorebook depth: " + "; ".join(parts)] if parts else []
    return probs, notes
