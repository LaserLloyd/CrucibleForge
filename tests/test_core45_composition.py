"""4.5 Core composition (A6 / S7a): the ACTIVE Core id list, its budget, and the panel rule.

The Core is DATA in profiles/bench45.yaml (``cases`` for Base items, ``core_generated`` +
``core_level`` for generated families, ``core_alternates`` for the switchable slots). These
tests pin the shipped composition; changing the Core at S7b is a deliberate edit of
``ACTIVE`` below plus the profile, in one commit."""
import copy
import json
from pathlib import Path

import pytest

from crucibleforge import budget, config, profiles, report

ROOT = Path(__file__).resolve().parent.parent
CFG = config.load_config(config.EXAMPLE_CONFIG_PATH)

#: the active Core id list, in profile order, by score (PINNED; A6 starting Core 2026-11-12)
ACTIVE = {
    "code": ["TZ16-pickup-dst-delay", "CB-C6-predict-node"]
            + [f"PATCHFIX-core-7-{i}" for i in (1, 2)] + [f"PLANTED_OPT-core-4-{i}" for i in (1, 2, 3)],
    "chat": ["CC4-second-pitch", "RV2-skarren-hut", "SESSION_STATE-multi_hard-core-9-1",
             "SESSION_STATE-multi_hard-core-9-2"],
    # Main NSFW mn1-mn5 (Admin N, EXP-20261113-21), ON since rc1 (lead 2026-11-13)
    "nsfw": ["NC3-palm-house", "NX2-juniper", "UX1-tide-cell", "NC2-after-hours", "NC1-tideglass"],
}
ALL_ACTIVE = [i for v in ACTIVE.values() for i in v]
#: rc1 NSFW items with no budget target in budget/targets-45.json yet (open: Admin N measures them)
NSFW_NO_TARGET_YET = {"NX2-juniper", "UX1-tide-cell"}
#: the Base ids (byte-identical 4.1 cases, stored rows reused): the Main items that are stored cases
BASE = ["TZ16-pickup-dst-delay", "CB-C6-predict-node", "CC4-second-pitch", "RV2-skarren-hut"]
# + Main NSFW mn1-mn5 (rc1, 2026-11-13): NC3, NX2, NC2, NC1 judged (UX1 is willing-scored) -> 11 estimated
JUDGE_CALLS_LIMIT = 11   # was 6 before NSFW: measured: CC4 2 + RV2 4 at group_window 4, no NSFW. TODO(C2, task 8): group_window 4 vs 6 on the stored rows; at 6 this drops to 3


@pytest.fixture(scope="module")
def prof():
    return profiles.load_profile("bench45", CFG)


@pytest.fixture(scope="module")
def rep(prof):
    return budget.compute(prof, targets=budget.load_targets(ROOT / "budget" / "targets-45.json"),
                          reference=budget.load_reference(ROOT / "budget" / "reference.json"), packs=False)


def _core_ids(prof):
    """Every Core id by declaration: the profile's scored case lists plus the pending instances."""
    ids = [i for cat, v in prof["cases"].items() if cat in budget.KIND_TO_SCORE for i in v or []]
    return ids + [c["id"] for c in profiles.core_pending(prof)]


def test_active_core_id_list_is_pinned(prof):
    assert sorted(_core_ids(prof)) == sorted(ALL_ACTIVE)
    rows = {r["id"]: r["score"] for r in budget.case_list_ids(prof)}
    for score, ids in ACTIVE.items():
        assert sorted(i for i, s in rows.items() if s == score) == sorted(ids), score


def test_base_items_are_the_five_stored_row_ids(prof):
    assert prof["reuse"]["from"] == ["bench"]
    assert set(BASE) <= set(prof["reuse"]["ids"])
    assert set(BASE) <= set(_core_ids(prof))
    # a generated instance never claims stored rows
    assert not [i for i in prof["reuse"]["ids"] if "-core-" in i]


