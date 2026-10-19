"""GUI access token management and lockout recovery (3.7.0).

CrucibleForge has one login: the GUI access token. These tests cover
`crucibleforge gui token show|rotate`, the `<data dir>/RESET_LOGIN` sentinel
(read once at start, consumed, never replays) and the guarantee that nothing a
network client can send reaches either.
"""
from __future__ import annotations

import ast
import http.client
import os
import shutil
import sys
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from crucibleforge import accounts, cli
from crucibleforge.gui import server as gui_server

ROOT = Path(__file__).resolve().parent.parent
POSIX = os.name == "posix"


@pytest.fixture
def data(tmp_path, monkeypatch):
    """A scratch data dir holding a valid models.yaml; no token env."""
    monkeypatch.delenv(accounts.ENV_TOKEN, raising=False)
    d = tmp_path / "data"
    d.mkdir()
    shutil.copy(ROOT / "models.example.yaml", d / "models.yaml")
    return d


def run_cli(data_dir, *argv, stdin=None, monkeypatch=None, capsys=None):
    if stdin is not None:
        import io
        monkeypatch.setattr(sys, "stdin", io.StringIO(stdin))
    with pytest.raises(SystemExit) as exc:
        cli.main(["--config", str(data_dir / "models.yaml"), "gui", "token", *argv])
    out = capsys.readouterr()
    return (exc.value.code or 0), out.out.strip(), out.err


# ------------------------------------------------------------ CLI: show / rotate

def test_show_without_a_token_says_how_to_make_one(data, capsys, monkeypatch):
    rc, out, err = run_cli(data, "show", monkeypatch=monkeypatch, capsys=capsys)
    assert rc == 1 and out == ""
    assert "gui token rotate" in err
    assert f"data dir: {data}" in err          # always says what it acts on


def test_rotate_writes_the_token_file_and_show_reads_it(data, capsys, monkeypatch):
    rc, out, err = run_cli(data, "rotate", monkeypatch=monkeypatch, capsys=capsys)
    assert rc == 0
    f = data / "gui_token"
    assert f.read_text(encoding="utf-8").strip() == out
    assert accounts.valid_token(out)
    assert f"data dir: {data}" in err and "restart" in err
    rc, shown, err = run_cli(data, "show", monkeypatch=monkeypatch, capsys=capsys)
    assert rc == 0 and shown == out and str(f) in err
    if POSIX:
        assert (f.stat().st_mode & 0o777) == 0o600


def test_rotate_twice_gives_a_different_token(data, capsys, monkeypatch):
    _, first, _ = run_cli(data, "rotate", monkeypatch=monkeypatch, capsys=capsys)
    _, second, _ = run_cli(data, "rotate", monkeypatch=monkeypatch, capsys=capsys)
    assert first != second
    assert (data / "gui_token").read_text(encoding="utf-8").strip() == second


def test_rotate_token_stdin_sets_a_chosen_token(data, capsys, monkeypatch):
    chosen = "my-own-token-0123456789"
    rc, out, _ = run_cli(data, "rotate", "--token-stdin", stdin=chosen + "\n",
                         monkeypatch=monkeypatch, capsys=capsys)
    assert rc == 0 and out == chosen
    assert accounts.read_stored_token(data) == chosen


@pytest.mark.parametrize("bad", ["short", "has space in it 123456", "semi;colon-0123456789", ""])
def test_rotate_token_stdin_rejects_unusable_tokens(data, capsys, monkeypatch, bad):
    rc, out, err = run_cli(data, "rotate", "--token-stdin", stdin=bad + "\n",
                           monkeypatch=monkeypatch, capsys=capsys)
    assert rc != 0 and out == ""
    assert not (data / "gui_token").exists()


def test_env_token_wins_for_show_and_rotate_warns(data, capsys, monkeypatch):
    monkeypatch.setenv(accounts.ENV_TOKEN, "env-token-0123456789")
    rc, out, err = run_cli(data, "show", monkeypatch=monkeypatch, capsys=capsys)
    assert rc == 0 and out == "env-token-0123456789"
    assert accounts.ENV_TOKEN in err
    rc, new, err = run_cli(data, "rotate", monkeypatch=monkeypatch, capsys=capsys)
    assert rc == 0 and "takes precedence" in err
    assert accounts.read_stored_token(data) == new      # file still written
    assert accounts.effective_token(data)[0] == "env-token-0123456789"


