"""Built on / latest versions only / MAX marker (2026-11-07, MakoBench board round), CrucibleForge side.

models.yaml `base`, `official`, `supersedes`; report.superseded_map; html_rows keys; the board page's
"latest versions only" box, Built on column and MAX tag (the real script.js under node)."""
import json
import shutil

import pytest

from crucibleforge import report
from crucibleforge.templates.board import render_html
from test_board_layout import COMPS, rows, run_board

needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")


def S(official=True, supersedes=None, total=10.0, provisional=False):
    return {"official": official, "supersedes": supersedes, "provisional": provisional,
            "scorecard": {"total": total}}


def test_chain_picks_the_newest_ranked_official_row():
    stats = {"o5": S(), "o55": S(supersedes="o5"), "o48": S(), "o5x": S(supersedes="o48")}
    stats["o5"]["supersedes"] = "o48"
    # o48 <- o5 <- o55 ; o5x also supersedes o48 (a fork): the walk stops at the fork
    m = report.superseded_map(list(stats), stats)
    assert m["o5"] == "o55"
    assert "o55" not in m
    assert "o48" not in m                        # forked: nothing is hidden on a guess


def test_simple_chain_hides_every_older_row():
    stats = {"a": S(), "b": S(supersedes="a"), "c": S(supersedes="b")}
    assert report.superseded_map(list(stats), stats) == {"a": "c", "b": "c"}


def test_provisional_or_unranked_newer_row_does_not_supersede():
    stats = {"a": S(), "b": S(supersedes="a", provisional=True)}
    assert report.superseded_map(list(stats), stats) == {}
    stats = {"a": S(), "b": S(supersedes="a", total=None)}
    assert report.superseded_map(list(stats), stats) == {}


def test_chain_passes_through_a_label_that_is_not_on_the_board():
    stats = {"a": S(), "c": S(supersedes="b")}
    stats["b"] = S(supersedes="a")
    assert report.superseded_map(["a", "c"], stats) == {"a": "c"}      # b is not on this board


def test_a_fine_tune_is_never_superseded():
    stats = {"tune": S(official=False), "new": S(supersedes="tune")}
    assert report.superseded_map(list(stats), stats) == {}


def test_row_facts_reads_the_models_yaml_keys():
    cfg = {"providers": {"api": {"type": "openai"}, "rig": {"type": "studioforge"}},
           "models": [{"name": "h", "provider": "api", "base": "Claude Opus", "supersedes": "h-old"},
                      {"name": "t", "provider": "rig", "base": "Gemma 4 31B"},
                      {"name": "q", "provider": "rig", "official": True, "base": "x"},
                      {"name": "bad", "provider": "api", "base": "x\ny"}]}
    f = report.row_facts("h", {}, cfg)
    assert (f["base"], f["official"], f["supersedes"]) == ("Claude Opus", True, "h-old")
    f = report.row_facts("t", {}, cfg)
    assert (f["base"], f["official"], f["supersedes"]) == ("Gemma 4 31B", False, None)
    assert report.row_facts("q", {}, cfg)["official"] is True
    assert report.row_facts("bad", {}, cfg)["base"] is None              # one line only
    assert report.row_facts("none", {}, cfg)["base"] is None


def test_older_line_and_part_labels():
    stats = {"a": S(), "b": S(supersedes="a")}
    for s in stats.values():
        s["scorecard"].update({"score_defs": [{"key": "nsfw", "group": "nsfw", "label": "NSFW",
                                               "parts": [{"key": "uncensored", "label": "Uncensored"}]}]})
    stats["a"]["scorecard"]["total"] = 9.0
    assert report._older_line(["a", "b"], stats) == "Older versions on this board: a (newer: b)"
    assert report.html_part_labels(stats) == {"nsfw/uncensored": "NSFW / Uncensored"}
    assert report._older_line(["a"], stats) == ""


def vrows():
    rs = rows()
    rs[0].update({"base": "Gemma 4 31B", "superseded_by": "m1", "official": True, "maxed_parts": ["nsfw/uncensored"]})
    rs[1].update({"base": "Qwen3.8 27B", "official": True})
    rs[2].update({"official": False})
    return rs


