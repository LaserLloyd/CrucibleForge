"""Owner 2026-11-25 (R37/R38): bonus pack points add on top and count in the rank (totals45.py, report._apply_totals45).

Code total = Code standard + Code packs, Chat likewise, Overall = Code total + Chat total; the maximum with packs is
derived from the packs that exist; the rank and the ties are on the totals; a model with no pack point is unchanged;
the adult page's packs stay inside NSFW / Uncensored and never reach Overall; suite 4.1's stored example reproduces.
"""
from __future__ import annotations

from crucibleforge import accuracy45 as A
from crucibleforge import display45
from crucibleforge import headline as H
from crucibleforge import report
from crucibleforge import resolution45
from crucibleforge import totals45 as T

ST = A.settings(None)


def _pk(part, pts, mx=1.0, state=T.EARNED, kind="ladder", rng=None):
    return {"part": part, "name": part.split("/")[-1], "kind": kind, "unlock": 0.9, "figure": 0.95,
            "state": state, "points": pts, "max": mx, **({"range": rng} if rng is not None else {})}


def _model(code, chat, code_packs=(), chat_packs=(), half=0.2, adult=None):
    """A stats entry as _apply_segments45 leaves it: the score tree, the pack lists, the standard ranges."""
    cp, hp = sum(p["points"] for p in code_packs), sum(p["points"] for p in chat_packs)
    s = {"segments45": {"code": {"points": code, "max": 10.0, "pack_points": cp},
                        "chat": {"points": chat, "max": 10.0, "pack_points": hp},
                        "overall": {"points": code + chat, "max": 20.0, "pending": None}},
         "packs45_list": {"code": list(code_packs), "chat": list(chat_packs)},
         "accuracy45": {k: A.block(half * (2 if k == "overall" else 1), A.SECTION_MAX[k], "pooled", ST)
                        for k in ("code", "chat", "overall")},
         "headline": {"S": {"work": code + cp, "chat": chat + hp, "overall": code + chat},
                      "P": {"work": code + cp, "chat": chat + hp, "overall": code + chat},
                      "T1": {"work": code, "chat": chat, "overall": code + chat}, "sort": code + chat,
                      "accuracy": {}}}
    if adult:
        s["nsfw_plus"] = adult
    return s


def _apply(s, label="m", rtr=None):
    report._apply_totals45(label, s, None, rtr)
    return s


def test_the_41_example_reproduces_totals_and_maxima():
    # suite 4.1's stored board line: Total 24.6 /28 = 19.3 + 5.3 bonus; Chat 12.6 /15 = 9.3 + 3.3; Code 12.0 /13
    code = [_pk("work/a", 1.0), _pk("work/b", 1.0), _pk("work/c", 0.0, state=T.LOCKED)]
    chat = [_pk("chat/x", 1.0), _pk("chat/y", 1.3, mx=2.0, kind="chat"), _pk("chat/z", 1.0, mx=2.0, kind="chat")]
    s = _apply(_model(10.0, 9.3, code, chat))
    t = s["totals45"]
    assert (t["code"]["standard"], t["code"]["bonus"], t["code"]["total"]) == (10.0, 2.0, 12.0)
    assert (t["chat"]["standard"], t["chat"]["bonus"], round(t["chat"]["total"], 4)) == (9.3, 3.3, 12.6)
    assert t["code"]["max_with_packs"] == 13.0 and t["chat"]["max_with_packs"] == 15.0
    ov = t["overall"]
    assert (round(ov["standard"], 4), round(ov["bonus"], 4), round(ov["total"], 4)) == (19.3, 5.3, 24.6)
    assert ov["max"] == 20.0 and ov["max_with_packs"] == 28.0
    hl = s["headline"]
    assert hl["S"]["overall"] == 24.6 and hl["sort"] == 24.6 and hl["T1"]["overall"] == 19.3
    assert s["segments45"]["overall"]["total"] == 24.6
    # the split is printed beside the total, and both ranges are labelled
    cell = report.headline_cell(hl, "overall")
    assert cell.startswith("**24**.6 of 28 ±") and "= 19.3 standard ±" in cell and "+ 5.3 bonus" in cell
    assert display45.section_cell(s["segments45"]["code"]).startswith("12.0 of 13 ±")