def test_token_verbs_work_when_models_yaml_is_broken(data, capsys, monkeypatch):
    # A lockout is exactly when the registry may also be wrong: the token
    # verbs only need to FIND the config, not validate it.
    (data / "models.yaml").write_text("providers: [not, a, mapping\n", encoding="utf-8")
    rc, out, _ = run_cli(data, "rotate", monkeypatch=monkeypatch, capsys=capsys)
    assert rc == 0 and accounts.read_stored_token(data) == out


def test_no_config_is_a_clear_error(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("CRUCIBLEFORGE_CONFIG", raising=False)
    with pytest.raises(SystemExit) as exc:
        cli.main(["--config", str(tmp_path / "missing.yaml"), "gui", "token", "show"])
    assert exc.value.code == 2
    assert "config error" in capsys.readouterr().err


def test_gui_without_subcommand_still_parses(data, monkeypatch):
    from crucibleforge import graders
    seen = {}
    monkeypatch.setattr(cli, "cmd_gui", lambda args, cfg: seen.update(
        host=args.host, port=args.port, sub=args.gui_cmd) or 0)
    monkeypatch.setattr(graders, "require_sandbox", lambda: None)
    with pytest.raises(SystemExit) as exc:
        cli.main(["--allow-unsandboxed", "--config", str(data / "models.yaml"),
                  "gui", "--port", "9999", "--no-open"])
    assert exc.value.code == 0
    assert seen == {"host": "127.0.0.1", "port": 9999, "sub": None}


# ------------------------------------------------------------ sentinel

def test_empty_sentinel_makes_a_random_token_and_is_consumed(data):
    (data / "RESET_LOGIN").write_bytes(b"")
    msg = accounts.consume_reset_sentinel(data)
    tok = accounts.read_stored_token(data)
    assert tok and accounts.valid_token(tok)
    assert not (data / "RESET_LOGIN").exists()
    assert "login reset" in msg and str(data / "gui_token") in msg
    assert tok not in msg                              # the log line never holds the secret
    assert accounts.consume_reset_sentinel(data) is None   # nothing left to replay


def test_sentinel_with_text_sets_that_token(data):
    (data / "gui_token").write_text("old-token-0123456789\n", encoding="utf-8")
    (data / "RESET_LOGIN").write_text("  brand-new-token-0123\n", encoding="utf-8")
    msg = accounts.consume_reset_sentinel(data)
    assert accounts.read_stored_token(data) == "brand-new-token-0123"
    assert "brand-new-token-0123" not in msg
    assert not (data / "RESET_LOGIN").exists()


@pytest.mark.parametrize("raw", [b"\xef\xbb\xbfbom-token-0123456789\r\n",
                                 "utf16-token-0123456789\r\n".encode("utf-16")])
def test_sentinel_accepts_bom_and_utf16_files(data, raw):
    (data / "RESET_LOGIN").write_bytes(raw)
    accounts.consume_reset_sentinel(data)
    assert accounts.read_stored_token(data) in ("bom-token-0123456789", "utf16-token-0123456789")


@pytest.mark.parametrize("raw", [b"short", b"two words here 0123456789", b"x" * 2000, b"\xff\xfe\x00"])
def test_unusable_sentinel_is_deleted_and_changes_nothing(data, raw):
    (data / "gui_token").write_text("keep-this-token-0123\n", encoding="utf-8")
    (data / "RESET_LOGIN").write_bytes(raw)
    msg = accounts.consume_reset_sentinel(data)
    assert "NOT reset" in msg
    assert not (data / "RESET_LOGIN").exists()          # consumed anyway: no replay
    assert accounts.read_stored_token(data) == "keep-this-token-0123"


def test_sentinel_that_cannot_be_deleted_is_not_acted_on(data, monkeypatch):
    (data / "RESET_LOGIN").write_bytes(b"")
    real_unlink = Path.unlink

    def deny(self, *a, **k):
        if self.name == "RESET_LOGIN":
            raise PermissionError("denied")
        return real_unlink(self, *a, **k)

    monkeypatch.setattr(Path, "unlink", deny)
    msg = accounts.consume_reset_sentinel(data)
    assert "NOT reset" in msg and "could not be deleted" in msg
    assert accounts.read_stored_token(data) is None     # acting would replay every start


def test_sentinel_directory_is_left_alone(data):
    (data / "RESET_LOGIN").mkdir()
    msg = accounts.consume_reset_sentinel(data)
    assert "directory" in msg and (data / "RESET_LOGIN").is_dir()
    assert accounts.read_stored_token(data) is None


def test_sentinel_symlink_is_not_followed(data, tmp_path):
    target = tmp_path / "elsewhere.txt"
    target.write_text("symlink-token-0123456789", encoding="utf-8")
    link = data / "RESET_LOGIN"
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable here")
    msg = accounts.consume_reset_sentinel(data)
    assert "not a regular file" in msg and "NOT reset" in msg
    assert not link.is_symlink() and target.exists()    # link removed, target untouched
    assert accounts.read_stored_token(data) is None


def test_no_sentinel_is_a_no_op(data):
    assert accounts.consume_reset_sentinel(data) is None
    assert accounts.consume_reset_sentinel(None) is None
    assert not (data / "gui_token").exists()


def test_sentinel_notes_when_env_token_still_overrides(data):
    (data / "RESET_LOGIN").write_bytes(b"")
    msg = accounts.consume_reset_sentinel(data, {accounts.ENV_TOKEN: "env-token-0123456789"})
    assert accounts.ENV_TOKEN in msg and "overrides" in msg


# ------------------------------------------------------------ serve() start-up

class _StopServer:
    """Stand-in for ThreadingHTTPServer: records the app, then 'Ctrl-C'."""
    daemon_threads = False

    def __init__(self, addr, handler):
        self.addr = addr

    def serve_forever(self):
        raise KeyboardInterrupt

    def server_close(self):
        pass


def start(data_dir, monkeypatch, **kw):
    monkeypatch.setattr(gui_server, "ThreadingHTTPServer", _StopServer)
    monkeypatch.setattr(gui_server, "Handler", type("H", (), {"app": None}))
    gui_server.serve(str(data_dir / "models.yaml"), open_browser=False, **kw)
    return gui_server.Handler.app


def test_serve_consumes_the_sentinel_once_and_uses_the_new_token(data, monkeypatch, capsys):
    (data / "gui_token").write_text("old-token-0123456789\n", encoding="utf-8")
    (data / "RESET_LOGIN").write_bytes(b"")
    app = start(data, monkeypatch)
    new = accounts.read_stored_token(data)
    assert new and new != "old-token-0123456789"
    assert app.token == new and app.require_token     # loopback too: a stored token is honoured
    assert not (data / "RESET_LOGIN").exists()
    out = capsys.readouterr().out
    assert out.count("login reset from RESET_LOGIN") == 1
    assert new not in out                       # a stored token is never logged
    # second start: no sentinel, same token, no second reset line
    app2 = start(data, monkeypatch)
    assert app2.token == new
    assert "login reset" not in capsys.readouterr().out


def test_serve_without_any_token_keeps_loopback_open(data, monkeypatch):
    assert start(data, monkeypatch).token is None


def test_serve_token_precedence_flag_then_env_then_file(data, monkeypatch):
    (data / "gui_token").write_text("file-token-0123456789\n", encoding="utf-8")
    assert start(data, monkeypatch).token == "file-token-0123456789"
    monkeypatch.setenv(accounts.ENV_TOKEN, "env-token-0123456789")
    assert start(data, monkeypatch).token == "env-token-0123456789"
    assert start(data, monkeypatch, token="flag-token-0123456789").token == "flag-token-0123456789"


def test_stored_env_and_flag_tokens_are_never_logged(data, monkeypatch, capsys):
    (data / "gui_token").write_text("file-token-0123456789\n", encoding="utf-8")
    start(data, monkeypatch)
    out = capsys.readouterr().out
    assert "file-token-0123456789" not in out and "?token=" not in out
    assert f"loaded from {data / 'gui_token'}" in out
    assert "crucibleforge gui token show" in out
    monkeypatch.setenv(accounts.ENV_TOKEN, "env-token-0123456789")
    start(data, monkeypatch)
    out = capsys.readouterr().out
    assert "env-token-0123456789" not in out and "?token=" not in out
    assert f"loaded from ${accounts.ENV_TOKEN}" in out
    start(data, monkeypatch, token="flag-token-0123456789")
    out = capsys.readouterr().out
    assert "flag-token-0123456789" not in out and "?token=" not in out


def test_a_freshly_generated_token_is_the_only_one_logged(data, monkeypatch, capsys):
    app = start(data, monkeypatch, host="0.0.0.0")
    out = capsys.readouterr().out
    assert accounts.read_stored_token(data) is None          # not persisted
    assert f"?token={app.token}" in out                       # the one place to read it
    assert "loaded from" not in out


def test_serve_off_loopback_without_stored_token_still_generates(data, monkeypatch):
    app = start(data, monkeypatch, host="0.0.0.0")
    assert app.token and app.require_token and accounts.read_stored_token(data) is None


# ------------------------------------------------------------ the network cannot trigger it

@pytest.fixture
def live(data):
    (data / "gui_token").write_text("served-token-0123456789\n", encoding="utf-8")
    app = gui_server.App(str(data / "models.yaml"), "served-token-0123456789", True)
    old = gui_server.Handler.app
    gui_server.Handler.app = app
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), gui_server.Handler)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    try:
        yield httpd.server_address[1]
    finally:
        httpd.shutdown()
        httpd.server_close()
        gui_server.Handler.app = old


