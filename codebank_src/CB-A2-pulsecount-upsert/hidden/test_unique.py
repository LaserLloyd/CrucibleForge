import sqlite3

import pytest

from pulsecount.db import connect


def test_database_rejects_duplicates(tmp_path):
    conn = connect(str(tmp_path / "c.db"))
    conn.execute("INSERT INTO counters (day, name, count, first_seen, last_seen) VALUES ('d','n',1,'t','t')")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO counters (day, name, count, first_seen, last_seen) VALUES ('d','n',1,'t','t')")
    # same name on another day is fine
    conn.execute("INSERT INTO counters (day, name, count, first_seen, last_seen) VALUES ('e','n',1,'t','t')")