def test_reuse_ids_are_widened_to_the_unchanged_ex_core_and_pack_cases(prof):
    """FABLE-REUSE 3 A5: the 12 unchanged ex-Core cases + LAG1-3, CL4, CL8, CB-C6, TZ19, XP1-2
    (+ the restored CQ1-3 / LRZ1-3 packs). The seven 45.1 rewrites are not reusable."""
    ids = set(prof["reuse"]["ids"])
    want = {"CL7-span-by-example", "CB-F1-larder-bughunt", "RD4-kettle-saddle", "RV2-skarren-hut",
            "WR1-ninth-string", "WR2-red-thread-key", "UX1-tide-cell", "UX3-brass-roast",
            "NC1-tideglass", "NC3-palm-house", "NX2-juniper", "XP3-named-peak", "NW1-salt-and-cedar",
            "LAG1-potkeeper-yearend", "LAG2-seedbank-units", "LAG3-tillroll-reconcile",
            "CL4-workday-mask", "CL8-machine-plan", "CB-C6-predict-node", "TZ19-hotel-cot-deadline",
            "XP1-three-rooms", "XP2-blindfold"}
    assert want <= ids
    rewritten = {"CL5-sheet-rounding", "CL6-number-format", "CB-C5-predict-pyhouse",
                 "TZ20-swim-saturday-age-day", "CC3-tea-hut", "RV1-three-at-the-table", "RD1-marisol"}
    assert not rewritten & ids


def test_core_levels_are_data(prof):
    """Levels live in core_level, never in the instance entries of the active Core. The level of
    each generated family is read off its pinned ACTIVE ids (one place to edit)."""
    import re
    cl = prof["core_level"]
    want = {}
    for i in ALL_ACTIVE:
        m = re.match(r"^([A-Z_]+)(?:-([a-z_]+))?-core-(\d+)-\d+$", i)
        if m:
            fam = m.group(1).lower() + (f"/{m.group(2)}" if m.group(2) else "")
            want[fam] = int(m.group(3))
    assert {k: cl[k] for k in want} == want
    for e in prof["core_generated"]:
        assert "level" not in e, f"{e['family']}: level must come from core_level"


def test_no_core_entry_is_pending_once_its_family_is_registered(prof):
    """`pending: <branch>` holds an entry back only while its family is missing; once the branch
    is merged the key is stale and must go (S10: a7-nsfw merged)."""
    from crucibleforge import gen
    known = set(gen.families())
    stale = [e["family"] for e in prof["core_generated"] if e.get("pending") and e["family"] in known]
    assert stale == []
    assert profiles.core_pending(prof) == []


def test_nominal_tokens_are_reported(rep):
    # the owner R21: the run length is derived and reported, never a hard limit
    assert rep["core"]["tokens"] > 0, rep["core"]["tokens"]


def test_judge_calls_fit(rep):
    assert rep["core"]["judge_calls"] <= JUDGE_CALLS_LIMIT, rep["core"]["judge_calls"]


def test_every_core_item_has_a_budget_source(rep):
    by = {r["id"]: r for r in rep["cases"]}
    assert sorted(by) == sorted(ALL_ACTIVE)
    for i, r in by.items():
        if r["source"] == "NONE":
            # only Admin N's listed items may still lack a committed design target (never silently zero);
            # once N commits one (f0ae933 gave NX2/UX1 an ESTIMATE) they are held to the normal rule below
            assert i in NSFW_NO_TARGET_YET, (i, r["source"])
            continue
        assert r["source"] in ("NOMINAL", "ESTIMATE", "TARGET"), (i, r["source"])
        assert r["tokens"], i


def test_budget_gate_passes_home_and_rig(rep):
    # the owner R21: minutes are reported, not gated (the budget command still prints its verdict)
    assert rep["core_home_min"] > 0 and rep["core_rig_min"] > 0


def test_scores_are_three_tens_and_every_core_item_is_in_a_part(prof):
    cat_of = {r["id"]: r["category"] for r in budget.case_list_ids(prof)}
    pend = {c["id"]: c for c in profiles.core_pending(prof)}
    placed = {}
    for sk, sv in prof["scores"].items():
        assert sum(float(p.get("max", 0)) for p in sv["parts"].values()) == 10, sk
        for pk, p in sv["parts"].items():
            listed = p.get("cases")
            for i in ALL_ACTIVE:
                if isinstance(listed, list):
                    if i in listed:
                        placed.setdefault(i, []).append(f"{sk}/{pk}")
                elif cat_of.get(i) in (p.get("scoring") or []) and float(p.get("max", 0)) > 0:
                    placed.setdefault(i, []).append(f"{sk}/{pk}")   # a part without `cases` scores its categories
    for i in ALL_ACTIVE:
        if i in pend:      # not yet in the part's list: it joins the part when its family exists
            assert pend[i]["part"], i
            continue
        assert len(placed.get(i, [])) == 1, (i, placed.get(i))


