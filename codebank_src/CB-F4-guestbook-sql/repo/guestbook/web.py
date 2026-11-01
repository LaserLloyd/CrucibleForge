"""WSGI front end."""
from urllib.parse import parse_qs

from . import entries, page
from .db import connect

PER_PAGE = 20


def make_app(conn=None):
    conn = conn or connect()

    def app(environ, start_response):
        method = environ.get("REQUEST_METHOD", "GET")
        if environ.get("PATH_INFO", "/") != "/":
            start_response("404 Not Found", [("Content-Type", "text/plain")])
            return [b"not found"]
        if method == "POST":
            size = int(environ.get("CONTENT_LENGTH") or 0)
            form = parse_qs(environ["wsgi.input"].read(size).decode("utf-8"))
            try:
                entries.add_entry(conn, form.get("name", [""])[0], form.get("text", [""])[0])
            except ValueError as e:
                start_response("400 Bad Request", [("Content-Type", "text/plain; charset=utf-8")])
                return [str(e).encode()]
            start_response("303 See Other", [("Location", "/")])
            return [b""]
        qs = parse_qs(environ.get("QUERY_STRING", ""))
        q = qs.get("q", [""])[0]
        try:
            n = max(1, int(qs.get("page", ["1"])[0]))
        except ValueError:
            n = 1
        rows = entries.search(conn, q) if q else entries.list_entries(conn, PER_PAGE, (n - 1) * PER_PAGE)
        body = page.render(rows, q or None, n).encode()
        start_response("200 OK", [("Content-Type", "text/html; charset=utf-8")])
        return [body]

    return app
