#!/bin/sh
# CrucibleForge container entrypoint.
#
#  1. First start: seed /data/models.yaml from the shipped template, pointing
#     the local providers at the Docker host (inside a container "localhost"
#     is the container itself, never LM Studio / StudioForge on the host).
#       LLM_BASE_URL  replaces the template's primary local URL
#                     (default http://host.docker.internal:1234/v1)
#       other http://localhost:PORT URLs become http://host.docker.internal:PORT
#     An existing /data/models.yaml is never touched.
#  2. `docker compose run --rm app status` works: a first argument that is not
#     an executable (or starts with "-") is a crucibleforge subcommand.
#  3. CRUCIBLEFORGE_GUI_TOKEN, when set, becomes the GUI's stable access token
#     (otherwise a new one is generated and printed on every start).
set -eu

CFG="${CRUCIBLEFORGE_CONFIG:-/data/models.yaml}"
LLM_BASE_URL="${LLM_BASE_URL:-http://host.docker.internal:1234/v1}"

if [ ! -f "$CFG" ] && [ -f /app/models.example.yaml ]; then
    mkdir -p "$(dirname "$CFG")"
    # '|' as the sed delimiter: URLs contain '/'.
    sed -e "s|http://localhost:1234/v1|${LLM_BASE_URL}|g" \
        -e 's|http://localhost:|http://host.docker.internal:|g' \
        /app/models.example.yaml > "$CFG"
    echo "crucibleforge: seeded $CFG (primary local provider -> ${LLM_BASE_URL})" >&2
fi

if [ "$#" -eq 0 ] || [ "${1#-}" != "$1" ] || ! command -v "$1" >/dev/null 2>&1; then
    set -- crucibleforge "$@"
fi

# Only the GUI takes --token.
if [ "${CRUCIBLEFORGE_GUI_TOKEN:-}" != "" ] && [ "${1:-}" = "crucibleforge" ] && [ "${2:-}" = "gui" ]; then
    set -- "$@" --token "$CRUCIBLEFORGE_GUI_TOKEN"
fi

exec "$@"
