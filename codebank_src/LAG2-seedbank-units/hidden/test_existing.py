from datetime import date

from _seedfix import call

from seedbank import db, sowing, store, viability
from seedbank.api import make_app
from seedbank.search import search


def test_store_and_search():
    conn = db.connect()
    a = store.add_seed(conn, "Purple Sprouting Broccoli", variety="Early", harvested="2023", notes=" keep ")
    store.add_seed(conn, "Cherokee Purple Tomato", source="Gran")
    assert a.family == "brassica" and a.harvested == 2023 and a.notes == " keep "
    assert [s.name for s in search(store.list_seeds(conn), "purple")] == ["Cherokee Purple Tomato",
                                                                           "Purple Sprouting Broccoli"]
    assert store.update_seed(conn, a.id, variety="Late").variety == "Late"
    assert store.families(conn) == ["brassica", "solanum"]
    assert [s.name for s in store.list_seeds(conn, family="solanum")] == ["Cherokee Purple Tomato"]


def test_viability_and_windows():
    assert viability.good_until("cucurbit", 2020) == 2025
    assert viability.status("", 2024, 2026) == "use soon"
    assert viability.status("legume", None, 2026) == "unknown"
    assert sowing.what_to_sow(["legume", "solanum"], date(2026, 4, 20), date(2026, 4, 10)) == [("legume", "outdoors")]


def test_api_basics():
    conn = db.connect()
    app = make_app(conn)
    st, s = call(app, "POST", "/seeds", {"name": "Runner Bean", "harvested": 2024})
    assert st == 201 and s["family"] == "legume" and s["name"] == "Runner Bean"
    sid = s["id"]
    assert call(app, "POST", "/seeds/%d/sowings" % sid, {"sown_on": "2026-05-01", "bed": "A", "count": 8})[0] == 201
    st, w = call(app, "GET", "/seeds/%d/sowings" % sid)
    assert [(x["bed"], x["count"]) for x in w] == [("A", 8)]
    st, v = call(app, "GET", "/viability", query="year=2027")
    assert v == [{"id": sid, "name": "Runner Bean", "status": "use soon"}]
    assert call(app, "POST", "/seeds", {"variety": "x"})[0] == 400
    assert call(app, "DELETE", "/seeds/%d" % sid)[0] == 204
    assert conn.execute("SELECT count(*) FROM sowings").fetchone()[0] == 0
    assert call(app, "GET", "/seeds/%d" % sid)[0] == 404
