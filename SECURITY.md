# Security policy

## Reporting a vulnerability

Please do not open a public issue for a security problem.

- Report privately via GitHub: **Security → Report a vulnerability** on the
  repository (private vulnerability reporting is enabled).

Include the CrucibleForge version (`crucibleforge/version.py`), your OS, and the smallest `models.yaml` that
reproduces the problem. Never attach real API keys, StudioForge PINs, run
transcripts you do not have the right to share, or a `models.yaml` that names
private hosts.

## Supported versions

Only the latest release receives fixes while the project is pre-1.0 in spirit
(the version number tracks the benchmark suite, not API stability).

## What to know about the trust model

- **The GUI is local by default.** `crucibleforge gui` binds `127.0.0.1`. Any
  non-loopback `--host` forces a generated access token (cookie or
  `X-CrucibleForge-Token`), and POSTs also require a same-origin request. The
  GUI speaks plain HTTP: do not expose it beyond a network you trust.
- **Server-side requests.** `/api/providers/test` and `/api/discover` make
  requests to a `base_url` you supply. That is by design for a tool that talks
  to arbitrary OpenAI-compatible servers; it is why the token gate exists.
- **API keys belong in the environment** (`api_key_env`). The GUI can write a
  literal `api_key` into `models.yaml` for non-`openai` provider types; prefer
  `api_key_env`. `models.yaml` is git-ignored for this reason.
- **Model-written code is untrusted.** Coding cases run inside `bwrap
  --unshare-all` (no network, read-only system, 15 s limit). Without
  bubblewrap (Windows, macOS) CrucibleForge refuses to run them unless you pass
  `--allow-unsandboxed`, which runs model-authored code with your privileges.
  Use it only on a disposable machine.
- **`--force-evict` can unload other tenants' models** on a shared StudioForge.
  It is never sent automatically, unless a provider sets `force_evict: true` in
  `models.yaml` (a standing policy for every run on that provider).
- **The Docker image runs model-written code unsandboxed.** The shipped compose
  file sets `CRUCIBLEFORGE_ALLOW_UNSANDBOXED=1`: inside the container there is no
  `bwrap`, so the container is the sandbox (no capabilities, `no-new-privileges`,
  read-only image, CPU/memory limits). Graded code can still reach the network
  and a writable `/data`. See
  [`docs/deploy/docker.md`](docs/deploy/docker.md#coding-cases-and-the-sandbox);
  build with `--build-arg WITH_BWRAP=1` (and a seccomp profile that allows user
  namespaces) for a stronger setup.
- **No telemetry.** Hosted APIs are contacted only when you configure a
  provider for them.

## Tests never touch a live server

The test-suite installs a socket guard (`tests/_netguard.py`) that refuses every
connection except to ephemeral loopback ports the tests open themselves, so a
test run cannot reach a real StudioForge, a hosted API, or a LAN host.
