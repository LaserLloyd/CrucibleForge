"""GUI access token: show it, rotate it, and recover it when you are locked out.

CrucibleForge has no user accounts and no password database. The only login is
the GUI's access token (required off-loopback; optional on loopback). Provider
API keys are not stored here at all (``api_key_env`` names an environment
variable). So "accounts" is this one secret, kept in the **data directory** --
the folder that holds ``models.yaml`` (and ``results/`` next to it):

    <data dir>/gui_token     the stored token, one line, mode 0600 (POSIX; on Windows the
                             file inherits the folder's ACL: keep the data dir private)
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
import stat
import tempfile
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
    # absolute(), not resolve(): config.load_config puts results/ beside the
    # UNRESOLVED path, and a symlinked models.yaml must not move the token
    # into whatever repo the link points at.
    return Path(cfg_path).absolute().parent if cfg_path else None


def token_path(data_dir: Path) -> Path:
    return data_dir / TOKEN_FILE


def sentinel_path(data_dir: Path) -> Path:
    return data_dir / SENTINEL


def read_stored_token(data_dir: Path | None) -> str | None:
    """The token in ``<data>/gui_token``, or None when absent/unreadable/invalid."""
    return _stored(data_dir)[0]


def _stored(data_dir: Path | None) -> tuple[str | None, str | None]:
    """(token, problem): problem is a reason when gui_token EXISTS but is unusable."""
    if data_dir is None:
        return None, None
    f = token_path(data_dir)
    try:
        tok = f.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return None, None
    except (OSError, UnicodeError) as e:
        return None, f"{f} exists but cannot be read ({e.__class__.__name__}); check its owner and mode"
    if valid_token(tok):
        return tok, None
    return None, (f"{f} exists but does not hold a usable token (12-256 characters of "
                  "A-Z a-z 0-9 . _ ~ -); run `crucibleforge gui token rotate`")


def stored_token_problem(data_dir: Path | None) -> str | None:
    """Why an existing gui_token is being ignored, or None (absent or fine)."""
    return _stored(data_dir)[1]


def write_token(data_dir: Path, tok: str) -> Path:
    """Atomically write ``tok`` to ``<data>/gui_token`` with mode 0600."""
    if not valid_token(tok):
        raise AccountError("a token must be 12-256 characters of A-Z a-z 0-9 . _ ~ -")
    data_dir.mkdir(parents=True, exist_ok=True)
    dest = token_path(data_dir)
    # mkstemp: unpredictable name, O_EXCL, mode 0600 -- a planted symlink at a
    # guessable temp name cannot redirect the write.
    fd, tmp_name = tempfile.mkstemp(dir=data_dir, prefix=f".{TOKEN_FILE}.")
    tmp = Path(tmp_name)
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
    try:
        st = os.lstat(sentinel)
    except FileNotFoundError:
        return None
    except OSError as e:
        return f"{sentinel} could not be inspected ({e.__class__.__name__}); left in place"
    if stat.S_ISDIR(st.st_mode):
        return f"{sentinel} is a directory; ignored (create an empty FILE named {SENTINEL})"
    if not stat.S_ISREG(st.st_mode):
        # symlink, FIFO, device: never opened (a FIFO would hang start-up)
        content, problem = None, "is not a regular file; not read"
    else:
        try:
            fd = os.open(sentinel, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
                         | getattr(os, "O_NONBLOCK", 0))
            with os.fdopen(fd, "rb") as fh:
                raw = fh.read(_SENTINEL_MAX_BYTES + 1)
        except FileNotFoundError:
            return None     # another start-up consumed it first
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
    except FileNotFoundError:
        return None
    except OSError as e:
        # Cannot delete it -> do NOT act on it, or it would replay every start.
        return (f"{sentinel} could not be deleted ({e.__class__.__name__}); "
                "login NOT reset (fix the permissions on the data dir; on Windows clear the file's read-only attribute)")
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
