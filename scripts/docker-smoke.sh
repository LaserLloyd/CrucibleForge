#!/bin/sh
# Build the CrucibleForge image and prove it works, with no model and no key.
#
#   scripts/docker-smoke.sh                 # docker, or podman when docker is absent
#   ENGINE=podman scripts/docker-smoke.sh
#   IMAGE=crucibleforge:ci NO_BUILD=1 scripts/docker-smoke.sh   # test an image built elsewhere
#
# Checks, in order:
#   1. the image builds
#   2. `--help` runs through the entrypoint as a non-root user
#   3. the first start seeds /data/models.yaml from the Docker starter, with no
#      provider left pointing into the container (localhost / 127.0.0.1)
#   4. `cases verify` re-derives gold answers offline (no model is called)
#   5. `config` resolves /data/models.yaml and /data/results
#   6. no private or operator-local file is in the image (cases/private,
#      *.local.*, models.yaml, .env, results/, docs/, .git ...)
#   7. compose.yaml is valid (when a compose provider is installed)
# Exit 0 = all passed. Everything it creates is removed again (except the image).
set -eu

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ENGINE="${ENGINE:-$(command -v docker >/dev/null 2>&1 && echo docker || echo podman)}"
IMAGE="${IMAGE:-crucibleforge:smoke}"
VOL="cf-smoke-$$"
fail() { echo "FAIL: $*" >&2; exit 1; }
ok() { echo "ok   $*"; }

cleanup() { "$ENGINE" volume rm -f "$VOL" >/dev/null 2>&1 || true; }
trap cleanup EXIT INT TERM

if [ "${NO_BUILD:-0}" != "1" ]; then
    # podman defaults to the OCI format, which drops the HEALTHCHECK
    fmt=""; [ "$(basename "$ENGINE")" = podman ] && fmt="--format docker"
    # shellcheck disable=SC2086
    "$ENGINE" build $fmt -t "$IMAGE" "$ROOT" >/dev/null || fail "image build"
    ok "build $IMAGE ($ENGINE)"
fi

run() {   # run <args...>: the CLI as compose.yaml runs it, on a scratch volume
    "$ENGINE" run --rm --read-only --tmpfs /tmp --cap-drop ALL \
        --security-opt no-new-privileges \
        -e CRUCIBLEFORGE_SEED_TEMPLATE=/app/deploy/models.docker.yaml \
        -e CRUCIBLEFORGE_ALLOW_UNSANDBOXED=1 \
        -v "$VOL:/data" "$IMAGE" "$@"
}

out="$(run --help 2>&1)" || fail "--help: $out"
echo "$out" | grep -q "usage: crucibleforge" || fail "--help printed no usage"
ok "--help"

uid="$(run id -u 2>/dev/null)"
[ "$uid" != "0" ] || fail "container runs as root"
ok "runs as uid $uid"

cfg="$(run cat /data/models.yaml 2>/dev/null)" || fail "no seeded /data/models.yaml"
echo "$cfg" | grep -q "^  local:" || fail "seed is not the Docker starter"
if echo "$cfg" | grep -E "base_url: *http://(localhost|127\.0\.0\.1)" >/dev/null; then
    fail "seeded models.yaml still points a provider into the container"
fi
ok "first-start seed (Docker starter, local provider -> host.docker.internal)"

out="$(run cases verify --categories math,instruct,reasoning 2>&1)" || fail "cases verify: $out"
ok "cases verify (offline)"

out="$(run config 2>&1)" || fail "config: $out"
echo "$out" | grep -q "/data/models.yaml" || fail "config does not resolve /data/models.yaml: $out"
ok "config resolves /data/models.yaml"

leaks="$(run sh -c 'find / -xdev \( -path /proc -o -path /sys -o -path /data \) -prune -o -print 2>/dev/null' \
    | grep -E '^/app/(cases/private|profiles/private|docs|results|runs|tests|scripts|codebank_src|\.git|\.env|models\.yaml$)|\.local\.|/app/.*\.private\.yaml$|CUSTOM-TESTS|scrub-rules|gui_token' \
    | grep -v '^/usr/share/doc/' || true)"
[ -z "$leaks" ] || fail "private/operator files in the image:
$leaks"
ok "no private or operator-local files in the image"

if "$ENGINE" compose version >/dev/null 2>&1; then
    (cd "$ROOT" && "$ENGINE" compose -f compose.yaml config --quiet) || fail "compose.yaml invalid"
    ok "compose.yaml valid ($ENGINE compose)"
else
    echo "skip compose.yaml check: no '$ENGINE compose' provider installed"
fi

echo "PASS: image $IMAGE"
