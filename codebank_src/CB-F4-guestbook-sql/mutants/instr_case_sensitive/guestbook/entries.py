"""Storing and finding guestbook entries."""


def _check(name, text):
    name, text = name.strip(), text.strip()
    if not 1 <= len(name) <= 80:
        raise ValueError("name must be 1-80 characters")
    if not 1 <= len(text) <= 2000:
        raise ValueError("text must be 1-2000 characters")
    return name, text


def add_entry(conn, name, text):
    name, text = _check(name, text)
    with conn:
        cur = conn.execute("INSERT INTO entries (name, text) VALUES (?, ?)", (name, text))
    return cur.lastrowid


def list_entries(conn, limit=20, offset=0):
    rows = conn.execute("SELECT id, name, text FROM entries ORDER BY id DESC LIMIT ? OFFSET ?", (limit, offset))
    return [dict(r) for r in rows]


def search(conn, q, limit=20):
    if not q:
        return []
    rows = conn.execute(
        "SELECT id, name, text FROM entries WHERE instr(name, ?) OR instr(text, ?) "
        "ORDER BY id DESC LIMIT ?", (q, q, int(limit)))
    return [dict(r) for r in rows]
