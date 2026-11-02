"""Model conformance: does the harness's request protocol work for THIS model?

The benchmark assumed one request protocol for every model (R1: "Request
protocol per provider/model is ASSUMED, never verified against the model's
behaviour or vendor docs").  When that assumption is wrong the board scores
the wrong thing: MiniMax lost its reasoning chain between turns and its
scores collapsed (1/10 recall with the chain dropped against 7/10 replayed),
and a retired model id that ``/v1/models`` still listed 404'd on every call.

``crucibleforge conform`` proves the protocol per model with a handful of
cheap calls that go THROUGH THE HARNESS'S OWN REQUEST PATH --
``Provider.chat`` and the runner's message builders
(``runner.assistant_turn``, ``runner._assistant_tool_turn``).  A hand-rolled
request would prove nothing: the point is to exercise the exact bytes and the
exact conversation the bench sends.

Checks (each PASS / WARN / FAIL with a one-line reason):

- **c1 served**    -- a 1-turn call succeeds; the ``model`` the API reports is
  recorded (alias detection); a 4xx is a FAIL.
- **c2 reasoning** -- on a small multi-step reasoning prompt: where the
  reasoning arrives (``reasoning_content`` field, inline ``<think>``, none);
  after the harness parses it the answer carries no think tags.  A thinking
  model whose reasoning was detected is a PASS; think tags leaking into the
  answer are a FAIL.
- **c3 chain**     -- a 2-turn memory probe whose turn-1 fact exists ONLY in
  the reasoning ("pick a random 6-digit number, do not write it; reply only
  OK" then "which number did you pick?"), 3 trials, with the provider's
  CURRENT replay setting and with the opposite one.  If the opposite recalls
  clearly better (>= 2 more of 3) the replay setting is wrong for this model
  -- that is exactly the MiniMax defect.  A non-thinking model is skipped
  (INFO): there is no chain to replay.
- **c4 tools**     -- a 2-step tool round trip built by the runner's tool-loop
  message builder: call -> tool result -> final answer.  Any 4xx or a
  malformed history is a FAIL.
- **c5 sampler**   -- the sampler the profile would send for this model is
  accepted (no 4xx); a parameter the API says it ignores is noted.
- **c6 budget**    -- one medium coding prompt at the board budget: an empty
  answer with ``finish=length`` is a WARN ("needs the escalation rung; see
  Room").

The result is written to ``results/conformance/<label>.json``::

    {"ts": ..., "suite_revision": ..., "protocol": {...}, "checks": [...],
     "verdict": "PASS"}

``protocol`` is the fingerprint of the request protocol the checks ran under
(provider type, reasoning replay, extra_body keys, sampler).  Part 3 stamps
it on every row so a harness change can tell which rows are stale.

Rig models (StudioForge / LM Studio) are only checked when the model is
already loaded: ``conform`` never loads a model behind the bench's back.
Outside a run such a model is reported as SKIP unless ``--allow-load`` is
passed.
"""
from __future__ import annotations

import copy
import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from . import runner
from .api import ChatResult, RequestRejected, TransportError, WrongModelError
from .config import results_dir
from .providers import Provider, provider_for

log = logging.getLogger(__name__)

#: a check's verdict
PASS = "PASS"
WARN = "WARN"
FAIL = "FAIL"
INFO = "INFO"
#: the model's overall verdict
SKIP = "SKIP"

#: the checks, in the order they run
CHECK_IDS = ("c1", "c2", "c3", "c4", "c5", "c6")

#: a think-ish marker left in an answer: the harness parsed the reasoning but
#: did not recognise the dialect, so the tags leaked into the answer.  The
#: KNOWN dialects are stripped at the API boundary (api.split_thinking), so a
#: hit here is a dialect nobody parses -- exactly the gemma4 case of
#: 2026-10-24, where three models scored 0.  The pattern is deliberately
#: broad: the point is to catch what the parser does NOT know.
_LEAK_RE = re.compile(
    r"<\s*/?\s*(?:think|thinking|thought|reasoning)\b[^>]*>"
    r"|<\|\s*/?\s*(?:think|thinking|thought|reasoning|chain|scratchpad)\b[^|]*\|>"
    r"|\(\s*think\s*\)|\[/?think\]",
    re.IGNORECASE)

