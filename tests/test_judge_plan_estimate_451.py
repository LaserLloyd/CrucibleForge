"""Review D (2026-11-25): the plan's hosted-judge estimate. A fresh standard run on the shipped candidate
(qwen/qwen3.8-27b at 0.04 / 1.50 USD per M tokens) made 837 judge calls for $0.2028; the plan said ~5 calls / $0.02.
The plan now counts what a standard run of the profile judges (judged rows, NSFW+ units, the session judge with its
rung check, pack rounds in the upper figure) at measured calls and tokens per call. No model, provider or judge is
called."""
from __future__ import annotations

import re

import pytest

from crucibleforge import cli, judge45, profiles

MEASURED_CALLS, MEASURED_USD = 837, 0.2028
PRICE = {"input": 0.04, "output": 1.50}          # the shipped candidate (models.example.yaml qwen27-judge-openrouter)
#: the standard plan's own judged cases: "5.0 judged-row equivalents at 3 samples" (all --plan, bench45)
T1_SAMPLES = 15

pytestmark = pytest.mark.live_ladder


@pytest.fixture
def prof(monkeypatch):
    monkeypatch.setenv("CF_COMPOSITION", "standard")      # the composition a standard run uses
    monkeypatch.setattr(profiles, "_ACTIVE", [profiles._ACTIVE[0]])   # restored after the test
    profiles.set_active("bench45")
    p = profiles.load_profile("bench45")
    if not (p.get("chains") or {}):
        pytest.skip("the 4.5 chains are not in this checkout")
    return p


def _n_adult(p) -> int:
    return sum(int(f.get("count") or 0) for k, ch in (p.get("chains") or {}).items() if k.startswith("nsfw/")
               for f in ch.get("flat") or [])


def _expected(lines: list[str]) -> tuple[float, float]:
    m = re.match(r"  judge cost \(estimate\) \(J on r\): ~\$([0-9.]+) expected for ~([0-9]+) call", lines[0])
    assert m, lines[0]
    return float(m.group(1)), float(m.group(2))


def test_the_standard_profile_estimate_is_within_a_factor_of_two_of_the_measured_run(prof):
    parts = cli.judge_call_model(prof, T1_SAMPLES, adult_on=True, n_adult=_n_adult(prof), judge_id="x/never-cached")
    usd, calls = _expected(judge45.judge_cost_plan_parts(PRICE, parts, route="J on r"))
    assert MEASURED_CALLS / 2 <= calls <= MEASURED_CALLS * 2, calls
    assert MEASURED_USD / 2 <= usd <= MEASURED_USD * 2, usd
    # the old estimate (judged-row equivalents x the old token guess) was two orders of magnitude short
    assert calls > 20 * (T1_SAMPLES / 3)


def test_the_call_model_counts_each_part(prof):
    parts = {p["what"].split(" (")[0]: p for p in cli.judge_call_model(
        prof, T1_SAMPLES, adult_on=True, n_adult=_n_adult(prof), judge_id="x/never-cached", pack_samples=30)}
    assert set(parts) == {"judged rows", "NSFW+ units", "session judge", "pack rounds, if every set unlocks"}
    assert parts["pack rounds, if every set unlocks"]["calls"] == 0          # the upper figure only
    assert parts["NSFW+ units"]["high"] == _n_adult(prof)
    s = parts["session judge"]
    assert s["high"] > s["calls"] > 0 and s["tokens"] == judge45.LADDER_CALL_TOKENS
    assert "12 sessions" in s["what"]


def test_no_adult_page_drops_its_calls(prof):
    on = cli.judge_call_model(prof, T1_SAMPLES, adult_on=True, n_adult=_n_adult(prof), judge_id="x")
    off = cli.judge_call_model(prof, T1_SAMPLES, adult_on=False, n_adult=0, judge_id="x")
    assert {p["what"].split(" (")[0] for p in off} == {"judged rows", "session judge"}
    assert sum(p["calls"] for p in off) < sum(p["calls"] for p in on)


def test_the_lines_without_a_price_still_say_the_calls():
    lines = judge45.judge_cost_plan_parts(None, [{"what": "judged rows (10)", "calls": 12, "high": 40,
                                                  "tokens": judge45.JUDGE_CALL_TOKENS}], route="J on r")
    assert lines[0].startswith("  judge cost unknown: J on r has no price in models.yaml; ~12-40 calls")
    assert lines[1] == "    judged rows (10): ~12 call(s), up to 40"


def test_the_nsfw_plus_plan_line_names_a_hosted_judge():
    p = {"judge": {"model_id": "local/judge"}}
    assert "(local, free)" in cli.plan_reader_line(5, None, p)
    line = cli.plan_reader_line(5, None, p, hosted="vendor/hosted-judge")
    assert "by vendor/hosted-judge" in line and "hosted, priced in the judge cost line below" in line


def test_the_nsfw_plus_plan_line_names_the_judge_the_user_gave_and_the_call_model():
    # Docker assessment item 4: after a run judged by another local judge the plan named the profile's judge
    import types
    p = {"judge": {"model_id": "local/judge"}}
    cfg = {"judge": {"candidates": [{"name": "other-judge", "model_id": "vendor/other-27b"}]}, "models": []}
    jid = cli.run_judge_id(types.SimpleNamespace(judge="fake:fake-judge"), cfg, p)
    assert jid == "fake-judge"
    assert cli.run_judge_id(types.SimpleNamespace(judge="other-judge"), cfg, p) == "vendor/other-27b"
    assert cli.run_judge_id(types.SimpleNamespace(judge=None), cfg, p) == "local/judge"
    line = cli.plan_reader_line(100, None, p, judge=jid)
    assert "by fake-judge" in line and "local/judge" not in line
    exp = round(100 * judge45.NSFW_PLUS_UNITS_READ)
    assert f"about {exp}, at most 100 calls by fake-judge" in line                 # the judge-cost call model


def test_the_upper_figure_is_the_call_model_at_the_measured_length_not_the_output_ceiling(prof):
    """Docker user path 2026-11-25: the plan said "up to $9.70" for a judge that cost $0.20 (every call priced at the
    8,192-token ceiling). The bound is the retry rules' calls at the measured length; the cut is priced apart."""
    parts = cli.judge_call_model(prof, T1_SAMPLES, adult_on=True, n_adult=_n_adult(prof), judge_id="x/never-cached",
                                 pack_samples=30)
    head = judge45.judge_cost_plan_parts(PRICE, parts, route="J on r")[0]
    m = re.search(r"up to \$([0-9.]+) for the standard run \(~([0-9]+) calls: every call asked again under the retry "
                  r"rules, each at the measured reply length\), \+ up to \$([0-9.]+) more if every pack round", head)
    assert m, head
    hi, n_hi, pk = float(m.group(1)), int(m.group(2)), float(m.group(3))
    assert MEASURED_USD <= hi <= 5 * MEASURED_USD and n_hi >= MEASURED_CALLS
    assert 0 < pk < hi
    assert "8,192-token output limit adds up to $0.0118 each" in head
