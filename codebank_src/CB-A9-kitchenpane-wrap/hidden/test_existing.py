from kitchenpane.pane import render
from kitchenpane.wrap import wrap


def test_ascii_wrapping_unchanged():
    assert wrap("take the bins out before eight tonight", 12) == ["take the", "bins out", "before eight", "tonight"]
    assert wrap("one\ntwo three", 20) == ["one", "two three"]


def test_pane_bullets():
    assert render(["dishwasher finished at 21:40", "rice cooker done"], 16) == [
        "* dishwasher", "  finished at", "  21:40", "* rice cooker", "  done"]
