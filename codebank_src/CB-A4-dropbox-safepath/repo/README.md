# dropbox

A one-way household file drop: devices PUT files, the owner reads them on
disk. Served by any WSGI server (`dropbox.app.make_app(root)`).

Routes:

* `PUT /files/<path>` stores the request body, creating folders as needed,
  and answers `201 {"stored": "<relative path>"}`.
* `GET /files/<path>` answers `200` with the bytes or `404 {"error": "not found"}`.
* A path that is not allowed answers `400 {"error": "unsafe path"}`, for
  both methods, and touches nothing on disk.

Path rules (`dropbox.paths.safe_path`):

* `<path>` arrives percent-encoded from the URL and is decoded exactly once.
* `/` and `\` both separate folders. `.` segments and empty segments are
  ignored; `..` segments are resolved like a shell would.
* The result must name a file strictly inside the drop root, after following
  any symlinks that already exist on disk. The root itself is not a file.
* A path containing a NUL character is never allowed.
* Everything else is a legal name, including names with dots in them
  (`a..b.txt`), spaces and non-ASCII letters.
