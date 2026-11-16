"""Reasoning replay: the 4.5 rule (2026-11-14, EXP-20261114-11).

Whether a model gets its own earlier reasoning back in a multi-turn
conversation is decided by WHAT ITS OWN CHAT TEMPLATE DOES AT DEFAULT
SETTINGS: the template is rendered (no GPU, no request) with a history whose
earlier assistant turn carries ``reasoning_content``; if that reasoning
reaches the prompt, the maker's template keeps it and the bench replays it.
A documented card or API recommendation overrides the render (profile
``reasoning_replay_rules``: Nanbeige4.3 says ``preserve_thinking`` False for
chat; MiniMax / Kimi K3 APIs say the reasoning must go back).

Why not conformance c3: c3 asks a model to recall a number that exists only
in its reasoning, so it favours replay wherever the template renders
``reasoning_content`` -- it measures what the template renders, not what the
maker intends. It is a diagnostic warning now (conformance.py).

The value is recorded per model in models.yaml::

    reasoning_replay: {value: true, source: template, template_sha: 1a2b...,
                       quote: "{%- if preserve_thinking is undefined or ..."}

``source`` is ``template`` | ``card`` | ``api-doc`` (a rule matched; it
carries ``url`` and ``quote``) | ``ab`` (a rule backed by a MEASURED A/B on the
family -- replay on vs off, same sessions -- that overrides the template; it
carries ``evidence``: the experiment id and the result, and a rule with source
``ab`` and no evidence is refused) | ``default`` (a hosted API with no template
and no rule: the provider default). ``template_sha`` pins the template the
record was rendered from: a rig model whose served template has another sha
is refused (fails closed) until ``crucibleforge replay-rule <label> --write``
re-renders it. A rig model with no record gets one without a person: the
bench-all plan renders it and ``--go`` writes it. Every row stamps
``protocol.reasoning_replay`` and ``protocol.reasoning_replay_source``.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

SOURCES = ("template", "card", "api-doc", "ab", "default")
RIG_TYPES = ("studioforge", "lmstudio")

#: marker strings of the render probe
_R = "R1_PRIOR_REASONING_7f3c"
_A = "A1_PRIOR_ANSWER_7f3c"
PROBE_MESSAGES = [
    {"role": "system", "content": "SYS"},
    {"role": "user", "content": "U1"},
    {"role": "assistant", "content": _A, "reasoning_content": _R},
    {"role": "user", "content": "U2"},
]


def template_sha(template: str | None) -> str | None:
    if not template:
        return None
    return hashlib.sha256(str(template).encode("utf-8")).hexdigest()[:16]


def render(template: str, messages: list[dict], **kwargs) -> str:
    """Render a HF/llama.cpp chat template in a sandbox (what the server does
    before it tokenizes). Raises on a template that does not render."""
    from jinja2.sandbox import ImmutableSandboxedEnvironment

    def _raise(msg):
        raise ValueError(f"template raised: {msg}")

    env = ImmutableSandboxedEnvironment(trim_blocks=True, lstrip_blocks=True)
    env.globals.update(raise_exception=_raise,
                       strftime_now=lambda f: _dt.datetime(2026, 1, 1).strftime(f))
    env.filters["tojson"] = lambda x, **k: json.dumps(x, ensure_ascii=False)
    return env.from_string(template).render(messages=messages, add_generation_prompt=True,
                                            bos_token="", eos_token="", **kwargs)


def deciding_line(template: str) -> str:
    """The template statement that decides what history keeps (for the
    record's quote), whitespace collapsed: the first ``{% if %}`` testing a
    ``preserve_*`` switch together with the last-query index, else the
    last-query test alone, else the first tag reading ``reasoning_content``,
    else one splitting on ``</think>``."""
    tags = [re.sub(r"\s+", " ", m.group(0)) for m in re.finditer(r"\{%-?.*?-?%\}", str(template), re.S)]
    for test in (lambda t: re.search(r"\bif\b", t) and "preserve" in t
                 and re.search(r"last_query_index|loop\.index0", t),
                 lambda t: re.search(r"loop\.index0\s*>\s*ns\.last_query_index", t),
                 lambda t: "reasoning_content" in t,
                 lambda t: "</think>" in t):
        hit = next((t for t in tags if test(t)), None)
        if hit:
            return hit[:200]
    return ""


def render_probe(template: str | None) -> dict:
    """{"keeps": bool | None, "error": str} -- does a PRIOR turn's reasoning
    reach the prompt with the template's default settings?"""
    if not template:
        return {"keeps": None, "error": "no chat template"}
    try:
        p = render(template, PROBE_MESSAGES)
    except Exception as e:  # noqa: BLE001 -- e.g. a template that refuses a system role
        try:
            p = render(template, PROBE_MESSAGES[1:])
        except Exception:  # noqa: BLE001 -- a template that cannot render decides nothing
            return {"keeps": None, "error": f"{type(e).__name__}: {str(e)[:160]}"}
    if _A not in p:
        return {"keeps": None, "error": "the render lost the earlier answer: probe inconclusive"}
    return {"keeps": _R in p, "error": ""}


# ------------------------------------------------------------- rules + records

def rules(cfg_p: dict | None) -> list[dict]:
    return list(((cfg_p or {}).get("defaults") or {}).get("reasoning_replay_rules") or [])


def match_rule(cfg_p: dict | None, entry: dict, provider_type: str | None = None) -> dict | None:
    mid = str(entry.get("model_id") or "")
    for r in rules(cfg_p):
        if r.get("provider_type") and r["provider_type"] != provider_type:
            continue
        if r.get("match") and re.search(r["match"], mid):
            return r
    return None


def resolve(cfg_p: dict | None, entry: dict, template: str | None,
            provider_type: str | None, provider_default: bool = False) -> dict | None:
    """The record the rule gives NOW (None: a rig model whose template is
    unreadable or does not render -- nothing can be decided)."""
    sha = template_sha(template)
    probe = render_probe(template) if template else {"keeps": None, "error": "no chat template"}
    rule = match_rule(cfg_p, entry, provider_type)
    if rule is not None:
        if rule.get("source") not in SOURCES or rule.get("source") == "default":
            raise ValueError(f"reasoning_replay_rules {rule.get('id')}: source {rule.get('source')!r}")
        if rule["source"] == "ab" and not rule.get("evidence"):
            raise ValueError(f"reasoning_replay_rules {rule.get('id')}: source ab needs evidence "
                             "(the A/B experiment id and its result)")
        rec = {"value": bool(rule["value"]), "source": rule["source"], "rule": rule["id"],
               "url": rule.get("url"), "quote": rule.get("quote"), "evidence": rule.get("evidence")}
        if sha:
            rec["template_sha"] = sha
            rec["template_value"] = probe["keeps"]
        return rec
    if probe["keeps"] is not None:
        return {"value": bool(probe["keeps"]), "source": "template", "template_sha": sha,
                "quote": deciding_line(template)}
    if provider_type in RIG_TYPES:
        return None
    return {"value": bool(provider_default), "source": "default"}


def stored(entry: dict) -> dict | None:
    """The entry's record (a legacy bare bool is not a record)."""
    r = entry.get("reasoning_replay")
    return r if isinstance(r, dict) and "value" in r else None


def value(entry: dict, provider_default: bool = False) -> bool:
    """The replay the run uses: the record's value; a legacy bool as written;
    else the provider default (MiniMax: on)."""
    r = entry.get("reasoning_replay")
    if isinstance(r, dict):
        return bool(r.get("value"))
    if r is not None:
        return bool(r)
    return bool(provider_default)


def source(entry: dict) -> str:
    r = entry.get("reasoning_replay")
    if isinstance(r, dict):
        return str(r.get("source") or "unknown")
    return "legacy" if r is not None else "default"


@dataclass
class Verdict:
    ok: bool
    label: str
    #: ok | new | stale | mismatch | unreadable
    status: str
    record: dict | None = None
    reason: str = ""
    fix: str = ""
    was: object = None
    notes: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"ok": self.ok, "status": self.status, "record": self.record,
                "reason": self.reason, "fix": self.fix, "was": self.was}