def test_the_maximum_counts_every_existing_pack_not_only_opened_ones():
    packs = [_pk("work/a", 0.0, state=T.LOCKED), _pk("work/b", 0.0, mx=3.0, state=T.LOCKED)]
    g = T.group_total(7.0, 10.0, packs)
    assert g["max_with_packs"] == 14.0 and g["total"] == 7.0 and g["bonus"] == 0.0


def test_bonus_changes_the_rank():
    a = _apply(_model(9.0, 8.0), "a")                                      # 17.0 standard, no packs
    b = _apply(_model(8.8, 7.9, [_pk("work/a", 1.0)], [_pk("chat/x", 0.6)]), "b")   # 16.7 standard + 1.6
    assert a["headline"]["T1"]["overall"] > b["headline"]["T1"]["overall"]
    stats = {"a": a, "b": b}
    assert report.rank_labels(["a", "b"], stats) == ["b", "a"]


def test_ties_are_decided_on_the_totals():
    # identical spread; standard 0.2 apart (a tie), but one has 2 bonus points: no tie on the totals
    a = _apply(_model(9.0, 8.0), "a")
    b = _apply(_model(8.9, 7.9, [_pk("work/a", 1.0), _pk("work/b", 1.0)]), "b")
    noise = [((i * 37) % 11 - 5) / 10 for i in range(200)]
    other = [((i * 53) % 13 - 6) / 10 for i in range(200)]          # independent draws: a real paired spread
    boots = {"a": {"samples": {"overall": [17.0 + x for x in noise]}},
             "b": {"samples": {"overall": [16.8 + x for x in other]}}}
    stats = {"a": a, "b": b}
    order = report.rank_labels(["a", "b"], stats)
    s_pt = {l: stats[l]["headline"]["P"]["overall"] for l in order}
    lab_std, _ = H.rank_groups(order, boots, s_pt)
    lab_tot, _ = H.rank_groups(order, report._total_boots45(order, boots, stats), s_pt)
    assert lab_std == {"b": "1=", "a": "1="}            # the old samples tie them
    assert lab_tot == {"b": "1", "a": "2"}              # 18.8 vs 17.0 on the totals separates them
    # two models with the same total tie whatever their split
    c = _apply(_model(9.5, 7.3), "c")                    # 16.8 standard, no packs
    d = _apply(_model(8.8, 7.0, [_pk("work/a", 1.0)]), "d")   # 15.8 + 1.0 = 16.8
    boots2 = {"c": {"samples": {"overall": [16.8 + x for x in noise]}},
              "d": {"samples": {"overall": [15.8 + x for x in other]}}}
    st2 = {"c": c, "d": d}
    lab, _ = H.rank_groups(["c", "d"], report._total_boots45(["c", "d"], boots2, st2),
                           {l: st2[l]["headline"]["P"]["overall"] for l in ("c", "d")})
    assert lab == {"c": "1=", "d": "1="}


def test_a_model_without_pack_points_is_unchanged():
    s = _model(9.1, 8.3)
    before_cell = display45.section_cell({**s["segments45"]["code"], "accuracy": s["accuracy45"]["code"]})
    _apply(s)
    t = s["totals45"]
    assert t["overall"]["total"] == t["overall"]["standard"] == 17.4
    assert s["headline"]["S"]["overall"] == 17.4 and s["headline"]["sort"] == 17.4
    code = {**s["segments45"]["code"], "accuracy": s["accuracy45"]["code"]}
    assert display45.section_cell(code) == before_cell
    assert "bonus" not in report.headline_cell(s["headline"], "overall")


