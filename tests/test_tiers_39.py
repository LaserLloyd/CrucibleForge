"""Suite 3.9 (2026-10-28): the basic Tier 1, per-case repeats and budgets,
and the Tier 2/3 ladder (crucibleforge/tiers.py). Runs as a clean clone
(conftest: no private files); the private scrub is tested on a throwaway
private category, the same way tests/test_private_categories.py does."""
from __future__ import annotations

from collections import Counter
from pathlib import Path
from types import SimpleNamespace

import pytest

from crucibleforge import config as cfgmod
from crucibleforge import profiles, runner, tiers
from crucibleforge.config import repeats_for


# The tier MECHANISM tests below were written for the first 3.9 profile
# (three scores Code / Reason / Chat, gates 75/80/75). The live profile now
# declares Coding/Work, Chat and NSFW with point sub-scores (the maintainer 2026-10-28;
# tests/test_categories_39.py); the mechanism is unchanged, so these tests
# run against a frozen copy of that profile (tests/fixtures/profiles-3.9a).
_P39A = Path(__file__).resolve().parent / "fixtures" / "profiles-3.9a"


@pytest.fixture(autouse=True)
def _frozen_39a(monkeypatch):
    from crucibleforge import report as _report
    monkeypatch.setattr(profiles, "_dirs", lambda cfg=None: [_P39A])
    monkeypatch.setattr(_report, "_PROFILE_LABELS", None)


@pytest.fixture(scope="module")
def bench():
    mp = pytest.MonkeyPatch()
    mp.setattr(profiles, "_dirs", lambda cfg=None: [_P39A])
    try:
        cfg = cfgmod.load_config(cfgmod.EXAMPLE_CONFIG_PATH)
        prof = profiles.load_profile("bench", cfg)
        cfg_p, cases = profiles.apply_profile(prof, cfg)
    finally:
        mp.undo()
    return SimpleNamespace(cfg=cfg, prof=prof, cfg_p=cfg_p, cases=cases)


def _rows(cfg_p, cases):
    n = Counter()
    for c in cases:
        n[c["category"]] += repeats_for(cfg_p, c["category"], case=c)
    return n


# ------------------------------------------------------------- tier 1 rows

def test_tier1_rows_and_repeats_follow_the_decisions(bench):
    """the maintainer 2026-10-28: code-bank fix/feature x1, math x1, every other
    deterministic category x2, Chat x1; the private component is not in a
    clean clone."""
    n = _rows(bench.cfg_p, bench.cases)
    assert n == {"perf": 4, "coding": 2, "codebank": 4, "codeagent": 4, "tooluse": 6,
                 "instruct": 2, "reasoning": 4, "math": 2, "rp": 1, "rpdeep": 1,
                 "nsfw": 5, "story": 1, "steer": 2}
    assert sum(v for k, v in n.items() if k != "perf") == 34
    # step 4 (2026-10-28): CZ08/CZ09/TZ01 left T1 (saturated on MiniMax-M3)
    ids = {c["id"] for c in bench.cases}
    assert not ids & {"CZ08-triple-substring", "CZ09-range-kth", "TZ01-contradicts-user-assumption"}


def test_case_repeats_override_the_category(bench):
    prof = {**bench.prof, "case_repeats": {"CZ05-optimal-bst-cost": 3}}
    cfg_p, cases = profiles.apply_profile(prof, bench.cfg)
    by = {c["id"]: c for c in cases}
    assert repeats_for(cfg_p, "coding", case=by["CZ05-optimal-bst-cost"]) == 3
    assert repeats_for(cfg_p, "codeagent", case=by["CB-F1-larder-bughunt"]) == 2
    assert repeats_for(cfg_p, "coding", smoke=True, case=by["CZ05-optimal-bst-cost"]) == 1
    # the shipped override: the predict quiz runs once
    c3 = next(c for c in bench.cases if c["id"] == "CB-C3-predict-sqlite")
    assert c3["repeats"] == 1


def test_case_repeats_merge_from_a_private_overlay():
    assert "case_repeats" in profiles._MERGE_KEYS


def test_dropped_items_stay_in_the_case_files(bench):
    """Removed from the benchmark, still available (PLAN §5 step 4)."""
    ids = {c["id"] for c in cfgmod.load_cases()}
    assert {"CZ02-segment-crossings", "TZ12-stock-transfer-pallets", "RX02-knights-knaves",
            "ST1-ten-elements", "CB-A2-pulsecount-upsert", "RD2-trieste"} <= ids
    t1 = {c["id"] for c in bench.cases}
    assert "TZ12-stock-transfer-pallets" not in t1 and "CZ02-segment-crossings" not in t1


