"""The container's first-start seed must point EVERY local provider at the
Docker host.

``deploy/docker-entrypoint.sh`` seeds ``/data/models.yaml`` from
``models.example.yaml`` with a few ``sed`` rewrites. Inside a container
``localhost`` and ``127.0.0.1`` are the container itself, so a local
``base_url`` the rewrites miss is a provider that can never be reached -- and
the README promises the host's StudioForge / LM Studio "is found without
editing". 2026-10-18 review: the example's StudioForge entry had moved to
``http://127.0.0.1:1234/v1`` and the seed only knew the ``localhost`` spelling.

This applies the script's own rewrite rules, in order, to the real template,
so the two files cannot drift apart again without this failing.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

from _hostenv import posix_bash

ROOT = Path(__file__).resolve().parent.parent
ENTRYPOINT = ROOT / "deploy" / "docker-entrypoint.sh"
TEMPLATE = ROOT / "models.example.yaml"
LLM_BASE_URL = "http://host.docker.internal:1234/v1"      # the compose default


def _seed_rules() -> list[tuple[str, str]]:
    """The ``-e 's|FROM|TO|g'`` expressions of the seed step, in script order."""
    text = ENTRYPOINT.read_text(encoding="utf-8")
    block = text.split("first-start seed", 1)[1].split("command + GUI token", 1)[0]
    rules = re.findall(r"""-e\s+["']s\|([^|]+)\|([^|]*)\|g["']""", block)
    assert rules, "no sed rewrite rules found in the seed step"
    # ${url_sed} is LLM_BASE_URL escaped for sed; the compose default needs none
    return [(a, b.replace("${url_sed}", LLM_BASE_URL).replace("${LLM_BASE_URL}", LLM_BASE_URL))
            for a, b in rules]


def _seeded_template() -> str:
    out = TEMPLATE.read_text(encoding="utf-8")
    for src, dst in _seed_rules():
        out = out.replace(src, dst)
    return out


def _base_urls(yaml_text: str) -> dict[str, str]:
    urls, provider = {}, None
    for line in yaml_text.splitlines():
        m = re.match(r"^  (\w+):", line)
        if m:
            provider = m.group(1)
        m = re.match(r"^\s+base_url:\s*(\S+)", line)
        if m and provider:
            urls[provider] = m.group(1)
    return urls


def test_every_local_provider_is_rewritten_to_the_docker_host():
    urls = _base_urls(_seeded_template())
    assert urls, "no providers parsed from models.example.yaml"
    local = {p: u for p, u in urls.items()
             if "localhost" in u or "127.0.0.1" in u or "[::1]" in u}
    assert not local, f"seeded providers still point into the container: {local}"


def test_the_studioforge_gateway_entry_takes_llm_base_url():
    """The StudioForge example (``llamacpp``, gateway :1234) and the LM Studio
    one both serve on 1234: both must become the operator's LLM_BASE_URL."""
    urls = _base_urls(_seeded_template())
    assert urls["llamacpp"] == LLM_BASE_URL
    assert urls["lmstudio"] == LLM_BASE_URL
    assert urls["ollama"] == "http://host.docker.internal:11434/v1"


@pytest.mark.skipif(posix_bash() is None, reason="needs a POSIX shell with sed")
def test_llm_base_url_with_sed_metacharacters_is_seeded_verbatim(tmp_path):
    """2026-10-18 second review: LLM_BASE_URL went into the sed replacement
    unescaped -- a '&' re-inserted the matched text and a '|' ended the
    expression. Runs the script's own escaping line and rewrite rule."""
    url = "http://gw.example:1234/v1?a=1&b=2|c"
    escape = next(line.strip() for line in ENTRYPOINT.read_text(encoding="utf-8").splitlines()
                  if line.strip().startswith('url_sed="'))
    script = tmp_path / "seed.sh"
    script.write_text(
        "set -eu\n"
        f"{escape}\n"
        "printf 'base_url: http://127.0.0.1:1234/v1\\n' | "
        "sed -e \"s|http://127.0.0.1:1234/v1|${url_sed}|g\"\n",
        encoding="utf-8", newline="\n")
    out = subprocess.run([posix_bash(), str(script)], capture_output=True, text=True,
                         encoding="utf-8", env={"PATH": "/usr/bin:/bin", "LLM_BASE_URL": url},
                         timeout=30)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == f"base_url: {url}"
