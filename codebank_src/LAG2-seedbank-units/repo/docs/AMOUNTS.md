# Structured amounts (schema v3)

Status: agreed at the kitchen table, not built yet.

Until v2 every seed has a free-text `quantity` ("2 packets", "about 30
seeds", "1.5oz"). Nobody can add those up, so v3 stores a number and a unit.

## 1. The amount

An amount is three values:

* `milli`: a non-negative integer, the quantity times 1000 (1.5 packets =
  1500);
* `unit`: one of `seed`, `packet`, `gram`;
* `approx`: true when the quantity is a guess ("about 30 seeds").

New module `seedbank/quantity.py` provides:

* `UNITS = ("seed", "packet", "gram")`
* `class QuantityError(ValueError)`: the message contains the original text.
* `Amount(milli, unit, approx=False)`: an immutable record with exactly those
  attribute names; two amounts are equal when the three values are.
* `parse_quantity(text) -> Amount | None`
* `format_amount(amount) -> str`

## 2. Parsing `parse_quantity(text)`

1. Lower-case the text, strip it and collapse every run of whitespace to one
   space. An empty result returns `None` (no amount, not an error).
2. Approximate marker, at most one, only at the start: `~` (spaces after it
   are dropped), or one of the words `about `, `around `, `approx `,
   `approx. `, `ca. ` (each followed by a space as shown). It sets
   `approx=True`. A second marker ("about ~3") is an error.
3. A number, in one of these forms:
   * digits: `30`;
   * decimal with a point or a comma: `1.5`, `1,5` (digits on both sides);
   * fraction `3/4` and mixed number `1 1/2` (denominator not 0);
   * words: `a`, `an`, `one` = 1; `two` ... `ten` = 2 ... 10;
     `half`, `half a`, `half an` = 1/2; `dozen`, `a dozen` = 12;
     `half dozen`, `half a dozen` = 6. The longest wording wins
     ("half a dozen seeds" is 6 seeds, "half a packet" is 1/2 packet).
     A number word must be followed by a space or the end of the text.

   A digit form may be glued to its unit (`30g`, `1.5oz`, `2pkts`).
   No number at the start is an error. There are no signs: `-3` is an error.
4. The unit is the rest of the text (one optional space after the number):
   * `seed`, `seeds` -> seed
   * `packet`, `packets`, `pkt`, `pkts`, `pack`, `packs`, `sachet`,
     `sachets` -> packet
   * `g`, `gram`, `grams` -> gram
   * `kg` -> gram, times 1000
   * `oz`, `ounce`, `ounces` -> gram, times 28.349523125
   * nothing -> seed
   * anything else (including extra words such as "2 packets from Gran")
     is an error.
5. The value is computed exactly (no binary floating point): `milli` =
   value x 1000 (x the conversion factor), rounded to the nearest integer,
   halves rounded up. `1/3 packet` -> 333, `0.5 oz` -> 14175, `1 kg` ->
   1000000.
6. Seeds are whole: when the unit is `seed`, the value itself (before x 1000)
   must be a whole number, otherwise it is an error ("half seed", "1.5",
   "2.5 seeds").

Every error raises `QuantityError`.

## 3. Formatting `format_amount(amount)`

* `None` -> `""`.
* The number is `milli / 1000` written as a plain decimal without trailing
  zeros or a trailing point and never in exponent form: 2000 -> `2`,
  1500 -> `1.5`, 333 -> `0.333`, 14175 -> `14.175`, 0 -> `0`.
* Then one space and the unit: `seed`/`seeds`, `packet`/`packets` (singular
  only when `milli == 1000`; `0.5 packets`), and `g` for grams in every case
  (`1 g`, `14.175 g`).
* `approx` puts `about ` in front: `about 30 seeds`.

`parse_quantity(format_amount(a)) == a` for every valid amount.

## 4. Database (v3)

The `seeds` table loses `quantity` and gains, with exactly these
declarations:

```sql
amount_milli INTEGER CHECK (amount_milli IS NULL OR amount_milli >= 0),
unit TEXT CHECK (unit IN ('seed', 'packet', 'gram')),
approx INTEGER NOT NULL DEFAULT 0 CHECK (approx IN (0, 1)),
CHECK ((amount_milli IS NULL) = (unit IS NULL))
```

Everything else in the schema stays as it is (ids, other columns, indexes,
the `sowings` table and its rows). A fresh database (`db.connect` on an
empty file) gets the same `seeds` columns as a migrated one.

### Migration v2 -> v3

For every seed row, with `q` = the old `quantity` stripped:

* `q` is empty: no amount (`amount_milli` and `unit` NULL, `approx` 0).
* `parse_quantity(q)` succeeds: store its three values.
* it raises `QuantityError`: no amount, and the text is kept in the notes
  as `[quantity: <q>]` (q exactly as stripped, original case): the notes
  become that tag when they were `''`, otherwise notes + `"\n"` + tag.

### How migrations run

* `db.MIGRATIONS` is a list of `(target_version, step)` in ascending order,
  `step(conn)` brings the schema from `target_version - 1` to
  `target_version`; `db.SCHEMA_VERSION` is the newest target. `migrate`
  reads both at call time.
* `db.migrate(conn)` takes a `sqlite3.Connection` in its default
  transaction mode with foreign keys ON (what `db.connect` gives) and applies
  every pending step AND the final `user_version` in ONE transaction: if any
  step raises, everything is rolled back (tables, rows and `user_version`
  exactly as before) and the exception propagates. Either way it returns
  with no transaction open and foreign keys ON.
* Already current: nothing changes (running it twice is harmless).
  `user_version` newer than `SCHEMA_VERSION` raises `db.SchemaTooNew` and
  changes nothing.
* A v1 database migrates through v2 to v3 in that one run.
* Deleting a seed still deletes its sowings (the foreign key keeps working
  after the migration), and no sowing is lost by migrating.

## 5. API

Every seed object carries `"amount"`: `null` or
`{"value": "<number as in format_amount>", "unit": "<unit>", "approx": <bool>}`
and, for old clients, `"quantity"`: `format_amount(...)` (`""` when none).

`POST /seeds` and `PATCH /seeds/<id>` accept either

* `"amount"`: an object as above, where `value` is a JSON number or a
  string of digits with an optional decimal point (no fractions or words),
  `unit` is required, `approx` is optional (default false) and must be a
  boolean; or `null` to clear the amount; or
* `"quantity"`: a string, parsed with `parse_quantity` (`""` clears).

Both in one body is a 400 (`"give amount or quantity, not both"`).
Invalid amounts (bad value, unknown unit, negative, non-whole seeds,
unparseable quantity) are a 400 and nothing is saved. Values are rounded to
milli exactly as in the parser (halves up).

`GET /seeds?unit=<unit>` lists only seeds with that unit (an unknown unit is
a 400). `GET /totals` returns, for each unit that at least one seed has,
`{"total": "<sum, formatted as in format_amount>", "approx": <true when any
of those seeds is approximate>}`, e.g.
`{"packet": {"total": "3.5", "approx": false}}`; `{}` when no seed has an
amount.

## 6. Using seed when sowing

`POST /seeds/<id>/sowings` takes an optional `"used"`: an amount object (as
in section 5; its `approx` is ignored) or a quantity string (parsed). When it
is given, the sowing is saved AND the amount is taken off the seed's stock,
both or neither:

* the seed has no amount: 409 `"seed has no amount"`;
* the unit differs from the seed's unit: 409 `"unit mismatch"` (no
  conversions; `"1 oz"` is grams, so it may be used from a gram stock);
