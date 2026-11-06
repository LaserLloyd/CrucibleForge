"""Static checks on the Docker setup (no Docker needed).

The image must never carry private or operator-local material, the beginner
compose file must keep the GUI on loopback and the data on a volume, the
starter registry must not point a provider into the container, and the
.env template must hold key NAMES only. scripts/docker-smoke.sh is the live
counterpart (it builds the image and lists its files).
"""
from __future__ import annotations

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent


def _ignore_lines() -> set[str]:
    return {l.strip() for l in (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
            if l.strip() and not l.lstrip().startswith("#")}


def test_dockerignore_keeps_private_material_out():
    lines = _ignore_lines()
    for must in ("cases/private", "profiles/private", "profiles/*.private.yaml",
                 "**/*.local.*", "*.local.*", "docs/CUSTOM-TESTS.md", "docs",
                 "models.yaml", "models.yaml.*", ".env", ".env.*", "results",
                 ".git", ".venv", "gui_token", "scripts/scrub-rules.local.txt"):
        assert must in lines, f".dockerignore lost {must!r}"
    assert not any(l.startswith("!") for l in lines), \
        "a negation in .dockerignore can re-include a private file"


def _compose() -> dict:
    return yaml.safe_load((ROOT / "compose.yaml").read_text(encoding="utf-8"))


def test_compose_cli_service_uses_the_volume_and_starter_seed():
    svc = _compose()["services"]["crucibleforge"]
    assert any(str(v).endswith(":/data") for v in svc["volumes"])
    assert svc["environment"]["CRUCIBLEFORGE_SEED_TEMPLATE"] == "/app/deploy/models.docker.yaml"
    assert svc["read_only"] is True and svc["cap_drop"] == ["ALL"]
    assert "host.docker.internal:host-gateway" in svc["extra_hosts"]
    assert not svc.get("ports"), "the CLI service must publish no port"


def test_compose_gui_is_opt_in_and_loopback_by_default():
    gui = _compose()["services"]["gui"]
    assert gui["profiles"] == ["gui"]
    (port,) = gui["ports"]
    assert port.startswith("${BIND_ADDR:-127.0.0.1}:"), port


def test_env_example_holds_names_not_values():
    for line in (ROOT / ".env.example").read_text(encoding="utf-8").splitlines():
        m = re.match(r"^\s*#?\s*([A-Z0-9_]*(KEY|TOKEN|SECRET|PASSWORD)[A-Z0-9_]*)=(.*)$", line)
        if m:
            assert m.group(3).strip() == "", f"{m.group(1)} has a value in .env.example"


def test_docker_starter_registry_points_local_at_the_host():
    cfg = yaml.safe_load((ROOT / "deploy" / "models.docker.yaml").read_text(encoding="utf-8"))
    provs = cfg["providers"]
    assert provs["local"]["base_url"] == "http://host.docker.internal:1234/v1"
    for name, p in provs.items():
        assert "localhost" not in p["base_url"] and "127.0.0.1" not in p["base_url"], name
        assert "api_key" not in p, f"{name}: keys belong in .env (api_key_env)"
    models = {m["name"]: m for m in cfg["models"]}
    assert {"local-model", "deepseek-flash"} <= set(models)
    for m in models.values():
        assert m["provider"] in provs, m["name"]
    # a judge never grades its own rows: the API judge must differ from the API model
    judges = {j["name"]: j for j in cfg["judge"]["candidates"]}
    assert judges["api-judge"]["model_id"] != models["deepseek-flash"]["model_id"]


def test_dockerfile_ships_the_starter_registry():
    text = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "deploy/models.docker.yaml" in text
    assert "deploy/compose" in _ignore_lines()          # but not the compose files
