"""GUI access token: show it, rotate it, and recover it when you are locked out.

CrucibleForge has no user accounts and no password database. The only login is
the GUI's access token (required off-loopback; optional on loopback). Provider
API keys are not stored here at all (``api_key_env`` names an environment
variable). So "accounts" is this one secret, kept in the **data directory** --
the folder that holds ``models.yaml`` (and ``results/`` next to it):

    <data dir>/gui_token     the stored token, one line, mode 0600
    <data dir>/RESET_LOGIN   recovery sentinel (see consume_reset_sentinel)

Token precedence at ``crucibleforge gui`` start: ``--token`` > the
``CRUCIBLEFORGE_GUI_TOKEN`` environment variable > ``gui_token`` > (off-loopback
only) a fresh random token printed on every start.

Recovery needs filesystem access to the data dir and a restart. Nothing here is
reachable over HTTP: the sentinel is looked at once, in ``server.serve`` before
the socket is bound, and no request handler imports or calls this module.
"""
from __future__ import annotations

import os
import re
import secrets
from pathlib import Path

ENV_TOKEN = "CRUCIBLEFORGE_GUI_TOKEN"
TOKEN_FILE = "gui_token"
SENTINEL = "RESET_LOGIN"

# The token travels in ?token=, a cookie and a header, so keep it URL-safe.
_TOKEN_RE = re.compile(r"[A-Za-z0-9._~-]{12,256}")
_SENTINEL_MAX_BYTES = 1024


class AccountError(RuntimeError):
    pass


def new_token() -> str:
    return secrets.token_urlsafe(24)


def valid_token(tok: str) -> bool:
    return bool(_TOKEN_RE.fullmatch(tok))


def data_dir_for(cfg_path: str | os.PathLike | None) -> Path | None:
    """The data directory: the folder holding models.yaml (None without one)."""
    return Path(cfg_path).resolve().parent if cfg_path else None


def token_path(data_dir: Path) -> Path:
    return data_dir / TOKEN_FILE


def sentinel_path(data_dir: Path) -> Path:
    return data_dir / SENTINEL


def read_stored_token(data_dir: Path | None) -> str | None:
    """The token in ``<data>/gui_token``, or None when absent/unreadable/invalid."""
    if data_dir is None:
        return None
    try:
        tok = token_path(data_dir).read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return tok if valid_token(tok) else None


def write_token(data_dir: Path, tok: str) -> Path:
    """Atomically write ``tok`` to ``<data>/gui_token`` with mode 0600."""
    if not valid_token(tok):
        raise AccountError("a token must be 12-256 characters of A-Z a-z 0-9 . _ ~ -")
    data_dir.mkdir(parents=True, exist_ok=True)
    dest = token_path(data_dir)
    tmp = dest.with_name(f"{TOKEN_FILE}.tmp.{os.getpid()}")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(tok + "\n")
        os.replace(tmp, dest)
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass
    return dest


def effective_token(data_dir: Path | None, environ=None) -> tuple[str | None, str]:
    """(token, source) the GUI would use without ``--token``.
    source is "env", "file" or "none"."""
    env = (environ if environ is not None else os.environ).get(ENV_TOKEN, "").strip()
    if env:
        return env, "env"
    stored = read_stored_token(data_dir)
    if stored:
        return stored, "file"
    return None, "none"


def rotate_token(data_dir: Path, new: str | None = None) -> str:
    """Replace the stored token (random unless ``new`` is given)."""
    tok = new if new is not None else new_token()
    write_token(data_dir, tok)
    return tok


def _decode_sentinel(raw: bytes) -> str:
    # PowerShell 5 redirection writes UTF-16 with a BOM; Notepad may add a
    # UTF-8 BOM. Accept both rather than lock out the one person who is
    # already locked out.
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16")
    return raw.decode("utf-8-sig")


def consume_reset_sentinel(data_dir: Path | None, environ=None) -> str | None:
    """If ``<data>/RESET_LOGIN`` exists, act on it ONCE and delete it.

    Empty file -> a random new token; otherwise the file's text (stripped) is
    the new token. Returns ONE log line (never containing the token) or None
    when there is no sentinel. The file is removed whether or not its content
    was usable, so it can never replay on a later start. A symlink or
    non-regular file is removed unread (it cannot be a deliberate sentinel and
    must not be followed), and a directory is left alone with a warning.
    """
    if data_dir is None:
        return None
    sentinel = sentinel_path(data_dir)
    if not (sentinel.is_symlink() or sentinel.exists()):
        return None
    if sentinel.is_dir() and not sentinel.is_symlink():
        return f"{sentinel} is a directory; ignored (create an empty FILE named {SENTINEL})"
    if sentinel.is_symlink():
        content, problem = None, "is a symlink; not followed"
    else:
        try:
            raw = sentinel.read_bytes()[:_SENTINEL_MAX_BYTES + 1]
        except OSError as e:
            return f"{sentinel} could not be read ({e.__class__.__name__}); left in place"
        if len(raw) > _SENTINEL_MAX_BYTES:
            content, problem = None, "is larger than 1 KiB"
        else:
            try:
                content, problem = _decode_sentinel(raw).strip(), None
            except UnicodeError:
                content, problem = None, "is not valid text"
    try:
        sentinel.unlink()
    except OSError as e:
        # Cannot delete it -> do NOT act on it, or it would replay every start.
        return (f"{sentinel} could not be deleted ({e.__class__.__name__}); "
                "login NOT reset (fix the permissions on the data dir)")
    if problem:
        return f"{sentinel} {problem}; deleted, login NOT reset"
    if content and not valid_token(content):
        return (f"{sentinel} held an unusable token (need 12-256 characters of "
                "A-Z a-z 0-9 . _ ~ -); deleted, login NOT reset")
    try:
        write_token(data_dir, content or new_token())
    except OSError as e:
        return f"{sentinel} deleted but the token could not be written ({e.__class__.__name__})"
    note = ""
    if (environ if environ is not None else os.environ).get(ENV_TOKEN, "").strip():
        note = f"; {ENV_TOKEN} is set and still overrides it"
    return (f"login reset from {sentinel.name}: new GUI token written to "
            f"{token_path(data_dir)} (run `crucibleforge gui token show`){note}")
