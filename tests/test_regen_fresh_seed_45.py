"""4.5 regenerate sends a FRESH seed (2026-11-14, G2 / lead). The rule fired on the reference's
SCENESHIFT empties, but the retry resent the request unchanged INCLUDING seed 41; a seeded
llama-server request reproduces its reply byte-identically, so 6 of 14 regenerates in
results-ref-calib2 (and 14 of 20 in results-ref-norp45) were the first attempt again. A user's
regenerate gets a new sample: the retry keeps everything else (messages, budget, sampler,
thinking) and records the seed it used."""
from __future__ import annotations

from crucibleforge import runner
from crucibleforge.api import ChatResult


class _Seeded:
    """A seeded server: the same seed gives the same reply."""
    name, type = "fake", "openai"
    extra_body, reasoning_replay = {}, False

    def __init__(self, by_seed, default=("Lenka nods.", "", "stop", 120)):
        self.by_seed, self.default, self.kw = by_seed, default, []

    def chat(self, model_id, messages, **kw):
        self.kw.append(kw)
        c, r, f, n = self.by_seed.get(kw.get("seed"), self.default)
        return ChatResult(response_text=c, reasoning_text=r, finish_reason=f, completion_tokens=n,
                          prompt_tokens=100, served_model="m")

    def switch_model(self, *a, **k):
        return 0.0


def _ctx(p):
    ctx = runner._Ctx({"defaults": {"empty_stop_rule": True, "on_empty_stop": "regenerate"}},
                      {"name": "m", "model_id": "m", "thinking": True}, p, 32768)
    ctx.recovery_enabled = False
    return ctx


EMPTY = ("", "Lenka: \"I don't know a pass", "stop", 613)


def test_the_regenerate_uses_a_different_seed_and_records_it():
    p = _Seeded({41: EMPTY})
    r = _ctx(p).call([{"role": "user", "content": "hi"}], max_tokens=64, temperature=1.0, top_p=0.95, seed=41)
    assert len(p.kw) == 2 and p.kw[0]["seed"] == 41 and p.kw[1]["seed"] != 41
    assert r.response_text == "Lenka nods." and r.empty_stop_retry["retry_visible"] is True
    assert r.empty_stop_retry["retry_seed"] == p.kw[1]["seed"]
    # nothing else changes
    a, b = dict(p.kw[0]), dict(p.kw[1])
    a.pop("seed"), b.pop("seed")
    assert a == b


def test_the_fresh_seed_is_deterministic_for_a_rerun():
    p1, p2 = _Seeded({41: EMPTY}), _Seeded({41: EMPTY})
    _ctx(p1).call([{"role": "user", "content": "hi"}], max_tokens=64, seed=41)
    _ctx(p2).call([{"role": "user", "content": "hi"}], max_tokens=64, seed=41)
    assert p1.kw[1]["seed"] == p2.kw[1]["seed"]


def test_a_session_turn_on_a_seeded_server_is_answered_by_the_regenerate():
    p = _Seeded({41: EMPTY})
    rows = runner._run_multiturn({"id": "S", "category": "rpdeep", "turns": ["a"], "max_tokens": 64,
                                  "rubric": "rp_multi"}, {}, _ctx(p), 64, 1.0, 0.95, 41)
    assert rows[0]["response"] == "Lenka nods." and "empty_stop" not in rows[0]
    assert rows[0]["empty_stop_retry"]["retry_seed"] != 41


def test_no_seed_sent_means_none_is_added():
    p = _Seeded({None: EMPTY})
    r = _ctx(p).call([{"role": "user", "content": "hi"}], max_tokens=64)
    assert "seed" not in p.kw[1] or p.kw[1]["seed"] is None
    assert r.empty_stop_retry.get("retry_seed") is None


def test_with_the_rule_off_nothing_changes():
    # bench 4.1 (no on_empty_stop): the 4.0 plain re-ask stays exactly as it was
    p = _Seeded({41: EMPTY})
    ctx = runner._Ctx({"defaults": {}}, {"name": "m", "model_id": "m", "thinking": True}, p, 32768)
    ctx.recovery_enabled = False
    ctx.call([{"role": "user", "content": "hi"}], max_tokens=64, seed=41)
    assert [k.get("seed") for k in p.kw] == [41, 41]
    p2 = _Seeded({41: EMPTY})
    ctx2 = runner._Ctx({"defaults": {"on_empty_stop": "none"}}, {"name": "m", "model_id": "m"}, p2, 32768)
    ctx2.recovery_enabled = False
    ctx2.call([{"role": "user", "content": "hi"}], max_tokens=64, seed=41)
    assert len(p2.kw) == 1


def test_the_recorded_seeds():
    p = _Seeded({41: EMPTY})
    r = _ctx(p).call([{"role": "user", "content": "hi"}], max_tokens=64, seed=41)
    esr = r.empty_stop_retry
    assert esr["first_seed"] == 41 and esr["retry_seed"] == runner.retry_seed(41, 1) != 41
