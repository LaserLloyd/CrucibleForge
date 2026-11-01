import json

import pytest

from _http import call, files_under
from dropbox.app import make_app
from dropbox.paths import UnsafePath, safe_path

ATTACKS = ["../x", "a/../../x", "..\\x", "a\\..\\..\\x", "%2e%2e/x",
           "%2E%2E%2Fx", "photos/%2e%2e/%2e%2e/x", "%2e%2e%5cx", "./../x", "a/b/../../../x"]


@pytest.mark.parametrize("attack", ATTACKS)
def test_rejected(tmp_path, attack):
    root = tmp_path / "drop"
    root.mkdir()
    with pytest.raises(UnsafePath):
        safe_path(root, attack)


def test_app_400_and_nothing_written(tmp_path):
    root = tmp_path / "drop"
    root.mkdir()
    app = make_app(root)
    for attack in ATTACKS:
        status, body = call(app, "PUT", "/files/" + attack, b"pwn")
        assert status == 400 and json.loads(body) == {"error": "unsafe path"}, attack
    assert files_under(tmp_path) == ["drop"]