def page(**kw):
    return render_html(vrows(), "sub", components=COMPS, footer=["f"],
                       part_labels={"nsfw/uncensored": "NSFW / Uncensored"}, **kw)


def test_page_has_the_box_the_column_and_the_part_labels():
    html = page()
    assert 'id="latestOnly"' in html
    data = json.loads(html.split('id="board-data">', 1)[1].split("</script>", 1)[0])
    assert "base" in [c["key"] for c in data["extra"]]
    assert data["part_labels"] == {"nsfw/uncensored": "NSFW / Uncensored"}


def test_a_board_without_tags_is_unchanged():
    html = render_html(rows(), "sub", components=COMPS, footer=["f"])
    assert 'id="latestOnly"' not in html
    data = json.loads(html.split('id="board-data">', 1)[1].split("</script>", 1)[0])
    assert "base" not in [c["key"] for c in data["extra"]]


@needs_node
def test_box_hides_the_older_row_and_is_saved():
    out = run_board(page(), """
      out.all = order();
      el('latestOnly').checked = true; el('latestOnly').fire('change', {target: el('latestOnly')});
      out.latest = order(); out.count = el('count').innerHTML; await wait(450); out.saved = saved();
      out.tag = /older version/.test(el('tbody').innerHTML);
      el('latestOnly').checked = false; el('latestOnly').fire('change', {target: el('latestOnly')});
      out.back = order(); out.tag2 = /older version/.test(el('tbody').innerHTML);
      out.max = /class="max-tag"/.test(el('tbody').innerHTML);
      out.maxTitle = (el('tbody').innerHTML.match(/max-tag" title="([^"]+)"/) || [])[1];
    """)
    assert out["all"] == "m0,m1,m2" and out["back"] == "m0,m1,m2"
    assert out["latest"] == "m1,m2"
    assert "1 older version hidden" in out["count"]
    assert out["saved"]["latest"] is True
    assert out["tag2"] is True and out["max"] is True
    assert "NSFW / Uncensored" in out["maxTitle"]


@needs_node
def test_text_filter_matches_the_built_on_tag():
    out = run_board(page(), """
      el('filter').value = 'gemma 4'; el('filter').fire('input', {target: el('filter')});
      out.hit = order();
    """)
    assert out["hit"] == "m0"


@needs_node
def test_saved_latest_is_dropped_when_no_row_is_superseded():
    out = run_board(render_html(rows(), "sub", components=COMPS, footer=["f"]), """
      out.order = order();
    """, store={"crucibleforge-board-layout": json.dumps({
        "version": 2, "columns": ["c:RP"], "sortKey": "rank", "sortDir": 1, "filters": {}, "text": "",
        "benched": False, "latest": True, "expanded": [], "view": "table",
        "classes": [], "leans": [], "current": False, "where": ""})})
    assert out["order"] == "m0,m1,m2"


def test_html_rows_carry_the_new_keys():
    from test_run_cost_20261107 import CHAT, _row
    cfg = {"providers": {"api": {"type": "openai", "base_url": "http://x"}},
           "models": [{"name": "m", "provider": "api", "base": "Claude Opus", "price": {"input": 1.0, "output": 2.0}}]}
    s = report.model_stats("m", cfg, rows=[_row(CHAT, cost=0.4)], meta={"provider_type": "openai"}, era=None)
    s.update(report.row_facts("m", {"provider_type": "openai"}, cfg))
    s["scorecard"] = report.scorecard(s, report.scoring_config(cfg))
    s["headline"] = {"maxed": {"nsfw": {"parts": ["uncensored", "nsfw_writing"], "category": False},
                               "chat": {"parts": [], "category": False}}}
    h = report.html_rows(["m"], {"m": s})[0]
    assert h["base"] == "Claude Opus" and h["official"] is True and h["superseded_by"] is None
    assert h["maxed_parts"] == ["nsfw/uncensored", "nsfw/nsfw_writing"]
    s["headline"] = {"maxed": {"nsfw": {"parts": [], "category": False}}}
    assert report.html_rows(["m"], {"m": s})[0]["maxed_parts"] is None
