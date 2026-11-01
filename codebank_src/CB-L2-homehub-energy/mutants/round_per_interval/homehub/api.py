"""WSGI JSON API for the dashboard.

    GET /devices                      -> [device snapshot]
    GET /energy/daily?plug=ID&days=N  -> {"plug": ID, "days": [{"date", "wh", "cents"}]}
                                         the N local days ending today (house zone), oldest
                                         first, days without data as wh 0 / cents 0
    GET /history/last?source=ID       -> the last stored event of a device, or 404
"""
import json
from datetime import datetime, time, timedelta, timezone
from urllib.parse import parse_qs
from zoneinfo import ZoneInfo

from .energy import Tariff, daily_costs


def _json(start_response, status, obj):
    body = json.dumps(obj).encode()
    start_response(status, [("Content-Type", "application/json"), ("Content-Length", str(len(body)))])
    return [body]


def make_app(hub, now=None):
    """`hub` has .devices {id: Device}, .history (storage.History), .config
    (config.Config). `now()` returns the current aware datetime (injected)."""
    now = now or (lambda: datetime.now(timezone.utc))

    def app(environ, start_response):
        path = environ.get("PATH_INFO", "")
        qs = {k: v[0] for k, v in parse_qs(environ.get("QUERY_STRING", "")).items()}
        if path == "/devices":
            return _json(start_response, "200 OK", [d.snapshot() for d in hub.devices.values()])
        if path == "/energy/daily":
            plug = qs.get("plug")
            if plug not in hub.devices:
                return _json(start_response, "404 Not Found", {"error": "unknown plug"})
            try:
                days = int(qs.get("days", "7"))
            except ValueError:
                days = 0
            if not 1 <= days <= 92:
                return _json(start_response, "400 Bad Request", {"error": "days must be 1..92"})
            zone = ZoneInfo(hub.config.zone)
            today = now().astimezone(zone).date()
            first = today - timedelta(days=days - 1)
            since = datetime.combine(first, time(0), tzinfo=zone) - timedelta(days=1)
            until = datetime.combine(today + timedelta(days=1), time(0), tzinfo=zone)
            readings = hub.history.readings(plug, since, until)
            per_day = daily_costs(readings, Tariff.from_config(hub.config.tariff), hub.config.zone)
            out = []
            for i in range(days):
                d = (first + timedelta(days=i)).isoformat()
                v = per_day.get(d, {"wh": 0, "cents": 0})
                out.append({"date": d, "wh": v["wh"], "cents": v["cents"]})
            return _json(start_response, "200 OK", {"plug": plug, "days": out})
        if path == "/history/last":
            last = hub.history.last(qs.get("source", ""))
            if last is None:
                return _json(start_response, "404 Not Found", {"error": "no events"})
            return _json(start_response, "200 OK", last)
        return _json(start_response, "404 Not Found", {"error": "not found"})

    return app
