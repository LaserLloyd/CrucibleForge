from kitchenpane.wrap import wrap

R, G, B, X, X2 = "\x1b[31m", "\x1b[1;32m", "\x1b[1m", "\x1b[0m", "\x1b[m"


def test_style_reopened_and_closed_per_line():
    assert wrap(f"{R}freezer door open{X} now", 8) == [f"{R}freezer{X}", f"{R}door{X}", f"{R}open{X} now"]


def test_style_carries_across_paragraphs():
    assert wrap(f"{R}alarm\nstill on", 20) == [f"{R}alarm{X}", f"{R}still on{X}"]
    assert wrap(f"{R}alarm\n\nend{X}", 20) == [f"{R}alarm{X}", f"{R}{X}", f"{R}end{X}"]


def test_styles_stack_and_short_reset():
    assert wrap(f"{G}a {B}b c{X2} d", 3) == [f"{G}a {B}b{X}", f"{G}{B}c{X2} d"]


def test_trailing_and_inner_sgr():
    assert wrap(f"abcdef{R}", 3) == ["abc", f"def{R}{X}"]
    assert wrap(f"ab{R}cdef", 3) == ["ab" + R + "c" + X, f"{R}def{X}"]


def test_no_reset_added_without_style():
    assert wrap(f"{R}x{X} y z", 3) == [f"{R}x{X} y", "z"]
