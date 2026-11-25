"""Assistant basics (checked), step 1: the profile declares the group (option (c) of the 2026-11-18 proposal).

Fifteen existing machine-checked items, two repeats each, no judge. They run in the default standard composition
(they are not adult content) and are declared in a composition block of their own, never as a ``scores:`` entry, so no
total, rank or pack gate can read them. Budgets are the board's one budget (case budget x factor within floor and cap):
16,384 for the short items, 24,576 for the reasoning items."""
from __future__ import annotations

import pytest

from crucibleforge import config, profiles, tiers
from crucibleforge.cli import plan_budget

IDS = {
    "instruct": ["IZ02-json-single-line-typed", "IZ04-lowercase-except-http", "IZ05-five-dash-lines-exact-26",
                 "IZ06-allcaps-recall-notice", "IZ07-json-city-record", "IZ08-two-questions-only"],
    "reasoning": ["RX02-knights-knaves", "RX08-water-jugs", "RX13-recurrence-term",
                  "RX20-five-houses-four-attributes", "RX21-flawed-cost-chain"],
    "longctx": ["LX2-multihop-16k", "LX3-aggregate-12k", "LX4-count-6k", "LX6-absent-12k"],
}
ALL = [i for v in IDS.values() for i in v]


@pytest.fixture
def prof(monkeypatch):
    monkeypatch.delenv("CF_COMPOSITION", raising=False)
    profiles.set_active("bench45")
    p = profiles.load_profile("bench45")
    profiles.activate_cases_root(p)
    return p


def test_the_block_names_the_fifteen_items_and_is_not_a_score(prof):
    a = prof["assistant"]
    assert prof["composition"] == "standard"
    assert a["label"] == "Assistant basics (checked)"
    assert list(a["cases"]) == ALL and len(ALL) == 15
    assert "assistant" not in [d["key"] for d in tiers.scores(prof)]
    # the ruled weights are untouched
    sc = {d["key"]: [(p["key"], p["weight"]) for p in d["parts"]] for d in tiers.scores(prof) if d["in_total"]}
    assert sum(w for _, w in sc["work"]) == 10 and sum(w for _, w in sc["chat"]) == 10


def test_the_cases_run_in_the_default_standard_composition(prof):
    for cat, ids in IDS.items():
        assert prof["cases"][cat] == ids


def test_one_repeat_each(prof):
    """Step 2 (2026-11-21, option A of the sizing re-run): one repeat per item (was two)."""
    cfg = {"defaults": {"repeats": {**prof["repeats"]}}}
    _cfg, selected = profiles.apply_profile({**prof, "chains": {}}, {"defaults": {}})
    mine = [c for c in selected if c["id"] in ALL]
    assert [c["id"] for c in mine] == ALL
    assert {config.repeats_for(_cfg, c["category"], False, c) for c in mine} == {1}
    assert prof["assistant"]["repeats"] == 1
    assert cfg["defaults"]["repeats"]["longctx"] == 2


def test_budgets_fit_the_stored_token_use(prof):
    cfg, selected = profiles.apply_profile({**prof, "chains": {}}, {"defaults": {}})
    got = {c["id"]: int(plan_budget(cfg, {"thinking": True}, c, 40960).split()[0]) for c in selected if c["id"] in ALL}
    # stored sound answers: formats <= 3.2k, long-document items <= 8k, reasoning up to ~21k (RX13 / RX20).
    # 4.5.1 (PLAN-451-LIMITS step 1, budgets_451): a reasoning allowance (floor 32,768) plus the item's own answer
    # allowance; 4.5.0 sent 16,384 / 24,576 here, under one shared cap
    for i in IDS["instruct"] + IDS["longctx"]:
        assert 32768 < got[i] <= 32768 + 1024, (i, got[i])
    for i in IDS["reasoning"]:
        assert got[i] == 63488, (i, got[i])


def test_other_compositions_do_not_carry_the_block(monkeypatch):
    monkeypatch.setenv("CF_COMPOSITION", "rc3-main")
    p = profiles.load_profile("bench45")
    assert not p.get("assistant")
    assert not any(i in (p["cases"].get("instruct") or []) for i in IDS["instruct"])