#: a 6-digit number (the c3 probe's hidden fact)
_NUM_RE = re.compile(r"(?<!\d)\d{6}(?!\d)")

#: c3's two turns.  The fact must exist ONLY in the reasoning: the model is
#: told not to write it, and a trial whose ANSWER contains the number is
#: discarded (the fact would then live in the content channel, where the
#: replay does not matter).
_CHAIN_TURN1 = ("Pick a random 6-digit number (digits only, no spaces) and "
                "remember it. Do not write the number anywhere in your reply -- "
                "only think about it. Reply with exactly: OK")
_CHAIN_TURN2 = "What number did you pick? Reply with only the 6 digits."

#: c2's probe: small, multi-step, and answerable in a sentence
_REASONING_PROMPT = ("A train leaves at 09:15 and arrives 4 hours 20 minutes "
                     "later. What time does it arrive? Work it out step by "
                     "step, then give the final time.")

#: c6's probe: one medium coding prompt
_CODING_PROMPT = (
    "Write a Python function `merge_intervals(intervals)` that takes a list of "
    "[start, end] pairs and returns the merged, non-overlapping intervals, "
    "sorted by start. Return only the code, in one ```python block.")

#: the budget the probes (c2/c3/c4) send: enough for a thinking model to
#: think a number out, small enough to stay a "cheap call". The board budget
#: itself is c6's job.
PROBE_BUDGET = 4096


@dataclass
class Check:
    """One check's verdict."""
    id: str
    status: str
    detail: str
    #: extra machine-readable facts the check learned (c1: the model the API
    #: reported serving the request)
    data: dict | None = None

    def to_dict(self) -> dict:
        d = {"id": self.id, "status": self.status, "detail": self.detail}
        if self.data:
            d["data"] = self.data
        return d


@dataclass
class Record:
    """The conformance record of one model."""
    label: str
    model_id: str
    provider: str
    provider_type: str
    ts: str
    suite_revision: str
    protocol: dict
    checks: list
    verdict: str = PASS
    served_model: str | None = None
    note: str | None = None

    def to_dict(self) -> dict:
        d = {"ts": self.ts, "suite_revision": self.suite_revision,
             "model_label": self.label, "model_id": self.model_id,
             "provider": self.provider, "provider_type": self.provider_type,
             "protocol": self.protocol,
             "checks": [c.to_dict() for c in self.checks],
             "verdict": self.verdict}
        if self.served_model:
            d["served_model"] = self.served_model
        if self.note:
            d["note"] = self.note
        return d


# ------------------------------------------------------------- plumbing

def with_profile(cfg: dict, profile: str | None = None) -> dict:
    """``cfg`` with the benchmark profile applied -- the budgets, the sampler
    rules and the regime a run would measure under.  The conformance checks
    must see the same profile the bench sends."""
    from .profiles import DEFAULT_PROFILE, apply_profile, load_profile
    prof = load_profile(profile or DEFAULT_PROFILE, cfg)
    cfg2, _cases = apply_profile(prof, cfg)
    # the profile's per-category budgets (the board budget a coding row gets)
    cfg2["_profile_max_tokens"] = dict(prof.get("max_tokens") or {})
    return cfg2


def record_path(label: str) -> Path:
    """Where a model's conformance record lives."""
    return results_dir() / "conformance" / f"{label}.json"


def write_record(rec: Record) -> Path:
    """Write the record to ``results/conformance/<label>.json``."""
    path = record_path(rec.label)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rec.to_dict(), indent=2), encoding="utf-8")
    return path


def read_record(label: str) -> dict | None:
    """A stored conformance record, or None."""
    path = record_path(label)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _ctx_len(cfg: dict, entry: dict) -> int:
    return int(entry.get("context_length")
               or (cfg.get("defaults") or {}).get("context_length") or 32768)


