#!/bin/sh
# CrucibleForge container entrypoint. Deliberately POSIX sh and short.
#
# In order:
#  1. If started as root: align the `app` account with PUID/PGID, chown /data,
#     drop privileges (setpriv) and re-exec. Not root: skip (nothing to adjust).
#  2. Fail LOUD and EARLY when /data is not writable (bind-mount UID mismatch).
#  3. Pre-upgrade backup: when the image version differs from the one that last
#     ran on this volume, copy the small state files (models.yaml, the results
#     summaries) to /data/backups/<old>-to-<new>-<timestamp>/ (keep the last 5).
#     Transcripts (transcripts_*.jsonl, the large files) are NOT copied; back
#     those up with the volume backup in docs/deploy/docker.md.
#  4. First start: seed /data/models.yaml with the local provider pointed at the
#     Docker host. An existing file is never touched.
#  5. `docker compose run --rm app status` works: a first argument that is not
#     an executable (or starts with "-") is a crucibleforge subcommand.
#  6. CRUCIBLEFORGE_GUI_TOKEN, when set, is the GUI's stable access token.
#
# It does not install, migrate or "repair" anything, and never recursively
# chowns a healthy volume on every boot.
set -eu

DATA_DIR=/data
PUID="${PUID:-1000}"
PGID="${PGID:-1000}"
CFG="${CRUCIBLEFORGE_CONFIG:-$DATA_DIR/models.yaml}"
LLM_BASE_URL="${LLM_BASE_URL:-http://host.docker.internal:1234/v1}"

log() { printf '%s crucibleforge-entrypoint: %s\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" "$*" >&2; }
die() { log "FATAL: $*"; exit 1; }

# ---- 1. privileges ----------------------------------------------------------
if [ "$(id -u)" = "0" ]; then
    log "started as root; chown ${DATA_DIR} to ${PUID}:${PGID} and drop privileges"
    if [ "$(id -u app)" != "$PUID" ] || [ "$(id -g app)" != "$PGID" ]; then
        groupmod -o -g "$PGID" app 2>/dev/null || true
        usermod -o -u "$PUID" -g "$PGID" app 2>/dev/null || true
    fi
    mkdir -p "$DATA_DIR"
    chown "$PUID:$PGID" "$DATA_DIR"
    if [ -e "$CFG" ] && [ "$(stat -c '%u' "$CFG")" != "$PUID" ]; then
        log "ownership mismatch on existing data: one-time recursive chown"
        chown -R "$PUID:$PGID" "$DATA_DIR"
    fi
    exec setpriv --reuid="$PUID" --regid="$PGID" --init-groups --inh-caps=-all -- "$0" "$@"
fi

# ---- 2. writability --------------------------------------------------------
if [ ! -w "$DATA_DIR" ]; then
    log "${DATA_DIR} is not writable by UID $(id -u) (owned by $(stat -c '%u:%g' "$DATA_DIR" 2>/dev/null || echo '?'))."
    log "  Bind mount owned by another user? Either chown it to $(id -u):$(id -g), or use the"
    log "  PUID/PGID override (docs/deploy/docker.md, 'Volume permissions'), or use the named volume."
    die "data directory not writable"
fi
mkdir -p "$DATA_DIR/results" "$DATA_DIR/tmp"

# ---- 3. pre-upgrade backup --------------------------------------------------
state_files() {
    [ -f "$DATA_DIR/models.yaml" ] && echo "$DATA_DIR/models.yaml"
    find "$DATA_DIR" -maxdepth 1 -name 'models.yaml.bak*' -type f 2>/dev/null
    find "$DATA_DIR/results" -maxdepth 1 -type f \( -name '*.csv' -o -name '*.json' -o -name '*.md' \) 2>/dev/null
    return 0
}

pre_upgrade_backup() {
    marker="$DATA_DIR/.image-version"
    pkg="$(python -c "import importlib.metadata as m; print(m.version('crucibleforge-bench'))" 2>/dev/null || echo unknown)"
    new="${pkg}-${IMAGE_VERSION:-dev}"
    old="$(cat "$marker" 2>/dev/null || true)"
    if [ "$old" != "$new" ]; then
        files="$(state_files)"
        if [ -n "$files" ]; then
            tag_old="$(printf '%s' "${old:-unversioned}" | tr -c 'A-Za-z0-9._+\n-' '_')"
            tag_new="$(printf '%s' "$new" | tr -c 'A-Za-z0-9._+\n-' '_')"
            dest="$DATA_DIR/backups/${tag_old}-to-${tag_new}-$(date -u '+%Y%m%dT%H%M%SZ')"
            mkdir -p "$dest"
            printf '%s\n' "$files" | while IFS= read -r f; do
                rel="${f#"$DATA_DIR"/}"
                mkdir -p "$dest/$(dirname "$rel")"
                cp -p "$f" "$dest/$rel"
            done
            log "image version changed (${old:-none} -> ${new}): state files copied to ${dest}"
            # keep the 5 newest
            ls -1dt "$DATA_DIR"/backups/*/ 2>/dev/null | tail -n +6 | while IFS= read -r d; do rm -rf "$d"; done
        fi
        printf '%s\n' "$new" > "$marker"
    fi
}
pre_upgrade_backup

# ---- 4. first-start seed -----------------------------------------------------
if [ ! -f "$CFG" ] && [ -f /app/models.example.yaml ]; then
    mkdir -p "$(dirname "$CFG")"
    # '|' as the sed delimiter: URLs contain '/'. Both spellings of loopback:
    # the template's StudioForge entry says 127.0.0.1, the LM Studio one says
    # localhost, and inside a container either one is the container itself
    # (tests/test_deploy_seed.py applies these rules to the real template).
    sed -e "s|http://localhost:1234/v1|${LLM_BASE_URL}|g" \
        -e "s|http://127.0.0.1:1234/v1|${LLM_BASE_URL}|g" \
        -e 's|http://localhost:|http://host.docker.internal:|g' \
        -e 's|http://127.0.0.1:|http://host.docker.internal:|g' \
        /app/models.example.yaml > "$CFG"
    log "seeded $CFG (primary local provider -> ${LLM_BASE_URL})"
fi

# ---- 5/6. command + GUI token -------------------------------------------------
if [ "$#" -eq 0 ] || [ "${1#-}" != "$1" ] || ! command -v "$1" >/dev/null 2>&1; then
    set -- crucibleforge "$@"
fi
if [ "${CRUCIBLEFORGE_GUI_TOKEN:-}" != "" ] && [ "${1:-}" = "crucibleforge" ] && [ "${2:-}" = "gui" ]; then
    set -- "$@" --token "$CRUCIBLEFORGE_GUI_TOKEN"
fi

log "data=${DATA_DIR} uid=$(id -u) gid=$(id -g) version=${IMAGE_VERSION:-dev}"
# exec: the app becomes the process that receives SIGTERM.
exec "$@"
