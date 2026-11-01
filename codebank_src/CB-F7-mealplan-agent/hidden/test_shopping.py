from _mp import load
from mealplan.plan import Week, shopping


def test_week_totals():
    load()
    w = Week()
    w.add("mon", "pancakes", 6)        # x1.5
    w.add("wed", "bread", 4)           # x0.5
    w.add("fri", "soup", 3)            # x1.5
    assert shopping(w) == [("carrots", 450.0, "g"), ("eggs", 3.0, "pcs"), ("flour", 625.0, "g"),
                           ("milk", 720.0, "ml"), ("salt", None, "to taste"), ("stock", 1125.0, "ml"),
                           ("sugar", 33.8, "ml"), ("water", 175.0, "ml"), ("yeast", 3.5, "g")]