# ----------------------------------------------------------------- budgets

def _ctx(cfg_p, ctx_len, thinking=True):
    d = cfg_p["defaults"]
    return SimpleNamespace(thinking=thinking, think_factor=float(d["thinking_max_tokens_factor"]),
                           think_cap=int(d["thinking_max_tokens_cap"]),
                           think_floor=int(d["thinking_max_tokens_floor"]),
                           bank_budget=int(d["bank_budget"]), ctx_len=ctx_len)


def test_board_regime_unchanged_for_non_bank_rows(bench):
    c = _ctx(bench.cfg_p, 40960)
    budget = runner._Ctx.budget
    assert budget(c, 6144) == 24576            # programs: x4 capped
    assert budget(c, 768) == 16384             # instruct/steer: the floor
    assert budget(c, 4096) == 16384            # reasoning/math: x4 = floor
    assert budget(c, 2000) == 16384            # chat rows
    # a non-thinking model: the old raw budget only without one_budget; under
    # the deployed regime's one-budget rule (S8) it gets the same 24,576
    assert budget(_ctx(bench.cfg_p, 40960, thinking=False), 6144) == 6144
    one = _ctx(bench.cfg_p, 40960, thinking=False)
    one.one_budget = True
    assert budget(one, 6144) == 24576
    assert runner.escalation_rungs(bench.cfg_p) == [65536, 131072]


def test_code_bank_rows_get_their_own_65k_budget(bench):
    """65,536 for every bank row, clamped to what fits the served window."""
    assert bench.cfg_p["defaults"]["bank_budget"] == 65536
    assert runner.bank_budget(_ctx(bench.cfg_p, 1_000_000), 3000) == 65536   # MiniMax
    assert runner.bank_budget(_ctx(bench.cfg_p, 40960), 3000) == \
        40960 - 3000 - runner.BANK_WINDOW_SLACK                                # rig at the floor
    assert runner.bank_budget(_ctx(bench.cfg_p, 73728), 10000) == \
        73728 - 10000 - runner.BANK_WINDOW_SLACK                               # T3 load


def test_bank_rows_never_escalate(bench):
    rows = [{"category": "codebank", "finish_reason": "length", "thinking": True,
             "case_id": "CB-B5-famcal-rrule", "repeat": 1, "max_tokens_sent": 65536},
            {"category": "coding", "finish_reason": "length", "thinking": True,
             "case_id": "CZ05-optimal-bst-cost", "repeat": 1, "max_tokens_sent": 24576}]
    assert runner.escalation_jobs(rows, 65536) == {(None, "CZ05-optimal-bst-cost", 1)}


def test_context_floor_raises_never_lowers(bench):
    d = bench.cfg_p
    assert d["defaults"]["context_floor"] == 40960
    assert runner.load_context(d, {"context_length": 32768}) == 40960        # rig
    assert runner.load_context(d, {"context_length": 1_000_000}) == 1_000_000
    assert runner.load_context(d, {"context_length": 32768}, min_ctx=73728) == 73728
    assert runner.load_context(d, {"context_length": 32768}, rung=65536) >= 65536
    no_floor = {**d, "defaults": {**d["defaults"], "context_floor": None}}
    assert runner.load_context(no_floor, {"context_length": 32768}) == 32768


def test_rounds_with_bank_rows_load_wide_enough_for_the_full_bank_budget(bench):
    """the maintainer 2026-10-28 "don't decrease context or thinking cap": a rig model
    is loaded at bank_budget + pad (73,728) whenever the round runs code-bank
    rows, so no bank row is clamped below 65,536; a round without bank rows
    keeps the 40,960 floor."""
    d = bench.cfg_p
    bank = [{"id": "CB-D4-watch-sessions", "category": "codebank"},
            {"id": "RPS1-hollow-reach", "category": "rp"}]
    chat = [{"id": "RPS1-hollow-reach", "category": "rp"}]
    assert runner.bank_context(d, bank) == 65536 + runner.ESCALATION_CTX_PAD == 73728
    assert runner.bank_context(d, chat) is None
    assert runner.load_context(d, {"context_length": 32768}, cases=bank) == 73728
    assert runner.load_context(d, {"context_length": 32768}, cases=chat) == 40960
    assert runner.load_context(d, {"context_length": 1_000_000}, cases=bank) == 1_000_000
    # at that load a T1 bank row (prompt well under 6k tokens) gets the whole budget
    assert runner.bank_budget(_ctx(d, 73728), 6000) == 65536
    no_bank = {**d, "defaults": {**d["defaults"], "bank_budget": 0}}
    assert runner.load_context(no_bank, {"context_length": 32768}, cases=bank) == 40960


