from guestbook.db import connect
from guestbook.entries import add_entry, list_entries, search


def test_add_and_list():
    conn = connect()
    add_entry(conn, "Ana", "Lovely view")
    add_entry(conn, "Ben", "Great coffee")
    assert [e["name"] for e in list_entries(conn)] == ["Ben", "Ana"]


def test_search_plain():
    conn = connect()
    add_entry(conn, "Ana", "Lovely view")
    assert [e["name"] for e in search(conn, "view")] == ["Ana"]