def test_alternates_are_off_and_budgeted(prof):
    alts = prof["core_alternates"]
    assert {a["slot"] for a in alts} == {"ledger", "hc1"}      # T7: RV2 is Main; NSFW is pending J7
    for a in alts:
        assert a.get("enabled") is False, a["slot"]
        assert a.get("until"), "an alternate says what turns it on"
    active_families = {e["family"] for e in prof["core_generated"]}
    assert "keyfunc" not in active_families and "keyfunc" not in str(prof["core_alternates"]).lower()
    assert "ledger" not in active_families      # off until a thinking-off measurement exists
    tg = budget.load_targets(ROOT / "budget" / "targets-45.json")
    for a in alts:
        for it in a.get("items", []):
            e = budget.target_entry(tg, it["id"] if isinstance(it, dict) else it)
            assert e and e.get("target_tokens") is not None, a["slot"]


def test_global_cap_is_in_the_profile(prof):
    assert prof["spend"]["global_cap_usd"] == 330


# ---------------------------------------------------------------- the ranked panel

@pytest.fixture(scope="module")
def board41():
    return json.loads((ROOT / "tests" / "fixtures" / "board41_panel.json").read_text(encoding="utf-8"))["models"]


def test_ranked_panel_contains_the_top_nsfw_class(prof, board41):
    pn = profiles.ranked_panel(prof, board41)
    top_nsfw = sorted(board41, key=lambda m: -board41[m]["nsfw"])[:prof["panel"]["nsfw_top"]]
    assert top_nsfw and set(top_nsfw) <= set(pn["ranked"])
    assert set(pn["sfw"]) <= set(pn["ranked"])
    # the NSFW leaders (tunes) are not on the panel by SFW class alone
    assert set(pn["nsfw"]) - set(pn["sfw"]), "the NSFW column must add models the SFW class misses"
    assert len(pn["ranked"]) == len(set(pn["ranked"]))


def test_panel_rule_is_a_union_not_a_choice(prof, board41):
    p2 = copy.deepcopy(prof)
    p2["panel"]["nsfw_top"] = 0
    assert profiles.ranked_panel(p2, board41)["ranked"] != profiles.ranked_panel(prof, board41)["ranked"]


def test_base_only_tier_and_tie_groups_are_declared(prof):
    t = prof["base_only"]
    assert t["label"] and "4.1" in t["label"] and t["scored"] == "base" and t["overall"] is None
    tg = prof["tie_groups"]
    assert tg["unit"] == "points" and tg["interval_z"] == 1.28 and tg["pair_factor"] == "sqrt2"
    assert tg["whole_points"] is True


# ---------------------------------------------------------------- the guard

def test_a_claude_code_row_is_refused_by_the_report(prof, monkeypatch):
    if not hasattr(report, "ClaudeCodeOnBoard"):
        pytest.skip("report.ClaudeCodeOnBoard lives on a8-gates; run after the merge")
    monkeypatch.setattr(profiles, "DEFAULT_PROFILE", "bench45")
    monkeypatch.setattr(report, "board_view", lambda label, cfg: ([{"case_id": "x"}], {"old": False}))
    monkeypatch.setattr(report, "load_meta", lambda label: {"provider_type": "claude_code", "provider": "claudecode"})
    monkeypatch.setattr(report, "_hidden", lambda cfg: None)
    monkeypatch.setattr(report, "_tier_profile", lambda cfg: prof)
    monkeypatch.setattr(report, "tier_model_stats", lambda label, cfg, rows, meta, era: {"meta": meta, "coverage": {}})
    monkeypatch.setattr(report, "_packs_pending_provisional", lambda *a, **k: None)
    monkeypatch.setattr(report, "row_facts", lambda *a, **k: {})
    monkeypatch.setattr(report, "room_stats", lambda *a, **k: {})
    monkeypatch.setattr(report, "_cards", lambda *a, **k: None)
    monkeypatch.setattr(report, "_headline", lambda *a, **k: None)
    with pytest.raises(report.ClaudeCodeOnBoard):
        report.board_stats("cc1", CFG)