def test_rpdeep_fact_groups_judge_at_one_sample_without_craft():
    from crucibleforge import judge
    assert judge.DEEP_FACT_SAMPLES == 1
    assert judge.DEEP_CRAFT is False


# --------------------------------------------------------------- tier lists

def test_tier_cases_are_stamped_and_run_once(bench):
    t2 = profiles.tier_cases(bench.prof, 2)
    t3 = profiles.tier_cases(bench.prof, 3)
    assert {c["tier"] for c in t2} == {2} and {c["tier"] for c in t3} == {3}
    assert all(c["repeats"] == 1 for c in t2 + t3)
    assert {c["tier_score"] for c in t2} == {"code", "reason", "chat"}
    assert {c["tier_score"] for c in t3} == {"code"}
    assert all(c["min_context"] == 73728 for c in t3)
    assert {c["id"] for c in t2 if c["tier_score"] == "chat"} == {"RD2-trieste", "RD3-granary"}
    only_code = profiles.tier_cases(bench.prof, 2, scores=["code"])
    assert {c["tier_score"] for c in only_code} == {"code"}
    # no tier case is also a tier-1 case
    t1 = {c["id"] for c in bench.cases}
    assert not t1 & {c["id"] for c in t2 + t3}


def test_case_tiers_map(bench):
    m = tiers.case_tiers(bench.prof)
    assert m["CZ05-optimal-bst-cost"] == (1, None)
    assert m["CZ02-segment-crossings"] == (2, "code")
    assert m["MH16-digit-sum-power"] == (2, "reason")
    assert m["CB-L3-homehub-nightpings"] == (3, "code")


# ------------------------------------------------------- gates and scoring

def _t1(code=None, reason=None, chat=None):
    return {"coding": code, "reason": reason, "chat": chat}


def _states(**kw):
    """_states(t2_code="rows", t3_code="na_ctx") -> {2: {...}, 3: {...}}"""
    out = {2: {}, 3: {}}
    for k, v in kw.items():
        t, s = k.split("_")
        out[int(t[1])][s] = v
    return out


def test_gates_unlock_per_score(bench):
    p = bench.prof
    assert tiers.gate(p, 2, "code") == 75 and tiers.gate(p, 2, "reason") == 80
    assert tiers.gate(p, 2, "chat") == 75 and tiers.gate(p, 3, "code") == 60
    assert tiers.unlocked(p, 2, {"code": 75.0, "reason": 79.9, "chat": 90}) == ["code", "chat"]
    assert tiers.unlocked(p, 2, {"code": None, "reason": 100, "chat": 10}) == ["reason"]
    # T3 has Code items only (Reason / Chat T3 are not built)
    assert tiers.unlocked(p, 3, {"code": 60, "reason": 100, "chat": 100}) == ["code"]
    assert tiers.unlocked(p, 3, {"code": 59.9}) == []


def test_score_is_t1_plus_half_t2_plus_half_t3(bench):
    cards = {2: _t1(code=70.0), 3: _t1(code=50.0)}
    ts = tiers.combine(bench.prof, _t1(code=80.0, reason=60.0, chat=76.0), cards,
                       _states(t2_code="rows", t3_code="rows", t2_chat="rows"))
    c = ts["code"]
    assert (c["t1"], c["t2"], c["t3"]) == (80.0, 70.0, 50.0)
    assert c["total"] == pytest.approx(80 + 35 + 25) and c["reached"] == 3
    assert tiers.label(c) == "140.0 (T3)"
    # max 200
    full = tiers.combine(bench.prof, _t1(code=100.0), {2: _t1(code=100.0), 3: _t1(code=100.0)},
                         _states(t2_code="rows", t3_code="rows"))["code"]
    assert full["total"] == 200.0


