import json

from _http import call
from dropbox.app import make_app
from dropbox.paths import safe_path


def test_legal_names(tmp_path):
    root = tmp_path / "drop"
    root.mkdir()
    cases = {
        "a..b.txt": "a..b.txt",
        "my%20file.txt": "my file.txt",
        "%252e%252e/x": "%2e%2e/x",
        "photos/./2026//img.jpg": "photos/2026/img.jpg",
        "%E5%90%8D%E5%89%8D.txt": "名前.txt",
        "x\\y.txt": "x/y.txt",
        "a/b/../c.txt": "a/c.txt",
        "..hidden": "..hidden",
        "notes..": "notes..",
        "....//x": "..../x",
        "/etc/passwd": "etc/passwd",
    }
    for given, want in cases.items():
        p = safe_path(root, given)
        assert p.relative_to(root).as_posix() == want, given


def test_app_stores_decoded_name(tmp_path):
    app = make_app(tmp_path)
    status, body = call(app, "PUT", "/files/trip/day%201/a..b.txt", b"x")
    assert status == 201 and json.loads(body) == {"stored": "trip/day 1/a..b.txt"}
    assert (tmp_path / "trip" / "day 1" / "a..b.txt").read_bytes() == b"x"
    assert call(app, "GET", "/files/trip/day%201/a..b.txt") == (200, b"x")
