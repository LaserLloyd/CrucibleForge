"""NSFW+ default on; --no-nsfw-plus opts out; --adult and --no-adult stay as silent aliases."""
from crucibleforge import adult_guard as G
from crucibleforge import cli


def _parse(argv):
    import argparse
    seen = {}
    mp = __import__("pytest").MonkeyPatch()
    mp.setattr(cli, "load_config", lambda p=None: {})
    mp.setattr(cli, "cmd_run", lambda a, c: seen.setdefault("a", a) and 0)
    try:
        cli.main(argv)
    except SystemExit:
        pass
    finally:
        mp.undo()
    return seen["a"]


def test_default_is_on():
    assert G.args_wants(_parse(["run"])) is True


def test_opt_out_and_aliases():
    assert G.args_wants(_parse(["run", "--no-nsfw-plus"])) is False
    assert G.args_wants(_parse(["run", "--no-adult"])) is False
    assert G.args_wants(_parse(["run", "--adult"])) is True


def test_aliases_are_hidden_from_help(capsys):
    import pytest
    with pytest.raises(SystemExit):
        cli.main(["run", "--help"])
    out = capsys.readouterr().out
    assert "--no-nsfw-plus" in out and "--adult" not in out and "--no-adult" not in out


def test_entry_keys_both_read():
    assert G.adult_page_planned({}, {"name": "m", "nsfw_plus": True}) is True
    assert G.adult_page_on({}, {"name": "m", "nsfw_plus": False}) is False
    assert G.adult_page_on({}, {"name": "m", "adult_page": False}) is False
    assert G.adult_page_on({}, {"name": "m"}) is True
