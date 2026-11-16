"""Unit tests for tools/nsfwv2 (item stats, kappa, bootstrap, re-judge stability,
gate, authorship ledger and the report's own-prose exclusion). Synthetic fixtures
only: ids and numbers, no case text."""
from __future__ import annotations

import json
import random
from pathlib import Path

import pytest

from crucibleforge import report
from tools.nsfwv2 import _common as cm
from tools.nsfwv2 import authorship as au
from tools.nsfwv2 import bootstrap as bs
from tools.nsfwv2 import gate, itemstats, kappa, rejudge_stability


@pytest.fixture(autouse=True)
def _fresh_case_cache(monkeypatch):
    monkeypatch.setattr(report, "_CASES_BY_ID", None)
    yield


def make_row(case, rep, model, w, items, samples=3, bench="b1"):
    """items: {eid: pass | (pass, n_yes_samples)}"""
    results = []
    for eid, v in items.items():
        ps, nyes = (v, None) if not isinstance(v, tuple) else v
        e = {"id": eid, "type": "judge", "pass": bool(ps), "credit": 1.0 if ps else 0.0}
        if nyes is not None:
            e["verified_samples"] = list(range(nyes))
        results.append(e)
    return {"category": "nsfw", "case_id": case, "repeat": rep, "bench_run_id": bench,
            "model_id": model, "model_label": model, "rubric": "nsfw_craft", "response": "x",
            "judge_model": "judge-x", "checks": {"results": []},
            "judge": {"samples": samples, "refused": False, "willingness": {"w": w, "votes": [w] * samples},
                      "elements": {"results": results}}}


def write_dir(path: Path, rows_by_model: dict):
    path.mkdir(parents=True, exist_ok=True)
    for m, rows in rows_by_model.items():
        with open(path / f"transcripts_{m}.jsonl", "w", encoding="utf-8") as fh:
            for r in rows:
                fh.write(json.dumps(r) + "\n")
    return path


def synth(models=("m1", "m2", "m3", "m4"), cases=("LU2-1",), reps=(1, 2), seed=1):
    """Models of rising ability; 'good' tracks ability, 'dead' always passes,
    'never' never passes, 'noise' is random."""
    rng = random.Random(seed)
    out = {}
    for mi, m in enumerate(models):
        rows = []
        for c in cases:
            for rep in reps:
                ab = (mi + rng.random()) / len(models)
                items = {f"a{k}": ab > (k + 0.5) / 6 for k in range(6)}
                items["dead"] = True
                items["never"] = False
                items["noise"] = rng.random() < 0.5
                rows.append(make_row(c, rep, m, 4, items))
        out[m] = rows
    return out


# ------------------------------------------------------------------ pack ids
def test_nsfwv2_pack_of_id_fallback_and_sets():
    assert cm.pack_of("LU2-3") == "P1" and cm.pack_of("LU3-3") == "P2"
    assert cm.pack_of("LW3-1") == "P1" and cm.pack_of("LW4-1") == "P2"
    assert cm.pack_of("LR2-2") == "P1" and cm.pack_of("LR3-2") == "P2"
    assert cm.pack_of("ZZ") is None
    assert cm.pack_of("LU2-1", {"LU2-1": {"pack": 2}}) == "P2"


# ----------------------------------------------------------------- itemstats
def test_nsfwv2_itemstats_flags_and_numbers(tmp_path):
    d = write_dir(tmp_path / "r", synth(reps=range(1, 6)))
    data = cm.load_dirs([d])
    t = itemstats.item_table(data, min_models=3)
    it = t["LU2-1"]["items"]
    assert it["dead"]["flags"] == ["dead"] and it["dead"]["p"] == 1.0
    assert "dead" in it["never"]["flags"] and it["never"]["p"] == 0.0
    assert it["dead"]["r_it"] is None
    assert it["a2"]["r_it"] > 0.2 and "dead" not in it["a2"]["flags"]
    assert it["a2"]["hi_lo"] > 0.3            # top half of models passes it more often
    assert it["a2"]["n"] == 20
    # low_disc only fires with enough models
    t8 = itemstats.item_table(data, min_models=8)
    assert all("low_disc" not in v["flags"] for v in t8["LU2-1"]["items"].values())


def test_nsfwv2_itemstats_low_disc_and_negative(tmp_path):
    rows = {}
    rng = random.Random(3)
    for mi, m in enumerate(["a", "b", "c", "d"]):
        rs = []
        for rep in range(8):
            ab = mi + rng.random()
            items = {f"g{k}": ab > k * 0.8 for k in range(5)}
            items["bad"] = not (ab > 2)           # anti-correlated key
            rs.append(make_row("LU2-1", rep, m, 4, items))
        rows[m] = rs
    t = itemstats.item_table(cm.load_dirs([write_dir(tmp_path / "r", rows)]), min_models=4)
    assert "neg_rit" in t["LU2-1"]["items"]["bad"]["flags"]
    assert "low_disc" in t["LU2-1"]["items"]["bad"]["flags"]


