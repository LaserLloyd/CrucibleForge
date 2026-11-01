# seedbank

The family's garden seed inventory: what seed we have, how much of it, where
it came from, when it was harvested, and what got sown where. Python stdlib
only; state in one SQLite file (`~/.local/share/seedbank/seeds.db`).

Layout:

* `seedbank/db.py`: connection, schema, migrations (`PRAGMA user_version`).
* `seedbank/models.py`: the `Seed` and `Sowing` records and their JSON shape.
* `seedbank/store.py`: create/read/update/delete for seeds and sowings.
* `seedbank/search.py`: forgiving name search for the dashboard box.
* `seedbank/viability.py`: crop families and how many years seed stays good.
* `seedbank/sowing.py`: sowing windows from the last frost date.
* `seedbank/export.py`: CSV export (Gran imports it into her spreadsheet).
* `seedbank/api.py`: WSGI JSON API for the kitchen tablet.
* `seedbank/cli.py`: the `seedbank` command.
* `seedbank/util.py`: small helpers (clock, slugs, JSON bodies).

Conventions:

* Timestamps are stored as UTC ISO-8601 strings (`util.now_iso()`).
* `harvested` is a year (integer) or NULL when unknown.
* Text columns are never NULL in the database; "nothing" is `''`.
* The API answers JSON; errors are `{"error": "<message>"}` with a 4xx status.
* `db.connect(path)` opens the file with foreign keys ON, creates the current
  schema in an empty file and migrates an older one.

Schema history:

* v1 (2024): seeds without a crop family.
* v2 (2025): `family` column, filled from `viability.family_of(name)`.
* v3: structured amounts, see `docs/AMOUNTS.md`.

Run the tests with `python -m pytest tests/`.
