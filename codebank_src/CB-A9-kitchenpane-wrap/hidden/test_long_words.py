from kitchenpane.wrap import wrap


def test_long_word_starts_new_line_and_fill_continues():
    assert wrap("to abcdefgh ij", 4) == ["to", "abcd", "efgh", "ij"]
    assert wrap("abcdefg hi", 4) == ["abcd", "efg", "hi"]
    assert wrap("abcdef g", 4) == ["abcd", "ef g"]


def test_wide_cluster_never_straddles():
    assert wrap("漢字漢字漢", 5) == ["漢字", "漢字", "漢"]
    assert wrap("a漢字漢", 4) == ["a漢", "字漢"]


def test_paragraphs_and_spaces():
    assert wrap("  a   b  \n\n c ", 10) == ["a b", "", "c"]
    assert wrap("", 5) == [""]