def _make_ctx(cfg: dict, entry: dict, provider: Provider):
    """The runner's own per-model context: the extra_body, the sampler and
    the budget rules the bench would use."""
    return runner._Ctx(cfg, entry, provider, _ctx_len(cfg, entry))


def protocol_fingerprint(cfg: dict, entry: dict, provider: Provider,
                         ctx=None) -> dict:
    """The request protocol a model is measured under.

    Everything that changes the bytes on the wire or the shape of the
    conversation: the provider type, whether the reasoning chain is replayed,
    the extra request fields, and the sampler.  A harness change that moves
    any of these changes the fingerprint, which is how a row can be told
    stale from one that is merely old."""
    ctx = ctx if ctx is not None else _make_ctx(cfg, entry, provider)
    extra = set(provider.extra_body or {}) | set(ctx.extra_body or {})
    if provider.reasoning_replay:
        # Provider.chat adds this one itself when the replay is on
        extra.add("reasoning_split")
    if ctx.regime == runner.REGIME_DEPLOYED:
        sampler = {"source": ctx.sampler_source, "params": dict(ctx.sampler or {})}
    else:
        sampler = {"source": "case", "params": {}}
    return {"provider_type": provider.type,
            "reasoning_replay": bool(provider.reasoning_replay),
            "extra_body_keys": sorted(extra),
            "sampler": sampler}


def _rig_resident(provider: Provider, model_id: str) -> bool:
    """True when a rig model is loaded right now (so it can be checked
    without loading anything)."""
    if provider.type == "studioforge":
        from . import studioforge
        try:
            plan = studioforge.loaded_plan(model_id, provider.base_url,
                                           provider.api_key, provider.mgmt_headers())
        except Exception:  # noqa: BLE001 — an unreadable status changes nothing
            return False
        return bool(plan) and plan.get("state") == "ready"
    if provider.type == "lmstudio":
        from . import lms
        try:
            return bool(lms.is_loaded(model_id))
        except Exception:  # noqa: BLE001
            return False
    return True


def _ask(ctx, messages: list[dict], *, max_tokens: int,
         temperature: float | None = None, top_p: float | None = None,
         tools: list | None = None) -> ChatResult:
    """One completion through the harness's own request path.

    ``Provider.chat`` is the bench's request path: it merges the provider's
    and the model's ``extra_body``, adds ``reasoning_split`` when the
    reasoning replay is on, and streams the answer.  The recovery pass is
    deliberately NOT used here -- a probe must see what the model really
    answered, not a re-ask of it.

    ``temperature`` / ``top_p`` are sent EXPLICITLY, ``None`` included: the
    bench sends nothing when the profile pins no sampler (the "as deployed"
    regime), and a probe must look like the bench.  The default is the
    model's OWN sampler (ctx.sampling), so a probe is the request the bench
    would send for this model.
    """
    if temperature is None and top_p is None:
        temperature, top_p = ctx.sampling({})
    kw = {"max_tokens": max_tokens, "seed": 42, "extra_body": ctx.extra_body,
          "temperature": temperature, "top_p": top_p}
    if tools:
        kw["tools"] = tools
    return ctx.provider.chat(ctx.model_id, messages, **kw)


# ------------------------------------------------------------- the checks

def _c1_served(ctx) -> Check:
    """A 1-turn call succeeds, and the model the API reports is recorded."""
    msgs = [{"role": "user", "content": "Reply with the single word: ready."}]
    try:
        r = _ask(ctx, msgs, max_tokens=64)
    except RequestRejected as e:
        return Check("c1", FAIL, f"the API rejected the call (HTTP {e.status}): "
                                  f"{str(e.body)[:160]}")
    except WrongModelError as e:
        return Check("c1", FAIL, f"the server answered from another model: "
                                  f"{str(e)[:160]}")
    except TransportError as e:
        return Check("c1", FAIL, f"the call failed: {str(e)[:200]}")
    served = r.served_model
    if not served:
        return Check("c1", WARN, "the API answered but did not name the model "
                                  "that served it")
    if served != ctx.model_id:
        return Check("c1", WARN, f"alias: requested {ctx.model_id!r}, the API "
                                  f"reports {served!r}",
                     {"served_model": served})
    return Check("c1", PASS, f"served by {served!r}", {"served_model": served})