def test_adult_packs_stay_in_their_own_numbers_and_never_reach_overall():
    from crucibleforge import nsfwpack45 as N
    nsfw_pk = next(p for p, g in N.GATE_OF.items() if not str(g[0]).startswith("unc"))
    unc_pk = next(p for p, g in N.GATE_OF.items() if str(g[0]).startswith("unc"))
    adult = {"numbers": {"nsfw": {"value": 6.0, "of": 10}, "uncensored": {"value": 7.0, "of": 10}},
             "total": {"value": 13.0},
             "packs45": {"board": {nsfw_pk: {"state": "scored", "points": 0.8, "gate_share": 0.93},
                                   unc_pk: {"state": "scored", "points": 0.5, "gate_share": 0.95}}},
             "accuracy": {}}
    s = _apply(_model(9.0, 8.0, adult=adult))
    assert s["totals45"]["overall"]["total"] == 17.0 and s["headline"]["sort"] == 17.0
    npt = s["nsfw_plus"]["totals45"]
    assert npt["nsfw"]["total"] == 6.8 and npt["uncensored"]["total"] == 7.5
    assert round(npt["nsfw_plus"]["total"], 4) == 14.3
    n_max = sum(1 for g in N.GATE_OF.values() if not str(g[0]).startswith("unc"))
    assert npt["nsfw"]["max_with_packs"] == 10.0 + n_max
    assert report.np_cell(s, "nsfw").startswith("6.8 of ")


def test_an_unlocked_pack_not_run_counts_zero_is_owed_and_the_row_still_ranks():
    s = _apply(_model(9.0, 8.0, [_pk("work/a", 0.0, state=T.NOT_RUN)]))
    assert s["totals45"]["code"]["owed"] == ["work/a (a)"]
    assert s["headline"]["sort"] == 17.0                     # ranked on what is measured
    assert any("owed" in n for n in s["coverage"]["notes"])


def test_total_range_from_replicate_totals_and_from_quadrature():
    packs = [_pk("chat/x", 1.0, mx=2.0, kind="chat", rng=0.3)]
    std = A.block(0.4, 10.0, "pooled", ST)
    q = T.total_range("chat", std, packs, 12.0, None, None, ST)
    assert abs(q["half"] - 0.5) < 1e-9 and q["max"] == 12.0 and "quadrature" in q["basis"]
    rtr = {"n": {"m": {"chat_total": 5}}, "by_model": {"m": {"chat_total": 0.2}}, "pooled": {}}
    r = T.total_range("chat", std, packs, 12.0, "m", rtr, ST)
    assert r["source"] == A.REPLICATES_SRC and abs(r["half"] - A.t90(4) * 0.2 / 5 ** 0.5) < 1e-3
    # a pack with no range of its own is named, never silently zero
    q2 = T.total_range("code", std, [_pk("work/a", 1.0)], 11.0, None, None, ST)
    assert "no pack range measured for a" in q2["note"]


def test_each_run_reports_its_totals_for_the_run_to_run_spread():
    pts = resolution45.report_points({"measured": {"code": {"points": 9.0, "pack_points": 1.5},
                                                   "chat": {"points": 8.0, "pack_points": 0.5}}})
    assert pts["code_total"] == 10.5 and pts["chat_total"] == 8.5 and pts["overall_total"] == 19.0


def test_public_fields_carry_the_totals_and_keep_the_old_names():
    s = _apply(_model(10.0, 9.3, [_pk("work/a", 1.0)], [_pk("chat/x", 1.0)]))
    for sec in ("code", "chat"):
        s["segments45"][sec].update({"label": sec, "measured_max": 10.0, "pending_points": 0, "segments": {}})
    s["segments45"]["overall"].update({"measured_points": 19.3, "measured_max": 20.0})
    m = report.seg45_measured(s)
    assert m["code"]["points"] == 10.0 and m["code"]["total"] == 11.0 and m["code"]["standard_points"] == 10.0
    assert m["overall"]["total"] == 21.3 and m["overall"]["max_with_packs"] == 22.0
    assert m["overall"]["points"] == 19.3                          # old name, standard points, one release
    assert {"standard_accuracy", "total_accuracy", "packs", "packs_owed", "bonus_points"} <= set(m["chat"])
    # the public headline agrees with the cells: S, P and m are the totals and the total's range
    hl = s["headline"]
    assert hl["S"]["overall"] == 21.3 and hl["m"]["overall"] == hl["accuracy"]["overall"]["half"]


