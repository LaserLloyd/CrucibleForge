"""``crucibleforge card-check <label | hf repo>`` (2026-11-13).

Reads a model's Hugging Face card(s) and chat template and PROPOSES what the
settings gate (settings_gate) needs: candidate recommended-sampler lines, the
maker's ``generation_config.json`` values, and the template facts. It never
writes anything -- a person (or an agent) reads the proposal, checks the
card, and writes the rule / registry entry.

The repo chain: a rig model id ``<publisher>/<repo>/<file>`` names the GGUF
repo ``<publisher>/<repo>``; its card's ``base_model:`` front matter leads to
the original (followed up to three hops), where the sampler recommendation
usually lives.
"""
from __future__ import annotations

import http.client
import json
import os
import re
import urllib.error
import urllib.request

HF = "https://huggingface.co"
TIMEOUT_S = 20
MAX_HOPS = 3

#: sampler words a card uses (README prose, tables, code)
_SAMPLER_RE = re.compile(
    r"\b(temperature|temp|top[_\- ]?p|top[_\- ]?k|min[_\- ]?p|presence[_\- ]?penalty|"
    r"repetition[_\- ]?penalty|repeat[_\- ]?penalty|frequency[_\- ]?penalty|dry[_\- ]?multiplier)\b"
    r"[^0-9\n]{0,24}?(-?\d+(?:\.\d+)?)", re.I)
_KEY = {"temp": "temperature", "temperature": "temperature", "topp": "top_p", "topk": "top_k",
        "minp": "min_p", "presencepenalty": "presence_penalty",
        "repetitionpenalty": "repeat_penalty", "repeatpenalty": "repeat_penalty",
        "frequencypenalty": "frequency_penalty", "drymultiplier": "dry_multiplier"}


def _get(url: str) -> str | None:
    """GET a public HF file as text; None when missing / gated / offline."""
    req = urllib.request.Request(url, headers={"User-Agent": "crucibleforge-card-check"})
    tok = os.environ.get("HF_TOKEN")
    if tok:
        # never forwarded to a redirect target (HF redirects to its CDN)
        req.add_unredirected_header("Authorization", f"Bearer {tok}")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:  # noqa: S310 — fixed https host
            return r.read(4_000_000).decode("utf-8", "replace")
    except (urllib.error.URLError, http.client.HTTPException, OSError, ValueError):
        return None


def repo_of(cfg: dict | None, arg: str) -> tuple[str, str | None, dict | None]:
    """(hf repo, rig model id or None, registry entry or None) for a label,
    a rig model id or a repo."""
    entry = None
    for m in (cfg or {}).get("models") or []:
        if arg in (m.get("name"), m.get("model_id")):
            entry = m
            break
    mid = entry.get("model_id") if entry else (arg if arg.count("/") >= 2 else None)
    ref = mid or arg
    parts = ref.split("/")
    repo = "/".join(parts[:2]) if len(parts) >= 2 else ref
    return repo, mid, entry


def front_matter(readme: str) -> dict:
    if not readme or not readme.startswith("---"):
        return {}
    end = readme.find("\n---", 3)
    if end < 0:
        return {}
    try:
        import yaml
        d = yaml.safe_load(readme[3:end])
        return d if isinstance(d, dict) else {}
    except Exception:  # noqa: BLE001 — a broken card header is just no header
        return {}


def base_models(readme: str) -> list[str]:
    b = front_matter(readme).get("base_model")
    if isinstance(b, str):
        return [b]
    return [x for x in (b or []) if isinstance(x, str)]


def sampler_lines(readme: str) -> list[dict]:
    """Card lines that name sampler values: {"line", "params"} (params parsed
    best effort; the line is what a person reads)."""
    out, seen = [], set()
    cols: list[str | None] | None = None      # the sampler columns of the table being read
    for ln in (readme or "").splitlines():
        st = ln.strip()
        if st.startswith("|"):
            cells = [c.strip() for c in st.strip("|").split("|")]
            if cols is None:
                names = [_KEY.get(re.sub(r"[_\- ]", "", c.lower())) for c in cells]
                if any(names):
                    cols = names                  # a header naming sampler columns
                    continue
            elif all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c):
                continue                          # the |---| rule under the header
            else:
                params = {}
                for name, cell in zip(cols, cells):
                    m = re.fullmatch(r"-?\d+(?:\.\d+)?", cell.replace(",", ""))
                    if name and m:
                        v = float(m.group(0))
                        params[name] = int(v) if name == "top_k" else v
                if params:
                    text = st[:220]
                    if text not in seen:
                        seen.add(text)
                        out.append({"line": text, "params": params})
                continue
        else:
            cols = None
        hits = list(_SAMPLER_RE.finditer(ln))
        if not hits:
            continue
        params = {}
        for h in hits:
            k = _KEY.get(re.sub(r"[_\- ]", "", h.group(1).lower()))
            if k and k not in params:
                v = float(h.group(2))
                params[k] = int(v) if k == "top_k" else v
        text = ln.strip()[:220]
        if text in seen:
            continue
        seen.add(text)
        out.append({"line": text, "params": params})
    return out


