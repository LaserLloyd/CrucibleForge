# syntax=docker/dockerfile:1.7
# =============================================================================
#  CrucibleForge - multi-stage container image
# =============================================================================
#  Build:   docker build -t crucibleforge:dev .
#  Run:     docker compose -f deploy/compose/compose.yml up -d
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
#   * uv is NOT in the runtime image; nothing wraps the process. tini is added
#     by compose (`init: true`).
# =============================================================================


# ---------------------------------------------------------------------------
#  Stage 1: builder
# ---------------------------------------------------------------------------
FROM ghcr.io/astral-sh/uv:python3.13-trixie-slim AS builder

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
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked

RUN find /app -name '__pycache__' -type d -prune -exec rm -rf {} + \
 && find /app -name '*.pyc' -delete


# ---------------------------------------------------------------------------
#  Stage 2: runtime
# ---------------------------------------------------------------------------
FROM python:3.13-slim-trixie AS runtime

ARG VERSION=dev
ARG REVISION=unknown
ARG CREATED

LABEL org.opencontainers.image.title="CrucibleForge" \
      org.opencontainers.image.description="LLM capability benchmark with deterministic graders, an LLM judge and a web GUI" \
      org.opencontainers.image.source="https://github.com/LaserLloyd/CrucibleForge" \
      org.opencontainers.image.documentation="https://github.com/LaserLloyd/CrucibleForge/blob/main/docs/deploy/docker.md" \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.revision="${REVISION}" \
      org.opencontainers.image.created="${CREATED}"

# tzdata so TZ= resolves; ca-certificates for HTTPS to hosted providers.
#
# bubblewrap (bwrap) is the sandbox the coding graders use when it is on PATH,
# and graders.py prefers it over --allow-unsandboxed whenever it finds it. It
# needs unprivileged user namespaces, which Docker's default seccomp profile
# blocks: with bwrap installed but unusable every coding case FAILS ("No
# permissions to create a new namespace"). So it is NOT installed by default and
# the container itself is the sandbox (CRUCIBLEFORGE_ALLOW_UNSANDBOXED=1 in the
# compose file). Build with --build-arg WITH_BWRAP=1 only if you also run the
# container with a seccomp profile that permits user namespaces.
ARG WITH_BWRAP=0
RUN apt-get update \
 && apt-get install -y --no-install-recommends tzdata ca-certificates \
 && if [ "$WITH_BWRAP" = "1" ]; then apt-get install -y --no-install-recommends bubblewrap; fi \
 && rm -rf /var/lib/apt/lists/*

# UID/GID 1000: a bind-mounted host directory owned by the person running
# `docker compose up` is then writable without further ceremony.
RUN groupadd --gid 1000 app \
 && useradd --uid 1000 --gid 1000 --create-home --shell /usr/sbin/nologin app

COPY --from=builder --chown=app:app /app /app
COPY --chmod=0755 deploy/docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
COPY --chmod=0755 deploy/healthcheck.py /usr/local/bin/healthcheck.py

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    CRUCIBLEFORGE_CONFIG=/data/models.yaml \
    CRUCIBLEFORGE_RESULTS=/data/results \
    HOME=/data \
    XDG_CONFIG_HOME=/data/.config \
    TMPDIR=/data/tmp

# No `VOLUME /data` on purpose: an anonymous volume would hide data loss.
# Compose mounts a named volume explicitly.
RUN install -d -o app -g app -m 0750 /data /data/results /data/tmp

WORKDIR /app
USER app
EXPOSE 8777

# The GUI answers 401 (token required) on a non-loopback bind; any HTTP answer
# proves the server is up, so only a connection failure is unhealthy.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD ["python", "/usr/local/bin/healthcheck.py"]

STOPSIGNAL SIGTERM
ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]
CMD ["gui", "--host", "0.0.0.0", "--port", "8777", "--no-open"]
