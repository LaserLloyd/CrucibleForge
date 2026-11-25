# Running CrucibleForge in Docker

> New to this? The short path (one `compose.yaml` at the repo root, a starter
> registry, run a benchmark in a few commands) is [`docs/DOCKER.md`](../DOCKER.md).
> This page covers the always-on GUI service in `deploy/compose/`.

CrucibleForge is a benchmark runner with a web GUI. The image contains the
program, the case set (`cases/`) and the profiles (`profiles/`); your registry
(`models.yaml`) and every result live in one volume, `/data`. No GPU is needed:
the models run on your LLM server, not in this container.

```bash
git clone https://github.com/LaserLloyd/CrucibleForge.git && cd CrucibleForge   # scrub-ok: public repo URL
docker compose -f deploy/compose/compose.yml up -d
docker compose -f deploy/compose/compose.yml logs app       # prints the GUI URL (with its access token on the first start; once a token is stored or set, read it with `exec app crucibleforge gui token show`)
```

Open the printed `http://127.0.0.1:8777/?token=...` URL once; a cookie carries
the token afterwards. Only a token generated on that start appears in the log; one you configured
(`CRUCIBLEFORGE_GUI_TOKEN`) or stored (`gui token rotate`) is never logged, so read it with
`docker compose -f deploy/compose/compose.yml exec app crucibleforge gui token show`.

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
| `CRUCIBLEFORGE_GUI_TOKEN` | empty | Stable GUI access token; wins over `/data/gui_token`. Empty = use `/data/gui_token` if it exists, else a fresh one is printed on every start. See "Accounts and lockout recovery". |
| `DEEPSEEK_API_KEY`, `OPENROUTER_API_KEY`, `OPENAI_API_KEY` | empty | Hosted-provider keys. The names must match `api_key_env` in `models.yaml`. Keys are never written to `models.yaml`. |
| `CRUCIBLEFORGE_ALLOW_UNSANDBOXED` | `1` | See "Coding cases and the sandbox". |
| `BIND_ADDR` / `HOST_PORT` | `127.0.0.1` / `8777` | Host side of the port mapping. |
| `DATA_PATH` | named volume `crucibleforge-data` | Use a host directory you own to browse or back up results. |
| `MEM_LIMIT` / `CPU_LIMIT` | `2g` / `2.0` | Resource limits. |

Also honoured, unchanged from a native install: `CRUCIBLEFORGE_CONFIG`,
`CRUCIBLEFORGE_RESULTS`, `CRUCIBLEFORGE_ENV_FILE`, `CRUCIBLEFORGE_NO_PRIVATE`.

## Accounts and lockout recovery

The GUI's access token is the only login (no users, no passwords; provider keys
are environment variables). Everything stateful is in `/data`, so the token
lives at `/data/gui_token`, on the same volume as `models.yaml`.

```bash
export COMPOSE_FILE=deploy/compose/compose.yml    # PowerShell: $env:COMPOSE_FILE = 'deploy/compose/compose.yml'
docker compose exec app crucibleforge gui token show      # the token, the data dir it used, and its source
docker compose exec app crucibleforge gui token rotate    # new random token -> /data/gui_token
printf '%s\n' 'a-token-you-chose-123' | docker compose exec -T app crucibleforge gui token rotate --token-stdin   # PowerShell: 'a-token-you-chose-123' | docker compose exec -T ...
docker compose restart app                                # a running GUI keeps the token it started with
```

`exec` runs as root in the container unless you add `--user <uid>`; run it
as the data folder's owner (`--user $(id -u)` on Linux) so the app can read
what it writes. If `CRUCIBLEFORGE_GUI_TOKEN` is set in
`deploy/compose/.env` it overrides the file: change it there and run
`docker compose up -d`.

**Lost the token and cannot read the logs?** Create the sentinel on the volume
and restart; CrucibleForge consumes it once, before the port opens:

```bash
docker compose exec app sh -c ': > /data/RESET_LOGIN'     # empty = random new token; or write the token you want into it
docker compose restart app
docker compose exec app crucibleforge gui token show
```

With the container stopped, `docker run --rm -v crucibleforge_crucibleforge-data:/data --user 1000:1000 busybox sh -c ': > /data/RESET_LOGIN'`
does the same (swap the volume name for your `DATA_PATH` bind mount if you use one). Only `gui_token`
changes; `models.yaml` and `results/` are kept. No network request can trigger the reset. The pre-upgrade
backup does not copy `gui_token` (it is a secret: rotate instead of restoring it); the volume backup
below does include it.

## Pointing it at StudioForge or LM Studio on the host

Inside a container `localhost` is the container. The compose file maps
`host.docker.internal` to the Docker host (Docker Desktop provides it; on Linux
engines the `host-gateway` entry in the compose file does), and the first start
rewrites the template's local providers:

* `http://localhost:1234/v1` and `http://127.0.0.1:1234/v1` (the LM Studio and
  StudioForge-gateway examples) become `$LLM_BASE_URL`
* every other `http://localhost:PORT` / `http://127.0.0.1:PORT` becomes
  `http://host.docker.internal:PORT`

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

Grading a coding case executes source a model wrote. Outside a container CrucibleForge isolates it with
`bubblewrap` (`bwrap`); `bwrap` needs unprivileged user namespaces, which Docker's default seccomp profile blocks,
so the image does not ship it. Instead the image carries a small setuid helper, `cf-jail`
([`deploy/cf-jail.c`](../../deploy/cf-jail.c)), and the engine starts every graded program through it
(`crucibleforge/jail.py`). The helper gives the program its own scratch folder, sets limits (512 processes,
8 GiB data, 900 CPU seconds, 1 GiB per file, 1024 open files, no core files), switches to user 65534 with no
groups, and installs a seccomp filter that refuses IPv4, IPv6, netlink and packet sockets, `ptrace`, cross-process
memory reads and `io_uring`. The benchmark itself runs as the owner of `/data` (the container starts as root only
to find that owner) and is non-dumpable, so its environment cannot be read through `/proc` by another user.