def _req(port, method, path, headers=None, body=None):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        c.request(method, path, body=body, headers=headers or {})
        r = c.getresponse()
        r.read()
        return r.status
    except (ConnectionError, http.client.HTTPException):
        return 0        # server dropped the connection without serving anything
    finally:
        c.close()


def test_no_http_request_creates_consumes_or_reads_the_sentinel(live, data):
    sentinel = data / "RESET_LOGIN"
    sentinel.write_bytes(b"")                       # planted AFTER start: only a restart may read it
    good = {"X-CrucibleForge-Token": "served-token-0123456789",
            "Content-Type": "application/json"}
    paths = ["/RESET_LOGIN", "/gui_token", "/static/RESET_LOGIN", "/static/gui_token",
             "/static/..%2fRESET_LOGIN", "/static/../gui_token", "/models.yaml",
             "/api/reset", "/api/reset-login", "/api/login", "/api/token", "/api/token/rotate",
             "/api/nothing?reset=1&RESET_LOGIN=1", "/nope?reset_login=1&token=nope"]
    for p in paths:
        for method in ("GET", "POST", "PUT", "DELETE"):
            for hdrs in ({}, good):
                status = _req(live, method, p, hdrs, b"{}" if method != "GET" else None)
                assert status in (0, 400, 401, 403, 404, 405, 501), (method, p, status)
    assert sentinel.exists(), "a network request consumed the sentinel"
    assert (data / "gui_token").read_text(encoding="utf-8").strip() == "served-token-0123456789"
    assert gui_server.Handler.app.token == "served-token-0123456789"