# ---------------------------------------------------------------- budget gate: thinking reference

def test_a_thinking_reference_is_not_a_nominal_source(prof):
    """A8 request 1: a digest entry that thinks (thought_share above the limit, or none stated
    when the profile demands it) never produces NOMINAL tokens."""
    cid = "CC4-second-pitch"        # CL3 is pending_from_hash (no NOMINAL by design)
    h = budget._hashes(None)[cid]["case"]
    ref = {"schema": budget.SCHEMA_REFERENCE, "token_basis": "answer", "models": {
        m: {"thought_share": 1.0, "cases": {cid: {"hash": h, "turn_tokens": [5000]}}}
        for m in prof["budget"]["reference_models"]}}
    tg = budget.load_targets(ROOT / "budget" / "targets-45.json")
    r = budget.compute(prof, targets=tg, reference=ref, packs=False)
    row = next(x for x in r["cases"] if x["id"] == cid)
    assert row["source"] != "NOMINAL" and row["tokens"] != 5000
    assert any("thinking" in n for n in r["notes"])
    # the same digest with thinking-off entries IS a nominal source
    for m in ref["models"].values():
        m["thought_share"] = 0.0
    r2 = budget.compute(prof, targets=tg, reference=ref, packs=False)
    assert next(x for x in r2["cases"] if x["id"] == cid)["source"] == "NOMINAL"


def test_digest_model_records_thought_share():
    rows = [{"case_id": "A", "bench_run_id": "r", "round": 1, "run_id": "x", "turn": 0, "repeat": 0,
             "ts": "2026-01-01", "metrics": {"completion_tokens": 100, "reasoning_tokens": 60}}]
    d = budget.digest_model("m", rows, {}, {"A"})
    assert d["thought_share"] == 1.0
    rows[0]["metrics"]["reasoning_tokens"] = 0
    assert budget.digest_model("m", rows, {}, {"A"})["thought_share"] == 0.0


# ---------------------------------------------------------------- A5 hand-over

A5_REUSE_IDS = (
    "CL3-paragraph-wrap, TZ16-pickup-dst-delay, CC4-second-pitch, WL2-lighthouse, NC2-after-hours, "
    "CL7-span-by-example, CB-F1-larder-bughunt, RD4-kettle-saddle, RV2-skarren-hut, WR1-ninth-string, "
    "WR2-red-thread-key, UX1-tide-cell, UX3-brass-roast, NC1-tideglass, NC3-palm-house, NX2-juniper, "
    "XP3-named-peak, NW1-salt-and-cedar, LAG1-potkeeper-yearend, LAG2-seedbank-units, "
    "LAG3-tillroll-reconcile, CL4-workday-mask, CL8-machine-plan, CB-C6-predict-node, "
    "TZ19-hotel-cot-deadline, XP1-three-rooms, XP2-blindfold, P1-tiny, P2-short, P3-gen, P4-longctx, "
    "CQ1-format-spec-from-scratch, CQ2-regex-engine-from-scratch, CQ3-spreadsheet-engine, "
    "LRZ1-cell-row-trace, LRZ2-tariff-ledger-deduction, LRZ3-nine-cottages-mixed-clues").split(", ")


def test_reuse_ids_are_exactly_the_a5_list(prof):
    assert len(A5_REUSE_IDS) == 37
    assert sorted(prof["reuse"]["ids"]) == sorted(A5_REUSE_IDS)