def test_routed_tie_test_reads_the_totals_the_rank_sorts_on():
    """The 4.5 board ranks with _rank45 (scale.tie per section): the gap and its SE must be on the totals, not on
    the standard scale, or a model ranked 3 points higher on its total still reads as tied (found on the
    before/after board, 2026-11-26)."""
    t = {"gap": 2.0, "se_diff": 4.0, "tied": True}                 # standard: 0.2 points apart, SE 0.4 -> tied
    std = A.block(0.31, 10.0, "pooled", ST)

    def g(bonus, half):
        return {"bonus_points": bonus, "accuracy": std, "total_accuracy": A.block(half, 17.0, "pooled", ST)}

    sep = report._tie45(t, {"total45": {"work": g(3.0, 0.5)}}, {"total45": {"work": g(0.0, 0.31)}}, "work")
    assert not sep["tied"] and abs(sep["gap_total"] - 3.2) < 1e-9
    # wide pack ranges keep a small total gap tied
    tie = report._tie45(t, {"total45": {"work": g(0.5, 1.6)}}, {"total45": {"work": g(0.0, 0.31)}}, "work")
    assert tie["tied"]
    # no totals on either row: scale.tie unchanged
    assert report._tie45(t, {}, {}, "work") is t


# ---------------------------------------------------------------- coordinator 2026-11-26: ranks without chaining


def _ranks(totals, halves, samples=None):
    order = sorted(totals, key=lambda l: -totals[l])
    return T.clear_ranks(order, samples or {}, totals, halves, 0.9)


def _pair_separable(a, b, totals, halves):
    return abs(totals[a] - totals[b]) > (halves[a] ** 2 + halves[b] ** 2) ** 0.5


def test_ties_never_chain_from_22_6_down_to_16_7():
    # the after-board shape: neighbours ~1 point apart, ranges ~±1.0; a chained rule made all of them "4="
    totals = {"ds": 22.6, "luna": 21.7, "glm": 20.7, "ref": 20.2, "arete": 19.9, "s27": 18.8, "s35": 17.9,
              "her": 16.8, "mm": 16.7}
    halves = {k: 1.0 for k in totals}
    r = _ranks(totals, halves)
    lab = r["labels"]
    assert lab["ds"] != lab["mm"]
    # "A= with B" implies the pair itself is not separable, never through a third model
    for a in totals:
        for b in totals:
            if a != b and lab[a] == lab[b]:
                assert not _pair_separable(a, b, totals, halves), (a, b, lab)
    # place = 1 + the models clearly better (before the running maximum); the printed place never improves downwards
    order = sorted(totals, key=lambda l: -totals[l])
    nums = [int(lab[l].rstrip("=")) for l in order]
    assert nums == sorted(nums) and nums[0] == 1
    assert all(int(lab[l].rstrip("=")) >= 1 + len(r["better"][l]) for l in order)
    assert r["methods"] == ["ranges"]


def test_a_noisy_row_never_prints_a_better_place_than_the_row_above():
    totals = {"a": 25.0, "b": 22.0, "c": 21.9}
    halves = {"a": 0.3, "b": 0.3, "c": 5.0}          # c is so noisy that nothing is clearly better than it
    lab = _ranks(totals, halves)["labels"]
    assert lab["a"] == "1" and lab["b"] == "2=" and lab["c"] == "2="   # c alone would be place 1


