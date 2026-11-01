from _seedfix import call

from seedbank import db
from seedbank.api import make_app


def _setup():
    conn = db.connect()
    app = make_app(conn)
    ids = {}
    for name, q in [("Kale", "2 packets"), ("Dill", "about 30 g"), ("Bean", "40 seeds"), ("Chard", "")]:
        ids[name] = call(app, "POST", "/seeds", {"name": name, "quantity": q})[1]["id"]
    return conn, app, ids


def _sowings(conn):
    return conn.execute("SELECT count(*) FROM sowings").fetchone()[0]


def test_use_takes_stock():
    conn, app, ids = _setup()
    st, w = call(app, "POST", "/seeds/%d/sowings" % ids["Kale"],
                 {"sown_on": "2026-04-01", "bed": "A", "used": {"value": "0.5", "unit": "packet"}})
    assert st == 201 and w["bed"] == "A"
    assert call(app, "GET", "/seeds/%d" % ids["Kale"])[1]["quantity"] == "1.5 packets"
    st, _ = call(app, "POST", "/seeds/%d/sowings" % ids["Dill"], {"sown_on": "2026-04-02", "used": "1 oz"})
    assert st == 201
    b = call(app, "GET", "/seeds/%d" % ids["Dill"])[1]
    assert b["amount"] == {"value": "1.65", "unit": "gram", "approx": True}
    st, _ = call(app, "POST", "/seeds/%d/sowings" % ids["Bean"],
                 {"sown_on": "2026-05-02", "used": {"value": 40, "unit": "seed", "approx": True}})
    assert st == 201
    b = call(app, "GET", "/seeds/%d" % ids["Bean"])[1]
    assert b["amount"] == {"value": "0", "unit": "seed", "approx": False} and b["quantity"] == "0 seeds"
    st, _ = call(app, "POST", "/seeds/%d/sowings" % ids["Chard"], {"sown_on": "2026-05-02", "count": 3})
    assert st == 201 and _sowings(conn) == 4


def test_conflicts_save_nothing():
    conn, app, ids = _setup()
    cases = [(ids["Chard"], {"value": 1, "unit": "seed"}, 409, "seed has no amount"),
             (ids["Kale"], {"value": 1, "unit": "gram"}, 409, "unit mismatch"),
             (ids["Kale"], "3 seeds", 409, "unit mismatch"),
             (ids["Kale"], {"value": "2.001", "unit": "packet"}, 409, "not enough seed"),
             (ids["Dill"], "2 oz", 409, "not enough seed"),
             (ids["Bean"], {"value": "1.5", "unit": "seed"}, 400, None),
             (ids["Bean"], "a handful", 400, None),
             (ids["Bean"], {"value": -1, "unit": "seed"}, 400, None)]
    for sid, used, status, msg in cases:
        st, b = call(app, "POST", "/seeds/%d/sowings" % sid, {"sown_on": "2026-04-01", "used": used})
        assert st == status, (used, st, b)
        if msg:
            assert b["error"] == msg
    assert _sowings(conn) == 0
    got = {b["name"]: b["quantity"] for b in call(app, "GET", "/seeds")[1]}
    assert got == {"Kale": "2 packets", "Dill": "about 30 g", "Bean": "40 seeds", "Chard": ""}
    st, _ = call(app, "POST", "/seeds/%d/sowings" % ids["Kale"],
                 {"sown_on": "2026-04-01", "used": {"value": 2, "unit": "packet"}})
    assert st == 201 and call(app, "GET", "/seeds/%d" % ids["Kale"])[1]["quantity"] == "0 packets"