def test_the_twenty_leavers_are_out_of_core(prof):
    # rc1 (Admin N, EXP-20261113-21): UX1, NC1, NC3 and NX2 came back as Main NSFW mn1-mn5
    leavers = {"CL5-sheet-rounding", "CL6-number-format", "CL7-span-by-example", "CB-C5-predict-pyhouse",
               "CB-F1-larder-bughunt", "TZ20-swim-saturday-age-day", "RD1-marisol", "RV1-three-at-the-table",
               "CC3-tea-hut", "WR1-ninth-string", "WR2-red-thread-key",
               "UX3-brass-roast", "XP3-named-peak",
               "NW1-salt-and-cedar", "RD4-kettle-saddle"}
    assert not leavers & set(_core_ids(prof))


def test_base_items_without_standard_45_are_pending_in_the_targets():
    """CL3, TZ16, WL2 deliberately lack element_standard 45 (stamping moves their hash and strands
    their stored rows): their targets say pending_from_hash, which exempts them from the lint."""
    from crucibleforge import element_lint, version
    tg = budget.load_targets(ROOT / "budget" / "targets-45.json")["cases"]
    for i in ("CL3-paragraph-wrap", "TZ16-pickup-dst-delay", "WL2-lighthouse", "RD4-kettle-saddle"):
        assert tg[i]["pending_from_hash"] == version.case_hash(i), i      # RD4: the 4.1 text, regroup is A5's request
    assert {"CL3-paragraph-wrap", "TZ16-pickup-dst-delay", "WL2-lighthouse", "RD4-kettle-saddle"} <= \
        element_lint.pending_ids(profiles.load_profile("bench45", CFG))


def test_classic_packs_are_discovered_and_numbered_by_name(tmp_path):
    """ladder.py: classic-<name>.json is a pack; its n is a pure function of the file name."""
    import zlib

    from crucibleforge import ladder
    assert ladder._SET.match("set-3.json").group(1) == "3"
    m = ladder._SET.match("classic-nsfw_writing.json")
    assert m and m.group(1) is None
    assert ladder._SET.match("classic-deep_rp-451.json")
    assert not ladder._SET.match("classic-.json") and not ladder._SET.match("notes.json")
    d = tmp_path / "chat" / "deep_rp"
    d.mkdir(parents=True)
    spec = {"level": 1, "cases": []}
    for name in ("classic-deep_rp.json", "set-2.json"):
        (d / name).write_text(json.dumps(spec), encoding="utf-8")
    found = {f.name: n for key, n, f in ladder._set_files([tmp_path])}
    assert found["set-2.json"] == 2
    assert found["classic-deep_rp.json"] == ladder.CLASSIC_N0 + zlib.crc32(b"classic-deep_rp") % 900000
    # adding a sibling never renumbers it
    (d / "classic-deep_rp-451.json").write_text(json.dumps(spec), encoding="utf-8")
    again = {f.name: n for key, n, f in ladder._set_files([tmp_path])}
    assert again["classic-deep_rp.json"] == found["classic-deep_rp.json"]


# ---------------------------------------------------------------- pack unlock by the owning Core score

FRONTIER_OWNER = {"work/frontier": "work", "chat/frontier": "chat", "work/reasoning": "work",
                  "chat/character": "chat", "chat/writing": "chat", "chat/deep_rp": "chat"}


def test_no_pack_hangs_on_a_zero_max_part_without_unlock_part(prof):
    """A pack on a part that carries no Core points would unlock off an empty standard share
    (never, or off nothing). Every such part names the Core score that owns its unlock."""
    from crucibleforge import ladder
    parts = {f"{sk}/{pk}": p for sk, sv in prof["scores"].items() for pk, p in sv["parts"].items()}
    attached = ladder.discover(prof)
    for key in attached:
        p = parts[key]
        if float(p.get("max", 0)) == 0:
            assert p.get("unlock_part") in prof["scores"], (key, "has packs but no unlock_part")
    for key, owner in FRONTIER_OWNER.items():
        assert parts[key]["max"] == 0 and parts[key]["unlock_part"] == owner


def _card(prof, share):
    def fake(src, w, partial=frozenset()):
        out = {"chat": None, "coding": None, "total": None, "tok_per_s": None,
               "components": {}, "weights": dict(w)}
        out.update({g: share for g in w})
        return out
    mp = pytest.MonkeyPatch()
    mp.setattr(report, "scorecard", fake)
    try:
        sc = report._declared_weights(prof, None, None)[0]
        return report.points_scorecard({}, sc, prof)
    finally:
        mp.undo()