def _c2_reasoning(ctx) -> Check:
    """Where the reasoning arrives, and whether the harness parsed it."""
    msgs = [{"role": "user", "content": _REASONING_PROMPT}]
    try:
        r = _ask(ctx, msgs, max_tokens=PROBE_BUDGET)
    except RequestRejected as e:
        return Check("c2", FAIL, f"the API rejected the reasoning probe "
                                  f"(HTTP {e.status}): {str(e.body)[:160]}")
    except TransportError as e:
        return Check("c2", FAIL, f"the reasoning probe failed: {str(e)[:200]}")
    # The text that can reach a grader: the content channel, and the
    # reasoning channel when the answer arrived there (api.ChatResult.
    # scoreable_text falls back to it).  A KNOWN dialect is already stripped
    # at the API boundary, so anything left is a dialect the harness does not
    # parse -- the tags would then be graded as the reply.
    for channel, text in (("the answer", r.response_text or ""),
                          ("the reasoning", r.reasoning_text or "")):
        leak = _LEAK_RE.search(text)
        if leak:
            return Check("c2", FAIL, f"think tags leaked into {channel}: "
                                      f"{leak.group(0)!r} (the harness does not "
                                      f"parse this dialect)")
    if not (r.reasoning_text or "").strip() and not r.reasoning_tokens:
        return Check("c2", INFO, "no reasoning channel on a reasoning prompt "
                                  "-- the model answers directly")
    where = ("the reasoning_content field" if r.reasoning_source == "channel"
             else "inline <think> tags" if r.reasoning_source == "inline"
             else "an unknown channel")
    return Check("c2", PASS, f"reasoning arrived in {where}; the answer carries "
                             f"no think tags")


def _chain_arm(ctx, replay: bool, trials: int = 3) -> dict:
    """One arm of the c3 probe: ``trials`` two-turn conversations whose
    turn-1 fact lives only in the reasoning.

    ``replay`` says whether the assistant turn goes back WITH its reasoning
    (the MiniMax protocol).  Returns the recall count, how many trials were
    conclusive, how many could prove nothing, and the first error."""
    recalled = conclusive = errors = 0
    # trials that could prove nothing, by cause: the model did not think at
    # all, or it wrote the number down (the fact would then live in the
    # content channel, where the replay does not matter)
    no_reasoning = wrote_down = 0
    first_error: str | None = None
    for _ in range(trials):
        try:
            r1 = _ask(ctx, [{"role": "user", "content": _CHAIN_TURN1}],
                      max_tokens=PROBE_BUDGET)
        except TransportError as e:
            errors += 1
            first_error = first_error or f"{type(e).__name__}: {str(e)[:160]}"
            continue
        numbers = set(_NUM_RE.findall(r1.reasoning_text or ""))
        if not numbers:
            # no reasoning channel (or no number in it): the fact cannot be
            # remembered, so the trial proves nothing
            no_reasoning += 1
            continue
        if _NUM_RE.search(r1.response_text or ""):
            # the model wrote the number down: the fact is in the content
            # channel, where the replay does not matter
            wrote_down += 1
            continue
        history = [{"role": "user", "content": _CHAIN_TURN1},
                   runner.assistant_turn(r1, replay),
                   {"role": "user", "content": _CHAIN_TURN2}]
        try:
            r2 = _ask(ctx, history, max_tokens=PROBE_BUDGET)
        except TransportError as e:
            errors += 1
            first_error = first_error or f"{type(e).__name__}: {str(e)[:160]}"
            continue
        conclusive += 1
        said = re.sub(r"\D", "", r2.response_text or "")
        if not said:
            said = re.sub(r"\D", "", r2.reasoning_text or "")
        # a recall is the number itself coming back, not a coincidence: the
        # reply is a short digit string, so a stray 6-digit number in the
        # reasoning (a date, an id) cannot pass for the answer
        if len(said) <= 12 and any(n in said for n in numbers):
            recalled += 1
    return {"recalled": recalled, "conclusive": conclusive,
            "no_reasoning": no_reasoning, "wrote_down": wrote_down,
            "errors": errors, "error": first_error}


