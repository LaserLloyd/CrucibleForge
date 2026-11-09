# Install

[README](../README.md) · [Install](INSTALL.md) · [Running](RUNNING.md) · [Config](CONFIG.md) · [Scoring](SCORING.md) · [CLI](CLI.md) · [Design notes](DESIGN.md) · [Cases](CASES.md) · [Developing](DEVELOPING.md)

Pick your platform; in each, method 1 is the simplest. CrucibleForge runs from a git checkout (a wheel or `pip install` is not supported:
the cases, profiles and `models.example.yaml` live outside the Python package). It needs an OpenAI-compatible LLM server to test, such as the
StudioForge gateway, LM Studio, Ollama or a hosted API; no GPU is needed on the machine that runs CrucibleForge.

Ports: 8777 (the web GUI, loopback only by default). Point the LLM endpoint at the StudioForge gateway `http://<host>:1234/v1`,
never at the panel on :8080, which does not serve `/v1`. If your gateway requires a key, put its name in `api_key_env` on the provider
in `models.yaml` (the key itself stays in an environment variable).

Native installs run model-written code for the coding cases, so on Linux they need `bubblewrap` (`bwrap`); Windows and macOS have none
and need `--allow-unsandboxed`. The Docker image avoids both: the container is the sandbox.

## Quickest Docker route (any platform)

No Python install and no GPU in the container: the models run on your LLM server or a hosted API. This uses the
`compose.yaml` at the repository root. The per-platform sections below use the hardened `deploy/compose/compose.yml`
service instead.

```bash
cp .env.example .env            # add an API key (e.g. DEEPSEEK_API_KEY) or point LLM_BASE_URL at your server
docker compose build
docker compose run --rm crucibleforge status
docker compose run --rm crucibleforge run --models deepseek-flash --smoke
docker compose run --rm crucibleforge report
docker compose --profile gui up -d gui      # results GUI on http://127.0.0.1:8777
```

Coding scores need no judge. Chat and NSFW scores need a judge model: pass `--judge` (see [DOCKER.md](DOCKER.md) for a
local judge and the trade-offs of an API judge). Podman works the same with `podman compose`.

## Windows

Run the blocks in PowerShell. After a `winget install`, close and reopen PowerShell.

### 1. Docker Desktop (simplest)

Choose this to get the GUI with no Python and no sandbox flag. Your `models.yaml` and results live on a Docker volume that survives upgrades.

```powershell
winget install --id Git.Git -e --source winget
winget install --id Docker.DockerDesktop -e --source winget
# start Docker Desktop once and wait until it says the engine is running, then:
git clone https://github.com/LaserLloyd/CrucibleForge.git   # scrub-ok: public repo URL
cd CrucibleForge
Copy-Item deploy\compose\.env.example deploy\compose\.env
# optional: edit deploy\compose\.env (LLM_BASE_URL, API keys); the default is http://host.docker.internal:1234/v1
docker compose -f deploy/compose/compose.yml up -d
docker compose -f deploy/compose/compose.yml ps                        # STATUS: healthy
docker compose -f deploy/compose/compose.yml logs app | Select-String "GUI:"   # open the printed http://127.0.0.1:8777/?token=... URL
```

| Pros | Cons |
|---|---|
| No Python, no `uv`, no sandbox flag; the container is the sandbox | Docker Desktop is a large install and needs virtualisation enabled |
| Data on a named volume; `down` keeps it, updates keep it | The LLM server on the host must accept connections from the container, not only on the host's `127.0.0.1` |
| Read-only image, non-root, no capabilities | Coding cases run in the container, which can reach the network |

### 2. Native, `uv` from a clone

Choose this to edit the code or run without Docker.

```powershell
winget install --id Git.Git -e --source winget
winget install --id astral-sh.uv -e --source winget
# close and reopen PowerShell, then:
git clone https://github.com/LaserLloyd/CrucibleForge.git   # scrub-ok: public repo URL
cd CrucibleForge
uv sync --python 3.13
uv run crucibleforge config --init              # writes models.yaml; edit its base_url to your gateway /v1
uv run crucibleforge cases list | Select-Object -Last 1     # prints the case count
uv run crucibleforge --allow-unsandboxed gui    # http://127.0.0.1:8777
```