def test_a_row_separable_from_its_running_place_takes_the_next_free_place(monkeypatch):
    # bootstrap pairing can make "clearly better" non-transitive: x beats a and e, a is noisy (beats no one),
    # e beats l, x does not beat l. l alone would be place 2 = e's place, but e is clearly better than l.
    beats = {("x", "a"), ("x", "e"), ("e", "l")}
    monkeypatch.setattr(T, "clearly_better", lambda a, b, *args: ((a, b) in beats, "boot"))
    order = ["x", "a", "e", "l"]
    lab = T.clear_ranks(order, {}, {k: 1.0 for k in order}, {}, 0.9)["labels"]
    assert lab["x"] == "1" and lab["a"] == "2=" and lab["e"] == "2="
    assert lab["l"] == "4"                             # never "2=" beside e, which is clearly better than it


def test_the_paired_bootstrap_is_used_where_samples_exist():
    import random
    rnd = random.Random(3)
    base = [rnd.gauss(0, 0.3) for _ in range(400)]
    samples = {"a": [20.0 + x for x in base], "b": [19.0 + x for x in base]}   # shared noise: paired diff exact
    r = T.clear_ranks(["a", "b"], samples, {"a": 20.0, "b": 19.0}, {"a": 3.0, "b": 3.0}, 0.9)
    assert r["labels"] == {"a": "1", "b": "2"} and r["methods"] == ["boot"]
    assert "paired bootstrap" in T.rank_note(r["methods"])


def test_total_samples_keep_pairing_and_add_pack_noise_only_with_packs():
    xs = [10.0, 10.5, 9.5, None]
    assert T.total_samples("m", xs, 12.0, 0.0) == [12.0, 12.5, 11.5, None]
    noisy = T.total_samples("m", xs, 12.0, 1.0)
    assert noisy == T.total_samples("m", xs, 12.0, 1.0) and noisy[:3] != [12.0, 12.5, 11.5]
    std = A.block(0.31, 20.0, "pooled", ST)
    assert T.pack_sd(A.block(0.31, 34.0, "pooled", ST), std) == 0.0
    assert abs(T.pack_sd(A.block(0.5, 34.0, "pooled", ST), A.block(0.3, 20.0, "p", ST)) - 0.4 / A.Z90) < 1e-9


def test_a_model_with_no_pack_point_keeps_its_standard_range():
    std = A.block(0.31, 20.0, "pooled", ST)
    # a pooled TOTAL spread (the replicated models' pack-gate flips) is never applied to a model without packs
    rtr = {"n": {"m": {"overall_total": 1}}, "by_model": {}, "pooled": {"overall_total": 0.633, "code_packs": 0.35,
                                                                        "chat_packs": 0.5},
           "pooled_df": {"overall_total": 4, "code_packs": 4, "chat_packs": 4},
           "pooled_by": {"overall_total": ["x", "y", "z"], "code_packs": ["x"], "chat_packs": ["x"]}}
    r0 = T.total_range("overall", std, [], 34.0, "m", rtr, ST, bonus=0.0)
    assert r0["half"] == 0.31 and "no pack noise" in r0["basis"]
    # chat packs only: the standard range and the chat packs' spread, not the code packs'
    r1 = T.total_range("overall", std, [], 34.0, "m", rtr, ST, bonus=1.0, pack_keys=["chat_packs"])
    want = (0.31 ** 2 + (A.t90(4) * 0.5) ** 2) ** 0.5
    assert abs(r1["half"] - want) < 1e-3 and "chat_packs" in r1["basis"]
    # five or more own runs: the model's own replicate totals
    rtr5 = {**rtr, "n": {"m": {"overall_total": 5}}, "by_model": {"m": {"overall_total": 0.2}}}
    r5 = T.total_range("overall", std, [], 34.0, "m", rtr5, ST, bonus=1.0)
    assert r5["source"] == A.REPLICATES_SRC


def test_total_points_alias_and_clearly_better_are_public():
    g = {"standard": 10.0, "bonus": 1.0, "total": 11.0, "max": 10.0, "max_with_packs": 17.0}
    assert report.totals_fields45(g)["total_points"] == 11.0
    assert "clearly_better" in report._HL_KEEP


def test_older_suites_keep_their_bootstrap_samples():
    boots = {"a": {"samples": {"overall": [1.0, 2.0]}}}
    assert report._total_boots45(["a"], boots, {"a": {}}) == boots