def _opposite_provider(provider: Provider, replay: bool) -> Provider:
    """The same provider with the reasoning replay knob flipped.

    The provider is copied, never mutated: the bench's own provider object
    must keep the setting the registry gave it.  ``Provider`` is a
    dataclass, but a shallow copy is used so that any provider-like object
    works (the copy shares the server, the key and the headers -- only the
    knob differs).
    """
    flipped = copy.copy(provider)
    flipped.reasoning_replay = replay
    return flipped


def _c3_chain(cfg, entry, provider, ctx) -> Check:
    """The 2-turn memory probe, with the current replay setting and with the
    opposite one."""
    current = bool(provider.reasoning_replay)
    # the opposite setting: the same provider with the knob flipped
    flipped = _opposite_provider(provider, not current)
    try:
        opp_ctx = _make_ctx(cfg, entry, flipped)
    except Exception as e:  # noqa: BLE001 — a broken config must not crash the run
        return Check("c3", WARN, f"the opposite replay setting could not be "
                                  f"prepared: {str(e)[:160]}")
    cur = _chain_arm(ctx, current)
    opp = _chain_arm(opp_ctx, not current)
    used = "the opposite setting"
    if opp["errors"] and not opp["conclusive"]:
        # the API refused the opposite configuration (MiniMax M3.1-Flash 400s
        # without reasoning_split): fall back to comparing the REPLAY itself,
        # with the request bytes unchanged.
        opp = _chain_arm(ctx, not current)
        used = "the opposite replay only (the API rejected the opposite body)"
    if not cur["conclusive"] and not opp["conclusive"]:
        if cur["errors"] or opp["errors"]:
            return Check("c3", WARN, f"the memory probe could not run: "
                                      f"{cur['error'] or opp['error']}")
        if cur["wrote_down"] or opp["wrote_down"]:
            # the model thought, but wrote the number down (the fact would
            # then live in the content channel, where the replay is moot)
            return Check("c3", INFO, "the model wrote the number down instead "
                                      "of only thinking it: the probe could "
                                      "not separate the two settings")
        return Check("c3", INFO, "no reasoning channel: the model is not a "
                                  "thinking model, there is no chain to replay")
    if cur["conclusive"] < 2 or opp["conclusive"] < 2:
        return Check("c3", WARN, f"too few conclusive trials to judge the "
                                  f"replay setting (current {cur['recalled']}/"
                                  f"{cur['conclusive']}, opposite "
                                  f"{opp['recalled']}/{opp['conclusive']})")
    if opp["recalled"] >= cur["recalled"] + 2:
        return Check("c3", FAIL,
                     f"reasoning replay setting is wrong for this model: "
                     f"{used} recalled {opp['recalled']}/{opp['conclusive']} "
                     f"against {cur['recalled']}/{cur['conclusive']} with the "
                     f"current setting")
    return Check("c3", PASS,
                 f"the current replay setting holds: {cur['recalled']}/"
                 f"{cur['conclusive']} recalled against "
                 f"{opp['recalled']}/{opp['conclusive']} with {used}")


