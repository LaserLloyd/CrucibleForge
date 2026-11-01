from kitchenpane.pane import render
from kitchenpane.wrap import wrap


def test_plain_ascii():
    assert wrap("take the bins out before eight tonight", 12) == ["take the", "bins out", "before eight", "tonight"]


def test_render_indents_continuations():
    assert render(["dishwasher finished at 21:40"], 16) == ["* dishwasher", "  finished at", "  21:40"]


def test_wide_names():
    # each kanji is 2 columns on the display
    assert wrap("買い物 リスト 更新", 8) == ["買い物", "リスト", "更新"]
