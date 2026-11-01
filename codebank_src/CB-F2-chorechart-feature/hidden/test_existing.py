import pytest

from chorechart.config import load
from chorechart.table import render


def test_config(tmp_path):
    p = tmp_path / "r.txt"
    p.write_text("Members: Ana ,Ben,, Cy\nchores:dishes\n")
    assert load(p) == (["Ana", "Ben", "Cy"], ["dishes"])
    p.write_text("pets: Rex\n")
    with pytest.raises(ValueError):
        load(p)


def test_render_empty():
    assert render([]) == ""
