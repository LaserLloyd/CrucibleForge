from _seedfix import call

from seedbank import db
from seedbank.api import make_app


def _app():
    conn = db.connect()
    return conn, make_app(conn)


def _count(conn):
    return conn.execute("SELECT count(*) FROM seeds").fetchone()[0]


def test_create_both_ways():
    conn, app = _app()
    st, b = call(app, "POST", "/seeds", {"name": "Kale", "quantity": "About 30 seeds"})
    assert st == 201
    assert b["amount"] == {"value": "30", "unit": "seed", "approx": True} and b["quantity"] == "about 30 seeds"
    st, b = call(app, "POST", "/seeds", {"name": "Pea", "amount": {"value": 1.5, "unit": "packet"}})
    assert st == 201 and b["amount"] == {"value": "1.5", "unit": "packet", "approx": False}
    assert b["quantity"] == "1.5 packets"
    st, b = call(app, "POST", "/seeds", {"name": "Dill", "amount": {"value": "0.0005", "unit": "gram", "approx": True}})
    assert st == 201 and b["amount"] == {"value": "0.001", "unit": "gram", "approx": True}
    st, b = call(app, "POST", "/seeds", {"name": "Leek", "amount": {"value": 3, "unit": "seed"}})
    assert st == 201 and b["quantity"] == "3 seeds"
    st, b = call(app, "POST", "/seeds", {"name": "Bare"})
    assert st == 201 and b["amount"] is None and b["quantity"] == ""
    st, b = call(app, "GET", "/seeds/%d" % b["id"])
    assert st == 200 and b["amount"] is None
    st, b = call(app, "GET", "/seeds/1")
    assert b["amount"]["approx"] is True and b["quantity"] == "about 30 seeds"


def test_rejections_save_nothing():
    conn, app = _app()
    bad = [{"name": "A", "quantity": "2 packets", "amount": {"value": 2, "unit": "packet"}},
           {"name": "A", "amount": {"value": "2.5", "unit": "seed"}},
           {"name": "A", "amount": {"value": 2, "unit": "bag"}},
           {"name": "A", "amount": {"value": "1/2", "unit": "packet"}},
           {"name": "A", "amount": {"value": -1, "unit": "gram"}},
           {"name": "A", "amount": {"value": "abc", "unit": "gram"}},
           {"name": "A", "amount": {"value": 2, "unit": "gram", "approx": "yes"}},
           {"name": "A", "amount": {"unit": "gram"}},
           {"name": "A", "amount": {"value": 2}},
           {"name": "A", "amount": {"value": True, "unit": "gram"}},
           {"name": "A", "quantity": "2 packets from Gran"},
           {"name": "A", "quantity": 7}]
    for body in bad:
        st, b = call(app, "POST", "/seeds", body)
        assert st == 400 and "error" in b, body
    assert _count(conn) == 0
    st, b = call(app, "POST", "/seeds", bad[0])
    assert b["error"] == "give amount or quantity, not both"


def test_patch():
    conn, app = _app()
    _, s = call(app, "POST", "/seeds", {"name": "Kale", "quantity": "2 packets"})
    path = "/seeds/%d" % s["id"]
    st, b = call(app, "PATCH", path, {"notes": "swap"})
    assert st == 200 and b["quantity"] == "2 packets" and b["notes"] == "swap"
    st, b = call(app, "PATCH", path, {"amount": {"value": "0.25", "unit": "packet"}})
    assert b["amount"] == {"value": "0.25", "unit": "packet", "approx": False}
    st, b = call(app, "PATCH", path, {"amount": {"value": 2, "unit": "seed"}, "quantity": "1"})
    assert st == 400
    st, b = call(app, "PATCH", path, {"quantity": "nonsense here"})
    assert st == 400
    assert call(app, "GET", path)[1]["quantity"] == "0.25 packets"
    st, b = call(app, "PATCH", path, {"quantity": ""})
    assert st == 200 and b["amount"] is None and b["quantity"] == ""
    call(app, "PATCH", path, {"quantity": "~5 g"})
    st, b = call(app, "PATCH", path, {"amount": None})
    assert st == 200 and b["amount"] is None
    row = conn.execute("SELECT amount_milli, unit, approx FROM seeds WHERE id = ?", (s["id"],)).fetchone()
    assert tuple(row) == (None, None, 0)


def test_filter_and_totals():
    conn, app = _app()
    assert call(app, "GET", "/totals") == (200, {})
    for name, q in [("Kale", "2 packets"), ("Pea", "1/2 packet"), ("Bean", "about 40 seeds"), ("Leek", "12"),
                    ("Dill", "0.5 oz"), ("Chard", ""), ("Beet", "1 packet")]:
        assert call(app, "POST", "/seeds", {"name": name, "quantity": q})[0] == 201
    st, b = call(app, "GET", "/seeds", query="unit=packet")
    assert st == 200 and [s["name"] for s in b] == ["Beet", "Kale", "Pea"]
    assert call(app, "GET", "/seeds", query="unit=bag")[0] == 400
    assert len(call(app, "GET", "/seeds")[1]) == 7
    st, b = call(app, "GET", "/totals")
    assert st == 200 and b == {"packet": {"total": "3.5", "approx": False},
                               "seed": {"total": "52", "approx": True},
                               "gram": {"total": "14.175", "approx": False}}
