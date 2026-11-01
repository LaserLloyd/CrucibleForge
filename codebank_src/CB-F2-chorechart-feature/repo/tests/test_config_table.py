from chorechart.config import load
from chorechart.table import render


def test_load(tmp_path):
    p = tmp_path / "rota.txt"
    p.write_text("members: Ana, Ben\n# comment\nchores: dishes, bins\n")
    assert load(p) == (["Ana", "Ben"], ["dishes", "bins"])


def test_render():
    assert render([(1, {"dishes": "Ana", "bins": None})]) == "week   | 1\ndishes | Ana\nbins   | -"
