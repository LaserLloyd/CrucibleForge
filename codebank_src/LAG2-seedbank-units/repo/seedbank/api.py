"""WSGI JSON API for the kitchen tablet.

    GET    /seeds[?family=F]        list (sorted by name)
    POST   /seeds                   create   {"name": ..., other fields}
    GET    /seeds/<id>              one seed
    PATCH  /seeds/<id>              change some fields
    DELETE /seeds/<id>              remove (and its sowings)
    GET    /seeds/<id>/sowings      sowings of a seed
    POST   /seeds/<id>/sowings      {"sown_on": "YYYY-MM-DD", "bed": ..., "count": ...}
    GET    /search?q=...            name search
    GET    /viability?year=YYYY     every seed with its viability status that season
"""
import json
from datetime import date
from urllib.parse import parse_qs

from . import search as _search
from . import store, util, viability

STATUS = {200: "200 OK", 201: "201 Created", 204: "204 No Content", 400: "400 Bad Request",
          404: "404 Not Found", 405: "405 Method Not Allowed"}


class HTTPError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


def _seed_or_404(conn, sid):
    s = store.get_seed(conn, sid)
    if s is None:
        raise HTTPError(404, "no such seed")
    return s


def _id(part):
    try:
        return int(part)
    except ValueError:
        raise HTTPError(404, "no such seed") from None


def _seed_fields(body, partial):
    fields = dict(body)
    fields.pop("id", None)
    fields.pop("created_at", None)
    if not partial and "name" not in fields:
        raise HTTPError(400, "name is required")
    return fields


def route(conn, method, path, query, environ):
    parts = [p for p in path.split("/") if p]
    if parts == ["seeds"]:
        if method == "GET":
            fam = query.get("family", [None])[0]
            return 200, [s.to_json() for s in store.list_seeds(conn, family=fam)]
        if method == "POST":
            fields = _seed_fields(util.read_json(environ), partial=False)
            name = fields.pop("name")
            return 201, store.add_seed(conn, name, **fields).to_json()
        raise HTTPError(405, "method not allowed")
    if len(parts) == 2 and parts[0] == "seeds":
        sid = _id(parts[1])
        if method == "GET":
            return 200, _seed_or_404(conn, sid).to_json()
        if method == "PATCH":
            _seed_or_404(conn, sid)
            fields = _seed_fields(util.read_json(environ), partial=True)
            return 200, store.update_seed(conn, sid, **fields).to_json()
        if method == "DELETE":
            if not store.delete_seed(conn, sid):
                raise HTTPError(404, "no such seed")
            return 204, None
        raise HTTPError(405, "method not allowed")
    if len(parts) == 3 and parts[0] == "seeds" and parts[2] == "sowings":
        sid = _id(parts[1])
        _seed_or_404(conn, sid)
        if method == "GET":
            return 200, [w.to_json() for w in store.sowings_for(conn, sid)]
        if method == "POST":
            b = util.read_json(environ)
            return 201, store.add_sowing(conn, sid, str(b.get("sown_on", "")), b.get("bed", ""),
                                         b.get("count")).to_json()
        raise HTTPError(405, "method not allowed")
    if parts == ["search"] and method == "GET":
        q = query.get("q", [""])[0]
        return 200, [s.to_json() for s in _search.search(store.list_seeds(conn), q)]
    if parts == ["viability"] and method == "GET":
        try:
            year = int(query.get("year", [str(date.today().year)])[0])
        except ValueError:
            raise HTTPError(400, "year must be a number") from None
        return 200, [{"id": s.id, "name": s.name, "status": viability.status(s.family, s.harvested, year)}
                     for s in store.list_seeds(conn)]
    raise HTTPError(404, "not found")


def make_app(conn):
    def app(environ, start_response):
        method = environ.get("REQUEST_METHOD", "GET")
        query = parse_qs(environ.get("QUERY_STRING", ""))
        try:
            status, body = route(conn, method, environ.get("PATH_INFO", "/"), query, environ)
        except HTTPError as e:
            status, body = e.status, {"error": str(e)}
        except ValueError as e:
            status, body = 400, {"error": str(e)}
        if body is None:
            start_response(STATUS[status], [])
            return [b""]
        data = json.dumps(body).encode("utf-8")
        start_response(STATUS[status], [("Content-Type", "application/json"),
                                        ("Content-Length", str(len(data)))])
        return [data]
    return app
