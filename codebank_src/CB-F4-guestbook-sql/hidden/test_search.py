from guestbook.db import connect
from guestbook.entries import add_entry, search


def _book():
    conn = connect()
    for name, text in [("Ana", "50% off at the bakery"), ("Ben", "500 steps to the beach"), ("Cy", "a_b test"),
                       ("Dee", "aXb test"), ("Eli", "path C:\\temp ok"), ("Fay", "it's O'Brien's pub"),
                       ("GRETA", "Lovely VIEW")]:
        add_entry(conn, name, text)
    return conn


def names(rows):
    return [r["name"] for r in rows]


def test_wildcards_are_literal():
    conn = _book()
    assert names(search(conn, "50%")) == ["Ana"]
    assert names(search(conn, "a_b")) == ["Cy"]
    assert names(search(conn, "C:\\temp")) == ["Eli"]
    assert names(search(conn, "%")) == ["Ana"]


def test_quotes_and_case():
    conn = _book()
    assert names(search(conn, "o'brien")) == ["Fay"]
    assert names(search(conn, "view")) == ["GRETA"]
    assert names(search(conn, "greta")) == ["GRETA"]
    assert names(search(conn, "' OR 1=1 --")) == []
    assert search(conn, "") == []


def test_limit_and_order():
    conn = connect()
    for i in range(30):
        add_entry(conn, f"g{i}", "same words")
    got = search(conn, "words", limit=5)
    assert names(got) == ["g29", "g28", "g27", "g26", "g25"]