def _same(a: dict, b: dict) -> bool:
    keys = ("value", "source", "template_sha", "rule")
    return all(a.get(k) == b.get(k) for k in keys)


def check(cfg_p: dict | None, entry: dict, provider_type: str | None,
          template: str | None, provider_default: bool = False) -> Verdict:
    """The replay preflight for ONE model.

    * stored record matches the rule now -> ok;
    * no record (or a legacy bool) and the rule can decide -> ``new``: ok, and
      the record is written at ``--go`` (``was`` = the legacy value);
    * stored record for ANOTHER template sha, or one the rule no longer
      gives -> refused until re-rendered (fails closed);
    * a rig model whose template cannot be read or rendered and that has no
      record -> refused."""
    from .settings_gate import effective_entry
    e = effective_entry(entry)
    label = str(e.get("name") or e.get("model_id") or "")
    fix = f"crucibleforge replay-rule {label} --write"
    have = stored(e)
    fresh = resolve(cfg_p, e, template, provider_type, provider_default)
    if fresh is None:
        if have is not None and template is None:
            return Verdict(True, label, "ok", have,
                           notes=["the served template could not be read now: the record stands"])
        why = ("its chat template could not be read" if template is None else
               f"its chat template does not render ({render_probe(template)['error']})")
        return Verdict(False, label, "unreadable", None, fix=fix,
                       reason=f"{label}: no reasoning-replay record and {why}")
    if have is None:
        return Verdict(True, label, "new", fresh, was=e.get("reasoning_replay"))
    if provider_type in RIG_TYPES and have.get("template_sha") and fresh.get("template_sha") \
            and have["template_sha"] != fresh["template_sha"]:
        return Verdict(False, label, "stale", fresh, fix=fix, was=have,
                       reason=(f"{label}: the served chat template changed (sha "
                               f"{have['template_sha']} -> {fresh['template_sha']}); the replay "
                               f"record must be re-rendered"))
    if not _same(have, fresh):
        return Verdict(False, label, "mismatch", fresh, fix=fix, was=have,
                       reason=(f"{label}: the stored replay record ({have.get('value')}, "
                               f"{have.get('source')}) is not what the rule gives now "
                               f"({fresh.get('value')}, {fresh.get('source')})"))
    return Verdict(True, label, "ok", have)


