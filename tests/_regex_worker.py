"""Persistent child process for the spawn-based regex-performance test.

Kept tiny and import-light on purpose: under the ``spawn`` start method (the only
one Windows has) the child re-imports this module, and a heavy import chain would
dominate the per-pattern deadline.
"""
import re


def serve(conn, adversarial):
    """Say "ready", then receive patterns until None; search each against every
    adversarial input and reply "done"."""
    conn.send("ready")
    while True:
        pattern = conn.recv()
        if pattern is None:
            return
        compiled = re.compile(pattern, re.I | re.M)
        for text in adversarial:
            for _ in compiled.finditer(text):
                pass
        conn.send("done")
