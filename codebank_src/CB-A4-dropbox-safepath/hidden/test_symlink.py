import pytest

from dropbox.paths import UnsafePath, safe_path


def test_symlink_out_of_root(tmp_path):
    root = tmp_path / "drop"
    root.mkdir()
    outside = tmp_path / "secret"
    outside.mkdir()
    (outside / "keys.txt").write_text("k")
    (root / "link").symlink_to(outside, target_is_directory=True)
    with pytest.raises(UnsafePath):
        safe_path(root, "link/keys.txt")
    with pytest.raises(UnsafePath):
        safe_path(root, "link/new.txt")


def test_symlink_to_sibling_with_same_prefix(tmp_path):
    root = tmp_path / "drop"
    root.mkdir()
    evil = tmp_path / "drop-evil"
    evil.mkdir()
    (root / "sib").symlink_to(evil, target_is_directory=True)
    with pytest.raises(UnsafePath):
        safe_path(root, "sib/x.txt")


def test_symlink_inside_root_is_fine(tmp_path):
    root = tmp_path / "drop"
    (root / "real").mkdir(parents=True)
    (root / "alias").symlink_to(root / "real", target_is_directory=True)
    p = safe_path(root, "alias/x.txt")
    assert p.resolve() == (root / "real" / "x.txt").resolve()