def test_a_locked_tier_scores_zero_not_na(bench):
    ts = tiers.combine(bench.prof, _t1(code=74.9, reason=60.0, chat=76.0),
                       {2: _t1(code=100.0, chat=None)}, _states(t2_code="rows"))
    c, r = ts["code"], ts["reason"]
    assert c["state"] == {2: "locked", 3: "locked"} and c["t2"] == 0.0 and c["t3"] == 0.0
    assert c["total"] == pytest.approx(74.9) and c["reached"] == 1
    assert tiers.label(c) == "74.9 (T1)"
    assert r["state"][2] == "locked" and r["state"][3] == "none" and r["total"] == 60.0
    # chat unlocked T2 but its rows are not there yet: pending, 0 for now
    h = ts["chat"]
    assert h["state"][2] == "pending" and h["total"] == 76.0
    assert tiers.label(h) == "76.0 (T1) [T2 pending]"


def test_t3_gate_is_on_t2_and_a_failed_t2_locks_t3(bench):
    ts = tiers.combine(bench.prof, _t1(code=90.0), {2: _t1(code=59.0), 3: _t1(code=100.0)},
                       _states(t2_code="rows", t3_code="rows"))["code"]
    assert ts["state"] == {2: "scored", 3: "locked"}
    assert ts["total"] == pytest.approx(90 + 29.5) and tiers.label(ts) == "119.5 (T2)"


def test_na_ctx_scores_zero_and_is_labelled(bench):
    ts = tiers.combine(bench.prof, _t1(code=90.0), {2: _t1(code=80.0)},
                       _states(t2_code="rows", t3_code="na_ctx"))["code"]
    assert ts["state"][3] == "na_ctx" and ts["t3"] == 0.0
    assert ts["total"] == pytest.approx(130.0) and ts["reached"] == 2
    assert tiers.label(ts) == "130.0 (T2) [T3 n/a (ctx)]"
    assert tiers._rows_state([{"grade": "skipped"}, {"grade": "skipped"}]) == "na_ctx"
    assert tiers._rows_state([{"grade": "skipped"}, {"grade": "pass"}]) == "rows"
    assert tiers._rows_state([]) == "none"


def test_no_t1_score_means_no_total(bench):
    ts = tiers.combine(bench.prof, _t1(), {}, _states())
    assert all(e["total"] is None for e in ts.values())
    assert tiers.label(ts["code"]) == "-" and tiers.overall(ts) is None


def test_overall_is_the_mean_of_the_three_totals():
    ts = {"code": {"total": 120.0}, "reason": {"total": 90.0}, "chat": {"total": 60.0}}
    assert tiers.overall(ts) == pytest.approx(90.0)
    assert tiers.overall({"code": {"total": 100.0}, "chat": {"total": None}}) == 100.0


def test_tier_weights_cover_only_what_the_tier_measures(bench):
    from crucibleforge import report
    sc = report.scoring_config(bench.cfg)
    t1 = tiers.tier_scoring(sc, bench.prof, 1)
    assert set(t1["coding"]) == {"coding", "codebank", "codeagent", "tooluse", "instruct"}
    assert set(t1["chat"]) == {"rp", "rpdeep", "nsfw", "explicit_peak", "willing", "story", "steer"}
    assert t1["reason"] == {"reasoning": 100}
    t2 = tiers.tier_scoring(sc, bench.prof, 2)
    assert t2["coding"] == {"codeagent": 10, "codebank": 15, "coding": 20}
    assert t2["reason"] == {"reasoning": 100} and t2["chat"] == {"rpdeep": 15}
    t3 = tiers.tier_scoring(sc, bench.prof, 3)
    assert t3["coding"] == {"codebank_long": 15, "codeagent_long": 10}
    assert t3["chat"] == {} and t3["reason"] == {}


# ------------------------------------------------------------ the board

import json  # noqa: E402

from crucibleforge import report, version  # noqa: E402


def _row(cid, cat, grade="pass", run="r1", **kw):
    return {"bench_run_id": run, "bench_revision": version.revision(), "profile": "bench",
            "case_id": cid, "repeat": kw.pop("repeat", 1), "turn": None, "category": cat,
            "grade": grade, "ts": "2026-10-28T01:00:00+00:00",
            "metrics": {"tok_per_s": 30.0}, **kw}