def test_nsfwv2_itemstats_unstable_and_engaged_only(tmp_path):
    rows = {"m": []}
    for rep in range(10):
        rows["m"].append(make_row("LU2-1", rep, "m", 4, {"split": (True, 2), "firm": (True, 3), "x": rep % 2 == 0}))
    rows["m"].append(make_row("LU2-1", 99, "m", 0, {"split": (False, 0), "firm": (False, 0), "x": False}))  # refusal row
    t = itemstats.item_table(cm.load_dirs([write_dir(tmp_path / "r", rows)]))
    its = t["LU2-1"]["items"]
    assert "unstable" in its["split"]["flags"] and "unstable" not in its["firm"]["flags"]
    assert its["firm"]["p"] == 1.0 and its["firm"]["n"] == 10      # the W0 row is not counted


def test_nsfwv2_itemstats_cli_json(tmp_path, capsys):
    d = write_dir(tmp_path / "r", synth())
    out = tmp_path / "items.json"
    assert itemstats.main([str(d), "--min-models", "3", "--out", str(out), "--sets-dir", str(tmp_path / "none")]) == 0
    js = json.loads(out.read_text(encoding="utf-8"))
    assert js["models"] == ["m1", "m2", "m3", "m4"] and "LU2-1" in js["cases"]
    assert "# Item statistics" in capsys.readouterr().out


# --------------------------------------------------------------------- kappa
def test_nsfwv2_cohen_and_fleiss_known_values():
    k, po, n = kappa.cohen([(1, 1)] * 4 + [(0, 0)] * 4 + [(1, 0)] + [(0, 1)])
    assert n == 10 and po == pytest.approx(0.8) and k == pytest.approx(0.6)
    assert kappa.cohen([(1, 1)] * 5)[0] is None            # constant raters: undefined
    assert kappa.cohen([])[2] == 0
    kf, N = kappa.fleiss([[1, 1, 1], [0, 0, 0], [1, 1, 1], [0, 0, 0]])
    assert N == 4 and kf == pytest.approx(1.0)
    kf2, _ = kappa.fleiss([[1, 0, 1], [0, 1, 0], [1, 1, 0], [0, 0, 1]])
    assert kf2 < 0.1
    assert kappa.band(0.3) == "low" and kappa.band(0.5) == "mid" and kappa.band(0.6) == "ok" and kappa.band(None) == "undefined"


def _kappa_dirs(tmp_path, flips=2):
    a = {"m": [], "n": []}
    b = {"m": [], "n": []}
    for m in a:
        for rep in range(1, 11):
            ia = {"e1": rep % 2 == 0, "e2": rep < 6}
            ib = dict(ia)
            if rep <= flips:
                ib["e1"] = not ib["e1"]
            a[m].append(make_row("LU2-1", rep, m, 4, {k: (v, 3 if v else 0) for k, v in ia.items()}))
            b[m].append(make_row("LU2-1", rep, m, 4, ib))
    return write_dir(tmp_path / "a", a), write_dir(tmp_path / "b", b)


def test_nsfwv2_kappa_pairs_by_row_and_element(tmp_path):
    da, db = _kappa_dirs(tmp_path)
    res = kappa.compare(cm.load_dirs([da]), cm.load_dirs([db]), min_pairs=10)
    assert res["paired_rows"] == 20 and res["paired_decisions"] == 40
    e2 = res["per_element"]["LU2-1"]["e2"]
    assert e2["n"] == 20 and e2["kappa"] == 1.0 and e2["band"] == "ok"
    e1 = res["per_element"]["LU2-1"]["e1"]
    assert e1["agree"] == pytest.approx(0.8) and e1["kappa"] < 1.0
    assert res["per_case"]["LU2-1"]["n"] == 40
    assert res["per_pack"]["P1"]["n"] == 40
    # fleiss: A has per-sample votes, B (no verified_samples) does not
    assert res["fleiss_a"]["pooled"]["n_rows"] > 0 and res["fleiss_b"]["pooled"]["n_rows"] == 0
    # default min_pairs 40: nothing rateable singly
    r40 = kappa.compare(cm.load_dirs([da]), cm.load_dirs([db]))
    assert r40["rated_elements"] == 0 and r40["per_element"]["LU2-1"]["e1"]["band"] == "insufficient"


def test_nsfwv2_kappa_subset_and_sample_list(tmp_path):
    da, _ = _kappa_dirs(tmp_path)
    full = cm.load_dirs([tmp_path / "b"])
    sub = {m: [r for r in rows if r["rep"] <= 4] for m, rows in full.items()}     # B judged only a subset
    res = kappa.compare(cm.load_dirs([da]), sub, min_pairs=1)
    assert res["paired_rows"] == 8
    res2 = kappa.compare(cm.load_dirs([da]), full, sample={"LU2-1|3"}, min_pairs=1)
    assert res2["paired_rows"] == 2
    f = tmp_path / "s.txt"
    f.write_text("# c\nLU2-1|3\n\n", encoding="utf-8")
    assert kappa._load_sample_list(str(f)) == {"LU2-1|3"}


