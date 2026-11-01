from _unit import SVC, one, read


def test_optional_env_file():
    s = read(SVC)
    assert one(s, "Service", "EnvironmentFile") in ("-%h/.config/hearth/backup.env",
                                                   "-/home/alex/.config/hearth/backup.env")
