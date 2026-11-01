# guestbook

The holiday-flat guestbook: guests leave a name and a message; the web page
lists them newest first and has a search box. SQLite + plain WSGI.

Contract:

* `entries.add_entry(conn, name, text)` stores any text exactly as given
  (quotes, backslashes, `%`, emoji, newlines...) and returns the new id.
  Name: 1-80 characters after stripping; text: 1-2000. Otherwise ValueError.
* `entries.list_entries(conn, limit=20, offset=0)`: newest first (by id).
* `entries.search(conn, q, limit=20)`: entries whose name or text contains
  `q` literally, ignoring ASCII letter case; `%`, `_` and `\` in `q` have no
  special meaning. Newest first. An empty `q` returns [].
* `web.app` serves `GET /` (the list, `?page=N`, 20 per page, N from 1),
  `GET /?q=...` (search results) and `POST /` (form fields `name`, `text`;
  answers 303 to `/` on success, 400 with a plain-text reason otherwise).
  Pages escape all user-supplied text for HTML (`& < > " '`).