def test_nsfwv2_kappa_cli(tmp_path, capsys):
    da, db = _kappa_dirs(tmp_path)
    out = tmp_path / "k.json"
    assert kappa.main([str(da), str(db), "--out", str(out), "--min-pairs", "10", "--sets-dir", str(tmp_path / "none")]) == 0
    assert json.loads(out.read_text(encoding="utf-8"))["paired_decisions"] == 40
    assert "pooled Cohen kappa" in capsys.readouterr().out


# ----------------------------------------------------------------- bootstrap
def _boot_data(tmp_path, gap=0.2, ncases=9):
    cases = [f"LU2-{i}" for i in range(1, 4)] + [f"LR2-{i}" for i in range(1, 4)] + [f"LW3-{i}" for i in range(1, 4)]
    cases = cases[:ncases] + [c.replace("2-", "3-").replace("W3", "W4") for c in cases[:ncases]]
    rng = random.Random(5)
    rows = {"hi": [], "lo": []}
    for c in cases:
        base = rng.uniform(0.3, 0.7)
        for rep in (1, 2):
            n_hi = max(0, min(10, round((base + gap / 2) * 10)))
            n_lo = max(0, min(10, round((base - gap / 2) * 10)))
            rows["hi"].append(make_row(c, rep, "hi", 4, {f"e{i}": i < n_hi for i in range(10)}))
            rows["lo"].append(make_row(c, rep, "lo", 4, {f"e{i}": i < n_lo for i in range(10)}))
    return cm.load_dirs([write_dir(tmp_path / "r", rows)])


def test_nsfwv2_bootstrap_point_matches_pack_scores_and_is_seeded(tmp_path):
    data = _boot_data(tmp_path)
    ps = cm.pack_scores(data)
    r1 = bs.run(data, resamples=800, seed=7)
    r2 = bs.run(data, resamples=800, seed=7)
    assert r1 == r2
    for p in ("P1", "P2"):
        m = r1["packs"][p]["models"]["hi"]
        assert m["S"] == pytest.approx(round(ps["hi"][p]["S"], 2))
        assert m["ci_lo"] <= m["S"] <= m["ci_hi"] and m["half_width"] > 0
    assert r1["packs"]["P1"]["cases"] == 9
    d = r1["packs"]["P1"]["diffs"]["hi - lo"]
    assert d["diff"] > 0 and d["excludes_zero"] and d["p_a_gt_b"] > 0.99


def test_nsfwv2_bootstrap_identical_cases_zero_width_and_bars(tmp_path):
    cases = [f"LU2-{i}" for i in range(1, 4)] + [f"LR2-{i}" for i in range(1, 4)] + [f"LW3-{i}" for i in range(1, 4)]
    rows = {"top": [make_row(c, 1, "top", 4, {"a": True, "b": True}) for c in cases],
            "bot": [make_row(c, 1, "bot", 4, {"a": True, "b": False}) for c in cases]}
    data = cm.load_dirs([write_dir(tmp_path / "r", rows)])
    r = bs.run(data, resamples=300)["packs"]["P1"]
    assert r["models"]["top"]["half_width"] == 0 and r["models"]["top"]["S"] == 100.0
    assert r["bars"]["spread>=20"]["holds_point"] and r["bars"]["spread>=20"]["robust"]
    assert not r["bars"]["n_ge90<=1"]["holds_point"] or r["bars"]["n_ge90<=1"]["point"] == 1
    assert r["bars"]["best>=50"]["robust"]
    assert r["top2"]["order"][0] == "top" and r["top2"]["holds"] == 1.0
    md = bs.render_md(bs.run(data, resamples=50))
    assert "| bar |" in md and "half-width" in md


# ----------------------------------------------------------------- stability
def test_nsfwv2_rejudge_stability_flags(tmp_path):
    cases = ["LU2-1", "LU2-2", "LU2-3"]
    a = {"m": [make_row(c, 1, "m", 4, {"x": True, "y": True}) for c in cases]}
    b_same = {"m": [make_row(c, 1, "m", 4, {"x": True, "y": True}) for c in cases]}
    b_diff = {"m": [make_row(c, 1, "m", 4 if c != "LU2-1" else 3, {"x": True, "y": True}) for c in cases]}
    da = cm.load_dirs([write_dir(tmp_path / "a", a)])
    r0 = rejudge_stability.compare(da, cm.load_dirs([write_dir(tmp_path / "b0", b_same)]))
    assert r0["n_flagged"] == 0 and r0["max_delta"] == 0
    r1 = rejudge_stability.compare(da, cm.load_dirs([write_dir(tmp_path / "b1", b_diff)]))
    assert r1["rows"][0]["delta"] == pytest.approx(100 / 3 * 0.25, abs=0.01)    # one case 100 -> 75
    assert r1["n_flagged"] == 1 and r1["rows"][0]["flag"]
    r2 = rejudge_stability.compare(da, cm.load_dirs([write_dir(tmp_path / "b2", b_diff)]), threshold=10)
    assert r2["n_flagged"] == 0
    # B judged only a subset of rows: only common rows are compared
    sub = {"m": b_diff["m"][:1]}
    r3 = rejudge_stability.compare(da, cm.load_dirs([write_dir(tmp_path / "b3", sub)]))
    assert r3["rows"][0]["cases"] == 1


