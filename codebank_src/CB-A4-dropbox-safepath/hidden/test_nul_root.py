import pytest

from dropbox.paths import UnsafePath, safe_path


@pytest.mark.parametrize("given", ["%00", "a%00b.txt", ".", "/", "a/..", "./", "%2e", "a/b/../..", "\\"])
def test_rejected(tmp_path, given):
    with pytest.raises(UnsafePath):
        safe_path(tmp_path, given)


def test_error_type_is_value_error(tmp_path):
    with pytest.raises(ValueError, match="unsafe path"):
        safe_path(tmp_path, "%00")
