from guestbook.db import connect
from guestbook.entries import add_entry, list_entries

TRICKY = ["O'Brien", "Dan\\'s \"place\"", "100% great; DROP TABLE entries; --", "emoji 👍🏽 and\nnewline",
          "x', 'y'); DELETE FROM entries; --", "back\\slash"]


def test_any_text_round_trips():
    conn = connect()
    for t in TRICKY:
        add_entry(conn, t, t)
    got = list_entries(conn, 50)
    assert [e["text"] for e in got] == [t.strip() for t in reversed(TRICKY)]
    assert [e["name"] for e in got] == [t.strip() for t in reversed(TRICKY)]
    assert conn.execute("SELECT count(*) FROM entries").fetchone()[0] == len(TRICKY)