`--allow-unsandboxed` goes before the subcommand and is needed for `run`, `all`, `gui`, `bench-all` and `cases verify`: model-written code then runs
as you, with no isolation. Use method 1 if that worries you.

| Pros | Cons |
|---|---|
| Smallest; edit the code and re-run at once | No sandbox on Windows: graded code runs as you |
| `uv` fetches Python 3.13 itself (the pin matches the Docker image) | `--detach` needs systemd, and the rig lock is not enforced on Windows |
| Results sit in the checkout next to `models.yaml` | You manage updates (`git pull`, `uv sync`) |

## Linux

Blocks assume Ubuntu or Debian (apt).

### 1. Docker Compose (simplest)

Choose this to get the GUI with no Python and no bubblewrap. Your `models.yaml` and results live on a Docker volume that survives upgrades.

```bash
sudo apt-get update && sudo apt-get install -y git curl docker.io docker-compose-v2
sudo usermod -aG docker $USER      # log out and in once, or prefix the docker lines below with sudo
git clone https://github.com/LaserLloyd/CrucibleForge.git && cd CrucibleForge   # scrub-ok: public repo URL
cp deploy/compose/.env.example deploy/compose/.env
# optional: edit deploy/compose/.env (LLM_BASE_URL, API keys); the default is http://host.docker.internal:1234/v1
docker compose -f deploy/compose/compose.yml up -d
docker compose -f deploy/compose/compose.yml ps                      # STATUS: healthy
docker compose -f deploy/compose/compose.yml logs app | grep "GUI:"  # open the printed http://127.0.0.1:8777/?token=... URL
```

| Pros | Cons |
|---|---|
| No Python, no `uv`, no bubblewrap; the container is the sandbox | Needs a Docker engine and about 190 MB for the image |
| Data on a named volume; `down` keeps it, updates keep it | The LLM server on the host must listen on an address the container can reach, not only `127.0.0.1` |
| Read-only image, non-root, no capabilities | Coding cases run in the container, which can reach the network |

### 2. Native, `uv` from a clone

Choose this to edit the code, use `--detach` (a `systemd --user` unit), or get bubblewrap isolation for the coding cases. A bare Ubuntu image has none of
`git`, `curl` or `bwrap`, so the first line installs them.

```bash
sudo apt-get update && sudo apt-get install -y git curl ca-certificates bubblewrap
curl -LsSf https://astral.sh/uv/install.sh | sh
source $HOME/.local/bin/env
git clone https://github.com/LaserLloyd/CrucibleForge.git && cd CrucibleForge   # scrub-ok: public repo URL
uv sync --python 3.13
uv run crucibleforge config --init     # writes models.yaml; edit its base_url to your gateway /v1
uv run crucibleforge cases list | tail -n 1     # prints the case count
uv run crucibleforge gui               # http://127.0.0.1:8777
```

If bubblewrap is missing or cannot start (a container, or a kernel that blocks unprivileged user namespaces), pass `--allow-unsandboxed` before the subcommand; CrucibleForge tests bwrap once at start and says which case it is.

| Pros | Cons |
|---|---|
| Bubblewrap isolates graded code from your home directory | Most prerequisites: git, curl, uv, bubblewrap |
| `--detach` runs long benchmarks as a `systemd --user` unit | You manage updates (`git pull`, `uv sync`) |
| Edit the code and re-run at once; results sit in the checkout | PEP 668 does not apply (uv makes its own venv), but `pip install .` is unsupported |

## macOS

The GUI and CLI are plain Python, so a native install should work; there is no bubblewrap on macOS, so every run command needs `--allow-unsandboxed`.
Docker Desktop avoids that.

### 1. Docker Desktop (simplest)