What that gives, each point checked by `python -m crucibleforge.jail --selftest` in the offline end-to-end test
(`deploy/e2e/offline_e2e.sh`, run by CI on every push):

* graded code cannot read the provider key: not in its environment, not in `/proc/<pid>/environ` of any process,
  not in `models.yaml` (mode 600) or anything else in `/data` (the folder is closed to other users);
* it cannot write to `/data`, `/data/results` or the image; it can write only its own scratch folder;
* it cannot open a network connection: not to the internet, not to your machine, not to the container's loopback;
* it runs under the limits above.

What it does not give: graded code still sees the image's own files (the engine source and the public test
cases, read-only), and a kernel flaw could break any of this; the helper does not replace a virtual machine.

**When the helper cannot work.** A setuid helper is switched off by `--security-opt no-new-privileges`, a
`nosuid` mount, or a user that cannot switch users. The entrypoint tests it at start; when it fails, graded code is
refused (the engine's normal "no sandbox" message) unless you set `CRUCIBLEFORGE_ALLOW_UNSANDBOXED=1`, in which
case the entrypoint logs a warning and graded code runs as the benchmark user, with network. The shipped compose
files therefore leave `no-new-privileges` off and keep `cap_drop: [ALL]` with only the capabilities the
entrypoint and the helper need.

## Your data, upgrades, backups

**All state is in the `/data` volume** (`crucibleforge-data`, or the host directory
in `DATA_PATH`): `models.yaml` and `results/` (transcripts, `runs.csv`, reports,
`crucibleforge.log`). Nothing stateful is written inside the image: its root
filesystem is mounted read-only (`/tmp` is a tmpfs), so a stray write fails
loudly instead of disappearing on the next update.

| Command | Effect on your data |
|---|---|
| `docker compose ... down` | stops and removes the container; **the volume is kept** |
| `docker compose ... down -v` | **also deletes the volume: `models.yaml` and every result are gone** |
| `docker compose ... up -d --build` after `git pull` | new image, same volume: data intact |

The container gets 60 s to stop (`stop_grace_period`). A stop is SIGTERM and exits
143 by design.

### Upgrades

```bash
git pull
docker compose -f deploy/compose/compose.yml up -d --build
```

On the first start of a new image version the entrypoint copies the small state
files (`models.yaml`, `models.yaml.bak*`, and the top-level `results/*.csv|json|md`)
to `/data/backups/<old>-to-<new>-<timestamp>/` and keeps the last 5. Transcripts
(`transcripts_*.jsonl`, the large files) are not copied: use the volume backup
below for those. The version compared is `<package version>-<VERSION build arg>`;
release images set `VERSION`, so pass `--build-arg VERSION=...` (or `VERSION=` in
`.env`) when building locally if you want a backup on every rebuild.

### Back up and restore the volume

Stop the app first so the copy is consistent. The volume is named
`<project>_<volume>` = `crucibleforge_crucibleforge-data` (`docker volume ls`).

```bash
docker compose -f deploy/compose/compose.yml stop app
docker run --rm -v crucibleforge_crucibleforge-data:/data:ro -v "$PWD":/backup alpine \
    tar czf /backup/crucibleforge-data-$(date +%F).tgz -C /data .
docker compose -f deploy/compose/compose.yml start app
```

Restore (replaces the volume's contents; the app must be down):

```bash
docker compose -f deploy/compose/compose.yml down
docker run --rm -v crucibleforge_crucibleforge-data:/data -v "$PWD":/backup alpine \
    sh -c 'rm -rf /data/* /data/.[!.]* && tar xzf /backup/crucibleforge-data-YYYY-MM-DD.tgz -C /data'
docker compose -f deploy/compose/compose.yml up -d
```

(If you use `DATA_PATH` with a host directory, back that directory up with your
usual tools instead, with the app stopped.)

### Volume permissions (bind mounts)

The image runs as UID/GID 1000. A named volume just works. A bind-mounted
`DATA_PATH` must be writable by that user; if it is owned by someone else the
container stops at once with a message saying so. Either `chown 1000:1000` it, or
let the container fix it and run as your user:

```bash
PUID=$(id -u) PGID=$(id -g) docker compose \
    -f deploy/compose/compose.yml -f deploy/compose/compose.puid.yml up -d
```

That override starts the container as root only to `chown` the data directory and
drop to `PUID:PGID` (`setpriv`); the app process runs with no capabilities.

## Image

Built `FROM ghcr.io/astral-sh/uv:python3.13-trixie-slim` and run on
`python:3.13-slim-trixie` (same interpreter at the same path, uv not in the
runtime image), both pinned by digest; the benchmark runs as the owner of
`/data`, graded code as uid 65534 through `cf-jail`; tini is PID 1; the GUI
service in `deploy/compose/compose.yml` declares the healthcheck. Tagged releases are published as
`ghcr.io/laserlloyd/crucibleforge:<version>` with a CycloneDX SBOM, SHA256SUMS, <!-- scrub-ok: public GHCR image path, the project's own handle -->
a cosign keyless signature and build-provenance attestation (see
`.github/workflows/containers.yml`).

## Rollback

Pin the previous release image as the `app` service's `image:` in
`deploy/compose/compose.yml`, then run
`docker compose -f deploy/compose/compose.yml up -d`. State backups from each
upgrade are in `/data/backups/`. The image is
`ghcr.io/laserlloyd/crucibleforge:<version>`. <!-- scrub-ok: published image name -->
