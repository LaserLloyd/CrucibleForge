"""Persistent child process for the spawn-based regex-performance test.

Kept tiny and import-light on purpose: under the ``spawn`` start method (the only
one Windows has) the child re-imports this module, and a heavy import chain would
dominate the per-pattern deadline.
"""
import re


def serve(conn, adversarial):
    """Say "ready", then receive patterns until None; search each against every
    adversarial input and reply "done". A pattern is a string (compiled with
    re.I | re.M, as session checks are) or a ``(pattern, flags)`` pair."""
    conn.send("ready")
    while True:
        item = conn.recv()
        if item is None:
            return
        pattern, flags = item if isinstance(item, tuple) else (item, re.I | re.M)
        compiled = re.compile(pattern, flags)
        for text in adversarial:
            for _ in compiled.finditer(text):
                pass
        conn.send("done")