```bash
brew install git
brew install --cask docker     # then open Docker Desktop once and wait for "Engine running"
git clone https://github.com/LaserLloyd/CrucibleForge.git && cd CrucibleForge   # scrub-ok: public repo URL
cp deploy/compose/.env.example deploy/compose/.env
docker compose -f deploy/compose/compose.yml up -d
docker compose -f deploy/compose/compose.yml ps                      # STATUS: healthy
docker compose -f deploy/compose/compose.yml logs app | grep "GUI:"  # open the printed URL
```

| Pros | Cons |
|---|---|
| No Python, no sandbox flag; data on a named volume | Docker Desktop is a large install |
| Same files and steps as Linux | Needs Homebrew (<https://brew.sh>) for the commands above |

### 2. Native, `uv` from a clone

```bash
brew install git uv
git clone https://github.com/LaserLloyd/CrucibleForge.git && cd CrucibleForge   # scrub-ok: public repo URL
uv sync --python 3.13
uv run crucibleforge config --init     # writes models.yaml; edit its base_url to your gateway /v1
uv run crucibleforge cases list | tail -n 1     # prints the case count
uv run crucibleforge --allow-unsandboxed gui    # http://127.0.0.1:8777
```

| Pros | Cons |
|---|---|
| Smallest; edit the code and re-run at once | No sandbox: graded code runs as you |
| `uv` fetches Python 3.13 itself | You manage updates (`git pull`, `uv sync`) |

## Docker settings, CLI and backups

The GUI URL in the log always shows port 8777, the container's port. If you changed `HOST_PORT`, open the same URL with that port instead.

Optional settings go in `deploy/compose/.env` (copy `.env.example`):

| Variable | Default | |
|---|---|---|
| `LLM_BASE_URL` | `http://host.docker.internal:1234/v1` | your local server as the container sees it (seeds `models.yaml` once; later edit it in the GUI) |
| `CRUCIBLEFORGE_GUI_TOKEN` | empty (`/data/gui_token`, else random per start) | stable GUI access token; lost it? see [Accounts and lockout recovery](RUNNING.md#accounts-and-lockout-recovery) |
| `DEEPSEEK_API_KEY` / `OPENROUTER_API_KEY` / `OPENAI_API_KEY` | empty | hosted providers; names must match `api_key_env` |
| any other key, e.g. `STUDIOFORGE_API_KEY` | not set | add the line to `.env`, then set `api_key_env: STUDIOFORGE_API_KEY` on that provider in the GUI (Providers) |
| `CRUCIBLEFORGE_ALLOW_UNSANDBOXED` | `1` | the container is the sandbox for model-written code ([why](deploy/docker.md#coding-cases-and-the-sandbox)) |
| `BIND_ADDR` / `HOST_PORT` | `127.0.0.1` / `8777` | host side of the port mapping; a token is required off-loopback |

Run the CLI in the same image and volume:

```bash
docker compose -f deploy/compose/compose.yml run --rm app cases verify
docker compose -f deploy/compose/compose.yml run --rm app all --models <label> --fresh --yes
```

## Upgrade and uninstall

- **Docker:** `git pull` then `docker compose -f deploy/compose/compose.yml up -d --build`. To roll back, `git checkout` the earlier commit and
  run the same `up -d --build`. `models.yaml` and `results/` live only in the `crucibleforge-data` volume, which `down` keeps and a new image reuses;
  on the first start of a new version the small state files are copied to `/data/backups/` (last 5 kept). `docker compose ... down` removes the
  container; **`down -v` also deletes the volume**. Back up with the app stopped:

  ```bash
  docker compose -f deploy/compose/compose.yml stop app
  docker run --rm -v crucibleforge_crucibleforge-data:/data:ro -v "$PWD":/backup alpine tar czf /backup/crucibleforge-data-$(date +%F).tgz -C /data .
  docker compose -f deploy/compose/compose.yml start app
  ```

  Restore, bind-mounted data (`PUID`/`PGID`) and the full list of settings: [`docs/deploy/docker.md`](deploy/docker.md).
- **Native:** `git pull && uv sync --python 3.13`. To uninstall, copy `models.yaml` and `results/` somewhere safe, then delete the clone
  (they sit in it unless you set `CRUCIBLEFORGE_CONFIG` or `CRUCIBLEFORGE_RESULTS`).
