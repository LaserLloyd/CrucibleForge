"""Early stop, lead 2026-11-19 (b): a resumed run is judged on the whole run's rows (stored + new, same minimum
sample), and under cut_at_full_budget: zero a did-not-finish reply on the five short-answer parts is not a
length-limit cut (a cut under the plain rule still is)."""
from test_chains_20261113 import _toys  # noqa: F401 - the toy chain families (autouse fixture)

from crucibleforge import earlystop as ES

CFG = {"defaults": {"early_stop": {"after_answers": 4, "max_answers": 10, "blank_rate_gt": 0.5,
                                   "token_limit_rate_gt": 0.2, "misrouted_rate_gt": 0.25}}}


def _ok(i):
    return {"case_id": f"c{i}", "response": "x", "finish_reason": "stop"}


def _cut(i, part="work/c2_trace", sent=1000, out=1000):
    return {"case_id": f"c{i}", "response": "x", "finish_reason": "length", "max_tokens_sent": sent,
            "metrics": {"completion_tokens": out}, "chain": {"id": f"ch{i}", "part": part}}


def test_stored_rows_seed_the_window_on_resume():
    es = ES.EarlyStop(CFG)
    assert es.seed([_cut(1, part="work/c1_implied"), _ok(2)]) is None   # 2 stored: below the minimum sample
    assert es.n == 2
    trip = es.observe([_ok(3), _cut(4, part="work/c1_implied")])
    assert trip and trip["signal"] == "token_limit" and trip["after_answers"] == 4
    assert trip["resumed_from"] == 2


def test_did_not_finish_on_a_short_part_is_not_a_length_cut_under_zero():
    es = ES.EarlyStop(CFG, dnf_exempt=ES.dnf_exempt_from({"cut_at_full_budget": "zero", "chains": [1]}))
    assert es.observe([_cut(1), _cut(2), _ok(3), _ok(4), _ok(5)]) is None
    assert es.counts["token_limit"] == 0 and es.counts.get("did_not_finish") == 2


def test_a_cut_on_a_long_part_or_below_the_budget_still_counts():
    ex = ES.dnf_exempt_from({"cut_at_full_budget": "zero", "chains": [1]})
    assert not ex(_cut(1, part="work/c1_implied"))
    assert not ex(_cut(1, out=500))
    assert ex(_cut(1))


def test_omit_counts_every_cut():
    assert ES.dnf_exempt_from({"cut_at_full_budget": "omit", "chains": [1]}) is None
    es = ES.EarlyStop(CFG)
    trip = es.observe([_cut(1), _cut(2), _ok(3), _ok(4)])
    assert trip and trip["signal"] == "token_limit"


def test_a_resumed_chain_run_is_judged_on_its_stored_answers_too(tmp_path, monkeypatch):
    import json

    from test_chains_20261113 import _prof, _run

    from crucibleforge import providers
    from crucibleforge.api import ChatResult
    cut = lambda lv, cid: ChatResult(response_text="nope", finish_reason="length", served_model="m",  # noqa: E731
                                      completion_tokens=64)
    off = {"defaults": {"context_length": 32768, "repeats": {"coding": 1}, "reasoning_overflow_recovery": False,
                        "early_stop": dict(ES.DEFAULTS, enabled=False)}}
    # first run (early stop off): 6 chains (two attempts each), every answer cut at the limit -> stored, ended chains
    _run(tmp_path, monkeypatch, _prof(n=6), {"m": cut}, workers=1, cfg_extra=off)
    from crucibleforge import config
    stored = len(config.load_transcripts("m"))
    assert stored == 12
    providers.clear_cache()
    on = {"defaults": {"context_length": 32768, "repeats": {"coding": 1}, "reasoning_overflow_recovery": False,
                       "early_stop": dict(ES.DEFAULTS)}}
    # resume with 30 chains: the new answers all pass; the stored cuts still count toward the window
    _run(tmp_path, monkeypatch, _prof(n=30), {"m": lambda lv, cid: "PASS"}, workers=1, cfg_extra=on)
    meta = json.loads((tmp_path / "meta_m.json").read_text(encoding="utf-8"))
    assert meta["early_stop"]["signal"] == "token_limit" and meta["early_stop"]["resumed_from"] == stored
    assert meta["early_stop"]["after_answers"] == 20          # 12 stored + 8 new, the same minimum sample


def test_the_runner_reads_the_rule_from_the_4_5_profile(monkeypatch):
    from crucibleforge import profiles, runner
    monkeypatch.setenv(profiles.COMPOSITION_ENV, "standard")
    ex = runner._early_stop_dnf_exempt({"_profile": "bench45", "defaults": {}})
    assert ex is not None
    assert ex(_cut(1, part="chat/h2_state", sent=65536, out=65000)) and not ex(_cut(1, part="work/c1_implied"))