# ---------------------------------------------------------------------- gate
def test_nsfwv2_gate_lines(tmp_path):
    data = _boot_data(tmp_path, gap=0.4)
    fit = tmp_path / "F.md"
    fit.write_text("| Case | Verdict | x |\n|---|---|---|\n" + "\n".join(
        f"| {c} | PASS | - |" for c in [f"L{p}{l}-{n}" for p, l in (("R", 2), ("R", 3), ("U", 2), ("U", 3), ("W", 3), ("W", 4)) for n in (1, 2, 3)]) +
        "\n| FR-A18 | FAIL | x |\n", encoding="utf-8")
    lines = gate.evaluate(data, str(fit), resamples=200)
    st = {n: s for n, s, _ in lines}
    assert st["2 fitness PASS on 18/18"] == "PASS"
    assert st["1 verify + lint clean"] == "NOT CHECKED" and st["7 rig needs written"] == "NOT CHECKED"
    assert st["3 no dead items (4+ models)"] == "NOT CHECKED"
    assert any(n.startswith("4 P2 max<=50") for n, *_ in lines)
    fit.write_text(fit.read_text(encoding="utf-8").replace("| LR2-1 | PASS", "| LR2-1 | FAIL"), encoding="utf-8")
    assert {n: s for n, s, _ in gate.evaluate(data, str(fit), resamples=50)}["2 fitness PASS on 18/18"] == "FAIL"
    items = {"models": ["a", "b"], "cases": {"LU2-1": {"items": {"i": {"flags": ["dead"]}}}}}
    s3 = {n: (s, m) for n, s, m in gate.evaluate(data, items=items, resamples=50)}["3 no dead items (4+ models)"]
    assert s3[0] == "FAIL" and "only 2" in s3[1]


def test_nsfwv2_gate_kappa_and_stability_lines():
    data = {}
    kp = {"rated_elements": 5, "rated_ok_share": 0.8, "min_pairs": 40, "per_case": {}, "pooled": {"kappa": 0.7}, "paired_decisions": 9}
    st = {n: s for n, s, _ in gate.evaluate(data, kappa=kp, stability={"n_flagged": 0, "max_delta": 1.0, "rows": []}, resamples=10)}
    assert st["5a kappa >= 0.6 on >= 80% of elements"] == "PASS" and st["5b re-judge stability <= 3 points"] == "PASS"
    kp["rated_ok_share"] = 0.5
    st = {n: s for n, s, _ in gate.evaluate(data, kappa=kp, resamples=10)}
    assert st["5a kappa >= 0.6 on >= 80% of elements"] == "FAIL"


# ------------------------------------------------------------------ authorship
def _log(tmp_path, rows):
    p = tmp_path / "log.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    return p


def test_nsfwv2_authorship_roles_from_build_log(tmp_path):
    p = _log(tmp_path, [
        {"pkg": "WP5", "case": "LR2-1", "step": "cowrite", "model": "grok"},
        {"pkg": "WP5", "case": "LR2-1", "step": "revise", "model": "deepseek-pro"},
        {"pkg": "WP12", "case": "LR2-1", "step": "fit-bad", "model": "deepseek"},
        {"pkg": "WP12", "case": "LR2-1", "step": "fit-good", "model": "grok"},
        {"pkg": "WP8", "case": "LR2-1", "step": "critique", "model": "grok+dsflash+dspro"},
        {"pkg": "WP5", "case": "LR2-1", "step": "directives-r1", "model": "claude-opus-5-5"},
        {"pkg": "WP5", "case": "LR2-1", "step": "patch-r1", "model": "deepseek-pro"},
        {"pkg": "WP2", "case": "charter", "step": "codesign", "model": "deepseek"},
        {"pkg": "WP8", "case": "LU2-1", "step": "cowrite", "model": "minimax"},   # WP8 co-write is not prose
        {"pkg": "L3", "case": "LU2-1", "step": "cowrite", "model": "claude-sonnet-5-5"},
    ])
    led = au.build(p)
    a = led["LR2-1"]
    assert a["prose"] == ["deepseek", "deepseek-pro", "grok"]
    assert a["breaks"] == ["claude", "deepseek"]
    assert a["elements"] == ["claude", "deepseek-pro"]
    assert a["critics"] == ["deepseek-flash", "deepseek-pro", "grok"]
    assert led["LU2-1"]["prose"] == ["claude"]
    assert au.norm_models("grok+dsflash") == ["grok", "deepseek-flash"]