def check(cfg: dict | None, arg: str, *, fetch=_get, rig_template: str | None = None) -> dict:
    """The proposal for ``arg`` (no writes). ``fetch(url)`` is injectable."""
    from . import settings_gate
    repo, mid, entry = repo_of(cfg, arg)
    chain, cards = [], []
    todo, hops = [repo], 0
    while todo and hops <= MAX_HOPS:
        r = todo.pop(0)
        if r in chain:
            continue
        chain.append(r)
        readme = fetch(f"{HF}/{r}/raw/main/README.md")
        gen = fetch(f"{HF}/{r}/raw/main/generation_config.json")
        tok = fetch(f"{HF}/{r}/raw/main/tokenizer_config.json")
        jin = fetch(f"{HF}/{r}/raw/main/chat_template.jinja")
        tpl = jin
        if not tpl and tok:
            try:
                ct = json.loads(tok).get("chat_template")
                tpl = ct if isinstance(ct, str) else (
                    next((x.get("template") for x in ct or [] if isinstance(x, dict)
                          and x.get("name") in (None, "default")), None))
            except (ValueError, AttributeError):
                tpl = None
        gen_vals = {}
        if gen:
            try:
                g = json.loads(gen)
                gen_vals = {k: g[k] for k in ("temperature", "top_p", "top_k", "min_p",
                                              "repetition_penalty", "presence_penalty", "do_sample")
                            if k in g}
            except ValueError:
                pass
        cards.append({"repo": r, "url": f"{HF}/{r}", "found": readme is not None,
                      "sampler_lines": sampler_lines(readme or ""),
                      "generation_config": gen_vals,
                      "template_facts": settings_gate.template_facts(tpl) if tpl else [],
                      "has_template": bool(tpl)})
        todo += [b for b in base_models(readme or "") if b not in chain]
        hops += 1
    out = {"arg": arg, "repo": repo, "model_id": mid, "label": (entry or {}).get("name"),
           "cards": cards,
           "rig_template_facts": settings_gate.template_facts(rig_template) if rig_template else [],
           "registry": {k: (entry or {}).get(k) for k in ("sampler", "settings_checked",
                                                          "extra_body", "thinking")
                        if (entry or {}).get(k) is not None}}
    if cfg and entry:
        try:
            from . import report
            from .runner import resolve_sampler
            out["current_sampler"] = list(resolve_sampler(report._profile_applied(cfg)[1], entry))
        except Exception:  # noqa: BLE001
            pass
    return out


def format_check(res: dict) -> str:
    L = [f"card-check {res['arg']}  (repo chain from {res['repo']})"]
    if res.get("current_sampler"):
        s, src = res["current_sampler"]
        L.append(f"  measured now with: {src} {s or '(server defaults)'}")
    if res.get("registry"):
        L.append(f"  registry: {json.dumps(res['registry'], ensure_ascii=False)}")
    any_lines = False
    for c in res["cards"]:
        L.append(f"\n  {c['url']}" + ("" if c["found"] else "  (no README readable)"))
        for s in c["sampler_lines"][:12]:
            any_lines = True
            L.append(f"    sampler? {s['line']}")
            if s["params"]:
                L.append(f"             -> {s['params']}")
        if c["generation_config"]:
            L.append(f"    generation_config.json: {c['generation_config']}")
        if c["has_template"]:
            L += ["  " + x for x in _facts(c["template_facts"]) or ["    template: no thinking facts"]]
    if res.get("rig_template_facts"):
        L.append("\n  chat template the RIG serves (GGUF):")
        L += ["  " + x for x in _facts(res["rig_template_facts"])]
    label = res.get("label") or "<label>"
    L.append("\n  proposal (a person decides; nothing was written):")
    def _gen_sampler(c):
        return {k: v for k, v in c["generation_config"].items() if k != "do_sample"}
    src_url = next((c["url"] for c in res["cards"] if c["sampler_lines"]),
                   next((c["url"] for c in res["cards"] if _gen_sampler(c)), ""))
    if not any(c["found"] for c in res["cards"]):
        L.append("    NOTHING proposed: no card could be read (offline, gated, or not an HF repo — "
                 "pass the rig model id or the HF repo). Read the card yourself before writing "
                 "settings_checked.")
    elif any_lines or any(_gen_sampler(c) for c in res["cards"]):
        L.append("    the card names sampler values: confirm the CHAT/general row and add a rule to")
        L.append("    profiles/bench.yaml recommended_samplers:")
        L.append(f"      - id: <family>\n        match: '<regex on the model id>'\n"
                 f"        sampler: {{temperature: …, top_p: …}}\n        source: {src_url}")
    else:
        L.append("    no sampler published on the cards read: add to the models.yaml entry of "
                 f"{label}:")
        L.append(f"      settings_checked:\n        sampler: none_published\n"
                 f"        source: {res['cards'][-1]['url'] if res['cards'] else 'https://huggingface.co/…'}\n"
                 f"        template: \"<from the template facts above>\"")
    return "\n".join(L) + "\n"


def _facts(facts: list[dict]) -> list[str]:
    from .settings_gate import format_facts
    return format_facts(facts)
