"""The adult page's two numbers on the non-routed bench45 board (the owner R52, lead 2026-11-14 rc3): NSFW and
Uncensored, each out of 10 (report.score_subtotals, filled by N2 in points_scorecard), print as two separate
columns with their margins, and the parts line beneath (pending parts say so; the number says it is scaled
from the measured points). A model whose adult page did not run reads "not run", never 0. Nothing of the
page enters Overall. Toy families on G2's fixed-set toy run (test_rc2_main / test_chains): no model, no rig."""
from __future__ import annotations

import importlib.util
import json
import re
import types
from pathlib import Path

from crucibleforge import cli, ladder, profiles, report, routing

_spec = importlib.util.spec_from_file_location("_tc", Path(__file__).with_name("test_chains_20261113.py"))
_tc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_tc)
_toys = _tc._toys          # the toy families (autouse fixture of that module, used here too)


def _flat(name, n=4):
    return {"flat": [{"family": "toychain", "level": 1, "count": n, "name": name}]}


def _prof(adult: bool):
    """Work 10 (one part) + the adult page: NSFW = a 4 + sexting 2.5 (pending) + b 2 + anatomy 1.5
    (pending); Uncensored = w 6 + k 4. in_total false: never in Overall."""
    prof = _tc._flatprof([{"family": "toychain", "level": 1, "count": 4, "name": "mn"}])
    prof["scores"].pop("chat", None)
    prof["cases"] = {"coding": []}
    prof["ladder"] = {"unlock": 0.9}
    parts = {"a": 4, "sexting": 2.5, "b": 2, "anatomy": 1.5, "w": 6, "k": 4}
    prof["scores"]["nsfw"] = {"label": "NSFW", "in_total": False,
                              "parts": {pk: {"max": mx, "scoring": ["coding"], "cases": [],
                                             "short": pk.title()} for pk, mx in parts.items()}}
    if adult:
        for pk in ("a", "b", "w", "k"):
            prof["chains"][f"nsfw/{pk}"] = _flat(f"x{pk}")
    prof["score_subtotals"] = {"nsfw": {"label": "NSFW", "score": "nsfw", "parts": ["a", "sexting", "b", "anatomy"]},
                               "uncensored": {"label": "Uncensored", "score": "nsfw", "parts": ["w", "k"]}}
    return prof


def _board(tmp_path, monkeypatch, pol, adult=True):
    prof = _prof(adult)
    assert routing.spec(prof) is None and not report.routed(prof)
    root = tmp_path / "ladder"
    root.mkdir(parents=True)
    monkeypatch.setattr(ladder, "roots", lambda *a, **k: [root])
    monkeypatch.setattr(profiles, "load_profile", lambda name=None, cfg=None: prof)
    base_cfg = _tc._cfg
    monkeypatch.setattr(_tc, "_cfg", lambda p, **kw: {**base_cfg(p, **kw), "_profile": "t"})
    _tc._run(tmp_path, monkeypatch, prof, {"m": pol})
    cfg = _tc._cfg(prof)
    cfg["models"] = [{"name": "m", "model_id": "m", "provider": "p"}]
    args = types.SimpleNamespace(smoke=False, profile="t", no_tiers=False, no_ladder=False, no_judge=True,
                                 skip_conformance=True, escalate=False, judge=None, force_evict=False)
    monkeypatch.setattr(cli, "_escalate_phase", lambda *a, **k: 0)
    monkeypatch.setattr(cli, "_ProviderGuard", lambda *a, **k: types.SimpleNamespace(restore=lambda: None))
    assert cli._ladder_phases(args, cfg, ["m"]) == 0
    md = report.generate("m", write=False, cfg=cfg)
    _, stats, _ = report.board_stats("m", cfg)
    return md, stats, report.render_report_html(["m"], stats, cfg)


def _data(html):
    m = re.search(r'<script type="application/json" id="board-data">(.*?)</script>', html, re.S)
    return json.loads(m.group(1))


def _head(md):
    return next(x for x in md.splitlines() if x.startswith("| # | Model |"))


def _row(md):
    return next(x for x in md.splitlines() if x.startswith("| 1 | m |"))


# a: 3 of 4 pass, b: 4 of 4, w: 2 of 4, k: 4 of 4; work 4 of 4
POL = lambda lv, cid: "nope" if cid.startswith(("FLAT-xa-004", "FLAT-xw-003", "FLAT-xw-004")) else "PASS"   # noqa: E731


