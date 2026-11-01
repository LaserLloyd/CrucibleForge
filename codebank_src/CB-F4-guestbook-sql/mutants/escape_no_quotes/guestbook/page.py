"""HTML for the guestbook page."""
from html import escape


def render(entries, q=None, page=1):
    head = "<h1>Guestbook</h1>"
    if q:
        head += f"<p>Results for <b>{escape(q, quote=False)}</b></p>"
    items = "".join(f"<li><b>{escape(e['name'], quote=False)}</b>: {escape(e['text'], quote=False)}</li>"
                    for e in entries)
    return f"<html><body>{head}<ul>{items}</ul><p>page {page}</p></body></html>"
