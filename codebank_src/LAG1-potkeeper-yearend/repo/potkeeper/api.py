"""Read-only WSGI JSON API for the kitchen tablet (README "API")."""
import json
import re
from datetime import date

from .dates import month_key, parse_month
from .goals import monthly_needed
from .reports import month_report, year_end

_MONTH = re.compile(r"^/month/(\d{4}-\d{2})$")
_YEAR = re.compile(r"^/year-end/(\d{4})$")


def make_app(ledger, household, goals=(), today=date.today):
    def respond(start, status, body):
        data = json.dumps(body).encode("utf-8")
        start(status, [("Content-Type", "application/json"), ("Content-Length", str(len(data)))])
        return [data]

    def app(environ, start):
        if environ.get("REQUEST_METHOD", "GET") != "GET":
            return respond(start, "405 Method Not Allowed", {"error": "read-only API"})
        path = environ.get("PATH_INFO", "")
        m = _MONTH.match(path)
        if m:
            try:
                parse_month(m.group(1))
            except ValueError as e:
                return respond(start, "400 Bad Request", {"error": str(e)})
            return respond(start, "200 OK", month_report(ledger, m.group(1)))
        m = _YEAR.match(path)
        if m:
            return respond(start, "200 OK", year_end(ledger, household, int(m.group(1))))
        if path == "/goals":
            month = month_key(today())
            return respond(start, "200 OK", [{"name": g.name, "target": g.target, "saved": g.saved,
                                               "due": g.due, "monthly_needed": monthly_needed(g, month)}
                                              for g in goals])
        return respond(start, "404 Not Found", {"error": "no such path"})
    return app
