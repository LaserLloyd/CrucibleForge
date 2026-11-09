"""The board header uses the inlined MakoBench display face."""
import importlib
import re

import importlib
rh = importlib.import_module("crucibleforge.templates.board.render_html")


def test_font_face_inlined_and_header_uses_it():
    assert "@font-face" in rh.FONT_CSS
    assert 'font-family:"Unbounded MakoBench"' in rh.FONT_CSS
    assert "url(data:font/woff2;base64," in rh.FONT_CSS
    assert "http" not in rh.FONT_CSS
    assert re.search(r'h1\{font-family:"Unbounded MakoBench",', rh.CSS)
    assert rh.CSS.count("Unbounded MakoBench") == 1


def test_rendered_page_carries_font():
    page = rh.render_html([{"model": "m", "provider": "p"}], "sub")
    assert page.count("@font-face") == 1
    assert "<header><h1>" in page and 'h1{font-family:"Unbounded MakoBench"' in page
