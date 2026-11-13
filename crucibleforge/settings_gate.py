"""Recommended-settings gate (2026-11-13).

Every model is measured with its maker's recommended sampler
(runner.resolve_sampler). Nanbeige4.3-33B was benched anyway on server
defaults because nobody had read its card -- the maintainer: "in the future
check recommended settings and templates before testing". So a RIG model is
not benchmarked unless its settings were checked:

* its sampler resolves to a registry ``sampler:`` or a profile rule
  (``recommended_samplers``) -- the card was read and a rule written; or
* it runs on server defaults ON PURPOSE: its registry entry says so::

      settings_checked:
        sampler: none_published          # or: card_says_defaults
        source: https://huggingface.co/<repo>   # where the card was read
        template: "<what the chat template does: thinking switch, forced <think>, ...>"

Hosted API models are exempt (the vendor fixes the sampler; rule
``api-vendor-fixed`` or nothing to send). The fixed regime (each case's own
sampler) is exempt too.

Template facts (:func:`template_facts`) come from the chat template the rig
serves (GGUF metadata) or the HF repo; they are surfaced by ``conformance``
and ``card-check`` as INFO when the entry's ``settings_checked.template``
acknowledges them and WARN when nothing does. They block only through the
rule above (a defaults model needs the template note to pass at all).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

#: provider types served by the rig (the gate applies); everything else is a
#: hosted API whose vendor fixes the sampler
RIG_TYPES = ("studioforge", "lmstudio")

#: accepted ``settings_checked.sampler`` values for a model that runs on the
#: server's defaults on purpose
DEFAULTS_OK = ("none_published", "card_says_defaults")

#: chat-template switches worth knowing before a run: they change what the
#: model is asked to do (thinking on/off, what history keeps)
TEMPLATE_SWITCHES = ("enable_thinking", "preserve_thinking", "clear_thinking",
                     "keep_thinking", "thinking_budget", "reasoning_effort",
                     "thinking_mode", "truncate_history_thinking")


@dataclass
class Verdict:
    ok: bool
    label: str
    #: "rule:<id>" | "registry" | "checked:<sampler>" | "api" | "fixed-regime"
    basis: str
    reason: str = ""
    fix: str = ""
    facts: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"ok": self.ok, "basis": self.basis, "reason": self.reason,
                "fix": self.fix, "facts": list(self.facts)}


def effective_entry(entry: dict) -> dict:
    """A registry entry with a pending registration's inherited settings
    (bench-all ``--inherit-from``) folded in."""
    inh = entry.get("inherit") or {}
    return {**entry, **{k: v for k, v in inh.items() if k != "tags"}}


def checked_block(entry: dict) -> tuple[dict | None, str]:
    """(the entry's ``settings_checked`` block, why it does not count or '')."""
    sc = entry.get("settings_checked")
    if not isinstance(sc, dict):
        return None, "no settings_checked block"
    samp = str(sc.get("sampler") or "").strip()
    src = str(sc.get("source") or "").strip()
    tpl = str(sc.get("template") or "").strip()
    if samp not in DEFAULTS_OK:
        return None, (f"settings_checked.sampler must be one of {', '.join(DEFAULTS_OK)} "
                      f"(got {samp or 'nothing'}) -- a card that publishes a sampler "
                      "gets a rule or a registry `sampler:` instead")
    if not re.match(r"https?://", src):
        return None, "settings_checked.source must be the URL the card was read at"
    if not tpl:
        return None, "settings_checked.template must say what the chat template does"
    return sc, ""


def fix_text(label: str, model_id: str | None = None, registered: bool = True) -> str:
    """What to add, exactly (printed with every refusal)."""
    ref = model_id or label
    pre = "" if registered else (
        f"{label} is not registered yet: `crucibleforge bench-all --only {ref} --go` registers it "
        f"(and runs nothing while it is refused); then ")
    repo = "/".join(ref.split("/")[:2]) if ref.count("/") >= 1 else f"<repo of {ref}>"
    return (f"{pre}read the model card first: `crucibleforge card-check {ref}`. If the card "
            f"publishes a sampler, add a rule to profiles/bench.yaml `recommended_samplers` "
            f"(id, match, sampler, source) or a registry `sampler:`. If it publishes none, add to "
            f"the models.yaml entry of {label}:\n"
            f"    settings_checked:\n"
            f"      sampler: none_published\n"
            f"      source: https://huggingface.co/{repo}   # the card you read (the base model's, often)\n"
            f"      template: \"<thinking switch / forced <think> / none -- from card-check>\"")


def check(cfg_p: dict, entry: dict, provider_type: str | None,
          template: str | None = None, registered: bool = True) -> Verdict:
    """The gate for ONE model. ``cfg_p`` = cfg with the benchmark profile
    applied (its ``recommended_samplers`` and ``regime``). ``template`` (the
    chat template, optional) only adds facts; it never decides."""
    from .runner import REGIME_DEPLOYED, resolve_sampler
    e = effective_entry(entry)
    label = str(e.get("name") or e.get("model_id") or "")
    sc = e.get("settings_checked") if isinstance(e.get("settings_checked"), dict) else {}
    facts = template_facts(template, acknowledged=bool(str(sc.get("template") or "").strip())) \
        if template else []
    if provider_type not in RIG_TYPES:
        return Verdict(True, label, "api", facts=facts)
    if ((cfg_p or {}).get("defaults") or {}).get("regime") != REGIME_DEPLOYED:
        return Verdict(True, label, "fixed-regime", facts=facts)
    _, source = resolve_sampler(cfg_p, e)
    if source == "registry":
        return Verdict(True, label, "registry", facts=facts)
    if source != "default":
        return Verdict(True, label, f"rule:{source}", facts=facts)
    block, why = checked_block(e)
    if block is not None:
        return Verdict(True, label, f"checked:{block['sampler']}", facts=facts)
    return Verdict(False, label, "default",
                   reason=(f"{label} has no recommended sampler (no profile rule matches, no "
                           f"registry `sampler:`) and its settings were not checked ({why})"),
                   fix=fix_text(label, e.get("model_id"), registered=registered), facts=facts)


# ------------------------------------------------------------ template facts

_GEN_RE = re.compile(r"add_generation_prompt", re.I)
_THINK_LIT = re.compile(r"""(['"])[^'"]*<(?:[\w-]+:)?think>[^'"]*\1""")
_EMPTY_THINK = re.compile(r"<((?:[\w-]+:)?think)>\s*(\\n)*\s*</\1>")
_JINJA_COMMENT = re.compile(r"\{#.*?#\}", re.S)


def _gen_prompt_blocks(t: str) -> list[str]:
    """The text of each ``{% if add_generation_prompt %}`` block (up to its
    matching endif, nesting counted)."""
    out = []
    for m in re.finditer(r"\{%-?\s*(?:if|elif)\b[^%]*\badd_generation_prompt\b[^%]*-?%\}", t):
        depth, i = 1, m.end()
        for tag in re.finditer(r"\{%-?\s*(if|endif)\b", t[m.end():]):
            depth += 1 if tag.group(1) == "if" else -1
            if depth == 0:
                i = m.end() + tag.start()
                break
        else:
            i = min(len(t), m.end() + 800)
        out.append(t[m.end():i])
    return out


def template_facts(template: str | None, acknowledged: bool = False) -> list[dict]:
    """Facts about a chat template that change what a run measures:

    * ``think_forced_open`` -- the generation prompt itself opens ``<think>``
      (the reply starts inside a reasoning block: the server must split it,
      e.g. ``reasoning_format``, or the answer is buried in the chain);
    * ``think_switch_in_prompt`` -- the generation prompt opens/closes
      ``<think>`` depending on a switch (an empty ``<think></think>`` = the
      thinking-off form);
    * ``switch:<name>`` -- a template switch (``enable_thinking``,
      ``preserve_thinking`` ...) the request may set via
      ``chat_template_kwargs``, with the line that reads it;
    * ``history_reasoning`` -- the template drops or keeps
      ``reasoning_content`` in history (multi-turn replay).

    Level INFO when ``acknowledged`` (the registry note covers them), else
    WARN for the first three kinds (history is always INFO)."""
    if not template:
        return []
    t = _JINJA_COMMENT.sub("", str(template))
    lvl = "INFO" if acknowledged else "WARN"
    facts: list[dict] = []
    for blk in _gen_prompt_blocks(t):
        lits = [m.group(0) for m in _THINK_LIT.finditer(blk)]
        if not lits:
            continue
        switch = next((s for s in TEMPLATE_SWITCHES if s in blk), None)
        # a <think> literal under its own {% if %} inside the block is conditional
        cond = re.search(r"\{%-?\s*if\b", blk[:blk.find(lits[0])] if lits[0] in blk else blk)
        if switch or cond or any(_EMPTY_THINK.search(x) for x in lits):
            facts.append({"id": "think_switch_in_prompt", "level": lvl,
                          "detail": f"the generation prompt opens/closes <think> on "
                                    f"{switch or 'a condition'}: {lits[0][:80]}"})
        else:
            facts.append({"id": "think_forced_open", "level": lvl,
                          "detail": f"the generation prompt always opens <think>: {lits[0][:80]}"})
        break
    lines = t.splitlines()
    for s in TEMPLATE_SWITCHES:
        hit = next((ln.strip() for ln in lines if re.search(rf"\b{s}\b", ln)), None)
        if hit:
            facts.append({"id": f"switch:{s}", "level": lvl,
                          "detail": f"template switch {s} (chat_template_kwargs): {hit[:120]}"})
    if "reasoning_content" in t:
        facts.append({"id": "history_reasoning", "level": "INFO",
                      "detail": "the template reads reasoning_content (what history keeps of a "
                                "past turn's reasoning)"})
    return facts


def format_facts(facts: list[dict]) -> list[str]:
    return [f"  {f['level']:4} template {f['id']}: {f['detail']}" for f in facts]


# ------------------------------------------------------------ plan side

def rig_templates(prov) -> dict[str, str]:
    """model_id -> chat template, from the rig listing (GGUF metadata);
    empty when unreadable (facts are information, never a reason to stop)."""
    try:
        from . import studioforge
        code, data = studioforge._mgmt("GET", prov.base_url, prov.api_key,
                                       prov.mgmt_headers() if hasattr(prov, "mgmt_headers") else None,
                                       "/api/models", timeout=20)
    except Exception:  # noqa: BLE001
        return {}
    if code != 200:
        return {}
    recs = data if isinstance(data, list) else (data.get("models") or data.get("data") or []) \
        if isinstance(data, dict) else []
    out = {}
    for m in recs:
        tpl = ((m.get("meta") or {}).get("chat_template")) if isinstance(m, dict) else None
        if isinstance(m, dict) and m.get("id") and tpl:
            out[m["id"]] = tpl
    return out


def gate_plan(p: dict, cfg: dict, cfg_p: dict | None = None, templates: dict | None = None) -> dict:
    """Apply the gate to a bench-all plan in place: a model that fails the
    gate AND has generation work (standard cases, escalation, ladder, a new
    registration) is taken out of the generation phases; its pending rows
    may still be judged, and a model with nothing to generate is left alone.
    A new registration is still REGISTERED (so its entry can be completed)
    but not run. ``p["settings"]`` = {label: Verdict.to_dict()} for every candidate;
    ``p["settings_blocked"]`` = the blocked labels."""
    if cfg_p is None:
        from . import report
        try:
            cfg_p = report._profile_applied(cfg)[1]
        except Exception:  # noqa: BLE001 -- no profile: the registry alone
            cfg_p = cfg
    prov_type = {n: (pr or {}).get("type") for n, pr in (cfg.get("providers") or {}).items()}
    by_name = {m["name"]: m for m in cfg.get("models") or []}
    add = {a["name"]: a for a in p.get("add") or []}
    default_prov = next((n for n, t in prov_type.items() if t == "studioforge"), None)
    work = ({m for g in p.get("groups") or [] for m in g["models"]}
            | {e.get("model") for e in p.get("escalate") or []}
            | {lp.get("model") for lp in p.get("ladder") or []} | set(add))
    settings, blocked = {}, []
    for label in list(dict.fromkeys(list(p.get("candidates") or []) + list(add))):
        entry = by_name.get(label) or add.get(label)
        if entry is None:
            continue
        ptype = prov_type.get(entry.get("provider") or default_prov) if entry.get("provider") \
            else ("studioforge" if label in add else prov_type.get(default_prov))
        v = check(cfg_p, entry, ptype, (templates or {}).get(entry.get("model_id")),
                  registered=label not in add)
        settings[label] = v.to_dict()
        if not v.ok and label in work:
            # only GENERATION is refused: a complete model is left alone, and
            # rows already generated may still be judged (the sampler is
            # not the judge's business)
            blocked.append(label)
    if blocked:
        b = set(blocked)
        groups = []
        for g in p.get("groups") or []:
            ms = [m for m in g["models"] if m not in b]
            if ms:
                groups.append({**g, "models": ms})
        p["groups"] = groups
        p["escalate"] = [e for e in p.get("escalate") or [] if e.get("model") not in b]
        p["ladder"] = [lp for lp in p.get("ladder") or [] if lp.get("model") not in b]
        p["per_model"] = {k: v for k, v in (p.get("per_model") or {}).items() if k not in b}
    p["settings"] = settings
    p["settings_blocked"] = blocked
    return p


def plan_lines(p: dict) -> list[str]:
    L = []
    for label in p.get("settings_blocked") or []:
        v = (p.get("settings") or {}).get(label) or {}
        L.append(f"  ! NOT RUN {label}: needs recommended settings — {v.get('reason')}")
        L.append("    " + str(v.get("fix") or "").replace("\n", "\n    "))
    return L
