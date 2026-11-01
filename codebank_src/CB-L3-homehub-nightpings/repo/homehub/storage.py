"""SQLite history of events."""
import json
import sqlite3
from datetime import datetime, timezone

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id      INTEGER PRIMARY KEY,
    source  TEXT NOT NULL,
    kind    TEXT NOT NULL,
    at      TEXT NOT NULL,          -- ISO-8601 UTC, 'YYYY-MM-DDTHH:MM:SS+00:00'
    data    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS events_source_at ON events(source, at);
"""


def _iso(at: datetime) -> str:
    return at.astimezone(timezone.utc).isoformat(timespec="seconds")


class History:
    def __init__(self, path: str = ":memory:"):
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.executescript(SCHEMA)

    def record(self, ev) -> None:
        with self.conn:
            self.conn.execute("INSERT INTO events (source, kind, at, data) VALUES (?, ?, ?, ?)",
                              (ev.source, ev.kind, _iso(ev.at), json.dumps(ev.data, sort_keys=True)))

    def readings(self, source: str, since: datetime, until: datetime) -> list[tuple[datetime, int]]:
        """[(at, wh)] of a plug's meter readings with since <= at < until, in time order."""
        rows = self.conn.execute(
            "SELECT at, data FROM events WHERE source = ? AND kind = 'reading' AND at >= ? AND at < ? ORDER BY at, id",
            (source, _iso(since), _iso(until))).fetchall()
        out = []
        for at, data in rows:
            d = json.loads(data)
            if "wh" in d:
                out.append((datetime.fromisoformat(at), int(d["wh"])))
        return out

    def last(self, source: str, kind: str | None = None):
        q = "SELECT kind, at, data FROM events WHERE source = ?" + (" AND kind = ?" if kind else "") + \
            " ORDER BY at DESC, id DESC LIMIT 1"
        row = self.conn.execute(q, (source, kind) if kind else (source,)).fetchone()
        if not row:
            return None
        return {"kind": row[0], "at": row[1], "data": json.loads(row[2])}
