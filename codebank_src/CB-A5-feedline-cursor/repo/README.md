# feedline

The household activity feed ("Ana uploaded 3 photos") behind the dashboard's
infinite scroll. Items live in SQLite (`items(id INTEGER PRIMARY KEY,
created_at TEXT, kind TEXT, text TEXT)`, `created_at` = ISO-8601 UTC with a
trailing Z, so text order is time order).

Paging contract (`feedline.feed.page(conn, limit, cursor=None)`):

* Items come newest first; items with the same `created_at` come in
  descending `id` order.
* `limit` is 1..100, anything else raises ValueError.
* It returns `(items, next_cursor)`. Following `next_cursor` page after page
  yields every item exactly once, in the order above, even while new items
  are being added at the top.
* `next_cursor` is None exactly when there is nothing after the page.
* Cursors are opaque strings for the client. A cursor that cannot be decoded
  raises ValueError("bad cursor").
* Release 1 issued cursors that are the base64url encoding of a bare
  `created_at`. Phones keep those for weeks, so they must keep working and
  mean what they meant then: continue with the items strictly older than
  that timestamp.