def test_nsfwv2_authorship_validate_and_write(tmp_path):
    assert au.validate(None) == ["missing"]
    assert au.validate({"prose": ["grok"], "elements": ["claude"], "breaks": ["claude"], "critics": []}) == []
    assert any("prose" in x for x in au.validate({"prose": [], "elements": ["c"], "breaks": ["c"], "critics": []}))
    sets = tmp_path / "sets"
    sets.mkdir()
    f = sets / "s.json"
    f.write_text(json.dumps({"cases": [{"id": "LR2-1", "meta": {"writer": "grok"}},
                                       {"id": "LR2-2", "meta": {"authorship": {"prose": ["qwen"], "elements": [], "breaks": [], "critics": []}}}]}), encoding="utf-8")
    log = _log(tmp_path, [{"pkg": "WP5", "case": "LR2-1", "step": "cowrite", "model": "grok"},
                          {"pkg": "WP5", "case": "LR2-2", "step": "cowrite", "model": "deepseek"}])
    before = f.read_text(encoding="utf-8")
    assert au.main([str(sets), str(log)]) == 0 and f.read_text(encoding="utf-8") == before          # dry run
    assert au.main([str(sets), str(log), "--write"]) == 0
    doc = json.loads(f.read_text(encoding="utf-8"))
    assert doc["cases"][0]["meta"]["authorship"]["prose"] == ["grok"]
    assert doc["cases"][0]["meta"]["writer"] == "grok"
    assert doc["cases"][1]["meta"]["authorship"]["prose"] == ["deepseek", "qwen"]     # union with stored
    assert au.validate(doc["cases"][1]["meta"]["authorship"]) == []


# ------------------------------------------------------- report exclusion rule
def _wc_rows(model, spec):
    """spec: {case: s_target 0..4 rung} -> judged rows for report.wc_block"""
    rows = []
    for case, w in spec.items():
        r = make_row(case, 1, model, w, {"a": True, "b": True})
        r["judge"]["willingness"]["w"] = w
        rows.append(r)
    return rows


def test_nsfwv2_report_excludes_own_prose_cases(monkeypatch):
    cases = {
        "LR2-1": {"id": "LR2-1", "meta": {"authorship": {"prose": ["grok", "deepseek-pro"], "elements": ["claude"], "breaks": ["claude"], "critics": []}}},
        "LR2-2": {"id": "LR2-2", "meta": {"authorship": {"prose": ["deepseek"], "elements": ["claude"], "breaks": ["claude"], "critics": []}}},
        "LR2-3": {"id": "LR2-3", "meta": {"authorship": {"prose": ["claude"], "elements": ["claude"], "breaks": ["claude"], "critics": []}}},
    }
    monkeypatch.setattr(report, "_CASES_BY_ID", cases)
    # 100 on LR2-1, 50 on LR2-2 (C=1: W4 -> 1.0, W2 rows are unengaged -> 0)
    spec = {"LR2-1": 4, "LR2-2": 2, "LR2-3": 4}
    blk = report.wc_block(_wc_rows("grok-4-7", spec))
    a = blk["authorship"]
    assert a["family"] == "grok" and a["n_excluded"] == 1 and a["excluded"] == ["LR2-1"]
    assert blk["S"] == pytest.approx((1.0 + 0.0 + 1.0) / 3, abs=1e-3)
    assert a["S_excl"] == pytest.approx(0.5, abs=1e-3)                  # LR2-2 (0) and LR2-3 (1)
    blk2 = report.wc_block(_wc_rows("deepseek-flash", spec))
    assert blk2["authorship"]["excluded"] == ["LR2-1", "LR2-2"] and blk2["authorship"]["S_excl"] == pytest.approx(1.0)
    blk3 = report.wc_block(_wc_rows("split-untied-31b-q8", spec))      # no family: nothing excluded
    assert blk3["authorship"]["n_excluded"] == 0 and blk3["authorship"]["S_excl"] == pytest.approx(blk3["S"], abs=1e-3)
    assert "excl. 1 own-prose case: 50.0" in report.wc_authorship_label(blk)
    assert report.wc_authorship_label({"S": 0.5}) == "50.0"


def test_nsfwv2_report_without_ledger_is_unchanged(monkeypatch):
    monkeypatch.setattr(report, "_CASES_BY_ID", {"LR2-1": {"id": "LR2-1", "meta": {}}})
    blk = report.wc_block(_wc_rows("grok-4-7", {"LR2-1": 4}))
    assert "authorship" not in blk
    assert report.author_family("deepseek-pro") == "deepseek" and report.author_family("grok-4-7") == "grok"
    assert report.author_family("claude-opus-5-5") == "claude" and report.author_family("zzz") is None
    card = {"ladder": {"nsfw": {"p1": {"wc": {**blk, "n": 1}}}}}
    assert "authorship" not in report._wc_cells(card)["nsfw/p1"]
    card["ladder"]["nsfw"]["p1"]["wc"]["authorship"] = {"n_excluded": 0}
    assert report._wc_cells(card)["nsfw/p1"]["authorship"] == {"n_excluded": 0}


