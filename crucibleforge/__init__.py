"""CrucibleForge: an LLM benchmark with verifiable grading, a points board and ladder packs."""

import sys as _sys

if _sys.platform == "win32":
    # `resource` and `fcntl` exist only on POSIX. The modules that use them (CPU and memory limits around graded
    # code, a file lock) import them at the top and are byte-frozen by the row stamps, so they cannot be edited to
    # import lazily. Empty stand-ins let the package import on Windows; the code paths that call into them are the
    # sandboxed graders, which Windows runs in Docker or with --allow-unsandboxed (README, Install).
    import types as _types

    for _name in ("resource", "fcntl"):
        _sys.modules.setdefault(_name, _types.ModuleType(_name))

from .version import SUITE_VERSION

__version__ = SUITE_VERSION
