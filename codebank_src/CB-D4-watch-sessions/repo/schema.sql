CREATE TABLE users (
    id                 INTEGER PRIMARY KEY,
    name               TEXT NOT NULL,
    utc_offset_minutes INTEGER NOT NULL      -- the user's fixed offset, e.g. 540 for UTC+9
);
CREATE TABLE plays (
    id         INTEGER PRIMARY KEY,
    user_id    INTEGER NOT NULL REFERENCES users(id),
    started_at TEXT NOT NULL,                -- 'YYYY-MM-DD HH:MM:SS', UTC
    seconds    INTEGER NOT NULL              -- >= 1
);