def gate_plan(p: dict, cfg: dict, cfg_p: dict | None, templates: dict | None) -> dict:
    """Apply the replay preflight to a bench-all plan (after settings_gate):
    ``p["replay"]`` = {label: Verdict.to_dict()}; refused models with
    generation work are planned out (like settings_gate) and listed in
    ``p["replay_blocked"]``; ``p["replay_new"]`` = {label: record} to write
    at ``--go``."""
    from .providers import reasoning_replay_default
    from .settings_gate import drop_from_plan
    provs = cfg.get("providers") or {}
    by_name = {m["name"]: m for m in cfg.get("models") or []}
    add = {a["name"]: a for a in p.get("add") or []}
    default_prov = next((n for n, pr in provs.items() if (pr or {}).get("type") == "studioforge"), None)
    work = ({m for g in p.get("groups") or [] for m in g["models"]}
            | {x.get("model") for x in p.get("escalate") or []}
            | {lp.get("model") for lp in p.get("ladder") or []} | set(add))
    out, blocked, new = {}, [], {}
    # the rig listing (GGUF chat templates) could not be read at all (rig down,
    # no management access): nothing can be rendered, so nothing is judged --
    # said in the plan, never a silent pass for a model the rule could check
    listing_ok = bool(templates)
    p["replay_unchecked"] = not listing_ok
    for label in list(dict.fromkeys(list(p.get("candidates") or []) + list(add))):
        entry = by_name.get(label) or add.get(label)
        if entry is None:
            continue
        pname = entry.get("provider") or default_prov
        pr = provs.get(pname) or {}
        ptype = pr.get("type") or ("studioforge" if label in add else None)
        v = check(cfg_p, entry, ptype, (templates or {}).get(entry.get("model_id")),
                  provider_default=reasoning_replay_default(pr) if pr else False)
        if not listing_ok and ptype in RIG_TYPES and v.status in ("unreadable",):
            out[label] = {**v.to_dict(), "ok": True, "status": "unchecked",
                          "reason": "the rig listing (chat templates) could not be read"}
            continue
        out[label] = v.to_dict()
        if v.status == "new" and ptype in RIG_TYPES:
            new[label] = v.record
        if not v.ok and label in work:
            blocked.append(label)
    if blocked:
        drop_from_plan(p, set(blocked))
    p["replay"], p["replay_blocked"], p["replay_new"] = out, blocked, new
    return p


def plan_lines(p: dict) -> list[str]:
    L = []
    if p.get("replay_unchecked") and any((v or {}).get("status") == "unchecked"
                                         for v in (p.get("replay") or {}).values()):
        L.append("  ! reasoning replay NOT CHECKED: the rig's chat templates could not be read "
                 "(models without a record run on their registry/provider default)")
    for label, rec in (p.get("replay_new") or {}).items():
        was = ((p.get("replay") or {}).get(label) or {}).get("was")
        L.append(f"  + replay {label}: {rec['value']} ({rec['source']}"
                 + (f", rule {rec['rule']}" if rec.get("rule") else "")
                 + ")" + (f" — was {was}" if was is not None else "") + " — recorded at --go")
    for label in p.get("replay_blocked") or []:
        v = (p.get("replay") or {}).get(label) or {}
        L.append(f"  ! NOT RUN {label}: {v.get('reason')}")
        L.append(f"    fix: {v.get('fix')}")
    return L


