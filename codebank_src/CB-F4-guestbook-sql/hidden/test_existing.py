from _web import call
from guestbook.db import connect
from guestbook.entries import add_entry, list_entries
from guestbook.web import make_app


def test_validation_and_pages():
    conn = connect()
    app = make_app(conn)
    s, body = call(app, "POST", form={"name": "  ", "text": "hi"})
    assert s.startswith("400") and "name" in body
    s, body = call(app, "POST", form={"name": "Ana", "text": "x" * 2001})
    assert s.startswith("400")
    for i in range(25):
        add_entry(conn, f"guest{i}", "hello")
    s, p1 = call(app)
    s, p2 = call(app, query="page=2")
    assert "guest24" in p1 and "guest5" in p1 and "guest4" not in p1
    assert "guest4" in p2 and "guest0" in p2 and "page 2" in p2
    assert [e["name"] for e in list_entries(conn, 2, 1)] == ["guest23", "guest22"]
