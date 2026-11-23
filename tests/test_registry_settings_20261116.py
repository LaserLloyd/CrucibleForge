"""The settings_checked records are in git (R0). The live models.yaml stays gitignored."""
import ipaddress
from pathlib import Path
from urllib.parse import urlparse

import pytest
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
        pytest.skip("no live models.yaml on this machine (the maintainer's own checkout)")
    live = yaml.safe_load(live_path.read_text(encoding="utf-8"))
    reg = yaml.safe_load(REG.read_text(encoding="utf-8"))
    # The registry is the tree's snapshot of the live file; the live file may have gained models since (it is the
    # production checkout's, edited between releases). Every model the registry names must equal its live entry
    # (settings_checked included); a model added to the live file later is not the snapshot's error.
    names = [m["name"] for m in reg["models"]]
    live_by = {m["name"]: m for m in live["models"]}
    def sc(models):
        return {m["name"]: m.get("settings_checked") for m in models if m.get("settings_checked")}
    assert sc(reg["models"]) == sc([live_by[n] for n in names if n in live_by])
    def rest(m):
        return {k: v for k, v in m.items() if k != "tags"}
    missing = [n for n in names if n not in live_by]
    assert not missing, missing
    assert [rest(live_by[n]) for n in names] == [rest(m) for m in reg["models"]]
    assert all("tags" not in m for m in reg["models"])