# ------------------------------------------------------------- registry write

def flow(rec: dict) -> str:
    """One-line YAML flow mapping of a record (key order fixed)."""
    import yaml
    order = ("value", "source", "rule", "template_sha", "template_value", "url", "quote", "evidence",
             "checked")
    d = {k: rec[k] for k in order if rec.get(k) is not None}
    return yaml.safe_dump(d, default_flow_style=True, width=10**6, allow_unicode=True,
                          sort_keys=False).strip()


def write_records(path: str | Path, records: dict[str, dict]) -> list[str]:
    """Set ``reasoning_replay:`` of each named entry in models.yaml as TEXT
    (comments elsewhere survive): an existing top-level ``reasoning_replay:``
    line of that entry is replaced, otherwise one is inserted after its
    ``model_id:`` line. The result is parsed and must differ ONLY in those
    keys, or nothing is written (ValueError). Returns the labels written."""
    import yaml
    if not records:
        return []
    path = Path(path)
    text = path.read_text(encoding="utf-8")
    before = yaml.safe_load(text) or {}
    lines = text.splitlines(keepends=True)
    stamp = _dt.date.today().isoformat()
    done = []
    for label, rec in records.items():
        rec = {**rec, "checked": stamp}
        start = next((i for i, ln in enumerate(lines)
                      if re.match(rf"^(\s*)- name:\s*['\"]?{re.escape(label)}['\"]?\s*(#.*)?$", ln)), None)
        if start is None:
            raise ValueError(f"{path}: no entry named {label}")
        ind = re.match(r"^(\s*)-", lines[start]).group(1) + "  "
        end = start + 1
        while end < len(lines) and not re.match(rf"^{ind[:-2]}- |^\S", lines[end]):
            end += 1
        new_line = f"{ind}reasoning_replay: {flow(rec)}\n"
        at = next((i for i in range(start + 1, end)
                   if re.match(rf"^{ind}reasoning_replay:", lines[i])), None)
        if at is not None:
            # a block-style mapping under the key goes with it
            j = at + 1
            while j < end and re.match(rf"^{ind}\s+\S", lines[j]):
                j += 1
            lines[at:j] = [new_line]
        else:
            mid = next((i for i in range(start, end) if re.match(rf"^({ind}|\s*- )model_id:", lines[i])),
                       start)
            lines.insert(mid + 1, new_line)
        done.append(label)
    out = "".join(lines)
    after = yaml.safe_load(out) or {}
    a = {m.get("name"): m for m in before.get("models") or []}
    b = {m.get("name"): m for m in after.get("models") or []}
    if set(a) != set(b):
        raise ValueError("replay record write changed the set of models: nothing written")
    for name in a:
        x = {k: v for k, v in a[name].items() if k != "reasoning_replay"}
        y = {k: v for k, v in b[name].items() if k != "reasoning_replay"}
        if x != y or (name not in records and a[name].get("reasoning_replay") != b[name].get("reasoning_replay")):
            raise ValueError(f"replay record write would change {name} beyond its record: nothing written")
    for name in records:
        if stored(b[name]) is None or bool(b[name]["reasoning_replay"]["value"]) != bool(records[name]["value"]):
            raise ValueError(f"replay record of {name} did not parse back: nothing written")
    tmp = path.with_suffix(".yaml.tmp")
    tmp.write_text(out, encoding="utf-8")
    try:
        import os
        import stat
        os.chmod(tmp, stat.S_IMODE(path.stat().st_mode))
    except OSError:
        pass
    tmp.replace(path)
    return done


def replayed_digest(messages: list[dict]) -> list[dict]:
    """What a request sent back of earlier reasoning: one entry per message
    carrying ``reasoning_content`` -- its index, size and sha256 (the row of
    the turn that produced it stores the text itself)."""
    out = []
    for i, m in enumerate(messages or []):
        rc = m.get("reasoning_content") if isinstance(m, dict) else None
        if rc:
            out.append({"msg": i, "chars": len(rc),
                        "sha256": hashlib.sha256(rc.encode("utf-8")).hexdigest()[:16]})
    return out
