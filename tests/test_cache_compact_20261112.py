"""CACHE-COMPACT (MakoBench 4.5, 2026-11-12): prompt caching survives long
agentic cases. Under bench45 (``compaction: hysteresis``) codeenv.compact
stubs old tool results in ONE step (over 60% of the cap -> down to about 40%)
and then leaves the history alone until it has grown by the same 20% again, so
most requests are a byte-prefix extension of the previous one. bench (4.1)
keeps the per-turn "just enough" rule byte for byte. Fakes only: no API call."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest

from crucibleforge import anthropic_native, api, codeenv, profiles, runner, spend

ROOT = Path(__file__).resolve().parent.parent
PRICED = {"price": {"input": 1, "output": 2}}


def _case() -> dict:
    # LAG1: the long-tier case puppy-a8 measured the per-turn cache break on
    cases = json.loads((ROOT / "cases" / "codeagent_long.json").read_text(encoding="utf-8"))
    case = next(c for c in cases if c["id"] == "LAG1-potkeeper-yearend")
    return {**copy.deepcopy(case), "category": "codeagent"}


class _Ctx:
    """A fake model that keeps reading the project's files until the tool-call
    limit (60 calls on the long tier): an episode that crosses the 60% line."""
    ctx_len = None
    bank_budget = 0

    def __init__(self, entry, profile, files):
        self.entry = entry
        self.cfg = {"_profile": profile} if profile else {}
        self.files = files
        self.sent: list[str] = []
        self.keys: list = []
        self.n = 0

    def budget(self, n):
        return n

    def recover_for(self, case):
        return False

    def call(self, messages, *, max_tokens, exact_budget=None, recover=True, **kw):
        self.sent.append(json.dumps(messages))
        self.keys.append(spend.episode_key("m"))
        self.n += 1
        r = api.ChatResult()
        r.reasoning_text = f"think {self.n}"
        r.response_text = f"note {self.n}: read the next file"
        r.finish_reason = "tool_calls"
        path = self.files[self.n % len(self.files)]
        r.tool_calls = [{"id": f"t{self.n}", "name": "read_file",
                         "arguments": json.dumps({"path": path})}]
        return r


def _episode(profile, entry=PRICED):
    case = _case()
    files = sorted(case["env"]["files"])
    ctx = _Ctx(entry, profile, files)
    row = runner._run_env_loop(case, {"case_id": case["id"]}, ctx, 4096, None, 42)
    return ctx, row


def _extension_share(sent: list[str]) -> float:
    """Share of turns whose request starts with the previous request's bytes
    (the prefix a provider cache can serve)."""
    pairs = list(zip(sent, sent[1:]))
    return sum(b.startswith(a[:-1]) for a, b in pairs) / len(pairs)


def _after_first_stub(sent: list[str]) -> list[str]:
    """The requests from the first compaction on (the turns the old rule broke)."""
    first = next(i for i, s in enumerate(sent) if codeenv.STUB_PREFIX in s)
    return sent[first:]


# ------------------------------------------------------------------ the switch

def test_bench45_declares_hysteresis_and_bench_does_not():
    assert profiles.profile_switch("bench45", "compaction") == "hysteresis"
    assert profiles.profile_switch("bench", "compaction") is None


# ------------------------------------------------------------- compact() itself

def _history(n, size=1400):
    msgs = [{"role": "system", "content": "S"}, {"role": "user", "content": "task"}]
    for i in range(n):
        msgs.append({"role": "assistant", "content": f"a{i}",
                     "tool_calls": [{"id": f"t{i}", "type": "function",
                                     "function": {"name": "read_file", "arguments": "{}"}}]})
        msgs.append({"role": "tool", "tool_call_id": f"t{i}", "content": f"line {i}\n" + "x" * size})
    return msgs


def test_hysteresis_stubs_down_to_about_40_percent_in_one_step():
    cap = 10000
    msgs = _history(30)
    assert codeenv.estimate_tokens(msgs) > 0.6 * cap
    codeenv.compact(msgs, 6, cap, mode="hysteresis")
    assert codeenv.estimate_tokens(msgs) <= 0.4 * cap
    assert any(m.get("_stub") for m in msgs)


def test_hysteresis_leaves_the_history_alone_until_it_grows_by_the_gap():
    cap = 10000
    msgs = _history(30)
    codeenv.compact(msgs, 6, cap, mode="hysteresis")
    before = json.dumps(codeenv.wire(msgs))
    floor = codeenv.estimate_tokens(msgs)
    grown = 0
    i = 100
    while codeenv.estimate_tokens(msgs) + 450 < floor + 0.2 * cap:
        msgs += _history(1)[2:]
        msgs[-1]["tool_call_id"] = msgs[-2]["tool_calls"][0]["id"] = f"t{i}"
        i += 1
        grown += 1
        codeenv.compact(msgs, 6, cap, mode="hysteresis")
        assert json.dumps(codeenv.wire(msgs)).startswith(before[:-1])   # no rewrite yet
    assert grown >= 3


def test_hysteresis_is_deterministic():
    a, b = _history(30), _history(30)
    codeenv.compact(a, 6, 10000, mode="hysteresis")
    codeenv.compact(b, 6, 10000, mode="hysteresis")
    assert codeenv.wire(a) == codeenv.wire(b)


def test_hysteresis_never_stubs_the_protected_window():
    msgs = _history(30)
    codeenv.compact(msgs, 6, 10000, mode="hysteresis")
    tools = [m for m in msgs if m.get("role") == "tool"]
    assert not any(m.get("_stub") for m in tools[-6:])


# ------------------------------------------------- the long fake agentic case

def test_long_episode_keeps_a_stable_prefix_on_at_least_80_percent_of_turns():
    ctx, _row = _episode("bench45")
    assert len(ctx.sent) >= 30
    assert any(codeenv.STUB_PREFIX in s for s in ctx.sent)   # it did compact
    assert _extension_share(ctx.sent) >= 0.8
    assert _extension_share(_after_first_stub(ctx.sent)) >= 0.8


def test_legacy_rule_is_what_bench_measures_and_breaks_the_prefix_often():
    ctx, _row = _episode("bench")
    assert _extension_share(_after_first_stub(ctx.sent)) < 0.5


def test_bench_requests_are_byte_identical_to_the_4_1_rule():
    # golden: the same fake episode at 29373b3 (before this change)
    for entry, want in ((PRICED, BENCH_PRICED_SHA), ({}, BENCH_RIG_SHA)):
        ctx, _row = _episode("bench", entry)
        got = hashlib.sha256("\n".join(ctx.sent).encode()).hexdigest()
        assert got == want


def test_the_rule_is_model_visible_under_bench45_only():
    ctx45, _ = _episode("bench45")
    ctx41, _ = _episode("bench")
    sys45 = json.loads(ctx45.sent[0])[0]["content"]
    sys41 = json.loads(ctx41.sent[0])[0]["content"]
    assert sys45.startswith(sys41) and codeenv.HYSTERESIS_NOTE in sys45
    assert codeenv.HYSTERESIS_NOTE not in sys41


def test_bench45_rows_say_which_rule_measured_them():
    _, row45 = _episode("bench45")
    _, row41 = _episode("bench")
    assert row45.get("compaction") == "hysteresis"
    assert "compaction" not in row41


# --------------------------------------- OpenAI-shape routing key per episode

def test_routing_key_is_stable_across_every_turn_of_an_episode():
    spend.set_context(label="runA", case_id="LAG1-potkeeper-yearend", repeat=1)
    try:
        ctx, _ = _episode("bench45")
        spend.set_context(label="runA", case_id="LAG1-potkeeper-yearend", repeat=2)
        ctx2, _ = _episode("bench45")
    finally:
        spend.clear_context()
    assert ctx.keys[0] and len(set(ctx.keys)) == 1
    assert len(set(ctx2.keys)) == 1 and ctx2.keys[0] != ctx.keys[0]


# ----------------------------------------------------- anthropic_native breakpoint

def _count_cc(obj) -> int:
    if isinstance(obj, dict):
        return int("cache_control" in obj) + sum(_count_cc(v) for v in obj.values())
    if isinstance(obj, list):
        return sum(_count_cc(v) for v in obj)
    return 0


def test_anthropic_breakpoint_after_the_last_stubbed_message():
    msgs = _history(30)
    msgs[0]["content"] += "\n" + codeenv.HYSTERESIS_NOTE        # a bench45 episode
    codeenv.compact(msgs, 6, 10000, mode="hysteresis")
    body = anthropic_native.build_request("m", codeenv.wire(msgs), max_tokens=8, tools=codeenv.TOOLS)
    results = [b for m in body["messages"] for b in m["content"] if b.get("type") == "tool_result"]
    marked = [i for i, b in enumerate(results) if "cache_control" in b]
    last_stub = max(i for i, b in enumerate(results) if b["content"].startswith(codeenv.STUB_PREFIX))
    assert marked == [last_stub]
    assert _count_cc(body) <= 4


def test_anthropic_bench_episode_with_stubs_gets_no_extra_breakpoint():
    msgs = _history(30)
    codeenv.compact(msgs, 6, 10000)                               # the 4.1 rule, bench system prompt
    assert any(m.get("_stub") for m in msgs)
    body = anthropic_native.build_request("m", codeenv.wire(msgs), max_tokens=8, tools=codeenv.TOOLS)
    assert _count_cc(body["messages"]) == 0 and _count_cc(body) == 2


def test_anthropic_body_without_stubs_is_unchanged():
    msgs = _history(3)
    body = anthropic_native.build_request("m", codeenv.wire(msgs), max_tokens=8, tools=codeenv.TOOLS)
    assert _count_cc(body["messages"]) == 0
    assert _count_cc(body) == 2           # automatic + system


def test_anthropic_breakpoints_stay_within_four_on_a_whole_episode():
    ctx, _ = _episode("bench45")
    marked = 0
    for s in ctx.sent:
        body = anthropic_native.build_request("m", json.loads(s), max_tokens=8, tools=codeenv.TOOLS)
        assert _count_cc(body) <= 4
        marked += _count_cc(body["messages"])
    assert marked > 0                                             # the stub breakpoint did fire


BENCH_PRICED_SHA = "33382d056097fd6902aec364dc21d428a5498e9ed780acc0129ce5bdbb6f3303"
BENCH_RIG_SHA = "67bf8c2c94408b49e1f704a9bb042af7d70fac6278b8e9dec741c8c97b059c54"


# ------------------------- Fable checkpoint 1556 (1): Base-row reuse untouched

#: case_hash at 29373b3 of the five reused Base items (bench45 ``reuse.ids``)
REUSE_HASH_29373B3 = {"CL3-paragraph-wrap": "b727e9eb", "TZ16-pickup-dst-delay": "cb40f684",
                      "CC4-second-pitch": "c7113d94", "NC2-after-hours": "320a1e57",
                      "WL2-lighthouse": "029b06e9"}


def test_reused_base_items_keep_their_stamp_and_stay_reusable_under_bench45():
    from crucibleforge import report
    from crucibleforge.version import case_hash
    prof = profiles.load_profile("bench45")
    # A6 widened reuse.ids past the five Base items (unchanged ex-Core + pack cases)
    assert set(REUSE_HASH_29373B3) <= set((prof.get("reuse") or {}).get("ids") or [])
    n = 0
    for cid, h in REUSE_HASH_29373B3.items():
        if case_hash(cid) != h:
            # A5 stamped element_standard 45 on CC4 / NC2: their live hash moves by design (their
            # stored rows count after S11); the three unstamped Base items must stay equal
            assert cid in ("CC4-second-pitch", "NC2-after-hours"), cid
            continue
        row = {"profile": "bench", "case_id": cid, "case_revision": h, "regime": prof.get("regime"),
               "finish_reason": "stop", "grade": "pass"}
        n += bool(report.reusable_row(row, prof))
    assert n >= 3


def test_the_rule_lives_only_in_the_agentic_env_loop_never_in_a_case_file():
    # no case file carries it (case_hash of every case, bench and bench45, is untouched);
    # it is appended to the system prompt of the code-agent env loop alone
    for f in (ROOT / "cases").rglob("*.json"):
        assert codeenv.HYSTERESIS_NOTE not in f.read_text(encoding="utf-8"), f
    import inspect
    src = inspect.getsource(runner)
    assert src.count("codeenv.HYSTERESIS_NOTE") == 1
    assert "HYSTERESIS_NOTE" in inspect.getsource(runner._run_env_loop)


# -------------- Fable checkpoint 1556 (2): routing keys reach the wire by host

def _captured_chat(monkeypatch, base_url, ptype="openai", repeat=1, turns=2):
    from crucibleforge import providers as pv
    seen = []

    def fake(base, key, model, messages, *, headers=None, extra_body=None, **kw):
        seen.append({"headers": dict(headers or {}), "body": dict(extra_body or {})})
        return api.ChatResult()
    monkeypatch.setattr(api, "stream_chat_retried", fake)
    monkeypatch.setenv("CC_KEY", "k")
    pv.clear_cache()
    cfg = {"providers": {"p": {"type": ptype, "base_url": base_url, "api_key_env": "CC_KEY"}},
           "models": [{"name": "m", "provider": "p", "model_id": "m"}]}
    prov = pv.get_provider(cfg, "p")
    spend.set_context(label="runA", case_id="LAG1-potkeeper-yearend", repeat=repeat)
    try:
        for _ in range(turns):
            prov.chat("m", [{"role": "user", "content": "hi"}], max_tokens=8)
    finally:
        spend.clear_context()
        pv.clear_cache()
    return seen


def test_xai_requests_carry_a_fresh_conv_id_header_per_request(monkeypatch):
    # 2026-11-22: a sticky key makes xAI replay the earlier turns' hidden reasoning into each later prompt
    # (test_grok_api_20261122); every request gets its own key (cache_route_scope "call")
    a = _captured_chat(monkeypatch, "https://api.x.ai/v1", repeat=1)
    ka = [s["headers"].get("x-grok-conv-id") for s in a]
    assert all(k and k.startswith("cf-") for k in ka) and len(set(ka)) == len(ka)
    assert all("prompt_cache_key" not in s["body"] for s in a)


def test_openai_requests_carry_a_stable_prompt_cache_key(monkeypatch):
    a = _captured_chat(monkeypatch, "https://api.openai.com/v1")
    ks = [s["body"].get("prompt_cache_key") for s in a]
    assert ks[0] and len(set(ks)) == 1 and len(ks[0]) <= 64
    assert all("x-grok-conv-id" not in s["headers"] for s in a)


@pytest.mark.parametrize("url,ptype", [("https://api.deepseek.com/v1", "openai"),
                                       ("https://api.moonshot.ai/v1", "openai"),
                                       ("https://api.minimax.io/v1", "openai"),
                                       ("https://api.anthropic.com/v1", "anthropic")])
def test_other_providers_get_no_routing_key(monkeypatch, url, ptype):
    for s in _captured_chat(monkeypatch, url, ptype=ptype):
        assert "x-grok-conv-id" not in s["headers"]
        assert not {"prompt_cache_key", "session_id"} & set(s["body"])