def _c4_tools(ctx) -> Check:
    """A 2-step tool round trip through the runner's tool-loop builder."""
    tools = [{"type": "function",
              "function": {"name": "get_weather",
                           "description": "The current weather in a city",
                           "parameters": {"type": "object",
                                          "properties": {"city": {"type": "string"}},
                                          "required": ["city"]}}}]
    payload = '{"city": "Oslo", "temp_c": 7, "sky": "rain"}'
    msgs = [{"role": "user",
             "content": "What is the weather in Oslo? Use the get_weather tool."}]
    try:
        r1 = _ask(ctx, msgs, max_tokens=PROBE_BUDGET, tools=tools)
    except RequestRejected as e:
        return Check("c4", FAIL, f"the API rejected the tool request "
                                  f"(HTTP {e.status}): {str(e.body)[:160]}")
    except TransportError as e:
        return Check("c4", FAIL, f"the tool request failed: {str(e)[:200]}")
    calls = r1.tool_calls or []
    if not calls:
        return Check("c4", WARN, "the model did not call the tool -- the round "
                                  "trip could not be exercised")
    msg, ids = runner._assistant_tool_turn(r1, calls)
    follow = list(msgs) + [msg]
    for cid in ids:
        follow.append({"role": "tool", "tool_call_id": cid, "content": payload})
    # the history must be well formed: every call answered by a tool message
    answered = {m.get("tool_call_id") for m in follow if m["role"] == "tool"}
    wanted = {c.get("id") for c in msg.get("tool_calls") or []}
    if not wanted <= answered:
        return Check("c4", FAIL, f"malformed history: tool calls "
                                  f"{sorted(str(x) for x in wanted)} are not "
                                  f"all answered ({sorted(str(x) for x in answered)})")
    try:
        r2 = _ask(ctx, follow, max_tokens=PROBE_BUDGET, tools=tools)
    except RequestRejected as e:
        return Check("c4", FAIL, f"the API rejected the tool RESULT turn "
                                  f"(HTTP {e.status}): {str(e.body)[:160]}")
    except TransportError as e:
        return Check("c4", FAIL, f"the tool result turn failed: {str(e)[:200]}")
    if not (r2.response_text or "").strip():
        return Check("c4", FAIL, "the model answered nothing after the tool "
                                  "result")
    return Check("c4", PASS, f"tool call -> result -> answer in "
                             f"{len(ids)} step(s), the history was accepted")


#: how an API says that a parameter is ignored
_IGNORE_WORDS = ("ignor", "not supported", "unsupported", "not applicable",
                 "no effect", "has no effect", "is not used")


def _ignored_params(text: str, params: dict) -> str:
    """The sampler parameters the API says it ignores, if it says so.

    The statement has to name the parameter: a reply that happens to contain
    the word "ignored" is not evidence about the sampler.
    """
    sentences = re.split(r"[.\n]", text or "")
    hit: list[str] = []
    for sentence in sentences:
        low = sentence.lower()
        if not any(w in low for w in _IGNORE_WORDS):
            continue
        for k in params:
            if k.lower() in low and k not in hit:
                hit.append(k)
    return ", ".join(hit)


def _c5_sampler(ctx) -> Check:
    """The sampler the profile would send for this model is accepted."""
    params = dict(ctx.sampler or {})
    temp, top_p = ctx.sampling({})
    sent = []
    if temp is not None:
        sent.append(f"temperature={temp}")
    if top_p is not None:
        sent.append(f"top_p={top_p}")
    for k in runner.SAMPLER_BODY_KEYS:
        if params.get(k) is not None:
            sent.append(f"{k}={params[k]}")
    try:
        r = _ask(ctx, [{"role": "user", "content": "Reply with the single word: ready."}],
                 max_tokens=64, temperature=temp, top_p=top_p)
    except RequestRejected as e:
        named = [p.split("=")[0] for p in sent
                 if p.split("=")[0] in str(e.body)]
        return Check("c5", FAIL, f"the API rejected the sampler "
                                  f"({', '.join(sent) or 'none'}): "
                                  f"HTTP {e.status}: {str(e.body)[:160]}"
                                  + (f" [rejected: {', '.join(named)}]" if named else ""))
    except TransportError as e:
        return Check("c5", FAIL, f"the sampler probe failed: {str(e)[:200]}")
    detail = (f"accepted: {', '.join(sent) or 'no sampler parameters sent'} "
              f"(source: {ctx.sampler_source})")
    ignored = _ignored_params(r.response_text or "", params)
    if ignored:
        return Check("c5", WARN, detail + f"; the API says it ignores {ignored}")
    return Check("c5", PASS, detail)


