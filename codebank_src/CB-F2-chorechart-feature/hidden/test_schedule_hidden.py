from chorechart.rotation import schedule
from chorechart.table import render


def test_schedule_with_absences_and_render():
    rows = schedule(["dishes", "bins"], ["Ana", "Ben", "Cy"], [5, 6], {6: {"Cy"}})
    assert rows == [(5, {"dishes": "Cy", "bins": "Ana"}), (6, {"dishes": "Ana", "bins": "Ben"})]
    assert render(rows) == "week   | 5 | 6\ndishes | Cy | Ana\nbins   | Ana | Ben"


def test_schedule_default_absences():
    assert schedule(["x"], ["A", "B"], []) == []
    assert schedule(["x"], ["A", "B"], [3]) == [(3, {"x": "B"})]
