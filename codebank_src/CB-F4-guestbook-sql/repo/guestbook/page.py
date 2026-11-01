"""HTML for the guestbook page."""


def render(entries, q=None, page=1):
    head = "<h1>Guestbook</h1>"
    if q:
        head += f"<p>Results for <b>{q}</b></p>"
    items = "".join(f"<li><b>{e['name']}</b>: {e['text']}</li>" for e in entries)
    return f"<html><body>{head}<ul>{items}</ul><p>page {page}</p></body></html>"