def _c6_budget(cfg, ctx) -> Check:
    """One medium coding prompt at the board budget."""
    case_mt = int((cfg.get("_profile_max_tokens") or {}).get("coding", 6144))
    budget = ctx.budget(case_mt)
    try:
        r = _ask(ctx, [{"role": "user", "content": _CODING_PROMPT}],
                 max_tokens=budget)
    except RequestRejected as e:
        return Check("c6", FAIL, f"the API rejected the coding prompt "
                                  f"(HTTP {e.status}): {str(e.body)[:160]}")
    except TransportError as e:
        return Check("c6", FAIL, f"the coding prompt failed: {str(e)[:200]}")
    if (r.response_text or "").strip():
        return Check("c6", PASS, f"answered within the board budget "
                                 f"({budget} tokens)")
    if r.finish_reason == "length":
        rung = ((cfg.get("defaults") or {}).get("escalation_rungs") or [None])[0]
        return Check("c6", WARN, f"empty answer at the board budget ({budget} "
                                 f"tokens, finish=length) -- needs the escalation "
                                 f"rung{(' ' + str(rung)) if rung else ''}; see Room")
    return Check("c6", FAIL, f"empty answer (finish={r.finish_reason}) at the "
                             f"board budget ({budget} tokens)")


# ------------------------------------------------------------- the run

def run_model(cfg: dict, entry: dict, *, allow_load: bool = False) -> Record:
    """Run the conformance checks for one model and return its record."""
    from .version import revision
    label = str(entry.get("name") or entry.get("model_id") or "")
    provider = provider_for(cfg, entry)
    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    ctx = _make_ctx(cfg, entry, provider)
    protocol = protocol_fingerprint(cfg, entry, provider, ctx)
    rec = Record(label=label, model_id=entry.get("model_id") or "",
                 provider=provider.name, provider_type=provider.type,
                 ts=ts, suite_revision=revision(cfg), protocol=protocol,
                 checks=[])
    if provider.type in ("studioforge", "lmstudio"):
        if not allow_load and not _rig_resident(provider, entry["model_id"]):
            rec.verdict = SKIP
            rec.note = (f"{provider.type} model is not loaded: a rig model is "
                        f"only checked while the bench has it loaded (or with "
                        f"--allow-load)")
            return rec
        if allow_load:
            provider.switch_model(entry["model_id"], _ctx_len(cfg, entry))
    checks = (
        ("c1", lambda: _c1_served(ctx)),
        ("c2", lambda: _c2_reasoning(ctx)),
        ("c3", lambda: _c3_chain(cfg, entry, provider, ctx)),
        ("c4", lambda: _c4_tools(ctx)),
        ("c5", lambda: _c5_sampler(ctx)),
        ("c6", lambda: _c6_budget(cfg, ctx)),
    )
    for cid, fn in checks:
        try:
            rec.checks.append(fn())
        except Exception as e:  # noqa: BLE001 — one broken check must not
            # take the whole model down
            log.warning("conformance check %s failed for %s: %s", cid, label, e)
            rec.checks.append(Check(cid, WARN, f"the check could not run: "
                                                f"{type(e).__name__}: {str(e)[:160]}"))
    statuses = {c.status for c in rec.checks}
    if FAIL in statuses:
        rec.verdict = FAIL
    elif WARN in statuses:
        rec.verdict = WARN
    else:
        rec.verdict = PASS
    c1 = next((c for c in rec.checks if c.id == "c1"), None)
    if c1 is not None and c1.data:
        rec.served_model = c1.data.get("served_model")
    return rec


def conform(cfg: dict, entries: list[dict], *, allow_load: bool = False,
            write: bool = True) -> list[Record]:
    """Run the conformance checks for every selected model."""
    records = []
    for entry in entries:
        log.info("conformance: %s (%s) via %s", entry.get("name"),
                 entry.get("model_id"), entry.get("provider"))
        rec = run_model(cfg, entry, allow_load=allow_load)
        if write:
            path = write_record(rec)
            log.info("conformance: %s -> %s (%s)", rec.label, path, rec.verdict)
        records.append(rec)
    return records
