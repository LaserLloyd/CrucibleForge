# photosort

Imports photos from the family's phones into the shared library
(`library/YYYY/MM/<name>`) and keeps a SQLite catalog of what was imported.

Each photo `X.jpg` in an import folder comes with a sidecar `X.jpg.json`:
`{"taken": "2026:03:31 23:30:00", "offset": "+09:00"}` (EXIF
DateTimeOriginal and OffsetTimeOriginal, as the phone wrote them; `offset`
may be missing). Files without a sidecar, and files that are not `.jpg`,
`.jpeg` or `.heic` (any letter case), are ignored.

Rules:

* The library folder is the year and month of `taken` AS WRITTEN (the
  phone's local wall time). The catalog stores the capture instant in UTC
  (`taken_utc`, ISO-8601 with a trailing Z): `taken` minus `offset`; with no
  offset, `taken` is taken to be UTC already.
* A photo is a duplicate when a photo with the same CONTENT (SHA-256 of the
  file bytes, `photosort.hashing.sha256_file`) is already in the catalog
  (from an earlier import or earlier in the same import). Duplicates are not
  copied and are listed in the result's `skipped`.
* Different photos may share a name: if `library/YYYY/MM/<name>` exists,
  use `<stem>-1<ext>`, `<stem>-2<ext>`, ... (first free).
* `import_dir(src, library, conn)` returns
  `{"imported": [relative dest paths], "skipped": [source names], "ignored": [source names]}`,
  each list in the order the source files are processed (sorted by name).
* The catalog schema version lives in `PRAGMA user_version`;
  `catalog.connect(path)` migrates older catalogs. Catalogs from release 1
  (version 1) must keep working: their photos count for duplicate checks.
