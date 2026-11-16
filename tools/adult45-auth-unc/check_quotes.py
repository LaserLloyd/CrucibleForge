import json, re, sys, html, urllib.request, urllib.parse
BASE = "http://127.0.0.1:8080/content/wikipedia_en_all_nopic/"
def norm(s):
    s = html.unescape(s)
    for a, b in (("‘", "'"), ("’", "'"), ("“", '"'), ("”", '"'), ("–", "-"), ("—", "-"), ("−", "-"), (" ", " ")):
        s = s.replace(a, b)
    return re.sub(r"\s+", " ", s).strip()
def text_of(title):
    t = urllib.parse.quote(title.replace(" ", "_"), safe="_()%,'!:")
    raw = urllib.request.urlopen(BASE + t, timeout=30).read().decode("utf8", "replace")
    raw = re.sub(r"(?s)<(script|style)[^>]*>.*?</\1>", " ", raw)
    raw = re.sub(r"<[^>]+>", " ", raw)
    return norm(raw)
def nospace(s): return re.sub(r"\s+", "", s)
if __name__ == "__main__":
    items = json.load(open(sys.argv[1] if len(sys.argv) > 1 else "k-add.json"))["items"]
    cache, miss = {}, 0
    for it in items:
        s = it["source"]
        refs = [(s["title"], s["quote"])]
        if "also" in s: refs.append((s["also"]["title"], s["also"]["quote"]))
        for title, q in refs:
            if title not in cache:
                try: cache[title] = nospace(text_of(title))
                except Exception as e: cache[title] = None; print("FETCH FAIL", it["id"], title, e)
            if cache[title] is None or nospace(norm(q)) not in cache[title]:
                miss += 1; print("MISS", it["id"], title, "|", q[:80])
            if len(q) > 300: miss += 1; print("LONG", it["id"], len(q))
    print(f"checked {len(items)} items, {miss} misses")
