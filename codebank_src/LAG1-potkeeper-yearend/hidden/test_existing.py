import io
import json
from datetime import date

import pytest

from potkeeper import cli, store
from potkeeper.household import Household, Member
from potkeeper.ledger import Ledger
from potkeeper.money import format_cents


def test_format_and_household():
    assert format_cents(0) == "0.00" and format_cents(5) == "0.05" and format_cents(123456789) == "1,234,567.89"
    with pytest.raises(ValueError):
        Household([Member("A", 0)])
    with pytest.raises(ValueError):
        Household([Member("A"), Member("A")])


def test_cli_import_and_month(tmp_path):
    csv_path = tmp_path / "s.csv"
    csv_path.write_text("date,amount,payee\n2026-05-02,-12.00,Bakery\n2026-05-03,100.00,Gift\n", encoding="utf-8")
    led = str(tmp_path / "ledger.json")
    out = io.StringIO()
    assert cli.main(["--ledger", led, "import", str(csv_path)], out=out) == 0
    assert "added 2, skipped 0" in out.getvalue()
    rules = tmp_path / "r.txt"
    rules.write_text("bakery => food\n", encoding="utf-8")
    cli.main(["--ledger", led, "categorise", str(rules)], out=io.StringIO())
    out = io.StringIO()
    cli.main(["--ledger", led, "month", "2026-05"], out=out)
    assert "income 100.00  spending 12.00" in out.getvalue()
    assert [t.category for t in store.load_ledger(led)] == ["food", "uncategorised"]


def test_cli_year_end_json(tmp_path):
    led = Ledger()
    led.add(date(2026, 6, 1), -1000, "Shop", shared=True, paid_by="A")
    store.save_ledger(led, str(tmp_path / "l.json"))
    (tmp_path / "h.json").write_text(json.dumps(Household([Member("A"), Member("B")]).to_json()), encoding="utf-8")
    out = io.StringIO()
    cli.main(["--ledger", str(tmp_path / "l.json"), "--household", str(tmp_path / "h.json"), "year-end", "2026", "--json"], out=out)
    r = json.loads(out.getvalue())
    assert r["settle"] == [["B", "A", 500]]


def test_goals_tags_export_api():
    from potkeeper.api import make_app
    from potkeeper.export import to_csv, to_json
    from potkeeper.goals import Goal, monthly_needed
    from potkeeper.tags import add_tags, clean, tag_totals, tagged
    g = Goal("bike", 50000, "2026-12", saved=10001)
    assert monthly_needed(g, "2027-01") == 39999 and monthly_needed(Goal("x", 5, "2026-01", 9), "2025-01") == 0
    assert clean([" Trip", "trip", "", "KIDS"]) == ["trip", "kids"]
    led = Ledger()
    t = led.add(date(2026, 7, 2), -123456, "Hotel, Lake", category="travel", memo="2 nights")
    add_tags(t, "Trip", "summer")
    led.add(date(2026, 7, 3), -500, "Ice", tags=["trip"])
    assert [x.id for x in tagged(led, "TRIP")] == [1, 2]
    assert tag_totals(led, date(2026, 7, 1), date(2026, 7, 31)) == {"trip": -123956, "summer": -123456}
    assert to_csv(led).splitlines()[:2] == ["id,date,amount,payee,category,memo",
                                            '1,2026-07-02,-1234.56,"Hotel, Lake",travel,2 nights']
    assert json.loads(to_json(led))[1] == {"id": 2, "date": "2026-07-03", "amount": -500, "payee": "Ice",
                                           "category": None, "memo": "", "shared": False, "paid_by": None,
                                           "tags": ["trip"]}
    app = make_app(led, Household([Member("A")]), [g], today=lambda: date(2026, 9, 15))
    out = {}
    def call(path, method="GET"):
        body = b"".join(app({"PATH_INFO": path, "REQUEST_METHOD": method}, lambda s, h: out.__setitem__("s", s)))
        return out["s"], json.loads(body)
    st, body = call("/goals")
    assert st == "200 OK" and [g["name"] for g in body] == ["bike"] and body[0]["due"] == "2026-12"
    assert call("/month/2026-13")[0].startswith("400") and call("/nope")[0].startswith("404")
    assert call("/month/2026-07", "POST")[0].startswith("405")
    assert call("/month/2026-06")[1] == {"income": 0, "spending": 0, "by_category": []}
