"""Forgiving name search for the dashboard box.

A query matches a seed when every query word is a prefix of some word of
"name variety" (case- and accent-insensitive). Results: best first, where
a match on the name beats a match only on the variety, then by name.
"""
from . import util


def _words(text):
    return util.fold(text).replace("-", " ").split()


def matches(query, seed):
    q = _words(query)
    if not q:
        return False
    words = _words(seed.name) + _words(seed.variety)
    return all(any(w.startswith(p) for w in words) for p in q)


def rank(query, seed):
    q = _words(query)
    name = _words(seed.name)
    on_name = sum(any(w.startswith(p) for w in name) for p in q)
    return (-on_name, util.fold(seed.name), util.fold(seed.variety), seed.id)


def search(seeds, query, limit=20):
    hits = [s for s in seeds if matches(query, s)]
    hits.sort(key=lambda s: rank(query, s))
    return hits[:limit]