def _seed(tmp_path, monkeypatch, label, rows):
    monkeypatch.setattr(cfgmod, "RESULTS_DIR", tmp_path)
    monkeypatch.setattr(report, "_PROFILE_LABELS", None)
    (tmp_path / f"transcripts_{label}.jsonl").write_text(
        "\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    (tmp_path / f"meta_{label}.json").write_text(json.dumps(
        {"model_id": "x/y", "provider": "p", "profile": "bench",
         "finished": "2026-10-28T01:30:00+00:00"}), encoding="utf-8")


def _tiered_rows():
    rows = [_row(c, "coding", repeat=r) for c in ("CZ05-optimal-bst-cost",
                                                  "CZ08-triple-substring", "CZ09-range-kth")
            for r in (1, 2)]
    rows += [_row(c, "reasoning", repeat=r) for c in ("RX13-recurrence-term",
                                                      "RX20-five-houses-four-attributes")
             for r in (1, 2)]
    rows += [_row("MH06-fermat-mod-prime", "math"), _row("MH11-strings-no-adjacent-no-ABC", "math")]
    # tier 2 Code: one of two programs passes (T2 Code = 50, under the T3 gate)
    rows += [_row("CZ02-segment-crossings", "coding", run="t2", tier=2),
             _row("CZ01-prime-census", "coding", grade="fail", run="t2", tier=2)]
    return rows


def test_board_scores_tiers_and_labels_the_tier_reached(tmp_path, monkeypatch, bench):
    _seed(tmp_path, monkeypatch, "m", _tiered_rows())
    md = report.generate(cfg=bench.cfg)
    s = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))["models"]["m"]
    c = s["scorecard"]
    # T1 Code = programs 100 (the only T1 Code component measured here)
    assert c["t1"]["coding"] == 100.0 and c["t1"]["reason"] == 100.0
    assert c["tiers"]["code"]["state"] == {"2": "scored", "3": "locked"} or \
        c["tiers"]["code"]["state"] == {2: "scored", 3: "locked"}
    assert c["coding"] == pytest.approx(100 + 0.5 * 50)          # 125, max 200
    assert c["labels"]["code"] == "125.0 (T2)"
    # Reason passed its gate (100 >= 80) but no T2 Reason row ran yet
    assert c["labels"]["reason"] == "100.0 (T1) [T2 pending]" and c["reason"] == 100.0
    assert c["chat"] is None
    # the components stay tier 1: CZ02/CZ01 do not enter Programs
    assert c["components"]["coding"] == 100.0
    head = md.split("## Scorecard")[1].split("\n\n")[1].splitlines()[0]
    # suite 3.9 headline (headline.py): S ± m first, the raw % in a detail column
    assert head.startswith("| # | Model | Overall | Code | Reason | Chat | Raw % ")
    assert "125.0 (T2) / 100.0 (T1) [T2 pending] / - → 112.5" in md and "## Tiers" in md
    assert "| **10**.6 ± " in md                                # Code 125 % -> S 10.6
    assert "100 / 50 / locked" in md                            # Code T1 / T2 / T3
    html = (tmp_path / "report.html").read_text(encoding="utf-8")
    assert "tierTag" in html and "Tiers" in html


def test_tier_rows_never_decide_completeness(tmp_path, monkeypatch, bench):
    """A tier-2 row pending the judge must not send the board back to old
    values; completeness is judged on the tier-1 cases."""
    rows = _tiered_rows() + [_row("RD2-trieste", "rpdeep", run="t2c", tier=2,
                                  needs_judge=True, rubric="rp_deep")]
    _seed(tmp_path, monkeypatch, "m", rows)
    ids = report._profile_case_ids(bench.cfg)
    assert "RD2-trieste" not in ids and "CZ05-optimal-bst-cost" in ids
    assert "RD2-trieste" in report._profile_case_ids(bench.cfg, with_tiers=True)
    kept = report.board_filter("m", bench.cfg)
    assert {"CZ02-segment-crossings", "RD2-trieste"} <= {r["case_id"] for r in kept}


