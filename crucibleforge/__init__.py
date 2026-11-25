"""CrucibleForge: an LLM benchmark with verifiable grading, a points board and ladder packs."""

import sys as _sys

if _sys.platform == "win32":
    # `resource` exists only on POSIX. The grading modules that use it (CPU and memory limits around graded code)
    # import it at the top and are byte-frozen by the row stamps, so they cannot be edited to import lazily. An empty
    # stand-in lets the package import on Windows; the code paths that call into it are the sandboxed graders, which
    # Windows runs in Docker or with --allow-unsandboxed (README, Install). There is no `fcntl` stand-in: every file
    # lock goes through filelock.py (msvcrt on Windows), and an empty fcntl would only make a lock look taken.
    import types as _types

    for _name in ("resource",):
        _sys.modules.setdefault(_name, _types.ModuleType(_name))

from .version import SUITE_VERSION

__version__ = SUITE_VERSION
