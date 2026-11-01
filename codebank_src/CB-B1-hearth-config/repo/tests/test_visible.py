from hearth.config import load_config


def test_v1_file(tmp_path):
    p = tmp_path / "hearth.toml"
    p.write_text('[server]\nhost = "0.0.0.0"\nport = 9000\n[storage]\npath = "/srv/h"\n')
    cfg, _ = load_config(p, env={})
    assert cfg["http"] == {"host": "0.0.0.0", "port": 9000}
    assert cfg["storage"]["dir"] == "/srv/h"