# ------------------------------------------------- review corrections (round 2)
def test_nsfwv2_itemstats_guard_exempt_and_refused_counted(tmp_path):
    rows = {"m": []}
    for rep in range(6):
        r = make_row("LU2-1", rep, "m", 4, {"e1": True})
        r["checks"] = {"results": [{"id": "no-disclaimer", "pass": True}, {"id": "other", "pass": True},
                                   {"id": "slop", "pass": False}]}
        r["judge"]["elements"]["results"].append({"id": "drift", "type": "judge", "pass": True, "credit": 1.0, "negative": True})
        rows["m"].append(r)
    rows["m"].append(make_row("LU2-1", 90, "m", 0, {"e1": False}))      # refused rep
    t = itemstats.item_table(cm.load_dirs([write_dir(tmp_path / "r", rows)]))["LU2-1"]
    assert t["n_refused_rows"] == 1 and t["n_rows"] == 6
    it = t["items"]
    assert "dead" not in it["chk:no-disclaimer"]["flags"] and it["chk:no-disclaimer"]["guard"]
    assert "dead" not in it["drift"]["flags"]
    assert "dead" in it["chk:other"]["flags"] and "dead" in it["e1"]["flags"]
    assert "dead" in it["chk:slop"]["flags"]                            # a guard that fails for everyone


def test_nsfwv2_kappa_paradox_stats():
    pairs = [(1, 1)] * 85 + [(0, 0)] * 3 + [(1, 0)] * 6 + [(0, 1)] * 6
    k = kappa.cohen(pairs)[0]
    ac1, pabak = kappa.ac1_pabak(pairs)
    assert k < 0.35 and ac1 > 0.8 and pabak == pytest.approx(2 * 0.88 - 1)
    assert kappa.ac1_pabak([]) == (None, None)


def test_nsfwv2_judge_delta_flags(tmp_path):
    from tools.nsfwv2 import judge_delta
    cases = ["LU2-1", "LU2-2", "LU2-3"]
    a = {"m": [make_row(c, 1, "m", 4, {"x": True}) for c in cases]}
    b = {"m": [make_row(c, 1, "m", 4, {"x": i < 1}) if False else make_row(c, 1, "m", 4, {"x": True, "y": False}) for c in cases]}
    r = judge_delta.compare(cm.load_dirs([write_dir(tmp_path / "a", a)]), cm.load_dirs([write_dir(tmp_path / "b", b)]), resamples=200)
    row = r["rows"][0]
    assert row["delta"] == pytest.approx(50.0) and row["flag"] and row["ci_excludes_0"]
    same = judge_delta.compare(cm.load_dirs([tmp_path / "a"]), cm.load_dirs([tmp_path / "a"]), resamples=50)
    assert same["n_flagged"] == 0


def test_nsfwv2_bootstrap_p2_max_needs_80_percent(tmp_path):
    cases = [f"LU3-{i}" for i in range(1, 4)] + [f"LR3-{i}" for i in range(1, 4)] + [f"LW4-{i}" for i in range(1, 4)]
    rows = {"m": [make_row(c, 1, "m", 4, {"a": True, "b": False}) for c in cases]}     # S 50 everywhere
    b = bs.run(cm.load_dirs([write_dir(tmp_path / "r", rows)]), resamples=100)["packs"]["P2"]["bars"]
    assert b["max<=50"]["required_share"] == 0.8 and b["best>=20"]["required_share"] == 0.95


def test_nsfwv2_pillars_untagged_and_scored(tmp_path):
    from tools.nsfwv2 import pillars
    sets = tmp_path / "sets"
    sets.mkdir()
    el = lambda i, p=None: {"id": i, **({"pillar": p} if p else {})}
    (sets / "s.json").write_text(json.dumps({"cases": [
        {"id": "LU2-1", "pack": 1, "judge_elements": [el("a", "rp"), el("b", "continuity"), el("c")], "checks": [{"id": "k"}]}]}), encoding="utf-8")
    cases = cm.load_cases(str(sets))
    tags = pillars.tag_map(cases)
    cov = pillars.coverage(cases, tags)
    assert cov["cases"]["LU2-1"]["untagged"] == ["c", "chk:k"] and cov["packs"]["P1"]["counts"]["rp"] == 1
    rows = {"m": [make_row("LU2-1", 1, "m", 4, {"a": True, "b": False, "c": True})]}
    sc = pillars.pillar_scores(cm.load_dirs([write_dir(tmp_path / "r", rows)], cases), tags, resamples=50)
    assert sc["m"]["P1"]["rp"]["S"] == 100.0 and sc["m"]["P1"]["continuity"]["S"] == 0.0
    assert "willingness" not in sc["m"]["P1"]
    # no tags at all: runs cleanly and says untagged
    (sets / "s.json").write_text(json.dumps({"cases": [{"id": "LU2-1", "pack": 1, "judge_elements": [el("a")]}]}), encoding="utf-8")
    assert pillars.main([str(sets), str(tmp_path / "r"), "--resamples", "20"]) == 0


