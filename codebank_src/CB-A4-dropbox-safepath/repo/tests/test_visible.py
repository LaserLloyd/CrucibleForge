import io
import json

from dropbox.app import make_app
from dropbox.paths import UnsafePath, safe_path


def call(app, method, uri, body=b""):
    out = {}
    def start_response(status, headers):
        out["status"] = int(status.split()[0])
    env = {"REQUEST_METHOD": method, "REQUEST_URI": uri, "PATH_INFO": uri,
           "CONTENT_LENGTH": str(len(body)), "wsgi.input": io.BytesIO(body)}
    data = b"".join(app(env, start_response))
    return out["status"], data


def test_put_then_get(tmp_path):
    app = make_app(tmp_path)
    status, body = call(app, "PUT", "/files/photos/cat.jpg", b"meow")
    assert status == 201 and json.loads(body) == {"stored": "photos/cat.jpg"}
    assert call(app, "GET", "/files/photos/cat.jpg") == (200, b"meow")


def test_plain_traversal_rejected(tmp_path):
    import pytest
    with pytest.raises(UnsafePath):
        safe_path(tmp_path, "")
