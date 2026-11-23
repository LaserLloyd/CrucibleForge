"""Per-row settings disclosure (fix round 2026-11-18, P2; DECISIONS-45 D11, review finding 12).

Every row the runner writes, for hosted and rig models alike, records what the measurement ran under, under
these exact names (the board reads them; an old row without them reads "not recorded"):

* ``thinking``: ``true`` when the row carried reasoning (tokens or text) or the run settled thinking on,
  ``false`` when the run settled it off and the row has none, else ``"unknown"``;
* ``effort``: the reasoning effort the request actually sent (``reasoning_effort``, ``reasoning.effort``, an
  Anthropic ``thinking.budget_tokens`` as ``"budget:<n>"``, a ``thinking.type``/``enable_thinking`` switch as
  ``"thinking:<value>"``, the Claude Code ``--effort``), or the string ``"provider default"`` when none was sent;
* ``reasoning_tokens``: the server-reported reasoning tokens (provider usage), else ``null`` (an estimate is
  not the provider's number);
* ``finish_reason``: the one canonical field (the runner already writes it; kept, never duplicated);
* ``cut``: the reply ended at the token ceiling (finish_reason ``length``/``max_tokens``, or a single reply's
  completion tokens >= the budget sent).

A hosted row also carries ``settings_note`` naming what the registry could not verify (:func:`settings_note`).
The publish gate (:func:`publish_refusal`) refuses a HOSTED row whose ``effort`` is missing.
"""
from __future__ import annotations

PROVIDER_DEFAULT = "provider default"
FIELDS = ("thinking", "effort", "reasoning_tokens", "finish_reason", "cut")
CUT_REASONS = ("length", "max_tokens")
#: provider types on hardware we own (spend.LOCAL_TYPES plus the Claude Code route is NOT local)
LOCAL_TYPES = frozenset({"studioforge", "lmstudio", "llamacpp", "llama.cpp", "ollama", "vllm", "local"})


def is_hosted(provider_type: str | None) -> bool:
    return str(provider_type or "openai").lower() not in LOCAL_TYPES


def effort_sent(extra_body: dict | None, cc_effort: str | None = None) -> str:
    """The reasoning effort a request body carries, as one string, or ``"provider default"``."""
    if cc_effort:
        return str(cc_effort)
    eb = extra_body or {}
    if eb.get("reasoning_effort") not in (None, ""):
        return str(eb["reasoning_effort"])
    r = eb.get("reasoning")
    if isinstance(r, dict) and r.get("effort") not in (None, ""):
        return str(r["effort"])
    t = eb.get("thinking")
    if isinstance(t, dict):
        if t.get("budget_tokens") is not None:
            return f"budget:{int(t['budget_tokens'])}"
        if t.get("type"):
            return f"thinking:{t['type']}"
    ctk = eb.get("chat_template_kwargs")
    if isinstance(ctk, dict) and "enable_thinking" in ctk:
        return f"thinking:{'enabled' if ctk['enable_thinking'] else 'disabled'}"
    return PROVIDER_DEFAULT


def _thinking(row: dict, settled) -> bool | str:
    m = row.get("metrics") or {}
    if (m.get("reasoning_tokens") or 0) > 0 or str(row.get("reasoning") or "").strip():
        return True
    if settled is True:
        return True
    if settled is False:
        return False
    return "unknown"


def _cut(row: dict) -> bool:
    if str(row.get("finish_reason") or "") in CUT_REASONS:
        return True
    m = row.get("metrics") or {}
    sent = row.get("max_tokens_sent")
    comp = m.get("completion_tokens")
    single = int(m.get("n_turns") or 1) <= 1
    return bool(single and sent and comp is not None and int(comp) >= int(sent))


def row_fields(row: dict, *, settled_thinking=None, extra_body: dict | None = None,
               cc_effort: str | None = None) -> dict:
    """The five disclosure fields of ``row`` (a row as the runner built it)."""
    m = row.get("metrics") or {}
    rt = m.get("reasoning_tokens") if m.get("reasoning_tokens_source") == "server" else None
    return {"thinking": _thinking(row, settled_thinking),
            "effort": effort_sent(extra_body, cc_effort),
            "reasoning_tokens": rt,
            "finish_reason": row.get("finish_reason"),
            "cut": _cut(row)}


def settings_note(entry: dict, provider_type: str | None, extra_body: dict | None = None) -> str | None:
    """What the registry cannot verify about a HOSTED model's settings (None for a rig model, whose settings
    settings_gate checks, and for a hosted entry that declares both its thinking mode and an effort)."""
    if not is_hosted(provider_type):
        return None
    missing = []
    if entry.get("thinking", "auto") not in (True, False):
        missing.append("thinking mode (models.yaml `thinking` is auto: read from the replies)")
    if effort_sent(extra_body if extra_body is not None else entry.get("extra_body"),
                   entry.get("effort") if entry.get("provider") == "claude_code" else None) == PROVIDER_DEFAULT:
        missing.append("reasoning effort (none sent: the vendor's default, which the vendor may change)")
    if not missing:
        return None
    return "not verifiable from the registry: " + "; ".join(missing)


def hosted_settings_verdict(entry: dict, provider_type: str | None) -> tuple[str, str]:
    """settings_gate's hosted branch: ("api-checked", "") when the registry declares the thinking mode and the
    effort, else ("api", <settings_note>). A hosted model is never refused on this (the vendor fixes the
    sampler); what was not verifiable rides on every row as ``settings_note``."""
    note = settings_note(entry, provider_type)
    return ("api", note) if note else ("api-checked", "")


def publish_refusal(row: dict, provider_type: str | None) -> str | None:
    """Why ``row`` may not be published, or None. A HOSTED row must record the effort it ran at (D11)."""
    if is_hosted(provider_type) and not row.get("effort"):
        return (f"{row.get('case_id')}: hosted row without a recorded reasoning effort (D11: thinking, effort, "
                "reasoning tokens and cuts are disclosed on every hosted row) - re-run it or keep it off the board")
    return None
