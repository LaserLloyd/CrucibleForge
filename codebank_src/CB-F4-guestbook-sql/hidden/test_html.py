from _web import call
from guestbook.db import connect
from guestbook.web import make_app


def test_entries_and_query_are_escaped():
    app = make_app(connect())
    s, _ = call(app, "POST", form={"name": "<script>alert(1)</script>", "text": 'say "hi" & <b>bye</b> \'x\''})
    assert s.startswith("303")
    s, page = call(app)
    assert "<script>" not in page and "&lt;script&gt;" in page
    assert "&quot;hi&quot; &amp; &lt;b&gt;bye&lt;/b&gt;" in page
    assert "&#x27;x&#x27;" in page or "&#39;x&#39;" in page
    s, page = call(app, query="q=%3Cimg+src%3Dx+onerror%3Dalert(1)%3E")
    assert "<img" not in page
