# CrucibleForge in Docker: the easy setup

Benchmark a model in a few commands, with nothing installed but Docker (or
Podman). The container needs **no GPU**: the models, and the judge, run on the
server it talks to. That is either an LLM server on your own machine
(llama.cpp's `llama-server`, LM Studio, StudioForge, Ollama, vLLM) or a hosted
API (DeepSeek, OpenRouter, OpenAI, Anthropic).

Everything you keep (your `models.yaml`, the results and the GUI token) lives
on one Docker volume, `crucibleforge-data`, mounted at `/data`.

> Want an always-on, hardened GUI service (healthcheck, resource limits,
> PUID/PGID for bind mounts, backups and upgrades)? That is
> [`deploy/compose/compose.yml`](deploy/docker.md). This page is the short path.

## 1. Set up (once)

```bash
git clone https://github.com/LaserLloyd/CrucibleForge.git    # scrub-ok: public repo URL
cd CrucibleForge
cp .env.example .env          # Windows PowerShell: copy .env.example .env
```

Open `.env` and fill in **one** of these:

* **a hosted API:** put your key after its name, e.g. `DEEPSEEK_API_KEY=sk-...`.
  DeepSeek is the cheapest way to try it.
* **a server on your machine:** set `LLM_BASE_URL` to it as the container sees
  it: `http://host.docker.internal:<port>/v1` (llama-server `8080`, LM Studio /
  StudioForge `1234`, Ollama `11434`).

Then build the image and check the setup:

```bash
docker compose build
docker compose run --rm crucibleforge status
```

`status` lists every provider (`UP` / `DOWN`, and whether its key is set), the
models in your registry and the case set. On the first start the container
writes a starter registry to `/data/models.yaml` from
[`deploy/models.docker.yaml`](../deploy/models.docker.yaml): one local model
(`local-model`), one API model (`deepseek-flash`), and a few disabled examples
for OpenRouter, OpenAI and Claude.

## 2. Run a benchmark

```bash
# a quick smoke: 4 cases, proves the model answers (DeepSeek: about 2 cents)
docker compose run --rm crucibleforge run --models deepseek-flash --smoke

# a real score that needs no judge: the Coding category (code is graded by tests)
docker compose run --rm crucibleforge run --models deepseek-flash --categories coding,tooluse,codebank,codeagent

# build the report
docker compose run --rm crucibleforge report
```

For a local model, use `--models local-model` after you set its `model_id`
(step 4).

The first run of a model also runs a short **conformance** check (does the
server speak the request protocol the bench needs?). If it fails, fix the
cause, then `docker compose run --rm crucibleforge conform --models <name>`
re-checks it (a failed record is kept until you do).

## 3. Read the results

The report is in the volume. Either print it:

```bash
docker compose run --rm -T crucibleforge cat /data/results/report.md
docker compose run --rm -T crucibleforge cat /data/results/report.html > report.html   # open in a browser
```

or start the results GUI:

```bash
docker compose --profile gui up -d gui
docker compose logs gui        # prints http://127.0.0.1:8777/?token=...
```

Open that URL once; a cookie keeps you signed in. The GUI listens on
`127.0.0.1` only (set `BIND_ADDR=0.0.0.0` in `.env` to open it to your network;
it still asks for the token). Set `CRUCIBLEFORGE_GUI_TOKEN` in `.env` for a
token that stays the same. Stop it with `docker compose --profile gui down`.

## 4. Your own models

Copy the registry out, edit it, copy it back:

```bash
docker compose run --rm -T crucibleforge cat /data/models.yaml > models.yaml
# edit models.yaml, then:
docker compose run --rm -T -v "$PWD/models.yaml:/in.yaml:ro" crucibleforge cp /in.yaml /data/models.yaml
```

(On Podman with SELinux write `:ro,Z`.) The GUI's Providers / Models pages edit
the same file. `models.example.yaml` documents every option. A local model
entry looks like:

```yaml
providers:
  local:
    type: openai
    base_url: http://host.docker.internal:8080/v1
models:
  - name: my-qwen
    provider: local
    model_id: "qwen3-8b"            # the id your server lists at /v1/models
    context_length: 32768
```

A hosted one only adds `api_key_env: SOME_KEY_NAME` to the provider and
`SOME_KEY_NAME=...` to `.env`. **Never** put a key in `models.yaml`. Claude goes
through Anthropic's OpenAI-compatible endpoint (`claude-example` in the
starter); set `max_output_tokens: 64000`, Claude refuses larger budgets.

## 5. The judge (Chat and NSFW scores)

Coding, tool use, math, instruction-following and reasoning are graded by
code. **Chat and NSFW answers are graded by a judge model**, so
`crucibleforge all` (run + judge + report) needs one:

```bash
docker compose run --rm crucibleforge all --models local-model --judge local-judge --yes
```

`--judge` takes a `judge.candidates` name from `models.yaml` (`local-judge`,
`api-judge`), a model name from the registry, or `provider:model_id`. Rules:

* The judge must be a **different model** from the one under test (it never
  grades its own answers).