def test_the_handlers_never_touch_the_accounts_module():
    """Structural guard: inside gui/server.py only serve() may reference `accounts`."""
    src = (ROOT / "crucibleforge" / "gui" / "server.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    users = set()
    for fn in ast.walk(tree):
        if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if any(isinstance(n, ast.Name) and n.id == "accounts" for n in ast.walk(fn)):
                users.add(fn.name)
    assert users == {"serve"}, users
    # and nothing else in the GUI package imports it
    for f in (ROOT / "crucibleforge" / "gui").glob("*.py"):
        if f.name != "server.py":
            assert "accounts" not in f.read_text(encoding="utf-8"), f


def test_token_file_and_sentinel_are_gitignored():
    ignore = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert "gui_token" in ignore and "RESET_LOGIN" in ignore


# ------------------------------------------------------------ review round (Opus)

def test_an_unusable_gui_token_file_is_reported_not_ignored(data, monkeypatch, capsys, caplog):
    (data / "gui_token").write_text("short\n", encoding="utf-8")
    start(data, monkeypatch)
    out = capsys.readouterr().out
    assert "warning:" in out and "does not hold a usable token" in out
    rc, shown, err = run_cli(data, "show", monkeypatch=monkeypatch, capsys=capsys)
    assert rc == 1 and "warning:" in err and "gui token rotate" in err


def test_a_stored_token_never_reaches_the_log_records(data, monkeypatch, caplog):
    (data / "gui_token").write_text("secret-token-0123456789\n", encoding="utf-8")
    (data / "RESET_LOGIN").write_bytes(b"")
    with caplog.at_level("DEBUG"):
        app = start(data, monkeypatch)
    assert app.token and app.token not in caplog.text
    assert "secret-token-0123456789" not in caplog.text


def test_the_sentinel_is_consumed_before_the_socket_is_bound(data, monkeypatch):
    (data / "RESET_LOGIN").write_bytes(b"")
    seen = {}

    class Probe(_StopServer):
        def __init__(self, addr, handler):
            seen["sentinel_present_at_bind"] = (data / "RESET_LOGIN").exists()
            seen["token_present_at_bind"] = (data / "gui_token").exists()
            super().__init__(addr, handler)

    monkeypatch.setattr(gui_server, "ThreadingHTTPServer", Probe)
    monkeypatch.setattr(gui_server, "Handler", type("H", (), {"app": None}))
    gui_server.serve(str(data / "models.yaml"), open_browser=False)
    assert seen == {"sentinel_present_at_bind": False, "token_present_at_bind": True}


def test_a_token_with_unsafe_characters_is_warned_about(data, monkeypatch, capsys):
    start(data, monkeypatch, token="a+b&c#d%e;f=g h")
    assert "warning:" in capsys.readouterr().out


def test_the_data_dir_is_the_unresolved_folder_of_models_yaml(tmp_path):
    real = tmp_path / "dotfiles"
    real.mkdir()
    (real / "models.yaml").write_text("x: 1\n", encoding="utf-8")
    home = tmp_path / "home"
    home.mkdir()
    link = home / "models.yaml"
    try:
        link.symlink_to(real / "models.yaml")
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable here")
    assert accounts.data_dir_for(link) == home          # where results/ goes too


@pytest.mark.skipif(not POSIX, reason="needs mkfifo")
def test_a_fifo_sentinel_is_never_opened(data):
    os.mkfifo(data / "RESET_LOGIN")
    msg = accounts.consume_reset_sentinel(data)          # would hang if it were opened
    assert "not a regular file" in msg and "NOT reset" in msg
    assert not (data / "RESET_LOGIN").exists()


def test_write_token_leaves_no_temp_files_behind(data):
    accounts.write_token(data, "plain-token-0123456789")
    accounts.write_token(data, "other-token-0123456789")
    assert sorted(p.name for p in data.iterdir()) == ["gui_token", "models.yaml"]


def test_token_stdin_tolerates_a_bom(data, capsys, monkeypatch):
    rc, out, _ = run_cli(data, "rotate", "--token-stdin", stdin="﻿bom-chosen-token-0123\n",
                         monkeypatch=monkeypatch, capsys=capsys)
    assert rc == 0 and out == "bom-chosen-token-0123"


def test_docker_entrypoint_does_not_put_the_token_on_argv():
    ep = (ROOT / "deploy" / "docker-entrypoint.sh").read_text(encoding="utf-8")
    code = [ln for ln in ep.splitlines() if not ln.lstrip().startswith("#")]
    assert not any("--token" in ln for ln in code)