* more than the stock: 409 `"not enough seed"`;
* an invalid amount: 400.

On any of these nothing is saved. Using everything leaves an amount of 0
(not "no amount"); the seed's `approx` flag does not change. Without `used`
a sowing behaves as before.

## 7. CSV export, import and CLI

* The CSV header becomes
  `id,name,variety,family,amount,unit,approx,harvested,source,notes`
  (`quantity` replaced in place by three columns). `amount` is the number as
  in `format_amount`, `approx` is `1` or `0`; a seed without an amount has
  three empty cells there.
* `export.import_csv(conn, fh)` reads such a file back (Gran edits her copy
  and sends it back) and returns the number of rows. It accepts the v3
  header above or the old v2 header
  (`id,name,variety,family,quantity,harvested,source,notes`), exactly.
  For each row: an empty `id` adds a seed, an existing id replaces that
  seed's fields with the row's, an unknown id or an id seen earlier in the
  same file is an error. `name` must not be empty; an empty `family` is
  filled from `viability.family_of(name)`; `harvested` is a year or empty.
  v3 rows: `amount`, `unit`, `approx` all empty = no amount; otherwise
  `amount` follows the API value rules, `unit` must be valid and `approx`
  is `0` or `1` (empty means `0`). v2 rows: `quantity` goes through
  `parse_quantity` and an unparseable one is an error (no `[quantity: ...]`
  notes here). Any error raises `export.CSVImportError` (a `ValueError`)
  whose message starts with `line N: `, N being the record number with the
  header as record 1, and leaves the database exactly as it was (all rows
  or none).
* `seedbank import FILE` prints `imported N seeds`; on an error it prints
  `error: line N: ...` to stderr, exits with status 1 and changes nothing.
* `seedbank add NAME` takes either `--qty TEXT` (parsed) or
  `--amount N --unit UNIT [--approx]`. Both forms together, or `--amount`
  without `--unit` (or the other way round), exit with status 2; an invalid
  quantity or amount prints `error: <message>` to stderr, exits with status
  1 and saves nothing.
* `seedbank list` shows `format_amount` in its quantity column (`-` when the
  seed has no amount).
