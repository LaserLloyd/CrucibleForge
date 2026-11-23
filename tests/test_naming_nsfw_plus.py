"""Visitor-facing text never says "adult page" / "adult test" (spec section 6)."""
import re
from pathlib import Path

import pytest

from crucibleforge import cli

ROOT = Path(__file__).resolve().parent.parent
DOCS = ["README.md", "docs/CLI.md", "docs/RUNNING.md", "docs/SCORING.md", "docs/SCORING-RULES-45.md",
        "docs/WHAT-IS-TESTED.md", "docs/FIXCHECK.md"]
BAD = re.compile(r"adult (page|test)\b", re.I)


@pytest.mark.parametrize("doc", DOCS)
def test_docs_use_nsfw_plus(doc):
    assert not BAD.search((ROOT / doc).read_text(encoding="utf-8")), doc


def test_changelog_states_the_rename_once():
    t = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert t.count("Renamed: the adult page is now **NSFW+**") == 1


def test_plan_lines_say_nsfw_plus():
    from crucibleforge import adult_guard as G
    assert G.OPT_IN_LINE == "NSFW+: not run (--no-nsfw-plus)"
    assert not BAD.search(G.PLAN_LINE)


@pytest.mark.parametrize("verb", ["run", "all", "recover", "escalate", "bench-all", "report"])
def test_cli_help_has_no_adult_page(verb, capsys):
    with pytest.raises(SystemExit):
        cli.main([verb, "--help"])
    assert not BAD.search(capsys.readouterr().out)


def _help_text(*argv):
    import io
    from contextlib import redirect_stdout
    buf = io.StringIO()
    with redirect_stdout(buf), pytest.raises(SystemExit):
        cli.main([*argv, "--help"])
    return buf.getvalue()


def _verbs():
    m = re.search(r"\{([a-z0-9,-]+)\}", _help_text())
    assert m
    return m.group(1).split(",")


def test_every_cli_verb_help_has_no_adult_page():
    for verb in _verbs():
        assert not BAD.search(_help_text(verb)), verb


def test_hidden_aliases_stay_out_of_help():
    out = _help_text("all")
    assert "--no-nsfw-plus" in out
    assert "--no-adult" not in out and "--adult " not in out


def test_guard_messages_say_nsfw_plus():
    from crucibleforge import adult_guard as G
    for msg in (G.PLAN_LINE, G.OPT_IN_LINE):
        assert not BAD.search(msg) and "NSFW+" in msg


# ---- a rendered board (Markdown and HTML) -----------------------------------------------------------------------
FIXTURE = ROOT / "tests" / "fixtures" / "board45_w6"


def _visible_html(text):
    text = re.sub(r"<script.*?</script>", " ", text, flags=re.S)
    text = re.sub(r"<style.*?</style>", " ", text, flags=re.S)
    return re.sub(r"<[^>]+>", " ", text)


def test_rendered_board_markdown_and_html_use_nsfw_plus():
    md = (FIXTURE / "report.md").read_text(encoding="utf-8")
    page = (FIXTURE / "report.html").read_text(encoding="utf-8")
    assert not BAD.search(md) and not BAD.search(_visible_html(page))   # script comments are not read by a visitor
    assert '<a id="nsfw-plus"></a>' in md and 'id="nsfw-plus"' in page
    for col in ("Overall /20", "Code /10", "Chat /10", "NSFW /10", "Uncensored /10"):
        assert col in md and col in page
    assert "NSFW + Uncensored" not in md and "NSFW + Uncensored" not in page


def test_board_shows_no_summed_nsfw_plus_figure_and_no_old_section_names():
    md = (FIXTURE / "report.md").read_text(encoding="utf-8")
    vis = _visible_html((FIXTURE / "report.html").read_text(encoding="utf-8"))
    for text in (md, vis):
        assert "/20)" not in text.split("## NSFW+")[-1].split("###")[0]
        assert "Role-play and memory" not in text and "Coding (small tasks)" not in text


# ---- wording rules for everything a visitor reads --------------------------------------------------------------
WORDING_FILES = DOCS + ["CHANGELOG.md"]
PRIVATE = re.compile(r"/var/home|/home/\w+|~/Documents|\bpuppy\b|\bJake\b|\bbluefin\b|\bbuild4\b|\.ts\.net|\bW[0-6]\b|\blane W")


@pytest.mark.parametrize("doc", DOCS)
def test_docs_have_no_em_dash_and_nothing_private(doc):
    t = (ROOT / doc).read_text(encoding="utf-8")
    assert "\u2014" not in t, doc
    assert not PRIVATE.search(t), (doc, PRIVATE.search(t).group(0))


def test_changelog_has_nothing_private():
    t = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    top = t.split("## Unreleased", 1)[0]
    assert not PRIVATE.search(top), PRIVATE.search(top).group(0)


@pytest.mark.shipping_default
def test_what_is_tested_names_every_segment_with_its_points():
    from crucibleforge import profiles, segments45
    t = (ROOT / "docs" / "WHAT-IS-TESTED.md").read_text(encoding="utf-8")
    tree = segments45.tree(dict(profiles.load_profile("bench45", {})))
    for sec in segments45.SECTIONS:
        for g in tree[sec].values():
            if isinstance(g, dict):
                assert re.search(rf"\| {re.escape(g['label'])} \| {g['max']:g} \|", t), g["label"]
    for lab, pts in (("Erotica", "4"), ("Adult role-play", "2.5"), ("Range", "2"), ("Anatomy", "1.5"),
                     ("Willingness", "6"), ("Knowledge", "4")):
        assert re.search(rf"\| {lab} \| {pts} \|", t), lab
    assert "Nothing on NSFW+ scores taste" in t


# ---- the pre-realignment score tree never reaches a public output ------------------------------------------------
OLD_TREE_LABELS = re.compile(r"Scene quality|Writing craft|Sensation|Peak scene|j_rplong|pending validation|"
                             r"Role-play and memory|Coding \(small tasks\)|Every category is out of 10|"
                             r"Role-play: long scenes|NSFW / ")


def test_rendered_board_data_and_text_carry_no_old_tree_labels():
    md = (FIXTURE / "report.md").read_text(encoding="utf-8")
    page = (FIXTURE / "report.html").read_text(encoding="utf-8")        # data (the json block) AND text
    for name, text in (("report.md", md), ("report.html", page)):
        m = OLD_TREE_LABELS.search(text)
        assert not m, (name, m.group(0))


def test_new_board_title_and_columns():
    md = (FIXTURE / "report.md").read_text(encoding="utf-8")
    page = (FIXTURE / "report.html").read_text(encoding="utf-8")
    assert md.startswith("# CrucibleForge: Chat & Coding")
    assert "<header><h1>CrucibleForge: Chat &amp; Coding</h1>" in page and "tiers_cell" not in page.split('id="board-data"')[1].split("</script>")[0]
    assert "—" not in md.splitlines()[0]


BOX_PATH = re.compile("|".join(["/var/" + "tmp/mb45", "mb45" + "-realign", "mb45" + "-runs", "/ho" + "me/pup" + "py"]))


def test_test_files_name_no_box_paths():
    hits = []
    for f in sorted((ROOT / "tests").glob("test_*.py")):
        if f.name == Path(__file__).name:
            continue
        for i, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            if BOX_PATH.search(line):
                hits.append(f"{f.name}:{i}")
    assert not hits, hits