@pytest.mark.live_ladder
def test_core_ratio_090_unlocks_the_frontier_packs_and_089_does_not(prof):   # unlock 0.9, the owner 2026-11-14
    from crucibleforge import ladder
    attached = ladder.discover(prof)
    frontier = [k for k in FRONTIER_OWNER if k in attached]
    assert frontier, "this tree has packs on the Frontier parts"
    hit = _card(prof, 90.0)["ladder_unrun"]
    miss = _card(prof, 89.0).get("ladder_unrun") or {}
    for k in frontier:
        assert hit.get(k, 0) >= 1, (k, hit)
        assert not miss.get(k), (k, miss)


@pytest.mark.live_ladder
def test_a_frontier_part_unlocks_off_its_owner_not_its_own_share(prof):
    """Owner score at 95 but the part's own (empty) standard share is None: still unlocks."""
    assert _card(prof, 95.0)["ladder_unrun"].get("work/reasoning", 0) >= 1


@pytest.mark.live_ladder
def test_a_scored_frontier_pack_adds_its_points(prof):
    """S10 review 4: an unlocked Frontier pack (part max 0, no standard share of its own) that
    has a share must add its pack points to the part and to the score; it was dropped
    (pts None) because the part's own share is None."""
    from crucibleforge import ladder
    seq = report._ladder_sets(prof).get("work/reasoning") or []
    assert seq, "work/reasoning has packs"
    plain = [x for x in seq if not x.get("rungs")]           # a hand-written (classic) pack
    assert plain
    stats = {"ladder_stats": {f"work/reasoning#{x['n']}": {"n": 1} for x in plain}}

    def fake(src, w, partial=frozenset()):
        out = {"chat": None, "coding": None, "total": None, "tok_per_s": None,
               "components": {}, "weights": dict(w)}
        out.update({g: 95.0 for g in w})
        return out
    mp = pytest.MonkeyPatch()
    mp.setattr(report, "scorecard", fake)
    try:
        sc = report._declared_weights(prof, None, None)[0]
        card = report.points_scorecard(stats, sc, prof)
    finally:
        mp.undo()
    det = card["ladder"]["work"]["reasoning"]
    scored = [x for x in det["sets"] if x["state"] == "scored"]
    assert scored and all(x["share"] == pytest.approx(0.95) for x in scored)
    assert card["parts"]["work"]["reasoning"] == pytest.approx(sum(x["share"] for x in scored))
    # (T7: the Main parts list their cases, so a synthetic card without per-part rows has no standard share)


def test_pack_new_numbers_after_set_files_only(tmp_path):
    """S10 review 7: a classic pack's crc number is not a sequence position; `pack new` takes
    the next set-<n> (not max(crc)+1)."""
    from crucibleforge import ladder
    prof = profiles.load_profile("bench45", CFG)
    d = tmp_path / "work" / "code"
    d.mkdir(parents=True)
    (d / "set-1.json").write_text(json.dumps({"level": 1, "cases": ["x"]}), encoding="utf-8")
    (d / "classic-code.json").write_text(json.dumps({"level": 1, "classic_fixed": True,
                                                     "cases": ["y"]}), encoding="utf-8")
    p = ladder.new_pack(prof, "work/code", root=tmp_path)
    assert p.name == "set-2.json"


def test_classic_pack_numbers_are_unique_per_part():
    from crucibleforge import ladder
    seen = {}
    for key, n, f in ladder._set_files():
        assert (key, n) not in seen, (f, seen.get((key, n)))
        seen[(key, n)] = f


def test_the_41_report_has_no_unlock_part_key():
    """S10 (board_ab G-41Freeze): unlock_part is emitted only on a part that sets it, never as
    null, so the 4.1 bench report (scorecard.score_defs) keeps its exact shape."""
    from crucibleforge import tiers
    blob = json.dumps(tiers.scores(profiles.load_profile("bench", CFG)), default=str)
    assert "unlock_part" not in blob
    blob45 = json.dumps(tiers.scores(profiles.load_profile("bench45", CFG)), default=str)
    assert '"unlock_part": null' not in blob45 and '"unlock_part": "work"' in blob45