def test_rpdeep_component_scores_complete_rows_only(monkeypatch):
    from crucibleforge import rpdeep
    rates = {("RD1-marisol", 1): 0.5, ("RD1-marisol", 2): 0.7, ("RD2-trieste", 1): None}
    monkeypatch.setattr(rpdeep, "score_row", lambda r, c, t: {
        "rate": rates[(r["case_id"], r["repeat"])], "items": []})
    monkeypatch.setattr(report, "_cases_by_id", lambda: {"RD1-marisol": {}, "RD2-trieste": {}})
    rows = [{"category": "rpdeep", "case_id": k[0], "repeat": k[1], "needs_judge": True,
             "bench_run_id": "r"} for k in rates]
    rows.append({"category": "rpdeep", "case_id": "RD3-granary", "grade": "skipped"})
    b = report.rpdeep_block(rows, rows)
    assert b["score"] == pytest.approx(0.6) and b["n"] == 2
    assert b["incomplete"] == 1 and b["skipped"] == ["RD3-granary"]
    assert report.component_values({"rpdeep": b, "rp": {}, "nsfw": {}, "steer": {},
                                     "coding": {}, "tooluse": {}, "instruct": {}})["rpdeep"] \
        == pytest.approx(0.6)


# ------------------------------------------------- the private component