def test_nsfwv2_report_rule_ignores_reviser_and_merges_deepseek(monkeypatch):
    cases = {"LR2-1": {"id": "LR2-1", "meta": {"writer": "grok", "reviser": "deepseek-pro",
                                                "authorship": {"prose": ["claude"], "elements": ["claude"], "breaks": ["claude"], "critics": []}}},
             "LR2-2": {"id": "LR2-2", "meta": {"authorship": {"prose": ["deepseek-pro"], "elements": ["claude"], "breaks": ["claude"], "critics": []}}}}
    monkeypatch.setattr(report, "_CASES_BY_ID", cases)
    spec = {"LR2-1": 4, "LR2-2": 4}
    assert report.wc_block(_wc_rows("grok-4-7", spec))["authorship"]["n_excluded"] == 0
    assert report.wc_block(_wc_rows("deepseek-flash", spec))["authorship"]["excluded"] == ["LR2-2"]


# ------------------------------------------------- lineage + held-out (r2)
def _cases_with(prose, shared=None, heldout=None):
    meta = {"authorship": {"prose": prose, "elements": ["claude"], "breaks": ["claude"], "critics": [],
                           **({"heldout": heldout} if heldout else {})}}
    if shared:
        meta["shared_data"] = shared
    return {"meta": meta}


def test_nsfwv2_lineage_exclusion_and_shared_data_flag(monkeypatch):
    monkeypatch.setattr(report, "_CASES_BY_ID", {
        "C1": _cases_with(["swish-31b"]),                    # sibling wrote the prose
        "C2": _cases_with(["claude"], shared=["readyart"]),
        "C3": _cases_with(["claude"])})
    ids = ["C1", "C2", "C3"]
    # a lineage sibling is excluded where another member authored; a model outside the family is not
    assert report.authored_cases_for("split-untied-31b-q8", ids) == ["C1"]
    assert report.authored_cases_for("swish-31b", ids) == ["C1"]
    assert report.authored_cases_for("gemma-x", ids) == []
    # shared data: flagged, never excluded
    assert report.lineage_flags("wicked-tutu-26b-q4", ids) == {"C2": ["readyart-shared-data"]}
    assert "C2" not in report.authored_cases_for("wicked-tutu-26b-q4", ids)
    assert report.lineage_flags("gemma-x", ids) == {}
    # family rule still works
    monkeypatch.setattr(report, "_CASES_BY_ID", {"C4": _cases_with(["deepseek-pro"])})
    assert report.authored_cases_for("deepseek-flash", ["C4"]) == ["C4"]


def test_nsfwv2_wc_block_reports_both_numbers_and_lineage_flag(monkeypatch):
    monkeypatch.setattr(report, "_CASES_BY_ID", {
        "LU2-1": _cases_with(["omega-arsenal-31b"]),
        "LU2-2": _cases_with(["claude"], shared=["readyart"])})
    rows = [make_row("LU2-1", 1, "swish-31b", 4, {"a": False}),
            make_row("LU2-2", 1, "swish-31b", 4, {"a": True})]
    wc = report.wc_block(rows)
    a = wc["authorship"]
    assert a["n_excluded"] == 1 and a["excluded"] == ["LU2-1"]
    assert a["S_excl"] == 1.0 and wc["S"] < 1.0
    assert a["n_lineage_flagged"] == 1
    assert wc["per_case"]["LU2-2"]["lineage_flag"] == "readyart-shared-data"
    assert "lineage_flag" not in wc["per_case"]["LU2-1"]


def test_nsfwv2_heldout_is_separate_and_not_pooled(tmp_path, monkeypatch):
    monkeypatch.setattr(report, "_CASES_BY_ID", {})
    cases = [f"LU2-{i}" for i in range(1, 4)] + [f"LR2-{i}" for i in range(1, 4)] + [f"LW3-{i}" for i in range(1, 4)]
    rows = {"top": [make_row(c, 1, "top", 4, {"a": True, "b": True}) for c in cases],
            "bot": [make_row(c, 1, "bot", 4, {"a": True, "b": False}) for c in cases],
            "hermes-4.3-36b-q8": [make_row(c, 1, "hermes-4.3-36b-q8", 4, {"a": True, "b": True}) for c in cases]}
    data = cm.load_dirs([write_dir(tmp_path / "r", rows)])
    tune, held = cm.split_heldout(data)
    assert set(held) == {"hermes-4.3-36b-q8"} and set(tune) == {"top", "bot"}
    r = bs.run(data, resamples=100, heldout=list(held))["packs"]["P1"]
    assert set(r["models"]) == {"top", "bot"}
    assert not any("hermes" in k for k in r["diffs"])
    h = r["heldout"]["hermes-4.3-36b-q8"]
    assert h["S"] == 100.0 and h["tune_separation"]["tune_models_above"] == 0
    assert "Held-out subjects" in bs.render_md(bs.run(data, resamples=20, heldout=list(held)))
    # the --heldout option and meta.authorship.heldout both mark a subject
    assert report.is_heldout("zzz-1", extra=["zzz"])
    assert report.is_heldout("qqq-7", cases={"C": _cases_with(["claude"], heldout=["qqq"])})


