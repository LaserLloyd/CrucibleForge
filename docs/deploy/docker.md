# Running CrucibleForge in Docker

CrucibleForge is a benchmark runner with a web GUI. The image contains the
program, the case set (`cases/`) and the profiles (`profiles/`); your registry
(`models.yaml`) and every result live in one volume, `/data`. No GPU is needed:
the models run on your LLM server, not in this container.

```bash
git clone https://github.com/LaserLloyd/CrucibleForge.git && cd CrucibleForge   # scrub-ok: public repo URL
docker compose -f deploy/compose/compose.yml up -d
docker compose -f deploy/compose/compose.yml logs app       # prints the GUI URL (with its access token)
```

Open the printed `http://127.0.0.1:8777/?token=...` URL once; a cookie carries
the token afterwards.

## Ports

| Port | Where | What |
|---|---|---|
| 8777 | container, published as `${BIND_ADDR:-127.0.0.1}:${HOST_PORT:-8777}` | the web GUI (CrucibleForge's own default) |

Nothing else listens. The container is published on loopback only unless you
set `BIND_ADDR=0.0.0.0`; the GUI then still demands its token.

## Configuration

Copy `deploy/compose/.env.example` to `deploy/compose/.env` (optional; every
value has a default).

| Variable | Default | Meaning |
|---|---|---|
| `LLM_BASE_URL` | `http://host.docker.internal:1234/v1` | Your local OpenAI-compatible server (StudioForge, LM Studio) **as seen from the container**. Used once, to seed `/data/models.yaml` on the first start. |
| `CRUCIBLEFORGE_GUI_TOKEN` | empty | Stable GUI access token. Empty = a fresh one is printed on every start. |
| `DEEPSEEK_API_KEY`, `OPENROUTER_API_KEY`, `OPENAI_API_KEY` | empty | Hosted-provider keys. The names must match `api_key_env` in `models.yaml`. Keys are never written to `models.yaml`. |
| `CRUCIBLEFORGE_ALLOW_UNSANDBOXED` | `1` | See "Coding cases and the sandbox". |
| `BIND_ADDR` / `HOST_PORT` | `127.0.0.1` / `8777` | Host side of the port mapping. |
| `DATA_PATH` | named volume `crucibleforge-data` | Use a host directory (owned by UID 1000) to browse or back up results. |
| `MEM_LIMIT` / `CPU_LIMIT` | `2g` / `2.0` | Resource limits. |

Also honoured, unchanged from a native install: `CRUCIBLEFORGE_CONFIG`,
`CRUCIBLEFORGE_RESULTS`, `CRUCIBLEFORGE_ENV_FILE`, `CRUCIBLEFORGE_NO_PRIVATE`.

## Pointing it at StudioForge or LM Studio on the host

Inside a container `localhost` is the container. The compose file maps
`host.docker.internal` to the Docker host (Docker Desktop provides it; on Linux
engines the `host-gateway` entry in the compose file does), and the first start
rewrites the template's local providers:

* `http://localhost:1234/v1` becomes `$LLM_BASE_URL`
* every other `http://localhost:PORT` becomes `http://host.docker.internal:PORT`

An existing `/data/models.yaml` is never touched. To change a provider later,
edit it in the GUI (Providers), or replace the file in the volume:

```bash
docker compose -f deploy/compose/compose.yml cp app:/data/models.yaml ./models.yaml   # copy out
# edit, then copy back:
docker compose -f deploy/compose/compose.yml cp ./models.yaml app:/data/models.yaml
```

(`cp` needs the `app` container to exist; `up -d` creates it.) The host's LLM
server must listen on an address the container can reach, not only on the
host's `127.0.0.1` (StudioForge and LM Studio both have a "serve on local
network" / bind-all option).

## Running the CLI

Any `crucibleforge` subcommand works as a one-off container sharing the volume.
A first argument that is not an executable is treated as a subcommand:

```bash
docker compose -f deploy/compose/compose.yml run --rm app cases list
docker compose -f deploy/compose/compose.yml run --rm app cases verify     # re-derives every gold answer, offline
docker compose -f deploy/compose/compose.yml run --rm app status           # contacts every provider in models.yaml
docker compose -f deploy/compose/compose.yml run --rm app all --models <label> --fresh --yes
docker compose -f deploy/compose/compose.yml run --rm app report
```

Results are in the volume under `/data/results` (`report.md`, `report.html`,
`failures.md`, `runs.csv`, `transcripts_<label>.jsonl`). A run started from the
CLI and one started from the GUI share the rig lock, so only one benchmark runs
at a time.

## Coding cases and the sandbox

Grading a coding case executes source a model wrote. CrucibleForge refuses to
do that unless it can isolate it with `bubblewrap` (`bwrap`), or you opt in
with `--allow-unsandboxed`. `bwrap` needs unprivileged user namespaces, which
Docker's default seccomp profile blocks, and the grader prefers `bwrap`
whenever it is on `PATH` even when the opt-in is set. So the image:

* does **not** install `bubblewrap` (a present-but-unusable `bwrap` would fail
  every coding case with "No permissions to create a new namespace"), and
* sets `CRUCIBLEFORGE_ALLOW_UNSANDBOXED=1` in the compose file: the **container
  is the sandbox**. It runs as UID 1000, with all capabilities dropped,
  `no-new-privileges`, and memory/CPU limits. It can still reach the network
  and read `/data`, so keep nothing in that volume you would not hand to a
  model's code.

To use `bwrap` instead, build with `--build-arg WITH_BWRAP=1`, run the
container under a seccomp profile that allows user namespaces, and set
`CRUCIBLEFORGE_ALLOW_UNSANDBOXED=0`.

## Data, upgrades, removal

```bash
git pull && docker compose -f deploy/compose/compose.yml up -d --build     # upgrade; /data is kept
docker compose -f deploy/compose/compose.yml down                          # stop; /data is kept
docker compose -f deploy/compose/compose.yml down -v                       # stop AND delete results + models.yaml
```

A stop sends SIGTERM; the process exits with status 143 by design (the CLI
turns SIGTERM into `SystemExit(128+15)` so lease-release handlers run), which
is a normal stop, not a crash.

## Image

Built `FROM ghcr.io/astral-sh/uv:python3.13-trixie-slim` and run on
`python:3.13-slim-trixie` (same interpreter at the same path, uv not in the
runtime image), non-root UID 1000, `/data` volume, tini via `init: true`,
`HEALTHCHECK` on the GUI port. Tagged releases are published as
`ghcr.io/laserlloyd/crucibleforge:<version>` with a CycloneDX SBOM, SHA256SUMS,
a cosign keyless signature and build-provenance attestation (see
`.github/workflows/containers.yml`).