def test_public_board_scrubs_max_nsfw_rp_with_tiers(tmp_path, monkeypatch):
    """Max NSFW RP stays private on the 3.9 profile: a private overlay
    component (as profiles/bench.private.yaml adds it) counts on the local
    board and is absent from every `report --public` file, tier tables
    included; the public Chat is scored without it."""
    import shutil
    import yaml
    cat, ids, lab = "zz-max-rp", ["ZQ1-private-one", "ZQ2-private-two"], "Max Hush RP"
    pdir = tmp_path / "cases-private"
    pdir.mkdir()
    (pdir / f"{cat}.json").write_text(json.dumps([
        {"id": i, "prompt": f"private {i}", "rubric": "nsfw_craft", "max_tokens": 100}
        for i in ids]), encoding="utf-8")
    monkeypatch.setattr(cfgmod, "PRIVATE_ENABLED", True)
    monkeypatch.setattr(cfgmod, "PRIVATE_CASES_DIR", pdir)
    monkeypatch.setattr(cfgmod, "PRIVATE_CATEGORIES", [cat])
    monkeypatch.setattr(cfgmod, "CATEGORIES", cfgmod.CATEGORIES + [cat])
    cdir = tmp_path / "conf"
    (cdir / "profiles").mkdir(parents=True)
    monkeypatch.setattr(profiles, "_dirs", lambda cfg=None: [cdir / "profiles"])
    shutil.copy(_P39A / "bench.yaml", cdir / "profiles" / "bench.yaml")
    (cdir / "profiles" / "bench.private.yaml").write_text(yaml.safe_dump({
        "private": [cat], "labels": {cat: lab}, "repeats": {cat: 1},
        "max_tokens": {cat: 100}, "cases": {cat: ids}, "scoring": {"chat": {cat: 10}}}),
        encoding="utf-8")
    cfg = cfgmod.load_config(cfgmod.EXAMPLE_CONFIG_PATH)
    cfg["_path"] = str(cdir / "models.yaml")
    results = tmp_path / "results"
    results.mkdir()
    monkeypatch.setattr(report, "_CASES_BY_ID", None)
    _seed(results, monkeypatch, "m", [])
    monkeypatch.setattr(report, "_PROFILE_LABELS", {cat: lab})
    from crucibleforge.judge import RUBRICS
    dims = RUBRICS["nsfw_craft"]["dims"]
    rows = _tiered_rows() + [
        _row(i, cat, needs_judge=True, rubric="nsfw_craft", response="text",
             private_revision=version.private_category_hash(cat),
             judge={"scores": {d: 8 for d in dims}}) for i in ids]
    (results / "transcripts_m.jsonl").write_text(
        "\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    # the private component is tier 1 Chat on the local board
    assert set(tiers.tier_scoring(report.scoring_config(cfg),
                                  profiles.load_profile("bench", cfg), 1)["chat"]) >= {cat}
    local = report.generate(cfg=cfg)
    assert lab in local
    report.generate(cfg=cfg, public=True)
    pub = results / "public"
    texts = {p.name: p.read_text(encoding="utf-8") for p in pub.iterdir()}
    assert set(texts) == {"report.md", "failures.md", "report.json", "report.html"}
    for name, text in texts.items():
        for term in (cat, lab, *ids):
            assert term.lower() not in text.lower(), (name, term)
    assert "## Tiers" in texts["report.md"]
    s = json.loads(texts["report.json"])["models"]["m"]
    assert cat not in s["scorecard"]["weights"]["chat"]


# ------------------------------------------------- the run: tier phases

from crucibleforge import cli  # noqa: E402


def test_tier_todo_runs_only_the_unlocked_scores(tmp_path, monkeypatch, bench):
    _seed(tmp_path, monkeypatch, "m", _tiered_rows())
    cfg_p = bench.cfg_p
    scores, cases, jobs = cli.tier_todo("m", cfg_p, bench.prof, 2)
    # Code 100 >= 75 and Reason 100 >= 80 unlock; Chat has no T1 score
    assert scores == ["code", "reason"]
    assert {c["tier_score"] for c in cases} == {"code", "reason"}
    # CZ02 / CZ01 already have current rows: not run again (a resumed nightly)
    ids = {j[1] for j in jobs}
    assert "CZ02-segment-crossings" not in ids and "CZ01-prime-census" not in ids
    assert {"CZ03-lisp-machine", "CZ11-lex-triple-substring", "CB-E3-lockrun", "CB-A8-shoplist-sync",
            "CB-F8-nudgebot-agent", "MH16-digit-sum-power", "MH22-dual-base-palindrome"} == ids
    assert all(j[0] is None and j[2] == 1 for j in jobs)          # new run id, 1 repeat
    # T3: Code T2 is 50 < 60 -> nothing
    assert cli.tier_todo("m", cfg_p, bench.prof, 3)[0] == []


def test_tier_phases_run_t2_then_t3_and_judge_each(tmp_path, monkeypatch, bench):
    rows = _tiered_rows()
    rows = [r for r in rows if r["case_id"] != "CZ01-prime-census"]   # T2 Code = 100 -> T3
    _seed(tmp_path, monkeypatch, "m", rows)
    calls = []

    def fake_run(cfg, entries, cases, jobs_by_label=None, min_ctx=None, record_key=None, **k):
        calls.append(("run", [e["name"] for e in entries], sorted(j[1] for j in jobs_by_label["m"]),
                      min_ctx, record_key))
        return {"m": {"failed": False, "rows": 1, "load_s": 0, "device": "p"}}

    class Guard:
        def __init__(self, *a, **k):
            pass

        def restore(self):
            pass

    import crucibleforge.runner as runner_mod
    monkeypatch.setattr(runner_mod, "run_models", fake_run)
    monkeypatch.setattr(cli, "_ProviderGuard", Guard)
    monkeypatch.setattr(cli, "_escalate_phase", lambda a, c, labels: calls.append(("esc", labels)) or 0)
    monkeypatch.setattr(cli, "cmd_judge", lambda a, c: calls.append(("judge", a.models)) or 0)
    cfg = {**bench.cfg, "models": [{"name": "m", "model_id": "x/y", "provider": "studioforge",
                                    "context_length": 32768}]}
    args = cli.argparse.Namespace(models="m", profile=None, judge=None, smoke=False)
    assert cli._tier_phases(args, cfg, ["m"]) == 0
    runs = [c for c in calls if c[0] == "run"]
    assert runs[0][2] == ["CB-A8-shoplist-sync", "CB-E3-lockrun", "CB-F8-nudgebot-agent",
                          "CZ01-prime-census", "CZ03-lisp-machine", "CZ11-lex-triple-substring",
                          "MH16-digit-sum-power", "MH22-dual-base-palindrome"]
    assert runs[0][3] is None and runs[0][4] == "tiers"
    # T2 Code (CZ02 passed, the fakes wrote nothing else) = 100 >= 60 -> T3 at 73728
    assert runs[1][2] == ["CB-L2-homehub-energy", "CB-L3-homehub-nightpings",
                          "CB-L4-homehub-sustained"]
    assert runs[1][3] == 73728
    assert [c[0] for c in calls] == ["run", "esc", "judge", "run", "esc", "judge"]


def test_no_tiers_flag_and_smoke_skip_the_tier_phases(bench):
    args = cli.argparse.Namespace(models="m", profile=None, judge=None, smoke=True)
    assert cli._tier_phases(args, bench.cfg, ["m"]) == 0
    args = cli.argparse.Namespace(models="m", profile=None, judge=None, smoke=False, no_tiers=True)
    assert cli._tier_phases(args, bench.cfg, ["m"]) == 0


def test_plan_prints_rows_repeats_budgets_and_tiers_without_a_model(capsys, monkeypatch, bench):
    import crucibleforge.runner as runner_mod
    monkeypatch.setattr(runner_mod, "run_models", lambda *a, **k: pytest.fail("ran a model"))
    cfg = {**bench.cfg, "models": [
        {"name": "rig", "model_id": "a/b", "provider": "studioforge", "context_length": 32768,
         "extra_body": {"reasoning_format": "deepseek"}},
        {"name": "api", "model_id": "M", "provider": "minimax", "context_length": 1000000}]}
    cfg.setdefault("providers", {})["minimax"] = {"type": "openai", "base_url": "http://x"}
    cfg["providers"].setdefault("studioforge", {"type": "studioforge", "base_url": "http://rig/v1"})
    args = cli.argparse.Namespace(models="rig,api", profile=None, judge=None, smoke=False,
                                  plan=True)
    assert cli.cmd_all(args, cfg) == 0
    out = capsys.readouterr().out
    rig, api = out.split("== api")
    # T1 has bank rows: the rig loads at 73,728 so every bank row gets 65,536
    assert "T1 — every model: 34 rows (25 cases)" in rig and "loaded at ctx 73728" in rig
    assert "budget 65536 (bank)" in rig and "budget 3" not in rig.split("T2 code")[0]
    t2r = rig.split("T2 reason")[1].split("T2 chat")[0]
    assert "loaded at ctx 40960" in t2r       # no bank rows: the plain floor
    assert "CZ05-optimal-bst-cost" in rig and "x2  budget 24576" in rig
    assert "IZ03-no-letter-e-three-lines         x2  budget 16384" in rig
    assert "MH06-fermat-mod-prime                x1" in rig
    assert "CB-B5-famcal-rrule" in rig and "(bank)" in rig
    assert "T2 code — if code >= 75 on T1: 7 rows" in rig
    assert "T3 code — if code >= 60 on T2: 3 rows, loaded at ctx 73728" in rig
    assert "T3 reason: no items" in rig and "T3 chat: no items" in rig
    # the hosted model: bank rows get the full 65,536
    # S8 (one budget per row): no "if thinking" split any more
    assert "65536 (bank)" in api and "if thinking" not in api
    assert "no model, provider or judge was called" in out


# ---------------------------------------------------------------- version

def test_suite_390_and_per_case_bank_stamps():
    assert version.SUITE_VERSION == "4.0.1"
    h = version.bank_case_hash("codebank", "CB-D4-watch-sessions")
    row = {"category": "codebank", "case_id": "CB-D4-watch-sessions", "bank_revision": h,
           "bench_revision": version.revision()}
    assert h and version.bank_row_current(row)
    # another task's stamp differs: editing one task leaves the others current
    assert h != version.bank_case_hash("codebank", "CB-B5-famcal-rrule")
    assert not version.bank_row_current({**row, "bench_revision": "3.8.0+efb56912"})
    assert "3.8.0+36a60ec6" in version.previous_regime_revisions()


def test_a_t1_item_the_context_cannot_serve_is_labelled(bench):
    from crucibleforge import report as rep
    st = {"tier_stats": {}, "tier_states": {2: {}, 3: {}},
          "rpdeep": {"score": None, "skipped": ["RD1-marisol"]},
          "rp": {"overall": 8.0}, "nsfw": {}, "steer": {}, "coding": {}, "tooluse": {},
          "instruct": {}, "speed": {}}
    c = rep.tier_scorecard(st, rep.scoring_config(bench.cfg), bench.prof)
    assert c["chat"] == pytest.approx(80.0)              # RP alone, renormalised
    assert c["labels"]["chat"] == "80.0 (T1) [T2 pending] [Deep RP n/a (ctx)]"


def test_old_values_count_tier_one_cases_only(bench, monkeypatch):
    """A 3.8 run had CZ02 in tier 1: as old values it must not be scored as
    a tier-2 result the model never had to unlock."""
    from crucibleforge import report as rep
    rows = [_row("CZ05-optimal-bst-cost", "coding"),
            _row("CZ02-segment-crossings", "coding")]
    st = rep.tier_model_stats("m", bench.cfg, rows, {}, {"old": True})
    assert st["coding"]["n"] == 1 and st["tier_stats"] == {}
    c = rep.tier_scorecard(st, rep.scoring_config(bench.cfg), bench.prof)
    assert c["coding"] == 100.0 and c["labels"]["code"] == "100.0 (T1) [T2 pending]"
