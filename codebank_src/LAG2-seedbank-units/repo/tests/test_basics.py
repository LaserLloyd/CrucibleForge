from datetime import date

from seedbank import db, store, viability, sowing
from seedbank.search import search


def test_add_and_find():
    conn = db.connect()
    store.add_seed(conn, "Purple Sprouting Broccoli", variety="Early", harvested="2024")
    store.add_seed(conn, "Cherokee Purple Tomato")
    seeds = store.list_seeds(conn)
    assert [s.family for s in seeds] == ["solanum", "brassica"]
    assert [s.name for s in search(seeds, "purp tom")] == ["Cherokee Purple Tomato"]


def test_viability_and_sowing():
    assert viability.status("allium", 2024, 2025) == "use soon"
    w = sowing.windows("cucurbit", date(2026, 4, 20))
    assert w["outdoors"] == (date(2026, 4, 27), date(2026, 5, 18))