def test_the_adult_page_prints_two_numbers_with_margins(tmp_path, monkeypatch):
    md, stats, _html = _board(tmp_path, monkeypatch, POL)
    sub = stats["m"]["scorecard"]["subtotals"]
    n, u = sub["nsfw"], sub["uncensored"]
    assert n["pending"] == ["sexting", "anatomy"] and not u["pending"]
    head = _head(md)
    assert "| NSFW (/10) | Uncensored (/10) |" in head
    assert "| NSFW |" not in head                       # the one NSFW column is replaced, not kept beside
    cells = [c.strip() for c in _row(md).split("|")]
    i = [c.strip() for c in head.split("|")].index("NSFW (/10)")
    hl = stats["m"]["headline"]
    mn = report.subtotal_margin(hl, n)
    mu = report.subtotal_margin(hl, u)
    assert mn is not None and mu is not None
    assert cells[i] == report.headline_cell({"S": {"x": n["points"]}, "m": {"x": mn}}, "x") + " [4 pts pending]"
    assert cells[i + 1] == report.headline_cell({"S": {"x": u["points"]}, "m": {"x": mu}}, "x")


def test_the_parts_line_names_every_part_and_says_scaled(tmp_path, monkeypatch):
    md, stats, _html = _board(tmp_path, monkeypatch, POL)
    n = stats["m"]["scorecard"]["subtotals"]["nsfw"]
    line = next(x for x in md.splitlines() if x.startswith("| m |") and "NSFW " in x and " = A " in x)
    assert f"(scaled from {n['measured_max']:g} of {n['max']:g} pts measured)" in line
    assert "Sexting pending" in line and "Anatomy pending" in line
    assert re.search(r"= A [\d.]+±[\d.]+ \+ Sexting pending \+ B [\d.]+±[\d.]+ \+ Anatomy pending", line)
    ucell = next(c.strip() for c in line.split("|") if c.strip().startswith("Uncensored "))
    assert "scaled" not in ucell and re.search(r"= W [\d.]+±[\d.]+ \+ K [\d.]+±[\d.]+$", ucell)
    # the values are the parts' own points: a 3 of 4, w 2 of 4
    assert "A 3.0" in line and "W 3.0" in ucell and "K 4.0" in ucell


def test_the_adult_page_never_enters_overall(tmp_path, monkeypatch):
    md, stats, _html = _board(tmp_path, monkeypatch, POL)
    hl = stats["m"]["headline"]
    assert hl["S"]["overall"] == hl["S"]["work"]          # Overall = Work alone


def test_a_model_without_the_adult_page_reads_not_run(tmp_path, monkeypatch):
    md, stats, html = _board(tmp_path, monkeypatch, lambda lv, cid: "PASS", adult=False)
    sub = stats["m"]["scorecard"]["subtotals"]
    assert sub["nsfw"]["points"] is None and sub["uncensored"]["points"] is None
    head = [c.strip() for c in _head(md).split("|")]
    cells = [c.strip() for c in _row(md).split("|")]
    assert cells[head.index("NSFW (/10)")] == "not run" and cells[head.index("Uncensored (/10)")] == "not run"
    assert "| m | NSFW not run | Uncensored not run |" in md or "NSFW not run" in md
    d = _data(html)
    r = d["rows"][0]
    assert r["cell_text"]["adult_nsfw"] == "not run" and r["cell_text"]["adult_uncensored"] == "not run"
    assert (r.get("s") or {}).get("adult_nsfw") is None


def test_the_html_board_shows_the_two_columns(tmp_path, monkeypatch):
    _md, stats, html = _board(tmp_path, monkeypatch, POL)
    d = _data(html)
    keys = [c["key"] for c in d["main"]]
    assert "adult_nsfw" in keys and "adult_uncensored" in keys and "nsfw" not in keys
    labels = {c["key"]: c["label"] for c in d["main"]}
    assert labels["adult_nsfw"] == "NSFW (/10)" and labels["adult_uncensored"] == "Uncensored (/10)"
    r = d["rows"][0]
    n = stats["m"]["scorecard"]["subtotals"]["nsfw"]
    assert r["s"]["adult_nsfw"] == round(n["points"], 1)
    assert r["m"]["adult_nsfw"] is not None
    assert r["sub_tag"]["adult_nsfw"] == "[4 pts pending]" and "adult_uncensored" not in (r.get("sub_tag") or {})
    assert "NSFW " in r["points_cell"] and "Uncensored " in r["points_cell"]