* Before judging, the judge must pass a calibration canary that includes
  scoring clearly explicit text. **Hosted judges usually fail it** (they refuse
  or under-rate that text) and the judge phase stops with
  `canary FAILED`. The reliable judge is a **local uncensored instruct
  model** (an "abliterated" / "heretic" fine-tune of a 12B-32B model, on your
  llama.cpp / LM Studio server; `local-judge` in the starter). It runs on your
  server's GPU or CPU, never in this container.
* Without `--judge`, the bench profile asks for the maintainer's own rig judge,
  which you do not have: always pass `--judge`.

## Commands at a glance

| What | Command |
|---|---|
| all subcommands | `docker compose run --rm crucibleforge --help` |
| check providers + registry | `docker compose run --rm crucibleforge status` |
| list / verify the cases (offline) | `docker compose run --rm crucibleforge cases verify` |
| benchmark | `docker compose run --rm crucibleforge run --models <name> [--smoke]` |
| judge stored answers | `docker compose run --rm crucibleforge judge --models <name> --judge <judge>` |
| everything | `docker compose run --rm crucibleforge all --models <name> --judge <judge> --yes` |
| report | `docker compose run --rm crucibleforge report` |
| results GUI | `docker compose --profile gui up -d gui` |
| a shell in the image | `docker compose run --rm crucibleforge sh` |
| update | `git pull && docker compose build` (the volume is kept) |
| delete everything | `docker compose down -v` (**deletes the volume: registry and results**) |

## Troubleshooting

**`DOWN` / `Connection refused` for a server on your machine.** Inside the
container `localhost` is the container; use `host.docker.internal`, which the
compose file maps to your machine.

* **Docker Desktop (Windows, macOS):** works for a server on `127.0.0.1`.
* **Docker on Linux:** `host.docker.internal` is the Docker bridge, so the
  server must listen on more than `127.0.0.1`: `llama-server --host 0.0.0.0`,
  LM Studio "Serve on Local Network", `OLLAMA_HOST=0.0.0.0`. A firewall may
  also need to allow the bridge (`172.17.0.0/16`).
* **Rootless Podman:** same, or keep the server on loopback and add the
  override that maps the host's loopback into the container:
  `podman compose -f compose.yaml -f compose.podman.yaml run --rm crucibleforge status`.
* `LLM_BASE_URL` is read only on the **first** start. Later, change
  `base_url` in `/data/models.yaml` (step 4).
* After fixing the connection, re-run `conform --models <name>`: a failed
  conformance record is kept until you do.

**`HTTP 401` / `wants an API key`.** `status` says which variable it looked
for: the name in `.env` must match `api_key_env` in `models.yaml` exactly, with
no quotes or spaces around the value. `.env` is read when the container starts,
so a new `run` picks a change up. A 401 from a local server means it wants a
key too (StudioForge does): add `api_key_env: LOCAL_API_KEY` to the `local`
provider and `LOCAL_API_KEY=...` to `.env`.

**`/data is not writable` / `Permission denied` on the volume.** The container
runs as UID 1000. The named volume just works. If you set `DATA_PATH` to a host
folder, that folder must be writable by UID 1000: `sudo chown 1000:1000 <dir>`,
or set `RUN_AS=$(id -u):$(id -g)` in `.env` to run as yourself, or use
`deploy/compose/compose.puid.yml`.
On **rootless Podman** UID 1000 in the container is not you on the host; use
the named volume, or `podman unshare chown 1000:1000 <dir>`. On an **SELinux**
host (Fedora, RHEL) a bind mount also needs `:Z`, e.g.
`DATA_PATH=./cf-data` plus `- ${DATA_PATH}:/data:Z` in `compose.yaml`.

**`canary FAILED` while judging.** The judge refused or under-rated the
calibration probes: use a local uncensored judge (section 5).

**`no cases selected`.** The bench profile is a fixed case list; `--smoke`
keeps only its 4 smoke cases, so `--smoke` with `--categories` usually selects
nothing. Drop one of them.

**Podman.** Every command works with `podman compose` instead of
`docker compose` (it needs `podman-compose` or Docker's `docker-compose`
installed as the provider). Build by hand with `podman build --format docker -t crucibleforge:dev .`
(`--format docker` keeps the image's HEALTHCHECK).

## Security notes

* Coding cases **execute code the model wrote**. In the container the
  container is the sandbox: non-root, all capabilities dropped,
  `no-new-privileges`, read-only image filesystem
  (`CRUCIBLEFORGE_ALLOW_UNSANDBOXED=1`). That code can still reach the network
  and read `/data` (including the GUI token), so benchmark models you are
  willing to run code from. [`deploy/docker.md`](deploy/docker.md) explains
  the bubblewrap alternative.
* `.env` holds your keys: keep it private (`chmod 600 .env`). It is
  git-ignored and never copied into the image; neither are `models.yaml`,
  `results/`, `cases/private/` or any `*.local.*` file (`.dockerignore`).

## Testing the image

`scripts/docker-smoke.sh` builds the image and checks it with no model and no
key: `--help`, non-root user, the first-start seed, offline `cases verify`,
`config`, that no private or operator-local file is in the image, and that
`compose.yaml` is valid. `ENGINE=podman scripts/docker-smoke.sh` for Podman.
CI builds the image on every pull request (`.github/workflows/containers.yml`).