def test_nsfwv2_authorship_write_refuses_heldout(tmp_path):
    sets = tmp_path / "sets"
    sets.mkdir()
    (sets / "a.json").write_text(json.dumps({"cases": [{"id": "C1", "meta": {}}]}), encoding="utf-8")
    log = tmp_path / "log.jsonl"
    log.write_text(json.dumps({"pkg": "WP5", "case": "C1", "step": "cowrite", "model": "hermes-4.3-36b"}) + "\n", encoding="utf-8")
    before = (sets / "a.json").read_text(encoding="utf-8")
    assert au.main([str(sets), str(log), "--write"]) == 2
    assert (sets / "a.json").read_text(encoding="utf-8") == before
    # dry run only warns; a normal model still writes
    log.write_text(json.dumps({"pkg": "WP5", "case": "C1", "step": "cowrite", "model": "gemma-x"}) + "\n", encoding="utf-8")
    au.main([str(sets), str(log), "--write"])
    assert "gemma-x" in (sets / "a.json").read_text(encoding="utf-8")
    log.write_text(json.dumps({"pkg": "WP5", "case": "C1", "step": "critique", "model": "foo"}) + "\n", encoding="utf-8")
    assert au.main([str(sets), str(log), "--heldout", "foo", "--write"]) == 2


# ------------------------------------------------------------ tunesep (M13)
def _tune_dir(tmp_path, spread=True):
    from tools.nsfwv2 import tunesep
    cases = [f"LU2-{i}" for i in range(1, 4)] + [f"LR2-{i}" for i in range(1, 4)] + [f"LW3-{i}" for i in range(1, 4)]
    n = {"tune-a": 9, "tune-b": 6, "tune-c": 3} if spread else {"tune-a": 5, "tune-b": 5, "tune-c": 5}
    n["hermes-4.3-36b-q8"] = 7
    n["hosted-x"] = 10
    rows = {m: [make_row(c, 1, m, 4, {f"e{i}": i < k + (j % 2 and 0) for i in range(10)}) for j, c in enumerate(cases)]
            for m, k in n.items()}
    # make half the items live (pass for some cases only) on the tunes
    return cm.load_dirs([write_dir(tmp_path / "r", rows)]), tunesep


def test_nsfwv2_tunesep_pass_and_heldout_row(tmp_path):
    data, ts = _tune_dir(tmp_path)
    o = ts.run(data, ["tune-"], resamples=300)
    c = o["checks"]
    assert o["tunes"] == ["tune-a", "tune-b", "tune-c"] and "hosted-x" not in o["tunes"]
    assert c["a_gap"]["gap"] == 60.0 and c["a_gap"]["pass"]
    assert c["b_p_top_gt_bottom"]["p"] == 1.0 and c["c_nonoverlap_pairs"]["share"] == 1.0
    # items are all-or-nothing per model, so every item has 0 < p < 1 on the tunes
    assert c["d_live_items"]["share"] > 0.4 and o["pass"] is True
    h = o["heldout_rows"][0]
    assert h["model"] == "hermes-4.3-36b-q8" and h["tune_models_above"] == 1 and h["rank_in_tunes"] == 2
    md = ts.render_md(o)
    assert "Held-out" in md and "Overall: PASS" in md


def test_nsfwv2_tunesep_fails_without_separation_and_gate_line(tmp_path):
    data, ts = _tune_dir(tmp_path, spread=False)
    o = ts.run(data, ["tune-"], resamples=200)
    assert o["pass"] is False and not o["checks"]["a_gap"]["pass"]
    assert ts.run(data, ["nope"], resamples=10)["error"].startswith("need >= 2")
    lines = gate.evaluate(data, tunes=["tune-"], resamples=100)
    assert next(x for x in lines if x[0].startswith("8 "))[1] == gate.F
    assert next(x for x in gate.evaluate(data, resamples=50) if x[0].startswith("8 "))[1] == gate.N


def test_nsfwv2_local_tune_excluded_from_its_own_prose_by_label_prefix(monkeypatch):
    monkeypatch.setattr(report, "_CASES_BY_ID", {
        "C1": _cases_with(["gemma-26b-qat"]), "C2": _cases_with(["swish-31b"]),
        "C3": _cases_with(["qwen3.8-27b"]), "C4": _cases_with(["claude"])})
    ids = ["C1", "C2", "C3", "C4"]
    assert report.authored_cases_for("Gemma_26B_QAT-q8-0-hb16", ids) == ["C1"]
    assert report.authored_cases_for("qwen3.8-27b-unsloth", ids) == ["C3"]
    # a lineage sibling of a prose entry excludes too
    assert report.authored_cases_for("omega-arsenal-31b-final-q8-0-hb16", ids) == ["C2"]
    assert report.authored_cases_for("other-9b", ids) == []
