"""WSGI front end."""
import json
from pathlib import Path

from .paths import UnsafePath
from .store import load, save

PREFIX = "/files/"


def _json(start_response, status, obj):
    body = json.dumps(obj).encode()
    start_response(status, [("Content-Type", "application/json"), ("Content-Length", str(len(body)))])
    return [body]


def make_app(root):
    root = Path(root)

    def app(environ, start_response):
        raw = environ.get("RAW_URI") or environ.get("REQUEST_URI") or environ.get("PATH_INFO", "")
        raw = raw.split("?", 1)[0]
        if not raw.startswith(PREFIX):
            return _json(start_response, "404 Not Found", {"error": "not found"})
        user_path = raw[len(PREFIX):]
        method = environ["REQUEST_METHOD"]
        try:
            if method == "PUT":
                size = int(environ.get("CONTENT_LENGTH") or 0)
                rel = save(root, user_path, environ["wsgi.input"].read(size))
                return _json(start_response, "201 Created", {"stored": rel})
            if method == "GET":
                data = load(root, user_path)
                if data is None:
                    return _json(start_response, "404 Not Found", {"error": "not found"})
                start_response("200 OK", [("Content-Type", "application/octet-stream"),
                                          ("Content-Length", str(len(data)))])
                return [data]
            return _json(start_response, "405 Method Not Allowed", {"error": "method not allowed"})
        except UnsafePath:
            return _json(start_response, "400 Bad Request", {"error": "unsafe path"})

    return app
