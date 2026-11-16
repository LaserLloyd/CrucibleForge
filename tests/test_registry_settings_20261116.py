"""The settings_checked records are in git (R0). The live models.yaml stays gitignored."""
import ipaddress
from pathlib import Path
from urllib.parse import urlparse

import yaml

ROOT = Path(__file__).resolve().parents[1]
REG = ROOT / "registry" / "models.audited.yaml"


def _machine(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    if not host or host == "localhost" or host.endswith(".local"):
        return True
    try:
        return not ipaddress.ip_address(host).is_global
    except ValueError:
        return False


def test_registry_keeps_the_eight_settings_records():
    reg = yaml.safe_load(REG.read_text(encoding="utf-8"))
    blocks = {m["name"]: m["settings_checked"] for m in reg["models"] if m.get("settings_checked")}
    assert len(blocks) == 8
    for name, block in blocks.items():
        assert block["sampler"] == "none_published", name
        assert block["source"].startswith("https://"), name
        assert block["template"].strip(), name
        assert not _machine(block["source"])


def test_registry_has_no_secrets_or_machine_urls():
    text = REG.read_text(encoding="utf-8")
    assert "api_key:" not in text
    reg = yaml.safe_load(text)

    def walk(node):
        if isinstance(node, dict):
            assert "api_key" not in node
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)
        elif isinstance(node, str) and node.startswith(("http://", "https://")):
            assert not _machine(node), node

    walk(reg)


def test_registry_matches_the_live_file_when_present():
    live_path = Path.home() / "Projects" / "crucibleforge" / "models.yaml"
    if not live_path.is_file():
        return
    live = yaml.safe_load(live_path.read_text(encoding="utf-8"))
    reg = yaml.safe_load(REG.read_text(encoding="utf-8"))
    def sc(cfg):
        return {m["name"]: m.get("settings_checked") for m in cfg["models"] if m.get("settings_checked")}
    assert sc(live) == sc(reg)
    def rest(models):
        return [{k: v for k, v in m.items() if k != "tags"} for m in models]
    assert rest(live["models"]) == rest(reg["models"])
    assert all("tags" not in m for m in reg["models"])
