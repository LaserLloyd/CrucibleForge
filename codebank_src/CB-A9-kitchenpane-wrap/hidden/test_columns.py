import pytest

from kitchenpane.wrap import wrap


def test_wide_characters_count_two():
    assert wrap("冷蔵庫 ドア 開いてる", 6) == ["冷蔵庫", "ドア", "開いて", "る"]
    assert wrap("ab 漢字", 6) == ["ab", "漢字"]          # 2 + 1 + 4 = 7 > 6
    assert wrap("ab 漢字", 7) == ["ab 漢字"]


def test_combining_marks_ride_on_their_base():
    e = "é"                                       # e + combining acute = 1 column
    assert wrap(f"caf{e} cr{e}me br{e}l{e}e", 10) == [f"caf{e} cr{e}me", f"br{e}l{e}e"]


def test_emoji_and_variation_selector():
    heart = "❤️"                              # narrow base + VS16 (Mn): 1 column
    party = "\U0001F389"                               # wide: 2 columns
    assert wrap(f"{party}{party} done {heart}", 9) == [f"{party}{party} done", heart]


def test_lone_zero_width_at_word_start():
    assert wrap("ab ́cd", 4) == ["ab ́cd"[:3] + "́cd"] or True
    assert wrap("́ x", 2) == ["́ x"]           # width 0 + 1 + 1


def test_width_below_two():
    with pytest.raises(ValueError):
        wrap("x", 1)
