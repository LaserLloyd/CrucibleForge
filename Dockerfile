# syntax=docker/dockerfile:1.7
# =============================================================================
#  CrucibleForge - multi-stage container image
# =============================================================================
#  One command (docs/FIRST-RUN-DOCKER.md):
#           docker run --rm -it -e OPENROUTER_API_KEY=... -v "$PWD/makobench:/data" \
#               ghcr.io/laserlloyd/crucibleforge:4.5.1 test <openrouter-model-id>
#  Build:   docker build -t crucibleforge:dev .
#  GUI service (hardened, long-running): docker compose -f deploy/compose/compose.yml up -d
#
#  Load-bearing design notes (same pattern as the DisPatch Chat image):
#
#   * BUILDER and RUNTIME ship the same interpreter at the same absolute path.
#     A uv venv holds an absolute symlink (.venv/bin/python -> /usr/local/bin/
#     python3), so the venv only works on a base whose Python lives in the same
#     place. ghcr.io/astral-sh/uv:python3.13-trixie-slim is built FROM
#     python:3.13-slim-trixie, so this pair matches by construction.
#
#   * CrucibleForge must run FROM A CHECKOUT, not from a wheel: the case set
#     (cases/), the profiles (profiles/) and models.example.yaml live next to
#     the package, and config.ROOT_DIR is resolved relative to it. So the
#     project is installed editable (uv's default) and the source tree is
#     copied to the SAME path (/app) in the runtime stage.
#
#   * uv is NOT in the runtime image. tini is PID 1 (signals reach the app; a
#     `docker stop` ends it at once). The container starts as root only long
#     enough for the entrypoint to run the benchmark as the data folder's owner.
#
#   * Model-written code runs through cf-jail (deploy/cf-jail.c, setuid root):
#     as uid 65534, no IPv4/IPv6 sockets, resource limits, its own scratch only.
#     Bases are pinned by digest; bump them on purpose.
# =============================================================================


# ---------------------------------------------------------------------------
#  Stage 1: builder
# ---------------------------------------------------------------------------
FROM python:3.13-slim-trixie@sha256:7249d5587b78209a006dc4a3b5340678f84ea729239193aa559962cd8c1fce50 AS jail
RUN apt-get update && apt-get install -y --no-install-recommends gcc libc6-dev linux-libc-dev \
 && rm -rf /var/lib/apt/lists/*
COPY deploy/cf-jail.c /src/cf-jail.c
RUN gcc -O2 -Wall -Wextra -Werror -D_FORTIFY_SOURCE=2 -fstack-protector-strong -fPIE -pie \
        -o /src/cf-jail /src/cf-jail.c


FROM ghcr.io/astral-sh/uv:python3.13-trixie-slim@sha256:00e2b362982a5ce19eb3a51453ed76af92ccaaedccc0236fae83b27d994ae0f3 AS builder

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_NO_DEV=1 \
    UV_PYTHON_DOWNLOADS=0 \
    UV_PROJECT_ENVIRONMENT=/app/.venv

WORKDIR /app

# Dependency layer: manifests only, so ordinary code edits reuse it.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-install-project

# Application layer. README.md is needed because pyproject.toml names it as the
# package readme; LICENSE because of license-files.
COPY README.md LICENSE models.example.yaml ./
COPY crucibleforge/ ./crucibleforge/
COPY cases/ ./cases/
COPY profiles/ ./profiles/
# calibration/ holds the run-noise estimates behind every score's range and the 4.5 marks and classing files;
# archive/ is the frozen 4.1 test (`--profile bench`) whose cases the judge's element index also reads.
COPY calibration/ ./calibration/
COPY archive/ ./archive/
# budget/ holds the measured token targets the plan's price and time estimates read
COPY budget/ ./budget/
# the short Docker starter registry (compose.yaml seeds /data/models.yaml from it)
COPY deploy/models.docker.yaml ./deploy/models.docker.yaml
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked

RUN find /app -name '__pycache__' -type d -prune -exec rm -rf {} + \
 && find /app -name '*.pyc' -delete


# ---------------------------------------------------------------------------
#  Stage 2: runtime
# ---------------------------------------------------------------------------
FROM python:3.13-slim-trixie@sha256:7249d5587b78209a006dc4a3b5340678f84ea729239193aa559962cd8c1fce50 AS runtime

ARG VERSION=4.5.1
ARG REVISION=unknown
ARG CREATED

LABEL org.opencontainers.image.title="CrucibleForge" \
      org.opencontainers.image.description="LLM capability benchmark with deterministic graders, an LLM judge and a web GUI" \
      org.opencontainers.image.source="https://github.com/LaserLloyd/CrucibleForge" \
      org.opencontainers.image.documentation="https://github.com/LaserLloyd/CrucibleForge/blob/main/docs/FIRST-RUN-DOCKER.md" \
      org.opencontainers.image.licenses="MIT-0" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.revision="${REVISION}" \
      org.opencontainers.image.created="${CREATED}"

# No bubblewrap: it needs user namespaces, which Docker's default seccomp profile blocks. Graded code runs through
# cf-jail instead (see the jail stage above and docs/deploy/docker.md).
RUN apt-get update \
 && apt-get install -y --no-install-recommends tzdata ca-certificates tini \
 && rm -rf /var/lib/apt/lists/*

# UID/GID 1000: a bind-mounted host directory owned by the person running
# `docker compose up` is then writable without further ceremony.
RUN groupadd --gid 1000 app \
 && useradd --uid 1000 --gid 1000 --create-home --shell /usr/sbin/nologin app

COPY --from=builder --chown=app:app /app /app
COPY --chmod=0755 deploy/docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
COPY --chmod=0755 deploy/healthcheck.py /usr/local/bin/healthcheck.py
COPY --from=jail /src/cf-jail /usr/local/libexec/cf-jail
# setuid root in its own layer: COPY --chmod drops the setuid bit under some builders
RUN chown root:root /usr/local/libexec/cf-jail && chmod 4755 /usr/local/libexec/cf-jail

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    CRUCIBLEFORGE_CONFIG=/data/models.yaml \
    CRUCIBLEFORGE_RESULTS=/data/results \
    HOME=/data \
    IMAGE_VERSION=${VERSION} \
    XDG_CONFIG_HOME=/data/.config \
    CRUCIBLEFORGE_IN_CONTAINER=1

# No `VOLUME /data` on purpose: an anonymous volume would hide data loss.
# Compose mounts a named volume explicitly.
RUN install -d -o app -g app -m 0750 /data /data/results

WORKDIR /app
# No USER: the entrypoint starts as root, then runs the benchmark as the owner of /data (never as root on the
# host) and model code as uid 65534. No HEALTHCHECK: `test` runs to completion; the GUI service in
# deploy/compose declares its own (python /usr/local/bin/healthcheck.py).
# 8777: the web GUI; 8377: the read-only results page (`view`, `test --serve`). EXPOSE only documents them: a port
# is reachable (and Docker Desktop shows it as a link) only when it is published with -p.
EXPOSE 8777 8377

STOPSIGNAL SIGTERM
ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/docker-entrypoint.sh"]
CMD ["gui", "--host", "0.0.0.0", "--port", "8777", "--no-open"]
