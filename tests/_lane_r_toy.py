"""Lane-r toy board (fix round 2026-11-18): a small 4.5-shaped profile on G2's toy families (test_chains_20261113)
-- Coding (two chain parts) and Chat (one chain part) in the total, an adult page (in_total false, NSFW +
Uncensored subtotals) beside it -- run end to end through the real runner with a stub provider into the test's
temp dir, then rendered. No model, no rig."""
from __future__ import annotations

import importlib.util
import json
import re
import types
from pathlib import Path

from crucibleforge import cli, config, ladder, profiles, report, routing

_spec = importlib.util.spec_from_file_location("_tc_r", Path(__file__).with_name("test_chains_20261113.py"))
_tc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_tc)
toys = _tc._toys
ChatResult = _tc.ChatResult


def flat(name, n=4, **kw):
    return {"flat": [{"family": "toychain", "level": 1, "count": n, "name": name, "ran_out": "exclude", **kw}]}


def prof45(adult: bool = True, parts=None):
    """Coding = a (5) + b (5); Chat = c (10); NSFW page = n1 4 + n2 6 | u1 6 + u2 4 (in_total false)."""
    parts = parts or {"work": {"a": 5, "b": 5}, "chat": {"c": 10}}
    prof = {"name": "t", "release_seed": "4.5.0", "pending_scaling": "none",
            "cases": {"coding": []}, "ladder": {"unlock": 0.9}, "scores": {}, "chains": {}}
    for sec, ps in parts.items():
        prof["scores"][sec] = {"label": {"work": "Coding", "chat": "Chat"}.get(sec, sec),
                               "parts": {pk: {"max": mx, "scoring": ["coding"], "cases": [],
                                              "short": pk.upper(), "label": f"Part {pk}"}
                                         for pk, mx in ps.items()}}
        for pk in ps:
            prof["chains"][f"{sec}/{pk}"] = flat(f"x{pk}")
    ad = {"n1": 4, "n2": 6, "u1": 6, "u2": 4}
    prof["scores"]["nsfw"] = {"label": "NSFW", "in_total": False,
                              "parts": {pk: {"max": mx, "scoring": ["coding"], "cases": [], "short": pk.upper()}
                                        for pk, mx in ad.items()}}
    if adult:
        for pk in ad:
            prof["chains"][f"nsfw/{pk}"] = flat(f"y{pk}")
    prof["score_subtotals"] = {"nsfw": {"label": "NSFW", "score": "nsfw", "parts": ["n1", "n2"]},
                               "uncensored": {"label": "Uncensored", "score": "nsfw", "parts": ["u1", "u2"]}}
    return prof


def board(tmp_path, monkeypatch, policies, prof=None, metas=None, labels=None, keep=None):
    """Run every model of ``policies`` on ``prof`` into tmp_path and render. Returns (md, stats, html).
    ``keep(model, row) -> bool`` filters each model's stored rows before the render; ``metas`` = {model: fields to
    set in its meta}."""
    prof = prof or prof45()
    assert routing.spec(prof) is None and not report.routed(prof)
    root = tmp_path / "ladder"
    root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(ladder, "roots", lambda *a, **k: [root])
    monkeypatch.setattr(profiles, "load_profile", lambda name=None, cfg=None: prof)
    base_cfg = _tc._cfg
    monkeypatch.setattr(_tc, "_cfg", lambda p, **kw: {**base_cfg(p, **kw), "_profile": "t"})
    _tc._run(tmp_path, monkeypatch, prof, policies)
    if keep is not None:
        for m in policies:
            p = Path(config.results_dir()) / f"transcripts_{m}.jsonl"
            rows = [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]
            p.write_text("".join(json.dumps(r) + "\n" for r in rows if keep(m, r)), encoding="utf-8")
    for m, extra in (metas or {}).items():
        p = Path(config.results_dir()) / f"meta_{m}.json"
        meta = json.loads(p.read_text(encoding="utf-8"))
        meta.update(extra)
        p.write_text(json.dumps(meta), encoding="utf-8")
    cfg = _tc._cfg(prof)
    cfg["models"] = [{"name": m, "model_id": m, "provider": "p"} for m in policies]
    args = types.SimpleNamespace(smoke=False, profile="t", no_tiers=False, no_ladder=False, no_judge=True,
                                 skip_conformance=True, escalate=False, judge=None, force_evict=False)
    monkeypatch.setattr(cli, "_escalate_phase", lambda *a, **k: 0)
    monkeypatch.setattr(cli, "_ProviderGuard", lambda *a, **k: types.SimpleNamespace(restore=lambda: None))
    assert cli._ladder_phases(args, cfg, list(policies)) == 0
    lab = labels or ",".join(policies)
    md = report.generate(lab, write=False, cfg=cfg)
    _, stats, _ = report.board_stats(lab, cfg)
    return md, stats, report.render_report_html(list(stats), stats, cfg)


def html_data(html):
    m = re.search(r'<script type="application/json" id="board-data">(.*?)</script>', html, re.S)
    return json.loads(m.group(1))


def head(md, start="| # | Model |"):
    return [c.strip() for c in next(x for x in md.splitlines() if x.startswith(start)).split("|")]


def row(md, model, start=None):
    for x in md.splitlines():
        cells = [c.strip() for c in x.split("|")]
        if len(cells) > 3 and model in cells[1:3] and (start is None or x.startswith(start)):
            return cells
    raise AssertionError(f"no row for {model}")


def cell(md, model, col):
    h = head(md)
    return row(md, model)[h.index(col)]


def cut(text="", n=64):
    """A reply cut at its ceiling."""
    return ChatResult(response_text=text, finish_reason="length", served_model="m", completion_tokens=n,
                      prompt_tokens=20, reasoning_tokens=n, total_s=0.01)


PASS = lambda lv, cid: "PASS"   # noqa: E731
