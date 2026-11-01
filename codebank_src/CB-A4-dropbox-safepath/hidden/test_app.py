import json

from _http import call
from dropbox.app import make_app


def test_routes(tmp_path):
    app = make_app(tmp_path)
    assert call(app, "PUT", "/files/a/b.txt", b"hello")[0] == 201
    assert call(app, "GET", "/files/a/b.txt") == (200, b"hello")
    s, b = call(app, "GET", "/files/a/missing.txt")
    assert s == 404 and json.loads(b) == {"error": "not found"}
    assert call(app, "DELETE", "/files/a/b.txt")[0] == 405
    assert call(app, "GET", "/other")[0] == 404
    s, b = call(app, "PUT", "/files/", b"x")
    assert s == 400 and json.loads(b) == {"error": "unsafe path"}


def test_overwrite_and_query_ignored(tmp_path):
    app = make_app(tmp_path)
    call(app, "PUT", "/files/n.txt", b"1")
    s, b = call(app, "PUT", "/files/n.txt?x=1", b"2")
    assert s == 201 and json.loads(b) == {"stored": "n.txt"}
    assert call(app, "GET", "/files/n.txt") == (200, b"2")
